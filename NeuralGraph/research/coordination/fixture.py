"""Seeded opaque four-node fixture for collective capability experiments."""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

from .adapters import MockMemoryNodeAdapter, PrivateMemoryRecord
from .contracts import AuthorizationContext, LearningDisposition, QueryBudget, QueryRequest
from .core import CapabilityRegistry, FailureInjector, LogicalClock, TraceLogger


FIXTURE_VERSION = "collective-capability-v1"
CAPABILITY_ID = "opaque_pair_reconstruction"
REQUIRED_SCOPE = "capability:opaque-pair"
REQUIRED_SLOTS = ("left", "right")


@dataclass(frozen=True)
class OpaqueFixture:
    seed: int
    run_id: str
    query: QueryRequest
    expected_answer: str
    left_symbol: str
    right_symbol: str
    distractor_symbols: tuple[str, ...]
    records_by_node: dict[str, tuple[PrivateMemoryRecord, ...]]


@dataclass(frozen=True)
class FixtureRuntime:
    fixture: OpaqueFixture
    clock: LogicalClock
    traces: TraceLogger
    failure: FailureInjector
    registry: CapabilityRegistry


def _opaque(rng: random.Random, prefix: str) -> str:
    body = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(14))
    return f"{prefix}_{body}"


def generate_fixture(seed: int, strategy: str) -> OpaqueFixture:
    rng = random.Random(seed)
    left = _opaque(rng, "LX")
    right = _opaque(rng, "RX")
    distractors = tuple(_opaque(rng, "DX") for _ in range(4))
    query_nonce = _opaque(rng, "QX")
    run_id = f"run_{seed}_{strategy}"
    issued_at = (LogicalClock(seed).now())
    query = QueryRequest(
        query_id=f"query_{seed}",
        content=f"Reconstruct the two-part capability identified by {query_nonce}",
        requested_capability=CAPABILITY_ID,
        requester_id="experiment-controller",
        issued_at=issued_at,
        valid_at=issued_at,
        authorization=AuthorizationContext(
            scopes=(REQUIRED_SCOPE,),
            allowed_node_ids=("node-a", "node-b", "node-c", "node-d"),
        ),
        budget=QueryBudget(max_nodes=3, max_claims=8, max_verifications=2),
    )

    def record(
        node: str,
        slot: str,
        value: str,
        memory: str,
        source: str,
        root: str,
        domain: str,
        confidence: float,
    ) -> PrivateMemoryRecord:
        return PrivateMemoryRecord(
            memory_id=memory,
            capability_id=CAPABILITY_ID,
            slot=slot,
            value=value,
            confidence=confidence,
            source_ids=(source,),
            parent_memory_ids=(f"parent-{memory}",),
            lineage_root_ids=(root,),
            failure_domains=(domain,),
            edge_path=(f"edge-{node}-{slot}",),
            required_scope=REQUIRED_SCOPE,
        )

    records = {
        "node-a": (record("a", "left", left, "memory-a", "source-1", "R1", "FD1", 0.96),),
        "node-b": (record("b", "right", right, "memory-b", "source-2", "R2", "FD2", 0.93),),
        # C is an apparent replica, not an independent reconstruction path.
        "node-c": (record("c", "right", right, "memory-c", "source-2-copy", "R2", "FD2", 0.91),),
        # D has equivalent support through independent source/root/domain R3/FD3.
        "node-d": (record("d", "right", right, "memory-d", "source-3", "R3", "FD3", 0.94),),
    }
    return OpaqueFixture(
        seed=seed,
        run_id=run_id,
        query=query,
        expected_answer=f"{left}:{right}",
        left_symbol=left,
        right_symbol=right,
        distractor_symbols=distractors,
        records_by_node=records,
    )


def build_runtime(seed: int, strategy: str) -> FixtureRuntime:
    fixture = generate_fixture(seed, strategy)
    clock = LogicalClock(seed)
    traces = TraceLogger(fixture.run_id, clock)
    failure = FailureInjector(fixture.run_id, seed, clock, traces)
    registry = CapabilityRegistry()
    for node_id in ("node-a", "node-b", "node-c", "node-d"):
        registry.register(MockMemoryNodeAdapter(
            node_id=node_id,
            records=fixture.records_by_node[node_id],
            failure_view=failure,
            clock=clock.now,
            learning_policy=(
                LearningDisposition.TENTATIVE if node_id == "node-d"
                else LearningDisposition.REJECTED
            ),
        ))
    return FixtureRuntime(fixture, clock, traces, failure, registry)


def assert_fixture_privacy(fixture: OpaqueFixture) -> dict[str, bool]:
    all_values = (fixture.left_symbol, fixture.right_symbol) + fixture.distractor_symbols
    unrelated = fixture.distractor_symbols
    return {
        "answer_absent_from_query": all(symbol not in fixture.query.content for symbol in all_values),
        "complete_answer_absent_from_every_memory": all(
            fixture.expected_answer != record.value
            for records in fixture.records_by_node.values()
            for record in records
        ),
        "answer_symbols_absent_from_distractors": all(
            fixture.left_symbol not in value and fixture.right_symbol not in value
            for value in unrelated
        ),
        "nodes_have_separate_record_collections": len({id(records) for records in fixture.records_by_node.values()}) == 4,
    }
