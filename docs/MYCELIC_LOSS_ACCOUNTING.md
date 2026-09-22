# Mycelic: where the hidden patterns go
## Loss accounting and gap decomposition

**What this is.** The benchmark report says the hierarchy finds 36% of the
hidden patterns at 50,000 users and the strongest centralised system finds
70%. This document says *where the other patterns went*. Every discoverable
gold pattern is traced through the pipeline and the first stage at which it
is lost is recorded, for the hierarchy (`H_mycelic_full`), its centralised
twin (`B4_central_triage`), the perfect-retrieval oracle (`Y_oracle_retrieval`)
and the strongest centralised baseline (`A2_chunked_ctx`), on the same worlds.
Then three paired diagnostics on identical worlds decompose the gap into the
stages responsible.

Nothing here is a design. It is the measurement the next design is built on.
Every number is computed from `research/mycelic/artifacts/loss_funnel.jsonl`
and `quick_diag*.jsonl` at generation time. 5 seeds at 10,000 users,
3 at 50,000.

## 1. The finding in one paragraph

The handoff pack's leading hypothesis was that the sketch channel is too poor
to seed good hypotheses. It is not: at 10,000 users the sketch triage sees
98.5% of gold anchors. The pattern dies later, at two places. First, the
**question budget**: the calibrated budget asks only 63.5% of gold anchors at
10k and 43% at 50k, and that is the single largest first-loss stage at both
scales (47% and 51% of the gap to A2). Second, the **register**: at 10k, 12%
of all patterns are matched at confidence ≥ 0.5 and then cut, because the
hierarchy rates spurious candidates as highly as genuine ones and the
register holds one entry per entity. A2 loses nothing at the register. Asking
every triage candidate recovers evidence for 98.5% of patterns and the kernel
forms a correct confident candidate for ~80% — more than A2 — and then buries
them. The calibrated small budget was the right choice *given the ranker*; the
ranker is the fault. At 50,000 users the sketch additionally loses 18% of
patterns (mostly rare: a single-witness facet cannot set a site bit under the
support threshold), so scale adds a third, smaller stage.

## 2. Stage-by-stage survival

Stages are ordered by pipeline position. They are not strictly nested — a
pattern can be visible to the sketch without having been extracted — so
`first stage lost` records the first stage in order that failed. The flat
systems have no sketch, triage, question or descent stage.

## 10,000 users (5 seeds)

### Stage survival, all patterns

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.970 <sub>[0.95, 0.99]</sub> | 0.920 <sub>[0.89, 0.96]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| on the triage candidate list | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| inside the question budget | — | — | 0.635 <sub>[0.58, 0.70]</sub> | — |
| descent reached a facet holder | — | — | 0.665 <sub>[0.61, 0.73]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.970 <sub>[0.95, 0.99]</sub> | 0.690 <sub>[0.64, 0.74]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.685 <sub>[0.62, 0.73]</sub> | 0.385 <sub>[0.33, 0.44]</sub> | 0.595 <sub>[0.54, 0.65]</sub> | 0.250 <sub>[0.21, 0.28]</sub> |
| matched primary rule at ANY confidence | 0.685 <sub>[0.62, 0.74]</sub> | 0.370 <sub>[0.32, 0.42]</sub> | 0.540 <sub>[0.45, 0.63]</sub> | 0.240 <sub>[0.19, 0.28]</sub> |
| matched with confidence >= 0.5 | 0.685 <sub>[0.62, 0.74]</sub> | 0.370 <sub>[0.32, 0.42]</sub> | 0.515 <sub>[0.44, 0.61]</sub> | 0.240 <sub>[0.19, 0.28]</sub> |
| survived the register cut (= reported) | 0.685 <sub>[0.62, 0.74]</sub> | 0.370 <sub>[0.32, 0.42]</sub> | 0.375 <sub>[0.32, 0.43]</sub> | 0.240 <sub>[0.19, 0.28]</sub> |

patterns counted: A2_chunked_ctx: 200, B4_central_triage: 200, H_mycelic_full: 200, Y_oracle_retrieval: 200

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.955 <sub>[0.92, 0.99]</sub> | 0.815 <sub>[0.75, 0.88]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| inside the question budget | — | — | 0.446 <sub>[0.36, 0.54]</sub> | — |
| descent reached a facet holder | — | — | 0.463 <sub>[0.41, 0.53]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.955 <sub>[0.92, 0.99]</sub> | 0.500 <sub>[0.42, 0.58]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.653 <sub>[0.55, 0.75]</sub> | 0.258 <sub>[0.19, 0.33]</sub> | 0.416 <sub>[0.30, 0.54]</sub> | 0.143 <sub>[0.10, 0.18]</sub> |
| matched primary rule at ANY confidence | 0.653 <sub>[0.55, 0.75]</sub> | 0.258 <sub>[0.19, 0.33]</sub> | 0.360 <sub>[0.26, 0.46]</sub> | 0.143 <sub>[0.10, 0.18]</sub> |
| matched with confidence >= 0.5 | 0.653 <sub>[0.55, 0.75]</sub> | 0.258 <sub>[0.19, 0.33]</sub> | 0.341 <sub>[0.25, 0.45]</sub> | 0.143 <sub>[0.10, 0.18]</sub> |
| survived the register cut (= reported) | 0.653 <sub>[0.55, 0.75]</sub> | 0.258 <sub>[0.19, 0.33]</sub> | 0.215 <sub>[0.16, 0.27]</sub> | 0.143 <sub>[0.10, 0.18]</sub> |

patterns counted: A2_chunked_ctx: 78, B4_central_triage: 78, H_mycelic_full: 78, Y_oracle_retrieval: 78

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.986 <sub>[0.96, 1.00]</sub> | 0.992 <sub>[0.98, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| inside the question budget | — | — | 0.771 <sub>[0.66, 0.85]</sub> | — |
| descent reached a facet holder | — | — | 0.798 <sub>[0.72, 0.88]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.986 <sub>[0.96, 1.00]</sub> | 0.821 <sub>[0.76, 0.88]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.715 <sub>[0.68, 0.76]</sub> | 0.475 <sub>[0.35, 0.58]</sub> | 0.721 <sub>[0.69, 0.76]</sub> | 0.325 <sub>[0.27, 0.37]</sub> |
| matched primary rule at ANY confidence | 0.716 <sub>[0.67, 0.76]</sub> | 0.450 <sub>[0.34, 0.55]</sub> | 0.660 <sub>[0.56, 0.75]</sub> | 0.307 <sub>[0.25, 0.35]</sub> |
| matched with confidence >= 0.5 | 0.716 <sub>[0.67, 0.76]</sub> | 0.450 <sub>[0.34, 0.55]</sub> | 0.630 <sub>[0.54, 0.72]</sub> | 0.307 <sub>[0.25, 0.35]</sub> |
| survived the register cut (= reported) | 0.716 <sub>[0.67, 0.76]</sub> | 0.450 <sub>[0.34, 0.55]</sub> | 0.484 <sub>[0.43, 0.55]</sub> | 0.307 <sub>[0.25, 0.35]</sub> |

patterns counted: A2_chunked_ctx: 122, B4_central_triage: 122, H_mycelic_full: 122, Y_oracle_retrieval: 122

### Where the hierarchy loses each pattern FIRST

| first stage lost | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 16 (8%) | 15 | 1 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 2 | 1 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 62 (31%) | 34 | 28 |
| descent reached a facet holder | 1 (0%) | 0 | 1 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (8%) | 5 | 12 |
| matched primary rule at ANY confidence | 11 (6%) | 4 | 7 |
| matched with confidence >= 0.5 | 4 (2%) | 0 | 4 |
| survived the register cut (= reported) | 24 (12%) | 8 | 16 |
| **reported (survived every stage)** | 62 (31%) | 10 | 52 |
| total | 200 | 78 | 122 |

### Why the sketch could not see the invisible ones

| why the sketch could not see it | all | rare | common | median witnesses |
|---|---:|---:|---:|---:|
| causal span < 2 across foreign sites | 3 | 2 | 1 | 7 |

### Discovery by number of witnesses (hierarchy)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 12 | 1.00 | 0.75 | 0.25 |
| 5–7 | 52 | 0.96 | 0.46 | 0.21 |
| 8–11 | 16 | 1.00 | 0.44 | 0.12 |
| 12–16 | 13 | 1.00 | 0.62 | 0.15 |
| 17–25 | 65 | 0.98 | 0.78 | 0.45 |
| 26–∞ | 42 | 1.00 | 0.93 | 0.67 |

### Discovery by number of witnesses (A2_chunked_ctx)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 12 | — | 1.00 | 0.67 |
| 5–7 | 52 | — | 1.00 | 0.56 |
| 8–11 | 16 | — | 1.00 | 0.81 |
| 12–16 | 13 | — | 1.00 | 0.77 |
| 17–25 | 65 | — | 1.00 | 0.74 |
| 26–∞ | 42 | — | 1.00 | 0.69 |

### Where the reported patterns sit in the register

| architecture | reported | median rank | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 137 | 197 | 19 | 37 | 0.85 |
| B4_central_triage | 74 | 263 | 9 | 16 | 0.88 |
| H_mycelic_full | 75 | 255 | 8 | 18 | 0.89 |
| Y_oracle_retrieval | 48 | 279 | 3 | 9 | 0.91 |

### Decomposing the gap to the strongest centralised system

Paired on 200 (seed, pattern) pairs at 10,000 users: `A2_chunked_ctx` reports 137 (68.5%), `H_mycelic_full` reports 75 (37.5%). `A2_chunked_ctx` finds 77 that `H_mycelic_full` misses; `H_mycelic_full` finds 15 that `A2_chunked_ctx` misses.

**Of the 77 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 3 | 4% | 3 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 1 | 1% | 1 |
| inside the question budget | 36 | 47% | 22 |
| candidate formed on right entity + chain | 6 | 8% | 2 |
| matched primary rule at ANY confidence | 7 | 9% | 3 |
| matched with confidence >= 0.5 | 3 | 4% | 0 |
| survived the register cut (= reported) | 21 | 27% | 7 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 200 (seed, pattern) pairs at 10,000 users: `B4_central_triage` reports 74 (37.0%), `H_mycelic_full` reports 75 (37.5%). `B4_central_triage` finds 34 that `H_mycelic_full` misses; `H_mycelic_full` finds 35 that `B4_central_triage` misses.

**Of the 34 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 18 | 53% | 7 |
| descent reached a facet holder | 1 | 3% | 0 |
| candidate formed on right entity + chain | 4 | 12% | 1 |
| matched primary rule at ANY confidence | 2 | 6% | 1 |
| matched with confidence >= 0.5 | 2 | 6% | 0 |
| survived the register cut (= reported) | 7 | 21% | 1 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 200 (seed, pattern) pairs at 10,000 users: `Y_oracle_retrieval` reports 48 (24.0%), `H_mycelic_full` reports 75 (37.5%). `Y_oracle_retrieval` finds 16 that `H_mycelic_full` misses; `H_mycelic_full` finds 43 that `Y_oracle_retrieval` misses.

**Of the 16 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 11 | 69% | 6 |
| candidate formed on right entity + chain | 1 | 6% | 0 |
| matched primary rule at ANY confidence | 2 | 12% | 1 |
| matched with confidence >= 0.5 | 1 | 6% | 0 |
| survived the register cut (= reported) | 1 | 6% | 0 |

## 50,000 users (3 seeds)

### Stage survival, all patterns

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.493 <sub>[0.47, 0.51]</sub> | 0.900 <sub>[0.87, 0.94]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.780 <sub>[0.74, 0.80]</sub> | — |
| on the triage candidate list | — | — | 0.780 <sub>[0.74, 0.80]</sub> | — |
| inside the question budget | — | — | 0.427 <sub>[0.36, 0.47]</sub> | — |
| descent reached a facet holder | — | — | 0.427 <sub>[0.35, 0.48]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.493 <sub>[0.47, 0.51]</sub> | 0.450 <sub>[0.38, 0.49]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.727 <sub>[0.70, 0.76]</sub> | 0.380 <sub>[0.34, 0.41]</sub> | 0.397 <sub>[0.34, 0.44]</sub> | 0.280 <sub>[0.16, 0.35]</sub> |
| matched primary rule at ANY confidence | 0.703 <sub>[0.68, 0.74]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.363 <sub>[0.31, 0.39]</sub> | 0.267 <sub>[0.16, 0.34]</sub> |
| matched with confidence >= 0.5 | 0.703 <sub>[0.68, 0.74]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.363 <sub>[0.31, 0.39]</sub> | 0.267 <sub>[0.16, 0.34]</sub> |
| survived the register cut (= reported) | 0.703 <sub>[0.68, 0.74]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.363 <sub>[0.31, 0.39]</sub> | 0.267 <sub>[0.16, 0.34]</sub> |

patterns counted: A2_chunked_ctx: 300, B4_central_triage: 300, H_mycelic_full: 300, Y_oracle_retrieval: 300

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.213 <sub>[0.18, 0.24]</sub> | 0.756 <sub>[0.69, 0.82]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.558 <sub>[0.44, 0.67]</sub> | — |
| on the triage candidate list | — | — | 0.558 <sub>[0.44, 0.67]</sub> | — |
| inside the question budget | — | — | 0.307 <sub>[0.23, 0.36]</sub> | — |
| descent reached a facet holder | — | — | 0.274 <sub>[0.15, 0.36]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.213 <sub>[0.18, 0.24]</sub> | 0.314 <sub>[0.23, 0.36]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.639 <sub>[0.62, 0.68]</sub> | 0.178 <sub>[0.13, 0.25]</sub> | 0.263 <sub>[0.18, 0.32]</sub> | 0.238 <sub>[0.11, 0.32]</sub> |
| matched primary rule at ANY confidence | 0.622 <sub>[0.56, 0.68]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.235 <sub>[0.15, 0.29]</sub> | 0.230 <sub>[0.11, 0.32]</sub> |
| matched with confidence >= 0.5 | 0.622 <sub>[0.56, 0.68]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.235 <sub>[0.15, 0.29]</sub> | 0.230 <sub>[0.11, 0.32]</sub> |
| survived the register cut (= reported) | 0.622 <sub>[0.56, 0.68]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.235 <sub>[0.15, 0.29]</sub> | 0.230 <sub>[0.11, 0.32]</sub> |

patterns counted: A2_chunked_ctx: 112, B4_central_triage: 112, H_mycelic_full: 112, Y_oracle_retrieval: 112

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1.000 <sub>[1.00, 1.00]</sub> | 0.662 <sub>[0.61, 0.72]</sub> | 0.990 <sub>[0.98, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.911 <sub>[0.89, 0.93]</sub> | — |
| on the triage candidate list | — | — | 0.911 <sub>[0.89, 0.93]</sub> | — |
| inside the question budget | — | — | 0.501 <sub>[0.44, 0.55]</sub> | — |
| descent reached a facet holder | — | — | 0.522 <sub>[0.48, 0.56]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.662 <sub>[0.61, 0.72]</sub> | 0.533 <sub>[0.48, 0.58]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.781 <sub>[0.76, 0.79]</sub> | 0.509 <sub>[0.44, 0.59]</sub> | 0.479 <sub>[0.44, 0.51]</sub> | 0.307 <sub>[0.20, 0.36]</sub> |
| matched primary rule at ANY confidence | 0.754 <sub>[0.75, 0.76]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.444 <sub>[0.41, 0.49]</sub> | 0.292 <sub>[0.20, 0.35]</sub> |
| matched with confidence >= 0.5 | 0.754 <sub>[0.75, 0.76]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.444 <sub>[0.41, 0.49]</sub> | 0.292 <sub>[0.20, 0.35]</sub> |
| survived the register cut (= reported) | 0.754 <sub>[0.75, 0.76]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.444 <sub>[0.41, 0.49]</sub> | 0.292 <sub>[0.20, 0.35]</sub> |

patterns counted: A2_chunked_ctx: 188, B4_central_triage: 188, H_mycelic_full: 188, Y_oracle_retrieval: 188

### Where the hierarchy loses each pattern FIRST

| first stage lost | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 30 (10%) | 28 | 2 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 54 (18%) | 39 | 15 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 100 (33%) | 23 | 77 |
| descent reached a facet holder | 1 (0%) | 1 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 13 (4%) | 4 | 9 |
| matched primary rule at ANY confidence | 7 (2%) | 1 | 6 |
| matched with confidence >= 0.5 | 0 (0%) | 0 | 0 |
| survived the register cut (= reported) | 0 (0%) | 0 | 0 |
| **reported (survived every stage)** | 95 (32%) | 16 | 79 |
| total | 300 | 112 | 188 |

### Why the sketch could not see the invisible ones

| why the sketch could not see it | all | rare | common | median witnesses |
|---|---:|---:|---:|---:|
| fewer than 2 foreign sites | 15 | 10 | 5 | 5 |
| foreign sites in fewer than 2 regions | 8 | 7 | 1 | 7 |
| causal span < 2 across foreign sites | 43 | 32 | 11 | 7 |

### Discovery by number of witnesses (hierarchy)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 16 | 0.56 | 0.44 | 0.31 |
| 5–7 | 75 | 0.55 | 0.28 | 0.20 |
| 8–11 | 22 | 0.59 | 0.32 | 0.27 |
| 12–16 | 25 | 0.80 | 0.32 | 0.20 |
| 17–25 | 104 | 0.91 | 0.49 | 0.41 |
| 26–∞ | 58 | 0.97 | 0.71 | 0.60 |

### Discovery by number of witnesses (A2_chunked_ctx)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 16 | — | 1.00 | 0.44 |
| 5–7 | 75 | — | 1.00 | 0.61 |
| 8–11 | 22 | — | 1.00 | 0.77 |
| 12–16 | 25 | — | 1.00 | 0.84 |
| 17–25 | 104 | — | 1.00 | 0.70 |
| 26–∞ | 58 | — | 1.00 | 0.81 |

### Where the reported patterns sit in the register

| architecture | reported | median rank | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 211 | 851 | 17 | 34 | 0.85 |
| B4_central_triage | 97 | 667 | 3 | 9 | 0.84 |
| H_mycelic_full | 109 | 886 | 1 | 5 | 0.88 |
| Y_oracle_retrieval | 80 | 1268 | 1 | 6 | 0.92 |

### Decomposing the gap to the strongest centralised system

Paired on 300 (seed, pattern) pairs at 50,000 users: `A2_chunked_ctx` reports 211 (70.3%), `H_mycelic_full` reports 109 (36.3%). `A2_chunked_ctx` finds 125 that `H_mycelic_full` misses; `H_mycelic_full` finds 23 that `A2_chunked_ctx` misses.

**Of the 125 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 13 | 10% | 11 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 38 | 30% | 26 |
| inside the question budget | 64 | 51% | 13 |
| candidate formed on right entity + chain | 5 | 4% | 1 |
| matched primary rule at ANY confidence | 5 | 4% | 0 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 300 (seed, pattern) pairs at 50,000 users: `B4_central_triage` reports 97 (32.3%), `H_mycelic_full` reports 109 (36.3%). `B4_central_triage` finds 32 that `H_mycelic_full` misses; `H_mycelic_full` finds 44 that `B4_central_triage` misses.

**Of the 32 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1 | 3% | 1 |
| inside the question budget | 27 | 84% | 1 |
| candidate formed on right entity + chain | 2 | 6% | 0 |
| matched primary rule at ANY confidence | 2 | 6% | 0 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 300 (seed, pattern) pairs at 50,000 users: `Y_oracle_retrieval` reports 80 (26.7%), `H_mycelic_full` reports 109 (36.3%). `Y_oracle_retrieval` finds 46 that `H_mycelic_full` misses; `H_mycelic_full` finds 75 that `Y_oracle_retrieval` misses.

**Of the 46 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 3 | 7% | 3 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 15 | 33% | 9 |
| inside the question budget | 22 | 48% | 3 |
| candidate formed on right entity + chain | 3 | 7% | 0 |
| matched primary rule at ANY confidence | 3 | 7% | 0 |


## 3. Paired decomposition on identical worlds

Each row is the calibrated hierarchy with one setting changed, run on the
same (scale, seed) worlds as the base. **An unbounded register is a
benchmark change, not a design**; it appears here only to measure how much
discovery is being lost to ranking rather than to retrieval or judgement.
Arrows mark a 95% bootstrap interval entirely on one side of zero.

### 10,000 users

| variant | found | evidence cov. | rare recall | AP | FDR | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full (calibrated) | 0.375 | 0.690 | 0.215 | 0.0227 | 0.975 | 1.09e+06 | 9.10e+04 |
| ask every triage candidate (question_frac = 1.0) (5 seeds) | 0.300 | 0.985 **↑** | 0.271 | 0.0113 **↓** | 0.980 | 2.14e+06 **↑** | 2.21e+05 **↑** |
| unbounded register (diagnostic only) (5 seeds) | 0.540 **↑** | 0.690 | 0.341 **↑** | 0.0261 **↑** | 0.985 **↑** | 1.16e+06 **↑** | 9.10e+04 |
| both (5 seeds) | 0.795 **↑** | 0.985 **↑** | 0.697 **↑** | 0.0193 | 0.989 **↑** | 2.36e+06 **↑** | 2.21e+05 **↑** |
| full budget, home-site evidence allowed (5 seeds) | 0.360 | 0.985 **↑** | 0.274 | 0.0209 | 0.976 | 2.76e+06 **↑** | 2.56e+05 **↑** |

### 50,000 users

| variant | found | evidence cov. | rare recall | AP | FDR | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full (calibrated) | 0.363 | 0.450 | 0.235 | 0.0081 | 0.987 | 2.97e+06 | 2.89e+05 |
| ask every triage candidate (question_frac = 1.0) (3 seeds) | 0.407 **↑** | 0.787 **↑** | 0.248 | 0.0085 | 0.986 **↓** | 6.27e+06 **↑** | 6.99e+05 **↑** |
| unbounded register (diagnostic only) (3 seeds) | 0.363 | 0.450 | 0.235 | 0.0081 | 0.987 | 2.98e+06 **↑** | 2.89e+05 |
| both (3 seeds) | 0.643 **↑** | 0.787 **↑** | 0.417 **↑** | 0.0112 **↑** | 0.993 **↑** | 6.83e+06 **↑** | 6.99e+05 **↑** |

**Reading it.** Asking every triage candidate raises evidence coverage from
0.69 to 0.985 at 10k (0.45 to 0.79 at 50k) and *lowers* discovery at 10k:
the recovered evidence produces candidates the register cannot hold. Remove
the register pressure and the same runs report 0.795 at 10k — above A2's
0.685 — and 0.643 at 50k against A2's 0.703, at two-thirds of A2's compute.
Allowing home-site evidence back in does not help, so the flood is not the
foreign-only filter. The residual at 50k is the sketch loss, which no
question budget can recover because the anchor never reaches the triage list.

## 4. Why the hierarchy's ranker is worse than A2's on the same operator

Both systems rank with the same confidence logistic. Its main terms — a
causal span, several regions, links dominated by different branches — are
exactly the properties the triage *pre-selects* anchors for. Among the
candidates the hierarchy's kernel actually has to order, those terms carry
almost no information, because every questioned anchor already has them. A2's
pool holds every anchor, including thousands with no cross-organisational
structure at all, for which the same terms are decisive. The hierarchy's
discrimination problem is a conditioning problem, not an operator problem.

The consequence is testable: features the triage did **not** condition on —
per-link independent support, inter-link lags, conflict volume, dispersion,
whether the candidate came from a question — should separate genuine from
spurious candidates among the triage survivors. Section 6 measures that.

## 5. Two smaller losses, measured

**Extraction (10% at 50k, 24% of rare patterns).** This is stochastic recall,
not a capability ceiling: a fresh edge-tier re-read of the same records
recovers 80–91% of the patterns lost there, a kernel-tier *local* re-read
100%. A user agent asked a targeted question about an entity can re-read its
own notes about it — the entity name is in every record's surface text — so
the loss is recoverable without moving any text.

**Sketch (18% at 50k, 39 of 54 rare).** 43 of the 54 fail on "causal span < 2
across foreign sites": a facet with one witness at a site sets no bit under
`sketch_min_support = 2`. Lowering the threshold lets benign single mentions
set bits and lengthens the triage list, which only pays if the ranker can
absorb the noise — which is the same lever as section 4.

## 6. How separable are genuine candidates, on kernel-side features?

Every candidate the synthesis produced (before the register cut) was dumped
with its confidence-logistic inputs and labelled against gold. One shared
logistic was fitted on the pooled candidates of all four systems on the
calibration seeds (500–502) and evaluated on the evaluation seeds (0–4) by
re-ranking each run's candidates under its own register cap. No feature reads
raw text; the privacy accounting is unchanged.

**Dump `default budget`** — 10,074 training candidates (seeds 500–502), 16,758 evaluation candidates (seeds 0–4), positive rate 2.4%. Pooled AUC gold-vs-spurious: hand-set confidence 0.609, learned 0.768.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 200 | 137 | 0.685 | 0.685 | 0.460 | 0.630 | 0.697 |
| B4_central_triage | 200 | 74 | 0.370 | 0.370 | 0.195 | 0.562 | 0.757 |
| H_mycelic_full | 200 | 108 | 0.375 | 0.450 | 0.325 | 0.692 | 0.802 |
| Y_oracle_retrieval | 200 | 48 | 0.240 | 0.240 | 0.175 | 0.521 | 0.726 |

Largest standardised weights: `dispersion` -1.53, `verified` +1.41, `n_sites` +1.35, `tspan` -0.80, `min_sup` -0.42, `n_indep` -0.38, `log_n_kos` +0.35.

**Dump `_qf1`** — 9,566 training candidates (seeds 500–502), 16,454 evaluation candidates (seeds 0–4), positive rate 1.0%. Pooled AUC gold-vs-spurious: hand-set confidence 0.672, learned 0.829.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 200 | 159 | 0.300 | 0.550 | 0.525 | 0.672 | 0.829 |

Largest standardised weights: `verified` +1.37, `dispersion` -1.28, `n_sites` +0.84, `tspan` -0.78, `n_links` +0.77, `min_lag` -0.50, `log_n_kos` -0.26.


## 7. What this changes about the next architecture

Not "richer sketches". In order of the loss they address:

1. **A calibrated ranker** over kernel-side features, fitted on held-out
   seeds by the same protocol as every other knob, so the full question
   budget can be spent without flooding the register. Targets the register
   loss (12% at 10k; binding at 50k once the budget is raised).
2. **A cheaper descent**, so the full budget is affordable at 50k (it
   currently doubles compute and calls). Targets the question-budget loss.
3. **Targeted local re-extraction** on descent. Targets the extraction loss.
4. **A support-1 sketch bit** for rare facets, *if* (1) absorbs the extra
   triage noise. Targets the sketch loss at scale.

Each is a paired experiment against `H_mycelic_full` on identical worlds,
with the centralised controls re-run at the same time. None moves raw text.

## 8. Reproducing this

```sh
python3 -m research.mycelic.loss_accounting      # 10k x 5, 50k x 3 -> loss_funnel.jsonl
python3 -m research.mycelic.loss_report          # the tables
python3 -m research.mycelic.quick_paired --tag diag_qf1 --cfg '{"question_frac": 1.0}' --seeds 0,1,2,3,4
python3 -m research.mycelic.calibrator dump && python3 -m research.mycelic.calibrator fit
python3 -m research.mycelic.loss_doc             # this document
python3 -m research.mycelic.make_pdf docs/MYCELIC_LOSS_ACCOUNTING.md docs/MYCELIC_LOSS_ACCOUNTING.pdf
```

_Generated 2026-09-22 by `research/mycelic/loss_doc.py` from the
raw artifacts._
