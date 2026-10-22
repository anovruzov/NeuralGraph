#!/usr/bin/env python3
"""Fixtures for the parsing helpers at the end of ``retrieval/service.py`` (``extract_keywords`` onwards). Writes
``service_helpers.json``.

Inputs are LoCoMo-shaped: every question of every conversation (temporal classification, keywords, month/year),
every message of conversations 0-1 (keywords, candidate spans and their cleaning, name candidates, month/year),
and for the questions of conversations 0-1 a memory list built from the evidence messages plus their neighbours,
with the duration metadata ``temporal_utils.extract_duration_metadata`` gives each (count, relationship status,
list items, duration, and the two semantic extractors). The semantic extractors embed with
``fake_embedding(text, 64)`` (texts containing "zz" fail, as a model error would) and record every text they
embed. Hand-written edge cases follow.
"""
from __future__ import annotations

import asyncio
import json

import _reference as R

import NeuralGraph.llm_backend as llm_backend
import NeuralGraph.research.retrieval.service as svc
from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.temporal_utils import extract_duration_metadata, parse_datetime_flexible

EMBED_DIM = 64
EMBEDDED: list[str] = []


async def fake_embed(session, text, **kwargs):
    EMBEDDED.append(text)
    if "zz" in text:
        raise RuntimeError("scripted embedding failure")
    return fake_embedding(text, EMBED_DIM)


llm_backend.llm_embed = fake_embed


def messages_of(conv):
    c = conv["conversation"]
    out, i = [], 1
    while f"session_{i}" in c:
        dt = c.get(f"session_{i}_date_time", "")
        for m in c[f"session_{i}"]:
            out.append({"dia_id": m.get("dia_id"), "speaker": m["speaker"], "text": m.get("text", ""), "datetime": dt})
        i += 1
    return out


def memories_for(qa, msgs, by_dia):
    idxs = []
    for ev in qa.get("evidence", []):
        for part in str(ev).replace(";", " ").split():
            if part in by_dia:
                i = by_dia[part]
                idxs.extend(range(max(0, i - 2), min(len(msgs), i + 3)))
    idxs = list(dict.fromkeys(idxs))[:8]
    mems = []
    for i in idxs:
        m = msgs[i]
        meta = {"speaker": m["speaker"]}
        dur = extract_duration_metadata(m["text"], parse_datetime_flexible(m["datetime"]))
        for k in ("duration_years", "since_year"):
            if dur.get(k) is not None:
                meta[k] = dur[k]
        mems.append({"text": m["text"], "metadata": meta})
    return mems


def per_message(text):
    spans = svc._extract_candidate_spans(text)
    cleaned = []
    for c in spans:
        n = svc._normalize_item(c)
        cl = svc._clean_candidate(n)
        cleaned.append([n, cl, svc._is_bad_candidate(cl)])
    return {"keywords": svc.extract_keywords(text), "spans": spans, "cleaned": cleaned,
            "names": svc._extract_name_candidates(text), "month_year": list(svc.extract_month_year_from_text(text))}


async def semantic(question, mems, use_embedding):
    EMBEDDED.clear()
    qemb = fake_embedding(question, EMBED_DIM) if use_embedding else []
    span = await svc.extract_span_answer_semantic(None, question, qemb, mems)
    span_calls = list(EMBEDDED)
    EMBEDDED.clear()
    items = await svc.extract_list_items_semantic(None, question, qemb, mems)
    return {"span": span, "span_embedded": span_calls, "items": items, "items_embedded": list(EMBEDDED)}


def per_question(q, mems, with_semantic):
    tk = svc.extract_topic_keywords_for_temporal(q)
    out = {"count": svc.extract_count_answer(q, mems), "relationship": svc.extract_relationship_status(q, mems),
           "list_items": svc.extract_list_items(q, mems), "list_items_topic": svc.extract_list_items(q, mems, tk),
           "duration": svc.extract_duration_answer(q, mems)}
    if with_semantic:
        out["semantic"] = asyncio.run(semantic(q, mems, True))
        out["semantic_no_embedding"] = asyncio.run(semantic(q, mems, False))
    return out


EDGE_TEXTS = [
    "", "   ", "The \"Lord of the Rings\" and 'Harry Potter' books", "We adopted a puppy named Oscar!",
    "My cat is called Bailey, from Sweden", "I moved to New York City in March 2019 and to Paris in 2021.",
    "Café Müller's réunion — Yoga\U0001F9D8‍♀️ at the Central Park", "a pic of Max and Luna",
    "She has 3 kids. Two children? one son", "I've been married since 2015, single before", "Fun‍ time with Anna️",
    "we went to the beach too", "the Pride parade as well", "my new car recently", "THE big apple lately\n",
    "Lists: apples, pears and plums & figs", "include painting, pottery and \"sunset\" art; e.g. watercolor",
    "I love reading, hiking & camping", "maybe in may", "back in 1999 or 2005", "ten years ago", "for 5 years",
    "Rock-climbing'n stuff_here x²", "\"\"", "''", "named", "called x",
]

EDGE_QUESTIONS = [
    "When did Melanie paint a sunrise?", "What date is the party?", "how long has she been married?",
    "How long ago was the trip?", "How old is Caroline's son?", "How many kids does she have?",
    "What is her relationship status?", "What books has Melanie read?", "What do Jon and Gina like to do?",
    "Which artists does Caroline enjoy?", "What symbols are on the flag?", "What kinds of art does she make?",
    "What are the names of her pets?", "Where did they go before 2020?", "What did she do last summer?",
    "Is it today or tomorrow?", "since when?", "In 2019 what happened?", "What has she bought recently?",
    "What events has Caroline participated in?", "Who supports her?", "", "   ",
    "What types of pets do they have?", "Did she join the support group?",
]


def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    out = {"questions": [], "messages": [], "qa_cases": [], "edge_texts": [], "edge_questions": [],
           "items": [], "cosine": []}
    for conv in data:
        for qa in conv["qa"]:
            q = qa["question"]
            answer = str(qa.get("answer", qa.get("adversarial_answer", "")))
            out["questions"].append([q, answer, svc.is_temporal_question(q),
                                     sorted(svc.extract_topic_keywords_for_temporal(q)), svc.extract_keywords(q),
                                     list(svc.extract_month_year_from_text(answer))])
    for ci, conv in enumerate(data[:2]):
        msgs = messages_of(conv)
        by_dia = {m["dia_id"]: i for i, m in enumerate(msgs)}
        for m in msgs:
            out["messages"].append([m["text"], per_message(m["text"])])
        for qi, qa in enumerate(conv["qa"]):
            mems = memories_for(qa, msgs, by_dia)
            out["qa_cases"].append({"question": qa["question"], "memories": mems,
                                    "result": per_question(qa["question"], mems, with_semantic=qi % 3 == 0)})
    edge_mems = [{"text": t, "metadata": {"speaker": "Melanie" if i % 2 else "Anna"}} for i, t in enumerate(EDGE_TEXTS)]
    edge_mems[5]["metadata"]["since_year"] = 2015
    edge_mems[6]["metadata"]["duration_years"] = 4.0
    edge_mems.append({"metadata": {"producer_id": "Jon"}})
    edge_mems.append({"text": "zz fails to embed: Zz Top and Lizzie", "metadata": {}})
    for t in EDGE_TEXTS:
        out["edge_texts"].append([t, per_message(t)])
    for q in EDGE_QUESTIONS:
        out["edge_questions"].append({"question": q, "is_temporal": svc.is_temporal_question(q),
                                      "result": per_question(q, edge_mems, with_semantic=True)})
    out["edge_memories"] = edge_mems
    out["items"] = [[x, svc._normalize_item(x), svc._clean_candidate(x), svc._is_bad_candidate(x)] for x in [
        "the dog", "A Car", "another day", "an apple too", "my  \"quoted\"  thing", "Hi", "ok", "s", "re", "ve", "ab",
        "the that", "had fun", "one two three four five", "Paris", "  ", "x-ray!", "anotherone", "eachother",
        "every one as well", "Lately", "things", "it its", "visited Rome"]]
    out["dedupe"] = svc._dedupe_items(["A", "a", "", "B", "b ", "B", "c"])
    out["cosine"] = [[a, b, svc._cosine_similarity(a, b)] for a, b in [
        ([1.0, 2.0, 3.0], [1.0, 2.0]), ([], [1.0]), ([0.0, 0.0], [1.0, 1.0]), ([0.1, 0.2, 0.3], [0.3, 0.2, 0.1]),
        (fake_embedding("hello world", 16), fake_embedding("world peace", 16))]]
    R.dump("service_helpers.json", out)


if __name__ == "__main__":
    main()
