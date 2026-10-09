"""The site process: one site's own records, its NeuralGraph, Tesseract through ``NeuralGraphMemoryAdapter``, the
shipped judge and verdict rules, and the site's own Boundary. Site side only; HQ reaches it through ``wire`` alone.

A question is answered with the shipped ``SiteVerifier.answer`` steps (``ARCHITECTURE.md`` 15.4), in the same order,
with one change: retrieval is Tesseract through the adapter instead of ``edge/verify.retrieve``
(``docs/collective/ROUTING-SPIKE.md`` 1.4):

1. ``Boundary.accept("in", "question")``; the site clock is set from the question's ``as_of`` (a simulation
   convenience);
2. a question answered before re-sends its stored bytes and uses no budget;
3. the pack's daily budgets;
4. an id outside the site's master data gets ``unknown`` without a record read;
5. retrieval: the graph holds one MESSAGE node per own record (forwarded-in copies left out), whose text is the
   narrative plus one line of the record's structured entity values and codes, with the record's received date. The
   query is the question rendered by ``pushdown.questions.render_text`` plus the entity's alias phrases. Tesseract
   ranks the site's whole session with the hash embedder (256 dimensions) and ``limit`` set to the session's node
   count; the adapter's ``local_retrieve`` keeps the returned nodes received in the question window, in Tesseract's
   order, the first ``max_records`` (L = 50). The adapter's ``claim_projection`` exports only the record handle, and
   its claims stay in this process;
6. each exported record is read with ``edge.verify.judge_payload`` and ``edge.verify.lexical_judge``;
   ``edge.verify.decide`` applies the verdict rules;
7. counts become buckets, ``evidence_ref`` is the HMAC of the verdict id with the site's seeded-demo secret, the
   verdict is stored and sent through the Boundary. ``truncated`` is true when the window held more than L own records.

With ``retrieval = "shipped"`` step 5 is ``edge.verify.retrieve`` with cap ``max_records`` (the equivalence test sets
it to ``verify_max_records``), and the bytes must equal ``SiteVerifier.answer``'s.

Site-local diagnostics (Tesseract time, node counts, the handles read) go to ``site-<id>.tesseract.jsonl`` in the
site's own directory; nothing of them crosses the wire.

Process mode: ``python -m research.routing_spike.site_process --config <file>`` reads one request per line on stdin
and writes one response per line on stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.edge.egress import EgressError
from mycelic.collective.edge.records import QuestionLogRow, RecordStore, VerdictRow, WindowRecord
from mycelic.collective.edge.site import EdgeSite, in_master_data
from mycelic.collective.edge.verify import SiteVerifier, _Outcome, decide, judge_question, retrieve
from mycelic.collective.edge.weeks import local_date
from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs.loader import FrozenPack, load_pack
from mycelic.collective.pushdown.questions import render_text

from . import wire

RETRIEVALS = ("tesseract", "shipped")
SESSION_PREFIX = "site:"
EMBED_DIM = 256
CONFIG_KEYS = ("clock_start", "demo_seed", "edge_dir", "embed_dim", "graph_path", "hq_dir", "master_data",
               "max_records", "pack", "retrieval", "site_id")


class SiteClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SiteConfig:
    pack: str
    site_id: str
    edge_dir: str
    hq_dir: str
    master_data: Mapping[str, Sequence[str]]
    demo_seed: int
    retrieval: str
    max_records: int
    graph_path: str
    clock_start: str
    embed_dim: int = EMBED_DIM

    def to_json(self) -> dict[str, Any]:
        return {"clock_start": self.clock_start, "demo_seed": self.demo_seed, "edge_dir": self.edge_dir,
                "embed_dim": self.embed_dim, "graph_path": self.graph_path, "hq_dir": self.hq_dir,
                "master_data": {t: list(self.master_data[t]) for t in sorted(self.master_data)},
                "max_records": self.max_records, "pack": self.pack, "retrieval": self.retrieval,
                "site_id": self.site_id}

    @classmethod
    def from_json(cls, obj: Mapping[str, Any]) -> "SiteConfig":
        if sorted(obj) != list(CONFIG_KEYS):
            raise ValueError("a site config has exactly the config keys") from None
        if obj["retrieval"] not in RETRIEVALS:
            raise ValueError("retrieval must be tesseract or shipped") from None
        return cls(pack=obj["pack"], site_id=obj["site_id"], edge_dir=obj["edge_dir"], hq_dir=obj["hq_dir"],
                   master_data={t: tuple(v) for t, v in obj["master_data"].items()}, demo_seed=obj["demo_seed"],
                   retrieval=obj["retrieval"], max_records=obj["max_records"], graph_path=obj["graph_path"],
                   clock_start=obj["clock_start"], embed_dim=obj["embed_dim"])


def node_text(record: WindowRecord) -> str:
    """The narrative plus one line of the record's structured entity values and codes."""
    values = [f"{t} {v}" for t in sorted(record.structured) for v in record.structured[t]]
    return f"{record.narrative}\nentities: {'; '.join(values)} | codes: {' '.join(sorted(record.codes))}"


def query_text(pack: FrozenPack, question: Mapping[str, Any]) -> str:
    """The question rendered by ``render_text`` plus the entity's alias phrases."""
    aliases = judge_question(pack, question["params"])["aliases"]
    return " ".join([render_text(pack, question), *aliases])


# --------------------------------------------------------------------------------------------------- retrieval

class ShippedRetrieval:
    """``edge/verify.retrieve`` with cap ``cap``: the equivalence test's step 5."""

    name = "shipped"

    def __init__(self, process: "SiteProcess", cap: int) -> None:
        self._process = process
        self._cap = cap
        self._cache: dict[tuple[str, str], frozenset[str]] = {}

    def retrieve(self, store: RecordStore, question: Mapping[str, Any]) -> tuple[list[WindowRecord], bool]:
        params = question["params"]
        site = self._process.site
        return retrieve(store, site.canonicaliser, entity_type=params["entity_type"], entity_id=params["entity_id"],
                        window=question["window"], cap=self._cap, cache=self._cache)

    def close(self) -> None:
        pass


class TesseractRetrieval:
    """Tesseract over the site's graph, through ``NeuralGraphMemoryAdapter``; the adapter's claims stay here."""

    name = "tesseract"

    def __init__(self, process: "SiteProcess", *, graph_path: str | Path, max_records: int, embed_dim: int) -> None:
        # the retrieval package (numpy, the backend module) is loaded only on the site side
        from NeuralGraph.chat_memory.llm import fake_embedding
        from NeuralGraph.research.coordination import (AuthorizationContext, CapabilityDescriptor,
                                                       NeuralGraphMemoryAdapter, PolicyStatus, QueryBudget,
                                                       QueryRequest)
        from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer
        from NeuralGraph.research.retrieval.sqlite_storage import SQLiteNeuralGraphStorage
        from NeuralGraph.research.retrieval.tesseract import Tesseract

        for name in ("NeuralGraph", "memmachine"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._process = process
        self._embed = fake_embedding
        self._dim = embed_dim
        self._max = max_records
        self._QueryRequest, self._QueryBudget, self._Auth = QueryRequest, QueryBudget, AuthorizationContext
        self._NeuralNode, self._MESSAGE = NeuralNode, NodeLayer.MESSAGE
        self.session = SESSION_PREFIX + process.config.site_id
        path = Path(graph_path)
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        self._loop = asyncio.new_event_loop()
        self._storage = SQLiteNeuralGraphStorage(db_path=path)
        self._tesseract = Tesseract(self._storage)
        pack = process.pack
        desc = wire.descriptor(process.config.site_id, pack.id, pack.config_hash, sorted(pack.questions))
        self.capability = CapabilityDescriptor(
            capability_id=desc["capability_id"], node_id=desc["node_id"], description=desc["description"],
            query_types=tuple(desc["query_types"]), policy_scope=tuple(desc["policy_scope"]))
        self._adapter = NeuralGraphMemoryAdapter(
            node_id=process.config.site_id, capability=self.capability, local_retrieve=self._local_retrieve,
            policy_filter=lambda node, context: PolicyStatus.ALLOWED, clock=process.clock,
            claim_projection=lambda node: {"record_ref": node.node_id})
        self._current: dict[str, Any] | None = None
        self.nodes = self._loop.run_until_complete(self._build())
        self.build_seconds = 0.0

    def _own_records(self) -> list[tuple[WindowRecord, str]]:
        """Every own record (forwarded-in excluded) with its received date, in ingest order."""
        site = self._process.site
        store = RecordStore(site.store.path, site_id=site.site_id, pack_id=site.pack.id,
                            config_hash=site.pack.config_hash)
        try:
            records = {r.record_ref: r for r in store.window_records("0000-W01", "9999-W53")}
        finally:
            store.close()
        conn = sqlite3.connect(f"file:{site.store.path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT record_ref, received_date FROM records WHERE forwarded_in = 0 "
                                "ORDER BY seq").fetchall()
        finally:
            conn.close()
        return [(records[ref], day) for ref, day in rows if ref in records]

    async def _build(self) -> int:
        nodes = []
        for record, day in self._own_records():
            text = node_text(record)
            received = datetime.fromisoformat(local_date(day) or day).replace(tzinfo=timezone.utc)
            nodes.append(self._NeuralNode(node_id=record.record_ref, layer=self._MESSAGE, content=text,
                                          embedding=self._embed(text, self._dim), session_key=self.session,
                                          created_at=received, updated_at=received, last_activated=received,
                                          metadata={"iso_week": record.iso_week, "received_date": local_date(day)}))
        await self._storage.save_nodes_batch(nodes)
        return len(nodes)

    async def _local_retrieve(self, request: Any) -> list[tuple[Any, float]]:
        current = self._current
        assert current is not None and current["query_id"] == request.query_id
        text = request.content
        t0 = time.perf_counter()
        ranked = await self._tesseract.retrieve(text, self._embed(text, self._dim), self.session, limit=self.nodes)
        current["seconds"] = time.perf_counter() - t0
        current["returned"] = len(ranked)
        start, end = current["window"]["start_week"], current["window"]["end_week"]
        kept = [(node, charge) for node, charge in ranked if start <= node.metadata["iso_week"] <= end]
        current["in_window_returned"] = len(kept)
        return kept[:self._max]

    def retrieve(self, store: RecordStore, question: Mapping[str, Any]) -> tuple[list[WindowRecord], bool]:
        window = question["window"]
        own = {r.record_ref: r for r in store.window_records(window["start_week"], window["end_week"])}
        self._current = {"query_id": question["question_id"], "window": dict(window)}
        request = self._QueryRequest(
            query_id=question["question_id"], content=query_text(self._process.pack, question),
            requester_id=f"site-local:{self._process.config.site_id}", issued_at=self._process.clock(),
            authorization=self._Auth(scopes=wire.POLICY_SCOPE), requested_capability=self.capability.capability_id,
            budget=self._QueryBudget(max_nodes=1, max_claims=self._max, max_verifications=0))
        exports = self._loop.run_until_complete(self._adapter.query(request))
        refs = [claim.content["record_ref"] for export in exports for claim in export.claims]
        records = [own[ref] for ref in refs]
        current, self._current = self._current, None
        self._process.diag({"question_id": question["question_id"], "seconds": current["seconds"],
                            "nodes": self.nodes, "returned": current["returned"],
                            "in_window_returned": current["in_window_returned"], "own_in_window": len(own),
                            "read": len(records), "refs": refs})
        return records, len(own) > self._max

    def close(self) -> None:
        self._storage.close()
        self._loop.close()


# --------------------------------------------------------------------------------------------------- the verifier

class SpikeVerifier(SiteVerifier):
    """``SiteVerifier`` with step 6 (retrieval) taken from ``retrieval``; every other step is the shipped one."""

    def __init__(self, site: EdgeSite, *, clock: SiteClock, demo_seed: int, retrieval: Any) -> None:
        super().__init__(site, runtime=None, clock=clock, demo_seed=demo_seed)
        self._retrieval = retrieval

    def _answer(self, store: RecordStore, question: dict[str, Any]) -> dict[str, Any]:
        # the shipped SiteVerifier._answer, line for line, except the retrieval call
        boundary = self.site.boundary
        ts = self._now()
        day = local_date(ts)
        qid, params = question["question_id"], question["params"]
        t, eid = params["entity_type"], params["entity_id"]
        if self._secret is None:
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "no_secret", ts))
            return boundary.send("out", "verdict", self._body(question, "unknown", reason="no_secret"))
        stored = store.verdict_for_question(qid)
        if stored is not None:
            return boundary.send("out", "verdict", strict_load(stored.body))
        answered = store.answered_count(t, eid, day)
        if answered >= self.pack.egress.question_budget_per_entity_per_day or (
                answered == 0 and store.answered_entities(day) >= self.pack.egress.question_entities_per_site_per_day):
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "budget", ts))
            return boundary.send("out", "verdict", self._body(question, "unknown", reason="budget"))
        if in_master_data(self.pack, self.site.master, t, eid):
            records, truncated = self._retrieval.retrieve(store, question)
            judged, failures = self._judge(question, records)
            outcome = decide(records, judged, failures)
        else:
            records, truncated, judged, failures = [], False, [], 0
            outcome = _Outcome("unknown", "not_master_data", "ok", (), (), 0)
        parsed = boundary.validate("out", "verdict", self._body(question, outcome.verdict, outcome=outcome,
                                                                truncated=truncated))
        if outcome.quality == "degraded":
            store.log_question(QuestionLogRow(0, qid, day, t, eid, "degraded", ts))
            return boundary.send("out", "verdict", parsed)
        data = canonical_bytes(parsed)
        misses = sum(1 for r in outcome.confirming if not store.has_claim(r.record_ref, t, eid, params["predicate"]))
        store.add_verdict(
            VerdictRow(verdict_id=parsed["verdict_id"], question_id=qid, evidence_ref=parsed["evidence_ref"],
                       body=data, sha256=sha256_hex(data), verdict=outcome.verdict,
                       confirming_refs=tuple(r.record_ref for r in outcome.confirming),
                       entity_refs=tuple(r.record_ref for r in outcome.entity), judged=len(judged),
                       failures=failures, unclear=outcome.unclear, extraction_misses=misses,
                       local_reason=outcome.local_reason, truncated=truncated, created_at=ts),
            QuestionLogRow(0, qid, day, t, eid, "answered", ts))
        return boundary.send("out", "verdict", parsed)


# --------------------------------------------------------------------------------------------------- the process

class SiteProcess:
    """One site behind the wire: ``handle(kind, body) -> body``."""

    def __init__(self, config: SiteConfig) -> None:
        self.config = config
        self.pack = load_pack(config.pack)
        self.clock = SiteClock(config.clock_start)
        edge = Path(config.edge_dir)
        self.site = EdgeSite(self.pack, config.site_id, edge, runtime=None, clock=self.clock,
                             master_data=config.master_data, hq_dir=config.hq_dir)
        self.descriptor_bytes = canonical_bytes(wire.descriptor(config.site_id, self.pack.id, self.pack.config_hash,
                                                                sorted(self.pack.questions)))
        self.descriptor_log = edge / f"site-{config.site_id}.descriptors.jsonl"
        self.diag_log = edge / f"site-{config.site_id}.tesseract.jsonl"
        self.retrieval: Any = None
        try:
            if config.retrieval == "tesseract":
                t0 = time.perf_counter()
                self.retrieval = TesseractRetrieval(self, graph_path=config.graph_path,
                                                    max_records=config.max_records, embed_dim=config.embed_dim)
                self.retrieval.build_seconds = time.perf_counter() - t0
            else:
                self.retrieval = ShippedRetrieval(self, config.max_records)
            self.verifier = SpikeVerifier(self.site, clock=self.clock, demo_seed=config.demo_seed,
                                          retrieval=self.retrieval)
        except BaseException:
            self.close()
            raise

    def diag(self, row: Mapping[str, Any]) -> None:
        with open(self.diag_log, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical_dumps(dict(row)) + "\n")

    def handle(self, kind: str, body: bytes) -> bytes:
        if kind == "describe":
            if body:
                raise wire.WireError("bad_request")
            data = self.descriptor_bytes
            with open(self.descriptor_log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_dumps({"body": strict_load(data), "bytes": len(data), "sha256": sha256_hex(data),
                                          "site": self.config.site_id, "ts": self.clock()}) + "\n")
            return data
        if kind != "question":
            raise wire.WireError("unknown_kind")
        try:
            question = strict_load(body)
        except StrictJsonError:
            raise wire.WireError("refused") from None
        as_of = question.get("as_of") if isinstance(question, dict) else None
        if not isinstance(as_of, str) or local_date(as_of) != as_of:
            raise wire.WireError("refused")
        self.clock.set(f"{as_of}T12:00:00Z")
        try:
            verdict = self.verifier.answer(question)
        except EgressError:
            raise wire.WireError("refused") from None
        return canonical_bytes(verdict)

    def close(self) -> None:
        if self.retrieval is not None:
            self.retrieval.close()
        self.site.close()


def write_config(config: SiteConfig, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_dumps(config.to_json()) + "\n", encoding="utf-8")
    return path


def read_config(path: str | Path) -> SiteConfig:
    return SiteConfig.from_json(strict_load(Path(path).read_bytes()))


def serve(config: SiteConfig, stdin: Any, stdout: Any) -> int:
    process = SiteProcess(config)
    try:
        for line in iter(stdin.readline, b""):
            stdout.write(wire.serve_line(process.handle, line))
            stdout.flush()
    finally:
        process.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.routing_spike.site_process",
                                     description="One site of the routing spike, behind the wire (synthetic).")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    return serve(read_config(args.config), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main())
