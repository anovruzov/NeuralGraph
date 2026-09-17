"""Command line for the cross-chat memory system.

    python -m NeuralGraph.chat_memory serve     # worker + dashboard + REST API + MCP (Streamable HTTP)
    python -m NeuralGraph.chat_memory mcp       # worker + MCP over stdio (Claude Desktop / Claude Code)
    python -m NeuralGraph.chat_memory ingest chats.jsonl [--process]
    python -m NeuralGraph.chat_memory process   # drain the queue in the foreground
    python -m NeuralGraph.chat_memory search "where does Ali live?"
    python -m NeuralGraph.chat_memory context "planning a trip"
    python -m NeuralGraph.chat_memory profile Ali
    python -m NeuralGraph.chat_memory stats
    python -m NeuralGraph.chat_memory forget mem_...

Common options: --db, --model, --base-url, --embed-model, --parallel, --user-name, --fake-llm (tests/demos).
LLM settings default to NeuralGraph.llm_backend's env vars (LLM_BASE_URL, LLM_MODEL, EMBED_MODEL).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from .service import ChatMemory, ChatMemoryConfig

DEFAULT_DB = os.environ.get("NEURALGRAPH_MEMORY_DB", "~/.neuralgraph/chat_memory.db")


def _build(args: argparse.Namespace) -> ChatMemory:
    cfg = ChatMemoryConfig()
    cfg.llm_model = args.model
    cfg.llm_base_url = args.base_url
    cfg.embed_model = args.embed_model
    cfg.llm_max_parallel = args.parallel
    cfg.worker.concurrency = args.workers if args.workers else max(1, min(args.parallel, 4))
    cfg.worker.batch_size = args.batch_size
    cfg.debounce_seconds = args.debounce
    if args.user_name:
        cfg.extraction.speaker_names["user"] = args.user_name
    if args.extract_assistant:
        cfg.extraction.extract_assistant = True
    llm = None
    if args.fake_llm:
        from .testing import scripted_fake_llm

        llm = scripted_fake_llm()
    return ChatMemory(args.db, llm=llm, config=cfg)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB}; env NEURALGRAPH_MEMORY_DB)")
    p.add_argument("--model", default=None, help="generation model (default: LLM_MODEL env / llm_backend default)")
    p.add_argument("--base-url", default=None, help="LM Studio / Ollama URL (default: LLM_BASE_URL env)")
    p.add_argument("--embed-model", default=None, help="embedding model (default: EMBED_MODEL env)")
    p.add_argument("--parallel", type=int, default=4, help="max concurrent LLM calls (match OLLAMA_NUM_PARALLEL)")
    p.add_argument("--workers", type=int, default=0, help="chats processed concurrently (default min(parallel,4))")
    p.add_argument("--batch-size", type=int, default=6, help="messages per extraction pass")
    p.add_argument("--debounce", type=float, default=1.0, help="seconds to wait so a turn and its reply batch together")
    p.add_argument("--user-name", default=None, help="display name for speaker 'user' (e.g. your name)")
    p.add_argument("--extract-assistant", action="store_true", help="also extract memories from assistant turns")
    p.add_argument("--fake-llm", action="store_true", help="deterministic fake model (demos/tests, no server)")
    p.add_argument("-v", "--verbose", action="store_true")


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _locomo_messages(path: Path, only: set[int] | None = None):
    """Yield (chat_id, message dict) from a LoCoMo-format file (evaluation/locomo/locomo10.json)."""
    from ..temporal_utils import parse_datetime_flexible

    data = json.loads(path.read_text(encoding="utf-8"))
    for ci, conv in enumerate(data):
        if only and ci not in only:
            continue
        c = conv.get("conversation", conv)
        i = 1
        while f"session_{i}" in c:
            dt = parse_datetime_flexible(c.get(f"session_{i}_date_time", ""))
            for m in c[f"session_{i}"]:
                yield f"locomo-{ci}", {"speaker": m.get("speaker", "Unknown"), "text": m.get("text", ""),
                                       "sent_at": dt.isoformat() if dt else None,
                                       "message_id": f"locomo-{ci}-{m.get('dia_id', '')}" if m.get("dia_id") else None}
            i += 1


async def cmd_serve(args: argparse.Namespace) -> int:
    from .ui.server import run_server

    cm = _build(args)
    await cm.start()
    runner = await run_server(cm, host=args.host, port=args.port, mcp_token=args.mcp_token or os.environ.get("NEURALGRAPH_MCP_TOKEN"),
                              api_token=args.api_token or os.environ.get("NEURALGRAPH_API_TOKEN"),
                              allowed_origins=args.allowed_origin or None, cors_origins=args.cors_origin or None,
                              allowed_hosts=args.allowed_host or None)
    print(f"dashboard: http://{args.host}:{args.port}/   MCP: http://{args.host}:{args.port}/mcp   db: {cm.store.db_path}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await stop.wait()
    print("shutting down…", flush=True)
    await runner.cleanup()
    await cm.close()
    return 0


async def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import serve_stdio

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)   # stdout is the protocol channel
    cm = _build(args)
    await cm.start()
    try:
        await serve_stdio(cm)
    finally:
        await cm.close()
    return 0


async def cmd_ingest(args: argparse.Namespace) -> int:
    cm = _build(args)
    path = Path(args.file)
    n = 0
    if args.locomo:
        only = {int(x) for x in args.only.split(",")} if args.only else None
        for chat_id, m in _locomo_messages(path, only):
            await cm.add_message(chat_id, m["speaker"], m["text"], sent_at=m.get("sent_at"), message_id=m.get("message_id"))
            n += 1
    else:
        for rec in _iter_jsonl(path):
            chat_id = rec.get("chat_id") or args.chat_id
            if not chat_id:
                print("record without chat_id and no --chat-id given", file=sys.stderr)
                return 2
            await cm.add_message(chat_id, rec.get("speaker") or rec.get("role") or "user", rec.get("text", ""),
                                 role=rec.get("role", ""), sent_at=rec.get("sent_at") or rec.get("timestamp"),
                                 message_id=rec.get("message_id") or rec.get("id"), metadata=rec.get("metadata"))
            n += 1
    print(f"ingested {n} messages into {cm.store.db_path}; queue: {await cm.store.job_counts()}")
    if args.process:
        batches = await cm.process_pending()
        print(f"processed {batches} batches: {json.dumps(cm.worker.metrics.as_dict() | {'recent': None}, default=str)[:400]}")
    await cm.close()
    return 0


async def cmd_process(args: argparse.Namespace) -> int:
    cm = _build(args)
    batches = await cm.process_pending(args.max_batches)
    m = cm.worker.metrics
    print(f"{batches} batches, {m.messages_processed} messages processed, {m.messages_skipped} skipped, "
          f"{m.memories_added} memories added, {m.memories_superseded} superseded, {m.duplicates} duplicates, "
          f"{m.relations} relations, {m.failures} failures")
    await cm.close()
    return 0


async def cmd_search(args: argparse.Namespace) -> int:
    cm = _build(args)
    hits = await cm.search(args.query, k=args.k, subject=args.subject, include_superseded=args.history)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=1, default=str))
    else:
        for h in hits:
            m = h.memory
            print(f"{h.score:.3f}  [{m.kind}] {m.text}   ({m.subject_name}; chat {m.chat_id}; {(m.event_time or m.observed_at or '')[:10]}; {h.explanation})")
        if not hits:
            print("(no relevant memories)")
    await cm.close()
    return 0


async def cmd_context(args: argparse.Namespace) -> int:
    cm = _build(args)
    print(await cm.context_for(args.query, k=args.k, max_chars=args.max_chars) or "(nothing relevant)")
    await cm.close()
    return 0


async def cmd_profile(args: argparse.Namespace) -> int:
    cm = _build(args)
    prof = await cm.profile(args.subject)
    if args.json:
        print(json.dumps(prof, ensure_ascii=False, indent=1, default=str))
    else:
        print(f"{prof['name']} — {prof['memory_count']} memories")
        for kind, mems in prof["memories_by_kind"].items():
            print(f"  {kind}:")
            for m in mems:
                print(f"    - {m['text']}  ({(m.get('event_time') or m.get('observed_at') or '')[:10]}, chat {m['chat_id']})")
        if prof["relations"]:
            print("  relations:")
            for r in prof["relations"]:
                print(f"    - {r['subject_id']} —{r['predicate']}→ {r['object_id']}  (x{r['observation_count']})")
    await cm.close()
    return 0


async def cmd_stats(args: argparse.Namespace) -> int:
    cm = _build(args)
    st = await cm.status()
    st.pop("recent_memories", None); st.pop("recent_events", None); st.pop("top_entities", None)
    print(json.dumps(st, ensure_ascii=False, indent=1, default=str))
    await cm.close()
    return 0


async def cmd_forget(args: argparse.Namespace) -> int:
    cm = _build(args)
    ok = await cm.retract(args.memory_id, args.reason)
    print("retracted" if ok else "not found")
    await cm.close()
    return 0 if ok else 1


async def cmd_remember(args: argparse.Namespace) -> int:
    cm = _build(args)
    m = await cm.remember(args.text, subject=args.subject, kind=args.kind, when=args.when)
    print(f"{m.memory_id}: {m.text}")
    await cm.close()
    return 0


async def cmd_maintain(args: argparse.Namespace) -> int:
    cm = _build(args)
    print(json.dumps(await cm.maintain()))
    await cm.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m NeuralGraph.chat_memory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run worker + dashboard + API + MCP (HTTP)"); _add_common(s)
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--mcp-token", default=None, help="bearer token required on /mcp (env NEURALGRAPH_MCP_TOKEN)")
    s.add_argument("--api-token", default=None, help="bearer token required on /api/* (env NEURALGRAPH_API_TOKEN)")
    s.add_argument("--allowed-origin", action="append", help="restrict browser Origin on /mcp (repeatable)")
    s.add_argument("--cors-origin", action="append", help="browser origins allowed to call /api/* cross-site (repeatable; '*' for any). Off by default.")
    s.add_argument("--allowed-host", action="append", help="accepted Host headers (repeatable). Default: loopback names when bound to 127.0.0.1")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("mcp", help="run worker + MCP server over stdio"); _add_common(s); s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("ingest", help="import a JSONL chat log or a LoCoMo file"); _add_common(s)
    s.add_argument("file"); s.add_argument("--chat-id", default=None, help="chat id for records without one")
    s.add_argument("--locomo", action="store_true", help="file is LoCoMo JSON (evaluation/locomo/locomo10.json)")
    s.add_argument("--only", default=None, help="LoCoMo conversation indices, e.g. 0,1")
    s.add_argument("--process", action="store_true", help="drain the queue after ingesting")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("process", help="drain the queue in the foreground"); _add_common(s)
    s.add_argument("--max-batches", type=int, default=None); s.set_defaults(fn=cmd_process)

    s = sub.add_parser("search", help="search memories"); _add_common(s)
    s.add_argument("query"); s.add_argument("-k", type=int, default=10); s.add_argument("--subject", default=None)
    s.add_argument("--history", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_search)

    s = sub.add_parser("context", help="prompt-ready context block"); _add_common(s)
    s.add_argument("query"); s.add_argument("-k", type=int, default=10); s.add_argument("--max-chars", type=int, default=2400)
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("profile", help="what is known about a subject"); _add_common(s)
    s.add_argument("subject"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("stats", help="counts, queue, tokens saved, grade"); _add_common(s); s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("forget", help="retract a memory"); _add_common(s)
    s.add_argument("memory_id"); s.add_argument("--reason", default="user request"); s.set_defaults(fn=cmd_forget)

    s = sub.add_parser("remember", help="store an explicit memory now"); _add_common(s)
    s.add_argument("text"); s.add_argument("--subject", default="user"); s.add_argument("--kind", default="fact")
    s.add_argument("--when", default=None); s.set_defaults(fn=cmd_remember)

    s = sub.add_parser("maintain", help="run one maintenance pass"); _add_common(s); s.set_defaults(fn=cmd_maintain)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr if args.cmd == "mcp" else sys.stdout)
    try:
        return asyncio.run(args.fn(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
