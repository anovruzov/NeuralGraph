"""End-to-end ACCURACY for the shipped chat_memory pipeline, fully local.

Takes databases already built by chat_memory_locomo.py / chat_memory_longmemeval.py, builds the context block
with the shipped defaults, has a local model answer, then grades the answer three ways:
  lenient   LLM judge (different model family), "is the response correct given the gold answer?" with the
            LongMemEval per-type conventions (abstention = must decline; knowledge-update = latest value)
  strict    LLM judge, every part of the gold answer must be present, no partial credit, no hedging
  substring gold (or every comma/and-split part) is a substring of the answer

  python research/benchmarks/chat_memory_answer_eval.py lme --rows .context/exp/lme_oracle_assistant_v2.json --db-dir /tmp/lme_assistant_v2 --out ...
  python research/benchmarks/chat_memory_answer_eval.py locomo --db /tmp/exp_F_prompt_only.db --sessions 8 --out ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research/benchmarks"))

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig  # noqa: E402

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


async def chat(session: aiohttp.ClientSession, base_url: str, model: str, prompt: str, *, max_tokens: int, prefill: bool) -> str:
    msgs = [{"role": "user", "content": prompt}]
    if prefill:
        msgs.append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
    body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens, "stream": False, "reasoning_effort": "none"}
    async with session.post(f"{base_url}/v1/chat/completions", json=body, timeout=aiohttp.ClientTimeout(total=600)) as r:
        r.raise_for_status()
        j = await r.json()
    txt = (j.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()


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


async def grade(session, args, qtype, question, gold, answer):
    lenient = await chat(session, args.base_url, args.judge_model, LENIENT_JUDGE.format(qtype=qtype, question=question, gold=gold, answer=answer), max_tokens=8, prefill=False)
    strict = await chat(session, args.base_url, args.judge_model, STRICT_JUDGE.format(qtype=qtype, question=question, gold=gold, answer=answer), max_tokens=8, prefill=False)
    return verdict(lenient), verdict(strict), substring_ok(gold, answer)


def cfg_for(args) -> ChatMemoryConfig:
    cfg = ChatMemoryConfig(); cfg.llm_model = args.model; cfg.llm_base_url = args.base_url; cfg.worker.maintenance_interval = 0
    return cfg


async def answer_from_db(session, args, db: Path, question: str, date_line: str) -> tuple[str, str, int]:
    cm = ChatMemory(db, config=cfg_for(args))
    block, hits = await cm.retriever.context_with_results(question, touch=False)
    await cm.close()
    memories = block if block else "(no relevant memories)"
    if args.answer_style == "compute":
        raw = await chat(session, args.base_url, args.model, COMPUTE_PROMPT.format(date_line=date_line, memories=memories, question=question), max_tokens=700, prefill=args.prefill)
        m = re.findall(r"Answer:\s*(.+)", raw)
        ans = m[-1].strip() if m else raw.strip().split("\n")[-1].strip()
    else:
        ans = await chat(session, args.base_url, args.model, ANSWER_PROMPT.format(date_line=date_line, memories=memories, question=question), max_tokens=160, prefill=args.prefill)
    return ans, block, len(hits)


async def run_lme(args):
    rows = json.loads(Path(args.rows).read_text())["rows"]
    data = {q["question_id"]: q for q in json.loads((ROOT / "research/datasets/longmemeval/longmemeval_oracle.json").read_text())}
    out_rows = []
    async with aiohttp.ClientSession() as session:
        for i, r in enumerate(rows):
            q = data[r["question_id"]]
            qtype = "abstention" if q["question_id"].endswith("_abs") else q["question_type"]
            gold = q["answer"] if qtype != "abstention" else "(no information available; the correct response is to say so)"
            t0 = time.perf_counter()
            ans, block, n = await answer_from_db(session, args, Path(args.db_dir) / f"{q['question_id']}.db", q["question"], f"Today's date: {q['question_date']}")
            t_ans = time.perf_counter() - t0
            len_ok, str_ok, sub_ok = await grade(session, args, qtype, q["question"], gold, ans)
            out_rows.append({"id": q["question_id"], "type": qtype, "question": q["question"], "gold": str(q["answer"]), "answer": ans,
                             "n_ctx": n, "ctx_chars": len(block), "answer_seconds": round(t_ans, 2), "lenient": len_ok, "strict": str_ok, "substring": sub_ok,
                             "evidence_retrieved": r.get("ctx_turn_rank") is not None})
            print(f"[{i+1}/{len(rows)}] {qtype:<26} L={'Y' if len_ok else '-'} S={'Y' if str_ok else '-'} sub={'Y' if sub_ok else '-'} | {ans[:70]!r}", flush=True)
            if args.out:
                Path(args.out).write_text(json.dumps({"summary": summarize(out_rows), "rows": out_rows}, indent=2))
    print(json.dumps(summarize(out_rows), indent=2))


async def run_locomo(args):
    from chat_memory_locomo import load_conv, eligible_qa, evidence_ids  # noqa: E402
    conv, qa = load_conv(args.conv)
    qs = eligible_qa(qa, args.sessions)
    names = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
    out_rows = []
    async with aiohttp.ClientSession() as session:
        for i, q in enumerate(qs):
            qtype = names.get(q["category"], str(q["category"]))
            t0 = time.perf_counter()
            ans, block, n = await answer_from_db(session, args, Path(args.db), q["question"], "")
            t_ans = time.perf_counter() - t0
            len_ok, str_ok, sub_ok = await grade(session, args, qtype, q["question"], q["answer"], ans)
            out_rows.append({"type": qtype, "question": q["question"], "gold": str(q["answer"]), "answer": ans, "n_ctx": n, "ctx_chars": len(block),
                             "answer_seconds": round(t_ans, 2), "lenient": len_ok, "strict": str_ok, "substring": sub_ok})
            print(f"[{i+1}/{len(qs)}] {qtype:<12} L={'Y' if len_ok else '-'} S={'Y' if str_ok else '-'} sub={'Y' if sub_ok else '-'} | gold={str(q['answer'])[:30]!r} ans={ans[:60]!r}", flush=True)
            if args.out:
                Path(args.out).write_text(json.dumps({"summary": summarize(out_rows), "rows": out_rows}, indent=2))
    print(json.dumps(summarize(out_rows), indent=2))


def summarize(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r)
    pct = lambda rs, k: round(100 * sum(1 for r in rs if r[k]) / max(1, len(rs)), 1)
    table = [{"type": t, "n": len(rs), "lenient": pct(rs, "lenient"), "strict": pct(rs, "strict"), "substring": pct(rs, "substring"),
              "mean_ctx_tokens": round(sum(r["ctx_chars"] for r in rs) / 4 / len(rs)), "mean_answer_s": round(sum(r["answer_seconds"] for r in rs) / len(rs), 2)}
             for t, rs in sorted(by.items())]
    overall = {"n": len(rows), "lenient": pct(rows, "lenient"), "strict": pct(rows, "strict"), "substring": pct(rows, "substring"),
               "mean_ctx_tokens": round(sum(r["ctx_chars"] for r in rows) / 4 / max(1, len(rows))),
               "mean_answer_s": round(sum(r["answer_seconds"] for r in rows) / max(1, len(rows)), 2)}
    return {"overall": overall, "by_type": table}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lme", "locomo"):
        s = sub.add_parser(name)
        s.add_argument("--model", default="qwen3.8-9b-mlx")
        s.add_argument("--judge-model", default="google/gemma-4-e4b")
        s.add_argument("--base-url", default="http://127.0.0.1:1234")
        s.add_argument("--prefill", action="store_true", default=True, help="empty-think prefill for the answer model (Qwen3.8)")
        s.add_argument("--out", default=None)
        s.add_argument("--answer-style", choices=["concise", "compute"], default="concise")
    sub.choices["lme"].add_argument("--rows", required=True); sub.choices["lme"].add_argument("--db-dir", required=True)
    sub.choices["locomo"].add_argument("--db", required=True); sub.choices["locomo"].add_argument("--sessions", type=int, default=8); sub.choices["locomo"].add_argument("--conv", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(run_lme(args) if args.cmd == "lme" else run_locomo(args))


if __name__ == "__main__":
    main()
