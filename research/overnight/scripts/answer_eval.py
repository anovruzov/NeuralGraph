#!/usr/bin/env python3
"""Answer + grade from pre-built context blocks. Stdlib only (urllib + threads); talks to LM Studio.

Input: a contexts JSON written by `chat-memory-context` ({"rows": [{id, type, question, gold, question_date,
context, source_quotes?, evidence_retrieved?...}]}). Output: {"config", "summary", "rows"} like the original
research/benchmarks/chat_memory_answer_eval.py, so numbers stay comparable with the 2026-10-22 baseline
(answerer Qwen3.8-9B concise, judge Gemma-4-e4b: strict 54.0 / lenient 80.0 / substring 44.0 on LME Oracle 50).

  answer_eval.py --contexts ctx.json --out res.json [--prompt concise|compute|<file>] [--model ...] [--judge-model ...]
                 [--render plain|quotes|nodate|saidon|quotes_saidon|quotes_user|quotes_user_saidon|chrono|chrono_quotes]
                 [--quotes-per-memory N]
                 [--calendar] [--cascade <prompt>] [--route-pref <prompt>] [--route-compute <prompt> [--route-compute-think [--route-compute-max-tokens 2500]]]
                 [--extra-judges] [--official-judge] [--k-limit N] [--threads 4] [--resume]
                 [--cascade-think [--cascade-max-tokens 3000]] [--vote N [--vote-temperature 0.7]] [--answer-model-think] [--no-think-fallback]

Prompt files: a text file with {date_line} {memories} {question} placeholders. If the file name contains "compute"
or the file's first line is "#mode: compute", the answer is taken from the last "Answer: ..." line (max_tokens 700);
otherwise the whole reply is the answer (max_tokens 160). "#mode: auto" = compute-style parsing with 600 tokens.
--render saidon: "(from chat ID, DATE)" -> "(said on DATE Wkd)" and "[DATE, kind]" -> "[DATE Wkd, kind]" (quotes_saidon adds quotes).
--render quotes_user[_saidon]: like quotes but user-side quotes only (speaker != assistant), a quote whose normalised text
  (whitespace-collapsed, lowercased) already appeared under an earlier memory is dropped, --quotes-per-memory N per memory
  (default 3 for quotes, 1 for quotes_user), and an over-long quote is cut at the last ". " / "? " / "! " before --quote-chars
  when that boundary lies past half the limit (else hard cut) and gets a trailing "…".
--render chrono[_quotes]: after --k-limit, the memory lines (with their quote lines for chrono_quotes, which uses the plain
  `quotes` quote selection) are sorted ascending by the leading "[YYYY-MM-DD..." event date, else the trailing
  "(from chat X, YYYY-MM-DD)" observed date (undated lines last, ties keep the original order); the header line becomes
  "Memories in chronological order (oldest first):".
--calendar: replace the date line with a deterministic "Today is ..." + "Calendar: ..." scaffold built from question_date.
--cascade P: any answer that starts with "I don't know" is re-asked with prompt P (kept unless also IDK); the label is never used.
--cascade-think: the cascade's second call (only) runs with thinking on (no empty-<think> prefill, no reasoning_effort=none)
  and --cascade-max-tokens (default 3000); only the reply `content` is parsed (LM Studio puts the reasoning in the separate
  `reasoning_content` field, which is ignored; a thinking reply truncated before the final text has empty content, and an empty
  or IDK second answer never replaces the primary one).
--vote N: self-consistency for the PRIMARY call: N samples at --vote-temperature (default 0.7) plus the T=0 answer; answers are
  normalised (lowercase, "answer:" prefix and punctuation dropped, decimal points kept) and the most common wins; a tie for the
  top count -> the T=0 answer. The row gets votes = [T=0 answer, sample 1, ...]; the cascade then applies to the voted answer.
--answer-model-think: the primary call (and the cascade, unless --cascade-think sets its own budget) thinks, --max-tokens honoured
  (0 = the per-mode default, which is too small for thinking: pass e.g. --max-tokens 3000).
--think-fallback / --no-think-fallback (default ON with --think or --answer-model-think): a thinking primary (or vote) call whose
  answer is empty / markdown-only (e.g. the reasoning hit max_tokens, finish_reason "length") is re-asked once with the same prompt
  without thinking (empty-think prefill, reasoning_effort none, max_tokens 300). Thinking rows record think_truncated and think_fallback.
--route-pref P: questions matching suggest|recommend|any ideas|what do you think|should I|tips use prompt P as the primary.
--route-compute P: questions matching COMPUTE_RE (duration / count / aggregation / date-difference wording) use prompt P as the
  primary (routed="compute"); --route-pref wins when both match; --cascade still applies afterwards.
--route-compute-think: the routed (compute-matching) primary call only runs with thinking on (no empty-<think> prefill, no
  reasoning_effort=none) and --route-compute-max-tokens (default 2500), with the think-fallback (empty/truncated -> re-asked once
  without thinking; forced ON for routed calls). Routed rows record route_compute_think, think_truncated, think_fallback and
  think_fallback_idk (a truncated thinking call whose fallback answer is "I don't know"; flag only). Non-matching questions keep
  the normal primary settings; the --cascade still applies afterwards to any IDK.
--extra-judges: also grade strict_v2 / lenient_v2 (rationale line + "VERDICT: CORRECT|WRONG", 200 tokens), cached like the others.
--official-judge: also grade `official` = the LongMemEval paper's per-question-type yes/no judge prompts (Wu et al., ICLR 2025;
  github.com/xiaowu0162/LongMemEval src/evaluation/evaluate_qa.py get_anscheck_prompt, texts verbatim), run on --judge-model
  (reasoning_effort none, max_tokens 8, correct iff "yes" in the lower-cased reply). Rows of type "abstention" use the official
  abstention prompt with the question's own gold explanation; types the official script does not know use the multi-session text.
--regrade-v2-missing (with --extra-judges and --resume, or --regrade-from): re-judge only the strict_v2/lenient_v2 values that are
  False with no judge-cache entry (unparsable verdict replies are never cached), rewrite --out in place; no answering.
--route-regex tight|wide|<regex>: which questions --route-compute routes (default wide = COMPUTE_RE, today's behaviour). tight = only
  clear multi-memory arithmetic wording (how long / how many days|weeks|months|years|times, days|weeks|months|years between|since,
  in total, total number|amount, altogether, combined, difference between, how old). A custom regex is compiled case-insensitively.
  The value is recorded as route_regex in the config.
--route-llm [--router-prompt router_v1]: decide the --route-compute route with ONE short non-thinking model call (--model, empty-<think>
  prefill except for gemma, reasoning_effort none, max_tokens 3, temperature 0) using prompts/router_v1.txt ({question} is the only
  placeholder). A reply starting with COMPUTE routes; anything else does not. Alone = the LLM decides (the regex is not used);
  with an explicit --route-regex = routed if EITHER the regex OR the LLM says compute. Rows record route_source ("regex" | "llm" |
  "both" | null) and router_reply; the call counts in `calls`. The verdict is cached in the judge cache (key: model, "router",
  question); a cache hit makes no call, so it is not counted. --route-pref still wins (no router call for pref questions).
--think-early-stop [--route-compute-max-tokens-first N (default 900)]: two-stage budget for the --route-compute-think call: first call
  with max_tokens N; if it was truncated (finish_reason length) with no "Answer:" line, ONE retry with the full
  --route-compute-max-tokens (no non-thinking fallback in between; the usual think-fallback applies after stage 2). Rows record
  think_stage (1 or 2). Giving --route-compute-max-tokens-first alone also switches this on. Needs --route-compute-think.
--concise-max-tokens N (default 160): the default max_tokens of concise-mode calls (--max-tokens, when non-zero, still wins).
--agentic-search (--db-dir <dir> | --db <file>) [--search-k 5] [--search-bin <path>]: for ROUTED compute questions only. The routed
  prompt gets one extra rule appended in code (AGENTIC_RULE; the prompt file is untouched) letting the reader write one line
  `SEARCH: <short query>` instead of the Answer line. A reply with a SEARCH line and no `Answer:` line runs the product CLI
  `neuralgraph-memory context "<query>" --db <db> -k <search-k>` (LME: <dir>/<row id>.db; LoCoMo: the one --db file; env
  LLM_NO_THINK_PREFILL=1, NEURALGRAPH_EXP=context_quotes=1,context_raw_messages=3,context_max_chars=6000), drops its header line
  and lines already in the prompt, and re-asks the SAME prompt once (thinking on, rule removed) with the new lines under
  `Additional memories for "<query>":`. A second SEARCH is treated as "I don't know" (cascade/fallback as usual). Rows record
  agentic_query, agentic_added_lines (and agentic_error / agentic_second_search when they happen); the extra call counts in `calls`.
--verify P: after the final answer (post-cascade), a non-IDK answer gets ONE extra non-thinking call (max_tokens 60) with prompt P
  ({memories} {question} {answer}; the memories block starts with the date line). Reply SUPPORTED -> verify "supported";
  UNSUPPORTED -> "unsupported" (answer kept, or replaced by "I don't know." with --verify-apply-unsupported idk, in which case
  verify_applied = "idk"); FIX: <x> -> answer replaced by x, verify "fixed". Replaced answers record answer_before_verify;
  an unparsable reply records verify "unparsed" and keeps the answer. The reply is stored as verify_reply.
Rows record calls = number of model calls made for the answer (cascade, votes, think retries, verify included; judges excluded).
Judge verdicts are cached in <out>.judgecache.json keyed by (judge model, prompt kind, type, question, gold, answer).
"""
from __future__ import annotations

import argparse
import calendar
import datetime
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROMPTS = HERE.parent / "prompts"

# byte-identical to research/benchmarks/chat_memory_answer_eval.py on branch PNurmanM/memory-architecture-stats
ANSWER_PROMPT = """You are an assistant with long-term memory. Below are memories retrieved for the user's question.
Answer the question using ONLY these memories. Be specific and concise (one sentence or a short list).
If the memories do not contain the information needed, reply exactly: I don't know.
{date_line}
{memories}

Question: {question}
Answer:"""

COMPUTE_PROMPT = """You are an assistant with long-term memory. Below are memories retrieved for the user's question.
Answer using ONLY these memories. The answer often has to be COMPUTED from several memories: add durations, count items,
compare or subtract dates (use the dates in brackets and today's date), convert units (30 minutes = 0.5 hours), or pick
the most recent value when a fact changed. Do that computation. Only reply "I don't know" when nothing in the memories
bears on the question at all.
{date_line}
{memories}

Question: {question}
Think step by step briefly, then give the final answer on the last line in the form:
Answer: <specific answer>"""

LENIENT_JUDGE = """You are grading an assistant's answer against a gold answer.
Question type: {qtype}
Question: {question}
Gold answer: {gold}
Assistant's answer: {answer}

Rules:
- The answer is CORRECT if it conveys the same information as the gold answer, even with different wording or extra detail.
- For type "abstention": the gold means the information was NOT available; the answer is CORRECT only if it declines or says it does not know.
- For type "knowledge-update": the answer must reflect the latest, updated value.
- For type "temporal-reasoning": dates or durations must match within the gold's precision.
Reply with exactly one word: CORRECT or WRONG."""

STRICT_JUDGE = """You are a strict grader. Compare the assistant's answer with the gold answer.
Question: {question}
Gold answer: {gold}
Assistant's answer: {answer}

Rules:
- CORRECT only if EVERY fact in the gold answer is explicitly present in the assistant's answer.
- A partial list, a vague or hedged answer, a superset that contradicts the gold, or "I don't know" when the gold has a value is WRONG.
- For type "abstention" (gold means no information): CORRECT only if the assistant explicitly declines.
Question type: {qtype}
Reply with exactly one word: CORRECT or WRONG."""

# v2 judges (R1 section 4): decide by facts, rationale line first, parsed from the final "VERDICT:" line.
STRICT_JUDGE_V2 = """You are a strict grader. Compare the assistant's answer with the gold answer.
Question type: {qtype}
Question: {question}
Gold answer: {gold}
Assistant's answer: {answer}

Decide by FACTS, not wording:
- List the facts in the gold answer (values with units, names, items, dates, yes/no). The answer is CORRECT only if every one of them is stated in the answer, or an exactly equal form of it ("nine" = 9, "an hour and a half" = 90 minutes = 1.5 hours, "$0.75" = 75 cents). Where the gold lists acceptable alternatives (for example "21 days; 22 days is also acceptable"), either is fine.
- Extra words, a full sentence, or additional TRUE detail around the correct facts do not make it wrong. Extra detail that contradicts the gold does.
- WRONG if the answer: gives a different value; omits any part of the gold (partial list, missing item, missing unit when the unit is part of the answer); offers several alternative answers or a range; says it is unsure ("might", "possibly", "I think", "probably", "not sure"); or says "I don't know"/cannot answer when the gold has a value.
- "about/around/approximately" in front of a value is NOT a hedge when the number equals the gold exactly; it IS a hedge when the number differs or a range is given.
- For type "abstention": CORRECT only if the answer explicitly declines or says the information is not available.
- For type "knowledge-update": the answer must give the latest value, not an older one.
- For type "single-session-preference": the gold is a rubric of what the user would prefer. CORRECT only if the answer's suggestion is consistent with the preferences in the rubric and uses the personal background it names; WRONG if it is generic, contradicts a "would not prefer" item, or declines.
Write one short sentence naming the gold facts and whether each is present, then a final line: VERDICT: CORRECT or VERDICT: WRONG
Keep the rationale to one sentence."""

# the original lenient text + the R1 refusal/rubric line; the one-word reply instruction is swapped for the VERDICT format
LENIENT_JUDGE_V2 = LENIENT_JUDGE.replace(
    "Reply with exactly one word: CORRECT or WRONG.",
    "- A refusal (\"I don't know\") is WRONG unless the type is abstention.\n"
    "- For type \"single-session-preference\": the gold is a rubric of what the user would prefer; the answer is CORRECT if its "
    "suggestion is consistent with that rubric and uses the personal background it names, WRONG if it is generic, "
    "contradicts a \"would not prefer\" item, or declines.\n"
    "Write one short sentence of justification, then a final line: VERDICT: CORRECT or VERDICT: WRONG\n"
    "Keep the rationale to one sentence.")
assert LENIENT_JUDGE_V2 != LENIENT_JUDGE

# --official-judge: LongMemEval get_anscheck_prompt (src/evaluation/evaluate_qa.py), template strings verbatim.
# Placeholders are filled positionally (question, correct answer / rubric / explanation, model response).
OFFICIAL_TEMPLATES = {
    "default": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "temporal-reasoning": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only.",
}  # single-session-user / single-session-assistant / multi-session all use "default"


def official_prompt(qtype: str, question: str, gold: str, answer: str) -> str:
    """Official LongMemEval judge prompt; the official script keys abstention on '_abs' in the id, we on type == 'abstention'."""
    return OFFICIAL_TEMPLATES.get(qtype, OFFICIAL_TEMPLATES["default"]).format(question, gold, answer)


ABSTENTION_GOLD = "(no information available; the correct response is to say so)"


_TLS = threading.local()  # per-thread count of successful model calls (answer_one resets it and reads it per row)


def calls_reset() -> None:
    _TLS.n = 0


def calls_get() -> int:
    return getattr(_TLS, "n", 0)


def chat(base_url: str, model: str, messages: list[dict], **kw) -> str:
    """Reply text only (think blocks dropped). See chat_ex for the arguments."""
    return chat_ex(base_url, model, messages, **kw)[0]


def chat_ex(base_url: str, model: str, messages: list[dict], *, max_tokens: int, temperature: float = 0.0,
            timeout: float = 600.0, reasoning_effort: str | None = "none", extra: dict | None = None) -> tuple[str, str]:
    """(text, finish_reason); finish_reason is "length" when the reply hit max_tokens ("" if the server omits it)."""
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    if extra:
        body.update(extra)
    req = urllib.request.Request(f"{base_url}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode())
            ch0 = (j.get("choices") or [{}])[0]
            txt = ch0.get("message", {}).get("content") or ""
            _TLS.n = getattr(_TLS, "n", 0) + 1
            return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip(), ch0.get("finish_reason") or ""
        except urllib.error.HTTPError as e:  # noqa: PERF203
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:  # noqa: BLE001
                pass
            last = f"HTTP {e.code}: {body}"
            print(f"[chat] attempt {attempt + 1} {model}: {last}", file=sys.stderr, flush=True)
            if "unloaded" in body.lower() or "failed to load" in body.lower():
                # LM Studio dropped the model (TTL / memory juggling): reload it explicitly and retry.
                import subprocess
                ctx = "16384" if "qwen" in model.lower() else "8192"
                subprocess.run(["lms", "load", model, "-y", "--context-length", ctx, "--parallel", "4", "--identifier", model],
                               capture_output=True, text=True, timeout=300)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"[chat] attempt {attempt + 1} {model}: {e}", file=sys.stderr, flush=True)
        time.sleep(min(60, 3 * 2 ** attempt))
    raise RuntimeError(f"chat failed after retries: {last}")


def is_gemma(model: str) -> bool:
    """google/gemma-* ids: no empty-<think> prefill (use reasoning_effort only)."""
    return model.lower().split("/")[-1].startswith("gemma")


def strip_think(txt: str) -> str:
    """Drop closed <think>..</think> blocks, then anything up to a stray closing </think> or from an unclosed <think> on."""
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
    if "</think>" in txt:
        txt = txt.rsplit("</think>", 1)[1]
    return re.sub(r"<think>.*", "", txt, flags=re.S).strip()


def gold_parts(gold) -> list[str]:
    s = str(gold).lower().strip()
    parts = [p.strip(" .") for p in re.split(r",| and |;", s) if p.strip(" .")]
    return parts or [s]


def substring_ok(gold, answer: str) -> bool:
    a = answer.lower()
    return all(p in a for p in gold_parts(gold))


def verdict(txt: str) -> bool:
    t = txt.strip().upper()
    return t.startswith("CORRECT") or ("CORRECT" in t and "WRONG" not in t)


def verdict_v2(txt: str):
    """Last 'VERDICT: CORRECT|WRONG' in the reply -> bool, or None when the reply has no verdict line (e.g. truncated)."""
    m = re.findall(r"VERDICT:?\s*\**\s*(CORRECT|WRONG)", txt.upper())
    return None if not m else m[-1] == "CORRECT"


class JudgeCache:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = json.loads(path.read_text()) if path.exists() else {}
        self.dirty = 0

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()

    def get(self, k):
        with self.lock:
            return self.data.get(k)

    def put(self, k, v):
        with self.lock:
            self.data[k] = v
            self.dirty += 1
            if self.dirty % 20 == 0:
                self.path.write_text(json.dumps(self.data))

    def flush(self):
        with self.lock:
            self.path.write_text(json.dumps(self.data))


def load_prompt(spec: str) -> tuple[str, str]:
    """-> (template, mode) where mode in {concise, compute, auto}."""
    if spec == "concise":
        return ANSWER_PROMPT, "concise"
    if spec == "compute":
        return COMPUTE_PROMPT, "compute"
    p = Path(spec)
    if not p.exists():
        p = PROMPTS / (spec if spec.endswith(".txt") else spec + ".txt")
    text = p.read_text()
    mode = "concise"
    first, _, rest = text.partition("\n")
    if first.startswith("#mode:"):
        mode = first.split(":", 1)[1].strip()
        text = rest
    elif "compute" in p.name:
        mode = "compute"
    return text, mode


_SAID_RE = re.compile(r"\s\(from chat [^)]*?,\s*(\d{4}-\d{2}-\d{2})\)\s*$")
_EVENT_RE = re.compile(r"^(\s*-\s*\[)(\d{4}-\d{2}-\d{2})(?=,)")


def _wk(iso: str) -> str | None:
    try:
        return datetime.date.fromisoformat(iso).strftime("%a")
    except ValueError:
        return None


def saidon_line(line: str) -> str:
    """'(from chat ID, D)' -> '(said on D Wkd)'; leading '[D, kind]' -> '[D Wkd, kind]'. Unparseable dates are left alone."""
    def said(m):
        w = _wk(m.group(1))
        return f" (said on {m.group(1)} {w})" if w else m.group(0)

    def event(m):
        w = _wk(m.group(2))
        return f"{m.group(1)}{m.group(2)} {w}" if w else m.group(0)
    return _EVENT_RE.sub(event, _SAID_RE.sub(said, line))


def calendar_scaffold(question_date: str) -> str | None:
    """Deterministic 'Today is ...' + 'Calendar: ...' lines from e.g. '2023/05/29 (Mon) 13:21' or '2023-05-29T13:21'."""
    m = re.match(r"\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", question_date or "")
    if not m:
        return None
    try:
        today = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    d = lambda x: x.isoformat()  # noqa: E731
    td = datetime.timedelta
    parts = [f"yesterday = {d(today - td(days=1))} ({(today - td(days=1)).strftime('%a')})"]
    for wd in range(7):  # most recent <weekday> strictly before today
        back = (today.weekday() - wd) % 7 or 7
        day = today - td(days=back)
        parts.append(f"last {day.strftime('%A')} = {d(day)}")
    mon = today - td(days=today.weekday())
    parts.append(f"last week = {d(mon - td(days=7))} to {d(mon - td(days=1))}")
    sat = today - td(days=(today.weekday() - 5) % 7 or 7)
    if sat + td(days=1) >= today:  # today is Sunday: the weekend in progress is not "last weekend"
        sat -= td(days=7)
    parts.append(f"last weekend = {d(sat)} to {d(sat + td(days=1))}")
    pm_y, pm_m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    parts.append(f"last month = {pm_y:04d}-{pm_m:02d}")
    for n in range(1, 7):
        parts.append(f"{n} day{'s' if n > 1 else ''} ago = {d(today - td(days=n))}")
    for n in range(1, 7):
        parts.append(f"{n} week{'s' if n > 1 else ''} ago = {d(today - td(days=7 * n))}")
    for n in range(1, 7):
        t = today.year * 12 + today.month - 1 - n
        y, mo = divmod(t, 12)
        mo += 1
        day = min(today.day, calendar.monthrange(y, mo)[1])
        parts.append(f"{n} month{'s' if n > 1 else ''} ago = about {d(datetime.date(y, mo, day))}")
    return f"Today is {today.strftime('%A')} {d(today)}.\nCalendar: " + "; ".join(parts) + "."


def make_date_line(args, row: dict) -> str:
    qd = row.get("question_date", "")
    if not qd:
        return ""
    if args.calendar:
        cal = calendar_scaffold(qd)
        if cal:
            return cal
    return f"Today's date: {qd}"


PREF_RE = re.compile(r"\b(?:suggest|recommend|any ideas|what do you think|tips|should I\b)", re.I)


# --route-compute: duration / count / aggregation / date-difference questions ("how long ...", "how many days ...",
# "total ...", "difference between ..."). Question text only, never the type or the gold.
COMPUTE_RE = re.compile(
    r"\b(?:how (?:long|many|much|old)|in total|total|altogether|combined|sum of|number of|difference|between|"
    r"days|weeks|months|years)\b", re.I)


# --route-regex tight: only questions that clearly need arithmetic over several memories.
TIGHT_SRC = (r"how (?:long|many days|many weeks|many months|many years|many times)|days? (?:between|since|until|ago)|"
             r"weeks? (?:between|since)|months? (?:between|since)|years? (?:between|since)|in total|total (?:number|amount)|"
             r"altogether|combined|difference between|how old")
TIGHT_RE = re.compile(r"\b(?:" + TIGHT_SRC + r")\b", re.I)


def resolve_route_re(spec: str):
    """--route-regex value -> compiled regex: wide = COMPUTE_RE, tight = TIGHT_RE, anything else a custom (case-insensitive) regex."""
    if spec in ("", "wide"):
        return COMPUTE_RE
    if spec == "tight":
        return TIGHT_RE
    return re.compile(spec, re.I)


def is_idk(ans: str) -> bool:
    return ans.strip().lower().replace("\u2019", "'").startswith("i don't know")


def nonidk_flag(r: dict, key: str) -> bool:
    """r[key + '_nonidk'] when present, else computed on the fly: key correct and not an IDK answer (abstention rows exempt)."""
    if key + "_nonidk" in r:
        return bool(r[key + "_nonidk"])
    return bool(r[key]) and not (is_idk(r["answer"]) and r["type"] != "abstention")


def cut_quote(t: str, limit: int) -> str:
    """Over-long quote: cut after the last '. ' / '? ' / '! ' within the limit if it lies past half the limit, else hard cut; add '…'."""
    if len(t) <= limit:
        return t
    head = t[: limit + 1]
    i = max(head.rfind(x) for x in (". ", "? ", "! "))
    return (head[: i + 1] if i > limit / 2 else t[:limit].rstrip()) + "…"


_LEAD_DATE_RE = re.compile(r"^\s*-\s*\[(\d{4}-\d{2}-\d{2})")


def memory_date(line: str) -> str:
    """Sort key for --render chrono: leading '[YYYY-MM-DD...' event date, else trailing '(from chat X, YYYY-MM-DD)' date,
    else '9999' (undated lines go last; sorted() is stable so ties keep their relevance order)."""
    m = _LEAD_DATE_RE.match(line) or _SAID_RE.search(line)
    return m.group(1) if m else "9999"


def render_memories(row: dict, render: str, k_limit: int | None, quote_chars: int, quotes_per_memory: int = 0) -> str:
    block = row.get("context") or ""
    if not block:
        return "(no relevant memories)"
    chrono = render.startswith("chrono")
    if chrono:
        render = "quotes" if render == "chrono_quotes" else "plain"
    lines = block.split("\n")
    header, items = lines[0], lines[1:]
    if k_limit:
        items = items[:k_limit]
    groups: list[list[str]] | None = None
    if render.endswith("saidon"):
        items = [saidon_line(l) for l in items]
    if render in ("quotes", "quotes_saidon", "quotes_user", "quotes_user_saidon") and row.get("source_quotes"):
        user_only = render.startswith("quotes_user")
        per_mem = quotes_per_memory or (1 if user_only else 3)
        ids = row.get("memory_ids") or []
        quotes = {q["memory_id"]: q.get("quotes", []) for q in row["source_quotes"]}
        seen: set[str] = set()
        out = []
        groups = []
        for i, line in enumerate(items):
            out.append(line)
            groups.append([line])
            mid = ids[i] if i < len(ids) else None
            emitted = 0
            for q in quotes.get(mid) or []:
                if emitted >= per_mem:
                    break
                raw = (q.get("text") or "").replace("\n", " ")
                if user_only:
                    norm = " ".join(raw.split()).lower()
                    if (q.get("speaker") or "").strip().lower() == "assistant" or norm in seen:
                        continue
                    seen.add(norm)
                    t = cut_quote(raw, quote_chars)
                else:
                    t = raw[:quote_chars]
                emitted += 1
                when = (q.get("sent_at") or "")[:10]
                out.append(f"    > {q.get('speaker', '')} ({when}): \"{t}\"")
                groups[-1].append(out[-1])
        items = out
    elif render == "nodate":
        items = [re.sub(r"\s\(from chat [^)]*\)$", "", l) for l in items]
    if chrono:
        header = "Memories in chronological order (oldest first):"
        items = [l for g in sorted(groups or [[l] for l in items], key=lambda g: memory_date(g[0])) for l in g]
    return "\n".join([header] + items)


AGENTIC_RULE = ('If a needed fact is missing from the memories, you may write exactly one line `SEARCH: <short query>` '
                'INSTEAD of the Answer line; you will receive more memories and be asked again.')
SEARCH_LINE_RE = re.compile(r"^[\s*_`>#-]*SEARCH\s*:\s*(.+?)\s*$", re.I | re.M)
DEFAULT_SEARCH_BIN = Path(__file__).resolve().parents[3] / "target/release/neuralgraph-memory"


def with_search_rule(template: str) -> str:
    """Append the agentic rule as the next numbered rule (just before the {date_line} line; at the end if there is none)."""
    nums = re.findall(r"^(\d+)\. ", template.split("{date_line}")[0], re.M)
    rule = f"{int(nums[-1]) + 1 if nums else 1}. {AGENTIC_RULE}"
    if "\n{date_line}" in template:
        return template.replace("\n{date_line}", "\n" + rule + "\n{date_line}", 1)
    return template.rstrip("\n") + "\n" + rule + "\n"


def agentic_db(args, row: dict):
    if getattr(args, "db", ""):
        return Path(args.db)
    return Path(args.db_dir) / f"{row['id']}.db"


def agentic_fetch(args, row: dict, query: str) -> str:
    """Run the product `context` CLI for the query -> stdout (a context block, or empty). Raises on a missing db / failed CLI."""
    import subprocess
    db = agentic_db(args, row)
    if not db.exists():
        raise FileNotFoundError(f"db not found: {db}")
    env = dict(os.environ, LLM_NO_THINK_PREFILL="1",
               NEURALGRAPH_EXP="context_quotes=1,context_raw_messages=3,context_max_chars=6000")
    p = subprocess.run([args.search_bin, "context", query, "--db", str(db), "-k", str(args.search_k)],
                       capture_output=True, text=True, env=env, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"context CLI rc={p.returncode}: {p.stderr[-200:]}")
    return p.stdout


def agentic_new_lines(block: str, present: str) -> list:
    """Drop the header line and every memory (its '- ' line plus its indented quote lines) whose head line is already in `present`;
    a sub-heading ('Raw messages that match the question:') is kept only when something under it survives."""
    lines = block.split("\n")
    if lines and lines[0].strip() and not lines[0].lstrip().startswith("-"):
        lines = lines[1:]
    have = {l.strip() for l in present.split("\n") if l.strip()}
    out: list = []
    pending_head = None
    skip = False
    for l in lines:
        if not l.strip():
            continue
        if l[0] in " \t":  # quote line of the preceding memory
            if not skip:
                out.append(l)
            continue
        if l.lstrip().startswith("-"):
            skip = l.strip() in have
            if not skip:
                if pending_head:
                    out.append(pending_head)
                    pending_head = None
                out.append(l)
                have.add(l.strip())
        else:
            pending_head, skip = l, False  # heading
    return out


def run_prompt(args, row: dict, template: str, mode: str, *, think: bool | None = None, max_tokens: int | None = None,
               temperature: float | None = None, info: dict | None = None, defer_truncated: bool = False,
               search_rule: bool = False, extra_memories: str = "", model: str | None = None) -> tuple[str, str, float]:
    """think/max_tokens/temperature override the args-level settings for this call (None = use args).
    info: if given and the call thinks, the call may fall back to a non-thinking re-ask (--think-fallback) and
    info gets think_truncated / think_fallback (bool).
    defer_truncated: a thinking call that hit max_tokens with no "Answer:" line skips the non-thinking fallback and sets
    info["think_deferred"] = True (the caller escalates to a bigger budget).
    model: model id for this call (None = args.model). google/gemma-* never gets the empty-<think> prefill; thinking is
    controlled by reasoning_effort alone ("none" when off, omitted when on)."""
    model = model or args.model
    no_prefill = is_gemma(model)
    if think is None:
        think = bool(args.think or args.answer_model_think)
    date_line = make_date_line(args, row)
    memories = render_memories(row, args.render, args.k_limit, args.quote_chars, args.quotes_per_memory)
    if search_rule:
        template = with_search_rule(template)
    if extra_memories:
        memories = memories + "\n\n" + extra_memories
    prompt = template.format(date_line=date_line, memories=memories, question=row["question"])
    msgs = [{"role": "user", "content": prompt}]
    if args.prefill and not think and not no_prefill:
        msgs.append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
    if max_tokens is None:
        max_tokens = {"concise": getattr(args, "concise_max_tokens", 160), "compute": 700, "auto": 600}[mode] \
            if args.max_tokens == 0 else args.max_tokens
    t0 = time.perf_counter()
    temp = args.temperature if temperature is None else temperature
    raw, finish = chat_ex(args.base_url, model, msgs, max_tokens=max_tokens, temperature=temp,
                          reasoning_effort=None if think else "none")
    dt = time.perf_counter() - t0
    if think:  # chat() returns `content` only (reasoning_content ignored); also drop leaked/unclosed think tags
        raw = strip_think(raw)
    ans = parse_answer(raw, mode)
    if think and info is not None:
        info["think_truncated"] = finish == "length"
        info["think_fallback"] = False
        info["think_fallback_idk"] = False
        no_answer_line = mode in ("compute", "auto") and not re.search(r"Answer:", raw)
        deferred = bool(defer_truncated and info["think_truncated"] and not re.search(r"Answer:", raw))
        if deferred:
            info["think_deferred"] = True
        elif getattr(args, "think_fallback", False) and (not has_content(ans) or (no_answer_line and not ans.strip())):
            # thinking produced nothing usable (typically hit the token cap): re-ask the same prompt once, no thinking
            t1 = time.perf_counter()
            raw = strip_think(chat(args.base_url, model,
                                   msgs + ([] if no_prefill else [{"role": "assistant", "content": "<think>\n\n</think>\n\n"}]),
                                   max_tokens=300, temperature=temp, reasoning_effort="none"))
            dt += time.perf_counter() - t1
            ans = parse_answer(raw, mode)
            info["think_fallback"] = True
            info["think_fallback_idk"] = bool(info["think_truncated"] and is_idk(ans))
    return ans, raw, dt


def has_content(ans: str) -> bool:
    """True if ans has anything left after dropping think tags, whitespace and markdown punctuation."""
    return bool(re.sub(r"[\s*_`#>~|\-]+", "", strip_think(ans)))


def parse_answer(raw: str, mode: str) -> str:
    if mode in ("compute", "auto"):
        # first line after the LAST "Answer:" (tolerates "**Answer:** x" / "Answer: **x**"); surrounding ** stripped
        m = re.findall(r"Answer:\s*\**\s*(.+)", raw)
        ans = (m[-1] if m else raw.strip().split("\n")[-1]).strip()
        return re.sub(r"^\*+|\*+$", "", ans).strip() or ans
    return raw


def norm_vote(ans: str) -> str:
    """Vote key: lowercase, leading 'answer:' and punctuation dropped (decimal points / thousands commas inside numbers kept
    as digits), whitespace collapsed."""
    t = re.sub(r"^\s*\**\s*answer\s*:\s*\**", "", ans.lower())
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    t = re.sub(r"[^\w.\s]|_", " ", t)
    return " ".join(t.split())


def vote_pick(answers: list[str]) -> int:
    """Index of the winning answer; answers[0] is the T=0 answer. Most common normalised answer wins; any tie for the
    top count -> index 0. The winner's first occurrence is returned."""
    keys = [norm_vote(a) for a in answers]
    cnt: dict[str, int] = {}
    for k in keys:
        cnt[k] = cnt.get(k, 0) + 1
    top = max(cnt.values())
    leaders = [k for k, c in cnt.items() if c == top]
    return 0 if len(leaders) > 1 else keys.index(leaders[0])


def run_verify(args, row: dict, template: str, ans: str) -> tuple[str, str, str, float]:
    """One non-thinking verification call (max_tokens 60) -> (label, fix, reply, seconds);
    label in supported / unsupported / fixed / unparsed, fix = corrected answer text (label fixed only)."""
    memories = make_date_line(args, row) + "\n" + render_memories(row, args.render, args.k_limit, args.quote_chars, args.quotes_per_memory)
    prompt = template.format(memories=memories, question=row["question"], answer=ans)
    msgs = [{"role": "user", "content": prompt}, {"role": "assistant", "content": "<think>\n\n</think>\n\n"}]
    t0 = time.perf_counter()
    reply = strip_think(chat(args.base_url, args.model, msgs, max_tokens=60, temperature=0.0, reasoning_effort="none"))
    dt = time.perf_counter() - t0
    line = next((l.strip() for l in reply.splitlines() if l.strip()), "").lstrip("*`_> -").strip()
    u = line.upper()
    if u.startswith("UNSUPPORTED"):
        return "unsupported", "", reply, dt
    if u.startswith("SUPPORTED"):
        return "supported", "", reply, dt
    if u.startswith("FIX"):
        fix = re.sub(r"^FIX\s*[:\-]?\s*\**\s*", "", line, flags=re.I)
        fix = re.sub(r"^answer\s*:\s*", "", fix.strip("*` "), flags=re.I).strip("*` ")
        if fix:
            return "fixed", fix, reply, dt
    return "unparsed", "", reply, dt


def router_verdict(args, cache, question: str) -> tuple[bool, str]:
    """--route-llm: one short non-thinking call -> (says COMPUTE, reply). Cached per (model, "router", question)."""
    k = JudgeCache.key(args.model, "router", question)
    reply = cache.get(k) if cache is not None else None
    if reply is None:
        tpl = load_prompt(args.router_prompt)[0].strip()
        msgs = [{"role": "user", "content": tpl.replace("{question}", question)}]
        if not is_gemma(args.model):
            msgs.append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
        reply = strip_think(chat(args.base_url, args.model, msgs, max_tokens=3, temperature=0.0, reasoning_effort="none"))
        if cache is not None:
            cache.put(k, reply)
    return reply.strip().upper().startswith("COMPUTE"), reply


def answer_one(args, row: dict, template: str, mode: str, pref=None, cascade=None, compute=None, cache=None) -> tuple[str, str, float, dict]:
    """Primary answer (optionally routed to the pref / compute prompt; pref wins), then the IDK cascade.
    Routing looks at the question text only; the row type and gold are never consulted."""
    meta: dict = {}
    calls_reset()
    route_re = resolve_route_re(getattr(args, "route_regex", "wide"))
    if pref is not None or compute is not None:
        route = "pref" if pref is not None and PREF_RE.search(row["question"]) else None
        if route is None and compute is not None:
            use_re = not getattr(args, "route_llm", False) or getattr(args, "route_regex_given", False)
            by_re = bool(use_re and route_re.search(row["question"]))
            by_llm = False
            if getattr(args, "route_llm", False):
                by_llm, meta["router_reply"] = router_verdict(args, cache, row["question"])
            if by_re or by_llm:
                route = "compute"
            meta["route_source"] = "both" if by_re and by_llm else "regex" if by_re else "llm" if by_llm else None
        meta["routed"] = route
        if route:
            template, mode = pref if route == "pref" else compute
    tinfo: dict = {}
    pkw: dict = {}  # per-call overrides for a thinking compute-routed primary call
    # --route-model applies to every call of a compute-routed row's primary path (two-stage retry, fallback, agentic re-ask, votes)
    mkw: dict = {"model": args.route_model} if meta.get("routed") == "compute" and getattr(args, "route_model", None) else {}
    if meta.get("routed") == "compute" and args.route_compute_think:
        pkw = {"think": True, "max_tokens": args.route_compute_max_tokens}
        meta["route_compute_think"] = True
    agentic = bool(pkw) and bool(getattr(args, "agentic_search", False))
    if agentic:
        pkw_a = {"search_rule": True}
    else:
        pkw_a = {}
    if pkw and getattr(args, "think_early_stop", False):
        # two-stage budget: small first call; only a truncated reply without an "Answer:" line is retried with the full budget
        t1: dict = {}
        ans, raw, dt = run_prompt(args, row, template, mode, info=t1, think=True,
                                  max_tokens=args.route_compute_max_tokens_first, defer_truncated=True, **mkw, **pkw_a)
        if t1.get("think_deferred"):
            meta["think_stage"] = 2
            ans, raw, dt2 = run_prompt(args, row, template, mode, info=tinfo, **mkw, **pkw, **pkw_a)
            dt += dt2
        else:
            meta["think_stage"] = 1
            tinfo = {k: v for k, v in t1.items() if k != "think_deferred"}
    else:
        ans, raw, dt = run_prompt(args, row, template, mode, info=tinfo, **mkw, **pkw, **pkw_a)
    meta.update(tinfo)  # think_truncated / think_fallback (thinking primary calls only)
    if agentic:
        text1 = strip_think(raw)
        sm = SEARCH_LINE_RE.findall(text1)
        if sm and not re.search(r"Answer:", text1):
            query = sm[-1].strip("*` \"'")
            meta["agentic_query"] = query
            added: list = []
            try:
                block = agentic_fetch(args, row, query)
                added = agentic_new_lines(block, render_memories(row, args.render, args.k_limit, args.quote_chars, args.quotes_per_memory))
            except Exception as e:  # noqa: BLE001  (missing db / CLI failure: ask again without extra memories)
                meta["agentic_error"] = str(e)[:200]
            meta["agentic_added_lines"] = len(added)
            extra = f'Additional memories for "{query}":\n' + ("\n".join(added) if added else "(no additional memories found)")
            t2: dict = {}
            ans, raw, dt2 = run_prompt(args, row, template, mode, info=t2, think=True, max_tokens=args.route_compute_max_tokens,
                                       extra_memories=extra, **mkw)
            dt += dt2
            meta.update(t2)
            text2 = strip_think(raw)
            if SEARCH_LINE_RE.search(text2) and not re.search(r"Answer:", text2):
                meta["agentic_second_search"] = True
                ans = "I don't know."
    if args.vote > 1:
        cands, raws = [ans], [raw]
        for _ in range(args.vote):
            a_i, r_i, dt_i = run_prompt(args, row, template, mode, temperature=args.vote_temperature, info={}, **mkw, **pkw)
            cands.append(a_i)
            raws.append(r_i)
            dt += dt_i
        meta["votes"] = cands
        w = vote_pick(cands)
        ans, raw = cands[w], raws[w]
    if cascade is not None:
        meta["answer_primary"] = ans
        meta["cascaded"] = False
        if is_idk(ans):
            ckw: dict = {"model": args.cascade_model} if getattr(args, "cascade_model", None) else {}
            if args.cascade_think:
                ans2, raw2, dt2 = run_prompt(args, row, *cascade, think=True, max_tokens=args.cascade_max_tokens, **ckw)
            else:
                ans2, raw2, dt2 = run_prompt(args, row, *cascade, **ckw)
            dt += dt2
            # a thinking call cut off before its final text has empty content: never let that replace the primary answer
            if not is_idk(ans2) and not ((args.cascade_think or args.answer_model_think or args.think) and not ans2.strip()):
                ans, raw, meta["cascaded"] = ans2, raw2, True
    if getattr(args, "verify", "") and not is_idk(ans) and ans.strip():
        label, fix, reply, dtv = run_verify(args, row, load_prompt(args.verify)[0], ans)
        dt += dtv
        meta["verify"] = label
        meta["verify_reply"] = reply
        if label == "fixed":
            meta["answer_before_verify"] = ans
            ans, raw = fix, reply
        elif label == "unsupported" and args.verify_apply_unsupported == "idk":
            meta["answer_before_verify"] = ans
            meta["verify_applied"] = "idk"
            ans, raw = "I don't know.", reply
    meta["calls"] = calls_get()
    return ans, raw, dt, meta


V2_JUDGE_MAX_TOKENS = 200
V2_JUDGES = (("strict_v2", STRICT_JUDGE_V2), ("lenient_v2", LENIENT_JUDGE_V2))


def judge_v2(args, cache: JudgeCache, name: str, tpl: str, qtype: str, question: str, gold: str, answer: str) -> bool:
    """One v2 verdict, cached. The cache key does not include the prompt text or token budget (cached verdicts stay valid).
    A reply without a VERDICT line counts as WRONG, warns, and is not cached."""
    k = JudgeCache.key(args.judge_model, name, qtype, question, gold, answer)
    v = cache.get(k)
    if v is None:
        txt = chat(args.base_url, args.judge_model, ([{"role": "user",
                   "content": tpl.format(qtype=qtype, question=question, gold=gold, answer=answer)}] + ([{"role": "assistant", "content": "<think>\n\n</think>\n\n"}] if args.judge_prefill else [])),
                   max_tokens=V2_JUDGE_MAX_TOKENS, reasoning_effort="none")
        v = verdict_v2(txt)
        if v is None:  # no VERDICT line (truncated?): count as WRONG but do not cache
            print(f"WARN {name}: no VERDICT in {txt[-80:]!r}", file=sys.stderr, flush=True)
            v = False
        else:
            cache.put(k, v)
    return v


def grade(args, cache: JudgeCache, qtype: str, question: str, gold: str, answer: str, official_gold: str | None = None) -> dict:
    out = {}
    for name, tpl in (("lenient", LENIENT_JUDGE), ("strict", STRICT_JUDGE)):
        k = JudgeCache.key(args.judge_model, name, qtype, question, gold, answer)
        v = cache.get(k)
        if v is None:
            txt = chat(args.base_url, args.judge_model, ([{"role": "user",
                       "content": tpl.format(qtype=qtype, question=question, gold=gold, answer=answer)}] + ([{"role": "assistant", "content": "<think>\n\n</think>\n\n"}] if args.judge_prefill else [])),
                       max_tokens=8, reasoning_effort="none")
            v = verdict(txt)
            cache.put(k, v)
        out[name] = v
    if args.extra_judges:
        for name, tpl in V2_JUDGES:
            out[name] = judge_v2(args, cache, name, tpl, qtype, question, gold, answer)
    if getattr(args, "official_judge", False):
        og = gold if official_gold is None else official_gold  # abstention rows: the question's own explanation
        k = JudgeCache.key(args.judge_model, "official", qtype, question, og, answer)
        v = cache.get(k)
        if v is None:
            txt = chat(args.base_url, args.judge_model, ([{"role": "user", "content": official_prompt(qtype, question, og, answer)}]
                       + ([{"role": "assistant", "content": "<think>\n\n</think>\n\n"}] if args.judge_prefill else [])),
                       max_tokens=8, reasoning_effort="none")
            v = "yes" in txt.lower()
            cache.put(k, v)
        out["official"] = v
    out["substring"] = substring_ok(gold, answer)
    return out


def summarize(rows: list[dict]) -> dict:
    def block(rs):
        n = max(1, len(rs))
        b = {"n": len(rs),
                "lenient": round(100 * sum(r["lenient"] for r in rs) / n, 1),
                "strict": round(100 * sum(r["strict"] for r in rs) / n, 1),
                "substring": round(100 * sum(r["substring"] for r in rs) / n, 1),
                "idk": sum(1 for r in rs if r["answer"].strip().lower().startswith("i don't know")),
                "mean_ctx_tokens": int(sum(r["ctx_chars"] for r in rs) / n / 4),
                "mean_answer_s": round(sum(r["answer_seconds"] for r in rs) / n, 2)}
        if rs and all("calls" in r for r in rs):
            b["mean_calls"] = round(sum(r["calls"] for r in rs) / n, 2)
        if any("verify" in r for r in rs):
            b["verify"] = {k: sum(1 for r in rs if r.get("verify") == k) for k in ("supported", "unsupported", "fixed", "unparsed")}
        for k in ("strict_v2", "lenient_v2", "official"):
            if rs and all(k in r for r in rs):
                b[k] = round(100 * sum(r[k] for r in rs) / n, 1)
        for k in ("lenient", "strict"):  # IDK counted wrong unless the row is abstention; computed for old rows lacking the field
            b[k + "_nonidk"] = round(100 * sum(nonidk_flag(r, k) for r in rs) / n, 1)
        return b
    by = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r)
    answerable = [r for r in rows if r["type"] != "abstention"]
    return {"overall": block(rows),
            "answerable": block(answerable),
            "by_type": [{"type": t, **block(rs)} for t, rs in sorted(by.items())]}


def regrade_v2_missing(args, doc: dict, out_path: Path, cache: JudgeCache) -> None:
    """Re-judge strict_v2/lenient_v2 values that came from an unparsable reply: the stored value is False and the judge cache
    has no entry for its key (such verdicts are never cached). Only those judge calls are made; the file is rewritten in place."""
    if not args.extra_judges:
        sys.exit("--regrade-v2-missing needs --extra-judges")
    src = out_path if args.resume else (Path(args.regrade_from) if args.regrade_from else None)
    if src is None or not src.exists():
        sys.exit("--regrade-v2-missing needs --resume (existing --out) or --regrade-from")
    payload = json.loads(src.read_text())
    ctx = {r["id"]: r for r in doc["rows"]}
    rows = payload["rows"]
    todo = []  # (row index, judge name, template, gold)
    for i, r in enumerate(rows):
        if r["id"] not in ctx:
            continue
        gold = ctx[r["id"]]["gold"] if r["type"] != "abstention" else ABSTENTION_GOLD
        for name, tpl in V2_JUDGES:
            if r.get(name) is False and cache.get(JudgeCache.key(args.judge_model, name, r["type"], r["question"], gold, r["answer"])) is None:
                todo.append((i, name, tpl, gold))
    before = summarize(rows)["overall"]
    print(f"{src.name}: {len(todo)} v2 verdicts / {len({t[0] for t in todo})} rows to re-judge", flush=True)

    def redo(t):
        i, name, tpl, gold = t
        r = rows[i]
        return judge_v2(args, cache, name, tpl, r["type"], r["question"], gold, r["answer"])

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        results = list(ex.map(redo, todo))
    flipped = 0
    for (i, name, _, _), v in zip(todo, results):
        flipped += int(v != rows[i][name])
        rows[i][name] = v
    cache.flush()
    payload["summary"] = summarize(rows)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out_path)
    after = payload["summary"]["overall"]
    print(json.dumps({"file": out_path.name, "rows_rejudged": len({t[0] for t in todo}), "verdicts_rejudged": len(todo),
                      "flipped_to_correct": flipped,
                      "strict_v2": [before.get("strict_v2"), after.get("strict_v2")],
                      "lenient_v2": [before.get("lenient_v2"), after.get("lenient_v2")]}), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contexts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="concise", help="concise | compute | <file in research/overnight/prompts>")
    ap.add_argument("--model", default="qwen3.8-9b-mlx")
    ap.add_argument("--route-model", default=None,
                    help="model id for the ROUTED compute call (incl. its two-stage retry / fallback / agentic re-ask); default = --model")
    ap.add_argument("--cascade-model", default=None, help="model id for the cascade call; default = --model")
    ap.add_argument("--judge-model", default="google/gemma-4-e4b")
    ap.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234"))
    ap.add_argument("--render", default="plain", choices=["plain", "quotes", "nodate", "saidon", "quotes_saidon", "quotes_user", "quotes_user_saidon", "chrono", "chrono_quotes"],
                    help="saidon: '(said on D Wkd)' + weekday on event dates; quotes_saidon: saidon, then source quotes; "
                         "chrono[_quotes]: memories sorted oldest first by event/observed date")
    ap.add_argument("--calendar", action="store_true", help="replace the date line with a deterministic calendar scaffold")
    ap.add_argument("--cascade", default="", help="prompt spec re-asked when the primary answer is \"I don't know\"")
    ap.add_argument("--route-pref", default="", help="prompt spec used instead of --prompt for suggest/recommend/tips questions")
    ap.add_argument("--route-compute", default="", help="prompt spec used instead of --prompt for duration/count/difference questions (COMPUTE_RE)")
    ap.add_argument("--route-compute-think", action="store_true",
                    help="the --route-compute primary call (compute-matching questions only) thinks (no prefill / reasoning_effort=none)")
    ap.add_argument("--route-compute-max-tokens", type=int, default=2500, help="max_tokens of the thinking routed compute call")
    ap.add_argument("--route-regex", default=None,
                    help="which questions --route-compute routes: wide (COMPUTE_RE, default) | tight (clear multi-memory arithmetic wording) | <custom regex>")
    ap.add_argument("--route-llm", action="store_true",
                    help="decide the --route-compute route with one short model call (--router-prompt); with an explicit --route-regex: regex OR llm")
    ap.add_argument("--router-prompt", default="router_v1", help="prompt spec of the --route-llm router call ({question} placeholder)")
    ap.add_argument("--think-early-stop", action="store_true",
                    help="two-stage budget for the --route-compute-think call: --route-compute-max-tokens-first first, one retry with the full "
                         "--route-compute-max-tokens if truncated with no Answer: line (rows record think_stage 1|2)")
    ap.add_argument("--route-compute-max-tokens-first", type=int, default=None,
                    help="stage-1 max_tokens for --think-early-stop (default 900; giving it alone also enables --think-early-stop)")
    ap.add_argument("--concise-max-tokens", type=int, default=160, help="default max_tokens of concise-mode calls (--max-tokens wins when non-zero)")
    ap.add_argument("--verify", default="", help="prompt spec for a post-answer verification call ({memories} {question} {answer}); replies SUPPORTED | UNSUPPORTED | FIX: <x>")
    ap.add_argument("--verify-apply-unsupported", default="keep", choices=["keep", "idk"],
                    help="on an UNSUPPORTED verdict keep the answer (default) or replace it with \"I don't know.\"")
    ap.add_argument("--agentic-search", action="store_true",
                    help="routed compute questions only: the thinking reader may write `SEARCH: <query>` once; the product `context` CLI "
                         "fetches more memories and the same prompt is re-asked (needs --route-compute-think and --db-dir or --db)")
    ap.add_argument("--db-dir", default="", help="--agentic-search, LME: directory holding <row id>.db per question")
    ap.add_argument("--db", default="", help="--agentic-search, LoCoMo: the single database file")
    ap.add_argument("--search-k", type=int, default=5, help="-k of the agentic `context` call")
    ap.add_argument("--search-bin", default=str(DEFAULT_SEARCH_BIN), help="neuralgraph-memory binary used by --agentic-search")
    ap.add_argument("--extra-judges", action="store_true", help="also grade strict_v2 and lenient_v2")
    ap.add_argument("--official-judge", action="store_true",
                    help="also grade `official` (LongMemEval's per-type yes/no judge prompts, run on --judge-model)")
    ap.add_argument("--k-limit", type=int, default=0)
    ap.add_argument("--quote-chars", type=int, default=300)
    ap.add_argument("--quotes-per-memory", type=int, default=0, help="0 = render default (3 for quotes, 1 for quotes_user)")
    ap.add_argument("--max-tokens", type=int, default=0, help="0 = by mode (160 concise / 700 compute / 600 auto)")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--prefill", dest="prefill", action="store_true", default=True, help="empty <think> prefill (Qwen)")
    ap.add_argument("--no-prefill", dest="prefill", action="store_false")
    ap.add_argument("--think", action="store_true", help="let the answerer think (no reasoning_effort=none, no prefill)")
    ap.add_argument("--think-fallback", dest="think_fallback", action="store_true", default=None,
                    help="if a thinking primary call yields no usable answer (e.g. hit the token cap), re-ask once without thinking "
                         "(empty-think prefill, reasoning_effort none, max_tokens 300); default ON with --think / --answer-model-think")
    ap.add_argument("--no-think-fallback", dest="think_fallback", action="store_false")
    ap.add_argument("--cascade-think", action="store_true",
                    help="cascade's second call only: thinking on (no prefill / reasoning_effort=none), --cascade-max-tokens")
    ap.add_argument("--cascade-max-tokens", type=int, default=3000, help="max_tokens of the thinking cascade call")
    ap.add_argument("--vote", type=int, default=1, help="self-consistency: N samples at --vote-temperature + the T=0 answer (1 = off)")
    ap.add_argument("--vote-temperature", type=float, default=0.7)
    ap.add_argument("--answer-model-think", action="store_true",
                    help="primary call thinks (no prefill / reasoning_effort=none); --max-tokens is honoured")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--types", default="", help="comma-separated type filter")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--judge-prefill", action="store_true", help="empty <think> prefill on judge calls (Qwen judges)")
    ap.add_argument("--regrade-v2-missing", action="store_true",
                    help="with --extra-judges and --resume (or --regrade-from): re-judge only strict_v2/lenient_v2 values that are False "
                         "and have no judge-cache entry (= the reply had no parsable VERDICT), then rewrite --out in place")
    ap.add_argument("--regrade-from", default="", help="result JSON whose answers are re-graded (no answering) under --judge-model")
    args = ap.parse_args()
    if args.think:
        args.prefill = False
    if args.think_fallback is None:
        args.think_fallback = bool(args.think or args.answer_model_think or (args.route_compute_think and args.route_compute))
    if args.route_compute_think and not args.route_compute:
        ap.error("--route-compute-think needs --route-compute")
    if args.answer_model_think and args.max_tokens == 0:
        print("WARN --answer-model-think with --max-tokens 0: the per-mode default (160/600/700) is too small for thinking; "
              "pass e.g. --max-tokens 3000", file=sys.stderr, flush=True)
    if args.vote < 1:
        ap.error("--vote must be >= 1")
    if args.route_compute_max_tokens_first is not None:
        args.think_early_stop = True
    if args.think_early_stop:
        if not args.route_compute_think:
            ap.error("--think-early-stop needs --route-compute-think")
        if args.route_compute_max_tokens_first is None:
            args.route_compute_max_tokens_first = 900
        if args.route_compute_max_tokens_first >= args.route_compute_max_tokens:
            ap.error("--route-compute-max-tokens-first must be smaller than --route-compute-max-tokens")
    args.route_regex_given = args.route_regex is not None
    if args.route_regex is None:
        args.route_regex = "wide"
    if args.route_llm:
        if not args.route_compute:
            ap.error("--route-llm needs --route-compute")
        load_prompt(args.router_prompt)  # fail early on a missing prompt file
    try:
        resolve_route_re(args.route_regex)
    except re.error as e:
        ap.error(f"--route-regex: {e}")
    if args.route_regex not in ("", "wide") and not args.route_compute:
        ap.error("--route-regex needs --route-compute")
    if args.concise_max_tokens < 1:
        ap.error("--concise-max-tokens must be >= 1")
    if args.agentic_search:
        if not args.route_compute_think:
            ap.error("--agentic-search needs --route-compute and --route-compute-think")
        if bool(args.db_dir) == bool(args.db):
            ap.error("--agentic-search needs exactly one of --db-dir (LME) or --db (LoCoMo)")
        if args.vote > 1:
            ap.error("--agentic-search does not combine with --vote")
        if args.search_k < 1:
            ap.error("--search-k must be >= 1")
    elif args.db_dir or args.db:
        ap.error("--db-dir / --db only apply with --agentic-search")

    template, mode = load_prompt(args.prompt)
    cascade = load_prompt(args.cascade) if args.cascade else None
    pref = load_prompt(args.route_pref) if args.route_pref else None
    compute = load_prompt(args.route_compute) if args.route_compute else None
    if args.verify:
        load_prompt(args.verify)  # fail early on a missing prompt file
    doc = json.loads(Path(args.contexts).read_text())
    rows_in = doc["rows"]
    if args.types:
        keep = {t.strip() for t in args.types.split(",")}
        rows_in = [r for r in rows_in if r["type"] in keep]
    if args.limit:
        rows_in = rows_in[: args.limit]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache = JudgeCache(out_path.with_suffix(".judgecache.json"))
    if args.regrade_v2_missing:
        regrade_v2_missing(args, doc, out_path, cache)
        return
    done: dict[str, dict] = {}
    if args.resume and out_path.exists():
        for r in json.loads(out_path.read_text())["rows"]:
            done[r["id"]] = r
    regrade: dict[str, dict] = {}
    if args.regrade_from:
        regrade = {r["id"]: r for r in json.loads(Path(args.regrade_from).read_text())["rows"]}
        rows_in = [r for r in rows_in if r["id"] in regrade]
    results: list[dict | None] = [None] * len(rows_in)
    lock = threading.Lock()
    t_start = time.perf_counter()

    def work(i: int):
        row = rows_in[i]
        if row["id"] in done:
            r0 = done[row["id"]]
            if args.extra_judges and not all(k in r0 for k in ("strict_v2", "lenient_v2")):
                g0 = grade(args, cache, r0["type"], r0["question"],
                           row["gold"] if r0["type"] != "abstention" else ABSTENTION_GOLD, r0["answer"])
                r0 = {**r0, "strict_v2": g0["strict_v2"], "lenient_v2": g0["lenient_v2"]}
            if args.official_judge and "official" not in r0:
                g0 = grade(args, cache, r0["type"], r0["question"],
                           row["gold"] if r0["type"] != "abstention" else ABSTENTION_GOLD, r0["answer"], official_gold=str(row["gold"]))
                r0 = {**r0, "official": g0["official"]}
            results[i] = {**r0, **{k + "_nonidk": nonidk_flag(r0, k) for k in ("lenient", "strict")}}
            return
        qtype = row["type"]
        gold = row["gold"] if qtype != "abstention" else ABSTENTION_GOLD
        if regrade:
            r0 = regrade[row["id"]]
            ans, raw, dt, meta = r0["answer"], r0.get("raw", ""), r0.get("answer_seconds", 0.0), {}
        else:
            ans, raw, dt, meta = answer_one(args, row, template, mode, pref, cascade, compute, cache)
        g = grade(args, cache, qtype, row["question"], gold, ans, official_gold=str(row["gold"]))
        r = {"id": row["id"], "type": qtype, "question": row["question"], "gold": str(row["gold"]), "answer": ans,
             "raw": raw if raw != ans else "", "n_ctx": row.get("n_ctx", 0), "ctx_chars": len(row.get("context") or ""),
             "answer_seconds": round(dt, 2), **g, "evidence_retrieved": row.get("evidence_retrieved"), **meta}
        r.update({k + "_nonidk": nonidk_flag(r, k) for k in ("lenient", "strict")})
        results[i] = r
        with lock:
            n_done = sum(1 for x in results if x is not None)
            print(f"[{n_done}/{len(rows_in)}] {qtype:<26} L={'Y' if g['lenient'] else '-'} S={'Y' if g['strict'] else '-'} "
                  f"sub={'Y' if g['substring'] else '-'}"
                  + (f" S2={'Y' if g['strict_v2'] else '-'} L2={'Y' if g['lenient_v2'] else '-'}" if args.extra_judges else "")
                  + (f" O={'Y' if g['official'] else '-'}" if args.official_judge else "")
                  + (" casc" if meta.get("cascaded") else "") + (f" {meta['routed']}" if meta.get("routed") else "")
                  + (f" src={meta['route_source']}" if meta.get("route_source") else "")
                  + (f" stage{meta['think_stage']}" if meta.get("think_stage") else "")
                  + (f" verify={meta['verify']}" if meta.get("verify") else "")
                  + (f" search={meta['agentic_query']!r}+{meta['agentic_added_lines']}" if meta.get("agentic_query") else "")
                  + f" | {ans[:70]!r}", flush=True)
            if n_done % 5 == 0:
                write(results, args, mode, t_start, out_path)

    def write(results, args, mode, t_start, out_path):
        rows = [r for r in results if r is not None]
        cfg = {k: v for k, v in vars(args).items()
               if k not in ("cascade_think", "cascade_max_tokens", "vote", "vote_temperature", "answer_model_think", "official_judge",
                                           "route_compute_think", "route_compute_max_tokens", "think_early_stop",
                                           "route_compute_max_tokens_first", "concise_max_tokens", "verify", "verify_apply_unsupported",
                                           "agentic_search", "db_dir", "db", "search_k", "search_bin",
                                           "route_model", "cascade_model", "route_llm", "router_prompt", "route_regex_given")}
        if args.route_model:
            cfg["route_model"] = args.route_model
        if args.cascade_model:
            cfg["cascade_model"] = args.cascade_model
        if args.official_judge:
            cfg["official_judge"] = True
        if args.route_llm:
            cfg.update(route_llm=True, router_prompt=args.router_prompt, route_regex_given=args.route_regex_given)
        if args.cascade_think:
            cfg.update(cascade_think=True, cascade_max_tokens=args.cascade_max_tokens)
        if args.vote > 1:
            cfg.update(vote=args.vote, vote_temperature=args.vote_temperature)
        if args.answer_model_think:
            cfg["answer_model_think"] = True
        if args.route_compute_think:
            cfg.update(route_compute_think=True, route_compute_max_tokens=args.route_compute_max_tokens)
        if args.think_early_stop:
            cfg.update(think_early_stop=True, route_compute_max_tokens_first=args.route_compute_max_tokens_first)
        if args.agentic_search:
            cfg.update(agentic_search=True, db_dir=args.db_dir, db=args.db, search_k=args.search_k)
        if args.concise_max_tokens != 160:
            cfg["concise_max_tokens"] = args.concise_max_tokens
        if args.verify:
            cfg.update(verify=args.verify, verify_apply_unsupported=args.verify_apply_unsupported)
        payload = {"config": {**cfg, "mode": mode, "elapsed_s": round(time.perf_counter() - t_start, 1)},
                   "summary": summarize(rows), "rows": rows}
        out_path.write_text(json.dumps(payload, indent=2))

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(work, range(len(rows_in))))
    write(results, args, mode, t_start, out_path)
    cache.flush()
    s = summarize([r for r in results if r is not None])
    print(json.dumps({"label": args.label, "overall": s["overall"], "answerable": s["answerable"]}, indent=1))


if __name__ == "__main__":
    main()
