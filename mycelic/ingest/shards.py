"""Per-holder domain sharding (docs/mycelic/INGESTION.md §7; product decision 1: "per user, split by domain when needed").

One store per holder. Shard ``s0`` is the holder's ``evidence.db``: the control shard (connectors, queue, locator,
tombstones, exports, shard map) and the default data shard. A domain subtree moves into its own file,
``<holder dir>/shd_<id>.db``, only when measured thresholds trip (§7.3) and an administrator or the owner approves the
split (:mod:`mycelic.ingest.reshard`). The logical hierarchy (tenant, holder, domain, source app, conversation, record)
stays logical: ids and indexes, never separate graphs.

This module holds:

* :class:`ShardSpec` and :class:`ShardRouter` (§7.2). Routing is sticky: a record with a ``record_locator`` row stays
  where it is (only a reshard moves it); a new record goes to the most specific *active* shard whose domain subtree
  covers its primary domain, otherwise to ``s0``.
* :class:`ShardSet`: the shards of one holder as seen by the process that writes them. It resolves shard files only from
  the holder's own ``shard_map`` and only inside the holder's directory (§7.10), opens one writer store per shard file
  (an exclusive ``flock`` on ``<file>.lock`` keeps a second process out, §7.6), serializes writes per shard with a
  re-entrant gate, gives each shard a read-only connection for fan-out retrieval, and replays the intent log of a data
  shard after a crash (:mod:`mycelic.ingest.intents`).
* :class:`ShardStats` collection, threshold signals sustained over a window, and :func:`recommend_splits` (§7.3, §7.9).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import socket
import sqlite3
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from NeuralGraph.chat_memory.retrieval import MemoryRetriever
from NeuralGraph.chat_memory.store import ChatMemoryStore

from ..util import j, jl, now_iso, parse_iso, plus_seconds
from .domains import UNCLASSIFIED, Taxonomy, is_personal

logger = logging.getLogger(__name__)

DEFAULT_SHARD = "s0"
SHARD_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SHARD_ID_RE = re.compile(r"^(s0|shd_[0-9a-f]{8,32})$")
QUERYABLE = ("active", "draining", "readonly")       # shards that hold live data for readers
OPENABLE = ("provisioning", "active", "draining", "readonly")
MAX_SHARDS_PER_HOLDER = 8                              # tenant quota default (§7.9 ingest_quota.max_shards_per_holder)
PER_SHARD_TIMEOUT = 1.5
LATENCY_WINDOW = 512


class ShardUnavailable(RuntimeError):
    pass


class ShardPathError(ShardUnavailable):
    """A shard file name that would resolve outside its holder's directory (tenant isolation, §7.10)."""


# ---------------------------------------------------------------------------------------------- spec
@dataclass(frozen=True)
class ShardSpec:
    shard_id: str
    ordinal: int
    file_name: str
    domain_ids: tuple[str, ...] = ()                  # () = default / catch-all shard
    time_from: str | None = None
    time_to: str | None = None
    status: str = "active"
    schema_version: int = 0
    last_backup_at: str | None = None

    @classmethod
    def from_row(cls, r: Mapping[str, Any]) -> "ShardSpec":
        keys = r.keys() if hasattr(r, "keys") else r
        return cls(shard_id=r["shard_id"], ordinal=int(r["ordinal"]), file_name=r["file_name"], domain_ids=tuple(jl(r["domain_ids"], []) or ()),
                   time_from=r["time_from"], time_to=r["time_to"], status=r["status"], schema_version=int(r["schema_version"] or 0),
                   last_backup_at=r["last_backup_at"] if "last_backup_at" in keys else None)

    def covers_time(self, created_at: str | None) -> bool:
        if not (self.time_from or self.time_to):
            return True
        if not created_at:
            return False
        return (not self.time_from or created_at >= self.time_from) and (not self.time_to or created_at < self.time_to)

    def time_specificity(self) -> int:
        return (1 if self.time_from else 0) + (1 if self.time_to else 0)

    def partition(self) -> dict[str, Any]:
        out: dict[str, Any] = {"domain_ids": list(self.domain_ids)} if self.domain_ids else {}
        if self.time_from or self.time_to:
            out.update(time_from=self.time_from, time_to=self.time_to)
        return out


def subtree(tax: Taxonomy, domain_ids: Iterable[str]) -> set[str]:
    """Every domain id in the subtrees rooted at ``domain_ids`` (aliases resolved; deprecated children included, since
    deprecated domains keep their members)."""
    out: set[str] = set()
    stack = [tax.resolve(d) for d in domain_ids if d]
    while stack:
        d = stack.pop()
        if d in out:
            continue
        out.add(d)
        stack.extend(c for c in tax._children.get(d, []))
    return out


def in_subtree(domain_id: str | None, roots: Iterable[str], tax: Taxonomy) -> bool:
    if not domain_id:
        return False
    rid = tax.resolve(domain_id)
    chain = {*tax.ancestors(rid), rid}
    return any(tax.resolve(r) in chain or rid.startswith(tax.resolve(r) + ".") for r in roots)


# ---------------------------------------------------------------------------------------------- per-shard write gate
class _Gate:
    """One writer at a time per shard inside this process. Re-entrant per asyncio task, because the pipeline holds the gate
    around ``EvidenceStore`` calls that take it themselves."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    async def __aenter__(self) -> "_Gate":
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner, self._depth = task, 1
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()

    @property
    def locked(self) -> bool:
        return self._lock.locked()


class _NoGate:
    async def __aenter__(self) -> "_NoGate":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


# ---------------------------------------------------------------------------------------------- one writer process per file
_FLOCKS: dict[str, list[int]] = {}
_FLOCKS_GUARD = threading.Lock()


def _acquire_file_lock(path: Path) -> str:
    """Exclusive ``flock`` on ``<file>.lock`` (§7.6). Re-entrant inside this process; another process gets
    :class:`ShardUnavailable`."""
    key = str(path) + ".lock"
    with _FLOCKS_GUARD:
        ent = _FLOCKS.get(key)
        if ent is not None:
            ent[1] += 1
            return key
        try:
            import fcntl
        except ImportError:               # not POSIX: in-process serialization only
            _FLOCKS[key] = [-1, 1]
            return key
        fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise ShardUnavailable(f"another process is the writer of {path.name}") from None
        _FLOCKS[key] = [fd, 1]
        return key


def _release_file_lock(key: str) -> None:
    with _FLOCKS_GUARD:
        ent = _FLOCKS.get(key)
        if ent is None:
            return
        ent[1] -= 1
        if ent[1] <= 0:
            _FLOCKS.pop(key, None)
            if ent[0] >= 0:
                with contextlib.suppress(OSError):
                    os.close(ent[0])


def create_shard_file(path: Path) -> None:
    """A new shard file: ``auto_vacuum=INCREMENTAL`` and ``secure_delete`` set before its first table (§5.1); the holder
    schema and migrations follow when the store opens it."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("CREATE TABLE IF NOT EXISTS holder_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------- readers (fan-out)
class _ReaderStore(ChatMemoryStore):
    """A query-only connection on a shard file, used from one worker thread at a time. Reads see committed data (WAL), and
    the retrieval cache follows other connections' commits through ``PRAGMA data_version``."""

    def __init__(self, path: str | Path) -> None:      # noqa: super().__init__ would create the schema
        self.db_path = str(path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None, timeout=5)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA query_only=ON")
        self._lock = asyncio.Lock()
        self.revision = 0
        self._bm25_cache = None
        self.has_fts = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name='memories_fts'").fetchone() is not None

    def _init_schema(self) -> None:                     # pragma: no cover - never called
        raise RuntimeError("reader stores never create a schema")


class ShardReader:
    """Read-only retrieval over one shard, run in a worker thread so a per-shard timeout really bounds the wait."""

    def __init__(self, shard_id: str, path: Path, llm: Any, config: Any) -> None:
        self.shard_id = shard_id
        self.store = _ReaderStore(path)
        self.retriever = MemoryRetriever(self.store, llm, config)
        self._mutex = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stale = False

    def search_sync(self, query: str, *, wait: float, **kw: Any) -> list[Any]:
        if not self._mutex.acquire(timeout=max(0.0, wait)):
            raise TimeoutError(f"shard {self.shard_id} is busy")
        try:
            if self._stale:
                self._stale = False
                self.retriever._index = None
                self.retriever.invalidate()
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(self.retriever.search(query, touch=False, **kw))
        finally:
            self._mutex.release()

    def invalidate(self) -> None:
        """Rebuild the index before the next search (never waits for a search in progress)."""
        self._stale = True

    def matrix_bytes(self) -> int:
        idx = self.retriever._index
        if idx is None or getattr(idx, "_dirty", True) or not hasattr(idx, "mats"):
            return 0
        return int(sum(M.nbytes for _ids, M in idx.mats.values()))

    def close(self) -> None:
        got = self._mutex.acquire(timeout=2.0)          # a search stuck past its timeout does not keep the holder from closing
        try:
            with contextlib.suppress(Exception):
                self.store._conn.close()
            if got and self._loop is not None:
                with contextlib.suppress(Exception):
                    self._loop.close()
                self._loop = None
        finally:
            if got:
                self._mutex.release()


# ---------------------------------------------------------------------------------------------- thresholds and stats
@dataclass
class SplitThresholds:
    """§7.3 signals (initial values; validated by research/ingest_bench). Configurable so tests can force a split."""
    matrix_bytes: int = 1 << 30                 # NeuralGraph's in-RAM vector matrix of the shard
    active_memories: int = 400_000
    file_bytes: int = 8 << 30
    write_p95_ms: float = 250.0                 # ...while the live queue lags more than live_lag_seconds
    live_lag_seconds: float = 600.0
    query_p95_ms: float = 800.0
    sustain_seconds: float = 24 * 3600.0        # a signal must hold this long before a split is recommended
    max_shards: int = MAX_SHARDS_PER_HOLDER

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any] | None) -> "SplitThresholds":
        base = cls()
        for k, v in (m or {}).items():
            if k in base.__dataclass_fields__ and isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(base, k, type(getattr(base, k))(v))
        return base


@dataclass
class ShardStats:
    shard_id: str
    records: int = 0
    documents: int = 0
    active_memories: int = 0
    superseded_memories: int = 0
    embedding_dims: list[int] = field(default_factory=list)
    matrix_bytes: int = 0
    file_bytes: int = 0
    wal_bytes: int = 0
    fts_rows: int = 0
    write_p95_ms: float | None = None
    query_p95_ms: float | None = None
    writes_sampled: int = 0
    queries_sampled: int = 0
    live_lag_seconds: float = 0.0
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def signals_for(st: ShardStats, th: SplitThresholds) -> dict[str, float]:
    """The §7.3 signals this sample trips (value of each)."""
    out: dict[str, float] = {}
    if st.matrix_bytes > th.matrix_bytes:
        out["matrix_bytes"] = float(st.matrix_bytes)
    if st.active_memories > th.active_memories:
        out["active_memories"] = float(st.active_memories)
    if st.file_bytes > th.file_bytes:
        out["file_bytes"] = float(st.file_bytes)
    if st.write_p95_ms is not None and st.write_p95_ms > th.write_p95_ms and st.live_lag_seconds > th.live_lag_seconds:
        out["write_p95"] = float(st.write_p95_ms)
    if st.query_p95_ms is not None and st.query_p95_ms > th.query_p95_ms:
        out["query_p95"] = float(st.query_p95_ms)
    return out


def _p95(samples: Sequence[float]) -> float | None:
    if not samples:
        return None
    xs = sorted(samples)
    return round(xs[min(len(xs) - 1, int(0.95 * (len(xs) - 1) + 0.5))], 3)


def domain_load_sync(c: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Per primary domain in one shard: live records, their active/superseded memories and the vector bytes they hold."""
    rows = c.execute("""SELECT r.primary_domain_id AS d, COUNT(DISTINCT r.record_id) AS records, COUNT(m.memory_id) AS memories,
                               COALESCE(SUM(CASE WHEN m.embedding IS NOT NULL THEN m.embedding_dim ELSE 0 END), 0) * 4 AS matrix_bytes
                        FROM ingest_records r LEFT JOIN memories m ON m.chat_id = r.record_id AND m.status IN ('active', 'superseded')
                        WHERE r.deletion_status IN ('live', 'redacted') AND r.primary_domain_id IS NOT NULL
                        GROUP BY r.primary_domain_id""").fetchall()
    return {r["d"]: {"records": int(r["records"]), "memories": int(r["memories"]), "matrix_bytes": int(r["matrix_bytes"])} for r in rows}


_SIGNAL_METRIC = {"matrix_bytes": "matrix_bytes", "active_memories": "memories", "file_bytes": "memories", "query_p95": "memories",
                  "write_p95": "records"}


def _subtree_key(domain_id: str, roots: Sequence[str], tax: Taxonomy) -> str | None:
    """The movable subtree a record's primary domain belongs to: its top-level domain in a catch-all shard, the child of the
    partition root in a domain shard (the root itself is the whole shard and cannot be split further by domain)."""
    rid = tax.resolve(domain_id)
    chain = [*tax.ancestors(rid), rid]
    if not roots:
        return chain[0] if chain else None
    for r in roots:
        rr = tax.resolve(r)
        if rr in chain:
            i = chain.index(rr)
            return chain[i + 1] if i + 1 < len(chain) else None
    return None


def recommend_splits(stats: Mapping[str, ShardStats], sustained: Mapping[str, Mapping[str, float]], loads: Mapping[str, Mapping[str, Mapping[str, int]]],
                     specs: Mapping[str, ShardSpec], tax: Taxonomy, *, thresholds: SplitThresholds | None = None) -> list[dict[str, Any]]:
    """For every shard with a sustained signal, the domain subtree whose move relieves it most (§7.3): the candidate with
    the largest share of the load the tripped signal measures (vector bytes, memories, or records). Subtrees already split
    out (another shard's partition) and ``unclassified`` are never candidates. When a single subtree is the whole load, a
    domain split cannot help and the recommendation says a time-range split is needed instead."""
    th = thresholds or SplitThresholds()
    taken = {tax.resolve(d) for s in specs.values() if s.status in OPENABLE for d in s.domain_ids}
    out: list[dict[str, Any]] = []
    queryable = [s for s in specs.values() if s.status in OPENABLE]
    for sid, signals in sustained.items():
        if not signals or sid not in specs:
            continue
        spec = specs[sid]
        metric = next((_SIGNAL_METRIC[s] for s in ("matrix_bytes", "active_memories", "query_p95", "file_bytes", "write_p95") if s in signals), "memories")
        cand: dict[str, dict[str, int]] = {}
        for d, load in (loads.get(sid) or {}).items():
            key = _subtree_key(d, spec.domain_ids, tax)
            if key is None or key == UNCLASSIFIED or key in taken:
                continue
            agg = cand.setdefault(key, {"records": 0, "memories": 0, "matrix_bytes": 0})
            for k in agg:
                agg[k] += int(load.get(k, 0))
        rec: dict[str, Any] = {"shard_id": sid, "signals": dict(signals), "metric": metric}
        if len(queryable) >= th.max_shards:
            out.append({**rec, "action": "quota_reached", "domain_ids": [], "moves": {}})
            continue
        if not cand:
            out.append({**rec, "action": "none_movable", "domain_ids": [], "moves": {}})
            continue
        best, load = max(cand.items(), key=lambda kv: (kv[1][metric], kv[1]["memories"], kv[0]))
        total = sum(v[metric] for v in cand.values()) or 1
        share = round(load[metric] / total, 4)
        action = "split" if (share < 0.95 or len(cand) > 1) and load[metric] > 0 else "split_by_time"
        out.append({**rec, "action": action, "domain_ids": [best], "moves": {**load, "share": share}})
    return out


# ---------------------------------------------------------------------------------------------- the router
class ShardRouter:
    """Write and read routing for one holder (§7.2). ``shards`` is the holder's :class:`ShardSet` (or, for callers that only
    have a store, a ``{shard_id: store}`` mapping with at least ``s0``)."""

    def __init__(self, shards: "ShardSet | Mapping[str, Any]", *, taxonomy: Taxonomy) -> None:
        if isinstance(shards, ShardSet):
            self.shardset: ShardSet | None = shards
            self.stores: dict[str, Any] = {}
        else:
            if DEFAULT_SHARD not in shards:
                raise ValueError("the control shard s0 must be open")
            self.shardset = None
            self.stores = dict(shards)
        self.taxonomy = taxonomy

    @property
    def control(self) -> Any:
        """The control shard: connectors, queue, locator, tombstones live here."""
        return self.shardset.control if self.shardset is not None else self.stores[DEFAULT_SHARD]

    def shards_sync(self, c: sqlite3.Connection) -> list[dict[str, Any]]:
        return [dict(r, domain_ids=jl(r["domain_ids"], [])) for r in
                c.execute("SELECT * FROM shard_map WHERE status IN ('active', 'readonly', 'draining') ORDER BY ordinal").fetchall()]

    def route_write_sync(self, c: sqlite3.Connection, record_key: str, *, primary_domain_id: str | None, created_at: str | None = None) -> str:
        """Existing record -> its ``record_locator`` shard (sticky; during a migration the source keeps the writes until
        cutover). New record -> the most specific *active* shard whose subtree covers the primary domain (deepest ancestor
        match, then the narrowest time range); else the catch-all shard ``s0``."""
        row = c.execute("SELECT shard_id FROM record_locator WHERE record_key=?", (record_key,)).fetchone()
        if row is not None:
            return row["shard_id"]                       # sticky
        best, best_score = DEFAULT_SHARD, (-1, 0)
        chain = [*self.taxonomy.ancestors(primary_domain_id), self.taxonomy.resolve(primary_domain_id)] if primary_domain_id else []
        for s in self.shards_sync(c):
            if s["status"] != "active":
                continue
            if not s["domain_ids"]:
                continue                                 # the catch-all shard is the default
            if not chain:
                continue
            spec = ShardSpec(shard_id=s["shard_id"], ordinal=int(s["ordinal"]), file_name=s["file_name"], domain_ids=tuple(s["domain_ids"]),
                             time_from=s["time_from"], time_to=s["time_to"], status=s["status"])
            if not spec.covers_time(created_at):
                continue
            for d in s["domain_ids"]:
                rd = self.taxonomy.resolve(d)
                if rd in chain and (chain.index(rd), spec.time_specificity()) > best_score:
                    best, best_score = s["shard_id"], (chain.index(rd), spec.time_specificity())
        return best

    def writer(self, shard_id: str) -> Any:
        if self.shardset is not None:
            return self.shardset.store(shard_id)
        store = self.stores.get(shard_id)
        if store is None:
            raise ShardUnavailable(f"shard {shard_id} is not open in this process")
        return store

    def gate(self, shard_id: str) -> Any:
        return self.shardset.gate(shard_id) if self.shardset is not None else _NoGate()

    def route_read_sync(self, c: sqlite3.Connection, domain_ids: list[str] | None = None) -> list[str]:
        """Shards to query (bounded fan-out). With one shard this is always ``[s0]``."""
        out = []
        for s in self.shards_sync(c):
            if domain_ids and s["domain_ids"]:
                chains = {a for d in domain_ids for a in [*self.taxonomy.ancestors(d), self.taxonomy.resolve(d)]}
                if not chains & set(s["domain_ids"]) and not any(self.taxonomy.resolve(d) in self.taxonomy.ancestors(x)
                                                                 for d in domain_ids for x in s["domain_ids"]):
                    continue
            out.append(s["shard_id"])
        if self.shardset is None:
            return [s for s in out if s in self.stores] or [DEFAULT_SHARD]
        return out or [DEFAULT_SHARD]

    def shards_for_query(self, *, domain_ids: Sequence[str] | None = None, since: str | None = None, until: str | None = None,
                         max_shards: int = MAX_SHARDS_PER_HOLDER) -> tuple[list[ShardSpec], bool]:
        """Queryable shards that can hold matches, ranked (default shard first, then by ordinal), cut to ``max_shards``.
        Returns ``(shards, truncated)``."""
        if self.shardset is None:
            return [ShardSpec(DEFAULT_SHARD, 0, "evidence.db")], False
        c = self.shardset.conn
        keep = set(self.route_read_sync(c, list(domain_ids) if domain_ids else None))
        specs = [s for s in self.shardset.queryable() if s.shard_id in keep]
        if since or until:
            specs = [s for s in specs if not (s.time_to and since and s.time_to <= since) and not (s.time_from and until and s.time_from > until)]
        specs.sort(key=lambda s: (0 if not s.domain_ids else 1, s.ordinal))
        return specs[:max(1, int(max_shards))], len(specs) > max(1, int(max_shards))


# ---------------------------------------------------------------------------------------------- the shard set
class ShardSet:
    """The shards of one holder in the process that writes them. ``control`` is the holder's s0 ``EvidenceStore``."""

    stats_interval = 600.0                   # §7.3: ShardStats every 10 minutes (heartbeats reuse the last sample)

    def __init__(self, control: Any) -> None:
        self.control = control
        self.holder_id = control.holder_id
        self.tenant_id = control.tenant_id
        path = str(control.path)
        self.base_dir: Path | None = None if path == ":memory:" else Path(path).expanduser().resolve().parent
        self._stores: dict[str, Any] = {DEFAULT_SHARD: control}
        self._file_locks: dict[str, str] = {}
        self._readers: dict[str, ShardReader] = {}
        self._gates: dict[str, _Gate] = defaultdict(_Gate)
        self.writer_id = f"{socket.gethostname()}:{os.getpid()}"
        self._latency: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=LATENCY_WINDOW))
        self.thresholds = SplitThresholds()
        self._stats_at = 0.0
        self.tasks: set[asyncio.Task] = set()
        self._read_executor: ThreadPoolExecutor | None = None
        self._closed = False

    # ------------------------------------------------------------------ the map
    @property
    def conn(self) -> sqlite3.Connection:
        return self.control.store._conn

    def specs(self, *, statuses: Iterable[str] | None = None) -> list[ShardSpec]:
        rows = self.conn.execute("SELECT * FROM shard_map ORDER BY ordinal").fetchall()
        want = set(statuses) if statuses is not None else None
        return [ShardSpec.from_row(r) for r in rows if want is None or r["status"] in want]

    def spec(self, shard_id: str) -> ShardSpec | None:
        r = self.conn.execute("SELECT * FROM shard_map WHERE shard_id=?", (shard_id,)).fetchone()
        return ShardSpec.from_row(r) if r is not None else None

    def has_data_shards(self) -> bool:
        """True once the holder has (or is building) a shard besides s0: write paths then take the per-shard gates and
        resolve each record's shard."""
        if self._closed:
            return False
        return self.conn.execute("SELECT 1 FROM shard_map WHERE shard_id<>? AND status<>'retired' LIMIT 1", (DEFAULT_SHARD,)).fetchone() is not None

    def queryable(self) -> list[ShardSpec]:
        return self.specs(statuses=QUERYABLE)

    def multi(self) -> bool:
        """More than one shard holds live data: reads fan out. With one shard every read takes today's single-store path."""
        if self._closed:
            return False
        n = self.conn.execute("SELECT COUNT(*) FROM shard_map WHERE status IN ('active', 'draining', 'readonly')").fetchone()[0]
        return int(n) > 1

    # ------------------------------------------------------------------ files (tenant isolation, §7.10)
    def path_for(self, file_name: str) -> Path:
        """A shard file of THIS holder: a plain file name from its own shard map, inside its own directory. Anything else
        (a path, ``..``, another holder's directory) is refused before a file is touched."""
        if self.base_dir is None:
            raise ShardUnavailable("an in-memory holder store cannot have data shards")
        name = str(file_name or "")
        if not SHARD_FILE_RE.match(name) or name in (".", "..") or name.startswith("."):
            raise ShardPathError(f"invalid shard file name {name!r}")
        p = (self.base_dir / name).resolve()
        if p.parent != self.base_dir:
            raise ShardPathError(f"shard file {name!r} resolves outside the holder directory")
        return p

    def store(self, shard_id: str) -> Any:
        """The writer store of one shard (opened on first use)."""
        if shard_id in self._stores:
            return self._stores[shard_id]
        spec = self.spec(shard_id)
        if spec is None or spec.status not in OPENABLE:
            raise ShardUnavailable(f"shard {shard_id} is not open-able ({spec.status if spec else 'unknown'})")
        return self._open(spec)

    def _open(self, spec: ShardSpec, *, create: bool = False) -> Any:
        if not SHARD_ID_RE.match(spec.shard_id) or spec.shard_id == DEFAULT_SHARD:
            raise ShardPathError(f"invalid data shard id {spec.shard_id!r}")
        path = self.path_for(spec.file_name)
        if not path.exists():
            if not create:
                raise ShardUnavailable(f"shard {spec.shard_id} has no file")
            create_shard_file(path)
        key = _acquire_file_lock(path)
        ctl = self.control
        try:
            store = type(ctl)(path, holder_id=ctl.holder_id, tenant_id=ctl.tenant_id, llm=ctl.llm, router=ctl.router,
                              export_policy=dict(ctl.export_policy), domains=list(ctl.domains), extract=ctl.extract,
                              retrieval=ctl.retriever.config, chunk_chars=ctl.chunk_chars, owner_ids=list(ctl.owner_ids))
        except Exception:
            _release_file_lock(key)
            raise
        store.store.control_store = ctl.store
        store.store.shard_id = spec.shard_id
        store.shard_id = spec.shard_id
        store._shard_set = self
        store._taxonomy = ctl._taxonomy
        self._stores[spec.shard_id] = store
        self._file_locks[spec.shard_id] = key
        return store

    def open_new(self, spec: ShardSpec) -> Any:
        """Provisioning: create the file (pragmas first), run the holder schema and migrations, open it for writing."""
        if spec.shard_id in self._stores:
            return self._stores[spec.shard_id]
        return self._open(spec, create=True)

    def open_stores(self, *, statuses: Iterable[str] = OPENABLE) -> list[tuple[ShardSpec, Any]]:
        out = []
        for spec in self.specs(statuses=statuses):
            if spec.shard_id == DEFAULT_SHARD:
                out.append((spec, self.control))
                continue
            try:
                if spec.status == "provisioning" and spec.shard_id not in self._stores and not self.path_for(spec.file_name).exists():
                    continue                      # planned, file not created yet: nothing in it, nothing owed
                out.append((spec, self.store(spec.shard_id)))
            except ShardUnavailable as exc:
                logger.warning("holder %s shard %s unavailable: %s", self.holder_id, spec.shard_id, exc)
        return out

    async def close_shard(self, shard_id: str) -> None:
        reader = self._readers.pop(shard_id, None)
        if reader is not None:
            await asyncio.to_thread(reader.close)
        if shard_id == DEFAULT_SHARD:
            return
        store = self._stores.pop(shard_id, None)
        if store is not None:
            await store.close()
        key = self._file_locks.pop(shard_id, None)
        if key is not None:
            _release_file_lock(key)

    async def close(self) -> None:
        for t in list(self.tasks):
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        for sid in list(self._readers):
            await self.close_shard(sid)
        for sid in [s for s in list(self._stores) if s != DEFAULT_SHARD]:
            await self.close_shard(sid)
        if self._read_executor is not None:
            self._read_executor.shutdown(wait=False, cancel_futures=True)
            self._read_executor = None
        self._closed = True

    # ------------------------------------------------------------------ gates, readers, caches
    def gate(self, shard_id: str) -> _Gate:
        return self._gates[shard_id]

    def reader(self, shard_id: str) -> ShardReader:
        r = self._readers.get(shard_id)
        if r is None:
            store = self.control if shard_id == DEFAULT_SHARD else self.store(shard_id)
            r = ShardReader(shard_id, Path(store.path), self.control.llm, self.control.retriever.config)
            self._readers[shard_id] = r
        return r

    def read_executor(self) -> ThreadPoolExecutor:
        """The holder's fan-out read worker: one thread, shards searched in turn (no GIL convoy between shard searches)."""
        if self._read_executor is None:
            self._read_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"shard-read-{self.holder_id}")
        return self._read_executor

    def replace_read_executor(self) -> None:
        """A shard overran its timeout on the worker: leave that thread to finish in the background, read on a fresh one."""
        old, self._read_executor = self._read_executor, None
        if old is not None:
            old.shutdown(wait=False, cancel_futures=True)

    def invalidate(self, shard_ids: Iterable[str] | None = None) -> None:
        """Drop retrieval caches after a reshard: the incremental index never sees hard-deleted rows (G8)."""
        ids = list(shard_ids) if shard_ids is not None else list(self._stores)
        for sid in ids:
            store = self._stores.get(sid)
            if store is not None:
                store.retriever._index = None
                store.retriever.invalidate()
            reader = self._readers.get(sid)
            if reader is not None:
                reader.invalidate()

    async def recover(self) -> int:
        """Apply what data shards still owe s0 (a crash between a shard commit and its control commit)."""
        if not self.has_data_shards():
            return 0
        n = 0
        for spec, store in self.open_stores():
            if spec.shard_id != DEFAULT_SHARD:
                n += await store.store.apply_pending_intents()
        return n

    # ------------------------------------------------------------------ records
    def shard_of_record(self, record_id: str) -> str:
        r = self.conn.execute("SELECT shard_id FROM record_locator WHERE record_id=?", (record_id,)).fetchone()
        return r["shard_id"] if r is not None else DEFAULT_SHARD

    def store_of_record(self, record_id: str) -> Any:
        sid = self.shard_of_record(record_id)
        if sid == DEFAULT_SHARD:
            return self.control
        try:
            return self.store(sid)
        except ShardUnavailable:
            return self.control

    # ------------------------------------------------------------------ latency (§7.9)
    def observe(self, shard_id: str, kind: str, ms: float) -> None:
        self._latency[(shard_id, kind)].append(float(ms))

    def p95(self, shard_id: str, kind: str) -> tuple[float | None, int]:
        xs = list(self._latency.get((shard_id, kind)) or ())
        return _p95(xs), len(xs)

    # ------------------------------------------------------------------ stats, signals, recommendations (§7.3, §7.9)
    def live_lag_seconds(self) -> float:
        r = self.conn.execute("SELECT MIN(created_at) FROM ingest_queue WHERE status IN ('queued', 'leased') AND priority_class='live'").fetchone()
        t = parse_iso(r[0]) if r and r[0] else None
        if t is None:
            return 0.0
        return max(0.0, time.time() - t.timestamp())

    def shard_stats(self, spec: ShardSpec, store: Any) -> ShardStats:
        c = store.store._conn
        mem = {r["status"]: int(r["n"]) for r in c.execute("SELECT status, COUNT(*) AS n FROM memories GROUP BY status")}
        dims = sorted(int(r[0]) for r in c.execute("SELECT DISTINCT embedding_dim FROM memories WHERE status IN ('active', 'superseded') "
                                                  "AND embedding IS NOT NULL AND embedding_dim IS NOT NULL"))
        matrix = int(c.execute("SELECT COALESCE(SUM(embedding_dim), 0) * 4 FROM memories WHERE status IN ('active', 'superseded') "
                               "AND embedding IS NOT NULL").fetchone()[0])
        records = int(c.execute("SELECT COUNT(*) FROM ingest_records WHERE deletion_status IN ('live', 'redacted')").fetchone()[0])
        docs = int(c.execute("SELECT COUNT(*) FROM documents WHERE status IN ('active', 'revised')").fetchone()[0])
        path = Path(store.path)
        size = path.stat().st_size if path.exists() else 0
        wal = Path(str(path) + "-wal")
        wp, wn = self.p95(spec.shard_id, "write")
        qp, qn = self.p95(spec.shard_id, "query")
        return ShardStats(shard_id=spec.shard_id, records=records, documents=docs, active_memories=mem.get("active", 0),
                          superseded_memories=mem.get("superseded", 0), embedding_dims=dims, matrix_bytes=matrix, file_bytes=size,
                          wal_bytes=wal.stat().st_size if wal.exists() else 0, fts_rows=sum(mem.values()), write_p95_ms=wp, query_p95_ms=qp,
                          writes_sampled=wn, queries_sampled=qn, live_lag_seconds=round(self.live_lag_seconds(), 1), at=now_iso())

    async def collect(self, *, now: float | None = None, force: bool = False) -> dict[str, ShardStats]:
        """Sample every shard, persist the stats in ``shard_map`` and track which signals hold (``shard_signals``)."""
        t = time.time() if now is None else float(now)
        if not force and self._stats_at and t - self._stats_at < self.stats_interval:
            return {}
        self._stats_at = t
        out: dict[str, ShardStats] = {}
        at = _iso_ts(t)
        for spec, store in self.open_stores(statuses=QUERYABLE):
            st = self.shard_stats(spec, store)
            out[spec.shard_id] = st
            tripped = signals_for(st, self.thresholds)

            def fn(c, spec=spec, st=st, tripped=tripped):
                c.execute("UPDATE shard_map SET stats=?, stats_at=? WHERE shard_id=?", (j(st.to_dict()), at, spec.shard_id))
                if tripped:
                    c.execute(f"DELETE FROM shard_signals WHERE shard_id=? AND signal NOT IN ({','.join('?' * len(tripped))})", (spec.shard_id, *tripped))
                else:
                    c.execute("DELETE FROM shard_signals WHERE shard_id=?", (spec.shard_id,))
                for sig, val in tripped.items():
                    c.execute("""INSERT INTO shard_signals(shard_id, signal, first_seen_at, last_seen_at, value) VALUES (?, ?, ?, ?, ?)
                                 ON CONFLICT(shard_id, signal) DO UPDATE SET last_seen_at=excluded.last_seen_at, value=excluded.value""",
                              (spec.shard_id, sig, at, at, val))
            await self.control.store.run_in_tx(fn)
        return out

    def signals(self, *, now: float | None = None, sustained_only: bool = True) -> dict[str, dict[str, float]]:
        t = time.time() if now is None else float(now)
        out: dict[str, dict[str, float]] = {}
        for r in self.conn.execute("SELECT * FROM shard_signals").fetchall():
            first = parse_iso(r["first_seen_at"])
            held = t - first.timestamp() if first else 0.0
            if not sustained_only or held >= self.thresholds.sustain_seconds:
                out.setdefault(r["shard_id"], {})[r["signal"]] = float(r["value"])
        return out

    def recommendations(self, *, now: float | None = None) -> list[dict[str, Any]]:
        sustained = self.signals(now=now)
        if not sustained:
            return []
        specs = {s.shard_id: s for s in self.specs()}
        fields = set(ShardStats.__dataclass_fields__) - {"shard_id"}
        stats = {r["shard_id"]: ShardStats(shard_id=r["shard_id"], **{k: v for k, v in (jl(r["stats"], {}) or {}).items() if k in fields})
                 for r in self.conn.execute("SELECT shard_id, stats FROM shard_map").fetchall() if r["shard_id"] in sustained}
        loads = {}
        for sid in sustained:
            try:
                loads[sid] = domain_load_sync((self.control if sid == DEFAULT_SHARD else self.store(sid)).store._conn)
            except ShardUnavailable:
                loads[sid] = {}
        return recommend_splits(stats, sustained, loads, specs, self.control.taxonomy(), thresholds=self.thresholds)

    def health(self, shard_id: str) -> str:
        r = self.conn.execute("SELECT 1 FROM shard_signals WHERE shard_id=? LIMIT 1", (shard_id,)).fetchone()
        return "hot" if r is not None else "ok"

    async def report(self, *, collect: bool = True, force: bool = False) -> dict[str, Any]:
        """What the coordinator registry mirrors (heartbeat): counts, bytes and latencies, tenant domain ids only. Stats are
        sampled at most every ``stats_interval`` seconds unless ``force``."""
        if collect:
            try:
                await self.collect(force=force)
            except Exception:
                logger.exception("shard stats collection failed for holder %s", self.holder_id)
        lease = plus_seconds(30)
        items = []
        for r in self.conn.execute("SELECT * FROM shard_map ORDER BY ordinal").fetchall():
            spec = ShardSpec.from_row(r)
            open_here = spec.shard_id == DEFAULT_SHARD or spec.shard_id in self._stores
            items.append({"shard_id": spec.shard_id, "ordinal": spec.ordinal, "file_name": spec.file_name if spec.shard_id != DEFAULT_SHARD else "evidence.db",
                          "partition": _public_partition(spec), "status": spec.status, "schema_version": spec.schema_version,
                          "stats": _numbers_only(jl(r["stats"], {})), "stats_at": r["stats_at"], "health": self.health(spec.shard_id),
                          "last_backup_at": r["last_backup_at"], "writer_id": self.writer_id if open_here else None,
                          "writer_lease_until": lease if open_here else None})
        from .reshard import migration_rows
        migrations = [{**m, "partition": {"domain_ids": [d for d in (m.get("partition") or {}).get("domain_ids", []) if not is_personal(d)]}}
                      for m in migration_rows(self.conn, limit=20)]
        recs = []
        for rec in self.recommendations():
            ids = [d for d in rec.get("domain_ids") or [] if not is_personal(d)]
            if rec.get("domain_ids") and not ids:
                continue                       # a personal domain never leaves the holder, not even as a recommendation
            recs.append({**rec, "domain_ids": ids})
        return {"items": items, "migrations": migrations, "recommendations": recs}

    # ------------------------------------------------------------------ migrations
    async def start_split(self, domain_ids: Sequence[str], *, requested_by: str, wait: bool = True, **kw: Any) -> dict[str, Any]:
        """Plan a split of ``domain_ids`` out of ``s0`` and run it (in the background unless ``wait``)."""
        from .reshard import SplitMigration
        mig = await SplitMigration.plan(self, domain_ids, requested_by=requested_by, from_shard=kw.pop("from_shard", DEFAULT_SHARD))
        if wait:
            return await mig.run(**kw)
        task = asyncio.create_task(self._run_quietly(mig, **kw), name=f"split-{mig.migration_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return mig.view()

    async def _run_quietly(self, mig: Any, **kw: Any) -> None:
        try:
            await mig.run(**kw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("split %s of holder %s stopped; it resumes from its checkpoint", mig.migration_id, self.holder_id)

    def migration(self, migration_id: str) -> dict[str, Any] | None:
        from .reshard import migration_view
        return migration_view(self.conn, migration_id)

    async def resume_migrations(self, **kw: Any) -> list[dict[str, Any]]:
        from .reshard import SplitMigration, unfinished
        out = []
        for mid in unfinished(self.conn):
            out.append(await SplitMigration.load(self, mid).run(**kw))
        return out

    async def start(self) -> None:
        """When the holder's writer starts: apply what data shards still owe s0, and resume an interrupted split (in the
        background, from its checkpoint)."""
        await self.recover()
        from .reshard import SplitMigration, unfinished
        for mid in unfinished(self.conn):
            task = asyncio.create_task(self._run_quietly(SplitMigration.load(self, mid)), name=f"split-{mid}")
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)


def _iso_ts(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="seconds")


def _public_partition(spec: ShardSpec) -> dict[str, Any]:
    p = spec.partition()
    if "domain_ids" in p:
        ids = [d for d in p["domain_ids"] if not is_personal(d)]
        p["domain_ids"] = ids + (["personal"] if len(ids) != len(spec.domain_ids) else [])
    return p


def _numbers_only(d: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
            out[k] = list(v)
        elif k == "at" and isinstance(v, str):
            out[k] = v
    return out


__all__ = ["DEFAULT_SHARD", "ShardSpec", "ShardRouter", "ShardSet", "ShardReader", "ShardStats", "SplitThresholds", "ShardUnavailable",
           "ShardPathError", "recommend_splits", "signals_for", "domain_load_sync", "subtree", "in_subtree", "create_shard_file"]
