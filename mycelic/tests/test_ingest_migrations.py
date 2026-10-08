"""Holder schema migrations (§5.1): an existing v1 holder file upgrades when it is opened and keeps working; migrations are
recorded, idempotent, immutable once applied, and a file newer than the code is refused."""
from __future__ import annotations

from pathlib import Path

import pytest

from NeuralGraph.chat_memory.models import Memory, now_iso
from NeuralGraph.chat_memory.store import ChatMemoryStore
from mycelic.evidence import EvidenceStore
from mycelic.evidence.migrate import HolderSchemaError, holder_migration_status, latest_holder_version
from mycelic.evidence.store import _MYCELIC_DDL, MYCELIC_SCHEMA_VERSION
from mycelic.util import fingerprint

from .ingest_support import TENANT, question

TEXT = "The VPN outage on March 4 was caused by an expired certificate on the edge gateway."


async def make_v1_file(path: Path) -> None:
    """A holder file exactly as the version-1 code left it: NeuralGraph tables, the baseline Mycelic tables, one document."""
    base = ChatMemoryStore(path)
    c = base._conn
    c.executescript(_MYCELIC_DDL)
    now = now_iso()
    c.execute("INSERT INTO holder_meta(key, value) VALUES ('mycelic_schema_version', '1')")
    c.execute("""INSERT INTO documents(doc_id, title, kind, text, source_root_id, origin_id, observed_at, domains, summary, status, version, chars,
                                       uploaded_by, created_at, updated_at) VALUES ('doc_old', 'Ops log', 'note', ?, ?, NULL, ?, '["ops"]', '',
                                       'active', 1, ?, 'ana', ?, ?)""", (TEXT, fingerprint(TEXT), now, len(TEXT), now, now))
    base._upsert_chat_sync(c, "doc_old", title="Ops log")
    m = Memory(memory_id="mem_old", text=TEXT, kind="fact", subject="ops log", subject_name="Ops log", speaker="Ops log", chat_id="doc_old",
               importance=0.6, confidence=0.9, event_time=None, event_time_precision="none", observed_at=now, created_at=now, updated_at=now,
               metadata={"doc_id": "doc_old", "chunk_index": 0})
    base._insert_memory_sync(c, m, [], [])
    await base.close()


async def test_a_v1_holder_upgrades_on_open_and_keeps_answering(tmp_path: Path) -> None:
    path = tmp_path / "evidence.db"
    await make_v1_file(path)
    store = EvidenceStore(path, holder_id="hold_old", tenant_id=TENANT)
    c = store.store._conn
    status = holder_migration_status(c)
    assert [m["version"] for m in status["applied"]] == list(range(1, latest_holder_version() + 1)) and status["pending"] == []
    assert c.execute("SELECT value FROM holder_meta WHERE key='mycelic_schema_version'").fetchone()[0] == str(MYCELIC_SCHEMA_VERSION)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ingest_records", "ingest_queue", "connectors", "connector_credentials", "domain_memberships", "domain_membership_history",
            "record_locator", "deletion_tombstones", "acl_memberships", "ingest_outbox", "shard_map"} <= tables
    assert c.execute("SELECT root_known FROM documents WHERE doc_id='doc_old'").fetchone()[0] == 1     # new column, old row
    assert c.execute("SELECT shard_id, file_name FROM shard_map").fetchall()[0][:] == ("s0", "evidence.db")
    assert c.execute("PRAGMA secure_delete").fetchone()[0] == 1
    resp = await store.answer_question(question("What caused the VPN outage on March 4?"))
    assert resp["status"] == "answered" and resp["evidence_refs"][0]["source_root_id"] == fingerprint(TEXT)
    assert "meta" not in resp["evidence_refs"][0]                                                    # uploads keep their shape
    doc = await store.ingest_document("New note", "A new upload after the upgrade works as before.")
    assert doc["status"] == "active"
    await store.close()
    # reopening applies nothing new
    again = EvidenceStore(path, holder_id="hold_old", tenant_id=TENANT)
    assert len(holder_migration_status(again.store._conn)["applied"]) == latest_holder_version()
    await again.close()


async def test_edited_or_newer_migrations_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "evidence.db"
    store = EvidenceStore(path, holder_id="h", tenant_id=TENANT)
    store.store._conn.execute("UPDATE holder_schema_migrations SET checksum='edited' WHERE version=2")
    await store.close()
    with pytest.raises(HolderSchemaError):
        EvidenceStore(path, holder_id="h", tenant_id=TENANT)
    other = tmp_path / "newer.db"
    s2 = EvidenceStore(other, holder_id="h", tenant_id=TENANT)
    s2.store._conn.execute("INSERT INTO holder_schema_migrations VALUES (99, 'future', 'x', '2030-01-01')")
    await s2.close()
    with pytest.raises(RuntimeError):
        EvidenceStore(other, holder_id="h", tenant_id=TENANT)
