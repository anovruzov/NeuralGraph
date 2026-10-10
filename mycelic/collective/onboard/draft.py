"""Drafting a pack from one export: D001 rules 1.3 to 1.6, and the two control lexicons of rule 4.

The output is a function of the export, the roles, the parameters (``data/defaults.json``), the language file
(``data/lang/<code>.json``) and the neutral template (``data/template/``): no clock, no unseeded randomness, every list
written in a fixed order, JSON with sorted keys. Each rule is one function here, named after it:

* rule 1.3, the training rows: :func:`date_column`, :func:`window_rows`, :func:`build_corpus` (the date column is
  read in every row, and so are the record-id, site and forbidden columns, only to refuse; every other value only in
  a training row with a narrative);
* amendment A2, the one refusal: :class:`Refusal`, built by :func:`export_refusal` from every row's record-id, site
  and forbidden values of one company's export; the drafter, the check, the label-names control and the last guard
  all use it (amendments A10 and A13); :func:`refusal_removed` counts what it removed (amendment A14);
* rule 1.4, the predicates: :func:`group_categories`, :func:`category_floor`, :func:`split_categories`,
  :func:`predicate_ids`, :func:`plan_predicates` (codes and the value map; refused categories left out);
* rule 1.5, the lexicon: :func:`candidate_terms`, :func:`term_table`, :func:`eligible_terms`, :func:`term_score`,
  :func:`assign_terms`, :func:`placeholder`;
* rule 1.6, the rest of the pack: :func:`entity_types`, :func:`assemble_pack`, :func:`fixtures`, and the normalised
  export :func:`normalised_rows`;
* rule 4, the controls: :func:`permuted_labels` (then :func:`assign_terms` again) and :func:`label_name_lexicon`
  (through the refusal, amendment A13; :func:`refused_label_parts` counts the parts it removed).
"""
from __future__ import annotations

import json
import random
import re
import unicodedata
from array import array
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..jsonio import sha256_hex, strict_load
from ..packs.canonical import find_bounded, fold_phrase, folded, is_alnum, split_sentences
from ..packs.connector import MAX_REF, SITE_ID_RE
from ..packs.loader import RESERVED, PackError, load_pack_dir
from .exports import Export, is_list_column
from .roles import Roles, check_roles, choose_date_format, column_evidence, infer_roles, parse_date, roles_right

DATA_DIR = Path(__file__).resolve().parent / "data"
TEMPLATE_DIR = DATA_DIR / "template"
LANG_DIR = DATA_DIR / "lang"
DEFAULTS = DATA_DIR / "defaults.json"
TEMPLATE_FILES = ("vocabulary_base.json", "egress.json", "detectors.json", "questions.json", "followups.json",
                  "rules.json", "generator_base.json", "ids.json")
PACK_FILES = ("pack.json", "vocabulary.json", "codes.json", "aliases.json", "mapping.json", "egress.json",
              "detectors.json", "rules.json", "questions.json", "followups.json", "generator.json")
FIXTURES = "fixtures/records.jsonl"
MIN_FIXTURES = 40
VALUE_KEY_MAX = 200
VALUE_MAP_MAX = 1000
CODE_LABEL_MAX = 120
_TOKEN = re.compile(r"\w+")
_ALNUM_RUN = re.compile(r"[^\W_]+")
_SLUG_RUN = re.compile(r"[^a-z0-9]+")
_ENTITY_RUN = re.compile(r"[^A-Z0-9]+")


class DraftError(ValueError):
    """The export cannot be drafted under the rule (no date format, no specific predicate, a value too long)."""


# --------------------------------------------------------------------------------------------------- data files

def _load_json(path: Path) -> Any:
    return strict_load(path.read_bytes())


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    return _load_json(Path(path) if path is not None else DEFAULTS)


def load_template() -> dict[str, Any]:
    return {name: _load_json(TEMPLATE_DIR / name) for name in TEMPLATE_FILES}


@dataclass(frozen=True)
class Language:
    code: str
    stop_words: frozenset[str]
    negation: Mapping[str, tuple[str, ...]]
    negation_words: frozenset[str]
    joining_words: tuple[str, ...]
    non_specific: frozenset[str]
    months: tuple[str, ...]
    site_words: tuple[str, ...]
    category_words: tuple[str, ...]
    other_label: str
    other_phrase: str
    scope_label: str
    scope_alias: str
    templates: Mapping[str, Any]


def load_language(code: str) -> Language:
    if re.fullmatch(r"[a-z]{2,3}", code or "") is None:
        raise DraftError(f"unknown language {code!r}")
    path = LANG_DIR / f"{code}.json"
    if not path.is_file():
        raise DraftError(f"no language file for {code!r}")
    raw = _load_json(path)
    negation = {k: tuple(raw["negation"][k]) for k in ("pre", "post", "terminators")}
    words = {w for cues in negation.values() for cue in cues for w in _TOKEN.findall(folded(cue))}
    return Language(
        code=raw["language"], stop_words=frozenset(folded(w) for w in raw["stop_words"]), negation=negation,
        negation_words=frozenset(words), joining_words=tuple(raw["joining_words"]),
        non_specific=frozenset(folded(w) for w in raw["non_specific_labels"]), months=tuple(raw["months"]),
        site_words=tuple(raw["site_words"]), category_words=tuple(raw["category_words"]),
        other_label=raw["other_bucket"]["label"], other_phrase=raw["other_bucket"]["phrase"],
        scope_label=raw["scope"]["label"], scope_alias=raw["scope"]["alias"], templates=raw["templates"])


def at_least(k: int, n: int, share: float) -> bool:
    """``k / n >= share``, exactly (the share read as its decimal text)."""
    return n > 0 and Fraction(k, n) >= Fraction(str(share))


# --------------------------------------------------------------------------------------------------- rule 1.3

@dataclass(frozen=True)
class Window:
    first: date
    last: date

    def contains(self, day: date | None) -> bool:
        return day is not None and self.first <= day <= self.last

    def to_json(self) -> list[str]:
        return [self.first.isoformat(), self.last.isoformat()]


def parse_window(first: str, last: str) -> Window:
    try:
        w = Window(date.fromisoformat(first), date.fromisoformat(last))
    except (TypeError, ValueError):
        raise DraftError("a window is two dates YYYY-MM-DD") from None
    if w.first > w.last:
        raise DraftError("a window's first date is after its last")
    return w


@dataclass(frozen=True)
class Dated:
    format: str
    dates: tuple[date | None, ...]
    rejected: int


def _scalar_column(export: Export, name: str, role: str) -> None:
    if is_list_column(name):
        raise DraftError(f"the {role} column cannot be a list column")


def date_column(export: Export, roles: Roles, lang: Language, params: Mapping[str, Any]) -> Dated:
    """Rule 1.3: the date column's format (the first that parses at least the share of its values over the whole
    export) and every row's date; the one field read in every row."""
    _scalar_column(export, roles.date, "date")
    values = [export.value(r, roles.date) for r in range(len(export))]
    fmt = choose_date_format([v for v in values if v is not None], lang.months, params["date_min_share"])
    if fmt is None:
        raise DraftError("no date format parses enough of the date column's values")
    dates = tuple(parse_date(v, fmt, lang.months) for v in values)
    return Dated(format=fmt, dates=dates, rejected=sum(d is None for d in dates))


def window_rows(dated: Dated, window: Window) -> list[int]:
    return [r for r, d in enumerate(dated.dates) if window.contains(d)]


def _cells(cell: Any) -> tuple[str, ...]:
    if cell is None:
        return ()
    return cell if isinstance(cell, tuple) else (cell,)


@dataclass(frozen=True)
class Corpus:
    rows: tuple[int, ...]
    refs: tuple[str, ...]
    sites: tuple[str | None, ...]
    narratives: tuple[str, ...]
    categories: tuple[tuple[str, ...], ...]
    entities: Mapping[str, tuple[tuple[str, ...], ...]]
    training_rows: int
    without_narrative: int

    def __len__(self) -> int:
        return len(self.rows)


def build_corpus(export: Export, roles: Roles, rows: Sequence[int]) -> Corpus:
    """Rule 1.3: the training rows with a non-empty narrative. A training row without one is ignored: none of its
    other values is read. The refused values are read apart, by :func:`export_refusal` (amendment A2)."""
    for name, role in ((roles.narrative, "narrative"), (roles.site, "site"), (roles.record_id, "record_id")):
        _scalar_column(export, name, role)
    keep, refs, sites, narratives, categories = [], [], [], [], []
    entities: dict[str, list[tuple[str, ...]]] = {c: [] for c in roles.entities}
    without = 0
    for r in rows:
        narrative = export.value(r, roles.narrative)
        if narrative is None:
            without += 1
            continue
        keep.append(r)
        narratives.append(narrative)
        refs.append(export.value(r, roles.record_id) or "")
        site = export.value(r, roles.site)
        sites.append(site)
        categories.append(_cells(export.value(r, roles.category)))
        for c in roles.entities:
            entities[c].append(_cells(export.value(r, c)))
    return Corpus(rows=tuple(keep), refs=tuple(refs), sites=tuple(sites), narratives=tuple(narratives),
                  categories=tuple(categories), entities={c: tuple(v) for c, v in entities.items()},
                  training_rows=len(rows), without_narrative=without)


# --------------------------------------------------------------------------------------------------- amendment A2

REFUSAL_KINDS = ("equal", "inside", "name_word")


class ValueIndex:
    """Values to find inside a text as whole words (``find_bounded``), indexed by their first alphanumeric run: a
    value that starts with a letter or digit can only match where that run is a whole run of the text."""

    def __init__(self, values: Iterable[str]) -> None:
        self.by_run: dict[str, list[str]] = {}
        self.general: list[str] = []
        for v in sorted(set(values)):
            if not v:
                continue
            if is_alnum(v[0]):
                self.by_run.setdefault(_ALNUM_RUN.match(v).group(), []).append(v)
            else:
                self.general.append(v)

    def found_in(self, text: str) -> str | None:
        for run in sorted(set(_ALNUM_RUN.findall(text))):
            for v in self.by_run.get(run, ()):
                if find_bounded(v, text):
                    return v
        for v in self.general:
            if find_bounded(v, text):
                return v
        return None

    def found_all(self, text: str) -> list[str]:
        """Every value found in ``text`` as whole words, sorted."""
        out = [v for run in sorted(set(_ALNUM_RUN.findall(text))) for v in self.by_run.get(run, ())
               if find_bounded(v, text)]
        out += [v for v in self.general if find_bounded(v, text)]
        return sorted(set(out))


def words_of(text: str) -> list[str]:
    """The words of a folded string: its runs of letters and digits."""
    return _ALNUM_RUN.findall(text)


class Refusal:
    """Amendment A2: what no string taken from records may carry. Built from the folded values of an export's
    record-id, site and forbidden columns (every row), it refuses a string that, folded,

    1. ``equal``: equals one of those values;
    2. ``inside``: holds, as whole words, a forbidden value of at least ``forbidden_inside_min_chars`` characters that
       holds a letter, or a record-id or site value of at least ``reference_inside_min_chars`` characters;
    3. ``name_word``: equals a word (letters only, at least ``name_word_min_letters`` of them) of a forbidden value of
       two or more words: a surname inside a name.

    A lexicon term is refused when it or any of its words is (:meth:`term`). The drafter, the check (rule 1.7), the
    label-names control (rule 4, amendment A13) and the last guard (rule 8, amendment A10) all use this one class,
    each over one company's export."""

    def __init__(self, references: Iterable[str], forbidden: Iterable[str], params: Mapping[str, Any]) -> None:
        p = params["refusal"]
        refs = {f for f in (folded(v) for v in references) if f}
        bad = {f for f in (folded(v) for v in forbidden) if f}
        self.equal = frozenset(refs | bad)
        self.inside = ValueIndex([v for v in bad if len(v) >= p["forbidden_inside_min_chars"]
                                  and any(ch.isalpha() for ch in v)]
                                 + [v for v in refs if len(v) >= p["reference_inside_min_chars"]])
        names: set[str] = set()
        for v in bad:
            words = words_of(v)
            if len(words) >= 2:
                names.update(w for w in words if w.isalpha() and len(w) >= p["name_word_min_letters"])
        self.name_words = frozenset(names)

    def string(self, text: str) -> str | None:
        """The kind of refusal of one string (a label, a spelling, an id, an alias), or None."""
        f = folded(text)
        if not f:
            return None
        if f in self.equal:
            return "equal"
        if self.inside.found_in(f) is not None:
            return "inside"
        if f in self.name_words:
            return "name_word"
        return None

    def term(self, text: str) -> str | None:
        """The kind of refusal of a lexicon term: the term itself, then each of its words."""
        kind = self.string(text)
        if kind is not None:
            return kind
        for w in words_of(folded(text)):
            kind = self.string(w)
            if kind is not None:
                return kind
        return None


def refused_values(export: Export, roles: Roles) -> tuple[list[str], list[str]]:
    """Amendment A2: the values of the record-id and site columns, and of the forbidden columns, in every row. These
    columns are read in every row for this one purpose (rule 1.3 as amended). A declared column the export lacks has
    no value: the drafter refuses such an export before it gets here, and the last guard still reads the rest."""
    references: list[str] = []
    forbidden: list[str] = []
    ref_columns = [name for name in (roles.record_id, roles.site) if export.has(name)]
    bad_columns = [name for name in roles.forbidden if export.has(name)]
    for r in range(len(export)):
        for name in ref_columns:
            references.extend(_cells(export.value(r, name)))
        for name in bad_columns:
            forbidden.extend(_cells(export.value(r, name)))
    return references, forbidden


def export_refusal(export: Export, roles: Roles, params: Mapping[str, Any]) -> Refusal:
    """The one refusal of an export (amendment A2): the drafter, the check, the label-names control and the last
    guard each build it with this function from the same company's export (amendment A10)."""
    return Refusal(*refused_values(export, roles), params)


# --------------------------------------------------------------------------------------------------- rule 1.4

@dataclass(frozen=True)
class Category:
    key: str
    label: str
    spellings: tuple[str, ...]
    count: int
    sites: int


def group_categories(corpus: Corpus) -> list[Category]:
    """Rule 1.4: values that fold equal are one category, labelled by its most frequent spelling (ties: the smaller
    string); its count is the number of corpus rows that carry it, its sites the distinct sites of those rows. Sorted
    by descending count, ties by label."""
    rows_of: Counter[str] = Counter()
    sites_of: dict[str, set[str]] = {}
    spelled: dict[str, Counter[str]] = {}
    for cats, site in zip(corpus.categories, corpus.sites):
        keys: set[str] = set()
        for value in dict.fromkeys(cats):
            key = folded(value)
            if not key:
                continue
            spelled.setdefault(key, Counter())[value] += 1
            if key not in keys:
                keys.add(key)
                rows_of[key] += 1
                if site is not None:
                    sites_of.setdefault(key, set()).add(site)
    out = []
    for key in rows_of:
        spellings = spelled[key]
        label = min(spellings, key=lambda s: (-spellings[s], s))
        out.append(Category(key=key, label=label, spellings=tuple(sorted(spellings)), count=rows_of[key],
                            sites=len(sites_of.get(key, ()))))
    return sorted(out, key=lambda c: (-c.count, c.label))


def category_floor(count: int, sites: int, params: Mapping[str, Any]) -> bool:
    """The privacy floor (rules 1.4 and 1.5): at least N records at at least S sites."""
    return count >= params["floor_records"] and sites >= params["floor_sites"]


@dataclass(frozen=True)
class Split:
    specific: tuple[Category, ...]
    other: tuple[Category, ...]
    below: tuple[Category, ...]


def split_categories(categories: Sequence[Category], params: Mapping[str, Any], lang: Language) -> Split:
    """Rule 1.4: categories under the floor are left out; of the rest, those with at least R records and a specific
    label are predicates (at most the cap, the largest counts first, ties by label); every other one goes to the other
    bucket."""
    floor = [c for c in categories if category_floor(c.count, c.sites, params)]
    below = tuple(c for c in categories if not category_floor(c.count, c.sites, params))
    ranked = sorted(floor, key=lambda c: (-c.count, c.label))
    candidates = [c for c in ranked if c.count >= params["predicate_min_records"] and c.key not in lang.non_specific]
    specific = tuple(candidates[:params["max_predicates"]])
    chosen = {c.key for c in specific}
    return Split(specific=specific, other=tuple(c for c in ranked if c.key not in chosen), below=below)


def slug(text: str, params: Mapping[str, Any], prefix: str) -> str:
    """Rule 1.4's id of a label: folded to ASCII, lower-cased, every run outside ``a-z0-9`` as ``_``, ``_`` stripped,
    cut; the prefix in front when it is shorter than 2 characters, starts with a digit or is a reserved word."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = _SLUG_RUN.sub("_", ascii_text).strip("_")[:params["id_max_chars"]]
    if len(s) < 2 or s[0].isdigit() or s in RESERVED:
        s = prefix + s
    return s


def predicate_ids(specific: Sequence[Category], params: Mapping[str, Any], ids: Mapping[str, Any]) -> list[str]:
    """Rule 1.4: one id per specific predicate, in its order; the other bucket's id is taken first and a repeat gets
    ``_2``, ``_3`` and so on."""
    taken = {ids["other_predicate"]}
    out = []
    for c in specific:
        base = slug(c.label, params, ids["id_prefix"])
        pid, n = base, 2
        while pid in taken:
            pid, n = f"{base}_{n}", n + 1
        taken.add(pid)
        out.append(pid)
    return out


@dataclass(frozen=True)
class Plan:
    """The predicates of one draft: ``ids`` in code order (descending count, ties by label). ``refused`` holds the
    categories that passed the floor but were left out because a string of theirs is refused (amendment A2)."""

    ids: tuple[str, ...]
    of_key: Mapping[str, str]
    labels: Mapping[str, str]
    codes: Mapping[str, str]
    counts: Mapping[str, int]
    value_map: Mapping[str, str]
    other_id: str
    other_code: str
    split: Split
    refused: tuple[Category, ...] = ()


def category_strings(c: Category, pid: str | None, params: Mapping[str, Any], ids: Mapping[str, Any]) -> list[str]:
    """Every string the drafter would write or print for a category (amendment A2): its spellings (its label among
    them); for a predicate also its label as cut, its id and its placeholder."""
    out = list(c.spellings)
    if pid is not None:
        out += [c.label[:params["label_max_chars"]], c.label[:CODE_LABEL_MAX], pid, placeholder(pid, ids)]
    return out


def plan_predicates(categories: Sequence[Category], params: Mapping[str, Any], lang: Language,
                    ids: Mapping[str, Any], refusal: Refusal | None = None) -> Plan:
    """Rule 1.4 with amendment A2: a category any of whose strings the refusal refuses is left out, as if under the
    floor, and the predicates are planned again without it (a removal can change other ids and the cap)."""
    refused: dict[str, Category] = {}
    while True:
        split = split_categories([c for c in categories if c.key not in refused], params, lang)
        if not split.specific:
            raise DraftError("no category reaches the predicate minimum: a pack needs one specific predicate")
        pids = predicate_ids(split.specific, params, ids)
        if refusal is None:
            break
        pairs = [*zip(split.specific, pids), *((c, None) for c in split.other)]
        newly = [c for c, pid in pairs
                 if any(refusal.string(x) is not None for x in category_strings(c, pid, params, ids))]
        if not newly:
            break
        refused.update((c.key, c) for c in newly)
    codes = {p: ids["code_format"].format(i) for i, p in enumerate(pids, start=1)}
    value_map: dict[str, str] = {}
    for c, p in zip(split.specific, pids):
        for s in c.spellings:
            value_map[s] = codes[p]
    for c in split.other:
        for s in c.spellings:
            value_map[s] = ids["other_code"]
    if any(len(s) > VALUE_KEY_MAX for s in value_map):
        raise DraftError(f"a category spelling is longer than {VALUE_KEY_MAX} characters")
    if len(value_map) > VALUE_MAP_MAX:
        raise DraftError(f"more than {VALUE_MAP_MAX} category spellings pass the floor")
    return Plan(ids=tuple(pids), of_key={c.key: p for c, p in zip(split.specific, pids)},
                labels={p: c.label for c, p in zip(split.specific, pids)}, codes=codes,
                counts={p: c.count for c, p in zip(split.specific, pids)},
                value_map=dict(sorted(value_map.items())), other_id=ids["other_predicate"],
                other_code=ids["other_code"], split=split,
                refused=tuple(sorted(refused.values(), key=lambda c: (-c.count, c.label))))


def record_labels(categories: Sequence[tuple[str, ...]], plan: Plan) -> list[tuple[str, ...]]:
    """The specific predicates each row is filed under (sorted)."""
    out = []
    for cats in categories:
        out.append(tuple(sorted({plan.of_key[k] for k in (folded(v) for v in cats) if k in plan.of_key})))
    return out


# --------------------------------------------------------------------------------------------------- rule 1.5

def qualifies(token: str, lang: Language, params: Mapping[str, Any]) -> bool:
    """Rule 1.5: letters only, at least the minimum letters, not a stop word, not a word of a negation cue."""
    return (token.isalpha() and len(token) >= params["token_min_letters"] and token not in lang.stop_words
            and token not in lang.negation_words)


def candidate_terms(narrative: str, lang: Language, params: Mapping[str, Any]) -> set[str]:
    """Rule 1.5: the unigrams and bigrams of one narrative, split into sentences and folded as the extractor does; a
    bigram is two qualifying tokens next to each other with exactly one space between them. A term longer than the
    loader's limit is not a candidate (amendment A4)."""
    out: set[str] = set()
    longest = params["term_max_chars"]
    for s, e in split_sentences(narrative):
        sent = fold_phrase(narrative[s:e])[0]
        prev: tuple[str, int, bool] | None = None
        for m in _TOKEN.finditer(sent):
            tok = m.group()
            q = qualifies(tok, lang, params)
            if q:
                if len(tok) <= longest:
                    out.add(tok)
                if prev is not None and prev[2] and sent[prev[1]:m.start()] == " ":
                    bigram = f"{prev[0]} {tok}"
                    if len(bigram) <= longest:
                        out.add(bigram)
            prev = (tok, m.end(), q)
    return out


@dataclass(frozen=True)
class TermTable:
    terms: tuple[str, ...]
    records: tuple[array, ...]
    df: tuple[int, ...]
    sites: tuple[int, ...]


def term_table(corpus: Corpus, lang: Language, params: Mapping[str, Any]) -> TermTable:
    """Each corpus record's candidate terms (as ids), and per term the records containing it and its sites (counted
    up to S, all the floor needs)."""
    vocab: dict[str, int] = {}
    terms: list[str] = []
    records: list[array] = []
    df: list[int] = []
    sites: list[set[str] | None] = []
    cap = params["floor_sites"]
    for narrative, site in zip(corpus.narratives, corpus.sites):
        ids = []
        for t in candidate_terms(narrative, lang, params):
            tid = vocab.get(t)
            if tid is None:
                tid = vocab[t] = len(terms)
                terms.append(t)
                df.append(0)
                sites.append(set())
            ids.append(tid)
            df[tid] += 1
            seen = sites[tid]
            if seen is not None and site is not None:
                seen.add(site)
                if len(seen) >= cap:
                    sites[tid] = None
        records.append(array("i", sorted(ids)))
    return TermTable(terms=tuple(terms), records=tuple(records), df=tuple(df),
                     sites=tuple(cap if s is None else len(s) for s in sites))


def eligible_terms(table: TermTable, refusal: Refusal, params: Mapping[str, Any]) -> tuple[list[int], list[int]]:
    """Rule 1.5's floor: a term in at least N records at at least S sites that the refusal does not refuse
    (amendment A2). Also the terms that passed the floor and were refused (amendment A14 counts them)."""
    out, refused = [], []
    for tid, term in enumerate(table.terms):
        if not category_floor(table.df[tid], table.sites[tid], params):
            continue
        if refusal.term(term) is not None:
            refused.append(tid)
            continue
        out.append(tid)
    return out, refused


def term_score(df_tc: int, df_t: int) -> Fraction:
    """p(c | t) = df(t, c) / df(t)."""
    return Fraction(df_tc, df_t)


def assign_terms(table: TermTable, eligible: Sequence[int], labels: Sequence[tuple[str, ...]],
                 pids: Sequence[str], params: Mapping[str, Any]) -> dict[str, list[str]]:
    """Rule 1.5: each eligible term goes to the predicate with the largest p(c | t) (ties: larger df(t, c), then the
    smaller id) when df(t, c) and p(c | t) reach their minimums; each predicate keeps at most K terms (largest
    df(t, c), then larger p(c | t), then the term)."""
    allowed = set(eligible)
    counts: dict[int, dict[str, int]] = {}
    for tids, preds in zip(table.records, labels):
        if not preds:
            continue
        for tid in tids:
            if tid in allowed:
                d = counts.setdefault(tid, {})
                for p in preds:
                    d[p] = d.get(p, 0) + 1
    p_min = Fraction(str(params["term_min_p"]))
    chosen: dict[str, list[tuple[int, Fraction, str]]] = {p: [] for p in pids}
    for tid in sorted(counts):
        best, k = min(counts[tid].items(), key=lambda kv: (-kv[1], kv[0]))
        score = term_score(k, table.df[tid])
        if k >= params["term_min_df"] and score >= p_min and best in chosen:
            chosen[best].append((k, score, table.terms[tid]))
    out = {}
    for p in pids:
        ranked = sorted(chosen[p], key=lambda x: (-x[0], -x[1], x[2]))
        out[p] = [t for _, _, t in ranked[:params["max_terms"]]]
    return out


def placeholder(pid: str, ids: Mapping[str, Any]) -> str:
    """Rule 1.5: the term of a predicate that learned none."""
    return ids["placeholder_prefix"] + pid.replace("_", "-")


def with_placeholders(lexicon: Mapping[str, list[str]], ids: Mapping[str, Any]) -> dict[str, list[str]]:
    return {p: (list(terms) if terms else [placeholder(p, ids)]) for p, terms in lexicon.items()}


# --------------------------------------------------------------------------------------------------- rule 4 controls

def permuted_labels(corpus: Corpus, seed: str) -> list[tuple[str, ...]]:
    """Rule 4: the corpus rows' category values, in record-id order, shuffled by ``random.Random(seed)`` and given
    back to the rows in that order. The counts stay; the link between text and label goes."""
    order = sorted(range(len(corpus)), key=lambda i: corpus.refs[i])
    values = [corpus.categories[i] for i in order]
    random.Random(seed).shuffle(values)
    out: list[tuple[str, ...]] = [()] * len(corpus)
    for pos, i in enumerate(order):
        out[i] = values[pos]
    return out


def label_parts(label: str, lang: Language) -> list[str]:
    """Rule 4: the folded label split at ``/``, ``,``, ``;``, brackets and the joining words; each part of 3 to 64
    characters that is not a stop word."""
    joins = "|".join(re.escape(w) for w in sorted(lang.joining_words))
    splitter = re.compile(r"[/,;()\[\]{}]" + (rf"|(?<![^\W_])(?:{joins})(?![^\W_])" if joins else ""))
    out: list[str] = []
    for part in splitter.split(folded(label)):
        p = " ".join(part.split())
        if 3 <= len(p) <= 64 and p not in lang.stop_words and p not in out:
            out.append(p)
    return out


def label_name_lexicon(plan: Plan, lang: Language, refusal: Refusal | None = None) -> dict[str, list[str]]:
    """Rule 4: each lexicon replaced by the words of its label; a part several labels share goes to the predicate with
    the most corpus rows (ties: the smaller id). Amendment A13: a part the refusal's term rule refuses is no term, as
    in the drafted and permuted lexicons (a refusal depends on the part alone, so it never moves a shared part)."""
    owner: dict[str, str] = {}
    for p in sorted(plan.ids, key=lambda p: (-plan.counts[p], p)):
        for part in label_parts(plan.labels[p], lang):
            owner.setdefault(part, p)
    return {p: [part for part in label_parts(plan.labels[p], lang)
                if owner[part] == p and (refusal is None or refusal.term(part) is None)] for p in plan.ids}


def refused_label_parts(plan: Plan, lang: Language, refusal: Refusal) -> int:
    """Amendment A13: how many distinct label parts the refusal takes out of the label-names control."""
    return len({part for p in plan.ids for part in label_parts(plan.labels[p], lang)
                if refusal.term(part) is not None})


# --------------------------------------------------------------------------------------------------- rule 1.6

@dataclass(frozen=True)
class EntityType:
    id: str
    column: str
    label: str
    ids: tuple[str, ...]
    aliases: Mapping[str, str]


def entity_id(value: str, ids: Mapping[str, Any]) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").upper()
    body = _ENTITY_RUN.sub("-", ascii_text).strip("-")
    return ids["entity_id_prefix"] + body if body else ""


def entity_types(corpus: Corpus, roles: Roles, params: Mapping[str, Any], ids: Mapping[str, Any],
                 taken: Iterable[str], refusal: Refusal | None = None) -> tuple[list[EntityType], int]:
    """Rule 1.6: a declared entity column becomes an alias-only type whose ids are its values that pass the floor,
    written in capitals with every run of other characters as ``-`` and the prefix; a value whose id is too long or
    repeats another's is left out, and so is one that the refusal refuses, or whose id it refuses (amendment A2); at
    most the most frequent are kept (ties by id). Its aliases are its values. Also the number of values refused."""
    used = set(taken)
    out = []
    refused = 0
    for column in roles.entities:
        tid, n = slug(column, params, ids["id_prefix"]), 2
        base = tid
        while tid in used:
            tid, n = f"{base}_{n}", n + 1
        used.add(tid)
        rows: Counter[str] = Counter()
        sites: dict[str, set[str]] = {}
        for cells, site in zip(corpus.entities[column], corpus.sites):
            for v in dict.fromkeys(cells):
                rows[v] += 1
                if site is not None:
                    sites.setdefault(v, set()).add(site)
        passing = [v for v in rows if category_floor(rows[v], len(sites.get(v, ())), params)]
        ranked = sorted(passing, key=lambda v: (-rows[v], entity_id(v, ids), v))
        kept: dict[str, str] = {}
        for v in ranked:
            eid = entity_id(v, ids)
            if not eid or len(eid) > params["entity_id_max_chars"] or eid in kept.values():
                continue
            if refusal is not None and (refusal.string(v) is not None or refusal.string(eid) is not None):
                refused += 1
                continue
            kept[v] = eid
            if len(kept) >= params["entity_max_ids"]:
                break
        if kept:
            out.append(EntityType(id=tid, column=column, label=column[:params["label_max_chars"]],
                                  ids=tuple(sorted(kept.values())), aliases=dict(sorted(kept.items()))))
    return out, refused


def _fill(text: str, slot: str, value: str) -> str:
    return text.replace("{" + slot + "}", value)


def assemble_pack(pack_id: str, plan: Plan, lexicon: Mapping[str, list[str]], lang: Language,
                  template: Mapping[str, Any], params: Mapping[str, Any], roles: Roles,
                  etypes: Sequence[EntityType]) -> dict[str, Any]:
    """Rule 1.6: the pack's files (by name, plus :data:`FIXTURES` as a list of lines) from the plan, the lexicon (one
    list per specific predicate, placeholders included), the language file and the neutral template."""
    ids = template["ids.json"]
    keys = ids["normalised"]
    base = template["vocabulary_base.json"]
    scope_type = next(iter(base["entity_types"]))
    scope = dict(base["entity_types"][scope_type], label=lang.scope_label)
    scope_id = scope["ids"][0]
    types: dict[str, Any] = {scope_type: scope}
    for et in etypes:
        types[et.id] = {"label": et.label, "id_format": None, "separator": None, "case": "upper",
                        "strip_leading_zeros": False, "exact_match_metric": False, "egress": False,
                        "ids": list(et.ids)}
    labels80 = {p: plan.labels[p][:params["label_max_chars"]] for p in plan.ids}
    predicates = {plan.other_id: {"label": lang.other_label[:params["label_max_chars"]],
                                  "lexicon": {lang.code: [lang.other_phrase]}}}
    for p in plan.ids:
        predicates[p] = {"label": labels80[p], "lexicon": {lang.code: list(lexicon[p])}}
    vocabulary = {"entity_types": types, "predicates": predicates,
                  "negation": {lang.code: {k: list(v) for k, v in lang.negation.items()}},
                  "negation_window": base["negation_window"], "extraction": base["extraction"]}
    codes = {plan.codes[p]: {"label": plan.labels[p][:CODE_LABEL_MAX], "predicate": p, "specific": True}
             for p in plan.ids}
    codes[plan.other_code] = {"label": lang.other_label, "predicate": plan.other_id, "specific": False}
    aliases = {scope_type: {lang.scope_alias: scope_id}}
    for et in etypes:
        aliases[et.id] = dict(et.aliases)
    entities = {scope_type: [keys["scope"]]}
    for et in etypes:
        entities[et.id] = [f"{keys['entities']}.{et.id}[]"]
    mapping = {"record_ref": keys["record_ref"], "site": keys["site"],
               "received_date": {"path": keys["date"], "format": "iso"}, "language": None,
               "codes": [{"path": keys["codes"] + "[]", "value_map": dict(plan.value_map)}],
               "entities": entities, "primary_entity_type": scope_type,
               "narrative": [{"path": keys["narrative"], "where": None}], "persons": {},
               "reporter": keys["reporter"] if roles.reporter is not None else None, "origin_ref": None,
               "origin_site": None, "required": []}
    questions = json.loads(json.dumps(template["questions.json"]))
    for tpl in questions["templates"].values():
        tpl["text"] = lang.templates["question"]
    t = lang.templates
    first = {plan.other_id: lang.other_phrase, **{p: lexicon[p][0] for p in plan.ids}}
    gen = dict(template["generator_base.json"])
    gen.update({
        "predicate_weights": {p: 1 for p in sorted(first)},
        "codes": dict(gen["codes"], generic_code=plan.other_code),
        "fill_rates": {scope_type: 1.0, **{et.id: 0.5 for et in etypes}},
        "universe": {scope_type: [scope_id], **{et.id: list(et.ids) for et in etypes}},
        "links": {},
        "narratives": {lang.code: {p: {"affirmed": [_fill(s, "term", first[p]) for s in t["affirmed"]],
                                       "negated": [_fill(s, "term", first[p]) for s in t["negated"]]}
                                   for p in sorted(first)}},
        "entity_sentences": {lang.code: [_fill(t["entity"], "entity", "{" + scope_type + "}")]},
        "filler": {lang.code: list(t["filler"])},
        "persons": {}})
    files = {
        "pack.json": {"id": pack_id, "version": ids["version"], "title": t["title"], "languages": [lang.code],
                      "illustrative": True, "disclaimer": t["disclaimer"], "same_author_as_code": True},
        "vocabulary.json": vocabulary, "codes.json": codes, "aliases.json": aliases, "mapping.json": mapping,
        "egress.json": template["egress.json"], "detectors.json": template["detectors.json"],
        "rules.json": template["rules.json"], "questions.json": questions,
        "followups.json": template["followups.json"], "generator.json": gen}
    files[FIXTURES] = fixtures(plan, lexicon, lang, gen, scope_type, scope_id, etypes, ids)
    return files


def fixtures(plan: Plan, lexicon: Mapping[str, list[str]], lang: Language, gen: Mapping[str, Any], scope_type: str,
             scope_id: str, etypes: Sequence[EntityType], ids: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rule 1.6: at least 40 fixtures, one synthetic sentence holding one term each, gold by construction: the
    predicates in id order, round after round, each round taking every predicate's next term."""
    terms = {plan.other_id: [lang.other_phrase], **{p: list(lexicon[p]) for p in plan.ids}}
    order = sorted(terms)
    codes = {plan.other_id: plan.other_code, **plan.codes}
    start = date.fromisoformat(gen["start"])
    sites = [s["id"] for s in gen["sites"]]
    out = []
    for i in range(max(MIN_FIXTURES, len(order))):
        p = order[i % len(order)]
        term = terms[p][(i // len(order)) % len(terms[p])]
        record = {"record_ref": ids["fixture_ref_format"].format(i + 1), "site": sites[i % len(sites)],
                  "received_date": (start + timedelta(days=i)).isoformat(), "language": lang.code,
                  "codes": [codes[p]], "entities": {scope_type: [scope_id], **{et.id: [] for et in etypes}},
                  "narrative": _fill(lang.templates["fixture"], "term", term), "persons": {}, "reporter": None,
                  "origin_ref": None, "origin_site": None, "synthetic": True}
        gold = [{"entity_type": scope_type, "entity_id": scope_id, "predicate": p, "negated": False}]
        out.append({"record": record, "gold": gold})
    return out


def pack_bytes(files: Mapping[str, Any]) -> dict[str, bytes]:
    """Every pack file as the bytes written: JSON with sorted keys, two-space indents and a final newline; the
    fixtures one sorted-key JSON object per line."""
    out = {}
    for name in PACK_FILES:
        out[name] = (json.dumps(files[name], indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    out[FIXTURES] = "".join(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n"
                            for line in files[FIXTURES]).encode("utf-8")
    return out


def write_pack(files: Mapping[str, Any], directory: str | Path) -> dict[str, str]:
    """Writes the pack into a new or empty directory; returns each file's sha256."""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise DraftError(f"{directory} exists and is not empty")
    (directory / "fixtures").mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, data in pack_bytes(files).items():
        (directory / name).write_bytes(data)
        hashes[name] = sha256_hex(data)
    return dict(sorted(hashes.items()))


# --------------------------------------------------------------------------------------------------- the draft

@dataclass
class Draft:
    """One export's draft: the pack's files and everything the scorer reuses."""

    roles: Roles
    lang: Language
    params: Mapping[str, Any]
    template: Mapping[str, Any]
    dated: Dated
    window: Window
    corpus: Corpus
    categories: list[Category]
    plan: Plan
    table: TermTable
    eligible: list[int]
    labels: list[tuple[str, ...]]
    learned: dict[str, list[str]]
    lexicon: dict[str, list[str]]
    etypes: list[EntityType]
    files: dict[str, Any]
    inferred: dict[str, str | None]
    facts: dict[str, Any] = field(default_factory=dict)
    refusal: Refusal | None = None
    refused_terms: int = 0
    refused_entity_values: int = 0
    refused_tids: list[int] = field(default_factory=list)


def draft_export(export: Export, roles: Roles, window: Window, pack_id: str, *,
                 params: Mapping[str, Any] | None = None, lang: Language | None = None,
                 template: Mapping[str, Any] | None = None) -> Draft:
    """Rules 1.2 to 1.6 over one export: the training rows, the predicates, the lexicon and the pack's files."""
    params = params if params is not None else load_params()
    lang = lang if lang is not None else load_language(roles.language)
    template = template if template is not None else load_template()
    ids = template["ids.json"]
    check_roles(roles, export)
    dated = date_column(export, roles, lang, params)
    train = window_rows(dated, window)
    corpus = build_corpus(export, roles, train)
    refusal = export_refusal(export, roles, params)
    evidence = column_evidence(export, train, lang.months)
    inferred = infer_roles(evidence, params, lang.site_words, lang.category_words, len(train))
    categories = group_categories(corpus)
    plan = plan_predicates(categories, params, lang, ids, refusal)
    table = term_table(corpus, lang, params)
    eligible, refused_tids = eligible_terms(table, refusal, params)
    labels = record_labels(corpus.categories, plan)
    learned = assign_terms(table, eligible, labels, plan.ids, params)
    lexicon = with_placeholders(learned, ids)
    scope_type = next(iter(template["vocabulary_base.json"]["entity_types"]))
    etypes, refused_entities = entity_types(corpus, roles, params, ids, {scope_type, plan.other_id, *plan.ids},
                                            refusal)
    files = assemble_pack(pack_id, plan, lexicon, lang, template, params, roles, etypes)
    d = Draft(roles=roles, lang=lang, params=params, template=template, dated=dated, window=window, corpus=corpus,
              categories=categories, plan=plan, table=table, eligible=eligible, labels=labels, learned=learned,
              lexicon=lexicon, etypes=etypes, files=files, inferred=inferred, refusal=refusal,
              refused_terms=len(refused_tids), refused_entity_values=refused_entities, refused_tids=refused_tids)
    d.facts = summarise(export, d)
    return d


def refusal_removed(d: Draft) -> dict[str, Any]:
    """Amendment A14: what the refusal removed, in counts only, never a refused label or term.

    * categories: how many were refused, each one's corpus rows (descending), the corpus rows filed under any of them
      and their share of the corpus, and how many had at least R rows and a specific label;
    * terms: how many floor-passing terms were refused; how many would have been assigned to a drafted predicate
      (``df(t, c)`` and ``p(c | t)`` at their minimums, rule 1.5, no cap), per predicate too; and how many of those
      would have been among their predicate's K terms had the refusal not applied."""
    plan, corpus, params = d.plan, d.corpus, d.params
    n = len(corpus)
    refused_keys = {c.key for c in plan.refused}
    rows = sum(1 for cats in corpus.categories if any(folded(v) in refused_keys for v in cats))
    specific = sum(1 for c in plan.refused
                   if c.count >= params["predicate_min_records"] and c.key not in d.lang.non_specific)
    uncapped = dict(params, max_terms=max(1, len(d.table.terms)))
    assignable = assign_terms(d.table, d.refused_tids, d.labels, plan.ids, uncapped)
    refused_set = {d.table.terms[t] for t in d.refused_tids}
    capped = assign_terms(d.table, sorted({*d.eligible, *d.refused_tids}), d.labels, plan.ids, params)
    return {"categories": len(plan.refused), "category_rows": [c.count for c in plan.refused],
            "rows": rows, "share": rows / n if n else None, "with_minimum": specific,
            "terms": len(d.refused_tids), "assignable": sum(len(v) for v in assignable.values()),
            "assignable_by_predicate": {p: len(assignable[p]) for p in plan.ids},
            "within_cap": sum(1 for p in plan.ids for t in capped[p] if t in refused_set)}


def summarise(export: Export, d: Draft) -> dict[str, Any]:
    """The draft's counts, roles and sizes (``draft.json``): aggregates, category labels that passed the floor, each
    predicate's first ten terms, and what the refusal removed (counts only); no record value."""
    plan, corpus = d.plan, d.corpus
    n = len(corpus)
    removed = refusal_removed(d)
    other_keys = {c.key for c in plan.split.other}
    other_rows = sum(1 for cats in corpus.categories if any(folded(v) in other_keys for v in cats))
    no_specific = sum(1 for labels in d.labels if not labels)
    sizes = sorted(len(d.learned[p]) for p in plan.ids)
    passing = [{"label": c.label, "records": c.count, "sites": c.sites,
                "predicate": plan.of_key.get(c.key, plan.other_id)}
               for c in sorted((*plan.split.specific, *plan.split.other), key=lambda c: (-c.count, c.label))]
    return {
        "export": {"format": export.format, "encoding": export.encoding, "columns": len(export.columns),
                   "rows": len(export), "rejected": dict(export.rejected), "blank_lines": export.blank_lines},
        "dates": {"format": d.dated.format, "rejected": d.dated.rejected},
        "training": {"window": d.window.to_json(), "rows": corpus.training_rows,
                     "without_narrative": corpus.without_narrative, "corpus": n},
        "roles": {"declared": d.roles.single(), "inferred": dict(d.inferred),
                  "right": roles_right(d.inferred, d.roles), "of": 5},
        "categories": {"passing_floor": passing, "below_floor": len(plan.split.below),
                       "specific": len(plan.ids), "other": len(plan.split.other), "refused": len(plan.refused)},
        "predicates": [{"id": p, "code": plan.codes[p], "label": plan.labels[p], "records": plan.counts[p],
                        "terms": len(d.learned[p]), "first_terms": d.lexicon[p][:10],
                        "refused_assignable": removed["assignable_by_predicate"][p]} for p in plan.ids],
        "other_bucket_rows": other_rows, "other_bucket_share": other_rows / n if n else None,
        "rows_without_specific": no_specific,
        "terms": {"total": sum(sizes), "min": sizes[0], "median": sizes[len(sizes) // 2], "max": sizes[-1],
                  "candidates": len(d.table.terms), "eligible": len(d.eligible), "refused": d.refused_terms},
        "placeholders": sum(1 for p in plan.ids if not d.learned[p]),
        "entity_types": [{"id": et.id, "ids": len(et.ids)} for et in d.etypes],
        "refused_entity_values": d.refused_entity_values,
        "refusal": {k: v for k, v in removed.items() if k != "assignable_by_predicate"},
    }


def load_written(directory: str | Path) -> tuple[Any, str | None]:
    """The loader's check of a written pack: (the frozen pack or None, the error text or None). The text is the
    failing file and the problem, without the JSON path, which can name a predicate or a category spelling."""
    try:
        return load_pack_dir(directory), None
    except PackError as err:
        return None, f"{err.file}: {err.problem}"


# --------------------------------------------------------------------------------------------------- normalised export

def normalised_rows(export: Export, roles: Roles, dated: Dated, window: Window,
                    template: Mapping[str, Any], etypes: Sequence[EntityType] = ()) -> tuple[list[dict[str, Any]],
                                                                                       dict[str, int]]:
    """Rule 1.6: one JSON object per row dated in ``window``: the record id, the site lower-cased, the date in ISO
    form, the category values, the narrative, the declared entity values and reporter, and the scope id. A row with no
    record id, a repeated one, one the pipeline would refuse, or a site outside the pipeline's site id pattern is
    rejected and counted."""
    ids = template["ids.json"]
    keys = ids["normalised"]
    scope_id = next(iter(template["vocabulary_base.json"]["entity_types"].values()))["ids"][0]
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    seen: set[str] = set()
    for r, day in enumerate(dated.dates):
        if day is None:
            rejected["bad_date"] += 1
            continue
        if not window.contains(day):
            continue
        ref = export.value(r, roles.record_id)
        if ref is None:
            rejected["missing_record_id"] += 1
            continue
        if len(ref) > MAX_REF or not ref.isprintable():
            rejected["bad_record_id"] += 1
            continue
        if ref in seen:
            rejected["duplicate_record_id"] += 1
            continue
        site = export.value(r, roles.site)
        site = site.lower() if site is not None else None
        if site is None or SITE_ID_RE.fullmatch(site) is None:
            rejected["bad_site"] += 1
            continue
        seen.add(ref)
        row: dict[str, Any] = {keys["record_ref"]: ref, keys["site"]: site, keys["date"]: day.isoformat(),
                               keys["scope"]: scope_id}
        cats = _cells(export.value(r, roles.category))
        if cats:
            row[keys["codes"]] = list(cats)
        narrative = export.value(r, roles.narrative)
        if narrative is not None:
            row[keys["narrative"]] = narrative
        if etypes:
            row[keys["entities"]] = {et.id: list(_cells(export.value(r, et.column))) for et in etypes}
        if roles.reporter is not None:
            reporter = export.value(r, roles.reporter)
            if reporter is not None:
                row[keys["reporter"]] = reporter
        rows.append(row)
    return rows, dict(sorted(rejected.items()))


def write_jsonl(rows: Iterable[Mapping[str, Any]], path: str | Path) -> str:
    data = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return sha256_hex(data)
