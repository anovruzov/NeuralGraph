"""Plan a lab run: validate the request and expand it, deterministically, into units and shards.

    python -m lab.plan (--request PATH | --event-path FILE --event-name push|workflow_dispatch)
        --manifest PATH --out DIR [--github-output FILE]

Paths are relative to the current directory; in event mode that is the repository (the workflow's workspace).
``--request`` accepts any regular ``<name>.json`` file; event mode finds the request with ``discover.py`` and
requires ``lab/requests/<name>.json``.

A unit is one experiment on one model: ``<experiment>-<model>`` (``unit_id``), run under the run id ``<unit>-<8 hex of
sha256(request sha256 | unit)>``; a sim unit is one model and one seed, ``sim-<model>-s<seed>``, whose params are the
plant, its pack, the weeks, ``lab.sim.world_settings`` (evaluation weeks and grace), the sim's tie salt, bootstrap
settings, ``top_n`` and the seed. An E1 unit is one model and one repeat, ``e1-<model>-r<k>`` (params: the labels'
pack, the labels, the model as ``endpoint``, the reference, ``repeat``, ``runs``, ``margin_points``, ``seed`` and
``bootstrap_b``); an E2 unit is one model, ``e2-<model>`` (params: the block's world and harness settings, the plant's
repository path ``plant_path``, the pack's site count ``sites``, the sorted seeds, the first of them as ``seed`` and
``central_context_tokens``, :func:`central_context_tokens`); ``x1`` and ``openfda`` are one model-free unit each
(model None, kind ``none``; X1 takes E2's params without ``top_n``, ``min_candidates``, ``central`` and
``central_context_tokens``, openFDA the normalised block plus ``requests_estimate``). A unit's ``kind`` is its model's
manifest kind (``hosted`` for an E1 unit of a hosted model). A unit that calls the hosted provider
(``lab.hosted.role``: an E1 unit of a hosted model, an E2 unit whose ``central`` is hosted) has ``needs_secret`` true
and ``env`` ``["MYCELIC_LAB_HOSTED_API_KEY"]``; every other unit false and ``[]``.

A shard is the set of units one runner job executes: units are grouped by (label, needs_secret), the label being the
model (``none`` for model-free units, ``hosted`` for every hosted unit, whatever its key), and inside a group placed
first-fit decreasing into shards of ``job_minutes - SHARD_OVERHEAD_MINUTES`` minutes by (serving class rank, -minutes,
unit id), the classes (:func:`serving_class`) ranked ``quality``, ``e2``, ``e3``. An E2 unit with a hosted central
comparator therefore gets a shard of its own, apart from its model's other units, so only the jobs that call the host
get the secrets. Shards are numbered ``sNNN-<label>`` in creation order; a shard runs its units in the order they
were placed, and its job timeout is its planned minutes plus the overhead. A shard's ``model`` is its units' model
(None for the ``none`` and ``hosted`` groups) and its ``kind`` theirs; the hosted units must fit one shard
(``$.experiments.e1.minutes``, exit 2).

**Hosted secrets.** The plan job learns only whether both hosted secrets are set: ``LAB_HAS_HOSTED`` is ``true`` or
anything else (absent). Without them, :func:`filter_hosted` removes, before packing (so before the
preregistration), every hosted E1 unit, then every E1 unit when fewer than two E1 models remain or the reference was
hosted, and every E2 unit with a hosted central comparator (never run with ``self`` in its place); ``skipped`` lists
them (``{"unit", "experiment", "model", "reason"}``, sorted by unit), the other units keep their run ids, and stdout
gets one ``::notice title=lab hosted skipped::`` line naming both secrets and the skipped units. A plan left with no
unit is valid (``has_units=false``, exit 0). With them, ``hosted`` holds per used key ``{"model": <the host's model
id>, "max_calls", "bound", "shares": {<shard>: <share>}}``: a shard's share is the preflight's
``PREFLIGHT_MAX_CALLS`` per canary task plus ``lab.hosted.unit_bound`` of each of its units of the key, and ``bound``
is their sum, the request's own bound (``lab.request``), at most ``max_calls``. ``hosted`` is ``{}`` without hosted
units.

Nothing from the preregistration (``lab.prereg``, which the workflow runs right after this) goes into the plan.

``DIR/plan.json`` (canonical JSON; no clock, host or environment value, so two plans of the same request, commit and
secret presence are byte-identical)::

    {"schema_version": 1, "kind": "lab_plan", "request": {"path", "name", "sha256", "purpose"}, "provider",
     "result_class": "plumbing" | "real", "manifest": {"path", "sha256"}, "lock": {"path", "sha256"}, "git_sha",
     "job_minutes", "max_parallel", "retention_days", "shard_overhead_minutes", "shard_capacity_minutes",
     "models": {"<key>": <resolved manifest entry> + {"lock": <lock entry> | null}},   # every model a unit uses,
                                                                                      # hosted centrals included
     "units": [{"unit", "run_id", "experiment", "model", "kind", "minutes", "params", "seeds", "needs_secret",
                "env", "shard"}],                                         # sorted by unit id
     "shards": [{"shard", "model", "kind", "needs_secret", "units", "planned_minutes", "timeout_minutes"}],
     "skipped": [{"unit", "experiment", "model", "reason"}], "matrix": {"include": [...]},
     "provision": [{"target": "server" | "gguf", "key": "" | "<model key>", "entry", "cache_path", "cache_key",
                    "restore_key", "restore_prefix"}],
     "hosted": {"<key>": {"model", "max_calls", "bound", "shares"}}}

A matrix entry's ``openfda`` is true exactly when its shard holds the openFDA unit (the run step then passes the
openFDA key secret, when there is one, to that shard only), and ``hosted`` is the shard's ``needs_secret`` (the run
step passes the two hosted secrets to those shards only); a hosted shard's ``model`` and ``gguf_key`` are empty.

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
problem writes ``DIR/plan-error.json`` (:func:`write_plan_error`, which ``lab.prereg`` uses too), prints ``error:
<path>: <problem>`` as the first stderr line and an ``::error`` workflow command, and exits 2. The openFDA fetch budget
depends on whether the repository has the openFDA key secret: the workflow says so in ``LAB_HAS_OPENFDA_KEY``
(``true`` or anything else), never with the key itself.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from mycelic.collective.experiments.common import RUN_ID_RE, write_json_atomic
from mycelic.collective.jsonio import canonical_dumps, sha256_hex

from . import EXIT_OK, EXIT_USAGE, ROOT, LabError, display_path, gh_data, gh_property, safe_path, shown_path
from .discover import REQUEST_PATH_RE, DiscoveryError, discover, git
from .manifest import CTX_DEFAULTS, Manifest, ManifestError, cache_dir, cache_key, cache_prefix, load_manifest
from mycelic.collective.packs import loader

from .hosted import HAS_VAR, KEY_VAR, PREFLIGHT_MAX_CALLS, preflight_tasks, role, unit_bound
from .notes import E1_WITHOUT_HOSTED, E2_CENTRAL_HOSTED_SKIPPED, HOSTED_NOTICE_TITLE, HOSTED_SECRETS_MISSING
from .request import (EXPERIMENTS, SHARD_OVERHEAD_MINUTES, Request, RequestError, e1_records, load_request,
                      openfda_requests)
from .sim import BOOTSTRAP_B, BOOTSTRAP_SEED, PLANTS, TIE_SALT, world_settings

MAX_SHARDS = 256
MAX_MATRIX_BYTES = 1000000
MAX_OUTPUT_BYTES = 900_000
MAX_UNIT_ID = 55
UNIT_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.ASCII)
SHARD_ID_RE = re.compile(r"s[0-9]{3}-[a-z0-9][a-z0-9-]{0,23}", re.ASCII)
NO_MODEL = "none"
HOSTED_GROUP = "hosted"
CLASS_RANK = {"quality": 0, "e2": 1, "e3": 2}
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

def serving_class(experiment: str) -> str:
    """Which model-server start a unit needs: ``e3`` (the E3 slots), ``e2`` (one long-context slot) or ``quality``."""
    return experiment if experiment in ("e3", "e2") else "quality"


def _params(experiment: str, block: dict[str, Any], seed: int) -> dict[str, Any]:
    if experiment == "e3":
        return {k: block[k] for k in ("concurrency", "requests", "warmup", "workloads", "seed")}
    if experiment == "sim":
        plant = PLANTS[block["plant"]]
        return {"plant": plant.name, "pack": plant.pack, "weeks": block["weeks"],
                **world_settings(plant, block["weeks"]), "tie_salt": TIE_SALT, "top_n": block["top_n"],
                "bootstrap_b": BOOTSTRAP_B, "bootstrap_seed": BOOTSTRAP_SEED, "seed": seed}
    return {k: block[k] for k in ("pack", "records", "seed")}


def _world_params(block: dict[str, Any]) -> dict[str, Any]:
    """The params E2 and X1 units share: the world, the plant and the harness settings."""
    pack = loader.load_pack(block["pack"])
    plant_path = (loader.BUILTIN_ROOT / block["pack"] / "fixtures" / f"{block['plant']}.json").relative_to(ROOT)
    seeds = sorted(block["seeds"])
    return {"pack": block["pack"], "plant": block["plant"], "plant_path": plant_path.as_posix(),
            "sites": len(pack.generator["sites"]), "seeds": seeds, "seed": seeds[0],
            **{k: block[k] for k in ("weeks", "eval_from", "eval_to", "grace_weeks", "tie_salt", "detector_author")}}


def central_context_tokens(central: str, model: str, manifest: Manifest) -> int:
    """The context one E2 central request gets: a hosted central comparator's ``context_tokens``; for ``self``, the
    model's ``e2_ctx_per_slot`` (its E2 server start's one slot), or ``CTX_DEFAULTS["e2_ctx_per_slot"]`` for an entry
    without one (fake)."""
    if central != "self":
        return manifest.models[central]["context_tokens"]
    return manifest.models[model].get("e2_ctx_per_slot", CTX_DEFAULTS["e2_ctx_per_slot"])


def _unit(request: Request, manifest: Manifest, experiment: str, model: str | None, suffix: str, minutes: int,
          params: dict[str, Any], seeds: list[int]) -> dict[str, Any]:
    uid = unit_id(experiment, model, suffix)
    return {"unit": uid, "run_id": run_id(uid, request.sha256), "experiment": experiment, "model": model,
            "kind": manifest.models[model]["kind"] if model is not None else NO_MODEL, "minutes": minutes,
            "params": params, "seeds": seeds, "needs_secret": False, "env": [], "shard": None}


def build_units(request: Request, manifest: Manifest) -> list[dict[str, Any]]:
    units = []
    for experiment in EXPERIMENTS:
        block = request.data["experiments"].get(experiment)
        if block is None:
            continue
        if experiment == "e1":
            common = {"pack": block["labels"]["pack"], "labels": block["labels"]}
            for model in block["models"]:
                for k in range(1, block["runs"] + 1):
                    params = {**common, "endpoint": model, "reference": block["reference"], "repeat": k,
                              **{key: block[key] for key in ("runs", "margin_points", "seed", "bootstrap_b")}}
                    units.append(_unit(request, manifest, "e1", model, f"r{k}", block["minutes"], params,
                                       [block["seed"]]))
        elif experiment == "e2":
            params = {**_world_params(block), **{key: block[key] for key in ("top_n", "min_candidates", "central",
                                                                              "bootstrap_b", "bootstrap_seed")}}
            for model in block["models"]:
                units.append(_unit(request, manifest, "e2", model, "", block["minutes"],
                                   {**params, "central_context_tokens": central_context_tokens(
                                       block["central"], model, manifest)}, list(params["seeds"])))
        elif experiment == "x1":
            params = {**_world_params(block), **{key: block[key] for key in ("bootstrap_b", "bootstrap_seed")}}
            units.append(_unit(request, manifest, "x1", None, "", block["minutes"], params, list(params["seeds"])))
        elif experiment == "openfda":
            params = {**block, "requests_estimate": openfda_requests(block["product_codes"],
                                                                      block["max_records_per_code"])}
            units.append(_unit(request, manifest, "openfda", None, "", block["minutes"], params, []))
        else:
            for model in block["models"]:
                for seed in (block["seeds"] if experiment == "sim" else [block["seed"]]):
                    units.append(_unit(request, manifest, experiment, model, f"s{seed}" if experiment == "sim" else "",
                                       block["minutes"], _params(experiment, block, seed), [seed]))
    for unit in units:
        if role(unit, manifest.models) is not None:
            unit.update(needs_secret=True, env=[KEY_VAR])
    units.sort(key=lambda u: u["unit"])
    run_ids = [u["run_id"] for u in units]
    if len(set(run_ids)) != len(run_ids) or len({u["unit"] for u in units}) != len(units):
        raise PlanError("$.experiments", "two units would share an id") from None
    return units


def group_label(unit: dict[str, Any]) -> str:
    """The shard group of a unit: :data:`HOSTED_GROUP` for every hosted unit, else its model or :data:`NO_MODEL`."""
    return HOSTED_GROUP if unit["kind"] == "hosted" else unit["model"] or NO_MODEL


def pack_shards(units: list[dict[str, Any]], capacity: int) -> list[dict[str, Any]]:
    """First-fit decreasing per (group label, needs_secret) group; see the module docstring."""
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for unit in units:
        groups.setdefault((group_label(unit), unit["needs_secret"]), []).append(unit)
    shards: list[dict[str, Any]] = []
    remaining: dict[str, int] = {}
    for label, needs_secret in sorted(groups):
        mine: list[dict[str, Any]] = []
        for unit in sorted(groups[(label, needs_secret)],
                           key=lambda u: (CLASS_RANK[serving_class(u["experiment"])], -u["minutes"], u["unit"])):
            if unit["minutes"] > capacity:
                raise PlanError("$.experiments", "a unit needs more minutes than a shard holds") from None
            target = next((s for s in mine if remaining[s["shard"]] >= unit["minutes"]), None)
            if target is None:
                if len(shards) >= MAX_SHARDS:
                    raise PlanError("$.experiments", f"the plan needs more than {MAX_SHARDS} shards") from None
                target = {"shard": f"s{len(shards) + 1:03d}-{label}",
                          "model": None if label == HOSTED_GROUP else unit["model"], "kind": unit["kind"],
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


def filter_hosted(units: list[dict[str, Any]], request: Request,
                  manifest: Manifest) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Without the hosted secrets: (the units that remain, the skipped units). Every hosted E1 unit is skipped
    (:data:`~lab.notes.HOSTED_SECRETS_MISSING`); then every remaining E1 unit when fewer than two E1 models remain or
    the reference was hosted (:data:`~lab.notes.E1_WITHOUT_HOSTED`); and every E2 unit whose central comparator is
    hosted (:data:`~lab.notes.E2_CENTRAL_HOSTED_SKIPPED`), never run with the model itself in its place."""
    reasons: dict[str, str] = {}
    for unit in units:
        found = role(unit, manifest.models)
        if found is not None:
            reasons[unit["unit"]] = HOSTED_SECRETS_MISSING if found[0] == "endpoint" else E2_CENTRAL_HOSTED_SKIPPED
    e1 = request.data["experiments"].get("e1")
    if e1 is not None:
        left = {u["model"] for u in units if u["experiment"] == "e1" and u["unit"] not in reasons}
        if len(left) < 2 or manifest.models[e1["reference"]]["kind"] == "hosted":
            for unit in units:
                if unit["experiment"] == "e1" and unit["unit"] not in reasons:
                    reasons[unit["unit"]] = E1_WITHOUT_HOSTED
    skipped = [{"unit": u["unit"], "experiment": u["experiment"], "model": u["model"], "reason": reasons[u["unit"]]}
               for u in units if u["unit"] in reasons]
    return [u for u in units if u["unit"] not in reasons], sorted(skipped, key=lambda s: s["unit"])


def hosted_shares(request: Request, manifest: Manifest, units: list[dict[str, Any]]) -> dict[str, Any]:
    """``plan.hosted``: per hosted key its model id, ``max_calls``, and each shard's share of the calls (the preflight
    of every canary task plus ``unit_bound`` of each of the shard's units of the key), whose sum is ``bound``."""
    shares: dict[str, dict[str, int]] = {}
    records: int | None = None                  # one E1 block, so one label set for every E1 unit
    for unit in units:
        found = role(unit, manifest.models)
        if found is None:
            continue
        key, shard = found[1], unit["shard"]
        p = unit["params"]
        if unit["experiment"] == "e1":
            records = e1_records(p["labels"]) if records is None else records
            calls = unit_bound("e1", records=records)
        else:
            calls = unit_bound("e2", top_n=p["top_n"], seeds=len(p["seeds"]))
        mine = shares.setdefault(key, {})
        if shard not in mine:
            mine[shard] = PREFLIGHT_MAX_CALLS * preflight_tasks(unit["experiment"])
        mine[shard] += calls
    return {key: {"model": manifest.models[key]["model"], "max_calls": request.data["hosted"][key]["max_calls"],
                  "bound": sum(shares[key].values()), "shares": dict(sorted(shares[key].items()))}
            for key in sorted(shares)}


def build_plan(request: Request, manifest: Manifest, git_sha: str, *, has_hosted: bool = False) -> dict[str, Any]:
    data = request.data
    capacity = data["job_minutes"] - SHARD_OVERHEAD_MINUTES
    units = build_units(request, manifest)
    skipped: list[dict[str, Any]] = []
    if not has_hosted:
        units, skipped = filter_hosted(units, request, manifest)
    shards = pack_shards(units, capacity)
    if sum(1 for s in shards if s["kind"] == "hosted") > 1:
        raise PlanError("$.experiments.e1.minutes", "the hosted E1 units need more minutes than one shard holds; lower "
                                                    "e1.minutes or e1.runs, or raise job_minutes") from None
    shard_of = {uid: s["shard"] for s in shards for uid in s["units"]}
    for unit in units:
        unit["shard"] = shard_of[unit["unit"]]
    matrix = {"include": [{"shard": s["shard"], "model": s["model"] or "", "kind": s["kind"],
                           "timeout_minutes": s["timeout_minutes"],
                           "gguf_key": s["model"] if s["kind"] == "gguf" else "", "hosted": s["needs_secret"],
                           "openfda": any(u == "openfda" for u in s["units"])}
                          for s in shards]}
    check_matrix_size(matrix)
    roles = [role(u, manifest.models) for u in units]
    used = sorted({u["model"] for u in units if u["model"]} | {r[1] for r in roles if r is not None})
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
        "units": units, "shards": shards, "skipped": skipped, "matrix": matrix, "provision": provision,
        "hosted": hosted_shares(request, manifest, units),
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
            "max_parallel": str(max(1, min(plan["max_parallel"], len(plan["shards"])))), "plan_sha256": plan_sha256,
            "provision_matrix": canonical_dumps({"include": plan["provision"]}),
            "retention_days": str(plan["retention_days"]), "result_class": plan["result_class"]}


def git_sha() -> str:
    r = git(Path.cwd(), "rev-parse", "HEAD")
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"


def write_plan_error(out: Path, source: str, request_path: str | None, path: str, problem: str,
                     hints: Sequence[str] = ()) -> int:
    """``out/plan-error.json``, ``error: <path>: <problem>`` as the first stderr line (then the hints) and one
    ``::error`` workflow command; returns the usage exit code. ``path`` must already be a safe path."""
    shown = f"{source} {path}" if source in ("manifest", "lock") else path
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "plan-error.json", {"schema_version": 1, "kind": "lab_plan_error", "source": source,
                                                "request": request_path, "path": path, "problem": problem})
    print(f"error: {shown}: {problem}", file=sys.stderr)
    for line in hints:
        print(gh_data(line), file=sys.stderr)
    file_property = f" file={gh_property(request_path)}" if request_path else ""
    print(f"::error{file_property}::{gh_data(chr(10).join([f'{shown}: {problem}', *hints]))}")
    return EXIT_USAGE


def _report(out: Path, err: LabError, request_path: str | None) -> int:
    if isinstance(err, ManifestError):
        source, path = err.source, safe_path(err.json_path)
    else:
        source = {RequestError: "request", DiscoveryError: "event"}.get(type(err), "plan")
        path = safe_path(err.path)
    hints = list(err.hints) if isinstance(err, DiscoveryError) else []
    return write_plan_error(out, source, request_path, path, err.problem, hints)


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
        request = load_request(path, manifest, strict_location=strict,
                               openfda_key=os.environ.get("LAB_HAS_OPENFDA_KEY") == "true")
        plan = build_plan(request, manifest, git_sha(), has_hosted=os.environ.get(HAS_VAR) == "true")
    except LabError as err:
        return _report(out, err, request_path)
    out.mkdir(parents=True, exist_ok=True)
    plan_sha256 = write_json_atomic(out / "plan.json", plan)
    if args.github_output:
        write_github_output(args.github_output, plan_outputs(plan, plan_sha256))
    if plan["skipped"]:
        notice = f"{HOSTED_SECRETS_MISSING}; skipped: " + ", ".join(s["unit"] for s in plan["skipped"])
        print(f"::notice title={gh_property(HOSTED_NOTICE_TITLE)}::{gh_data(notice)}")
    print(f"plan: {len(plan['units'])} units in {len(plan['shards'])} shards ({plan['result_class']})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
