"""The outcome check (G7): after a follow-up executed, did the failure mode recur? Measurement only; not causal, no
counterfactual.

Built ahead of E2 and X4 (STRATEGY sections 5.5 and 7): approval-routed follow-up is unvalidated; nothing here
measures it.

:func:`evaluate` is pure. Over the conclusion's key, its contributing sites and both channels (codes and text_only):

* windows: the original window of ``L`` ISO weeks; the post window of ``L`` weeks from ``post_start`` (default the
  week after the original window; one at or before the original window's end, or not an ISO week, is
  :class:`OutcomeError`); the baseline, the ``baseline_weeks`` (pack detectors) weeks before the original window;
* each cell counts ``[n, n]``, and a ``'<k'`` cell ``[1, k - 1]`` (as G4 imputes it); ``baseline_lb/ub`` and
  ``post_lb/ub`` sum them; ``expected_ub = max(lambda_floor * sites, baseline_ub / B) * L`` and ``expected_lb =
  baseline_lb / B * L`` with ``B = baseline_weeks``;
* the decision, the first that applies: the post window is not yet closed at ``as_of`` or a site's coverage (its last
  closed week received) ends before it, ``insufficient_data`` (``post_window_incomplete``); more than half the post
  cells are ``'<k'``, ``insufficient_data`` (``mostly_suppressed``); ``post_lb >= 1`` and
  ``poisson_sf(post_lb, expected_ub) < alpha_site``, ``recurred``; ``post_ub <= expected_lb``, ``not_recurred``; else
  ``insufficient_data`` (``inconclusive``).

Every result carries :data:`~.policy.OUTCOME_LABEL`. :func:`check` feeds :func:`evaluate` from HQ's store
(``HqReader``: the key's cells visible at the UTC date of ``as_of`` and the sites' coverage).
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from ..edge.egress import SCHEMA_VERSION
from ..edge.weeks import closed_through, iso_week, valid_week, week_monday
from ..stats import poisson_logsf, poisson_sf
from .policy import OUTCOME_LABEL, utc_date

if TYPE_CHECKING:
    from ..detect.store import CellRow, HqReader
    from ..packs.loader import FrozenPack
    from .service import ConclusionView

STATUSES = ("recurred", "not_recurred", "insufficient_data")
REASONS = (None, "post_window_incomplete", "mostly_suppressed", "inconclusive")
CHANNELS = ("codes", "text_only")


class OutcomeError(ValueError):
    """A malformed outcome check; the text is fixed and never holds a value."""


def _shift(week: str, n: int) -> str:
    return iso_week(week_monday(week) + timedelta(weeks=n))


def _weeks(start: str, end: str) -> int:
    return (week_monday(end) - week_monday(start)).days // 7 + 1


def windows(pack: "FrozenPack", window: Sequence[str],
            post_start: str | None = None) -> tuple[tuple[str, str], tuple[str, str]]:
    """``(baseline window, post window)`` for an original window ``(start, end)``."""
    start, end = window
    length = _weeks(start, end)
    if post_start is None:
        post_start = _shift(end, 1)
    elif not valid_week(post_start) or post_start <= end:
        raise OutcomeError("the post window overlaps the original window") from None
    baseline = pack.detectors["baseline_weeks"]
    return (_shift(start, -baseline), _shift(start, -1)), (post_start, _shift(post_start, length - 1))


def evaluate(pack: "FrozenPack", *, conclusion_id: str, candidate_key: str, window: Sequence[str],
             post_start: str | None, sites: Iterable[str], cells: Iterable["CellRow"], coverage: Mapping[str, str],
             as_of: str) -> dict[str, Any]:
    sites = sorted(set(sites))
    if not sites:
        raise OutcomeError("an outcome needs at least one site") from None
    k = pack.egress.k
    (base_start, base_end), (post_start, post_end) = windows(pack, window, post_start)
    length = _weeks(window[0], window[1])
    weeks_b = pack.detectors["baseline_weeks"]
    floor = pack.detectors["burst"]["lambda_floor"]
    alpha = pack.detectors["burst"]["alpha_site"]
    baseline_lb = baseline_ub = post_lb = post_ub = 0
    post_cells = suppressed = 0
    for c in cells:
        if c.site not in sites or c.channel not in CHANNELS:
            continue
        lb, ub = (c.n, c.n) if c.n is not None else (1, k - 1)
        if base_start <= c.iso_week <= base_end:
            baseline_lb += lb
            baseline_ub += ub
        elif post_start <= c.iso_week <= post_end:
            post_lb += lb
            post_ub += ub
            post_cells += 1
            suppressed += int(c.n is None)
    expected_ub = max(floor * len(sites), baseline_ub / weeks_b) * length
    expected_lb = baseline_lb / weeks_b * length
    logp = None
    if post_lb >= 1:
        value = poisson_logsf(post_lb, expected_ub)
        logp = value if math.isfinite(value) else None
    incomplete = (post_end > closed_through(utc_date(as_of), pack.egress.close_lag_days)
                  or any(coverage.get(s) is None or coverage[s] < post_end for s in sites))
    if incomplete:
        status, reason = "insufficient_data", "post_window_incomplete"
    elif 2 * suppressed > post_cells:
        status, reason = "insufficient_data", "mostly_suppressed"
    elif post_lb >= 1 and poisson_sf(post_lb, expected_ub) < alpha:
        status, reason = "recurred", None
    elif post_ub <= expected_lb:
        status, reason = "not_recurred", None
    else:
        status, reason = "insufficient_data", "inconclusive"
    return {"schema_version": SCHEMA_VERSION, "conclusion_id": conclusion_id, "candidate_key": candidate_key,
            "status": status, "reason": reason,
            "original_window": {"start_week": window[0], "end_week": window[1]},
            "post_window": {"start_week": post_start, "end_week": post_end},
            "baseline_window": {"start_week": base_start, "end_week": base_end}, "sites": sites,
            "post_lb": post_lb, "post_ub": post_ub, "baseline_lb": baseline_lb, "baseline_ub": baseline_ub,
            "expected_lb": expected_lb, "expected_ub": expected_ub, "logp": logp, "alpha": alpha,
            "post_cells": post_cells, "suppressed_cells": suppressed, "as_of": as_of, "label": OUTCOME_LABEL}


def check(reader: "HqReader", pack: "FrozenPack", view: "ConclusionView", *, as_of: str,
          post_start: str | None = None) -> dict[str, Any]:
    """:func:`evaluate` over HQ's cells of the conclusion's key visible at the UTC date of ``as_of``, its
    contributing sites and their coverage."""
    (base_start, _), (_, post_end) = windows(pack, view.window, post_start)
    day = utc_date(as_of)
    cells = reader.key_cells(view.entity_type, view.entity_id, view.predicate, base_start, post_end, day, CHANNELS)
    return evaluate(pack, conclusion_id=view.conclusion_id, candidate_key=view.candidate_key, window=view.window,
                    post_start=post_start, sites=view.contributing_sites, cells=cells,
                    coverage=reader.site_coverage(day), as_of=as_of)
