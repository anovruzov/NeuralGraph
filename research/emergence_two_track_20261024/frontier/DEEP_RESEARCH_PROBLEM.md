# Deep research problem: discovering rare distributed truths under per-owner privacy

*A self-contained research brief for Mycelic (EMERGENCE) and NeuralGraph (memory QA). Written 2026-10-24 from
measurements on branch `claude/mycelic-gate-0-recovery-tjl82x`. Every number cites the file that holds its raw
output. Nothing here is a forecast.*

---

## 0. How to work on this

You are asked to **think, not to tune**. The team has already spent many cycles on downstream fixes: retrieval,
graph edges, routing, re-asking and recursive follow-up. Measurement shows the binding constraints lie elsewhere.
Your job is to find what is true, and whether 80–85% is reachable at all under each possible set of rules.

Rules:

1. **Bound before you build.** A proposal must name the measured number it moves (§4), and how it would show up in
   the funnel. If it cannot, say so.
2. **Verify every citation.** Fetch the abstract page and match title, authors, date and the reported number.
   Mark anything you cannot fetch as UNVERIFIED. A previous literature report cited 22 arXiv IDs that this team
   could not verify; do not repeat that.
3. **Report against interest.** If your best idea fails a falsification test, the failure is the result.
4. **Keep "diagnostic" and "official" apart.** Diagnostic runs bypass a guard or skip drills; they are never
   baselines.
5. **A privacy change is a decision for the founder, not a tweak.** You may — and should — analyse what a different
   disclosure contract would buy. Say clearly that adopting it is the founder's call.

---

## 1. The problem in one paragraph

Thousands of agents (employees) each write records into their site's private store. A real cross-site pattern — a
causal chain of events about one entity, such as a supplier — leaves **a few records at each of several sites**. To
protect individuals, every site exports only **weekly counts per (entity, predicate), and suppresses any count below
k = 3** (exported as `'<k'`). HQ sees only these suppressed aggregates. It must decide which keys to investigate,
ask sites targeted yes/no questions, and conclude which patterns are real. Decoys, background chatter and echoes of
a single event must not be confirmed.

**Research question:** what is the highest discovery accuracy any algorithm can reach, under which disclosure
contract? And what is the smallest change to that contract — paired with which inference algorithm — that reaches
≥80–85% recall on rare patterns at 10,000–50,000 agents, with false discoveries held at or below today's level?

A second, separate track (§6) is NeuralGraph's single-store conversational memory QA, which sits at 66.7%.

---

## 2. The system, precisely

### 2.1 The world (EMERGENCE generator, `research/e2e_ingest/emergence_world.py`)

| target users | users | sites | records | patterns | of which rare (seed 0) | decoys | entities |
|---|---|---|---|---|---|---|---|
| 200 | 309 | 7 | 12,964 | 40 | 18 | 50 | 150 |
| 2,000 | 1,999 | 9 | 68,463 | 40 | 9 | 50 | 150 |
| 10,000 / 50,000 | benchmark targets; worlds generated offline for ceilings | | | 40 | | 50 | 150 |

- 26 weeks; each record has a user, a site, a day, an entity anchor (plus an optional aux entity), a fine predicate
  mapped to a chain predicate, a polarity, and an event id (several records can report one event).
- **Patterns:** 40 planted causal chains, each with an anchor entity and a chain. Their facet records are spread over
  a few users, sites and regions, and each has a label window in which finding it counts.
- **Decoys:** 50, in 5 classes (e.g. scrambled order, echoes, single-site bursts). They must *not* be confirmed.
- **Background:** ordinary chain-predicate records on the same entities. Background grows with the number of users;
  the 40 patterns do not. That asymmetry is central (§4.3).

### 2.2 The pipeline (all in-process; NATS JetStream is the real fabric in e2e runs)

```
records ──► site store (private; raw text never leaves)
          │  lexical extraction → claims; weekly cells per (entity, predicate): n, roots, reporters
          ▼
  site Boundary: k-suppression per site (count < 3 → '<k'), near-duplicate-root collapsing (opt-in)
          ▼
  HQ store: suppressed cells from every site
          ▼
  HQ detection (mycelic/collective/detect/detectors.py): detectors D2–D7 (Poisson tail, PMI, history) and a rules
  channel over the cells → candidates (key, snapshot week, score); rule-only keys can get snapshots (opt-in)
          ▼
  question selection: top TOP_N = 60 candidates per run by detector score (rule-only snapshots score 0.0)
          ▼
  routing: TesseractCoordinator (NeuralGraph/coordination/) picks m = 3 sites per question
          ▼
  site verification (mycelic/collective/edge/verify.py):
     reads = the shipped entity retrieval's first L = 50 matches; Tesseract's ranking fills any room left
     lexical judge per record: mentions_entity, describes_predicate
     decide: confirm if any read record is yes/yes; refute if records mention the entity but none describes the
     predicate; else unknown
          ▼
  pushdown gate (TB_v4: count, roots, reporters, freshness; N1 weak corroborator; N2 roots only from k-root sites)
          ▼
  hq_bridge → signed events on the fabric → conclusions (with lineage)
          ▼
  scorer: a pattern is FOUND if a supported, lineage-checked conclusion sits on its (anchor, chain) key with a
  snapshot week inside its label window
```

### 2.3 Metrics

- **Recall** = patterns found / 240 (40 patterns × dev seeds 0–5). Rare recall is reported separately.
- **Decoys accepted** (out of 300) — the bar is ≤ 118, the official baseline's count.
- **Background-supported** = confirmed keys carrying no pattern and no decoy.
- Dev seeds 0–5 only. Sealed seeds 94000–94009 stay untouched until a frozen candidate exists.

---

## 3. Non-negotiable constraints, as they stand today

- Raw owner-level text never leaves a site. HQ sees only k-suppressed aggregates and the answers sites give to
  questions.
- No wildcard authorization. A logical site ID never grants access to evidence.
- No synthesized failure domains, no gold labels at inference time, no training on confirmation worlds.
- Coordination stays domain-neutral (`contracts.py`, `core.py` import no storage types).
- Never refresh a pinned artifact to get green; never skip, xfail or quarantine a test.

The per-site k-floor is in this list *as the current contract*. §5-P1 asks what changing it would buy. Changing it
is a founder decision.

---

## 4. What is measured (and therefore off the table to re-propose)

### 4.1 Where accuracy stands

| configuration | status | recall | rare | decoys | background-supported | questions / seed |
|---|---|---|---|---|---|---|
| official (Track A) | official, all checks green | **93/240 = 0.3875** | — | 118 | 3 | ~57 |
| v4 (`rs_nd_n1_n2`: rule snapshots, near-duplicate roots, N2, all candidates asked) | diagnostic | 163/240 = 0.679 | 27/103 | 181 | 96 | ~530 |

Raw: `raw/official_ce0e357/`, `REPORT.md`. The "~63%" figure that circulated was a diagnostic (v2/v3), not a baseline.

### 4.2 The funnel (official run, all 240 patterns; `raw/plateau/gap_decomposition.csv`)

| state | patterns | rare | feasible under F/TB_v4 |
|---|---|---|---|
| found | 93 | 5 | 92 (+1 background-assisted: seed 4 p022) |
| asked, wrong verdict | 5 | 4 | 4 |
| never asked | **142** | 94 | 83 |

Judging is near-perfect on what it is given (93 of 98 asked). **The loss is upstream of retrieval:** which keys
become questions, and whether the evidence clears the per-site k-floor at all.

### 4.3 Ceilings: perfect detection, routing and judging; best week in the label window (`raw/plateau/`)

Evidence models: **F** = the pattern's own facet records only; **K** = every record on the key; **B** = K counted
polarity-blind.

| world | F | K | B | non-planted keys passing the count gate | decoys passing (of 300) |
|---|---|---|---|---|---|
| 200 users | 0.746 | 0.829 | 0.854 | 9.6% | 219 |
| 2,000 | 0.738 | 0.950 | 0.963 | 69.9% | 270 |
| 10,000 | **0.683** | 0.921 | 0.929 | **69.4%** | 278 |

Under per-site k-suppression, **planted evidence alone cannot exceed 0.683 at 10K**. K and B go higher only because
background records on the key push it over the threshold. At that scale the same gate passes ~70% of keys that carry
no pattern.

### 4.4 New: what the per-site k-floor itself costs (`tools/plateau/agg_ceiling.py`, `raw/plateau/agg_ceiling_by_world_size.txt`)

Same windows and gate conditions. "agg" applies k to the cross-site **total** (records ≥ 3, reporters ≥ 3,
roots ≥ 3, ≥ 2 contributing sites), as a secure sum across sites would allow:

| world | gate | F patterns | F rare | K patterns | non-planted keys passing |
|---|---|---|---|---|---|
| 200 | per-site (TB_v4) | 0.742 | 41/103 | 0.825 | 16.5% |
| 200 | **aggregate-then-threshold** | **1.000** | **103/103** | 1.000 | 43.7% |
| 2,000 | per-site | 0.738 | 29/92 | 0.950 | 73.2% |
| 2,000 | aggregate | **1.000** | **92/92** | 1.000 | 96.5% |
| 10,000 | per-site | 0.683 | 25/101 | 0.921 | 72.4% |
| 10,000 | aggregate | **1.000** | **101/101** | 1.000 | **98.5%** |

**Every planted pattern, at every world size, is visible once k is applied after aggregation rather than before.**
The per-site floor alone causes the entire planted-evidence recall loss (0.683 → 1.000 at 10K).

The price is total loss of specificity: a count gate on the aggregate passes 98.5% of keys with no pattern at 10K.
Counts carry the recall; they carry almost no specificity. So the problem splits cleanly in two:

- **recall is a disclosure question (P1);**
- **specificity is a statistics question (P2)** that today's count gate does not attempt.

**Specificity on the totals, measured** (`tools/plateau/agg_spec.py`, `raw/plateau/agg_specificity_by_world_size.txt`).
A burst test on the cross-site total — a Poisson excess over the key's own prior six weeks, never later weeks —
gives the following at 10K:

| gate (10K users) | recall ceiling | rare | non-planted keys passing |
|---|---|---|---|
| per-site rule (today) | 0.683 | 25/101 | 72.4% |
| total + burst z ≥ 3 | **0.912** | 88/101 | **64.6%** |
| total + burst z ≥ 4 | **0.812** | 65/101 | **46.4%** |

**Aggregate-then-threshold with a burst test dominates today's rule at 10K on both axes:** more recall *and* fewer
false passes. The absolute false-pass rate is still high. The site judge and the question budget filter further
downstream, and a stronger statistic (P2) is still needed. At 200 users, z ≥ 3 gives 0.900 recall at 26.3% (today:
0.742 at 16.5%).

(The non-planted rates here count any week of the run, so they are higher than §4.3's; compare rows within this
table, not across tables.)

### 4.5 Ruled out by measurement — do not re-propose without new evidence

| idea | result | where |
|---|---|---|
| better site retrieval, graph edges, multi-hop (NeuralGraph `TemporalChainManager`, `NeuralRetriever`) | The benchmarked path persists 0 edges and never calls them. A site reads every entity-matched record first; **0 of 2,940** judge "mentions" fall outside that match, so no ranker can add a confirm. Edges on vs off: **0 of 539 verdicts differ**. | `frontier/NATIVE_GRAPH_PROOF.md` |
| routing / oracle routing | an offline evidence check predicts the system's verdict on 98.6% of questions | `PLATEAU_DIAGNOSIS.md` |
| recursive evidence acquisition (ask holders for out-of-window records) | targeted 5/42 vs random 4/42 recovered; precision 4.3% vs 5.4% — **no better than its own control** | `RECURSIVE_ACQUISITION.md` |
| re-asking later | +4/240 on the asked-wrong bucket. On v4: ask once when ready 165 → 173; ask every 2 weeks 199, but at 13,361 questions (~25× the budget), and echo decoys rise from 24 to 53–56 | `EVIDENCE_CEILING.md`, `raw/plateau/v5b_offline_seeds0-5.txt` |
| promoting rule-only candidates at a fixed budget | every arm loses patterns; the rule-only pool yields ~1 pattern per 220 questions | `frontier/CANDIDATE_RESCUE.md` |
| k-safe specificity gates at 10K: stage order, bounded gaps, key bursts, per-site bursts, multi-reporter | all trade recall for specificity, on a frontier far below target. Best specific point: two-site bursts — 1.4% of non-planted keys pass, but 0/101 rare found | `frontier/EXTERNAL_REPORT_REVIEW.md` |

### 4.6 The one positive lever found inside the current contract

At the same 60-question budget, the detector pool holds 84–90 candidates per seed, containing 151 findable patterns.
The shipped score ranks poorly: patterns per 15-rank band run 23, 24, 30, **41** (ranks 45–59), 28. Ordering
detector candidates **with a rule hit on or before their snapshot week first** finds **128 vs 118**, with identical
decoys (113) and background (13). That is the best of ten orderings on dev seeds; end-to-end confirmation is running.
The bound with labels is 151. (`frontier/CANDIDATE_RESCUE.md`)

---

## 5. The open problems, ranked by leverage

### P1 — The disclosure contract (the real lever; a founder decision)

The per-site k-floor destroys the signal before any representation, graph or algorithm sees it. By the data
processing inequality, no downstream architecture can recover what the Boundary suppressed. §4.4 shows the cost:
planted recall goes from 0.683 to 1.000 at 10K when k applies after aggregation. That disclosure also admits 98.5%
of keys with no pattern, so P1 cannot be adopted without P2.

Questions:

1. **Formalise it.** For a key with true per-site counts c₁…c_S, compare what is observable:
   - per-site suppression: each cᵢ is seen only if ≥ k;
   - secure aggregation: only Σcᵢ (and perhaps the number of contributing sites), thresholded at k;
   - differentially private counts: noisy counts with budget ε.

   What is the detection power for a planted chain against Poisson background, as a function of S, the background
   rate (which grows with users) and the per-site spread of facets? Derive the recall–false-positive frontier under
   each contract.
2. **Privacy equivalence.** Does secure aggregation with a k-threshold on the total give the same protection to an
   individual reporter as per-site k? Consider small sites, intersection and differencing attacks across weeks and
   keys, and the number of contributing sites. Where it does not, what extra rule restores it (for example a minimum
   number of contributing sites, a minimum total, or DP noise)? Relevant foundations: practical secure aggregation for
   federated learning (Bonawitz et al., 2017), k-anonymity and its limits, and differential privacy's composition
   over repeated weekly releases. Verify the citations.
3. **The minimal contract that clears the bar.** Which contract, combined with which detector, reaches ≥80–85% recall
   at 10K with decoys ≤ 118 and background-supported near today's level? State the residual privacy risk precisely.

### P2 — Specificity at scale (the problem P1 creates)

Once the signal is visible, the challenge is false discovery. Background grows with users while patterns stay at 40.
At 10K, ~70% of chain keys with no pattern already pass a count gate.

1. What k-safe (or contract-safe) statistics separate planted causal chains from dense background?
   - Measured: stage order, bounded gaps and bursts trade recall away at scale.
   - Untested: spatial-temporal scan statistics over the organisation (site → region → org), changepoint and
     sequential tests per key with **FDR control across the ~thousands of keys tested per week** (Benjamini–Hochberg,
     e-values, knockoffs).
   - Untested: the organisational concentration of reporters. Indicative only: at region level it persists at 10K
     (0.299 vs 0.425 for random keys); at team level it vanishes.
2. Is "a pattern" in this generator separable from background at 10K at all? Compute the Bayes-optimal detector on
   the *full* (unsuppressed) data as an absolute ceiling. If even that is below 85%, the target is a property of the
   benchmark, not of the system — say so.

### P3 — Selecting questions under a budget

The pool almost fits the budget (86 vs 60), yet the shipped score is close to random (118 vs 104.9 ± 4.6). Learning to
rank from system outputs (verdicts, never gold), contextual bandits that allocate questions by expected discovery
value, and calibration of the detector score are all open. The bound is 151/240 inside the current contract.

### P4 — Question timing

When should a candidate be asked? A key asked before its evidence matures gets refuted; one asked late misses its
window. "Ask once when ready" helps a little (+8 on v4). Is there an optimal stopping formulation that lifts this
without multiplying questions?

### P5 — The judge

The lexical judge confirms only on entity mention plus a predicate pair. An LLM judge (the local Qwen track) might
accept paraphrase and alias records that the shipped entity retrieval does not match. That is the **only** setting
in which site retrieval, graph edges or multi-hop traversal can change accuracy (§4.5). Should the judge change, and
how do we keep its false confirms bounded?

### P6 — Scale (10K–50K) as a systems question

Detection cost, cell volume and question fan-out at 50K, and whether the conclusions of P1–P4 hold as S and the
background grow. Every claim needs multiple seeds and confidence intervals.

---

## 6. Track 2 — NeuralGraph memory QA (single store, LoCoMo-style)

Separate benchmark, separate owner (Nurman). Artifact: `demo/maximal.json`, 1,540 questions, GPT-4o judge.
Diagnosis: `docs/ACCURACY.md`.

| category | n | accuracy |
|---|---|---|
| multi-hop | 841 | 74.1% |
| temporal | 321 | 64.5% |
| single-hop | 282 | **52.1%** |
| open-domain | 96 | 52.1% |
| **total** | 1,540 | **66.7%** |

Of 513 wrong answers:

- 147 selection errors (had the evidence, answered something else);
- 120 incomplete lists;
- 60 wrong abstentions;
- 139 genuine retrieval misses;
- 47 unanswerable (the gold answer appears nowhere in the conversation).

So the hard ceiling is **96.9%**. About 70% of failures had the evidence retrieved: generation, not retrieval. 85%
requires converting 60% of every recoverable failure; generation fixes alone plausibly reach ~79%.

Open problems:

1. An answer contract that fixes selection, completeness and abstention together. It is drafted in
   `evaluation/replay_generation.py`, but has no measured run yet; it needs a live model.
2. Then the 139 retrieval misses: are they lexical gaps, temporal anchoring, or entity resolution?
3. Why single-hop is *worse* than multi-hop: an answer-type mismatch on "what/which" questions accounts for 49
   failures.

Rule: the retrieval-track files (`answering.py`, `llm_profile_extractor.py`, `prompts.py`, `reranker.py`,
`service.py`, `tesseract.py`, `demo/runner.py`) may not be edited without Anar's explicit say-so. Calling them is fine.

---

## 7. What to deliver

1. **A formal model of P1/P2.** Observation model per disclosure contract, detection power and FDR under Poisson
   background with a planted chain, and the recall–false-discovery frontier as world size grows. Check it against
   §4.3–4.4.
2. **A ranked list of hypotheses.** For each: mechanism, the measured number in §4 it moves, the prediction, and the
   falsification test runnable with our tools (offline ceiling scripts first, then an e2e arm).
3. **A privacy analysis** of any contract change, written for a founder decision. State the risk you accept and the
   risk you remove.
4. **Verified literature only.** Each paper needs its abstract page fetched, its title and date matched, and its
   result restated with its evaluation setting.
5. **An honest verdict on the target.** Under the current contract, is 80% at 10K impossible (the measured F ceiling
   is 0.683)? Under which contract does it become possible, and at what false-discovery cost?

## 8. Where everything lives (`research/emergence_two_track_20261024/` unless noted)

| what | file |
|---|---|
| headline results, labels | `REPORT.md`, `PLATEAU_DIAGNOSIS.md` |
| funnel and ceiling | `EVIDENCE_CEILING.md`, `raw/plateau/gap_decomposition.csv`, `raw/plateau/ceiling_by_world_size.txt` |
| specificity | `raw/plateau/specificity_by_world_size.txt`, `frontier/EXTERNAL_REPORT_REVIEW.md` |
| per-site vs aggregate threshold | `tools/plateau/agg_ceiling.py`, `raw/plateau/agg_ceiling_by_world_size.txt` |
| site graph / edges proof | `frontier/NATIVE_GRAPH_PROOF.md` |
| budget-matched selection | `frontier/CANDIDATE_RESCUE.md`, `tools/rescue/` |
| recursive acquisition | `RECURSIVE_ACQUISITION.md` |
| integrity and pins | `BENCHMARK_INTEGRITY.md`, `frontier/PIN_LEDGER.md` |
| NeuralGraph QA | `docs/ACCURACY.md` (repo root) |
