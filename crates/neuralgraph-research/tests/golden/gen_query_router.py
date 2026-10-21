#!/usr/bin/env python3
"""Fixtures for ``retrieval/query_router.py``: the fuzzy/alias matchers, ``QueryRouter.analyze`` over every LoCoMo
question (known entities = the two speakers) plus edge cases, and ``FilteredRetriever`` in hard/soft/boost mode over
a real conversation's nodes. Writes ``query_router.json``.

Python kept ``known_entities`` and the de-duplicated ``rare_terms`` in a ``set``, whose iteration order was
randomised per process. The generator replaces ``set`` inside the module with an insertion-ordered set so the
recorded order is the one the Rust port (``IndexSet``) defines.
"""
from __future__ import annotations

import asyncio
import json
import re

import _reference as R

import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.query_router as qr
import NeuralGraph.research.retrieval.storage as storage_mod

R.freeze_now([dt_mod, storage_mod])
from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer  # noqa: E402
from NeuralGraph.research.retrieval.storage import InMemoryNeuralGraphStorage  # noqa: E402


class OrderedSet:
    """Insertion-ordered stand-in for ``set`` (only what query_router uses)."""

    def __init__(self, items=()):
        self._d = dict.fromkeys(items)

    def add(self, x):
        self._d[x] = None

    def update(self, items):
        for x in items:
            self._d[x] = None

    def __iter__(self):
        return iter(self._d)

    def __contains__(self, x):
        return x in self._d

    def __len__(self):
        return len(self._d)


qr.set = OrderedSet

LOCOMO = json.loads((R.DATASETS / "locomo10.json").read_text())


def analysis_row(a):
    """QueryAnalysis without query_text, as a compact list (field order of the dataclass)."""
    return [a.query_type.value, a.entities, a.primary_subject, a.speaker_filter, a.temporal_markers,
            a.temporal_direction, a.has_negation, a.confidence, a.strategy, a.filter_params, a.rare_terms,
            a.is_open_domain, a.open_domain_confidence, a.is_list_question, a.list_question_type,
            a.suppress_multi_hop]


def sfr(r):
    return [r.node_id, r.score_modifier, r.match_reason, r.is_speaker_match, r.is_content_match,
            r.is_entity_id_match, r.is_fuzzy_match, r.boost_applied]


WORDS = ["Caroline", "Carol", "Carolyn", "carrie", "Melanie", "Mel", "Melany", "Jon", "Jonathan", "nathan",
         "John", "Johnny", "Kate", "Katie", "katherine", "catherine", "Bob", "Robert", "Rob", "Liz", "", "a",
         "ab", "Évelyne", "evelyne", "Gina", "Jean", "Joanna", "Nate", "Tim", "Timothy", "Deborah", "Debra",
         " Caroline ", "Andrew", "Drew", "pottery", "potter", "sweden", "swedish", "Calvin", "Dave", "David"]


def matchers():
    pairs = [(a, b) for a in WORDS for b in WORDS]
    return {
        "edit_distance": [[a, b, qr._compute_edit_distance(a, b)] for a, b in pairs],
        "fuzzy": [[a, b, t, *qr._fuzzy_match(a, b, t)] for a, b in pairs for t in (0.7, 0.5)],
        "alias": [[a, b, *qr._is_alias_match(a, b)] for a, b in pairs],
    }


def match_entity_cases():
    contents = ["", "I went to the pottery class with Carolyn yesterday!", "caroline said hi",
                "Melany and Jonathon went hiking 🧘‍♀️ today", "nothing relevant here", "Carolline's bday",
                "met Carolinn\u0301 and Jonathn\u200dx and mel\ufe0f today"]
    speakers = [None, "", "Caroline", "Carol", "Caro", "Melanie", "Mel", "Jonathan", "Bob"]
    id_lists = [None, [], ["Sweden", "Jon"], ["Carolyn"], ["katie", "Robert"], ["Carrie"]]
    entities = ["Caroline", "carol", "Mel", "Jon", "Robert", "Kate", "Sweden", "pottery", "Zed", "Carrie"]
    out = []
    for e in entities:
        for s in speakers:
            for ids in id_lists:
                for c in contents:
                    r = qr._match_entity(e, s, c, ids, 0.7)
                    out.append([e, s, c, ids, sfr(r)])
    return out


EDGE_QUERIES = [
    "What is Caroline's job?", "what is caroline's job", "When did Melanie go to New York last week?",
    "Did John Smith not attend the meeting?", "What does the person who mentioned \"Charlotte's Web\" like?",
    "What is photosynthesis?", "Define gravity", "How does a vaccine work?", "What books has Melanie read?",
    "What do Caroline and Melanie have in common?", "Which movies is she watching?", "How many kids does Mel have?",
    "Did Caroline say anything about 'the parade'?", "What did the friend named bob tell Caroline on 5/12/2023?",
    "Who is Dr. smith?", "What is the NASA budget of 20kg or 3.5 MPH or 40%?", "  What about Sweden?  ",
    "Has Caroline ever painted a sunset?", "According to Melanie, what happened after the trip?",
    "List all the hobbies of Caroline", "Is Melanie a teacher?", "Why did Caroline move from Sweden in 2019?",
    "What is the population of France?", "When was the telephone invented?", "Where is the Eiffel Tower located?",
    "What happened to Jon in the Summer of 2021?", "Between Caroline and Melanie, who is older?",
    "Does Caroline like pottery?", "What did Mrs. Robinson make?", "", "Bob", "I don't know what Évelyne said",
    "WHAT IS CAROLINE'S FAVORITE COLOR?", "The person who visited Paris - who is it?",
    "What are the implementations of photosynthesis and evolution in biology?",
    "How long has Caroline been practicing art since 2016?", "Did they both go camping yesterday?",
    "What kinds of music does the user Kate enjoy?", "Which items has John Smith bought?",
    "How old is Melanie's daughter?", "Where does Jon work?", "What is a black hole?",
    "Who was the first president of the United States?", "What year did The Beatles break up?",
    "Who called Caroline?", "what has caroline painted", "Caroline's new job: what is it?",
    "What did Melanie\u0301 say about Sweden\u200d?", "Is Kate\ufe0f friends with Jon\u0301?",
]


def analyze_all():
    convs = []
    for conv in LOCOMO:
        c = conv["conversation"]
        router = qr.QueryRouter(OrderedSet([c["speaker_a"], c["speaker_b"]]))
        convs.append({"sample_id": conv.get("sample_id"),
                      "rows": [analysis_row(router.analyze(q["question"])) for q in conv["qa"]]})
    known = ["Caroline", "Melanie", "John Smith", "New York", "Kate"]
    router = qr.QueryRouter(OrderedSet(known))
    edge = [[q, analysis_row(router.analyze(q))] for q in EDGE_QUERIES]
    bare = qr.QueryRouter()
    edge_no_known = [[q, analysis_row(bare.analyze(q))] for q in EDGE_QUERIES]
    return {"locomo": convs, "edge_known": known, "edge": edge, "edge_no_known": edge_no_known}


CAP = re.compile(r"\b[A-Z][a-z]+\b")


def build_nodes():
    """Nodes from the first two sessions of conversation 0, plus hand-made nodes for the fuzzy branches."""
    c = LOCOMO[0]["conversation"]
    turns = c["session_1"] + c["session_2"]
    nodes = []
    for i, t in enumerate(turns):
        meta = {"speaker": t["speaker"]}
        if i % 3 == 0:
            meta["timestamp"] = "2023-05-08T13:56:00"
        if i % 7 == 0:
            meta = {"producer_id": t["speaker"]}
        if i % 11 == 0:
            meta = {}
        ids = [w for w in dict.fromkeys(CAP.findall(t["text"])) if w != t["speaker"]]
        nodes.append(NeuralNode(node_id=f"n{i:03d}", layer=NodeLayer.MESSAGE, content=t["text"], session_key="s0",
                                entity_ids=ids, metadata=meta))
    extra = [
        ("Carol", "I finally finished my pottery bowl last week", [], {"speaker": "Carol"}),
        ("Mel", "We went camping yesterday", ["Carrie"], {"speaker": "Mel", "timestamp": "x"}),
        ("Carolyn", "Carolyn here", ["Sweden"], {"speaker": "Carolyn"}),
        ("", "Melany told me about the trip in May", ["Jon"], {"producer_id": "Bob"}),
        ("", "", [], {"timestamp": ""}),
    ]
    for j, (_, text, ids, meta) in enumerate(extra):
        nodes.append(NeuralNode(node_id=f"x{j}", layer=NodeLayer.MESSAGE, content=text, session_key="s0",
                                entity_ids=ids, metadata=meta))
    return nodes


def node_json(n):
    return {"node_id": n.node_id, "content": n.content, "entity_ids": n.entity_ids, "metadata": n.metadata}


FILTER_QUERIES = [q["question"] for q in LOCOMO[0]["qa"][:24]] + [
    "What did Carol say about pottery?", "When did Mel go camping?", "What is Carrie's job?",
    "What did the person who mentioned the trip like?", "Did Caroline not go?", "What is photosynthesis?",
    "When did Zed visit Sweden?", "What does Jon do?", "Who is Bob's friend?",
]

DIRECT_PARAMS = [
    {}, {"entity_filter": "Caroline"}, {"entity_filter": "Carol", "speaker_filter": "Melanie"},
    {"entity_filter": "Mel", "speaker_filter": "Mel"}, {"entity_filter": "Zed"}, {"entity_filter": "pottery"},
    {"entity_filter": "Jon"}, {"entity_filter": "Sweden", "speaker_filter": "Caroline"},
    {"temporal_markers": ["yesterday", "last week", "may"]}, {"temporal_markers": [], "entity_filter": "Melanie"},
    {"temporal_markers": ["when", "ago", "may", "2023"], "entity_filter": "Carrie"},
    {"temporal_markers": ["zzz"]},
]


def filtered():
    nodes = build_nodes()
    out = {"nodes": [node_json(n) for n in nodes], "runs": [], "direct": []}
    for mode in ["boost", "soft", "hard", "other"]:
        fr = qr.FilteredRetriever(storage=None, filter_mode=mode)
        fr.update_context(nodes)
        rows = []
        for q in FILTER_QUERIES:
            f, a, soft = asyncio.run(fr.get_filtered_candidates(q, "s0", base_candidates=nodes))
            rows.append([q, [n.node_id for n in f], a.strategy,
                         None if soft is None else [[k, sfr(v)] for k, v in soft.items()]])
        out["runs"].append({"mode": mode, "known": list(fr.router.known_entities), "rows": rows,
                            "stats": fr.get_filter_statistics()})
    # direct calls of each filter with fixed params (fallback paths included)
    fr = qr.FilteredRetriever(storage=None, filter_mode="hard", soft_fallback_threshold=0.1)
    for params in DIRECT_PARAMS:
        sub = nodes[:30]
        row = {"params": params}
        f, s = fr._apply_entity_filter_soft(sub, params)
        row["entity_soft"] = [[k, sfr(v)] for k, v in s.items()]
        row["entity_hard"] = [n.node_id for n in fr._apply_entity_filter_hard(sub, params)]
        row["entity_hard_small"] = [n.node_id for n in fr._apply_entity_filter_hard(nodes[:8], params)]
        f, s = fr._apply_entity_filter_boost(sub, params)
        row["entity_boost"] = [[k, sfr(v)] for k, v in s.items()]
        f, s = fr._apply_temporal_filter_soft(sub, params)
        row["temporal_soft"] = [[k, sfr(v)] for k, v in s.items()]
        row["temporal_hard"] = [n.node_id for n in fr._apply_temporal_filter_hard(sub, params)]
        f, s = fr._apply_temporal_filter_boost(sub, params)
        row["temporal_boost"] = [[k, sfr(v)] for k, v in s.items()]
        row["stats"] = fr.get_filter_statistics()
        out["direct"].append(row)
    # the storage path (base_candidates=None): Python's layer index is a set, so only membership is compared
    storage = InMemoryNeuralGraphStorage()

    async def fill():
        for n in nodes:
            await storage.save_node(n)
    asyncio.run(fill())
    fr = qr.create_filtered_retriever(storage, "soft")
    f, a, soft = asyncio.run(fr.get_filtered_candidates("What is Caroline's job?", "s0"))
    out["storage_path"] = {"ids": sorted(n.node_id for n in f),
                           "soft": sorted([k, sfr(v)] for k, v in soft.items())}
    return out


def dump_compact(name, obj):
    """Like ``R.dump`` without indentation (the fixture would be 3 MB indented)."""
    path = R.HERE / name
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.name} ({path.stat().st_size // 1024} KiB)")


def main():
    dump_compact("query_router.json", {
        "matchers": matchers(),
        "match_entity": match_entity_cases(),
        "analyze": analyze_all(),
        "filtered": filtered(),
    })


if __name__ == "__main__":
    main()
