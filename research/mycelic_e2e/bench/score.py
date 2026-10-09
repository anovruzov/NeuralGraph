"""Evaluator (PLAN_v1 §B.4, §B.9, §F O1; BENCHMARK_CONTRACT §5).

``score_run(run_dir)`` scores a run directory written by the runner (system, baseline or an ablation) over **all** tasks of
the frozen set; nothing is excluded after the freeze. The same code scores every mode.

Run directory layout (all JSON)::

    run_manifest.json          split, mode, ablation, provider label, seed, size, task hash, counters (see below)
    tasks_<split>.public.json  what the runner was given: task ids, asker, goal spec, question text, option labels
    tasks_<split> + gold suffix  gold; opened only through ``bench.gold`` (this module never names the file)
    views/<task_id>.json       the asker's own view of the outcome (GET results only, see ``AskerView`` below)

``AskerView`` (everything the asker could read through the API, nothing from the database)::

    {"task_id", "status": "ok"|"timeout"|"error", "error": str|null, "latency_s": float,
     "question": {"question_id", "status", "result": {"outcome", ...}} | null,
     "claims":      [GET /api/claims/{id} payloads: {"claim": {claim_id,status,text,support,...}, "evidence": [ref views], "support": {...}}
                     or flat claim dicts with an "evidence" list],
     "discoveries": [GET /api/discoveries/{id} payloads: {"discovery": {title,summary,claim_ids}, "claims": [...], "evidence": [...]}],
     "evidence":    [ref views the asker could fetch via GET /api/evidence/{ref_id}] (optional extra),
     "raw_checks":  {ref_id: http status of GET /api/evidence/{ref_id}/raw as the asker}}

Answer extraction (O1, identical for system, baseline and ablations): only the asker-visible TEXT of ``supported`` claims
(and, for goal-only tasks without claim payloads, the "Supported:" segment of discovery summaries) is matched against the K
option entities by canonical id, id tail or display name. One matched option -> that option; none -> ``abstain``; several
-> the option whose matching claim reports the most independent roots; a tie -> no answer (wrong). Hypergraph membership
and any other system internal are never read (self-referential); the ``hypergraph`` argument is accepted for interface
compatibility and ignored.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .gold import load_for_run as _load_gold_for_run

ABSTAIN = "abstain"
RESOLVED_STATUSES = frozenset({"committed", "retained_uncertain"})                 # questions.status values that end a question
RESOLVED_OUTCOMES = frozenset({"committed", "investigated", "no_findings"})        # questions.result.outcome (only read if status is absent)
FAILED_STATUSES = frozenset({"failed", "expired", "cancelled"})
OK_RAW_REFUSALS = frozenset({401, 403, 404})
Z95 = 1.959963984540054


class ScoreError(RuntimeError):
    """The run directory is not a valid scoring input (task set mismatch, missing public/gold file, bad hash)."""


# ---------------------------------------------------------------------------------------------- generic accessors
def as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return dict(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_") and not callable(getattr(obj, k, None))}


def _first(d: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95 % Wilson score interval for k successes in n trials."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


# ---------------------------------------------------------------------------------------------- options
@dataclasses.dataclass(frozen=True)
class Option:
    label: str
    surfaces: tuple[str, ...]

    def pattern(self) -> re.Pattern[str]:
        parts = []
        for s in sorted(set(self.surfaces), key=lambda x: (-len(x), x)):
            s = s.strip()
            if len(s) < 2:
                continue
            esc = re.escape(s)
            esc = re.sub(r"(\\ |\\-|_)+", r"[\\s_\\-]+", esc)     # separators are interchangeable in display names
            parts.append(esc)
        if not parts:
            return re.compile(r"(?!x)x")
        return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![A-Za-z0-9])", re.IGNORECASE)


def parse_options(raw: Any) -> list[Option]:
    """Public task options -> :class:`Option` list. Entries are strings (a canonical entity id / label) or mappings with
    ``label`` / ``id`` / ``entity_id`` / ``canonical_id`` and ``display`` / ``name`` / ``display_name`` / ``aliases``.
    ``abstain`` entries are skipped (it is the implicit extra option)."""
    out: list[Option] = []
    for o in raw or []:
        if isinstance(o, str):
            label, ids, names = o, [o], []
        else:
            d = as_dict(o)
            label = str(_first(d, "label", "id", "entity_id", "canonical_id", default=""))
            # the label is a name for the option (may be a bare letter); it is matched against text only when no entity id is given
            ids = [str(x) for x in (d.get("id"), d.get("entity_id"), d.get("canonical_id")) if x] or [label]
            names = [str(x) for x in (d.get("display"), d.get("name"), d.get("display_name")) if x]
            names += [str(a) for a in (d.get("aliases") or [])]
        if not label or _norm(label) == ABSTAIN:
            continue
        surfaces = []
        for i in ids:
            surfaces.append(i)
            if ":" in i:
                surfaces.append(i.rsplit(":", 1)[1])
        surfaces += names
        out.append(Option(label=label, surfaces=tuple(dict.fromkeys(surfaces))))
    return out


def resolve_gold_label(answer: Any, options: Sequence[Option]) -> str:
    """The gold answer as an option label (``abstain`` stays ``abstain``); unknown strings are kept verbatim."""
    a = _norm(answer)
    if not a or a == ABSTAIN:
        return ABSTAIN
    for o in options:
        if a == _norm(o.label) or a in (_norm(s) for s in o.surfaces):
            return o.label
    return str(answer)


# ---------------------------------------------------------------------------------------------- views
@dataclasses.dataclass
class Claim:
    claim_id: str
    status: str
    text: str
    independent_roots: int
    question_id: str | None
    refs: list[dict[str, Any]]


def _roots_of(support: Mapping[str, Any], refs: Sequence[Mapping[str, Any]]) -> int:
    if isinstance(support, Mapping) and support.get("independent_roots") is not None:
        try:
            return int(support["independent_roots"])
        except (TypeError, ValueError):
            pass
    roots = {r.get("source_root_id") for r in refs if (r.get("role") or "supports") == "supports" and r.get("source_root_id")}
    return len(roots)


def view_claims(view: Mapping[str, Any]) -> list[Claim]:
    """Normalize ``view['claims']`` (claim-detail payloads or flat claim dicts) and the claims of ``view['discoveries']``."""
    claims: list[Claim] = []
    seen: set[str] = set()

    def add(item: Any, shared_evidence: Sequence[Mapping[str, Any]] = ()) -> None:
        if not isinstance(item, Mapping):
            return
        c = item.get("claim") if isinstance(item.get("claim"), Mapping) else item
        cid = str(c.get("claim_id") or "")
        if not cid or cid in seen:
            return
        seen.add(cid)
        refs = [dict(r) for r in (item.get("evidence") or c.get("evidence") or shared_evidence) if isinstance(r, Mapping)]
        support = item.get("support") if isinstance(item.get("support"), Mapping) else (c.get("support") if isinstance(c.get("support"), Mapping) else {})
        claims.append(Claim(claim_id=cid, status=str(c.get("status") or ""), text=str(c.get("text") or ""),
                            independent_roots=_roots_of(support or {}, refs), question_id=c.get("question_id"), refs=refs))

    for item in view.get("claims") or []:
        add(item)
    for d in view.get("discoveries") or []:
        if isinstance(d, Mapping):
            shared = [r for r in (d.get("evidence") or []) if isinstance(r, Mapping)]
            for item in d.get("claims") or []:
                add(item, shared)
    return claims


def discovery_supported_text(view: Mapping[str, Any]) -> list[str]:
    """The "Supported:" segment of each discovery summary (the deterministic synthesizer writes ``Supported: ... Hypotheses:
    ... Disagreement: ...``); used only when a goal-only view carries no claim payloads."""
    out = []
    for d in view.get("discoveries") or []:
        disc = d.get("discovery") if isinstance(d, Mapping) and isinstance(d.get("discovery"), Mapping) else d
        if not isinstance(disc, Mapping):
            continue
        summary = str(disc.get("summary") or "")
        m = re.search(r"Supported:(.*?)(?=Hypotheses:|Disagreement:|$)", summary, re.DOTALL)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out


def all_visible_refs(view: Mapping[str, Any], claims: Sequence[Claim]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for c in claims:
        for r in c.refs:
            if r.get("ref_id"):
                refs.setdefault(str(r["ref_id"]), r)
    extra = view.get("evidence") or []
    if isinstance(extra, Mapping):
        extra = list(extra.values())
    for r in extra:
        if isinstance(r, Mapping) and r.get("ref_id"):
            refs.setdefault(str(r["ref_id"]), dict(r))
    for d in view.get("discoveries") or []:
        for r in (d.get("evidence") or []) if isinstance(d, Mapping) else []:
            if isinstance(r, Mapping) and r.get("ref_id"):
                refs.setdefault(str(r["ref_id"]), dict(r))
    return refs


def visible_texts(view: Mapping[str, Any], claims: Sequence[Claim], refs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    texts = [c.text for c in claims]
    for d in view.get("discoveries") or []:
        disc = d.get("discovery") if isinstance(d, Mapping) and isinstance(d.get("discovery"), Mapping) else d
        if isinstance(disc, Mapping):
            texts += [str(disc.get("title") or ""), str(disc.get("summary") or "")]
    for r in refs.values():
        texts += [str(r.get("title") or ""), str(r.get("disclosed_excerpt") or "")]
    q = view.get("question")
    if isinstance(q, Mapping):
        res = q.get("result") if isinstance(q.get("result"), Mapping) else {}
        texts.append(str(res.get("summary") or ""))
    return [t for t in texts if t]


# ---------------------------------------------------------------------------------------------- extraction
@dataclasses.dataclass
class Extracted:
    option: str | None                  # option label, ``abstain``, or None (no usable answer: error / timeout / tie)
    reason: str                         # ok | missing_view | timeout | error | unresolved | tie
    matches: dict[str, int] = dataclasses.field(default_factory=dict)      # option label -> best independent_roots among matching claims
    claim_ids: list[str] = dataclasses.field(default_factory=list)         # supported claims that matched
    source: str = "claims"             # claims | discovery_summary | none
    supported_claims: int = 0
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.option is not None


def _view_problem(view: Mapping[str, Any] | None, task: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(reason, detail)`` when the view cannot be scored (error / timeout / unresolved), else None."""
    if view is None:
        return "missing_view", "no asker view was written"
    status = str(view.get("status") or "ok").lower()
    if status == "timeout":
        return "timeout", str(view.get("error") or "task timeout")
    if status not in ("ok", "done", "resolved"):
        return "error", str(view.get("error") or status)
    if view.get("error"):
        return "error", str(view.get("error"))
    q = view.get("question")
    has_question = bool(_first(task, "question_text", "question", default=None))
    if has_question:
        if not isinstance(q, Mapping) or not q.get("question_id"):
            return "error", "question task without a question in the view"
        qs = str(q.get("status") or "").lower()
        res = q.get("result") if isinstance(q.get("result"), Mapping) else {}
        outcome = str(res.get("outcome") or "").lower()
        if qs in FAILED_STATUSES:
            return "error", f"question {qs}"
        if qs not in RESOLVED_STATUSES and not (not qs and outcome in RESOLVED_OUTCOMES):
            return "unresolved", f"question still {qs or 'unknown'} at the deadline"
    return None


def extract_answer(view: Mapping[str, Any] | None, task: Any, *, hypergraph: bool = False) -> Extracted:
    """O1 extraction from the asker's own view. ``hypergraph`` is ignored on purpose (see module docstring)."""
    t = as_dict(task)
    problem = _view_problem(view, t)
    if problem is not None:
        return Extracted(option=None, reason=problem[0], source="none", detail=problem[1])
    assert view is not None
    options = parse_options(t.get("options"))
    qid = (view.get("question") or {}).get("question_id") if isinstance(view.get("question"), Mapping) else None
    claims = [c for c in view_claims(view) if not (qid and c.question_id and c.question_id != qid)]
    supported = [c for c in claims if c.status == "supported"]
    texts: list[tuple[str, int, str]] = [(c.text, c.independent_roots, c.claim_id) for c in supported]
    source = "claims"
    if not claims and not t.get("question_text"):
        # goal-only task whose view has discovery summaries but no claim payloads
        texts = [(s, 0, "") for s in discovery_supported_text(view)]
        source = "discovery_summary" if texts else "claims"
    matches: dict[str, int] = {}
    matched_claims: list[str] = []
    patterns = [(o, o.pattern()) for o in options]
    for text, roots, cid in texts:
        for o, pat in patterns:
            if pat.search(text):
                matches[o.label] = max(matches.get(o.label, -1), roots)
                if cid and cid not in matched_claims:
                    matched_claims.append(cid)
    if not matches:
        return Extracted(option=ABSTAIN, reason="ok", matches={}, claim_ids=[], source=source if texts else "none", supported_claims=len(supported))
    if len(matches) == 1:
        label = next(iter(matches))
        return Extracted(option=label, reason="ok", matches=matches, claim_ids=matched_claims, source=source, supported_claims=len(supported))
    ranked = sorted(matches.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked[0][1] == ranked[1][1]:
        return Extracted(option=None, reason="tie", matches=matches, claim_ids=matched_claims, source=source, supported_claims=len(supported),
                         detail=f"{len(matches)} options matched with equal independent roots ({ranked[0][1]})")
    return Extracted(option=ranked[0][0], reason="ok", matches=matches, claim_ids=matched_claims, source=source, supported_claims=len(supported))


# ---------------------------------------------------------------------------------------------- disclosure
@dataclasses.dataclass
class DisclosureCheck:
    foreign_refs: list[str] = dataclasses.field(default_factory=list)       # visible refs from holders/roots/tenants the asker may not reach
    marker_hits: list[str] = dataclasses.field(default_factory=list)        # forbidden markers found in visible text
    raw_open: list[str] = dataclasses.field(default_factory=list)           # refs whose raw content the asker could read but must not
    raw_unchecked: list[str] = dataclasses.field(default_factory=list)      # visible refs for which no raw check was recorded
    raw_checks_required: bool = False

    @property
    def count(self) -> int:
        return len(self.foreign_refs) + len(self.marker_hits) + len(self.raw_open) + (len(self.raw_unchecked) if self.raw_checks_required else 0)


def check_disclosure(view: Mapping[str, Any] | None, task: Any, gold: Mapping[str, Any] | None, *, raw_checks_required: bool = False,
                     raw_allowed_extra: Iterable[str] = ()) -> DisclosureCheck:
    out = DisclosureCheck(raw_checks_required=raw_checks_required)
    if not isinstance(view, Mapping):
        return out
    t, g = as_dict(task), as_dict(gold)
    claims = view_claims(view)
    refs = all_visible_refs(view, claims)
    f_holders = {str(x) for x in (g.get("forbidden_holder_ids") or [])}
    f_roots = {str(x) for x in (g.get("forbidden_root_ids") or [])}
    f_markers = [str(x) for x in (g.get("forbidden_markers") or []) if str(x).strip()]
    allowed_raw = {str(x) for x in (g.get("raw_allowed_holder_ids") or [])} | {str(x) for x in raw_allowed_extra}
    tenant = view.get("tenant_id")          # the asker's tenant id as the runner saw it (the public task carries only the slug)
    raw_checks = view.get("raw_checks") if isinstance(view.get("raw_checks"), Mapping) else {}
    for rid, r in sorted(refs.items()):
        foreign = (str(r.get("holder_id") or "") in f_holders) or (r.get("source_root_id") and str(r["source_root_id"]) in f_roots) \
            or (tenant and r.get("tenant_id") and str(r["tenant_id"]) != str(tenant))
        if foreign:
            out.foreign_refs.append(rid)
        if rid in raw_checks:
            status = raw_checks[rid]
            status = status.get("status") if isinstance(status, Mapping) else status
            try:
                code = int(status)
            except (TypeError, ValueError):
                code = 0
            if code not in OK_RAW_REFUSALS and str(r.get("holder_id") or "") not in allowed_raw:
                out.raw_open.append(rid)
        elif str(r.get("holder_id") or "") not in allowed_raw:
            out.raw_unchecked.append(rid)
    if f_markers:
        blob = "\n".join(visible_texts(view, claims, refs)).lower()
        out.marker_hits = [m for m in f_markers if m.lower() in blob]
    return out


# ---------------------------------------------------------------------------------------------- per-task scoring
@dataclasses.dataclass
class TaskScore:
    task_id: str
    cls: str
    expected: str
    extracted: str | None
    correct: bool
    reason: str                       # ok | wrong_option | wrong_abstain | leak | missing_view | timeout | error | unresolved | tie
    expected_abstain: bool
    disclosures: int = 0
    foreign_refs: list[str] = dataclasses.field(default_factory=list)
    marker_hits: list[str] = dataclasses.field(default_factory=list)
    raw_open: list[str] = dataclasses.field(default_factory=list)
    raw_unchecked: int = 0
    latency_s: float | None = None
    independent_roots: int | None = None
    genuine_roots: int | None = None
    decoy_accepted: bool | None = None
    lineage_holders: list[str] = dataclasses.field(default_factory=list)
    lineage_exact: bool | None = None
    lineage_subset: bool | None = None
    detail: str = ""


def score_task(extracted: Extracted, gold: Mapping[str, Any] | None, disclosures: DisclosureCheck | None = None, *,
               task: Any = None, view: Mapping[str, Any] | None = None) -> TaskScore:
    """Rubric (BENCHMARK_CONTRACT §5). Positive classes: correct iff the extracted option equals gold. Expected-abstain
    classes: correct iff ``abstain`` AND no leaked foreign ref / forbidden marker / open raw access. Error, timeout, missing
    view, unresolved question or an unbreakable tie -> wrong."""
    g, t = as_dict(gold), as_dict(task)
    options = parse_options(t.get("options"))
    expected = resolve_gold_label(g.get("answer"), options)
    expected_abstain = bool(g.get("expected_abstain")) if g.get("expected_abstain") is not None else expected == ABSTAIN
    if expected_abstain:
        expected = ABSTAIN
    disc = disclosures or DisclosureCheck()
    cls = str(g.get("cls") or _first(t, "cls", "class", "task_class", default="") or "")
    ts = TaskScore(task_id=str(g.get("task_id") or t.get("task_id") or ""), cls=cls, expected=expected, extracted=extracted.option, correct=False,
                   reason=extracted.reason, expected_abstain=expected_abstain, disclosures=disc.count, foreign_refs=list(disc.foreign_refs),
                   marker_hits=list(disc.marker_hits), raw_open=list(disc.raw_open), raw_unchecked=len(disc.raw_unchecked),
                   genuine_roots=g.get("genuine_roots"), detail=extracted.detail)
    if isinstance(view, Mapping):
        lat = view.get("latency_s")
        ts.latency_s = float(lat) if isinstance(lat, (int, float)) else None
    if extracted.option is None:
        return ts                                            # error / timeout / tie / unresolved: wrong
    if expected_abstain:
        if extracted.option != ABSTAIN:
            ts.reason = "wrong_abstain"
        elif disc.count:
            ts.reason = "leak"
        else:
            ts.correct, ts.reason = True, "ok"
    else:
        ts.correct = extracted.option == expected
        ts.reason = "ok" if ts.correct else "wrong_option"
    decoys = {resolve_gold_label(x, options) for x in (g.get("decoy_options") or [])}
    if expected_abstain:
        ts.decoy_accepted = extracted.option != ABSTAIN
    elif decoys:
        ts.decoy_accepted = extracted.option in decoys
    if extracted.option != ABSTAIN and extracted.option in extracted.matches:
        ts.independent_roots = extracted.matches[extracted.option]
    # lineage: holders behind the chosen option's supporting references
    gold_holders = {str(h) for h in (g.get("holders") or [])}
    if isinstance(view, Mapping) and extracted.claim_ids and gold_holders:
        claims = {c.claim_id: c for c in view_claims(view)}
        got = sorted({str(r.get("holder_id")) for cid in extracted.claim_ids if cid in claims for r in claims[cid].refs
                      if r.get("holder_id") and (r.get("role") or "supports") == "supports"})
        ts.lineage_holders = got
        ts.lineage_exact = set(got) == gold_holders
        ts.lineage_subset = bool(got) and set(got) <= gold_holders
    return ts


# ---------------------------------------------------------------------------------------------- run scoring
@dataclasses.dataclass
class ClassRow:
    cls: str
    n: int
    correct: int
    accuracy: float
    ci_low: float
    ci_high: float
    errors: int = 0
    abstained: int = 0


@dataclasses.dataclass
class RunScore:
    split: str
    mode: str
    ablation: str | None
    provider_label: str | None
    seed: Any
    size: Any
    n: int
    correct: int
    accuracy: float
    ci_low: float
    ci_high: float
    per_class: list[ClassRow]
    supporting: dict[str, Any]
    disclosures: int
    compliance: bool
    errors: dict[str, int]
    tasks_sha256: str | None
    tasks: list[TaskScore]
    manifest: dict[str, Any]

    def to_dict(self, *, with_tasks: bool = True) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if not with_tasks:
            d.pop("tasks", None)
        return d


PUBLIC_FORBIDDEN_KEYS = frozenset({"answer", "gold", "gold_option", "gold_answer", "expected_abstain", "genuine_roots", "decoy_options",
                                   "forbidden_holder_ids", "forbidden_root_ids", "forbidden_markers", "raw_allowed_holder_ids"})


def public_gold_leaks(tasks: Iterable[Mapping[str, Any]]) -> list[str]:
    """Task ids whose public record carries a gold-only field (isolation breach; the contract forbids gold in public files)."""
    return sorted(str(t.get("task_id")) for t in tasks if PUBLIC_FORBIDDEN_KEYS & set(as_dict(t)))


def load_public(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, Mapping):
        data = data.get("tasks", data)
        if isinstance(data, Mapping):
            data = [{"task_id": k, **as_dict(v)} for k, v in data.items()]
    return [as_dict(x) for x in data]


def _manifest_get(m: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in m and m[k] is not None:
            return m[k]
    return None


def score_tasks(tasks: Sequence[Mapping[str, Any]], gold: Mapping[str, Mapping[str, Any]], views: Mapping[str, Mapping[str, Any] | None], *,
                manifest: Mapping[str, Any] | None = None, tasks_sha256: str | None = None, hypergraph: bool = False,
                raw_authority: Mapping[str, Iterable[str]] | None = None) -> RunScore:
    """Score every task in ``tasks`` (the frozen set). A task with no entry in ``views`` is wrong (``missing_view``)."""
    manifest = dict(manifest or {})
    raw_required = bool(manifest.get("raw_checks_enabled"))
    scores: list[TaskScore] = []
    for task in tasks:
        tid = str(task["task_id"])
        view = views.get(tid)
        ext = extract_answer(view, task, hypergraph=hypergraph)
        scores.append(score_task(ext, gold.get(tid), check_disclosure(view, task, gold.get(tid), raw_checks_required=raw_required,
                                                                      raw_allowed_extra=(raw_authority or {}).get(tid, ())), task=task, view=view))
    return aggregate(scores, manifest=manifest, tasks_sha256=tasks_sha256)


def aggregate(scores: Sequence[TaskScore], *, manifest: Mapping[str, Any], tasks_sha256: str | None = None) -> RunScore:
    n = len(scores)
    k = sum(1 for s in scores if s.correct)
    lo, hi = wilson(k, n)
    by_cls: dict[str, list[TaskScore]] = defaultdict(list)
    for s in scores:
        by_cls[s.cls or "unknown"].append(s)
    rows = []
    for cls in sorted(by_cls):
        ss = by_cls[cls]
        kk = sum(1 for s in ss if s.correct)
        l, h = wilson(kk, len(ss))
        rows.append(ClassRow(cls=cls, n=len(ss), correct=kk, accuracy=kk / len(ss), ci_low=l, ci_high=h,
                             errors=sum(1 for s in ss if s.reason in ("missing_view", "timeout", "error", "unresolved")),
                             abstained=sum(1 for s in ss if s.extracted == ABSTAIN)))
    errors: dict[str, int] = defaultdict(int)
    for s in scores:
        if s.reason in ("missing_view", "timeout", "error", "unresolved", "tie"):
            errors[s.reason] += 1
    disclosures = sum(s.disclosures for s in scores)
    lat = [s.latency_s for s in scores if s.latency_s is not None]
    pos = [s for s in scores if not s.expected_abstain]
    abst = [s for s in scores if s.expected_abstain]
    roots_eval = [s for s in scores if s.genuine_roots is not None and s.independent_roots is not None]
    lin = [s for s in pos if s.lineage_exact is not None]
    usage = manifest.get("model_usage") if isinstance(manifest.get("model_usage"), Mapping) else {}
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), Mapping) else {}
    supporting = {
        "independent_support_correctness": {"evaluated": len(roots_eval), "correct": sum(1 for s in roots_eval if s.independent_roots == s.genuine_roots),
                                            "rate": (sum(1 for s in roots_eval if s.independent_roots == s.genuine_roots) / len(roots_eval)) if roots_eval else None},
        "decoy_acceptance": {"evaluated": sum(1 for s in scores if s.decoy_accepted is not None), "accepted": sum(1 for s in scores if s.decoy_accepted),
                             "rate": (sum(1 for s in scores if s.decoy_accepted) / max(1, sum(1 for s in scores if s.decoy_accepted is not None)))
                             if any(s.decoy_accepted is not None for s in scores) else None},
        "lineage": {"evaluated": len(lin), "exact": sum(1 for s in lin if s.lineage_exact), "subset": sum(1 for s in lin if s.lineage_subset)},
        "unauthorized_disclosures": {"total": disclosures, "foreign_refs": sum(len(s.foreign_refs) for s in scores),
                                     "marker_hits": sum(len(s.marker_hits) for s in scores), "raw_open": sum(len(s.raw_open) for s in scores),
                                     "raw_unchecked": sum(s.raw_unchecked for s in scores), "raw_checks_required": bool(manifest.get("raw_checks_enabled")),
                                     "tasks_with_disclosure": sum(1 for s in scores if s.disclosures)},
        "abstentions": sum(1 for s in scores if s.extracted == ABSTAIN),
        "positive_abstain_rate": (sum(1 for s in pos if s.extracted == ABSTAIN) / len(pos)) if pos else None,
        "latency_s": {"n": len(lat), "p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95), "mean": statistics.fmean(lat) if lat else None},
        "model_calls": usage.get("calls"), "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "holders": {k2: counts.get(k2) for k2 in ("created", "with_records", "activated", "routed")},
        "n_positive": len(pos), "n_expected_abstain": len(abst),
    }
    return RunScore(split=str(manifest.get("split") or ""), mode=str(manifest.get("mode") or ""), ablation=manifest.get("ablation"),
                    provider_label=_manifest_get(manifest, "provider_label", "provider"), seed=manifest.get("seed"), size=manifest.get("size"),
                    n=n, correct=k, accuracy=(k / n) if n else 0.0, ci_low=lo, ci_high=hi, per_class=rows, supporting=supporting,
                    disclosures=disclosures, compliance=disclosures == 0, errors=dict(errors), tasks_sha256=tasks_sha256,
                    tasks=list(scores), manifest=dict(manifest))


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _load_authority(d: Path) -> dict[str, list[str]] | None:
    """``raw_authority.json`` (task id -> holder ids whose raw content the task's asker may legitimately read, i.e. holders they own or lead,
    computed by ``baseline_central.write_raw_authority`` from the organization with the API's own authorization rule). Not gold: it is
    derived from the org directory and says nothing about answers."""
    p = d / "raw_authority.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {str(k): [str(x) for x in v] for k, v in data.items()} if isinstance(data, dict) else None


def score_run(run_dir: str | Path) -> RunScore:
    """Score ``run_dir`` over ALL tasks of its public file. Raises :class:`ScoreError` when the frozen set and the gold
    disagree (a task without gold, or gold for a task that was not issued)."""
    d = Path(run_dir)
    mpath = d / "run_manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    split = manifest.get("split")
    if not split:
        found = sorted(p.name[len("tasks_"):-len(".public.json")] for p in d.glob("tasks_*.public.json"))
        if len(found) != 1:
            raise ScoreError(f"cannot determine the split of {d} (manifest has none; public files: {found})")
        split = found[0]
        manifest["split"] = split
    public = d / f"tasks_{split}.public.json"
    if not public.exists():
        raise ScoreError(f"missing {public.name}")
    tasks = load_public(public)
    sha = _file_sha256(public)
    expected_sha = manifest.get("tasks_sha256") or manifest.get("public_sha256")
    if expected_sha and expected_sha != sha:
        raise ScoreError(f"{public.name} changed after the run (sha256 {sha[:16]} != manifest {str(expected_sha)[:16]})")
    try:
        gold = _load_gold_for_run(d, split)
    except FileNotFoundError:
        raise ScoreError(f"missing gold for split {split!r} in {d}") from None
    ids = [str(t["task_id"]) for t in tasks]
    if len(set(ids)) != len(ids):
        raise ScoreError("duplicate task ids in the public task file")
    missing_gold = [i for i in ids if i not in gold]
    extra_gold = [i for i in gold if i not in set(ids)]
    if missing_gold or extra_gold:
        raise ScoreError(f"task set mismatch: {len(missing_gold)} tasks without gold, {len(extra_gold)} gold entries without a task")
    views: dict[str, Mapping[str, Any] | None] = {}
    for tid in ids:
        vp = d / "views" / f"{tid}.json"
        try:
            views[tid] = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else None
        except (ValueError, OSError):
            views[tid] = {"status": "error", "error": "unreadable view file"}
    authority = _load_authority(d)
    rs = score_tasks(tasks, gold, views, manifest=manifest, tasks_sha256=sha, raw_authority=authority)
    rs.supporting["raw_authority"] = "raw_authority.json" if authority is not None else None
    leaks = public_gold_leaks(tasks)
    rs.supporting["public_gold_leaks"] = leaks
    rs.supporting["isolation_ok"] = not leaks
    return rs


def format_table(rs: RunScore) -> str:
    lines = [f"split={rs.split} mode={rs.mode} ablation={rs.ablation} provider={rs.provider_label} n={rs.n}",
             f"accuracy = {rs.correct}/{rs.n} = {rs.accuracy:.3f}  95% Wilson [{rs.ci_low:.3f}, {rs.ci_high:.3f}]  errors={rs.errors or 0}",
             "disclosures = {total} (foreign refs {foreign_refs}, forbidden markers {marker_hits}, raw open {raw_open}, raw unchecked {raw_unchecked})".format(
                 **{k: rs.supporting["unauthorized_disclosures"][k] for k in ("total", "foreign_refs", "marker_hits", "raw_open", "raw_unchecked")}),
             f"{'class':<24}{'n':>4}{'ok':>4}{'acc':>7}   CI"]
    for r in rs.per_class:
        lines.append(f"{r.cls:<24}{r.n:>4}{r.correct:>4}{r.accuracy:>7.3f}   [{r.ci_low:.2f}, {r.ci_high:.2f}]")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Score a Mycelic-E2E run directory")
    ap.add_argument("run_dir")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(list(argv) if argv is not None else None)
    rs = score_run(ns.run_dir)
    print(json.dumps(rs.to_dict(with_tasks=False), indent=2, default=str) if ns.json else format_table(rs))
    return 0


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
