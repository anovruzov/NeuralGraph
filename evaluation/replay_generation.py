"""Replay the answer-generation step with Claude, holding retrieval fixed.

`docs/ACCURACY.md` established that single-hop failure is a *generation*
problem: ~76% of failures had their evidence retrieved. That diagnosis implies
a specific experiment, and `demo/maximal.json` already contains everything it
needs -- the retrieved memories are recorded per question.

So the generation step can be replayed on its own. Same questions, same
retrieved context, different answerer. Retrieval is held fixed by construction,
which is what makes any measured difference attributable to generation rather
than to a better search.

    export ANTHROPIC_API_KEY=sk-ant-...
    python3 -m evaluation.replay_generation demo/maximal.json --limit 40 --category single_hop

WHY THE GRADER IS NOT A JUDGE MODEL

The recorded run used a GPT-4o judge emitting a binary verdict. That hides
exactly the thing the diagnosis found: answers fail as *sets* -- naming two of
three gold items scores the same zero as naming none. So the primary grader
here is deterministic item-level precision/recall/F1 against the gold list.

That choice has three benefits: it costs nothing, it is perfectly reproducible,
and it shows partial credit, so "the fix recovered a third item" is visible
rather than being rounded away. `exact_set_match` is reported alongside as the
strict binary comparable to the original judge.

An LLM judge is deliberately not the default. Using Claude to grade Claude
would score the fix with the model being fixed.

COST. A full 1540-question replay is roughly 3M input tokens; at Opus 5 rates
(~$5/1M in, $25/1M out) expect on the order of $20-40. Use `--limit` for a
pilot first, and `--dry-run` to see the estimate and a sample prompt without
spending anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evaluation.diagnose_accuracy import (
    answer_items,
    content_tokens,
    evidence_present,
    retrieved_text,
)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"

# Groq speaks the OpenAI chat-completions dialect. It is offered because it is
# cheap and fast enough to replay all 1540 questions repeatedly while iterating
# on the prompt, which is the actual bottleneck in fixing a generation problem.
#
# NOTE: the Groq path is UNVERIFIED against the live API. The environment this
# was written in blocks api.groq.com at the egress proxy (403 on CONNECT), so
# only its request construction and response parsing are tested, not a real
# round trip. Run it once with --limit 5 before trusting a full sweep.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Ollama serves the same OpenAI chat-completions dialect on /v1, so local models
# and Groq share one backend and differ only in base URL and auth. Local Ollama
# needs no key; the auth header is sent only when one is configured.
OLLAMA_BASE_URL = "http://localhost:11434/v1"

# Gemini exposes an OpenAI-compatible surface, so it reuses the same backend
# rather than needing a native REST client. Verified working against
# gemini-3.7-flash, which honours response_format json_object.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

#: Transient-failure retry. Hosted endpoints throttle under sustained load, and
#: an unretried 429/503 is indistinguishable from a wrong answer in the score.
MAX_ATTEMPTS = 5
BACKOFF_BASE = 2.0


class QuotaExhausted(RuntimeError):
    """A per-day quota is gone. Not retryable, and not survivable within a run.

    Distinct from ordinary throttling. A free Gemini tier allows 20 requests
    per day per model, and its 429 body reads "check your plan and billing
    details" -- indistinguishable at a glance from a rate limit. Retrying it
    burns the remainder of the allowance and turns every remaining question
    into a scored wrong answer, so a long sweep quietly reports throttling as
    model accuracy. The run must stop instead.
    """


#: Substrings identifying a per-day quota rather than a per-minute rate limit.
DAILY_QUOTA_MARKERS = ("PerDay", "requests per day", "RequestsPerDay")


def is_daily_quota_error(body: str) -> bool:
    return any(marker.lower() in (body or "").lower() for marker in DAILY_QUOTA_MARKERS)


class RateLimiter:
    """Pace requests to a requests-per-minute ceiling.

    Free hosted tiers throttle per minute, and their 429 body says "check your
    plan and billing details" even when the limit is purely rate-based -- which
    reads as a hard quota and is not. Bursting into that wastes the run: a
    throttled request that exhausts its retries is scored as a wrong answer, so
    the measured accuracy silently reflects throttling rather than the model.

    Pacing is enforced ahead of the request instead of discovered through
    failures. ``rpm <= 0`` disables it.
    """

    def __init__(self, rpm: float) -> None:
        self._interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self) -> None:
        if not self._interval:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if wait:
            await asyncio.sleep(wait)

PROVIDERS = {
    "anthropic": {"openai_dialect": False, "base_url": None, "key_env": (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")},
    "groq": {"openai_dialect": True, "base_url": GROQ_BASE_URL,
             "key_env": ("GROQ_API_KEY",)},
    "ollama": {"openai_dialect": True, "base_url": OLLAMA_BASE_URL, "key_env": ()},
    "gemini": {"openai_dialect": True, "base_url": GEMINI_BASE_URL,
               "key_env": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
}

# The prompt is the intervention under test. Every instruction in it targets a
# failure mode measured in docs/ACCURACY.md, and nothing else is in here:
#   - 120 failures omitted a gold item        -> enumerate exhaustively
#   - a third invented plausible items        -> only what the evidence states
#   - 147 answered unrelated content          -> answer THIS question, right type
#   -  60 abstained with the evidence present -> abstain only as a last resort
SYSTEM_PROMPT = """\
You answer questions about a conversation, using only the supplied excerpts.

Rules:
1. Answer from the excerpts. Answer if the excerpts support an answer at all, \
even a partial one - only set not_in_evidence when nothing in them bears on \
the question. Do not hedge: never write "there is no mention ... however".
2. Never add an item the excerpts do not state. Do not infer plausible extras.
3. If the question asks what things, which things, or otherwise admits more \
than one answer, list EVERY distinct item the excerpts support - scan all of \
them before answering, not just the first relevant one.
4. Answer the question actually asked, and match its type: a "when" question \
takes a time, a "who" a person, a "what" a thing. Never answer a "what" \
question with a date. Related material that does not answer the question must \
be left out.
5. Give the answer only. No preamble, no explanation, no restating the question.
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Every distinct item answering the question. One element for a "
                    "single-valued answer. Empty if the evidence does not answer it."
                ),
            },
            "not_in_evidence": {
                "type": "boolean",
                "description": "True when the excerpts do not answer the question.",
            },
        },
        "required": ["items", "not_in_evidence"],
        "additionalProperties": False,
    },
}


@dataclass
class Grade:
    """Item-level scoring of one answer against its gold list."""

    precision: float
    recall: float
    f1: float
    exact_set_match: bool
    gold_items: int
    predicted_items: int
    missing: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)


def _stem(token: str) -> str:
    """Strip a plural 's' so 'horses' and 'horse' compare equal.

    Deliberately minimal. Real stemming would conflate distinct gold items
    ("painting" and "paint") and silently inflate recall, which is the metric
    under test. This is the same morphology trap that inverted the two evidence
    measures in ``diagnose_accuracy`` — worth handling, not worth over-handling.
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _item_key(item: str) -> frozenset[str]:
    """Compare items by stemmed content tokens, so 'horses' matches 'a horse'."""
    return frozenset(_stem(token) for token in content_tokens(item))


def grade(gold: str, predicted: str | list[str]) -> Grade:
    """Score predicted items against gold items by token-set overlap.

    Two items match when their content-token sets intersect. Exact string
    equality would fail on ordinary paraphrase ("Pride parade" vs "LGBTQ+ pride
    parade") and would measure phrasing rather than content.
    """
    gold_list = [g for g in answer_items(gold) if _item_key(g)]
    if isinstance(predicted, str):
        predicted_list = [p for p in answer_items(predicted) if _item_key(p)]
    else:
        predicted_list = [p for p in predicted if _item_key(p)]

    gold_keys = [_item_key(g) for g in gold_list]
    pred_keys = [_item_key(p) for p in predicted_list]

    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    for gi, gk in enumerate(gold_keys):
        for pi, pk in enumerate(pred_keys):
            if pi in matched_pred:
                continue
            if gk & pk:
                matched_gold.add(gi)
                matched_pred.add(pi)
                break

    hits = len(matched_gold)
    precision = hits / len(pred_keys) if pred_keys else 0.0
    recall = hits / len(gold_keys) if gold_keys else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Grade(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        exact_set_match=bool(gold_keys) and hits == len(gold_keys) == len(pred_keys),
        gold_items=len(gold_keys),
        predicted_items=len(pred_keys),
        missing=[g for i, g in enumerate(gold_list) if i not in matched_gold],
        spurious=[p for i, p in enumerate(predicted_list) if i not in matched_pred],
    )


def build_prompt(record: dict[str, Any]) -> str:
    excerpts = []
    for memory in record.get("retrieved_memories") or ():
        if isinstance(memory, dict):
            speaker = memory.get("speaker") or "unknown"
            when = memory.get("datetime") or ""
            text = memory.get("text") or ""
            excerpts.append(f"[{speaker}{(', ' + when) if when else ''}] {text}")
        else:
            excerpts.append(str(memory))
    joined = "\n".join(f"- {line}" for line in excerpts)
    return f"Excerpts:\n{joined}\n\nQuestion: {record.get('question', '')}"


def estimate_cost(records: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Rough offline estimate, so a dry run costs nothing.

    Characters/4 is a crude token proxy; it is used only to warn about the order
    of magnitude before spending money, never reported as a measurement.
    """
    chars = sum(len(build_prompt(r)) + len(SYSTEM_PROMPT) for r in records)
    input_tokens = chars / 4
    output_tokens = 60 * len(records)
    rate_in, rate_out = (5.0, 25.0) if "opus" in model else (3.0, 15.0)
    return {
        "questions": len(records),
        "estimated_input_tokens": int(input_tokens),
        "estimated_output_tokens": int(output_tokens),
        "estimated_usd": round(
            input_tokens / 1e6 * rate_in + output_tokens / 1e6 * rate_out, 2
        ),
        "note": "chars/4 proxy; order-of-magnitude only",
    }


class ProgressReporter:
    """Prints a line every N completed requests, with running exact-set-match
    accuracy by category. Single-threaded asyncio, so plain counters need no
    lock."""

    def __init__(self, total: int, every: int = 10) -> None:
        self.total = total
        self.every = every
        self.done = 0
        self.errors = 0
        self.category_n: dict[str, int] = {}
        self.category_correct: dict[str, int] = {}

    def tick(self, category: str | None = None, correct: bool | None = None) -> None:
        self.done += 1
        if category is None or correct is None:
            self.errors += 1
        else:
            self.category_n[category] = self.category_n.get(category, 0) + 1
            self.category_correct[category] = (
                self.category_correct.get(category, 0) + (1 if correct else 0)
            )
        if self.done % self.every == 0 or self.done == self.total:
            parts = [
                f"{cat}={self.category_correct[cat]}/{n}"
                f" ({self.category_correct[cat] / n:.1%})"
                for cat, n in sorted(self.category_n.items())
            ]
            errs = f", errors={self.errors}" if self.errors else ""
            print(
                f"progress: {self.done}/{self.total} -- " + ", ".join(parts) + errs,
                file=sys.stderr, flush=True,
            )


async def openai_dialect_answer(session, record: dict[str, Any], model: str,
                                base_url: str, api_key: str | None,
                                semaphore: asyncio.Semaphore,
                                limiter: "RateLimiter | None" = None,
                                progress: "ProgressReporter | None" = None) -> dict[str, Any]:
    """One question through an OpenAI-compatible endpoint (Groq or Ollama)."""
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        # OpenAI-dialect JSON mode. Unlike Anthropic structured outputs this
        # guarantees only that the reply parses as JSON, not that it matches the
        # schema -- so the schema is restated in the prompt and the parse below
        # falls back to splitting prose if a key is missing.
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + JSON_INSTRUCTION},
            {"role": "user", "content": build_prompt(record)},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # Hosted endpoints return 429/503 under load. A long sweep hits them
    # routinely, and an unretried transient is scored as a wrong answer, which
    # would depress the measured result for a reason that has nothing to do
    # with the model or the prompt.
    payload = None
    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        if limiter is not None:
            await limiter.acquire()
        async with semaphore:
            try:
                async with session.post(
                    f"{base_url}/chat/completions", json=body, headers=headers,
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        break
                    last_error = f"HTTP {response.status}: {(await response.text())[:200]}"
                    if response.status == 429 and is_daily_quota_error(last_error):
                        raise QuotaExhausted(last_error)
                    retryable = response.status in (408, 409, 429, 500, 502, 503, 504)
                    # Honour the server's own backoff when it supplies one.
                    hinted = response.headers.get("Retry-After")
                    retry_after = float(hinted) if (hinted or "").strip().isdigit() else None
            except QuotaExhausted:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retryable, retry_after = True, None
        if not retryable or attempt == MAX_ATTEMPTS - 1:
            break
        # Backoff outside the semaphore so a sleeping retry does not hold a slot.
        await asyncio.sleep(retry_after or BACKOFF_BASE * (2 ** attempt) + random.random())
    if payload is None:
        if progress is not None:
            progress.tick()
        return {"id": record.get("id"), "error": last_error}

    text = payload["choices"][0]["message"]["content"]
    items, not_in_evidence = parse_items(text)
    usage = payload.get("usage") or {}
    row = build_row(record, items, not_in_evidence,
                     usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    if progress is not None:
        progress.tick(row["category"], row["claude"]["exact_set_match"])
    return row


JSON_INSTRUCTION = (
    '\nReply with JSON only, exactly: {"items": ["..."], "not_in_evidence": false}\n'
)


THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_items(text: str) -> tuple[list[str], bool]:
    """Read the item list out of a model reply, tolerating messy wrappers.

    Three layers, because a silent fallback here would be scored as a bad answer
    rather than as a parse failure, and would blame the prompt for a formatting
    problem:

    1. Strip ``<think>`` blocks. Reasoning models (qwen3, deepseek-r1) emit them
       ahead of the answer even under JSON mode, which makes ``json.loads`` fail
       on otherwise perfect output.
    2. Parse the whole reply as JSON.
    3. Failing that, take the outermost ``{...}`` — covers fenced code blocks and
       models that add a sentence before the object.

    Only if all three fail does it fall back to splitting prose.
    """
    cleaned = THINK_BLOCK.sub("", text or "").strip()
    embedded = JSON_OBJECT.search(cleaned)
    candidates = [cleaned]
    if embedded:
        candidates.append(embedded.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict) or "items" not in parsed:
            continue
        items = parsed.get("items")
        flag = bool(parsed.get("not_in_evidence"))
        if isinstance(items, list):
            return [str(i) for i in items], flag
        if isinstance(items, str):
            # A model that ignored the array type and returned "a, b" as one
            # string. Split it rather than falling through to prose parsing,
            # which would otherwise shred the raw JSON into nonsense items.
            return answer_items(items), flag
    return answer_items(cleaned), False


def build_row(record: dict[str, Any], items: list[str], not_in_evidence: bool,
              input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """Score one replayed answer against gold, alongside the recorded baseline.

    Retrieval is held fixed by construction (`docs/ACCURACY.md`), so a wrong
    answer here needs one more bit to be actionable for NeuralGraph: whether
    the retrieved excerpts recorded in the artifact actually contained the
    gold answer. `evidence_present` reuses the exact strict/lenient substring
    check `diagnose_accuracy.py` used for the original diagnosis, so this
    field means the same thing here as it does there -- a row failing with
    `evidence_lenient: true` is a generation defect regardless of which model
    produced `claude_items`; `evidence_lenient: false` is a retrieval miss and
    belongs to the NeuralGraph retrieval track, not to this replay.
    """
    gold = str(record.get("gold_answer", ""))
    strict, lenient = evidence_present(record)
    return {
        "id": record.get("id"),
        "category": record.get("category"),
        "question": record.get("question"),
        "gold_answer": record.get("gold_answer"),
        "retrieved_text": retrieved_text(record),
        "evidence_strict": strict,
        "evidence_lenient": lenient,
        "baseline_answer": record.get("generated_answer"),
        "baseline_judged_correct": bool(record.get("correct")),
        "claude_items": items,
        "claude_not_in_evidence": not_in_evidence,
        "claude": grade(gold, items).__dict__,
        "baseline": grade(gold, str(record.get("generated_answer", ""))).__dict__,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


async def answer_one(client, record: dict[str, Any], model: str, effort: str,
                     semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                output_config={"effort": effort, "format": RESPONSE_FORMAT},
                messages=[{"role": "user", "content": build_prompt(record)}],
            )
        except Exception as exc:  # surfaced per-record; the sweep continues
            return {"id": record.get("id"), "error": f"{type(exc).__name__}: {exc}"}

    text = "".join(block.text for block in response.content if block.type == "text")
    items, not_in_evidence = parse_items(text)
    return build_row(record, items, not_in_evidence,
                     response.usage.input_tokens, response.usage.output_tokens)


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]
    if not scored:
        return {"scored": 0, "errors": len(errors),
                "error_sample": [r["error"] for r in errors[:3]]}

    def mean(key: str, side: str) -> float:
        return round(sum(r[side][key] for r in scored) / len(scored), 4)

    return {
        "scored": len(scored),
        "errors": len(errors),
        "error_sample": [r["error"] for r in errors[:3]],
        "claude": {
            "item_f1": mean("f1", "claude"),
            "item_recall": mean("recall", "claude"),
            "item_precision": mean("precision", "claude"),
            "exact_set_match": round(
                sum(1 for r in scored if r["claude"]["exact_set_match"]) / len(scored), 4
            ),
        },
        "baseline": {
            "item_f1": mean("f1", "baseline"),
            "item_recall": mean("recall", "baseline"),
            "item_precision": mean("precision", "baseline"),
            "exact_set_match": round(
                sum(1 for r in scored if r["baseline"]["exact_set_match"]) / len(scored), 4
            ),
            "original_judge_accuracy": round(
                sum(1 for r in scored if r["baseline_judged_correct"]) / len(scored), 4
            ),
        },
        "tokens": {
            "input": sum(r["usage"]["input_tokens"] for r in scored),
            "output": sum(r["usage"]["output_tokens"] for r in scored),
        },
        # Same split docs/ACCURACY.md used to attribute failure between
        # generation and retrieval. Wrong here + evidence_lenient=True means
        # this model's generation dropped evidence it was handed; wrong +
        # evidence_lenient=False is a retrieval miss inherited from the
        # recorded artifact, not something this replay's model could have
        # fixed.
        "diagnosis": {
            "wrong": len(wrong := [r for r in scored if not r["claude"]["exact_set_match"]]),
            "wrong_with_evidence": sum(1 for r in wrong if r["evidence_lenient"]),
            "wrong_without_evidence": sum(1 for r in wrong if not r["evidence_lenient"]),
        },
    }


async def run(records, provider, model, effort, concurrency, base_url=None, rpm=0.0):
    semaphore = asyncio.Semaphore(concurrency)
    spec = PROVIDERS[provider]
    if spec["openai_dialect"]:
        import aiohttp

        url = base_url or spec["base_url"]
        key = next((os.environ[n] for n in spec["key_env"] if os.environ.get(n)), None)
        # trust_env is required for aiohttp to honour HTTPS_PROXY at all.
        async with aiohttp.ClientSession(trust_env=True) as session:
            limiter = RateLimiter(rpm)
            progress = ProgressReporter(len(records))
            tasks = [
                asyncio.ensure_future(
                    openai_dialect_answer(session, r, model, url, key, semaphore, limiter,
                                          progress)
                )
                for r in records
            ]
            try:
                return await asyncio.gather(*tasks)
            except QuotaExhausted as exc:
                # Stop immediately. Letting the remaining questions run would
                # burn nothing useful and would report a quota wall as accuracy.
                for task in tasks:
                    task.cancel()
                done = await asyncio.gather(*tasks, return_exceptions=True)
                rows = [r for r in done if isinstance(r, dict)]
                print(
                    f"\nABORTED: daily quota exhausted after {len(rows)} scored "
                    f"question(s).\n{exc}\n"
                    "This is a per-day limit, not throttling -- waiting will not help "
                    "today. Raise the limit (enable billing) or use another provider.",
                    file=sys.stderr,
                )
                return rows

    import anthropic

    client = anthropic.AsyncAnthropic()
    return await asyncio.gather(
        *(answer_one(client, r, model, effort, semaphore) for r in records)
    )


async def list_models(base_url: str, api_key: str | None) -> list[str]:
    import aiohttp

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(f"{base_url}/models", headers=headers) as response:
            payload = await response.json()
    return sorted(m["id"] for m in payload.get("data", []))





def stratified_sample(records: list[dict[str, Any]], size: int,
                      seed: int = 20260813) -> list[dict[str, Any]]:
    """Pick ``size`` questions that mirror the corpus, deterministically.

    Taking the first N is not a sample. ``demo/maximal.json`` is ordered by
    conversation, so the first 300 records cover conversations 1-3 of 10 --
    three speakers' worth of content, with a category mix that does not match
    the corpus. A number measured on that says little about the benchmark.

    This allocates per category in proportion to the corpus (largest remainder,
    so the parts sum to exactly ``size``) and samples within each category with
    a fixed seed, which spreads the draw across all ten conversations. The same
    seed always yields the same question set, so two model runs are compared on
    identical questions rather than on two different samples.
    """
    import random as _random

    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_category.setdefault(str(record.get("category", "unknown")), []).append(record)

    total = len(records)
    size = min(size, total)
    exact = {c: len(rows) * size / total for c, rows in by_category.items()}
    quota = {c: int(v) for c, v in exact.items()}
    # Largest remainder, so rounding never loses or invents a question.
    shortfall = size - sum(quota.values())
    for category in sorted(exact, key=lambda c: (-(exact[c] - quota[c]), c))[:shortfall]:
        quota[category] += 1

    picked: list[dict[str, Any]] = []
    for category in sorted(by_category):
        rows = sorted(by_category[category], key=lambda r: r.get("id", 0))
        picked.extend(
            _random.Random(f"{seed}:{category}").sample(rows, min(quota[category], len(rows)))
        )
    return sorted(picked, key=lambda r: r.get("id", 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--category", help="restrict to one category")
    parser.add_argument("--limit", type=int,
                        help="first N questions in file order (pilots only; NOT a sample)")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="N questions stratified by category across all "
                             "conversations, deterministic (prefer this over --limit)")
    parser.add_argument("--sample-seed", type=int, default=20260813)
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="anthropic")
    parser.add_argument("--base-url", help="override the provider's endpoint")
    parser.add_argument("--model", default=None,
                        help="defaults to claude-opus-5 on anthropic; required on groq "
                             "(use --list-models to see what your key can reach)")
    parser.add_argument("--list-models", action="store_true",
                        help="groq/ollama: print available model ids and exit")
    parser.add_argument("--effort", default=DEFAULT_EFFORT,
                        choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--rpm", type=float, default=0.0,
                        help="requests-per-minute ceiling (hosted free tiers "
                             "throttle per minute; 0 disables pacing)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="print a sample prompt and cost estimate; call nothing")
    args = parser.parse_args(argv)

    if args.list_models:
        spec = PROVIDERS[args.provider]
        if not spec["openai_dialect"]:
            print("--list-models supports groq and ollama", file=sys.stderr)
            return 2
        key = next((os.environ[n] for n in spec["key_env"] if os.environ.get(n)), None)
        for model_id in asyncio.run(
            list_models(args.base_url or spec["base_url"], key)
        ):
            print(model_id)
        return 0

    model = args.model or (DEFAULT_MODEL if args.provider == "anthropic" else None)
    if model is None:
        print(
            f"--model is required for --provider {args.provider}. Model ids change "
            "often and vary by install, so nothing is guessed here.\n"
            f"  ollama:  ollama list\n"
            f"  groq:    python3 -m evaluation.replay_generation --list-models "
            "--provider groq demo/maximal.json",
            file=sys.stderr,
        )
        return 2

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    records = payload["results"] if isinstance(payload, dict) else payload
    if args.category:
        records = [r for r in records if r.get("category") == args.category]
    if args.sample:
        records = stratified_sample(records, args.sample, args.sample_seed)
    elif args.limit:
        records = records[:args.limit]

    if args.dry_run:
        print(json.dumps(estimate_cost(records, model), indent=2))
        if records:
            print("\n--- system prompt ---\n" + SYSTEM_PROMPT)
            print("--- sample user prompt ---\n" + build_prompt(records[0])[:1200])
        return 0

    key_env = PROVIDERS[args.provider]["key_env"]
    if key_env and not any(os.environ.get(name) for name in key_env):
        wanted = " or ".join(key_env)
        print(
            f"No credentials found for --provider {args.provider}. Set {wanted}.\n"
            "  anthropic: https://console.anthropic.com -> Settings -> API keys\n"
            "  groq:      https://console.groq.com -> API Keys\n"
            "Run with --dry-run to preview prompts and cost without a key.",
            file=sys.stderr,
        )
        return 2

    rows = asyncio.run(run(records, args.provider, model, args.effort,
                           args.concurrency, args.base_url, args.rpm))
    report = {
        "provider": args.provider,
        "model": model,
        "effort": args.effort,
        "category": args.category or "all",
        "selection": (f"stratified sample of {args.sample} (seed {args.sample_seed})"
                      if args.sample else
                      f"first {args.limit} in file order" if args.limit else "all"),
        "summary": summarise(rows),
        "rows": rows,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
