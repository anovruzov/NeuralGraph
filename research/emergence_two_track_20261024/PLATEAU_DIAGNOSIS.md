# EMERGENCE-Next (Track B): why it plateaus, and what the evidence allows

Date: 2026-10-24. Author: plateau_researcher (analysis only). Status: **final, all six dev seeds** (v4 seed 5 folded
in; the interim version at bfd4fd13 covered seeds 0–4).
Scope: Track B only (EMERGENCE-Next v0 protocol, `EMERGENCE_NEXT_SPEC.md`). Track A (frozen R0) figures appear only
where the Track B SPEC itself uses them (its decoy bar, §2), or to check that this note's method reproduces the R0
ceiling. No Track A figure is set beside a Track B figure as a recall comparison.
Seeds: development seeds 0–5 only. No preliminary, held-out or sealed seed (94000–94009) was generated, read or
used. No e2e run was launched. No model was called.

Path conventions: scripts are in `tools/plateau/` (small outputs in `raw/plateau/`). `$EM` is the run-report
directory (`<config>-s<seed>/e2e-ingest-report.json`, outside the repo). `$EMERGENCE_RULE` is a checkout of the code
at commit `28275759`. The scripts read `PLATEAU_SCRATCH`, a directory holding `em/` and `plateau/`.

## 0. Answer first

1. **v2 (62.9%) loses its rare patterns before anyone asks a question.** 81 of the 86 rare patterns v2 misses
   (seeds 0–5) had a detector candidate on their key that was **never asked**: the candidate came from the rule layer
   only, and rule-only candidates get no snapshot. **The bottleneck is detection → question (the candidate source).**
2. **v4 fixed that stage and moved the bottleneck to question timing.** v4 asks every rule-layer candidate **once**,
   at the first rule week where a full window fits, and that week is usually too early. Of the 76 rare patterns v4
   misses (seeds 0–5):
   - 37 were asked **before** the first week their evidence could pass the gate;
   - 34 can never pass at any week (§3);
   - 2 were asked after the last feasible week, 1 between feasible weeks;
   - **only 2** were asked at a feasible week and still not supported (s1:p021, s5:p004).

   Once the evidence is there, the verifier and gate almost never fail.
3. **v4 is real but small: 0.679, not 69.4%.**
   - Pooled over seeds 0–5, v4 finds **163/240 = 0.679**. Rare recall is **27/103 = 0.262** and common recall
     136/137. Per-seed recall runs 0.625–0.825 (sd 0.078).
   - On the same seeds v2 finds 151/240. v4 gains 12 patterns, all asked through rule snapshots, and loses none
     (paired t = 3.2, df 5, two-sided p ≈ 0.025).
   - The 69.4% in the brief was seeds 0–3. Seeds 4 (0.675) and 5 (0.625) pull it down.
4. **The ceiling for 80% under the frozen invariants is a narrow yes on paper and a no in practice.**
   - **Oracle semantics:** using the harness oracle's own evidence semantics (the pattern's own records,
     contradicting reports excluded), the v4 gate caps `recall_all` at **179/240 = 0.746**, with rare at most
     42/103. **80% is impossible.**
   - **This system's bound:** using the most permissive model that this system actually behaves like (every record
     on the key, anchor or aux, counted polarity-blind as the lexical judge does), the cap is **205/240 = 0.854**,
     with rare at most 68/103. No found pattern falsifies this bound: 698 found patterns across five recorded
     configurations, all inside it.
   - **Fragmented patterns:** 32 of the 35 rare patterns that stay undiscoverable even under the permissive model
     are fragmented. No site ever holds k = 3 records in any 6-week window. Admitting them would need cross-site
     pooling below k, which breaks the k-per-count invariant.
   - **What the SPEC gate would take:** 0.80 on 20 worlds with a two-sided 95% t lower end ≥ 0.80 means turning
     about **94–96% of every evidence-feasible pattern into a find**. Without the polarity-blind reading it means
     **96.5%**, and one dev world (seed 5) is then capped at 0.775. Today v4 converts 79.5% of its feasible set
     (163/205), and 40% of the feasible rare patterns (27/68).
5. **Decoys rule out the SPEC gate today, independently of recall.**
   - **Counts, seeds 0–5:** v4 accepts **181/300 decoys**, v2 174 and v3 (v2 + N2) 153.
   - **The brief's claim fails:** the brief said v4 *lowered* decoy acceptance. The reports show v4 accepts **more**
     decoys than v2 and v3 on every matched seed set.
   - **Types 2 and 5 alone break the bar:** both are saturated in v4 (59/60 and 60/60). Together (119) they exceed
     the SPEC bar, which is the Track A baseline on the same worlds: 118.
   - **Where further rare recall pushes decoys:** it cannot raise types 2 or 5, which are already at the ceiling. It
     does raise type 4 (echo): the timing estimate gives 24 → 46–57 (§4).

**Recommendation (one line):** do **not** push v4 to a held-out gate run. The structural change rare recall needs is
**provenance-preserving rolling detection**: a detection run per week, each recording its own snapshot week, so a key
is asked again as its evidence matures. Pair it with a **temporal-order and staleness check in the site verifier**
for decoy types 2 and 5. Without that check, Track B fails its own decoy bar whatever its recall.

## 1. Q1: where the missing rare golds die

### Method

Script: `tools/plateau/funnel.py`.

Each Gold pattern goes to the first stage it fails. The inputs are the recorded report plus an offline detector
replay (`tools/plateau/detect_dump.py`). The replay regenerates the dev world with the frozen generator and runs the
shipped pipeline and detector with the exact `--pack-override` read from that report's `run.command`. Code:
`$EMERGENCE_RULE` at `28275759` (read-only). Its v2 pack path is byte-identical to `f830a57c`, because the two later
commits add opt-in keys only.

**Replay validation:** for all 12 config-seeds the replay's asked-candidate count equals the report's
`routing.candidates_asked`. v2: 84/88/90/84/86/86. v4: 539/539/494/552/533/524.

| Stage | Meaning | Source |
|---|---|---|
| S0 not ingested | the run lost records | `ingest.loss`, `total_ingested == total_expected`: **0 lost in every run** |
| S1 no detector candidate | neither the detector nor the rule channel produced the key | replay |
| S2 candidate not asked | key is a candidate but has no snapshot (rule-only), so no question | replay + `discovery.fabric_conclusions` |
| S3 asked outside window | asked, but the snapshot week is outside the pattern's label window, so the scorer labels it background | `fabric_conclusions[].snapshot_week` vs labels |
| S4 verdict | asked in window, status `hypothesis` / `stale` / `contested` | `fabric_conclusions[].status` |
| S5 scoring | supported in window but not counted (`lineage_ok` false) | `discovery.found_patterns` |

### Histogram: rare Gold patterns, seeds 0–5

| Config (commit) | Rare | Found | S0 | S1 | **S2** | S3 | **S4** hyp / stale | S5 |
|---|---|---|---|---|---|---|---|---|
| v2 h1n+nd+N1 (`f830a57c`) | 103 | 17 | 0 | 0 | **81** | 1 | 2 / 2 | 0 |
| v4 rs+h1+nd+N1+N2 (`28275759`) | 103 | 27 | 0 | 0 | 0 | **19** | **54** / 3 | 0 |

Per seed, v4 rare (found / S3 / S4): s0 4/4/10, s1 6/3/12, s2 2/0/12, s3 4/2/5, s4 6/7/6, s5 5/3/12.

Common patterns: v2 misses only 3 of 137 (2 at S2, 1 at S3). v4 misses 1 of 137 (at S3).

**Dominant stage: for v2 it is detection → routing (the candidate source); for v4 it is question timing.** The second
split below shows why "verdict" is the wrong reading of v4's S4 column.

### Second split: is the miss an evidence limit, a timing limit, or a system limit?

Scripts: `tools/plateau/cross.py`, `tools/plateau/examples.py`. Each miss is crossed with the Q3 evidence ceiling
(the v4 gate, §3) and asks two questions:

- Did *any* week in the label window allow `supported`?
- Was the week actually asked one of those weeks?

"Feasible" here means feasible under model F or model B. The two are different evidence semantics, and neither
contains the other: 19 patterns have F-feasible weeks that B lacks, from facet records that are not on the key.

| v4 rare misses (76; seeds 0–4 in brackets: 61) | Count |
|---|---|
| Never feasible at any week (structural, §3) | **34** (27) |
| Feasible at some week; asked **before** the first feasible week | **37** (31) |
| Feasible; asked after the last feasible week | 2 (2) |
| Feasible; asked between feasible weeks, not on one | 1 (0) |
| Asked at a feasible week, and the verifier/gate still said `hypothesis` | **2** (1): s1:p021, s5:p004 |

(The interim version split the 61 as 27 / 31 / 2 / 1 / 1, which counts s1:p021 twice. The correct split is above.)

**Calibration of the evidence predictor** (`tools/plateau/sim_policy.py`, step 1). The predictor says "the model-B v4
gate passes at the asked week". Over all 3,181 recorded v4 questions (seeds 0–5) it matches the system's `supported` /
not-supported on 3,138 (98.6%):

- gold keys: 238/240;
- decoy keys: 279/283;
- other keys: 2,621/2,658.

So S4 in v4 is not a judging failure. The question was asked when the routed sites did not yet hold the evidence.

v2's 81 S2 misses split the same way. 27 are feasible under the oracle model (F), 22 more under the permissive model
(B), and 32 are never feasible.

**v4 vs v2 on seeds 0–5:** v4 gains 12 patterns (10 rare) and loses 0. All 12 gains were asked through a rule
snapshot (`snapshot_source = rule`). Of v4's 76 rare misses, 71 come from rule-sourced snapshots.

### Three worked examples

Report paths are `$EM/<config>-s<seed>/e2e-ingest-report.json`.

1. **Seed 0, p002: never asked in v2, asked too early in v4.**
   - Pattern: rare, `entity:E-TESSERA-EDGE:chain_supply`, label window 2024-W06..W14, label sites r01 and r06.
   - **v2** (`abl-v2-h1n_nd_n1-s0`): the key is a rule-only candidate (rule weeks W06–W18 and W22–W25) with no
     detector week and no snapshot. It was never asked: **S2**.
   - **v4** (`abl-v4-rs_nd_n1_n2-s0`): rule snapshot at **W06**, the first rule week. Status `hypothesis`. The
     oracle row says "no routed site holds the pattern strongly": r06 held 2 records from 1 user.
   - **Evidence:** it passes the v4 gate at W10–W11 under model F, or W10–W14 under model B. Both ranges are inside
     the window, and all of those weeks are rule weeks the detector already recorded. This is a timing miss.
2. **Seed 0, p005: supported, but at the wrong week.**
   - Pattern: rare, `entity:E-WISTERIAX:chain_network`, window W10..W21.
   - **v4** asked at the detector snapshot **W08**, and the fabric note is `supported`. W08 is before the label
     window, so `candidate_label` scores the note as background: **S3**.
   - **Evidence:** it passes in-window at W10–W15 under model B. The note is counted as one of v4's *background
     supported* conclusions, not as a find.
3. **Seed 0, p004: structurally undiscoverable.**
   - Pattern: rare, `entity:E-TESSERA-PAY:chain_supply`, window W13..W21, label sites r02, r03 and r04.
   - **v4** asked at its rule snapshot W15 and got `hypothesis`. The routed sites held 0, 1 and 1 records.
   - **Evidence:** across every week of the window and every site, the key never holds more than 1 record at any
     site in a 6-week window. That holds even when aux mentions and background records are counted polarity-blind.
   - No gate that keeps k = 3 per site count can support it.

A fourth case, seed 1, p013 (rare, `entity:E-KESTREL:chain_firmware`): asked at W06 (rule snapshot), result
`hypothesis`. It is feasible under model B at W10–W15 and never under model F. This is a timing miss whose eventual
support would come from non-pattern records on the key.

## 2. Q2: is v4 real?

Script: `tools/plateau/q2q4.py`. Reports: `$EM/abl-v4-rs_nd_n1_n2-s{0..5}/e2e-ingest-report.json`.

- Run ids: `ABLATION-v4-rs_nd_n1_n2-s<seed>-28275759`.
- Code: `28275759`, with `code_dirty_start/end = false` on all six seeds.
- Pack override: `{"detectors": {"min_history_weeks": 1, "rule_snapshots": true}, "egress": {"near_duplicate_roots":
  {"jaccard": 0.65, "days": 14}}, "questions": {"pushdown": {"weak_corroborating_sites": 1, "roots_from_k_sites_only":
  true}}}`.
- Flags: `--top-n 100000`, `--skip-drills`.
- Seed 5 finished at 1,243 s wall time.

**Seeds used: 0–5.**

| Seed | v4 found | v4 rare | v4 common | v4 decoys | v2 found (same seed) | Δ |
|---|---|---|---|---|---|---|
| 0 | 25/40 | 4/18 | 21/22 | 28 | 22 | +3 |
| 1 | 25/40 | 6/21 | 19/19 | 33 | 24 | +1 |
| 2 | 28/40 | 2/14 | 26/26 | 28 | 28 | 0 |
| 3 | 33/40 | 4/11 | 29/29 | 35 | 30 | +3 |
| 4 | 27/40 | 6/19 | 21/21 | 30 | 23 | +4 |
| 5 | 25/40 | 5/20 | 20/20 | 27 | 24 | +1 |
| **Pooled** | **163/240 = 0.679** | **27/103 = 0.262** | **136/137 = 0.993** | **181/300** | 151/240 = 0.629 | **+12** |

- **Spread.** Per-seed recall runs 0.625–0.825 (sd 0.078). The brief's 69.4% was seeds 0–3 only (111/160).
- **Paired against v2.** The mean gain is +2.0 patterns per world (+0.050), sd 1.55, t = 3.2, df 5, two-sided
  p ≈ 0.025. The direction is real and the size is small. v4 never loses a pattern v2 found.
- **Cost.** v4 asks 6.1× the questions (3,181 vs 518). Supported conclusions labelled background rise from 22 to 96.
  These are unscored positives, and they include gold keys supported before their window (example 2 above).
- **v2 reference.** `$EM/abl-v2-h1n_nd_n1-s{0..5}`, code `f830a57c`: 151/240 = 0.629, rare 17/103 = 0.165, common
  134/137, per-seed 0.550–0.750 (sd 0.078). Seeds 1–5 ran with `code_dirty_*` true: the then-uncommitted N2 code sat
  in the tree, inactive (REPORT §6b).
- **v3 reference.** `$EM/abl-v3-h1n_nd_n1_n2-s{0..5}`, code `b4eeb7bb`: the same 151/240, and the same found set on
  every seed.

## 3. Q3: the honest ceiling

Script: `tools/plateau/ceiling_b.py`. Output: `raw/plateau/ceiling_b.json`.

### Method

This mirrors `tools/ceiling.py` and `tools/categories2.py` (REPORT §3) and adds the gate's remaining conditions.

For each Gold pattern and each week w inside its label window, it takes the 6-week question window ending at w (the
`question_window` floor; window_weeks 6). Per site it counts records, users (reporters), generator events (roots),
contradiction and the newest week. as_of(w) is the detector's as_of_W, sunday(w) + 20 days.

A pattern is feasible when some week passes the gate under one of two rules:

- **R0 count:** at least 2 sites with ≥ k records. Used only to check the method (below).
- **v4 gate** (`gate.py` with N1 = 1 and N2 on):
  - at least 1 k-record site, plus either another k-site or one site confirming with 1–2 records;
  - roots, counted only over k-sites whose own roots reach k (N2), must be ≥ 3;
  - reporters, summed over k-sites (`'<k'` counts as 1), must be ≥ 3;
  - the newest k-site week must be within `freshness_days` (42).

The computation assumes perfect routing (best ≤ 3 sites, M = 3), detection, timing and judging. Every figure is
therefore an upper bound.

Evidence models:

- **F**: the pattern's own facet records, with contradicting (polarity −1) records excluded. These are the harness
  oracle's semantics (`harness.pattern_facts`).
- **K**: every record whose anchor **or** aux is the pattern's anchor and whose predicate maps to its chain, positive
  polarity only. This is the REPORT reviewer's permissive model.
- **B**: K counted polarity-blind. Added after the falsification check below.

**Method check.** Model B under R0 count gives **153/240 = 0.6375**, exactly REPORT §3's anchor-or-aux R0 cap. Model
B under the N1 count rule gives 207/240, exactly REPORT §3's R0 + ONE_CORR admit-through (0.863). The method
reproduces the recorded R0 feasibility check.

**Falsification check** (`tools/plateau/cross.py`). Every pattern a recorded run found must lie inside the matching
ceiling.

- **Model K is not a valid bound.** It misses s4:p022 (`clean` and `abl-h1n`) and s4:p029 (v4). In both, the
  shipped lexical judge counted a site holding a **contradicting** report as a k-record confirmer: r01 held 2
  positive records and 1 negative, and confirmed with 3.
- **Model B has no violations.** Checked against all 698 found patterns of `clean`, `abl-h1n`, `abl-v2-h1n_nd_n1`,
  `abl-v3-h1n_nd_n1_n2` and `abl-v4-rs_nd_n1_n2`, seeds 0–5.
- **So B is this system's ceiling and F is the oracle-semantics ceiling.** The gap between them is support that
  comes from aux mentions, background records on the key and negated reports.

### Results under the v4 gate (dev seeds 0–5, 240 patterns, 103 rare)

| Evidence model | Cap on `recall_all` | Rare cap | Per seed (of 40) |
|---|---|---|---|
| F, own records (oracle semantics) | **179/240 = 0.746** | 42/103 = 0.408 | 30, 29, 31, 33, 28, 28 |
| F, contradicted sites excluded | 170/240 = 0.708 | 38/103 | 29, 28, 31, 30, 25, 27 |
| K, key-level, positive only | 199/240 = 0.829 | 62/103 = 0.602 | 34, 32, 33, 35, 34, **31** |
| **B, key-level, polarity-blind (system bound)** | **205/240 = 0.854** | **68/103 = 0.660** | 34, 33, 34, 36, 36, **32** |

Common patterns are feasible in every model: 137/137, or 132–136 with contradicted sites excluded.

### Structurally undiscoverable rare patterns, per seed

Rare patterns with no feasible week under the v4 gate:

| Seed | 0 | 1 | 2 | 3 | 4 | 5 | Total |
|---|---|---|---|---|---|---|---|
| Rare Gold | 18 | 21 | 14 | 11 | 19 | 20 | 103 |
| Undiscoverable, model B | 6 | 7 | 6 | 4 | 4 | 8 | **35** |
| Undiscoverable, model F | 10 | 11 | 9 | 7 | 12 | 12 | **61** |
| Undiscoverable under both F and B | 6 | 7 | 6 | 4 | 4 | 7 | **34** |

The last row differs from model B only at seed 5. There, s5:p006 is F-feasible from facet records that are not on its
key.

Why the 35 fail under B (`tools/plateau/structural.py`):

- **32: no site ever reaches k = 3 records in any 6-week window inside the label window.** The evidence is
  fragmented. s0:p003 has 1 + 2 + 2 + 2 records over four sites. s0:p004 has 1 + 1 + 1.
- **2:** a 3-record site has fewer than 3 reporters.
- **1:** one site holds 3 records from 3 reporters and 3 roots, and no other site holds anything (ONE_ALONE).

Admitting the 32 needs pooled sub-k counts across sites. That changes the k-per-count rule, which is a disclosure
invariant, not a setting. Under the frozen invariants these 32 are undiscoverable.

### What 70% and 80% require

There are 240 patterns: 137 common and 103 rare.

| Target | Overall needed | Rare needed (common at 134–137) | Feasible? |
|---|---|---|---|
| 70% | 168/240 | 31–34, i.e. rare ≥ 0.30–0.33 | **Yes.** The rare cap is 0.408 under F and 0.660 under B. The timing estimate (§4) gives 0.758–0.846. |
| 80% | 192/240 | 55–58, i.e. rare ≥ 0.53–0.56 | **Not under F** (cap 0.746). **Under B only with about 94% conversion** of the feasible set (192 of 205). **Under K, 96.5%** (192 of 199), with seed 5 capped at 0.775. |

**The SPEC gate needs more than a mean of 0.80.** It needs mean ≥ 0.80 and a 95% t lower end ≥ 0.80 over 20 worlds.

- The ceiling's own per-world sd is 0.040.
- Even a system **sitting exactly on the B ceiling** would have a lower end of about 0.835.
- A system converting a fraction c of the feasible set has a lower end of about 0.854·c − 0.019. That needs
  **c ≥ 0.96**.

**v4 is far from that.** It converts 163 of 205 B-feasible patterns (79.5%) and 27 of 68 feasible rare patterns
(40%). Most of the rare patterns still missing are feasible only through one k-site plus a 1–2-record corroborator,
or through non-pattern records on the key. **Under the frozen invariants, 80% is not a credible target for this
benchmark. 70% is.**

## 4. Q4: decoy tension

Scripts: `tools/plateau/q2q4.py` reads accepted counts from `discovery.decoy_accepted_by_type`.
`tools/plateau/decoy_ceiling.py` applies the same v4 gate (model B) to each decoy's frozen facet-0 key and window.

### Accepted decoys by type, seeds 0–5 (60 per type)

| Type | v2 `f830a57c` | v3 `b4eeb7bb` (v2 + N2) | **v4 `28275759`** | B-feasible (evidence-perfect) | v4 headroom |
|---|---|---|---|---|---|
| 1 | 8 | 8 | **12** | 16 | 4 |
| 2 temporal scramble | 59 | 59 | **59** | 60 | 1 |
| 3 near-miss entity | 12 | 12 | **23** | 29 | 6 |
| 4 echo | 47 | 26 | **27** | 57 | 30 |
| 5 stale chain | 48 | 48 | **60** | 60 | 0 |
| **Total** | 174 | 153 | **181** | 222 | 41 |

On seeds 0–4 alone the same picture holds: v2 147, v3 130, v4 154. The interim note used these figures.

- **The SPEC bar** (§2): total decoy acceptance may be no higher than the Track A baseline on the same worlds. The
  baseline `clean` runs (`e094bd82`, seeds 0–5) accept **118** decoys, by type `{1:6, 2:37, 3:4, 4:41, 5:30}`. v4
  accepts 181.
- **The rule_snapshots lever alone (v3 → v4) adds 28 decoys:** type 1 +4, type 3 +11, type 4 +1, type 5 +12.
  - Type 5 rises because the rule snapshot asks stale chains at their first week, while they are still fresh.
  - The claim that v4 lowered decoy acceptance does not hold. On matched seeds v2 accepts 174 and v4 181.
- **Does lifting rare recall further pull types 2, 4 and 5 up?**
  - **Types 2 and 5 cannot rise: they are already saturated** (59/60 and 60/60). Their evidence is 100% feasible,
    so no count rule separates them. REPORT §3 reached the same conclusion for R0. Only a temporal-order check (type
    2) and a staleness check on the chain (type 5) in the verifier can bring them down. Together these two types
    (119) already exceed the bar (118).
  - **Type 4 (echo) is where the tension is.** It has 30 decoys of evidence-feasible headroom. N2 holds it at 27
    only because v4 asks each key once, early.
- **Estimate** (`tools/plateau/sim_policy.py`, step 2). An offline replay of label-free asking policies over the
  weeks the detector itself recorded (rule and detector weeks), scored with the frozen label rules. This is an
  estimate, not a run.

| Policy (seeds 0–5) | Est. found | Est. rare | Est. decoys (1/2/3/4/5) | Other supported | Questions |
|---|---|---|---|---|---|
| P0 v4 as run (one ask, first snapshot) | 165/240 = 0.688 (actual 163) | 29/103 (actual 27) | 179 (12/59/24/24/60) (actual 181) | 199 | 3,181 |
| P3 snapshot + next 2 weeks | 182/240 = 0.758 | 46/103 = 0.447 | 201 (12/59/24/**46**/60) | 543 | 9,100 |
| P2 re-ask every 2nd later week | 199/240 = 0.829 | 62/103 = 0.602 | 217 (15/59/27/**56**/60) | 535 | 13,361 |
| P1 re-ask every later week | 203/240 = 0.846 | 66/103 = 0.641 | 218 (15/59/27/**57**/60) | 1,010 | 25,234 |

- **Answer:** yes. The lever that lifts rare recall, asking again as the evidence matures, **does** pull type 4 up,
  by +22 to +33. Types 1 and 3 rise by a few. Types 2 and 5 stay pinned at the ceiling either way.
- **Fidelity:** P0 reproduces the recorded run to within 2 patterns and 2 decoys.
- **Limit:** the 98.6% calibration was measured at the weeks v4 actually asked. At later weeks it is unverified, so
  these figures need the run proposed in §5.

## 5. Recommendation and the run that would settle it

**Do not send v4 to a held-out gate run.** It would fail the SPEC on decoys whatever its recall (181 against a bar of
118), and its recall (0.679 pooled over six worlds) is below 0.70.

**What rare recall needs is a structural change to question timing, with provenance kept.** Asking each key once at
its first rule week loses 37 of the 76 rare misses, which become supportable later in their window.

- **What not to do:** an HQ-side re-ask does not work. `hq_bridge.py:269-277` publishes every conclusion under the
  single snapshot week the detection run recorded for the key, so a later re-ask is still scored at the early week.
  This is consistent with the Track A `persist` arm's +1.
- **What to do:** run detection per week (rolling `as_of`), with each run recording its own candidate snapshot
  week. Ask a key again while it stays a detector or rule candidate and its last conclusion is `hypothesis`.
- **What must come with it:** a verifier temporal-order check (type 2) and a chain-staleness check (type 5). Without
  them Track B cannot meet its own decoy bar.
- **Constraints:** both are candidate-source and verifier changes, founder-gated (N) per the root-cause report.
  Neither touches k, the Boundary, or the generator, labels and scorer.

**Proposed run (not launched; CPU is shared).**

- **Configuration:** `abl-v5-roll_rs_nd_n1_n2`, dev seeds 0–5, real process mode, `--skip-drills`.
- **Command:** `tools/ablate.py --tag v5-roll_rs_nd_n1_n2-s<seed> --pack-override '<v4 override>' --candidates
  persist+rule -- run --size 200 --seed <seed> --mode process --skip-drills --top-n 100000 --out
  $EM/abl-v5-roll_rs_nd_n1_n2-s<seed>`.
- **Prerequisite (code first):** the bridge must accept per-week detection runs, i.e. one `save_run` per re-ask
  week, so that each question's `run_id` carries its own snapshot week. Today the bridge refuses `persist+rule`, as
  it should.
- **Read-out:** `recall_all`, `recall_rare` and `decoy_accepted_by_type` against the §4 estimate (P2: about 0.83 /
  0.60 / 217), plus the calibration of predicted against actual status at the re-ask weeks.
- **Paired run:** the same configuration plus a type-2 / type-5 verifier check, to see how far decoys fall toward the
  bar.

## 6. Reproduce

All scripts are in `tools/plateau/`. Set `PLATEAU_SCRATCH` (a directory holding `em/` and `plateau/`) and run with
`PYTHONPATH=$EMERGENCE_RULE PYTHONDONTWRITEBYTECODE=1`. `$EMERGENCE_RULE` is at `28275759` and is only read.

| Script | What it does | Writes |
|---|---|---|
| `detect_dump.py <report> det-<cfg>-s<seed>.json work` | Detector replay, about 7 s per world | the det file |
| `funnel.py 0,1,2,3,4,5` | Q1 histogram | `funnel_rows.json` (`raw/plateau/`) |
| `cross.py` | Falsification check and second split | — |
| `examples.py` | Gains and losses, timing direction (F ∪ B), snapshot sources | — |
| `ceiling_b.py` | Q3 caps | `ceiling_b.json` (`raw/plateau/`) |
| `structural.py` | Why the undiscoverable rare patterns fail | — |
| `q2q4.py` | Per-seed and decoy tables | — |
| `decoy_ceiling.py` | Decoy feasibility | `decoy_ceiling.json` (`raw/plateau/`) |
| `sim_policy.py 0,1,2,3,4,5` | Calibration and timing estimates | — |
