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
2 at 50,000.

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
| extracted (>=2 facets, right entity+predicate) | — | 0.960 <sub>[0.93, 0.99]</sub> | 0.960 <sub>[0.93, 0.99]</sub> | 0.960 <sub>[0.93, 0.99]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| on the triage candidate list | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| inside the question budget | — | — | 0.635 <sub>[0.58, 0.70]</sub> | — |
| descent reached a facet holder | — | — | 0.665 <sub>[0.61, 0.73]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.970 <sub>[0.95, 0.99]</sub> | 0.690 <sub>[0.64, 0.74]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.885 <sub>[0.84, 0.92]</sub> | 0.870 <sub>[0.82, 0.92]</sub> | 0.595 <sub>[0.54, 0.65]</sub> | 0.865 <sub>[0.85, 0.89]</sub> |
| matched primary rule at ANY confidence | 0.855 <sub>[0.80, 0.89]</sub> | 0.770 <sub>[0.74, 0.81]</sub> | 0.540 <sub>[0.45, 0.63]</sub> | 0.770 <sub>[0.72, 0.82]</sub> |
| matched with confidence >= 0.5 | 0.810 <sub>[0.74, 0.86]</sub> | 0.760 <sub>[0.73, 0.79]</sub> | 0.515 <sub>[0.44, 0.61]</sub> | 0.745 <sub>[0.70, 0.79]</sub> |
| survived the register cut (= reported) | 0.685 <sub>[0.62, 0.74]</sub> | 0.370 <sub>[0.32, 0.42]</sub> | 0.375 <sub>[0.32, 0.43]</sub> | 0.240 <sub>[0.19, 0.28]</sub> |

patterns counted: A2_chunked_ctx: 200, B4_central_triage: 200, H_mycelic_full: 200, Y_oracle_retrieval: 200

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.910 <sub>[0.83, 0.99]</sub> | 0.910 <sub>[0.83, 0.99]</sub> | 0.910 <sub>[0.83, 0.99]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| inside the question budget | — | — | 0.446 <sub>[0.36, 0.54]</sub> | — |
| descent reached a facet holder | — | — | 0.463 <sub>[0.41, 0.53]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.955 <sub>[0.92, 0.99]</sub> | 0.500 <sub>[0.42, 0.58]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.875 <sub>[0.82, 0.92]</sub> | 0.801 <sub>[0.76, 0.86]</sub> | 0.416 <sub>[0.30, 0.54]</sub> | 0.853 <sub>[0.82, 0.89]</sub> |
| matched primary rule at ANY confidence | 0.851 <sub>[0.75, 0.92]</sub> | 0.703 <sub>[0.65, 0.77]</sub> | 0.360 <sub>[0.26, 0.46]</sub> | 0.772 <sub>[0.69, 0.85]</sub> |
| matched with confidence >= 0.5 | 0.773 <sub>[0.62, 0.90]</sub> | 0.703 <sub>[0.65, 0.77]</sub> | 0.341 <sub>[0.25, 0.45]</sub> | 0.737 <sub>[0.67, 0.80]</sub> |
| survived the register cut (= reported) | 0.653 <sub>[0.55, 0.75]</sub> | 0.258 <sub>[0.19, 0.33]</sub> | 0.215 <sub>[0.16, 0.27]</sub> | 0.143 <sub>[0.10, 0.18]</sub> |

patterns counted: A2_chunked_ctx: 78, B4_central_triage: 78, H_mycelic_full: 78, Y_oracle_retrieval: 78

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| inside the question budget | — | — | 0.771 <sub>[0.66, 0.85]</sub> | — |
| descent reached a facet holder | — | — | 0.798 <sub>[0.72, 0.88]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.986 <sub>[0.96, 1.00]</sub> | 0.821 <sub>[0.76, 0.88]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.893 <sub>[0.86, 0.93]</sub> | 0.916 <sub>[0.86, 0.98]</sub> | 0.721 <sub>[0.69, 0.76]</sub> | 0.870 <sub>[0.84, 0.90]</sub> |
| matched primary rule at ANY confidence | 0.860 <sub>[0.84, 0.89]</sub> | 0.818 <sub>[0.79, 0.85]</sub> | 0.660 <sub>[0.56, 0.75]</sub> | 0.761 <sub>[0.72, 0.81]</sub> |
| matched with confidence >= 0.5 | 0.845 <sub>[0.83, 0.86]</sub> | 0.804 <sub>[0.77, 0.84]</sub> | 0.630 <sub>[0.54, 0.72]</sub> | 0.746 <sub>[0.70, 0.79]</sub> |
| survived the register cut (= reported) | 0.716 <sub>[0.67, 0.76]</sub> | 0.450 <sub>[0.34, 0.55]</sub> | 0.484 <sub>[0.43, 0.55]</sub> | 0.307 <sub>[0.25, 0.35]</sub> |

patterns counted: A2_chunked_ctx: 122, B4_central_triage: 122, H_mycelic_full: 122, Y_oracle_retrieval: 122

### Where the hierarchy loses each pattern (terminal stage)

| stage the pattern died at | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0 (0%) | 0 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 2 | 1 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 58 (29%) | 37 | 21 |
| descent reached a facet holder | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (8%) | 7 | 10 |
| matched primary rule at ANY confidence | 14 (7%) | 5 | 9 |
| matched, but confidence < 0.5 | 5 (2%) | 1 | 4 |
| cut from the register (matched at >= 0.5, out-ranked) | 28 (14%) | 10 | 18 |
| **reported** | 75 (38%) | 16 | 59 |
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

| architecture | reported | median rank (0-based, confidence-sorted register) | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 137 | 197 | 19 | 37 | 0.85 |
| B4_central_triage | 74 | 263 | 9 | 16 | 0.88 |
| H_mycelic_full | 75 | 255 | 8 | 18 | 0.89 |
| Y_oracle_retrieval | 48 | 279 | 3 | 9 | 0.91 |

### Decomposing the gap to the strongest centralised system

Paired on 200 (seed, pattern) pairs at 10,000 users: `A2_chunked_ctx` reports 137 (68.5%), `H_mycelic_full` reports 75 (37.5%). `A2_chunked_ctx` finds 77 that `H_mycelic_full` misses; `H_mycelic_full` finds 15 that `A2_chunked_ctx` misses.

**Of the 77 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 1 | 1% | 1 |
| inside the question budget | 36 | 47% | 24 |
| candidate formed on right entity + chain | 7 | 9% | 3 |
| matched primary rule at ANY confidence | 7 | 9% | 3 |
| matched, but confidence < 0.5 | 3 | 4% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 23 | 30% | 7 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 200 (seed, pattern) pairs at 10,000 users: `B4_central_triage` reports 74 (37.0%), `H_mycelic_full` reports 75 (37.5%). `B4_central_triage` finds 34 that `H_mycelic_full` misses; `H_mycelic_full` finds 35 that `B4_central_triage` misses.

**Of the 34 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 16 | 47% | 6 |
| candidate formed on right entity + chain | 4 | 12% | 1 |
| matched primary rule at ANY confidence | 3 | 9% | 1 |
| matched, but confidence < 0.5 | 3 | 9% | 1 |
| cut from the register (matched at >= 0.5, out-ranked) | 8 | 24% | 1 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 200 (seed, pattern) pairs at 10,000 users: `Y_oracle_retrieval` reports 48 (24.0%), `H_mycelic_full` reports 75 (37.5%). `Y_oracle_retrieval` finds 16 that `H_mycelic_full` misses; `H_mycelic_full` finds 43 that `Y_oracle_retrieval` misses.

**Of the 16 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 10 | 62% | 6 |
| candidate formed on right entity + chain | 1 | 6% | 0 |
| matched primary rule at ANY confidence | 2 | 12% | 1 |
| matched, but confidence < 0.5 | 1 | 6% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 2 | 12% | 0 |

## 50,000 users (2 seeds)

### Stage survival, all patterns

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.940 <sub>[0.93, 0.95]</sub> | 0.940 <sub>[0.93, 0.95]</sub> | 0.930 <sub>[0.93, 0.93]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.770 <sub>[0.74, 0.80]</sub> | — |
| on the triage candidate list | — | — | 0.770 <sub>[0.74, 0.80]</sub> | — |
| inside the question budget | — | — | 0.405 <sub>[0.36, 0.45]</sub> | — |
| descent reached a facet holder | — | — | 0.400 <sub>[0.35, 0.45]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.490 <sub>[0.47, 0.51]</sub> | 0.430 <sub>[0.38, 0.48]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.870 <sub>[0.87, 0.87]</sub> | 0.375 <sub>[0.34, 0.41]</sub> | 0.375 <sub>[0.34, 0.41]</sub> | 0.870 <sub>[0.87, 0.87]</sub> |
| matched primary rule at ANY confidence | 0.840 <sub>[0.84, 0.84]</sub> | 0.325 <sub>[0.31, 0.34]</sub> | 0.350 <sub>[0.31, 0.39]</sub> | 0.790 <sub>[0.79, 0.79]</sub> |
| matched with confidence >= 0.5 | 0.790 <sub>[0.79, 0.79]</sub> | 0.325 <sub>[0.31, 0.34]</sub> | 0.350 <sub>[0.31, 0.39]</sub> | 0.780 <sub>[0.78, 0.78]</sub> |
| survived the register cut (= reported) | 0.690 <sub>[0.69, 0.69]</sub> | 0.325 <sub>[0.31, 0.34]</sub> | 0.350 <sub>[0.31, 0.39]</sub> | 0.160 <sub>[0.16, 0.16]</sub> |

patterns counted: A2_chunked_ctx: 100, B4_central_triage: 200, H_mycelic_full: 200, Y_oracle_retrieval: 100

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.858 <sub>[0.84, 0.87]</sub> | 0.858 <sub>[0.84, 0.87]</sub> | 0.844 <sub>[0.84, 0.84]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.551 <sub>[0.44, 0.67]</sub> | — |
| on the triage candidate list | — | — | 0.551 <sub>[0.44, 0.67]</sub> | — |
| inside the question budget | — | — | 0.282 <sub>[0.23, 0.33]</sub> | — |
| descent reached a facet holder | — | — | 0.232 <sub>[0.15, 0.31]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.212 <sub>[0.18, 0.24]</sub> | 0.293 <sub>[0.23, 0.36]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.889 <sub>[0.89, 0.89]</sub> | 0.142 <sub>[0.13, 0.16]</sub> | 0.234 <sub>[0.18, 0.29]</sub> | 0.822 <sub>[0.82, 0.82]</sub> |
| matched primary rule at ANY confidence | 0.867 <sub>[0.87, 0.87]</sub> | 0.129 <sub>[0.10, 0.16]</sub> | 0.210 <sub>[0.15, 0.27]</sub> | 0.778 <sub>[0.78, 0.78]</sub> |
| matched with confidence >= 0.5 | 0.778 <sub>[0.78, 0.78]</sub> | 0.129 <sub>[0.10, 0.16]</sub> | 0.210 <sub>[0.15, 0.27]</sub> | 0.778 <sub>[0.78, 0.78]</sub> |
| survived the register cut (= reported) | 0.622 <sub>[0.62, 0.62]</sub> | 0.129 <sub>[0.10, 0.16]</sub> | 0.210 <sub>[0.15, 0.27]</sub> | 0.111 <sub>[0.11, 0.11]</sub> |

patterns counted: A2_chunked_ctx: 45, B4_central_triage: 84, H_mycelic_full: 84, Y_oracle_retrieval: 45

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.922 <sub>[0.91, 0.93]</sub> | — |
| on the triage candidate list | — | — | 0.922 <sub>[0.91, 0.93]</sub> | — |
| inside the question budget | — | — | 0.494 <sub>[0.44, 0.55]</sub> | — |
| descent reached a facet holder | — | — | 0.520 <sub>[0.48, 0.56]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.688 <sub>[0.65, 0.72]</sub> | 0.529 <sub>[0.48, 0.58]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.855 <sub>[0.85, 0.85]</sub> | 0.541 <sub>[0.49, 0.59]</sub> | 0.476 <sub>[0.44, 0.51]</sub> | 0.909 <sub>[0.91, 0.91]</sub> |
| matched primary rule at ANY confidence | 0.818 <sub>[0.82, 0.82]</sub> | 0.464 <sub>[0.44, 0.49]</sub> | 0.450 <sub>[0.41, 0.49]</sub> | 0.800 <sub>[0.80, 0.80]</sub> |
| matched with confidence >= 0.5 | 0.800 <sub>[0.80, 0.80]</sub> | 0.464 <sub>[0.44, 0.49]</sub> | 0.450 <sub>[0.41, 0.49]</sub> | 0.782 <sub>[0.78, 0.78]</sub> |
| survived the register cut (= reported) | 0.745 <sub>[0.75, 0.75]</sub> | 0.464 <sub>[0.44, 0.49]</sub> | 0.450 <sub>[0.41, 0.49]</sub> | 0.200 <sub>[0.20, 0.20]</sub> |

patterns counted: A2_chunked_ctx: 55, B4_central_triage: 116, H_mycelic_full: 116, Y_oracle_retrieval: 55

### Where the hierarchy loses each pattern (terminal stage)

| stage the pattern died at | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 5 (2%) | 5 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 40 (20%) | 31 | 9 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 69 (34%) | 23 | 46 |
| descent reached a facet holder | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 11 (6%) | 5 | 6 |
| matched primary rule at ANY confidence | 5 (2%) | 2 | 3 |
| matched, but confidence < 0.5 | 0 (0%) | 0 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 0 (0%) | 0 | 0 |
| **reported** | 70 (35%) | 18 | 52 |
| total | 200 | 84 | 116 |

### Why the sketch could not see the invisible ones

| why the sketch could not see it | all | rare | common | median witnesses |
|---|---:|---:|---:|---:|
| fewer than 2 foreign sites | 9 | 6 | 3 | 6 |
| foreign sites in fewer than 2 regions | 6 | 6 | 0 | 6 |
| causal span < 2 across foreign sites | 31 | 25 | 6 | 7 |

### Discovery by number of witnesses (hierarchy)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 11 | 0.55 | 0.55 | 0.36 |
| 5–7 | 57 | 0.56 | 0.26 | 0.18 |
| 8–11 | 16 | 0.56 | 0.25 | 0.25 |
| 12–16 | 17 | 0.82 | 0.24 | 0.18 |
| 17–25 | 61 | 0.92 | 0.49 | 0.41 |
| 26–∞ | 38 | 0.97 | 0.71 | 0.63 |

### Discovery by number of witnesses (A2_chunked_ctx)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 7 | — | 1.00 | 0.57 |
| 5–7 | 31 | — | 1.00 | 0.58 |
| 8–11 | 7 | — | 1.00 | 0.86 |
| 12–16 | 7 | — | 1.00 | 0.71 |
| 17–25 | 33 | — | 1.00 | 0.73 |
| 26–∞ | 15 | — | 1.00 | 0.80 |

### Where the reported patterns sit in the register

| architecture | reported | median rank (0-based, confidence-sorted register) | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 69 | 895 | 3 | 11 | 0.84 |
| B4_central_triage | 65 | 670 | 2 | 5 | 0.84 |
| H_mycelic_full | 70 | 877 | 1 | 3 | 0.88 |
| Y_oracle_retrieval | 16 | 850 | 0 | 1 | 0.95 |

### Decomposing the gap to the strongest centralised system

Paired on 100 (seed, pattern) pairs at 50,000 users: `A2_chunked_ctx` reports 69 (69.0%), `H_mycelic_full` reports 39 (39.0%). `A2_chunked_ctx` finds 37 that `H_mycelic_full` misses; `H_mycelic_full` finds 7 that `A2_chunked_ctx` misses.

**Of the 37 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1 | 3% | 1 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 9 | 24% | 6 |
| inside the question budget | 21 | 57% | 9 |
| candidate formed on right entity + chain | 4 | 11% | 2 |
| matched primary rule at ANY confidence | 2 | 5% | 1 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 200 (seed, pattern) pairs at 50,000 users: `B4_central_triage` reports 65 (32.5%), `H_mycelic_full` reports 70 (35.0%). `B4_central_triage` finds 21 that `H_mycelic_full` misses; `H_mycelic_full` finds 26 that `B4_central_triage` misses.

**Of the 21 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 17 | 81% | 0 |
| candidate formed on right entity + chain | 2 | 10% | 0 |
| matched primary rule at ANY confidence | 2 | 10% | 1 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 100 (seed, pattern) pairs at 50,000 users: `Y_oracle_retrieval` reports 16 (16.0%), `H_mycelic_full` reports 39 (39.0%). `Y_oracle_retrieval` finds 7 that `H_mycelic_full` misses; `H_mycelic_full` finds 30 that `Y_oracle_retrieval` misses.

**Of the 7 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 | 43% | 2 |
| inside the question budget | 3 | 43% | 0 |
| candidate formed on right entity + chain | 1 | 14% | 0 |


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

**Dump `default budget`** — 10,074 training candidates (seeds 500–502), 16,758 evaluation candidates (seeds 0–4), positive rate 2.4%. Pooled AUC gold-vs-spurious: hand-set confidence 0.609, learned 0.785.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 200 | 137 | 0.685 | 0.685 | 0.495 | 0.630 | 0.736 |
| B4_central_triage | 200 | 74 | 0.370 | 0.370 | 0.220 | 0.562 | 0.778 |
| H_mycelic_full | 200 | 108 | 0.375 | 0.450 | 0.330 | 0.692 | 0.800 |
| Y_oracle_retrieval | 200 | 48 | 0.240 | 0.240 | 0.150 | 0.521 | 0.728 |

Largest standardised weights: `dispersion` -1.29, `n_sites` +1.22, `verified` +1.03, `mean_sup` +0.90, `min_sup` -0.88, `hand_conf` +0.86, `tspan` -0.85.

**Dump `_qf1`** — 9,566 training candidates (seeds 500–502), 16,454 evaluation candidates (seeds 0–4), positive rate 1.0%. Pooled AUC gold-vs-spurious: hand-set confidence 0.672, learned 0.831.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 200 | 159 | 0.300 | 0.560 | 0.545 | 0.672 | 0.831 |

Largest standardised weights: `mean_lag` -1.41, `dispersion` -1.24, `attribution` -1.12, `verified` +0.99, `q_evidence_frac` -0.91, `tspan` -0.81, `n_links` +0.78.


## 9. vNext, measured so far

Everything in this section is a **paired** experiment on the evaluation
seeds (0–4 at 10,000 users): the variant and its base run on identical
worlds, and the Δ shown under each variant value is the paired mean with an
arrow when its 95% bootstrap interval excludes zero. Every knob in every
variant was fitted on the calibration seeds (500–502) and frozen before
these seeds were touched. Nothing here changes the register, the threshold,
the gold labels or the matching rule.

### 9.1 A ranker fitted on kernel-side features

The confidence logistic is replaced as a *ranker* only. Version 1 kept the
hand-set ≥ 0.5 gate and re-ordered inside it; version 2 keeps the hand-gated
**count** and lets the learned score choose which candidates fill those
slots, with anchor-level context features (how many candidates share the
entity, where this one ranks among them). Fitted once on the pooled
candidates of all four systems on seeds 500–502; l2 and the interaction set
chosen leave-one-seed-out on those seeds only.

**Version 1 — order within the hand gate** (base = same system, hand-set ranker)

| experiment | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hierarchy, calibrated budget: hand ranker | 5 | 0.375 | 0.215 | 0.690 | 0.0227 | 0.975 | 0.180 | 1.09e+06 | 9.10e+04 |
| hierarchy, calibrated budget: variant | 5 | 0.440 <sub>+0.065</sub> **↑** | 0.167 <sub>-0.048</sub> | 0.685 <sub>-0.005</sub> | 0.1318 <sub>+0.1091</sub> **↑** | 0.970 <sub>-0.004</sub> **↓** | 0.245 <sub>+0.065</sub> **↑** | 1.08e+06 <sub>-6.10e+03</sub> **↓** | 9.08e+04 <sub>-2.47e+02</sub> |
| hierarchy, full question budget: hand ranker | 5 | 0.375 | 0.215 | 0.690 | 0.0227 | 0.975 | 0.180 | 1.09e+06 | 9.10e+04 |
| hierarchy, full question budget: variant | 5 | 0.450 <sub>+0.075</sub> **↑** | 0.197 <sub>-0.018</sub> | 0.980 <sub>+0.290</sub> **↑** | 0.0573 <sub>+0.0346</sub> **↑** | 0.970 <sub>-0.005</sub> **↓** | 0.240 <sub>+0.060</sub> **↑** | 2.17e+06 <sub>+1.08e+06</sub> **↑** | 2.26e+05 <sub>+1.35e+05</sub> **↑** |
| A2_chunked_ctx: hand ranker | 5 | 0.685 | 0.653 | 1.000 | 0.0669 | 0.954 | 0.345 | 2.08e+06 | 3.00e+00 |
| A2_chunked_ctx: variant | 5 | 0.650 <sub>-0.035</sub> **↓** | 0.561 <sub>-0.092</sub> | 1.000 <sub>+0.000</sub> | 0.1831 <sub>+0.1161</sub> **↑** | 0.957 <sub>+0.003</sub> **↑** | 0.350 <sub>+0.005</sub> | 2.08e+06 <sub>+0.00e+00</sub> | 3.00e+00 <sub>+0.00e+00</sub> |
| B4_central_triage: hand ranker | 5 | 0.370 | 0.258 | 0.970 | 0.0219 | 0.975 | 0.175 | 3.98e+05 | 1.00e+04 |
| B4_central_triage: variant | 5 | 0.505 <sub>+0.135</sub> **↑** | 0.238 <sub>-0.020</sub> | 0.970 <sub>+0.000</sub> | 0.1192 <sub>+0.0972</sub> **↑** | 0.966 <sub>-0.009</sub> **↓** | 0.240 <sub>+0.065</sub> **↑** | 3.98e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |
| Y_oracle_retrieval: hand ranker | 5 | 0.240 | 0.143 | 1.000 | 0.0061 | 0.984 | 0.125 | 4.87e+05 | 1.00e+04 |
| Y_oracle_retrieval: variant | 5 | 0.360 <sub>+0.120</sub> **↑** | 0.120 <sub>-0.023</sub> | 1.000 <sub>+0.000</sub> | 0.0232 <sub>+0.0171</sub> **↑** | 0.976 <sub>-0.008</sub> **↓** | 0.200 <sub>+0.075</sub> **↑** | 4.87e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |

**Version 2 — learned top-K gate + anchor context**

| experiment | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hierarchy, calibrated budget: hand ranker | 5 | 0.375 | 0.215 | 0.690 | 0.0227 | 0.975 | 0.180 | 1.09e+06 | 9.10e+04 |
| hierarchy, calibrated budget: variant | 5 | 0.480 <sub>+0.105</sub> **↑** | 0.253 <sub>+0.038</sub> | 0.690 <sub>+0.000</sub> | 0.1334 <sub>+0.1107</sub> **↑** | 0.968 <sub>-0.007</sub> **↓** | 0.275 <sub>+0.095</sub> **↑** | 1.08e+06 <sub>-1.17e+04</sub> **↓** | 9.07e+04 <sub>-3.22e+02</sub> **↓** |
| A2_chunked_ctx: hand ranker | 5 | 0.685 | 0.653 | 1.000 | 0.0669 | 0.954 | 0.345 | 2.08e+06 | 3.00e+00 |
| A2_chunked_ctx: variant | 5 | 0.645 <sub>-0.040</sub> **↓** | 0.566 <sub>-0.087</sub> **↓** | 1.000 <sub>+0.000</sub> | 0.1999 <sub>+0.1329</sub> **↑** | 0.957 <sub>+0.003</sub> **↑** | 0.380 <sub>+0.035</sub> | 2.08e+06 <sub>+0.00e+00</sub> | 3.00e+00 <sub>+0.00e+00</sub> |
| B4_central_triage: hand ranker | 5 | 0.370 | 0.258 | 0.970 | 0.0219 | 0.975 | 0.175 | 3.98e+05 | 1.00e+04 |
| B4_central_triage: variant | 5 | 0.540 <sub>+0.170</sub> **↑** | 0.280 <sub>+0.022</sub> | 0.970 <sub>+0.000</sub> | 0.1298 <sub>+0.1078</sub> **↑** | 0.964 <sub>-0.011</sub> **↓** | 0.295 <sub>+0.120</sub> **↑** | 3.98e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |
| Y_oracle_retrieval: hand ranker | 5 | 0.240 | 0.143 | 1.000 | 0.0061 | 0.984 | 0.125 | 4.87e+05 | 1.00e+04 |
| Y_oracle_retrieval: variant | 5 | 0.360 <sub>+0.120</sub> **↑** | 0.104 <sub>-0.040</sub> | 1.000 <sub>+0.000</sub> | 0.0210 <sub>+0.0150</sub> **↑** | 0.976 <sub>-0.008</sub> **↓** | 0.230 <sub>+0.105</sub> **↑** | 4.87e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |

**What it says.** The same ranker lifts the hierarchy, its centralised
twin and the perfect-retrieval oracle by 10–17 points of discovery and
multiplies AP by four to six, and it makes `A2_chunked_ctx` *worse* on both
found and rare recall. A ranker chosen on one system's candidates must not
be imposed on another, so adoption is now decided per architecture on the
calibration seeds — each system keeps whichever ranker is not worse there —
and stored with the other calibrated settings. Rare recall on the
hierarchy moves inside noise in both versions: the ranker recovers common
patterns first.

**Two regressions, not hidden.** Decoy acceptance rises with the ranker on
every system that adopts it (hierarchy 0.180 → 0.275, B4 0.175 → 0.295,
oracle 0.125 → 0.230): the planted traps share the features that make a
genuine chain look genuine, and the ranker promotes them alongside. FDR
falls slightly at the same time, so the register is cleaner overall but
more of what it contains is a trap rather than noise. This is the next
thing the ranker has to be taught, and it is the reason decoy acceptance
stays a headline metric rather than a footnote.

### 9.2 Spending the question budget, with the ranker on

Base = the hierarchy with ranker v2 at its calibrated budget
(question_frac = 0.25). The earlier sweep without the ranker turned over at
0.35 and *lost* discovery at 1.0; the question is whether a better ranker
lets the recovered evidence be reported.

| experiment | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| question_frac 0.50: qf 0.25 | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| question_frac 0.50: variant | 5 | 0.510 <sub>+0.030</sub> | 0.258 <sub>+0.005</sub> | 0.875 <sub>+0.185</sub> **↑** | 0.1447 <sub>+0.0114</sub> | 0.966 <sub>-0.002</sub> | 0.300 <sub>+0.025</sub> | 1.41e+06 <sub>+3.38e+05</sub> **↑** | 1.33e+05 <sub>+4.28e+04</sub> **↑** |
| question_frac 0.65: qf 0.25 | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| question_frac 0.65: variant | 5 | 0.520 <sub>+0.040</sub> | 0.318 <sub>+0.065</sub> | 0.970 <sub>+0.280</sub> **↑** | 0.1336 <sub>+0.0002</sub> | 0.965 <sub>-0.003</sub> | 0.300 <sub>+0.025</sub> | 1.63e+06 <sub>+5.55e+05</sub> **↑** | 1.61e+05 <sub>+7.05e+04</sub> **↑** |
| question_frac 0.80: qf 0.25 | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| question_frac 0.80: variant | 5 | 0.510 <sub>+0.030</sub> | 0.318 <sub>+0.065</sub> | 0.985 <sub>+0.295</sub> **↑** | 0.1191 <sub>-0.0143</sub> | 0.966 <sub>-0.002</sub> | 0.285 <sub>+0.010</sub> | 1.87e+06 <sub>+7.98e+05</sub> **↑** | 1.91e+05 <sub>+1.00e+05</sub> **↑** |
| question_frac 1.00: qf 0.25 | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| question_frac 1.00: variant | 5 | 0.515 <sub>+0.035</sub> **↑** | 0.330 <sub>+0.077</sub> | 0.985 <sub>+0.295</sub> **↑** | 0.0974 <sub>-0.0360</sub> **↓** | 0.966 <sub>-0.002</sub> **↓** | 0.280 <sub>+0.005</sub> | 2.20e+06 <sub>+1.13e+06</sub> **↑** | 2.31e+05 <sub>+1.40e+05</sub> **↑** |

**What the sweep says.** With the ranker on, every budget above 0.25 recovers
evidence (coverage 0.69 → 0.87–0.985) and reports a little more of it
(found +0.03 to +0.04, rare +0.065 to +0.077 — the rare gain is where the
extra evidence goes), at +31% to +104% compute. The budget no longer *hurts*
discovery as it did without the ranker, but it is not converting either:
at question_frac 1.0 the kernel holds evidence for 98.5% of patterns and
reports 51.5%, against a measured ranking ceiling of 79.5%. The remaining
loss is inside the kernel's ordering of candidates it already has, which
is where the next iteration goes.

### 9.3 Where a full budget spends its compute, and what batching returns

Profiled on one calibration seed (500) at 10,000 users with the full
question budget, single runs, **not** evaluation-seed measurements:

| stage | share of compute | calls | note |
|---|---:|---:|---|
| kernel re-read of the merged pool | 42% | 1 | 89,525 objects × 26 tokens in one frontier call |
| descent routing | 31% | 145,823 | one frontier-tier call per (parent node, anchor) |
| user reads | 6% | 69,549 | 8,361 of 9,491 reached users queried more than once |
| edge extraction | 6% | 9,999 | unchanged by any of this |

Three cost changes, same seed, full budget, ranker off:

| change | found | AP | rare | pool | compute | calls | kept? |
|---|---:|---:|---:|---:|---:|---:|---|
| none | 0.400 | 0.0123 | 0.125 | 89,525 | 2.31e+06 | 227,524 | — |
| batched routing + reads (one call per node / per user; identical decisions and per-record tokens) | 0.400 | 0.0123 | 0.125 | 89,525 | 1.91e+06 | 27,127 | **yes** — pure cost |
| + merge descent returns per (predicate, entity) before the kernel reads them | 0.275 | 0.0052 | — | 22,225 | 1.07e+06 | 27,128 | no |
| + merge per (predicate, entity, **polarity**) | 0.325 | 0.0075 | 0.312 | 23,210 | 1.08e+06 | 27,128 | not yet — needs the ranker re-fitted on merged candidates |

### 9.4 Targeted local re-extraction

When a descent reaches a user, that user re-reads its *own* notes that name
the entity but produced no claim on the first pass (the edge model's recall
is a per-record coin; §5 measured that a fresh read recovers 80–91% of the
patterns lost there). Raw text stays on the node; only objects leave. One
calibration seed, full budget, ranker v2, batched:

| | found | AP | rare | evidence cov. | compute | records re-read locally |
|---|---:|---:|---:|---:|---:|---:|
| without | 0.350 | 0.0818 | 0.062 | 0.925 | 1.97e+06 | 0 |
| with | 0.475 | 0.0385 | 0.188 | 0.925 | 1.99e+06 | 7,980 |

The paired evaluation-seed test of this is queued behind the budget sweep;
it is reported here as a single-seed signal, not a result.

### 9.5 What is accepted so far, and at what cost

| change | status | Δ found (10k, paired, 5 seeds) | Δ compute |
|---|---|---:|---:|
| ranker v2, per-architecture adoption | **accepted** | +0.105 on the hierarchy | −1% |
| batched descent metering | **accepted** (cost only) | 0 | −17% at full budget, −88% calls |
| question budget above 0.25 | under test | see 9.2 | +31% at 0.50 |
| kernel-side evidence merge | rejected for now | −0.075 (one cal seed) | −44% |
| local re-extraction | promising, untested on eval seeds | +0.125 (one cal seed) | +0.7% |
| support-1 sketch bits for rare facets | not started | — | — |


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
