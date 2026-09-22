# Mycelic vNext — loss accounting (old vs new)

_The full per-pattern accounting, with the stage definitions, the sketch-failure taxonomy, the witness curve and the rank tables, is `docs/MYCELIC_LOSS_ACCOUNTING.md` (regenerated on the new artifacts). This file puts the old and new funnels side by side for the hierarchy._

Stages (each row is a gold pattern; a stage is passed only if every earlier stage was): extracted → sketch_visible → in_triage → questioned → descent_reached → in_pool → candidate → matched_any → matched_tau → in_register. `loss_stage` is the terminal loss (first failing stage after the last passing one).

## 10,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.897 | 0.897 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| on the triage candidate list | 0.985 | 0.985 | 0.974 | 0.974 | 0.992 | 0.992 |
| inside the question budget | 0.635 | 0.975 | 0.436 | 0.962 | 0.762 | 0.984 |
| descent reached a facet holder | 0.665 | 0.940 | 0.462 | 0.885 | 0.795 | 0.975 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.690 | 0.980 | 0.487 | 0.962 | 0.820 | 0.992 |
| candidate formed on right entity + chain | 0.595 | 0.900 | 0.397 | 0.872 | 0.721 | 0.918 |
| matched primary rule at ANY confidence | 0.540 | 0.890 | 0.346 | 0.872 | 0.664 | 0.902 |
| matched with confidence >= 0.5 | 0.515 | 0.885 | 0.333 | 0.859 | 0.631 | 0.902 |
| survived the register cut (= reported) | 0.375 | 0.625 | 0.205 | 0.372 | 0.484 | 0.787 |
| patterns | 200 | 200 | 78 | 78 | 122 | 122 |

### Where they died

| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0 (0%) | 0 (0%) | 0 | 0 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 3 (2%) | 3 (2%) | 2 | 2 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 58 (29%) | 0 (0%) | 37 | 0 |
| descent reached a facet holder | 0 (0%) | 1 (0%) | 0 | 1 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 17 (8%) | 15 (8%) | 7 | 6 |
| matched primary rule at ANY confidence | 14 (7%) | 3 (2%) | 5 | 1 |
| matched, but confidence < 0.5 | 5 (2%) | 1 (0%) | 1 | 1 |
| cut from the register (matched at >= 0.5, out-ranked) | 28 (14%) | 52 (26%) | 10 | 38 |
| **reported** | 75 (38%) | 125 (62%) | 16 | 29 |

## 50,000

| stage | OLD all | NEW all | OLD rare | NEW rare | OLD common | NEW common |
|---|---:|---:|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 0.960 | 0.960 | 0.893 | 0.893 | 1.000 | 1.000 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| on the triage candidate list | 0.780 | 0.780 | 0.562 | 0.562 | 0.910 | 0.910 |
| inside the question budget | 0.427 | 0.710 | 0.304 | 0.500 | 0.500 | 0.835 |
| descent reached a facet holder | 0.427 | 0.703 | 0.268 | 0.482 | 0.521 | 0.835 |
| >=2 gold links in kernel pool (= evidence coverage) | 0.450 | 0.727 | 0.312 | 0.527 | 0.532 | 0.846 |
| candidate formed on right entity + chain | 0.397 | 0.677 | 0.259 | 0.509 | 0.479 | 0.777 |
| matched primary rule at ANY confidence | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| matched with confidence >= 0.5 | 0.363 | 0.657 | 0.232 | 0.491 | 0.441 | 0.755 |
| survived the register cut (= reported) | 0.363 | 0.587 | 0.232 | 0.339 | 0.441 | 0.734 |
| patterns | 300 | 300 | 112 | 112 | 188 | 188 |

### Where they died

| the pattern died at | OLD | NEW | OLD rare | NEW rare |
|---|---:|---:|---:|---:|
| extracted (>=2 facets, right entity+predicate) | 5 (2%) | 5 (2%) | 5 | 5 |
| visible to sketch triage (>=2 foreign sites, >=2 regions, span>=2) | 60 (20%) | 58 (19%) | 43 | 41 |
| on the triage candidate list | 0 (0%) | 0 (0%) | 0 | 0 |
| inside the question budget | 100 (33%) | 17 (6%) | 29 | 6 |
| descent reached a facet holder | 0 (0%) | 0 (0%) | 0 | 0 |
| >=2 gold links in kernel pool (= evidence coverage) | 0 (0%) | 0 (0%) | 0 | 0 |
| candidate formed on right entity + chain | 15 (5%) | 17 (6%) | 6 | 3 |
| matched primary rule at ANY confidence | 11 (4%) | 6 (2%) | 3 | 2 |
| matched, but confidence < 0.5 | 0 (0%) | 0 (0%) | 0 | 0 |
| cut from the register (matched at >= 0.5, out-ranked) | 0 (0%) | 21 (7%) | 0 | 17 |
| **reported** | 109 (36%) | 176 (59%) | 26 | 38 |

## Reading

The pack's eight loss categories map onto the columns as: never represented in sketches = ¬sketch_visible; filtered from knowledge objects = extracted but ¬in_pool after reach; not selected for questioning = ¬questioned; descent routing miss = ¬descent_reached; local extraction miss = ¬extracted; evidence at kernel but no candidate = in_pool ∧ ¬candidate; candidate rejected by verification = candidate ∧ ¬matched_tau; ranking buried it = matched_tau ∧ ¬in_register.
