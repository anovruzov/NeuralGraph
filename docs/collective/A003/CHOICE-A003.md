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
  branch is fixed by a dated amendment that cites CHOICE-A002's Runs entry when it exists. (As committed. A002's
  result is recorded since, in CHOICE-A002's Runs entry, Run 1; the amendment of 2026-10-14 quotes it, reads the
  branch from it under "The base task, read from A002 run 1", branch N, and fills A002's exclusion facts from it: C2,
  C3.)

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
below. By the pinned rule, a pass in this cycle can only be provisional; confirmation needs the next cycle (the one
exception, the confirmation case of "The base task and the arms", C1, is not in force: A002 run 1's P is `failure`).

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen J001's result (RESULTS run 9; CHOICE-J001, Runs), A001's result (CHOICE-A001, Runs, Run 1; RESULTS
  run 11), L001 runs 1 and 2 (RESULTS run 10), RESULTS runs 1 to 11, D001, D002 and D003 (`docs/collective/onboard/`),
  A002's rule and build note (CHOICE-A002, BUILD-A002), the loop prompt (`docs/strategy/LOOP-48H.md`) and the loop's
  status log (`docs/handoff/LOOP-48H-STATUS.md`).
- It has not seen A002's result: lab run 38439612256 is in flight; no figure of it is cited, and the base task of this
  cycle is left to the adaptation rule below, both branches written now.
- (C2) The amendment of 2026-10-14 was written after A002's result was recorded: lab run 38439612256 ended at 18:23:04
  UTC on 2026-10-13, about twenty minutes after this rule's commit at 18:02:56 UTC. Its author has read A002's whole
  Runs entry, the plan job's comparator figures included (printed while the plan job ran, 17:33:30 to 17:34:58 UTC,
  before this rule's commit, and recorded in the status log at 18:05:20 UTC, after it; the rule's text cites none of
  them, and whether the rule's author had read them before its commit is not recorded). They bear on no setting of
  this cycle: the branch is read from the committed rule's trigger, which reads the sets' completeness, P's verdict
  word and `E_minus_N` only, the last two first printed by A002's aggregate job; the exclusion facts of C3 are facts
  of which records A002 judged.
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
  before every drawn record, never from the judged record's (the next section says why that holds by construction for
  received dates, and item 8 of "The data and the draw" enforces it on the day by record id and by text, C4); the
  lever is pinned in section 2.6 of the same document, and its known limit is named in trap 2.
- **The comparators** (section 2.4), on the same records, paired, with 95% cluster-bootstrap intervals of the
  difference: the lexical judge (`lexical_judge`, `verify.py:171-198`; J001 0.670 [0.627, 0.713]; A001 0.663 [0.627,
  0.703]), the predicate-only bound (`lab/j1.py:346-361`; J001 0.580; A001 0.577; a point value fitted to the draw's
  answers, no interval) and the record-blind control (`PRIOR_RULE`, `lab/j1.py:133-134`; J001 0.527 [0.467, 0.587];
  A001 0.520 [0.463, 0.577]). This cycle's lexical comparator is **the lexical judge with the drafted terms** (the
  same pinned `lexical_judge` over a pack whose vocabulary is the drafted term set, everything else the hand pack's;
  "The terms", item 9), as lever 5 says; the hand-pack lexical judge is computed and reported beside it on the same
  records, as is the drafted term-presence judge (rule 8, C5), and the pass test must beat all three (rule 13; C5,
  C6). The record-blind control reads the question's predicate
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

This section answers the brief's item (1). Every setting here is fixed now; the figures the term run printed (counts,
hashes) are recorded by the amendment after the term run ("Amended after the term run, before build 2, 2026-10-14").

1. **The cutoff date is 2023-01-01, the first day of the pinned window.** `NHTSA_WINDOW` is `("20230101",
   "20241231")` (`lab/goldlabels.py:50`), so every eligible record of every J-cycle draw was received on or after
   2023-01-01, by the pinned eligibility code (`goldlabels.nhtsa_lines`, `:74-93`; `lab.j2labels.eligible_records`,
   `lab/j2labels.py:89-106`, pinned equal to it by A001's A4), which reads the exporter's rows for the window
   (`tools/market/nhtsa_export.py:68-113`, `complaints`: a row is kept only when its received date lies in the
   window). The drafting set is every complaint of the same six makes received **before** the cutoff, from
   2020-01-01 (D002's training window's first day, `D002-settings.json`, `arms.nhtsa.train`; the archive's name says
   it holds complaints received 2020 to 2024, `nhtsa_export.py:47`; C14) to 2022-12-31: D002's NHTSA training window
   verbatim. With the cutoff at the window's first day, "received before every drawn record" holds by construction
   for every record's own received date, and "outside the draw" holds by construction for every complaint whose rows
   all lie on one side of the cutoff; a complaint with rows on both sides, and a drawn narrative equal to a drafting
   one, are removed from the eligible list before the draw (item 8 of "The data and the draw", C4). The draw of this
   cycle is A002's draw procedure with seed 4 and three cycles excluded ("The data and the draw"), restricted by
   nothing but those removals, since the restriction "received on or after the cutoff" is the pinned window itself. A later cutoff (for example the start of 2024) would have given the drafter one more year and restricted
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
   `rows_csv`). The makes' rows are concatenated in `NHTSA_MAKES` order, and a row whose `odino` already appeared (a
   complaint filed under two makes, which `complaints` keeps once per make) is dropped, as the draw's mapping drops
   it (`map_rows`, `duplicate_record_ref`, `mycelic/collective/packs/connector.py:250-251`); the count dropped is
   printed and kept in `terms.json` (C28). One export and one drafted pack, not six (C18): per-make drafting with the
   questions split by make would be runnable (`lab.j1.lexical_verdicts`, `lab/j1.py:314-324`, called once per make
   on the questions whose record's vehicle belongs to that make, then merged), but it leaves more predicates without
   terms (D002: 30 placeholder predicates over six packs, CHOICE-D002 Run 1), the hand comparator is one pack over
   all six makes, and the pooled refusal removes the six makes' vehicle words together, which D002's per-make refusal
   did not; its count is printed. D002 drafted per make because its unit was the company that gives an export; this
   cycle's unit is the population of the draw. A declared set
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
   `draft.placeholder(<id>, ids)`: the template's prefix `unlearned-`, then the id with each `_` written `-`
   (`draft.py:627-633`; `data/template/ids.json`; C21). The settings are the package's data files, and the package is hash-pinned
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
   corpus records can be left with few terms or none; the count per predicate is reported (trap 11). T covers every
   specific predicate (C18), because lever 5 names the lexical judge "with the same drafted terms", not a subset: a
   set over eight predicates would leave the other specific predicates with the hand lexicon on the judge's side and
   no terms on the model's, and the comparator would be neither the hand judge nor a drafted one. The eight are
   CHOICE-A002 B24's predicates whose hand lexicon is not the label in lower case, as the hand pack's
   `vocabulary.json` shows: `electronic_stability_control_esc`, `engine_and_engine_cooling`, `fuel_propulsion_system`,
   `fuel_system_gasoline`, `latches_locks_linkages`, `service_brakes_hydraulic`, `visibility_wiper` and the
   non-specific `unknown_or_other`, never asked; seven of them are specific. A drafted list for a label-only predicate
   may or may not hold the label's own word (D002's label echo 33.8%); the hand judge beside it and the hand clause
   carry the label-only reading.
7. **The term-set file and its sha256.** `prereg/jt/terms.json`, canonical JSON (sorted keys, `jsonio.canonical_dumps`),
   holding: `kind` `lab_jt_terms`, `schema_version` 1; the cutoff `2023-01-01` and the drafting window; the makes;
   the complaint archive's sha256 and byte size; the export's row count, the cross-make duplicates dropped (C28), the
   earliest and latest received date (C14), the corpus size (training rows with a narrative), the categories passing
   the floor, the specific predicates drafted, the other-bucket share, `rows_without_specific`, the candidate and
   eligible term counts and P_draft's placeholder count (`draft.summarise`, `draft.py:990-1024`; C22); the drafting
   block of C4 (the sha256 of the sorted record ids, the sha256 of `prereg/jt/drafting_refs.jsonl` and of
   `prereg/jt/drafting_text.jsonl`, the `straddling` count); P_draft's four loader hashes (`config_hash`,
   `vocabulary_hash`, `detector_hash`, `fixtures_hash`, `mycelic/collective/packs/loader.py:24-30`) and each of its
   files' sha256, and P_D's four hashes (C21); the hand pack's hashes; the check's result by check; the onboard code
   hash, the two data files' sha256 and the code hash of `lab/jt.py` (C29); and `terms`: the 26 entries of T, each
   `{"predicate": <hand id>, "spelling": <the NHTSA spelling>, "drafted_id": <the drafted predicate's id or null>,
   "corpus_records": <int or null>, "terms": [...]}`, in hand-predicate id order, terms in the drafter's order. Its
   sha256 over the file's bytes is **the term set's sha256**. The term run prints it in one fixed console line
   (`jt terms: ... terms_sha256 <64 hex>`), in the plan summary and in the report between the `MYCELIC-LAB` markers
   (`lab/summary.py:106-111`, C13); it prints no term. **The term set is never committed to the repository**: it
   lives in the term run's plan artifact (retention 90 days, C15), as the labels files do; the
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
   lexicon is replaced by T's list, or by the placeholder `mycelic.collective.onboard.draft.placeholder(<hand id>,
   ids)` with the onboard template's ids (the drafter's own device, `draft.py:627-629`: `unlearned-` and the id with
   each `_` written `-`; C21) when the list is empty, because the loader needs at least one term in the primary language (`loader.py:804-805`)
   and the placeholder occurs in no real text; `unknown_or_other` keeps its hand lexicon (never asked, non-specific
   in the drafter's sense, no drafted entry). Every other file of P_D is the hand pack's byte for byte (`pack.json`,
   `codes.json`, `aliases.json`, `mapping.json`, `egress.json`, `detectors.json`, `rules.json`, `questions.json`,
   `followups.json`, `generator.json`, the fixtures): the entity type, its id format and aliases, the codes and the
   mapping are the hand pack's, so the canonicaliser, the entity mentions, the codes channel and the labels'
   reading are unchanged, and only predicate matching changes. P_D's fixtures and generator narratives are the hand
   pack's and are written with the hand lexicon; the loader checks them structurally only (`loader.py:1479-1517`);
   nothing in this cycle extracts from them, and `fixtures_hash` equal to the hand pack's is the pin that says they
   were not touched (C21). The drafter's own pack is **P_draft** (uploaded under `prereg/jt/pack/`); P_D is rebuilt
   and never uploaded (C21). P_D is built by a lab function from the hand
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
    memory, drafts (item 4) and checks (item 5) in a subprocess with its own timeout (C11), writes P_draft,
    `terms.json` and the two drafting files of C4 under `prereg/jt/`, prints the fixed line with the sha256 and the
    counts, and makes no model call and draws nothing. The unit itself
    (`python -m lab.jt verify` in its shard, model-free, kind `none`, the lab's x1 pattern) re-hashes `terms.json`
    against the preregistration's pin, rebuilds P_D from the hand pack and the file, checks its hashes against the
    pinned ones and exits; the aggregate reports the hashes and counts; the report block carries the sha256. The run
    uploads `prereg/jt/terms.json`, P_draft and the two drafting files (record ids and narrative hashes, C4) in its
    plan artifact `lab-plan-<run id>-<attempt>`, never the export or any narrative. **Then a dated amendment** of this
    file records: the term run's id and artifact, the sha256 of `terms.json`, P_D's `vocabulary_hash` and
    `config_hash`, P_draft's hashes, the archive's sha256 and size, the counts of items 7 and 11, the drafting block of
    C4, the check's result, the onboard code hash and the code hash of `lab/jt.py` (C29), the artifact's `expires_at`
    as the Actions API lists it (C15), and the step's wall time and peak memory (C11). The term set is the output of
    the first term run whose plan job writes `prereg/jt/terms.json` and whose check passes; its sha256 is the one the
    amendment records. A later term run is pushed only after that artifact has expired or has failed the judging plan
    job's check (rule 17), and every term run's id and sha256 are listed in Runs, in order, with the reason for each
    later one (C10). **Then the judging run's** plan job downloads that artifact
    (a download-artifact step `terms`, "The build plan"), reads `prereg/jt/terms.json`, refuses to run unless the
    file's bytes hash to the committed sha256 in full (64 hex, carried in the request's `terms` entry), unless its
    cutoff, window, makes and hand-pack hashes are this rule's, unless `lab/jt.py` at the judging commit hashes to the
    code hash the file records (C29), and unless P_D rebuilt from it has the committed `vocabulary_hash`
    (`J4_TERMS_UNVERIFIED`, a stop before any draw and any model, "stopped before any model ran";
    the loop asks); it never drafts, whatever the archive of its day holds (trap 7). An expired or absent term
    artifact is the same stop; a new term run is then a new amendment with the new sha256, recorded as such.
    **Recorded** (the amendment after the term run, T1): the term set is term run 1's, lab run 38612800578, artifact
    `lab-plan-38612800578-1` (artifact id 11918947585, expiring 2027-01-12T18:09:48Z); its sha256 is `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`;
    P_D's `vocabulary_hash` is `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579` and its `config_hash`
    `6219eb0993b5668ecea91cc0811a6e446971d62001fe85e54662dc0a2b56698a`.
11. **What the term run reads and prints.** It reads the archive, the hand pack and the onboard package's data; it
    reads no earlier run's artifact and no record received on or after the cutoff. It prints: the archive's sha256
    and size; the row counts per make and pooled; the corpus size; the categories passing the floor and the
    specific predicates; the number of hand predicates reached (of 26), the terms per reached predicate (min, median,
    max, total) and the number left empty; the refusal's counts (A14's shape: refused categories, refused terms,
    assignable, within the cap); `rows_without_specific`, the candidate and eligible term counts and P_draft's
    placeholder count (C22); the cross-make duplicates dropped (C28); the earliest and latest received date of the
    pooled export, dates and never refs (C14); the drafting export's row count, the sha256 of its sorted record ids
    and the `straddling` count (C4); the check's result by check; P_draft's and P_D's hashes (C21); the term set's
    sha256; the code hashes (C29); the step's wall time and peak resident memory (C11). Never a term, a category spelling, a narrative, a record id, a state or a vehicle. A category spelling
    is public and D002's report printed them; this cycle's console does not, because nothing in the term set is
    needed in a document before the run, and the artifact holds everything.

## The base task and the arms: the pre-registered adaptation rule

This section answers the brief's item (2). It is written now with both branches complete; the branch is fixed by a
dated amendment, before the judging build, from CHOICE-A002's Runs entry for A002's result run, cited as written
there (C2). A002's result run is the first A002 lab run in which a model judged a drawn record (LOOP-48H section 6.3:
a stop before any model call is not a result, and the same request is re-pushed; CHOICE-A002 rule 16 and B13: when a
plan job printed and the run then failed before any model judged, the re-run's draw is the result). The term run does
not depend on the branch and may happen before A002's result is recorded; its request is pushed only after A002's lab
run has ended and no other lab run is in flight (C31). (The reading of this rule on A002 run 1 is recorded under "The
base task, read from A002 run 1" of the amendment of 2026-10-14: branch N. It is a reading of the rule below, not a
change of it.)

- **Branch E holds when** CHOICE-A002's Runs entry for A002's result run records task E's set complete (as
  CHOICE-A002 rule 12 reads "complete": `measurement` true, 5 of 5 parts, `records_not_judged` 0, the dropped share at
  most 0.01; in the arms table, P's row "Parts finished" 5 of 5 and "Records left out" 0) **and either** the arms
  table prints `pass_provisional` as the verdict word of P = (a-4b, E `evidence`) (the confirmation case below),
  **or** task N's set is complete too (row N, the same cells) and the lifts table prints the lift `E_minus_N`
  (CHOICE-A002 rule 10: E's balanced accuracy minus N's, paired by record) with a 95% lower end above 0 (C2). Then the
  base task of this cycle is A002's task E, `judge_record_evidence` (`lab/j3.py:93, 119, 198-204, 214-215`: schema S_E with the
  string `citation` first, the instructions `EVIDENCE_INSTRUCTIONS`, 512 output tokens), imported unchanged, with
  its grounded reading G (`lab/j3.py:264-293`, `grounded`, `grounded_reply`; `GROUNDING_MIN_CHARS` 4) as the declared
  secondary reading.
- **Branch N holds in every other recorded case** (C2): task E's set incomplete; or P not `pass_provisional` with
  task N's set incomplete (a lift over an incomplete set is not read, CHOICE-A002 rule 14), with `E_minus_N` not
  printed, or with its lower end at or below 0. A run stopped before any model ran is never a trigger: while A002 has
  no result run, build 2 does not start; if A002 were abandoned without a result run by a recorded decision, this
  cycle would need a new choice file, since its three-entry exclusion presumes an A002 draw a model judged. The
  amendment that reads the branch quotes the cells it read as the Runs entry prints them and says whether A002's plan
  job's comparator figures were read. Then the base task is A001's task N,
  `judge_record_negation` (`lab/j2.py:80-93`, `NEGATION_INSTRUCTIONS`; `:134-137`, the shipped schema and
  `JUDGE_MAX_TOKENS` 256), imported unchanged, with the unclear-to-no mapping M (`lab/j2.py:101`, `MAPPING`;
  `:162-169`, `mapped_reply`) as the declared secondary reading, as A001 measured it (arm C read as answered, 0.577;
  arm D through the mapping, 0.653).
- **Why these two and no third.** N is the best measured instruction family (A001 run 1: N raised a-4b from 0.307 to
  0.577 as answered (C − A 0.270 [0.217, 0.327]) and left it level under the mapping (0.667 to 0.653, D − B -0.013
  [-0.063, 0.037], not told apart; CHOICE-A001 Run 1), the two mapped arms level with the lexical judge; C24); E
  is N with the citation field, A002's primary, built on N for the reason CHOICE-A002 gives ("stacking a new field
  on an untested instruction would make its lift unreadable"). The rationale task R and the composite arms are not
  bases: a composite is the lexical judge by another name once the terms are in the payload (trap 6), and R's
  direction is unmeasured. The condition on `E_minus_N` is the lift that says whether the field moved the answers;
  without it, E is N with a longer reply, and N is the cheaper base. Ties fail the condition (A002 B17: equality
  fails every strict clause).
- **Branch E, confirmation case** (C1). When A002's result run records P = (a-4b, `evidence`) with the verdict word
  `pass_provisional`, section 2.5 requires that pair, named as the primary before this cycle's run, to pass again on
  this cycle's fresh draw. Then **P = (a-4b, `evidence`)** (arm B under branch E, the answers as answered), D
  (`evidence_terms`) is the headline secondary, the two tasks, the four arms and the 4 comparisons are unchanged, the
  pass order puts P's set first (task B, then task D, for rule 15's reason), and P's pass test is rule 13 as written
  for this cycle (the drafted lexical judge in the second clause, the hand clause and the term-presence clause), at
  least as strict as the test A002's P would have passed; D's word is read by the same rule beside it. Success in the
  sense of section 2.5 is then reachable in this cycle for (a-4b, `evidence`) alone and only by this confirmation; a
  pass by D stays provisional. The amendment that records the branch names which primary is in force, and the
  request's `primary` arm follows it (`evidence` in the confirmation case, `<base>_terms` otherwise). (Read on A002 run
  1 under "The base task, read from A002 run 1": its P is `failure`, so this case is not in force.)
- **Under either branch the arms are the same four, on a-4b alone** (rule 5): the base task with the drafted-terms
  payload, read as answered (**D**, the primary arm); D under the branch's declared reading (**Dr**: G under branch E,
  M under branch N; the headline secondary); the base task without the terms, read as answered (**B**, the control:
  the same task the earlier cycle measured, replicated on this draw); B under the reading (**Br**). Model calls are
  made for the two tasks, D's and B's; Dr and Br are readings of the same replies. **P = (a-4b, D)**, except in the
  confirmation case above, where P = (a-4b, `evidence`) (C1).
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
chain file, A002's rebuild, the stop rules over three cycles, the split's removals and tripwires of item 8, C4) lives
in a new module `lab/j4labels.py` that generalises
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
3. **A002: B1 (the artifact), R3 (the rebuild) and K3 (the chain).** The entry names A002's result run (C2, C3); from
   A002 run 1, lab run 38439612256: run 38439612256, attempt 1, artifact `lab-plan-38439612256-1` (artifact id
   11833749961; retention 30 days, `lab/requests/accuracy-002.json`, `retention_days` 30; expiring
   2026-11-12T17:34:56Z as the Actions API listed it on 2026-10-14). The plan job downloads that artifact
   into `${{ runner.temp }}/lab-earlier/38439612256` by a third step `earlier-3` of the
   same shape, keyed on `steps.plan.outputs.j4_exclude_run_id_3` and `j4_exclude_artifact_3` (filled from the third
   entry of a j4 plan; "The build plan"). `lab.j4labels.earlier_labels_refs(directory, entry)`, A002's check
   generalised by one more layout row (`prereg/j3/labels.jsonl`; `prereg/j3/labels.json` of kind `lab_j2_labels`,
   the kind A002's B22 kept, saying `source` `nhtsa`, `n` 150, `seed` 3 with `n` equal to the refs found;
   `prereg/j3/questions.jsonl`'s refs equal to the labels' and the file hashing to A002's questions prefix
   `84486fa38568`, from A002 run 1, lab run 38439612256, held as the constant `A002_QUESTIONS_SHA256_PREFIX` as A002's
   code holds A001's, `lab/j3labels.py:56` (C3); every file cross-checked against the artifact's manifest
   `prereg/prereg.json`; outcomes `lab.j2labels.OUTCOMES`; every read inside `try`/`except`; nothing from the
   artifact printed, written or uploaded but the refs on the runner), gives **B1**: the file must hash to A002's
   labels sha256 prefix `0a64a62d94a7`, from A002 run 1, lab run 38439612256 (C3). `lab.j4labels.earlier_chain_refs(directory,
   entry)`, `lab.j2labels._check_earlier`'s chain branch generalised to `prereg/j3/excluded.jsonl`
   (`lab/j2labels.py:152-169`: the prefix, the manifest, one `{"record_ref"}` line per ref), gives **K3**, 450 refs
   expected (CHOICE-A002 item 7), against the chain file's prefix `65d51f0fca51` with 450 lines, from A002 run 1, lab
   run 38439612256 (C3). `lab.j4labels.rebuild_a002_refs(fetch, J001_set | A001_set, "0a64a62d94a7", seed=3)`, `lab.j2labels.draw(eligible, J001_set | A001_set, 150, 3)` over
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
     is not recoverable; A002's "Declined"). Two chains may each establish one cycle in the same plan job when each
     cycle a chain's reading draws on is established without that same chain (for example J001 by K2 less A001's own
     set, then A002 by K3 less the union of J001's and A001's sets); the by-chain reading is printed per cycle with
     the chain it used (C20). R2 is attempted whenever J001's set is established; R3 whenever J001's
     and A001's are, by whichever source.
   - **The consistency counts**, each printed as a count and stopping above 0: J1 and R1 equal when both verified;
     A1 and R2 equal; B1 and R3 equal; every verified source of J001 and A001 inside K2 when K2 verified; every
     verified source of all three cycles, K2 included, inside K3 when K3 verified; K2 equal to the union of J001's
     and A001's sets when both are established by their own sources; K3 equal to the union of all three sets when all
     three are established by their own sources.
   - **The excluded set is the union of every verified source's refs** (J1, R1, A1, R2, K2, B1, R3, K3): 450 refs
     expected, the count printed; the split's removals of item 8 join it before the draw, counted apart (C4).
   - **The plan job stops** before any draw and any model, and the loop asks, when any cycle's set is not established
     or when any count is above 0.
   - One mechanism verifying per cycle suffices (A001's A2, A002's item 5, the founder ask B6 posted): the record
     ref is the complaint file's own id column (`mapping.json`, `record_ref`), the same in every version of the
     file. The complaint file's sha256 is printed beside A001's `a5a5de2d31f3…` (CHOICE-A001 Run 1) and A002's
     `216ecb36f9f5…` (from A002 run 1, lab run 38439612256; C3).
5. **The draw.** `lab.j2labels.eligible_records` builds the eligible list exactly as `lab.goldlabels.nhtsa_lines`
   builds it (the makes and the window of `goldlabels.py:49-50` verbatim; a narrative, one vehicle, at least one
   specific component code; codes removed; sorted by `record_ref`); `lab.j2labels.draw(eligible, excluded, 150, 4)`
   removes the excluded refs (the 450 earlier refs and the split's removals of item 8, C4) and samples
   `random.Random("nhtsa:4").sample` of 150, sorted by `record_ref`, in label-file lines of `goldlabels`' shape;
   `lab.j2labels.build_labels` unchanged writes the labels and their record (kind `lab_j2_labels`;
   `exclusion.mechanisms` J001's pair; `exclusion.excluded_runs` one entry per cycle in seed order with the
   artifact's, the rebuild's and the chain's outcomes and counts, A002's B22 shape with a third entry, and an entry of
   kind `split` with the two counts of item 8, counts only (C4); the sources, the cycles and the consistency counts
   beside them), the complaint file's sha256, `available`, the excluded count, the eligible count after exclusion (on
   A001's day's file, 58,319 less 450 would be 57,869, less the split's removals; the day's file decides) and the
   overlap (0, else the plan job stops).
6. **The chain, carried forward.** The plan job writes `prereg/j4/excluded.jsonl`: one `{"record_ref"}` line per
   ref, sorted, refs only, the union of the excluded refs (the 450 earlier refs and the split's removals, C4) and this
   draw's 150 (600 lines plus the split's removals expected), and prints its sha256 prefix and line count. The next
   cycle names this run's artifact, this file and that prefix. Every later CHOICE lists every earlier run id and
   labels sha256 prefix; for this cycle the list is: J001, run 37996675559, labels `bc6d092d8bca`, seed 1; A001, run
   38114522160, labels `623780b8a1de`, seed 2, chain `9a948cbf2cd9`; A002, run 38439612256, labels `0a64a62d94a7`,
   seed 3, chain `65d51f0fca51` (from A002 run 1, lab run 38439612256; C3).
7. **What is printed:** one fixed line with `available`, the excluded count (450 expected, and the split's removals
   by record id and by text beside it, C4), the eligible count after exclusion, the overlap (0 expected), the outcome
   of each of the eight sources with any by-chain reading named and every consistency count computed, the new labels'
   sha256 prefix, the questions' prefix, the chain file's prefix and line count, the complaint file's prefix; then the
   split's line (item 8: the drafting set's row count and the sha256 of its sorted record ids, beside the term run's;
   the two tripwire counts, C4); then the term check's line (the term set's sha256 verified against the request, P_D's
   hashes, `lab/jt.py`'s code hash, C29); then the comparators' figures (rule 8), the three lexical judges first (the
   drafted lexical judge, the hand-pack lexical judge and the drafted term-presence judge, C5), before any shard
   starts; those figures are never a reason to redraw (LOOP-48H section 6.3). Never a ref, never a term. If fewer
   than 150 eligible unexcluded records remain, the plan job stops, and no further fresh draw is pre-registered. On
   a re-run after a plan job printed (rule 17), both plan jobs' lines are recorded.
8. **The received-date split, enforced on the day** (C4). Lever 5's text requires terms drafted only from records
   outside the draw and received before every drawn record. The cutoff gives the second by construction for every
   record's own received date; record identity and text are enforced here, in the plan job, from its own download,
   computed in memory, nothing of it written but the refs removed.
   - **The drafting set, rebuilt.** `nhtsa_export.complaints` of the six makes over 20200101 to 20221231 with
     `fetch_nhtsa._Every`, concatenated and deduplicated as the term run built it ("The terms", item 3). Its row count
     and the sha256 of its sorted record ids (the bytes `lab.j2labels.excluded_jsonl` writes for them) are printed
     beside the term run's (`terms.json`). When the day's archive sha256 equals the term run's, both figures must
     equal the term run's, else the plan job stops with `J4_SPLIT_VIOLATED` before any draw; when the archives
     differ, both are printed and the two drafting sets are used together below.
   - **Removed by record id, before the draw.** Every eligible record whose `record_ref` is a record id of the term
     run's drafting set (`prereg/jt/drafting_refs.jsonl` in the term artifact, verified against `terms.json`) or of
     the rebuilt one: `split_excluded_by_ref`. This removes a complaint whose rows lie on both sides of the cutoff.
   - **Removed by text, before the draw.** Every other eligible record whose non-empty folded narrative
     (`packs.canonical.folded`) has its sha256 in the term run's `prereg/jt/drafting_text.jsonl` or equals a folded
     narrative of the rebuilt set: `split_excluded_by_text`.
   - The removed refs join the 450 earlier refs in the excluded set handed to `lab.j2labels.build_labels`, unchanged,
     so they are counted in `exclusion.excluded`, are in the chain file (item 6) and are excluded by later cycles too;
     the two counts are printed in the fixed line, carried in the labels record (an `excluded_runs` entry of kind
     `split`, counts only) and in Runs, never a ref.
   - **Tripwires after the draw**, each stopping the plan job with `J4_SPLIT_VIOLATED` before any model when above 0:
     the number of drawn records whose received date lies before the cutoff (0 by the pinned window: a check against
     a change of the eligibility code or of the file); the number of drawn records whose record id or folded
     narrative is in either drafting set (0 by the two removals: a check of the removal itself).
   The archive sha256 of the term run's day and of this day are printed and compared (trap 7).

## The rule

1. **Records: 150 fresh NHTSA complaints, labels seed 4.** `lab.j4labels` over `lab.j2labels` and `lab.j3labels`,
   source `nhtsa`, the pack `docs/collective/replay/vehicles/pack`, the makes, the window and the eligibility rule of
   `lab.goldlabels` verbatim, J001's, A001's and A002's 450 records excluded as "The data and the draw" says, and the
   records the drafting set holds by record id or by text removed before the draw (item 8 there, C4),
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
     record."), as A002 inserted E's and R's sentences: built as `base.rpartition(" Judge only this record.")[0] + " "
     + <the terms sentence> + " Judge only this record."`, the base being `NEGATION_INSTRUCTIONS` under branch N (in
     force) or `EVIDENCE_INSTRUCTIONS` under branch E, refused unless the base ends with that sentence, since
     `lab.j3._with_field` splits `NEGATION_INSTRUCTIONS` only (`lab/j3.py:112-116`); a test pins both (C32); the
     payload B's with
     `question.predicate_terms` added ("The terms", item 8). Nothing else differs between D and B: one key in the
     payload and one sentence in the task text, so `D_minus_B` reads the lever and nothing else (compare A002's
     B18, where a lift carried a schema and a cap with the field).
   - **Codes never reach the judge in any task.** The payload's codes list is empty (the labels hide them; the runner
     refuses a record that holds any). D's and B's texts name no code, category or filed label. `predicate_terms`
     holds words of other records' narratives, drafted before the cutoff; it holds no code and nothing of the judged
     record. A term may equal a word of a component's name, the asked predicate's own label among them, which the
     shipped payload already carries as `predicate_label` (`verify.py:153-159`), so it tells the model nothing about
     the key that the question did not; it is never a record id, a site value or a vehicle word of the drafting
     export (the refusal, CHOICE-D001 A2 and A6) (C9).
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
   **The primary pair P = (a-4b, D)**; in the confirmation case of "The base task and the arms", P = (a-4b, B) under
   branch E, that is (a-4b, `evidence`), and D is the headline secondary (C1; not in force, A002 run 1's P being
   `failure`). Every other arm is a secondary, Dr the headline secondary, each reported with
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
   metric, what the drafted terms did to word matching. **And the drafted term-presence judge** (C5;
   `prereg/j4/presence_drafted.jsonl`, computed in the plan job on the same payloads): for each question,
   `mentions_entity` as the drafted lexical judge answers that question, and `describes_predicate` yes when any term
   of T[predicate] occurs word-bounded in a folded sentence of the payload's `record.text`
   (`mycelic.collective.onboard.check.term_presence`, `check.py:167-195`, over `packs.canonical.fold_phrase` and
   `split_sentences`: no negation cue, no pairing), no otherwise (an empty list matches nothing); its verdict by the
   pinned `decide`; scored by `lab.j1.score`, paired with every arm by `lab.j1.paired` and with each lexical judge,
   reported beside the two lexical judges with its sensitivity, specificity and unknown count; a comparator, not an
   arm, not counted. The lexical judge applies the pack's negation cues within three tokens and leaves out a
   predicate the text states only as negated (`extract.py:17-23`; `pair`, `:550-554`); the term-presence judge is the
   same terms read without that handling.
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
    (four; a clause of rule 13's added clauses, C6); each arm minus the drafted term-presence judge (four; a clause of
    rule 13's added clauses, C5); the lifts: `D_minus_B` (the lever: the terms in the payload, answers as answered; P
    over the control), `Dr_minus_Br` (the lever under the reading), `Dr_minus_D` (the reading under the terms),
    `Br_minus_B` (the reading under the base: A001's D − C or A002's Eg − E, replicated).
11. **The primary pair, the headline arm, and the exploratory rest.**
    - **P = (a-4b, D)** (in the confirmation case, (a-4b, `evidence`), C1; not in force). The 86 verdict of rule 13
      is read on P alone. The aggregate computes the verdict word for
      every arm, so the Runs entry prints Dr's word beside P's, labelled "the primary under the other reading of the
      section 7 default" (A002's B1 form).
    - **How P meets "chosen from earlier recorded results before the run."** The model is the only one any lever
      moved (above). The arm is lever 5, the next in the pinned order, on the best measured instruction family (N or
      its measured extension E, by the adaptation rule), read by the shipped `decide` on the answers as answered,
      which is what the standing default allows as primary. No (model, arm) pair measured on a fresh draw has met a
      pass test (A001: every word `failure`; A002: unknown when this rule was committed, and every word `failure` in
      A002 run 1, CHOICE-A002 Run 1; a pass there makes (a-4b, `evidence`) this cycle's primary for confirmation, the
      confirmation case, C1), so none is promoted.
    - **The headline arm is D, P's arm**: its balanced accuracy interval is read against the drafted lexical judge's
      balanced accuracy on the same records as J001's rule 7 reads it: **better** only when the interval's lower end
      is above it, **worse** only when its upper end is below it, otherwise **not told apart**; the same words
      against the hand lexical judge and the drafted term-presence judge beside it (C5). Dr's lines are reported beside D's in the same words, labelled the
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
    (the two lexical judges, the term-presence judge of C5, the control, the bound) are not arms and are not counted,
    as A002's ablations were not.
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

    **The added clauses: lever 5's, and two this rule adds** (C6). Lever 5's: for this cycle "the lexical judge" in
    the pass test's second clause, in Partial and in Failure is the lexical judge with the drafted terms (rule 8), as
    section 2.6 item 5 says for a lever-5 cycle. **This rule's, stricter than lever 5 and declared before any run:**
    the pass test additionally requires the paired difference to the hand-pack lexical judge to have its 95% lower end
    above 0 (the hand clause), so that no reading of lever 5 lowers the floor A001 and A002 stood on: a drafted judge
    that scores below the hand judge on this draw would otherwise be an easier bar; **and** the paired difference to
    the drafted term-presence judge of rule 8 to have its 95% lower end above 0 (the term-presence clause, C5): the
    pass test beats the terms read with and without the lexical judge's negation handling. `pass_provisional` needs
    the four clauses of the block with the drafted judge in the second, the hand clause and the term-presence clause.
    A P that meets the four clauses but not an added clause is `partial`, reported with every lexical figure.
    `partial` by its second route (BA below 0.860 but the lexical judge beaten with the lower end above 0) requires
    the paired lower end above 0 against all three lexical judges; a P with BA below 0.860 that beats the drafted
    judge but not each of the other two is `failure`, reported plainly with every lexical figure: "P scored BA b [lo,
    hi]; gap to 0.860 is g; drafted lexical l_d [lo, hi]; hand lexical l_h [lo, hi]; term presence l_p [lo, hi];
    bound p." Each added clause is one more interval clause, so it can only make `pass_provisional` and `partial`
    rarer, never commoner. The aggregate also prints P's verdict word under lever 5's text alone (the block's four
    clauses with the drafted judge, `lab.j2.verdict_word` unchanged), labelled "the word under lever 5's text alone",
    beside the word of this rule, as Dr's word is printed beside P's. The word is computed by a new function
    `lab.j4.verdict_word` (C19), shaped as `lab.j3.composite_verdict_word` (`lab/j3.py:477-502`): the word under lever
    5's text alone is `lab.j2.verdict_word` over the scores, the drafted-judge difference and the bound, unchanged;
    then `incomplete` when that word is `incomplete`, or when the hand-judge or the term-presence difference is not a
    number; `pass_provisional` only when that word is `pass_provisional` and both added clauses hold; `partial` when
    that word is `pass_provisional` and an added clause fails, when that word is `partial` with BA at or above 0.860,
    or when that word is `partial` with BA below 0.860 and both added clauses hold; else `failure`. `lab.j4.pass_test`
    is `lab.j2.pass_test` over the drafted-judge difference with the clauses `beats_hand_lexical` and
    `beats_term_presence` added, `passed` only when all six hold. For this cycle, read as A001's A5 and A6 and A002's
    rule 12 read it: "complete" is read per task set (every part of the set finished for that task, `measurement`
    true, `records_not_judged` 0, the dropped share at most 0.01); P's set is task D's, and "the same draw" is the 150
    records task D's set scored. The aggregate evaluates the verdict words in the order **`incomplete`,
    `pass_provisional`, `partial`, `failure`**, the first whose condition holds, with the figures behind every clause
    printed beside it; a missing figure of any of the three lexical judges, the bound or the interval makes the word
    `incomplete`. Equality fails every strict clause
    (A002's B17; `lab.j2.pass_test` compares with `>`). Where the headline (the arm's interval against a judge's
    point) and the paired clause disagree, the word follows the paired clause. Success in the sense of the rule
    cannot be reached in this cycle, except for (a-4b, `evidence`) in the confirmation case (C1), which is not in
    force.
14. **Reported beside the verdict, deciding nothing:** for every arm: sensitivity, specificity, accuracy and the
    unknown share with intervals; the count of each answer pair, for positives and for negatives, as answered and
    after the reading; the verdict counts; failures by kind and per task; per predicate, how often each judge
    confirmed it, for positives and for negatives (`by_predicate`); the three lexical judges (C5), the control and
    the bound on the same records, with the drafted judge's and the term-presence judge's sensitivity, specificity and
    unknown count and their paired differences to the hand judge and to each other; per predicate, the number of
    drafted terms T carried (0 to 50) and, for D's lines, the count and share of questions whose payload carried an
    empty list; the agreement of D's verdicts with the drafted lexical judge's and with the term-presence judge's,
    per kind (the share of questions on which the two verdicts are equal), and D's confirm rate where each of those
    judges confirms and where it refutes, per kind (the readings of trap 6; C5); under branch E, for each of D and B:
    the grounded share of `yes` answers on positives and on negatives, the share of empty citations, the median and
    95th percentile of citation length after `norm`, the share of grounded citations that equal, after `norm`, one of
    the asked predicate's drafted terms, and the share that contain one as a whole word (`packs.canonical.find_bounded`;
    trap 4; C16); the share of replies whose first
    key is the new field (branch E) per task; the output tokens' median and 95th percentile and the replies cut at
    the cap per task (from the ledgers); seconds per judge call per task (median and 95th percentile) with the
    runner CPU model, a runner number, not site hardware; and B's and Br's figures beside the earlier cycle's arms
    (A001 run 1's C and D under branch N; A002 run 1's E and Eg under branch E), labelled a between-draw difference.
15. **Units and limits** (sized in "Why the units are this size"):
    - one unit per part: **5 units**; a unit judges its part's 60 questions twice, in two passes, in this order:
      **task D first** (P's set), then task B (the control last: a late stop loses the control, which decides nothing
      toward 86, before it loses the lever's reading); in the confirmation case, where P's set is task B's, task B
      first for the same reason (C1; not in force); 120 calls a unit;
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
    pin. The plan artifact holds the labels, questions, the three lexical files (C5), the control, the chain file and a copy of
    `terms.json` with the term run's artifact name and sha256 (public complaint text, refs and public vocabulary; the
    artifact, never the repository). Reports, step summaries, console lines and commits hold counts, scores and
    hashes only: no narrative, no citation, no term, no record ref, no VIN, no city, no dealer, no operator name.
    The warm-up calls the two tasks once on a generated record about no drawn record (the judge's generated
    payloads, `lab/warmup.py:131-143`, `judge_example`), task D's typical payload carrying T's list for the generated
    record's asked predicate (the pack's first sorted predicate, `air_bags`), and its worst-case payload (the longest
    generated narrative repeated to the cap, `_worst_text`, `:126-128`) carrying the longest list in T by characters
    (its term count printed, never a term), so that the slot check covers the longest D prompt the run can send
    (C12); the probe is the first fitting task's in sorted
    (task name, pack) order (`lab/warmup.py:417`, `fitting[0]`), which is task B's under either branch
    (`judge_record_evidence` sorts before `judge_record_evidence_terms`; `judge_record_negation` before
    `judge_record_negation_terms`), so `repeat_identical` is recorded for the control's task, not P's, and the D
    pass, run first, starts on a cold prefix (A002's B4 inverted); latency is reported per task and decides nothing.
17. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may run
    again unchanged, with the same seeds and n (LOOP-48H section 6.3); a term artifact that has expired or does not
    verify is such a failure (a stop before any model ran), and the re-run needs a new term run and a new amendment
    with the new sha256 (the terms are the same rule's output on a later archive day; both sha256 values are
    recorded). Those are the only two causes of a second term run; the term run's printed counts are none (C10). A
    term run that does not fit its step's or its plan job's time, or its runner's memory, is an infrastructure failure
    of the same kind, remedied by a reviewed workflow change and never by a drafter parameter (C11). A set left
    incomplete gets no verdict from this run; judging it again is a new choice file. Any
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
| Cutoff and drafting set | cutoff 2023-01-01 (`NHTSA_WINDOW[0]`); the six makes' complaints received 2020-01-01 to 2022-12-31, one pooled export of D002's seven columns, every row received before the cutoff, a complaint under two makes kept once (C28); the drafting set's record ids and narratives removed from the eligible list before the draw, with tripwires after it (C4) |
| The drafter | `mycelic.collective.onboard.draft.draft_export`, D002's NHTSA roles (`vehicle` and `reporter` forbidden), `data/defaults.json` (N 10, S 3, R 50, K 50, df 10, p 0.6, 3 letters, 64 characters, 8-gram), `lang/en.json`; the check `check_pack`; code hash over the onboard package and the three download modules, recorded by amendment |
| The term set T | one entry per hand specific predicate (26), the drafted predicate's `en` lexicon through the hand value map's spelling, placeholders left out, empty when unreached; `prereg/jt/terms.json`, from the first term run whose check passes (C10): term run 1, lab run 38612800578, sha256 `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc` (T1) |
| The drafted packs | P_draft, the drafter's own pack, uploaded under `prereg/jt/pack/`; P_D, the hand pack with each specific predicate's `en` lexicon replaced by T's list or `draft.placeholder(<id>, ids)`, rebuilt and never uploaded (C21); P_D's `vocabulary_hash` `c0260f0edab4…` and `config_hash` `6219eb0993b5…`, in full under T2 |
| Excluded | J001's 150: run 37996675559, artifact `lab-plan-37996675559-1`, `prereg/j1/labels.jsonl` `bc6d092d8bca…`, rebuild seed 1; A001's 150: run 38114522160, `lab-plan-38114522160-1`, `prereg/j2/labels.jsonl` `623780b8a1de…`, chain `prereg/j2/excluded.jsonl` `9a948cbf2cd9…`, rebuild seed 2 over the eligible list less J001's set; A002's 150, from A002 run 1, lab run 38439612256 (C3): run 38439612256, attempt 1, `lab-plan-38439612256-1` (artifact id 11833749961, expiring 2026-11-12T17:34:56Z), `prereg/j3/labels.jsonl` `0a64a62d94a7…`, `prereg/j3/questions.jsonl` `84486fa38568…`, chain `prereg/j3/excluded.jsonl` `65d51f0fca51…` (450 lines), rebuild seed 3 over the eligible list less J001's and A001's sets; 450 refs expected; eight sources, the stop rules of item 4; and the split's removals by record id and by text (C4) |
| Labels | n 150, seed 4 (`random.Random("nhtsa:4")` over the eligible records less the 450 excluded and the split's removals, C4), codes removed |
| Questions | `lab.j1.build_questions`, seed 4, parts 5; `NEGATIVE_DRAW`, `EXCLUDED`, `TWINS` unchanged |
| Base task | branch E: `judge_record_evidence` (`lab.j3`, 512 tokens, S_E, reading G); branch N: `judge_record_negation` (`lab.j2`, 256 tokens, the shipped schema, reading M); the branch by the adaptation rule from CHOICE-A002's Runs entry for A002's result run, read from named cells (C2); read on A002 run 1 under "The base task, read from A002 run 1": branch N, `judge_record_negation` |
| Task D | the base task's name with `_terms`; the base's schema and cap; the base's instructions with the terms sentence before the last sentence, built by one named construction (C32); the payload with `question.predicate_terms`; pinned by sha256 |
| Arms | D `<base>_terms`, Dr `<base>_terms_<reading>`, B `<base>`, Br `<base>_<reading>`; pass order D then B (B then D in the confirmation case, C1) |
| Primary pair | P = (a-4b, D); (a-4b, `evidence`) in the confirmation case (C1), the amendment that records the branch naming which is in force; Dr's verdict word printed beside P's, labelled the primary under the other reading of the section 7 default; P's word under lever 5's text alone printed beside (C6) |
| Headline arm | D, P's arm (B's, `evidence`, in the confirmation case, C1), against the drafted lexical judge, the hand judge and the term-presence judge beside; Dr the headline secondary; 4 (model, arm, cycle) comparisons this cycle, 25 since the loop began; no multiplicity correction |
| Comparators | the drafted lexical judge (the pass test's), the hand-pack lexical judge (beside, and a clause of both `pass_provisional` and `partial`, C6), the drafted term-presence judge (beside, and a clause of both words, C5), the record-blind control (`PRIOR_TOP`), the predicate-only bound; all in the plan job |
| Bootstrap | over records, B 10,000, `bootstrap_seed` 4 (`j1:4`), 95% percentile; paired differences with the same draws |
| Codes in the payload | empty in every task; a record with codes is refused; the only key added to the payload is `predicate_terms`, on task D |
| Endpoint | the lab's pinned model server on loopback at `site:lab`, data label `public`; HTTP deadline 600 s, 1 retry; no escalation |
| Units | 5 (1 model × 5 parts), 120 calls each, 300 minutes, one per shard; `job_minutes` 330; `max_parallel` 5 |
| Withhold | `WITHHOLD_SHARE` 0.01 per task set |
| Requests | the term run: `lab/templates/terms-003.json`, copied to `lab/requests/terms-003.json`, kind `jt`; the judging run: `lab/templates/accuracy-003.json`, copied to `lab/requests/accuracy-003.json`, kind `j4`; `retention_days` 90 for the term run (C15) and 30 for the judging run; `provider` `llama-server`; `comparisons_before` 21 |

## Why these arms, and why this primary pair

**The yes on a negative is the failure to fix, and no cycle has told the model what the component's problems look
like.** Under the shipped instruction a-4b abstains (unknown share 0.560, A001 run 1, arm A); under the negation
instruction it answers, mostly yes (sensitivity 0.833, specificity 0.320, arm C); the mapping converts abstention into
refutation and the best it reached was level with the lexical judge (0.667 [0.620, 0.710], paired 0.003 [-0.047,
0.050]). The payload names the component by its label alone. D002 measured, on the same six makes and the same
held-out years, that a vocabulary learned from the earlier years' filed categories reads the later years' categories
far better than the hand pack's label-like lexicon (micro F1 0.590 against 0.406, difference 0.184 [0.157, 0.212];
coverage 0.840 against the label-names control's 0.476, D002 Run 1, the hand pack's coverage not recorded; C8): the
drafted terms carry more of how a filed problem is written than the label does, under the extractor's own metric.
Lever 5 puts that vocabulary in front of the model and, by the same stroke, raises the comparator the model must beat
to the lexical judge that matches those same terms.

- **Arm D (the primary)** gives the model the terms and reads its answers as answered. `D_minus_B` is the lever's
  reading: whether the terms move the answers at all, and in which direction (read with sensitivity and specificity:
  a list of terms can make a model say yes more, which lowers specificity, or let it say no to a component whose
  usual words are absent, which raises it). `D` minus the drafted lexical judge is the question the loop needs
  answered: whether the model adds anything to the terms, or merely matches them.
- **Arm Dr (the headline secondary)** reads D under the branch's declared reading: the mapping under branch N (A001's
  measured lift of the mapping under N: 0.077 [0.047, 0.107]; A002 run 1 replicated it at 0.053 [0.030, 0.080],
  CHOICE-A002 Run 1) or the grounding check under branch E (A002's measured lift, unknown when this rule was
  committed; A002 run 1 recorded `Eg_minus_E` 0.013 [0.003, 0.027], CHOICE-A002 Run 1). It is secondary under the standing default (the declaration); a call before the
  request push promotes it by amendment.
- **Arms B and Br** are the controls: the earlier cycle's arm and its reading on a fresh draw. Without B a lift could
  be a between-draw difference (A002's B14: two independent draws differ by sampling alone by up to about 0.10). B's
  figures beside the earlier cycle's are a replication, reported, not tested.
- **The drafted lexical judge is a comparator, not an arm.** It calls the pinned `lexical_judge` and makes no model
  call. It is computed in the plan job before any shard starts, as every comparator is, and the pass test must beat
  it (rule 13). It is also the ablation that lever 4's composite needed and this cycle's arms do not. A model that
  answers yes exactly when a term occurs in the text, negation ignored, reproduces the drafted term-presence judge of
  rule 8 line for line; one that also applies the pack's negation cues within three tokens reproduces the drafted
  lexical judge; the pass test must beat both (C5), and the agreement and conditional confirm rates of rule 14 say how
  close D came to either (trap 6).
- **Why a-4b alone**: "The base task and the arms". Running the two smaller models would add four comparisons and
  runner minutes with no reading toward 86 that A001 did not already give; they return when a lever is one they can
  follow, or when lever 8 replaces them with a larger local rung.
- **Why no third task.** A task that carries the terms without the sentence, or the sentence without the terms,
  would separate the instruction from the data; that costs 300 calls per task and a comparison, and the lever's
  text names the payload, not the sentence. A task carrying the hand pack's lexicon phrases instead of the drafted
  terms (the predicate-descriptions arm A002 declined) would test the hand comparator's lexicon; it is left to a
  later rule with the chief scientist's view.
- **The honest expectation for P, from the recorded figures: failure.** D002's figures say that the drafted terms
  match the filed category more often than the hand lexicon under the extractor's own metric; D002 recorded no
  coverage for the hand pack, so no figure says how much more often the drafted terms fire than the hand lexicon, and
  none says anything about this judge's balanced accuracy, which trades sensitivity against specificity on paired
  questions. Whether the drafted judge scores above, level with or below the hand judge is measured in the plan job
  (its sensitivity and specificity, printed beside the hand judge's, are the first such figures) and is not predicted
  here (C8). If it scores above, the bar P must clear rises above the 0.663 to 0.680 the hand judge gave on J001's,
  A001's and A002's draws (0.670, 0.663 and 0.680); if not, the hand clause holds the bar at the hand judge's level.
  The model with the label alone scored 0.577 as answered and 0.653 to 0.667 under a mapping (A001 run 1), and under
  task N on A002's draw 0.633 [0.590, 0.677] as answered and 0.687 [0.643, 0.730] under M, against that draw's
  lexical judge 0.680 [0.637, 0.723] (CHOICE-A002 Run 1). For P to pass, the terms would have to move a-4b by more than they
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
  CHOICE-A001 Run 1, the latency table). Task B under branch N is the negation task, whose a-4b medians there are
  8,637.7 ms and 9,431.5 ms by runner CPU: at those medians a pass of 60 calls takes about 518 to 566 s and 120 such
  calls about 1,036 to 1,132 s (derived here from those medians; C7); the longest A001 shard (`s011-a-4b`, 120 calls)
  took 1,377.2 s of wall time (CHOICE-A001 Run 1). A002's per-task latencies were not known when this rule was
  committed; A002 run 1 recorded the negation task's a-4b medians at 4,909.1, 6,645.3 and 9,297.1 ms by runner CPU
  (95th percentiles 7,163.6 to 16,111.1 ms), and its longest shard (`s004-a-4b`, 180 calls over three tasks) took
  2,735.4 s (CHOICE-A002 Run 1).
- **The longer payload.** Task D's input grows by `predicate_terms`: at most 50 terms of at most 64 characters each,
  so at most about 3,200 characters more in the data block, against a narrative of up to 6,000 characters; the
  reply does not grow. The extra cost is prompt processing only. A001's figures do not separate prompt processing
  from decoding, and the cost of task D's longer prompt is not measured. This rule assumes, without a figure, at most
  twice an A001 call (C7): a D pass of 60 calls at most about 1,132 s at the slowest recorded negation median, and a
  unit under branch N at most about 1,698 s (derived here), which the 300-minute budget covers more than ten times
  over. If a D call takes longer than that, the budget stop falls in pass B and D, P's set, stays complete (rule
  15). The warm-up's latency for task D and the run's per-task latency are reported, decide nothing, and replace the
  assumption for the next rule.
- **Under branch E** the replies carry citations (512-token cap). A002's arithmetic for E (its "Why the units are
  this size": a citation of 10 to 60 tokens adds about 1 to 9 s at the recorded decode rate of 7.0 to 9.0 tokens per
  second, RESULTS run 1, the E3 table, a-4b; a reply cut at the cap adds about 57 to 73 s and its repair about as
  much again) applies to both passes here: at the medians the two passes take under an hour; if every call of both
  passes ran to the cap and was repaired once, a unit would need about 120 × 160 s, more than 300 minutes, and the
  budget stop would fall in pass B, with D, P's set, complete (rule 15). A002 run 1 recorded no reply of any task cut
  at its cap (CHOICE-A002 Run 1); branch E is not in force.
- **One unit per shard** lets a slow unit run to its budget without delaying another. The expected wall time is
  about 1 to 2 hours, dominated by provisioning, the warm-up and the D pass; the five shards run at once. The job
  budget is five shards of at most 330 minutes each, as A002's; the expected use is a small fraction of it.
- **The term run** is one model-free unit of 10 minutes in a 45-minute job (the minimum `job_minutes`,
  `lab/request.py:1103`); its work is the plan job's `prereg` step, whose timeout is 20 minutes inside the plan job's
  30 (`.github/workflows/mycelic-lab.yml:77` and `:28`; C11): the archive download (A001's whole plan job took 44 s,
  the status log, 05:28), the pooled export, the draft and the check. The pooled corpus is the union of D002's six
  training corpora (their sum less the complaints filed under two makes, counted once, C28); D002's 358 s covered six
  drafts with their checks, controls and scoring on one shared runner (CHOICE-D002 Run 1). The draft and the check run
  in a subprocess under `lab.prereg._Steps.run`, with their own timeout below the step's and capped logs among the
  plan's prereg logs, as the L1 step runs (`L1_TIMEOUT_S`, `lab/prereg.py:131`, used at `:638`; the build fixes the
  timeout and `BUILD-A003.md` records it); a timeout or an out-of-memory exit refuses the plan with
  `JT_DRAFT_FAILED`, a stop before any model. The term run prints the step's wall time and the subprocess's peak
  resident memory (`resource.getrusage`). A term run that does not fit is an infrastructure failure under rule 17:
  the remedy is a reviewed workflow change (the step's and the plan job's timeouts, or where the step runs), never a
  drafter parameter; no setting of this rule changes, and the amendment records the attempt.
- If a pass does not finish anyway, rule 15 applies.

## How it is read

- **The verdict on P** is read by rule 13 and only there: `pass_provisional`, `partial`, `failure` or `incomplete`,
  printed by the aggregate with "P scored BA b [lo, hi]; gap to 0.860 is g; lexical l [lo, hi]; bound p", where l is
  the drafted lexical judge's figure, with the hand judge's and the term-presence judge's printed beside it (C5,
  C6), and with P's word under lever 5's text alone printed beside the word of this rule (C6).
- **The drafted lexical judge against the hand lexical judge**, paired by record in the plan job, is the first line
  of the Runs entry after the fixed line: it says what lever 5's vocabulary did to word matching on this metric
  before any model ran. A drafted judge above the hand judge (lower end above 0) raises the bar as expected; one not
  told apart from it says the terms carry no more than the labels on this draw; one below it says the extra terms
  fire on negatives more than they find positives. In every case `pass_provisional` and `partial` need every lexical
  judge beaten (C6), so a drafted judge below the hand judge never lowers the bar A001 and A002 stood on. The
  term-presence judge beside them (C5) says what the same terms give without the lexical judge's negation handling.
- **The honest expectation** is failure on P ("Why these arms"). The lever's reading survives a failure: `D_minus_B`
  with sensitivity and specificity, and D minus the drafted judge.
- **The headline** (rule 11) says whether a-4b with the drafted terms judges better than, worse than, or not apart
  from word matching with the same terms, and the same against the hand judge and the term-presence judge (C5);
  Dr's lines are reported beside.
- **The lifts** (rule 10) say what the lever did. Read `D_minus_B` with sensitivity and specificity: terms that make
  the model confirm more raise sensitivity and lower specificity and move balanced accuracy little; terms that let
  it refute a component whose usual words are absent raise specificity. `Dr_minus_D` repeats the reading's measured
  effect under the terms; `Br_minus_B` replicates it without them.
- **The agreement readings** (rule 14, trap 6): a D whose verdicts agree with the drafted lexical judge, or with the
  term-presence judge (C5), on nearly every question, and whose confirms follow that judge's confirms, is word
  matching with a model in the loop; a D that confirms where the judges refute and refutes where they confirm is
  reading something else, well or badly, and its paired differences to the judges say which.
- **Branch E's citation readings** (rule 14, trap 4): the shares of grounded citations that equal a drafted term or
  contain one as a whole word (C16) say how often the model's evidence is the term it was handed; a high share with
  no gain over the drafted judge is the trap closed. Branch E is not in force.
- **150 records resolve little** (A002's B14): a balanced-accuracy interval is up to about 0.14 wide; a paired
  difference to a lexical judge is told from 0 only when it is about 0.05 or more; `partial` needs an arm about 0.05
  or more above the drafted judge, whatever that judge scores; an arm between is `failure` for want of power, not
  evidence that the lever does nothing. The 0.05 is A001 run 1's paired half-width of a model arm against the hand
  lexical judge (0.047 to 0.054, B14); A002 run 1's paired intervals to the lexical judge were 0.097 to 0.113 wide
  (derived here from its arms table). D and the drafted judge read the same terms, so their per-record agreement,
  which sets the paired interval's width, is unmeasured and may make the clause easier or harder to tell from 0 than
  A001's. The drafted judge's balanced accuracy b_L is not known until the plan job prints it, so the difference P
  needs (0.860 − b_L at least, and about 0.05 to be told from 0) is not known before the run; a D within about 0.05 of
  b_L is `failure` for want of power, not evidence that the terms did nothing, and `D_minus_B` carries the lever's
  reading either way (C25).
- **0.5 is no judging, with J001's caveats.** A judge that confirms everything, or refutes everything, scores 0.5 on a
  balanced draw; one that answers unknown everywhere scores 0. The record-blind control and the predicate-only bound
  are read as J001 reads them: neither decides anything; a model at or below the bound is not shown to read.
- **This is one public field,** complaints written to a regulator, with the models possibly having seen them in
  training. A pass here would be a pass on public text the models may have seen; the Runs entry says so.

## Traps

1. **Terms drafted from any drawn record, or from records received after a drawn record.** Closed by construction
   for received dates and enforced on the day for record identity and text ("The terms", items 1 to 3; item 8 of
   "The data and the draw"; C4): the drafter's export holds only rows received before 2023-01-01, the first day of
   the pinned window, and every drawn record was received on or after it by the pinned eligibility code; a drawn
   record received before the cutoff stops the plan job. The term run reads no earlier cycle's artifact and nothing
   of this cycle's draw, which does not exist when it runs. What a date alone does not close, item 8 closes before
   the draw by record id (a complaint whose rows lie on both sides of the cutoff, in the drafting export by its
   earlier rows) and by text (trap 3), and checks after it.
2. **A drafted vocabulary that encodes the key.** The drafter learns from filed categories, which are the key's
   source. What the received-date split keeps out: the drawn records' own categories, narratives, ids and states
   enter nothing (item 8 of "The data and the draw" enforcing it by record id and by text, C4), neither the corpus (`draft.py:204-227`, training rows only), nor the refusal (every row of the
   export it is given, and the export ends before the cutoff), nor the check, nor the privacy floor; so no drawn
   record's filing shaped any term, and a term can carry nothing about any drawn record in particular. What leaks
   through category-level learning even so: the terms encode the filing convention, how complaints filed under a
   component usually name it and its failures, over tens of thousands of other complaints of the same six makes;
   p(c | t) at least 0.6 with df(t, c) at least 10 favours frequent categories in ties and thresholds; and the
   payload's list for a predicate is longer when the category is common. A judge that matches the terms therefore
   recovers part of the key without reading for a problem, and a model told the terms can do the same. That is the
   lever's known limit, not a hidden one: the lever text itself makes the lexical judge with the same terms the
   comparator, and the pass test must beat it and the term-presence judge of rule 8 (C5); the record-blind control and the predicate-only bound stand against
   the frequency leak as before; and each question carries the terms of its own predicate, positive or negative
   alike, so the list's presence and length say nothing about which question is which beyond the predicate's
   frequency, which the bound already prices.
3. **Duplicate narratives across the split.** A drawn complaint whose text equals a pre-cutoff complaint's (a
   re-submission, a copied narrative) was, as text, in the corpus, though as a record it was not. A term is one or
   two words in at least ten records at three states, so no single narrative makes a term, and the 8-gram check
   refuses nothing of this kind (terms are shorter). Such a record is removed from the eligible list before the draw
   (item 8 of "The data and the draw", by text, against the term run's narrative hashes and the day's rebuild), and
   the count is reported (C4). The removal changes the population by exactly the count printed (a narrative repeated
   across the years, word for word, is the case it removes); it reads no code and no answer.
4. **A model that copies drafted terms into the citation (branch E).** The grounding check g says a citation came
   from the record, not that it reports a problem (A002 trap 3); a drafted term of four or more characters that
   occurs in the record is a grounded citation under G (a three-letter term, which the drafter allows, is not:
   `token_min_letters` 3, `GROUNDING_MIN_CHARS` 4, `lab/j3.py:127`; C26), and the drafted lexical judge confirms
   there too. The shares of grounded citations equal to a drafted term and containing one as a whole word, per kind
   (rule 14, C16), expose this; the pass test's clause against the
   drafted judge is its backstop. A term copied that is not in the record is ungrounded and read as a no under G,
   which costs the model a true confirm if the record reported the problem in other words.
5. **A payload so long that the narrative is cut.** The narrative is cut at 6,000 characters by `judge_payload`
   before anything is added (`verify.py:162-168`, `truncate`), and `predicate_terms` is added beside it, never in
   its place: the terms never shorten the narrative. The whole prompt (system text, the task's instructions, the
   schema, the data block with a 6,000-character narrative and at most about 3,200 characters of terms) stays far
   inside the 16,384-token slot. A reply to a prompt the server could not fit would be an HTTP error, a transport
   failure, counted and withheld as rule 9 says; the warm-up's worst-case payload (the longest generated narrative
   repeated to the cap, `lab/warmup.py:126-128`) carries the longest list in T by characters, not the first sorted
   predicate's (C12), so a slot that cannot hold the longest D payload stops the shard before any drawn record is
   sent (an infrastructure failure under rule 17).
6. **The drafted lexical judge is the model's own payload: a composite by another name.** The lexical judge applies
   the pack's negation cues within three tokens and leaves a predicate stated only as negated out (`extract.py:17-23`;
   `pair`, `:550-554`); a model that confirms on any occurrence of a term, negation ignored, is a cruder matcher that
   can score above it on this field, where a negated mention is usually a problem report (J001 trap 3). The
   term-presence judge of rule 8 is that matcher, and the pass test must beat both (C5). A model that reproduces either
   inherits its score with the model contributing nothing, as lever 4's composite would inherit the hand judge. The
   pass test's clauses against the two judges (rule 13) are what lever 4's ablation clause was; the agreement shares
   and the conditional confirm rates of rule 14 say how far D is from each; and `D_minus_B` says whether the terms
   moved the model at all. A D that equals either judge line for line is reported as such.
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
    starts; those figures are never a reason to redraw, and the drafted judge's figure is never a reason to re-draft;
    nor are the term run's printed counts (predicates reached, empty lists, terms per predicate, the refusal's counts)
    a reason for a second term run: a second term run happens only for rule 17's two causes, and its sha256 is
    recorded beside the first's (C10). A seed or cutoff change is a numbered amendment stating a cause independent of
    the printed figures, reviewed before the build.
18. **Three artifacts expire.** J001's on 2026-11-08T22:00:03Z, A001's about 2026-11-10, A002's on
    2026-11-12T17:34:56Z (from A002 run 1, lab run 38439612256, as the Actions API listed it on 2026-10-14; C3), and
    the term run's 90 days after it runs, its `expires_at` recorded by amendment (C15). After each expiry that cycle's
    freshness rests on its rebuild and on the chains under item 4; after the term artifact's expiry the judging run
    cannot start without a new term run and a new amendment. The judging request is therefore pushed before the term
    artifact's recorded `expires_at`, its push date recorded in Runs beside it; a push after it finds no artifact and
    the plan job refuses with `J4_TERMS_UNVERIFIED` (C15).
19. **The complaint file changes.** The labels come from the file as published on the day. The exclusion works on
    refs, so freshness holds whenever an artifact verifies, and a changed file is reported through the rebuilds'
    prefixes and the file's sha256 beside `a5a5de2d31f3…` and A002's `216ecb36f9f5…` (from A002 run 1, lab run
    38439612256; C3). If the file changed, the three rebuilds fail
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
  and provisions nothing: the precedent of `lab/requests/openfda-001.json`, a model-free request whose one listed
  model is never served; `lab.plan.provision_entries` provisions nothing when no shard serves a gguf model,
  `lab/plan.py:321-324`, called at `:409`; C17); `job_minutes` 45; `max_parallel` 1; `retention_days` 90 (C15); a
  `purpose` naming this file and the copy's name and no other digit; pushed only after A002's lab run has ended and
  no other lab run is in flight (C31);
- `experiments.jt`: `minutes` 10; `pack` `docs/collective/replay/vehicles/pack`; `cutoff` `2023-01-01`; `from`
  `2020-01-01`; the makes, the roles, the columns, the drafter's parameters and the language are code constants of
  `lab/jt.py` (`NHTSA_MAKES` imported from `lab.goldlabels`; the rest written out, pinned by a test against
  `D002-settings.json`'s `arms.nhtsa` and against `data/defaults.json`), not request fields, so that no request can
  draft from another window or another make set.

`lab.request` validates it (`cutoff` equal to `NHTSA_WINDOW[0]` in ISO form; `from` equal to 2020-01-01, the first
day of `D002-settings.json` `arms.nhtsa.train`, a test pinning `lab/jt.py`'s window to it, C23; the pack exactly the
vehicle pack).

**The judging run**, `lab/templates/accuracy-003.json`, copied to `lab/requests/accuracy-003.json`:

- top level: `provider` `llama-server`; `models` `["a-4b"]`; `job_minutes` 330; `max_parallel` 5; `retention_days`
  30; a `purpose` naming this file and the copy's name and no other digit;
- `experiments.j4`: `minutes` 300; `labels` `{source: nhtsa, pack: docs/collective/replay/vehicles/pack, n: 150,
  seed: 4}`; `parts` 5; `seed` 4; `bootstrap_b` 10000; `bootstrap_seed` 4; `base_task` `evidence` or `negation` (by
  the adaptation rule; read on A002 run 1 under "The base task, read from A002 run 1": `negation`); `primary`
  `{model: a-4b, arm: <base>_terms}`, or `{model: a-4b, arm: evidence}` in the confirmation case (C1; not in force);
  `comparisons_before` 21;
- `experiments.j4.exclude`, a list of three entries in seed order: J001 (`run_id` 37996675559, `artifact`
  `lab-plan-37996675559-1`, `file` `prereg/j1/labels.jsonl`, `sha256_prefix` `bc6d092d8bca`, rebuild seed 1 with
  the same prefix); A001 (`run_id` 38114522160, `artifact` `lab-plan-38114522160-1`, `file`
  `prereg/j2/labels.jsonl`, `sha256_prefix` `623780b8a1de`, `chain_file` `prereg/j2/excluded.jsonl`,
  `chain_sha256_prefix` `9a948cbf2cd9`, rebuild seed 2 with prefix `623780b8a1de`); A002, the result run's entry
  (C2, C3), from A002 run 1, lab run 38439612256: `run_id` 38439612256, `artifact` `lab-plan-38439612256-1`, `file`
  `prereg/j3/labels.jsonl`, `sha256_prefix` `0a64a62d94a7`, `chain_file` `prereg/j3/excluded.jsonl`,
  `chain_sha256_prefix` `65d51f0fca51`, rebuild seed 3 with the prefix `0a64a62d94a7` (A002's questions prefix
  `84486fa38568` is a code constant of `lab/j4labels.py`, as A001's is of `lab/j3labels.py:56`);
- `experiments.j4.terms`: `run_id` and `artifact` (`lab-plan-<run id>-<attempt>`) of the term run, `file`
  `prereg/jt/terms.json`, `sha256` the full 64 hex of the committed term set, `vocabulary_hash` P_D's full 64 hex,
  `cutoff` `2023-01-01`; filled by the amendment after the term run (T1, T2): `run_id` 38612800578, `artifact`
  `lab-plan-38612800578-1`, `file` `prereg/jt/terms.json`, `sha256` `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`,
  `vocabulary_hash` `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579`, `cutoff` `2023-01-01`.

`lab.request` validates every field (integers in range, artifact names of the lab's shape, lower-case hex prefixes
of 12 to 64 characters for the exclusions (`SHA256_PREFIX_RE`, `lab/request.py:285`) and exactly 64 for the term set
and the hash (`_j4_terms`: `sha256` and `vocabulary_hash` by `[0-9a-f]{64}`, `run_id` an integer, `artifact` of the
lab's artifact-name shape, `file` equal to `prereg/jt/terms.json`, `cutoff` equal to `NHTSA_WINDOW[0]` in ISO form;
C23), files pinned per entry
(`prereg/j1/labels.jsonl`, `prereg/j2/labels.jsonl`, `prereg/j3/labels.jsonl`; the chain keys on the second and third
entries only, `prereg/j2/excluded.jsonl` and `prereg/j3/excluded.jsonl`), rebuild seeds 1, 2, 3 in order, `base_task`
one of the two, the primary arm `<base_task>_terms`, or `evidence` with `base_task` `evidence` (the confirmation
case, C1), for the primary and any arm of that branch's four for the validator, the model one of the block's and a
gguf model); `lab.plan --github-output` echoes the three excluded runs
and artifacts (`j2_exclude_*`, `j3_exclude_*_2`, `j4_exclude_*_3`) and the term run (`j4_terms_run_id`,
`j4_terms_artifact`); the plan job downloads all four.

## The build plan

The build starts only after this rule's review and any amendment are committed, in two builds with a lab run
between them. It changes no pinned file: `lab/j1.py` and `lab/goldlabels.py` (J001's `CODE_FILES`), `lab/j2.py` and
`lab/j2labels.py` (A001's), `lab/j3.py` and `lab/j3labels.py` (A002's, `lab/j3.py:175`), nothing under
`mycelic/collective/` (the onboard package and its data files included), `mycelic/*.py`, `connectors/openfda.py`,
`research/**`, `lab/shard.py`, the vehicle pack, any earlier `CHOICE-*.md`, `run-*.json` or `*-settings.json`.

**The order.** (0) Before build 1, the j3 template dry run's plan summary, `report.json` and `report.md` are recorded
on the build's base commit (`5a8f980`, or a later commit with the same lab code), and tests pin them byte for byte
after each build, as D003's build recorded D002's golden outputs (C30). (1) Build 1, the term-drafting kind `jt`, on
`wf/a003-terms`: build, review, fix, merge. (2) Push `lab/requests/terms-003.json` alone, only after A002's lab run
has ended and no other lab run is in flight (C31; A002's run ended at 18:23:04 UTC on 2026-10-13): the term run. (3)
The dated amendment of this file: the term run's figures from the first term run whose check passes ("The terms",
item 10; C10), with the artifact's `expires_at` (C15). A002's prefixes and the base-task branch are already recorded
by the amendment of 2026-10-14 (C3; "The base task, read from A002 run 1"); build 2 starts only once A002's result run
is recorded (C2), which it is. (4) Build 2, the judging kind `j4`, on `wf/a003`: build, review, fix, merge. (5) Push
`lab/requests/accuracy-003.json` alone: the judging run, before the term artifact's recorded `expires_at`; the push
date is recorded in Runs beside that timestamp, and a push after it is refused by the plan job as
`J4_TERMS_UNVERIFIED` and needs a new term run and amendment (C15).

- **Two new kinds, not additive options on `j3`.** `lab/j3.py`'s constants `TASKS`, `ARMS`, `LIFTS`, `LINE_KEYS` and
  `PREREG_KEYS` are read by the request validator (`lab/request.py:747`, the primary arm over `lab.j3.ARMS`), the
  unit adapter (`lab/units.py:303`), the warm-up (`lab/warmup.py:207-226`) and the aggregate (`lab/aggregate.py:1463`:
  the comparison count is `len(lab_j3.ARMS)` times the models); the summary holds its own copies
  (`lab/summary.py:187-189`). Extending them would change what a re-aggregation of A002's run computes, which is
  A002's recorded computation, and `lab/j3.py` and `lab/j3labels.py` are in A002's code hash (`lab/j3.py:175`), so
  editing either changes a pin A002's units verified. So A003 is a new kind `j4` whose own constants hold its two
  tasks, its four arms per branch, its lifts and its line keys, and which imports from `lab.j2` unchanged
  (`NEGATION_TASK`, `NEGATION_INSTRUCTIONS`, `negation_task`, `MAPPING`, `mapped_reply`, `mapping_pin`,
  `lift_reading`), calls `lab.j2.pass_test` and `lab.j2.verdict_word` unchanged inside its own `pass_test` and
  `verdict_word` (C19), and imports from `lab.j3` unchanged (`EVIDENCE_TASK`, `evidence_task`, `evidence_schema`,
  `EVIDENCE_INSTRUCTIONS`, `FIELD_MAX_TOKENS`, `norm`, `grounded`, `grounded_reply`, `grounding_pin`, `first_key`,
  `LAST_SENTENCE`; task D's text is built by C32's construction, since `_with_field` splits N's text only), from
  `lab.j2labels` unchanged (`earlier_refs`, `rebuild_refs`, `eligible_records`, `draw`, `label_lines`,
  `excluded_jsonl`, `build_labels`, `nhtsa_rows`) and from `lab.j3labels` unchanged (`earlier_labels_refs`,
  `rebuild_a001_refs`, `chain_entry`); and the term run is a kind `jt` of its own, so that no judging kind drafts.
  A002 can be re-aggregated at any later commit with its report unchanged, and the golden outputs of step (0) pin
  that claim byte for byte (C30).
- **New modules.** `lab/jt.py` (the term kind: the cutoff and window constants; the roles and columns written out;
  `pooled_export(blob)`: the six makes' rows over the drafting window through `nhtsa_export.complaints` with
  `fetch_nhtsa._Every` and `REPORTER` as D002's split, loading `tools/onboard/fetch_nhtsa.py` by path inside this
  function only, never at import (C29), concatenated in `NHTSA_MAKES` order with a repeated `odino` dropped and
  counted (C28), as one CSV through `fetch_nhtsa.rows_csv`, parsed by `onboard.exports.parse_export`; the drafting
  block (C4: the row count, the sha256 of the sorted record ids, `prereg/jt/drafting_refs.jsonl`,
  `prereg/jt/drafting_text.jsonl`, `straddling`, the earliest and latest received date); `draft(export)`:
  `draft_export` with the roles and `load_params()`, run with the check in a subprocess under `lab.prereg._Steps.run`
  with its own timeout, peak memory and wall time printed (C11); `check`: `check_pack`; `term_set(draft, hand)`: T
  through the hand pack's value map, `terms.json`'s document and its sha256; `drafted_pack(hand_dir, terms,
  out_dir)`: P_D's files from the hand pack's bytes with `vocabulary.json` rewritten and empty lists given
  `draft.placeholder(<id>, ids)` (C21), loaded by `load_pack_dir`; the code hash of `lab/jt.py` (C29); the
  preregistration document with every hash, P_draft's and P_D's apart (C21); the fixed console line with the counts
  of C22; the verify command of the unit) and `lab/j4.py` (task D's two names, the sentence, `drafted_task(base)`
  by C32's construction, the payload with `predicate_terms`; the arms per branch, the lifts, the line keys; the
  drafted term-presence judge over `onboard.check.term_presence` (C5); `pass_test` and `verdict_word` of C19 with the
  word under lever 5's text alone (C6); the verdict line with the branch reading; the line check that recomputes the
  reading, `grounded` and the terms; the j1-shaped views; the pins (the hand pack's hashes, P_D's, the term set's
  sha256, both tasks, the reading, the arms); the preregistration document; the runner: two passes, D first (B first
  in the confirmation case, C1), A001's budget timer and signals, `complete_by_task`), `lab/j4labels.py` (the three
  layouts; `earlier_labels_refs` over A002's layout with `A002_QUESTIONS_SHA256_PREFIX` `84486fa38568` (C3) and
  `earlier_chain_refs` over `prereg/j3/excluded.jsonl`, each pinned equal to the pinned function on the earlier
  layouts by a test; `rebuild_a002_refs`; the eight sources, the stop rules and the consistency counts of item 4,
  with two chains each establishing a cycle as C20 says; the union; the split's removals by record id and by text
  and its tripwires (C4); the `excluded_runs` entries, three and one of kind `split`; the chain file of 600 lines
  plus the split's removals), `lab/j4terms.py` (the term artifact's check: the file's full sha256 against the
  request, the manifest cross-check, the document's cutoff, window, makes and hand-pack hashes against the code's and
  the repository's, the drafting files' sha256 against the document (C4), `lab/jt.py`'s code hash against the
  document (C29), P_D rebuilt and its hashes against the request's; outcomes as `lab.j2labels.OUTCOMES` plus `terms
  absent`; nothing of the artifact printed but counts and hashes);
  `tests/lab/test_lab_jt.py`, `tests/lab/test_lab_j4.py`, `tests/lab/j4_data.py` (the synthetic archive at 120
  complaints per make with a drafting-window block before the cutoff, so that four disjoint draws of 150 and a draft
  fit; three synthetic earlier artifacts built from it by the pinned code, the A002-shaped one at seed 3 with
  `prereg/j3/` files and the chain of three draws; a synthetic term artifact drafted from the synthetic pre-cutoff
  rows by the real drafter, holding a complaint with rows on both sides of the cutoff, a narrative repeated across the
  cutoff and a complaint under two makes (C4, C28); a three-entry stand-in and a term stand-in);
  `lab/templates/terms-003.json` (`retention_days` 90, C15), `lab/templates/accuracy-003.json` (`base_task`
  `negation`, `primary` `{model: a-4b, arm: negation_terms}`, the third exclude entry from A002 run 1, the term entry
  by the term run's amendment); `docs/collective/A003/BUILD-A003.md`.
- **The term run's wiring (build 1).** `lab/request.py` (`EXPERIMENTS` gains `jt`; `_jt` validating `minutes`,
  `pack`, `cutoff` equal to `NHTSA_WINDOW[0]` in ISO form and `from` equal to 2020-01-01, C23); `lab/plan.py`
  (`build_units`' `jt` branch: one model-free unit `jt`, kind `none`, as `x1`; the unit's params copied as
  `build_units` copies them, `lab/plan.py:246`, C27); `lab/prereg.py` (`_jt`: the one cached fetch, `lab.jt`'s steps,
  the draft and the check in a subprocess under `_Steps.run` with their own timeout and capped logs, as the L1 step
  runs, C11; `prereg/jt/terms.json`, `prereg/jt/pack/`, `prereg/jt/check.json`, `prereg/jt/drafting_refs.jsonl`,
  `prereg/jt/drafting_text.jsonl`, `prereg/jt/prereg.json`; `preregister`'s unit list and manifest key `jt`
  (`:706-711`) and `main()`'s `prereg:` line (`:788-791`), C27; the fixed line; a failed check refuses the plan with
  `JT_FLOOR_FAILED`, a draft the drafter refuses, a timeout or an out-of-memory exit with `JT_DRAFT_FAILED`);
  `lab/units.py` (`ADAPTERS["jt"]` running `python -m lab.jt verify`, `PREREG_EXPERIMENTS` gaining `jt`, the status
  reading; `unit_notes` and `public_text`, `:869-900`, giving a jt unit the public-data note, not `synthetic`, C27);
  `lab/aggregate.py` (`jt_block`: the hashes, the counts, the check's result; the source-collection loop,
  `:2025-2035`, and the report keys, `:2104`, C27); `lab/summary.py` (the jt lines of the plan summary and the report,
  the sha256 between the markers); `lab/notes.py` (`JT_LABEL`, `JT_TERMS_NOTE`, `JT_FLOOR_FAILED`, `JT_DRAFT_FAILED`,
  the section titles, `:590-605`, C27; every sentence free of ASCII digits: `tests/lab/test_lab_guards.py:153`,
  `test_notes_hold_no_digits`, so the cutoff is written in words there); the dry run (`lab/dryrun.py --jt-data DIR`,
  refused together with `--j2-data`, `--j3-data` and `--j4-data`, `lab/dryrun.py:122`, C27); `docs/lab/REFERENCE.md`
  and `lab/requests/README.md` (a `jt` block); `tests/lab/test_lab_docs.py`'s pins.
- **The judging run's wiring (build 2), every function a j4 kind touches** (A002's B25 list, with the additions):
  `lab/request.py` (`EXPERIMENTS` gains `j4`; `_J4_KEYS`, `_J4_REQUIRED` with `base_task` and `terms`;
  `_j4_exclude_list` validating three entries in seed order with the three files pinned per entry and the chain
  keys on the second and third; `_j4_terms` validating the term entry with the full-length pattern `[0-9a-f]{64}`
  for `sha256` and `vocabulary_hash`, the lab's artifact-name shape, `file` and `cutoff` as C23 says; `_primary`
  called with the branch's arms, accepting `evidence` with `base_task` `evidence` for the confirmation case, C1);
  `lab/plan.py` (`build_units`' `j4` branch, its unit params carrying `base_task` and `terms`, `lab/plan.py:246`,
  C27; `exclude_entries` returning three entries for a j4 plan;
  `plan_outputs` and `OUTPUT_KEYS` gaining `j4_exclude_run_id_3`, `j4_exclude_artifact_3`, `j4_terms_run_id` and
  `j4_terms_artifact`, empty for every other plan, the earlier pairs filled as before); `.github/workflows/mycelic-lab.yml`
  (two plan-job steps after `earlier-2` and before `prereg`: `earlier-3`, keyed on `j4_exclude_run_id_3`, into
  `${{ runner.temp }}/lab-earlier/<run id>`, and `terms`, keyed on `j4_terms_run_id`, into
  `${{ runner.temp }}/lab-terms/<run id>`, both `actions/download-artifact` at the same pinned sha, `github-token:
  ${{ github.token }}`, `continue-on-error: true`, under the workflow's read-only `actions: read`); `lab/prereg.py`
  (`_j4`: the term check through `lab.j4terms` first, then the eight sources and the stop rules through
  `lab.j4labels`, the split's rebuild, removals and tripwires (C4), the draw, J001's questions, the three lexical
  judges (C5), the control, the bound, the chain file, the pins; `preregister`'s unit list and manifest key `j4`
  (`:706-711`) and `main()`'s `prereg:` line (`:788-791`), C27; a `--terms-plans` directory beside
  `--earlier-plans`, and a `--terms-stand-in` for the dry run; `load_stand_in`'s list branch, `:404-419`, taking
  three entries for j4 as it takes two for j3, C27; the fixed lines); `lab/units.py` (`ADAPTERS["j4"]` with the two
  tasks in pass order; `required_tasks` (`:310-321`) and `write_routing` (`:452-471`) reading the unit's
  `params.base_task` for the branch's two task names, since `Adapter.routed_tasks` is a static tuple (`:281-304`),
  C27; `required_tasks` returning the D task; `build_argv`, whose J branch (`:357-359`) needs the unit's budget, and
  `run_unit`'s budget list (`:1218-1219`) gaining `j4`, C27; `harness_status` (`_j4_status`: exit 1 with D's pass
  finished is `ok` with `J4_TERMS_STOPPED`), `participation`, `harness_measurement`, `harness_verdict`, `unit_notes`,
  `public_text`, `PREREG_EXPERIMENTS`, `REPLY_PARTICIPATION`, `PASS_EXPERIMENTS`, `run_unit`'s `with_pack` list,
  `lab/units.py:1197`); `lab/warmup.py` (`warm_tasks`: a j4 branch registering D and B with the judge's generated
  payloads, D's typical payload carrying the term list of the asked predicate and its worst-case payload the longest
  list in T by characters (C12), both read from the preregistration's `terms.json` through `prereg`; the probe is
  task B's, both branches); `lab/responder.py` (handlers for the two `_terms` names, the shipped judge's answer behind
  the base's field, in the pack branch and the pack-free one); `lab/aggregate.py` (`j4_block`: the term pin, the two
  sets, the four arms with the three lexical judges, the control and the bound on the same records, the verdict words
  of C19 and the word under lever 5's text alone (C6), the lifts, the agreement readings with both judges (C5),
  branch E's citation readings (C16), the first-key shares, the token figures, `primary_result` with Dr's word; the
  manifest cross-checked against `prereg/j4/prereg.json` as A002's fix did; the source-collection loop and the report
  keys, C27); `lab/summary.py` (`J4_ARMS` per branch, `J4_LIFTS`; the j4 lines of the plan summary, with eight
  sources, three cycles, the split's counts and the counts; the report's sections); `lab/notes.py` (`J4_LABEL`,
  `J4_ARMS_NOTE`, `J4_VERDICT_NOTE` with the added clauses and the drafted judge, `J4_PRIMARY_LINE` with every
  lexical figure, `J4_TERMS_NOTE`, `J4_EXCLUSION_NOTE`, `J4_STAND_IN`, `J4_TERMS_STOPPED`, `J4_TERMS_UNVERIFIED`,
  `J4_FRESHNESS_UNVERIFIED`, `J4_EXCLUSION_DISAGREES`, `J4_TOO_FEW_RECORDS`, `J4_SPLIT_VIOLATED`, the section titles,
  every sentence free of ASCII digits); `lab/dryrun.py` (`--j4-data`, exclusive with the other data flags, C27);
  `lab/__init__.py`; `docs/lab/REFERENCE.md`, `docs/lab/README.md`, `lab/requests/README.md`,
  `docs/lab/INTEGRATION.md`.
- **Tests, declared.** `tests/lab/test_lab_jt.py` pins: the roles, the columns and the parameters equal to
  `D002-settings.json`'s `arms.nhtsa` and `data/defaults.json`; the cutoff equal to `NHTSA_WINDOW[0]` and the window's
  first day equal to `arms.nhtsa.train`'s (C23); the pooled export over the synthetic archive holds no row received on
  or after the cutoff and every make's rows before it, a complaint under two makes once (C28); the drafting files and
  the `straddling` count (C4); the draft on it through the real drafter; the subprocess's timeout and an
  out-of-memory exit giving `JT_DRAFT_FAILED` (C11); the check; T's 26 entries through the hand value map, the
  placeholder left out, the empty list for an unreached predicate, the twin split; P_D loading, its hashes differing
  from the hand pack's in `vocabulary_hash` and `config_hash` and equal in `detector_hash` and `fixtures_hash`; no
  placeholder of P_D folding equal to a T term or a hand term (C21); the drafted lexical judge over P_D confirming a
  synthetic narrative that holds a drafted term and refuting one that holds only the hand label; `terms.json`'s
  sha256 stable across two drafts of the same export; the fixed line without a term; the request block's errors; the
  unit's verify command; `lab.jt` importing without `tools/market` on the path (C29); a dry run of the kind; and
  A002's golden outputs byte for byte (C30). `tests/lab/test_lab_j4.py` pins:
  task D's text equal to this file's sentence inserted before the base's last sentence, under both branches, by C32's
  construction; the
  payload equal to the shipped payload plus `predicate_terms` and nothing else; the schema and cap equal to the
  base's; every `rule N` of this file naming the item whose bold title the test lists; the four arms per branch, the
  lifts, the readings as the pinned functions; the line check's every outcome; the words in A5's order with the
  added clauses on every cell of C19 (a P at or above 0.860 above the drafted judge but not the hand or the
  term-presence judge is `partial`; a P below 0.860 above the drafted judge but not both others is `failure`; a
  missing hand or term-presence figure is `incomplete`; `lab.j4.verdict_word` equal to `lab.j2.verdict_word` where the
  added clauses hold), the word under lever 5's text alone, and the confirmation case's primary and pass order (C1);
  the term-presence judge over `onboard.check.term_presence` confirming a negated mention the lexical judge refutes
  (C5); the three layouts' checks, each pinned
  equal to the pinned function on the earlier layouts case for case; the eight sources and the stop rules on
  synthetic artifacts (each cycle by own sources, by a chain with the others established, chains alone stopping,
  R2 and R3 `not attempted`, every consistency count stopping, two chains each establishing a cycle as C20 says);
  A002's questions prefix (C3); the term check's every outcome (a changed byte, a wrong cutoff, another hash, another
  code hash of `lab/jt.py`, a drafting file that does not match, an absent artifact); the split: a straddling
  complaint and a repeated narrative removed before the draw, the rebuild compared with the term run's on equal
  archives, and both tripwires stopping with `J4_SPLIT_VIOLATED` (C4); the draw over the eligible list less 450 refs
  and the split's removals, disjoint from the three synthetic draws, the chain file of 600 lines plus the removals;
  the warm-up's worst-case D payload carrying the longest list in T (C12); `plan_outputs` on a j4, a
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
  verified on the stand-in, the split's line with its two removal counts and its tripwires at 0 (C4), the draw line
  with eligible after exclusion at least 150 and overlap 0, the three lexical judges printed (C5), every unit `ok` and
  `plumbing`, every word `incomplete`, the comparisons 25, and no ref and no term anywhere. In a dry run the fake answers D as it answers B, so nothing in a dry run measures the terms;
  `BUILD-A003.md` says so.

## Runs

One so far: term run 1, below the two templates. The judging run's entry follows it.

**The term run's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule and the
request; the archive's sha256 and size; the fixed line (the rows per make and pooled, the cross-make duplicates
dropped, the earliest and latest received date, the corpus size, the categories passing the floor, the specific
predicates drafted, the hand predicates reached of 26 and the terms per reached predicate, the empty entries, the
refusal's counts, `rows_without_specific`, the candidate and eligible term counts, P_draft's placeholders, the check's
result by check, P_draft's and P_D's four hashes and the hand pack's, the drafting export's row count, the sha256 of
its sorted record ids and of the two drafting files, the `straddling` count, the onboard code hash, the two data
files' sha256 and `lab/jt.py`'s code hash, **the term set's sha256**); the step's wall time and peak resident memory
(C11); the unit's verify line; the report's marker line with its sha256; the artifact's `expires_at` (C15); every
term run's id and sha256 in order, with the reason for each later one (C10); and that no model ran and nothing was
drawn. The amendment that follows it copies the sha256, the hashes and the counts into "The terms" and "The request".

**The judging run's entry** will hold: the lab run id, the request file and its sha256, the commits of the rule, its
amendments and the request, and the branch in force with the A002 figures that fixed it (branch N, read from A002 run
1, as "The base task, read from A002 run 1" quotes them); the request's push date beside the term artifact's
`expires_at` (C15); the plan job's term line (the term artifact verified, the sha256, P_D's hashes and `lab/jt.py`'s
code hash equal to the committed ones, the archive's sha256 beside the term run's); the plan job's fixed draw line
(`available`, excluded 450 and the split's removals by record id and by text, eligible after exclusion, overlap 0,
the outcome of each of the eight sources with any by-chain reading named and every consistency count computed, the
labels' sha256 prefix, the questions' prefix, the prereg prefix, the chain file's prefix with its line count, the
complaint file's prefix beside `a5a5de2d31f3…` and A002's `216ecb36f9f5…`; the split's line of item 8, the drafting
set's figures beside the term run's and the two tripwire counts (C4); on a re-run after a plan job printed, both plan
jobs' lines); the comparators on the fresh draw with intervals: the drafted lexical judge with sensitivity,
specificity and unknown count, the hand lexical judge and the drafted term-presence judge beside it (C5), their paired
differences, the control and the bound; the four arms' table (balanced accuracy with interval, sensitivity,
specificity, unknown share, the paired difference to the drafted judge, to the hand judge and to the term-presence
judge, the bound on the same records, the pass test with the added clauses, the verdict word); the verdict on P =
(a-4b, D) in rule 13's words with every lexical figure, P's word under lever 5's text alone beside it (C6), and Dr's
verdict word beside it labelled "the primary under the other reading of the section 7 default"; the headline for D
against the three judges with Dr's lines beside; the lifts of rule 10 with their readings; the agreement readings; the
term counts per predicate and the empty-list share; under branch E the citation readings; the first-key shares and
the token figures per task; B and Br beside the earlier cycle's arms as a between-draw difference; the parts
finished per task and `records left out` per arm; anything that stopped before a model ran; the model-call latency
per task with the runner CPU; the comparison count ("4 this cycle, 25 since the loop began"); and the public-text
sentence.

### Term run 1 (lab run 38612800578, 2026-10-14)

**The run.** Lab run [38612800578](https://github.com/anovruzov/NeuralGraph/actions/runs/38612800578) (workflow
`mycelic-lab`), created 18:09:47 UTC, completed `success` at 18:11:49 UTC. Request `lab/requests/terms-003.json`,
sha256 `f3c3e4237c2f65d2027ad4dfc226789c62cc38bf935744b10e6f281a0a0ac851`, pushed alone as commit `5d2c1d3`, after
A002's lab run had ended and with no other lab run in flight (C31). Before the run, in this order: the rule `efa79fe`,
amended `7daab37` (C1 to C32) before any build; build 1 `31cee9d` (the lab kind `jt`), its two reviews, the fix
`a4e717f` and the merge `d270bd3`. Plan job 116020175039 (18:09:51 to 18:11:14, success): the self-test ran 79 tests,
OK; plan sha256 `871d83a19ea67bef71b2908d8e08a99e67931770fa3624552c38d893f0d4a839`; the `prereg` step ran from
18:10:07 to 18:11:08; it uploaded `lab-plan-38612800578-1` (artifact id 11918947585, 23 files, 3,007,683 bytes, digest
`a33c9ff30756…`); its plan summary block is 58 lines with sha256 `c866098933c7…`. The provision job was skipped (no
shard serves a model). One shard, `s001-none` (job 116020868513, 18:11:17 to 18:11:31), attempt 1, `sealed`, exit
code 0; its download step verified the plan artifact's digest; the runner's line for the unit reads `lab: unit jt
status ok class no-model exit 0 wall_s 0.2`. Aggregate job 116021013553 (18:11:34 to 18:11:48, success): its log
prints `report/report.md` between the `MYCELIC-LAB` markers as 64 lines with sha256 `ef68e3e122dd…`, matching
(recomputed from the log's lines with the timestamp prefix stripped and a trailing newline, as `lab/summary.py`
computes it); its INDEX line reads `report/report.json=290 report/report.md=64 report/lock-candidate.json=42`; lock
status `unchanged`, 0 new entries. The `report/report.json` block begins before the readable tail of the log, so its
sha256 is not verified here, and no figure below is taken from it alone. Every figure below is copied from the plan
job's `jt` lines; the report block repeats them (hashes as 12-hex prefixes; the draft timeout aside) and says `verified by the unit
yes`.

**The plan job's fixed lines**, printed before the unit ran, in the log's own words:

```
jt export: archive_sha256 4e5ae79183c489a385dac63d5d716995e6e053bdac26b9dbc91d6ce63244a4e7 archive_bytes 75874030 rows FORD=26919 CHEVROLET=15649 JEEP=10284 HONDA=10485 NISSAN=7969 DODGE=6284 pooled 77590 cross_make_duplicates 0 earliest_received 2020-01-01 latest_received 2022-12-31
jt drafting: rows 77590 refs_sha256 6072b844d878d2783f02603b5ac303e56745a40e623201158d3dee0e4c4c2be9 drafting_refs_sha256 6072b844d878d2783f02603b5ac303e56745a40e623201158d3dee0e4c4c2be9 drafting_text_sha256 4e92180e66e6fe71470a4437b378c4aa4b913bab137d53b13558636a82174823 narratives 77346 straddling 0
jt draft: corpus 77590 categories_passing_floor 36 specific_predicates 30 other_bucket_share 0.143 rows_without_specific 5934 terms_candidates 319171 terms_eligible 21173 placeholders 7 refused_categories 0 refused_terms 678 assignable 35 within_cap 8
jt check: passed yes loader=passed ngram=passed refused_strings=passed refused_terms=passed template=passed term_floor=passed value_floor=passed
jt terms: hand_predicates 26 reached 25 empty 3 terms_min 0 terms_median 43 terms_max 50 terms_total 753 p_draft_config_hash f6bdf3c65b31972d2bb73a1e89ff70c9d4a41b734dc27ac9cb95008e49d70420 p_draft_vocabulary_hash 09069d9f19d775208ffac44f20c195605955d010cf1f6d5a037b0f39d23527da p_draft_detector_hash 39499e9cc0a09942239d42dacba991b02db401624f201af85b975e5a92f08416 p_draft_fixtures_hash 693cf303644a33d2ea1087d8eafc4403ba9256b49178959d7bc96177ff81229a p_d_config_hash 6219eb0993b5668ecea91cc0811a6e446971d62001fe85e54662dc0a2b56698a p_d_vocabulary_hash c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579 p_d_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 p_d_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f hand_config_hash a588e72d7dbd0307d42234ceb79a85a915f0454d947469a4bfa0a42595f2f7bd hand_vocabulary_hash 867df7fc83c556e267842f219e1087b4bbd6af052ff5d201206d0aa8c789aa96 hand_detector_hash 325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779 hand_fixtures_hash 9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f onboard_code_hash a1cf1bf6d4202823c4ab11d28d49fc46926319e897379964bc3f89d77b7f9c0b defaults_sha256 9ca66ffc98580f3e2f22da8d702b943db52ba198c79be14e763bd97ad8efc95b lang_en_sha256 9c76178a457bba9d4d9f98460a4d8263f5112b048b978cf651a46162bbd10cdb jt_code_hash f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76 terms_sha256 42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc
jt step: wall_s 61.2 draft_wall_s 36.6 draft_timeout_s 900 peak_rss_kib 456148
```

**What it shows.** The check passed by every one of its seven checks, so the plan was written, and this run is the
first term run: its output is the term set (C10), sha256
`42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`. Of the 26 hand specific predicates, 25 are
reached by a drafted predicate holding their spelling; 3 entries are empty (the one unreached predicate and two
reached ones whose drafted predicate kept no term: the minimum over reached predicates is 0); the reached lists hold
43 terms at the median and 50, the cap K, at most, 753 terms in all. The drafting export holds 77,590 rows received
2020-01-01 to 2022-12-31; no complaint of it has a row received on or after the cutoff in the same archive
(`straddling` 0), and no complaint was filed under two of the six makes in it (cross-make duplicates 0). The work fit
with room: the `prereg` step took 61 s of its 20 minutes, the draft and the check 36.6 s of the subprocess's 900 s,
at a peak resident memory of 456,148 KiB on a runner with 15.6 GiB. No model ran and nothing was drawn. No term,
category spelling, narrative, record id, state or vehicle is in the lines read here or in this entry. Every term
run so far, in order: term run 1, lab run 38612800578, sha256 `42c591ccbb66…`; no later term run.

The figures are copied into the rule by the amendment after the term run ("Amended after the term run, before build
2, 2026-10-14").

## Amended before any build, 2026-10-14

**A002's result was known when this amendment was written, and this amendment says so.** It was written on
2026-10-14, from 13:39 UTC, by the AI system that wrote the rule, after A002's result had been recorded. No A003 code
exists, no term has been drafted, no A003 request has been pushed, and no record of this cycle's draw exists. A002
run 1 (lab run 38439612256, created 17:33:27 UTC on 2026-10-13, attempt 1, completed `success` at 18:23:04 UTC;
aggregate job 115464859031, 18:21:53 to 18:23:04 UTC; `report/report.md` printed between the `MYCELIC-LAB` markers as
144 lines with sha256 `58325c34d6bf…`, recomputed from the job's log and matching) ended about twenty minutes after
this rule was committed (`efa79fe`, 18:02:56 UTC on 2026-10-13) and before this amendment. Its figures, as
CHOICE-A002 "## Runs", Run 1 records them (commit `5c9544e`, 2026-10-14 13:32:22 UTC, on the branch
`wf/record-a002`, not yet merged into the deployment branch as this is written), with the run's jobs and its plan
artifact as the Actions API lists them (read 2026-10-14):

- every (model, task) set finished 5 of 5 parts, for each of the three tasks E, R and N, and `records left out` is 0
  in every arm; nothing stopped before a model ran; 900 model calls, 300 per task;
- the primary pair P = (a-4b, E `evidence`) scored 0.540 [0.503, 0.577], paired to the lexical judge -0.140 [-0.190,
  -0.090], verdict word **`failure`**; its headline secondary Eg `evidence_grounded` scored 0.553 [0.517, 0.590],
  word `failure`;
- the lift `E_minus_N` is -0.093 [-0.137, -0.053], "hurts";
- arm D `negation_mapped` (task N read through the unclear-to-no mapping) scored 0.687 [0.643, 0.730], paired to the
  lexical judge 0.007 [-0.050, 0.063], not told apart, word `failure`;
- the comparators on that draw: the lexical judge 0.680 [0.637, 0.723], the record-blind control 0.517 [0.460,
  0.573], the predicate-only bound 0.553;
- the plan job's fixed line: labels sha256 prefix `0a64a62d94a7`, questions `84486fa38568`, chain file
  `prereg/j3/excluded.jsonl` `65d51f0fca51` with 450 lines, complaint file `216ecb36f9f5`; the plan artifact
  `lab-plan-38439612256-1`, artifact id 11833749961, 74,062 bytes, created 2026-10-13T17:34:56Z, expiring
  2026-11-12T17:34:56Z.

**Under the rule as committed at `efa79fe`, these figures select branch N** ("The base task, read from A002 run 1",
below). Three constraints bind every item that follows. (a) No item changes which branch A002's recorded figures
select: an item that amends the adaptation rule only removes discretion or adds a case for an outcome that did not
occur, and says that it leaves branch N selected; a finding whose fix would change the selection would be declined
under "Declined" with that reason (no finding's fix would). (b) The facts of the exclusion that A002's run fixed (its
run id and attempt, its plan artifact with the artifact's id and expiry, the labels, questions and chain prefixes, the
chain's line count, the complaint file's prefix) are filled here, each labelled "from A002 run 1, lab run
38439612256": they say which records A002 judged, not how they were judged. The term set's sha256 and every figure of
the term run stay placeholders. (c) The base task is not chosen by this amendment: it is read from the committed rule,
under its own heading, and is not written as a change of the rule.

Three reviews of the rule at `efa79fe` against the lab's code at the deployment branch's head `5a8f980`, the loop
prompt and the onboard package found eleven blocking gaps and forty-four smaller ones, many raised by two or three
reviews. The changes are C1 to C32, numbered as this cycle's task asks (A001's A1 to A12 and A002's B1 to B25 live in
their own files and are cited with those files' names); each names its findings by where they point and says what the
finding was, what is now and what it changes. Where the text above and this section disagree, this section wins; the
body above was edited to agree, and each edit carries its item's number in parentheses.

Since the rule was committed, its author has read the files the items cite: `lab/units.py`, `lab/prereg.py`,
`lab/aggregate.py`, `lab/notes.py`, `lab/plan.py`, `lab/dryrun.py`, `lab/request.py`, `lab/summary.py`,
`lab/warmup.py`, `lab/j2.py`, `lab/j3.py`, `lab/j2labels.py`, `lab/j3labels.py`, `lab/requests/accuracy-002.json`,
`lab/requests/openfda-001.json`, `.github/workflows/mycelic-lab.yml`, `mycelic/collective/edge/verify.py`,
`mycelic/collective/edge/extract.py`, `mycelic/collective/onboard/check.py`, `mycelic/collective/onboard/draft.py`,
`mycelic/collective/onboard/data/defaults.json`, `mycelic/collective/onboard/data/template/ids.json`,
`mycelic/collective/packs/canonical.py`, `mycelic/collective/packs/connector.py`, `mycelic/collective/packs/loader.py`,
`tools/market/nhtsa_export.py`, `tools/onboard/fetch_nhtsa.py`, `docs/collective/onboard/D002-settings.json`, the hand
pack's `vocabulary.json` and `mapping.json`, CHOICE-A001's Run 1, CHOICE-D002's Run 1, CHOICE-L001's Runs, CHOICE-A002's
rule, its B1 to B25 and its Run 1, LOOP-48H sections 1, 2, 5, 6 and 7, and the status log's entry at `1e14f88`. No
figure here comes from this cycle's draw, which does not exist; no term has been seen; no reply of J001, A001 or A002
has been read.

**No finding asked to change the pinned metric, and none is changed.** 86 stays balanced accuracy 0.860 against the
filed-category key at record level with the codes hidden, on 150 fresh records scored as J001; rule 13's block stays
word for word LOOP-48H section 2.5; the interval rule and the confirmation rule are untouched; the drafted lexical
judge stays the pass test's lexical comparator, as lever 5 says. No floor is lowered: the pass test gains a clause (C5)
and `partial` a condition (C6), and neither loses one; the excluded set gains the split's removals (C4) and loses
nothing; the primary pair and the adaptation rule stand as declared, the rule losing discretion (C2) and gaining a case
for an outcome that did not occur (C1); the rest are figures traced to their sources, readings, the term run's limits
and the build plan. "Floors after this amendment" lists every floor.

### The base task, read from A002 run 1

This is a reading of the rule as committed at `efa79fe`, not a change of it. The committed trigger, verbatim (the
bullets of "The base task and the arms" before this amendment):

> **Branch E holds when A002 run 1 is complete for task E's set** (the aggregate's word is read: `measurement` true,
> 5 of 5 parts, dropped share at most 0.01, as CHOICE-A002 rule 12 reads "complete") **and either** P = (a-4b, E
> `evidence`) has the verdict word `pass_provisional`, **or** the lift `E_minus_N` (CHOICE-A002 rule 10: E's
> balanced accuracy minus N's, paired by record) has its 95% lower end above 0 on A002 run 1.
>
> **Branch N holds otherwise**: A002 incomplete on task E, stopped before any model ran, or complete with P not
> `pass_provisional` and `E_minus_N`'s lower end at or below 0.

The cells that resolve it, as CHOICE-A002's Run 1 prints them (commit `5c9544e`):

| What the trigger reads | Where CHOICE-A002's Run 1 prints it | As printed |
|---|---|---|
| Task E's set complete | the nine arms' table, row "E `evidence` (P)", columns "Parts finished" and "Records left out"; the verdict paragraph: "task E's set is complete (5 of 5 parts, class `model`, the report's "Model measurement" note, 0 records left out)" | 5 of 5; 0 |
| Task N's set complete (read by C2's lift clause) | the same table, row "N `negation`"; "Parts and records": "Every task set finished 5 of 5 parts for each of the three tasks (E, R and N)" | 5 of 5; 0 |
| P's verdict word | the same table, row "E `evidence` (P)", column "Verdict word" | `failure` |
| `E_minus_N` with its 95% interval | the lifts table, row `E_minus_N` | -0.093 [-0.137, -0.053], hurts |

The reading. A002 run 1 is A002's result run (C2): a model judged every drawn record, 900 calls, and "Nothing stopped
before a model ran". Task E's set is complete, so the run is not "incomplete on task E". P's word is `failure`, not
`pass_provisional`, so the first disjunct of branch E fails. `E_minus_N`'s lower end, -0.137, is not above 0, so the
second fails. Branch E does not hold, and branch N's clause "complete with P not `pass_provisional` and `E_minus_N`'s
lower end at or below 0" does: **branch N**. The same cells give branch N under C2's trigger (both sets complete; P
not `pass_provisional`; the lift's lower end at or below 0), and C1's confirmation case is not in force (P is not
`pass_provisional`).

So, read from the rule as committed: the base task is A001's task N, `judge_record_negation` (`lab/j2.py:80-93`,
`NEGATION_INSTRUCTIONS`; the shipped schema; `JUDGE_MAX_TOKENS` 256), imported unchanged, with the unclear-to-no
mapping M as the declared secondary reading; task D is `judge_record_negation_terms`, with the same schema and the
256-token cap; the four arms are D `negation_terms` (P's arm), Dr `negation_terms_mapped` (the headline secondary), B
`negation` and Br `negation_mapped`; **P = (a-4b, `negation_terms`)**; the request's `base_task` is `negation` and
its `primary` `{model: a-4b, arm: negation_terms}`; the pass order is task D, then task B. The comparison count stays
4 this cycle and 25 since the loop began: A002 run 1's aggregate printed "comparisons since the loop began: 21 (9 this
cycle, 12 before)" (CHOICE-A002 Run 1), the 21 this rule's `comparisons_before` carries.

For the reader, deciding nothing: on A002's draw, task N as answered scored 0.633 [0.590, 0.677] and task N through M
0.687 [0.643, 0.730], against that draw's lexical judge 0.680 [0.637, 0.723] (CHOICE-A002 Run 1); arms B and Br of
this cycle replicate those two on a fresh draw, as A002's N and D replicated A001's C and D.

A002's plan job printed its comparator figures while it ran (17:33:30 to 17:34:58 UTC on 2026-10-13), before this
rule's commit; the loop's status log recorded them at 18:05:20 UTC (`1e14f88`), after it. The rule's text cites none
of them, and whether its author had read the plan job's log before 18:02:56 UTC is not recorded. This amendment's
author has read them and A002's whole Runs entry. They enter no condition of the adaptation rule, which reads the two
sets' completeness, P's word and `E_minus_N` only, the last two first printed by A002's aggregate job at 18:23; they
bear on no setting of this cycle beyond the branch read here and the exclusion facts of C3.

### C1. The confirmation case: a provisional pass in A002 would make its pair this cycle's primary ("The base task and the arms"; rule 11; the settings, rows "Primary pair" and "Headline arm"; "The request")

- **The finding** (blocking): section 2.5, copied as rule 13, makes success depend on "P, named as the primary in the
  next cycle's CHOICE file before that run, passes again on that cycle's fresh draw". A003 is the cycle after A002.
  Had A002's P = (a-4b, `evidence`) been `pass_provisional`, the rule would have taken branch E and named P = (a-4b,
  `evidence_terms`): the pair that passed would have been only the control B, its confirmation never read, and the
  provisional pass could not have become a success in the cycle the pinned rule names. Rule 11 read an A002 pass as a
  reason to pick a base task, not as the confirmation obligation; left open, the primary would have been chosen
  after A002's data was seen.
- **Why the finding is right:** section 2.5 names the pair that must pass again, not its task family.
- **Now:** "The base task and the arms" gains, before "Under either branch the arms are the same four", the bullet
  **"Branch E, confirmation case"**: when A002's result run records P = (a-4b, `evidence`) with the word
  `pass_provisional`, section 2.5 requires that pair, named as the primary before this cycle's run, to pass again on
  this cycle's fresh draw; then **P = (a-4b, `evidence`)** (arm B under branch E, the answers as answered), D
  (`evidence_terms`) is the headline secondary, the two tasks, the four arms and the 4 comparisons are unchanged, the
  pass order puts P's set first (task B, then task D, for rule 15's reason: a late stop then loses the lever's
  reading before it loses the confirmation), and P's pass test is rule 13 as written for this cycle (the drafted
  lexical judge in the second clause, the hand clause and C5's term-presence clause), at least as strict as the test
  A002's P would have passed; D's word is read by the same rule beside it. Success in the sense of section 2.5 is then
  reachable in this cycle for (a-4b, `evidence`) alone and only by this confirmation; a pass by D stays provisional.
  Rule 11's sentence "a pass there would make its arm the base here by the adaptation rule" now reads "a pass there
  makes (a-4b, `evidence`) this cycle's primary for confirmation (the confirmation case, C1)". The settings rows and
  "The request" say "(a-4b, D); (a-4b, `evidence`) in the confirmation case"; the amendment that records the branch
  names which is in force; `lab.request` accepts for a j4 block the primary arm `<base_task>_terms`, or `evidence`
  with `base_task` `evidence`.
- **What it changes:** a case for an outcome that did not occur. A002 run 1's P is `failure`, so the confirmation case
  is not in force and **branch N stays selected**, with P = (a-4b, `negation_terms`) and the pass order D, then B. The
  finding's fix kept "the pass order (D first)" in the confirmation case; this item puts P's set first there instead,
  for the reason rule 15 gives, and says so here.

### C2. The trigger, read from named cells of A002's result run ("The base task and the arms", its first paragraph and the Branch E and Branch N bullets; the declaration)

- **The findings** (blocking, three reviews). (a) `E_minus_N` was required with task E's set complete only; the
  aggregate computes every lift whenever both views are non-empty, over the records both score, and marks it
  `complete` false when either set is not (`lab/aggregate.py:1374-1380`), while CHOICE-A002 rule 14 says a partial
  set decides nothing, so a lift over three finished N parts could have flipped the branch. (b) "Stopped before any
  model ran" was listed as a branch-N case, but under LOOP-48H section 6.3 such a run is not a result and the same
  request is re-pushed (a new run id, or attempt 2 with its artifact `-2`), and under CHOICE-A002 B13 a re-run's draw
  is the result; L001's "Run 1" was exactly such a stop (CHOICE-L001, Runs). "A002 run 1" did not say whether it
  meant the first attempt or the recorded result, so the branch could have depended on when the amendment was
  written, and in one case the request would have named an artifact no model judged. (c) The condition named no
  printed field. (d) Nothing said what happens if A002's result is not recorded when build 2 would start. (e) A
  missing `E_minus_N` (task N's set incomplete) failed branch E's clause only through the word "otherwise".
- **Now:** the section's first paragraph reads the branch "from CHOICE-A002's Runs entry for A002's result run": the
  first A002 lab run in which a model judged a drawn record (LOOP-48H section 6.3; CHOICE-A002 rule 16 and B13). The
  bullets read: **Branch E holds when** that entry records task E's set complete (as CHOICE-A002 rule 12 reads
  "complete": `measurement` true, 5 of 5 parts, `records_not_judged` 0, the dropped share at most 0.01; in the arms
  table, P's row "Parts finished" 5 of 5 and "Records left out" 0) **and either** the arms table prints
  `pass_provisional` as P's verdict word (the confirmation case, C1), **or** task N's set is complete too (row N, the
  same cells) and the lifts table prints `E_minus_N` with a 95% lower end above 0. **Branch N holds in every other
  recorded case**: task E's set incomplete; or P not `pass_provisional` with task N's set incomplete (a lift over an
  incomplete set is not read), with `E_minus_N` not printed, or with its lower end at or below 0. A run stopped before
  any model ran is never a trigger: while A002 has no result run, build 2 does not start (the term run does not wait
  for A002's record, C31); if A002 were abandoned without a result run by a recorded decision, this cycle would need a
  new choice file, since its three-entry exclusion presumes an A002 draw a model judged. The amendment that reads the
  branch quotes the cells it read as the Runs entry prints them, and says whether A002's plan job's comparator figures
  were read ("The base task, read from A002 run 1" does both). The declaration gains a bullet saying that this
  amendment was written after A002's result was recorded and that its author read those figures.
- **What it changes:** discretion removed and the cells named. Lab run 38439612256 is A002's result run, and its cells
  (both sets complete; P `failure`; `E_minus_N` -0.093 [-0.137, -0.053]) give branch N under the committed text and
  under this one: **branch N stays selected**. Two clauses of the findings' fixes are declined under "Declined"
  (task N's completeness for both disjuncts; branch N for an abandoned A002); neither bears on the selection.

### C3. A002's exclusion entry, filled from A002 run 1 ("The data and the draw", items 3, 4 and 6; the settings, row "Excluded"; "The request"; trap 18; the build plan)

- **The findings** (blocking, two reviews; one smaller): the entry hard-coded run 38439612256 and
  `lab-plan-38439612256-1` while only the two prefixes were placeholders, so a stopped first attempt would have named
  an artifact holding no judged draw (B1 and K3 then absent, A002 established by R3 alone or not at all); its
  retention date was "about"; and A002's questions file was not pinned by its prefix, so the B1 check was one pin
  weaker than the A1 check it generalises (A002 pinned A001's questions prefix `2ba18ea89bb7`).
- **Now:** the entry names A002's result run (C2). From A002 run 1, lab run 38439612256 (CHOICE-A002 Run 1; the
  Actions API, read 2026-10-14): run id 38439612256, attempt 1; artifact `lab-plan-38439612256-1`, artifact id
  11833749961, expiring 2026-11-12T17:34:56Z; `prereg/j3/labels.jsonl` prefix `0a64a62d94a7` (150 records, seed 3);
  `prereg/j3/questions.jsonl` prefix `84486fa38568` (300 questions in 5 parts); chain `prereg/j3/excluded.jsonl`
  prefix `65d51f0fca51` with 450 lines; complaint file prefix `216ecb36f9f5`. R3 is the seed-3 rebuild verified
  against the labels prefix `0a64a62d94a7`. B1 checks the questions file against `84486fa38568` as A002's code checks
  A001's (`lab/j3labels.py:56`, `A001_QUESTIONS_SHA256_PREFIX`): `lab/j4labels.py` holds it as the constant
  `A002_QUESTIONS_SHA256_PREFIX`, printed with B1's outcome. The request's third entry carries `run_id` 38439612256,
  `artifact` `lab-plan-38439612256-1`, `file` `prereg/j3/labels.jsonl`, `sha256_prefix` `0a64a62d94a7`,
  `rebuild_seed` 3, `rebuild_sha256_prefix` `0a64a62d94a7`, `chain_file` `prereg/j3/excluded.jsonl` and
  `chain_sha256_prefix` `65d51f0fca51`. A first A002 draw that no model judged would not hold "records judged in an
  earlier cycle" (LOOP-48H section 1, standing rule 1) and would not be excluded; none exists, since A002 run 1 was
  A002's first and only run.
- **What it changes:** placeholders filled with facts of which records A002 judged, and one more pin (A002's
  questions file). No branch, no setting and no figure of the result. Carrying the questions prefix as a field of the
  request's third entry is declined in form under "Declined" (the pin is a code constant instead).

### C4. The received-date split, enforced on the day by record id and by text ("The terms", items 1, 3, 7 and 11; "The data and the draw", items 5 to 8; rule 1; the settings; traps 1, 2 and 3; the build plan; Runs)

- **The findings** (blocking, two reviews). Lever 5's text requires terms "drafted only from records outside the draw
  and received before every drawn record"; the rule enforced this through one column and asserted the rest "by
  construction". (a) Record identity was never compared: `nhtsa_export.complaints` (`tools/market/nhtsa_export.py:68-113`)
  keys a complaint by its ODINO and keeps only the rows whose received date lies in the window it is given,
  separately for each window, so a complaint with rows on both sides of 2023-01-01 would be a drafting record, its
  own filed categories and narrative shaping the terms, and an eligible record with the same `record_ref`. (b) The
  first count of item 8 (drawn records received before the cutoff) is 0 by the pinned window and cannot see (a).
  (c) The narrative-equality count was declared "deciding nothing", so a drawn record whose exact text was in the
  corpus would be judged with terms partly learned from it. Both checks are computable in the judging plan job from
  the archive it already downloads.
- **Why the findings are right:** the date split is one column; "outside the draw" is a property of record identity,
  which no step compared.
- **Now:** item 8 of "The data and the draw" is replaced (in the body) by "The received-date split, enforced on the
  day": the plan job rebuilds the drafting set from its own download, prints its row count and the sha256 of its
  sorted record ids beside the term run's, and stops with `J4_SPLIT_VIOLATED` when the two archives are equal and the
  figures are not; before the draw it removes from the eligible list, beside the 450 earlier refs, every record whose
  `record_ref` is a record id of the term run's drafting set or of the rebuilt one (`split_excluded_by_ref`) and every
  other record whose non-empty folded narrative is a drafting narrative (`split_excluded_by_text`); after the draw it
  stops with `J4_SPLIT_VIOLATED` when any drawn record was received before the cutoff or has a drafting record id or
  narrative. The term run writes two files into its artifact, never into the repository:
  `prereg/jt/drafting_refs.jsonl` (one `{"record_ref"}` line per drafting-export row, sorted: the bytes
  `lab.j2labels.excluded_jsonl` writes) and `prereg/jt/drafting_text.jsonl` (one `{"sha256"}` line per distinct
  non-empty folded narrative of the export, sorted: hashes, no text); `terms.json` records both files' sha256, the
  export's row count and the sha256 of its sorted record ids, so the term set's sha256 pins them. The term run also
  prints `straddling`, the number of drafting-export rows whose `odino` has a row of the six makes received on or
  after 2023-01-01 in the same archive (a count not yet measured; it decides nothing in the term run, since those
  records are removed from the draw by record id). The removed refs join the excluded set handed to
  `lab.j2labels.build_labels`, unchanged, so they are counted in `exclusion.excluded`, are in the chain file and are
  excluded by later cycles too; the labels record carries the two counts as an `excluded_runs` entry of kind `split`
  (counts only). Items 1, 3, 7 and 11 of "The terms", rule 1, items 5 to 7 of "The data and the draw", traps 1 to 3,
  the settings, the build plan and Runs say so.
- **Why this is not a change of the draw:** the population, the window, the eligibility rule, n 150, seed 4 and the
  sampler are section 2.3's; the removal is the lever's own requirement (section 2.6, item 5, of the same document)
  applied on the runner by the mechanism that removes earlier cycles' records; it removes and never adds, reads no
  code, no answer and no figure, and is fixed before any draw exists. The number removed is printed; a large text
  removal (one narrative repeated across the years) would change the population by exactly that number, and trap 3
  says so.
- **What it changes:** two counts become exclusions, and two tripwires stop the plan job; the excluded set grows by
  the split's removals, and the chain file holds 600 lines plus them. One clause of the fixes (a stop in the term run
  on `straddling`, and the other review's fallback of stops in place of exclusions) is declined under "Declined".

### C5. The drafted term-presence judge: the terms read without the lexical judge's negation handling (rules 8, 10, 13 and 14; trap 6; "Why these arms"; "The data and the draw", item 7; the settings; the build plan; Runs)

- **The finding** (blocking): the pass test's only defence against passing by term matching was the paired clause
  against the drafted lexical judge, and the rule called that judge what term matching gives. It is not the ceiling of
  term matching: `verify.lexical_judge` runs the lexical extractor and `pair`, which apply the pack's negation cues
  within a window of three tokens (`negation_window` 3, the hand pack's `vocabulary.json`) and leave out a predicate
  the text states only as negated (`mycelic/collective/edge/extract.py:17-23`; `pair`, `:550-554`, "negated claims
  never appear"). On this field a negated component mention is usually a problem report (J001 trap 3: "did not
  deploy"; the negation instruction exists for that reason), so a reader that confirms on any occurrence of a drafted
  term and ignores negation is a cruder matcher that can score above the comparator without reading the record; a
  model doing that would beat the drafted-judge clause, and the headline would call it better than word matching.
- **Now:** rule 8 gains **the drafted term-presence judge**: for each question, `mentions_entity` as the drafted
  lexical judge answers that question, and `describes_predicate` yes when any term of T[predicate] occurs
  word-bounded in a folded sentence of the payload's `record.text` (`mycelic.collective.onboard.check.term_presence`,
  `check.py:167-195`, over `packs.canonical.fold_phrase` and `split_sentences`: no negation cue, no pairing), no
  otherwise (an empty list matches nothing); its verdict by the pinned `decide`, as every judge's; computed in the plan
  job before any model runs (`prereg/j4/presence_drafted.jsonl`), scored by `lab.j1.score`, paired with every arm by
  `lab.j1.paired` and with each lexical judge; a comparator, not an arm, not counted. Rule 13's pass test gains the
  clause "the paired difference to the drafted term-presence judge has its 95% lower end above 0"; a P that meets
  every other clause but not this one is `partial`, reported with every lexical figure (C6). Rule 10 gains each arm
  minus the term-presence judge; rule 14 gains the agreement of D's verdicts with the term-presence judge's, per
  kind, and D's confirm rate where that judge confirms and where it refutes; trap 6's first two sentences are replaced
  (in the body); "Why these arms" says which matcher a model reproduces; item 7 of "The data and the draw" prints the
  judge's figure with the two lexical judges'.
- **What it changes:** one comparator and one pass clause, declared before any run; the clause can only make
  `pass_provisional` and `partial` rarer. Section 2.4's three comparators are computed as declared, and lever 5's
  substitution of the drafted judge stands.

### C6. Every lexical judge for `partial`, the added clauses named as this rule's, and the word under lever 5's text alone (rule 13; "How it is read"; the settings, row "Comparators"; Runs)

- **The findings** (one blocking; two smaller). (a) The hand clause guarded `pass_provisional` only; `partial`'s
  second route ("BA < 0.860 but P beats the lexical judge with the lower end above 0") read "the lexical judge" as
  the drafted judge, and so did `failure`. The rule expects that the drafted judge may score below the hand judge on
  this draw ("How it is read"; trap 11), and then a P that beat the drafted judge but not the hand judge would be
  `partial` where A001 and A002 would have said `failure`: an easier bar for a word A002 reserved for beating word
  matching on the hand pack. (b) The hand clause goes beyond lever 5's text, which names only the drafted judge and the
  hand judge "reported beside"; the heading "from lever 5's text" misattributed it, and a reader across cycles must
  know which substitution each word carries; one review proposed dropping the clause and printing the hand comparison
  beside.
- **Now:** rule 13's paragraph is titled **"The added clauses: lever 5's, and two this rule adds"**. Lever 5's: "the
  lexical judge" in the pass test's second clause, in Partial and in Failure is the drafted lexical judge. This
  rule's, stricter than lever 5 and declared before any run: the hand clause (the paired difference to the hand-pack
  lexical judge, lower end above 0) and the term-presence clause (C5), both required for `pass_provisional`; and
  `partial` by its second route requires the paired lower end above 0 against all three lexical judges. A P with BA
  below 0.860 that beats the drafted judge but not each of the other two is `failure`, reported plainly with every
  lexical figure: "P scored BA b [lo, hi]; gap to 0.860 is g; drafted lexical l_d [lo, hi]; hand lexical l_h [lo,
  hi]; term presence l_p [lo, hi]; bound p." Each added clause is one more interval clause, so it can only make
  `pass_provisional` and `partial` rarer, never commoner. The aggregate also prints P's verdict word under lever 5's
  text alone (the block's four clauses with the drafted judge: `lab.j2.verdict_word` unchanged), labelled "the word
  under lever 5's text alone", beside the word of this rule, as Dr's word is printed beside P's. In "How it is read",
  "All three cases leave the pass test as rule 13 states it" now reads "In every case `pass_provisional` and `partial`
  need every lexical judge beaten (C6), so a drafted judge below the hand judge never lowers the bar A001 and A002
  stood on." The settings row "Comparators" says the hand judge is a clause of both words.
- **What it changes:** `partial` is harder to reach by its second route; nothing gets easier; the record says which
  substitution each word carries. Dropping the hand clause is declined under "Declined".

### C7. The latency figures, traced, and the assumed cost of task D labelled as an assumption ("Why the units are this size", bullets "The recorded per-call latency" and "The longer payload")

- **The findings** (blocking, three reviews; one smaller): "a pass of 60 calls of task B under branch N takes 493 to
  566 s, a unit of 120 calls 1,002 to 1,255 s" traced to no source. Task B under branch N is the negation task, whose
  recorded a-4b medians are 8,637.7 ms (Intel Xeon Platinum 8370C) and 9,431.5 ms (AMD EPYC 7763) (CHOICE-A001 Run
  1, the latency table, the negation-task column); 493 s is 60 times the shipped task's 8,211.6 ms; 1,002 to 1,255 s
  follows from no recorded median. "It bounds a D call at twice an A001 call" presented an assumed factor as a bound.
- **Now:** the latency bullet reads: at the negation task's recorded a-4b medians, 8,637.7 ms and 9,431.5 ms by runner
  CPU (CHOICE-A001 Run 1), a pass of 60 calls takes about 518 to 566 s and 120 such calls about 1,036 to 1,132 s
  (derived here from those medians); the longest A001 shard (`s011-a-4b`, 120 calls) took 1,377.2 s of wall time
  (CHOICE-A001 Run 1). A002 run 1 recorded the negation task's a-4b medians at 4,909.1, 6,645.3 and 9,297.1 ms by
  runner CPU (95th percentiles 7,163.6 to 16,111.1 ms), and its longest shard (`s004-a-4b`, 180 calls over three
  tasks) took 2,735.4 s (CHOICE-A002 Run 1). The longer-payload bullet reads: the cost of task D's longer prompt is not
  measured; this rule assumes, without a figure, at most twice an A001 call: a D pass of 60 calls at most about 1,132 s
  and a unit under branch N at most about 1,698 s (derived here), which the 300-minute budget covers more than ten
  times over; if a D call takes longer than that, the budget stop falls in pass B and D, P's set, stays complete
  (rule 15); the warm-up's latency for task D and the run's per-task latency are reported, decide nothing, and replace
  the assumption for the next rule.
- **What it changes:** figures and their sources; no setting. The range two reviews proposed (985 to 1,132 s) is
  declined under "Declined".

### C8. The coverage figure, and what D002's figures say about this judge ("Why these arms"; "The honest expectation for P")

- **The findings** (blocking; one smaller): 0.476 is recorded in CHOICE-D002 Run 1's table as the label-names
  control's coverage on NHTSA (row "label names", column "Coverage"); the hand pack's coverage is recorded nowhere in
  D002's Runs entry. The rule twice read 0.476 as the hand pack's and built on it the expectation that the drafted
  judge's sensitivity rises well above the hand judge's. And "therefore rises" read D002's micro F1, a score of the
  lexical extractor's claims per (record, predicate), as a prior on a judge's balanced accuracy over paired questions,
  which the rule itself calls a different metric.
- **Now:** "Why these arms" reads "coverage 0.840 against the label-names control's 0.476 (D002 Run 1; the hand
  pack's coverage is not recorded)". The honest-expectation bullet reads: D002's figures say that the drafted terms
  match the filed category more often than the hand lexicon under the extractor's own metric; D002 recorded no
  coverage for the hand pack, so no figure says how much more often the drafted terms fire than the hand lexicon, and
  none says anything about this judge's balanced accuracy, which trades sensitivity against specificity on paired
  questions; whether the drafted judge scores above, level with or below the hand judge is measured in the plan job
  (its sensitivity and specificity, printed beside the hand judge's, are the first such figures) and is not predicted
  here; if it scores above, the bar P must clear rises above the 0.663 to 0.680 the hand judge gave on J001's, A001's
  and A002's draws (0.670, 0.663 and 0.680); if not, the hand clause holds the bar at the hand judge's level. A002 run
  1's figures for task N are added, as recorded.
- **What it changes:** a misread figure removed and an inference withdrawn; no setting.

### C9. What `predicate_terms` holds (rule 3, "Codes never reach the judge in any task")

- **The findings** (three reviews): "it holds no code, no category spelling and nothing of the judged record" is false
  as written. A drafted unigram or bigram can equal a word of a category's folded name (`engine`, `steering`,
  `tires`): the refusal removes record ids, site values and vehicle words, not category words, and D002 recorded a
  label echo of 33.8% on NHTSA (CHOICE-D002 Run 1). It is not a leak: the shipped payload already carries the
  predicate's label (`predicate_label`, `verify.py:153-159`), and every question, positive or negative, carries its
  own predicate's terms.
- **Now:** the sentence reads: "`predicate_terms` holds words of other records' narratives, drafted before the cutoff;
  it holds no code and nothing of the judged record. A term may equal a word of a component's name, the asked
  predicate's own label among them, which the shipped payload already carries as `predicate_label` (`verify.py:153-159`),
  so it tells the model nothing about the key that the question did not; it is never a record id, a site value or a
  vehicle word of the drafting export (the refusal, CHOICE-D001 A2 and A6)."
- **What it changes:** a sentence made true; no setting.

### C10. The first term run whose check passes is the term set ("The terms", item 10; rule 17; trap 17)

- **The findings** (two): nothing said that the first term run whose check passes is the term set. The term run
  prints counts (hand predicates reached, terms per predicate, empty lists, the refusal's counts) before the
  amendment, and the archive is re-published from time to time, so repeated term runs would offer a choice among term
  sets by their counts; and trap 17 did not say those counts are no reason for a second term run.
- **Now:** item 10 adds: "The term set is the output of the first term run whose plan job writes
  `prereg/jt/terms.json` and whose check passes; its sha256 is the one the amendment records. A later term run is
  pushed only after that artifact has expired or has failed the judging plan job's check (rule 17), and every term
  run's id and sha256 are listed in Runs, in order, with the reason for each later one." Trap 17 adds: "nor are the
  term run's printed counts (predicates reached, empty lists, terms per predicate, the refusal's counts) a reason for
  a second term run; a second term run happens only for rule 17's two causes, and its sha256 is recorded beside the
  first's."
- **What it changes:** a choice among term sets closed, as re-draws are.

### C11. The term run's time and memory, and their remedy ("Why the units are this size", bullet "The term run"; "The terms", items 10 and 11; the build plan)

- **The findings** (three). (a) Only the `prereg` step's 20 minutes were named; the plan job itself has
  `timeout-minutes: 30` (`.github/workflows/mycelic-lab.yml:28`; the step's 20 at `:77`), and the download, the
  export, the draft and the check sit inside it with the checkout, the self-test, the plan step, the guard, the summary
  and the upload. (b) Memory was not pre-declared: the term table holds every unigram and bigram of every narrative
  with its records (`draft.py:549-576`), and a plan job killed for memory would look like an infrastructure failure
  with no declared remedy, the obvious remedy (a drafter parameter) being a setting this rule forbids. (c) "More rows
  than any one of D002's per-make drafts and fewer than their sum" was wrong: with D002's six makes over the same
  training window, the pooled corpus is the union of D002's six training corpora; and if the in-process draft were
  killed, no defined note would be left.
- **Now:** the bullet reads: the term run's work is the plan job's `prereg` step, whose timeout is 20 minutes inside
  the plan job's 30 (`.github/workflows/mycelic-lab.yml:77` and `:28`); the pooled corpus is the union of D002's six
  training corpora (their sum less the complaints filed under two makes, counted once, C28); D002's 358 s covered six
  drafts with their checks, controls and scoring (CHOICE-D002 Run 1). The draft and the check run in a subprocess
  under `lab.prereg._Steps.run`, with their own timeout below the step's and capped logs among the plan's prereg logs,
  as the L1 step runs (`L1_TIMEOUT_S`, `lab/prereg.py:131`, used at `:638`; the build fixes the timeout and
  `BUILD-A003.md` records it); a timeout or an out-of-memory exit refuses the plan with `JT_DRAFT_FAILED`, a stop
  before any model. The term run prints the step's wall time and the subprocess's peak resident memory
  (`resource.getrusage`). A term run that does not fit is an infrastructure failure under rule 17: the remedy is a
  reviewed workflow change (the step's and the plan job's timeouts, or where the step runs), never a drafter
  parameter; no setting of this rule changes, and the amendment records the attempt.
- **What it changes:** a failure mode with a note and a remedy, and two printed figures; no setting.

### C12. The warm-up's worst-case payload for task D carries the longest list (rule 16; trap 5; the build plan)

- **The findings** (two): the warm-up's asked predicate is the pack's first sorted predicate (`lab/warmup.py:131-143`,
  `judge_example`, `sorted(pack.predicates)[0]`: `air_bags` in the hand pack), whose list in T may be empty or short,
  so the worst-case payload (`_worst_text`, `:126-128`) would not exercise the longest D prompt, and trap 5's guarantee
  held only by chance.
- **Now:** rule 16 reads: "task D's typical payload carrying T's list for the asked predicate, and its worst-case
  payload carrying the longest list in T by characters (its term count printed, never a term), so that the slot check
  covers the longest D prompt the run can send". Trap 5 and the build plan's `lab/warmup.py` item name both payloads.
- **What it changes:** the warm-up checks what trap 5 says it checks. A synthetic longer list is declined under
  "Declined".

### C13. The report markers' line reference ("The terms", item 7)

- **The finding:** `lab/summary.py:95-99` was copied from LOOP-48H section 6.2 and is stale: at `5a8f980` the block
  format (`=== MYCELIC-LAB <label> BEGIN lines=<n> sha256=<hex> ===`) is documented at `lab/summary.py:106-111`.
- **Now:** item 7 cites `lab/summary.py:106-111`.
- **What it changes:** a citation.

### C14. The drafting window's first day, cited for what it is ("The terms", items 1 and 11)

- **The finding:** "(the archive's first day; `COMPLAINTS_RECEIVED_2020-2024.zip`, `nhtsa_export.py:47`)" supports the
  file's name only; that the archive's first received date is 2020-01-01 is an inference. The figure that matters is
  D002's training window, which is sourced.
- **Now:** item 1 reads "from 2020-01-01 (D002's training window's first day, `D002-settings.json`,
  `arms.nhtsa.train`; the archive's name says it holds complaints received 2020 to 2024, `nhtsa_export.py:47`)"; item
  11 adds the earliest and latest received date of the pooled export (dates, never refs) to the printed list and to
  `terms.json`.
- **What it changes:** a citation and two printed dates.

### C15. The term artifact's retention and expiry ("The request", the term run; "The terms", items 7 and 10; trap 18; the settings, row "Requests"; "The build plan", "The order")

- **The findings** (two): the term artifact's 30 days were stated but never recorded as a date, so the judging
  request's push date could not be checked against it in Runs (A001 recorded J001's expiry timestamp,
  CHOICE-A001:118-119, for this reason); and a 30-day retention put a hard deadline on build 2, after which a new term
  run, a new sha256 and a new amendment are forced; the validator allows 1 to 90 days (`lab/request.py:1105`).
- **Now:** the term request's `retention_days` is 90. The amendment that records the term run records the artifact's
  `expires_at` as the Actions API lists it; the judging request is pushed before that date, and its push date is
  recorded in Runs beside it; a push after it finds no artifact, and the plan job refuses with `J4_TERMS_UNVERIFIED`,
  a stop before any model that needs a new term run and a new amendment (C10). The judging request keeps 30 days.
- **What it changes:** a longer window and a recorded date; no setting of the judging run.

### C16. Branch E's citation reading counts a term inside a citation (rule 14)

- **The finding:** task E asks for "the shortest passage that reports the problem", so a copied drafted term usually
  sits inside a longer citation; equality alone under-reads trap 4.
- **Now:** rule 14 reads "the share of grounded citations that equal, after `norm`, one of the asked predicate's
  drafted terms, and the share that contain one as a whole word (`packs.canonical.find_bounded`)".
- **What it changes:** one more reported share under branch E, which is not in force (branch N); it decides nothing.

### C17. The term request's precedent ("The request", the term run)

- **The finding:** the x1 block runs beside model units; the precedent for a request whose only unit is model-free and
  whose one listed model is never served is `lab/requests/openfda-001.json`, whose `purpose` says so ("model-free, so
  the one listed model is never served").
- **Now:** "the precedent of `lab/requests/openfda-001.json`, a model-free request whose one listed model is never
  served; `lab.plan.provision_entries` provisions nothing when no shard serves a gguf model (`lab/plan.py:321-324`,
  called at `:409`)".
- **What it changes:** a citation.

### C18. T covers all 26 specific predicates, and one pooled pack, for the right reasons ("The terms", items 3 and 6)

- **The findings** (three). (a) Reading T as all 26 specific predicates is right, but the file should say why. (b) The
  brief's "same eight predicates" can only be CHOICE-A002 B24's eight whose hand lexicon is not the label, a set that
  includes the non-specific `unknown_or_other`, never asked, so "eight specific predicates" has no exact referent, and
  a reader of B24 could think the drafted judge keeps the hand lexicon on the label-only predicates. (c) The reason
  given against per-make drafting (the lexical judge takes one pack) is wrong: per-make drafting with the questions
  split by make is runnable; pooled drafting is still preferable, for other reasons.
- **Now:** item 6 adds: "T covers every specific predicate, because lever 5 names the lexical judge 'with the same
  drafted terms', not a subset: a set over eight predicates would leave the other specific predicates with the hand
  lexicon on the judge's side and no terms on the model's, and the comparator would be neither the hand judge nor a
  drafted one. The eight are B24's predicates whose hand lexicon is not the label in lower case, as the hand pack's
  `vocabulary.json` shows: `electronic_stability_control_esc`, `engine_and_engine_cooling`, `fuel_propulsion_system`,
  `fuel_system_gasoline`, `latches_locks_linkages`, `service_brakes_hydraulic`, `visibility_wiper` and the
  non-specific `unknown_or_other`, never asked; seven of them are specific. A drafted list for a label-only predicate
  may or may not hold the label's own word (D002's label echo 33.8%); the hand judge beside it and the hand clause
  carry the label-only reading." Item 3's reason now reads: per-make drafting with the questions split by make would
  be runnable, but it leaves more predicates without terms (D002: 30 placeholder predicates over six packs), the hand
  comparator is one pack over the six makes, and the pooled refusal removes the six makes' vehicle words together,
  which D002's per-make refusal did not; its count is printed.
- **What it changes:** reasons; T and the pack are unchanged.

### C19. The j4 verdict word, as a function (rule 13; the build plan, "Two new kinds" and `lab/j4.py`)

- **The findings** (two): with the added clauses the word is no longer `lab.j2.verdict_word`'s, and the build plan
  said `lab.j4` imports `pass_test` and `verdict_word` from `lab.j2` unchanged; imported unchanged, they cannot
  evaluate the hand clause, cannot make a missing hand-judge figure `incomplete`, and would return `pass_provisional`
  where rule 13 says `partial` (`lab/j2.py:238-278`). The rule should name the function's shape, as A002 did for
  `composite_verdict_word` (`lab/j3.py:477-502`).
- **Now:** rule 13 adds: "The word is computed by a new function `lab.j4.verdict_word`, shaped as
  `lab.j3.composite_verdict_word`. The word under lever 5's text alone is `lab.j2.verdict_word` over the scores, the
  drafted-judge difference and the bound, unchanged. Then: `incomplete` when that word is `incomplete`, or when the
  hand-judge or the term-presence difference is not a number; `pass_provisional` only when that word is
  `pass_provisional` and both added clauses hold; `partial` when that word is `pass_provisional` and an added clause
  fails, when that word is `partial` with BA at or above 0.860, or when that word is `partial` with BA below 0.860 and
  both added clauses hold; else `failure`. `lab.j4.pass_test` is `lab.j2.pass_test` over the drafted-judge difference
  with the clauses `beats_hand_lexical` and `beats_term_presence` added, `passed` only when all six hold." The build
  plan's "Two new kinds" reads "calls `lab.j2.pass_test` and `lab.j2.verdict_word` unchanged inside its own
  `pass_test` and `verdict_word`"; a test pins every cell, and pins `lab.j4.verdict_word` equal to
  `lab.j2.verdict_word` on every input whose two added clauses hold and whose word is not a second-route `partial`
  failing them.
- **What it changes:** the word's computation named. One clause of a finding's fix ("`partial` when the j2 word would
  be `pass_provisional` or `partial`") is narrowed by C6 and declined under "Declined" as written.

### C20. Two chains in one plan job ("The data and the draw", item 4)

- **The finding:** "A chain establishes at most one cycle" did not say whether K2 and K3 may each establish one cycle
  in the same plan job; A002's code allows a chain reading only when the other cycle is established by its own
  sources (`lab/j3labels.py:193-210`).
- **Now:** item 4 adds: "Two chains may each establish one cycle in the same plan job when each cycle a chain's
  reading draws on is established without that same chain (for example J001 by K2 less A001's own set, then A002 by
  K3 less the union of J001's and A001's sets); the by-chain reading is printed per cycle with the chain it used."
- **What it changes:** a reading made explicit; no stop rule weaker.

### C21. P_draft and P_D; P_D's fixtures; the placeholder's form ("The terms", items 4, 7, 9, 10 and 11; the settings; the build plan)

- **The findings** (three): "the drafted pack" named two packs, the drafter's own pack (its predicates and codes,
  uploaded under `prereg/jt/pack/`) and P_D (the hand pack with T's lexicons), and the hashes printed, stored and
  pinned were not assigned to one or the other. P_D's fixtures and generator narratives are the hand pack's, written
  with the hand lexicon, and the loader validates fixtures structurally only (`loader.py:1479-1517`). And
  `unlearned-<id>` left the build a choice of form.
- **Now:** the drafter's pack is **P_draft** (uploaded under `prereg/jt/pack/`) and the hand pack with T's lexicons
  is **P_D** (rebuilt, never uploaded). `terms.json` holds P_draft's four loader hashes and its files' sha256, and
  P_D's four hashes; the amendment and the request pin P_D's `vocabulary_hash` and `config_hash`. Item 9 adds: "P_D's
  fixtures and generator narratives are the hand pack's and are written with the hand lexicon; the loader checks them
  structurally only (`loader.py:1479-1517`); nothing in this cycle extracts from them, and `fixtures_hash` equal to
  the hand pack's is the pin that says they were not touched." The placeholder in P_D is
  `mycelic.collective.onboard.draft.placeholder(<hand id>, ids)` with the onboard template's ids (`draft.py:627-629`:
  the prefix `unlearned-` of `data/template/ids.json`, then the id with each `_` written `-`); a test pins that no
  placeholder folds equal to a T term or to a hand term.
- **What it changes:** names and a pin; no setting.

### C22. The drafter's summary counts, carried ("The terms", items 7 and 11)

- **The finding:** `draft.summarise` (`draft.py:990-1024`) also records `rows_without_specific`, the candidate and
  eligible term counts and `placeholders`; the first two say how much of the corpus had no specific label and how many
  candidates the floor removed, which trap 11's thin-list reading needs.
- **Now:** items 7 and 11 add "`rows_without_specific`, the candidate and eligible term counts (`terms.candidates`,
  `terms.eligible`) and P_draft's placeholder count (`draft.summarise`)".
- **What it changes:** counts printed and kept.

### C23. The request's validation of the term run's window and of the term entry ("The request"; the build plan, `lab/request.py`)

- **The findings** (two): the validation "`from` before `cutoff`" let a request draft from any start date before the
  cutoff (2021-06-01, say), against "so that no request can draft from another window"; the judging run would catch a
  wrong window only after a term run and an amendment. And the term entry's full-length fields need a new pattern:
  the exclusions' prefix pattern accepts 12 to 64 lower-case hex digits (`SHA256_PREFIX_RE`, defined at
  `lab/request.py:285`; the finding cited its use at `lab/prereg.py:417`).
- **Now:** `lab.request` validates the term block's `cutoff` equal to `NHTSA_WINDOW[0]` in ISO form and `from` equal
  to 2020-01-01, the first day of `D002-settings.json` `arms.nhtsa.train` (a test pins `lab/jt.py`'s window to it).
  `_j4_terms` validates `sha256` and `vocabulary_hash` with the full-length pattern `[0-9a-f]{64}`, `run_id` as an
  integer, `artifact` with the lab's artifact-name shape, `file` equal to `prereg/jt/terms.json` and `cutoff` equal
  to `NHTSA_WINDOW[0]` in ISO form.
- **What it changes:** two validators narrowed.

### C24. "N raised a-4b", corrected ("The base task and the arms", "Why these two and no third")

- **The findings** (two): "N raised a-4b from 0.307 to 0.577 as answered and from 0.667 to 0.653 under the mapping":
  0.653 is below 0.667, and CHOICE-A001 Run 1 records D − B as -0.013 [-0.063, 0.037], not told apart.
- **Now:** "N raised a-4b from 0.307 to 0.577 as answered (C − A 0.270 [0.217, 0.327]) and left it level under the
  mapping (0.667 to 0.653, D − B -0.013 [-0.063, 0.037], not told apart; CHOICE-A001 Run 1), the two mapped arms level
  with the lexical judge".
- **What it changes:** a sentence corrected.

### C25. What 150 records resolve in this cycle ("How it is read", "150 records resolve little")

- **The finding:** the bullet gave A002 B14's figures without their provenance in this cycle's terms: the 0.05 is A001
  run 1's paired half-width of a model arm against the hand lexical judge (0.047 to 0.054); D and the drafted judge
  read the same terms, so their per-record agreement, which sets the paired interval's width, is unmeasured; and the
  height of the bar (the drafted judge's balanced accuracy) is unknown until the plan job prints it.
- **Now:** the bullet adds: "The 0.05 is A001 run 1's paired half-width of a model arm against the hand lexical judge
  (0.047 to 0.054, B14); A002 run 1's paired intervals to the lexical judge were 0.097 to 0.113 wide (derived here
  from its arms table). D and the drafted judge read the same terms, so their per-record agreement, which sets the
  paired interval's width, is unmeasured and may make the clause easier or harder to tell from 0 than A001's. The
  drafted judge's balanced accuracy b_L is not known until the plan job prints it, so the difference P needs (0.860 −
  b_L at least, and about 0.05 to be told from 0) is not known before the run; a D within about 0.05 of b_L is
  `failure` for want of power, not evidence that the terms did nothing, and `D_minus_B` carries the lever's reading
  either way."
- **What it changes:** a reading; no test.

### C26. Trap 4: a three-letter term is not grounded (trap 4)

- **The finding:** a term can be three letters (`token_min_letters` 3, `mycelic/collective/onboard/data/defaults.json`),
  and `grounded` needs `norm(citation)` of at least `GROUNDING_MIN_CHARS` 4 (`lab/j3.py:127`, `:264-270`), so a
  three-letter term copied as the citation is not grounded under G.
- **Now:** "a drafted term of four or more characters that occurs in the record is a grounded citation under G (a
  three-letter term, which the drafter allows, is not), and the drafted lexical judge confirms there too".
- **What it changes:** a sentence corrected; branch E only, which is not in force.

### C27. The wiring touch points the build must change (the build plan)

- **The finding:** several touch points were not named. `run_unit`'s budget list (`lab/units.py:1218-1219`,
  `("sim", "j1", "l1", "j2", "j3")`): `build_argv` raises for a J unit without a budget (`:357-359`), so every j4 unit
  would fail before any call. `Adapter.routed_tasks` is a static tuple (`:281-304`), while j4's two task names depend
  on `base_task`, so `write_routing` (`:452-471`) and `required_tasks` (`:310-321`) must read the unit's
  `params.base_task`. `unit_notes` and `public_text` (`:869-900`): without a branch a jt unit gets the `synthetic`
  note. `lab/prereg.py`: `preregister`'s `_units` tuple and manifest keys (`:706-711`), `main()`'s `prereg:` line
  (`:788-791`) and `load_stand_in`'s list branch (`:404-419`: two entries for j3, three for j4). `lab/aggregate.py`:
  the source-collection loop (`:2025-2035`) and the report keys (`:2104`). `lab/notes.py`: the section titles
  (`:590-605`). `lab/plan.py:246`: the units' params must carry `base_task` and `terms`. `lab/dryrun.py:122`:
  `--jt-data` and `--j4-data` exclusive with `--j2-data` and `--j3-data`.
- **Now:** the build plan's wiring lists name each of them (in the body).
- **What it changes:** the build plan.

### C28. Cross-make duplicates in the pooled export ("The terms", item 3)

- **The finding:** `complaints` removes duplicate ODINOs only within one make (one call per make,
  `nhtsa_export.py:68-113`), so a complaint naming vehicles of two makes appears twice in the pooled CSV and the drafter
  would count it twice; the draw path rejects the second copy (`map_rows`, `duplicate_record_ref`,
  `mycelic/collective/packs/connector.py:250-251`). The rule did not say which treatment the drafter gets.
- **Now:** rows are concatenated in `NHTSA_MAKES` order, and a row whose `odino` already appeared is dropped, as the
  draw's mapping drops it; the count dropped is printed and kept in `terms.json`.
- **What it changes:** the drafter's corpus counts each complaint once.

### C29. The code hash covers `lab/jt.py`, and the download module is loaded inside the export function (the build plan; "The terms", items 7 and 10)

- **The findings** (two): build 2 and the judging run rebuild P_D with `lab/jt.py` (`term_set`, `drafted_pack`), so a
  change to that module after the term run would silently change how T or P_D are built, and the `vocabulary_hash`
  check catches a changed P_D only as a stop after the fact. And loading `tools/onboard/fetch_nhtsa.py` runs
  `sys.path.insert` and `import nhtsa_export` at import time (`fetch_nhtsa.py:39-41`): if `lab/jt.py` loaded it at
  import, every process importing `lab.prereg` would get `tools/market` on its path.
- **Now:** `terms.json` records a code hash over `lab/jt.py` beside the onboard code hash; `lab.j4terms` refuses with
  `J4_TERMS_UNVERIFIED` when the hash of `lab/jt.py` at the judging commit differs; any change to `lab/jt.py` after the
  term run needs a new term run and a new amendment. `lab/jt.py` loads `fetch_nhtsa` by path inside `pooled_export`
  only, never at import.
- **What it changes:** one more pin and an import rule.

### C30. A002's re-aggregation pinned byte for byte (the build plan, "Two new kinds")

- **The finding:** "A002 can be re-aggregated at any later commit with its report unchanged", yet both builds edit
  modules A002's re-aggregation runs through (`lab/aggregate.py`, `lab/summary.py`, `lab/units.py`, `lab/prereg.py`,
  `lab/plan.py`), and no test pinned the claim.
- **Now:** before build 1, the j3 template dry run's plan summary, `report.json` and `report.md` are recorded on the
  build's base commit (`5a8f980`, or a later commit with the same lab code); tests pin them byte for byte after each
  build, as D003's build recorded D002's golden outputs.
- **What it changes:** one more pin.

### C31. The term request waits for A002's run to end ("The base task and the arms", first paragraph; "The build plan", "The order", step (2))

- **The finding:** LOOP-48H section 5 allows one lab run in flight at a time, and A002's run might still have been
  running when build 1 merged.
- **Now:** the term request is pushed only after A002's lab run has ended (its aggregate job finished, or the run
  cancelled or failed) and no other lab run is in flight; the term run's result may still precede A002's Runs entry.
  A002's run ended at 18:23:04 UTC on 2026-10-13 (the Actions API), so for A002 the condition holds now.
- **What it changes:** an order.

### C32. Task D's instructions, built from either base (rule 3)

- **The finding:** `lab.j3._with_field` splits only `NEGATION_INSTRUCTIONS` (`lab/j3.py:112-116`), so under branch E
  task D's text could not be built with it as is.
- **Now:** rule 3 reads: task D's instructions are `base.rpartition(" Judge only this record.")[0] + " " + <the terms
  sentence> + " Judge only this record."`, the base being `NEGATION_INSTRUCTIONS` under branch N (in force) or
  `EVIDENCE_INSTRUCTIONS` under branch E, refused unless the base ends with that sentence; a test pins both.
- **What it changes:** a construction named; under branch N the text is N's with the terms sentence before its last
  sentence, as declared.

### Declined

No finding's fix would have changed the branch A002's recorded figures select, so none is declined for that reason.
These clauses are declined, each for the reason given.

- **Task N's set complete for both disjuncts of branch E** (the blocking finding on the trigger's three readings, the
  first clause of its fix). Declined for the `pass_provisional` disjunct: a provisional pass on A002's P with task N's
  set incomplete would then have selected branch N, and section 2.5's confirmation obligation (C1) would have been lost
  to an incomplete control set. Task N's completeness is required for the lift disjunct only (C2), which is where the
  finding's reason (a lift over an incomplete set decides nothing) applies. Neither text bears on the selection: A002
  run 1's two sets are complete and its P is `failure`, branch N under both.
- **Branch N when A002 is abandoned with no result** (the blocking finding on A002's result run, one clause). Declined
  in favour of the other review's "a new choice file" (C2): this cycle's three-entry exclusion presumes an A002 draw a
  model judged, and an A003 run with A002 abandoned would have no third entry to verify. It does not arise: A002 run 1
  is A002's result run.
- **A stop in the term run on `straddling`, and stops in place of exclusions** (the two blocking findings on the
  split: one asked the term run to stop with `JT_SPLIT_VIOLATED` when a drafting row's `odino` has a row after the
  cutoff and kept the duplicate-narrative count deciding nothing; the other offered, as an alternative, both counts
  stopping the plan job before any draw). Declined: C4 removes every drafting record id and every drafting narrative
  from the eligible list before the draw, which closes both routes for every drawn record whatever the counts are,
  where a stop would end the cycle over records that need only be left out; the straddling count is printed, and the
  text check is an exclusion, stricter than a count that decides nothing. C4 keeps a stop where a count above 0 can
  only mean a fault: a drawn record received before the cutoff, or a drawn record with a drafting id or narrative
  after the removal.
- **Dropping the hand clause** (the smaller finding on rule 13's added clause, its first option: print the hand
  comparison beside and let it decide nothing). Declined: it would lower the success clause as declared, and this
  rule's floors may rise, never fall. Its second option, naming the clause as this rule's addition, is C6.
- **The latency range 985 to 1,132 s** (two of the three blocking findings on the latency figures). Declined: 985 s is
  120 times the shipped task's median, 8,211.6 ms; task B under branch N is the negation task, whose recorded medians
  are 8,637.7 and 9,431.5 ms (CHOICE-A001 Run 1), so 120 calls take about 1,036 to 1,132 s (C7), as the third
  review said.
- **`partial` whenever the j2 word is `pass_provisional` or `partial`** (the smaller finding on the build plan's j4
  functions, one clause). Declined as written, because it conflicts with C6: a j2 `partial` by the second route (BA
  below 0.860) is `partial` here only when both added clauses hold, and `failure` otherwise (C19).
- **A002's questions prefix as a field of the request's third entry** (the smaller finding on item 3, one clause).
  Declined in form, adopted in substance: A002 pinned A001's questions prefix as a code constant
  (`lab/j3labels.py:56`), and A003 does the same (`lab.j4labels.A002_QUESTIONS_SHA256_PREFIX`, C3), so that no
  request can move the pin; the settings row lists it.
- **A synthetic list of 50 terms of 64 letters for the warm-up's worst case** (the smaller finding on rule 16 and trap
  5, one clause). Declined: T is fixed and verified before any shard starts, so its longest list is the longest D
  prompt the run can send, and a synthetic list would check a prompt that no question sends (C12).

### Floors after this amendment

| Floor | Before this amendment (`efa79fe`) | After | Direction |
|---|---|---|---|
| The target and the metric | balanced accuracy 0.860 against the filed-category key, record level, codes hidden, 150 fresh NHTSA complaints (300 questions), scored as J001 | unchanged | unchanged |
| The verdict rule | LOOP-48H section 2.5 verbatim; the words in A5's order; the drafted lexical judge as "the lexical judge"; `pass_provisional` also needs the hand clause | the same block; `pass_provisional` also needs the term-presence clause (C5); `partial` by its second route needs every lexical judge beaten, else `failure` (C6); `incomplete` when the hand or the term-presence difference is not a number (C19); the word under lever 5's text alone printed beside (C6) | raised |
| The pass test's lexical clauses | the drafted lexical judge (lever 5's) and the hand-pack lexical judge (this rule's) | those two and the drafted term-presence judge (C5), each a paired lower end above 0 | raised |
| The comparators | the drafted lexical judge, the hand-pack lexical judge, the record-blind control, the predicate-only bound | the same four and the drafted term-presence judge (C5); none is an arm or counted | raised |
| The interval rule | cluster bootstrap over records, B 10,000, 95%, seed `j1:4`, the same draws for every judge and arm, paired on the records both sides score; equality fails every strict clause | unchanged | unchanged |
| The confirmation rule | a first pass is provisional; success needs P named again and passing on the next cycle's fresh draw; success unreachable in this cycle | unchanged; the confirmation case for an A002 provisional pass pre-declared (C1), not in force | unchanged; a case closed |
| Completeness | per task set: 5 of 5 parts, `measurement` true, `records_not_judged` 0, dropped share at most 0.01; a missing lexical, bound or interval figure makes the word `incomplete` | the same; a missing term-presence figure too (C5, C19) | unchanged in kind |
| The primary pair | P = (a-4b, D), D `<base>_terms`, read as answered; Dr's word beside | the same; under branch N, read from A002 run 1: P = (a-4b, `negation_terms`); (a-4b, `evidence`) only in the confirmation case, not in force (C1) | unchanged |
| The adaptation rule | branch E when A002 run 1 is complete on task E and P is `pass_provisional` or `E_minus_N`'s lower end is above 0; else branch N | the same conditions on A002's result run, read from named cells; the lift read only with task N's set complete; a stop before any model never a trigger; the confirmation case (C1, C2) | discretion removed; branch N selected under both texts |
| The draw | `random.Random("nhtsa:4").sample` of 150 over the eligible list of the day's file less the excluded refs; seeds 4; parts 5 | unchanged | unchanged |
| The exclusion | J001's, A001's and A002's sets, 450 refs, eight sources, the stop rules; A002's prefixes placeholders | the same, A002's entry filled from A002 run 1 with its questions prefix pinned (C3); two chains may each establish a cycle when each draws on cycles established without it (C20); the split's removals by record id and by text added (C4) | raised |
| The received-date split | cutoff 2023-01-01, drafting window 2020-01-01 to 2022-12-31, "by construction"; a drawn-before-cutoff count stopping above 0; a text count deciding nothing | the same cutoff and window; the drafting set's record ids and narratives removed from the eligible list before the draw; tripwires stop after it; the term run's drafting set and the plan job's compared (C4) | raised |
| The term set's pin | sha256 committed by amendment; the plan job refuses a file that does not hash to it, or a P_D with another `vocabulary_hash` | the same; the first term run whose check passes is the term set (C10); `terms.json` also pins the drafting refs and text files (C4) and `lab/jt.py`'s code hash (C29); the artifact's expiry recorded (C15) | raised |
| The tasks and the tokens | the base task imported unchanged; task D the base with one sentence and one payload key; branch N 256 tokens, branch E 512 | unchanged; branch N in force (256 tokens); task D's text built by one named construction (C32) | unchanged |
| The comparison count | 4 this cycle, 25 since the loop began; no multiplicity correction | unchanged | unchanged |
| The first run is the result | a re-run only for a failure before any model judged; one named setting change before a re-push (a warm-up cut's `max_tokens`) | unchanged; a term run that does not fit is such a failure, remedied by a reviewed workflow change, never a drafter parameter (C11) | unchanged |
| What is printed | counts, scores, hashes; no ref, narrative, citation or term in any report, summary, console line, commit or document | unchanged; the term run's drafting refs and narrative hashes live in its artifact only (C4) | unchanged |

### Placeholders to be filled by later amendments before the run

Filled by this amendment, from A002 run 1, lab run 38439612256 (C3), or read under its own heading:

1. A002's labels sha256 prefix: `0a64a62d94a7` (from A002 run 1, lab run 38439612256).
2. A002's chain file prefix and line count: `65d51f0fca51`, 450 lines (from A002 run 1, lab run 38439612256).
3. A002's questions prefix: `84486fa38568` (from A002 run 1, lab run 38439612256).
4. A002's plan artifact: `lab-plan-38439612256-1`, attempt 1, artifact id 11833749961, expiring 2026-11-12T17:34:56Z
   (from A002 run 1, lab run 38439612256; the expiry as the Actions API lists it).
5. A002's complaint file prefix: `216ecb36f9f5` (from A002 run 1, lab run 38439612256).
6. The resolved base task: branch N, task N, P = (a-4b, `negation_terms`). Not filled as a change of the rule: read
   from the committed rule under "The base task, read from A002 run 1".

Filled by the amendment after the term run, 2026-10-14, from term run 1, lab run 38612800578, the first term run and
one whose check passed (C10); each figure is under T1 to T9 there:

7. The term run's lab run id, attempt and artifact name `lab-plan-<run id>-<attempt>`: 38612800578, attempt 1,
   `lab-plan-38612800578-1` (T1).
8. **The term set's sha256**: the full 64 hex of `prereg/jt/terms.json`: `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc` (T1).
9. P_D's `vocabulary_hash` and `config_hash` (64 hex each) and its `detector_hash` and `fixtures_hash` (expected
   equal to the hand pack's); P_draft's four hashes and its files' sha256 (C21): T2; the files' sha256 are held in
   `terms.json` and pinned by its sha256, and no console line prints them (T10).
10. The complaint archive's sha256 and size on the term run's day: T3.
11. The drafting export's row count, the sha256 of its sorted record ids, the sha256 of
    `prereg/jt/drafting_refs.jsonl` and of `prereg/jt/drafting_text.jsonl`, the `straddling` count and the
    cross-make duplicates dropped (C4, C28): T4.
12. The counts of "The terms", items 7 and 11: the rows per make and pooled, the corpus size, the categories passing
    the floor, the specific predicates drafted, the hand predicates reached of 26, the terms per reached predicate,
    the empty entries, the refusal's counts, `rows_without_specific`, the candidate and eligible term counts, P_draft's
    placeholders, and the earliest and latest received date (C14, C22): T5.
13. The check's result by check: T6.
14. The onboard code hash, the sha256 of `data/defaults.json` and of `data/lang/en.json`, and `lab/jt.py`'s code hash
    (C29): T7.
15. The term artifact's `expires_at` as the Actions API lists it (C15): 2027-01-12T18:09:48Z (T8).
16. The term run's wall time and peak resident memory (C11): T9.

Conditional, and not a placeholder of this rule's figures: a chief-scientist call on decision 9 or on the
verbatim-substring check, if one arrives before the judging request is pushed, is adopted by a dated amendment (the
declaration); none is recorded under "Decisions received" in the status log at `5c9544e`.

## Amended after the term run, before build 2, 2026-10-14

This amendment records term run 1, lab run 38612800578 (Runs, "Term run 1"), the first term run and one whose check
passed, so its output is the term set (C10). It fills placeholders 7 to 16 ("Placeholders to be filled by later
amendments before the run") and copies the term set's sha256 and P_D's two pinned hashes into "The terms" (item 10),
the settings (rows "The term set T" and "The drafted packs") and "The request" (`experiments.j4.terms`). It changes
no setting, no floor, no arm, no task and no reading. It is written after the term run and before build 2 (the
judging kind `j4`): no judging code exists, no draw of this cycle exists, and no model has judged any record of it.
Its sources are the plan job's `jt` lines (job 116020175039), the aggregate job's report block (64 lines, sha256
verified, Runs) and the Actions API's artifact listing for the run.

### T1. The term set

- Lab run 38612800578, attempt 1, at commit `5d2c1d3` (the request alone on the merge `d270bd3`).
- Artifact `lab-plan-38612800578-1`, artifact id 11918947585, 23 files, 3,007,683 bytes, digest
  `a33c9ff30756e708c99e2a96771424934b05b48135d130d7c975353f26ec6eab`; the file `prereg/jt/terms.json` in it.
- **The term set's sha256: `42c591ccbb66b81455ca19840a54e18cbb69b5613dd2eab8f3f8c70f9cea39cc`.** The judging plan
  job refuses any other bytes (`J4_TERMS_UNVERIFIED`, "The terms", item 10).

### T2. The packs' hashes

| Pack | `config_hash` | `vocabulary_hash` | `detector_hash` | `fixtures_hash` |
|---|---|---|---|---|
| P_D (the hand pack with T's lexicons) | `6219eb0993b5668ecea91cc0811a6e446971d62001fe85e54662dc0a2b56698a` | `c0260f0edab4638915b1fd0065e236ca20b497c0b217495c77adf018800fe579` | `325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779` | `9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f` |
| P_draft (the drafter's own pack) | `f6bdf3c65b31972d2bb73a1e89ff70c9d4a41b734dc27ac9cb95008e49d70420` | `09069d9f19d775208ffac44f20c195605955d010cf1f6d5a037b0f39d23527da` | `39499e9cc0a09942239d42dacba991b02db401624f201af85b975e5a92f08416` | `693cf303644a33d2ea1087d8eafc4403ba9256b49178959d7bc96177ff81229a` |
| The hand pack | `a588e72d7dbd0307d42234ceb79a85a915f0454d947469a4bfa0a42595f2f7bd` | `867df7fc83c556e267842f219e1087b4bbd6af052ff5d201206d0aa8c789aa96` | `325b125afa2d8ae473384ed5802b6dd284a93ad3e83c853a065e8c5fb4582779` | `9d9d8bd6770efc127edbbc7cb79d75524655719f949cb6da4ce83d51cdec1e8f` |

P_D's `detector_hash` and `fixtures_hash` equal the hand pack's, the pin that says its detectors, fixtures and
generator narratives were not touched (C21); its `config_hash` and `vocabulary_hash` differ from the hand pack's, as
a changed `vocabulary.json` makes them. The request pins P_D's `vocabulary_hash`; the judging plan job and the
aggregate rebuild P_D from `terms.json` and refuse any difference.

### T3. The complaint archive on the term run's day

sha256 `4e5ae79183c489a385dac63d5d716995e6e053bdac26b9dbc91d6ce63244a4e7`, 75,874,030 bytes. The judging plan job
prints its own day's archive sha256 beside this one (C4).

### T4. The drafting block (C4, C28)

The drafting export's rows 77,590; the sha256 of its sorted record ids
`6072b844d878d2783f02603b5ac303e56745a40e623201158d3dee0e4c4c2be9`, equal to the sha256 of
`prereg/jt/drafting_refs.jsonl` (by construction: `lab/jt.py`, `drafting_block`, hashes the file's bytes for both);
the sha256 of `prereg/jt/drafting_text.jsonl` `4e92180e66e6fe71470a4437b378c4aa4b913bab137d53b13558636a82174823`,
77,346 distinct non-empty folded narratives; `straddling` 0; cross-make duplicates dropped 0.

### T5. The counts of "The terms", items 7 and 11 (C14, C22)

- Rows per make: `FORD` 26,919; `CHEVROLET` 15,649; `JEEP` 10,284; `HONDA` 10,485; `NISSAN` 7,969; `DODGE` 6,284;
  pooled 77,590; earliest received 2020-01-01, latest 2022-12-31.
- The drafter: corpus 77,590 records; categories passing the floor 36; specific predicates drafted 30; other-bucket
  share 0.143; `rows_without_specific` 5,934; candidate terms 319,171; eligible terms 21,173; P_draft's placeholders
  7.
- The refusal: refused categories 0; refused terms 678; refused terms that would have been assigned 35; refused terms
  within the cap 8.
- The term set: hand predicates 26; reached 25; empty entries 3; terms per reached predicate min 0, median 43, max
  50; total 753.

### T6. The check, by check

Passed. `loader` passed; `ngram` passed, 0 failures; `refused_strings` passed, 0; `refused_terms` passed, 0;
`template` passed; `term_floor` passed, 0; `value_floor` passed, 0.

### T7. The code hashes (C29)

- The onboard code hash: `a1cf1bf6d4202823c4ab11d28d49fc46926319e897379964bc3f89d77b7f9c0b`.
- `mycelic/collective/onboard/data/defaults.json`: `9ca66ffc98580f3e2f22da8d702b943db52ba198c79be14e763bd97ad8efc95b`.
- `mycelic/collective/onboard/data/lang/en.json`: `9c76178a457bba9d4d9f98460a4d8263f5112b048b978cf651a46162bbd10cdb`.
- `lab/jt.py`'s code hash: `f5743df58d418643ef231db62485219024cbb761cb22f8889e268b589e723d76`.

From this amendment `lab/jt.py` is frozen: the judging plan job refuses when `lab/jt.py` at the judging commit hashes
to anything else (C29), so build 2 does not edit it, and a test of build 2 pins its code hash to this value. A change
to it would need a new term run and a new amendment.

### T8. The term artifact's expiry (C15)

`expires_at` 2027-01-12T18:09:48Z, as the Actions API lists it for artifact 11918947585 (created
2026-10-14T18:11:10Z, `expired` false). The judging request is pushed before that instant, and its push date is
recorded in Runs beside it.

### T9. Time and memory (C11)

The `prereg` step ran from 18:10:07 to 18:11:08 UTC (its timeout 20 minutes, inside the plan job's 30); the step's
wall time 61.2 s; the draft and the check, in their subprocess, 36.6 s under a 900 s timeout; the subprocess's peak
resident memory 456,148 KiB. The runner had 15.6 GiB and 4 CPUs (`AMD EPYC 7763 64-Core Processor`).

### T10. What this amendment does not copy, and why

- **P_draft's files' sha256** (placeholder 9, C21). `terms.json` holds them and its sha256 pins them byte for byte;
  as built, no console line prints them, and the sandbox that writes this file cannot download the artifact (its
  storage host is outside the network policy, and no other route is taken). They are not copied here; nothing in the
  judging run reads them except through `terms.json`.
- **The unit's own verify line.** `python -m lab.jt verify` writes its line to the unit's log inside the shard
  artifact, not to the job's console. The console shows the runner's unit line (Runs), and the report block shows the
  aggregate's reading of the unit's result: `verified by the unit yes`.
- **The `report/report.json` block's sha256**, which begins before the log's readable tail (Runs).

### T11. Readings that change nothing

- Three empty entries and the thinner lists are no reason for a second term run (C10; trap 17); trap 11 reads them in
  the judging run's report, beside the per-predicate figures.
- `straddling` 0 says that in this archive no drafting complaint has a row received in the judged window. The
  judging plan job still rebuilds the drafting set from its own download, compares it with T4, and removes from the
  eligible list by record id and by text before the draw (C4); its counts are its own day's.
- The comparison count is unchanged: 4 this cycle, 25 since the loop began.
