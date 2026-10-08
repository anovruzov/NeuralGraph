# Mycelic v5 optimisation protocol (pre-registered)

Written and committed **before** any v5 experiment ran. A change to anything below is recorded as an amendment, with its date and reason, in §9. The decision record is `artifacts/ledger_v5.jsonl` (one row per experiment, accepted or rejected); the narrative is `logs/research_log.md`.

## 1. Goal and claims

* **Objective:** raise `found_anywhere_in_register` for the best Mycelic configuration at **10,000 users** toward **0.90**. A stable, well-supported result in the eighties is acceptable if gains plateau under §6. A result below 0.80 is reported as is, with its bottlenecks. The standard is not relaxed.
* **Claiming the target:** "0.90 achieved" may be said only if the mean on the sealed final set (§2) is ≥ 0.90. The 95% interval is reported next to the mean, and the report says separately whether the lower bound clears 0.90.
* **Scale claims** (50k) are reported separately from the 10k target.
* **Simulator, not live agents:** every number is produced by the simulator, whose model decisions are simulated operators (see `docs/mycelic_v5/ORACLE_AUDIT.md`). No v5 number is evidence of live-LLM accuracy.

## 2. Seeds

| role | 10k seeds | 50k seeds | use |
|---|---|---|---|
| **FINAL, sealed** | **3000–3029** | **3000–3002** | One-shot final evaluation (§7). Enforced in code: `protocol.check_world` refuses these worlds unless `MYCELIC_FINAL_EVAL=1`, and logs every opened build to `artifacts/final_access.log`. |
| TUNE | 500–599 | 500–509 | Fitting, screening, leave-one-seed-out estimates. The v4 ranker was fitted on 500–539. |
| DEV-VALIDATION | 600–649 | 600–602 | One paired read per candidate that passed screening. Repeated reads across rounds are expected and disclosed; this panel is development data. |
| TRANSFER | 650–679 | — | Org shapes, noise and difficulty variants (§5). |
| LEGACY (development data) | 0–29 | 0–4 | Historical evaluation panels, all read before v5. 15–29 were read once by the round-1 refuter for the 0.70 criterion. Seed 5 was also run once for timing before round 1. Reported only for continuity. |
| ad hoc (development data) | 900, 540–552 | — | Profiling and refuter ablations. |

**Seed policy for v5:** final seeds are never used while optimising.

## 3. Incumbent, comparators and fairness

* **Incumbent:** v4, commit `794b44d`. For `H_mycelic_full` it adds the cluster-feature ranker of its own (fitted on seeds 500–539, decoy weight 10) and the register cut after enrichment. Measured on the then-unread seeds 15–29: found 0.713 [0.678, 0.750].
* **Comparators**, re-run on identical worlds and paired seeds after every accepted change:
  1. `A2_chunked_ctx`, the strongest existing centralised baseline.
  2. **A2-tuned**: A2 given the same tuning effort as H (own ranker, same feature set and fitting procedure, same seeds, same selection rule).
  3. **C-Mycelic**: a centralised version of Mycelic's candidate and questioning machinery. All raw records are central; it runs the same synthesis, ranker procedure and question rounds, answered by central re-reads.
  4. `B4_central_triage`.
  5. The previous Mycelic configurations: v3 (`29307f2`) and v4.
* **Shared correctness fixes** (bugs, shortcuts, metering) are applied to every affected architecture.
* **Capability:** two regimes are reported.
  * **Natural:** the `back-loaded` allocation (edge small-7b … kernel frontier-plus).
  * **Capability-matched:** every system uses the same tier for every model call.
* **Evidence and scoring:** the same corpus, the same `evaluate()`, the same register cap `min(6000, max(600, n_entities))`, and stopping rules fixed per system before a comparison is run. No system gets extra rounds that its comparator lacks.
* **Cost:** the meter charges extraction, aggregation and summaries, routing, descent reads, questions, kernel passes and verification. The simulator models no retries or failures; that is stated in every cost table.
  * Comparisons are reported both at **equal compute** (found interpolated at matched compute units) and as **quality-versus-cost curves**. Each curve sweeps that system's main budget knob: H `question_frac`; A2 records read / chunk budget; C-Mycelic question budget; B4 kernel object cap.
* **Unconstrained vs privacy-constrained:**
  * The unconstrained comparison lets centralised systems centralise raw text.
  * The privacy-constrained comparison is a separate table restricted to systems with `raw_text_exposure_fraction == 0`.
  * Centralised access is never handicapped to make Mycelic win.

## 4. Metrics and frozen guardrails

**Tracked for every run:**
* found, AP, `discovery_precision`, FDR, rare recall;
* `decoy_acceptance_all` and D1/D2/D3/D5;
* `lineage_accuracy`, `provenance_preservation`, `independent_evidence_accuracy`;
* `hallucination_rate`, `unsupported_reports`, evidence coverage;
* compute units, inference calls, tokens, modelled wall time (`meter.wall_seconds`);
* raw-text and claim exposure;
* **supported found** (below).

**Supported found** is an evaluator-only metric, added without changing any existing metric. A gold pattern counts as supported-found when some register hypothesis matches it under the primary rule **and** at least `max(2, ceil(|preds|/2))` of the pattern's links are backed by that hypothesis's member objects. A link is backed when the member object for that predicate carries an evidence pointer to one of the pattern's own evidence records. This separates real discoveries from coincidental matches of noise chains that happen to sit on a gold entity.

**Guardrails** apply to a candidate change against the incumbent, paired, on the same seeds; Δ is candidate minus incumbent. A change that violates any one is rejected:

| # | metric | limit |
|---|---|---|
| G1 | `decoy_acceptance_all` | mean Δ ≤ +0.02 |
| G2 | each of D1, D2, D3, D5 | mean Δ ≤ +0.05 |
| G3 | AP; `discovery_precision` | AP Δ ≥ −0.01; precision relative change ≥ −10% |
| G4 | supported found | Δ ≥ 0, and Δ ≥ 0.5 × Δfound (at least half of any gain must be evidence-backed) |
| G5 | `lineage_accuracy`, `independent_evidence_accuracy` | Δ ≥ −0.02 each |
| G6 | `hallucination_rate` | Δ ≤ +0.01 |
| G7 | compute units | ratio ≤ 1.25 (above that, accepted only if the gain is ≥ +0.01 found per +10% compute **and** the configuration is on or above the centralised quality-cost frontier at its compute); modelled wall time ratio ≤ 1.5 |

## 5. Accepting a change

1. **Motivation:** a diagnostic (loss funnel, error analysis) identifies the loss the change targets, before it is built. Single-seed improvements are never a reason.
2. **Screen on TUNE seeds.** Anything fitted is estimated end-to-end leave-one-seed-out. Losers are recorded in the ledger as rejected.
3. **One paired read on DEV-VALIDATION** (10k seeds 600–649) against the incumbent. Accept iff the mean Δfound > 0 with its 95% bootstrap lower bound > 0, and G1–G7 hold.
4. **Transfer check before adoption into the final candidate:**
   * 50k on 600–602;
   * at least two org shapes (`build_org` fan-in or region count) and two corpus variants (noise rates; rare fraction / facet support) on TRANSFER seeds.
   
   A change that hurts any transfer condition by more than its 10k gain is flagged in the report. It is not silently kept.
5. **Ablation:** each accepted change is removed singly from the final candidate to confirm its contribution. Each is also offered to the centralised comparators where applicable; whether they benefit is reported.

## 6. Plateau rule (stop optimising)

Stop and go to the final evaluation when the first of these holds:
* (a) two consecutive rounds add < +0.01 found on DEV-VALIDATION (paired mean of the round's accepted changes);
* (b) the analyst's measured headroom falls below 0.03 of gold. Headroom means gold patterns with genuine ≥ 2-link evidence in the kernel pool that are not reported, plus those an identified, unbuilt mechanism could plausibly recover;
* (c) six rounds have completed;
* (d) DEV-VALIDATION found ≥ 0.92 with all guardrails holding.

## 7. Final evaluation (one shot)

* **Freeze first:** the configuration, ranker and code are frozen and committed, and the commit is recorded.
* **One run, by an independent refuter**, with `MYCELIC_FINAL_EVAL=1`:
  * every system of §3, both capability regimes, on 10k seeds 3000–3029 and 50k seeds 3000–3002;
  * quality-cost points at the frozen budgets, plus one sweep point either side.
* **Report:**
  * the mean and 95% bootstrap CI of the level;
  * paired Δ vs each comparator with CI and sign test;
  * every metric of §4;
  * cost and failure modes.
* `artifacts/final_access.log` must show exactly one access per final world per system run.

## 8. Deliverables before any paid live run

* The frozen configuration, commits, reproduction commands and raw artifacts.
* The paired comparison table against the centralised alternatives (unconstrained and privacy-constrained), with cost.
* The final verdict and an independent refutation report.
* A statement of which conclusions depend on oracle-assisted simulation.
* A staged live-LLM plan that replaces those assumptions with real model decisions, keeps gold only in the evaluator, and gives centralised comparators identical tasks and budgets.

No paid live simulation is launched under this protocol.

## 9. Amendments

* **A1 (2026-10-08, after the oracle audit, before any v5 optimisation experiment).**
  * **Observable defaults:** the replacements S1–S3 are the default for every architecture (`ops.OBSERVABLE`; `MYCELIC_OBSERVABLE=none` restores the legacy paths). On the audit's paired runs (seeds 580–599 at 10k, and 9 architectures on two worlds) they reproduce every v4 register exactly; S2 acts only with `local_reextract`.
  * **Seal at every scale:** final seeds are refused at every scale (finding P1), and `corpus.build_corpus` checks too.
  * **Acceptance gate:** `research.mycelic.test_leakage` and `research.mycelic.test_mycelic` must pass for any accepted change; both run in CI.
  * **Robustness study:** before the final report, a harsher-operator study applied identically to every architecture is required (correlated extraction misses, a text-derived echo signature with paraphrase noise, date and polarity errors at small tiers, hallucinations that cite evidence). It is reported as a sensitivity analysis, not as the target metric.
