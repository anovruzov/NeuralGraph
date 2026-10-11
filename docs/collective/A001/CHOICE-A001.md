# Accuracy test A001: the first cycle toward 86, on a fresh draw of real complaints

The founder's goal is "loop until we hit 86". What 86 means is pinned in `docs/strategy/LOOP-48H.md`, section 2
(commit `cccadaf`): balanced accuracy 0.860 of the site verifier's verdicts against the filed-category key, at record
level, with the codes hidden, on a fresh pre-registered draw of 150 real public NHTSA complaints, scored exactly as
J001 was scored (`docs/collective/replay/vehicles/CHOICE-J001.md`), with the interval and confirmation rules of its
section 2.5. This file is the rule for the loop's first cycle. It is committed alone, before any code is written for
it and before any record of its draw exists. How the lab will run it (a new experiment kind, `j2`, on J001's pattern)
goes in `BUILD-A001.md`, which is written after this rule.

**What the evidence says.** Every small model has judged the product's narrow question worse than word matching, and
the dominant failure is a yes-bias: the judges confirm nearly everything and almost never say no.

- J001 run 9 (lab run 37996675559; `docs/lab/RESULTS.md`, run 9), 150 NHTSA complaints, codes hidden: balanced
  accuracy 0.500, 0.477 and 0.387 for a-0p5b, a-1p5b and a-4b, against the lexical judge's 0.670 [0.627, 0.713], the
  record-blind control's 0.527 and the predicate-only bound's 0.580. a-0p5b said `describes_predicate` yes on 145 of
  150 positives and 145 of 150 negatives; a-1p5b on 138 and 131. a-4b told them apart (yes on 97 positives and 37
  negatives) but answered unclear on 51 positives and 94 negatives, and the shipped `decide` turns every unclear into
  `unknown`, which is never correct. A reading chosen after the result, unclear as refute, would have put a-4b at
  0.700; it decides nothing (CHOICE-J001, Runs).
- L001 run 2 (lab run 38102412415, aggregate job 114362048048, `report.md` sha256 `e8b3960786ca…`; to be recorded as
  RESULTS run 10), real MSHA records through the whole pushdown path: site-level balanced accuracy 0.500, 0.417 and
  0.417 against the lexical judge's 0.641 and the route-role baseline's 0.808; sensitivity 1.000, 0.833 and 0.833;
  **specificity 0.000 for all three**: no model ever refuted a site answer; unknown share 0.000, 0.161 and 0.323.
  a-0p5b's gate said `supported` on all five questions.

**What this cycle tests.** The first two levers of the pinned lever order (LOOP-48H section 2.6), as four declared
arms on one fresh draw: the product's judge unchanged (the J001 baseline, so every lift is paired), a declared reply
mapping that reads an unclear component answer as a no before the pinned `decide` (lever 1, decision 9), a variant
judge instruction that defines negation and absence (lever 2, J001 trap 3), and both together. One primary pair is
named below. By the pinned rule, a pass in this cycle can only be provisional; confirmation needs the next cycle.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen J001's result (RESULTS run 9; CHOICE-J001, Runs), including each model's answer-pair counts and the
  post-hoc 0.700 reading of a-4b, which it names below as a secondary's prior expectation and never as a result.
- It has seen L001 run 2's report (the aggregate job's `report.md`, sha256 `e8b3960786ca…`), L001 run 1's record,
  RESULTS runs 1 to 9, D002 and D003, the handoff (`docs/handoff/HANDOFF-2026-10-09.md`) and the loop prompt
  (`docs/strategy/LOOP-48H.md`).
- It has not seen any record of this cycle's draw. The draw does not exist: it is made in the plan job on a runner,
  from the complaint file as published on the day of the run, after J001's records are removed. The sandbox cannot
  reach NHTSA.
- It has not read J001's stored replies. They sit in the run's artifacts, which this environment did not download.
  The variant instruction and the mapping below were written from J001's answer counts and from trap 3 of its rule,
  not from any reply text.
- No call on the handoff's decision 9 (how a site verifier's "unclear" counts) has been received: no
  `docs/handoff/LOOP-48H-STATUS.md` exists at `cccadaf`, and no message from the founder or the chief scientist names
  one. The loop prompt's default applies (section 7): the shipped `decide` is primary and the declared mapping is a
  secondary. A call received before the run is adopted by a dated amendment before any run; one received after the run
  bears on the next cycle.
- The models may have seen these public complaints in training. The answer key is the components each complaint was
  filed under, not labels anyone checked.

## The metric, pinned

Copied in substance from LOOP-48H sections 2.1 to 2.4; where this file and that section disagree, that section wins,
and the disagreement is a defect of this file.

- **86 is balanced accuracy 0.860** of the site verifier's verdicts against the filed-category key, on a scale where
  the key itself scores 1.000. Balanced accuracy is (sensitivity + specificity) / 2, scored as J001 scores: for each
  record one question about a filed category (positive) and one about a category the record was not filed under
  (negative, drawn with the frequency of the other records' positives, `lab/j1.py:130` `NEGATIVE_DRAW`); a `confirm`
  on a positive is correct, a `refute` on a negative is correct, `unknown` is never correct. Intervals are a cluster
  bootstrap over records, B 10,000, 95%.
- **The centralized baseline** is the filed-category key: the codes the filer attached to each record, which a
  centralized system already holds (HQ's `codes`-channel cells; `docs/collective/L001/CHOICE-L001.md`, K2). A
  centralized reader that sees the codes scores 1.000 by construction. The site verifier reads only its own record's
  text, codes hidden. So 86 means: the site verifier recovers the centralized answer at balanced accuracy 0.860 while
  reading nothing but its own record.
- **The data class is fixed** (section 2.3): real public NHTSA complaints of the vehicle pack, the j1 eligibility rule,
  n 150, codes hidden, through `judge_task`, `judge_payload` and `decide` of `mycelic/collective/edge/verify.py`
  (`:135-144`, `:162-168`, `:243-259`; pinned, unchanged), or through `Runtime.run(task, payload, schema, ref=...)`
  (`mycelic/collective/inference/runtime.py:166-167`) with a lab-side TaskSpec scored by the same pinned `decide`. Any
  change of data class gives a result that can never count toward 86. No arm may place the judged record's codes, its
  filed categories or anything derived from them in the payload, the task text or a reply mapping.
- **The comparators** (section 2.4), on the same records, paired, with 95% cluster-bootstrap intervals of the
  difference: the lexical judge (`lexical_judge`, `verify.py:171`; J001 0.670 [0.627, 0.713]), the predicate-only
  bound (`lab/j1.py:346-361`; J001 0.580, a point value fitted to the draw's answers, no interval) and the
  record-blind control (`PRIOR_RULE`, `lab/j1.py:133-135`; J001 0.527 [0.467, 0.587]).
- **Rejected as primary** (gameable): site-level balanced accuracy (the route-role baseline scores 0.808 reading no
  record, L001 runs 1 and 2), codes visible to the judge (it reads the key), and a plain ratio to the lexical judge.
- **The secondary** (BA_site − bound) / (BA_central − bound) against a hosted central reader needs a key the lab does
  not have (`NHTSA_HOSTED`, `lab/request.py:234`; LOOP-48H section 7). It is not run and is never assumed.

## The data and the draw

**NHTSA complaints, as J001 drew them, and nothing else in this cycle.** The reasons:

- **The pinned definition names them.** Section 2.3 fixes the population, the eligibility rule and the size. A J-type
  draw over MSHA records would be a different data class whose result could never count toward 86.
- **Size.** 150 records give 300 questions and 150 clusters; L001 run 2 scored 31 site answers over 10 mines, and
  its intervals ran from 0.33 to 0.50 for a point estimate of 0.417. One record-level draw has about 0.035 of
  standard error on balanced accuracy (section 2.5); the site-level test cannot read a difference smaller than 0.2.
- **Label noise is known and shared.** The key is the filed component codes (J001 trap 1). Its noise ceiling is not
  measured, and it costs every judge alike, the lexical one included. MSHA's filed categories have the same
  property (L001 trap 1) and add a drafted pack whose terms were learned from the same operator's own text (L001
  trap 11).
- **Prior runs.** J001 gives the baseline, the three comparators and the answer counts that justify the arms. Its
  pipeline (`lab/j1.py`, `lab/goldlabels.py`) is pinned and importable, so the questions, the comparators and the
  scoring of this cycle are J001's code, called unchanged. L001's path pools records per mine and has no J-type
  question draw; building one would be a new experiment kind on top of j2.
- **No MSHA replication arm this cycle.** The budget below is spent on four arms over three models. A record-level
  MSHA reading may be a later cycle's labelled secondary; it is listed under "Decisions this does not make".

**A fresh, verifiable draw (section 2.3).** The lab draws `random.Random("nhtsa:<seed>").sample(eligible, n)` from a
complaint file downloaded live in the plan job (`lab/goldlabels.py:90, 133`), so a new seed alone gives no
disjointness from J001, and record refs may never be committed. The exclusion therefore happens on the runner, in the
j2 plan job, in the new module `lab/j2labels.py`:

1. **Mechanism 1, the artifact.** Download J001's plan artifact `lab-plan-37996675559-1` (run 37996675559, attempt
   1; 63,802 bytes; retention 30 days, so until 2026-11-08T22:00:03Z; the API listed it on 2026-10-11), as
   `tools/l1/guard_probe.py` downloads another run's plan artifact. Read `prereg/j1/labels.json`: it must say
   `source` `nhtsa`, `n` 150, `seed` 1 and a `sha256` that starts with `bc6d092d8bca` (RESULTS run 9; CHOICE-J001,
   Runs), else the artifact is not J001's and the mechanism fails. Take the 150 record refs of
   `prereg/j1/questions.jsonl`.
2. **Mechanism 2, the rebuild.** Rebuild the seed-1, n-150 draw from today's complaint file with
   `lab.goldlabels.build_labels("nhtsa", <pack>, 150, 1)`. The mechanism verifies only when the labels' sha256 starts
   with `bc6d092d8bca`; then its 150 refs are J001's. A different sha256 means the complaint file has changed since
   J001; the mechanism then fails and the plan job prints that it did, with both sha256 prefixes.
3. **Stop rules.** If neither mechanism verifies, the plan job stops before any model runs, and the loop asks (an
   open ask in `LOOP-48H-STATUS.md`); it does not run. If both verify, their two ref sets must be equal, else the
   plan job stops. The excluded set is the union of what the verifying mechanisms give: 150 refs.
4. **The draw.** Build the eligible list exactly as `lab.goldlabels.nhtsa_lines` builds it (`goldlabels.py:79-87`:
   the makes `NHTSA_MAKES` and the window `NHTSA_WINDOW` of `goldlabels.py:49-50` verbatim; a narrative, one vehicle,
   at least one specific component code; codes removed; sorted by `record_ref`), remove the excluded refs, and sample
   `random.Random("nhtsa:2").sample(eligible_unexcluded, 150)`, sorted by `record_ref`, in label-file lines of the
   same shape as `goldlabels` writes (`e1_extract.read_labels` reads them, so `lab.j1` needs no change).
5. **What is printed:** one fixed line with the `available` count (the eligible records, `goldlabels.py:143`), the
   excluded count (150 expected), the eligible count after exclusion, the overlap count between the new draw and the
   excluded set (0 expected; above 0 the plan job stops), which mechanisms verified, and the new labels' sha256 prefix.
   Never a ref. If fewer than 150 eligible unexcluded records remain, the plan job stops, and no further fresh draw
   is pre-registered.
6. **Later cycles** list the run ids and labels sha256 prefixes of every earlier cycle, this one included, and exclude
   each from its own plan artifact the same way. For this cycle the list is: J001, run 37996675559, labels
   `bc6d092d8bca`.

## The rule

1. **Records: 150 fresh NHTSA complaints, labels seed 2.** `lab.j2labels`, source `nhtsa`, the pack
   `docs/collective/replay/vehicles/pack`, the makes, the window and the eligibility rule of `lab.goldlabels`
   verbatim, J001's 150 records excluded as "The data and the draw" says, `random.Random("nhtsa:2").sample` of 150.
   The labels are built from the complaint file as published on the day of the run; the file's sha256 and the
   labels' sha256 are preregistered. Each label record has its codes removed (`lab/j1.py:275-279` refuses one that
   still holds them).
2. **Questions: two per record, J001's code, seed 2, five parts.** `lab.j1.build_questions(pack, labels, seed=2,
   parts=5)`, unchanged: the positive is one filed predicate (`random.Random("j1:2:<record_ref>:positive")`), the
   negative follows the other records' positives (`NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged, `lab/j1.py:118-130`).
   Record `i` of 150 is in part `i * 5 // 150 + 1`: 30 records and 60 questions a part. The questions file's sha256
   is preregistered. 150 positives and 150 negatives: 300 questions per model and task.
3. **Arms: four, declared now; two task variants and one reply mapping.** Every arm reads the same payload,
   `verify.judge_payload` of the record with its codes empty (`lab.j1.payload`), uses the shipped schema
   (`verify.judge_schema`, answers `yes`, `no` or `unclear`) and is scored by the pinned `verify.decide` over the one
   record (`lab.j1.verdict`), exactly as J001.
   - **Arm A, `shipped`:** the product's judge, unchanged: `verify.judge_task()` (task `judge_record`, 256 output
     tokens), the shipped reply, `decide`. This is J001's path on the fresh draw: the baseline every lift is paired
     against, and what the product does today at record level.
   - **Arm B, `shipped_mapped`:** arm A's replies read through the mapping M, then `decide`. M: a reply's
     `describes_predicate` `unclear` is read as `no`; `mentions_entity` is left as answered; nothing else changes. So
     a record whose judge names the vehicle but cannot confirm the component is a `refute`. M is a constant of
     `lab/j2.py`, pinned by its text in the preregistration. It reads only the reply: never the record, the
     question, the codes or the filed categories.
   - **Arm C, `negation`:** a lab-side TaskSpec N through `Runtime.run(N, payload, schema, ref=...)`: task
     `judge_record_negation`, data class `raw`, 256 output tokens (`JUDGE_MAX_TOKENS`, `verify.py:87`), the shipped
     schema, the shipped reply, `decide`. N's instructions, fixed now, verbatim:

     > Judge one record of this site against one question. The question names an entity (its type, its id and the
     > aliases it may be written as) and a predicate. mentions_entity: yes when the record names that entity in its
     > text or among its structured entities, no when it does not, unclear when you cannot tell. describes_predicate:
     > the predicate names a component or system of the entity. Answer yes when the record reports a problem,
     > failure, defect or safety concern with that component or system, including a report that it did not work, did
     > not deploy, did not engage, stopped working or failed (the record's codes are statements too). Answer no when
     > the record does not mention that component or system, or mentions it only to say that it worked or was not
     > involved; a component the record never mentions is a no, not an unclear. Answer unclear only when the record
     > mentions that component or system and you cannot tell whether it reports a problem with it. Judge only this
     > record.

     The first three sentences and the last are the shipped instruction's (`verify.py:136-142`). Only the
     `describes_predicate` sentence changes. The build pins N's instructions by sha256 in the preregistration, and
     every unit checks the pin before its first call, as J001 checks the shipped task's (`lab/j1.py:569-572`).
   - **Arm D, `negation_mapped`:** arm C's replies read through M, then `decide`.
   - **Model calls are made for arms A and C only.** Arms B and D make no call of their own: they are arm A's and
     arm C's replies, reply by reply, read through M before `decide`. So the lift of M is paired at the level of the
     single reply, and the budget is two task variants, not four arms.
   - **Codes never reach the judge in any arm.** The payload's codes list is empty (the labels hide them; the runner
     refuses a record that holds any). N's text names no code, category or filed label. M reads the reply alone.
4. **The judging path: the product's, through the lab's pinned server.** As J001's rule 3: `Runtime.run` with its one
   repair after an invalid reply and no escalation, at boundary `site:lab`, data label `public`, through the lab's
   pinned model server on one runner; HTTP deadline 600 s and 1 retry; the client sends temperature 0 on every call
   and the server runs with a fixed seed (J001 rule 4), so one run per model. The routing document routes both task
   names, `judge_record` and `judge_record_negation`, to the unit's one endpoint, without escalation. What
   `SiteVerifier.answer` adds around these functions (boundary checks, budgets, master data, secrets, storage, the
   pooling of many records into one verdict) is not used.
5. **Models:** a-0p5b, a-1p5b and a-4b (`lab/models.json`, unchanged: a-4b with `--reasoning off`), one run each.
6. **The comparators, in the plan job, before any model runs,** as J001's rule 5, on the fresh draw: the lexical
   judge (`lab.j1.lexical_verdicts`), the record-blind control (`lab.j1.prior_verdicts`, `PRIOR_TOP` unchanged) and
   the predicate-only bound (`lab.j1.prior_bound`), each scored on every record and, in the aggregate, on each
   (model, task) set's scored records. The plan job prints their figures. Those figures are never a reason to redraw
   (LOOP-48H section 6.3).
7. **Scoring, against the filed codes,** as J001's rule 6, by `lab.j1.score` unchanged: a verdict is correct when it
   is `confirm` on a positive or `refute` on a negative; `unknown` is never correct; sensitivity, specificity,
   balanced accuracy (equal to accuracy here, as every record has one positive and one negative) and the unknown
   share, each with a percentile-bootstrap 95% interval over records, `stats.cluster_bootstrap_mean`, B 10,000,
   `bootstrap_seed` 2 (seed string `j1:2`), a record's two questions drawn together, the same draws for every judge
   and arm scored on the same records. A model failure (no valid reply after the repair) is `unknown` and counts. A
   transport failure (timeout, network, HTTP error, size limit) leaves that record out of that (model, task) set and
   of both arms built on it; the comparators are compared on the same records. If more than `WITHHOLD_SHARE` 0.01
   of a set's records leave, the set and its two arms are withheld (`lab/j1.py:128, 503-515`).
8. **Paired differences,** by `lab.j1.paired` unchanged (`stats.paired_bootstrap`, B 10,000, seed `j1:2`), over the
   records both sides score, record by record:
   - each (model, arm) minus the lexical judge;
   - the lifts: B − A, C − A, D − A, D − B and D − C per model. B − A and D − C are lever 1's readings (the mapping,
     under each instruction); C − A and D − B are lever 2's (the instruction, under each verdict reading).
9. **The primary pair, the per-model primary arm, and the exploratory rest.**
   - **P = (a-4b, arm C).** The 86 verdict of rule 10 is read on P alone. Why P is this pair is under "Why these
     arms, and why this primary pair".
   - **The per-model primary arm is C for every model.** For each model, arm C's balanced accuracy interval is read
     against the lexical judge's balanced accuracy on the same records as J001's rule 7 reads it: **better** only
     when the interval's lower end is above it, **worse** only when its upper end is below it, otherwise **not told
     apart**. Three comparisons, no correction, as J001.
   - **Every other (model, arm) pair is exploratory:** arm A for all three models, and arms B and D, the declared
     mapping secondaries (decision 9 pending). Each is reported with the same statistics as P, including rule 10's
     pass test. A secondary that meets the pass test is a hypothesis for the next cycle's primary, never a result.
     The lifts of rule 8 are read as "helps" when the lower end is above 0, "hurts" when the upper end is below 0,
     otherwise "not told apart"; they decide nothing toward 86.
   - **The comparison count.** This cycle makes 12 (model, arm, cycle) comparisons: 4 arms × 3 models × 1 cycle. The
     running count since the loop began is therefore 12 after this cycle; J001 and L001 predate the loop and are not
     counted. The Runs entry prints it.
10. **The verdict rule.** Copied verbatim from LOOP-48H section 2.5 (lines re-wrapped; the words are its). Its "§7"
    and "§9" are that prompt's sections 7 (what needs a human) and 9 (the final report).

    - **One primary pair.** Each CHOICE file names exactly one primary pair P = (model, arm), chosen from earlier
      recorded results before the run. Every other (model, arm) pair in the run is a secondary, reported with the
      same statistics; a secondary that meets the pass test is a hypothesis for the next cycle's primary, never a
      result.
    - **Complete.** A verdict is read only when every planned record of the draw is scored, `measurement` is true,
      and the share of records dropped by transport failures is at most `WITHHOLD_SHARE` 0.01
      (`lab/j1.py:128, 503-515`). Otherwise the cycle is **incomplete**: record it, and the next cycle needs a new
      rule on a fresh draw.
    - **Pass test on P:** BA point estimate ≥ 0.860 **and** the paired difference to the lexical judge has its 95%
      lower end above 0 **and** the model's BA 95% lower end is above the predicate-only bound's point value computed
      on the same draw **and** the unknown share is reported.
    - **Success (86 reached):** P passes in this cycle **and** P, named as the primary in the next cycle's CHOICE
      file before that run, passes again on that cycle's fresh draw. A first pass is **provisional**, shipped path
      included; only a confirmed success ends the loop.
    - **Partial:** BA ≥ 0.860 but an interval clause fails; or BA < 0.860 but P beats the lexical judge with the
      lower end above 0. Report both numbers and continue.
    - **Failure:** BA below the lexical judge, or the interval to lexical includes 0. Report plainly: "P scored BA b
      [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p." Continue with the next lever.
    - **Multiplicity.** Every CHOICE Runs entry and the §9 report print the running count of (model, arm, cycle)
      comparisons made since the loop began. With 150 records the BA standard error is about 0.035, so a sampling
      peak can cross 0.860 on some draw; the confirmation rule exists for that reason.
    - **Secondary (report when available, never promote):** the chance-corrected share (BA_site − bound) /
      (BA_central − bound) against a hosted central reader, only when §7 allows one on real text.

    For this cycle: "complete" is read per (model, task) set, and P's set is a-4b's task N set; "the same draw" is
    the 150 records a-4b's task N set scored. The aggregate computes the verdict word for P (`pass_provisional`,
    `partial`, `failure` or `incomplete`) and, labelled exploratory, for every other pair, from the figures alone,
    so that no one reads it by hand. Success in the sense of the rule cannot be reached in this cycle.
11. **Reported beside the verdict, deciding nothing:** for every (model, arm): sensitivity, specificity, accuracy and
    the unknown share with intervals; the count of each answer pair, for positives and for negatives, before and
    after M; the verdict counts; failures by kind; per predicate, how often each judge confirmed it, for positives
    and for negatives (`by_predicate`); the comparators on the same records; seconds per judge call per task
    (median and 95th percentile from the ledgers) with the runner CPU model, a runner number, not site hardware; and
    arm A's figures beside J001 run 9's, labelled a between-draw difference.
12. **Units and limits** (sized in "Why the units are this size"):
    - one unit per model and part: 15 units; a unit judges its part's 60 questions twice, in two passes: first every
      question with the shipped task (arm A, and B by M), then every question with task N (arm C, and D by M);
      120 calls a unit;
    - each unit has 300 minutes and runs alone in its shard; `job_minutes` 330 gives a shard capacity of 305 and a
      job timeout of 325 (`SHARD_OVERHEAD_MINUTES` 25, `lab/request.py:174`); `max_parallel` 15 (the cap is 16,
      `lab/request.py:871`);
    - each unit stops at its budget and keeps what it judged (`stopped: budget`), as J001's runner does; a pass left
      unfinished leaves that (model, task) set incomplete for that part; a finished first pass stays complete;
    - **a (model, task) set is scored only when all 5 of its parts finished.** Otherwise its finished parts are a
      partial reading and decide nothing. Arms B and D are complete exactly when arms A and C are.
13. **What is written, and the guards: J001's, unchanged in kind.** Each verdict line holds the question id, the
    record ref, the kind, the predicate, the task, the whole reply (the two answers, kept because the data are
    public), the shipped verdict, the mapped verdict, the error kind and `scored`; never a narrative. The aggregate
    recomputes both verdicts from the reply with the one pinned `decide` and refuses a line that disagrees
    (`lab.j1.line_problem`'s check, extended to the mapped verdict). The plan artifact holds the labels and
    questions files, as J001's did (public complaint text and refs; the artifact, never the repository). Reports,
    step summaries, console lines and commits hold counts, scores and hashes only: no narrative, no record ref, no
    VIN, no city, no dealer, no operator name. The plan job's console gets the fixed lines of "The data and the
    draw", item 5, and the comparators' figures. The warm-up sends a generated record about no drawn record, with
    both tasks, and records `repeat_identical` (J001 rule 4; L001 K5).
14. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3). A set left incomplete gets no verdict from this
    run; judging it again is a new choice file. Any change to a setting, to N's text or to M is a new choice file.
    After the run, no reading other than arms A to D is made of the replies: anything else they suggest is a
    hypothesis for the next cycle's rule.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Models | a-0p5b, a-1p5b, a-4b; one run each; temperature 0 (the client), fixed server seed |
| Population | NHTSA complaints, pack `docs/collective/replay/vehicles/pack`, `NHTSA_MAKES` and `NHTSA_WINDOW` verbatim, j1 eligibility |
| Excluded | J001's 150 records: run 37996675559, artifact `lab-plan-37996675559-1`, labels `bc6d092d8bca…`, seed 1, n 150 |
| Labels | n 150, seed 2 (`random.Random("nhtsa:2")` over the eligible records less the excluded), codes removed |
| Questions | `lab.j1.build_questions`, seed 2, parts 5; `NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged |
| Arms | A `shipped`; B `shipped_mapped`; C `negation`; D `negation_mapped`; model calls for the two tasks only |
| Mapping M | `describes_predicate` `unclear` read as `no`; `mentions_entity` as answered; then the pinned `decide` |
| Task N | `judge_record_negation`, raw, 256 tokens, shipped schema, instructions as rule 3 (pinned by sha256) |
| Primary pair | P = (a-4b, C) |
| Per-model primary arm | C; A, B and D exploratory; 12 (model, arm, cycle) comparisons this cycle, 12 since the loop began |
| Comparators | lexical judge, record-blind control (`PRIOR_TOP`), predicate-only bound; all in the plan job |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 2 (`j1:2`), 95% percentile; paired differences with the same draws |
| Codes in the payload | empty in every arm; a record with codes is refused |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 600 s, 1 retry; no escalation |
| Units | 15 (3 models × 5 parts), 120 calls each, 300 minutes, one per shard; `job_minutes` 330; `max_parallel` 15 |
| Withhold | `WITHHOLD_SHARE` 0.01 per (model, task) set |
| Request | `lab/templates/accuracy.json`, copied to `lab/requests/accuracy-001.json`; `retention_days` 30; `provider` `llama-server` |

## Why these arms, and why this primary pair

**The yes-bias is the failure to fix.** In J001, a-0p5b and a-1p5b said the narrative described the component on
nearly every question, filed or not (145 and 145 of 150; 138 and 131 of 150). a-4b said yes on 97 positives and 37
negatives, so it reads something, but it answered unclear on 145 of its 300 questions, and the shipped `decide` makes
every unclear an `unknown`. At site level (L001 run 2) no model refuted anything: specificity 0.000 for all three,
because `decide` confirms a mine on any one yes-and-yes record and refutes only when no record describes the predicate
and fewer than half the replies are unclear (`verify.py:251-259`). A judge that cannot say no cannot score above 0.5
on a balanced draw, whatever it reads.

- **Arm A** is the control for everything else: J001's path on a fresh draw. Without it, a lift could be a between-draw
  difference. It is also what the product returns today.
- **Arm B (lever 1, decision 9)** tests the one reading that J001's counts predict can move a-4b: with unclear read as
  no, J001's counts give sensitivity 97/150 and specificity (94 + 19)/150, balanced accuracy 0.700, if the fresh
  draw behaves like J001's. That is the prior expectation of a secondary, not a result; the loop prompt notes that
  this lever alone cannot reach 0.86, because the 37 yes answers on negatives are fixed by it. For a-0p5b the mapping
  is expected to do nothing (it answered unclear on almost nothing), and for a-1p5b almost nothing (its 24 unknowns
  came from `mentions_entity`, which M leaves alone). B is a secondary because no call on decision 9 has been
  received; the loop prompt's default makes the shipped `decide` primary.
- **Arm C (lever 2, J001 trap 3)** attacks both residual errors through the instruction, leaving `decide` as shipped.
  The shipped text says `describes_predicate` is no when the record "says that it did not happen"; complaints state
  failures as negations ("did not deploy"), so a literal judge answers no on positives. N says such a report is a
  yes. The shipped text gives no rule for absence; N says a component the record never mentions is a no, not an
  unclear, and reserves unclear for a mentioned component whose problem cannot be told. If a-4b follows it, the 94
  unclear answers on negatives become refutes without any change to the product's verdict rule, and some of the 37
  yes answers on negatives (a component mentioned, no problem reported) may become no. No measurement of this lever
  exists; that is why it is tested.
- **Arm D** is C with the mapping: what remains unclear under N is read as no. It is the arm with the most changes and
  the least product realism; it is a secondary.
- **Why P = (a-4b, C).** From recorded results: a-4b is the only model whose answers separated positives from
  negatives (J001: 97 against 37 yes answers; the other two confirmed nearly everything, and all three had specificity
  0.000 in L001 run 2), so it is the only model for which a lever on unclear and negation can plausibly matter. Arm C
  is the first lever in the pinned order that the loop prompt's default allows as primary (the shipped `decide`), and
  it is the one whose effect has never been measured. Arm B's prior expectation (0.700 against a lexical judge near
  0.67) would most likely give "failure" under the verdict rule even if it held, since the paired interval would
  include 0 at this sample size; it stays the secondary that decision 9 can promote. Naming D would stack both
  changes on the untested one.
- **Why C is every model's primary arm.** One arm per model keeps J001's three-comparison headline; C is the lever
  under test; for a-0p5b and a-1p5b, J001's counts predict that M changes little, so C and D would be nearly the same
  reading. A C-arm lift for a-0p5b says whether a 0.5B model follows an instruction about absence at all; a null
  there says nothing about the instruction for larger models.
- **What is left out and why.** The schema variants of lever 3 (an evidence span with a verbatim-substring check,
  rationale before the answer, a symmetric two-part question) and the lexical ensemble of lever 4 (which needs an
  ablation comparator) are later cycles: four arms is the ceiling the lab's one-hour budget allows per cycle, and
  stacking more changes on an untested instruction would make a lift unreadable. The codes-present product-realism
  secondary (handoff Q10) is a later cycle's labelled secondary. The R003 settings and pack-v2 need the chief
  scientist (LOOP-48H section 7).

## Why the units are this size

- **The calls.** 15 units of 120 calls: 1,800 model calls in all, 600 per model (300 per task). Arms B and D add
  none.
- **The recorded per-call latency.** J001's judge calls on real complaint narratives (RESULTS run 9): a-0p5b 1.0 to
  1.5 s median, a-1p5b 1.4 to 3.2 s, a-4b 6.0 to 9.0 s, by runner CPU. L001 run 2's judge calls on MSHA narratives
  (shorter records): 0.5 s, 1.0 s and 3.5 s median. Task N adds about 90 words to the system text; the reply is still
  two words from a fixed list, capped at 256 tokens.
- **At J001's slowest medians,** a unit's 120 calls take a-0p5b 3.0 min, a-1p5b 6.4 min and a-4b 18.0 min. A unit's
  300 minutes is 150 s a call, about 17 times a-4b's slowest median; J001 gave 180 s a call and its slowest unit
  finished in well under its budget (the whole run took 22 minutes).
- **The tail.** RESULTS records no 95th percentile for judge calls on NHTSA text. For extraction calls with a-4b in
  R002 the 95th percentile was 177 s against a median of 25.8 s (CHOICE-R002, run 1); judge calls read the same
  narrative but write two words, so their tail should be the prompt's, not the reply's. Only the looseness of the
  budget covers it.
- **One unit per shard** keeps the shard matrix at 15 of the lab's 16, and lets a slow a-4b unit run to its budget
  without delaying another unit. The expected wall time is J001's order: about 25 to 35 minutes, dominated by
  provisioning, the warm-up and a-4b's second pass.
- If a pass does not finish anyway, rule 12 applies.

## How it is read

- **The verdict on P** is read by rule 10 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p."
- **The honest expectation.** a-4b scored 0.387 under the shipped path and the lexical judge 0.670; a pass needs
  0.860, a gain of 0.47 on one draw with a standard error near 0.035. A-4b's recorded counts put the mapping's
  reading near 0.70. The likely outcomes are failure or partial. The cycle still measures what the loop needs:
  lever 2's effect, lever 1's as a secondary, and J001's result on a second draw.
- **The per-model headline** (rule 9) says, for arm C, whether each model under the negation instruction judges better
  than, worse than, or not apart from the lexical judge, as J001's rule 7 said for the shipped instruction. "Better"
  supports a small model at the site on this kind of text, on the task the product gives it, with an instruction the
  product does not yet carry. "Worse" is a real negative for the instruction on that model.
- **The lifts** (rule 8) say what each lever did on each model. Lever 1 helps where B − A or D − C has its lower end
  above 0; lever 2 where C − A or D − B does. Read them with sensitivity and specificity: a lever that raises
  specificity by lowering sensitivity by the same amount (the model says no more often everywhere) moves balanced
  accuracy little and shows no reading; the answer-pair counts before and after M show whether the model tells
  positives from negatives or only moved its default answer.
- **Arm A against J001 run 9** is a replication on a fresh draw. Differences within about two standard errors
  (0.07) are sampling; a larger one says the two draws differ, and both are reported.
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. Under M, a judge that answers unclear everywhere
  refutes everything and scores 0.5: the mapping turns abstention into refutation and shows no reading by itself,
  which is why the pass test needs the lexical judge and the bound beaten. The record-blind control and the
  predicate-only bound are read as J001 reads them: neither decides anything; a model at or below the bound is not
  shown to read the record.
- **150 records give intervals about 0.07 wide.** Small differences between arms or models cannot be read.
- **This is one public field,** complaints written to a regulator, with the models possibly having seen them in
  training. A pass here would be a pass on public text the models may have seen; the Runs entry says so.

## Traps

1. **Filed codes are not truth.** A narrative can describe a component it was not filed under, and the reverse. No
   judge can reach 1.0, the key's noise ceiling is unmeasured, and 0.860 may sit above it for some predicates. The
   key costs every judge, the lexical one included.
2. **Old and new names.** The pack keeps three old-and-new name pairs as separate predicates (`TWINS`); a negative is
   never the other name of a filed component; a positive is the filed name. No text can tell the two names apart,
   and N's "component or system" wording does not change that.
3. **Negation is now the lever.** The shipped reading of "did not" stays in arms A and B; N's reading is fixed above
   and applies in C and D. A complaint that states a failure as a negation is a positive under the key; a complaint
   that mentions a component only to say it worked is a negative only if it was not filed under it. N can also make a
   model answer no on a positive whose problem it does not recognise as one. The lifts carry that cost.
4. **The codes are hidden.** In the product a record's codes go into the payload, and both instructions say they are
   statements too. Here every arm reads only the narrative and the structured vehicle. A codes-present arm is not
   run; it could only be a labelled secondary.
5. **The vehicle answer.** The vehicle is in every record's structured entities, so `mentions_entity` should be yes on
   every question. M does not touch it: a model that says no or unclear there gets `unknown` in every arm. In J001
   a-1p5b did so on 24 of 300 questions. The answer counts show how often it happens.
6. **Easy negatives.** Negatives are drawn at random, so most name a component the narrative never mentions. N tells
   the judge that absence is a no, which is exactly the easy case. Specificity can rise without any reading of the
   hard case, a component mentioned without a problem. Sensitivity weighs the same, and both are reported.
7. **Training data.** The models may have seen these public complaints, the fresh draw's included. That would help
   them, not the lexical judge. Exclusion of J001's records removes records these models judged, not records they
   may have read in training.
8. **Post-hoc readings.** The replies are stored, so other mappings, thresholds or combinations can be computed after
   the run. None counts: only arms A to D are readings of this cycle. Anything else is a hypothesis for the next
   cycle's rule on a fresh draw (rule 14). J001 is never re-scored.
9. **Tuning on the draw.** N's text, M, the seeds and the parts are fixed in this file before the draw exists. The
   plan job prints the comparators before any shard starts; those figures are never a reason to redraw. A seed change
   is a numbered amendment stating a cause independent of the printed figures, reviewed before the build.
10. **The mapping converts abstention into refutation.** Under M, "I cannot tell" counts as "not described". That is
    a product decision (decision 9) with a cost: a judge that abstains honestly on a hard positive loses it either
    way, and one that abstains on everything scores 0.5. M's lift on a-4b comes mostly from the 94 negatives it could
    not confirm, which is the easy case of trap 6.
11. **Prompt-cache effects.** Each shard's one server caches the prompt prefix (the system text, the task's
    instructions and the schema) across calls, and the two passes of a unit have different prefixes. At temperature 0
    with a fixed server seed the reply should not depend on the cache's state; the lab checks that only through the
    warm-up's `repeat_identical` on one generated record. Timings differ with cache hits, and the first call of each
    pass is slower; latency is reported per task and decides nothing.
12. **Instruction following is a capacity.** A 0.5B model may ignore N entirely (J001: yes on 145 of 150 both ways).
    A null C − A for a-0p5b is a result for that model, not for the instruction.
13. **Twelve pairs, one draw.** With a standard error near 0.035, the best of twelve (model, arm) readings on one draw
    is biased upward. The pass test is read on P alone; a secondary that passes is a hypothesis; the confirmation rule
    exists for this reason.
14. **The complaint file changes.** The labels come from the file as published on the day. The eligible list may
    differ from J001's day; the exclusion works on refs, so freshness holds either way, and a changed file is
    reported through mechanism 2's sha256.
15. **N is written for this pack.** "Component or system" fits vehicle complaints. It is a lab TaskSpec, not a
    product text: shipping it would need pack-neutral wording and a decision.
16. **One record a question.** The product pools a window of records into one verdict (`decide` over many records);
    this cycle judges one record at a time, so the pooling, the routes and the gate are not measured. L001 measures
    them.

## What it does not show

- No site-level verdict: how pooling, routing and the commit gate turn record verdicts into an alert's answer. That
  is L001's measurement, where the route-role baseline reads nothing and scores 0.808.
- No early warning. There are no outcomes here, no detection and no alert; this is reading, not signal.
- No partner data. These are public complaints written to a regulator, not a company's own records, and not another
  field or pack.
- Not the product's behaviour for arms B, C and D. The product runs arm A. The other arms are lab readings of declared
  changes, measured so that a decision can be made; none is shipped by this cycle.
- Not the hosted central reader of section 2.5's secondary; no key exists and none is assumed.
- Not a-4b with reasoning on, the R003 settings, pack-v2, a larger local model, or a drafted-terms payload (levers 5
  to 8).
- Nothing about Tesseract, ingestion or latency as a test (latency is reported, not judged).
- Not the key's noise ceiling, nor whether any judge could reach 0.860 on these labels.

## Decisions this does not make

- **Shipping any arm is the chief scientist's decision 9** (`HANDOFF-2026-10-09.md`, decisions table), and a change
  to `judge_task` is a product change of the same kind. This cycle measures declared arms through lab-side TaskSpecs
  and a lab-side mapping; the pinned `verify.py` (`judge_task`, `judge_payload`, `lexical_judge`, `decide`) is
  imported and never changed, and the product's record-level behaviour is arm A. The handoff's decisions table stays
  as it is; a call reaches this loop only as a message, recorded under `## Decisions received` in
  `docs/handoff/LOOP-48H-STATUS.md`.
- **The R003 settings** (`HANDOFF:329-331`) and **pack-v2** are the chief scientist's; a pack-v2 cycle would be
  labelled and never pooled toward 86 (LOOP-48H section 2.6, lever 7).
- **Decisions 1, 2 and 7** (Tesseract placement, routing, the embedder) are untouched; the accuracy track does not
  build Tesseract in.
- **The founder's alternative readings** of 86 (LOOP-48H section 2.7) are not adopted; the pinned reading runs by
  default, and the hour-0 ask stands.
- **The hosted central reader** needs the founder's secrets and a reviewed lab role; nothing here assumes it.
- **An MSHA record-level replication**, a codes-present secondary, the lever-3 schema variants and the lever-4
  lexical ensemble are left to later cycles' rules.

## Amendments

None yet. After review, amendments go in a dated section here, before any run, each saying what was, what is now and
why, as L001's K1 to K14 do. Where an amendment disagrees with the text above, the amendment wins.

## Runs

None yet. The entry for the first run will hold: the lab run id and its times (UTC), the request file and its
sha256 prefix, the commit, the aggregate job id and the report's sha256 prefix with "matching"; the plan job's fixed
line (`available`, excluded 150, eligible after exclusion, overlap 0, the mechanisms that verified, the labels' sha256
prefix) and the complaint file's sha256 prefix; the comparators on the fresh draw; one table over the 12 (model, arm)
pairs with balanced accuracy and its interval, sensitivity, specificity, the unknown share, the paired difference to
the lexical judge with its interval, the bound on the same records and the pass-test word; the verdict on P by rule
10, written plainly with the gap to 0.860; the three per-model headlines; the lifts of rule 8 with intervals; arm A
beside J001 run 9; the running comparison count (12); anything that stopped before a model ran; and the sentence
that the result is on public text the models may have seen in training.
