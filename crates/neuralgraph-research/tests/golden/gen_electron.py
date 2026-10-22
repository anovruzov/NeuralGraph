#!/usr/bin/env python3
"""Fixtures for ``retrieval/electron.py``: resonance factors, electron propagation / blending / interference,
accumulated charge, the electron pool stepped one step at a time on a LoCoMo graph (``_retrievers.build_graph``,
conversation 1) with every electron and charge recorded, settled runs with different pool parameters, the
``AttributeError`` of a signature-less electron, ``ElectronRetriever`` runs (configs x questions x wave
amplitudes, no-seed sessions), ``_apply_entity_boost``, and ``expand_with_dialogue_links`` through a
``DialogueLinker`` built over the same messages (the insertion-ordered copy of the linker module, as
``gen_dialogue_linker.py`` uses). Electron ids come from the shared uuid counter. Writes ``electron.json``."""
from __future__ import annotations

import asyncio
import json
import uuid as _uuid

import _engine as E
import _reference as R
import _retrievers as T
from _ordered_sets import load_ordered

import NeuralGraph.research.retrieval.electron as electron_mod  # noqa: E402

T.install([electron_mod])
R.ordered_ids([electron_mod])
dl = load_ordered("NeuralGraph.research.retrieval.dialogue_linker")

from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge  # noqa: E402
from NeuralGraph.research.retrieval.electron import (  # noqa: E402
    AccumulatedCharge, Electron, ElectronPool, ElectronRetriever, ElectronRetrieverConfig, calculate_resonance)
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402

DIM = E.DIM
QA = json.loads((R.DATASETS / "locomo10.json").read_text(encoding="utf-8"))[1]["qa"]
QUESTIONS = [QA[i]["question"] for i in [0, 2, 5, 8, 11]] + ["What does Gina think about Jon's dance studio?"]
WAVES = [
    None,
    {},
    {"entity": 0.8, "action": 0.5, "emotional": 0.2},
    {"temporal": 0.9, "causal": 0.4},
    {"emotional": 0.7, "relational": 0.7, "quantitative": 0.1},
    {"entity": 0.9, "temporal": 0.35, "__suppress_multi_hop": True},
    {"unknown": 0.6, "entity": -0.4},
]


class HexIds:
    def __init__(self):
        self.n = 0

    def uuid4(self):
        self.n += 1
        return _uuid.UUID(int=self.n << 96)


def edump(e):
    return [e.electron_id, e.charge, e.energy, e.frequency, e.phase, e.source_node_id, e.current_node_id,
            e.birth_time_ms, e.arrival_time_ms, e.hop_count, e.path, T.jsonable_waves(e.wave_signature),
            e.decay_factor]


def cdump(c):
    return [c.node_id, c.excitatory_charge, c.inhibitory_charge, c.electron_count, c.last_arrival_ms,
            c.phase_coherence, c.net_charge, c.total_energy, c.exceeds_threshold(), c.exceeds_threshold(0.05)]


def pool_state(p):
    return {"electrons": [edump(e) for e in p._electrons], "charge": [cdump(c) for c in p._node_charge.values()],
            "fired": [list(f) for f in p._fired_nodes], "refractory": dict(p._refractory_until), "time": p._current_time_ms,
            "waves_cached": list(p._node_wave_amplitudes)}


def err(fn):
    try:
        return {"ok": fn()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


async def aerr(fn):
    try:
        return {"ok": await fn()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def pure():
    out = {}
    sigs = [w for w in WAVES if w is not None] + [{"entity": 0.0005}, {"temporal": -0.9, "entity": 0.1},
                                                  {"action": 0.3, "state": 0.3, "spatial": 0.3, "causal": 0.3}]
    out["sigs"] = [T.jsonable_waves(s) for s in sigs]
    out["resonance"] = [[calculate_resonance(a, b) for b in sigs] for a in sigs]
    out["resonance_none"] = calculate_resonance(None, sigs[2])
    edges = [
        NeuralEdge(edge_id="e1", source_id="a", target_id="b", edge_type=EdgeType.TEMPORAL, base_weight=0.8,
                   propagation_delay_ms=40.0),
        NeuralEdge(edge_id="e2", source_id="b", target_id="c", edge_type=EdgeType.ENTITY, base_weight=0.6, sign=-1.0,
                   confidence=0.7, propagation_delay_ms=500.0),
        NeuralEdge(edge_id="e3", source_id="c", target_id="d", edge_type=EdgeType.SEMANTIC, base_weight=1.3,
                   propagation_delay_ms=0.0, ltp_boost=1.6),
    ]
    out["edges"] = [e.to_dict() for e in edges]
    e0 = Electron(charge=1.0, energy=0.9, frequency=14.1, phase=6.0, source_node_id="a", current_node_id="a",
                  path=["a"], wave_signature={"entity": 0.8, "temporal": 0.1})
    chain = [e0]
    for edge, src, blend in [(edges[0], {"temporal": 0.5, "spatial": 0.2}, 0.3), (edges[1], {}, 0.3),
                             (edges[2], {"entity": 1.0}, 0.75), (edges[0], None, 0.5)]:
        chain.append(chain[-1].propagate(edge, source_node_wave_amplitudes=src, wave_blend_factor=blend))
    out["chain"] = [edump(e) for e in chain]
    out["interfere"] = [[a.interfere_with(b) for b in chain] for a in chain]
    out["flags"] = [[e.is_excitatory, e.is_inhibitory, e.effective_charge] for e in chain]
    out["blend"] = [Electron._blend_wave_signatures(a, b, f) for a, b, f in [
        ({"entity": 0.8}, {"temporal": 0.5}, 0.3), ({}, {"x": 1.0}, 0.5), ({"a": 1.0, "b": 2.0}, {"b": 1.0}, 0.0)]]
    none_sig = Electron(wave_signature=None, current_node_id="a")
    out["none_sig"] = [err(lambda: edump(none_sig.propagate(edges[0]))),
                       err(lambda: edump(none_sig.propagate(edges[0], source_node_wave_amplitudes={"x": 1.0})))]
    acc = AccumulatedCharge(node_id="n", excitatory_charge=0.7, inhibitory_charge=-0.3)
    out["acc"] = cdump(acc)
    out["default_electron"] = edump(Electron())
    return out


async def pool_runs(st, msgs, episodes):
    out = {}
    seeds = [m.node_id for m in msgs[:5]] + [episodes[0].node_id, "missing-node"]
    out["seeds"] = seeds
    p = ElectronPool(st)
    steps = []
    injected = await p.inject_query_burst(WAVES[2], seeds)
    steps.append({"injected": [edump(e) for e in injected], "state": pool_state(p)})
    for _ in range(4):
        n = await p.propagate_step()
        steps.append({"created": n, "state": pool_state(p)})
    out["steps"] = steps
    out["accumulated"] = p.get_accumulated_charges()
    # injecting again while some seeds are refractory
    out["reinject"] = [edump(e) for e in await p.inject_query_burst(WAVES[3], seeds, initial_energy=0.6)]
    out["settled"] = await p.propagate_until_settled(max_steps=30)
    out["after_settled"] = pool_state(p)
    p._accumulate_charge(Electron(charge=-1.0, energy=0.4, current_node_id=seeds[0], arrival_time_ms=9.0))
    p._accumulate_charge(Electron(charge=1.0, energy=0.25, current_node_id="fresh", arrival_time_ms=3.0))
    out["legacy_charge"] = [cdump(p._node_charge[seeds[0]]), cdump(p._node_charge["fresh"])]
    p.reset()
    out["after_reset"] = pool_state(p)

    variants = []
    for params in [dict(), dict(theta_frequency_hz=8.0, firing_threshold=0.2, refractory_ms=5.0, max_hops=2,
                                energy_floor=0.05),
                   dict(firing_threshold=1.5, refractory_ms=0.5, max_hops=8, energy_floor=0.2)]:
        for wi in [1, 2, 4, 6]:
            for max_steps in [1, 50]:
                pool = ElectronPool(st, **params)
                await pool.inject_query_burst(WAVES[wi], seeds[::2], initial_energy=1.2)
                settled = await pool.propagate_until_settled(max_steps=max_steps)
                variants.append({"params": params, "w": wi, "max_steps": max_steps, "settled": settled,
                                 "accumulated": pool.get_accumulated_charges(), "time": pool._current_time_ms,
                                 "fired": [list(f) for f in pool._fired_nodes]})
    out["variants"] = variants
    none_pool = ElectronPool(st)
    await none_pool.inject_query_burst(None, seeds[:2])
    out["none_pool_state"] = pool_state(none_pool)
    out["none_pool_step"] = await aerr(none_pool.propagate_step)
    return out


async def main():
    out = {"pure": pure()}
    specs = T.message_specs(conv_index=1, sessions=(1, 2, 3), max_turns=14)
    out["specs"] = specs
    st = InMemoryNeuralGraphStorage()
    h, tm, msgs, episodes, topics = await T.build_graph(st, specs, "e1")
    out["episodes"], out["topics"] = E.ids(episodes), E.ids(topics)
    out["pool"] = await pool_runs(st, msgs, episodes)

    retrievers = {"default": ElectronRetriever(st),
                  "custom": ElectronRetriever(st, ElectronRetrieverConfig(seed_count=6, firing_threshold=0.3,
                                                                          max_hops=3, final_limit=8,
                                                                          max_propagation_steps=4,
                                                                          initial_energy=0.8))}
    runs = []
    for name, r in retrievers.items():
        for qi, q in enumerate(QUESTIONS):
            for wi in range(len(WAVES)):
                res = await T.call(lambda r=r, q=q, wi=wi: r.retrieve(q, fake_embedding(q, DIM), "e1",
                                                                       query_wave_amplitudes=WAVES[wi]))
                runs.append({"r": name, "q": qi, "w": wi, "result": res,
                             "fired": [list(f) for f in r._pool._fired_nodes]})
    for lim in [3, 0, 60]:
        r = retrievers["default"]
        q = QUESTIONS[0]
        runs.append({"r": "default", "q": 0, "w": 2, "limit": lim, "result": await T.call(
            lambda: r.retrieve(q, fake_embedding(q, DIM), "e1", limit=lim, query_wave_amplitudes=WAVES[2])),
            "fired": [list(f) for f in r._pool._fired_nodes]})
    out["runs"] = runs
    out["no_seeds"] = await T.call(lambda: retrievers["default"].retrieve("anything", fake_embedding("x", DIM),
                                                                          "empty-session"))

    d = retrievers["default"]
    fired = [(n_id, s) for n_id, s in runs[2]["result"]["nodes"]]
    fired_dup = fired[:4] + [(fired[0][0], 0.01)] + [("not-a-node", 3.0)]
    out["fired"] = fired_dup
    out["entity_boost"] = [await d._apply_entity_boost(fired_dup, ents) for ents in [
        ["Gina"], ["JON", "dance"], [], ["zzz"]]]

    dl.uuid = HexIds()
    linker = dl.DialogueLinker(st)
    await linker.process_dialogue_sequence(msgs, "e1")
    out["linked"] = {m.node_id: linker.get_linked_messages(m.node_id) for m in msgs[:6]}
    out["expand"] = [await d.expand_with_dialogue_links(fired_dup, linker),
                     await d.expand_with_dialogue_links(fired_dup, None),
                     await d.expand_with_dialogue_links([], linker),
                     await d.expand_with_dialogue_links([(m.node_id, 1.0 / (i + 1)) for i, m in enumerate(msgs)],
                                                        linker)]
    out["uuid_counter"] = R.UUIDS.n
    R.dump("electron.json", out)


asyncio.run(main())
