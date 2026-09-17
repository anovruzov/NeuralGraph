"""Selective memory + relationship extraction with a small instruction model (Qwen-7B class).

Pipeline for one batch of consecutive messages from one chat (see :class:`MemoryExtractor.build_plan`):

1. **Gate** — messages that are almost certainly chit-chat (``textutil.is_probable_chitchat``) are marked
   skipped without any LLM call.
2. **Extract** — two LLM calls run concurrently: ``MEMORY_PROMPT`` (self-contained third-person memories
   with subject/kind/when/importance/confidence/sources/entities) and ``RELATION_PROMPT`` (entities and
   typed triples, alias-resolved against the cross-chat entity registry).
3. **Normalise** — names resolve through :class:`EntityRegistry` (speakers, aliases, known entities,
   prefix/fuzzy match); ``when`` becomes an ISO ``event_time`` + precision; weak candidates are dropped.
4. **Reconcile** — every surviving candidate is embedded and compared with existing active memories of the
   same subject: cosine >= ``dedupe_cosine`` is a duplicate (no LLM); cosine >= ``reconcile_cosine`` asks the
   LLM (``RECONCILE_PROMPT``) to choose ADD / DUPLICATE / UPDATE / CONTRADICT; otherwise ADD.
5. **Plan** — the result is an :class:`ExtractionPlan` that the store applies in one transaction.

Nothing here writes to the store; that keeps retries idempotent.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .jsonutil import as_float, as_list, as_str, parse_json_object
from .llm import LLMClient
from .models import MEMORY_KINDS, ChatMessage, Memory, iso, new_id, now_iso, parse_iso
from .store import ChatMemoryStore, ExtractionPlan
from .textutil import clip, content_hash, is_probable_chitchat, norm_entity, norm_relation, normalize_ws, tokenize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------------------------

MEMORY_PROMPT = """You maintain the long-term memory of an AI assistant across many separate chats.
Read the NEW MESSAGES and extract only what is worth remembering in FUTURE conversations. Use CONTEXT only to resolve references.

KEEP (durable, specific, useful later): identity details (name, location, job, family, age), stable facts, preferences and habits, dislikes, important events with dates, plans and commitments, relationships between people, health, goals, decisions, projects, possessions, strong opinions.
SKIP: greetings, small talk, thanks, jokes, questions that carry no information, generic advice or explanations from the assistant, hypotheticals, momentary states ("I'm tired right now"), anything already in KNOWN MEMORIES unless the new message adds a concrete detail.
Never store facts about the assistant itself.

RULES:
1. One memory = ONE self-contained sentence in the third person with full names. Never use "I", "you", "he", "she", "they" as the subject. Resolve nicknames and pronouns with KNOWN ENTITIES and the speaker names.
2. "subject" = the person (or thing) the memory is about, as a full name. "kind" = one of {kinds}.
3. Resolve relative time with the message date: "yesterday" said on 2026-09-17 is 2026-09-16. "when" = the date the memory is ABOUT as YYYY-MM-DD, YYYY-MM or YYYY; null when the memory is not tied to a date.
4. importance 0-1: 0.9 identity and major life events, 0.7 preferences, plans, relationships, health; 0.5 minor facts; 0.3 trivia.
5. confidence 0-1: 1.0 stated explicitly, 0.6 clearly implied, lower if uncertain. Do not invent details.
6. "sources" = indices of the NEW MESSAGES that support the memory.
7. "entities" = people, places, organisations, pets, products, projects, events named in the memory: short canonical names with a type from {{person, place, org, pet, product, project, event, other}}.
8. Messages marked (assistant) are reference only and can never be a source; only what the human participants say counts.
9. At most {max_memories} memories; fewer is better than padding. If nothing qualifies return {{"memories": []}}.

CHAT PARTICIPANTS: {speakers}
KNOWN ENTITIES: {registry}
KNOWN MEMORIES (already stored, do not repeat):
{known_memories}

CONTEXT (earlier messages, for reference only):
{context}

NEW MESSAGES (date: {date}):
{messages}

Return ONLY JSON:
{{"memories": [{{"text": "...", "kind": "fact", "subject": "Full Name", "when": null, "importance": 0.7, "confidence": 0.9, "sources": [1], "entities": [{{"name": "...", "type": "..."}}]}}]}}"""

RELATION_PROMPT = """You are building a knowledge graph from a chat between {speakers}.
From the NEW MESSAGES extract entities and [subject, relation, object] facts that are stated or clearly implied. Use CONTEXT only to resolve references.

RULES:
1. Entities: people, places, organisations, pets, products, projects, events, activities, media titles, jobs, health conditions. Short canonical names (lowercase is fine), singular. Resolve "I", "my", "me" to the speaker of that message. Reuse KNOWN ENTITIES names exactly when they refer to the same thing (nicknames, first names, pronouns).
2. Relation: a short snake_case verb phrase such as lives_in, works_at, works_as, has_pet, married_to, sibling_of, parent_of, friend_of, likes, dislikes, owns, uses, studied_at, plans_to_visit, moved_from, diagnosed_with, member_of, born_in, plays.
3. Skip greetings, small talk and generic assistant advice. Never invent relations. Never make the assistant a subject.
4. "source" = index of the NEW MESSAGE that supports the triple; "confidence" 0-1.
5. At most {max_triples} triples.

KNOWN ENTITIES: {registry}

CONTEXT (earlier messages):
{context}

NEW MESSAGES (date: {date}):
{messages}

Return ONLY JSON:
{{"entities": [{{"name": "...", "type": "person"}}], "triples": [{{"s": "...", "r": "...", "o": "...", "source": 1, "confidence": 0.9}}]}}"""

RECONCILE_PROMPT = """You maintain a memory store about people. A NEW memory was just extracted. Compare it with the EXISTING memories about the same subject and decide what to do.

NEW (observed {new_date}): "{new_text}"

EXISTING:
{existing}

Decide exactly one:
- DUPLICATE: NEW adds nothing beyond one existing memory. target = its letter.
- UPDATE: NEW refines, extends or corrects the same fact as one existing memory. target = its letter; "text" = ONE merged sentence in third person that keeps every detail that is still true.
- CONTRADICT: NEW conflicts with one existing memory (the old fact is no longer true, e.g. moved, changed job, broke up). target = its letter.
- ADD: NEW is about something different from all existing memories. target = null.

Return ONLY JSON: {{"decision": "ADD", "target": null, "text": null, "reason": "short"}}"""

_TYPE_MAP = {
    "person": "person", "people": "person", "human": "person", "user": "person", "friend": "person", "family": "person",
    "place": "place", "location": "place", "city": "place", "country": "place", "address": "place", "venue": "place",
    "org": "org", "organisation": "org", "organization": "org", "company": "org", "employer": "org", "school": "org",
    "university": "org", "team": "org", "band": "org",
    "pet": "pet", "animal": "pet", "dog": "pet", "cat": "pet",
    "product": "product", "device": "product", "software": "product", "tool": "product", "app": "product", "brand": "product",
    "project": "project", "event": "event", "activity": "event", "trip": "event", "hobby": "other", "job": "other",
    "media": "product", "book": "product", "movie": "product", "film": "product", "game": "product", "show": "product",
    "health": "other", "condition": "other", "date": "other", "time": "other",
}

_FIRST_PERSON = {"i", "me", "my", "myself", "mine", "the user", "user", "speaker"}
_ANNOTATION_RE = re.compile(r"\s*\[=\s*[^\]]*\]")
# mentions that are never entities on their own (dates, generic words the model likes to tag)
_BLOCKED_ENTITY_KEYS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "yesterday",
    "tomorrow", "week", "month", "year", "weekend", "morning", "evening", "night", "afternoon", "spring", "summer",
    "autumn", "fall", "winter", "update", "thing", "things", "stuff", "someone", "something", "everyone", "nothing",
    "none", "null", "unknown", "n a", "user", "assistant", "ai", "bot", "system", "chat", "message", "conversation",
}
_COMMON_CAPS = {
    "the", "she", "they", "his", "her", "their", "this", "that", "there", "these", "those", "when", "where", "what", "who",
    "how", "after", "before", "since", "during", "every", "each", "both", "also", "then", "now", "however", "although",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "new", "one", "two",
    "three", "four", "five", "six", "seven", "eight", "nine", "ten", "christmas", "easter", "monday", "english",
    "spanish", "french", "german", "italian", "chinese", "japanese", "covid", "internet", "instagram", "youtube",
}
_GENERIC_SPEAKERS = {"user", "assistant", "system", "ai", "bot", "human", "you", "me", "unknown"}


def norm_type(t: Any) -> str:
    key = norm_entity(as_str(t, 40))
    if not key:
        return ""
    return _TYPE_MAP.get(key, _TYPE_MAP.get(key.split()[0], "other" if key else ""))


# ---------------------------------------------------------------------------------------------
# Entity registry (alias resolution across chats)
# ---------------------------------------------------------------------------------------------


class EntityRegistry:
    """In-memory view of known entities for one extraction pass.

    Built from the chat participants, the globally most-mentioned entities, this chat's entities and
    all aliases. ``resolve`` maps a raw mention to a canonical ``entity_id`` (new ids are returned
    for genuinely new entities and recorded so that the same batch reuses them).
    """

    def __init__(self, *, fuzzy_cutoff: float = 0.9) -> None:
        self.entries: dict[str, dict[str, Any]] = {}   # entity_id -> {name, type, count}
        self.aliases: dict[str, str] = {}              # alias key -> entity_id
        self.fuzzy_cutoff = fuzzy_cutoff
        self.new_entities: dict[str, dict[str, Any]] = {}

    def add(self, entity_id: str, name: str, etype: str = "", count: int = 0, aliases: Iterable[str] = ()) -> None:
        if not entity_id:
            return
        cur = self.entries.get(entity_id)
        if cur is None:
            self.entries[entity_id] = {"name": name or entity_id, "type": etype or "", "count": int(count)}
        else:
            cur["count"] = max(cur["count"], int(count))
            if not cur["type"] and etype:
                cur["type"] = etype
        for a in aliases:
            k = norm_entity(a)
            if k and k != entity_id:
                self.aliases.setdefault(k, entity_id)

    @classmethod
    async def load(cls, store: ChatMemoryStore, chat_id: str, speakers: Iterable[str], *, limit: int = 120,
                   speaker_names: dict[str, str] | None = None) -> "EntityRegistry":
        reg = cls()
        speaker_names = speaker_names or {}
        for sp in speakers:
            display = speaker_names.get(sp, sp)
            key = norm_entity(display)
            if key:
                reg.add(key, display, "person", 1, aliases=[sp] if sp != display else [])
        for e in await store.top_entities(limit):
            reg.add(e.entity_id, e.name, e.type, e.mention_count)
        for e in await store.entities_for_chat(chat_id, 60):
            reg.add(e.entity_id, e.name, e.type, e.mention_count)
        reg.aliases.update({k: v for k, v in (await store.all_entity_keys()).items() if k != v})
        return reg

    def prompt_text(self, limit: int = 80) -> str:
        top = sorted(self.entries.items(), key=lambda kv: -kv[1]["count"])[:limit]
        if not top:
            return "(none yet)"
        return ", ".join(f"{v['name']} ({v['type']})" if v["type"] else v["name"] for _, v in top)

    def display(self, entity_id: str) -> str:
        e = self.entries.get(entity_id) or self.new_entities.get(entity_id)
        return e["name"] if e else entity_id

    def type_of(self, entity_id: str) -> str:
        e = self.entries.get(entity_id) or self.new_entities.get(entity_id)
        return e["type"] if e else ""

    def resolve(self, mention: Any, *, speaker_id: str | None = None, etype: str = "", create: bool = True) -> str | None:
        """Map a mention to an entity id. Returns None for empty/unusable mentions."""
        raw = as_str(mention, 80)
        key = norm_entity(raw)
        if not key:
            return None
        if key in _FIRST_PERSON:
            return speaker_id
        if key in _BLOCKED_ENTITY_KEYS or re.fullmatch(r"[\d\s'\-]+", key) or len(key) < 2:
            return None
        if key in self.entries or key in self.new_entities:
            return key
        if key in self.aliases:
            return self.aliases[key]
        # "melanie's" / "dr. smith" style variants are handled by norm_entity; now try structural matches
        people = [eid for eid, v in self.entries.items() if v["type"] == "person"]
        # first-name / prefix match against people (either direction, min 3 chars, unambiguous)
        if len(key) >= 3:
            cands = [eid for eid in people if eid.startswith(key + " ") or key.startswith(eid + " ")]
            if len(cands) == 1:
                return cands[0]
            # a bare first name that matches exactly one person's first token
            cands = [eid for eid in people if eid.split()[0] == key]
            if len(cands) == 1:
                return cands[0]
        if len(key) >= 5:
            pool = list(self.entries.keys()) + list(self.new_entities.keys()) + list(self.aliases.keys())
            best = difflib.get_close_matches(key, pool, n=1, cutoff=self.fuzzy_cutoff)
            if best:
                return self.aliases.get(best[0], best[0])
        if not create:
            return None
        self.new_entities[key] = {"name": raw.strip() or key, "type": norm_type(etype), "count": 0}
        return key


# ---------------------------------------------------------------------------------------------
# Temporal normalisation of the "when" field
# ---------------------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def parse_when(when: Any, observed_at: str | None) -> tuple[str | None, str]:
    """Turn a model-supplied ``when`` into (ISO event_time, precision) or (None, 'none')."""
    s = as_str(when, 60).strip().strip('"').lower()
    if not s or s in ("null", "none", "unknown", "n/a", "na", "-"):
        return None, "none"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[t ].*)?$", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d"), "day"
        except ValueError:
            return None, "none"
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m and 1 <= int(m[2]) <= 12:
        return f"{int(m[1]):04d}-{int(m[2]):02d}", "month"
    m = re.match(r"^(\d{4})$", s)
    if m and 1900 <= int(m[1]) <= 2100:
        return m[1], "year"
    m = re.match(r"^([a-z]+)\.?\s+(\d{4})$", s)          # "march 2024"
    if m and m[1] in _MONTHS:
        return f"{int(m[2]):04d}-{_MONTHS[m[1]]:02d}", "month"
    m = re.match(r"^(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})$", s)   # "8 may 2023"
    if m and m[2] in _MONTHS:
        try:
            return datetime(int(m[3]), _MONTHS[m[2]], int(m[1])).strftime("%Y-%m-%d"), "day"
        except ValueError:
            return None, "none"
    m = re.match(r"^([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$", s)   # "may 8, 2023"
    if m and m[1] in _MONTHS:
        try:
            return datetime(int(m[3]), _MONTHS[m[1]], int(m[2])).strftime("%Y-%m-%d"), "day"
        except ValueError:
            return None, "none"
    # relative phrases: resolve with the repo's temporal utilities against the message date
    base = parse_iso(observed_at)
    if base is not None:
        try:
            from ..temporal_utils import parse_datetime_flexible, resolve_relative_dates

            _, resolved = resolve_relative_dates(s, base.replace(tzinfo=None))
            vals = [v for v in resolved.values() if isinstance(v, str) and re.search(r"\d", v)]
            if len(vals) == 1:
                dt = parse_datetime_flexible(vals[0])
                if dt is not None:
                    return dt.strftime("%Y-%m-%d"), "day"
                ym = re.search(r"([a-z]+)\s+(\d{4})", vals[0].lower())
                if ym and ym[1] in _MONTHS:
                    return f"{int(ym[2]):04d}-{_MONTHS[ym[1]]:02d}", "month"
            m2 = re.match(r"^(\d{1,2})\s+([a-z]+)\s+(\d{4})$", s)
            if m2:
                dt = parse_datetime_flexible(s)
                if dt is not None:
                    return dt.strftime("%Y-%m-%d"), "day"
        except Exception:  # pragma: no cover - defensive, temporal utils are regex based
            pass
    return None, "none"


# ---------------------------------------------------------------------------------------------
# Candidates and configuration
# ---------------------------------------------------------------------------------------------


@dataclass
class CandidateMemory:
    text: str
    kind: str
    subject_raw: str
    importance: float
    confidence: float
    when_raw: str
    source_indices: list[int]
    entities: list[tuple[str, str]]          # (name, type)
    # filled during normalisation
    subject_id: str = ""
    subject_name: str = ""
    speaker: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    observed_at: str = ""
    event_time: str | None = None
    event_time_precision: str = "none"
    entity_ids: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    text_hash: str = ""


@dataclass
class ExtractionConfig:
    context_messages: int = 6
    max_message_chars: int = 1500
    max_memories_per_batch: int = 8
    max_triples_per_batch: int = 12
    registry_prompt_entities: int = 80
    known_memories_in_prompt: int = 12
    min_importance: float = 0.3
    min_confidence: float = 0.45
    dedupe_cosine: float = 0.94
    reconcile_cosine: float = 0.62
    related_cosine: float = 0.55
    reconcile_candidates: int = 4
    max_related_links: int = 3
    extract_relations: bool = True
    gate_chitchat: bool = True
    generate_max_tokens: int = 2000        # 8 memories x ~150 tokens of JSON fits comfortably
    reconcile_max_tokens: int = 220
    llm_timeout: float = 120.0
    speaker_names: dict[str, str] = field(default_factory=dict)   # e.g. {"user": "Ali Novruzov"}
    ignore_speakers: tuple[str, ...] = ("assistant", "system")     # never the *subject* of a memory
    extract_assistant: bool = False        # assistant/system turns are context only, never a memory source
    annotate_relative_dates: bool = True   # "yesterday [= 7 May 2023]" so the model copies absolute dates
    min_grounding_overlap: float = 0.2     # share of a memory's content tokens that must appear in the window
    require_name_grounding: bool = True    # capitalised names in a memory must occur in the window/registry


def _cos(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _annotate_dates(text: str, sent_at: str | None) -> str:
    base = parse_iso(sent_at)
    if base is None:
        return text
    try:
        from ..temporal_utils import resolve_relative_dates

        annotated, _ = resolve_relative_dates(text, base.replace(tzinfo=None))
        return annotated
    except Exception:  # pragma: no cover - defensive
        return text


def _fmt_messages(msgs: list[ChatMessage], names: dict[str, str], max_chars: int, numbered: bool,
                  assistant_roles: tuple[str, ...] = (), annotate: bool = False) -> str:
    lines = []
    for i, m in enumerate(msgs, 1):
        who = names.get(m.speaker, m.speaker)
        date = (m.sent_at or "")[:10]
        prefix = f"{i}. " if numbered else ""
        stamp = f" ({date})" if date and not numbered else ""
        tag = " (assistant)" if (m.role or m.speaker).lower() in assistant_roles else ""
        body = clip(normalize_ws(m.text), max_chars)
        if annotate:
            body = _annotate_dates(body, m.sent_at)
        lines.append(f"{prefix}[{who}{tag}]{stamp}: {body}")
    return "\n".join(lines) if lines else "(none)"


class UnparseableOutput(RuntimeError):
    """The model returned no JSON object at all; the batch is retried (with variation) rather than dropped."""


_REPAIR_SUFFIX = ("\n\nIMPORTANT: your previous answer to this request was not valid JSON. Return ONLY the JSON object, "
                  "starting with {{ and ending with }}, with no prose before or after it.")


class MemoryExtractor:
    """Turns a batch of messages into an :class:`ExtractionPlan` using the LLM. Read-only w.r.t. the store."""

    def __init__(self, store: ChatMemoryStore, llm: LLMClient, config: ExtractionConfig | None = None) -> None:
        self.store = store
        self.llm = llm
        self.config = config or ExtractionConfig()
        self._pending_audit: list[tuple[str, str | None, dict[str, Any]]] = []

    # ------------------------------------------------------------------ public entry
    async def build_plan(self, batch: list[ChatMessage], job_ids: Iterable[int] = (), *, attempt: int = 1) -> ExtractionPlan:
        """``attempt`` (1-based) comes from the job queue: retries get a repair instruction and more output room,
        so a failed prompt is never re-sent byte-identical at temperature 0."""
        cfg = self.config
        assert batch, "empty batch"
        chat_id = batch[0].chat_id
        plan = ExtractionPlan(chat_id, job_ids)
        names = dict(cfg.speaker_names)

        worthy: list[ChatMessage] = []          # everything the model sees as NEW MESSAGES (incl. assistant turns)
        for m in batch:
            if cfg.gate_chitchat and is_probable_chitchat(m.text):
                plan.skipped_message_ids.append(m.message_id)
                plan.audit.append(("gate_skip", m.message_id, {"reason": "chitchat", "text": clip(m.text, 80)}))
            else:
                worthy.append(m)
        targets = [m for m in worthy if cfg.extract_assistant or not self._is_assistant(m)]
        if not targets:
            # only assistant/system turns (or nothing) left: no LLM call, keep them as processed context
            plan.processed_message_ids.extend(m.message_id for m in worthy)
            if worthy:
                plan.audit.append(("gate_skip", chat_id, {"reason": "assistant_only", "messages": len(worthy)}))
            return plan

        context = await self.store.context_before(chat_id, worthy[0].seq, cfg.context_messages)
        speakers = list(dict.fromkeys([*(m.speaker for m in context), *(m.speaker for m in batch)]))
        registry = await EntityRegistry.load(self.store, chat_id, speakers, speaker_names=names)
        speaker_ids = {sp: norm_entity(names.get(sp, sp)) for sp in speakers}
        human_speakers = [sp for sp in speakers if sp.lower() not in cfg.ignore_speakers]

        known = await self._known_memories(speaker_ids, human_speakers)
        date = (worthy[-1].sent_at or worthy[0].sent_at or now_iso())[:10]
        roles = tuple(r.lower() for r in cfg.ignore_speakers)
        ctx_txt = _fmt_messages(context, names, cfg.max_message_chars, numbered=False, assistant_roles=roles)
        new_txt = _fmt_messages(worthy, names, cfg.max_message_chars, numbered=True, assistant_roles=roles,
                                annotate=cfg.annotate_relative_dates)
        repair = _REPAIR_SUFFIX.format() if attempt > 1 else ""
        max_tokens = int(cfg.generate_max_tokens * (1.0 + 0.5 * min(3, attempt - 1)))
        mem_prompt = MEMORY_PROMPT.format(
            kinds=", ".join(MEMORY_KINDS), max_memories=cfg.max_memories_per_batch,
            speakers=", ".join(names.get(s, s) for s in speakers) or "unknown",
            registry=registry.prompt_text(cfg.registry_prompt_entities),
            known_memories="\n".join(f"- {k}" for k in known) or "(none)",
            context=ctx_txt, messages=new_txt, date=date,
        ) + repair
        tasks = [self.llm.generate(mem_prompt, max_tokens=max_tokens, timeout=cfg.llm_timeout)]
        if cfg.extract_relations:
            rel_prompt = RELATION_PROMPT.format(
                speakers=" and ".join(names.get(s, s) for s in speakers) or "unknown", max_triples=cfg.max_triples_per_batch,
                registry=registry.prompt_text(cfg.registry_prompt_entities),
                context=ctx_txt, messages=new_txt, date=date,
            ) + repair
            tasks.append(self.llm.generate(rel_prompt, max_tokens=max_tokens, timeout=cfg.llm_timeout))
        outs = await asyncio.gather(*tasks)
        mem_out = outs[0]
        rel_out = outs[1] if len(outs) > 1 else ""

        if parse_json_object(mem_out) is None:
            # no JSON at all (prose, refusal, empty): let the queue retry with variation instead of losing the facts
            raise UnparseableOutput(f"memory extraction returned no JSON (attempt {attempt}): {clip(mem_out, 160)!r}")
        candidates = self._parse_memories(mem_out, worthy)
        self._pending_audit = []
        candidates = self._normalise(candidates, worthy, registry, speaker_ids, names)
        plan.audit.extend(self._pending_audit)
        self._pending_audit = []
        candidates = self._ground(candidates, worthy, context, registry, names, plan)
        plan.audit.append(("extract", chat_id, {"messages": [m.message_id for m in worthy], "raw_candidates": len(candidates)}))

        # relations (independent of memory reconciliation; provenance attached below)
        rel_entities, triples = self._parse_relations(rel_out, worthy, registry, speaker_ids) if rel_out else ([], [])

        # reconcile candidates against the existing store
        await self._reconcile(candidates, plan, worthy)

        # entities: everything referenced by kept memories + relation endpoints
        seen_at = worthy[-1].sent_at or now_iso()
        ent_mentions: dict[str, int] = {}
        for mem, _, ent_ids in plan.new_memories:
            for e in ent_ids:
                ent_mentions[e] = ent_mentions.get(e, 0) + 1
        for s, _, o, *_ in triples:
            for e in (s, o):
                ent_mentions[e] = ent_mentions.get(e, 0) + 1
        for e in rel_entities:
            ent_mentions.setdefault(e, 0)
        for eid, n in ent_mentions.items():
            plan.entities.append({"entity_id": eid, "name": registry.display(eid), "type": registry.type_of(eid),
                                  "seen_at": seen_at, "mentions": max(1, n)})
        # attach relation provenance: a memory that mentions both endpoints from the same source message
        msg_time = {m.message_id: (m.sent_at or m.ingested_at) for m in worthy}
        for s, p, o, conf, msg_id in triples:
            mem_id = None
            for mem, srcs, ent_ids in plan.new_memories:
                if s in ent_ids and o in ent_ids and (msg_id in srcs or not msg_id):
                    mem_id = mem.memory_id
                    break
            plan.relations.append((s, p, o, conf, mem_id, msg_id, msg_time.get(msg_id)))

        plan.processed_message_ids.extend(m.message_id for m in worthy)
        return plan

    # ------------------------------------------------------------------ helpers
    def _is_assistant(self, m: ChatMessage) -> bool:
        roles = tuple(r.lower() for r in self.config.ignore_speakers)
        return (m.role or "").lower() in roles or m.speaker.lower() in roles

    def _ground(self, cands: list[CandidateMemory], worthy: list[ChatMessage], context: list[ChatMessage],
                registry: EntityRegistry, names: dict[str, str], plan: ExtractionPlan) -> list[CandidateMemory]:
        """Drop candidates the window does not support: assistant-only sources, hallucinated names, no overlap."""
        cfg = self.config
        window_text = " ".join(m.text for m in worthy + context)
        window_tokens = set(tokenize(window_text))
        window_lower = " " + norm_entity(window_text) + " "
        known_names = set()
        for eid, v in list(registry.entries.items()) + list(registry.new_entities.items()):
            known_names.update(norm_entity(v["name"]).split())
            known_names.update(eid.split())
        for a in registry.aliases:
            known_names.update(a.split())
        for sp in names.values():
            known_names.update(norm_entity(sp).split())
        kept = []
        for c in cands:
            srcs = [m for m in worthy if m.message_id in c.source_message_ids]
            if srcs and all(self._is_assistant(m) for m in srcs) and not cfg.extract_assistant:
                plan.audit.append(("reject", None, {"reason": "assistant_source", "text": clip(c.text, 100)}))
                continue
            if cfg.require_name_grounding:
                caps = [w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", c.text)]
                bad = [w for w in caps if norm_entity(w) not in known_names and (" " + norm_entity(w) + " ") not in window_lower]
                # allow ordinary sentence-initial words ("She", "The") and month names
                bad = [w for w in bad if w.lower() not in _COMMON_CAPS]
                if bad:
                    plan.audit.append(("reject", None, {"reason": "unknown_name", "names": bad[:3], "text": clip(c.text, 100)}))
                    continue
            toks = set(tokenize(c.text))
            if toks and cfg.min_grounding_overlap > 0:
                overlap = len(toks & window_tokens) / len(toks)
                if overlap < cfg.min_grounding_overlap:
                    plan.audit.append(("reject", None, {"reason": "ungrounded", "overlap": round(overlap, 2), "text": clip(c.text, 100)}))
                    continue
            kept.append(c)
        return kept

    async def _known_memories(self, speaker_ids: dict[str, str], human_speakers: list[str]) -> list[str]:
        out: list[str] = []
        per = max(3, self.config.known_memories_in_prompt // max(1, len(human_speakers)))
        for sp in human_speakers:
            sid = speaker_ids.get(sp)
            if not sid:
                continue
            for m in await self.store.list_memories(subject=sid, limit=per, order="importance DESC"):
                out.append(clip(m.text, 160))
        return out[: self.config.known_memories_in_prompt]

    def _parse_memories(self, raw: str, worthy: list[ChatMessage]) -> list[CandidateMemory]:
        obj = parse_json_object(raw) or {}
        items = as_list(obj.get("memories"))
        out: list[CandidateMemory] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            text = normalize_ws(_ANNOTATION_RE.sub("", as_str(it.get("text") or it.get("memory") or it.get("fact"), 600)))
            if len(text) < 8:
                continue
            kind = norm_entity(as_str(it.get("kind"), 30)) or "other"
            if kind not in MEMORY_KINDS:
                kind = {"facts": "fact", "events": "event", "plans": "plan", "preferences": "preference",
                        "relation": "relationship", "relationships": "relationship", "identity": "identity",
                        "goal": "plan", "habit": "preference", "belief": "opinion", "todo": "task"}.get(kind, "other")
            srcs: list[int] = []
            for s in as_list(it.get("sources") or it.get("source")):
                try:
                    i = int(s)
                except (TypeError, ValueError):
                    continue
                if 1 <= i <= len(worthy) and i not in srcs:
                    srcs.append(i)
            ents: list[tuple[str, str]] = []
            for e in as_list(it.get("entities")):
                if isinstance(e, dict):
                    name = as_str(e.get("name"), 80)
                    etype = as_str(e.get("type"), 30)
                else:
                    name, etype = as_str(e, 80), ""
                if name:
                    ents.append((name, etype))
            out.append(CandidateMemory(
                text=text, kind=kind, subject_raw=as_str(it.get("subject"), 80),
                importance=as_float(it.get("importance"), 0.5), confidence=as_float(it.get("confidence"), 0.8),
                when_raw=as_str(it.get("when"), 60), source_indices=srcs, entities=ents,
            ))
            if len(out) >= self.config.max_memories_per_batch:
                break
        return out

    def _audit_reject(self, c: CandidateMemory, reason: str, **extra: Any) -> None:
        self._pending_audit.append(("reject", None, {"reason": reason, "text": clip(c.text, 100), **extra}))

    def _normalise(self, cands: list[CandidateMemory], worthy: list[ChatMessage], registry: EntityRegistry,
                   speaker_ids: dict[str, str], names: dict[str, str]) -> list[CandidateMemory]:
        cfg = self.config
        kept: list[CandidateMemory] = []
        seen_hash: set[str] = set()
        for c in cands:
            if c.importance < cfg.min_importance or c.confidence < cfg.min_confidence:
                self._audit_reject(c, "low_importance", importance=c.importance, confidence=c.confidence)
                continue
            if re.match(r"^(i|you|he|she|they|we)\b", c.text.lower()):
                # third-person rule violated; still usable if a subject is given, rewrite the pronoun
                if c.subject_raw:
                    c.text = re.sub(r"^(i|you|he|she|they|we)\b", c.subject_raw, c.text, count=1, flags=re.IGNORECASE)
                else:
                    continue
            src_msgs = [worthy[i - 1] for i in c.source_indices] or [worthy[-1]]
            primary = src_msgs[0]
            c.speaker = primary.speaker
            c.source_message_ids = [m.message_id for m in src_msgs]
            c.observed_at = primary.sent_at or primary.ingested_at
            speaker_eid = speaker_ids.get(primary.speaker) or norm_entity(primary.speaker)
            sid = registry.resolve(c.subject_raw, speaker_id=speaker_eid, etype="person") if c.subject_raw else None
            if not sid:
                sid = speaker_eid
            if (sid and sid.lower() in cfg.ignore_speakers) or (self._is_assistant(primary) and sid == speaker_eid and not cfg.extract_assistant):
                # a memory about the assistant itself is never stored
                self._audit_reject(c, "assistant_subject")
                continue
            c.subject_id = sid
            c.subject_name = registry.display(sid) if sid in registry.entries or sid in registry.new_entities else (c.subject_raw or names.get(primary.speaker, primary.speaker))
            registry.add(sid, c.subject_name, "person")
            c.event_time, c.event_time_precision = parse_when(c.when_raw, c.observed_at)
            ent_ids: list[str] = [sid]
            for name, etype in c.entities:
                eid = registry.resolve(name, speaker_id=speaker_eid, etype=etype)
                if eid and eid not in ent_ids and eid.lower() not in cfg.ignore_speakers:
                    ent_ids.append(eid)
            c.entity_ids = ent_ids
            c.text_hash = content_hash(normalize_ws(c.text).lower())
            if c.text_hash in seen_hash:
                continue
            seen_hash.add(c.text_hash)
            kept.append(c)
        return kept

    def _parse_relations(self, raw: str, worthy: list[ChatMessage], registry: EntityRegistry,
                         speaker_ids: dict[str, str]) -> tuple[list[str], list[tuple[str, str, str, float, str | None]]]:
        obj = parse_json_object(raw) or {}
        ent_ids: list[str] = []
        for e in as_list(obj.get("entities")):
            name = as_str(e.get("name"), 80) if isinstance(e, dict) else as_str(e, 80)
            etype = as_str(e.get("type"), 30) if isinstance(e, dict) else ""
            eid = registry.resolve(name, speaker_id=None, etype=etype, create=True) if name else None
            if eid and eid not in ent_ids and eid.lower() not in self.config.ignore_speakers:
                ent_ids.append(eid)
        triples: list[tuple[str, str, str, float, str | None]] = []
        seen: set[tuple[str, str, str]] = set()
        for t in as_list(obj.get("triples")):
            if isinstance(t, dict):
                s, r, o = t.get("s") or t.get("subject"), t.get("r") or t.get("relation") or t.get("predicate"), t.get("o") or t.get("object")
                src, conf = t.get("source"), as_float(t.get("confidence"), 0.7)
            elif isinstance(t, (list, tuple)) and len(t) >= 3:
                s, r, o = t[0], t[1], t[2]
                src, conf = None, 0.7
            else:
                continue
            try:
                idx = int(src) if src is not None else None
            except (TypeError, ValueError):
                idx = None
            msg = worthy[idx - 1] if idx and 1 <= idx <= len(worthy) else worthy[-1]
            speaker_eid = speaker_ids.get(msg.speaker) or norm_entity(msg.speaker)
            sid = registry.resolve(s, speaker_id=speaker_eid)
            oid = registry.resolve(o, speaker_id=speaker_eid)
            pred = norm_relation(as_str(r, 60))
            if not sid or not oid or not pred or sid == oid:
                continue
            if sid.lower() in self.config.ignore_speakers or oid.lower() in self.config.ignore_speakers:
                continue
            key = (sid, pred, oid)
            if key in seen:
                continue
            seen.add(key)
            triples.append((sid, pred, oid, conf, msg.message_id))
            if len(triples) >= self.config.max_triples_per_batch:
                break
        return ent_ids, triples

    async def _reconcile(self, cands: list[CandidateMemory], plan: ExtractionPlan, worthy: list[ChatMessage]) -> None:
        cfg = self.config
        if not cands:
            return
        try:
            embs = await self.llm.embed_many([c.text for c in cands])
        except Exception as exc:  # embedding server down: keep going, maintenance back-fills vectors later
            logger.warning("embedding failed for %d candidates (%s); storing without vectors", len(cands), exc)
            plan.audit.append(("embed_failed", plan.chat_id, {"count": len(cands), "error": str(exc)[:200]}))
            embs = [None] * len(cands)
        for c, e in zip(cands, embs):
            c.embedding = e or None
        # existing active memories per subject (with embeddings), fetched once per subject
        by_subject: dict[str, list[Memory]] = {}
        for c in cands:
            if c.subject_id not in by_subject:
                by_subject[c.subject_id] = await self.store.list_memories(subject=c.subject_id, limit=2000, order="observed_at DESC")

        accepted: list[CandidateMemory] = []   # for intra-batch dedupe
        for c in cands:
            # exact text duplicate anywhere
            exact = await self.store.find_active_by_text_hash(c.text_hash)
            if exact is not None:
                plan.duplicate_sources.append((exact.memory_id, c.source_message_ids))
                plan.audit.append(("duplicate", exact.memory_id, {"text": clip(c.text, 120), "how": "exact"}))
                continue
            # intra-batch near-duplicate
            if any(_cos(c.embedding, a.embedding) >= cfg.dedupe_cosine for a in accepted):
                plan.audit.append(("duplicate", None, {"text": clip(c.text, 120), "how": "intra_batch"}))
                continue
            if c.embedding:
                sims = sorted(((_cos(c.embedding, m.embedding), m) for m in by_subject.get(c.subject_id, []) if m.embedding),
                              key=lambda x: -x[0])
                top = [(s, m) for s, m in sims[: cfg.reconcile_candidates] if s >= cfg.related_cosine]
            else:
                # no vector: use keyword overlap among the subject's memories as the pre-filter
                pool = {m.memory_id: m for m in by_subject.get(c.subject_id, [])}
                kw = await self.store.keyword_candidates(c.text, limit=cfg.reconcile_candidates * 4)
                top = [(cfg.reconcile_cosine, pool[mid]) for mid, _ in kw if mid in pool][: cfg.reconcile_candidates]
            decision, target, merged_text = "ADD", None, None
            if top and top[0][0] >= cfg.dedupe_cosine:
                decision, target = "DUPLICATE", top[0][1]
            elif top and top[0][0] >= cfg.reconcile_cosine:
                decision, target, merged_text = await self._ask_reconcile(c, [m for _, m in top])
            self._apply_decision(c, decision, target, merged_text, top, plan)
            if decision in ("ADD", "UPDATE", "CONTRADICT"):
                accepted.append(c)

    async def _ask_reconcile(self, c: CandidateMemory, existing: list[Memory]) -> tuple[str, Memory | None, str | None]:
        letters = "ABCDEFGH"
        listing = "\n".join(f"{letters[i]}. (observed {(m.observed_at or '')[:10]}) \"{clip(m.text, 300)}\"" for i, m in enumerate(existing))
        prompt = RECONCILE_PROMPT.format(new_date=(c.observed_at or "")[:10], new_text=clip(c.text, 400), existing=listing)
        try:
            raw = await self.llm.generate(prompt, max_tokens=self.config.reconcile_max_tokens, timeout=self.config.llm_timeout)
        except Exception as exc:  # LLM failure: fall back to ADD (dedupe already handled the obvious case)
            logger.warning("reconcile call failed, defaulting to ADD: %s", exc)
            return "ADD", None, None
        obj = parse_json_object(raw) or {}
        decision = as_str(obj.get("decision"), 20).upper().strip()
        if decision not in ("ADD", "DUPLICATE", "UPDATE", "CONTRADICT"):
            m = re.search(r"\b(ADD|DUPLICATE|UPDATE|CONTRADICT)\b", raw.upper())
            decision = m.group(1) if m else "ADD"
        target = None
        t = as_str(obj.get("target"), 5).upper().strip().rstrip(".")
        if t and t[0] in letters[: len(existing)]:
            target = existing[letters.index(t[0])]
        if decision != "ADD" and target is None:
            target = existing[0]
        text = normalize_ws(as_str(obj.get("text"), 600)) or None
        if decision == "UPDATE" and (not text or len(text) < 8):
            text = c.text
        return decision, target, text

    def _apply_decision(self, c: CandidateMemory, decision: str, target: Memory | None, merged_text: str | None,
                        top: list[tuple[float, Memory]], plan: ExtractionPlan) -> None:
        cfg = self.config
        now = now_iso()
        if decision == "DUPLICATE" and target is not None:
            plan.duplicate_sources.append((target.memory_id, c.source_message_ids))
            plan.audit.append(("duplicate", target.memory_id, {"text": clip(c.text, 120), "how": "reconcile"}))
            return
        text = c.text
        version = 1
        status = "active"
        superseded_by = None
        if decision in ("UPDATE", "CONTRADICT") and target is not None:
            if decision == "UPDATE" and merged_text:
                text = merged_text
            version = target.version + 1
            # temporal order: a candidate observed EARLIER than the existing memory cannot supersede it
            if c.observed_at and target.observed_at and c.observed_at < target.observed_at:
                status, superseded_by = "superseded", target.memory_id
        mem = Memory(
            memory_id=new_id("mem"), text=text, kind=c.kind, subject=c.subject_id, subject_name=c.subject_name,
            speaker=c.speaker, chat_id=plan.chat_id, importance=c.importance, confidence=c.confidence,
            event_time=c.event_time, event_time_precision=c.event_time_precision, observed_at=c.observed_at or now,
            created_at=now, updated_at=now, status=status, superseded_by=superseded_by, version=version,
            text_hash=content_hash(normalize_ws(text).lower()), embedding=c.embedding,
            metadata={"decision": decision, "source_kind": "chat", "when_raw": c.when_raw or None},
        )
        plan.new_memories.append((mem, c.source_message_ids, c.entity_ids))
        if decision in ("UPDATE", "CONTRADICT") and target is not None:
            link = "supersedes" if decision == "UPDATE" else "contradicts"
            if status == "active":
                plan.supersedes.append((target.memory_id, mem.memory_id, link))
            else:  # older observation: link for history only, existing memory stays current
                plan.links.append((target.memory_id, mem.memory_id, link, 1.0))
            plan.audit.append((decision.lower(), mem.memory_id, {"target": target.memory_id, "text": clip(text, 120)}))
        else:
            plan.audit.append(("add", mem.memory_id, {"text": clip(text, 120)}))
        # related links to the nearest neighbours that were not superseded
        n = 0
        for sim, m in top:
            if target is not None and m.memory_id == target.memory_id:
                continue
            if sim >= cfg.related_cosine and n < cfg.max_related_links:
                plan.links.append((mem.memory_id, m.memory_id, "related", round(float(sim), 4)))
                n += 1
