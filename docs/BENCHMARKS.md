# NeuralGraph Benchmarks

> **Correction, 2026-10-09: LoCoMo category names were swapped.** Until
> 2026-10-09 `research/benchmarks/runner.py` (and the harnesses that import its
> `CATEGORIES`: `retrieval_eval.py`, `two_agent_eval.py`) mapped dataset category 1
> to `single_hop` and category 4 to `multi_hop`. The dataset says the reverse:
> category 1 questions cite 3.14 evidence turns on average (n=282) and category 4
> questions 1.07 (n=841). The harness now maps 1 to `multi_hop` and 4 to
> `single_hop`. Every per-category number in this file produced before that date
> carries the swapped names: a row labelled `single_hop` is category 1 (multi-hop
> questions) and a row labelled `multi_hop` is category 4 (single-hop questions).
> The numbers are left as they were reported; totals are unaffected. Result files
> written before that date also carry a fixed judge string in
> `metadata["judge"]` that does not record which judge ran; runner.py now writes the
> configured judge model id (or `unknown`). Evidence counts from:
> `python -c "import json,re;from collections import defaultdict as D;e=D(list);[e[q['category']].append(len([p for x in q.get('evidence',[]) for p in re.split(r'[;,\s]+',x) if p])) for c in json.load(open('research/datasets/locomo10.json')) for q in c['qa']];print({k:(len(v),round(sum(v)/len(v),2)) for k,v in sorted(e.items())})"`
> which prints `{1: (282, 3.14), 2: (321, 1.17), 3: (96, 2.17), 4: (841, 1.07), 5: (446, 1.03)}`.

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

Source: `NeuralGraph/research/coordination/artifacts/` at `13a1729`
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

Source: `research/reports/RESULTS_ALL.md` + `research/results/*.json` at
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

### Evidence recall by dia_id and the router fallback (2026-10-09, hash embedder)

**These are hash-embedder numbers: structural, not retrieval quality.** The
embedding server cannot be reached from the sandbox, so every run below used
the deterministic hash embedder (`EMBEDDER=hash`). They show which messages
the code can reach. Real embeddings will move them.

What changed:

- `retrieval_eval.py` now scores evidence recall. For each question, `ev@k`
  is the share of its gold evidence dia_ids (`qa["evidence"]`, joined entries
  such as `D8:6; D9:17` split) found among the dia_ids of the first k
  retrieved messages. It is averaged per category over the questions that
  cite evidence: 1,536 of the 1,540 category 1–4 questions.
- Tesseract's router scores five types and OPEN has no store. Of those 1,536
  questions, 895 score highest on OPEN and 27 fire no marker. Each store passed
  only its top 40 / 50 / 40 / 30 to fusion, whatever the route. A route with no
  store now falls back to every store: each store passes every node it charges,
  and fusion sums all four charges. Routes with a store are unchanged
  (`tesseract.routed_store_type`, tests in
  `NeuralGraph/tests/test_tesseract_router.py`).

Evidence recall, all ten conversations, from
`EMBEDDER=hash VARIANTS=embed,tesseract python3 research/benchmarks/retrieval_eval.py`.
Before is commit 08dae54 (scorer only), after is 0f78362 (router fallback).
Embedding rows are the same at both commits.

| category | n_ev | embed ev@10 | embed ev@50 | Tesseract before ev@10 | before ev@50 | after ev@10 | after ev@50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi_hop (cat. 1) | 282 | 6.4% | 19.4% | 25.4% | 44.6% | 26.6% | 51.6% |
| temporal (cat. 2) | 321 | 23.0% | 47.5% | 68.8% | 82.1% | 69.1% | 83.2% |
| open_domain (cat. 3) | 92 | 6.5% | 19.2% | 22.6% | 35.7% | 24.4% | 43.9% |
| single_hop (cat. 4) | 841 | 25.4% | 47.9% | 57.5% | 72.8% | 62.2% | 78.4% |
| **all** | **1,536** | 20.3% | 40.8% | **51.9%** | **67.4%** | **54.8%** | **72.4%** |

The answer-substring recall in the same runs moved from 33.6% to 35.0% at
k=10 and from 44.3% to 46.6% at k=50 (1,540 questions). For multi_hop it fell
from 38.3% to 36.5% at k=10 while ev@10 rose. Substring recall misses most
temporal questions (5.9% at k=10 against 68.8% ev@10) because their answers
are dates the messages rarely contain as written.

Gold evidence that is in no store's candidate list, from
`EMBEDDER=hash python3 research/benchmarks/router_coverage.py` (gold ids pooled
per route; the script was run against the code of each commit):

| route | questions | gold ids | in no list, before | in no list, after |
|---|---:|---:|---:|---:|
| temporal | 404 | 463 | 71 (15.3%) | 71 (15.3%) |
| entity | 149 | 198 | 50 (25.3%) | 50 (25.3%) |
| multi_hop | 58 | 170 | 54 (31.8%) | 54 (31.8%) |
| adversarial | 3 | 6 | 3 | 3 |
| no store (OPEN or none) | 922 | 1,524 | 504 (33.1%) | 2 (0.1%) |
| **all** | **1,536** | **2,361** | **682 (28.9%)** | **180 (7.6%)** |

One of the two ids still missing on the no-store route, `D10:19`, is not a
turn of its conversation. The handoff's 30.7% was measured before merge
61c893e. The fallback costs no measurable time: on conversation 0 (419
messages) a no-store question took 240 ms before and 241 ms after, mean over
82 questions, timed with an uncommitted loop around `Tesseract.retrieve`.

Not changed: `detect_query_type` still sends conversational questions to OPEN,
because a speaker's name is not a session marker. Routes with a store still
cut each store's list: 178 of their 837 gold ids (21.3%) are in no list.

---

## Track C — The December baseline (superseded, leaky)

**The artifact for this track is not published in this repository.**
`maximal.json` (December 2025, GPT-4o lenient judge) used the gold answer as
an acceptance gate and the gold category label for routing. Those numbers are
leaked, so shipping them invites exactly the comparison they cannot support.
The table is kept only so the record of what was superseded is complete; it is
not a baseline anyone should cite or reproduce against. Every result in
`research/results/` comes from the leakage-free harness.

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
| Tesseract evidence recall by dia_id | ev@50 67.4% → 72.4% with the router fallback | hash embedder: structural only, reproducible offline |
| Agentic Web N=100/1K/10K | — | proposed only; no experiment exists |
