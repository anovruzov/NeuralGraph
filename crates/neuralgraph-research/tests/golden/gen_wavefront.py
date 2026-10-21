#!/usr/bin/env python3
"""Fixtures for ``retrieval/interference.py`` and ``retrieval/wavefront.py``: the interference grid (phases of
both signs and beyond 2*pi), theta phases of naive/aware timestamps, memory matching, trunk compression and
alignment, and wavefront propagation / diagonal-sweep retrieval over a LoCoMo graph with temporal, semantic,
entity and hierarchy edges. Writes ``wavefront.json``."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import _engine as E
import _reference as R

E.install()
from NeuralGraph.research.retrieval.hierarchy import HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.interference import InterferenceScorer, ResonanceAmplifier  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402
from NeuralGraph.research.retrieval.temporal import TemporalChainManager  # noqa: E402
from NeuralGraph.research.retrieval.wavefront import DiagonalSweepRetriever, WavefrontPropagator  # noqa: E402

QUERIES = [
    {"entity": 0.8, "temporal": 0.4},
    {"action": 0.9, "emotional": 0.5, "unknown_dim": 0.6, "state": 0.0},
    {"temporal": 1.0},
    {},
]
MEMORIES = [
    {"entity": 0.7, "temporal": 0.2, "relational": 0.5},
    {"action": 0.4, "emotional": 0.9, "unknown_dim": 0.1},
    {},
    {"temporal": 0.95, "causal": 0.3, "quantitative": 0.6, "spatial": 0.2, "state": 0.5},
]
PHASES = [0.0, 0.5, -0.5, 0.78, 1.0, 2.0, 2.4, 3.0, -3.1, 4.5, 6.2, 7.0, -7.0, 6.283185307179586, 100.0, -1e-300]


def result_dict(r):
    return [r.activation, r.interference_type.value, r.phase_diff, r.amplitude_product]


def err(fn):
    try:
        return {"ok": fn()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def wf_result(r):
    return {"nodes": [[w.node.node_id, w.activation, w.phase, w.interference_type.value, w.hop_depth, w.path]
                      for w in r.nodes],
            "total_propagations": r.total_propagations, "constructive_count": r.constructive_count,
            "destructive_count": r.destructive_count, "partial_count": r.partial_count,
            "max_depth_reached": r.max_depth_reached}


def interference():
    out = {"queries": QUERIES, "memories": MEMORIES, "phases": PHASES}
    out["grid"] = [[[result_dict(InterferenceScorer.compute_interference(q, m, qp, mp)) for qp, mp in
                     zip(PHASES, reversed(PHASES))] for m in MEMORIES] for q in QUERIES]
    out["phase_pairs"] = [[result_dict(InterferenceScorer.compute_interference(QUERIES[0], MEMORIES[0], 0.0, p))
                           for p in PHASES]]
    stamps = ["2020-01-01T00:00:00+00:00", "2023-05-08T13:56:00+00:00", "2023-05-08T13:56:00.083333+00:00",
              "2019-12-31T23:59:59.900000+00:00", "2026-09-30T08:00:00+02:00", "2023-05-08T13:56:00"]
    refs = [None, "2023-05-08T13:56:00+00:00", "2023-05-08T13:56:00"]
    out["stamps"], out["refs"] = stamps, refs
    out["temporal_phase"] = [[err(lambda s=s, r=r: InterferenceScorer.compute_temporal_phase(
        datetime.fromisoformat(s), None if r is None else datetime.fromisoformat(r))) for r in refs] for s in stamps]
    trunks = [ResonanceAmplifier.compress_to_trunk(a) for a in QUERIES + MEMORIES +
              [{"entity": 2.0, "relational": 1.0, "action": 3.0}]]
    out["trunks"] = trunks
    out["alignment"] = [[ResonanceAmplifier.compute_trunk_alignment(a, b) for b in trunks] for a in trunks]
    out["alignment_partial"] = [ResonanceAmplifier.compute_trunk_alignment({"entity_trunk": 0.5}, {}),
                                ResonanceAmplifier.compute_trunk_alignment({"entity_trunk": 0.5, "x": 1.0},
                                                                           {"entity_trunk": 0.25})]
    return out


async def main():
    out = {"interference": interference()}
    specs = E.locomo_messages(0, [1, 2, 3], max_turns=10)
    out["specs"] = specs
    st = InMemoryNeuralGraphStorage()
    h = HierarchyManager(st)
    tm = TemporalChainManager(st)
    msgs = await E.add_messages(h, specs, "w1")
    await tm.build_temporal_chain(msgs, "w1")
    await E.add_semantic_and_entity_edges(st, msgs)
    episodes = await h.auto_segment_to_episodes("w1")
    out["episodes"] = E.ids(episodes)
    ref = datetime(2023, 5, 8, 14, 0, tzinfo=timezone.utc)
    out["match"] = [[(lambda a, t: [a, t.value])(*InterferenceScorer.score_memory_match(q, n, r))
                     for q in QUERIES for r in [None, ref]] for n in msgs[::4]]

    prop = WavefrontPropagator(st)
    loose = WavefrontPropagator(st, temporal_decay=0.95, hierarchy_decay=0.9, min_activation=0.01)
    seeds_sets = [[msgs[0]], [msgs[5], msgs[5], msgs[12]], [episodes[0]], [episodes[1], msgs[25]], msgs[::6], []]
    runs = []
    for qi, q in enumerate(QUERIES):
        for si, seeds in enumerate(seeds_sets):
            for name, p, th, hh in [("default", prop, 5, 2), ("default", prop, 1, 0), ("default", prop, 0, 1),
                                    ("default", prop, 3, 3), ("loose", loose, 5, 2), ("loose", loose, 1, 0),
                                    ("loose", loose, 0, 1), ("loose", loose, 2, 4)]:
                r = await p.propagate(q, seeds, max_time_hops=th, max_hierarchy_hops=hh)
                runs.append({"propagator": name, "query": qi, "seeds": si, "time_hops": th, "hierarchy_hops": hh,
                             "result": wf_result(r)})
    out["seeds_sets"] = [E.ids(s) for s in seeds_sets]
    out["runs"] = runs
    out["run_ref"] = wf_result(await prop.propagate(QUERIES[0], [msgs[3]], reference_time=ref))
    tight = WavefrontPropagator(st, temporal_decay=0.5, hierarchy_decay=0.99, min_activation=0.4)
    out["run_tight"] = wf_result(await tight.propagate(QUERIES[1], msgs[::3]))
    naive = await h.create_message_node(content="naive", session_key="w1", created_at=datetime(2023, 5, 8, 13, 0))
    try:
        await prop.propagate(QUERIES[0], [naive])
        out["naive_error"] = None
    except TypeError as exc:
        out["naive_error"] = f"TypeError: {exc}"

    retriever = DiagonalSweepRetriever(prop)
    out["retrieve_loose"] = [E.scored(await DiagonalSweepRetriever(loose).retrieve(q, msgs[::5], limit=25))
                             for q in QUERIES]
    out["retrieve"] = [E.scored(await retriever.retrieve(q, seeds)) for q in QUERIES for seeds in seeds_sets[:5]]
    custom = DiagonalSweepRetriever(tight, constructive_boost=0.1, destructive_penalty=0.9)
    out["retrieve_custom"] = [E.scored(await custom.retrieve(QUERIES[0], msgs[::2], limit=7)),
                              E.scored(await custom.retrieve(QUERIES[2], msgs, limit=0))]
    out["graph"] = await E.dump_graph(st, "w1")
    R.dump("wavefront.json", out)


asyncio.run(main())
