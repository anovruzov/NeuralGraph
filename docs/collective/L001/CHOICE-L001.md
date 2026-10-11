# Latency test L001: how long does an alert take to answer on CPU, and is the answer right?

When HQ raises an alert, it forms a narrow question about it (`mycelic/collective/pushdown`). Each site's verifier
(`mycelic/collective/edge/verify.py`) judges its own records against that question with a small model, and only the
verdict comes back. HQ's commit gate then decides. No lab run has timed that path from end to end. The lab's runs
recorded pushdown's ranking on planted worlds (`docs/lab/RESULTS.md`, runs 2 and 5) and the judge's per-call latency
(runs 2, 8 and 9), but none timed an alert from its question to the gate's decision. J001 timed the judge one record
at a time.

L001 times the whole path on a real alert: the one the drafter demo raised on MSHA's public accident file. It runs
with each of three small models at every mine, and it scores the answer against the categories the records were filed
under. This rule is committed alone, before any code is written for it and before any of its data is read.

How the lab will run it (a new experiment kind, `l1`, on J001's pattern) goes in `BUILD-L001.md`, which is written
after this rule.

**Amended before any run, on 2026-10-10.** The section of that name, just before "Runs", changes rules 2 to 13, the
settings, "How it is read", the traps and "What it does not show" (K1 to K13). It was written after a review, before
any of L001's code and before any of L001's data was read. Where it disagrees with the text above it, it wins.

**Amended after run 1, on 2026-10-10, before any model ran.** K14, after K13 in the same section, changes rule 12's
guard (K9): values that the plan's own text holds are left out of the guard's value sets. Run 1 stopped at the plan
job's guard, and K14 was written after that job printed the preregistered comparators. Run 1 is recorded under "Runs"
and is not a result. The first run under K14 is.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen the drafter demo's recorded file (`demo/onboard/recorded/record-001-demo.json`, run 38036725404):
  - c1's audit raised one X alert and no S alert. The alert is SLIP OR FALL OF PERSON in week 2023-W51, and 9 mines
    sent a cell of it in the 8 weeks up to that week.
  - c1's held-out years hold 1,033 records at 10 mines, and its drafted pack has 5 specific predicates.
  - The file also holds the cells each mine sent.
- It has seen D002's and D003's results. It has seen J001's result: every model judged worse than the lexical judge,
  with balanced accuracy 0.500, 0.477 and 0.387 against the lexical judge's 0.670. It has seen RESULTS runs 1 to 9
  with their timings.
- It has not seen any of c1's records or narratives. It has not seen how many records any mine received in the
  alert's window, and no verdict on these records exists. The sandbox cannot reach MSHA; only a runner can.
- The statements under "What the code allows" come from two sources:
  - reading the code;
  - one run of the path in the sandbox on the onboard tests' synthetic export (`corpus_rows` in
    `tests/mycelic/test_collective_onboard.py`), which holds no MSHA record.

  No figure from that run is used here.
- The models may have seen MSHA's public narratives in training. The answer key is each record's filed category, not
  a label anyone checked.

## What the code allows

1. **Pushdown runs on a drafted pack as it is.**
   - The drafter copies `questions.json` from its neutral template (`mycelic/collective/onboard/data/template/`). The
     file has a `pushdown` block and one question template, `count_predicate_on_entity`, for the type
     `export_scope`, with predicates null.
   - The block's values are `min_confirming_sites` 2, `min_independent_roots` 3, `min_independent_reporters` 3,
     `freshness_days` 42 and `max_sibling_sites` 2.
   - The loader requires `questions.json`; only `mapping_openfda.json` is optional. It also checks that every egress
     type has a template with predicates null. c1's drafted pack passed the loader in the demo.
   - So no data-only addition is needed, and none is made.
2. **What a question is about.** A drafted pack has one egress type, `export_scope`, and its one id is `ALL`.
   - Every normalised row carries `ALL`, so an alert's key is `export_scope:ALL:<predicate>`.
   - An alias-only type always passes the master-data rule (`site.in_master_data`), so no mine needs master data.
3. **What a mine's verifier reads.** `retrieve` takes the mine's own records in the question's window whose structured
   values resolve to the entity.
   - Every record names `ALL`, so a mine judges every record it received in the window, newest first, up to
     `verify_max_records` (500).
   - The predicate does not narrow the records. Every question about one window reads the same records at a mine.
4. **The model can sit inside each mine.** A runtime is bound to `site:<mine>`. When its endpoint has the same
   boundary, the call runs in mode `own` (`inference/runtime.py`).
   - That mode needs no exemption, so records that are not synthetic may be judged.
   - No record goes to an endpoint outside its mine's declared boundary.
   - In the lab, every mine's endpoint is the same model server, on the runner's loopback address. The boundary is
     declared, not a separate machine.
5. **Sites answer one after another.** The orchestrator asks the routed sites one at a time, in sorted order. Each
   call runs on a thread that is joined against `deadline_seconds`, which lies in (0, 3600] with a default of 600
   (`pushdown/orchestrator.py`; ARCHITECTURE 15.3).
   - INTEGRATION.md (G6, "Further decisions and deviations") records why. The first version started every site at
     once, so the sites' lines landed in HQ's shared logs in thread order, and a determinism test failed.
   - One site at a time keeps a fixed order, and a hung site delays the others by at most the deadline.
   - So the product's time to answer an alert is the sum of the sites' times plus HQ's own steps.
6. **The alert can be asked when HQ would have asked it.**
   - A detection run at a date D sees only bundles dated on or before D, and only weeks closed by D
     (`detect/detectors.py`, "No look-ahead").
   - A run on the alert's available date stores the candidate with its snapshot at the alert's week. The available
     date is the closing date of that week (`baselines.closing_date`), as the audit dates its alerts.
   - `verify_stored(run_id, key)` then asks at the candidate's own `as_of`, over the 8 weeks that end at the alert's
     week. Eight is the pack's `window_weeks`.
7. **Real data is feasible, so there is no synthetic fallback.** Nothing above needs a change to pinned code, so E2's
   planted world is not used and no planted truth is used.
   - If the pipeline raises no X or S alert for c1 on the day of the run, L001 stops in the plan job, before any
     model runs.
   - A new choice file then decides what comes next.

## The rule

1. **Data and pipeline: the demo's, unchanged.**
   - **The file.** MSHA's accident file is fetched on the runner by `tools/onboard/fetch_msha.py` and split by D002's
     settings (`docs/collective/onboard/D002-settings.json`, sha256 `8bb9ba6ab9c8…`). The demo read `Accidents.zip`,
     52,269,752 bytes, sha256 `62d0c861a5c3…`. If the file's sha256 differs on the day, both are reported, and L001
     is then not on the demo's file.
   - **The operator** is c1, D002's audit company (`audit_company` in the settings).
   - **The pack.** c1's pack is drafted from its training years, 2015-01-01 to 2021-12-31, by `draft_export`. It then
     goes through the loader and the privacy floor, as in the demo's step (b).
   - **The records.** c1's held-out years, 2022-01-01 to 2024-12-31, are normalised. Each mine id is replaced by its
     label, m01 onwards in the order of the ids, as in the demo's step (d).
   - **The pipeline** is the audit's (`evaluate.baselines.run_pipeline`) over those records, with enterprise `pilot`
     and tie salt `pilot`. Each mine is a site; the sites extract lexically and send k-suppressed weekly cells to HQ.
   - **Checks.** The plan job records the drafted pack's config hash, the alert list and the audit's counts. Every
     unit must reproduce them.
   - When the file is the demo's, the audit must also give the demo's figures: 1,033 records, 10 mines, 139 weeks,
     one X alert and no S alert. Otherwise the plan refuses, because the code has drifted.
2. **Alerts: every X and S alert of c1's audit in the evaluated weeks.**
   - At most 3 are asked, in order of available date, then channel, then key. From the demo, one is expected: X,
     SLIP OR FALL OF PERSON, 2023-W51.
   - Each alert's candidate comes from HQ's detection run on the alert's available date, in the alert's channel, with
     tie salt `pilot`. The run is saved in HQ's store (`save_run`).
   - That run must list the same alert in the same week. Otherwise the plan refuses.
   - A key can alert again after its cooldown. Its stored candidate keeps the snapshot of its first alert, so L001
     asks the later alert with `verify_stored(run_id, key, as_of=<its available date>)`. The window then starts at the
     first alert's window start (`question_window`); the stored path allows no other. The demo's single alert does not
     raise this case.
3. **Questions: the alert's own question, and control questions.**
   - **The alert question** is the product's: `Orchestrator.verify_stored(run_id, key)`, at the candidate's own
     `as_of`.
   - **Control questions** are not alerts.
     - For each alert, L001 also asks about every other specific predicate of the drafted pack (not
       `other_category`), in sorted id order, and at most 4.
     - Each is about the same entity, in the same window, at the same `as_of` and in the same channel.
     - Each goes through the documented test path: `constructed_candidate`, with `window_start` set to the alert
       question's window start, then `verify_candidate`.
     - c1's drafted pack has 5 specific predicates, so 4 control questions are expected.
   - **Why controls.** The alert question asks the mines where the alert's counts rose, so nearly all of them are
     expected to confirm.
     - Those answers alone cannot tell a judge that reads the records from one that confirms everything.
     - The controls ask the same mines about the same records, for categories HQ did not alert on. So the answers
       hold refutes as well as confirms.
   - **Fresh state for every question.** Every question, alert or control, gets a fresh HQ store and fresh mine
     stores, rebuilt by the same pipeline.
     - So no budget, stored verdict, cache or late thread carries from one question to the next.
     - Every question's path is a cold one, as an alert's first question is.
4. **The mines: the product's verifier, with the filed category hidden.**
   - **The verifier.** Each routed mine answers with `SiteVerifier.answer`, unchanged. That covers the Boundary, the
     budgets, master data, retrieval, the judge on each record, `decide`, the stored verdict and the bucketed body
     that crosses back. The secret is seeded (`demo_seed` 1), as E2 seeds its verifiers' secrets.
   - **The filed category is hidden from every judge but the key judge.** The verifiers' stores hold a copy of each
     mine's records with `codes` emptied; everything else is as ingested. HQ's detection reads the cells of the
     unchanged records, so the alert is the demo's.
     - Why: a record's codes go into the judge's payload, and in a drafted pack the code is the filed category,
       which is the answer key.
     - The lexical judge maps codes to predicates through the pack, so with the codes in its payload it would read
       the key itself. A model sees only an opaque code. J001 hid the codes too (its trap 4).
     - The plan job checks the copy: at every routed mine and for every question, it must retrieve the same records,
       in the same order, as the unchanged store does.
   - **The model.** Each mine has a runtime bound to `site:<mine>`.
     - Its one endpoint is the lab's pinned model server, at the same boundary.
     - The route sends `judge_record` to that endpoint, with no escalation.
     - The data label is `public`.
     - Each HTTP call has a deadline of 600 s and 1 retry, as J001's endpoints have.
     - The verifier's clock is set to the question's `as_of`, which is the day the budgets count.
   - **Delivery** is the orchestrator's own: one mine after another, in sorted order, with `deadline_seconds` 3600,
     the most it allows.
     - Why not the default of 600 s: in the lab every mine shares one model server. A mine past its deadline leaves
       a late thread that keeps calling that server while the next mine runs, which slows the next mine. The
       product's mines have their own servers. A deadline of 3600 s keeps the calls one at a time.
     - A mine still running at 3600 s is a timeout, as in the product. When `verify_stored` returns, `join_late`
       waits up to the unit's remaining budget. `collect_late` then takes in the late verdicts at the question's
       `as_of`, and the gate decides again.
5. **The judges.** Every judge answers through the same path: the same questions, routes, windows, records and gate.
   - **Models:** a-0p5b, a-1p5b and a-4b (`lab/models.json`), one run each. Every call is sent at temperature 0, and
     the server's seed is fixed, so a second run would measure server noise, not the model (J001, rule 4).
   - **The lexical judge:** `SiteVerifier` with no runtime, which judges with `lexical_judge`. It is the verifier's
     judge when no model runs, and it reads the copy with the codes hidden. Its terms are the drafted pack's, learned
     from c1's training years.
   - **The key judge (the answer key):** a fake runtime at each mine. On a record it answers `mentions_entity` yes,
     and it answers `describes_predicate` yes exactly when the record's filed category maps to the question's
     predicate in the drafted pack's `codes.json`.
     - It reads the unchanged stores, because it needs the codes.
     - Its site verdicts and gate statuses are the truth that L001 scores against (rule 7).
   - **The record-blind control** decides nothing. It is a fake runtime that never reads a record.
     - For the alert question it answers yes and yes on every record, and for a control question yes and no.
     - So a mine with any record confirms the alert and refutes every control. It is what the alert alone tells HQ.
   - **The predicate-only bound** also decides nothing. It is the most that a judge which knows only the question
     could score on these site answers (rule 8). It is fitted to the answers, so it is optimistic, and it has no
     interval.
   - **When they run.** The key judge, the lexical judge and the control run in the plan job, before any model runs.
     Their verdicts, gate results and scores are preregistered.
   - Each unit also runs the lexical path again on its own runner (rule 6). It must reproduce the preregistered
     verdicts.
6. **What is timed.** Wall-clock time on the runner, by `time.perf_counter`, in the unit's own process. HQ and the
   mines run in that one process, with no network between them.
   - **Time to answer.** It runs from the call to `verify_stored` (or `verify_candidate`) to the call's return. The
     return is the gate's first decision.
   - If a mine timed out, the time to the gate's decision after the late verdicts are taken in is reported too.
   - **Stages.** A timing wrapper around each mine's handler records when the orchestrator calls the handler and when
     it returns. The handler is otherwise `SiteVerifier.answer`, unchanged.
     - *Question build* runs from the call's start to the first mine's start. It covers the window, the question
       body, the routes and the stored question.
     - *Each mine* runs from its handler's start to its return. It covers the Boundary, retrieval, every judge call,
       the verdict rules, the stored verdict and the send.
     - *Model calls* are part of each mine's time: the sum of the `latency_ms` of that mine's judge calls in its
       ledger, and the number of those calls.
     - *Between mines* is each gap from one mine's return to the next mine's start. It covers the verdict's intake at
       HQ and the start of the next thread.
     - *Gate* runs from the last mine's return to the call's return. It covers the last verdict's intake, the gate
       and the stored conclusion.
   - **Counts.** For each question: the records judged (the calls to `Runtime.run`), the HTTP attempts (the ledger's
     rows, repairs included) and the records at each mine.
   - **Per model, over all alerts:** the median and the 95th percentile (`stats.percentile`) of these:
     - the time to answer;
     - each stage's time;
     - a mine's answer time;
     - a judge call's latency;
     - the model calls per alert.

     With one alert, the median and the 95th percentile of the time to answer are both that one time, and they are
     reported as such. The same figures over all questions, alerts and controls together, are reported apart and
     labelled so.
   - **Beside these, derived and deciding nothing:**
     - *At once:* the question build, plus the slowest mine, plus the gate. This is what asking every mine at once
       would take if each mine had a server like this runner's. It is not run, because on one runner the mines would
       share one server.
     - *The default deadline:* the time and the gate's decision had the deadline been 600 s. Each mine over 600 s
       counts as 600 s and as an HQ timeout record, and the pinned `gate.evaluate` decides on the rest. It is not run.
     - *No model:* the lexical path's time on the same runner.
   - **Not timed:** the detection that raised the alert, the model's loading, the server's start and warm-up, and any
     network or message transport between HQ and the mines.
7. **Correctness.**
   - **A site answer** is one mine's verdict on one question.
     - It is *scored* when the key judge's verdict there is `confirm` or `refute`.
     - The key judge answers yes or no on every record, so its only `unknown` is at a mine with no record in the
       window. There every judge answers `unknown` (`no_records`).
   - **Correct.** A judge's site answer is correct when it equals the key judge's: `confirm` where the key confirms,
     and `refute` where it refutes.
     - `unknown`, for any reason (`unclear`, `degraded`, a timeout or an error), is never correct.
     - The support bucket is not compared; rule 10 reports it.
   - **Measures.** *Sensitivity* is the share of the key's confirms that the judge confirmed. *Specificity* is the
     share of the key's refutes that the judge refuted. *Balanced accuracy* is their mean. Accuracy and the unknown
     share are reported too.
   - **The gate's decision** on a question is correct when two statuses are equal:
     - the status the gate gives on the judge's verdicts;
     - the status the same pinned gate (`gate.evaluate`) gives on the key judge's verdicts, for the same question,
       routes, window and `as_of`.

     The reasons are not compared. The final decision is used, after any late verdicts. Also reported: whether both
     decisions are `supported`, or both are not.
   - **Transport failures.** A judge call can end in a transport failure: a timeout, a network or HTTP error, or a
     size limit, as `e1_extract.failure_class` sorts them.
     - A site answer with any such call is left out of every judge's scores for that model, and it is counted.
     - A model failure (no valid reply after the repair) belongs to the product, and it counts as it falls.
     - If more than 5% of a model's scored site answers are left out, its headline is withheld.
8. **Intervals.**
   - **The bootstrap** is a percentile bootstrap over mines, with B 10,000 and `random.Random("l1:1")`.
     - A mine's scored site answers are drawn together, because a mine's questions read the same records.
     - Every judge is scored on the same draws.
   - **In each draw,** sensitivity, specificity and balanced accuracy are computed over the drawn answers.
     - A draw with no key confirm, or no key refute, gives no balanced accuracy. Such draws are counted and left out.
     - If they are more than 5% of the draws, the interval is withheld.
   - **The interval** is the 2.5th and the 97.5th percentile (`stats.percentile`).
   - **The predicate-only bound.** Let P and N be the key's confirms and refutes among the scored site answers, and
     let P_q and N_q be those of question q. The bound is half the sum, over the questions, of max(P_q / P, N_q / N):
     each question is answered the one way, confirm everywhere or refute everywhere, that scores more. It has no
     interval.
9. **The headline, fixed now: correctness against the lexical judge.**
   - For each model, take its balanced accuracy minus the lexical judge's, over the same scored site answers. Its
     paired interval comes from the same draws.
   - The model **answers better than the lexical judge** if the interval's lower end is above 0. It **answers worse**
     if the interval's upper end is below 0. Otherwise the two are **not told apart**.
   - Each of the three models is compared once, with no correction for three comparisons.
   - A model is scored only when every one of its question units finished (rule 11). Otherwise its finished questions
     are a partial reading and decide nothing.
   - **Latency is a measurement, not a test.** No time passes or fails, and no threshold is set.
10. **Reported beside the headline, deciding nothing.**
    - Each judge's sensitivity, specificity, balanced accuracy, accuracy and unknown share, with their intervals. The
      record-blind control's figures and the predicate-only bound sit beside them.
    - Per question:
      - each judge's site verdicts beside the key's;
      - their support buckets;
      - the gate's status beside the key's.
    - Per judge, how many gate decisions were correct, out of the questions.
    - The reasons behind each `unknown`: `unclear`, `degraded`, `no_records`, timeout or error.
    - **Per record,** with J001's measures, from the replies:
      - A judged record is a positive when it was filed under the question's predicate, and a negative otherwise.
      - For each model and for the lexical judge: sensitivity, specificity, balanced accuracy, the unknown share and
        the answer pairs, with a bootstrap over records (seed `l1:1:records`).
      - The models' replies are taken by a wrapper around each mine's `Runtime.run`, which passes the call through
        unchanged and keeps the reply. The lexical judge's replies are computed from the same payloads.
    - For each counted confirm: the share of its confirming records, as its mine's own `resolve` lists them, that
      were filed under the predicate.
    - The narrative overlap of every artifact that crossed between HQ and the mines (the questions and the verdicts),
      scanned as E2 scans pushdown's artifacts (`leakage.scan`). It is expected to be 0.
    - Every latency figure of rule 6, with the runner's CPU model.
11. **Units and limits** (sized in "Why the units are this size").
    - One unit per model and question. 5 questions are expected (1 alert and 4 controls), so 15 units.
    - Each unit has 300 minutes and runs alone in its shard. A shard job has 330 minutes: the unit's 300 plus the
      lab's 25-minute overhead, within the shard's capacity of 305. `max_parallel` is 15.
    - A unit stops at its budget. A path not finished by then is reported as not finished, with its elapsed time as a
      lower bound, and that model gets no verdict on its question.
    - Each unit gets the file itself, by fetching it or from a cache the plan job saved, never from an artifact. It
      checks the file's sha256 against the plan's, rebuilds the pipeline and checks every preregistered hash before
      the model runs.
12. **What is written, and the guard.**
    - Nothing a run writes holds a record id, a mine id, an operator's or controller's name or id, a narrative or a
      drafted term.
      - Mines are m01 onwards, and the operator is c1.
      - In the per-record results, a record is its index in its mine's retrieved list.
    - The drafted pack goes into no artifact; only its hashes do.
    - Before upload, every file that the plan job or a unit writes is scanned with D002's last guard and backstop
      (`onboard.report`).
      - The scan looks for every record id, mine id and refused value of c1 to c5.
      - It also looks for any 8-word run of c1's narratives.
      - A hit withholds the file, keeping only the hit counts, and fails the job.
13. **The first run is the result.**
    - A run that fails before any model judges, for an infrastructure reason, may run again unchanged.
    - A model left incomplete gets no headline from this run, and judging it again needs a new choice file.
    - Any change to a setting needs a new choice file.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Models | a-0p5b, a-1p5b, a-4b; one run each |
| Operator, pack, records | c1; drafted from 2015-01-01 to 2021-12-31; held out 2022-01-01 to 2024-12-31; D002's settings |
| Alerts asked | every X and S alert of c1's audit in the evaluated weeks, at most 3 |
| Control questions | the drafted pack's other specific predicates, sorted, at most 4 per alert |
| Codes in the judges' payloads | empty (the key judge alone reads them) |
| Orchestrator deadline | 3600 s per mine; sites one after another, in sorted order |
| Endpoint | the lab's pinned model server on loopback, at `site:<mine>`; HTTP deadline 600 s, 1 retry; no escalation |
| Verifier secret | `demo_seed` 1 |
| Bootstrap | over mines, B 10,000, seed `l1:1`; over records, seed `l1:1:records` |
| Units | one per model and question; 300 minutes; one per shard; `job_minutes` 330; `max_parallel` 15 |

## Why the units are this size

- **The bound.** A question's model calls are the records that its routed mines received in the window.
  - At most, that is c1's whole held-out set: 1,033 records (the demo).
  - The real count is smaller. The plan job counts it before any model runs.
- **The recorded per-call latency.** J001's judge calls on real complaint narratives (RESULTS run 9) are the closest
  record. The slowest median of each model, across CPUs, is:
  - a-0p5b, 1.5 s;
  - a-1p5b, 3.2 s;
  - a-4b, 9.0 s.

  All three are on an AMD EPYC 7763. On generated text the judge calls were faster: 862 ms, 868 ms and 3,435 ms in
  run 2, and 4.0 s for a-4b in run 8. MSHA's narratives have never been judged.
- **At the bound and those medians,** 1,033 calls take:
  - a-0p5b: 1,033 x 1.5 s = 1,549.5 s (25.8 min);
  - a-1p5b: 1,033 x 3.2 s = 3,305.6 s (55.1 min);
  - a-4b: 1,033 x 9.0 s = 9,297 s (155.0 min).

  A unit's 300 minutes is 1.9 times the slowest of these.
- **One question per shard.** At the bound, a-4b's five questions would take 5 x 155.0 = 775 minutes, more than one
  shard's 305.
- **The deadline per mine.** At a-4b's median of 9.0 s, 3,600 s holds 400 calls, fewer than `verify_max_records`
  (500). A mine with more records than that in the window times out with a-4b, and rule 4 applies.
- **The tail.** RESULTS records no 95th percentile for judge calls. For extraction calls with a-4b in R002, the 95th
  percentile was 177 s against a median of 25.8 s (CHOICE-R002, run 1). Only the looseness of the bound covers a slow
  tail.
- If a unit runs out anyway, rule 11 applies.

## How it is read

- **The time.** One alert gives one time per model, on one runner's CPU.
  - It says how long this path took on a shared 4-vCPU runner with one model server, not on a site's hardware.
  - Models can be compared only on the same CPU model.
  - The stage times show where the time goes. The number of judge calls at each mine says why a mine took as long as
    it did.
- **The control questions' times** show the same path, for other categories, on the same records. Their spread is the
  spread of one window's questions, not of alerts.
- **A model that answers better than the lexical judge** gives HQ a better site answer on these records than the
  drafted pack's terms do. That supports a small model inside the mine on this kind of text. It does not show the same
  on another field or another company.
- **A model not told apart from the lexical judge** gives no evidence that it adds anything to the answer here. The
  lexical judge makes no model call.
- **A model that answers worse** is a real negative for the small site model, on the answer the product actually
  returns.
- **0.5 is no judging at all.** A judge that gives the same answer at every mine has a balanced accuracy of 0.5.
  - The record-blind control shows what the alert alone gives.
  - The predicate-only bound shows the most that knowing only the question could give.
- **The gate's decisions are few:** 5 questions per model are expected. Their agreement with the key is a count, not
  an estimate.
- **Ten mines give wide intervals.** Small differences between the models cannot be read.

## Traps

1. **Filed categories are not truth.** A narrative can describe a category it was not filed under, and the reverse.
   No judge can reach 1.0, and the key costs every judge, the lexical one included.
2. **Pooling.** A mine confirms when any one of its records is judged yes and yes.
   - A judge's false confirms add up over a mine's records. So a mine with many records is easy to confirm wrongly,
     while a true confirm needs only one record.
   - In J001, a-0p5b said the record described the component on 145 of 150 negatives. At a mine with several records
     such a judge confirms almost always.
   - For every judge, sensitivity rises and specificity falls as a mine's records grow.
3. **Unclear is not refute.** `decide` refutes only when fewer than half the replies are unclear and none describes
   the predicate. In J001, a-4b answered unclear on 94 of 150 negatives. A mine with such replies answers `unknown`,
   which is never correct.
4. **The alert's own mines.** The alert question asks where the counts rose, so most of its answers are expected to
   confirm. The control questions are there to supply refutes (rule 3).
   - X also counts text-only cells. So a contributing mine can be one whose narratives matched the drafted terms
     while none of its records was filed under the category.
   - At such a mine the key refutes, and the key's gate decision is then contested.
5. **The entity is the whole export.** The question names `ALL` ("Whole export (every record)"), which is in every
   record's structured entities. A model that answers no or unclear about it gives `unknown`, whatever it says about
   the category (J001, trap 5).
6. **The codes are hidden** (rule 4). In the product they go into the payload. So the timed prompt lacks a short list
   of codes.
7. **One server for every mine.** The product's mines would each have their own server. Sites answer one after
   another, as the product asks them, so the shared server serves one mine at a time unless a mine times out (rule 4).
   The time if every mine were asked at once is derived, not run.
8. **One process, no network.** The timed path calls each mine's handler in HQ's own process. The product sends
   questions and verdicts between machines, and that transport is not timed.
9. **The runner's CPU varies between shards,** and each question runs on a shard of its own.
10. **Training data.** The models may have seen these public narratives. That would help them, not the lexical judge.
11. **The drafted terms were learned from c1's own training years,** for the very categories the questions ask about.
    So the lexical judge is a reader fitted to this company's text, not a generic lexicon.

## What it does not show

- How long other alerts take, at other companies or in other fields. This is one alert, one operator and one public
  file.
- How fast a site's own hardware is. These are shared GitHub runners whose CPU model varies.
- How the path performs when every site is asked at once. The product does not ask that way, and that time is only
  derived.
- How the path performs with the product's default deadline of 600 s. That too is only derived.
- How long the transport between machines, the detection or the model's loading takes.
- Whether the alert is right, early or useful. There are no outcomes, and the demo's review list is unjudged.
- How the path does on a company's own records. These are public records written to a regulator.
- How a judge does when it sees the codes, as the product's payload has them.
- Whether a larger model or a hosted model would answer better.
- Anything about a synthetic world, since none is run.

## Amended before any run, 2026-10-10

No L001 code exists, nothing has been fetched for L001, and no judge has run. A review of this rule against the code
found two gaps that would let the headline read wrongly, and ten smaller ones. The changes are K1 to K13, numbered
apart from D001's A1 to A14, D002's B1 and B2 and D003's E1 to E18. Each says what was, what is now, and why. Where
the text above disagrees with this section, this section wins.

The declaration still holds. Since the rule was committed, its author has read the code and the lab files that each
item cites, and the demo's recorded file again, which the declaration already lists. No figure here comes from L001's
data, which no one has read.

K14 was added later, after run 1 stopped at the plan job's guard and before any model ran. Its heading and its first
bullet say what its author had seen by then.

### K1. Each question goes to its own key's mines (rules 3 and 5; "How it is read"; trap 4)

- **Was:**
  - Rule 3 said the control questions "ask the same mines about the same records" as the alert question, so their
    answers hold refutes as well as confirms.
  - Rule 5 said the record-blind control "is what the alert alone tells HQ". "How it is read" said it "shows what the
    alert alone gives".
  - Trap 4 said the control questions are there to supply refutes.
- **Why that is wrong:**
  - The orchestrator routes every question, a control question included, from that question's own key (`_routes` in
    `pushdown/orchestrator.py`, which `_verify` calls for every question). `constructed_candidate` takes no routes,
    and no pinned function sends a question to another key's mines.
  - The contributing mines are those with a cell of the question's key in the window, visible at `as_of`, in the run
    channel's cell channels. For X these are `codes` and `text_only` (ARCHITECTURE 15.3).
  - A mine sends a cell for every key with a record in the week, a suppressed count included (`build_cells` in
    `edge/site.py`). So one record makes a mine contributing.
  - The siblings are the first `max_sibling_sites` (2) other mines by the entity's lower-bound cell volume, over the
    span from `baseline_weeks` before the window to its end. The entity is `ALL`, which every cell names, so this is
    each mine's volume of all its cells there. A sibling need not have a record in the window.
  - So a control question about a predicate p goes mostly to mines that filed a record under p in the window, or
    whose narratives matched p's drafted terms. There the key mostly confirms. The control questions do not ask the
    alert's mines.
  - At a mine that two questions both reach, the records read are the same ("What the code allows", item 3). What
    differs between the questions is which mines are asked.
  - The record-blind control confirms the alert question and refutes every control question, at each mine with
    records. At a control question's contributing mines it is wrong wherever the key confirms. It is not what the
    alert alone tells HQ.
- **Now:**
  - **Rule 3's reason for the controls** reads: each question, alert or control, goes to the mines with a `codes` or
    `text_only` cell of its own key in the window, and to at most 2 siblings chosen by cell volume. The key's refutes
    then come only from siblings with records in the window and from mines that sent only `text_only` cells of the
    key (K2). The control questions add site answers, confirms and refutes alike. They are not asked of the alert's
    mines.
  - **The plan job counts**, before any model runs and for each question:
    - the routed mines by role, contributing or sibling;
    - the contributing mines by the channels of their cells of the key (K2's strata);
    - the siblings with a record in the window, and those without.

    These counts and every question's routes are preregistered. Every judge's path must give each question the same
    id and the same routes, or the plan job or the unit refuses.
  - **The record-blind control** stays, and it decides nothing. It is a judge that answers by which question it was
    asked, never by the record: the alert question yes, every control question no. It answers one way per question,
    so it cannot score above the predicate-only bound. What HQ's routes alone give is K2's route-role baseline.
  - Trap 4 is replaced (see "Traps, as amended").

### K2. The route-role baseline, three strata, and the lexical judge by construction (rules 5, 7, 8 and 10)

- **Was:** The lexical judge was the only comparator. The record-blind control and the predicate-only bound sat beside
  it, and the site answers were scored as one pool.
- **Why that fails:** With K1's routing, HQ's cells fix both the key and the lexical judge at most mines, before any
  record is read.
  - **The key.** Every record in the window names `ALL`, so a mine retrieves all of them, newest first, up to
    `verify_max_records` (500). The key judge confirms at a mine when one of them was filed under p. A record filed
    under p in the window is also what makes the mine send a `codes` cell of the key there. So the key confirms where
    a mine sent a `codes` cell of the key, and refutes at every other mine with records.
  - **The lexical judge, with the codes hidden** (`lexical_judge` in `edge/verify.py`). It answers `mentions_entity`
    yes on every record, because the structured `ALL` always resolves (`codes_channel`). With no codes, `pair` joins
    every text predicate the extractor affirms with `ALL` (`edge/extract.py`). So it confirms at a mine when one
    narrative affirms p under the drafted terms.
  - **The `text_only` cells** come from the same extraction at the mine's ingest: C_text less C_codes, with negated
    claims dropped (`pair`). The mine extracts from the whole narrative, and the judge reads it cut at
    `max_input_chars`. That cut is the only difference.
  - **So, at each kind of mine:**
    - A mine that sent only `text_only` cells of the key holds no record filed under p and a narrative that affirms
      p. The key refutes and the lexical judge confirms: wrong by construction.
    - A sibling with records sent no cell of the key, so it holds neither. Both refute: right by construction.
    - At a mine that sent a `codes` cell, the key confirms. The lexical judge confirms too when the mine also sent a
      `text_only` cell. Otherwise it confirms only when a record filed under p also affirms p in its text, which no
      cell shows.

    Each "by construction" holds up to three exceptions: the narrative's cut, the cap of 500 records, and records
    outside the pack's languages.
  - **A reader HQ already has.** It confirms at a contributing mine and refutes at a sibling, and reads no record. It
    is right at every key confirm, and it agrees with the lexical judge at every key refute, with the same exceptions.
    So it is no worse than the lexical judge on sensitivity, specificity or balanced accuracy, up to those
    exceptions.
  - **What follows:**
    - The lexical judge's specificity is set by routing, up to those exceptions: the scored siblings, over the scored
      siblings plus the mines that sent only `text_only` cells.
    - So the sign of a model's difference from the lexical judge can follow the mix of routes, not reading.
    - A model could be "better than the lexical judge" and still be below a reader that reads nothing.
    - Neither the record-blind control nor the predicate-only bound uses the route, so neither shows this.
  - **The key is HQ's own cells.** A reader of HQ's `codes`-channel cells would equal the key. So the correctness half
    of L001 measures whether a reader recovers the filed category from the narratives alone, with the codes hidden. It
    does not measure whether HQ gains an answer it lacks.
- **Now:**
  - **The route-role baseline** joins rule 5.
    - It is a fake runtime at each mine that never reads a record. At a mine routed as contributing for the question,
      it answers yes and yes on every record. At a sibling, it answers yes and no.
    - It runs through the same path as every judge, in the plan job, on its own fresh stores (K4), before any model
      runs. Its verdicts, gate results and scores are preregistered.
    - Like every judge, it answers `unknown` at a mine with no record.
    - It decides the headline, beside the lexical judge (K3).
  - **Three strata,** fixed by HQ's cells before any model runs (K1's counts). For each question, a routed mine is one
    of these:
    - a *codes contributor*: contributing, with a `codes` cell of the key in the window, whatever `text_only` cells it
      also sent;
    - a *text-only contributor*: contributing, with only `text_only` cells of the key in the window;
    - a *sibling*.
  - **Rule 10 adds, for every judge** (each model, the lexical judge, the key, the record-blind control and the
    route-role baseline), its site answers in each stratum:
    - the counts of `confirm`, `refute` and `unknown`;
    - among the scored answers, how many equal the key's;
    - sensitivity over the codes contributors, and specificity over the text-only contributors and over the siblings
      apart, each with rule 8's interval.

    These decide nothing.
  - **The plan job checks the construction** before any model runs, and preregisters these counts:
    - mines where the key does not confirm at a codes contributor, or does not refute at a text-only contributor or
      at a sibling with records. None is expected; a record filed under p beyond the 500 newest would make one;
    - mines where the lexical judge does not confirm at a text-only contributor, or does not refute at a sibling with
      records. None is expected, except through the narrative's cut or a record outside the pack's languages;
    - the lexical judge's confirms and refutes at codes contributors that sent no `text_only` cell.

    A count above zero is reported, not refused. The strata stay as HQ's cells define them.
  - "How it is read, as amended" and "Traps, as amended" below say what this means.

### K3. The headline: better than both the lexical judge and the route-role baseline (rule 9; "How it is read")

- **Was:** A model answered better when the interval of its balanced accuracy minus the lexical judge's was above 0.
  "How it is read" said that this supports a small model inside the mine on this kind of text.
- **Why:** K2. A model can beat the lexical judge through the mix of routes alone, and still sit below a reader that
  reads nothing and that HQ already has. "Supports a small model inside the mine" would then be wrong.
- **Now:**
  - For each model, two paired differences, over the same scored site answers, with intervals from the same draws
    (rule 8):
    - *D_lex*: its balanced accuracy minus the lexical judge's;
    - *D_route*: its balanced accuracy minus the route-role baseline's.
  - **The headline, fixed now:**
    - **answers better**: the lower ends of both intervals are above 0;
    - **better than the lexical judge only**: the lower end of D_lex's interval is above 0, and that of D_route's is
      not. This is read as adding nothing over HQ's cells;
    - **answers worse**: the upper end of D_lex's interval is below 0;
    - **not told apart**: any other case;
    - **no verdict**: an interval is withheld (K7), more than 5% of the model's scored site answers are left out
      (rule 7), or one of its units did not finish (rule 9).
  - Each model is compared once with each of the two, with no correction for the six comparisons.
  - Rule 10 shows both comparators, the record-blind control and the predicate-only bound beside each model.

### K4. Fresh stores for every question and judge, and a check on the timed calls (rules 3 and 5)

- **Was:** Every question got a fresh HQ store and fresh mine stores. Each unit also ran the lexical path again on its
  own runner.
- **Why that fails:** The stores were fresh for each question, not for each judge.
  - A mine that has answered a question re-sends its stored verdict, with no budget used and no model call (`_answer`
    in `edge/verify.py`, `store.verdict_for_question`).
  - HQ reuses a stored question with its stored routes (`_verify` in `pushdown/orchestrator.py`).
  - The question id does not depend on the judge.
  - So if a unit's lexical rerun and its timed model path shared stores, the model path would make no model call,
    take almost no time and return the lexical judge's verdicts. The same holds in the plan job, for the key judge,
    the lexical judge and the controls.
- **Now:**
  - **Fresh stores for each path.** A path is one judge on one question. Each gets its own fresh HQ store and fresh
    mine stores, rebuilt by the same pipeline.
    - In the plan job: the key judge, the lexical judge, the record-blind control and the route-role baseline.
    - In each unit: the timed model path and the lexical rerun. The timed model path runs on stores that no other
      judge has touched, and the lexical rerun runs after it.
  - **The plan job preregisters** each routed mine's retrieved records for each question: their count, read with the
    codes hidden. Rule 4 already checks that they are the unchanged store's records.
  - **The unit check.** On the timed path, at every routed mine:
    - the calls to `Runtime.run`, counted by rule 10's wrapper, must equal the mine's preregistered count of retrieved
      records;
    - fewer are allowed only when the verifier stopped early: its verdict is `degraded`, or its ledger shows the
      breaker's stop after `BREAKER_AFTER` consecutive failures that say the server is down;
    - at a mine that timed out (K6), the calls are counted when the unit ends, and fewer are allowed if its late
      thread was still running then;
    - every call must have at least one `judge_record` row in the mine's ledger.

    A failed check fails the unit, naming the mine and the check. That model's question is then not finished
    (rule 9).

### K5. The warm-up and the shared prompt cache (rule 6, "Not timed"; trap 7; a new trap)

- **Was:** The server's start and warm-up were not timed. The rule did not say what the warm-up sends.
- **Why:**
  - The lab's server keeps its prompt cache. It runs with `--cache-ram` (`server_argv` in `lab/server.py`), and the
    client never sets `cache_prompt`, so the server's default prompt caching applies (`inference/client.py`).
  - The judge's prompt is the system text with the task's instructions and schema, then the payload
    (`render_messages` in `inference/tasks.py`). In the payload's canonical key order, the question part comes before
    the record.
  - So on the one server, each mine after the first starts its first call with a cached prefix that holds the whole
    question part. In the product, each mine's own server would evaluate that prefix once. The lab saves one prefix
    evaluation for each mine after the first.
  - A warm-up that sent one of the unit's own payloads would make the first timed call a full cache hit.
- **Now:**
  - **The warm-up** sends only judge payloads built from no MSHA record and about no question that a unit asks: a
    generated record, as J001's warm-up uses (`judge_example` in `lab/warmup.py`), with a question that none of the
    five asks. Its fit check's worst case is a generated narrative, cut at the drafted pack's `max_input_chars`.
  - **Rule 6 adds, for each mine,** the latency of its first judge call beside the median of its calls. So the size of
    the effect can be read.
  - **Not recorded:** the server's count of cached tokens. The client keeps only the reply's `prompt_tokens` and
    `completion_tokens` (`inference/client.py`), and no pinned code is changed for it.
  - **A new trap,** trap 12, says that the question part is cached across mines on the one server.

### K6. One reading of a timeout (rule 4, "Delivery"; rule 6, the stages; rule 7)

- **Was:**
  - Rule 7 said a timeout is never correct. Rule 4 took the late verdicts in, and rule 7 used "the final decision ...
    after any late verdicts".
  - The Gate stage ran "from the last mine's return".
  - Rule 4 said that a deadline of 3600 s keeps the calls one at a time.
- **Why:**
  - It was unclear whether a mine that timed out is scored as the timeout or as its late verdict.
  - The Gate stage is undefined when the last mine timed out.
  - By "Why the units are this size", a-4b times out at a mine with more than about 400 records. Its late thread then
    keeps calling the shared server while the next mines are timed. The product's mines would not share a server.
- **Now:**
  - **A site answer is scored as the first decision saw it,** when `verify_stored` or `verify_candidate` returned. A
    mine that timed out is `unknown` there (HQ's `timeout` record), which is never correct. Its late verdict is
    reported beside it, and it decides nothing.
  - **The gate's decision** that is compared with the key's is that first decision. The decision after `collect_late`
    is reported beside it, and it decides nothing.
  - **A mine that timed out ends** at its handler's start plus the deadline (3600 s), when the orchestrator stopped
    waiting for it. *Between mines* after it runs from that end to the next mine's start. *Gate* runs from the last
    mine's end, so defined, to the call's return.
  - **Contention is flagged.** A mine whose handler ran while a late thread of the same question was still alive is
    flagged *contended*. Its times, and its question's time to answer, are reported apart, and any figure that pools
    them with others is labelled so.
  - **Rule 4's sentence** now reads: a deadline of 3600 s keeps the calls one at a time until a mine times out.

### K7. A withheld interval gives no verdict, and the plan job checks the draws first (rules 8 and 9)

- **Was:** When more than 5% of the draws had no key confirm or no key refute, the interval was withheld. The rule did
  not say what the headline then was.
- **Why:**
  - It could be read as "not told apart".
  - The case is likely. The key's refutes come only from scored siblings (at most 2 for each question) and from
    text-only contributors (K2), over 10 mines. The demo's alert question had 9 contributing mines of 10, so at most 1
    sibling.
  - If the refutes sit at 2 of the 10 mines, a draw of 10 mines misses both with probability (8/10)^10, which is 0.107
    rounded.
  - The share depends only on the key's verdicts, so it can be computed before any model runs.
- **Now:**
  - A withheld interval gives that model no headline verdict. Its headline is "no verdict" (K3), never "not told
    apart".
  - The plan job runs rule 8's draws (B 10,000, `random.Random("l1:1")`, the same draws that every judge is scored on)
    over the key's scored site answers. It preregisters the share of draws with no key confirm or no key refute.
  - If that share is above 5%, L001 stops in the plan job, before any model runs, as it stops when no alert is raised
    ("What the code allows", item 7). A new choice file then decides.
  - If a model's left-out answers (rule 7) raise the share above 5% on its own scored answers, its intervals are
    withheld, and it gets no verdict.

### K8. The gate's agreement, beside a constant status (rules 7 and 10; "How it is read")

- **Was:** Rule 10 reported, for each judge, how many gate decisions were correct, out of the questions.
- **Why:**
  - A confirm with fewer than k (3) yes-and-yes records is weak, and the gate does not count it (`pushdown/gate.py`).
  - An 8-week window holds few records at a mine. In the demo's recorded file, 1,078 of the 1,109 weekly cells that
    c1's mines sent held a count under 3.
  - So the key's gate status will mostly be `hypothesis`, or `contested` where a text-only contributor refutes. A
    judge can agree with it largely by default, and a count of agreements could be read as skill.
- **Now:** Rule 10 also reports the key's gate statuses over the 5 questions. Beside each judge's count, it reports
  the agreement that a constant status would get: the number of questions with the key's most frequent status. "How
  it is read, as amended" says how to read the two.

### K9. What the guard scans, and when; what the console shows (rule 12)

- **Was:** Before upload, every file that the plan job or a unit writes was scanned with D002's last guard and
  backstop. A hit withheld the file and failed the job.
- **Why:** The repository and its workflow logs are public. The rule left out four places where values from records
  can land:
  - the shard root's server logs (`server/`);
  - the units' `stdout.log` and `stderr.log` (REFERENCE.md, "Shard root");
  - the plan job's console, since `python -m lab.prereg` writes to the job log (`.github/workflows/mycelic-lab.yml`);
  - the step summaries that `lab.summary ... --log` writes. That step and the upload run under `!cancelled()`, so they
    run after a failed step too.

  Also, a traceback's exception value, such as a `KeyError` that holds a document number, goes straight to the job
  log. And a step that reads the whole MSHA file holds values of operators outside c1 to c5, which are not in the
  guard's sentinels at all (`company_sentinels` and `Backstop` in `onboard/report.py`).
- **Now:**
  - **What is scanned:** every file of every directory the run uploads. That is the plan directory; the whole shard
    root, with `server/`, `routing/`, `units/` (the unit records and logs), `runs/`, `provenance.json` and
    `status.json`; and the aggregate's report directory.
  - **When:** on every path (success, failure, a unit's budget, SIGTERM), always before any `lab.summary --log` reads
    the directory, and before upload. A hit withholds the file, keeping only the hit counts, and fails the job. The
    summary then reads only those counts.
  - **The console.** Every step that reads MSHA data sends its stdout and stderr to captured files, which are scanned
    like any file. The console gets only fixed lines and exception class names, never an exception's value.
  - **Steps that read the whole file** write an error, in the console and in their captured files alike, as its class
    name and code location only. The guard's sentinels cover c1 to c5 only, so this rule, not the scan, keeps other
    operators' values out.

### K10. The units' file: the plan's cache first, and a re-run before the first model call (rules 11 and 13)

- **Was:** Each unit got the file by fetching it, or from a cache the plan job saved. Only a run that failed before
  any model judged could run again unchanged.
- **Why:**
  - `tools/onboard/fetch_msha.py` fetches the file live from MSHA. Up to 15 shards and the plan job would each
    download it.
  - If a fetch fails, or MSHA changes the file between the plan job and a unit, that unit fails its sha256 check.
  - Under rule 9 its model then gets no headline. With the other units' models already judging, rule 13 then needs a
    new choice file. One network failure or a weekly refresh could cost a model its result.
- **Now:**
  - The plan job saves the file in the workflow's cache, under a key made from the file's sha256 in the plan. Every
    unit restores it from there first, and fetches it from MSHA only on a cache miss. Either way, the unit checks the
    sha256 against the plan's before anything else.
  - A failed restore and fetch, or a sha256 mismatch, before the unit's first model call, is an infrastructure
    failure.
  - A shard job whose unit failed so may run again against the same plan and the same preregistration, without a new
    choice file. Its model is then scored when all its units finish.
  - A unit that made any model call may not run again. How the lab re-runs a shard job, and how the aggregate takes
    the re-run's result in place of the failed one, is for BUILD-L001.

### K11. Five question slots, fixed in the request (rules 2 and 11; the settings)

- **Was:** One unit per model and question, with 5 questions expected. Rule 2 allowed up to 3 alerts, each with up to
  4 control questions: up to 15 questions and 45 units. The rule did not say how the unit matrix is built.
- **Why:** The lab fixes the unit matrix in `lab.plan`, from the request alone, before any data is read. In
  `.github/workflows/mycelic-lab.yml`, the plan job's `matrix` and `max_parallel` come from the `plan` step, and the
  `prereg` step runs after it. J001 fixed its number of parts in its request (REFERENCE.md, `j1`).
- **Now:**
  - The request fixes 5 question slots per model, so 15 units. Slot 1 is the alert question. Slots 2 to 5 are the
    control questions, in sorted predicate id order.
  - The plan job stops before any model runs if the data give any other number of questions: not exactly one X or S
    alert in the evaluated weeks, or not exactly 4 other specific predicates. A new choice file then decides.
  - Rule 2's "at most 3" is so narrowed to exactly one, and its paragraph on a key that alerts again does not arise.
  - On the demo's file, rule 1 already requires one X alert and no S alert, and the demo's drafted pack had 5
    specific predicates. So 5 questions are expected.

### K12. Number formats (rule 12)

- **Was:** The rule fixed no format for the numbers L001 writes.
- **Why:**
  - The backstop and the refusal match a record id or a mine id of at least `reference_inside_min_chars` (5)
    characters as a whole run of letters and digits (`ValueIndex` and `Refusal` in `onboard/draft.py`; D002's
    settings).
  - L001's files are mostly numbers. A total in milliseconds, or a float written with many decimals, can hold a run of
    digits that equals a mine id or a record id.
  - Such a false hit after the models ran would withhold the result, and rule 13 allows no unchanged re-run.
- **Now:**
  - Every number that L001's own code writes takes one of these forms:
    - a time in seconds, with at most 3 decimals, and never a total in milliseconds;
    - a share, an accuracy or an interval end, with at most 3 decimals;
    - a count, as an integer below 100,000. Tokens summed over calls are not written, and a size is written in KiB
      with at most 3 decimals.

    No float is written unrounded, and no time is written as an epoch; timestamps are ISO 8601 text.
  - A unit has 300 minutes, so no time it writes reaches 100,000 s. No number that L001's code writes after the plan
    job holds a run of more than 5 digits.
  - The ledgers keep the pinned client's format: one HTTP try's latency in milliseconds, with one decimal. The HTTP
    deadline of 600 s keeps it to 6 digits before the point.
  - The plan job's own larger numbers, such as the file's byte count, are scanned before any model runs (K9), so a
    false hit there costs no model result.
  - Nothing is exempt from the scan. A hit on a number is still a hit.

### K13. The shard's minutes, added up (rule 11; the settings)

- **Was:** "A shard job has 330 minutes: the unit's 300 plus the lab's 25-minute overhead, within the shard's capacity
  of 305."
- **Why:** 300 plus 25 is 325, not 330. The lab sets each shard's timeout to its planned minutes plus 25
  (`timeout_minutes` in `lab/plan.py`), and a shard's capacity is `job_minutes` less 25 (REFERENCE.md, "Request
  format"). 330 is `job_minutes`.
- **Now:** `job_minutes` 330 gives a shard capacity of 305. One 300-minute unit fills a shard, whose job timeout is
  then 325 minutes. `max_parallel` stays 15.

### K14. Values the plan's own text holds (rule 12, K9), amended after run 1 and before any model ran

- **When it was written.** After run 1 (see "Runs") stopped at the plan job's guard, and after that job printed its
  preregistration, with the comparators of K3, in its summary. Before any model ran: no model has been downloaded or
  called for L001, and no unit has run. Unlike K1 to K13, it comes after L001's data were read, by the plan job. Its
  author has seen the preregistered figures and the probe's counts, and no value from the file. It changes nothing
  that those figures depend on.
- **Was (K9; K12's last bullet):**
  - Every file of every directory the run uploads is scanned for every record id, mine id and refused value of c1 to
    c5, and for any 8-word run of c1's narratives. A hit withholds the file and fails the job.
  - The values come from D002's split of the file (`D.refused_values` and `report.Backstop`), and they match as whole
    words on folded text (`draft.ValueIndex.found_all`). Nothing is exempt.
  - The guard is `lab/l1guard.py`, run by `python -m lab.msha guard --plan PLAN --dir DIR` in the plan, run and
    aggregate jobs.
- **What run 1 showed:**
  - `plan.json` held one refused value as a whole word. `lab.plan` writes that file from the request,
    `lab/models.json`, its lock (`lab/models.lock.json`) and the commit, before any MSHA file is read. So the value
    did not come from the file. It is lab text that equals a record value.
  - The probe (see "Runs") found that hit only in the plan's shard fields: one contractor id of 4 characters equals
    the label of one of the plan's 15 shards. In J001's run, the same column's hits fell in one shard's files and in
    the reports, the pattern of one shard label colliding.
  - So the guard as K9 wrote it cannot pass on this file with this plan, whatever the models do. The plan job fails
    first. Had it not, the guards over the shard root that carries that label and over the report would withhold
    files and fail their jobs after the models had run, and rule 13 allows no unchanged re-run then.
- **Now:**
  - Before it scans, the guard reads `plan.json`, the file that `--plan` names. `lab.plan` writes it from the request,
    the model manifest, the lock and the commit before the MSHA file is read. In the plan job, `lab.plan` runs before
    `lab.prereg`, and `lab.prereg` checks the plan's bytes and does not change them.
  - The guard leaves out of its record-id, mine-id and refused-value sets every value that is found, as a whole word,
    in `plan.json`'s text. The matching is the scan's own: folded text, whole words.
  - It prints one fixed line with the number of values left out of each set, and never a value.
  - `plan.json` is still scanned like every other file. Nothing else is exempt: no file, no directory and no other
    value. The narrative n-gram scan is unchanged.
  - If `plan.json` cannot be read as a lab plan (it is missing, or withheld), nothing is left out, and the scan is
    K9's.
  - K9's "nothing is exempt" and K12's last bullet now read: no file is exempt, and a hit on a number is still a hit.
    Only the values that `plan.json`'s text holds are left out of the value sets.
  - This amendment is committed alone, before the guard's code changes. BUILD-L001 records the change.
- **Why it is sound:**
  - Such a value is already in public lab text that the run writes by design: the shard and unit labels, which the
    plan, each shard's files and the report carry. Its occurrence in an output cannot be told apart from that text,
    and publishing it reveals nothing that the plan does not.
  - What is left out is fixed before any MSHA file is read, by committed files and the commit. No record, and nothing
    a unit writes, can add to it.
  - L001's outputs carry no contractor id by construction. They hold verdicts, counts and times, with mines as m01
    onwards and records as indexes (rule 12).
- **What it costs.** A record value equal to a word of the plan's text is not caught by the value scan, wherever it
  appears. Run 1's plan held one such value: the probe found 1 refused value (a contractor id of 4 characters), no
  record id and no mine id. Run 2's plan is expected to differ from run 1's only in what comes from the request and
  the commit (the request's name, path, sha256 and purpose, the run ids taken from that sha256, and the commit). So 1
  value is expected to be left out, and no record id or mine id. The guard's line prints the count, and run 2's record
  states it. The n-gram scan still catches any run of a narrative.
- **What does not change:** the file and its checks, the alert, the questions and their slots, the judges, the
  comparators and how they are preregistered, the headline (K3), the settings, the units and their budgets. Where and
  when the guard runs, and what a hit does (K9), also stay. Run 2's plan job computes the preregistration again, on the
  same file and settings. The same figures are expected, and run 2's record sets them beside run 1's.
- **The next run.** Run 2 is a new request, `lab/requests/latency-002.json`. It is `lab/templates/latency.json` with
  every field unchanged except `purpose`, which names run 2 and K14. latency-001 was the template unchanged, so run 2's
  settings are latency-001's: provider `llama-server`, the three models, `job_minutes` 330, `max_parallel` 15,
  `retention_days` 30, and the `l1` block (`minutes` 300, `questions` 5, `bootstrap_b` 10000, `bootstrap_seed` 1).
  The first run under K14 is the result (rule 13). A run that fails before any model judges, for an infrastructure
  reason, may still run again unchanged, and K10 still applies.

### What the plan job does before any model runs, as amended

In this order. Any stop or refusal ends L001 before any model runs, and everything else is preregistered.

1. Rule 1's checks: the file's sha256, the drafted pack's config hash, the alert list and the audit's counts, and the
   demo's figures when the file is the demo's.
2. The questions: exactly 5 (K11). With no alert, L001 stops ("What the code allows", item 7).
3. For each question: its id, window and routes; the routed mines by role and by stratum; the siblings with and
   without a record in the window (K1, K2); and each routed mine's retrieved records with the codes hidden, checked
   against the unchanged store and counted (rule 4, K4).
4. The key judge, the lexical judge, the record-blind control and the route-role baseline, each on its own fresh
   stores for each question (K4): their site verdicts, gate results and scores, by stratum, and the construction
   counts (K2).
5. The predicate-only bound, the key's gate statuses and the agreement of a constant status (K8).
6. The share of bootstrap draws with no key confirm or no key refute. Above 5%, L001 stops (K7).
7. The guard over the plan directory, before the summary and the upload (K9), with the values that `plan.json`'s text
   holds left out of its value sets (K14).

### The settings, as amended

| Setting | Value |
|---|---|
| Models | a-0p5b, a-1p5b, a-4b; one run each |
| Operator, pack, records | c1; drafted from 2015-01-01 to 2021-12-31; held out 2022-01-01 to 2024-12-31; D002's settings |
| Questions | 5 slots per model, in the request: the alert question, then the 4 other specific predicates, sorted |
| Routes | the product's: each question's own contributing mines (`codes` or `text_only` cells) and at most 2 siblings |
| Codes in the judges' payloads | empty (the key judge alone reads them) |
| Headline comparators | the lexical judge and the route-role baseline; a model answers better only above both |
| Beside, deciding nothing | the record-blind control, the predicate-only bound, every judge's answers by stratum |
| Stores | fresh HQ and mine stores for every question and judge |
| Orchestrator deadline | 3600 s per mine; sites one after another, in sorted order; scored at the first decision |
| Endpoint | the lab's pinned model server on loopback, at `site:<mine>`; HTTP deadline 600 s, 1 retry; no escalation |
| Warm-up | a generated record and a question that no unit asks |
| Verifier secret | `demo_seed` 1 |
| Bootstrap | over mines, B 10,000, seed `l1:1`; over records, seed `l1:1:records`; stop above 5% empty draws |
| Units | 15 (3 models x 5 slots); 300 min; one per shard; `job_minutes` 330 (capacity 305, timeout 325) |
| Parallel shards | `max_parallel` 15 |
| The file in a unit | the plan's cache first, MSHA on a miss; sha256 checked against the plan's |
| Numbers written | seconds and shares with at most 3 decimals; integer counts below 100,000 |

### How it is read, as amended

These replace or extend the bullets of "How it is read" that they name. The others stand.

- **A model that answers better** (replacing "A model that answers better than the lexical judge"). It reads the filed
  category from these narratives, with the codes hidden, better than the drafted terms and better than HQ's routes.
  Here HQ's `codes`-channel cells already hold that category (K2), so it is not an answer HQ lacked. Hiding the codes
  stands in for mines whose records carry no code the pack maps, and the result bears on those. It shows nothing for
  another field or another company.
- **A model better than the lexical judge only** adds nothing over HQ's cells. It is not shown to beat a reader of
  HQ's routes, which reads no record.
- **The lexical judge reads with the extractor that chose the routes.** It is wrong by construction at text-only
  contributors and right at siblings with records, so its specificity is set by the mix of routes (K2).
- **The strata show where a model differs.** Against the route-role baseline, a model can gain only at text-only
  contributors. At codes contributors and at siblings, that baseline is right by construction (K2, with its
  exceptions), so a model can only lose there.
- **The record-blind control** (replacing "shows what the alert alone gives") answers by which question was asked. The
  route-role baseline shows what HQ's routes alone give.
- **The gate's decisions** (extending "The gate's decisions are few"). Read each judge's agreement with the key beside
  the agreement of a constant status (K8). A judge that matches the key no more often than a constant status shows
  nothing by it.
- **The time** (extending "The time"). The first call at each mine after the first can be faster than the product's
  would be, because the question part is cached on the one server. Compare each mine's first-call latency with its
  median (K5). Mines flagged as contended were timed while a late thread shared the server (K6).

### Traps, as amended

- **Trap 4 is replaced: routing sets much of the score.** Each question asks its own key's mines (K1). The key refutes
  only at siblings with records and at text-only contributors. By construction, the lexical judge is wrong at the
  second and right at the first (K2). A pooled score mixes the three strata, so read them apart.
- **Trap 7 is extended.** After a mine times out, its late thread keeps calling the shared server while the next
  mines are timed. Those mines are flagged (K6).
- **Trap 11 is extended.** The same drafted terms, run by each mine's extractor, made the `text_only` cells that chose
  the routes. The lexical judge reads again with that extractor.
- **Trap 12, new: the question part is cached across mines.** The judge's prompt starts with the task's instructions
  and the question part, and one server serves every mine. After the first mine, each mine's first call takes that
  prefix from the cache, where a mine's own server would evaluate it once. The first-call latencies show how much
  this saves (K5).

### What it does not show, as amended

One item is added:

- Whether HQ gains an answer it lacks. Here, HQ's `codes`-channel cells hold the answer key (K2).

## Runs

### Run 1: latency-001, 2026-10-10 (stopped at the plan job's guard; no measurement, not counted)

[Run 38090725021](https://github.com/anovruzov/NeuralGraph/actions/runs/38090725021), request
`lab/requests/latency-001.json`, pushed as `2dff113`, which started the run at 22:15 UTC. The rule (`0fa028d`, amended
K1 to K13 in `9f505e7`) and the build (`9531318`, fixes `fc202e7`) were merged in `9eadd8f`.

- **What ran.** The plan job (job 114326419289) wrote the plan and ran the l1 preregistration
  (`prereg: e1 no x1 no e2 no j1 no l1 yes`, 22:15:38 to 22:17:23 UTC). It then saved the MSHA file in the workflow's
  cache, key `lab-msha-62d0c861a5c3a8e7` (K10). The cache is saved only after the preregistration step succeeds, so
  rule 1's checks, K11's five questions and K7's share of empty draws did not stop the run.
- **Where it stopped.** The guard over the plan directory (`python -m lab.msha guard --plan ... --dir lab-plan`, K9)
  printed exactly this line and exited 1:

  ```
  l1 guard: lab-plan files 7 withheld 1 record_id 0 mine_id 0 refused_value 1 narrative_ngrams 0 unread 0
  ```

  The withheld file was `plan.json`. Its bytes were replaced by its hit counts, so the plan summary that followed
  showed the request, the models, the shards and the units as n/a. The provision, run and aggregate jobs were skipped.
- **No model ran.** No model was downloaded or called, and no unit ran.

**The preregistration, as the plan job printed it.** Before it stopped, the plan job printed its summary
(`summary.md`, 50 lines, sha256 `173e07ea5f31fcd28aafbae8586618b3f80061634ce848f5216a1dc410f710a2`). Its figures were
fixed before any model ran. The lexical judge's and the route-role baseline's are the headline's comparators (K3), the
key's is the truth, and the rest sit beside them. Quoted as printed:

- MSHA file sha `62d0c861a5c3`, the demo's file: yes. Records 1033, mines 10, weeks 139.
- Alert: channel X, 2023-W51, `slip_or_fall_of_person`.

| Slot | Kind | Predicate | Contributing mines | Codes contributors | Text-only contributors | Siblings | Records retrieved | Key's gate status |
|---|---|---|---|---|---|---|---|---|
| 1 | alert | `slip_or_fall_of_person` | 9 | 6 | 3 | 0 | 36 | contested |
| 2 | control | `handling_of_materials` | 7 | 6 | 1 | 2 | 36 | contested |
| 3 | control | `handtools_nonpowered` | 1 | 1 | 0 | 2 | 19 | hypothesis |
| 4 | control | `machinery` | 2 | 2 | 0 | 2 | 25 | hypothesis |
| 5 | control | `powered_haulage` | 4 | 3 | 1 | 2 | 29 | contested |

| Judge | Balanced accuracy [interval] | Sensitivity | Specificity | Unknown share |
|---|---|---|---|---|
| lexical | 0.641 [0.403, 0.840] | 0.667 | 0.615 | 0.000 |
| route_role | 0.808 [0.667, 0.938] | 1.000 | 0.615 | 0.000 |
| record_blind | 0.551 [0.379, 0.714] | 0.333 | 0.769 | 0.000 |
| key | 1.000 [1.000, 1.000] | 1.000 | 1.000 | 0.000 |

Predicate-only bound 0.603. Empty draws share 0.000. Constant status agreement 3 of 5.

**Why the guard withheld `plan.json`: a probe, with counts only.** `tools/l1/guard_probe.py` (workflow
`.github/workflows/l1-guard-probe.yml`, commit `920fe95`, run 38091846827, job 114329701887) rebuilt the guard on the
same file and counted where its values meet lab text. It printed no value.

- **The refused values:** 11,770, of 5 operators. By column and length: CLOSED_DOC_NO 11,185 of 8 or more
  characters; CONTRACTOR_ID 202 of 4 characters and 111 of 5; CONTROLLER_ID 3 of 6 and 2 of 7; CONTROLLER_NAME 5 of 8
  or more; OPERATOR_ID 72 of 6 and 30 of 7; OPERATOR_NAME 160 of 8 or more.
- **`plan.json` as run 1 wrote it** (regenerated from the same request at `2dff113`): refused_value 1, from
  CONTRACTOR_ID. The only path classes that held a hit were `.matrix.include[].shard`, `.shards[].shard` and
  `.units[].shard`. So one contractor id of 4 characters equals the label of one of the plan's 15 shards.
- **J001's run files** (run 37996675559, 9 shards): other JSON files, 7 of 99 with hits; reports, 2 of 3; summaries,
  2 of 20; every hit from CONTRACTOR_ID. `run.json` 0 of 18 files, ledgers and replies 0 of 49, server logs 0 of 9,
  other logs 0 of 36. The lab's code (29 files), the lab's docs (5) and L001's docs (2): 0. That pattern, one shard's
  files plus the reports, fits one shard label colliding.
- **So, unchanged,** the later guards would also withhold files and fail their jobs after the models had run: the
  guard over the shard root that carries the colliding label, and the guard over the report.

**Reading.** The run gave no measurement. It is recorded here and is not counted as a result of the test. It did not
fail for an infrastructure reason: an unchanged re-run would stop at the same line. K14 changes the guard, and the
first run under K14 is the result.

### Run 2: latency-002, 2026-10-11 (the result)

[Run 38102412415](https://github.com/anovruzov/NeuralGraph/actions/runs/38102412415), request
`lab/requests/latency-002.json` (sha `c0757276beab`), pushed as `abbf2e4`, which started the run at 01:36 UTC on
2026-10-11; the aggregate job finished at 01:44 UTC. It is the first run under K14, so by rule 13 and K14 it is the
result of the test. Every figure below is copied from the aggregate job's `report.md`; none is rounded.

- **What ran.** The plan job (job 114360892773) wrote the plan (`a475391c94fb`) and ran the l1 preregistration
  (`prereg: e1 no x1 no e2 no j1 no l1 yes`). The K14 line printed
  `l1 guard: values in the plan's own text, left out: record_id 0 mine_id 0 refused_value 1`: the one value K14
  expected, no record id and no mine id. The guard over the plan directory then printed
  `l1 guard: lab-plan files 9 withheld 0 …`, and the run went on to the provision, run and aggregate jobs. Every
  shard's guard printed the same count left out (`refused_value 1`) and `withheld 0`, and so did the aggregate job's
  (job 114362048048). Its `report.md` (146 lines, sha256 `e8b3960786ca…`, checked against the marker) is the only
  source of the figures below. Before the run, the probe re-run under K14 on the real file (run 38102272696) left out
  exactly 1 refused value (a contractor id of 4 characters) and found 0 hits in `plan.json`, in J001's 234 run files,
  in the lab's code and docs, and in run 1's plan artifact.
- **All 15 units finished.** 3 models x 5 slots, one unit per shard; every shard `sealed` at attempt 1 with exit code
  0, every unit status `ok`, class `model`. Shard wall times 25.7 to 199.4 s; unit wall times 23.4 to 173.1 s. No
  call failed, no question was contended or left unfinished, and no mine timed out (the longest question took
  146.125 s against the 3,600 s deadline per mine). Provisioning verified the server and the three model files from
  the cache against the lock (`2cda5ff93639`, `74a4da8c9fdb`, `6a1a2eb6d156`, `7485fe6f11af`); the lock is unchanged.
- **The preregistration matched run 1's** in every figure, as K14 expected: the same alert, the same five slots with
  the same routes and retrieved records, and the same figures for the key, the lexical judge, the route-role baseline
  and the record-blind control. Quoted from the report:
  - MSHA file sha `62d0c861a5c3`, the demo's file: yes. Operator c1. Records 1033, mines 10, weeks 139.
  - Alert: channel X, 2023-W51, `slip_or_fall_of_person`.

  | Slot | Kind | Predicate | Contributing mines | Codes contributors | Text-only contributors | Siblings | Records retrieved | Key's gate status |
  |---|---|---|---|---|---|---|---|---|
  | 1 | alert | `slip_or_fall_of_person` | 9 | 6 | 3 | 0 | 36 | contested |
  | 2 | control | `handling_of_materials` | 7 | 6 | 1 | 2 | 36 | contested |
  | 3 | control | `handtools_nonpowered` | 1 | 1 | 0 | 2 | 19 | hypothesis |
  | 4 | control | `machinery` | 2 | 2 | 0 | 2 | 25 | hypothesis |
  | 5 | control | `powered_haulage` | 4 | 3 | 1 | 2 | 29 | contested |

  | Judge | Balanced accuracy [interval] | Sensitivity | Specificity | Unknown share |
  |---|---|---|---|---|
  | lexical | 0.641 [0.403, 0.840] | 0.667 | 0.615 | 0.000 |
  | route_role | 0.808 [0.667, 0.938] | 1.000 | 0.615 | 0.000 |
  | record_blind | 0.551 [0.379, 0.714] | 0.333 | 0.769 | 0.000 |
  | key | 1.000 [1.000, 1.000] | 1.000 | 1.000 | 0.000 |

  Predicate-only bound 0.603. Empty draws share 0.000. Constant status agreement 3 of 5.

**The results, read by the rule as amended.** Each model finished 5 of 5 questions, and 0 of its 31 scored site
answers were left out, so each gets a headline (K3, rule 7, rule 9). Both paired differences come from the same
bootstrap draws over mines (rule 8).

| Model | Balanced accuracy [interval] | Minus the lexical judge [interval] | Minus the route-role baseline [interval] | Headline (K3) |
|---|---|---|---|---|
| a-0p5b | 0.500 [0.500, 0.500] | -0.141 [-0.340, 0.097] | -0.308 [-0.438, -0.167] | **not told apart** |
| a-1p5b | 0.417 [0.333, 0.500] | -0.224 [-0.436, -0.007] | -0.391 [-0.533, -0.256] | **worse** |
| a-4b | 0.417 [0.333, 0.500] | -0.224 [-0.436, -0.007] | -0.391 [-0.533, -0.256] | **worse** |

- **a-0p5b: not told apart.** The lower end of its interval against the lexical judge is below 0, so it does not
  answer better; the upper end is above 0, so by K3 it is not "worse" either. Its interval against the route-role
  baseline lies wholly below 0, which K3 does not name and which decides nothing. No model is "better than the
  lexical judge only".
- **a-1p5b and a-4b: worse.** The upper end of each interval against the lexical judge is below 0 (-0.007). Both
  intervals against the route-role baseline also lie below 0.

| Judge | Balanced accuracy [interval] | Sensitivity | Specificity | Unknown share | Site answers left out |
|---|---|---|---|---|---|
| lexical | 0.641 [0.403, 0.840] | 0.667 | 0.615 | 0.000 | |
| route_role | 0.808 [0.667, 0.938] | 1.000 | 0.615 | 0.000 | |
| record_blind | 0.551 [0.379, 0.714] | 0.333 | 0.769 | 0.000 | |
| a-0p5b | 0.500 [0.500, 0.500] | 1.000 | 0.000 | 0.000 | 0 of 31 |
| a-1p5b | 0.417 [0.333, 0.500] | 0.833 | 0.000 | 0.161 | 0 of 31 |
| a-4b | 0.417 [0.333, 0.500] | 0.833 | 0.000 | 0.323 | 0 of 31 |

- **Specificity 0.000 for every model:** none refuted at any mine where the key refuted. a-0p5b confirmed at every
  scored mine (sensitivity 1.000, unknown share 0.000): 0.5 is a judge that gives the same answer everywhere ("How it
  is read"). a-1p5b and a-4b confirmed at fewer of the key's confirms (0.833) and answered `unknown` at 0.161 and
  0.323 of the scored site answers, which is never correct (rule 7), so they sit below 0.5.
- **The gate (K8).** Gate decisions equal to the key's: a-0p5b 0 of 5, a-1p5b 1 of 5, a-4b 2 of 5. A constant
  status, the key's most frequent (`contested`), agrees on 3 of 5. No model matches the key as often as a constant
  status, so none shows anything by it. The statuses, by slot (the gate's first decision; no mine timed out, so no
  late verdict followed):

| Slot | Kind | Predicate | Key's gate status | a-0p5b | a-1p5b | a-4b |
|---|---|---|---|---|---|---|
| 1 | alert | `slip_or_fall_of_person` | contested | supported | hypothesis | hypothesis |
| 2 | control | `handling_of_materials` | contested | supported | supported | hypothesis |
| 3 | control | `handtools_nonpowered` | hypothesis | supported | hypothesis | hypothesis |
| 4 | control | `machinery` | hypothesis | supported | stale | hypothesis |
| 5 | control | `powered_haulage` | contested | supported | hypothesis | hypothesis |

  a-0p5b's gate opened `supported` on all five questions, including the two (slots 3 and 4) where the key's status
  was `hypothesis`. a-4b's gate said `hypothesis` on all five, which matches the key exactly where the key said
  `hypothesis`. a-1p5b's gate said `supported` once, `stale` once and `hypothesis` three times.

**The latency, a measurement (rule 6, rule 9: it passes or fails nothing).** Times are seconds on one shared
GitHub-hosted runner per shard, with one model server serving every mine in turn, the mines asked one after another in
HQ's own process; the question build, each mine and the gate are timed apart; the "at once" and "default deadline"
times are derived, not run; "no model" is the lexical path on the same runner. The shards ran on three CPU models
(AMD EPYC 7763, AMD EPYC 9V45, AMD EPYC 9V74), and the alert question's three shards were not all on the same one, so
compare timings only between rows of the same CPU model.

| Model | The alert (slot 1): runner CPU | Time to answer s | At once s, derived | Default deadline s, derived | No model s | Question build s | Gate s | Mine median s | Mine 95th percentile s | Judge call median s | Judge call 95th percentile s | Model calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| a-0p5b | AMD EPYC 7763 | 22.668 | 5.298 | 22.668 | 0.039 | 0.002 | 0.003 | 2.237 | 4.866 | 0.621 | 0.870 | 36 |
| a-1p5b | AMD EPYC 9V74 | 35.774 | 9.162 | 35.774 | 0.036 | 0.002 | 0.002 | 3.491 | 7.969 | 0.936 | 1.379 | 36 |
| a-4b | AMD EPYC 7763 | 143.058 | 34.633 | 143.058 | 0.039 | 0.002 | 0.003 | 14.334 | 31.167 | 3.792 | 5.306 | 36 |

| Model | All five questions | Time to answer median s | Time to answer 95th percentile s | Mine median s | Mine 95th percentile s | Judge call median s | Judge call 95th percentile s | Model calls, median | Contended questions | Questions not finished |
|---|---|---|---|---|---|---|---|---|---|---|
| a-0p5b | 5 | 15.406 | 22.710 | 2.247 | 5.293 | 0.523 | 0.870 | 29.0 | 0 | 0 |
| a-1p5b | 5 | 28.111 | 43.892 | 4.678 | 10.165 | 1.041 | 1.763 | 29.0 | 0 | 0 |
| a-4b | 5 | 119.476 | 145.512 | 14.664 | 34.695 | 3.518 | 5.484 | 29.0 | 0 | 0 |

| Model | Slot | Gate status, first decision | Key's gate status | Time to answer s | At once s | Default deadline s | No model s | Runner CPU |
|---|---|---|---|---|---|---|---|---|
| a-0p5b | 1 | supported | contested | 22.668 | 5.298 | 22.668 | 0.039 | AMD EPYC 7763 |
| a-0p5b | 2 | supported | contested | 22.721 | 5.297 | 22.721 | 0.042 | AMD EPYC 7763 |
| a-0p5b | 3 | supported | hypothesis | 10.940 | 5.104 | 10.940 | 0.016 | AMD EPYC 9V74 |
| a-0p5b | 4 | supported | hypothesis | 15.406 | 5.339 | 15.406 | 0.022 | AMD EPYC 7763 |
| a-0p5b | 5 | supported | contested | 9.362 | 2.856 | 9.362 | 0.016 | AMD EPYC 9V45 |
| a-1p5b | 1 | hypothesis | contested | 35.774 | 9.162 | 35.774 | 0.036 | AMD EPYC 9V74 |
| a-1p5b | 2 | supported | contested | 45.922 | 10.639 | 45.922 | 0.038 | AMD EPYC 7763 |
| a-1p5b | 3 | hypothesis | hypothesis | 24.315 | 11.030 | 24.315 | 0.018 | AMD EPYC 7763 |
| a-1p5b | 4 | stale | hypothesis | 28.111 | 9.713 | 28.111 | 0.018 | AMD EPYC 9V74 |
| a-1p5b | 5 | hypothesis | contested | 27.955 | 8.002 | 27.955 | 0.021 | AMD EPYC 9V74 |
| a-4b | 1 | hypothesis | contested | 143.058 | 34.633 | 143.058 | 0.039 | AMD EPYC 7763 |
| a-4b | 2 | hypothesis | contested | 146.125 | 34.766 | 146.125 | 0.038 | AMD EPYC 7763 |
| a-4b | 3 | hypothesis | hypothesis | 34.539 | 16.617 | 34.539 | 0.010 | AMD EPYC 9V45 |
| a-4b | 4 | hypothesis | hypothesis | 95.484 | 33.311 | 95.484 | 0.020 | AMD EPYC 7763 |
| a-4b | 5 | hypothesis | contested | 119.476 | 35.364 | 119.476 | 0.027 | AMD EPYC 7763 |

- **The alert, one mine after another:** 22.668 s with a-0p5b, 35.774 s with a-1p5b and 143.058 s with a-4b, for 9
  mines and 36 model calls. a-1p5b's alert shard ran on an AMD EPYC 9V74 and the other two on an AMD EPYC 7763, so
  the three are not one comparison. The question build (0.002 s) and the gate (0.002 to 0.003 s) are not where the
  time goes; the mines are, and within a mine the judge calls: medians 0.621, 0.936 and 3.792 s a call on the alert.
- **Derived, deciding nothing.** Had every mine been asked at once, each with a server like this runner's, the
  alert would have taken 5.298, 9.162 and 34.633 s. Under the product's default deadline of 600 s nothing changes:
  no mine came near it, so the default-deadline time equals the time to answer on every row. The lexical path with
  no model answered the alert in 0.039, 0.036 and 0.039 s on the same runners (0.010 to 0.042 s over all 15 rows).
- **The five questions together:** median time to answer 15.406, 28.111 and 119.476 s; 95th percentile 22.710,
  43.892 and 145.512 s; median 29 model calls a question (the slots retrieved 36, 36, 19, 25 and 29 records). The
  control questions' spread is one window's questions, not alerts ("How it is read").
- 0 contended questions and 0 questions not finished for every model, so no time is a lower bound and no time is
  shown apart (K6, rule 11).

**What it does not show** ("What it does not show", as amended). One alert of one operator on one public file, so
nothing about other alerts, companies or fields. Shared GitHub-hosted runners whose CPU model varied between shards,
so nothing about a site's hardware. The codes were hidden from every judge but the key, and HQ's `codes`-channel cells
already held the answer (K2), so nothing about whether HQ gained an answer it lacked, and nothing about a judge that
sees the codes. No transport between machines, no detection and no model loading was timed. No outcome exists, so
nothing about whether the alert was right, early or useful; this is not an early-warning result. The "at once" and
"default deadline" times were derived, not run.

**Verdict.** The test's pre-registered question, whether a small-model verifier inside each mine answers HQ's narrow
question better than the lexical judge and better than HQ's routes, is answered **no** for all three models: a-0p5b
is not told apart from the lexical judge and a-1p5b and a-4b answer worse, and every interval against the route-role
baseline, a reader of HQ's routes that reads no record, lies below 0. Specificity 0.000 means none of the three
refuted anything: where the key refuted, every model confirmed or answered `unknown`. a-0p5b's gate opened
`supported` on every one of the five questions, including the two the key rated `hypothesis`. The latency is
recorded as a measurement: the alert was answered in 22.668 to 143.058 s one mine after another on these shared
runners, with the lexical path at 0.036 to 0.039 s.

**The budget.** "Why the units are this size" budgeted up to 1,033 model calls a question, c1's whole held-out set,
and gave each unit 300 minutes. The plan job counted 36 records for the alert question and 19 to 36 for the controls,
so each question made at most 36 calls, and the shards finished in minutes: 25.7 to 199.4 s of wall time each, the
units 23.4 to 173.1 s.
