#!/usr/bin/env python3
"""Fixtures for ``retrieval/pattern_completion.py``: numpy connection weights (float64 BLAS dot and norms) on
many vector lengths, the edge and embedding caches, spreading (hybrid and embedding-only), activation delta,
sparsity, energy, full CA3 completions over a LoCoMo graph under several configurations (iterations and
convergence read from the completer's log line), and theta-gamma slot organisation. Writes
``pattern_completion.json``."""
from __future__ import annotations

import asyncio
import re

import numpy as np

import _engine as E
import _reference as R

E.install()
import NeuralGraph.research.retrieval.pattern_completion as pc_mod  # noqa: E402
from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.hierarchy import HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.pattern_completion import (  # noqa: E402
    ActivationState, CA3PatternCompleter, PatternCompletionConfig, ThetaGammaOrganizer)
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402


class LogCapture:
    def __init__(self):
        self.last = None

    def info(self, msg, *a, **k):
        self.last = msg

    def debug(self, *a, **k):
        pass


LOG = LogCapture()
pc_mod.logger = LOG
LINE = re.compile(r"Pattern completion: (\d+) seeds -> (\d+) nodes \((\d+) iterations, converged=(True|False)\)")


def log_state():
    m = LINE.fullmatch(LOG.last)
    return {"seeds": int(m[1]), "nodes": int(m[2]), "iterations": int(m[3]), "converged": m[4] == "True"}


def vectors():
    rng = np.random.default_rng(23)
    out = []
    for dim in [1, 2, 3, 7, 8, 16, 17, 32, 33, 64, 100, 127, 128, 129, 255, 256, 300, 768]:
        for _ in range(3):
            out.append([[float(x) for x in rng.standard_normal(dim)], [float(x) for x in rng.standard_normal(dim)]])
    out.append([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]])
    out.append([[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])
    out.append([[1e-200, 3e-200], [2e-200, 1e-200]])
    out.append([[1e200, -3e150, 2e100], [5e199, 1e10, -2.0]])
    return out


async def main():
    st = InMemoryNeuralGraphStorage()
    h = HierarchyManager(st)
    specs = E.locomo_messages(1, [1, 2, 3], max_turns=14)
    msgs = await E.add_messages(h, specs, "p1")
    await E.add_semantic_and_entity_edges(st, msgs, window=8)
    # a few backwards edges so the incoming-edge branch of the cache sees new sources
    from NeuralGraph.research.retrieval.data_types import EdgeType, NeuralEdge
    for a, b in [(30, 2), (25, 4), (12, 3)]:
        await st.save_edge(NeuralEdge(edge_id=f"back-{a}-{b}", source_id=msgs[a].node_id, target_id=msgs[b].node_id,
                                      edge_type=EdgeType.ENTITY if a % 2 else EdgeType.SEMANTIC, base_weight=0.45))
    out = {"specs": specs}

    pc = CA3PatternCompleter(st)
    vecs = vectors()
    out["vectors"] = vecs
    out["weights"] = [pc._compute_connection_weight(a, b) for a, b in vecs]
    embs = [fake_embedding(s["content"], E.DIM) for s in specs]
    out["weights_fake"] = [[pc._compute_connection_weight(embs[i], embs[j]) for j in range(0, len(embs), 3)]
                           for i in range(0, len(embs), 5)]

    queries = ["How is the adoption going with the kids?", "Tell me about the painting and art class",
               "camping trip with family at the beach", "zzz nothing matches"]
    out["queries"] = queries
    configs = {
        "default": None,
        "long": PatternCompletionConfig(max_iterations=6, convergence_threshold=0.0, sparsity_k=5),
        "wide": PatternCompletionConfig(connectivity_radius=0.1, spreading_factor=0.9, activation_decay=0.5,
                                        use_energy_function=False),
        "converging": PatternCompletionConfig(max_iterations=10, convergence_threshold=0.05),
    }
    completions = []
    for name, cfg in configs.items():
        comp = CA3PatternCompleter(st, cfg)
        for q in queries:
            seeds = await st.vector_search(fake_embedding(q, E.DIM), "p1", limit=5)
            result = await comp.complete_pattern(seeds, "p1")
            completions.append({"config": name, "query": q, "seeds": E.scored(seeds), "result": E.scored(result),
                                "state": log_state()})
        subset = msgs[::2]
        seeds = [(msgs[0], 0.9), (msgs[1], 1.7), (msgs[40], -0.2)]
        result = await comp.complete_pattern(seeds, "p1", all_nodes=subset)
        completions.append({"config": name, "query": "subset", "seeds": E.scored(seeds), "result": E.scored(result),
                            "state": log_state()})
    out["completions"] = completions
    out["empty_seeds"] = E.scored(await pc.complete_pattern([], "p1"))
    out["no_nodes"] = E.scored(await pc.complete_pattern([(msgs[0], 0.5)], "empty-session"))

    # internals on an explicit state
    await pc._build_edge_cache(msgs)
    pc._build_embedding_cache(msgs + [await h.create_message_node(content="no embedding", session_key="p2")])
    out["edge_cache"] = {k: [[t, w] for t, w in v] for k, v in pc._edge_cache.items()}
    out["embedding_cache_ids"] = list(pc._embedding_cache)
    state = pc._initialize_state([(msgs[3], 0.8), (msgs[10], 0.05), (msgs[17], 1.4), (msgs[22], 0.4)])
    out["state"] = state.activations
    hybrid = await pc._spread_activation_hybrid(state, msgs)
    plain = pc._spread_activation(state, msgs)
    out["hybrid"] = hybrid
    out["plain"] = plain
    quiet = ActivationState(activations={msgs[0].node_id: 0.05, "ghost": 0.1})
    out["hybrid_quiet"] = await pc._spread_activation_hybrid(quiet, msgs)
    out["plain_quiet"] = pc._spread_activation(quiet, msgs)
    out["delta"] = [pc._compute_activation_delta(state.activations, hybrid), pc._compute_activation_delta({}, {}),
                    pc._compute_activation_delta(hybrid, {"x": 0.25})]
    out["sparsity"] = [pc._enforce_sparsity(hybrid), pc._enforce_sparsity(state.activations)]
    out["energy"] = [pc._compute_energy(hybrid, msgs), pc._compute_energy(plain, msgs),
                     pc._compute_energy({"ghost": 1.0, msgs[0].node_id: 0.5}, msgs)]
    out["build_result"] = E.scored(pc._build_result(ActivationState(activations=hybrid), msgs[5:]))

    # theta-gamma
    nowave = await h.create_message_node(content="no waves", session_key="p1")
    ranked = [(n, 1.0 - 0.01 * i) for i, n in enumerate(msgs[:20] + [nowave])]
    tg = []
    for slots in [7, 3, 1]:
        org = ThetaGammaOrganizer(gamma_slots=slots)
        organized = org.organize_by_phase(ranked, {"entity": 0.5})
        tg.append({"slots": slots, "organized": [E.scored(s) for s in organized],
                   "assignment": [org._compute_slot_assignment(n, None) for n, _ in ranked],
                   "flat": E.scored(org.flatten_organized(organized))})
    out["theta_gamma"] = tg
    out["theta_gamma_empty"] = [len(ThetaGammaOrganizer().organize_by_phase([])),
                                E.scored(ThetaGammaOrganizer().flatten_organized([]))]
    R.dump("pattern_completion.json", out)


asyncio.run(main())
