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

## Runs

None yet.
