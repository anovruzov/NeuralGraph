"""Preregister a plan's E1, J1, X1 and E2 units in the plan job, before any model runs.

    python -m lab.prereg --plan PLAN

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
* L1 (latency test L001, ``lab.l1``), in a subprocess whose stdout and stderr go to ``D/prereg-logs`` (K9: the step
  reads MSHA's file, so nothing of it reaches the console): ``lab.l1 prereg`` downloads the file into ``lab-msha``
  beside ``D`` (:func:`lab.l1.raw_dir`, the workflow's cache path) unless it is there, rebuilds the drafter demo's
  pipeline, asks every question with the key judge, the lexical judge, the record-blind control and the route-role
  baseline on fresh stores, and writes ``D/prereg/l1/prereg.json``, ``scores.json`` and ``warmup.json``. A stop of the
  rule (no alert, more than one, the demo's figures, the draws, ...) leaves ``D/prereg-l1-stop.json`` and refuses the
  plan with :data:`~lab.notes.L1_STOPPED` and the stop's code; a failed download refuses it with
  :data:`~lab.notes.L1_FETCH_FAILED`. The manifest's ``l1`` block holds ``cache_key`` (:func:`l1_cache_key`), which
  ``lab.l1 cache-key`` hands the workflow, so the plan job saves the file in the workflow's cache (K10);
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

stdout is one line, ``prereg: e1 yes|no x1 yes|no e2 yes|no j1 yes|no``, with `` l1 yes`` appended for a plan with L1
units. A plan that cannot be read, or a
``D/prereg`` that exists already, is a usage error (exit 2, nothing written). A refused step writes
``D/plan-error.json`` (source
``prereg``), prints ``error: <path>: <problem>`` first on stderr and an ``::error`` workflow command, and exits 2; the
manifest is then never written, so no shard can use a partial preregistration.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.experiments.common import write_json_atomic
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader

from . import EXIT_OK, EXIT_USAGE, ROOT, shown_path
from . import hosted as lab_hosted
from . import j1 as lab_j1
from . import l1 as lab_l1
from . import units as lab_units
from .goldlabels import GoldLabelsError, build_labels, nhtsa_export
from .notes import L1_FETCH_FAILED, L1_STOPPED
from .plan import write_plan_error

PLACEHOLDER_BASE_URL = "http://127.0.0.1:9/v1"
E1_BOUNDARY = "site:lab"
STEP_TIMEOUT_S = 300
REHEARSAL_TIMEOUT_S = 900
L1_TIMEOUT_S = 1080                 # the L1 step: the file, the draft, the audit and 25 paths on fresh stores
REHEARSAL_DEADLINE_S = 3600
REHEARSAL_BOOTSTRAP_B = 1000
E1_MODULE = "mycelic.collective.experiments.e1_extract"
X1_MODULE = "mycelic.collective.evaluate.harness"
E2_MODULE = "mycelic.collective.experiments.e2_pushdown"
MANIFEST = "prereg/prereg.json"
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
                l1_raw: Path | None = None) -> dict[str, Any]:
    """Every step the plan's units need, then the manifest; raises :class:`_StepFailed` for a refused step.
    ``nhtsa_fetch`` (tests only) returns the NHTSA complaint archive in place of its download; ``l1_raw`` names the
    directory of MSHA's file for L1 (default :func:`lab.l1.raw_dir`)."""
    plan_bytes = plan_path.read_bytes()
    plan = strict_load(plan_bytes)
    root = plan_path.parent
    (root / "prereg").mkdir()
    steps = _Steps(root)
    e1_units, x1_units, e2_units, j1_units, l1_units = (_units(plan, name)
                                                        for name in ("e1", "x1", "e2", "j1", "l1"))
    fetch = _once(nhtsa_fetch)
    manifest: dict[str, Any] = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": sha256_hex(plan_bytes),
                                "files": {}, "e1": None, "x1": None, "e2": None, "j1": None, "l1": None}
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
    manifest["files"] = _file_map(root)
    write_json_atomic(root / MANIFEST, manifest)
    return manifest


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.prereg",
                                description="Preregister a plan's E1, X1 and E2 units before any model runs.")
    p.add_argument("--plan", required=True)
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
    try:
        manifest = preregister(plan_path)
    except _StepFailed as err:
        return write_plan_error(plan_path.parent, "prereg", request_path, err.path, err.problem)
    shown = ("e1", "x1", "e2", "j1", *(("l1",) if manifest["l1"] is not None else ()))
    print(" ".join(["prereg:", *(f"{name} {'yes' if manifest[name] is not None else 'no'}" for name in shown)]),
          flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
