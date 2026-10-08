"""T1 drafts at HQ (G7): the draft payload, the template drafter and the id-scope scan.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

A draft is written from structured inputs only (:func:`draft_payload`: the follow-up type, its args, the conclusion's
key, labels, window, sites, decision unit and support lower bounds, and each T0 packet's status, verdict, support
bucket, code labels and co-mentions); it holds no reasons text and no narrative, so record text never reaches a
drafter. :class:`DraftWriter` asks a ``central`` runtime for the task :data:`DRAFT_TASK` (data class ``structured``;
the runtime validates the reply against the type's ``draft_schema``, with one repair) or, without a runtime, fills
:func:`template_draft` (deterministic; also the fake provider's handler) and checks it against the schema. Either
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


def template_draft(pack: "FrozenPack") -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """The deterministic drafter (and the fake provider's handler): every required string property of the type's
    draft schema gets ``'<type label>: <summary>'`` cut at its ``maxLength``, every required array ``[]``; other
    property types are left out (the schema check then refuses the draft). The summary is ``'<predicate label> on
    <entity type label> <entity id>: supported, <n> confirming site(s), weeks <start> to <end> (conclusion <id>
    v<version>)'``; it holds no domain literal."""

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        ft = pack.followups[payload["followup_type"]]
        c = payload["conclusion"]
        # not "supported at <n> site(s)": two letters, a space and a number read as a space-separated id of a
        # two-letter id format, which the id-scope scan rightly counts as an unresolved lookalike
        summary = (f"{c['predicate_label']} on {c['entity_type_label']} {c['entity_id']}: supported, "
                   f"{len(c['confirming_sites'])} confirming site(s), weeks {c['window']['start_week']} to "
                   f"{c['window']['end_week']} (conclusion {c['conclusion_id']} v{c['version']})")
        schema = ft.draft_json_schema()
        out: dict[str, Any] = {}
        for name in sorted(schema["required"]):
            prop = schema["properties"][name]
            base = prop["type"] if isinstance(prop["type"], str) else prop["type"][0]
            if base == "string":
                out[name] = f"{payload['type_label']}: {summary}"[:prop["maxLength"]]
            elif base == "array":
                out[name] = []
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
