# Wave 2 — the falsifiers' verdicts

**Status: all four reported (F-H3 16:26Z, F-H5 16:39Z, F-H4 16:40Z, F-H7 16:42Z). Zero of four targets survived.** Deliverables verbatim under `swarm/wave2/<id>/`.

Every falsifier was handed its target's kills in advance (`WAVE_1_RESULTS.md` §3) and told that confirming was
not its job. Read-only throughout; `git status` clean in every tree touched.

## 1. Verdicts

| target | falsifier | verdict | which kill fired | which nearly did |
|---|---|---|---|---|
| **H3-3** rule-week band | F-H3 | **DEAD** | 3 of 4: beats the free line on 4/6 seeds not 6/6 (margin +0.036, p = 0.12, survives no best-of discount); top-20 precision 0.14 vs measured p* 0.39; adds 5–12 accepted decoys to a bar with zero headroom | kill 4 missed: the ≥16 demotion is a distinct statistic — worth exactly AUC 0.584 on 307 keys, which is the best-of-8 line to three decimals |
| **H5-3** shape-gated count (the lever) | F-H5 | **DEAD** | kill 4: at the ranker's own best label-free week, c_g = **0.616**, not the ~0.75 H5 estimated (H5's figure was feasibility at *some* week of the label window); p* = 0.447 against measured P@20 0.393. Kill 1: at 2,000 users the θ ≤ 1.25 gate passes **67% of background** vs 39% of gold (74%/75% at 200), P@20 gold 0.108 | kill 2 (one formula) survived by 0.05 on the two weakest seeds; kill 3 (θ wobbles 1.25 ↔ 1.4 across subsets, not a leak) |
| **H5-2** co-activation (the statistic) | F-H5 | **reclassified**, not promoted | survives every leak and selection test at 200 users (0.945; empirical best-of-56 null max 0.619, p < 0.0005) and erodes to **0.742** at 2,000 (0.69 on classes 1 and 3, which are 76% of decoys). Mechanism corrected: only 5% of gold co-active weeks are same-region, and seed 4 keeps the 7-site org shape yet drops 0.94 → 0.73 — the erosion is **record density**, not region granularity. Any denser world kills it | — |
| **H4-3** expected utility over the cell class | F-H4 | **DEAD** | its own per-seed kill (seeds 1 and 4 below 0.70 at ν = (1,1)); **80% of its AUC orders keys at classes where the gate never clears** — among keys that can yield a verdict, 0.657; at 2,000 users the thin class is 90.9% unplanted and LOSO AUC is **0.451**; at its own argmax week c_g = 0.15–0.24 → p* > 1. Its 0.842 on the asked set was **look-ahead** (a later week than the one asked on 82.7% of rows; 0.661 at the asked week). Mechanism false even at 200: common gold (50/137), not rare (6/103), passes through the thin state | kills 2 and 3: no leak (permutation of the whole fit, 0/200 reach 0.731); and the replay reorder below |
| H4-2 week choice | F-H4 | DEAD as a stand-alone claim | P̂(clear \| class) is 0 or 1 on 41/64 classes — a gate lookup, not a VoI; a parameter-free structural rule gets 101 vs its 99; ×4.5 optimistic, not replayable | — |
| *reorder the already-asked pool by the exported class at the snapshot week* | F-H4 | **alive at replay level only** | 138/240 found vs rule-first 128 (e2e-confirmed 127), decoys 97 vs 113, bg 11 vs 13, rare 10 vs 8; per seed better on 5, tie on 1 (sign p = 1/32); seed-swap stable 136–139. The selector is a **label-fitted** class table — H8 §2 says a fitted table is labels selecting; the label-free variants reach 137 with rare 8 → 4 and decoys 110. Not the hypothesis H4 promoted; an empirical reorder whose at-decision AUC (0.66) sits in the selection band | — |
| **H7-1** echo-decoy veto | F-H7 | **DEAD** | kill 1: the shipped pack gives every record its own root (`records.py:412-420`, `root_ref` always `None`), so **`support_bucket == roots_bucket` in 899 of 904 recorded confirms** — the veto is a no-op on the shipped pack and acts only under the v4 `nd` override. Kill 2: replaying all 341 questions / 904 confirms through the patched gate: **93 → 93, 118 → 118, type-4 41 → 41**. Kill 3: gold cost appears the moment gold shares roots (14–15/240 at 25% sharing, beyond N2's own 12–13). Kill 4: at 2,000 users only 10–36% of type-4 passes removed (an echo site holds 3–9 records on 1–3 roots, invisible to a bucket gap) | — ; the proposal is delivered as `gate_veto.patch` with its exact pin consequences (`SPIKE_CODE_HASH` → `8b2c6940…`, all five `LIVE_CODE_HASHES_R002` digests move), never applied |

## 2. What this does to the control rule

```
survivors: 0 of 4 targets (H3-3, H5-3, H4-3, H7-1 all dead)
→ NEGATIVE_RESULT.md written: sentence B. The swarm stops at 14 of 24.
H7-1 is dead too: inert on the shipped pack, a no-op on the recorded verdicts.
The replay-level reorder is handed to NEXT_EXPERIMENTS.md as ONE e2e arm, gated on a founders' ruling
about label-fitted tables -- not spawned as a wave 3, because it does not answer the swarm's question
(it re-ranks what is already asked; it reaches no never-asked pattern).
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
