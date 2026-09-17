"""``ChatMemory``: the one object applications use.

    cm = ChatMemory("~/.neuralgraph/chat_memory.db")      # real Qwen/LM Studio via llm_backend env vars
    await cm.start()                                        # background worker starts (non-blocking)
    await cm.add_message("chat-42", "user", "I moved to Berlin last month")   # returns immediately
    ...
    block = await cm.context_for("where does the user live?")   # prepend to the next prompt
    hits  = await cm.search("Berlin")
    await cm.stop()

The chat path (``add_message``) only writes the raw message and a queue entry; every LLM call happens in
the worker. ``ChatMemory`` also keeps the token ledger behind the dashboard's "tokens saved" figure and
computes the five-axis grade.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .extraction import ExtractionConfig, MemoryExtractor
from .llm import BackendLLMClient, LLMClient
from .models import ChatMessage, Memory, RetrievedMemory, parse_iso, utcnow
from .retrieval import MemoryRetriever, RetrievalConfig
from .store import ChatMemoryStore
from .worker import MemoryWorker, WorkerConfig

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Cheap tokenizer-free estimate (~4 chars per token for English chat text)."""
    if not text:
        return 0
    return max(1, int(round(len(text) / 4.0)))


@dataclass
class ChatMemoryConfig:
    debounce_seconds: float = 1.0          # let a user turn and its reply land in the same batch
    max_attempts: int = 5
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    llm_max_parallel: int = 4
    llm_model: str | None = None
    llm_base_url: str | None = None
    embed_model: str | None = None


class ChatMemory:
    def __init__(self, db_path: str | Path = "~/.neuralgraph/chat_memory.db", *, llm: LLMClient | None = None,
                 config: ChatMemoryConfig | None = None) -> None:
        self.config = config or ChatMemoryConfig()
        self.store = ChatMemoryStore(db_path)
        self.llm: LLMClient = llm or BackendLLMClient(
            base_url=self.config.llm_base_url, model=self.config.llm_model, embed_model=self.config.embed_model,
            max_parallel=self.config.llm_max_parallel,
        )
        self.extractor = MemoryExtractor(self.store, self.llm, self.config.extraction)
        self.worker = MemoryWorker(self.store, self.llm, self.extractor, self.config.worker)
        self.retriever = MemoryRetriever(self.store, self.llm, self.config.retrieval)
        self._context_ledger = {"queries": 0, "context_tokens": 0, "raw_tokens_avoided": 0}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.worker.start()

    async def stop(self, timeout: float = 30.0) -> None:
        await self.worker.stop(timeout=timeout)

    async def close(self) -> None:
        await self.stop()
        await self.llm.close()
        await self.store.close()

    async def __aenter__(self) -> "ChatMemory":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ ingestion (never blocks on the LLM)
    async def add_message(self, chat_id: str, speaker: str, text: str, *, role: str = "", sent_at: str | None = None,
                          message_id: str | None = None, metadata: dict[str, Any] | None = None) -> ChatMessage:
        if not chat_id or not isinstance(chat_id, str):
            raise ValueError("chat_id must be a non-empty string")
        if not role:
            role = "assistant" if speaker.lower() == "assistant" else ("user" if speaker.lower() == "user" else "")
        msg, created = await self.store.add_message(
            chat_id, speaker, text, role=role, sent_at=sent_at, message_id=message_id, metadata=metadata,
            debounce_seconds=self.config.debounce_seconds, max_attempts=self.config.max_attempts,
        )
        if created:
            self.worker.wake()
        return msg

    async def add_messages(self, chat_id: str, messages: Iterable[dict[str, Any]]) -> list[ChatMessage]:
        """Bulk import: each item is {"speaker", "text", "role"?, "sent_at"?, "message_id"?, "metadata"?}."""
        out = []
        for m in messages:
            out.append(await self.add_message(chat_id, m.get("speaker") or m.get("role") or "user", m.get("text", ""),
                                              role=m.get("role", ""), sent_at=m.get("sent_at"), message_id=m.get("message_id"),
                                              metadata=m.get("metadata")))
        return out

    async def wait_until_idle(self, timeout: float | None = None) -> bool:
        return await self.worker.wait_idle(timeout)

    async def process_pending(self, max_batches: int | None = None) -> int:
        """Synchronously process queued work in the current task (for scripts and tests)."""
        return await self.worker.drain(max_batches)

    # ------------------------------------------------------------------ retrieval
    async def search(self, query: str, **kwargs: Any) -> list[RetrievedMemory]:
        return await self.retriever.search(query, **kwargs)

    async def context_for(self, query: str, **kwargs: Any) -> str:
        block, results = await self.retriever.context_with_results(query, **kwargs)
        if block:
            # ledger: what would it have cost to re-read the source chats instead?
            chats = {r.memory.chat_id for r in results}
            raw = max(1, int(round((await self.store.chat_text_chars(chats)) / 4.0))) if chats else 0
            self._context_ledger["queries"] += 1
            self._context_ledger["context_tokens"] += estimate_tokens(block)
            self._context_ledger["raw_tokens_avoided"] += raw
        return block

    async def profile(self, subject: str, **kwargs: Any) -> dict[str, Any]:
        return await self.retriever.profile(subject, **kwargs)

    async def related(self, memory_id: str) -> list[dict[str, Any]]:
        return await self.retriever.related(memory_id)

    async def timeline(self, **kwargs: Any) -> list[Memory]:
        return await self.retriever.timeline(**kwargs)

    async def memories(self, **kwargs: Any) -> list[Memory]:
        return await self.store.list_memories(**kwargs)

    async def remember(self, text: str, *, subject: str = "user", kind: str = "fact", importance: float = 0.8,
                       confidence: float = 1.0, chat_id: str = "manual", when: str | None = None,
                       speaker: str | None = None) -> Memory:
        """Store an explicit memory immediately (no LLM extraction), e.g. "remember that ...".

        Still deduplicated against existing memories of the subject (exact text, or cosine >= dedupe threshold).
        """
        from .extraction import parse_when
        from .models import new_id, now_iso
        from .textutil import content_hash, norm_entity, normalize_ws

        text = normalize_ws(text)
        if len(text) < 3:
            raise ValueError("memory text too short")
        subject_name = self.config.extraction.speaker_names.get(subject, subject)
        sid = norm_entity(subject_name) or "user"
        thash = content_hash(text.lower())
        existing = await self.store.find_active_by_text_hash(thash)
        if existing is not None:
            return existing
        try:
            emb = await self.llm.embed(text)
        except Exception:
            emb = None
        if emb:
            from .extraction import _cos
            for m in await self.store.list_memories(subject=sid, limit=2000):
                if m.embedding and _cos(emb, m.embedding) >= self.config.extraction.dedupe_cosine:
                    await self.store.add_memory_sources(m.memory_id, [])
                    return m
        now = now_iso()
        event_time, precision = parse_when(when, now)
        mem = Memory(memory_id=new_id("mem"), text=text, kind=kind if kind in __import__("NeuralGraph.chat_memory.models", fromlist=["MEMORY_KINDS"]).MEMORY_KINDS else "fact",
                     subject=sid, subject_name=subject_name, speaker=speaker or subject_name, chat_id=chat_id,
                     importance=max(0.0, min(1.0, float(importance))), confidence=max(0.0, min(1.0, float(confidence))),
                     event_time=event_time, event_time_precision=precision, observed_at=now, created_at=now, updated_at=now,
                     text_hash=thash, embedding=emb, metadata={"decision": "MANUAL", "source_kind": "explicit"})
        await self.store.upsert_entity(sid, subject_name, "person", seen_at=now)
        await self.store.insert_memory(mem, entity_ids=[sid])
        await self.store.log_event("remember", mem.memory_id, {"text": text[:120], "chat_id": chat_id})
        self.retriever.invalidate()
        return mem

    async def retract(self, memory_id: str, reason: str = "user request") -> bool:
        ok = await self.store.retract(memory_id, reason)
        self.retriever.invalidate()
        return ok

    async def maintain(self) -> dict[str, Any]:
        return await self.worker.maintain()

    # ------------------------------------------------------------------ accounting & grading
    async def token_ledger(self) -> dict[str, Any]:
        """Tokens of raw chat text the memory layer has digested vs. tokens of the memories that replace it."""
        c = self.store._conn
        raw_processed = int(c.execute("SELECT COALESCE(SUM(length(text)), 0) AS n FROM messages WHERE status IN ('processed','skipped')").fetchone()["n"])
        raw_all = int(c.execute("SELECT COALESCE(SUM(length(text)), 0) AS n FROM messages").fetchone()["n"])
        mem_active = int(c.execute("SELECT COALESCE(SUM(length(text)), 0) AS n FROM memories WHERE status='active'").fetchone()["n"])
        chars_to_tokens = lambda n: max(1, int(round(n / 4.0))) if n else 0
        raw_tokens = chars_to_tokens(raw_processed)
        mem_tokens = chars_to_tokens(mem_active)
        saved = max(0, raw_tokens - mem_tokens)
        return {
            "raw_tokens_total": chars_to_tokens(raw_all),
            "raw_tokens_digested": raw_tokens,
            "memory_tokens": mem_tokens,
            "tokens_saved": saved,
            "compression_ratio": round(raw_tokens / mem_tokens, 2) if mem_tokens else None,
            "context_queries": self._context_ledger["queries"],
            "context_tokens_served": self._context_ledger["context_tokens"],
            "context_raw_tokens_avoided": self._context_ledger["raw_tokens_avoided"],
        }

    async def grade(self) -> dict[str, Any]:
        """Five-axis grade (0-100 each) + letter. Axes: coverage, compression, connectivity, freshness, reliability."""
        st = await self.store.stats()
        ledger = await self.token_ledger()
        msgs = st["messages"]
        total = sum(msgs.values()) or 0
        done = msgs.get("processed", 0) + msgs.get("skipped", 0)
        coverage = 100.0 * done / total if total else 100.0
        raw, mem = ledger["raw_tokens_digested"], ledger["memory_tokens"]
        # log scale: 1x -> 0, 2x -> 50, 4x or better -> 100 (nothing digested yet -> 100, nothing kept -> 100)
        if not raw:
            compression = 100.0
        elif not mem:
            compression = 100.0
        else:
            compression = 100.0 * max(0.0, min(1.0, math.log2(max(1e-9, raw / mem)) / 2.0))
        active = st["memories"]["by_status"].get("active", 0)
        rel = st["relations"].get("active", 0)
        links = st["links"]
        connectivity = 100.0 * min(1.0, (rel + links) / (1.5 * active)) if active else 0.0
        # freshness: how far behind the newest ingested message the worker is
        c = self.store._conn
        newest = c.execute("SELECT MAX(ingested_at) AS t FROM messages").fetchone()["t"]
        oldest_pending = c.execute("SELECT MIN(ingested_at) AS t FROM messages WHERE status IN ('pending','processing')").fetchone()["t"]
        if not newest or not oldest_pending:
            freshness = 100.0
        else:
            lag = (utcnow() - (parse_iso(oldest_pending) or utcnow())).total_seconds()
            freshness = 100.0 * math.exp(-lag / 600.0)   # 10-minute e-folding
        attempts = self.worker.metrics.batches + self.worker.metrics.failures
        fail_rate = (self.worker.metrics.failures / attempts) if attempts else 0.0
        dead = int(c.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='dead' AND kind='extract'").fetchone()["n"])
        reliability = 100.0 * max(0.0, 1.0 - fail_rate - 0.05 * dead)
        axes = {
            "coverage": round(coverage, 1), "compression": round(compression, 1), "connectivity": round(connectivity, 1),
            "freshness": round(freshness, 1), "reliability": round(reliability, 1),
        }
        overall = sum(axes.values()) / 5.0
        letter = "S" if overall >= 92 else "A" if overall >= 82 else "B" if overall >= 70 else "C" if overall >= 55 else "D"
        return {"axes": axes, "overall": round(overall, 1), "letter": letter}

    async def status(self) -> dict[str, Any]:
        """Everything the dashboard shows, in one call."""
        st = await self.store.stats()
        st["worker"] = {"running": self.worker.running, "worker_id": self.worker.worker_id,
                        "concurrency": self.config.worker.concurrency, **self.worker.metrics.as_dict()}
        st["llm"] = getattr(self.llm, "stats", None).as_dict() if getattr(self.llm, "stats", None) else {}
        st["llm"].update({"model": getattr(self.llm, "model", "fake"), "embed_model": getattr(self.llm, "embed_model", "fake"),
                          "base_url": getattr(self.llm, "base_url", "")})
        st["tokens"] = await self.token_ledger()
        st["grade"] = await self.grade()
        st["recent_memories"] = [m.to_dict() for m in await self.store.list_memories(limit=12, order="created_at DESC")]
        st["top_entities"] = [e.to_dict() for e in await self.store.top_entities(15)]
        st["recent_events"] = await self.store.recent_events(15)
        return st
