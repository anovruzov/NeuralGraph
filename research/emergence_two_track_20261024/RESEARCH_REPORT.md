# EMERGENCE accuracy: consolidated research report

Date 2026-10-24 · branch `claude/mycelic-gate-0-recovery-tjl82x` · every figure links to its raw output.
Labels: **OFFICIAL** = frozen pack, all checks green. **DIAGNOSTIC** = a guard bypassed, a pack override, or drills
skipped. Development seeds 0–5 only; sealed seeds 94000–94009 untouched. This file is the entry point; the documents
it cites hold the detail.

## 1. Verdict against the goal

| goal item | status |
|---|---|
| ≥ 80% end-to-end accuracy | **Not met.** Official 38.75%; best budget-matched diagnostic 52.9%; best unbounded diagnostic 67.9% (fails the decoy limit). |
| Is 80% reachable? | **Not under the current privacy rule.** The planted-evidence ceiling is 74.6% at 200 users and 68.3% at 10K (§4). With the k-threshold applied after a secure cross-site sum, the ceiling becomes 100% — but a count gate then passes 98.5% of keys with no pattern at 10K. Changing the rule is a founder decision. |
| Bottlenecks identified and eliminated | Identified and measured (§4). Eliminated within the current rules: candidate ranking (+8/240, end to end). Ruled out by measurement: site retrieval, graph edges, multi-hop, routing, recursive acquisition. |
| Reproducible 10K and 50K simulations | **10K:** offline ceilings at 6 seeds; one end-to-end run reached verdicts and was stopped before scoring. **50K:** none. Three walls block a frozen 10K run (§6). |
| Held-out validation | **None.** It needs a frozen candidate that passes drills and suites; the founder gates the sealed seeds. |
| Lineage, privacy, integrity | Preserved: no raw text leaves a site; every conclusion carries record hashes; pins are evidence-logged, not refreshed (§8). |
| Research report | This file. |

## 2. The benchmark

A synthetic enterprise (200 users → 309 people, 7 sites, 12,964 records; 10K → 9,999 people, 15 sites, 329,730
records), with 40 planted cross-site patterns and 50 decoys per seed.

- Sites export weekly counts per (entity, predicate), with counts under k = 3 suppressed.
- HQ detects candidates, asks the top 60, and routes each question to 3 sites.
- Sites judge their own records with a word-matching judge.
- Conclusions are signed onto a NATS JetStream fabric with lineage.

Score = planted patterns found / 240 (40 × 6 seeds). False-discovery checks: decoys accepted (limit 118, the official
count) and keys confirmed with no pattern. Full description: `frontier/DEEP_RESEARCH_PROBLEM.md` §2.

## 3. Accuracy

| configuration | label | questions / seed | found / 240 | rare / 103 | decoys | no-pattern confirms | raw |
|---|---|---|---|---|---|---|---|
| official (Track A) | OFFICIAL | ~57 | **93 (38.75%)** | 5 | 118 | 3 | `raw/official_ce0e357/` |
| v4 config (rule snapshots, near-duplicate roots, N2), shipped order | DIAGNOSTIC | 60 | 119 (49.6%) | 7 | 112 | 13 | `raw/rescue/e2e_b60_seeds0-5.txt` |
| **v4 config, rule-hit-first order** | DIAGNOSTIC, candidate | 60 | **127 (52.9%)** | 7 | 113 | 13 | same |
| v4 config, every candidate asked | DIAGNOSTIC | ~530 | 163 (67.9%) | 27 | 181 ✗ | 96 | `REPORT.md`, `raw/v4_*` |

The rule-hit-first gain: 4 seeds better, 2 tied, none worse (sign test p = 1/16). The replay on real verdicts had
predicted 118 → 128. It was the best of ten orderings on these seeds, so it still needs held-out confirmation.
(`frontier/CANDIDATE_RESCUE.md`)

Separate benchmark, NeuralGraph single-store memory QA: 66.7% (1,027/1,540), hard ceiling 96.9%. That is a recorded
artifact, not re-run here. (`docs/ACCURACY.md`)

## 4. Where accuracy is lost

1. **Funnel (official run):** 93 found, 5 asked but wrong, **142 never asked**. Judging is right on 93 of 98 asked.
   (`EVIDENCE_CEILING.md`, `raw/plateau/gap_decomposition.csv`)
2. **Per-site k-floor.** Planted-evidence ceiling by world size:

   | world | per-site rule | k on cross-site total | non-planted keys passing (total gate) |
   |---|---|---|---|
   | 200 | 0.742 | 1.000 | 43.7% |
   | 2,000 | 0.738 | 1.000 | 96.5% |
   | 10,000 | 0.683 | 1.000 | 98.5% |

   (`tools/plateau/agg_ceiling.py`, `raw/plateau/agg_ceiling_by_world_size.txt`)
3. **Specificity at scale.** Every privacy-safe gate tested at 10K trades recall away: stage order, bounded gaps,
   key bursts, per-site bursts, multi-reporter. (`frontier/EXTERNAL_REPORT_REVIEW.md`)
4. **Ranking under the budget.** The detector pool (84–90 per seed) almost fits the 60-question budget and holds
   151 findable patterns, but the shipped score ranks close to random (118 vs 104.9 ± 4.6).

Ruled out by measurement:

| idea | result | where |
|---|---|---|
| site retrieval, graph edges, multi-hop | 0 of 2,940 judge mentions fall outside the entity match; edges on vs off changes 0 of 539 verdicts | `frontier/NATIVE_GRAPH_PROOF.md` |
| routing | an evidence check predicts verdicts on 98.6% of questions | `PLATEAU_DIAGNOSIS.md` |
| recursive acquisition | no better than its random control (4.3% vs 5.4% precision) | `RECURSIVE_ACQUISITION.md` |
| rule-only candidate rescue | loses patterns at a fixed budget | `frontier/CANDIDATE_RESCUE.md` §1 |

## 5. Latency (end to end, real fabric, process mode)

| run | question → verdict p50 / p95 / p99 | per-site answer p50 / p95 | main pass |
|---|---|---|---|
| 200 users, 60 questions/seed, 6 seeds (360 questions) | 2.15 / 3.23 / 3.53 s | 0.70 / 1.15 s | 139 s mean |
| 200 users, v4 every candidate, seed 0 (539 questions) | 2.11 / 3.50 s | — | 1,219 s |
| 10K users, 60 questions, seed 0 (in-process mode) | — | — | **3,294 s** (largest site graph: 93,909 nodes) |
| site graph edges on vs off (Tesseract ranking) | p50 0.676 vs 0.656 s, p95 1.222 vs 1.220 s | | |

At 10K the main pass is about 24× slower for the same 60 questions. Site ranking over all of a site's nodes is the
scaling cost.

## 6. Scale: what exists and what blocks 10K and 50K

- **Offline at 10K:** ceilings, specificity and every gate experiment, on 6 seeds (`raw/plateau/`).
- **End to end at 10K:** one diagnostic run (seed 0, rule-hit-first) built the world, ingested 329,730 records at 15
  sites, detected 4,202 candidates, survived the site-kill drill, and reached 60 verdicts: 46 supported, 8
  hypothesis, 6 contested. It was stopped on request before scoring.
- **Three walls to a frozen 10K benchmark,** each found by running it:
  1. The frozen benchmark defines worlds only up to 2,000 users.
  2. The 10K world has 599 entities; the frozen pack registers 150.
  3. The product's pack loader caps entity ids at 500.

  The diagnostic bridged them in memory (`tools/ablate.py --extra-size`). A frozen 10K benchmark needs a deliberate
  definition and a loader change.
- **50K:** not attempted. The 10K main pass alone took 55 minutes.

## 7. What was built (code on the local `emergence-rule` series, exported as patches; opt-in, off by default)

| change | effect measured |
|---|---|
| N1 weak corroborator, N2 roots only from k-root sites, near-duplicate roots | part of v4 (`REPORT.md`) |
| `rule_snapshots` | rule-layer keys can be asked; drives the 163 (unbounded) |
| v5 `reask` with per-week detection | offline +8 (ask-once-when-ready, on v4); not run end to end |
| `graph_edges` (site-local TEMPORAL and ENTITY edges, as-of safe) | 0 verdicts change (`frontier/NATIVE_GRAPH_PROOF.md`) |
| `--order rulefirst` (diagnostic wrapper) | +8/240 end to end at the same budget |
| replay instrument (`tools/rescue/`) | predicts end-to-end scores within one pattern per arm |
| world map (`tools/worldmap/`) | renders runs: regions, sites, findings, lineage hashes |

## 8. Integrity

- No pin refreshed to get green. `frontier/PIN_LEDGER.md` records which commit broke which pin. Pin governance
  belongs to `research/report-integrity`.
- Suite at the local head: 8 failures, all pin or contract mismatches listed in the ledger; live tests pass 26/26 with
  the broker.
- Diagnostic runs exit 1 by design (drills skipped).

## 9. Remaining failures

- **Rare patterns:** 7/103 at the 60-question budget; the per-site floor makes most of them invisible.
- **Decoys:** at the limit (113 of 118); any lever that asks more questions breaks it.
- **No held-out run, no frozen candidate, no 50K.**
- **NeuralGraph QA:** the generation contract is unmeasured; a live model is needed.

## 10. Reproduce

```bash
ARMS_SCRATCH=<dir> WT=<emergence tree> TAGP=r1- tools/arms.sh b60_rs_nd_n2 0 1 2 3 4 5
ARMS_SCRATCH=<dir> WT=<emergence tree> TAGP=r1- tools/arms.sh b60_rs_nd_n2_rulefirst 0 1 2 3 4 5
PYTHONPATH=<emergence tree> python3 tools/plateau/agg_ceiling.py 10000 0,1,2,3,4,5
python3 tools/rescue/det_rank.py <pool dir>       # after tools/rescue/dump_pools.py
```

## 11. Decisions that unblock the goal (founder)

1. **Disclosure contract:** whether to evaluate aggregate-then-threshold (secure sum) with a written privacy analysis.
   It is the only measured path past the 68.3% ceiling at 10K. Offline, at 10K, with past totals thresholded as they
   would be released, cross-site totals plus a burst test (z ≥ 3) reach a 0.908 recall ceiling with 43.8% of
   non-planted keys passing, against today's 0.683 with 72.4%. That is better on both axes
   (`raw/plateau/agg_specificity_by_world_size.txt`). The privacy analysis is `frontier/DISCLOSURE_ANALYSIS.md`.
2. **Freeze a candidate** (rule-hit-first, v4 config) and run it with drills and suites. Then authorize the sealed
   seeds.
3. **Define a 10K benchmark** (world table row, pack, loader cap) and allocate compute for 10K and 50K runs.
