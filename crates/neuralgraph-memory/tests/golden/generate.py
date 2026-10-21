#!/usr/bin/env python3
"""Generate the golden fixtures the Rust port is tested against, from the Python reference implementation.

The reference is the Python package ``NeuralGraph.chat_memory`` as released at ``REFERENCE_COMMIT`` (the last
commit before the port). It is extracted from git into a temporary directory, so this script works on any checkout
even though the repository now keeps only part of that package. Every deterministic piece of it is exercised here
and its outputs written as JSON next to this file; ``tests/golden.rs`` replays the same inputs through the Rust
crate and asserts equality.

Determinism: the clock is pinned (every module's ``utcnow``) and ``new_id`` is replaced by per-prefix counters
(``mem_00000000000000000001``...). The Rust side pins its clock and ids the same way, so whole database dumps,
prompts, MCP and REST responses compare byte for byte.

Run from the repository root:

    python crates/neuralgraph-memory/tests/golden/generate.py

Needs Python >= 3.11 with aiohttp, numpy and rank-bm25 (``pip install -r requirements.txt``) and git. Set
``NG_PYTHON_REFERENCE=/path/to/checkout`` to use an existing tree that contains ``NeuralGraph/`` instead.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# origin/main before the Rust port: the last release of the Python memory server
REFERENCE_COMMIT = "f431110221c009f61fc5973e6e62a86ec467cfc3"


def _reference_root() -> Path:
    override = os.environ.get("NG_PYTHON_REFERENCE")
    if override:
        return Path(override)
    dest = Path(tempfile.mkdtemp(prefix="ng-python-reference-"))
    tar_bytes = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", REFERENCE_COMMIT, "NeuralGraph"],
                               check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        tar.extractall(dest, filter="data")
    return dest


REF = _reference_root()
sys.path.insert(0, str(REF))

import numpy as np  # noqa: E402

import NeuralGraph.chat_memory.extraction as extraction_mod  # noqa: E402
import NeuralGraph.chat_memory.models as models_mod  # noqa: E402
import NeuralGraph.chat_memory.retrieval as retrieval_mod  # noqa: E402
import NeuralGraph.chat_memory.service as service_mod  # noqa: E402
import NeuralGraph.chat_memory.store as store_mod  # noqa: E402
import NeuralGraph.chat_memory.worker as worker_mod  # noqa: E402
from NeuralGraph import temporal_utils  # noqa: E402
from NeuralGraph.chat_memory import jsonutil, textutil  # noqa: E402
from NeuralGraph.chat_memory.extraction import (  # noqa: E402
    EntityRegistry,
    ExtractionConfig,
    MemoryExtractor,
    parse_when,
)
from NeuralGraph.chat_memory.llm import FakeLLMClient, fake_embedding  # noqa: E402
from NeuralGraph.chat_memory.mcp_server import MCPProtocol  # noqa: E402
from NeuralGraph.chat_memory.models import Memory  # noqa: E402
from NeuralGraph.chat_memory.service import ChatMemory, ChatMemoryConfig  # noqa: E402
from NeuralGraph.chat_memory.store import ChatMemoryStore, ExtractionPlan  # noqa: E402
from NeuralGraph.chat_memory.testing import scripted_fake_llm, scripted_responder  # noqa: E402

SAMPLE = ROOT / "demo" / "data" / "sample_chats.jsonl"
INGEST_NOW = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)
QUERY_NOW = datetime(2026, 10, 15, 12, 30, 0, tzinfo=timezone.utc)


def _finite(v):
    """JSON has no inf/nan: encode them as {"float": "inf"} so serde can read every fixture."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return {"float": repr(v)}
    if isinstance(v, list):
        return [_finite(x) for x in v]
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    return v


def write(name: str, data) -> None:
    text = json.dumps(_finite(data), ensure_ascii=False, indent=1, sort_keys=False, allow_nan=False)
    (HERE / name).write_text(text + "\n", encoding="utf-8")
    print(f"wrote {name}")


# ---------------------------------------------------------------------------------------------- determinism hooks
_clock = {"now": INGEST_NOW}


def pin_clock(dt: datetime) -> None:
    _clock["now"] = dt


def _utcnow() -> datetime:
    return _clock["now"]


for _m in (models_mod, store_mod, retrieval_mod, service_mod, worker_mod):
    _m.utcnow = _utcnow

_ids: dict[str, int] = {}
_id_start = {"n": 0}


def pin_ids(start: int) -> None:
    _ids.clear()
    _id_start["n"] = start


def _new_id(prefix: str) -> str:
    _ids[prefix] = _ids.get(prefix, _id_start["n"]) + 1
    return f"{prefix}_{_ids[prefix]:020d}"


for _m in (models_mod, store_mod, extraction_mod):
    _m.new_id = _new_id


# ---------------------------------------------------------------------------------------------- db dumps
TABLES = [
    ("meta", "key"), ("chats", "chat_id"), ("messages", "rid"), ("memories", "rid"),
    ("memory_sources", "memory_id, message_id"), ("entities", "entity_id"), ("entity_aliases", "alias"),
    ("memory_entities", "memory_id, entity_id"), ("relations", "relation_id"),
    ("memory_links", "source_id, target_id, link_type"), ("jobs", "job_id"), ("audit_log", "id"),
    ("sqlite_sequence", "name"),
]


def dump_db(path: str | Path) -> dict:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    out = {}
    for table, order in TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        out[table] = [{k: (r[k].hex() if isinstance(r[k], bytes) else r[k]) for k in r.keys()} for r in rows]
    out["memories_fts_count"] = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    conn.close()
    return out


def sample_records() -> list[dict]:
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]


async def ingest_like_cli(cm: ChatMemory, records: list[dict]) -> None:
    """Exactly what ``cli.cmd_ingest`` did for each JSONL record."""
    for rec in records:
        await cm.add_message(rec.get("chat_id"), rec.get("speaker") or rec.get("role") or "user", rec.get("text", ""),
                             role=rec.get("role", ""), sent_at=rec.get("sent_at") or rec.get("timestamp"),
                             message_id=rec.get("message_id") or rec.get("id"), metadata=rec.get("metadata"))


def plan_dump(plan: ExtractionPlan) -> dict:
    def mem(m: Memory) -> dict:
        d = m.to_dict(include_embedding=True)
        return d
    return {
        "chat_id": plan.chat_id, "job_ids": plan.job_ids,
        "entities": [{"entity_id": e["entity_id"], "name": e["name"], "type": e.get("type", ""), "seen_at": e.get("seen_at"),
                      "mentions": int(e.get("mentions", 1)), "aliases": list(e.get("aliases", ()))} for e in plan.entities],
        "new_memories": [{"memory": mem(m), "sources": list(s), "entity_ids": list(e)} for m, s, e in plan.new_memories],
        "supersedes": [list(x) for x in plan.supersedes],
        "duplicate_sources": [[a, list(b)] for a, b in plan.duplicate_sources],
        "relations": [list(r) + ([None] if len(r) == 6 else []) for r in plan.relations],
        "links": [list(x) for x in plan.links],
        "processed_message_ids": plan.processed_message_ids,
        "skipped_message_ids": plan.skipped_message_ids,
        "audit": [[k, r, d] for k, r, d in plan.audit],
    }


# ---------------------------------------------------------------------------------------------- pure functions
TEXTS = [
    "", "   ", "ok", "thanks!", "lol", "Hi!", "Sure! Let me know if you need anything else.", "good morning",
    "I moved to Berlin", "My cat is called Luna", "42", "Can you remind me what Mel said about Tokyo?",
    "I moved to Berlin in March 2024, it's great", "Zürich Müller", "São Paulo", "  Melanie's ", "The Beatles'",
    "What is Mel's job?", "Ali's brother Jörg Müller lives in Zürich.", "running cities visited planned buses glass",
    "hated cooking lives", "I'm Ali. I just moved from Baku to Berlin last month for a nursing job at Charité.",
    "Sure, of course! Happy to help.", "yes yes ok thanks", "hmmmm", "hahaha!!", "k", "Ok.", "I'm 34 and I have 2 kids.",
    "x" * 30, "Ünïcödé — test… “quotes” and 'apostrophes' don't break it's tokens",
    "the-the 'quoted' --dash-- 12 345 a1 b2 c3", "ΣΊΣΥΦΟΣ İstanbul ǅ ß straße", "tabs\tand\nnewlines\r\nhere\x1c\x1f",
    "Long time no see! How are you?", "you're welcome", "Let me know if you have questions about the trip",
    "Perfect, thanks!", "My sister Leyla is visiting from Baku next weekend.",
]


def gen_textutil() -> None:
    out = {"cases": [], "stem": {}, "norm_relation": {}, "content_hash": [], "clip": []}
    for t in TEXTS:
        out["cases"].append({
            "text": t, "normalize_ws": textutil.normalize_ws(t), "fold": textutil.fold(t),
            "tokenize": textutil.tokenize(t), "tokenize_nostem": textutil.tokenize(t, do_stem=False),
            "tokenize_nonum": textutil.tokenize(t, keep_numbers=False),
            "fts_match_string": textutil.fts_match_string(t), "norm_entity": textutil.norm_entity(t),
            "norm_relation": textutil.norm_relation(t), "is_probable_chitchat": textutil.is_probable_chitchat(t),
        })
    for w in ["lives", "running", "cities", "visited", "planned", "buses", "glass", "hated", "cooking", "ties", "dies",
              "boxes", "dishes", "going", "sing", "bled", "agreed", "hopping", "filled", "fizzed", "rated", "troubled",
              "sized", "hoped", "fixing", "news", "is", "was", "bus", "abilities", "studying", "boxed", "played"]:
        out["stem"][w] = textutil.stem(w)
    for r in ["Lives In", "works_at", "  has-pet!! ", "", "a" * 80, "Ünïcode rel", "--x--"]:
        out["norm_relation"][r] = textutil.norm_relation(r)
    for parts in [[], [""], ["a"], ["a", "b"], ["ab"], ["chat", "Ali", "I moved", "2026-01-01", ""], ["ünï", "€"]]:
        out["content_hash"].append({"parts": parts, "hash": textutil.content_hash(*parts)})
    for t, n in [("hello world", 5), ("hello world", 50), ("a  b  c  d", 4), ("ünïcödé text", 4), ("", 3), ("abc", 0), ("abc", 1)]:
        out["clip"].append({"text": t, "limit": n, "out": textutil.clip(t, n)})
    write("textutil.json", out)


JSON_INPUTS = [
    'Sure! Here is the JSON:\n```json\n{"memories": [{"text": "a",}, ],}\n```\nHope this helps.',
    '<think>reasoning</think>{"memories": [{"text": "one"}, {"text": "tw',
    '[["a","b","c"]] trailing', "no json here", "{}", "[]", '{"text": "he said {hi} and \\"bye\\"", "n": 1}',
    '```\n{"a": 1}\n```', '```JSON {"a": [1, 2, 3,]} ```', 'prefix {"a": {"b": [1, {"c": "d"}]}} suffix {"x": 2}',
    '{"a": "unterminated', '{"a": 1, "b":', '{"a": [1, 2', '[1, 2, {"a": "b"', '{"a": 1.5e3, "b": -0.0, "c": true, "d": null}',
    '{"memories": []}', 'I cannot help with that.', '<think>{"no": 1}</think> {"yes": 2}', '{"dup": 1, "dup": 2}',
    '{"ü": "ñ", "emoji": "\\ud83d\\ude00"}', ' \n {"a": "b"} \n ', '{"a": 1}}', '{{"a": 1}', '"just a string"',
]
SCALARS = [None, "x", "  a  b  ", "ü" * 70, 1, 1.5, 0.1, 1e-7, 1e16, True, False, [1, "a"], {"k": "v", "n": [1, 2]}, {}, "",
           "0.9", " 0.4 ", "high", "1_000", "nan", "inf", "-Infinity", "2", -3, 1e300, 0.0]


def gen_jsonutil() -> None:
    out = {"parse": [], "as_str": [], "as_float": [], "as_list": []}
    for t in JSON_INPUTS:
        out["parse"].append({"text": t, "object": jsonutil.parse_json_object(t), "array": jsonutil.parse_json_array(t)})
    for v in SCALARS:
        out["as_str"].append({"value": v, "limit": 0, "out": jsonutil.as_str(v)})
        out["as_str"].append({"value": v, "limit": 5, "out": jsonutil.as_str(v, 5)})
        f = jsonutil.as_float(v, 0.5)
        out["as_float"].append({"value": v, "out": f})
        out["as_list"].append({"value": v, "out": jsonutil.as_list(v)})
    write("jsonutil.json", out)


BASES = ["2026-09-17T10:00:00+00:00", "2023-05-08T13:56:00+00:00", "2026-01-01T00:30:00+02:00",
         "2024-02-29T23:59:59-05:00", "2026-03-02T09:12:00+00:00", "2026-12-31T12:00:00Z", "2025-06-15T08:00:00"]
PHRASES = [
    "I went there yesterday", "last night was fun", "last weekend we hiked", "last week I started", "this week is busy",
    "last weekend and last week", "last year I moved", "last month we met", "this month I travel", "this year is good",
    "next month I go", "a few weeks ago", "few weeks ago", "two weeks ago", "2 weeks ago", "3 days ago and 10 days ago",
    "two days ago", "last Monday", "last friday and last Fri.", "this sat", "next tues", "on wed", "on Weds we met",
    "today and tonight", "tomorrow", "this morning", "this afternoon and this evening", "earlier today",
    "the other day", "a couple days ago", "couple of days ago", "the day before yesterday", "next week",
    "over the weekend", "during the weekend", "three days ago", "five days ago", "a week ago", "ten years ago",
    "5 years ago", "on thursday next week", "nothing relative here", "Yesterday and YESTERDAY",
    "last weekend [= already]", "on Sunday, last Sunday and this Sunday", "on the weekend", "on su", "last mo",
    "I said thanks today [= 1 Jan 2020]", "99999999999 days ago",
]
WHENS = [
    "2024-03-15", "2024-03", "2024", "March 2024", "8 May 2023", "May 8, 2023", "null", "2024-13-01", "yesterday",
    "next summer", "last year", "this year", "next year", "a year ago", "two years ago", "2024-02-30", "1899",
    "2101", "sept 2024", "sep 2024", "8 may, 2023", "may 8 2023", "  \"2024-01-05\"  ", "2024-01-05T10:00:00",
    "2024-01-05 10:00", "last month", "a few weeks ago", "last weekend", "today", "tomorrow", "N/A", "-", "",
    "31 feb 2024", "last monday", "two weeks ago", "10 days ago", "5 years ago", "this week", "jan 2025",
]
FLEX = ["1:56 pm on 8 May, 2023", "10:15 am on 27 January, 2024", "12:00 am on 1 June, 2023", "2023-05-08T13:56:00",
        "2023-05-08", "8 May 2023", "May 8, 2023", "May 8 2023", "around 16 September 2026", "week of 14 September 2026",
        "7-8 March 2026", "September 2026", "2025", "31 February 2023", "garbage", "", "2026-03-02T09:12:00+00:00",
        "13:00 pm on 8 May, 2023"]
ISO_INPUTS = ["2026-09-17T08:00:00+00:00", "2026-09-17T08:00:00Z", "2026-09-17", "2026-09-17T08:00", "2026-09-17T08",
              "2026-09-17 08:00:00", "2026-09-17T08:00:00.123+02:00", "2026-09-17T08:00:00.123456-05:30", "20260917",
              "20260917T080000", "2026-09-17T08:00:00+0200", "garbage", "", "2026-13-01", "2026-09-17T25:00:00",
              "2026-09-17x08:00:00", "2026-09-17T08:00:00.1", "2026-09-17T08:00:00,5"]


def gen_temporal() -> None:
    out = {"resolve": [], "flexible": [], "parse_when": [], "parse_iso": [], "older": []}
    for b in BASES:
        base = models_mod.parse_iso(b).replace(tzinfo=None)
        for p in PHRASES:
            try:
                text, vals = temporal_utils.resolve_relative_dates(p, base)
                out["resolve"].append({"base": b, "text": p, "out": text, "resolved": vals, "error": None})
            except Exception as exc:  # noqa: BLE001
                out["resolve"].append({"base": b, "text": p, "out": None, "resolved": None, "error": type(exc).__name__})
    for s in FLEX:
        try:
            dt = temporal_utils.parse_datetime_flexible(s)
            out["flexible"].append({"text": s, "iso": dt.isoformat() if dt else None, "error": None})
        except Exception as exc:  # noqa: BLE001
            out["flexible"].append({"text": s, "iso": None, "error": type(exc).__name__})
    for w in WHENS:
        for b in [None] + BASES:
            out["parse_when"].append({"when": w, "observed_at": b, "out": list(parse_when(w, b))})
    for s in ISO_INPUTS:
        dt = models_mod.parse_iso(s)
        out["parse_iso"].append({"text": s, "iso": dt.isoformat() if dt else None})
    for a, b in [("2026-05-01T12:00:00+02:00", "2026-05-01T09:00:00+00:00"), ("2026-05-01T08:00:00+02:00", "2026-05-01T09:00:00+00:00"),
                 ("", "2026-01-01"), ("x", "y"), ("2026-01-01", "garbage"), ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")]:
        out["older"].append({"a": a, "b": b, "out": extraction_mod._older(a, b)})
    write("temporal.json", out)


def gen_pycompat() -> None:
    floats = [0.1, 1.0, 1e16, 1e15, 0.0001, 0.00001, 1.5e-7, 123456789012345678.0, -2.5, 0.30000000000000004, 0.0, -0.0,
              5e-324, 1.7976931348623157e308, 0.6931471805599453, 2 / 3, 100.0, 1234.5678, 0.7, 0.9, 1e22, 1e-5, 12345678901234567.0]
    rounds = [(x, n) for x in [0.125, 0.375, 2.5, 0.5, 1.5, 0.0625, 1.005, 2.675, 0.785, 0.123456789, 0.99995, 1 / 3,
                               0.0, -0.001, 12.3456, 0.6999999880790710] for n in (0, 1, 2, 3, 4, 5, 6)]
    values = [{"a": 1, "b": [1.5, "é", None, True], "c": {"z": 1, "y": 2}}, [], {}, "ü\n\t\"\\\x01\x7f 😀", 1e16, -0.0]
    strings = ["abc", "it's", 'say "hi"', "both ' and \"", "tab\there", "nl\n", "ü€😀", "\x00\x1f\x7f\x85\xa0", "back\\slash"]
    write("pycompat.json", {
        "float_repr": [{"x": x, "repr": repr(x)} for x in floats],
        "round": [{"x": x, "n": n, "out": round(x, n), "fixed": f"{x:.{n}f}"} for x, n in rounds],
        "dumps": [{"value": v, "default": json.dumps(v), "unicode": json.dumps(v, ensure_ascii=False),
                   "stored": json.dumps(v, ensure_ascii=False, sort_keys=True),
                   "pretty": json.dumps(v, ensure_ascii=False, indent=1),
                   "compact": json.dumps(v, ensure_ascii=False, separators=(",", ":"))} for v in values],
        "repr_str": [{"s": s, "repr": repr(s)} for s in strings],
        "float_parse": [{"s": s, "out": (lambda s: (lambda f: None if f != f else f)(float(s)) if _floatable(s) else "error")(s)}
                        for s in ["1", " 0.9 ", "1_000", "1__0", "_1", "1e3", ".5", "5.", "+5", "--5", "inf", "-Infinity",
                                  "nan", "high", "", "0x10", "1e", "١"]],
    })


def _floatable(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def gen_difflib() -> None:
    import difflib
    words = ["berlin", "berlín", "melanie carter", "melanie", "tokyo", "prenzlauer berg", "kreuzberg", "luna",
             "charite", "charité", "leyla", "jonas", "ali novruzov", "ali", "baku", "hamburg", "osaka", "kyoto",
             "a" * 210 + "b", "ab" * 120, "abcdef", "fedcba", "the quick brown fox", "quick brown the fox"]
    pairs = []
    for a in words:
        for b in words:
            s = difflib.SequenceMatcher(None, a, b)
            pairs.append({"a": a, "b": b, "ratio": s.ratio(), "quick": s.quick_ratio(), "real_quick": s.real_quick_ratio()})
    close = []
    for w in words + ["berln", "melani carter", "tokio", "kreuzbergg", "charitee"]:
        for cutoff in (0.6, 0.8, 0.9):
            close.append({"word": w, "cutoff": cutoff, "n": 3, "out": difflib.get_close_matches(w, words, n=3, cutoff=cutoff)})
    write("difflib.json", {"pairs": pairs, "close": close})


def gen_registry() -> None:
    cases = []
    r = EntityRegistry()
    ops = [
        ("add", "melanie carter", "Melanie Carter", "person", 10, ["mel"]),
        ("add", "berlin", "Berlin", "place", 5, []),
        ("resolve", "Mel", None, "", True), ("resolve", "Melanie", None, "", True), ("resolve", "melanie carter's", None, "", True),
        ("resolve", "I", "ali", "", True), ("resolve", "Berlín", None, "", True), ("resolve", "Tokyo", None, "city", True),
        ("resolve", "May", None, "", True), ("resolve", "2024", None, "", True), ("resolve", "", None, "", True),
        ("add", "alex smith", "Alex Smith", "person", 3, []), ("add", "alex jones", "Alex Jones", "person", 3, []),
        ("resolve", "Alex", None, "", True), ("resolve", "Alex Jones", None, "person", True), ("resolve", "the user", "ali", "", True),
        ("resolve", "Dr. Smith", None, "person", False), ("resolve", "Kreuzberg", None, "Place", True), ("resolve", "kreuzbergg", None, "", True),
        ("resolve", "my", None, "", True), ("resolve", "x", None, "", True), ("resolve", "12-34", None, "", True),
        ("resolve", "assistant", None, "", True), ("resolve", "Luna", None, "Dog", True), ("resolve", "Charité", None, "organisation", True),
        ("resolve", "charite", None, "", False), ("add", "ali", "Ali", "", 2, ["ali n", "Ali Novruzov"]), ("resolve", "Ali N", None, "", True),
        ("resolve", "ali novruzov", None, "", True), ("resolve", "Ali", None, "", True), ("resolve", {"x": 1}, None, "", True),
        ("resolve", 42, None, "", True), ("resolve", "Pottery Studio", None, "hobby class", True),
    ]
    for op in ops:
        if op[0] == "add":
            r.add(op[1], op[2], op[3], op[4], aliases=op[5])
            cases.append({"op": "add", "id": op[1], "name": op[2], "type": op[3], "count": op[4], "aliases": op[5]})
        else:
            got = r.resolve(op[1], speaker_id=op[2], etype=op[3], create=op[4])
            cases.append({"op": "resolve", "mention": op[1], "speaker_id": op[2], "etype": op[3], "create": op[4], "out": got})
    cases.append({"op": "state", "prompt_text": r.prompt_text(80),
                  "new_entities": {k: v for k, v in r.new_entities.items()}, "aliases": dict(r.aliases)})
    types = {t: extraction_mod.norm_type(t) for t in ["person", "People", "city", "big city", "hobby class", "", "unknown thing",
                                                      "University of X", "pet", "Dog", "media", "  "]}
    write("registry.json", {"cases": cases, "norm_type": types})


def gen_fake_llm(prompts: list[str]) -> None:
    texts = ["Ali moved to Berlin", "weather nice", "", "!!!", "Ali has a cat called Luna.", "Zürich Müller running cities",
             "embedding dimension probe", "x" * 100]
    emb = [{"text": t, "dim": d, "vec": fake_embedding(t, d)} for t in texts for d in (256, 128, 3)]
    synthetic = [
        "You maintain the long-term memory\nNEW MESSAGES (date: 2026-01-01):\n1. [Ali]: I'm a nurse and I love my cat Luna. My sister Leyla lives in Baku.\n2. [assistant (assistant)]: Nice!\n\nReturn ONLY JSON",
        "You are building a knowledge graph\nNEW MESSAGES (date: 2026-01-01):\n1. [Ali]: I moved from Baku to Berlin. My daughter Aysel is allergic to peanuts and I'm vegetarian.\n\nReturn ONLY JSON",
        "You maintain a memory store about people.\nNEW (observed 2026-05-01): \"Ali moved to Tokyo.\"\n\nEXISTING:\nA. (observed 2026-01-01) \"Ali lives in Berlin.\"\n",
        "You maintain a memory store about people.\nNEW (observed 2026-05-01): \"ali lives in berlin.\"\n\nEXISTING:\nA. (observed 2026-01-01) \"Ali lives in Berlin.\"\n",
        "something else entirely",
    ]
    responses = [{"prompt": p, "response": scripted_responder(p)} for p in synthetic + prompts]
    write("fake_llm.json", {"embeddings": emb, "responder": responses,
                            "third_person": [{"text": t, "name": n, "out": __import__("NeuralGraph.chat_memory.testing", fromlist=["third_person"]).third_person(t, n)}
                                             for t, n in [("I am tired", "Ali Novruzov"), ("my cat and I", "Ali"), ("We went", "Mel"),
                                                          ("I'm sure it's mine", "Ali"), ("Me too", "Ali")]]})


def gen_prompts() -> None:
    write("prompts.json", {n.lstrip("_"): getattr(extraction_mod, n) for n in
                           ["MEMORY_PROMPT", "ASSISTANT_RULE_OFF", "ASSISTANT_RULE_ON", "ASSISTANT_SKIP_OFF",
                            "ASSISTANT_SKIP_ON", "RELATION_PROMPT", "RECONCILE_PROMPT", "_REPAIR_SUFFIX"]})


# ---------------------------------------------------------------------------------------------- full ingest
INGEST_VARIANTS = [
    {"name": "cli_defaults", "batch_size": 2, "user_name": None, "extract_assistant": True},
    {"name": "user_named", "batch_size": 2, "user_name": "Ali Novruzov", "extract_assistant": True},
    {"name": "no_assistant_batch6", "batch_size": 6, "user_name": "Ali Novruzov", "extract_assistant": False},
]


async def gen_ingest(tmp: Path) -> list[str]:
    out = []
    all_prompts: list[str] = []
    for v in INGEST_VARIANTS:
        pin_clock(INGEST_NOW)
        pin_ids(0)
        db = tmp / f"ingest_{v['name']}.db"
        cfg = ChatMemoryConfig()
        cfg.debounce_seconds = 0.0
        cfg.worker.batch_size = v["batch_size"]
        if v["user_name"]:
            cfg.extraction.speaker_names["user"] = v["user_name"]
        cfg.extraction.extract_assistant = v["extract_assistant"]
        llm = scripted_fake_llm()
        cm = ChatMemory(db, llm=llm, config=cfg)
        await ingest_like_cli(cm, sample_records())
        batches = await cm.process_pending()
        metrics = cm.worker.metrics.as_dict()
        status = await cm.status()
        ledger = await cm.token_ledger()
        grade = await cm.grade()
        await cm.close()
        all_prompts.extend(llm.prompts)
        out.append({"variant": v, "batches": batches, "prompts": llm.prompts, "embedded": llm.embedded,
                     "metrics": {k: metrics[k] for k in metrics if k not in ("recent", "started_at", "last_batch_seconds")},
                     "recent": metrics["recent"], "ledger": ledger, "grade": grade,
                     "llm_stats": status["llm"], "db": dump_db(db)})
        if v["name"] == "user_named":
            shutil.copy(db, HERE / "ingest_user_named.sqlite")
    write("ingest.json", out)
    return all_prompts


# ---------------------------------------------------------------------------------------------- retrieval
SEARCHES = [
    ("Where does Ali live now?", {}), ("What is Ali allergic to?", {}), ("Who is Jonas?", {}),
    ("When is Ali's mother's birthday?", {}), ("What are Ali's plans for the autumn?", {}), ("Tell me about Luna", {}),
    ("What can't Ali eat?", {"k": 3}), ("Berlin", {"k": 15}), ("Luna cat", {"channels": "VKG"}), ("berlin", {"channels": "G", "k": 5}),
    ("marathon", {"channels": "K"}), ("marathon training", {"channels": "V"}), ("Ali", {"kinds": ["event"], "k": 20}),
    ("Ali", {"subject": "Ali Novruzov", "k": 20}), ("Leyla", {"subject": "Leyla"}), ("Berlin", {"chat_id": "2026-03-02-onboarding"}),
    ("Where did Ali live?", {"since": "2026-03-01", "until": "2026-03-31"}), ("Ali", {"until": "2026-04-01", "k": 20}),
    ("Ali", {"since": "2026-06", "k": 20}), ("Where does Ali live?", {"include_superseded": True, "k": 10}),
    ("zzzz qqqq", {}), ("", {}), ("Ali", {"with_sources": False}), ("Charité nursing job", {"channels": "VKG", "k": 5}),
    ("vegetarian dinner sister", {"k": 8}), ("Ali", {"speaker": "user", "k": 20}), ("Ali", {"speaker": "Ali Novruzov", "k": 5}),
    ("half marathon", {"kinds": ["plan", "event"], "since": "2026"}), ("Tokyo trip", {"query_embedding": [0.0] * 256}),
    ("Where does Mel live?", {"k": 3}), ("Where does Melanie live?", {"k": 3}), ("pottery", {"subject": "Melanie"}),
    ("visit Berlin", {"since": "2026-10-01", "until": "2026-10-31"}), ("visit Berlin", {"since": "2026-11-01"}),
    ("Where does Ali live?", {"include_superseded": True, "channels": "VKG"}), ("Baku Berlin nursing", {"since": "2026-03-01", "until": "2026-03-31"}),
    ("Sunday", {}), ("Luna balcony vet", {"channels": "VKG", "k": 4}),
]
CONTEXTS = [
    ("Where does Ali live?", {}), ("planning a trip and a dinner with the family", {}), ("Mel pottery", {"max_chars": 260}),
    ("Berlin", {"k": 5, "max_chars": 900}), ("zzzz", {}), ("What does Ali eat?", {"k": 6, "max_chars": 200}),
    ("Ali's cat", {"subject": "Ali Novruzov"}), ("Luna", {"k": 0}),
]


def _search_kwargs(o: dict) -> dict:
    return dict(o)


# writes applied to the copied database before the queries, so history, aliases, links and retractions are covered
RETRIEVAL_SETUP = [
    {"op": "remember", "text": "Mel lives in Tokyo and works at a pottery studio.", "subject": "Mel", "kind": "identity", "importance": 0.9},
    {"op": "remember", "text": "Mel plans to visit Berlin in October 2026.", "subject": "Mel", "kind": "plan", "when": "2026-10"},
    {"op": "upsert_entity", "entity_id": "mel", "name": "Mel", "type": "person", "aliases": ["melanie"]},
    {"op": "supersede", "old": "mem_00000000000000000001", "new": "mem_00000000000000000010", "link": "contradicts"},
    {"op": "add_link", "source": "mem_00000000000000000003", "target": "mem_00000000000000000011", "type": "related", "weight": 0.5},
    {"op": "retract", "memory_id": "mem_00000000000000000007", "reason": "test"},
    {"op": "upsert_relation", "s": "mel", "p": "visits", "o": "berlin", "chat_id": "manual", "memory_id": "mem_00000000000000500002"},
]


async def apply_setup(cm: ChatMemory, steps: list[dict]) -> None:
    for st in steps:
        op = st["op"]
        if op == "remember":
            await cm.remember(st["text"], subject=st["subject"], kind=st["kind"], importance=st.get("importance", 0.8),
                              when=st.get("when"))
        elif op == "upsert_entity":
            await cm.store.upsert_entity(st["entity_id"], st["name"], st["type"], aliases=st["aliases"])
        elif op == "supersede":
            await cm.store.supersede(st["old"], st["new"], st["link"])
        elif op == "add_link":
            await cm.store.add_link(st["source"], st["target"], st["type"], st["weight"])
        elif op == "retract":
            await cm.retract(st["memory_id"], st["reason"])
        elif op == "upsert_relation":
            await cm.store.upsert_relation(st["s"], st["p"], st["o"], chat_id=st["chat_id"], memory_id=st["memory_id"])


async def gen_retrieval(tmp: Path) -> None:
    src = HERE / "ingest_user_named.sqlite"
    db = tmp / "retrieval.db"
    shutil.copy(src, db)
    pin_clock(QUERY_NOW)
    pin_ids(500000)
    llm = FakeLLMClient()
    cfg = ChatMemoryConfig()
    cm = ChatMemory(db, llm=llm, config=cfg)
    out = {"now": QUERY_NOW.isoformat(), "setup": RETRIEVAL_SETUP, "search": [], "context": [], "profile": [],
           "related": [], "timeline": [], "query_entities": []}
    await apply_setup(cm, RETRIEVAL_SETUP)
    for q, o in SEARCHES:
        hits = await cm.search(q, **_search_kwargs(o))
        out["search"].append({"query": q, "opts": o, "results": [h.to_dict() for h in hits]})
    for q, o in CONTEXTS:
        k = o.get("k")
        mc = o.get("max_chars")
        filt = {kk: vv for kk, vv in o.items() if kk not in ("k", "max_chars")}
        block, res = await cm.retriever.context_with_results(q, k=k, max_chars=mc, **filt)
        out["context"].append({"query": q, "opts": o, "block": block, "ids": [r.memory.memory_id for r in res]})
    for s in ["Ali Novruzov", "ali novruzov", "Ali", "Leyla", "Luna", "nobody", "user", "Jonas", "Melanie", "Mel"]:
        out["profile"].append({"subject": s, "limit": 60, "out": await cm.profile(s)})
    conn = sqlite3.connect(db)
    mids = [r[0] for r in conn.execute("SELECT memory_id FROM memories ORDER BY rid")]
    conn.close()
    for mid in mids + ["nope"]:
        out["related"].append({"memory_id": mid, "out": await cm.related(mid)})
    for kw in [{}, {"subject": "Ali Novruzov"}, {"since": "2026-04", "until": "2026-06-30"}, {"subject": "leyla", "limit": 3}]:
        out["timeline"].append({"opts": kw, "ids": [m.memory_id for m in await cm.timeline(**kw)]})
    for q in ["What is Mel's job?", "where does ali novruzov live", "Luna and Leyla in Berlin", "nothing here", "Ali's"]:
        out["query_entities"].append({"query": q, "out": await cm.retriever.query_entities(q)})
    out["ledger"] = await cm.token_ledger()
    out["grade"] = await cm.grade()
    await cm.close()
    out["db_after"] = dump_db(db)
    write("retrieval.json", out)

    # in-process BM25 fallback (no FTS5) over the same database
    db2 = tmp / "bm25.db"
    shutil.copy(src, db2)
    store = ChatMemoryStore(db2)
    store.has_fts = False
    kw = []
    for q, f in [("Berlin", {}), ("marathon training plan", {}), ("Luna vet shots", {}), ("zzz", {}), ("Ali", {"subject": "ali novruzov"}),
                 ("Berlin", {"status": "superseded"}), ("cat", {"kinds": ["fact"]}), ("Baku", {"since": "2026-04-01"}),
                 ("peanuts", {"chat_id": "2026-03-19-food"}), ("the and of", {})]:
        kw.append({"query": q, "filters": f, "out": await store.keyword_candidates(q, 50, **f)})
    await store.close()
    write("bm25.json", kw)


# ---------------------------------------------------------------------------------------------- extraction scenarios
MEMORY_JSON = {"memories": [
    {"text": "Ali moved to Berlin in March 2026.", "kind": "event", "subject": "Ali", "when": "2026-03", "importance": 0.9,
     "confidence": 1.0, "sources": [1], "entities": [{"name": "Berlin", "type": "place"}]},
    {"text": "Ali has a cat called Luna.", "kind": "fact", "subject": "Ali", "when": None, "importance": 0.7,
     "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]},
    {"text": "Ali is tired today.", "kind": "other", "subject": "Ali", "when": None, "importance": 0.1, "confidence": 0.9, "sources": [1]},
    {"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali", "when": None, "importance": 0.6, "confidence": 0.9, "sources": [1]},
    {"text": "The assistant recommends visiting museums.", "kind": "other", "subject": "assistant", "importance": 0.5, "confidence": 0.9, "sources": [2]},
]}
RELATION_JSON = {"entities": [{"name": "Berlin", "type": "place"}, {"name": "Luna", "type": "pet"}],
                 "triples": [{"s": "I", "r": "lives in", "o": "Berlin", "source": 1, "confidence": 0.9},
                             {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.9},
                             {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.5}]}
EMPTY_REL = {"entities": [], "triples": []}
SEED = [{"op": "add_message", "chat_id": "c1", "speaker": "Ali", "text": "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.", "sent_at": "2026-04-01T10:00:00+00:00"},
        {"op": "add_message", "chat_id": "c1", "speaker": "assistant", "text": "Berlin has great museums, you should visit them.", "role": "assistant", "sent_at": "2026-04-01T10:00:10+00:00"}]


def script(memories=None, relations=None, reconcile=None) -> dict:
    return {"script": [["You maintain the long-term memory", json.dumps(memories if memories is not None else MEMORY_JSON)],
                       ["You are building a knowledge graph", json.dumps(relations if relations is not None else RELATION_JSON)],
                       ["You maintain a memory store", json.dumps(reconcile or {"decision": "ADD", "target": None})]]}


def cat_memory_step(**kw) -> dict:
    d = {"op": "insert_memory", "text": "Ali has a cat called Luna.", "subject": "ali", "chat_id": "c1",
         "observed_at": "2026-03-01T00:00:00+00:00", "embedding_text": "Ali has a cat called Luna.", "kind": "fact",
         "importance": 0.7, "event_time": "2026-02", "event_time_precision": "month", "sources": [], "entities": ["ali", "luna"]}
    d.update(kw)
    return d


SCENARIOS = [
    {"name": "plan_from_scripted_llm", "steps": SEED + [{"op": "build_plan", "messages": [0, 1], **script()}]},
    {"name": "gate_assistant_only_opt_out", "steps": [
        {"op": "add_message", "chat_id": "c1", "speaker": "Ali", "text": "thanks!!"},
        {"op": "add_message", "chat_id": "c1", "speaker": "assistant", "text": "You're welcome, anything else?", "role": "assistant"},
        {"op": "build_plan", "messages": [0, 1], "config": {"extract_assistant": False}, **script()},
        {"op": "add_message", "chat_id": "c1", "speaker": "assistant", "text": "Berlin has three opera houses and a famous zoo.", "role": "assistant"},
        {"op": "build_plan", "messages": [2], "config": {"extract_assistant": False}, **script()},
        {"op": "build_plan", "messages": [1], **script()},
        {"op": "build_plan", "messages": [2], **script()}]},
    {"name": "duplicate_exact", "steps": SEED + [
        {"op": "build_plan", "messages": [0, 1], "apply": True, **script()},
        {"op": "add_message", "chat_id": "c2", "speaker": "Ali", "text": "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.", "sent_at": "2026-05-01T10:00:00+00:00"},
        {"op": "build_plan", "messages": [2], "apply": True, **script()}]},
    {"name": "contradict_supersedes_when_newer", "steps": SEED + [
        {"op": "build_plan", "messages": [0, 1], "apply": True, **script()},
        {"op": "add_message", "chat_id": "c2", "speaker": "Ali", "text": "I moved to Tokyo in June 2026.", "sent_at": "2026-07-01T10:00:00+00:00"},
        {"op": "build_plan", "messages": [2], "apply": True, "config": {"reconcile_cosine": 0.3},
         **script({"memories": [{"text": "Ali moved to Tokyo in June 2026.", "kind": "event", "subject": "Ali", "when": "2026-06", "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Tokyo", "type": "place"}]}]},
                  EMPTY_REL, {"decision": "CONTRADICT", "target": "A", "text": None, "reason": "moved again"})}]},
    {"name": "older_observation_cannot_supersede", "steps": SEED + [
        {"op": "build_plan", "messages": [0, 1], "apply": True, **script()},
        {"op": "add_message", "chat_id": "old-chat", "speaker": "Ali", "text": "I moved to Paris in 2020.", "sent_at": "2020-05-01T10:00:00+00:00"},
        {"op": "build_plan", "messages": [2], "apply": True, "config": {"reconcile_cosine": 0.3},
         **script({"memories": [{"text": "Ali moved to Paris in 2020.", "kind": "event", "subject": "Ali", "when": "2020", "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Paris", "type": "place"}]}]},
                  EMPTY_REL, {"decision": "CONTRADICT", "target": "A"})}]},
    {"name": "update_merges_text", "steps": SEED + [
        {"op": "build_plan", "messages": [0, 1], "apply": True, **script()},
        {"op": "add_message", "chat_id": "c2", "speaker": "Ali", "text": "My cat Luna is a grey British shorthair.", "sent_at": "2026-05-01T10:00:00+00:00"},
        {"op": "build_plan", "messages": [2], "apply": True, "config": {"reconcile_cosine": 0.3},
         **script({"memories": [{"text": "Ali has a cat called Luna, a grey British shorthair.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]},
                  EMPTY_REL, {"decision": "UPDATE", "target": "B", "text": "Ali has a grey British shorthair cat called Luna."})}]},
    {"name": "embedding_outage", "steps": SEED + [{"op": "build_plan", "messages": [0, 1], "apply": True, "embed_error": "embedding server down", **script()}]},
    {"name": "unparseable_output", "steps": SEED + [
        {"op": "build_plan", "messages": [0, 1], "default_response": "I cannot help with that."},
        {"op": "build_plan", "messages": [0, 1], "attempt": 2, "default_response": "I cannot help with that."},
        {"op": "build_plan", "messages": [0, 1], "attempt": 4, "default_response": "{\"memories\": []}"}]},
    {"name": "hallucinated_name_in_entities", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "I met someone interesting at the market yesterday.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "build_plan", "messages": [0], **script({"memories": [{"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali", "importance": 0.6, "confidence": 0.9, "sources": [1], "entities": [{"name": "Zorblax Quintrell", "type": "person"}]}]}, EMPTY_REL)}]},
    {"name": "relation_endpoints_grounded", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "I work at Charite as a nurse.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "build_plan", "messages": [0], **script({"memories": []}, {"entities": [{"name": "Charite", "type": "org"}, {"name": "Globex Corp", "type": "org"}],
                                                                           "triples": [{"s": "I", "r": "works_at", "o": "Charite", "source": 1, "confidence": 0.9},
                                                                                       {"s": "I", "r": "friend_of", "o": "Bob Zimmer", "source": 1, "confidence": 0.9}]})}]},
    {"name": "short_identity_facts", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "I'm 34 and I have 2 kids.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "build_plan", "messages": [0], **script({"memories": [
            {"text": "Ali is 34 years old.", "kind": "identity", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]},
            {"text": "Ali has 2 kids.", "kind": "fact", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]}]}, EMPTY_REL)}]},
    {"name": "assistant_subject_rejected", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "You're really helpful and patient, thank you so much for the detailed answer.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "build_plan", "messages": [0], **script({"memories": [{"text": "The assistant is helpful and patient.", "kind": "opinion", "subject": "assistant", "importance": 0.6, "confidence": 0.9, "sources": [1]}]}, EMPTY_REL)}]},
    {"name": "missing_sources_default_human", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "I adopted a dog called Rex last week.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "add_message", "chat_id": "c", "speaker": "assistant", "text": "Congratulations on adopting Rex!", "role": "assistant", "sent_at": "2026-05-02T10:00:10+00:00"},
        {"op": "build_plan", "messages": [0, 1], **script({"memories": [{"text": "Ali adopted a dog called Rex.", "kind": "event", "subject": "Ali", "importance": 0.7, "confidence": 0.9}]}, EMPTY_REL)}]},
    {"name": "backslash_subject", "steps": [
        {"op": "add_message", "chat_id": "c", "speaker": "Ali", "text": "I love hiking in the Alps every summer.", "sent_at": "2026-05-02T10:00:00+00:00"},
        {"op": "build_plan", "messages": [0], **script({"memories": [{"text": "I love hiking in the Alps.", "kind": "preference", "subject": "Ali\\u00e9", "importance": 0.7, "confidence": 0.9, "sources": [1]}]}, EMPTY_REL)}]},
    {"name": "update_keeps_target_metadata", "steps": [
        {"op": "add_message", "chat_id": "c0", "speaker": "Ali", "text": "I have a cat called Luna.", "sent_at": "2026-03-01T00:00:00+00:00"},
        cat_memory_step(sources_from_messages=[0]),
        {"op": "add_message", "chat_id": "c1", "speaker": "Ali", "text": "Luna is a grey British shorthair.", "sent_at": "2026-05-01T00:00:00+00:00"},
        {"op": "build_plan", "messages": [1], "config": {"reconcile_cosine": 0.2},
         **script({"memories": [{"text": "Ali's cat Luna is a grey British shorthair.", "kind": "other", "subject": "Ali", "importance": 0.5, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]},
                  EMPTY_REL, {"decision": "UPDATE", "target": "A", "text": "Ali has a grey British shorthair cat called Luna."})}]},
    {"name": "two_candidates_chain", "steps": [
        {"op": "add_message", "chat_id": "c0", "speaker": "Ali", "text": "I have a cat called Luna.", "sent_at": "2026-03-01T00:00:00+00:00"},
        cat_memory_step(sources_from_messages=[0]),
        {"op": "add_message", "chat_id": "c1", "speaker": "Ali", "text": "Actually Luna is 4, and she is a British shorthair.", "sent_at": "2026-05-01T00:00:00+00:00"},
        {"op": "build_plan", "messages": [1], "apply": True, "config": {"reconcile_cosine": 0.2, "dedupe_cosine": 0.99},
         **script({"memories": [
             {"text": "Ali's cat Luna is 4 years old.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]},
             {"text": "Ali's cat Luna is a British shorthair.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]}]},
                  EMPTY_REL, {"decision": "UPDATE", "target": "A", "text": None})}]},
    {"name": "weird_model_output", "steps": [
        {"op": "add_message", "chat_id": "w", "speaker": "user", "text": "My partner Sam and I live in Lisbon; we adopted two cats, Miso and Tofu, in May 2025.", "role": "user", "sent_at": "2026-06-10T09:00:00+00:00"},
        {"op": "add_message", "chat_id": "w", "speaker": "assistant", "text": "Lovely! For cats in Lisbon, try the Clinica Veterinaria do Areeiro; dilute tea tree oil 1:10 never on cats.", "role": "assistant", "sent_at": "2026-06-10T09:00:30+00:00"},
        {"op": "build_plan", "messages": [0, 1], "config": {"speaker_names": {"user": "Nina Petrova"}},
         **script({"memories": [
             {"memory": "I live in Lisbon with my partner Sam.", "kind": "Facts", "subject": "I", "when": "", "importance": "0.9", "confidence": "high", "source": "1", "entities": ["Lisbon", {"name": "Sam", "type": "Person"}]},
             {"fact": "Nina adopted two cats, Miso and Tofu, in May 2025.", "kind": "events", "subject": "the user", "when": "May 2025", "importance": 2, "confidence": 0.95, "sources": [1, 1, 9, "x"], "entities": [{"name": "Miso", "type": "cat"}, {"name": "Tofu", "type": "Animal"}]},
             {"text": "The user was recommended Clinica Veterinaria do Areeiro in Lisbon.", "kind": "todo", "subject": "user", "when": "yesterday", "importance": 0.7, "confidence": 0.9, "sources": [2], "entities": [{"name": "Clinica Veterinaria do Areeiro", "type": "venue"}]},
             {"text": "short", "kind": "fact"}, "not a dict", {"text": "Nina thinks tea tree oil is dangerous for cats [= 1 May 2025].", "kind": "belief", "subject": "Nina Petrova", "importance": 0.6, "sources": [2]},
             {"text": "We plan to visit Porto next month.", "kind": "goal", "subject": "", "when": "next month", "importance": 0.7, "confidence": 0.8, "sources": [1]}]},
                  {"entities": [{"name": "Lisbon", "type": "city"}, "Sam", {"name": "", "type": "x"}, {"name": "Clinica Veterinaria do Areeiro", "type": "org"}],
                   "triples": [["Nina", "lives in", "Lisbon"], {"subject": "I", "relation": "Partner Of", "object": "Sam", "source": "1", "confidence": "0.8"},
                               {"s": "user", "r": "has_pet", "o": "Miso", "source": 1}, {"s": "Sam", "r": "", "o": "Lisbon"}, {"s": "Lisbon", "r": "x", "o": "Lisbon"}, 7]},
                  {"decision": "add"})}]},
]


async def run_scenario(sc: dict, tmp: Path) -> dict:
    pin_clock(INGEST_NOW)
    pin_ids(0)
    store = ChatMemoryStore(tmp / f"sc_{sc['name']}.db")
    msgs = []
    results = []
    for step in sc["steps"]:
        op = step["op"]
        if op == "add_message":
            m, _ = await store.add_message(step["chat_id"], step["speaker"], step["text"], role=step.get("role", ""),
                                           sent_at=step.get("sent_at"))
            msgs.append(m)
        elif op == "insert_memory":
            now = models_mod.now_iso()
            mem = Memory(memory_id=_new_id("mem"), text=step["text"], kind=step["kind"], subject=step["subject"],
                         subject_name=step["subject"].title(), speaker=step["subject"].title(), chat_id=step["chat_id"],
                         importance=step["importance"], confidence=0.9, event_time=step.get("event_time"),
                         event_time_precision=step.get("event_time_precision", "none"), observed_at=step["observed_at"],
                         created_at=now, updated_at=now, text_hash=textutil.content_hash(step["text"].lower()),
                         embedding=fake_embedding(step["embedding_text"]))
            srcs = [msgs[i].message_id for i in step.get("sources_from_messages", [])]
            await store.insert_memory(mem, source_message_ids=srcs, entity_ids=step["entities"])
        elif op == "build_plan":
            cfg = ExtractionConfig()
            for k, v in step.get("config", {}).items():
                setattr(cfg, k, v)
            if "script" in step:
                llm = FakeLLMClient(script=[tuple(x) for x in step["script"]])
            else:
                llm = FakeLLMClient(default_response=step["default_response"])
            if step.get("embed_error"):
                err = step["embed_error"]

                async def broken(*_a, **_k):
                    raise RuntimeError(err)
                llm.embed = broken  # type: ignore[assignment]
                llm.embed_many = broken  # type: ignore[assignment]
            batch = [(await store.get_message(msgs[i].message_id)) for i in step["messages"]]
            try:
                plan = await MemoryExtractor(store, llm, cfg).build_plan(batch, [], attempt=step.get("attempt", 1))
                res = {"plan": plan_dump(plan), "error": None}
                if step.get("apply"):
                    res["counts"] = await store.apply_plan(plan)
            except Exception as exc:  # noqa: BLE001
                res = {"plan": None, "error": f"{type(exc).__name__}: {exc}"}
            res["prompts"] = llm.prompts
            res["generate_calls"] = llm.stats.generate_calls
            results.append(res)
    await store.close()
    return {"name": sc["name"], "steps": sc["steps"], "results": results, "db": dump_db(tmp / f"sc_{sc['name']}.db")}


async def gen_extraction(tmp: Path) -> None:
    write("extraction.json", [await run_scenario(sc, tmp) for sc in SCENARIOS])


# ---------------------------------------------------------------------------------------------- MCP
def mcp_messages() -> list:
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}  # noqa: E731
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": "s4", "method": "tools/list"},
        call(5, "memory_search", {"query": "Luna"}),
        call(6, "memory_search", {"query": "Where does Ali live?", "k": "3", "include_superseded": 1}),
        call(7, "memory_search", {"query": "Berlin", "kinds": ["event", "plan"], "since": "2026-03", "subject": "Ali Novruzov"}),
        call(8, "memory_search", {"nonsense": 1}),
        call(9, "memory_search", {}),
        call(10, "memory_search", {"query": "x", "k": "abc"}),
        call(11, "memory_search", {"query": 5}),
        call(12, "memory_search", {"query": None}),
        call(13, "memory_search", {"query": "Ali", "kinds": 5}),
        call(14, "memory_search", {"query": "Ali", "since": 2020}),
        call(15, "memory_context", {"query": "Ali cat"}),
        call(16, "memory_context", {"query": "zzz qqq"}),
        call(17, "memory_context", {"query": "Berlin", "k": 2, "max_chars": 300, "subject": "Ali Novruzov"}),
        call(18, "memory_profile", {"subject": "Ali Novruzov"}),
        call(19, "memory_profile", {"subject": "Leyla", "limit": 2}),
        call(20, "memory_related", {"memory_id": "nope"}),
        call(21, "memory_related", {"memory_id": "mem_00000000000000000003"}),
        call(22, "memory_entities", {}),
        call(23, "memory_entities", {"entity": "Ali", "limit": 5}),
        call(24, "memory_entities", {"entity": "ali novruzov"}),
        call(25, "memory_entities", {"entity": "Nobody"}),
        call(26, "memory_status", {}),
        call(27, "memory_status", {"x": 1}),
        call(28, "memory_add_message", {"chat_id": "s1", "speaker": "Ali", "text": "I moved to Berlin in March and my cat Luna is 3 years old.", "sent_at": "2026-04-01T10:00:00+00:00"}),
        call(29, "memory_add_message", {"chat_id": "", "speaker": "Ali", "text": "x"}),
        call(30, "memory_add_message", {"chat_id": "s1", "speaker": "Ali"}),
        call(31, "memory_add_message", {"speaker": "Ali"}),
        call(32, "memory_remember", {"text": "Ali prefers tea.", "subject": "Ali"}),
        call(33, "memory_remember", {"text": "Ali prefers tea.", "subject": "Ali"}),
        call(34, "memory_remember", {"text": "x"}),
        call(35, "memory_remember", {"text": "Ali likes jazz.", "importance": "high"}),
        call(36, "memory_remember", {"text": "Ali likes jazz.", "importance": [1]}),
        call(37, "memory_remember", {"text": "Mel visits in 2027.", "subject": "Mel", "kind": "plan", "importance": 2, "chat_id": "mcp-x", "when": "2027"}),
        call(38, "memory_forget", {"memory_id": "mem_00000000000500001"}),
        call(39, "memory_forget", {"memory_id": "nope", "reason": "test"}),
        {"jsonrpc": "2.0", "id": 40, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 41, "method": "tools/call", "params": {"name": "memory_status", "arguments": [1]}},
        {"jsonrpc": "2.0", "id": 42, "method": "tools/call", "params": {"arguments": {}}},
        {"jsonrpc": "2.0", "id": 43, "method": "does/not/exist"},
        {"jsonrpc": "1.0", "id": 44},
        {"jsonrpc": "2.0", "id": 45, "method": 5},
        {"jsonrpc": "2.0", "id": 46, "method": "ping", "params": []},
        {"jsonrpc": "2.0", "id": 4.5, "method": "ping"},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
        {"jsonrpc": "2.0", "id": [1], "method": "ping"},
        {"jsonrpc": "2.0", "id": 47, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 48, "method": "resources/templates/list"},
        {"jsonrpc": "2.0", "id": 49, "method": "resources/read", "params": {"uri": "memory://profile/Ali Novruzov"}},
        {"jsonrpc": "2.0", "id": 50, "method": "resources/read", "params": {"uri": "memory://entities"}},
        {"jsonrpc": "2.0", "id": 51, "method": "resources/read", "params": {"uri": "memory://status"}},
        {"jsonrpc": "2.0", "id": 52, "method": "resources/read", "params": {"uri": "memory://nope"}},
        {"jsonrpc": "2.0", "id": 53, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 54, "method": "prompts/get", "params": {"name": "recall", "arguments": {"topic": "Luna"}}},
        {"jsonrpc": "2.0", "id": 55, "method": "prompts/get", "params": {"name": "recall"}},
        {"jsonrpc": "2.0", "id": 56, "method": "prompts/get", "params": {"name": "recall", "arguments": [1]}},
        {"jsonrpc": "2.0", "id": 57, "method": "logging/setLevel", "params": {"level": "info"}},
        {"jsonrpc": "2.0", "method": "unknown/notification"},
        "not an object",
        [{"jsonrpc": "2.0", "id": 58, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}],
        [],
        [{"jsonrpc": "2.0", "method": "notifications/cancelled"}],
    ]


async def gen_mcp(tmp: Path) -> None:
    db = tmp / "mcp.db"
    shutil.copy(HERE / "ingest_user_named.sqlite", db)
    pin_clock(QUERY_NOW)
    pin_ids(500000)
    cfg = ChatMemoryConfig()
    cfg.debounce_seconds = 0
    cfg.extraction.speaker_names["user"] = "Ali Novruzov"
    cm = ChatMemory(db, llm=scripted_fake_llm(), config=cfg)
    proto = MCPProtocol(cm)
    out = []
    for msg in mcp_messages():
        resp = await proto.handle_payload(msg)
        out.append({"request": msg, "response": resp})
        if isinstance(msg, dict) and msg.get("id") == 28:
            await cm.process_pending()
    await cm.close()
    write("mcp.json", {"exchanges": out, "db_after": dump_db(db)})


# ---------------------------------------------------------------------------------------------- REST
REST_HEADERS_KEPT = ["content-type", "cache-control", "allow", "access-control-allow-origin", "access-control-allow-methods",
                     "access-control-allow-headers", "access-control-expose-headers", "vary", "www-authenticate",
                     "mcp-protocol-version"]


def rest_requests() -> list[dict]:
    h = {"Host": "127.0.0.1:8765"}
    j = {**h, "Content-Type": "application/json"}
    return [
        {"app": "plain", "method": "GET", "path": "/healthz", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/status", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/search?q=Luna", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/search?q=Where+does+Ali+live%3F&k=3&history=1", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/search?q=Ali&k=500&kinds=event,plan&since=2026-03", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/search", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/search?q=x&k=abc", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/context?q=Berlin", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/context?q=Berlin&k=2&max_chars=50", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/context?q=Berlin&max_chars=x", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/memories?subject=Ali+Novruzov&limit=3", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/memories?status=superseded", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/memories?limit=-5&kind=fact,event", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/entities?limit=3", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/entities?limit=zz", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/profile/Ali%20Novruzov", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/profile/Nobody", "headers": h},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "body": {"chat_id": "api", "speaker": "Ali", "text": "I adopted a dog called Rex in Berlin.", "sent_at": "2026-10-01T09:00:00+00:00"}},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "body": {"chat_id": "api", "speaker": "", "text": "x"}},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "body": {"chat_id": "x", "speaker": "Ali", "text": "hi", "sent_at": 12345}},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "body": {"chat_id": "x", "speaker": "Ali", "text": "hi", "metadata": [1]}},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "raw": "[1, 2]"},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": j, "raw": "{not json"},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": {**h, "Content-Type": "text/plain"}, "raw": "{}"},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": {**h, "Content-Type": "text/plain", "Origin": "https://evil.example"}, "raw": "{}"},
        {"app": "plain", "method": "POST", "path": "/api/messages", "headers": {**j, "Origin": "http://127.0.0.1:8765"}, "body": {"chat_id": "api", "speaker": "Ali", "text": "Same-origin write works.", "sent_at": "2026-10-01T09:01:00+00:00"}},
        {"app": "plain", "method": "POST", "path": "/api/messages/batch", "headers": j, "body": {"chat_id": "api", "messages": [{"speaker": "Ali", "text": "I also like tea.", "sent_at": "2026-10-01T09:02:00+00:00"}, {"text": "and coffee", "role": "user", "sent_at": "2026-10-01T09:03:00+00:00"}]}},
        {"app": "plain", "method": "POST", "path": "/api/messages/batch", "headers": j, "body": {"chat_id": "b", "messages": [{"text": "ok one"}, {"text": "bad", "speaker": 5}]}},
        {"app": "plain", "method": "POST", "path": "/api/messages/batch", "headers": j, "body": {"chat_id": "b", "messages": ["not an object"]}},
        {"app": "plain", "method": "POST", "path": "/api/messages/batch", "headers": j, "body": {"messages": []}},
        {"app": "plain", "method": "POST", "path": "/api/remember", "headers": j, "body": {"text": "Ali likes jazz.", "subject": "Ali Novruzov"}},
        {"app": "plain", "method": "POST", "path": "/api/remember", "headers": j, "body": {"text": "Ali likes tea.", "importance": "high"}},
        {"app": "plain", "method": "POST", "path": "/api/remember", "headers": j, "body": {"text": "ab"}},
        {"app": "plain", "method": "POST", "path": "/api/remember", "headers": j, "body": {"text": "Ali likes 70s soul.", "importance": True, "kind": "preference", "when": "1975", "chat_id": "rest"}},
        {"app": "plain", "method": "POST", "path": "/api/remember", "headers": j, "body": {"text": "x", "subject": 5}},
        {"app": "plain", "method": "POST", "path": "/api/forget", "headers": j, "body": {"memory_id": "mem_00000000000500001"}},
        {"app": "plain", "method": "POST", "path": "/api/forget", "headers": j, "body": {"memory_id": 5}},
        {"app": "plain", "method": "POST", "path": "/api/forget", "headers": j, "body": {"memory_id": "nope", "reason": "test"}},
        {"app": "plain", "method": "POST", "path": "/api/maintain", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/messages", "headers": h},
        {"app": "plain", "method": "POST", "path": "/api/status", "headers": j, "body": {}},
        {"app": "plain", "method": "GET", "path": "/nope", "headers": h},
        {"app": "plain", "method": "GET", "path": "/api/profile/", "headers": h},
        {"app": "plain", "method": "OPTIONS", "path": "/mcp", "headers": {**h, "Origin": "https://evil.example"}},
        {"app": "plain", "method": "GET", "path": "/api/status", "headers": {**h, "Origin": "https://evil.example"}},
        {"app": "plain", "method": "POST", "path": "/mcp", "headers": {**j, "MCP-Protocol-Version": "1999-01-01"}, "body": {"jsonrpc": "2.0", "id": 1, "method": "ping"}},
        {"app": "plain", "method": "POST", "path": "/mcp", "headers": {**h, "Content-Type": "text/plain", "Origin": "https://evil.example"}, "raw": "{}"},
        {"app": "plain", "method": "GET", "path": "/", "headers": h},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": j, "body": {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"}, "body": {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret", "Mcp-Session-Id": "abc"}, "body": {"jsonrpc": "2.0", "method": "notifications/initialized"}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret", "Mcp-Session-Id": "abc"}, "body": {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Luna"}}}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret", "Accept": "text/event-stream"}, "body": {"jsonrpc": "2.0", "id": 9, "method": "ping"}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret"}, "raw": "{not json"},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer secret"}, "body": [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]},
        {"app": "mcp_token", "method": "GET", "path": "/mcp", "headers": {**h, "Authorization": "Bearer secret", "Accept": "application/json"}},
        {"app": "mcp_token", "method": "DELETE", "path": "/mcp", "headers": {**h, "Authorization": "Bearer secret", "Mcp-Session-Id": "abc"}},
        {"app": "mcp_token", "method": "PUT", "path": "/mcp", "headers": {**h, "Authorization": "Bearer secret"}},
        {"app": "mcp_token", "method": "POST", "path": "/mcp", "headers": {**j, "Authorization": "Bearer wrong"}, "body": {"jsonrpc": "2.0", "id": 1, "method": "ping"}},
        {"app": "cors_hosts", "method": "OPTIONS", "path": "/mcp", "headers": {**h, "Origin": "https://claude.ai"}},
        {"app": "cors_hosts", "method": "GET", "path": "/api/status", "headers": {**h, "Origin": "https://other.example"}},
        {"app": "cors_hosts", "method": "GET", "path": "/healthz", "headers": {**h, "Origin": "https://claude.ai"}},
        {"app": "cors_hosts", "method": "GET", "path": "/nope", "headers": {**h, "Origin": "https://claude.ai"}},
        {"app": "cors_hosts", "method": "GET", "path": "/api/status", "headers": {"Host": "attacker.example"}},
        {"app": "cors_hosts", "method": "GET", "path": "/healthz", "headers": {"Host": "localhost:8765"}},
        {"app": "cors_hosts", "method": "GET", "path": "/healthz", "headers": {"Host": "[::1]:8765"}},
        {"app": "cors_hosts", "method": "POST", "path": "/api/messages", "headers": {**j, "Origin": "https://claude.ai"}, "body": {"chat_id": "c", "speaker": "Ali", "text": "Cross-site but allowed origin.", "sent_at": "2026-10-01T10:00:00+00:00"}},
        {"app": "api_token", "method": "GET", "path": "/api/status", "headers": h},
        {"app": "api_token", "method": "GET", "path": "/api/status", "headers": {**h, "Authorization": "Bearer api-secret"}},
        {"app": "api_token", "method": "GET", "path": "/", "headers": h},
        {"app": "api_token", "method": "GET", "path": "/healthz", "headers": h},
        {"app": "origins", "method": "POST", "path": "/mcp", "headers": {**j, "Origin": "https://x.example"}, "body": {"jsonrpc": "2.0", "id": 1, "method": "ping"}},
        {"app": "origins", "method": "POST", "path": "/mcp", "headers": {**j, "Origin": "https://ok.example"}, "body": {"jsonrpc": "2.0", "id": 1, "method": "ping"}},
    ]


REST_APPS = {
    "plain": {},
    "mcp_token": {"mcp_token": "secret"},
    "cors_hosts": {"cors_origins": ["https://claude.ai"], "allowed_hosts": ["localhost", "127.0.0.1", "[::1]"]},
    "api_token": {"api_token": "api-secret"},
    "origins": {"allowed_origins": ["https://ok.example"], "cors_origins": ["*"]},
}


async def gen_rest(tmp: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from NeuralGraph.chat_memory.ui.server import create_app

    db = tmp / "rest.db"
    shutil.copy(HERE / "ingest_user_named.sqlite", db)
    pin_clock(QUERY_NOW)
    pin_ids(500000)
    cfg = ChatMemoryConfig()
    cfg.debounce_seconds = 0
    cfg.extraction.speaker_names["user"] = "Ali Novruzov"
    cm = ChatMemory(db, llm=scripted_fake_llm(), config=cfg)
    clients = {}
    for name, kw in REST_APPS.items():
        c = TestClient(TestServer(create_app(cm, **kw)))
        await c.start_server()
        clients[name] = c
    out = []
    for req in rest_requests():
        c = clients[req["app"]]
        data = req.get("raw")
        if "body" in req:
            data = json.dumps(req["body"])
        r = await c.request(req["method"], req["path"], headers=req["headers"], data=data.encode() if data is not None else None)
        raw = await r.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
            kind = "json"
        except ValueError:
            body = text
            kind = "text"
        hdrs = {k: r.headers.get(k) for k in REST_HEADERS_KEPT if r.headers.get(k) is not None}
        if r.headers.get("Mcp-Session-Id"):
            hdrs["mcp-session-id"] = "<session>" if req["headers"].get("Mcp-Session-Id") is None else r.headers["Mcp-Session-Id"]
        out.append({"request": req, "status": r.status, "headers": hdrs, "kind": kind, "body": body})
    for c in clients.values():
        await c.close()
    await cm.close()
    write("rest.json", {"exchanges": out, "dashboard_sha_len": len((REF / "NeuralGraph/chat_memory/ui/dashboard.html").read_text(encoding="utf-8"))})


async def main() -> None:
    np.seterr(all="ignore")
    gen_textutil()
    gen_jsonutil()
    gen_temporal()
    gen_pycompat()
    gen_difflib()
    gen_registry()
    gen_prompts()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        prompts = await gen_ingest(tmp)
        gen_fake_llm(prompts)
        await gen_extraction(tmp)
        await gen_retrieval(tmp)
        await gen_mcp(tmp)
        await gen_rest(tmp)


if __name__ == "__main__":
    asyncio.run(main())
