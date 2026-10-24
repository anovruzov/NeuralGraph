# BENCHMARK_INTEGRITY.md — what was wrong, what is true now, and what is now impossible

The short version: **no code was wrong.** `detectors.py` is byte-identical between `d88c150` (the tree v4's
163/240 was produced on) and `ce0e357`. Three pinned digests had gone stale, the suites gate that would have
caught them was never evaluated, and the verdict printed `checks_pass: true` anyway. The defect is provenance
and reporting, not behaviour — which is worse, not better, because behaviour defects announce themselves.

## 1. Three stale pins, one branch, found separately

This repository keeps two kinds of pinned digest. One reads a committed artifact (`CODE_HASHES` from
`effort.json`) and cannot drift. The other hashes the **live code**, and goes stale the moment the code under it
moves on purpose — which is exactly what it is for, but only if every such pin is re-pinned in the same commit.

| pin | covers | moved by | re-pinned |
|---|---|---|---|
| `report.SPIKE_CODE_HASH` | 142 spike files | N1, N2, `rule_snapshots` | **three times** — `b7b1a18`, `8981505`, `d88c150` |
| `DETECT_SHA256` (`test_collective_pushdown.py`) | `detect/{detectors,rules,org}.py` | `9857556` opt-in `rule_snapshots` | **never**, until `ce0e357` |
| `LIVE_CODE_HASHES_R002` (`test_collective_x3.py`) | `EVAL_CODE_FILES` globs `detect/ edge/ packs/ evaluate/`; E2, openfda_replay and X5 add `pushdown/` | the same three changes | **never**, until this work |

So the branch has been red in `tests/mycelic` for the whole of the Track-B v4 effort. Every one of the five
digests in `LIVE_CODE_HASHES_R002` had moved, for the same mechanical reason: the globs cover precisely the
directories the levers changed.

**None of this was refreshed to get green.** Each has been diffed, its cause identified in the commit that moved
it, and re-pinned deliberately with that reason recorded beside the constant — `ce0e357` for `DETECT_SHA256`,
and here for the other, renamed `LIVE_CODE_HASHES_TB_V4` so the name carries the change that moved it.

## 2. The hole that let it run for days

v4's ablations were invoked with `--skip-drills` and **no** `--suites`. `c10_suites_green` therefore never
evaluated. And `verdict()` only looked at the checks that *ran*:

```python
checks_pass = not [name for name, c in checks.items() if not c.get("ok")]
```

An empty failure list over a short list of checks is indistinguishable from an empty failure list over all of
them. `raw/v4_s0_replay_reference.json` records `checks_pass: true` on exactly this basis. The runs that would
have caught the stale pins were the runs nobody ran, and the verdict format could not say so.

This is not a hypothetical. The test suite itself encoded the hole:
`LiveRunTests::test_only_the_floors_this_scale_cannot_reach_fail` asserted
`checks_pass == not failed` on a run whose `not_run` contained `c10_suites_green`.

## 3. The fix: a required check that did not run is a failure

`verdict()` now takes `required=`, and `harness.py` passes `RUN_REQUIRED_CHECKS` (= every E2E check), so the run
job owes all of them:

```python
unevaluated = sorted(name for name in required if name in missing)
checks_pass = not failed and not unevaluated
if unevaluated:
    reason = f"required checks not evaluated: {', '.join(unevaluated)}"
```

The verdict now carries `unevaluated_required` alongside `failed`, so a reader sees *which* gate was skipped
rather than inferring it from a short `checks_run` list. Six tests in `tests/e2e_ingest/test_report_integrity.py`
hold the contract, the sharpest being: a run that skips the drills cannot report `checks_pass: true`, and the
skipped gate is named. The live floors test was updated to the new contract — tightened, not relaxed.

`E2E_INGEST_FLOOR` and `E2E_INGEST_TESTS_AT_THIS_BUILD` rise 159 → 165 for those six, deliberately, as the floor
may only rise. `research/e2e_ingest/suites.py` is not among the 142 spike files, so `SPIKE_CODE_HASH` is
untouched by that change.

## 4. The fix for the recurrence: one test that names every live-code pin

Three pins went stale separately on one branch, and each was found by a different accident. `LiveCodePinTests`
in `tests/mycelic/test_collective_x3.py` now checks all three in one place, with a message that tells the next
person to re-pin deliberately and to look at the others while they are there. A new live-code pin belongs in
that list, not only beside its own constant.

## 5. What this means for the v4 figure

163/240 was produced on a tree with a stale `DETECT_SHA256`, by runs that skipped the drills and never
evaluated the suites gate, and recorded as `checks_pass: true`. Under this project's own rule — no result
claimed from a stale-pin tree — **it is not a current best and should not be quoted as one** until it is
reproduced on a tree whose pins are current, with drills on and suites evidence supplied.

That reproduction is running. One thing is already settled about it: because it runs on `ce0e357`, which still
carries the stale `LIVE_CODE_HASHES_R002`, its `tests/mycelic` is red and its verdict will read `pass: false`
whatever the six seeds score. The seed scores will still be worth having, and they will be reported as what they
are — scores from a run that did not pass — not as a reproduction of the claim.

*(Per-seed results and the verdict will be filled in here when the run completes.)*

## 6. What is now impossible that was possible before

1. A run that skips a required gate cannot report `checks_pass: true`. It reports `false` and names the gate.
2. A reader of a verdict cannot mistake "nothing failed" for "nothing was checked": `unevaluated_required` and
   `checks_run` are both in the document.
3. A live-code pin cannot go stale alone without `LiveCodePinTests` failing — the other two are checked in the
   same test, by name, with the instruction attached.

What is still possible, and worth saying: a *required* list that is itself wrong. `RUN_REQUIRED_CHECKS` is
`E2E_CHECKS` today, which is right for the run job, but nothing forces a future job to declare its obligations
honestly. The guard is only as good as that list.
