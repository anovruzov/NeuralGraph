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
A001 is the same: the judges confirm too much. Under the best measured instruction, a-4b refutes about 48 of 150
negatives (specificity 0.320, A001 run 1, arm C; the count is arithmetic from the rate, the report's exact count not
read) while confirming about 125 of 150 positives (sensitivity 0.833). The residual error that a lever must fix is a
yes on a negative, and every arm below is aimed at it. Counts below that are not in a Runs entry are arithmetic from
the recorded rates and are marked "about" (B9).

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
  is read for the verbatim-substring check is declared in rule 11 and asked once (B1: a reading applied to the reply
  before the pinned `decide` is a declared mapping and secondary under the default, so P = (a-4b, E) and Eg is the
  headline secondary). A call received before the run is adopted by a dated amendment before any run; one received
  after the run bears on the next cycle.
- A001's A2 declared a founder ask on section 2.3's stop rule; `docs/handoff/LOOP-48H-STATUS.md` holds no such line
  under `## Open asks`, so it was not posted (B6). This rule rests on A2's reading for both earlier cycles and posts
  the ask with its own: "NEEDS FOUNDER: section 2.3's stop rule is read as stop when no mechanism verifies a cycle,
  not stop unless the rebuilt sha matches; a verified artifact's refs are the records that cycle judged (CHOICE-A001
  A2; CHOICE-A002, The data and the draw, item 5)." A strict ruling received before the run is adopted by a dated
  amendment.
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
  (rule 5); they are comparators, not arms, and they decide nothing but the composite arms' extra pass clause.
- **Rejected as primary** (gameable): site-level balanced accuracy (the route-role baseline scores 0.808 [0.667,
  0.938] reading no record, L001 runs 1 and 2), codes visible to the judge (it reads the key), and a plain ratio to
  the lexical judge.
- **The secondary** (BA_site − bound) / (BA_central − bound) against a hosted central reader needs a key the lab does
  not have (`NHTSA_HOSTED`, `lab/request.py:262`; LOOP-48H section 7). It is not run and is never assumed. No hosted
  model is named or used anywhere in this cycle.

## The data and the draw

**NHTSA complaints, as J001 and A001 drew them, and nothing else in this cycle.** The reasons are A001's ("The data
and the draw"): the pinned definition names them; 150 records give 300 questions and 150 clusters with a balanced
accuracy standard error near 0.035; the key's noise is shared by every judge; J001's pipeline (`lab/j1.py`,
`lab/goldlabels.py`) is pinned and importable, so the questions, the comparators and the scoring of this cycle are
J001's code, called unchanged; and A001's exclusion machinery (`lab/j2labels.py`) is importable, so the eligible
list, the draw and the artifact checks of J001's labels file (J1) and of A001's chain file (K) are A001's code, called
unchanged; A001's labels file (A1) is checked by `lab.j3labels.earlier_labels_refs`, A001's check generalised to the
j2 layout and pinned equal to it on the j1 layout by a test (B3).

**A fresh, verifiable draw (section 2.3), with two earlier cycles excluded.** The lab draws
`random.Random("nhtsa:<seed>").sample(eligible, n)` from a complaint file downloaded live in the plan job
(`lab/goldlabels.py:90, 133`), so a new seed alone gives no disjointness, and record refs may never be committed. The
exclusion happens on the runner, in the j3 plan job, in a new module `lab/j3labels.py` that imports `lab/j2labels.py`
unchanged. Every earlier cycle is excluded by the artifact mechanism and by the rebuild mechanism:

1. **J001's 150 records, mechanism 1 (the artifact).** The plan job downloads J001's plan artifact
   `lab-plan-37996675559-1` (run 37996675559; retention 30 days, until 2026-11-08T22:00:03Z; CHOICE-A001, item 1 and
   A1) into `${{ runner.temp }}/lab-earlier/37996675559` with `actions/download-artifact` under the workflow's
   read-only `actions: read`, as A001's plan job did: the workflow's `earlier` step, keyed on
   `steps.plan.outputs.j2_exclude_run_id`, which `lab.plan.plan_outputs` fills from the first `exclude` entry of a j2
   plan or of a j3 plan (B2). `lab.j2labels.earlier_refs` is called unchanged with the entry
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
   2026-11-10) into `${{ runner.temp }}/lab-earlier/38114522160` by a second download step `earlier-2` of the same
   shape, keyed on `steps.plan.outputs.j3_exclude_run_id_2` (B2; the workflow change is declared under "The build
   plan"). The check is A1's, implemented in `lab/j3labels.py` as `earlier_labels_refs(directory, entry)`, a sibling
   of `lab.j2labels._check_earlier` generalised to the j2 layout (`prereg/j2/labels.jsonl`, `prereg/j2/labels.json`
   of kind `lab_j2_labels`, `prereg/j2/questions.jsonl`, the record `{source: nhtsa, n: 150, seed: 2}`), because
   `lab.j2labels.earlier_refs` unchanged returns `malformed` for a file outside its `EXCLUDE_FILES` and hardcodes the
   j1 paths and `REBUILD` seed 1 (`lab/j2labels.py:53-58, 152-182`); its outcomes are `lab.j2labels.OUTCOMES`, every
   read inside `try`/`except`, nothing from the artifact printed, written or uploaded but the refs on the runner
   (B3): the file `prereg/j2/labels.jsonl` must hash to the prefix `623780b8a1de` (CHOICE-A001, Run 1: "labels sha256
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
   - **The sources (B5).** **J001's set** is established when J1 or R1 verified; else, when K verified and A001's
     set is established by A1, as K less A1, printed "J001 by chain" (A001's artifact recorded J001's refs by
     mechanism 1 and the chain file is sha256-pinned). **A001's set** is established when A1 or R2 verified; else,
     when K verified and J001's set is established by J1 or R1, as K less J001's set, printed "A001 by chain". K
     alone, with neither cycle otherwise established, establishes neither (the split is not recoverable) and the plan
     job stops. R2 is attempted over the eligible list less J001's set whenever that set is established (by J1, by R1
     or by the chain); else its outcome is `not attempted`.
   - **The consistency checks**, each printed as a count and stopping above 0: J1 and R1 equal when both verified
     (the symmetric difference); A1 and R2 equal when both verified; every other verified source's refs a subset of K
     when K verified (the count of refs outside K); K equal to the union of J001's set and A001's set when both are
     established without the chain (the symmetric difference).
   - **The excluded set is the union of every verified source's refs** (J1, R1, A1, R2, K): a verified source's refs
     are records an earlier cycle judged, whichever source gives them, so the union never costs freshness and is
     never smaller than the union of the two established sets; 300 refs expected, and the count is printed.
   - **The plan job stops** before any model runs, and the loop asks (an open ask in `LOOP-48H-STATUS.md`), when
     either cycle's set is not established or when any consistency count is above 0; it does not run.
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
   count, the eligible count after exclusion, the overlap (0, else the plan job stops), every source's outcome
   (`exclusion.mechanisms` holds J001's pair, `artifact` J1 and `rebuild` R1, as A001's record did;
   `exclusion.excluded_runs` holds one entry per cycle in seed order with that cycle's `artifact` and `rebuild`
   outcomes and, for A001's, `chain` K and `derived` the by-chain reading when one was used, the counts found and the
   requested keys; the record's kind stays `lab_j2_labels`, written by `lab.j2labels.build_labels` unchanged and
   extended by `lab.j3labels` as `lab.prereg._j2` extends A001's, `lab/prereg.py:441-443`; B22) and the complaint
   file's sha256.
7. **The chain, carried forward (A3).** The plan job writes `prereg/j3/excluded.jsonl`: one `{"record_ref"}` line per
   ref, sorted, refs only, the union of the 300 excluded refs and this draw's 150 (450 lines expected), and prints its
   sha256 prefix. The next cycle names this run's artifact, this file and that prefix, and excludes every earlier
   cycle as this one does: by its own artifact and its own rebuild, chained in seed order. Every later CHOICE lists
   every earlier run id and labels sha256 prefix; for this cycle the list is: J001, run 37996675559, labels
   `bc6d092d8bca`, seed 1; A001, run 38114522160, labels `623780b8a1de`, seed 2, chain `9a948cbf2cd9`.
8. **What is printed:** one fixed line with the `available` count (the eligible records, `goldlabels.py:143`;
   A001's day: 58,319), the excluded count (300 expected), the eligible count after exclusion, the overlap count
   between the new draw and the excluded set (0 expected), the outcome of each of the five sources (J1, R1, A1, R2,
   K) with any by-chain reading named and the consistency counts that were computed (B5), the new labels' sha256
   prefix, the chain file's prefix and the complaint file's prefix. Never a ref. If fewer than 150 eligible unexcluded
   records remain, the plan job stops, and no further fresh draw is pre-registered. On a re-run after a plan job
   printed (rule 16), the first plan job's line is recorded beside this one (B13). Then the comparators' figures
   (rule 8), before any shard starts; those figures are never a reason to redraw (LOOP-48H section 6.3).

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
     reply with `jsonparse.extract_object` over `jsonio.strict_load`, `json.loads` with an `object_pairs_hook` that
     keeps the document's key order and refuses duplicates, `mycelic/collective/inference/jsonparse.py:124`,
     `mycelic/collective/jsonio.py:94-123`; B11) at write time, from the parsed reply's document order, and the
     aggregate reports the share of replies that begin with the new field, per task; the stored verdict line is
     canonical JSON with sorted keys, so the aggregate checks the stored `first_key` for shape and never recomputes it
     (B19). That share is reported; it decides nothing.
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
     `extraction.max_input_chars`): the string whose canonical JSON encoding the model received inside the data block
     (`tasks.py`, `data_block`: `<` escaped as the six characters `\u003c`, newlines and quotes JSON-escaped,
     non-ASCII literal, `jsonio.py:143`), compared after the reply's own JSON decoding, so that a faithful copy
     decodes to the same characters (B12). Let `norm(s)`
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
5. **The arms: nine, declared now, all on the one model (rule 7); one primary pair.** Model calls are made for the
   three tasks only; every arm is a reading of one task's replies, or a join of one arm's verdicts with a plan-job
   comparator's, reply by reply, before the pinned `decide` or at the verdict (composite arms). Letters name the
   lifts of rule 10.
   | Letter | Arm | Task | Reading before the pinned `decide` |
   |---|---|---|---|
   | N | `negation` | N | the two answers as answered (A001's arm C, the control) |
   | D | `negation_mapped` | N | M (A001's arm D) |
   | **E** | **`evidence`** | **E** | **the two answers as answered; the citation ignored** (the primary arm, B1) |
   | Eg | `evidence_grounded` | E | G (the headline secondary, B1) |
   | Egm | `evidence_grounded_mapped` | E | G, then M |
   | R | `rationale` | R | the two answers as answered; the analysis ignored |
   | Rm | `rationale_mapped` | R | M |
   | C | `composite` | E (and the lexical judge) | at the verdict: `confirm` when the lexical judge's verdict on the question is `confirm`, else Eg's verdict |
   | Cm | `composite_mapped` | E (and the lexical judge) | at the verdict: `confirm` when the lexical judge's verdict on the question is `confirm`, else Egm's verdict |
   - **The composite rule (lever 4), exactly.** For each question `q`: let `L(q)` be the lexical judge's verdict
     line for `q` written by the plan job (`prereg/j3/lexical.jsonl`, `lab.j1.lexical_verdicts` over the pinned
     `verify.lexical_judge`, rule 8), and `X(q)` the model arm's verdict (Eg for C, Egm for Cm; `unknown` when the
     model failed). `composite(q) = confirm if L(q) == confirm else X(q)`. So a composite says yes whenever the
     lexical judge does, and otherwise defers to the model; its answer-pair counts are the model's. It calls the
     pinned lexical judge through the plan job's lines, joined by question id; the lab holds no other lexical judge.
   - **The ablation comparators, declared (lever 4):**
     - **ablation A, `lexical_or_no`:** the same composite with the model's reply replaced by the constant reply
       `{"mentions_entity": "yes", "describes_predicate": "no"}`, whose verdict under the pinned `decide` over the one
       record is `refute`: `confirm` where the lexical judge confirms, else `refute`. It inherits the lexical judge
       and nothing else; on a draw where the lexical judge never answers unknown (J001 and A001: unknown share
       0.000, RESULTS runs 9 and 11) it is the lexical judge's verdicts exactly, line for line, so
       `C_minus_ablation_A` equals `C_minus_lexical` to the record (the same bootstrap draws) and its clause and the
       "beats lexical" clause are one test; it is declared because the lever names it and because a lexical
       `unknown` (a record whose language the pack does not support) would separate them, and the Runs entry says
       which case held, the lexical judge's unknown count on this draw (B8);
     - **ablation B, `lexical_or_record_blind`:** the same composite with the model's reply replaced by the
       record-blind control's verdict for the question (`lab.j1.prior_verdicts`, `PRIOR_RULE`, `PRIOR_TOP`
       unchanged): `confirm` where the lexical judge confirms, else the control's verdict.
     Both are computed in the plan job, before any model runs, on every record, and in the aggregate on the records
     each composite arm scored; both are paired with each composite arm by `lab.j1.paired` unchanged. The pass test
     of a composite arm (rule 12) additionally requires the paired difference to ablation A **and** to ablation B to
     have their 95% lower ends above 0, so that a composite passes only where the model's replies add to what the
     lexical judge and a reader that reads nothing already give. Both are declared because the lever text allows
     either and the stricter reading costs nothing; on a draw without a lexical `unknown`, the clause that adds a
     test is ablation B's (B8).
   - **The primary pair P = (a-4b, E `evidence`)** (B1). Why is under "Why these arms, and why this primary pair".
     **Every other arm is a secondary**, Eg the headline secondary, each reported with the same statistics, including
     rule 12's pass test, and a secondary that meets the pass test is a hypothesis for the next cycle's primary,
     never a result.
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
    - the lifts, each named by the lever it reads: `E_minus_N` (the citation field together with its schema S_E and
      the 512-token cap, answers read as shipped; the three are not separable in this cycle, B18; P over the
      control), `Eg_minus_E` (the verbatim check), `Eg_minus_N` (field and check together: the headline secondary
      over the control), `Egm_minus_Eg` (the mapping under the check), `Egm_minus_D` (field and check under the
      mapping), `R_minus_N` (the rationale field together with S_R and the 512-token cap), `Rm_minus_D` (the
      rationale under the mapping), `D_minus_N` (the mapping under N: A001's
      D − C, replicated), `C_minus_Eg` and `Cm_minus_Egm` (the lexical disjunction over the grounded arms);
    - the ablation differences `C_minus_ablation_A`, `C_minus_ablation_B`, `Cm_minus_ablation_A`,
      `Cm_minus_ablation_B` (the composite arms' extra pass clauses).
11. **The primary pair, the headline arm, and the exploratory rest.**
    - **P = (a-4b, E `evidence`)** (B1). The 86 verdict of rule 12 is read on P alone. The aggregate computes the
      verdict word for every arm, so the Runs entry prints Eg's word beside P's, labelled "the primary under the
      other reading of the section 7 default"; a ruling received after the run is answered from that line and
      changes nothing in this cycle.
    - **How P meets "chosen from earlier recorded results before the run."** The model: a-4b is the only model
      whose answers separated positives from negatives in J001 (yes on 97 positives and 37 negatives, RESULTS run 9)
      and the only one any lever moved in A001 (lifts 0.270 [0.217, 0.327] and 0.360 [0.323, 0.393] against 0.000
      and -0.010 to 0.030 for the other two, CHOICE-A001 Run 1). The arm: under the best measured instruction
      a-4b's recorded residual is specificity 0.320 at sensitivity 0.833 (A001 run 1, arm C), that is, yes answers on
      negatives; lever 3 is the next lever in the pinned order, and E, its evidence-span field read by the shipped
      `decide` on the answers as answered, is the first lever-3 arm the section 7 default allows as primary (B1);
      the verbatim-substring check G, whose only action is to turn a yes that cannot be grounded in the record into
      a no, is the declared mapping on E's replies and the headline secondary. No (model, arm) pair measured on a
      fresh draw met any pass test (A001: every verdict word `failure`), so none is promoted; P is a new arm built
      on the measured instruction.
    - **The section 7 default, read as A001's A7 read it (B1).** A reading applied to the reply before the pinned
      `decide` is a declared mapping and secondary under the default ("shipped `decide` primary, declared mappings
      secondary"), and G is such a reading, as M is. **P = (a-4b, E `evidence`)**; Eg is the headline secondary and
      the first hypothesis for the next cycle's primary. A chief-scientist call received before the request push
      that names lever 3's verbatim-substring check primary-eligible promotes P to (a-4b, Eg) by a dated amendment
      before any run, and the headline arm with it; a call that counts unclear as refute names (a-4b, Egm) the same
      way. The ask, one line, in the loop's next reply and under `## Open asks` of `LOOP-48H-STATUS.md`: "NEEDS
      CHIEF SCIENTIST: may lever 3's verbatim-substring check (G: a yes read as no when the citation is not a
      substring of the record's text, applied before `decide`) be A002's primary arm? Default if silent: no;
      P = (a-4b, evidence), Eg the headline secondary." No request is pushed on a reading this file gives itself.
    - **The headline arm is E, P's arm** (one model, so one headline): E's balanced accuracy interval is read
      against the lexical judge's balanced accuracy on the same records as J001's rule 7 reads it: **better** only
      when the interval's lower end is above it, **worse** only when its upper end is below it, otherwise **not told
      apart**. Eg's line is reported beside it in the same words, labelled the headline secondary (B1).
    - **Every other arm is exploratory:** N and D (the controls; D is A001's mapping secondary, decision 9 pending),
      Eg (the headline secondary), Egm, R, Rm, C and Cm. Each is reported with the same statistics as P, including
      rule 12's pass test (with
      the ablation clauses for C and Cm). The lifts of rule 10 are read as "helps" when the lower end is above 0,
      "hurts" when the upper end is below 0, otherwise "not told apart"; they decide nothing toward 86.
    - **The comparison count.** This cycle makes 9 (model, arm, cycle) comparisons: 9 arms × 1 model × 1 cycle,
      the composite arms and the controls counted. 12 were made before it (A001 run 1: "12 since the loop began").
      The running count since the loop began is therefore **21** after this cycle. The request carries
      `comparisons_before` 12; the Runs entry prints the count. No multiplicity correction is applied: the 86 verdict
      is read on P alone and the one headline is P's arm, so there is one decision per cycle; the eight other verdict
      words are exploratory and are not corrected, which is why a secondary that passes is a hypothesis and never a
      result (section 2.5, One primary pair and Multiplicity), and the running count is printed so that a reader can
      discount a sampling peak across cycles (B15).
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
    with the ablation figures; a composite whose ablation difference is not a number on either ablation (an ablation
    file missing, no common record) is `incomplete`, as a missing bound or lexical figure makes any arm
    (`lab.j2.verdict_word`; B8). The aggregate computes the word for P and, labelled exploratory, for every other
    arm. Success in the sense of the rule cannot be reached in this cycle.

    Equality fails every strict clause (B17): a paired lower end of exactly 0 does not beat the lexical judge and a
    balanced-accuracy lower end equal to the bound does not beat it (A001 run 1: P's point value equalled the bound
    and its lower end lay below it; `lab.j2.pass_test` compares with `>`). The headline of rule 11 is read from the
    arm's own interval against the lexical judge's point value and the pass test from the paired difference's
    interval, which removes the record variance both judges share; the two can disagree on one draw, and where they
    do the verdict word follows the paired clause and the headline is reported as the per-model line it is.
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
    - each unit has 300 minutes (the request's `minutes`) and runs alone in its shard; `job_minutes` 330 gives a
      shard capacity of 305 and a job timeout of 325 (`SHARD_OVERHEAD_MINUTES` 25, `lab/request.py:193`);
      `max_parallel` 5 (the cap is 16, `lab/request.py:967`); the unit's budget is the smaller of 300 minutes and the
      shard's remaining time (the job start plus 315 minutes, the timeout less `JOB_TAIL_MINUTES` 10,
      `lab/shard.py:138-153`, less `DEADLINE_MARGIN_S` 120 s, `:486`) less `SIM_BUDGET_MARGIN_S` 30 s
      (`lab/units.py:189`, `build_argv`), so 300 is its upper bound, and provisioning, the server start and the
      warm-up come off it when they take more than about 13 minutes (B20);
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
    `decide`; never a narrative beyond what the reply itself quotes. The reply's first key is read from the parsed
    reply's document order at write time (`runtime.py:340-350`, `jsonparse.extract_object`); the stored line is
    canonical JSON with sorted keys (`lab.j2.jsonl_bytes`' shape), so the aggregate checks `first_key` for shape, one
    of the task's schema property names, and never recomputes it (B19). The aggregate recomputes every reading's
    verdict from the stored reply with the pinned `decide`, recomputes `grounded` from the stored citation and the
    payload text it rebuilds from the plan artifact's labels file (`lab.j1.payload`), and refuses a line that
    disagrees with any of them (A001's A8, with two more readings). Unlike A001's check, which recomputed every
    verdict over `lab.j1.stand_in` and read no narrative (`lab/j1.py:327-332, 381`; `lab/aggregate.py:1092-1097`
    reads the plan artifact's `lexical.jsonl`, `prior.jsonl` and questions only), this recomputation reads the drawn
    narratives from the plan artifact's `prereg/j3/labels.jsonl` through `lab.j1.payload` (the pack loaded from the
    repository), inside the aggregate job only; nothing of a narrative or a citation leaves it: the report, the step
    summary and the console hold counts, scores, lengths and hashes, and the line check's refusal names a key, never
    a value (B23). The plan artifact holds the labels, questions, lexical, control,
    ablation and chain files (public complaint text and refs; the artifact, never the repository). Reports, step
    summaries, console lines and commits hold counts, scores and hashes only: no narrative, no citation, no analysis,
    no record ref, no VIN, no city, no dealer, no operator name. The warm-up calls the three tasks once on a generated
    record about no drawn record (A001's A11: the probe, sent twice for `repeat_identical`, is the first fitting
    task's in sorted (task name, pack) order, `lab/warmup.py:5-7, 46-47`; `warm_tasks` `:242-244`,
    `sorted(found.items())`; the probe `:391-394`, `fitting[0]`; for a j3 unit the shipped task is not among the
    unit's tasks, so the probe is task E's, `judge_record_evidence` being the first of the three task names in sorted
    order; `repeat_identical` is recorded for task E, P's task, and for no other task; B4).
16. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3). A set left incomplete gets no verdict from this
    run; judging it again is a new choice file. Any change to a setting, to E's or R's text or schema, to g, G, M or
    the composite rule is a new choice file; a warm-up reply cut at the cap on the generated record is the one
    setting change this rule allows before a re-push, by a numbered amendment that raises that task's `max_tokens`
    (trap 20, B7), as section 6.3 allows a lab-side cause fixed by amendment. If the plan job printed and the run
    then failed before any model judged, the re-run's plan job draws from the complaint file of its own day:
    `random.Random("nhtsa:3").sample` over that day's eligible list, so a changed file gives other records under the
    same seed; the Runs entry records both plan jobs' fixed lines (labels, questions, chain and complaint-file
    prefixes, the five sources' outcomes) and both comparator prints, and the second draw is the result; a
    complaint-file prefix that differs between the two is reported; the primary pair, the seeds and n do not change,
    the draw stays fresh (exclusion by refs) and no pass path opens, since P was fixed before either draw (B13).
    After the run, no reading other than the nine arms is made of the replies: anything else they suggest (another
    floor for g, another composite rule, a reading of the analysis text) is a hypothesis for the next cycle's rule.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Models | a-4b; one run; temperature 0 (the client), fixed server seed; `--reasoning off` (`lab/models.json`, unchanged) |
| Population | NHTSA complaints, pack `docs/collective/replay/vehicles/pack`, `NHTSA_MAKES` and `NHTSA_WINDOW` verbatim, j1 eligibility |
| Excluded | J001's 150: run 37996675559, artifact `lab-plan-37996675559-1`, `prereg/j1/labels.jsonl` sha256 `bc6d092d8bca…`, rebuild seed 1; A001's 150: run 38114522160, artifact `lab-plan-38114522160-1`, `prereg/j2/labels.jsonl` sha256 `623780b8a1de…`, chain `prereg/j2/excluded.jsonl` sha256 `9a948cbf2cd9…`, rebuild seed 2 over the eligible list less J001's set; 300 refs expected; each cycle by its artifact and its rebuild, one verifying source per cycle suffices, verifying sources must agree; a cycle with no source of its own is established by the chain K less the other cycle's established set, K alone establishing neither; the excluded set is the union of every verified source's refs (B5) |
| Labels | n 150, seed 3 (`random.Random("nhtsa:3")` over the eligible records less the 300 excluded), codes removed |
| Questions | `lab.j1.build_questions`, seed 3, parts 5; `NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged |
| Tasks | N `judge_record_negation` (A001's, 256 tokens, shipped schema); E `judge_record_evidence` (512 tokens, S_E); R `judge_record_rationale` (512 tokens, S_R); instructions and schemas as rule 3, pinned by sha256; pass order E, R, N |
| Mappings | M: `describes_predicate` `unclear` read as `no`; G: `describes_predicate` `yes` read as `no` when the citation is not grounded; g: `norm` is whitespace-collapsed, stripped, case-folded; at least 4 characters; a substring of `norm(text)` |
| Arms | N `negation`, D `negation_mapped`, E `evidence`, Eg `evidence_grounded`, Egm `evidence_grounded_mapped`, R `rationale`, Rm `rationale_mapped`, C `composite`, Cm `composite_mapped` |
| Composite | `confirm` where the lexical judge's verdict is `confirm`, else the grounded arm's verdict (Eg for C, Egm for Cm) |
| Ablations | A `lexical_or_no` (constant reply yes/no, so `refute`, where the lexical judge does not confirm); B `lexical_or_record_blind` (the record-blind control's verdict there); both in the plan job |
| Primary pair | P = (a-4b, E) (B1); Eg's verdict word printed beside P's, labelled the primary under the other reading of the section 7 default |
| Headline arm | E, P's arm; Eg the headline secondary, its line beside E's; every other arm exploratory; 9 (model, arm, cycle) comparisons this cycle, 21 since the loop began; no multiplicity correction (B15) |
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
  difference (A001's arm A was 0.080 below J001's a-4b, inside the about 0.10 that two standard errors of a
  difference between two independent draws span, B14). D stays because decision 9 is pending and the mapping's lift
  under N is measured (0.077 [0.047, 0.107]).
- **Arm E (the primary, B1)** separates two effects that one arm would confound: asking for a citation may itself
  change the answers (a model that must quote may answer no where it cannot), and the check changes the verdicts.
  E − N reads the first; Eg − E the second. E is P because it is the first lever-3 arm the section 7 default allows
  as primary (rule 11): its verdicts are the pinned `decide` on the answers as answered. **The prior expectation for
  P, from A001's figures: failure.** E's instruction is N's with the citation request added, and nothing recorded
  says that asking for a passage moves the answers, so E is expected near N's 0.577 [0.530, 0.623] (A001 run 1,
  arm C), below the lexical judge; E − N is the reading that says whether the request alone moved anything.
- **Arm Eg (the headline secondary, B1)** attacks the measured residual directly. Under N a-4b did not refute about
  102 of 150 negatives (specificity 0.320; the count is arithmetic from the rate, B9) and about 41 of its 300
  verdicts were `unknown` (unknown share 0.137; an unknown verdict is an unclear answer in either field, a no or an
  unclear to `mentions_entity`, or a failed call, `verify.py:243-259`; no call failed in A001; the split by kind and
  by cause is in the run's report and was not read; B16). G turns a yes without a grounded citation into a no.
  **The prior expectation, from A001's figures:** if G removed every ungrounded yes on a negative and no yes on a
  positive, sensitivity would stay 0.833 and specificity would rise to (150 − u)/150, where u is the number of
  unknown verdicts on negatives (every one stays unknown under G; under M those whose `mentions_entity` is yes
  become `refute`, which is why Egm's ceiling is higher), between about 16 (41 less the about 25 positives not
  confirmed, at most) and about 41: balanced accuracy between 0.780 and 0.863. If G removed true confirms as often
  as false ones, it would move nothing or hurt. So a pass on Eg needs the favourable end of that range and a check
  that removes nothing true, and only the upper half of the range is far enough above the lexical judge for
  `partial` to be readable ("150 records resolve little", B14); the likely outcomes are failure or partial,
  exploratory either way, and the cycle still measures what the loop needs: whether a verbatim check moves
  specificity without the mapping. Eg is the first hypothesis for the next cycle's primary; a chief-scientist call
  before the request push can promote it to this cycle's (rule 11).
- **Arm Egm** is Eg with the mapping, the arm with the highest arithmetic ceiling: with every unclear on a negative
  read as no, (0.833 + 1.000) / 2 = 0.917 at the limit. It is a secondary under the section 7 default; a decision 9
  call before the run promotes it (rule 11).
- **Arm R (and Rm)** tests the other lever-3 variant: a rationale before the answer. a-4b runs with reasoning off
  (`lab/models.json`), so the analysis field is the only place the model writes before it answers. No recorded figure
  predicts its direction; that is why it is measured, under the shipped reading and under the mapping.
- **Arms C and Cm (lever 4)** add the lexical judge's confirms to the grounded arms. On A001's draw the lexical
  judge confirmed about 50 of 150 positives and about 1 of 150 negatives (sensitivity 0.333, specificity 0.993; the
  counts are arithmetic from the rates, B9), so the
  disjunction can only raise sensitivity by the positives the lexical judge catches and the model misses, at a cost
  of at most about one negative in 150. It is built on the grounded arms, whose check may lower sensitivity, because
  that is where a near-free sensitivity backfill is worth measuring. A composite that says yes whenever the lexical
  judge does inherits the lexical score with the model contributing nothing; that is what ablation A measures, and
  why the composite arms' pass test needs the paired difference to both ablations above 0.
- **Why a-4b alone.** a-0p5b and a-1p5b scored 0.500 [0.500, 0.500] and 0.477 [0.447, 0.503] in J001 (RESULTS
  run 9) and 0.467 to 0.497 on every arm in A001 (RESULTS run 11): a-0p5b answered `describes_predicate` yes on 145
  of 150 positives and 145 of 150 negatives in J001 (CHOICE-J001, Runs; B16); a-1p5b under N moved toward yes
  (sensitivity 0.880 to 0.973, specificity 0.053 to
  0.020, A001 run 1); the mapping changed no score of either (lifts 0.000 [0.000, 0.000]); every lift on them was
  not told apart from zero. Both levers of this cycle ask the model to follow an instruction (quote a passage;
  write an analysis) that a model which did not follow N cannot be expected to follow, and the composite and the
  grounding check read as the lexical judge on a model that answers the same way every time. Running them would add
  six comparisons to the multiplicity count and runner minutes, with no reading toward 86 that A001 did not already
  give. They return when a lever is one they can follow, or when lever 8 replaces them with a larger local rung.
- **Why no predicate-descriptions arm.** The pinned order's lever 3 names "predicate descriptions". The pack's only
  per-predicate text is the `label` (already in every payload as `predicate_label`, `verify.py:147-160`) and the
  `lexicon` phrases of `docs/collective/replay/vehicles/pack/vocabulary.json` (one to three phrases; for 19 of the
  27 predicates the phrase is the label in lower case, for 8 it drops a word or splits the label into two or three
  phrases, the twin and the slash-named predicates among them, B24); `codes.json` holds one code per predicate with
  the same label. Those phrases are the
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
  only the looseness of the budget covers it. The 195 minutes assume the medians and task R alone at the cap. At the
  recorded 95th percentile (16.4 s, A001 run 1, a-4b on the slowest CPU model) with both E and R cut at the cap on
  every call, the three passes would need more than 300 minutes; the budget stop then falls in R or N, and E, P's
  set, stays complete (rule 14; B20).
- **One unit per shard** lets a slow unit run to its budget without delaying another. The expected wall time is
  about 1 to 2 hours, dominated by provisioning, the warm-up and the rationale pass; the five shards run at once.
- If a pass does not finish anyway, rule 14 applies.

## How it is read

- **The verdict on P** is read by rule 12 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p."
- **The honest expectation.** a-4b's best measured arm under the shipped verdict rule scored 0.577 [0.530, 0.623]
  (A001 run 1, arm C) and the lexical judge 0.663 [0.627, 0.703]; a pass needs 0.860 and an interval to the lexical
  judge above 0. P = (a-4b, E) is expected near arm C, so P's expected word is **failure** (B1). Eg's arithmetic
  ceiling from A001's figures is 0.780 to 0.863, and only the upper half of that range is far enough above the
  lexical judge for `partial` to be readable (see "150 records resolve little", B14); its likely words are failure
  or partial, exploratory either way. The cycle still measures lever 3 twice and lever 4 once, with their ablations,
  and replicates A001's arms C and D on a third draw.
- **The headline** (rule 11) says, for E, P's arm, whether a-4b with the evidence field judges better than, worse
  than, or not apart from the lexical judge, as J001's rule 7 and A001's rule 9 said for their arms, and reports Eg's
  line beside it in the same words, labelled the headline secondary (B1).
- **The lifts** (rule 10) say what each lever did. Read them with sensitivity and specificity: a lever that raises
  specificity by lowering sensitivity by the same amount moves balanced accuracy little and shows no reading. For
  Eg − E, read the grounded share of yes answers on positives against negatives: a check that removes yes answers on
  both kinds alike is a cost with no reading; one that removes them on negatives and keeps them on positives is the
  lever working. E − N and R − N carry the schema and the cap with the field; a cycle that wants the field's text
  alone needs a control task with an unused string field, which this rule does not run (B18).
- **The composite arms** are read against both ablations first: a composite not told apart from ablation A (here,
  from the lexical judge) is the lexical judge with the model contributing nothing; one above it but not above
  ablation B adds no more than a reader of the question's predicate alone adds. On a draw where the lexical judge
  gives no `unknown` verdict (both earlier draws), ablation A is the lexical judge's verdict line for line,
  `C_minus_ablation_A` equals `C_minus_lexical` and the ablation-A clause repeats the pass test's lexical clause; the
  extra pass clause that bites is then ablation B's, and the Runs entry says which case held (B8). Only a composite
  above both, by the paired lower ends, is read as the model contributing, and even then its word is read by
  rule 12.
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
  0.623] and 0.653 [0.610, 0.697]). The two draws are independent, so the difference of two point values has a
  standard error of about 0.05 (the square root of 2 times 0.035); differences within about 0.10 are sampling; a
  larger one says the draws differ, and both are reported (B14).
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. Under G a judge that quotes nothing refutes everything
  it would have confirmed and scores 0.5 at best on its yes answers; under M a judge that answers unclear everywhere
  refutes everything and scores 0.5: neither mapping shows a reading by itself, which is why the pass test needs the
  lexical judge and the bound beaten. The record-blind control and the predicate-only bound are read as J001 reads
  them: neither decides anything; a model at or below the bound is not shown to read the record.
- **150 records resolve little (B14).** With the pinned standard error of about 0.035 (section 2.5), a
  balanced-accuracy interval is up to about 0.14 wide; A001 run 1 recorded 0.087 to 0.093 for a-4b's arms and 0.093
  to 0.107 for their paired differences to the lexical judge. A paired difference to the lexical judge is told from
  0 only when it is about 0.05 or more (A001's paired half-widths 0.047 to 0.054), so `partial` needs an arm near
  0.72 or above on a draw where the lexical judge scores near 0.66, and an arm between the two is `failure` for want
  of power, not evidence that the lever does nothing; the lifts of rule 10 are read the same way.
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
4. **The check costs true confirms.** A model that answers yes rightly but paraphrases, writes the data block's
   escape sequences as literal characters (a backslash-n for a line break, a backslash-quote for a quotation mark),
   joins two lines without the space the break stood for or changes a character, or quotes a passage beyond the
   6,000-character cut loses the confirm under G; a faithful copy across a line break decodes to a newline that
   `norm` collapses, and is grounded (B12). The sensitivity of Eg against E carries that cost, and E − N against
   Eg − E separates it from the field's own effect.
5. **The data block's encoding.** The narrative reaches the model as canonical JSON inside the data block with every
   `<` written as the six characters `\u003c` (`tasks.py:70-75`, `_escape_lt`, `data_block`; B12); a copied passage
   comes back through the model's JSON reply, so
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
   Timings differ with cache hits; the warm-up's last request is task E's probe, so the E pass starts on a warm
   prefix and the R and N passes on cold ones, and the first call of each of those two passes is slower (B4);
   latency is reported per task and decides nothing.
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
    freshness against those draws rests on its rebuild, which rests on the complaint file not having changed in the
    makes and window, and on the chain under item 5 (B5): J001's set by K less A1 while A001's artifact lives,
    A001's set by K less J001's set while J001's lives; after both expire, on the rebuilds alone, with this cycle's
    chain file carrying all three sets forward for the next cycle. A cycle that cannot establish a set stops and
    asks; it does not run.
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
    under rule 16 and section 6.3, not a result. A warm-up reply cut at the cap (`finish_reason` `length`) on task E
    or R skips every j3 unit of the shard (`lab/warmup.py:382-383`, `WARMUP_LENGTH`, the `calls` step; a unit depends
    on each task its adapter routes), which is also an infrastructure failure under rule 16 and section 6.3,
    recorded in Runs as "stopped before any model ran" with the task named. Because the server is deterministic at
    temperature 0 with a fixed seed, a re-run unchanged repeats the cut, so the re-run is not unchanged: a numbered
    amendment raises that task's `max_tokens` (a cause independent of any printed figure, the warm-up's generated
    record being no drawn record), reviewed before the re-push, and the budget arithmetic is redone (B7).

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
- **How the section 7 default reads for the verbatim-substring check** is declared in rule 11 as A001's A7 read it
  (G is a declared mapping, secondary; P = (a-4b, E)) and asked once; the promotion of Eg to the primary is the chief
  scientist's call, adopted by a dated amendment only when it arrives before the request push (B1).
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
  evidence}` (B1); `comparisons_before` 12;
- `experiments.j3.exclude`, a list of two entries in seed order: J001 (`run_id` 37996675559, `artifact`
  `lab-plan-37996675559-1`, `file` `prereg/j1/labels.jsonl`, `sha256_prefix` `bc6d092d8bca`, rebuild seed 1 with the
  same prefix) and A001 (`run_id` 38114522160, `artifact` `lab-plan-38114522160-1`, `file` `prereg/j2/labels.jsonl`,
  `sha256_prefix` `623780b8a1de`, the chain file `prereg/j2/excluded.jsonl` with prefix `9a948cbf2cd9`, rebuild seed 2
  with prefix `623780b8a1de` over the eligible list less the first entry's set).

`lab.request` validates every field (integers in range, artifact names of the lab's shape, lower-case hex prefixes of
at least 12 characters, files from the j3 block's own fixed list `lab.j3labels.EXCLUDE_FILES`, `prereg/j1/labels.jsonl`
for J001 and `prereg/j2/labels.jsonl` for A001 with the chain key naming `prereg/j2/excluded.jsonl`, since
`lab.request`'s j2 list, `lab.j2labels.EXCLUDE_FILES` at `lab/j2labels.py:54`, refuses the second (B3); the primary
arm one of `lab.j3.ARMS`, the model one of the block's and a gguf model, seeds in order); `lab.plan --github-output`
echoes both excluded runs and artifacts (`j2_exclude_*` for the first, `j3_exclude_*_2` for the second, B2); the plan
job downloads both.

## The build plan

The build starts only after this rule's review and any amendment are committed. It changes no pinned file:
`lab/j1.py` and `lab/goldlabels.py` (J001's `CODE_FILES`), `lab/j2.py` and `lab/j2labels.py` (A001's `CODE_FILES`,
`lab/j2.py:115`), nothing under `mycelic/collective/`, `connectors/openfda.py`, the vehicle pack, any earlier
`CHOICE-*.md`, `run-*.json` or `*-settings.json`.

- **A new kind `j3`, not additive arms in `j2`.** `lab/j2.py`'s constants `TASKS`, `ARMS`, `LIFTS`, `LINE_KEYS` and
  `PREREG_KEYS` are read by the request validator (`lab/request.py:611`, the primary arm), the unit adapter
  (`lab/units.py:292`) and the aggregate (`lab/aggregate.py:1111`: the comparison count is `len(ARMS)` times the
  models); the summary holds its own copies of the arm and lift names (`lab/summary.py:177-178`, `J2_ARMS` and
  `J2_LIFTS`), which extending `lab/j2.py` would desynchronise (B11). Extending them would change what a
  re-aggregation of A001's run computes (its count, its tables, its line check), which is A001's recorded
  computation; and `lab/j2.py` and `lab/j2labels.py` are in A001's code hash. So A002 is a new kind whose own
  constants hold its tasks, arms, lifts and line keys, and which imports from `lab.j2` unchanged: `NEGATION_TASK`,
  `NEGATION_INSTRUCTIONS`, `negation_task`, `MAPPING`, `mapped_reply`, `pass_test`, `verdict_word`, `lift_reading`
  and `task_pins`' shape; and from `lab.j2labels` unchanged: `earlier_refs` (for J1 and K), `rebuild_refs`,
  `eligible_records`, `draw`, `label_lines`, `excluded_jsonl`, `build_labels` and `nhtsa_rows`. A001 can be
  re-aggregated at any later commit with its report unchanged.
- **New modules.** `lab/j3.py` (tasks E and R with their schemas and instructions; g, G and the composite; the nine
  arms, the lifts and the ablation names; the verdict line with its readings and `first_key`; the line check that
  recomputes every reading and `grounded`; the j1-shaped views; the pins; the preregistration document; the runner:
  three passes, E first, A001's budget timer and signals, `complete_by_task` per task); `lab/j3labels.py`
  (`EXCLUDE_FILES`; `earlier_labels_refs`, the A001 labels check of item 3, B3; the two earlier cycles' sources with
  J1 and K through `lab.j2labels.earlier_refs`; the A001 rebuild over the eligible list less J001's set; the per-cycle
  stop rules with the by-chain readings and the consistency counts, B5; the union; the labels record with every
  source's outcome in `exclusion.excluded_runs`, B22; the chain file); `tests/lab/test_lab_j3.py`;
  `tests/lab/j3_data.py` (a synthetic complaint archive of at least 90 complaints per make, six makes, about 540
  eligible records, so that the J001 stand-in, the A001 stand-in and the dry run's own draw, 450 records, fit,
  where `tests/lab/j2_data.py`'s default of 60 per make gives about 360 and `lab.j2labels.draw` would refuse the
  third draw, `lab/j2labels.py:113-116`, B21; `python -m tests.lab.j3_data --out DIR` defaults to it; a synthetic
  J001-shaped earlier artifact and a synthetic A001-shaped one built from it by the pinned code, and a stand-in for
  both entries); `lab/templates/accuracy-002.json`; `docs/collective/A002/BUILD-A002.md`.
- **Wiring, all unpinned (B25; every function a j3 kind touches, so that no unit silently warms nothing, gets no
  pack or falls out of the participation floor, as A001's A10 found).** `lab/request.py` (`EXPERIMENTS` gains `j3`;
  `_J3_KEYS`, `_J3_REQUIRED`; `_j3_exclude_list` validating two entries in seed order, each of `_exclude`'s shape
  with the j3 file list `lab.j3labels.EXCLUDE_FILES`, the second with the chain keys and the rebuild keys, B3;
  `_primary` taking the arms tuple, called with `lab.j3.ARMS`; `_j3`; `validate`'s checks table; the docstring
  block); `lab/plan.py` (`build_units`' j3 branch with the `exclude` list; `exclude_entries(plan)` beside
  `j2_exclude`, which stays; `plan_outputs` and `OUTPUT_KEYS` per B2); `lab/prereg.py` (`_j3`: both directories
  under one `--earlier-plans`, the five sources and the stop rules through `lab.j3labels`, the draw, J001's
  questions and comparators, `prereg/j3/ablation_a.jsonl` and `ablation_b.jsonl`, `prereg/j3/excluded.jsonl`, the
  pins; `preregister`'s `j3_units` and manifest key `j3`; `main`'s `shown` list and a two-entry stand-in for
  `--exclude-stand-in`; the fixed lines); `lab/units.py` (`ADAPTERS["j3"]` with `lab.j3.TASKS` in pass order;
  `required_tasks` returning `[lab.j3.EVIDENCE_TASK]`; `build_argv`'s `in ("j1", "j2", "j3")`; `write_routing`'s j3
  branch with `lab.j3.routing_doc`; `harness_status` dispatching to `_j3_status`: exit 1 with `complete_by_task[E]`
  true is `ok` with `J3_EVIDENCE_STOPPED`, else `failed` with `J1_STOPPED`; `participation`'s j2 branch extended to
  j3; `harness_measurement`, `harness_verdict`, `unit_notes`, `public_text`, `PREREG_EXPERIMENTS` and
  `REPLY_PARTICIPATION` gaining `j3`; `run_unit`'s `with_pack` list, `lab/units.py:1158`); `lab/warmup.py`
  (`warm_tasks`: a j3 branch registering E, N and R with the judge's generated payloads and the unit's `keys`; the
  docstring; the probe is the first task name in sorted order, task E's, B4); `lab/responder.py` (handlers for
  `judge_record_evidence` and `judge_record_rationale` in the pack branch and the `pack_free_judge` branch, the
  lexical answer plus an empty `citation` or `analysis`, so a dry run's replies satisfy S_E and S_R; an unknown name
  is still refused); `lab/aggregate.py` (`_j3_lines`, `_j3_part`, `_j3_arm`, `_j3_composite`, `_j3_model`,
  `j3_block`: the sets, the nine arms with the comparators, the ablations and the verdict words, the lifts and the
  ablation differences, the grounded shares by kind, the citation lengths, the token and cut counts, the first-key
  shares, `primary_result` with Eg's word labelled, B1; `main`'s source map and the report key `j3`; the
  docstring); `lab/summary.py` (`J3_ARMS`, `J3_LIFTS`, `J3_ABLATIONS`; `_j3_prereg_lines`, `_j3_exclusion_lines`,
  `_j3_tables`; `_class_section`'s signature and the report loop at `:778-783`; the `COLUMNS` entries; the notes
  import); `lab/notes.py` (`J3_LABEL`, `J3_ARMS_NOTE`, `J3_VERDICT_NOTE` with the ablation clauses, `J3_PRIMARY_LINE`,
  `J3_EXCLUSION_NOTE`, `J3_STAND_IN`, `J3_EVIDENCE_STOPPED`, `J3_FRESHNESS_UNVERIFIED`, `J3_EXCLUSION_DISAGREES`,
  `J3_TOO_FEW_RECORDS`, every sentence free of ASCII digits, `tests/lab/test_lab_guards.py`
  `test_notes_hold_no_digits`, so no figure can ride in a fixed sentence; the experiment label dict at `:531`);
  `lab/dryrun.py` (`--j3-data`); `lab/__init__.py` (the docstring's kinds); `.github/workflows/mycelic-lab.yml`
  (the `earlier-2` step per B2); `lab/templates/accuracy-002.json`; `lab/shard.py` unchanged. `lab/j2.py`,
  `lab/j2labels.py`, `lab.aggregate.j2_block` and `lab.summary._j2_tables` are not edited, so A001's recorded
  computation is unchanged by all of it. Documentation: `docs/lab/REFERENCE.md` (the request table at `:22`, a `j3`
  block after the j2 block near `:179`, the unit kinds at `:272`, and wherever else it lists `j2`), `docs/lab/README.md`
  (`:14`, `:39`, the j2 paragraph near `:417`), `lab/requests/README.md` (`:12-17`), `docs/lab/INTEGRATION.md` (a
  paragraph after the A001 one at `:247-268`).
- **Tests, declared.** `tests/lab/test_lab_j3.py` pins: E's and R's instructions equal to this file's blockquotes
  (lines joined) and N's text verbatim with the new sentences inserted before the last; that every `rule N` of this
  file, N in 1 to 16, names the item whose bold title the test lists for it (5 the arms, 7 the models, 8 the
  comparators, 10 the paired differences, 11 the primary pair, 12 the verdict rule, 15 what is written, 16 the first
  run; B10); the schemas' properties, `required`, the absence of strict keywords and the new field first in insertion
  order; `max_tokens` 512 and 256; g's declared cases ("How it is read"); G and M on every answer and their
  commutation; the composite rule and both ablations on every verdict pair; every view passing through `lab.j1.score`
  and `lab.j1.paired`; the line check's every outcome (keys, task, question, answers, each reading's verdict,
  `grounded` recomputed, `first_key` checked for shape, B19); the words in A5's order with the ablation clauses and
  the `incomplete` word for a missing ablation figure (B8); the sources and stop rules on synthetic artifacts (each
  cycle by artifact alone, rebuild alone, both agreeing, both disagreeing, the chain agreeing and disagreeing, each
  cycle by chain with the other cycle established, K alone stopping, neither verifying, the A001 rebuild `not
  attempted` when J001's set is not established, every consistency count, B5); the A001 artifact check's every
  outcome on the synthetic A001-shaped artifact (a changed byte, a manifest that disagrees, a labels record with
  another seed, n or kind, a questions file with other refs, an absent directory, an absent file, a malformed line)
  and its agreement with `lab.j2labels.earlier_refs` on the J001-shaped one, case for case (B3); the labels record's
  `exclusion.mechanisms` and `excluded_runs` entries (B22); the draw over the eligible list less 300 refs, disjoint
  from both synthetic earlier draws; `plan_outputs` on a j3, a j2 and another plan (B2); the request block's every
  error, the j3 file list included (B3); the template giving 5 units in 5 shards; the runner through the fake server
  (three passes in order, the pins refused before any call, a stop in each pass, the status reading, participation);
  the wiring (both fake handlers, the warm-up's three tasks with the probe task E, B4; the plan's outputs); the small
  and the template dry runs with every number traced and no ref anywhere. The pins of `tests/lab/test_lab_workflow.py`
  change with the workflow as B2 says (`github.token` in exactly three places; the steps with `github-token` are
  `earlier` and `earlier-2`; five `download-artifact` steps with a `name`, three of them `own`; the step order before
  `prereg`), and `tests/lab/test_lab_plan.py:677, 695` gain the two output keys; `tests/lab/helpers.py`
  (`PREREG_EXPERIMENTS`, `:67`), `tests/lab/test_lab_docs.py` (the note names at `:64-66, 89-90`; the request keys at
  `:289` gain `_J3_KEYS` and the list entry's keys) and `tests/lab/test_lab_sim.py:1278` (the experiments message)
  gain `j3` where they list the experiments. `tests/lab/test_lab_guards.py` is unchanged: `lab/j3labels.py` makes no
  network call, and `actions: read` is read-only.
- **The guards the build and this file obey.** No model-family name anywhere: `tests/mycelic/test_collective_guards.py`
  (`NoModelNamesTests`, which scans `docs/collective` and the collective code) and `tests/lab/test_lab_guards.py`
  (which scans `lab/`, `tests/lab/` and `docs/lab/`); the models are a-0p5b, a-1p5b and a-4b, and the deployment
  branch is called that. No record ref in any report, summary, console line, commit or document; the report guards
  refuse record ids. No ASCII digit in any `lab/notes.py` sentence. Lab Python imports only the standard library,
  `mycelic` and `lab`.
- **Before merge** (section 2.3): `python -m pytest tests/lab -q -p no:cacheprovider` in the background, the onboard
  and guard suites, and the template dry run `python -m lab.dryrun --request lab/templates/accuracy-002.json --out OUT
  --j3-data DIR`, which must show 5 units in 5 shards, every source verified on the stand-in, the draw line with
  eligible after exclusion at least 150 and overlap 0 (B21), every unit `ok` and `plumbing`, every word `incomplete`,
  the comparisons 21, and no ref of the draw or the excluded set anywhere.
  In a dry run the fake answers E and R as the lexical judge with an empty field, so Eg and Egm refute every
  question the fake would have confirmed, and nothing in a dry run measures the instructions, the check or the
  composite; `BUILD-A002.md` says so.

## Runs

### Run 1 (lab run 38439612256, 2026-10-13)

**The run.** Lab run [38439612256](https://github.com/anovruzov/NeuralGraph/actions/runs/38439612256) (workflow
`mycelic-lab`), created 17:33:27 UTC, completed `success` at 18:23:04 UTC. Request `lab/requests/accuracy-002.json`,
sha256 `77173e537c9f25550e322dcda2743ea9f61fba92d3928f45cda928b129447ee9`, pushed alone as commit `5a8f980` at
17:33:21 UTC. Before the run, in this order: the rule `b0f12bd`, amended `284b375` (B1 to B25) before any build; the
build `6be0b0b` (the lab kind `j3`), its reviews, the fix `70d269d` and the merge `6c8553d`; the code push `cbdd3c7`.
The standing section 7 default held (no chief-scientist call arrived before the request push), so P = (a-4b, E
`evidence`) and Eg is the headline secondary, as B1 says. Plan job 115442458382 (17:33:30 to 17:34:58, success),
plan sha256 `b949c7204f270ef37e184892285f618faef0b820d836f823c17ea655b5a1727a`: it downloaded J001's plan artifact
`lab-plan-37996675559-1` (artifact id 11647645214) and A001's `lab-plan-38114522160-1` (artifact id 11693535286),
both digests verified by the download step, and uploaded this run's `lab-plan-38439612256-1` (artifact id
11833749961, 19 files, 74,062 bytes); its plan summary block is 58 lines with sha256 `24c4593090e1…`. Provision jobs
115443156784 (the server, 17:35:02 to 17:35:17) and 115443156973 (the a-4b model file, 17:35:02 to 17:35:29): both
files from the cache, verified by the lock (server `2cda5ff93639`, a-4b `7485fe6f11af`), lock status `unchanged`,
0 new entries. Five shards, `s001-a-4b` to `s005-a-4b` (jobs 115443387591, 115443387490, 115443387481,
115443387596 and 115443387585), all started at 17:35:32, attempt 1, all `sealed` with exit code 0; they finished at
18:09:13, 18:21:19, 18:09:15, 18:21:50 and 18:06:53. Aggregate job 115464859031 (18:21:53 to 18:23:04, success); its
log prints `report/report.md` between the `MYCELIC-LAB` markers as 144 lines with sha256 `58325c34d6bf…`, matching
(recomputed from the log's lines with the timestamp prefix stripped and a trailing newline, as `lab/summary.py`
computes it), and `report/lock-candidate.json` as 42 lines with sha256 `a153a34a1a5d…`, matching on the same
recomputation; its INDEX line reads `report/report.json=13422 report/report.md=144 report/lock-candidate.json=42`.
The `report/report.json` block begins before the readable tail of the log, so its sha256 is not verified here, and
no figure below is taken from it. Every figure below is copied from the report block or from the plan job's log.

**The plan job's fixed line**, printed before any model ran, in the log's own words: `j3 draw: available 58319
excluded 300 eligible_after_exclusion 58019 overlap 0 sources J1=verified R1=verified A1=verified R2=verified
K=verified j001=artifact a001=artifact consistency j1_r1=0 a1_r2=0 outside_chain=0 chain_union=0 labels_sha256
0a64a62d94a7 questions_sha256 84486fa38568 excluded_sha256 65d51f0fca51 excluded_lines 450 complaint_file_sha256
216ecb36f9f5`. That is: `available` 58319; excluded 300; eligible after exclusion 58019; overlap with the excluded set
0; all five sources `verified`, J1, R1, A1 and R2 with 150 refs each and K with 300 (the plan summary's counts);
J001's set established by its artifact and A001's set by its artifact, with no by-chain reading used; the consistency
counts `j1_r1` 0, `a1_r2` 0, `outside_chain` 0 and `chain_union` 0, all computed (B5); labels sha256
`0a64a62d94a7…` (150 records, seed 3); questions sha256 `84486fa38568…` (300 questions in 5 parts); prereg sha256
`47bba18a0d35…` (the plan summary block); the chain file `prereg/j3/excluded.jsonl` sha256 `65d51f0fca51…` with 450
lines; complaint file sha256 `216ecb36f9f5…` beside A001's `a5a5de2d31f3…`. The two complaint-file prefixes differ,
which by trap 17 reports a file changed since A001's day; the exclusion works on refs, and here both artifacts and
both rebuilds verified, so the plan job's exclusion verified both earlier draws against the file as published on the
day. There was one plan job and no re-run, so B13's case of two plan jobs' lines does not arise. Cycles established, as the plan
summary printed them: `j001` by `artifact` (150 refs), `a001` by `artifact` (150 refs). Every task set finished 5 of
5 parts, and `records left out` is 0 in every arm.

**The comparators and both ablations on the fresh draw** (plan job, every record: the same 150 records and 300
questions every arm scored, since no record was left out):

| Comparator | Balanced accuracy [95% interval] |
|---|---|
| Lexical judge | 0.680 [0.637, 0.723] |
| Record-blind control (decides nothing) | 0.517 [0.460, 0.573] |
| Predicate-only bound (a fitted point value, decides nothing) | 0.553 |
| Ablation A, `lexical_or_no` (the composite arms' extra clause only) | 0.680 [0.637, 0.723] |
| Ablation B, `lexical_or_record_blind` (the composite arms' extra clause only) | 0.627 [0.573, 0.680] |

The lexical judge's unknown count on this draw is 0 (the plan job: `lexical_unknown 0`), so B8's case without a
lexical `unknown` held: ablation A is the lexical judge's verdicts line for line, its balanced accuracy and interval
are the lexical judge's, and `C_minus_ablation_A` equals C minus the lexical judge to the record (-0.110 [-0.160,
-0.060] both; for Cm -0.087 [-0.137, -0.040] both). The ablation-A clause repeated the pass test's lexical clause, and
the clause that added a test was ablation B's.

**The nine arms** on a-4b, on the same records (cluster bootstrap over records, B 10,000; the paired differences are
the arm minus the lexical judge, ablation A or ablation B, record by record; the lexical judge scored 0.680 on every
arm's records; the pass test is rule 12's, the verdict word rule 12's in the order `incomplete`, `pass_provisional`,
`partial`, `failure`; P in bold):

| Arm | Task, reading | Balanced accuracy [95% interval] | Sensitivity | Specificity | Unknown share | Minus the lexical judge [interval] | Minus ablation A [interval] | Minus ablation B [interval] | Bound on the same records | Parts finished | Records left out | Pass test | Verdict word |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N `negation` | N, as answered | 0.633 [0.590, 0.677] | 0.907 | 0.360 | 0.090 | -0.047 [-0.103, 0.010] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| D `negation_mapped` | N, M | 0.687 [0.643, 0.730] | 0.907 | 0.467 | 0.017 | 0.007 [-0.050, 0.063] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| **E `evidence` (P)** | **E, as answered** | **0.540 [0.503, 0.577]** | 0.900 | 0.180 | 0.130 | **-0.140 [-0.190, -0.090]** | | | 0.553 | 5 of 5 | 0 | **no** | **`failure`** |
| Eg `evidence_grounded` (headline secondary) | E, G | 0.553 [0.517, 0.590] | 0.887 | 0.220 | 0.130 | -0.127 [-0.177, -0.073] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| Egm `evidence_grounded_mapped` | E, G then M | 0.577 [0.543, 0.613] | 0.887 | 0.267 | 0.090 | -0.103 [-0.153, -0.053] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| R `rationale` | R, as answered | 0.590 [0.557, 0.623] | 0.980 | 0.200 | 0.143 | -0.090 [-0.140, -0.040] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| Rm `rationale_mapped` | R, M | 0.610 [0.573, 0.647] | 0.980 | 0.240 | 0.120 | -0.070 [-0.123, -0.017] | | | 0.553 | 5 of 5 | 0 | no | `failure` |
| C `composite` | E, the lexical judge's confirm, else Eg | 0.570 [0.530, 0.610] | 0.927 | 0.213 | 0.127 | -0.110 [-0.160, -0.060] | -0.110 [-0.160, -0.060] | -0.057 [-0.123, 0.007] | 0.553 | 5 of 5 | 0 | no | `failure` |
| Cm `composite_mapped` | E, the lexical judge's confirm, else Egm | 0.593 [0.557, 0.630] | 0.927 | 0.260 | 0.090 | -0.087 [-0.137, -0.040] | -0.087 [-0.137, -0.040] | -0.033 [-0.097, 0.030] | 0.553 | 5 of 5 | 0 | no | `failure` |

**The verdict on P = (a-4b, E `evidence`), by rule 12: failure.** P scored BA 0.540 [0.503, 0.577]; gap to 0.860 is
0.320; lexical 0.680 [0.637, 0.723]; bound 0.553. In the order of rule 12: task E's set is complete (5 of 5 parts,
class `model`, the report's "Model measurement" note, 0 records left out), so not `incomplete`; the point estimate is
below 0.860, so not `pass_provisional`; the lexical judge was not beaten (the paired difference -0.140 [-0.190, -0.090]
has its whole interval below zero), so not `partial`; `failure` holds. P's balanced accuracy is below the
predicate-only bound's point value 0.553, and so is its lower end 0.503; its unknown share is 0.130. **The primary
under the other reading of the section 7 default**, (a-4b, Eg `evidence_grounded`): `failure` (0.553 [0.517, 0.590],
paired -0.127 [-0.177, -0.073]). Success in the sense of the rule could not be reached in this cycle, and was not.
Section 2.5: continue with the next lever. The first run is the result (rule 16).

**The headline** (rule 11: E, P's arm, against the lexical judge's 0.680 on the same records): **worse** (0.540
[0.503, 0.577], upper end below 0.680). The headline secondary, Eg: **worse** (0.553 [0.517, 0.590], upper end below
0.680). With the evidence field, a-4b judges worse than word matching on this draw, read as answered and through the
grounding check alike.

**The lifts of rule 10**, paired by record over 150 records ("helps" when the lower end is above 0, "hurts" when the
upper end is below 0, else "not told apart"; they decide nothing toward 86):

| Lift | Lever | Difference [95% interval] | Reading |
|---|---|---|---|
| `E_minus_N` | the evidence field, with S_E and the 512-token cap (B18) | -0.093 [-0.137, -0.053] | hurts |
| `Eg_minus_E` | the grounding check | 0.013 [0.003, 0.027] | helps |
| `Eg_minus_N` | the evidence field and the check | -0.080 [-0.123, -0.037] | hurts |
| `Egm_minus_Eg` | the unclear-to-no mapping under the check | 0.023 [0.007, 0.040] | helps |
| `Egm_minus_D` | the evidence field and the check under the mapping | -0.110 [-0.153, -0.067] | hurts |
| `R_minus_N` | the rationale field, with S_R and the 512-token cap (B18) | -0.043 [-0.090, 0.003] | not told apart |
| `Rm_minus_D` | the rationale field under the mapping | -0.077 [-0.123, -0.030] | hurts |
| `D_minus_N` | the unclear-to-no mapping under N (A001's D − C, replicated) | 0.053 [0.030, 0.080] | helps |
| `C_minus_Eg` | the lexical disjunction over Eg | 0.017 [0.000, 0.033] | not told apart |
| `Cm_minus_Egm` | the lexical disjunction over Egm | 0.017 [0.000, 0.033] | not told apart |
| `C_minus_ablation_A` | the composite against `lexical_or_no` | -0.110 [-0.160, -0.060] | hurts |
| `C_minus_ablation_B` | the composite against `lexical_or_record_blind` | -0.057 [-0.123, 0.007] | not told apart |
| `Cm_minus_ablation_A` | the mapped composite against `lexical_or_no` | -0.087 [-0.137, -0.040] | hurts |
| `Cm_minus_ablation_B` | the mapped composite against `lexical_or_record_blind` | -0.033 [-0.097, 0.030] | not told apart |

Read with sensitivity and specificity, as "How it is read" asks: the evidence field (E − N) left sensitivity near
where it was (0.907 to 0.900) and lowered specificity from 0.360 to 0.180, with the unknown share from 0.090 to 0.130:
asking for a passage made a-4b refute fewer negatives, the opposite of what the lever was aimed at. The grounding check
(Eg − E) moved sensitivity from 0.900 to 0.887 and specificity from 0.180 to 0.220; the grounded share of yes answers
was higher on positives (0.985, 133 of 135) than on negatives (0.933, 84 of 90), the direction "How it is read" calls
the lever working, at the size of a lift of 0.013. The mapping under the check (Egm − Eg) moved specificity from 0.220
to 0.267 at the same sensitivity, 0.887, and the unknown share from 0.130 to 0.090; under N (D − N) it moved
specificity from 0.360 to 0.467 at the same sensitivity, 0.907, and the unknown share from 0.090 to 0.017 (A001's
D − C was 0.077 [0.047, 0.107]). The rationale field (R − N) raised sensitivity from 0.907 to 0.980 and lowered
specificity from 0.360 to 0.200, a move toward yes. The lexical disjunction (C − Eg, Cm − Egm) raised sensitivity from
0.887 to 0.927 and lowered specificity a little (0.220 to 0.213; 0.267 to 0.260); both lifts have a lower end of
exactly 0.000, which is not above 0 (B17). Where the lexical judge does not confirm, a composite takes the grounded
arm's verdict and ablation A answers no; both composites scored below ablation A, so on those questions the model's
verdicts did worse than a no, and neither composite is told apart from ablation B. Every arm other than E is a
secondary, a hypothesis for the next cycle's rule and never a result; none met its pass test; and no reading other
than the nine arms was made of the replies (rule 16).

**The grounding check's readings on task E, and the replies per task** (rule 13; they decide nothing):

| Replies (task E) | Grounded share of yes answers on positives | On negatives | Empty-citation share | Citation length, median | Citation length, 95th percentile |
|---|---|---|---|---|---|
| 300 | 0.985 (133 of 135) | 0.933 (84 of 90) | 0.170 | 104.5 | 585.5 |

The citation lengths are in characters after `norm` (rule 13).

| Task | First key is the new field (share) | Attempts | Calls | Output tokens, median | Output tokens, 95th percentile | Replies cut at the cap |
|---|---|---|---|---|---|---|
| E `judge_record_evidence` | 1.000 | 300 | 300 | 38.0 | 143.0 | 0 |
| R `judge_record_rationale` | 1.000 | 300 | 300 | 87.0 | 115.1 | 0 |
| N `judge_record_negation` | n/a (no new field) | 300 | 300 | 16.0 | 17.0 | 0 |

Task R wrote a median of 87.0 output tokens (95th percentile 115.1) and no reply of any task was cut at its cap.
Every reply of tasks E and R began with the new field, so the reply order trap 6 names was not seen.

**N and D beside A001 run 1's arms C and D** (`docs/collective/A001/CHOICE-A001.md`, Run 1, cited as written there).
A between-draw difference, reported, not tested:

| Arm | A001 run 1 (150 records, labels `623780b8a1de…`): balanced accuracy [interval], sensitivity, specificity, unknown share | A002 run 1 (150 fresh records, labels `0a64a62d94a7…`): the same | Difference of the two point values |
|---|---|---|---|
| N `negation` (A001's arm C) | 0.577 [0.530, 0.623], 0.833, 0.320, 0.137 | 0.633 [0.590, 0.677], 0.907, 0.360, 0.090 | 0.633 − 0.577 = 0.056 |
| D `negation_mapped` (A001's arm D) | 0.653 [0.610, 0.697], 0.833, 0.473, 0.017 | 0.687 [0.643, 0.730], 0.907, 0.467, 0.017 | 0.687 − 0.653 = 0.034 |

The two draws are independent, so the difference of two point values has a standard error of about 0.05 (the square
root of 2 times 0.035); differences within about 0.10 are sampling; a larger one says the draws differ, and both are
reported (B14). Both differences, 0.056 and 0.034, are within about 0.10. The comparators on the two draws, for the
reader: the lexical judge 0.680 [0.637, 0.723] here and 0.663 [0.627, 0.703] on A001's; the record-blind control
0.517 [0.460, 0.573] and 0.520 [0.463, 0.577]; the predicate-only bound 0.553 and 0.577.

**Parts and records.** Every task set finished 5 of 5 parts for each of the three tasks (E, R and N), and every arm
built on them shows 5 of 5; `records left out` is 0 in every arm. 900 model calls were made, 300 per task; the six
other arms are readings of the same replies (the composites joined with the plan job's lexical lines).

**Nothing stopped before a model ran.** The plan job's guard (`l1 guard: no l1 units`), the five sources, the stop
rules and the draw passed; both provision jobs took their files from the cache, verified by the lock; no shard failed
(each `sealed`, attempt 1, exit code 0); no warm-up cut skipped a unit (trap 20); and no unit stopped at its budget:
every task set finished 5 of 5 parts, task N last in every unit, the longest shard (`s004-a-4b`) in 2735.4 s of wall
time and the longest unit (`j3-a-4b-p4`) in 2665.8 s, against a 300-minute budget.

**Model-call latency, from the ledgers** (one shared GitHub-hosted runner per shard, a runner number and not site
hardware; compare only within a CPU model; the CPU model as the report quotes it; a unit's three tasks ran on the same
server, in the order E, R, N):

| Runner CPU | Shards | Calls per task | Median ms, evidence / rationale / negation | 95th percentile ms, evidence / rationale / negation |
|---|---|---|---|---|
| `AMD EPYC 7763 64-Core Processor` | `s004-a-4b` | 60 | 13646.9 / 18272.5 / 9297.1 | 28610.8 / 26965.2 / 16111.1 |
| `AMD EPYC 9V74 80-Core Processor` | `s002-a-4b`, `s003-a-4b`, `s005-a-4b` | 180 | 9466.4 / 14474.4 / 6645.3 | 25662.2 / 23430.1 / 13960.1 |
| `INTEL(R) XEON(R) PLATINUM 8573C` | `s001-a-4b` | 60 | 8381.1 / 16777.5 / 4909.1 | 17745.8 / 22974.3 / 7163.6 |

Over all five shards, per task (across CPU models, so not one comparison): evidence median 10095.5 ms and 95th
percentile 26211.6 ms; rationale 15836.2 and 24885.6 ms; negation 6502.3 and 14282.0 ms. Within each CPU model the
rationale task's median is the highest of the three and the negation task's the lowest. The shards ran on three CPU
models; the figures of different rows are not one comparison.

**The comparison count.** 9 (model, arm, cycle) comparisons this cycle (9 arms × 1 model × 1 cycle); 21 since the
loop began (12 before this cycle, A001 run 1's). The aggregate printed "comparisons since the loop began: 21 (9 this
cycle, 12 before)".

**Public text.** This result is on public text the models may have seen in training: real NHTSA complaint
narratives, read with their codes hidden and scored against the filed codes, which are not checked labels. A pass here
would have been a pass on such text; this failure is a failure on it.

**What the next cycle inherits.** The A003 draft on wf/a003-design (`efa79fe`, committed alone, in review) picks its
base task from this entry by its own pre-registered adaptation rule, whose text is that draft's and is not changed
here. A002's figures for it: task E's set is complete; P = (a-4b, E) is `failure`; `E_minus_N` is -0.093 [-0.137,
-0.053], hurts. So under that draft's rule the base task of A003 is A001's negation task N, with the unclear-to-no
mapping M as its declared reading. The best arm this cycle, D `negation_mapped` (that task read through that mapping),
scored 0.687 [0.643, 0.730], paired 0.007 [-0.050, 0.063] to the lexical judge: not told apart from word matching, a
hypothesis, never a result.

## Amended before any build, 2026-10-13

No A002 code exists, no request has been pushed, and no record of the cycle's draw exists. Three reviews of this rule
against the lab's code at `4e55949`, the loop prompt and the loop's status log found three blocking gaps (the primary
pair's reading of the standing default; the workflow's download wiring; the A001 artifact check) and thirty-three
smaller ones, several raised by two or three reviews. The changes are B1 to B25, numbered as this cycle's task asks
(A001's A1 to A12 live in `CHOICE-A001.md` and are cited with that file's name; the reviews' own labels for their
findings are not reused here, and each item names its findings by where they point). Each says what the finding was,
what is now, and what it changes, as A001's A1 to A12 do. Where the text above and this section disagree, this
section wins; the body above was edited to agree, and each edit carries its item's number in parentheses.

The declaration still holds. Since the rule was committed, its author has read the files each item cites:
`lab/j2labels.py`, `lab/j2.py`, `lab/plan.py`, `lab/request.py`, `lab/warmup.py`, `lab/shard.py`, `lab/units.py`,
`lab/summary.py`, `lab/aggregate.py`, `lab/prereg.py`, `lab/notes.py`, `lab/responder.py`, `lab/dryrun.py`,
`.github/workflows/mycelic-lab.yml`, `tests/lab/test_lab_workflow.py`, `tests/lab/test_lab_plan.py`,
`tests/lab/j2_data.py`, `mycelic/collective/jsonio.py`, `mycelic/collective/inference/jsonparse.py`,
`mycelic/collective/inference/tasks.py`, `mycelic/collective/inference/runtime.py`,
`docs/collective/replay/vehicles/pack/vocabulary.json`, `docs/handoff/LOOP-48H-STATUS.md`, CHOICE-A001's A1 to A12
and its Run 1, and CHOICE-J001's Runs entry. No figure here comes from A002's data, which does not exist; no reply
of J001's or A001's has been read, and no report of either run.

**No finding asked to change the pinned metric, and none is changed.** 86 stays balanced accuracy 0.860 against the
filed-category key at record level with the codes hidden, on 150 fresh records scored as J001; the comparators stay
the lexical judge, the predicate-only bound and the record-blind control, with the two ablations for the composite
arms; rule 12's block stays word for word LOOP-48H section 2.5; the interval rule and the confirmation rule are
untouched. No floor is lowered: B1 moves the primary pair to the conservative reading of the standing default; B5
enlarges the excluded set to every verified source's refs and keeps the per-cycle stop; B7 allows one named setting
change before a re-push only where section 6.3 already allows a lab-side cause fixed by amendment; the rest are
readings, citations, the labels record and the build plan.

### B1. The primary pair under the section 7 default, read as A001's A7 read it (rule 11; rule 5; the declaration; the settings; "Why these arms"; "How it is read"; "Decisions this does not make"; "The request"; Runs)

- **The finding** (blocking; also the finding on rule 11's bullet that asked to keep P = (a-4b, Eg) with E's word
  printed beside it): P = (a-4b, Eg) is a reading applied to the reply before the pinned `decide` (G: a yes read as
  no when the citation is not grounded), which the rule itself calls "the grounding mapping" and, under "What this
  cycle tests", "a verbatim-substring check read as a declared reply mapping". The standing default (LOOP-48H
  section 7: "Default if silent: shipped `decide` primary, declared mappings secondary") is in force: the status log
  lists decision 9 under Open asks and nothing under Decisions received. A001's A7 read that same default as barring
  a mapped arm from the primary. This rule reversed that reading in the one direction that gives its primary
  headroom (E alone is expected near N's 0.577 [0.530, 0.623]; Eg's ceiling is 0.780 to 0.863), on the argument
  that the default's wording predates lever 3, although sections 2.6 and 7 are one document of one date and lever 1's
  mappings were made secondary by the same default in A001; and it asked once and proceeded on the favourable
  reading if nobody answered, which inverts "default if silent". A reading of the standing default that changes
  between cycles in the direction of the primary is a floor lower than A001's, and a provisional pass on Eg could be
  contested afterwards as a pass on a mapped secondary with the draw spent.
- **Why the finding is right:** G and M are the same kind of thing, a function of the reply applied before the one
  pinned `decide`; the default names that kind, not decision 9's question alone; and the rule's own declaration
  says a call on the default is adopted by amendment, which presumes the default is the floor until a call arrives.
- **Now:**
  - Rule 11's sub-item reads **"The section 7 default, read as A001's A7 read it"**: a reading applied to the reply
    before the pinned `decide` is a declared mapping and secondary under the default, and G is such a reading, as M
    is. **P = (a-4b, E `evidence`)**; Eg is the headline secondary and the first hypothesis for the next cycle's
    primary. A chief-scientist call received before the request push that names lever 3's verbatim-substring check
    primary-eligible promotes P to (a-4b, Eg) by a dated amendment before any run, and the headline arm with it; a
    call that counts unclear as refute names (a-4b, Egm) the same way. The ask, one line, in the loop's next reply
    and under `## Open asks` of `LOOP-48H-STATUS.md`: "NEEDS CHIEF SCIENTIST: may lever 3's verbatim-substring check
    (G: a yes read as no when the citation is not a substring of the record's text, applied before `decide`) be
    A002's primary arm? Default if silent: no; P = (a-4b, evidence), Eg the headline secondary." No request is pushed
    on a reading this file gives itself.
  - Rule 5's table bolds E and unbolds Eg, and its primary-pair bullet names E. Rule 11's first bullet names E and
    says the aggregate computes the verdict word for every arm, so the Runs entry prints Eg's word beside P's,
    labelled "the primary under the other reading of the section 7 default", and a ruling received after the run
    is answered from that line and changes nothing in this cycle. The headline arm is E, P's arm; Eg's line is
    reported beside it in the same words, labelled the headline secondary. "How P meets" reads E as the first
    lever-3 arm the default allows as primary and G as the declared mapping on E's replies. The settings rows
    "Primary pair" and "Headline arm" say so. "Why these arms" moves the primary paragraph to arm E, whose
    expectation is failure, since E is expected near N's 0.577 [0.530, 0.623] unless asking for a citation itself
    changes the answers, which E − N reads, and keeps Eg's ceiling arithmetic as the headline secondary's. "How it
    is read" states P's expectation as failure and the 0.780 to 0.863 ceiling as Eg's. The declaration's decision 9
    bullet points at rule 11. "The request" carries `primary: {model: a-4b, arm: evidence}`. "Decisions this does
    not make" says the promotion of Eg is the chief scientist's call. The Runs placeholder carries the verdict on
    P = (a-4b, E), Eg's word labelled, and the headline for E with Eg's line beside it.
- **What it changes:** the primary pair, from the arm with headroom to the arm the standing default allows; the
  honest expectation, from "failure or partial" to failure on P; nothing in what is measured (the nine arms, the
  three tasks, the pass order E, R, N, the comparators, the ablations and the count of 9 comparisons are unchanged)
  and nothing in the pass test. The finding that asked to keep P = (a-4b, Eg) with E's word printed beside it is
  adopted for its printing, inverted, and declined for its primary (see "Declined").

### B2. J001's artifact is downloaded on a j3 plan (The data and the draw, items 1 and 3; the build plan; the workflow test pins)

- **The finding** (blocking): the workflow's only download step is keyed on `steps.plan.outputs.j2_exclude_run_id`
  and `j2_exclude_artifact` (`.github/workflows/mycelic-lab.yml:57-65`), which `lab.plan.plan_outputs` fills from
  j2 units only (`lab/plan.py:435-461`; `OUTPUT_KEYS`, `:127-128`). The build plan named new outputs
  `j3_exclude_run_id_1`/`_artifact_1` and `_2`, added one more step, and pinned five download steps with a name
  (four today, `tests/lab/test_lab_workflow.py:257-261`). With those names the existing step is skipped on every
  j3 plan: J1 is `artifact absent` on every run, J001's set rests on the rebuild R1 alone, and item 1's sentence
  that every earlier cycle is excluded by both mechanisms is false on the first run for a wiring reason, A001's A1
  lesson repeated; the day the complaint file changes, J001's set is unestablished and the plan job stops although
  the artifact was reachable.
- **Now:** `lab.plan.plan_outputs` fills the existing outputs `j2_exclude_run_id` and `j2_exclude_artifact` from the
  first `exclude` entry of a j2 plan or of a j3 plan (for A002, J001's run 37996675559 and
  `lab-plan-37996675559-1`; the `j2_` prefix is kept because the workflow's `earlier` step and its pins name it), and
  adds `j3_exclude_run_id_2` and `j3_exclude_artifact_2` from a j3 plan's second entry (A001's run 38114522160 and
  `lab-plan-38114522160-1`), empty for every other plan; `OUTPUT_KEYS` gains both in sorted place;
  `lab.plan.exclude_entries(plan)` returns the entry list of a j2 or a j3 plan (one entry for j2, two for j3, None
  otherwise) and `lab.plan.j2_exclude` stays as it is, since `tests/lab/test_lab_j2.py:888-891, 1678` call it. The
  existing `earlier` step therefore downloads J001's artifact unchanged, and one new step `earlier-2` of the same
  shape (`actions/download-artifact` at the same pinned sha, `if: steps.plan.outputs.j3_exclude_run_id_2 != ''`,
  `continue-on-error: true`, `name: ${{ steps.plan.outputs.j3_exclude_artifact_2 }}`, `run-id: ${{
  steps.plan.outputs.j3_exclude_run_id_2 }}`, `github-token: ${{ github.token }}`, `path: ${{ runner.temp
  }}/lab-earlier/${{ steps.plan.outputs.j3_exclude_run_id_2 }}`) follows it, before `prereg`. The pins of
  `tests/lab/test_lab_workflow.py` change to: `github.token` in exactly three places (`:182`); the steps with
  `github-token` are `earlier` and `earlier-2` in the plan job (`:189-190`); five `download-artifact` steps with a
  `name`, three of them `own` (`:259-261`); `plan_steps[at - 1]` is `earlier-2`, `[at - 2]` `earlier` and
  `[at - 3]` `plan` (`:355-357`). `tests/lab/test_lab_plan.py:677, 695` gain the two keys in their github-output
  lines. `tests/lab/test_lab_j3.py` pins `plan_outputs` on a j3 plan (both pairs filled), on a j2 plan (the first
  pair only, byte for byte what A001's plan job got) and on any other plan (all four empty). Items 1 and 3 name the
  step and the output each download is keyed on.
- **What it changes:** the build plan's `lab/plan.py` and workflow bullets and the test pins (B25). A j2 plan's
  outputs are what A001's were, so A001's recorded plan is unchanged.

### B3. The A001 artifact check, as new code pinned to the old (The data and the draw, preamble and item 3; "The request"; the build plan)

- **The finding** (blocking; also two findings on item 3's "The check is A1's, on a j2-shaped artifact" and on the
  request's "files from a fixed list"): `lab.j2labels.earlier_refs` accepts only `prereg/j1/labels.jsonl`, checked
  against `prereg/j1/labels.json` with `{source nhtsa, n 150, seed 1}` and `prereg/j1/questions.jsonl`, or
  `prereg/j2/excluded.jsonl`; any other `file` returns `malformed` (`lab/j2labels.py:53-58, 152-182`), and
  `lab.request._exclude` refuses a `file` outside `EXCLUDE_FILES` with `J2_FILE_PROBLEM` (`lab/request.py:592-594`).
  Item 3 described a check of `prereg/j2/labels.jsonl` with seed 2 and `prereg/j2/questions.jsonl` while the preamble
  said every artifact check is A001's code called unchanged, which it cannot be; the request as written would be
  refused at the plan step before any download; and a builder calling `earlier_refs` for A1 would get `malformed`
  on every run, which the stop rules would read as "A001's set established by R2 alone".
- **The choice.** The blocking finding offered two readings: A1 derived from the chain file K (every artifact check
  pinned code, `prereg/j2/labels.jsonl` not read, its prefix checked by the rebuild alone), or a new check of A001's
  labels file generalised from A001's. This rule takes the second, because it is what item 3 declared; because it
  makes A001's set independent of J001's (A1 needs no J001 set, where K less J001's set does), which is what lets
  the chain establish J001's set after J001's artifact expires (B5); and because the new function is pinned to the
  old by a test on the j1 layout. The first reading's one advantage, no new artifact-reading code, is kept where it
  can be: J1 and K are read by `earlier_refs` unchanged, and every read of the new function is inside
  `try`/`except` with the same fixed outcomes.
- **Now:** item 3 reads: the check is A1's, implemented in `lab/j3labels.py` as `earlier_labels_refs(directory,
  entry)`, a sibling of `lab.j2labels._check_earlier` generalised to the j2 layout (`prereg/j2/labels.jsonl`;
  `prereg/j2/labels.json` of kind `lab_j2_labels` saying `source` `nhtsa`, `n` 150, `seed` 2 with `n` equal to the
  refs found; refs equal to `prereg/j2/questions.jsonl`'s, prefix `2ba18ea89bb7`; the manifest cross-check of all
  three files against `prereg/prereg.json`; outcomes `lab.j2labels.OUTCOMES`; nothing from the artifact printed,
  written or uploaded but the refs on the runner), and K by `lab.j2labels.earlier_refs` unchanged; the preamble
  says which check is which. `lab.j3labels.EXCLUDE_FILES` is `(*lab.j2labels.EXCLUDE_FILES,
  "prereg/j2/labels.jsonl")`, and the j3 block's entry validator in `lab.request` carries that list, with the chain
  keys (file `prereg/j2/excluded.jsonl`, prefix `9a948cbf2cd9`) and the rebuild keys (seed 2, prefix `623780b8a1de`)
  on the second entry, since `lab.request._exclude`'s j2 list refuses the second file; "The request" says so. The
  build's tests: the j2-shaped check's every outcome on the synthetic A001-shaped artifact (a changed byte, a
  manifest that disagrees, a labels record with another seed, n or kind, a questions file with other refs, an absent
  directory, an absent file, a malformed line), and the same function on the J001-shaped synthetic artifact, the j1
  layout, beside `lab.j2labels.earlier_refs`, with the same outcome on every case of A001's A1 table, which pins
  the generalisation to the pinned code.
- **What it changes:** one new function and one new file list; the request's second entry keeps `file`
  `prereg/j2/labels.jsonl` and prefix `623780b8a1de`; nothing in how J001's artifact or the chain file is read.

### B4. The warm-up's probe is task E's (rule 15; trap 8; the build plan)

- **The finding** (three reviews): rule 15 said "the probe is task N's, the first task name in sorted order among E,
  N, R", which contradicts itself. `lab.warmup.warm_tasks` returns its tasks `sorted(found.items())` by (task name,
  pack) (`lab/warmup.py:242-244`; the docstring `:5-7`) and the probe is `fitting[0]`'s request body, sent twice
  (`:391-394`; the docstring `:46-47`). `judge_record_evidence` sorts before `judge_record_negation` and
  `judge_record_rationale`, so the probe, and `repeat_identical`, are task E's; and trap 8's inference reverses: the
  E pass, run first, follows E's own probe on the same server, so it starts on a warm prefix, the opposite of A001's
  A9 situation. The citation `lab/warmup.py:9-10, 44-45` was A001's A11's, to an earlier docstring.
- **Now:** rule 15 says the probe is the first fitting task's in sorted (task name, pack) order, with the four line
  references above, so for a j3 unit task E's, and that `repeat_identical` is recorded for task E, P's task, and for
  no other task. Trap 8 says the warm-up's last request is task E's probe, so the E pass starts on a warm prefix and
  the R and N passes on cold ones, and the first call of each of those two passes is slower. The build plan's
  `lab/warmup.py` item says the probe is the first task name in sorted order, task E's, and the build's test pins
  the probe task as E.
- **What it changes:** a citation and a reading of timings, which decide nothing.

### B5. The stop rules with the chain, and the excluded set as the union of every verified source (The data and the draw, items 5 and 8; trap 16; the settings; the build plan)

- **The finding** (two reviews): item 5 established J001's set only by J1 or R1 and A001's only by A1 or R2, so K
  (A001's chain, which holds both cycles' 150 refs by A001's verified artifact mechanism) could only confirm an
  already established union. After J001's artifact expires (2026-11-08) on a day the complaint file has changed,
  J1 is absent, R1 mismatches, R2 is `not attempted`, and the plan job stops although A1 and K verified, which
  contradicts A001's A3 ("the chain survives J001's expiry") and trap 16's own sentence. One review proposed that
  the excluded set be the union of every verified source's refs and that the plan job stop only when no source of a
  cycle verified and K did not verify; the other that K less the other cycle's established set establish a cycle,
  K alone establishing neither.
- **Now:** item 5 reads, for the sources: J001's set is established when J1 or R1 verified; else, when K verified
  and A001's set is established by A1, as K less A1, printed "J001 by chain". A001's set is established when A1 or
  R2 verified; else, when K verified and J001's set is established by J1 or R1, as K less J001's set, printed
  "A001 by chain". K alone, with neither cycle otherwise established, establishes neither and the plan job stops.
  R2 is attempted over the eligible list less J001's set whenever that set is established, by J1, by R1 or by the
  chain; else `not attempted`. The consistency checks, each printed as a count and stopping above 0: J1 and R1
  equal when both verified; A1 and R2 equal when both verified; every other verified source's refs a subset of K
  when K verified; K equal to the union of J001's set and A001's set when both are established without the chain.
  The excluded set is the union of every verified source's refs (J1, R1, A1, R2, K), never smaller than the union
  of the two established sets, 300 refs expected and the count printed. The plan job stops before any model runs,
  and the loop asks, when either cycle's set is not established or when any consistency count is above 0. Item 8
  and the Runs entry print the five sources' outcomes with any by-chain reading named and the consistency counts
  computed. Trap 16 says what each cycle's freshness rests on after each expiry. Trap 17 is unchanged. The settings
  row "Excluded" carries the chain reading. The build's tests cover each cycle by chain with the other cycle
  established, K alone stopping, and every consistency count.
- **Declined in part:** the proposal that K alone suffice, and the check "K equal to the union of the other verified
  sources when K and at least one of them verified" (see "Declined").
- **What it changes:** the excluded set can only grow (the union is a superset of what the rule had); the per-cycle
  requirement of the stop rule is kept; one cycle's artifact expiring no longer stops the loop on a changed file
  while the other's lives, which is what A3 promised.

### B6. The founder ask A001's A2 promised was never posted (the declaration)

- **The finding:** A001's A2 declared a one-line founder ask on section 2.3's stop rule, to be posted under `## Open
  asks` of `docs/handoff/LOOP-48H-STATUS.md` when that file was created. The status log's Open asks hold five lines,
  none about the stop rule, and its Decisions received hold nothing on it. This rule kept A2's reading as the gate
  for two cycles without saying that the ask was never posted.
- **Now:** the declaration gains a bullet after the decision 9 bullet saying so, resting this rule on A2's reading
  for both earlier cycles and posting the ask with its own: "NEEDS FOUNDER: section 2.3's stop rule is read as stop
  when no mechanism verifies a cycle, not stop unless the rebuilt sha matches; a verified artifact's refs are the
  records that cycle judged (CHOICE-A001 A2; CHOICE-A002, The data and the draw, item 5)." A strict ruling received
  before the run is adopted by a dated amendment. Both asks, this one and B1's, go in the loop's next reply and
  under `## Open asks` with the date; this file cannot write the status log, since it is committed alone.
- **What it changes:** a declaration; no setting.

### B7. A warm-up reply cut at the cap stops the shard before any model judges (trap 20; rule 16)

- **The finding** (two reviews): the warm-up calls each task once with `Runtime.single`, no repair, and a reply
  with `finish_reason` `length` skips every unit that depends on the task (`lab/warmup.py:382-383`,
  `WARMUP_LENGTH`; the docstring's `calls` step); a j3 unit depends on all three tasks its adapter routes, so one
  cut reply to task E's or R's warm-up call on the generated record skips all five units: nothing judges, the run
  stopped before any model ran. At temperature 0 with a fixed seed a re-run "unchanged" repeats the cut, and raising
  the cut task's `max_tokens` is "a new choice file" by rule 16. Trap 20 named only a refused schema.
- **Now:** trap 20 names the cut as a second way to stop before any model judges, an infrastructure failure under
  rule 16 and section 6.3, recorded as "stopped before any model ran" with the task named; because the server is
  deterministic, the re-run is not unchanged: a numbered amendment raises that task's `max_tokens` (a cause
  independent of any printed figure, the warm-up's generated record being no drawn record), reviewed before the
  re-push, and the budget arithmetic is redone. Rule 16 names this as the one setting change the rule allows before
  a re-push, by amendment, as section 6.3 allows a lab-side cause fixed by amendment.
- **What it changes:** a named exception to rule 16's "any change to a setting is a new choice file", confined to
  a run in which no model judged any drawn record; no floor on the measured run.

### B8. The composite word when an ablation figure is missing; ablation A is the lexical judge on this pack (rule 12; rule 5; "How it is read"; Runs)

- **The finding** (two reviews): (1) the word for C and Cm when an ablation figure is not a number (an ablation file
  missing, no common record) was not declared, while `lab.j2.verdict_word` returns `incomplete` for a missing bound
  or lexical interval; (2) on this pack the lexical judge never answers unknown (unknown share 0.000, RESULTS runs 9
  and 11; a j1 vehicle question gives `mentions_entity` yes from the structured entity), so ablation A is the
  lexical judge's verdicts line for line, `C_minus_ablation_A` is `C_minus_lexical` to the record with the same
  bootstrap draws, and the ablation-A clause coincides with the "beats lexical" clause; the clause that adds a test
  is ablation B's, and the rule read "against both ablations first" as if they were two tests.
- **Now:** rule 12 says a composite whose ablation difference is not a number on either ablation is `incomplete`,
  as a missing bound or lexical figure makes any arm. Rule 5's ablation A says it is the lexical judge's verdicts
  exactly on such a draw, so its clause and the "beats lexical" clause are one test; it is declared because the lever
  names it and because a lexical `unknown` (a record whose language the pack does not support) would separate them;
  the Runs entry says which case held, the lexical judge's unknown count on this draw; and on a draw without a
  lexical `unknown` the clause that adds a test is ablation B's. "How it is read" says a composite not told apart
  from ablation A (here, from the lexical judge) is the lexical judge with the model contributing nothing, and one
  above it but not above ablation B adds no more than a reader of the question's predicate alone adds.
- **What it changes:** a declared word for one more cell, and an honest count of the composite's extra clauses;
  the ablation requirement stands as written.

### B9. Counts derived from rates are marked as such ("What the evidence says"; "Why these arms")

- **The finding:** the counts 48 and 125 (a-4b's refuted negatives and confirmed positives under arm C), 102, 41
  (its unknown verdicts), 16 and 25 (the split's bounds), and 50 and 1 (the lexical judge's confirms) are arithmetic
  from rounded rates (0.320 × 150 = 48; 0.833 × 150 = 124.95; 0.137 × 300 = 41.1; 0.333 × 150 = 49.95; 0.993 gives
  149 of 150), not figures the records print; the declaration says the run's report, which holds the exact counts,
  was not read. Stated as counts beside "cited as written, never re-scored", they read as recorded figures.
- **Now:** each is written with its rate and "about", and "What the evidence says" ends with: counts not in a Runs
  entry are arithmetic from the recorded rates and are marked "about".
- **What it changes:** wording; no figure.

### B10. Eight cross-references carried A001's rule numbers (the declaration; the metric; item 8; rule 5)

- **The finding:** 'declared in rule 9' (the reading is in rule 11); 'ablation comparators of lever 4 (rule 6)'
  (they are declared in rule 5); 'the comparators' figures (rule 6)' twice (the comparators are rule 8); 'all on the
  one model (rule 5)' (the models are rule 7); 'the lifts of rule 8' (the lifts are rule 10); 'the pass test of a
  composite arm (rule 10)' and "including rule 10's pass test" (the pass test is rule 12), while "Decisions this
  does not make" already said rule 11, so the file contradicted itself.
- **Now:** every one is corrected; the build's test pins that every `rule N` of this file, N in 1 to 16, names the
  item whose bold title the test lists for it.
- **What it changes:** references; nothing measured.

### B11. Stale or loose code citations (the metric; rule 3; the build plan)

- **The finding** (three reviews): `NHTSA_HOSTED` is `lab/request.py:262`, not `:234` (copied from LOOP-48H; `:234`
  is `_G0_KEYS`); the warm-up's sorted-by-name sentence is `lab/warmup.py:5-7` and the probe `:46-47`, not `:9-10,
  44-45`; `lab/summary.py:177-178` holds its own `J2_ARMS` and `J2_LIFTS` tuples rather than reading `lab/j2.py`'s
  constants, so the build-plan argument stands differently (extending `lab/j2.py` would desynchronise the summary's
  copies); and "the runtime parses the reply with `json.loads`, `jsonparse.py`" is `jsonparse.extract_object`
  handing the candidate to `jsonio.strict_load` (`jsonparse.py:124`), `json.loads` under an `object_pairs_hook`
  that keeps document order and refuses duplicates (`jsonio.py:94-123`). Every other lab line reference in the rule
  holds at this commit.
- **Now:** the four are corrected where they stand (the warm-up citation in B4).
- **What it changes:** citations.

### B12. What g compares, and the data block's escape (rule 4; trap 4; trap 5)

- **The finding** (two reviews): rule 4 said `text` is "exactly the string the model received inside the data
  block", but the model received `canonical_dumps(payload)` with `<` escaped, newlines and quotes JSON-escaped and
  non-ASCII literal (`ensure_ascii=False`, `jsonio.py:143`; `tasks.py`, `data_block`), and g compares the decoded
  narrative with the decoded reply string. Trap 4 listed "quotes across a line break the data block encoded" as a
  cost, but a copied `\n` decodes to a newline that `norm`'s whitespace collapse absorbs, so that case is grounded;
  the residual costs are a copy that drops the break without a space or changes a character, and a model that
  returns an escape sequence as literal characters. Trap 5's "every `<` written as `<`" said nothing: the escape
  `\u003c` (`tasks.py:70-75`, `_escape_lt`) had been rendered as a plain `<` in the committed file.
- **Now:** rule 4 defines `text` as the string whose canonical JSON encoding the model received inside the data
  block, compared after the reply's own JSON decoding, so a faithful copy decodes to the same characters; trap 4
  names the real costs and says a faithful copy across a line break is grounded; trap 5 writes the escape as the six
  characters `\u003c`.
- **What it changes:** the definition of what g compares is now what the build will implement; g itself (`norm`,
  the 4-character floor, the substring test) is unchanged.

### B13. A re-run after the plan job printed draws from its own day (rule 16; item 8; Runs)

- **The finding:** rule 16 allows a run that failed before any model judged to run again unchanged, with the same
  seeds and n (section 6.3). The draw is `random.Random("nhtsa:3").sample` over the eligible list of the complaint
  file as published on the day; if the file changed between the two plan jobs, the same seed gives other records,
  with comparator figures the first plan job had already printed for another draw. No pass path opens (the draw
  stays fresh by refs and P was fixed before either), but the rule did not say which draw is the result or how both
  are recorded.
- **Now:** rule 16 says the re-run's plan job draws from the file of its own day; the Runs entry records both plan
  jobs' fixed lines and both comparator prints, and the second draw is the result; a complaint-file prefix that
  differs between the two is reported; the primary pair, the seeds and n do not change. Item 8 and the Runs
  placeholder carry the first plan job's line beside the second's.
- **What it changes:** what is recorded; no setting.

### B14. What 150 records resolve ("How it is read"; "Why these arms")

- **The finding:** three statistical statements were inconsistent with the pinned standard error and with A001's
  recorded intervals. (a) "intervals about 0.07 wide": with the pinned standard error of about 0.035 a 95% interval
  is about 0.07 each side, up to 0.14 wide; A001 run 1 recorded a-4b's arm intervals 0.087 to 0.093 wide (the
  review's "0.080 to 0.093" is corrected: the widths are 0.087, 0.090, 0.093 and 0.087) and the paired intervals to
  the lexical judge 0.093 to 0.107 wide; and 0.07 was used two bullets earlier as "two standard errors", a
  half-width. (b) The between-draw reading compared two independent draws, whose difference has a standard error
  of about 0.05, so two standard errors of the difference are about 0.10 and A001's 0.080 move of arm A is inside
  it. (c) The rule nowhere said what paired difference to the lexical judge 150 records can resolve: A001's paired
  half-widths are 0.047 to 0.054, so a paired lower end above 0 needs a difference of roughly 0.05 or more, an arm
  at about 0.72 or more against a lexical judge near 0.66; an arm between the two is `failure` by power, not by
  evidence of no effect.
- **Now:** "150 records resolve little" replaces the 0.07 bullet with those figures; the between-draw bullet reads
  differences within about 0.10 as sampling; the honest expectation and Eg's paragraph say only the upper half of
  Eg's range is far enough above the lexical judge for `partial` to be readable; arm N's between-draw sentence is
  corrected.
- **What it changes:** readings; no test, no interval rule.

### B15. No multiplicity correction, declared (rule 11)

- **The finding:** A001's rule 9 said "Three comparisons, no correction, as J001"; this rule printed the count (9
  this cycle, 21 since the loop began) but never said whether a correction is applied to the nine pass tests or
  declined one with a reason; the reason exists in trap 12 but the rule's text should declare it.
- **Now:** the comparison-count bullet says no multiplicity correction is applied: the 86 verdict is read on P alone
  and the one headline is P's arm, so there is one decision per cycle; the eight other verdict words are
  exploratory and uncorrected, which is why a secondary that passes is a hypothesis and never a result, and the
  running count is printed so that a reader can discount a sampling peak across cycles.
- **What it changes:** a declaration; the count and the pass test are unchanged.

### B16. Unknown verdicts, not unclear answers; the 145/145 citation ("Why these arms")

- **The finding:** (a) `unknown share` counts unknown verdicts, not unclear answers: `decide` (`verify.py:243-259`)
  returns `unknown` for an unclear answer in either field, for `mentions_entity` no, and for a failed call. The
  ceiling arithmetic (u in about 16 to 41, balanced accuracy 0.780 to 0.863) is right for unknown verdicts on
  negatives, which stay unknown under G, but the sentence attributed all 41 to unclear answers, a split the
  declaration says was not read. (b) The 145/145 counts are printed in CHOICE-J001's Runs entry, not in RESULTS
  run 9, which the sentence cited.
- **Now:** Eg's paragraph says about 41 of the 300 verdicts were `unknown`, what an unknown verdict is, that no call
  failed in A001, and that the split by kind and by cause was not read; u is the number of unknown verdicts on
  negatives, every one of which stays unknown under G; under M those whose `mentions_entity` is yes become `refute`,
  which is why Egm's ceiling is higher (the review's "stays unknown under G and under M" is declined for M, see
  "Declined"). "Why a-4b alone" cites CHOICE-J001's Runs entry for the 145/145 counts and RESULTS run 9 for the
  scores.
- **What it changes:** wording and a citation; the arithmetic ceiling is unchanged.

### B17. Ties, and the headline against the paired clause (rule 12)

- **The finding:** ties were not read anywhere: the pass test's second and third clauses and the lifts' readings
  are strict, so equality fails them, as A001's P showed (balanced accuracy equal to the bound 0.577, lower end
  0.530 below it); and the headline (the arm's interval against the lexical judge's point value, J001's rule 7) and
  the pass test's second clause (the paired difference's interval) can disagree, because the paired difference
  removes the record variance both judges share.
- **Now:** rule 12's paragraph says equality fails every strict clause (`lab.j2.pass_test` compares with `>`), that
  the headline and the paired clause can disagree on one draw, and that where they do the verdict word follows the
  paired clause and the headline is reported as the per-model line it is.
- **What it changes:** a reading of the verbatim block; no clause.

### B18. The lifts carry the schema and the cap with the field (rule 10; "How it is read")

- **The finding:** tasks E and R differ from N in three things at once: the inserted sentences, the schema (one more
  required string property, declared first) and the token cap (512 against 256). `E_minus_N` and `R_minus_N`
  therefore read the field with its schema and cap, not the citation or rationale text alone; a lift could come from
  the schema change rather than from grounding or reasoning. The rule's lever reading named only the field.
- **Now:** rule 10 names the three together for each lift and says they are not separable in this cycle; "How it is
  read" says a cycle that wants the field's text alone needs a control task with an unused string field, which this
  rule does not run.
- **What it changes:** how two lifts are read; nothing run.

### B19. The reply's first key is recorded, never recomputed (rule 3; rule 15; the build plan)

- **The finding:** `Runtime.run` returns the parsed object in document order (`extract_object` over `strict_load`,
  `runtime.py:340-350`), so `first_key` is meaningful at write time; but a verdict line serialised with the lab's
  canonical JSON (sorted keys) loses the reply's order, so the aggregate's line check can only test `first_key`'s
  presence and value set, not recompute it from the stored reply, unlike the four verdicts and `grounded`. The new
  field also sorts first alphabetically, so a recomputation from stored lines would be vacuous.
- **Now:** rule 3 and rule 15 say `first_key` is read from the parsed reply's document order at write time and
  checked by the aggregate for shape only, one of the task's schema property names; the build plan's line-check
  test says "`first_key` checked for shape".
- **What it changes:** what the line check can verify; the share is reported as before and decides nothing.

### B20. The unit's budget and the tail (rule 14; "Why the units are this size")

- **The finding:** "300 minutes" is the request's `minutes`, not the budget the unit gets. The shard's deadline is
  the job start plus (325 − 10) minutes (`lab/shard.py:138-153`, `JOB_TAIL_MINUTES`), the unit's time is `min(300 ×
  60, deadline − now − 120 s)` (`:486`, `DEADLINE_MARGIN_S`) and its `--budget-seconds` is that less 30 s
  (`SIM_BUDGET_MARGIN_S`, `lab/units.py:189`, in `build_argv`; the review's `:301-302` is `required_tasks`), so
  provisioning, the server start and the warm-up come off the 300 when they take more than about 13 minutes. The
  worst case "at most 195 minutes" used medians and let only task R run to the cap; at A001's a-4b 95th percentile
  (16.4 s) with E and R both cut at 512 tokens on every call the unit passes 300 minutes, and the budget stop then
  falls in R or N while E stays complete, which rule 14 already covers.
- **Now:** rule 14's second bullet states the budget as the smaller of 300 minutes and the shard's remaining time
  less the margins, with the lines; "The tail" says what the 195 minutes assume and what the 95th percentile with
  both caps hit would need.
- **What it changes:** arithmetic the reader can check; the budget itself is unchanged.

### B21. The synthetic archive is large enough for three draws (the build plan)

- **The finding:** the template dry run needs three disjoint draws of 150 from the synthetic archive (the J001
  stand-in, the A001 stand-in built from it, and the j3 draw), 450 eligible records; `tests/lab/j2_data.py` builds
  about 360 at its default `per_make` 60 (`tests/lab/j2_data.py:7-9`), so `lab.j2labels.draw` would refuse the j3
  draw (`lab/j2labels.py:113-116`) and the pre-merge gate could not pass.
- **Now:** `tests/lab/j3_data.py` builds at least 90 complaints per make (six makes, about 540 eligible records) and
  `python -m tests.lab.j3_data --out DIR` defaults to it; the pre-merge dry run must show the draw line with
  eligible after exclusion at least 150 and overlap 0.
- **What it changes:** test data; nothing about the real draw.

### B22. Where five outcomes go in the labels record (The data and the draw, item 6; the build plan)

- **The finding:** `lab.j2labels.build_labels` writes `exclusion.mechanisms` with exactly two keys, `artifact` and
  `rebuild` (`lab/j2labels.py:62, 247-248`), and the record kind `lab_j2_labels`; five outcomes do not fit two keys,
  and the rule did not say where they go or whether `lab.j3labels` writes its own record.
- **Now:** item 6 says `exclusion.mechanisms` holds J001's pair (`artifact` J1, `rebuild` R1, as A001's record did),
  and `exclusion.excluded_runs` holds one entry per cycle in seed order with that cycle's `artifact` and `rebuild`
  outcomes and, for A001's, `chain` K and `derived` the by-chain reading when one was used, the counts found and the
  requested keys; the record's kind stays `lab_j2_labels`, written by `lab.j2labels.build_labels` unchanged and
  extended by `lab.j3labels` as `lab.prereg._j2` extends A001's (`lab/prereg.py:441-443`); the build's tests pin
  both.
- **What it changes:** the record's shape, so that the build need not invent one; `build_labels` is called
  unchanged.

### B23. The aggregate reads the drawn narratives to recompute `grounded` (rule 15)

- **The finding:** A001's line check reads no narrative on purpose: `lab.j1.line_problem` recomputes verdicts over
  `lab.j1.stand_in` (`lab/j1.py:327-332, 381`), and the aggregate reads the plan artifact's `lexical.jsonl`,
  `prior.jsonl` and questions only (`lab/aggregate.py:1092-1097`). Recomputing `grounded` makes the aggregate read
  every drawn narrative, which is feasible and the data are public, but it is a departure from A001's check that the
  rule should name, with what may leave the aggregate.
- **Now:** rule 15 names the departure: the recomputation reads the drawn narratives from the plan artifact's
  `prereg/j3/labels.jsonl` through `lab.j1.payload` with the pack loaded from the repository, inside the aggregate
  job only; nothing of a narrative or a citation leaves it; the report, the step summary and the console hold
  counts, scores, lengths and hashes, and the line check's refusal names a key, never a value.
- **What it changes:** a named reading of public text inside one job; no guard is weakened.

### B24. The eight predicates whose lexicon is not the label ("Why these arms")

- **The finding:** the counts 19 and 8 hold against `docs/collective/replay/vehicles/pack/vocabulary.json`, but
  "adds or splits a word" misdescribed the eight: three drop words (`engine cooling`, `hydraulic`, `unknown or
  other`) and five split the label into two or three phrases; none adds a word. Checked against the file: the
  three with one phrase shorter than the label and the five with two or three phrases are as the review says.
- **Now:** the sentence reads "for 8 it drops a word or splits the label into two or three phrases, the twin and the
  slash-named predicates among them".
- **What it changes:** a description; the decision not to run a predicate-descriptions arm stands.

### B25. Every lab change the build makes (the build plan)

- **The finding:** the wiring list named the modules but not every function a j3 kind must touch; the omissions are
  the ones that make a unit silently warm nothing, get no pack, or fall out of the participation floor
  (`run_unit`'s `with_pack` list, `lab/units.py:1158`, the review's `:280` corrected, as A001's A10 found;
  `warm_tasks` has no j3 branch, so a j3 unit would be in no `WarmTask` and get no warm-up; `PREREG_EXPERIMENTS`,
  `REPLY_PARTICIPATION`, `public_text`, `unit_notes`, `harness_measurement`, `harness_verdict`, `build_argv`,
  `write_routing`, `participation`'s j2 branch). The review's answer to "name every lab change" is the list; A001's
  recorded computation is unchanged by all of it, since `lab/j2.py`, `lab/j2labels.py`, `lab.aggregate.j2_block` and
  `lab.summary._j2_tables` are not edited.
- **Now:** the "Wiring, all unpinned" bullet is the review's list with B2's plan and workflow changes, B3's request
  and `lab/j3labels.py` changes, B4's warm-up item and the corrected lines folded in; the tests and docs sentence
  names every test file and every doc line that lists the experiments; `lab/shard.py` is unchanged.
- **What it changes:** the build plan; the builder follows this list.

### Declined

- **Keep P = (a-4b, Eg) under rule 11's reading, with E's word printed beside it** (the finding on rule 11's
  section 7 bullet, the settings row and the Runs entry). Declined for the primary: B1 adopts the conservative
  reading of the standing default, under which no reading applied before `decide` is primary without a received
  call, and the rule does not grant itself the reading under which its primary can pass. Adopted for its printing,
  inverted: the Runs entry prints Eg's word labelled "the primary under the other reading of the section 7
  default", so that a ruling after the run is answered from the record.
- **K alone suffices; stop only when no source of a cycle verified and K did not verify** (the finding on item 5
  and traps 16 and 17, in part). Declined: the rule's floor is that each cycle's set is established, and K alone
  cannot be split into the two cycles' sets, so neither consistency check nor R2 can run on it. The only case in
  which K verifies while none of J1, R1, A1 and R2 does is A001's artifact readable with its chain file intact but
  its labels file failing (K and A1 are files of the same artifact) on a day the complaint file changed, an
  anomaly the plan job should stop on and the loop should ask about. A later rule may relax this with a reason, not
  this one before its first run. The same finding's check "K equal to the union of the other verified sources when K
  and at least one of them verified" is wrong as stated: when only J1 verified, K holds 300 refs and J1 150, so they
  cannot be equal; B5 replaces it by "every other verified source's refs a subset of K" and by equality with the
  union when both cycles' sets are established without the chain. The rest of that finding, the union of every
  verified source as the excluded set, is adopted.
- **"Every one stays unknown under G and under M"** (the finding on Eg's paragraph, in part). Declined for M: under
  M an unclear `describes_predicate` with `mentions_entity` yes becomes `no`, and the pinned `decide` then returns
  `refute` (`verify.py:243-259`); only a no or an unclear to `mentions_entity`, and a failed call, stay unknown
  under M. That is why Egm's arithmetic ceiling (0.917) is above Eg's (0.863); B16's sentence says so. The part
  about G is adopted.
- **Corrected, not declined:** `lab/units.py:280` for `with_pack` is `:1158`; `lab/units.py:301-302` for
  `--budget-seconds` is `SIM_BUDGET_MARGIN_S` at `:189` applied in `build_argv`; "a-4b's arm intervals 0.080 to 0.093
  wide" is 0.087 to 0.093, and the paired intervals 0.093 to 0.107. The blocking finding on the A001 artifact check
  offered two readings and recommended the one that keeps every artifact check pinned code; B3 takes its alternative,
  for the reasons it gives, which the finding allowed.

### Floors after this amendment

| Floor | Before this amendment (`b0f12bd`) | After | Direction |
|---|---|---|---|
| The target and the metric | balanced accuracy 0.860 against the filed-category key, record level, codes hidden, 150 fresh NHTSA complaints (300 questions), scored as J001 | unchanged | unchanged |
| The verdict rule | LOOP-48H section 2.5 verbatim; the words in A5's order; composite arms need both ablation clauses | the same, and: `incomplete` when an ablation difference is not a number (B8); equality fails every strict clause; where the headline and the paired clause disagree the word follows the paired clause (B17) | more cells decided, none loosened |
| The primary pair | (a-4b, Eg): a reading applied before `decide` | (a-4b, E): the shipped `decide` on the answers as answered; Eg the headline secondary; promotion only by a call received before the request push, by dated amendment (B1) | raised |
| The headline arm | Eg | E, P's arm; Eg's line beside it, labelled (B1) | follows P |
| The comparators | lexical judge, record-blind control, predicate-only bound; ablations A and B for the composite arms | unchanged; ablation A named as the lexical judge line for line on a draw without a lexical `unknown` (B8) | unchanged |
| The ablation requirement | a composite's `pass_provisional` needs its paired lower end above ablation A and above ablation B | unchanged | unchanged |
| The interval rule | cluster bootstrap over records, B 10,000, 95%, seed `j1:3`, the same draws for every judge and arm, paired on the records both sides score | unchanged | unchanged |
| The confirmation rule | a first pass is provisional; success needs P named again and passing on the next cycle's fresh draw | unchanged; success unreachable in this cycle | unchanged |
| The draw | `random.Random("nhtsa:3").sample` of 150 over the eligible list of the day's file less the excluded refs; seeds 3; parts 5 | unchanged; on a re-run after a plan job printed, the second draw is the result and both are recorded (B13) | unchanged |
| The exclusion | the union of J001's and A001's established sets; 300 refs | the union of every verified source's refs (J1, R1, A1, R2, K), never smaller than before (B5); 300 refs expected and printed | raised |
| The stop rule | a cycle with no verifying source stops; both verified sources of a cycle must agree; K must equal the union | each cycle's set established by its own sources or by the chain less the other cycle's established set; K alone stops; every consistency count above 0 stops (B5) | unchanged in strength; one artifact's expiry no longer stops on a changed file while the other lives |
| The artifact checks | all pinned code (impossible for A001's labels file) | J1 and K by `lab.j2labels.earlier_refs` unchanged; A1 by `lab.j3labels.earlier_labels_refs`, pinned equal to it on the j1 layout by a test (B3) | made real |
| The comparison count | 9 this cycle, 21 since the loop began | the same; no multiplicity correction, declared with its reason (B15) | unchanged |
| The first run is the result | a re-run only for a failure before any model judged, unchanged | the same; one named setting change before a re-push, a warm-up cut's `max_tokens` by amendment (B7); a re-run's draw is the result (B13) | unchanged; section 6.3's allowance named |
| The tasks and the tokens | N 256; E 512; R 512; pass order E, R, N; texts and schemas pinned by sha256 | unchanged | unchanged |
| What is printed | counts, scores, hashes; no ref, narrative, citation or analysis in any report, summary, console line, commit or document | unchanged; the aggregate reads narratives inside its job only and prints none (B23) | unchanged |
