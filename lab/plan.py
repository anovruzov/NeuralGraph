"""Plan a lab run: validate the request and expand it, deterministically, into units and shards.

    python -m lab.plan (--request PATH | --event-path FILE --event-name push|workflow_dispatch)
        --manifest PATH --out DIR [--github-output FILE]

Paths are relative to the current directory; in event mode that is the repository (the workflow's workspace).
``--request`` accepts any regular ``<name>.json`` file; event mode finds the request with ``discover.py`` and
requires ``lab/requests/<name>.json``.

A unit is one experiment on one model: ``<experiment>-<model>`` (``unit_id``), run under the run id
``<unit>-<8 hex of sha256(request sha256 | unit)>``. A shard is the set of units one runner job executes, all on
one model: units are grouped by model (``none`` for model-free units), and inside a group placed first-fit
decreasing by minutes into shards of ``job_minutes - SHARD_OVERHEAD_MINUTES`` minutes, E3 units last, ties broken
by unit id. Shards are numbered ``sNNN-<label>`` in creation order; a shard runs its units in the order they were
placed, and its job timeout is its planned minutes plus the overhead.

``DIR/plan.json`` (canonical JSON; no clock, host or environment value, so two plans of the same request at the
same commit are byte-identical)::

    {"schema_version": 1, "kind": "lab_plan", "request": {"path", "name", "sha256", "purpose"}, "provider",
     "result_class": "plumbing" | "real", "manifest": {"path", "sha256"}, "lock": {"path", "sha256"}, "git_sha",
     "job_minutes", "max_parallel", "retention_days", "shard_overhead_minutes", "shard_capacity_minutes",
     "models": {"<key>": <resolved manifest entry> + {"lock": <lock entry> | null}},
     "units": [{"unit", "run_id", "experiment", "model", "kind", "minutes", "params", "seeds", "needs_secret",
                "env", "shard"}],                                         # sorted by unit id
     "shards": [{"shard", "model", "kind", "needs_secret", "units", "planned_minutes", "timeout_minutes"}],
     "skipped": [], "matrix": {"include": [...]},
     "provision": [{"target": "server" | "gguf", "key": "" | "<model key>", "entry", "cache_path", "cache_key",
                    "restore_key", "restore_prefix"}]}

``provision`` lists what the provision matrix downloads and verifies once per run: the server first, then each gguf
model the plan uses in sorted key order; empty when no shard serves a gguf model. ``entry`` (``server`` or
``gguf-<key>``) names the provision job's artifact; ``cache_path`` is the directory under the cache root
(``manifest.cache_dir``); ``restore_key`` is the sha-derived cache key when the lock pins the file, else ``""`` (an
unpinned file is only ever restored by ``restore_prefix`` and then re-verified); ``cache_key`` is the exact key the
provision job restores: ``restore_key``, or ``restore_prefix`` plus ``unlocked``, a key no save ever writes (saved
keys end in 16 hex).

``--github-output`` appends ``has_provision``, ``has_units``, ``matrix``, ``max_parallel``, ``plan_sha256``,
``provision_matrix`` (``{"include": <provision>}``), ``retention_days`` and ``result_class``, one ``name=value`` line
each. GitHub caps a job's outputs at 1 MB counted in UTF-16, so the plan refuses (``the job matrix would be too
large``) when these outputs plus the workflow's ``plan_artifact`` name would exceed :data:`MAX_OUTPUT_BYTES` UTF-16
bytes (:func:`check_outputs_size`). When the event runs nothing, no plan is written: ``DIR/plan-nothing.json``
(``{"schema_version": 1, "kind": "lab_plan_nothing", "notice": <the first notice, a fixed sentence>, "requests":
[<request paths of a merge that brought several>]}``; never a branch name or a command) records why, the notices are
printed as ``::notice`` lines and the outputs say so (``has_units=false``, ``has_provision=false``); exit 0. Any
problem writes ``DIR/plan-error.json``, prints ``error: <path>: <problem>`` as the first stderr line and an
``::error`` workflow command, and exits 2.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from mycelic.collective.experiments.common import RUN_ID_RE, write_json_atomic
from mycelic.collective.jsonio import canonical_dumps, sha256_hex

from . import EXIT_OK, EXIT_USAGE, LabError, display_path, gh_data, gh_property, safe_path, shown_path
from .discover import REQUEST_PATH_RE, DiscoveryError, discover, git
from .manifest import Manifest, ManifestError, cache_dir, cache_key, cache_prefix, load_manifest
from .request import EXPERIMENTS, SHARD_OVERHEAD_MINUTES, Request, RequestError, load_request

MAX_SHARDS = 256
MAX_MATRIX_BYTES = 1000000
MAX_OUTPUT_BYTES = 900_000
MAX_UNIT_ID = 55
UNIT_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.ASCII)
SHARD_ID_RE = re.compile(r"s[0-9]{3}-[a-z0-9][a-z0-9-]{0,23}", re.ASCII)
NO_MODEL = "none"
OUTPUT_KEYS = ("has_provision", "has_units", "matrix", "max_parallel", "plan_sha256", "provision_matrix",
               "retention_days", "result_class")


class PlanError(LabError):
    pass


# --------------------------------------------------------------------------------------------------- ids

def unit_id(experiment: str, model: str | None, suffix: str = "") -> str:
    """``<experiment>-<model>-<suffix>`` without the empty parts; suffix is ``""``, ``r<k>`` or ``s<seed>``."""
    uid = "-".join(p for p in (experiment, model or "", suffix) if p)
    if len(uid) > MAX_UNIT_ID or UNIT_ID_RE.fullmatch(uid) is None:
        raise PlanError("$.experiments", "a unit id would be malformed or longer than 55 characters") from None
    return uid


def run_id(uid: str, request_sha256: str) -> str:
    rid = f"{uid}-{sha256_hex(request_sha256 + '|' + uid)[:8]}"
    if len(rid) > 64 or RUN_ID_RE.fullmatch(rid) is None:
        raise PlanError("$.experiments", "a run id would be malformed or longer than 64 characters") from None
    return rid


# --------------------------------------------------------------------------------------------------- units and shards

def _params(experiment: str, block: dict[str, Any]) -> dict[str, Any]:
    if experiment == "e3":
        return {k: block[k] for k in ("concurrency", "requests", "warmup", "workloads", "seed")}
    return {k: block[k] for k in ("pack", "records", "seed")}


def build_units(request: Request, manifest: Manifest) -> list[dict[str, Any]]:
    units = []
    for experiment in EXPERIMENTS:
        block = request.data["experiments"].get(experiment)
        if block is None:
            continue
        for model in block["models"]:
            uid = unit_id(experiment, model)
            units.append({"unit": uid, "run_id": run_id(uid, request.sha256), "experiment": experiment,
                          "model": model, "kind": manifest.models[model]["kind"], "minutes": block["minutes"],
                          "params": _params(experiment, block), "seeds": [block["seed"]], "needs_secret": False,
                          "env": [], "shard": None})
    units.sort(key=lambda u: u["unit"])
    run_ids = [u["run_id"] for u in units]
    if len(set(run_ids)) != len(run_ids) or len({u["unit"] for u in units}) != len(units):
        raise PlanError("$.experiments", "two units would share an id") from None
    return units


def pack_shards(units: list[dict[str, Any]], capacity: int) -> list[dict[str, Any]]:
    """First-fit decreasing per (model label, needs_secret) group; see the module docstring."""
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for unit in units:
        groups.setdefault((unit["model"] or NO_MODEL, unit["needs_secret"]), []).append(unit)
    shards: list[dict[str, Any]] = []
    remaining: dict[str, int] = {}
    for label, needs_secret in sorted(groups):
        mine: list[dict[str, Any]] = []
        for unit in sorted(groups[(label, needs_secret)],
                           key=lambda u: (u["experiment"] == "e3", -u["minutes"], u["unit"])):
            if unit["minutes"] > capacity:
                raise PlanError("$.experiments", "a unit needs more minutes than a shard holds") from None
            target = next((s for s in mine if remaining[s["shard"]] >= unit["minutes"]), None)
            if target is None:
                if len(shards) >= MAX_SHARDS:
                    raise PlanError("$.experiments", f"the plan needs more than {MAX_SHARDS} shards") from None
                target = {"shard": f"s{len(shards) + 1:03d}-{label}", "model": unit["model"], "kind": unit["kind"],
                          "needs_secret": needs_secret, "units": [], "planned_minutes": 0, "timeout_minutes": 0}
                shards.append(target)
                mine.append(target)
                remaining[target["shard"]] = capacity
            target["units"].append(unit["unit"])
            target["planned_minutes"] += unit["minutes"]
            target["timeout_minutes"] = target["planned_minutes"] + SHARD_OVERHEAD_MINUTES
            remaining[target["shard"]] -= unit["minutes"]
    return shards


def check_matrix_size(obj: Any, limit: int = MAX_MATRIX_BYTES) -> None:
    """GitHub counts output sizes in UTF-16 code units of two bytes each."""
    if len(canonical_dumps(obj).encode("utf-16-le")) >= limit:
        raise PlanError("$.experiments", "the job matrix would be too large") from None


def output_size(outputs: dict[str, str]) -> int:
    """The UTF-16 size of ``name=value`` over all outputs, as GitHub measures a job's outputs."""
    return sum(len((name + "=" + value).encode("utf-16-le")) for name, value in outputs.items())


def check_outputs_size(outputs: dict[str, str], limit: int | None = None) -> None:
    if output_size(outputs) > (MAX_OUTPUT_BYTES if limit is None else limit):
        raise PlanError("$.experiments", "the job matrix would be too large") from None


def provision_entries(manifest: Manifest, gguf_keys: list[str]) -> list[dict[str, Any]]:
    """The server, then each gguf key in sorted order; nothing without a gguf key."""
    if not gguf_keys:
        return []

    def entry(target: str, key: str, name: str, locked: dict[str, Any] | None) -> dict[str, Any]:
        restore_key = cache_key(target, name, locked["sha256"]) if locked is not None else ""
        return {"target": target, "key": key, "entry": "server" if target == "server" else f"gguf-{key}",
                "cache_path": cache_dir(target, name),
                "cache_key": restore_key or cache_prefix(target, name) + "unlocked",
                "restore_key": restore_key, "restore_prefix": cache_prefix(target, name)}

    tag = manifest.server["tag"]
    return [entry("server", "", tag, manifest.lock.server),
            *(entry("gguf", key, key, manifest.lock.models.get(key)) for key in sorted(gguf_keys))]


def build_plan(request: Request, manifest: Manifest, git_sha: str) -> dict[str, Any]:
    data = request.data
    capacity = data["job_minutes"] - SHARD_OVERHEAD_MINUTES
    units = build_units(request, manifest)
    shards = pack_shards(units, capacity)
    shard_of = {uid: s["shard"] for s in shards for uid in s["units"]}
    for unit in units:
        unit["shard"] = shard_of[unit["unit"]]
    matrix = {"include": [{"shard": s["shard"], "model": s["model"] or "", "kind": s["kind"],
                           "timeout_minutes": s["timeout_minutes"],
                           "gguf_key": s["model"] if s["kind"] == "gguf" else "", "hosted": False, "openfda": False}
                          for s in shards]}
    check_matrix_size(matrix)
    used = sorted({u["model"] for u in units if u["model"]})
    provision = provision_entries(manifest, sorted({s["model"] for s in shards if s["kind"] == "gguf"}))
    check_matrix_size({"include": provision})
    plan = {
        "schema_version": 1, "kind": "lab_plan",
        "request": {"path": request.path, "name": request.name, "sha256": request.sha256, "purpose": data["purpose"]},
        "provider": data["provider"], "result_class": "plumbing" if data["provider"] == "fake" else "real",
        "manifest": {"path": manifest.path, "sha256": manifest.sha256},
        "lock": {"path": manifest.lock.path, "sha256": manifest.lock.sha256},
        "git_sha": git_sha, "job_minutes": data["job_minutes"], "max_parallel": data["max_parallel"],
        "retention_days": data["retention_days"], "shard_overhead_minutes": SHARD_OVERHEAD_MINUTES,
        "shard_capacity_minutes": capacity,
        "models": {key: {**manifest.models[key], "lock": manifest.lock.models.get(key)} for key in used},
        "units": units, "shards": shards, "skipped": [], "matrix": matrix, "provision": provision,
    }
    check_outputs_size(plan_outputs(plan, "0" * 64) | {"plan_artifact": "x" * 64})
    return plan


# --------------------------------------------------------------------------------------------------- outputs

def write_github_output(path: str | Path, outputs: dict[str, str]) -> None:
    lines = []
    for name, value in outputs.items():
        if "\r" in value or "\n" in value:
            raise ValueError(f"output {name} would span lines") from None
        lines.append(f"{name}={value}\n")
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(lines))


def plan_outputs(plan: dict[str, Any] | None, plan_sha256: str) -> dict[str, str]:
    if plan is None:
        return {"has_provision": "false", "has_units": "false", "matrix": canonical_dumps({"include": []}),
                "max_parallel": "1", "plan_sha256": "", "provision_matrix": canonical_dumps({"include": []}),
                "retention_days": "1", "result_class": "none"}
    return {"has_provision": "true" if plan["provision"] else "false",
            "has_units": "true" if plan["units"] else "false", "matrix": canonical_dumps(plan["matrix"]),
            "max_parallel": str(min(plan["max_parallel"], len(plan["shards"]))), "plan_sha256": plan_sha256,
            "provision_matrix": canonical_dumps({"include": plan["provision"]}),
            "retention_days": str(plan["retention_days"]), "result_class": plan["result_class"]}


def git_sha() -> str:
    r = git(Path.cwd(), "rev-parse", "HEAD")
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"


def _report(out: Path, err: LabError, request_path: str | None) -> int:
    if isinstance(err, ManifestError):
        source, path = err.source, safe_path(err.json_path)
    else:
        source = {RequestError: "request", DiscoveryError: "event"}.get(type(err), "plan")
        path = safe_path(err.path)
    shown = f"{source} {path}" if source in ("manifest", "lock") else path
    hints = list(err.hints) if isinstance(err, DiscoveryError) else []
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "plan-error.json", {"schema_version": 1, "kind": "lab_plan_error", "source": source,
                                                "request": request_path, "path": path, "problem": err.problem})
    print(f"error: {shown}: {err.problem}", file=sys.stderr)
    for line in hints:
        print(gh_data(line), file=sys.stderr)
    file_property = f" file={gh_property(request_path)}" if request_path else ""
    print(f"::error{file_property}::{gh_data(chr(10).join([f'{shown}: {err.problem}', *hints]))}")
    return EXIT_USAGE


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.plan", description="Validate a lab request and plan its shards.")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", help="a request file (any directory)")
    source.add_argument("--event-path", help="the GitHub event file (GITHUB_EVENT_PATH)")
    p.add_argument("--event-name", choices=("push", "workflow_dispatch"))
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--github-output")
    return p


def main(argv: list[str] | None = None) -> int:
    p = _parser()
    args = p.parse_args(argv)
    if (args.event_path is None) != (args.event_name is None):
        p.error("--event-path and --event-name go together")
    out = Path(args.out)
    if (out / "plan.json").exists() or (out / "plan.json").is_symlink():
        print("error: --out already holds a plan.json", file=sys.stderr)
        return EXIT_USAGE
    request_path = None
    try:
        manifest = load_manifest(args.manifest)
        if args.request is not None:
            path, strict = args.request, False
        else:
            found = discover(args.event_path, args.event_name, Path.cwd())
            if found.error is not None:
                raise found.error
            if found.outcome == "nothing":
                out.mkdir(parents=True, exist_ok=True)
                write_json_atomic(out / "plan-nothing.json", {
                    "schema_version": 1, "kind": "lab_plan_nothing", "notice": found.notices[0],
                    "requests": [p for p in found.requests if REQUEST_PATH_RE.fullmatch(p)]})
                for notice in found.notices:
                    print(f"::notice::{gh_data(notice)}")
                if args.github_output:
                    write_github_output(args.github_output, plan_outputs(None, ""))
                return EXIT_OK
            path, strict = found.request, True
        request_path = shown_path(display_path(path))
        request = load_request(path, manifest, strict_location=strict)
        plan = build_plan(request, manifest, git_sha())
    except LabError as err:
        return _report(out, err, request_path)
    out.mkdir(parents=True, exist_ok=True)
    plan_sha256 = write_json_atomic(out / "plan.json", plan)
    if args.github_output:
        write_github_output(args.github_output, plan_outputs(plan, plan_sha256))
    print(f"plan: {len(plan['units'])} units in {len(plan['shards'])} shards ({plan['result_class']})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
