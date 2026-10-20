"""LongMemEval (Wu et al., ICLR 2025) on the SHIPPED chat_memory pipeline, retrieval-only, fully local.

Each question has its own chat history (a list of user/assistant sessions with dates). For every sampled
question this script builds a fresh database, ingests each session as its own chat through the real
extractor/worker, then asks the question through MemoryRetriever and checks whether a retrieved memory
cites a turn the dataset marks `has_answer: true`. No answer model, no judge.

  python research/benchmarks/chat_memory_longmemeval.py --n 50 --seed 1 --out .context/exp/lme_oracle_default.json
  python research/benchmarks/chat_memory_longmemeval.py --n 50 --seed 1 --extract-assistant --out ...

Metrics per question type:
  turn@k      a retrieved memory (k = shipped context_k) cites a has_answer turn           (exact, provenance)
  session@k   a retrieved memory cites any turn of an answer session                       (looser)
  ceiling     some memory in the DB cites a has_answer turn at all (extraction, not retrieval)
  abstain     for *_abs questions: context_for returned an empty block (the right behaviour is "nothing relevant")
Dataset: research/datasets/longmemeval/longmemeval_oracle.json (HF xiaowu0162/longmemeval-cleaned, MIT).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig  # noqa: E402

DEFAULT_FILE = ROOT / "research/datasets/longmemeval/longmemeval_oracle.json"
_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s*(?:\([A-Za-z]{3}\))?\s*(\d{1,2}):(\d{2})")


def parse_lme_date(s: str) -> str | None:
    """'2023/04/10 (Mon) 17:50' -> '2023-04-10T17:50:00'"""
    m = _DATE_RE.search(s or "")
    if not m:
        return None
    y, mo, d, hh, mm = (int(x) for x in m.groups())
    return datetime(y, mo, d, hh, mm).isoformat()


def stratified_sample(data: list[dict], n: int, seed: int, min_per_type: int = 3) -> list[dict]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for q in data:
        by_type[q["question_type"]].append(q)
    rng = random.Random(seed)
    total = len(data)
    out: list[dict] = []
    for t, qs in sorted(by_type.items()):
        k = max(min_per_type, round(n * len(qs) / total))
        out.extend(rng.sample(qs, min(k, len(qs))))
    rng.shuffle(out)
    return out[:max(n, len(out))] if len(out) <= n else out[:n]


def make_config(args) -> ChatMemoryConfig:
    cfg = ChatMemoryConfig()
    cfg.debounce_seconds = 0.0
    cfg.llm_model = args.model
    cfg.llm_base_url = args.base_url
    cfg.llm_max_parallel = args.parallel
    cfg.worker.concurrency = args.concurrency
    cfg.worker.maintenance_interval = 0
    cfg.extraction.llm_timeout = 600.0
    if args.extract_assistant is not None:
        cfg.extraction.extract_assistant = args.extract_assistant
    cfg.extraction.speaker_names = {"user": args.user_name}
    if args.batch_size:
        cfg.worker.batch_size = args.batch_size
    return cfg


async def run_question(q: dict, args, db_dir: Path) -> dict:
    db = db_dir / f"{q['question_id']}.db"
    if db.exists():
        db.unlink()
    cm = ChatMemory(db, config=make_config(args))
    # message ids: <session_id>:<turn index>; has_answer turns are the gold set
    gold_turns: set[str] = set()
    answer_sessions = set(q.get("answer_session_ids") or [])
    n_msgs = 0
    t0 = time.perf_counter()
    for sid, sdate, turns in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"]):
        sent_at = parse_lme_date(sdate)
        batch = []
        for i, t in enumerate(turns):
            mid = f"{sid}:{i}"
            role = (t.get("role") or "user").lower()
            batch.append({"speaker": "assistant" if role == "assistant" else "user", "role": role,
                          "text": t.get("content") or "", "sent_at": sent_at, "message_id": mid})
            if t.get("has_answer"):
                gold_turns.add(mid)
        await cm.add_messages(sid, batch)
        n_msgs += len(batch)
    batches = await cm.process_pending()
    t_extract = time.perf_counter() - t0
    m = cm.worker.metrics

    # retrieval with the shipped defaults (context_with_results: k, channels, floors from config)
    r = cm.retriever
    block, hits = await r.context_with_results(q["question"], touch=False)
    hits_search = await r.search(q["question"], k=r.config.context_k, with_sources=False, touch=False)
    await cm.close()

    c = sqlite3.connect(db)
    src: dict[str, set[str]] = defaultdict(set)
    for mid, msg in c.execute("SELECT ms.memory_id, ms.message_id FROM memory_sources ms JOIN memories m ON m.memory_id=ms.memory_id WHERE m.status='active'"):
        src[mid].add(msg)
    n_mem = c.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
    c.close()
    cited = set().union(*src.values()) if src else set()

    def sess_of(mid: str) -> str:
        return mid.rsplit(":", 1)[0]

    def turn_hit(hs):
        return next((i for i, h in enumerate(hs) if src.get(h.memory.memory_id, set()) & gold_turns), None)

    def sess_hit(hs):
        return next((i for i, h in enumerate(hs) if {sess_of(x) for x in src.get(h.memory.memory_id, set())} & answer_sessions), None)

    is_abs = q["question_id"].endswith("_abs")
    return {
        "question_id": q["question_id"], "type": q["question_type"], "abstention": is_abs,
        "question": q["question"], "answer": q["answer"],
        "sessions": len(q["haystack_sessions"]), "messages": n_msgs, "gold_turns": len(gold_turns),
        "memories": n_mem, "batches": batches, "extract_seconds": round(t_extract, 1),
        "skipped": m.messages_skipped, "failures": m.failures, "dead": m.dead_jobs,
        "ceiling_turn": bool(cited & gold_turns),
        "ceiling_session": bool({sess_of(x) for x in cited} & answer_sessions),
        "ctx_turn_rank": turn_hit(hits), "ctx_session_rank": sess_hit(hits), "ctx_n": len(hits), "ctx_empty": block == "",
        "search_turn_rank": turn_hit(hits_search), "search_session_rank": sess_hit(hits_search),
        "context_preview": block[:400],
    }


def summarize(rows: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)
    table = []
    for t, rs in sorted(by_type.items()):
        n = len(rs)
        non_abs = [r for r in rs if not r["abstention"]]
        abs_rows = [r for r in rs if r["abstention"]]
        table.append({
            "type": t, "n": n,
            "turn@k": round(100 * sum(r["ctx_turn_rank"] is not None for r in non_abs) / max(1, len(non_abs)), 1),
            "session@k": round(100 * sum(r["ctx_session_rank"] is not None for r in non_abs) / max(1, len(non_abs)), 1),
            "ceiling_turn": round(100 * sum(r["ceiling_turn"] for r in non_abs) / max(1, len(non_abs)), 1),
            "abstain_ok": (round(100 * sum(r["ctx_empty"] for r in abs_rows) / len(abs_rows), 1) if abs_rows else None),
            "n_abs": len(abs_rows),
            "mean_memories": round(sum(r["memories"] for r in rs) / n, 1),
            "mean_extract_s": round(sum(r["extract_seconds"] for r in rs) / n, 1),
        })
    non_abs = [r for r in rows if not r["abstention"]]
    abs_rows = [r for r in rows if r["abstention"]]
    overall = {
        "n": len(rows), "n_non_abstention": len(non_abs),
        "turn@k": round(100 * sum(r["ctx_turn_rank"] is not None for r in non_abs) / max(1, len(non_abs)), 1),
        "session@k": round(100 * sum(r["ctx_session_rank"] is not None for r in non_abs) / max(1, len(non_abs)), 1),
        "ceiling_turn": round(100 * sum(r["ceiling_turn"] for r in non_abs) / max(1, len(non_abs)), 1),
        "abstain_ok": (round(100 * sum(r["ctx_empty"] for r in abs_rows) / len(abs_rows), 1) if abs_rows else None),
        "total_messages": sum(r["messages"] for r in rows), "total_memories": sum(r["memories"] for r in rows),
        "total_extract_s": round(sum(r["extract_seconds"] for r in rows), 1),
    }
    return {"overall": overall, "by_type": table}


async def main_async(args):
    data = json.loads(Path(args.file).read_text())
    sample = stratified_sample(data, args.n, args.seed) if args.n else data
    if args.types:
        keep = {t.strip() for t in args.types.split(",")}
        sample = [q for q in sample if q["question_type"] in keep]
    db_dir = Path(args.db_dir).expanduser(); db_dir.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else None
    rows: list[dict] = []
    done: set[str] = set()
    if out and out.exists() and args.resume:
        rows = json.loads(out.read_text()).get("rows", [])
        done = {r["question_id"] for r in rows}
    print(f"{len(sample)} questions ({len(done)} already done); types: {dict(Counter(q['question_type'] for q in sample))}", flush=True)
    for i, q in enumerate(sample):
        if q["question_id"] in done:
            continue
        row = await run_question(q, args, db_dir)
        rows.append(row)
        print(f"[{i+1}/{len(sample)}] {row['type']:<26} msgs={row['messages']:>3} mem={row['memories']:>3} "
              f"{row['extract_seconds']:>6.1f}s  ceiling={'Y' if row['ceiling_turn'] else '-'} "
              f"ctx_turn={row['ctx_turn_rank']} ctx_sess={row['ctx_session_rank']} empty={row['ctx_empty']}", flush=True)
        if out:
            out.write_text(json.dumps({"config": vars(args), "summary": summarize(rows), "rows": rows}, indent=2))
    s = summarize(rows)
    print(json.dumps(s, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--n", type=int, default=50, help="stratified sample size (0 = all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--db-dir", default="/tmp/lme")
    ap.add_argument("--out", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--model", default="qwen3.8-9b-mlx")
    ap.add_argument("--base-url", default="http://127.0.0.1:1234")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--concurrency", type=int, default=2, help="sessions (chats) extracted in parallel")
    ap.add_argument("--batch-size", type=int, default=0, help="0 = shipped default")
    ap.add_argument("--extract-assistant", dest="extract_assistant", action="store_true", default=None)
    ap.add_argument("--no-extract-assistant", dest="extract_assistant", action="store_false")
    ap.add_argument("--user-name", default="The user")
    ap.add_argument("--types", default=None, help="comma-separated question_type filter applied after sampling")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
