# Accuracy test A001: the first cycle toward 86, on a fresh draw of real complaints

The founder's goal is "loop until we hit 86". What 86 means is pinned in `docs/strategy/LOOP-48H.md`, section 2
(commit `cccadaf`): balanced accuracy 0.860 of the site verifier's verdicts against the filed-category key, at record
level, with the codes hidden, on a fresh pre-registered draw of 150 real public NHTSA complaints, scored exactly as
J001 was scored (`docs/collective/replay/vehicles/CHOICE-J001.md`), with the interval and confirmation rules of its
section 2.5. This file is the rule for the loop's first cycle. It is committed alone, before any code is written for
it and before any record of its draw exists. How the lab will run it (a new experiment kind, `j2`, on J001's pattern)
goes in `BUILD-A001.md`, which is written after this rule.

**Amended before any run, on 2026-10-11.** The section of that name, just before "Runs", answers the review of this
rule: A1 to A12 change "The data and the draw" (items 1 to 6), rules 1, 9, 12 and 13, the reading of rule 10 (its
words stay), the settings, traps 11 and 14 and the Runs entry, and declare the lab changes the build needs (a
workflow permission and an artifact download step among them). It was written after the review, before any of
A001's code and before any record of its draw existed. Where it disagrees with the text above it, it wins. The
pinned metric is not changed.

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

## Amended before any run, 2026-10-11

No A001 code exists, no request has been pushed, and no record of the cycle's draw exists. A review of this rule
against the lab's code and workflows found two blocking gaps, both in the freshness mechanism, and eleven smaller
ones. The changes are A1 to A12, numbered as this cycle's task asks (D001's A1 to A14 live in `CHOICE-D001.md` and
are cited with that file's name). Each says what was, why, and what is now, as L001's K1 to K14 do. Where the text
above disagrees with this section, this section wins.

The declaration still holds. Since the rule was committed, its author has read the files each item cites: the three
lab workflows, `lab/j1.py`, `lab/goldlabels.py`, `lab/prereg.py`, `lab/responder.py`, `lab/units.py`,
`lab/warmup.py`, `lab/aggregate.py`, `tools/l1/guard_probe.py`, `tests/lab/test_lab_workflow.py` and
`tests/lab/test_lab_guards.py`. No figure here comes from A001's data, which does not exist, and no reply of J001's
has been read.

**No finding asked to change the pinned metric, and none is changed.** 86 stays balanced accuracy 0.860 against the
filed-category key at record level with the codes hidden, on 150 fresh records scored as J001; the comparators stay
the lexical judge, the predicate-only bound and the record-blind control; rule 10's block stays word for word
LOOP-48H section 2.5. A5 to A7 add readings of that block to the paragraph after it, so that the aggregate computes
the verdict word without a hand reading. Two findings offered a choice between two fixes (A9, A1's download step) and
one offered an optional extra (A11); each item says which was taken and why.

### A1. Mechanism 1 as the lab can run it (The data and the draw, item 1; the settings; rule 13)

- **Was:** Item 1 said J001's plan artifact is downloaded "as `tools/l1/guard_probe.py` downloads another run's plan
  artifact", read `prereg/j1/labels.json` for `source`, `n`, `seed` and a `sha256` field, and took the 150 refs
  from `prereg/j1/questions.jsonl`. Nothing said where the download runs or what it needs.
- **Why that cannot run:**
  - `tools/l1/guard_probe.py` downloads nothing. It takes `--run1-artifact DIR`, "run 1's plan artifact, unpacked"
    (its docstring and `main`), a directory a workflow step fetched; its only network call fetches MSHA's file.
  - The lab workflow's token holds `permissions: contents: read` only (`.github/workflows/mycelic-lab.yml:14-15`),
    which cannot list or download another run's artifacts, and no token reaches the plan job's prereg step: the
    token goes to the provision job's server step alone (`:126`; `tests/lab/test_lab_workflow.py:179-183` pins it
    there).
  - `lab/j2labels.py` may not call the Actions API itself: `tests/lab/test_lab_guards.py:119-125` allows the API
    host and an `Authorization` header in `lab/provision.py` only.
  - So, as written, mechanism 1 fails on every run for a permissions reason, freshness rests on mechanism 2 alone,
    and the plan job stops the day the complaint file changes: the first run would not be the result, for a cause
    the rule claimed to cover.
  - The check trusted the `sha256` field of `labels.json`, a value the artifact asserts about itself.
- **Now:**
  - **The precedent** is the lab's own: `.github/workflows/lab-reaggregate.yml:18-20, 47-52` and
    `.github/workflows/l1-guard-probe.yml:17-19, 45-55` download another run's artifacts with
    `actions/download-artifact` (the pinned sha the lab workflow already uses), `run-id`, `name` and
    `github-token: ${{ github.token }}`, under `permissions: actions: read`.
  - **The workflow change, declared.** `mycelic-lab.yml` gets `permissions: {actions: read, contents: read}` at the
    top (read-only; no job-level permissions) and one plan-job step after `plan` and before `prereg`:
    `actions/download-artifact` with `name: ${{ steps.plan.outputs.j2_exclude_artifact }}`,
    `run-id: ${{ steps.plan.outputs.j2_exclude_run_id }}`, `github-token: ${{ github.token }}` and
    `path: ${{ runner.temp }}/lab-earlier/${{ steps.plan.outputs.j2_exclude_run_id }}`, run only when the run id
    output is not empty (it is empty for a plan without j2 units), with `continue-on-error: true`, so that a
    missing or expired artifact reaches `lab.j2labels` as an absent directory and a fixed message, never as a job
    failure without a plan error. The directory is never under `${{ runner.temp }}/lab-plan`: `lab/prereg.py:472`
    refuses a plan directory that already holds `prereg/`, and everything under `lab-plan` is uploaded as this
    run's plan artifact. `lab-earlier` is not uploaded, not scanned by `lab.msha guard` and not read by
    `lab.summary`. A shell step looping over runs was not chosen: the workflow test pins run blocks to lab commands,
    the guard test pins the API host to `lab/provision.py`, and with A3 each cycle needs one earlier artifact.
  - **The request names the excluded run.** The `j2` request block carries one `exclude` entry: the run id
    (37996675559), the artifact name (`lab-plan-37996675559-1`), the file inside it that lists the records
    (`prereg/j1/labels.jsonl`) and the sha256 prefix that file's bytes must have (`bc6d092d8bca`). `lab.request`
    validates it (an integer run id, an artifact name of the lab's shape, a lower-case hex prefix of at least 12
    characters, a file path from a fixed list), `lab.plan --github-output` echoes `j2_exclude_run_id` and
    `j2_exclude_artifact`, and the j2 preregistration writes it as `excluded_runs` with the counts found. The exact
    key names are the build's (`BUILD-A001.md`); their content is fixed here. A later cycle changes the request, not
    `lab/j2labels.py` (A3).
  - **The check, hardened.** `lab.prereg` passes the directory to `lab.j2labels` (`--earlier-plans`). It reads
    `<run id>/prereg/j1/labels.jsonl` and hashes its bytes: the sha256 must start with the request's prefix
    (`bc6d092d8bca`; the field in `labels.json` is not trusted). It cross-checks that sha256 and the file's size
    against the artifact's manifest `prereg/prereg.json` (`lab/prereg.py`: "the sha256 and size of every other file
    under `D/prereg`"), and `labels.json` must still say `source` `nhtsa`, `n` 150 and `seed` 1. The 150 refs are
    the `record.record_ref` of the labels file's lines; the refs of `prereg/j1/questions.jsonl` must be the same
    set, else the mechanism fails. Every read is `strict_load` inside `try`/`except`; any failure, a missing
    directory included, prints one fixed message (`mechanism 1: artifact absent`, `sha256 mismatch`, `manifest
    mismatch`, `refs mismatch`) and a count, never a value, a path or an exception's text. The downloaded directory
    is untrusted input: nothing from it is printed or written anywhere but into the exclusion set on the runner.
  - **Tests, declared.** `tests/lab/test_lab_workflow.py`'s pins change with the workflow: `permissions` equal to
    `{"actions": "read", "contents": "read"}` (`:104`); `github.token` in exactly two places, the provision server
    step's environment and the download step's `github-token` (`:179-183`); four `download-artifact` steps with a
    `name`, the fourth's being the j2 exclude output (`:250-253`). `tests/lab/test_lab_guards.py` is unchanged:
    `lab/j2labels.py` makes no network call. `tests/lab/test_lab_j2.py` pins the hardened check on a synthetic
    artifact directory: a changed byte in the labels file, a manifest that disagrees, a questions file with other
    refs and an absent directory each fail with their fixed message, and a consistent directory gives 150 refs.
  - Item 1 reads accordingly; the artifact's size, expiry and "the API listed it on 2026-10-11" stand.

### A2. The stop rule, read explicitly: one mechanism verifying suffices (items 2 and 3; trap 14; an ask)

- **Was:** Item 2 said mechanism 2 "verifies only when the labels' sha256 starts with `bc6d092d8bca`" and otherwise
  "fails and the plan job prints that it did"; item 3 said the plan job stops "if neither mechanism verifies". The
  metric section says that where this file and LOOP-48H section 2 disagree, that section wins.
- **Why that is not enough:** Section 2.3 holds two sentences in one bullet: "rebuild the seed-1 n-150 draw and stop
  unless its labels sha starts with `bc6d092d8bca`" and, two sentences later, "If neither mechanism can verify
  freshness, stop and ask". Read alone, the first stops the run the day NHTSA's file changes, whatever the artifact
  shows; the second lets one mechanism suffice. The rule adopted the second without saying that it was choosing, so
  the file contradicted itself on the one rule that decides whether the first run can be the result.
- **Now:**
  - **The reading, recorded before any run.** Mechanism 2 is a second check, not a second gate. Mechanism 1's refs
    are the records J001 judged: the record ref is the complaint file's own id column
    (`docs/collective/replay/vehicles/pack/mapping.json`, `record_ref`), the same in every version of the file, so
    removing those refs gives disjointness from J001 whatever the file holds today. When mechanism 1 verifies, the
    run proceeds; a rebuilt labels sha256 that does not start with `bc6d092d8bca` is printed as its prefix beside
    `bc6d092d8bca`, as evidence that the complaint file changed since 2026-10-09, and recorded in the Runs entry.
    Section 2.3's first sentence is read as the gate that applies when mechanism 1 did not verify, which is what
    its last sentence says. If the founder rules the strict reading before the run, a dated amendment adopts it,
    and the run then stops whenever the file changed.
  - **The ask,** one line, in the loop's next reply and under `## Open asks` of `docs/handoff/LOOP-48H-STATUS.md`
    when that file is created (the L001 run 2 record creates it): "NEEDS FOUNDER: section 2.3's stop rule is read
    as 'stop when neither mechanism verifies', not 'stop unless the rebuilt sha matches'; the artifact's refs are
    the records J001 judged (CHOICE-A001, A2)." Asked once; the loop does not block on it.
  - **Mechanism 2's call, pinned.** `lab.goldlabels.build_labels("nhtsa", NHTSA_PACK, 150, 1, fetch=<the
    preregistration's one cached fetch>)`: `lab.prereg` downloads the complaint archive at most once per
    preregistration and hands the same bytes to every labels build, so the rebuild, the exclusion and the new draw
    read one file, and the complaint file sha256 the plan job prints is that file's.
  - **Both verifying.** When both mechanisms verify, the plan job prints the size of the symmetric difference of
    their two ref sets (0 expected) and stops when it is above 0; item 3's "must be equal, else the plan job stops"
    is this count, printed.
  - Trap 14 reads: the exclusion works on refs, so freshness holds whenever mechanism 1 verifies; a changed file is
    reported through mechanism 2's sha256 and stops nothing by itself.

### A3. The excluded set carried forward (item 6; rule 13; Runs)

- **Was:** Item 6 said each later cycle lists every earlier cycle's run id and labels sha256 and excludes each "from
  its own plan artifact the same way".
- **Why:** J001's artifact expires 2026-11-08T22:00:03Z and each cycle's after 30 days (`retention_days` 30). By the
  third or fourth cycle freshness would rest on several artifacts, some expired, and on mechanism 2's byte-stable
  complaint file. One `download-artifact` step fetches one run's artifact (A1).
- **Now:**
  - The j2 plan job writes `prereg/j2/excluded.jsonl` into this run's plan artifact: one line per ref, refs only,
    sorted, the union of every excluded ref and this draw's 150 refs (300 lines this cycle), and prints its sha256
    prefix in the fixed line (a hash, never a ref). The Runs entry records that prefix. The artifact, never the
    repository, holds the file, as it holds the labels and questions files.
  - Each later cycle's request names the previous cycle's run, artifact, `prereg/j2/excluded.jsonl` and the sha256
    prefix its Runs entry printed; its plan job downloads that one artifact (A1's step), hashes the file's bytes,
    excludes every ref in it, and writes its own `excluded.jsonl` with its draw added. The chain survives J001's
    expiry as long as each cycle runs within 30 days of the previous one; if it does not, mechanism 1 cannot verify
    and the plan job stops and asks (section 2.3). Each later CHOICE still lists every earlier run id and labels
    sha256, as section 2.3 says, and mechanism 2's rebuild of J001's seed-1 draw stays as the independent check on
    the chain's oldest link.
  - Rule 13's list of what the plan artifact holds gains this file.

### A4. The eligible list, rebuilt and pinned equal (item 4; rule 1)

- **Was:** Item 4 said the eligible list is built "exactly as `lab.goldlabels.nhtsa_lines` builds it" and named no
  check that the copy stays equal to the pinned code.
- **Why:** `nhtsa_lines` (`lab/goldlabels.py:74-93`) builds `eligible` and samples it in one function, with no
  exclusion parameter, and `lab/goldlabels.py` is pinned. `lab/j2labels.py` must therefore re-implement the
  eligible build (`goldlabels.py:79-87`), and a re-implementation can drift.
- **Now:**
  - `lab/j2labels.py` re-implements the eligible build of `goldlabels.py:79-87`: `map_rows` with the pack's
    `mapping`, `synthetic=False`; a non-blank narrative, exactly one vehicle, at least one specific component code;
    codes emptied and `site` set to `PUBLIC_SITE`; sorted by `record_ref`. It then removes the excluded refs and
    samples `random.Random("nhtsa:2").sample(eligible_unexcluded, 150)`, sorted by `record_ref`, in label-file
    lines of `goldlabels`' shape, and writes a labels record with `available` (the eligible count before exclusion,
    `goldlabels.py:143`), `excluded`, `eligible_after_exclusion`, `overlap`, the mechanisms' results and the
    complaint file's sha256 beside the fields `lab.j1` and `e1_extract.read_labels` read.
  - `tests/lab/test_lab_j2.py` pins, on a small synthetic export and the vehicle pack: with no exclusion and the
    same `n` and seed, `j2labels`' lines equal `goldlabels.nhtsa_lines(pack, rows, n, seed)[0]` byte for byte;
    with exclusions, the draw equals `sorted(random.Random("nhtsa:2").sample(eligible_unexcluded, n),
    key=record_ref)` over the synthetic eligible list less the excluded refs, and holds no excluded ref; and fewer
    than `n` eligible unexcluded records refuse with a fixed message. If the equality test fails, the build is
    wrong, not the pinned code.
  - The excluded runs reach the plan from the request block (A1), are echoed into the j2 preregistration and the
    plan summary as `excluded_runs` (run id, artifact, sha256 prefix, refs found: counts and hashes only), and reach
    the report the same way. Rule 1's "J001's 150 records excluded as 'The data and the draw' says" reads with A1
    to A3.

### A5. The verdict word, in one order (rule 10, "For this cycle")

- **Was:** The paragraph after the verbatim block said the aggregate computes the verdict word "from the figures
  alone" and named the four words.
- **Why:** The verbatim words overlap. With BA at or above 0.860 and the paired interval to the lexical judge
  including 0, "Partial" ("BA ≥ 0.860 but an interval clause fails") and "Failure" ("the interval to lexical
  includes 0") both hold. Without a precedence the word is not computable in that cell; `lab.j1.paired` returns
  `ci_low` and `ci_high` only (`lab/j1.py:518-534`), no word.
- **Now:** The paragraph gains: the aggregate evaluates the words in the order `incomplete`, `pass_provisional`,
  `partial`, `failure`, and the first whose condition holds is the verdict word; the figures behind every clause
  (BA with its interval, the paired difference to the lexical judge with its interval, the bound's point value on
  the same records, the unknown share, the dropped share and the parts finished) are printed beside it. So a BA at
  or above 0.860 whose interval to the lexical judge includes 0 is `partial`, the more specific clause, and is
  reported with both numbers as the block says; the remainder (BA below 0.860 and the lower end of the difference
  at or below 0) is `failure`, so the four words cover every cell. The verbatim block is unchanged.

### A6. "Complete", read as the lab records it (rule 10, "For this cycle"; rules 7 and 12)

- **Was:** The block requires that "every planned record of the draw is scored" and that the dropped share is at
  most 0.01; rule 7 kept a set with one dropped record. The paragraph did not say which reading the aggregate
  implements.
- **Why:** Read literally, one record left out by a transport failure is not scored, the set is incomplete, and the
  0.01 clause can never apply. Read as the lab records it, "scored" and "dropped" are two of the three states of
  `lab.j1.split_records` (`lab/j1.py:406-421`: scored, dropped, not judged), and `headline_for` returns
  `incomplete` only when a part did not finish and `withheld` when `dropped_share` is above `WITHHOLD_SHARE`
  (`:503-515`).
- **Now:** The paragraph gains: "every planned record scored" is read as every part of the (model, task) set
  finished (every one of its calls for that task made, A9; `stopped` null) with `measurement` true and
  `records_not_judged` 0; records dropped by transport failures are governed by the 0.01 clause (`lab.j1.score`'s
  `dropped_share`, dropped over scored plus dropped). So one dropped record of 150 (0.0067) leaves the set complete
  and two (0.0133) withhold it, which is rule 7 as written. A set that is `incomplete` or `withheld` gets the word
  `incomplete`, and the figures say which.

### A7. How P meets "chosen from earlier recorded results" (rule 9; "Why P")

- **Was:** Rule 9 named P = (a-4b, arm C) and "Why P" gave the reasoning, but not as a satisfaction of the block's
  clause that P is "chosen from earlier recorded results before the run". Arm C has no recorded result.
- **Why:** P is fixed before the draw exists, so there is no selection after the fact; but a later reader could
  contest the clause, and the rule should say how it is met.
- **Now:** Rule 9 gains: the clause is met as follows. The model comes from J001 run 9's answer counts (a-4b is the
  only model that separated positives from negatives: yes on 97 positives and 37 negatives, 145 unclear) and from
  L001 run 2's specificity 0.000 for all three models. The arm comes from the pinned lever order (section 2.6)
  under the section 7 default (shipped `decide` primary, mappings secondary): lever 1 has no measurable headroom to
  0.860, because J001's 37 yes answers on negatives are fixed under it, so lever 2 is the first arm the default
  allows as primary. No (model, arm) pair measured on a fresh draw exists yet; the first cycle's P cannot be one.
  If the chief scientist's decision 9 arrives before the run, a dated amendment names P = (a-4b, D) or (a-4b, B),
  as the declaration already allows.

### A8. The line check, without touching `lab/j1.py` (rule 13; rule 3, arm B)

- **Was:** Rule 13 said the aggregate refuses a line that disagrees with the recomputed verdicts by
  "`lab.j1.line_problem`'s check, extended to the mapped verdict".
- **Why:** `lab.j1.line_problem` returns `keys` for any line whose key set is not exactly `LINE_KEYS`
  (`lab/j1.py:364-369`), and the aggregate's `_j1_lines` does the same (`lab/aggregate.py:754-761`); a j2 line
  carries at least `task` and the mapped verdict, so neither can be reused as is, and `lab/j1.py` is pinned
  (section 2.3). "Extended" read as a change to pinned code.
- **Now:**
  - `lab/j2.py` defines `LINE_KEYS = (*lab_j1.LINE_KEYS, "task", "mapped_verdict")` and its own `line_problem`,
    which (1) requires `task` to be one of the two task names; (2) projects the line to `lab_j1.LINE_KEYS` and calls
    `lab_j1.line_problem(question, projected)` unchanged, so the shipped verdict is recomputed by the one pinned
    `decide` over `lab_j1.stand_in`; (3) recomputes `mapped_verdict` as `lab_j1.verdict(lab_j1.stand_in(ref),
    M(reply))`, `unknown` when the reply is null, and refuses a mismatch. `lab.j1` is imported and unchanged; j2
    verdict lines are read by a j2 reader, and the comparators' files (`lexical.jsonl`, `prior.jsonl`) stay
    j1-shaped and are read by `_j1_lines` as J001's are.
  - Arms B and D are j1-shaped views: copies of arm A's and arm C's lines with `verdict` replaced by
    `mapped_verdict` and, for the answer counts after M, `describes_predicate` replaced by M's reading.
    `lab.j1.score` and `lab.j1.paired` read only the keys they name (`lab/j1.py:443-481, 518-534`), so the views,
    and the lines with their two extra keys, pass through them unchanged.
  - Rule 13 reads: "the aggregate recomputes the shipped verdict with `lab.j1.line_problem` on the shipped fields
    and the mapped verdict with the pinned `decide` on the mapped reply, and refuses a line that disagrees with
    either".

### A9. Task N first in every unit (rule 12; "Why the units"; trap 11)

- **Was:** Each unit judged its 60 questions in two passes, "first every question with the shipped task, then every
  question with task N"; "a finished first pass stays complete".
- **Why:** Any late stop (budget, runner lost, server down after the first pass; `lab/j1.py:790-795` stops the loop
  before the next question) would complete arm A, the control, and leave arm C, hence P, incomplete; by rule 10 the
  cycle is `incomplete` and the draw is spent. The order protected the control at the expense of the primary.
- **Now:**
  - Every unit runs task N first (arm C, and D by M), then the shipped task (arm A, and B by M). A stop in the
    second pass leaves P's set complete and the control's incomplete for that part: the verdict on P is read (its
    pairing is with the lexical judge, computed in the plan job for every record), and arm A, the lifts of rule 8
    and the replication of J001 are recorded as incomplete and decide nothing, which they never did toward 86.
  - A part is finished for a (model, task) set when every one of its 60 calls for that task was made (a model
    failure after the repair is a call made: `unknown`, scored). `run.json` records `complete` per task.
  - The interleaved alternative (shipped then N for each question, so that both sets stop together) was not chosen:
    it protects the control, which decides nothing, by exposing P to every stop, and it alternates two prompt
    prefixes on every call.
  - Trap 11 reads: the two passes keep one prefix each, N's first; the first call of each pass is slower; the
    warm-up's last request is the shipped task's probe (A11), so the N pass starts on a cold prefix. Timings are
    reported per task and decide nothing.

### A10. The fake provider answers task N (section 2.3's wiring list; the dry run)

- **Was:** The wiring list (section 2.3, and the build it implies) did not name `lab/responder.py`.
- **Why:** The fake provider used by the template dry run and by the fake-server tests dispatches on the schema
  name, which is the task name, and refuses an unknown name by dropping the connection (`lab/responder.py:3-4,
  15-18, 71-75`), after which the participation check makes the unit invalid. Every arm-C call
  (`judge_record_negation`) in a dry run would fail, and the dry run the rule requires before merge could not pass.
- **Now:** `lab/responder.py` registers the negation task under its own name with the shipped task's handler
  (`self._handlers[lab_j2.NEGATION_TASK] = self._handlers[JUDGE_TASK]`, in the pack branch and in the
  `pack_free_judge` branch alike), and `lab/units.py`'s `with_pack` list (`:1112`) gains `j2`, so a j2 dry-run unit
  gets a `FakeServer` with the vehicle pack. In a dry run the fake answer to N is therefore the lexical judge's, so
  a dry run never measures the instruction: arms C and D there equal arms A and B (the lexical judge never answers
  unclear, so M changes nothing). `BUILD-A001.md` says so, and `tests/lab/test_lab_j2.py` pins that both names are
  answered and that an unknown name is still refused.

### A11. What the warm-up records for N (rule 13; trap 11)

- **Was:** Rule 13 said the warm-up sends a generated record "with both tasks, and records `repeat_identical`".
- **Why:** The lab's probe sends the first fitting task's request body twice, tasks sorted by task name
  (`lab/warmup.py:9-10, 44-45`), so `repeat_identical` is recorded for `judge_record` and not for
  `judge_record_negation`; the text overstated what the lab records for N.
- **Now:** Rule 13 reads: the warm-up calls both tasks once on a generated record about no drawn record (its `fit`
  and `calls` steps; `lab/warmup.py` lists both tasks for a j2 unit) and records `repeat_identical` for the shipped
  task's probe, the only probe the lab sends. No second probe is declared: the probe's purpose is to stop on
  `reasoning_content` and to show the server's determinism on one record, and both hold for the one server the two
  tasks share; a j2-only probe would be a reviewed lab change with no reading attached.

### A12. The build, as amended

The lab changes the build makes, all unpinned, added to section 2.3's list: `lab/j2.py`, `lab/j2labels.py`;
`lab/request.py` (the `j2` block with `exclude`), `lab/plan.py` (the two outputs), `lab/prereg.py`
(`--earlier-plans`, the j2 preregistration with `excluded_runs` and `prereg/j2/excluded.jsonl`), `lab/units.py` (the
j2 adapter with both task names, `with_pack`), `lab/warmup.py` (both tasks for j2), `lab/responder.py` (A10),
`lab/aggregate.py`, `lab/summary.py`, `lab/notes.py`; `.github/workflows/mycelic-lab.yml` (`actions: read`, the
download step); `lab/templates/accuracy.json`; `tests/lab/test_lab_j2.py` and the pins of
`tests/lab/test_lab_workflow.py` named in A1; `docs/lab/REFERENCE.md`, `lab/requests/README.md`,
`docs/lab/INTEGRATION.md`, `BUILD-A001.md`. `lab/j1.py`, `lab/goldlabels.py` and every pinned module are imported
and unchanged. No privacy guard is weakened: `actions: read` is read-only; the downloaded artifact is read on the
runner and never printed, uploaded or scanned into a summary; the console gets fixed lines and counts;
`tests/lab/test_lab_guards.py` is unchanged.

### The settings, as amended

| Setting | Value |
|---|---|
| Excluded | J001's 150 records from `lab-plan-37996675559-1` (run 37996675559), `prereg/j1/labels.jsonl` whose bytes' sha256 starts with `bc6d092d8bca`, checked against the artifact's manifest, refs cross-checked with its `questions.jsonl`; downloaded by the plan job under `actions: read` into `lab-earlier/<run id>`; named in the request block (A1) |
| Stop rule | mechanism 1 verifying suffices; mechanism 2's mismatch is printed and recorded; neither verifying stops the plan job before any model runs; both verifying prints the symmetric difference (0 expected; above 0 stops) (A2) |
| Carried forward | `prereg/j2/excluded.jsonl` in the plan artifact: every excluded ref plus the draw's 150, refs only; its sha256 prefix in the Runs entry (A3) |
| Units | 15 (3 models × 5 parts), 120 calls each: the 60 questions with task N first, then with the shipped task; 300 minutes, one per shard (A9) |
| Verdict word | `incomplete`, `pass_provisional`, `partial`, `failure`, in that order, the first that holds; the figures printed beside it (A5) |
| Complete | every call of the (model, task) set's five parts made, `measurement` true, `records_not_judged` 0, dropped share at most 0.01 (A6) |

### Runs entry, as amended

The first run's entry adds: which mechanisms verified and the symmetric difference count when both did; the rebuilt
seed-1 labels' sha256 prefix beside `bc6d092d8bca` (A2); the `excluded.jsonl` sha256 prefix (A3); `excluded_runs` as
the plan summary printed it (counts and hashes); and, per (model, task) set, the parts finished per task (A9).

## Runs

### Run 1 (lab run 38114522160, 2026-10-11)

**The run.** Lab run [38114522160](https://github.com/anovruzov/NeuralGraph/actions/runs/38114522160) (workflow
`mycelic-lab`), created 05:21:31 UTC, completed `success` at 05:48:06 UTC. Request `lab/requests/accuracy-001.json`,
sha256 `b623ec781075…`, pushed alone as commit `3361910` at 05:21:29 UTC; the rule is `90d2084`, amended `146431c`,
both before the run. Plan job 114396657335 (05:21:34 to 05:22:18, success). Fifteen shards, `s001-a-0p5b` to
`s015-a-4b`, all `sealed` with exit code 0, started at 05:23:00 and 05:23:01; the last finished at 05:46:28. Aggregate
job 114400537232 (05:46:30 to 05:48:05, success); its log prints `report/report.md` between the `MYCELIC-LAB` markers
as 155 lines with sha256 `cfd7749b3719…`, matching (recomputed from the log's lines with the timestamp prefix
stripped, as `lab/summary.py` computes it). Every figure below is copied from that block or from the plan job's log.

**The plan job's fixed line**, printed before any model ran: `available` 58319; excluded 150; eligible after
exclusion 58169; overlap with the excluded set 0; mechanisms `artifact=verified` `rebuild=verified` (the job's own
words: "both mechanisms verified; symmetric difference 0"); labels sha256 `623780b8a1de…` (150 records, seed 2);
questions sha256 `2ba18ea89bb7…` (300 questions in 5 parts); prereg sha256 `fb8f06442693…`; excluded refs file
(`prereg/j2/excluded.jsonl`, A3) sha256 `9a948cbf2cd9…`; complaint file sha256 `a5a5de2d31f3…`. The rebuilt seed-1
draw verified against `bc6d092d8bca…` (the console printed the mechanism's state, not a separate prefix). Excluded
runs, as the plan summary printed them: J001, run 37996675559, artifact `lab-plan-37996675559-1`, 150 records. Every
(model, task) set finished 5 of 5 parts for both tasks, and `records left out` is 0 in every arm.

**The comparators on the fresh draw** (plan job, the same 150 records and 300 questions):

| Comparator | Balanced accuracy [95% interval] | Sensitivity | Specificity |
|---|---|---|---|
| Lexical judge | 0.663 [0.627, 0.703] | 0.333 | 0.993 |
| Record-blind control (decides nothing) | 0.520 [0.463, 0.577] | | |
| Predicate-only bound (a fitted point value, decides nothing) | 0.577 | | |

**The 12 (model, arm) pairs** on the same records (cluster bootstrap over records, B 10,000; the paired difference is
the arm minus the lexical judge, record by record; the pass test is rule 10's, the verdict word rule 10's in A5's
order; P in bold):

| Model | Arm | Balanced accuracy [95% interval] | Sensitivity | Specificity | Unknown share | Minus the lexical judge [interval] | Bound on the same records | Pass test | Verdict word |
|---|---|---|---|---|---|---|---|---|---|
| a-0p5b | A `shipped` | 0.493 [0.480, 0.507] | 0.933 | 0.053 | 0.000 | -0.170 [-0.210, -0.133] | 0.577 | no | `failure` |
| a-0p5b | B `shipped_mapped` | 0.493 [0.480, 0.507] | 0.933 | 0.053 | 0.000 | -0.170 [-0.210, -0.133] | 0.577 | no | `failure` |
| a-0p5b | C `negation` | 0.483 [0.447, 0.520] | 0.553 | 0.413 | 0.000 | -0.180 [-0.233, -0.127] | 0.577 | no | `failure` |
| a-0p5b | D `negation_mapped` | 0.483 [0.447, 0.520] | 0.553 | 0.413 | 0.000 | -0.180 [-0.233, -0.127] | 0.577 | no | `failure` |
| a-1p5b | A `shipped` | 0.467 [0.440, 0.493] | 0.880 | 0.053 | 0.073 | -0.197 [-0.243, -0.150] | 0.577 | no | `failure` |
| a-1p5b | B `shipped_mapped` | 0.467 [0.440, 0.493] | 0.880 | 0.053 | 0.073 | -0.197 [-0.243, -0.150] | 0.577 | no | `failure` |
| a-1p5b | C `negation` | 0.497 [0.480, 0.513] | 0.973 | 0.020 | 0.010 | -0.167 [-0.207, -0.127] | 0.577 | no | `failure` |
| a-1p5b | D `negation_mapped` | 0.497 [0.480, 0.513] | 0.973 | 0.020 | 0.010 | -0.167 [-0.207, -0.127] | 0.577 | no | `failure` |
| a-4b | A `shipped` | 0.307 [0.263, 0.350] | 0.560 | 0.053 | 0.560 | -0.357 [-0.403, -0.310] | 0.577 | no | `failure` |
| a-4b | B `shipped_mapped` | 0.667 [0.620, 0.710] | 0.560 | 0.773 | 0.000 | 0.003 [-0.047, 0.050] | 0.577 | no | `failure` |
| **a-4b** | **C `negation` (P)** | **0.577 [0.530, 0.623]** | 0.833 | 0.320 | 0.137 | **-0.087 [-0.140, -0.033]** | 0.577 | **no** | **`failure`** |
| a-4b | D `negation_mapped` | 0.653 [0.610, 0.697] | 0.833 | 0.473 | 0.017 | -0.010 [-0.063, 0.043] | 0.577 | no | `failure` |

**The verdict on P, by rule 10: failure.** P scored BA 0.577 [0.530, 0.623]; gap to 0.860 is 0.283; lexical 0.663
[0.627, 0.703]; bound 0.577. In A5's order: the set is complete (a-4b's task N set finished 5 of 5 parts, measurement
true, 0 records left out), so not `incomplete`; the point estimate is below 0.860, so not `pass_provisional`; the
lexical judge was not beaten (the paired difference -0.087 [-0.140, -0.033] has its whole interval below zero), so not
`partial`; `failure` holds. P's balanced accuracy equals the predicate-only bound's point value, and its lower end
(0.530) is below it; its unknown share is 0.137. Success in the sense of the rule could not be reached in this cycle,
and was not. Section 2.5: continue with the next lever. The first run is the result (rule 14).

**The three per-model headlines** (rule 9: arm C against the lexical judge's 0.663 on the same records): a-0p5b
**worse** (0.483 [0.447, 0.520], upper end below 0.663); a-1p5b **worse** (0.497 [0.480, 0.513]); a-4b **worse**
(0.577 [0.530, 0.623]). Under the negation instruction no model judges as well as word matching on this draw.

**The lifts of rule 8**, paired by record over 150 records ("helps" when the lower end is above 0, "hurts" when the
upper end is below 0, else "not told apart"; they decide nothing toward 86):

| Model | B − A (lever 1, the mapping under the shipped instruction) | C − A (lever 2, the instruction under the shipped reading) | D − A (both) | D − B (lever 2 under the mapping) | D − C (lever 1 under the negation instruction) |
|---|---|---|---|---|---|
| a-0p5b | 0.000 [0.000, 0.000], not told apart | -0.010 [-0.047, 0.030], not told apart | -0.010 [-0.047, 0.030], not told apart | -0.010 [-0.047, 0.030], not told apart | 0.000 [0.000, 0.000], not told apart |
| a-1p5b | 0.000 [0.000, 0.000], not told apart | 0.030 [0.000, 0.060], not told apart | 0.030 [0.000, 0.060], not told apart | 0.030 [0.000, 0.060], not told apart | 0.000 [0.000, 0.000], not told apart |
| a-4b | 0.360 [0.323, 0.393], helps | 0.270 [0.217, 0.327], helps | 0.347 [0.290, 0.407], helps | -0.013 [-0.063, 0.037], not told apart | 0.077 [0.047, 0.107], helps |

Read with sensitivity and specificity, as "How it is read" asks: on a-4b the mapping under the shipped instruction
(B − A) left sensitivity at 0.560 and moved specificity from 0.053 to 0.773 while the unknown share went from 0.560 to
0.000, the conversion of abstention into refutation that trap 10 names; the negation instruction (C − A) moved
sensitivity from 0.560 to 0.833 and specificity from 0.053 to 0.320, with an unknown share of 0.137; under the
negation instruction the mapping (D − C) moved specificity from 0.320 to 0.473 at the same sensitivity, 0.833. Arm B
and arm D of a-4b are not told apart from each other (D − B) or from the lexical judge. On a-0p5b the negation
instruction moved the default answer (sensitivity 0.933 to 0.553, specificity 0.053 to 0.413) and balanced accuracy
by -0.010; on a-1p5b it moved the answers toward yes (sensitivity 0.880 to 0.973, specificity 0.053 to 0.020) and
balanced accuracy by 0.030; neither is told apart from zero. The mapping changed no score of a-0p5b or a-1p5b: B equals
A and D equals C on every record (lifts 0.000 [0.000, 0.000]). Arms B and D are secondaries: a-4b's B (0.667 [0.620,
0.710], pass test no) and D (0.653 [0.610, 0.697], pass test no) are hypotheses for the next cycle's rule, never
results, and no reading other than arms A to D was made of the replies (rule 14).

**Arm A beside J001 run 9** (`docs/lab/RESULTS.md`, Run 9, cited as written there; J001's bound from "What the
evidence says" above). A between-draw difference, reported, not tested; "How it is read" calls differences within
about 0.07 sampling:

| Judge | J001 run 9 (150 records, labels `bc6d092d8bca…`): balanced accuracy [interval], sensitivity, specificity, unknown share | A001 run 1, arm A (150 fresh records, labels `623780b8a1de…`): the same | Difference of the two point values |
|---|---|---|---|
| a-0p5b | 0.500 [0.500, 0.500], 0.967, 0.033, 0.000 | 0.493 [0.480, 0.507], 0.933, 0.053, 0.000 | -0.007 |
| a-1p5b | 0.477 [0.447, 0.503], 0.867, 0.087, 0.080 | 0.467 [0.440, 0.493], 0.880, 0.053, 0.073 | -0.010 |
| a-4b | 0.387 [0.340, 0.433], 0.647, 0.127, 0.483 | 0.307 [0.263, 0.350], 0.560, 0.053, 0.560 | -0.080 |
| Lexical judge | 0.670 [0.627, 0.713], 0.380, 0.960, 0.000 | 0.663 [0.627, 0.703], 0.333, 0.993, 0.000 | -0.007 |
| Record-blind control | 0.527 [0.467, 0.587] | 0.520 [0.463, 0.577] | -0.007 |
| Predicate-only bound | 0.580 | 0.577 | -0.003 |

The two smaller models and every comparator moved by 0.010 or less between the draws. a-4b's arm A is 0.080 lower on
the fresh draw, a little beyond the 0.07 that "How it is read" calls sampling; its unknown share is 0.560 against
0.483 and its sensitivity 0.560 against 0.647. Both figures stand. Nothing here says which draw is the odd one.

**The comparison count.** 12 (model, arm, cycle) comparisons this cycle (4 arms × 3 models × 1 cycle); 12 since the
loop began (0 before this cycle; J001 and L001 predate the loop and are not counted). The aggregate printed
"comparisons since the loop began: 12 (12 this cycle, 0 before)".

**Nothing stopped before a model ran.** The plan job's guard (`l1 guard: no l1 units`), the exclusion check and the
draw passed; no shard failed; no call failed (`records left out` 0 in every arm); no unit stopped at its budget: every
unit finished both passes, the longest shard (`s011-a-4b`) in 1377.2 s of wall time against a 300-minute budget.

**Model-call latency, from the ledgers** (one shared GitHub-hosted runner per shard, a runner number and not site
hardware; compare only within a CPU model; a unit's two tasks ran on the same server):

| Model | Runner CPU | Calls per task | Median ms, shipped task / negation task | 95th percentile ms, shipped / negation |
|---|---|---|---|---|
| a-0p5b | AMD EPYC 7763 | 120 | 1521.8 / 1561.0 | 2721.4 / 2754.1 |
| a-0p5b | AMD EPYC 9V74 | 120 | 955.9 / 986.0 | 1386.3 / 1399.7 |
| a-0p5b | Intel Xeon Platinum 8573C | 60 | 1093.8 / 1135.7 | 1803.0 / 1812.0 |
| a-1p5b | AMD EPYC 7763 | 120 | 3140.4 / 3179.3 | 4787.3 / 4858.5 |
| a-1p5b | AMD EPYC 9V45 | 120 | 1428.2 / 1349.8 | 2223.3 / 2231.5 |
| a-1p5b | Intel Xeon 6973P-C | 60 | 1613.0 / 1597.2 | 2622.9 / 2736.4 |
| a-4b | AMD EPYC 7763 | 180 | 9275.1 / 9431.5 | 16209.7 / 16427.0 |
| a-4b | Intel Xeon Platinum 8370C | 120 | 8211.6 / 8637.7 | 11754.1 / 12020.0 |

Within a CPU model the two tasks' medians differ by less than 0.5 s for every model. The shards ran on six CPU
models; the figures of different rows are not one comparison.

**Public text.** This result is on public text the models may have seen in training: real NHTSA complaint
narratives, read with their codes hidden and scored against the filed codes, which are not checked labels. A pass here
would have been a pass on such text; this failure is a failure on it.
