"""Hash-chained memory bus: an append-only log of query executions.

The bus is the orchestrator's shared view of what the collective has already
reconstructed.  It is event-sourced: every ``TesseractCoordinator.execute``
result is appended as one ``BusEntry`` that links **vertically** to the entry
before it (``prev_hash``) and **horizontally** to earlier entries whose queries
were semantically similar (``peer_entry_ids``).  The vertical chain makes the
history tamper-evident; the horizontal links map similar queries onto the
peers that answered them, so the orchestrator can route a new query to the
nodes that produced selected evidence for one like it before.

What crosses onto the bus is deliberately narrow, and the privacy test pins
it: opaque claim ids, node ids, lineage roots and failure domains, the
outcome, and a **bottom-k sketch** of the query's tokens.  Neither the query
text nor any claim payload is stored.  Similarity is estimated from the
sketch alone (a bottom-k MinHash estimate of token Jaccard), which is what
lets two nodes agree on "similar" without either revealing its query.

No wall clock: entries are stamped by the caller's clock so a run replays
byte for byte.  ``contracts.py`` is not widened; the bus consumes
``QueryRequest`` and ``QueryExecution`` and adds nothing to them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import QueryRequest
from .core import QueryExecution, TesseractCoordinator, canonical_json, to_jsonable

GENESIS_HASH = "0" * 64
BUS_SCHEMA_VERSION = "1.0"
_TOKEN = re.compile(r"[a-z0-9]{3,}")


def sketch(content: str, size: int = 16) -> tuple[str, ...]:
    """Bottom-k sketch of a text: the ``size`` smallest token hashes.

    Tokens are lower-cased alphanumeric runs of three or more characters.  Only
    hashes leave this function, never tokens, so the sketch can sit on a shared
    bus without disclosing the query.
    """
    hashes = sorted({
        hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        for token in _TOKEN.findall(content.lower())
    })
    return tuple(hashes[:size])


def similarity(a: tuple[str, ...], b: tuple[str, ...], size: int = 16) -> float:
    """Bottom-k estimate of the Jaccard similarity of two sketched texts."""
    if not a or not b:
        return 0.0
    union = sorted(set(a) | set(b))[:size]
    shared = sum(1 for h in union if h in a and h in b)
    return round(shared / len(union), 6)


@dataclass(frozen=True)
class BusEntry:
    seq: int
    entry_id: str
    query_id: str
    phase: str
    query_sketch: tuple[str, ...]
    route: tuple[str, ...]
    producer_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    lineage_root_ids: tuple[str, ...]
    failure_domains: tuple[str, ...]
    success: bool
    confidence: float
    coverage: float
    recorded_at: str
    prev_hash: str
    peer_entry_ids: tuple[str, ...]
    peer_similarity: tuple[float, ...]
    hash: str = ""

    def payload(self) -> str:
        """Canonical bytes the hash covers: every field except ``hash``."""
        return canonical_json({f.name: getattr(self, f.name) for f in fields(self) if f.name != "hash"})

    def digest(self) -> str:
        return hashlib.sha256(self.payload().encode("utf-8")).hexdigest()


class MemoryBus:
    """Append-only, hash-chained, horizontally linked log of executions."""

    def __init__(
        self,
        clock: Callable[[], str],
        similarity_threshold: float = 0.5,
        max_peers: int = 5,
        sketch_size: int = 16,
    ) -> None:
        if not 0.0 < similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be in (0, 1]")
        self._clock = clock
        self._threshold = similarity_threshold
        self._max_peers = max_peers
        self._sketch_size = sketch_size
        self._entries: list[BusEntry] = []

    @property
    def entries(self) -> tuple[BusEntry, ...]:
        return tuple(self._entries)

    @property
    def head_hash(self) -> str:
        return self._entries[-1].hash if self._entries else GENESIS_HASH

    # ----- horizontal mapping -------------------------------------------
    def peers_for(self, request: QueryRequest) -> tuple[tuple[BusEntry, float], ...]:
        """Earlier entries whose query was similar, most similar first."""
        key = sketch(request.content, self._sketch_size)
        scored = [
            (entry, similarity(key, entry.query_sketch, self._sketch_size))
            for entry in self._entries
        ]
        peers = [(entry, score) for entry, score in scored if score >= self._threshold]
        peers.sort(key=lambda item: (-item[1], -item[0].seq))
        return tuple(peers[: self._max_peers])

    def route_hint(self, request: QueryRequest) -> tuple[str, ...]:
        """Nodes that produced *selected* evidence for similar, successful queries.

        Ordered by how often a node was selected across the peers, then by
        the order it was first seen.  Empty when nothing similar succeeded.
        The orchestrator passes this as ``preferred_node_ids``; the router
        still applies eligibility, failure masks and the node budget.
        """
        counts: dict[str, int] = {}
        order: list[str] = []
        for entry, _score in self.peers_for(request):
            if not entry.success:
                continue
            for node_id in entry.selected_node_ids:
                if node_id not in counts:
                    order.append(node_id)
                counts[node_id] = counts.get(node_id, 0) + 1
        return tuple(sorted(order, key=lambda node: (-counts[node], order.index(node))))

    def peer_graph(self) -> dict[str, tuple[str, ...]]:
        """entry_id -> the earlier entries it is horizontally linked to."""
        return {entry.entry_id: entry.peer_entry_ids for entry in self._entries}

    # ----- append -------------------------------------------------------
    def append(self, request: QueryRequest, execution: QueryExecution) -> BusEntry:
        peers = self.peers_for(request)
        seq = len(self._entries) + 1
        entry = BusEntry(
            seq=seq,
            entry_id=f"bus_{seq:06d}",
            query_id=request.query_id,
            phase=execution.phase,
            query_sketch=sketch(request.content, self._sketch_size),
            route=tuple(execution.route),
            producer_node_ids=tuple(sorted({claim.producer_node_id for claim in execution.claims})),
            selected_node_ids=tuple(execution.synthesis.selected_node_ids),
            claim_ids=tuple(claim.claim_id for claim in execution.claims),
            lineage_root_ids=tuple(sorted({root for claim in execution.claims for root in claim.lineage_root_ids})),
            failure_domains=tuple(sorted({domain for claim in execution.claims for domain in claim.failure_domains})),
            success=execution.synthesis.success,
            confidence=execution.synthesis.confidence,
            coverage=execution.metrics.valid_support_coverage,
            recorded_at=self._clock(),
            prev_hash=self.head_hash,
            peer_entry_ids=tuple(peer.entry_id for peer, _ in peers),
            peer_similarity=tuple(score for _, score in peers),
        )
        entry = replace(entry, hash=entry.digest())
        self._entries.append(entry)
        return entry

    # ----- integrity ----------------------------------------------------
    def verify(self) -> int:
        """Return 0 if the chain is intact, else the seq of the first bad entry."""
        prev = GENESIS_HASH
        for expected_seq, entry in enumerate(self._entries, 1):
            if entry.seq != expected_seq or entry.prev_hash != prev or entry.digest() != entry.hash:
                return entry.seq
            prev = entry.hash
        return 0

    # ----- persistence (event sourcing) ---------------------------------
    def dump(self, path: Path) -> None:
        lines = [json.dumps({"schema_version": BUS_SCHEMA_VERSION, "genesis": GENESIS_HASH})]
        lines.extend(canonical_json(entry) for entry in self._entries)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path, clock: Callable[[], str], **options: Any) -> "MemoryBus":
        bus = cls(clock, **options)
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        if not lines:
            return bus
        header = json.loads(lines[0])
        if header.get("schema_version") != BUS_SCHEMA_VERSION:
            raise ValueError("unsupported bus schema")
        for line in lines[1:]:
            bus._entries.append(_entry_from_json(json.loads(line)))
        bad = bus.verify()
        if bad:
            raise ValueError(f"bus chain broken at seq {bad}")
        return bus

    def to_jsonable(self) -> list[dict[str, Any]]:
        return [to_jsonable(entry) for entry in self._entries]


def _entry_from_json(data: dict[str, Any]) -> BusEntry:
    kwargs: dict[str, Any] = {}
    for f in fields(BusEntry):
        value = data[f.name]
        if isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return BusEntry(**kwargs)


async def execute_on_bus(
    coordinator: TesseractCoordinator,
    bus: MemoryBus,
    request: QueryRequest,
    phase: str,
) -> tuple[QueryExecution, BusEntry]:
    """Route with the bus's hint, execute, append.  The orchestrator's loop."""
    hint = bus.route_hint(request)
    execution = await coordinator.execute(request, phase, preferred_node_ids=hint or None)
    return execution, bus.append(request, execution)


def replay_hashes(entries: Iterable[BusEntry]) -> tuple[str, ...]:
    """Recompute every hash from payloads; equal to stored hashes iff intact."""
    return tuple(entry.digest() for entry in entries)
