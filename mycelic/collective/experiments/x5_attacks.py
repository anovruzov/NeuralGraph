"""X5 (B3): the attacks of a red team that holds only what HQ holds, as pure functions of facts and known values.

Synthetic, same-author and internal only. :mod:`.x5_inference` turns every artifact HQ holds (cells by channel, usage
summaries, HQ's own questions and verdicts, packets, the follow-up drafts, outbox and ledgers, HQ's store results and
the run files) into :class:`Fact` s, builds what the attacker knows about each target and the truth separately, and
calls the functions here. Nothing here reads a file, a store or a world, and no attack function takes a truth
argument: truth only meets a prediction in :func:`outcome`.

**Facts.** ``Fact(source, site, first, last, entity_type, entity_id, predicate, channel, field, lo, hi, text)``: an
artifact of type ``source`` says that at ``site`` (None: no site), over the week indexes ``[first, last]`` (weeks
counted from the generator's start), the quantity ``field`` of ``(entity, predicate, channel)`` lies in ``[lo, hi]``
(``hi`` None: unbounded). A predicate of None is an entity-level fact; a channel of None sums both channels.
``string`` facts carry one string leaf of an artifact item in ``text``. :class:`FactIndex` indexes them.

**The attacks** (:data:`ATTACKS`; the matched baselines use the same targets and the attacker's knowledge minus the
artifact):

* **A1 membership.** The score of an original record is the share of its keys (the attacker's own lexical extraction,
  egress types and the site's master-data rule) with a non-string fact of ``lo >= 1`` at its site, for the key's
  entity and predicate (and channel, when the fact has one), whose span holds the record's week. Only the keys of the
  channels the artifact type can carry count (:data:`A1_CHANNELS`: one channel for each cell type and for the
  allowed-fields reference, every channel otherwise). ``A1_calibrated``: member iff the score reaches the shadow
  threshold of the artifact type itself (:func:`calibrate` on that type's own shadow facts), so only the types with a
  shadow analogue (:data:`A1_CALIBRATED_TYPES`) have one; ``A1_fixed``: member iff the score is 1. A record with no
  such key gets a seeded coin. Strata are balanced, so the baseline is 0.5 exactly and a target's value is
  ``2 * correct - 1``.
* **A2 predicate attribute inference.** For a member with structured entities, ``support(p)`` counts its known
  entities with a positive fact for ``p`` at its site and week; the prediction is the argmax, ties (and a support of
  0, uncovered) broken by the shadow prior chain ``P(p | e*, site) -> P(p | e*) -> P(p | type of e*) -> P(p)``, then
  the predicate id. The baseline is the prior chain alone.
* **A3 count inference inside the protected range.** The variables are a (site, key)'s weekly counts per channel over
  the connected span of its facts. Cell counts bound sums per channel; a covered week with no cell bounds the cell by
  the covered fact (0, or ``k - 1`` when cells below k are dropped); support, presence and the entity-level zeros
  bound sums over both channels; code and co-mention counts bound them from below only (records carrying a code need
  not confirm the key). Bounds propagate to a fixpoint (at most :data:`MAX_ROUNDS`); the target's interval, cut to
  ``[1, k - 1]``, takes the shadow mode inside it (the smaller n on ties); an empty interval falls back to the shadow
  mode over ``[1, k - 1]``, which is also the baseline.
* **A4 reporter linkage inside the protected range.** Only the target cell's ``n`` and ``n_reporters`` facts: an
  ``n_reporters`` of at most 1 says same; an exact ``n`` with ``n_reporters`` at least ``n`` on the same span says
  different; otherwise the shadow majority, which is the baseline.
* **A5 person-name negative control.** The field's generator surnames found among the string tokens at the site (or
  with no site) in a span holding the week; the prediction is the most frequent shadow surname among those found, else
  the shadow mode, which is the baseline. On real artifacts nothing names a person, so this must sit at chance.
* **A6 presence oracle.** The attacker's own questions: a confirm or a refute says the site holds the entity in the
  window. The baseline is the shadow presence share of the (entity, site), present when it is at least 0.5.

**Statistics** (:func:`summarise`): accuracy with a Wilson interval, the matched baseline, and the advantage (the mean
per-target value) with a cluster-bootstrap interval (``stats.cluster_bootstrap_mean``, clusters ``(seed, site,
week)`` or, for A6, ``(seed, site, window start)``). :func:`label`: ``leak`` when ``ci_low > 0``, ``at_chance`` when
the interval lies within ``[-0.05, 0.05]``, else ``inconclusive``; no target is ``inconclusive`` with no advantage.
Primary entries also carry the Bonferroni interval from the same seed (nested). The labels are spelled out by
:data:`LABELS` (one of them is also a device-pack predicate id, so it is never a whole literal here).

Deterministic: string-keyed ``random.Random`` only, sorted iteration, no clock.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .. import stats

LEAK, AT_CHANCE, INCONCLUSIVE = LABELS = tuple("leak at_chance inconclusive".split())
AT_CHANCE_MARGIN = 0.05
ALPHA = 0.05
LABEL_RULES = {LEAK: "ci_low > 0", AT_CHANCE: "-0.05 <= ci_low and ci_high <= 0.05", INCONCLUSIVE: "otherwise"}
ATTACKS = ("A1_calibrated", "A1_fixed", "A2", "A3", "A4", "A5", "A6")
PRIMARY_ATTACKS = ("A1_calibrated", "A2", "A3", "A4")
ATTACK_NAMES = {
    "A1_calibrated": "membership inference (A1, calibrated threshold)",
    "A1_fixed": "membership inference (A1, every key present)",
    "A2": "predicate attribute inference (A2)",
    "A3": "count inference inside the protected range (A3)",
    "A4": "reporter linkage inside the protected range (A4)",
    "A5": "person-name inference (A5, negative control)",
    "A6": "the presence oracle (A6)",
}
BASE_TYPES = ("cells_codes", "cells_text", "usage_summary", "verdicts_passive", "verdicts_active", "packets",
              "followup", "hq_results", "run_files")
ALL_TYPE = "all"
REFERENCE_TYPES = ("allowed_fields_reference", "allowed_plus_all")
ARTIFACT_TYPES = (*BASE_TYPES, ALL_TYPE, *REFERENCE_TYPES)
CELL_TYPES = ("cells_codes", "cells_text")
# what A1 to A5 may read on the k1 reference, and A4 on any variant
K1_TYPES = (*CELL_TYPES, ALL_TYPE)
A4_TYPES = (*CELL_TYPES, ALL_TYPE, *REFERENCE_TYPES)
A5_INJECTED_TYPES = ("cells_text", ALL_TYPE)
CHANNELS = ("codes", "text_only")         # edge.egress.CHANNELS (pinned by a test; this module imports no edge code)
# A1: the channels an artifact type's facts can carry (a type not named here carries both: a fact of channel None
# sums them); a key of another channel can never be present in it, so it does not count in the type's score
A1_CHANNELS = {"cells_codes": CHANNELS[:1], "cells_text": CHANNELS[1:], "allowed_fields_reference": CHANNELS[:1]}
# A1_calibrated: the types whose threshold is calibrated on their own shadow facts; shadow worlds run no pipeline, so
# they hold cells and R (model-free)'s cells only (``all`` from the shadow cells of both channels)
A1_CALIBRATED_TYPES = (*CELL_TYPES, ALL_TYPE, *REFERENCE_TYPES)
A3_FIELDS = ("n", "covered", "support", "presence", "code", "co_mention", "entity_records")
LOWER_ONLY = ("code", "co_mention")
MAX_ROUNDS = 100
SHORT_SPAN = 13                            # facts spanning at most this many weeks are indexed per week
A1_BASELINE = 0.5
STATUSES = ("run", "not_applicable", "not_run")
FAMILIES = ("primary", "exploratory")
USAGE_REASON = "usage summaries name no entity or predicate"
A4_REASON = "A4 reads cell-level n and n_reporters only; this artifact has none"
A1_SHADOW_REASON = ("shadow worlds run no pipeline, so this artifact type has no shadow facts to calibrate an A1 "
                    "threshold on; A1_fixed reads it")
A6_REASON = "A6 reads only the attacker's own questions"
K1_REASON = "the k1 reference replaces only the cells"
INJECTED_REASON = "the injected positive control tests A5 only, on cells_text and all"
VOLUME_A6_REASON = "A6 is pre-registered at default volume only"
A4_K2_REASON = "no cell with 2 <= n < k at k = 2 (a cell with n = 2 is exact: designed disclosure)"
VARIANT_KINDS = ("pipeline", "derived")
STATEMENT = ("X5 here is synthetic, same-author and internal only: the worlds, the red team and the defences were "
             "written by the same AI system on the packs' synthetic generators, so these figures describe these "
             "artifacts on these worlds and are never a buyer claim.")
SENTENCES = {
    (LEAK, LEAK): (
        "On {pack}, {attack_name} leaks: advantage {estimate} over chance (95% CI {ci_low} to {ci_high}), above 0 at "
        "the 95% and at the Bonferroni level for {family_size} tests; this fails STRATEGY 11.2's X5 bar (at chance or "
        "near it)."),
    (LEAK, AT_CHANCE): (
        "On {pack}, {attack_name} shows a leak at the 95% level (advantage {estimate}, 95% CI {ci_low} to {ci_high}) "
        "that lies within 0.05 of chance at the Bonferroni level for {family_size} tests; this still fails STRATEGY "
        "11.2's X5 bar (at chance or near it), since the pre-registered bar reads the 95% label."),
    (LEAK, INCONCLUSIVE): (
        "On {pack}, {attack_name} shows a leak at the 95% level (advantage {estimate}, 95% CI {ci_low} to {ci_high}) "
        "that does not stay above 0 at the Bonferroni level for {family_size} tests; this still fails STRATEGY 11.2's "
        "X5 bar (at chance or near it), since the attack is not shown to be at chance."),
    (AT_CHANCE, AT_CHANCE): (
        "On {pack}, {attack_name} is at chance: advantage {estimate} (95% CI {ci_low} to {ci_high}), within 0.05 of "
        "chance at the 95% and at the Bonferroni level for {family_size} tests; this meets STRATEGY 11.2's X5 bar for "
        "this attack on synthetic, same-author worlds."),
    (AT_CHANCE, INCONCLUSIVE): (
        "On {pack}, {attack_name} is within 0.05 of chance at the 95% level only (advantage {estimate}, 95% CI "
        "{ci_low} to {ci_high}), not at the Bonferroni level for {family_size} tests; inconclusive against STRATEGY "
        "11.2's X5 bar."),
    (INCONCLUSIVE, INCONCLUSIVE): (
        "On {pack}, {attack_name} is inconclusive: advantage {estimate} (95% CI {ci_low} to {ci_high}); this run does "
        "not decide STRATEGY 11.2's X5 bar for this attack."),
}
BAR_OUTCOMES = ("fails", "met", "undecided")
BAR_SENTENCES = {
    "fails": ("STRATEGY 11.2's X5 bar fails on these synthetic worlds: at least one primary attack leaks at the 95% "
              "level; the claim that nothing leaks beyond text may not be made."),
    "met": ("STRATEGY 11.2's X5 bar is met on these synthetic, same-author worlds only: all {family_size} primary "
            "attacks are at chance at the Bonferroni level; this describes these artifacts on these worlds and is "
            "never a buyer claim."),
    "undecided": ("STRATEGY 11.2's X5 bar is undecided on these synthetic worlds: no primary attack leaks at the 95% "
                  "level, but not every primary attack is at chance at the Bonferroni level; the claim that nothing "
                  "leaks beyond text may not be made."),
}
_LETTERS = re.compile(r"[^\W\d_]+")


# --------------------------------------------------------------------------------------------------- facts

@dataclass(frozen=True)
class Fact:
    source: str
    site: str | None
    first: int
    last: int
    entity_type: str | None
    entity_id: str | None
    predicate: str | None
    channel: str | None
    field: str
    lo: int
    hi: int | None
    text: str | None = None


def tokens(text: str) -> frozenset[str]:
    """The letter runs of ``text`` (split on everything that is not a letter), upper-cased."""
    return frozenset(t.upper() for t in _LETTERS.findall(text))


def span_holds(fact: Fact, week: int) -> bool:
    return fact.first <= week <= fact.last


class FactIndex:
    """Facts by (site, entity) and by week: positive facts for A1 and A2, every count fact for A3 and A4, covered
    facts per site, string tokens per (site, week) for A5."""

    def __init__(self, facts: Iterable[Fact]) -> None:
        self._pos_week: dict[tuple[Any, ...], set[tuple[str, str | None]]] = {}
        self._pos_long: dict[tuple[Any, ...], list[tuple[int, int, str, str | None]]] = {}
        self._entity: dict[tuple[Any, ...], list[Fact]] = {}
        self._covered: dict[tuple[Any, ...], list[Fact]] = {}
        self._tok_week: dict[tuple[Any, ...], set[str]] = {}
        self._tok_long: dict[str | None, list[tuple[int, int, frozenset[str]]]] = {}
        self.size = 0
        for f in facts:
            self.size += 1
            short = f.last - f.first + 1 <= SHORT_SPAN
            if f.field == "string":
                found = tokens(f.text or "")
                if not found:
                    continue
                if short:
                    for w in range(f.first, f.last + 1):
                        self._tok_week.setdefault((f.site, w), set()).update(found)
                else:
                    self._tok_long.setdefault(f.site, []).append((f.first, f.last, found))
                continue
            if f.field == "covered":
                for w in range(f.first, f.last + 1):
                    self._covered.setdefault((f.site, w), []).append(f)
                continue
            if f.entity_type is None or f.entity_id is None:
                continue
            key = (f.site, f.entity_type, f.entity_id)
            self._entity.setdefault(key, []).append(f)
            if f.predicate is None or f.lo < 1:
                continue
            if short:
                for w in range(f.first, f.last + 1):
                    self._pos_week.setdefault((*key, w), set()).add((f.predicate, f.channel))
            else:
                self._pos_long.setdefault(key, []).append((f.first, f.last, f.predicate, f.channel))

    def positive(self, site: str, entity_type: str, entity_id: str, predicate: str, channel: str | None,
                 week: int) -> bool:
        """A non-string fact with ``lo >= 1`` for the entity and predicate at the site whose span holds ``week``;
        its channel is None or ``channel`` (``channel`` None: any)."""
        found = self._pos_week.get((site, entity_type, entity_id, week), ())
        for p, ch in found:
            if p == predicate and (ch is None or channel is None or ch == channel):
                return True
        for first, last, p, ch in self._pos_long.get((site, entity_type, entity_id), ()):
            if first <= week <= last and p == predicate and (ch is None or channel is None or ch == channel):
                return True
        return False

    def predicates_at(self, site: str, entity_type: str, entity_id: str, week: int) -> set[str]:
        out = {p for p, _ in self._pos_week.get((site, entity_type, entity_id, week), ())}
        out.update(p for first, last, p, _ in self._pos_long.get((site, entity_type, entity_id), ())
                   if first <= week <= last)
        return out

    def entity_facts(self, site: str, entity_type: str, entity_id: str) -> list[Fact]:
        return list(self._entity.get((site, entity_type, entity_id), ()))

    def covered_between(self, site: str, first: int, last: int) -> list[Fact]:
        """The covered facts at ``site`` whose span meets ``[first, last]``, each once, in span order."""
        found: dict[int, Fact] = {}
        for w in range(first, last + 1):
            for f in self._covered.get((site, w), ()):
                found.setdefault(id(f), f)
        return sorted(found.values(), key=lambda f: (f.first, f.last, f.channel or ""))

    def tokens_at(self, site: str, week: int) -> set[str]:
        out: set[str] = set()
        for s in (site, None):
            out.update(self._tok_week.get((s, week), ()))
            for first, last, found in self._tok_long.get(s, ()):
                if first <= week <= last:
                    out.update(found)
        return out


# --------------------------------------------------------------------------------------------------- known values

@dataclass(frozen=True)
class A1Known:
    site: str
    week: int
    ref: str
    keys: tuple[tuple[str, str, str, str], ...]          # (entity_type, entity_id, predicate, channel)
    coin: bool


@dataclass(frozen=True)
class A2Known:
    site: str
    week: int
    entities: tuple[tuple[str, str], ...]                 # canonical structured entities, sorted
    primary: tuple[str, str]                               # e*: the primary type's entity, else the first entity


@dataclass(frozen=True)
class A3Known:
    site: str
    week: int
    entity_type: str
    entity_id: str
    predicate: str
    channel: str
    cells_cover: bool          # the key may leave this site (egress type, master-data rule): a missing cell means 0


@dataclass(frozen=True)
class A4Known:
    site: str
    week: int
    entity_type: str
    entity_id: str
    predicate: str
    channel: str


@dataclass(frozen=True)
class A5Known:
    site: str
    week: int
    field: str


@dataclass(frozen=True)
class A2Prior:
    """Shadow counts of affirmed predicates per (entity, site), per entity, per entity type and overall."""

    by_entity_site: Mapping[tuple[str, str, str], Mapping[str, int]]
    by_entity: Mapping[tuple[str, str], Mapping[str, int]]
    by_type: Mapping[str, Mapping[str, int]]
    overall: Mapping[str, int]

    def rank(self, predicate: str, entity: tuple[str, str], site: str) -> tuple[Any, ...]:
        t, e = entity
        return (-self.by_entity_site.get((t, e, site), {}).get(predicate, 0),
                -self.by_entity.get((t, e), {}).get(predicate, 0), -self.by_type.get(t, {}).get(predicate, 0),
                -self.overall.get(predicate, 0), predicate)


# --------------------------------------------------------------------------------------------------- A1

def a1_coin(seed: int, ref: str) -> bool:
    return random.Random(f"x5:{seed}:a1coin:{ref}").random() < 0.5


def a1_score(known: A1Known, index: FactIndex, channels: Sequence[str] | None = None) -> float | None:
    """The share of the record's keys (those of ``channels`` when given: the channels the attacked artifact type can
    carry, :data:`A1_CHANNELS`) with a positive fact at its site and week; None without such keys."""
    keys = [key for key in known.keys if channels is None or key[3] in channels]
    if not keys:
        return None
    hits = sum(1 for t, e, p, ch in keys if index.positive(known.site, t, e, p, ch, known.week))
    return hits / len(keys)


def calibrate(scores: Sequence[float | None], members: Sequence[bool]) -> float:
    """The shadow threshold: among the sorted distinct scores, the one that maximises accuracy with ``member iff
    score >= t`` (records without keys take their coin either way and are left out), the smallest on ties; 1.0
    when no shadow record has a key."""
    scored = sorted((s, m) for s, m in zip(scores, members) if s is not None)
    if not scored:
        return 1.0
    distinct = sorted({s for s, _ in scored})
    total_members = sum(1 for _, m in scored if m)
    best_t, best = distinct[0], -1
    below_members = below_others = 0
    i = 0
    for t in distinct:
        while i < len(scored) and scored[i][0] < t:
            below_members += scored[i][1]
            below_others += not scored[i][1]
            i += 1
        correct = (total_members - below_members) + below_others
        if correct > best:
            best_t, best = t, correct
    return best_t


def a1_predict(score: float | None, coin: bool, threshold: float | None) -> tuple[bool, bool]:
    """``(member, covered)``: ``threshold`` None is the fixed rule (score 1); no key takes the coin."""
    if score is None:
        return coin, False
    return (score == 1.0 if threshold is None else score >= threshold), True


# --------------------------------------------------------------------------------------------------- A2

def a2_prior_prediction(known: A2Known, prior: A2Prior, predicates: Sequence[str]) -> str:
    return min(predicates, key=lambda p: prior.rank(p, known.primary, known.site))


def a2_predict(known: A2Known, index: FactIndex, prior: A2Prior, predicates: Sequence[str]) -> tuple[str, bool]:
    support: dict[str, int] = {}
    for t, e in known.entities:
        for p in sorted(index.predicates_at(known.site, t, e, known.week)):
            support[p] = support.get(p, 0) + 1
    if not support:
        return a2_prior_prediction(known, prior, predicates), False
    best = min(predicates, key=lambda p: (-support.get(p, 0), *prior.rank(p, known.primary, known.site)))
    return best, True


# --------------------------------------------------------------------------------------------------- A3

def _component(facts: Sequence[Fact], week: int) -> tuple[int, int, list[Fact]]:
    """The facts connected to ``week`` through overlapping spans, and their joint span."""
    ordered = sorted(facts, key=lambda f: (f.first, f.last))
    groups: list[tuple[int, int, list[Fact]]] = []
    for f in ordered:
        if groups and f.first <= groups[-1][1]:
            a, b, members = groups[-1]
            members.append(f)
            groups[-1] = (a, max(b, f.last), members)
        else:
            groups.append((f.first, f.last, [f]))
    for a, b, members in groups:
        if a <= week <= b:
            return a, b, members
    return week, week, []


def _propagate(n_vars: int, constraints: Sequence[tuple[list[int], float, float]]) -> tuple[list[float], list[float]]:
    """Interval bounds of non-negative variables under sum constraints ``low <= sum(vars) <= high``, tightened to a
    fixpoint: ``lo_v >= low - sum of the others' hi`` and ``hi_v <= high - sum of the others' lo``. Bounds only move
    inwards, so a bound computed from an earlier, looser one is still valid. It stops at the first empty interval
    (the constraints contradict each other, which the caller reads as no interval)."""
    lo = [0.0] * n_vars
    hi = [math.inf] * n_vars
    for _ in range(MAX_ROUNDS):
        changed = False
        for vars_, low, high in constraints:
            sum_lo = math.fsum(lo[v] for v in vars_)
            finite = [hi[v] for v in vars_ if hi[v] != math.inf]
            n_inf = len(vars_) - len(finite)
            sum_hi = math.fsum(finite)
            for v in vars_:
                if hi[v] == math.inf:
                    rest_hi = math.inf if n_inf > 1 else sum_hi
                else:
                    rest_hi = math.inf if n_inf > 0 else sum_hi - hi[v]
                new_lo = low - rest_hi
                new_hi = high - (sum_lo - lo[v])
                if new_lo > lo[v] + 1e-9:
                    lo[v], changed = new_lo, True
                if new_hi < hi[v] - 1e-9:
                    hi[v], changed = new_hi, True
                if lo[v] > hi[v] + 1e-9:
                    return lo, hi                      # infeasible: stop before the bounds run away
        if not changed:
            break
    return lo, hi


def a3_interval(known: A3Known, index: FactIndex, k: int) -> tuple[int, int] | None:
    """The target cell's count interval inside ``[1, k - 1]`` from bound propagation over the facts connected to it;
    None when the constraints leave it empty."""
    p = known.predicate
    facts = [f for f in index.entity_facts(known.site, known.entity_type, known.entity_id)
             if f.field in A3_FIELDS and (f.predicate == p or (f.predicate is None and f.hi is not None))]
    covered: list[Fact] = []
    while True:
        a, b, used = _component(facts + covered, known.week)
        wider = index.covered_between(known.site, a, b) if known.cells_cover else []
        if len(wider) == len(covered):
            break
        covered = wider
    channels = {c: i for i, c in enumerate(CHANNELS)}

    def var(w: int, c: str) -> int:
        return (w - a) * len(CHANNELS) + channels[c]

    cell_spans = {(f.first, f.last, f.channel) for f in used if f.field == "n"}
    constraints: list[tuple[list[int], float, float]] = []
    for f in used:
        chans = [f.channel] if f.channel is not None else list(CHANNELS)
        if any(c not in channels for c in chans):
            continue
        if f.field == "covered":
            if (f.first, f.last, f.channel) in cell_spans:
                continue
            low, high = 0.0, (math.inf if f.hi is None else float(f.hi))
        elif f.predicate is None:
            low, high = 0.0, float(f.hi)
        elif f.field in LOWER_ONLY:
            low, high = float(f.lo), math.inf
        else:
            low, high = float(f.lo), (math.inf if f.hi is None else float(f.hi))
        vars_ = [var(w, c) for w in range(max(f.first, a), min(f.last, b) + 1) for c in chans]
        if vars_:
            constraints.append((vars_, low, high))
    lo, hi = _propagate((b - a + 1) * len(CHANNELS), constraints)
    if any(lo[v] > hi[v] + 1e-9 for v in range(len(lo))):
        return None
    target = var(known.week, known.channel)
    low_n = max(1, math.ceil(lo[target] - 1e-9))
    high_n = k - 1 if hi[target] == math.inf else min(k - 1, math.floor(hi[target] + 1e-9))
    if low_n > high_n:
        return None
    return low_n, high_n


def mode_in(prior: Mapping[int, int], low: int, high: int) -> int:
    """The most frequent shadow count in ``[low, high]``, the smaller on ties."""
    return min(range(low, high + 1), key=lambda n: (-prior.get(n, 0), n))


def a3_predict(known: A3Known, index: FactIndex, k: int, prior: Mapping[int, int]) -> tuple[int, bool]:
    interval = a3_interval(known, index, k)
    if interval is None:
        return mode_in(prior, 1, k - 1), False
    low, high = interval
    return mode_in(prior, low, high), (low, high) != (1, k - 1)


# --------------------------------------------------------------------------------------------------- A4

def a4_predict(known: A4Known, index: FactIndex, prior_same: bool) -> tuple[bool, bool]:
    """``(same reporter, covered)`` from the target cell's ``n`` and ``n_reporters`` facts only."""
    found = [f for f in index.entity_facts(known.site, known.entity_type, known.entity_id)
             if f.field in ("n", "n_reporters") and f.predicate == known.predicate and f.channel == known.channel
             and span_holds(f, known.week)]
    if any(f.field == "n_reporters" and f.hi is not None and f.hi <= 1 for f in found):
        return True, True
    for f in found:
        if f.field == "n" and f.hi is not None and f.lo == f.hi:
            if any(g.field == "n_reporters" and (g.first, g.last) == (f.first, f.last) and g.lo >= f.lo
                   for g in found):
                return False, True
    return prior_same, False


# --------------------------------------------------------------------------------------------------- A5 and A6

def prior_mode(prior: Mapping[str, int]) -> str:
    return min(prior, key=lambda name: (-prior[name], name))


def a5_predict(known: A5Known, index: FactIndex, names: Sequence[str], prior: Mapping[str, int]) -> tuple[str, bool]:
    """``(surname, covered)``: the most frequent shadow surname among those found at the site and week, else the
    shadow mode. ``names`` are the field's generator surnames, upper-cased; ``prior`` counts each of them."""
    present = index.tokens_at(known.site, known.week)
    found = sorted(n for n in names if n in present)
    if not found:
        return prior_mode(prior), False
    return min(found, key=lambda name: (-prior.get(name, 0), name)), True


def a6_predict(verdict: str | None) -> tuple[bool, bool]:
    """``(present, covered)``: a confirm or a refute says the site holds the entity in the window; an unanswered
    window (None) is not covered and reads as absent."""
    if verdict is None:
        return False, False
    return verdict in ("confirm", "refute"), True


# --------------------------------------------------------------------------------------------------- outcomes

@dataclass(frozen=True)
class Outcome:
    cluster: tuple[Any, ...]
    correct: bool
    baseline_correct: bool
    covered: bool


def outcome(cluster: tuple[Any, ...], predicted: Any, baseline: Any, truth: Any, covered: bool) -> Outcome:
    """Where truth meets a prediction: a set truth (A2) is matched by membership, anything else by equality."""
    if isinstance(truth, (set, frozenset)):
        return Outcome(cluster, predicted in truth, baseline in truth, covered)
    return Outcome(cluster, predicted == truth, baseline == truth, covered)


def value(attack: str, o: Outcome) -> float:
    """A target's contribution to the advantage: ``2c - 1`` for A1 (baseline 0.5), else attack minus baseline."""
    if attack.startswith("A1"):
        return 2.0 * o.correct - 1.0
    return float(int(o.correct) - int(o.baseline_correct))


def label(ci_low: float | None, ci_high: float | None) -> str:
    if ci_low is None or ci_high is None:
        return INCONCLUSIVE
    if ci_low > 0:
        return LEAK
    if -AT_CHANCE_MARGIN <= ci_low and ci_high <= AT_CHANCE_MARGIN:
        return AT_CHANCE
    return INCONCLUSIVE


def _clusters(clusters: Sequence[tuple[Any, ...]], values: Sequence[float]) -> list[list[float]]:
    grouped: dict[tuple[Any, ...], list[float]] = {}
    for c, v in zip(clusters, values):
        grouped.setdefault(c, []).append(v)
    return [grouped[c] for c in sorted(grouped)]


def bootstrap(clusters: Sequence[tuple[Any, ...]], values: Sequence[float], *, B: int, seed: str,
              alpha: float = ALPHA) -> dict[str, Any]:
    r = stats.cluster_bootstrap_mean(_clusters(clusters, values), B=B, seed=seed, alpha=alpha)
    return {"estimate": r["mean"], "ci_low": r["ci_low"], "ci_high": r["ci_high"], "B": r["B"], "seed": r["seed"],
            "method": r["method"], "n_clusters": r["n_clusters"]}


def empty_entry(status: str, reason: str) -> dict[str, Any]:
    return {"status": status, "reason": reason, "primary": False, "family": "exploratory", "n_targets": None,
            "coverage": None, "accuracy": None, "accuracy_wilson": None, "baseline_accuracy": None, "advantage": None,
            "label": None, "label_bonferroni": None, "advantage_bonferroni": None, "incremental": None}


def summarise(attack: str, outcomes: Sequence[Outcome], *, B: int, seed: str, primary: bool,
              family_size: int) -> dict[str, Any]:
    """One results entry. ``n_targets`` 0: no advantage and ``inconclusive``. A primary entry also carries the
    Bonferroni interval at ``ALPHA / family_size`` from the same seed, which contains the 95% interval."""
    n = len(outcomes)
    entry = {**empty_entry("run", None), "primary": primary, "family": FAMILIES[0] if primary else FAMILIES[1],
             "n_targets": n}
    if n == 0:
        entry.update(label=INCONCLUSIVE, label_bonferroni=INCONCLUSIVE if primary else None)
        return entry
    correct = sum(o.correct for o in outcomes)
    wilson = stats.wilson(correct, n)
    entry.update(coverage=sum(o.covered for o in outcomes) / n, accuracy=correct / n,
                 accuracy_wilson={"ci_low": wilson[0], "ci_high": wilson[1]},
                 baseline_accuracy=A1_BASELINE if attack.startswith("A1")
                 else sum(o.baseline_correct for o in outcomes) / n)
    clusters = [o.cluster for o in outcomes]
    values = [value(attack, o) for o in outcomes]
    entry["advantage"] = bootstrap(clusters, values, B=B, seed=seed)
    entry["label"] = label(entry["advantage"]["ci_low"], entry["advantage"]["ci_high"])
    if primary:
        alpha = ALPHA / family_size
        adjusted = bootstrap(clusters, values, B=B, seed=seed, alpha=alpha)
        entry["advantage_bonferroni"] = {"ci_low": adjusted["ci_low"], "ci_high": adjusted["ci_high"], "alpha": alpha}
        entry["label_bonferroni"] = label(adjusted["ci_low"], adjusted["ci_high"])
    return entry


def incremental(attack: str, plus: Sequence[Outcome], reference: Sequence[Outcome], *, B: int,
                seed: str) -> dict[str, Any]:
    """The advantage HQ's artifacts add over the allowed-fields reference on the same targets: per target,
    ``value(allowed_plus_all) - value(allowed_fields_reference)``."""
    if len(plus) != len(reference) or any(a.cluster != b.cluster for a, b in zip(plus, reference)):
        raise ValueError("the incremental entry needs the same targets in the same order") from None
    if not plus:
        return {"n_targets": 0, "advantage": None, "label": INCONCLUSIVE}
    values = [value(attack, a) - value(attack, b) for a, b in zip(plus, reference)]
    adv = bootstrap([o.cluster for o in plus], values, B=B, seed=seed)
    return {"n_targets": len(plus), "advantage": adv, "label": label(adv["ci_low"], adv["ci_high"])}


# --------------------------------------------------------------------------------------------------- applicability

def applicability(variant_id: str, variant_kind: str, variant_k: int, attack: str,
                  artifact_type: str) -> tuple[str, str | None]:
    """``(status, reason)``: ``applicable`` (reason None), ``not_applicable`` or ``not_run``, from the static table:
    the derived variants' rules first, then volume's A6, then the attack-by-artifact table (A1_calibrated only on a
    type with a shadow analogue), then A4 at k = 2."""
    if variant_id == "k1_reference":
        if artifact_type not in K1_TYPES or attack not in ATTACKS[:6]:
            return "not_applicable", K1_REASON
    elif variant_id == "a5_injected":
        if attack != "A5" or artifact_type not in A5_INJECTED_TYPES:
            return "not_applicable", INJECTED_REASON
    elif variant_id == "volume" and attack == "A6":
        return "not_run", VOLUME_A6_REASON
    if attack == "A6":
        return ("applicable", None) if artifact_type == "verdicts_active" else ("not_applicable", A6_REASON)
    if attack == "A4" and artifact_type not in A4_TYPES:
        return "not_applicable", A4_REASON
    if attack in ("A1_calibrated", "A1_fixed", "A2", "A3") and artifact_type == "usage_summary":
        return "not_applicable", USAGE_REASON
    if attack == "A1_calibrated" and artifact_type not in A1_CALIBRATED_TYPES:
        return "not_applicable", A1_SHADOW_REASON
    if attack == "A4" and variant_k <= 2 and variant_id != "k1_reference":
        return "not_applicable", A4_K2_REASON
    return "applicable", None
