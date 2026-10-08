"""Deterministic model provider for tests and the demonstration (docs/mycelic/DECISIONS.md D7).

The fake never touches the network. ``complete()`` reads the ``### TASK: <name>`` marker that every task
prompt starts with, pulls the JSON input out of the ``<data>`` block and applies the task's *fake rules*
from :mod:`mycelic.models.tasks` (each ``fake_rules`` summary there is the contract; this module is its
implementation). The rules are deliberately lexical (shared content tokens, numbers, negations) so the
discovery loop, the seed scenario and the tests behave identically on every machine.

Tokenization used by every rule: lower-case, ``[a-z0-9]+``, a small stopword list dropped, then the light
stemmer from :mod:`NeuralGraph.chat_memory.textutil`. Numbers are ``\\d+(\\.\\d+)?``; negation tokens are
``not | no | never | longer`` and are compared separately from content tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from NeuralGraph.chat_memory.textutil import stem

from .base import Completion, ModelError

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_TASK_RE = re.compile(r"^\s*###\s*TASK:\s*([a-z_]+)", re.IGNORECASE)
_DATA_RE = re.compile(r"<data>(.*?)</data>", re.DOTALL)   # first block: untrusted text is escaped and cannot close it early
_SENTENCE_RE = re.compile(r"[.!?\n]")
_SPEAKER_LINE_RE = re.compile(r"^\s*[A-Za-z][\w .'\-]{0,40}:\s*\S")

STOPWORDS = frozenset(
    "the a an and or of to in on for with is are was were be by at as it this that from what which have has had "
    "do does did you your we our they their about any all".split()
)
NEGATIONS = frozenset({"not", "no", "never", "longer"})


# ---------------------------------------------------------------------------------------------- tokens
def raw_tokens(text: Any) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


def content_tokens(text: Any) -> list[str]:
    """Stemmed content tokens: stopwords and negation markers removed, numbers kept as-is."""
    return [t if t[0].isdigit() else stem(t) for t in raw_tokens(text) if t not in STOPWORDS and t not in NEGATIONS]


def content_words(text: Any, *, drop_numbers: bool = False) -> list[str]:
    """Surface forms (unstemmed) of the content tokens, for text that people will read."""
    out = []
    for t in raw_tokens(text):
        if t in STOPWORDS or t in NEGATIONS:
            continue
        if drop_numbers and t[0].isdigit():
            continue
        out.append(t)
    return out


def numbers(text: Any) -> set[str]:
    return set(_NUMBER_RE.findall(str(text or "")))


def has_negation(text: Any) -> bool:
    return any(t in NEGATIONS for t in raw_tokens(text))


def shared_tokens(a: Any, b: Any) -> int:
    return len(set(content_tokens(a)) & set(content_tokens(b)))


# ---------------------------------------------------------------------------------------------- helpers
def _list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (tuple, set)):
        return list(v)
    return [v]


def _dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in _list(v) if isinstance(x, dict)]


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _text_of(item: Any, *keys: str) -> str:
    """First non-empty string among ``keys`` of a dict; a bare string is returned as-is."""
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return _s(item)
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _int(v: Any, default: int) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _domain_mentioned(domain: str, tokens: set[str]) -> bool:
    dt = content_tokens(domain)
    return bool(dt) and all(t in tokens for t in dt)


def _id_of(item: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


class _Groups:
    """Tiny union-find for clustering agreeing responses / recurring discoveries."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def clusters(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return [v for _, v in sorted(out.items())]


# ---------------------------------------------------------------------------------------------- tasks
def identify_gap(inp: dict[str, Any]) -> dict[str, Any]:
    goal = inp.get("goal")
    goal_title = _text_of(goal, "title", "name", "objective") or "the goal"
    raw_obs = inp.get("observations")
    if isinstance(raw_obs, dict):
        # the loop sends observations grouped by kind ({"open_conflicts": [...], "stale_claims": [...], ...})
        observations = [{**item, "kind": item.get("kind") or key} for key, items in raw_obs.items() if isinstance(items, list)
                        for item in items if isinstance(item, dict)]
    else:
        observations = _dicts(raw_obs)
    claims = _dicts(inp.get("existing_claims"))
    domains = [str(d).strip() for d in _list(inp.get("candidate_domains")) if _s(d)]
    max_gaps = _int(inp.get("max_gaps"), 5) or 5

    covered: set[str] = set()
    for c in claims:
        if _s(c.get("status")).lower() != "supported":
            continue
        if c.get("domains"):
            for d in _list(c.get("domains")):
                covered.add(str(d).strip().lower())
            continue
        # a supported claim that carries no domain list covers the candidate domains its text mentions
        toks = set(content_tokens(_text_of(c, "text", "title", "summary")))
        for d in domains:
            if _domain_mentioned(d, toks):
                covered.add(d.lower())

    gaps: list[dict[str, Any]] = []
    for d in domains:
        if d.lower() in covered:
            continue
        gaps.append({
            "description": f"Evidence about {d} relevant to {goal_title}", "kind": "gap", "domains": [d],
            "uncertainty": 0.7, "impact": 0.6, "missing_evidence": 1.0, "information_gain": 0.6,
            "rationale": f"No supported claim covers the domain '{d}' yet.",
        })
    for obs in observations:
        kind = _s(obs.get("kind")).lower()
        if "conflict" not in kind:
            continue
        desc = _text_of(obs, "summary", "text", "description", "title") or f"conflict {_id_of(obs, 'conflict_id', 'ref_id', 'id')}"
        gaps.append({
            "description": desc, "kind": "contradiction", "domains": [str(d) for d in _list(obs.get("domains"))],
            "uncertainty": 0.9, "impact": 0.6, "missing_evidence": 0.3, "information_gain": 0.6,
            "rationale": "Records disagree; an open conflict blocks commitment.",
        })
    for c in claims:
        if _s(c.get("status")).lower() != "hypothesis":
            continue
        gaps.append({
            "description": _text_of(c, "text", "title", "summary") or f"claim {_id_of(c, 'claim_id', 'id')}",
            "kind": "verification", "domains": [str(d) for d in _list(c.get("domains"))],
            "uncertainty": 0.7, "impact": 0.6, "missing_evidence": 0.3, "information_gain": 0.6,
            "rationale": "The claim is a hypothesis: independent support is missing.",
        })
    for obs in observations:
        kind = _s(obs.get("kind")).lower()
        if "stale" not in kind:
            continue
        desc = _text_of(obs, "summary", "text", "description", "claim_text", "title") or f"stale claim {_id_of(obs, 'claim_id', 'ref_id', 'id')}"
        gaps.append({
            "description": desc, "kind": "verification", "domains": [str(d) for d in _list(obs.get("domains"))],
            "uncertainty": 0.7, "impact": 0.6, "missing_evidence": 0.3, "information_gain": 0.6,
            "rationale": "The supporting evidence is stale; re-verification is due.",
        })
    return {"gaps": gaps[:max_gaps]}


def draft_question(inp: dict[str, Any]) -> dict[str, Any]:
    gap = inp.get("gap") if isinstance(inp.get("gap"), dict) else {}
    kind = _s(gap.get("kind")).lower() or "gap"
    domains = [str(d) for d in _list(gap.get("domains")) if _s(d)]
    description = _text_of(gap, "description", "text") or "this topic"
    domain = domains[0] if domains else description
    if kind == "verification":
        question = f"What evidence do you hold that confirms or contradicts: {description}?"
    elif kind == "contradiction":
        question = f"Records disagree about {description}. What do your own records show, with dates?"
    elif kind == "relationship":
        question = f"How is {domain} connected to other blockers you have observed?"
    elif kind in ("hypothesis", "prediction"):
        question = f"If {description}, what would your records show? What do they show?"
    else:
        kind = "gap"
        question = f"What recurring operational blockers related to {domain} have you recorded, and what caused them?"
    days = _int(inp.get("valid_window_days"), 90) or 90
    return {"question": question, "kind": kind, "candidate_domains": domains,
            "uncertainty_note": f"Answer from your own records; answers are considered valid for {days} days."}


def answer_from_evidence(inp: dict[str, Any]) -> dict[str, Any]:
    question = _s(inp.get("question"))
    kept = []
    for item in _dicts(inp.get("evidence")):
        excerpt = _text_of(item, "excerpt", "text", "content")
        if excerpt and shared_tokens(question, excerpt) >= 2:
            kept.append((item, excerpt))
        if len(kept) >= 3:
            break
    if not kept:
        return {"answer": "", "confidence": 0.0, "used_ref_ids": [], "no_evidence": True}
    return {
        "answer": " ".join(e for _, e in kept),
        "confidence": round(min(0.9, 0.5 + 0.15 * len(kept)), 4),
        "used_ref_ids": [_id_of(i, "ref_id", "id") for i, _ in kept if _id_of(i, "ref_id", "id")],
        "no_evidence": False,
    }


def evaluate_responses(inp: dict[str, Any]) -> dict[str, Any]:
    responses = []
    for r in _dicts(inp.get("responses")):
        content = _text_of(r, "content", "answer", "text")
        if not content or bool(r.get("no_evidence")):
            continue
        responses.append((r, content))
    n = len(responses)
    groups = _Groups(n)
    disagreements: list[dict[str, Any]] = []
    in_disagreement: set[int] = set()
    for i in range(n):
        for k in range(i + 1, n):
            a, b = responses[i][1], responses[k][1]
            if shared_tokens(a, b) < 3:
                continue
            na, nb = numbers(a), numbers(b)
            numeric_mismatch = bool(na) and bool(nb) and na != nb
            negation_mismatch = has_negation(a) != has_negation(b)
            if numeric_mismatch or negation_mismatch:
                in_disagreement.update((i, k))
                if numeric_mismatch:
                    summary = f"Numbers differ: {', '.join(sorted(na))} vs {', '.join(sorted(nb))}"
                else:
                    summary = "One response negates what the other states"
                disagreements.append({
                    "summary": summary, "a_text": a, "b_text": b,
                    "a_response_ids": [_id_of(responses[i][0], "response_id", "id")],
                    "b_response_ids": [_id_of(responses[k][0], "response_id", "id")],
                })
            else:
                groups.union(i, k)

    findings: list[dict[str, Any]] = []
    for cluster in groups.clusters():
        members = [responses[i] for i in cluster]
        ref_ids: list[str] = []
        for r, _ in members:
            for ref in _dicts(r.get("refs")):
                rid = _id_of(ref, "ref_id", "id")
                if rid and rid not in ref_ids:
                    ref_ids.append(rid)
        ids = [_id_of(r, "response_id", "id") for r, _ in members]
        if len(members) >= 2:
            longest = max(members, key=lambda m: len(m[1]))[1]
            findings.append({"text": longest, "kind": "finding",
                             "confidence": round(min(0.95, 0.6 + 0.15 * (len(members) - 1)), 4),
                             "supporting_response_ids": ids, "supporting_ref_ids": ref_ids})
        elif cluster[0] not in in_disagreement:
            # one source: an observation (kind finding) whose status the commit gate keeps at hypothesis until another
            # independent source confirms it; kind 'hypothesis' is reserved for conjectures, which never become supported
            findings.append({"text": members[0][1], "kind": "finding", "confidence": 0.5,
                             "supporting_response_ids": ids, "supporting_ref_ids": ref_ids})

    today = _s(inp.get("today"))[:10]
    observed = sorted({_s(ref.get("observed_at"))[:10] for r, _ in responses for ref in _dicts(r.get("refs")) if _s(ref.get("observed_at"))})
    if observed:
        note = f"Evidence observed between {observed[0]} and {observed[-1]}" + (f"; evaluated on {today}." if today else ".")
    else:
        note = "No observation dates were provided."
    return {"findings": findings, "disagreements": disagreements, "relevance": 0.8 if responses else 0.2, "freshness_note": note}


def compose_verification_question(inp: dict[str, Any]) -> dict[str, Any]:
    words = content_words(inp.get("finding_text"), drop_numbers=True)[:5]
    topic = " ".join(words) or _s(inp.get("original_question")) or "this topic"
    return {"question": f"Independently of any other team: what do your own records show about {topic}? Include dates."}


def _finding_status(f: dict[str, Any]) -> str:
    status = _s(f.get("status")).lower()
    kind = _s(f.get("kind")).lower()
    if status:
        return status
    return "hypothesis" if kind == "hypothesis" else "supported"


def synthesize_discovery(inp: dict[str, Any]) -> dict[str, Any]:
    findings = _dicts(inp.get("findings"))
    conflicts = _dicts(inp.get("conflicts"))
    level = _s(inp.get("level")).lower()
    max_followups = _int(inp.get("max_followups"), 3)
    supported = [f for f in findings if _finding_status(f) == "supported"]
    hypotheses = [f for f in findings if _finding_status(f) == "hypothesis"]
    texts = lambda items: [_text_of(f, "text", "title", "summary") for f in items]  # noqa: E731

    first = (supported or findings)
    title = _truncate(_text_of(first[0], "text", "title", "summary"), 80) if first else _truncate(
        _text_of(inp.get("question"), "text", "question") or _text_of(inp.get("goal"), "title") or "No findings", 80)
    parts = []
    if supported:
        parts.append("Supported: " + " ".join(texts(supported)))
    if hypotheses:
        parts.append("Hypotheses: " + " ".join(texts(hypotheses)))
    if conflicts:
        parts.append("Disagreement: " + " ".join(_text_of(c, "summary", "text") for c in conflicts))
    summary = " ".join(parts) or "No findings could be committed for this question yet."
    kind = "contradiction" if conflicts else "finding" if supported else "hypothesis"

    followups: list[dict[str, Any]] = []
    for c in conflicts:
        a = _text_of(c, "a_text", "a") or "record A"
        b = _text_of(c, "b_text", "b") or "record B"
        followups.append({"question": f"Which record is current: {a} or {b}?", "kind": "contradiction",
                          "rationale": "Resolving the disagreement unblocks commitment."})
    for f in hypotheses:
        followups.append({"question": f"What further evidence supports or refutes: {_text_of(f, 'text', 'title', 'summary')}?",
                          "kind": "verification", "rationale": "Independent support would raise confidence."})
    followups = followups[:max_followups]
    strong = any(_float(f.get("confidence")) >= 0.9 for f in findings)
    escalate = bool(conflicts) or (strong and level in ("team", "department"))
    reason = ("Open disagreement needs attention above this unit." if conflicts else
              "A high-confidence finding is relevant one level up." if escalate else "")
    return {"title": title, "summary": summary, "kind": kind, "followup_questions": followups,
            "escalate": escalate, "escalation_reason": reason}


_CONSTRAINT_WORDS = ("budget", "headcount", "capacity", "approval", "licence", "license")


def aggregate_level(inp: dict[str, Any]) -> dict[str, Any]:
    level = _s(inp.get("level")) or "unit"
    unit_name = _s(inp.get("unit_name")) or "this unit"
    discoveries = _dicts(inp.get("discoveries"))
    claims = _dicts(inp.get("claims"))
    conflicts = _dicts(inp.get("conflicts"))

    def d_id(d: dict[str, Any]) -> str:
        return _id_of(d, "discovery_id", "id")

    def d_unit(d: dict[str, Any]) -> str:
        return _text_of(d, "unit_name", "unit", "unit_id")

    n = len(discoveries)
    groups = _Groups(n)
    linked: set[int] = set()
    for i in range(n):
        for k in range(i + 1, n):
            if d_unit(discoveries[i]) == d_unit(discoveries[k]):
                continue
            if shared_tokens(_text_of(discoveries[i], "title"), _text_of(discoveries[k], "title")) >= 3:
                groups.union(i, k)
                linked.update((i, k))
    recurring = []
    for cluster in groups.clusters():
        if len(cluster) < 2:
            continue
        members = [discoveries[i] for i in cluster]
        units: list[str] = []
        for m in members:
            u = d_unit(m)
            if u and u not in units:
                units.append(u)
        recurring.append({"text": _text_of(members[0], "title"), "discovery_ids": [d_id(m) for m in members], "unit_names": units})

    constraints = [{"text": _text_of(c, "text", "title"), "discovery_ids": [], "claim_ids": [_id_of(c, "claim_id", "id")]}
                   for c in claims if any(w in _text_of(c, "text", "title").lower() for w in _CONSTRAINT_WORDS)]
    open_conflicts = [c for c in conflicts if _s(c.get("status")).lower() in ("", "open")]
    conflicting = [{"text": _text_of(c, "summary", "text"), "conflict_ids": [_id_of(c, "conflict_id", "id")]} for c in open_conflicts]
    opportunities = [{"text": _text_of(d, "title"), "discovery_ids": [d_id(d)]} for d in discoveries if _s(d.get("kind")).lower() == "relationship"]
    escalations = [{"text": _text_of(d, "title"), "discovery_ids": [d_id(d)]} for d in discoveries
                   if _s(d.get("status")).lower() == "escalated" or _s(d.get("kind")).lower() == "contradiction"]
    uncertainties = [{"text": _text_of(c, "text", "title"), "claim_ids": [_id_of(c, "claim_id", "id")]} for c in claims
                     if _s(c.get("status")).lower() in ("hypothesis", "stale")]
    decisions = [dict(e) for e in escalations] + [{"text": c["text"], "discovery_ids": [], "conflict_ids": c["conflict_ids"]} for c in conflicting]
    summary = (f"{unit_name} ({level}) has {n} discoveries, {len(recurring)} recurring problems across units and "
               f"{len(conflicting)} open conflicts. {len(constraints)} constraints and {len(uncertainties)} material uncertainties "
               f"are noted. {len(escalations)} escalations and {len(decisions)} decisions need attention.")
    return {"summary": summary, "recurring_problems": recurring, "constraints": constraints, "conflicting_findings": conflicting,
            "opportunities": opportunities, "escalations": escalations, "material_uncertainties": uncertainties,
            "decisions_needed": decisions}


def chat_answer(inp: dict[str, Any]) -> dict[str, Any]:
    question = _s(inp.get("question"))
    scored = []
    for idx, item in enumerate(_dicts(inp.get("context"))):
        text = _text_of(item, "text", "summary", "excerpt", "title")
        n = shared_tokens(question, text) if text else 0
        if n >= 1:
            scored.append((-n, idx, item, text))
    scored.sort(key=lambda t: (t[0], t[1]))
    top = scored[:3]
    if not top:
        return {"answer": "I have no authorized knowledge that answers that.", "citations": []}
    parts = []
    citations = []
    for _, _, item, text in top:
        typ = _s(item.get("type")) or "item"
        cid = _id_of(item, "id", "claim_id", "discovery_id", "memory_id", "ref_id")
        parts.append(f"{text} [{typ}:{cid}]")
        citations.append({"type": typ, "id": cid})
    return {"answer": " ".join(parts), "citations": citations}


def classify_document(inp: dict[str, Any]) -> dict[str, Any]:
    title = _s(inp.get("title"))
    text = _s(inp.get("text"))
    toks = set(content_tokens(title + " " + text))
    domains = [str(d) for d in _list(inp.get("known_domains")) if _s(d) and _domain_mentioned(str(d), toks)]
    body = text or title
    first = _SENTENCE_RE.split(body.strip(), maxsplit=1)[0] if body.strip() else ""
    summary = _truncate(first or title, 140)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    speaker_lines = sum(1 for ln in lines if _SPEAKER_LINE_RE.match(ln))
    conversation = speaker_lines >= 2 or (speaker_lines >= 1 and speaker_lines == len(lines))
    return {"domains": domains or ["general"], "summary": summary, "kind": "conversation" if conversation else "note"}


def record_outcome(inp: dict[str, Any]) -> dict[str, Any]:
    discovery = inp.get("discovery") if isinstance(inp.get("discovery"), dict) else {}
    claims = _dicts(discovery.get("claims")) or _dicts(discovery.get("findings"))
    criteria = _list(inp.get("success_criteria"))
    metric = ""
    if criteria:
        c0 = criteria[0]
        metric = _text_of(c0, "metric", "name") if isinstance(c0, dict) else _s(c0)
    metric = metric or "resolution_time_hours"
    actions = []
    for c in claims:
        if _s(c.get("status")).lower() != "supported":
            continue
        actions.append({"text": f"Address: {_text_of(c, 'text', 'title', 'summary')}", "metric": metric, "target": "reduce",
                        "evidence_claim_ids": [_id_of(c, "claim_id", "id")]})
        if len(actions) >= 3:
            break
    return {"actions": actions}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "identify_gap": identify_gap,
    "draft_question": draft_question,
    "answer_from_evidence": answer_from_evidence,
    "evaluate_responses": evaluate_responses,
    "compose_verification_question": compose_verification_question,
    "synthesize_discovery": synthesize_discovery,
    "aggregate_level": aggregate_level,
    "chat_answer": chat_answer,
    "classify_document": classify_document,
    "record_outcome": record_outcome,
}


def parse_task_prompt(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Return ``(task, input)`` from a rendered task prompt, or ``(None, None)`` when it is not one."""
    first_line = (text or "").lstrip().split("\n", 1)[0]
    m = _TASK_RE.match(first_line)
    if not m:
        return None, None
    task = m.group(1).lower()
    dm = _DATA_RE.search(text)
    if not dm:
        return task, {}
    try:
        data = json.loads(dm.group(1))
    except ValueError:
        return task, {}
    return task, data if isinstance(data, dict) else {}


class FakeProvider:
    """Deterministic :class:`ModelProvider`.

    ``overrides`` maps a task name to ``fn(input, model) -> dict | str | None``: a dict is returned as JSON, a
    string as raw text (so tests can make the model "misbehave"), ``None`` falls through to the built-in rule.
    ``fail_times`` makes the first N calls raise :class:`ModelError` (retry paths); ``latency`` sleeps per call.
    """

    name = "fake"

    def __init__(self, *, fail_times: int = 0, latency: float = 0.0,
                 overrides: dict[str, Callable[[dict[str, Any], str], Any]] | None = None) -> None:
        self.fail_times = fail_times
        self.latency = latency
        self.overrides = dict(overrides or {})
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def complete(self, messages: list[dict[str, str]], *, model: str, max_tokens: int = 1024, temperature: float = 0.0,
                       json_mode: bool = True, timeout: float = 120.0) -> Completion:
        t0 = time.perf_counter()
        user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        task, data = parse_task_prompt(user)
        self.calls.append({"model": model, "task": task, "messages": messages, "json_mode": json_mode})
        if self.latency:
            await asyncio.sleep(self.latency)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ModelError("scripted failure")
        text = self._respond(task, data or {}, user, model, json_mode)
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return Completion(text=text, provider=self.name, model=model, input_tokens=prompt_chars // 4,
                          output_tokens=len(text) // 4, latency_ms=int((time.perf_counter() - t0) * 1000),
                          raw={"task": task})

    def _respond(self, task: str | None, data: dict[str, Any], user: str, model: str, json_mode: bool) -> str:
        if task and task in self.overrides:
            out = self.overrides[task](data, model)
            if isinstance(out, str):
                return out
            if out is not None:
                return json.dumps(out, ensure_ascii=False)
        if task and task in HANDLERS:
            return json.dumps(HANDLERS[task](data), ensure_ascii=False)
        if task:
            logger.warning("fake provider: unknown task %r", task)
            return "{}"
        if json_mode:
            return "{}"
        return "FAKE: " + " ".join(user.split())[:200]

    async def close(self) -> None:
        self.closed = True
