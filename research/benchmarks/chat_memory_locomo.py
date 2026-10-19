"""Retrieval-only LoCoMo harness for the SHIPPED chat_memory pipeline.

Unlike research/benchmarks/runner.py (which drives the research Tesseract engine over raw messages),
this ingests a LoCoMo conversation through the real extractor/worker, then asks the questions through
MemoryRetriever and measures whether the evidence reached the retrieved set. No answer model, no judge.

  ingest:  python research/benchmarks/chat_memory_locomo.py ingest --db /tmp/a.db --sessions 6 --batch-size 6
           python research/benchmarks/chat_memory_locomo.py ingest --db /tmp/b.db --sessions 6 --batch-words 300
  eval:    python research/benchmarks/chat_memory_locomo.py eval --db /tmp/a.db --sessions 6

Metrics per retrieval variant (over eligible questions = non-adversarial, all evidence inside the ingested sessions):
  evidence@k   a retrieved memory cites one of the gold evidence messages (provenance, exact)
  gold@k       a gold-answer part is a substring of a retrieved memory's text (loose, like the research recall@k)
  ctx_evidence same as evidence@k but through context_with_results, i.e. what Claude would actually see
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig  # noqa: E402

DATASET = ROOT / "research/datasets/locomo10.json"


def load_conv(conv_idx: int):
    data = json.loads(DATASET.read_text())
    c = data[conv_idx]
    conv = c["conversation"]
    return conv, c["qa"]


def messages_for(conv, conv_idx: int, max_session: int, captions: bool = True):
    from NeuralGraph.temporal_utils import parse_datetime_flexible
    out = []
    i = 1
    while f"session_{i}" in conv and i <= max_session:
        dt = parse_datetime_flexible(conv.get(f"session_{i}_date_time", ""))
        for m in conv[f"session_{i}"]:
            text = m["text"] or ""
            if captions and m.get("blip_caption"):
                text = f"{text} [shared image: {m['blip_caption']}]".strip()
            out.append({"speaker": m["speaker"], "text": text, "sent_at": dt.isoformat() if dt else None,
                        "message_id": f"locomo-{conv_idx}-{m['dia_id']}"})
        i += 1
    return out


def sess_of(ev: str) -> int:
    return int(ev.split(":")[0][1:])


def evidence_ids(q) -> list[str]:
    """LoCoMo evidence lists occasionally join ids with ';' or ',' inside one string."""
    return [e.strip() for e in re.split(r"[;,\s]+", " ".join(q.get("evidence") or [])) if e.strip()]


def eligible_qa(qa, max_session: int):
    return [q for q in qa if q.get("category") != 5 and q.get("evidence")
            and all(sess_of(e) <= max_session for e in evidence_ids(q))]


def gold_parts(answer) -> list[str]:
    s = str(answer).lower().strip()
    parts = [p.strip(" .") for p in re.split(r",| and |;", s) if p.strip(" .")]
    return parts or [s]


# ----------------------------------------------------------------------------- ingest
async def do_ingest(args):
    conv, qa = load_conv(args.conv)
    msgs = messages_for(conv, args.conv, args.sessions, captions=not args.no_captions)
    cfg = ChatMemoryConfig()
    cfg.debounce_seconds = 0.0
    cfg.llm_model = args.model
    cfg.llm_base_url = args.base_url
    cfg.llm_max_parallel = args.parallel
    cfg.worker.concurrency = args.concurrency
    cfg.worker.batch_size = args.batch_size
    cfg.worker.batch_words = args.batch_words
    cfg.worker.maintenance_interval = 0
    cfg.extraction.max_memories_per_batch = args.max_memories
    cfg.extraction.max_triples_per_batch = max(12, args.max_memories + 4)
    cfg.extraction.generate_max_tokens = args.gen_tokens
    cfg.extraction.reconcile_max_tokens = args.reconcile_tokens
    cfg.extraction.llm_timeout = 600.0
    cfg.extraction.context_messages = args.context_messages
    cfg.extraction.gate_chitchat = not args.no_gate
    cfg.extraction.extract_relations = not args.no_relations
    if args.reconcile_cosine is not None:
        cfg.extraction.reconcile_cosine = args.reconcile_cosine
    if args.min_importance is not None:
        cfg.extraction.min_importance = args.min_importance
    db = Path(args.db).expanduser()
    if db.exists():
        db.unlink()
    cm = ChatMemory(db, config=cfg)
    t0 = time.perf_counter()
    chat_id = f"locomo-{args.conv}"
    await cm.add_messages(chat_id, msgs)
    batches = await cm.process_pending()
    dt = time.perf_counter() - t0
    m = cm.worker.metrics
    summary = {
        "db": str(db), "sessions": args.sessions, "messages": len(msgs),
        "words": sum(len(x["text"].split()) for x in msgs),
        "batch_size": args.batch_size, "batch_words": args.batch_words, "max_memories": args.max_memories,
        "context_messages": args.context_messages, "gate": not args.no_gate, "captions": not args.no_captions, "relations": not args.no_relations, "reconcile_cosine": cfg.extraction.reconcile_cosine,
        "batches": batches, "seconds": round(dt, 1), "sec_per_message": round(dt / max(1, len(msgs)), 2),
        "memories_added": m.memories_added, "memories_superseded": m.memories_superseded,
        "duplicates": m.duplicates, "relations": m.relations, "links": m.links,
        "messages_processed": m.messages_processed, "messages_skipped": m.messages_skipped,
        "failures": m.failures, "dead_jobs": m.dead_jobs, "last_error": m.last_error,
    }
    await cm.close()
    summary.update(db_stats(db))
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2))


def db_stats(db: Path) -> dict:
    c = sqlite3.connect(db)
    out = {}
    out["memories_active"] = c.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
    out["memories_total"] = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    out["entities"] = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    out["relations_active"] = c.execute("SELECT COUNT(*) FROM relations WHERE status='active'").fetchone()[0]
    out["msg_status"] = dict(c.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall())
    out["audit"] = dict(c.execute("SELECT kind, COUNT(*) FROM audit_log GROUP BY kind").fetchall())
    out["memory_kinds"] = dict(c.execute("SELECT kind, COUNT(*) FROM memories WHERE status='active' GROUP BY kind").fetchall())
    out["avg_memory_words"] = round(c.execute("SELECT AVG(LENGTH(text)-LENGTH(REPLACE(text,' ',''))+1) FROM memories WHERE status='active'").fetchone()[0] or 0, 1)
    c.close()
    return out


# ----------------------------------------------------------------------------- eval
async def do_eval(args):
    conv, qa = load_conv(args.conv)
    qs = eligible_qa(qa, args.sessions)
    speakers = [conv["speaker_a"], conv["speaker_b"]]
    cfg = ChatMemoryConfig()
    cfg.llm_model = args.model
    cfg.llm_base_url = args.base_url
    cfg.worker.maintenance_interval = 0
    cm = ChatMemory(Path(args.db).expanduser(), config=cfg)   # no start(): read-only, worker idle
    r = cm.retriever
    # provenance map straight from the DB (the retriever only loads source ids when with_sources=True,
    # and context_with_results hardcodes it off)
    _c = sqlite3.connect(Path(args.db).expanduser())
    src_map: dict[str, set[str]] = {}
    for mid, msg in _c.execute("SELECT memory_id, message_id FROM memory_sources"):
        src_map.setdefault(mid, set()).add(msg)
    _c.close()

    def named(q: str) -> list[str]:
        ql = q.lower()
        return [s for s in speakers if s.lower() in ql]

    variants = {
        "base_VKG_k10": lambda q: dict(k=10, channels="VKG"),
        "nograph_VK_k10": lambda q: dict(k=10, channels="VK"),
        "vector_only_k10": lambda q: dict(k=10, channels="V"),
        "route_hard_k10": lambda q: dict(k=10, channels="VKG", subject=(named(q)[0] if len(named(q)) == 1 else None)),
        "route_hard_nograph_k10": lambda q: dict(k=10, channels="VK", subject=(named(q)[0] if len(named(q)) == 1 else None)),
        "base_k15": lambda q: dict(k=15, channels="VKG"),
        "base_k5": lambda q: dict(k=5, channels="VKG"),
    }
    res = {v: {"evidence": 0, "gold": 0, "first_rank": [], "n_ret": [], "by_cat": Counter(), "n_by_cat": Counter()} for v in variants}
    res["ctx_k10"] = {"evidence": 0, "gold": 0, "first_rank": [], "n_ret": [], "by_cat": Counter(), "n_by_cat": Counter()}
    res["ctx_route_k10"] = {"evidence": 0, "gold": 0, "first_rank": [], "n_ret": [], "by_cat": Counter(), "n_by_cat": Counter()}
    res["SHIPPED_DEFAULTS"] = {"evidence": 0, "gold": 0, "first_rank": [], "n_ret": [], "by_cat": Counter(), "n_by_cat": Counter()}
    rows = []
    t0 = time.perf_counter()
    for q in qs:
        ev_ids = {f"locomo-{args.conv}-{e}" for e in evidence_ids(q)}
        parts = gold_parts(q["answer"])
        cat = q["category"]
        qemb = await cm.llm.embed(q["question"])
        row = {"q": q["question"], "cat": cat, "gold": q["answer"], "named": named(q["question"])}

        def score(name, hits):
            st = res[name]
            st["n_by_cat"][cat] += 1
            st["n_ret"].append(len(hits))
            texts = [h.memory.text.lower() for h in hits]
            ev_hit = next((i for i, h in enumerate(hits) if src_map.get(h.memory.memory_id, set()) & ev_ids), None)
            gold_hit = any(p in t for p in parts for t in texts)
            if ev_hit is not None:
                st["evidence"] += 1; st["by_cat"][cat] += 1; st["first_rank"].append(ev_hit + 1)
            if gold_hit:
                st["gold"] += 1
            row[name] = {"ev": ev_hit is not None, "gold": gold_hit, "rank": ev_hit}

        for name, mk in variants.items():
            kw = mk(q["question"])
            hits = await r.search(q["question"], query_embedding=qemb, with_sources=False, touch=False, **kw)
            score(name, hits)
        _, hits = await r.context_with_results(q["question"], k=10, query_embedding=qemb, touch=False)
        score("ctx_k10", hits)
        subj = named(q["question"])[0] if len(named(q["question"])) == 1 else None
        _, hits = await r.context_with_results(q["question"], k=10, query_embedding=qemb, touch=False, subject=subj)
        score("ctx_route_k10", hits)
        _, hits = await r.context_with_results(q["question"], query_embedding=qemb, touch=False)   # no overrides
        score("SHIPPED_DEFAULTS", hits)
        rows.append(row)
    await cm.close()

    n = len(qs)
    table = []
    for name, st in res.items():
        table.append({
            "variant": name, "n": n,
            "evidence@k": round(100 * st["evidence"] / n, 1),
            "gold@k": round(100 * st["gold"] / n, 1),
            "mean_first_rank": round(sum(st["first_rank"]) / len(st["first_rank"]), 2) if st["first_rank"] else None,
            "mean_retrieved": round(sum(st["n_ret"]) / n, 1),
            "evidence_by_cat": {str(c): f"{st['by_cat'][c]}/{st['n_by_cat'][c]}" for c in sorted(st["n_by_cat"])},
        })
    out = {"db": args.db, "sessions": args.sessions, "questions": n, "eval_seconds": round(time.perf_counter() - t0, 1),
           "stats": db_stats(Path(args.db).expanduser()), "variants": table}
    print(json.dumps(out, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({**out, "rows": rows}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "eval"):
        s = sub.add_parser(name)
        s.add_argument("--db", required=True)
        s.add_argument("--conv", type=int, default=0)
        s.add_argument("--sessions", type=int, default=6)
        s.add_argument("--model", default="qwen3.8-9b-mlx")
        s.add_argument("--base-url", default="http://127.0.0.1:1234")
        s.add_argument("--out", default=None)
    s = sub.choices["ingest"]
    s.add_argument("--batch-size", type=int, default=6)
    s.add_argument("--batch-words", type=int, default=0)
    s.add_argument("--max-memories", type=int, default=8)
    s.add_argument("--context-messages", type=int, default=6)
    s.add_argument("--gen-tokens", type=int, default=3500)
    s.add_argument("--reconcile-tokens", type=int, default=1200)
    s.add_argument("--min-importance", type=float, default=None)
    s.add_argument("--no-gate", action="store_true")
    s.add_argument("--no-captions", action="store_true", help="drop LoCoMo image captions (the pre-fix behaviour)")
    s.add_argument("--no-relations", action="store_true", help="skip the entity/relation extraction call")
    s.add_argument("--reconcile-cosine", type=float, default=None)
    s.add_argument("--concurrency", type=int, default=1)
    s.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    asyncio.run(do_ingest(args) if args.cmd == "ingest" else do_eval(args))


if __name__ == "__main__":
    main()
