# Reader test R001: can small models read real complaints?

The replays (V001 to V003) asked whether counting complaints across sites warns early. They used no model: X read the
narratives lexically. The product's other claim is that a small model running at each site can read that site's own
free text into structured facts. Until now that has been measured only on generated text (`docs/lab/RESULTS.md`, run
2), where the lexical reader is exact by construction. R001 measures it on real narratives: NHTSA vehicle complaints,
read by the lab's three pinned small models on a 4-vCPU runner, with each complaint's component codes hidden and used
as the answer key. The rule and the reading below are committed before any model reads a narrative.

## The declaration

The rule was written by the AI system that wrote this repository's code. It has seen the results of V001 to V003, which
used the same complaint file, but no model output on these narratives. The models were trained on public web text,
which may include NHTSA complaints. The answer key is the components each complaint was **filed under**, not labels
anyone checked. A narrative can describe a component it was not filed under, and the reverse, so no reader can reach an
F1 of 1.0, and a reader can be right where the key is wrong.

## The rule

1. **Records** (`lab.goldlabels`, source `nhtsa`, built in the lab's plan job):
   - complaints received 2023-01-01 to 2024-12-31 for the six makes of V001 to V003 (FORD, CHEVROLET, JEEP, HONDA,
     NISSAN, DODGE), exported exactly as the replays export them;
   - eligible: a narrative, one vehicle, and at least one specific component code (not "unknown or other");
   - 150 drawn by `random.Random("nhtsa:1")`.
2. **What a reader sees:** the narrative and the vehicle (its make, model and year from the structured fields). The
   codes are removed.
3. **The answer key:** one claim per specific component the complaint was filed under (the vehicle, that component's
   predicate, not negated). The vehicle pack's 26 components are the predicates.
4. **Readers:**
   - the lab's pinned models a-0p5b, a-1p5b and a-4b, through the lab's pinned llama-server on one GitHub-hosted
     4-vCPU runner each, with E1's extraction prompt and schema unchanged, 3 repeats each;
   - the lexical extractor, the pack's category names as its phrases, scored on the same records by E1's own counting,
     in the plan job.
5. **Scoring:** E1 unchanged, with data label `public` and boundary `site:lab`.
6. **The headline, fixed now:**
   - each reader's **predicate micro F1** against the key; for a model, its percentile-bootstrap 95% interval over
     records;
   - a model **reads better than the lexical baseline** only if the lower end of its interval is above the lexical
     F1, and **worse** only if the upper end is below it;
   - E1's own primary metric (field F1, which also counts the vehicle) and its non-inferiority verdict against a-4b are
     reported as E1 prints them, but they do not decide this test.
7. **Speed:** each model's unit wall time per record read, on this runner. That is a runner number, not site hardware.
8. **The first run is the result.** A run that fails before any model reads for an infrastructure reason may run again
   unchanged. Any change to a setting is a new choice file.

## How it is read

- The lexical baseline is weak on purpose: it knows only the category names. A model that beats it shows that it reads
  free text, not that it beats a lexicon a company would tune. A model that does not beat it is a real negative for
  the "small models at the site" claim on this text.
- 150 records give intervals of several F1 points; small differences between models are not readable.
- This is one public field. It is not a company's records, and complaints written to a regulator are not internal
  service notes.

## A handicap found after the rule and before the result

On 2026-10-09 around 05:10 UTC, while reader-001 was still running and before any of its results were read, a code
review found a handicap against the models (`docs/handoff/` diagnosis, finding F1 of the reading-path review).
- **Post-processing drops correct answers.** `ModelExtractor.postprocess` (mycelic/collective/edge/extract.py) drops a
  model's whole item, predicate included, when the item names a vehicle in words that do not resolve to the pack's
  canonical id. The prompt tells the model to copy the vehicle as written ("2021 FORD F-150"), and the vehicle pack's
  ids (FORD-F150-2021) have no aliases, so a model that follows the prompt loses those predicates. The lexical
  extractor attaches its predicates to the structured vehicle instead, so the baseline is spared by construction.
- **On ten constructed narratives,** a perfect prompt-following reader scored predicate F1 0.571, and the same
  answers with a null vehicle scored 1.0. The lexical baseline scored 0.588.
- **Smaller traps.** The prompt and schema were reused from the device pack unchanged, which adds three:
  - `unknown_or_other` is offered but never in the key;
  - "set negated when the event did not happen" turns failure statements such as "the air bags did not deploy" into
    negations;
  - three pairs of old and new NHTSA names for the same component cannot be told apart from text.

**The rule stands as written.** The result is the pre-registered headline. It will be reported beside this handicap
and beside the drop counts by reason wherever the run's files expose them, because the raw model replies are not
stored. A model at or below the lexical baseline therefore does not yet show that it read badly. A follow-up that
fixes the post-processing (a null or structured vehicle accepted, raw replies kept) is a new choice file.

## Runs

1. **reader-001** ([37874950202](https://github.com/anovruzov/NeuralGraph/actions/runs/37874950202), commit `16901e5`):
   the plan job downloaded the complaint file and pre-registered the labels: 150 records, 206 claims, sha256 beginning
   `bc6d092d8bca`. All 9 units finished `ok` (3 repeats per model, 6 shards). The report comes from the `aggregate`
   job (113685208656), and both files were read in full: `report.json` sha256 `ee6db53ba685…` and `report.md`
   `4edb84513c42…` match the hashes the job printed.

## The result

**Every model scored exactly zero, and by the rule each reads worse than the lexical baseline.**

| Reader | Predicate F1 | 95% interval | Field F1 | Valid JSON | Wall seconds per record (runner CPU) |
|---|---|---|---|---|---|
| Lexical extractor (plan job) | 0.434 (tp 69, fp 43, fn 137) | none (fixed) | 0.550 | n/a | n/a |
| a-0p5b | 0.000 | 0.000 to 0.000 | 0.000 | 1.000 | 9.9 to 10.5 (AMD EPYC 7763) |
| a-1p5b | 0.000 | 0.000 to 0.000 | 0.000 | 1.000 | 20.5 to 21.7 (AMD EPYC 9V45) |
| a-4b | 0.000 | 0.000 to 0.000 | 0.000 | 0.996 | 35.2 to 38.9 (AMD EPYC 9V74) |

- **The pre-registered headline:** each model's interval (0 to 0) lies wholly below the lexical F1 of 0.434. So by
  rule 6, each model **reads worse** than the lexical baseline. That is the result of the first run, and it stands.
- **The vehicle was never right either:** exact vehicle matches were 0 of 150 for every model.
- **E1's own block, as it prints it:**
  - every model is marked non-inferior to a-4b, with a difference of 0.000;
  - every kill flag is set;
  - both verdicts compare zeros, so they carry no information here.
- **Speed:** each unit's wall time divided by 150 records, including server start. The shards ran on three different
  CPU models, so the rows are not comparable with each other. Ledger medians per call were 10.8 s (a-0p5b), 23.9 s
  (a-1p5b) and 18.8 s (a-4b). a-4b's 95th percentile was 118.6 s, and 2 of its 450 calls failed.

## What the zero most likely is

An F1 of exactly 0.000 is not a weak reader's score. Three models of different sizes, 450 reads each, almost all
valid JSON, and not one correct claim: that is the signature of post-processing discarding every item. It is the
handicap logged above, in a stronger form than the ten-narrative estimate. The raw replies were not stored, so this
cannot be confirmed from the run. It can be reproduced, though.

- **The prompt and schema:** for this pack, the only entity type is `vehicle`, and the prompt says to copy
  `entity_text` exactly as written.
- **The drop rule:** `ModelExtractor.postprocess` (`mycelic/collective/edge/extract.py`) drops an item whole, predicate
  included, when:
  - its `entity_type` is set but its `entity_text` is null (counted `not_canonical`); or
  - the text, such as "2021 FORD F-150", does not resolve to a pack id (also `not_canonical`).
- **What survives:** an item keeps its predicate only when both entity fields are null, or when the text names a pack
  id as the narrative writes it ("FORD F150 2021"), which complaints rarely do.
- **The reproduction** (`python tools/market/r001_postprocess_probe.py`): through the repo's own Runtime, a fake
  server, `ModelExtractor` (no fallback) and E1's counting, on the ten constructed narratives above:
  - a reader that always sets the type to `vehicle`, with the vehicle as written or null, scores predicate F1
    **0.000**, with all 10 items dropped as `not_canonical`;
  - the same predicates with both entity fields null score 1.000;
  - the lexical extractor scores 0.588.

So R001 shows that **the reading path as built cannot score a model on this pack**. It does not show that the models
read badly. Whether they can read real complaints remains unmeasured.

**Next, as a new choice file (R002) before any re-run:**

- attach a predicate to the structured vehicle when the entity fields do not resolve, and count each such case;
- keep the raw replies for public data;
- merge the three old/new name pairs;
- drop the `unknown_or_other` trap and define negation for component predicates.

The handoff plan lists this as task 4.1 (`docs/handoff/HANDOFF-2026-10-09.md`).
