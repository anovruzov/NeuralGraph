# Accuracy test A003: the third cycle toward 86, the drafted-terms payload on a fresh draw

The founder's goal is "loop until we hit 86". What 86 means is pinned in `docs/strategy/LOOP-48H.md`, section 2
(2.1 to 2.7): balanced accuracy 0.860 of the site verifier's verdicts against the filed-category key, at record
level, with the codes hidden, on a fresh pre-registered draw of 150 real public NHTSA complaints (300 questions),
scored exactly as J001 was scored (`docs/collective/replay/vehicles/CHOICE-J001.md`), with the interval's lower end
above the lexical judge and above the predicate-only bound, one primary (model, arm) pair per cycle, and confirmation
on the next cycle's fresh draw. This file is the rule for the loop's third cycle, lever 5 of the pinned lever order
(section 2.6, item 5, quoted whole under "What this cycle tests"). It is committed alone, on 2026-10-13, before any
code is written for it, before any term is drafted and before any record of its draw exists. How the lab will run it
(a term-drafting kind `jt` that runs in the plan job alone, and a judging kind `j4` on A002's `j3` pattern) goes in
`BUILD-A003.md`, written after this rule, its reviews and its amendments.

Nothing here redefines the target, the metric, the data class, the draw, the comparators, the interval rule or the
confirmation rule. Site-level balanced accuracy stays rejected as primary. Where this cycle adds a comparator (the
lexical judge with the drafted terms), it does so because the pinned lever text says so, and the pass test gets
stricter, never looser (rule 13).

**What the evidence says.** Every result so far is cited as written in its record and is never re-scored here.

- **J001 run 9** (lab run 37996675559; `docs/lab/RESULTS.md`, Run 9; labels sha256 `bc6d092d8bca…`), 150 NHTSA
  complaints, codes hidden: balanced accuracy 0.500 [0.500, 0.500], 0.477 [0.447, 0.503] and 0.387 [0.340, 0.433]
  for a-0p5b, a-1p5b and a-4b, against the lexical judge's 0.670 [0.627, 0.713] (sensitivity 0.380, specificity
  0.960), the record-blind control's 0.527 [0.467, 0.587] and the predicate-only bound's 0.580.
- **A001 run 1** (lab run 38114522160; `docs/collective/A001/CHOICE-A001.md`, "## Runs", Run 1; `docs/lab/RESULTS.md`,
  Run 11; labels sha256 `623780b8a1de…`), 150 fresh complaints with J001's excluded, codes hidden, four arms on three
  models, 12 (model, arm, cycle) comparisons: the primary pair P = (a-4b, negation instruction) scored 0.577 [0.530,
  0.623] against the lexical judge's 0.663 [0.627, 0.703] on the same records, paired difference -0.087 [-0.140,
  -0.033], verdict word **failure**; a-4b with the shipped instruction read through the unclear-to-no mapping scored
  0.667 [0.620, 0.710], paired 0.003 [-0.047, 0.050], not told apart; a-4b negation mapped 0.653 [0.610, 0.697],
  paired -0.010 [-0.063, 0.043]; a-0p5b and a-1p5b scored 0.467 to 0.497 on every arm; the comparators on that draw:
  lexical judge 0.663 [0.627, 0.703] (sensitivity 0.333, specificity 0.993), record-blind control 0.520 [0.463,
  0.577], predicate-only bound 0.577; `available` 58,319 eligible records on that day's complaint file.
- **D002 run 1** (run 38024536763; `docs/collective/onboard/CHOICE-D002.md`, "## Runs", Run 1; the rule is
  `CHOICE-D001.md` with D002's two changes), the drafter on real public exports, no model: on NHTSA, one pack drafted
  per make from each make's complaints received 2020-01-01 to 2022-12-31 (`D002-settings.json`, `arms.nhtsa.train`)
  read the held-out later years (2023-01-01 to 2024-12-31, `arms.nhtsa.test`) with micro F1 0.590 [0.568, 0.611]
  against 0.405 [0.380, 0.430] for the label-names control, coverage 0.840 against 0.476; in the matched label space
  against the hand-built vehicle pack `docs/collective/replay/vehicles/pack/`, drafted 0.590 against the hand pack's
  0.406 on 1,198 records, difference 0.184 [0.157, 0.212] (criterion C2, passed); per make drafted F1 0.529 to 0.661;
  label echo (a sampled narrative holding its own filed label) 33.8% on NHTSA; the refusal refused 666 NHTSA terms
  that had passed the floor, of which 30 would have been assigned and 9 within the 50-term cap; 30 NHTSA predicates
  (over the six packs) learned no term and got a placeholder. On MSHA, drafted 0.559 [0.532, 0.586] against the
  majority prior's 0.344 (C1, passed). D002's reader is the lexical extractor, its metric micro F1 of affirmed claims,
  its unit a (record, predicate) pair: a different metric from this loop's, on the same public field and the same
  held-out years. It is the measured premise of lever 5 and nothing more.
- **A002** (`docs/collective/A002/CHOICE-A002.md`, amended B1 to B25; lab run 38439612256, in flight as this rule
  is written, its plan artifact `lab-plan-38439612256-1`): nine arms on a-4b (N, D, E, Eg, Egm, R, Rm, C, Cm), the
  primary pair P = (a-4b, E `evidence`) with Eg the headline secondary, a fresh draw with seed 3 and J001's and
  A001's records excluded, 9 comparisons, **21 since the loop began** after it. **Its result is not known, and this
  rule assumes nothing about it**: the base task of this cycle is chosen from A002's recorded result by the
  pre-registered adaptation rule of "The base task and the arms", written now with both branches complete, and the
  branch is fixed by a dated amendment that cites CHOICE-A002's Runs entry when it exists.

The pattern across J001, L001 run 2 and A001 is the same (CHOICE-A002, "What the evidence says"): the judges confirm
too much; under the best measured instruction a-4b's residual error is a yes on a negative (A001 run 1, arm C:
sensitivity 0.833, specificity 0.320). What no cycle has given the model yet is any statement of how a component's
problems are usually written: the shipped payload carries the predicate's label alone (`verify.judge_question`,
`mycelic/collective/edge/verify.py:153-159`, `predicate_label`), and the hand pack's lexicon is one to three
phrases per predicate, for 19 of 27 the label in lower case (CHOICE-A002, "Why no predicate-descriptions arm", B24).
D002 measured that a vocabulary learned from an export's own earlier years reads the later years' filed categories
better than that hand list. Lever 5 asks whether such a vocabulary, given to the model, moves it, and whether the
model then adds anything to what the vocabulary alone gives.

**What this cycle tests.** Lever 5 of the pinned lever order, section 2.6, item 5, verbatim: "Drafted-terms payload
(D002: drafted 0.590 vs hand-built 0.406, run 38024536763). Terms are drafted only from records outside the draw and
received before every drawn record (a received-date split); the term set's sha256 is committed in the CHOICE before
the run; in that cycle the lexical comparator of §2.4 is the lexical judge with the same drafted terms (the hand-pack
lexical judge reported beside it), and the pass test must beat the drafted lexical judge." Levers 1 and 2 are A001's,
levers 3 and 4 A002's (measured or running); lever 6 needs a reviewed change to the warm-up (`lab/warmup.py:46-50`),
lever 7 the chief scientist's settings, lever 8 a model rung: none of them this cycle. One primary pair is named
below. By the pinned rule, a pass in this cycle can only be provisional; confirmation needs the next cycle.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen J001's result (RESULTS run 9; CHOICE-J001, Runs), A001's result (CHOICE-A001, Runs, Run 1; RESULTS
  run 11), L001 runs 1 and 2 (RESULTS run 10), RESULTS runs 1 to 11, D001, D002 and D003 (`docs/collective/onboard/`),
  A002's rule and build note (CHOICE-A002, BUILD-A002), the loop prompt (`docs/strategy/LOOP-48H.md`) and the loop's
  status log (`docs/handoff/LOOP-48H-STATUS.md`).
- It has not seen A002's result: lab run 38439612256 is in flight; no figure of it is cited, and the base task of this
  cycle is left to the adaptation rule below, both branches written now.
- It has not seen any record of this cycle's draw. The draw does not exist: it is made in the plan job on a runner,
  from the complaint file as published on the day of the run, after J001's, A001's and A002's records are removed.
  The sandbox cannot reach NHTSA (standing rule 2: real NHTSA data is fetched only on GitHub runners).
- It has not seen any drafted term. No term has been drafted: the term set is produced by a lab run on a runner (the
  term run, kind `jt`) after this rule and its build are committed, from complaints received before the cutoff date
  only, and its sha256 is committed by a dated amendment before the judging run. The term run prints counts and
  hashes, never a term; the author will have seen those counts before the amendment, and no term, no narrative and
  no record of any draw.
- It has not read J001's, A001's or A002's stored replies. The instruction sentence of the drafted-terms task (rule 3)
  was written from the hand pack's shape and from D002's recorded figures, not from any reply text.
- No call on the handoff's decision 9 (how a site verifier's "unclear" counts) and no answer to A002's ask on the
  verbatim-substring check have been received: `docs/handoff/LOOP-48H-STATUS.md` lists both under "Open asks" and
  nothing on them under "Decisions received". The loop prompt's default applies (section 7: "shipped `decide`
  primary, declared mappings secondary"), read as A001's A7 and A002's B1 read it: a reading applied to the reply
  before the pinned `decide` is a declared mapping and secondary. So the primary arm of this cycle reads the answers
  as answered under either branch, and the branch's declared reading (G or M) is the headline secondary (rule 11). A
  call received before the request push is adopted by a dated amendment before any run; one received after the run
  bears on the next cycle.
- The founder ask A002's B6 posted on section 2.3's stop rule stands; this rule rests on the same reading (one
  verifying source per cycle suffices; a verified artifact's refs are the records that cycle judged) for all three
  earlier cycles.
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
- **The data class is fixed** (section 2.3): real public NHTSA complaints of the vehicle pack, the makes `NHTSA_MAKES`
  and the window `NHTSA_WINDOW` of `lab/goldlabels.py:49-50` verbatim, the j1 eligibility rule, n 150, codes hidden,
  through `judge_task`, `judge_payload` and `decide` of `mycelic/collective/edge/verify.py` (`:135-144`, `:162-168`,
  `:243-259`; pinned, unchanged), or through `Runtime.run(task, payload, schema, ref=...)`
  (`mycelic/collective/inference/runtime.py:166-167`) with a lab-side TaskSpec scored by the same pinned `decide`.
  Any change of data class gives a result that can never count toward 86. No arm may place the judged record's
  codes, its filed categories or anything derived from them in the payload, the task text or a reply mapping. The
  drafted terms are derived from other records' filed categories, every one received before the cutoff date and so
  before every drawn record, never from the judged record's (the next section says why that holds by construction);
  the lever is pinned in section 2.6 of the same document, and its known limit is named in trap 2.
- **The comparators** (section 2.4), on the same records, paired, with 95% cluster-bootstrap intervals of the
  difference: the lexical judge (`lexical_judge`, `verify.py:171-198`; J001 0.670 [0.627, 0.713]; A001 0.663 [0.627,
  0.703]), the predicate-only bound (`lab/j1.py:346-361`; J001 0.580; A001 0.577; a point value fitted to the draw's
  answers, no interval) and the record-blind control (`PRIOR_RULE`, `lab/j1.py:133-134`; J001 0.527 [0.467, 0.587];
  A001 0.520 [0.463, 0.577]). This cycle's lexical comparator is **the lexical judge with the drafted terms** (the
  same pinned `lexical_judge` over a pack whose vocabulary is the drafted term set, everything else the hand pack's;
  "The terms", item 9), as lever 5 says; the hand-pack lexical judge is computed and reported beside it on the same
  records, and the pass test must beat both (rule 13). The record-blind control reads the question's predicate
  against `PRIOR_TOP` and the predicate-only bound reads the questions alone (`lab/j1.py:335-361`): neither reads a
  pack's vocabulary, so the drafted pack enters neither, and both are J001's code on the fresh draw, unchanged.
- **Rejected as primary** (gameable): site-level balanced accuracy (the route-role baseline scores 0.808 [0.667,
  0.938] reading no record, L001 runs 1 and 2), codes visible to the judge (it reads the key), and a plain ratio to
  the lexical judge.
- **The secondary** (BA_site − bound) / (BA_central − bound) against a hosted central reader needs a key the lab does
  not have (`NHTSA_HOSTED`, `lab/request.py:297`; LOOP-48H section 7). It is not run and is never assumed. No hosted
  model is named or used anywhere in this cycle; the term run and the judging run make no egress but the complaint
  archive's download from NHTSA on the runner.

## The terms: what they are, where they come from, how they are pinned

This section answers the brief's item (1). Every setting here is fixed now; the figures the term run prints (counts,
hashes) are placeholders until the amendment that records them.

1. **The cutoff date is 2023-01-01, the first day of the pinned window.** `NHTSA_WINDOW` is `("20230101",
   "20241231")` (`lab/goldlabels.py:50`), so every eligible record of every J-cycle draw was received on or after
   2023-01-01, by the pinned eligibility code (`goldlabels.nhtsa_lines`, `:74-93`; `lab.j2labels.eligible_records`,
   `lab/j2labels.py:89-106`, pinned equal to it by A001's A4), which reads the exporter's rows for the window
   (`tools/market/nhtsa_export.py:68-113`, `complaints`: a row is kept only when its received date lies in the
   window). The drafting set is every complaint of the same six makes received **before** the cutoff, from
   2020-01-01 (the archive's first day; `COMPLAINTS_RECEIVED_2020-2024.zip`, `nhtsa_export.py:47`) to 2022-12-31:
   D002's NHTSA training window verbatim (`D002-settings.json`, `arms.nhtsa.train`). With the cutoff at the window's
   first day, "received before every drawn record" and "outside the draw" hold by construction, with no change to
   the draw: the draw of this cycle is A002's draw procedure with seed 4 and three cycles excluded ("The data and the
   draw"), restricted to nothing, since the restriction "received on or after the cutoff" is the pinned window
   itself. A later cutoff (for example the start of 2024) would have given the drafter one more year and restricted
   the draw to part of the pinned window, a change of the draw; the rule declines it.
2. **The received date is one field read two ways.** In the archive it is the complaint file's `LDATE` column
   (`nhtsa_export.py:50`, `C_LDATE`, 0-based field 16, written `YYYYMMDD`), which the exporter writes as the
   export column `received` (`:98`). The pack mapping carries it as `received_date: {"path": "received", "format":
   "yyyymmdd"}` (`docs/collective/replay/vehicles/pack/mapping.json:4-7`; `mycelic/collective/packs/connector.py:252-255`
   parses it) for the draw; the drafter reads the same column as its `date` role (`D002-settings.json`,
   `arms.nhtsa.roles.date` `received`; format `YYYYMMDD`, the second of `roles.DATE_FORMATS`,
   `mycelic/collective/onboard/roles.py:33`, chosen when it parses at least 95% of the values, `:170-179`). The
   two paths read the same bytes of the same file, so the split is one line through one column.
3. **Which records: the six makes, one pooled export.** The drafting set is the rows `nhtsa_export.complaints` builds
   for each of `NHTSA_MAKES` (`goldlabels.py:49`: the draw's six makes, verbatim) over `("20200101", "20221231")`,
   with every top-level component name kept as filed (`tools/onboard/fetch_nhtsa.py:49-54`, `_Every`, as D002's
   split did: none folded into the catch-all) and the `reporter` column holding each row's own complaint number
   (`:45`, `REPORTER`), written as one CSV with D002's seven columns in D002's order (`odino`, `state`, `received`,
   `components[]`, `vehicle`, `summary`, `reporter`; `D002-settings.json`, `arms.nhtsa.columns`; `fetch_nhtsa.py:78-89`,
   `rows_csv`). One export and one drafted pack, not six: the lexical judge takes one pack
   (`lab.j1.lexical_verdicts(pack, labels, questions)`, `lab/j1.py:314-324`) and the hand comparator is one pack
   over all six makes, so the drafted comparator must be one pack over the same six. D002 drafted per make because
   its unit was the company that gives an export; this cycle's unit is the population of the draw. A declared set
   wider than the six makes would draft from records of a population the draw never samples; the rule declines it.
   **The export the drafter reads holds only rows received before the cutoff**: no row received on or after
   2023-01-01 is in it, so neither the drafter's corpus, nor its refusal (which reads the record-id, site and
   forbidden columns of every row of the export it is given, CHOICE-D001 A2), nor the privacy check's n-gram scan
   (which reads every narrative of the export, CHOICE-D001 1.7 item 4) touches any record of the pinned window.
4. **The drafter and its settings: the onboard package's own, unchanged.** `mycelic.collective.onboard.draft.draft_export`
   (`mycelic/collective/onboard/draft.py:931-962`) with the roles fixed in code (`language` `en`; `record_id`
   `odino`; `site` `state`; `date` `received`; `narrative` `summary`; `category` `components[]`; `entities` none;
   `reporter` null; `forbidden` `reporter` and `vehicle`: D002's NHTSA roles, `D002-settings.json`,
   `arms.nhtsa.roles`, with A6's `vehicle` so that make and model words are refused as terms), the training window
   above, and the parameters of `mycelic/collective/onboard/data/defaults.json` (`load_params()` with no argument,
   `draft.py:72-73`): the privacy floor N = 10 records at S = 3 sites (`floor_records`, `floor_sites`; a site is a
   state here), a specific predicate needs R = 50 corpus records (`predicate_min_records`) and a label outside the
   language file's non-specific list (`data/lang/en.json`, which lists `unknown or other`, `:84`), at most 199
   predicates, at most K = 50 terms per predicate (`max_terms`), a term is a unigram or bigram of letters-only tokens
   of at least 3 letters that is no stop word and no negation word (`draft.py:512-538`), at most 64 characters (A4),
   assigned to the predicate with the largest p(c | t) = df(t, c) / df(t) when df(t, c) is at least 10 and p(c | t)
   at least 0.6 (`term_min_df`, `term_min_p`; `draft.py:598-624`), kept in the order largest df(t, c), then larger
   p(c | t), then the term; the refusal of A2 (`draft.py:273-324`: a term equal to, holding, or being a name word of
   a record id, a site value or a forbidden value is refused); a predicate that learned no term gets the placeholder
   `unlearned-<id>` (`draft.py:627-633`). The settings are the package's data files, and the package is hash-pinned
   (standing rule 4): the term run prints the sha256 of `defaults.json` and `lang/en.json` and the code hash over
   `mycelic/collective/onboard/**` (code and data), `tools/onboard/fetch_nhtsa.py`, `tools/market/nhtsa_export.py`
   and `tools/market/vehicle_pack.py` (`mycelic.collective.experiments.common.code_hash`, `common.py:88-94`; the file
   list is CHOICE-D001 A8's), and the amendment records them. No parameter is changed for this cycle; a parameters
   file other than the defaults would be a setting this rule would have to justify, and it has no figure to justify
   one with.
5. **The privacy floor, checked mechanically, before anything is uploaded.** `mycelic.collective.onboard.check.check_pack`
   (`check.py:330-426`) runs on the drafted pack against the same export in the plan job: the term floor recounted
   from the folded sentences, the value floor, the refused strings and terms, the template rebuild (A3) and the
   8-token n-gram scan against every narrative of the export. A pack that fails any check stops the term run before
   its artifact is written (the plan job refuses the plan with a fixed note, `JT_FLOOR_FAILED`): no term set, no
   amendment, and the loop asks. The check's result (counts by check, never a string) is in the artifact and the
   amendment.
6. **What the drafted terms are: the drafted pack's lexicon, mapped to the hand pack's predicates.** The drafter
   makes a pack whose predicates are the filed category names that pass the floor and the minimum, each with its
   value map of spellings (`draft.py:464-499`, `plan_predicates`). The hand pack's value map sends 26 exact NHTSA
   spellings to its 26 specific codes `NHT-001` to `NHT-026` and `UNKNOWN OR OTHER` to `NHT-999` (`mapping.json:9-41`;
   `codes.json`). The term set **T** is, for every specific predicate p of the hand pack, the drafted lexicon (`en`)
   of the drafted predicate whose value map holds the spelling the hand pack sends to p's code, placeholders left
   out: D002's matched space C read from the hand pack's side (`mycelic/collective/onboard/score.py:592-602`,
   `matched_space`: a name is in C when it is a specific predicate of the drafted pack and maps to a specific code of
   the hand pack). A hand predicate whose spelling the drafter left out (below the floor or the minimum, refused, or
   in the other bucket) gets the empty list. `unknown_or_other` is never asked (`lab/j1.py:118`, `EXCLUDED`) and is
   non-specific in both packs; it gets no entry. So T has one entry per hand specific predicate, 26 entries, each a
   list of 0 to 50 terms. The three old-and-new name pairs (`TWINS`, `lab/j1.py:120-121`) are two hand predicates
   each and two drafted categories each: a term goes to one of the two (the larger p(c | t)), so the twin with fewer
   corpus records can be left with few terms or none; the count per predicate is reported (trap 11).
7. **The term-set file and its sha256.** `prereg/jt/terms.json`, canonical JSON (sorted keys, `jsonio.canonical_dumps`),
   holding: `kind` `lab_jt_terms`, `schema_version` 1; the cutoff `2023-01-01` and the drafting window; the makes;
   the complaint archive's sha256 and byte size; the export's row count, the corpus size (training rows with a
   narrative), the categories passing the floor, the specific predicates drafted, the other-bucket share; the
   drafted pack's four loader hashes (`config_hash`, `vocabulary_hash`, `detector_hash`, `fixtures_hash`,
   `mycelic/collective/packs/loader.py:24-30`) and each pack file's sha256; the hand pack's hashes; the check's
   result by check; the onboard code hash and the two data files' sha256; and `terms`: the 26 entries of T, each
   `{"predicate": <hand id>, "spelling": <the NHTSA spelling>, "drafted_id": <the drafted predicate's id or null>,
   "corpus_records": <int or null>, "terms": [...]}`, in hand-predicate id order, terms in the drafter's order. Its
   sha256 over the file's bytes is **the term set's sha256**. The term run prints it in one fixed console line
   (`jt terms: ... terms_sha256 <64 hex>`), in the plan summary and in the report between the `MYCELIC-LAB` markers
   (`lab/summary.py:95-99`); it prints no term. **The term set is never committed to the repository**: it lives in
   the term run's plan artifact (retention 30 days, as every lab artifact of this loop), as the labels files do; the
   amendment commits the sha256, the hashes and the counts. The reason is the no-dealer, no-city rule: the drafter's
   refusal removes record ids, states and vehicle words, and the floor needs ten records at three states, but a
   national dealer chain's name or a word that is also a place could pass, and nothing in a document or a console
   line may carry one. Anyone with repository access can read the artifact.
8. **Where the terms enter the model's payload (a).** The payload of the drafted-terms task is the shipped payload
   (`lab.j1.payload`, `lab/j1.py:292-294`: `verify.judge_payload` of the record with its codes empty) with one key
   added to its question block: `predicate_terms`, the list T[predicate] for the question's predicate, in the
   drafter's order, the empty list when T has none. It sits inside the data block beside `predicate` and
   `predicate_label` (`mycelic/collective/inference/tasks.py:70-80`, `data_block`, `render_messages`: canonical JSON,
   so the key follows `predicate_label` in sorted order), declared to be data by the system text. The task
   instruction names it in one sentence inserted before the base instruction's last sentence (rule 3), in these
   words, fixed now:

   > predicate_terms: words and phrases that earlier complaints filed under that component or system used more often
   > than complaints filed under other components or systems. They show how such a problem is usually written, not
   > whether this record reports one: a record can report the problem in other words, and a term can appear in a
   > record that reports no problem with that component or system. Judge the record's own statements.

   The schema is the base task's, unchanged (the two answers; with the citation under branch E), and so is the
   output cap (`JUDGE_MAX_TOKENS` 256, `verify.py:87`, under branch N; 512 under branch E, `lab/j3.py:95`,
   `FIELD_MAX_TOKENS`): the terms are input, and the reply does not grow. The input grows by at most 50 terms of at
   most 64 characters (about 3,200 characters at the loader's and the drafter's limits, in practice far less: the
   candidates are one or two words of letters), against a narrative cut at 6,000 characters
   (`vocabulary.json`, `extraction.max_input_chars`; `verify.py:162-168`) and a slot of 16,384 tokens
   (`lab/models.json`, `ctx_per_slot`): the narrative is never cut shorter for the terms (trap 5). A positive's and a
   negative's payloads have the same shape: each carries the terms of its own asked predicate, so the presence,
   count or order of terms says nothing about which question is the positive.
9. **Where the terms enter the drafted lexical judge (b).** The drafted pack **P_D** is the hand pack
   `docs/collective/replay/vehicles/pack/` with one change: in `vocabulary.json`, each specific predicate's `en`
   lexicon is replaced by T's list, or by the placeholder `unlearned-<id>` (the drafter's own device, `draft.py:627`)
   when the list is empty, because the loader needs at least one term in the primary language (`loader.py:804-805`)
   and the placeholder occurs in no real text; `unknown_or_other` keeps its hand lexicon (never asked, non-specific
   in the drafter's sense, no drafted entry). Every other file of P_D is the hand pack's byte for byte (`pack.json`,
   `codes.json`, `aliases.json`, `mapping.json`, `egress.json`, `detectors.json`, `rules.json`, `questions.json`,
   `followups.json`, `generator.json`, the fixtures): the entity type, its id format and aliases, the codes and the
   mapping are the hand pack's, so the canonicaliser, the entity mentions, the codes channel and the labels'
   reading are unchanged, and only predicate matching changes. P_D is built by a lab function from the hand
   directory and `terms.json` in a scratch directory (the term run builds it and pins its hashes; the judging run's
   plan job and aggregate rebuild it and refuse any difference), loaded with `loader.load_pack_dir`
   (`loader.py:1522-1566`; the loader's term rules hold: each term folds to 1 to 64 characters, belongs to one
   predicate, `:650-654`, `:789-806`, as the drafter guarantees). The drafted lexical judge is
   `verify.lexical_judge(P_D, Canonicaliser(P_D))` (`verify.py:171-198`, the pinned function) run by
   `lab.j1.lexical_verdicts(P_D, labels, questions)` (`lab/j1.py:314-324`, the pinned function) in the plan job on
   every question's payload, before any model runs: `prereg/j4/lexical_drafted.jsonl` and its scores. The lexical
   judge reads the payload's `question` by its `entity_type`, `entity_id` and `predicate` keys (`verify.py:188-191`)
   and ignores `predicate_terms`, so the same payload serves both judges and the model. The hand-pack lexical judge
   is J001's `prereg/j4/lexical.jsonl` on the hand pack, unchanged, reported beside.
10. **How the sha256 gets committed before the run, with an archive the sandbox cannot reach.** A new lab kind `jt`
    (the term run): a request whose only block is `experiments.jt`, one model-free unit, in which the plan job's
    preregistration step (`lab.prereg`, `.github/workflows/mycelic-lab.yml:75-78`) downloads the complaint archive
    once (`lab/prereg.py:666-679`, `_once`, the same fetch the J-cycles use), builds the pooled export of item 3 in
    memory, drafts (item 4), checks (item 5), writes the drafted pack and `terms.json` under `prereg/jt/`, prints
    the fixed line with the sha256 and the counts, and makes no model call and draws nothing. The unit itself
    (`python -m lab.jt verify` in its shard, model-free, kind `none`, the lab's x1 pattern) re-hashes `terms.json`
    against the preregistration's pin, rebuilds P_D from the hand pack and the file, checks its hashes against the
    pinned ones and exits; the aggregate reports the hashes and counts; the report block carries the sha256. The run
    uploads `prereg/jt/terms.json` and the drafted pack in its plan artifact `lab-plan-<run id>-<attempt>`, never
    the export or any narrative. **Then a dated amendment** of this file records: the term run's id and artifact, the
    sha256 of `terms.json`, P_D's `vocabulary_hash` and `config_hash`, the archive's sha256 and size, the counts of
    item 7, the check's result, the onboard code hash. **Then the judging run's** plan job downloads that artifact
    (a download-artifact step `terms`, "The build plan"), reads `prereg/jt/terms.json`, refuses to run unless the
    file's bytes hash to the committed sha256 in full (64 hex, carried in the request's `terms` entry), unless its
    cutoff, window, makes and hand-pack hashes are this rule's, and unless P_D rebuilt from it has the committed
    `vocabulary_hash` (`J4_TERMS_UNVERIFIED`, a stop before any draw and any model, "stopped before any model ran";
    the loop asks); it never drafts, whatever the archive of its day holds (trap 7). An expired or absent term
    artifact is the same stop; a new term run is then a new amendment with the new sha256, recorded as such.
11. **What the term run reads and prints.** It reads the archive, the hand pack and the onboard package's data; it
    reads no earlier run's artifact and no record received on or after the cutoff. It prints: the archive's sha256
    and size; the row counts per make and pooled; the corpus size; the categories passing the floor and the
    specific predicates; the number of hand predicates reached (of 26), the terms per reached predicate (min, median,
    max, total) and the number left empty; the refusal's counts (A14's shape: refused categories, refused terms,
    assignable, within the cap); the check's result by check; the drafted pack's hashes; the term set's sha256; the
    code hash. Never a term, a category spelling, a narrative, a record id, a state or a vehicle. A category spelling
    is public and D002's report printed them; this cycle's console does not, because nothing in the term set is
    needed in a document before the run, and the artifact holds everything.

## The base task and the arms: the pre-registered adaptation rule

This section answers the brief's item (2). It is written now with both branches complete; the branch is fixed by a
dated amendment, before the judging build, from CHOICE-A002's Runs entry, Run 1, cited as written there. The term run
does not depend on the branch and may happen before A002's result is recorded.

- **Branch E holds when A002 run 1 is complete for task E's set** (the aggregate's word is read: `measurement` true,
  5 of 5 parts, dropped share at most 0.01, as CHOICE-A002 rule 12 reads "complete") **and either** P = (a-4b, E
  `evidence`) has the verdict word `pass_provisional`, **or** the lift `E_minus_N` (CHOICE-A002 rule 10: E's
  balanced accuracy minus N's, paired by record) has its 95% lower end above 0 on A002 run 1. Then the base task of
  this cycle is A002's task E, `judge_record_evidence` (`lab/j3.py:93, 119, 198-204, 214-215`: schema S_E with the
  string `citation` first, the instructions `EVIDENCE_INSTRUCTIONS`, 512 output tokens), imported unchanged, with
  its grounded reading G (`lab/j3.py:264-293`, `grounded`, `grounded_reply`; `GROUNDING_MIN_CHARS` 4) as the declared
  secondary reading.
- **Branch N holds otherwise**: A002 incomplete on task E, stopped before any model ran, or complete with P not
  `pass_provisional` and `E_minus_N`'s lower end at or below 0. Then the base task is A001's task N,
  `judge_record_negation` (`lab/j2.py:80-93`, `NEGATION_INSTRUCTIONS`; `:134-137`, the shipped schema and
  `JUDGE_MAX_TOKENS` 256), imported unchanged, with the unclear-to-no mapping M (`lab/j2.py:101`, `MAPPING`;
  `:162-169`, `mapped_reply`) as the declared secondary reading, as A001 measured it (arm C read as answered, 0.577;
  arm D through the mapping, 0.653).
- **Why these two and no third.** N is the best measured instruction family (A001 run 1: N raised a-4b from 0.307 to
  0.577 as answered and from 0.667 to 0.653 under the mapping, the two mapped arms level with the lexical judge); E
  is N with the citation field, A002's primary, built on N for the reason CHOICE-A002 gives ("stacking a new field
  on an untested instruction would make its lift unreadable"). The rationale task R and the composite arms are not
  bases: a composite is the lexical judge by another name once the terms are in the payload (trap 6), and R's
  direction is unmeasured. The condition on `E_minus_N` is the lift that says whether the field moved the answers;
  without it, E is N with a longer reply, and N is the cheaper base. Ties fail the condition (A002 B17: equality
  fails every strict clause).
- **Under either branch the arms are the same four, on a-4b alone** (rule 5): the base task with the drafted-terms
  payload, read as answered (**D**, the primary arm); D under the branch's declared reading (**Dr**: G under branch E,
  M under branch N; the headline secondary); the base task without the terms, read as answered (**B**, the control:
  the same task the earlier cycle measured, replicated on this draw); B under the reading (**Br**). Model calls are
  made for the two tasks, D's and B's; Dr and Br are readings of the same replies. **P = (a-4b, D).**
- **a-4b is the primary model** and the only one run: it is the only model whose answers separated positives from
  negatives in J001 (yes on 97 of 150 positives and 37 of 150 negatives, RESULTS run 9) and the only one any lever
  moved in A001 (lifts 0.270 [0.217, 0.327] and 0.360 [0.323, 0.393] against 0.000 and -0.010 to 0.030 for the
  others, CHOICE-A001 Run 1). a-0p5b and a-1p5b are not run: they scored 0.467 to 0.497 on every A001 arm, the
  mapping changed no score of either, and a model that answers yes whatever the record would read a list of terms
  as more reasons to say yes; each would add two comparisons with no reading toward 86 ("Why these arms"). No
  figure justifies another primary model.

## The data and the draw

**NHTSA complaints, as J001, A001 and A002 drew them, and nothing else in this cycle.** The reasons are A001's and
A002's: the pinned definition names them; 150 records give 300 questions and 150 clusters with a balanced accuracy
standard error near 0.035; the key's noise is shared by every judge; J001's pipeline (`lab/j1.py`, `lab/goldlabels.py`)
is pinned and importable, so the questions, the comparators and the scoring are J001's code, called unchanged; A001's
exclusion machinery (`lab/j2labels.py`) and A002's (`lab/j3labels.py`, both in A002's code hash, `lab/j3.py:175`,
`CODE_FILES`) are importable, so J001's artifact check, J001's rebuild and A001's chain file are read by
`lab.j2labels.earlier_refs` and `rebuild_refs` unchanged, A001's labels file by `lab.j3labels.earlier_labels_refs`
unchanged, and A001's rebuild by `lab.j3labels.rebuild_a001_refs` unchanged; what is new (A002's labels file, A002's
chain file, A002's rebuild, the stop rules over three cycles) lives in a new module `lab/j4labels.py` that generalises
the two pinned checks to A002's layout and is pinned equal to them on the earlier layouts by a test (as A002's B3 did
for A001's layout).

**A fresh, verifiable draw (section 2.3), with three earlier cycles excluded.** The lab draws
`random.Random("nhtsa:<seed>").sample(eligible, n)` from a complaint file downloaded live in the plan job
(`lab/goldlabels.py:90, 133`), so a new seed alone gives no disjointness, and record refs may never be committed. The
exclusion happens on the runner, in the j4 plan job. Every earlier cycle is excluded by the artifact mechanism and by
the rebuild mechanism, eight sources in all:

1. **J001: J1 (the artifact) and R1 (the rebuild)**, exactly as A002's items 1 and 2: the plan job downloads
   `lab-plan-37996675559-1` (run 37996675559; retention until 2026-11-08T22:00:03Z, CHOICE-A001:119) into
   `${{ runner.temp }}/lab-earlier/37996675559` by the workflow's `earlier` step, keyed on
   `steps.plan.outputs.j2_exclude_run_id` (which `lab.plan.plan_outputs` fills from the first `exclude` entry of a
   j2, j3 or j4 plan, `lab/plan.py:463-485`); `lab.j2labels.earlier_refs` unchanged with the entry (file
   `prereg/j1/labels.jsonl`, sha256 prefix `bc6d092d8bca`) gives **J1**; `lab.j2labels.rebuild_refs(fetch,
   "bc6d092d8bca")` unchanged, the seed-1 n-150 draw from today's file, gives **R1**.
2. **A001: A1 (the artifact), R2 (the rebuild) and K2 (the chain)**, exactly as A002's items 3 and 4: the plan job
   downloads `lab-plan-38114522160-1` (run 38114522160; retention until about 2026-11-10, CHOICE-A002 item 3) by the
   `earlier-2` step keyed on `j3_exclude_run_id_2` (filled from the second entry of a j3 or j4 plan);
   `lab.j3labels.earlier_labels_refs` unchanged on `prereg/j2/labels.jsonl` (prefix `623780b8a1de`; `labels.json` of
   kind `lab_j2_labels`, nhtsa, 150, seed 2; refs equal to `prereg/j2/questions.jsonl`'s, prefix `2ba18ea89bb7`;
   manifest cross-check) gives **A1**; `lab.j2labels.earlier_refs` unchanged on the chain file
   `prereg/j2/excluded.jsonl` (prefix `9a948cbf2cd9`) gives **K2**, 300 refs; `lab.j3labels.rebuild_a001_refs(fetch,
   J001_set, "623780b8a1de", seed=2)` unchanged, `lab.j2labels.draw` of 150 at seed 2 over the eligible list less
   J001's set, gives **R2**, attempted only once J001's set is established.
3. **A002: B1 (the artifact), R3 (the rebuild) and K3 (the chain).** The plan job downloads `lab-plan-38439612256-1`
   (run 38439612256; retention 30 days from its run, `lab/templates/accuracy-002.json`, `retention_days` 30, so
   until about 2026-11-12) into `${{ runner.temp }}/lab-earlier/38439612256` by a third step `earlier-3` of the
   same shape, keyed on `steps.plan.outputs.j4_exclude_run_id_3` and `j4_exclude_artifact_3` (filled from the third
   entry of a j4 plan; "The build plan"). `lab.j4labels.earlier_labels_refs(directory, entry)`, A002's check
   generalised by one more layout row (`prereg/j3/labels.jsonl`; `prereg/j3/labels.json` of kind `lab_j2_labels`,
   the kind A002's B22 kept, saying `source` `nhtsa`, `n` 150, `seed` 3 with `n` equal to the refs found;
   `prereg/j3/questions.jsonl`'s refs equal to the labels'; every file cross-checked against the artifact's manifest
   `prereg/prereg.json`; outcomes `lab.j2labels.OUTCOMES`; every read inside `try`/`except`; nothing from the
   artifact printed, written or uploaded but the refs on the runner), gives **B1**: the file must hash to A002's
   labels sha256 prefix, **placeholder: the prefix CHOICE-A002's Run 1 records, filled by amendment when A002's plan
   job has printed it**. `lab.j4labels.earlier_chain_refs(directory, entry)`, `lab.j2labels._check_earlier`'s chain
   branch generalised to `prereg/j3/excluded.jsonl` (`lab/j2labels.py:152-169`: the prefix, the manifest, one
   `{"record_ref"}` line per ref), gives **K3**, 450 refs expected (CHOICE-A002 item 7), against **placeholder: the
   chain file's prefix from A002's plan job's fixed line, by amendment**. `lab.j4labels.rebuild_a002_refs(fetch,
   J001_set | A001_set, prefix, seed=3)`, `lab.j2labels.draw(eligible, J001_set | A001_set, 150, 3)` over
   `lab.j2labels.eligible_records` of today's file, verified when the bytes of `lab.j2labels.label_lines` over the
   sample hash to A002's labels prefix, gives **R3**, attempted only once J001's and A001's sets are established
   (else `not attempted`).
4. **The stop rules, read now for three cycles** (A002's B5 generalised; each cycle's set must be established).
   - **The sources.** A cycle's set is established by its own sources, the union of its artifact's and its rebuild's
     refs when both verified (J1 or R1; A1 or R2; B1 or R3). A cycle with no verifying source of its own is
     established from a verified chain less the sets of the other cycles that chain covers, each of those
     established without that chain: J001 from K2 less A001's set, or from K3 less A001's and A002's sets; A001 from
     K2 less J001's set, or from K3 less J001's and A002's sets; A002 from K3 less J001's and A001's sets. A chain
     establishes at most one cycle, and a chain alone, with no other cycle established, establishes none (the split
     is not recoverable; A002's "Declined"). R2 is attempted whenever J001's set is established; R3 whenever J001's
     and A001's are, by whichever source.
   - **The consistency counts**, each printed as a count and stopping above 0: J1 and R1 equal when both verified;
     A1 and R2 equal; B1 and R3 equal; every verified source of J001 and A001 inside K2 when K2 verified; every
     verified source of all three cycles, K2 included, inside K3 when K3 verified; K2 equal to the union of J001's
     and A001's sets when both are established by their own sources; K3 equal to the union of all three sets when all
     three are established by their own sources.
   - **The excluded set is the union of every verified source's refs** (J1, R1, A1, R2, K2, B1, R3, K3): 450 refs
     expected, the count printed.
   - **The plan job stops** before any draw and any model, and the loop asks, when any cycle's set is not established
     or when any count is above 0.
   - One mechanism verifying per cycle suffices (A001's A2, A002's item 5, the founder ask B6 posted): the record
     ref is the complaint file's own id column (`mapping.json`, `record_ref`), the same in every version of the
     file. The complaint file's sha256 is printed beside A001's `a5a5de2d31f3…` (CHOICE-A001 Run 1) and A002's
     (placeholder, by amendment).
5. **The draw.** `lab.j2labels.eligible_records` builds the eligible list exactly as `lab.goldlabels.nhtsa_lines`
   builds it (the makes and the window of `goldlabels.py:49-50` verbatim; a narrative, one vehicle, at least one
   specific component code; codes removed; sorted by `record_ref`); `lab.j2labels.draw(eligible, excluded, 150, 4)`
   removes the excluded refs and samples `random.Random("nhtsa:4").sample` of 150, sorted by `record_ref`, in
   label-file lines of `goldlabels`' shape; `lab.j2labels.build_labels` unchanged writes the labels and their record
   (kind `lab_j2_labels`; `exclusion.mechanisms` J001's pair; `exclusion.excluded_runs` one entry per cycle in seed
   order with the artifact's, the rebuild's and the chain's outcomes and counts, A002's B22 shape with a third
   entry; the sources, the cycles and the consistency counts beside them), the complaint file's sha256, `available`,
   the excluded count, the eligible count after exclusion (on A001's day's file, 58,319 less 450 would be 57,869;
   the day's file decides) and the overlap (0, else the plan job stops).
6. **The chain, carried forward.** The plan job writes `prereg/j4/excluded.jsonl`: one `{"record_ref"}` line per
   ref, sorted, refs only, the union of the 450 excluded refs and this draw's 150 (600 lines expected), and prints
   its sha256 prefix. The next cycle names this run's artifact, this file and that prefix. Every later CHOICE lists
   every earlier run id and labels sha256 prefix; for this cycle the list is: J001, run 37996675559, labels
   `bc6d092d8bca`, seed 1; A001, run 38114522160, labels `623780b8a1de`, seed 2, chain `9a948cbf2cd9`; A002, run
   38439612256, labels placeholder, seed 3, chain placeholder, both by amendment.
7. **What is printed:** one fixed line with `available`, the excluded count (450 expected), the eligible count after
   exclusion, the overlap (0 expected), the outcome of each of the eight sources with any by-chain reading named and
   every consistency count computed, the new labels' sha256 prefix, the questions' prefix, the chain file's prefix
   and line count, the complaint file's prefix; then the term check's line (the term set's sha256 verified against
   the request, P_D's hashes); then the comparators' figures (rule 8), both lexical judges first, before any shard
   starts; those figures are never a reason to redraw (LOOP-48H section 6.3). Never a ref, never a term. If fewer
   than 150 eligible unexcluded records remain, the plan job stops, and no further fresh draw is pre-registered. On
   a re-run after a plan job printed (rule 17), both plan jobs' lines are recorded.
8. **The received-date split, checked on the day.** The plan job also prints, from its own download, the number of
   drawn records whose received date lies before the cutoff (0 by the pinned window; a count above 0 stops the plan
   job: the eligibility code or the file would have changed) and the number of drawn records whose folded narrative
   equals the folded narrative of some drafting-set row (`packs.canonical.folded` over `nhtsa_export.complaints` of
   the six makes over the drafting window, computed in memory, nothing of it written): 0 expected, reported, deciding
   nothing (trap 3). The second count reads the day's archive, which may differ from the term run's; the archive
   sha256 of both is printed and compared (trap 7).

## The rule

1. **Records: 150 fresh NHTSA complaints, labels seed 4.** `lab.j4labels` over `lab.j2labels` and `lab.j3labels`,
   source `nhtsa`, the pack `docs/collective/replay/vehicles/pack`, the makes, the window and the eligibility rule of
   `lab.goldlabels` verbatim, J001's, A001's and A002's 450 records excluded as "The data and the draw" says,
   `random.Random("nhtsa:4").sample` of 150. The labels are built from the complaint file as published on the day of
   the run; the file's sha256 and the labels' sha256 are preregistered. Each label record has its codes removed
   (`lab/j1.py:275-279` refuses one that still holds them).
2. **Questions: two per record, J001's code, seed 4, five parts.** `lab.j1.build_questions(pack, labels, seed=4,
   parts=5)` with the hand pack, unchanged: the positive is one filed predicate, the negative follows the other
   records' positives (`NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged, `lab/j1.py:118-130`). Record `i` of 150 is in
   part `i * 5 // 150 + 1`: 30 records and 60 questions a part. The questions file's sha256 is preregistered. 150
   positives and 150 negatives: 300 questions per task.
3. **The tasks: two, declared now; model calls are made for these only.** Every task reads the shipped payload
   (`lab.j1.payload`, codes empty) through `Runtime.run(task, payload, schema, ref=...)`, and every verdict is the
   pinned `verify.decide` over the one record (`lab.j1.verdict`) applied to the reply's two answers, exactly as
   J001, A001 and A002.
   - **Task B, the base task**, imported unchanged: under branch N `lab.j2.negation_task()` (`judge_record_negation`,
     data class `raw`, 256 output tokens, the shipped schema, `NEGATION_INSTRUCTIONS`); under branch E
     `lab.j3.evidence_task()` (`judge_record_evidence`, 512 output tokens, S_E, `EVIDENCE_INSTRUCTIONS`). It is this
     cycle's control: the earlier cycle's arm, replicated on a fresh draw, so that every lift is paired within the
     draw.
   - **Task D, the drafted-terms task**: the same data class, schema and output cap as B; the name
     `judge_record_negation_terms` under branch N and `judge_record_evidence_terms` under branch E; the instructions
     B's text with the sentence of "The terms", item 8, inserted before B's last sentence ("Judge only this
     record."), as A002 inserted E's and R's sentences (`lab/j3.py:112-116`, `_with_field`); the payload B's with
     `question.predicate_terms` added ("The terms", item 8). Nothing else differs between D and B: one key in the
     payload and one sentence in the task text, so `D_minus_B` reads the lever and nothing else (compare A002's
     B18, where a lift carried a schema and a cap with the field).
   - **Codes never reach the judge in any task.** The payload's codes list is empty (the labels hide them; the runner
     refuses a record that holds any). D's and B's texts name no code, category or filed label. `predicate_terms`
     holds words of other records' narratives, drafted before the cutoff; it holds no code, no category spelling
     and nothing of the judged record.
4. **The readings, declared now.** Under branch N, M (`lab.j2.MAPPING`, `mapped_reply`, unchanged: a reply's
   `describes_predicate` `unclear` is read as `no`; `mentions_entity` left as answered). Under branch E, G
   (`lab.j3.grounded`, `grounded_reply`, unchanged: `describes_predicate` `yes` read as `no` when `norm(citation)`
   has fewer than 4 characters or is not a substring of `norm(text)`, `text` the payload's record text; A002's
   rule 4 and B12 verbatim). The reading reads the reply (and, for G, the payload's text); neither reads the codes,
   the filed categories, the question's predicate, the terms or anything derived from them.
5. **The arms: four, declared now, all on a-4b; one primary pair.**
   | Letter | Arm name under branch N | Arm name under branch E | Task | Reading before the pinned `decide` |
   |---|---|---|---|---|
   | **D** | **`negation_terms`** | **`evidence_terms`** | **D** | **the two answers as answered** (the primary arm) |
   | Dr | `negation_terms_mapped` | `evidence_terms_grounded` | D | M under branch N; G under branch E (the headline secondary) |
   | B | `negation` | `evidence` | B | as answered (the control: A001's arm C; A002's arm E) |
   | Br | `negation_mapped` | `evidence_grounded` | B | M under branch N; G under branch E (A001's arm D; A002's arm Eg) |
   **The primary pair P = (a-4b, D).** Every other arm is a secondary, Dr the headline secondary, each reported with
   the same statistics, including rule 13's pass test, and a secondary that meets the pass test is a hypothesis for
   the next cycle's primary, never a result. The kind's constants hold both branches' names; the request's
   `base_task` picks one, and the preregistration pins the four names in use.
6. **The judging path: the product's, through the lab's pinned server.** As A002's rule 6: `Runtime.run` with its one
   repair after an invalid reply and no escalation, at boundary `site:lab`, data label `public`, through the lab's
   pinned model server on one runner; HTTP deadline 600 s and 1 retry; the client sends temperature 0 on every call
   and the server runs with a fixed seed, so one run per model. The routing document routes the two task names to
   the unit's one endpoint, without escalation; a unit refuses a routing that routes either elsewhere or escalates.
7. **Models: a-4b alone** (`lab/models.json`, unchanged: `--reasoning off`), one run ("The base task and the arms").
8. **The comparators, in the plan job, before any model runs:** the drafted lexical judge (`lab.j1.lexical_verdicts`
   over P_D; "The terms", item 9), the hand-pack lexical judge (`lab.j1.lexical_verdicts` over the hand pack), the
   record-blind control (`lab.j1.prior_verdicts`, `PRIOR_TOP` unchanged) and the predicate-only bound
   (`lab.j1.prior_bound`), each scored on every record and, in the aggregate, on each arm's scored records. The plan
   job prints their figures, the two lexical judges' with sensitivity, specificity and unknown count. Those figures
   are never a reason to redraw. The drafted lexical judge and the hand lexical judge are also paired with each
   other, record by record (`lab.j1.paired`), in the plan job: the one figure that says, on this draw and this
   metric, what the drafted terms did to word matching.
9. **Scoring, against the filed codes,** by `lab.j1.score` unchanged on each arm's j1-shaped view: a verdict is
   correct when it is `confirm` on a positive or `refute` on a negative; `unknown` is never correct; sensitivity,
   specificity, balanced accuracy and the unknown share, each with a percentile-bootstrap 95% interval over records,
   `stats.cluster_bootstrap_mean`, B 10,000, `bootstrap_seed` 4 (seed string `j1:4`), a record's two questions drawn
   together, the same draws for every judge and arm scored on the same records. A model failure (no valid reply after
   the repair) is `unknown` and counts. A transport failure leaves that record out of that task's set and of every
   arm built on it; the comparators are compared on the same records. If more than `WITHHOLD_SHARE` 0.01 of a set's
   records leave, the set and its arms are withheld (`lab/j1.py:128`).
10. **Paired differences,** by `lab.j1.paired` unchanged (B 10,000, seed `j1:4`), over the records both sides score:
    each arm minus the drafted lexical judge (four; the pass test's clause); each arm minus the hand lexical judge
    (four; reported beside); the lifts: `D_minus_B` (the lever: the terms in the payload, answers as answered; P
    over the control), `Dr_minus_Br` (the lever under the reading), `Dr_minus_D` (the reading under the terms),
    `Br_minus_B` (the reading under the base: A001's D − C or A002's Eg − E, replicated).
11. **The primary pair, the headline arm, and the exploratory rest.**
    - **P = (a-4b, D).** The 86 verdict of rule 13 is read on P alone. The aggregate computes the verdict word for
      every arm, so the Runs entry prints Dr's word beside P's, labelled "the primary under the other reading of the
      section 7 default" (A002's B1 form).
    - **How P meets "chosen from earlier recorded results before the run."** The model is the only one any lever
      moved (above). The arm is lever 5, the next in the pinned order, on the best measured instruction family (N or
      its measured extension E, by the adaptation rule), read by the shipped `decide` on the answers as answered,
      which is what the standing default allows as primary. No (model, arm) pair measured on a fresh draw has met a
      pass test (A001: every word `failure`; A002: unknown; a pass there would make its arm the base here by the
      adaptation rule), so none is promoted.
    - **The headline arm is D, P's arm**: its balanced accuracy interval is read against the drafted lexical judge's
      balanced accuracy on the same records as J001's rule 7 reads it: **better** only when the interval's lower end
      is above it, **worse** only when its upper end is below it, otherwise **not told apart**; the same words
      against the hand lexical judge beside it. Dr's lines are reported beside D's in the same words, labelled the
      headline secondary.
    - **Every other arm is exploratory:** B and Br (the controls) and Dr. The lifts of rule 10 are read as "helps"
      when the lower end is above 0, "hurts" when the upper end is below 0, otherwise "not told apart"; they decide
      nothing toward 86.
12. **The comparison count.** This cycle makes 4 (model, arm, cycle) comparisons: 4 arms × 1 model × 1 cycle, the
    controls counted. 21 were made before it (12 after A001, 9 declared by A002, CHOICE-A002 rule 11). The running
    count since the loop began is therefore **25** after this cycle. The request carries `comparisons_before` 21;
    the Runs entry prints the count. No multiplicity correction is applied, for A002's reason (B15): the 86 verdict
    is read on P alone and the one headline is P's arm, so there is one decision per cycle; the three other verdict
    words are exploratory and uncorrected, which is why a secondary that passes is a hypothesis and never a result,
    and the running count is printed so that a reader can discount a sampling peak across cycles. The comparators
    (the two lexical judges, the control, the bound) are not arms and are not counted, as A002's ablations were not.
13. **The verdict rule.** Copied verbatim from LOOP-48H section 2.5 (lines re-wrapped; the words are its). Its "§7"
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

    **The added clause, from lever 5's text: the pass test must beat the drafted lexical judge.** For this cycle "the
    lexical judge" in the pass test's second clause, in Partial and in Failure is the lexical judge with the drafted
    terms (rule 8), as section 2.6 item 5 says for a lever-5 cycle; **and** the pass test additionally requires the
    paired difference to the hand-pack lexical judge to have its 95% lower end above 0, so that no reading of lever
    5 lowers the floor A001 and A002 stood on: a drafted judge that scores below the hand judge on this draw would
    otherwise be an easier bar. `pass_provisional` needs the four clauses of the block with the drafted judge in the
    second, and the hand clause. A P that meets the four clauses but not the hand clause is `partial`, reported with
    both lexical figures. For this cycle, read as A001's A5 and A6 and A002's rule 12 read it: "complete" is read per
    task set (every part of the set finished for that task, `measurement` true, `records_not_judged` 0, the dropped
    share at most 0.01); P's set is task D's, and "the same draw" is the 150 records task D's set scored. The
    aggregate evaluates the verdict words in the order **`incomplete`, `pass_provisional`, `partial`, `failure`**,
    the first whose condition holds, with the figures behind every clause printed beside it; a missing figure of
    either lexical judge, the bound or the interval makes the word `incomplete`. Equality fails every strict clause
    (A002's B17; `lab.j2.pass_test` compares with `>`). Where the headline (the arm's interval against a judge's
    point) and the paired clause disagree, the word follows the paired clause. Success in the sense of the rule
    cannot be reached in this cycle.
14. **Reported beside the verdict, deciding nothing:** for every arm: sensitivity, specificity, accuracy and the
    unknown share with intervals; the count of each answer pair, for positives and for negatives, as answered and
    after the reading; the verdict counts; failures by kind and per task; per predicate, how often each judge
    confirmed it, for positives and for negatives (`by_predicate`); both lexical judges, the control and the bound
    on the same records, with the drafted judge's sensitivity, specificity and unknown count and its paired
    difference to the hand judge; per predicate, the number of drafted terms T carried (0 to 50) and, for D's lines,
    the count and share of questions whose payload carried an empty list; the agreement of D's verdicts with the
    drafted lexical judge's, per kind (the share of questions on which the two verdicts are equal), and D's confirm
    rate where the drafted judge confirms and where it refutes, per kind (the readings of trap 6); under branch E,
    for each of D and B: the grounded share of `yes` answers on positives and on negatives, the share of empty
    citations, the median and 95th percentile of citation length after `norm`, and the share of grounded citations
    that equal, after `norm`, one of the asked predicate's drafted terms (trap 4); the share of replies whose first
    key is the new field (branch E) per task; the output tokens' median and 95th percentile and the replies cut at
    the cap per task (from the ledgers); seconds per judge call per task (median and 95th percentile) with the
    runner CPU model, a runner number, not site hardware; and B's and Br's figures beside the earlier cycle's arms
    (A001 run 1's C and D under branch N; A002 run 1's E and Eg under branch E), labelled a between-draw difference.
15. **Units and limits** (sized in "Why the units are this size"):
    - one unit per part: **5 units**; a unit judges its part's 60 questions twice, in two passes, in this order:
      **task D first** (P's set), then task B (the control last: a late stop loses the control, which decides nothing
      toward 86, before it loses the lever's reading); 120 calls a unit;
    - each unit has 300 minutes (the request's `minutes`) and runs alone in its shard; `job_minutes` 330 gives a
      shard capacity of 305 and a job timeout of 325 (`SHARD_OVERHEAD_MINUTES` 25, `lab/request.py:222`);
      `max_parallel` 5 (the cap is 16, `lab/request.py:1104`); the unit's budget is the smaller of 300 minutes and
      the shard's remaining time (the job start plus 315 minutes, the timeout less `JOB_TAIL_MINUTES` 10,
      `lab/shard.py:138-152`, less `DEADLINE_MARGIN_S` 120 s, `:133`) less `SIM_BUDGET_MARGIN_S` 30 s
      (`lab/units.py:197`, `:1218-1219`), so 300 is its upper bound and provisioning, the server start and the
      warm-up come off it when they take more than about 13 minutes (A002's B20);
    - each unit stops at its budget and keeps what it judged (`stopped: budget`); a stop ends the pass it falls in
      and the pass after it; a finished earlier pass stays complete; `run.json` records `complete_by_task`,
      `stopped_task` and `questions_done_by_task`;
    - **a task set is scored only when all 5 of its parts finished for that task.** Otherwise its finished parts are
      a partial reading and decide nothing. The arms of one task are complete exactly when the task's set is.
16. **What is written, and the guards: A002's, unchanged in kind.** Each verdict line holds the question id, the
    record ref, the kind, the predicate, the task, the whole reply (the two answers and, under branch E, the
    citation), whether the citation is grounded (branch E), the reply's first key as written (A002's B19), the error
    kind, `scored`, and every reading's verdict: the shipped reading's and the branch reading's, each by the one
    pinned `decide`; never a narrative beyond what the reply itself quotes, and never the terms (the terms are the
    artifact's `terms.json`, once). The aggregate recomputes every reading's verdict from the stored reply with the
    pinned `decide`, recomputes `grounded` under branch E from the stored citation and the payload text it rebuilds
    from the plan artifact's labels (`lab.j1.payload`, inside the aggregate job only, A002's B23), checks the first
    key for shape, and refuses a line that disagrees with any of them. It rebuilds each question's `predicate_terms`
    from the plan artifact's `terms.json` and refuses the block unless that file hashes to the preregistration's
    pin. The plan artifact holds the labels, questions, both lexical files, the control, the chain file and a copy of
    `terms.json` with the term run's artifact name and sha256 (public complaint text, refs and public vocabulary; the
    artifact, never the repository). Reports, step summaries, console lines and commits hold counts, scores and
    hashes only: no narrative, no citation, no term, no record ref, no VIN, no city, no dealer, no operator name.
    The warm-up calls the two tasks once on a generated record about no drawn record (the judge's generated
    payloads, `lab/warmup.py:131-143`, `judge_example`), task D's payload carrying T's list for the generated
    record's asked predicate (the pack's first sorted predicate); the probe is the first fitting task's in sorted
    (task name, pack) order (`lab/warmup.py:417`, `fitting[0]`), which is task B's under either branch
    (`judge_record_evidence` sorts before `judge_record_evidence_terms`; `judge_record_negation` before
    `judge_record_negation_terms`), so `repeat_identical` is recorded for the control's task, not P's, and the D
    pass, run first, starts on a cold prefix (A002's B4 inverted); latency is reported per task and decides nothing.
17. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3); a term artifact that has expired or does not
    verify is such a failure (a stop before any model ran), and the re-run needs a new term run and a new amendment
    with the new sha256 (the terms are the same rule's output on a later archive day; both sha256 values are
    recorded). A set left incomplete gets no verdict from this run; judging it again is a new choice file. Any
    change to a setting, to D's sentence, to the cutoff, to the drafter's parameters, to the readings or to the
    arms is a new choice file; a warm-up reply cut at the cap on the generated record is the one setting change this
    rule allows before a re-push, by a numbered amendment that raises that task's `max_tokens` (A002's B7). If the
    plan job printed and the run then failed before any model judged, the re-run's plan job draws from the complaint
    file of its own day under the same seed, both plan jobs' fixed lines are recorded and the second draw is the
    result (A002's B13). After the run, no reading other than the four arms is made of the replies: anything else
    they suggest (another term order, a shorter list, a term-matching reading of the citation) is a hypothesis for
    the next cycle's rule.

**The settings, all fixed now.**

| Setting | Value |
|---|---|
| Models | a-4b; one run; temperature 0 (the client), fixed server seed; `--reasoning off` (`lab/models.json`, unchanged) |
| Population | NHTSA complaints, pack `docs/collective/replay/vehicles/pack`, `NHTSA_MAKES` and `NHTSA_WINDOW` verbatim, j1 eligibility |
| Cutoff and drafting set | cutoff 2023-01-01 (`NHTSA_WINDOW[0]`); the six makes' complaints received 2020-01-01 to 2022-12-31, one pooled export of D002's seven columns, every row received before the cutoff |
| The drafter | `mycelic.collective.onboard.draft.draft_export`, D002's NHTSA roles (`vehicle` and `reporter` forbidden), `data/defaults.json` (N 10, S 3, R 50, K 50, df 10, p 0.6, 3 letters, 64 characters, 8-gram), `lang/en.json`; the check `check_pack`; code hash over the onboard package and the three download modules, recorded by amendment |
| The term set T | one entry per hand specific predicate (26), the drafted predicate's `en` lexicon through the hand value map's spelling, placeholders left out, empty when unreached; `prereg/jt/terms.json`; sha256 placeholder, by amendment after the term run |
| The drafted pack P_D | the hand pack with each specific predicate's `en` lexicon replaced by T's list or the placeholder; `vocabulary_hash` and `config_hash` placeholders, by amendment |
| Excluded | J001's 150: run 37996675559, artifact `lab-plan-37996675559-1`, `prereg/j1/labels.jsonl` `bc6d092d8bca…`, rebuild seed 1; A001's 150: run 38114522160, `lab-plan-38114522160-1`, `prereg/j2/labels.jsonl` `623780b8a1de…`, chain `prereg/j2/excluded.jsonl` `9a948cbf2cd9…`, rebuild seed 2 over the eligible list less J001's set; A002's 150: run 38439612256, `lab-plan-38439612256-1`, `prereg/j3/labels.jsonl` placeholder, chain `prereg/j3/excluded.jsonl` placeholder, rebuild seed 3 over the eligible list less J001's and A001's sets; 450 refs expected; eight sources, the stop rules of item 4 |
| Labels | n 150, seed 4 (`random.Random("nhtsa:4")` over the eligible records less the 450 excluded), codes removed |
| Questions | `lab.j1.build_questions`, seed 4, parts 5; `NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged |
| Base task | branch E: `judge_record_evidence` (`lab.j3`, 512 tokens, S_E, reading G); branch N: `judge_record_negation` (`lab.j2`, 256 tokens, the shipped schema, reading M); the branch by the adaptation rule, fixed by amendment from CHOICE-A002 Run 1 |
| Task D | the base task's name with `_terms`; the base's schema and cap; the base's instructions with the terms sentence before the last sentence; the payload with `question.predicate_terms`; pinned by sha256 |
| Arms | D `<base>_terms`, Dr `<base>_terms_<reading>`, B `<base>`, Br `<base>_<reading>`; pass order D then B |
| Primary pair | P = (a-4b, D); Dr's verdict word printed beside P's, labelled the primary under the other reading of the section 7 default |
| Headline arm | D, P's arm, against the drafted lexical judge, the hand judge beside; Dr the headline secondary; 4 (model, arm, cycle) comparisons this cycle, 25 since the loop began; no multiplicity correction |
| Comparators | the drafted lexical judge (the pass test's), the hand-pack lexical judge (beside, and a second pass clause), the record-blind control (`PRIOR_TOP`), the predicate-only bound; all in the plan job |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 4 (`j1:4`), 95% percentile; paired differences with the same draws |
| Codes in the payload | empty in every task; a record with codes is refused; the only key added to the payload is `predicate_terms`, on task D |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 600 s, 1 retry; no escalation |
| Units | 5 (1 model × 5 parts), 120 calls each, 300 minutes, one per shard; `job_minutes` 330; `max_parallel` 5 |
| Withhold | `WITHHOLD_SHARE` 0.01 per task set |
| Requests | the term run: `lab/templates/terms-003.json`, copied to `lab/requests/terms-003.json`, kind `jt`; the judging run: `lab/templates/accuracy-003.json`, copied to `lab/requests/accuracy-003.json`, kind `j4`; `retention_days` 30; `provider` `llama-server`; `comparisons_before` 21 |

## Why these arms, and why this primary pair

**The yes on a negative is the failure to fix, and no cycle has told the model what the component's problems look
like.** Under the shipped instruction a-4b abstains (unknown share 0.560, A001 run 1, arm A); under the negation
instruction it answers, mostly yes (sensitivity 0.833, specificity 0.320, arm C); the mapping converts abstention into
refutation and the best it reached was level with the lexical judge (0.667 [0.620, 0.710], paired 0.003 [-0.047,
0.050]). The payload names the component by its label alone. D002 measured, on the same six makes and the same
held-out years, that a vocabulary learned from the earlier years' filed categories reads the later years' categories
far better than the hand pack's label-like lexicon (micro F1 0.590 against 0.406, difference 0.184 [0.157, 0.212],
coverage 0.840 against 0.476): the drafted terms carry more of how a filed problem is written than the label does.
Lever 5 puts that vocabulary in front of the model and, by the same stroke, raises the comparator the model must beat
to the lexical judge that matches those same terms.

- **Arm D (the primary)** gives the model the terms and reads its answers as answered. `D_minus_B` is the lever's
  reading: whether the terms move the answers at all, and in which direction (read with sensitivity and specificity:
  a list of terms can make a model say yes more, which lowers specificity, or let it say no to a component whose
  usual words are absent, which raises it). `D` minus the drafted lexical judge is the question the loop needs
  answered: whether the model adds anything to the terms, or merely matches them.
- **Arm Dr (the headline secondary)** reads D under the branch's declared reading: the mapping under branch N (A001's
  measured lift of the mapping under N: 0.077 [0.047, 0.107]) or the grounding check under branch E (A002's
  measured lift, unknown here). It is secondary under the standing default (the declaration); a call before the
  request push promotes it by amendment.
- **Arms B and Br** are the controls: the earlier cycle's arm and its reading on a fresh draw. Without B a lift could
  be a between-draw difference (A002's B14: two independent draws differ by sampling alone by up to about 0.10). B's
  figures beside the earlier cycle's are a replication, reported, not tested.
- **The drafted lexical judge is a comparator, not an arm.** It calls the pinned `lexical_judge` and makes no model
  call. It is computed in the plan job before any shard starts, as every comparator is, and the pass test must beat
  it (rule 13). It is also the ablation that lever 4's composite needed and this cycle's arms do not: a model that
  answers yes exactly when a term occurs in the text reproduces the drafted lexical judge line for line, and the
  agreement and conditional confirm rates of rule 14 say how close D came to that (trap 6).
- **Why a-4b alone**: "The base task and the arms". Running the two smaller models would add four comparisons and
  runner minutes with no reading toward 86 that A001 did not already give; they return when a lever is one they can
  follow, or when lever 8 replaces them with a larger local rung.
- **Why no third task.** A task that carries the terms without the sentence, or the sentence without the terms,
  would separate the instruction from the data; that costs 300 calls per task and a comparison, and the lever's
  text names the payload, not the sentence. A task carrying the hand pack's lexicon phrases instead of the drafted
  terms (the predicate-descriptions arm A002 declined) would test the hand comparator's lexicon; it is left to a
  later rule with the chief scientist's view.
- **The honest expectation for P, from the recorded figures: failure.** The drafted lexical judge is expected to
  score higher than the hand lexical judge on this draw, because its terms read the filed categories better on the
  same years under D002's metric (coverage 0.840 against 0.476 says it fires on far more records; its sensitivity
  should rise well above the hand judge's 0.333 to 0.380 and its specificity fall below 0.960 to 0.993; whether
  balanced accuracy rises is what the plan job measures, and no figure here says by how much). The bar P must clear
  therefore rises above the 0.663 to 0.670 the hand judge gave, while the model with the label alone scored 0.577
  as answered and 0.653 to 0.667 under a mapping. For P to pass, the terms would have to move a-4b by more than they
  move word matching, and above 0.860. Nothing recorded predicts that. The cycle still measures what the loop needs:
  the lever's direction (`D_minus_B`), the model's margin over the terms it was given (D minus the drafted judge),
  and the drafted judge's own balanced accuracy on this metric, which D002 did not measure.
- **What is left out and why.** a-4b with reasoning on (lever 6) needs a reviewed change to the warm-up
  (`lab/warmup.py:46-50`); the R003 settings and pack-v2 (lever 7) need the chief scientist; a larger local rung
  (lever 8) needs a manifest entry and a check run. Four arms on one model is the smallest set that pairs the lever
  with its control under both readings.

## Why the units are this size

- **The calls.** 5 units of 120 calls: 600 model calls, 300 per task. The two reading arms add none.
- **The recorded per-call latency.** A001 run 1's a-4b judge calls on real complaint narratives, two-word replies,
  256-token cap: medians 8,211.6 to 9,431.5 ms by runner CPU and task (95th percentiles 11,754.1 to 16,427.0 ms;
  CHOICE-A001 Run 1, the latency table). At those medians a pass of 60 calls of task B under branch N takes 493 to
  566 s, a unit of 120 calls 1,002 to 1,255 s; the longest A001 shard (`s011-a-4b`, 120 calls) took 1,377.2 s of
  wall time. A002's per-task latencies are not known as this is written; its Runs entry will give them, and the
  amendment that fixes the branch cites them beside these.
- **The longer payload.** Task D's input grows by `predicate_terms`: at most 50 terms of at most 64 characters each,
  so at most about 3,200 characters more in the data block, against a narrative of up to 6,000 characters; the
  reply does not grow. The extra cost is prompt processing only. A001's figures do not separate prompt processing
  from decoding, and this rule invents no rate; it bounds a D call at twice an A001 call: a D pass of 60 calls at
  most about 1,130 s at the slowest recorded median, a unit under branch N at most about 1,700 s, under half an
  hour, against a 300-minute budget.
- **Under branch E** the replies carry citations (512-token cap). A002's arithmetic for E (its "Why the units are
  this size": a citation of 10 to 60 tokens adds about 1 to 9 s at the recorded decode rate of 7.0 to 9.0 tokens per
  second, RESULTS run 1, the E3 table, a-4b; a reply cut at the cap adds about 57 to 73 s and its repair about as
  much again) applies to both passes here: at the medians the two passes take under an hour; if every call of both
  passes ran to the cap and was repaired once, a unit would need about 120 × 160 s, more than 300 minutes, and the
  budget stop would fall in pass B, with D, P's set, complete (rule 15). A002's recorded cut counts, once known, say
  how far from that worst case a-4b sits.
- **One unit per shard** lets a slow unit run to its budget without delaying another. The expected wall time is
  about 1 to 2 hours, dominated by provisioning, the warm-up and the D pass; the five shards run at once. The job
  budget is five shards of at most 330 minutes each, as A002's; the expected use is a small fraction of it.
- **The term run** is one model-free unit of 10 minutes in a 45-minute job (the minimum `job_minutes`,
  `lab/request.py:1103`); its work is the plan job's `prereg` step, whose timeout is 20 minutes
  (`.github/workflows/mycelic-lab.yml:75-78`): the archive download (A001's whole plan job took 44 s, the status log,
  05:28), the pooled export, the draft and the check (D002's NHTSA arm, six per-make drafts with their checks and
  scoring, took 358 s on one shared runner, CHOICE-D002 Run 1). One pooled draft over three years of six makes has
  more rows than any one of D002's per-make drafts and fewer than their sum; if the step does not fit 20 minutes,
  the fix is a workflow change to the step's timeout, reviewed, with no setting of this rule changed.
- If a pass does not finish anyway, rule 15 applies.

## How it is read

- **The verdict on P** is read by rule 13 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p", where l is
  the drafted lexical judge's figure, with the hand judge's printed beside it.
- **The drafted lexical judge against the hand lexical judge**, paired by record in the plan job, is the first line
  of the Runs entry after the fixed line: it says what lever 5's vocabulary did to word matching on this metric
  before any model ran. A drafted judge above the hand judge (lower end above 0) raises the bar as expected; one not
  told apart from it says the terms carry no more than the labels on this draw; one below it says the extra terms
  fire on negatives more than they find positives. All three cases leave the pass test as rule 13 states it.
- **The honest expectation** is failure on P ("Why these arms"). The lever's reading survives a failure: `D_minus_B`
  with sensitivity and specificity, and D minus the drafted judge.
- **The headline** (rule 11) says whether a-4b with the drafted terms judges better than, worse than, or not apart
  from word matching with the same terms, and the same against the hand judge; Dr's lines are reported beside.
- **The lifts** (rule 10) say what the lever did. Read `D_minus_B` with sensitivity and specificity: terms that make
  the model confirm more raise sensitivity and lower specificity and move balanced accuracy little; terms that let
  it refute a component whose usual words are absent raise specificity. `Dr_minus_D` repeats the reading's measured
  effect under the terms; `Br_minus_B` replicates it without them.
- **The agreement readings** (rule 14, trap 6): a D whose verdicts agree with the drafted lexical judge on nearly
  every question, and whose confirms follow the judge's confirms, is word matching with a model in the loop; a D
  that confirms where the judge refutes and refutes where it confirms is reading something else, well or badly, and
  its paired difference to the judge says which.
- **Branch E's citation readings** (rule 14, trap 4): the share of grounded citations that equal a drafted term says
  how often the model's evidence is the term it was handed; a high share with no gain over the drafted judge is the
  trap closed.
- **150 records resolve little** (A002's B14): a balanced-accuracy interval is up to about 0.14 wide; a paired
  difference to a lexical judge is told from 0 only when it is about 0.05 or more; `partial` needs an arm about 0.05
  or more above the drafted judge, whatever that judge scores; an arm between is `failure` for want of power, not
  evidence that the lever does nothing.
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. The record-blind control and the predicate-only bound
  are read as J001 reads them: neither decides anything; a model at or below the bound is not shown to read.
- **This is one public field,** complaints written to a regulator, with the models possibly having seen them in
  training. A pass here would be a pass on public text the models may have seen; the Runs entry says so.

## Traps

1. **Terms drafted from any drawn record, or from records received after a drawn record.** Closed by construction
   ("The terms", items 1 to 3): the drafter's export holds only rows received before 2023-01-01, the first day of the
   pinned window, and every drawn record was received on or after it by the pinned eligibility code; the plan job
   checks the first half on the day (item 8 of "The data and the draw": the count of drawn records received before
   the cutoff, 0 or stop). The term run reads no earlier cycle's artifact and nothing of this cycle's draw, which
   does not exist when it runs. What is not closed by a date: a complaint re-submitted after the cutoff with the
   same narrative as one before it (trap 3).
2. **A drafted vocabulary that encodes the key.** The drafter learns from filed categories, which are the key's
   source. What the received-date split keeps out: the drawn records' own categories, narratives, ids and states
   enter nothing, neither the corpus (`draft.py:204-227`, training rows only), nor the refusal (every row of the
   export it is given, and the export ends before the cutoff), nor the check, nor the privacy floor; so no drawn
   record's filing shaped any term, and a term can carry nothing about any drawn record in particular. What leaks
   through category-level learning even so: the terms encode the filing convention, how complaints filed under a
   component usually name it and its failures, over tens of thousands of other complaints of the same six makes;
   p(c | t) at least 0.6 with df(t, c) at least 10 favours frequent categories in ties and thresholds; and the
   payload's list for a predicate is longer when the category is common. A judge that matches the terms therefore
   recovers part of the key without reading for a problem, and a model told the terms can do the same. That is the
   lever's known limit, not a hidden one: the lever text itself makes the lexical judge with the same terms the
   comparator, and the pass test must beat it; the record-blind control and the predicate-only bound stand against
   the frequency leak as before; and each question carries the terms of its own predicate, positive or negative
   alike, so the list's presence and length say nothing about which question is which beyond the predicate's
   frequency, which the bound already prices.
3. **Duplicate narratives across the split.** A drawn complaint whose text equals a pre-cutoff complaint's (a
   re-submission, a copied narrative) was, as text, in the corpus, though as a record it was not. A term is one or
   two words in at least ten records at three states, so no single narrative makes a term, and the 8-gram check
   refuses nothing of this kind (terms are shorter). The count of drawn records whose folded narrative equals a
   drafting-set narrative is printed (item 8), from the day's archive, 0 expected; it decides nothing.
4. **A model that copies drafted terms into the citation (branch E).** The grounding check g says a citation came
   from the record, not that it reports a problem (A002 trap 3); a drafted term that occurs in the record is a
   grounded citation of at least 4 characters, and the drafted lexical judge confirms there too. The share of
   grounded citations equal to a drafted term, per kind (rule 14), exposes this; the pass test's clause against the
   drafted judge is its backstop. A term copied that is not in the record is ungrounded and read as a no under G,
   which costs the model a true confirm if the record reported the problem in other words.
5. **A payload so long that the narrative is cut.** The narrative is cut at 6,000 characters by `judge_payload`
   before anything is added (`verify.py:162-168`, `truncate`), and `predicate_terms` is added beside it, never in
   its place: the terms never shorten the narrative. The whole prompt (system text, the task's instructions, the
   schema, the data block with a 6,000-character narrative and at most about 3,200 characters of terms) stays far
   inside the 16,384-token slot. A reply to a prompt the server could not fit would be an HTTP error, a transport
   failure, counted and withheld as rule 9 says; the warm-up's worst-case payload (the longest generated narrative
   repeated to the cap, `lab/warmup.py:126-128`) carries the terms too, so a slot that cannot hold the D payload
   stops the shard before any drawn record is sent (an infrastructure failure under rule 17).
6. **The drafted lexical judge is the model's own payload: a composite by another name.** The lexical judge and the
   model read the same list. A model that confirms exactly when a term occurs in the narrative reproduces the judge
   and inherits its score with the model contributing nothing, as lever 4's composite would inherit the hand judge.
   The pass test's clause against the drafted judge (rule 13) is what lever 4's ablation clause was; the agreement
   share and the conditional confirm rates of rule 14 say how far D is from the judge; and `D_minus_B` says whether
   the terms moved the model at all. A D that equals the drafted judge line for line is reported as such.
7. **The plan job drafting from a different archive day than the term run.** Closed: the judging run's plan job
   never drafts; it downloads the term run's artifact and refuses unless `terms.json` hashes to the committed sha256
   and P_D rebuilt from it has the committed `vocabulary_hash` ("The terms", item 10). The archive it downloads for
   the draw may differ from the term run's (a re-published file); both sha256 values are printed and compared, the
   draw comes from the day's file as every cycle's did, and the terms are the committed bytes whatever the day's file
   holds. A term artifact that has expired stops the plan job; a new term run on a later archive is a new sha256 by
   amendment, never a silent re-draft.
8. **Filed codes are not truth** (J001 trap 1). A narrative can describe a component it was not filed under, and the
   reverse. No judge can reach 1.0, the key's noise ceiling is unmeasured, and 0.860 may sit above it for some
   predicates. The key costs every judge, both lexical ones included; and the drafter learned from that same key.
9. **Old and new names** (J001 trap 2; CHOICE-A002 trap 2). The pack keeps three old-and-new name pairs as separate
   predicates (`TWINS`); a negative is never the other name of a filed component; a positive is the filed name. The
   drafter sees the two names as two categories and gives each term to one of them, so the terms of a twin pair are
   split by filing history, not by meaning (trap 11). No text can tell the two names apart.
10. **The mapping converts abstention into refutation** (A001 trap 10; branch N). Under M, "I cannot tell" counts as
    "not described". Dr and Br carry that reading; it is decision 9's, pending, and secondary here.
11. **Empty and thin term lists.** A hand predicate the drafter did not reach (below the floor or the minimum of 50
    corpus records, refused, or the losing twin) gets an empty list: on its questions task D is task B with an
    empty key and one sentence more, and the drafted lexical judge's placeholder matches nothing, so the judge
    refutes every such question (`mentions_entity` yes from the structured vehicle, `describes_predicate` no). The
    count of empty lists among the 300 questions and the terms per predicate are reported (rule 14); D002 recorded
    30 placeholder predicates over its six per-make NHTSA packs, so some are expected here too, fewer on one pooled
    corpus. A thin list biases the drafted judge toward refuting those predicates and the model toward whatever it
    does without terms; neither is a leak, both are reported.
12. **The terms' order carries frequency.** The list is in the drafter's order, largest df(t, c) first: the first
    term is the category's most frequent learned word. The order is part of the drafted pack's vocabulary and is
    fixed now; a cycle may not re-order the list after seeing the result (rule 17).
13. **One model.** With a-4b alone there is no reading of whether the lever generalises across model sizes; the
    reason is recorded above, and it is a multiplicity choice, not a claim about other models.
14. **Four arms, one draw.** With a standard error near 0.035, the best of four readings on one draw is biased upward.
    The pass test is read on P alone; a secondary that passes is a hypothesis; the confirmation rule exists for this
    reason, and the running count (25 after this cycle) is printed.
15. **Training data.** The models may have seen these public complaints, the fresh draw's and the drafting set's
    included. That would help them, not the lexical judges. Exclusion of the three earlier draws removes records this
    model judged, not records it may have read in training.
16. **Post-hoc readings.** The replies are stored, so other readings (a term-matching reading of the answers, a
    shorter list, another order) can be computed after the run. None counts: only the four arms are readings of this
    cycle. Anything else is a hypothesis for the next cycle's rule on a fresh draw (rule 17). J001, A001 and A002 are
    never re-scored.
17. **Tuning on the draw.** D's sentence, the cutoff, the drafter's parameters, the readings, the seeds and the parts
    are fixed in this file before the terms and the draw exist. The plan job prints the comparators before any shard
    starts; those figures are never a reason to redraw, and the drafted judge's figure is never a reason to re-draft.
    A seed or cutoff change is a numbered amendment stating a cause independent of the printed figures, reviewed
    before the build.
18. **Three artifacts expire.** J001's on 2026-11-08T22:00:03Z, A001's about 2026-11-10, A002's about 2026-11-12,
    and the term run's 30 days after it runs. After each expiry that cycle's freshness rests on its rebuild and on
    the chains under item 4; after the term artifact's expiry the judging run cannot start without a new term run
    and a new amendment. The judging request must therefore be pushed within 30 days of the term run.
19. **The complaint file changes.** The labels come from the file as published on the day. The exclusion works on
    refs, so freshness holds whenever an artifact verifies, and a changed file is reported through the rebuilds'
    prefixes and the file's sha256 beside `a5a5de2d31f3…` and A002's. If the file changed, the three rebuilds fail
    together, since each later rebuild samples the eligible list the earlier ones read; the artifacts then carry
    all three cycles.
20. **The texts are written for this pack.** "Component or system" fits vehicle complaints, as N and E do (A001 trap
    15, A002 trap 18); so does the terms sentence. They are lab TaskSpecs, not product texts: shipping any of them,
    or a drafted vocabulary in the product's judge payload, would need pack-neutral wording and a decision.
21. **One record a question.** The product pools a window of records into one verdict; this cycle judges one record
    at a time, so the pooling, the routes and the gate are not measured. L001 measures them.
22. **The schema is the base task's, but the task is new to the server.** Only the warm-up, which calls each task
    once on a generated record, shows the real server accepting task D's longer prompt before a drawn record is
    sent. A server that refuses it, or a warm-up reply cut at the cap, stops the shard before any model judges, an
    infrastructure failure under rule 17 and section 6.3 (A002's B7 applies to a cut).
23. **A dealer or a place among the terms.** The drafter's refusal removes record ids, state values and the vehicle
    column's make and model words, and the floor needs ten records at three states; a name that is a national chain's
    or a word that is also a place could still pass. That is why no term is printed or committed anywhere, and the
    term set lives in the artifact alone (item 7). If a reviewer of the artifact finds such a term, the term set is
    not edited by hand: the finding is recorded, and whether the drafter's refusal should learn a new column or list
    is the next drafting rule's question.

## What it does not show

- No site-level verdict: how pooling, routing and the commit gate turn record verdicts into an alert's answer. That
  is L001's measurement, where the route-role baseline reads nothing and scores 0.808.
- No early warning. There are no outcomes here, no detection and no alert; this is reading, not signal.
- No partner data. These are public complaints written to a regulator, not a company's own records, and not another
  field or pack. A partner's drafted pack would be drafted from the partner's own export (D001's premise); this cycle
  says nothing about how a model reads with such a pack on such records.
- Not the product's behaviour for any arm. The product runs the shipped instruction, the shipped payload without
  terms and the shipped verdict rule (A001 measured it on two draws: 0.387 [0.340, 0.433] and 0.307 [0.263, 0.350]);
  every arm here is a lab reading of a declared change, measured so that a decision can be made; none is shipped by
  this cycle.
- Not whether a drafted lexical judge is itself a site verifier the loop may count: `SiteVerifier` judges with
  `lexical_judge` when no runtime is configured (`verify.py:264-301`), and the drafted judge's balanced accuracy is
  a comparator figure here, printed before any model runs and deciding nothing toward 86 ("Decisions this does not
  make").
- Not the hosted central reader of section 2.5's secondary; no key exists and none is assumed.
- Not a-0p5b or a-1p5b on any of these arms; not a-4b with reasoning on, the R003 settings, pack-v2, a larger local
  model, the rationale task or a composite (levers 6 to 8 and the parts of levers 3 and 4 not carried forward).
- Not D002's claim re-measured: D002 measured the drafter's reading in micro F1 on a per-make sample; this cycle
  measures balanced accuracy of judges on a draw with the drafted judge as a comparator; the two are not one number.
- Nothing about Tesseract, ingestion or latency as a test (latency is reported, not judged).
- Not the key's noise ceiling, nor whether any judge could reach 0.860 on these labels.

## Decisions this does not make

- **Shipping any arm is the chief scientist's decision 9** (`HANDOFF-2026-10-09.md`, decisions table), and a change
  to `judge_task`, `judge_schema`, `judge_payload` or `decide` is a product change of the same kind. This cycle
  measures declared arms through lab-side TaskSpecs and lab-side readings; the pinned `verify.py` is imported and
  never changed. The handoff's decisions table stays as it is.
- **The branch reading as primary** (M under N, G under E) is declared secondary under the standing default, as
  A001's A7 and A002's B1 read it, and asked once in A002's ask; a call received before the request push promotes
  Dr by a dated amendment. No request is pushed on a reading this file gives itself.
- **Whether a drafted lexical judge counts as the site verifier.** The pinned rule names a (model, arm) pair as P;
  the lexical judge is the product's judge when no model is configured. If the drafted lexical judge's balanced
  accuracy reaches 0.860 on this draw, that is a comparator figure, recorded, and the loop asks the founder one
  line: "NEEDS FOUNDER: the lexical judge with drafted terms is the site verifier's judge when no model is
  configured; may a lexical-judge pair be a primary in a later cycle under section 2.5, or does 86 name a model?
  Default if silent: a model." No cycle counts it until an answer arrives.
- **The term set's provenance and refusal lists** are the drafting rules' (D001 to D003) and the chief scientist's;
  this rule runs the drafter as built, with its defaults, and changes none of its data files.
- **The R003 settings** and **pack-v2** are the chief scientist's; a pack-v2 cycle would be labelled and never
  pooled toward 86 (section 2.6, lever 7).
- **Decisions 1, 2 and 7** (Tesseract placement, routing, the embedder) are untouched; the accuracy track does not
  build Tesseract in.
- **The founder's alternative readings** of 86 (section 2.7) are not adopted; the pinned reading runs by default,
  and the hour-0 ask stands.
- **The hosted central reader** needs the founder's secrets and a reviewed lab role; nothing here assumes it, and no
  hosted model is named.
- **Later cycles' questions**, left to their rules: the two smaller models on this arm; a term list cut to the first
  k terms, or sorted; a payload that carries every predicate's terms (a drafted description of the whole space) rather
  than the asked predicate's; a drafted pack drafted per make; a composite of D with either lexical judge; a
  predicate-descriptions arm; an MSHA record-level replication with the drafter demo's pack.

## The request

Two requests, each pushed alone after its code is merged (LOOP-48H section 6.4: the request push is the only thing
that starts a lab run). Their content, fixed now (the exact key names are the build's, recorded in `BUILD-A003.md`):

**The term run**, `lab/templates/terms-003.json`, copied to `lab/requests/terms-003.json`:

- top level: `provider` `llama-server`; `models` `["a-4b"]` (no block uses it; the plan holds one model-free unit
  and provisions nothing, the x1 precedent); `job_minutes` 45; `max_parallel` 1; `retention_days` 30; a `purpose`
  naming this file and the copy's name and no other digit;
- `experiments.jt`: `minutes` 10; `pack` `docs/collective/replay/vehicles/pack`; `cutoff` `2023-01-01`; `from`
  `2020-01-01`; the makes, the roles, the columns, the drafter's parameters and the language are code constants of
  `lab/jt.py` (`NHTSA_MAKES` imported from `lab.goldlabels`; the rest written out, pinned by a test against
  `D002-settings.json`'s `arms.nhtsa` and against `data/defaults.json`), not request fields, so that no request can
  draft from another window or another make set.

`lab.request` validates it (`cutoff` equal to `NHTSA_WINDOW[0]` in ISO form; `from` before `cutoff`; the pack exactly
the vehicle pack).

**The judging run**, `lab/templates/accuracy-003.json`, copied to `lab/requests/accuracy-003.json`:

- top level: `provider` `llama-server`; `models` `["a-4b"]`; `job_minutes` 330; `max_parallel` 5; `retention_days`
  30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.j4`: `minutes` 300; `labels` `{source: nhtsa, pack: docs/collective/replay/vehicles/pack, n: 150,
  seed: 4}`; `parts` 5; `seed` 4; `bootstrap_b` 10000; `bootstrap_seed` 4; `base_task` `evidence` or `negation` (by
  the adaptation rule's amendment); `primary` `{model: a-4b, arm: <base>_terms}`; `comparisons_before` 21;
- `experiments.j4.exclude`, a list of three entries in seed order: J001 (`run_id` 37996675559, `artifact`
  `lab-plan-37996675559-1`, `file` `prereg/j1/labels.jsonl`, `sha256_prefix` `bc6d092d8bca`, rebuild seed 1 with
  the same prefix); A001 (`run_id` 38114522160, `artifact` `lab-plan-38114522160-1`, `file`
  `prereg/j2/labels.jsonl`, `sha256_prefix` `623780b8a1de`, `chain_file` `prereg/j2/excluded.jsonl`,
  `chain_sha256_prefix` `9a948cbf2cd9`, rebuild seed 2 with prefix `623780b8a1de`); A002 (`run_id` 38439612256,
  `artifact` `lab-plan-38439612256-1`, `file` `prereg/j3/labels.jsonl`, `sha256_prefix` placeholder, `chain_file`
  `prereg/j3/excluded.jsonl`, `chain_sha256_prefix` placeholder, rebuild seed 3 with the labels prefix; both
  placeholders by amendment);
- `experiments.j4.terms`: `run_id` and `artifact` (`lab-plan-<run id>-<attempt>`) of the term run, `file`
  `prereg/jt/terms.json`, `sha256` the full 64 hex of the committed term set, `vocabulary_hash` P_D's full 64 hex,
  `cutoff` `2023-01-01`; all by amendment.

`lab.request` validates every field (integers in range, artifact names of the lab's shape, lower-case hex prefixes
of at least 12 characters for the exclusions and exactly 64 for the term set and the hash, files pinned per entry
(`prereg/j1/labels.jsonl`, `prereg/j2/labels.jsonl`, `prereg/j3/labels.jsonl`; the chain keys on the second and third
entries only, `prereg/j2/excluded.jsonl` and `prereg/j3/excluded.jsonl`), rebuild seeds 1, 2, 3 in order, `base_task`
one of the two, the primary arm `<base_task>_terms` for the primary and any arm of that branch's four for the
validator, the model one of the block's and a gguf model); `lab.plan --github-output` echoes the three excluded runs
and artifacts (`j2_exclude_*`, `j3_exclude_*_2`, `j4_exclude_*_3`) and the term run (`j4_terms_run_id`,
`j4_terms_artifact`); the plan job downloads all four.

## The build plan

The build starts only after this rule's review and any amendment are committed, in two builds with a lab run
between them. It changes no pinned file: `lab/j1.py` and `lab/goldlabels.py` (J001's `CODE_FILES`), `lab/j2.py` and
`lab/j2labels.py` (A001's), `lab/j3.py` and `lab/j3labels.py` (A002's, `lab/j3.py:175`), nothing under
`mycelic/collective/` (the onboard package and its data files included), `mycelic/*.py`, `connectors/openfda.py`,
`research/**`, `lab/shard.py`, the vehicle pack, any earlier `CHOICE-*.md`, `run-*.json` or `*-settings.json`.

**The order.** (1) Build 1, the term-drafting kind `jt`, on `wf/a003-terms`: build, review, fix, merge. (2) Push
`lab/requests/terms-003.json` alone: the term run. (3) The dated amendment of this file: the term run's figures
("The terms", item 10); A002's labels and chain prefixes once its plan job has printed them; the base-task branch
once CHOICE-A002's Run 1 is recorded (the branch and the term run are independent; the amendment that fixes the
branch may be a second dated amendment if A002's record comes later, but it comes before build 2). (4) Build 2, the
judging kind `j4`, on `wf/a003`: build, review, fix, merge. (5) Push `lab/requests/accuracy-003.json` alone: the
judging run, within 30 days of the term run.

- **Two new kinds, not additive options on `j3`.** `lab/j3.py`'s constants `TASKS`, `ARMS`, `LIFTS`, `LINE_KEYS` and
  `PREREG_KEYS` are read by the request validator (`lab/request.py:747`, the primary arm over `lab.j3.ARMS`), the
  unit adapter (`lab/units.py:303`), the warm-up (`lab/warmup.py:207-226`) and the aggregate (`lab/aggregate.py:1463`:
  the comparison count is `len(lab_j3.ARMS)` times the models); the summary holds its own copies
  (`lab/summary.py:187-189`). Extending them would change what a re-aggregation of A002's run computes, which is
  A002's recorded computation, and `lab/j3.py` and `lab/j3labels.py` are in A002's code hash (`lab/j3.py:175`), so
  editing either changes a pin A002's units verified. So A003 is a new kind `j4` whose own constants hold its two
  tasks, its four arms per branch, its lifts and its line keys, and which imports from `lab.j2` unchanged
  (`NEGATION_TASK`, `negation_task`, `MAPPING`, `mapped_reply`, `mapping_pin`, `pass_test`, `verdict_word`,
  `lift_reading`), from `lab.j3` unchanged (`EVIDENCE_TASK`, `evidence_task`, `evidence_schema`,
  `EVIDENCE_INSTRUCTIONS`, `FIELD_MAX_TOKENS`, `norm`, `grounded`, `grounded_reply`, `grounding_pin`, `first_key`,
  `LAST_SENTENCE`, `_with_field`'s construction), from `lab.j2labels` unchanged (`earlier_refs`, `rebuild_refs`,
  `eligible_records`, `draw`, `label_lines`, `excluded_jsonl`, `build_labels`, `nhtsa_rows`) and from `lab.j3labels`
  unchanged (`earlier_labels_refs`, `rebuild_a001_refs`, `chain_entry`); and the term run is a kind `jt` of its own,
  so that no judging kind drafts. A002 can be re-aggregated at any later commit with its report unchanged.
- **New modules.** `lab/jt.py` (the term kind: the cutoff and window constants; the roles and columns written out;
  `pooled_export(blob)`: the six makes' rows over the drafting window through `nhtsa_export.complaints` with
  `fetch_nhtsa._Every` and `REPORTER` as D002's split, as one CSV through `fetch_nhtsa.rows_csv`, parsed by
  `onboard.exports.parse_export`; `draft(export)`: `draft_export` with the roles and `load_params()`; `check`:
  `check_pack`; `term_set(draft, hand)`: T through the hand pack's value map, `terms.json`'s document and its
  sha256; `drafted_pack(hand_dir, terms, out_dir)`: P_D's files from the hand pack's bytes with `vocabulary.json`
  rewritten, loaded by `load_pack_dir`; the preregistration document with every hash; the fixed console line; the
  verify command of the unit) and `lab/j4.py` (task D's two names, the sentence, `drafted_task(base)`, the payload
  with `predicate_terms`; the arms per branch, the lifts, the line keys; the verdict line with the branch reading;
  the line check that recomputes the reading, `grounded` and the terms; the j1-shaped views; the pins (the hand
  pack's hashes, P_D's, the term set's sha256, both tasks, the reading, the arms); the preregistration document; the
  runner: two passes, D first, A001's budget timer and signals, `complete_by_task`), `lab/j4labels.py` (the three
  layouts; `earlier_labels_refs` over A002's layout and `earlier_chain_refs` over `prereg/j3/excluded.jsonl`, each
  pinned equal to the pinned function on the earlier layouts by a test; `rebuild_a002_refs`; the eight sources, the
  stop rules and the consistency counts of item 4; the union; the `excluded_runs` entries, three; the chain file of
  600 lines; the two received-date counts of item 8), `lab/j4terms.py` (the term artifact's check: the file's full
  sha256 against the request, the manifest cross-check, the document's cutoff, window, makes and hand-pack hashes
  against the code's and the repository's, P_D rebuilt and its hashes against the request's; outcomes as
  `lab.j2labels.OUTCOMES` plus `terms absent`; nothing of the artifact printed but counts and hashes);
  `tests/lab/test_lab_jt.py`, `tests/lab/test_lab_j4.py`, `tests/lab/j4_data.py` (the synthetic archive at 120
  complaints per make with a drafting-window block before the cutoff, so that four disjoint draws of 150 and a draft
  fit; three synthetic earlier artifacts built from it by the pinned code, the A002-shaped one at seed 3 with
  `prereg/j3/` files and the chain of three draws; a synthetic term artifact drafted from the synthetic pre-cutoff
  rows by the real drafter; a three-entry stand-in and a term stand-in); `lab/templates/terms-003.json`,
  `lab/templates/accuracy-003.json`; `docs/collective/A003/BUILD-A003.md`.
- **The term run's wiring (build 1).** `lab/request.py` (`EXPERIMENTS` gains `jt`; `_jt` validating `minutes`,
  `pack`, `cutoff`, `from`); `lab/plan.py` (`build_units`' `jt` branch: one model-free unit `jt`, kind `none`, as
  `x1`); `lab/prereg.py` (`_jt`: the one cached fetch, `lab.jt`'s steps, `prereg/jt/terms.json`, `prereg/jt/pack/`,
  `prereg/jt/check.json`, `prereg/jt/prereg.json`; `preregister`'s `jt_units` and manifest key `jt`; the fixed line;
  a failed check refuses the plan with `JT_FLOOR_FAILED`, a draft the drafter refuses with `JT_DRAFT_FAILED`);
  `lab/units.py` (`ADAPTERS["jt"]` running `python -m lab.jt verify`, `PREREG_EXPERIMENTS` gaining `jt`, the status
  reading); `lab/aggregate.py` (`jt_block`: the hashes, the counts, the check's result); `lab/summary.py` (the jt
  lines of the plan summary and the report, the sha256 between the markers); `lab/notes.py` (`JT_LABEL`,
  `JT_TERMS_NOTE`, `JT_FLOOR_FAILED`, `JT_DRAFT_FAILED`, every sentence free of ASCII digits:
  `tests/lab/test_lab_guards.py:153`, `test_notes_hold_no_digits`, so the cutoff is written in words there); the
  dry run (`lab/dryrun.py --jt-data DIR`, refused together with the other data flags); `docs/lab/REFERENCE.md` and
  `lab/requests/README.md` (a `jt` block); `tests/lab/test_lab_docs.py`'s pins.
- **The judging run's wiring (build 2), every function a j4 kind touches** (A002's B25 list, with the additions):
  `lab/request.py` (`EXPERIMENTS` gains `j4`; `_J4_KEYS`, `_J4_REQUIRED` with `base_task` and `terms`;
  `_j4_exclude_list` validating three entries in seed order with the three files pinned per entry and the chain
  keys on the second and third; `_j4_terms` validating the term entry; `_primary` called with the branch's arms);
  `lab/plan.py` (`build_units`' `j4` branch; `exclude_entries` returning three entries for a j4 plan;
  `plan_outputs` and `OUTPUT_KEYS` gaining `j4_exclude_run_id_3`, `j4_exclude_artifact_3`, `j4_terms_run_id` and
  `j4_terms_artifact`, empty for every other plan, the earlier pairs filled as before); `.github/workflows/mycelic-lab.yml`
  (two plan-job steps after `earlier-2` and before `prereg`: `earlier-3`, keyed on `j4_exclude_run_id_3`, into
  `${{ runner.temp }}/lab-earlier/<run id>`, and `terms`, keyed on `j4_terms_run_id`, into
  `${{ runner.temp }}/lab-terms/<run id>`, both `actions/download-artifact` at the same pinned sha, `github-token:
  ${{ github.token }}`, `continue-on-error: true`, under the workflow's read-only `actions: read`); `lab/prereg.py`
  (`_j4`: the term check through `lab.j4terms` first, then the eight sources and the stop rules through
  `lab.j4labels`, the draw, J001's questions, both lexical judges, the control, the bound, the chain file, the pins;
  `preregister`'s `j4_units`, a `--terms-plans` directory beside `--earlier-plans`, and a `--terms-stand-in` for the
  dry run; the fixed lines); `lab/units.py` (`ADAPTERS["j4"]` with the two tasks in pass order; `required_tasks`
  returning the D task; `build_argv`, `write_routing`, `harness_status` (`_j4_status`: exit 1 with D's pass finished
  is `ok` with `J4_TERMS_STOPPED`), `participation`, `harness_measurement`, `harness_verdict`, `unit_notes`,
  `public_text`, `PREREG_EXPERIMENTS`, `REPLY_PARTICIPATION`, `PASS_EXPERIMENTS`, `run_unit`'s `with_pack` list,
  `lab/units.py:1197`); `lab/warmup.py` (`warm_tasks`: a j4 branch registering D and B with the judge's generated
  payloads, D's carrying the term list of the asked predicate read from the preregistration's `terms.json` through
  `prereg`; the probe is task B's, both branches); `lab/responder.py` (handlers for the two `_terms` names, the
  shipped judge's answer behind the base's field, in the pack branch and the pack-free one); `lab/aggregate.py`
  (`j4_block`: the term pin, the two sets, the four arms with both lexical judges, the control and the bound on the
  same records, the verdict words with the hand clause, the lifts, the agreement readings, branch E's citation
  readings, the first-key shares, the token figures, `primary_result` with Dr's word; the manifest cross-checked
  against `prereg/j4/prereg.json` as A002's fix did); `lab/summary.py` (`J4_ARMS` per branch, `J4_LIFTS`; the j4
  lines of the plan summary, with eight sources, three cycles and the counts; the report's sections); `lab/notes.py`
  (`J4_LABEL`, `J4_ARMS_NOTE`, `J4_VERDICT_NOTE` with the hand clause and the drafted judge, `J4_PRIMARY_LINE` with
  both lexical figures, `J4_TERMS_NOTE`, `J4_EXCLUSION_NOTE`, `J4_STAND_IN`, `J4_TERMS_STOPPED`,
  `J4_TERMS_UNVERIFIED`, `J4_FRESHNESS_UNVERIFIED`, `J4_EXCLUSION_DISAGREES`, `J4_TOO_FEW_RECORDS`,
  `J4_SPLIT_VIOLATED`, every sentence free of ASCII digits); `lab/dryrun.py` (`--j4-data`); `lab/__init__.py`;
  `docs/lab/REFERENCE.md`, `docs/lab/README.md`, `lab/requests/README.md`, `docs/lab/INTEGRATION.md`.
- **Tests, declared.** `tests/lab/test_lab_jt.py` pins: the roles, the columns and the parameters equal to
  `D002-settings.json`'s `arms.nhtsa` and `data/defaults.json`; the cutoff equal to `NHTSA_WINDOW[0]`; the pooled
  export over the synthetic archive holds no row received on or after the cutoff and every make's rows before it;
  the draft on it through the real drafter; the check; T's 26 entries through the hand value map, the placeholder
  left out, the empty list for an unreached predicate, the twin split; P_D loading, its hashes differing from the
  hand pack's in `vocabulary_hash` and `config_hash` and equal in `detector_hash` and `fixtures_hash`; the drafted
  lexical judge over P_D confirming a synthetic narrative that holds a drafted term and refuting one that holds only
  the hand label; `terms.json`'s sha256 stable across two drafts of the same export; the fixed line without a term;
  the request block's errors; the unit's verify command; a dry run of the kind. `tests/lab/test_lab_j4.py` pins:
  task D's text equal to this file's sentence inserted before the base's last sentence, under both branches; the
  payload equal to the shipped payload plus `predicate_terms` and nothing else; the schema and cap equal to the
  base's; every `rule N` of this file naming the item whose bold title the test lists; the four arms per branch, the
  lifts, the readings as the pinned functions; the line check's every outcome; the words in A5's order with the hand
  clause (a P above the drafted judge but not the hand judge is `partial`); the three layouts' checks, each pinned
  equal to the pinned function on the earlier layouts case for case; the eight sources and the stop rules on
  synthetic artifacts (each cycle by own sources, by a chain with the others established, chains alone stopping,
  R2 and R3 `not attempted`, every consistency count stopping); the term check's every outcome (a changed byte, a
  wrong cutoff, another hash, an absent artifact); the two received-date counts; the draw over the eligible list
  less 450 refs, disjoint from the three synthetic draws, the chain file of 600 lines; `plan_outputs` on a j4, a
  j3, a j2 and another plan; the template giving 5 units in 5 shards; the runner through the fake server (two
  passes in order, D first, the pins refused before any call, a stop in each pass); the wiring (both fake handlers,
  the warm-up's two tasks with the probe task B and the terms in D's payload, the plan's six outputs, the two new
  workflow steps of the earlier steps' shape); the small and the template dry runs with every number traced and no
  ref or term anywhere. The pins of `tests/lab/test_lab_workflow.py` change with the workflow: `github.token` in
  exactly five places; the steps with `github-token` are `earlier`, `earlier-2`, `earlier-3` and `terms`; seven
  `download-artifact` steps with a `name`, three of them `own`; the order `plan`, `earlier`, `earlier-2`,
  `earlier-3`, `terms`, `prereg`; `tests/lab/test_lab_plan.py` gains the four output keys; `tests/lab/helpers.py`,
  `tests/lab/test_lab_docs.py`, `tests/lab/test_lab_sim.py` and `tests/lab/test_lab_shipped.py` gain `jt` and `j4`
  where they list the experiments and the templates.
- **The guards the build and this file obey.** No model-family name anywhere: `tests/mycelic/test_collective_guards.py`
  (`NoModelNamesTests`, `:734-761`, which scans `docs/collective` and the collective code) and
  `tests/lab/test_lab_guards.py` (`:83-91`, which scans `lab/`, `tests/lab/` and `docs/lab/`); the models are
  a-0p5b, a-1p5b and a-4b, and the deployment branch is called that. No record ref, no term, no narrative in any
  report, summary, console line, commit or document; the report guards refuse record ids. No ASCII digit in any
  `lab/notes.py` sentence. Lab Python imports only the standard library, `mycelic` and `lab`; `tools/` modules are
  loaded by path as `lab.goldlabels.nhtsa_export` loads them (`lab/goldlabels.py:66-71`). The term run makes no
  network call but the archive's download, and the judging run's plan job none but the archive's download and the
  four artifact downloads.
- **Before each merge** (section 2.3): `python -m pytest tests/lab -q -p no:cacheprovider` in the background on a
  runner with room, the onboard and guard suites, and the template dry runs: `python -m lab.dryrun --request
  lab/templates/terms-003.json --out OUT --jt-data DIR`, which must show one unit in one shard, the check passed,
  and no term in any console line or summary; `python -m lab.dryrun --request lab/templates/accuracy-003.json --out
  OUT --j4-data DIR`, which must show 5 units in 5 shards, the term stand-in verified, every one of the eight sources
  verified on the stand-in, the draw line with eligible after exclusion at least 150 and overlap 0, both lexical
  judges printed, every unit `ok` and `plumbing`, every word `incomplete`, the comparisons 25, and no ref and no
  term anywhere. In a dry run the fake answers D as it answers B, so nothing in a dry run measures the terms;
  `BUILD-A003.md` says so.

## Runs

None yet. The first entries will be two.

**The term run's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule and the
request; the archive's sha256 and size; the fixed line (the rows per make and pooled, the corpus size, the categories
passing the floor, the specific predicates drafted, the hand predicates reached of 26 and the terms per reached
predicate, the empty entries, the refusal's counts, the check's result by check, P_D's four hashes and the hand pack's,
the onboard code hash and the two data files' sha256, **the term set's sha256**); the unit's verify line; the report's
marker line with its sha256; and that no model ran and nothing was drawn. The amendment that follows it copies the
sha256, the hashes and the counts into "The terms" and "The request".

**The judging run's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule, its
amendments and the request, and the branch in force with the A002 figures that fixed it; the plan job's term line
(the term artifact verified, the sha256 and P_D's hashes equal to the committed ones, the archive's sha256 beside the
term run's); the plan job's fixed draw line (`available`, excluded 450, eligible after exclusion, overlap 0, the
outcome of each of the eight sources with any by-chain reading named and every consistency count computed, the
labels' sha256 prefix, the questions' prefix, the prereg prefix, the chain file's prefix with its line count, the
complaint file's prefix beside `a5a5de2d31f3…` and A002's; the two received-date counts of item 8; on a re-run after
a plan job printed, both plan jobs' lines); the comparators on the fresh draw with intervals: the drafted lexical
judge with sensitivity, specificity and unknown count, the hand lexical judge beside it, their paired difference, the
control and the bound; the four arms' table (balanced accuracy with interval, sensitivity, specificity, unknown
share, the paired difference to the drafted judge and to the hand judge, the bound on the same records, the pass test
with the hand clause, the verdict word); the verdict on P = (a-4b, D) in rule 13's words with its figures, and Dr's
verdict word beside it labelled "the primary under the other reading of the section 7 default"; the headline for D
against both judges with Dr's lines beside; the lifts of rule 10 with their readings; the agreement readings; the
term counts per predicate and the empty-list share; under branch E the citation readings; the first-key shares and
the token figures per task; B and Br beside the earlier cycle's arms as a between-draw difference; the parts
finished per task and `records left out` per arm; anything that stopped before a model ran; the model-call latency
per task with the runner CPU; the comparison count ("4 this cycle, 25 since the loop began"); and the public-text
sentence.
