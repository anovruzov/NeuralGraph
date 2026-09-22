# Mycelic: where the hidden patterns go
## Loss accounting and gap decomposition

**What this is.** The v1 benchmark said the hierarchy found 34% of the hidden patterns at 50,000 users and the strongest centralised system found 69%; after the vNext work (section 9) the rerun gives 57% and 78%. This document says *where the other patterns went*. Every discoverable
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
| extracted (>=2 facets, right entity+predicate) | — | 0.960 <sub>[0.93, 0.99]</sub> | 0.960 <sub>[0.93, 0.99]</sub> | 0.960 <sub>[0.93, 0.99]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| on the triage candidate list | — | — | 0.985 <sub>[0.97, 0.99]</sub> | — |
| inside the question budget | — | — | 0.975 <sub>[0.96, 0.99]</sub> | — |
| descent reached a facet holder | — | — | 0.940 <sub>[0.92, 0.97]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.970 <sub>[0.95, 0.99]</sub> | 0.980 <sub>[0.96, 0.99]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.885 <sub>[0.84, 0.92]</sub> | 0.870 <sub>[0.82, 0.92]</sub> | 0.900 <sub>[0.88, 0.93]</sub> | 0.865 <sub>[0.85, 0.89]</sub> |
| matched primary rule at ANY confidence | 0.855 <sub>[0.80, 0.89]</sub> | 0.770 <sub>[0.74, 0.81]</sub> | 0.890 <sub>[0.86, 0.92]</sub> | 0.770 <sub>[0.72, 0.82]</sub> |
| matched with confidence >= 0.5 | 0.830 <sub>[0.76, 0.87]</sub> | 0.750 <sub>[0.72, 0.78]</sub> | 0.885 <sub>[0.86, 0.91]</sub> | 0.760 <sub>[0.72, 0.80]</sub> |
| survived the register cut (= reported) | 0.740 <sub>[0.65, 0.81]</sub> | 0.540 <sub>[0.49, 0.59]</sub> | 0.625 <sub>[0.55, 0.69]</sub> | 0.415 <sub>[0.37, 0.47]</sub> |

patterns counted: A2_chunked_ctx: 200, B4_central_triage: 200, H_mycelic_full: 200, Y_oracle_retrieval: 200

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.910 <sub>[0.83, 0.99]</sub> | 0.910 <sub>[0.83, 0.99]</sub> | 0.910 <sub>[0.83, 0.99]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.977 <sub>[0.95, 1.00]</sub> | — |
| inside the question budget | — | — | 0.963 <sub>[0.94, 0.99]</sub> | — |
| descent reached a facet holder | — | — | 0.890 <sub>[0.85, 0.93]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.955 <sub>[0.92, 0.99]</sub> | 0.967 <sub>[0.93, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.875 <sub>[0.82, 0.92]</sub> | 0.801 <sub>[0.76, 0.86]</sub> | 0.867 <sub>[0.84, 0.89]</sub> | 0.853 <sub>[0.82, 0.89]</sub> |
| matched primary rule at ANY confidence | 0.851 <sub>[0.75, 0.92]</sub> | 0.703 <sub>[0.65, 0.77]</sub> | 0.873 <sub>[0.85, 0.90]</sub> | 0.772 <sub>[0.69, 0.85]</sub> |
| matched with confidence >= 0.5 | 0.804 <sub>[0.66, 0.91]</sub> | 0.662 <sub>[0.61, 0.71]</sub> | 0.860 <sub>[0.82, 0.90]</sub> | 0.748 <sub>[0.68, 0.80]</sub> |
| survived the register cut (= reported) | 0.669 <sub>[0.51, 0.79]</sub> | 0.283 <sub>[0.22, 0.35]</sub> | 0.375 <sub>[0.27, 0.45]</sub> | 0.120 <sub>[0.05, 0.20]</sub> |

patterns counted: A2_chunked_ctx: 78, B4_central_triage: 78, H_mycelic_full: 78, Y_oracle_retrieval: 78

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| on the triage candidate list | — | — | 0.993 <sub>[0.98, 1.00]</sub> | — |
| inside the question budget | — | — | 0.986 <sub>[0.96, 1.00]</sub> | — |
| descent reached a facet holder | — | — | 0.976 <sub>[0.95, 1.00]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.986 <sub>[0.96, 1.00]</sub> | 0.993 <sub>[0.98, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.893 <sub>[0.86, 0.93]</sub> | 0.916 <sub>[0.86, 0.98]</sub> | 0.919 <sub>[0.88, 0.97]</sub> | 0.870 <sub>[0.84, 0.90]</sub> |
| matched primary rule at ANY confidence | 0.860 <sub>[0.84, 0.89]</sub> | 0.818 <sub>[0.79, 0.85]</sub> | 0.904 <sub>[0.87, 0.94]</sub> | 0.761 <sub>[0.72, 0.81]</sub> |
| matched with confidence >= 0.5 | 0.853 <sub>[0.83, 0.89]</sub> | 0.811 <sub>[0.79, 0.85]</sub> | 0.904 <sub>[0.87, 0.94]</sub> | 0.761 <sub>[0.72, 0.81]</sub> |
| survived the register cut (= reported) | 0.789 <sub>[0.75, 0.83]</sub> | 0.702 <sub>[0.67, 0.72]</sub> | 0.789 <sub>[0.73, 0.85]</sub> | 0.599 <sub>[0.55, 0.65]</sub> |

patterns counted: A2_chunked_ctx: 122, B4_central_triage: 122, H_mycelic_full: 122, Y_oracle_retrieval: 122

### Where the hierarchy loses each pattern (terminal stage)

| stage the pattern died at | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0 (0%) | 0 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 2 | 1 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 0 (0%) | 0 | 0 |
| descent reached a facet holder | 1 (0%) | 1 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 15 (8%) | 6 | 9 |
| matched primary rule at ANY confidence | 3 (2%) | 1 | 2 |
| matched, but confidence < 0.5 | 1 (0%) | 1 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 52 (26%) | 38 | 14 |
| **reported** | 125 (62%) | 29 | 96 |
| total | 200 | 78 | 122 |

### Why the sketch could not see the invisible ones

| why the sketch could not see it | all | rare | common | median witnesses |
|---|---:|---:|---:|---:|
| causal span < 2 across foreign sites | 3 | 2 | 1 | 7 |

### Discovery by number of witnesses (hierarchy)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 12 | 1.00 | 1.00 | 0.50 |
| 5–7 | 52 | 0.96 | 0.94 | 0.35 |
| 8–11 | 16 | 1.00 | 1.00 | 0.38 |
| 12–16 | 13 | 1.00 | 1.00 | 0.54 |
| 17–25 | 65 | 0.98 | 0.98 | 0.77 |
| 26–∞ | 42 | 1.00 | 1.00 | 0.90 |

### Discovery by number of witnesses (A2_chunked_ctx)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 12 | — | 1.00 | 0.58 |
| 5–7 | 52 | — | 1.00 | 0.65 |
| 8–11 | 16 | — | 1.00 | 0.69 |
| 12–16 | 13 | — | 1.00 | 0.85 |
| 17–25 | 65 | — | 1.00 | 0.77 |
| 26–∞ | 42 | — | 1.00 | 0.83 |

### Where the reported patterns sit in the register

| architecture | reported | median rank (0-based, confidence-sorted register) | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 148 | 92 | 47 | 77 | 0.87 |
| B4_central_triage | 108 | 107 | 36 | 53 | 0.85 |
| H_mycelic_full | 125 | 86 | 46 | 68 | 0.90 |
| Y_oracle_retrieval | 83 | 230 | 10 | 21 | 0.84 |

### Decomposing the gap to the strongest centralised system

Paired on 200 (seed, pattern) pairs at 10,000 users: `A2_chunked_ctx` reports 148 (74.0%), `H_mycelic_full` reports 125 (62.5%). `A2_chunked_ctx` finds 45 that `H_mycelic_full` misses; `H_mycelic_full` finds 22 that `A2_chunked_ctx` misses.

**Of the 45 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 2 | 4% | 2 |
| descent reached a facet holder | 1 | 2% | 1 |
| candidate formed on right entity + chain | 3 | 7% | 1 |
| matched primary rule at ANY confidence | 2 | 4% | 1 |
| cut from the register (matched at >= 0.5, out-ranked) | 37 | 82% | 26 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 200 (seed, pattern) pairs at 10,000 users: `B4_central_triage` reports 108 (54.0%), `H_mycelic_full` reports 125 (62.5%). `B4_central_triage` finds 22 that `H_mycelic_full` misses; `H_mycelic_full` finds 39 that `B4_central_triage` misses.

**Of the 22 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| candidate formed on right entity + chain | 1 | 5% | 0 |
| matched primary rule at ANY confidence | 1 | 5% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 20 | 91% | 12 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 200 (seed, pattern) pairs at 10,000 users: `Y_oracle_retrieval` reports 83 (41.5%), `H_mycelic_full` reports 125 (62.5%). `Y_oracle_retrieval` finds 17 that `H_mycelic_full` misses; `H_mycelic_full` finds 59 that `Y_oracle_retrieval` misses.

**Of the 17 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| candidate formed on right entity + chain | 2 | 12% | 0 |
| matched primary rule at ANY confidence | 2 | 12% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 13 | 76% | 7 |

## 50,000 users (3 seeds)

### Stage survival, all patterns

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.960 <sub>[0.93, 1.00]</sub> | 0.960 <sub>[0.93, 1.00]</sub> | 0.960 <sub>[0.93, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.780 <sub>[0.74, 0.80]</sub> | — |
| on the triage candidate list | — | — | 0.780 <sub>[0.74, 0.80]</sub> | — |
| inside the question budget | — | — | 0.710 <sub>[0.66, 0.75]</sub> | — |
| descent reached a facet holder | — | — | 0.703 <sub>[0.66, 0.74]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.493 <sub>[0.47, 0.51]</sub> | 0.727 <sub>[0.69, 0.75]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.890 <sub>[0.87, 0.91]</sub> | 0.380 <sub>[0.34, 0.41]</sub> | 0.677 <sub>[0.61, 0.73]</sub> | 0.917 <sub>[0.87, 0.97]</sub> |
| matched primary rule at ANY confidence | 0.853 <sub>[0.84, 0.87]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.657 <sub>[0.59, 0.69]</sub> | 0.797 <sub>[0.74, 0.86]</sub> |
| matched with confidence >= 0.5 | 0.833 <sub>[0.81, 0.87]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.657 <sub>[0.59, 0.69]</sub> | 0.797 <sub>[0.74, 0.86]</sub> |
| survived the register cut (= reported) | 0.797 <sub>[0.77, 0.83]</sub> | 0.323 <sub>[0.31, 0.34]</sub> | 0.587 <sub>[0.52, 0.64]</sub> | 0.447 <sub>[0.39, 0.54]</sub> |

patterns counted: A2_chunked_ctx: 300, B4_central_triage: 300, H_mycelic_full: 300, Y_oracle_retrieval: 300

### Stage survival, rare-signal patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 0.905 <sub>[0.84, 1.00]</sub> | 0.905 <sub>[0.84, 1.00]</sub> | 0.905 <sub>[0.84, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.558 <sub>[0.44, 0.67]</sub> | — |
| on the triage candidate list | — | — | 0.558 <sub>[0.44, 0.67]</sub> | — |
| inside the question budget | — | — | 0.501 <sub>[0.41, 0.56]</sub> | — |
| descent reached a facet holder | — | — | 0.486 <sub>[0.41, 0.54]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.213 <sub>[0.18, 0.24]</sub> | 0.525 <sub>[0.46, 0.58]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.893 <sub>[0.89, 0.90]</sub> | 0.178 <sub>[0.13, 0.25]</sub> | 0.506 <sub>[0.46, 0.56]</sub> | 0.895 <sub>[0.82, 0.96]</sub> |
| matched primary rule at ANY confidence | 0.857 <sub>[0.85, 0.87]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.489 <sub>[0.41, 0.56]</sub> | 0.779 <sub>[0.67, 0.89]</sub> |
| matched with confidence >= 0.5 | 0.808 <sub>[0.74, 0.86]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.489 <sub>[0.41, 0.56]</sub> | 0.779 <sub>[0.67, 0.89]</sub> |
| survived the register cut (= reported) | 0.712 <sub>[0.67, 0.76]</sub> | 0.157 <sub>[0.10, 0.21]</sub> | 0.343 <sub>[0.28, 0.39]</sub> | 0.241 <sub>[0.18, 0.32]</sub> |

patterns counted: A2_chunked_ctx: 112, B4_central_triage: 112, H_mycelic_full: 112, Y_oracle_retrieval: 112

### Stage survival, common patterns only

| stage | A2_chunked_ctx | B4_central_triage | H_mycelic_full | Y_oracle_retrieval |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | — | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | — | — | 0.911 <sub>[0.89, 0.93]</sub> | — |
| on the triage candidate list | — | — | 0.911 <sub>[0.89, 0.93]</sub> | — |
| inside the question budget | — | — | 0.836 <sub>[0.82, 0.85]</sub> | — |
| descent reached a facet holder | — | — | 0.837 <sub>[0.82, 0.87]</sub> | — |
| >=2 gold links in kernel pool (= evidence coverage) | 1.000 <sub>[1.00, 1.00]</sub> | 0.662 <sub>[0.61, 0.72]</sub> | 0.847 <sub>[0.83, 0.87]</sub> | 1.000 <sub>[1.00, 1.00]</sub> |
| candidate formed on right entity + chain | 0.885 <sub>[0.85, 0.92]</sub> | 0.509 <sub>[0.44, 0.59]</sub> | 0.775 <sub>[0.70, 0.82]</sub> | 0.933 <sub>[0.91, 0.97]</sub> |
| matched primary rule at ANY confidence | 0.849 <sub>[0.82, 0.88]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.756 <sub>[0.70, 0.80]</sub> | 0.811 <sub>[0.79, 0.85]</sub> |
| matched with confidence >= 0.5 | 0.849 <sub>[0.82, 0.88]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.756 <sub>[0.70, 0.80]</sub> | 0.811 <sub>[0.79, 0.85]</sub> |
| survived the register cut (= reported) | 0.843 <sub>[0.82, 0.88]</sub> | 0.430 <sub>[0.36, 0.49]</sub> | 0.736 <sub>[0.67, 0.80]</sub> | 0.570 <sub>[0.53, 0.62]</sub> |

patterns counted: A2_chunked_ctx: 188, B4_central_triage: 188, H_mycelic_full: 188, Y_oracle_retrieval: 188

### Where the hierarchy loses each pattern (terminal stage)

| stage the pattern died at | all | rare | common |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 5 (2%) | 5 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 58 (19%) | 41 | 17 |
| on the triage candidate list | 0 (0%) | 0 | 0 |
| inside the question budget | 17 (6%) | 6 | 11 |
| descent reached a facet holder | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (6%) | 3 | 14 |
| matched primary rule at ANY confidence | 6 (2%) | 2 | 4 |
| matched, but confidence < 0.5 | 0 (0%) | 0 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 21 (7%) | 17 | 4 |
| **reported** | 176 (59%) | 38 | 138 |
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
| 0–4 | 16 | 0.56 | 0.56 | 0.38 |
| 5–7 | 75 | 0.55 | 0.49 | 0.28 |
| 8–11 | 22 | 0.59 | 0.59 | 0.50 |
| 12–16 | 25 | 0.80 | 0.68 | 0.48 |
| 17–25 | 104 | 0.91 | 0.83 | 0.73 |
| 26–∞ | 58 | 0.97 | 0.97 | 0.86 |

### Discovery by number of witnesses (A2_chunked_ctx)

| witnesses (records) | n | visible to sketch | in pool | reported |
|---|---:|---:|---:|---:|
| 0–4 | 16 | — | 1.00 | 0.56 |
| 5–7 | 75 | — | 1.00 | 0.73 |
| 8–11 | 22 | — | 1.00 | 0.77 |
| 12–16 | 25 | — | 1.00 | 0.84 |
| 17–25 | 104 | — | 1.00 | 0.85 |
| 26–∞ | 58 | — | 1.00 | 0.84 |

### Where the reported patterns sit in the register

| architecture | reported | median rank (0-based, confidence-sorted register) | within top 40 | within top 100 | median confidence |
|---|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 239 | 396 | 27 | 54 | 0.96 |
| B4_central_triage | 97 | 155 | 13 | 38 | 0.90 |
| H_mycelic_full | 176 | 268 | 37 | 65 | 0.89 |
| Y_oracle_retrieval | 134 | 876 | 3 | 6 | 0.92 |

### Decomposing the gap to the strongest centralised system

Paired on 300 (seed, pattern) pairs at 50,000 users: `A2_chunked_ctx` reports 239 (79.7%), `H_mycelic_full` reports 176 (58.7%). `A2_chunked_ctx` finds 88 that `H_mycelic_full` misses; `H_mycelic_full` finds 25 that `A2_chunked_ctx` misses.

**Of the 88 patterns `A2_chunked_ctx` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 1 | 1% | 1 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 48 | 55% | 32 |
| inside the question budget | 12 | 14% | 5 |
| candidate formed on right entity + chain | 5 | 6% | 0 |
| matched primary rule at ANY confidence | 4 | 5% | 2 |
| cut from the register (matched at >= 0.5, out-ranked) | 18 | 20% | 14 |

### Decomposing the gap to the centralised twin (same algorithm)

Paired on 300 (seed, pattern) pairs at 50,000 users: `B4_central_triage` reports 97 (32.3%), `H_mycelic_full` reports 176 (58.7%). `B4_central_triage` finds 15 that `H_mycelic_full` misses; `H_mycelic_full` finds 94 that `B4_central_triage` misses.

**Of the 15 patterns `B4_central_triage` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| inside the question budget | 4 | 27% | 0 |
| candidate formed on right entity + chain | 4 | 27% | 0 |
| matched primary rule at ANY confidence | 1 | 7% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 6 | 40% | 4 |

### Decomposing the gap to the perfect-retrieval oracle

Paired on 300 (seed, pattern) pairs at 50,000 users: `Y_oracle_retrieval` reports 134 (44.7%), `H_mycelic_full` reports 176 (58.7%). `Y_oracle_retrieval` finds 30 that `H_mycelic_full` misses; `H_mycelic_full` finds 72 that `Y_oracle_retrieval` misses.

**Of the 30 patterns `Y_oracle_retrieval` reports and `H_mycelic_full` does not, the first stage `H_mycelic_full` lost them at:**

| stage it died at | patterns | share of the gap | of which rare |
|---|---:|---:|---:|
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 13 | 43% | 6 |
| inside the question budget | 5 | 17% | 0 |
| candidate formed on right entity + chain | 3 | 10% | 0 |
| matched primary rule at ANY confidence | 3 | 10% | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 6 | 20% | 4 |


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

**Dump `_finalH`** — 9,404 training candidates (seeds 500–502), 15,894 evaluation candidates (seeds 0–4), positive rate 1.1%. Pooled AUC gold-vs-spurious: hand-set confidence 0.690, learned 0.848.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_mycelic_full | 200 | 172 | 0.365 | 0.620 | 0.585 | 0.690 | 0.848 |

Largest standardised weights: `log_n_kos` +3.53, `dispersion*n_sites` -2.07, `n_sites` +2.04, `q_evidence_frac` +1.60, `echo_ratio` -1.39, `attribution` +1.23, `n_origin_users` -1.19.

**Dump `_finalC`** — 5,400 training candidates (seeds 500–502), 9,000 evaluation candidates (seeds 0–4), positive rate 4.2%. Pooled AUC gold-vs-spurious: hand-set confidence 0.714, learned 0.744.

| architecture | gold patterns | with any candidate | found (current ranker) | found (learned rank, same #kept) | found (learned, p≥0.5) | AUC current | AUC learned |
|---|---:|---:|---:|---:|---:|---:|---:|
| A2_chunked_ctx | 200 | 148 | 0.740 | 0.740 | 0.480 | 0.779 | 0.813 |
| B4_central_triage | 200 | 108 | 0.540 | 0.540 | 0.290 | 0.727 | 0.724 |
| Y_oracle_retrieval | 200 | 83 | 0.415 | 0.415 | 0.165 | 0.609 | 0.648 |

Largest standardised weights: `n_sites` +2.82, `n_links` +2.31, `n_links*min_sup` -1.86, `tspan*n_links` -1.67, `dispersion*n_sites` -1.65, `rank_in_anchor` -1.47, `rank_in_anchor*n_same_anchor` +1.07, `min_sup` +0.94.


## 7. What this changed about the next architecture

Not "richer sketches". In order of the loss they addressed, with the
measured outcome (section 9 has every paired table):

1. **A calibrated ranker** over kernel-side features, fitted on the
   calibration seeds by the same protocol as every other knob, so the
   question budget could be spent without flooding the register. Done:
   +0.21 found at 10k, +0.16 at 50k, together with the budget it unlocked.
2. **A cheaper descent**, so the budget is affordable at 50k. Done as
   batched metering (calls −61% to −71% at identical decisions); the
   chain-scoped variant that halves compute is kept as a separate arm
   because it costs evidence coverage.
3. **Link timing in the kernel's temporal check**, which the loss replay
   found contaminated by stale mentions. Done (hybrid): +0.04 at 10k, +0.06
   at 50k, free.
4. **Targeted local re-extraction** on descent. Measured and rejected: no
   gain at 10k, inconclusive at 50k.
5. **A support-1 sketch bit** for rare facets. Measured on one calibration
   seed and rejected as implemented: it floods the triage list.

Each was a paired experiment against `H_mycelic_full` on identical worlds
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

## 9. vNext, measured

Everything in this section is a **paired** experiment: the variant and its
base run on identical worlds, and the Δ shown under each variant value is
the paired mean with an arrow when its 95% bootstrap interval excludes zero.
Evaluation seeds are 0–4 at 10,000 users and 0–2 at 50,000; every knob and
every ranker was fitted on the calibration seeds (500–502) before the
evaluation seeds were read, and the two tables that use calibration seeds
say so. Nothing here changes the register cap, the threshold, the gold
labels, the worlds or the matching rule; the unbounded register appears
only as a diagnostic.

### 9.1 A ranker fitted on kernel-side features

The confidence logistic is replaced as a *ranker* only. Version 1 kept the
hand-set ≥ 0.5 gate and re-ordered inside it; version 2 keeps the hand-gated
**count** and lets the learned score choose which candidates fill those
slots, with anchor-level context features; version 3 adds evidence-shape
features (origin users, single-witness fraction, echo ratio, lag
dispersion), the triage context of the anchor and the kernel's own
attribution verdict — 45 kernel-held quantities, no raw text. l2 and the
a-priori interaction set are chosen leave-one-seed-out on the calibration
seeds; the depth-2 boosted trees lost to the logistic in every selection.
Adoption is decided per architecture on the calibration seeds (each system
keeps whichever ranker is not worse for it), so no control is compared at a
ranker chosen for somebody else.

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

**Version 3 — evidence shape + triage context + attribution** (the hierarchy row also carries the budget and batching changes of 9.2; the controls run at their own settings)

| experiment | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hierarchy: ranker v3 + question_frac 0.65 + batched descent: hand ranker | 5 | 0.375 | 0.215 | 0.690 | 0.0227 | 0.975 | 0.180 | 1.09e+06 | 9.10e+04 |
| hierarchy: ranker v3 + question_frac 0.65 + batched descent: variant | 5 | 0.585 <sub>+0.210</sub> **↑** | 0.390 <sub>+0.175</sub> **↑** | 0.970 <sub>+0.280</sub> **↑** | 0.1570 <sub>+0.1343</sub> **↑** | 0.961 <sub>-0.014</sub> **↓** | 0.330 <sub>+0.150</sub> **↑** | 1.36e+06 <sub>+2.73e+05</sub> **↑** | 2.66e+04 <sub>-6.45e+04</sub> **↓** |
| A2_chunked_ctx: hand ranker | 5 | 0.685 | 0.653 | 1.000 | 0.0669 | 0.954 | 0.345 | 2.08e+06 | 3.00e+00 |
| A2_chunked_ctx: variant | 5 | 0.745 <sub>+0.060</sub> **↑** | 0.649 <sub>-0.003</sub> | 1.000 <sub>+0.000</sub> | 0.2117 <sub>+0.1448</sub> **↑** | 0.950 <sub>-0.004</sub> | 0.445 <sub>+0.100</sub> **↑** | 2.08e+06 <sub>+0.00e+00</sub> | 3.00e+00 <sub>+0.00e+00</sub> |
| B4_central_triage: hand ranker | 5 | 0.370 | 0.258 | 0.970 | 0.0219 | 0.975 | 0.175 | 3.98e+05 | 1.00e+04 |
| B4_central_triage: variant | 5 | 0.560 <sub>+0.190</sub> **↑** | 0.308 <sub>+0.050</sub> | 0.970 <sub>+0.000</sub> | 0.1457 <sub>+0.1238</sub> **↑** | 0.963 <sub>-0.012</sub> **↓** | 0.350 <sub>+0.175</sub> **↑** | 3.98e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |
| Y_oracle_retrieval: hand ranker | 5 | 0.240 | 0.143 | 1.000 | 0.0061 | 0.984 | 0.125 | 4.87e+05 | 1.00e+04 |
| Y_oracle_retrieval: variant | 5 | 0.425 <sub>+0.185</sub> **↑** | 0.134 <sub>-0.010</sub> | 1.000 <sub>+0.000</sub> | 0.0252 <sub>+0.0191</sub> **↑** | 0.972 <sub>-0.012</sub> **↓** | 0.265 <sub>+0.140</sub> **↑** | 4.87e+05 <sub>+0.00e+00</sub> | 1.00e+04 <sub>+0.00e+00</sub> |

**What it says.** Version 3 lifts the hierarchy by 21 points of discovery on
5/5 seeds, rare recall from 0.21 to 0.39 and evidence coverage to 0.97, and
now helps `A2_chunked_ctx` too (0.685 → 0.745), so every control adopts it.
Decoy acceptance rises with the ranker on every system by about +0.15: the
planted traps share the features that make a genuine chain look genuine.
FDR falls slightly at the same time.

**The decoy regression, attacked and not fixed.** Up-weighting decoy rows in
the fit and penalising decoys in the selection objective chose weight 10 on
the calibration seeds; on the evaluation seeds it lost found and rare
recall for a 0.05 reduction in decoy acceptance. Rejected; decoy weighting
stays available as an explicit option.


| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ranker v3 (base) | 5 | 0.585 | 0.390 | 0.970 | 0.1570 | 0.961 | 0.330 | 1.36e+06 | 2.66e+04 |
| decoy-weighted selection (weight 10) | 5 | 0.525 <sub>-0.060</sub> **↓** | 0.272 <sub>-0.118</sub> **↓** | 0.970 <sub>+0.000</sub> | 0.1850 <sub>+0.0279</sub> | 0.965 <sub>+0.004</sub> | 0.280 <sub>-0.050</sub> **↓** | 1.36e+06 <sub>-4.70e+03</sub> | 2.66e+04 <sub>-8.60e+00</sub> |

### 9.2 The question budget, with the ranker fixed

Base = the hierarchy with ranker v3 and batched descent at question_frac
0.50; the v1 calibration had chosen 0.25 because the hand-set confidence
let extra candidates flood the register.


| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| question_frac 0.50 (base) | 5 | 0.555 | 0.356 | 0.875 | 0.1572 | 0.963 | 0.320 | 1.20e+06 | 2.62e+04 |
| question_frac 0.65 | 5 | 0.585 <sub>+0.030</sub> | 0.390 <sub>+0.033</sub> | 0.970 <sub>+0.095</sub> **↑** | 0.1570 <sub>-0.0002</sub> | 0.961 <sub>-0.002</sub> | 0.330 <sub>+0.010</sub> | 1.36e+06 <sub>+1.58e+05</sub> **↑** | 2.66e+04 <sub>+3.73e+02</sub> **↑** |
| question_frac 0.80 | 5 | 0.565 <sub>+0.010</sub> | 0.368 <sub>+0.012</sub> | 0.985 <sub>+0.110</sub> **↑** | 0.0537 <sub>-0.1035</sub> **↓** | 0.962 <sub>-0.001</sub> | 0.345 <sub>+0.025</sub> **↑** | 1.54e+06 <sub>+3.41e+05</sub> **↑** | 2.67e+04 <sub>+5.25e+02</sub> **↑** |
| question_frac 1.00 | 5 | 0.565 <sub>+0.010</sub> | 0.384 <sub>+0.028</sub> | 0.985 <sub>+0.110</sub> **↑** | 0.0464 <sub>-0.1108</sub> **↓** | 0.962 <sub>-0.001</sub> | 0.315 <sub>-0.005</sub> | 1.79e+06 <sub>+5.84e+05</sub> **↑** | 2.68e+04 <sub>+5.59e+02</sub> **↑** |

**What the sweep says.** Coverage saturates at 0.65 (0.97); 0.80 and 1.00 buy
no discovery, cost +13% and +31% compute and lose AP, because the wider
budget floods the 600-entry register with more same-anchor chains than the
ranker was fitted to demote. 0.65 is the budget at both scales (9.6).

### 9.3 Batched metering, and the merge that was rejected

Batching meters one routing call per node and one read per queried user
per round, with identical decisions and identical per-record tokens; it is
why calls fall by about two thirds while compute rises with the budget.
Profiled on one calibration seed (500) at the full budget, single runs,
**not** evaluation-seed measurements:

| stage | share of compute | calls | note |
|---|---:|---:|---|
| kernel re-read of the merged pool | 42% | 1 | 89,525 objects × 26 tokens in one frontier call |
| descent routing | 31% | 145,823 | one frontier-tier call per (parent node, anchor) |
| user reads | 6% | 69,549 | 8,361 of 9,491 reached users queried more than once |
| edge extraction | 6% | 9,999 | unchanged by any of this |

| change (same seed, full budget, ranker off) | found | AP | rare | pool | compute | calls | kept? |
|---|---:|---:|---:|---:|---:|---:|---|
| none | 0.400 | 0.0123 | 0.125 | 89,525 | 2.31e+06 | 227,524 | — |
| batched routing + reads | 0.400 | 0.0123 | 0.125 | 89,525 | 1.91e+06 | 27,127 | **yes** — pure cost |
| + merge descent returns per (predicate, entity) before the kernel reads them | 0.275 | 0.0052 | — | 22,225 | 1.07e+06 | 27,128 | no |
| + merge per (predicate, entity, **polarity**) | 0.325 | 0.0075 | 0.312 | 23,210 | 1.08e+06 | 27,128 | no — needs the ranker re-fitted on merged candidates; queued |

### 9.4 Targeted local re-extraction: rejected

When a descent reaches a user, that user re-reads its *own* notes that name
the entity but produced no claim on the first pass; raw text stays on the
node. One calibration seed promised +0.125. Paired on the evaluation seeds
(base = the v1 hierarchy; compare the two variant rows):


| experiment | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ranker v2, qf 0.65, batched: v1 hierarchy | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| ranker v2, qf 0.65, batched: variant | 5 | 0.520 <sub>+0.040</sub> | 0.318 <sub>+0.065</sub> | 0.970 <sub>+0.280</sub> **↑** | 0.1336 <sub>+0.0002</sub> | 0.965 <sub>-0.003</sub> | 0.300 <sub>+0.025</sub> | 1.35e+06 <sub>+2.75e+05</sub> **↑** | 2.66e+04 <sub>-6.42e+04</sub> **↓** |
| same + local re-extraction: v1 hierarchy | 5 | 0.480 | 0.253 | 0.690 | 0.1334 | 0.968 | 0.275 | 1.08e+06 | 9.07e+04 |
| same + local re-extraction: variant | 5 | 0.515 <sub>+0.035</sub> | 0.277 <sub>+0.023</sub> | 0.970 <sub>+0.280</sub> **↑** | 0.1490 <sub>+0.0156</sub> | 0.966 <sub>-0.002</sub> | 0.280 <sub>+0.005</sub> | 1.37e+06 <sub>+2.93e+05</sub> **↑** | 2.66e+04 <sub>-6.41e+04</sub> **↓** |

No gain at 10k, +0.04/−0.01 on two 50k seeds (9.6), six times the simulator
wall time. Off in the frozen configuration.

### 9.5 Link timing in the temporal check

The temporal DP dated every link at the earliest mention of its (predicate,
entity) pair. A descent that returns every mention of an entity drags that
date to a stale or routine mention, and the chain-order test then fails:
the panel's replay found 20–22 of 25 in-pool gold patterns per 10k seed with
at least one link dated that way. Two repairs, both free (no calls, no new
candidates): *modal* dates a link at its heaviest witness cluster; *hybrid*
does that for links with ≥ 2 agreeing witnesses and lets single-witness
links float across their clusters as separate DP states.

**Screen on the calibration seeds 500–502** (ranker v3 not refitted; these
are NOT evaluation-seed numbers):


| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qf 0.65 + batched, min timing (base) | 3 | 0.542 | 0.187 | 0.925 | 0.2112 | 0.964 | 0.467 | 1.39e+06 | 2.67e+04 |
| modal timing | 3 | 0.583 <sub>+0.042</sub> | 0.232 <sub>+0.045</sub> | 0.917 <sub>-0.008</sub> | 0.2375 <sub>+0.0264</sub> | 0.961 <sub>-0.003</sub> **↓** | 0.375 <sub>-0.092</sub> **↓** | 1.39e+06 <sub>-3.60e+03</sub> **↓** | 2.67e+04 <sub>-6.13e+01</sub> **↓** |
| hybrid timing | 3 | 0.625 <sub>+0.083</sub> | 0.367 <sub>+0.180</sub> | 0.917 <sub>-0.008</sub> | 0.1939 <sub>-0.0173</sub> | 0.958 <sub>-0.006</sub> | 0.367 <sub>-0.100</sub> **↓** | 1.37e+06 <sub>-2.85e+04</sub> **↓** | 2.66e+04 <sub>-1.53e+02</sub> **↓** |
| chain-scoped questions (strict) | 3 | 0.550 <sub>+0.008</sub> | 0.121 <sub>-0.066</sub> | 0.750 <sub>-0.175</sub> **↓** | 0.2177 <sub>+0.0065</sub> | 0.963 <sub>-0.001</sub> | 0.458 <sub>-0.008</sub> | 7.29e+05 <sub>-6.66e+05</sub> **↓** | 2.64e+04 <sub>-2.97e+02</sub> **↓** |
| chain-scoped + modal | 3 | 0.583 <sub>+0.042</sub> | 0.197 <sub>+0.010</sub> | 0.750 <sub>-0.175</sub> **↓** | 0.2337 <sub>+0.0225</sub> **↑** | 0.961 <sub>-0.003</sub> **↓** | 0.367 <sub>-0.100</sub> **↓** | 7.28e+05 <sub>-6.67e+05</sub> **↓** | 2.64e+04 <sub>-3.47e+02</sub> **↓** |
| chain-scoped + hybrid | 3 | 0.600 <sub>+0.058</sub> | 0.285 <sub>+0.098</sub> | 0.758 <sub>-0.167</sub> **↓** | 0.2211 <sub>+0.0099</sub> | 0.960 <sub>-0.004</sub> | 0.425 <sub>-0.042</sub> | 7.31e+05 <sub>-6.64e+05</sub> **↓** | 2.63e+04 <sub>-4.13e+02</sub> **↓** |

**Evaluation seeds 0–4, ranker refitted on the calibration seeds under each pipeline:**


| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| min timing, ranker v3 (base) | 5 | 0.585 | 0.390 | 0.970 | 0.1570 | 0.961 | 0.330 | 1.36e+06 | 2.66e+04 |
| hybrid, ranker v3 (not refitted) | 5 | 0.630 <sub>+0.045</sub> | 0.415 <sub>+0.025</sub> | 0.980 <sub>+0.010</sub> | 0.1734 <sub>+0.0164</sub> | 0.958 <sub>-0.003</sub> | 0.320 <sub>-0.010</sub> | 1.36e+06 <sub>-5.70e+03</sub> | 2.66e+04 <sub>+6.20e+00</sub> |
| hybrid, refitted ranker | 5 | 0.625 <sub>+0.040</sub> | 0.375 <sub>-0.015</sub> | 0.980 <sub>+0.010</sub> | 0.1849 <sub>+0.0279</sub> | 0.958 <sub>-0.003</sub> | 0.360 <sub>+0.030</sub> | 1.37e+06 <sub>+7.28e+03</sub> | 2.67e+04 <sub>+1.34e+02</sub> |
| hybrid + chain-scoped questions (lean) | 5 | 0.610 <sub>+0.025</sub> | 0.318 <sub>-0.071</sub> | 0.800 <sub>-0.170</sub> **↓** | 0.1944 <sub>+0.0373</sub> | 0.959 <sub>-0.002</sub> | 0.375 <sub>+0.045</sub> | 7.26e+05 <sub>-6.35e+05</sub> **↓** | 2.64e+04 <sub>-1.81e+02</sub> |

| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| min timing, ranker v3 (base) | 5 | 0.585 | 0.390 | 0.970 | 0.1570 | 0.961 | 0.330 | 1.36e+06 | 2.66e+04 |
| modal, ranker v3 (not refitted) | 5 | 0.550 <sub>-0.035</sub> | 0.290 <sub>-0.100</sub> | 0.970 <sub>+0.000</sub> | 0.1710 <sub>+0.0140</sub> | 0.963 <sub>+0.002</sub> | 0.340 <sub>+0.010</sub> | 1.36e+06 <sub>-4.13e+02</sub> | 2.67e+04 <sub>+7.38e+01</sub> |
| modal, refitted ranker | 5 | 0.575 <sub>-0.010</sub> | 0.332 <sub>-0.058</sub> | 0.965 <sub>-0.005</sub> | 0.1748 <sub>+0.0178</sub> | 0.962 <sub>+0.001</sub> | 0.340 <sub>+0.010</sub> | 1.36e+06 <sub>+3.48e+03</sub> | 2.66e+04 <sub>-4.00e-01</sub> |

Modal did not replicate (+0.04 on the calibration seeds, −0.01 on the
evaluation seeds); hybrid did (+0.04, no seed worse). The calibration-seed
"decoy resistance" did not replicate either: on the evaluation seeds the
stale-chain family D5 rises under hybrid with the refitted ranker (+0.12
at 10k, +0.13 at 50k, up on 7 of 8 seeds) while D1–D3 are flat, so the
honest statement is "found +0.04/+0.06, D5 +0.12/+0.13". The independent
review traced it to a staleness gate that one routine positive mention
after a retraction defeats, which min timing had been masking by accident;
the per-family numbers are in 9.7 and the gate fix is queued. The refitted ranker
goes forward because that is the pre-registered procedure; the previous
ranker's better rare recall and decoy acceptance under hybrid timing are
reported, not acted on, because acting on them would be selection on the
held-out seeds. The chain-scoped variant halves compute at a cost in
coverage and rare recall and is kept as a separate arm (`H_mycelic_lean`).

### 9.6 50,000 users


| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1 hierarchy (hand ranker, qf 0.25) (base) | 3 | 0.363 | 0.235 | 0.450 | 0.0081 | 0.987 | 0.197 | 2.97e+06 | 2.89e+05 |
| ranker v3, qf 0.65, batched | 3 | 0.527 <sub>+0.163</sub> **↑** | 0.335 <sub>+0.100</sub> **↑** | 0.723 <sub>+0.273</sub> **↑** | 0.0775 <sub>+0.0694</sub> **↑** | 0.982 <sub>-0.005</sub> **↓** | 0.333 <sub>+0.137</sub> **↑** | 3.92e+06 <sub>+9.48e+05</sub> **↑** | 1.14e+05 <sub>-1.75e+05</sub> **↓** |
| ranker v3, qf 0.80, batched | 3 | 0.533 <sub>+0.170</sub> **↑** | 0.303 <sub>+0.067</sub> **↑** | 0.770 <sub>+0.320</sub> **↑** | 0.0745 <sub>+0.0664</sub> **↑** | 0.982 <sub>-0.005</sub> **↓** | 0.367 <sub>+0.170</sub> **↑** | 4.39e+06 <sub>+1.42e+06</sub> **↑** | 1.16e+05 <sub>-1.73e+05</sub> **↓** |
| ranker v3, qf 1.00, batched | 3 | 0.533 <sub>+0.170</sub> **↑** | 0.294 <sub>+0.059</sub> **↑** | 0.787 <sub>+0.337</sub> **↑** | 0.0346 <sub>+0.0265</sub> **↑** | 0.982 <sub>-0.005</sub> **↓** | 0.360 <sub>+0.163</sub> **↑** | 5.15e+06 <sub>+2.18e+06</sub> **↑** | 1.18e+05 <sub>-1.72e+05</sub> **↓** |
| ranker v3, qf 0.65 + local re-extraction | 3 | 0.533 <sub>+0.170</sub> **↑** | 0.310 <sub>+0.075</sub> **↑** | 0.720 <sub>+0.270</sub> **↑** | 0.0829 <sub>+0.0747</sub> **↑** | 0.982 <sub>-0.005</sub> **↓** | 0.373 <sub>+0.177</sub> **↑** | 3.94e+06 <sub>+9.70e+05</sub> **↑** | 1.14e+05 <sub>-1.75e+05</sub> **↓** |
| A2_chunked_ctx, hand ranker | 3 | 0.703 <sub>+0.340</sub> **↑** | 0.622 <sub>+0.386</sub> **↑** | 1.000 <sub>+0.550</sub> **↑** | 0.0594 <sub>+0.0513</sub> **↑** | 0.976 <sub>-0.011</sub> **↓** | 0.373 <sub>+0.177</sub> **↑** | 1.02e+07 <sub>+7.21e+06</sub> **↑** | 1.00e+01 <sub>-2.89e+05</sub> **↓** |
| A2_chunked_ctx, ranker v3 | 3 | 0.780 <sub>+0.417</sub> **↑** | 0.677 <sub>+0.441</sub> **↑** | 1.000 <sub>+0.550</sub> **↑** | 0.1130 <sub>+0.1049</sub> **↑** | 0.974 <sub>-0.013</sub> **↓** | 0.443 <sub>+0.247</sub> **↑** | 1.02e+07 <sub>+7.21e+06</sub> **↑** | 1.00e+01 <sub>-2.89e+05</sub> **↓** |

| arm | seeds | found | rare recall | evidence cov. | AP | FDR | decoy acc. | compute | calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ranker v3, qf 0.65, batched (base) | 3 | 0.527 | 0.335 | 0.723 | 0.0775 | 0.982 | 0.333 | 3.92e+06 | 1.14e+05 |
| hybrid timing, refitted ranker | 3 | 0.587 <sub>+0.060</sub> **↑** | 0.343 <sub>+0.009</sub> | 0.727 <sub>+0.003</sub> | 0.0981 <sub>+0.0206</sub> **↑** | 0.980 <sub>-0.002</sub> **↓** | 0.373 <sub>+0.040</sub> | 3.91e+06 <sub>-2.44e+03</sub> | 1.14e+05 <sub>-4.37e+01</sub> |
| hybrid + chain-scoped questions | 3 | 0.523 <sub>-0.003</sub> | 0.263 <sub>-0.072</sub> **↓** | 0.580 <sub>-0.143</sub> **↓** | 0.1647 <sub>+0.0872</sub> **↑** | 0.982 <sub>-0.000</sub> | 0.340 <sub>+0.007</sub> | 2.71e+06 <sub>-1.20e+06</sub> **↓** | 1.13e+05 <sub>-1.22e+03</sub> **↓** |

At 50k the register (2,999 slots) is not the binding stage; coverage (0.72)
is. The budget saturates at 0.65 here too, re-extraction does not pay, and
hybrid timing adds +0.06 on 3/3 seeds at equal compute. The hierarchy ends
at about 0.59 against A2's 0.77 with the same ranker, at 38% of A2's
compute; that gap is the question budget's reach, not the register.

Two accounting caveats from the independent review apply to every 50k row
above: the "calls" column compares unbatched v1 metering with batched vNext
metering (batching alone is −84% calls at identical decisions; the v1 base
re-metered batched is in 9.7), and the kernel's single read of its pool is
3.2–3.4M tokens at 50k (1.3–1.5M at 10k) against the modelled tier's 1M
context, which the simulator enforces only for the flat controls.

### 9.7 The ledger, the cost of each accepted change, and what is frozen


| change | scale | seeds | Δ found | Δ rare | Δ cov. | Δ decoy all | Δ D1 | Δ D2 | Δ D3 | Δ D5 stale | compute | calls (metering) | status |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ranker v1: learned order inside the hand gate | 10,000 | eval 0-4 | +0.065 (5/5 better) | -0.048 | -0.005 | +0.065 | +0.000 | +0.060 | +0.120 | +0.080 | -1% | -0% | superseded |
| ranker v2: learned top-K, anchor-context features | 10,000 | eval 0-4 | +0.105 (5/5 better) | +0.038 | +0.000 | +0.095 | +0.000 | +0.040 | +0.100 | +0.240 | -1% | -0% | superseded |
| ranker v3 + question_frac 0.65 + batched descent | 10,000 | eval 0-4 | +0.210 (5/5 better) | +0.175 | +0.280 | +0.150 | +0.000 | +0.000 | +0.200 | +0.400 | +25% | -71% | ACCEPTED |
| decoy-weighted ranker selection | 10,000 | eval 0-4 | -0.060 (1/5 better) | -0.118 | +0.000 | -0.050 | +0.020 | -0.040 | +0.020 | -0.200 | -0% | -0% | rejected |
| question_frac 0.80 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/4 better) | -0.022 | +0.015 | +0.015 | +0.000 | +0.020 | +0.000 | +0.040 | +13% | +1% | rejected |
| question_frac 1.00 (vs 0.65) | 10,000 | eval 0-4 | -0.020 (1/3 better) | -0.005 | +0.015 | -0.015 | +0.000 | +0.020 | -0.020 | -0.060 | +31% | +1% | rejected |
| local re-extraction at the user (vs qf 0.65 batched) | 10,000 | eval 0-4 | -0.005 (3/5 better) | -0.042 | +0.000 | -0.020 | +0.020 | +0.040 | -0.020 | -0.120 | +1% | +0% | rejected |
| batched descent metering alone (ranker v2, qf 0.65; same decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -17% | -84% | ACCEPTED |
| modal link timing (+ refitted ranker) | 10,000 | eval 0-4 | -0.010 (2/5 better) | -0.058 | -0.005 | +0.010 | +0.020 | -0.040 | -0.000 | +0.060 | +0% | -0% | rejected |
| hybrid link timing (+ refitted ranker) | 10,000 | eval 0-4 | +0.040 (2/2 better) | -0.015 | +0.010 | +0.030 | -0.020 | +0.020 | +0.000 | +0.120 | +1% | +1% | ACCEPTED |
| hybrid + chain-scoped questions (H_mycelic_lean) | 10,000 | eval 0-4 | +0.025 (3/4 better) | -0.071 | -0.170 | +0.045 | +0.020 | +0.200 | -0.100 | +0.060 | -47% | -1% | Pareto arm |
| ranker v3 + qf 0.65 + batched, 50k | 50,000 | eval 0-2 | +0.163 (3/3 better) | +0.100 | +0.273 | +0.137 | +0.053 | +0.107 | +0.093 | +0.293 | +32% | -61% | ACCEPTED |
| question_frac 0.80 at 50k | 50,000 | eval 0-2 | +0.007 (2/3 better) | -0.032 | +0.047 | +0.033 | +0.013 | +0.093 | +0.027 | -0.000 | +12% | +2% | rejected |
| local re-extraction at 50k | 50,000 | eval 0-1 | +0.007 (1/3 better) | -0.025 | -0.003 | +0.040 | +0.013 | +0.067 | +0.027 | +0.053 | +1% | -0% | rejected |
| hybrid link timing at 50k (+ refitted ranker) | 50,000 | eval 0-2 | +0.060 (3/3 better) | +0.009 | +0.003 | +0.040 | +0.013 | +0.013 | +0.000 | +0.133 | -0% | -0% | validation |
| hybrid + chain-scoped questions at 50k | 50,000 | eval 0-2 | -0.003 (1/3 better) | -0.072 | -0.143 | +0.007 | +0.000 | +0.013 | -0.013 | +0.027 | -31% | -1% | Pareto arm |
| v1 hierarchy re-metered with batched descent (identical decisions) | 10,000 | eval 0-4 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -73% | metering control |
| v1 hierarchy re-metered with batched descent at 50k | 50,000 | eval 0-2 | +0.000 (0/0 better) | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | +0.000 | -12% | -65% | metering control |
| unbounded register (DIAGNOSTIC ONLY, not a fix) | 10,000 | eval 0-4 | +0.165 (5/5 better) | +0.127 | +0.000 | +0.150 | +0.040 | +0.180 | +0.100 | +0.280 | +7% | +0% | diagnostic |
| unbounded register + full question budget (DIAGNOSTIC) | 10,000 | eval 0-4 | +0.420 (5/5 better) | +0.482 | +0.295 | +0.295 | +0.120 | +0.200 | +0.440 | +0.420 | +117% | +143% | diagnostic |
| chain-scoped questions alone (calibration seeds) | 10,000 | cal 500-502 | +0.008 (2/3 better) | -0.066 | -0.175 | -0.008 | +0.067 | +0.033 | -0.100 | -0.033 | -48% | -1% | screen |
| cluster-aware staleness gate (strong positives only), calibration seeds | 10,000 | cal 500-502 | +0.058 (2/2 better) | +0.019 | -0.008 | -0.042 | +0.000 | -0.033 | -0.067 | -0.067 | -0% | +0% | screen |
| strong staleness gate, frozen ranker, FRESH panel | 10,000 | eval 10-14 | -0.035 (0/3 better) | -0.039 | +0.000 | -0.040 | -0.040 | -0.020 | +0.020 | -0.120 | +0% | +0% | fresh-panel read |
| strong staleness gate + refitted ranker, FRESH panel | 10,000 | eval 10-14 | +0.020 (2/4 better) | +0.017 | +0.010 | -0.020 | -0.060 | -0.080 | +0.040 | +0.020 | +0% | +0% | fresh-panel read |

**Compute per accepted change, 10k evaluation seeds:**

| configuration | found | rare recall | compute | calls (metering) | kernel prompt tokens | Δ found per +10% compute (vs previous row) |
|---|---:|---:|---:|---:|---:|---:|
| v1 hierarchy (hand ranker, qf 0.25), metered unbatched as benchmarked | 0.375 | 0.215 | 1.09e+06 | 9.10e+04 | 7.88e+05 | — |
| v1 hierarchy re-metered with batched descent (identical decisions) — the like-for-like base | 0.375 | 0.215 | 9.53e+05 | 2.46e+04 | 7.88e+05 | +0.000 at -12.4% compute |
| + ranker v3, qf 0.65, batched descent | 0.585 | 0.390 | 1.36e+06 | 2.66e+04 | 1.39e+06 | +0.049 |
| + hybrid link timing (refitted ranker) | 0.625 | 0.375 | 1.37e+06 | 2.67e+04 | 1.40e+06 | +0.040 at +0.5% compute |

The first row is the v1 base as it was benchmarked (unbatched metering); the second is the same decisions re-metered with batched descent, which is the base every later row should be read against. Batching is a metering convention: it changes no decision and no per-record token.

**Frozen configuration** (`calibration.json`, with the file that justified each value):

| knob | frozen value | seeds the cited evidence was read on | evidence |
|---|---|---|---|
| `question_frac` | `0.65` | quick_qf_v3.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_qf_v3.jsonl` |
| `batched_descent` | `True` | quick_v3_H.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_v3_H.jsonl` |
| `link_time` | `hybrid` | quick_refit_hyb.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_refit_hyb.jsonl` |
| `local_reextract` | `False` | quick_rx_reextract.jsonl: seeds 0–4 at 10,000 — evaluation (development panel) | `quick_rx_reextract.jsonl` |
| `strict_targeting` | `False` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| `triage_target_chains` | `none` | quick_cal_screen.jsonl: seeds 500–502 at 10,000 — calibration | `quick_cal_screen.jsonl` |
| ranker | logistic, l2 0.3, interactions True, 23614 candidates | seeds [500, 501, 502] | `research/mycelic/artifacts/calibration.hyb.json` |
| ranker adopted by | A2_chunked_ctx, A_flat_rag, B2_map_reduce, B4_central_triage, D_hier_nolineage, E_hier_lineage, F_hier_retrieval, G_hier_questions, H_mycelic_full, H_mycelic_lean, I_mycelic_completion, J_mycelic_verified, Y_oracle_retrieval | calibration seeds | ranker_arch_table |

The full benchmark is rerun on this configuration by `final_rerun.sh`
(every experiment, the controls on identical worlds, the loss accounting at
both scales, the candidate dump with an out-of-sample ranker evaluation),
and the old-vs-new comparison, the architecture, the experiment matrix and
the adversarial review are in `docs/mycelic_vnext/`.

