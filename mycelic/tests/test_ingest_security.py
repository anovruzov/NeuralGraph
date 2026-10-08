"""Security of the ingestion layer: credential encryption (round trip, tamper detection, AAD binding, rotation, refusal of a
key generated next to the data), credentials at rest, secret masking, signed notices, and no content in logs."""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mycelic.ingest.connectors.local_export import LocalExportConnector, sign_notice
from mycelic.ingest.contract import Credentials, RedactingFilter, Secret
from mycelic.ingest.crypto import CredentialTampered, TokenVault, VaultUnavailable
from mycelic.ingest.service import IngestService

from .ingest_support import OWNER, connect_export, make_pipeline, make_store, question, table_contains, write_jsonl

TOKEN = "ghp_" + "A1b2C3d4" * 5
IDS = {"tenant_id": "ten_a", "holder_id": "hold_a", "connector_id": "con_a"}


def creds(token: str = TOKEN) -> Credentials:
    return Credentials(kind="pat", access_token=Secret(token), refresh_token=Secret("refresh-" + token), expires_at="2026-12-01T00:00:00Z",
                       extra={"token_type": "bearer"})


def flip(b: bytes, i: int = -1) -> bytes:
    a = bytearray(b)
    a[i] ^= 0x01
    return bytes(a)


def test_vault_round_trip_and_tamper_detection() -> None:
    vault = TokenVault.from_secret("master-secret-0123456789")
    sealed = vault.seal(credentials=creds(), **IDS)
    assert TOKEN.encode() not in sealed.ciphertext and TOKEN.encode() not in sealed.wrapped_dek
    assert sealed.ciphertext[:2] == b"v1" and sealed.aad == "mycelic/cred/v1|ten_a|hold_a|con_a"
    opened = vault.open(sealed, **IDS)
    assert opened.access_token.reveal() == TOKEN and opened.refresh_token.reveal() == "refresh-" + TOKEN and opened.extra == {"token_type": "bearer"}
    for bad in (dataclasses.replace(sealed, ciphertext=flip(sealed.ciphertext)), dataclasses.replace(sealed, ciphertext=flip(sealed.ciphertext, 5)),
                dataclasses.replace(sealed, wrapped_dek=flip(sealed.wrapped_dek)), dataclasses.replace(sealed, ciphertext=sealed.ciphertext[:20])):
        with pytest.raises(CredentialTampered):
            vault.open(bad, **IDS)
    # a row copied to another connector, holder or tenant fails authentication (the AAD is recomputed from the caller's ids)
    for other in ({**IDS, "connector_id": "con_b"}, {**IDS, "holder_id": "hold_b"}, {**IDS, "tenant_id": "ten_b"}):
        with pytest.raises(CredentialTampered):
            vault.open(sealed, **other)
    with pytest.raises(CredentialTampered):
        TokenVault.from_secret("another-master-key-xyz").open(sealed, **IDS)
    # nonces are fresh: sealing the same credentials twice gives different ciphertexts
    assert vault.seal(credentials=creds(), **IDS).ciphertext != sealed.ciphertext
    replaced = vault.replace_credentials(sealed, credentials=creds("ghp_" + "Z" * 40), **IDS)
    assert replaced.wrapped_dek == sealed.wrapped_dek and vault.open(replaced, **IDS).access_token.reveal() == "ghp_" + "Z" * 40


def test_key_rotation_rewraps_without_touching_the_ciphertext() -> None:
    old = TokenVault.from_secret("old-master-key-123456")
    sealed = old.seal(credentials=creds(), **IDS)
    rotated = TokenVault.from_secret("new-master-key-654321", previous=["old-master-key-123456"])
    assert rotated.open(sealed, **IDS).access_token.reveal() == TOKEN          # previous keys still unwrap
    rewrapped = rotated.rewrap(sealed, **IDS)
    assert rewrapped.kid == rotated.active_kid != sealed.kid and rewrapped.ciphertext == sealed.ciphertext
    assert TokenVault.from_secret("new-master-key-654321").open(rewrapped, **IDS).access_token.reveal() == TOKEN


def test_a_key_generated_next_to_the_data_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secret_key").write_text("generated-local-key-0000")
    local = SimpleNamespace(secret_key="generated-local-key-0000", data_dir=str(tmp_path))
    with pytest.raises(VaultUnavailable):
        TokenVault.from_settings(local, environ={})
    assert TokenVault.from_settings(local, environ={"MYCELIC_ALLOW_LOCAL_KEK": "1"}).active_kid
    from_env = SimpleNamespace(secret_key="env-provided-key-1111", data_dir=str(tmp_path))
    assert TokenVault.from_settings(from_env, environ={"MYCELIC_SECRET_KEY": "env-provided-key-1111"}).active_kid
    with pytest.raises(VaultUnavailable):
        TokenVault.from_settings(SimpleNamespace(secret_key="", data_dir=str(tmp_path)), environ={})


def test_secrets_never_print() -> None:
    s = Secret(TOKEN)
    assert TOKEN not in repr(s) and TOKEN not in str(s) and TOKEN not in repr(creds()) and TOKEN not in f"{creds()}"


async def test_credentials_are_sealed_at_rest_and_shredded_on_disconnect(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store, vault=TokenVault.from_secret("master-secret-0123456789"))
    svc = IngestService(pipe)
    f = write_jsonl(tmp_path / "a.jsonl", [], header={"id": "a", "visibility": "public"})
    con = await svc.add_connector("local_export", created_by=OWNER, config={"paths": [str(f)]}, credentials=creds())
    cid = con["connector_id"]
    assert table_contains(store, TOKEN) == [] and TOKEN.encode() not in Path(store.path).read_bytes()
    got = await pipe.context(pipe.db.get_connector(cid)).secrets.get()
    assert got.access_token.reveal() == TOKEN
    out = await svc.disconnect(cid, actor=OWNER)
    assert out["status"] == "disconnected" and pipe.db.get_credentials(cid) is None
    assert (await pipe.context(pipe.db.get_connector(cid)).secrets.get()).kind == "none"
    no_vault = IngestService(make_pipeline(make_store(tmp_path, "nv")))
    with pytest.raises(VaultUnavailable):
        await no_vault.add_connector("local_export", created_by=OWNER, config={"paths": [str(f)]}, credentials=creds())
    await store.close()
    await no_vault.p.evidence.close()


async def test_secrets_in_content_are_masked_and_the_record_restricted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    f = write_jsonl(tmp_path / "ops.jsonl", [{"type": "message", "id": "s1", "author": "a", "created_at": "2026-06-01T00:00:00Z",
                                              "text": f"Rotating the deploy token {TOKEN} for the release pipeline today."}],
                    header={"id": "ops", "source_type": "channel", "visibility": "public"})
    con = await connect_export(svc, [f], source_app="teamchat")
    await pipe.sync(con["connector_id"])
    await pipe.process_available()
    assert table_contains(store, TOKEN) == []
    rec = store.store._conn.execute("SELECT sensitivity, flags FROM ingest_records").fetchone()
    assert rec["sensitivity"] == "restricted" and "contains_secret" in json.loads(rec["flags"])
    resp = await store.answer_question(question("Which deploy token was rotated for the release pipeline?"))
    assert all(r["disclosure_level"] == "none" and r["disclosed_excerpt"] == "" for r in resp["evidence_refs"])
    await store.close()


def test_signed_notices_and_content_free_parsing() -> None:
    secret = b"notice-secret"
    body = json.dumps({"delivery_id": "d1", "account_id": "acme", "source_id": "deploys", "action": "changed",
                       "objects": [{"type": "message", "id": "m1", "text": "secret body"}], "title": "a title"}).encode()
    now = time.time()
    good = {"X-Mycelic-Signature": sign_notice(secret, body, now=now)}
    assert LocalExportConnector.verify_webhook(good, body, secret, now=now)
    assert not LocalExportConnector.verify_webhook(good, body + b" ", secret, now=now)               # tampered body
    assert not LocalExportConnector.verify_webhook(good, body, b"wrong", now=now)                     # wrong secret
    assert not LocalExportConnector.verify_webhook(good, body, secret, now=now + 301)                 # outside the tolerance
    assert not LocalExportConnector.verify_webhook({}, body, secret, now=now)
    [notice] = LocalExportConnector.parse_webhook(good, body)
    assert notice.object_refs == ({"type": "message", "id": "m1"},) and "secret body" not in repr(notice) and "a title" not in repr(notice)


def test_redacting_filter() -> None:
    rec = logging.LogRecord("mycelic.ingest.x", logging.INFO, __file__, 1, "token %s and %s", (TOKEN, "x" * 200), None)
    rec.body = "a message body"
    RedactingFilter().filter(rec)
    msg = rec.getMessage()
    assert TOKEN not in msg and "x" * 200 not in msg and "<redacted:len=200>" in msg and rec.body == "<redacted>"


async def test_no_content_reaches_logs_metrics_or_envelopes(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    sentinel = "Sentinelword7731"
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([json.dumps({"type": "source", "id": "s", "source_type": "channel", "visibility": "public"}),
                            json.dumps({"type": "message", "id": "a", "author": "a", "created_at": "2026-01-01T00:00:00Z", "title": f"{sentinel} title",
                                        "text": f"The {sentinel} rollout failed again and again."}),
                            json.dumps({"type": "unknown_kind", "id": "b", "text": f"{sentinel} in a broken record"}),
                            "{" + sentinel]) + "\n")
    con = await connect_export(svc, [f], source_app="teamchat")
    await pipe.sync(con["connector_id"])
    await pipe.process_available()
    with f.open("a") as fh:
        fh.write(json.dumps({"type": "delete", "id": "a", "deleted_at": "2026-01-02T00:00:00Z"}) + "\n")
    await pipe.sync(con["connector_id"])
    await pipe.process_available()
    assert caplog.records, "the pipeline is expected to log something"
    for r in caplog.records:
        assert sentinel not in r.getMessage() and sentinel.lower() not in r.getMessage().lower()
    assert sentinel not in json.dumps(pipe.db.metrics())
    assert sentinel not in json.dumps(pipe.publisher.sent)
    assert table_contains(store, sentinel) == []          # deleted: gone from the holder too
    await store.close()
