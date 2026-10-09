"""Lab requests and the model manifest: strict validation, fixed messages, and nothing echoed.

The malformed table runs every case through ``lab.plan.main`` in-process (three also as real subprocesses): exit 2,
the first stderr line ``error: <path>: <problem>``, a matching ``plan-error.json``, an ``::error`` workflow command and
no ``plan.json``. Each case carries a unique sentinel in the offending value (or, when the offence leaves no room for
one, in a later value), and the sentinel must appear in none of the outputs.
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from lab import ROOT, check_keys, gh_data, gh_property, safe_path
from lab.manifest import (ARGS_CROSS, ARGS_PROBLEM, ARGS_REPEAT, CONTEXT_TOKENS_RANGE, REPIN, REVISION_PROBLEM,
                          ManifestError, load_manifest, parse_server_args)
from lab.notes import PLACEHOLDER
from lab.request import HOSTED_CENTRAL_CONTEXT, PACK_PROBLEM, RequestError, load_request, validate
from tests.lab.helpers import MANIFEST_TEST, PLUMBING_MIN, lab_cli, plumbing_min, run_plan, sim_block, write_json

MANIFEST = load_manifest(MANIFEST_TEST)


def _sentinel(i: int) -> str:
    return f"Qz{i:02d}SnTl"


def _set(obj: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    node = obj
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return obj


def _delete(obj: dict[str, Any], dotted: str) -> dict[str, Any]:
    node = obj
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]
    return obj


def _with(*edits: tuple[str, Any], sentinel_at: str | None = "experiments.g0.pack") -> Callable[[str], bytes]:
    """A case body: plumbing-min with ``edits`` applied; ``<S>`` in any string is replaced by the sentinel; the
    sentinel is also put at ``sentinel_at`` (a later value) unless that is None."""

    def build(s: str) -> bytes:
        obj = plumbing_min()
        if sentinel_at is not None:
            _set(obj, sentinel_at, s)
        for dotted, value in edits:
            dotted = dotted.replace("<S>", s)
            if value is _DELETE:
                _delete(obj, dotted)
            else:
                _set(obj, dotted, _subst(value, s))
        return json.dumps(obj).encode("utf-8")

    return build


def _raw(template: str) -> Callable[[str], bytes]:
    return lambda s: template.replace("<S>", s).encode("utf-8")


def _subst(value: Any, s: str) -> Any:
    if isinstance(value, str):
        return value.replace("<S>", s)
    if isinstance(value, list):
        return [_subst(v, s) for v in value]
    if isinstance(value, dict):
        return {_subst(k, s): _subst(v, s) for k, v in value.items()}
    return value


class _Delete:
    pass


_DELETE = _Delete()
_VALID_TEXT = json.dumps(plumbing_min())
E1 = {"minutes": 5, "labels": {"source": "generator", "pack": "device_quality", "n": 40, "seed": 1},
      "reference": "fake-b", "margin_points": 5, "runs": 3, "seed": 1}
E2 = {"minutes": 5, "pack": "device_quality", "plant": "plant_e2_smoke", "seeds": [1], "weeks": 52, "eval_from": 20,
      "eval_to": 51, "top_n": 5}
X1 = {"minutes": 5, "pack": "device_quality", "plant": "plant_smoke", "seeds": [1], "weeks": 52, "eval_from": 26,
      "eval_to": 51}
OPENFDA = {"minutes": 5, "pack": "device_quality", "product_codes": ["AAA", "BBB", "CCC"], "date_from": "20240101",
           "date_to": "20241229", "max_records_per_code": 2000, "manufacturers": ["ACME Devices"],
           "manufacturer_field": "device[].manufacturer_d_name", "partition_field": "event_location",
           "saw_recall_outcomes": "no"}


def _block(name: str, base: dict[str, Any], **changes: Any) -> Callable[[str], bytes]:
    """plumbing-min plus the block ``name`` (``base`` with ``changes``; a value ``_DELETE`` drops the key), the sentinel
    in the purpose (checked before every block), and ``<S>`` in any value replaced by it."""
    block = copy.deepcopy(base)
    for key, value in changes.items():
        if value is _DELETE:
            block.pop(key, None)
        else:
            block[key] = value
    return _with((f"experiments.{name}", block), sentinel_at="purpose")


def _e1_labels(**changes: Any) -> dict[str, Any]:
    return {**E1["labels"], **changes}

# (label, body builder, expected path, expected problem prefix)
CASES: list[tuple[str, Callable[[str], bytes], str, str]] = [
    ("top-list", lambda s: json.dumps([s]).encode(), "$", "must be an object"),
    ("top-string", lambda s: json.dumps(s).encode(), "$", "must be an object"),
    ("unknown-unsafe-key", lambda s: json.dumps({**plumbing_min(), s: 1}).encode(), "$",
     "unknown key (name not shown)"),
    ("unknown-safe-key-secrets", _with(("secrets", "<S>")), "$.secrets", "unknown key"),
    ("unknown-key-newline", lambda s: json.dumps({**plumbing_min(), "a\n" + s: 1}).encode(), "$",
     "unknown key (name not shown)"),
    ("missing-models", _with(("models", _DELETE)), "$.models", "required"),
    ("schema-version-2", _with(("schema_version", 2)), "$.schema_version", "must be 1"),
    ("schema-version-string", _with(("schema_version", "1")), "$.schema_version", "must be 1"),
    ("purpose-empty", _with(("purpose", "")), "$.purpose", "must be 1 to 500 characters"),
    ("purpose-too-long", _with(("purpose", "x" * 501 + "<S>")), "$.purpose", "must be 1 to 500 characters"),
    ("purpose-control", _with(("purpose", "bell\x07<S>")), "$.purpose", "control or format character"),
    ("purpose-newline", _with(("purpose", "line\n<S>")), "$.purpose", "control or format character"),
    ("purpose-c1-control", _with(("purpose", "next\x85<S>")), "$.purpose", "control or format character"),
    ("purpose-rlo", _with(("purpose", "‮<S>")), "$.purpose", "control or format character"),
    ("purpose-zwsp", _with(("purpose", "a​<S>")), "$.purpose", "control or format character"),
    ("purpose-line-separator", _with(("purpose", "a <S>")), "$.purpose", "control or format character"),
    ("purpose-private-use", _with(("purpose", "a<S>")), "$.purpose", "control or format character"),
    ("purpose-lone-surrogate", _raw(_VALID_TEXT.replace('"Plumbing check', '"\\ud800<S> check')), "$.purpose",
     "control or format character"),
    ("purpose-placeholder", _with(("purpose", "<fill in why <S>>")), "$.purpose", PLACEHOLDER),
    ("purpose-leading-dash", _with(("purpose", "-<S>")), "$.purpose", "must not start with '-'"),
    ("provider-unknown", _with(("provider", "<S>")), "$.provider", "must be fake or the manifest's server program"),
    ("provider-placeholder", _with(("provider", "<provider <S>>")), "$.provider", PLACEHOLDER),
    ("provider-leading-dash", _with(("provider", "--<S>")), "$.provider", "must not start with '-'"),
    ("models-empty", _with(("models", [])), "$.models", "must be a list of 1 to 8 model keys"),
    ("models-unknown", _with(("models", ["fake-a", "<S>"])), "$.models[1]", "not a model in the manifest"),
    ("models-duplicate", _with(("models", ["fake-a", "fake-a"])), "$.models[1]", "duplicate"),
    ("models-too-many", _with(("models", ["fake-a"] * 9)), "$.models", "must be a list of 1 to 8 model keys"),
    ("models-key-too-long", _with(("models", ["k" * 25 + "<S>"])), "$.models[0]", "must be 1 to 24 characters"),
    ("models-not-string", _with(("models", [7])), "$.models[0]", "must be a string"),
    ("fake-kind-server-provider", _with(("provider", "llama-server")), "$.models[0]",
     "kind fake needs provider fake"),
    ("gguf-kind-fake-provider", _with(("models", ["tiny-gguf"]), ("experiments.e3.models", ["tiny-gguf"])),
     "$.models[0]", "kind gguf needs the server provider"),
    ("job-minutes-bool", _with(("job_minutes", True)), "$.job_minutes", "must be an int in [45, 330]"),
    ("job-minutes-float", _with(("job_minutes", 45.0)), "$.job_minutes", "must be an int in [45, 330]"),
    ("job-minutes-low", _with(("job_minutes", 44)), "$.job_minutes", "must be an int in [45, 330]"),
    ("job-minutes-placeholder", _with(("job_minutes", "<minutes <S>>")), "$.job_minutes", PLACEHOLDER),
    ("job-minutes-dash-string", _with(("job_minutes", "-<S>")), "$.job_minutes", "must not start with '-'"),
    ("max-parallel-high", _with(("max_parallel", 17)), "$.max_parallel", "must be an int in [1, 16]"),
    ("retention-zero", _with(("retention_days", 0)), "$.retention_days", "must be an int in [1, 90]"),
    ("experiments-missing", _with(("experiments", _DELETE), sentinel_at="purpose"), "$.experiments", "required"),
    ("experiments-empty", _with(("experiments", {}), sentinel_at="purpose"), "$.experiments",
     "needs at least one of e1, e2, e3, g0, sim, x1, openfda"),
    ("experiment-e4", _with(("experiments.e4", {"seed": "<S>"})), "$.experiments.e4", "unknown experiment"),
    ("experiment-n1", _with(("experiments.n1", "<S>")), "$.experiments.n1", "unknown experiment"),
    ("sim-not-object", _with(("experiments.sim", "<S>"), sentinel_at=None), "$.experiments.sim", "must be an object"),
    ("sim-unknown-key", _with(("experiments.sim", {**sim_block(), "speed": "<S>"}), sentinel_at="purpose"),
     "$.experiments.sim.speed", "unknown key"),
    ("sim-missing-plant", _with(("experiments.sim", sim_block()), ("experiments.sim.plant", _DELETE),
                                sentinel_at="purpose"), "$.experiments.sim.plant", "required"),
    ("sim-plant-unknown", _with(("experiments.sim", sim_block(plant="<S>")), sentinel_at="purpose"),
     "$.experiments.sim.plant", "must be one of plant_smoke, sim_small"),
    ("sim-plant-path", _with(("experiments.sim", sim_block(plant="lab/plants/device_quality/<S>.json")),
                             sentinel_at="purpose"), "$.experiments.sim.plant",
     "must be one of plant_smoke, sim_small"),
    ("sim-plant-placeholder", _with(("experiments.sim", sim_block(plant="<plant <S>>")), sentinel_at="purpose"),
     "$.experiments.sim.plant", PLACEHOLDER),
    ("sim-weeks-low", _with(("experiments.sim", sim_block(weeks=33)), sentinel_at="purpose"),
     "$.experiments.sim.weeks", "must be an int in [34, 52]"),
    ("sim-plant-does-not-fit", _with(("experiments.sim", sim_block(plant="plant_smoke")), sentinel_at="purpose"),
     "$.experiments.sim.weeks", "the plant does not fit these weeks ("),
    ("sim-top-n-high", _with(("experiments.sim", sim_block(top_n=61)), sentinel_at="purpose"),
     "$.experiments.sim.top_n", "must be an int in [1, 60]"),
    ("sim-seeds-duplicate", _with(("experiments.sim", sim_block(seeds=[1, 1])), sentinel_at="purpose"),
     "$.experiments.sim.seeds[1]", "duplicate"),
    ("sim-seeds-too-many", _with(("experiments.sim", sim_block(seeds=[1, 2, 3, 4, 5, 6])), sentinel_at="purpose"),
     "$.experiments.sim.seeds", "must be a list of 1 to 5 seeds"),
    ("sim-seed-placeholder", _with(("experiments.sim", sim_block(seeds=["<seed <S>>"])), sentinel_at=None),
     "$.experiments.sim.seeds[0]", PLACEHOLDER),
    ("sim-models-not-subset", _with(("experiments.sim", sim_block(models=["fake-fail"])), sentinel_at="purpose"),
     "$.experiments.sim.models[0]", "not one of $.models"),
    ("experiment-unsafe-name", _with(("experiments.<S>\n", {})), "$.experiments",
     "unknown experiment (name not shown)"),
    ("e3-unknown-key", _with(("experiments.e3.speed", "<S>")), "$.experiments.e3.speed", "unknown key"),
    ("e3-missing-seed", _with(("experiments.e3.seed", _DELETE)), "$.experiments.e3.seed", "required"),
    ("e3-minutes-capacity", _with(("experiments.e3.minutes", 21)), "$.experiments.e3.minutes",
     "exceeds the shard capacity"),
    ("e3-minutes-zero", _with(("experiments.e3.minutes", 0)), "$.experiments.e3.minutes", "must be an int >= 1"),
    ("g0-minutes-huge", _with(("experiments.g0.minutes", 10 ** 7), sentinel_at="purpose"),
     "$.experiments.g0.minutes", "exceeds the shard capacity"),
    ("e3-models-not-subset", _with(("experiments.e3.models", ["fake-fail"])), "$.experiments.e3.models[0]",
     "not one of $.models"),
    ("e3-models-empty", _with(("experiments.e3.models", [])), "$.experiments.e3.models",
     "must be a list of 1 to 8 model keys"),
    ("e3-concurrency-duplicate", _with(("experiments.e3.concurrency", [1, 1])), "$.experiments.e3.concurrency[1]",
     "duplicate"),
    ("e3-concurrency-high", _with(("experiments.e3.concurrency", [9])), "$.experiments.e3.concurrency[0]",
     "must be an int in [1, 8]"),
    ("e3-concurrency-too-many", _with(("experiments.e3.concurrency", [1, 2, 3, 4, 5])),
     "$.experiments.e3.concurrency", "must be a list of 1 to 4 ints"),
    ("e3-requests-low", _with(("experiments.e3.requests", 2)), "$.experiments.e3.requests",
     "must be an int in [3, 200]"),
    ("e3-warmup-not-below-requests", _with(("experiments.e3.warmup", 3)), "$.experiments.e3.warmup",
     "must be an int in [0, requests - 1]"),
    ("e3-workload-unknown", _with(("experiments.e3.workloads", ["<S>"])), "$.experiments.e3.workloads[0]",
     "must be one of extraction, short"),
    ("e3-workloads-empty", _with(("experiments.e3.workloads", [])), "$.experiments.e3.workloads",
     "must be a list of 1 to 2 workloads"),
    ("e3-seed-negative", _with(("experiments.e3.seed", -1)), "$.experiments.e3.seed",
     "must be an int in [0, 2147483647]"),
    ("e3-seed-too-big", _with(("experiments.e3.seed", 2147483648)), "$.experiments.e3.seed",
     "must be an int in [0, 2147483647]"),
    ("e3-seed-placeholder", _with(("experiments.e3.seed", "<seed <S>>")), "$.experiments.e3.seed", PLACEHOLDER),
    ("g0-pack-relative-path", _with(("experiments.g0.pack", "../packs/<S>"), sentinel_at=None),
     "$.experiments.g0.pack", PACK_PROBLEM),
    ("g0-pack-absolute-path", _with(("experiments.g0.pack", "/etc/<S>"), sentinel_at=None),
     "$.experiments.g0.pack", PACK_PROBLEM),
    ("g0-pack-repo-path", _with(("experiments.g0.pack", "mycelic/collective/packs/data/device_quality"),
                                ("purpose", "a repo path <S>"), sentinel_at=None), "$.experiments.g0.pack",
     PACK_PROBLEM),
    ("g0-pack-unknown-id", _with(("experiments.g0.pack", "no_such_pack"), ("purpose", "unknown pack <S>"),
                                 sentinel_at=None), "$.experiments.g0.pack", PACK_PROBLEM),
    ("g0-pack-unknown-sentinel", _with(), "$.experiments.g0.pack", PACK_PROBLEM),
    ("g0-records-low", _with(("experiments.g0.records", 49), sentinel_at="purpose"), "$.experiments.g0.records",
     "must be an int in [50, 2000]"),
    ("g0-records-bool", _with(("experiments.g0.records", False), sentinel_at="purpose"), "$.experiments.g0.records",
     "must be an int in [50, 2000]"),
    ("e1-unknown-key", _block("e1", E1, speed="<S>"), "$.experiments.e1.speed", "unknown key"),
    ("e1-missing-reference", _block("e1", E1, reference=_DELETE), "$.experiments.e1.reference", "required"),
    ("e1-one-model", _block("e1", E1, models=["fake-a"], reference="fake-a"), "$.experiments.e1.models",
     "E1 needs at least 2 models"),
    ("e1-one-request-model", lambda s: _with(("models", ["fake-a"]), ("experiments", {"e1": {**E1,
                                                                                              "reference": "fake-a"}}),
                                             sentinel_at="purpose")(s), "$.experiments.e1",
     "E1 needs at least 2 models"),
    ("e1-reference-outside", _block("e1", E1, models=["fake-a", "fake-b"], reference="fake-fail"),
     "$.experiments.e1.reference", "must be one of the E1 models"),
    ("e1-runs-below-three", _block("e1", E1, runs=2), "$.experiments.e1.runs", "must be an int in [3, 5]"),
    ("e1-margin-above-twenty", _block("e1", E1, margin_points=21), "$.experiments.e1.margin_points",
     "must be an int in [1, 20]"),
    ("e1-bootstrap-low", _block("e1", E1, bootstrap_b=999), "$.experiments.e1.bootstrap_b",
     "must be an int in [1000, 20000]"),
    ("e1-labels-source", _block("e1", E1, labels=_e1_labels(source="human")), "$.experiments.e1.labels.source",
     "must be one of fixtures, generator"),
    ("e1-fixtures-with-n", _block("e1", E1, labels={"source": "fixtures", "pack": "device_quality", "n": 40}),
     "$.experiments.e1.labels.n", "only for generator labels"),
    ("e1-fixtures-with-seed", _block("e1", E1, labels={"source": "fixtures", "pack": "device_quality", "seed": 1}),
     "$.experiments.e1.labels.seed", "only for generator labels"),
    ("e1-generator-without-n", _block("e1", E1, labels={"source": "generator", "pack": "device_quality", "seed": 1}),
     "$.experiments.e1.labels.n", "required"),
    ("e1-labels-n-low", _block("e1", E1, labels=_e1_labels(n=39)), "$.experiments.e1.labels.n",
     "must be an int in [40, 2000]"),
    ("e1-labels-pack", _block("e1", E1, labels=_e1_labels(pack="no_such_pack")), "$.experiments.e1.labels.pack",
     PACK_PROBLEM),
    ("e2-bad-plant-name", _block("e2", E2, plant="plant_<S>"), "$.experiments.e2.plant",
     "must name a plant fixture of the pack"),
    ("e2-plant-path", _block("e2", E2, plant="../device_quality/fixtures/plant_e2_smoke"), "$.experiments.e2.plant",
     "must name a plant fixture of the pack"),
    ("e2-plant-of-another-pack", _block("e2", E2, pack="claims_integrity"), "$.experiments.e2.plant",
     "must name a plant fixture of the pack"),
    ("e2-eval-from-below-nineteen", _block("e2", E2, eval_from=18), "$.experiments.e2.eval_from",
     "must be an int in [19, 51]"),
    ("e2-eval-to-before-from", _block("e2", E2, eval_to=19), "$.experiments.e2.eval_to",
     "must be an int in [20, 51]"),
    ("e2-weeks-above-104", _block("e2", E2, weeks=105), "$.experiments.e2.weeks", "must be an int in [34, 104]"),
    ("e2-tie-salt-space", _block("e2", E2, tie_salt="lab e2"), "$.experiments.e2.tie_salt",
     "must match [A-Za-z0-9._-]{1,64}"),
    ("e2-author-81", _block("e2", E2, detector_author="a" * 81), "$.experiments.e2.detector_author",
     "must be 1 to 80 characters"),
    ("e2-author-nbsp", _block("e2", E2, detector_author="mycelic\u00a0engineering"),
     "$.experiments.e2.detector_author", "must be printable characters"),
    ("e2-top-n-low", _block("e2", E2, top_n=4), "$.experiments.e2.top_n", "must be an int in [5, 60]"),
    ("e2-min-candidates-above-top-n", _block("e2", E2, min_candidates=6), "$.experiments.e2.min_candidates",
     "must be an int in [1, 5]"),
    ("e2-central-not-self", _block("e2", E2, central="hosted"), "$.experiments.e2.central",
     "must be self or a hosted model of $.models"),
    ("e2-seeds-six", _block("e2", E2, seeds=[1, 2, 3, 4, 5, 6]), "$.experiments.e2.seeds",
     "must be a list of 1 to 5 seeds"),
    ("e2-grace-high", _block("e2", E2, grace_weeks=9), "$.experiments.e2.grace_weeks", "must be an int in [0, 8]"),
    ("x1-models-key", _block("x1", X1, models=["fake-a"]), "$.experiments.x1.models", "unknown key"),
    ("x1-seeds-eleven", _block("x1", X1, seeds=list(range(11))), "$.experiments.x1.seeds",
     "must be a list of 1 to 10 seeds"),
    ("x1-missing-plant", _block("x1", X1, plant=_DELETE), "$.experiments.x1.plant", "required"),
    ("openfda-pack-without-mapping", _block("openfda", OPENFDA, pack="claims_integrity"),
     "$.experiments.openfda.pack", "must be a built-in pack with an openFDA mapping"),
    ("openfda-code-lowercase", _block("openfda", OPENFDA, product_codes=["aaa", "BBB", "CCC"]),
     "$.experiments.openfda.product_codes[0]", "must be three upper-case letters"),
    ("openfda-two-codes", _block("openfda", OPENFDA, product_codes=["AAA", "BBB"]),
     "$.experiments.openfda.product_codes", "must be a list of 3 to 5 product codes"),
    ("openfda-code-duplicate", _block("openfda", OPENFDA, product_codes=["AAA", "BBB", "AAA"]),
     "$.experiments.openfda.product_codes[2]", "duplicate"),
    ("openfda-date-not-calendar", _block("openfda", OPENFDA, date_from="20240230"), "$.experiments.openfda.date_from",
     "must be a calendar date YYYYMMDD"),
    ("openfda-date-dashed", _block("openfda", OPENFDA, date_to="2024-12-29"), "$.experiments.openfda.date_to",
     "must be a calendar date YYYYMMDD"),
    ("openfda-date-to-before-from", _block("openfda", OPENFDA, date_to="20231229"), "$.experiments.openfda.date_to",
     "must be after date_from"),
    ("openfda-span-too-short", _block("openfda", OPENFDA, date_to="20240301"), "$.experiments.openfda.date_to",
     "the date range must span at least 21 ISO weeks"),
    ("openfda-manufacturer-leading-dash", _block("openfda", OPENFDA, manufacturers=["-ACME"]),
     "$.experiments.openfda.manufacturers[0]", "must not start with '-'"),
    ("openfda-manufacturer-newline", _block("openfda", OPENFDA, manufacturers=["ACME\nDevices"]),
     "$.experiments.openfda.manufacturers[0]", "control or format character"),
    ("openfda-manufacturer-nbsp", _block("openfda", OPENFDA, manufacturers=["ACME\u00a0Devices"]),
     "$.experiments.openfda.manufacturers[0]", "must be printable characters"),
    ("openfda-manufacturer-duplicate", _block("openfda", OPENFDA, manufacturers=["ACME", "ACME"]),
     "$.experiments.openfda.manufacturers[1]", "duplicate"),
    ("openfda-manufacturers-eleven", _block("openfda", OPENFDA, manufacturers=[f"M{i}" for i in range(11)]),
     "$.experiments.openfda.manufacturers", "must be a list of 1 to 10 names"),
    ("openfda-firm-too-long", _block("openfda", OPENFDA, recalling_firms=["x" * 121]),
     "$.experiments.openfda.recalling_firms[0]", "must be 1 to 120 characters"),
    ("openfda-field-dot-dot", _block("openfda", OPENFDA, manufacturer_field="device..manufacturer_d_name"),
     "$.experiments.openfda.manufacturer_field", "must be a field path such as device[].manufacturer_d_name"),
    ("openfda-field-trailing-dot", _block("openfda", OPENFDA, partition_field="event_location."),
     "$.experiments.openfda.partition_field", "must be a field path such as device[].manufacturer_d_name"),
    ("openfda-missing-saw", _block("openfda", OPENFDA, saw_recall_outcomes=_DELETE),
     "$.experiments.openfda.saw_recall_outcomes", "required"),
    ("openfda-saw-maybe", _block("openfda", OPENFDA, saw_recall_outcomes="maybe"),
     "$.experiments.openfda.saw_recall_outcomes", "must be yes or no"),
    ("openfda-coverage-zero", _block("openfda", OPENFDA, min_partition_coverage=0),
     "$.experiments.openfda.min_partition_coverage", "must be a number in (0, 1]"),
    ("openfda-coverage-bool", _block("openfda", OPENFDA, min_partition_coverage=True),
     "$.experiments.openfda.min_partition_coverage", "must be a number in (0, 1]"),
    ("openfda-post-weeks-zero", _block("openfda", OPENFDA, post_weeks=0), "$.experiments.openfda.post_weeks",
     "must be an int in [1, 104]"),
    ("openfda-n1-sheet-large", _block("openfda", OPENFDA, n1_sheet={"n": 1001, "seed": 1}),
     "$.experiments.openfda.n1_sheet.n", "must be an int in [1, 1000]"),
    ("openfda-e1-sheet-no-seed", _block("openfda", OPENFDA, e1_sheet={"n": 20}), "$.experiments.openfda.e1_sheet.seed",
     "required"),
    ("openfda-records-above-cap", _block("openfda", OPENFDA, max_records_per_code=25001),
     "$.experiments.openfda.max_records_per_code", "must be an int in [1, 25000]"),
    ("openfda-budget-without-key", _block("openfda", OPENFDA, product_codes=["AAA", "BBB", "CCC", "DDD", "EEE"],
                                          max_records_per_code=25000),
     "$.experiments.openfda.max_records_per_code", "the fetch would make about 2500 openFDA requests, more than "
                                                   "the 800 allowed without an API key"),
    ("json-bom", lambda s: b"\xef\xbb\xbf" + _VALID_TEXT.replace("Plumbing", s).encode(), "$", "invalid JSON (bom)"),
    ("json-not-utf8", lambda s: _VALID_TEXT.replace("Plumbing", s).encode().replace(b"check", b"\xff\xfe"), "$",
     "invalid JSON (encoding)"),
    ("json-nan", _raw(_VALID_TEXT.replace('"job_minutes": 45', '"job_minutes": NaN').replace("Plumbing", "<S>")),
     "$", "invalid JSON (non_finite)"),
    ("json-infinity", _raw(_VALID_TEXT.replace('"job_minutes": 45', '"job_minutes": Infinity')
                           .replace("Plumbing", "<S>")), "$", "invalid JSON (non_finite)"),
    ("json-1e999", _raw(_VALID_TEXT.replace('"job_minutes": 45', '"job_minutes": 1e999').replace("Plumbing", "<S>")),
     "$", "invalid JSON (non_finite)"),
    ("json-duplicate-safe", _raw('{"schema_version": 1, "schema_version": 1, "purpose": "<S>"}'),
     "$.schema_version", "invalid JSON (duplicate_key)"),
    ("json-duplicate-unsafe", _raw('{"<S>": 1, "<S>": 2}'), "$", "invalid JSON (duplicate_key)"),
    ("json-syntax", _raw('{"purpose": "<S>", '), "$", "invalid JSON (syntax)"),
    ("over-64-kib", lambda s: json.dumps({**plumbing_min(), "purpose": s}).encode() + b" " * 65536, "$",
     "larger than 64 KiB"),
]


class MalformedRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-request-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _check(self, index: int, label: str, build: Callable[[str], bytes], path: str, problem: str,
               subprocess_run: bool = False) -> None:
        sentinel = _sentinel(index)
        body = build(sentinel)
        self.assertIn(sentinel.encode(), body, "every case must carry its sentinel")
        request = self.tmp / f"case-{index:02d}.json"
        request.write_bytes(body)
        out_dir = self.tmp / f"out-{index:02d}"
        argv = ["--request", str(request), "--manifest", str(MANIFEST_TEST), "--out", str(out_dir)]
        if subprocess_run:
            r = lab_cli("lab.plan", *argv)
            code, stdout, stderr = r.returncode, r.stdout, r.stderr
        else:
            code, stdout, stderr = run_plan(argv)
        self.assertEqual(code, 2, stderr)
        first = stderr.splitlines()[0]
        self.assertTrue(first.startswith(f"error: {path}: {problem}"), first)
        error = json.loads((out_dir / "plan-error.json").read_text(encoding="utf-8"))
        self.assertEqual((error["kind"], error["source"], error["path"]), ("lab_plan_error", "request", path))
        self.assertTrue(error["problem"].startswith(problem), error["problem"])
        self.assertEqual(first, f"error: {error['path']}: {error['problem']}")
        self.assertTrue(any(line.startswith("::error file=") for line in stdout.splitlines()), stdout)
        self.assertFalse((out_dir / "plan.json").exists())
        for text in (stdout, stderr, (out_dir / "plan-error.json").read_text(encoding="utf-8")):
            self.assertNotIn(sentinel, text)

    def test_table_has_at_least_forty_cases(self) -> None:
        self.assertGreaterEqual(len(CASES), 40)
        self.assertEqual(len({c[0] for c in CASES}), len(CASES))

    def test_every_case_in_process(self) -> None:
        for index, (label, build, path, problem) in enumerate(CASES):
            with self.subTest(label):
                self._check(index, label, build, path, problem)

    def test_three_cases_as_subprocesses(self) -> None:
        labels = ("unknown-key-newline", "e3-seed-placeholder", "json-duplicate-unsafe")
        for index, (label, build, path, problem) in enumerate(CASES):
            if label in labels:
                with self.subTest(label):
                    self._check(index, label, build, path, problem, subprocess_run=True)

    def test_messages_never_hold_the_value(self) -> None:
        obj = plumbing_min()
        obj["experiments"]["e3"]["workloads"] = ["Qz99SnTl-workload"]
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertNotIn("Qz99SnTl", str(caught.exception))


class ExperimentBlockTests(unittest.TestCase):
    """The E1, E2, X1 and openFDA blocks' normalised form: every key present, the documented defaults filled in."""

    def blocks(self, *, openfda_key: bool = False, **blocks: Any) -> dict[str, Any]:
        obj = plumbing_min()
        obj["experiments"] = blocks
        return validate(obj, MANIFEST, openfda_key=openfda_key)["experiments"]

    def test_defaults(self) -> None:
        got = self.blocks(e1=E1, e2=E2, x1=X1, openfda=OPENFDA)
        self.assertEqual(list(got), ["e1", "e2", "x1", "openfda"])
        self.assertEqual(got["e1"], {**E1, "models": ["fake-a", "fake-b"], "bootstrap_b": 10000})
        self.assertEqual(list(got["e1"]), ["models", "minutes", "labels", "reference", "margin_points", "runs", "seed",
                                           "bootstrap_b"])
        self.assertEqual(got["e2"], {**E2, "models": ["fake-a", "fake-b"], "grace_weeks": 4, "tie_salt": "lab-e2",
                                     "detector_author": "mycelic engineering", "min_candidates": 5, "central": "self",
                                     "bootstrap_b": 10000, "bootstrap_seed": 1})
        self.assertEqual(got["x1"], {**X1, "grace_weeks": 4, "tie_salt": "lab-x1",
                                     "detector_author": "mycelic engineering", "bootstrap_b": 10000,
                                     "bootstrap_seed": 1})
        self.assertEqual(got["openfda"], {**OPENFDA, "recalling_firms": ["ACME Devices"], "lookback_weeks": 26,
                                          "post_weeks": 26, "min_partition_coverage": 0.5, "tie_salt": "lab-replay",
                                          "n1_sheet": None, "e1_sheet": None})

    def test_given_values_are_kept(self) -> None:
        got = self.blocks(e2={**E2, "models": ["fake-b"], "min_candidates": 3, "tie_salt": "s.1", "seeds": [3, 1]},
                          openfda={**OPENFDA, "recalling_firms": ["ACME Devices, Inc."], "min_partition_coverage": 1,
                                   "n1_sheet": {"n": 30, "seed": 2}, "manufacturers": ["A&B Co.", "ACME Devices"]})
        self.assertEqual((got["e2"]["models"], got["e2"]["min_candidates"], got["e2"]["tie_salt"], got["e2"]["seeds"]),
                         (["fake-b"], 3, "s.1", [3, 1]))
        self.assertEqual((got["openfda"]["recalling_firms"], got["openfda"]["min_partition_coverage"],
                          got["openfda"]["n1_sheet"], got["openfda"]["manufacturers"]),
                         (["ACME Devices, Inc."], 1, {"n": 30, "seed": 2}, ["A&B Co.", "ACME Devices"]))

    def test_fixtures_labels_have_no_n_or_seed(self) -> None:
        got = self.blocks(e1={**E1, "labels": {"source": "fixtures", "pack": "claims_integrity"}})
        self.assertEqual(got["e1"]["labels"], {"source": "fixtures", "pack": "claims_integrity", "n": None,
                                               "seed": None})

    def test_budget_with_and_without_the_key(self) -> None:
        big = {**OPENFDA, "product_codes": ["AAA", "BBB", "CCC", "DDD", "EEE"], "max_records_per_code": 25000}
        self.assertEqual(self.blocks(openfda=big, openfda_key=True)["openfda"]["max_records_per_code"], 25000)
        with self.assertRaises(RequestError) as caught:
            self.blocks(openfda=big)
        self.assertEqual(caught.exception.path, "$.experiments.openfda.max_records_per_code")
        # without the key a page is 100 records: 5 codes x 2 x 250 pages
        self.assertIn("about 2500 openFDA requests", caught.exception.problem)
        self.assertEqual(self.blocks(openfda={**OPENFDA, "max_records_per_code": 13300})["openfda"]
                         ["max_records_per_code"], 13300)
        with self.assertRaises(RequestError) as caught:
            self.blocks(openfda={**OPENFDA, "max_records_per_code": 13400})
        self.assertIn("about 804 openFDA requests, more than the 800 allowed without an API key",
                      caught.exception.problem)

    def test_model_kinds_for_e1_and_e2(self) -> None:
        from lab import request as lab_request
        problems = {"e1": "E1 runs only gguf, fake or hosted models", "e2": "E2 runs only gguf or fake models"}
        with mock.patch.object(lab_request, "SIM_KINDS", ("gguf",)):
            for name, block in (("e1", E1), ("e2", E2)):
                with self.subTest(name=name), self.assertRaises(RequestError) as caught:
                    self.blocks(**{name: block})
                self.assertEqual((caught.exception.path, caught.exception.problem), ("$.models[0]", problems[name]))

    def test_hosted_central_needs_context_tokens(self) -> None:
        self.assertEqual(HOSTED_CENTRAL_CONTEXT, "a hosted central comparator needs context_tokens in its manifest "
                                                 "entry (the context one request gets on the host)")
        obj = plumbing_min()
        obj["models"] = ["fake-a", "fake-b", "h-b"]
        obj["experiments"] = {"e2": {**E2, "models": ["fake-a"], "central": "h-b"}}
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual((caught.exception.path, caught.exception.problem),
                         ("$.experiments.e2.central", HOSTED_CENTRAL_CONTEXT))
        obj["models"][2] = "h-a"
        obj["experiments"]["e2"]["central"] = "h-a"
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual(caught.exception.path, "$.hosted")

    def test_block_order(self) -> None:
        with self.assertRaises(RequestError) as caught:
            self.blocks(x1={**X1, "seeds": []}, e1={**E1, "runs": 2})
        self.assertEqual(caught.exception.path, "$.experiments.e1.runs")
        with self.assertRaises(RequestError) as caught:
            self.blocks(openfda={**OPENFDA, "pack": "claims_integrity", "date_to": "x"})
        self.assertEqual(caught.exception.path, "$.experiments.openfda.pack")


class LoadTests(unittest.TestCase):
    def test_plumbing_min_normalised(self) -> None:
        request = load_request(PLUMBING_MIN, MANIFEST, root=ROOT)
        self.assertEqual(request.path, "tests/lab/data/requests/plumbing-min.json")
        self.assertEqual(request.name, "plumbing-min")
        import hashlib
        self.assertEqual(request.sha256, hashlib.sha256(PLUMBING_MIN.read_bytes()).hexdigest())
        self.assertEqual(request.data["experiments"]["e3"]["models"], ["fake-a", "fake-b"])
        self.assertEqual(request.data["experiments"]["g0"]["models"], ["fake-b"])
        self.assertEqual(request.data["provider"], "fake")

    def test_sim_block_normalised_with_default_models(self) -> None:
        obj = plumbing_min()
        obj["experiments"]["sim"] = {"minutes": 5, "plant": "sim_small", "weeks": 34, "top_n": 10, "seeds": [2, 1]}
        self.assertEqual(validate(obj, MANIFEST)["experiments"]["sim"],
                         {"models": ["fake-a", "fake-b"], "minutes": 5, "plant": "sim_small", "weeks": 34,
                          "top_n": 10, "seeds": [2, 1]})
        obj["experiments"]["sim"]["models"] = ["fake-b"]
        self.assertEqual(validate(obj, MANIFEST)["experiments"]["sim"]["models"], ["fake-b"])
        obj["experiments"] = {"sim": {**obj["experiments"]["sim"], "plant": "plant_smoke", "weeks": 52}}
        self.assertEqual(list(validate(obj, MANIFEST)["experiments"]), ["sim"])

    def test_markdown_purpose_is_kept_verbatim(self) -> None:
        obj = plumbing_min()
        obj["purpose"] = "**bold** [link](x) | table | `code`"
        self.assertEqual(validate(obj, MANIFEST)["purpose"], obj["purpose"])

    def test_pack_as_path_unknown_or_absolute_gets_one_message(self) -> None:
        for pack in ("../device_quality", "/abs/device_quality", "mycelic/collective/packs/data/device_quality",
                     "unknown_pack", "Device_quality", "device_quality/", "x" * 80):
            obj = plumbing_min()
            obj["experiments"]["g0"]["pack"] = pack
            with self.subTest(pack=pack), self.assertRaises(RequestError) as caught:
                validate(obj, MANIFEST)
            self.assertEqual((caught.exception.path, caught.exception.problem), ("$.experiments.g0.pack", PACK_PROBLEM))

    def test_provider_follows_the_manifest_server(self) -> None:
        obj = plumbing_min()
        obj.update(provider="llama-server", models=["tiny-gguf"])
        obj["experiments"] = {"e3": {**obj["experiments"]["e3"]}}
        self.assertEqual(validate(obj, MANIFEST)["provider"], "llama-server")
        self.assertEqual(MANIFEST.providers(), ("fake", MANIFEST.server["program"]))
        obj["provider"] = "other-server"
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual(caught.exception.path, "$.provider")
        self.assertEqual(validate(plumbing_min(), MANIFEST)["provider"], "fake")

    def test_hazard_order(self) -> None:
        obj = plumbing_min()
        obj["purpose"] = "-<x>\x07"
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual(caught.exception.problem, PLACEHOLDER)
        obj["purpose"] = "-\x07"
        with self.assertRaises(RequestError) as caught:
            validate(obj, MANIFEST)
        self.assertEqual(caught.exception.problem, "control or format character")


class PathRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lab-paths-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.body = PLUMBING_MIN.read_bytes()

    def _put(self, rel: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.body)
        return path

    def _refused(self, path: Path, problem: str, *, strict: bool = True) -> None:
        with self.assertRaises(RequestError) as caught:
            load_request(path, MANIFEST, strict_location=strict, root=self.root)
        self.assertEqual((caught.exception.path, caught.exception.problem), ("request", problem))

    def test_strict_location(self) -> None:
        ok = self._put("lab/requests/ok.json")
        self.assertEqual(load_request(ok, MANIFEST, strict_location=True, root=self.root).path, "lab/requests/ok.json")
        self._refused(self._put("lab/requests/sub/x.json"), "must be lab/requests/<name>.json")
        self._refused(self._put("other/x.json"), "must be lab/requests/<name>.json")
        self._refused(self._put("lab/requests/Bad.json"), "bad request name")
        link = self.root / "lab" / "requests" / "link.json"
        link.symlink_to(ok)
        self._refused(link, "is a symlink")
        (self.root / "lab" / "requests" / "x.json").mkdir()
        self._refused(self.root / "lab" / "requests" / "x.json", "not a regular file")
        self._refused(self.root / "lab" / "requests" / "missing.json", "not found")

    def test_requests_directory_symlink(self) -> None:
        real = self.root / "elsewhere"
        real.mkdir()
        (real / "ok.json").write_bytes(self.body)
        (self.root / "lab").mkdir()
        (self.root / "lab" / "requests").symlink_to(real, target_is_directory=True)
        self._refused(self.root / "lab" / "requests" / "ok.json", "must be lab/requests/<name>.json")

    def test_relaxed_request_mode(self) -> None:
        path = self._put("anywhere/my-request.json")
        request = load_request(path, MANIFEST, root=self.root)
        self.assertEqual((request.name, request.path), ("my-request", "anywhere/my-request.json"))
        link = self.root / "anywhere" / "link.json"
        link.symlink_to(path)
        self._refused(link, "is a symlink", strict=False)
        self._refused(self._put("anywhere/Bad_Name.json"), "bad request name", strict=False)
        self._refused(self._put("anywhere/name.JSON"), "bad request name", strict=False)
        self._refused(self._put("anywhere/a.b.json"), "bad request name", strict=False)

    def test_size_limit(self) -> None:
        text = json.dumps(plumbing_min()).encode("utf-8")
        exact = self.root / "exact.json"
        exact.write_bytes(text + b" " * (65536 - len(text)))
        self.assertEqual(exact.stat().st_size, 65536)
        load_request(exact, MANIFEST, root=self.root)
        over = self.root / "over.json"
        over.write_bytes(text + b" " * (65537 - len(text)))
        with self.assertRaises(RequestError) as caught:
            load_request(over, MANIFEST, root=self.root)
        self.assertEqual((caught.exception.path, caught.exception.problem), ("$", "larger than 64 KiB"))


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-manifest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.manifest = json.loads(MANIFEST_TEST.read_text(encoding="utf-8"))
        self.lock = {"schema_version": 1, "server": None, "models": {}}

    def _load(self, manifest: dict[str, Any], lock: dict[str, Any] | None = None) -> Any:
        write_json(self.tmp / "m.json", manifest)
        write_json(self.tmp / "m.lock.json", self.lock if lock is None else lock)
        return load_manifest(self.tmp / "m.json")

    def _refused(self, manifest: dict[str, Any], path: str, problem: str, lock: dict[str, Any] | None = None) -> None:
        with self.assertRaises(ManifestError) as caught:
            self._load(manifest, lock)
        self.assertEqual(caught.exception.path, path)
        self.assertTrue(caught.exception.problem.startswith(problem), caught.exception.problem)

    def test_test_manifests_load(self) -> None:
        self.assertTrue((ROOT / "tests" / "lab" / "data" / "manifest-test.lock.json").is_file())
        self.assertEqual(MANIFEST.models["fake-fail"]["persona"], "unauthorized")
        self.assertEqual(MANIFEST.models["tiny-gguf"]["ctx_per_slot"], 16384)
        self.assertEqual(MANIFEST.server["threads"], "physical")
        self.assertEqual(MANIFEST.server["cache_ram_mib"], 1024)

    def test_hosted_context_tokens(self) -> None:
        self.assertEqual(CONTEXT_TOKENS_RANGE, (2048, 2000000))
        self.assertEqual(MANIFEST.models["h-a"]["context_tokens"], 32768)
        self.assertIsNone(MANIFEST.models["h-b"]["context_tokens"])
        for value in (2048, 2000000):
            with self.subTest(value=value):
                m = copy.deepcopy(self.manifest)
                m["models"]["h-b"]["context_tokens"] = value
                self.assertEqual(self._load(m).models["h-b"]["context_tokens"], value)
        for value in (2047, 2000001, True, False, 32768.0, "32768", None):
            with self.subTest(value=value):
                m = copy.deepcopy(self.manifest)
                m["models"]["h-b"]["context_tokens"] = value
                self._refused(m, "$.models.h-b.context_tokens", "must be an int in [2048, 2000000]")
        m = copy.deepcopy(self.manifest)
        m["models"]["tiny-gguf"]["context_tokens"] = 4096
        self._refused(m, "$.models.tiny-gguf.context_tokens", "unknown key")

    def test_gguf_needs_a_server(self) -> None:
        m = copy.deepcopy(self.manifest)
        m["server"] = None
        self._refused(m, "$.server", "required when a gguf model is listed")

    def test_reserved_key(self) -> None:
        m = copy.deepcopy(self.manifest)
        m["models"]["none"] = m["models"]["fake-a"]
        self._refused(m, "$.models.none", "reserved")

    def test_server_args_allowlist(self) -> None:
        for args in ([], ["--reasoning", "off"], ["--reasoning", "on"], ["--reasoning", "auto"],
                     ["--reasoning-budget", "0"], ["--reasoning-budget", "-1"], ["--reasoning-budget", "32768"],
                     ["--jinja"], ["--no-jinja"], ["--reasoning", "off", "--reasoning-budget", "128", "--jinja"]):
            with self.subTest(args=args):
                self.assertEqual(parse_server_args(args, "$.x"), tuple(args))
                m = copy.deepcopy(self.manifest)
                m["models"]["tiny-gguf"]["server_args"] = args
                self.assertEqual(self._load(m).models["tiny-gguf"]["server_args"], args)
                m = copy.deepcopy(self.manifest)
                m["server"]["args"] = args
                self.assertEqual(self._load(m).server["args"], args)
        refused = (["--threads", "4"], ["--ctx-size", "1"], ["--host"], ["--host", "0.0.0.0"], ["--port", "8080"],
                   ["--api-key", "k"], ["-m", "x.gguf"], ["--alias", "a"], ["--seed", "1"], ["--cache-ram", "0"],
                   ["--fit", "on"], ["-np", "4"], ["--reasoning"], ["--reasoning", "maybe"],
                   ["--reasoning-budget"], ["--reasoning-budget", "x"], ["--reasoning-budget", "-2"],
                   ["--reasoning-budget", "32769"], ["--reasoning-budget", "+1"], ["--jinja", "yes"], "--jinja",
                   [7], ["--reasoning", "off", "--reasoning", "on"])
        for args in refused:
            with self.subTest(args=args):
                with self.assertRaises(ManifestError) as caught:
                    parse_server_args(args, "$.x")
                problem = ARGS_REPEAT if args == ["--reasoning", "off", "--reasoning", "on"] else ARGS_PROBLEM
                self.assertEqual(caught.exception.problem, problem)
                m = copy.deepcopy(self.manifest)
                m["server"]["args"] = args
                self._refused(m, "$.server.args", problem)
                m = copy.deepcopy(self.manifest)
                m["models"]["tiny-gguf"]["server_args"] = args
                self._refused(m, "$.models.tiny-gguf.server_args", problem)
        for args in (["--jinja", "--no-jinja"], ["--no-jinja", "--jinja"], ["--jinja", "--jinja"]):
            with self.subTest(args=args), self.assertRaises(ManifestError) as caught:
                parse_server_args(args, "$.x")
            self.assertEqual(caught.exception.problem, ARGS_REPEAT)
        for server_args, model_args in ((["--reasoning", "off"], ["--reasoning", "on"]), (["--jinja"], ["--no-jinja"]),
                                        (["--reasoning-budget", "0"], ["--reasoning-budget", "0"])):
            m = copy.deepcopy(self.manifest)
            m["server"]["args"] = server_args
            m["models"]["tiny-gguf"]["server_args"] = model_args
            with self.subTest(server=server_args, model=model_args):
                self._refused(m, "$.models.tiny-gguf.server_args", ARGS_CROSS)
        m = copy.deepcopy(self.manifest)
        m["server"]["args"] = ["--jinja"]
        m["models"]["tiny-gguf"]["server_args"] = ["--reasoning", "off"]
        self.assertEqual(self._load(m).models["tiny-gguf"]["server_args"], ["--reasoning", "off"])

    def test_revision_branch_and_commit(self) -> None:
        for revision in ("main", "v1.2", "release_2", "0123456789abcdef0123456789abcdef01234567"):
            m = copy.deepcopy(self.manifest)
            m["models"]["tiny-gguf"]["gguf"]["revision"] = revision
            with self.subTest(revision=revision):
                self.assertEqual(self._load(m).models["tiny-gguf"]["gguf"]["revision"], revision)
        for revision in ("refs/pr/1", "a/b", "-x", "", "1abc", "main branch", "x" * 65, 7, None):
            m = copy.deepcopy(self.manifest)
            m["models"]["tiny-gguf"]["gguf"]["revision"] = revision
            with self.subTest(revision=revision):
                self._refused(m, "$.models.tiny-gguf.gguf.revision", REVISION_PROBLEM)

    def test_fake_entries(self) -> None:
        m = copy.deepcopy(self.manifest)
        m["models"]["fake-a"]["response_format"] = "json_object"
        self._refused(m, "$.models.fake-a.response_format", "a fake model needs json_schema")
        m = copy.deepcopy(self.manifest)
        m["models"]["fake-a"]["persona"] = "no-such-persona"
        self._refused(m, "$.models.fake-a.persona", "must be one of")

    def test_server_url_must_match_tag_and_asset(self) -> None:
        m = copy.deepcopy(self.manifest)
        m["server"]["url"] = m["server"]["url"].replace("b0000/", "b0001/")
        self._refused(m, "$.server.url", "must be https://github.com/")
        m = copy.deepcopy(self.manifest)
        m["server"]["url"] = m["server"]["url"].replace("github.com", "example.com")
        self._refused(m, "$.server.url", "must be https://github.com/")

    def test_hf_base(self) -> None:
        for bad in ("http://huggingface.co", "https://user:pw@huggingface.co", "https://huggingface.co/x",
                    "https://huggingface.co?q=1"):
            m = copy.deepcopy(self.manifest)
            m["hf_base"] = bad
            with self.subTest(bad=bad):
                self._refused(m, "$.hf_base", "must be an https URL")

    def test_lock_revision_and_commit_rules(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        good = {"repo": "example-org/tiny-test-GGUF", "file": "tiny-test-q4.gguf", "revision": commit,
                "commit": commit, "sha256": "a" * 64, "size": 10}
        loaded = self._load(self.manifest, {"schema_version": 1, "server": None, "models": {"tiny-gguf": good}})
        self.assertEqual(loaded.lock.models["tiny-gguf"], good)
        self.assertEqual(list(loaded.lock.models["tiny-gguf"]), ["repo", "file", "revision", "commit", "sha256",
                                                                 "size"])
        for field, value in (("repo", "example-org/other-GGUF"), ("file", "other.gguf"), ("revision", "main")):
            with self.subTest(field=field):
                self._refused(self.manifest, "lock $.models.tiny-gguf", REPIN,
                              {"schema_version": 1, "server": None, "models": {"tiny-gguf": {**good, field: value}}})
        self._refused(self.manifest, "lock $.models.tiny-gguf.commit", "must equal the revision",
                      {"schema_version": 1, "server": None, "models": {"tiny-gguf": {**good, "commit": "b" * 40}}})
        self._refused(self.manifest, "lock $.models.tiny-gguf.revision", "required",
                      {"schema_version": 1, "server": None,
                       "models": {"tiny-gguf": {k: v for k, v in good.items() if k != "revision"}}})
        branch = copy.deepcopy(self.manifest)
        branch["models"]["tiny-gguf"]["gguf"]["revision"] = "main"
        pinned = {**good, "revision": "main", "commit": "c" * 40}
        loaded = self._load(branch, {"schema_version": 1, "server": None, "models": {"tiny-gguf": pinned}})
        self.assertEqual(loaded.lock.models["tiny-gguf"]["commit"], "c" * 40)
        for bad in ("main", "C" * 40, "c" * 39, 7):
            with self.subTest(commit=bad):
                self._refused(branch, "lock $.models.tiny-gguf.commit", "must be a 40-hex commit id",
                              {"schema_version": 1, "server": None, "models": {"tiny-gguf": {**pinned, "commit": bad}}})
        self._refused(self.manifest, "lock $.models", REPIN,
                      {"schema_version": 1, "server": None, "models": {"fake-a": good}})
        self._refused(self.manifest, "lock $.server", REPIN,
                      {"schema_version": 1, "server": {"tag": "b9999", "asset": "x.tar.gz", "sha256": "c" * 64},
                       "models": {}})

    def test_missing_lock(self) -> None:
        write_json(self.tmp / "solo.json", self.manifest)
        with self.assertRaises(ManifestError) as caught:
            load_manifest(self.tmp / "solo.json")
        self.assertEqual((caught.exception.source, caught.exception.json_path), ("lock", "$"))

    def test_unknown_key_names_shown_only_when_safe(self) -> None:
        m = copy.deepcopy(self.manifest)
        m["models"]["fake-a"]["Odd Key"] = 1
        self._refused(m, "$.models.fake-a", "unknown key (name not shown)")
        m = copy.deepcopy(self.manifest)
        m["models"]["fake-a"]["extra"] = 1
        self._refused(m, "$.models.fake-a.extra", "unknown key")


class ConventionTests(unittest.TestCase):
    def test_safe_path(self) -> None:
        for p in ("$", "$.a", "$.experiments.e3.concurrency[1]", "request", "event", "manifest", "lock"):
            self.assertEqual(safe_path(p), p)
        for p in ("$.A", "$.a b", "$[\"x\"]", "line 1 column 2", "$." + "a" * 41, "plan", "", "$.a\n"):
            self.assertEqual(safe_path(p), "$")

    def test_workflow_command_escaping(self) -> None:
        self.assertEqual(gh_data("a%b\r\nc"), "a%25b%0D%0Ac")
        self.assertEqual(gh_property("a:b,c%\n"), "a%3Ab%2Cc%25%0A")

    def test_check_keys_order(self) -> None:
        with self.assertRaises(RequestError) as caught:
            check_keys({"zeta": 1, "alpha": 2}, ("x",), ("x",), "$", RequestError)
        self.assertEqual(caught.exception.path, "$.alpha")
        with self.assertRaises(RequestError) as caught:
            check_keys({}, ("x", "y"), ("y", "x"), "$", RequestError)
        self.assertEqual(caught.exception.path, "$.y")


if __name__ == "__main__":
    unittest.main()
