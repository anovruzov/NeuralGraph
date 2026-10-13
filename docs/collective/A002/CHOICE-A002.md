# Accuracy test A002: the second cycle toward 86, two schema variants and a lexical composite on a fresh draw

The founder's goal is "loop until we hit 86". What 86 means is pinned in `docs/strategy/LOOP-48H.md`, section 2
(2.1 to 2.7): balanced accuracy 0.860 of the site verifier's verdicts against the filed-category key, at record
level, with the codes hidden, on a fresh pre-registered draw of 150 real public NHTSA complaints (300 questions),
scored exactly as J001 was scored (`docs/collective/replay/vehicles/CHOICE-J001.md`), with the interval's lower end
above the lexical judge and above the predicate-only bound, one primary (model, arm) pair per cycle, and confirmation
on the next cycle's fresh draw. This file is the rule for the loop's second cycle. It is committed alone, before any
code is written for it and before any record of its draw exists. How the lab will run it (a new experiment kind,
`j3`, on A001's `j2` pattern) goes in `BUILD-A002.md`, which is written after this rule and its review.

Nothing here redefines the target, the metric, the data class, the draw, the comparators, the interval rule or the
confirmation rule. Site-level balanced accuracy stays rejected as primary.

**What the evidence says.** Every result so far is cited as written in its record and is never re-scored here.

- **J001 run 9** (lab run 37996675559; `docs/lab/RESULTS.md`, Run 9; labels sha256 `bc6d092d8bca…`), 150 NHTSA
  complaints, codes hidden: balanced accuracy 0.500 [0.500, 0.500], 0.477 [0.447, 0.503] and 0.387 [0.340, 0.433]
  for a-0p5b, a-1p5b and a-4b, against the lexical judge's 0.670 [0.627, 0.713] (sensitivity 0.380, specificity
  0.960), the record-blind control's 0.527 [0.467, 0.587] and the predicate-only bound's 0.580. a-4b said yes on 97
  of 150 positives and 37 of 150 negatives and answered unclear on 51 positives and 94 negatives.
- **A001 run 1** (lab run 38114522160; `docs/collective/A001/CHOICE-A001.md`, "## Runs", Run 1; `docs/lab/RESULTS.md`,
  Run 11; labels sha256 `623780b8a1de…`), 150 fresh complaints with J001's excluded, codes hidden, four arms on three
  models, 12 (model, arm, cycle) comparisons, the first of the loop:
  - the primary pair P = (a-4b, negation instruction) scored 0.577 [0.530, 0.623] against the lexical judge's 0.663
    [0.627, 0.703] on the same records, paired difference -0.087 [-0.140, -0.033]; the verdict word is **failure**;
    its balanced accuracy equals the predicate-only bound's point value 0.577 and its lower end 0.530 is below it;
    sensitivity 0.833, specificity 0.320, unknown share 0.137;
  - a-4b with the shipped instruction read through the unclear-to-no mapping scored 0.667 [0.620, 0.710], paired
    0.003 [-0.047, 0.050] to the lexical judge: **not told apart**; sensitivity 0.560, specificity 0.773, unknown
    share 0.000; a-4b negation mapped scored 0.653 [0.610, 0.697], paired -0.010 [-0.063, 0.043]; sensitivity 0.833,
    specificity 0.473, unknown share 0.017;
  - a-4b under the shipped instruction and the shipped verdict rule scored 0.307 [0.263, 0.350], sensitivity 0.560,
    specificity 0.053, unknown share 0.560;
  - the mapping lifts a-4b by 0.360 [0.323, 0.393] under the shipped instruction and by 0.077 [0.047, 0.107] under
    the negation instruction; the negation instruction lifts a-4b by 0.270 [0.217, 0.327] under the shipped reading
    and -0.013 [-0.063, 0.037] under the mapping;
  - a-0p5b and a-1p5b scored 0.467 to 0.497 on every arm (a-0p5b 0.493 [0.480, 0.507] and 0.483 [0.447, 0.520];
    a-1p5b 0.467 [0.440, 0.493] and 0.497 [0.480, 0.513]); the mapping changed no score of either (lifts 0.000
    [0.000, 0.000]); the negation instruction moved them by -0.010 [-0.047, 0.030] and 0.030 [0.000, 0.060], not
    told apart from zero;
  - the comparators on that draw: lexical judge 0.663 [0.627, 0.703] (sensitivity 0.333, specificity 0.993),
    record-blind control 0.520 [0.463, 0.577], predicate-only bound 0.577;
  - secondaries that met no pass test are hypotheses, not results; every one of A001's secondaries met none.

The pattern across J001, L001 run 2 (`RESULTS.md`, Run 10: specificity 0.000 for all three models at site level) and
A001 is the same: the judges confirm too much. Under the best measured instruction, a-4b refutes 48 of 150 negatives
(specificity 0.320, A001 run 1, arm C) while confirming 125 of 150 positives (sensitivity 0.833). The residual error
that a lever must fix is a yes on a negative, and every arm below is aimed at it.

**What this cycle tests.** Levers 3 and 4 of the pinned lever order (LOOP-48H section 2.6; levers 1 and 2 were
A001's and are measured): two schema variants of the lab-side negation instruction, each a new TaskSpec with its own
max tokens, an evidence-span field with a verbatim-substring check read as a declared reply mapping, a
rationale-before-answer field, and a composite judge that calls the pinned lexical judge, with the ablation
comparators lever 4 requires. Predicate descriptions are not run this cycle; the reason is under "Why these arms".
One primary pair is named below. By the pinned rule, a pass in this cycle can only be provisional; confirmation
needs the next cycle.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen J001's result (RESULTS run 9; CHOICE-J001, Runs), A001's result (CHOICE-A001, Runs, Run 1; RESULTS
  run 11), L001 runs 1 and 2 (RESULTS run 10), RESULTS runs 1 to 11, D002 and D003, the handoff
  (`docs/handoff/HANDOFF-2026-10-09.md`), the loop prompt (`docs/strategy/LOOP-48H.md`) and the loop's status log
  (`docs/handoff/LOOP-48H-STATUS.md`).
- It has not seen any record of this cycle's draw. The draw does not exist: it is made in the plan job on a runner,
  from the complaint file as published on the day of the run, after J001's and A001's records are removed. The
  sandbox cannot reach NHTSA.
- It has not read J001's or A001's stored replies. They sit in those runs' artifacts, which this environment did not
  download. The two variant instructions, the schemas, the check and the composite rule below were written from
  A001's recorded figures (the answer counts by kind that its Runs entry prints, its sensitivities, specificities and
  unknown shares) and from A001's traps, not from any reply text. The split of A001's unknown answers between
  positives and negatives is in the run's report, not in its Runs entry, and was not read; where the arithmetic below
  needs it, it gives a range.
- No call on the handoff's decision 9 (how a site verifier's "unclear" counts) has been received:
  `docs/handoff/LOOP-48H-STATUS.md` lists it under "Open asks" and names no decision under "Decisions received". The
  loop prompt's default applies (section 7: "shipped `decide` primary, declared mappings secondary"). How that default
  is read for the verbatim-substring check is declared in rule 9 and asked once. A call received before the run is
  adopted by a dated amendment before any run; one received after the run bears on the next cycle.
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
  (`mycelic/collective/inference/runtime.py:166-167`) with a lab-side TaskSpec scored by the same pinned `decide`.
  Any change of data class gives a result that can never count toward 86. No arm may place the judged record's
  codes, its filed categories or anything derived from them in the payload, the task text or a reply mapping.
- **The comparators** (section 2.4), on the same records, paired, with 95% cluster-bootstrap intervals of the
  difference: the lexical judge (`lexical_judge`, `verify.py:171`; J001 0.670 [0.627, 0.713]; A001 0.663 [0.627,
  0.703]), the predicate-only bound (`lab/j1.py:346-361`; J001 0.580; A001 0.577; a point value fitted to the draw's
  answers, no interval) and the record-blind control (`PRIOR_RULE`, `lab/j1.py:133-135`; J001 0.527 [0.467, 0.587];
  A001 0.520 [0.463, 0.577]). This cycle adds, for the composite arms only, the two ablation comparators of lever 4
  (rule 6); they are comparators, not arms, and they decide nothing but the composite arms' extra pass clause.
- **Rejected as primary** (gameable): site-level balanced accuracy (the route-role baseline scores 0.808 [0.667,
  0.938] reading no record, L001 runs 1 and 2), codes visible to the judge (it reads the key), and a plain ratio to
  the lexical judge.
- **The secondary** (BA_site − bound) / (BA_central − bound) against a hosted central reader needs a key the lab does
  not have (`NHTSA_HOSTED`, `lab/request.py:234`; LOOP-48H section 7). It is not run and is never assumed. No hosted
  model is named or used anywhere in this cycle.

## The data and the draw

**NHTSA complaints, as J001 and A001 drew them, and nothing else in this cycle.** The reasons are A001's ("The data
and the draw"): the pinned definition names them; 150 records give 300 questions and 150 clusters with a balanced
accuracy standard error near 0.035; the key's noise is shared by every judge; J001's pipeline (`lab/j1.py`,
`lab/goldlabels.py`) is pinned and importable, so the questions, the comparators and the scoring of this cycle are
J001's code, called unchanged; and A001's exclusion machinery (`lab/j2labels.py`) is importable, so the eligible
list, the draw and the artifact check are A001's code, called unchanged.

**A fresh, verifiable draw (section 2.3), with two earlier cycles excluded.** The lab draws
`random.Random("nhtsa:<seed>").sample(eligible, n)` from a complaint file downloaded live in the plan job
(`lab/goldlabels.py:90, 133`), so a new seed alone gives no disjointness, and record refs may never be committed. The
exclusion happens on the runner, in the j3 plan job, in a new module `lab/j3labels.py` that imports `lab/j2labels.py`
unchanged. Every earlier cycle is excluded by the artifact mechanism and by the rebuild mechanism:

1. **J001's 150 records, mechanism 1 (the artifact).** The plan job downloads J001's plan artifact
   `lab-plan-37996675559-1` (run 37996675559; retention 30 days, until 2026-11-08T22:00:03Z; CHOICE-A001, item 1 and
   A1) into `${{ runner.temp }}/lab-earlier/37996675559` with `actions/download-artifact` under the workflow's
   read-only `actions: read`, as A001's plan job did. `lab.j2labels.earlier_refs` is called unchanged with the entry
   (file `prereg/j1/labels.jsonl`, sha256 prefix `bc6d092d8bca`): the file's bytes must hash to the prefix, agree
   with the artifact's manifest `prereg/prereg.json`, its refs must be `prereg/j1/questions.jsonl`'s and its
   `labels.json` must say `source` `nhtsa`, `n` 150, `seed` 1 (A1's hardened check). Outcome `verified` gives the
   set **J1** of 150 refs; any other outcome is one of `lab.j2labels.OUTCOMES`, printed with a count, never a value.
2. **J001's 150 records, mechanism 2 (the rebuild).** `lab.j2labels.rebuild_refs(fetch, "bc6d092d8bca")`, unchanged:
   J001's seed-1, n-150 draw rebuilt from today's complaint file through the preregistration's one cached fetch;
   `verified` when the labels' sha256 starts with `bc6d092d8bca` (RESULTS run 9), giving the set **R1**; else
   `sha256 mismatch`, with the rebuilt prefix printed beside the pinned one (A2). A001's plan job verified this
   rebuild on 2026-10-11 (CHOICE-A001, Run 1: `rebuild=verified`).
3. **A001's 150 records, mechanism 1 (the artifact).** The plan job downloads A001's plan artifact
   `lab-plan-38114522160-1` (run 38114522160, attempt 1; retention 30 days from 2026-10-11, so until about
   2026-11-10) into `${{ runner.temp }}/lab-earlier/38114522160` by a second download step of the same shape (the
   workflow change is declared under "The build plan"). The check is A1's, on a j2-shaped artifact: the file
   `prereg/j2/labels.jsonl` must hash to the prefix `623780b8a1de` (CHOICE-A001, Run 1: "labels sha256
   `623780b8a1de…`"), agree with the manifest, and its `labels.json` must say `source` `nhtsa`, `n` 150, `seed` 2
   with `n` equal to the refs found; its refs must be the refs of `prereg/j2/questions.jsonl` (sha256 prefix
   `2ba18ea89bb7`, CHOICE-A001, Run 1). Outcome `verified` gives the set **A1** of 150 refs. In the same artifact the
   chain file `prereg/j2/excluded.jsonl` (A3; sha256 prefix `9a948cbf2cd9`, CHOICE-A001, Run 1) is read by
   `lab.j2labels.earlier_refs` unchanged with its own entry; `verified` gives the set **K** of 300 refs, A001's
   chain: J001's 150 and A001's 150.
4. **A001's 150 records, mechanism 2 (the rebuild).** A001's draw was `random.Random("nhtsa:2").sample` over the
   eligible list less J001's 150 refs (CHOICE-A001, rule 1 and A4). The rebuild repeats it: `lab.j2labels.draw(
   eligible, J001_set, 150, 2)` over `lab.j2labels.eligible_records` of today's file (the same cached fetch), where
   `J001_set` is J001's established set of item 5; the bytes of `lab.j2labels.label_lines` over the sample verify
   when their sha256 starts with `623780b8a1de`, giving the set **R2**; else `sha256 mismatch`, printed with the
   rebuilt prefix beside the pinned one. Without a J001 set (item 5 stops first) the outcome is `not attempted`.
5. **The stop rules, read now for two cycles** (this settles the cycle-2 limit BUILD-A001.md records under "Known
   limits of the build": A001's symmetric-difference stop would stop every second cycle, because the chain holds 300
   refs and J001's rebuild 150).
   - **J001's set** is established when J1 or R1 verified; when both verified they must be equal (the symmetric
     difference printed, 0 expected; above 0 the plan job stops). **A001's set** is established when A1 or R2
     verified; when both verified they must be equal (printed, 0 expected; above 0 stops). When K verified it must
     equal the union of the two established sets (the symmetric difference printed, 0 expected; above 0 stops).
   - If either cycle has no verifying source, the plan job stops before any model runs, and the loop asks (an open
     ask in `LOOP-48H-STATUS.md`); it does not run.
   - **The excluded set** is the union of J001's set and A001's set: 300 refs expected.
   - One mechanism verifying per cycle suffices (A2's reading, kept): the record ref is the complaint file's own id
     column (`docs/collective/replay/vehicles/pack/mapping.json`, `record_ref`), the same in every version of the
     file, so removing the refs the artifacts give is disjointness from both earlier draws whatever the file holds
     today; a rebuild that does not verify says the complaint file changed since that cycle and stops nothing by
     itself. The complaint file's sha256 is printed beside A001's `a5a5de2d31f3…` (CHOICE-A001, Run 1).
6. **The draw.** `lab.j2labels.eligible_records` builds the eligible list exactly as `lab.goldlabels.nhtsa_lines`
   builds it (pinned equal byte for byte by A001's test, A4: the makes `NHTSA_MAKES` and the window `NHTSA_WINDOW` of
   `goldlabels.py:49-50` verbatim; a narrative, one vehicle, at least one specific component code; codes removed;
   sorted by `record_ref`); `lab.j2labels.draw(eligible, excluded, 150, 3)` removes the 300 excluded refs and samples
   `random.Random("nhtsa:3").sample` of 150, sorted by `record_ref`, in label-file lines of `goldlabels`' shape, so
   `e1_extract.read_labels` and `lab.j1` read them unchanged. The labels record carries `available`, the excluded
   count, the eligible count after exclusion, the overlap (0, else the plan job stops), every source's outcome and the
   complaint file's sha256.
7. **The chain, carried forward (A3).** The plan job writes `prereg/j3/excluded.jsonl`: one `{"record_ref"}` line per
   ref, sorted, refs only, the union of the 300 excluded refs and this draw's 150 (450 lines expected), and prints its
   sha256 prefix. The next cycle names this run's artifact, this file and that prefix, and excludes every earlier
   cycle as this one does: by its own artifact and its own rebuild, chained in seed order. Every later CHOICE lists
   every earlier run id and labels sha256 prefix; for this cycle the list is: J001, run 37996675559, labels
   `bc6d092d8bca`, seed 1; A001, run 38114522160, labels `623780b8a1de`, seed 2, chain `9a948cbf2cd9`.
8. **What is printed:** one fixed line with the `available` count (the eligible records, `goldlabels.py:143`;
   A001's day: 58,319), the excluded count (300 expected), the eligible count after exclusion, the overlap count
   between the new draw and the excluded set (0 expected), the outcome of each of the five sources (J1, R1, A1, R2,
   K) and the symmetric differences that were computed, the new labels' sha256 prefix, the chain file's prefix and the
   complaint file's prefix. Never a ref. If fewer than 150 eligible unexcluded records remain, the plan job stops, and
   no further fresh draw is pre-registered. Then the comparators' figures (rule 6), before any shard starts; those
   figures are never a reason to redraw (LOOP-48H section 6.3).

## The rule

1. **Records: 150 fresh NHTSA complaints, labels seed 3.** `lab.j3labels` over `lab.j2labels`, source `nhtsa`, the
   pack `docs/collective/replay/vehicles/pack`, the makes, the window and the eligibility rule of `lab.goldlabels`
   verbatim, J001's and A001's 300 records excluded as "The data and the draw" says, `random.Random("nhtsa:3").sample`
   of 150. The labels are built from the complaint file as published on the day of the run; the file's sha256 and
   the labels' sha256 are preregistered. Each label record has its codes removed (`lab/j1.py:275-279` refuses one
   that still holds them).
2. **Questions: two per record, J001's code, seed 3, five parts.** `lab.j1.build_questions(pack, labels, seed=3,
   parts=5)`, unchanged: the positive is one filed predicate (`random.Random("j1:3:<record_ref>:positive")`), the
   negative follows the other records' positives (`NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged, `lab/j1.py:118-130`).
   Record `i` of 150 is in part `i * 5 // 150 + 1`: 30 records and 60 questions a part. The questions file's sha256
   is preregistered. 150 positives and 150 negatives: 300 questions per task.
3. **The tasks: three, declared now; model calls are made for these only.** Every task reads the shipped payload,
   `verify.judge_payload` of the record with its codes empty (`lab.j1.payload`), through `Runtime.run(task, payload,
   schema, ref=...)`, and every verdict is the pinned `verify.decide` over the one record (`lab.j1.verdict`) applied
   to the reply's two answers, exactly as J001 and A001.
   - **Task N, `judge_record_negation`:** A001's arm C task, unchanged: `lab.j2.negation_task()`, data class `raw`,
     256 output tokens (`JUDGE_MAX_TOKENS`, `verify.py:87`), the shipped schema (`verify.judge_schema`:
     `mentions_entity` and `describes_predicate`, each `yes`, `no` or `unclear`), instructions
     `lab.j2.NEGATION_INSTRUCTIONS` (the
     blockquote of CHOICE-A001 rule 3, pinned by sha256). It is this cycle's control: A001's primary arm, replicated
     on a fresh draw, so that every lift is paired within the draw.
   - **Task E, `judge_record_evidence`** (lever 3, the evidence span): data class `raw`, **512 output tokens**, the
     schema S_E and the instructions below, fixed now, verbatim. S_E is the shipped schema plus one string property
     `citation`, declared first: `{"type": "object", "additionalProperties": false, "required": ["citation",
     "describes_predicate", "mentions_entity"], "properties": {"citation": {"type": "string"}, "mentions_entity": A,
     "describes_predicate": A}}` with `A = {"type": "string", "enum": ["yes", "no", "unclear"]}`. The instructions
     are N's text with the `citation` sentences inserted before the last sentence:

     > Judge one record of this site against one question. The question names an entity (its type, its id and the
     > aliases it may be written as) and a predicate. mentions_entity: yes when the record names that entity in its
     > text or among its structured entities, no when it does not, unclear when you cannot tell. describes_predicate:
     > the predicate names a component or system of the entity. Answer yes when the record reports a problem,
     > failure, defect or safety concern with that component or system, including a report that it did not work, did
     > not deploy, did not engage, stopped working or failed (the record's codes are statements too). Answer no when
     > the record does not mention that component or system, or mentions it only to say that it worked or was not
     > involved; a component the record never mentions is a no, not an unclear. Answer unclear only when the record
     > mentions that component or system and you cannot tell whether it reports a problem with it. citation: copy,
     > exactly as it is written in the record's text, the shortest passage that reports the problem with that
     > component or system; when the record reports no such problem, or you cannot find such a passage, write an
     > empty string. Give the citation first, then the two answers. A yes without a passage copied from the record
     > counts as a no. Judge only this record.

   - **Task R, `judge_record_rationale`** (lever 3, the rationale before the answer): data class `raw`, **512 output
     tokens**, the schema S_R and the instructions below, fixed now, verbatim. S_R is the shipped schema plus one
     string property `analysis`, declared first: `{"type": "object", "additionalProperties": false, "required":
     ["analysis", "describes_predicate", "mentions_entity"], "properties": {"analysis": {"type": "string"},
     "mentions_entity": A, "describes_predicate": A}}`. The instructions are N's text with the `analysis` sentences
     inserted before the last sentence:

     > Judge one record of this site against one question. The question names an entity (its type, its id and the
     > aliases it may be written as) and a predicate. mentions_entity: yes when the record names that entity in its
     > text or among its structured entities, no when it does not, unclear when you cannot tell. describes_predicate:
     > the predicate names a component or system of the entity. Answer yes when the record reports a problem,
     > failure, defect or safety concern with that component or system, including a report that it did not work, did
     > not deploy, did not engage, stopped working or failed (the record's codes are statements too). Answer no when
     > the record does not mention that component or system, or mentions it only to say that it worked or was not
     > involved; a component the record never mentions is a no, not an unclear. Answer unclear only when the record
     > mentions that component or system and you cannot tell whether it reports a problem with it. analysis: before
     > answering, write two or three sentences that say which component or system the record reports a problem with,
     > whether that is the component or system the predicate names, and what in the record's text shows it; then
     > give the two answers. Judge only this record.

   - **Why 512 tokens, and no length keyword.** The shipped cap of 256 (`verify.py:87`) bounds a two-word reply; a
     citation of up to a few dozen words plus the two answers, or two or three sentences of analysis plus the two
     answers, fits well inside 512, and a reply cut at the cap (`finish_reason` `length`) gets the runtime's one
     repair asking for a shorter object (`mycelic/collective/inference/tasks.py`, `repair_message`;
     `runtime.py:175-179`); a second cut is a model failure, `unknown`, scored. Neither schema uses `minLength`,
     `maxLength` or `pattern`: those are the keywords some servers reject (`mycelic/collective/schemacheck.py:31`,
     `STRICT_KEYWORDS`, dropped from the reduced transport), so S_E and S_R are the same bytes under the `full` and
     the `reduced` transport (`runtime.py:312-316`), and the only length bound is the task's `max_tokens`
     (`client.py:493-499` sends it on every call). `TaskSpec` accepts up to 32,768 (`tasks.py`, `MAX_TASK_TOKENS`).
   - **Field order.** The new property is first in each schema's insertion order and first in alphabetical order
     (`analysis`, `citation` sort before `describes_predicate` and `mentions_entity`), so it comes first both in the
     schema the request body carries (`client.py:493-513` serialises the schema dict as given) and in the canonical
     schema the system text shows (`tasks.py`, `render_messages`, `canonical_dumps`). Whether the server's grammar
     makes the model write it first is the server's; the lab records each reply's first key (the runtime parses the
     reply with `json.loads`, `mycelic/collective/inference/jsonparse.py`, which keeps the document's key order) and
     the aggregate reports the share of replies that begin with the new field, per task. That share is reported; it
     decides nothing.
   - **Codes never reach the judge in any task.** The payload's codes list is empty (the labels hide them; the runner
     refuses a record that holds any). E's and R's texts name no code, category or filed label. The payload is the
     shipped one for all three tasks: no arm adds a key to it.
4. **The two reply mappings and the reply predicate, declared now.** Each reads the reply, and the grounding check
   reads the payload's text; none reads the codes, the filed categories, the question's predicate or anything
   derived from them.
   - **M, the unclear-to-no mapping** (A001's `lab.j2.MAPPING`, unchanged): a reply's `describes_predicate` `unclear`
     is read as `no`; `mentions_entity` is left as answered; nothing else changes.
   - **The grounding predicate g(reply, text).** Let `text` be the payload's `record.text`: the narrative after
     `truncate` at `max_input_chars` 6,000 (`verify.py:162-168`; `docs/collective/replay/vehicles/pack/vocabulary.json`,
     `extraction.max_input_chars`), exactly the string the model received inside the data block. Let `norm(s)`
     collapse every run of whitespace in `s` to one space, strip it, and case-fold it (`str.casefold`); nothing else
     (no punctuation removed, no accents folded). The citation is **grounded** when `norm(citation)` has at least 4
     characters and occurs as a substring of `norm(text)`. The 4-character floor is a floor against a vacuous check
     (a single character occurs in every narrative), not a measure of evidence quality; the median citation length
     is reported. Case folding and whitespace collapse are declared because the comparison is of a model's copy with
     a narrative, and a copy that differs only in case or spacing is still the record's text; any other difference
     (a changed word, a paraphrase, a passage from another record, an invented passage) is not grounded.
   - **G, the grounding mapping:** a reply's `describes_predicate` `yes` is read as `no` when the citation is not
     grounded; `yes` with a grounded citation, `no` and `unclear` are left as answered; `mentions_entity` is left as
     answered. G and M act on disjoint answers (G on `yes`, M on `unclear`), so they commute; the mapped grounded arm
     applies G then M.
   - M, g and G are constants of `lab/j3.py`, pinned by their text in the preregistration. The build pins g by a test
     on the cases "How it is read" lists.
5. **The arms: nine, declared now, all on the one model (rule 5); one primary pair.** Model calls are made for the
   three tasks only; every arm is a reading of one task's replies, or a join of one arm's verdicts with a plan-job
   comparator's, reply by reply, before the pinned `decide` or at the verdict (composite arms). Letters name the
   lifts of rule 8.
   | Letter | Arm | Task | Reading before the pinned `decide` |
   |---|---|---|---|
   | N | `negation` | N | the two answers as answered (A001's arm C, the control) |
   | D | `negation_mapped` | N | M (A001's arm D) |
   | E | `evidence` | E | the two answers as answered; the citation ignored |
   | **Eg** | **`evidence_grounded`** | **E** | **G** (the primary arm) |
   | Egm | `evidence_grounded_mapped` | E | G, then M |
   | R | `rationale` | R | the two answers as answered; the analysis ignored |
   | Rm | `rationale_mapped` | R | M |
   | C | `composite` | E (and the lexical judge) | at the verdict: `confirm` when the lexical judge's verdict on the question is `confirm`, else Eg's verdict |
   | Cm | `composite_mapped` | E (and the lexical judge) | at the verdict: `confirm` when the lexical judge's verdict on the question is `confirm`, else Egm's verdict |
   - **The composite rule (lever 4), exactly.** For each question `q`: let `L(q)` be the lexical judge's verdict
     line for `q` written by the plan job (`prereg/j3/lexical.jsonl`, `lab.j1.lexical_verdicts` over the pinned
     `verify.lexical_judge`, rule 6), and `X(q)` the model arm's verdict (Eg for C, Egm for Cm; `unknown` when the
     model failed). `composite(q) = confirm if L(q) == confirm else X(q)`. So a composite says yes whenever the
     lexical judge does, and otherwise defers to the model; its answer-pair counts are the model's. It calls the
     pinned lexical judge through the plan job's lines, joined by question id; the lab holds no other lexical judge.
   - **The ablation comparators, declared (lever 4):**
     - **ablation A, `lexical_or_no`:** the same composite with the model's reply replaced by the constant reply
       `{"mentions_entity": "yes", "describes_predicate": "no"}`, whose verdict under the pinned `decide` over the one
       record is `refute`: `confirm` where the lexical judge confirms, else `refute`. It inherits the lexical judge
       and nothing else; on a draw where the lexical judge never answers unknown (J001 and A001: unknown share
       0.000, RESULTS runs 9 and 11) it is the lexical judge's verdicts exactly;
     - **ablation B, `lexical_or_record_blind`:** the same composite with the model's reply replaced by the
       record-blind control's verdict for the question (`lab.j1.prior_verdicts`, `PRIOR_RULE`, `PRIOR_TOP`
       unchanged): `confirm` where the lexical judge confirms, else the control's verdict.
     Both are computed in the plan job, before any model runs, on every record, and in the aggregate on the records
     each composite arm scored; both are paired with each composite arm by `lab.j1.paired` unchanged. The pass test
     of a composite arm (rule 10) additionally requires the paired difference to ablation A **and** to ablation B to
     have their 95% lower ends above 0, so that a composite passes only where the model's replies add to what the
     lexical judge and a reader that reads nothing already give. Both are declared because the lever text allows
     either and the stricter reading costs nothing.
   - **The primary pair P = (a-4b, Eg `evidence_grounded`).** Why is under "Why these arms, and why this primary
     pair". **Every other arm is a secondary**, reported with the same statistics, including rule 10's pass test,
     and a secondary that meets the pass test is a hypothesis for the next cycle's primary, never a result.
6. **The judging path: the product's, through the lab's pinned server.** As A001's rule 4: `Runtime.run` with its one
   repair after an invalid reply and no escalation, at boundary `site:lab`, data label `public`, through the lab's
   pinned model server on one runner; HTTP deadline 600 s and 1 retry; the client sends temperature 0 on every call
   and the server runs with a fixed seed (J001 rule 4), so one run per model. The routing document routes the three
   task names to the unit's one endpoint, without escalation; a unit refuses a routing that routes any of them
   elsewhere or escalates. What `SiteVerifier.answer` adds around these functions (boundary checks, budgets, master
   data, secrets, storage, the pooling of many records into one verdict) is not used.
7. **Models: a-4b alone** (`lab/models.json`, unchanged: `--reasoning off`), one run. a-0p5b and a-1p5b are not run
   this cycle; the reason is under "Why these arms".
8. **The comparators, in the plan job, before any model runs,** as J001's rule 5 and A001's rule 6, on the fresh
   draw: the lexical judge (`lab.j1.lexical_verdicts`), the record-blind control (`lab.j1.prior_verdicts`, `PRIOR_TOP`
   unchanged), the predicate-only bound (`lab.j1.prior_bound`), and this cycle's two ablation comparators (rule 5),
   each scored on every record and, in the aggregate, on each arm's scored records. The plan job prints their
   figures. Those figures are never a reason to redraw.
9. **Scoring, against the filed codes,** as J001's rule 6 and A001's rule 7, by `lab.j1.score` unchanged on each
   arm's j1-shaped view: a verdict is correct when it is `confirm` on a positive or `refute` on a negative;
   `unknown` is never correct; sensitivity, specificity, balanced accuracy (equal to accuracy here) and the unknown
   share, each with a percentile-bootstrap 95% interval over records, `stats.cluster_bootstrap_mean`, B 10,000,
   `bootstrap_seed` 3 (seed string `j1:3`), a record's two questions drawn together, the same draws for every judge
   and arm scored on the same records. A model failure (no valid reply after the repair) is `unknown` and counts. A
   transport failure (timeout, network, HTTP error, size limit) leaves that record out of that task's set and of
   every arm built on it; the comparators are compared on the same records. If more than `WITHHOLD_SHARE` 0.01 of a
   set's records leave, the set and its arms are withheld (`lab/j1.py:128, 503-515`). The composite arms are built on
   task E's set and share its completeness.
10. **Paired differences,** by `lab.j1.paired` unchanged (`stats.paired_bootstrap`, B 10,000, seed `j1:3`), over the
    records both sides score, record by record:
    - each arm minus the lexical judge (nine);
    - the lifts, each named by the lever it reads: `E_minus_N` (the citation field, answers read as shipped),
      `Eg_minus_E` (the verbatim check), `Eg_minus_N` (field and check together: P over the control),
      `Egm_minus_Eg` (the mapping under the check), `Egm_minus_D` (field and check under the mapping), `R_minus_N`
      (the rationale), `Rm_minus_D` (the rationale under the mapping), `D_minus_N` (the mapping under N: A001's
      D − C, replicated), `C_minus_Eg` and `Cm_minus_Egm` (the lexical disjunction over the grounded arms);
    - the ablation differences `C_minus_ablation_A`, `C_minus_ablation_B`, `Cm_minus_ablation_A`,
      `Cm_minus_ablation_B` (the composite arms' extra pass clauses).
11. **The primary pair, the headline arm, and the exploratory rest.**
    - **P = (a-4b, Eg).** The 86 verdict of rule 12 is read on P alone.
    - **How P meets "chosen from earlier recorded results before the run."** The model: a-4b is the only model
      whose answers separated positives from negatives in J001 (yes on 97 positives and 37 negatives, RESULTS run 9)
      and the only one any lever moved in A001 (lifts 0.270 [0.217, 0.327] and 0.360 [0.323, 0.393] against 0.000
      and -0.010 to 0.030 for the other two, CHOICE-A001 Run 1). The arm: under the best measured instruction
      a-4b's recorded residual is specificity 0.320 at sensitivity 0.833 (A001 run 1, arm C), that is, yes answers on
      negatives; the verbatim-substring check is the lever in the pinned order whose only action is to turn a yes
      that cannot be grounded in the record into a no, and lever 3 is the next lever in that order. No (model, arm)
      pair measured on a fresh draw met any pass test (A001: every verdict word `failure`), so none is promoted;
      P is a new arm built on the measured instruction.
    - **The section 7 default, read for G.** The default says "shipped `decide` primary, declared mappings
      secondary", written for decision 9 (how "unclear" counts). G reads no unclear answer and leaves every unclear
      as answered; it is the "verbatim-substring check" that lever 3 names in the pinned order, which the default's
      wording predates as a lever. This rule therefore reads G as primary-eligible and M as a secondary reading,
      says so here, and asks once (one line, in the loop's next reply and under `## Open asks` of
      `LOOP-48H-STATUS.md`): "NEEDS CHIEF SCIENTIST: the section 7 default is read as leaving lever 3's
      verbatim-substring check primary-eligible (it reads no unclear answer); if not, A002's primary becomes
      (a-4b, `evidence`) by a dated amendment before the run." A decision 9 call that counts unclear as refute,
      received before the run, names P = (a-4b, Egm) by a dated amendment before any run.
    - **The headline arm is Eg** (one model, so one headline): Eg's balanced accuracy interval is read against the
      lexical judge's balanced accuracy on the same records as J001's rule 7 reads it: **better** only when the
      interval's lower end is above it, **worse** only when its upper end is below it, otherwise **not told apart**.
    - **Every other arm is exploratory:** N and D (the controls; D is A001's mapping secondary, decision 9 pending),
      E, Egm, R, Rm, C and Cm. Each is reported with the same statistics as P, including rule 12's pass test (with
      the ablation clauses for C and Cm). The lifts of rule 10 are read as "helps" when the lower end is above 0,
      "hurts" when the upper end is below 0, otherwise "not told apart"; they decide nothing toward 86.
    - **The comparison count.** This cycle makes 9 (model, arm, cycle) comparisons: 9 arms × 1 model × 1 cycle,
      the composite arms and the controls counted. 12 were made before it (A001 run 1: "12 since the loop began").
      The running count since the loop began is therefore **21** after this cycle. The request carries
      `comparisons_before` 12; the Runs entry prints the count.
12. **The verdict rule.** Copied verbatim from LOOP-48H section 2.5 (lines re-wrapped; the words are its). Its "§7"
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

    For this cycle, read as A001's A5 and A6 read it: "complete" is read per task set: every part of the set
    finished for that task (every one of its calls for that task made; a model failure after the repair is a call
    made), `measurement` true, `records_not_judged` 0, and the dropped share (dropped over scored plus dropped) at
    most 0.01; P's set is task E's, and "the same draw" is the 150 records task E's set scored. The aggregate
    evaluates the verdict words in the order **`incomplete`, `pass_provisional`, `partial`, `failure`**, and the
    first whose condition holds is the verdict word, with the figures behind every clause printed beside it, so that
    no one reads it by hand; a balanced accuracy at or above 0.860 whose interval to the lexical judge includes 0 is
    `partial`, and the remainder is `failure`. For C and Cm, `pass_provisional` additionally needs both ablation
    clauses of rule 5; a composite that meets the four clauses but not an ablation clause is `partial`, reported
    with the ablation figures. The aggregate computes the word for P and, labelled exploratory, for every other arm.
    Success in the sense of the rule cannot be reached in this cycle.
13. **Reported beside the verdict, deciding nothing:** for every arm: sensitivity, specificity, accuracy and the
    unknown share with intervals; the count of each answer pair, for positives and for negatives, as answered and
    after G and M; the verdict counts; failures by kind and per task; per predicate, how often each judge confirmed
    it, for positives and for negatives (`by_predicate`); the comparators and the two ablations on the same records;
    for task E, the grounded share of `yes` answers on positives and on negatives separately (the one diagnostic
    that says whether the check removes false confirms or true ones), the share of empty citations, and the median
    and 95th percentile of citation length in characters after `norm`; for task R, the median and 95th percentile of
    output tokens from the ledger (`tokens_out`) and the count of replies cut at the cap (`finish_reason` `length`),
    per task; the share of replies whose first key is the new field, per task; seconds per judge call per task
    (median and 95th percentile from the ledgers) with the runner CPU model, a runner number, not site hardware; and
    N's and D's figures beside A001 run 1's arms C and D, labelled a between-draw difference.
14. **Units and limits** (sized in "Why the units are this size"):
    - one unit per part: **5 units**; a unit judges its part's 60 questions three times, in three passes, in this
      order: **task E first** (P's set, A9's reasoning), then task R, then task N (the control last: a late stop
      loses the control, which decides nothing toward 86, before it loses a lever's reading); 180 calls a unit;
    - each unit has 300 minutes and runs alone in its shard; `job_minutes` 330 gives a shard capacity of 305 and a
      job timeout of 325 (`SHARD_OVERHEAD_MINUTES` 25, `lab/request.py:193`); `max_parallel` 5 (the cap is 16,
      `lab/request.py:967`);
    - each unit stops at its budget and keeps what it judged (`stopped: budget`), as A001's runner does; a stop
      ends the pass it falls in and the passes after it; a finished earlier pass stays complete; `run.json` records
      `complete_by_task`, `stopped_task` and `questions_done_by_task`;
    - **a task set is scored only when all 5 of its parts finished for that task.** Otherwise its finished parts are
      a partial reading and decide nothing. The arms of one task are complete exactly when the task's set is; C and
      Cm are complete exactly when task E's set is.
15. **What is written, and the guards: A001's, unchanged in kind.** Each verdict line holds the question id, the
    record ref, the kind, the predicate, the task, the whole reply (the two answers and, for tasks E and R, the
    citation or the analysis, kept as A001 keeps the whole reply because the data are public complaint text that the
    plan artifact already holds in full), whether the citation is grounded, the reply's first key, the error kind,
    `scored`, and every reading's verdict: the shipped reading, M's, G's and G-then-M's, each by the one pinned
    `decide`; never a narrative beyond what the reply itself quotes. The aggregate recomputes every reading's verdict
    from the stored reply with the pinned `decide`, recomputes `grounded` from the stored citation and the payload
    text it rebuilds from the plan artifact's labels file (`lab.j1.payload`), and refuses a line that disagrees with
    any of them (A001's A8, with two more readings). The plan artifact holds the labels, questions, lexical, control,
    ablation and chain files (public complaint text and refs; the artifact, never the repository). Reports, step
    summaries, console lines and commits hold counts, scores and hashes only: no narrative, no citation, no analysis,
    no record ref, no VIN, no city, no dealer, no operator name. The warm-up calls the three tasks once on a generated
    record about no drawn record (A001's A11: the probe, sent twice for `repeat_identical`, is the shipped task's;
    for a j3 unit the shipped task is not among the unit's tasks, so the probe is task N's, the first task name in
    sorted order among E, N, R, `lab/warmup.py:9-10, 44-45`).
16. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3). A set left incomplete gets no verdict from this
    run; judging it again is a new choice file. Any change to a setting, to E's or R's text or schema, to g, G, M or
    the composite rule is a new choice file. After the run, no reading other than the nine arms is made of the
    replies: anything else they suggest (another floor for g, another composite rule, a reading of the analysis text)
    is a hypothesis for the next cycle's rule.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Models | a-4b; one run; temperature 0 (the client), fixed server seed; `--reasoning off` (`lab/models.json`, unchanged) |
| Population | NHTSA complaints, pack `docs/collective/replay/vehicles/pack`, `NHTSA_MAKES` and `NHTSA_WINDOW` verbatim, j1 eligibility |
| Excluded | J001's 150: run 37996675559, artifact `lab-plan-37996675559-1`, `prereg/j1/labels.jsonl` sha256 `bc6d092d8bca…`, rebuild seed 1; A001's 150: run 38114522160, artifact `lab-plan-38114522160-1`, `prereg/j2/labels.jsonl` sha256 `623780b8a1de…`, chain `prereg/j2/excluded.jsonl` sha256 `9a948cbf2cd9…`, rebuild seed 2 over the eligible list less J001's set; 300 refs expected; each cycle by its artifact and its rebuild, one verifying source per cycle suffices, verifying sources must agree |
| Labels | n 150, seed 3 (`random.Random("nhtsa:3")` over the eligible records less the 300 excluded), codes removed |
| Questions | `lab.j1.build_questions`, seed 3, parts 5; `NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged |
| Tasks | N `judge_record_negation` (A001's, 256 tokens, shipped schema); E `judge_record_evidence` (512 tokens, S_E); R `judge_record_rationale` (512 tokens, S_R); instructions and schemas as rule 3, pinned by sha256; pass order E, R, N |
| Mappings | M: `describes_predicate` `unclear` read as `no`; G: `describes_predicate` `yes` read as `no` when the citation is not grounded; g: `norm` is whitespace-collapsed, stripped, case-folded; at least 4 characters; a substring of `norm(text)` |
| Arms | N `negation`, D `negation_mapped`, E `evidence`, Eg `evidence_grounded`, Egm `evidence_grounded_mapped`, R `rationale`, Rm `rationale_mapped`, C `composite`, Cm `composite_mapped` |
| Composite | `confirm` where the lexical judge's verdict is `confirm`, else the grounded arm's verdict (Eg for C, Egm for Cm) |
| Ablations | A `lexical_or_no` (constant reply yes/no, so `refute`, where the lexical judge does not confirm); B `lexical_or_record_blind` (the record-blind control's verdict there); both in the plan job |
| Primary pair | P = (a-4b, Eg) |
| Headline arm | Eg; every other arm exploratory; 9 (model, arm, cycle) comparisons this cycle, 21 since the loop began |
| Comparators | lexical judge, record-blind control (`PRIOR_TOP`), predicate-only bound, ablations A and B; all in the plan job |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 3 (`j1:3`), 95% percentile; paired differences with the same draws |
| Codes in the payload | empty in every task; a record with codes is refused; no key added to the payload |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 600 s, 1 retry; no escalation |
| Units | 5 (1 model × 5 parts), 180 calls each, 300 minutes, one per shard; `job_minutes` 330; `max_parallel` 5 |
| Withhold | `WITHHOLD_SHARE` 0.01 per task set |
| Request | `lab/templates/accuracy-002.json`, copied to `lab/requests/accuracy-002.json`; `retention_days` 30; `provider` `llama-server`; `comparisons_before` 12 |

## Why these arms, and why this primary pair

**The yes on a negative is the failure to fix.** Under the shipped instruction a-4b abstains (unknown share 0.560,
A001 run 1, arm A) and under the negation instruction it answers, but mostly yes: sensitivity 0.833, specificity
0.320 (arm C). The mapping converts abstention into refutation (arm B: specificity 0.053 to 0.773 at sensitivity
0.560; arm D: 0.320 to 0.473 at 0.833), and the best it reached was level with the lexical judge (0.667 [0.620,
0.710], paired 0.003 [-0.047, 0.050]). Nothing measured so far makes a-4b say no to a component the record does not
describe while keeping its yes to one it does. Both schema variants and the composite are built on task N because
N's lift over the shipped instruction is measured (0.270 [0.217, 0.327], A001 run 1, C − A) and stacking a new
field on an untested instruction would make its lift unreadable (CHOICE-A001, "What is left out").

- **Arm N (and D)** are the controls: A001's arms C and D on a fresh draw. Without N, a lift could be a between-draw
  difference (A001's arm A was 0.080 below J001's a-4b, a little beyond the 0.07 its reading calls sampling). D
  stays because decision 9 is pending and the mapping's lift under N is measured (0.077 [0.047, 0.107]).
- **Arm E (the field alone)** separates two effects that one arm would confound: asking for a citation may itself
  change the answers (a model that must quote may answer no where it cannot), and the check changes the verdicts.
  E − N reads the first; Eg − E the second.
- **Arm Eg (the primary)** attacks the measured residual directly. Under N a-4b did not refute 102 of 150 negatives
  (specificity 0.320) and answered unclear on 41 of its 300 questions (unknown share 0.137; the split by kind is in
  the run's report and was not read). G turns a yes without a grounded citation into a no. **The prior expectation,
  from A001's figures:** if G removed every ungrounded yes on a negative and no yes on a positive, sensitivity would
  stay 0.833 and specificity would rise to (150 − u)/150, where u is the number of unclear answers on negatives,
  between 16 (41 unclear less the 25 positives not confirmed, at most) and 41: balanced accuracy between 0.780 and
  0.863. If G removed true confirms as often as false ones, it would move nothing or hurt. So a pass on P needs the
  favourable end of that range and a check that removes nothing true; the likely outcomes are failure or partial,
  and the cycle still measures what the loop needs: whether a verbatim check moves specificity without the mapping.
- **Arm Egm** is Eg with the mapping, the arm with the highest arithmetic ceiling: with every unclear on a negative
  read as no, (0.833 + 1.000) / 2 = 0.917 at the limit. It is a secondary under the section 7 default; a decision 9
  call before the run promotes it (rule 11).
- **Arm R (and Rm)** tests the other lever-3 variant: a rationale before the answer. a-4b runs with reasoning off
  (`lab/models.json`), so the analysis field is the only place the model writes before it answers. No recorded figure
  predicts its direction; that is why it is measured, under the shipped reading and under the mapping.
- **Arms C and Cm (lever 4)** add the lexical judge's confirms to the grounded arms. On A001's draw the lexical
  judge confirmed 50 of 150 positives and 1 of 150 negatives (sensitivity 0.333, specificity 0.993), so the
  disjunction can only raise sensitivity by the positives the lexical judge catches and the model misses, at a cost
  of at most about one negative in 150. It is built on the grounded arms, whose check may lower sensitivity, because
  that is where a near-free sensitivity backfill is worth measuring. A composite that says yes whenever the lexical
  judge does inherits the lexical score with the model contributing nothing; that is what ablation A measures, and
  why the composite arms' pass test needs the paired difference to both ablations above 0.
- **Why a-4b alone.** a-0p5b and a-1p5b scored 0.500 [0.500, 0.500] and 0.477 [0.447, 0.503] in J001 (RESULTS
  run 9) and 0.467 to 0.497 on every arm in A001 (RESULTS run 11): a-0p5b said yes on 145 of 150 positives and 145
  of 150 negatives in J001; a-1p5b under N moved toward yes (sensitivity 0.880 to 0.973, specificity 0.053 to
  0.020, A001 run 1); the mapping changed no score of either (lifts 0.000 [0.000, 0.000]); every lift on them was
  not told apart from zero. Both levers of this cycle ask the model to follow an instruction (quote a passage;
  write an analysis) that a model which did not follow N cannot be expected to follow, and the composite and the
  grounding check read as the lexical judge on a model that answers the same way every time. Running them would add
  six comparisons to the multiplicity count and runner minutes, with no reading toward 86 that A001 did not already
  give. They return when a lever is one they can follow, or when lever 8 replaces them with a larger local rung.
- **Why no predicate-descriptions arm.** The pinned order's lever 3 names "predicate descriptions". The pack's only
  per-predicate text is the `label` (already in every payload as `predicate_label`, `verify.py:147-160`) and the
  `lexicon` phrases of `docs/collective/replay/vehicles/pack/vocabulary.json` (one to three phrases; for 19 of the
  27 predicates the phrase is the label in lower case, for 8 it adds or splits a word, for example the twin and the
  slash-named predicates); `codes.json` holds one code per predicate with the same label. Those phrases are the
  lexical judge's own terms (`verify.lexical_judge` reads them through `LexicalExtractor`), so an arm carrying them
  would test whether the model can match the comparator's lexicon, and its lift over the lexical judge would be
  confounded with lexicon knowledge rather than reading. A hand-written description per predicate would be text
  this rule's author invents with no recorded figure behind it, close to the key's category names. It is left to a
  later cycle's rule with the chief scientist's view, and listed under "Decisions this does not make".
- **What is left out and why.** The drafted-terms payload (lever 5) needs a received-date split and a committed
  term set; a-4b with reasoning on (lever 6) needs a reviewed lab change to the warm-up (`lab/warmup.py:46`); the
  R003 settings and pack-v2 (lever 7) need the chief scientist; a larger local rung (lever 8) needs a manifest entry
  and a check run. Nine arms on one model is the ceiling this rule accepts for one draw's multiplicity.

## Why the units are this size

- **The calls.** 5 units of 180 calls: 900 model calls, 300 per task. The six view arms add none; the composite arms
  join plan-job lines.
- **The recorded per-call latency.** A001 run 1's a-4b judge calls on real complaint narratives, two-word replies:
  medians 8,211.6 to 9,431.5 ms by runner CPU and task (95th percentiles 11,754.1 to 16,427.0 ms; CHOICE-A001, Run 1,
  the latency table). At those medians a unit's 60 calls of task N take 493 to 566 s. The longest A001 shard
  (`s011-a-4b`, 120 calls) took 1,377.2 s of wall time.
- **The longer replies.** The recorded decode rate of a-4b on a runner is 7.0 to 9.0 tokens per second (RESULTS
  run 1, the E3 table, AMD EPYC 9V74). A citation of 10 to 60 tokens adds about 1 to 9 s to a call; an analysis of
  50 to 150 tokens adds about 6 to 21 s; a reply at the 512-token cap adds about 57 to 73 s, and its repair about as
  much again. At the medians a unit's three passes take about 566 (N) + 60 × 18 (E) + 60 × 30 (R) ≈ 3,500 s, under
  an hour; if every task R call ran to the cap and was repaired once, task R alone would take about 8,000 to 10,000 s
  and the unit about 9,600 to 11,700 s, at most 195 minutes, inside the 300-minute budget.
- **The tail.** A001 recorded 95th percentiles under twice its medians; the rationale pass is the one new tail, and
  only the looseness of the budget covers it.
- **One unit per shard** lets a slow unit run to its budget without delaying another. The expected wall time is
  about 1 to 2 hours, dominated by provisioning, the warm-up and the rationale pass; the five shards run at once.
- If a pass does not finish anyway, rule 14 applies.

## How it is read

- **The verdict on P** is read by rule 12 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p."
- **The honest expectation.** a-4b's best measured arm under the shipped verdict rule scored 0.577 [0.530, 0.623]
  (A001 run 1, arm C) and the lexical judge 0.663 [0.627, 0.703]; a pass needs 0.860 and an interval to the lexical
  judge above 0. P's arithmetic ceiling from A001's figures is 0.780 to 0.863. The likely outcomes are failure or
  partial. The cycle still measures lever 3 twice and lever 4 once, with their ablations, and replicates A001's arms
  C and D on a third draw.
- **The headline** (rule 11) says, for Eg, whether a-4b with the grounded evidence field judges better than, worse
  than, or not apart from the lexical judge, as J001's rule 7 and A001's rule 9 said for their arms.
- **The lifts** (rule 10) say what each lever did. Read them with sensitivity and specificity: a lever that raises
  specificity by lowering sensitivity by the same amount moves balanced accuracy little and shows no reading. For
  Eg − E, read the grounded share of yes answers on positives against negatives: a check that removes yes answers on
  both kinds alike is a cost with no reading; one that removes them on negatives and keeps them on positives is the
  lever working.
- **The composite arms** are read against both ablations first: a composite not told apart from ablation A is the
  lexical judge with the model contributing nothing; one above ablation A but not above ablation B adds no more than
  a reader that reads nothing adds. Only a composite above both, by the paired lower ends, is read as the model
  contributing, and even then its word is read by rule 12.
- **The grounding check, declared cases** (the build pins each): a citation that occurs in the text exactly is
  grounded; one that differs only in case or in runs of whitespace is grounded; one that differs by a word, a
  paraphrase, an added or dropped character other than whitespace, or a passage of another record is not; an empty
  citation, or one shorter than 4 characters after `norm`, is not; the text compared is the truncated payload text,
  so a passage beyond the 6,000-character cut is not grounded (the model never saw it); the record's language is the
  pack's (`en`), so no accent folding is needed and none is done.
- **First-key shares and lengths** say whether the field was written first and how much was written; they decide
  nothing. A low first-key share for task R means the server's grammar did not order the fields and the rationale
  followed the answer on those replies; the arm's score stands as measured, and the share is recorded beside it.
- **N and D against A001 run 1's arms C and D** is a replication on a fresh draw of a fresh draw (0.577 [0.530,
  0.623] and 0.653 [0.610, 0.697]). Differences within about two standard errors (0.07) are sampling; a larger one
  says the draws differ, and both are reported.
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. Under G a judge that quotes nothing refutes everything
  it would have confirmed and scores 0.5 at best on its yes answers; under M a judge that answers unclear everywhere
  refutes everything and scores 0.5: neither mapping shows a reading by itself, which is why the pass test needs the
  lexical judge and the bound beaten. The record-blind control and the predicate-only bound are read as J001 reads
  them: neither decides anything; a model at or below the bound is not shown to read the record.
- **150 records give intervals about 0.07 wide.** Small differences between arms cannot be read.
- **This is one public field,** complaints written to a regulator, with the models possibly having seen them in
  training. A pass here would be a pass on public text the models may have seen; the Runs entry says so.

## Traps

1. **Filed codes are not truth.** A narrative can describe a component it was not filed under, and the reverse. No
   judge can reach 1.0, the key's noise ceiling is unmeasured, and 0.860 may sit above it for some predicates. The
   key costs every judge, the lexical one included.
2. **Old and new names.** The pack keeps three old-and-new name pairs as separate predicates (`TWINS`); a negative is
   never the other name of a filed component; a positive is the filed name. No text can tell the two names apart,
   and no citation or analysis changes that.
3. **The check can be satisfied without evidence.** A model that copies any passage of at least 4 characters passes
   g; a model that copies the predicate's label where the narrative happens to contain it passes g and behaves as
   the lexical judge does. g says that a passage came from the record, not that it reports the problem. The grounded
   shares by kind, the citation lengths and the composite's ablations are the readings that expose this; the pass
   test's lexical and bound clauses are its backstop.
4. **The check costs true confirms.** A model that answers yes rightly but paraphrases, quotes across a line break
   the data block encoded, or quotes a passage beyond the 6,000-character cut loses the confirm under G. The
   sensitivity of Eg against E carries that cost, and E − N against Eg − E separates it from the field's own effect.
5. **The data block's encoding.** The narrative reaches the model as canonical JSON inside the data block with every
   `<` written as `<` (`tasks.py`, `data_block`); a copied passage comes back through the model's JSON reply, so
   escapes decode and a faithful copy matches. A model that returns the escape sequence as literal characters does
   not match; that is a cost g accepts rather than a second normalisation declared after the fact.
6. **The rationale may follow the answer.** The schema puts `analysis` first; whether the server's grammar enforces
   the order is the server's. The first-key share is recorded; a cycle may not re-order the fields after seeing it.
7. **The cap and the repair.** A reply cut at 512 tokens gets one repair; a second cut is a model failure, `unknown`.
   A model that writes long analyses can lose questions this way on task R without any reading of its judgement;
   the count of cut replies per task is reported, and the budget's arithmetic above assumes the worst case.
8. **Prompt-cache effects.** Each shard's one server caches the prompt prefix across calls, and the three passes of a
   unit have three prefixes, run E, R, N. At temperature 0 with a fixed server seed the reply should not depend on
   the cache's state; the lab checks that only through the warm-up's `repeat_identical` on one generated record.
   Timings differ with cache hits, and the first call of each pass is slower; latency is reported per task and
   decides nothing.
9. **The composite inherits the lexical judge.** Lever 4's text says it: a composite that says yes whenever the
   lexical judge does inherits the lexical score with the model contributing nothing. Ablation A is that composite
   exactly, and the composite arms' pass test needs the paired lower end above it and above ablation B.
10. **The mapping converts abstention into refutation** (A001 trap 10). Under M, "I cannot tell" counts as "not
    described". D, Egm, Rm and Cm carry that reading; it is decision 9's, pending, and secondary here.
11. **One model.** With a-4b alone there is no reading of whether the field, the check or the composite generalises
    across model sizes; the per-model headline is one line. The reason for one model is recorded above, and it is a
    multiplicity choice, not a claim about other models.
12. **Nine arms, one draw.** With a standard error near 0.035, the best of nine readings on one draw is biased
    upward. The pass test is read on P alone; a secondary that passes is a hypothesis; the confirmation rule exists
    for this reason, and the running count (21 after this cycle) is printed.
13. **Training data.** The models may have seen these public complaints, the fresh draw's included. That would help
    them, not the lexical judge. Exclusion of J001's and A001's records removes records this model judged, not
    records it may have read in training.
14. **Post-hoc readings.** The replies, citations and analyses are stored, so other floors for g, other normalisations,
    other composite rules and readings of the analysis text can be computed after the run. None counts: only the nine
    arms are readings of this cycle. Anything else is a hypothesis for the next cycle's rule on a fresh draw
    (rule 16). J001 and A001 are never re-scored.
15. **Tuning on the draw.** E's and R's texts and schemas, g, G, M, the composite rule, the seeds and the parts are
    fixed in this file before the draw exists. The plan job prints the comparators and the ablations before any
    shard starts; those figures are never a reason to redraw. A seed change is a numbered amendment stating a cause
    independent of the printed figures, reviewed before the build.
16. **Two artifacts expire.** J001's on 2026-11-08T22:00:03Z, A001's about 2026-11-10. After that each cycle's
    freshness against those draws rests on the rebuilds, which rest on the complaint file not having changed in the
    makes and window, and on the chain file of the previous cycle's artifact. A cycle that cannot establish a set
    stops and asks; it does not run.
17. **The complaint file changes.** The labels come from the file as published on the day. The eligible list may
    differ from A001's day (58,319 then); the exclusion works on refs, so freshness holds whenever an artifact
    verifies, and a changed file is reported through the rebuilds' prefixes and the file's sha256 beside
    `a5a5de2d31f3…`. If the file changed, J001's rebuild and A001's rebuild fail together, since A001's rebuild
    samples the eligible list J001's rebuild reads; the artifacts then carry both cycles.
18. **E and R are written for this pack.** "Component or system" fits vehicle complaints, as N does (A001 trap 15).
    They are lab TaskSpecs, not product texts: shipping any of them would need pack-neutral wording and a decision.
19. **One record a question.** The product pools a window of records into one verdict (`decide` over many records);
    this cycle judges one record at a time, so the pooling, the routes and the gate are not measured. L001 measures
    them. A citation is a lab reading; the product's verdict carries no evidence span.
20. **The schemas are new to the server.** The lab's fake server answers any schema; only the warm-up, which calls
    each task once on a generated record, shows the real server accepting S_E and S_R before a drawn record is sent.
    A server that refuses either schema stops the unit before any model judges, which is an infrastructure failure
    under rule 16 and section 6.3, not a result.

## What it does not show

- No site-level verdict: how pooling, routing and the commit gate turn record verdicts into an alert's answer. That
  is L001's measurement, where the route-role baseline reads nothing and scores 0.808.
- No early warning. There are no outcomes here, no detection and no alert; this is reading, not signal.
- No partner data. These are public complaints written to a regulator, not a company's own records, and not another
  field or pack.
- Not the product's behaviour for any arm. The product runs the shipped instruction and the shipped verdict rule,
  which A001 measured on two draws (0.387 [0.340, 0.433], J001; 0.307 [0.263, 0.350], A001) and this cycle does not
  run again. Every arm here is a lab reading of a declared change, measured so that a decision can be made; none is
  shipped by this cycle.
- Not the hosted central reader of section 2.5's secondary; no key exists and none is assumed.
- Not a-0p5b or a-1p5b on any of these arms; not a-4b with reasoning on, the R003 settings, pack-v2, a larger local
  model, a drafted-terms payload or predicate descriptions (levers 5 to 8 and the part of lever 3 left out).
- Nothing about Tesseract, ingestion or latency as a test (latency is reported, not judged).
- Not the key's noise ceiling, nor whether any judge could reach 0.860 on these labels.

## Decisions this does not make

- **Shipping any arm is the chief scientist's decision 9** (`HANDOFF-2026-10-09.md`, decisions table), and a change
  to `judge_task`, `judge_schema` or `decide` is a product change of the same kind. This cycle measures declared
  arms through lab-side TaskSpecs, lab-side mappings and a lab-side composite; the pinned `verify.py` (`judge_task`,
  `judge_schema`, `judge_payload`, `lexical_judge`, `decide`) is imported and never changed. The handoff's decisions
  table stays as it is; a call reaches this loop only as a message, recorded under `## Decisions received` in
  `docs/handoff/LOOP-48H-STATUS.md`.
- **How the section 7 default reads for the verbatim-substring check** is declared in rule 11 and asked once; the
  answer, if it comes before the run, is adopted by a dated amendment.
- **The R003 settings** (`HANDOFF:329-331`) and **pack-v2** are the chief scientist's; a pack-v2 cycle would be
  labelled and never pooled toward 86 (LOOP-48H section 2.6, lever 7).
- **Decisions 1, 2 and 7** (Tesseract placement, routing, the embedder) are untouched; the accuracy track does not
  build Tesseract in.
- **The founder's alternative readings** of 86 (LOOP-48H section 2.7) are not adopted; the pinned reading runs by
  default, and the hour-0 ask stands.
- **The hosted central reader** needs the founder's secrets and a reviewed lab role; nothing here assumes it, and no
  hosted model is named.
- **A predicate-descriptions arm**, an MSHA record-level replication, a codes-present product-realism secondary
  (handoff Q10), a second composite rule and the two smaller models' readings on these arms are left to later
  cycles' rules.

## The request

`lab/templates/accuracy-002.json`, copied to `lab/requests/accuracy-002.json` and pushed alone after the code is
merged (LOOP-48H section 6.4: the request push is the only thing that starts a lab run). Its content, fixed now (the
exact key names are the build's, recorded in `BUILD-A002.md`):

- top level: `provider` `llama-server`; `models` `["a-4b"]`; `job_minutes` 330; `max_parallel` 5; `retention_days`
  30; a `purpose` naming this file and the copy's name;
- `experiments.j3`: `minutes` 300; `labels` `{source: nhtsa, pack: docs/collective/replay/vehicles/pack, n: 150,
  seed: 3}`; `parts` 5; `seed` 3; `bootstrap_b` 10000; `bootstrap_seed` 3; `primary` `{model: a-4b, arm:
  evidence_grounded}`; `comparisons_before` 12;
- `experiments.j3.exclude`, a list of two entries in seed order: J001 (`run_id` 37996675559, `artifact`
  `lab-plan-37996675559-1`, `file` `prereg/j1/labels.jsonl`, `sha256_prefix` `bc6d092d8bca`, rebuild seed 1 with the
  same prefix) and A001 (`run_id` 38114522160, `artifact` `lab-plan-38114522160-1`, `file` `prereg/j2/labels.jsonl`,
  `sha256_prefix` `623780b8a1de`, the chain file `prereg/j2/excluded.jsonl` with prefix `9a948cbf2cd9`, rebuild seed 2
  with prefix `623780b8a1de` over the eligible list less the first entry's set).

`lab.request` validates every field (integers in range, artifact names of the lab's shape, lower-case hex prefixes of
at least 12 characters, files from a fixed list, the primary arm one of `lab.j3.ARMS`, the model one of the block's
and a gguf model, seeds in order); `lab.plan --github-output` echoes both excluded runs and artifacts; the plan job
downloads both.

## The build plan

The build starts only after this rule's review and any amendment are committed. It changes no pinned file:
`lab/j1.py` and `lab/goldlabels.py` (J001's `CODE_FILES`), `lab/j2.py` and `lab/j2labels.py` (A001's `CODE_FILES`,
`lab/j2.py:115`), nothing under `mycelic/collective/`, `connectors/openfda.py`, the vehicle pack, any earlier
`CHOICE-*.md`, `run-*.json` or `*-settings.json`.

- **A new kind `j3`, not additive arms in `j2`.** `lab/j2.py`'s constants `TASKS`, `ARMS`, `LIFTS`, `LINE_KEYS` and
  `PREREG_KEYS` are read by the request validator (`lab/request.py:611`, the primary arm), the unit adapter
  (`lab/units.py:292`), the aggregate (`lab/aggregate.py:1111`: the comparison count is `len(ARMS)` times the
  models) and the summary (`lab/summary.py:177-178`, the arm and lift tables). Extending them would change what a
  re-aggregation of A001's run computes (its count, its tables, its line check), which is A001's recorded
  computation; and `lab/j2.py` and `lab/j2labels.py` are in A001's code hash. So A002 is a new kind whose own
  constants hold its tasks, arms, lifts and line keys, and which imports from `lab.j2` unchanged: `NEGATION_TASK`,
  `NEGATION_INSTRUCTIONS`, `negation_task`, `MAPPING`, `mapped_reply`, `pass_test`, `verdict_word`, `lift_reading`
  and `task_pins`' shape; and from `lab.j2labels` unchanged: `earlier_refs`, `rebuild_refs`, `eligible_records`,
  `draw`, `label_lines`, `excluded_jsonl` and `nhtsa_rows`. A001 can be re-aggregated at any later commit with its
  report unchanged.
- **New modules.** `lab/j3.py` (tasks E and R with their schemas and instructions; g, G and the composite; the nine
  arms, the lifts and the ablation names; the verdict line with its readings and `first_key`; the line check that
  recomputes every reading and `grounded`; the j1-shaped views; the pins; the preregistration document; the runner:
  three passes, E first, A001's budget timer and signals, `complete_by_task` per task); `lab/j3labels.py` (the two
  earlier cycles' sources, the A001 rebuild over the eligible list less J001's set, the per-cycle stop rules, the
  union, the labels record with every source's outcome, the chain file); `tests/lab/test_lab_j3.py`;
  `tests/lab/j3_data.py` (a synthetic complaint archive, a synthetic J001-shaped earlier artifact and a synthetic
  A001-shaped one built from it by the pinned code, and a stand-in for both entries); `lab/templates/accuracy-002.json`;
  `docs/collective/A002/BUILD-A002.md`.
- **Wiring, all unpinned.** `lab/request.py` (the `j3` block: `exclude` as a list of two entries with the rebuild
  and chain keys, `primary.arm` in `lab.j3.ARMS`, `comparisons_before`, `j3` in `EXPERIMENTS`); `lab/plan.py` (two
  pairs of outputs, `j3_exclude_run_id_1`/`_artifact_1` and `_2`, empty for every other plan); `lab/prereg.py`
  (`_j3`: both downloads' directories, the five sources, the stop rules, the draw, J001's questions and comparators,
  the two ablations as `prereg/j3/ablation_a.jsonl` and `ablation_b.jsonl`, `prereg/j3/excluded.jsonl`, the pins);
  `lab/units.py` (the `j3` adapter with the three task names; the status reading: exit 1 with task E finished is
  `ok` with a fixed note, any other stop `failed`; the participation floor judges task E always and R and N only when
  their passes finished); `lab/warmup.py` (the three tasks on the judge's generated payloads; the probe is the first
  task name in sorted order); `lab/responder.py` (handlers for `judge_record_evidence` and `judge_record_rationale`
  that answer as the shipped judge's handler and add an empty `citation` or `analysis`, so a dry run's replies
  satisfy S_E and S_R; an unknown name is still refused); `lab/aggregate.py` (a `j3` block: the sets, the nine arms
  with the comparators, the ablations and the verdict words, the lifts and the ablation differences, the grounded
  shares by kind, the citation lengths, the token and cut counts, the first-key shares, `primary_result`);
  `lab/summary.py` (the J3 lines of the plan summary and the J3 sections of the report); `lab/notes.py` (the J3
  notes, every sentence free of ASCII digits: `tests/lab/test_lab_guards.py`, `test_notes_hold_no_digits`, so no
  figure can ride in a fixed sentence; counts, prefixes and ids are inserted where a sentence is rendered);
  `lab/dryrun.py` (`--j3-data`); `.github/workflows/mycelic-lab.yml` (a second plan-job download step of the
  `earlier` step's shape, `actions/download-artifact` at the same pinned sha, run only when the second run id output
  is not empty, `continue-on-error: true`, into `${{ runner.temp }}/lab-earlier/<run id>`, never under `lab-plan`);
  `docs/lab/REFERENCE.md`, `docs/lab/README.md`, `docs/lab/INTEGRATION.md`, `lab/requests/README.md`.
- **Tests, declared.** `tests/lab/test_lab_j3.py` pins: E's and R's instructions equal to this file's blockquotes
  (lines joined) and N's text verbatim with the new sentences inserted before the last; the schemas' properties,
  `required`, the absence of strict keywords and the new field first in insertion order; `max_tokens` 512 and 256;
  g's declared cases ("How it is read"); G and M on every answer and their commutation; the composite rule and both
  ablations on every verdict pair; every view passing through `lab.j1.score` and `lab.j1.paired`; the line check's
  every outcome (keys, task, question, answers, each reading's verdict, `grounded`, `first_key`); the words in A5's
  order with the ablation clauses; the sources and stop rules on synthetic artifacts (each cycle by artifact alone,
  rebuild alone, both agreeing, both disagreeing, the chain agreeing and disagreeing, neither verifying, the A001
  rebuild `not attempted` when J001's set is not established); the draw over the eligible list less 300 refs,
  disjoint from both synthetic earlier draws; the request block's every error; the template giving 5 units in 5
  shards; the runner through the fake server (three passes in order, the pins refused before any call, a stop in
  each pass, the status reading, participation); the wiring (both fake handlers, the warm-up's three tasks, the
  plan's four outputs); the small and the template dry runs with every number traced and no ref anywhere. The pins
  of `tests/lab/test_lab_workflow.py` change with the workflow (`github.token` in exactly three places; five
  `download-artifact` steps with a `name`); `tests/lab/test_lab_docs.py`, `test_lab_plan.py`, `test_lab_sim.py` and
  `tests/lab/helpers.py` gain `j3` where they list the experiments. `tests/lab/test_lab_guards.py` is unchanged:
  `lab/j3labels.py` makes no network call, and `actions: read` is read-only.
- **The guards the build and this file obey.** No model-family name anywhere: `tests/mycelic/test_collective_guards.py`
  (`NoModelNamesTests`, which scans `docs/collective` and the collective code) and `tests/lab/test_lab_guards.py`
  (which scans `lab/`, `tests/lab/` and `docs/lab/`); the models are a-0p5b, a-1p5b and a-4b, and the deployment
  branch is called that. No record ref in any report, summary, console line, commit or document; the report guards
  refuse record ids. No ASCII digit in any `lab/notes.py` sentence. Lab Python imports only the standard library,
  `mycelic` and `lab`.
- **Before merge** (section 2.3): `python -m pytest tests/lab -q -p no:cacheprovider` in the background, the onboard
  and guard suites, and the template dry run `python -m lab.dryrun --request lab/templates/accuracy-002.json --out OUT
  --j3-data DIR`, which must show 5 units in 5 shards, every source verified on the stand-in, every unit `ok`
  and `plumbing`, every word `incomplete`, the comparisons 21, and no ref of the draw or the excluded set anywhere.
  In a dry run the fake answers E and R as the lexical judge with an empty field, so Eg and Egm refute every
  question the fake would have confirmed, and nothing in a dry run measures the instructions, the check or the
  composite; `BUILD-A002.md` says so.

## Runs

None yet. The first run's entry will hold: the lab run id, the request file and its sha256, the commits of the rule,
its amendments and the request; the plan job's fixed line (`available`, excluded 300, eligible after exclusion,
overlap 0, the outcome of each of the five sources and the symmetric differences computed, the labels' sha256
prefix, the questions' prefix, the prereg prefix, the chain file's prefix with its line count, the complaint file's
prefix beside `a5a5de2d31f3…`); the comparators and both ablations on the fresh draw with intervals; the nine arms'
table (balanced accuracy with interval, sensitivity, specificity, unknown share, the paired difference to the lexical
judge, the bound on the same records, the pass test, the verdict word; for C and Cm the two ablation differences);
the verdict on P in rule 12's words with its figures; the headline for Eg; the lifts of rule 10 with their readings;
the grounded shares by kind, the empty-citation share, the citation lengths, the task R token figures and cut counts,
the first-key shares per task; N and D beside A001 run 1's arms C and D as a between-draw difference; the parts
finished per task and `records left out` per arm; anything that stopped before a model ran; the model-call latency
per task with the runner CPU; the comparison count ("9 this cycle, 21 since the loop began"); and the public-text
sentence.
