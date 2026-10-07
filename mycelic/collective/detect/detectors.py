"""Model-free detection at HQ: the detectors D2 to D7 and the rules channel over the k-suppressed weekly cells, walked
week by week with an alert budget and a cooldown.

:func:`run_detection` is pure: it takes the frozen pack (every parameter comes from its ``detectors.json``, pinned by
``detector_hash``), the org config, the bundles and cells a run may see, the run's ``as_of``, its channel and a tie
salt, and returns the result JSON object. :func:`detect` feeds it from a :class:`~.store.CollectiveStore`.

Rules:

* **Channels.** Run ``X`` reads ``codes`` and ``text_only`` cells; run ``S`` (the baseline) reads ``codes`` cells
  only, so nothing in an S run (history, eligibility, rules) depends on text. In X a series' weekly value at a site
  combines its two cells, which count disjoint record sets.
* **No look-ahead.** A run at ``as_of`` D uses only bundles with ``as_of <= D`` and weeks up to
  ``closed_through(D)``; anything else is counted in ``ignored_cells``. Step W reads only weeks <= W. A site has
  reported W when its loaded bundles cover W (the on-time-reporting assumption: a bundle that reached HQ after W
  closed is used at step W). The step's own ``as_of_W = min(D, sunday(W) + close_lag_days + 6 days)`` is used only
  for staleness.
* **Imputation.** A count is ``(v, v)`` for an int and ``(1, k - 1)`` for ``'<k'``; an absent cell is ``(0, 0)``.
  Every use takes the side that makes a detector less likely to fire: evidence (window counts, the key's own PMI
  count in the window, support, rule counts, independent roots) takes the lower bound, the null hypothesis (baseline
  rates, the key's own PMI count in the baseline) the upper bound, and the opposite sides for past exceedances (A1).
  D3's nuisance counts (the entity's other predicates, the predicate's other entities, the rest of the type) take
  one shared imputation in the window and the baseline, an int as is and ``'<k'`` as ``k / 2``, so suppressed
  background shifts both PMIs alike instead of deciding the rise.
* **D2 cross-site burst.** Per eligible site and per test: ``B = min(baseline_weeks, history)``, ``lam =
  max(lambda_floor, baseline ub / B)``, ``c = window lb``, ``logp = log P(Poisson(lam * window_weeks) >= c)``; a test
  certainly exceeds when ``c >= 1`` and ``logp < log(alpha_site / tests)``. S has one test (its codes cells). In X,
  once the series has cells in both channels at the site, there are two tests (Bonferroni over two): the combined
  count, and S's own codes count against a rate of at least the series' certain rate over both channels (combined
  baseline lb / B), so text-only background no longer delays a burst that S sees, and records moving between channels
  never look like a fresh series; before that the tests coincide and the one test runs at ``alpha_site``. The site
  certainly exceeds when one of its tests does, and the snapshot shows the test with the lowest ``logp``. ``p_s`` is
  the share of the site's past windows (ending at or before ``W - window_weeks``, each with enough history) whose
  exceedance was possible under the bounds (any test), clipped to ``[p_min, p_max]``. ``surprise = -log
  P(PoissonBinomial(p_s) >= m)`` over the ``n`` eligible sites, ``m`` of them exceeding; a candidate when ``m >=
  burst.min_sites``. A site whose history of the series is all ``'<k'`` stays in ``n`` (A2).
* **D3 co-occurrence lift.** Per eligible site, PMI of (entity, predicate) within the entity type, smoothed, derived
  from cells only; ``rise = pmi_window - pmi_baseline``; rising when ``rise > pmi_delta`` and the key's window lower
  bound is at least k; a candidate when ``cooccurrence.min_sites`` eligible sites rise.
* **D4 to D6.** ``res_conf`` (the lowest ``res_conf_min`` in the lineage), independent roots (lower bounds),
  ``root_ratio_ub`` and ``echo``, few reporters per site, the high base rate of the predicate among the key's own
  entity type, excluding the key itself (A3), short and suppressed histories; a candidate whose newest lineage week
  is more than ``stale_days`` before ``as_of_W`` is removed at that step.
* **D7 ranker.** ``score = logistic(bias + fsum(weight_f * feature_f))`` with the pack's default weights; nothing is
  fitted and nothing is learned.
* **Alerts.** Per week the candidates not cooling are ordered by ``(-score, sha256(tie_salt|key))`` and the first
  ``alert_budget_per_week`` alert; the lexical key never decides a tie. A key that alerted cools until it has been
  absent from the candidates for ``cooldown_weeks`` consecutive steps; a cooling candidate uses no budget. Rule hits
  are merged by key, never ranked and never use budget.
* **Determinism.** Never iterate a set or a set-derived dict without ``sorted()``; float sums via ``math.fsum`` or a
  fixed order; no ``hash()``; no clock and no entropy; the only randomness is none. The result is byte-identical under
  any ``PYTHONHASHSEED``.
* **Cost.** One pass over the cells builds per-(site, series), per-(site, series, channel) in X, per-(site, entity),
  per-(site, type, predicate) and per-(site, type) prefix arrays; the walk is then O((sites x series + rules x
  entities) x weeks) with no per-step scan of the cells.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .. import stats
from ..edge.weeks import closed_through, local_date, next_week, week_sunday
from ..jsonio import canonical_bytes, sha256_hex
from .org import OrgConfig
from .rules import RuleHit, evaluate_rule, series_key
from .store import RUN_CHANNELS, BundleRow, CellRow, CollectiveStore

if TYPE_CHECKING:
    from ..packs.loader import FrozenPack

SCHEMA_VERSION = 1
FEATURES = ("burst_surprise", "pmi_rise", "log_independent_roots", "supporting_sites", "low_res_conf", "echo",
            "few_reporters_share", "high_base_rate")
IGNORED_KEYS = ("after_last_week", "invisible_bundle", "not_in_org", "other_channel")
RUN_STATUSES = ("ok", "insufficient_baseline", "empty")
SITE_STATUSES = ("late", "short_history", "eligible")
DETECTOR_CHANNEL = "detector"
RULE_CHANNEL = "rule"
COMBINED_TEST = "combined"
TIE_SALT_RE = re.compile(r"[\x20-\x7e]{1,128}", re.ASCII)

Series = tuple[str, str, str]                     # (entity_type, entity_id, predicate)


class DetectionError(ValueError):
    pass


@dataclass(frozen=True)
class DetectorConfig:
    alert_budget_per_week: int
    cooldown_weeks: int
    baseline_weeks: int
    window_weeks: int
    min_history_weeks: int
    alpha_site: float
    lambda_floor: float
    p_min: float
    p_max: float
    burst_min_sites: int
    pmi_smoothing: float
    pmi_delta: float
    cooccurrence_min_sites: int
    res_conf_threshold: float
    echo_min_ratio: float
    stale_days: int
    base_rate_site_fraction: float
    bias: float
    weights: Mapping[str, float]
    k: int
    close_lag_days: int

    @classmethod
    def from_pack(cls, pack: "FrozenPack") -> "DetectorConfig":
        d = pack.detectors
        burst, cooc = d["burst"], d["cooccurrence"]
        return cls(alert_budget_per_week=d["alert_budget_per_week"], cooldown_weeks=d["cooldown_weeks"],
                   baseline_weeks=d["baseline_weeks"], window_weeks=d["window_weeks"],
                   min_history_weeks=d["min_history_weeks"], alpha_site=burst["alpha_site"],
                   lambda_floor=burst["lambda_floor"], p_min=burst["p_min"], p_max=burst["p_max"],
                   burst_min_sites=burst["min_sites"], pmi_smoothing=cooc["pmi_smoothing"],
                   pmi_delta=cooc["pmi_delta"], cooccurrence_min_sites=cooc["min_sites"],
                   res_conf_threshold=d["resolution"]["res_conf_min"],
                   echo_min_ratio=d["independence"]["echo_min_ratio"], stale_days=d["decoy"]["stale_days"],
                   base_rate_site_fraction=d["decoy"]["base_rate_site_fraction"], bias=d["ranker"]["bias"],
                   weights=MappingProxyType({f: d["ranker"]["weights"][f] for f in FEATURES}), k=pack.egress.k,
                   close_lag_days=pack.egress.close_lag_days)


def bounds(value: int | None, k: int) -> tuple[int, int]:
    """``(value, value)`` for an int count, ``(1, k - 1)`` for ``'<k'`` (None)."""
    return (1, k - 1) if value is None else (value, value)


def twice_imputed(value: int | None, k: int) -> int:
    """Twice D3's shared nuisance imputation: ``2 * value`` for an int count, ``k`` (``'<k'`` as ``k / 2``) for None;
    kept doubled so the prefix sums stay ints."""
    return k if value is None else 2 * value


def result_bytes(result: Mapping[str, Any]) -> bytes:
    return canonical_bytes(result)


def _check_args(as_of: Any, run_channel: Any, tie_salt: Any) -> None:
    if not isinstance(as_of, str) or local_date(as_of) != as_of:
        raise DetectionError("as_of must be a calendar date YYYY-MM-DD") from None
    if not isinstance(run_channel, str) or run_channel not in RUN_CHANNELS:
        raise DetectionError("run_channel must be X or S") from None
    if not isinstance(tie_salt, str) or TIE_SALT_RE.fullmatch(tie_salt) is None:
        raise DetectionError("tie_salt must be 1 to 128 printable ASCII characters") from None


def _prefix(values: Sequence[int]) -> list[int]:
    out = [0] * (len(values) + 1)
    for i, v in enumerate(values):
        out[i + 1] = out[i] + v
    return out


def _span(prefix: list[int] | None, lo: int, hi: int) -> int:
    """The sum of weeks ``lo..hi`` (inclusive, ``lo`` clipped at 0); 0 for an empty span or an absent series."""
    lo = max(lo, 0)
    if prefix is None or hi < lo:
        return 0
    return prefix[hi + 1] - prefix[lo]


def _cell_order(c: CellRow) -> tuple[str, ...]:
    return (c.site, c.entity_type, c.entity_id, c.predicate, c.iso_week, c.channel)


def _lineage_item(c: CellRow) -> dict[str, Any]:
    return {"site": c.site, "entity_type": c.entity_type, "entity_id": c.entity_id, "predicate": c.predicate,
            "iso_week": c.iso_week, "channel": c.channel, "bundle": c.bundle}


class _Index:
    """Prefix arrays and per-week precomputations over the cells of one run."""

    def __init__(self, cfg: DetectorConfig, org: OrgConfig, bundles: Sequence[BundleRow], used: Sequence[CellRow],
                 last_week: str, channels: Sequence[str]) -> None:
        self.cfg = cfg
        self.org = org
        self.channels = tuple(channels)
        self.split_tests = ((COMBINED_TEST,) if self.channels == RUN_CHANNELS["S"]
                            else (COMBINED_TEST, *RUN_CHANNELS["S"]))
        self.sites = tuple(sorted(org.sites))
        weeks = [min(c.iso_week for c in used)]
        while weeks[-1] < last_week:
            weeks.append(next_week(weeks[-1]))
        self.weeks = tuple(weeks)
        self.pos = {w: i for i, w in enumerate(weeks)}
        nw = len(weeks)
        self.reported_through: dict[str, str] = {}
        for b in bundles:
            if b.closed_through > self.reported_through.get(b.site, ""):
                self.reported_through[b.site] = b.closed_through
        k = cfg.k
        raw: dict[tuple[Any, ...], tuple[list[int], list[int], list[int]]] = {}
        self.cells_of: dict[tuple[str, Series], list[tuple[int, CellRow]]] = {}
        self.start: dict[str, int] = {}
        entities: dict[tuple[str, str, str], list[str]] = {}
        first_in: dict[tuple[str, Series], dict[str, int]] = {}

        def add(key: tuple[Any, ...], i: int, lo: int, hi: int, mid2: int) -> None:
            arrays = raw.get(key)
            if arrays is None:
                arrays = raw[key] = ([0] * nw, [0] * nw, [0] * nw)
            arrays[0][i] += lo
            arrays[1][i] += hi
            arrays[2][i] += mid2

        for c in used:
            i = self.pos[c.iso_week]
            lo, hi = bounds(c.n, k)
            mid2 = twice_imputed(c.n, k)
            ser = (c.entity_type, c.entity_id, c.predicate)
            add(("s", c.site, ser), i, lo, hi, mid2)
            if len(self.split_tests) > 1:
                add(("c", c.site, ser, c.channel), i, lo, hi, mid2)
                seen = first_in.setdefault((c.site, ser), {})
                seen[c.channel] = min(seen.get(c.channel, nw), i)
            add(("e", c.site, c.entity_type, c.entity_id), i, lo, hi, mid2)
            add(("p", c.site, c.entity_type, c.predicate), i, lo, hi, mid2)
            add(("t", c.site, c.entity_type), i, lo, hi, mid2)
            cell_list = self.cells_of.setdefault((c.site, ser), [])
            if not cell_list:
                entities.setdefault((c.site, c.entity_type, c.predicate), []).append(c.entity_id)
            cell_list.append((i, c))
            if i < self.start.get(c.site, nw):
                self.start[c.site] = i
        self.lb = {key: _prefix(arrays[0]) for key, arrays in raw.items()}
        self.ub = {key: _prefix(arrays[1]) for key, arrays in raw.items()}
        self.mid2 = {key: _prefix(arrays[2]) for key, arrays in raw.items() if key[0] != "c"}
        # per (site, series) in X: the first week index with cells of the series in every channel (the two D2 tests
        # are distinct from then on)
        self.split_from = {sk: max(seen.values()) for sk, seen in sorted(first_in.items())
                           if len(seen) == len(self.channels)}
        self.entities = {key: tuple(sorted(ids)) for key, ids in sorted(entities.items())}
        series_sites: dict[Series, list[str]] = {}
        for site, ser in sorted(self.cells_of):
            series_sites.setdefault(ser, []).append(site)
            self.cells_of[(site, ser)].sort(key=lambda item: (item[0], item[1].channel))
        self.series_sites = {ser: tuple(series_sites[ser]) for ser in sorted(series_sites)}
        self.log_alpha = {n: math.log(cfg.alpha_site / n) for n in sorted({1, len(self.split_tests)})}
        self._logsf: dict[tuple[int, float], float] = {}
        # per (site, series): certain exceedance per week and a prefix count of possible exceedances (A1); per
        # (site, entity type, predicate): how many series certainly exceed each week (A3)
        self.exceeded: dict[tuple[str, Series], bytearray] = {}
        self.possible: dict[tuple[str, Series], list[int]] = {}
        self.exceeding: dict[tuple[str, str, str], list[int]] = {}
        for site, ser in sorted(self.cells_of):
            exc = bytearray(nw)
            possible = [0] * nw
            counts = self.exceeding.setdefault((site, ser[0], ser[2]), [0] * nw)
            for i in range(self.history_start(site), nw):
                exc[i] = self.d2_site(site, ser, i)[5]
                possible[i] = self.possible_at(site, ser, i)
                counts[i] += exc[i]
            self.exceeded[(site, ser)] = exc
            self.possible[(site, ser)] = _prefix(possible)

    # ---------------------------------------------------------------- per-site quantities
    def logsf(self, c: int, lam: float) -> float:
        key = (c, lam)
        value = self._logsf.get(key)
        if value is None:
            value = self._logsf[key] = stats.poisson_logsf(c, lam)
        return value

    def history(self, site: str, i: int) -> int:
        start = self.start.get(site)
        return 0 if start is None else max(0, i - self.cfg.window_weeks - start + 1)

    def history_start(self, site: str) -> int:
        """The first week index at which ``site`` has ``min_history_weeks`` of history."""
        return self.start[site] + self.cfg.window_weeks + self.cfg.min_history_weeks - 1

    def status(self, site: str, i: int) -> str:
        through = self.reported_through.get(site)
        if through is None or through < self.weeks[i]:
            return "late"
        return "eligible" if self.history(site, i) >= self.cfg.min_history_weeks else "short_history"

    def window_lb(self, site: str, ser: Series, i: int) -> int:
        return _span(self.lb.get(("s", site, ser)), i - self.cfg.window_weeks + 1, i)

    def tests_at(self, site: str, ser: Series, i: int) -> tuple[str, ...]:
        """The D2 tests of a site's series at week index ``i``: the combined count alone, or in X, once the series has
        cells in both channels, the combined count and S's own codes count."""
        split = self.split_from.get((site, ser))
        return self.split_tests if split is not None and split <= i else (COMBINED_TEST,)

    @staticmethod
    def _test_key(test: str, site: str, ser: Series) -> tuple[Any, ...]:
        return ("s", site, ser) if test == COMBINED_TEST else ("c", site, ser, test)

    def d2_site(self, site: str, ser: Series, i: int) -> tuple[str, int, float, float, float, bool]:
        """``(test, c, lam, expected, logp, exceeded)`` of a site with history at week index ``i``: each test is the
        window lb against the baseline ub at ``alpha_site / tests``, and the codes test's rate is at least the
        series' certain rate over both channels (combined baseline lb), so records that move between channels never
        look like a fresh series; the site exceeds when one test does, and the values are those of the test with the
        lowest ``logp`` (the first test on a tie)."""
        cfg = self.cfg
        b_eff = min(cfg.baseline_weeks, self.history(site, i))
        top = i - cfg.window_weeks
        tests = self.tests_at(site, ser, i)
        log_alpha = self.log_alpha[len(tests)]
        certain = _span(self.lb.get(("s", site, ser)), top - b_eff + 1, top) / b_eff
        best: tuple[str, int, float, float, float, bool] | None = None
        for test in tests:
            key = self._test_key(test, site, ser)
            c = _span(self.lb.get(key), i - cfg.window_weeks + 1, i)
            lam = max(cfg.lambda_floor, _span(self.ub.get(key), top - b_eff + 1, top) / b_eff, certain)
            expected = lam * cfg.window_weeks
            logp = self.logsf(c, expected)
            if best is None or logp < best[4]:
                best = (test, c, lam, expected, logp, c >= 1 and logp < log_alpha)
        assert best is not None
        return best

    def possible_at(self, site: str, ser: Series, i: int) -> int:
        """1 when an exceedance at week index ``i`` was possible under the bounds in any of the site's tests (window
        ub against baseline lb)."""
        cfg = self.cfg
        b_eff = min(cfg.baseline_weeks, self.history(site, i))
        top = i - cfg.window_weeks
        tests = self.tests_at(site, ser, i)
        log_alpha = self.log_alpha[len(tests)]
        certain = _span(self.lb.get(("s", site, ser)), top - b_eff + 1, top) / b_eff
        for test in tests:
            key = self._test_key(test, site, ser)
            c = _span(self.ub.get(key), i - cfg.window_weeks + 1, i)
            lam = max(cfg.lambda_floor, _span(self.lb.get(key), top - b_eff + 1, top) / b_eff, certain)
            if c >= 1 and self.logsf(c, lam * cfg.window_weeks) < log_alpha:
                return 1
        return 0

    def p_s(self, site: str, ser: Series, i: int) -> float:
        """The share of past windows (ending at or before ``i - window_weeks``, each with enough history) whose
        exceedance was possible, clipped to ``[p_min, p_max]``; ``p_max`` when there is none."""
        cfg = self.cfg
        first, last = self.history_start(site), i - cfg.window_weeks
        if last < first:
            return cfg.p_max
        prefix = self.possible.get((site, ser))
        count = prefix[last + 1] - prefix[first] if prefix is not None else 0
        return min(cfg.p_max, max(cfg.p_min, count / (last - first + 1)))

    def suppressed_history(self, site: str, ser: Series, i: int) -> bool:
        before = [c for j, c in self.cells_of.get((site, ser), ()) if j <= i - self.cfg.window_weeks]
        return bool(before) and all(c.n is None for c in before)

    def _pmi(self, keys: Sequence[tuple[Any, ...]], lo: int, hi: int, own: int) -> float:
        """The smoothed PMI over weeks ``lo..hi`` with the key's own count ``own`` in all four counts and every other
        cell (the entity's other predicates, the predicate's other entities, the rest of the type) at the shared
        imputation."""
        own2, e2, p2, n2 = (_span(self.mid2.get(key), lo, hi) for key in keys)
        return stats.smoothed_pmi(own, own + (e2 - own2) / 2, own + (p2 - own2) / 2, own + (n2 - own2) / 2,
                                  self.cfg.pmi_smoothing)

    def d3_site(self, site: str, ser: Series, i: int) -> tuple[int, float, float, float, bool]:
        """``(n_ep_lb, pmi_window, pmi_baseline, rise, rising)`` of an eligible site at week index ``i``: the key's
        own count takes its lower bound in the window and its upper bound in the baseline."""
        cfg = self.cfg
        t, eid, p = ser
        keys = (("s", site, ser), ("e", site, t, eid), ("p", site, t, p), ("t", site, t))
        lo, hi = i - cfg.window_weeks + 1, i
        n_ep = _span(self.lb.get(keys[0]), lo, hi)
        pmi_window = self._pmi(keys, lo, hi, n_ep)
        top = i - cfg.window_weeks
        lo = max(self.start[site], top - cfg.baseline_weeks + 1)
        pmi_baseline = self._pmi(keys, lo, top, _span(self.ub.get(keys[0]), lo, top))
        rise = pmi_window - pmi_baseline
        return n_ep, pmi_window, pmi_baseline, rise, rise > cfg.pmi_delta and n_ep >= cfg.k

    def window_cells(self, site: str, ser: Series, lo: int, hi: int) -> list[CellRow]:
        return [c for j, c in self.cells_of.get((site, ser), ()) if max(lo, 0) <= j <= hi]

    # ---------------------------------------------------------------- one candidate at one week
    def detail(self, ser: Series, i: int, status: Mapping[str, str], as_of_w: str) -> dict[str, Any]:
        """The detector part of a candidate at week index ``i``: D2 and D3 per org site, D4 to D6, features and
        score. ``status`` maps every org site to its status at ``i``."""
        cfg = self.cfg
        k = cfg.k
        eligible = [s for s in self.sites if status[s] == "eligible"]
        d2_sites, d3_sites, ps, exceeded, rising, rises, suppressed = [], [], [], [], [], [], []
        for s in self.sites:
            if status[s] != "eligible":
                d2_sites.append({"site": s, "status": status[s], "history_weeks": self.history(s, i), "test": None,
                                 "c": None, "baseline_rate": None, "expected": None, "logp": None, "exceeded": None,
                                 "p_s": None, "suppressed_history": None})
                d3_sites.append({"site": s, "status": status[s], "n_ep_lb": None, "pmi_window": None,
                                 "pmi_baseline": None, "rise": None, "rising": None})
                continue
            test, c, lam, expected, logp, is_exceeded = self.d2_site(s, ser, i)
            p = self.p_s(s, ser, i)
            sup = self.suppressed_history(s, ser, i)
            ps.append(p)
            if is_exceeded:
                exceeded.append(s)
            if sup:
                suppressed.append(s)
            d2_sites.append({"site": s, "status": "eligible", "history_weeks": self.history(s, i), "test": test,
                             "c": c, "baseline_rate": lam, "expected": expected, "logp": logp,
                             "exceeded": is_exceeded, "p_s": p, "suppressed_history": sup})
            n_ep, pmi_window, pmi_baseline, rise, is_rising = self.d3_site(s, ser, i)
            if is_rising:
                rising.append(s)
                rises.append(rise)
            d3_sites.append({"site": s, "status": "eligible", "n_ep_lb": n_ep, "pmi_window": pmi_window,
                             "pmi_baseline": pmi_baseline, "rise": rise, "rising": is_rising})
        m = len(exceeded)
        surprise = 0.0 if m == 0 else 0.0 - stats.poisson_binomial_logsf(ps, m)
        lo = i - cfg.window_weeks + 1
        contributing = [s for s in self.sites if status[s] != "late" and self.window_cells(s, ser, lo, i)]
        lineage = [c for s in contributing for c in self.window_cells(s, ser, lo, i)]
        lineage.sort(key=_cell_order)
        supporting = sorted(set(exceeded) | set(rising))
        confs = [c.res_conf_min for c in lineage if c.n is not None]
        res_conf = min(confs) if confs else None
        independent_roots = sum(bounds(c.n_roots, k)[0] for c in lineage)
        ratio_num = sum(min(bounds(c.n_roots, k)[1], bounds(c.n, k)[1]) for c in lineage)
        ratio_den = sum(bounds(c.n, k)[0] for c in lineage)
        root_ratio_ub = min(1.0, ratio_num / ratio_den)
        echo = root_ratio_ub < cfg.echo_min_ratio
        few = sorted({c.site for c in lineage if c.n is not None and c.n_reporters is None})
        others = 0
        for s in eligible:
            row = self.exceeding.get((s, ser[0], ser[2]))
            own = self.exceeded.get((s, ser))
            if row is not None and row[i] - (own[i] if own is not None else 0) > 0:
                others += 1
        high_base_rate = others / len(eligible) > cfg.base_rate_site_fraction
        low_res_conf = res_conf is not None and res_conf < cfg.res_conf_threshold
        features = {"burst_surprise": surprise, "pmi_rise": math.fsum(rises) / len(rises) if rises else 0.0,
                    "log_independent_roots": math.log1p(independent_roots),
                    "supporting_sites": float(len(supporting)), "low_res_conf": 1.0 if low_res_conf else 0.0,
                    "echo": 1.0 if echo else 0.0, "few_reporters_share": len(few) / len(contributing),
                    "high_base_rate": 1.0 if high_base_rate else 0.0}
        score = stats.logistic(cfg.bias + math.fsum(cfg.weights[f] * features[f] for f in FEATURES))
        unit = self.org.decision_unit(supporting)
        return {"week": self.weeks[i], "as_of": as_of_w, "window": [self.weeks[max(lo, 0)], self.weeks[i]],
                "score": score, "features": features,
                "flags": {"echo": echo, "few_reporters_sites": few, "high_base_rate": high_base_rate,
                          "short_history_sites": [s for s in contributing if status[s] == "short_history"],
                          "suppressed_history_sites": suppressed},
                "res_conf": res_conf, "independent_roots": independent_roots, "root_ratio_ub": root_ratio_ub,
                "d2": {"m": m, "n": len(eligible), "surprise": surprise, "sites": d2_sites},
                "d3": {"rising": len(rising), "sites": d3_sites}, "supporting_sites": supporting,
                "contributing_sites": contributing, "decision_unit": unit,
                "lineage": [_lineage_item(c) for c in lineage]}


def _as_of_at(week: str, as_of: str, close_lag_days: int) -> str:
    """The step's own as_of: ``min(D, sunday(W) + close_lag_days + 6 days)``."""
    return min(as_of, (week_sunday(week) + timedelta(days=close_lag_days + 6)).isoformat())


def _stale(detail: Mapping[str, Any], stale_days: int) -> bool:
    newest = max(item["iso_week"] for item in detail["lineage"])
    return (date.fromisoformat(detail["as_of"]) - week_sunday(newest)).days > stale_days


def _rule_part(idx: _Index, ser: Series, hits: Sequence[RuleHit], weeks: Sequence[str],
               rules: Mapping[str, Any]) -> dict[str, Any]:
    """A key's rule part at its first rule week; ``hits`` are that week's hits of the key (one per rule)."""
    i = idx.pos[hits[0].week]
    sites = sorted((site, h.rule_id, count) for h in hits for site, count in h.sites)
    cells: dict[tuple[str, ...], CellRow] = {}
    starts = []
    for h in hits:
        lo = i - rules[h.rule_id].window_weeks + 1
        starts.append(max(lo, 0))
        for site, _ in h.sites:
            cells.update((_cell_order(c), c) for c in idx.window_cells(site, ser, lo, i))
    lineage = [cells[key] for key in sorted(cells)]
    return {"rule_ids": sorted({h.rule_id for h in hits}), "first_week": hits[0].week, "weeks": list(weeks),
            "window": [idx.weeks[min(starts)], hits[0].week],
            "sites": [{"site": s, "rule_id": r, "count_lb": n} for s, r, n in sites],
            "decision_unit": idx.org.decision_unit(s for s, _, _ in sites),
            "lineage": [_lineage_item(c) for c in lineage]}


def run_detection(pack: "FrozenPack", org: OrgConfig, *, bundles: Sequence[BundleRow], cells: Sequence[CellRow],
                  as_of: str, run_channel: str, tie_salt: str) -> dict[str, Any]:
    _check_args(as_of, run_channel, tie_salt)
    cfg = DetectorConfig.from_pack(pack)
    channels = RUN_CHANNELS[run_channel]
    last_week = closed_through(as_of, cfg.close_lag_days)
    loaded = [b for b in bundles if b.as_of <= as_of]
    loaded_shas = frozenset(b.sha256 for b in loaded)
    visible = sorted((b for b in loaded if b.site in org.sites), key=lambda b: (b.site, b.closed_through, b.sha256))
    ignored = dict.fromkeys(IGNORED_KEYS, 0)
    used = []
    for c in cells:
        if c.iso_week > last_week:
            ignored["after_last_week"] += 1
        elif c.bundle not in loaded_shas or c.as_of > as_of:
            ignored["invisible_bundle"] += 1
        elif c.site not in org.sites:
            ignored["not_in_org"] += 1
        elif c.channel not in channels:
            ignored["other_channel"] += 1
        else:
            used.append(c)
    used.sort(key=_cell_order)
    shas = sorted(b.sha256 for b in visible)
    identity = {"run_channel": run_channel, "as_of": as_of, "tie_salt": tie_salt, "config_hash": pack.config_hash,
                "detector_hash": pack.detector_hash, "org_hash": org.org_hash, "bundles": shas}
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "run_id": "det-" + sha256_hex(canonical_bytes(identity))[:16],
        "pack": pack.id, "config_hash": pack.config_hash, "detector_hash": pack.detector_hash,
        "org_hash": org.org_hash, "run_channel": run_channel, "cell_channels": list(channels), "as_of": as_of,
        "first_week": None, "last_week": last_week, "tie_salt": tie_salt, "bundles": len(visible),
        "bundles_sha256": sha256_hex("\n".join(shas)), "cells": len(used), "ignored_cells": ignored,
        "status": "empty", "insufficient_baseline_weeks": 0, "weeks": [], "alerts": [], "candidates": [],
        "rule_hits": []}
    if not used:
        return result
    idx = _Index(cfg, org, visible, used, last_week, channels)
    result["first_week"] = idx.weeks[0]
    _walk(idx, pack, as_of, run_channel, tie_salt, result)
    return result


def _walk(idx: _Index, pack: "FrozenPack", as_of: str, run_channel: str, tie_salt: str,
          result: dict[str, Any]) -> None:
    cfg = idx.cfg
    needed = min(cfg.burst_min_sites, cfg.cooccurrence_min_sites)
    cooling: dict[str, int] = {}
    candidate_weeks: dict[str, list[str]] = {}
    alert_weeks: dict[str, list[str]] = {}
    detectors: dict[str, set[str]] = {}
    first_detail: dict[str, dict[str, Any]] = {}
    alert_detail: dict[str, dict[str, Any]] = {}
    rule_weeks: dict[tuple[str, str], list[str]] = {}
    rule_first: dict[tuple[str, str], RuleHit] = {}
    key_rule_hits: dict[str, list[RuleHit]] = {}
    key_rule_weeks: dict[str, list[str]] = {}
    series = {series_key(*ser): ser for ser in idx.series_sites}
    for i, week in enumerate(idx.weeks):
        as_of_w = _as_of_at(week, as_of, cfg.close_lag_days)
        status = {s: idx.status(s, i) for s in idx.sites}
        eligible = [s for s in idx.sites if status[s] == "eligible"]
        reported = [s for s in idx.sites if status[s] != "late"]
        found: dict[str, tuple[dict[str, Any], list[str]]] = {}
        stale_removed = 0
        if eligible:
            for ser, sites_with in idx.series_sites.items():
                with_cells = [s for s in sites_with if status[s] == "eligible"]
                if len(with_cells) < needed:
                    continue
                m = sum(idx.exceeded[(s, ser)][i] for s in with_cells)
                rising = sum(1 for s in with_cells
                             if idx.window_lb(s, ser, i) >= cfg.k and idx.d3_site(s, ser, i)[4])
                which = [name for name, fired in (("d2", m >= cfg.burst_min_sites),
                                                  ("d3", rising >= cfg.cooccurrence_min_sites)) if fired]
                if not which:
                    continue
                detail = idx.detail(ser, i, status, as_of_w)
                if _stale(detail, cfg.stale_days):
                    stale_removed += 1
                    continue
                found[series_key(*ser)] = (detail, which)
        # rules: lower-bound counts of reported sites over each rule's own window
        step_hits: list[RuleHit] = []
        for rule_id, rule in pack.rules.items():
            lo = i - rule.window_weeks + 1
            counts = {}
            for s in reported:
                per = {}
                for eid in idx.entities.get((s, rule.entity_type, rule.predicate), ()):
                    n = _span(idx.lb.get(("s", s, (rule.entity_type, eid, rule.predicate))), lo, i)
                    if n > 0:
                        per[eid] = n
                counts[s] = per
            step_hits += evaluate_rule(rule, counts, week=week)
        for hit in step_hits:
            rule_weeks.setdefault((hit.rule_id, hit.key), []).append(week)
            rule_first.setdefault((hit.rule_id, hit.key), hit)
            if hit.key not in key_rule_weeks:
                key_rule_weeks[hit.key] = [week]
                key_rule_hits[hit.key] = [hit]
            elif key_rule_weeks[hit.key][0] == week:
                key_rule_hits[hit.key].append(hit)
            elif key_rule_weeks[hit.key][-1] != week:
                key_rule_weeks[hit.key].append(week)
        # cooldown: a cooling key present now restarts its quiet count; absent for cooldown_weeks steps, it is released
        for key in sorted(cooling):
            if key in found:
                cooling[key] = 0
            else:
                cooling[key] += 1
                if cooling[key] >= cfg.cooldown_weeks:
                    del cooling[key]
        cooling_now = [key for key in sorted(found) if key in cooling]
        ranked = sorted((key for key in sorted(found) if key not in cooling),
                        key=lambda key: (-found[key][0]["score"], sha256_hex(f"{tie_salt}|{key}")))
        alerts = ranked[:cfg.alert_budget_per_week]
        for key in sorted(found):
            detail, which = found[key]
            candidate_weeks.setdefault(key, []).append(week)
            detectors.setdefault(key, set()).update(which)
            first_detail.setdefault(key, detail)
        for rank, key in enumerate(alerts, start=1):
            result["alerts"].append({"week": week, "rank": rank, "key": key, "score": found[key][0]["score"]})
            alert_weeks.setdefault(key, []).append(week)
            alert_detail.setdefault(key, found[key][0])
            if cfg.cooldown_weeks > 0:
                cooling[key] = 0
        step_status = "ok" if eligible else "insufficient_baseline"
        result["insufficient_baseline_weeks"] += int(step_status == "insufficient_baseline")
        result["weeks"].append({"week": week, "as_of": as_of_w, "status": step_status,
                                "reported_sites": len(reported), "eligible_sites": len(eligible),
                                "late_sites": [s for s in idx.sites if status[s] == "late"], "candidates": len(found),
                                "stale_removed": stale_removed, "cooling": len(cooling_now),
                                "rule_hits": len(step_hits), "alerts": alerts})
    result["status"] = "ok" if result["insufficient_baseline_weeks"] < len(idx.weeks) else "insufficient_baseline"
    result["rule_hits"] = [
        {"rule_id": rule_id, "key": key, "first_week": rule_first[(rule_id, key)].week,
         "weeks": rule_weeks[(rule_id, key)],
         "sites_at_first": [{"site": s, "count_lb": n} for s, n in rule_first[(rule_id, key)].sites]}
        for rule_id, key in sorted(rule_weeks)]
    for key in sorted(set(candidate_weeks) | set(key_rule_weeks)):
        ser = series[key]                             # a rule key has a lower-bound count > 0, so it has cells
        snapshot = alert_detail.get(key, first_detail.get(key))
        rule = (_rule_part(idx, ser, key_rule_hits[key], key_rule_weeks[key], pack.rules)
                if key in key_rule_weeks else None)
        channels = [name for name, present in ((DETECTOR_CHANNEL, key in candidate_weeks),
                                               (RULE_CHANNEL, rule is not None)) if present]
        weeks_alerted = alert_weeks.get(key, [])
        result["candidates"].append({
            "key": key, "entity_type": ser[0], "entity_id": ser[1], "predicate": ser[2], "run_channel": run_channel,
            "channels": channels, "detectors": sorted(detectors.get(key, ())),
            "first_candidate_week": candidate_weeks[key][0] if key in candidate_weeks else None,
            "candidate_weeks": candidate_weeks.get(key, []), "alert_weeks": weeks_alerted,
            "detection_week": weeks_alerted[0] if weeks_alerted else None,
            "decision_unit": snapshot["decision_unit"] if snapshot is not None else rule["decision_unit"],
            "config_hash": pack.config_hash, "detector_hash": pack.detector_hash, "org_hash": idx.org.org_hash,
            "snapshot": snapshot, "rule": rule})


def detect(store: CollectiveStore, *, as_of: str, run_channel: str, tie_salt: str) -> dict[str, Any]:
    _check_args(as_of, run_channel, tie_salt)
    bundles, cells = store.detection_inputs(as_of, run_channel)
    return run_detection(store.pack, store.org, bundles=bundles, cells=cells, as_of=as_of, run_channel=run_channel,
                         tie_salt=tie_salt)
