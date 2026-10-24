# Wave 2 — the falsifiers' verdicts

**Status: two of four reported (F-H3 16:26Z, F-H5 16:39Z). F-H4 and F-H7 outstanding; this document is
amended when they land.** Deliverables verbatim under `swarm/wave2/<id>/`.

Every falsifier was handed its target's kills in advance (`WAVE_1_RESULTS.md` §3) and told that confirming was
not its job. Read-only throughout; `git status` clean in every tree touched.

## 1. Verdicts

| target | falsifier | verdict | which kill fired | which nearly did |
|---|---|---|---|---|
| **H3-3** rule-week band | F-H3 | **DEAD** | 3 of 4: beats the free line on 4/6 seeds not 6/6 (margin +0.036, p = 0.12, survives no best-of discount); top-20 precision 0.14 vs measured p* 0.39; adds 5–12 accepted decoys to a bar with zero headroom | kill 4 missed: the ≥16 demotion is a distinct statistic — worth exactly AUC 0.584 on 307 keys, which is the best-of-8 line to three decimals |
| **H5-3** shape-gated count (the lever) | F-H5 | **DEAD** | kill 4: at the ranker's own best label-free week, c_g = **0.616**, not the ~0.75 H5 estimated (H5's figure was feasibility at *some* week of the label window); p* = 0.447 against measured P@20 0.393. Kill 1: at 2,000 users the θ ≤ 1.25 gate passes **67% of background** vs 39% of gold (74%/75% at 200), P@20 gold 0.108 | kill 2 (one formula) survived by 0.05 on the two weakest seeds; kill 3 (θ wobbles 1.25 ↔ 1.4 across subsets, not a leak) |
| **H5-2** co-activation (the statistic) | F-H5 | **reclassified**, not promoted | survives every leak and selection test at 200 users (0.945; empirical best-of-56 null max 0.619, p < 0.0005) and erodes to **0.742** at 2,000 (0.69 on classes 1 and 3, which are 76% of decoys). Mechanism corrected: only 5% of gold co-active weeks are same-region, and seed 4 keeps the 7-site org shape yet drops 0.94 → 0.73 — the erosion is **record density**, not region granularity. Any denser world kills it | — |
| H4-3 expected utility | F-H4 | *pending* | | |
| H7-1 echo-decoy veto | F-H7 | *pending* | | |

## 2. What this does to the control rule

```
survivors so far: 0 of 2 rankers (H3-3 dead, H5-3 dead)
wave 3 (combination vs budget-matched control) runs only if F-H4 survives
H7-1 is a gate fix, not a ranker: it rides along with wave 3 or lands on its own, but cannot trigger it
if F-H4 dies: NEGATIVE_RESULT.md with sentence B, and the swarm stops at 14 of 24
```

## 3. Two things the falsifiers found that the hypotheses did not

**The feasibility term was being estimated, not measured.** H5 reported "~75% of top-20 gold is gate-feasible";
F-H5 traced that to the pool CSV's flag, which means feasible at *some* week of the label window — 240/240 of
the gold, by construction. At the week the ranker would ask, the figure is 0.616; at the shipped rule layer's
week it is 0.340 (p* = 0.81). H6 warned that this term was dominant. It was. **Any future ranker claim must
report c_g at its own asked week or be scored against p* ≈ 0.8.**

**"Region = site at 200 users" was the wrong mechanism, from two independent agents.** H1 and H5 both read the
generator's `pick_users` and concluded the signature came from regions collapsing to single sites. F-H5 tested
that directly — region-granularity sync recovers only 0.742 → 0.756, and a seed that keeps the 200-user org
shape still loses the signal — and found the real cause in record density. A hypothesis's own account of why
it works is itself a claim to falsify.

## 4. Reported against interest

- F-H5's pooled 0.70 line at 2,000 users is **not crossed by the letter** (0.742, CI 0.684–0.794, P(< 0.70) =
  0.07). The kill is on the lever's P@20 and on the dominant decoy classes, and F-H5 says so both ways.
- H5-2 is the only statistic in two waves that beats its empirical null by a wide margin. It is not nothing; it
  is a fact about sparse worlds, and a 200-user benchmark is a sparse world.
- F-H3 found H3-3's ≥16 demotion is a genuinely distinct signal (LR test p = 0.052 given longest run) — it
  simply adds exactly as much as a best-of-8 selection would.
- The 2,000-user real path costs 26–34 s per seed (pipeline 21–27 s, detection 1.6–2.3 s); `ew.generate(2000)`
  itself is 0.1 s. The "90 s per seed" in the briefs was `ceiling_b.py`'s per-week Python loop, as four agents
  now note.
