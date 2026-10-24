# EMERGENCE-Next (Track B): why it plateaus, and what the evidence allows

Date: 2026-10-24. Author: plateau_researcher (analysis only). Status: **interim, seeds 0–4** (seed 5 to be folded in when its report finishes; §2).
Scope: Track B only (EMERGENCE-Next v0 protocol, `EMERGENCE_NEXT_SPEC.md`). Track A (frozen R0) figures appear only
where the Track B SPEC itself uses them (its decoy bar, §2), or to check that this note's method reproduces the R0
ceiling. No Track A figure is set beside a Track B figure as a recall comparison.
Seeds: development seeds 0–5 only. No preliminary, held-out or sealed seed (94000–94009) was generated, read or
used. No e2e run was launched. No model was called.

Path conventions: scripts are in `tools/plateau/` (small outputs in `raw/plateau/`); `$EM` is the run-report directory (`<config>-s<seed>/e2e-ingest-report.json`, outside the repo); `$EMERGENCE_RULE` is a checkout of the code at commit `28275759`; the scripts read `PLATEAU_SCRATCH`, a directory holding `em/` and `plateau/`.

## 0. Answer first

1. **v2 (62.9%) loses its rare patterns before anyone asks a question.** 81 of the 86 rare patterns v2 misses
   (seeds 0–5) had a detector candidate on their key that was **never asked**: the candidate came from the rule layer
   only, and rule-only candidates get no snapshot. **The bottleneck is detection → question (the candidate source).**
2. **v4 fixed that stage and moved the bottleneck to question timing.** v4 asks every rule-layer candidate, **once**,
   at the first rule week where a full window fits. That week is usually too early. Of the 61 rare patterns v4
   misses (seeds 0–4), 31 were asked **before** the first week their evidence could pass the gate. 27 can never pass
   at any week (§3). 2 were asked after the last feasible week, 1 between feasible weeks. **Exactly 1** was asked at
   a feasible week and still not supported. Once the evidence is there, the verifier and gate almost never fail.
3. **v4 is real but small, and it is not 69.4%.** Pooled over seeds 0–4, v4 finds **138/200 = 0.690**. Rare recall
   is **22/83 = 0.265** and common recall 116/117. Per-seed recall runs 0.625–0.825 (sd 0.082). On the same five
   seeds v2 finds 127/200. v4 gains 11 patterns, all asked through rule snapshots, and loses none (paired t = 3.0,
   df 4, p ≈ 0.04). v4 seed 5 had not finished when this was written (§2).
4. **The ceiling for 80% under the frozen invariants is a narrow yes on paper and a no in practice.**
   - Using the harness oracle's own evidence semantics (the pattern's own records, contradicting reports excluded),
     the v4 gate caps `recall_all` at **179/240 = 0.746**, with rare at most 42/103. **80% is impossible.**
   - Using the most permissive model that this system actually behaves like (every record on the key, anchor or
     aux, counted polarity-blind as the lexical judge does), the cap is **205/240 = 0.854**, with rare at most
     68/103. This bound is not falsified by any of the 673 found patterns across five recorded configurations.
   - **32 of the 35 rare patterns that stay undiscoverable even under the permissive model are fragmented**: no site
     ever holds k = 3 records in any 6-week window. Admitting them would need cross-site pooling below k, which
     breaks the k-per-count invariant.
   - Reaching 0.80 on the SPEC gate (20 worlds, two-sided 95% t lower end ≥ 0.80) means turning about **94% of
     every evidence-feasible pattern into a find**. Without the polarity-blind reading it means **96.5%**, and one
     dev world (seed 5) is then capped at 0.775. Today v4 converts 80% of its feasible set, and 39% of the feasible
     rare patterns.
5. **Decoys rule out the SPEC gate today, independently of recall.** v4 accepts **154 decoys** on seeds 0–4. v2
   accepts 147 and v3 (v2 + N2) 130. The brief said v4 *lowered* decoy acceptance. That is **not** what the reports
   show: v4 accepts **more** decoys than v2 and v3 on matched seeds. Types 2 and 5 are already saturated in v4
   (49/50, 50/50), and those two types alone (99) exceed the SPEC bar (the Track A baseline on the same worlds:
   96). Raising rare recall further cannot raise types 2 or 5, which are already at the ceiling. It can raise type 4
   (echo): the timing estimate gives 22 → 48 (§4).

**Recommendation (one line):** do **not** push v4 to a held-out gate run. The structural change rare recall needs
is **provenance-preserving rolling detection**: a detection run per week, each recording its own snapshot week, so a
key is asked again as its evidence matures. Gate it on a **temporal-order and staleness check in the site verifier**
for decoy types 2 and 5. Without that check, Track B fails its own decoy bar whatever its recall.

## 1. Q1: where the missing rare golds die

### Method

Script: `tools/plateau/funnel.py`

Each Gold pattern goes to the first stage it fails. The inputs are the recorded report plus an offline detector
replay (`detect_dump.py` in the same folder). The replay regenerates the dev world with the frozen generator and
runs the shipped pipeline and detector with the exact `--pack-override` read from that report's `run.command`.
Code: `$EMERGENCE_RULE` at `28275759` (read-only). Its v2 pack path is byte-identical to `f830a57c`: the
two later commits are opt-in keys only.

**Replay validation:** for all 11 seeds the replay's asked-candidate count equals the report's
`routing.candidates_asked`: v2 84/88/90/84/86/86 and v4 539/539/494/552/533.

| Stage | Meaning | Source |
|---|---|---|
| S0 not ingested | the run lost records | `ingest.loss`, `total_ingested == total_expected`: **0 lost in every run** |
| S1 no detector candidate | neither the detector nor the rule channel produced the key | replay |
| S2 candidate not asked | key is a candidate but has no snapshot (rule-only), so no question | replay + `discovery.fabric_conclusions` |
| S3 asked outside window | asked, but the snapshot week is outside the pattern's label window, so the scorer labels it background | `fabric_conclusions[].snapshot_week` vs labels |
| S4 verdict | asked in window, status `hypothesis` / `stale` / `contested` | `fabric_conclusions[].status` |
| S5 scoring | supported in window but not counted (`lineage_ok` false) | `discovery.found_patterns` |

### Histogram: rare Gold patterns

| Config (commit) | Seeds | Rare | Found | S0 | S1 | **S2** | S3 | **S4** hyp / stale | S5 |
|---|---|---|---|---|---|---|---|---|---|
| v2 h1n+nd+N1 (`f830a57c`) | 0–5 | 103 | 17 | 0 | 0 | **81** | 1 | 2 / 2 | 0 |
| v4 rs+h1+nd+N1+N2 (`28275759`) | 0–4 | 83 | 22 | 0 | 0 | 0 | **16** | **43** / 2 | 0 |

Common patterns: v2 misses only 3 of 137 (2 at S2, 1 at S3). v4 misses 1 of 117 (S3).

**Dominant stage: v2 is detection → routing (candidate source); v4 is question timing.** The second split below
shows why "verdict" is the wrong reading of v4's S4 row.

### Second split: is the miss an evidence limit, a timing limit, or a system limit?

Script: `cross.py`. Each miss is crossed with the Q3 evidence ceiling (model B, v4 gate, §3): did *any* week in the
label window allow `supported`, and was the week actually asked one of those weeks?

| v4 rare misses, seeds 0–4 (61) | Count |
|---|---|
| Never feasible at any week (structural, §3) | **27** (S3: 4, S4: 23) |
| Feasible at some week; asked **before** the first feasible week | **31** |
| Feasible; asked after the last feasible week / between feasible weeks | 2 / 1 |
| Asked at a feasible week, and the verifier/gate still said `hypothesis` | **1** (s1:p021) |

**Calibration of the evidence predictor.** Script: `sim_policy.py`, step 1. On all 2,657 recorded v4 questions
(seeds 0–4), "model-B v4 gate passes at the asked week" matches the system's `supported` / not on 2,621 (98.6%).
The match is 199/200 on gold keys, 234/238 on decoy keys and 2,188/2,219 on other keys. So S4 in v4 is not a
judging failure. The question was asked when the routed sites did not yet hold the evidence.

v2's 81 S2 misses split the same way: 27 feasible under the oracle model (F), 22 more feasible under the permissive
model (B), and 32 never feasible.

v4 vs v2 on matched seeds 0–4: v4 gains 11 patterns (9 rare) and loses 0. All 11 gains were asked through a rule
snapshot (`snapshot_source = rule`). v4's misses are 57/61 rule-sourced snapshots.

### Three worked examples

Paths are under `$EM/`.

1. **Seed 0, p002 (rare, `entity:E-TESSERA-EDGE:chain_supply`, label window 2024-W06..W14, label sites r01, r06): candidate never
   asked (v2), then asked too early (v4).**
   - v2 (`abl-v2-h1n_nd_n1-s0`): the key is a rule-only candidate (rule weeks W06–W18, W22–W25), with no detector
     week and no snapshot. It was never asked: **S2**.
   - v4 (`abl-v4-rs_nd_n1_n2-s0`): a rule snapshot at **W06**, the first rule week. Status `hypothesis`. The oracle
     row says "no routed site holds the pattern strongly": r06 had 2 records from 1 user.
   - The evidence passes the v4 gate at W10–W11 (model F) or W10–W14 (model B). Both are inside the window and both
     are rule weeks the detector already recorded. A timing miss.
2. **Seed 0, p005 (rare, `entity:E-WISTERIAX:chain_network`, window W10..W21): supported, but at the wrong week.**
   - v4 asked at the detector snapshot **W08**, and the fabric note is `supported`. W08 is before the label window,
     so `candidate_label` scores it as background: **S3**.
   - The evidence passes in-window at W10–W15 (model B). This pattern counts as a v4 *background supported*
     conclusion, not a find.
3. **Seed 0, p004 (rare, `entity:E-TESSERA-PAY:chain_supply`, window W13..W21, label sites r02, r03, r04): structurally undiscoverable.**
   - v4 asked at its rule snapshot W15 and got `hypothesis`. The routed sites held 0, 1 and 1 records.
   - Over every week of the window and every site, the key never holds more than 1 record at any site in a 6-week
     window, even counting aux mentions and background records polarity-blind.
   - No gate that keeps k = 3 per site count can support it.

Also seed 1, p013 (rare, `entity:E-KESTREL:chain_firmware`): asked W06 (rule), `hypothesis`. Feasible under model B at W10–W15, never under model F. A
timing miss whose eventual support would come from non-pattern records on the key.

## 2. Q2: is v4 real?

Script: `q2q4.py`. Reports: `…/scratchpad/em/abl-v4-rs_nd_n1_n2-s{0..4}/e2e-ingest-report.json`. Run ids
`ABLATION-v4-rs_nd_n1_n2-s<seed>-28275759`, code `28275759`, `code_dirty_start/end = false`. Command pack override:
`{"detectors": {"min_history_weeks": 1, "rule_snapshots": true}, "egress": {"near_duplicate_roots": {"jaccard":
0.65, "days": 14}}, "questions": {"pushdown": {"weak_corroborating_sites": 1, "roots_from_k_sites_only": true}}}`,
`--top-n 100000`, `--skip-drills`.

**Seeds used: 0–4.** Seed 5 (`abl-v4-rs_nd_n1_n2-s5`) was still at phase `world`/main pass when this was written.
Its started_at is 2026-10-24T13:02:45Z, and seeds 3 and 4 took 1,556 s and 1,328 s.

| Seed | v4 found | v4 rare | v4 common | v4 decoys | v2 found (same seed) | Δ |
|---|---|---|---|---|---|---|
| 0 | 25/40 | 4/18 | 21/22 | 28 | 22 | +3 |
| 1 | 25/40 | 6/21 | 19/19 | 33 | 24 | +1 |
| 2 | 28/40 | 2/14 | 26/26 | 28 | 28 | 0 |
| 3 | 33/40 | 4/11 | 29/29 | 35 | 30 | +3 |
| 4 | 27/40 | 6/19 | 21/21 | 30 | 23 | +4 |
| **Pooled** | **138/200 = 0.690** | **22/83 = 0.265** | **116/117 = 0.991** | **154/250** | 127/200 = 0.635 | **+11** |

- **Spread:** per-seed recall is 0.625–0.825 (sd 0.082). The 69.4% in the brief was seeds 0–3 (111/160); seed 4
  (0.675) pulls the pooled figure to 0.690.
- **Paired vs v2:** mean +2.2 patterns per world (+0.055), sd 1.64, t = 3.0, df 4, two-sided p ≈ 0.04. The effect
  is real in direction, small, and measured on five worlds.
- **Cost:** about 6× the questions (2,657 vs 432 on seeds 0–4). Supported conclusions labelled background rise from
  16 to 83: unscored positives, including gold keys supported before their window (example 2 above).
- **v2 reference:** `…/em/abl-v2-h1n_nd_n1-s{0..5}`, code `f830a57c`. 151/240 = 0.629, rare 17/103 = 0.165,
  common 134/137, per-seed 0.550–0.750 (sd 0.078). Seeds 1–5 ran with `code_dirty_*` true: the then-uncommitted
  N2 code sat in the tree and was inactive (REPORT §6b).
- **v3 reference:** `…/em/abl-v3-h1n_nd_n1_n2-s{0..5}`, code `b4eeb7bb`. Same 151/240, same found sets.

To fold in seed 5 once its report reads `"phase": "finished"`, run these from the plateau folder in order:
`detect_dump.py <report> det-v4-s5.json work`, then `funnel.py 0,1,2,3,4,5`, `cross.py`, `q2q4.py`,
`decoy_ceiling.py` and `sim_policy.py 0,1,2,3,4,5`. Each command needs
`PYTHONPATH=$EMERGENCE_RULE PYTHONDONTWRITEBYTECODE=1`.

## 3. Q3: the honest ceiling

Script: `tools/plateau/ceiling_b.py`.
Output: `ceiling_b.json`.

### Method

This mirrors `tools/ceiling.py` and `tools/categories2.py` (REPORT §3) and adds the gate's remaining conditions. For
each Gold pattern and each week w inside its label window, it takes the 6-week question window ending at w (the
`question_window` floor; window_weeks 6). Per site it counts records, users (reporters), generator events (roots)
and contradiction, plus the newest week. as_of(w) is the detector's as_of_W, sunday(w) + 20 days.

The pattern is feasible when some week passes the gate under:

- **R0 count:** at least 2 sites with ≥ k records. Used only to check the method; below.
- **v4 gate** (`gate.py` with N1 = 1 and N2 on): at least 1 k-record site, plus either another k-site or one site
  confirming with 1–2 records.
  - Roots count only over k-sites whose own roots reach k (N2), and need roots ≥ 3.
  - Reporters, summed over k-sites (`'<k'` counts 1), need ≥ 3.
  - The newest k-site week must be within `freshness_days` 42.
  - Routing is perfect (best ≤ 3 sites, M = 3), and so are detection, timing and judging. This makes every figure
    an upper bound.

Evidence models:

- **F:** the pattern's own facet records, contradicting (polarity −1) records excluded. These are the harness
  oracle's semantics (`harness.pattern_facts`).
- **K:** every record whose anchor **or** aux is the pattern's anchor and whose predicate maps to its chain,
  positive polarity. This is the REPORT reviewer's permissive model.
- **B:** K counted polarity-blind. Added after the falsification check below.

**Method check.** Model B under R0 count gives **153/240 = 0.6375**, exactly REPORT §3's anchor-or-aux R0 cap. Model
B under the N1 count rule gives 207/240, exactly REPORT §3's R0 + ONE_CORR admit-through (0.863). The method
reproduces the recorded R0 feasibility check.

**Falsification check** (`cross.py`): every pattern found by a recorded run must lie inside the matching ceiling.

- Model **K is not a valid bound.** It misses `clean`/`abl-h1n` s4:p022 and v4 s4:p029. In both, the shipped
  lexical judge counted a site holding a **contradicting** report as a k-record confirmer: r01 held 2 positive
  records and 1 negative, and confirmed with 3.
- Model **B** has no violations across the 673 found patterns of `clean`, `abl-h1n`, `abl-v2-h1n_nd_n1`,
  `abl-v3-h1n_nd_n1_n2` and `abl-v4-rs_nd_n1_n2` (s0–4).
- So **B is this system's ceiling and F is the oracle-semantics ceiling.** The gap between them is support that
  comes from aux mentions, background records on the key, and negated reports.

### Results under the v4 gate (dev seeds 0–5, 240 patterns, 103 rare)

| Evidence model | Cap `recall_all` | Rare cap | Per seed (of 40) |
|---|---|---|---|
| F, own records (oracle semantics) | **179/240 = 0.746** | 42/103 = 0.408 | 30, 29, 31, 33, 28, 28 |
| F, contradicted sites excluded | 170/240 = 0.708 | 38/103 | 29, 28, 31, 30, 25, 27 |
| K, key-level, positive only | 199/240 = 0.829 | 62/103 = 0.602 | 34, 32, 33, 35, 34, **31** |
| **B, key-level, polarity-blind (system bound)** | **205/240 = 0.854** | **68/103 = 0.660** | 34, 33, 34, 36, 36, **32** |

Common patterns are feasible in every model (137/137, or 132–136 with contradicted sites excluded).

### Structurally undiscoverable rare patterns, per seed

These are rare patterns with no feasible week under the v4 gate.

| Seed | 0 | 1 | 2 | 3 | 4 | 5 | Total |
|---|---|---|---|---|---|---|---|
| Rare Gold | 18 | 21 | 14 | 11 | 19 | 20 | 103 |
| Undiscoverable, model B | 6 | 7 | 6 | 4 | 4 | 8 | **35** |
| Undiscoverable, model F | 10 | 11 | 9 | 7 | 12 | 12 | **61** |

Why the 35 fail under B (`structural.py`):

- **32: no site ever reaches k = 3 records in any 6-week window inside the label window.** The evidence is
  fragmented: s0:p003 has 1 + 2 + 2 + 2 records over four sites, and s0:p004 has 1 + 1 + 1.
- **2:** a 3-record site has fewer than 3 reporters.
- **1:** one site holds 3/3/3 and no other site holds anything (ONE_ALONE).

Admitting the 32 needs pooled sub-k counts across sites. That changes the k-per-count rule (a disclosure invariant),
not a setting. They are undiscoverable under the frozen invariants.

### What 70% and 80% require

There are 240 patterns, 137 common and 103 rare.

| Target | Overall needed | Rare needed if common is 134–137 | Feasible? |
|---|---|---|---|
| 70% | 168/240 | 31–34 → rare ≥ 0.30–0.33 | **Yes.** Under F, rare cap 0.408; under B, 0.660. The timing estimate (§4) gives 0.765–0.855 on s0–4. |
| 80% | 192/240 | 55–58 → rare ≥ 0.53–0.56 | **Not under F** (cap 0.746). **Under B only with about 94% conversion** of the feasible set: 192 of 205. **Under K, 96.5%** (192 of 199), with seed 5 capped at 0.775. |

The SPEC gate needs both mean ≥ 0.80 and a 95% t lower end ≥ 0.80 over 20 worlds. The ceiling's own per-world sd is
0.040, so even a system **sitting exactly on the B ceiling** has lower end ≈ 0.835. A system that converts a
fraction c of the feasible set has lower end ≈ 0.854·c − 0.019, which needs **c ≥ 0.96**.

v4 today converts 138 of its 173 B-feasible patterns on s0–4 (80%), and 22 of 56 feasible rare (39%). The rare
patterns it would have to convert are mostly feasible only through one k-site plus a 1–2-record corroborator, or
through non-pattern records on the key. **80% is not a credible target for this benchmark under the frozen
invariants. 70% is.**

## 4. Q4: decoy tension

Scripts: `q2q4.py` (accepted counts from `discovery.decoy_accepted_by_type`) and `decoy_ceiling.py` (the same v4 gate
applied to each decoy's frozen facet-0 key and window, model B).

### Accepted decoys by type, matched seeds 0–4 (50 per type)

| Type | v2 `f830a57c` | v3 `b4eeb7bb` (v2 + N2) | **v4 `28275759`** | v4 B-feasible (evidence-perfect) | v4 headroom |
|---|---|---|---|---|---|
| 1 | 7 | 7 | **11** | 15 | 4 |
| 2 temporal scramble | 49 | 49 | **49** | 50 | 1 |
| 3 near-miss entity | 10 | 10 | **19** | 24 | 5 |
| 4 echo | 41 | 24 | **25** | 48 | 23 |
| 5 stale chain | 40 | 40 | **50** | 50 | 0 |
| **Total** | 147 | 130 | **154** | 187 | 33 |

- Over seeds 0–5: v2 accepts 174/300 and v3 153/300, by type `{1:8, 2:59, 3:12, 4:47, 5:48}` and
  `{1:8, 2:59, 3:12, 4:26, 5:48}`.
- **SPEC bar** (§2: total decoy acceptance no higher than the Track A baseline on the same worlds): the baseline
  `clean` runs (`e094bd82`, seeds 0–4) accept **96** decoys, by type `{1:6, 2:30, 3:3, 4:33, 5:24}`. v4 accepts 154.
- **The rule_snapshots lever alone (v3 → v4) adds 24 decoys:** type 1 +4, type 3 +9, type 4 +1, type 5 +10.
  - Type 5 rises because the rule snapshot asks stale chains at their first, still-fresh week.
  - The claim that v4 lowered decoy acceptance does not hold on matched seeds: 147 (v2) → 154.
- **Does lifting rare recall further pull types 2/4/5 up?**
  - **Types 2 and 5 cannot rise: they are already saturated** (49/50, 50/50). They are 100% evidence-feasible, so
    no count rule separates them (REPORT §3 reached the same conclusion for R0). Only a temporal-order check (type
    2) and a staleness check on the chain (type 5) in the verifier can bring them down. Those two types alone (99)
    exceed the bar (96).
  - **Type 4 (echo) is where the tension is.** It has 23 decoys of evidence-feasible headroom. N2 keeps it at 25
    only because v4 asks once, early.
- **Estimate** (`sim_policy.py`, step 2). This is an offline replay of label-free asking policies over the
  detector's own recorded rule/detector weeks, scored with the frozen label rules. It is an estimate, not a run.

| Policy (s0–4) | Est. found | Est. rare | Est. decoys (1/2/3/4/5) | Other supported | Questions |
|---|---|---|---|---|---|
| P0 v4 as run (one ask, first snapshot) | 139/200 = 0.695 (actual 138) | 23/83 (actual 22) | 152 (11/49/20/22/50) (actual 154) | 174 | 2,657 |
| P3 snapshot + next 2 weeks | 153/200 = 0.765 | 37/83 = 0.446 | 169 (11/49/20/**39**/50) | 470 | 7,608 |
| P2 re-ask every 2nd later week | 169/200 = 0.845 | 52/83 = 0.627 | 184 (14/49/23/**48**/50) | 457 | 11,151 |
| P1 re-ask every later week | 171/200 = 0.855 | 54/83 = 0.651 | 184 (14/49/23/**48**/50) | 861 | 21,071 |

- **Answer:** yes, the lever that lifts rare recall (asking again as evidence matures) **does** pull type 4 up, by
  +17 to +26. Types 1 and 3 rise by a few. Types 2 and 5 stay pinned at the ceiling either way.
- P0 reproduces the recorded run within 1 pattern and 2 decoys.
- Calibration (98.6%) holds at the weeks v4 actually asked. At later weeks it is unverified, so these figures need
  the run proposed in §5.

## 5. Recommendation and the run that would settle it

**Do not send v4 to a held-out gate run.** It would fail the SPEC on decoys (154 vs a bar of 96 on s0–4) whatever
its recall, and its recall (0.690 pooled, five worlds) sits below 0.70.

**The structural change rare recall needs: question timing, with provenance kept.** Asking each key once at its
first rule week throws away 31 of 61 rare misses that become supportable later in their window.

- **What not to do:** an HQ-side re-ask does not work. `hq_bridge.py:269-277` publishes every conclusion under the
  detection run's single recorded snapshot week for the key, so a later re-ask would still be scored at the early
  week. This is consistent with the Track A `persist` arm's +1.
- **What to do:** run detection per week (rolling `as_of`), each run recording its own candidate snapshot week, and
  ask a key again while it stays a detector or rule candidate and its last conclusion is `hypothesis`.
- **What has to come with it:** a verifier temporal-order check (type 2) and a chain-staleness check (type 5).
  Without them Track B cannot meet its own decoy bar. Both are candidate-source and verifier changes (founder-gated
  N per the root-cause report). Neither touches k, the Boundary or the generator, labels and scorer.

**Proposed run (not launched; CPU is shared).** Configuration: `abl-v5-roll_rs_nd_n1_n2`, dev seeds 0–5, real
process mode, `--skip-drills`.

- **Command:** `tools/ablate.py --tag v5-roll_rs_nd_n1_n2-s<seed> --pack-override '<v4 override>' --candidates
  persist+rule -- run --size 200 --seed <seed> --mode process --skip-drills --top-n 100000 --out
  …/em/abl-v5-roll_rs_nd_n1_n2-s<seed>`.
- **Prerequisite:** this needs code first. The bridge must accept per-week detection runs: one `save_run` per
  re-ask week, so each question's `run_id` carries its own snapshot week. Today `persist+rule` is refused by the
  bridge, correctly.
- **Read-out:** `recall_all`, `recall_rare` and `decoy_accepted_by_type` against the §4 estimate (P2: about 0.845 /
  0.63 / 184), and the calibration of predicted vs actual status at the re-ask weeks.
- **Paired with:** the same run plus a type-2 / type-5 verifier check, to see how far decoys fall toward the bar.

## 6. Reproduce

All scripts are in `tools/plateau/`.
Run them with `PYTHONPATH=$EMERGENCE_RULE PYTHONDONTWRITEBYTECODE=1` from that folder (worktree
`28275759`, read-only):

| Script | What it does | Writes |
|---|---|---|
| `detect_dump.py <report> det-<cfg>-s<seed>.json work` | Detector replay, about 7 s per world | the det file |
| `funnel.py 0,1,2,3,4` | Q1 histogram | `funnel_rows.json` |
| `cross.py` | Falsification check and second split | — |
| `examples.py` | Worked examples, gains/losses, timing direction | — |
| `ceiling_b.py` | Q3 caps | `ceiling_b.json` |
| `structural.py` | Why the undiscoverable rare patterns fail | — |
| `q2q4.py` | Per-seed and decoy tables | — |
| `decoy_ceiling.py` | Decoy feasibility | `decoy_ceiling.json` |
| `sim_policy.py 0,1,2,3,4` | Calibration and timing estimates | — |
