#!/usr/bin/env python3
"""Fixtures for ``retrieval/retriever.py`` and ``retrieval/flash_retriever.py`` over a LoCoMo graph
(``_retrievers.build_graph``): the query-analysis helpers on LoCoMo questions and edge cases, ``NeuralRetriever``
runs with four configurations x questions x wave amplitudes (ranked nodes and scores, stages, candidate counts,
co-activations, and the ``NameError`` temporal queries raised), the temporal branch of ``_fast_recall`` pinned by
defining its two missing names as module globals, the specialised retrievers, the error paths (non-string
``producer_id``, naive timestamps), ``FlashRetriever`` scores for every node, ``HybridFlashRetriever`` runs, and
the mutated graph. Writes ``retrievers.json``."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import _engine as E
import _reference as R
import _retrievers as T

import NeuralGraph.research.retrieval.flash_retriever as flash_mod  # noqa: E402
import NeuralGraph.research.retrieval.retriever as retriever_mod  # noqa: E402

T.install([retriever_mod, flash_mod])

from NeuralGraph.chat_memory.llm import fake_embedding  # noqa: E402
from NeuralGraph.research.retrieval.data_types import (  # noqa: E402
    NeuralNode, NodeLayer, RetrievalStageConfig, EdgeType)
from NeuralGraph.research.retrieval.flash_retriever import (  # noqa: E402
    FlashConfig, FlashRetriever, HybridFlashRetriever)
from NeuralGraph.research.retrieval.gating import create_permissive_registry  # noqa: E402
from NeuralGraph.research.retrieval.hierarchy import HierarchyManager  # noqa: E402
from NeuralGraph.research.retrieval.retriever import NeuralRetriever, RetrieverConfig  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402

DIM = E.DIM
QA = T.E.json.loads((R.DATASETS / "locomo10.json").read_text(encoding="utf-8"))[0]["qa"]
QUESTIONS = [QA[i]["question"] for i in [0, 1, 3, 4, 6, 9, 12, 15]] + [
    "What does Caroline think about Sweden and LGBTQ pride?"]
WAVES = [
    None,
    {},
    {"entity": 0.8, "action": 0.5, "emotional": 0.2},
    {"entity": 0.6, "relational": 0.45, "causal": 0.5},
    {"temporal": 0.7, "entity": 0.6},
    {"temporal": 0.2, "spatial": 0.9, "state": 0.3},
    {"entity": 0.9, "temporal": 0.35, "__suppress_multi_hop": True},
]
EDGE_TEXTS = [
    "On 2023-02-30 and 2023/13 nothing happened", "Meet me 2023-5-8 or 2023/05", "In june 2023 we met",
    "In 2019, 2023 and 2031 something", "It cost $1,500.50 over 3 years and 2 weeks, 5km away, 3rd time",
    "LGBTQ+ self-portrait of Caroline's \"Nothing Is Impossible\" and 'Becoming Nicole' book",
    "naïve café Ünïcode Straße ÅNGSTRÖM", "Did Melanie go to Paris With Oliver?", "", "x",
    "queue relationship fixation happiness friendship expression", "What did Mel and Caroline do on 7 May 2023?",
    "MARCH 2024 marks the month", "septiembre 2023", "2023-12-31T23:59 deadline",
]
RESOLVED = ["2023-05-08", "2023-05-08T13:56:00+02:00", "20230508", "May 2023", "8 May 2023", "2023", "0000",
            "31 February 2023", "Mayo 2023", "  2023", "2023-W19-1", None, 5, "13 june 2024", "0 May 2023",
            "1 Smarch 2023", "2023-05-08 10:00", "2023W191", "2023-W19", "2020-W53-7", "2023-W53-1", "2023-05-08T10:00Z",
            "20230508T1000Z", "2023-05-08Z", "2023-05-08T24:00", "2023-W19-1T10:00Z", "2023-05-08T10:00:00.1234Z"]


def iso(d):
    return None if d is None else d.isoformat()


def analysis(nodes):
    r = NeuralRetriever(InMemoryNeuralGraphStorage())
    f = FlashRetriever(InMemoryNeuralGraphStorage())
    texts = QUESTIONS + [q["question"] for q in QA[20:40]] + EDGE_TEXTS
    out = {"texts": texts, "waves": [T.jsonable_waves(w) for w in WAVES]}
    rows = []
    for t in texts:
        tokens, target = r._extract_temporal_tokens(t)
        kw = r._extract_keywords(t)
        specifics = f._extract_specifics(t)
        rows.append({
            "entities": r._extract_entities(t), "keywords": sorted(kw), "flash_keywords": sorted(f._extract_keywords(t)),
            "specifics": sorted(specifics), "temporal_tokens": sorted(tokens), "target_date": iso(target),
            "keyword_scores": [r._compute_keyword_score(n.content, kw) for n in nodes[::5]],
            "specificity": [f._compute_specificity_bonus(specifics, n.content) for n in nodes[::5]],
        })
    out["rows"] = rows
    wave_list = [w for w in WAVES if w] + [n.wave_amplitudes for n in nodes[:12]] + [{"entity": -0.5, "x": 0.0}]
    out["wave_list"] = [T.jsonable_waves(w) for w in wave_list]
    out["alignment"] = [[r._compute_wave_alignment(a, b) for b in wave_list] for a in wave_list]
    out["flash_alignment"] = [[f._compute_wave_alignment(a, b) for b in wave_list[:6]] for a in wave_list]
    out["factual"] = [r._is_factual_query(w) for w in WAVES]
    dates = []
    for resolved in RESOLVED:
        n = NeuralNode(node_id="d", layer=NodeLayer.MESSAGE, content="x",
                       created_at=datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc),
                       metadata={} if resolved is None else {"resolved_date": resolved})
        try:
            dates.append({"ok": [iso(r._parse_node_date(n)), iso(f._parse_node_date(n))]})
        except Exception as exc:  # noqa: BLE001
            dates.append({"error": f"{type(exc).__name__}: {exc}"})
    out["resolved"] = RESOLVED
    out["parse_node_date"] = dates
    return out


def custom_stages():
    return [
        RetrievalStageConfig(name="recall", max_candidates=40, edge_types=[], layer_filter=[NodeLayer.MESSAGE],
                             max_hops=0, score_threshold=0.1, use_vector_search=True),
        RetrievalStageConfig(name="everything", max_candidates=60, edge_types=[], layer_filter=None, max_hops=2,
                             score_threshold=0.05),
        RetrievalStageConfig(name="semantic_only", max_candidates=7, edge_types=[EdgeType.SEMANTIC],
                             layer_filter=[NodeLayer.EPISODE], max_hops=4, score_threshold=0.01),
    ]


async def main():
    out = {}
    specs = T.message_specs()
    out["specs"] = specs
    st = InMemoryNeuralGraphStorage()
    h, tm, msgs, episodes, topics = await T.build_graph(st, specs, "r1")
    out["episodes"], out["topics"] = E.ids(episodes), E.ids(topics)
    out["analysis"] = analysis(msgs)

    retrievers = {
        "default": NeuralRetriever(st),
        "temporal": NeuralRetriever(st, temporal_manager=tm, config=RetrieverConfig(final_limit=12)),
        "plain": NeuralRetriever(st, config=RetrieverConfig(use_pattern_completion=False,
                                                            use_wavefront_propagation=False, use_keyword_boost=False,
                                                            record_co_activations=False)),
        "custom": NeuralRetriever(st, gate_registry=create_permissive_registry(),
                                  config=RetrieverConfig(stages=custom_stages(), hop_decay_factor=0.7,
                                                         min_score_threshold=0.5, final_limit=15)),
        "empty_stages": NeuralRetriever(st, config=RetrieverConfig(stages=[], rerank_by_heat=False,
                                                                   use_wave_amplitudes=False)),
    }
    out["custom_config_stages"] = [s.to_dict() for s in retrievers["custom"]._config.stages]
    out["default_config_stages"] = [s.to_dict() for s in retrievers["empty_stages"]._config.stages]
    layer_filters = {None: None, "msg": [NodeLayer.MESSAGE], "ep_topic": [NodeLayer.EPISODE, NodeLayer.TOPIC]}
    calls = []
    for name in ["default", "plain"]:
        for qi in range(len(QUESTIONS)):
            for wi in [0, 2, 3, 5]:
                calls.append({"r": name, "q": qi, "w": wi, "limit": None, "layers": None})
    for name in ["temporal", "custom", "empty_stages"]:
        for qi in range(4):
            for wi in [0, 1, 2, 4, 6]:
                calls.append({"r": name, "q": qi, "w": wi, "limit": None, "layers": None})
    calls += [{"r": "default", "q": 8, "w": 2, "limit": 5, "layers": "msg"},
              {"r": "default", "q": 8, "w": 0, "limit": 0, "layers": "ep_topic"},
              {"r": "temporal", "q": 2, "w": 3, "limit": 30, "layers": "ep_topic"}]
    for c in calls:
        r = retrievers[c["r"]]
        q = QUESTIONS[c["q"]]
        c["result"] = await T.call(lambda r=r, q=q, c=c: r.retrieve(
            q, fake_embedding(q, DIM), "r1", limit=c["limit"], layer_filter=layer_filters[c["layers"]],
            query_wave_amplitudes=WAVES[c["w"]]))
    out["calls"] = calls

    # the temporal branch of _fast_recall, with its two names defined as module globals
    names_runs = []
    for text in ["When did Caroline go to the LGBTQ support group in May 2023?", "Plans for 2023-05-25 at noon"]:
        tokens, target = retriever_mod.NeuralRetriever(st)._extract_temporal_tokens(text)
        retriever_mod.query_temporal_tokens, retriever_mod.query_target_date = tokens, target
        for name in ["default", "temporal", "custom"]:
            for qi in [0, 1, 8]:
                for wi in [4, 6]:
                    q = QUESTIONS[qi]
                    r = retrievers[name]
                    names_runs.append({"text": text, "r": name, "q": qi, "w": wi, "result": await T.call(
                        lambda r=r, q=q, wi=wi: r.retrieve(q, fake_embedding(q, DIM), "r1",
                                                           query_wave_amplitudes=WAVES[wi]))})
    del retriever_mod.query_temporal_tokens, retriever_mod.query_target_date
    out["names_runs"] = names_runs

    d, t = retrievers["default"], retrievers["temporal"]
    out["by_entities"] = [E.scored(await d.retrieve_by_entities(ents, "r1", limit=lim)) for ents, lim in [
        (["Caroline"], 20), (["Caroline", "ent:art"], 20), (["ent:kids", "ent:family", "ent:kids"], 5),
        ([], 20), (["Nobody"], 20), (["Melanie", "ent:support", "Caroline"], 8)]]
    ctx = []
    for node in [msgs[5], msgs[0], msgs[-1], episodes[0]]:
        for b, a in [(3, 3), (1, 5), (0, 0)]:
            for r in [d, t]:
                c = await r.retrieve_temporal_context(node, before_count=b, after_count=a)
                ctx.append({"before": E.scored(c["before"]), "after": E.scored(c["after"])})
    out["temporal_context"] = ctx
    hier = []
    for qi in [0, 3, 8]:
        q = QUESTIONS[qi]
        for layer in [NodeLayer.MESSAGE, NodeLayer.EPISODE, NodeLayer.TOPIC, NodeLayer.PERSONA]:
            for lim in [4, 10]:
                hier.append(E.scored(await d.retrieve_with_hierarchy(fake_embedding(q, DIM), "r1",
                                                                      target_layer=layer, limit=lim)))
    out["hierarchy"] = hier
    exp = []
    for qi, rounds, lim in [(0, 0, 5), (1, 1, 8), (8, 2, 6), (4, 3, 4), (2, -1, 3)]:
        q = QUESTIONS[qi]
        exp.append(await T.call(lambda q=q, rounds=rounds, lim=lim: d.retrieve_with_expansion(
            q, fake_embedding(q, DIM), "r1", expansion_rounds=rounds, limit=lim)))
    out["expansion"] = exp
    out["record_one"] = await d._record_co_activations([msgs[0]])

    # flash and hybrid over the (now mutated) graph
    every = FlashRetriever(st, FlashConfig(min_score=-1.0))
    flash = FlashRetriever(st)
    flash_runs = []
    for qi in range(len(QUESTIONS)):
        for wi in range(len(WAVES)):
            q = QUESTIONS[qi]
            flash_runs.append({"q": qi, "w": wi, "every": await T.call(lambda q=q, wi=wi: every.retrieve(
                q, fake_embedding(q, DIM), "r1", limit=1000, query_wave_amplitudes=WAVES[wi])),
                "default": await T.call(lambda q=q, wi=wi: flash.retrieve(
                    q, fake_embedding(q, DIM), "r1", query_wave_amplitudes=WAVES[wi]))})
    out["flash_runs"] = flash_runs
    out["flash_no_embedding"] = await T.call(lambda: every.retrieve(QUESTIONS[0], [], "r1", limit=1000))
    hybrids = {"default": HybridFlashRetriever(st),
               "custom": HybridFlashRetriever(st, FlashConfig(final_limit=7, semantic_weight=0.6, min_score=0.3,
                                                              speaker_match_boost=0.5))}
    hybrid_runs = []
    for name, hy in hybrids.items():
        for qi in range(len(QUESTIONS)):
            for wi in [0, 2, 4, 6]:
                q = QUESTIONS[qi]
                hybrid_runs.append({"h": name, "q": qi, "w": wi, "result": await T.call(
                    lambda hy=hy, q=q, wi=wi: hy.retrieve(q, fake_embedding(q, DIM), "r1",
                                                          query_wave_amplitudes=WAVES[wi]))})
    hybrid_runs.append({"h": "default", "q": -1, "w": 0, "result": await T.call(
        lambda: hybrids["default"].retrieve("what happened yesterday?", [], "r1"))})
    hybrid_runs.append({"h": "default", "q": -2, "w": 2, "result": await T.call(
        lambda: hybrids["default"].retrieve("what happened yesterday?", fake_embedding("yesterday", DIM), "r1",
                                            limit=3, query_wave_amplitudes=WAVES[2]))})
    out["hybrid_runs"] = hybrid_runs

    # error paths on small separate sessions
    st2 = InMemoryNeuralGraphStorage()
    h2 = HierarchyManager(st2)
    bad = [await h2.create_message_node(content=c, session_key="bad", embedding=fake_embedding(c, DIM),
                                        wave_amplitudes={"entity": 0.7}, entity_ids=["Caroline"],
                                        created_at=datetime(2023, 5, 8, 13, 0, tzinfo=timezone.utc), **meta)
           for c, meta in [("Caroline went to the support group", {"producer_id": 7}),
                           ("Caroline painted a lake", {"speaker": 7}),
                           ("Melanie likes pottery", {"speaker": "Melanie"})]]
    naive = [await h2.create_message_node(content=c, session_key="naive", embedding=fake_embedding(c, DIM),
                                          wave_amplitudes={"temporal": 0.5}, created_at=datetime(2023, 5, 8, 13, i))
             for i, c in enumerate(["Caroline went camping", "Melanie went camping too", "camping is fun"])]
    q = "What did Caroline do at the support group camping?"
    errs = {}
    errs["retrieve_bad"] = await T.call(lambda: NeuralRetriever(st2).retrieve(q, fake_embedding(q, DIM), "bad"))
    errs["retrieve_bad_waves"] = await T.call(lambda: NeuralRetriever(st2).retrieve(
        q, fake_embedding(q, DIM), "bad", query_wave_amplitudes=WAVES[2]))
    errs["retrieve_naive"] = await T.call(lambda: NeuralRetriever(st2).retrieve(
        q, fake_embedding(q, DIM), "naive", query_wave_amplitudes=WAVES[2]))
    errs["retrieve_naive_nowave"] = await T.call(lambda: NeuralRetriever(st2).retrieve(
        q, fake_embedding(q, DIM), "naive"))
    errs["flash_bad"] = await T.call(lambda: FlashRetriever(st2).retrieve(q, fake_embedding(q, DIM), "bad"))
    errs["flash_bad_noentity"] = await T.call(lambda: FlashRetriever(st2).retrieve(
        "what about pottery", fake_embedding(q, DIM), "bad"))
    errs["flash_naive"] = await T.call(lambda: FlashRetriever(st2).retrieve(
        q, fake_embedding(q, DIM), "naive", query_wave_amplitudes=WAVES[5]))
    errs["flash_naive_nowave"] = await T.call(lambda: FlashRetriever(st2).retrieve(
        q, fake_embedding(q, DIM), "naive"))
    errs["hybrid_bad"] = await T.call(lambda: HybridFlashRetriever(st2).retrieve(
        "Tell me about Caroline", fake_embedding("lake", DIM), "bad"))
    out["errors"] = errs
    out["bad_ids"], out["naive_ids"] = E.ids(bad), E.ids(naive)
    out["graph"] = await E.dump_graph(st, "r1")
    out["graph_bad"] = await E.dump_graph(st2, "bad")
    R.dump("retrievers.json", out)


asyncio.run(main())
