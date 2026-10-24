# Analysis of the "Mycelic Frontier Research Mode" prompt

The prompt asked for 63% → 85%+ at 10K agents, literature first, three research agents, gates 0–6, five
architectures and a roadmap. This note reads it against what the measurements have since shown. The aim is to make
the next prompt sharper, not to grade this one.

## What it got right, and these parts did the real work

- **"Reproduce before trusting."** That instruction exposed the false premise: ~63% was a diagnostic. The official
  baseline is 0.3875.
- **Gate 1 before building ("if the evidence upper bound is below 85%, determine what prevents the target before
  rebuilding routing").** It fired: the planted-evidence ceiling is 0.683 at 10K. Everything useful since then
  followed from honouring that gate.
- **Phase 3's instruction to treat reported defects as hypotheses and instrument every transition.** It produced the
  funnel (93 / 5 / 142) and the proof that the site graph is irrelevant to verdicts.
- **The scientific rules.** Controls for every claim, no hypothetical gains, no "hypergraph exists because the class
  exists". These rules made the negative results trustworthy, including the recursive-acquisition null and the
  rescue arms losing.
- **The final question** — "what architecture lets incomplete agents discover truths none can observe?" — is the right
  question. The answer just turned out to be about *disclosure and statistics*, not geometry.

## Where it pointed the effort wrong

1. **A false baseline, and a target stated as a mission.** "63% → 85%" anchored ambition on a diagnostic number, and
   set a target that measurement shows is infeasible under the current contract (F ceiling 0.683 at 10K). A target
   should be conditional: "≥85% *if* the evidence ceiling allows; otherwise identify the contract that would."
2. **Most of the search space is downstream of the loss.**
   - Directions A (geometry, embeddings, ANN), B (sharding) and C (hypergraphs, Tesseract routing) all improve
     *retrieval and routing*.
   - Measured: judging is right on 93/98 asked questions; an evidence check predicts verdicts 98.6% of the time; no
     site ranker can add a confirm (0 of 2,940); edges change 0 of 539 verdicts.
   - The loss is (a) the per-site k-floor erasing signal before HQ sees it, (b) poor candidate ranking under a fixed
     budget, and (c) specificity at scale. None of A–C touches these.
   - The prompt listed eight loss categories in Phase 3, but put no category for **"suppressed by the privacy
     boundary before ingestion at HQ"** — the dominant one.
3. **It never named the privacy contract as a variable.** "Preserve privacy boundaries" sat among the rules, so the
   most powerful lever — aggregate-then-threshold disclosure, worth 0.683 → 1.000 planted recall at 10K — was
   implicitly off-limits. A research prompt should mark which constraints are physics and which are policy.
4. **No false-discovery objective.** Accuracy, recall and FDR are all listed under Gate 6, but the mission is stated
   only as accuracy. At 10K a count gate admits ~70% of keys with no pattern (98.5% on the aggregate). Any recall gain
   that ignores this is fake progress. The objective should be recall at a fixed decoy and background-supported level.
5. **"No budget" is implicit.** The prompt invites recursion, expansion and re-asking without fixing the question
   budget. Measured: ask-every-2-weeks reaches 199/240 only at ~25× the questions, and re-asking more than doubles
   echo decoys (24 → 53–56). Every arm needs a budget-matched control. The prompt asked for controls in general, but
   not for this one specifically.
6. **The literature window was unverifiable and invited fabrication.** "arXiv, August 24 – October 24, 2026" could
   not be fetched from the container (egress blocked). An external report then cited 22 IDs from that window that
   nobody could verify. A prompt should require fetched abstract pages, and accept "UNVERIFIED" as an answer.
7. **The three agents were more ceremony than substance.** Agent B could not work without arXiv access. Agent C's
   adversarial checks were the valuable part, and they apply to everything, not to one agent. A single researcher
   with a mandatory adversarial pass on every claim gets the same benefit with less theatre.
8. **It mixed two benchmarks.** "Mycelic and NeuralGraph accuracy" are different problems:
   - EMERGENCE is distributed rare-pattern discovery, 0.3875 official, with a lexical judge.
   - LoCoMo-style memory QA is single-store, 66.7%, generation-limited, with an LLM judge.

   A combined mission makes it easy to apply a fix for one to the other.
9. **Five architectures up front forces invention before diagnosis.** "Invent five high-upside architectures" in
   Phase 4 pressures the work to produce designs whether or not the diagnosis supports them. Better: "propose
   architectures only for losses Phase 3 confirmed, and say 'none' if none are confirmed."

## What the next prompt should say

> Under the current disclosure contract the planted-evidence ceiling at 10K is 0.683; under
> aggregate-then-threshold it is 1.000, but a count gate then admits 98.5% of non-planted keys. Find the disclosure
> contract and the detection statistic that together maximise rare-pattern recall at 10K–50K, subject to decoys
> ≤ 118, background-supported confirms near today's level, a fixed question budget, and a stated privacy guarantee
> for individual reporters. Bound first, verify citations, report against interest.

`DEEP_RESEARCH_PROBLEM.md` is that prompt, written out with full context.
