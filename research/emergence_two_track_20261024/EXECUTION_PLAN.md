# Execution plan — Benchmark integrity & recursive discovery

Written 2026-10-24 after auditing the branch. Every number below is one we already hold; nothing here is
forecast. Status markers are updated in place as the work lands.

## 1. The branch, as it actually stands

| Ref | Head | State |
|---|---|---|
| `claude/session-analysis-continuation-sm1a1t` (my push target) | `1987110` | pushed; carries the figure work and the worktree gitignore |
| `research/emergence-next-continuation-20261024` | `ce0e357` local / `4402c23` on origin | **ce0e357 is not pushed.** Local-only repairs |
| `research/report-integrity` (new, off ce0e357) | working | the §1.5 reporting fix |
| `research/gate-freshness-enforce` | `ce0e357` | freshness parity test, not started |
| `research/n1-sibling-refute` | `fac8d0c` | N1 netting, landed on its own branch |

Commits that matter, newest first:

- `ce0e357` repairs what the adversarial review confirmed: the stale `DETECT_SHA256` pin, c8, the record floor.
- `4402c23` replaces the seed guard with seed-independent invariants, so seeds 1–5 run as themselves rather than
  as guard-bypass diagnostics.
- `d88c150` re-pins the spike code hash for `rule_snapshots`.

## 2. What the integrity audit found, and what it did not

**It did not find bad code.** `mycelic/collective/detect/detectors.py` is byte-identical between `d88c150` (the
tree v4 ran on) and `ce0e357`. The defect is provenance, not behaviour:

1. `test_detection_is_byte_identical_to_its_pin` was red because the test's `DETECT_SHA256` literal still named
   `9d23000e…` while the file hashed `d113c269…`. Re-pinned in `ce0e357`.
2. The v4 ablations ran `--skip-drills` **without** `--suites`, so `c10_suites_green` never evaluated — and the
   verdict still printed `checks_pass: true`, because `verdict()` only looked at checks that ran.

(2) is the one that can mislead a reader, and it is a reporting defect, not a run defect. A run that skips a
required gate must not be able to report a clean bill.

## 3. The reporting fix (§1.5) — in flight

`verdict()` takes `required=`; a required check that appears in `not_run` sets `checks_pass: false`, is listed
under `unevaluated_required`, and becomes the verdict's `reason`. `harness.py` passes `RUN_REQUIRED_CHECKS`
(= `E2E_CHECKS`) so the run job owes every E2E check. Six tests in
`tests/e2e_ingest/test_report_integrity.py` hold the contract, including the one that matters: a run that skips
the drills cannot report `checks_pass: true`, and the skipped gate is named.

Two repairs ride with it, both pre-existing breakage from `ce0e357` that I introduced and did not finish:

- the c8 rework split `world_is_section_3_1` into `world_invariants_are_section_3_1` and
  `world_draw_is_reported`; the green-report fixture never grew a `table_row_diff`, and the mutation table still
  named the removed assertion. Both fixed, with a mutation for the new assertion that fails a draw report naming
  a count the draw may not move.
- `E2E_INGEST_FLOOR` / `E2E_INGEST_TESTS_AT_THIS_BUILD` 159 → 165, deliberately, for the six new tests. The floor
  may only rise. `research/e2e_ingest/suites.py` is not in the 142-file spike-code set, so `SPIKE_CODE_HASH` is
  untouched.

## 4. Order of work, and the decision rule for each step

| # | Step | Decision rule |
|---|---|---|
| P0a | v4 reproduction on `ce0e357`, 6 seeds, **drills on, suites evidence on** | If 163/240 does not survive, v4 is withdrawn as a result, not re-run until it agrees |
| P0b | Reporting fix lands green | Any required check unevaluated ⇒ `checks_pass: false`. No exceptions, no flag |
| 1 | `BENCHMARK_INTEGRITY.md` | States what was wrong, what is true now, and what is now impossible |
| 2 | `EVIDENCE_CEILING.md` | The k-per-site invariant and the four ceilings. 85% is a disclosure decision, not a backlog |
| 3 | Rare-miss recovery, controlled | A case counts as recovered only with an actual corrected verdict. 37/76 are the candidates |
| 4 | Recursive acquisition + budget-matched untargeted control | The loop beats the control at equal budget, or it is not the lever |
| 5 | Graph edge persistence, traced | No graph claim without exercised edges. Today: 12,964 nodes, **0** edges |
| 6 | `ADVERSARIAL_REVIEW.md`, `NEXT_EXPERIMENTS.md` | Every claim this mission makes gets attacked before it ships |

## 5. What this plan deliberately does not do

The ablation evidence is accepted as complete: oracle routing bought **0 points** (46/80 at M=3 and M=all,
seeds 0–1) and the exact-vector counterfactual changed **0 of 165** verdicts at seed 0. Embedding geometry, ANN,
sharding and hypergraph work are therefore optimisations of a path that changes no verdict, and are out of scope.
The reasoning, including against a frontier-literature program that recommends exactly that stack, is in
`LITERATURE_GATES.md`.

## 6. Standing constraints this plan runs under

Dev seeds 0–5 only; sealed seeds 94000–94009 untouched. No paid or hosted model calls inside the benchmark. No
edits to goldens, labels or metrics. Anything that bypasses the guard or skips drills is labelled DIAGNOSTIC.
N-levers need the founders' decision before any held-out run. `tesseract.py` is not mine to modify. Never claim a
new best from an invalid tree, a recovered case without a corrected verdict, or a graph capability without
exercised edges.
