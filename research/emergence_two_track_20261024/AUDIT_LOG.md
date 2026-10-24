# Audit log

One entry per audit tick. Each item is PASS / FAIL / N/A with the evidence that decided it. A FAIL stops merging
into the shared branch until a later tick passes it; work continues in worktrees.

Checklist (from the two-session coordination agreement):
1. reproducibility · 2. traceability · 3. labelling · 4. invariants · 5. cost columns · 6. budget matching

---

## 2026-10-24 — tick 1 (first entry, written by the orchestrator before the standing auditor's first run)

Audited sha: `ng-recovery` at `3200c10` (research branch) and `ng-deploy` at `ce0e357` (deployment-continuation,
draft PR #17). Scope: everything this session produced today.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS (partial)** | The Track A ceiling re-derives on this checkout: `tools/ceiling.py 0..5` → 0.6125 (6-week) / 0.65 (26-week); `tools/categories2.py 0..5` → R0 153/240 = 0.6375 and 179/240 = 0.746, matching REPORT §3 row for row. `raw/plateau/ceiling_b.json` recomputed independently: F·TB_v4 179/240, B·TB_v4 205/240, K·TB_v4 199/240, 35/61/34 undiscoverable — all matching PLATEAU_DIAGNOSIS §3. **Not audited:** no v2/v4 run report is present in this container (`$EM` is the other session's scratch), so no config was replayed from its recorded command. That replay is owed. |
| 2 | Traceability | **PASS for this session's numbers** | Official baseline: `runs/official-ce0e357/seed-{0..5}`, run ids `OFFICIAL-s<seed>-ce0e357`, code `ce0e357`, no pack override, drills on. Seed 0 report sha256 `87829c640b3216f346cb9a2abf426bcd7c7009393a9249f9657619b89e3eec9d`. Broker `nats-server v2.10.29`, release sha256 `e314da74a83a1d3876db8814c27eb990fe729640fd6452025b441bcede8390da`. |
| 3 | Labelling | **PASS** | Nothing from this session is called a baseline except the official runs. 0.629 / 0.679 are named diagnostic ablations (pack override + `--skip-drills`) wherever they appear in CONTINUATION.md and in chat. Track A and Track B figures are not set side by side as recall comparisons. |
| 4 | Invariants | **PASS** | Seeds 94000–94009: absent from every command, log and report in this container (`grep -r 9400[0-9]` over `runs/`, `tools/`, the worktrees → no hits outside the SPEC's own prose). Goldens/labels/scorer: `git diff e094bd8..ce0e357 -- research/mycelic/corpus.py research/mycelic/org.py` is empty; `tests/golden/*` unchanged. k, the Boundary and the generator untouched. Pin moves each have their own commit and reason: `ce0e357` (DETECT_SHA256, with the commit that invalidated it named), `fac8d0c` (SPIKE_CODE_HASH `6c9f5856` → `bf315f95`, naming `pushdown/gate.py`). |
| 5 | Cost columns | **FAIL** | The official baseline rows report found / recall / decoys-by-type / checks, but not background-supported conclusions or questions per run. Both are present in the reports (`routing.questions_asked`, `discovery` background counts) and simply not surfaced. Owed before any lever result is claimed. |
| 6 | Budget matching | **N/A this tick** | No targeted lever has produced a result yet. The untargeted budget-matched control arm is specified and not yet run. |

**Result: 1 FAIL (item 5).** It blocks claiming any lever result, not the baseline itself: the baseline makes no
comparative claim. Fix is mechanical — add the two columns to the result table and to the run summary.

### Corrections this tick records against earlier statements by this session

- **"No run could have had `checks_pass` true" was too broad.** What was red is
  `tests/mycelic/test_collective_pushdown.py::PushdownImportGuardTests::test_detection_is_byte_identical_to_its_pin`
  (`:101`, `DETECT_SHA256["detectors.py"]`), not `c7`. `c7` checks `report.SPIKE_CODE_HASH`, which was re-pinned
  deliberately; the two pins are independent. An ablation run with `--skip-drills` and no `--suites` never
  evaluates `c10_suites_green`, so that red test did not gate those runs.
- **"Type-2 decoys are separable at the verifier" was premature.** The evidence is locally visible, but the naive
  order check does not fire at the week the system asks. Measured below; the claim is withdrawn in that form.


## 2026-10-24 — tick 2 (orchestrator; the official baseline landed)

Audited sha: `ng-recovery` at `fd787b7` + this commit. Change since tick 1: the six-seed official baseline
completed and is preserved under `raw/official_ce0e357/`.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS for the baseline** | Six seeds, one command each, unmodified CLI, drills on. Per-seed found 13, 12, 17, 17, 19, 15 reproduces the recorded Track A figures exactly, and the pooled decoy total is 118 — the same 118 the SPEC uses as its bar. Report sha256 per seed in `raw/official_ce0e357/BASELINE_SEEDS_0-5.csv`. Still owed: a replay of one *v4* config, which needs a report this container does not have. |
| 2 | Traceability | **PASS** | Every row carries run id `OFFICIAL-s<seed>-ce0e357`, code `ce0e357`, pack override `null`, seed, and the report's sha256. Logs preserved beside them. |
| 3 | Labelling | **PASS** | This is the official path: no guard bypass, no `--skip-drills`, no pack override. It is the only thing in this folder entitled to be called a baseline. |
| 4 | Invariants | **PASS** | Unchanged from tick 1; no generator, golden, label or scorer file touched by this commit. |
| 5 | Cost columns | **PASS (now)** | `BASELINE_SEEDS_0-5.csv` carries decoys by type, `questions_asked`, `background_supported` and `supported_conclusions` next to recall. Pooled: 341 questions, 3 background-supported. The FAIL from tick 1 is cleared for the baseline rows; any lever result still has to carry the same columns. |
| 6 | Budget matching | **N/A** | No lever result yet. |

**Result: no FAIL.** Merging unblocked.

**The number:** official, dev seeds 0–5, code `ce0e357`, drills on, every check green on every seed —
**93/240 = 0.3875 recall, 118/300 decoys accepted, 341 questions, 3 background-supported conclusions.**
All six seeds are official; previously only seed 0 could be, which is what the seed-guard repair changed.


## 2026-10-24 — tick 3 (orchestrator; engineer result folded in)

Change since tick 2: an engineer's freshness investigation (no code change, no commit in the shared branch) and
§9 of CONTINUATION.md.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS** | The engineer's class-5 ceiling of 30/60 for the shipped source is confirmed independently by our official baseline: `BASELINE_SEEDS_0-5.csv` type-5 column sums to exactly 30. Its gate/oracle parity sweep is a scratch script, not a committed test — see the FAIL below. |
| 2 | Traceability | **PASS** | Every claim in §9 carries file:line or a report path; the two figures quoted from `EMERGENCE_ROOT_CAUSE_REPORT.md` are marked as quoted, not re-run. |
| 3 | Labelling | **PASS** | The engineer's measurement is labelled a proxy (world + pipeline + detection, no site verifiers, no judge, no fabric), not an e2e run. |
| 4 | Invariants | **PASS** | No change committed; no pinned file touched, so `SPIKE_CODE_HASH` is unaffected. |
| 5 | Cost columns | **PASS** | Unchanged from tick 2. |
| 6 | Budget matching | **N/A** | No lever result yet. |

**Result: no FAIL, one item owed.** The gate/oracle freshness parity has no regression guard: the oracle pins 42
as a literal and the gate reads it from the pack, and nothing asserts they agree. Small test, worth adding before
either session changes a freshness value.

**2026-10-24 14:21 — tick 5.** Change detected since `d85c7f9`: `837a887`, `9164290`, `ebeb031` (the other
session: shared research context, evidence ceiling by world size, count-gate specificity by world size).
Fast-forwarded locally. **Audit deferred, not skipped** — the tick-4 auditor is still running on
`069df7e`/`132c0ff`/`5eeb945`/`d85c7f9`, and two concurrent auditors would duplicate each other. These three shas
are queued for the next auditor, which must look hardest at the specificity claim (non-planted keys passing the
count gate: 9.6% at 200 users, ~70% at 2K and 10K). If that holds, the benchmark's discrimination degrades with
world size, which bears on every scaling plan in the folder and on whether a 2K or 10K result means what a 200-user
result means. No merge into the shared branch is blocked by this tick.


## 2026-10-24 — tick 4 (independent auditor, read-only)

Audited: `ng-recovery` at `d85c7f9` (merge of `132c0ff` and the Gate-0 session's `5eeb945`); `ng-deploy` at
`ce0e357`, clean.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS (baseline, ceiling) / CANNOT VERIFY (v4 replay)** | All six baseline reports re-read: sha256 match `BASELINE_SEEDS_0-5.csv`; found 13/12/17/17/19/15 = 93/240; decoys 118 by type {1:6, 2:37, 3:4, 4:41, 5:30}; 341 questions; 3 background-supported; `checks_pass` true ×6; commands carry no override and no `--skip-drills`. Ceiling recomputed from `ceiling_b.json` cell for cell (F 179, Fs 170, K 199, B 205; undiscoverable 61/41/35). The v4 replay cannot be done here: commit `28275759` is not an object in this repo (only a patch `From` line), and report sha `4ef21383…` exists nowhere in this container. |
| 2 | Traceability | **FAIL** | `raw/plateau/v5b_offline_seeds0-5.txt` is a truncated stdout capture (line 1 is the tail of the calibration print at `v5b_offline.py:128-130`; the `agree` dict and the six `seed N done` lines are missing; line 9 is a paste artifact). No ESTIMATE label in the file, no inputs, no commit for either tree, no pack override, no seed set beyond the filename. **And the figure is unreconciled:** `sim_policy.py:55` (key-level) gives background-supported 199/535 where `v5b_offline.py:120` (label-level) gives 61/233. REPORT.md:134-135 uses 61 to call the model "optimistic by about a third"; with 199 it is pessimistic by 2×. Nothing states which matches `discovery.background_supported`. Baseline traceability passes. |
| 3 | Labelling | **PASS (new text), one carried observation** | New text labels diagnostics correctly. Carried: REPORT.md:9-10 and :125 set Track B recall beside the Track A baseline, against REPORT.md:32-33's own rule; §5's heading (:113) says "Track A ablations" over five Track B rows; REPORT.md:47-48 and :67 ("seed 0 only", "seeds 1–5 can only run as diagnostics") are stale after patches 0014/0015 and `069df7e`. |
| 4 | Invariants | **PASS** | The four new commits touch only the research folder. Generator, labels and scorer unchanged in `ng-deploy` (`corpus.py`, `org.py`, `world.py` diffs empty; `discovery()`, `expected_status`, the decoy rule untouched). Seeds 94000–94009 appear in no command, log or report — every grep hit is a substring of a longer number or prose saying they are sealed. Every pin move has its own commit with a reason. |
| 5 | Cost columns | **PASS (partial)** | Baseline CSV carries the full set and the per-row type sums equal the totals. REPORT §5 now carries background-supported and questions per world (tick-1 FAIL cleared), but rows still show a decoy **total** with selected class deltas; by-type data exists in `raw/e2e_ledger.jsonl` and is surfacing work. |
| 6 | Budget matching | **N/A** | No lever result yet. |

**Stale-pin check:** `ng-deploy` at `ce0e357` is **green** — `test_detection_is_byte_identical_to_its_pin` passes,
`DETECT_SHA256["detectors.py"]` equals the file. Two notes: `ce0e357`'s own message says "1 failed → 81 passed"
while 80 tests collect today — a count this session should explain rather than defend. And, more seriously:
**the tree v4's 163/240 was produced on had the stale pin.** At `d88c150` (≡ `28275759`) the pin reads
`9d23000e` while `detectors.py` hashes `d113c269`. Under this project's own rule — no result claimed from a
stale-pin tree — the current-best v4 figure is claimed from a tree that violated it, and
`raw/v4_s0_replay_reference.json` records `checks_pass: true` without disclosing it. The runs used
`--skip-drills` with no `--suites`, so `c10_suites_green` never evaluated and nothing caught it.

**Result: 1 FAIL (item 2).** The official six-seed baseline passes every item and is unaffected. The FAIL blocks
the v5b estimate and the §5 column, not the baseline.

### Owed (auditor's list, owners assigned by this session)

1. **Gate-0 session:** rewrite `v5b_offline_seeds0-5.txt` with a header — ESTIMATE label, both tree commits, seed
   set, pack override, complete stdout.
2. **Gate-0 session:** reconcile the two background-supported definitions and correct whichever document is wrong.
3. **Gate-0 session:** commit a v4 ledger with the six report sha256s; today `4ef21383` appears only in the
   reference JSON and seeds 1–5 have no digest at all.
4. **Both:** decide whether "no result from a stale-pin tree" has a DIAGNOSTIC exception, or whether v4 must be
   re-run on `ce0e357`+ before it is called current best. This is a governance decision, not a fix.
5. **Gate-0 session:** REPORT.md:47-48, :67 are stale; :9-10 and :125 compare tracks; §5's heading is wrong.
6. **Gate-0 session:** per-row decoys by type in §5.
7. **Either, when the CPU is free:** the v4 replay on `ce0e357`+, which is an e2e run and outside the auditor's remit.
8. **This session:** the freshness-parity guard now has a third literal to cover — `v5b_offline.py:23`
   `K, FRESH = 3, 42`, beside `harness.py:105` and the pack's `freshness_days`.

---

## 2026-10-24 — tick 6 (audited by the orchestrator directly; no agent spent, machine busy with the v4 replay)

Audited shas: `ng-recovery` at `97fe49f` (fast-forwarded over `545dd12`: `837a887`, `9164290`, `ebeb031`,
`66d4b80`, `97fe49f`), `ng-deploy` at `ce0e357`, `ng-integrity` at `research/report-integrity`.
Scope: the frontier scale backlog queued at tick 5, plus this session's own new work.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS, with two disclosures owed** | The specificity-by-world-size claim reproduces from the committed artifact: `raw/plateau/specificity_by_world_size.txt` gives non-planted chain keys ever passing = K 173/1800 = **0.096** at 200, 1258/1800 = **0.699** at 2,000, 1249/1800 = **0.694** at 10,000. The method in `tools/plateau/specificity_scale.py` reuses `ceiling_b.gates`/`site_stats`, so it is the same gate model the ceilings use. See the two disclosures below. |
| 2 | Traceability | **FAIL (new, mine)** | `tests/mycelic` is **red at `ce0e357`**: `test_collective_x3.py::EvidenceTests::test_the_code_hashes_are_the_pinned_ones`, 1 failed / 1734 passed / 45617 subtests, from the v4 replay's own suites step (`runs/v4repro-ce0e357/suites/tests-mycelic.log`). All five digests in `LIVE_CODE_HASHES_R002` have moved. Cause is mechanical and this branch's own doing: `EVAL_CODE_FILES` globs `mycelic/collective/{detect,edge,packs,evaluate}/*.py`, and E2 / openfda_replay / X5 add `pushdown/*.py`; those are exactly the files changed for `rule_snapshots`, N1 and N2. `SPIKE_CODE_HASH` was re-pinned three times across those changes (`b7b1a18`, `8981505`, `d88c150`); this overlapping pin was re-pinned zero times. **Nothing has been re-pinned** — rule 2 wants a diff, a reason and a deliberate commit. |
| 3 | Labelling | **PASS** | `EVIDENCE_CEILING.md` names 74.6% as the defensible figure for this system and 85.4% as a reviewer-constructed permissive model, in the same table, and says so in §6. The ceiling table is headed "upper bounds by construction". |
| 4 | Invariants | **PASS** | No seed outside 0–5 appears in any new command or artifact. No golden, label or metric touched: the new files are two CSVs, two docs and the detector dumps. `tesseract.py` unmodified. |
| 5 | Cost columns | **PASS for the new work** | `raw/plateau/gap_decomposition.csv` and `raw/plateau/candidate_pool.csv` carry per-row state, feasibility, the asked week and the detector channel. 3,054 + 240 rows, both re-derivable from the dumps. |
| 6 | Budget matching | **N/A** | Still no targeted lever result. The untargeted control arm remains specified and unrun. |

### Two disclosures owed on the specificity claim (item 1)

Both are method facts, not errors, and neither overturns the direction.

1. **The windows are not like for like.** Decoys are scored over their own label window
   (`d["found_from"], d["found_to"]`); non-planted keys are scored over the whole world
   (`weeks[0], weeks[-1]`). "Ever passing" therefore gets many more chances for a non-planted key than for a
   decoy or a gold pattern. The bias is roughly constant in world size, so the 0.096 → 0.70 *trend* survives
   it, but the absolute level is an upper bound on what a windowed comparison would give.
2. **The sampled pool changes character with size.** Candidates are filtered to keys with ≥3 records anywhere,
   then 300 are sampled per seed. At 200 users that pool barely exists; at 10K it is large. The rate is
   therefore "a random non-planted key *with ≥3 records*", which is the right unit, but the pool it is drawn
   from is not the same population across sizes. The per-seed pool sizes are printed by the tool and were not
   kept in the artifact; they should be.

**And one fact the summary line should carry:** 2,000 → 10,000 is **flat** (0.699 → 0.694), not rising. A pure
density story predicts monotone increase toward 1.0. The saturation says the binding constraint at scale is the
two-site requirement and the 6-week window, not record count. Writing it as "~70% at 2K and 10K" is accurate;
writing it as a trend that continues would not be.

### Why this claim matters more than it looks

It bears directly on this session's own result. The rule-only candidate pool at 200 users is 2,831 keys over six
seeds, of which **83% carry no planted pattern or decoy at all** (`raw/plateau/candidate_pool.csv`). If a random
non-planted key passes the count gate 70% of the time at 2K, then lowering the detection bar to reach the
never-asked patterns is not a lever at any size above this one — it is a false-positive generator. The recursive
loop therefore has to *verify*, not merely *ask*, and its untargeted control has to be budget-matched against
that same pool.

**Result: 1 FAIL (item 2), new, and it is this session's to carry.** It blocks calling `ce0e357` a green tree
and therefore blocks any "current best" claim sourced from it — including the v4 replay now running, whose
verdict will read `pass: false` on this suite whatever the seeds score. It does not touch the official six-seed
baseline, which was run with drills on and whose six reports are digest-pinned.

### Owed, added this tick

9. **This session:** decide the `LIVE_CODE_HASHES_R002` question deliberately — whether two overlapping pinned
   digests over the same globs should both exist, or whether one should be derived from the other. Then re-pin
   with the reason, in its own commit.
10. **This session:** keep the per-seed pool sizes from `specificity_scale.py` in the artifact, and add the
    window caveat to the line in `frontier/CONTEXT.md` that quotes 9.6% / ~70%.

---

## 2026-10-24 — tick 8 (delivered 44 minutes late; audited by the orchestrator, no agent spent)

The tick queued at 15:54Z and reached the session at 16:39Z: a 10-minute cadence cannot be met by turns that
run forty minutes, and a late tick audits a state that no longer exists. This entry covers the whole interval
since tick 6 (`ac3d81c`). Audited: `ng-recovery` through `9dfab02` plus the other session's `a381621`,
`2b19748`, `5e51690`, `6970ac4`, `ecffe22`, `efa93e9`; `ng-arena` through `f985e91`.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS** | Wave 1: ten agents, each with scripts and captured outputs copied verbatim under `swarm/wave1/<id>/`; H7's gate model reproduces `ceiling_b.json` exactly (B 205/240, K 199/240), H4's exactly but for a full-window edge (1/205, stated), H5's cells match the real `detection_inputs` path at Jaccard 0.959. Arena: 12 tests, counted from the run output. The console-started run reproduces the LiveRunTests short configuration. |
| 2 | Traceability | **FAIL, mine, corrected** | Commit `b07e9a3` on `research/arena` says "Thirteen tests green"; the run it was written after showed **11 passed, 1 failed**. The message was written before the result was read. Corrected in `cf49665`, which says so. Everything else traces: every arena event carries the file and line it was read from; `MANIFEST.json`'s stale ng-recovery head (D9's catch) corrected. |
| 3 | Labelling | **PASS** | Every wave-1 number is labelled ESTIMATE (gate model) or, for H2's label-fitted 0.83, "ORACLE-DIAGNOSTIC — NOT a hypothesis". The other session's 127 vs 119 is "diagnostic". The console labels a run by its own verdict: the short run shows `pass=False`, `c3_idempotent` FAILED (within `SHORT_SCALE`), `c10` unevaluated. Nothing is called a baseline. |
| 4 | Invariants | **PASS, one disclosure** | No seed outside 0–5 in any agent output (F-H5's 2,000-user worlds use seeds 0–5 at a larger size). No pin changed; `LIVE_CODE_HASHES_R002` stays stale by decision. `tesseract.py` untouched. `git status` clean in every tree every agent touched — F-H3 disclosed creating and removing a stray `key` file in `/home/user/NeuralGraph`. The console binds loopback and is read-only on every repo. |
| 5 | Cost columns | **PASS** | Wave 1: 2.09M tokens, 712–1,462 s wall each, ω(E) = 10 measured. Console runs report questions (20), decoys (8), found (2/40) beside recall. Agent count 14 of the cap of 24. |
| 6 | Budget matching | **PASS for method, N/A for result** | H6 derived p* before any ranker was scored (0.47 displacing / 0.19 additive); F-H3 applied it and died on it. The budget-matched combination arm (wave 3) is not yet run. |

**Result: 1 FAIL (item 2), mine, caught and corrected within the hour.** It does not touch any measured
number; it touched a claim about tests, which is exactly the class of claim this programme exists to police.

### Owed, added this tick

11. **Both sessions:** the cadence. A 10-minute tick is re-armed as agreed, but the honest unit of audit is the
    wave boundary. Proposal to the Gate-0 session: one entry per wave (spawn, return, control-rule decision),
    with the 10-minute tick reduced to "merge and re-arm" between boundaries.
12. **This session:** fold the four falsifiers' verdicts into `ADVERSARIAL_REVIEW.md` when F-H5/F-H4/F-H7
    land; the earlier independent reviewer (C1–C5) was stopped by the user before reporting (confirmed 16:47Z) —
    said so in the document.

---

## 2026-10-24 — tick 9 (on time; the wave-2 audit; audited by the orchestrator, no agent spent)

Audited: `ng-recovery` through `4b20f83` — wave 2's four falsification reports (`swarm/wave2/`),
`WAVE_2_RESULTS.md`, `NEGATIVE_RESULT.md`, `ADVERSARIAL_REVIEW.md`, `FINAL_ANSWERS.md`, and the swarm's stop.
The other session pushed nothing since tick 8.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS** | Each falsifier re-ran its target's pre-registered test first and reproduced it before attacking: F-H4 reproduces H4-3 to three decimals (0.731) and the official per-seed found 13/12/17/17/19/15 exactly through the replay; F-H7's replay of all 341 questions / 904 confirms reproduces every recorded status and the 93/118 per seed and per type; F-H5's rebuilt arm reproduces H5-2 (0.953 vs 0.946). Scripts and outputs copied verbatim. |
| 2 | Traceability | **PASS** | Every kill carries its number and, where it is a code fact, its file:line (`records.py:412-420`, `verify.py:336`, `gate.py:234/240/262`). F-H7's patch is a proposal with its exact pin consequences computed, never applied. One correction made this interval: the C1–C5 reviewer was "stopped by the user", not "presumed lost" — the document and tick 8's item 12 were wrong and now say so. |
| 3 | Labelling | **PASS** | `NEGATIVE_RESULT.md` labels the one surviving item "alive at replay level only" and "not the hypothesis H4 promoted"; the TB_v4 figures under `nd` are called "model numbers"; H5-2 is "reclassified, not promoted". `ADVERSARIAL_REVIEW.md` separates independent verdicts (Part A) from self-attack (Part B) by name. |
| 4 | Invariants | **PASS** | No seed outside 0–5 in any wave-2 output; 2,000-user worlds use dev seeds. No pin changed; F-H7 computed what a re-pin would write and left it in the ledger's hands. `git status` clean in every tree every falsifier touched. Sealed seeds untouched. |
| 5 | Cost columns | **PASS** | Wave 2: four agents, 934K tokens, 573–1,498 s wall. The swarm's total: 14 agents, ~3.0M tokens. The arena run started from the console reports questions/decoys/found beside its verdict. |
| 6 | Budget matching | **PASS** | The control rule was applied as written: zero survivors → sentence B → stop at 14 of 24. No wave 3 was spawned on the replay-level reorder, because it does not answer the swarm's question and its table is label-fitted; it is handed on as one budget-matched e2e arm against rule-first (128), with a label-free variant required alongside. |

**Result: 0 FAIL.** The swarm closed by its own rule. The three decisions that remain are the founders': the
reorder arm (and the labels-select ruling it needs), the disclosure invariant, and the re-pin.

Note on cadence: nothing is in flight. Until the per-wave cadence (owed 11) is agreed or the auditor is stood
down, ticks will be one-line "no change" entries.

17:04Z — tick 10: no change since 606869a; nothing in flight; no agent spent.

17:15Z — tick 11: 2 new commit(s) from the other session merged; full audit owed next tick.

### tick 11 — the full entry (the one-liner above promised it next tick; delivered now)

Audited: the Gate-0 session's `317aac6` (runbook, world-map builder, `--extra-size` scale probe), `d55daf7`
(`RESEARCH_REPORT.md`), and `4d065d6`; merged at `fcd0695`. No agent spent.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS** | `RESEARCH_REPORT.md` §4.2's 10K figures reproduce from the raw it cites to the unit: `agg_ceiling_by_world_size.txt` lines 6–7 give F 164/240 = 0.683 under the per-site rule and 1,773/1,800 = 98.5% non-planted keys passing the aggregate gate. The 0.742 at 200 (178/240) vs `ceiling_b`'s 0.746 (179/240) is the full-6-week-window protocol difference H4 already recorded (1 pattern). The world map injects report data (`__DATA__`); the template carries no literal figure. |
| 2 | Traceability | **PASS with two gaps** | (a) §5's 10K latency rows (3,294 s main pass) and §6's 10K run cite no `raw/` path — the report's own rule is "every figure links to its raw output". (b) Two pin ledgers now exist (`PIN_LEDGER.md`, `frontier/PIN_LEDGER.md`); they were written separately and agree on substance. Cross-referenced this tick with one line each; no content changed. |
| 3 | Labelling | **PASS** | Every non-official number is labelled DIAGNOSTIC in the report and the runbook; the runbook's "do not claim" list is explicit (no 80%, no 10K/50K accuracy, no held-out). `--extra-size` prints "DIAGNOSTIC scale probe" and "cap raised in memory" on every use; the `s10k_*` arms are `in_process` + `--skip-drills`, diagnostic twice over. The world map shows "Unlabelled" unless an answer key is supplied. |
| 4 | Invariants | **PASS, with a note** | No pin, golden, label or sealed seed touched (`git diff --name-only` clean on all of them). The scale probe bypasses two guards in memory — `check_world_counts` (already bypassed by the wrapper) and the loader's 500-entity schema (`_loader.S_ET` rebuilt in memory) — so c8's invariants are not enforced on a 10K run and no such run can ever be called official; the report says exactly that in §6. |
| 5 | Cost columns | **PASS** | §3 reports questions, found, rare, decoys and no-pattern confirms per arm with raw paths; §5 reports latency per run. |
| 6 | Budget matching | **PASS** | The 127 is reported at the same 60-question budget as the 119 it beats, best-of-ten disclosed, held-out confirmation named as still owed. |

**Result: 0 FAIL.** Three corrections owed to the report, none of them a number:

13. **Gate-0 session:** §8 says the 8 suite failures are "all pin or contract mismatches listed in the ledger" — true,
    and incomplete: 7 of the 8 (the c8 `green_report()` fixture and mutation table) are **repaired** on
    `research/report-integrity` at `5da07a4`; only the `LIVE_CODE_HASHES_R002` failure remains, by decision.
14. **Gate-0 session:** §4's "ruled out by measurement" table predates the swarm's close. Add one row: ranking the
    rule-only pool — spectral, PU, rule identity, information gain, expected utility, co-activation, echo veto —
    zero of four promoted statistics survived falsification; `swarm/NEGATIVE_RESULT.md`, sentence B.
15. **Gate-0 session:** §4.2's column "non-planted keys passing (total gate)" (43.7% at 200) sits beside the
    earlier `specificity_by_world_size.txt` figure for the *per-site* gate (9.6% at 200, K) and `agg_ceiling`'s own
    per-site figure (16.5%). Three numbers for "non-planted keys passing at 200 users" under three protocols; the
    report should say which gate and which window each is, or a reader will take 43.7% as the per-site rate.
16. **Note, both sessions:** §7's "v5 reask offline +8" is on the v4 pool; on the official pool the re-ask lever's
    ceiling is +4/240 (`EVIDENCE_CEILING.md` §7). Different base, not a contradiction — say so where it is quoted.

### tick 11b — `8ed994c` (learned ranker on system verdicts), merged at `237c3b7`; audited ahead of tick 12

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Reproducibility | **PASS** | one script, one raw line (`raw/rescue/learned_rank_seeds0-5.txt`), per-seed figures printed; same replay tooling that reproduces shipped 118 / rule-first 128. |
| 2 | Traceability | **PASS** | raw path cited in `CANDIDATE_RESCUE.md` §5. |
| 3 | Labelling | **PASS** | "replay", "LOSO", "target = system verdict, never the gold label" in the docstring and the doc; `label` read only by `common.score`. |
| 4 | Invariants | **PASS** | dev seeds 0–5; no pin, golden or label touched; the fitted object is indexed by nothing finer than eleven inference-time features. |
| 5 | Cost columns | **PASS** | found, rare, decoys, no-pattern confirms, at B = 60. |
| 6 | Budget matching | **PASS** | same 60-question budget as the orderings it is compared to. |

**Result: 0 FAIL, and a convergence worth recording.** This is the label-free counterpart of the swarm's H2-3:
fitting on the system's own verdicts cannot find gold because the judge supports 207 of 216 asked decoys
(`swarm/wave1/H2/HYPOTHESES.md`), so "supported" carries almost no gold signal. Two sessions, two methods
(PN transfer on the snapshot pool; logistic on the detector pool at the asked week), one answer: **119 vs 118.**
It also settles, for this benchmark, the question F-H4 left open about label-fitted tables — the label-free
ranker is the one that does nothing, which is why the 138 needs a ruling before it is run.
