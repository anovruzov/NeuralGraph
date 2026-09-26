"""Cross-chat memory demo.

Feeds demo/data/sample_chats.jsonl (five chats, one person, several months) into the memory system,
lets the background Qwen worker digest them, then asks questions that span chats.

    # with a real local model (LM Studio or Ollama; see NeuralGraph/llm_backend.py env vars)
    LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text \
        .venv/bin/python demo/chat_memory_demo.py --user-name "Ali Novruzov"

    # without any server (deterministic scripted fake)
    .venv/bin/python demo/chat_memory_demo.py --fake-llm

    # keep the dashboard + MCP endpoint up after the demo
    .venv/bin/python demo/chat_memory_demo.py --fake-llm --serve
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig  # noqa: E402

QUESTIONS = [
    "Where does Ali live now?",
    "What is Ali allergic to?",
    "Who is Jonas?",
    "When is Ali's mother's birthday?",
    "What are Ali's plans for the autumn?",
    "Tell me about Luna",
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="SQLite path (default: a temp file)")
    ap.add_argument("--fake-llm", action="store_true")
    ap.add_argument("--user-name", default="Ali Novruzov")
    ap.add_argument("--serve", action="store_true", help="keep the dashboard at http://127.0.0.1:8765 running afterwards")
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()

    cfg = ChatMemoryConfig()
    cfg.extraction.speaker_names["user"] = args.user_name
    cfg.debounce_seconds = 0.0
    cfg.llm_max_parallel = args.parallel
    cfg.worker.concurrency = min(args.parallel, 4)
    llm = None
    if args.fake_llm:
        from NeuralGraph.chat_memory.testing import scripted_fake_llm

        llm = scripted_fake_llm(latency=0.05)
    db = args.db or str(Path(tempfile.mkdtemp()) / "chat_memory_demo.db")
    cm = ChatMemory(db, llm=llm, config=cfg)
    await cm.start()

    path = Path(__file__).resolve().parent / "data" / "sample_chats.jsonl"
    t0 = time.perf_counter()
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        await cm.add_message(rec["chat_id"], rec["speaker"], rec["text"], role=rec.get("role", ""), sent_at=rec.get("sent_at"))
        n += 1
    print(f"queued {n} messages in {(time.perf_counter() - t0) * 1000:.0f} ms (the chat path never waits for the model)")
    print("worker is digesting in the background ...", flush=True)
    t1 = time.perf_counter()
    while not await cm.wait_until_idle(timeout=5):
        m = cm.worker.metrics
        print(f"  {m.batches} batches, {m.memories_added} memories, {m.relations} relations, {m.failures} failures", flush=True)
    m = cm.worker.metrics
    print(f"digested in {time.perf_counter() - t1:.1f}s: {m.messages_processed} processed, {m.messages_skipped} skipped, "
          f"{m.memories_added} memories, {m.memories_superseded} superseded, {m.duplicates} duplicates, {m.relations} relations\n")

    print("== memories ==")
    for mem in await cm.memories(limit=100, order="observed_at ASC"):
        when = f" [{mem.event_time}]" if mem.event_time else ""
        print(f"  ({mem.kind}) {mem.text}{when}  <- {mem.chat_id}")
    print("\n== relations ==")
    for e in await cm.store.top_entities(5):
        for r in await cm.store.relations_for(e.entity_id, limit=8):
            print(f"  {r.subject_id} --{r.predicate}--> {r.object_id}  (x{r.observation_count}, {r.chat_id})")
    print("\n== questions across chats ==")
    for q in QUESTIONS:
        hits = await cm.search(q, k=3)
        print(f"Q: {q}")
        for h in hits:
            print(f"   {h.score:.3f} {h.memory.text}  ({h.memory.chat_id}; {h.explanation})")
    print("\n== context block for a new chat ==")
    print(await cm.context_for("planning a trip and a dinner with the family"))
    print("\n== ledger & grade ==")
    print(json.dumps(await cm.token_ledger(), indent=1))
    print(json.dumps(await cm.grade(), indent=1))

    if args.serve:
        from NeuralGraph.chat_memory.ui.server import run_server

        runner = await run_server(cm, host="127.0.0.1", port=8765)
        print("\ndashboard: http://127.0.0.1:8765/   MCP: http://127.0.0.1:8765/mcp   (Ctrl-C to stop)")
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        await runner.cleanup()
    await cm.close()
    print(f"\ndb: {db}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
