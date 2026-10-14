"""Preregister a plan's E1, J1, J2, J3, JT, L1, X1 and E2 units in the plan job, before any model runs.

    python -m lab.prereg --plan PLAN [--earlier-plans DIR] [--nhtsa-archive FILE] [--exclude-stand-in FILE]

``D`` is the plan's directory. Each step runs only when the plan holds units that need it:

* E1: :func:`lab.goldlabels.build_labels` writes ``D/prereg/e1/labels.jsonl`` and its record ``labels.json``; the
  routing file ``routing.json`` pins every E1 endpoint, in sorted model-key order (:func:`e1_routing_doc` at
  :data:`PLACEHOLDER_BASE_URL`: the pins never include the address, so a shard's routing to its own server matches
  them); then ``e1_extract prereg`` (data label ``synthetic``, or ``public`` for ``nhtsa`` labels; boundary
  :data:`E1_BOUNDARY`; the synthetic raw-text
  exemption only when an endpoint lies outside every boundary, which a hosted endpoint does: ``lab.hosted.endpoint``,
  boundary ``external``) writes ``D/prereg/e1/prereg/prereg.json``;
* J1 (judge test J001, ``lab.j1``), in this process: :func:`lab.goldlabels.build_labels` (``nhtsa``) writes
  ``D/prereg/j1/labels.jsonl`` and ``labels.json``; ``lab.j1.build_questions`` ``questions.jsonl`` and
  ``questions.json``; the verifier's lexical judge, on every question's payload before any model runs,
  ``lexical.jsonl`` (its verdict lines) and ``lexical.json`` (``lab.j1.score`` on every record); the record-blind
  control (``lab.j1.prior_verdicts``, which reads no record: it confirms the four predicates filed most in the whole
  complaint file) ``prior.jsonl`` and ``prior.json``; ``routing.json`` pins every J1 model (``lab.j1.routing_doc``
  at :data:`PLACEHOLDER_BASE_URL`); and ``prereg.json`` (``lab.j1.prereg_doc``:
  the hashes, the judge task's instructions, schema and ``max_tokens``, the code hash and files, the endpoints' pins,
  the boundary, the data label, the seeds, ``parts``, the twins, the excluded predicate and the bootstrap settings);
* J2 (accuracy test A001, ``lab.j2`` and ``lab.j2labels``; ``docs/collective/A001/CHOICE-A001.md`` as amended), in
  this process, under ``D/prereg/j2/``: the fresh draw's ``labels.jsonl`` and ``labels.json`` (``lab.j2labels``: the
  eligible records less the excluded refs, ``random.Random("nhtsa:<seed>").sample``), after the two freshness
  mechanisms ran: mechanism 1 reads the earlier run's plan artifact under ``--earlier-plans`` (default ``lab-earlier``
  beside ``D``, where the workflow's download step puts ``<run id>/``; the request's ``exclude`` entry names the run,
  the artifact, the file and the sha256 prefix) and mechanism 2 rebuilds J001's seed-1 draw from the complaint file
  (``lab.j2labels.rebuild_refs``). Neither verifying refuses the plan (:data:`~lab.notes.J2_FRESHNESS_UNVERIFIED`);
  both verifying with different ref sets refuses it (:data:`~lab.notes.J2_EXCLUSION_DISAGREES`); fewer eligible
  unexcluded records than the draw needs refuses it (:data:`~lab.notes.J2_TOO_FEW_RECORDS`). Then J001's code,
  unchanged: ``questions.jsonl`` and ``questions.json`` (``lab.j1.build_questions``), ``lexical.jsonl`` and
  ``lexical.json``, ``prior.jsonl`` and ``prior.json``; ``excluded.jsonl`` (A3: every excluded ref plus the draw's,
  refs only, for the next cycle to exclude); ``routing.json`` (``lab.j2.routing_doc``); and ``prereg.json``
  (``lab.j2.prereg_doc``: J001's pins plus both tasks' pins, the mapping, the arms, the primary pair, the exclusion
  counts and hashes, ``comparisons_before`` and the target). stdout gets the fixed draw line (the ``available`` count,
  the excluded count, the eligible count after exclusion, the overlap, the mechanisms' outcomes and the hashes), a
  mechanism-2 line when its sha256 differs, and the comparators' figures: counts, scores and hashes, never a ref.
  ``--nhtsa-archive FILE`` (tests only) is the complaint archive in place of its download; ``--exclude-stand-in
  FILE`` (tests only, ``lab.dryrun --j2-data``) replaces the request's ``exclude`` entry for the checks with a test
  artifact's and marks the preregistration ``stand_in`` (:data:`~lab.notes.J2_STAND_IN`), so a dry run can pass the
  checks in a sandbox that holds no real artifact; the workflow's prereg command passes neither;
* J3 (accuracy test A002, ``lab.j3`` and ``lab.j3labels``; ``docs/collective/A002/CHOICE-A002.md`` as amended), in
  this process, under ``D/prereg/j3/``: J2's files plus ``ablation_a.jsonl`` and ``ablation_b.jsonl`` (the two
  ablation comparators of the composite arms, ``lab.j3.ablation_a_lines`` and ``ablation_b_lines``, with their
  scores ``ablation_a.json`` and ``ablation_b.json``), after the five sources of the two earlier cycles ran
  (``lab.j3labels.establish``: J001's artifact and rebuild, A001's artifact, rebuild and chain file, under one
  ``--earlier-plans`` directory, ``<run id>/`` each, and the stop rules of item 5 as amended B5). A cycle whose set is
  not established refuses the plan (:data:`~lab.notes.J3_FRESHNESS_UNVERIFIED`); a consistency count above zero
  refuses it (:data:`~lab.notes.J3_EXCLUSION_DISAGREES`); too few records refuse it
  (:data:`~lab.notes.J3_TOO_FEW_RECORDS`). The labels record is ``lab.j2labels.build_labels``' extended with every
  source's outcome (B22); ``excluded.jsonl`` carries the 300 excluded refs and the draw's. stdout gets one line per
  source that did not verify, the cycles' readings and the consistency counts, the fixed draw line and the
  comparators' figures with both ablations. ``--exclude-stand-in FILE`` is then a two-entry list (``lab.dryrun
  --j3-data``);
* L1 (latency test L001, ``lab.l1``), in a subprocess whose stdout and stderr go to ``D/prereg-logs`` (K9: the step
  reads MSHA's file, so nothing of it reaches the console): ``lab.l1 prereg`` downloads the file into ``lab-msha``
  beside ``D`` (:func:`lab.l1.raw_dir`, the workflow's cache path) unless it is there, rebuilds the drafter demo's
  pipeline, asks every question with the key judge, the lexical judge, the record-blind control and the route-role
  baseline on fresh stores, and writes ``D/prereg/l1/prereg.json``, ``scores.json`` and ``warmup.json``. A stop of the
  rule (no alert, more than one, the demo's figures, the draws, ...) leaves ``D/prereg-l1-stop.json`` and refuses the
  plan with :data:`~lab.notes.L1_STOPPED` and the stop's code; a failed download refuses it with
  :data:`~lab.notes.L1_FETCH_FAILED`. The manifest's ``l1`` block holds ``cache_key`` (:func:`l1_cache_key`), which
  ``lab.l1 cache-key`` hands the workflow, so the plan job saves the file in the workflow's cache (K10);
* JT (accuracy test A003's term run, ``lab.jt``; ``docs/collective/A003/CHOICE-A003.md`` as amended, "The terms"):
  the complaint archive's one cached fetch, the pooled drafting export of the six makes' complaints received before
  the cutoff (``lab.jt.pooled_export``, in this process) and its two drafting files; then the draft and the check in a
  subprocess, ``lab.jt draft``, under this step runner with :data:`lab.jt.DRAFT_TIMEOUT_S` (C11), the export written
  to a scratch directory beside ``D`` (never under it: everything under ``D`` is uploaded) and removed afterwards. A
  download or an archive that cannot be read refuses the plan with :data:`~lab.notes.JT_FETCH_FAILED`; a check that
  fails with :data:`~lab.notes.JT_FLOOR_FAILED` (after a ``jt check: passed no`` line with each failing check's
  count); a draft the drafter refuses, a timeout, an out-of-memory exit, a crash, a missing or malformed result, or an
  export or a P_D rebuild that fails in this process with :data:`~lab.notes.JT_DRAFT_FAILED`; each stop prints a ``jt
  step: stopped`` line (wall seconds, the draft's, the largest resident memory of a finished child) and nothing of
  the draft is written under ``D``. Otherwise ``D/prereg/jt/`` gets the drafted pack P_draft (``pack/``),
  ``terms.json`` (the term set, whose sha256 is the term set's), ``drafting_refs.jsonl``, ``drafting_text.jsonl``,
  ``check.json`` (the check's counts and booleans by check, ``lab.jt.check_counts``) and ``prereg.json`` (what the
  unit verifies), and stdout the term run's fixed lines (``lab.jt.console_lines``: counts, dates and hashes, never a
  term), then the step's wall time and the draft's peak resident memory;
* X1: the evaluation harness's prereg (run id ``x1``, ``D/prereg/x1/x1/prereg.json``) and ``check-plant``;
* E2: the same with run id ``e2`` (``D/prereg/x1/e2/prereg.json``) and ``check-plant``, then a model-free rehearsal:
  ``e2_pushdown run`` without routing (fake site and central judges) in a scratch directory beside ``D``, removed
  afterwards. It picks the same candidates and retrieves the same central_raw records as the real run, so its counts
  size the real run: ``D/prereg/e2/rehearsal.json`` holds the candidates, the judge calls per task (ledger rows with
  attempt 0 or 1), the central_raw and central_allowed record counts (max and sum over the items), the item with the
  most raw records (``worst``), the pipeline and wall seconds and the e2.json ``content_hash``.

Last, ``D/prereg/prereg.json`` (the manifest): the plan's sha256, the sha256 and size of every other file under
``D/prereg``, and per experiment (null when the plan has none) the labels record, endpoints, reference and prereg path
(E1), the prereg path (X1), the prereg path, plant path and rehearsal (E2), and the labels and questions records, the
lexical judge's and the record-blind control's scores, the predicate-only bound on every record
(``lab.j1.prior_bound``), the endpoints, the prereg path and the settings (J1). Every shard and the aggregate call
:func:`load_prereg` before they use any of it. Harness logs go to ``D/prereg-logs/<step>.stdout.log`` and
``.stderr.log`` (capped; not in the manifest). Each subprocess gets the units' environment allowlist, the repository
as its directory, and :data:`STEP_TIMEOUT_S` (:data:`REHEARSAL_TIMEOUT_S` for the rehearsal).

The NHTSA complaint archive (``nhtsa`` labels, E1's or J1's) is downloaded at most once per preregistration;
:func:`preregister` takes a keyword-only ``nhtsa_fetch`` in its place, which only tests pass.

stdout ends with one line, ``prereg: e1 yes|no x1 yes|no e2 yes|no j1 yes|no``, with `` l1 yes`` appended for a plan
with L1 units, `` j2 yes`` for one with J2 units, `` j3 yes`` for one with J3 units (whose fixed draw and comparator
lines come before it) and `` jt yes`` for one with the term run's unit (whose fixed lines come before it). A plan
that cannot be read, or a
``D/prereg`` that exists already, is a usage error (exit 2, nothing written). A refused step writes
``D/plan-error.json`` (source
``prereg``), prints ``error: <path>: <problem>`` first on stderr and an ``::error`` workflow command, and exits 2; the
manifest is then never written, so no shard can use a partial preregistration.
"""
from __future__ import annotations

import argparse
import http.client
import os
import resource
import shutil
import stat
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.experiments.common import code_commit, code_dirty, write_json_atomic
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader

from . import EXIT_OK, EXIT_USAGE, ROOT, shown_path
from . import hosted as lab_hosted
from . import j1 as lab_j1
from . import j2 as lab_j2
from . import j2labels as lab_j2labels
from . import j3 as lab_j3
from . import j3labels as lab_j3labels
from . import jt as lab_jt
from . import l1 as lab_l1
from . import units as lab_units
from .goldlabels import GoldLabelsError, build_labels, nhtsa_export
from .notes import (J2_EXCLUSION_DISAGREES, J2_FRESHNESS_UNVERIFIED, J2_TOO_FEW_RECORDS, J3_EXCLUSION_DISAGREES,
                    J3_FRESHNESS_UNVERIFIED, J3_TOO_FEW_RECORDS, JT_DRAFT_FAILED, JT_FETCH_FAILED, JT_FLOOR_FAILED,
                    L1_FETCH_FAILED, L1_STOPPED)
from .plan import write_plan_error
from .request import (RequestError, _exclude as validate_exclude, _j3_exclude_list as validate_j3_exclude,
                      SHA256_PREFIX_RE)

PLACEHOLDER_BASE_URL = "http://127.0.0.1:9/v1"
E1_BOUNDARY = "site:lab"
STEP_TIMEOUT_S = 300
REHEARSAL_TIMEOUT_S = 900
JT_MODULE = lab_jt.CLI              # the term run's draft step (tests replace the module and the command)
JT_DRAFT_COMMAND = "draft"
L1_TIMEOUT_S = 1080                 # the L1 step: the file, the draft, the audit and 25 paths on fresh stores
REHEARSAL_DEADLINE_S = 3600
REHEARSAL_BOOTSTRAP_B = 1000
E1_MODULE = "mycelic.collective.experiments.e1_extract"
X1_MODULE = "mycelic.collective.evaluate.harness"
E2_MODULE = "mycelic.collective.experiments.e2_pushdown"
MANIFEST = "prereg/prereg.json"
EARLIER_DIRNAME = "lab-earlier"     # the workflow's download path for an earlier run's plan artifact (A1)
CENTRAL_TASKS = ("judge_candidate_raw", "judge_candidate_allowed")
E1_REFUSED = "E1 preregistration refused these settings; its log is in the plan artifact under prereg-logs"
X1_REFUSED = "the evaluation harness refused these settings; its log is in the plan artifact under prereg-logs"
PLANT_REFUSED = "the plant does not fit the preregistered world"
REHEARSAL_REFUSED = ("the model-free rehearsal refused these settings (fewer candidates than min_candidates, or a "
                     "world or plant problem); its log is in the plan artifact under prereg-logs")


class PreregError(ValueError):
    pass


class _StepFailed(Exception):
    def __init__(self, path: str, problem: str) -> None:
        super().__init__(problem)
        self.path = path
        self.problem = problem


@dataclass(frozen=True)
class Prereg:
    """A verified preregistration: ``dir`` is the plan's directory (the files are under ``dir/prereg``)."""

    dir: Path
    manifest: dict[str, Any]
    sha256: str


# --------------------------------------------------------------------------------------------------- routing pins

def e1_endpoint(entry: Mapping[str, Any], base_url: str) -> dict[str, Any]:
    """One E1 endpoint: the model's alias and transport inside :data:`E1_BOUNDARY`, or a hosted model's endpoint
    (``lab.hosted.endpoint``, boundary ``external``)."""
    if entry["kind"] == "hosted":
        return lab_hosted.endpoint(entry, base_url)
    return {"provider": "openai_compat", "boundary": E1_BOUNDARY, "base_url": base_url, "model": entry["alias"],
            "response_format": entry["response_format"], "transport_schema": entry["transport_schema"],
            "connect_timeout_s": 5, "deadline_s": 900, "max_retries": 1}


def e1_routing_doc(models: Mapping[str, Mapping[str, Any]], keys: Sequence[str], base_url: str) -> dict[str, Any]:
    """The only builder of E1 routing: the prereg's (at the placeholder address) and each shard's (at its server)."""
    return {"schema_version": 1, "endpoints": {key: e1_endpoint(models[key], base_url) for key in keys}, "routes": {}}


# --------------------------------------------------------------------------------------------------- loading

def _regular(path: Path) -> bytes | None:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        return path.read_bytes()
    except OSError:
        return None


def load_prereg(plan_path: str | os.PathLike[str]) -> Prereg:
    """The preregistration beside ``plan_path``, after checking that it was made for these plan bytes and that every
    file it lists is a regular file with its recorded sha256 and size; :class:`PreregError` otherwise."""
    directory = Path(os.path.abspath(plan_path)).parent
    plan_bytes = _regular(Path(plan_path))
    data = _regular(directory / MANIFEST)
    if plan_bytes is None or data is None:
        raise PreregError("the plan or its preregistration cannot be read") from None
    failed = False
    try:
        manifest = strict_load(data)
    except StrictJsonError:
        failed = True
    if failed or not isinstance(manifest, dict) or manifest.get("kind") != "lab_prereg" \
            or manifest.get("schema_version") != 1:
        raise PreregError("not a lab preregistration") from None
    if manifest.get("plan_sha256") != sha256_hex(plan_bytes):
        raise PreregError("the preregistration is for another plan") from None
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise PreregError("the preregistration lists no files") from None
    for rel, meta in files.items():
        parts = rel.split("/") if isinstance(rel, str) else []
        if not parts or parts[0] != "prereg" or ".." in parts or "" in parts or not isinstance(meta, dict):
            raise PreregError("the preregistration lists a bad path") from None
        body = _regular(directory / rel)
        if body is None or meta.get("sha256") != sha256_hex(body) or meta.get("bytes") != len(body):
            raise PreregError("a preregistered file is missing or differs") from None
    return Prereg(dir=directory, manifest=manifest, sha256=sha256_hex(data))


# --------------------------------------------------------------------------------------------------- steps

class _Steps:
    """Runs the harness steps: argv, environment allowlist, repository cwd, timeout and capped logs."""

    def __init__(self, directory: Path) -> None:
        self.logs = directory / "prereg-logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def run(self, step: str, block: str, module: str, command: str, flags: Sequence[tuple[str, Any]],
            timeout_s: float = STEP_TIMEOUT_S) -> int:
        argv = [sys.executable, "-m", module, command, *(lab_units.flag(n, v) for n, v in flags)]
        out, err = self.logs / f"{step}.stdout.log", self.logs / f"{step}.stderr.log"
        result = lab_units.run_process(argv, lab_units.subprocess_env(os.environ, []), cwd=ROOT, timeout_s=timeout_s,
                                       stdout_path=out, stderr_path=err)
        lab_units.cap_log(out)
        lab_units.cap_log(err)
        if result.timed_out:
            raise _StepFailed(block, f"{step} did not finish in time")
        return result.exit_code if result.exit_code is not None else -1


def _units(plan: Mapping[str, Any], experiment: str) -> list[dict[str, Any]]:
    return [u for u in plan["units"] if u.get("experiment") == experiment]


def _e1(steps: _Steps, plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path,
        nhtsa_fetch: Callable[[], bytes] | None = None) -> dict[str, Any]:
    params = units[0]["params"]
    labels = params["labels"]
    try:
        data, record = build_labels(labels["source"], labels["pack"], labels["n"], labels["seed"],
                                    fetch=nhtsa_fetch if labels["source"] == "nhtsa" else None)
    except GoldLabelsError as err:
        raise _StepFailed("$.experiments.e1.labels.n", err.problem) from None
    e1 = root / "prereg" / "e1"
    e1.mkdir(parents=True)
    (e1 / "labels.jsonl").write_bytes(data)
    write_json_atomic(e1 / "labels.json", record)
    endpoints = sorted({u["params"]["endpoint"] for u in units})
    routing = e1_routing_doc(plan["models"], endpoints, PLACEHOLDER_BASE_URL)
    write_json_atomic(e1 / "routing.json", routing)
    external = any(e["boundary"] == "external" for e in routing["endpoints"].values())
    data_label = "public" if labels["source"] == "nhtsa" else "synthetic"
    flags: list[tuple[str, Any]] = [("pack", params["pack"]), ("labels", e1 / "labels.jsonl"),
                                    ("routing", e1 / "routing.json"), *(("endpoint", key) for key in endpoints),
                                    ("reference", params["reference"]), ("margin", params["margin_points"]),
                                    ("runs", params["runs"]), ("seed", params["seed"]),
                                    ("bootstrap-b", params["bootstrap_b"]), ("data-label", data_label),
                                    ("boundary", E1_BOUNDARY),
                                    *([("allow-external-raw", data_label)] if external else []),
                                    ("run-id", "prereg"), ("runs-dir", root / "prereg")]
    if steps.run("e1-prereg", "$.experiments.e1", E1_MODULE, "prereg", flags) != 0:
        raise _StepFailed("$.experiments.e1", E1_REFUSED)
    return {"labels": record, "endpoints": endpoints, "reference": params["reference"],
            "prereg": "prereg/e1/prereg/prereg.json"}


def _harness(steps: _Steps, unit: Mapping[str, Any], experiment: str, root: Path) -> str:
    """The evaluation harness's prereg (run id ``experiment``) and check-plant; the prereg's path relative to D."""
    p, block = unit["params"], f"$.experiments.{experiment}"
    flags = [("pack", p["pack"]), ("seeds", ",".join(str(s) for s in p["seeds"])), ("sites", p["sites"]),
             ("weeks", p["weeks"]), ("eval-from", p["eval_from"]), ("eval-to", p["eval_to"]),
             ("grace-weeks", p["grace_weeks"]), ("tie-salt", p["tie_salt"]), ("detector-author", p["detector_author"]),
             ("bootstrap-b", p["bootstrap_b"]), ("bootstrap-seed", p["bootstrap_seed"]), ("run-id", experiment),
             ("runs-dir", root / "prereg")]
    if steps.run(f"{experiment}-prereg", block, X1_MODULE, "prereg", flags) != 0:
        raise _StepFailed(block, X1_REFUSED)
    rel = f"prereg/x1/{experiment}/prereg.json"
    checked = steps.run(f"{experiment}-check-plant", block, X1_MODULE, "check-plant",
                        [("prereg", root / rel), ("plant", ROOT / p["plant_path"])])
    if checked != 0:
        raise _StepFailed(f"{block}.plant", PLANT_REFUSED)
    return rel


def _call_rows(paths: Sequence[Path], tasks: Sequence[str]) -> dict[str, int]:
    counts = dict.fromkeys(tasks, 0)
    for path in paths:
        for row in read_ledger(path):
            if row["task"] in counts and row["attempt"] in (0, 1):
                counts[row["task"]] += 1
    return counts


def rehearsal_doc(run: Path, wall_s: float) -> dict[str, Any]:
    """``rehearsal.json`` from a rehearsal's run directory (``e2.json`` and its ledgers)."""
    doc = strict_load((run / "e2.json").read_bytes())
    items = doc["items"]
    raw = [i["central_raw_records"] for i in items]
    allowed = [i["central_allowed_records"] for i in items]
    worst = sorted(items, key=lambda i: (-i["central_raw_records"], i["seed"], i["key"]))[0] if items else None
    calls = _call_rows(sorted(run.glob("work/seed-*/edge/site-*.ledger.jsonl")), ("judge_record",))
    calls.update(_call_rows([run / "central.ledger.jsonl"] if (run / "central.ledger.jsonl").exists() else [],
                            CENTRAL_TASKS))
    return {"schema_version": 1, "kind": "lab_e2_rehearsal", "measurement": False,
            "candidates": {k: doc["candidates"][k] for k in ("total", "seeds", "by_seed")},
            "below_protocol_minimum": doc["stamps"]["below_protocol_minimum"], "calls": calls,
            "central_raw_records": {"max": max(raw, default=0), "sum": sum(raw)},
            "central_allowed_records": {"max": max(allowed, default=0), "sum": sum(allowed)},
            "worst": {"seed": worst["seed"], "key": worst["key"]} if worst is not None else None,
            "pipeline_s": round(sum(doc["timings"]["per_seed_pipeline_s"]), 3), "wall_s": round(wall_s, 3),
            "e2_content_hash": doc["content_hash"]}


def _e2(steps: _Steps, unit: Mapping[str, Any], root: Path) -> dict[str, Any]:
    p = unit["params"]
    rel = _harness(steps, unit, "e2", root)
    scratch = tempfile.TemporaryDirectory(prefix="lab-rehearsal-", dir=root.parent)
    try:
        t0 = time.monotonic()
        code = steps.run("e2-rehearsal", "$.experiments.e2", E2_MODULE, "run", [
            ("x1-prereg", root / rel), ("plant", ROOT / p["plant_path"]), ("run-id", "rehearsal"),
            ("top-n", p["top_n"]), ("min-candidates", p["min_candidates"]), ("allow-external-raw", "synthetic"),
            ("data-label", "synthetic"), ("deadline-seconds", REHEARSAL_DEADLINE_S),
            ("bootstrap-b", REHEARSAL_BOOTSTRAP_B), ("bootstrap-seed", p["bootstrap_seed"]),
            ("runs-dir", scratch.name)], timeout_s=REHEARSAL_TIMEOUT_S)
        wall = time.monotonic() - t0
        run = Path(scratch.name) / "e2" / "rehearsal"
        if code != 0 or not (run / "e2.json").is_file():
            raise _StepFailed("$.experiments.e2", REHEARSAL_REFUSED)
        rehearsal = rehearsal_doc(run, wall)
    finally:
        scratch.cleanup()
    (root / "prereg" / "e2").mkdir(parents=True)
    write_json_atomic(root / "prereg" / "e2" / "rehearsal.json", rehearsal)
    return {"prereg": rel, "plant_path": p["plant_path"], "rehearsal": rehearsal}


def _j1(plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path,
        nhtsa_fetch: Callable[[], bytes] | None) -> dict[str, Any]:
    """J1's preregistration (see the module docstring), all in this process; the manifest's ``j1`` block."""
    p = units[0]["params"]
    labels = p["labels"]
    try:
        data, record = build_labels("nhtsa", labels["pack"], labels["n"], labels["seed"], fetch=nhtsa_fetch)
    except GoldLabelsError as err:
        raise _StepFailed("$.experiments.j1.labels.n", err.problem) from None
    pack = loader.load_pack(ROOT / labels["pack"])
    try:
        questions_bytes, questions = lab_j1.build_questions(pack, data, seed=p["seed"], parts=p["parts"])
        lines = lab_j1.read_questions(questions_bytes)
        lexical_lines = lab_j1.lexical_verdicts(pack, data, lines)
    except lab_j1.J1Error as err:
        raise _StepFailed("$.experiments.j1", err.problem) from None
    prior_lines = lab_j1.prior_verdicts(lines)
    lexical_bytes, prior_bytes = lab_j1.jsonl_bytes(lexical_lines), lab_j1.jsonl_bytes(prior_lines)
    lexical = lab_j1.score(lines, lexical_lines, bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"])
    prior = lab_j1.score(lines, prior_lines, bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"])
    endpoints = sorted({u["model"] for u in units})
    routing = lab_j1.routing_doc(plan["models"], endpoints, PLACEHOLDER_BASE_URL)
    j1 = root / "prereg" / "j1"
    j1.mkdir(parents=True)
    (j1 / "labels.jsonl").write_bytes(data)
    write_json_atomic(j1 / "labels.json", record)
    (j1 / "questions.jsonl").write_bytes(questions_bytes)
    write_json_atomic(j1 / "questions.json", questions)
    (j1 / "lexical.jsonl").write_bytes(lexical_bytes)
    write_json_atomic(j1 / "lexical.json", lexical)
    (j1 / "prior.jsonl").write_bytes(prior_bytes)
    write_json_atomic(j1 / "prior.json", prior)
    write_json_atomic(j1 / "routing.json", routing)
    write_json_atomic(j1 / "prereg.json", lab_j1.prereg_doc(
        pack=pack, pack_ref=labels["pack"], labels=record, questions=questions,
        lexical_sha256=sha256_hex(lexical_bytes), prior_sha256=sha256_hex(prior_bytes), routing=routing,
        seed=p["seed"], parts=p["parts"], bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"]))
    return {"labels": record, "questions": questions, "lexical": lexical, "prior": prior,
            "prior_bound": lab_j1.prior_bound(lines), "endpoints": endpoints,
            "prereg": "prereg/j1/prereg.json", "parts": p["parts"], "seed": p["seed"],
            "bootstrap_b": p["bootstrap_b"], "bootstrap_seed": p["bootstrap_seed"]}


def earlier_dir(plan_dir: Path) -> Path:
    """Where the plan job's download step puts an earlier run's plan artifact: ``lab-earlier/<run id>/`` beside the
    plan's directory (never under it: everything under the plan's directory is uploaded)."""
    return Path(plan_dir).parent / EARLIER_DIRNAME


def load_stand_in(path: str | Path) -> dict[str, Any] | list[dict[str, Any]]:
    """A tests-only exclusion stand-in: for J2 (``lab.dryrun --j2-data``) the request's ``exclude`` keys, validated
    as the request validates them, plus an optional ``rebuild_sha256_prefix`` for mechanism 2; for J3 (``lab.dryrun
    --j3-data``) a two-entry list of the j3 block's ``exclude`` shape, validated as the request validates it."""
    doc = strict_load(Path(path).read_bytes())
    if isinstance(doc, list):
        return validate_j3_exclude(doc, "$.exclude_stand_in")
    if not isinstance(doc, dict):
        raise RequestError("exclude_stand_in", "must be an object or a list")
    rebuild = doc.pop("rebuild_sha256_prefix", None)
    out = validate_exclude(doc, "$.exclude_stand_in")
    if rebuild is not None:
        if not isinstance(rebuild, str) or SHA256_PREFIX_RE.fullmatch(rebuild) is None:
            raise RequestError("$.exclude_stand_in.rebuild_sha256_prefix", "must be 12 to 64 lower-case hex digits")
        out["rebuild_sha256_prefix"] = rebuild
    return out


def _sha12(value: Any) -> str:
    return value[:12] if isinstance(value, str) else "none"


def _j2(plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path, nhtsa_fetch: Callable[[], bytes],
        earlier_plans: Path | None, stand_in: Mapping[str, Any] | None) -> dict[str, Any]:
    """J2's preregistration (see the module docstring), all in this process; the manifest's ``j2`` block."""
    p = units[0]["params"]
    labels = p["labels"]
    requested = dict(p["exclude"])
    used = dict(stand_in) if stand_in is not None else requested
    rebuild_prefix = used.pop("rebuild_sha256_prefix", lab_j2labels.J001_LABELS_SHA256_PREFIX)
    refs1, outcome1 = lab_j2labels.earlier_refs(earlier_plans, used)
    refs2, outcome2, rebuilt_sha = lab_j2labels.rebuild_refs(nhtsa_fetch, rebuild_prefix)
    mechanisms = {"artifact": outcome1, "rebuild": outcome2}
    if outcome2 != lab_j2labels.VERIFIED:
        print(f"j2 exclusion: mechanism rebuild {outcome2}: rebuilt seed-one labels sha256 {_sha12(rebuilt_sha)} "
              f"against the pinned prefix {rebuild_prefix[:12]}", flush=True)
    if outcome1 != lab_j2labels.VERIFIED:
        print(f"j2 exclusion: mechanism artifact {outcome1}", flush=True)
    verifying = [r for r in (refs1, refs2) if r is not None]
    if not verifying:
        raise _StepFailed("$.experiments.j2.exclude", J2_FRESHNESS_UNVERIFIED)
    difference = len(refs1 ^ refs2) if refs1 is not None and refs2 is not None else None
    if difference is not None:
        print(f"j2 exclusion: both mechanisms verified; symmetric difference {difference}", flush=True)
        if difference:
            raise _StepFailed("$.experiments.j2.exclude", J2_EXCLUSION_DISAGREES)
    excluded: set[str] = set().union(*verifying)
    excluded_runs = [{"run_id": used["run_id"], "artifact": used["artifact"], "file": used["file"],
                      "sha256_prefix": used["sha256_prefix"], "refs": len(refs1) if refs1 is not None else 0,
                      "outcome": outcome1, "stand_in": stand_in is not None,
                      "requested": {k: requested[k] for k in ("run_id", "artifact", "file", "sha256_prefix")}}]
    try:
        data, record = lab_j2labels.build_labels(labels["pack"], labels["n"], labels["seed"], fetch=nhtsa_fetch,
                                                 excluded=excluded, mechanisms=mechanisms,
                                                 excluded_runs=excluded_runs)
    except lab_j2labels.J2LabelsError as err:
        print(f"j2 draw: {err.problem}", flush=True)
        raise _StepFailed("$.experiments.j2.labels.n", J2_TOO_FEW_RECORDS) from None
    exclusion = record["exclusion"]
    exclusion.update(rebuilt_sha256=rebuilt_sha or None, rebuild_expected_prefix=rebuild_prefix,
                     symmetric_difference=difference, stand_in=stand_in is not None)
    pack = loader.load_pack(ROOT / labels["pack"])
    try:
        questions_bytes, questions = lab_j1.build_questions(pack, data, seed=p["seed"], parts=p["parts"])
        lines = lab_j1.read_questions(questions_bytes)
        lexical_lines = lab_j1.lexical_verdicts(pack, data, lines)
    except lab_j1.J1Error as err:
        raise _StepFailed("$.experiments.j2", err.problem) from None
    prior_lines = lab_j1.prior_verdicts(lines)
    lexical_bytes, prior_bytes = lab_j1.jsonl_bytes(lexical_lines), lab_j1.jsonl_bytes(prior_lines)
    lexical = lab_j1.score(lines, lexical_lines, bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"])
    prior = lab_j1.score(lines, prior_lines, bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"])
    bound = lab_j1.prior_bound(lines)
    endpoints = sorted({u["model"] for u in units})
    routing = lab_j2.routing_doc(plan["models"], endpoints, PLACEHOLDER_BASE_URL)
    drawn = {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}
    excluded_bytes = lab_j2labels.excluded_jsonl(excluded | drawn)
    j2 = root / "prereg" / "j2"
    j2.mkdir(parents=True)
    (j2 / "labels.jsonl").write_bytes(data)
    write_json_atomic(j2 / "labels.json", record)
    (j2 / "questions.jsonl").write_bytes(questions_bytes)
    write_json_atomic(j2 / "questions.json", questions)
    (j2 / "lexical.jsonl").write_bytes(lexical_bytes)
    write_json_atomic(j2 / "lexical.json", lexical)
    (j2 / "prior.jsonl").write_bytes(prior_bytes)
    write_json_atomic(j2 / "prior.json", prior)
    (j2 / "excluded.jsonl").write_bytes(excluded_bytes)
    write_json_atomic(j2 / "routing.json", routing)
    write_json_atomic(j2 / "prereg.json", lab_j2.prereg_doc(
        pack=pack, pack_ref=labels["pack"], labels=record, questions=questions,
        lexical_sha256=sha256_hex(lexical_bytes), prior_sha256=sha256_hex(prior_bytes), routing=routing,
        seed=p["seed"], parts=p["parts"], bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"],
        primary=p["primary"], comparisons_before=p["comparisons_before"]))
    print(f"j2 draw: available {record['available']} excluded {exclusion['excluded']} eligible_after_exclusion "
          f"{exclusion['eligible_after_exclusion']} overlap {exclusion['overlap']} mechanisms "
          f"artifact={outcome1} rebuild={outcome2} labels_sha256 {record['sha256'][:12]} excluded_sha256 "
          f"{sha256_hex(excluded_bytes)[:12]} complaint_file_sha256 {exclusion['complaint_file_sha256'][:12]}",
          flush=True)

    def interval(score: Mapping[str, Any]) -> str:
        ba = score["balanced_accuracy"]
        return f"{ba['value']:.3f} [{ba['ci_low']:.3f}, {ba['ci_high']:.3f}]"

    print(f"j2 comparators: lexical balanced_accuracy {interval(lexical)} record_blind {interval(prior)} "
          f"predicate_only_bound {bound['value']:.3f}", flush=True)
    return {"labels": record, "questions": questions, "lexical": lexical, "prior": prior, "prior_bound": bound,
            "endpoints": endpoints, "prereg": "prereg/j2/prereg.json", "parts": p["parts"], "seed": p["seed"],
            "bootstrap_b": p["bootstrap_b"], "bootstrap_seed": p["bootstrap_seed"], "exclusion": exclusion,
            "excluded_runs": excluded_runs, "stand_in": stand_in is not None,
            "excluded_sha256": sha256_hex(excluded_bytes), "primary": dict(p["primary"]),
            "headline_arm": p["primary"]["arm"], "comparisons_before": p["comparisons_before"],
            "tasks": lab_j2.task_pins(), "mapping": lab_j2.mapping_pin(), "arms": lab_j2.arms_doc(),
            "target_balanced_accuracy": lab_j2.TARGET_BA}


def _interval(score: Mapping[str, Any]) -> str:
    ba = score["balanced_accuracy"]
    return f"{ba['value']:.3f} [{ba['ci_low']:.3f}, {ba['ci_high']:.3f}]"


def _j3(plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path, nhtsa_fetch: Callable[[], bytes],
        earlier_plans: Path | None, stand_in: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """J3's preregistration (see the module docstring), all in this process; the manifest's ``j3`` block."""
    p = units[0]["params"]
    labels = p["labels"]
    requested = [dict(e) for e in p["exclude"]]
    used = [dict(e) for e in stand_in] if stand_in is not None else requested
    est = lab_j3labels.establish(earlier_plans, used[0], used[1], nhtsa_fetch)
    sources = est.summary["sources"]
    for name, word, entry in (("R1", "one", used[0]), ("R2", "two", used[1])):
        outcome = sources[name]["outcome"]
        if outcome != lab_j2labels.VERIFIED:
            tail = (f": rebuilt seed-{word} labels sha256 {_sha12(est.shas[name])} against the pinned prefix "
                    f"{entry['rebuild_sha256_prefix'][:12]}" if est.shas[name] else "")
            print(f"j3 exclusion: source {name} {outcome}{tail}", flush=True)
    for name in ("J1", "A1", "K"):
        if sources[name]["outcome"] != lab_j2labels.VERIFIED:
            print(f"j3 exclusion: source {name} {sources[name]['outcome']}", flush=True)
    cycles = est.summary["cycles"]
    consistency = " ".join(f"{k}={'none' if v is None else v}" for k, v in est.summary["consistency"].items())
    print("j3 exclusion: " + " ".join(f"{c}={cycles[c]['by'] or 'not established'}" for c in lab_j3labels.CYCLES)
          + f" consistency {consistency} excluded {est.summary['excluded']}", flush=True)
    if est.problem == "unverified":
        raise _StepFailed("$.experiments.j3.exclude", J3_FRESHNESS_UNVERIFIED)
    if est.problem is not None:
        raise _StepFailed("$.experiments.j3.exclude", J3_EXCLUSION_DISAGREES)
    excluded_runs = lab_j3labels.excluded_runs(est, (used[0], used[1]), (requested[0], requested[1]),
                                               stand_in is not None)
    try:
        data, record = lab_j2labels.build_labels(labels["pack"], labels["n"], labels["seed"], fetch=nhtsa_fetch,
                                                 excluded=est.excluded, mechanisms=lab_j3labels.mechanisms(est),
                                                 excluded_runs=excluded_runs)
    except lab_j2labels.J2LabelsError as err:
        print(f"j3 draw: {err.problem}", flush=True)
        raise _StepFailed("$.experiments.j3.labels.n", J3_TOO_FEW_RECORDS) from None
    exclusion = lab_j3labels.extend_exclusion(record, est)
    exclusion.update(rebuilt_sha256={"j001": est.shas["R1"] or None, "a001": est.shas["R2"] or None},
                     rebuild_expected_prefix={"j001": used[0]["rebuild_sha256_prefix"],
                                              "a001": used[1]["rebuild_sha256_prefix"]},
                     stand_in=stand_in is not None)
    pack = loader.load_pack(ROOT / labels["pack"])
    try:
        questions_bytes, questions = lab_j1.build_questions(pack, data, seed=p["seed"], parts=p["parts"])
        lines = lab_j1.read_questions(questions_bytes)
        lexical_lines = lab_j1.lexical_verdicts(pack, data, lines)
    except lab_j1.J1Error as err:
        raise _StepFailed("$.experiments.j3", err.problem) from None
    prior_lines = lab_j1.prior_verdicts(lines)
    ablation_a = lab_j3.ablation_a_lines(lines, lexical_lines)
    ablation_b = lab_j3.ablation_b_lines(lines, lexical_lines, prior_lines)
    files = {"lexical": lexical_lines, "prior": prior_lines, "ablation_a": ablation_a, "ablation_b": ablation_b}
    bytes_of = {name: lab_j1.jsonl_bytes(rows) for name, rows in files.items()}
    scores = {name: lab_j1.score(lines, rows, bootstrap_b=p["bootstrap_b"], bootstrap_seed=p["bootstrap_seed"])
              for name, rows in files.items()}
    bound = lab_j1.prior_bound(lines)
    lexical_unknown = sum(1 for line in lexical_lines if line["verdict"] == "unknown")
    endpoints = sorted({u["model"] for u in units})
    routing = lab_j3.routing_doc(plan["models"], endpoints, PLACEHOLDER_BASE_URL)
    drawn = {r["record_ref"] for r, _ in lab_j1.parse_labels(data)}
    excluded_bytes = lab_j3labels.chain_bytes(est.excluded, drawn)
    excluded_lines = len(est.excluded | drawn)
    j3 = root / "prereg" / "j3"
    j3.mkdir(parents=True)
    (j3 / "labels.jsonl").write_bytes(data)
    write_json_atomic(j3 / "labels.json", record)
    (j3 / "questions.jsonl").write_bytes(questions_bytes)
    write_json_atomic(j3 / "questions.json", questions)
    for name in files:
        (j3 / f"{name}.jsonl").write_bytes(bytes_of[name])
        write_json_atomic(j3 / f"{name}.json", scores[name])
    (j3 / "excluded.jsonl").write_bytes(excluded_bytes)
    write_json_atomic(j3 / "routing.json", routing)
    write_json_atomic(j3 / "prereg.json", lab_j3.prereg_doc(
        pack=pack, pack_ref=labels["pack"], labels=record, questions=questions,
        lexical_sha256=sha256_hex(bytes_of["lexical"]), prior_sha256=sha256_hex(bytes_of["prior"]),
        ablation_a_sha256=sha256_hex(bytes_of["ablation_a"]), ablation_b_sha256=sha256_hex(bytes_of["ablation_b"]),
        routing=routing, seed=p["seed"], parts=p["parts"], bootstrap_b=p["bootstrap_b"],
        bootstrap_seed=p["bootstrap_seed"], primary=p["primary"], comparisons_before=p["comparisons_before"]))
    print(f"j3 draw: available {record['available']} excluded {exclusion['excluded']} eligible_after_exclusion "
          f"{exclusion['eligible_after_exclusion']} overlap {exclusion['overlap']} sources "
          + " ".join(f"{s}={sources[s]['outcome']}" for s in lab_j3labels.SOURCES)
          + " " + " ".join(f"{c}={cycles[c]['by']}" for c in lab_j3labels.CYCLES)
          + f" consistency {consistency} labels_sha256 {record['sha256'][:12]} questions_sha256 "
          f"{questions['sha256'][:12]} excluded_sha256 {sha256_hex(excluded_bytes)[:12]} excluded_lines "
          f"{excluded_lines} complaint_file_sha256 {exclusion['complaint_file_sha256'][:12]}", flush=True)
    print(f"j3 comparators: lexical balanced_accuracy {_interval(scores['lexical'])} record_blind "
          f"{_interval(scores['prior'])} predicate_only_bound {bound['value']:.3f} ablation_a "
          f"{_interval(scores['ablation_a'])} ablation_b {_interval(scores['ablation_b'])} lexical_unknown "
          f"{lexical_unknown}", flush=True)
    return {"labels": record, "questions": questions, "lexical": scores["lexical"], "prior": scores["prior"],
            "ablation_a": scores["ablation_a"], "ablation_b": scores["ablation_b"], "prior_bound": bound,
            "lexical_unknown": lexical_unknown, "endpoints": endpoints, "prereg": "prereg/j3/prereg.json",
            "parts": p["parts"], "seed": p["seed"], "bootstrap_b": p["bootstrap_b"],
            "bootstrap_seed": p["bootstrap_seed"], "exclusion": exclusion, "excluded_runs": excluded_runs,
            "stand_in": stand_in is not None, "excluded_sha256": sha256_hex(excluded_bytes),
            "excluded_lines": excluded_lines, "primary": dict(p["primary"]), "headline_arm": p["primary"]["arm"],
            "headline_secondary": lab_j3.HEADLINE_SECONDARY, "comparisons_before": p["comparisons_before"],
            "tasks": lab_j3.task_pins(), "mapping": lab_j3.mapping_pin(), "grounding": lab_j3.grounding_pin(),
            "composite": lab_j3.composite_pin(), "arms": lab_j3.arms_doc(), "lifts": lab_j3.lifts_doc(),
            "ablation_differences": lab_j3.ablation_differences_doc(),
            "target_balanced_accuracy": lab_j3.TARGET_BA}


def l1_cache_key(sha256: str) -> str:
    """K10: the workflow cache key of the MSHA file, ``lab-msha-<16 hex of its sha256>``."""
    return f"lab-msha-{sha256[:16]}"


def _l1(steps: _Steps, plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path,
        raw: Path | None) -> dict[str, Any]:
    """L1's preregistration (see the module docstring), in a subprocess; the manifest's ``l1`` block."""
    raw = raw if raw is not None else lab_l1.raw_dir(root)
    code = steps.run("l1-prereg", "$.experiments.l1", "lab.l1", "prereg",
                     [("plan", root / "plan.json"), ("raw", raw)], timeout_s=L1_TIMEOUT_S)
    if code == lab_l1.EXIT_INFRA:
        raise _StepFailed("$.experiments.l1", L1_FETCH_FAILED)
    stop = root / lab_l1.STOP_FILE
    if code != 0 or not (root / "prereg" / "l1" / "prereg.json").is_file():
        reason = "unknown"
        if stop.is_file():
            try:
                found = strict_load(stop.read_bytes()).get("stop")
                reason = found if found in lab_l1.STOPS else "unknown"
            except (OSError, StrictJsonError, AttributeError):
                reason = "unknown"
        raise _StepFailed("$.experiments.l1", L1_STOPPED.format(stop=reason))
    doc = strict_load((root / "prereg" / "l1" / "prereg.json").read_bytes())
    scores = strict_load((root / "prereg" / "l1" / "scores.json").read_bytes())
    p = units[0]["params"]
    return {"prereg": "prereg/l1/prereg.json", "scores_path": "prereg/l1/scores.json",
            "warmup": "prereg/l1/warmup.json", "input": doc["input"], "audit": doc["audit"],
            "demo_figures": doc["demo_figures"], "alert": doc["alert"], "mines": doc["mines"],
            "questions": [{**{k: q[k] for k in ("slot", "kind", "predicate", "question_id", "window", "routes",
                                                "strata", "records")}, "counts": lab_l1.question_counts(q),
                           "key_status": doc["judges"]["key"][str(q["slot"])]["status"]}
                          for q in doc["questions"]],
            "draws": doc["draws"], "scores": scores, "endpoints": sorted({u["model"] for u in units}),
            "cache_key": l1_cache_key(doc["input"]["sha256"]), "bootstrap_b": p["bootstrap_b"],
            "bootstrap_seed": p["bootstrap_seed"]}


ARCHIVE_UNREADABLE = (zipfile.BadZipFile, zlib.error, EOFError)    # an archive that is not a readable zip


def _jt(steps: _Steps, root: Path, nhtsa_fetch: Callable[[], bytes]) -> dict[str, Any]:
    """The term run's preregistration (see the module docstring); the manifest's ``jt`` block. Every stop is a fixed
    note: a download or an archive that cannot be read is :data:`~lab.notes.JT_FETCH_FAILED`; a failed check
    :data:`~lab.notes.JT_FLOOR_FAILED`; every other failure, in this process or in the draft's, is
    :data:`~lab.notes.JT_DRAFT_FAILED`; each prints a ``jt step: stopped ...`` line (wall seconds, the draft's wall
    seconds and the largest resident memory of a finished child of this process, which covers a killed draft)."""
    block = "$.experiments.jt"
    t0 = time.monotonic()
    timing: dict[str, float] = {}

    def stop(problem: str) -> _StepFailed:
        children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        draft_wall = f"{timing['draft']:.1f}" if "draft" in timing else "none"
        print(f"jt step: stopped wall_s {time.monotonic() - t0:.1f} draft_wall_s {draft_wall} draft_timeout_s "
              f"{lab_jt.DRAFT_TIMEOUT_S} children_peak_rss_kib {children}", flush=True)
        return _StepFailed(block, problem)

    try:
        blob = nhtsa_fetch()
    except (OSError, ValueError, http.client.HTTPException):
        raise stop(JT_FETCH_FAILED) from None
    try:
        pooled = lab_jt.pooled_export(blob)
        refs_bytes = lab_jt.drafting_refs_bytes(pooled.export)
        text_bytes = lab_jt.drafting_text_bytes(pooled.export)
    except ARCHIVE_UNREADABLE:
        raise stop(JT_FETCH_FAILED) from None
    except (ValueError, KeyError, OSError):         # ExportError and JtError are ValueErrors
        raise stop(JT_DRAFT_FAILED) from None
    scratch = tempfile.TemporaryDirectory(prefix="lab-jt-", dir=root.parent)
    try:
        work = Path(scratch.name)
        (work / "export.csv").write_bytes(pooled.data)
        t1 = time.monotonic()
        try:
            code = steps.run("jt-draft", block, JT_MODULE, JT_DRAFT_COMMAND,
                             [("export", work / "export.csv"), ("out", work / "draft")],
                             timeout_s=lab_jt.DRAFT_TIMEOUT_S)
        except _StepFailed:
            timing["draft"] = time.monotonic() - t1
            raise stop(JT_DRAFT_FAILED) from None
        draft_wall = timing["draft"] = time.monotonic() - t1
        draft: Any = None
        try:
            draft = strict_load((work / "draft" / lab_jt.DRAFT_RESULT).read_bytes())
        except (OSError, StrictJsonError):
            draft = None
        good = (isinstance(draft, dict) and draft.get("kind") == lab_jt.DRAFT_KIND
                and isinstance(draft.get("check"), dict))
        if code == lab_jt.EXIT_FLOOR and good:
            checks = lab_jt.check_counts(draft["check"])["checks"]
            failures = {name: b["failures"] for name, b in checks.items()
                        if not b.get("passed") and isinstance(b.get("failures"), int)}
            print("jt check: passed no " + " ".join(
                f"{name}={'passed' if b.get('passed') else 'failed'}" for name, b in checks.items())
                + "".join(f" {name}_failures {n}" for name, n in failures.items()), flush=True)
            raise stop(JT_FLOOR_FAILED)
        if code != 0 or not good or draft["check"].get("passed") is not True:
            raise stop(JT_DRAFT_FAILED)
        try:
            p_d = lab_jt.p_d_hashes(draft["terms"])
            terms = lab_jt.terms_document(archive=blob, pooled=pooled, refs_bytes=refs_bytes, text_bytes=text_bytes,
                                          draft=draft, p_d=p_d, hand=lab_jt.hand_pack().hashes())
            terms_bytes = lab_jt.document_bytes(terms)
            check = lab_jt.check_counts(draft["check"])
            peak = draft["peak_rss_kib"]
            if not isinstance(peak, int) or isinstance(peak, bool):
                raise TypeError("the draft's peak memory is not a count")
        except (loader.PackError, ValueError, KeyError, TypeError, AttributeError, OSError):
            raise stop(JT_DRAFT_FAILED) from None
        terms_sha = sha256_hex(terms_bytes)
        (root / lab_jt.JT_DIR).mkdir(parents=True)
        shutil.copytree(work / "draft" / "pack", root / lab_jt.PACK_DIR)
        (root / lab_jt.TERMS_FILE).write_bytes(terms_bytes)
        (root / lab_jt.DRAFTING_REFS_FILE).write_bytes(refs_bytes)
        (root / lab_jt.DRAFTING_TEXT_FILE).write_bytes(text_bytes)
        write_json_atomic(root / lab_jt.CHECK_FILE, check)
        write_json_atomic(root / lab_jt.PREREG_FILE, lab_jt.prereg_doc(
            terms, terms_sha, refs_bytes, text_bytes, code_commit=code_commit(),
            code_dirty=code_dirty(["lab/jt.py", lab_jt.ONBOARD_DIR])))
    finally:
        scratch.cleanup()
    wall = time.monotonic() - t0
    for line in lab_jt.console_lines(terms, terms_sha):
        print(line, flush=True)
    print(f"jt step: wall_s {wall:.1f} draft_wall_s {draft_wall:.1f} draft_timeout_s {lab_jt.DRAFT_TIMEOUT_S} "
          f"peak_rss_kib {peak}", flush=True)
    return {"prereg": lab_jt.PREREG_FILE, "terms": lab_jt.TERMS_FILE, "terms_sha256": terms_sha,
            "cutoff": terms["cutoff"], "window": terms["window"], "makes": terms["makes"],
            "archive": terms["archive"], "export": terms["export"], "drafting": terms["drafting"],
            "draft": terms["draft"], "term_counts": terms["term_counts"], "check": terms["check"],
            "p_draft": terms["p_draft"]["hashes"], "p_d": terms["p_d"]["hashes"], "hand": terms["hand"]["hashes"],
            "code": terms["code"],
            "step": {"wall_s": round(wall, 3), "draft_wall_s": round(draft_wall, 3),
                     "draft_timeout_s": lab_jt.DRAFT_TIMEOUT_S, "peak_rss_kib": peak}}


def _once(fetch: Callable[[], bytes] | None) -> Callable[[], bytes]:
    """``fetch``, or the NHTSA complaint archive's download, called at most once."""
    kept: list[bytes] = []

    def get() -> bytes:
        if not kept:
            if fetch is not None:
                kept.append(fetch())
            else:
                export = nhtsa_export()
                kept.append(export._fetch(export.COMPLAINTS_URL))
        return kept[0]

    return get


def _file_map(root: Path) -> dict[str, dict[str, Any]]:
    files = {}
    for path in sorted((root / "prereg").rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        files[rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
    return dict(sorted(files.items()))


def preregister(plan_path: Path, *, nhtsa_fetch: Callable[[], bytes] | None = None,
                l1_raw: Path | None = None, earlier_plans: Path | None = None,
                exclude_stand_in: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Every step the plan's units need, then the manifest; raises :class:`_StepFailed` for a refused step.
    ``nhtsa_fetch`` (tests only) returns the NHTSA complaint archive in place of its download; ``l1_raw`` names the
    directory of MSHA's file for L1 (default :func:`lab.l1.raw_dir`); ``earlier_plans`` the directory of the earlier
    plan artifacts for J2 and J3 (default :func:`earlier_dir`) and ``exclude_stand_in`` (tests only) a stand-in for
    the request's ``exclude`` entry (:func:`load_stand_in`: an object for J2, a two-entry list for J3)."""
    plan_bytes = plan_path.read_bytes()
    plan = strict_load(plan_bytes)
    root = plan_path.parent
    (root / "prereg").mkdir()
    steps = _Steps(root)
    e1_units, x1_units, e2_units, j1_units, l1_units, j2_units, j3_units, jt_units = (
        _units(plan, name) for name in ("e1", "x1", "e2", "j1", "l1", "j2", "j3", "jt"))
    fetch = _once(nhtsa_fetch)
    manifest: dict[str, Any] = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": sha256_hex(plan_bytes),
                                "files": {}, "e1": None, "x1": None, "e2": None, "j1": None, "l1": None, "j2": None,
                                "j3": None, "jt": None}
    if e1_units:
        manifest["e1"] = _e1(steps, plan, e1_units, root, fetch)
    if x1_units:
        manifest["x1"] = {"prereg": _harness(steps, x1_units[0], "x1", root)}
    if e2_units:
        manifest["e2"] = _e2(steps, e2_units[0], root)
    if j1_units:
        manifest["j1"] = _j1(plan, j1_units, root, fetch)
    if l1_units:
        manifest["l1"] = _l1(steps, plan, l1_units, root, l1_raw)
    if j2_units:
        manifest["j2"] = _j2(plan, j2_units, root, fetch,
                             earlier_plans if earlier_plans is not None else earlier_dir(root),
                             exclude_stand_in if isinstance(exclude_stand_in, Mapping) else None)
    if j3_units:
        manifest["j3"] = _j3(plan, j3_units, root, fetch,
                             earlier_plans if earlier_plans is not None else earlier_dir(root),
                             exclude_stand_in if isinstance(exclude_stand_in, (list, tuple)) else None)
    if jt_units:
        manifest["jt"] = _jt(steps, root, fetch)
    manifest["files"] = _file_map(root)
    write_json_atomic(root / MANIFEST, manifest)
    return manifest


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.prereg",
                                description="Preregister a plan's E1, J1, J2, J3, JT, L1, X1 and E2 units before any "
                                            "model runs.")
    p.add_argument("--plan", required=True)
    p.add_argument("--earlier-plans", help="the earlier plan artifacts for J2's or J3's exclusion, <DIR>/<run id>/ "
                                           "(default: lab-earlier beside the plan's directory)")
    p.add_argument("--nhtsa-archive", help="tests only: the NHTSA complaint archive in place of its download")
    p.add_argument("--exclude-stand-in", help="tests only: a stand-in for the J2 request's exclude entry (an "
                                              "object) or the J3 request's exclude list (two entries)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(os.path.abspath(args.plan))
    failed = False
    try:
        plan = strict_load(plan_path.read_bytes())
    except (OSError, StrictJsonError):
        failed = True
    if failed or not isinstance(plan, dict) or plan.get("kind") != "lab_plan" or not isinstance(plan.get("units"),
                                                                                                  list):
        print("error: the plan cannot be read", file=sys.stderr)
        return EXIT_USAGE
    if (plan_path.parent / "prereg").exists() or (plan_path.parent / "prereg").is_symlink():
        print("error: the plan's directory already holds a prereg/", file=sys.stderr)
        return EXIT_USAGE
    request = plan.get("request")
    request_path = shown_path(request.get("path")) if isinstance(request, dict) else None
    stand_in = None
    archive = Path(args.nhtsa_archive) if args.nhtsa_archive else None
    try:
        if args.exclude_stand_in:
            stand_in = load_stand_in(args.exclude_stand_in)
    except (OSError, StrictJsonError, RequestError):
        print("error: the exclusion stand-in cannot be read", file=sys.stderr)
        return EXIT_USAGE
    # only the options given are passed, so a caller's own keywords (a test's ``functools.partial`` of
    # :func:`preregister` with ``nhtsa_fetch``) are not overridden by an explicit None
    options: dict[str, Any] = {}
    if archive is not None:
        options["nhtsa_fetch"] = archive.read_bytes
    if args.earlier_plans:
        options["earlier_plans"] = Path(args.earlier_plans)
    if stand_in is not None:
        options["exclude_stand_in"] = stand_in
    try:
        manifest = preregister(plan_path, **options)
    except _StepFailed as err:
        return write_plan_error(plan_path.parent, "prereg", request_path, err.path, err.problem)
    shown = ("e1", "x1", "e2", "j1", *(("l1",) if manifest.get("l1") is not None else ()),
             *(("j2",) if manifest.get("j2") is not None else ()),
             *(("j3",) if manifest.get("j3") is not None else ()),
             *(("jt",) if manifest.get("jt") is not None else ()))
    print(" ".join(["prereg:", *(f"{name} {'yes' if manifest.get(name) is not None else 'no'}" for name in shown)]),
          flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
