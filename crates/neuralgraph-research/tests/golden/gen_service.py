#!/usr/bin/env python3
"""Fixtures for ``NeuralGraphService`` and its configurations (``retrieval/service.py`` before ``extract_keywords``).
Writes ``service.json``.

Services are built over LoCoMo conversation 0 the way a benchmark harness drives them: episodes (stand-ins for the
``memmachine`` ``Episode``: uid, content, a timezone-aware ``created_at``, ``filterable_metadata`` with entity ids,
``producer_id`` = the speaker, an importance score), ``fake_embedding(text, 32)`` vectors (the real model is
replaced by the product's deterministic embedder; the query side uses the same), wave amplitudes recorded by
``_engine.locomo_messages``. The clock is frozen, uuids count up (nodes, edges, electrons), and every ``set`` in
the engine modules is insertion-ordered (``_retrievers.install``; the dialogue linker is the insertion-ordered
copy ``_ordered_sets.load_ordered`` makes, its hex ids counting up as in ``gen_dialogue_linker.py``), so every
result is reproducible. The service's ``random`` (the global module in Python) is replaced by a seeded
``random.Random`` per scenario; the Rust service takes a ``PyRandom`` with the same seed.

What is recorded:
- the default configuration (every field) and the two preset configurations;
- ``default``: two ingestion batches (counts, errors, node ids; the whole graph afterwards), statistics, and
  ``retrieve`` for LoCoMo questions with different query wave amplitudes (ranked ids and scores, stages, counts,
  the logged gate profile, the wave dict after the call), the dynamic state of nodes and edges afterwards;
- ``single_hop`` / ``multi_hop``: the same under the preset configurations;
- ``variants``: configurations that switch components (electron / sequential retriever, no dialogue linking,
  hard / soft / no query routing, temporal dynamics always or never, no hierarchy / temporal / consolidation
  manager, disabled service, no neural retrieval);
- ``ingestion``: created_at shapes (datetime objects, LoCoMo strings, ISO strings, none, naive), embeddings
  missing or short, malformed input, and the error paths (a naive timestamp among aware ones, an unparseable
  date, a whitespace-only message breaking the dialogue linker);
- ``consolidation``: post-ingestion consolidation, manual cycles, heat, STDP, summation, patterns, refractory
  state, gate tuning, linking, statistics, clearing;
- ``wave_memory`` / ``extraction``: ``process_wave_memory`` and ``process_extraction_result`` with stand-ins for
  the ``memmachine`` types;
- ``external``: blending of scripted external sources (with and without embeddings, duplicates, a failing source,
  mismatched embedding sizes), the open-domain tracer;
- ``auto_consolidation``: the background task (coarse, it is timing based);
- ``rng``: the probabilistic LTP decay with a seeded generator.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
from dataclasses import asdict
from datetime import datetime

import _engine as E
import _reference as R
import _retrievers as T
from _ordered_sets import load_ordered

import NeuralGraph.research.retrieval.electron as electron_mod  # noqa: E402
import NeuralGraph.research.retrieval.external_retriever as ext_mod  # noqa: E402
import NeuralGraph.research.retrieval.flash_retriever as flash_mod  # noqa: E402
import NeuralGraph.research.retrieval.query_router as qr_mod  # noqa: E402
import NeuralGraph.research.retrieval.retriever as retriever_mod  # noqa: E402
import NeuralGraph.research.retrieval.service as svc  # noqa: E402

# ``InMemoryNeuralGraphStorage.clear_session`` empties its index sets with ``.clear()``
E.OrderedSet.clear = lambda self: self._d.clear()
T.install([retriever_mod, flash_mod, electron_mod, qr_mod, svc])
R.ordered_ids([electron_mod])
R.freeze_now([ext_mod])
dl = load_ordered("NeuralGraph.research.retrieval.dialogue_linker")
svc.DialogueLinker = dl.DialogueLinker
svc.create_dialogue_links = dl.create_dialogue_links

from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.consolidation import ConsolidationConfig  # noqa: E402
from NeuralGraph.research.retrieval.data_types import EdgeType  # noqa: E402
from NeuralGraph.research.retrieval.external_retriever import ExternalResult, ExternalRetriever  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402

DIM = E.DIM
SK = "conv0"
QA = json.loads((R.DATASETS / "locomo10.json").read_text(encoding="utf-8"))[0]["qa"]
QUESTIONS = [QA[i]["question"] for i in [0, 1, 2, 3, 4, 5, 6, 9, 12, 15, 30, 60, 100, 150, 190]] + [
    "What does Caroline think about Sweden and LGBTQ pride?", "What is the capital of France?"]
WAVE_VARIANTS = [
    None,
    {},
    {"entity": 0.8, "action": 0.5, "emotional": 0.2},
    {"temporal": 0.7, "entity": 0.6},
    {"entity": 0.9, "temporal": 0.35, "__suppress_multi_hop": True},
]


class HexIds:
    """``uuid`` stand-in for the dialogue linker: ``uuid4().hex`` is ``f"{n:08x}" + "0" * 24``."""

    def __init__(self):
        import uuid
        self._uuid = uuid
        self.n = 0

    def uuid4(self):
        self.n += 1
        return self._uuid.UUID(int=self.n << 96)


dl.uuid = HexIds()


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


CAP = Capture()
_logger = logging.getLogger(svc.__name__)
_logger.setLevel(logging.DEBUG)
_logger.addHandler(CAP)
_logger.propagate = False


def reset(seed=1234):
    """Fresh counters and a seeded generator for the service's ``random``."""
    R.UUIDS.reset()
    dl.uuid.n = 0
    svc.random = random.Random(seed)


# ---------------------------------------------------------------------------------------------------------
# stand-ins for the memmachine types
# ---------------------------------------------------------------------------------------------------------

class Episode:
    def __init__(self, spec):
        self.uid = spec["uid"]
        self.content = spec["content"]
        created = spec.get("created_at")
        if created is None:
            self.created_at = None
        elif "dt" in created:
            self.created_at = datetime.fromisoformat(created["dt"])
        else:
            self.created_at = created["text"]
        if spec.get("entity_ids") is not None:
            self.filterable_metadata = {"entity_ids": list(spec["entity_ids"])}
        else:
            self.filterable_metadata = None
        self.producer_id = spec.get("producer_id")
        self.importance_score = spec.get("importance_score", 0.5)


def episodes(specs):
    return [Episode(s) for s in specs]


def embeddings_for(specs):
    return [fake_embedding(s["content"], DIM) for s in specs]


def waves_for(specs):
    return [dict(s.get("wave") or {}) for s in specs]


def episode_specs(conv=0, sessions=(1, 2, 3), max_turns=16):
    """Episode specs for LoCoMo turns: aware UTC timestamps, entity ids, the speaker as producer."""
    out = []
    for i, s in enumerate(T.message_specs(conv, sessions, max_turns)):
        out.append({
            "uid": f"ep-{conv}-{i:04d}",
            "content": s["content"],
            "created_at": {"dt": s["created_at"]},
            "entity_ids": s["entity_ids"],
            "producer_id": s["speaker"],
            "importance_score": s["importance_score"],
            "wave": s["wave_amplitudes"],
        })
    return out


class Amplitudes:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return dict(self._d)


class Kind:
    def __init__(self, value):
        self.value = value


class Signal:
    def __init__(self, spec):
        self.signal_type = Kind(spec["type"])
        for key in ("raw_expression", "entity_name", "cause", "effect", "amplitude", "embedding"):
            if key in spec:
                setattr(self, key, spec[key])
        if "resolved_date" in spec:
            self.resolved_date = datetime.fromisoformat(spec["resolved_date"]) if spec["resolved_date"] else None


class Wave:
    def __init__(self, spec):
        self.wave_id = spec["wave_id"]
        self.content = spec["content"]
        self.embedding = spec["embedding"]
        if spec["amplitudes"] is not None:
            self.amplitudes = Amplitudes(spec["amplitudes"])
        self.signals = [Signal(s) for s in spec["signals"]]

    def _of(self, kind):
        return [s for s in self.signals if s.signal_type.value == kind]

    def get_temporal_signals(self):
        return self._of("temporal")

    def get_entity_signals(self):
        return self._of("entity")

    def get_relation_signals(self):
        return self._of("relation")


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def extraction(spec):
    return Obj(
        entities=[Obj(uid=e["uid"], name=e["name"], entity_type=Kind(e["entity_type"]), mekb_score=e["mekb_score"])
                  for e in spec["entities"]],
        relations=[Obj(uid=r["uid"], source_entity_uid=r["source_entity_uid"],
                       target_entity_uid=r["target_entity_uid"], relation_type=Kind(r["relation_type"]),
                       weight=r["weight"], confidence=r["confidence"]) for r in spec["relations"]],
        confidence=spec["confidence"])


# ---------------------------------------------------------------------------------------------------------
# recorders
# ---------------------------------------------------------------------------------------------------------

def ingest_rec(r):
    return {"nodes_created": r.nodes_created, "temporal_edges_created": r.temporal_edges_created,
            "entity_edges_created": r.entity_edges_created, "semantic_edges_created": r.semantic_edges_created,
            "errors": r.errors, "node_ids": [n.node_id for n in r.nodes]}


def result_rec(r):
    return {"nodes": [[n.node_id, s] for n, s in r.nodes], "stages": r.stages_executed,
            "seen": r.total_candidates_seen, "co": r.co_activations_recorded}


def slim_node(node):
    """The ``to_dict`` values of a node, in order (the fixture would otherwise be mostly key names); a MESSAGE
    node's embedding is ``"fake"`` when it is ``fake_embedding(content)`` (vectors would dominate the file). The
    Rust test applies the same rules to ``to_dict()``."""
    d = node.to_dict()
    if d["layer"] == 0 and d["embedding"] == fake_embedding(d["content"], DIM):
        d["embedding"] = "fake"
    return copy.deepcopy(list(d.values()))  # ``to_dict`` aliases the live lists and dicts


async def graph(storage, sk=SK):
    return {"nodes": [slim_node(n) for n in await storage.get_nodes_by_session(sk)],
            "edges": [copy.deepcopy(list(e.to_dict().values())) for e in await storage.get_all_edges(sk)]}


async def digest(storage, sk=SK, full=False):
    """The state retrieval and consolidation change: activation, heat, edge weights and counts (``full`` adds
    the timestamps, fairness counters and hierarchy links)."""
    nodes = await storage.get_nodes_by_session(sk)
    edges = await storage.get_all_edges(sk)
    if not full:
        return {"nodes": [[n.node_id, n.activation_level, n.heat_score, getattr(n, "_activation_count", 0),
                           n.consolidation_state.value] for n in nodes],
                "edges": [[e.edge_id, e.base_weight, e.ltp_boost, e.activation_count] for e in edges]}
    return {
        "nodes": [[n.node_id, n.activation_level, n.heat_score, n.last_activated.isoformat(),
                   getattr(n, "_activation_count", 0), dict(n.edge_type_counts), n.diversity_score,
                   n.consolidation_state.value, n.parent_id, list(n.child_ids)] for n in nodes],
        "edges": [[e.edge_id, e.base_weight, e.confidence, e.ltp_boost, e.activation_count,
                   e.last_activated.isoformat()] for e in edges],
    }


def waves_json(w):
    return None if w is None else {k: float(v) for k, v in w.items()}


async def run_questions(service, questions, limit=10, sk=SK, wave_variants=WAVE_VARIANTS):
    rows = []
    for i, q in enumerate(questions):
        variant = wave_variants[i % len(wave_variants)]
        waves = None if variant is None else dict(variant)
        before = waves_json(waves)
        CAP.messages.clear()
        row = {"q": q, "waves_in": before}
        try:
            r = await service.retrieve(q, fake_embedding(q, DIM), sk, limit=limit, query_wave_amplitudes=waves)
            row["result"] = result_rec(r)
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["waves_out"] = waves_json(waves)
        row["gate"] = [m for m in CAP.messages if m.startswith("Gate profile selected")]
        rows.append(row)
    return rows


def make(cfg=None, seed=1234, storage=None):
    reset(seed)
    return svc.NeuralGraphService(storage=storage or InMemoryNeuralGraphStorage(), config=cfg)


async def ingest_two_batches(service, specs, split):
    r1 = await service.process_episodes(episodes(specs[:split]), SK, embeddings_for(specs[:split]),
                                        waves_for(specs[:split]))
    r2 = await service.process_episodes(episodes(specs[split:]), SK, embeddings_for(specs[split:]),
                                        waves_for(specs[split:]))
    return [ingest_rec(r1), ingest_rec(r2)]


def config_dict(cfg):
    return asdict(cfg)


# ---------------------------------------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------------------------------------

SPECS = episode_specs()
assert episode_specs(0, (1,), 9) == SPECS[:9]  # the ingestion cases' base is a prefix


async def scenario_main(cfg, full_graph, questions=QUESTIONS, limit=10):
    service = make(cfg)
    out = {"ingest": await ingest_two_batches(service, SPECS, 24)}
    if full_graph:
        out["graph"] = await graph(service.storage)
    out["stats_ingested"] = await service.get_statistics(SK)
    out["questions"] = await run_questions(service, questions, limit)
    out["digest"] = await digest(service.storage, full=full_graph)
    out["stats_retrieved"] = await service.get_statistics(SK)
    out["linker"] = linker_summary(service)
    return out


def linker_summary(service):
    linker = service._dialogue_linkers.get(SK)
    if linker is None:
        return None
    return {"aggregators": len(linker._aggregators), "bindings": len(linker._bindings)}


async def scenario_variants():
    questions = QUESTIONS[::3]
    variants = {
        "electron": svc.NeuralGraphServiceConfig(use_electron_retriever=True),
        "electron_no_dialogue": svc.NeuralGraphServiceConfig(use_electron_retriever=True,
                                                             dialogue_linking_enabled=False),
        "sequential": svc.NeuralGraphServiceConfig(use_flash_retriever=False),
        "no_dialogue": svc.NeuralGraphServiceConfig(dialogue_linking_enabled=False),
        "routing_hard": svc.NeuralGraphServiceConfig(query_routing_mode="hard"),
        "routing_soft": svc.NeuralGraphServiceConfig(query_routing_mode="soft"),
        "routing_off": svc.NeuralGraphServiceConfig(query_routing_enabled=False),
        "dynamics_always": svc.NeuralGraphServiceConfig(temporal_dynamics_probability=1.0),
        "dynamics_never": svc.NeuralGraphServiceConfig(apply_temporal_dynamics=False),
        "no_hierarchy": svc.NeuralGraphServiceConfig(hierarchy_enabled=False),
        "no_temporal": svc.NeuralGraphServiceConfig(temporal_enabled=False),
        "no_consolidation": svc.NeuralGraphServiceConfig(consolidation_enabled=False),
        "no_segmentation": svc.NeuralGraphServiceConfig(auto_episode_segmentation=False,
                                                        auto_temporal_linking=False),
        "disabled": svc.NeuralGraphServiceConfig(enabled=False),
        "no_neural_retrieval": svc.NeuralGraphServiceConfig(use_neural_retrieval=False),
        "custom_components": svc.NeuralGraphServiceConfig(
            flash_config=flash_mod.FlashConfig(final_limit=7, min_score=0.2),
            hierarchy_config=svc.HierarchyConfig(min_messages_per_episode=3, max_messages_per_episode=8),
            temporal_config=svc.TemporalConfig(max_time_gap_hours=48.0),
            retriever_config=retriever_mod.RetrieverConfig(final_limit=9)),
        "sequential_custom": svc.NeuralGraphServiceConfig(use_flash_retriever=False,
                                                          retriever_config=retriever_mod.RetrieverConfig(final_limit=9)),
    }
    out = {}
    for name, cfg in variants.items():
        service = make(cfg)
        row = {"ingest": await ingest_two_batches(service, SPECS[:30], 18)}
        row["questions"] = await run_questions(service, questions, 8)
        row["digest"] = await digest(service.storage)
        row["stats"] = await service.get_statistics(SK)
        row["linker"] = linker_summary(service)
        out[name] = row
    return out


ROUTING_QUESTIONS = [
    "the one who painted the sunrise", "the woman who moved", "which book was it that she read",
    "Who is Caroline's friend?", "whose pet is named Luna", "the thing that she mentioned yesterday",
    "what is the one thing", QA[14]["question"], "the book that Caroline mentioned",
    "Who is Melanie's sister?",
]


async def scenario_routing():
    """Query types LoCoMo's questions rarely produce (multi-hop, adversarial, suppressed multi-hop), under each
    routing mode and the electron retriever."""
    out = {}
    variants = [("boost", svc.NeuralGraphServiceConfig()),
                ("hard", svc.NeuralGraphServiceConfig(query_routing_mode="hard")),
                ("soft", svc.NeuralGraphServiceConfig(query_routing_mode="soft")),
                ("electron", svc.NeuralGraphServiceConfig(use_electron_retriever=True)),
                ("sequential", svc.NeuralGraphServiceConfig(use_flash_retriever=False))]
    for name, cfg in variants:
        service = make(cfg)
        await ingest_two_batches(service, SPECS[:30], 18)
        out[name] = {"questions": await run_questions(service, ROUTING_QUESTIONS, 8,
                                                      wave_variants=[None, {}, {"entity": 0.7, "temporal": 0.4}]),
                     "digest": await digest(service.storage), "stats": (await service.get_statistics(SK))["query_routing"]}
    return out


def dt(s):
    return {"dt": s}


async def ingest_case(name, cfg, specs, embeddings, wave_list, out, sk=SK, edges_only=False):
    service = make(cfg)
    try:
        r = await service.process_episodes(episodes(specs), sk, embeddings, wave_list)
        row = {"result": ingest_rec(r)}
    except Exception as exc:  # noqa: BLE001
        row = {"error": f"{type(exc).__name__}: {exc}"}
    row["graph"] = await graph(service.storage, sk)
    if edges_only:
        row["graph"] = {"edges": row["graph"]["edges"]}
    row["totals"] = [service._total_nodes_created, service._total_edges_created, service._total_retrievals]
    row["linker"] = linker_summary(service) if sk == SK else None
    out[name] = row
    return service


async def scenario_ingestion():
    out = {}
    base = episode_specs(0, (1,), 9)

    # created_at shapes
    def with_created(spec, created):
        return dict(spec, created_at=created)

    locomo_text = ["1:56 pm on 8 May, 2023", "1:57 pm on 8 May, 2023", "8 May 2023", "May 8, 2023"]
    shapes = []
    for i, s in enumerate(base):
        kind = i % 6
        if kind == 0:
            shapes.append(with_created(s, {"text": f"2023-05-08T13:{56 + i % 3}:00"}))
        elif kind == 1:
            shapes.append(with_created(s, {"text": locomo_text[i % 4]}))
        elif kind == 2:
            shapes.append(with_created(s, {"text": "yesterday-ish"}))
        elif kind == 3:
            shapes.append(with_created(s, {"text": ""}))
        elif kind == 4:
            shapes.append(with_created(s, {"dt": f"2023-05-08T13:{(56 + i) % 60:02d}:00"}))  # naive datetime
        else:
            shapes.append(with_created(s, None))
    await ingest_case("created_at_shapes", svc.NeuralGraphServiceConfig(), shapes, embeddings_for(shapes),
                      waves_for(shapes), out)

    # every episode a LoCoMo-format string (naive), all in one batch
    locomo_all = [with_created(s, {"text": "1:%02d pm on 8 May, 2023" % (i + 10)}) for i, s in enumerate(base)]
    await ingest_case("locomo_strings", svc.NeuralGraphServiceConfig(), locomo_all, embeddings_for(locomo_all),
                      waves_for(locomo_all), out)

    # ISO strings with offsets (aware)
    iso_all = [with_created(s, {"text": "2023-05-08T%02d:30:00+02:00" % (8 + i)}) for i, s in enumerate(base)]
    await ingest_case("iso_strings", svc.NeuralGraphServiceConfig(), iso_all, embeddings_for(iso_all),
                      waves_for(iso_all), out)

    # no timestamps at all: the frozen clock
    none_all = [with_created(s, None) for s in base]
    await ingest_case("no_created_at", svc.NeuralGraphServiceConfig(), none_all, embeddings_for(none_all),
                      waves_for(none_all), out)

    # a naive timestamp among aware ones: the temporal chain cannot order them
    mixed = [dict(s) for s in base]
    mixed[5] = with_created(mixed[5], {"dt": "2023-05-08T13:59:00"})
    await ingest_case("naive_among_aware", svc.NeuralGraphServiceConfig(), mixed, embeddings_for(mixed),
                      waves_for(mixed), out)

    # an impossible date string fails that episode only
    bad = [dict(s) for s in base]
    bad[3] = with_created(bad[3], {"text": "31 February 2023"})
    bad[7] = with_created(bad[7], {"text": "2023-05-08T25:00:00"})
    await ingest_case("bad_dates", svc.NeuralGraphServiceConfig(), bad, embeddings_for(bad), waves_for(bad), out)

    # missing / short / odd embeddings and waves
    emb = embeddings_for(base)
    await ingest_case("no_embeddings", svc.NeuralGraphServiceConfig(), base, None, None, out)
    await ingest_case("short_embeddings", svc.NeuralGraphServiceConfig(), base, emb[:5], waves_for(base)[:3], out)
    odd = [list(e) for e in emb]
    odd[2] = []
    await ingest_case("empty_embedding", svc.NeuralGraphServiceConfig(), base, odd, waves_for(base), out)
    await ingest_case("empty_batch", svc.NeuralGraphServiceConfig(), [], None, None, out)

    # metadata shapes: no filterable metadata, entity ids as non-lists, duplicated ids, producer-less
    meta = [dict(s) for s in base]
    meta[0]["entity_ids"] = None
    meta[1]["entity_ids"] = []
    meta[2]["entity_ids"] = ["Caroline", "Caroline", "ent:caroline"]
    meta[3]["producer_id"] = None
    meta[4]["importance_score"] = 0.95
    meta[5]["importance_score"] = 0.0
    meta[6]["entity_ids"] = ["Caroline", "Melanie"]
    meta[7]["entity_ids"] = ["Caroline"]
    await ingest_case("metadata_shapes", svc.NeuralGraphServiceConfig(), meta, embeddings_for(meta),
                      waves_for(meta), out)

    # one speaker answering another with whitespace only breaks the dialogue linker; the rest is unaffected
    blank = [dict(s) for s in base]
    blank[1] = dict(blank[1], content="   ", producer_id="Melanie")
    blank[0] = dict(blank[0], producer_id="Caroline")
    await ingest_case("whitespace_message", svc.NeuralGraphServiceConfig(), blank, embeddings_for(blank),
                      waves_for(blank), out)

    # a single episode: no chains, no linker, no semantic edges
    await ingest_case("single_episode", svc.NeuralGraphServiceConfig(), base[:1], embeddings_for(base[:1]),
                      waves_for(base[:1]), out)

    # entity edges: many nodes sharing entities across a long distance (decay below 0.1 drops edges)
    long = episode_specs(0, (1, 2, 3), 16)
    for i, s in enumerate(long):
        s["entity_ids"] = ["ent:shared"] if i % 4 == 0 else s["entity_ids"]
    await ingest_case("entity_decay", svc.NeuralGraphServiceConfig(auto_episode_segmentation=False), long,
                      embeddings_for(long), waves_for(long), out, edges_only=True)

    # identical contents are perfectly similar: semantic edges through the ingestion path
    twins = [dict(base[i % 3], uid=f"twin-{i}", created_at={"dt": f"2023-05-08T13:{10 + i}:00+00:00"})
             for i in range(7)]
    await ingest_case("semantic_twins", svc.NeuralGraphServiceConfig(), twins, embeddings_for(twins),
                      waves_for(twins), out)

    # same episodes ingested twice: existing entity / semantic edges are strengthened
    service = make(svc.NeuralGraphServiceConfig(dialogue_linking_enabled=False, auto_consolidation_on_ingestion=False))
    first = await service.process_episodes(episodes(base), SK, embeddings_for(base), waves_for(base))
    second = await service.process_episodes(episodes(base), SK, embeddings_for(base), waves_for(base))
    out["ingest_twice"] = {"first": ingest_rec(first), "second": ingest_rec(second),
                           "graph": await graph(service.storage), "digest": await digest(service.storage),
                           "totals": [service._total_nodes_created, service._total_edges_created]}

    # the helpers called directly
    service = make(svc.NeuralGraphServiceConfig(auto_consolidation_on_ingestion=False))
    r = await service.process_episodes(episodes(base), SK, embeddings_for(base), waves_for(base))
    nodes = r.nodes
    direct = {}
    direct["semantic_default"] = await service._create_semantic_edges_batch(nodes, SK)
    direct["semantic_low"] = await service._create_semantic_edges_batch(nodes, SK, 0.2, 2)
    direct["semantic_none"] = await service._create_semantic_edges_batch(nodes[:1], SK)
    direct["entity_edges"] = [await service._create_entity_edges(n, SK) for n in nodes]
    direct["graph"] = await graph(service.storage)
    out["direct"] = direct
    return out


async def scenario_consolidation():
    out = {}
    cons = ConsolidationConfig(promotion_heat_threshold=0.55, eviction_heat_threshold=0.35,
                               min_cycle_interval_seconds=0.0)
    specs = episode_specs(0, (1, 2), 14)

    # post-ingestion consolidation triggered by promotion / eviction candidates
    service = make(svc.NeuralGraphServiceConfig(consolidation_config=cons))
    out["ingest"] = await ingest_two_batches(service, specs, 14)
    out["graph_after_ingest"] = await graph(service.storage)
    out["stats_after_ingest"] = await service.get_statistics(SK)

    # manual cycles
    out["cycles"] = []
    for _ in range(2):
        r = await service.run_consolidation(SK)
        out["cycles"].append({"promoted": r.nodes_promoted, "evicted": r.nodes_evicted, "pruned": r.edges_pruned,
                              "merged": r.nodes_merged, "hubs": r.hubs_created, "session": r.session_key})
    out["digest_after_cycles"] = await digest(service.storage)

    # a default-interval service only runs one cycle per minute
    service = make(svc.NeuralGraphServiceConfig(auto_consolidation_on_ingestion=False))
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    first = await service.run_consolidation(SK)
    second = await service.run_consolidation(SK)
    out["interval"] = [asdict(first) | {"total_time_ms": None}, asdict(second) | {"total_time_ms": None}]

    # consolidation disabled
    service = make(svc.NeuralGraphServiceConfig(consolidation_enabled=False))
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    r = await service.run_consolidation(SK)
    out["disabled"] = {"result": asdict(r) | {"total_time_ms": r.total_time_ms == 0},
                       "boost": await service.boost_node_heat("x", 0.5), "decay": await service.decay_all_heat(SK),
                       "stats": await service.get_statistics(SK)}

    # exposed dynamics, heat and tuning on a normal service
    service = make(svc.NeuralGraphServiceConfig(auto_consolidation_on_ingestion=False))
    r = await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    nodes = r.nodes
    ids = [n.node_id for n in nodes]
    dyn = {}
    dyn["stdp"] = [await service.apply_stdp(ids[i], ids[i + 1], d) for i, d in enumerate([5.0, -5.0, 0.0, 30.0, -30.0])]
    dyn["stdp_missing"] = [await service.apply_stdp(ids[0], ids[5], 5.0), await service.apply_stdp("nope", "none", 1.0)]
    dyn["summation_unknown"] = await service.get_temporal_summation("nope")
    dyn["summation"] = [await service.get_temporal_summation(i) for i in ids[:6]]
    dyn["patterns"] = await service.analyze_temporal_patterns(SK)
    dyn["refractory_unknown"] = await service.get_refractory_state("nope")
    dyn["refractory_idle"] = await service.get_refractory_state(ids[0])
    nodes[1].activate(1.0)
    nodes[2].activate(0.5, 5000.0)
    await service.storage.update_node(nodes[1])
    dyn["refractory_active"] = [await service.get_refractory_state(ids[1]), await service.get_refractory_state(ids[2])]
    dyn["boost"] = [await service.boost_node_heat(ids[0], 0.5), await service.boost_node_heat(ids[0], 20.0),
                    await service.boost_node_heat("nope", 1.0), await service.boost_node_heat(ids[3])]
    dyn["decay_all"] = await service.decay_all_heat(SK)
    dyn["by_heat"] = [[(n.node_id, h) for n, h in await service.get_nodes_by_heat(SK, *args)]
                      for args in [(), (0.0, 10.0, 5), (0.4, 0.6, 50), (3.0, 10.0, 50), (-1.0, 100.0, 0)]]
    dyn["link"] = [await service.link_to_existing_memories(ids[0], [ids[5], "nope", ids[6]]),
                   await service.link_to_existing_memories(ids[1], [ids[7]], EdgeType.CAUSAL),
                   await service.link_to_existing_memories(ids[1], [])]
    dyn["episode_ids"] = service.get_episode_node_ids(SK)
    registry = {}
    for strictness in [None, "strict", "permissive", "balanced", "other"]:
        if strictness is None:
            registry["initial"] = asdict(service._gate_registry._config)
            continue
        service.set_gate_strictness(strictness)
        registry[strictness] = asdict(service._gate_registry._config)
    service.adapt_gate_threshold(100, 50)
    service.adapt_gate_threshold(10)
    registry["after_adapt"] = asdict(service._gate_registry._config)
    registry["retriever_registry_untouched"] = service._retriever._gates is not service._gate_registry
    dyn["gate"] = registry
    dyn["digest"] = await digest(service.storage)
    dyn["stats"] = await service.get_statistics(SK)
    out["dynamics"] = dyn

    # no temporal manager: the exposed temporal helpers fall back
    service = make(svc.NeuralGraphServiceConfig(temporal_enabled=False, auto_consolidation_on_ingestion=False))
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    nid = (await service.storage.get_nodes_by_session(SK))[0].node_id
    out["no_temporal"] = {"stdp": await service.apply_stdp(nid, nid, 1.0), "summation": await service.get_temporal_summation(nid),
                          "patterns": await service.analyze_temporal_patterns(SK),
                          "refractory": await service.get_refractory_state(nid)}

    # clear and close
    service = make(svc.NeuralGraphServiceConfig(auto_consolidation_on_ingestion=False))
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    await service.retrieve(QUESTIONS[0], fake_embedding(QUESTIONS[0], DIM), SK)
    out["cleared"] = await service.clear_session(SK)
    out["stats_cleared"] = await service.get_statistics(SK)
    out["cleared_again"] = await service.clear_session(SK)
    await service.close()
    out["closed"] = [service._closed, service._consolidation_task is None]
    await service.close()
    return out


def wave_specs(nodes):
    def emb(text):
        return fake_embedding(text, DIM)

    return [
        {"wave_id": "w-1", "content": "Caroline moved to Sweden four years ago.", "embedding": emb("Caroline moved to Sweden"),
         "amplitudes": {"entity": 0.9, "temporal": 0.4, "causal": 0.2}, "signals": [
             {"type": "temporal", "raw_expression": "four years ago", "resolved_date": "2019-05-08T00:00:00"},
             {"type": "temporal", "raw_expression": "next week", "resolved_date": None},
             {"type": "entity", "entity_name": "Caroline"},
             {"type": "entity", "entity_name": ""},
             {"type": "entity", "entity_name": "Sweden"},
             {"type": "relation"},
         ]},
        {"wave_id": "w-2", "content": "She cried because the adoption agency said no.",
         "embedding": nodes[3].embedding, "amplitudes": {"emotional": 0.8, "causal": 0.7}, "signals": [
             {"type": "causal", "cause": "agency said no", "effect": "she cried", "amplitude": 0.8,
              "embedding": nodes[3].embedding},
             {"type": "causal", "cause": "no money", "effect": "no adoption", "amplitude": 0.6,
              "embedding": nodes[4].embedding},
             {"type": "causal", "cause": "orphan cause", "effect": "", "amplitude": 0.5, "embedding": nodes[5].embedding},
             {"type": "causal", "cause": "far away", "effect": "unrelated", "amplitude": 0.4,
              "embedding": fake_embedding("zzzz qqqq xxxx", DIM)},
             {"type": "causal", "cause": "default amplitude", "effect": "no embedding"},
             {"type": "other"},
         ]},
        {"wave_id": "w-3", "content": "plain", "embedding": None, "amplitudes": None, "signals": []},
        {"wave_id": "w-4", "content": "A wave whose causal edge already exists", "embedding": nodes[3].embedding,
         "amplitudes": {"entity": 0.3}, "signals": [
             {"type": "causal", "cause": "c", "effect": "e", "amplitude": 0.9, "embedding": nodes[3].embedding}]},
    ]


def extraction_spec(node_ids):
    return {
        "entities": [
            {"uid": "ent:caroline", "name": "Caroline", "entity_type": "person", "mekb_score": 0.9},
            {"uid": "kg-1", "name": "Sweden", "entity_type": "location", "mekb_score": 0.7},
            {"uid": "kg-2", "name": "Adoption agency", "entity_type": "organization", "mekb_score": 0.4},
            {"uid": "kg-1", "name": "Sweden again", "entity_type": "location", "mekb_score": 0.1},
        ],
        "relations": [
            {"uid": "r-1", "source_entity_uid": "kg-1", "target_entity_uid": "kg-2", "relation_type": "related_to",
             "weight": 7.5, "confidence": 0.8},
            {"uid": "r-2", "source_entity_uid": "ent:caroline", "target_entity_uid": "kg-1",
             "relation_type": "lives_in", "weight": 10, "confidence": 0.95},
            {"uid": "r-3", "source_entity_uid": "kg-9", "target_entity_uid": "kg-1", "relation_type": "x",
             "weight": 1.0, "confidence": 0.1},
        ],
        "confidence": 0.85,
    }


async def scenario_wave_and_extraction():
    out = {}
    specs = episode_specs(0, (1,), 12)
    service = make(svc.NeuralGraphServiceConfig(auto_consolidation_on_ingestion=False))
    r = await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    nodes = r.nodes
    out["wave_specs"] = wave_specs(nodes)
    # a causal effect whose best match is similar, but below the 0.5 the service requires
    for spec in SPECS[30:]:
        emb = fake_embedding(spec["content"], DIM)
        best = await service.storage.vector_search(emb, SK, limit=3)
        if best and 0.42 <= best[0][1] < 0.49:
            out["wave_specs"].append({"wave_id": "w-5", "content": "borderline", "embedding": emb,
                                      "amplitudes": {"entity": 0.5}, "signals": [
                                          {"type": "causal", "cause": "c", "effect": "borderline", "amplitude": 0.7,
                                           "embedding": emb}]})
            break
    else:
        raise SystemExit("no borderline effect text found")
    out["waves"] = []
    for spec, uid in zip(out["wave_specs"], ["ep-1", None, "ep-3", None, "ep-5"]):
        node = await service.process_wave_memory(Wave(spec), SK, uid)
        out["waves"].append(None if node is None else slim_node(node))
    out["extraction_spec"] = extraction_spec([n.node_id for n in nodes])
    out["extraction"] = []
    for source in [None, nodes[0].node_id, ""]:
        out["extraction"].append(await service.process_extraction_result(extraction(out["extraction_spec"]), SK, source))
    out["graph"] = await graph(service.storage)
    out["totals"] = [service._total_nodes_created, service._total_edges_created]

    # disabled / no hierarchy
    service = make(svc.NeuralGraphServiceConfig(enabled=False))
    out["disabled"] = [await service.process_wave_memory(Wave(out["wave_specs"][0]), SK, None),
                       await service.process_extraction_result(extraction(out["extraction_spec"]), SK, None)]
    service = make(svc.NeuralGraphServiceConfig(hierarchy_enabled=False))
    out["no_hierarchy"] = [await service.process_wave_memory(Wave(out["wave_specs"][0]), SK, None),
                           await service.process_extraction_result(extraction(out["extraction_spec"]), SK, "src"),
                           await graph(service.storage)]
    return out


class Scripted(ExternalRetriever):
    """An external source returning scripted results (or raising)."""

    def __init__(self, name, results, error=None):
        self._name = name
        self.results = results
        self.error = error
        self.calls = []

    @property
    def source_name(self):
        return self._name

    async def retrieve(self, query, query_embedding=None, limit=5, timeout_ms=2000.0):
        self.calls.append([query, None if query_embedding is None else len(query_embedding), limit, timeout_ms])
        if self.error:
            raise RuntimeError(self.error)
        return list(self.results)

    async def is_available(self):
        return True


def ext_results(duplicate_of):
    return [
        ExternalResult(content="Paris is the capital and largest city of France.", source="scripted",
                       source_url="https://example.org/paris", title="Paris", relevance_score=0.9,
                       embedding=fake_embedding("What is the capital of France?", DIM)),
        ExternalResult(content="France is a country in Western Europe.", source="scripted", title="France",
                       relevance_score=0.6, embedding=fake_embedding("France country Europe", DIM)),
        ExternalResult(content="No embedding for this one, so only relevance counts.", source="scripted",
                       relevance_score=0.5),
        ExternalResult(content="A zero embedding is never normalised.", source="scripted", relevance_score=0.7,
                       embedding=[0.0] * DIM),
        ExternalResult(content=duplicate_of, source="scripted", title="duplicate", relevance_score=0.99,
                       embedding=fake_embedding(duplicate_of, DIM)),
        ExternalResult(content="Tiny relevance.", source="scripted", relevance_score=0.0,
                       embedding=fake_embedding("tiny", DIM)),
    ]


def ext_rec(r):
    return {"nodes": [[n.node_id, s, slim_node(n) if n.node_id.startswith("external_") else None] for n, s in r.nodes],
            "stages": r.stages_executed, "seen": r.total_candidates_seen, "co": r.co_activations_recorded}


async def scenario_external():
    out = {}
    specs = episode_specs(0, (1, 2), 14)
    questions = ["What is the capital of France?", "Who wrote Pride and Prejudice?", QUESTIONS[0], QUESTIONS[3],
                 "Define photosynthesis.", "What does Caroline think about Sweden and LGBTQ pride?"]
    tracer = ext_mod.get_open_domain_tracer()

    async def run(name, cfg, sources, qvec=None, limit=6):
        service = make(cfg)
        await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
        dup = specs[3]["content"]
        instances = [src(dup) for src in sources]
        for inst in instances:
            service._external_registry.register(inst, enabled=True)
        before = dict(tracer._stats)
        rows = []
        for q in questions:
            qe = fake_embedding(q, DIM) if qvec is None else qvec
            try:
                r = await service.retrieve(q, qe, SK, limit=limit)
                rows.append(ext_rec(r))
            except Exception as exc:  # noqa: BLE001
                rows.append({"error": f"{type(exc).__name__}: {exc}"})
        stats = tracer.get_stats()
        traces = tracer.get_recent_traces(len(questions)) if cfg.open_domain_tracing_enabled else None
        if traces:
            for t in traces:
                t["external_retrieval_latency_ms"] = None
        out[name] = {
            "rows": rows,
            "stats_delta": {k: stats[k] - before[k] for k in before},
            "traces": traces,
            "registry": sorted(service._external_registry._retrievers) if service._external_registry else None,
            "calls": [i.calls for i in instances],
        }
        return service

    forced = dict(external_retrieval_enabled=True, external_retrieval_min_session_hits=100)
    scripted = lambda dup: Scripted("scripted", ext_results(dup))  # noqa: E731
    await run("blend", svc.NeuralGraphServiceConfig(open_domain_tracing_enabled=True, **forced), [scripted])
    await run("weight", svc.NeuralGraphServiceConfig(external_result_weight=1.5, external_max_results=2, **forced),
              [scripted])
    await run("only_open_domain", svc.NeuralGraphServiceConfig(
        external_retrieval_enabled=True, external_retrieval_min_session_hits=0, open_domain_tracing_enabled=True),
        [scripted])
    await run("confidence", svc.NeuralGraphServiceConfig(
        external_retrieval_enabled=True, external_retrieval_min_session_hits=0,
        external_retrieval_confidence_threshold=0.0, open_domain_tracing_enabled=True), [scripted])
    await run("two_sources", svc.NeuralGraphServiceConfig(open_domain_tracing_enabled=True, **forced),
              [scripted, lambda dup: Scripted("other", [ExternalResult(content="Other source text.", source="other",
                                                                      relevance_score=0.8)])])
    await run("failing_source", svc.NeuralGraphServiceConfig(open_domain_tracing_enabled=True, **forced),
              [lambda dup: Scripted("broken", [], error="boom")])
    await run("empty_source", svc.NeuralGraphServiceConfig(open_domain_tracing_enabled=True, **forced),
              [lambda dup: Scripted("empty", [])])
    await run("no_sources", svc.NeuralGraphServiceConfig(open_domain_tracing_enabled=True, **forced), [])
    await run("untraced", svc.NeuralGraphServiceConfig(**forced), [scripted])
    await run("zero_query_embedding", svc.NeuralGraphServiceConfig(**forced), [scripted], qvec=[0.0] * DIM)
    await run("wrong_size_query", svc.NeuralGraphServiceConfig(**forced), [scripted], qvec=fake_embedding("x", 16))
    await run("electron", svc.NeuralGraphServiceConfig(use_electron_retriever=True, **forced), [scripted])
    wiki = await run("wikipedia_registry", svc.NeuralGraphServiceConfig(
        external_retrieval_enabled=True, wikipedia_enabled=True, wikipedia_extract_chars=300,
        external_retrieval_timeout_ms=500.0, external_retrieval_min_session_hits=-1,
        external_retrieval_confidence_threshold=2.0), [])
    reg = wiki._external_registry._retrievers["wikipedia"]
    out["wikipedia_registry"]["wikipedia_config"] = asdict(reg._config)

    # content duplicate check, called directly
    service = make(svc.NeuralGraphServiceConfig())
    pairs = [("", "x"), ("x", ""), ("   ", "a"), ("a b c d e", "a b c d e"), ("a b c d e", "A B C D E"),
             ("a b c d e", "a b c d f"), ("a b c d e", "a b c d e f"), ("a b c d e f g h i j", "a b c d e f g h i k"),
             ("Hello, world", "hello world"), ("x y", "y x"), ("a a a", "a"),
             ("a b c d", "a b c d e"), ("a b", "a b c d")]
    out["duplicates"] = [[a, b, service._is_content_duplicate(a, b), service._is_content_duplicate(a, b, 0.5)]
                         for a, b in pairs]
    return out


async def scenario_auto_consolidation():
    out = {}
    cons = ConsolidationConfig(promotion_heat_threshold=5.0, eviction_heat_threshold=0.99,
                               eviction_age_threshold_days=0.0, eviction_recency_threshold_days=0.0,
                               min_cycle_interval_seconds=0.0, recency_aware_retention=False,
                               factual_node_protection=False)
    specs = episode_specs(0, (1, 2), 14)
    service = make(svc.NeuralGraphServiceConfig(consolidation_config=cons, auto_consolidation_interval_seconds=0.02,
                                                auto_consolidation_on_ingestion=False))
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    before = (await service.get_statistics(SK))["consolidation"]["node_count"]
    await service.start_auto_consolidation(SK)
    running = service._consolidation_task is not None
    await service.start_auto_consolidation(SK)  # a second start is a no-op
    await asyncio.sleep(0.5)
    after = (await service.get_statistics(SK))["consolidation"]["node_count"]
    await service.stop_auto_consolidation()
    out["task"] = {"running_after_start": running, "task_after_stop": service._consolidation_task is None,
                   "nodes_before": before, "evicted_something": after < before, "nodes_after_positive": after >= 0}
    await service.stop_auto_consolidation()  # nothing to stop
    await service.close()
    out["closed"] = service._closed
    # starting after close: the loop ends at once
    await service.start_auto_consolidation(SK)
    await asyncio.sleep(0.05)
    out["after_close_done"] = service._consolidation_task.done()
    await service.stop_auto_consolidation()
    # no consolidation manager: the loop idles
    service = make(svc.NeuralGraphServiceConfig(consolidation_enabled=False, auto_consolidation_interval_seconds=0.01))
    await service.start_auto_consolidation(SK)
    await asyncio.sleep(0.05)
    out["idle_running"] = not service._consolidation_task.done()
    await service.close()
    out["idle_closed"] = service._consolidation_task is None
    return out


async def scenario_rng():
    out = {}
    specs = episode_specs(0, (1, 2), 14)
    for name, p, seed in [("p05_seed7", 0.5, 7), ("p05_seed99", 0.5, 99), ("p01_seed3", 0.1, 3), ("p1", 1.0, 5),
                          ("p0", 0.0, 5)]:
        service = make(svc.NeuralGraphServiceConfig(temporal_dynamics_probability=p, query_routing_enabled=False,
                                                    dialogue_linking_enabled=False), seed=seed)
        await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
        sums = []
        for q in QUESTIONS[:12]:
            await service.retrieve(q, fake_embedding(q, DIM), SK, limit=6)
            edges = await service.storage.get_all_edges(SK)
            sums.append([sum(e.ltp_boost for e in edges), sum(1 for e in edges if e.ltp_boost > 1.0)])
        out[name] = {"ltp": sums}
    return out


async def scenario_stdp():
    """Episodes 25 ms apart, so that the STDP the retrieval applies to co-retrieved neighbours (a function of the
    milliseconds between their creation times) does not vanish, as it does with LoCoMo's minute spacing."""
    out = {}
    specs = episode_specs(0, (1,), 12)
    fast = [dict(s, created_at={"dt": "2023-05-08T13:56:00.%06d+00:00" % (i * 25000)}) for i, s in enumerate(specs)]
    service = make(svc.NeuralGraphServiceConfig(
        temporal_dynamics_probability=0.5, query_routing_enabled=False, dialogue_linking_enabled=False,
        auto_consolidation_on_ingestion=False), seed=11)
    r = await service.process_episodes(episodes(fast), SK, embeddings_for(fast), waves_for(fast))
    out["ingest"] = ingest_rec(r)
    out["fast"] = fast
    out["questions"] = await run_questions(service, QUESTIONS[:8], 6, wave_variants=[None])
    out["digest"] = await digest(service.storage)
    return out


async def scenario_retrieval_errors():
    """Naive and aware creation times in one result: STDP subtracts them outside any try."""
    out = {}
    specs = episode_specs(0, (1,), 12)
    service = make(svc.NeuralGraphServiceConfig(query_routing_enabled=False, dialogue_linking_enabled=False,
                                                auto_temporal_linking=False))
    r = await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    r.nodes[2].created_at = r.nodes[2].created_at.replace(tzinfo=None)
    await service.storage.update_node(r.nodes[2])
    out["rows"] = await run_questions(service, QUESTIONS[:6], 8, wave_variants=[None])
    out["totals"] = [service._total_retrievals]
    # without temporal dynamics the same retrieval succeeds
    service = make(svc.NeuralGraphServiceConfig(query_routing_enabled=False, dialogue_linking_enabled=False,
                                                auto_temporal_linking=False, apply_temporal_dynamics=False))
    r = await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    r.nodes[2].created_at = r.nodes[2].created_at.replace(tzinfo=None)
    await service.storage.update_node(r.nodes[2])
    out["rows_no_dynamics"] = await run_questions(service, QUESTIONS[:6], 8, wave_variants=[None])
    # retrieve_with_context
    service = make(svc.NeuralGraphServiceConfig())
    await service.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    ctx = []
    for q in QUESTIONS[:5]:
        c = await service.retrieve_with_context(q, fake_embedding(q, DIM), SK, 5)
        c = dict(c, total_time_ms=None)
        ctx.append(_ctx_json(c))
    out["context"] = ctx
    empty = make(svc.NeuralGraphServiceConfig())
    c = await empty.retrieve_with_context("anything", fake_embedding("anything", DIM), SK, 5)
    out["context_empty"] = _ctx_json(dict(c, total_time_ms=None))
    nohier = make(svc.NeuralGraphServiceConfig(hierarchy_enabled=False, temporal_enabled=False))
    await nohier.process_episodes(episodes(specs), SK, embeddings_for(specs), waves_for(specs))
    c = await nohier.retrieve_with_context("anything", fake_embedding("anything", DIM), SK, 5)
    out["context_bare"] = _ctx_json(dict(c, total_time_ms=None))
    return out


def _ctx_json(c):
    def scored(pairs):
        return [[n.node_id, s] for n, s in pairs]

    tc = c["temporal_context"]
    return {"nodes": scored(c["nodes"]), "stages": c["stages_executed"],
            "temporal": {k: scored(v) for k, v in tc.items()} if tc else {},
            "hierarchy": c["hierarchy_summary"]}


def dump_compact(name, obj):
    """One line per top-level key, compact values: the fixture would be twice the size with ``R.dump``'s
    indentation."""
    from pathlib import Path
    lines = [f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in obj.items()]
    path = R.HERE / name
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"wrote {path.relative_to(R.ROOT)} ({path.stat().st_size // 1024} KiB)")


async def main():
    out = {"dim": DIM, "session_key": SK, "questions": QUESTIONS, "wave_variants": [waves_json(w) for w in WAVE_VARIANTS],
           "episodes": SPECS,
           "spec_sets": {"s1_12": episode_specs(0, (1,), 12), "s12_14": episode_specs(0, (1, 2), 14)}}
    out["configs"] = {"default": config_dict(svc.NeuralGraphServiceConfig()),
                      "single_hop": config_dict(svc.create_single_hop_optimized_config()),
                      "multi_hop": config_dict(svc.create_multi_hop_optimized_config())}
    out["default"] = await scenario_main(svc.NeuralGraphServiceConfig(), True)
    out["single_hop"] = await scenario_main(svc.create_single_hop_optimized_config(), False)
    out["multi_hop"] = await scenario_main(svc.create_multi_hop_optimized_config(), False)
    out["variants"] = await scenario_variants()
    out["routing"] = await scenario_routing()
    out["ingestion"] = await scenario_ingestion()
    out["consolidation"] = await scenario_consolidation()
    out["wave_memory"] = await scenario_wave_and_extraction()
    out["external"] = await scenario_external()
    out["auto_consolidation"] = await scenario_auto_consolidation()
    out["rng"] = await scenario_rng()
    out["stdp"] = await scenario_stdp()
    out["errors"] = await scenario_retrieval_errors()
    dump_compact("service.json", out)


if __name__ == "__main__":
    asyncio.run(main())
