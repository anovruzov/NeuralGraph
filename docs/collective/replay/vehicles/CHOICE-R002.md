# Reader test R002: R001 again, with the post-processing that zeroed it fixed

R001 asked whether small models can read real NHTSA complaints. Every model scored predicate F1 0.000. The most likely
cause was the reading path, not the models: `ModelExtractor.postprocess` dropped every item whose entity was typed
`vehicle` without resolving to a pack id (`CHOICE-R001.md`, "What the zero most likely is"). R002 asks R001's question
again with that one step fixed. The rule and the reading below are committed before any model reads a narrative.

## The declaration

The rule was written by the AI system that wrote this repository's code. It has seen R001's result: every model at
0.000, and the lexical extractor at 0.434. It has also seen main-001, V001 to V003 and DIAG-E001. It has not seen any
model reply on these narratives, because none was stored. The fix was chosen before this run, from R001's code and an
offline reproduction (`tools/market/r001_postprocess_probe.py`), not from any R002 output. Everything in R001's
declaration still holds: the models may have seen public complaints in training, and the answer key is the components
each complaint was filed under, not checked labels.

## The rule

1. **Records, readers, prompt, schema, scoring and headline: R001's, unchanged** (`CHOICE-R001.md`, rules 1 to 7):
   - 150 records drawn by `random.Random("nhtsa:1")` from the six makes' complaints received in 2023 and 2024;
   - a-0p5b, a-1p5b and a-4b, 3 repeats each, through the lab's pinned server on one 4-vCPU runner per shard;
   - E1's extraction prompt and schema;
   - the lexical extractor scored the same way, in the plan job.

   The labels are rebuilt by the same rule from the complaint file as published on the day of the run. If their
   sha256 differs from R001's (`bc6d092d8bca…`), both are reported, and the two runs are not on the same records.
2. **The one change: post-processing.** When an item has a predicate and its entity cannot be used, the predicate is
   attached to the record's structured vehicle, as the lexical extractor already does, and the event is counted.
   "Cannot be used" means one of these cases:
   - only one of type and text is given (a type with a null text, or a text with no type);
   - the text is not found in the narrative, or it is found but the pack's scanner reads no id of that type there;
   - the text does not resolve to a pack id.

   Items without a predicate, items whose text is a person value, and items from a record with no structured vehicle
   are dropped as before.
3. **Recorded beside the headline:**
   - each model's drops and re-attachments by reason. Re-attachments count items, not new claims: an item
     re-attached to a claim the record already has is counted as re-attached and as a duplicate;
   - the share of records with no claim;
   - the raw replies (`replies.jsonl`, public data only), so that a later choice file can re-score them without
     running the models again.
4. **The headline, fixed now (R001's rule 6):**
   - each model's predicate micro F1 with its percentile-bootstrap 95% interval over records;
   - a model **reads better than the lexical baseline** only if the lower end of its interval is above the lexical F1,
     and **worse** only if the upper end is below it;
   - E1's field F1, kill flag and non-inferiority verdict are reported as printed, and do not decide anything.
5. **The first run is the result.** A run that fails before any model reads, for an infrastructure reason, may run
   again unchanged. Any change to a setting is a new choice file.

**Amended before any run, 2026-10-09.** Rule 2 first named three cases. The code merged for this run
(`ModelExtractor.postprocess`, `mycelic/collective/edge/extract.py`) re-attaches on every entity failure it counts as
`not_canonical` or `ungrounded`, which adds a text with no type and a text the scanner reads no id in. Rule 2 now says
what the code does, and rule 3 says how a re-attached duplicate is counted. No model had read a narrative for R002
when this was changed.

## How it is read

- **The traps R001 listed beside the post-processing remain:**
  - `unknown_or_other` is offered but never in the key;
  - failure stated as a negation ("the air bags did not deploy") is marked negated;
  - three old/new NHTSA name pairs cannot be told apart from text.

  They count against the models and not against the lexical extractor. A model that ties the lexical baseline here
  reads at least as well as it.
- **A model above the baseline** shows that it reads these narratives better than a lexicon of category names. It
  does not show that it beats a lexicon a company would tune.
- **A model at or below the baseline,** with re-attachments counted and replies stored, is a real negative for the
  "small models at the site" claim on this text. Unlike R001, it can be inspected.
- 150 records give intervals several F1 points wide, so small differences between models are not readable.

## Runs

1. **Run [37910989965](https://github.com/anovruzov/NeuralGraph/actions/runs/37910989965)** (request
   `reader-002`, commit `7a94535`, 9 units in 6 shards). Read from the aggregate job's log (job 113866712365):
   `report.md` sha256 `081347404246…` and `report.json` sha256 `41ebc90eb48e…`, both matching the log.
   - The labels are R001's: sha256 `bc6d092d8bca…`, 150 records, 206 claims, so both runs read the same records.
     The lexical extractor scores predicate F1 0.434 on them, as in R001.
   - a-0p5b and a-1p5b finished all three repeats. a-4b finished repeat 3; repeats 1 and 2 timed out at the unit's
     150-minute limit (shard s005 ran 5 hours 2 minutes). The a-4b calls took a median 25.8 s and a 95th percentile
     of 177 s on that runner.
   - E1's comparison needs every repeat of its reference model (a-4b), so the aggregate scored no model. The
     headline of rule 4 does not need that comparison: it is each model's own predicate F1 against the lexical F1.
     The scores exist in the run's artifacts, which cannot be read from this environment.

   **What happens next, fixed before it runs:** the run's artifacts are re-aggregated in CI with code that scores
   each model from its completed repeats (a-4b from repeat 3 alone, which is reported as such). No model reads
   again and no setting changes. The re-aggregation is recorded here as run 1's reading, not as a new run.

   **Model call latency** (median per extraction call, from the ledgers; shared runners, not site hardware):
   a-0p5b 10.9 s and 12.5 s on two CPU types, a-1p5b 34.7 s and 52.1 s, a-4b 25.8 s.
