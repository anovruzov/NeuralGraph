"""Source ACLs at disclosure time, private sources, and what may be ingested at all (product decisions 2026-10-08).

A member-restricted or private record is disclosed (answer, raw view, manual response) only when the requesting audience
is entirely inside the source's members, or to the holder owner. Private sources are not exportable by default.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycelic.ingest.acl import Audience, decide, narrow
from mycelic.ingest.events import Permissions
from mycelic.ingest.service import IngestService
from mycelic.ingest.store import IngestStore

from .ingest_support import OWNER, connect_export, make_pipeline, make_store, question, table_contains, write_jsonl

BEN, CY, EVE = "usr_ben", "usr_cy", "usr_eve"
PMAP = {"u-ana": OWNER, "u-ben": BEN, "u-cy": CY}
Q = "Is the Globex vendor contract renewal blocked on legal review?"


async def restricted_holder(tmp_path: Path, *, visibility: str = "members", membership_ref: str | None = None):
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    header = {"id": "deal-room", "name": "#deal-room", "source_type": "channel", "visibility": visibility, "member_ids": ["u-ana", "u-ben"]}
    if membership_ref:
        header["membership_ref"] = membership_ref
    f = write_jsonl(tmp_path / "deal.jsonl", [
        {"type": "message", "id": "x1", "author": "u-ben", "created_at": "2026-06-01T09:00:00Z", "title": "Globex legal hold",
         "text": "The Globex vendor contract renewal is blocked on legal review of the liability clause."}], header=header)
    pub = write_jsonl(tmp_path / "pub.jsonl", [
        {"type": "message", "id": "p1", "author": "u-cy", "created_at": "2026-06-02T09:00:00Z",
         "text": "Public note: the Globex renewal call moved to Friday."}], header={"id": "general", "source_type": "channel", "visibility": "public"})
    con = await connect_export(svc, [f, pub], source_app="teamchat", principal_map=PMAP)
    await pipe.sync(con["connector_id"])
    await pipe.process_available()
    return store, pipe, svc, con


def cited_restricted(resp) -> list[dict]:
    """References to the restricted record x1 (recognised by its content root, which every reference carries)."""
    from mycelic.util import fingerprint
    root = fingerprint("The Globex vendor contract renewal is blocked on legal review of the liability clause.")
    return [r for r in resp["evidence_refs"] if r["source_root_id"] == root]


# ---------------------------------------------------------------------------------------------- pure rules
def test_audience_rules() -> None:
    members = Permissions("members", ("a", "b"))
    assert decide(None, None, exportable=False) == (True, "upload")
    assert decide(Permissions("public"), None, exportable=False)[0]
    assert decide(members, Audience(frozenset({"a"}), complete=True), exportable=True)[0]
    assert not decide(members, Audience(frozenset({"a", "z"}), complete=True), exportable=True)[0]
    assert not decide(members, Audience(frozenset({"a"}), complete=False), exportable=True)[0]       # incomplete audience
    assert not decide(members, None, exportable=True)[0]                                             # unknown audience
    assert decide(members, Audience.owner_only(), exportable=False)[0]                               # the owner sees all
    priv = Permissions("private", ("a",))
    assert decide(priv, Audience(frozenset({"a"}), complete=True), exportable=False) == (False, "private_not_exportable")
    assert decide(priv, Audience(frozenset({"a"}), complete=True), exportable=True)[0]
    assert decide(Permissions("members", (), "ref1"), Audience(frozenset({"m"}), complete=True), exportable=True,
                  resolve_ref=lambda r: ["m"] if r == "ref1" else [])[0]
    # the holder owner principal counts as inside
    assert decide(members, Audience(frozenset({"own"}), complete=True), exportable=True, owner_ids=["own"])[0]
    # a record may narrow its source, never widen it
    assert narrow(Permissions("public"), Permissions("members", ("a",))).visibility == "members"
    assert narrow(Permissions("members", ("a", "b")), Permissions("public")).member_ids == ("a", "b")
    assert narrow(Permissions("members", ("a", "b")), Permissions("members", ("b", "c"))).member_ids == ("b",)
    assert narrow(Permissions("private", ("a",)), Permissions("public")).visibility == "private"


# ---------------------------------------------------------------------------------------------- answer_question
async def test_member_restricted_records_reach_only_an_audience_inside_the_members(tmp_path: Path) -> None:
    store, _pipe, _svc, _con = await restricted_holder(tmp_path)
    # no audience on the question (today's coordinator): restricted records are withheld, public ones still answer
    resp = await store.answer_question(question(Q))
    assert not cited_restricted(resp) and "liability" not in json.dumps(resp)
    # an audience entirely inside the source's members
    inside = await store.answer_question(question(Q, audience={"principal_ids": [BEN], "complete": True}))
    restricted = cited_restricted(inside)
    assert inside["status"] == "answered" and restricted
    assert restricted[0]["title"] == "teamchat message"                  # titles of non-public records are content too
    assert restricted[0]["meta"]["source_app"] == "teamchat"
    # one outsider in the audience withholds the record from everyone in it
    mixed = await store.answer_question(question(Q, audience={"principal_ids": [BEN, EVE], "complete": True}))
    assert not cited_restricted(mixed) and "liability" not in json.dumps(mixed)
    # an audience that is not enumerated completely is never "inside"
    partial = await store.answer_question(question(Q, audience={"principal_ids": [BEN], "complete": False}))
    assert not cited_restricted(partial)
    # the owner sees everything, with the real title
    own = await store.answer_question(question(Q, audience={"owner": True}))
    assert any(r["title"] == "Globex legal hold" for r in own["evidence_refs"])
    await store.close()


async def test_raw_for_ref_rechecks_the_source_acl(tmp_path: Path) -> None:
    store, _pipe, _svc, _con = await restricted_holder(tmp_path)
    resp = await store.answer_question(question(Q, audience={"principal_ids": [BEN], "complete": True}))
    ref = cited_restricted(resp)[0]["ref_id"]
    assert "liability" in (await store.raw_for_ref(ref, audience={"principal_ids": [BEN], "complete": True}))["text"]
    for denied in (None, {"principal_ids": [EVE], "complete": True}, {"principal_ids": [BEN, EVE], "complete": True}):
        raw = await store.raw_for_ref(ref, audience=denied)
        assert raw["status"] == "withheld" and raw["text"] == "" and raw["chunk_text"] is None and "Globex" not in json.dumps(raw)
    assert "liability" in (await store.raw_for_ref(ref, audience={"owner": True}))["text"]
    await store.close()


async def test_membership_refs_are_resolved_at_use_time(tmp_path: Path) -> None:
    store, _pipe, _svc, _con = await restricted_holder(tmp_path, membership_ref="deal-members")
    aud = {"principal_ids": [BEN], "complete": True}
    assert cited_restricted(await store.answer_question(question(Q, audience=aud)))
    # Ben leaves the channel: every record of the source is withheld from him at once, without rewriting any record
    await store.store.run_in_tx(lambda c: IngestStore.set_membership_sync(c, "teamchat:deal-members", [OWNER]))
    assert not cited_restricted(await store.answer_question(question(Q, audience=aud)))
    await store.close()


async def test_private_sources_are_not_exportable_until_the_owner_opts_in(tmp_path: Path) -> None:
    store, _pipe, svc, con = await restricted_holder(tmp_path, visibility="private")
    aud = {"principal_ids": [BEN], "complete": True}
    assert not cited_restricted(await store.answer_question(question(Q, audience=aud)))
    src = next(s for s in svc.sources(con["connector_id"]) if s.external_id == "deal-room")
    assert src.exportable is False
    await svc.set_source(src.source_id, actor=OWNER, exportable=True)
    assert cited_restricted(await store.answer_question(question(Q, audience=aud)))
    # still never to an outsider
    assert not cited_restricted(await store.answer_question(question(Q, audience={"principal_ids": [EVE], "complete": True})))
    await store.close()


async def test_manual_responses_follow_the_same_acl(tmp_path: Path) -> None:
    store, _pipe, _svc, _con = await restricted_holder(tmp_path)
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='x1'").fetchone()[0]
    out = await store.manual_response(question(Q, audience={"principal_ids": [EVE], "complete": True}), "It is blocked.", [rid])
    assert out["evidence_refs"] == []
    ok = await store.manual_response(question(Q, audience={"principal_ids": [BEN], "complete": True}), "It is blocked.", [rid])
    assert len(ok["evidence_refs"]) == 1 and ok["evidence_refs"][0]["meta"]["source_app"] == "teamchat"
    await store.close()


# ---------------------------------------------------------------------------------------------- admission
async def test_nothing_is_ingested_silently(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    pipe = make_pipeline(store)
    svc = IngestService(pipe)
    chan = write_jsonl(tmp_path / "chan.jsonl", [{"type": "message", "id": "c1", "author": "u-ben", "created_at": "2026-06-01T09:00:00Z",
                                                  "text": "pendingsentinel text of a channel nobody reviewed"}],
                       header={"id": "chan", "source_type": "channel", "visibility": "public"})
    dm = write_jsonl(tmp_path / "dm.jsonl", [{"type": "message", "id": "d1", "author": "u-ben", "created_at": "2026-06-01T09:00:00Z",
                                              "text": "dmsentinel private chat"}],
                     header={"id": "dm-ben", "source_type": "dm", "visibility": "private", "member_ids": ["u-ana", "u-ben"]})
    con = await svc.add_connector("local_export", created_by=OWNER, config={"source_app": "teamchat", "paths": [str(chan), str(dm)],
                                                                            "auto_include": True})
    counts = await svc.discover_sources(con["connector_id"])
    sel = {s.external_id: (s.selection, s.selection_reason) for s in svc.sources(con["connector_id"])}
    assert sel["dm-ben"] == ("excluded", "default_dm_excluded")         # even under an auto-include rule
    assert sel["chan"] == ("included", "auto_rule") and counts["discovered"] == 2
    # without the rule, a discovered source waits for review
    con2 = await svc.add_connector("local_export", created_by=OWNER, config={"source_app": "otherchat", "paths": [str(chan)]})
    await svc.discover_sources(con2["connector_id"])
    assert [s.selection for s in svc.sources(con2["connector_id"])] == ["pending_review"]
    await pipe.sync(con2["connector_id"])
    # an exclusion rule drops matching records before anything is stored
    await svc.add_exclusion(actor=OWNER, scope="body_regex", match="pending[s]entinel", connector_id=con["connector_id"])
    report = await pipe.sync(con["connector_id"])
    await pipe.process_available()
    assert report.excluded == 1
    assert table_contains(store, "pendingsentinel") == [] and table_contains(store, "dmsentinel") == []
    await store.close()


async def test_excluding_a_source_later_can_delete_its_records(tmp_path: Path) -> None:
    store, pipe, svc, con = await restricted_holder(tmp_path)
    src = next(s for s in svc.sources(con["connector_id"]) if s.external_id == "general")
    assert table_contains(store, "moved to Friday")
    await svc.set_source(src.source_id, actor=OWNER, selection="excluded", existing_records="delete")
    assert table_contains(store, "moved to Friday") == []
    tomb = store.store._conn.execute("SELECT reason FROM deletion_tombstones").fetchall()
    assert [t[0] for t in tomb] == ["excluded_after_ingest"]
    await store.close()


async def test_owner_deletes_a_record_synchronously(tmp_path: Path) -> None:
    store, pipe, svc, _con = await restricted_holder(tmp_path)
    rid = store.store._conn.execute("SELECT record_id FROM ingest_records WHERE source_object_id='x1'").fetchone()[0]
    resp = await store.answer_question(question(Q, audience={"owner": True}))
    refs = {r[0] for r in store.store._conn.execute("SELECT ref_id FROM exports WHERE doc_id=?", (rid,))}
    assert refs and resp["status"] == "answered"
    out = await svc.delete_record(rid, actor=OWNER)
    assert set(out["affected_ref_ids"]) == refs
    assert table_contains(store, "liability clause") == []
    assert pipe.publisher.of("evidence_event")[-1]["reason"] == "owner_deleted"
    await store.close()


async def test_connector_ownership_matches_the_holder_kind(tmp_path: Path) -> None:
    user_store = make_store(tmp_path, "user")
    f = write_jsonl(tmp_path / "x.jsonl", [], header={"id": "x", "visibility": "public"})
    svc = IngestService(make_pipeline(user_store))
    with pytest.raises(ValueError):
        await svc.add_connector("local_export", created_by=OWNER, ownership="org", config={"paths": [str(f)]})
    with pytest.raises(PermissionError):
        await svc.add_connector("local_export", created_by=EVE, config={"paths": [str(f)]})      # only the owner connects
    personal = await svc.add_connector("local_export", created_by=OWNER, config={"paths": [str(f)]})
    assert personal["ownership"] == "personal"
    unit_store = make_store(tmp_path, "unit", owner_ids=("lead_1",))
    usvc = IngestService(make_pipeline(unit_store, holder_kind="unit"))
    with pytest.raises(ValueError):
        await usvc.add_connector("local_export", created_by="lead_1", ownership="personal", config={"paths": [str(f)]})
    org = await usvc.add_connector("local_export", created_by="lead_1", config={"paths": [str(f)]})
    assert org["ownership"] == "org"
    with pytest.raises(ValueError):                                                          # the same account twice
        await usvc.add_connector("local_export", created_by="lead_1", config={"paths": [str(f)]})
    await user_store.close()
    await unit_store.close()
