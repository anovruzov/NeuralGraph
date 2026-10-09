"""Preregister a plan's E1, X1 and E2 units in the plan job, before any model runs.

    python -m lab.prereg --plan PLAN

``D`` is the plan's directory. Each step runs only when the plan holds units that need it:

* E1: :func:`lab.goldlabels.build_labels` writes ``D/prereg/e1/labels.jsonl`` and its record ``labels.json``; the
  routing file ``routing.json`` pins every E1 endpoint, in sorted model-key order (:func:`e1_routing_doc` at
  :data:`PLACEHOLDER_BASE_URL`: the pins never include the address, so a shard's routing to its own server matches
  them); then ``e1_extract prereg`` (data label ``synthetic``, or ``public`` for ``nhtsa`` labels; boundary
  :data:`E1_BOUNDARY`; the synthetic raw-text
  exemption only when an endpoint lies outside every boundary, which a hosted endpoint does: ``lab.hosted.endpoint``,
  boundary ``external``) writes ``D/prereg/e1/prereg/prereg.json``;
* X1: the evaluation harness's prereg (run id ``x1``, ``D/prereg/x1/x1/prereg.json``) and ``check-plant``;
* E2: the same with run id ``e2`` (``D/prereg/x1/e2/prereg.json``) and ``check-plant``, then a model-free rehearsal:
  ``e2_pushdown run`` without routing (fake site and central judges) in a scratch directory beside ``D``, removed
  afterwards. It picks the same candidates and retrieves the same central_raw records as the real run, so its counts
  size the real run: ``D/prereg/e2/rehearsal.json`` holds the candidates, the judge calls per task (ledger rows with
  attempt 0 or 1), the central_raw and central_allowed record counts (max and sum over the items), the item with the
  most raw records (``worst``), the pipeline and wall seconds and the e2.json ``content_hash``.

Last, ``D/prereg/prereg.json`` (the manifest): the plan's sha256, the sha256 and size of every other file under
``D/prereg``, and per experiment (null when the plan has none) the labels record, endpoints, reference and prereg path
(E1), the prereg path (X1), and the prereg path, plant path and rehearsal (E2). Every shard and the aggregate call
:func:`load_prereg` before they use any of it. Harness logs go to ``D/prereg-logs/<step>.stdout.log`` and
``.stderr.log`` (capped; not in the manifest). Each subprocess gets the units' environment allowlist, the repository
as its directory, and :data:`STEP_TIMEOUT_S` (:data:`REHEARSAL_TIMEOUT_S` for the rehearsal).

stdout is one line, ``prereg: e1 yes|no x1 yes|no e2 yes|no``. A plan that cannot be read, or a ``D/prereg`` that
exists already, is a usage error (exit 2, nothing written). A refused step writes ``D/plan-error.json`` (source
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
from typing import Any, Mapping, Sequence

from mycelic.collective.experiments.common import write_json_atomic
from mycelic.collective.inference.ledger import read_ledger
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import EXIT_OK, EXIT_USAGE, ROOT, shown_path
from . import hosted as lab_hosted
from . import units as lab_units
from .goldlabels import GoldLabelsError, build_labels
from .plan import write_plan_error

PLACEHOLDER_BASE_URL = "http://127.0.0.1:9/v1"
E1_BOUNDARY = "site:lab"
STEP_TIMEOUT_S = 300
REHEARSAL_TIMEOUT_S = 900
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


def _e1(steps: _Steps, plan: Mapping[str, Any], units: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    params = units[0]["params"]
    labels = params["labels"]
    try:
        data, record = build_labels(labels["source"], labels["pack"], labels["n"], labels["seed"])
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


def _file_map(root: Path) -> dict[str, dict[str, Any]]:
    files = {}
    for path in sorted((root / "prereg").rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        files[rel] = {"sha256": sha256_hex(data), "bytes": len(data)}
    return dict(sorted(files.items()))


def preregister(plan_path: Path) -> dict[str, Any]:
    """Every step the plan's units need, then the manifest; raises :class:`_StepFailed` for a refused step."""
    plan_bytes = plan_path.read_bytes()
    plan = strict_load(plan_bytes)
    root = plan_path.parent
    (root / "prereg").mkdir()
    steps = _Steps(root)
    e1_units, x1_units, e2_units = (_units(plan, name) for name in ("e1", "x1", "e2"))
    manifest: dict[str, Any] = {"schema_version": 1, "kind": "lab_prereg", "plan_sha256": sha256_hex(plan_bytes),
                                "files": {}, "e1": None, "x1": None, "e2": None}
    if e1_units:
        manifest["e1"] = _e1(steps, plan, e1_units, root)
    if x1_units:
        manifest["x1"] = {"prereg": _harness(steps, x1_units[0], "x1", root)}
    if e2_units:
        manifest["e2"] = _e2(steps, e2_units[0], root)
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
    print(" ".join(["prereg:", *(f"{name} {'yes' if manifest[name] is not None else 'no'}"
                                 for name in ("e1", "x1", "e2"))]), flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
