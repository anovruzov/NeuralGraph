#!/usr/bin/env python3
"""Fixtures for the LLM entity-graph extractor (``retrieval/kg_extractor.py``). Writes ``kg_extractor.json``.

``llm_backend.llm_generate`` is replaced by a script: every prompt the extractor sends is recorded with the
response it got (``None`` = the call raised). The responses are shaped from the LoCoMo message text (entities from
its capitalised words, a triple between the first two) and rotated through every odd shape the parser must
survive: code fences, prose around the JSON, invalid JSON, strings where lists belong, nicknames, first person,
possessives, null types, numbers, over-long names. The Rust test replays the same responses in order and must
send byte-identical prompts and produce the same registry and triples.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path

import _reference as R

import NeuralGraph.research.retrieval.kg_extractor as kg

N_MESSAGES = 40


def scripted_response(i: int, msg: dict) -> str | None:
    caps = [w for w in re.findall(r"\b[A-Z][a-z]+\b", msg["text"]) if w not in {"I", "The"}]
    ents = [{"name": c, "type": "person" if i % 3 == 0 else "thing"} for c in caps[:3]]
    trip = [[caps[0], "talked about", caps[1]]] if len(caps) >= 2 else []
    body = {"entities": ents + [{"name": "I", "type": "person"}], "triples": trip + [["my", "likes", "Painting"]]}
    shape = i % 20
    if shape == 0:
        return json.dumps(body)
    if shape == 1:
        return "```json\n" + json.dumps(body, indent=2) + "\n```"
    if shape == 2:
        return "Sure! Here you go: " + json.dumps(body) + " Hope that helps {really}."
    if shape == 3:
        return '{"entities": [{"name": "Mel", "type": "person"}, {"name": "Caroline\'s", "type": null}], ' \
               '"triples": [["Mel", "is friends with", "caroline"], ["x", "y"], "abc", ["Mel", "r", "Mel"]]}'
    if shape == 4:
        return "no json at all"
    if shape == 5:
        return '{"entities": [{"name": "broken", }'
    if shape == 6:
        return None
    if shape == 7:
        return '{"entities": "abc", "triples": "xyz"}'
    if shape == 8:
        return '{"entities": ["Melanie", 42, null, ["a", "b"], {"k": 1.5}], "triples": [[1, 2.5, true]]}'
    if shape == 9:
        return '{"entities": [{"name": "melanei", "type": "Person"}, {"name": "carolin"}, ' \
               '{"name": "' + "x" * 70 + '", "type": "long"}, {"type": "orphan"}], "triples": []}'
    if shape == 10:
        return '{"entities": {"Sweden": 1, "Gym": 2}, "triples": {"a": 1}}'
    if shape == 11:
        return '{"entities": [], "triples": [], "extra": {"nested": {"deep": [1, 2, 3]}}}'
    if shape == 12:
        return '{"entities": [{"name": "Art Show!!", "type": "Event / Show"}, ' \
               '{"name": "  The   LGBTQ+  Group  ", "type": "organisation"}], ' \
               '"triples": [["I", "Attended", "art show"], ["me", "joined", "the lgbtq group"]]}'
    if shape == 13:
        return '{"entities": null, "triples": [["Caroline", "has_pet", "Oscar"]]}'
    if shape == 14:
        return '<think>hmm {"entities": []}</think> {"entities": [{"name": "Café Müller", "type": "Place"}]}'
    return json.dumps(body)


async def run_conversation(messages, speakers, cache_path=None, **kw):
    calls = []

    async def fake_generate(session, prompt, **kwargs):
        i = len(calls)
        resp = scripted_response(i, messages[i])
        calls.append({"prompt": prompt, "kwargs": kwargs, "response": resp})
        if resp is None:
            raise RuntimeError("scripted failure")
        return resp

    kg.llm_backend.llm_generate = fake_generate
    result = await kg.extract_conversation(None, messages, speakers, cache_path, **kw)
    return calls, result


def flatten(conv):
    c = conv.get("conversation", conv)
    msgs, i = [], 1
    while f"session_{i}" in c:
        for m in c[f"session_{i}"]:
            msgs.append({"speaker": m.get("speaker", "Unknown"), "text": m.get("text", "")})
        i += 1
    return msgs


def unit_cases():
    names = ["Melanie", "  The LGBTQ+ group's ", "Caroline's", "I", "MY", "mine", "", "!!!", None, 42, 1.5, True,
             ["a", 1], {"k": "v"}, "Café Müller", "rock-climbing 'gym'", "x\ty\nz", "İstanbul"]
    registry = {"melanie": {"type": "person", "count": 3}, "caroline": {"type": "person", "count": 2},
                "art show": {"type": "event", "count": 1}, "lgbtq group": {"type": "", "count": 1},
                "al": {"type": "person", "count": 1}}
    mentions = ["Mel", "Melanie", "melanie's", "carolina", "caro", "art shows", "art", "the lgbtq group",
                "lgbtq groups", "me", "Al", "alfred", "xyz", None, 7]
    parse_inputs = ['{"entities": [{"name": "a"}], "triples": [["a", "b", "c"]]}', "", "{}", "{ bad", "[1, 2]",
                    'pre {"entities": [1, "x"]} mid {"triples": []} post', '{"entities": 0, "triples": false}',
                    '{"entities": {"a": 1}, "triples": "ab"}', '{"triples": [["a","b","c"], ["d"], ("e")]}']
    out = {"norm": [[n, kg._norm(n)] for n in names],
           "resolve": [[m, "Melanie", kg._resolve(m, registry, "Melanie")] for m in mentions],
           "registry": registry, "parse": []}
    for p in parse_inputs:
        out["parse"].append([p, kg._parse(p)])
    for bad in ['{"entities": 5}', '{"triples": 3.5}', '{"entities": true}']:
        try:
            kg._parse(bad)
            out["parse"].append([bad, "no error"])
        except TypeError as e:
            out["parse"].append([bad, {"error": f"TypeError: {e}"}])
    return out


def main():
    data = json.load(open(R.DATASETS / "locomo10.json"))
    conv = data[1]
    msgs = flatten(conv)[:N_MESSAGES]
    speakers = sorted({m["speaker"] for m in msgs})
    calls, result = asyncio.run(run_conversation(msgs, speakers, max_registry=6, log_every=0))

    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "sub" / "conv1.json"
        calls_c, res_c = asyncio.run(run_conversation(msgs[:12], speakers, cache, log_every=5))
        cache_text = cache.read_text()
        calls_hit, res_hit = asyncio.run(run_conversation(msgs[:12], speakers, cache))
        assert not calls_hit and res_hit == json.loads(cache_text)
    # a TypeError escaping the try: "entities": 5
    async def bad_generate(session, prompt, **kwargs):
        return '{"entities": 5}'
    kg.llm_backend.llm_generate = bad_generate
    try:
        asyncio.run(kg.extract_conversation(None, msgs[:2], speakers))
        bad = None
    except TypeError as e:
        bad = f"TypeError: {e}"

    R.dump("kg_extractor.json", {
        "conv_idx": 1, "n_messages": N_MESSAGES, "speakers": speakers,
        "calls": calls, "result": result,
        "cached_run": {"calls": calls_c, "result": res_c, "file": cache_text},
        "type_error": bad,
        "units": unit_cases(),
        "format_check": kg.PROMPT.format(speakers="{a} and {b}", speaker="{x}", text="{{y}}", registry="}"),
        "flatten_lengths": [len(flatten(c)) for c in data],
    })


if __name__ == "__main__":
    main()
