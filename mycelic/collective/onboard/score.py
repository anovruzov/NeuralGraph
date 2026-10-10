"""One arm of a drafting test (D001, or D002, which changes only rules 1.1 and 9): per company, the draft, its
checks, the controls, the held-out sample, reading and metrics.

    python -m mycelic.collective.onboard score --settings FILE --arm NAME --exports DIR --out DIR

Rules 2 to 6 of ``docs/collective/onboard/CHOICE-D001.md``:

* **Companies** come from ``<exports>/companies.json`` (``{label: {"file": name, ...counts}}``, written by a download
  script), in label order. Nothing else names a company.
* **Per company:** the roles file (from the settings), the draft (:mod:`.draft`), the privacy floor and the loader's
  check (:mod:`.check`), the controls (rule 4), the held-out sample (rule 3, :func:`draw_sample`), every reader's
  predictions, and the metrics.
* **Reading** (rule 3, :func:`read_records`): the record goes through the reader's mapping with its category column
  removed and its codes emptied; its only structured entity is the scope (a hand pack's own entity for a hand pack).
  The prediction is the predicates of its affirmed text claims, as ``pair`` keeps them, less the other bucket.
* **Metrics** (rule 5, :func:`reader_metrics`): micro precision, recall and F1 with ``stats.bootstrap_f1``; macro F1
  over (company, predicate) pairs with gold, by the same draw rule (:func:`bootstrap_macro`); coverage; differences
  by ``stats.paired_bootstrap_f1``; all with seed ``<prefix>:<arm>`` and B from the settings, so every reader gets the
  same draws.
* **The matched space** (rule 2.2, :func:`matched_space`, the gold by :func:`matched_gold` as amended in A7) when the
  arm names hand packs; the criterion of rule 6 the arm names (:func:`criterion`), with its deciding values unrounded
  under ``exact`` beside the comparisons it makes.

Per-record data (gold, predictions) stays in memory. ``arm.json`` holds aggregates, category labels that passed the
floor and the first ten terms of each predicate; floats are rounded to four places, except under an ``exact`` key. A
company whose pack failed the privacy floor keeps none of those strings (amendment A5, :func:`withhold_strings`).
What the refusal removed is counted per company and summed beside the criterion (amendment A14,
:func:`refusal_totals`); the label-names control goes through the same refusal as the drafted pack (A13).
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .. import stats
from ..edge.extract import LexicalExtractor, sense
from ..jsonio import strict_load
from ..packs.canonical import Canonicaliser, find_bounded, folded
from ..packs.connector import SITE_ID_RE, map_rows
from ..packs.loader import FrozenPack, PackError, load_pack_dir
from ..pilot.audit import set_path
from .check import check_pack, term_presence
from .draft import (Draft, DraftError, Window, _cells, assign_terms, draft_export, label_name_lexicon, label_word_lexicon,
                    label_words, load_language, load_template, load_written, parse_window, permuted_labels,
                    record_labels, refused_label_parts, refused_label_words, set_prior, slug, with_non_specific,
                    with_placeholders, write_pack)
from .exports import Export, ExportError, read_export
from .roles import Roles, RolesError, roles_from_json

ROOT = Path(__file__).resolve().parents[3]
READERS = ("drafted", "majority_prior", "permuted_labels", "label_names")
CONTROLS = READERS[1:]
# D003 (CHOICE-D003.md): the record-blind readers of C2's space (E2), the reader-guard counts (E1, P10) and the
# record subsets the reported comparisons are repeated on (E7, P6)
SPACE_CONTROLS = ("all_reached", "most_frequent_reached")
GUARD_COUNTS = ("rejected", "no_primary", "structured_unresolved", "no_entity")
SUBSETS = ("no_label_echo", "no_word_echo", "no_masked_repeat")
_DIGIT_RUN = re.compile(r"\d+")
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
EXPERIMENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}", re.ASCII)
DIGITS = 4
EXACT = "exact"


class ScoreError(ValueError):
    """Bad settings, a missing companies file or a company label that is not a plain name."""


# --------------------------------------------------------------------------------------------------- settings

def load_settings(path: str | Path) -> tuple[dict[str, Any], str]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ScoreError(f"cannot read {path} ({exc.__class__.__name__})") from None
    return strict_load(data), hashlib.sha256(data).hexdigest()


def experiment_id(settings: Mapping[str, Any]) -> str:
    """The settings' experiment id (``D001``, ``D002``): every printed artifact takes it from here. A plain name, so
    that it can sit in a printed marker."""
    value = settings.get("experiment")
    if not isinstance(value, str) or EXPERIMENT_RE.fullmatch(value) is None:
        raise ScoreError("the settings name no plain experiment id")
    return value


def arm_roles(settings: Mapping[str, Any], arm: str) -> Roles:
    return roles_from_json({"language": settings["language"], "roles": settings["arms"][arm]["roles"]})


def criterion_specs(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """D003's change P1: an arm's ``criterion`` is one criterion (D001, D002) or a list of them, each computed as
    D002 computes its one."""
    c = spec["criterion"]
    return list(c) if isinstance(c, list) else [c]


def criterion_spec(spec: Mapping[str, Any], cid: str) -> Mapping[str, Any] | None:
    return next((c for c in criterion_specs(spec) if c["id"] == cid), None)


def arm_controls(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """C1's controls: D002's three, or the list C1's settings give (D003's E6 and P9 add the set prior and the
    label words)."""
    c1 = criterion_spec(spec, "C1")
    return tuple(c1["controls"]) if c1 is not None and c1.get("controls") else CONTROLS


def reach_spec(spec: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The C2 of D003's E2 (P7): scored in the hand predicates the drafted names reach, against record-blind readers
    too. None for D002's C2 (rule 2.2 with A7) and for an arm without C2."""
    c2 = criterion_spec(spec, "C2")
    return c2 if c2 is not None and c2.get("space") == "reach" else None


def cluster_on(spec: Mapping[str, Any]) -> bool:
    """D003's change P8: a criterion decided by the interval over (company, received day) clusters (E3)."""
    return any(c.get("interval") == "cluster" for c in criterion_specs(spec))


def reported(spec: Mapping[str, Any], key: str) -> Any:
    """One of D003's reported measures the arm's settings switch on (P6, P10, P11); None when off."""
    return (spec.get("reported") or {}).get(key)


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_companies(exports: Path) -> dict[str, dict[str, Any]]:
    path = exports / "companies.json"
    if not path.is_file():
        raise ScoreError(f"no companies file at {path}")
    companies = strict_load(path.read_bytes())
    if not isinstance(companies, dict):
        raise ScoreError("companies.json must be an object")
    for label, entry in companies.items():
        if LABEL_RE.fullmatch(label) is None or not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ScoreError("companies.json: each entry is {label: {\"file\": name, ...}} with a plain label")
        if Path(entry["file"]).name != entry["file"]:
            raise ScoreError("companies.json: a file is a plain name inside the exports directory")
    return {k: companies[k] for k in sorted(companies)}


def tree_hashes(rel_dirs: Iterable[str], root: Path = ROOT) -> dict[str, str]:
    """sha256 of every file under each directory (``__pycache__`` aside), by repository-relative path."""
    out = {}
    for rel in rel_dirs:
        base = root / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def rounded(value: Any, digits: int = DIGITS) -> Any:
    """Floats rounded to ``digits`` places, except the values under an ``exact`` key: a criterion's deciding values,
    printed as the comparison saw them."""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: (v if k == EXACT else rounded(v, digits)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v, digits) for v in value]
    return value


def write_json(path: Path, obj: Any) -> str:
    data = (json.dumps(rounded(obj), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------------- rule 3

@dataclass(frozen=True)
class Sampled:
    company: str
    row: int
    ref: str
    narrative: str
    gold: frozenset[str]
    names: tuple[str, ...]


def draw_sample(export: Export, d: Draft, test: Window, seed: str, size: int,
                company: str) -> tuple[list[Sampled], dict[str, int]]:
    """Rule 3: the test-window records with a narrative; less those whose folded narrative equals a training
    record's; of equal folded narratives only the smallest record id; then those with a filed specific predicate.
    ``size`` of them (all when fewer) by ``random.Random(seed).sample`` from the eligible sorted by record id."""
    roles = d.roles
    counts = dict.fromkeys(("test_rows", "with_narrative", "no_record_id", "repeated_record_id", "in_training",
                            "repeated_in_test", "no_specific", "eligible", "drawn"), 0)
    training = {folded(n) for n in d.corpus.narratives}
    by_ref: dict[str, tuple[int, str]] = {}
    for r, day in enumerate(d.dated.dates):
        if not test.contains(day):
            continue
        counts["test_rows"] += 1
        narrative = export.value(r, roles.narrative)
        if narrative is None:
            continue
        counts["with_narrative"] += 1
        ref = export.value(r, roles.record_id)
        if ref is None:
            counts["no_record_id"] += 1
            continue
        if ref in by_ref:
            counts["repeated_record_id"] += 1
            continue
        by_ref[ref] = (r, narrative)
    first_of: dict[str, str] = {}
    for ref in sorted(by_ref):
        key = folded(by_ref[ref][1])
        if key in training:
            counts["in_training"] += 1
        elif key in first_of:
            counts["repeated_in_test"] += 1
        else:
            first_of[key] = ref
    eligible = []
    for ref in sorted(first_of.values()):
        r, narrative = by_ref[ref]
        names = _cells(export.value(r, roles.category))
        gold = frozenset(d.plan.of_key[k] for k in (folded(v) for v in names) if k in d.plan.of_key)
        if not gold:
            counts["no_specific"] += 1
            continue
        eligible.append(Sampled(company=company, row=r, ref=ref, narrative=narrative, gold=gold, names=names))
    counts["eligible"] = len(eligible)
    drawn = random.Random(seed).sample(eligible, min(size, len(eligible)))
    counts["drawn"] = len(drawn)
    return drawn, counts


def label_in_text(sample: Sequence[Sampled], plan: Any) -> int:
    """How many sampled records hold one of their own filed specific labels in their narrative, folded and
    word-bounded."""
    n = 0
    for s in sample:
        text = folded(s.narrative)
        keys = {folded(v) for v in s.names if folded(v) in plan.of_key}
        if any(find_bounded(k, text) for k in keys):
            n += 1
    return n


# --------------------------------------------------------------------------------------------------- reading

def drafted_rows(sample: Sequence[Sampled], export: Export, d: Draft) -> list[dict[str, Any]]:
    """What a drafted reader reads: the normalised form without the category values (rule 3). A site outside the
    pipeline's pattern is replaced by a fixed one: the reader reads only the narrative."""
    ids = d.template["ids.json"]
    keys = ids["normalised"]
    scope_id = next(iter(d.template["vocabulary_base.json"]["entity_types"].values()))["ids"][0]
    rows = []
    for s in sample:
        site = export.value(s.row, d.roles.site)
        site = site.lower() if site is not None else None
        if site is None or SITE_ID_RE.fullmatch(site) is None:
            site = ids["reader_site"]
        day = d.dated.dates[s.row]
        rows.append({keys["record_ref"]: s.ref, keys["site"]: site, keys["date"]: day.isoformat(),
                     keys["narrative"]: s.narrative, keys["scope"]: scope_id})
    return rows


def raw_rows(sample: Sequence[Sampled], export: Export, pack: FrozenPack) -> list[dict[str, Any]]:
    """What a hand pack reads: the export's own row shaped by the column names (``a.b`` nests, ``x[]`` is a list),
    less every column its mapping takes codes from."""
    dropped = {spec["path"].split(".")[0].removesuffix("[]") for spec in pack.mapping()["codes"]}
    rows = []
    for s in sample:
        row: dict[str, Any] = {}
        for name, cell in zip(export.columns, export.rows[s.row]):
            if not name or cell is None:
                continue
            set_path(row, name, list(cell) if isinstance(cell, tuple) else cell)
        for key in dropped:
            row.pop(key, None)
        rows.append(row)
    return rows


def read_records(pack: FrozenPack, rows: Sequence[Mapping[str, Any]], drop: Iterable[str]) -> tuple[
        list[frozenset[str]], int]:
    """Rule 3: each row through the pack's mapping, codes emptied, then the lexical extractor; the predicates of the
    affirmed claims, less ``drop``. A row the mapping rejects reads as nothing and is counted."""
    preds, counts = read_records_counted(pack, rows, drop)
    return preds, counts["rejected"]


def read_records_counted(pack: FrozenPack, rows: Sequence[Mapping[str, Any]], drop: Iterable[str]) -> tuple[
        list[frozenset[str]], dict[str, int]]:
    """:func:`read_records` with D003's reader guard (E1, P10) counted beside it: the rows the mapping rejected, the
    records left without a primary entity, the structured entity values the canonicaliser did not resolve
    (``structured_unresolved``) and the predicates the extractor dropped for want of an entity (``no_entity``)."""
    canon = Canonicaliser(pack)
    extractor = LexicalExtractor(pack, canon)
    drop = set(drop)
    out: list[frozenset[str]] = []
    counts = dict.fromkeys(GUARD_COUNTS, 0)
    for row in rows:
        mapped = map_rows([row], pack)
        if not mapped.records:
            counts["rejected"] += 1
            out.append(frozenset())
            continue
        record = dict(mapped.records[0], codes=[])
        codes, text, claims = sense(record, pack, canon, extractor)
        counts["no_primary"] += int(codes.primary is None)
        counts["structured_unresolved"] += codes.structured_unresolved
        counts["no_entity"] += text.drops.get("no_entity", 0) if text is not None else 0
        out.append(frozenset(c.predicate for c in claims) - drop)
    return out, counts


def generic_predicates(pack: FrozenPack) -> set[str]:
    """Predicates reached by no specific code (a hand pack's catch-all, a draft's other bucket)."""
    specific = {c.predicate for c in pack.codes.values() if c.specific}
    return {p for p in pack.predicates if p not in specific}


def control_pack(d: Draft, lexicon: Mapping[str, list[str]], workdir: Path, name: str) -> FrozenPack:
    """The drafted pack with each specific predicate's lexicon replaced (rule 4); written and loaded."""
    files = copy.deepcopy(d.files)
    for p, terms in lexicon.items():
        files["vocabulary.json"]["predicates"][p]["lexicon"][d.lang.code] = list(terms)
    out = workdir / name
    write_pack(files, out)
    pack, error = load_written(out)
    if pack is None:
        raise DraftError(f"the {name} control does not load: {error}")
    return pack


# --------------------------------------------------------------------------------------------------- rule 5

def record_counts(pred: frozenset[str], gold: frozenset[str]) -> tuple[int, int, int]:
    return len(pred & gold), len(pred - gold), len(gold - pred)


def micro(counts: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
    tp, fp, fn = (sum(c[i] for c in counts) for i in range(3))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "f1": stats.f1_from_counts(tp, fp, fn)}


def exact_f1(counts: Sequence[tuple[int, int, int]]) -> Fraction | None:
    tp, fp, fn = (sum(c[i] for c in counts) for i in range(3))
    return Fraction(2 * tp, 2 * tp + fp + fn) if 2 * tp + fp + fn else None


def _pairs(companies: Sequence[str], preds: Sequence[frozenset[str]],
           golds: Sequence[frozenset[str]]) -> tuple[list[tuple[str, str]], list[list[tuple[int, int, int, int]]]]:
    """The (company, predicate) pairs any record touches, and per record its (pair, tp, fp, fn) contributions."""
    index: dict[tuple[str, str], int] = {}
    contrib = []
    for company, pred, gold in zip(companies, preds, golds):
        row = []
        for p in sorted(pred | gold):
            key = (company, p)
            if key not in index:
                index[key] = len(index)
            row.append((index[key], int(p in pred and p in gold), int(p in pred and p not in gold),
                        int(p in gold and p not in pred)))
        contrib.append(row)
    return list(index), contrib


def _macro_of(n_pairs: int, picks: Iterable[int],
              contrib: Sequence[Sequence[tuple[int, int, int, int]]]) -> float | None:
    t = [[0, 0, 0] for _ in range(n_pairs)]
    for j in picks:
        for i, tp, fp, fn in contrib[j]:
            c = t[i]
            c[0] += tp
            c[1] += fp
            c[2] += fn
    f1s = [2 * tp / (2 * tp + fp + fn) for tp, fp, fn in t if tp + fn > 0]
    return sum(f1s) / len(f1s) if f1s else None


def macro_f1(companies: Sequence[str], preds: Sequence[frozenset[str]],
             golds: Sequence[frozenset[str]]) -> float | None:
    """Rule 5: the mean over (company, predicate) pairs with at least one gold record of each pair's F1 over that
    company's records."""
    pairs, contrib = _pairs(companies, preds, golds)
    return _macro_of(len(pairs), range(len(contrib)), contrib)


def bootstrap_macro(companies: Sequence[str], preds: Sequence[frozenset[str]], golds: Sequence[frozenset[str]], *,
                    B: int, seed: str, alpha: float = 0.05) -> dict[str, Any]:
    """Macro F1's percentile interval by the draw rule of ``stats.bootstrap_f1``: ``random.Random(seed)``, and per
    replicate ``n`` draws of ``randrange(n)``, so the same records as every other interval of the arm."""
    pairs, contrib = _pairs(companies, preds, golds)
    n = len(contrib)
    if n == 0:
        return {"f1": None, "ci_low": None, "ci_high": None, "B": B, "seed": seed, "undefined": B, "pairs": 0}
    rng = random.Random(seed)
    reps, undefined = [], 0
    for _ in range(B):
        v = _macro_of(len(pairs), [rng.randrange(n) for _ in range(n)], contrib)
        if v is None:
            undefined += 1
        else:
            reps.append(v)
    full = _macro_of(len(pairs), range(n), contrib)
    with_gold = {i for row in contrib for i, tp, _, fn in row if tp + fn}
    return {"f1": full, "ci_low": stats.percentile(reps, 100 * alpha / 2) if reps else None,
            "ci_high": stats.percentile(reps, 100 * (1 - alpha / 2)) if reps else None, "B": B, "seed": seed,
            "undefined": undefined, "pairs": len(with_gold)}


def reader_metrics(companies: Sequence[str], preds: Sequence[frozenset[str]], golds: Sequence[frozenset[str]], *,
                   B: int, seed: str) -> dict[str, Any]:
    counts = [record_counts(p, g) for p, g in zip(preds, golds)]
    out: dict[str, Any] = {"records": len(counts), "coverage": (sum(1 for p in preds if p) / len(preds)) if preds
                           else None}
    m = micro(counts)
    if counts:
        boot = stats.bootstrap_f1(counts, B=B, seed=seed)
        m.update({"ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "undefined": boot["undefined"]})
    else:
        m.update({"ci_low": None, "ci_high": None, "undefined": None})
    out["micro"] = m
    out["macro"] = bootstrap_macro(companies, preds, golds, B=B, seed=seed)
    return out


def difference(a: Sequence[frozenset[str]], b: Sequence[frozenset[str]], golds: Sequence[frozenset[str]], *, B: int,
               seed: str) -> dict[str, Any]:
    """``a`` minus ``b`` micro F1 with ``stats.paired_bootstrap_f1``; the exact difference beside it."""
    ca = [record_counts(p, g) for p, g in zip(a, golds)]
    cb = [record_counts(p, g) for p, g in zip(b, golds)]
    if not ca:
        return {"n": 0, "diff": None, "ci_low": None, "ci_high": None, "exact_diff": None}
    out = stats.paired_bootstrap_f1(ca, cb, B=B, seed=seed)
    fa, fb = exact_f1(ca), exact_f1(cb)
    out["exact_diff"] = None if fa is None or fb is None else fa - fb
    return out


# --------------------------------------------------------------------------------------------------- D003's E3

def cluster_members(companies: Sequence[str], days: Sequence[str]) -> tuple[list[int], int]:
    """E3: each record's cluster, the (company label, received day) clusters numbered in that order; and how many."""
    keys = sorted(set(zip(companies, days)))
    index = {k: i for i, k in enumerate(keys)}
    return [index[k] for k in zip(companies, days)], len(keys)


def cluster_counts(counts: Sequence[tuple[int, int, int]], members: Sequence[int], k: int) -> list[tuple[int, ...]]:
    """E3: a cluster's (tp, fp, fn) is the sum over its records."""
    out = [[0, 0, 0] for _ in range(k)]
    for c, m in zip(counts, members):
        for i in range(3):
            out[m][i] += c[i]
    return [tuple(x) for x in out]


def cluster_f1(preds: Sequence[frozenset[str]], golds: Sequence[frozenset[str]], companies: Sequence[str],
               days: Sequence[str], *, B: int, seed: str) -> dict[str, Any]:
    """E3: one reader's micro F1 with ``stats.bootstrap_f1`` over the cluster triples (each replicate draws K of the
    K clusters, and keeps every record of each one drawn)."""
    if not golds:
        return {"f1": None, "ci_low": None, "ci_high": None, "undefined": None, "clusters": 0}
    members, k = cluster_members(companies, days)
    counts = cluster_counts([record_counts(p, g) for p, g in zip(preds, golds)], members, k)
    boot = stats.bootstrap_f1(counts, B=B, seed=seed)
    return {"f1": boot["f1"], "ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "undefined": boot["undefined"],
            "clusters": k}


def cluster_difference(a: Sequence[frozenset[str]], b: Sequence[frozenset[str]], golds: Sequence[frozenset[str]],
                       companies: Sequence[str], days: Sequence[str], *, B: int, seed: str) -> dict[str, Any]:
    """E3: ``a`` minus ``b`` micro F1 with ``stats.paired_bootstrap_f1`` over the cluster triples, ordered by
    (company label, day); the point difference is the same as over records, and the exact one is beside it."""
    if not golds:
        return {"n": 0, "diff": None, "ci_low": None, "ci_high": None, "exact_diff": None, "clusters": 0}
    members, k = cluster_members(companies, days)
    ra = [record_counts(p, g) for p, g in zip(a, golds)]
    rb = [record_counts(p, g) for p, g in zip(b, golds)]
    out = stats.paired_bootstrap_f1(cluster_counts(ra, members, k), cluster_counts(rb, members, k), B=B, seed=seed)
    fa, fb = exact_f1(ra), exact_f1(rb)
    out["exact_diff"] = None if fa is None or fb is None else fa - fb
    out["clusters"] = k
    return out


def compare(a: Sequence[frozenset[str]], b: Sequence[frozenset[str]], golds: Sequence[frozenset[str]],
            companies: Sequence[str], days: Sequence[str], *, B: int, seed: str) -> dict[str, Any]:
    """One difference two ways: ``days``, the interval over (company, received day) clusters that decides (E3), and
    ``records``, D002's interval over records, printed beside it and deciding nothing."""
    return {"days": cluster_difference(a, b, golds, companies, days, B=B, seed=seed),
            "records": difference(a, b, golds, B=B, seed=seed)}


def _floats(value: Any) -> Any:
    """Exact fractions as floats, for the JSON file (the comparisons were made on the fractions)."""
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, dict):
        return {k: _floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats(v) for v in value]
    return value


# --------------------------------------------------------------------------------------------------- D003's E7, E4

def label_echo(sample: Sequence[Sampled], plan: Any) -> list[bool]:
    """Section 4: whether each sampled record's narrative holds one of its own filed specific labels, folded and
    word-bounded (the test of :func:`label_in_text`, record by record)."""
    out = []
    for s in sample:
        text = folded(s.narrative)
        keys = {folded(v) for v in s.names if folded(v) in plan.of_key}
        out.append(any(find_bounded(k, text) for k in sorted(keys)))
    return out


def word_echo(sample: Sequence[Sampled], d: Draft) -> list[bool]:
    """E7: whether each sampled record's folded narrative holds, word-bounded, a word of one of its own filed
    specific labels, each word taken as E6 takes label words (a word the refusal refuses is left out)."""
    out = []
    for s in sample:
        text = folded(s.narrative)
        words = {w for v in s.names if folded(v) in d.plan.of_key for w in label_words(v, d.lang)
                 if d.refusal is None or d.refusal.term(w) is None}
        out.append(any(find_bounded(w, text) for w in sorted(words)))
    return out


def masked(text: str) -> str:
    """E7: a narrative folded, every run of digits replaced by ``0``."""
    return _DIGIT_RUN.sub("0", folded(text))


def masked_repeats(sample: Sequence[Sampled], export: Export, d: Draft, test: Window) -> list[bool]:
    """E7: whether each sampled record's masked narrative equals that of a training-corpus record, or that of
    another test-window record of the same company (the first row of each record id, as rule 3 reads them)."""
    training = {masked(n) for n in d.corpus.narratives}
    seen: dict[str, int] = {}
    refs: set[str] = set()
    for r, day in enumerate(d.dated.dates):
        if not test.contains(day):
            continue
        narrative = export.value(r, d.roles.narrative)
        ref = export.value(r, d.roles.record_id)
        if narrative is None or ref is None or ref in refs:
            continue
        refs.add(ref)
        key = masked(narrative)
        seen[key] = seen.get(key, 0) + 1
    return [masked(s.narrative) in training or seen.get(masked(s.narrative), 0) > 1 for s in sample]


def single_narrative_terms(d: Draft, placeholder_prefix: str) -> dict[str, int]:
    """E4's reported measure (P11): how many of the printed terms (the first ten per predicate) and how many of the
    learned lexicon terms occur in only one distinct folded narrative of the training corpus."""
    reps: dict[str, str] = {}
    for n in d.corpus.narratives:
        reps.setdefault(folded(n), n)
    lexicon = sorted({folded(t) for p in d.plan.ids for t in d.learned[p]})
    df: dict[str, int] = {}
    for terms in term_presence([reps[k] for k in sorted(reps)], lexicon):
        for t in terms:
            df[t] = df.get(t, 0) + 1
    single = {t for t in lexicon if df.get(t, 0) == 1}
    printed = sorted({folded(t) for p in d.plan.ids for t in d.lexicon[p][:10]
                      if not t.startswith(placeholder_prefix)})
    return {"lexicon_terms": len(lexicon), "lexicon_single": len(single),
            "printed_terms": len(printed), "printed_single": sum(1 for t in printed if t in single)}


# --------------------------------------------------------------------------------------------------- rule 2.2

def matched_space(d: Draft, hand: FrozenPack) -> dict[str, str]:
    """C: every name that a drafted specific predicate stands for and that the hand pack's value map sends to a
    specific code, with that code's predicate."""
    spec = hand.mapping()["codes"][0]["value_map"] or {}
    drafted_codes = set(d.plan.codes.values())
    out = {}
    for name, code in sorted(d.plan.value_map.items()):
        hand_code = spec.get(name)
        if code in drafted_codes and hand_code is not None and hand.codes[hand_code].specific:
            out[name] = hand.codes[hand_code].predicate
    return out


def names_of(d: Draft) -> dict[str, list[str]]:
    by_code = {c: p for p, c in d.plan.codes.items()}
    out: dict[str, list[str]] = {p: [] for p in d.plan.ids}
    for name, code in sorted(d.plan.value_map.items()):
        if code in by_code:
            out[by_code[code]].append(name)
    return out


def matched_gold(sample: Sequence[Sampled], hand: FrozenPack, reach: set[str]) -> list[frozenset[str]]:
    """Rule 2.2 as amended (A7): a record's gold in the matched space is the hand predicate of each filed name that
    the hand pack maps to a specific code whose predicate C reaches. A hand pack with one name per code (``pack/``)
    gives the same gold as the names in C."""
    spec = hand.mapping()["codes"][0]["value_map"] or {}
    out = []
    for s in sample:
        preds = set()
        for n in s.names:
            code = spec.get(n)
            if code is not None and hand.codes[code].specific and hand.codes[code].predicate in reach:
                preds.add(hand.codes[code].predicate)
        out.append(frozenset(preds))
    return out


def hand_gold(sample: Sequence[Sampled], hand: FrozenPack) -> list[frozenset[str]]:
    spec = hand.mapping()["codes"][0]["value_map"] or {}
    out = []
    for s in sample:
        out.append(frozenset(hand.codes[spec[n]].predicate for n in s.names
                             if n in spec and hand.codes[spec[n]].specific))
    return out


def most_frequent_reached(d: Draft, hand: FrozenPack, reach: set[str]) -> str | None:
    """D003's E2: the one reached hand predicate that the most training-corpus records of the company carry by A7's
    rule (each filed name the hand map sends to a specific code whose predicate C reaches); ties go to the smaller
    id. None when no corpus record carries one."""
    spec = hand.mapping()["codes"][0]["value_map"] or {}
    rows: dict[str, int] = {}
    for names in d.corpus.categories:
        carried = set()
        for n in names:
            code = spec.get(n)
            if code is not None and hand.codes[code].specific and hand.codes[code].predicate in reach:
                carried.add(hand.codes[code].predicate)
        for p in carried:
            rows[p] = rows.get(p, 0) + 1
    return min(rows, key=lambda p: (-rows[p], p)) if rows else None


# --------------------------------------------------------------------------------------------------- the arm

def criterion(spec: Mapping[str, Any], pooled: Mapping[str, Any], matched: Mapping[str, Any] | None,
              errors: Sequence[str], companies: int) -> dict[str, Any]:
    """Rule 6: C1 (the drafted reader against the best control) or C2 (against the hand pack in the matched space).
    A criterion that cannot be computed fails."""
    cid = spec["id"]
    margin = spec["margin"]
    out: dict[str, Any] = {"id": cid, "margin": margin, "passed": False, "reason": None}
    if errors:
        out["reason"] = "a company could not be drafted, checked or read"
        return out
    if companies == 0:
        out["reason"] = "no company"
        return out
    if cid == "C1":
        f1 = {r: pooled["readers"][r]["micro"]["f1"] for r in CONTROLS}
        if any(v is None for v in f1.values()) or pooled["readers"]["drafted"]["micro"]["f1"] is None:
            out["reason"] = "an F1 is undefined"
            return out
        best = max(CONTROLS, key=lambda r: (f1[r], -CONTROLS.index(r)))
        diff = pooled["differences"][best]
        out.update({"best_control": best, "diff": diff["diff"], "ci_low": diff["ci_low"], "ci_high": diff["ci_high"]})
        if diff["exact_diff"] is None or diff["ci_low"] is None:
            out["reason"] = "the difference is undefined"
            return out
        exact = Fraction(diff["exact_diff"])
        at_margin = exact >= Fraction(str(margin))
        above_zero = diff["ci_low"] > 0
        out[EXACT] = {"diff": float(exact), "diff_fraction": f"{exact.numerator}/{exact.denominator}",
                      "ci_low": diff["ci_low"], "ci_high": diff["ci_high"],
                      "comparisons": {f"diff >= {margin}": at_margin, "ci_low > 0": above_zero}}
        out["passed"] = bool(at_margin and above_zero)
        return out
    against = spec["against"]
    if matched is None or against not in matched["hand"]:
        out["reason"] = "no matched space"
        return out
    diff = matched["hand"][against]["difference"]
    out.update({"against": against, "diff": diff["diff"], "ci_low": diff["ci_low"], "ci_high": diff["ci_high"],
                "records": diff["n"]})
    if diff["ci_low"] is None:
        out["reason"] = "the difference is undefined"
        return out
    out["passed"] = bool(diff["ci_low"] > -margin)
    out["superior"] = bool(diff["ci_low"] > 0)
    out[EXACT] = {"diff": diff["diff"], "ci_low": diff["ci_low"], "ci_high": diff["ci_high"],
                  "comparisons": {f"ci_low > -{margin}": out["passed"], "ci_low > 0 (superior, decides nothing)":
                                  out["superior"]}}
    return out


def criterion_days(spec: Mapping[str, Any], pooled: Mapping[str, Any], errors: Sequence[str],
                   companies: int) -> dict[str, Any]:
    """D003's C1 (section 8 with E3 and E6): the drafted reader against the best of the settings' controls (the
    highest pooled micro F1 on the whole sample; ties to the earlier in the list). It passes when the exact
    difference is at least the margin and the lower end of the interval over (company, received day) clusters is
    above 0. The record interval is printed beside it and decides nothing. The labels come from the settings (P5)."""
    controls = tuple(spec["controls"])
    labels = spec["labels"]
    margin = spec["margin"]
    out: dict[str, Any] = {"id": "C1", "margin": margin, "passed": False, "reason": None}
    if errors:
        out["reason"] = "a company could not be drafted, checked or read"
        return out
    if companies == 0:
        out["reason"] = "no company"
        return out
    f1 = {r: pooled["readers"][r]["micro"]["f1"] for r in controls}
    if any(v is None for v in f1.values()) or pooled["readers"]["drafted"]["micro"]["f1"] is None:
        out["reason"] = "an F1 is undefined"
        return out
    best = max(controls, key=lambda r: (f1[r], -controls.index(r)))
    days, rec = pooled["compared"][best]["days"], pooled["compared"][best]["records"]
    out.update({"best_control": best, "diff": days["diff"], "ci_low": days["ci_low"], "ci_high": days["ci_high"],
                "records_ci_low": rec["ci_low"], "records_ci_high": rec["ci_high"], "clusters": days["clusters"]})
    if days["exact_diff"] is None or days["ci_low"] is None:
        out["reason"] = "the difference is undefined"
        return out
    exact = Fraction(days["exact_diff"])
    at_margin = exact >= Fraction(str(margin))
    above_zero = days["ci_low"] > 0
    out[EXACT] = {"diff": float(exact), "diff_fraction": f"{exact.numerator}/{exact.denominator}",
                  "ci_low": days["ci_low"], "ci_high": days["ci_high"],
                  "comparisons": {labels["margin"]: at_margin, labels["interval"]: above_zero,
                                  labels["records"]: rec["ci_low"] is not None and rec["ci_low"] > 0}}
    out["passed"] = bool(at_margin and above_zero)
    return out


def criterion_reach(spec: Mapping[str, Any], space: Mapping[str, Any] | None, guard: Mapping[str, Any] | None,
                    errors: Sequence[str], companies: int) -> dict[str, Any]:
    """D003's C2 (section 8 with E2, E3, E1 and E8): in the space of the hand predicates the drafted names reach,
    the lower end of the cluster interval of drafted minus each of the hand pack, ``all_reached`` and
    ``most_frequent_reached`` must be above 0, on at least ``min_records`` records with a nonempty gold. Fewer
    records, or a deciding hand copy that rejected a sampled record or left one without a primary entity, leave C2
    not decided, and it fails. Drafted minus the hand pack as non-inferiority is printed under a label that says it
    decides nothing."""
    against = spec["against"]
    labels = spec["labels"]
    out: dict[str, Any] = {"id": "C2", "against": against, "passed": False, "decided": False, "reason": None}
    if errors:
        out["reason"] = "a company could not be drafted, checked or read"
        return out
    if companies == 0 or space is None:
        out["reason"] = "no company" if companies == 0 else "no space"
        return out
    out.update({"scored": space["scored"], "records": space["with_gold"], "min_records": spec["min_records"],
                "matched_names": space["matched_names"]})
    if spec.get("guard") and guard is not None and (guard["rejected"] or guard["no_primary"]):
        out["reason"] = (f"the deciding hand copy rejected {guard['rejected']} sampled records and left "
                         f"{guard['no_primary']} without a primary entity")
        return out
    if space["with_gold"] < spec["min_records"]:
        out["reason"] = f"{space['with_gold']} records with a nonempty gold, fewer than {spec['min_records']}"
        return out
    readers = (against, *spec["controls"])
    diffs = {r: space["compared"][r]["days"] for r in readers}
    if any(diffs[r]["ci_low"] is None for r in readers):
        out["reason"] = "a difference is undefined"
        return out
    out["decided"] = True
    superior = {r: diffs[r]["ci_low"] > 0 for r in readers}
    reported = diffs[against]["ci_low"] > -spec["reported_margin"]
    out.update({"diff": {r: diffs[r]["diff"] for r in readers}, "ci_low": {r: diffs[r]["ci_low"] for r in readers},
                "ci_high": {r: diffs[r]["ci_high"] for r in readers}, "clusters": diffs[against]["clusters"]})
    comparisons = {labels["superior"].format(reader=r): superior[r] for r in readers}
    comparisons[labels["reported"]] = reported
    out[EXACT] = {"diff": {r: diffs[r]["diff"] for r in readers}, "ci_low": {r: diffs[r]["ci_low"] for r in readers},
                  "ci_high": {r: diffs[r]["ci_high"] for r in readers}, "comparisons": comparisons}
    out["passed"] = all(superior.values())
    return out


def withhold_strings(agg: Mapping[str, Any]) -> dict[str, Any]:
    """Amendment A5: a company whose pack failed the privacy floor keeps no label, id or term in ``arm.json``: the
    category labels become their count, each predicate keeps its code and counts, and the majority prior its code."""
    out = copy.deepcopy(dict(agg))
    facts = out["draft"]
    facts["categories"]["passing_floor"] = len(facts["categories"]["passing_floor"])
    facts["predicates"] = [{"code": p["code"], "records": p["records"], "terms": p["terms"],
                            "refused_assignable": p["refused_assignable"]} for p in facts["predicates"]]
    out["controls"]["majority_prior"].pop("predicate", None)
    if "set_prior" in out["controls"]:
        out["controls"]["set_prior"].pop("predicates", None)
    out["strings_withheld"] = True
    return out


REFUSAL_SUMS = ("categories", "rows", "with_minimum", "terms", "assignable", "within_cap")


def refusal_totals(companies: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Amendment A14: what the refusal removed, summed over an arm's drafted companies (printed beside its criterion):
    refused categories and the corpus rows filed under them, with their share of the pooled corpus; refused terms,
    those that would have been assigned and those that would have been among their predicate's K terms; and the
    label parts the refusal took out of the label-names control. Counts only."""
    out: dict[str, Any] = dict.fromkeys((*REFUSAL_SUMS, "corpus", "label_parts", "companies"), 0)
    for c in companies.values():
        if "error" in c:
            continue
        removed = c["draft"]["refusal"]
        for key in REFUSAL_SUMS:
            out[key] += removed[key]
        out["corpus"] += c["draft"]["training"]["corpus"]
        out["label_parts"] += c["controls"]["label_names"]["refused"]
        out["companies"] += 1
    out["share"] = out["rows"] / out["corpus"] if out["corpus"] else None
    return out


def _company(settings: Mapping[str, Any], arm: str, label: str, entry: Mapping[str, Any], exports: Path,
             out: Path, workdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """One company: (its aggregates for arm.json, its in-memory records for pooling)."""
    spec = settings["arms"][arm]
    params = settings["params"]
    roles = arm_roles(settings, arm)
    lang = with_non_specific(load_language(roles.language), params)
    template = load_template()
    controls = arm_controls(spec)
    cdir = out / label
    write_json(cdir / "roles.json", roles.to_json())
    export = read_export(exports / entry["file"])
    train, test = parse_window(*spec["train"]), parse_window(*spec["test"])
    pack_id = slug(f"{arm} {label}", params, template["ids.json"]["id_prefix"])
    d = draft_export(export, roles, train, pack_id, params=params, lang=lang, template=template)
    hashes = write_pack(d.files, cdir / "pack")
    pack, error = load_written(cdir / "pack")
    write_json(cdir / "draft.json", {"kind": "onboard_draft", "schema_version": 1, **d.facts,
                                     "pack_files": hashes, "loader": {"passed": pack is not None, "error": error}})
    checked = check_pack(cdir / "pack", export, roles, train, params=params, lang=lang, template=template,
                         settings=settings)
    write_json(cdir / "check.json", checked)
    agg: dict[str, Any] = {
        "file_sha256": hashlib.sha256((exports / entry["file"]).read_bytes()).hexdigest(),
        "counts": {k: v for k, v in entry.items() if k != "file"},
        "draft": d.facts, "pack": {"id": pack_id, "files": hashes,
                                     "hashes": pack.hashes() if pack is not None else None,
                                     "loader": {"passed": pack is not None, "error": error}},
        "check": checked}
    if pack is None:
        raise DraftError(f"the drafted pack does not load ({error.split(': ', 1)[0]})")
    boot = settings["bootstrap"]
    seed = f"{settings['sample']['seed_prefix']}:{arm}:{label}"
    sample, counts = draw_sample(export, d, test, seed, spec["per_company"], label)
    agg["sample"] = counts
    agg["label_in_text"] = label_in_text(sample, d.plan)
    agg["label_in_text_share"] = agg["label_in_text"] / len(sample) if sample else None
    golds = [s.gold for s in sample]
    top = min(d.plan.ids, key=lambda p: (-d.plan.counts[p], p))
    permuted = permuted_labels(d.corpus, f"{settings['permute']['seed_prefix']}:{arm}:{label}")
    perm_lex = with_placeholders(assign_terms(d.table, d.eligible, record_labels(permuted, d.plan), d.plan.ids,
                                              params), template["ids.json"])
    name_lex = with_placeholders(label_name_lexicon(d.plan, lang, d.refusal), template["ids.json"])
    agg["controls"] = {"majority_prior": {"predicate": top, "code": d.plan.codes[top]},
                       "permuted_labels": {"placeholders": sum(1 for p in d.plan.ids
                                                               if perm_lex[p][0].startswith(
                                                                   template["ids.json"]["placeholder_prefix"])),
                                           "terms": sum(len(v) for v in perm_lex.values())},
                       "label_names": {"placeholders": sum(1 for p in d.plan.ids
                                                           if name_lex[p][0].startswith(
                                                               template["ids.json"]["placeholder_prefix"])),
                                       "terms": sum(len(v) for v in name_lex.values()),
                                       "refused": refused_label_parts(d.plan, lang, d.refusal)}}
    prefix = template["ids.json"]["placeholder_prefix"]
    if "set_prior" in controls:
        prior = set_prior(d.labels)
        agg["controls"]["set_prior"] = {"predicates": list(prior), "codes": [d.plan.codes[p] for p in prior]}
    if "label_words" in controls:
        word_lex = with_placeholders(label_word_lexicon(d.plan, lang, d.refusal), template["ids.json"])
        agg["controls"]["label_words"] = {"placeholders": sum(1 for p in d.plan.ids if word_lex[p][0].startswith(prefix)),
                                          "terms": sum(len(v) for v in word_lex.values()),
                                          "refused": refused_label_words(d.plan, lang, d.refusal)}
    rows = drafted_rows(sample, export, d)
    other = {d.plan.other_id}
    preds: dict[str, list[frozenset[str]]] = {}
    rejected: dict[str, int] = {}
    guard: dict[str, dict[str, int]] = {}
    preds["drafted"], guard["drafted"] = read_records_counted(pack, rows, other)
    rejected["drafted"] = guard["drafted"]["rejected"]
    preds["majority_prior"] = [frozenset({top}) for _ in sample]
    preds["permuted_labels"], rejected["permuted_labels"] = read_records(
        control_pack(d, perm_lex, workdir / label, "permuted_labels"), rows, other)
    preds["label_names"], rejected["label_names"] = read_records(
        control_pack(d, name_lex, workdir / label, "label_names"), rows, other)
    if "set_prior" in controls:
        preds["set_prior"] = [frozenset(prior) for _ in sample]
    if "label_words" in controls:
        preds["label_words"], rejected["label_words"] = read_records(
            control_pack(d, word_lex, workdir / label, "label_words"), rows, other)
    agg["reader_rejected"] = rejected
    companies = [label] * len(sample)
    agg["readers"] = {r: reader_metrics(companies, preds[r], golds, B=boot["B"],
                                        seed=f"{boot['seed_prefix']}:{arm}") for r in ("drafted", *controls)}
    memory: dict[str, Any] = {"sample": sample, "preds": preds, "golds": golds, "hand": {}, "draft": d,
                              "lexicons": {"permuted_labels": perm_lex, "label_names": name_lex}, "majority": top,
                              "days": [d.dated.dates[s.row].isoformat() for s in sample]}
    if reported(spec, "echo_free") or reported(spec, "masked_repeats"):
        flags = {"label_echo": label_echo(sample, d.plan), "word_echo": word_echo(sample, d),
                 "masked_repeat": masked_repeats(sample, export, d, test)}
        memory["flags"] = flags
        agg["echo"] = {"records": len(sample), **{k: sum(v) for k, v in flags.items()}}
    if reported(spec, "single_narrative_terms"):
        agg["single_narrative_terms"] = single_narrative_terms(d, prefix)
    if cluster_on(spec):
        agg["clusters"] = len(set(memory["days"]))
    space_spec = reach_spec(spec)
    for name, path in sorted(spec.get("hand_packs", {}).items()):
        if space_spec is not None:
            _hand_reach(name, load_pack_dir(resolve(path)), sample, export, d, preds["drafted"], memory, agg, guard,
                        label, B=boot["B"], seed=f"{boot['seed_prefix']}:{arm}")
            continue
        hand = load_pack_dir(resolve(path))
        hrows = raw_rows(sample, export, hand)
        hpred, hrej = read_records(hand, hrows, generic_predicates(hand))
        space = matched_space(d, hand)
        by_name = names_of(d)
        reach = set(space.values())
        m_gold = matched_gold(sample, hand, reach)
        m_hand = [p & reach for p in hpred]
        m_draft = [frozenset(space[n] for p in pr for n in by_name.get(p, ()) if n in space)
                   for pr in preds["drafted"]]
        h_gold = hand_gold(sample, hand)
        memory["hand"][name] = {"pred": hpred, "gold": h_gold, "m_gold": m_gold, "m_hand": m_hand,
                                "m_draft": m_draft}
        keep = [i for i, g in enumerate(m_gold) if g]
        mc = [label] * len(keep)
        agg.setdefault("hand", {})[name] = {
            "rejected": hrej, "matched_names": len(space), "matched_predicates": len(reach),
            "matched_records": len(keep),
            "matched": {"drafted": reader_metrics(mc, [m_draft[i] for i in keep], [m_gold[i] for i in keep],
                                                  B=boot["B"], seed=f"{boot['seed_prefix']}:{arm}"),
                        "hand": reader_metrics(mc, [m_hand[i] for i in keep], [m_gold[i] for i in keep],
                                               B=boot["B"], seed=f"{boot['seed_prefix']}:{arm}")},
            "own_space": reader_metrics(companies, hpred, h_gold, B=boot["B"], seed=f"{boot['seed_prefix']}:{arm}")}
    if reported(spec, "reader_guard"):
        agg["reader_guard"] = guard
    if not checked["passed"]:
        agg = withhold_strings(agg)
    return agg, memory


def _hand_reach(name: str, hand: FrozenPack, sample: Sequence[Sampled], export: Export, d: Draft,
                drafted: Sequence[frozenset[str]], memory: dict[str, Any], agg: dict[str, Any],
                guard: dict[str, dict[str, int]], label: str, *, B: int, seed: str) -> None:
    """D003's C2 space for one company and one hand copy (E2, P7): C is the company's names that are drafted
    specific predicates and that the copy's map sends to a specific code; if C is empty the company has no record in
    the space, else every one of its sampled records is scored, with the gold of A7 restricted to the predicates C
    reaches (possibly empty). The readers: drafted, the hand copy, ``all_reached`` and ``most_frequent_reached``.
    The copy's reading is counted for the reader guard (E1, P10). Own-space metrics as before."""
    n = len(sample)
    hrows = raw_rows(sample, export, hand)
    hpred, counts = read_records_counted(hand, hrows, generic_predicates(hand))
    guard[name] = counts
    space = matched_space(d, hand)
    by_name = names_of(d)
    reach = set(space.values())
    m_gold = matched_gold(sample, hand, reach)
    m_hand = [p & reach for p in hpred]
    m_draft = [frozenset(space[x] for p in pr for x in by_name.get(p, ()) if x in space) for pr in drafted]
    top = most_frequent_reached(d, hand, reach)
    readers = {"drafted": m_draft, name: m_hand, "all_reached": [frozenset(reach)] * n,
               "most_frequent_reached": [frozenset({top}) if top is not None else frozenset()] * n}
    h_gold = hand_gold(sample, hand)
    memory["hand"][name] = {"pred": hpred, "gold": h_gold, "m_gold": m_gold, "scored": [bool(reach)] * n,
                            "days": list(memory["days"]), **{f"space:{r}": list(v) for r, v in readers.items()}}
    keep = list(range(n)) if reach else []
    companies = [label] * len(keep)
    agg.setdefault("hand", {})[name] = {
        "rejected": counts["rejected"], "matched_names": len(space), "reach": sorted(reach),
        "most_frequent_reached": top, "scored": len(keep), "with_gold": sum(1 for i in keep if m_gold[i]),
        "space": {r: reader_metrics(companies, [v[i] for i in keep], [m_gold[i] for i in keep], B=B, seed=seed)
                  for r, v in readers.items()},
        "own_space": reader_metrics([label] * n, hpred, h_gold, B=B, seed=seed)}


def run_arm(settings: Mapping[str, Any], settings_sha256: str, arm: str, exports: Path, out: Path,
            commit: Callable[[], str]) -> dict[str, Any]:
    if arm not in settings["arms"]:
        raise ScoreError(f"no arm {arm!r} in the settings")
    spec = settings["arms"][arm]
    boot = settings["bootstrap"]
    seed = f"{boot['seed_prefix']}:{arm}"
    companies = load_companies(exports)
    doc: dict[str, Any] = {"kind": "onboard_d001_arm", "schema_version": 1, "experiment": experiment_id(settings),
                           "arm": arm, "settings_sha256": settings_sha256, "code_commit": commit(),
                           "code_files": tree_hashes(("mycelic/collective/onboard",)),
                           "exports_dir": str(exports), "companies": {}, "errors": []}
    for extra in ("source.json", "definitions.json"):
        if (exports / extra).is_file():
            doc[extra.removesuffix(".json")] = strict_load((exports / extra).read_bytes())
    controls = arm_controls(spec)
    readers = ("drafted", *controls)
    pooled_mem: dict[str, Any] = {"companies": [], "preds": {r: [] for r in readers}, "golds": [], "hand": {},
                                  "days": [], "flags": {}}
    with tempfile.TemporaryDirectory(prefix="onboard-controls-") as tmp:
        for label, entry in companies.items():
            try:
                agg, mem = _company(settings, arm, label, entry, exports, out, Path(tmp))
            except (DraftError, ExportError, RolesError, PackError, OSError) as err:
                doc["companies"][label] = {"error": err.__class__.__name__ + ": " + str(err)}
                doc["errors"].append(label)
                continue
            doc["companies"][label] = agg
            pooled_mem["companies"] += [label] * len(mem["sample"])
            pooled_mem["golds"] += mem["golds"]
            pooled_mem["days"] += mem["days"]
            for k, v in mem.get("flags", {}).items():
                pooled_mem["flags"].setdefault(k, []).extend(v)
            for r in readers:
                pooled_mem["preds"][r] += mem["preds"][r]
            for name, h in mem["hand"].items():
                acc = pooled_mem["hand"].setdefault(name, {k: [] for k in h} | {"companies": []})
                for k, v in h.items():
                    acc[k] += v
                acc["companies"] += [label] * len(mem["sample"])
    golds, comps = pooled_mem["golds"], pooled_mem["companies"]
    pooled: dict[str, Any] = {"records": len(golds), "readers": {}, "differences": {}}
    for r in readers:
        pooled["readers"][r] = reader_metrics(comps, pooled_mem["preds"][r], golds, B=boot["B"], seed=seed)
    for r in controls:
        pooled["differences"][r] = difference(pooled_mem["preds"]["drafted"], pooled_mem["preds"][r], golds,
                                              B=boot["B"], seed=seed)
    n_text = sum(c.get("label_in_text", 0) for c in doc["companies"].values() if "error" not in c)
    pooled["label_in_text_share"] = n_text / len(golds) if golds else None
    doc["pooled"] = pooled
    if isinstance(spec["criterion"], list):
        return _finish_d003(settings, spec, arm, doc, pooled_mem, len(companies), out)
    matched = None
    if pooled_mem["hand"]:
        matched = {"hand": {}}
        for name, h in sorted(pooled_mem["hand"].items()):
            keep = [i for i, g in enumerate(h["m_gold"]) if g]
            mg = [h["m_gold"][i] for i in keep]
            md = [h["m_draft"][i] for i in keep]
            mh = [h["m_hand"][i] for i in keep]
            mc = [h["companies"][i] for i in keep]
            own = reader_metrics(h["companies"], h["pred"], h["gold"], B=boot["B"], seed=seed)
            matched["hand"][name] = {
                "records": len(keep),
                "drafted": reader_metrics(mc, md, mg, B=boot["B"], seed=seed),
                "hand": reader_metrics(mc, mh, mg, B=boot["B"], seed=seed),
                "difference": difference(md, mh, mg, B=boot["B"], seed=seed),
                "own_space": own}
    doc["matched"] = matched
    doc["criterion"] = criterion(spec["criterion"], pooled, matched, doc["errors"], len(companies))
    doc["criterion"]["refusal"] = refusal_totals(doc["companies"])
    against = spec["criterion"].get("against")
    if against is not None:
        doc["criterion"]["matched_names"] = {label: c["hand"][against]["matched_names"]
                                             for label, c in doc["companies"].items()
                                             if "error" not in c and against in c.get("hand", {})}
    for d in (doc["criterion"], *(doc["pooled"]["differences"].values()),
              *((m["difference"] for m in matched["hand"].values()) if matched else ())):
        if isinstance(d.get("exact_diff"), Fraction):
            d["exact_diff"] = float(d["exact_diff"])
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "arm.json", doc)
    return doc


def _pick(values: Sequence[Any], idx: Sequence[int]) -> list[Any]:
    return [values[i] for i in idx]


def _finish_d003(settings: Mapping[str, Any], spec: Mapping[str, Any], arm: str, doc: dict[str, Any],
                 mem: Mapping[str, Any], n_companies: int, out: Path) -> dict[str, Any]:
    """The rest of an arm whose ``criterion`` is a list (D003, change P1): the cluster intervals (E3, P8), the C2
    space (E2, P7), the criteria in their listed order, the reported comparisons on the records without an echo or
    a masked repeat (E7, P6) and what the refusal removed beside each criterion (A14)."""
    boot = settings["bootstrap"]
    B, seed = boot["B"], f"{boot['seed_prefix']}:{arm}"
    controls = arm_controls(spec)
    pooled = doc["pooled"]
    golds, comps, days = mem["golds"], mem["companies"], mem["days"]
    drafted = mem["preds"]["drafted"]
    if cluster_on(spec):
        _, k = cluster_members(comps, days)
        pooled["cluster"] = {
            "clusters": k, "per_company": {c: len({d for x, d in zip(comps, days) if x == c}) for c in sorted(set(comps))},
            "readers": {r: cluster_f1(mem["preds"][r], golds, comps, days, B=B, seed=seed) for r in ("drafted", *controls)}}
        pooled["compared"] = {r: {"days": cluster_difference(drafted, mem["preds"][r], golds, comps, days, B=B,
                                                             seed=seed),
                                  "records": pooled["differences"][r]} for r in controls}
    flags = mem.get("flags") or {}
    if flags:
        n = len(golds)
        pooled["echo"] = {"records": n, **{k: (sum(v) / n if n else None) for k, v in flags.items()}}
    space_spec = reach_spec(spec)
    space: dict[str, Any] = {}
    if space_spec is not None:
        for name, h in sorted(mem["hand"].items()):
            idx = [i for i, s in enumerate(h["scored"]) if s]
            sg, sc, sd = _pick(h["m_gold"], idx), _pick(h["companies"], idx), _pick(h["days"], idx)
            preds = {r: _pick(h[f"space:{r}"], idx) for r in ("drafted", name, *SPACE_CONTROLS)}
            space[name] = {
                "scored": len(idx), "with_gold": sum(1 for g in sg if g),
                "matched_names": {lbl: c["hand"][name]["matched_names"] for lbl, c in doc["companies"].items()
                                  if "error" not in c and name in c.get("hand", {})},
                "readers": {r: {**reader_metrics(sc, v, sg, B=B, seed=seed),
                                "days": cluster_f1(v, sg, sc, sd, B=B, seed=seed)} for r, v in preds.items()},
                "compared": {r: compare(preds["drafted"], preds[r], sg, sc, sd, B=B, seed=seed)
                             for r in (name, *SPACE_CONTROLS)},
                "own_space": reader_metrics(h["companies"], h["pred"], h["gold"], B=B, seed=seed)}
        doc["space"] = space
    guard = None
    if space_spec is not None and reported(spec, "reader_guard"):
        against = space_spec["against"]
        guard = {k: sum(c["reader_guard"][against][k] for c in doc["companies"].values() if "error" not in c)
                 for k in GUARD_COUNTS}
        doc["reader_guard"] = {r: {k: sum(c["reader_guard"][r][k] for c in doc["companies"].values()
                                          if "error" not in c) for k in GUARD_COUNTS}
                               for r in ("drafted", *sorted(mem["hand"]))}
    criteria = []
    for c in criterion_specs(spec):
        if c["id"] == "C1" and c.get("controls"):
            one = criterion_days(c, pooled, doc["errors"], n_companies)
        elif c["id"] == "C2" and c.get("space") == "reach":
            one = criterion_reach(c, space.get(c["against"]), guard, doc["errors"], n_companies)
            one["matched_names"] = (space.get(c["against"]) or {}).get("matched_names", {})
        else:
            one = criterion(c, pooled, None, doc["errors"], n_companies)
        one["refusal"] = refusal_totals(doc["companies"])
        if reported(spec, "kept_days"):
            one["kept_days"] = {lbl: {w: co["counts"].get(f"kept_days_{w}") for w in ("train", "test")}
                                for lbl, co in doc["companies"].items() if "error" not in co}
        criteria.append(one)
    doc["criterion"] = criteria
    best = next((c.get("best_control") for c in criteria if c["id"] == "C1"), None)
    if flags and best is not None:
        doc["subsets"] = _subsets(flags, mem, best, space_spec, B=B, seed=seed)
    doc["matched"] = None
    doc = _floats(doc)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "arm.json", doc)
    return doc


def _subsets(flags: Mapping[str, Sequence[bool]], mem: Mapping[str, Any], best: str,
             space_spec: Mapping[str, Any] | None, *, B: int, seed: str) -> dict[str, Any]:
    """E7 and P6, reported only: C1's comparison (drafted minus the best control of the whole sample) and C2's
    three comparisons, repeated on the sampled records that hold no echo of their own filed labels (whole label;
    label word) and on those with no digit-masked repeat, each with the cluster and the record interval."""
    golds, comps, days = mem["golds"], mem["companies"], mem["days"]
    out: dict[str, Any] = {}
    for key, flag in zip(SUBSETS, ("label_echo", "word_echo", "masked_repeat")):
        idx = [i for i, f in enumerate(flags[flag]) if not f]
        entry: dict[str, Any] = {"records": len(idx), "c1": {
            "against": best, **compare(_pick(mem["preds"]["drafted"], idx), _pick(mem["preds"][best], idx),
                                       _pick(golds, idx), _pick(comps, idx), _pick(days, idx), B=B, seed=seed)}}
        if space_spec is not None and space_spec["against"] in mem["hand"]:
            name = space_spec["against"]
            h = mem["hand"][name]
            keep = [i for i in idx if h["scored"][i]]
            sg = _pick(h["m_gold"], keep)
            entry["c2"] = {"scored": len(keep), "with_gold": sum(1 for g in sg if g), "compared": {
                r: compare(_pick(h["space:drafted"], keep), _pick(h[f"space:{r}"], keep), sg, _pick(h["companies"], keep),
                           _pick(h["days"], keep), B=B, seed=seed) for r in (name, *SPACE_CONTROLS)}}
        out[key] = entry
    return out
