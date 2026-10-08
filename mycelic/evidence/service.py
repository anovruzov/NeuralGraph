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
"""
from __future__ import annotations

import inspect
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Iterable

from NeuralGraph.chat_memory.llm import fake_embedding
from NeuralGraph.chat_memory.models import Memory, RetrievedMemory, iso, new_id, now_iso, parse_iso
from NeuralGraph.chat_memory.retrieval import MemoryRetriever, RetrievalConfig
from NeuralGraph.chat_memory.textutil import clip, content_hash, fold, norm_entity, normalize_ws, stem, tokenize

from .. import __version__ as HOLDER_VERSION
from ..util import fingerprint
from .store import AlreadyProcessed, MycelicMemoryStore

logger = logging.getLogger(__name__)

RETRIEVAL_OPERATOR = "neuralgraph.hybrid"
DISCLOSURE_LEVELS = ("none", "summary", "excerpt")          # least to most disclosing
DEFAULT_EXPORT_POLICY: dict[str, Any] = {
    "disclosure": "excerpt", "max_excerpt_chars": 480, "answer_scopes": ["unit", "org"], "deny_patterns": [],
    "max_answer_chars": 1200,
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

    def __init__(self, path: str | Path, *, holder_id: str, tenant_id: str, llm: Any | None = None, router: Any | None = None,
                 export_policy: dict[str, Any] | None = None, domains: Iterable[str] = (), extract: bool = False,
                 retrieval: RetrievalConfig | None = None, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> None:
        self.path = str(path)
        self.holder_id = holder_id
        self.tenant_id = tenant_id
        self.llm = llm if llm is not None else HashEmbedder()
        self.router = router
        self.extract = bool(extract)
        self.chunk_chars = int(chunk_chars)
        self.store = MycelicMemoryStore(path)
        self.retriever = MemoryRetriever(self.store, self.llm, retrieval)
        self.export_policy: dict[str, Any] = {}
        self.domains: list[str] = []
        self._deny: list[re.Pattern[str]] = []
        self._policy_problems: list[str] = []
        self.update_policy(export_policy, list(domains))

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

    def describe(self) -> dict[str, Any]:
        return {"holder_id": self.holder_id, "tenant_id": self.tenant_id, "path": self.path, "export_policy": dict(self.export_policy),
                "domains": list(self.domains), "extract": self.extract, "holder_version": HOLDER_VERSION}

    async def close(self) -> None:
        await self.store.close()

    # ------------------------------------------------------------------ ingestion
    async def ingest_document(self, title: str, text: str, *, kind: str = "note", observed_at: str | None = None,
                              domains: Iterable[str] | None = None, origin_id: str | None = None, uploaded_by: str | None = None,
                              doc_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        """Store a document, chunk it, and index every chunk as a memory with provenance. Idempotent on ``doc_id``.

        Embeddings and classification (model calls) happen before the transaction; the transaction then writes the
        document, its version-1 row, the chunk messages, the memories, the entities and the last-ingest marker.
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
        root = fingerprint(origin_id) if origin_id else fingerprint(text)
        chunks = chunk_text(text, target=self.chunk_chars)
        embeddings = await self._embed_all(chunks)
        classified = await self._classify(title, text)
        doc_domains = [str(d) for d in domains] if domains else list(classified["domains"])
        if kind == "note" and classified.get("kind") == "conversation":
            kind = "conversation"
        doc = {"doc_id": doc_id, "title": title, "kind": kind or "note", "source_root_id": root, "origin_id": origin_id,
               "observed_at": observed, "domains": doc_domains, "summary": classified.get("summary") or "", "status": "active",
               "version": 1, "chars": len(text), "uploaded_by": uploaded_by, "created_at": now, "updated_at": now}
        memories = self._build_memories(doc, chunks, embeddings, version=1)
        speaker = uploaded_by or "document"

        def fn(c):
            again = self.store._get_document_sync(c, doc_id)
            if again is not None:
                return self._public_document_sync(c, again)
            self.store._insert_document_sync(c, doc, text)
            self.store._add_version_sync(c, doc_id, 1, text, title, observed, now, "ingest")
            self._write_chunks_sync(c, doc, chunks, memories, version=1, speaker=speaker)
            self.store._set_holder_meta_sync(c, "last_ingest_at", now)
            self.store._log_sync(c, "document.ingest", doc_id, {"title": title, "chunks": len(chunks), "source_root_id": root})
            return self._public_document_sync(c, doc)

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="ingest")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    async def revise_document(self, doc_id: str, text: str, *, title: str | None = None, observed_at: str | None = None,
                              reason: str = "", domains: Iterable[str] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        """New version: old chunk memories are superseded by the new ones (retracted where the new text has fewer
        chunks), the root is recomputed, and the references that pointed at old chunks are reported as
        ``affected_ref_ids`` so the coordinator can re-verify the claims that cite them."""
        doc = await self.store.get_document(doc_id, with_text=True)
        if doc is None:
            raise KeyError(doc_id)
        if doc["status"] == "retracted":
            raise ValueError("document is retracted")
        text = _clean_text(text)
        if not text:
            raise ValueError("document text is empty")
        new_title = normalize_ws(title) if title else doc["title"]
        observed = _norm_time(observed_at) if observed_at else doc.get("observed_at")
        now = now_iso()
        root = fingerprint(doc["origin_id"]) if doc.get("origin_id") else fingerprint(text)
        version = int(doc["version"]) + 1
        chunks = chunk_text(text, target=self.chunk_chars)
        embeddings = await self._embed_all(chunks)
        classified = await self._classify(new_title, text)
        doc_domains = [str(d) for d in domains] if domains else (doc.get("domains") or list(classified["domains"]))
        new_doc = {**doc, "title": new_title, "source_root_id": root, "observed_at": observed, "domains": doc_domains,
                   "summary": classified.get("summary") or doc.get("summary") or "", "status": "revised", "version": version,
                   "chars": len(text), "updated_at": now}
        new_doc.pop("text", None)
        memories = self._build_memories(new_doc, chunks, embeddings, version=version)
        speaker = doc.get("uploaded_by") or "document"

        def fn(c):
            current = self.store._get_document_sync(c, doc_id)
            if current is None:
                raise KeyError(doc_id)
            if current["status"] == "retracted":
                raise ValueError("document is retracted")
            old_ids = self.store._document_memory_ids_sync(c, doc_id, status="active")
            affected = [e["ref_id"] for e in self.store._exports_for_memories_sync(c, old_ids)]
            self.store._update_document_sync(c, doc_id, title=new_title, text=text, source_root_id=root, observed_at=observed,
                                             domains=doc_domains, summary=new_doc["summary"], status="revised", version=version,
                                             chars=len(text), updated_at=now)
            self.store._add_version_sync(c, doc_id, version, text, new_title, observed, now, reason)
            self._write_chunks_sync(c, new_doc, chunks, memories, version=version, speaker=speaker)
            for old, new in zip(old_ids, memories):
                self.store._supersede_sync(c, old, new.memory_id)
            self.store._retract_memories_sync(c, old_ids[len(memories):], f"revised: {reason}" if reason else "revised")
            self.store._log_sync(c, "document.revise", doc_id, {"version": version, "reason": reason, "affected_refs": len(affected),
                                                                 "source_root_id": root})
            return {"document": self._public_document_sync(c, new_doc), "affected_ref_ids": affected,
                    "previous_source_root_id": current["source_root_id"], "new_source_root_id": root, "reason": reason}

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="revise")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    async def retract_document(self, doc_id: str, reason: str = "", *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Retract every chunk memory and mark the document; reports every reference ever exported from it."""
        doc = await self.store.get_document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        now = now_iso()

        def fn(c):
            current = self.store._get_document_sync(c, doc_id)
            if current is None:
                raise KeyError(doc_id)
            ids = self.store._document_memory_ids_sync(c, doc_id, status=None)
            affected = [e["ref_id"] for e in self.store._exports_for_document_sync(c, doc_id)]
            if ids:
                self.store._retract_memories_sync(c, ids, reason or "retracted")
            if current["status"] != "retracted":
                self.store._update_document_sync(c, doc_id, status="retracted", updated_at=now)
                current = {**current, "status": "retracted", "updated_at": now}
                self.store._log_sync(c, "document.retract", doc_id, {"reason": reason, "affected_refs": len(affected)})
            return {"document": self._public_document_sync(c, current), "affected_ref_ids": affected, "reason": reason}

        try:
            return await self.store.run_in_tx(fn, idempotency_key=idempotency_key, op="retract")
        except AlreadyProcessed as exc:
            return exc.outcome.get("result") or {}

    # ------------------------------------------------------------------ reads
    async def document(self, doc_id: str) -> dict[str, Any] | None:
        doc = await self.store.get_document(doc_id)
        return await self._public_document(doc) if doc else None

    async def document_text(self, doc_id: str, version: int | None = None) -> str | None:
        if version is not None:
            return await self.store.document_version_text(doc_id, version)
        doc = await self.store.get_document(doc_id, with_text=True)
        return doc["text"] if doc else None

    async def list_documents(self, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return [await self._public_document(d) for d in await self.store.list_documents(status=status, limit=limit, offset=offset)]

    async def search(self, query: str, k: int = 10, **filters: Any) -> list[dict[str, Any]]:
        """Hybrid retrieval over this holder's memories (owner/API view: memory and document ids are visible here,
        because the owner may see their own store; the coordinator never calls this)."""
        return [self._result_dict(r) for r in await self._retrieve(query, k=k, **filters)]

    async def raw_for_ref(self, ref_id: str) -> dict[str, Any] | None:
        """Resolve an exported reference to its document. The coordinator calls this only after its own raw-grant
        check, and the holder service only for an envelope signed with its route key."""
        export = await self.store.get_export(ref_id)
        if export is None:
            return None
        doc = await self.store.get_document(export["doc_id"], with_text=True)
        if doc is None:
            return None
        memory = await self.store.get_memory(export["memory_id"])
        return {"ref_id": ref_id, "doc_id": doc["doc_id"], "title": doc["title"], "text": doc["text"], "observed_at": doc.get("observed_at"),
                "version": doc["version"], "status": doc["status"], "source_root_id": doc["source_root_id"], "kind": doc["kind"],
                "chunk_text": memory.text if memory else None, "question_id": export["question_id"]}

    async def stats(self) -> dict[str, Any]:
        docs = await self.store.document_counts()
        mem = await self.store.memory_counts()
        return {
            "holder_id": self.holder_id,
            "documents": sum(n for s, n in docs.items() if s != "retracted"),
            "documents_by_status": docs,
            "memories": int(mem["by_status"].get("active", 0)),
            "memories_by_status": mem["by_status"],
            "entities": await self.store.entity_count(),
            "queue": await self.store.pending_jobs(),
            "exports": await self.store.export_count(),
            "questions_answered": int(await self.store.get_holder_meta("questions_answered", "0") or 0),
            "last_ingest_at": await self.store.get_holder_meta("last_ingest_at"),
            "holder_version": HOLDER_VERSION,
        }

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
        results = await self._retrieve(text, k=RETRIEVAL_K, since=question.get("valid_from"), until=question.get("valid_to"))
        known = await self.store.exports_for_question(qid)
        items: list[dict[str, Any]] = []
        docs: dict[str, dict[str, Any] | None] = {}
        channels: set[str] = set()
        for r in results:
            m = r.memory
            doc_id = m.metadata.get("doc_id") or m.chat_id
            if doc_id not in docs:
                docs[doc_id] = await self.store.get_document(doc_id)
            doc = docs[doc_id]
            if doc is None or doc["status"] == "retracted":
                continue
            channels.update(ch for ch in ("vector", "keyword", "graph") if ch in r.channels)
            # the model (holder-side) reads the whole redacted chunk; the excerpt cap bounds what is *disclosed*
            redacted = redact(m.text, self._deny)
            excerpt = clip(redacted, max_excerpt) if max_excerpt else ""
            disclosed = {"excerpt": excerpt, "summary": clip(redact(doc.get("summary") or "", self._deny), max_excerpt or 140), "none": ""}[level]
            items.append({
                "ref_id": (known.get(m.memory_id) or {}).get("ref_id") or new_ref_id(),
                "memory_id": m.memory_id, "doc": doc, "model_text": redacted, "disclosed": disclosed,
                "observed_at": m.observed_at, "kind": doc["kind"], "title": self._disclosed_title(doc, level),
            })
        provenance["channels"] = sorted(channels)
        provenance["memory_count"] = len(items)
        if not items:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key)

        model_out = await self._run_answer(text, [{"ref_id": i["ref_id"], "excerpt": i["model_text"], "observed_at": i["observed_at"]} for i in items],
                                           question_id=qid, provenance=provenance)
        used_ids = [str(x) for x in (model_out.get("used_ref_ids") or [])]
        used = [i for i in items if i["ref_id"] in set(used_ids)]
        answer = normalize_ws(str(model_out.get("answer") or ""))
        if model_out.get("no_evidence") or not used or not answer:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key)
        max_answer = int(self.export_policy["max_answer_chars"])
        content = clip(redact(answer, self._deny), max_answer) if max_answer else redact(answer, self._deny)
        refs = [{
            "ref_id": i["ref_id"], "source_root_id": i["doc"]["source_root_id"], "root_known": bool(i["doc"]["source_root_id"]),
            "kind": i["kind"], "title": i["title"], "disclosed_excerpt": i["disclosed"], "disclosure_level": level,
            # evidence is as fresh as the time its content was observed, not the time it was uploaded or indexed
            "observed_at": i["observed_at"], "freshness_at": i["observed_at"] or i["doc"].get("observed_at"),
        } for i in used]
        try:
            confidence = max(0.0, min(1.0, float(model_out.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.5
        response = {**base, "status": "answered", "content": content, "confidence": round(confidence, 3), "evidence_refs": refs,
                    "provenance": provenance, "freshness_at": max((r["freshness_at"] for r in refs if r["freshness_at"]), default=None)}
        exports = [{"ref_id": i["ref_id"], "memory_id": i["memory_id"], "doc_id": i["doc"]["doc_id"], "question_id": qid,
                    "disclosed_excerpt": i["disclosed"], "disclosure_level": level} for i in used]
        return await self._commit_answer(exports, response, idempotency_key)

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
        refs: list[dict[str, Any]] = []
        exports: list[dict[str, Any]] = []
        for doc_id in ids:
            doc = await self.store.get_document(doc_id, with_text=True)
            if doc is None:
                raise KeyError(doc_id)
            memory_ids = await self.store.document_memory_ids(doc_id)
            if doc["status"] == "retracted" or not memory_ids:
                continue
            memory_id = memory_ids[0]          # the document's first chunk anchors the reference in the export ledger
            excerpt = clip(redact(doc["text"], self._deny), max_excerpt) if max_excerpt else ""
            disclosed = {"excerpt": excerpt, "summary": clip(redact(doc.get("summary") or "", self._deny), max_excerpt or 140), "none": ""}[level]
            ref_id = (known.get(memory_id) or {}).get("ref_id") or new_ref_id()
            refs.append({"ref_id": ref_id, "source_root_id": doc["source_root_id"], "root_known": bool(doc["source_root_id"]), "kind": doc["kind"],
                         "title": self._disclosed_title(doc, level), "disclosed_excerpt": disclosed, "disclosure_level": level, "observed_at": doc.get("observed_at"),
                         "freshness_at": doc.get("observed_at")})
            exports.append({"ref_id": ref_id, "memory_id": memory_id, "doc_id": doc_id, "question_id": qid, "disclosed_excerpt": disclosed,
                            "disclosure_level": level})
        provenance["memory_count"] = len(refs)
        if not content:
            response = {**base, "status": "no_evidence", "content": "", "confidence": 0.0, "evidence_refs": [], "provenance": provenance,
                        "freshness_at": None}
            return await self._commit_answer([], response, idempotency_key, op="manual_response")
        max_answer = int(self.export_policy["max_answer_chars"])
        response = {**base, "status": "answered", "content": clip(redact(content, self._deny), max_answer) if max_answer else redact(content, self._deny),
                    "confidence": 0.8, "evidence_refs": refs, "provenance": provenance,
                    "freshness_at": max((r["freshness_at"] for r in refs if r["freshness_at"]), default=None)}
        return await self._commit_answer(exports, response, idempotency_key, op="manual_response")

    # ------------------------------------------------------------------ internals
    def _disclosed_title(self, doc: dict[str, Any], level: str) -> str:
        """Titles are disclosure too: redacted like the text, and replaced by the document kind when nothing may be disclosed."""
        if level == "none":
            return str(doc.get("kind") or "document")
        return redact(str(doc.get("title") or ""), self._deny)

    async def _commit_answer(self, exports: list[dict[str, Any]], response: dict[str, Any], idempotency_key: str | None,
                             op: str = "question") -> dict[str, Any]:
        """Exports (the only state a question leaves behind) and the idempotency marker land in one transaction."""
        if not exports and not idempotency_key:
            return response

        def fn(c):
            for e in exports:
                # the answer was composed outside this transaction: if a cited chunk was superseded or its document retracted
                # meanwhile, the revise/retract could not have reported this export, so the whole answer is redone instead
                m = c.execute("SELECT status FROM memories WHERE memory_id=?", (e["memory_id"],)).fetchone()
                d = c.execute("SELECT status FROM documents WHERE doc_id=?", (e["doc_id"],)).fetchone()
                if m is None or m["status"] != "active" or d is None or d["status"] == "retracted":
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
        mine = set(self.domains)
        wanted = {str(d) for d in (question.get("candidate_domains") or [])}
        if mine and wanted and "*" not in mine and "*" not in wanted and not (mine & wanted):
            return "no matching evidence domain"
        return None

    async def _retrieve(self, query: str, *, k: int = 10, since: str | None = None, until: str | None = None,
                        **filters: Any) -> list[RetrievedMemory]:
        """Active chunk memories only: a time window makes the retriever include superseded rows (they were true at
        the time), but evidence must cite the current version so a reference is not stale on arrival."""
        query = normalize_ws(query)
        if not query:
            return []
        since_iso, until_iso = _norm_time(since), _norm_time(until)
        try:
            results = await self.retriever.search(query, k=max(1, k) * 2, since=since_iso, until=until_iso, **filters)
        except Exception:
            logger.exception("retrieval failed for holder %s", self.holder_id)
            return []
        return [r for r in results if r.memory.status == "active"][:k]

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

    async def _run_answer(self, question: str, evidence: list[dict[str, Any]], *, question_id: str, provenance: dict[str, Any]) -> dict[str, Any]:
        if self.router is not None:
            try:
                out = await self.router.run_task("answer_from_evidence", {"question": question, "evidence": evidence},
                                                 tenant_id=self.tenant_id, question_id=question_id)
                provenance["answer_method"] = "model"
                return dict(out)
            except Exception as exc:
                logger.warning("answer_from_evidence via router failed (%s); using the rule", exc)
        provenance["answer_method"] = "rule"
        return rule_answer_from_evidence(question, evidence)

    def _build_memories(self, doc: dict[str, Any], chunks: list[str], embeddings: list[list[float] | None], *, version: int) -> list[Memory]:
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
                metadata={"doc_id": doc["doc_id"], "chunk_index": i, "source_root_id": doc["source_root_id"],
                          "domains": list(doc.get("domains") or []), "kind": doc.get("kind") or "note", "version": int(version),
                          "title": doc["title"], "source_kind": "document"},
            ))
        return out

    def _write_chunks_sync(self, c, doc: dict[str, Any], chunks: list[str], memories: list[Memory], *, version: int, speaker: str) -> None:
        subject = norm_entity(doc["title"]) or doc["doc_id"]
        seen_at = doc.get("observed_at") or now_iso()
        self.store._upsert_entity_sync(c, subject, doc["title"], "document", seen_at, mentions=len(chunks))
        entity_ids = [subject]
        for d in doc.get("domains") or []:
            key = norm_entity(str(d))
            if key and key not in entity_ids:
                self.store._upsert_entity_sync(c, key, str(d), "domain", seen_at, mentions=len(chunks))
                entity_ids.append(key)
        for i, (chunk, mem) in enumerate(zip(chunks, memories)):
            message_id = f"msg_{content_hash(doc['doc_id'], str(version), str(i))}"
            self.store._insert_message_sync(c, doc["doc_id"], speaker, chunk, sent_at=doc.get("observed_at"), message_id=message_id,
                                            metadata={"doc_id": doc["doc_id"], "chunk_index": i, "version": int(version)},
                                            title=doc["title"], enqueue=self.extract)
            self.store._insert_memory_sync(c, mem, [message_id], entity_ids)

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
