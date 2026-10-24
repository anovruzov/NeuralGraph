# EVIDENCE_CEILING.md — what this benchmark can be scored at, before any retrieval work

Derived here, in this session, from `raw/plateau/ceiling_b.json` (the frozen generator, dev seeds 0–5,
40 gold-discoverable patterns per seed = 240, of which 103 rare and 137 common). The generator is
`tools/plateau/ceiling_b.py`; re-derive with `python3 tools/plateau/ceiling_b.py 0 1 2 3 4 5`.

Every figure below is an **upper bound by construction**: perfect detection, perfect judging, perfect routing,
and the *best single week* inside the label window. No real system can exceed them, and a real system that
approaches one is not finding more evidence — it is guessing.

## 1. The short version

| | |
|---|---|
| Official six-seed baseline (`ce0e357`, drills on) | **93/240 = 0.3875** |
| Ceiling under the harness's own evidence semantics (F, TB_v4) | **179/240 = 0.7458** |
| Ceiling under the most permissive model we can defend (B, N1_count) | **207/240 = 0.8625** |
| Ceiling under the most permissive model *at the shipped gate* (B, TB_v4) | **205/240 = 0.8542** |
| Patterns no model and no gate can reach at any week | **35, every one of them rare** |

An 85% target sits **above** the honest ceiling (74.6%) and **at** the permissive one (85.4%). It is not an
engineering backlog; it is a disclosure decision about the benchmark's labels.

## 2. The full grid

Evidence models — F: the pattern's own facet records (what the harness oracle counts). K: every record whose
anchor *or* aux is the pattern's anchor and whose predicate maps to its chain (a reviewer's permissive reading;
background records on the key count). B: K counted polarity-blind, which is what the shipped lexical judge
actually does. A trailing **s** is the strict-contradiction variant: a site holding any polarity −1 record is
dropped entirely. B has no strict variant — polarity-blind is the opposite of strict.

Gates — `R0_count`: ≥2 sites at k. `R0_full`: R0 plus roots ≥3, reporters ≥3, freshness. `N1_count`: ≥2 k-sites
**or** one k-site plus one weak site. `TB_v4`: the Track-B v4 gate (N1 weak corroborator + N2 roots only from
k-root sites), full conditions.

| model | gate | feasible | ceiling | rare feasible | rare ceiling | common |
|---|---|---|---|---|---|---|
| F | R0_count | 145/240 | **0.6042** | 8/103 | 0.0777 | 137/137 |
| F | R0_full | 145/240 | **0.6042** | 8/103 | 0.0777 | 137/137 |
| F | N1_count | 179/240 | **0.7458** | 42/103 | 0.4078 | 137/137 |
| F | TB_v4 | 179/240 | **0.7458** | 42/103 | 0.4078 | 137/137 |
| Fs | R0_count | 140/240 | **0.5833** | 8/103 | 0.0777 | 132/137 |
| Fs | R0_full | 139/240 | **0.5792** | 8/103 | 0.0777 | 131/137 |
| Fs | N1_count | 171/240 | **0.7125** | 38/103 | 0.3689 | 133/137 |
| Fs | TB_v4 | 170/240 | **0.7083** | 38/103 | 0.3689 | 132/137 |
| K | R0_count | 151/240 | **0.6292** | 14/103 | 0.1359 | 137/137 |
| K | R0_full | 151/240 | **0.6292** | 14/103 | 0.1359 | 137/137 |
| K | N1_count | 201/240 | **0.8375** | 64/103 | 0.6214 | 137/137 |
| K | TB_v4 | 199/240 | **0.8292** | 62/103 | 0.6019 | 137/137 |
| Ks | R0_count | 143/240 | **0.5958** | 13/103 | 0.1262 | 130/137 |
| Ks | R0_full | 142/240 | **0.5917** | 13/103 | 0.1262 | 129/137 |
| Ks | N1_count | 196/240 | **0.8167** | 60/103 | 0.5825 | 136/137 |
| Ks | TB_v4 | 193/240 | **0.8042** | 58/103 | 0.5631 | 135/137 |
| B | R0_count | 153/240 | **0.6375** | 16/103 | 0.1553 | 137/137 |
| B | R0_full | 153/240 | **0.6375** | 16/103 | 0.1553 | 137/137 |
| B | N1_count | 207/240 | **0.8625** | 70/103 | 0.6796 | 137/137 |
| B | TB_v4 | 205/240 | **0.8542** | 68/103 | 0.6602 | 137/137 |

Per-seed, at the shipped gate: F/TB_v4 **30, 29, 31, 33, 28, 28**; B/TB_v4 **34, 33, 34, 36, 36, 32**
(out of 40 each). The ceiling is stable across seeds; the baseline's 13/12/17/17/19/15 is not close to it.

## 3. The one structural fact the whole table rests on

**Every infeasible pattern is rare. All 137 common patterns are feasible under every model at every gate.**
Nothing in this benchmark is hard because it is subtle; it is hard because the evidence is thin on one axis —
how many records about a pattern land at a *single site*.

Counting under F, the harness's own semantics:

| | rare patterns |
|---|---|
| Never reach **two** sites at k=3 in any week of the window (`R0_count` infeasible) | **95 / 103** |
| Of those, reach exactly one k-site plus a weak site, so N1 rescues them | 34 |
| Reach neither (`N1_count` infeasible) — invisible to the count gate in any form | **61 / 103** |
| Still infeasible at `TB_v4` under the *most permissive* model B | **35 / 103** |

This is the **k-per-site disclosure invariant**: a site discloses a pattern only once it holds k = 3 records
about it, and the gate wants two such sites. For 95 of 103 rare patterns, the world never puts 3 records about
the pattern at two sites inside one 6-week window. No amount of routing, re-ranking, embedding geometry or
hypergraph structure changes a record count.

## 4. Why the ceiling, not the gap, is the finding

Three measurements already close off the architectural reading of the gap:

- **Routing is not the bottleneck.** Asking every site instead of three (M = all vs M = 3, seeds 0–1) scored
  **46/80 both ways — a 0-point gain.**
- **Index geometry is not the bottleneck.** Tesseract's exact-vector counterfactual changed **0 of 165** verdicts
  at seed 0.
- **There is no graph to exploit.** The site graphs hold 12,964 nodes and **0** persisted edges; `save_edge` is
  called only from `research/benchmarks/`.

So the 46-point gap between 0.3875 and 0.8542 does not decompose into "retrieval loss". The parts that *are*
recoverable are the subject of `RARE_MISS_RECOVERY.csv` (timing: evidence that is feasible at a later week in
the same window than the week the system asked) and `RECURSIVE_ACQUISITION.md` (acquisition: asking a second
time for what a first answer showed was missing).

## 5. What would move the ceiling, and what it would cost

Only an **N-lever** moves it — a change to what the world discloses, not to how we search it:

| Lever | Effect on the ceiling | Cost |
|---|---|---|
| N1 (weak-site corroboration, shipped in v4) | F: 0.6042 → 0.7458 (+34 rare patterns) | already paid; netting against refuting siblings added on `research/n1-sibling-refute` |
| Lower k from 3 | would convert part of the 61 | changes the disclosure invariant the benchmark is *about* |
| Count the key polarity-blind (model B) in the gate, not just the judge | F → B at TB_v4: 179 → 205 (**+26**) | the gate would accept evidence a contradicting record argues against |
| Widen the 6-week window | unmeasured | changes the task |

Each is a founders' decision, not an engineering one, and **none of them may be exercised on a held-out seed
before that decision**. Sealed seeds 94000–94009 stay untouched.

## 6. Honest limits of this document

- These are ceilings on *evidence availability*, not on scoring. A system can be at the ceiling for evidence and
  still be wrong, and the decoy bar (118/300 accepted at baseline) is a separate constraint this table says
  nothing about. See `raw/plateau/decoy_ceiling.json`.
- Model K and B are reviewer-constructed readings of what "evidence for the pattern" means. They are *more*
  permissive than the harness; quoting 85.4% as "our ceiling" without saying so would be overclaiming. The
  defensible number for this system as built is **74.6%**.
- The grid is computed at 200 users. A separate measurement suggests discrimination itself degrades with world
  size (non-planted keys passing the count gate: 9.6% at 200 users, ~70% at 2K and 10K). If that reproduces, a
  ceiling computed at 2K would not mean what this one means.

## 7. The gap decomposes exactly, and not where the architecture work assumed

Per-pattern funnel state for all 240 (seed × pattern), from the official `ce0e357` reports' `oracle_rows` and
`found_patterns` against the feasibility grid above. Raw rows: `raw/plateau/gap_decomposition.csv`.

| funnel state | patterns | of which rare | feasible under F/TB_v4 |
|---|---|---|---|
| **found** (asked, correct verdict) | 93 | 5 | 92 |
| **asked, wrong verdict** | 5 | 4 | 4 |
| **never asked** — no candidate was ever generated | **142** | 94 | **83** |
| total | 240 | 103 | 179 |

and the arithmetic closes exactly:

```
92  found and feasible
 4  asked, wrong verdict, feasible
83  never asked, feasible
---
179 = the F/TB_v4 ceiling
```

Three things follow, and they are not what a retrieval-architecture programme would predict.

1. **Judging is almost perfect on what it is given.** Of 98 patterns that became candidates and were asked,
   93 got the right verdict. The re-ask lever — asking an existing candidate again at a later week — can touch
   at most the 4 feasible-later cases among the 5 wrong ones. That is a ceiling of **+4 / 240 = 1.7 points** on
   this population, which is the honest size of the v5 re-ask proposal as stated.
2. **The bottleneck is candidate generation.** 142 of 240 gold patterns never produced a candidate at all, and
   **83 of them had evidence that would have cleared the shipped gate** at some week in their window. That is
   **34.6 points** sitting behind a question that was never asked.
3. **Candidate precision, not candidate recall, is what the detector optimises today.** Per seed the detector
   emits 53–62 candidates of which only 13–19 are true patterns and 31–40 are decoys; `candidates_detected`
   equals `candidates_asked` to within 2, so nothing is being dropped by the top-N cut. Lowering the detection
   bar to reach the 83 would admit decoys on a 2:1 ratio unless the second round can discriminate — which is
   precisely what a recursive acquisition round is for, and precisely what the budget-matched untargeted control
   arm exists to falsify.

One accounting note, reported against interest: **one** found pattern is *not* feasible under F/TB_v4 (93 found
vs 92 found-and-feasible). The shipped judge is polarity-blind, so it can support a pattern the F model says had
no feasible week. It is one case in 240 and it does not move any conclusion, but the ceiling under F is
therefore not a strict upper bound on the *score* — only on evidence availability under that model.
