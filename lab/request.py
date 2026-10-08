"""A lab request: the one file a founder writes to ask for a run, validated strictly before anything runs.

``lab/requests/<name>.json``, where ``<name>`` matches ``[a-z0-9][a-z0-9-]{0,39}``; at most 64 KiB of strict JSON.
Every key below is required unless marked optional (an optional key's default is given as ``default X``); unknown keys
are refused at every level::

    {"schema_version": 1,
     "purpose": "<why this run, 1..500 characters>",
     "provider": "fake" | "<the manifest's server program>",
     "models": ["<manifest model key>", ...],          # 1..8 distinct
     "job_minutes": 45..330,                           # the time limit of each shard job
     "max_parallel": 1..16,                            # shard jobs that may run at once
     "retention_days": 1..90,                          # how long the result artifacts are kept
     "experiments": {                                  # at least one of e1, e2, e3, g0, sim, x1, openfda
         "e1": {...}, "e2": {...}, "e3": {...}, "g0": {...}, "sim": {...}, "x1": {...}, "openfda": {...}},
     "hosted": {"<hosted model key>": {"max_calls": 1..1000000}, ...}}   # optional, default {}; see below

``capacity`` below is ``job_minutes - SHARD_OVERHEAD_MINUTES``; a seed is an int in ``0..2147483647``. The blocks:

``e1``, extraction F1 of two or more models against labelled records (one unit per model and repeat)::

    {"models": [...],                     # default $.models; gguf, fake or hosted models; at least 2
     "minutes": 1..capacity,              # per repeat unit
     "labels": {"source": "generator" | "fixtures", "pack": "<built-in pack id>",
                "n": 40..2000, "seed": <seed>},   # n and seed for generator labels only, and then required
     "reference": "<one of the E1 models>", "margin_points": 1..20, "runs": 3..5, "seed": <seed>,
     "bootstrap_b": 1000..20000}          # default 10000

``e2``, pushdown verification against central reading on a planted synthetic world (one unit per model)::

    {"models": [...],                     # default $.models; gguf or fake models only
     "minutes": 1..capacity, "pack": "<built-in pack id>",
     "plant": "plant_<name>",             # a fixture of the pack: packs/data/<pack>/fixtures/<plant>.json
     "seeds": [<seed>, ...],              # 1..5 distinct
     "weeks": min_weeks..104,             # min_weeks = baseline_weeks + window_weeks of the pack
     "eval_from": low..weeks-1,           # low = window_weeks + min_history_weeks - 1
     "eval_to": eval_from..weeks-1,
     "grace_weeks": 0..8,                 # default 4
     "tie_salt": "[A-Za-z0-9._-]{1,64}",  # default "lab-e2"
     "detector_author": "<1..80 printable characters>",   # default "mycelic engineering"
     "top_n": 5..60,                      # candidates per seed
     "min_candidates": 1..top_n,          # default top_n
     "central": "self" | "<a hosted model of $.models>",   # default "self": the model under test itself; a hosted
                                                           # entry needs context_tokens (HOSTED_CENTRAL_CONTEXT)
     "bootstrap_b": 1000..20000,          # default 10000
     "bootstrap_seed": <seed>}            # default 1

``x1``, the model-free evaluation harness on a plant fixture (one unit, no model): ``e2``'s keys without
``models``, ``top_n``, ``min_candidates`` and ``central``, with 1..10 seeds and ``tie_salt`` default ``"lab-x1"``.

``e3`` and ``g0`` (one unit per model) and ``sim`` (one unit per model and seed)::

    "e3": {"models": [...],               # optional: a subset of $.models (default: all of them)
           "minutes": 1..capacity, "concurrency": [1..8, ...],   # 1..4 distinct levels, run in the given order
           "requests": 3..200, "warmup": 0..requests-1, "workloads": ["extraction" | "short", ...], "seed": <seed>}
    "g0": {"models": [...], "minutes": 1..capacity, "pack": "<built-in pack id>", "records": 50..2000,
           "seed": <seed>}
    "sim": {"models": [...],              # optional; gguf or fake models only
            "minutes": 1..capacity,       # per unit: one unit per model and seed
            "plant": "plant_smoke" | "sim_small",   # lab.sim.PLANTS
            "weeks": min_weeks..52, "top_n": 1..60,
            "seeds": [<seed>, ...]}       # 1..5 distinct; the plant must fit every seed's world

``openfda``, the public replay and the labelling sheets (one unit, no model; it reaches api.fda.gov)::

    {"minutes": 1..capacity, "pack": "<a built-in pack with an openFDA mapping>",
     "product_codes": ["ABC", ...],       # 3..5 distinct, three upper-case letters each
     "date_from": "YYYYMMDD", "date_to": "YYYYMMDD",   # calendar dates; the span must cover enough ISO weeks
     "max_records_per_code": 1..25000,
     "manufacturers": ["<name>", ...],    # 1..10 distinct exact spellings, each 1..120 printable characters
     "manufacturer_field": "device[].manufacturer_d_name", "partition_field": "<field path>",
     "recalling_firms": [...],            # default the manufacturers
     "lookback_weeks": 1..104, "post_weeks": 1..104,   # default 26 each
     "min_partition_coverage": 0.5,       # default 0.5; a number in (0, 1]
     "tie_salt": "...",                   # default "lab-replay"
     "saw_recall_outcomes": "yes" | "no", # always required: did the requester see recall outcomes first?
     "n1_sheet": {"n": 1..1000, "seed": <seed>},       # optional: the N1 labelling sheet
     "e1_sheet": {"n": 1..2000, "seed": <seed>}}       # optional: the openFDA E1 labelling sheet

The fetch budget is checked last: ``product_codes x 2 x ceil(max_records_per_code / 1000)`` requests
(:func:`openfda_requests`) may be at most :data:`OPENFDA_CAP` (100 without the ``MYCELIC_LAB_OPENFDA_API_KEY``
secret, 1000 with it; the plan job is told which by ``LAB_HAS_OPENFDA_KEY``).

A fake model needs provider ``fake`` and a gguf model needs the server provider; a hosted model goes with either.
``provider: "fake"`` makes the whole run a plumbing check: nothing it writes measures a model (its hosted units still
call the configured host, labelled plumbing). A hosted model runs only as an ``e1`` model or as ``e2.central``: one in
``e2.models``, ``sim``, ``e3`` or ``g0`` is refused (:data:`HOSTED_PLACE`), and any other model kind outside
:data:`SIM_KINDS` is refused for ``e1`` (where hosted is allowed), ``e2`` and ``sim``; each at
``$.experiments.<block>.models[i]``, or at ``$.models[j]`` when the block names none.

``hosted`` is checked after every experiment block: it is required exactly when ``e1`` or ``e2.central`` uses a hosted
model and then names exactly those models (unknown keys first, in sorted order, then each used key in sorted order),
each with ``{"max_calls": 1..1000000}``; it is refused when no hosted model is used, unless empty. Last, each used key's
``max_calls`` must be at least the most model calls the request can make on the host (``lab.hosted``'s constants)::

    bound = [K in e1.models] x (PREFLIGHT_MAX_CALLS + e1.runs x records x 2)
          + [e2.central == K] x len(e2.models) x (2 x PREFLIGHT_MAX_CALLS + 2 x top_n x len(seeds) x 2)

where ``records`` is ``labels.n`` for generator labels, else the pack's fixture records (:func:`e1_records`): a
preflight of up to ``PREFLIGHT_MAX_CALLS`` calls per canary task and shard, then one call and one repair per E1 record
and per E2 central call. The bound does not depend on whether the secrets are present.
After the keys, ``sim`` checks models, minutes, plant, weeks, top_n, seeds, then for each seed in order whether the
plant fits that world under ``lab.sim.world_settings`` (``the plant does not fit these weeks (<the plant check's
problem>)`` at ``$.experiments.sim.weeks``); every other block checks its values in the order listed above.

Every string leaf, wherever it sits (an int position included), is checked in this order and the first failure
wins: string type, a ``<...>`` placeholder, a control or format character (Unicode categories Cc, Cf, Cs, Co, Cn,
Zl, Zp), a leading ``-``, the length cap, then the field's pattern or choices. Objects report unknown keys first (in
sorted order; a key is named only when the resulting JSON path is safe), then missing required keys in the order
above, then each value in that order. No message ever holds the offending value; every error is a
:class:`RequestError` whose path is a JSON path, ``$``, or ``request`` for a file problem.

The loaded :class:`Request` holds the normalised data (every block key present, defaults filled in) and the sha256 of
the file bytes.
"""
from __future__ import annotations

import functools
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from mycelic.collective.experiments.e3_latency import WORKLOADS
from mycelic.collective.experiments.openfda_replay import OPENFDA_MAPPING, replay_weeks
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load
from mycelic.collective.packs import loader

from . import LabError, check_keys, display_path, safe_path
from .goldlabels import build_labels
from .hosted import PREFLIGHT_MAX_CALLS, preflight_tasks, unit_bound
from .manifest import Manifest
from .notes import PLACEHOLDER
from .sim import MAX_SEEDS, MAX_TOP_N, MAX_WEEKS, PLANTS, min_weeks, plant_problem

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}", re.ASCII)
PLACEHOLDER_RE = re.compile(r"<[^<>]{0,200}>")
MAX_BYTES = 65536
SHARD_OVERHEAD_MINUTES = 25
SEED_MAX = 2147483647
BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
EXPERIMENTS = ("e1", "e2", "e3", "g0", "sim", "x1", "openfda")
SIM_KINDS = ("gguf", "fake")
MAX_MODELS = 8
LABEL_SOURCES = ("fixtures", "generator")
PLANT_RE = re.compile(r"plant_[a-z0-9_]{1,40}", re.ASCII)
TIE_SALT_RE = re.compile(r"[A-Za-z0-9._-]{1,64}", re.ASCII)
CODE_RE = re.compile(r"[A-Z]{3}", re.ASCII)
DATE_RE = re.compile(r"[0-9]{8}", re.ASCII)
MAX_E2_WEEKS = 104
MAX_GRACE_WEEKS = 8
MAX_AUTHOR = 80
MAX_NAME = 120
MAX_FIELD_PATH = 200
MAX_RECORDS_PER_CODE = 25000
OPENFDA_PAGE = 1000
OPENFDA_CAP = {False: 100, True: 1000}
DEFAULT_BOOTSTRAP_B = 10000
DEFAULT_AUTHOR = "mycelic engineering"
DEFAULT_TIE_SALTS = {"e2": "lab-e2", "x1": "lab-x1", "openfda": "lab-replay"}

_TOP_KEYS = ("schema_version", "purpose", "provider", "models", "job_minutes", "max_parallel", "retention_days",
             "experiments")
_E1_KEYS = ("models", "minutes", "labels", "reference", "margin_points", "runs", "seed", "bootstrap_b")
_E1_REQUIRED = ("minutes", "labels", "reference", "margin_points", "runs", "seed")
_LABELS_KEYS = ("source", "pack", "n", "seed")
_E2_KEYS = ("models", "minutes", "pack", "plant", "seeds", "weeks", "eval_from", "eval_to", "grace_weeks", "tie_salt",
            "detector_author", "top_n", "min_candidates", "central", "bootstrap_b", "bootstrap_seed")
_E2_REQUIRED = ("minutes", "pack", "plant", "seeds", "weeks", "eval_from", "eval_to", "top_n")
_X1_KEYS = ("minutes", "pack", "plant", "seeds", "weeks", "eval_from", "eval_to", "grace_weeks", "tie_salt",
            "detector_author", "bootstrap_b", "bootstrap_seed")
_X1_REQUIRED = ("minutes", "pack", "plant", "seeds", "weeks", "eval_from", "eval_to")
_OPENFDA_KEYS = ("minutes", "pack", "product_codes", "date_from", "date_to", "max_records_per_code", "manufacturers",
                 "manufacturer_field", "partition_field", "recalling_firms", "lookback_weeks", "post_weeks",
                 "min_partition_coverage", "tie_salt", "saw_recall_outcomes", "n1_sheet", "e1_sheet")
_OPENFDA_REQUIRED = ("minutes", "pack", "product_codes", "date_from", "date_to", "max_records_per_code",
                     "manufacturers", "manufacturer_field", "partition_field", "saw_recall_outcomes")
_SHEET_KEYS = ("n", "seed")
_E3_KEYS = ("models", "minutes", "concurrency", "requests", "warmup", "workloads", "seed")
_G0_KEYS = ("models", "minutes", "pack", "records", "seed")
_SIM_KEYS = ("models", "minutes", "plant", "weeks", "top_n", "seeds")
PACK_PROBLEM = "must be a built-in pack id"
PLANT_PROBLEM = "must name a plant fixture of the pack"
OPENFDA_PACK_PROBLEM = "must be a built-in pack with an openFDA mapping"
PRINTABLE_PROBLEM = "must be printable characters"
FIELD_PATH_PROBLEM = "must be a field path such as device[].manufacturer_d_name"
DATE_PROBLEM = "must be a calendar date YYYYMMDD"
LOCATION_PROBLEM = "must be lab/requests/<name>.json"
HOSTED_PLACE = ("a hosted model runs only in e1 or as e2.central: the simulation and the canary scan run each site's "
                "model inside the runner, and E3 measures this runner")
HOSTED_CENTRAL_CONTEXT = ("a hosted central comparator needs context_tokens in its manifest entry (the context one "
                          "request gets on the host)")
MAX_HOSTED_CALLS = 1000000


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


def _e3(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.e3"
    block = _object(raw, path)
    check_keys(block, _E3_KEYS, _E3_KEYS[1:], path, RequestError)
    out: dict[str, Any] = {"models": _no_hosted(block, path, models, manifest),
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


def _g0(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.g0"
    block = _object(raw, path)
    check_keys(block, _G0_KEYS, _G0_KEYS[1:], path, RequestError)
    out: dict[str, Any] = {"models": _no_hosted(block, path, models, manifest),
                           "minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    out["pack"] = _pack(block["pack"], f"{path}.pack")
    out["records"] = _int(block["records"], f"{path}.records", 50, 2000)
    out["seed"] = _int(block["seed"], f"{path}.seed", 0, SEED_MAX)
    return out


def _model_at(block: dict[str, Any], path: str, models: list[str], key: str, i: int) -> str:
    return f"{path}.models[{i}]" if "models" in block else f"$.models[{models.index(key)}]"


def _no_hosted(block: dict[str, Any], path: str, models: list[str], manifest: Manifest) -> list[str]:
    """The block's models (default: $.models), none of them hosted (:data:`HOSTED_PLACE`)."""
    chosen = _block_models(block, path, models)
    for i, key in enumerate(chosen):
        if manifest.models[key]["kind"] == "hosted":
            raise RequestError(_model_at(block, path, models, key, i), HOSTED_PLACE) from None
    return chosen


def _model_kinds(block: dict[str, Any], path: str, models: list[str], manifest: Manifest, problem: str,
                 allowed: tuple[str, ...] | None = None) -> list[str]:
    """The block's models (default: $.models), each of a kind in ``allowed`` (default :data:`SIM_KINDS`, read at call
    time); a hosted model where hosted is not allowed is :data:`HOSTED_PLACE`, any other kind ``problem``."""
    allowed = SIM_KINDS if allowed is None else allowed
    chosen = _block_models(block, path, models)
    for i, key in enumerate(chosen):
        kind = manifest.models[key]["kind"]
        if kind == "hosted" and kind not in allowed:
            raise RequestError(_model_at(block, path, models, key, i), HOSTED_PLACE) from None
        if kind not in allowed:
            raise RequestError(_model_at(block, path, models, key, i), problem) from None
    return chosen


def _pack(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise RequestError(path, "must be a string") from None
    _no_hazard(value, path)
    if not builtin_pack(value):
        raise RequestError(path, PACK_PROBLEM) from None
    return value


def _seed(value: Any, path: str) -> int:
    return _int(value, path, 0, SEED_MAX)


def _optional(block: dict[str, Any], key: str, default: Any, check: Any) -> Any:
    return check(block[key]) if key in block else default


def _sim(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.sim"
    block = _object(raw, path)
    check_keys(block, _SIM_KEYS, _SIM_KEYS[1:], path, RequestError)
    chosen = _model_kinds(block, path, models, manifest, "the simulation runs only gguf or fake models")
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


def _bootstrap_b(block: dict[str, Any], path: str) -> int:
    return _optional(block, "bootstrap_b", DEFAULT_BOOTSTRAP_B,
                     lambda v: _int(v, f"{path}.bootstrap_b", 1000, 20000))


def _labels(raw: Any, path: str) -> dict[str, Any]:
    block = _object(raw, path)
    check_keys(block, _LABELS_KEYS, ("source", "pack"), path, RequestError)
    source = _string(block["source"], f"{path}.source", 16)
    if source not in LABEL_SOURCES:
        raise RequestError(f"{path}.source", f"must be one of {', '.join(LABEL_SOURCES)}") from None
    out: dict[str, Any] = {"source": source, "pack": _pack(block["pack"], f"{path}.pack"), "n": None, "seed": None}
    if source == "generator":
        for key in _LABELS_KEYS[2:]:
            if key not in block:
                raise RequestError(f"{path}.{key}", "required") from None
        out["n"] = _int(block["n"], f"{path}.n", 40, 2000)
        out["seed"] = _seed(block["seed"], f"{path}.seed")
    else:
        for key in _LABELS_KEYS[2:]:
            if key in block:
                raise RequestError(f"{path}.{key}", "only for generator labels") from None
    return out


def _e1(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.e1"
    block = _object(raw, path)
    check_keys(block, _E1_KEYS, _E1_REQUIRED, path, RequestError)
    chosen = _model_kinds(block, path, models, manifest, "E1 runs only gguf, fake or hosted models",
                          allowed=(*SIM_KINDS, "hosted"))
    if len(chosen) < 2:
        raise RequestError(f"{path}.models" if "models" in block else path, "E1 needs at least 2 models") from None
    out: dict[str, Any] = {"models": chosen, "minutes": _minutes(block["minutes"], f"{path}.minutes", capacity),
                           "labels": _labels(block["labels"], f"{path}.labels")}
    reference = _string(block["reference"], f"{path}.reference", 24)
    if reference not in chosen:
        raise RequestError(f"{path}.reference", "must be one of the E1 models") from None
    out["reference"] = reference
    out["margin_points"] = _int(block["margin_points"], f"{path}.margin_points", 1, 20)
    out["runs"] = _int(block["runs"], f"{path}.runs", 3, 5)
    out["seed"] = _seed(block["seed"], f"{path}.seed")
    out["bootstrap_b"] = _bootstrap_b(block, path)
    return out


def _plant(value: Any, path: str, pack: str) -> str:
    name = _string(value, path, 64)
    fixture = loader.BUILTIN_ROOT / pack / "fixtures" / f"{name}.json"
    ok = PLANT_RE.fullmatch(name) is not None
    try:
        ok = ok and name + ".json" in os.listdir(fixture.parent) and stat.S_ISREG(os.lstat(fixture).st_mode)
    except OSError:
        ok = False
    if not ok:
        raise RequestError(path, PLANT_PROBLEM) from None
    return name


def _tie_salt(block: dict[str, Any], path: str, default: str) -> str:
    def check(value: Any) -> str:
        salt = _string(value, f"{path}.tie_salt", 64)
        if TIE_SALT_RE.fullmatch(salt) is None:
            raise RequestError(f"{path}.tie_salt", f"must match {TIE_SALT_RE.pattern}") from None
        return salt

    return _optional(block, "tie_salt", default, check)


def _printable(value: Any, path: str, cap: int) -> str:
    text = _string(value, path, cap)
    if not text.isprintable():
        raise RequestError(path, PRINTABLE_PROBLEM) from None
    return text


def _world(block: dict[str, Any], path: str, capacity: int, max_seeds: int, default_salt: str) -> dict[str, Any]:
    """The keys E2 and X1 share, checked in the order listed in the module docstring."""
    out: dict[str, Any] = {"minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    out["pack"] = _pack(block["pack"], f"{path}.pack")
    out["plant"] = _plant(block["plant"], f"{path}.plant", out["pack"])
    out["seeds"] = _distinct(_list(block["seeds"], f"{path}.seeds", 1, max_seeds, "seeds"), f"{path}.seeds", _seed)
    pack = loader.load_pack(out["pack"])
    out["weeks"] = _int(block["weeks"], f"{path}.weeks", min_weeks(pack), MAX_E2_WEEKS)
    low = pack.detectors["window_weeks"] + pack.detectors["min_history_weeks"] - 1
    out["eval_from"] = _int(block["eval_from"], f"{path}.eval_from", low, out["weeks"] - 1)
    out["eval_to"] = _int(block["eval_to"], f"{path}.eval_to", out["eval_from"], out["weeks"] - 1)
    out["grace_weeks"] = _optional(block, "grace_weeks", 4,
                                   lambda v: _int(v, f"{path}.grace_weeks", 0, MAX_GRACE_WEEKS))
    out["tie_salt"] = _tie_salt(block, path, default_salt)
    out["detector_author"] = _optional(block, "detector_author", DEFAULT_AUTHOR,
                                       lambda v: _printable(v, f"{path}.detector_author", MAX_AUTHOR))
    return out


def _e2(raw: Any, models: list[str], capacity: int, manifest: Manifest) -> dict[str, Any]:
    path = "$.experiments.e2"
    block = _object(raw, path)
    check_keys(block, _E2_KEYS, _E2_REQUIRED, path, RequestError)
    out: dict[str, Any] = {"models": _model_kinds(block, path, models, manifest, "E2 runs only gguf or fake models")}
    out.update(_world(block, path, capacity, MAX_SEEDS, DEFAULT_TIE_SALTS["e2"]))
    out["top_n"] = _int(block["top_n"], f"{path}.top_n", 5, MAX_TOP_N)
    out["min_candidates"] = _optional(block, "min_candidates", out["top_n"],
                                      lambda v: _int(v, f"{path}.min_candidates", 1, out["top_n"]))

    def central(value: Any) -> str:
        name = _string(value, f"{path}.central", 24)
        if name != "self" and (name not in models or manifest.models[name]["kind"] != "hosted"):
            raise RequestError(f"{path}.central", "must be self or a hosted model of $.models") from None
        if name != "self" and manifest.models[name]["context_tokens"] is None:
            raise RequestError(f"{path}.central", HOSTED_CENTRAL_CONTEXT) from None
        return name

    out["central"] = _optional(block, "central", "self", central)
    out["bootstrap_b"] = _bootstrap_b(block, path)
    out["bootstrap_seed"] = _optional(block, "bootstrap_seed", 1, lambda v: _seed(v, f"{path}.bootstrap_seed"))
    return out


def _x1(raw: Any, capacity: int) -> dict[str, Any]:
    path = "$.experiments.x1"
    block = _object(raw, path)
    check_keys(block, _X1_KEYS, _X1_REQUIRED, path, RequestError)
    out = _world(block, path, capacity, 10, DEFAULT_TIE_SALTS["x1"])
    out["bootstrap_b"] = _bootstrap_b(block, path)
    out["bootstrap_seed"] = _optional(block, "bootstrap_seed", 1, lambda v: _seed(v, f"{path}.bootstrap_seed"))
    return out


def e1_records(labels: dict[str, Any]) -> int:
    """The records one E1 repeat reads: ``n`` for generator labels, else the pack's fixture records."""
    if labels["source"] == "generator":
        return labels["n"]
    return build_labels("fixtures", labels["pack"])[1]["records"]


def hosted_bound(experiments: dict[str, Any], key: str) -> int:
    """The most model calls the normalised experiments can make on the host for ``key``; see the module docstring.
    ``lab.plan`` splits the same calls into per-shard shares."""
    bound = 0
    e1, e2 = experiments.get("e1"), experiments.get("e2")
    if e1 is not None and key in e1["models"]:
        bound += (PREFLIGHT_MAX_CALLS * preflight_tasks("e1")
                  + e1["runs"] * unit_bound("e1", records=e1_records(e1["labels"])))
    if e2 is not None and e2["central"] == key:
        bound += len(e2["models"]) * (PREFLIGHT_MAX_CALLS * preflight_tasks("e2")
                                      + unit_bound("e2", top_n=e2["top_n"], seeds=len(e2["seeds"])))
    return bound


def hosted_used(experiments: dict[str, Any], manifest: Manifest) -> list[str]:
    """The hosted models the normalised experiments use: E1 models of kind hosted and a hosted ``e2.central``."""
    used = {k for k in experiments.get("e1", {}).get("models", []) if manifest.models[k]["kind"] == "hosted"}
    central = experiments.get("e2", {}).get("central", "self")
    if central != "self":
        used.add(central)
    return sorted(used)


def _hosted(top: dict[str, Any], experiments: dict[str, Any], manifest: Manifest) -> dict[str, Any]:
    """The ``hosted`` block, checked after every experiment block; the bound is checked last."""
    path = "$.hosted"
    used = hosted_used(experiments, manifest)
    if "hosted" not in top:
        if used:
            raise RequestError(path, "required: the hosted models e1 or e2.central uses need max_calls") from None
        return {}
    block = _object(top["hosted"], path)
    if not used:
        if block:
            raise RequestError(path, "only for the hosted models e1 or e2.central uses") from None
        return {}
    check_keys(block, used, used, path, RequestError, unknown="not a hosted model e1 or e2.central uses")
    out: dict[str, Any] = {}
    for key in used:
        entry = _object(block[key], f"{path}.{key}")
        check_keys(entry, ("max_calls",), ("max_calls",), f"{path}.{key}", RequestError)
        out[key] = {"max_calls": _int(entry["max_calls"], f"{path}.{key}.max_calls", 1, MAX_HOSTED_CALLS)}
    for key in used:
        bound = hosted_bound(experiments, key)
        if out[key]["max_calls"] < bound:
            raise RequestError(f"{path}.{key}.max_calls", (
                f"must be at least {bound}, the most model calls this request can make on the host (a preflight of up "
                f"to {PREFLIGHT_MAX_CALLS} calls per task and shard, and one repair per call); lower e1.runs, "
                "e1.labels.n, e2.top_n or e2.seeds, or raise max_calls")) from None
    return out


def openfda_requests(codes: list[str], max_records: int) -> int:
    """The openFDA requests a fetch of both datasets may make: per code and dataset, one per page of 1000 records."""
    return len(codes) * 2 * math.ceil(max_records / OPENFDA_PAGE)


def _compact_date(value: Any, path: str) -> date:
    text = _string(value, path, 32)
    day = None
    if DATE_RE.fullmatch(text) is not None:
        try:
            day = date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            day = None
    if day is None:
        raise RequestError(path, DATE_PROBLEM) from None
    return day


def _names(value: Any, path: str) -> list[str]:
    _no_hazard(value, path)
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise RequestError(path, "must be a list of 1 to 10 names") from None
    return _distinct(value, path, lambda v, p: _printable(v, p, MAX_NAME))


def _field_path(value: Any, path: str) -> str:
    text = _string(value, path, MAX_FIELD_PATH)
    if loader.PATH_RE.fullmatch(text) is None:
        raise RequestError(path, FIELD_PATH_PROBLEM) from None
    return text


def _sheet(value: Any, path: str, high: int) -> dict[str, int]:
    block = _object(value, path)
    check_keys(block, _SHEET_KEYS, _SHEET_KEYS, path, RequestError)
    return {"n": _int(block["n"], f"{path}.n", 1, high), "seed": _seed(block["seed"], f"{path}.seed")}


def _coverage(value: Any, path: str) -> int | float:
    _no_hazard(value, path)
    ok = (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
          and 0 < value <= 1)
    if not ok:
        raise RequestError(path, "must be a number in (0, 1]") from None
    return value


def _openfda(raw: Any, capacity: int, openfda_key: bool) -> dict[str, Any]:
    path = "$.experiments.openfda"
    block = _object(raw, path)
    check_keys(block, _OPENFDA_KEYS, _OPENFDA_REQUIRED, path, RequestError)
    out: dict[str, Any] = {"minutes": _minutes(block["minutes"], f"{path}.minutes", capacity)}
    pack_id = block["pack"]
    if not isinstance(pack_id, str):
        raise RequestError(f"{path}.pack", "must be a string") from None
    _no_hazard(pack_id, f"{path}.pack")
    if not builtin_pack(pack_id) or OPENFDA_MAPPING not in loader.load_pack(pack_id).mappings:
        raise RequestError(f"{path}.pack", OPENFDA_PACK_PROBLEM) from None
    out["pack"] = pack_id
    codes = block["product_codes"]
    _no_hazard(codes, f"{path}.product_codes")
    if not isinstance(codes, list) or not 3 <= len(codes) <= 5:
        raise RequestError(f"{path}.product_codes", "must be a list of 3 to 5 product codes") from None

    def code(value: Any, p: str) -> str:
        text = _string(value, p, 16)
        if CODE_RE.fullmatch(text) is None:
            raise RequestError(p, "must be three upper-case letters") from None
        return text

    out["product_codes"] = _distinct(codes, f"{path}.product_codes", code)
    first = _compact_date(block["date_from"], f"{path}.date_from")
    last = _compact_date(block["date_to"], f"{path}.date_to")
    if last <= first:
        raise RequestError(f"{path}.date_to", "must be after date_from") from None
    pack = loader.load_pack(pack_id)
    need = pack.detectors["window_weeks"] + pack.detectors["min_history_weeks"] + 1
    if len(replay_weeks(first.isoformat(), last.isoformat())) < need:
        raise RequestError(f"{path}.date_to", f"the date range must span at least {need} ISO weeks") from None
    out["date_from"], out["date_to"] = block["date_from"], block["date_to"]
    out["max_records_per_code"] = _int(block["max_records_per_code"], f"{path}.max_records_per_code", 1,
                                       MAX_RECORDS_PER_CODE)
    out["manufacturers"] = _names(block["manufacturers"], f"{path}.manufacturers")
    out["manufacturer_field"] = _field_path(block["manufacturer_field"], f"{path}.manufacturer_field")
    out["partition_field"] = _field_path(block["partition_field"], f"{path}.partition_field")
    out["recalling_firms"] = _optional(block, "recalling_firms", list(out["manufacturers"]),
                                       lambda v: _names(v, f"{path}.recalling_firms"))
    for key in ("lookback_weeks", "post_weeks"):
        out[key] = _optional(block, key, 26, lambda v, key=key: _int(v, f"{path}.{key}", 1, 104))
    out["min_partition_coverage"] = _optional(block, "min_partition_coverage", 0.5,
                                              lambda v: _coverage(v, f"{path}.min_partition_coverage"))
    out["tie_salt"] = _tie_salt(block, path, DEFAULT_TIE_SALTS["openfda"])
    saw = _string(block["saw_recall_outcomes"], f"{path}.saw_recall_outcomes", 16)
    if saw not in ("yes", "no"):
        raise RequestError(f"{path}.saw_recall_outcomes", "must be yes or no") from None
    out["saw_recall_outcomes"] = saw
    out["n1_sheet"] = _optional(block, "n1_sheet", None, lambda v: _sheet(v, f"{path}.n1_sheet", 1000))
    out["e1_sheet"] = _optional(block, "e1_sheet", None, lambda v: _sheet(v, f"{path}.e1_sheet", 2000))
    problem = openfda_budget_problem(openfda_requests(out["product_codes"], out["max_records_per_code"]), openfda_key)
    if problem is not None:
        raise RequestError(f"{path}.max_records_per_code", problem) from None
    return out


def openfda_budget_problem(estimate: int, key: bool) -> str | None:
    """The problem when ``estimate`` openFDA requests exceed :data:`OPENFDA_CAP` for the key state, else None."""
    cap = OPENFDA_CAP[key]
    if estimate <= cap:
        return None
    remedy = ("allowed with an API key; lower max_records_per_code or product_codes" if key else
              "allowed without an API key; add the MYCELIC_LAB_OPENFDA_API_KEY repository secret, or lower "
              "max_records_per_code or product_codes")
    return f"the fetch would make about {estimate} openFDA requests, more than the {cap} {remedy}"


def validate(obj: Any, manifest: Manifest, *, openfda_key: bool = False) -> dict[str, Any]:
    """The normalised request, or :class:`RequestError` for the first problem in the documented order."""
    top = _object(obj, "$")
    check_keys(top, (*_TOP_KEYS, "hosted"), _TOP_KEYS, "$", RequestError)
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
    checks = {"e1": lambda raw: _e1(raw, models, capacity, manifest),
              "e2": lambda raw: _e2(raw, models, capacity, manifest),
              "e3": lambda raw: _e3(raw, models, capacity, manifest),
              "g0": lambda raw: _g0(raw, models, capacity, manifest),
              "sim": lambda raw: _sim(raw, models, capacity, manifest), "x1": lambda raw: _x1(raw, capacity),
              "openfda": lambda raw: _openfda(raw, capacity, openfda_key)}
    out["experiments"] = {name: checks[name](experiments[name]) for name in EXPERIMENTS if name in experiments}
    out["hosted"] = _hosted(top, out["experiments"], manifest)
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
                 root: str | os.PathLike[str] | None = None, openfda_key: bool = False) -> Request:
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
    return Request(path=shown, name=absolute.stem, sha256=sha256_hex(data),
                   data=validate(obj, manifest, openfda_key=openfda_key))
