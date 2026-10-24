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
