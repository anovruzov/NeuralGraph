/*
  Every number shown on the site, with the repository artifact it is read from.
  Scopes are deliberately not merged: the 282-question A/B and the 744-question slice are different cohorts
  under different judges, and are labeled as such wherever they appear.
*/
export type Metric = {
  value: string;
  unit?: string;
  what: string;
  scope: string;
  delta?: string;
  source: string;
};

/* Track 01 — the clean A/B. Same 282 single-hop questions, same code, same judge (Gemma lenient); only retrieval changed. */
export const locomoAB: Metric[] = [
  { value: "73.8", unit: "%", what: "Accuracy with the shipped local pair routing stack", scope: "LoCoMo category 1 · 282 questions · Gemma lenient judge", delta: "from 64.9% flat retrieval", source: "docs/BENCHMARKS.md · research/results/local_pairs_single_hop.json" },
  { value: "46.8", what: "Recall@10 of the gold evidence", scope: "282 questions · retrieval only", delta: "from 39.4", source: "docs/BENCHMARKS.md · research/reports/RESULTS_ALL.md §1.3" },
  { value: "+8.9", unit: "pts", what: "Gain that survives all four graders tried, p < .001", scope: "Gemma lenient · Qwen lenient · Qwen strict · substring", source: "docs/BENCHMARKS.md · research/reports/N5_judge_sensitivity.md" },
];

/* Track 01 — the shipped configuration on the evaluated slice: conversations 1–5, 744 of 1,540 questions, Gemma lenient judge. */
export const locomoSlice: Metric[] = [
  { value: "72.2", unit: "%", what: "Overall accuracy", scope: "744 questions · conv. 1–5", source: "docs/BENCHMARKS.md · research/results/capstone_full.json" },
  { value: "72.8", unit: "%", what: "Accuracy on the runner's multi-hop label (LoCoMo category 4)", scope: "400 questions in that slice", source: "docs/BENCHMARKS.md" },
  { value: "73.7", unit: "%", what: "Temporal accuracy", scope: "156 questions in that slice", source: "docs/BENCHMARKS.md" },
];

/* Track 02 — the enterprise-hierarchy benchmark: synthetic enterprise with planted hidden problems, 18 architectures, 50,000 users, 5 seeds. */
export const emergence: Metric[] = [
  { value: "0", unit: "/ 306", what: "Facet records of a hidden pattern that rank in the global top 900 by any per-record feature", scope: "10,000 users · the signals are invisible one record at a time", source: "docs/MYCELIC_ENTERPRISE.md §2, §28" },
  { value: "0→57", unit: "%", what: "Hidden problems found: upward summarisation alone, then with targeted questioning back down the hierarchy", scope: "50,000 users · 5 seeds · Δ +0.546, CI [+0.49, +0.61]", source: "docs/MYCELIC_ENTERPRISE.md §2, §17" },
  { value: "0", unit: "%", what: "Original records that leave the agent owning them", scope: "vs 22% for the best centralised option", source: "docs/MYCELIC_ENTERPRISE.md §1" },
];

/* Track 03 — continual questioning, from the same benchmark's ablations. */
export const recursion: Metric[] = [
  { value: "516", what: "Questions the full system asks per run", scope: "10,000 users · simulation benchmark", source: "docs/MYCELIC_ENTERPRISE.md §17" },
  { value: "61", unit: "%", what: "Questions that changed a conclusion or its confidence", scope: "10,000 users · counted only when the hypothesis set moved", source: "docs/MYCELIC_ENTERPRISE.md §17" },
  { value: "−0.55", what: "Discovery lost when questioning is removed entirely", scope: "50,000 users · 5/5 seeds worse · CI [−0.61, −0.48]", source: "docs/MYCELIC_ENTERPRISE.md §17" },
];

/* Deployment facts from the runtime's own demos and tests. */
export const runtime = {
  smoke: "Six agents in three teams; the enterprise conclusion's lineage names every contributing observation, agent, team and layer.",
  strategic: "Fourteen agents in eight teams across three regions; a second-source recommendation rests on 8 of them, through two derivation steps.",
  scale: "The same scenario with 99 agent processes passes in about twenty seconds.",
  source: "README.md · tests/smoke/mycelic_smoke.py · demo/mycelic_strategic_demo.py",
};
