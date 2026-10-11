"""A persistent, incremental SQLite NeuralGraph per site, and the routing spike's site process over it
(``docs/collective/E2E-INGEST.md`` section 6, "What ``site_graph.py`` changes against the spike"; M9, M17).

    python -m research.e2e_ingest.site_graph --config FILE

The spike's ``TesseractRetrieval`` deletes and rebuilds the graph file at every start (``site_process.py``
``__init__``, ``_build``). Here:

* :class:`PersistentTesseractRetrieval` keeps the SQLite graph. At start it reads the site store's ``records``
  table (``mode=ro``) for the site's own records (``forwarded_in = 0``) with ``seq`` past a watermark kept in the graph
  file (table :data:`SYNC_TABLE`, one row per session), upserts one MESSAGE node per record keyed on ``record_ref``
  (the storage's ``INSERT OR REPLACE``) with the spike's ``node_text``, the hash embedding and the record's received
  date, in batches of :data:`BATCH`, and moves the watermark after each batch's commit, never before it, so a crash
  between the two leaves nodes the next start upserts again, never nodes the watermark skips. It never deletes a node.
  Then it holds every node of the session by ref, as the spike does, and sets ``nodes`` and ``build_seconds``. It
  re-implements ``__init__`` only (M17) and inherits ``_own_records``, ``_local_retrieve``, ``union``,
  ``entity_matched``, ``retrieve`` and ``close``: a question is answered exactly as the spike answers it, so a site's
  verdict bytes equal the spike's on the same store, and the nodes are the spike's nodes (same id, text, embedding,
  dates and metadata), so a ranking is the spike's ranking (Tesseract breaks ties by node id).
* :class:`E2ESiteProcess` is ``SiteProcess`` with that retrieval in step 5 and nothing else changed (M17).
* :class:`E2EChildProcessEndpoint` is ``wire.ChildProcessEndpoint`` with this module in ``Popen`` (M17).
* The sync writes one row of counts (no ref, no text) to ``site-<id>.graph.jsonl`` in the site's directory.

Harness-side readers (M9): :func:`graph_node_ids` reads the graph file ``mode=ro`` (``SELECT node_id FROM nodes WHERE
session_key = ? AND layer = ?``), :func:`graph_watermark` the sync row, :func:`node_ids_digest` the sha256 over the
sorted ids joined by newlines. :meth:`PersistentTesseractRetrieval.rank` is the ranking of one query text at one
``as_of`` (criterion 3's fixed queries), site side; its node ids are record refs and never leave the site.

Site side: this module loads numpy and the retrieval package by design. HQ-side modules never import it.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from mycelic.collective.edge.records import RecordStore
from mycelic.collective.edge.site import EdgeSite
from mycelic.collective.edge.weeks import local_date
from mycelic.collective.jsonio import canonical_bytes, canonical_dumps, sha256_hex, strict_load
from mycelic.collective.packs.loader import load_pack
from NeuralGraph.research.retrieval.data_types import NodeLayer
from research.routing_spike import wire
from research.routing_spike.site_process import (SESSION_PREFIX, AsOfStorage, ShippedRetrieval, SiteClock, SiteConfig,
                                                 SiteProcess, SpikeVerifier, TesseractRetrieval, node_text,
                                                 query_text, read_config, write_config)

__all__ = ["BATCH", "MODULE", "SYNC_TABLE", "E2EChildProcessEndpoint", "E2ESiteProcess",
           "PersistentTesseractRetrieval", "close_endpoints", "graph_node_count", "graph_node_ids",
           "graph_watermark", "main", "node_ids_digest", "open_endpoints", "read_sync_log", "serve", "sync_log_path",
           "node_text", "query_text", "read_config", "write_config"]

MODULE = "research.e2e_ingest.site_graph"
SYNC_TABLE = "e2e_sync"
BATCH = 500
MESSAGE_LAYER = NodeLayer.MESSAGE.value
_CREATE_SYNC = (f"CREATE TABLE IF NOT EXISTS {SYNC_TABLE} (session_key TEXT PRIMARY KEY, seq INTEGER NOT NULL, "
                "updated_at TEXT NOT NULL)")
_READ_SYNC = f"SELECT seq FROM {SYNC_TABLE} WHERE session_key = ? ORDER BY session_key LIMIT 1"
_WRITE_SYNC = f"INSERT OR REPLACE INTO {SYNC_TABLE} (session_key, seq, updated_at) VALUES (?, ?, ?)"
_PENDING = ("SELECT seq, record_ref, received_date FROM records WHERE forwarded_in = 0 AND seq > ? ORDER BY seq")
_NODE_IDS = "SELECT node_id FROM nodes WHERE session_key = ? AND layer = ? ORDER BY node_id"
_NODE_COUNT = "SELECT COUNT(*) FROM nodes WHERE session_key = ? AND layer = ?"


def sync_log_path(edge_dir: str | Path, site_id: str) -> Path:
    return Path(edge_dir) / f"site-{site_id}.graph.jsonl"


# --------------------------------------------------------------------------------------------------- the graph

class PersistentTesseractRetrieval(TesseractRetrieval):
    """The spike's ``TesseractRetrieval`` over a graph file that persists across starts (module docstring)."""

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
        self._loop = asyncio.new_event_loop()
        self._storage = SQLiteNeuralGraphStorage(db_path=path)
        self._view = AsOfStorage(self._storage)
        self._tesseract = Tesseract(self._view)
        self._match_cache: dict[tuple[str, str], frozenset[str]] = {}
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
        self._by_ref: dict[str, Any] = {}
        self.graph_path = path
        self.sync: dict[str, Any] = {}
        t0 = time.perf_counter()
        self.nodes = self._loop.run_until_complete(self._sync())
        self.build_seconds = time.perf_counter() - t0

    # ------------------------------------------------------------------ the watermark (in the graph file)
    def _watermark(self) -> int:
        conn = sqlite3.connect(str(self.graph_path))
        try:
            conn.execute(_CREATE_SYNC)
            conn.commit()
            row = conn.execute(_READ_SYNC, (self.session,)).fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            conn.close()

    def _set_watermark(self, seq: int) -> None:
        conn = sqlite3.connect(str(self.graph_path))
        try:
            conn.execute(_CREATE_SYNC)
            conn.execute(_WRITE_SYNC, (self.session, int(seq), self._process.clock()))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ the sync
    def _pending(self, after_seq: int) -> list[tuple[int, str, str]]:
        """``(seq, record_ref, received_date)`` of the site's own records past the watermark, in seq order; the site
        store read ``mode=ro`` as the spike reads it."""
        site = self._process.site
        conn = sqlite3.connect(f"file:{site.store.path}?mode=ro", uri=True)
        try:
            return [(int(seq), ref, day) for seq, ref, day in conn.execute(_PENDING, (after_seq,)).fetchall()]
        finally:
            conn.close()

    def _node(self, record: Any, day: str) -> Any:
        """One MESSAGE node for one own record: the spike's ``_build`` node, field for field."""
        text = node_text(record)
        received = datetime.fromisoformat(local_date(day) or day).replace(tzinfo=timezone.utc)
        return self._NeuralNode(node_id=record.record_ref, layer=self._MESSAGE, content=text,
                                embedding=self._embed(text, self._dim), session_key=self.session,
                                created_at=received, updated_at=received, last_activated=received,
                                metadata={"iso_week": record.iso_week, "received_date": local_date(day)})

    async def _sync(self) -> int:
        """Bring the graph up to the site store (module docstring); returns the session's node count."""
        t0 = time.perf_counter()
        site = self._process.site
        seq_before = self._watermark()
        nodes_before = graph_node_count(self.graph_path, site.site_id)
        pending = self._pending(seq_before)
        added = upserted = 0
        if pending:
            store = RecordStore(site.store.path, site_id=site.site_id, pack_id=site.pack.id,
                                config_hash=site.pack.config_hash)
            try:
                records = {r.record_ref: r for r in store.window_records("0000-W01", "9999-W53")}
            finally:
                store.close()
            for start in range(0, len(pending), BATCH):
                batch = pending[start:start + BATCH]
                nodes = [self._node(records[ref], day) for _, ref, day in batch if ref in records]
                if nodes:
                    before = graph_node_count(self.graph_path, site.site_id)
                    await self._storage.save_nodes_batch(nodes)
                    after = graph_node_count(self.graph_path, site.site_id)
                    added += after - before
                    upserted += len(nodes)
                self._set_watermark(batch[-1][0])
        held = await self._storage.get_nodes_by_session(self.session, self._MESSAGE)
        self._by_ref = {node.node_id: node for node in held}
        self.sync = {"event": "sync", "site": site.site_id, "seq_before": seq_before, "seq_after": self._watermark(),
                     "nodes_before": nodes_before, "nodes_added": added, "nodes_upserted": upserted,
                     "nodes": len(held), "seconds": time.perf_counter() - t0, "ts": self._process.clock()}
        with open(sync_log_path(self._process.config.edge_dir, site.site_id), "a", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(canonical_dumps(self.sync) + "\n")
        return len(held)

    # ------------------------------------------------------------------ one ranking
    def rank(self, text: str, as_of: str, limit: int | None = None) -> list[str]:
        """Tesseract's order over the session's nodes received on or before ``as_of`` for ``text`` (the spike's
        ranking call, ``limit`` the node count), the first ``limit`` node ids. Site side; the ids are record refs."""
        if not isinstance(as_of, str) or local_date(as_of) != as_of:
            raise ValueError("as_of must be a calendar date") from None
        self._view.cutoff, self._view.last_count = as_of, None
        try:
            ranked = self._loop.run_until_complete(
                self._tesseract.retrieve(text, self._embed(text, self._dim), self.session, limit=self.nodes))
        finally:
            self._view.cutoff = None
        ids = [node.node_id for node, _ in ranked]
        return ids if limit is None else ids[:limit]


# --------------------------------------------------------------------------------------------------- the process

class E2ESiteProcess(SiteProcess):
    """``SiteProcess`` with the persistent retrieval in step 5 and nothing else changed (M17)."""

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
                self.retrieval = PersistentTesseractRetrieval(self, graph_path=config.graph_path,
                                                              max_records=config.max_records,
                                                              embed_dim=config.embed_dim)
                self.retrieval.build_seconds = time.perf_counter() - t0
            else:
                self.retrieval = ShippedRetrieval(self, config.max_records)
            self.verifier = SpikeVerifier(self.site, clock=self.clock, demo_seed=config.demo_seed,
                                          retrieval=self.retrieval)
        except BaseException:
            self.close()
            raise


class E2EChildProcessEndpoint(wire.ChildProcessEndpoint):
    """``python -m research.e2e_ingest.site_graph --config <file>``: the spike's child-process endpoint with this
    module in ``Popen`` (M17); ``request`` and ``close`` are inherited."""

    def __init__(self, site_id: str, config_path: str | Path, *, log_path: str | Path | None = None,
                 python: str | None = None) -> None:
        wire._Logged.__init__(self, site_id, log_path)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(wire.ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self._proc = subprocess.Popen([python or sys.executable, "-m", MODULE,
                                       "--config", str(config_path)], cwd=str(wire.ROOT), env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._io = threading.Lock()

    @property
    def pid(self) -> int:
        return self._proc.pid


def open_endpoints(configs: Mapping[str, SiteConfig], mode: str, wire_log: Path, config_dir: Path) -> dict[str, Any]:
    """One endpoint per site, as the spike's ``run.open_endpoints``, with this module's process and endpoint."""
    if mode not in ("in_process", "process"):
        raise ValueError("mode is in_process or process") from None
    out: dict[str, Any] = {}
    try:
        for sid in sorted(configs):
            if mode == "process":
                path = write_config(configs[sid], Path(config_dir) / f"site-{sid}.json")
                out[sid] = E2EChildProcessEndpoint(sid, path, log_path=wire_log)
            else:
                cfg = configs[sid]
                out[sid] = wire.InProcessEndpoint(sid, lambda cfg=cfg: E2ESiteProcess(cfg), log_path=wire_log)
    except BaseException:
        close_endpoints(out)
        raise
    return out


def close_endpoints(endpoints: Mapping[str, Any]) -> None:
    for endpoint in endpoints.values():
        endpoint.close()


# --------------------------------------------------------------------------------------------------- harness readers

def graph_node_ids(graph_path: str | Path, site_id: str) -> list[str]:
    """The MESSAGE node ids of the site's session, read ``mode=ro`` from the graph file (M9), sorted."""
    conn = sqlite3.connect(f"file:{Path(graph_path)}?mode=ro", uri=True)
    try:
        return [row[0] for row in conn.execute(_NODE_IDS, (SESSION_PREFIX + site_id, MESSAGE_LAYER)).fetchall()]
    finally:
        conn.close()


def graph_node_count(graph_path: str | Path, site_id: str) -> int:
    path = Path(graph_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            return int(conn.execute(_NODE_COUNT, (SESSION_PREFIX + site_id, MESSAGE_LAYER)).fetchone()[0])
        except sqlite3.OperationalError:
            return 0                                   # no nodes table yet
    finally:
        conn.close()


def graph_watermark(graph_path: str | Path, site_id: str) -> int | None:
    """The sync row's ``seq`` for the site's session, or None without one."""
    path = Path(graph_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            row = conn.execute(_READ_SYNC, (SESSION_PREFIX + site_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return int(row[0]) if row is not None else None
    finally:
        conn.close()


def node_ids_digest(ids: Sequence[str]) -> str:
    """sha256 over the sorted ids joined by newlines (M9's ``node_ids_sha256`` and ``record_refs_sha256``)."""
    return sha256_hex("\n".join(sorted(ids)))


def read_sync_log(edge_dir: str | Path, site_id: str) -> list[dict[str, Any]]:
    path = sync_log_path(edge_dir, site_id)
    if not path.exists():
        return []
    return [strict_load(line) for line in path.read_bytes().split(b"\n") if line]


# --------------------------------------------------------------------------------------------------- process mode

def serve(config: SiteConfig, stdin: Any, stdout: Any) -> int:
    process = E2ESiteProcess(config)
    try:
        for line in iter(stdin.readline, b""):
            stdout.write(wire.serve_line(process.handle, line))
            stdout.flush()
    finally:
        process.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m {MODULE}",
                                     description="One site of the end-to-end run, behind the wire (synthetic).")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    return serve(read_config(args.config), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main())
