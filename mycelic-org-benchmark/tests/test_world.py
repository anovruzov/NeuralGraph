"""World generator ground-truth properties (DESIGN.md §3)."""

from __future__ import annotations

import functools
import re

import numpy as np
import pytest

from mycelic_bench.config import load_config
from mycelic_bench.vocab import ATTRIBUTES, CANARY_PREFIX, LABELS, N_ATTR, SENSITIVE_KINDS, cell_index
from mycelic_bench.world import LAYER_NAMES_5, cell_match_mask, dataset_hash, generate_world, ground_truth_summary

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]
# a topology with several departments and regions so that cross-team and global effects exist
TOPOLOGY = ["org.workers_per_team=20", "org.teams_per_department=3", "org.departments_per_region=2", "org.n_regions=0"]
LAYER_RANK = {l: i for i, l in enumerate(LAYER_NAMES_5)}


@functools.lru_cache(maxsize=None)
def world(seed: int = 0):
    cfg = load_config(overrides=SMALL + TOPOLOGY)
    return cfg, generate_world(cfg, seed)


def effect_scope_mask(w, e) -> np.ndarray:
    m = np.ones(w.n, dtype=bool)
    if e.scope_layer in ("team", "department", "region"):
        m &= w.org.membership(e.scope_layer)[w.worker] == e.scope_unit
    return m


def first_layer_meeting(w, e, threshold: float) -> str:
    """Lowest layer at which some unit holds >= threshold unique matching records inside the effect's scope."""
    m = cell_match_mask(w.attrs, e.cell) & effect_scope_mask(w, e) & ~w.is_copy
    for layer in LAYER_NAMES_5:
        counts = np.bincount(w.org.membership(layer)[w.worker[m]], minlength=w.org.n_units(layer))
        if len(counts) and counts.max() >= threshold:
            return layer
    return "none"


def test_topology_and_effect_kinds() -> None:
    cfg, w = world(0)
    assert w.org.n_teams == 15 and w.org.n_departments == 5 and w.org.n_regions == 3
    summary = ground_truth_summary(w)
    kinds = summary["effects_by_kind"]
    assert kinds.get("local", 0) == 20 and kinds.get("cross_team", 0) == 5 and kinds.get("global", 0) == 3
    assert kinds.get("decoy", 0) == 5
    assert summary["dataset_sha256"] == w.dataset_sha256
    assert w.n == 3000 and len(w.effects) > 0


@pytest.mark.parametrize("seed", [0, 1])
def test_min_layer_property(seed: int) -> None:
    """For every local / cross-team / global effect the recorded minimum discovery layer is
    exactly the lowest layer at which some unit has >= n_min * power_margin unique matching
    records within the effect's scope; no unit below it reaches that count."""
    cfg, w = world(seed)
    margin = float(cfg["world"]["power_margin"])
    checked = 0
    for e in w.effects:
        if e.kind not in ("local", "cross_team", "global"):
            continue
        assert e.n_min > 0 and e.min_layer in LAYER_NAMES_5
        threshold = e.n_min * margin
        assert first_layer_meeting(w, e, threshold) == e.min_layer, e.effect_id
        m = cell_match_mask(w.attrs, e.cell) & effect_scope_mask(w, e) & ~w.is_copy
        for layer in LAYER_NAMES_5:
            counts = np.bincount(w.org.membership(layer)[w.worker[m]], minlength=w.org.n_units(layer))
            if LAYER_RANK[layer] < LAYER_RANK[e.min_layer]:
                assert counts.max() < threshold, (e.effect_id, layer)
            # the stored per-unit evidence distribution matches
            assert e.unit_counts[layer] == int(counts.max())
            assert e.contributing_units[layer] == int((counts > 0).sum())
        checked += 1
    assert checked >= 25


def test_effect_kind_constraints() -> None:
    cfg, w = world(0)
    for e in w.effects:
        if e.kind == "local":
            assert e.min_layer in ("worker", "team") and e.scope_layer == "team" and 0 <= e.scope_unit < w.org.n_teams
            assert e.order in cfg["world"]["local_orders"]
        elif e.kind == "cross_team":
            assert e.min_layer == "department" and e.scope_layer == "department"
            assert e.contributing_units["team"] >= 2
        elif e.kind == "global":
            assert e.min_layer in ("department", "region", "executive") and e.scope_layer is None
            assert e.contributing_units["department"] >= 2 and e.contributing_units["team"] >= 3
            assert e.contributing_units["region"] >= 2
            assert e.order in cfg["world"]["global_orders"]
        elif e.kind == "decoy":
            assert e.delta == 0.0
        if e.kind in ("local", "cross_team", "global"):
            lo, hi = cfg["world"]["effect_delta_range"]
            assert lo <= e.delta <= hi
            assert set(e.true_independent_support) == set(LAYER_NAMES_5)


def test_scoped_effects_only_raise_p_true_inside_scope() -> None:
    cfg, w = world(0)
    base = w.base_rates[w.attrs[:, 0], w.attrs[:, 2], :]
    checked = 0
    for e in w.effects:
        if e.kind not in ("local", "cross_team"):
            continue
        cm = cell_match_mask(w.attrs, e.cell)
        inside = cm & effect_scope_mask(w, e)
        outside = cm & ~effect_scope_mask(w, e)
        if inside.sum() == 0 or outside.sum() == 0:
            continue
        lift_in = w.p_true[inside, e.label] - base[inside, e.label]
        lift_out = w.p_true[outside, e.label] - base[outside, e.label]
        assert np.all(lift_in >= np.minimum(e.delta, 0.97 - base[inside, e.label]) - 1e-5)
        # other effects with the same label may overlap a few records; the bulk sees no lift
        assert np.mean(lift_out < e.delta - 1e-5) >= 0.8
        assert lift_in.mean() - lift_out.mean() >= 0.5 * e.delta
        checked += 1
    assert checked >= 10


def test_unscoped_effects_raise_p_true_everywhere_active() -> None:
    cfg, w = world(0)
    base = w.base_rates[w.attrs[:, 0], w.attrs[:, 2], :]
    for e in w.effects:
        if e.kind != "global":
            continue
        m = w.effect_active_mask(e)
        assert m.sum() > 0
        lift = w.p_true[m, e.label] - base[m, e.label]
        assert np.all(lift >= np.minimum(e.delta, 0.97 - base[m, e.label]) - 1e-5)


def test_canaries_unique_and_only_in_sensitive_records() -> None:
    cfg, w = world(0)
    pat = re.compile(rf"^{CANARY_PREFIX}({'|'.join(SENSITIVE_KINDS)})-[0-9a-f]{{8}}$")
    canaries = [c for c in w.canary if c]
    assert len(canaries) == int(w.sensitive.sum()) > 0
    assert len(set(canaries)) == len(canaries)
    for i in range(w.n):
        if w.sensitive[i]:
            assert pat.match(w.canary[i]), w.canary[i]
            assert SENSITIVE_KINDS[w.sensitive_kind[i]] in w.canary[i]
            assert w.canary[i] in w.rationale(i) and w.canary[i] in w.prompt(i)
        else:
            assert w.canary[i] == "" and w.sensitive_kind[i] == -1
            assert CANARY_PREFIX not in w.rationale(i) and CANARY_PREFIX not in w.prompt(i)
    frac = w.sensitive.mean()
    assert abs(frac - cfg["world"]["sensitive_fraction"]) < 0.03


def test_copies_share_fingerprints_with_a_same_team_original() -> None:
    cfg, w = world(0)
    copies = np.flatnonzero(w.is_copy)
    assert len(copies) > 0
    originals = ~w.is_copy
    for i in copies:
        src = np.flatnonzero((w.fingerprint == w.fingerprint[i]) & originals)
        assert len(src) >= 1, i
        j = int(src[0])
        assert w.task_id[i] == w.task_id[j] and w.observed_labels[i] == w.observed_labels[j]
        np.testing.assert_array_equal(w.attrs[i], w.attrs[j])
        assert w.team[i] == w.team[j] and w.origin_worker[i] == w.origin_worker[j] and w.origin_worker[i] != w.worker[i]
    # originals have distinct evidence roots
    fp = w.fingerprint[originals]
    assert len(np.unique(fp)) == len(fp)
    # copies come from duplicator workers
    from mycelic_bench.world import WORKER_TYPE_INDEX
    assert set(w.org.worker_type[w.worker[copies]].tolist()) == {WORKER_TYPE_INDEX["duplicator"]}


def test_dataset_hash_stable_and_seed_dependent() -> None:
    cfg, w0 = world(0)
    again = generate_world(cfg, 0)
    assert again.dataset_sha256 == w0.dataset_sha256 == dataset_hash(again)
    np.testing.assert_array_equal(again.attrs, w0.attrs)
    np.testing.assert_array_equal(again.observed_labels, w0.observed_labels)
    assert [e.effect_id for e in again.effects] == [e.effect_id for e in w0.effects]
    _, w1 = world(1)
    assert w1.dataset_sha256 != w0.dataset_sha256


def test_record_view_hides_truth_and_record_schema() -> None:
    cfg, w = world(0)
    rv = w.records()
    for forbidden in ("effects", "p_true", "true_labels", "canary", "is_copy"):
        assert not hasattr(rv, forbidden)
    assert rv.n == w.n and rv.rationale(0) == w.rationale(0)
    rec = w.record(0)
    for key in ("worker_id", "team_id", "department_id", "region_id", "task_id", "prompt", "model_response",
                "worker_score", "error_labels", "rationale", "confidence", "timestamp", "sensitive", "attributes"):
        assert key in rec
    assert set(rec["attributes"]) == set(ATTRIBUTES)
    assert all(l in LABELS for l in rec["error_labels"])
    assert 0 <= rec["worker_score"] <= 10 and 0 < rec["confidence"] < 1


def test_effect_signatures_unique_outside_groups() -> None:
    cfg, w = world(0)
    seen = {}
    for e in w.effects:
        key = (e.cell, e.label)
        if key in seen:
            assert e.group is not None and seen[key] == e.group, e.effect_id
        else:
            seen[key] = e.group
    ci = cell_index()
    for e in w.effects:
        assert len(ci.decode(e.cell)) == e.order or e.kind in ("decoy",)


def test_release_schedule_drives_model_version() -> None:
    cfg, w = world(0)
    vi = ATTRIBUTES.index("model_version")
    for v, r in w.release_round.items():
        code = int(v[1:]) - 1
        early = w.round < r
        assert not (w.attrs[early, vi] >= code).any() or code == 0
