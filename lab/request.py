"""A lab request: the one file a founder writes to ask for a run, validated strictly before anything runs.

``lab/requests/<name>.json``, where ``<name>`` matches ``[a-z0-9][a-z0-9-]{0,39}``; at most 64 KiB of strict JSON.
Every key below is required unless marked optional; unknown keys are refused at every level::

    {"schema_version": 1,
     "purpose": "<why this run, 1..500 characters>",
     "provider": "fake" | "<the manifest's server program>",
     "models": ["<manifest model key>", ...],          # 1..8 distinct
     "job_minutes": 45..330,                           # the time limit of each shard job
     "max_parallel": 1..16,                            # shard jobs that may run at once
     "retention_days": 1..90,                          # how long the result artifacts are kept
     "experiments": {                                  # at least one of e3, g0, sim
         "e3": {"models": [...],                       # optional: a subset of $.models (default: all of them)
                "minutes": 1..capacity,                # capacity = job_minutes - SHARD_OVERHEAD_MINUTES
                "concurrency": [1..8, ...],            # 1..4 distinct levels, run in the given order
                "requests": 3..200, "warmup": 0..requests-1,
                "workloads": ["extraction" | "short", ...], "seed": 0..2147483647},
         "g0": {"models": [...], "minutes": 1..capacity, "pack": "<built-in pack id>", "records": 50..2000,
                "seed": 0..2147483647},
         "sim": {"models": [...],                      # optional; gguf or fake models only (SIM_KINDS)
                 "minutes": 1..capacity,               # per unit: one unit per model and seed
                 "plant": "plant_smoke" | "sim_small", # lab.sim.PLANTS
                 "weeks": min_weeks..52,               # baseline_weeks + window_weeks of the plant's pack (34)
                 "top_n": 1..60,                       # candidates verified by pushdown
                 "seeds": [0..2147483647, ...]}}}      # 1..5 distinct; the plant must fit every seed's world

A fake model needs provider ``fake`` and a gguf model needs the server provider. ``provider: "fake"`` makes the whole
run a plumbing check: nothing it writes measures a model. The sim block is checked in this order: keys, models (a
kind outside :data:`SIM_KINDS` is refused at ``$.experiments.sim.models[i]``, or at ``$.models[j]`` when the block
names none), minutes, plant, weeks, top_n, seeds, then for each seed in order whether the plant fits that world under
``lab.sim.world_settings`` (``the plant does not fit these weeks (<the plant check's problem>)`` at
``$.experiments.sim.weeks``).

Every string leaf, wherever it sits (an int position included), is checked in this order and the first failure
wins: string type, a ``<...>`` placeholder, a control or format character (Unicode categories Cc, Cf, Cs, Co, Cn,
Zl, Zp), a leading ``-``, the length cap, then the field's pattern or choices. Objects report unknown keys first (in
sorted order; a key is named only when the resulting JSON path is safe), then missing required keys in the order
above, then each value in that order. No message ever holds the offending value; every error is a
:class:`RequestError` whose path is a JSON path, ``$``, or ``request`` for a file problem.

The loaded :class:`Request` holds the normalised data (block ``models`` filled in) and the sha256 of the file bytes.
"""
from __future__ import annotations

import functools
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader

from . import LabError, check_keys, display_path, safe_path
from .manifest import Manifest
from .notes import PLACEHOLDER
from .sim import MAX_SEEDS, MAX_TOP_N, MAX_WEEKS, PLANTS, min_weeks, plant_problem

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}", re.ASCII)
PLACEHOLDER_RE = re.compile(r"<[^<>]{0,200}>")
MAX_BYTES = 65536
SHARD_OVERHEAD_MINUTES = 25
SEED_MAX = 2147483647
BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
EXPERIMENTS = ("e3", "g0", "sim")
SIM_KINDS = ("gguf", "fake")
MAX_MODELS = 8

_TOP_KEYS = ("schema_version", "purpose", "provider", "models", "job_minutes", "max_parallel", "retention_days",
             "experiments")
_E3_KEYS = ("models", "minutes", "concurrency", "requests", "warmup", "workloads", "seed")
_G0_KEYS = ("models", "minutes", "pack", "records", "seed")
_SIM_KEYS = ("models", "minutes", "plant", "weeks", "top_n", "seeds")
PACK_PROBLEM = "must be a built-in pack id"
LOCATION_PROBLEM = "must be lab/requests/<name>.json"


class RequestError(LabError):
    pass


@dataclass(frozen=True)
class Request:
    path: str
    name: str
    sha256: str
    data: dict[str, Any]


# --------------------------------------------------------------------------------------------------- leaves

def _hazard(value: str) -> str | None:
    if PLACEHOLDER_RE.search(value) is not None:
        return PLACEHOLDER
    if any(unicodedata.category(c) in BAD_CATEGORIES for c in value):
        return "control or format character"
    if value.startswith("-"):
        return "must not start with '-'"
    return None


def _no_hazard(value: Any, path: str) -> None:
    """Rules 2-4 for a string found anywhere, so a placeholder in an int or list position says so."""
    if isinstance(value, str):
        problem = _hazard(value)
        if problem is not None:
            raise RequestError(path, problem) from None


def _string(value: Any, path: str, cap: int) -> str:
    if not isinstance(value, str):
        raise RequestError(path, "must be a string") from None
    _no_hazard(value, path)
    if not 1 <= len(value) <= cap:
        raise RequestError(path, f"must be 1 to {cap} characters") from None
    return value


def _int(value: Any, path: str, low: int, high: int, problem: str | None = None) -> int:
    _no_hazard(value, path)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise RequestError(path, problem or f"must be an int in [{low}, {high}]") from None
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    _no_hazard(value, path)
    if not isinstance(value, dict):
        raise RequestError(path, "must be an object") from None
    return value


def _list(value: Any, path: str, low: int, high: int, what: str) -> list[Any]:
    _no_hazard(value, path)
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise RequestError(path, f"must be a list of {low} to {high} {what}") from None
    return value


def _distinct(items: list[Any], path: str, check: Any) -> list[Any]:
    seen: list[Any] = []
    for i, item in enumerate(items):
        value = check(item, f"{path}[{i}]")
        if value in seen:
            raise RequestError(f"{path}[{i}]", "duplicate") from None
        seen.append(value)
    return seen


def _key_list(value: Any, path: str, allowed: list[str], problem: str, high: int) -> list[str]:
    def check(item: Any, p: str) -> str:
        key = _string(item, p, 24)
        if key not in allowed:
            raise RequestError(p, problem) from None
        return key

    return _distinct(_list(value, path, 1, high, "model keys"), path, check)


@functools.lru_cache(maxsize=None)
def builtin_pack(ref: str) -> bool:
    """Whether ``ref`` names a directory directly under the built-in pack root that loads as a pack."""
    if loader.ID_RE.fullmatch(ref) is None:
        return False
    try:
        listed = ref in os.listdir(loader.BUILTIN_ROOT)
        directory = loader.BUILTIN_ROOT / ref
        listed = listed and not directory.is_symlink() and directory.is_dir()
    except OSError:
        return False
    if not listed:
        return False
    failed = False
    try:
        loader.load_pack(ref)
    except (loader.PackError, OSError, ValueError):
        failed = True
    return not failed


# --------------------------------------------------------------------------------------------------- blocks

def _block_models(block: dict[str, Any], path: str, models: list[str]) -> list[str]:
    if "models" not in block:
        return list(models)
    return _key_list(block["models"], f"{path}.models", models, "not one of $.models", MAX_MODELS)


def _minutes(value: Any, path: str, capacity: int) -> int:
    _no_hazard(value, path)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RequestError(path, "must be an int >= 1") from None
    if value > capacity:
        raise RequestError(path, "exceeds the shard capacity (job_minutes minus the shard overhead)") from None
    return value


def _e3(raw: Any, models: list[str], capacity: int) -> dict[str, Any]:
    path = "$.experiments.e3"
    block = _object(raw, path)
    check_keys(block, _E3_KEYS, _E3_KEYS[1:], path, RequestError)
    out: dict[str, Any] = {"models": _block_models(block, path, models),
                           "minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    out["concurrency"] = _distinct(_list(block["concurrency"], f"{path}.concurrency", 1, 4, "ints"),
                                   f"{path}.concurrency", lambda v, p: _int(v, p, 1, 8))
    out["requests"] = _int(block["requests"], f"{path}.requests", 3, 200)
    out["warmup"] = _int(block["warmup"], f"{path}.warmup", 0, out["requests"] - 1,
                         "must be an int in [0, requests - 1]")

    def workload(v: Any, p: str) -> str:
        name = _string(v, p, 32)
        if name not in WORKLOADS:
            raise RequestError(p, f"must be one of {', '.join(WORKLOADS)}") from None
        return name

    out["workloads"] = _distinct(_list(block["workloads"], f"{path}.workloads", 1, len(WORKLOADS), "workloads"),
                                 f"{path}.workloads", workload)
    out["seed"] = _int(block["seed"], f"{path}.seed", 0, SEED_MAX)
    return out


def _g0(raw: Any, models: list[str], capacity: int) -> dict[str, Any]:
    path = "$.experiments.g0"
    block = _object(raw, path)
    check_keys(block, _G0_KEYS, _G0_KEYS[1:], path, RequestError)
    out: dict[str, Any] = {"models": _block_models(block, path, models),
                           "minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    pack = block["pack"]
    if not isinstance(pack, str):
        raise RequestError(f"{path}.pack", "must be a string") from None
    _no_hazard(pack, f"{path}.pack")
    if not builtin_pack(pack):
        raise RequestError(f"{path}.pack", PACK_PROBLEM) from None
    out["pack"] = pack
    out["records"] = _int(block["records"], f"{path}.records", 50, 2000)
    out["seed"] = _int(block["seed"], f"{path}.seed", 0, SEED_MAX)
    return out


def _sim(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.sim"
    block = _object(raw, path)
    check_keys(block, _SIM_KEYS, _SIM_KEYS[1:], path, RequestError)
    chosen = _block_models(block, path, models)
    for i, key in enumerate(chosen):
        if manifest.models[key]["kind"] not in SIM_KINDS:
            at = f"{path}.models[{i}]" if "models" in block else f"$.models[{models.index(key)}]"
            raise RequestError(at, "the simulation runs only gguf or fake models") from None
    out: dict[str, Any] = {"models": chosen, "minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    plant = block["plant"]
    if not isinstance(plant, str):
        raise RequestError(f"{path}.plant", "must be a string") from None
    _no_hazard(plant, f"{path}.plant")
    if plant not in PLANTS:
        raise RequestError(f"{path}.plant", f"must be one of {', '.join(sorted(PLANTS))}") from None
    out["plant"] = plant
    out["weeks"] = _int(block["weeks"], f"{path}.weeks", min_weeks(loader.load_pack(PLANTS[plant].pack)), MAX_WEEKS)
    out["top_n"] = _int(block["top_n"], f"{path}.top_n", 1, MAX_TOP_N)
    out["seeds"] = _distinct(_list(block["seeds"], f"{path}.seeds", 1, MAX_SEEDS, "seeds"), f"{path}.seeds",
                             lambda v, p: _int(v, p, 0, SEED_MAX))
    for seed in out["seeds"]:
        problem = plant_problem(plant, seed, out["weeks"])
        if problem is not None:
            raise RequestError(f"{path}.weeks", f"the plant does not fit these weeks ({problem})") from None
    return out


def validate(obj: Any, manifest: Manifest) -> dict[str, Any]:
    """The normalised request, or :class:`RequestError` for the first problem in the documented order."""
    top = _object(obj, "$")
    check_keys(top, _TOP_KEYS, _TOP_KEYS, "$", RequestError)
    _int(top["schema_version"], "$.schema_version", 1, 1, "must be 1")
    out: dict[str, Any] = {"schema_version": 1, "purpose": _string(top["purpose"], "$.purpose", 500)}
    provider = _string(top["provider"], "$.provider", 32)
    if provider not in manifest.providers():
        raise RequestError("$.provider", "must be fake or the manifest's server program") from None
    out["provider"] = provider
    models = _key_list(top["models"], "$.models", sorted(manifest.models), "not a model in the manifest",
                       MAX_MODELS)
    for i, key in enumerate(models):
        kind = manifest.models[key]["kind"]
        if kind == "fake" and provider != "fake":
            raise RequestError(f"$.models[{i}]", "kind fake needs provider fake") from None
        if kind == "gguf" and provider == "fake":
            raise RequestError(f"$.models[{i}]", "kind gguf needs the server provider") from None
    out["models"] = models
    out["job_minutes"] = _int(top["job_minutes"], "$.job_minutes", 45, 330)
    out["max_parallel"] = _int(top["max_parallel"], "$.max_parallel", 1, 16)
    out["retention_days"] = _int(top["retention_days"], "$.retention_days", 1, 90)
    experiments = _object(top["experiments"], "$.experiments")
    check_keys(experiments, EXPERIMENTS, (), "$.experiments", RequestError, unknown="unknown experiment")
    if not experiments:
        raise RequestError("$.experiments", f"needs at least one of {', '.join(EXPERIMENTS)}") from None
    capacity = out["job_minutes"] - SHARD_OVERHEAD_MINUTES
    blocks: dict[str, Any] = {}
    if "e3" in experiments:
        blocks["e3"] = _e3(experiments["e3"], models, capacity)
    if "g0" in experiments:
        blocks["g0"] = _g0(experiments["g0"], models, capacity)
    if "sim" in experiments:
        blocks["sim"] = _sim(experiments["sim"], models, capacity, manifest)
    out["experiments"] = blocks
    return out


# --------------------------------------------------------------------------------------------------- the file

def _read(absolute: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(absolute, flags)
    except OSError:
        raise RequestError("request", "cannot be read") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RequestError("request", "not a regular file") from None
        chunks, size = [], 0
        while size <= MAX_BYTES:
            chunk = os.read(fd, MAX_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def load_request(path: str | os.PathLike[str], manifest: Manifest, *, strict_location: bool = False,
                 root: str | os.PathLike[str] | None = None) -> Request:
    root_dir = Path(os.path.abspath(root if root is not None else os.getcwd()))
    absolute = Path(os.path.abspath(path))
    shown = display_path(absolute, root_dir)
    try:
        mode = os.lstat(absolute).st_mode
    except FileNotFoundError:
        raise RequestError("request", "not found") from None
    except OSError:
        raise RequestError("request", "cannot be read") from None
    if stat.S_ISLNK(mode):
        raise RequestError("request", "is a symlink") from None
    if not stat.S_ISREG(mode):
        raise RequestError("request", "not a regular file") from None
    if absolute.suffix != ".json" or NAME_RE.fullmatch(absolute.stem) is None:
        raise RequestError("request", "bad request name") from None
    if strict_location:
        requests_dir = root_dir / "lab" / "requests"
        ok = shown == f"lab/requests/{absolute.stem}.json"
        try:
            ok = ok and not stat.S_ISLNK(os.lstat(requests_dir).st_mode)
            ok = ok and absolute.parent.resolve() == requests_dir.resolve()
        except OSError:
            ok = False
        if not ok:
            raise RequestError("request", LOCATION_PROBLEM) from None
    data = _read(absolute)
    if len(data) > MAX_BYTES:
        raise RequestError("$", "larger than 64 KiB") from None
    try:
        obj = strict_load(data)
    except StrictJsonError as err:
        raise RequestError(safe_path(err.path), f"invalid JSON ({err.reason})") from None
    return Request(path=shown, name=absolute.stem, sha256=sha256_hex(data), data=validate(obj, manifest))
