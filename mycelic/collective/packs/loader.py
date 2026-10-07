"""Load a domain pack: a directory of strict JSON, validated, frozen and hashed.

    python -m mycelic.collective.packs.loader check <pack> [--dry-run]

A pack is ``pack.json``, ``vocabulary.json``, ``codes.json``, ``aliases.json``, ``mapping.json``, an optional
``mapping_openfda.json``, ``egress.json``, ``detectors.json``, ``rules.json``, ``questions.json``,
``followups.json``, ``generator.json`` and ``fixtures/records.jsonl``; ``docs/collective/PACKS.md`` documents
every format. Names starting with ``.`` are ignored; anything else is refused, except plant specs
``fixtures/plant_<name>.json`` (:data:`PLANT_FIXTURE_RE`, G5): the listing accepts them, but the loader never reads
them and no hash covers them; only the evaluation harness reads them (``evaluate/plant.py``).

Every file is read with :func:`~mycelic.collective.jsonio.strict_load`. Every object is closed and every listed
key required. schemacheck cannot express maps with free keys, so map keys are checked here in code against the id
regexes and each map value (and each closed object) is validated with a compiled schemacheck schema; a schema
problem ``(path, keyword)`` becomes ``PackError(file, <absolute path>, keyword)``. Every failure is a
:class:`PackError` naming the pack-relative file and a JSON path; the guarantee comes from explicit checks of type
and shape before each access, never from a catch-all handler.

:func:`load_pack_dir` validates each file, then the cross-references (codes, rules, questions, follow-ups, aliases,
mappings, egress, generator, fixtures), then computes the four hashes over the parsed files and freezes the
result into a :class:`FrozenPack`, which holds only frozen dataclasses, ``MappingProxyType`` (built in sorted key
order), tuples, frozensets, scalars and compiled patterns. The four hashes (:data:`HASH_SCOPES`):

* ``config_hash``: every file but ``generator.json`` and the fixtures, plus the version;
* ``vocabulary_hash``: vocabulary, aliases, codes and the mappings, plus the version (what E1 pins);
* ``detector_hash``: detectors and rules, plus the egress fields detectors read (:data:`DETECTOR_EGRESS_FIELDS`);
* ``fixtures_hash``: ``generator.json`` and the fixture lines.

Each is a sha256 over canonical JSON, so it is invariant to key order, whitespace and CRLF line ends.
"""
from __future__ import annotations

import argparse
import builtins
import json
import keyword
import math
import os
import re
import string
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .. import schemacheck
from ..jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, short_digest, strict_load
from .canonical import (CASES, HARD_SEPS, METHODS, SEGMENT_KINDS, SEP_VALUES, UNRESOLVED_KEYS, Canonicaliser,
                        IdFormat, IdFormatError, ascii_case, check_segments, compile_id_format, folded, is_alnum,
                        split_sentences)
from .connector import RECORD_KEYS, SITE_ID_RE, record_problems, valid_date

BUILTIN_ROOT = Path(__file__).resolve().parent / "data"
CLI = "packs.loader check"

ID_RE = re.compile(r"[a-z][a-z0-9_]{1,40}", re.ASCII)
CODE_ID_RE = re.compile(r"[A-Z][A-Z0-9-]{1,30}", re.ASCII)
ALIAS_ONLY_ID_RE = re.compile(r"[A-Z][A-Z0-9]*(-[A-Z0-9]+)*", re.ASCII)
LANG_RE = re.compile(r"[a-z]{2,3}", re.ASCII)
SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)
VENDOR_FIELD = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
VENDOR_FIELD_RE = re.compile(VENDOR_FIELD, re.ASCII)
PATH_RE = re.compile(rf"{VENDOR_FIELD}(\[\])?(\.{VENDOR_FIELD}(\[\])?)*", re.ASCII)
_SLOT_RE = re.compile(r"\{([^{}]*)\}")

FILES = ("pack.json", "vocabulary.json", "codes.json", "aliases.json", "mapping.json", "mapping_openfda.json",
         "egress.json", "detectors.json", "rules.json", "questions.json", "followups.json", "generator.json")
OPTIONAL_FILES = ("mapping_openfda.json",)
FIXTURES_DIR = "fixtures"
FIXTURES_FILE = "fixtures/records.jsonl"
PLANT_FIXTURE_RE = re.compile(r"plant_[a-z0-9_]{1,40}\.json", re.ASCII)
MAPPING_FILES = MappingProxyType({"mapping": "mapping.json", "mapping_openfda": "mapping_openfda.json"})
DETECTOR_EGRESS_FIELDS = ("k", "suppress_below_days", "count_granularity", "close_lag_days", "min_window_weeks")
HASH_SCOPES = MappingProxyType({
    "config_hash": MappingProxyType({
        "files": ("pack.json", "vocabulary.json", "codes.json", "aliases.json", "mapping.json", "mapping_openfda.json",
                  "egress.json", "detectors.json", "rules.json", "questions.json", "followups.json"),
        "version": True, "egress_fields": (), "fixtures": False}),
    "vocabulary_hash": MappingProxyType({
        "files": ("vocabulary.json", "aliases.json", "codes.json", "mapping.json", "mapping_openfda.json"),
        "version": True, "egress_fields": (), "fixtures": False}),
    "detector_hash": MappingProxyType({
        "files": ("detectors.json", "rules.json"), "version": False, "egress_fields": DETECTOR_EGRESS_FIELDS,
        "fixtures": False}),
    "fixtures_hash": MappingProxyType({
        "files": ("generator.json",), "version": False, "egress_fields": (), "fixtures": True}),
})
MIN_FIXTURES = 40
PLACEHOLDERS = ("window", "predicate_label", "entity_type_label", "entity_id")
TIERS = ("T0", "T1", "T2")
EXECUTOR_FOR_TIER = MappingProxyType({"T0": "packet", "T1": "draft", "T2": "write"})
ARG_KINDS = ("entity_id", "predicate", "conclusion_id", "enum", "integer")
CONCLUSION_ID_PATTERN = "[a-z0-9][a-z0-9_.:-]{0,95}"
SURFACES = ("exact", "lower", "underscore", "en_dash", "fullwidth", "space", "alias")
GEN_KINDS = ("name", "pattern", "choice")
RATES = ("forwarding_rate", "mixed_language_rate", "negated_sentence_rate", "second_predicate_rate",
         "entity_sentence_rate", "zero_claim_rate")
MAX_DRAFT_STRING = 4000

# ----------------------------------------------------------------------------------------- the formats' key names

_PACK_KEYS = ("id", "version", "title", "languages", "illustrative", "disclaimer", "same_author_as_code")
_VOCAB_KEYS = ("entity_types", "predicates", "negation", "negation_window", "extraction")
_ET_KEYS = ("label", "id_format", "separator", "case", "strip_leading_zeros", "exact_match_metric", "egress", "ids")
_PRED_KEYS = ("label", "lexicon")
_NEG_KEYS = ("pre", "post", "terminators")
_EXTRACTION_KEYS = ("max_input_chars", "max_output_tokens", "max_claims", "confidence")
_CONF_KEYS = ("exact", "alias", "variant")
_CODE_KEYS = ("label", "predicate", "specific")
_MAPPING_KEYS = ("record_ref", "site", "received_date", "language", "codes", "entities", "primary_entity_type",
                 "narrative", "persons", "reporter", "origin_ref", "origin_site", "required")
_DATE_KEYS = ("path", "format")
_CODESPEC_KEYS = ("path", "value_map")
_NARRATIVE_KEYS = ("path", "where")
_WHERE_KEYS = ("field", "in")
_EGRESS_KEYS = ("k", "suppress_below_days", "count_granularity", "close_lag_days", "min_window_weeks",
                "verdict_count_buckets", "egress_entity_types", "require_master_data", "never_fields",
                "central_allowed_fields", "verify_max_records", "question_budget_per_entity_per_day")
_DETECTOR_KEYS = ("alert_budget_per_week", "cooldown_weeks", "baseline_weeks", "window_weeks", "min_history_weeks",
                  "burst", "cooccurrence", "resolution", "independence", "decoy", "ranker")
_RANKER_WEIGHTS = ("burst_surprise", "pmi_rise", "log_independent_roots", "supporting_sites", "low_res_conf", "echo",
                   "few_reporters_share", "high_base_rate")
_DETECTOR_NESTED_KEYS = ("alpha_site", "lambda_floor", "p_min", "p_max", "min_sites", "pmi_smoothing", "pmi_delta",
                         "res_conf_min", "echo_min_ratio", "stale_days", "base_rate_site_fraction", "bias", "weights",
                         *_RANKER_WEIGHTS)
_RULE_KEYS = ("label", "entity_type", "predicate", "min_sites", "min_count_per_site", "window_weeks")
_TEMPLATE_KEYS = ("text", "entity_types", "predicates")
_FOLLOWUP_KEYS = ("label", "tier", "enabled", "executor", "args_schema", "draft_schema", "owner_role", "daily_cap",
                  "ack_days", "escalate_to_role")
_ARG_KEYS = MappingProxyType({"entity_id": ("kind", "entity_type"), "predicate": ("kind",), "conclusion_id": ("kind",),
                              "enum": ("kind", "values"), "integer": ("kind", "minimum", "maximum")})
_GENERATOR_KEYS = ("start", "sites", *RATES, "predicate_weights", "codes", "fill_rates", "universe", "links",
                   "surface", "narratives", "entity_sentences", "filler", "persons", "reporter", "master_data")
_SITE_KEYS = ("id", "label", "language", "weekly_volume", "reporters")
_GEN_CODES_KEYS = ("specific_rate", "generic_rate", "generic_code")
_LINK_KEYS = ("parent", "map")
_NARRATIVE_TEMPLATE_KEYS = ("affirmed", "negated")
_GEN_KEYS = MappingProxyType({"name": ("kind", "first", "last"), "pattern": ("kind", "segments", "separator", "case"),
                              "choice": ("kind", "values")})
_FIXTURE_KEYS = ("record", "gold")
_GOLD_KEYS = ("entity_type", "entity_id", "predicate", "negated")
_CLAIM_FIELDS = ("entity_type", "entity_id", "entity_text", "predicate", "negated", "claims", "channel", "extractor",
                 "res_conf")
_ENUM_WORDS = ("confirm", "refute", "unknown", "supported", "hypothesis", "contested", "stale", "codes", "text_only",
               "lexical", "model", "fallback", "packet", "draft", "write", "none", "other", "all", "not")
_FORMAT_WORDS = (
    "pack", "path", "format", "iso", "yyyymmdd", "kind", "values", "minimum", "maximum", "parent", "map", "first",
    "last", "pattern", "segments", "choice", "name", "person", "required", "optional", "upper", "lower", "week",
    "record", "gold", "templates", "types", "roles", "rules", "weights", "lexicon", "aliases", "mapping", "vocabulary",
    "egress", "detectors", "questions", "followups", "generator", "fixtures", "mapping_openfda",
)
# python -S does not install site's builtins (exit, quit, help, ...); list them so RESERVED is the same either way
_SITE_BUILTINS = ("copyright", "credits", "exit", "help", "license", "quit")


def _reserved() -> frozenset[str]:
    words: set[str] = set(_ENUM_WORDS) | set(RECORD_KEYS) | set(_CLAIM_FIELDS) | set(SEGMENT_KINDS) | set(_FORMAT_WORDS)
    for keys in (_PACK_KEYS, _VOCAB_KEYS, _ET_KEYS, _PRED_KEYS, _NEG_KEYS, _EXTRACTION_KEYS, _CONF_KEYS, _CODE_KEYS,
                 _MAPPING_KEYS, _DATE_KEYS, _CODESPEC_KEYS, _NARRATIVE_KEYS, _WHERE_KEYS, _EGRESS_KEYS, _DETECTOR_KEYS,
                 _DETECTOR_NESTED_KEYS, _RULE_KEYS, _TEMPLATE_KEYS, _FOLLOWUP_KEYS, _GENERATOR_KEYS, _SITE_KEYS,
                 _GEN_CODES_KEYS, _LINK_KEYS, _NARRATIVE_TEMPLATE_KEYS, _FIXTURE_KEYS, _GOLD_KEYS, PLACEHOLDERS,
                 SURFACES, GEN_KINDS, ARG_KINDS, SEP_VALUES, CASES, METHODS, UNRESOLVED_KEYS,
                 tuple(EXECUTOR_FOR_TIER.values()), tuple(schemacheck.ALLOWED_KEYWORDS), schemacheck.TYPES,
                 ("entity_sentences", "filler", "universe", "links", "surface", "narratives", "master_data",
                  "fill_rates", "predicate_weights", "sites", "start", "label")):
        words.update(keys)
    for keys in _ARG_KEYS.values():
        words.update(keys)
    for keys in _GEN_KEYS.values():
        words.update(keys)
    words.update(keyword.kwlist)
    words.update(keyword.softkwlist)
    words.update(name for name in dir(builtins) if name == name.lower())
    words.update(_SITE_BUILTINS)
    return frozenset(words)


RESERVED = _reserved()


class PackError(ValueError):
    def __init__(self, file: str, path: str, problem: str) -> None:
        super().__init__(f"{file}: {path}: {problem}")
        self.file = file
        self.path = path
        self.problem = problem


# --------------------------------------------------------------------------------------------------- frozen values

def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(value[k]) for k in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: thaw(value[k]) for k in value}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    if isinstance(value, frozenset):
        return sorted(thaw(v) for v in value)
    return value


@dataclass(frozen=True)
class EntityType:
    id: str
    label: str
    id_format: IdFormat | None
    separator: str | None
    case: str
    strip_leading_zeros: bool
    exact_match_metric: bool
    egress: bool
    ids: tuple[str, ...] | None


@dataclass(frozen=True)
class Predicate:
    id: str
    label: str
    lexicon: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class Negation:
    pre: tuple[str, ...]
    post: tuple[str, ...]
    terminators: tuple[str, ...]


@dataclass(frozen=True)
class ExtractionConfig:
    max_input_chars: int
    max_output_tokens: int
    max_claims: int
    confidence_exact: float
    confidence_alias: float
    confidence_variant: float


@dataclass(frozen=True)
class Code:
    id: str
    label: str
    predicate: str
    specific: bool


@dataclass(frozen=True)
class Egress:
    k: int
    suppress_below_days: int
    count_granularity: str
    close_lag_days: int
    min_window_weeks: int
    verdict_count_buckets: tuple[int, ...]
    egress_entity_types: tuple[str, ...]
    require_master_data: bool
    never_fields: tuple[str, ...]
    central_allowed_fields: tuple[str, ...]
    verify_max_records: int
    question_budget_per_entity_per_day: int


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    entity_type: str
    predicate: str
    min_sites: int
    min_count_per_site: int
    window_weeks: int


@dataclass(frozen=True)
class QuestionTemplate:
    id: str
    text: str
    entity_types: tuple[str, ...]
    predicates: tuple[str, ...] | None


@dataclass(frozen=True)
class Role:
    id: str
    label: str


@dataclass(frozen=True)
class FollowupType:
    """``args`` is the frozen args DSL; ``compiled_args`` the schemacheck schema it compiles to (frozen)."""

    id: str
    label: str
    tier: str
    enabled: bool
    executor: str
    args: Mapping[str, Any]
    draft_schema: Mapping[str, Any] | None
    owner_role: str
    daily_cap: int
    ack_days: int
    escalate_to_role: str | None
    compiled_args: Mapping[str, Any]

    def args_json_schema(self) -> dict[str, Any]:
        return thaw(self.compiled_args)

    def draft_json_schema(self) -> dict[str, Any] | None:
        return None if self.draft_schema is None else thaw(self.draft_schema)


@dataclass(frozen=True)
class GoldClaim:
    entity_type: str
    entity_id: str
    predicate: str | None
    negated: bool


@dataclass(frozen=True)
class Fixture:
    record: Mapping[str, Any]
    gold: tuple[GoldClaim, ...]


@dataclass(frozen=True)
class FrozenPack:
    id: str
    version: str
    title: str
    languages: tuple[str, ...]
    illustrative: bool
    disclaimer: str | None
    same_author_as_code: bool
    source: str
    directory: str
    config_hash: str
    vocabulary_hash: str
    detector_hash: str
    fixtures_hash: str
    entity_types: Mapping[str, EntityType]
    predicates: Mapping[str, Predicate]
    negation: Mapping[str, Negation]
    negation_window: int
    extraction: ExtractionConfig
    codes: Mapping[str, Code]
    aliases: Mapping[str, Mapping[str, str]]
    mappings: Mapping[str, Mapping[str, Any]]
    egress: Egress
    detectors: Mapping[str, Any]
    rules: Mapping[str, Rule]
    questions: Mapping[str, QuestionTemplate]
    roles: Mapping[str, Role]
    followups: Mapping[str, FollowupType]
    generator: Mapping[str, Any]
    fixtures: tuple[Fixture, ...] = field(default=())

    def mapping(self, name: str = "mapping") -> Mapping[str, Any]:
        found = self.mappings.get(name) if isinstance(name, str) else None
        if found is None:
            raise ValueError(f"pack {self.id} has no mapping {name!r}") from None
        return found

    def alias_targets(self) -> Mapping[str, frozenset[str]]:
        return MappingProxyType({t: frozenset(self.aliases.get(t, {}).values()) for t in self.entity_types})

    def hashes(self) -> dict[str, str]:
        return {"config_hash": self.config_hash, "vocabulary_hash": self.vocabulary_hash,
                "detector_hash": self.detector_hash, "fixtures_hash": self.fixtures_hash}


# --------------------------------------------------------------------------------------------------- hashes

def _digest(obj: Any) -> str:
    """sha256 of canonical JSON. Anything canonical JSON refuses (possible only for unvalidated input) is hashed
    from an ASCII-escaped dump, or from its type name, so this never raises."""
    data = None
    try:
        data = canonical_bytes(obj)
    except StrictJsonError:
        data = None
    if data is None:
        try:
            data = b"!" + json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=True,
                                     default=repr).encode("ascii")
        except (TypeError, ValueError, RecursionError):
            data = b"!" + type(obj).__name__.encode("ascii")
    return sha256_hex(data)


def compute_hashes(files: Mapping[str, Any], fixture_lines: Sequence[Any]) -> dict[str, str]:
    """The four pack hashes over parsed files (keyed by file name) and parsed fixture lines. Pure; works on any
    parsed JSON and never raises. An absent optional file is omitted from its scope, not hashed as null."""
    files = files if isinstance(files, Mapping) else {}
    present = {name: _digest(files[name]) for name in sorted(files) if isinstance(name, str)}
    pack = files.get("pack.json")
    version = pack.get("version") if isinstance(pack, dict) else None
    egress = files.get("egress.json")
    egress = egress if isinstance(egress, dict) else {}
    lines = list(fixture_lines) if isinstance(fixture_lines, (list, tuple)) else []
    dumps = []
    for line in lines:
        try:
            dumps.append(canonical_dumps(line))
        except StrictJsonError:
            dumps.append("!" + _digest(line))
    fixtures = sha256_hex("\n".join(dumps))
    out = {}
    for scope, spec in HASH_SCOPES.items():
        obj: dict[str, Any] = {"scope": scope, "files": {n: present[n] for n in spec["files"] if n in present}}
        if spec["version"]:
            obj["version"] = version
        if spec["egress_fields"]:
            obj["egress"] = {f: egress.get(f) for f in spec["egress_fields"]}
        if spec["fixtures"]:
            obj["fixtures"] = fixtures
        out[scope] = _digest(obj)
    return out


# --------------------------------------------------------------------------------------------------- helpers

_SIMPLE_KEY = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)


def _child(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if _SIMPLE_KEY.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def _schema(obj: dict[str, Any]) -> schemacheck.Schema:
    return schemacheck.compile(obj)


def _string(lo: int, hi: int, *, pattern: str | None = None, nullable: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"type": ["string", "null"] if nullable else "string", "minLength": lo, "maxLength": hi}
    if pattern is not None:
        out["pattern"] = pattern
    return out


def _int(lo: int | None = None, hi: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "integer"}
    if lo is not None:
        out["minimum"] = lo
    if hi is not None:
        out["maximum"] = hi
    return out


def _closed(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": props}


_BOOL = {"type": "boolean"}
_NUMBER = {"type": "number"}
_LABEL80 = _string(1, 80)
_LABEL120 = _string(1, 120)
_ID = _string(2, 41, pattern=ID_RE.pattern)
_ID_LIST = {"type": "array", "items": _ID, "maxItems": 200}
_PATH = _string(1, 512, pattern=PATH_RE.pattern)
_PATH_OR_NULL = _string(1, 512, pattern=PATH_RE.pattern, nullable=True)
_STRINGS = {"type": "array", "items": _string(0, 400), "maxItems": 1000}

S_PACK = _schema(_closed({
    "id": _ID, "version": _string(5, 40, pattern=SEMVER_RE.pattern), "title": _LABEL120,
    "languages": {"type": "array", "items": _string(2, 3, pattern=LANG_RE.pattern), "minItems": 1, "maxItems": 8},
    "illustrative": _BOOL, "disclaimer": _string(1, 600, nullable=True), "same_author_as_code": _BOOL}))
S_ET = _schema(_closed({
    "label": _LABEL80, "separator": {"type": ["string", "null"], "enum": ["-", "/", None]},
    "case": {"type": "string", "enum": list(CASES)}, "strip_leading_zeros": _BOOL, "exact_match_metric": _BOOL,
    "egress": _BOOL,
    "ids": {"type": ["array", "null"], "items": _string(1, 40, pattern=ALIAS_ONLY_ID_RE.pattern), "minItems": 1,
            "maxItems": 500}}))
S_LABEL80 = _schema(_LABEL80)
S_LABEL120 = _schema(_LABEL120)
S_TERMS = _schema({"type": "array", "items": _string(1, 200), "maxItems": 200})
S_WINDOW = _schema(_int(1, 8))
S_EXTRACTION = _schema(_closed({
    "max_input_chars": _int(500, 100000), "max_output_tokens": _int(64, 4096), "max_claims": _int(1, 50),
    "confidence": _closed({"exact": _NUMBER, "alias": _NUMBER, "variant": _NUMBER})}))
S_CODE = _schema(_closed({"label": _LABEL120, "predicate": _ID, "specific": _BOOL}))
S_ALIAS = _schema(_string(1, 80))
S_TARGET = _schema(_string(1, 40))
S_PATH = _schema(_PATH)
S_PATH_OR_NULL = _schema(_PATH_OR_NULL)
S_DATE_SPEC = _schema(_closed({"path": _PATH, "format": {"type": "string", "enum": ["iso", "yyyymmdd"]}}))
S_PATH_LIST = _schema({"type": "array", "items": _PATH, "minItems": 1, "maxItems": 20})
S_REQUIRED = _schema({"type": "array", "items": _string(1, 60), "maxItems": 30})
S_VALUE_KEY = _schema(_string(1, 200))
S_CODE_ID = _schema(_string(2, 31, pattern=CODE_ID_RE.pattern))
S_WHERE = _schema(_closed({"field": _string(1, 64, pattern=VENDOR_FIELD_RE.pattern),
                           "in": {"type": "array", "items": _string(1, 200), "minItems": 1, "maxItems": 50}}))
S_EGRESS = _schema(_closed({
    "k": _int(2, 1000), "suppress_below_days": _int(31, 3660),
    "count_granularity": {"type": "string", "enum": ["week"]},
    "close_lag_days": _int(0, 60), "min_window_weeks": _int(5, 104),
    "verdict_count_buckets": {"type": "array", "items": _int(1, 1000000), "minItems": 1, "maxItems": 10},
    "egress_entity_types": _ID_LIST, "require_master_data": _BOOL,
    "never_fields": {"type": "array", "items": _string(1, 120), "maxItems": 200},
    "central_allowed_fields": {"type": "array", "items": _string(1, 120), "maxItems": 200},
    "verify_max_records": _int(1, 10000), "question_budget_per_entity_per_day": _int(1, 100)}))


def _real(lo: float, hi: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "number", "minimum": lo}
    if hi is not None:
        out["maximum"] = hi
    return out


S_DETECTORS = _schema(_closed({
    "alert_budget_per_week": _int(0, 1000), "cooldown_weeks": _int(0, 52), "baseline_weeks": _int(4, 104),
    "window_weeks": _int(1, 26), "min_history_weeks": _int(1, 104),
    "burst": _closed({"alpha_site": _real(0, 0.5), "lambda_floor": _real(0, 1), "p_min": _NUMBER, "p_max": _NUMBER,
                      "min_sites": _int(2, 1000)}),
    "cooccurrence": _closed({"pmi_smoothing": _real(0, 10), "pmi_delta": _real(0, 20), "min_sites": _int(2, 1000)}),
    "resolution": _closed({"res_conf_min": _real(0, 1)}),
    "independence": _closed({"echo_min_ratio": _real(0, 1)}),
    "decoy": _closed({"stale_days": _int(7, 3660), "base_rate_site_fraction": _real(0, 1)}),
    "ranker": _closed({"bias": _NUMBER, "weights": _closed({name: _NUMBER for name in _RANKER_WEIGHTS})})}))
S_RULE = _schema(_closed({"label": _LABEL120, "entity_type": _ID, "predicate": _ID, "min_sites": _int(2, 1000),
                          "min_count_per_site": _int(1, 1000000), "window_weeks": _int(1, 104)}))
S_TEMPLATE = _schema(_closed({"text": _string(1, 400), "entity_types": {"type": "array", "items": _ID, "minItems": 1,
                                                                        "maxItems": 16},
                              "predicates": {"type": ["array", "null"], "items": _ID, "minItems": 1,
                                             "maxItems": 200}}))
S_ROLE = _schema(_closed({"label": _LABEL120}))
S_FOLLOWUP_SCALARS = _schema(_closed({
    "label": _LABEL120, "tier": _string(1, 8), "enabled": _BOOL,
    "executor": {"type": "string", "enum": list(EXECUTOR_FOR_TIER.values())}, "owner_role": _ID,
    "daily_cap": _int(1, 1000), "ack_days": _int(1, 30), "escalate_to_role": _string(2, 41, pattern=ID_RE.pattern,
                                                                                     nullable=True)}))
S_ENUM_VALUES = _schema({"type": "array", "items": _string(1, 64), "minItems": 1, "maxItems": 50})
S_INT = _schema({"type": "integer"})
S_DATE = _schema(_string(10, 10))
S_SITE = _schema(_closed({"id": _string(1, 64, pattern=SITE_ID_RE.pattern), "label": _LABEL80,
                          "language": _string(2, 3, pattern=LANG_RE.pattern),
                          "weekly_volume": {"type": "array", "items": _int(1, 200), "minItems": 2, "maxItems": 2}}))
S_GEN_CODES = _schema(_closed({"specific_rate": _NUMBER, "generic_rate": _NUMBER,
                               "generic_code": _string(2, 31, pattern=CODE_ID_RE.pattern)}))
S_POSITIVE = _schema({"type": "number", "minimum": 0})
S_SURFACE = _schema(_closed({name: {"type": "number", "minimum": 0} for name in SURFACES}))
S_ID_VALUES = _schema({"type": "array", "items": _string(1, 40), "minItems": 1, "maxItems": 1000})
S_TEMPLATES = _schema({"type": "array", "items": _string(1, 400), "minItems": 1, "maxItems": 200})
S_FILLER = _schema({"type": "array", "items": _string(1, 400), "minItems": 5, "maxItems": 200})
S_GEN_NAME = _schema(_closed({"kind": {"type": "string", "enum": ["name"]},
                              "first": {"type": "array", "items": _string(1, 40), "minItems": 1, "maxItems": 500},
                              "last": {"type": "array", "items": _string(1, 40), "minItems": 1, "maxItems": 500}}))
S_GEN_PATTERN = _schema(_closed({"kind": {"type": "string", "enum": ["pattern"]},
                                 "separator": {"type": ["string", "null"], "enum": ["-", "/", " ", None]},
                                 "case": {"type": "string", "enum": list(CASES)}}))
S_GEN_CHOICE = _schema(_closed({"kind": {"type": "string", "enum": ["choice"]},
                                "values": {"type": "array", "items": _string(1, 80), "minItems": 1,
                                           "maxItems": 500}}))
S_GOLD = _schema(_closed({"entity_type": _ID, "entity_id": _string(1, 64),
                          "predicate": _string(2, 41, pattern=ID_RE.pattern, nullable=True), "negated": _BOOL}))


def _check(file: str, path: str, value: Any, schema: schemacheck.Schema) -> None:
    problems = schema.validate(value)
    if problems:
        p, kw = problems[0]
        raise PackError(file, path + p[1:], kw) from None


def _object(file: str, path: str, value: Any, keys: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackError(file, path, "must be an object") from None
    for key in sorted(value):
        if key not in keys:
            raise PackError(file, _child(path, key), "unknown key") from None
    for key in keys:
        if key not in value:
            raise PackError(file, _child(path, key), "missing key") from None
    return value


def _map(file: str, path: str, value: Any, key_re: re.Pattern[str], what: str, *, lo: int = 0,
         hi: int = 1000, reserved: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackError(file, path, "must be an object") from None
    if not lo <= len(value) <= hi:
        raise PackError(file, path, f"must have {lo}..{hi} entries") from None
    for key in sorted(value):
        if key_re.fullmatch(key) is None:
            raise PackError(file, _child(path, key), f"invalid {what}") from None
        if reserved and key in RESERVED:
            raise PackError(file, _child(path, key), "reserved word") from None
    return value


def _ref(file: str, path: str, value: str, known: Mapping[str, Any] | Iterable[str], what: str) -> None:
    if value not in known:
        raise PackError(file, path, f"unknown {what}") from None


def _rate(file: str, path: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PackError(file, path, "must be a number") from None
    if not 0 <= value <= 1:
        raise PackError(file, path, "rate outside [0, 1]") from None
    return value


def _term(file: str, path: str, value: str) -> str:
    term = folded(value)
    if not 1 <= len(term) <= 64 or ";" in term:
        raise PackError(file, path, "a term must fold to 1..64 characters without ';'") from None
    return term


# --------------------------------------------------------------------------------------------------- reading

def _read(directory: Path, name: str) -> Any:
    path = directory / name
    if not path.exists() and not path.is_symlink():
        raise PackError(name, "$", "missing file") from None
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PackError(name, "$", f"cannot read ({exc.__class__.__name__})") from None
    try:
        value = strict_load(data)
    except StrictJsonError as err:
        raise PackError(name, err.path, err.reason) from None
    try:
        canonical_dumps(value)
    except StrictJsonError as err:
        raise PackError(name, "$", err.reason) from None
    return value


def _read_fixtures(directory: Path) -> list[tuple[int, Any]]:
    path = directory / FIXTURES_FILE
    if not path.exists() and not path.is_symlink():
        raise PackError(FIXTURES_FILE, "$", "missing file") from None
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PackError(FIXTURES_FILE, "$", f"cannot read ({exc.__class__.__name__})") from None
    lines = []
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if not raw.strip():
            continue
        try:
            value = strict_load(raw)
            canonical_dumps(value)
        except StrictJsonError as err:
            raise PackError(FIXTURES_FILE, f"line {number} {err.path}", err.reason) from None
        lines.append((number, value))
    return lines


def _listing(directory: Path) -> None:
    if not directory.is_dir():
        raise PackError("pack", "$", f"not a directory: {directory}") from None
    try:
        names = sorted(os.listdir(directory))
        fixture_names = sorted(os.listdir(directory / FIXTURES_DIR)) if (directory / FIXTURES_DIR).is_dir() else []
    except OSError as exc:
        raise PackError("pack", "$", f"cannot list ({exc.__class__.__name__})") from None
    for name in names:
        if name.startswith("."):
            continue
        if name not in FILES and name != FIXTURES_DIR:
            raise PackError(name, "$", "unexpected file") from None
    if FIXTURES_DIR in names and not (directory / FIXTURES_DIR).is_dir():
        raise PackError(FIXTURES_DIR, "$", "must be a directory") from None
    for name in fixture_names:
        if not name.startswith(".") and name != "records.jsonl" and PLANT_FIXTURE_RE.fullmatch(name) is None:
            raise PackError(f"{FIXTURES_DIR}/{name}", "$", "unexpected file") from None


# --------------------------------------------------------------------------------------------------- validation

class _Build:
    """The state of one load: parsed files and what has been validated so far."""

    def __init__(self, directory: Path, files: dict[str, Any], fixture_lines: list[tuple[int, Any]]) -> None:
        self.directory = directory
        self.files = files
        self.fixture_lines = fixture_lines
        self.languages: tuple[str, ...] = ()
        self.entity_types: dict[str, EntityType] = {}
        self.predicates: dict[str, Predicate] = {}
        self.codes: dict[str, Code] = {}
        self.person_fields: set[str] = set()
        self.mapping_persons: set[str] = set()


def _pack_json(b: _Build) -> dict[str, Any]:
    f = "pack.json"
    raw = _object(f, "$", b.files[f], _PACK_KEYS)
    _check(f, "$", raw, S_PACK)
    if raw["id"] in RESERVED:
        raise PackError(f, "$.id", "reserved word") from None
    langs = raw["languages"]
    if len(set(langs)) != len(langs):
        raise PackError(f, "$.languages", "languages must be unique") from None
    if raw["illustrative"] and raw["disclaimer"] is None:
        raise PackError(f, "$.disclaimer", "an illustrative pack needs a disclaimer") from None
    b.languages = tuple(langs)
    return raw


def _vocabulary(b: _Build) -> tuple[dict[str, Negation], int, ExtractionConfig]:
    f = "vocabulary.json"
    raw = _object(f, "$", b.files[f], _VOCAB_KEYS)
    ets = _map(f, "$.entity_types", raw["entity_types"], ID_RE, "entity type id", lo=1, hi=16, reserved=True)
    for t in sorted(ets):
        path = _child("$.entity_types", t)
        et = _object(f, path, ets[t], _ET_KEYS)
        _check(f, path, {k: et[k] for k in _ET_KEYS if k != "id_format"}, S_ET)
        fmt = None
        if (et["id_format"] is None) == (et["ids"] is None):
            raise PackError(f, path, "exactly one of id_format and ids must be null") from None
        if et["ids"] is not None:
            if et["separator"] is not None or et["strip_leading_zeros"]:
                raise PackError(f, path, "an alias-only type has separator null and strip_leading_zeros false") \
                    from None
            if len(set(et["ids"])) != len(et["ids"]):
                raise PackError(f, _child(path, "ids"), "ids must be unique") from None
        else:
            failure = None
            try:
                fmt = compile_id_format(t, et["id_format"], et["case"], et["separator"], et["strip_leading_zeros"])
            except IdFormatError as err:
                failure = (_child(path, "id_format") + ("" if err.index is None else f"[{err.index}]"), err.problem)
            if failure is not None:
                raise PackError(f, *failure) from None
        b.entity_types[t] = EntityType(id=t, label=et["label"], id_format=fmt, separator=et["separator"],
                                       case=et["case"], strip_leading_zeros=et["strip_leading_zeros"],
                                       exact_match_metric=et["exact_match_metric"], egress=et["egress"],
                                       ids=None if et["ids"] is None else tuple(et["ids"]))

    preds = _map(f, "$.predicates", raw["predicates"], ID_RE, "predicate id", lo=1, hi=200, reserved=True)
    seen_terms: dict[str, dict[str, str]] = {}
    for p in sorted(preds):
        path = _child("$.predicates", p)
        pred = _object(f, path, preds[p], _PRED_KEYS)
        _check(f, _child(path, "label"), pred["label"], S_LABEL80)
        lex = _map(f, _child(path, "lexicon"), pred["lexicon"], LANG_RE, "language", hi=8)
        lexicon = {}
        for lang in sorted(lex):
            lpath = _child(_child(path, "lexicon"), lang)
            _ref(f, lpath, lang, b.languages, "language (not in pack.json languages)")
            _check(f, lpath, lex[lang], S_TERMS)
            terms = []
            for i, term in enumerate(lex[lang]):
                t_folded = _term(f, f"{lpath}[{i}]", term)
                owner = seen_terms.setdefault(lang, {}).get(t_folded)
                if owner is not None:
                    raise PackError(f, f"{lpath}[{i}]", f"term already belongs to predicate {owner}") from None
                seen_terms[lang][t_folded] = p
                terms.append(term)
            lexicon[lang] = tuple(terms)
        if not lexicon.get(b.languages[0]):
            raise PackError(f, _child(path, "lexicon"), "needs at least one term in the primary language") from None
        b.predicates[p] = Predicate(id=p, label=pred["label"], lexicon=freeze(lexicon))

    neg_raw = _map(f, "$.negation", raw["negation"], LANG_RE, "language", hi=8)
    negation = {}
    for lang in sorted(neg_raw):
        _ref(f, _child("$.negation", lang), lang, b.languages, "language (not in pack.json languages)")
    for lang in b.languages:
        path = _child("$.negation", lang)
        if lang not in neg_raw:
            raise PackError(f, path, "every pack language needs a negation entry") from None
        neg = _object(f, path, neg_raw[lang], _NEG_KEYS)
        lists = {}
        for key in _NEG_KEYS:
            _check(f, _child(path, key), neg[key], S_TERMS)
            for i, term in enumerate(neg[key]):
                _term(f, f"{_child(path, key)}[{i}]", term)
            lists[key] = tuple(neg[key])
        negation[lang] = Negation(**lists)
    _check(f, "$.negation_window", raw["negation_window"], S_WINDOW)
    ex = raw["extraction"]
    _check(f, "$.extraction", ex, S_EXTRACTION)
    c = ex["confidence"]
    if not 0 < c["variant"] <= c["alias"] <= c["exact"] <= 1:
        raise PackError(f, "$.extraction.confidence", "need 0 < variant <= alias <= exact <= 1") from None
    config = ExtractionConfig(max_input_chars=ex["max_input_chars"], max_output_tokens=ex["max_output_tokens"],
                              max_claims=ex["max_claims"], confidence_exact=c["exact"], confidence_alias=c["alias"],
                              confidence_variant=c["variant"])
    return negation, raw["negation_window"], config


def _codes(b: _Build) -> None:
    f = "codes.json"
    raw = _map(f, "$", b.files[f], CODE_ID_RE, "code id", lo=1, hi=1000)
    for code in sorted(raw):
        path = _child("$", code)
        _object(f, path, raw[code], _CODE_KEYS)
        _check(f, path, raw[code], S_CODE)
        _ref(f, _child(path, "predicate"), raw[code]["predicate"], b.predicates, "predicate")
        b.codes[code] = Code(id=code, label=raw[code]["label"], predicate=raw[code]["predicate"],
                             specific=raw[code]["specific"])
    kinds = {c.specific for c in b.codes.values()}
    if kinds != {True, False}:
        raise PackError(f, "$", "needs at least one specific and one non-specific code") from None


def _canonical(b: _Build, t: str, value: Any) -> bool:
    et = b.entity_types[t]
    if not isinstance(value, str):
        return False
    if et.id_format is None:
        return value in et.ids
    return et.id_format.is_canonical(value)


def _aliases(b: _Build) -> dict[str, dict[str, str]]:
    f = "aliases.json"
    raw = _map(f, "$", b.files[f], ID_RE, "entity type id", hi=16)
    out: dict[str, dict[str, str]] = {}
    for t in sorted(raw):
        path = _child("$", t)
        _ref(f, path, t, b.entity_types, "entity type")
        table = raw[t]
        if not isinstance(table, dict):
            raise PackError(f, path, "must be an object") from None
        normalised: dict[str, str] = {}
        for alias in sorted(table):
            apath = _child(path, alias)
            _check(f, apath, alias, S_ALIAS)
            target = table[alias]
            _check(f, apath, target, S_TARGET)
            norm = folded(alias)
            if not norm:
                raise PackError(f, apath, "alias folds to an empty string") from None
            if not _canonical(b, t, target):
                raise PackError(f, apath, "unknown target (not a canonical id of the type)") from None
            if norm in normalised:
                problem = "duplicate alias" if normalised[norm] == target else "ambiguous alias"
                raise PackError(f, apath, problem) from None
            normalised[norm] = target
            for other in sorted(b.entity_types):
                fmt = b.entity_types[other].id_format
                if fmt is not None and fmt.pattern.fullmatch(ascii_case(norm, fmt.case)) is not None:
                    raise PackError(f, apath, f"alias looks like an id of type {other}") from None
        for alias in sorted(table):
            if folded(table[alias]) in normalised:
                raise PackError(f, _child(path, alias), "alias chain") from None
        out[t] = dict(table)
    return out


def _single_path(f: str, path: str, value: Any, *, nullable: bool) -> None:
    _check(f, path, value, S_PATH_OR_NULL if nullable else S_PATH)
    if value is not None and "[]" in value:
        raise PackError(f, path, "path fans out but the field takes one value") from None


def _mapping_file(b: _Build, name: str) -> dict[str, Any]:
    f = MAPPING_FILES[name]
    raw = _object(f, "$", b.files[f], _MAPPING_KEYS)
    _single_path(f, "$.record_ref", raw["record_ref"], nullable=False)
    _single_path(f, "$.site", raw["site"], nullable=True)
    _object(f, "$.received_date", raw["received_date"], _DATE_KEYS)
    _check(f, "$.received_date", raw["received_date"], S_DATE_SPEC)
    _single_path(f, "$.received_date.path", raw["received_date"]["path"], nullable=False)
    _single_path(f, "$.language", raw["language"], nullable=True)
    if not isinstance(raw["codes"], list) or len(raw["codes"]) > 20:
        raise PackError(f, "$.codes", "must be a list of at most 20 code sources") from None
    for i, spec in enumerate(raw["codes"]):
        path = f"$.codes[{i}]"
        _object(f, path, spec, _CODESPEC_KEYS)
        _check(f, _child(path, "path"), spec["path"], S_PATH)
        vm = spec["value_map"]
        if vm is not None:
            if not isinstance(vm, dict) or not 1 <= len(vm) <= 1000:
                raise PackError(f, _child(path, "value_map"), "must be null or an object of 1..1000 entries") \
                    from None
            for key in sorted(vm):
                vpath = _child(_child(path, "value_map"), key)
                _check(f, vpath, key, S_VALUE_KEY)
                _check(f, vpath, vm[key], S_CODE_ID)
                _ref(f, vpath, vm[key], b.codes, "code")
    ents = _map(f, "$.entities", raw["entities"], ID_RE, "entity type id", lo=1, hi=16)
    for t in sorted(ents):
        path = _child("$.entities", t)
        _ref(f, path, t, b.entity_types, "entity type")
        _check(f, path, ents[t], S_PATH_LIST)
    if not isinstance(raw["primary_entity_type"], str) or raw["primary_entity_type"] not in ents:
        raise PackError(f, "$.primary_entity_type", "must be a key of entities") from None
    if not isinstance(raw["narrative"], list) or not 1 <= len(raw["narrative"]) <= 20:
        raise PackError(f, "$.narrative", "must be a list of 1..20 text sources") from None
    for i, spec in enumerate(raw["narrative"]):
        path = f"$.narrative[{i}]"
        _object(f, path, spec, _NARRATIVE_KEYS)
        _check(f, _child(path, "path"), spec["path"], S_PATH)
        if spec["where"] is not None:
            _object(f, _child(path, "where"), spec["where"], _WHERE_KEYS)
            _check(f, _child(path, "where"), spec["where"], S_WHERE)
            if "[]" not in spec["path"]:
                raise PackError(f, _child(path, "where"), "where needs a fanned-out array in the path") from None
    persons = _map(f, "$.persons", raw["persons"], ID_RE, "person field name", hi=20)
    for p in sorted(persons):
        _single_path(f, _child("$.persons", p), persons[p], nullable=False)
    for key in ("reporter", "origin_ref", "origin_site"):
        _single_path(f, f"$.{key}", raw[key], nullable=True)
    if (raw["origin_ref"] is None) != (raw["origin_site"] is None):
        raise PackError(f, "$.origin_site", "origin_ref and origin_site must be both null or both set") from None
    _check(f, "$.required", raw["required"], S_REQUIRED)
    targets = ("narrative", "codes", "language", "reporter", "site", *(f"entities.{t}" for t in ents))
    if len(set(raw["required"])) != len(raw["required"]):
        raise PackError(f, "$.required", "targets must be unique") from None
    for i, target in enumerate(raw["required"]):
        _ref(f, f"$.required[{i}]", target, targets, "required target")
    b.person_fields.update(persons)
    return raw


def _field_names(b: _Build) -> set[str]:
    return set(RECORD_KEYS) | {f"entities.{t}" for t in b.entity_types} | {f"persons.{p}" for p in b.person_fields}


def _egress(b: _Build) -> Egress:
    f = "egress.json"
    raw = _object(f, "$", b.files[f], _EGRESS_KEYS)
    _check(f, "$", raw, S_EGRESS)
    buckets = raw["verdict_count_buckets"]
    if any(buckets[i] >= buckets[i + 1] for i in range(len(buckets) - 1)):
        raise PackError(f, "$.verdict_count_buckets", "must be strictly ascending") from None
    if buckets[0] != raw["k"]:
        raise PackError(f, "$.verdict_count_buckets[0]", "must equal k") from None
    types = raw["egress_entity_types"]
    if len(set(types)) != len(types):
        raise PackError(f, "$.egress_entity_types", "must be unique") from None
    for i, t in enumerate(types):
        _ref(f, f"$.egress_entity_types[{i}]", t, b.entity_types, "entity type")
    if set(types) != {t for t, et in b.entity_types.items() if et.egress}:
        raise PackError(f, "$.egress_entity_types", "differs from the vocabulary's egress flags") from None
    names = _field_names(b)
    for key in ("never_fields", "central_allowed_fields"):
        if len(set(raw[key])) != len(raw[key]):
            raise PackError(f, f"$.{key}", "must be unique") from None
        for i, name in enumerate(raw[key]):
            _ref(f, f"$.{key}[{i}]", name, names, "field")
    for needed in ("narrative", "reporter", *sorted(f"persons.{p}" for p in b.person_fields)):
        if needed not in raw["never_fields"]:
            raise PackError(f, "$.never_fields", f"must contain {needed}") from None
    for i, name in enumerate(raw["central_allowed_fields"]):
        if name in raw["never_fields"]:
            raise PackError(f, f"$.central_allowed_fields[{i}]", "field in both egress and never lists") from None
    for i, name in enumerate(raw["never_fields"]):
        if name.startswith("entities.") and name[len("entities."):] in types:
            raise PackError(f, f"$.never_fields[{i}]", "an egress entity type cannot be a never field") from None
    return Egress(k=raw["k"], suppress_below_days=raw["suppress_below_days"],
                  count_granularity=raw["count_granularity"], close_lag_days=raw["close_lag_days"],
                  min_window_weeks=raw["min_window_weeks"], verdict_count_buckets=tuple(buckets),
                  egress_entity_types=tuple(types), require_master_data=raw["require_master_data"],
                  never_fields=tuple(raw["never_fields"]), central_allowed_fields=tuple(raw["central_allowed_fields"]),
                  verify_max_records=raw["verify_max_records"],
                  question_budget_per_entity_per_day=raw["question_budget_per_entity_per_day"])


def _detectors(b: _Build, egress: Egress) -> dict[str, Any]:
    """The schema gives types and closed ranges; the open ends and the cross-checks are explicit here."""
    f = "detectors.json"
    raw = b.files[f]
    _check(f, "$", raw, S_DETECTORS)
    for path, value in (("$.burst.alpha_site", raw["burst"]["alpha_site"]),
                        ("$.burst.lambda_floor", raw["burst"]["lambda_floor"]),
                        ("$.cooccurrence.pmi_smoothing", raw["cooccurrence"]["pmi_smoothing"]),
                        ("$.resolution.res_conf_min", raw["resolution"]["res_conf_min"]),
                        ("$.independence.echo_min_ratio", raw["independence"]["echo_min_ratio"]),
                        ("$.decoy.base_rate_site_fraction", raw["decoy"]["base_rate_site_fraction"])):
        if not value > 0:
            raise PackError(f, path, "must be > 0") from None
    if raw["window_weeks"] < egress.min_window_weeks:
        raise PackError(f, "$.window_weeks", "must be at least egress.min_window_weeks") from None
    if not 0 < raw["burst"]["p_min"]:
        raise PackError(f, "$.burst.p_min", "must be > 0") from None
    if not raw["burst"]["p_min"] <= raw["burst"]["p_max"]:
        raise PackError(f, "$.burst.p_max", "must be at least p_min") from None
    if not raw["burst"]["p_max"] < 1:
        raise PackError(f, "$.burst.p_max", "must be < 1") from None
    if raw["decoy"]["stale_days"] < egress.close_lag_days + 7:
        raise PackError(f, "$.decoy.stale_days", "must be at least egress.close_lag_days + 7") from None
    return raw


def _rules(b: _Build, egress: Egress) -> dict[str, Rule]:
    f = "rules.json"
    raw = _object(f, "$", b.files[f], ("rules",))
    rules = _map(f, "$.rules", raw["rules"], ID_RE, "rule id", hi=200, reserved=True)
    out = {}
    for r in sorted(rules):
        path = _child("$.rules", r)
        _object(f, path, rules[r], _RULE_KEYS)
        _check(f, path, rules[r], S_RULE)
        _ref(f, _child(path, "entity_type"), rules[r]["entity_type"], b.entity_types, "entity type")
        _ref(f, _child(path, "predicate"), rules[r]["predicate"], b.predicates, "predicate")
        if rules[r]["window_weeks"] < egress.min_window_weeks:
            raise PackError(f, _child(path, "window_weeks"), "must be at least egress.min_window_weeks") from None
        out[r] = Rule(id=r, **{k: rules[r][k] for k in _RULE_KEYS})
    return out


def _question_text(f: str, path: str, text: str) -> None:
    parsed = None
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        parsed = None
    if parsed is None:
        raise PackError(f, path, "unbalanced braces") from None
    for _, name, spec, conversion in parsed:
        if name is None:
            continue
        if name not in PLACEHOLDERS:
            raise PackError(f, path, "unknown placeholder") from None
        if conversion is not None or spec:
            raise PackError(f, path, "placeholder may not carry a conversion or format spec") from None


def _questions(b: _Build) -> dict[str, QuestionTemplate]:
    f = "questions.json"
    raw = _object(f, "$", b.files[f], ("templates",))
    templates = _map(f, "$.templates", raw["templates"], ID_RE, "template id", hi=200, reserved=True)
    out = {}
    for q in sorted(templates):
        path = _child("$.templates", q)
        tpl = _object(f, path, templates[q], _TEMPLATE_KEYS)
        _check(f, path, tpl, S_TEMPLATE)
        _question_text(f, _child(path, "text"), tpl["text"])
        for i, t in enumerate(tpl["entity_types"]):
            _ref(f, f"{_child(path, 'entity_types')}[{i}]", t, b.entity_types, "entity type")
        for i, p in enumerate(tpl["predicates"] or ()):
            _ref(f, f"{_child(path, 'predicates')}[{i}]", p, b.predicates, "predicate")
        out[q] = QuestionTemplate(id=q, text=tpl["text"], entity_types=tuple(tpl["entity_types"]),
                                  predicates=None if tpl["predicates"] is None else tuple(tpl["predicates"]))
    return out


def _arg_schema(b: _Build, spec: Mapping[str, Any]) -> dict[str, Any]:
    kind = spec["kind"]
    if kind == "entity_id":
        et = b.entity_types[spec["entity_type"]]
        if et.id_format is None:
            return {"type": "string", "enum": list(et.ids), "maxLength": 64}
        return {"type": "string", "pattern": et.id_format.canonical.pattern, "maxLength": 64}
    if kind == "predicate":
        return {"type": "string", "enum": sorted(b.predicates)}
    if kind == "conclusion_id":
        return {"type": "string", "pattern": CONCLUSION_ID_PATTERN, "maxLength": 96}
    if kind == "enum":
        return {"type": "string", "enum": list(spec["values"])}
    return {"type": "integer", "minimum": spec["minimum"], "maximum": spec["maximum"]}


def _draft_strings(node: Any, path: str) -> str | None:
    """Path of the first string node without maxLength (or above the cap), else None."""
    if not isinstance(node, dict):
        return None
    types = node.get("type")
    is_string = types == "string" or (isinstance(types, list) and "string" in types)
    if is_string:
        cap = node.get("maxLength")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap > MAX_DRAFT_STRING:
            return path
    props = node.get("properties")
    if isinstance(props, dict):
        for name in sorted(props):
            found = _draft_strings(props[name], _child(_child(path, "properties"), name))
            if found is not None:
                return found
    if isinstance(node.get("items"), dict):
        return _draft_strings(node["items"], _child(path, "items"))
    return None


def _compiles(f: str, path: str, schema: Any) -> schemacheck.Schema:
    compiled = problem = None
    try:
        compiled = schemacheck.compile(schema)
    except schemacheck.SchemaError as err:
        problem = str(err)
    if compiled is None:
        raise PackError(f, path, problem or "does not compile") from None
    return compiled


def _followups(b: _Build) -> tuple[dict[str, Role], dict[str, FollowupType]]:
    f = "followups.json"
    raw = _object(f, "$", b.files[f], ("roles", "types"))
    roles_raw = _map(f, "$.roles", raw["roles"], ID_RE, "role id", lo=1, hi=50, reserved=True)
    roles = {}
    for r in sorted(roles_raw):
        _object(f, _child("$.roles", r), roles_raw[r], ("label",))
        _check(f, _child("$.roles", r), roles_raw[r], S_ROLE)
        roles[r] = Role(id=r, label=roles_raw[r]["label"])
    types = _map(f, "$.types", raw["types"], ID_RE, "follow-up type id", hi=50, reserved=True)
    out = {}
    for name in sorted(types):
        path = _child("$.types", name)
        ft = _object(f, path, types[name], _FOLLOWUP_KEYS)
        tier = ft["tier"]
        if tier == "T3":
            raise PackError(f, _child(path, "tier"), "T3 is not an action type") from None
        if isinstance(tier, str) and tier not in TIERS:
            raise PackError(f, _child(path, "tier"), "tier must be T0, T1 or T2") from None
        _check(f, path, {k: ft[k] for k in _FOLLOWUP_KEYS if k not in ("args_schema", "draft_schema")},
               S_FOLLOWUP_SCALARS)
        if tier == "T2" and ft["enabled"]:
            raise PackError(f, _child(path, "enabled"), "T2 writes are off") from None
        if EXECUTOR_FOR_TIER[tier] != ft["executor"]:
            raise PackError(f, _child(path, "executor"), "tier and executor do not pair (T0 packet, T1 draft, "
                                                         "T2 write)") from None
        _ref(f, _child(path, "owner_role"), ft["owner_role"], roles, "role")
        if ft["escalate_to_role"] is not None:
            _ref(f, _child(path, "escalate_to_role"), ft["escalate_to_role"], roles, "role")
        args = _map(f, _child(path, "args_schema"), ft["args_schema"], ID_RE, "arg name", hi=20)
        compiled_props = {}
        for arg in sorted(args):
            apath = _child(_child(path, "args_schema"), arg)
            spec = args[arg]
            if not isinstance(spec, dict):
                raise PackError(f, apath, "must be an object") from None
            kind = spec.get("kind")
            if kind not in ARG_KINDS:
                raise PackError(f, _child(apath, "kind"), "free text or unsupported kind") from None
            for key in sorted(spec):
                if key not in _ARG_KEYS[kind]:
                    raise PackError(f, _child(apath, key), "unsupported keyword") from None
            _object(f, apath, spec, _ARG_KEYS[kind])
            if kind == "entity_id":
                if not isinstance(spec["entity_type"], str):
                    raise PackError(f, _child(apath, "entity_type"), "must be a string") from None
                _ref(f, _child(apath, "entity_type"), spec["entity_type"], b.entity_types, "entity type")
            elif kind == "enum":
                _check(f, _child(apath, "values"), spec["values"], S_ENUM_VALUES)
                if len(set(spec["values"])) != len(spec["values"]):
                    raise PackError(f, _child(apath, "values"), "values must be unique") from None
            elif kind == "integer":
                _check(f, _child(apath, "minimum"), spec["minimum"], S_INT)
                _check(f, _child(apath, "maximum"), spec["maximum"], S_INT)
                if spec["minimum"] > spec["maximum"]:
                    raise PackError(f, apath, "minimum above maximum") from None
            compiled_props[arg] = _arg_schema(b, spec)
        compiled = {"type": "object", "additionalProperties": False, "required": sorted(compiled_props),
                    "properties": compiled_props}
        _compiles(f, _child(path, "args_schema"), compiled)
        draft = ft["draft_schema"]
        if (draft is not None) != (ft["executor"] == "draft"):
            raise PackError(f, _child(path, "draft_schema"), "draft_schema is set exactly when executor is draft") \
                from None
        if draft is not None:
            dpath = _child(path, "draft_schema")
            compiled_draft = _compiles(f, dpath, draft)
            if compiled_draft.top_type != "object":
                raise PackError(f, dpath, "the top level must be an object") from None
            missing = _draft_strings(draft, dpath)
            if missing is not None:
                raise PackError(f, missing, "draft_schema string without maxLength") from None
        out[name] = FollowupType(id=name, label=ft["label"], tier=tier, enabled=ft["enabled"],
                                 executor=ft["executor"], args=freeze(args),
                                 draft_schema=None if draft is None else freeze(draft), owner_role=ft["owner_role"],
                                 daily_cap=ft["daily_cap"], ack_days=ft["ack_days"],
                                 escalate_to_role=ft["escalate_to_role"], compiled_args=freeze(compiled))
    return roles, out


# --------------------------------------------------------------------------------------------------- generator.json

def _sentence(f: str, path: str, text: str, b: _Build, *, slots_allowed: bool) -> list[str]:
    """Check a template (or filler) sentence; returns its slot names."""
    slots = []
    for m in _SLOT_RE.finditer(text):
        name = m.group(1)
        if not slots_allowed:
            raise PackError(f, path, "filler may not hold slots") from None
        if name.startswith("person:"):
            if name[len("person:"):] not in b.mapping_persons:
                raise PackError(f, path, "unknown person slot") from None
        elif name not in b.entity_types:
            raise PackError(f, path, "unknown slot") from None
        before = text[m.start() - 1] if m.start() > 0 else ""
        after = text[m.end()] if m.end() < len(text) else ""
        after2 = text[m.end() + 1] if m.end() + 1 < len(text) else ""
        if ((before and (is_alnum(before) or before in "{}")) or (after and (is_alnum(after) or after in "{}"))
                or (after in HARD_SEPS and after2 and is_alnum(after2))):
            raise PackError(f, path, "slot not on a word boundary") from None
        slots.append(name)
    rest = _SLOT_RE.sub("x", text)
    if "{" in rest or "}" in rest:
        raise PackError(f, path, "unbalanced braces") from None
    if len([s for s in slots if not s.startswith("person:")]) != len({s for s in slots if not s.startswith("person:")}):
        raise PackError(f, path, "at most one slot per type") from None
    if not rest.rstrip().endswith((".", "!", "?")) or len(split_sentences(rest)) != 1:
        raise PackError(f, path, "must be a single sentence ending in . ! or ?") from None
    return slots


def _gen_spec(f: str, path: str, raw: Any) -> None:
    if not isinstance(raw, dict):
        raise PackError(f, path, "must be an object") from None
    kind = raw.get("kind")
    if kind not in GEN_KINDS:
        raise PackError(f, _child(path, "kind"), "unknown generator kind (name, pattern or choice)") from None
    _object(f, path, raw, _GEN_KEYS[kind])
    if kind == "name":
        _check(f, path, raw, S_GEN_NAME)
    elif kind == "choice":
        _check(f, path, raw, S_GEN_CHOICE)
    else:
        _check(f, path, {k: raw[k] for k in ("kind", "separator", "case")}, S_GEN_PATTERN)
        failure = None
        try:
            check_segments(raw["segments"], raw["case"], separators=("-", "/", " "), separator=raw["separator"])
        except IdFormatError as err:
            failure = (_child(path, "segments") + ("" if err.index is None else f"[{err.index}]"), err.problem)
        if failure is not None:
            raise PackError(f, *failure) from None


def _generator(b: _Build, mapping: Mapping[str, Any], aliases: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    f = "generator.json"
    raw = _object(f, "$", b.files[f], _GENERATOR_KEYS)
    start = raw["start"]
    _check(f, "$.start", start, S_DATE)
    if not valid_date(start):
        raise PackError(f, "$.start", "must be a calendar date YYYY-MM-DD") from None
    if date.fromisoformat(start).weekday() != 0:
        raise PackError(f, "$.start", "must be a Monday") from None
    if not isinstance(raw["sites"], list) or len(raw["sites"]) < 6:
        raise PackError(f, "$.sites", "needs at least 6 sites") from None
    site_ids = []
    for i, site in enumerate(raw["sites"]):
        path = f"$.sites[{i}]"
        _object(f, path, site, _SITE_KEYS)
        reporters = site["reporters"]
        if isinstance(reporters, bool) or not isinstance(reporters, int) or reporters < 1:
            raise PackError(f, _child(path, "reporters"), "reporter pool < 1") from None
        _check(f, path, {k: site[k] for k in _SITE_KEYS if k != "reporters"}, S_SITE)
        if site["weekly_volume"][0] > site["weekly_volume"][1]:
            raise PackError(f, _child(path, "weekly_volume"), "min above max") from None
        _ref(f, _child(path, "language"), site["language"], b.languages, "language (not in pack.json languages)")
        if site["id"] in site_ids:
            raise PackError(f, _child(path, "id"), "duplicate site id") from None
        site_ids.append(site["id"])
    for key in RATES:
        _rate(f, f"$.{key}", raw[key])
    weights = _map(f, "$.predicate_weights", raw["predicate_weights"], ID_RE, "predicate id", lo=1, hi=200)
    for p in sorted(weights):
        _ref(f, _child("$.predicate_weights", p), p, b.predicates, "predicate")
        _check(f, _child("$.predicate_weights", p), weights[p], S_POSITIVE)
        if weights[p] <= 0:
            raise PackError(f, _child("$.predicate_weights", p), "must be > 0") from None
    codes = _object(f, "$.codes", raw["codes"], _GEN_CODES_KEYS)
    _check(f, "$.codes", codes, S_GEN_CODES)
    _rate(f, "$.codes.specific_rate", codes["specific_rate"])
    _rate(f, "$.codes.generic_rate", codes["generic_rate"])
    if codes["specific_rate"] + codes["generic_rate"] > 1:
        raise PackError(f, "$.codes", "specific_rate + generic_rate must be <= 1") from None
    _ref(f, "$.codes.generic_code", codes["generic_code"], b.codes, "code")
    if b.codes[codes["generic_code"]].specific:
        raise PackError(f, "$.codes.generic_code", "must be a non-specific code") from None
    fill = _map(f, "$.fill_rates", raw["fill_rates"], ID_RE, "entity type id", hi=16)
    for t in sorted(fill):
        _ref(f, _child("$.fill_rates", t), t, mapping["entities"], "entity type (not in mapping.json entities)")
        _rate(f, _child("$.fill_rates", t), fill[t])

    universe = _map(f, "$.universe", raw["universe"], ID_RE, "entity type id", hi=16)
    if set(universe) != set(b.entity_types):
        raise PackError(f, "$.universe", "must list every entity type") from None
    for t in sorted(universe):
        path = _child("$.universe", t)
        _check(f, path, universe[t], S_ID_VALUES)
        if len(set(universe[t])) != len(universe[t]):
            raise PackError(f, path, "ids must be unique") from None
        for i, v in enumerate(universe[t]):
            if not _canonical(b, t, v):
                raise PackError(f, f"{path}[{i}]", "not a canonical id of the type") from None
            if b.entity_types[t].id_format is None and v not in aliases.get(t, {}).values():
                raise PackError(f, f"{path}[{i}]", "an alias-only id needs an alias to be written in text") from None
    links = _map(f, "$.links", raw["links"], ID_RE, "entity type id", hi=16)
    for child in sorted(links):
        path = _child("$.links", child)
        _ref(f, path, child, b.entity_types, "entity type")
        link = _object(f, path, links[child], _LINK_KEYS)
        parent = link["parent"]
        if not isinstance(parent, str) or parent not in b.entity_types or parent == child:
            raise PackError(f, _child(path, "parent"), "must be another entity type") from None
        if parent in links:
            raise PackError(f, _child(path, "parent"), "link chain (a parent may not itself be linked)") from None
        lmap = link["map"]
        if not isinstance(lmap, dict) or set(lmap) != set(universe[parent]):
            raise PackError(f, _child(path, "map"), "must map every universe id of the parent") from None
        for pid in sorted(lmap):
            mpath = _child(_child(path, "map"), pid)
            _check(f, mpath, lmap[pid], S_ID_VALUES)
            for i, cid in enumerate(lmap[pid]):
                if cid not in universe[child]:
                    raise PackError(f, f"{mpath}[{i}]", "not in the universe of the child type") from None
    surface = raw["surface"]
    _check(f, "$.surface", surface, S_SURFACE)
    if sum(surface.values()) <= 0:
        raise PackError(f, "$.surface", "weights must sum to more than 0") from None

    narratives = _map(f, "$.narratives", raw["narratives"], LANG_RE, "language", hi=8)
    with_templates = set()
    for lang in sorted(narratives):
        lpath = _child("$.narratives", lang)
        _ref(f, lpath, lang, b.languages, "language (not in pack.json languages)")
        preds = _map(f, lpath, narratives[lang], ID_RE, "predicate id", hi=200)
        for p in sorted(preds):
            ppath = _child(lpath, p)
            _ref(f, ppath, p, b.predicates, "predicate")
            tpl = _object(f, ppath, preds[p], _NARRATIVE_TEMPLATE_KEYS)
            for key, least in (("affirmed", 2), ("negated", 1)):
                _check(f, _child(ppath, key), tpl[key], S_TEMPLATES)
                if len(tpl[key]) < least:
                    raise PackError(f, _child(ppath, key), f"needs at least {least} templates") from None
                for i, text in enumerate(tpl[key]):
                    _sentence(f, f"{_child(ppath, key)}[{i}]", text, b, slots_allowed=True)
            if p in weights:
                with_templates.add(lang)
    site_langs = sorted({s["language"] for s in raw["sites"]})
    for lang in site_langs:
        if lang not in with_templates:
            raise PackError(f, "$.narratives", f"site language {lang} has no weighted predicate with templates") \
                from None
    if raw["mixed_language_rate"] > 0 and len(with_templates) < 2:
        raise PackError(f, "$.mixed_language_rate", "mixed records need a second language with templates") from None
    ent_sent = _map(f, "$.entity_sentences", raw["entity_sentences"], LANG_RE, "language", hi=8)
    filler = _map(f, "$.filler", raw["filler"], LANG_RE, "language", hi=8)
    for lang in site_langs:
        if lang not in ent_sent:
            raise PackError(f, "$.entity_sentences", f"missing site language {lang}") from None
        if lang not in filler:
            raise PackError(f, "$.filler", f"missing site language {lang}") from None
    for lang in sorted(ent_sent):
        lpath = _child("$.entity_sentences", lang)
        _ref(f, lpath, lang, b.languages, "language (not in pack.json languages)")
        _check(f, lpath, ent_sent[lang], S_TEMPLATES)
        for i, text in enumerate(ent_sent[lang]):
            slots = _sentence(f, f"{lpath}[{i}]", text, b, slots_allowed=True)
            if not any(not s.startswith("person:") for s in slots):
                raise PackError(f, f"{lpath}[{i}]", "an entity sentence needs an entity slot") from None
    for lang in sorted(filler):
        lpath = _child("$.filler", lang)
        _ref(f, lpath, lang, b.languages, "language (not in pack.json languages)")
        _check(f, lpath, filler[lang], S_FILLER)
        for i, text in enumerate(filler[lang]):
            _sentence(f, f"{lpath}[{i}]", text, b, slots_allowed=False)
    persons = _map(f, "$.persons", raw["persons"], ID_RE, "person field name", hi=20)
    if set(persons) != set(mapping["persons"]):
        raise PackError(f, "$.persons", "must give a generator for every person field of mapping.json") from None
    for p in sorted(persons):
        _gen_spec(f, _child("$.persons", p), persons[p])
    _gen_spec(f, "$.reporter", raw["reporter"])
    master = _map(f, "$.master_data", raw["master_data"], SITE_ID_RE, "site id", hi=64)
    for site in sorted(master):
        spath = _child("$.master_data", site)
        _ref(f, spath, site, site_ids, "site")
        per_type = _map(f, spath, master[site], ID_RE, "entity type id", hi=16)
        for t in sorted(per_type):
            tpath = _child(spath, t)
            _ref(f, tpath, t, b.entity_types, "entity type")
            if per_type[t] == "all":
                continue
            _check(f, tpath, per_type[t], S_ID_VALUES)
            for i, v in enumerate(per_type[t]):
                if v not in universe[t]:
                    raise PackError(f, f"{tpath}[{i}]", "not in the universe of the type") from None
    return raw


# --------------------------------------------------------------------------------------------------- fixtures

def _fixtures(b: _Build, pack: FrozenPack) -> tuple[Fixture, ...]:
    f = FIXTURES_FILE
    if len(b.fixture_lines) < MIN_FIXTURES:
        raise PackError(f, "$", f"needs at least {MIN_FIXTURES} fixtures") from None
    canon = Canonicaliser(pack)
    refs: set[str] = set()
    out = []
    for number, line in b.fixture_lines:
        at = f"line {number} $"
        _object(f, at, line, _FIXTURE_KEYS)
        problems = record_problems(line["record"], pack, "mapping")
        if problems:
            raise PackError(f, f"{at}.record", problems[0]) from None
        ref = line["record"]["record_ref"]
        if ref in refs:
            raise PackError(f, f"{at}.record.record_ref", "duplicate record_ref") from None
        refs.add(ref)
        if not isinstance(line["gold"], list):
            raise PackError(f, f"{at}.gold", "must be a list") from None
        gold = []
        for i, g in enumerate(line["gold"]):
            gpath = f"{at}.gold[{i}]"
            _object(f, gpath, g, _GOLD_KEYS)
            _check(f, gpath, g, S_GOLD)
            _ref(f, f"{gpath}.entity_type", g["entity_type"], pack.entity_types, "entity type")
            m = canon.resolve_exact(g["entity_type"], g["entity_id"])
            if m is None or m.entity_id != g["entity_id"] or m.method != "exact":
                raise PackError(f, f"{gpath}.entity_id", "gold id is not canonical") from None
            if g["predicate"] is not None:
                _ref(f, f"{gpath}.predicate", g["predicate"], pack.predicates, "predicate")
            elif g["negated"]:
                raise PackError(f, f"{gpath}.negated", "an entity-only claim cannot be negated") from None
            claim = GoldClaim(entity_type=g["entity_type"], entity_id=g["entity_id"], predicate=g["predicate"],
                              negated=g["negated"])
            if claim in gold:
                raise PackError(f, gpath, "duplicate gold claim") from None
            gold.append(claim)
        out.append(Fixture(record=freeze(line["record"]), gold=tuple(gold)))
    return tuple(out)


# --------------------------------------------------------------------------------------------------- load

def load_pack_dir(directory: str | os.PathLike[str], *, expected_id: str | None = None) -> FrozenPack:
    directory = Path(directory)
    _listing(directory)
    files: dict[str, Any] = {}
    for name in FILES:
        if name in OPTIONAL_FILES and not (directory / name).exists() and not (directory / name).is_symlink():
            continue                                   # absent; a dangling symlink is read and fails as unreadable
        files[name] = _read(directory, name)
    fixture_lines = _read_fixtures(directory)
    b = _Build(directory, files, fixture_lines)

    meta = _pack_json(b)
    if expected_id is not None and meta["id"] != expected_id:
        raise PackError("pack.json", "$.id", "pack id differs from directory name") from None
    negation, window, extraction = _vocabulary(b)
    _codes(b)
    aliases = _aliases(b)
    mappings = {name: _mapping_file(b, name) for name in sorted(MAPPING_FILES) if MAPPING_FILES[name] in files}
    b.mapping_persons = set(mappings["mapping"]["persons"])
    egress = _egress(b)
    detectors = _detectors(b, egress)
    rules = _rules(b, egress)
    questions = _questions(b)
    roles, followups = _followups(b)
    generator = _generator(b, mappings["mapping"], aliases)
    hashes = compute_hashes(files, [line for _, line in fixture_lines])
    resolved = directory.resolve()
    source = "builtin" if expected_id is not None and resolved == (BUILTIN_ROOT / expected_id).resolve() else "path"
    pack = FrozenPack(
        id=meta["id"], version=meta["version"], title=meta["title"], languages=tuple(meta["languages"]),
        illustrative=meta["illustrative"], disclaimer=meta["disclaimer"],
        same_author_as_code=meta["same_author_as_code"], source=source, directory=str(resolved), **hashes,
        entity_types=MappingProxyType({t: b.entity_types[t] for t in sorted(b.entity_types)}),
        predicates=MappingProxyType({p: b.predicates[p] for p in sorted(b.predicates)}),
        negation=MappingProxyType({lang: negation[lang] for lang in sorted(negation)}),
        negation_window=window, extraction=extraction,
        codes=MappingProxyType({c: b.codes[c] for c in sorted(b.codes)}),
        aliases=freeze(aliases), mappings=freeze(mappings), egress=egress, detectors=freeze(detectors),
        rules=MappingProxyType({r: rules[r] for r in sorted(rules)}),
        questions=MappingProxyType({q: questions[q] for q in sorted(questions)}),
        roles=MappingProxyType({r: roles[r] for r in sorted(roles)}),
        followups=MappingProxyType({x: followups[x] for x in sorted(followups)}),
        generator=freeze(generator),
    )
    return replace(pack, fixtures=_fixtures(b, pack))


def load_pack(ref: str | os.PathLike[str]) -> FrozenPack:
    """A built-in pack by id (a str matching the id regex with no path separator), else a pack directory path."""
    if isinstance(ref, str) and ID_RE.fullmatch(ref) and "/" not in ref and os.sep not in ref:
        directory = BUILTIN_ROOT / ref
        if not directory.is_dir():
            raise PackError("pack", "$", f"unknown built-in pack {ref}") from None
        return load_pack_dir(directory, expected_id=ref)
    return load_pack_dir(ref)


def is_builtin_ref(ref: str) -> bool:
    return bool(ID_RE.fullmatch(ref)) and "/" not in ref and os.sep not in ref


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m mycelic.collective.packs.loader",
                                description="Validate a domain pack and print its frozen hashes.")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="load a pack (a built-in id or a directory) and print its hashes")
    c.add_argument("pack", help="a built-in pack id or a pack directory")
    c.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run:
        print(f"dry-run: {CLI}")
        if not is_builtin_ref(args.pack) and not Path(args.pack).exists():
            print(f"would need: pack {args.pack}")
            return 0
    try:
        pack = load_pack(args.pack)
    except PackError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    if args.dry_run:
        return 0
    print(f"pack: {pack.id} {pack.version} (source {pack.source})")
    for name, value in pack.hashes().items():
        print(f"{name}: {value} ({short_digest(value)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
