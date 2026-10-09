"""The privacy floor of a drafted pack (D001 rule 1.7), the loader's check (M2) and the label scan of M1.

Every check is recomputed from the export, never taken from the drafter's counts:

1. **term floor:** every lexicon term but the placeholders and the other bucket's phrase is found, word-bounded in a
   folded sentence, in at least N corpus records at at least S sites (:func:`term_presence`, which reads the folded
   sentences directly rather than the drafter's candidate terms);
2. **value floor:** every category spelling in the value map, and every declared entity id, passes the same floor;
3. **values:** no string in any pack file, keys included, folds equal to a value of a forbidden, site or record-id
   column (every row of the export), and no lexicon term holds a folded site or forbidden value as whole words;
4. **text:** no string in any pack file holds ``ngram_tokens`` consecutive ``\\w+`` tokens (folded) that occur
   consecutively in any narrative of the export, training or test.

The result names each check with counts and the files where a failure sits, never the offending string: a failing
pack may hold exactly what must not be printed.
"""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..jsonio import strict_load
from ..packs.canonical import fold_phrase, folded, is_alnum, split_sentences, term_regex
from ..packs.loader import RESERVED
from .draft import (FIXTURES, PACK_FILES, Language, ValueIndex, Window, _cells, build_corpus, category_floor,
                    date_column, entity_id, load_language, load_params, load_template, load_written, window_rows)
from .exports import Export
from .roles import Roles

CHECKS = ("loader", "term_floor", "value_floor", "fold_equal", "term_contains", "ngram", "labels")
_TOKEN = re.compile(r"\w+")
_RUN = re.compile(r"[^\W_]+")


def json_strings(value: Any) -> Iterable[str]:
    """Every string of a parsed JSON value, object keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from json_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from json_strings(v)


def pack_strings(pack_dir: str | Path) -> list[tuple[str, str]]:
    """``(file, string)`` for every string and key of every pack file and fixture line."""
    pack_dir = Path(pack_dir)
    out: list[tuple[str, str]] = []
    for name in PACK_FILES:
        path = pack_dir / name
        if path.is_file():
            out += [(name, s) for s in json_strings(strict_load(path.read_bytes()))]
    fx = pack_dir / FIXTURES
    if fx.is_file():
        for line in fx.read_bytes().split(b"\n"):
            if line.strip():
                out += [(FIXTURES, s) for s in json_strings(strict_load(line))]
    return out


def _runs(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _RUN.finditer(text)]


def term_presence(narratives: Sequence[str], terms: Iterable[str]) -> list[set[str]]:
    """For each narrative, the given (folded) terms found word-bounded in one of its folded sentences. A term made of
    alphanumeric runs is looked up among the sentence's spans of as many whole runs; any other term by its regex."""
    terms = sorted(set(terms))
    by_runs: dict[int, set[str]] = {}
    other: list[str] = []
    for t in terms:
        spans = _runs(t)
        if t and is_alnum(t[0]) and is_alnum(t[-1]) and spans:
            by_runs.setdefault(len(spans), set()).add(t)
        else:
            other.append(t)
    patterns = [(t, term_regex([t])) for t in other]
    out = []
    for narrative in narratives:
        found: set[str] = set()
        for s, e in split_sentences(narrative):
            sent = fold_phrase(narrative[s:e])[0]
            spans = _runs(sent)
            for n, wanted in by_runs.items():
                for i in range(len(spans) - n + 1):
                    piece = sent[spans[i][0]:spans[i + n - 1][1]]
                    if piece in wanted:
                        found.add(piece)
            for t, pattern in patterns:
                if pattern is not None and pattern.search(sent):
                    found.add(t)
        out.append(found)
    return out


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(folded(text))


def ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    toks = tokens(text)
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def ngram_hits(strings: Iterable[str], narratives: Iterable[str], n: int) -> set[tuple[str, ...]]:
    """The n-grams of ``strings`` that occur in some narrative."""
    wanted: set[tuple[str, ...]] = set()
    for s in strings:
        wanted |= ngrams(s, n)
    if not wanted:
        return set()
    hits = set()
    for narrative in narratives:
        toks = tokens(narrative)
        for i in range(len(toks) - n + 1):
            g = tuple(toks[i:i + n])
            if g in wanted:
                hits.add(g)
    return hits


def column_names(settings: Mapping[str, Any]) -> set[str]:
    """M1's terms: every column name of either source in the settings (with and without a trailing ``[]``), less the
    words the loader reserves for the pipeline's own record fields."""
    names: set[str] = set()
    for arm in settings["arms"].values():
        names.update(arm["columns"])
        roles = arm["roles"]
        for key in ("record_id", "site", "date", "narrative", "category", "reporter"):
            if roles.get(key):
                names.add(roles[key])
        names.update(roles["entities"])
        names.update(roles["forbidden"])
    terms = set()
    for n in names:
        terms.add(n)
        terms.add(n.removesuffix("[]"))
    return {t for t in terms if t} - set(RESERVED)


def source_hits(source: str, terms: set[str]) -> list[str]:
    """Identifiers and whole string constants (docstrings aside) of Python ``source`` equal to a term."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    hits = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names = [node.arg]
        elif isinstance(node, ast.alias):
            names = [*node.name.split("."), *([node.asname] if node.asname else [])]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            names = [node.value]
        hits += [f"line {getattr(node, 'lineno', 0)}" for n in names if n in terms]
    return hits


def package_hits(package_dir: str | Path, terms: set[str]) -> dict[str, int]:
    """M1 over a package directory: per file, the identifiers, string constants (Python) or JSON strings and keys
    (data) equal to a term. Counts only."""
    out: dict[str, int] = {}
    root = Path(package_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            n = len(source_hits(path.read_text(encoding="utf-8"), terms))
        elif path.suffix == ".json":
            n = sum(1 for s in json_strings(json.loads(path.read_text(encoding="utf-8"))) if s in terms)
        else:
            continue
        if n:
            out[rel] = n
    return out


def label_hits(labels: Iterable[str], terms: set[str]) -> int:
    """M1 at run time: drafted category labels equal (case-sensitive) to a column name."""
    return sum(1 for label in labels if label in terms)


def check_pack(pack_dir: str | Path, export: Export, roles: Roles, window: Window, *,
               params: Mapping[str, Any] | None = None, lang: Language | None = None,
               template: Mapping[str, Any] | None = None, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rule 1.7 and the loader's check of one drafted pack against its export; ``passed`` is false when any check
    fails. With ``settings``, also M1's label scan."""
    params = params if params is not None else load_params()
    lang = lang if lang is not None else load_language(roles.language)
    template = template if template is not None else load_template()
    ids = template["ids.json"]
    pack_dir = Path(pack_dir)
    result: dict[str, Any] = {}
    pack, error = load_written(pack_dir)
    result["loader"] = {"passed": pack is not None, "error": error}
    dated = date_column(export, roles, lang, params)
    corpus = build_corpus(export, roles, window_rows(dated, window))
    vocab = json.loads((pack_dir / "vocabulary.json").read_text(encoding="utf-8"))
    mapping = json.loads((pack_dir / "mapping.json").read_text(encoding="utf-8"))
    terms: list[str] = []
    term_of: dict[str, str] = {}
    for p, pred in vocab["predicates"].items():
        for lexicon in pred["lexicon"].values():
            for t in lexicon:
                if p == ids["other_predicate"] or t.startswith(ids["placeholder_prefix"]):
                    continue
                terms.append(folded(t))
                term_of[folded(t)] = p
    presence = term_presence(corpus.narratives, terms)
    df: Counter[str] = Counter()
    sites: dict[str, set[str]] = {}
    for found, site in zip(presence, corpus.sites):
        for t in found:
            df[t] += 1
            if site is not None:
                sites.setdefault(t, set()).add(site)
    low = [t for t in terms if not category_floor(df[t], len(sites.get(t, ())), params)]
    result["term_floor"] = {"passed": not low, "terms": len(terms), "failures": len(low)}

    rows_of: Counter[str] = Counter()
    sites_of: dict[str, set[str]] = {}
    for cats, site in zip(corpus.categories, corpus.sites):
        for key in {folded(v) for v in cats}:
            rows_of[key] += 1
            if site is not None:
                sites_of.setdefault(key, set()).add(site)
    spellings = [s for spec in mapping["codes"] for s in (spec["value_map"] or {})]
    bad_values = [s for s in spellings
                  if not category_floor(rows_of[folded(s)], len(sites_of.get(folded(s), ())), params)]
    entity_fail = 0
    entity_count = 0
    scope_type = next(iter(template["vocabulary_base.json"]["entity_types"]))
    for t, et in vocab["entity_types"].items():
        if t == scope_type:
            continue
        column = next((c for c in roles.entities if c[:params["label_max_chars"]] == et["label"]), None)
        e_rows: Counter[str] = Counter()
        e_sites: dict[str, set[str]] = {}
        for cells, site in zip(corpus.entities[column] if column is not None else (), corpus.sites):
            for eid in {entity_id(v, ids) for v in cells}:
                e_rows[eid] += 1
                if site is not None:
                    e_sites.setdefault(eid, set()).add(site)
        for eid in et["ids"] or ():
            entity_count += 1
            if not category_floor(e_rows[eid], len(e_sites.get(eid, ())), params):
                entity_fail += 1
    result["value_floor"] = {"passed": not bad_values and not entity_fail, "spellings": len(spellings),
                             "failures": len(bad_values) + entity_fail, "entity_ids": entity_count}

    refused_equal: set[str] = set()
    refused_inside: set[str] = set()
    narratives: list[str] = []
    for r in range(len(export)):
        for name in (roles.record_id, roles.site, *roles.forbidden):
            for v in _cells(export.value(r, name)):
                f = folded(v)
                if f:
                    refused_equal.add(f)
                    if name != roles.record_id:
                        refused_inside.add(f)
        n = export.value(r, roles.narrative)
        if n is not None:
            narratives.append(n)
    strings = pack_strings(pack_dir)
    equal_files = sorted({name for name, s in strings if folded(s) in refused_equal})
    result["fold_equal"] = {"passed": not equal_files, "strings": len(strings),
                            "failures": sum(1 for _, s in strings if folded(s) in refused_equal),
                            "files": equal_files}
    inside = ValueIndex(refused_inside)
    lexicon_terms = [folded(t) for p in vocab["predicates"].values() for lex in p["lexicon"].values() for t in lex]
    contained = [t for t in lexicon_terms if inside.found_in(t) is not None]
    result["term_contains"] = {"passed": not contained, "terms": len(lexicon_terms), "failures": len(contained)}

    n = params["ngram_tokens"]
    hits = ngram_hits([s for _, s in strings], narratives, n)
    hit_files = sorted({name for name, s in strings if ngrams(s, n) & hits})
    result["ngram"] = {"passed": not hits, "tokens": n, "narratives": len(narratives), "failures": len(hits),
                       "files": hit_files}
    if settings is not None:
        names = column_names(settings)
        labels = [p["label"] for p in vocab["predicates"].values()]
        labels += [s for spec in mapping["codes"] for s in (spec["value_map"] or {})]
        k = label_hits(labels, names)
        result["labels"] = {"passed": k == 0, "labels": len(labels), "failures": k}
    passed = all(v["passed"] for v in result.values())
    pack_id = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))["id"]
    return {"kind": "onboard_check", "schema_version": 1, "pack": pack_id, "passed": passed,
            "checks": {k: result[k] for k in CHECKS if k in result}}
