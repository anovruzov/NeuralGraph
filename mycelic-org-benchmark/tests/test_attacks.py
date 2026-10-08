"""Attacks (MODULE_SPEC.md Task B): `apply_attacks` tags workers/records and mutates the world in place,
`plan.restore()` puts every array back byte-for-byte, the device-side hook emits the right artefacts per
attack type, and the poison accounting of B6 (no lineage) vs B7 (lineage) is reported on the same
attacked world."""

from __future__ import annotations

import functools

import numpy as np
import pytest

from mycelic_bench import attacks as A
from mycelic_bench.agents import SimulatedSLM, make_batches
from mycelic_bench.config import get_profile, load_config
from mycelic_bench.runner import run_system
from mycelic_bench.schemas import ModelProfile
from mycelic_bench.vocab import N_VALUES, cell_index
from mycelic_bench.world import dataset_hash, generate_world

SMALL = [
    "org.n_workers=300", "org.n_rounds=8", "world.n_local_findings=20", "world.n_cross_team_findings=5",
    "world.n_global_findings=3", "world.n_decoys=5", "world.n_contradictions=2", "world.n_temporal_revisions=2",
]
ARRAYS = ("attrs", "observed_labels", "confidence", "worker", "score", "task_id", "fingerprint", "origin_worker", "is_copy",
          "mention_mask", "attack_tag", "true_labels", "round")


@functools.lru_cache(maxsize=None)
def small_world():
    cfg = load_config(overrides=SMALL)
    return cfg, generate_world(cfg, 0)


def _snapshot(w):
    snap = {k: np.copy(getattr(w, k)) for k in ARRAYS}
    snap["injection_payload"] = list(w.injection_payload)
    snap["worker_attack"] = np.copy(w.org.worker_attack)
    snap["hash"] = dataset_hash(w)
    snap["true_is"] = {e.effect_id: dict(e.true_independent_support) for e in w.effects}
    return snap


def _assert_equal_snapshot(w, snap):
    for k in ARRAYS:
        cur = getattr(w, k)
        assert cur.dtype == snap[k].dtype and cur.shape == snap[k].shape, k
        assert cur.tobytes() == snap[k].tobytes(), f"{k} differs after restore"
    assert list(w.injection_payload) == snap["injection_payload"]
    assert w.org.worker_attack.tobytes() == snap["worker_attack"].tobytes()
    assert dataset_hash(w) == snap["hash"]
    assert {e.effect_id: dict(e.true_independent_support) for e in w.effects} == snap["true_is"]


def _round_batches(w, plan, slm, r: int):
    rec = w.records()
    idx = np.flatnonzero(rec.round == r)
    return make_batches(rec, idx, rec.team, w.org.n_teams, slm, "none", plan.hook if plan is not None else None)


def test_apply_attacks_tags_and_restore_is_byte_exact() -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    assert int((w.attack_tag > 0).sum()) == 0 and int((w.org.worker_attack > 0).sum()) == 0
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.20, type_mix=cfg["attacks"]["type_mix"])
    try:
        n_mal = len(plan.malicious_workers)
        assert n_mal == round(0.20 * w.org.n_workers) == 60
        assert set(np.flatnonzero(w.org.worker_attack > 0).tolist()) == set(int(x) for x in plan.malicious_workers)
        # every record of a malicious worker (by original ownership) is tagged, nothing else
        owner = plan.record_owner
        expected = np.isin(owner, plan.malicious_workers)
        assert np.array_equal(w.attack_tag > 0, expected)
        assert int((w.attack_tag > 0).sum()) > 0
        codes = set(int(c) for c in np.unique(w.attack_tag[w.attack_tag > 0]))
        assert codes <= set(A.ATTACK_CODE.values())
        assert len(codes) >= 5                        # the standard mix spreads over many types
        # something was actually mutated (fabricated records / spoofed ids / payloads)
        assert dataset_hash(w) != before["hash"]
        s = plan.summary()
        assert s["n_malicious"] == n_mal and s["fraction"] == 0.20 and s["active"] is True
        assert sum(s["workers_by_type"].values()) == n_mal
        assert s["n_targets"] >= 1 and s["n_records_fabricated"] > 0 and s["n_records_with_payload"] > 0
        assert s["n_records_spoofed"] > 0
        # determinism + nesting: 10% attackers are a subset of the 20% attackers for the same seed
        plan10 = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix=cfg["attacks"]["type_mix"])
        assert set(plan10.malicious_workers.tolist()) <= set(plan.malicious_workers.tolist())
        assert not plan.active                         # applying a new plan restored the previous one
        plan10.restore()
    finally:
        plan.restore()
    _assert_equal_snapshot(w, before)
    # restore is idempotent
    plan.restore()
    _assert_equal_snapshot(w, before)


def test_fake_signatures_are_not_true_associations() -> None:
    cfg, w = small_world()
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.20, type_mix=cfg["attacks"]["type_mix"])
    try:
        ci = cell_index()
        truth = {(e.cell, e.label) for e in w.effects}
        assert plan.targets and all(t.key not in truth for t in plan.targets)
        for t in plan.targets:
            for e in w.effects:
                if e.label == t.label:
                    assert not A._sub_or_super(ci, t.cell, e.cell), (t.cell_str, e.effect_id)
    finally:
        plan.restore()


@pytest.mark.parametrize("attack", ["false_claim", "fake_consensus", "coordinated", "provenance_spoof",
                                    "duplicate_evidence", "adversarial_format", "confidence_inflation"])
def test_hook_emits_the_right_artifacts(attack: str) -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    profile = get_profile(cfg, cfg["models"]["edge_profile"])
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix={attack: 1.0})
    try:
        code = A.ATTACK_CODE[attack]
        assert set(np.unique(w.attack_tag[w.attack_tag > 0]).tolist()) == {code}
        slm = SimulatedSLM(profile, 1)
        claims, spoofed, malformed, grown, batches_n = [], 0, 0, 0, 0
        for r in range(w.n_rounds):
            plain = _round_batches(w, None, SimulatedSLM(profile, 1), r)
            hooked = _round_batches(w, plan, slm, r)
            for t, b in hooked.items():
                batches_n += 1
                claims.extend(b.injected_claims)
                spoofed += int((~b.signature_ok).sum())
                malformed += int(((b.attrs >= N_VALUES[None, :]).any(axis=1) | ~np.isfinite(b.confidence)).sum())
                grown += len(b) - len(plain[t])
                assert len(b.signature_ok) == len(b) == len(b.is_attack) == len(b.attrs)
        mal = set(int(x) for x in plan.malicious_workers)
        if attack in ("false_claim", "fake_consensus", "coordinated", "confidence_inflation", "provenance_spoof",
                      "adversarial_format"):
            assert claims, attack
            assert all(c["attack"] == attack for c in claims)
            assert all(0 <= c["cell"] < cell_index().total and 0 <= c["label"] < 18 for c in claims)
            assert all(plan.is_target(c["cell"], c["label"]) for c in claims)
        if attack in ("false_claim", "fake_consensus", "coordinated", "confidence_inflation"):
            assert all(c["worker"] in mal for c in claims)
        if attack == "fake_consensus":
            assert len({(c["cell"], c["label"], c["n"], c["k"]) for c in claims}) == 1   # everyone echoes the same claim
        if attack == "confidence_inflation":
            assert all(c["confidence"] == 0.99 and c["n"] < cfg["policy"]["n_min"] for c in claims)
            assert np.all(w.confidence[w.attack_tag > 0] == 0.99)
        if attack == "provenance_spoof":
            assert spoofed == int((w.attack_tag == code).sum()) > 0
            assert all(c["worker"] not in mal and c["signature"].startswith("forged:") for c in claims)
        else:
            assert spoofed == 0
        if attack == "adversarial_format":
            assert malformed == int((w.attack_tag == code).sum()) > 0
            assert all(c["k"] > c["n"] or not np.isfinite(c["confidence"]) for c in claims)
        else:
            assert malformed == 0
        if attack == "duplicate_evidence":
            assert grown > 0 and plan.hook_stats["records_duplicated"] == grown
            # the copies share the fingerprint of an existing record in the batch
            b = next(b for b in _round_batches(w, plan, SimulatedSLM(profile, 1), 0).values() if (b.is_attack == code).any())
            fps = b.fingerprint
            assert len(np.unique(fps)) < len(fps)
        else:
            assert grown == 0
        assert plan.hook_stats["batches_touched"] > 0
    finally:
        plan.restore()
    _assert_equal_snapshot(w, before)


def test_prompt_injection_depends_on_the_model_profile() -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    base = get_profile(cfg, cfg["models"]["edge_profile"])
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix={"prompt_injection": 1.0})
    try:
        code = A.ATTACK_CODE["prompt_injection"]
        payload_idx = np.flatnonzero(w.attack_tag == code)
        assert len(payload_idx) > 0 and all(w.injection_payload[int(i)] for i in payload_idx)
        rec = w.records()
        assert any(A.DEFAULTS["injection_payloads"][0].split(".")[0] in rec.rationale(int(i)) or "SYSTEM OVERRIDE" in rec.rationale(int(i))
                   or "aggregated conclusion" in rec.rationale(int(i)) for i in payload_idx[:20])
        for sus, expect in ((1.0, True), (0.0, False)):
            prof = ModelProfile(**{**base.__dict__, "name": f"p{sus}", "injection_susceptibility": sus})
            plan.reset_hook_stats()
            claims = []
            hijacked = 0
            for r in range(w.n_rounds):
                for b in _round_batches(w, plan, SimulatedSLM(prof, 3), r).values():
                    claims.extend(b.injected_claims)
                    for j in np.flatnonzero(b.is_attack == code):
                        hijacked += int(plan.is_target(cell_index().encode([(a, int(b.attrs[j, a])) for a, _ in
                                                                              cell_index().decode(plan.targets[plan.record_target[b.idx[j]]].cell)]),
                                                       plan.targets[plan.record_target[b.idx[j]]].label))
            assert (len(claims) > 0) is expect, (sus, len(claims))
            assert (plan.hook_stats["records_hijacked"] > 0) is expect
            if expect:
                assert all(c["attack"] == "prompt_injection" for c in claims)
                assert plan.hook_stats["records_hijacked"] == len(claims) == len(payload_idx)
                assert hijacked == len(payload_idx)
    finally:
        plan.restore()
    _assert_equal_snapshot(w, before)


def test_hidden_in_rationale_flips_labels_without_touching_attrs() -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix={"hidden_in_rationale": 1.0})
    try:
        assert plan.n_flipped > 0
        assert w.attrs.tobytes() == before["attrs"].tobytes()
        changed = np.flatnonzero(w.observed_labels != before["observed_labels"])
        # n_flipped counts records whose target bit was switched on; some already carried the label (no change)
        assert 0 < len(changed) <= plan.n_flipped and np.all(w.attack_tag[changed] > 0)
        hidden = [t for t in plan.targets if t.kind == "hidden"]
        assert hidden and sum(t.n_flipped for t in hidden) == plan.n_flipped
        assert all(t.order == 2 and "hidden_in_rationale" in t.attack_types for t in hidden)
    finally:
        plan.restore()
    _assert_equal_snapshot(w, before)


def test_poison_accounting_reported_for_b6_and_b7() -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    # 10% keeps the lineage-blind run short (its accepted-claim count explodes with the malicious fraction)
    plan = A.apply_attacks(w, cfg, seed=0, fraction=0.10, type_mix=cfg["attacks"]["type_mix"])
    try:
        out = {}
        for name in ("B6_hier_no_lineage", "B7_mycelic"):
            res = run_system(w, cfg, 0, name, attack_hook=plan.hook)
            m = res["metrics"]
            p = m["poison"]
            assert p["attack_records"] == int((w.attack_tag > 0).sum()) > 0
            assert isinstance(p["poison_claims_at_root"], int) and p["poison_claims_at_root"] >= 0
            assert 0.0 <= p["poison_promotion_rate"] <= 1.0
            assert p["poison_claims_at_root"] == m["categories"]["poison"]
            assert set(p["poison_by_layer"]) >= {"team", "department", "executive"}
            assert "attack_records_kept_at_team" in p and "claims_quarantined" in p
            out[name] = p
        # the lineage-aware system drops spoofed / replayed records at the team layer; the lineage-blind one keeps all
        assert out["B7_mycelic"]["attack_records_kept_at_team"] < out["B6_hier_no_lineage"]["attack_records_kept_at_team"]
        assert out["B7_mycelic"]["claims_quarantined"] > 0
        # both numbers are reported, never hidden: the comparison itself is what the poisoning experiment measures
        assert {"poison_claims_at_root", "poison_promotion_rate"} <= set(out["B6_hier_no_lineage"])
    finally:
        plan.restore()
    _assert_equal_snapshot(w, before)


def test_independent_support_scenarios_round_trip() -> None:
    cfg, w = small_world()
    before = _snapshot(w)
    scen = A.independent_support_scenarios(w, cfg, seed=0)
    try:
        table = scen.table()
        assert [c["cluster"] for c in table] == list(A.CLUSTER_KINDS)
        by = {c["cluster"]: c for c in table}
        assert by["exact_copies_50"]["n_records"] == 50 and by["exact_copies_50"]["distinct_fingerprints"] == 1
        assert by["exact_copies_50"]["true_independent_support"] == 1.0
        assert by["paraphrases_10"]["distinct_fingerprints"] == 1 and by["paraphrases_10"]["distinct_workers"] == 10
        assert by["copy_chain_5"]["distinct_fingerprints"] > 1 and by["copy_chain_5"]["true_independent_support"] == 1.0
        assert by["independent_3"]["true_independent_support"] > 1.0
        assert all(c["true_independent_support"] >= 0 for c in table)
        rows = scen.score([])
        assert len(rows) == len(table) and all(r["asserted"] is False for r in rows)
    finally:
        scen.restore()
    _assert_equal_snapshot(w, before)
