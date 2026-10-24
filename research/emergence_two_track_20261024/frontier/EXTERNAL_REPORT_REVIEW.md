# Review of the external deep-research report, and its first experiment

Input: `EXTERNAL_DEEP_RESEARCH_REPORT.md` (user-supplied, 2026-10-24). Facts it is checked against: `CONTEXT.md`.

## Citation status: UNVERIFIED

The report cites 22 arXiv papers (Aug 31 – Oct 21, 2026). None could be checked from this session: arxiv.org is not
reachable from the container (egress proxy `connect_rejected`; the fetch tool returns `ENOTFOUND`). The identifiers
are in valid `YYMM.NNNNN` form; nothing here confirms that any title, author, date or reported number is real. **Do not
cite them until each abs page has been fetched and matched** on a machine with arXiv access. Note: several IDs carry a
`2609` prefix with an "Aug 31" date, which is worth checking specifically.

## What the report gets right

- Bound before building: its Gate 1 (evidence-availability ceiling) and Gate 2 (oracle routing) come first, with the
  explicit rule that if the evidence oracle is below 85%, routing cannot close the gap.
- The error decomposition (absent → link → route → retrieve → combine → judge) matches `PLATEAU_DIAGNOSIS.md` §1.
- Controls: equal-budget comparisons, hypergraph vs its pairwise projection, shuffled-hyperedge nulls, and never adding
  percentage gains from different papers.
- The right objective: conditional utility $I(Y; X_i \mid q, E)$, not similarity.

## Its gates against measurements already made here

| Report gate | Status |
|---|---|
| G0 reproduce "~63%" | Premise wrong: ~63% is a diagnostic (v2/v3). Official baseline **93/240 = 0.3875**, checks green, seeds 0–5 (`raw/official_ce0e357/`). Best diagnostic 0.679 (v4). |
| G1 evidence ceiling ≥ 85% | **Measured, fails**: pattern-evidence ceiling 0.746 at 200 users, **0.683 at 10K**. Key-volume ceilings reach 0.92 at 10K only because the count gate then approves ~70% of keys with no pattern and 93% of decoys. |
| G2 oracle routing | Not the loss: each site's retrieval reads every node; an offline evidence check predicts the system's verdict on 98.6% of questions. |
| G3 exact vs ANN | Not applicable: no ANN index; hash embeddings, no edges, exhaustive search. |

By the report's own decision tree this is the branch "oracle < 85% → fix ingestion, representation, labels or task
assumptions", which is what the scale measurement concluded independently (evidence specificity, not routing).

## What does not transfer

The papers address centralized text QA (one corpus, all documents embedded together, an LLM generating). EMERGENCE has
no raw text crossing the Boundary (only k-suppressed counts), no LLM in the loop (lexical judge), and exhaustive
site-local retrieval. Mechanisms that pool text or entities across owners (G³RAG/SG²RAG graphs, HyperReCo-style
cross-shard entity hyperedges) change what crosses the Boundary: a founder decision, not a retrieval upgrade.

## First experiment: does chain structure restore specificity? (rejected at scale)

Hypothesis (from the report's "higher-order relations" thread, adapted to the Boundary): requiring the key's records to
show distinct chain stages in causal order — stages from each record's extracted predicate and the pack's public chain
definition; no generator label read — restores the specificity the count gate loses as the world grows.

Gates (all on top of the v4 count gate, key model): `local2` = some site's own records hold ≥2 distinct stages in order
(site-local; exports nothing new); `glob2`/`glob3` = ≥2/≥3 distinct stages in order across sites (needs per-stage
presence to cross the Boundary; upper bound only). Script `tools/plateau/structure_gate.py`; output
`raw/plateau/structure_gate_by_world_size.txt`; dev seeds 0–5; offline model, not a run.

| Gate | 200 users: patterns / non-planted keys passing | 2,000 | 10,000 |
|---|---|---|---|
| count | 0.825 / 9.6% | 0.950 / 69.9% | 0.921 / 69.4% |
| local2 | 0.600 / 3.9% | 0.854 / 54.4% | 0.846 / 58.2% |
| glob3 | 0.654 / **0.9%** | 0.579 / 35.3% | 0.571 / **43.8%** |

At 200 users order separates almost perfectly (and brings decoys to 106, under the bar of 118; type-2 scramble 59 → 7).
At 10K it still passes 44% of keys with no pattern while cutting the pattern ceiling to 0.571: a dense key shows several
stages in plausible order by chance. **Rejected as a scale fix.** Caveat: non-planted keys are tested over all weeks
(more chances) and patterns only in their label window; the bias is the same for every gate, so the comparison holds.

Next hypotheses to falsify the same way: consecutive stages within a bounded time gap (the generator spaces facets by
days); the same event confirmed by several distinct reporters (roots vs reports); per-stage bursts relative to the
key's own baseline. Each must be site-visible or state what new field it needs to export.

## Second round: reporters, bounded gaps, bursts (all rejected as scale fixes)

- **Same event confirmed by several reporters: rejected by inspection.** Planted facet records are each their own event
  (1.0 reports per event); background chain records share events (7.2 reports/event at 200 users, 1.5 at 10K).
  Multi-reporter confirmation selects echoes, not patterns. Planted facets are also not always in time order (one 10K
  pattern: days 57, 73, 91, 75), which is part of why the stage-order gate cost recall.
- **Bounded-gap stages and key-relative bursts** (`tools/plateau/burst_gate.py`,
  `raw/plateau/burst_gap_gate_by_world_size.txt`; on top of the count gate; burst = Poisson excess of the window over the
  key's own prior weeks only, never future weeks):

| Gate | 200 users: patterns / non-planted keys passing | 2,000 | 10,000 |
|---|---|---|---|
| count | 0.825 / 9.6% | 0.950 / 69.9% | 0.921 / 69.4% |
| gap21 (≥3 consecutive stages ≤21 days apart) | 0.571 / **0.4%** | 0.729 / 29.9% | 0.708 / 40.2% |
| burst z≥3 | 0.708 / 6.6% | 0.704 / 29.5% | 0.650 / 28.4% |
| burst z≥4 | 0.679 / 5.1% | 0.642 / 20.2% | 0.575 / 18.8% |

**At 10K every key-level, k-safe signal measured trades recall for specificity on a frontier far below the target**:
the best operating point still passes about one key in five with no pattern at a pattern ceiling under 0.6. Within this
representation (per-key aggregates under k per site), 85% at 10K with a tolerable false-pass rate is not reachable.
Untested: organisational structure of a key's reporters (pattern facets come from specific users over a few regions and
sites; background reporters are random) — the report's hypergraph idea in the form that fits the Boundary.
