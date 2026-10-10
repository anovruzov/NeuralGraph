"""The privacy floor of a drafted pack (D001 rule 1.7 as amended), the loader's check (M2) and the label scan of M1.

Every check is recomputed from the export, never taken from the drafter's counts:

1. **term floor:** every lexicon term but the placeholders and the other bucket's phrase is found, word-bounded in a
   folded sentence, in at least N corpus records at at least S sites (:func:`term_presence`, which reads the folded
   sentences directly rather than the drafter's candidate terms);
2. **value floor:** every category spelling in the value map, and every declared entity id, passes the same floor;
3. **refused strings and terms** (amendment A3, 3a): the strings the drafter derived from records, read from the
   written pack by position (:func:`derived_strings`: the specific predicates' ids, labels and placeholders, their
   code labels, the value map's spellings, the declared entity types' ids and aliases), and apart the learned lexicon
   terms. The export's one :class:`.draft.Refusal` (every row's record-id, site and forbidden values, the very
   function the drafter used) refuses none of them: a term by its term rule, every other string by its string rule.
   The template's own strings and keys are never compared with record values;
4. **template** (A3, 3b): every other string is the template's. :func:`rebuild_pack` fills the neutral template and
   the language file with the strings of 3 and the written pack must hold the same content, file by file;
5. **text:** no string in any pack file holds ``ngram_tokens`` consecutive ``\\w+`` tokens (folded) that occur
   consecutively in any narrative of the export, training or test.

The result names each check with counts, kinds and the files where a failure sits, never the offending string: a
failing pack may hold exactly what must not be printed.
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
from .draft import (FIXTURES, PACK_FILES, REFUSAL_KINDS, EntityType, Language, Plan, Refusal, Split, Window,
                    assemble_pack, build_corpus, category_floor, date_column, entity_id, export_refusal,
                    load_language, load_params, load_template, load_written, window_rows)
from .exports import Export
from .roles import Roles

CHECKS = ("loader", "term_floor", "value_floor", "refused_strings", "refused_terms", "template", "ngram", "labels")
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


def derived_strings(pack_dir: str | Path, template: Mapping[str, Any]) -> tuple[list[tuple[str, str]],
                                                                               list[tuple[str, str]]]:
    """Amendment A3, 3a: ``(file, string)`` for every string the drafter derived from records, read from the written
    pack by position (the specific predicates' ids, labels and placeholders, their code labels and predicates, the
    value map's spellings, the declared entity types' ids and aliases); and apart ``(file, term)`` for every learned
    lexicon term. The other bucket's label and phrase and every template string are left out: none comes from a
    record."""
    pack_dir = Path(pack_dir)
    ids = template["ids.json"]
    other = ids["other_predicate"]
    scope_type = next(iter(template["vocabulary_base.json"]["entity_types"]))
    vocab = strict_load((pack_dir / "vocabulary.json").read_bytes())
    codes = strict_load((pack_dir / "codes.json").read_bytes())
    mapping = strict_load((pack_dir / "mapping.json").read_bytes())
    aliases = strict_load((pack_dir / "aliases.json").read_bytes())
    strings: list[tuple[str, str]] = []
    terms: list[tuple[str, str]] = []
    for p, pred in vocab["predicates"].items():
        if p == other:
            continue
        strings += [("vocabulary.json", p), ("vocabulary.json", pred["label"])]
        for lexicon in pred["lexicon"].values():
            for t in lexicon:
                (strings if t.startswith(ids["placeholder_prefix"]) else terms).append(("vocabulary.json", t))
    for spec in codes.values():
        if spec["predicate"] != other:
            strings += [("codes.json", spec["label"]), ("codes.json", spec["predicate"])]
    for spec in mapping["codes"]:
        strings += [("mapping.json", s) for s in (spec["value_map"] or {})]
    for t, et in vocab["entity_types"].items():
        if t != scope_type:
            strings += [("vocabulary.json", e) for e in et["ids"] or ()]
    for t, table in aliases.items():
        if t != scope_type:
            for alias, eid in table.items():
                strings += [("aliases.json", alias), ("aliases.json", eid)]
    return strings, terms


def refusal_result(items: Sequence[tuple[str, str]], refuse: Any, what: str) -> dict[str, Any]:
    """One refusal check's result: counts by kind and the files where a refused string sits, never the string."""
    kinds: Counter[str] = Counter()
    files: set[str] = set()
    for name, s in items:
        kind = refuse(s)
        if kind is not None:
            kinds[kind] += 1
            files.add(name)
    return {"passed": not kinds, what: len(items), "failures": sum(kinds.values()),
            "kinds": {k: kinds[k] for k in REFUSAL_KINDS}, "files": sorted(files)}


def rebuild_pack(loaded: Mapping[str, Any], lang: Language, template: Mapping[str, Any],
                 params: Mapping[str, Any], roles: Roles) -> dict[str, Any]:
    """Amendment A3, 3b: the pack the neutral template and the language file make from the written pack's own derived
    strings (predicate ids, code labels, value map, lexicons, entity types and aliases) and its id."""
    ids = template["ids.json"]
    scope_type = next(iter(template["vocabulary_base.json"]["entity_types"]))
    vocab, codes = loaded["vocabulary.json"], loaded["codes.json"]
    pred_code = {spec["predicate"]: code for code, spec in codes.items() if code != ids["other_code"]}
    pids = tuple(sorted(pred_code, key=lambda p: pred_code[p]))
    plan = Plan(ids=pids, of_key={}, labels={p: codes[pred_code[p]]["label"] for p in pids},
                codes={p: pred_code[p] for p in pids}, counts={},
                value_map=dict(loaded["mapping.json"]["codes"][0]["value_map"] or {}),
                other_id=ids["other_predicate"], other_code=ids["other_code"], split=Split((), (), ()))
    lexicon = {p: list(vocab["predicates"][p]["lexicon"][lang.code]) for p in pids}
    etypes = [EntityType(id=t, column=et["label"], label=et["label"], ids=tuple(et["ids"] or ()),
                         aliases=dict(loaded["aliases.json"].get(t, {})))
              for t, et in sorted(vocab["entity_types"].items()) if t != scope_type]
    return assemble_pack(loaded["pack.json"]["id"], plan, lexicon, lang, template, params, roles, etypes)


def template_result(pack_dir: str | Path, lang: Language, template: Mapping[str, Any], params: Mapping[str, Any],
                    roles: Roles) -> dict[str, Any]:
    """Amendment A3, 3b: the files whose content differs from :func:`rebuild_pack`'s, or the error that stopped the
    rebuild (a pack the template cannot make)."""
    pack_dir = Path(pack_dir)
    try:
        loaded: dict[str, Any] = {name: strict_load((pack_dir / name).read_bytes()) for name in PACK_FILES}
        loaded[FIXTURES] = [strict_load(line) for line in (pack_dir / FIXTURES).read_bytes().split(b"\n")
                            if line.strip()]
        rebuilt = rebuild_pack(loaded, lang, template, params, roles)
    except (OSError, KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration) as err:
        return {"passed": False, "files": [], "error": err.__class__.__name__}
    differ = [name for name in (*PACK_FILES, FIXTURES) if json.loads(json.dumps(rebuilt[name])) != loaded[name]]
    return {"passed": not differ, "files": differ, "error": None}


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


REPO = Path(__file__).resolve().parents[3]


def earlier_settings(settings: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """D003's change P12: the settings files the settings list under ``m1_settings`` (repository-relative paths), each
    read as it is. Settings without the list (D001's, D002's) name none."""
    out = []
    for rel in settings.get("m1_settings") or ():
        path = Path(rel)
        out.append(strict_load((path if path.is_absolute() else REPO / path).read_bytes()))
    return out


def exempt_labels(params: Mapping[str, Any]) -> frozenset[str]:
    """D003's change P13 (E15): the folded ``non_specific_labels`` of the parameters, FDA's public generic terms. A
    string that folds equal to one is left out of rule 1.7's n-gram check and of the last guard's n-gram scan; every
    other check still reads it. Parameters without them (D001's, D002's) exempt nothing."""
    return frozenset(folded(w) for w in params.get("non_specific_labels") or ())


def within_spellings(hits: Iterable[tuple[str, ...]], spellings: Iterable[str], n: int) -> int:
    """D003's change P13: how many n-gram hits lie wholly within one of the given value-map spellings."""
    grams: set[tuple[str, ...]] = set()
    for s in spellings:
        grams |= ngrams(s, n)
    return sum(1 for g in hits if g in grams)


def column_names(settings: Mapping[str, Any]) -> set[str]:
    """M1's terms: every column name of either source in the settings (with and without a trailing ``[]``), less the
    words the loader reserves for the pipeline's own record fields. With D003's change P12, the column names of the
    earlier settings files the settings list join them."""
    names: set[str] = set()
    for earlier in earlier_settings(settings):
        names |= column_names(earlier)
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

    refusal = export_refusal(export, roles, params)
    derived, learned = derived_strings(pack_dir, template)
    result["refused_strings"] = refusal_result(derived, refusal.string, "strings")
    result["refused_terms"] = refusal_result(learned, refusal.term, "terms")
    result["template"] = template_result(pack_dir, lang, template, params, roles)

    narratives = [n for n in (export.value(r, roles.narrative) for r in range(len(export))) if n is not None]
    strings = pack_strings(pack_dir)
    n = params["ngram_tokens"]
    exempt = exempt_labels(params)
    scanned = [(name, s) for name, s in strings if folded(s) not in exempt]
    hits = ngram_hits([s for _, s in scanned], narratives, n)
    hit_files = sorted({name for name, s in scanned if ngrams(s, n) & hits})
    result["ngram"] = {"passed": not hits, "tokens": n, "narratives": len(narratives), "failures": len(hits),
                       "files": hit_files}
    if exempt:
        spellings = [s for spec in mapping["codes"] for s in (spec["value_map"] or {})]
        result["ngram"]["exempt_strings"] = len(strings) - len(scanned)
        result["ngram"]["within_value_map"] = within_spellings(hits, spellings, n)
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
