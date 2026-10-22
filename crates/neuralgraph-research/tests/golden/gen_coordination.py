#!/usr/bin/env python3
"""Fixtures for the coordination simulator (``coordination.json``).

The pinned artifacts in ``research/coordination/artifacts/`` cover seed 20260813. This generator records what
they do not: the logical clock and opaque ids, the experiment and benchmark at other seeds (negative, wrapping
past a day, large), a short sweep and a scale sweep at another seed, the single-seed benchmark markdown (which
has no pinned artifact), canonical serialisations of mock and real-adapter exports, the storage lineage
resolver's output, and the exact ``str``/``repr`` of every error the real adapter's boundary raises.

``benchmark.py`` and ``scale.py`` exist only at ``13a1729`` (they produced the pinned artifacts but never landed
on main); they are extracted from git into the reference tree with their imports pointed at
``NeuralGraph.research.coordination``, exactly as the reproduction check ran them.

Run from the repository root: ``PYTHONHASHSEED=0 python crates/neuralgraph-research/tests/golden/gen_coordination.py``
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import replace

import _reference as R

ARTIFACT_COMMIT = "13a1729"
COORD = R.REF / "NeuralGraph" / "research" / "coordination"
for name in ("benchmark", "scale"):
    source = subprocess.run(
        ["git", "-C", str(R.ROOT), "show", f"{ARTIFACT_COMMIT}:NeuralGraph/coordination/{name}.py"],
        check=True, capture_output=True, text=True).stdout
    target = COORD / f"{name}.py"
    if not target.exists():
        target.write_text(source.replace("NeuralGraph.coordination", "NeuralGraph.research.coordination"))

from NeuralGraph.research.coordination import benchmark, scale  # noqa: E402
from NeuralGraph.research.coordination.adapters import (  # noqa: E402
    MockMemoryNodeAdapter, NeuralGraphMemoryAdapter, PrivateMemoryRecord,
)
from NeuralGraph.research.coordination.contracts import (  # noqa: E402
    AuthorizationContext, Availability, CapabilityDescriptor, PolicyStatus,
)
from NeuralGraph.research.coordination.core import LogicalClock, canonical_json  # noqa: E402
from NeuralGraph.research.coordination.experiment import run_experiment  # noqa: E402
from NeuralGraph.research.coordination.fixture import REQUIRED_SCOPE, build_runtime  # noqa: E402
from NeuralGraph.research.coordination.storage_adapter import StorageLineageResolver  # noqa: E402
from NeuralGraph.research.retrieval.data_types import (  # noqa: E402
    EdgeType, NeuralEdge, NeuralNode, NodeLayer,
)
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402


def artifact_sha(result) -> str:
    return hashlib.sha256((json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8")).hexdigest()


def clocks():
    out = []
    for seed in [0, 1, 86399, 86400, 86401, -1, -86401, 20260813, 2**40 + 3]:
        clock = LogicalClock(seed)
        out.append({"seed": seed, "now": [clock.now() for _ in range(3)]})
    # 1001 ticks cross a second boundary
    clock = LogicalClock(5)
    ticks = [clock.now() for _ in range(1001)]
    out.append({"seed": 5, "tick_1000": ticks[1000], "tick_999": ticks[999]})
    return out


def opaque_ids():
    cases = [("trace", ["query_1", "memory-a", "0"]), ("claim", ["q", "node-a", "memory-a"]),
             ("evidence", ["memory-b"]), ("trace", []), ("claim", ["ünïcode", "|", ""])]
    return [{"kind": k, "parts": p, "id": MockMemoryNodeAdapter._opaque_id(k, *p)} for k, p in cases]


def experiments():
    out = []
    for seed in [0, -5, 86401]:
        result = asyncio.run(run_experiment(seed))
        out.append({"seed": seed, "result": result, "sha256": artifact_sha(result)})
    return out


def benchmark_cases():
    full = asyncio.run(benchmark.run_benchmark(1))
    markdown = {str(seed): benchmark.render_markdown(asyncio.run(benchmark.run_benchmark(seed)))
                for seed in (20260813, 1)}
    sweep = asyncio.run(benchmark.run_sweep(5, 3))
    return {
        "seed1": full,
        "seed1_sha256": artifact_sha(full),
        "markdown": markdown,
        "sweep_5_3": sweep,
        "sweep_5_3_sha256": artifact_sha(sweep),
        "sweep_5_3_markdown": benchmark.render_sweep_markdown(sweep),
    }


def scale_cases():
    result = asyncio.run(scale.run_scale_sweep(11))
    return {"seed11": result, "seed11_sha256": artifact_sha(result), "seed11_markdown": scale.render_markdown(result)}


def mock_exports():
    """Canonical exports of the mock adapter on the allowed, denied and redacted paths."""
    runtime = build_runtime(77, "golden")
    query = runtime.fixture.query
    allowed = asyncio.run(runtime.registry.adapter_for("node-b").query(query))
    denied = asyncio.run(runtime.registry.adapter_for("node-a").query(
        replace(query, authorization=AuthorizationContext(scopes=("wrong-scope",)))))
    record = PrivateMemoryRecord(
        memory_id="secret-memory", capability_id="secret-capability", slot="secret-slot", value="V",
        confidence=0.8, source_ids=("s",), parent_memory_ids=("p",), lineage_root_ids=("r",),
        failure_domains=("d",), edge_path=("e",), required_scope="secret-scope", policy_mode=PolicyStatus.REDACTED)
    adapter = MockMemoryNodeAdapter("redacted-node", (record,), runtime.failure, runtime.clock.now)
    redacted_request = replace(query, requested_capability="secret-capability", authorization=AuthorizationContext(
        scopes=("secret-scope",), allowed_node_ids=("redacted-node",), allow_redacted=True))
    redacted = asyncio.run(adapter.query(redacted_request))
    refused = asyncio.run(adapter.query(replace(redacted_request, authorization=AuthorizationContext(
        scopes=("secret-scope",), allowed_node_ids=("redacted-node",)))))
    return {"seed": 77, "allowed": canonical_json(allowed), "denied": canonical_json(denied),
            "redacted": canonical_json(redacted), "redaction_refused": canonical_json(refused),
            "trace_events": canonical_json(runtime.traces.events)}


SESSION = "golden-session"


def lineage_graph():
    """messages -> episodes -> topic, plus a dangling source and a cycle. Distinct embeddings: no score ties."""
    storage = InMemoryNeuralGraphStorage()

    async def build():
        def node(node_id, layer, content, embedding=None, sources=(), meta=None):
            return NeuralNode(node_id=node_id, layer=layer, content=content, embedding=embedding,
                              session_key=SESSION, source_memory_ids=list(sources), metadata=meta or {})

        for i in range(4):
            await storage.save_node(node(f"m{i}", NodeLayer.MESSAGE, f"raw {i}", [0.1 * (i + 1), 0.2, 0.3]))
        await storage.save_node(node("e1", NodeLayer.EPISODE, "episode one", [0.9, 0.1, 0.05], ["m0", "m1"],
                                     {"slot": "left"}))
        await storage.save_node(node("e2", NodeLayer.EPISODE, "episode two", [0.7, 0.3, 0.1], ["m2", "gone"],
                                     {"slot": "right"}))
        await storage.save_node(node("t1", NodeLayer.TOPIC, "topic", [0.5, 0.5, 0.5]))
        for parent, child in [("e1", "m0"), ("e1", "m1"), ("e2", "m2"), ("e2", "m3"), ("t1", "e1"), ("t1", "e2"),
                              ("m3", "e2")]:
            await storage.save_edge(NeuralEdge(edge_id=f"edge-{parent}-{child}", source_id=parent,
                                               target_id=child, edge_type=EdgeType.HIERARCHY))
        await storage.save_edge(NeuralEdge(edge_id="sem-e1-e2", source_id="e1", target_id="e2",
                                           edge_type=EdgeType.SEMANTIC))

    asyncio.run(build())
    return storage


def lineage_cases():
    storage = lineage_graph()
    out = []
    for node_id in ["m0", "e1", "e2", "t1"]:
        for depth, nodes in [(3, 64), (1, 64), (2, 2), (0, 64), (5, 1)]:
            resolver = StorageLineageResolver(storage, max_depth=depth, max_nodes=nodes)
            node = asyncio.run(storage.get_node(node_id))
            resolved = asyncio.run(resolver(node))
            out.append({"node": node_id, "max_depth": depth, "max_nodes": nodes,
                        "resolved": {k: list(v) for k, v in resolved.items()}})
    return out


def real_adapter_exports():
    storage = lineage_graph()
    runtime = build_runtime(78, "golden-real")
    capability = CapabilityDescriptor(
        capability_id=runtime.fixture.query.requested_capability, node_id="real-node", description="golden",
        query_types=("composition",), policy_scope=(REQUIRED_SCOPE,), availability=Availability.AVAILABLE)
    request = replace(runtime.fixture.query, authorization=AuthorizationContext(
        scopes=(REQUIRED_SCOPE,), allowed_node_ids=("real-node",)))

    async def retrieve(_request):
        return await storage.vector_search([0.8, 0.2, 0.1], SESSION, limit=8, layer_filter=[NodeLayer.EPISODE])

    def projection(node):
        return {"slot": str(node.metadata["slot"]), "value": str(node.content), "n": 3, "x": 0.25, "ok": True}

    out = {}
    for label, policy, kwargs in [
        ("default", PolicyStatus.ALLOWED, {}),
        ("resolver", PolicyStatus.ALLOWED, {"lineage_resolver": StorageLineageResolver(storage)}),
        ("projection", PolicyStatus.ALLOWED, {"lineage_resolver": StorageLineageResolver(storage),
                                               "claim_projection": projection}),
        ("denied", PolicyStatus.DENIED, {"lineage_resolver": StorageLineageResolver(storage)}),
    ]:
        adapter = NeuralGraphMemoryAdapter("real-node", capability, retrieve, lambda _n, _a, p=policy: p,
                                           runtime.clock.now, **kwargs)
        out[label] = canonical_json(asyncio.run(adapter.query(request)))
    return out


class Stub:
    def __init__(self, node_id="local-memory-1", content="locally held fact", source_memory_ids=()):
        self.node_id = node_id
        self.content = content
        self.source_memory_ids = list(source_memory_ids)


class NoId:
    content = "x"


class Leaky:
    pass


def adapter_errors():
    """`str(exc)` and `repr(exc)` of every error the real adapter's validation raises."""
    runtime = build_runtime(79, "golden-errors")
    capability = CapabilityDescriptor(
        capability_id=runtime.fixture.query.requested_capability, node_id="real-node", description="golden",
        query_types=("composition",), policy_scope=(REQUIRED_SCOPE,), availability=Availability.AVAILABLE)
    cases = [
        ("no_node_id", NoId(), {}),
        ("blank_node_id", Stub(node_id="  "), {}),
        ("resolver_list", Stub(), {"lineage_resolver": ["x"]}),
        ("resolver_none", Stub(), {"lineage_resolver": None}),
        ("bare_string", Stub(), {"lineage_resolver": {"source_ids": "memory-42"}}),
        ("bytes", Stub(), {"lineage_resolver": {"lineage_root_ids": b"m"}}),
        ("set", Stub(), {"lineage_resolver": {"edge_path": {"m"}}}),
        ("int_entry", Stub(), {"lineage_resolver": {"parent_memory_ids": (7,)}}),
        ("object_entry", Stub(), {"lineage_resolver": {"failure_domains": (Leaky(),)}}),
        ("blank_entry", Stub(), {"lineage_resolver": {"parent_memory_ids": (" ",)}}),
        ("operator_int", Stub(), {"lineage_resolver": {"retrieval_operator": 7}}),
        ("operator_blank", Stub(), {"lineage_resolver": {"retrieval_operator": ""}}),
        ("content_not_dict", Stub(), {"claim_projection": "text"}),
        ("content_key", Stub(), {"claim_projection": {7: "v"}}),
        ("content_value", Stub(), {"claim_projection": {"text": [1]}}),
        ("content_quote_key", Stub(), {"claim_projection": {"it's": {}}}),
        ("content_nan", Stub(), {"claim_projection": {"score": float("nan")}}),
        ("content_inf", Stub(), {"claim_projection": {"score": float("-inf")}}),
        ("stored_lineage", Stub(source_memory_ids=[""]), {}),
    ]
    out = []
    for label, node, spec in cases:
        kwargs = {}
        if "lineage_resolver" in spec:
            value = spec["lineage_resolver"]

            async def resolver(_node, value=value):
                return value
            kwargs["lineage_resolver"] = resolver
        if "claim_projection" in spec:
            value = spec["claim_projection"]
            kwargs["claim_projection"] = lambda _node, value=value: value

        async def retrieve(_request, node=node):
            return [(node, 0.9)]

        adapter = NeuralGraphMemoryAdapter("real-node", capability, retrieve, lambda _n, _a: PolicyStatus.ALLOWED,
                                           runtime.clock.now, **kwargs)
        try:
            asyncio.run(adapter.query(runtime.fixture.query))
            raise AssertionError(f"{label} did not raise")
        except (TypeError, ValueError) as exc:
            out.append({"case": label, "type": type(exc).__name__, "str": str(exc), "repr": repr(exc)})
    return out


def main():
    R.dump("coordination.json", {
        "clock": clocks(),
        "opaque_ids": opaque_ids(),
        "mock_exports": mock_exports(),
        "lineage": lineage_cases(),
        "real_adapter": real_adapter_exports(),
        "adapter_errors": adapter_errors(),
        "experiments": experiments(),
        "benchmark": benchmark_cases(),
        "scale": scale_cases(),
    })


if __name__ == "__main__":
    main()
