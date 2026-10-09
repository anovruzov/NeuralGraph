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
