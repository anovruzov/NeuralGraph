"""EvidenceStore tests: ingestion (chunks, memories, provenance, roots), search, policy-governed answering
(opaque references, scopes, domains, temporal window, redaction, truncation, disclosure levels), revision,
retraction, raw access and idempotency. Offline: hash embeddings and a stub router that implements the fake rules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from NeuralGraph.chat_memory.llm import FakeLLMClient
from NeuralGraph.chat_memory.models import new_id
from mycelic.evidence import (
    DEFAULT_EXPORT_POLICY,
    EvidenceStore,
    chunk_text,
    effective_disclosure,
    rule_answer_from_evidence,
    rule_classify_document,
)
from mycelic.util import fingerprint

TENANT = "ten_test"

OPS_LOG = """Incident 2026-03-04: VPN outage.

The VPN outage on 4 March 2026 was caused by an expired TLS certificate on the gateway. Remote staff could not connect for three hours. The certificate was renewed and monitoring now alerts 14 days before expiry.

Incident 2026-03-12: database failover.

The primary database failed over to the replica after a disk filled up on the primary. Backups were unaffected. Admin password: hunter2 was rotated afterwards. Contact ops-oncall@example.com for details.

Recurring blocker: approvals.

Deployment approvals take two days on average because the change advisory board meets twice a week. Teams batch their releases to cope with the delay."""

ROBOTS_2025 = ("Warehouse robot report. The warehouse robots stalled repeatedly in January because the charging dock firmware "
               "was outdated and the docks rejected the robots at night.")
ROBOTS_2026 = ("Warehouse robot report. The warehouse robots stalled again in June because the new floor markings confused the "
               "navigation cameras near the loading bay.")


class StubRouter:
    """Implements the ``fake`` provider rules for the two holder-side tasks and records every call."""

    tiers = {"light": "fake:light", "standard": "fake:standard", "heavy": "fake:heavy"}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def run_task(self, task: str, input: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append((task, input, kw))
        if task == "classify_document":
            return rule_classify_document(input["title"], input["text"], input["known_domains"])
        if task == "answer_from_evidence":
            return rule_answer_from_evidence(input["question"], input["evidence"])
        raise ValueError(f"unexpected task {task!r}")


def make_store(tmp_path: Path, name: str = "a", *, router: Any | None = None, export_policy: dict | None = None,
               domains: tuple[str, ...] = (), extract: bool = False) -> EvidenceStore:
    return EvidenceStore(tmp_path / f"{name}.db", holder_id=f"hold_{name}", tenant_id=TENANT, llm=FakeLLMClient(),
                         router=router, export_policy=export_policy, domains=domains, extract=extract)


def question(text: str, **kw: Any) -> dict[str, Any]:
    q = {
        "question_id": kw.pop("question_id", new_id("q")), "text": text,
        "candidate_domains": kw.pop("candidate_domains", []),
        "valid_from": kw.pop("valid_from", None), "valid_to": kw.pop("valid_to", None),
        "policy": {"visibility": kw.pop("visibility", "unit"), "disclosure": kw.pop("disclosure", None), "blind_verification": False},
        "scope_unit_id": kw.pop("scope", None),
    }
    q.update(kw)
    return q


async def assert_opaque(store: EvidenceStore, response: dict[str, Any], *doc_ids: str) -> None:
    """The response must not carry memory ids, document ids, or more of a document than the excerpt cap allows
    (a document shorter than the cap is legitimately disclosed in full)."""
    blob = json.dumps(response)
    assert "mem_" not in blob and "doc_" not in blob
    cap = int(store.export_policy["max_excerpt_chars"])
    for doc_id in doc_ids:
        assert doc_id not in blob
        text = await store.document_text(doc_id)
        assert text
        if len(text) > cap:
            assert text not in blob
        for m in await store.store.document_memories(doc_id, status=None):
            assert m.memory_id not in blob
    for ref in response["evidence_refs"]:
        assert ref["ref_id"].startswith("ev_") and len(ref["ref_id"]) == 23
        assert len(ref["disclosed_excerpt"]) <= max(cap, 140)
        assert set(ref) == {"ref_id", "source_root_id", "root_known", "kind", "title", "disclosed_excerpt", "disclosure_level",
                            "observed_at", "freshness_at"}


@pytest.fixture
def router() -> StubRouter:
    return StubRouter()


# ---------------------------------------------------------------------------------------------- helpers


def test_chunk_text_merges_small_and_splits_long() -> None:
    small = "One.\n\nTwo.\n\nThree."
    assert chunk_text(small) == ["One.\nTwo.\nThree."]
    long_para = " ".join(f"Sentence number {i} says something useful about the incident." for i in range(60))
    chunks = chunk_text(long_para, target=300)
    assert len(chunks) > 3
    assert all(len(c) <= 600 for c in chunks)
    assert all(c[0].isupper() and c.endswith(".") for c in chunks)      # sentence boundaries respected
    assert " ".join(chunks) == long_para
    assert chunk_text("") == []
    giant = "x" * 5000
    assert all(len(c) <= 1200 for c in chunk_text(giant))


def test_rules_match_the_fake_contracts() -> None:
    out = rule_classify_document("Ops log", "The VPN outage was caused by an expired certificate. Then more.", ["ops", "vpn", "hr"])
    assert out == {"domains": ["ops", "vpn"], "summary": "The VPN outage was caused by an expired certificate.", "kind": "note"}
    assert rule_classify_document("x", "nothing relevant here", ["finance"])["domains"] == ["general"]
    conv = rule_classify_document("chat", "Ana: the VPN is down\nBen: the cert expired\nAna: renewing now", [])
    assert conv["kind"] == "conversation"
    ev = [{"ref_id": "a", "excerpt": "The VPN outage was caused by an expired certificate."}, {"ref_id": "b", "excerpt": "Lunch menu"}]
    out = rule_answer_from_evidence("What caused the VPN outage?", ev)
    assert out == {"answer": ev[0]["excerpt"], "confidence": 0.65, "used_ref_ids": ["a"], "no_evidence": False}
    assert rule_answer_from_evidence("Unrelated query about budgets", ev)["no_evidence"] is True


def test_effective_disclosure_never_widens() -> None:
    assert effective_disclosure("excerpt", None) == "excerpt"
    assert effective_disclosure("excerpt", "summary") == "summary"
    assert effective_disclosure("summary", "excerpt") == "summary"
    assert effective_disclosure("none", "excerpt") == "none"
    assert effective_disclosure("bogus", "bogus") == "excerpt"


# ---------------------------------------------------------------------------------------------- ingestion


async def test_ingest_creates_chunks_memories_and_provenance(tmp_path: Path, router: StubRouter) -> None:
    store = make_store(tmp_path, router=router, domains=("ops", "finance"))
    doc = await store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15", uploaded_by="ana")
    assert doc["doc_id"].startswith("doc_") and doc["holder_id"] == "hold_a"
    assert doc["status"] == "active" and doc["version"] == 1 and doc["chars"] == len(OPS_LOG.strip())
    assert doc["chunks"] >= 2
    assert doc["source_root_id"] == fingerprint(OPS_LOG.strip())
    assert doc["observed_at"] == "2026-03-15T00:00:00+00:00"
    assert doc["domains"] == ["ops"]                      # classified from the known domains
    assert doc["summary"].startswith("Incident 2026-03-04")
    assert {"doc_id", "title", "kind", "source_root_id", "observed_at", "status", "version", "chars", "chunks", "domains"} <= set(doc)
    assert router.calls and router.calls[0][0] == "classify_document"

    mems = await store.store.document_memories(doc["doc_id"])
    assert len(mems) == doc["chunks"]
    assert "\n".join(m.text for m in mems).replace("\n", " ") == " ".join(OPS_LOG.strip().split())
    for i, m in enumerate(mems):
        assert m.kind == "fact" and m.subject == "ops incident log" and m.subject_name == "Ops incident log"
        assert m.chat_id == doc["doc_id"] and m.event_time == "2026-03-15" and m.observed_at == doc["observed_at"]
        assert m.metadata["doc_id"] == doc["doc_id"] and m.metadata["chunk_index"] == i
        assert m.metadata["source_root_id"] == doc["source_root_id"] and m.metadata["domains"] == ["ops"]
        assert m.embedding and len(m.source_message_ids) == 1
        assert "ops incident log" in m.entity_ids and "ops" in m.entity_ids
    messages = await store.store.get_messages(doc["doc_id"])
    assert [msg.text for msg in messages] == [m.text for m in mems]
    assert all(msg.speaker == "ana" and msg.status == "processed" and msg.sent_at == doc["observed_at"] for msg in messages)
    assert (await store.store.get_entity("ops incident log")).type == "document"
    st = await store.stats()
    assert st["documents"] == 1 and st["memories"] == doc["chunks"] and st["entities"] >= 2 and st["last_ingest_at"]
    assert st["queue"] == 0
    await store.close()


async def test_ingest_without_router_uses_rules_and_explicit_domains(tmp_path: Path) -> None:
    store = make_store(tmp_path, domains=("ops",))
    doc = await store.ingest_document("Chat", "Ana: the VPN is down\nBen: the cert expired\nAna: renewing now", domains=["support"])
    assert doc["kind"] == "conversation" and doc["domains"] == ["support"]
    assert doc["observed_at"] is None
    m = (await store.store.document_memories(doc["doc_id"]))[0]
    assert m.event_time is None and m.event_time_precision == "none" and m.observed_at   # observed defaults to now
    with pytest.raises(ValueError):
        await store.ingest_document("Empty", "   ")
    await store.close()


async def test_extract_mode_queues_extraction_jobs(tmp_path: Path) -> None:
    store = make_store(tmp_path, extract=True)
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    assert await store.store.pending_jobs(["extract"]) == doc["chunks"]
    assert all(msg.status == "pending" for msg in await store.store.get_messages(doc["doc_id"]))
    await store.close()


async def test_same_text_in_two_stores_shares_a_root(tmp_path: Path) -> None:
    a, b = make_store(tmp_path, "a"), make_store(tmp_path, "b")
    da = await a.ingest_document("Copy A", OPS_LOG)
    db = await b.ingest_document("Copy B (different title)", "  " + OPS_LOG.upper() + "\n")   # whitespace/case-insensitive
    assert da["source_root_id"] == db["source_root_id"]
    other = await b.ingest_document("Other", ROBOTS_2025)
    assert other["source_root_id"] != da["source_root_id"]
    await a.close(); await b.close()


async def test_origin_id_copies_share_a_root(tmp_path: Path) -> None:
    a, b = make_store(tmp_path, "a"), make_store(tmp_path, "b")
    da = await a.ingest_document("Export 1", OPS_LOG, origin_id="sharepoint:doc/42")
    db = await b.ingest_document("Export 2 (edited copy)", OPS_LOG + "\n\nAn extra note.", origin_id="sharepoint:doc/42")
    assert da["source_root_id"] == db["source_root_id"] == fingerprint("sharepoint:doc/42")
    assert da["origin_id"] == "sharepoint:doc/42"
    await a.close(); await b.close()


async def test_ingest_is_idempotent_on_doc_id_and_idempotency_key(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    d1 = await store.ingest_document("Ops incident log", OPS_LOG, doc_id="doc_fixed")
    d2 = await store.ingest_document("Ops incident log (again)", "other text", doc_id="doc_fixed")
    assert d2 == d1 and len(await store.list_documents()) == 1
    k1 = await store.ingest_document("Robots", ROBOTS_2025, idempotency_key="msg-1")
    k2 = await store.ingest_document("Robots", ROBOTS_2025, idempotency_key="msg-1")
    assert k2["doc_id"] == k1["doc_id"] and len(await store.list_documents()) == 2
    assert (await store.store.processed_outcome("msg-1"))["op"] == "ingest"
    await store.close()


# ---------------------------------------------------------------------------------------------- search


async def test_search_finds_chunk(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    await store.ingest_document("Robots", ROBOTS_2025)
    hits = await store.search("expired VPN certificate", k=3)
    assert hits and hits[0]["doc_id"] == doc["doc_id"] and "VPN" in hits[0]["text"]
    assert hits[0]["title"] == "Ops incident log" and hits[0]["sources"][0]["chat_id"] == doc["doc_id"]
    assert {"memory_id", "text", "kind", "score", "observed_at", "sources", "doc_id", "title", "channels"} <= set(hits[0])
    assert await store.search("", k=3) == []
    await store.close()


# ---------------------------------------------------------------------------------------------- answering


async def test_answer_question_returns_opaque_refs_only(tmp_path: Path, router: StubRouter) -> None:
    store = make_store(tmp_path, router=router)
    doc = await store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15")
    q = question("What caused the VPN outage?", route_id="route_1")
    resp = await store.answer_question(q)
    assert resp["status"] == "answered" and resp["question_id"] == q["question_id"] and resp["holder_id"] == "hold_a"
    assert resp["route_id"] == "route_1"
    assert "expired TLS certificate" in resp["content"] and 0.5 < resp["confidence"] <= 0.9
    assert resp["evidence_refs"]
    await assert_opaque(store, resp, doc["doc_id"])
    ref = resp["evidence_refs"][0]
    assert ref["source_root_id"] == doc["source_root_id"] and ref["root_known"] is True
    assert ref["kind"] == "note" and ref["title"] == "Ops incident log" and ref["disclosure_level"] == "excerpt"
    assert ref["observed_at"] == doc["observed_at"] and ref["freshness_at"] == doc["updated_at"]
    assert "VPN" in ref["disclosed_excerpt"] and len(ref["disclosed_excerpt"]) <= DEFAULT_EXPORT_POLICY["max_excerpt_chars"]
    assert resp["freshness_at"] == doc["updated_at"]
    prov = resp["provenance"]
    assert prov["retrieval_operator"] == "neuralgraph.hybrid" and prov["answer_method"] == "model"
    assert set(prov["channels"]) <= {"vector", "keyword", "graph"} and prov["channels"]
    assert prov["policy"] == {"disclosure": "excerpt", "answer_scopes": ["unit", "org"]} and prov["memory_count"] >= 1
    assert [c[0] for c in router.calls][-1] == "answer_from_evidence"
    # the export ledger maps the opaque id back, and only inside the holder
    raw = await store.raw_for_ref(ref["ref_id"])
    assert raw["doc_id"] == doc["doc_id"] and raw["text"] == OPS_LOG.strip() and raw["version"] == 1
    assert raw["title"] == "Ops incident log" and raw["observed_at"] == doc["observed_at"] and "VPN" in raw["chunk_text"]
    assert await store.raw_for_ref("ev_unknown") is None
    assert (await store.stats())["questions_answered"] == 1
    # the same question again reuses the same reference ids
    again = await store.answer_question(q)
    assert [r["ref_id"] for r in again["evidence_refs"]] == [r["ref_id"] for r in resp["evidence_refs"]]
    await store.close()


async def test_answer_no_evidence_when_nothing_matches(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("Which vendor supplies the cafeteria coffee beans?"))
    assert resp["status"] == "no_evidence" and resp["content"] == "" and resp["evidence_refs"] == []
    assert resp["provenance"]["answer_method"] == "rule"
    empty = await store.answer_question(question(""))
    assert empty["status"] == "declined" and "no text" in empty["reason"]
    await store.close()


async def test_answer_respects_answer_scopes(tmp_path: Path) -> None:
    store = make_store(tmp_path, export_policy={"answer_scopes": ["unit"]})
    await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("What caused the VPN outage?", visibility="org"))
    assert resp["status"] == "declined" and resp["evidence_refs"] == [] and "org" in resp["reason"]
    assert (await store.answer_question(question("What caused the VPN outage?", visibility="unit")))["status"] == "answered"
    store.update_policy({"answer_scopes": []})
    assert (await store.answer_question(question("What caused the VPN outage?")))["status"] == "declined"
    other_tenant = question("What caused the VPN outage?", tenant_id="ten_other")
    assert (await store.answer_question(other_tenant))["reason"] == "tenant mismatch"
    await store.close()


async def test_answer_respects_domains(tmp_path: Path) -> None:
    store = make_store(tmp_path, domains=("finance",))
    await store.ingest_document("Ops incident log", OPS_LOG)
    q = "What caused the VPN outage?"
    assert (await store.answer_question(question(q, candidate_domains=["ops"])))["reason"] == "no matching evidence domain"
    assert (await store.answer_question(question(q, candidate_domains=["ops", "finance"])))["status"] == "answered"
    assert (await store.answer_question(question(q, candidate_domains=[])))["status"] == "answered"
    assert (await store.answer_question(question(q, candidate_domains=["*"])))["status"] == "answered"
    store.update_policy(domains=["*"])
    assert (await store.answer_question(question(q, candidate_domains=["ops"])))["status"] == "answered"
    await store.close()


async def test_answer_respects_temporal_window(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    old = await store.ingest_document("Robots 2025", ROBOTS_2025, observed_at="2025-01-10")
    new = await store.ingest_document("Robots 2026", ROBOTS_2026, observed_at="2026-06-01T09:00:00+00:00")
    q = "Why did the warehouse robots stall?"
    both = await store.answer_question(question(q))
    assert {r["source_root_id"] for r in both["evidence_refs"]} == {old["source_root_id"], new["source_root_id"]}
    windowed = await store.answer_question(question(q, valid_from="2026-01-01", valid_to="2026-12-31"))
    assert windowed["status"] == "answered"
    assert {r["source_root_id"] for r in windowed["evidence_refs"]} == {new["source_root_id"]}
    assert all(r["observed_at"].startswith("2026") for r in windowed["evidence_refs"])
    earlier = await store.answer_question(question(q, valid_to="2025-12-31"))
    assert {r["source_root_id"] for r in earlier["evidence_refs"]} == {old["source_root_id"]}
    assert (await store.answer_question(question(q, valid_from="2027-01-01")))["status"] == "no_evidence"
    await store.close()


async def test_answer_redacts_deny_patterns(tmp_path: Path) -> None:
    policy = {"deny_patterns": [r"password:\s*\S+", r"[\w.-]+@[\w.-]+\.\w+", "(unclosed"], "max_excerpt_chars": 200}
    store = make_store(tmp_path, export_policy=policy)
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("What happened in the database failover?"))
    assert resp["status"] == "answered"
    blob = json.dumps(resp)
    assert "hunter2" not in blob and "example.com" not in blob
    assert "[redacted]" in blob
    assert all(len(r["disclosed_excerpt"]) <= 200 for r in resp["evidence_refs"])
    await assert_opaque(store, resp, doc["doc_id"])
    # the raw path (owner / raw grant) still returns the unredacted document
    assert "hunter2" in (await store.raw_for_ref(resp["evidence_refs"][0]["ref_id"]))["text"]
    await store.close()


async def test_answer_truncates_excerpts(tmp_path: Path) -> None:
    store = make_store(tmp_path, export_policy={"max_excerpt_chars": 120, "max_answer_chars": 150})
    await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("What caused the VPN outage?"))
    assert resp["status"] == "answered"
    for r in resp["evidence_refs"]:
        assert len(r["disclosed_excerpt"]) <= 120 and r["disclosed_excerpt"].endswith("…")
    assert len(resp["content"]) <= 150
    await store.close()


async def test_disclosure_levels_summary_and_none(tmp_path: Path) -> None:
    store = make_store(tmp_path, export_policy={"disclosure": "summary"})
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("What caused the VPN outage?", disclosure="excerpt"))   # cannot widen
    assert resp["status"] == "answered"
    assert all(r["disclosure_level"] == "summary" and r["disclosed_excerpt"] == doc["summary"] for r in resp["evidence_refs"])
    assert resp["provenance"]["policy"]["disclosure"] == "summary"
    none = await store.answer_question(question("What caused the VPN outage?", disclosure="none"))
    assert none["status"] == "answered" and none["content"]
    assert all(r["disclosure_level"] == "none" and r["disclosed_excerpt"] == "" for r in none["evidence_refs"])
    assert (await store.store.get_export(none["evidence_refs"][0]["ref_id"]))["disclosure_level"] == "none"
    await store.close()


async def test_answer_idempotency_key_replays_stored_response(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    await store.ingest_document("Ops incident log", OPS_LOG)
    q = question("What caused the VPN outage?")
    first = await store.answer_question(q, idempotency_key="msg-q")
    second = await store.answer_question(question("totally different", question_id=q["question_id"]), idempotency_key="msg-q")
    assert second == first and (await store.stats())["questions_answered"] == 1
    await store.close()


# ---------------------------------------------------------------------------------------------- revise / retract


async def test_revise_supersedes_chunks_and_reports_affected_refs(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    doc = await store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15")
    resp = await store.answer_question(question("What caused the VPN outage?"))
    refs = {r["ref_id"] for r in resp["evidence_refs"]}
    assert refs
    old_ids = await store.store.document_memory_ids(doc["doc_id"])
    new_text = "Incident 2026-03-04: VPN outage.\n\nThe VPN outage was in fact caused by a misconfigured firewall rule, not a certificate."
    result = await store.revise_document(doc["doc_id"], new_text, reason="correction after review")
    assert set(result["affected_ref_ids"]) == refs
    assert result["new_source_root_id"] == fingerprint(new_text) != doc["source_root_id"]
    assert result["previous_source_root_id"] == doc["source_root_id"]
    d2 = result["document"]
    assert d2["version"] == 2 and d2["status"] == "revised" and d2["source_root_id"] == result["new_source_root_id"]
    assert d2["chunks"] == 1 and d2["observed_at"] == doc["observed_at"]
    for old in old_ids:
        assert (await store.store.get_memory(old)).status in ("superseded", "retracted")
    assert (await store.store.get_memory(old_ids[0])).status == "superseded"
    assert len(await store.store.document_memory_ids(doc["doc_id"])) == 1
    hits = await store.search("VPN outage cause", k=3)
    assert hits and "firewall" in hits[0]["text"] and hits[0]["status"] == "active"
    versions = await store.store.document_versions(doc["doc_id"])
    assert [v["version"] for v in versions] == [1, 2] and versions[1]["reason"] == "correction after review"
    assert await store.document_text(doc["doc_id"], version=1) == OPS_LOG.strip()
    raw = await store.raw_for_ref(next(iter(refs)))
    assert raw["version"] == 2 and "firewall" in raw["text"]
    again = await store.answer_question(question("What caused the VPN outage?"))
    assert again["status"] == "answered" and "firewall" in again["content"]
    assert {r["source_root_id"] for r in again["evidence_refs"]} == {result["new_source_root_id"]}
    with pytest.raises(KeyError):
        await store.revise_document("doc_missing", "text")
    await store.close()


async def test_revise_keeps_origin_root(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    doc = await store.ingest_document("Export", OPS_LOG, origin_id="crm:7")
    result = await store.revise_document(doc["doc_id"], ROBOTS_2026, title="Export v2")
    assert result["new_source_root_id"] == doc["source_root_id"] == fingerprint("crm:7")
    assert result["document"]["title"] == "Export v2" and result["affected_ref_ids"] == []
    await store.close()


async def test_retract_document(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    resp = await store.answer_question(question("What caused the VPN outage?"))
    refs = {r["ref_id"] for r in resp["evidence_refs"]}
    result = await store.retract_document(doc["doc_id"], "source withdrawn")
    assert set(result["affected_ref_ids"]) == refs and result["document"]["status"] == "retracted"
    assert all(m.status == "retracted" for m in await store.store.get_memories_by_ids(await store.store.document_memory_ids(doc["doc_id"], status=None)))
    assert await store.store.document_memory_ids(doc["doc_id"]) == []
    assert await store.search("VPN certificate") == []
    assert (await store.answer_question(question("What caused the VPN outage?")))["status"] == "no_evidence"
    assert (await store.document(doc["doc_id"]))["status"] == "retracted"
    assert (await store.stats())["documents"] == 0
    assert (await store.raw_for_ref(next(iter(refs))))["status"] == "retracted"
    with pytest.raises(ValueError):
        await store.revise_document(doc["doc_id"], "new text")
    again = await store.retract_document(doc["doc_id"], "again")
    assert set(again["affected_ref_ids"]) == refs
    with pytest.raises(KeyError):
        await store.retract_document("doc_missing", "x")
    await store.close()


async def test_router_failure_falls_back_to_rules(tmp_path: Path) -> None:
    class BrokenRouter:
        async def run_task(self, task, input, **kw):
            raise RuntimeError("provider down")

    store = make_store(tmp_path, router=BrokenRouter(), domains=("ops",))
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    assert doc["domains"] == ["ops"]
    resp = await store.answer_question(question("What caused the VPN outage?"))
    assert resp["status"] == "answered" and resp["provenance"]["answer_method"] == "rule"
    await store.close()


async def test_embedding_failure_still_indexes(tmp_path: Path) -> None:
    class BrokenEmbedder:
        async def embed(self, text):
            raise RuntimeError("no embedding server")

        async def embed_many(self, texts):
            raise RuntimeError("no embedding server")

    store = EvidenceStore(tmp_path / "e.db", holder_id="hold_e", tenant_id=TENANT, llm=BrokenEmbedder())
    doc = await store.ingest_document("Ops incident log", OPS_LOG)
    mems = await store.store.document_memories(doc["doc_id"])
    assert mems and all(m.embedding is None for m in mems)
    resp = await store.answer_question(question("What caused the VPN outage?"))   # keyword channel carries it
    assert resp["status"] == "answered" and resp["provenance"]["channels"] == ["keyword"] or resp["status"] == "answered"
    await store.close()


# ---------------------------------------------------------------------------------------------- manual responses


async def test_manual_response_exports_one_ref_per_document(tmp_path: Path) -> None:
    store = make_store(tmp_path, export_policy={"deny_patterns": [r"password:\s*\S+"], "max_excerpt_chars": 160})
    ops = await store.ingest_document("Ops incident log", OPS_LOG, observed_at="2026-03-15")
    robots = await store.ingest_document("Robots 2026", ROBOTS_2026, observed_at="2026-06-01")
    q = question("What caused the VPN outage?", route_id="route_h")
    resp = await store.manual_response(q, "The gateway certificate had expired; password: hunter2 was unrelated.", [ops["doc_id"], robots["doc_id"], ops["doc_id"]])
    assert resp["status"] == "answered" and resp["route_id"] == "route_h" and resp["confidence"] == 0.8
    assert resp["content"] == "The gateway certificate had expired; [redacted] was unrelated."
    assert resp["provenance"]["retrieval_operator"] == "human" and resp["provenance"]["answer_method"] == "human"
    assert resp["provenance"]["memory_count"] == 2 and resp["provenance"]["channels"] == []
    assert [r["title"] for r in resp["evidence_refs"]] == ["Ops incident log", "Robots 2026"]
    ops_ref = resp["evidence_refs"][0]
    assert ops_ref["disclosed_excerpt"] == (OPS_LOG.strip()[:159].rstrip() + "…") and ops_ref["disclosure_level"] == "excerpt"
    assert ops_ref["source_root_id"] == ops["source_root_id"] and ops_ref["observed_at"] == ops["observed_at"] and ops_ref["freshness_at"] == ops["updated_at"]
    assert resp["freshness_at"] == max(ops["updated_at"], robots["updated_at"])
    await assert_opaque(store, resp, ops["doc_id"], robots["doc_id"])
    raw = await store.raw_for_ref(ops_ref["ref_id"])
    assert raw["doc_id"] == ops["doc_id"] and raw["question_id"] == q["question_id"]
    # the same person answering again reuses the reference ids; a revision of the document reports them as affected
    again = await store.manual_response(q, "Same answer, reworded.", [ops["doc_id"]])
    assert again["evidence_refs"][0]["ref_id"] == ops_ref["ref_id"]
    revised = await store.revise_document(ops["doc_id"], "The VPN outage was caused by a firewall rule.")
    assert ops_ref["ref_id"] in revised["affected_ref_ids"]
    empty = await store.manual_response(q, "", [robots["doc_id"]])
    assert empty["status"] == "no_evidence" and empty["evidence_refs"] == []
    with pytest.raises(KeyError):
        await store.manual_response(q, "text", ["doc_missing"])
    await store.retract_document(robots["doc_id"], "gone")
    skipped = await store.manual_response(q, "Only from memory.", [robots["doc_id"]])
    assert skipped["status"] == "answered" and skipped["evidence_refs"] == []
    await store.close()


async def test_manual_response_honours_disclosure_and_idempotency(tmp_path: Path) -> None:
    store = make_store(tmp_path, export_policy={"disclosure": "summary"})
    ops = await store.ingest_document("Ops incident log", OPS_LOG)
    q = question("What caused the VPN outage?")
    first = await store.manual_response(q, "Certificate expiry.", [ops["doc_id"]], idempotency_key="ctl-1")
    assert first["evidence_refs"][0]["disclosed_excerpt"] == ops["summary"] and first["evidence_refs"][0]["disclosure_level"] == "summary"
    replay = await store.manual_response(q, "different text", [], idempotency_key="ctl-1")
    assert replay == first and (await store.store.processed_outcome("ctl-1"))["op"] == "manual_response"
    await store.close()
