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
