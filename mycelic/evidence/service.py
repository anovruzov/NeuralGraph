"""``EvidenceStore``: one holder's evidence on a NeuralGraph store (docs/mycelic/DECISIONS.md D2, D3, D11).

What lives here and why:

* Documents are chunked into ~600-character paragraphs. Each chunk is recorded as a raw message (provenance) and
  indexed as a memory through the store's own insert path, so the hybrid retrieval (vector + FTS5 + entity graph,
  RRF) measured in the research track is the one that answers questions. Nothing is forked from NeuralGraph.
* ``source_root_id`` is the content fingerprint of the document (or of ``origin_id`` when the upload names its
  origin), so two holders that upload the same text share a root and count once for independent support.
* :meth:`answer_question` is the *only* path by which evidence leaves the holder. It applies the export policy
  (``answer_scopes``, ``disclosure``, ``max_excerpt_chars``, ``deny_patterns``), answers strictly from retrieved
  evidence (a model task, or the same rule the deterministic fake implements when no router is configured) and
  returns a ``ResponseArtifact`` whose references are opaque ``ev_`` ids. Memory ids, document ids and full texts
  never appear in it; :meth:`raw_for_ref` resolves a reference back to the document only for the coordinator's
  raw-grant path.
* Mutations accept an ``idempotency_key`` (the transport ``msg_id``) so the holder service can commit the effect and
  the processed-message marker in one transaction.
* Records that arrive through connectors (docs/mycelic/INGESTION.md) are written through the same primitives with a few
  keyword-only parameters (explicit root, conversation chat, speaker, metadata, and ``extra_sync``, which runs inside the
  same transaction so the ingestion catalog commits atomically with the content). Each carries its source ACL; the
  answer, raw and manual-response paths disclose a member-restricted or private record only to an audience entirely
  inside the source's members, or to the holder owner (:mod:`mycelic.ingest.acl`).
* Retraction and deletion purge content (text, chunks, FTS rows, embeddings, versions, excerpts) and keep a
  content-free tombstone; :meth:`raw_for_ref` never returns text of a withdrawn document.
"""
from __future__ import annotations

import contextlib
import inspect
import logging
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.chat_memory.models import Memory, RetrievedMemory, iso, new_id, now_iso, parse_iso
from NeuralGraph.chat_memory.retrieval import MemoryRetriever, RetrievalConfig
from NeuralGraph.chat_memory.textutil import clip, content_hash, fold, norm_entity, normalize_ws, stem, tokenize

from .. import __version__ as HOLDER_VERSION
from ..ingest.acl import Audience, decide, narrow
from ..ingest.domains import Taxonomy, default_taxonomy, domains_overlap, is_personal, load_taxonomy_sync
from ..ingest.events import Permissions
from ..ingest.normalize import mask_secrets
from ..util import fingerprint, j, jl
from .store import LIVE_DOCUMENT_STATUSES, AlreadyProcessed, MycelicMemoryStore

ExtraSync = Callable[[sqlite3.Connection, dict[str, Any]], Any]
REF_KIND = {"message": "message", "document": "document", "event": "record", "conversation": "conversation"}

logger = logging.getLogger(__name__)

RETRIEVAL_OPERATOR = "neuralgraph.hybrid"
DISCLOSURE_LEVELS = ("none", "summary", "excerpt")          # least to most disclosing
DEFAULT_EXPORT_POLICY: dict[str, Any] = {
    "disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"], "deny_patterns": [],
    "max_answer_chars": 1200, "disclose_source_app": True,
}
DEFAULT_CHUNK_CHARS = 600
RETRIEVAL_K = 8

_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")
# a chat turn starts with a short capitalised name ("Ana:", "Ben Lee:"), never with digits ("Incident 2026-03-04:" is a heading)
_SPEAKER_LINE_RE = re.compile(r"^[A-Z][A-Za-z.'\-]{0,24}(?: [A-Z][A-Za-z.'\-]{0,24}){0,2}:\s")
_WORD_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------------------------
# Pure helpers (also used by the tests and the standalone process)
# ---------------------------------------------------------------------------------------------


def chunk_text(text: str, *, target: int = DEFAULT_CHUNK_CHARS, hard_max: int | None = None) -> list[str]:
    """Split a document into retrieval units of about ``target`` characters.

    Paragraphs (blank-line separated) are the natural unit; small ones are merged until the next would push the
    chunk past ``target``, and paragraphs longer than ``hard_max`` (default ``2 * target``) are split at sentence
    boundaries so an excerpt never starts mid-sentence. A single sentence longer than ``hard_max`` is cut at
    whitespace, which is the only case where a chunk can start mid-sentence.
    """
    hard_max = hard_max or target * 2
    paragraphs = [normalize_ws(p) for p in _PARA_RE.split((text or "").replace("\r\n", "\n"))]
    pieces: list[str] = []
    for p in paragraphs:
        if not p:
            continue
        if len(p) <= hard_max:
            pieces.append(p)
        else:
            pieces.extend(_split_long_paragraph(p, target, hard_max))
    chunks: list[str] = []
    current = ""
    for p in pieces:
        if current and len(current) + 1 + len(p) > target:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def _split_long_paragraph(paragraph: str, target: int, hard_max: int) -> list[str]:
    out: list[str] = []
    current = ""
    for sentence in (s for s in _SENT_RE.split(paragraph) if s):
        while len(sentence) > hard_max:
            cut = sentence.rfind(" ", 0, target)
            cut = cut if cut > target // 2 else target
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > target:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        out.append(current)
    return out


def _content_words(text: str) -> set[str]:
    """Raw and stemmed lower-case words, so short domain names ('hr') match as well as inflected ones."""
    words = _WORD_RE.findall(fold(text))
    return set(words) | {stem(w) for w in words}


def rule_classify_document(title: str, text: str, known_domains: Iterable[str]) -> dict[str, Any]:
    """The deterministic ``classify_document`` rule (mycelic.models.tasks): known domains whose name appears in the
    title or text, the first sentence as summary, ``conversation`` when lines look like ``Name: ...`` turns."""
    words = _content_words(f"{title}\n{text}")
    domains: list[str] = []
    for d in known_domains or ():
        parts = _WORD_RE.findall(fold(str(d)))
        if parts and all(p in words or stem(p) in words for p in parts) and d not in domains:
            domains.append(str(d))
    if not domains:
        domains = ["general"]
    first = next((s for s in _SENT_RE.split(normalize_ws(text)) if s.strip()), "")
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    speaker_lines = sum(1 for ln in lines if _SPEAKER_LINE_RE.match(ln.strip()))
    kind = "conversation" if lines and speaker_lines >= 2 and speaker_lines * 2 >= len(lines) else "note"
    return {"domains": domains, "summary": clip(first, 140), "kind": kind}


def rule_answer_from_evidence(question: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """The deterministic ``answer_from_evidence`` rule: keep items sharing >= 2 content tokens with the question
    (at most 3), join their excerpts, confidence ``min(0.9, 0.5 + 0.15 * kept)``."""
    q_tokens = set(tokenize(question))
    kept: list[dict[str, Any]] = []
    for item in evidence:
        if len(q_tokens & set(tokenize(item.get("excerpt") or ""))) >= 2:
            kept.append(item)
            if len(kept) == 3:
                break
    if not kept:
        return {"answer": "", "confidence": 0.0, "used_ref_ids": [], "no_evidence": True}
    return {
        "answer": " ".join(i.get("excerpt") or "" for i in kept),
        "confidence": min(0.9, 0.5 + 0.15 * len(kept)),
        "used_ref_ids": [i["ref_id"] for i in kept],
        "no_evidence": False,
    }


def compile_deny_patterns(patterns: Iterable[str] | None) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for p in patterns or ():
        try:
            out.append(re.compile(str(p), re.IGNORECASE))
        except re.error as exc:
            logger.warning("ignoring invalid deny pattern %r: %s", p, exc)
    return out


def redact(text: str, patterns: list[re.Pattern[str]]) -> str:
    for p in patterns:
        text = p.sub("[redacted]", text)
    return text


def effective_disclosure(holder_level: str | None, requested: str | None) -> str:
    """The lower of what the holder allows and what the question asks for (a question can never widen disclosure)."""
    h = holder_level if holder_level in DISCLOSURE_LEVELS else "excerpt"
    if requested not in DISCLOSURE_LEVELS:
        return h
    return DISCLOSURE_LEVELS[min(DISCLOSURE_LEVELS.index(h), DISCLOSURE_LEVELS.index(requested))]


def new_ref_id() -> str:
    return "ev_" + secrets.token_hex(10)


ANSWER_SCOPES = ("private", "unit", "org")


def policy_problems(export_policy: dict[str, Any] | None) -> list[str]:
    """Everything wrong with an export policy as written. The API refuses such a policy; a holder that somehow has one
    declines to answer rather than falling back to a more permissive reading."""
    p = export_policy or {}
    problems: list[str] = []
    if p.get("disclosure") is not None and p["disclosure"] not in DISCLOSURE_LEVELS:
        problems.append(f"disclosure must be one of {', '.join(DISCLOSURE_LEVELS)}")
    scopes = p.get("answer_scopes")
    if scopes is not None and (not isinstance(scopes, list) or any(s not in ANSWER_SCOPES for s in scopes)):
        problems.append(f"answer_scopes must be a list drawn from {', '.join(ANSWER_SCOPES)}")
    pats = p.get("deny_patterns")
    if pats is not None:
        if not isinstance(pats, list) or any(not isinstance(x, str) for x in pats):
            problems.append("deny_patterns must be a list of regular expressions")
        else:
            for x in pats:
                try:
                    re.compile(x)
                except re.error as exc:
                    problems.append(f"deny pattern {x!r} does not compile: {exc}")
    for k in ("max_excerpt_chars", "max_answer_chars"):
        v = p.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            problems.append(f"{k} must be a non-negative integer")
    if p.get("auto_domains") is not None and not isinstance(p["auto_domains"], bool):
        problems.append("auto_domains must be true or false")
    return problems


class EvidenceChanged(RuntimeError):
    """A document was revised or retracted while an answer citing it was being written; the answer is retried."""


def normalize_policy(export_policy: dict[str, Any] | None) -> dict[str, Any]:
    policy = dict(DEFAULT_EXPORT_POLICY)
    for k, v in (export_policy or {}).items():
        if v is not None:
            policy[k] = v
    policy["answer_scopes"] = [str(s) for s in (policy.get("answer_scopes") or [])]
    policy["deny_patterns"] = [str(p) for p in (policy.get("deny_patterns") or [])]
    policy["max_excerpt_chars"] = max(0, int(policy.get("max_excerpt_chars") or 0))
    policy["max_answer_chars"] = max(0, int(policy.get("max_answer_chars") or 0))
    if policy.get("disclosure") not in DISCLOSURE_LEVELS:
        policy["disclosure"] = "none"          # an unreadable setting discloses nothing, never more
    return policy


def _norm_time(value: Any) -> str | None:
    """Accept an ISO timestamp or a date and return a canonical ISO UTC string (None when absent or unparsable)."""
    if not value:
        return None
    s = str(value).strip()
    dt = parse_iso(s if len(s) > 10 else f"{s}T00:00:00+00:00")
    return iso(dt) if dt else None


class HashEmbedder:
    """Deterministic bag-of-words embeddings (the ``hash`` provider of D7) so a holder works with no model configured.
    Presents NeuralGraph's ``LLMClient`` embedding shape."""

    name = "hash"
    model = "hash-256"
    dim = 256

    async def embed(self, text: str) -> list[float]:
        return fake_embedding(text, self.dim)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [fake_embedding(t, self.dim) for t in texts]

    async def generate(self, prompt: str, **kw: Any) -> str:
        raise NotImplementedError("HashEmbedder has no generation model")

    async def close(self) -> None:
        return None


class EmbeddingAdapter:
    """Wrap a Mycelic ``EmbeddingProvider`` (``embed(list[str]) -> list[list[float]]``) into the NeuralGraph
    ``LLMClient`` embedding shape that ``MemoryRetriever`` expects (``embed(str) -> list[float]``)."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.name = getattr(provider, "name", "embedding")
        self.model = getattr(provider, "model", "")

    async def embed(self, text: str) -> list[float]:
        vecs = await self.provider.embed([text])
        return [float(x) for x in vecs[0]]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in await self.provider.embed(list(texts))]

    async def generate(self, prompt: str, **kw: Any) -> str:
        raise NotImplementedError("embedding providers do not generate text")

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if close is not None:
            r = close()
            if inspect.isawaitable(r):
                await r


# ---------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------


class EvidenceStore:
    """One holder's evidence: documents, their chunk memories and the export ledger, on a NeuralGraph store.

    ``llm`` is anything with ``embed(text)`` / ``embed_many(texts)`` (NeuralGraph ``LLMClient``, :class:`HashEmbedder`,
    :class:`EmbeddingAdapter`); ``router`` is a ``ModelRouter`` (``run_task``) or ``None`` for the rule-based
    fallbacks. ``extract=True`` also queues NeuralGraph extraction jobs for every chunk so a configured
    ``MemoryExtractor`` worker can derive finer memories; the worker itself is run by the caller.
    """

    # how a sharded holder merges its shards' rankings (§7.4): "rrf" (shard-level reciprocal-rank fusion, as designed) or
    # "channel" (rank fusion per retrieval channel across shards; see mycelic.ingest.fanout.channel_rrf_merge)
    fanout_merge = "rrf"
    fanout_fetch_k: int | None = None            # per-shard depth of a fan-out search (default 2 * k, as a single store fetches)

    def __init__(self, path: str | Path, *, holder_id: str, tenant_id: str, llm: Any | None = None, router: Any | None = None,
                 export_policy: dict[str, Any] | None = None, domains: Iterable[str] = (), extract: bool = False,
                 retrieval: RetrievalConfig | None = None, chunk_chars: int = DEFAULT_CHUNK_CHARS, owner_ids: Iterable[str] = ()) -> None:
        self.path = str(path)
        self.holder_id = holder_id
        self.tenant_id = tenant_id
        self.llm = llm if llm is not None else HashEmbedder()
        self.router = router
        self.extract = bool(extract)
        self.chunk_chars = int(chunk_chars)
        self.owner_ids: list[str] = [str(o) for o in owner_ids if o]     # principals that own this holder (ACL: always inside)
        self.store = MycelicMemoryStore(path)
        self.retriever = MemoryRetriever(self.store, self.llm, retrieval)
        self.export_policy: dict[str, Any] = {}
        self.domains: list[str] = []
        self._deny: list[re.Pattern[str]] = []
        self._policy_problems: list[str] = []
        self._taxonomy: Taxonomy | None = None
        self.shard_id = "s0"                 # a data shard's store is opened by the holder's ShardSet, which sets these
        self._shard_set: Any = None
        self.update_policy(export_policy, list(domains))

    # ------------------------------------------------------------------ shards (docs/mycelic/INGESTION.md §7)
    @property
    def shards(self) -> Any:
        """The holder's :class:`~mycelic.ingest.shards.ShardSet` (created on first use; a data shard returns its holder's)."""
        if self._shard_set is None:
            from ..ingest.shards import ShardSet
            self._shard_set = ShardSet(self)
        return self._shard_set

    def _is_control(self) -> bool:
        return self.store.control_store is None

    def _sharded(self) -> bool:
        """The holder has (or is building) a data shard: records may live outside this file."""
        return self._is_control() and self.shards.has_data_shards()

    def _multi(self) -> bool:
        """More than one shard holds live data: reads fan out (with one shard, today's single-store path is taken)."""
        return self._is_control() and self.shards.multi()

    def _doc_store(self, doc_id: str) -> "EvidenceStore":
        """The store whose file holds ``doc_id`` (connector records are routed by ``record_locator``; uploads stay in s0)."""
        if not self._sharded():
            return self
        return self.shards.store_of_record(doc_id)

    @contextlib.asynccontextmanager
    async def _owner_gate(self, doc_id: str) -> Any:
        """The store that holds ``doc_id``, with that shard's write gate held. A split's cutover holds the source's gate while
        it moves records, so the owner is looked up again once the gate is ours (a mutation never lands on a stale copy).
        Re-entrant for the task that already holds the gate (the pipeline). A holder with a single shard takes no gate."""
        if not self._is_control():
            async with self.shards.gate(self.shard_id):
                yield self
            return
        if not self.shards.has_data_shards():
            yield self
            return
        for _attempt in range(8):
            sid = self.shards.shard_of_record(doc_id)
            gate = self.shards.gate(sid)
            await gate.__aenter__()
            if self.shards.shard_of_record(doc_id) == sid:
                break
            await gate.__aexit__(None, None, None)
        else:
            raise RuntimeError(f"the shard of {doc_id} kept changing")
        try:
            yield self if sid == self.shard_id else self.shards.store(sid)
        finally:
            await gate.__aexit__(None, None, None)

    def record_exists(self, doc_id: str) -> bool:
        """Is ``doc_id`` a connector record of this holder (whichever shard holds it)?"""
        return self._doc_store(doc_id).store._conn.execute("SELECT 1 FROM ingest_records WHERE record_id=?", (doc_id,)).fetchone() is not None

    def traverser(self) -> Any:
        """Paths over the holder's typed edges across its shards (§7.5)."""
        from ..ingest.fanout import GraphTraverser
        return GraphTraverser(self.shards)

    # ------------------------------------------------------------------ policy
    def update_policy(self, export_policy: dict[str, Any] | None = None, domains: Iterable[str] | None = None) -> None:
        """Hot-reload the export policy and/or the evidence domains (the org registry is the source of truth)."""
        if export_policy is not None:
            self._policy_problems = policy_problems(export_policy)
            self.export_policy = normalize_policy(export_policy)
            self._deny = compile_deny_patterns(self.export_policy["deny_patterns"])
        elif not self.export_policy:
            self.export_policy = normalize_policy(None)
            self._deny = []
        if domains is not None:
            self.domains = [str(d) for d in domains]
        if self._shard_set is not None and self._is_control():
            for sid, s in list(self._shard_set._stores.items()):
                if s is not self:
                    s.update_policy(export_policy, domains)

    def describe(self) -> dict[str, Any]:
        return {"holder_id": self.holder_id, "tenant_id": self.tenant_id, "path": self.path, "export_policy": dict(self.export_policy),
                "domains": list(self.domains), "extract": self.extract, "holder_version": HOLDER_VERSION}

    async def close(self) -> None:
        if self._shard_set is not None and self._is_control():
            await self._shard_set.close()
        await self.store.close()

    # ------------------------------------------------------------------ ingestion
    async def ingest_document(self, title: str, text: str, *, kind: str = "note", observed_at: str | None = None,
                              domains: Iterable[str] | None = None, origin_id: str | None = None, uploaded_by: str | None = None,
                              doc_id: str | None = None, idempotency_key: str | None = None,
                              source_root_id: str | None = None, root_known: bool = True, conversation_chat_id: str | None = None,
                              speaker: str | None = None, extra_metadata: dict[str, Any] | None = None,
                              entities: Iterable[tuple[str, str, str]] | None = None, audit_detail: str = "full", classify: bool = True,
                              embeddings: list[list[float] | None] | None = None, extra_sync: ExtraSync | None = None) -> dict[str, Any]:
        """Store a document, chunk it, and index every chunk as a memory with provenance. Idempotent on ``doc_id``.

        Embeddings and classification (model calls) happen before the transaction; the transaction then writes the
        document, its version-1 row, the chunk messages, the memories, the entities and the last-ingest marker.

        Keyword-only parameters used by connector ingestion (all optional, defaults keep the upload behaviour):
        ``source_root_id``/``root_known`` (explicit root computed by the connector rules), ``conversation_chat_id`` (chat
        of the provenance message rows; memories keep ``chat_id = doc_id``), ``speaker``, ``extra_metadata`` (merged into
        memory and message metadata), ``entities`` (``(entity_id, name, type)`` linked to every chunk memory),
        ``audit_detail='ids'`` (no title in the audit log), ``classify=False`` (no model call; the rule summary is used),
        ``embeddings`` (precomputed, one per chunk of ``chunk_text(text)``), and ``extra_sync(conn, write)`` which runs in
        the same transaction after the content rows, with ``write = {doc, memory_ids, message_ids, chunks, version}``.
        ``extra_sync`` runs only when this call creates the document.
        """
        title = normalize_ws(title) or "Untitled"
        text = _clean_text(text)
        if not text:
            raise ValueError("document text is empty")
        doc_id = doc_id or new_id("doc")
        existing = await self.store.get_document(doc_id)
        if existing is not None:
            result = await self._public_document(existing)
            if idempotency_key:
                await self.store.mark_processed(idempotency_key, {"op": "ingest", "result": result})
            return result
        observed = _norm_time(observed_at)
        now = now_iso()
        if source_root_id is not None:
            root = source_root_id
        else:
            root = fingerprint(origin_id) if origin_id else fingerprint(text)
        chunks = chunk_text(text, target=self.chunk_chars)
        if embeddings is None or len(embeddings) != len(chunks):
            embeddings = await self._embed_all(chunks)
        classified = await self._classify(title, text) if classify else rule_classify_document(title, text, ())
        doc_domains = [str(d) for d in domains] if domains else list(classified["domains"])
        if kind == "note" and classified.get("kind") == "conversation":
            kind = "conversation"
        doc = {"doc_id": doc_id, "title": title, "kind": kind or "note", "source_root_id": root, "origin_id": origin_id,
               "observed_at": observed, "domains": doc_domains, "summary": classified.get("summary") or "", "status": "active",
               "version": 1, "chars": len(text), "uploaded_by": uploaded_by, "created_at": now, "updated_at": now,
               "root_known": bool(root_known and root)}
        memories = self._build_memories(doc, chunks, embeddings, version=1, extra_metadata=extra_metadata)
        speaker = speaker or uploaded_by or "document"
        detail = {"title": title, "chunks": len(chunks), "source_root_id": root} if audit_detail == "full" else {"chunks": len(chunks)}

        def fn(c):
            again = self.store._get_document_sync(c, doc_id)
            if again is not None:
                return self._public_document_sync(c, again)
            self.store._insert_document_sync(c, doc, text)
            self.store._add_version_sync(c, doc_id, 1, text, title, observed, now, "ingest")
            message_ids = self._write_chunks_sync(c, doc, chunks, memories, version=1, speaker=speaker, chat_id=conversation_chat_id,
                                                  extra_metadata=extra_metadata, entities=entities)
            self.store._set_holder_meta_sync(c, "last_ingest_at", now)
            self.store._log_sync(c, "document.ingest", doc_id, detail)
            if extra_sync is not None:
                extra_sync(c, {"doc": doc, "memory_ids": [m.memory_id for m in memories], "message_ids": message_ids, "chunks": len(chunks),
                               "version": 1})
            return self._public_document_sync(c, doc)

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="ingest")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    async def revise_document(self, doc_id: str, text: str, *, title: str | None = None, observed_at: str | None = None,
                              reason: str = "", domains: Iterable[str] | None = None, idempotency_key: str | None = None,
                              source_root_id: str | None = None, root_known: bool = True, conversation_chat_id: str | None = None,
                              speaker: str | None = None, extra_metadata: dict[str, Any] | None = None,
                              entities: Iterable[tuple[str, str, str]] | None = None, audit_detail: str = "full", classify: bool = True,
                              embeddings: list[list[float] | None] | None = None, extra_sync: ExtraSync | None = None) -> dict[str, Any]:
        """New version: old chunk memories are superseded by the new ones (retracted where the new text has fewer
        chunks), the root is recomputed, and the references that pointed at old chunks are reported as
        ``affected_ref_ids`` so the coordinator can re-verify the claims that cite them.

        The keyword-only connector parameters are those of :meth:`ingest_document`. A document whose content was redacted
        (``status='redacted'``, connector records only) can receive a new version when the source shows content again;
        retracted and deleted documents cannot be revised. On a sharded holder the call runs in the shard that holds the
        document, under that shard's write gate."""
        async with self._owner_gate(doc_id) as owner:
            return await owner._revise_local(doc_id, text, title=title, observed_at=observed_at, reason=reason, domains=domains,
                                             idempotency_key=idempotency_key, source_root_id=source_root_id, root_known=root_known,
                                             conversation_chat_id=conversation_chat_id, speaker=speaker, extra_metadata=extra_metadata,
                                             entities=entities, audit_detail=audit_detail, classify=classify, embeddings=embeddings,
                                             extra_sync=extra_sync)

    async def _revise_local(self, doc_id: str, text: str, *, title: str | None, observed_at: str | None, reason: str, domains: Iterable[str] | None,
                            idempotency_key: str | None, source_root_id: str | None, root_known: bool, conversation_chat_id: str | None,
                            speaker: str | None, extra_metadata: dict[str, Any] | None, entities: Iterable[tuple[str, str, str]] | None,
                            audit_detail: str, classify: bool, embeddings: list[list[float] | None] | None, extra_sync: ExtraSync | None) -> dict[str, Any]:
        doc = await self.store.get_document(doc_id, with_text=True)
        if doc is None:
            raise KeyError(doc_id)
        if doc["status"] in ("retracted", "deleted", "suspended"):
            raise ValueError(f"document is {doc['status']}")
        text = _clean_text(text)
        if not text:
            raise ValueError("document text is empty")
        new_title = normalize_ws(title) if title else doc["title"]
        observed = _norm_time(observed_at) if observed_at else doc.get("observed_at")
        now = now_iso()
        if source_root_id is not None:
            root = source_root_id
        else:
            root = fingerprint(doc["origin_id"]) if doc.get("origin_id") else fingerprint(text)
        version = int(doc["version"]) + 1
        chunks = chunk_text(text, target=self.chunk_chars)
        if embeddings is None or len(embeddings) != len(chunks):
            embeddings = await self._embed_all(chunks)
        classified = await self._classify(new_title, text) if classify else rule_classify_document(new_title, text, ())
        doc_domains = [str(d) for d in domains] if domains else (doc.get("domains") or list(classified["domains"]))
        new_doc = {**doc, "title": new_title, "source_root_id": root, "observed_at": observed, "domains": doc_domains,
                   "summary": classified.get("summary") or doc.get("summary") or "", "status": "revised", "version": version,
                   "chars": len(text), "updated_at": now, "root_known": bool(root_known and root)}
        new_doc.pop("text", None)
        memories = self._build_memories(new_doc, chunks, embeddings, version=version, extra_metadata=extra_metadata)
        speaker = speaker or doc.get("uploaded_by") or "document"
        detail_extra = {"source_root_id": root} if audit_detail == "full" else {}

        def fn(c):
            current = self.store._get_document_sync(c, doc_id)
            if current is None:
                raise KeyError(doc_id)
            if current["status"] in ("retracted", "deleted", "suspended"):
                raise ValueError(f"document is {current['status']}")
            old_ids = self.store._document_memory_ids_sync(c, doc_id, status="active")
            affected = [e["ref_id"] for e in self.store._exports_for_memories_sync(self.store._exports_conn(c), old_ids)]
            self.store._update_document_sync(c, doc_id, title=new_title, text=text, source_root_id=root, observed_at=observed,
                                             domains=doc_domains, summary=new_doc["summary"], status="revised", version=version,
                                             chars=len(text), updated_at=now, root_known=1 if new_doc["root_known"] else 0)
            self.store._add_version_sync(c, doc_id, version, text, new_title, observed, now, reason)
            message_ids = self._write_chunks_sync(c, new_doc, chunks, memories, version=version, speaker=speaker, chat_id=conversation_chat_id,
                                                  extra_metadata=extra_metadata, entities=entities)
            for old, new in zip(old_ids, memories):
                self.store._supersede_sync(c, old, new.memory_id)
            self.store._retract_memories_sync(c, old_ids[len(memories):], f"revised: {reason}" if reason else "revised")
            self.store._log_sync(c, "document.revise", doc_id, {"version": version, "reason": reason, "affected_refs": len(affected),
                                                                 **detail_extra})
            if extra_sync is not None:
                extra_sync(c, {"doc": new_doc, "memory_ids": [m.memory_id for m in memories], "message_ids": message_ids, "chunks": len(chunks),
                               "version": version, "superseded_memory_ids": old_ids, "affected_ref_ids": affected})
            return {"document": self._public_document_sync(c, new_doc), "affected_ref_ids": affected,
                    "previous_source_root_id": current["source_root_id"], "new_source_root_id": root, "reason": reason}

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="revise")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    async def retract_document(self, doc_id: str, reason: str = "", *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Withdraw a document: its content is purged (text, versions, chunk memories, FTS rows, embeddings, chunk messages,
        disclosed excerpts) and a content-free tombstone row stays with ``status='retracted'``. Reports every reference
        ever exported from it. Idempotent: retracting again reports the same references and changes nothing."""
        return await self._withdraw(doc_id, reason, status="retracted", idempotency_key=idempotency_key, op="retract")

    async def delete_document(self, doc_id: str, reason: str = "", *, purge: bool = True, status: str = "deleted",
                              idempotency_key: str | None = None, extra_sync: ExtraSync | None = None, optimize: bool = True) -> dict[str, Any]:
        """Delete a document's content (INGESTION.md §9.6 H1) and keep a content-free tombstone. ``status`` is ``deleted``
        (deleted at the source or by the owner) or ``redacted`` (identity kept, content removed). ``extra_sync`` runs in the
        same transaction with ``{doc_id, affected_ref_ids, purged}``. Returns ``{document, affected_ref_ids, purged, reason}``."""
        if not purge:
            raise ValueError("deletion always purges content")
        if status not in ("deleted", "redacted"):
            raise ValueError("status must be deleted or redacted")
        return await self._withdraw(doc_id, reason, status=status, idempotency_key=idempotency_key, op="delete", extra_sync=extra_sync,
                                    optimize=optimize)

    async def _withdraw(self, doc_id: str, reason: str, *, status: str, idempotency_key: str | None, op: str,
                        extra_sync: ExtraSync | None = None, optimize: bool = True) -> dict[str, Any]:
        async with self._owner_gate(doc_id) as owner:
            return await owner._withdraw_local(doc_id, reason, status=status, idempotency_key=idempotency_key, op=op, extra_sync=extra_sync,
                                               optimize=optimize)

    async def _withdraw_local(self, doc_id: str, reason: str, *, status: str, idempotency_key: str | None, op: str,
                              extra_sync: ExtraSync | None = None, optimize: bool = True) -> dict[str, Any]:
        doc = await self.store.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        title = {"retracted": "[retracted]", "deleted": "[deleted]", "redacted": "[redacted]"}[status]

        def fn(c):
            current = self.store._get_document_sync(c, doc_id)
            if current is None:
                raise KeyError(doc_id)
            if current["status"] in ("retracted", "deleted") or (current["status"] == "redacted" and status == "redacted"):
                # already withdrawn: report the same references, purge nothing new
                affected = [e["ref_id"] for e in self.store._exports_for_document_sync(self.store._exports_conn(c), doc_id)]
                result = {"affected_ref_ids": affected, "purged": {"memories": 0, "messages": 0, "versions": 0, "entities": 0}}
            else:
                result = self.store._purge_document_sync(c, doc_id, status=status, title=title, reason=reason)
                self.store._log_sync(c, f"document.{op}", doc_id, {"reason": reason, "affected_refs": len(result["affected_ref_ids"]),
                                                                    "purged": result["purged"]})
            if optimize:
                self.store._optimize_fts_sync(c)
            if extra_sync is not None:
                extra_sync(c, {"doc_id": doc_id, "affected_ref_ids": result["affected_ref_ids"], "purged": result["purged"]})
            current = self.store._get_document_sync(c, doc_id)
            return {"document": self._public_document_sync(c, current), "affected_ref_ids": result["affected_ref_ids"],
                    "purged": result["purged"], "reason": reason}

        try:
            result = await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op=op)
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}
        if optimize:
            await self.store.checkpoint_wal()
        return result

    async def update_document_metadata(self, doc_id: str, *, domains: Iterable[str] | None = None, extra_sync: ExtraSync | None = None,
                                       idempotency_key: str | None = None) -> dict[str, Any]:
        """Metadata-only change (labels, ACL, domains): no new version and no re-embedding; the document's domain list and
        its active chunk memories' metadata follow."""
        async with self._owner_gate(doc_id) as owner:
            return await owner._update_metadata_local(doc_id, domains=domains, extra_sync=extra_sync, idempotency_key=idempotency_key)

    async def _update_metadata_local(self, doc_id: str, *, domains: Iterable[str] | None, extra_sync: ExtraSync | None,
                                     idempotency_key: str | None) -> dict[str, Any]:
        def fn(c):
            current = self.store._get_document_sync(c, doc_id)
            if current is None:
                raise KeyError(doc_id)
            if domains is not None:
                ds = [str(d) for d in domains]
                self.store._update_document_sync(c, doc_id, domains=ds, updated_at=now_iso())
                c.execute("UPDATE memories SET metadata=json_set(metadata, '$.domains', json(?)) WHERE chat_id=? AND status='active'",
                          (j(ds), doc_id))
            if extra_sync is not None:
                extra_sync(c, {"doc_id": doc_id})
            return self._public_document_sync(c, self.store._get_document_sync(c, doc_id))

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="update_metadata")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    # ------------------------------------------------------------------ reads
    # Every read below takes the reader's ``audience`` ({"principal_ids", "complete", "owner"}). Connector records are
    # re-checked against their source ACL for it: a record outside what the audience may see is left out (lists,
    # search) or reported missing (single reads). With no audience nothing restricted is shown (fail closed); only the
    # holder's owner (``owner: true``) sees every live record. Direct uploads have no source ACL.
    def _doc_visible(self, doc_id: str, audience: Any, *, owner: "EvidenceStore | None" = None) -> bool:
        acl = self._record_acl_sync((owner or self._doc_store(doc_id)).store._conn, doc_id)
        return acl is None or self._acl_allows(acl, Audience.from_payload(audience))[0]

    async def document(self, doc_id: str, *, audience: Any = None) -> dict[str, Any] | None:
        owner = self._doc_store(doc_id)
        doc = await owner.store.get_document(doc_id)
        if doc is None or not self._doc_visible(doc_id, audience, owner=owner):
            return None
        return await owner._public_document(doc)

    async def document_text(self, doc_id: str, version: int | None = None, *, audience: Any = None) -> str | None:
        owner = self._doc_store(doc_id)
        if not self._doc_visible(doc_id, audience, owner=owner):
            return None
        if version is not None:
            return await owner.store.document_version_text(doc_id, version)
        doc = await owner.store.get_document(doc_id, with_text=True)
        return doc["text"] if doc else None

    async def list_documents(self, *, status: str | None = None, limit: int = 100, offset: int = 0, audience: Any = None) -> list[dict[str, Any]]:
        if not self._multi():
            docs = await self.store.list_documents(status=status, limit=limit, offset=offset)
            return [await self._public_document(d) for d in docs if self._doc_visible(d["doc_id"], audience, owner=self)]
        # every shard's newest documents, merged in the single-store order (updated_at DESC, doc_id)
        merged: list[tuple[dict[str, Any], EvidenceStore]] = []
        for _spec, owner in self.shards.open_stores(statuses=("active", "draining", "readonly")):
            merged += [(d, owner) for d in await owner.store.list_documents(status=status, limit=limit + offset, offset=0)]
        merged.sort(key=lambda x: x[0]["doc_id"])
        merged.sort(key=lambda x: x[0].get("updated_at") or "", reverse=True)
        page = merged[offset:offset + limit]
        return [await owner._public_document(d) for d, owner in page if self._doc_visible(d["doc_id"], audience, owner=owner)]

    async def recent_memories(self, limit: int = 20) -> list[Any]:
        """The newest active memories of the holder, across its shards (the owner's "recent memory" view)."""
        if not self._multi():
            return await self.store.list_memories(status="active", limit=limit, order="created_at DESC")
        out: list[Any] = []
        for _spec, owner in self.shards.open_stores(statuses=("active", "draining", "readonly")):
            out += await owner.store.list_memories(status="active", limit=limit, order="created_at DESC")
        out.sort(key=lambda m: m.created_at or "", reverse=True)
        return out[:limit]

    async def search(self, query: str, k: int = 10, *, audience: Any = None, **filters: Any) -> list[dict[str, Any]]:
        """Hybrid retrieval over this holder's memories for an API reader (memory and document ids are visible here;
        the coordinator's question path is :meth:`answer_question`). Restricted connector records are filtered out
        before retrieval, so they neither appear nor influence the ranking. On a holder with several shards the search fans
        out (§7.4) and the returned list carries ``partial`` / ``truncated``; with one shard it is today's single-store
        search."""
        if self._multi():
            from ..ingest.fanout import ShardedResults
            res = await self._retrieve_sharded(query, k=k, audience=Audience.from_payload(audience), **filters)
            return ShardedResults([{**self._result_dict(r), "shard_id": getattr(r, "shard_id", "s0")} for r in res], partial=res.partial,
                                  truncated=res.truncated, shards=res.shards, failed=res.failed, timings_ms=res.timings_ms)
        allowed = self._allowed_memory_ids(Audience.from_payload(audience))
        return [self._result_dict(r) for r in await self._retrieve(query, k=k, allowed_ids=allowed, **filters)]

    async def raw_for_ref(self, ref_id: str, *, audience: Any = None) -> dict[str, Any] | None:
        """Resolve an exported reference to its document. The coordinator calls this only after its own raw-grant
        check, and the holder service only for an envelope signed with its route key.

        Never returns text of a withdrawn document (retracted, deleted, redacted, suspended): the reply is content-free
        with the document's status. A connector record is re-checked against its source ACL at this moment: unless the
        ``audience`` (``{"principal_ids", "complete", "owner"}``) is the holder owner or entirely inside the source's
        members, the reply is content-free with ``status='withheld'``."""
        export = await self.store.get_export(ref_id)
        if export is None:
            return None
        owner = self._doc_store(export["doc_id"])          # exports stay in s0; the document is wherever its record lives
        doc = await owner.store.get_document(export["doc_id"], with_text=True)
        if doc is None:
            return None
        base = {"ref_id": ref_id, "doc_id": doc["doc_id"], "observed_at": doc.get("observed_at"), "version": doc["version"],
                "status": doc["status"], "source_root_id": doc["source_root_id"], "kind": doc["kind"], "question_id": export["question_id"]}
        if doc["status"] not in LIVE_DOCUMENT_STATUSES:
            return {**base, "title": doc["title"], "text": "", "chunk_text": None, "withheld": doc["status"]}
        acl = self._record_acl_sync(owner.store._conn, doc["doc_id"])
        if acl is not None:
            ok, why = self._acl_allows(acl, Audience.from_payload(audience))
            if not ok:
                return {**base, "status": "withheld", "title": f"{acl['source_app']} {acl['kind']}", "text": "", "chunk_text": None,
                        "withheld": why}
        memory = await owner.store.get_memory(export["memory_id"])
        return {**base, "title": doc["title"], "text": doc["text"], "chunk_text": memory.text if memory else None}

    async def stats(self) -> dict[str, Any]:
        docs = await self.store.document_counts()
        mem = await self.store.memory_counts()
        entities = await self.store.entity_count()
        queue = await self.store.pending_jobs()
        last_ingest = await self.store.get_holder_meta("last_ingest_at")
        multi = self._multi()
        if multi:
            # counts over every shard (an entity named in two shards is counted in each)
            docs, by_status = dict(docs), dict(mem["by_status"])
            for spec, owner in self.shards.open_stores(statuses=("active", "draining", "readonly")):
                if owner is self:
                    continue
                for s, n in (await owner.store.document_counts()).items():
                    docs[s] = docs.get(s, 0) + n
                for s, n in (await owner.store.memory_counts())["by_status"].items():
                    by_status[s] = by_status.get(s, 0) + n
                entities += await owner.store.entity_count()
                queue += await owner.store.pending_jobs()
                other = await owner.store.get_holder_meta("last_ingest_at")
                last_ingest = max(x for x in (last_ingest, other) if x) if (last_ingest or other) else None
            mem = {"by_status": by_status}
        out = {
            "holder_id": self.holder_id,
            "documents": sum(n for s, n in docs.items() if s in LIVE_DOCUMENT_STATUSES),
            "documents_by_status": docs,
            "memories": int(mem["by_status"].get("active", 0)),
            "memories_by_status": mem["by_status"],
            "entities": entities,
            "queue": queue,
            "exports": await self.store.export_count(),
            "questions_answered": int(await self.store.get_holder_meta("questions_answered", "0") or 0),
            "last_ingest_at": last_ingest,
            "holder_version": HOLDER_VERSION,
            "ingest": self._ingest_stats_sharded() if multi else self._ingest_stats_sync(self.store._conn),
        }
        if self._is_control() and self.path != ":memory:":
            try:
                out["shards"] = await self.shards.report()       # counts, bytes and latencies per shard (coordinator registry)
            except Exception:
                logger.exception("shard report failed for holder %s", self.holder_id)
        return out

    # ------------------------------------------------------------------ connector records: ACL and catalog
    def taxonomy(self) -> Taxonomy:
        """The holder's copy of the tenant taxonomy plus its personal domains (the defaults until one is installed)."""
        if self._taxonomy is None:
            tax = load_taxonomy_sync(self.store._conn)
            self._taxonomy = tax if tax.domains else default_taxonomy()
        return self._taxonomy

    def set_taxonomy(self, tax: Taxonomy | None) -> None:
        self._taxonomy = tax

    def _record_acl_sync(self, c: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
        """The connector record behind a document (``None`` for direct uploads): permissions, source opt-in and state.
        ``c`` is the connection of the shard that holds the record; sources and connectors are always read from s0."""
        if c is self.store._conn and self._is_control():
            r = c.execute("""SELECT r.record_id, r.object_key, r.kind, r.source_app, r.permissions, r.visibility, r.deletion_status, r.sensitivity, r.flags,
                                    r.source_id, s.exportable, s.disclosure, s.access_state, s.allow_external_models, s.visibility AS source_visibility,
                                    s.member_ids AS source_member_ids, s.membership_ref AS source_membership_ref, cn.connector_type
                             FROM ingest_records r LEFT JOIN connector_sources s ON s.source_id = r.source_id
                             LEFT JOIN connectors cn ON cn.connector_id = r.connector_id WHERE r.record_id=?""", (doc_id,)).fetchone()
        else:
            r = self._record_acl_row_split(c, doc_id)
        if r is None:
            return None
        perms = Permissions.from_dict(jl(r["permissions"], {}))
        if r["source_visibility"]:
            # the source's ACL as it is *now* narrows what was recorded at ingest: a channel turned members-only, or a member
            # removed, takes effect at the next use without re-ingesting (a source that opens up widens only on re-sync)
            perms = narrow(Permissions(r["source_visibility"], tuple(jl(r["source_member_ids"], []) or ()), r["source_membership_ref"]), perms)
        exportable = bool(r["exportable"]) if r["exportable"] is not None else perms.visibility != "private"
        return {"record_id": r["record_id"], "kind": r["kind"], "source_app": r["source_app"], "permissions": perms,
                "visibility": perms.visibility, "deletion_status": r["deletion_status"], "sensitivity": r["sensitivity"],
                "flags": set(jl(r["flags"], [])), "exportable": exportable, "disclosure": r["disclosure"],
                "access_state": r["access_state"] or "ok", "connector_type": r["connector_type"] or r["source_app"],
                "allow_external_models": r["allow_external_models"], "object_key": r["object_key"]}

    def _control_conn(self) -> sqlite3.Connection:
        return (self.store.control_store or self.store)._conn

    def _record_acl_row_split(self, c: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
        """The same row as the s0 join above, for a record that lives in a data shard: the record from its shard, its source
        and connector from s0 (control tables)."""
        r = c.execute("""SELECT record_id, object_key, kind, source_app, permissions, visibility, deletion_status, sensitivity, flags, source_id,
                                connector_id FROM ingest_records WHERE record_id=?""", (doc_id,)).fetchone()
        if r is None:
            return None
        ctl = self._control_conn()
        s = ctl.execute("""SELECT exportable, disclosure, access_state, allow_external_models, visibility AS source_visibility,
                                  member_ids AS source_member_ids, membership_ref AS source_membership_ref FROM connector_sources WHERE source_id=?""",
                        (r["source_id"],)).fetchone() if r["source_id"] else None
        cn = ctl.execute("SELECT connector_type FROM connectors WHERE connector_id=?", (r["connector_id"],)).fetchone()
        row = dict(r)
        row.update(dict(s) if s is not None else {"exportable": None, "disclosure": None, "access_state": None, "allow_external_models": None,
                                                  "source_visibility": None, "source_member_ids": None, "source_membership_ref": None})
        row["connector_type"] = cn["connector_type"] if cn is not None else None
        return row

    def _members_of_ref(self, ref: str) -> list[str]:
        return [x["member_id"] for x in self._control_conn().execute("SELECT member_id FROM acl_memberships WHERE membership_ref=?", (ref,))]

    def _acl_allows(self, acl: dict[str, Any], audience: Audience | None) -> tuple[bool, str]:
        owner = audience is not None and audience.owner
        if acl["deletion_status"] != "live":
            return False, acl["deletion_status"]
        if acl["access_state"] != "ok" and not owner:
            return False, "access_lost"
        return decide(acl["permissions"], audience, exportable=acl["exportable"], owner_ids=self.owner_ids, resolve_ref=self._members_of_ref)

    def _allowed_memory_ids(self, audience: Audience | None) -> set[str] | None:
        """The retrieval allow-set for this audience, or ``None`` when nothing in the holder is withheld from it."""
        if audience is not None and audience.owner:
            return None
        c = self.store._conn
        rows = c.execute("""SELECT r.record_id FROM ingest_records r LEFT JOIN connector_sources s ON s.source_id = r.source_id
                            WHERE r.visibility <> 'public' OR COALESCE(s.visibility, 'public') <> 'public' OR r.deletion_status <> 'live'
                               OR COALESCE(s.access_state, 'ok') <> 'ok'""").fetchall()
        denied = []
        for row in rows:
            acl = self._record_acl_sync(c, row["record_id"])
            if acl is not None and not self._acl_allows(acl, audience)[0]:
                denied.append(row["record_id"])
        if not denied:
            return None
        blocked: set[str] = set()
        for i in range(0, len(denied), 500):
            part = denied[i:i + 500]
            blocked.update(x["memory_id"] for x in c.execute(f"SELECT memory_id FROM memories WHERE chat_id IN ({','.join('?' * len(part))})", part))
            blocked.update(x["memory_id"] for x in c.execute(f"SELECT memory_id FROM record_memories WHERE record_id IN ({','.join('?' * len(part))})", part))
        return {x["memory_id"] for x in c.execute("SELECT memory_id FROM memories WHERE status='active'")} - blocked

    def _allowed_memory_ids_in(self, owner: "EvidenceStore", audience: Audience | None) -> set[str] | None:
        """:meth:`_allowed_memory_ids` for one shard of a sharded holder (the audience filter applies per shard, before
        ranking). s0 is the same computation as above; a data shard reads its records from its own file and their sources
        from s0."""
        if owner is self:
            return self._allowed_memory_ids(audience)
        if audience is not None and audience.owner:
            return None
        c = owner.store._conn
        restricted = [r["source_id"] for r in self._control_conn().execute(
            "SELECT source_id FROM connector_sources WHERE visibility <> 'public' OR access_state <> 'ok'")]
        ids = {r["record_id"] for r in c.execute("SELECT record_id FROM ingest_records WHERE visibility <> 'public' OR deletion_status <> 'live'")}
        for i in range(0, len(restricted), 500):
            part = restricted[i:i + 500]
            ids.update(r["record_id"] for r in c.execute(f"SELECT record_id FROM ingest_records WHERE source_id IN ({','.join('?' * len(part))})", part))
        denied = []
        for rid in sorted(ids):
            acl = self._record_acl_sync(c, rid)
            if acl is not None and not self._acl_allows(acl, audience)[0]:
                denied.append(rid)
        if not denied:
            return None
        blocked: set[str] = set()
        for i in range(0, len(denied), 500):
            part = denied[i:i + 500]
            blocked.update(x["memory_id"] for x in c.execute(f"SELECT memory_id FROM memories WHERE chat_id IN ({','.join('?' * len(part))})", part))
            blocked.update(x["memory_id"] for x in c.execute(f"SELECT memory_id FROM record_memories WHERE record_id IN ({','.join('?' * len(part))})", part))
        return {x["memory_id"] for x in c.execute("SELECT memory_id FROM memories WHERE status='active'")} - blocked

    def _record_domains_sync(self, c: sqlite3.Connection, record_id: str) -> list[str]:
        rows = c.execute("SELECT domain_id FROM domain_memberships WHERE record_id=? AND status='active' ORDER BY is_primary DESC, confidence DESC, domain_id",
                         (record_id,)).fetchall()
        return [r["domain_id"] for r in rows if not is_personal(r["domain_id"])]

    def _ingest_stats_sync(self, c: sqlite3.Connection, *, min_records_to_publish: int = 5) -> dict[str, Any]:
        """Counts only (heartbeat E10): never names, titles or personal domains."""
        records = {r["source_app"]: int(r["n"]) for r in c.execute(
            "SELECT source_app, COUNT(*) AS n FROM ingest_records WHERE deletion_status='live' GROUP BY source_app")}
        queue = {r["priority_class"]: int(r["n"]) for r in c.execute(
            "SELECT priority_class, COUNT(*) AS n FROM ingest_queue WHERE status IN ('queued','leased') GROUP BY priority_class")}
        queue["dead"] = int(c.execute("SELECT COUNT(*) AS n FROM ingest_queue WHERE status='dead'").fetchone()["n"])
        domains = {r["domain_id"]: int(r["n"]) for r in c.execute(
            """SELECT dm.domain_id, COUNT(*) AS n FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
               WHERE dm.status='active' AND r.deletion_status='live' GROUP BY dm.domain_id""")
            if not is_personal(r["domain_id"]) and int(r["n"]) >= min_records_to_publish}
        connectors = int(c.execute("SELECT COUNT(*) AS n FROM connectors WHERE status NOT IN ('disconnected')").fetchone()["n"])
        return {"connectors": connectors, "records": sum(records.values()), "by_app": records, "queue": queue, "domains": domains}

    def _ingest_stats_sharded(self, *, min_records_to_publish: int = 5) -> dict[str, Any]:
        """:meth:`_ingest_stats_sync` summed over the shards (the publication threshold applies to the holder's totals)."""
        out = self._ingest_stats_sync(self.store._conn, min_records_to_publish=0)
        by_app: dict[str, int] = {}
        domains: dict[str, int] = {}
        for spec, owner in self.shards.open_stores(statuses=("active", "draining", "readonly")):
            c = owner.store._conn
            for r in c.execute("SELECT source_app, COUNT(*) AS n FROM ingest_records WHERE deletion_status='live' GROUP BY source_app"):
                by_app[r["source_app"]] = by_app.get(r["source_app"], 0) + int(r["n"])
            for r in c.execute("""SELECT dm.domain_id, COUNT(*) AS n FROM domain_memberships dm JOIN ingest_records r ON r.record_id = dm.record_id
                                  WHERE dm.status='active' AND r.deletion_status='live' GROUP BY dm.domain_id"""):
                if not is_personal(r["domain_id"]):
                    domains[r["domain_id"]] = domains.get(r["domain_id"], 0) + int(r["n"])
        out.update(records=sum(by_app.values()), by_app=by_app, domains={d: n for d, n in domains.items() if n >= min_records_to_publish})
        return out

    # ------------------------------------------------------------------ answering
    async def answer_question(self, question: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Answer a routed ``QuestionArtifact`` and return the ``ResponseArtifact`` payload.

        Steps: policy check (scope, domains, tenant) -> temporal retrieval -> evidence items with opaque ``ref_id``
        and the policy-approved disclosure -> model task ``answer_from_evidence`` (or its rule) -> commit exports for
        the references actually used. The response never carries memory ids, document ids or full texts.

        Two different bounds apply: ``deny_patterns`` are removed before anything reaches a model, and the model then
        reads whole (redacted) chunks so truncation cannot hide the relevant sentence; ``max_excerpt_chars`` and the
        disclosure level bound the ``disclosed_excerpt`` of each reference, ``max_answer_chars`` bounds ``content``.
        With the deterministic fake the answer quotes the evidence verbatim, so ``max_answer_chars`` is the effective
        cap on what the answer text itself discloses.
        """
        qid = str(question.get("question_id") or new_id("q"))
        text = normalize_ws(str(question.get("text") or ""))
        policy = question.get("policy") or {}
        now = now_iso()
        base = {"question_id": qid, "holder_id": self.holder_id, "answered_at": now}
        if question.get("route_id"):
            base["route_id"] = question["route_id"]
        provenance = {"retrieval_operator": RETRIEVAL_OPERATOR, "channels": [], "holder_version": HOLDER_VERSION,
                      "policy": {"disclosure": self.export_policy["disclosure"], "answer_scopes": list(self.export_policy["answer_scopes"])},
                      "memory_count": 0}

        reason = self._policy_reason(question)
        if not reason and self._policy_problems:
            reason = "export policy is invalid (" + "; ".join(self._policy_problems) + "); declining until the owner fixes it"
        if reason or not text:
            reason = reason or "question has no text"
            response = {**base, "status": "declined", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None, "reason": reason}
            return await self._commit_answer([], response, idempotency_key)

        level = effective_disclosure(self.export_policy["disclosure"], policy.get("disclosure"))
        max_excerpt = int(self.export_policy["max_excerpt_chars"])
        audience = Audience.from_payload(question.get("audience"))
        # records the audience may not see are excluded before ranking (recall), and re-checked per item below (use time)
        if self._multi():
            # several shards: bounded fan-out, the audience filter applied per shard, ranks fused (§7.4)
            results = await self._retrieve_sharded(text, k=RETRIEVAL_K, since=question.get("valid_from"), until=question.get("valid_to"),
                                                   audience=audience)
            provenance["shards"] = results.describe()
        else:
            allowed_ids = self._allowed_memory_ids(audience)
            results = await self._retrieve(text, k=RETRIEVAL_K, since=question.get("valid_from"), until=question.get("valid_to"), allowed_ids=allowed_ids)
        known = await self.store.exports_for_question(qid)
        items: list[dict[str, Any]] = []
        docs: dict[str, dict[str, Any] | None] = {}
        acls: dict[str, dict[str, Any] | None] = {}
        conns: dict[str, sqlite3.Connection] = {}
        channels: set[str] = set()
        for r in results:
            m = r.memory
            doc_id = m.metadata.get("doc_id") or m.chat_id
            if doc_id not in docs:
                owner = self._result_store(r)
                conns[doc_id] = owner.store._conn
                docs[doc_id] = await owner.store.get_document(doc_id)
                acls[doc_id] = self._record_acl_sync(conns[doc_id], doc_id) if docs[doc_id] is not None else None
            doc, acl = docs[doc_id], acls[doc_id]
            if doc is None or doc["status"] not in LIVE_DOCUMENT_STATUSES:
                continue
            if acl is not None and not self._acl_allows(acl, audience)[0]:
                continue
            item_level = self._record_level(level, acl)
            if acl is not None and item_level == "none":
                # a connector record its source (or its sensitivity) keeps at disclosure 'none' never reaches a model or the
                # answer text: an answer quoting it would disclose exactly what the reference withholds
                continue
            channels.update(ch for ch in ("vector", "keyword", "graph") if ch in r.channels)
            # the model (holder-side) reads the whole redacted chunk; the excerpt cap bounds what is *disclosed*
            redacted = self._redact(m.text)
            excerpt = clip(redacted, max_excerpt) if max_excerpt else ""
            disclosed = {"excerpt": excerpt, "summary": clip(self._redact(doc.get("summary") or ""), max_excerpt or 140), "none": ""}[item_level]
            items.append({
                "ref_id": (known.get(m.memory_id) or {}).get("ref_id") or new_ref_id(),
                "memory_id": m.memory_id, "doc": doc, "acl": acl, "level": item_level, "model_text": redacted, "disclosed": disclosed,
                "observed_at": m.observed_at, "kind": doc["kind"], "title": self._disclosed_title(doc, item_level, acl=acl, audience=audience),
                "conn": conns[doc_id],
            })
        provenance["channels"] = sorted(channels)
        provenance["memory_count"] = len(items)
        if not items:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key)

        # a record flagged as carrying instructions, or from a source whose owner forbids external models, never reaches a
        # model: the answer is composed by the local rule
        local_only = any(i["acl"] is not None and ("suspicious_instructions" in i["acl"]["flags"] or i["acl"].get("allow_external_models") == 0)
                         for i in items)
        model_out = await self._run_answer(text, [{"ref_id": i["ref_id"], "excerpt": i["model_text"], "observed_at": i["observed_at"]} for i in items],
                                           question_id=qid, provenance=provenance, force_rule=local_only)
        used_ids = [str(x) for x in (model_out.get("used_ref_ids") or [])]
        used = [i for i in items if i["ref_id"] in set(used_ids)]
        answer = normalize_ws(str(model_out.get("answer") or ""))
        if model_out.get("no_evidence") or not used or not answer:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key)
        max_answer = int(self.export_policy["max_answer_chars"])
        content = clip(self._redact(answer), max_answer) if max_answer else self._redact(answer)
        refs = [self._ref_shape(i) for i in used]
        try:
            confidence = max(0.0, min(1.0, float(model_out.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.5
        response = {**base, "status": "answered", "content": content, "confidence": round(confidence, 3), "evidence_refs": refs,
                    "provenance": provenance, "freshness_at": max((r["freshness_at"] for r in refs if r["freshness_at"]), default=None)}
        exports = [{"ref_id": i["ref_id"], "memory_id": i["memory_id"], "doc_id": i["doc"]["doc_id"], "question_id": qid,
                    "disclosed_excerpt": i["disclosed"], "disclosure_level": i["level"]} for i in used]
        return await self._commit_answer(exports, response, idempotency_key)

    def _record_level(self, level: str, acl: dict[str, Any] | None) -> str:
        """A connector record can lower (never raise) the disclosure: the source's own setting, and ``none`` for restricted records."""
        if acl is None:
            return level
        out = level
        if acl.get("disclosure") in DISCLOSURE_LEVELS:
            out = effective_disclosure(out, acl["disclosure"])
        if acl.get("sensitivity") == "restricted":
            out = "none"
        return out

    def _ref_shape(self, i: dict[str, Any]) -> dict[str, Any]:
        doc, acl = i["doc"], i["acl"]
        root = doc["source_root_id"]
        ref = {
            "ref_id": i["ref_id"], "source_root_id": root, "root_known": bool(root) and bool(doc.get("root_known", 1)),
            "kind": REF_KIND.get(doc["kind"], doc["kind"]) if acl is not None else i["kind"], "title": i["title"],
            "disclosed_excerpt": i["disclosed"], "disclosure_level": i["level"],
            # evidence is as fresh as the time its content was observed, not the time it was uploaded or indexed
            "observed_at": i["observed_at"], "freshness_at": i["observed_at"] or doc.get("observed_at"),
        }
        if acl is not None:
            # the provider object's identity (a one-way hash): two holders' copies of one message, even at different versions,
            # are one source for independence counting (product decision 2: one canonical identity and root)
            ref["meta"] = {"object_key": acl["object_key"]}
            if self.export_policy.get("disclose_source_app", True):
                ref["meta"].update({"source_app": acl["source_app"], "record_kind": acl["kind"], "connector_type": acl["connector_type"],
                                    "domain_ids": self._record_domains_sync(i.get("conn") or self.store._conn, doc["doc_id"])})
        return ref

    def _redact(self, text: str) -> str:
        """Owner deny patterns plus the built-in secret detectors, always (INGESTION.md §10.4)."""
        return mask_secrets(redact(text or "", self._deny))[0]

    async def manual_response(self, question: dict[str, Any], content: str, doc_ids: Iterable[str], *,
                              idempotency_key: str | None = None) -> dict[str, Any]:
        """A person answers from documents they name (``POST /questions/{id}/respond`` or the ``manual_response``
        control): the same ResponseArtifact shape as :meth:`answer_question` with one reference per document and
        ``provenance.retrieval_operator = 'human'``. The export policy still governs each reference's disclosure and
        the person's text passes through the same deny patterns. Unknown document ids raise ``KeyError``; retracted
        documents are skipped."""
        qid = str(question.get("question_id") or new_id("q"))
        ids = [str(d) for d in dict.fromkeys(doc_ids or []) if d]
        content = normalize_ws(str(content or ""))
        now = now_iso()
        base = {"question_id": qid, "holder_id": self.holder_id, "answered_at": now}
        if question.get("route_id"):
            base["route_id"] = question["route_id"]
        level = effective_disclosure(self.export_policy["disclosure"], (question.get("policy") or {}).get("disclosure"))
        max_excerpt = int(self.export_policy["max_excerpt_chars"])
        provenance = {"retrieval_operator": "human", "channels": [], "holder_version": HOLDER_VERSION,
                      "policy": {"disclosure": self.export_policy["disclosure"], "answer_scopes": list(self.export_policy["answer_scopes"])},
                      "memory_count": 0, "answer_method": "human"}
        known = await self.store.exports_for_question(qid)
        audience = Audience.from_payload(question.get("audience"))
        refs: list[dict[str, Any]] = []
        exports: list[dict[str, Any]] = []
        for doc_id in ids:
            owner = self._doc_store(doc_id)
            doc = await owner.store.get_document(doc_id, with_text=True)
            if doc is None:
                raise KeyError(doc_id)
            memory_ids = await owner.store.document_memory_ids(doc_id)
            if doc["status"] not in LIVE_DOCUMENT_STATUSES or not memory_ids:
                continue
            acl = self._record_acl_sync(owner.store._conn, doc_id)
            if acl is not None and not self._acl_allows(acl, audience)[0]:
                continue                       # a person cannot export what the audience may not see either
            item_level = self._record_level(level, acl)
            memory_id = memory_ids[0]          # the document's first chunk anchors the reference in the export ledger
            excerpt = clip(self._redact(doc["text"]), max_excerpt) if max_excerpt else ""
            disclosed = {"excerpt": excerpt, "summary": clip(self._redact(doc.get("summary") or ""), max_excerpt or 140), "none": ""}[item_level]
            ref_id = (known.get(memory_id) or {}).get("ref_id") or new_ref_id()
            item = {"ref_id": ref_id, "doc": doc, "acl": acl, "level": item_level, "disclosed": disclosed, "observed_at": doc.get("observed_at"),
                    "kind": doc["kind"], "title": self._disclosed_title(doc, item_level, acl=acl, audience=audience), "conn": owner.store._conn}
            refs.append(self._ref_shape(item))
            exports.append({"ref_id": ref_id, "memory_id": memory_id, "doc_id": doc_id, "question_id": qid, "disclosed_excerpt": disclosed,
                            "disclosure_level": item_level})
        provenance["memory_count"] = len(refs)
        if not content:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key, op="manual_response")
        max_answer = int(self.export_policy["max_answer_chars"])
        response = {**base, "status": "answered", "content": clip(self._redact(content), max_answer) if max_answer else self._redact(content),
                    "confidence": 0.8, "evidence_refs": refs, "provenance": provenance,
                    "freshness_at": max((r["freshness_at"] for r in refs if r["freshness_at"]), default=None)}
        return await self._commit_answer(exports, response, idempotency_key, op="manual_response")

    # ------------------------------------------------------------------ internals
    def _disclosed_title(self, doc: dict[str, Any], level: str, *, acl: dict[str, Any] | None = None, audience: Audience | None = None) -> str:
        """Titles are disclosure too: redacted like the text, and replaced by the document kind when nothing may be disclosed.
        Email subjects and issue titles are content: a record from a non-public source discloses only ``"<app> <kind>"``
        unless the audience is the holder owner."""
        if level == "none":
            return str(doc.get("kind") or "document")
        if acl is not None and acl["visibility"] != "public" and not (audience is not None and audience.owner):
            return f"{acl['source_app']} {acl['kind']}"
        return self._redact(str(doc.get("title") or ""))

    async def _commit_answer(self, exports: list[dict[str, Any]], response: dict[str, Any], idempotency_key: str | None,
                             op: str = "question") -> dict[str, Any]:
        """Exports (the only state a question leaves behind) and the idempotency marker land in one transaction."""
        if not exports and not idempotency_key:
            return response
        # exports live in s0; a cited chunk lives in its record's shard (read in the same synchronous step, so no write of
        # this process can land between the check and the commit)
        owners = {e["doc_id"]: self._doc_store(e["doc_id"]).store._conn for e in exports}

        def fn(c):
            for e in exports:
                # the answer was composed outside this transaction: if a cited chunk was superseded or its document retracted
                # meanwhile, the revise/retract could not have reported this export, so the whole answer is redone instead
                mc = c if owners[e["doc_id"]] is self.store._conn else owners[e["doc_id"]]
                m = mc.execute("SELECT status FROM memories WHERE memory_id=?", (e["memory_id"],)).fetchone()
                d = mc.execute("SELECT status FROM documents WHERE doc_id=?", (e["doc_id"],)).fetchone()
                if m is None or m["status"] != "active" or d is None or d["status"] not in LIVE_DOCUMENT_STATUSES:
                    raise EvidenceChanged(f"evidence for {e['ref_id']} changed while answering")
                ref = self.store._record_export_sync(c, created_at=response["answered_at"], **e)
                if ref != e["ref_id"]:   # a concurrent answer to the same question won the (memory, question) slot
                    for r in response["evidence_refs"]:
                        if r["ref_id"] == e["ref_id"]:
                            r["ref_id"] = ref
            if response["status"] == "answered":
                self.store._increment_meta_sync(c, "questions_answered")
            self.store._log_sync(c, "question.answer", response["question_id"],
                                 {"status": response["status"], "refs": len(response["evidence_refs"]), "reason": response.get("reason")})
            return response

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op=op)
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or response

    def _policy_reason(self, question: dict[str, Any]) -> str | None:
        """Mirror of ``Authorizer.can_route`` on the holder side (re-checked at use time, D9)."""
        if question.get("tenant_id") and question["tenant_id"] != self.tenant_id:
            return "tenant mismatch"
        scopes = set(self.export_policy.get("answer_scopes") or [])
        visibility = str((question.get("policy") or {}).get("visibility") or "unit")
        if visibility not in scopes:
            return f"holder policy does not answer {visibility}-scoped questions"
        mine = {d for d in self.domains if not is_personal(d)}
        asked = {str(d) for d in (question.get("candidate_domains") or [])}
        wanted = {d for d in asked if not is_personal(d)}
        if asked and not wanted:
            return "personal domains never route questions across the organization"
        # taxonomy-aware: equal, aliased, or one an ancestor of the other (a question on 'infrastructure' reaches a holder
        # tagged 'infrastructure.ci-cd'); unknown flat strings still match only themselves, '*' matches everything
        if mine and wanted and not domains_overlap(mine, wanted, self.taxonomy()):
            return "no matching evidence domain"
        return None

    async def _retrieve(self, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                        allowed_ids: set[str] | None = None, **filters: Any) -> list[RetrievedMemory]:
        """Active chunk memories only: a time window makes the retriever include superseded rows (they were true at
        the time), but evidence must cite the current version so a reference is not stale on arrival. ``allowed_ids``
        restricts retrieval to memories the requesting audience may see."""
        query = normalize_ws(query)
        if not query:
            return []
        if allowed_ids is not None:
            filters["allowed_ids"] = allowed_ids
        since_iso, until_iso = _norm_time(since), _norm_time(until)
        t0 = time.perf_counter()
        try:
            results = await self.retriever.search(query, k=max(1, k) * 2, since=since_iso, until=until_iso, **filters)
        except Exception:
            logger.exception("retrieval failed for holder %s", self.holder_id)
            return []
        if self._is_control():
            self.shards.observe(self.shard_id, "query", (time.perf_counter() - t0) * 1000)     # §7.9 query p95
        return [r for r in results if r.memory.status == "active"][:k]

    async def _retrieve_sharded(self, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                                audience: Audience | None = None, **filters: Any) -> Any:
        """Bounded fan-out over the holder's shards (§7.4): at most 8 shards, 1.5 s per shard, the query embedded once, the
        audience allow-set computed per shard, ranks fused by RRF. A memory whose record has moved on (the source rows of a
        split between its cutover and its cleanup) is only taken from the shard its record lives in. The result carries
        ``partial`` and ``truncated``."""
        from ..ingest.fanout import ShardedResults, ShardedRetriever
        query = normalize_ws(query)
        if not query:
            return ShardedResults([])

        def allowed_for(spec: Any) -> set[str] | None:
            owner = self if spec.shard_id == self.shard_id else self.shards.store(spec.shard_id)
            return self._allowed_memory_ids_in(owner, audience)

        res = await ShardedRetriever(self.shards, merge=self.fanout_merge).search(query, k=max(1, k) * 2, fetch_k=self.fanout_fetch_k or max(1, k) * 2,
                                                                                  since=_norm_time(since),
                                                         until=_norm_time(until), allowed_for=allowed_for, filters=filters)
        kept = [r for r in res if self.shards.shard_of_record(r.memory.metadata.get("doc_id") or r.memory.chat_id) == getattr(r, "shard_id", "s0")]
        return ShardedResults(kept[:k], partial=res.partial, truncated=res.truncated, shards=res.shards, failed=res.failed, timings_ms=res.timings_ms)

    def _result_store(self, r: Any) -> "EvidenceStore":
        sid = getattr(r, "shard_id", None)
        if not sid or sid == self.shard_id:
            return self
        return self.shards.store(sid)

    @staticmethod
    def _result_dict(r: RetrievedMemory) -> dict[str, Any]:
        m = r.memory
        return {
            "memory_id": m.memory_id, "text": m.text, "kind": m.kind, "score": r.score, "observed_at": m.observed_at,
            "event_time": m.event_time, "status": m.status, "doc_id": m.metadata.get("doc_id") or m.chat_id,
            "title": m.metadata.get("title") or m.subject_name, "chunk_index": m.metadata.get("chunk_index"),
            "source_root_id": m.metadata.get("source_root_id"), "channels": dict(r.channels), "explanation": r.explanation,
            "sources": [{"message_id": s.message_id, "chat_id": s.chat_id, "seq": s.seq} for s in r.sources],
        }

    async def _embed_all(self, chunks: list[str]) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        for chunk in chunks:
            try:
                vec = await self.llm.embed(chunk)
                out.append([float(x) for x in vec] if vec else None)
            except Exception as exc:
                logger.warning("embedding failed for holder %s (%s); chunk indexed without a vector", self.holder_id, exc)
                out.append(None)
        return out

    async def _classify(self, title: str, text: str) -> dict[str, Any]:
        if self.router is not None:
            try:
                out = await self.router.run_task("classify_document", {"title": title, "text": text[:6000], "known_domains": list(self.domains)},
                                                 tenant_id=self.tenant_id)
                domains = [str(d) for d in (out.get("domains") or [])] or ["general"]
                return {"domains": domains, "summary": clip(normalize_ws(str(out.get("summary") or "")), 280),
                        "kind": str(out.get("kind") or "note")}
            except Exception as exc:
                logger.warning("classify_document via router failed (%s); using the rule", exc)
        return rule_classify_document(title, text, self.domains)

    async def _run_answer(self, question: str, evidence: list[dict[str, Any]], *, question_id: str, provenance: dict[str, Any],
                          force_rule: bool = False) -> dict[str, Any]:
        if self.router is not None and not force_rule:
            try:
                out = await self.router.run_task("answer_from_evidence", {"question": question, "evidence": evidence},
                                                 tenant_id=self.tenant_id, question_id=question_id)
                provenance["answer_method"] = "model"
                return dict(out)
            except Exception as exc:
                logger.warning("answer_from_evidence via router failed (%s); using the rule", exc)
        provenance["answer_method"] = "rule"
        return rule_answer_from_evidence(question, evidence)

    def _build_memories(self, doc: dict[str, Any], chunks: list[str], embeddings: list[list[float] | None], *, version: int,
                        extra_metadata: dict[str, Any] | None = None) -> list[Memory]:
        now = now_iso()
        observed = doc.get("observed_at") or now
        event_time = observed[:10] if doc.get("observed_at") else None
        subject = norm_entity(doc["title"]) or doc["doc_id"]
        out: list[Memory] = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            out.append(Memory(
                memory_id=new_id("mem"), text=chunk, kind="fact", subject=subject, subject_name=doc["title"], speaker=doc["title"],
                chat_id=doc["doc_id"], importance=0.6, confidence=0.9, event_time=event_time,
                event_time_precision="day" if event_time else "none", observed_at=observed, created_at=now, updated_at=now,
                text_hash=content_hash(chunk.lower()), embedding=emb,
                metadata={**(extra_metadata or {}), "doc_id": doc["doc_id"], "chunk_index": i, "source_root_id": doc["source_root_id"],
                          "domains": list(doc.get("domains") or []), "kind": doc.get("kind") or "note", "version": int(version),
                          "title": doc["title"], "source_kind": "document" if not extra_metadata else "record"},
            ))
        return out

    def _write_chunks_sync(self, c, doc: dict[str, Any], chunks: list[str], memories: list[Memory], *, version: int, speaker: str,
                           chat_id: str | None = None, extra_metadata: dict[str, Any] | None = None,
                           entities: Iterable[tuple[str, str, str]] | None = None) -> list[str]:
        """Chunk provenance messages (in ``chat_id``, default the document's own chat) and chunk memories. Returns the message ids."""
        subject = norm_entity(doc["title"]) or doc["doc_id"]
        seen_at = doc.get("observed_at") or now_iso()
        self.store._upsert_entity_sync(c, subject, doc["title"], "document", seen_at, mentions=len(chunks))
        entity_ids = [subject]
        for d in doc.get("domains") or []:
            key = norm_entity(str(d))
            if key and key not in entity_ids:
                self.store._upsert_entity_sync(c, key, str(d), "domain", seen_at, mentions=len(chunks))
                entity_ids.append(key)
        for eid, name, etype in entities or ():
            if eid and eid not in entity_ids:
                self.store._upsert_entity_sync(c, eid, name or eid, etype or "", seen_at, mentions=len(chunks))
                entity_ids.append(eid)
        chat = chat_id or doc["doc_id"]
        message_ids: list[str] = []
        for i, (chunk, mem) in enumerate(zip(chunks, memories)):
            message_id = f"msg_{content_hash(doc['doc_id'], str(version), str(i))}"
            # a conversation chat keeps its own (container) title; a document's own chat is titled by the document
            self.store._insert_message_sync(c, chat, speaker, chunk, sent_at=doc.get("observed_at"), message_id=message_id,
                                            metadata={**(extra_metadata or {}), "doc_id": doc["doc_id"], "chunk_index": i, "version": int(version)},
                                            title=None if chat_id else doc["title"], enqueue=self.extract)
            self.store._insert_memory_sync(c, mem, [message_id], entity_ids)
            message_ids.append(message_id)
        return message_ids

    def _public_document_sync(self, c, doc: dict[str, Any]) -> dict[str, Any]:
        chunks = int(c.execute("SELECT COUNT(*) AS n FROM memories WHERE chat_id=? AND status='active'", (doc["doc_id"],)).fetchone()["n"])
        return self._public_shape(doc, chunks)

    async def _public_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        return self._public_document_sync(self.store._conn, doc)

    def _public_shape(self, doc: dict[str, Any], chunks: int) -> dict[str, Any]:
        return {"doc_id": doc["doc_id"], "holder_id": self.holder_id, "title": doc["title"], "kind": doc.get("kind") or "note",
                "source_root_id": doc["source_root_id"], "origin_id": doc.get("origin_id"), "observed_at": doc.get("observed_at"),
                "status": doc.get("status") or "active", "version": int(doc.get("version") or 1), "chars": int(doc.get("chars") or 0),
                "chunks": chunks, "domains": list(doc.get("domains") or []), "summary": doc.get("summary") or "",
                "uploaded_by": doc.get("uploaded_by"), "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at")}


def _clean_text(text: Any) -> str:
    return (text if isinstance(text, str) else str(text or "")).replace("\r\n", "\n").strip()
