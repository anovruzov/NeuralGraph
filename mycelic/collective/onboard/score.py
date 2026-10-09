"""One arm of D001: per company, the draft, its checks, the controls, the held-out sample, reading and metrics.

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
* **The matched space** (rule 2.2, :func:`matched_space`) when the arm names hand packs; the criterion of rule 6 the
  arm names (:func:`criterion`).

Per-record data (gold, predictions) stays in memory. ``arm.json`` holds aggregates, category labels that passed the
floor and the first ten terms of each predicate; floats are rounded to four places.
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
from .check import check_pack
from .draft import (Draft, DraftError, Window, _cells, assign_terms, draft_export, label_name_lexicon, load_language,
                    load_template, load_written, parse_window, permuted_labels, record_labels, slug, with_placeholders,
                    write_pack)
from .exports import Export, ExportError, read_export
from .roles import Roles, RolesError, roles_from_json

ROOT = Path(__file__).resolve().parents[3]
READERS = ("drafted", "majority_prior", "permuted_labels", "label_names")
CONTROLS = READERS[1:]
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
DIGITS = 4


class ScoreError(ValueError):
    """Bad settings, a missing companies file or a company label that is not a plain name."""


# --------------------------------------------------------------------------------------------------- settings

def load_settings(path: str | Path) -> tuple[dict[str, Any], str]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ScoreError(f"cannot read {path} ({exc.__class__.__name__})") from None
    return strict_load(data), hashlib.sha256(data).hexdigest()


def arm_roles(settings: Mapping[str, Any], arm: str) -> Roles:
    return roles_from_json({"language": settings["language"], "roles": settings["arms"][arm]["roles"]})


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
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: rounded(v, digits) for k, v in value.items()}
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
    canon = Canonicaliser(pack)
    extractor = LexicalExtractor(pack, canon)
    drop = set(drop)
    out, rejected = [], 0
    for row in rows:
        mapped = map_rows([row], pack)
        if not mapped.records:
            rejected += 1
            out.append(frozenset())
            continue
        record = dict(mapped.records[0], codes=[])
        _, _, claims = sense(record, pack, canon, extractor)
        out.append(frozenset(c.predicate for c in claims) - drop)
    return out, rejected


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


def hand_gold(sample: Sequence[Sampled], hand: FrozenPack) -> list[frozenset[str]]:
    spec = hand.mapping()["codes"][0]["value_map"] or {}
    out = []
    for s in sample:
        out.append(frozenset(hand.codes[spec[n]].predicate for n in s.names
                             if n in spec and hand.codes[spec[n]].specific))
    return out


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
        out["passed"] = bool(diff["exact_diff"] >= Fraction(str(margin)) and diff["ci_low"] > 0)
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
    return out


def _company(settings: Mapping[str, Any], arm: str, label: str, entry: Mapping[str, Any], exports: Path,
             out: Path, workdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """One company: (its aggregates for arm.json, its in-memory records for pooling)."""
    spec = settings["arms"][arm]
    params = settings["params"]
    roles = arm_roles(settings, arm)
    lang = load_language(roles.language)
    template = load_template()
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
        raise DraftError(f"the drafted pack does not load: {error}")
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
    name_lex = with_placeholders(label_name_lexicon(d.plan, lang), template["ids.json"])
    agg["controls"] = {"majority_prior": {"predicate": top},
                       "permuted_labels": {"placeholders": sum(1 for p in d.plan.ids
                                                               if perm_lex[p][0].startswith(
                                                                   template["ids.json"]["placeholder_prefix"])),
                                           "terms": sum(len(v) for v in perm_lex.values())},
                       "label_names": {"placeholders": sum(1 for p in d.plan.ids
                                                           if name_lex[p][0].startswith(
                                                               template["ids.json"]["placeholder_prefix"])),
                                       "terms": sum(len(v) for v in name_lex.values())}}
    rows = drafted_rows(sample, export, d)
    other = {d.plan.other_id}
    preds: dict[str, list[frozenset[str]]] = {}
    rejected: dict[str, int] = {}
    preds["drafted"], rejected["drafted"] = read_records(pack, rows, other)
    preds["majority_prior"] = [frozenset({top}) for _ in sample]
    preds["permuted_labels"], rejected["permuted_labels"] = read_records(
        control_pack(d, perm_lex, workdir / label, "permuted_labels"), rows, other)
    preds["label_names"], rejected["label_names"] = read_records(
        control_pack(d, name_lex, workdir / label, "label_names"), rows, other)
    agg["reader_rejected"] = rejected
    companies = [label] * len(sample)
    agg["readers"] = {r: reader_metrics(companies, preds[r], golds, B=boot["B"],
                                        seed=f"{boot['seed_prefix']}:{arm}") for r in READERS}
    memory: dict[str, Any] = {"sample": sample, "preds": preds, "golds": golds, "hand": {}}
    for name, path in sorted(spec.get("hand_packs", {}).items()):
        hand = load_pack_dir(resolve(path))
        hrows = raw_rows(sample, export, hand)
        hpred, hrej = read_records(hand, hrows, generic_predicates(hand))
        space = matched_space(d, hand)
        by_name = names_of(d)
        reach = set(space.values())
        m_gold = [frozenset(space[n] for n in s.names if n in space) for s in sample]
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
    return agg, memory


def run_arm(settings: Mapping[str, Any], settings_sha256: str, arm: str, exports: Path, out: Path,
            commit: Callable[[], str]) -> dict[str, Any]:
    if arm not in settings["arms"]:
        raise ScoreError(f"no arm {arm!r} in the settings")
    spec = settings["arms"][arm]
    boot = settings["bootstrap"]
    seed = f"{boot['seed_prefix']}:{arm}"
    companies = load_companies(exports)
    doc: dict[str, Any] = {"kind": "onboard_d001_arm", "schema_version": 1, "arm": arm,
                           "settings_sha256": settings_sha256, "code_commit": commit(),
                           "code_files": tree_hashes(("mycelic/collective/onboard",)),
                           "exports_dir": str(exports), "companies": {}, "errors": []}
    for extra in ("source.json", "definitions.json"):
        if (exports / extra).is_file():
            doc[extra.removesuffix(".json")] = strict_load((exports / extra).read_bytes())
    pooled_mem: dict[str, Any] = {"companies": [], "preds": {r: [] for r in READERS}, "golds": [], "hand": {}}
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
            for r in READERS:
                pooled_mem["preds"][r] += mem["preds"][r]
            for name, h in mem["hand"].items():
                acc = pooled_mem["hand"].setdefault(name, {k: [] for k in h} | {"companies": []})
                for k, v in h.items():
                    acc[k] += v
                acc["companies"] += [label] * len(mem["sample"])
    golds, comps = pooled_mem["golds"], pooled_mem["companies"]
    pooled: dict[str, Any] = {"records": len(golds), "readers": {}, "differences": {}}
    for r in READERS:
        pooled["readers"][r] = reader_metrics(comps, pooled_mem["preds"][r], golds, B=boot["B"], seed=seed)
    for r in CONTROLS:
        pooled["differences"][r] = difference(pooled_mem["preds"]["drafted"], pooled_mem["preds"][r], golds,
                                              B=boot["B"], seed=seed)
    n_text = sum(c.get("label_in_text", 0) for c in doc["companies"].values() if "error" not in c)
    pooled["label_in_text_share"] = n_text / len(golds) if golds else None
    doc["pooled"] = pooled
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
    for d in (doc["criterion"], *(doc["pooled"]["differences"].values()),
              *((m["difference"] for m in matched["hand"].values()) if matched else ())):
        if isinstance(d.get("exact_diff"), Fraction):
            d["exact_diff"] = float(d["exact_diff"])
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "arm.json", doc)
    return doc
