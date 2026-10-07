"""E2: does pushdown verification keep the ranking quality of central reading with no raw text leaving a site?

    python -m mycelic.collective.experiments.e2_pushdown run --x1-prereg FILE --plant FILE --run-id ID
        [--top-n 60] [--min-candidates N] [--site-routing DIR] [--central-routing FILE --central-context-tokens N]
        [--allow-external-raw synthetic] [--data-label synthetic] [--deadline-seconds 600] [--bootstrap-b 10000]
        [--bootstrap-seed 1] [--runs-dir runs] [--allow-dirty] [--dry-run]

It runs on G5's planted synthetic worlds (an X1 prereg and a plant spec) and compares four conditions on the same
candidates (:data:`CONDITION_LABELS`):

* ``stats_only``: the detector's snapshot score;
* ``central_raw``: one central judge reads the raw text of every site's matching records (retrieved with the sites'
  own :func:`~..edge.verify.retrieve`, forwarded-in excluded, at most :data:`CENTRAL_MAX_RECORDS`); raw text crosses
  site boundaries, so it needs ``--allow-external-raw synthetic --data-label synthetic`` and synthetic worlds;
* ``central_allowed``: STRATEGY's R for this task: the same central judge reads only the pack's
  ``central_allowed_fields`` of every record (forwarded copies included, since origin fields are not allowed) whose
  allowed structured values resolve to the entity, received in the window; no narrative, so 0 raw-text bytes;
* ``pushdown``: :meth:`~..pushdown.orchestrator.Orchestrator.verify_stored` at the snapshot's ``as_of``; each site
  answers inside its boundary; scored ``STATUS_RANK[status] * 1_000_000 + support lower bound``.

Checks, each exiting 2 before anything is written: a new run id; the prereg (its pack's four hashes and the G5
evaluation code hash, as the X1 harness pins them); dirty code under the evaluation paths, ``pushdown/`` and this
file without ``--allow-dirty`` (stamped); the world and the plant spec with its binding; both raw-text flags equal to
``synthetic``; a site routing file per prereg site whose judge route (and escalation) stays at ``site:<id>`` or
``any-simulated`` and is not fake; a central routing file whose two tasks name non-site, non-fake endpoints, with
``--central-context-tokens`` (the context the central server gives one request: Ollama's ``num_ctx``,
llama-server's ``-c`` divided by ``--parallel``); ``--bootstrap-b`` at least 1000. Without ``--site-routing`` every
site judges with an in-process fake (the lexical judge); without ``--central-routing`` the central conditions use
fake handlers (rehearsal only: yes/yes per record, ``score = min(100, 30 * min(3, confirming sites) + min(10,
confirming records))``).

Per seed (prereg order): generate and plant the world, run the real pipeline (``evaluate.baselines``), detect run X
at the pipeline's ``as_of`` with the prereg tie salt, and take the detector candidates whose first candidate week is
an evaluation week, by ``(-snapshot score, sha256(tie_salt|key))``, the first ``--top-n``. A candidate is ``true``
when its key is a pattern key and its snapshot week lies in the pattern's found window, ``decoy`` for a decoy key,
else ``background``. Fewer than 300 candidates or 5 seeds exits 2 (the partial run directory is left) unless
``--min-candidates`` allows it, which is stamped ``below_protocol_minimum``. The items are then scored in
``(as_of, seed, key)`` order with the site clock at the item's ``as_of`` (the question budget's day).

**Delivery.** After each item's pushdown, deliveries still running at the deadline get one more
``--deadline-seconds`` (:meth:`~..pushdown.orchestrator.Orchestrator.join_late`) and are then collected at the item's
own ``as_of`` (:meth:`~..pushdown.orchestrator.Orchestrator.collect_late`), so the item is scored on every answer a
site gave and the next question never queues behind a late one. A route that still ends ``unknown`` with reason
``timeout`` or ``error``, or a ``degraded`` judge, is unanswered. A central call that fails (an
:class:`~..inference.errors.InferenceError` after the runtime's retries, repair and escalation) is recorded on its
item by kind and the run goes on; such items are left out of the statistics.

Statistics: :func:`~..stats.paired_ranking_bootstrap` over the items without a central failure, resampled by
candidate key (the same key in several seeds is one cluster), k = 40, the ratio pushdown AP / central_raw AP
(epsilon 0.01), the AP of a random order (``chance``, near the prevalence of true items) and the chance-corrected
ratio ``(AP_pushdown - chance) / (AP_central_raw - chance)``. Raw text: ``central_raw`` counts the UTF-8 bytes of
every text it sent; the others count the narrative shingle overlap (``leakage.scan`` with an empty canary manifest,
per seed) of their payloads (``central_allowed``) and of every crossing transport artifact (``pushdown``: the
questions, the verdict rows, the site ingress and egress logs), each of which must be 0; the in-memory
``central_raw`` payloads must overlap. The
resolvability of every supported conclusion is checked at each counted confirming site with its own verifier.

``measurement`` is false whenever a fake was involved (an endpoint or a ledger row), and then the 0.90 bar verdict is
withheld. It is also withheld (:func:`bar_verdict`, in this order) when a central call failed; when a central_raw
prompt reached the declared context (server-reported prompt tokens plus the task's output budget at or above
``--central-context-tokens``) or its prompt tokens were not reported, since the central reference may then have read a
truncated prompt; when more than :data:`MAX_UNANSWERED_SHARE` of the pushdown routes were unanswered; when the ratio
is undefined; and when the pool is uninformative (central_raw's lift over chance has a 95% interval reaching 0). The
bar passes only when the AP ratio and its interval's low end are at least 0.90, the chance-corrected ratio is at
least 0.90, and pushdown sent 0 raw-text bytes: a constant or random pushdown score keeps none of central_raw's
lift over chance and fails. ``e2.json`` is validated against :data:`E2_SCHEMA` before it is
written, holds no record text, ref or narrative, and its ``content_hash`` excludes ``content_hash``, ``created_at``,
``run_id``, ``paths`` and ``timings``. ``central.ledger.jsonl`` sits beside it; the site ledgers stay in the work
directories. Everything here is synthetic and internal; no figure is a product number.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .. import schemacheck, stats
from ..detect.detectors import detect
from ..edge.egress import read_log
from ..edge.extract import codes_channel, truncate
from ..edge.records import WindowRecord
from ..edge.verify import JUDGE_TASK, SiteVerifier, judge_question, lexical_judge, retrieve
from ..edge.weeks import iso_week, local_date
from ..evaluate.baselines import EvaluationError, Pipeline, run_pipeline, world_weeks
from ..evaluate.harness import (EVAL_CODE_FILES, EVAL_DIRTY_PATHS, arr_schema, check_clean, check_world,
                                eval_code_hash, load_checked_plant, obj_schema, pinned_differences, prereg_pack,
                                read_prereg, typed_schema, union_problems)
from ..evaluate.plant import PlantError, PlantSpec, labels_doc, plant
from ..inference.errors import KINDS as ERROR_KINDS
from ..inference.errors import InferenceBoundaryError, InferenceError
from ..inference.fake import FakeProvider
from ..inference.ledger import read_ledger
from ..inference.routing import ConfigError, Endpoint, RoutingConfig, is_site_boundary, load_routing, parse_routing
from ..inference.runtime import Runtime
from ..inference.tasks import TaskSpec
from ..jsonio import StrictJsonError, canonical_bytes, sha256_hex, strict_load
from ..leakage import CANARY_PREFIX, Artifact, LeakageError, Manifest, scan
from ..packs.canonical import Canonicaliser
from ..packs.generator import GeneratorError, generate
from ..packs.loader import FrozenPack
from ..pushdown.gate import STATUS_RANK, STATUSES
from ..pushdown.orchestrator import Orchestrator
from ..pushdown.questions import PushdownError, question_window
from .common import (ROOT, DryRun, UsageError, check_run_id, code_commit, code_dirty, code_files, code_hash, fail,
                     measurement_flag, run_dir, utc_clock, write_json_atomic)

CLI = "experiments.e2_pushdown"
KIND = "e2"
SCHEMA_VERSION = 1
E2_CODE_FILES = (*EVAL_CODE_FILES, "mycelic/collective/pushdown/*.py", "mycelic/collective/inference/*.py",
                 "mycelic/collective/leakage.py", "mycelic/collective/experiments/e2_pushdown.py")
E2_DIRTY_PATHS = (*EVAL_DIRTY_PATHS, "mycelic/collective/pushdown", "mycelic/collective/experiments/e2_pushdown.py")
CONDITIONS = ("stats_only", "central_raw", "central_allowed", "pushdown")
CONDITION_LABELS = {
    "stats_only": "stats_only: the detector's own snapshot score (model-free detection over k-suppressed cells); no "
                  "question is asked",
    "central_raw": "central_raw: one central judge reads the raw record text of every site's matching records in the "
                   "question window; raw text crosses site boundaries, so this condition runs on synthetic worlds only",
    "central_allowed": "central_allowed: STRATEGY's R for this task: one central judge reads only the fields policy "
                       "allows to leave (central_allowed_fields: site, received date, codes and structured entity "
                       "values), never narrative",
    "pushdown": "pushdown: the contributing and sibling sites answer a narrow question from their own records inside "
                "their boundaries; only bucketed verdicts cross; ranked by gate status, then the support lower bound",
}
CENTRAL_TASKS = ("judge_candidate_raw", "judge_candidate_allowed")
LABELS = ("true", "decoy", "background")
TOP_K = 40
PROTOCOL_MIN_CANDIDATES = 300
PROTOCOL_MIN_SEEDS = 5
CENTRAL_MAX_RECORDS = 400
BAR = 0.90
EPSILON = 0.01
MAX_UNANSWERED_SHARE = 0.05
UNANSWERED_REASONS = ("timeout", "error", "degraded")
CENTRAL_MAX_TOKENS = 64
MIN_BOOTSTRAP_B = 1000
STATUS_SCALE = 1_000_000
RAW_FLAGS_MESSAGE = ("central_raw sends raw record text across site boundaries: pass --allow-external-raw synthetic "
                     "and --data-label synthetic (synthetic worlds only)")
UNKNOWN_REASONS = ("none", "degraded", "budget", "no_secret", "timeout", "error")
CONTENT_HASH_EXCLUDES = ("$.content_hash", "$.created_at", "$.run_id", "$.paths", "$.timings")
SCAN_METHOD = ("leakage.scan narrative shingles (24 characters) against each seed's narratives, with an empty "
               "canary manifest")
NOTES = [
    "Synthetic and same-author: the worlds, the plant spec, the pack and the code were written by one author. "
    "Internal only; never shown to buyers. With a fake judge anywhere nothing in this file is a measurement.",
    "central_allowed is STRATEGY's R for this task; central_raw is the unrestricted central reference and moves raw "
    "text, which only synthetic worlds allow.",
    "Cross-site copies without an origin marker count as independent roots at each site, so pushdown can reach "
    "supported on them (a known hard case, as in G5).",
    "The fake central handlers count yes/yes records with the same deterministic judge the sites use; they are a "
    "rehearsal of the plumbing, not a comparator.",
    "The candidate pool is mostly true items on planted worlds, so a random order already has a high AP (chance). "
    "The bar therefore also needs the chance-corrected ratio, and the bootstrap resamples candidate keys, not items.",
]


def e2_code_files() -> list[str]:
    return code_files(E2_CODE_FILES)


def e2_code_hash() -> str:
    return code_hash([ROOT / p for p in e2_code_files()])


def dirty_state() -> bool | str:
    return code_dirty(list(E2_DIRTY_PATHS))


# --------------------------------------------------------------------------------------------------- schema

_O, _A, _T = obj_schema, arr_schema, typed_schema
_HEX = _T("string", pattern="[0-9a-f]{64}")
_STR = _T("string")
_NSTR = _T("string", nullable=True)
_NAT = _T("integer", minimum=0)
_NNAT = _T("integer", nullable=True, minimum=0)
_POS = _T("integer", minimum=1)
_NUM = _T("number")
_NNUM = _T("number", nullable=True)
_BOOL = _T("boolean")
_WEEK = _T("string", pattern="[0-9]{4}-W[0-9]{2}")


def _const(value: Any) -> dict[str, Any]:
    return _T({bool: "boolean", int: "integer", str: "string"}[type(value)], const=value)


def _enum(values: Sequence[str]) -> dict[str, Any]:
    return _T("string", enum=list(values))


_CONDITION = _O({"ap": _NNUM, "ap_ci_low": _NNUM, "ap_ci_high": _NNUM, "ap_undefined": _NAT, "precision_at_k": _NUM,
                 "p_ci_low": _NNUM, "p_ci_high": _NNUM})
E2_SCHEMA = schemacheck.compile(_O({
    "kind": _const("e2_pushdown"), "schema_version": _const(SCHEMA_VERSION), "run_id": _STR, "created_at": _STR,
    "stamps": _O({"synthetic": _const(True), "internal_only": _const(True), "measurement": _BOOL,
                  "same_author_pack": _BOOL, "below_protocol_minimum": _BOOL, "allow_dirty": _BOOL,
                  "secret_mode": _const("seeded-demo"), "data_label": _const("synthetic")}),
    "hashes": _O({name: _HEX for name in ("config_hash", "vocabulary_hash", "detector_hash", "fixtures_hash",
                                          "code_hash", "x1_prereg_sha256", "plant_sha256")}),
    # code_dirty is true, false or "unknown": declared boolean-or-null, "unknown" checked by union_problems
    "code": _O({"code_commit": _STR, "code_dirty": _T("boolean", nullable=True), "code_files": _A(_STR)}),
    "endpoints": _O({"site_judge": _enum(["fake", "routing"]), "central_judge": _enum(["fake", "routing"]),
                     "site_routing": _A(_O({"site": _STR, "routing_sha256": _T("string", nullable=True,
                                                                               pattern="[0-9a-f]{64}")})),
                     "central_routing_sha256": _T("string", nullable=True, pattern="[0-9a-f]{64}"),
                     "providers": _A(_STR)}),
    "settings": _O({"top_n": _POS, "min_candidates": _NNAT, "deadline_seconds": _NUM, "bootstrap_b": _POS,
                    "central_context_tokens": _NNAT, "max_unanswered_share": _NUM,
                    "bootstrap_seed": _T("integer"), "k": _const(TOP_K), "central_max_records":
                        _const(CENTRAL_MAX_RECORDS), "epsilon": _NUM, "bar": _NUM, "tie_salt": _STR,
                    "seeds": _A(_NAT, 1), "eval_from_week": _WEEK, "eval_to_week": _WEEK, "grace_weeks": _NAT}),
    "candidates": _O({"total": _NAT, "seeds": _NAT,
                      "by_seed": _A(_O({"seed": _NAT, "detected": _NAT, "candidates": _NAT})),
                      "by_label": _O({label: _NAT for label in LABELS})}),
    "condition_labels": _O({c: _const(CONDITION_LABELS[c]) for c in CONDITIONS}),
    "bootstrap": _O({"n": _NAT, "n_relevant": _NAT, "B": _POS, "seed": _T("integer"), "k": _POS, "method": _STR,
                     "clusters": _const("candidate key"), "n_clusters": _NAT, "excluded_central_failures": _NAT}),
    "chance": _O({"prevalence": _NUM, "ap": _NNUM, "ap_ci_low": _NNUM, "ap_ci_high": _NNUM}),
    "conditions": _O({c: _CONDITION for c in CONDITIONS}),
    "ratio": _O({"numerator": _const("pushdown"), "denominator": _const("central_raw"), "estimate": _NNUM,
                 "ci_low": _NNUM, "ci_high": _NNUM, "undefined": _NAT, "epsilon": _NUM, "lift_estimate": _NNUM,
                 "lift_ci_low": _NNUM, "lift_ci_high": _NNUM, "lift_undefined": _NAT, "denominator_lift": _NNUM,
                 "denominator_lift_ci_low": _NNUM, "denominator_lift_ci_high": _NNUM}),
    "central": _O({"failures": _O({c: _O({kind: _NAT for kind in ERROR_KINDS})
                                    for c in ("central_raw", "central_allowed")}),
                   "failed_items": _NAT, "context_tokens": _NNAT, "prompt_tokens_max": _NNAT,
                   "prompt_tokens_median": _NNUM, "prompt_tokens_unreported": _NAT, "at_context_limit": _NAT}),
    "raw_text_bytes": _O({c: _NAT for c in CONDITIONS}),
    "raw_text_scan": _O({"method": _const(SCAN_METHOD), "central_raw": _NAT, "central_allowed": _NAT,
                         "pushdown": _NAT, "artifacts": _O({c: _O({"bytes": _NAT, "items": _NAT})
                                                            for c in ("central_raw", "central_allowed", "pushdown")})}),
    "pushdown": _O({"statuses": _O({s: _NAT for s in STATUSES}),
                    "verdicts": _O({"confirm": _NAT, "refute": _NAT,
                                    "unknown": _O({r: _NAT for r in UNKNOWN_REASONS})}),
                    "routes": _O({"contributing": _NAT, "sibling": _NAT}), "budget_unknowns": _NAT, "timeouts": _NAT,
                    "errors": _NAT, "late_collected": _NAT, "unanswered": _NAT, "unanswered_share": _NNUM,
                    "resolvability": _O({"supported": _NAT, "resolvable": _NAT, "share": _NNUM,
                                         "extraction_miss_confirmations": _NAT})}),
    "verdict": _O({"bar": _NUM, "ratio": _NUM, "ratio_at_least_bar": _BOOL, "ci_low_at_least_bar": _BOOL,
                   "lift_ratio": _NNUM, "lift_ratio_at_least_bar": _BOOL, "pushdown_raw_text_bytes_zero": _BOOL,
                   "pass": _BOOL}, nullable=True),
    "verdicts_withheld": _BOOL, "withheld_reason": _NSTR,
    "items": _A(_O({"seed": _NAT, "key": _STR, "label": _enum(LABELS), "as_of": _STR, "question_id": _HEX,
                    "conclusion_id": _STR, "status": _enum(STATUSES),
                    "scores": _O({c: _NNUM for c in CONDITIONS}), "central_raw_records": _NAT,
                    "central_raw_truncated": _BOOL, "central_allowed_records": _NAT,
                    "central_errors": _O({c: _NSTR for c in ("central_raw", "central_allowed")}),
                    "central_raw_prompt_tokens": _NNAT, "late_collected": _NAT})),
    "notes": _A(_STR),
    "paths": _O({name: _STR for name in ("prereg", "plant", "run_dir", "workdir", "central_ledger")}),
    "timings": _O({"total_s": _NUM, "per_seed_pipeline_s": _A(_NUM), "score_s": _NUM}),
    "content_hash_excludes": _A(_STR), "content_hash": _HEX}))


def e2_problems(doc: Any) -> list[tuple[str, str]]:
    return union_problems(E2_SCHEMA, doc, ("code", "code_dirty"))


def content_hash(doc: Mapping[str, Any]) -> str:
    excluded = {p[2:] for p in CONTENT_HASH_EXCLUDES}
    return sha256_hex(canonical_bytes({k: doc[k] for k in sorted(doc) if k not in excluded}))


# --------------------------------------------------------------------------------------------------- the central judge

def central_task(name: str) -> TaskSpec:
    instructions = (
        "Score how strongly these records, taken together, show that the named entity had the named predicate in "
        "the window: 0 for no support, up to 100 for strong support from several sites. Count a record only when "
        "it states the predicate for that entity; a record saying that it did not happen counts against.")
    return TaskSpec(name, "raw" if name == CENTRAL_TASKS[0] else "structured", instructions, CENTRAL_MAX_TOKENS)


CENTRAL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["score"],
                  "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}}}


def _score(hits: Sequence[Mapping[str, Any]]) -> int:
    return min(100, 30 * min(3, len({h["site"] for h in hits})) + min(10, len(hits)))


def fake_central_raw(pack: FrozenPack, master_data: Mapping[str, Mapping[str, Sequence[str]]]) -> Callable[..., Any]:
    """Rehearsal only: yes/yes records by each record's own site judge."""
    judges = {site: lexical_judge(pack, Canonicaliser(pack, known=master_data[site])) for site in sorted(master_data)}

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        hits = []
        for r in payload["records"]:
            reply = judges[r["site"]]({"question": payload["question"], "record": {
                "language": r["language"], "codes": r["codes"], "entities": r["entities"], "text": r["text"]}})
            if reply["mentions_entity"] == "yes" and reply["describes_predicate"] == "yes":
                hits.append(r)
        return {"score": _score(hits)}

    return handler


def fake_central_allowed(pack: FrozenPack,
                         master_data: Mapping[str, Mapping[str, Sequence[str]]]) -> Callable[..., Any]:
    """Rehearsal only: a record counts when a structured value resolves to the entity and a code maps to the
    predicate."""
    canonicalisers = {site: Canonicaliser(pack, known=master_data[site]) for site in sorted(master_data)}
    types = sorted(pack.mapping()["entities"])

    def handler(payload: Mapping[str, Any]) -> dict[str, Any]:
        q = payload["question"]
        hits = []
        for r in payload["records"]:
            view = {"codes": r["codes"], "entities": {t: list(r["entities"].get(t, [])) for t in types}}
            codes = codes_channel(view, pack, canonicalisers[r["site"]])
            if ((q["entity_type"], q["entity_id"]) in {(e.entity_type, e.entity_id) for e in codes.entities}
                    and q["predicate"] in codes.predicates):
                hits.append(r)
        return {"score": _score(hits)}

    return handler


# --------------------------------------------------------------------------------------------------- the run

class SimClock:
    """The clock every component of a run receives; set to each item's ``as_of``."""

    def __init__(self, value: str) -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


@dataclass
class _Seed:
    seed: int
    workdir: Path
    pipeline: Pipeline
    master_data: Mapping[str, Mapping[str, Sequence[str]]]
    records: list[dict[str, Any]]
    run_id: str
    candidates: list[dict[str, Any]]
    detected: int
    verifiers: dict[str, SiteVerifier]
    runtimes: list[Runtime]
    orchestrator: Orchestrator
    allowed: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = field(default_factory=dict)
    mentions: dict[str, dict[tuple[str, str], frozenset[str]]] = field(default_factory=dict)
    raw_payloads: list[bytes] = field(default_factory=list)
    allowed_payloads: list[bytes] = field(default_factory=list)


@dataclass
class _Settings:
    pack: FrozenPack
    prereg: Mapping[str, Any]
    prereg_sha: str
    spec: PlantSpec
    site_routing: dict[str, RoutingConfig] | None
    central_routing: RoutingConfig | None
    dirty: bool | str


def _site_routing(directory: str, site_ids: Sequence[str], *, check_env: bool) -> dict[str, RoutingConfig]:
    out = {}
    for sid in site_ids:
        path = Path(directory) / f"{sid}.json"
        try:
            config = load_routing(path, tasks=[JUDGE_TASK], check_env=check_env)
        except ConfigError as err:
            raise UsageError(f"site routing {sid}: {err}") from None
        route = config.routes[JUDGE_TASK]
        for name in (route.endpoint, route.escalate_to):
            if name is None:
                continue
            endpoint = config.endpoints[name]
            if endpoint.boundary not in (f"site:{sid}", "any-simulated") or endpoint.provider == "fake":
                raise UsageError(f"the judge route of site {sid} may leave the site") from None
        out[sid] = config
    return out


def _central_routing(path: str, *, check_env: bool) -> RoutingConfig:
    try:
        config = load_routing(path, tasks=list(CENTRAL_TASKS), check_env=check_env)
    except ConfigError as err:
        raise UsageError(f"central routing: {err}") from None
    for task in CENTRAL_TASKS:
        route = config.routes[task]
        for name in (route.endpoint, route.escalate_to):
            if name is None:
                continue
            endpoint = config.endpoints[name]
            if is_site_boundary(endpoint.boundary) or endpoint.provider == "fake":
                raise UsageError("the central routes must name non-site, non-fake endpoints") from None
    return config


def _check(args: argparse.Namespace, dry: DryRun | None) -> _Settings | None:
    """Every refusal before anything is written; None when a dry run lacks an input."""
    run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
    if args.allow_external_raw != "synthetic" or args.data_label != "synthetic":
        raise UsageError(RAW_FLAGS_MESSAGE) from None
    if args.bootstrap_b < MIN_BOOTSTRAP_B:
        raise UsageError(f"--bootstrap-b must be >= {MIN_BOOTSTRAP_B}") from None
    if args.top_n < 1 or (args.min_candidates is not None and args.min_candidates < 1):
        raise UsageError("--top-n and --min-candidates must be >= 1") from None
    if not (math.isfinite(args.deadline_seconds) and 0 < args.deadline_seconds <= 3600):
        raise UsageError("--deadline-seconds must be in (0, 3600]") from None
    context = args.central_context_tokens
    if args.central_routing and (context is None or context <= CENTRAL_MAX_TOKENS):
        raise UsageError("--central-routing needs --central-context-tokens: the context one request gets on the "
                         "central server (Ollama num_ctx, llama-server -c / --parallel), above "
                         f"{CENTRAL_MAX_TOKENS}") from None
    if not args.central_routing and context is not None:
        raise UsageError("--central-context-tokens goes with --central-routing") from None
    missing = [(what, p) for what, p in (("prereg file", args.x1_prereg), ("plant file", args.plant),
                                         ("central routing file", args.central_routing)) if p and not Path(p).is_file()]
    if args.site_routing is not None and not Path(args.site_routing).is_dir():
        missing.append(("site routing directory", args.site_routing))
    if dry is not None and missing:
        for what, p in missing:
            dry.need(f"{what} {p}")
        return None
    prereg, data = read_prereg(args.x1_prereg)
    pack = prereg_pack(prereg)
    differing = pinned_differences(prereg, pack, eval_code_hash())
    if differing:
        raise UsageError(f"pinned values differ from the prereg: {', '.join(differing)}") from None
    if not {"site", "received_date"} <= set(pack.egress.central_allowed_fields):
        raise UsageError("central_allowed needs site and received_date among the pack's central_allowed_fields") \
            from None
    dirty = dirty_state()
    check_clean(args.allow_dirty, dry, dirty)
    check_world(pack, prereg)
    spec = load_checked_plant(args.plant, pack, prereg)
    if spec.prereg_sha256 is not None and spec.prereg_sha256 != sha256_hex(data):
        raise UsageError("the plant spec's prereg_sha256 is not the sha256 of this prereg file") from None
    site_routing = (_site_routing(args.site_routing, prereg["world"]["site_ids"], check_env=dry is None)
                    if args.site_routing is not None else None)
    central = _central_routing(args.central_routing, check_env=dry is None) if args.central_routing else None
    return _Settings(pack=pack, prereg=prereg, prereg_sha=sha256_hex(data), spec=spec, site_routing=site_routing,
                     central_routing=central, dirty=dirty)


def _fake_runtime(pack: FrozenPack, boundary: str, ledger: Path, run_id: str, clock: SimClock,
                  handlers: Mapping[str, Callable[..., Any]], **kw: Any) -> Runtime:
    config = parse_routing({"schema_version": 1, "endpoints": {"fake": {"provider": "fake", "boundary": boundary}},
                            "routes": {task: {"endpoint": "fake"} for task in handlers}}, allow_fake=True)
    provider = FakeProvider()
    for task in sorted(handlers):
        provider.register(task, handlers[task])
    return Runtime(config, boundary=boundary, ledger_path=ledger, run_id=run_id, clock=clock, data_label="synthetic",
                   allow_fake=True, fake=provider, sleep=lambda s: None, environ={}, **kw)


def _label(key: str, week: str, labels: Mapping[str, Any]) -> str:
    for p in labels["patterns"]:
        if p["key"] == key and p["found_from"] <= week <= p["found_to"]:
            return "true"
    if any(key in d["keys"] for d in labels["decoys"]):
        return "decoy"
    return "background"


def _seed(cfg: _Settings, args: argparse.Namespace, seed: int, workdir: Path, clock: SimClock) -> _Seed:
    pack, prereg = cfg.pack, cfg.prereg
    w, ev = prereg["world"], prereg["evaluation"]
    weeks = world_weeks(w["start"], w["weeks"])
    world = generate(pack, seed, w["sites"], w["weeks"])
    records = list(world.records) + list(plant(world, cfg.spec, pack).records)
    pipeline = run_pipeline(pack, records, site_ids=w["site_ids"], master_data=world.master_data, weeks=weeks,
                            workdir=workdir)
    runtimes: list[Runtime] = []
    try:
        salt = prereg["tie_salt"]
        result = detect(pipeline.store, as_of=pipeline.as_of, run_channel="X", tie_salt=salt)
        pipeline.store.save_run(result)
        detected = [c for c in result["candidates"] if c["first_candidate_week"] is not None
                    and ev["eval_from_week"] <= c["first_candidate_week"] <= ev["eval_to_week"]]
        detected.sort(key=lambda c: (-c["snapshot"]["score"], sha256_hex(f"{salt}|{c['key']}")))
        verifiers: dict[str, SiteVerifier] = {}
        for sid in w["site_ids"]:
            ledger = workdir / "edge" / f"site-{sid}.ledger.jsonl"
            site = pipeline.sites[sid]
            if cfg.site_routing is not None:
                runtime = Runtime(cfg.site_routing[sid], boundary=f"site:{sid}", ledger_path=ledger,
                                  run_id=args.run_id, clock=clock, data_label="synthetic", simulation=True)
            else:
                runtime = _fake_runtime(pack, f"site:{sid}", ledger, args.run_id, clock,
                                        {JUDGE_TASK: lexical_judge(pack, site.canonicaliser)})
            runtimes.append(runtime)
            verifiers[sid] = SiteVerifier(site, runtime=runtime, clock=clock, demo_seed=seed)
        orchestrator = Orchestrator(pipeline.store, handlers={sid: v.answer for sid, v in verifiers.items()},
                                    clock=clock, deadline_seconds=args.deadline_seconds)
    except BaseException:
        for runtime in runtimes:
            runtime.close()
        pipeline.close()
        raise
    out = _Seed(seed=seed, workdir=workdir, pipeline=pipeline, master_data=world.master_data, records=records,
                run_id=result["run_id"],
                candidates=detected[:args.top_n], detected=len(detected), verifiers=verifiers, runtimes=runtimes,
                orchestrator=orchestrator)
    canonicalisers = {sid: Canonicaliser(pack, known=world.master_data[sid]) for sid in w["site_ids"]}
    for record in records:
        view = allowed_view(pack, record)
        day = local_date(view["received_date"])
        resolved = codes_channel(view, pack, canonicalisers[view["site"]]).entities
        for e in resolved:
            out.allowed.setdefault((e.entity_type, e.entity_id), []).append((iso_week(day), view))
    return out


def allowed_view(pack: FrozenPack, record: Mapping[str, Any]) -> dict[str, Any]:
    """What ``central_allowed`` sees of a record: ``site``, ``received_date``, ``codes`` and the ``entities.<type>``
    values, each only when it is among the pack's ``central_allowed_fields`` (an entity type that is not is empty).
    ``record`` is read by subscript only, for allowed fields: never iterated, never ``.get``, never its narrative."""
    allowed = frozenset(pack.egress.central_allowed_fields)
    types = sorted(pack.mapping()["entities"])
    return {"site": record["site"], "received_date": record["received_date"],
            "codes": sorted(record["codes"]) if "codes" in allowed else [],
            "entities": {t: list(record["entities"][t]) if f"entities.{t}" in allowed else [] for t in types}}


def _judge_central(runtime: Runtime, name: str, payload: dict[str, Any], ref: str) -> tuple[int | None, str | None]:
    """``(score, None)``, or ``(None, error kind)`` when the call failed after the runtime's retries, repair and
    escalation; a boundary refusal propagates (a configuration error, never a result)."""
    try:
        reply = runtime.run(central_task(name), payload, CENTRAL_SCHEMA, ref=ref)
    except InferenceBoundaryError:
        raise
    except InferenceError as err:
        return None, err.kind
    return reply["score"], None


def _central_raw(s: _Seed, cfg: _Settings, runtime: Runtime, params: Mapping[str, str], window: Mapping[str, str],
                 ref: str) -> tuple[int | None, int, bool, int, str | None]:
    """(score, records, truncated, text bytes sent, error kind)."""
    pack = cfg.pack
    pool: list[tuple[str, WindowRecord]] = []
    truncated = False
    for sid in sorted(s.pipeline.sites):
        site = s.pipeline.sites[sid]
        found, cut = retrieve(site.store, site.canonicaliser, entity_type=params["entity_type"],
                              entity_id=params["entity_id"], window=window, cap=CENTRAL_MAX_RECORDS,
                              cache=s.mentions.setdefault(sid, {}))
        truncated = truncated or cut
        pool += [(sid, r) for r in found]
    pool.sort(key=lambda item: item[1].iso_week, reverse=True)
    truncated = truncated or len(pool) > CENTRAL_MAX_RECORDS
    pool = pool[:CENTRAL_MAX_RECORDS]
    if not pool:
        return 0, 0, truncated, 0, None
    texts = [truncate(r.narrative, pack.extraction.max_input_chars)[0] for _, r in pool]
    payload = {"question": judge_question(pack, params), "window": dict(window),
               "records": [{"site": sid, "week": r.iso_week, "codes": sorted(r.codes),
                            "entities": {t: list(r.structured[t]) for t in sorted(r.structured)},
                            "language": r.language, "text": text} for (sid, r), text in zip(pool, texts)]}
    s.raw_payloads.append(canonical_bytes(payload))
    score, error = _judge_central(runtime, CENTRAL_TASKS[0], payload, ref)
    return score, len(pool), truncated, sum(len(t.encode("utf-8")) for t in texts), error


def _central_allowed(s: _Seed, cfg: _Settings, runtime: Runtime, params: Mapping[str, str],
                     window: Mapping[str, str], ref: str) -> tuple[int | None, int, str | None]:
    """(score, records, error kind)."""
    rows = [view for week, view in s.allowed.get((params["entity_type"], params["entity_id"]), ())
            if window["start_week"] <= week <= window["end_week"]]
    if not rows:
        return 0, 0, None
    payload = {"question": judge_question(cfg.pack, params), "window": dict(window), "records": rows}
    s.allowed_payloads.append(canonical_bytes(payload))
    score, error = _judge_central(runtime, CENTRAL_TASKS[1], payload, ref)
    return score, len(rows), error


def _resolvable(s: _Seed, body: Mapping[str, Any]) -> tuple[bool, int]:
    """(every ref of this counted confirm resolves at its site to its own in-window confirming records, the
    verdict's extraction misses)."""
    site = s.pipeline.sites[body["site"]]
    verifier = s.verifiers[body["site"]]
    resolution = verifier.resolve(body["evidence_ref"])
    audit = verifier.audit(body["verdict_id"])
    if resolution is None or audit is None or resolution.verdict != "confirm":
        return False, 0
    window = body["window"]
    own = {r.record_ref for r in site.store.window_records(window["start_week"], window["end_week"])}
    ok = (bool(resolution.record_refs) and len(resolution.record_refs) == audit.confirming
          and all(ref in own for ref in resolution.record_refs))
    return ok, audit.extraction_misses


def _scan(s: _Seed, cfg: _Settings) -> dict[str, dict[str, int]]:
    """Narrative shingle overlap per class: the central payloads and every crossing pushdown artifact."""
    workdir = s.workdir
    artifacts = [Artifact("central_raw", f"seed-{s.seed}/central_raw#{i}", data=d)
                 for i, d in enumerate(s.raw_payloads, start=1)]
    artifacts += [Artifact("central_allowed", f"seed-{s.seed}/central_allowed#{i}", data=d)
                  for i, d in enumerate(s.allowed_payloads, start=1)]
    hq = workdir / "hq"
    if (hq / "questions.jsonl").exists():
        artifacts.append(Artifact("pushdown", f"seed-{s.seed}/hq/questions.jsonl", path=hq / "questions.jsonl"))
    for number, row in enumerate(read_log(hq / "receive.jsonl"), start=1):
        if row["artifact_type"] == "verdict":
            artifacts.append(Artifact("pushdown", f"seed-{s.seed}/hq/receive.jsonl#{number}",
                                      data=canonical_bytes(row["body"])))
    for sid in sorted(s.pipeline.sites):
        for name in (f"site-{sid}.ingress.jsonl", f"site-{sid}.egress.jsonl"):
            if (workdir / "edge" / name).exists():
                artifacts.append(Artifact("pushdown", f"seed-{s.seed}/edge/{name}", path=workdir / "edge" / name))
    manifest = Manifest(pack_id=cfg.pack.id, config_hash=cfg.pack.config_hash, prefix=CANARY_PREFIX, canaries=())
    report = scan(artifacts, manifest, [r["narrative"] for r in s.records], cfg.pack)
    out = {c: {"overlap": 0, "bytes": 0, "items": 0} for c in ("central_raw", "central_allowed", "pushdown")}
    for hit in report["shingle_hits"]:
        out[hit["artifact_class"]]["overlap"] += hit["bytes"]
    for c, entry in report["artifact_classes"].items():
        out[c]["bytes"], out[c]["items"] = entry["bytes"], entry["items"]
    return out


def _central_runtime(cfg: _Settings, args: argparse.Namespace, out_dir: Path, clock: SimClock,
                     master_data: Mapping[str, Mapping[str, Sequence[str]]]) -> Runtime:
    ledger = out_dir / "central.ledger.jsonl"
    if cfg.central_routing is not None:
        return Runtime(cfg.central_routing, boundary="central", ledger_path=ledger, run_id=args.run_id, clock=clock,
                       data_label="synthetic", allow_external_raw="synthetic", simulation=True)
    return _fake_runtime(cfg.pack, "central", ledger, args.run_id, clock,
                         {CENTRAL_TASKS[0]: fake_central_raw(cfg.pack, master_data),
                          CENTRAL_TASKS[1]: fake_central_allowed(cfg.pack, master_data)},
                         allow_external_raw="synthetic")


def run(args: argparse.Namespace, cfg: _Settings, out_dir: Path, started: float) -> int:
    pack, prereg = cfg.pack, cfg.prereg
    w, ev = prereg["world"], prereg["evaluation"]
    weeks = world_weeks(w["start"], w["weeks"])
    labels = labels_doc(cfg.spec, pack, weeks=weeks, eval_from=ev["eval_from"], eval_to=ev["eval_to"],
                        grace_weeks=ev["grace_weeks"])
    clock = SimClock(f"{w['start']}T12:00:00Z")
    seeds: list[_Seed] = []
    central: Runtime | None = None
    pipeline_s: list[float] = []
    out_dir.mkdir(parents=True)
    try:
        for seed in w["seeds"]:
            t0 = time.perf_counter()
            seeds.append(_seed(cfg, args, seed, out_dir / "work" / f"seed-{seed}", clock))
            pipeline_s.append(time.perf_counter() - t0)
        total = sum(len(s.candidates) for s in seeds)
        below = total < PROTOCOL_MIN_CANDIDATES or len(seeds) < PROTOCOL_MIN_SEEDS
        if args.min_candidates is None and below:
            return fail(f"only {total} candidates over {len(seeds)} seeds (protocol minimum "
                        f"{PROTOCOL_MIN_CANDIDATES} over {PROTOCOL_MIN_SEEDS}); pass --min-candidates N to run below "
                        "it (stamped)")
        if args.min_candidates is not None and total < args.min_candidates:
            return fail(f"only {total} candidates, fewer than --min-candidates {args.min_candidates}")
        central = _central_runtime(cfg, args, out_dir, clock, seeds[0].master_data)
        t0 = time.perf_counter()
        doc = _score_items(args, cfg, seeds, labels, central, clock, below)
        score_s = time.perf_counter() - t0
    finally:
        for s in seeds:
            for runtime in s.runtimes:
                runtime.close()
            s.pipeline.close()
        if central is not None:
            central.close()
    doc["paths"] = {"prereg": str(Path(args.x1_prereg).resolve()), "plant": str(Path(args.plant).resolve()),
                    "run_dir": str(out_dir.resolve()), "workdir": str((out_dir / "work").resolve()),
                    "central_ledger": str((out_dir / "central.ledger.jsonl").resolve())}
    doc["timings"] = {"total_s": time.perf_counter() - started, "per_seed_pipeline_s": pipeline_s, "score_s": score_s}
    doc["run_id"] = args.run_id
    doc["created_at"] = utc_clock()
    problems = e2_problems(doc)
    if problems:
        raise AssertionError(f"e2.json fails its schema at {problems[0][0]} ({problems[0][1]}): a bug")
    write_json_atomic(out_dir / "e2.json", doc)
    ratio = doc["ratio"]["estimate"]
    print(f"e2: candidates={doc['candidates']['total']} seeds={doc['candidates']['seeds']} "
          f"ratio={'null' if ratio is None else f'{ratio:.3f}'} measurement={str(doc['stamps']['measurement']).lower()}"
          f" verdict={'withheld' if doc['verdicts_withheld'] else 'reported'} -> {out_dir / 'e2.json'} "
          "(synthetic, internal only)")
    return 0


def _prompt_tokens(rows: Sequence[Mapping[str, Any]]) -> dict[str, int | None]:
    """The server-reported prompt tokens per central_raw ref: the largest over its attempts that got a reply."""
    out: dict[str, int | None] = {}
    for r in rows:
        if r["task"] != CENTRAL_TASKS[0] or r["attempt"] < 1:
            continue
        tokens = r["tokens_in"]
        if tokens is not None:
            out[r["ref"]] = max(tokens, out.get(r["ref"]) or 0)
        else:
            out.setdefault(r["ref"], None)
    return out


def _score_items(args: argparse.Namespace, cfg: _Settings, seeds: Sequence[_Seed], labels: Mapping[str, Any],
                 central: Runtime, clock: SimClock, below: bool) -> dict[str, Any]:
    pack, prereg = cfg.pack, cfg.prereg
    w, ev = prereg["world"], prereg["evaluation"]
    work = []
    for s in seeds:
        for c in s.candidates:
            work.append((c["snapshot"]["as_of"], s.seed, c["key"], s, c))
    work.sort(key=lambda item: item[:3])
    items: list[dict[str, Any]] = []
    raw_refs: list[str | None] = []
    raw_sent = 0
    verdicts = {"confirm": 0, "refute": 0, "unknown": dict.fromkeys(UNKNOWN_REASONS, 0)}
    statuses = dict.fromkeys(STATUSES, 0)
    routes = {"contributing": 0, "sibling": 0}
    central_failures = {c: dict.fromkeys(ERROR_KINDS, 0) for c in ("central_raw", "central_allowed")}
    supported = resolvable = misses = late_collected = 0
    for n, (as_of, seed, key, s, c) in enumerate(work, start=1):
        clock.set(f"{as_of}T12:00:00Z")
        params = {"entity_type": c["entity_type"], "entity_id": c["entity_id"], "predicate": c["predicate"]}
        window = question_window(pack, as_of=as_of, candidate_window_start=c["snapshot"]["window"][0])
        raw_ref = f"e2:{n}:raw"
        raw_score, raw_records, raw_truncated, sent, raw_error = _central_raw(s, cfg, central, params, window,
                                                                              raw_ref)
        raw_sent += sent
        allowed_score, allowed_records, allowed_error = _central_allowed(s, cfg, central, params, window,
                                                                         f"e2:{n}:allowed")
        for name, error in (("central_raw", raw_error), ("central_allowed", allowed_error)):
            if error is not None:
                central_failures[name][error] += 1
        conclusion = s.orchestrator.verify_stored(s.run_id, key)
        late = 0
        if s.orchestrator.late_deliveries:
            pending = s.orchestrator.late_deliveries
            still = s.orchestrator.join_late(args.deadline_seconds)
            s.orchestrator.collect_late(as_of=as_of)
            late = pending - still
            late_collected += late
            conclusion = s.orchestrator.conclusion(conclusion.conclusion_id)
        gate_doc = conclusion.body["gate"]
        statuses[conclusion.status] += 1
        for u in gate_doc["used"]:
            routes[u["role"]] += 1
        for u in gate_doc["used"]:
            if u["verdict"] != "unknown":
                verdicts[u["verdict"]] += 1
                continue
            body = _body(s, conclusion.question_id, u)
            reason = body.get("reason") or ("degraded" if body.get("quality") == "degraded" else "none")
            verdicts["unknown"][reason] += 1
        if conclusion.status == "supported":
            supported += 1
            checks = [_resolvable(s, _body(s, conclusion.question_id, u)) for u in gate_doc["used"] if u["counted"]]
            resolvable += int(all(ok for ok, _ in checks))
            misses += sum(m for _, m in checks)
        support_lb = gate_doc["support"]["support_lb"]
        raw_refs.append(raw_ref if raw_records else None)
        items.append({"seed": seed, "key": key, "label": _label(key, c["snapshot"]["week"], labels), "as_of": as_of,
                      "question_id": conclusion.question_id, "conclusion_id": conclusion.conclusion_id,
                      "status": conclusion.status,
                      "scores": {"stats_only": c["snapshot"]["score"], "central_raw": raw_score,
                                 "central_allowed": allowed_score,
                                 "pushdown": STATUS_RANK[conclusion.status] * STATUS_SCALE + support_lb},
                      "central_raw_records": raw_records, "central_raw_truncated": raw_truncated,
                      "central_allowed_records": allowed_records,
                      "central_errors": {"central_raw": raw_error, "central_allowed": allowed_error},
                      "central_raw_prompt_tokens": None, "late_collected": late})
    rows = read_ledger(central.ledger.path) if central.ledger.path.exists() else []
    tokens = _prompt_tokens(rows)
    for item, ref in zip(items, raw_refs):
        item["central_raw_prompt_tokens"] = tokens.get(ref) if ref is not None else None
    context = args.central_context_tokens
    sent_refs = [ref for ref in raw_refs if ref is not None and ref in tokens]
    reported = [tokens[ref] for ref in sent_refs if tokens[ref] is not None]
    unreported = sum(1 for ref in sent_refs if tokens[ref] is None)
    at_limit = 0 if context is None else unreported + sum(1 for t in reported if t + CENTRAL_MAX_TOKENS >= context)
    order = sorted(range(len(items)), key=lambda i: (items[i]["seed"], items[i]["key"]))
    items = [items[i] for i in order]
    scored = [i for i in items if i["scores"]["central_raw"] is not None and i["scores"]["central_allowed"] is not None]
    failed_items = len(items) - len(scored)
    boot = stats.paired_ranking_bootstrap({c: [i["scores"][c] for i in scored] for c in CONDITIONS},
                                          [i["label"] == "true" for i in scored], k=TOP_K, B=args.bootstrap_b,
                                          seed=args.bootstrap_seed, ratio=("pushdown", "central_raw"),
                                          epsilon=EPSILON, clusters=[i["key"] for i in scored]) if scored else None
    scans = [_scan(s, cfg) for s in seeds]
    overlap = {c: sum(x[c]["overlap"] for x in scans) for c in ("central_raw", "central_allowed", "pushdown")}
    artifacts = {c: {"bytes": sum(x[c]["bytes"] for x in scans), "items": sum(x[c]["items"] for x in scans)}
                 for c in ("central_raw", "central_allowed", "pushdown")}
    endpoints: list[Endpoint] = list(central.config.endpoints.values())
    for s in seeds:
        for runtime in s.runtimes:
            endpoints += list(runtime.config.endpoints.values())
            rows += read_ledger(runtime.ledger.path) if runtime.ledger.path.exists() else []
    measurement = measurement_flag(endpoints, rows, False)
    ratio = boot["ratio"] if boot is not None else _empty_ratio()
    raw_text_bytes = {"stats_only": 0, "central_raw": raw_sent, "central_allowed": overlap["central_allowed"],
                      "pushdown": overlap["pushdown"]}
    used = sum(routes.values())
    unanswered = sum(verdicts["unknown"][r] for r in UNANSWERED_REASONS)
    unanswered_share = unanswered / used if used else None
    verdict, withheld_reason = bar_verdict(measurement, ratio, raw_text_bytes["pushdown"],
                                           central_failures=failed_items, at_context_limit=at_limit,
                                           unanswered_share=unanswered_share)
    by_label = dict.fromkeys(LABELS, 0)
    for i in items:
        by_label[i["label"]] += 1
    providers = sorted({f"{e.boundary}:{e.provider}" for e in endpoints})
    chance = boot["chance"] if boot is not None else {"prevalence": 0.0, "ap": None, "ap_ci_low": None,
                                                     "ap_ci_high": None}
    conditions = boot["conditions"] if boot is not None else {c: _empty_condition() for c in CONDITIONS}
    doc: dict[str, Any] = {
        "kind": "e2_pushdown", "schema_version": SCHEMA_VERSION,
        "stamps": {"synthetic": True, "internal_only": True, "measurement": measurement,
                   "same_author_pack": pack.same_author_as_code, "below_protocol_minimum": below,
                   "allow_dirty": bool(args.allow_dirty), "secret_mode": "seeded-demo", "data_label": "synthetic"},
        "hashes": {**pack.hashes(), "code_hash": e2_code_hash(), "x1_prereg_sha256": cfg.prereg_sha,
                   "plant_sha256": cfg.spec.sha256},
        "code": {"code_commit": code_commit(), "code_dirty": cfg.dirty, "code_files": e2_code_files()},
        "endpoints": {"site_judge": "routing" if cfg.site_routing is not None else "fake",
                      "central_judge": "routing" if cfg.central_routing is not None else "fake",
                      "site_routing": [{"site": sid, "routing_sha256": cfg.site_routing[sid].sha256
                                        if cfg.site_routing is not None else None} for sid in w["site_ids"]],
                      "central_routing_sha256": cfg.central_routing.sha256 if cfg.central_routing else None,
                      "providers": providers},
        "settings": {"top_n": args.top_n, "min_candidates": args.min_candidates,
                     "deadline_seconds": float(args.deadline_seconds), "bootstrap_b": args.bootstrap_b,
                     "central_context_tokens": context, "max_unanswered_share": MAX_UNANSWERED_SHARE,
                     "bootstrap_seed": args.bootstrap_seed, "k": TOP_K, "central_max_records": CENTRAL_MAX_RECORDS,
                     "epsilon": EPSILON, "bar": BAR, "tie_salt": prereg["tie_salt"], "seeds": list(w["seeds"]),
                     "eval_from_week": ev["eval_from_week"], "eval_to_week": ev["eval_to_week"],
                     "grace_weeks": ev["grace_weeks"]},
        "candidates": {"total": len(items), "seeds": len(seeds),
                       "by_seed": [{"seed": s.seed, "detected": s.detected, "candidates": len(s.candidates)}
                                   for s in seeds], "by_label": by_label},
        "condition_labels": dict(CONDITION_LABELS),
        "bootstrap": {"n": len(scored), "n_relevant": sum(1 for i in scored if i["label"] == "true"),
                      "B": args.bootstrap_b, "seed": args.bootstrap_seed, "k": TOP_K,
                      "method": boot["method"] if boot is not None else "paired cluster percentile",
                      "clusters": "candidate key", "n_clusters": boot["n_clusters"] if boot is not None else 0,
                      "excluded_central_failures": failed_items},
        "chance": chance, "conditions": conditions, "ratio": ratio,
        "central": {"failures": central_failures,
                    "failed_items": failed_items, "context_tokens": context,
                    "prompt_tokens_max": max(reported) if reported else None,
                    "prompt_tokens_median": stats.percentile(reported, 50), "prompt_tokens_unreported": unreported,
                    "at_context_limit": at_limit},
        "raw_text_bytes": raw_text_bytes,
        "raw_text_scan": {"method": SCAN_METHOD, **overlap, "artifacts": artifacts},
        "pushdown": {"statuses": statuses, "verdicts": verdicts, "routes": routes,
                     "budget_unknowns": verdicts["unknown"]["budget"], "timeouts": verdicts["unknown"]["timeout"],
                     "errors": verdicts["unknown"]["error"], "late_collected": late_collected,
                     "unanswered": unanswered, "unanswered_share": unanswered_share,
                     "resolvability": {"supported": supported, "resolvable": resolvable,
                                       "share": resolvable / supported if supported else None,
                                       "extraction_miss_confirmations": misses}},
        "verdict": verdict, "verdicts_withheld": verdict is None, "withheld_reason": withheld_reason,
        "items": items, "notes": list(NOTES), "content_hash_excludes": list(CONTENT_HASH_EXCLUDES),
    }
    doc["content_hash"] = content_hash(doc)
    return doc


def _empty_ratio() -> dict[str, Any]:
    return {"numerator": "pushdown", "denominator": "central_raw", "estimate": None, "ci_low": None, "ci_high": None,
            "undefined": 0, "epsilon": EPSILON, "lift_estimate": None, "lift_ci_low": None, "lift_ci_high": None,
            "lift_undefined": 0, "denominator_lift": None, "denominator_lift_ci_low": None,
            "denominator_lift_ci_high": None}


def _empty_condition() -> dict[str, Any]:
    return {"ap": None, "ap_ci_low": None, "ap_ci_high": None, "ap_undefined": 0, "precision_at_k": 0.0,
            "p_ci_low": None, "p_ci_high": None}


def bar_verdict(measurement: bool, ratio: Mapping[str, Any], pushdown_raw_text_bytes: int, *,
                central_failures: int = 0, at_context_limit: int = 0,
                unanswered_share: float | None = 0.0) -> tuple[dict[str, Any] | None, str | None]:
    """``(verdict, withheld_reason)``: the 0.90 bar is judged only for a measurement (no fake anywhere) whose central
    calls all returned, whose central_raw prompts all fitted the declared context, whose pushdown routes were
    answered (at most :data:`MAX_UNANSWERED_SHARE` unanswered), with a defined ratio and an informative pool
    (central_raw beats a random order: its lift over chance has a 95% interval above 0); otherwise the verdict is
    None and the reason says why. The bar needs the AP ratio and its interval's low end at :data:`BAR`, the
    chance-corrected ratio at :data:`BAR` and zero pushdown raw-text bytes."""
    if not measurement:
        return None, "a fake judge was involved (a fake endpoint or a fake-marked ledger row): a rehearsal"
    if central_failures:
        return None, (f"the central judge failed on {central_failures} items (see central.failures): the comparison "
                      "is incomplete; re-run against a healthy server")
    if at_context_limit:
        return None, (f"{at_context_limit} central_raw prompts reached the declared context, or the server did not "
                      "report their prompt tokens: the central reference may have read a truncated prompt; raise the "
                      "context (--central-context-tokens and the server's own setting) and re-run")
    if unanswered_share is not None and unanswered_share > MAX_UNANSWERED_SHARE:
        return None, (f"{unanswered_share:.0%} of the pushdown routes were unanswered (timeout, error or a degraded "
                      "judge): the run measured the deadline and the servers, not pushdown")
    if ratio["estimate"] is None:
        return None, "the ratio is undefined (central_raw AP is null or below epsilon)"
    if ratio["denominator_lift_ci_low"] is None or ratio["denominator_lift_ci_low"] <= 0:
        return None, ("the candidate pool is uninformative: central_raw does not beat a random order (its lift over "
                      "the chance AP has a 95% interval reaching 0), so no ratio can show that pushdown keeps it")
    at_bar = ratio["estimate"] >= BAR
    ci_at_bar = ratio["ci_low"] is not None and ratio["ci_low"] >= BAR
    lift = ratio["lift_estimate"]
    lift_at_bar = lift is not None and lift >= BAR
    zero = pushdown_raw_text_bytes == 0
    return {"bar": BAR, "ratio": ratio["estimate"], "ratio_at_least_bar": at_bar, "ci_low_at_least_bar": ci_at_bar,
            "lift_ratio": lift, "lift_ratio_at_least_bar": lift_at_bar, "pushdown_raw_text_bytes_zero": zero,
            "pass": at_bar and ci_at_bar and lift_at_bar and zero}, None


def _body(s: _Seed, question_id: str, used: Mapping[str, Any]) -> dict[str, Any]:
    """The stored body of one used verdict (a site's verdict or an HQ record)."""
    for v in s.pipeline.store.pd_verdicts(question_id):
        if v.site == used["site"] and v.seq == used["seq"]:
            return strict_load(v.body)
    raise AssertionError("a used verdict is not stored: a bug")


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.e2_pushdown",
                                description="E2: pushdown verification against central reading on planted "
                                            "synthetic worlds (internal only; not a measurement with fakes).")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("run", help="run the four conditions over every prereg seed and write e2.json")
    s.add_argument("--x1-prereg", required=True, help="an X1 prereg.json (evaluate.harness prereg)")
    s.add_argument("--plant", required=True, help="a plant spec checked against the prereg")
    s.add_argument("--run-id", required=True)
    s.add_argument("--top-n", type=int, default=60, help="candidates per seed (default 60)")
    s.add_argument("--min-candidates", type=int, default=None, help="run below the protocol minimum (stamped)")
    s.add_argument("--site-routing", default=None, help="a directory of <site id>.json routing files")
    s.add_argument("--central-routing", default=None, help="the central comparator's routing file")
    s.add_argument("--central-context-tokens", type=int, default=None,
                   help="the context one request gets on the central server (needed with --central-routing)")
    s.add_argument("--allow-external-raw", default=None, help="must be synthetic: central_raw moves raw text")
    s.add_argument("--data-label", default=None, help="must be synthetic")
    s.add_argument("--deadline-seconds", type=float, default=600.0)
    s.add_argument("--bootstrap-b", type=int, default=10000)
    s.add_argument("--bootstrap-seed", type=int, default=1)
    s.add_argument("--runs-dir", default="runs")
    s.add_argument("--allow-dirty", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    return p


def cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    dry = DryRun(f"{CLI} run") if args.dry_run else None
    try:
        out_dir = run_dir(args.runs_dir, KIND, check_run_id(args.run_id))
        cfg = _check(args, dry)
        if dry is not None:
            if cfg is not None and cfg.central_routing is not None:
                for e in sorted(cfg.central_routing.routed_endpoint_names()):
                    dry.need(f"network {cfg.central_routing.endpoints[e].host_label} (central endpoint {e})")
            for name in ("e2.json", "central.ledger.jsonl", "work/seed-<seed>/"):
                dry.write(str(out_dir / name))
            return dry.emit()
    except (UsageError, PlantError, GeneratorError, ConfigError) as exc:
        return fail(str(exc))
    try:
        return run(args, cfg, out_dir, started)
    except KeyboardInterrupt:
        print(f"e2 run: interrupted; {out_dir} is partial and its run id cannot be reused", file=sys.stderr)
        return 130
    except (EvaluationError, GeneratorError, PlantError, PushdownError, LeakageError, InferenceError) as exc:
        return fail(str(exc))
    except (OSError, StrictJsonError) as exc:
        return fail(f"cannot read or write a run file ({exc.__class__.__name__})")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return {"run": cmd_run}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
