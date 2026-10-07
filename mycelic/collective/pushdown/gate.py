"""The commit gate for pushdown verification: bucketed verdicts in, a status and its exact reasons out.

Ported (adapted, not merged) from origin/claude/mycelic-implementation-vr034p@388aa30, mycelic/knowledge/gate.py and
support.py. Kept: nothing becomes a conclusion without the five checks, each readable in ``checks``:

1. **authorization**: the question is routed under this pack's hash, and each verdict comes from a site the question
   was routed to (re-checked here, never trusted from the verdict);
2. **schema**: the question and every verdict pass the Boundary's closed spec (``edge/egress.py``);
3. **provenance**: support rests on counted confirms, each carrying an evidence reference its site can resolve;
4. **temporal validity**: no look-ahead (the question window is closed at ``as_of``; a verdict received after
   ``as_of`` is not seen) and freshness;
5. **support**: independent sites, roots and reporters against the pack's thresholds; open disagreement.

and the status precedence ``contested`` > ``hypothesis`` (no evidence) > ``stale`` > the support checks, with
``rejected`` when a question-level check fails, and every reason a readable sentence.

Deviations from the port:

* no Authorizer or OrgService: authorisation is "routed for this question_id and the pack hash matches";
* ``as_of`` is injected, where vr034p's ``freshness()`` defaults to ``utcnow()``;
* the inputs are bucketed verdicts (:func:`~..edge.egress.verdict_buckets`), not excerpts or evidence refs;
* a ``'<k'`` roots or reporters bucket counts as its lower bound 1, where vr034p never counts unknown independence
  (every bucket here has a known lower bound);
* a weak confirm (support ``'<k'``, fewer than k yes/yes records) is not counted (D1), at a contributing site and at a
  sibling alike; it is noted;
* a per-verdict failure excludes that verdict rather than rejecting the conclusion; only question-level failures
  reject;
* contested only from a contributing site's refute; a sibling's refute is scoped negative evidence (a note).

Rules of :func:`evaluate`, in order:

* **Question-level** (status ``rejected``): the question's pack hash is not the pack's; the question fails the
  schema; ``as_of`` is before the question's ``as_of`` or its window ends after the last week closed at ``as_of``.
* **Per-verdict exclusions** (records sorted by site, then seq; the first matching check excludes): received after
  ``as_of``; a site not routed this question; another pack hash; another question id; a body that fails the verdict
  schema; another window. An HQ record (a timeout or an error) gets only the first two.
* **Per site** the valid record with the highest seq is used; valid records with more than one distinct sha256 add the
  note that the site answered again. Identical duplicates add nothing.
* **Counted confirms** are confirms whose ``support_bucket`` is not ``'<k'``; their sums use bucket lower bounds.
* **Status**: contested if and only if a contributing site's used verdict refutes; else hypothesis when no site
  confirms at all; else stale when counted confirms exist and their newest week ended more than ``freshness_days``
  before ``as_of`` (exactly ``freshness_days`` is not stale); else one hypothesis reason per failing support check
  (sites, roots, reporters, in that order); else supported.
* **Reasons**: the exclusions, then the status reasons, then the notes in this order of groups, each sorted by site:
  superseded, sibling refutes, sibling confirms (counted), weak confirms, truncated subsets, unknowns.

Pure and deterministic: no clock, no I/O, every iteration sorted; the same inputs in any order give byte-identical
:meth:`GateResult.to_dict` output under any ``PYTHONHASHSEED``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ..edge.egress import SUPPRESSED, bucket_lower, check_artifact, verdict_buckets
from ..edge.weeks import closed_through, local_date, week_sunday
from ..jsonio import canonical_bytes, sha256_hex

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

STATUSES = ("supported", "hypothesis", "stale", "contested", "rejected")
STATUS_RANK: Mapping[str, int] = MappingProxyType({"supported": 4, "hypothesis": 3, "stale": 2, "contested": 1,
                                                   "rejected": 0})
ROLES = ("contributing", "sibling")
SOURCES = ("site", "hq")
HQ_REASONS = ("timeout", "error")
CHECKS = ("authorization", "schema", "provenance", "temporal", "support")
HQ_RECORD_KEYS = ("question_id", "reason", "site", "source", "verdict")


@dataclass(frozen=True)
class GateParams:
    k: int
    labels: tuple[str, ...]
    min_confirming_sites: int
    min_independent_roots: int
    min_independent_reporters: int
    freshness_days: int
    close_lag_days: int

    @classmethod
    def from_pack(cls, pack: "FrozenPack") -> "GateParams":
        pd = pack.pushdown
        return cls(k=pack.egress.k, labels=verdict_buckets(pack), min_confirming_sites=pd.min_confirming_sites,
                   min_independent_roots=pd.min_independent_roots,
                   min_independent_reporters=pd.min_independent_reporters, freshness_days=pd.freshness_days,
                   close_lag_days=pack.egress.close_lag_days)

    def to_dict(self) -> dict[str, Any]:
        return {"k": self.k, "labels": list(self.labels), "min_confirming_sites": self.min_confirming_sites,
                "min_independent_roots": self.min_independent_roots,
                "min_independent_reporters": self.min_independent_reporters, "freshness_days": self.freshness_days,
                "close_lag_days": self.close_lag_days}


@dataclass(frozen=True)
class VerdictRecord:
    """One verdict HQ holds: a site's body (``source`` ``site``) or an HQ record ``{question_id, site, verdict:
    unknown, reason: timeout | error, source: hq}``; ``seq`` numbers a site's records for one question from 1."""

    site: str
    seq: int
    source: str
    body: Any
    received_as_of: str

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValueError("seq must be an int >= 1") from None
        if self.source not in SOURCES:
            raise ValueError("source must be site or hq") from None
        if not isinstance(self.received_as_of, str) or local_date(self.received_as_of) != self.received_as_of:
            raise ValueError("received_as_of must be a calendar date YYYY-MM-DD") from None
        if self.source == "hq":
            body = self.body
            if (not isinstance(body, dict) or sorted(body) != list(HQ_RECORD_KEYS) or body["site"] != self.site
                    or body["verdict"] != "unknown" or body["reason"] not in HQ_REASONS or body["source"] != "hq"):
                raise ValueError("an HQ record is {question_id, site, verdict: unknown, reason, source: hq}") from None


@dataclass(frozen=True)
class GateResult:
    status: str
    reasons: tuple[str, ...]
    checks: Mapping[str, bool | None]
    support: Mapping[str, Any]
    freshness: Mapping[str, Any]
    excluded: tuple[Mapping[str, Any], ...]
    used: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        def plain(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {k: plain(value[k]) for k in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [plain(v) for v in value]
            return value
        return {"status": self.status, "reasons": list(self.reasons), "checks": plain(self.checks),
                "support": plain(self.support), "freshness": plain(self.freshness),
                "excluded": plain(self.excluded), "used": plain(self.used)}


def hq_record_body(question_id: str, site: str, reason: str) -> dict[str, Any]:
    """The body of an HQ record for a route that timed out or failed."""
    if reason not in HQ_REASONS:
        raise ValueError("an HQ record's reason is timeout or error") from None
    return {"question_id": question_id, "site": site, "verdict": "unknown", "reason": reason, "source": "hq"}


def _sha(body: Any) -> str:
    return sha256_hex(canonical_bytes(body))


def _result(status: str, reasons: Sequence[str], checks: Mapping[str, bool | None], *,
            support: Mapping[str, Any] | None = None, freshness: Mapping[str, Any] | None = None,
            excluded: Sequence[Mapping[str, Any]] = (), used: Sequence[Mapping[str, Any]] = ()) -> GateResult:
    empty_support = {"confirming_sites": [], "weak_confirming_sites": [], "refuting_contributing": [],
                     "refuting_siblings": [], "unknown_sites": [], "roots_lb": 0, "reporters_lb": 0, "support_lb": 0,
                     "newest_week": None, "truncated_sites": []}
    return GateResult(status=status, reasons=tuple(reasons),
                      checks=MappingProxyType({c: checks.get(c) for c in CHECKS}),
                      support=MappingProxyType(dict(support if support is not None else empty_support)),
                      freshness=MappingProxyType(dict(freshness if freshness is not None else {
                          "newest_week": None, "age_days": None, "freshness_days": None, "stale": None})),
                      excluded=tuple(MappingProxyType(dict(e)) for e in excluded),
                      used=tuple(MappingProxyType(dict(u)) for u in used))


def _exclusion(pack: "FrozenPack", question: Mapping[str, Any], routes: Mapping[str, str], record: VerdictRecord,
               as_of: str) -> str | None:
    site = record.site
    if record.received_as_of > as_of:
        return f"excluded: {site} verdict arrived after as_of"
    if site not in routes:
        return f"excluded: {site} was not routed this question"
    if record.source == "hq":
        return None
    body = record.body
    if not isinstance(body, dict) or body.get("pack_hash") != question["pack_hash"]:
        return f"excluded: {site} answered under another pack hash"
    if body.get("question_id") != question["question_id"]:
        return f"excluded: {site} answered another question"
    found = check_artifact(pack, site, "verdict", body)
    if found is not None:
        return f"excluded: {site} verdict fails the schema at {found[0]} ({found[1]})"
    if body["window"] != question["window"]:
        return f"excluded: {site} answered for another window"
    return None


def evaluate(pack: "FrozenPack", question: Any, routes: Mapping[str, str], records: Sequence[VerdictRecord], *,
             as_of: str) -> GateResult:
    if not isinstance(as_of, str) or local_date(as_of) != as_of:
        raise ValueError("as_of must be a calendar date YYYY-MM-DD") from None
    params = GateParams.from_pack(pack)
    k = params.k

    # ---- question-level checks: rejected
    if not isinstance(question, dict) or question.get("pack_hash") != pack.config_hash:
        return _result("rejected", ["rejected: the question was made under another pack hash"],
                       {"authorization": False})
    found = check_artifact(pack, "", "question", question)
    if found is not None:
        return _result("rejected", [f"rejected: the question fails the schema at {found[0]} ({found[1]})"],
                       {"authorization": True, "schema": False})
    if as_of < question["as_of"] or question["window"]["end_week"] > closed_through(as_of, params.close_lag_days):
        return _result("rejected", ["rejected: the question window ends after the last week closed at as_of "
                                    "(look-ahead)"], {"authorization": True, "schema": True, "temporal": False})

    # ---- per-verdict exclusions, then the latest valid record per site
    excluded: list[dict[str, Any]] = []
    valid: dict[str, list[VerdictRecord]] = {}
    for record in sorted(records, key=lambda r: (r.site, r.seq, _sha(r.body))):
        reason = _exclusion(pack, question, routes, record, as_of)
        if reason is not None:
            excluded.append({"site": record.site, "seq": record.seq, "reason": reason})
        else:
            valid.setdefault(record.site, []).append(record)
    superseded = sorted(site for site in valid if len({_sha(r.body) for r in valid[site]}) > 1)
    used = []
    latest: dict[str, VerdictRecord] = {}
    for site in sorted(valid):
        record = valid[site][-1]
        latest[site] = record
        body = record.body
        counted = (record.source == "site" and body["verdict"] == "confirm" and body["support_bucket"] != SUPPRESSED)
        used.append({"site": site, "seq": record.seq, "source": record.source, "sha256": _sha(body),
                     "role": routes[site], "verdict": body["verdict"], "counted": counted})

    # ---- classify the used verdicts
    confirming = [u["site"] for u in used if u["counted"]]
    weak = [u["site"] for u in used if u["verdict"] == "confirm" and not u["counted"]]
    refuting_contributing = [u["site"] for u in used if u["verdict"] == "refute" and u["role"] == "contributing"]
    refuting_siblings = [u["site"] for u in used if u["verdict"] == "refute" and u["role"] == "sibling"]
    unknown = [u["site"] for u in used if u["verdict"] == "unknown"]
    truncated = [u["site"] for u in used if u["source"] == "site" and latest[u["site"]].body["truncated"]]
    counted_bodies = [latest[s].body for s in confirming]
    roots_lb = sum(bucket_lower(b["roots_bucket"], pack) for b in counted_bodies)
    reporters_lb = sum(bucket_lower(b["reporters_bucket"], pack) for b in counted_bodies)
    support_lb = sum(bucket_lower(b["support_bucket"], pack) for b in counted_bodies)
    newest = max((b["newest_week"] for b in counted_bodies), default=None)
    age = (date.fromisoformat(as_of) - week_sunday(newest)).days if newest is not None else None
    is_stale = age is not None and age > params.freshness_days

    # ---- status
    status_reasons: list[str]
    if refuting_contributing:
        status = "contested"
        status_reasons = [f"contested: contributing site {site} refutes" for site in refuting_contributing]
    elif not confirming and not weak:
        status = "hypothesis"
        status_reasons = ["hypothesis: no evidence (no site confirms)"]
    elif is_stale:
        status = "stale"
        status_reasons = [f"stale: the newest confirming week {newest} ended more than {params.freshness_days} days "
                          f"before {as_of}"]
    else:
        n = len(confirming)
        status_reasons = []
        if n < params.min_confirming_sites:
            status_reasons.append(f"hypothesis: {n} confirming site(s) with at least {k} records; "
                                  f"min_confirming_sites is {params.min_confirming_sites}")
        if roots_lb < params.min_independent_roots:
            status_reasons.append(f"hypothesis: independent roots, lower bound {roots_lb}; "
                                  f"min_independent_roots is {params.min_independent_roots}")
        if reporters_lb < params.min_independent_reporters:
            status_reasons.append(f"hypothesis: independent reporters, lower bound {reporters_lb}; "
                                  f"min_independent_reporters is {params.min_independent_reporters}")
        if status_reasons:
            status = "hypothesis"
        else:
            status = "supported"
            status_reasons = [f"supported: {n} confirming sites; independent roots, lower bound {roots_lb}; "
                              f"independent reporters, lower bound {reporters_lb}"]

    # ---- notes
    notes = [f"{site} answered again; the latest verdict is used" for site in superseded]
    notes += [f"not observed at {site} (sibling refutes; scoped negative evidence)" for site in refuting_siblings]
    notes += [f"sibling {site} confirms (counted as support)" for site in confirming if routes[site] == "sibling"]
    notes += [f"{site} confirms with fewer than {k} records (not counted)" for site in weak]
    notes += [f"{site} judged a truncated subset (verify_max_records)" for site in truncated]
    for site in unknown:
        body = latest[site].body
        if body.get("reason") is not None:
            notes.append(f"{site} unknown ({body['reason']})")
        elif body.get("quality") == "degraded":
            notes.append(f"{site} unknown (degraded)")
        else:
            notes.append(f"{site} unknown")

    support = {"confirming_sites": confirming, "weak_confirming_sites": weak,
               "refuting_contributing": refuting_contributing, "refuting_siblings": refuting_siblings,
               "unknown_sites": unknown, "roots_lb": roots_lb, "reporters_lb": reporters_lb, "support_lb": support_lb,
               "newest_week": newest, "truncated_sites": truncated}
    freshness = {"newest_week": newest, "age_days": age, "freshness_days": params.freshness_days, "stale": is_stale}
    checks = {"authorization": True, "schema": True, "provenance": bool(confirming), "temporal": not is_stale,
              "support": status == "supported"}
    return _result(status, [e["reason"] for e in excluded] + status_reasons + notes, checks, support=support,
                   freshness=freshness, excluded=excluded, used=used)
