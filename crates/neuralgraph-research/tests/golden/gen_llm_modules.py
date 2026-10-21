#!/usr/bin/env python3
"""Fixtures for the LLM-facing retrieval modules: ``prompts``, ``answering``, ``reranker``, ``speaker_profiles``,
``llm_profile_extractor`` and ``llm_profile_query``. Writes ``llm_modules.json``.

``llm_backend.llm_generate`` / ``llm_embed`` are replaced by a scripted model that records every prompt and keyword
argument and answers from a per-case script (text, an exception, or a delay that trips a timeout). The two code
paths that talked HTTP directly (query rewrites against Ollama, the optional OpenAI fact extraction) are run
against a local aiohttp server with canned responses; the requests it received are recorded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import warnings
import re
from datetime import datetime, timedelta

import _reference as R

import aiohttp
from aiohttp import web

import NeuralGraph.llm_backend as llm_backend
import NeuralGraph.research.retrieval.data_types as dt_mod
import NeuralGraph.research.retrieval.storage as storage_mod

R.freeze_now([dt_mod, storage_mod])
import NeuralGraph.research.retrieval.answering as answering  # noqa: E402
import NeuralGraph.research.retrieval.llm_profile_extractor as extractor  # noqa: E402
import NeuralGraph.research.retrieval.llm_profile_query as pquery  # noqa: E402
import NeuralGraph.research.retrieval.prompts as prompts  # noqa: E402
import NeuralGraph.research.retrieval.query_router as qr  # noqa: E402
import NeuralGraph.research.retrieval.reranker as reranker  # noqa: E402
import NeuralGraph.research.retrieval.speaker_profiles as profiles  # noqa: E402
from NeuralGraph.research.retrieval.data_types import NeuralNode, NodeLayer  # noqa: E402

# LLMReranker opens an aiohttp session it never closes when rerank() is used directly.
warnings.filterwarnings("ignore", category=ResourceWarning)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

LOCOMO = json.loads((R.DATASETS / "locomo10.json").read_text())
CONV = LOCOMO[0]["conversation"]
TURNS = [t for k in sorted((k for k in CONV if re.fullmatch(r"session_\d+", k)), key=lambda k: int(k[8:]))
         for t in CONV[k]]
QUESTIONS = [q["question"] for q in LOCOMO[0]["qa"]]


# ---------------------------------------------------------------------------------------------------------------
# Scripted model
# ---------------------------------------------------------------------------------------------------------------

class ScriptedModel:
    """Stands in for ``llm_backend.llm_generate``/``llm_embed``: records calls, answers from ``outcomes``."""

    def __init__(self):
        self.outcomes = []
        self.calls = []

    def script(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def _next(self):
        return self.outcomes.pop(0)

    async def generate(self, session, prompt, *, model=None, base_url=None, temperature=0.0, max_tokens=256,
                       timeout_seconds=60.0):
        self.calls.append({"prompt": prompt, "model": model, "base_url": base_url, "temperature": temperature,
                           "max_tokens": max_tokens, "timeout_seconds": timeout_seconds})
        o = self._next()
        if "sleep" in o:
            await asyncio.sleep(o["sleep"])
        if "error" in o:
            raise RuntimeError(o["error"])
        return o["text"]

    async def embed(self, session, text, *, model=None, base_url=None, timeout_seconds=30.0):
        self.calls.append({"text": text, "model": model, "base_url": base_url, "timeout_seconds": timeout_seconds})
        o = self._next()
        if "error" in o:
            raise RuntimeError(o["error"])
        return o["vector"]


MODEL = ScriptedModel()
llm_backend.llm_generate = MODEL.generate
llm_backend.llm_embed = MODEL.embed


def run(coro):
    return asyncio.run(coro)


def scripted(outcomes, coro_fn):
    """Run ``coro_fn()`` against the scripted model; returns (result, calls)."""
    MODEL.script(outcomes)
    result = run(coro_fn())
    assert not MODEL.outcomes, f"unused outcomes {MODEL.outcomes}"
    return result, MODEL.calls


# ---------------------------------------------------------------------------------------------------------------
# Canned HTTP server (real aiohttp client on the Python side)
# ---------------------------------------------------------------------------------------------------------------

async def with_server(responses, fn):
    """Serve ``responses`` (status, content_type, body) in order; returns (fn result, recorded requests)."""
    queue = list(responses)
    seen = []

    async def handler(request):
        body = await request.read()
        seen.append({"method": request.method, "path": request.path,
                     "authorization": request.headers.get("Authorization"),
                     "json": json.loads(body) if body else None})
        status, ctype, text = queue.pop(0)
        return web.Response(status=status, body=text.encode(), headers={"Content-Type": ctype})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            result = await fn(session, f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()
    return result, seen


# ---------------------------------------------------------------------------------------------------------------
# prompts.py
# ---------------------------------------------------------------------------------------------------------------

PROMPT_NAMES = ["RETRIEVAL_ANSWER_PROMPT", "SINGLE_HOP_ANSWER_PROMPT", "LIST_QUESTION_ANSWER_PROMPT",
                "AGGREGATION_ANSWER_PROMPT", "TEMPORAL_QUERY_PROMPT", "MULTI_HOP_QUERY_PROMPT",
                "OPEN_DOMAIN_QUERY_PROMPT", "QUERY_REWRITE_PROMPT", "SINGLE_HOP_REWRITE_PROMPT",
                "TEMPORAL_REWRITE_PROMPT"]
ANSWER_PROMPT_NAMES = ["TEMPORAL_ANSWER_PROMPT", "OPEN_DOMAIN_INFER_PROMPT", "OPEN_DOMAIN_WORLD_PROMPT",
                       "OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT", "STRICT_ANSWER_PROMPT"]


def prompt_name(text):
    for n in PROMPT_NAMES:
        if getattr(prompts, n) is text:
            return n
    raise AssertionError("unknown prompt")


def context_nodes():
    base = datetime(2023, 5, 8, 13, 56)
    out = []
    for i, t in enumerate(TURNS[:24]):
        s = t["speaker"]
        meta = [
            {"speaker": s},
            {"producer_id": s, "speaker": "ignored"},
            {"speaker": s, "resolved_date": "2023-05-07"},
            {"producer_id": None, "speaker": s, "resolved_date": ""},
            {"speaker": "", "resolved_date": 2023},
            {},
            {"producer_id": 7, "resolved_date": None},
            {"speaker": {"name": s}, "resolved_date": ["May", 7]},
        ][i % 8]
        created = base + timedelta(minutes=37 * i, seconds=i)
        out.append({"node_id": f"c{i:02d}", "content": t["text"], "created_at": created.isoformat(),
                    "metadata": meta})
    return out


def py_node(d):
    n = NeuralNode(node_id=d["node_id"], layer=NodeLayer.MESSAGE, content=d["content"],
                   metadata=dict(d.get("metadata", {})))
    if "created_at" in d:
        n.created_at = datetime.fromisoformat(d["created_at"])
    return n


def prompts_fixture():
    nodes = context_nodes()
    pairs = [(py_node(d), 0.5) for d in nodes]
    contexts = []
    for max_chars, ts, sp in [(15000, True, True), (700, True, True), (0, True, True), (15000, False, True),
                              (15000, True, False), (300, False, False), (2000, True, True)]:
        contexts.append([max_chars, ts, sp, prompts.format_retrieval_context(pairs, max_chars, ts, sp)])

    rewrite_queries = QUESTIONS[:40] + ["Who's there?", "What's up with Bob's car", "plain statement",
                                        "how long ago was it", "{question} {num_rewrites}", ""]
    rewrite = [[q, n, *prompts.get_rewrite_prompt(q, n)] for q in rewrite_queries for n in (2, 3)]

    dedupe_cases = [
        ["What is Caroline's job?", ["What does Caroline do for work?", "What is Caroline's job?",
                                     "what is caroline's   JOB?", "Caroline job", "What does Caroline do for work"]],
        ["When did Mel go camping?", []],
        ["", ["a", "", "a b", "b a"]],
        ["Where did Caroline move from?", ["From where did Caroline move?", "Where did Caroline move from 4 years ago?",
                                           "Which country did Caroline move from?",
                                           "Which country did Caroline move from originally?"]],
    ]
    dedupe = [[o, rw, t, prompts.deduplicate_rewrites(o, rw, t)] for o, rw in dedupe_cases for t in (0.85, 0.5, 0.0)]

    fuse_inputs = [
        {"orig": [["a", 0.9], ["b", 0.5], ["c", 0.3]], "rw1": [["b", 0.6], ["d", 0.8], ["a", 0.95]],
         "rw2": [["b", 0.7], ["e", 0.1], ["d", 0.81]], "rw3": [["b", 0.9], ["c", 0.3]]},
        {"rw1": [["x", 0.2], ["y", 0.2]], "rw2": [["x", 0.3]]},
        {"orig": [["a", 0.5], ["a", 0.4]], "rw": [["a", 0.5]]},
    ]
    fuse = []
    for inp in fuse_inputs:
        nodes_by_id = {}

        def node(i):
            return nodes_by_id.setdefault(i, NeuralNode(node_id=i, layer=NodeLayer.MESSAGE, content=i))
        rbq = {q: [(node(i), s) for i, s in rs] for q, rs in inp.items()}
        for limit in (10, 2, -1):
            for penalty in (0.7, 1.5):
                out = prompts.fuse_rewrite_results(rbq, "orig", limit, penalty)
                fuse.append([inp, limit, penalty, [[n.node_id, s] for n, s in out]])

    select = []
    for conv in LOCOMO:
        c = conv["conversation"]
        router = qr.QueryRouter({c["speaker_a"], c["speaker_b"]})
        for q in conv["qa"]:
            a = router.analyze(q["question"])
            select.append([prompt_name(prompts.select_prompt_for_query(q["question"])),
                           prompt_name(prompts.select_prompt_for_query(q["question"], a.is_list_question,
                                                                       a.list_question_type))])
    select_edge = []
    for q in ["what do .* and .* have", "Has .* read this?", "has bob read?", "What is Bob's dog?",
              "what does x do", "how many", "WHEN", "they went together", "Would she?", "Where is Paris",
              "plain", "who is bob's friend", "what did tom say"]:
        for il, lt in [(False, None), (True, "plural"), (False, "plural"), (True, "aggregation"), (True, "all")]:
            select_edge.append([q, il, lt, prompt_name(prompts.select_prompt_for_query(q, il, lt))])

    rewrites_http = []
    cases = [
        ("What is Caroline's job?", 2, (200, "application/json",
                                        json.dumps({"response": ' ["What does Caroline do for work?", "short", 7, '
                                                                '"What profession does Caroline have?", '
                                                                '"Caroline\'s occupation?"] '}))),
        ("When did Mel go camping?", 3, (200, "application/json; charset=utf-8",
                                         json.dumps({"response": "Sure!\n[\"When did Melanie go camping?\", "
                                                                 "\"What date did Mel camp?\"]\nDone"}))),
        ("plain", 2, (200, "application/json", json.dumps({"response": "1. \"First rewrite here\",\n"
                                                                       "[ignored line\n  'Second one',  \n\n"
                                                                       "third"}))),
        ("plain", 2, (200, "application/json", json.dumps({"response": "[not json] and [more]"}))),
        ("plain", -1, (200, "application/json", json.dumps({"response": "a\nb\nc"}))),
        ("plain", 2, (200, "application/json", json.dumps({"other": 1}))),
        ("plain", 2, (200, "application/json", json.dumps({"response": 5}))),
        ("plain", 2, (200, "application/json", json.dumps([1, 2]))),
        ("plain", 2, (200, "text/plain", json.dumps({"response": "[\"abcdefg\"]"}))),
        ("plain", 2, (500, "application/json", json.dumps({"response": "[\"server error text\"]"}))),
        ("plain", 2, (200, "application/json", "")),
        ("plain", 2, (200, "application/json", "not json")),
        ("plain", 2, (200, "application/vnd.api+json", json.dumps({"response": "[\"vendor json ok\"]"}))),
    ]
    for q, n, resp in cases:
        async def call(session, base, q=q, n=n):
            return await prompts.generate_query_rewrites(q, session, base_url=base, model="rw-model",
                                                         num_rewrites=n, timeout_seconds=5.0)
        result, seen = run(with_server([resp], call))
        rewrites_http.append({"query": q, "num_rewrites": n, "response": list(resp), "requests": seen,
                              "result": result})

    async def refused():
        async with aiohttp.ClientSession() as session:
            return await prompts.generate_query_rewrites("plain", session, base_url="http://127.0.0.1:9",
                                                         model="m", num_rewrites=2, timeout_seconds=2.0)
    rewrites_refused = run(refused())

    parse_cases = ['["What is Bob\'s job title?", "Bob\'s work?", "Which job does Bob have?"]', "line one\nline two",
                   '  ["x"] ', "[", "]", "][", '["unterminated', "", '[{"a": 1}, "valid rewrite"]', '"q1",\n"q2",']
    parse = [[p, n, prompts_parse(p, n)] for p in parse_cases for n in (2, 1, 0, -1)]

    return {"constants": {n: getattr(prompts, n) for n in PROMPT_NAMES}, "context_nodes": nodes,
            "contexts": contexts, "rewrite_prompts": rewrite, "dedupe": dedupe, "fuse": fuse, "select": select,
            "select_edge": select_edge, "rewrites_http": rewrites_http, "rewrites_refused": rewrites_refused,
            "parse_rewrites": parse}


def prompts_parse(resp, n):
    """Run generate_query_rewrites's parsing on a raw response through a canned server."""
    async def call(session, base):
        return await prompts.generate_query_rewrites("q", session, base_url=base, model="m", num_rewrites=n)
    result, _ = run(with_server([(200, "application/json", json.dumps({"response": resp}))], call))
    return result


# ---------------------------------------------------------------------------------------------------------------
# answering.py
# ---------------------------------------------------------------------------------------------------------------

def answering_fixture():
    cfg = answering.AnsweringConfig(ollama_base_url="http://answer-host:1234", answer_model="answer-model",
                                    embedding_model="embed-model", answer_timeout_seconds=12.5,
                                    embedding_timeout_seconds=3.0, num_predict=77)
    context = "[2023-05-08] Caroline: I went to a LGBTQ support group {question} yesterday.\nMelanie: {context} ok"
    modes = ["TEMPORAL", "LIST", "AGGREGATION", "OPEN_DOMAIN_INFER", "OPEN_DOMAIN_WORLD", "OPEN_DOMAIN_WORLD_MEM",
             "OPEN_DOMAIN_INFER_WORLD", "STRICT", "INFERENTIAL", ""]
    outcomes = [{"text": "May 7, 2023"}, {"text": ""}, {"error": "Cannot connect to host answer-host:1234"}]
    answers = []
    for mode in modes:
        for o in outcomes:
            result, calls = scripted([o], lambda: answering.generate_answer(
                None, "When did Caroline go to the LGBTQ support group?", context, mode=mode, config=cfg))
            answers.append({"mode": mode, "outcome": o, "calls": calls, "result": result})
    embeds = []
    for o in [{"vector": [0.25, -1.5, 3.0]}, {"vector": []}, {"error": "timeout"}]:
        result, calls = scripted([o], lambda: answering.get_embedding(None, "I went camping", config=cfg))
        embeds.append({"outcome": o, "calls": calls, "result": result})
    d = answering.AnsweringConfig()
    defaults = {"answer_timeout_seconds": d.answer_timeout_seconds,
                "embedding_timeout_seconds": d.embedding_timeout_seconds, "num_predict": d.num_predict}
    return {"constants": {n: getattr(answering, n) for n in ANSWER_PROMPT_NAMES},
            "config": cfg.__dict__, "context": context, "answers": answers, "embeds": embeds,
            "defaults": defaults}


# ---------------------------------------------------------------------------------------------------------------
# reranker.py
# ---------------------------------------------------------------------------------------------------------------

class FakeTime:
    now = 1000.0

    @classmethod
    def time(cls):
        return cls.now

    def __getattr__(self, name):
        import time
        return getattr(time, name)


def cache_state(c):
    return [[[k, v[0], v[1]] for k, v in c._cache.items()], list(c._access_order)]


def score_cache_fixture():
    import time as real_time
    reranker.time = type("T", (), {"time": staticmethod(FakeTime.time), "perf_counter": real_time.perf_counter})
    out = []
    for max_size, ttl in [(3, 300.0), (2, 10.0)]:
        c = reranker.ScoreCache(max_size=max_size, ttl_seconds=ttl)
        steps = []
        ops = [("set", "q", "a", 1.0, 1000.0), ("set", "q", "b", 2.0, 1001.0), ("get", "q", "a", 1002.0),
               ("set", "q", "c", 3.0, 1003.0), ("set", "q", "d", 0.5, 1004.0), ("get", "q", "b", 1005.0),
               ("get", "q", "a", 1006.0), ("set", "q", "a", 1.5, 1007.0), ("get", "q", "c", 1015.0),
               ("get", "q", "d", 1400.0), ("set", "q", "a", 2.5, 1401.0), ("get", "q", "a", 1401.0),
               ("set", "q", "e", 0.0, 1402.0), ("get", "q|||x", "y", 1402.0), ("set", "q|||x", "y", 9.0, 1403.0),
               ("get", "q", "x|||y", 1403.0), ("clear",), ("get", "q", "a", 1404.0), ("set", "", "", 1.0, 1405.0)]
        for op in ops:
            if op[0] == "set":
                FakeTime.now = op[4]
                c.set(op[1], op[2], op[3])
                res = None
            elif op[0] == "get":
                FakeTime.now = op[3]
                res = c.get(op[1], op[2])
            else:
                c.clear()
                res = None
            steps.append([list(op), res, cache_state(c)])
        out.append({"max_size": max_size, "ttl": ttl, "steps": steps})
    reranker.time = real_time
    keys = [[q, t, reranker.ScoreCache()._make_key(q, t)] for q, t in
            [("q", "a"), ("", ""), ("What is Caroline's job?", "I'm a counselor 🧘‍♀️"), ("a|||b", "c")]]
    return {"caches": out, "keys": keys}


LLM_SCORE_REPLIES = ['{"score": 2}', '{"score": 5}', '{"score": -1}', '{"score": "3"}', '{"score": 2.7}',
                     '{"score": true}', '{"score": null}', '{"score": [1]}', '{}', '[3]', '2', 'Score: 3',
                     'The answer is 0', 'no digits', '', ' {"score": 1} ', '```json\n{"score": 3}\n```',
                     '{"score": " 2 "}', '{"score": "1_0"}', '{"score": 1e400}', '{"score": NaN}',
                     '{"score": "2.5"}', '{"score": 99999999999999999999999}', '{"score": -1e30}', '"3"', 'null',
                     '{"score": 1, "score": 3}', '{"Score": 0} 2']


def node_of(i, t, extra=None):
    meta = {"speaker": t["speaker"]}
    if extra:
        meta.update(extra)
    return NeuralNode(node_id=f"t{i:03d}", layer=NodeLayer.MESSAGE, content=t["text"], metadata=meta)


def node_json(n):
    return {"node_id": n.node_id, "content": n.content, "metadata": n.metadata}


def rr_config(**kw):
    return reranker.RerankerConfig(llm_model="rr-model", llm_base_url="http://rr-host:1", **kw)


def reranker_fixture():
    q = QUESTIONS[0]
    long_text = TURNS[3]["text"] * 6
    llm_scores = []
    for reply in LLM_SCORE_REPLIES:
        rr = reranker.LLMReranker(rr_config())
        result, calls = scripted([{"text": reply}], lambda: rr.score_candidate(q, long_text, "Caroline"))
        llm_scores.append({"reply": reply, "calls": calls, "result": result})
    rr = reranker.LLMReranker(rr_config())
    result, calls = scripted([{"error": "boom"}], lambda: rr.score_candidate("Q?", "ünïcode text", ""))
    llm_scores.append({"reply": None, "error": "boom", "calls": calls, "result": result})

    semantic = []
    texts = [t for t in TURNS[:60]]
    for qi, question in enumerate(QUESTIONS[:40]):
        for t in texts[qi % 7::7][:8]:
            for sp in (t["speaker"], ""):
                semantic.append([question, t["text"], sp,
                                 run(reranker.SemanticBoostReranker().score_candidate(question, t["text"], sp))])
    for question, text, sp in [("Who?", "short", "Who"), ("What did Caroline say?", "x" * 600, "caroline"),
                               ("A B C", "a b c", "A"), ("", "", ""), ("Caroline Caroline Melanie",
                                                                       "caroline and melanie", "Melanie")]:
        semantic.append([question, text, sp, run(reranker.SemanticBoostReranker().score_candidate(question, text, sp))])

    # candidate pool: real turns, duplicated content and a duplicated node id
    pool = [node_of(i, TURNS[i]) for i in range(24)]
    pool.append(NeuralNode(node_id="dup-content", layer=NodeLayer.MESSAGE, content=TURNS[2]["text"],
                           metadata={"producer_id": "Melanie"}))
    pool.append(NeuralNode(node_id="t005", layer=NodeLayer.MESSAGE, content="same id as t005", metadata={}))
    charges = [round(0.9 - 0.03 * i, 6) if i % 5 else 0.5 for i in range(len(pool))]
    cands = list(zip(pool, charges))

    def stats(r):
        s = r.get_stats()
        return [s["total_reranks"], s["cache_hits"], s["cache_misses"], s["cache_hit_rate"]]

    def out_list(out):
        return [[n.node_id, s] for n, s in out]

    reranks = []
    for name, cfg_kw, limit in [("semantic", {}, 10), ("semantic", {"max_candidates": 12}, 5),
                                ("semantic", {"min_score_threshold": 0.75}, 30), ("semantic", {}, -3)]:
        r = reranker.SemanticBoostReranker(rr_config(**cfg_kw))
        first = run(r.rerank(QUESTIONS[2], cands, limit))
        second = run(r.rerank(QUESTIONS[2], cands, limit))
        reranks.append({"kind": name, "config": cfg_kw, "limit": limit, "first": out_list(first),
                        "second": out_list(second), "stats": stats(r), "outcomes": [], "calls": []})

    def llm_outcomes(n, timeouts=()):
        replies = ['{"score": 3}', '{"score": 0}', "2", "garbage", '{"score": 1}']
        out = []
        for i in range(n):
            o = {"text": replies[i % len(replies)]}
            if i in timeouts:
                o["sleep"] = 0.2
            if i % 9 == 4:
                o = {"error": "llm down"}
            out.append(o)
        return out

    # LLM reranker: 24 distinct contents among the first 25 (dup-content hits the cache), two timeouts
    for cfg_kw, limit, timeouts in [({"async_timeout_ms": 5000.0}, 10, ()), ({"async_timeout_ms": 50.0}, 8, (3, 7)),
                                    ({"async_timeout_ms": 0.0}, 5, None)]:
        r = reranker.LLMReranker(rr_config(**cfg_kw))
        n_calls = 0 if timeouts is None else 25
        outcomes = [] if timeouts is None else llm_outcomes(25, timeouts)
        MODEL.script(outcomes)
        out = run(r.rerank(QUESTIONS[5], cands, limit))
        calls = MODEL.calls
        assert len(calls) == n_calls and not MODEL.outcomes, (len(calls), MODEL.outcomes)
        reranks.append({"kind": "llm", "config": cfg_kw, "limit": limit, "first": out_list(out), "second": None,
                        "stats": stats(r), "outcomes": outcomes, "calls": calls})

    # Hybrid: semantic to 3*limit, then the LLM over those
    for limit in (5, 20):
        r = reranker.HybridReranker(rr_config(async_timeout_ms=5000.0))
        sem = run(reranker.SemanticBoostReranker(rr_config()).rerank(QUESTIONS[7], cands, min(limit * 3, len(cands))))
        distinct = len({n.content for n, _ in sem}) if len(sem) > limit else 0
        outcomes = llm_outcomes(distinct)
        MODEL.script(outcomes)
        out = run(r.rerank(QUESTIONS[7], cands, limit))
        assert not MODEL.outcomes
        reranks.append({"kind": "hybrid", "config": {"async_timeout_ms": 5000.0}, "limit": limit,
                        "first": out_list(out), "second": None, "stats": stats(r), "outcomes": outcomes,
                        "calls": MODEL.calls})

    factories = []
    for t in ["llm", "semantic", "hybrid", "cross_encoder", ""]:
        try:
            factories.append([t, type(reranker.create_reranker(t)).__name__])
        except ValueError as e:
            factories.append([t, f"ValueError: {e}"])
    rc = run(reranker.rerank_candidates(QUESTIONS[3], cands))
    rc_limit = run(reranker.rerank_candidates(QUESTIONS[3], cands, "semantic", 4, original_score_weight=0.5))
    try:
        run(reranker.rerank_candidates(QUESTIONS[3], cands, "bogus"))
        rc_err = None
    except ValueError as e:
        rc_err = str(e)

    # rerank_candidates_parallel: the runner's call
    parallel = []
    pool2 = [node_of(i, TURNS[i]) for i in range(40, 80)]
    ties = [0.8, 0.8, 0.7, 0.7, 0.7, 0.65, 0.9, 0.8, 0.6, 0.6]
    pcands = [(n, ties[i % len(ties)] - (0.0 if i < 20 else 0.05)) for i, n in enumerate(pool2)]
    replies = ['{"score": 3}', '{"score": 2}', "1", "nonsense", '{"score": 9}', '{"score": "0"}', "", '{"score": 2}',
               "Score: 2 of 3", '[2]']
    for kw, n_cands in [({}, 40), ({"limit": 5}, 40), ({"max_candidates": 10, "limit": 15}, 40),
                        ({"score_boost": 0.5, "limit": 30}, 25), ({"limit": -2}, 12), ({"max_candidates": -5}, 12),
                        ({"timeout_seconds": 2.5}, 3), ({}, 0)]:
        cands_n = pcands[:n_cands]
        m = len(cands_n[:kw.get("max_candidates", 50)])
        outcomes = [({"error": "Server disconnected"} if i % 11 == 6 else {"text": replies[i % len(replies)]})
                    for i in range(m)]
        args = {"llm_model": "rr-model", "llm_base_url": "http://rr-host:1", **kw}
        result, calls = scripted(outcomes, lambda: reranker.rerank_candidates_parallel(
            QUESTIONS[11], cands_n, **args))
        parallel.append({"kwargs": kw, "n_candidates": n_cands, "outcomes": outcomes, "calls": calls,
                         "result": [[n.node_id, s] for n, s in result]})
    # the runner's real shape: a LoCoMo question over each conversation turn window
    for qi in (20, 33, 47):
        question = QUESTIONS[qi]
        window = [(node_of(100 + i, TURNS[100 + i]), round(1.0 - i * 0.017, 4)) for i in range(30)]
        outcomes = [{"text": replies[(i * 7 + qi) % len(replies)]} for i in range(30)]
        result, calls = scripted(outcomes, lambda: reranker.rerank_candidates_parallel(
            question, window, max_candidates=30, limit=15, llm_model="rr-model", llm_base_url="http://rr-host:1"))
        parallel.append({"kwargs": {"max_candidates": 30, "limit": 15}, "window_question": qi,
                         "outcomes": outcomes, "calls": calls, "result": [[n.node_id, s] for n, s in result]})

    cfg = reranker.RerankerConfig()
    defaults = {k: v for k, v in cfg.__dict__.items() if k not in ("llm_base_url", "llm_model")}
    return {"llm_scores": llm_scores, "semantic": semantic, "pool": [node_json(n) for n in pool],
            "charges": charges, "reranks": reranks, "factories": factories,
            "rerank_candidates": [[n.node_id, s] for n, s in rc], "rerank_candidates_limit":
            [[n.node_id, s] for n, s in rc_limit], "rerank_candidates_error": rc_err,
            "parallel_pool": [node_json(n) for n in pool2], "parallel_charges": [c for _, c in pcands],
            "window_pool": [node_json(node_of(100 + i, TURNS[100 + i])) for i in range(30)],
            "parallel": parallel, "config_defaults": defaults, **score_cache_fixture()}


# ---------------------------------------------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------------------------------------------

FACT_REPLIES = [
    '{"facts": ["went camping at the beach", "  has two kids  ", "", 5, null, "reads Charlotte\'s Web"]}',
    '```json\n{"facts": ["practices pottery"]}\n```',
    '```\n{"facts": ["fenced without tag"]}\n```',
    '```', '{"facts": "not a list"}', '{"facts": {"a": 1}}', '{"other": []}', '[1, 2]', 'not json', '',
    '{"facts": []}', '  {"facts": ["leading space"]}', '```json{"facts": ["glued"]}```trailing',
]


def profiles_fixture():
    extractor.USE_OPENAI_EXTRACTION = False
    msg = TURNS[4]["text"]
    extract = []
    for reply in FACT_REPLIES + [None]:
        o = {"text": reply} if reply is not None else {"error": "connection reset"}
        result, calls = scripted([o], lambda: extractor.extract_speaker_facts_llm(None, "Melanie", msg))
        extract.append({"outcome": o, "calls": calls, "result": result})

    messages = [{"speaker": t["speaker"], "text": t["text"]} for t in TURNS[:37]]
    outcomes = [({"error": "x"} if i % 6 == 5 else {"text": FACT_REPLIES[i % 3]}) for i in range(37)]
    par, calls = scripted(outcomes, lambda: extractor.extract_facts_parallel(None, messages, batch_size=15))
    parallel = {"messages": messages, "outcomes": outcomes, "calls": calls, "result": par}
    par7, calls7 = scripted(outcomes[:10], lambda: extractor.extract_facts_parallel(None, messages[:10], 4))
    try:
        run(extractor.extract_facts_parallel(None, messages, batch_size=0))
        zero = None
    except ValueError as e:
        zero = str(e)
    neg = run(extractor.extract_facts_parallel(None, messages, batch_size=-3))

    # OpenAI path through a canned server (the session rewrites the api.openai.com URL to it)
    extractor.USE_OPENAI_EXTRACTION = True
    extractor.OPENAI_API_KEY = "sk-test"
    openai_cases = [
        (200, "application/json", json.dumps({"choices": [{"message": {"content": ' {"facts": ["openai fact"]} '}}]})),
        (500, "application/json", json.dumps({"choices": [{"message": {"content": '{"facts": ["x"]}'}}]})),
        (200, "application/json", json.dumps({"choices": [{"message": {"content": None}}]})),
        (200, "application/json", json.dumps({"choices": []})),
        (200, "application/json", json.dumps({})),
        (200, "application/json", json.dumps({"choices": [{}]})),
        (200, "text/html", json.dumps({"choices": [{"message": {"content": '{"facts": ["y"]}'}}]})),
        (200, "application/json", "[]"),
        (200, "application/json", json.dumps({"choices": [{"message": {"content": '```json\n{"facts": ["z"]}\n```'}}]})),
    ]
    openai = []
    for resp in openai_cases:
        async def call(session, base):
            class Redirect:
                def post(self, url, **kw):
                    return session.post(url.replace("https://api.openai.com", base), **kw)
            return await extractor.extract_speaker_facts_llm(Redirect(), "Caroline", msg)
        result, seen = run(with_server([resp], call))
        openai.append({"response": list(resp), "requests": seen, "result": result})
    extractor.USE_OPENAI_EXTRACTION = False
    extractor.OPENAI_API_KEY = ""

    profile = {"name": "Caroline", "extracted_facts": [f"fact {i}" for i in range(105)] + [7, None],
               "all_messages": [t["text"] for t in TURNS[:26]] + ["x" * 250]}
    small = {"extracted_facts": [], "all_messages": ["only message"]}
    empty = {}
    replies = [
        '{"answer": "Sweden", "reasoning": "moved from Sweden"}',
        '```json\n{"answer": ["pottery", "painting", 3], "reasoning": ["a", 1]}\n```',
        '{"answer": "NOT_FOUND", "reasoning": "nothing"}', '{"answer": "", "reasoning": null}', '{"reasoning": "r"}',
        '{"answer": "  padded  "}', '{"answer": null}', '{"answer": 3.0, "reasoning": {"k": true}}',
        '{"answer": {"a": "b", "c": [1, 2.5]}}', '[1]', '"text"', '5', 'null', 'not json', '',
        '{"answer": "pottery, painting and mentoring", "reasoning": "r"}',
    ]
    golds = [None, "Sweden", "pottery, painting and mentoring", "", 2022, '"Sweden", "Norway"', "a and b, cd"]
    pq = []
    for prof, name in [(profile, "Caroline"), (small, "Mel"), (empty, "Nobody")]:
        for i, reply in enumerate(replies):
            gold = golds[i % len(golds)]
            result, calls = scripted([{"text": reply}], lambda: pquery.query_profile_with_llm(
                None, name, prof, "Where did Caroline move from?", gold))
            pq.append({"profile": prof, "speaker": name, "gold": gold, "reply": reply, "calls": calls,
                       "result": result})
    result, calls = scripted([{"error": "down"}], lambda: pquery.query_profile_with_llm(None, "C", small, "Q?"))
    pq.append({"profile": small, "speaker": "C", "gold": None, "reply": None, "calls": calls, "result": result})

    conf = []
    for a in ["Sweden", "pottery, painting", "nothing", "", "2022"]:
        for g in golds[1:] + ['ab, "xyz"', "a , b , c", 'and and and', 0, 3.5, True]:
            conf.append([a, g, pquery._calculate_answer_confidence(a, g)])

    # profiler
    async def build():
        p = profiles.UniversalSpeakerProfiler()

        async def ext(name, text):
            return [f"{name} said {len(text)} chars"] if len(text) > 60 else []
        for i, t in enumerate(TURNS[:30]):
            await p.add_message(t["speaker"], t["text"], llm_extractor=ext if i % 2 == 0 else None)
        out = {"profiles": p.get_all_profiles()}
        out["no_target"] = await p.query_single_hop(None, "What did the dog eat?", None)
        try:
            await p.query_single_hop(None, "What is MELANIE's hobby?", None)
            out["target_error"] = None
        except ModuleNotFoundError as e:
            out["target_error"] = str(e)
        try:
            await p.profiles["Caroline"].query(None, "q")
            out["query_error"] = None
        except ModuleNotFoundError as e:
            out["query_error"] = str(e)
        return out
    profiler = run(build())

    return {"extract": extract, "parallel": parallel, "parallel_batch4": {"result": par7, "calls": calls7},
            "batch_zero_error": zero, "batch_negative": neg, "openai": openai, "profile_query": pq,
            "confidence": conf, "profiler": profiler}


def main():
    path = R.HERE / "llm_modules.json"
    obj = {"prompts": prompts_fixture(), "answering": answering_fixture(), "reranker": reranker_fixture(),
           "profiles": profiles_fixture()}
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.name} ({path.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
