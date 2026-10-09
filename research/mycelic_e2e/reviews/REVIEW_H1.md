# Review of the harness fix H1

## Review of harness fix H1 fe8adfa996c739229bfacadb4b59a63fc9199a23

**Reviewer:** REVIEWER-4, model `claude-opus-5-5` (independent; did not write the patch; patch author claude-sonnet-5-5)
**Verdict: ACCEPT.** No concrete defect found. Four non-blocking notes follow.

Scope: commit `fe8adfa` ("Harness: a per-task API error or timeout makes that task wrong instead of aborting the run (H1)"), parent `4c27744`. It changes bench/issue.py (+81/-4), bench/run.py (+12/-6) and adds bench/tests/test_api_isolation.py (+237). HEAD moved to `aec440f` during the review. That commit changes only `EXPERIMENTS.jsonl` and `reviews/REVIEW_WP1.md` (`git diff --stat fe8adfa aec440f`), so the reviewed bench code is identical at HEAD. Nothing under /root/sealed_holdout was opened. /home/user/ng-impl was not modified: its pre-existing ` M research/mycelic_e2e/EXPERIMENTS.jsonl` is not mine. All work was done in the copy at `$SCR/rv10`.

### Commands and outputs
```
$ git -C /home/user/ng-impl log --oneline -1          -> fe8adfa Harness: a per-task API error ... (H1)
$ git -C /home/user/ng-impl archive fe8adfa | tar -x -C $SCR/rv10
$ git diff --stat 4c27744 fe8adfa                     -> issue.py 81 +++-, run.py 12 +-, tests/test_api_isolation.py 237 +
$ cd $SCR/rv10 && python -m pytest research/mycelic_e2e/bench/tests -q -p no:cacheprovider --basetemp $SCR/rv10_bt
  181 passed, 2 skipped, 11 warnings in 14.14s        (matches the engineer's report)
$ python -I $SCR/rv10_checks/check_h1.py $SCR/rv10    -> 37 checks, FAILURES: 0   (full output: $SCR/rv10_checks/out.txt)
$ python -c 'issubclass(...)'  (py 3.13.16, aiohttp 3.14.4):
  TimeoutError, aiohttp.ServerTimeoutError, aiohttp.ClientError -> Exception (caught)
  asyncio.CancelledError, KeyboardInterrupt                      -> BaseException only (not caught)
```
My script `$SCR/rv10_checks/check_h1.py` imports bench from the copy and checks the following:
- A. No-error equivalence. A rich stub API (claims, discoveries, goal-level output, raw checks) drives `issue_task`/`question_status`/`run_loop_now`/`collect_view` and their `safe_*` wrappers. Result: an identical 14-call sequence, identical `Issued`/status/loop/view results, and every `api_errors` count at zero.
- B. A failure late inside `collect_view`, on `/raw`, `/api/discoveries/`, the second `GET /api/goals/g1` or `/api/claims/c2`, after earlier reads succeeded. Each gives a full error view: status=error, tenant_id kept, claims/discoveries/evidence/raw_checks empty, question None. No partial view is ever scored.
- C. `CancelledError` and `KeyboardInterrupt` propagate from all four wrappers. The engineer's test covers only the status wrapper.
- D. `"error"` is in neither `issue.RESOLVED` nor `score.RESOLVED_STATUSES`.
- E. For all 10 real classes (world.py names, including `common_origin_copies`), both the issue-error view and the read-error view score `correct=False` with `reason=error`. For the 5 abstain classes I assert `expected_abstain=True`. A sanity check shows that the same empty view with `status=ok` WOULD score abstain-correct for `denied`. The `status="error"` field is therefore what keeps these views wrong, and the patch always sets it.

### 1. Correctness
- **Every per-task call in the wave loop is wrapped.** The wave loop calls `safe_issue_task` (run.py:274), `safe_question_status` (run.py:283), `safe_run_loop_now` (run.py:291) and `safe_collect_view` (run.py:296). No bare `issue.*` call remains (the source grep in test_api_isolation.py:187-193 agrees).
- **The `asyncio.gather` (run.py:275) can no longer raise for an API failure.** `one(t)` can only raise from `tokens[...]`/`ids.units[...]` lookups, which are deterministic configuration errors, or from BaseException.
- **No other `issue.*` callers exist.** No other bench module calls these functions: `grep -rn "collect_view|issue_task|run_loop_now|question_status" --include=*.py bench`, excluding tests, issue.py and run.py, finds none.
- **Every wrapper catches `Exception` only** (issue.py:184, 193, 202, 220). CancelledError and KeyboardInterrupt propagate (check C).
- **Run-level steps are not wrapped and still abort.** materialize, feed (run.py:219), heartbeat waits, `build_worker`/`worker.start()` (run.py:259-260), `wait_quiet` and `rt.worker.wake()` are untouched. The diff touches only the four call lines, the `api_errors` construction (run.py:266), the manifest keys (run.py:324) and a log line (run.py:335-336).

### 2. Integrity
- **Error views score wrong for every class.** `_view_problem` (score.py:357-358) returns `("error", ...)` for any status outside ok/done/resolved before any class logic runs. `extract_answer` (score.py:380-382) then returns `option=None`, and `score_task` (score.py:540-541) returns `correct=False`. The abstain branch (score.py:542) is never reached. Verified for all classes, abstain included (check E).
- **The error view carries the asker's tenant_id and no content.** `error_view` (issue.py:209-214) builds on `_empty_view` (issue.py:73-75). That is the same dict `collect_view` built before the patch: I compared the old inline literal with the new helper and the keys and values are identical. tenant_id comes from `ids.tenants[t["tenant"]]` (run.py:296), as before. `score_tasks`' tenant_id requirement (score.py:652-655) is satisfied, and the engineer's `test_run_with_one_error_view...` (test file :221-237) runs `score_run` end to end.
- **A failed status poll cannot end the wait early or resolve anything.** `safe_question_status` returns `"error"` (issue.py:196), which is not in `RESOLVED` (issue.py:16). `all(s in RESOLVED ...)` (run.py:285) therefore stays false and polling continues to the deadline. Poll results are used only to decide whether to break. Resolution comes only from the final `collect_view` GET.
- **No change in behavior without errors.** Same calls, same arguments, same order, same deadlines (`deadline`, `wait_quiet`, `sleep(1.0)` unchanged). The only additions are two manifest keys (`api_errors`, `api_error_tasks`) and one conditional log line. Verified by diff and by check A.
- **G7 cross-check.** For an error view, `arch_gate._cross_check_views` (arch_gate.py:820-905) finds no claims, discoveries or question to check (question None), so G7 does not fail falsely.

### 3. Tests
The engineer's 181 passed / 2 skipped reproduces. test_api_isolation.py has no vacuous assertions; each test asserts concrete status, error prefix, counts and log lines. It has these gaps, all covered by my script:
- CancelledError is tested only for the status wrapper (:174-184).
- No test compares calls and results when no error occurs.
- The driver check is a source grep (:187-193) rather than an execution of the wave loop.
- The class parametrization (:197-218) does not assert `ts.expected_abstain`. Its labels `common_origin`/`copies` are not world.py class names (`common_origin_pos`/`common_origin_copies`). Scoring keys off gold, so this is harmless.

### 4. Ablations, baseline and report
- `ablations.py` patches only `mycelic.*` bindings (ablations.py:99-167) and never `bench.issue`. It runs through the same `run.amain`, so it inherits the fix.
- `baseline_central.py` does not import `bench.issue`. It already writes its own error views with tenant_id (baseline_central.py:595).
- `report.py`, `ledger.py:315` and `tools/results_table.py` read the manifest with `.get`. The new keys are additive and break nothing.

### Non-blocking notes
- **N1. Loop and status failures are counted, not made wrong.** The commit title and the header comment at issue.py:155-157 say "makes THAT task wrong". For the `loop` and `status` kinds, the task is still scored on its later view. This cannot inflate a score:
  - A status failure can only lengthen the wait (see §2).
  - `run_loop_now` is called only for goal-only tasks (run.py:290). These are always the positive class `cross_domain` (world.py:695-697), so a missed extra tick cannot produce an abstain-correct result.
  - Before the patch, an HTTP non-2xx from `run_now` was already ignored (the return value was discarded at 4c27744 run.py:290).

  Suggestion: report `api_error_tasks` next to the accuracy figure. Alternatively, a later change could make loop-error tasks error views, if the contract is to be read literally.
- **N2. Liveness.** The poll loop checks `deadline` only once per iteration (run.py:282). Each poll is sequential and can block up to 300 s (feed.py:25, `ClientTimeout(total=300)`). With `--wave 15` (run.py:357), a starved API can overrun `T_task=180 s` by up to 15×300 s per iteration. Before the patch, such a run aborted instead, and slow-but-successful polls behaved the same. This affects time only, not scores.
- **N3. goal_id is lost on a partial issue failure.** If the goal POST succeeds and the question POST raises, `safe_issue_task` (issue.py:183-189) returns a fresh `Issued` without `goal_id`/`activation_error`. The goal stays active server-side, but `correlation.tasks` (run.py:328-329) shows `goal_id: null`. This affects observability only; the task is an error view. `issued_at` is also the time of the failure, not of the issue.
- **N4. Disclosure about holdout material.** Two early recursive greps over `research/mycelic_e2e/bench` (`goal_only`; manifest and view consumers) did not exclude `bench/holdout/`. Neither printed any line from it, nothing there was listed or opened, and all later greps used `--exclude-dir=holdout`.

---


## Review of harness fix H2 7d2e63b

**Reviewer:** REVIEWER-4, model `claude-opus-5-5` (independent; did not write the patch; engineer claude-sonnet-5-5)
**Verdict: ACCEPT.** No concrete defect found.

Scope: commit `7d2e63bc765d9adffd3e34c93bdb2c7cb4351fc3`, parent `0852d33`. The bench code at 0852d33 is identical to fe8adfa (`git diff --quiet fe8adfa 0852d33 -- research/mycelic_e2e/bench` → identical). The commit makes 3 lines of change in bench/issue.py:146-148 and adds tests/test_raw_check_coverage.py (+82). score.py is unchanged (`git diff --stat 0852d33 7d2e63b -- .../score.py` is empty). Nothing under /root/sealed_holdout or bench/holdout was opened, and ng-impl was not modified. The commit's Co-Authored-By trailer names Opus 5.5; the coordinator says it normalized the trailer. The engineer was claude-sonnet-5-5, so the ledger should record that.

### Commands and outputs
```
$ git -C /home/user/ng-impl archive 7d2e63b | tar -x -C $SCR/rv11
$ cd $SCR/rv11 && python -m pytest research/mycelic_e2e/bench/tests -q -p no:cacheprovider --basetemp $SCR/rv11_bt
  183 passed, 2 skipped, 11 warnings in 16.18s          (matches the engineer's report)
$ python -I $SCR/rv10_checks/check_h2_c6.py $SCR/rv11 $SCR/runs/C6-S1      (all 120 views, scorer's own check_disclosure)
  views with unchecked refs: 56  total unchecked: 1039          (= score.json "disclosures": 1039)
  unchecked refs by source set: {('goal_discoveries',): 1039}
  unchecked refs NOT in goal_discoveries[*].evidence (would remain after H2): 0
  goal-discovery embedded claims carrying their own evidence: 0
  sample dev-001 coincidence    60 unchecked: q_claims 0, view_evidence 0, q_discoveries 0, goal_claims 0, goal_evidence 0, goal_discoveries 60
  sample dev-002 single_domain  16 unchecked: ... goal_discoveries 16
  sample dev-024 denied          4 unchecked: ... goal_discoveries 4
  claims columns containing 'evid': []
$ python -I $SCR/rv10_checks/check_h2_open.py $SCR/rv10   (pre-H2 code)  vs  $SCR/rv11   (H2)
  pre-H2  raw=200 required=False: raw_checks={}            raw_unchecked=['r_x'] count=0 -> correct=True  reason=ok    <- an open raw was invisible
  pre-H2  raw=403 required=True : raw_checks={}            raw_unchecked=['r_x'] count=1 -> correct=False reason=leak  <- a refused raw counted as a leak (the C6-S1 symptom)
  H2      raw=200 required=False: raw_checks={'r_x': 200}  raw_open=['r_x']      count=1 -> correct=False reason=leak
  H2      raw=200 required=True : raw_checks={'r_x': 200}  raw_open=['r_x']      count=1 -> correct=False reason=leak
  H2      raw=403 required=*    : raw_checks={'r_x': 403}  raw_open=[]           count=0 -> correct=True  reason=ok
  H2      raw GETs: ['/api/evidence/r_x/raw'] | view.evidence: [] | goal_evidence: []  (question-level lists unchanged)
```

### 1. Coverage: every ref the scorer counts is now raw-checked
The scorer's visible refs are `all_visible_refs` (score.py:299-314) over the question view, merged with the goal-level view `goal_level_view` (score.py:438-441, 453-457). The table maps each source to where collect_view raw-checks it:

| Scorer source | collect_view raw-checks it at |
|---|---|
| question claims: `item.evidence`, else `claim.evidence`, else the parent discovery's evidence (`view_claims`, score.py:256-281) | claim detail top-level `evidence`, issue.py:110-116. `claim_detail` returns refs only at the top level (mycelic/knowledge/service.py:332-333). The nested `claim` is `claim_summary` (a claims row), and the claims table has no evidence column (PRAGMA above). |
| `view["evidence"]` | It is `list(seen_refs.values())` (issue.py:124), so every entry is checked. |
| question discoveries `[*].evidence`, and their embedded `claims` (shared refs) | issue.py:117-123. The embedded claims are `claim_summary` rows with no own evidence, so the scorer uses the discovery's refs (service.py:895). |
| goal claims `[:60]` and `goal_evidence` | issue.py:130-139 (`goal_evidence` is a subset of these). |
| **goal discoveries `[:30]` `[*].evidence`, and their embedded claims** | **issue.py:146-148, new in H2.** This was the only gap. |
| `lineage` in discovery details | The scorer does not read it. Its evidence nodes come from `refs_for_claim` over the same discovery claims (service.py:926-927), the same set as that discovery's `evidence`. |

H2 appends the new refs to `seen_refs` after `view["evidence"]` was copied (issue.py:124) and after the goal-claim loop. So `view["evidence"]`, `view["goal_evidence"]`, the claim call order and the order of the existing raw GETs are unchanged; the new raw GETs come last. This is confirmed by the engineer's test (test_raw_check_coverage.py:74-82) and by my check.

### 2. Integrity
- **Real disclosures stay visible.** In check_disclosure (score.py:476-484), a checked ref whose code is not in `OK_RAW_REFUSALS = {401, 403, 404}` (score.py:62) and whose holder is not raw-allowed counts as `raw_open`. A 200, a 5xx or a non-integer code are all counted as disclosures.
- **H2 makes measurement stricter, not more lenient.** Before H2, a goal-discovery ref with open raw was hidden when `raw_checks_enabled` was false: count 0, abstain scored correct. With raw checks required, a correctly refused ref was counted as a leak. After H2, an open raw is always `raw_open`, so the abstain task is wrong, and a 403 is not a disclosure. See check_h2_open above.
- **Errors still make the task wrong.** An exception raised by any of the extra raw GETs goes through H1's `safe_collect_view` and yields an error view, which is scored wrong.
- **No scoring change.** score.py is unchanged, `raw_required` still comes from `manifest["raw_checks_enabled"]` (score.py:657), and there are no other code changes.

### 3. Tests
183 passed, 2 skipped. test_raw_check_coverage.py is not vacuous. It asserts:
- every raw-check key, and the goal-discovery ref's 403;
- that a control view without that entry reports `raw_unchecked == ["ev_goal_disc"]` (:60-71);
- unchanged lists and call order (:74-82).

It does not cover an open (200) raw on a goal-discovery ref. My `check_h2_open.py` covers that case and shows it counts as `raw_open`.

### 4. C6-S1 evidence
All 24 failing expected-abstain tasks in `$SCR/runs/C6-S1/score.json` have reason `leak`, with foreign 0, markers 0, raw_open 0 and only `raw_unchecked`: single_domain 8, coincidence 7, common_origin_copies 5, denied 3, cross_tenant 1. Across all 120 views, each of the 1039 unchecked refs is listed only in `goal_discoveries[*].evidence`, and none would remain after H2. Samples: dev-001 (coincidence, 60), dev-002 (single_domain, 16), dev-024 (denied, 4).

### Non-blocking notes
- **N1.** C6-S1 and C6abl-A1-S1 cannot be re-scored into the H2 result: their views never made those raw requests. Whether the 24 tasks become correct, or show a real `raw_open`, needs a new run.
- **N2.** The raw-check count per view grows by up to 70 in C6-S1 (dev-109). That is more wall time, and more chances for an API failure that H1 turns into an error view.
- **N3 (pre-existing, outside H2).** The goal-level caps `[:60]` claims and `[:30]` discoveries (issue.py:130, 140) mean output beyond them never reaches the view, so the disclosure check cannot see it at all.

---

## Review of the A5 ablation fix (scratch dec4637)

**Reviewer:** REVIEWER-4, model `claude-opus-5-5` (independent; did not write the patch; engineer claude-sonnet-5-5)
**Verdict: ACCEPT. No concrete defect found.**

Scope: scratch repo `$SCR/code/a5fix`, commit `dec4637` on `9d01a5b` (the base copy of 7d2e63b). `git diff 9d01a5b dec4637` is byte-identical to `$SCR/a5fix.diff`. The commit changes bench/ablations.py (+26/-7), bench/run.py (1 line) and bench/tests/test_ablations.py (+63). I did not modify /home/user/ng-impl, and I did not open /root/sealed_holdout or `$SCR/runs/H-*`. I did not inspect the other running process (cwd `$SCR/code/7f37551`).

### Commands and outputs
```
$ cd $SCR/code/a5fix && PYTHONDONTWRITEBYTECODE=1 python -m pytest research/mycelic_e2e/bench/tests -q -p no:cacheprovider --basetemp $SCR/rv12_bt
  185 passed, 2 skipped, 11 warnings in 12.74s            (matches the engineer's report)
$ grep -rn "_can_route|can_route_many|\.can_route(" --include=*.py mycelic   (excluding tests)
  authz.py:504  can_route      -> self._can_route(question, holder, self.route_context(...))
  authz.py:510  can_route_many -> [self._can_route(question, h, ctx) for h in holders]
  inquiry/service.py:395 candidate_holders -> self.authz._can_route(q, h, ctx)        (direct: the path the first A5 missed)
  can_route callers: discovery/engine.py:321 (can_route_many), :371, :1174, :1344; api/routes_org.py:546; inquiry/service.py:554, :599;
                     knowledge/hypergraph.py:690; knowledge/gate.py:211        -> all reach _can_route
$ python -I $SCR/rv10_checks/check_a5.py $SCR/code/a5fix <tmp>     (spy wraps the patched _can_route and also evaluates the saved real rule)
  patch counters: {'can_route_calls': 11, 'loosened': 6} | independent tally: {'calls': 11, 'loosened': 6}
  COUNTERS MATCH ; assertion "ablation never refuses what the real rule allows" held on every call
  restored to real: True | no-ablation manifest value: {}
  A1..A4, A6: counters {} / describe.calls {}
$ python -I -c 'import ...bench.report, ...bench.arch_gate, ...bench.score; ...'
  ablations imported by gate/report/score chain: False | run imported: False

### 1. Correctness
- **Every routing entry point reaches the replacement.** The table below lists them. Patching `can_route` alone left `candidate_holders` untouched, which is exactly the C7abl-A5-S1 no-op that ANALYST-2 found.

| Entry point | Calls | Reaches replacement |
|---|---|---|
| `candidate_holders`, which `route()` uses | `_can_route` directly (inquiry/service.py:395) | yes |
| goal-domain scan | `can_route_many` (engine.py:321) → `_can_route` (authz.py:510) | yes |
| all other callers | `can_route` → `_can_route` (authz.py:504) | yes |

- **The replacement is installed before any caller can hold a reference to it.** `p.setattr` replaces the class attribute (ablations.py:79-83). It is applied at run.py:127, before `build_runtime` at run.py:131. No module stores a bound reference: every hit in the grep is a call.
- **The signature matches.** The replacement is `(self, question, holder, ctx)`, the same as authz.py:512, and the result is the same `(allowed, reason)` tuple.
- **The tenant boundary and revoked holders are still refused.** See ablations.py:172-175 and the engineer's test.
- **The counters are accurate.** My spy independently evaluates the saved real rule on the same `(q, h, ctx)`. Its tally equals the patch's counters (11 calls, 6 loosened), and the ablation never refused a holder the real rule allows.
  - The extra call to `original(...)` has no side effects beyond the memo caches the unablated system fills anyway: no DB write and no audit (authz.py:512-559).
  - Both counters count calls, not distinct routes. One route is evaluated by `candidate_holders` and again by the use-time check (service.py:554) and gate.py:211. So `loosened` is evidence that the ablation is live, not a count of leaked routes.
- **The ablation removes the asker check too.** G4's replay calls `can_route` without `asker` (arch_gate.py:736), so G4 cannot see asker-only loosenings. As a result, G4 denials can be fewer than `loosened`, as expected.

### 2. Harness-only
- **The patch applies only under `--ablation A5`.** `ablations.apply` is called only when an ablation is named (run.py:127). `test_nothing_is_patched_by_default` now also pins `Authorizer._can_route.__module__ == "mycelic.authz"`.
- **Without an ablation, `ablation_calls` is `{}`** (run.py:314: `dict(abl_patch.counters) if abl_patch else {}`). The other ablations (A1-A4, A6) also give `{}` (my check).
- **No system, scorer or gate code changes.** The diff touches only bench/ablations.py, one manifest line in bench/run.py and the tests.
- **The gate keeps the real rule.** report.py, arch_gate.py and score.py do not import ablations or run.py, checked through sys.modules. G4's replay builds its own `Authorizer` in its own process (arch_gate.py:713-736), so it uses the real `_can_route`. The scorer and gate read only `run_manifest` keys they know, so the new `ablation_calls` key is additive.

### 3. Tests
185 passed, 2 skipped. The new `test_a5_replaces_the_one_choke_point...` is not vacuous:
- On the real `candidate_holders` path, it asserts that the out-of-scope holder `hb` and the private-only holder `hc` are admitted, with `loosened >= 2`.
- After `restore()`, the same question rejects both holders with the real reasons, and the counters freeze.

`test_ablation_counters_reach_the_manifest` is a source grep plus a `describe()` check; that is acceptable.

### 4. Dev run C7abl-A5fix-S1 (finished 10:29:34; code `$SCR/code/a5fix` at dec4637, log `rev=7d2e63b+a5fix`, "1 binding(s) replaced")
```
run_manifest.json: ablation=A5  ablation_patches=['mycelic.authz.Authorizer._can_route']
                   ablation_calls={'can_route_calls': 243748, 'loosened': 155645}   api_errors all 0   n_tasks=120
log: done in 487.0s: views=120 questions={'committed': 95, 'retained_uncertain': 15, 'None': 10} supported_claims=68
$ python -I $SCR/rv10_checks/gate_a5.py $SCR/code/a5fix $SCR/runs/C7abl-A5fix-S1 $SCR/rv12_gate/coord.db
  (fresh process, sqlite backup of data/coord.db, no --write; the run dir is untouched)
  ablations module loaded in gate process: False
  architecture gate: INVALID (G4)
  G4 fail: 2742/2742 questions routed; rank_method {'hypergraph': 2742}; as-of replay denials 13554
           (current domains differ from route-time domains on 0 of 27420 routes)
     e.g. q_c499dc77fbf3473f8214 -> hold_7908acafbaa74eaeb506: can_route denies as of the route (no matching evidence domain; ...)
  G1 G2 G3 G5 G6 G7 G8 G9 G10: pass
```
- **`loosened` > 0.** The replacement ran 243,748 times on the live routing path and changed the outcome 155,645 times.
- **G4 now fails, as the ablation design intends.** The earlier no-op A5 (C7abl-A5-S1) had 0 replay denials and G4 valid. Now there are 13,554 denials over 27,420 routes, so G4 is shown to be load-bearing.
- **Nothing else moved.** All other gates pass, so the ablation did not disturb any other mechanism.

### Non-blocking notes
- **N1.** `loosened` counts calls, not distinct routes. The same (question, holder) pair is evaluated several times (candidate_holders, the use-time recheck, the gate.py:211 recheck). Report it as liveness evidence, not as a number of leaked routes.
- **N2.** A5 also drops the asker check, which G4's replay does not evaluate (it runs `can_route` without `asker`). G4's denial count is therefore a lower bound on what the ablation changed.
- **N3.** The extra real-rule call roughly doubles routing-authorization work under A5 only. It has no effect on a run without an ablation.
- **N4.** The run process was still shutting down when I copied coord.db. I copied it through the sqlite backup API, after the "done" log line, when routing was complete.

---

## Review of the A4 ablation fix 3c18b1a

**Reviewer:** REVIEWER-4, model `claude-opus-5-5` (independent; did not write the patch; engineer ENGINEER-3, claude-sonnet-5-5, whose trailer is accurate)
**Verdict: BLOCK. The A4 mechanism is correct, but the committed change makes the A4 run report as INVALID (unexpected G4 failure). One-line harness fix below.**

Scope: commit `3c18b1a` on `31533dd`, copied with `git archive 3c18b1a | tar -x -C $SCR/rv13`. It changes bench/ablations.py (+37/-12) and bench/tests/test_ablations.py (+139/-17); there are no system, scorer or gate changes. I did not modify ng-impl, and I did not open /root/sealed_holdout or `$SCR/runs/H-*`.

### Commands and outputs
```
$ cd $SCR/rv13 && PYTHONDONTWRITEBYTECODE=1 python -m pytest research/mycelic_e2e/bench/tests -q -p no:cacheprovider --basetemp $SCR/rv13_bt
  187 passed, 2 skipped, 11 warnings in 13.27s            (matches the engineer's report)
$ grep -rnE "(INSERT|UPDATE|DELETE|REPLACE)...(term_index|entity_registry|hyperedges|hyperedge_members)" --include=*.py mycelic   (no tests)
  every writer is in mycelic/knowledge/hypergraph.py
$ grep -c INSERT mycelic/db/migrations/0005_hypergraph.sql mycelic/db/migrations/0007_term_index.sql  -> 0, 0   (no SQL backfill)
$ python -I $SCR/rv10_checks/check_a4.py $SCR/rv13
  (imports EVERY mycelic module, applies A4, then scans every mycelic module's attributes and the gc referrers of the real sinks)
  patched: ['mycelic.knowledge.hypergraph.upsert_entity_index_sync', 'mycelic.knowledge.hypergraph.upsert_term_index_sync']
  module attributes still bound to a real sink: []
  entity / term referrers: my own lookup dict + the patch's undo-lambda defaults only (no closure, partial or default arg elsewhere)
  other hypergraph functions using _registry_count / _new_index_version / _bump_count: none (only the replaced entity sink)
  restored: True
```

### 1. No other writer can fill the indexes under A4
Mapping each index table to its writers:
- **`term_index`**
  - Inserts happen only in `upsert_term_index_sync` (hypergraph.py:491-497), which A4 replaces.
  - `withdraw_holder_entities_sync` only *deletes* rows (hypergraph.py:531).
- **entity-index hyperedges and members (`kind='entity_index'`)**
  - Created only in `upsert_entity_index_sync`, through `_insert_edge(kind="entity_index")` (:443), `_new_index_version` (:374-392, called at :447 and :465) and `_bump_count` (:452-471), all inside the replaced sink.
  - Every other `'entity_index'` hit is a read: org.py:574, routing.py:81, hypergraph.py:536 and :646.
- **`entity_registry`**
  - Written only by `_registry_count` (:411-413), whose only callers are :456 and :477 inside `upsert_entity_index_sync`.
  - **It needs no separate path:** grepping other modules for `_registry_count|_new_index_version|_bump_count|_insert_edge(` finds nothing.
- **Claim `about` entity members** on support/discovery edges (`scrub_claim_entities_sync` :280-298, the claim-edge writers) are claim-text tagging. They are not the entity index, and G8's index checks do not read them.

How the callers reach the replacement:
- `rebind` replaces every `mycelic.*` module attribute that is the original. org.py calls `hg.upsert_*` (org.py:578, 588), which resolves at call time, and `withdraw_holder_entities_sync` calls the entity sink through its module global (:529). Both resolve to the replacement.
- This covers all three heartbeat feeders, which converge on `OrgService.holder_heartbeat`, and the revoke / opt-out withdrawal (org.py:615-616).
- My scan shows no surviving reference to the real sinks anywhere in the loaded code.

### 2. Returning 0 is what the callers expect
- org.py:581 `if changed:` and org.py:590 `if n_terms:` gate only the `holder.entities_published` / `holder.terms_published` audit rows. With 0, those audits are simply absent, consistent with an empty index.
- `withdraw_holder_entities_sync` adds the result (`n += ...`, :529). 0 is a valid integer.
- **No "published" flag depends on these returns.** `published_domains` is written separately (org.py:563-565) and is untouched, so domain routing is not ablated. That is correct: A4 targets only the entity and term indexes.
- The holder's own reported stats (`entities_reported` / `terms_reported`) stay intact, as the engineer's test asserts.

### 3. Harness-only
- **The patch is installed only under `--ablation A4`.** `ablations.apply` runs only when an ablation is named (run.py:127) and dispatches A4 to `_a4` (ablations.py:109). `test_nothing_is_patched_by_default` pins both sinks to their real `__module__`/`__name__`.
- **The old `_heartbeat_stats` patch is gone**, so the embedded feeder is unpatched. The test asserts the holder still reports its terms and entities.
- **The manifest gets the counters** under `ablation_calls` (run.py:314, unchanged since A5).
- **No system, scorer or gate code changes.** report.py runs the gate without `expect_term_index`, so the term-coverage check applies (arch_gate.py:1056-1083). `EXPECTED_GATE_FAILURES["A4"] = ["G8"]` (ledger.py:43).

### 4. Tests are not vacuous
- **Control:** without the ablation, each of the three feeders fills the index (`term_rows == 3*len(TERMS)`, entity edges and members, `published_audits > 0`).
- **Ablated:** the same beats leave all four quantities at 0. The counters equal exact expected values (12 calls each; suppressed = 12×len). The withdrawal path increments the entity counter.
- **Restore:** after `restore()`, the next beat fills the index again and the counters freeze.
- **G8:** G8 passes on the control database (3/3 term holders, 2 entities) and fails on the A4 database. The test asserts the exact problem strings "term index covers 0/3" and "entity index covers 0/2".

### 5. Dev run C8abl-A4fix-S1 (log `rev=3c18b1a`, "2 binding(s) replaced"; run finished 10:53:51; `report --finalize` wrote arch_gate.json)
```
run_manifest.json ablation_calls = {'entity_index_calls': 20333, 'term_index_calls': 20333, 'entities_suppressed': 0, 'terms_suppressed': 1038293}
                  api_errors all 0; n_tasks 120
final coord.db (sqlite backup copy): term_index 0 rows; hyperedges kind='entity_index' 0; entity_registry 0; terms/entities_published audits 0
arch_gate.json (written by report.py): valid=False failed=['G4', 'G8']
  G8: "term index covers 0/112 = 0.00 of the holders with public records (< 0.95)"         <- intended
  G4: "ranker expected but no route audit row has rank_method 'hypergraph'"                  <- NOT expected for A4
my read-only gate run (fresh process, ablations not loaded) reproduces it exactly:
  G4 fail: 3317/3317 questions routed; rank_method {'domains': 3317}; as-of replay denials 0 (0 of 32834 routes)
  G8 fail: entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records), terms 0/112 holders (0%), 0 violating
  G1 G2 G3 G5 G6 G7 G9 G10 pass
report.md:32  | ablation A4 (C8abl-A4fix-S1) | ... | INVALID: G4,G8 | 120 | 111 | 92.5% | ...
report.md:34  | ablation A5 (C7abl-A5fix-S1) | ... | ablation: G4   | ...     (for comparison)
```
- **`ablation_calls` > 0.** Both sinks ran 20,333 times on the live path, and 1,038,293 term ids were suppressed.
- **G8 now fails as intended.** The earlier A4 runs C6abl-A4-S1 and C7abl-A4-S1 are both "valid" at report.md:30-31.
- **`entities_suppressed` is 0** because at size S no holder reaches `entity_min_records` (G8: 0 expected entity pairs). This run exercises only the term half of A4. The entity half is covered by the engineer's unit test, not by this world.

### BLOCK reason: a concrete defect in the change as committed
**Defect.** With 3c18b1a, the A4 ablation run is classified **INVALID** instead of an expected ablation outcome.
- Now that the index really is empty, `rank_holders` never uses graph incidence. It sets `rank_method="domains"` when no entity or term incidence contributes (mycelic/inquiry/routing.py:76, 165).
- G4's ranker assertion then fails (arch_gate.py:664-668), because report.py:60 still passes `expect_ranker = hg and rs.ablation != "A1"`, which is True for A4.
- `EXPECTED_GATE_FAILURES["A4"] == ["G8"]` (ledger.py:43), so report.py:82-84 labels the run `INVALID: G4,G8`.

This is a direct consequence of the fix. The old A4 left the term index full, so the ranker ran and G4 passed. The commit changed A4's behaviour without updating the harness expectations that classify it.

**Reproducer.** `$SCR/runs/C8abl-A4fix-S1`: `arch_gate.json` `failed=['G4','G8']`, and report.md:32 shows `INVALID: G4,G8`. Independently: `python -I $SCR/rv10_checks/gate_a4.py $SCR/rv13 $SCR/runs/C8abl-A4fix-S1 $SCR/rv13_gate/coord.db`.

**Minimal fix (one line, harness-only).** In report.py:60, do not expect the ranker under A4, as is already done for A1, since the hypergraph ranker cannot run without an index: `expect_ranker=... (hg and rs.ablation not in ("A1", "A4"))`. A manual gate run should use `--no-ranker` for A4.

Do NOT add G4 to `EXPECTED_GATE_FAILURES["A4"]` instead. That would also hide a real routing-authorization failure in an A4 run; this run's replay found 0 denials, and G4's authorization replay should stay strict.

After the fix, re-run `report --finalize` on this run dir; no new run is needed. The label should read `ablation: G8`.

**Everything else in the commit is correct and needs no change:**
- the sink replacement;
- the counters;
- the removal of the `_heartbeat_stats` patch;
- the tests.

---

## Re-review: report fix 658a093 and the A4 rerun

**Reviewer:** REVIEWER-4, model `claude-opus-5-5` (independent; did not write the patch; engineer ENGINEER-3, claude-sonnet-5-5)
**Verdict: ACCEPT. No concrete defect found; the rerun meets all three conditions (G8 fails, G4 passes, report label "ablation: G8").**

Scope: commit `658a093` (parent 9577cc5). It changes bench/report.py (+7/-1), the bench/ablations.py docstring (+4/-2), and bench/tests/test_arch_gate.py (+41/-3). There is no system or scorer code in this commit.

**Note on the rerun:** its parent chain since 3c18b1a also contains `195e9ad`, a system change in mycelic/knowledge/service.py (claim-support revisions, reviewed separately). The C9 rerun therefore differs from C8abl-A4fix-S1 by that change as well, not only by the report fix.

I did not modify ng-impl, and I did not open /root/sealed_holdout or `$SCR/runs/H-*`.

**Custody:** my BLOCK review of 3c18b1a suggested re-running `report --finalize` on the C8 run dir. That advice assumed the holder stores were still present, as they were when I gated it. Once the stores were trimmed, the re-finalize degraded that run's gate, which is correctly retired as `C8abl-A4fix-S1.regated-without-stores`. The clean rerun is the right course.

### Commands and outputs
```
$ git -C /home/user/ng-impl show 658a093            (diff read in full)
$ git archive 658a093 | tar -x -C $SCR/rv15 && cd $SCR/rv15 && PYTHONDONTWRITEBYTECODE=1 python -m pytest research/mycelic_e2e/bench/tests -q -p no:cacheprovider --basetemp $SCR/rv15_bt
  193 passed, 2 skipped, 11 warnings in 13.96s        (matches the engineer's report)
$ mutation: same tree with RANKER_NOT_EXPECTED = ("A1",)   (i.e. the fix reverted)
  FAILED test_arch_gate.py::test_finalize_expects_no_hypergraph_ranker_only_where_the_ablation_removes_it[A4-domains-False]
  1 failed, 5 passed            -> the new test is load-bearing for exactly the A4 case
$ diff -rq (mycelic, bench) $SCR/rv15 $SCR/code/658a093  -> identical: the C9 run (pid 8942, cwd $SCR/code/658a093, log rev=658a093) runs this commit
$ grep expect_ranker / != "A1" across research/mycelic_e2e (excluding tests and arch_gate.py itself)
  report.py:66 is the only place that derives expect_ranker from the ablation
```

### Diff review
- **The ranker check is now skipped under A1 and A4 only.** report.py:37 adds `RANKER_NOT_EXPECTED = ("A1", "A4")`, and report.py:66 changes `rs.ablation != "A1"` to `rs.ablation not in RANKER_NOT_EXPECTED`.
  - An explicit `expect_ranker` argument still wins.
  - `expect_hypergraph=False` still disables the ranker check.
  - Only G4's ranker assertion (arch_gate.py:664-668) is affected. G4's authorization replay and its audit and budget checks still run under A4.
- **G4 is deliberately NOT added to `EXPECTED_GATE_FAILURES["A4"]`, which still equals `["G8"]`.** A real routing-authorization failure under A4 therefore still reads INVALID, as I asked.
- **The tests cover the right cases.**
  - The parametrized finalize test covers none/A1/A2/A4/A5. The "none + domains" case is the control proving the assertion still fires for the full system.
  - It asserts that the replay ran with 0 denials in every case, and checks `expect_ranker` in both the in-memory report and the written arch_gate.json.
  - `test_a4_does_not_excuse_a_g4_failure_in_the_ledger` pins `["G8"]` → `ablation_expected` and `["G4","G8"]` → `invalid`.
- The ablations.py change is a docstring only.

### The A4 rerun C9abl-A4-S1 (code `$SCR/code/658a093` = 658a093; log `rev=658a093`, "2 binding(s) replaced")
```
run_manifest.json (11:12:44): ablation_calls = {'entity_index_calls': 18243, 'term_index_calls': 18243, 'entities_suppressed': 0, 'terms_suppressed': 926517}
                              api_errors all 0; n_tasks 120
arch_gate.json (11:13:51, written BEFORE "[11:14:17] holder stores trimmed after gate"):
  valid=False  failed=['G8']  expect_ranker=False  expect_hypergraph=True
  G1 pass: 1536 live documents across 136 holder files      G2 pass: 136 distinct holder files    (stores were present at gate time)
  G4 pass: 2883/2883 questions routed; rank_method {'domains': 2883}; as-of replay denials 0; replay {'checked': 28494, 'denied': 0}
  G8 fail: entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records), terms 0/112 holders (0%) -> "term index covers 0/112 = 0.00 ... (< 0.95)"
  G3 G5 G6 G7 G9 G10 pass
report.md:35  | ablation A4 (C9abl-A4-S1) | deterministic-provider | ablation: G8 | 120 | 110 | 91.7% | [85.3%, 95.4%] | 0 | ...
score.txt: accuracy = 110/120 = 0.917, errors=0, disclosures = 0
ledger.gate_status(arch_gate.json, "A4") -> {'gate_status': 'ablation_expected', 'gate_failed': ['G8'], 'gate_unexpected_failed': [], ...}
```
All three conditions hold:
- **G8 fails:** the term index is empty, 0/112.
- **G4 passes:** the ranker assertion is skipped, and the authorization replay still checked all 28,494 routes with 0 denials.
- **The report labels the run "ablation: G8".**

`ablation_calls` > 0 confirms the sinks were live: 18,243 calls each, 926,517 term ids suppressed.

### Non-blocking notes
- **N1. Gate custody.** The holder stores are trimmed after the gate, so arch_gate.json at 11:13:51 is the authoritative gate for C9. Any later `report --finalize` on this directory would degrade it, as happened to C8. Suggestion: make `finalize` refuse to overwrite an existing arch_gate.json when the holder stores are missing.
- **N2. `tools/results_table.py --regate`.** It calls `G.check(run)` with default expectations (results_table.py:23-26), so a re-gate of an A1 or A4 run there would show the G4 ranker failure. The same was already true for A1. Use the stored arch_gate.json, or pass the ranker expectation, if that tool is used for the tables.
- **N3. The entity half of A4 is untested at size S.** `entities_suppressed` is 0 because at size S no holder reaches `entity_min_records`, so this run exercises only the term half of A4. The entity half is covered by the engineer's unit test.
- **N4. C9 is not a pure A4-fix comparison.** C9 includes the system change 195e9ad, so C9 vs C8 (110 vs 111 correct) does not compare the A4 fix alone.
