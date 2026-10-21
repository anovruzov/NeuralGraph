#!/usr/bin/env python3
"""Fixtures for ``retrieval/consolidation.py``: full consolidation cycles over LoCoMo graphs (recovery of stuck
nodes, pruning, merging of near-duplicates, summary hubs linked into the temporal chain, promotion and
eviction with factual-node protection) at 20 and 60 days after ingestion, the cycle interval, every public
helper (pruning by type, heat boost and decay, statistics, savings, protection) and the errors Python raised.
Writes ``consolidation.json``. ``total_time_ms`` is wall-clock and left out."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import _engine as E
import _reference as R

E.install()
from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.consolidation import ConsolidationConfig, ConsolidationManager  # noqa: E402
from NeuralGraph.research.retrieval.data_types import ConsolidationState, EdgeType  # noqa: E402
from NeuralGraph.research.retrieval.hierarchy import HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402
from NeuralGraph.research.retrieval.temporal import TemporalChainManager  # noqa: E402

T0 = R.FIXED_NOW
NOWS = {"t0": T0, "t19d": T0 + timedelta(days=19), "t20d": T0 + timedelta(days=20, hours=1),
        "t60d": T0 + timedelta(days=60)}
DUPLICATES = [
    (0, "Hey Mel, good to see you!! How have you been?", ["ent:friends"], None),
    (3, None, ["ent:kids", "ent:school"], "Melanie"),
    (3, None, [], None),
]


def cycle(result):
    d = result.to_dict()
    d.pop("total_time_ms")
    return d


async def err(coro):
    try:
        r = await coro
        return {"ok": None if r is None else (r.node_id if hasattr(r, "node_id") else r)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


async def build(st, h, tm, specs, session):
    msgs = await E.add_messages(h, specs, session)
    for src, content, ents, speaker in DUPLICATES:
        text = content or specs[src]["content"] + " Really."
        meta = {"speaker": speaker} if speaker else {}
        msgs.append(await h.create_message_node(
            content=text, session_key=session, embedding=fake_embedding(specs[src]["content"], E.DIM),
            entity_ids=ents, importance_score=0.45, created_at=datetime.fromisoformat(specs[src]["created_at"]),
            **meta))
    lonely = await h.create_message_node(content="ok", session_key=session, importance_score=0.3,
                                         embedding=fake_embedding("ok", E.DIM),
                                         created_at=datetime.fromisoformat(specs[1]["created_at"]))
    msgs.append(lonely)
    await tm.build_temporal_chain(msgs[: len(specs)], session)
    await E.add_semantic_and_entity_edges(st, msgs[: len(specs)])
    return msgs


async def main():
    specs = E.locomo_messages(0, [1, 2, 3, 4], max_turns=15)
    out = {"specs": specs, "nows": {k: v.isoformat() for k, v in NOWS.items()}}
    st = InMemoryNeuralGraphStorage()
    h = HierarchyManager(st)
    tm = TemporalChainManager(st)
    msgs = await build(st, h, tm, specs, "c1")
    cm = ConsolidationManager(st, h, temporal_manager=tm)
    out["stats_t0"] = await cm.get_consolidation_statistics("c1")
    out["savings_t0"] = await cm.estimate_memory_savings("c1")
    out["protect_t0"] = [await cm._should_protect_factual_node(n) for n in msgs]

    E.set_now(NOWS["t19d"])
    for i in [4, 11, 20]:
        for _ in range(12):
            msgs[i].activate(0.5)
    msgs[30].activate(0.2)
    E.set_now(NOWS["t20d"])
    msgs[2].consolidation_state = ConsolidationState.CONSOLIDATING
    msgs[9].consolidation_state = ConsolidationState.CONSOLIDATING
    msgs[9].heat_score = 0.1
    msgs[16].consolidation_state = ConsolidationState.CONSOLIDATING
    msgs[16].updated_at = NOWS["t20d"] - timedelta(minutes=2)
    out["stats_t20"] = await cm.get_consolidation_statistics("c1")
    out["clusters"] = [E.ids(c) for c in await cm._find_dense_clusters("c1")]
    out["cycle1"] = cycle(await cm.run_consolidation_cycle("c1"))
    out["cycle1_again"] = cycle(await cm.run_consolidation_cycle("c1"))
    out["graph1"] = await E.dump_graph(st, "c1")
    out["stats_after"] = await cm.get_consolidation_statistics("c1")
    out["savings_after"] = await cm.estimate_memory_savings("c1")

    # a second graph, two cycles 60 days on, no temporal manager, no interval
    st2 = InMemoryNeuralGraphStorage()
    h2 = HierarchyManager(st2)
    tm2 = TemporalChainManager(st2)
    E.set_now(NOWS["t0"])
    msgs2 = await build(st2, h2, tm2, specs[:30], "c2")
    E.set_now(NOWS["t60d"])
    cm2 = ConsolidationManager(st2, config=ConsolidationConfig(min_cycle_interval_seconds=0.0, prune_batch_size=7,
                                                                merge_max_per_cycle=1, hub_max_per_cycle=1,
                                                                hub_cluster_size_threshold=3))
    out["c2_cycles"] = [cycle(await cm2.run_consolidation_cycle("c2")) for _ in range(3)]
    out["graph2"] = await E.dump_graph(st2, "c2")

    # helpers on a third graph
    st3 = InMemoryNeuralGraphStorage()
    h3 = HierarchyManager(st3)
    tm3 = TemporalChainManager(st3)
    E.set_now(NOWS["t0"])
    msgs3 = await build(st3, h3, tm3, specs[:20], "c3")
    E.set_now(NOWS["t20d"])
    cm3 = ConsolidationManager(st3, config=ConsolidationConfig(recency_aware_retention=False))
    out["prune_by_type"] = [await cm3.prune_edges_by_type("c3", EdgeType.ENTITY, 0.2),
                            await cm3.prune_edges_by_type("c3", EdgeType.SEMANTIC),
                            await cm3.prune_edges_by_type("c3", EdgeType.TEMPORAL, 0.0)]
    out["boost"] = [await cm3.boost_heat(msgs3[0].node_id), await cm3.boost_heat(msgs3[0].node_id, 20.0),
                    await cm3.boost_heat("missing", 1.0)]
    msgs3[5].heat_score = 0.0
    out["decay_all"] = [await cm3.decay_all_heat("c3"), await cm3.decay_all_heat("c3", decay_rate=0.3)]
    out["protect_session"] = await cm3.protect_session_facts("c3")
    out["lifecycle"] = list(await cm3._process_heat_lifecycle("c3"))
    out["empty_stats"] = await cm3.get_consolidation_statistics("nothing")
    out["empty_savings"] = await err(cm3.estimate_memory_savings("nothing"))
    out["hub_empty"] = await err(cm3._create_hub_for_cluster([], "c3"))
    naive = await h3.create_message_node(content="naive", session_key="c3", created_at=datetime(2023, 5, 8, 13, 0))
    out["hub_mixed"] = await err(cm3._create_hub_for_cluster([msgs3[1], naive], "c3"))
    naive.consolidation_state = ConsolidationState.CONSOLIDATING
    naive.updated_at = datetime(2026, 1, 1)
    out["recover_naive"] = await err(cm3._recover_orphaned_nodes("c3"))
    out["graph3"] = await E.dump_graph(st3, "c3")
    out["counts"] = [len(msgs), len(msgs2), len(msgs3)]
    R.dump("consolidation.json", out)


asyncio.run(main())
