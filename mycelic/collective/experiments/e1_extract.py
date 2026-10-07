"""E1: can a model inside the boundary extract claims well enough? A pre-registered harness (STRATEGY section 11.2).

    python -m mycelic.collective.experiments.e1_extract prepare --pack P --source openfda|jsonl|records
        [--cache DIR] [--input FILE] [--site S] --n 600 --seed S --run-id ID
    python -m mycelic.collective.experiments.e1_extract label-check --pack P --records FILE --sheet CSV --out FILE
    python -m mycelic.collective.experiments.e1_extract prereg --pack P --labels L --routing R --endpoint A
        --endpoint B --reference B --margin M --runs K --seed S [--bootstrap-b 10000]
        --data-label public|synthetic|partner --boundary site:<id>|central [--allow-external-raw public|synthetic]
        [--allow-dirty] --run-id ID
    python -m mycelic.collective.experiments.e1_extract run --prereg FILE --labels L --routing R --endpoint NAME
        --repeat K --run-id ID [--allow-dirty]
    python -m mycelic.collective.experiments.e1_extract compare --prereg FILE --run-dirs D [D ...] --run-id ID
        [--reference NAME] [--margin M] [--allow-incomplete] [--allow-dirty]

Every subcommand takes ``--runs-dir`` (default ``runs``) and ``--dry-run`` (the common contract). Exit codes: 0 ok;
2 usage, configuration or pinned-value error; 130 run interrupted.

**What is scored.** The extraction output (:class:`~mycelic.collective.edge.extract.TextClaim` s, not ``pair()``
output) against labelled gold. The primary metric is field-level micro F1: each claim contributes its entity
``(type, id)`` and, with a predicate, ``('predicate', p)`` or ``('predicate', 'not:' + p)``; TP, FP and FN are summed
over (record, field, value) across records and runs; F1 = 2TP / (2TP + FP + FN), None when nothing was predicted
or labelled anywhere. Secondary: claim F1 over ``(type, id, predicate, negated)`` tuples with a predicate, entity
F1, predicate F1, the share of records whose first attempt was valid JSON for the schema, and exact match of the
id set per type the pack flags with ``exact_match_metric`` (reported separately, as STRATEGY asks): the share of
records matched in every run, with a Wilson interval over records. A model error on a record is an empty prediction. Confidence intervals bootstrap records (all runs'
counts per record pooled). The paired comparison bootstraps the per-record mean field F1 difference against the
reference over the records both sides have, with an exact sign test.

**What is pinned.** ``prereg`` fixes the pack's vocabulary hash, the labels' sha256, the scoring code's hash
(:data:`E1_CODE_FILES`), every endpoint's model, provider, boundary, response format and transport schema, the
reference, margin, runs, seed, bootstrap B, boundary, data label and external-raw exemption; ``run`` and ``compare``
refuse any difference before writing anything. Each run stamps the sha256 of its predictions and ledger into
run.json, and ``compare`` refuses a run whose files differ, a run that never finished writing run.json, and a
pre-registered endpoint without runs (unless ``--allow-incomplete``, which is stamped and lists it). Raw records go
to an endpoint outside the boundary only with ``--allow-external-raw`` equal to a public or synthetic data label,
so partner data never leaves; public means records from a public source (site ``public``).

**What it does not claim.** ``measurement`` is false whenever a fake was involved (a fake server's header or model
listing). Then the non-inferiority and kill verdicts are withheld (null). No real-model result was produced where
this harness was written; it was rehearsed against local fake servers only.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .. import stats
from ..connectors import openfda
from ..edge.extract import ModelExtractor, codes_channel
from ..inference.client import list_models
from ..inference.ledger import read_ledger
from ..inference.routing import ConfigError, Endpoint, key_problem, load_routing, missing_env, runtime_boundary_ok
from ..inference.runtime import DATA_LABELS, EXTERNAL_RAW_LABELS, Runtime, boundary_mode
from ..jsonio import StrictJsonError, canonical_dumps, sha256_hex, short_digest, strict_load
from ..packs.canonical import Canonicaliser
from ..packs.connector import ConnectorError, map_rows, read_jsonl, record_mapping
from ..packs.loader import FrozenPack, PackError, load_pack
from .common import (ROOT, DryRun, UsageError, check_run_id, code_commit, code_dirty, code_hash, fail,
                     measurement_flag, run_dir, utc_clock, write_json_atomic)

CLI = "e1_extract"
KIND = "e1"
SCHEMA_VERSION = 1
KILL_BELOW = 0.80
UNDERPOWERED_BELOW = 600
MIN_RUNS = 3
MIN_BOOTSTRAP_B = 1000
PRIMARY_METRIC = "field_f1"
SECONDARY_METRICS = ("claim_f1", "entity_f1", "predicate_f1", "json_validity_rate", "exact_match")
METRICS = ("field", "claim", "entity", "predicate")
SOURCES = ("openfda", "jsonl", "records")
OPENFDA_MAPPING = "mapping_openfda"
OPENFDA_SITE = "public"
E1_CODE_FILES = ("mycelic/collective/edge/extract.py", "mycelic/collective/packs/canonical.py",
                 "mycelic/collective/packs/loader.py", "mycelic/collective/packs/connector.py",
                 "mycelic/collective/inference/*.py", "mycelic/collective/schemacheck.py",
                 "mycelic/collective/jsonio.py", "mycelic/collective/stats.py",
                 "mycelic/collective/experiments/e1_extract.py")
PREREG_KEYS = ("kind", "schema_version", "experiment", "run_id", "created_at", "pack", "code_hash", "code_files",
               "code_commit", "code_dirty", "allow_dirty", "labels", "primary_metric", "secondary_metrics", "margin",
               "kill_below", "underpowered_below", "endpoints", "reference", "runs", "seed", "bootstrap_b",
               "boundary", "data_label", "allow_external_raw")
PINNED_ENDPOINT_FIELDS = ("provider", "boundary", "model", "response_format", "transport_schema")
GOLD_ITEM_RE = re.compile(r"(?:(?P<type>[a-z][a-z0-9_]{1,40}):(?P<text>[^@;]+?))?\s*(?:@(?P<neg>!)?"
                          r"(?P<pred>[a-z][a-z0-9_]{1,40}))?", re.ASCII)
NOTES = [
    "Synthetic, public or partner as data_label says; a rehearsal against fake servers has measurement false and "
    "is never quoted.",
    "field_f1 is the primary metric: micro F1 over (record, field, value), fields being each claim's entity and "
    "its predicate (negated predicates as not:<p>). claim_f1, entity_f1 and predicate_f1 are secondary and never "
    "substituted for it.",
    "Confidence intervals are percentile bootstraps over records, with every run's counts for a record pooled.",
    "The paired difference is the per-record mean field F1 of the endpoint minus the reference's, over records "
    "both have; records only one side has are counted in dropped.",
    f"underpowered is true below {UNDERPOWERED_BELOW} paired records; non_inferior needs ci95 low > -margin "
    f"(strict) and kill_flag is set below {KILL_BELOW} absolute field F1 (strict).",
    "exact_match counts records whose gold names at least one id of the type; a record matches when its predicted "
    "id set equals the gold's in every run, so repeats are not counted as independent trials. Each run.json has "
    "that run's own rate.",
]


def e1_code_files() -> list[str]:
    files: set[str] = set()
    for pattern in E1_CODE_FILES:
        files.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern))
    return sorted(files)


def e1_code_hash() -> str:
    return code_hash([ROOT / p for p in e1_code_files()])


def _dirty() -> bool:
    """True when the code is dirty or its state is unknown (no git)."""
    state = code_dirty()
    return state is not False


def _stamps() -> dict[str, Any]:
    return {"code_commit": code_commit(), "code_dirty": code_dirty(), "e1_code_hash": e1_code_hash()}


# --------------------------------------------------------------------------------------------------- metrics

def field_values(claims: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    out = set()
    for c in claims:
        out.add((c["entity_type"], c["entity_id"]))
        if c["predicate"] is not None:
            out.add(("predicate", ("not:" if c["negated"] else "") + c["predicate"]))
    return out


def claim_tuples(claims: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str, bool]]:
    return {(c["entity_type"], c["entity_id"], c["predicate"], bool(c["negated"])) for c in claims
            if c["predicate"] is not None}


def _counts(predicted: set[Any], gold: set[Any]) -> tuple[int, int, int]:
    return len(predicted & gold), len(predicted - gold), len(gold - predicted)


def record_counts(predicted: Sequence[Mapping[str, Any]],
                  gold: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, int, int]]:
    pf, gf = field_values(predicted), field_values(gold)
    return {
        "field": _counts(pf, gf),
        "claim": _counts(claim_tuples(predicted), claim_tuples(gold)),
        "entity": _counts({v for v in pf if v[0] != "predicate"}, {v for v in gf if v[0] != "predicate"}),
        "predicate": _counts({v for v in pf if v[0] == "predicate"}, {v for v in gf if v[0] == "predicate"}),
    }


def record_field_f1(predicted: Sequence[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]]) -> float:
    """Per-record field F1: 1.0 when both sides are empty, 0.0 when exactly one is."""
    pf, gf = field_values(predicted), field_values(gold)
    if not pf and not gf:
        return 1.0
    if not pf or not gf:
        return 0.0
    return stats.f1_from_counts(*_counts(pf, gf))


def micro_f1(counts: Iterable[Sequence[int]]) -> dict[str, Any]:
    tp = fp = fn = 0
    for a, b, c in counts:
        tp, fp, fn = tp + a, fp + b, fn + c
    return {"value": stats.f1_from_counts(tp, fp, fn), "tp": tp, "fp": fp, "fn": fn}


def exact_flags(predicted: Sequence[Mapping[str, Any]], gold: Sequence[Mapping[str, Any]],
                types: Sequence[str]) -> dict[str, bool | None]:
    """Per type: None when the gold names no id of that type, else whether the predicted id set equals the gold's."""
    out = {}
    for t in types:
        want = {c["entity_id"] for c in gold if c["entity_type"] == t}
        out[t] = None if not want else {c["entity_id"] for c in predicted if c["entity_type"] == t} == want
    return out


def exact_summary(records: Iterable[Sequence[Mapping[str, Any]]], types: Sequence[str], *,
                  ci: bool) -> dict[str, Any]:
    """Per type, over records (each given as its per-run flags): ``n`` counts records whose gold names an id of the
    type, ``matches`` those whose predicted id set equalled the gold's in every run. Records are the unit, so the
    Wilson interval does not treat repeats of one record as independent trials."""
    out = {}
    records = [list(r) for r in records]
    for t in types:
        hits = [[f[t] for f in runs if f.get(t) is not None] for runs in records]
        hits = [h for h in hits if h]
        n, matches = len(hits), sum(1 for h in hits if all(h))
        entry: dict[str, Any] = {"n": n, "matches": matches, "rate": matches / n if n else None}
        if ci:
            interval = stats.wilson(matches, n)
            entry["ci_low"], entry["ci_high"] = interval if interval is not None else (None, None)
        out[t] = entry
    return out


def verdicts(ci_low: float | None, margin: float, abs_f1: float | None,
             kill_below: float = KILL_BELOW) -> dict[str, bool | None]:
    return {"non_inferior": None if ci_low is None else ci_low > -margin,
            "kill_flag": None if abs_f1 is None else abs_f1 < kill_below}


def parse_margin(text: str) -> float:
    """``0 < m < 1`` is a fraction; ``1 <= m <= 20`` is points (stored as ``m / 100``)."""
    value = None
    try:
        value = float(text)
    except (TypeError, ValueError):
        value = None
    if value is None or not math.isfinite(value) or not (0 < value < 1 or 1 <= value <= 20):
        raise UsageError("--margin must be a fraction in (0, 1) or points in [1, 20]") from None
    return value if value < 1 else value / 100


# --------------------------------------------------------------------------------------------------- gold cells

def parse_gold_cell(cell: str | None) -> list[dict[str, Any]]:
    """Items ``type:text@pred``, ``type:text@!pred``, ``@pred``, ``type:text``; ``-`` is zero claims. Raises
    ValueError for an empty cell ('unlabelled') or an item that does not parse."""
    cell = (cell or "").strip()
    if not cell:
        raise ValueError("unlabelled") from None
    if cell == "-":
        return []
    items = []
    for n, raw in enumerate(cell.split(";"), start=1):
        item = raw.strip()
        m = GOLD_ITEM_RE.fullmatch(item)
        if not item or m is None or (m.group("type") is None and m.group("pred") is None):
            raise ValueError(f"cannot parse item {n}") from None
        items.append({"entity_type": m.group("type"),
                      "entity_text": m.group("text").strip() if m.group("text") is not None else None,
                      "predicate": m.group("pred"), "negated": m.group("neg") is not None})
    return items


def format_gold_cell(claims: Sequence[Mapping[str, Any]]) -> str:
    """The inverse of :func:`parse_gold_cell` for claims with ``entity_type``, ``entity_text``, ``predicate`` and
    ``negated``; ``entity_text`` None marks a predicate attached to the primary entity (the generator's gold)."""
    if not claims:
        return "-"
    items = []
    for c in claims:
        pred = "" if c["predicate"] is None else "@" + ("!" if c["negated"] else "") + c["predicate"]
        if c["entity_text"] is None:
            if not pred:
                raise ValueError("a claim needs an entity or a predicate") from None
            items.append(pred)
            continue
        text = c["entity_text"]
        if not isinstance(text, str) or not text.strip() or text != text.strip() or "@" in text or ";" in text:
            raise ValueError("entity_text cannot be written in a gold cell") from None
        items.append(f"{c['entity_type']}:{text}{pred}")
    return "; ".join(items)


def resolve_gold(items: Sequence[Mapping[str, Any]], record: Mapping[str, Any], pack: FrozenPack,
                 canonicaliser: Canonicaliser) -> tuple[list[dict[str, Any]], list[str]]:
    gold, problems = [], []
    primary = None
    for item in items:
        pred = item["predicate"]
        if pred is not None and pred not in pack.predicates:
            problems.append(f"unknown predicate {pred}")
            continue
        if item["entity_type"] is None:
            if primary is None:
                primary = codes_channel(record, pack, canonicaliser).primary
            if primary is None:
                problems.append(f"no primary entity for @{pred}")
                continue
            entity = (primary.entity_type, primary.entity_id)
        else:
            t = item["entity_type"]
            if t not in pack.entity_types:
                problems.append(f"unknown entity type {t}")
                continue
            m = canonicaliser.resolve_exact(t, item["entity_text"])
            if m is None:
                problems.append(f"not canonical: {t}:{item['entity_text']}")
                continue
            entity = (t, m.entity_id)
        claim = {"entity_type": entity[0], "entity_id": entity[1], "predicate": pred,
                 "negated": bool(item["negated"]) and pred is not None}
        if claim not in gold:
            gold.append(claim)
    return sorted(gold, key=_gold_key), problems


def _gold_key(c: Mapping[str, Any]) -> tuple[str, str, str, bool]:
    return c["entity_type"], c["entity_id"], c["predicate"] or "", c["negated"]


# --------------------------------------------------------------------------------------------------- files

def _load_pack(ref: str) -> FrozenPack:
    try:
        return load_pack(ref)
    except PackError as err:
        raise UsageError(f"pack {ref}: {err}") from None


def _read_bytes(path: str | Path, what: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {what} {path} ({exc.__class__.__name__})") from None


def _jsonl(data: bytes, what: str) -> list[tuple[int, Any]]:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    out = []
    for number, line in enumerate(data.split(b"\n"), start=1):
        line = line.rstrip(b"\r")
        if not line.strip():
            continue
        try:
            out.append((number, strict_load(line)))
        except StrictJsonError as err:
            raise UsageError(f"{what} line {number}: {err.reason}") from None
    return out


def _records_mapping(records: Sequence[Mapping[str, Any]], pack: FrozenPack, what: str) -> str:
    names = set()
    for i, record in enumerate(records, start=1):
        name = record_mapping(record, pack)
        if name is None:
            raise UsageError(f"{what}: record {i} is not a record of pack {pack.id}") from None
        names.add(name)
    if len(names) > 1:
        raise UsageError(f"{what}: records follow different mappings ({', '.join(sorted(names))})") from None
    return names.pop() if names else "mapping"


def read_records(path: str | Path, pack: FrozenPack) -> tuple[list[dict[str, Any]], str]:
    records = [r for _, r in _jsonl(_read_bytes(path, "records file"), "records file")]
    mapping = _records_mapping(records, pack, "records file")
    refs = [r["record_ref"] for r in records]
    if len(set(refs)) != len(refs):
        raise UsageError("records file: a record_ref appears twice") from None
    return records, mapping


def read_labels(path: str | Path, pack: FrozenPack,
                canonicaliser: Canonicaliser) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], str]:
    """Label lines ``{"record", "gold"}``, re-validated against the pack; returns them and the file's sha256."""
    data = _read_bytes(path, "labels file")
    lines = []
    refs: set[str] = set()
    for number, line in _jsonl(data, "labels file"):
        where = f"labels file line {number}"
        if not isinstance(line, dict) or sorted(line) != ["gold", "record"] or not isinstance(line["gold"], list):
            raise UsageError(f"{where}: must be {{\"record\", \"gold\": [...]}}") from None
        record = line["record"]
        if record_mapping(record, pack) is None:
            raise UsageError(f"{where}: the record is not a record of pack {pack.id}") from None
        if record["record_ref"] in refs:
            raise UsageError(f"{where}: duplicate record_ref") from None
        refs.add(record["record_ref"])
        for g in line["gold"]:
            ok = (isinstance(g, dict) and sorted(g) == ["entity_id", "entity_type", "negated", "predicate"]
                  and isinstance(g["negated"], bool)
                  and (g["predicate"] is None
                       or (isinstance(g["predicate"], str) and g["predicate"] in pack.predicates))
                  and not (g["predicate"] is None and g["negated"]))
            m = canonicaliser.resolve_exact(g["entity_type"], g["entity_id"]) if ok else None
            if m is None or m.entity_id != g["entity_id"] or m.method != "exact":
                raise UsageError(f"{where}: a gold claim does not resolve to a vocabulary id and predicate") from None
        lines.append((record, line["gold"]))
    if not lines:
        raise UsageError("labels file: no labelled records") from None
    return lines, sha256_hex(data)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return (_is_int(v) or isinstance(v, float)) and math.isfinite(v)


def _prereg_problem(doc: Any) -> str | None:
    """The first problem with the parts of a prereg.json that run and compare rely on, else None."""
    if not isinstance(doc, dict) or sorted(doc) != sorted(PREREG_KEYS):
        return "its keys are not those of an e1 prereg.json"
    if doc["kind"] != "e1_prereg" or doc["schema_version"] != SCHEMA_VERSION:
        return "kind or schema_version"
    pack, labels, endpoints = doc["pack"], doc["labels"], doc["endpoints"]
    if not (isinstance(pack, dict) and isinstance(pack.get("ref"), str) and isinstance(pack.get("vocabulary_hash"), str)):
        return "pack"
    if not (isinstance(labels, dict) and isinstance(labels.get("sha256"), str)):
        return "labels"
    if not isinstance(doc["code_hash"], str):
        return "code_hash"
    if not (isinstance(endpoints, list) and endpoints
            and all(isinstance(e, dict) and isinstance(e.get("name"), str)
                    and all(f in e for f in PINNED_ENDPOINT_FIELDS) for e in endpoints)):
        return "endpoints"
    names = [e["name"] for e in endpoints]
    if len(set(names)) != len(names) or doc["reference"] not in names:
        return "endpoint names or reference"
    if not (_is_number(doc["margin"]) and 0 < doc["margin"] < 1 and _is_number(doc["kill_below"])):
        return "margin or kill_below"
    if not (_is_int(doc["runs"]) and doc["runs"] >= MIN_RUNS and _is_int(doc["seed"])
            and _is_int(doc["bootstrap_b"]) and doc["bootstrap_b"] >= MIN_BOOTSTRAP_B):
        return "runs, seed or bootstrap_b"
    if not (isinstance(doc["boundary"], str) and runtime_boundary_ok(doc["boundary"])):
        return "boundary"
    if doc["data_label"] not in DATA_LABELS or doc["allow_external_raw"] not in (None, *EXTERNAL_RAW_LABELS):
        return "data_label or allow_external_raw"
    if doc["allow_external_raw"] is not None and doc["allow_external_raw"] != doc["data_label"]:
        return "allow_external_raw differs from data_label"
    return None


def read_prereg(path: str | Path) -> tuple[dict[str, Any], str]:
    data = _read_bytes(path, "prereg")
    try:
        doc = strict_load(data)
    except StrictJsonError as err:
        raise UsageError(f"prereg {path} is not strict JSON ({err.reason})") from None
    problem = _prereg_problem(doc)
    if problem is not None:
        raise UsageError(f"prereg {path} is not a valid e1 prereg.json ({problem})") from None
    return doc, sha256_hex(data)


def check_data_label(labels: Sequence[tuple[Mapping[str, Any], Any]], data_label: str) -> None:
    """The data label must agree with the labelled records: synthetic exactly when every record is synthetic, and
    public only for records prepared from a public source (site ``public``), so raw partner records cannot be
    relabelled public to reach an external endpoint."""
    synthetic = all(r["synthetic"] for r, _ in labels)
    if synthetic != (data_label == "synthetic"):
        raise UsageError("--data-label must be synthetic exactly when every labelled record is synthetic") from None
    if data_label == "public" and any(r["site"] != OPENFDA_SITE for r, _ in labels):
        raise UsageError(f"--data-label public needs every labelled record to come from a public source (site "
                         f"{OPENFDA_SITE!r}, as prepare --source openfda writes); other records are partner data") \
            from None


def _sheet_header(pack: FrozenPack, mapping: str) -> list[str]:
    types = sorted(pack.mapping(mapping)["entities"])
    return ["row", "record_ref", "language", "received_date", "codes", *(f"entities.{t}" for t in types),
            "narrative", "gold_claims", "labeller_note"]


def sheet_bytes(records: Sequence[Mapping[str, Any]], pack: FrozenPack, mapping: str) -> bytes:
    header = _sheet_header(pack, mapping)
    types = sorted(pack.mapping(mapping)["entities"])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for i, r in enumerate(records, start=1):
        writer.writerow([i, r["record_ref"], r["language"] or "", r["received_date"], ";".join(r["codes"]),
                         *(";".join(r["entities"].get(t, [])) for t in types), r["narrative"], "", ""])
    return buf.getvalue().encode("utf-8")


def read_sheet(path: str | Path) -> tuple[list[str], list[list[str]]]:
    text = None
    try:
        text = _read_bytes(path, "sheet").decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise UsageError("the sheet is not UTF-8") from None
    rows = None
    limit = csv.field_size_limit()
    csv.field_size_limit(max(limit, len(text) + 1))     # prepare writes whole narratives; a field fits in the file
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error:
        rows = None
    finally:
        csv.field_size_limit(limit)
    if not rows:
        raise UsageError("the sheet is not a CSV file with a header") from None
    return rows[0], rows[1:]


# --------------------------------------------------------------------------------------------------- endpoints

def _boundary_check(boundary: str, endpoint: Endpoint, allow_external_raw: str | None) -> None:
    mode = boundary_mode(boundary, endpoint, "raw", simulation=False, allow_external_raw=allow_external_raw)
    if mode == "refused":
        raise UsageError(f"endpoint {endpoint.name} (boundary {endpoint.boundary}) may not receive raw records "
                         f"from boundary {boundary}; external endpoints need --allow-external-raw equal to a public "
                         "or synthetic data label") from None


def _select(config: Any, names: Sequence[str]) -> list[Endpoint]:
    out = []
    for name in names:
        endpoint = config.endpoints.get(name)
        if endpoint is None:
            raise UsageError(f"endpoint {name!r} is not in the routing file") from None
        if endpoint.provider != "openai_compat":
            raise UsageError(f"endpoint {name!r} is not openai_compat: E1 measures real servers only") from None
        out.append(endpoint)
    return out


def _load_routing(path: str, *, check_env: bool) -> Any:
    try:
        return load_routing(path, check_env=check_env)
    except ConfigError as exc:
        raise UsageError(f"routing: {exc}") from None


def _pinned(name: str, pinned: Any, now: Any, source: str = "the prereg") -> None:
    if pinned != now:
        def show(v: Any) -> str:
            if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v):
                return short_digest(v)
            return repr(v)
        raise UsageError(f"{name} differs from {source}: pinned {show(pinned)}, now {show(now)}") from None


def _check_clean(args: argparse.Namespace, dry: DryRun | None) -> None:
    """Dirty code (or no git) needs --allow-dirty; a dry run lists it as a need instead of failing."""
    if not _dirty() or args.allow_dirty:
        return
    if dry is not None:
        dry.need("committed collective code (or --allow-dirty, which is stamped)")
        return
    raise UsageError("the collective code has uncommitted changes (or git is unavailable); commit first or pass "
                     "--allow-dirty, which is stamped") from None


# --------------------------------------------------------------------------------------------------- prepare

def cmd_prepare(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        if args.n < 1:
            raise UsageError("--n must be >= 1") from None
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        pack = _load_pack(args.pack)
        source = args.cache if args.source == "openfda" else args.input
        if source is None:
            raise UsageError("--source openfda needs --cache; jsonl and records need --input") from None
        if args.source == "openfda" and OPENFDA_MAPPING not in pack.mappings:
            raise UsageError(f"pack {pack.id} has no {OPENFDA_MAPPING}.json") from None
        if args.dry_run:
            dry = DryRun(f"{CLI} prepare")
            if not Path(source).exists():
                dry.need(f"{'openFDA cache' if args.source == 'openfda' else 'input file'} {source}")
            elif args.source == "openfda":
                openfda.read_manifest(source)
            for name in ("records.jsonl", "sheet.csv", "prepare.json"):
                dry.write(str(out_dir / name))
            return dry.emit()
        records, rejected, unmapped, data_label, mapping = _prepare_source(args, pack)
    except (UsageError, ConnectorError, openfda.CacheError) as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"cannot read the source ({exc.__class__.__name__})")

    eligible = sorted((r for r in records if r["narrative"].strip()), key=lambda r: r["record_ref"])
    sample = random.Random(f"e1:{args.seed}").sample(eligible, min(args.n, len(eligible)))
    random.Random(f"e1:{args.seed}:order").shuffle(sample)
    records_bytes = "".join(canonical_dumps(r) + "\n" for r in sample).encode("utf-8")
    sheet = sheet_bytes(sample, pack, mapping)
    try:
        out_dir.mkdir(parents=True)
    except OSError as exc:
        return fail(f"cannot create the run directory ({exc.__class__.__name__})")
    (out_dir / "records.jsonl").write_bytes(records_bytes)
    (out_dir / "sheet.csv").write_bytes(sheet)
    doc = {
        "kind": "e1_prepare", "schema_version": SCHEMA_VERSION, "run_id": args.run_id,
        "pack": {"id": pack.id, "version": pack.version, "vocabulary_hash": pack.vocabulary_hash},
        "source": args.source, "mapping": mapping, "data_label": data_label, "measurement": False,
        "n_requested": args.n, "n_written": len(sample), "eligible": len(eligible),
        "shortfall": max(0, args.n - len(eligible)), "rows": len(records) + sum(rejected.values()),
        "rejected": rejected, "unmapped_code_values": unmapped, "records_sha256": sha256_hex(records_bytes),
        "sheet_sha256": sha256_hex(sheet), "seed": args.seed, **_stamps(),
    }
    write_json_atomic(out_dir / "prepare.json", doc)
    print(f"e1 prepare: wrote {len(sample)} of {args.n} requested records to {out_dir} (data_label={data_label})")
    return 0


def _prepare_source(args: argparse.Namespace,
                    pack: FrozenPack) -> tuple[list[dict[str, Any]], dict[str, int], int, str, str]:
    if args.source == "openfda":
        cache = openfda.load_cache(args.cache)
        data_label = cache.manifest.get("data_label")
        if data_label not in ("public", "synthetic"):
            raise UsageError("the openFDA cache manifest has no public or synthetic data_label") from None
        mapped = map_rows([r for _, r in cache.records()], pack, mapping=OPENFDA_MAPPING, site=OPENFDA_SITE,
                          synthetic=data_label == "synthetic")
        return list(mapped.records), mapped.rejected, mapped.unmapped_code_values, data_label, OPENFDA_MAPPING
    if args.source == "jsonl":
        mapped = read_jsonl(args.input, pack, mapping="mapping", site=args.site)
        return list(mapped.records), mapped.rejected, mapped.unmapped_code_values, "partner", "mapping"
    records, mapping = read_records(args.input, pack)
    data_label = "synthetic" if records and all(r["synthetic"] for r in records) else "partner"
    return records, {}, 0, data_label, mapping


# --------------------------------------------------------------------------------------------------- label-check

def cmd_label_check(args: argparse.Namespace) -> int:
    try:
        if Path(args.out).exists():
            raise UsageError(f"--out already exists: {args.out}") from None
        pack = _load_pack(args.pack)
        if args.dry_run:
            dry = DryRun(f"{CLI} label-check")
            for path, what in ((args.records, "records file"), (args.sheet, "labelled sheet")):
                if not Path(path).is_file():
                    dry.need(f"{what} {path}")
            if Path(args.records).is_file():
                read_records(args.records, pack)
            if Path(args.sheet).is_file():
                read_sheet(args.sheet)
            dry.write(args.out)
            return dry.emit()
        records, mapping = read_records(args.records, pack)
        header, rows = read_sheet(args.sheet)
    except UsageError as exc:
        return fail(str(exc))
    expected = _sheet_header(pack, mapping)
    if header != expected:
        return fail(f"the sheet header differs from prepare's: expected {','.join(expected)}")
    canonicaliser = Canonicaliser(pack)
    by_ref = {r["record_ref"]: r for r in records}
    ref_col, gold_col = expected.index("record_ref"), expected.index("gold_claims")
    problems: list[str] = []
    seen: set[str] = set()
    labelled: dict[str, list[dict[str, Any]]] = {}
    for position, row in enumerate(rows, start=1):
        if not any(cell.strip() for cell in row):
            continue
        row = row + [""] * (len(expected) - len(row))
        ref = row[ref_col]
        label = f"row {row[0].strip() if row[0].strip().isdigit() else position} ({ref})"
        if ref in seen:
            problems.append(f"{label}: duplicate record_ref")
            continue
        seen.add(ref)
        if ref not in by_ref:
            problems.append(f"{label}: record_ref not in the records file")
            continue
        try:
            items = parse_gold_cell(row[gold_col])
        except ValueError as exc:
            problems.append(f"{label}: {exc}")
            continue
        gold, issues = resolve_gold(items, by_ref[ref], pack, canonicaliser)
        problems += [f"{label}: {issue}" for issue in issues]
        labelled[ref] = gold
    for ref in sorted(set(by_ref) - seen):
        problems.append(f"row - ({ref}): record missing from the sheet")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return fail(f"{len(problems)} labelling problems")
    data = "".join(canonical_dumps({"record": by_ref[ref], "gold": labelled[ref]}) + "\n"
                   for ref in sorted(labelled)).encode("utf-8")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(data)
    print(f"e1 label-check: {len(labelled)} records, {sum(len(g) for g in labelled.values())} claims; wrote "
          f"{args.out} sha256 {sha256_hex(data)}")
    return 0


# --------------------------------------------------------------------------------------------------- prereg

def cmd_prereg(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        margin = parse_margin(args.margin)
        if args.runs < MIN_RUNS:
            raise UsageError(f"--runs must be >= {MIN_RUNS}") from None
        if args.bootstrap_b < MIN_BOOTSTRAP_B:
            raise UsageError(f"--bootstrap-b must be >= {MIN_BOOTSTRAP_B}") from None
        if not runtime_boundary_ok(args.boundary):
            raise UsageError("--boundary must be central or site:<id>") from None
        names = list(dict.fromkeys(args.endpoint))
        if len(names) < 2:
            raise UsageError("give at least 2 distinct --endpoint values") from None
        if args.reference not in names:
            raise UsageError("reference not among the evaluated endpoints") from None
        if args.allow_external_raw is not None and args.allow_external_raw != args.data_label:
            raise UsageError("--allow-external-raw must equal --data-label (public or synthetic); partner data "
                             "never goes external") from None
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        pack = _load_pack(args.pack)
        canonicaliser = Canonicaliser(pack)
        dry = DryRun(f"{CLI} prereg") if args.dry_run else None
        labels = sha = None
        if Path(args.labels).is_file() or dry is None:
            labels, sha = read_labels(args.labels, pack, canonicaliser)
            check_data_label(labels, args.data_label)
        else:
            dry.need(f"labels file {args.labels}")
        endpoints = None
        if Path(args.routing).is_file() or dry is None:
            endpoints = _select(_load_routing(args.routing, check_env=False), names)
            for endpoint in endpoints:
                _boundary_check(args.boundary, endpoint, args.allow_external_raw)
        else:
            dry.need(f"routing file {args.routing}")
        _check_clean(args, dry)
        if dry is not None:
            dry.write(str(out_dir / "prereg.json"))
            return dry.emit()
    except UsageError as exc:
        return fail(str(exc))
    doc = {
        "kind": "e1_prereg", "schema_version": SCHEMA_VERSION, "experiment": "E1", "run_id": args.run_id,
        "created_at": utc_clock(),
        "pack": {"ref": args.pack, "id": pack.id, "version": pack.version, "vocabulary_hash": pack.vocabulary_hash},
        "code_hash": e1_code_hash(), "code_files": e1_code_files(), "code_commit": code_commit(),
        "code_dirty": code_dirty(), "allow_dirty": bool(args.allow_dirty),
        "labels": {"sha256": sha, "records": len(labels), "claims": sum(len(g) for _, g in labels)},
        "primary_metric": PRIMARY_METRIC, "secondary_metrics": list(SECONDARY_METRICS), "margin": margin,
        "kill_below": KILL_BELOW, "underpowered_below": UNDERPOWERED_BELOW,
        "endpoints": [{"name": e.name, **{f: getattr(e, f) for f in PINNED_ENDPOINT_FIELDS}} for e in endpoints],
        "reference": args.reference, "runs": args.runs, "seed": args.seed, "bootstrap_b": args.bootstrap_b,
        "boundary": args.boundary, "data_label": args.data_label, "allow_external_raw": args.allow_external_raw,
    }
    out_dir.mkdir(parents=True)
    digest = write_json_atomic(out_dir / "prereg.json", doc)
    print(f"e1 prereg: wrote {out_dir / 'prereg.json'} sha256 {digest}")
    return 0


# --------------------------------------------------------------------------------------------------- run

def _check_pinned_endpoint(prereg: Mapping[str, Any], endpoint: Endpoint) -> None:
    pinned = next(e for e in prereg["endpoints"] if e["name"] == endpoint.name)
    for f in PINNED_ENDPOINT_FIELDS:
        _pinned(f"endpoint {endpoint.name} {f}", pinned[f], getattr(endpoint, f))


def _ledger_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    refs: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        refs.setdefault(r["ref"], []).append(r)
    n = len(refs)
    first_ok = sum(1 for rs in refs.values() if any(r["attempt"] == 1 and r["ok"] for r in rs))
    any_ok = sum(1 for rs in refs.values() if any(r["ok"] for r in rs))
    latencies = [r["latency_ms"] for r in rows if r["ok"] and r["attempt"] >= 1 and r["latency_ms"] is not None]
    return {"refs": n, "first_ok": first_ok, "any_ok": any_ok, "latencies": latencies}


def _run_doc(base: Mapping[str, Any], predictions: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
             types: Sequence[str], *, complete: bool, measurement: bool) -> dict[str, Any]:
    ledger = _ledger_summary(rows)
    drops: dict[str, int] = {}
    for p in predictions:
        for k, v in p["drops"].items():
            drops[k] = drops.get(k, 0) + v
    served = sorted({r["model_served"] for r in rows if r["model_served"]})
    metrics = {f"{m}_f1": micro_f1(p["counts"][m] for p in predictions) for m in METRICS}
    metrics.update({
        "json_validity_rate": ledger["first_ok"] / ledger["refs"] if ledger["refs"] else None,
        "valid_after_repair_rate": ledger["any_ok"] / ledger["refs"] if ledger["refs"] else None,
        "exact_match": exact_summary(([p["exact"]] for p in predictions), types, ci=False),
        "latency_ms_p50": stats.percentile(ledger["latencies"], 50),
        "latency_ms_p95": stats.percentile(ledger["latencies"], 95),
        "truncated": sum(1 for p in predictions if p["truncated"]), "drops": dict(sorted(drops.items())),
    })
    return {**base, "complete": complete, "records_done": len(predictions), "measurement": measurement,
            "models_served": served, "model_mismatch": any(m != base["model_requested"] for m in served),
            "metrics": metrics, "finished_at": utc_clock()}


def cmd_run(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        dry = DryRun(f"{CLI} run") if args.dry_run else None
        if not Path(args.prereg).is_file():
            if dry is None:
                raise UsageError(f"prereg missing: {args.prereg}") from None
            dry.need(f"prereg file {args.prereg}")
            for path, what in ((args.labels, "labels file"), (args.routing, "routing file")):
                if not Path(path).is_file():
                    dry.need(f"{what} {path}")
            for name in ("run.json", "predictions.jsonl", "ledger.jsonl"):
                dry.write(str(out_dir / name))
            return dry.emit()
        prereg, prereg_sha = read_prereg(args.prereg)
        names = [e["name"] for e in prereg["endpoints"]]
        if args.endpoint not in names:
            raise UsageError(f"endpoint {args.endpoint!r} is not in the prereg") from None
        if not 1 <= args.repeat <= prereg["runs"]:
            raise UsageError(f"--repeat must be in [1, {prereg['runs']}]") from None
        pack = _load_pack(prereg["pack"]["ref"])
        _pinned("pack vocabulary_hash", prereg["pack"]["vocabulary_hash"], pack.vocabulary_hash)
        _pinned("e1_code_hash", prereg["code_hash"], e1_code_hash())
        canonicaliser = Canonicaliser(pack)
        if not Path(args.labels).is_file() and dry is not None:
            dry.need(f"labels file {args.labels}")
            labels = None
        else:
            labels, sha = read_labels(args.labels, pack, canonicaliser)
            _pinned("labels sha256", prereg["labels"]["sha256"], sha)
            check_data_label(labels, prereg["data_label"])
        endpoint = None
        if not Path(args.routing).is_file() and dry is not None:
            dry.need(f"routing file {args.routing}")
        else:
            config = _load_routing(args.routing, check_env=False)
            endpoint = _select(config, [args.endpoint])[0]
            _check_pinned_endpoint(prereg, endpoint)
            _boundary_check(prereg["boundary"], endpoint, prereg["allow_external_raw"])
            problem = key_problem(endpoint, os.environ) if dry is None else None
            if problem is not None:
                raise UsageError(f"routing: {problem}") from None
        _check_clean(args, dry)
        if dry is not None:
            if endpoint is not None:
                for var in missing_env(config, [endpoint.name]):
                    dry.need(f"env {var}")
                dry.need(f"network {endpoint.host_label} (endpoint {endpoint.name}, a running server)")
            for name in ("run.json", "predictions.jsonl", "ledger.jsonl"):
                dry.write(str(out_dir / name))
            return dry.emit()
        out_dir.mkdir(parents=True)
    except UsageError as exc:
        return fail(str(exc))
    return _execute(args, out_dir, prereg, prereg_sha, pack, canonicaliser, labels, config, endpoint)


def _execute(args: argparse.Namespace, out_dir: Path, prereg: Mapping[str, Any], prereg_sha: str, pack: FrozenPack,
             canonicaliser: Canonicaliser, labels: Sequence[tuple[Mapping[str, Any], list]], config: Any,
             endpoint: Endpoint) -> int:
    types = sorted(t for t, et in pack.entity_types.items() if et.exact_match_metric)
    base = {
        "kind": "e1_run", "schema_version": SCHEMA_VERSION, "run_id": args.run_id, "experiment": "E1",
        "prereg_sha256": prereg_sha, "endpoint": endpoint.name, "repeat": args.repeat,
        "records_total": len(labels),
        "pinned": {"pack_vocabulary_hash": prereg["pack"]["vocabulary_hash"], "code_hash": prereg["code_hash"],
                   "labels_sha256": prereg["labels"]["sha256"],
                   **{f: getattr(endpoint, f) for f in PINNED_ENDPOINT_FIELDS}},
        **_stamps(), "allow_dirty": bool(args.allow_dirty), "data_label": prereg["data_label"],
        "boundary": prereg["boundary"], "allow_external_raw": prereg["allow_external_raw"],
        "model_requested": endpoint.model, "models_listed": None, "listed_fake": None, "started_at": utc_clock(),
    }
    write_json_atomic(out_dir / "run.json", {**base, "complete": False, "records_done": 0, "measurement": False,
                                              "models_served": [], "model_mismatch": False, "metrics": None,
                                              "predictions_sha256": None, "ledger_sha256": None,
                                              "finished_at": None})
    try:
        rt = Runtime(config, boundary=prereg["boundary"], ledger_path=out_dir / "ledger.jsonl", run_id=args.run_id,
                     clock=utc_clock, data_label=prereg["data_label"],
                     allow_external_raw=prereg["allow_external_raw"])
    except ConfigError as exc:
        return fail(str(exc))
    predictions: list[dict[str, Any]] = []
    interrupted = False
    try:
        listed = list_models(endpoint)
        base.update({"models_listed": listed["ids"] if listed["ok"] else None, "listed_fake": listed["fake"]})
        extractor = ModelExtractor(pack, canonicaliser, rt, endpoint=endpoint.name, fallback=False)
        with open(out_dir / "predictions.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for i, (record, gold) in enumerate(labels):
                codes = codes_channel(record, pack, canonicaliser)
                result = extractor.extract(record, codes, ref=f"e1-{args.repeat}-{i:06d}")
                claims = [{"entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
                           "negated": c.negated} for c in result.claims]
                line = {"record_ref": record["record_ref"], "claims": claims, "ok": result.error_kind is None,
                        "error_kind": result.error_kind, "truncated": result.truncated, "drops": dict(result.drops),
                        "counts": record_counts(claims, gold), "exact": exact_flags(claims, gold, types),
                        "field_f1": record_field_f1(claims, gold)}
                fh.write(canonical_dumps(line) + "\n")
                fh.flush()
                predictions.append(line)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        rt.close()
    rows = read_ledger(out_dir / "ledger.jsonl")
    measurement = measurement_flag([endpoint], rows, bool(base["listed_fake"]))
    doc = _run_doc(base, predictions, rows, types, complete=not interrupted, measurement=measurement)
    doc.update({"predictions_sha256": _file_sha(out_dir / "predictions.jsonl"),
                "ledger_sha256": _file_sha(out_dir / "ledger.jsonl")})
    write_json_atomic(out_dir / "run.json", doc)
    if interrupted:
        print(f"e1 run: interrupted after {len(predictions)} records; run.json says complete=false", file=sys.stderr)
        return 130
    print(f"e1 run: {args.endpoint} repeat {args.repeat}: {len(predictions)} records; field_f1 "
          f"{doc['metrics']['field_f1']['value']} (measurement={str(measurement).lower()}); wrote {out_dir}")
    return 0


# --------------------------------------------------------------------------------------------------- compare

def _file_sha(path: Path) -> str | None:
    """sha256 of a run file, None when it does not exist (a run interrupted before its first request has no
    ledger)."""
    return sha256_hex(_read_bytes(path, path.name)) if path.exists() else None


RUN_FIELD_TYPES = (("endpoint", str), ("repeat", int), ("complete", bool), ("measurement", bool),
                   ("models_served", list), ("model_mismatch", bool), ("prereg_sha256", str))


def _run_problem(run: Any) -> str | None:
    if not isinstance(run, dict) or run.get("kind") != "e1_run":
        return "not an e1 run"
    for key, kind in RUN_FIELD_TYPES:
        value = run.get(key)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            return key
    if not all(isinstance(m, str) for m in run["models_served"]):
        return "models_served"
    if not isinstance(run.get("model_requested"), (str, type(None))):
        return "model_requested"
    return None


def _prediction_ok(p: Any) -> bool:
    return (isinstance(p, dict) and isinstance(p.get("record_ref"), str) and _is_number(p.get("field_f1"))
            and isinstance(p.get("exact"), dict) and isinstance(p.get("counts"), dict)
            and all(isinstance(p["counts"].get(m), list) and len(p["counts"][m]) == 3
                    and all(_is_int(x) and x >= 0 for x in p["counts"][m]) for m in METRICS))


def _read_run(directory: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """run.json, its predictions and ledger rows. The two files must match the sha256 run.json stamped when the run
    ended, so an edited prediction or ledger line (or a run that never finished writing) is refused."""
    directory = Path(directory)
    data = _read_bytes(directory / "run.json", "run.json")
    try:
        run = strict_load(data)
    except StrictJsonError as err:
        raise UsageError(f"{directory}/run.json is not strict JSON ({err.reason})") from None
    problem = _run_problem(run)
    if problem is not None:
        raise UsageError(f"{directory}/run.json is not a valid e1 run.json ({problem})") from None
    if run.get("finished_at") is None:
        raise UsageError(f"run {directory} never finished writing run.json (it was killed); run it again") from None
    for name, key in (("predictions.jsonl", "predictions_sha256"), ("ledger.jsonl", "ledger_sha256")):
        _pinned(f"{name} sha256 of run {directory}", run.get(key), _file_sha(directory / name), "its run.json")
    predictions = [p for _, p in _jsonl(_read_bytes(directory / "predictions.jsonl", "predictions"),
                                        f"{directory}/predictions.jsonl")] \
        if (directory / "predictions.jsonl").exists() else []
    if not all(_prediction_ok(p) for p in predictions):
        raise UsageError(f"run {directory}: predictions.jsonl lines are not e1 predictions") from None
    rows = read_ledger(directory / "ledger.jsonl") if (directory / "ledger.jsonl").exists() else []
    return run, predictions, rows


def _endpoint_block(name: str, runs: Sequence[tuple[dict, list, list]], prereg: Mapping[str, Any],
                    types: Sequence[str]) -> tuple[dict[str, Any], dict[str, float]]:
    seed, b = prereg["seed"], prereg["bootstrap_b"]
    pooled: dict[str, dict[str, list[int]]] = {}
    per_record_f1: dict[str, list[float]] = {}
    flags: dict[str, list[Mapping[str, Any]]] = {}
    rows: list[Mapping[str, Any]] = []
    for run, predictions, ledger in runs:
        rows += ledger
        for p in predictions:
            entry = pooled.setdefault(p["record_ref"], {m: [0, 0, 0] for m in METRICS})
            for m in METRICS:
                entry[m] = [x + y for x, y in zip(entry[m], p["counts"][m])]
            per_record_f1.setdefault(p["record_ref"], []).append(p["field_f1"])
            flags.setdefault(p["record_ref"], []).append(p["exact"])
    block: dict[str, Any] = {"model": runs[0][0]["model_requested"], "runs": len(runs)}
    served = sorted({m for run, _, _ in runs for m in run["models_served"]})
    block.update({"models_served": served, "model_mismatch": any(run["model_mismatch"] for run, _, _ in runs)})
    refs = sorted(pooled)
    for m in METRICS:
        key = f"{m}_f1"
        if refs:
            boot = stats.bootstrap_f1([pooled[r][m] for r in refs], B=b, seed=f"e1:{seed}:{name}:{key}")
            block[key] = {"value": boot["f1"], "ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "B": b,
                          "seed": boot["seed"]}
        else:
            block[key] = {"value": None, "ci_low": None, "ci_high": None, "B": b, "seed": f"e1:{seed}:{name}:{key}"}
    ledger = _ledger_summary(rows)
    block["json_validity_rate"] = ledger["first_ok"] / ledger["refs"] if ledger["refs"] else None
    block["valid_after_repair_rate"] = ledger["any_ok"] / ledger["refs"] if ledger["refs"] else None
    block["exact_match"] = exact_summary((flags[r] for r in sorted(flags)), types, ci=True)
    block["latency_ms_p50"] = stats.percentile(ledger["latencies"], 50)
    block["latency_ms_p95"] = stats.percentile(ledger["latencies"], 95)
    per_run = sorted(({"repeat": run["repeat"], "field_f1": micro_f1(p["counts"]["field"] for p in preds)["value"]}
                      for run, preds, _ in runs), key=lambda x: x["repeat"])
    block["per_run"] = per_run
    block["run_sd"] = {"field_f1": stats.sd([r["field_f1"] for r in per_run if r["field_f1"] is not None])}
    means = {ref: math.fsum(v) / len(v) for ref, v in per_record_f1.items()}
    return block, means


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        check_run_id(args.run_id)
        out_dir = run_dir(args.runs_dir, KIND, args.run_id)
        dry = DryRun(f"{CLI} compare") if args.dry_run else None
        missing = [p for p in [args.prereg, *args.run_dirs] if not Path(p).exists()]
        if missing:
            if dry is None:
                raise UsageError(f"missing: {', '.join(missing)}") from None
            for p in missing:
                dry.need(f"{'prereg file' if p == args.prereg else 'run directory'} {p}")
            dry.write(str(out_dir / "e1.json"))
            return dry.emit()
        prereg, prereg_sha = read_prereg(args.prereg)
        if args.reference is not None and args.reference != prereg["reference"]:
            raise UsageError(f"--reference differs from the prereg: pinned {prereg['reference']!r}, "
                             f"now {args.reference!r}") from None
        if args.margin is not None:
            _pinned("margin", prereg["margin"], parse_margin(args.margin))
        names = [e["name"] for e in prereg["endpoints"]]
        by_endpoint: dict[str, list[tuple[dict, list, list]]] = {}
        incomplete = False
        for directory in args.run_dirs:
            run, predictions, rows = _read_run(directory)
            _pinned(f"prereg_sha256 of run {directory}", prereg_sha, run.get("prereg_sha256"))
            if run.get("endpoint") not in names:
                raise UsageError(f"run {directory}: endpoint {run.get('endpoint')!r} is not in the prereg") from None
            if not run.get("complete"):
                if not args.allow_incomplete:
                    raise UsageError(f"run {directory} is incomplete; pass --allow-incomplete, which is stamped") \
                        from None
                incomplete = True
            by_endpoint.setdefault(run["endpoint"], []).append((run, predictions, rows))
        if prereg["reference"] not in by_endpoint:
            raise UsageError("the reference has no runs") from None
        unrun = [n for n in names if n not in by_endpoint]
        if unrun and not args.allow_incomplete:
            raise UsageError(f"pre-registered endpoints without runs: {', '.join(unrun)}; run them, or pass "
                             "--allow-incomplete, which is stamped and lists them in e1.json") from None
        for name, runs in sorted(by_endpoint.items()):
            repeats = [run["repeat"] for run, _, _ in runs]
            if len(set(repeats)) != len(repeats):
                raise UsageError(f"endpoint {name}: a repeat index appears twice") from None
            if sorted(repeats) != list(range(1, prereg["runs"] + 1)):
                raise UsageError(f"endpoint {name}: repeats must be exactly 1..{prereg['runs']}, got "
                                 f"{sorted(repeats)}") from None
        pack = _load_pack(prereg["pack"]["ref"])
        _pinned("pack vocabulary_hash", prereg["pack"]["vocabulary_hash"], pack.vocabulary_hash)
        _pinned("e1_code_hash", prereg["code_hash"], e1_code_hash())
        _check_clean(args, dry)
        if dry is not None:
            dry.write(str(out_dir / "e1.json"))
            return dry.emit()
    except UsageError as exc:
        return fail(str(exc))

    types = sorted(t for t, et in pack.entity_types.items() if et.exact_match_metric)
    measurement = all(run["measurement"] is True for runs in by_endpoint.values() for run, _, _ in runs)
    blocks, means = {}, {}
    for name in sorted(by_endpoint):
        blocks[name], means[name] = _endpoint_block(name, by_endpoint[name], prereg, types)
    reference = prereg["reference"]
    paired = {}
    for name in sorted(by_endpoint):
        if name == reference:
            continue
        shared = sorted(set(means[name]) & set(means[reference]))
        dropped = {"only_model": len(set(means[name]) - set(means[reference])),
                   "only_reference": len(set(means[reference]) - set(means[name]))}
        entry: dict[str, Any] = {"against": reference, "n": len(shared), "dropped": dropped}
        seed = f"e1:{prereg['seed']}:{name}"
        if shared:
            a, r = [means[name][k] for k in shared], [means[reference][k] for k in shared]
            boot = stats.paired_bootstrap(a, r, B=prereg["bootstrap_b"], seed=seed)
            sign = stats.sign_test([x - y for x, y in zip(a, r)])
            entry.update({"mean_diff": boot["mean_diff"], "ci95": [boot["ci_low"], boot["ci_high"]],
                          "B": prereg["bootstrap_b"], "seed": seed, "sign": sign, "sign_p": sign["p_value"]})
        else:
            entry.update({"mean_diff": None, "ci95": [None, None], "B": prereg["bootstrap_b"], "seed": seed,
                          "sign": None, "sign_p": None})
        entry["underpowered"] = len(shared) < UNDERPOWERED_BELOW
        v = verdicts(entry["ci95"][0], prereg["margin"], blocks[name]["field_f1"]["value"])
        entry["non_inferior"] = v["non_inferior"] if measurement else None
        entry["kill_flag"] = v["kill_flag"] if measurement else None
        paired[name] = entry
    doc = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "run_id": args.run_id, "prereg_sha256": prereg_sha,
        "experiment": "E1", "measurement": measurement, "data_label": prereg["data_label"],
        "primary_metric": PRIMARY_METRIC, "margin": prereg["margin"], "kill_below": prereg["kill_below"],
        "reference": reference, **_stamps(), "allow_dirty": bool(args.allow_dirty),
        "allow_incomplete": bool(args.allow_incomplete), "incomplete_runs": incomplete,
        "endpoints_without_runs": unrun,
        "allow_external_raw": prereg["allow_external_raw"], "endpoints": blocks, "paired": paired,
        "verdicts_withheld": None if measurement else "measurement_false", "notes": NOTES,
        "run_dirs": [str(d) for d in args.run_dirs], "created_at": utc_clock(),
    }
    out_dir.mkdir(parents=True)
    write_json_atomic(out_dir / "e1.json", doc)
    print(f"e1 compare: wrote {out_dir / 'e1.json'} (measurement={str(measurement).lower()}, "
          f"data_label={prereg['data_label']})")
    return 0


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.experiments.e1_extract",
                                description="E1: pre-registered extraction F1 of in-boundary models vs a reference.")
    sub = p.add_subparsers(dest="command", required=True)

    def common(s: argparse.ArgumentParser) -> None:
        s.add_argument("--runs-dir", default="runs")
        s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("prepare", help="sample records and write the labelling sheet")
    s.add_argument("--pack", required=True, help="a built-in pack id or a pack directory")
    s.add_argument("--source", required=True, choices=SOURCES)
    s.add_argument("--cache", default=None, help="openFDA cache directory (--source openfda)")
    s.add_argument("--input", default=None, help="vendor JSONL (--source jsonl) or record JSONL (--source records)")
    s.add_argument("--site", default=None, help="site id for --source jsonl when the mapping has no site path")
    s.add_argument("--n", type=int, default=600)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--run-id", required=True)
    common(s)
    s = sub.add_parser("label-check", help="check a labelled sheet and write labels.jsonl")
    s.add_argument("--pack", required=True)
    s.add_argument("--records", required=True, help="records.jsonl written by prepare")
    s.add_argument("--sheet", required=True, help="the labelled sheet.csv")
    s.add_argument("--out", required=True, help="labels.jsonl to create")
    common(s)
    s = sub.add_parser("prereg", help="pin the experiment before any model is run")
    s.add_argument("--pack", required=True)
    s.add_argument("--labels", required=True)
    s.add_argument("--routing", required=True)
    s.add_argument("--endpoint", required=True, action="append", help="repeat for each evaluated endpoint")
    s.add_argument("--reference", required=True, help="the comparator endpoint (one of --endpoint)")
    s.add_argument("--margin", required=True, help="non-inferiority margin: a fraction (0.05) or points (5)")
    s.add_argument("--runs", type=int, required=True)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--bootstrap-b", type=int, default=10000)
    s.add_argument("--data-label", required=True, choices=DATA_LABELS)
    s.add_argument("--boundary", required=True, help="central or site:<id>")
    s.add_argument("--allow-external-raw", default=None, choices=EXTERNAL_RAW_LABELS)
    s.add_argument("--allow-dirty", action="store_true")
    s.add_argument("--run-id", required=True)
    common(s)
    s = sub.add_parser("run", help="one repeat of one endpoint")
    s.add_argument("--prereg", required=True)
    s.add_argument("--labels", required=True)
    s.add_argument("--routing", required=True)
    s.add_argument("--endpoint", required=True)
    s.add_argument("--repeat", type=int, required=True)
    s.add_argument("--run-id", required=True)
    s.add_argument("--allow-dirty", action="store_true")
    common(s)
    s = sub.add_parser("compare", help="score the runs against the prereg and write e1.json")
    s.add_argument("--prereg", required=True)
    s.add_argument("--run-dirs", required=True, nargs="+")
    s.add_argument("--run-id", required=True)
    s.add_argument("--reference", default=None)
    s.add_argument("--margin", default=None)
    s.add_argument("--allow-incomplete", action="store_true")
    s.add_argument("--allow-dirty", action="store_true")
    common(s)
    return p


COMMANDS = {"prepare": cmd_prepare, "label-check": cmd_label_check, "prereg": cmd_prereg, "run": cmd_run,
            "compare": cmd_compare}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
