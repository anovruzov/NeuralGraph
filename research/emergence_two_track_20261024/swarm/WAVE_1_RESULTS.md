# Wave 1 — ten agents, what came back, and what the control rule says next

Launched 15:44Z; every agent stamped its own START (15:48:27–15:48:29Z) and END. **All ten overlapped from
15:48:29Z until the first END at 15:59:37Z: ω(E) = 10 for eleven minutes**, measured from the stamps by the
arena console (`research/arena`), not asserted. That is the builder layer's §09 proof; it says nothing about
model-call overlap inside an agent, which no stamp bounds.

All ten reported by 16:12Z (H4 last, END 16:12:03Z).
Agent deliverables are copied verbatim under `swarm/wave1/<id>/`.

## 1. Verdicts, in the agents' own words, with my reading

| agent | family | verdict | the number that decides it |
|---|---|---|---|
| H1 | spectral / low-rank | **DEAD ×3** | residual, leverage and rank-1 energy all ≤ 0.54 pooled; rank-1 energy *reversed* (0.328) — at 200 users site = region and the generator gives **decoys** the shared site profile |
| H2 | positive-unlabeled | **DEAD ×3** | the class prior does not calibrate (π̂ 0.29–0.93 vs 0.049) because the known positives are *gate-selected*, not SCAR; best PU ranker 0.643, a plantedness ranker not a decoy discriminator |
| H3 | Bayesian by rule identity | **DEAD ×2**, H3-3 promoted | rule family uninformative across seeds (χ² p = 0.40; LOSO family correlation **negative** on 6/6 folds). Side finding: rule-week count is informative *non-monotonically* (9–15 good, 8 and ≥16 decoy-heavy), band AUC 0.706 |
| H4 | information gain vs expected utility | H4-1 and H4-2 DEAD, **H4-3 PROMOTED** | IG is zero exactly where the yield is (the exported buckets already decide the gate), so it ranks the undecidable first: 0.638. Expected corrected verdicts maximises verdicts at 1.2 decoys per gold — the recorded +3 −14 shape. **Net expected utility** P(G\|b)[P(gold) − ν·P(decoy) − ν·P(other)] over the k-suppressed cell class: **LOSO AUC 0.794, every held-out seed 0.741–0.826**; orders the *already-asked* set at 0.842 vs 0.499 for rule_weeks. Mechanism: only rare gold passes through the thin N1-shaped state (one certain k-site + exactly one weak site) |
| H5 | cross-site timing | H5-1 DEAD, **H5-2 and H5-3 PROMOTED** | co-activation (sites lit per active week inside the longest rule run): **AUC 0.945 gold-vs-decoy on the real Boundary cells, all five decoy classes 0.92–0.99**, permutation null 0.503 ± 0.030. Shape-gated count: LOSO P@20 gold 0.25–0.50 (mean 0.394), decoy 0.00–0.10 |
| H6 | break-even | threshold delivered | **p* ≥ 0.47 at top-10–30 under the displacing budget, ≥ 0.19 under the additive one.** At N = 60 no ranker clears (a), not even a label oracle. Dominant caveat: p* assumes the key is asked at a gate-feasible week |
| H7 | correlated evidence / inhibition | **H7-1 PROMOTED**, H7-2 and H7-3 DEAD | overcount verified and bounded: 26 keys pass TB_v4 only because sites share roots — **all class-4 echo decoys, zero gold, zero ceiling change**. An exported-contract veto (support bucket above roots bucket) takes type-4 passes **57 → 4** at zero gold cost. Contradiction as a negative weight is monotone *worse* (gold's planted contradiction lands inside its window; d5's lands after) |
| H8 | null / adversary | thresholds delivered | beat a hashed-key null at p < 0.05: pooled AUC > 0.549; beat best-of-50: > 0.62. **Free reference line: longest contiguous rule run already gives 0.668**, so a new statistic must clear that. Leak alarm ≥ 0.79. The entity-name prior leaks *through* LOSO (dev and sealed seeds share ~150 names) and is caught only by a seed-swap test |
| D9 | execution path | table D, 17 rows | the 142 originate at `detectors.py:636`: `snapshot = alert_detail.get(key, first_detail.get(key))` is `None` for every rule-only key; the only branch that fills it (639–647) is behind `pack.detectors["rule_snapshots"]`, absent from the shipped pack; `ordered_candidates` then drops `snapshot is None` (`emergence_world.py:339`). The bypass boundary is `site_graph.py:188`. The coordinator is a route-cutter whose synthesis is never read; "evidence truncation" is the L = 50 site read cap plus lossy bucketing, not a list cut |
| D10 | Layer B (MycelicLabs) | audit, 206 lines | no worker abstraction; three tokio tasks, one consumer with in-flight 1, one SQLite writer mutex — **structural maximum of overlapping apply workers today: 1**. Zero NeuralGraph dependency; production retrieval is BM25 only; zero model-inference call sites in the workspace. 0 of 15 W_i fields present as a type; 8 missing outright |

## 2. Survivors against H6 and H8, before any falsifier is spent

This is my integration, applying the two thresholds every hypothesis agreed to clear in advance.

| survivor | vs H8 (beat 0.549 / 0.62 / free line 0.668) | vs H6 (p* 0.47 displace / 0.19 add) | standing |
|---|---|---|---|
| **H3-3** non-monotone rule-week band | 0.706 pooled, min 0.663 — above the nulls, **at** the free line; 8 variants, bucket edges chosen on the pooled table | top-20 precision 25/120 = 0.21: **fails (a)**; clears (b) on gold but admits 20/120 decoys against a bar with zero headroom | likely dead; one cheap pre-registered test owed |
| **H5-3** shape-gated count (co-activation → in-window k-cells → longest run) | 0.945 on the gating statistic; permutation max 0.601, best-of-26 null 0.615 — **clear by 0.33** | P@20 gold 0.394 LOSO. H6's p* = 0.275 / c_g; H5 reports ~75% of its top-20 gold is gate-feasible, so c_g ≈ 0.75 and **p* ≈ 0.37 — cleared, marginally, under the displacing budget**; decoys 0.00–0.10 at top-20, so (b)'s zero-headroom decoy bar is the binding question | **the first candidate for sentence A** |
| **H7-1** echo-decoy veto | not a ranker (all-decoy-vs-gold 0.415); d4-vs-gold 0.998 | zero gold cost, −53 type-4 passes over the pool; cannot alone clear the SPEC decoy bar (d2 + d5 = 119 > 118) | a gate fix, orthogonal to ranking; combines with H5-3 |

Two things H5 and H1 found independently and should be read together: **the generator draws a gold facet's
reporters from one region and a decoy facet's from all of its regions** (`corpus.py pick_users`). At 200 users a
region is one site. H1 saw this as low rank belonging to decoys; H5 turned it into the statistic. It is why
H5-2 works — and why its first falsification test must be at 2,000 users, where a region holds several
sites and the signature should weaken. If it does, the result is a property of this benchmark at this size,
and must be said so.

## 3. The control rule

```
yield(wave 1) = promoted / hypothesis agents spawned = 4 / 8 = 0.5  ≥ 0.25
→ spawn wave 2: one falsifier per survivor (4), cap 24 total, 10 spent → 14
```

**Three agents converged on one structural signature without talking to each other.** H2's oracle direction
(weeks with ≥2 polarity-blind k-sites negative, ≥1 k-site positive), H5-2 (gold lights one site per week,
decoys 2–4 at once) and H4-3 (rare gold alone passes through the thin one-k-site-plus-one-weak state) are the
same fact about this generator read three ways: **gold is thin and staggered, decoys are broad and synchronous.**
That convergence is the finding of wave 1, and its vulnerability is the same for all three — it is a
construction property of the generator at 200 users.

Each falsifier is told exactly what would kill its target, pre-registered here:

| falsifier | target | kills, in order |
|---|---|---|
| F-H5 | H5-3 (and H5-2 as its gate) | (1) **scale**: recompute co-activation at 2,000 users with `ceiling_scale.py`'s world; AUC gold-vs-decoy below 0.70 kills it as a lever and reclassifies it as a 200-user artefact. (2) **one formula**: the lexicographic ranker with θ = 1.25, no re-tuning, scored on all six seeds; every seed's P@20 gold must beat H8's max-of-6 line (0.55 on the gold+decoy subset / 0.25 on the full pool). (3) **seed-swap** per H8 §5 (the entity-name leak passes LOSO). (4) **feasibility at the asked week**: c_g measured, not the 75% H5 estimated. (5) hand the surviving top-20 per seed to the replay for an actual verdict — never claim a recovered pattern from the estimate |
| F-H7 | H7-1 | (1) does the shipped `roots_bucket` track distinct roots under the `nd` near-duplicate rule? Offline gate replay of seed 0 with the veto, then one **DIAGNOSTIC** e2e at seed 0 (`--skip-drills` labelled as such) counting accepted decoys by type against the official 17. (2) gold cost on a world where gold records share roots — construct one in the generator's own terms and report the cost honestly |
| F-H4 | H4-3 (and H4-2's week choice) | (1) one pre-registered procedure (the class definition, ν = (1,1), LOSO fit) re-run once, no variant; (2) seed-swap and H8's leak checklist on the class features — the class is built from k-suppressed cells, which are exported, but the fitted table is indexed by class and must not be indexed by anything finer; (3) **the only budget-neutral use is reordering the asked set**: replay seeds 0–5 with the asked set ordered by S_Net3 using the Gate-0 replay tooling (`tools/rescue/`) and compare to the recorded rule-hit-first **127 vs 119** at 60 questions — if it does not beat 127 with decoys at or below the recorded, it adds nothing to what Gate-0 already confirmed e2e; (4) the thin-state mechanism at 2,000 users; (5) H4-2's label-free week choice (argmax_w P̂(clear\|b_w), 1-in-30 → 1-in-18) as a separate, cheaper claim |
| F-H3 | H3-3 | (1) a single pre-registered formula (band 9–15 → 2, ≥16 → 1, ≤8 → 0), seed-swap, and the free line: it must beat longest-run 0.668 on every seed or it is dead. (2) its top-20 against H6 (a): if below 0.47 with c_g measured, dead regardless of AUC |

Wave 3 (combination against a budget-matched control) is spawned only if F-H5 or F-H4 survives; H7-1 rides
along as the decoy-side term. If both die, the swarm writes `NEGATIVE_RESULT.md` with sentence B and stops.

## 4. Insertion points named by the agents, for the eventual code change

| change | repository | file | function | what crosses the Boundary |
|---|---|---|---|---|
| co-activation as a candidate feature | NeuralGraph (ng-deploy lineage) | `mycelic/collective/detect/detectors.py` | `run_detection` → `_walk`, from cells `_Index.window_cells` already indexes | **nothing new** — the cells are already HQ-side |
| echo-decoy veto | same | `mycelic/collective/pushdown/gate.py` | `evaluate`, the `counted` test: do not count a confirm whose support bucket exceeds its roots bucket | nothing — both buckets are already exported |
| order the asked set by expected utility | same | `research/e2e_ingest/emergence_world.py` | `ordered_candidates`: order by E[ΔU] over the candidate's exported cells instead of (−score, salt), same budget | nothing |
| the rule-only snapshot itself | same | `detectors.py:636–647` | the `rule_snapshots` branch, today behind a pack key the shipped pack does not set | nothing |

## 5. Reported against interest

- H2's **oracle diagnostic** (labels used to fit, explicitly not a hypothesis) reaches AUC 0.83–0.86 gold-vs-decoy
  on exported bucket features, leave-one-seed-out. The exported contracts *do* hold a gold-vs-decoy direction;
  no label-free statistic found so far reaches it except H5-2's. Any later claim of "the contracts cannot see
  it" is false.
- H6: at N = 60 per seed **no ranker clears the displacing budget, including a label oracle** — the pool caps
  top-60 precision at 0.38. Promotion only ever pays at small N.
- H7-3's against-interest hypothesis (the ceiling is optimistic because of overcounting) is **dead on its own
  kill**: B 205/240 and K 199/240 are identical per seed after root de-duplication. The ceiling stands.
- H8: "survives LOSO" is worth p ≈ 0.016 at best on this sample, and nothing if it means five of six. The sample
  resolves ~0.03 AUC pooled, ~0.07 per seed. Every 0.55–0.63 claim in this wave is inside the selection envelope
  unless pre-registered as one formula.
- D9: `R/swarm/MANIFEST.json` recorded ng-recovery at `c41f193`; HEAD was `e6aba1c`. Corrected.
- The `graph_edges` patched tree behind the other session's prove_path second column exists in no repo on this
  machine — only its doc commit. Its counters are unverified here.

## 6. What this wave cost

Ten agents, 2.09M tokens, 712–1,462 s wall each, fully overlapped. No e2e run, no suite, no commit, no push
by any agent; `git status` clean in every tree they touched. One cell reported the 90 s/seed world rebuild
the brief warned of as 0.3–5 s — the 90 s is `ceiling_b.py`'s per-week Python loop, not the generator.
