"""T1 drafts at HQ (G7): the draft payload, the template drafter and the id-scope scan.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

A draft is written from structured inputs only (:func:`draft_payload`: the follow-up type, its args, the conclusion's
key, labels, window, sites, decision unit and support lower bounds, and each T0 packet's status, verdict, support
bucket, code labels and co-mentions); it holds no reasons text and no narrative, so record text never reaches a
drafter. :class:`DraftWriter` asks a ``central`` runtime for the task :data:`DRAFT_TASK` (data class ``structured``;
the runtime validates the reply against the type's ``draft_schema``, with one repair) or, without a runtime, fills
:func:`template_draft` (deterministic; also the fake provider's handler) and checks it against the schema. The
template drafter fills each property from the source the type's pack ``template`` names (B1; :func:`template_sources`):
the headline, the summary, the evidence of the ok packets, the conclusion's entity ids of one type and the
co-mentioned ones, the confirming sites, or nothing (``for_owner``: left for the named owner to write). Either
way the draft must then pass :func:`draft_scope_problem`: no exact or variant mention of an id of a type with an id
format outside the conclusion's scope ids and the packets' co-mentions, whether written as the id or as one of its
aliases (a product, supplier, repair shop or clinic named by its name), and no unresolved lookalike (a homoglyph, a
non-ASCII digit, a separator form of an id that the canonicaliser recognises but cannot resolve). Mentions of
alias-only types are ignored: those are closed pack vocabularies whose aliases are ordinary words, so a predicate
label may read as an alias (D8). The aliases of a type with an id format are proper names, so they are checked like
the id (audit round 3). The scan sees only the forms the canonicaliser recognises for each id format: an id restated
with a separator its format does not have, or without the one it has, is not seen, nor is a name that is not in the
pack's alias table (LEAKAGE section 10 lists the forms; the second G7 ``NOT_COVERED`` item covers the residual). A
failure is a reason from :data:`DRAFT_FAILURE_REASONS` and no draft; nothing retries here.

This is the only follow-up module that imports inference (``tasks`` and ``errors``; the runtime only for type
checking). Deterministic: no clock, no randomness, every iteration sorted.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from .. import schemacheck
from ..inference.errors import KINDS, InferenceError
from ..inference.tasks import TaskSpec
from ..jsonio import canonical_bytes, canonical_dumps, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.loader import TEMPLATE_ARRAY_SOURCES, TEMPLATE_ENTITY_IDS, TEMPLATE_FOR_OWNER, TEMPLATE_STRING_SOURCES

if TYPE_CHECKING:
    from ..inference.runtime import Runtime
    from ..packs.loader import FollowupType, FrozenPack
    from .service import ConclusionView

DRAFT_TASK = "draft_followup"
DRAFT_MAX_TOKENS = 2048
DRAFT_FAILURE_REASONS = (*KINDS, "out_of_scope_id")
DRAFT_INSTRUCTIONS = (
    "Fill the follow-up form that the output JSON schema describes, from the structured conclusion and the packet "
    "summaries in the data only. Invent no id, count, site, week or date: use only values present in the data, and "
    "leave a list empty rather than guess. Write plain sentences for the people who will review the form."
)
PAYLOAD_KEYS = ("args", "conclusion", "followup_type", "packets", "tier", "type_label")
CONCLUSION_KEYS = ("candidate_key", "conclusion_id", "confirming_sites", "decision_unit", "entity_id", "entity_type",
                   "entity_type_label", "newest_week", "predicate", "predicate_label", "reporters_lb", "roots_lb",
                   "status", "support_lb", "version", "window")
PACKET_SUMMARY_KEYS = ("co_mentions", "codes", "site", "status", "support_bucket", "verdict")
_SIMPLE_KEY = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
# the words a template source is built from (packs/loader.py validates the forms: a list of text sources, and
# entity_ids:<egress entity type>)
SOURCES = (*TEMPLATE_STRING_SOURCES, *(s for s in TEMPLATE_ARRAY_SOURCES if s not in TEMPLATE_STRING_SOURCES),
           TEMPLATE_ENTITY_IDS.rstrip(":"))
FOR_OWNER_TEXT = ("For the named owner to write: the template fills only what it can take from the conclusion and "
                  "the site packets.")
EVIDENCE_NONE = "no site packet with status ok"


class DraftError(ValueError):
    """A drafter that cannot be built; the text never holds a value."""


def draft_task() -> TaskSpec:
    return TaskSpec(DRAFT_TASK, "structured", DRAFT_INSTRUCTIONS, DRAFT_MAX_TOKENS)


def draft_payload(pack: "FrozenPack", ft: "FollowupType", view: "ConclusionView", args: Mapping[str, Any],
                  packets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exactly :data:`PAYLOAD_KEYS`; structured values only (no reasons text, no narrative)."""
    summaries = [{"site": p["site"], "status": p["status"], "verdict": p["verdict"],
                  "support_bucket": p["support_bucket"], "codes": p["codes"], "co_mentions": p["co_mentions"]}
                 for p in packets]
    summaries.sort(key=lambda s: (s["site"], canonical_dumps(s)))
    payload = {
        "followup_type": ft.id, "type_label": ft.label, "tier": ft.tier, "args": args,
        "conclusion": {
            "conclusion_id": view.conclusion_id, "version": view.version, "candidate_key": view.candidate_key,
            "entity_type": view.entity_type, "entity_type_label": pack.entity_types[view.entity_type].label,
            "entity_id": view.entity_id, "predicate": view.predicate,
            "predicate_label": pack.predicates[view.predicate].label, "status": view.status,
            "window": {"start_week": view.window[0], "end_week": view.window[1]},
            "confirming_sites": list(view.confirming_sites), "decision_unit": view.decision_unit,
            "support_lb": view.support_lb, "roots_lb": view.roots_lb, "reporters_lb": view.reporters_lb,
            "newest_week": view.newest_week},
        "packets": summaries}
    return strict_load(canonical_bytes(payload))


def template_sources(ft: "FollowupType") -> dict[str, str]:
    """Each templated property's source as one word: a list of text sources joined with ``+`` (``summary+evidence``),
    ``entity_ids:<type>`` as written; ``{}`` for a type without a template."""
    if ft.template is None:
        return {}
    return {name: source if isinstance(source, str) else "+".join(source)
            for name, source in sorted(ft.template.items())}


def _clause(pack: "FrozenPack", packet: Mapping[str, Any]) -> str:
    """One ok packet as evidence: its site, verdict and support bucket, then its codes and co-mentions with their
    count labels."""
    clause = f"{packet['site']}: {packet['verdict']}, support {packet['support_bucket']}"
    if packet["codes"]:
        clause += ", codes: " + ", ".join(f"{pack.codes[c['code']].label} ({c['n']})" for c in packet["codes"])
    if packet["co_mentions"]:
        clause += ", named with: " + ", ".join(
            f"{pack.entity_types[m['entity_type']].label} {m['entity_id']} ({m['n']})" for m in packet["co_mentions"])
    return clause


def _fit(sources: Sequence[str], texts: Mapping[str, str], clauses: Sequence[str], limit: int) -> str:
    """The sources' texts joined with a space (empty parts left out), at most ``limit`` characters: whole evidence
    clauses dropped from the end first, then the evidence part, then a cut at the last space (inside a token only
    when the text has no space)."""
    clauses = list(clauses)

    def compose(evidence: str) -> str:
        return " ".join(t for t in (evidence if src == "evidence" else texts[src] for src in sources) if t)

    text = compose("; ".join(clauses) if clauses else EVIDENCE_NONE)
    if "evidence" in sources:
        while clauses and len(text) > limit:
            clauses.pop()
            text = compose("; ".join(clauses))
        if len(text) > limit:
            text = compose("")
    if len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        text = text[:cut] if cut > 0 else text[:limit]
    return text


def template_draft(pack: "FrozenPack") -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """The deterministic drafter (and the fake provider's handler): every property of the type's ``template`` is
    filled from its source (B1), strings cut to ``maxLength`` (:func:`_fit`) and arrays to ``maxItems``:

    * ``headline``: ``'<type label>: <predicate label> on <entity type label> <entity id>'``;
    * ``summary``: ``'<predicate label> on <entity type label> <entity id>: supported, <n> confirming site(s), weeks
      <start> to <end> (conclusion <id> v<version>)'``;
    * ``evidence``: one clause per packet with status ``ok``, in payload order (``'<site>: <verdict>, support
      <bucket>'``, then ``', codes: <code label> (<n>), ...'`` and ``', named with: <entity type label> <id> (<n>),
      ...'``), joined with ``'; '``; :data:`EVIDENCE_NONE` without an ok packet;
    * ``for_owner``: :data:`FOR_OWNER_TEXT` for a string, ``[]`` for an array;
    * ``entity_ids:<type>``: the sorted unique ids of that type among the conclusion's entity and every ok packet's
      co-mentions;
    * ``confirming_sites``: the conclusion's confirming sites.

    Structured inputs only; it holds no domain literal."""

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        ft = pack.followups[payload["followup_type"]]
        c = payload["conclusion"]
        subject = f"{c['predicate_label']} on {c['entity_type_label']} {c['entity_id']}"
        # not "supported at <n> site(s)": two letters, a space and a number read as a space-separated id of a
        # two-letter id format, which the id-scope scan rightly counts as an unresolved lookalike
        texts = {TEMPLATE_FOR_OWNER: FOR_OWNER_TEXT, "headline": f"{payload['type_label']}: {subject}",
                 "summary": (f"{subject}: supported, {len(c['confirming_sites'])} confirming site(s), weeks "
                             f"{c['window']['start_week']} to {c['window']['end_week']} (conclusion "
                             f"{c['conclusion_id']} v{c['version']})")}
        ok = [p for p in payload["packets"] if p["status"] == "ok"]
        clauses = [_clause(pack, p) for p in ok]
        schema = ft.draft_json_schema()
        out: dict[str, Any] = {}
        for name, source in sorted((ft.template or {}).items()):
            prop = schema["properties"][name]
            if isinstance(source, str) and source.startswith(TEMPLATE_ENTITY_IDS):
                entity_type = source[len(TEMPLATE_ENTITY_IDS):]
                ids = {c["entity_id"]} if c["entity_type"] == entity_type else set()
                ids |= {m["entity_id"] for p in ok for m in p["co_mentions"] if m["entity_type"] == entity_type}
                out[name] = sorted(ids)[:prop.get("maxItems", len(ids))]
            elif source == "confirming_sites":
                sites = list(c["confirming_sites"])
                out[name] = sites[:prop.get("maxItems", len(sites))]
            elif source == TEMPLATE_FOR_OWNER and (prop["type"] if isinstance(prop["type"], str)
                                                   else prop["type"][0]) == "array":
                out[name] = []
            else:
                parts = (source,) if isinstance(source, str) else tuple(source)
                out[name] = _fit(parts, texts, clauses, prop["maxLength"])
        return out

    return handler


def _child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if _SIMPLE_KEY.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def _strings(value: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(value, str):
        out.append((path, value))
    elif isinstance(value, dict):
        for key in sorted(value):
            _strings(value[key], _child(path, key), out)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _strings(item, _child(path, i), out)


def draft_scope_problem(pack: "FrozenPack", scope: Iterable[tuple[str, str]], draft: Any) -> str | None:
    """The JSON path of the first string that names an id of a type with an id format outside ``scope`` (exact,
    variant or by one of its aliases), or holds an unresolved lookalike; None when the draft names only ids in scope
    (D8)."""
    known: dict[str, set[str]] = {}
    for entity_type, entity_id in sorted(scope):
        et = pack.entity_types.get(entity_type)
        if et is not None and (entity_id in et.ids if et.id_format is None else et.id_format.is_canonical(entity_id)):
            known.setdefault(entity_type, set()).add(entity_id)
    allowed = {(t, eid) for t in known for eid in known[t]}
    canonicaliser = Canonicaliser(pack, known=known)
    texts: list[tuple[str, str]] = []
    _strings(draft, "$", texts)
    for path, text in texts:
        scanned = canonicaliser.scan(text)
        if any(scanned.unresolved.values()):
            return path
        for m in scanned.mentions:
            if pack.entity_types[m.entity_type].id_format is None:
                continue
            if (m.entity_type, m.entity_id) not in allowed:
                return path
    return None


class DraftWriter:
    """Writes T1 drafts; ``runtime`` must be bound to the ``central`` boundary, or None for the template."""

    def __init__(self, pack: "FrozenPack", *, runtime: "Runtime | None") -> None:
        if runtime is not None and runtime.boundary != "central":
            raise DraftError("a draft runtime must be bound to the central boundary") from None
        self.pack = pack
        self.runtime = runtime
        self._task = draft_task()
        self._template = template_draft(pack)

    def write(self, ft: "FollowupType", payload: Mapping[str, Any], *, ref: str,
              scope: Iterable[tuple[str, str]]) -> tuple[dict[str, Any] | None, str | None]:
        """``(draft, None)`` or ``(None, reason)``; ``ref`` is the ledger handle ``d:<key suffix>:<attempt>``."""
        schema = schemacheck.compile(ft.draft_json_schema())
        draft = reason = None
        if self.runtime is not None:
            try:
                draft = self.runtime.run(self._task, dict(payload), schema, ref=ref)
            except InferenceError as err:
                reason = err.kind
        else:
            draft = self._template(payload)
            if schema.validate(draft):
                draft, reason = None, "schema_invalid"
        if reason is not None:
            return None, reason
        if draft_scope_problem(self.pack, scope, draft) is not None:
            return None, "out_of_scope_id"
        return strict_load(canonical_bytes(draft)), None
