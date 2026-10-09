# Judge test J001: can a small model answer the site verifier's narrow question on real complaints?

R001 and R002 asked small models to read whole complaints into claims. Every model scored below a lexical extractor.
That is open extraction, and it is not what a site model does in the product. There, HQ asks a narrow question
about one entity and one failure, and the site's model judges each of its own records against it (pushdown
verification, `mycelic/collective/edge/verify.py`). That judging has never been measured on real text. J001 measures
it on the same 150 NHTSA complaints as R001 and R002, through the product's own judge, against the lexical judge the
verifier uses when no model runs. The rule and the reading below are committed before any model judges a narrative.

How the lab will run it (a new experiment kind, `j1`) is in `BUILD-J001.md`.

## The declaration

- The rule was written by the AI system that wrote this repository's code.
- It has seen R001's result: every model at predicate F1 0.000, and the lexical extractor at 0.434
  (`CHOICE-R001.md`).
- It has seen R002's result: a-0p5b 0.026 [0.015, 0.038], a-1p5b 0.178 [0.150, 0.209] and a-4b 0.218
  [0.185, 0.254] from its one finished repeat, each interval below the lexical 0.434 (`CHOICE-R002.md`, run 1).
- It has also seen the other lab runs and replays as `docs/lab/RESULTS.md` records them (main-001, g0-001, V001 to
  V003), with their timings, and the pack notes in `PACK-V2.md`.
- It has not seen any judge reply on these narratives: no judge has run on them. It has not read R002's stored
  replies, which sit in the run's artifacts and cannot be read from this environment. The 150 narratives are
  rebuilt in the lab's plan job, and no copy of them is in the repository.
- The design comes from the code and from R001 and R002, not from any J001 output.
- R001's declaration still holds. The models may have seen public complaints in training. The answer key is the
  components each complaint was filed under, not labels anyone checked.

## The rule

1. **Records: R001's and R002's, by the same code.** `lab.goldlabels`, source `nhtsa`, the pack
   `docs/collective/replay/vehicles/pack`, 150 records drawn by `random.Random("nhtsa:1")`:
   - complaints received 2023-01-01 to 2024-12-31 for FORD, CHEVROLET, JEEP, HONDA, NISSAN and DODGE;
   - eligible: a narrative, one vehicle, and at least one specific component code.

   The labels are rebuilt from the complaint file as published on the day of the run. If their sha256 differs from
   R001's (`bc6d092d8bca…`), both are reported, and J001 is not on R001's records.
2. **Questions: two per record, fixed in the plan job before any model runs.**
   - The entity is the record's structured vehicle, as its pack id.
   - **Positive:** one predicate the record was filed under. When there are several,
     `random.Random("j1:1:<record_ref>:positive").choice` picks one from their sorted list.
   - **Negative** (amended before any run, see below): one predicate the record was not filed under, drawn with the
     frequency of filed predicates among the other records of the draw (leave one record out).
     - Each other record counts once, for the filed predicate its own positive question asks. So the negatives
       follow the positives.
     - The candidates are the pack's predicates, less this record's filed ones, less the other name of any of them
       (the three pairs under "Traps"), and less `unknown_or_other`.
     - The rule is fixed and seeded. `total` is the candidates' summed count. The candidates take positions in
       their sorted order, each as many as its count, and `random.Random("j1:1:<record_ref>:negative").randrange(total)`
       picks one position.
     - If no candidate has a count, the plan job refuses the draw.
   - A predicate is then asked as a negative about as often as it is asked as a positive, and the predicate alone
     tells a judge almost nothing about the answer.
   - So 150 positives and 150 negatives: 300 questions per model. The question file's sha256 is preregistered.
3. **The judging path: the product's, unchanged.**
   - The payload is `judge_payload`: the question (the vehicle id, its aliases, the predicate and its label) and the
     record (its language, its codes, its structured vehicle and its narrative cut at 6,000 characters). This pack
     has no aliases.
   - **The codes are hidden.** The record's codes list is empty, as it was for R001's and R002's readers.
   - The task and schema are `judge_task()` and `judge_schema()`: task `judge_record`, answers `mentions_entity`
     and `describes_predicate`, each `yes`, `no` or `unclear`, at most 256 output tokens.
   - The call is `Runtime.run`, with its one repair after an invalid reply and no escalation, at boundary
     `site:lab`, data label `public`, through the lab's pinned model server on one 4-vCPU runner.
   - **The verdict is the verifier's own rule,** `decide`, applied to that one record:
     - yes and yes is `confirm`;
     - `mentions_entity` yes with `describes_predicate` no is `refute`;
     - every other pair is `unknown`, and so is a call that failed (rule 6 says which failures are scored).
   - Not used: what `SiteVerifier.answer` adds around these functions (the boundary checks, budgets, master data,
     secrets and storage), and the pooling of many records into one verdict. Those decide whether a question is
     judged and over which records, not how one record is judged.
4. **Models:** a-0p5b, a-1p5b and a-4b (`lab/models.json`), 1 repeat each.
   - The runtime sends temperature 0 on every call (`mycelic/collective/inference/client.py`), and the lab starts
     its server with a fixed seed (`lab/server.py`). A second repeat would measure server noise, not sampling.
   - The warm-up sends one judge request twice and records whether the replies matched (`repeat_identical`).
5. **The lexical judge:** `lexical_judge` (`verify.py`), the verifier's judge when no model runs. It reads the same
   payloads and is scored the same way, in the plan job, before any model runs.
   - With the codes hidden, it says `mentions_entity` yes whenever the structured vehicle resolves to a pack id.
   - It says `describes_predicate` yes only when one of the pack's phrases for the predicate is in the narrative
     and not negated. The phrases are the category name or its parts, such as "fuel" or "wiper".
   - **The record-blind control** (added before any run, and amended again before any run, see below) never reads
     the record and decides nothing.
     - It confirms a question when its predicate is one of the four filed most often in the whole NHTSA complaint
       file: `engine`, `electrical_system`, `air_bags` and `power_train`. Otherwise it refutes.
     - The four are fixed now, from the component counts in `nhtsa-probe.json` (all makes, all years) mapped to the
       pack's predicates. Four is the number with the largest balanced accuracy under the amended draw on those
       counts. No label of the 150 records chose them.
     - It is scored like the lexical judge, in the plan job, on every record and on each model's records, and it is
       shown beside the lexical judge.
     - It shows what a judge gets on these questions from knowing which components are filed most, without reading
       the record.
   - **The predicate-only bound** (added in the second amendment, see below) decides nothing either.
     - It is the most any judge that sees only the predicate could score on the records scored. For each predicate it
       takes the answer, confirm or refute, that is right more often there.
     - So its balanced accuracy is the sum, over the predicates, of the larger of each one's positive and negative
       counts, divided by the number of questions.
     - It is fitted to the answers it is scored on, so it is optimistic. It has no interval.
     - It is computed in the plan job on every record, and on each model's records.
6. **Scoring, against the filed codes** (not checked labels):
   - a verdict is correct when it is `confirm` on a positive or `refute` on a negative; `unknown` is never correct;
   - **sensitivity** is the share of positives confirmed, **specificity** the share of negatives refuted, and
     **balanced accuracy** their mean;
   - **accuracy** is the share of all questions answered correctly. Every record has one positive and one negative,
     so accuracy equals balanced accuracy here. Both are reported;
   - **the unknown share** is the share of questions answered `unknown`;
   - each has a percentile-bootstrap 95% interval over records: `stats.cluster_bootstrap_mean`, B 10,000, seed
     `j1:1`, a record's two questions drawn together, and the same draws for every judge;
   - **a model failure** (no valid reply after the repair) is `unknown`, and counts;
   - **a transport failure** (timeout, network, HTTP error, size limit) is the server's, not the model's. That record
     leaves the model's scores, and the lexical judge is compared on the same records. If more than 1% of a model's
     records leave, its verdict is withheld.
7. **The headline, fixed now:**
   - each model's balanced accuracy with its 95% interval, beside the lexical judge's balanced accuracy on the same
     records;
   - a model **judges better than the lexical judge** only if the lower end of its interval is above the lexical
     judge's balanced accuracy, and **worse** only if the upper end is below it. Otherwise the two are **not told
     apart** on these records;
   - each of the three models is compared once, with no correction for three comparisons.
8. **Reported beside the headline, deciding nothing:**
   - sensitivity, specificity, accuracy and the unknown share, with their intervals;
   - the count of each answer pair, for positives and for negatives;
   - model minus lexical balanced accuracy, record by record, with a paired percentile-bootstrap interval
     (`stats.paired_bootstrap`);
   - failures by kind;
   - each model's seconds per judge call (median and 95th percentile from the ledgers), per runner CPU. That is a
     runner number, not site hardware;
   - the record-blind control's balanced accuracy, sensitivity and specificity, with their intervals, and the
     predicate-only bound, on the same records as each model;
   - per predicate, how often each judge confirmed it, for positives and for negatives.
9. **Units and limits:**
   - the 150 records, in record order, are cut into 6 parts of 25, so 50 questions a part;
   - one unit per model and part, 18 units, each with 150 minutes;
   - shard jobs of 330 minutes, so a shard runs 2 units of one model (300 minutes plus the lab's 25-minute shard
     overhead), and each model gets 3 shards: 9 shards;
   - each unit stops at its own budget and keeps what it judged;
   - **a model is scored only when all 6 of its parts finished.** Otherwise its finished parts are reported as a
     partial reading, and they decide nothing.
10. **The first run is the result.** A run that fails before any model judges, for an infrastructure reason, may
    run again unchanged. A model left incomplete gets no verdict from this run; judging it again is a new choice
    file. Any change to a setting is a new choice file.

## Amended before any run, 2026-10-09

No model had judged a narrative, and no question file had been drawn on the real records, when this was changed. So
it is an amendment, as R002's rule 2 was amended before its run (`CHOICE-R002.md`). Every other rule is unchanged.

**What changed.**
- Rule 2's negative. It was drawn uniformly from the pack's predicates, less the filed ones, their twins and
  `unknown_or_other`. It is now drawn with the frequency of filed predicates among the other records of the draw,
  with the same exclusions.
- Rule 5 gains the record-blind control. Rule 8 reports it, and each judge's confirms per predicate. Neither decides
  anything.
- "How it is read" no longer says that a judge that answers without regard to the record scores 0.5. That holds only
  when the predicate carries no information.

**Why.** An adversarial review of the build found that the first draw let the predicate a judge is asked give away
part of the answer.
- Positives follow the components the complaints were filed under, and a few components hold most of the filings.
- Negatives were spread evenly over all the other predicates, many of them rare.
- So a judge that never reads the narrative, and confirms only the most-filed components, scores well above 0.5. The
  lexical judge reads only the record and gets none of this.
- A model that leans on which components are common could then be "not told apart" from the lexical judge, or
  "better", without judging. The headline could not tell that from real judging.

**The figures.** They come from `python3 tools/market/j001_prior_probe.py`, the review's computation kept in the
repository. It scores a judge that confirms a question only when its predicate is among the K most-filed components.
Its counts are the component rows of the whole NHTSA complaint file (`nhtsa-probe.json`: all makes, all years),
mapped to the pack's predicates. They stand in for J001's 150 records and are not those records. Each record is
taken to have one filed predicate.

| Negative draw | K | Sensitivity | Specificity | Balanced accuracy |
|---|---|---|---|---|
| first rule: uniform | 1 | 0.141 | 0.966 | 0.553 |
| first rule: uniform | 5 | 0.576 | 0.821 | 0.698 |
| first rule: uniform | 8 | 0.742 | 0.707 | 0.725 |
| amended rule: frequency | 1 | 0.141 | 0.870 | 0.506 |
| amended rule: frequency | 4 | 0.494 | 0.530 | 0.512 |
| amended rule: frequency | 8 | 0.742 | 0.275 | 0.509 |

The largest balanced accuracy over K is 0.725 (K 8) under the first rule and 0.512 (K 4) under the amended rule. The
amended draw cannot reach exactly 0.5: a record's own filed predicate is never its negative, so common predicates are
asked a little less often as negatives than as positives. The record-blind control measures what is left on the real
records.

## Amended again before any run, 2026-10-09

Still no model had judged a narrative, and no question file had been drawn on the real records, when this was changed.
It changes only the record-blind control of rule 5, what rule 8 reports beside it, and how the two are read. Rule 2,
the scoring, the headline and every setting of rule 9 are unchanged. Neither the control nor the new bound decides
anything.

**What changed.**
- Rule 5's control. It confirmed a question when its predicate was asked more often as a positive than as a negative
  among the other records' questions. It now confirms when the predicate is one of the four filed most often in the
  whole complaint file.
- Rule 5 gains the predicate-only bound. Rule 8 reports it beside the control.
- "How it is read" says what each of the two shows.

**Why.** A second review of the build found that the first control did not show how far the predicate alone gets a
judge. It was biased low.
- Leaving a question's own record out moves the count against that question. A positive takes one positive off its
  predicate's count, and a negative takes one negative off.
- So when a predicate is asked as often as a positive as as a negative, or once more as a positive, every question on
  it gets the wrong answer.
- The amended draw of rule 2 aims at that balance. The more balanced the draw, the nearer the control goes to 0, not
  to 0.5. On an exactly balanced draw it scores 0.
- Rule 10 freezes the first run's report. Printed beside each model, a control at its mean in the table below (0.436
  or 0.445) would have read as a predicate-only baseline, and one below what the predicate alone gives.

The new control never counts the questions it is scored on, so it has no such artefact. On an exactly balanced draw,
any judge that answers by the predicate alone scores 0.5, and so do the new control and the bound.

**The four predicates.** `python3 tools/market/j001_prior_probe.py`, the first amendment's command, scores a judge
that confirms the K most-filed components under the amended draw, on the whole file's counts. Its largest balanced
accuracy is 0.512, at K 4. `python3 tools/market/j001_control_check.py` names the four: `engine`,
`electrical_system`, `air_bags` and `power_train`.

**The figures.** They come from `python3 tools/market/j001_control_check.py`.
- It draws constructed labels of 150 records. Each record is filed under predicates drawn with the whole-file counts
  as weights.
- One set has one filed predicate a record. The other aims at 1.37 a record, as R001's labels have (206 claims on 150
  records, `CHOICE-R001.md`); its draws averaged 1.36.
- The questions are drawn by J001's own code (`lab.j1.build_questions`) under the amended rule 2. For comparison, the
  same records are also given rule 2's first, uniform negatives.
- These are not J001's records. Each row is over 40 draws.

| Labels | Negative draw | Judge | Mean | Smallest | Largest |
|---|---|---|---|---|---|
| one filed predicate a record | amended | first control (leave one out) | 0.445 | 0.213 | 0.553 |
| one filed predicate a record | amended | new control (top four) | 0.507 | 0.467 | 0.547 |
| one filed predicate a record | amended | predicate-only bound | 0.565 | 0.537 | 0.597 |
| 1.36 filed predicates a record | amended | first control (leave one out) | 0.436 | 0.273 | 0.550 |
| 1.36 filed predicates a record | amended | new control (top four) | 0.517 | 0.483 | 0.560 |
| 1.36 filed predicates a record | amended | predicate-only bound | 0.567 | 0.540 | 0.600 |
| 1.36 filed predicates a record | first, uniform | new control (top four) | 0.693 | 0.623 | 0.740 |
| 1.36 filed predicates a record | first, uniform | predicate-only bound | 0.768 | 0.723 | 0.807 |

On an exactly balanced draw of 8 constructed records, the same command gives the first control 0.000, and the new
control and the bound 0.500 each.

- The first control fell as low as 0.213 on a draw where the predicate tells little.
- The new control stays near 0.5 under the amended draw, and well above it under the first, uniform draw. So it would
  still show a leak like the one the first amendment closed.
- The bound is above 0.5 on every draw, because it is fitted to the answers. It is the most a judge that sees only the
  predicate could get, not what one would get.

## Why the units are this size

- **R002 ran out of time.** a-4b's E1 repeats 1 and 2 did not finish 150 extraction calls in their 150-minute
  units, so they spent more than 60 s on each call they made. a-4b's calls had a median of 25.8 s and a 95th
  percentile of 177 s on that runner (`CHOICE-R002.md`, run 1).
- **J001 gives each call three times that.** A unit has 50 calls in 150 minutes: 180 s a call.
- **A judge call is the smaller call.** Its reply is two words from a fixed list, capped at 256 tokens. An
  extraction reply on this pack is capped at 1,024 tokens. Both prompts hold the same narrative, cut at the same
  6,000 characters.
- **Where both were timed, judging was faster.** No judge call has run on these narratives. On generated text,
  each model's judge calls had a lower median than its extraction calls:

  | Run | Model | Judge median | Extraction median |
  |---|---|---|---|
  | smoke-001 (run 2) | a-0p5b | 862 ms | 2,366 ms |
  | smoke-001 (run 2) | a-1p5b | 868 ms | 3,910 ms |
  | smoke-001 (run 2) | a-4b | 3,435 ms | 4,274 ms |
  | g0-001 (run 8) | a-4b | 4.0 s | 6.5 s |

  The figures are from `docs/lab/RESULTS.md`, runs 2 and 8.
- **The slowest timing the lab has for a-4b** is check-001's (run 1): a median of 85.0 s for E3's extraction
  workload (1,500 words in, up to 200 tokens out) on an AMD EPYC 9V74. 180 s is about twice that.
- If a part does not finish anyway, rule 9 applies: that model gets no verdict.

## How it is read

- **A model that judges better** answers the verifier's narrow question on these complaints better than the
  pack's category names do. That supports "a small model at the site helps pushdown verification" on this kind of
  text. It does not show that the model beats a lexicon a company would tune. It does not show how the verdict
  pooled over many records behaves.
- **A model not told apart from the lexical judge** gives no evidence that it adds anything here. The verifier's
  lexical judge costs nothing to run.
- **A model that judges worse** is a real negative for the site model on this text, on the task the product
  actually gives it.
- **Sensitivity and specificity say where a judge stands.** The lexical judge confirms a question only when that
  component's phrase appears, not negated, in the narrative. A model can gain where a complaint describes a
  component without naming its category. A model that confirms freely gains sensitivity and loses specificity.
- **0.5 is no judging at all, but only when the predicate carries no information** (amended before any run, see
  above).
  - A judge that confirms every question, or refutes every one, has a balanced accuracy of 0.5. One that answers
    `unknown` to every question has 0.
  - A judge that answers by the predicate alone, without reading the record, has 0.5 only when each predicate is
    asked as a negative as often as it is asked as a positive. The amended draw of rule 2 makes that nearly so, not
    exactly: a record's own filed predicate is never its negative.
  - The record-blind control (rule 5, amended again before any run) shows what knowing which components are filed
    most gets a judge on these records. It is fitted to nothing in them.
  - The predicate-only bound (rule 5) shows the most that knowing only the predicate could get on these records. It
    is fitted to their answers, so it is optimistic: it sits above 0.5 even where the predicate tells little.
  - Neither decides anything. A model's balanced accuracy at or below the bound does not show that the model reads
    the record. Its confirms per predicate (rule 8) show where it leans.
- **150 records give intervals several points wide.** Small differences between the models cannot be read.
- **This is one public field.** It is not a company's records, and complaints written to a regulator are not
  internal service notes.

## Traps

1. **Filed codes are not truth.** A narrative can describe a component it was not filed under, and the reverse.
   A careful judge that confirms such a negative is scored wrong. No judge can reach 1.0, and the key costs every
   judge, the lexical one included.
2. **Old and new names.** NHTSA's files use an old and a new name for some components (`PACK-V2.md`). This pack
   keeps three such pairs as separate predicates, and no text can tell the two names of a pair apart:

   | Old name | New name |
   |---|---|
   | `engine_and_engine_cooling` | `engine` |
   | `fuel_system_gasoline` | `fuel_propulsion_system` |
   | `service_brakes_hydraulic` | `service_brakes` |

   A negative is never the other name of a filed component. A positive is the filed name, whichever it is.
3. **Negation.** The shipped instruction says `describes_predicate` is no when the record "says that it did not
   happen". Complaints often state a failure as a negation: "the air bags did not deploy". A model that follows the
   instruction to the letter can answer no on a positive. The lexical judge has its own negation cues. The
   instruction is used unchanged, so this cost is part of what is measured.
4. **The codes are hidden.** In the product a record's codes go into the payload, and the instruction says they
   are statements too. With codes present the question is easier for both judges. Here both judges read only the
   narrative and the structured vehicle.
5. **The vehicle answer.** The vehicle is in the payload's structured entities, so `mentions_entity` should be yes
   on every question. A model that says no or unclear there gets `unknown`, whatever it says about the failure: the
   shipped rule needs yes for both a confirm and a refute. The answer counts show how often that happens.
6. **Easy negatives.** Negatives are drawn at random, so most name a component the narrative never mentions.
   Specificity is then high for any judge that reads at all. Balanced accuracy weighs it the same as sensitivity,
   and both are reported.
7. **Training data.** The models may have seen these public complaints. That would help them, not the lexical
   judge.
8. **One record a question.** The product pools a window of records into one verdict. J001 judges one record at a
   time, so the pooling is not measured.

## Runs

None yet.
