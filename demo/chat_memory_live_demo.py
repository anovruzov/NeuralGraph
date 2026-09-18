"""Self-running, narrated demo of cross-chat memory: watch the worker learn while chats stream in.

What it does
------------
1. Starts the memory system: background worker + dashboard + REST + MCP (Streamable HTTP) on one port.
2. Replays five sample chats (`evaluation/chats/sample_chats.jsonl`) with human pacing, so the dashboard
   animates: the avatar "learns", memories appear in the stream, entities and relations grow, the pentagon
   grade moves.
3. Before each new chat it does what Claude would do through MCP: calls ``memory_context`` over the HTTP
   endpoint and prints what the assistant would know from the earlier chats. After the last chat it asks a
   few cross-chat questions through ``memory_search``.
4. Optionally saves dashboard screenshots along the way (``--screenshots``, needs a Chromium binary).
5. Keeps serving at the end so you can click around and point Claude Code / Claude Desktop at it.

Run it
------
    # no model server needed (deterministic fake model)
    .venv/bin/python demo/chat_memory_live_demo.py --fake-llm

    # your local Qwen (Ollama) or LM Studio, see NeuralGraph/llm_backend.py for the env vars
    LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text \\
        .venv/bin/python demo/chat_memory_live_demo.py --user-name "Ali Novruzov"

    # faster / slower replay, extra volume from LoCoMo, exit when done
    .venv/bin/python demo/chat_memory_live_demo.py --fake-llm --pace 0.3 --locomo 0 --no-serve --screenshots

Then open http://127.0.0.1:8765/ (the script prints the URL). MCP for Claude: http://127.0.0.1:8765/mcp
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from NeuralGraph.chat_memory import ChatMemory, ChatMemoryConfig  # noqa: E402
from NeuralGraph.chat_memory.ui.server import run_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "evaluation" / "chats" / "sample_chats.jsonl"
LOCOMO = ROOT / "evaluation" / "locomo" / "locomo10.json"
OUT_DIR = ROOT / "demo" / "results" / "chat_memory_demo"

QUESTIONS = [
    "Where does Ali live now?",
    "What can't Ali eat?",
    "Who is Jonas?",
    "When is Ali's mother's birthday?",
    "What is Ali planning for the autumn?",
    "Tell me about Luna",
]

C = {"h": "\033[1;36m", "b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "x": "\033[0m"}


def say(kind: str, text: str) -> None:
    prefix = {"chat": f"{C['d']}   chat{C['x']}", "mem": f"{C['g']}   memory{C['x']}", "claude": f"{C['h']}   claude{C['x']}",
              "head": f"\n{C['b']}==={C['x']}", "note": f"{C['y']}   note{C['x']}"}[kind]
    print(f"{prefix} {text}", flush=True)


def load_sample() -> dict[str, list[dict]]:
    chats: dict[str, list[dict]] = {}
    for line in SAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            chats.setdefault(rec["chat_id"], []).append(rec)
    return chats


def load_locomo(index: int) -> list[dict]:
    from NeuralGraph.temporal_utils import parse_datetime_flexible

    data = json.loads(LOCOMO.read_text(encoding="utf-8"))
    c = data[index].get("conversation", data[index])
    out, i = [], 1
    while f"session_{i}" in c:
        dt = parse_datetime_flexible(c.get(f"session_{i}_date_time", ""))
        for m in c[f"session_{i}"]:
            out.append({"chat_id": f"locomo-{index}", "speaker": m.get("speaker", "Unknown"), "text": m.get("text", ""),
                        "sent_at": dt.isoformat() if dt else None})
        i += 1
    return out


class MCPClient:
    """Minimal MCP Streamable HTTP client (what Claude does when it calls our tools)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.session = aiohttp.ClientSession()
        self.sid: str | None = None
        self.n = 0

    async def __aenter__(self) -> "MCPClient":
        r = await self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo", "version": "1"}})
        await self.session.post(self.url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self._headers())
        self.server = r["serverInfo"]["name"]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.session.close()

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    async def _rpc(self, method: str, params: dict) -> dict:
        self.n += 1
        async with self.session.post(self.url, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params},
                                     headers=self._headers()) as resp:
            if resp.headers.get("Mcp-Session-Id"):
                self.sid = resp.headers["Mcp-Session-Id"]
            body = await resp.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    async def call(self, name: str, **args) -> dict:
        res = await self._rpc("tools/call", {"name": name, "arguments": args})
        return res.get("structuredContent") or {"text": res["content"][0]["text"]}


def find_chromium() -> str | None:
    for cand in (os.environ.get("CHROMIUM"), shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"),
                 "/opt/pw-browsers/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


async def screenshot(chromium: str | None, url: str, path: Path, height: int = 1500) -> bool:
    """Headless Chromium screenshot, run as a subprocess without blocking the event loop (the server that
    renders the page lives in this same process)."""
    if not chromium:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            chromium, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
            f"--window-size=1280,{height}", "--timeout=8000", f"--screenshot={path}", url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=60)
        return path.exists() and path.stat().st_size > 20_000
    except Exception:
        return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="SQLite path (default: a fresh temp file)")
    ap.add_argument("--fake-llm", action="store_true", help="deterministic fake model, no server needed")
    ap.add_argument("--user-name", default="Ali Novruzov")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--pace", type=float, default=1.2, help="seconds between replayed messages")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--locomo", type=int, default=None, help="also stream this LoCoMo conversation (0-9) for volume")
    ap.add_argument("--screenshots", action="store_true", help=f"save dashboard screenshots to {OUT_DIR}")
    ap.add_argument("--no-serve", action="store_true", help="exit when the story ends instead of keeping the server up")
    args = ap.parse_args()

    cfg = ChatMemoryConfig()
    cfg.extraction.speaker_names["user"] = args.user_name
    cfg.debounce_seconds = 0.8
    cfg.llm_max_parallel = args.parallel
    cfg.worker.concurrency = min(args.parallel, 4)
    llm = None
    if args.fake_llm:
        from NeuralGraph.chat_memory.testing import scripted_fake_llm

        llm = scripted_fake_llm(latency=0.6)   # a little latency so the avatar visibly "learns"
    db = args.db or str(Path(tempfile.mkdtemp()) / "chat_memory_live_demo.db")
    cm = ChatMemory(db, llm=llm, config=cfg)
    await cm.start()
    runner = await run_server(cm, host=args.host, port=args.port)
    base = f"http://{args.host}:{args.port}"
    chromium = find_chromium() if args.screenshots else None
    shots = 0

    async def shot(name: str) -> None:
        nonlocal shots
        if args.screenshots:
            await asyncio.sleep(2.5)   # let the dashboard poll once more so the picture shows the latest state
            if await screenshot(chromium, base + "/", OUT_DIR / f"{shots:02d}_{name}.png"):
                shots += 1
                say("note", f"screenshot saved: {OUT_DIR / f'{shots - 1:02d}_{name}.png'}")
            elif chromium is None:
                say("note", "no Chromium binary found; set CHROMIUM=/path/to/chrome to enable screenshots")

    say("head", f"NeuralGraph cross-chat memory — live demo ({'fake model' if args.fake_llm else 'real model via llm_backend'})")
    print(f"   dashboard  {C['b']}{base}/{C['x']}\n   MCP (HTTP) {base}/mcp\n   db         {db}\n", flush=True)
    say("note", "open the dashboard now and keep it visible; the story starts in 3 s")
    await asyncio.sleep(3)
    await shot("empty")

    chats = load_sample()
    async with MCPClient(base + "/mcp") as mcp:
        say("note", f"connected to MCP server '{mcp.server}' the way Claude would")
        for n, (chat_id, msgs) in enumerate(chats.items(), 1):
            say("head", f"chat {n}/{len(chats)}: {chat_id}")
            if n > 1:
                first_user = next((m["text"] for m in msgs if m.get("role") == "user"), msgs[0]["text"])
                ctx = await mcp.call("memory_context", query=first_user, k=6, max_chars=900)
                if ctx.get("context"):
                    say("claude", "memory_context(...) before replying — what earlier chats taught me:")
                    for line in ctx["context"].splitlines()[1:]:
                        print(f"            {line}", flush=True)
                else:
                    say("claude", "memory_context(...) → nothing relevant yet")
            for m in msgs:
                who = args.user_name if m["speaker"] == "user" else m["speaker"]
                say("chat", f"{who}: {m['text'][:110]}{'…' if len(m['text']) > 110 else ''}")
                await mcp.call("memory_add_message", chat_id=chat_id, speaker=m["speaker"], text=m["text"],
                               role=m.get("role", ""), sent_at=m.get("sent_at"))
                await asyncio.sleep(args.pace)
            # let the worker digest this chat and show what it learned
            before = {x.memory_id for x in await cm.memories(limit=1000)}
            await cm.wait_until_idle(timeout=120)
            for x in await cm.memories(limit=1000, order="created_at ASC"):
                if x.memory_id not in before:
                    tag = "" if x.metadata.get("decision", "ADD") == "ADD" else f" ({x.metadata['decision'].lower()})"
                    say("mem", f"[{x.kind}] {x.text}{tag}")
            st = await mcp.call("memory_status")
            say("note", f"{st['memories']['by_status'].get('active', 0)} active memories, {st['entities']} entities, "
                        f"{st['relations'].get('active', 0)} relations · tokens saved {st['tokens']['tokens_saved']} · grade {st['grade']['letter']}")
            await shot(f"after_chat_{n}")

        if args.locomo is not None:
            say("head", f"volume: streaming LoCoMo conversation {args.locomo} as one more chat")
            for m in load_locomo(args.locomo):
                await mcp.call("memory_add_message", chat_id=m["chat_id"], speaker=m["speaker"], text=m["text"], sent_at=m.get("sent_at"))
                await asyncio.sleep(min(args.pace, 0.15))
            await cm.wait_until_idle(timeout=600)
            await shot("after_locomo")

        say("head", "questions that need more than one chat to answer")
        for q in QUESTIONS:
            res = await mcp.call("memory_search", query=q, k=3)
            say("claude", f"memory_search({q!r})")
            for r in res["results"]:
                print(f"            {r['score']:.3f}  {r['text']}   ← {r['chat_id']} ({r['why']})", flush=True)
        prof = await mcp.call("memory_profile", subject=args.user_name)
        say("claude", f"memory_profile({args.user_name!r}) → {prof['memory_count']} memories, {len(prof['relations'])} relations")
        for r in prof["relations"][:8]:
            print(f"            {r['subject_id']} —{r['predicate']}→ {r['object_id']}", flush=True)
        say("claude", 'memory_remember("Ali prefers dark roast coffee.")')
        await mcp.call("memory_remember", text="Ali prefers dark roast coffee.", subject=args.user_name, kind="preference")
        res = await mcp.call("memory_search", query="coffee", k=1)
        say("mem", f"recalled: {res['results'][0]['text'] if res['results'] else '(nothing)'}")
        await shot("final")

    st = await cm.status()
    say("head", "done")
    print(f"   grade {st['grade']['letter']} ({st['grade']['overall']}) · axes {st['grade']['axes']}\n"
          f"   tokens: {st['tokens']['raw_tokens_digested']} raw → {st['tokens']['memory_tokens']} memory, {st['tokens']['tokens_saved']} saved\n"
          f"   worker: {st['worker']['batches']} batches, {st['llm'].get('generate_calls', 0)} model calls, {st['worker']['failures']} failures", flush=True)
    if args.no_serve:
        await runner.cleanup()
        await cm.close()
        return 0
    print(f"\n   still serving. Dashboard: {base}/   MCP: {base}/mcp\n"
          f"   Claude Code:  claude mcp add --transport http neuralgraph-memory {base}/mcp\n"
          f"   Ctrl-C to stop.", flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    await runner.cleanup()
    await cm.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
