#!/usr/bin/env python3
"""Fixtures for ``retrieval/temporal.py``: temporal chains over LoCoMo sessions, causal edges and chains,
summation, co-activation and LTP, STDP, LTP decay, temporal context and patterns, refractory periods,
consistency boosts and conflict decay, with "now" moved forward between steps. Writes ``temporal.json``."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import _engine as E
import _reference as R

E.install()
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge, NodeLayer  # noqa: E402
from NeuralGraph.research.retrieval.hierarchy import HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402
from NeuralGraph.research.retrieval.temporal import TemporalChainManager, TemporalConfig  # noqa: E402

T0 = R.FIXED_NOW
NOWS = {"t0": T0, "t3h": T0 + timedelta(hours=3), "t30d": T0 + timedelta(days=30, minutes=7)}


async def err(coro):
    try:
        return {"ok": await coro}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def edicts(edges):
    return [e.to_dict() for e in edges]


def chain(pairs):
    return [[n.node_id, e.edge_id] for n, e in pairs]


async def main():
    specs = E.locomo_messages(0, [1, 2, 3], max_turns=10)
    out = {"specs": specs, "nows": {k: v.isoformat() for k, v in NOWS.items()}}
    st = InMemoryNeuralGraphStorage()
    h = HierarchyManager(st)
    tm = TemporalChainManager(st)
    msgs = await E.add_messages(h, specs, "t1")

    # chains ------------------------------------------------------------------------------------------------
    out["chain"] = edicts(await tm.build_temporal_chain(list(reversed(msgs)), "t1"))
    out["chain_single"] = edicts(await tm.build_temporal_chain(msgs[:1], "t1"))
    extra = []
    for i, minutes in enumerate([5, 30, 2000]):
        extra.append(await h.create_message_node(content=f"extra note {i}", session_key="t1",
                                                 created_at=datetime.fromisoformat(specs[-1]["created_at"]) + timedelta(minutes=minutes)))
    out["auto_link"] = edicts(await tm.auto_link_temporal("t1"))
    out["auto_link_episode"] = edicts(await tm.auto_link_temporal("t1", NodeLayer.EPISODE))
    naive_a = await h.create_message_node(content="naive a", session_key="t2", created_at=datetime(2023, 5, 8, 13, 0))
    naive_b = await h.create_message_node(content="naive b", session_key="t2", created_at=datetime(2023, 5, 8, 12, 0, 0, 500))
    aware_c = await h.create_message_node(content="aware c", session_key="t2",
                                          created_at=datetime(2023, 5, 8, 14, 0, tzinfo=timezone.utc))
    out["naive_edge"] = (await tm.create_temporal_edge(naive_a, aware_c, direction="after")).to_dict()
    out["naive_chain"] = edicts(await tm.build_temporal_chain([naive_a, naive_b], "t2"))
    out["mixed_chain"] = await err(tm.build_temporal_chain([naive_a, aware_c], "t2"))

    # causal ------------------------------------------------------------------------------------------------
    m = msgs
    out["causal"] = [
        None if e is None else e.to_dict() for e in [
            await tm.create_causal_edge(m[0], m[1], confidence=0.5),
            await tm.create_causal_edge(m[5], m[1]),
            await tm.create_causal_edge(m[1], m[2]),
            await tm.create_causal_edge(m[1], m[3], confidence=0.95, causal_type="enables"),
            await tm.create_causal_edge(m[3], m[4], confidence=0.7, causal_type="prevents"),
            await tm.create_causal_edge(m[4], m[1], confidence=0.9),
            await tm.create_causal_edge(naive_a, aware_c, confidence=0.6),
        ]]
    loose = TemporalChainManager(st, TemporalConfig(causal_time_order_required=False))
    out["causal_loose"] = (await loose.create_causal_edge(m[6], m[2], confidence=0.61)).to_dict()
    out["follow_causal"] = {
        "effects": chain(await tm.follow_causal_chain(m[1])),
        "effects_1": chain(await tm.follow_causal_chain(m[1], max_hops=1)),
        "causes": chain(await tm.follow_causal_chain(m[4], direction="causes", max_hops=5)),
        "none": chain(await tm.follow_causal_chain(m[9])),
    }

    # temporal traversal (activates edges) ------------------------------------------------------------------
    out["follow_temporal"] = {
        "backward": chain(await tm.follow_temporal_chain(m[8], max_hops=3)),
        "forward": chain(await tm.follow_temporal_chain(m[8], direction="forward", max_hops=5)),
        "strict": chain(await tm.follow_temporal_chain(m[8], min_weight=0.99999)),
        "start": chain(await tm.follow_temporal_chain(m[0])),
    }
    ctx = await tm.get_temporal_context(m[15], before_count=2, after_count=4)
    out["context"] = {"before": E.ids(ctx["before"]), "after": E.ids(ctx["after"])}

    # co-activation ------------------------------------------------------------------------------------------
    out["co_activation"] = [
        await tm.record_co_activation([m[2], m[7], m[4]]),
        await tm.record_co_activation([m[7], m[2], m[11], m[2]], context="test"),
        await tm.record_co_activation([m[3]]),
    ]
    out["ltp_strength"] = [await tm.get_ltp_strength(m[2], m[7]), await tm.get_ltp_strength(m[7], m[2]),
                           await tm.get_ltp_strength(m[4], m[11]), await tm.get_ltp_strength(m[0], m[1])]

    # three hours later: summation, STDP --------------------------------------------------------------------
    E.set_now(NOWS["t3h"])
    out["summation"] = [[await tm.temporal_summation(n, w) for w in [None, 12.0, 0.0, 2.5]] for n in m[::3] + extra]
    out["threshold"] = [[await tm.check_summation_threshold(n, t) for t in [None, 0.1, 5.0]] for n in m[::4]]
    first = out["chain"]
    stdp = []
    for i, dt_ms in enumerate([15.0, -8.0, 0.0, 400.0, -1e6, 3.0]):
        e = first[i]
        stdp.append(await tm.apply_stdp(e["source_id"], e["target_id"], dt_ms))
    stdp.append(await tm.apply_stdp(m[0].node_id, m[5].node_id, 5.0))
    out["stdp"] = stdp

    # a month later: decay ----------------------------------------------------------------------------------
    E.set_now(NOWS["t30d"])
    out["decay_ltp"] = await tm.decay_ltp("t1")
    out["decay_ltp_fast"] = await tm.decay_ltp("t1", decay_factor=0.5)
    out["decay_ltp_t2"] = await tm.decay_ltp("t2", decay_factor=0.0)
    out["patterns"] = {str(k): await tm.find_temporal_patterns("t1", min_occurrences=k) for k in [1, 2, 3, 500]}

    # refractory ----------------------------------------------------------------------------------------------
    r = m[20]
    refr = {"initial": [await tm.is_in_refractory(r), await tm.get_refractory_remaining(r)]}
    r.activate(0.4)
    refr["activated"] = [await tm.is_in_refractory(r), await tm.get_refractory_remaining(r), r.refractory_until.isoformat()]
    await tm.set_refractory_period(r, 250.5)
    refr["set"] = [await tm.is_in_refractory(r), await tm.get_refractory_remaining(r), r.refractory_until.isoformat()]
    refr["default"] = await err(tm.set_refractory_period(r))
    refr["zero"] = await err(tm.set_refractory_period(r, 0))
    r.refractory_until = datetime(2026, 10, 30, 8, 7, 0, 125000)  # naive, taken as UTC
    refr["naive"] = [await tm.is_in_refractory(r), await tm.get_refractory_remaining(r)]
    r.refractory_until = NOWS["t30d"] - timedelta(seconds=1)
    refr["past"] = [await tm.is_in_refractory(r), await tm.get_refractory_remaining(r)]
    out["refractory"] = refr

    # consistency boosts --------------------------------------------------------------------------------------
    d = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
    windows = [
        [d(2023, 5, 8), d(2023, 5, 9)],
        [d(2023, 5, 1), d(2023, 5, 3)],
        [d(2023, 6, 10, 12), d(2023, 7, 1)],
        [d(2023, 5, 25), None],
        [None, d(2023, 5, 20)],
        [None, None],
        [datetime(2023, 5, 8, 10), datetime(2023, 5, 8, 18)],
    ]
    markers = [None, ["May", "Yesterday"], [], ["support group", "last week", "kids"]]
    stamped = await h.create_message_node(content="nothing temporal", session_key="t3", timestamp="2023-05-08")
    boost_nodes = m[::4] + [stamped, naive_a]
    out["windows"] = [[None if w is None else w.isoformat() for w in pair] for pair in windows]
    out["markers"] = markers
    out["boosts"] = [[[list(tm.compute_temporal_consistency_boost(n, s, e, mk)) for mk in markers]
                      for s, e in windows] for n in boost_nodes]
    off = TemporalChainManager(st, TemporalConfig(temporal_consistency_enabled=False, conflict_detection_enabled=False))
    out["boost_disabled"] = list(off.compute_temporal_consistency_boost(m[0], *windows[0]))
    cands = [(n, 1.0 - 0.03 * i) for i, n in enumerate(boost_nodes)]
    out["applied"] = [E.scored(await tm.apply_temporal_consistency_boosts(cands, s, e, ["May"])) for s, e in windows]
    out["applied_disabled"] = E.scored(await off.apply_temporal_consistency_boosts(cands, *windows[0]))

    # conflict decay -----------------------------------------------------------------------------------------
    for src, tgt, direction in [(m[12], m[2], "before"), (m[2], m[12], "after"), (m[2], m[3], "after"),
                                (m[3], m[2], "before"), (m[0], m[25], None), (m[25], m[0], "sideways")]:
        e = NeuralEdge(edge_id=f"conflict-{src.node_id[-2:]}-{tgt.node_id[-2:]}", source_id=src.node_id,
                       target_id=tgt.node_id, edge_type=EdgeType.TEMPORAL, base_weight=0.8,
                       metadata={} if direction is None else {"direction": direction})
        e.effective_weight  # cache the weight before the decay invalidates it
        await st.save_edge(e)
    out["conflicts"] = [await tm.decay_conflicting_edges("t1", NOWS["t30d"]),
                        await tm.decay_conflicting_edges("t1", datetime(2026, 1, 1), tolerance_hours=1e6),
                        await tm.decay_conflicting_edges("t1", NOWS["t30d"], tolerance_hours=0.5),
                        await off.decay_conflicting_edges("t1", NOWS["t30d"])]
    out["graph"] = await E.dump_graph(st, "t1")
    out["graph_t2"] = await E.dump_graph(st, "t2")
    R.dump("temporal.json", out)


asyncio.run(main())
