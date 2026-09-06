# NeuralGraph Benchmarks

One page for every benchmark result across the project's three research
tracks, with the source commit and evidence class for each number.
Compiled 2026-09-06 during the repository audit.

> **Read the labels carefully.** The LoCoMo category constants inherited
> from the December code swap single-hop and multi-hop (dataset category
> 1, n=282, averages 3.13 evidence messages per question; category 4,
> n=841, averages 1.07). Retrieval tables below use the labels **as
> reported** in the campaign docs; PR #1 carries the corrected mapping.
> Aggregates are unaffected — only which row is called what.

---

## Track A — Coordination: capability survival (Anar)

Source: `NeuralGraph/coordination/artifacts/` at `13a1729`
(branch `claude/session-analysis-continuation-sm1a1t`).
Deterministic simulator, seed 20260813, 30-seed sweep, 5-node fixture.
**Evidence class: bitwise reproducible** — all SHA256SUMS verify and all
four JSON artifacts regenerate byte-identically (re-verified 2026-09-06,
macOS arm64, Python 3.11.14; PYTHONHASHSEED-independent). 277 tests pass.

Mean capability survival per (strategy, intervention), 30 seeds:

| strategy | node fail | mem del | lineage root | auth revoke | route rm | edge corrupt | stale | partition | worst domain | **mean** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| isolated_local | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.000** |
| centralized | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | **0.111** |
| fixed_distributed_replication | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| source_count_repair | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| full_replication | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | **0.667** |
| random_path_diversification | 1.00 | 1.00 | 0.67 | 0.67 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.704** |
| **lineage_aware_repair** | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.778** |
| oracle_min_cut | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | **0.889** |

Survival, unrecovered silent forgetting, transfer volume:

| strategy | survival | unrecovered forgetting | mean bytes |
|---|---:|---:|---:|
| isolated_local | 0.000 | 0.000 | 1,371 |
| centralized | 0.111 | 0.667 | 3,020 |
| fixed_distributed_replication | 0.556 | 0.444 | 4,943 |
| source_count_repair | 0.556 | 0.444 | 4,941 |
| full_replication | 0.667 | 0.333 | 9,877 |
| random_path_diversification | 0.704 | 0.296 | 5,327 |
| **lineage_aware_repair** | **0.778** | **0.222** | 5,448 |
| oracle_min_cut | 0.889 | 0.111 | 6,461 |

Notes that must survive into any citation: "bytes" is serialized
transfer volume, not resident-memory savings; forgetting shown is
*unrecovered* after repair; full replication survives partition while
the repair strategies do not; scope is a small-N deterministic simulator
— it validates the mechanism, not 1K–10K-agent populations.

Invariants (from CLAUDE.md — if one moves, a claim about the world
changed): replica counting survives lineage-root failure at 0.00;
lineage_aware 0.778 / oracle 0.889; only min-cut-2 survives
worst_single_domain_failure; every scale verdict invariant across
K ∈ {2,3,5,8}, H ∈ {2,3}; lineage_aware loses to source_count in exactly
one of six cells (node_failure, I=1).

---

## Track B — Retrieval: the LoCoMo campaign (Nurman)

Source: `docs/research/RESULTS_ALL.md` + `demo/results/*.json` at
`e054178` (merged as PR #3, 2026-09-06). LoCoMo, 10 conversations,
1,540 questions. Models: gemma-4-e4b (answer/rerank/campaign judge),
nomic-embed-text v1.5, qwen3.6-35b (independent judge). Leakage-free
harness (gold-answer gate and gold-category routing removed).

### Headline — shipped system, conversations 1–5 (744 of 1,540 questions)

| category (as reported) | n | new (Gemma lenient) | old leaky, same questions (GPT-4o lenient) |
|---|---:|---:|---:|
| single_hop | 142 | 73.9% | |
| multi_hop | 400 | 72.8% | |
| temporal | 156 | 73.7% | |
| open_domain | 46 | 56.5% | |
| **overall** | **744** | **72.2%** | **66.9%** |

Different judges per column — the +5.3 is indicative. Conversations
6–10 and the adversarial category are not yet run. Shipped config:
`RETRIEVAL_MODE=local_pairs` + open-domain flags.

### The clean A/B — same 282 questions, same judge, only retrieval changed

| retrieval | accuracy | recall@10 | recall@50 | s/q |
|---|---:|---:|---:|---:|
| flat (original Tesseract) | 64.9% | 39.4 | 61.7 | 6.4 |
| hybrid (speaker boost + embed back-fill) | 67.0% | 44.7 | 61.7 | 8.2 |
| graph (time chain + speaker + dialogue links) | 66.7% | 39.4 | 61.7 | 7.9 |
| **local_pairs (per-agent routing + pair back-fill)** | **73.8%** | 46.8 | 62.8 | 7.9 |

The +8.9 flat→shipped gain survives all four judges (+11.3 Qwen lenient,
+9.6 Qwen strict, +4.6 substring; p < .001) — the campaign's most
defensible single result.

### All 18 experiments

| # | experiment | level | result | verdict |
|---|---|---|---|---|
| H9 | per-agent memory routing | retrieval + e2e | recall@10 +7.4; e2e +8.9 with pairs | **works** |
| H3 | pair nodes as back-fill | retrieval | +3.2 recall@all | **works** |
| H5 | open-domain prompt flags | e2e | +14.6 lenient, −1.0 strict | lenient-judge only |
| — | speaker boost + embed back-fill | e2e | +2.1 | small |
| — | graph-neighbour expansion | both | +1.4 recall, +1.8 e2e | ≤ embedding back-fill |
| H1 | listwise reranker | e2e | −4 accuracy, −56% latency | speed only |
| H2/N1 | wider / narrower context | e2e | −2…−9 either direction | hurts |
| H7 | LLM router | e2e | +1 / −2 | null |
| H4/H6 | scoring-formula fixes | retrieval | within noise | null |
| N3 | reply-only pairs | retrieval | −1 to −2 | hurts |
| N4 | pair-aware reranker/prompt | e2e | −7 / −3 / −12 | hurts |
| H8 | 35B answer model | e2e | −7 lenient, +4 strict | judge artifact |
| M1 | mega search (vector+BM25+graph hop) | both | recall@10 +6.9; e2e −10 single_hop | hurts e2e |
| M2 | LLM-built entity graph | both | recall@10 +5.0; e2e −8 single_hop | hurts e2e |
| N5 | judge sensitivity | evaluation | 51-point spread by grader | **paper-grade** |

### Judge sensitivity — same answers, four graders

| answer file | n | Gemma lenient | Qwen lenient | Qwen strict | substring |
|---|---:|---:|---:|---:|---:|
| flat, single_hop | 282 | 64.9 | 51.4 | 21.6 | 13.5 |
| shipped, single_hop | 282 | 73.8 | 62.8 | 31.2 | 18.1 |
| H5 open-domain treatment | 96 | 57.3 | 39.6 | 10.4 | 9.4 |

Judge model alone moves scores ~12 points at identical prompt; prompt
leniency ~30 points. Kappa vs campaign judge: Qwen lenient 0.72, strict
0.30, substring 0.17. **Any headline number must name its judge.**

### Mechanics worth keeping

- Per-agent routing equals oracle routing (gold spoken by the other
  agent only 2.9% of the time).
- Speaker names are embedding-level pointers to the *wrong* speaker
  (0.1% own-name vs 33.4% other-name incidence).
- The entity-graph PageRank hop is the only graph mechanism that beat
  plain embeddings at retrieval (+6.9 recall@10) — and still loses
  end-to-end by flooding the context with related non-answers.
- Retrieval decides; the reranker only reorders. 15/30 context memories
  is the measured optimum.

---

## Track C — The December baseline (superseded, leaky)

Source: `demo/maximal.json` (December 2025, GPT-4o lenient judge, gold
answer used as acceptance gate and gold category for routing —
superseded by Track B's leakage-free harness).

| measure | value | note |
|---|---:|---|
| overall accuracy | 66.7% | 66.9% on the 744-question comparison cohort |
| single_hop (as labeled) | 52.1% | worse than multi_hop (74.1%) |
| failures with evidence retrieved | ~76% | generation problem, not retrieval |
| unanswerable questions | 47 | gold appears nowhere in the conversation |
| hard ceiling | 96.9% | not 100% |

Diagnosis: `docs/ACCURACY.md` at the lineage snapshot. These numbers
exist to be compared against, not cited.

---

## Evidence classes at a glance

| track | headline | evidence class |
|---|---|---|
| Coordination survival | lineage_aware 0.778 vs oracle 0.889 | bitwise reproducible (pinned + SHA256SUMS + tests) |
| Retrieval flat→shipped | +8.9, survives all four judges | judge-robust, committed per-question artifacts |
| Retrieval overall | 72.2% vs 66.9% | partial cohort (conv 1–5), cross-judge, lenient |
| December baseline | 66.7% | leaky, superseded |
| Agentic Web N=100/1K/10K | — | proposed only; no experiment exists |
