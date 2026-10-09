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

## Runs

1. **reader-001** ([37874950202](https://github.com/anovruzov/NeuralGraph/actions/runs/37874950202), commit `16901e5`):
   the plan job downloaded the complaint file and pre-registered the labels: 150 records, 206 claims, sha256 beginning
   `bc6d092d8bca`. The models have not finished; the result is recorded here when the report is in.
