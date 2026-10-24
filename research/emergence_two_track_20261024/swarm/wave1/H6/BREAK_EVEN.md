# BREAK_EVEN.md — H6: the precision a ranker over the rule-only pool must reach before asking pays

**Summary (10 lines).**
1. Under budget (a) — every rule-only question displaces a shipped snapshot question — the break-even top-N gold precision is **p\*_a = g_s / c_g = 0.275 / 0.587 = 0.47**, independent of N; the 10,000-draw Monte Carlo puts the crossing at 0.465 for N = 10, 20, 30.
2. At **N = 60 per seed no ranker clears (a), including a label oracle**: the filtered pool holds 20–27 gold per seed, so top-60 precision is capped at 0.38 < 0.47, and the 81 feasible gold it could ever find are fewer than the 93 found patterns it would displace (MC: −12.0 ± 0 at p = 1).
3. The 0.47 assumes the ranker asks each key at a **gate-feasible week** (c_g = 81/138 = 0.587). The shipped rule layer picks the week itself and the recorded conversion is then **0.092 per gold key** (A3 random arm: 3.0 gained / 32.5 gold keys asked; "1 pattern per 220 questions"), which makes p\*_a = 2.98 — **unattainable at any precision**. Intermediate (ranker picks key, rule layer picks week, 65/138 land in-window): c_g = 0.276, p\*_a = 0.995.
4. Under budget (b) — questions added, nothing displaced — the gold break-even is 0, but every accepted decoy breaches the 118/118 bar; at an exchange rate of one accepted decoy per found pattern, **p\*_b = λδa_d / (c_g + λδa_d) = 0.19** (a_d = 0.551, δ = 0.246), and 0.035 at the recorded rule-only acceptance a_d = 0.086.
5. At the wave-0 operating point (rule_weeks ≥ 11: 13.5 % gold / 21.3 % decoy) and N = 10 per seed, six-seed totals: **(a) −11.2 found, −13.7 accepted decoys, +3.8 background; (b) +5.1 found (+2.1 pts), +7.1 accepted decoys (118 → 125), +4.3 background.** Every seed loses under (a).
6. Decoy acceptance enters p\* only through λ; with λ = 0 the decoy side is a constraint, and under (a) it is **not binding** (a displaced snapshot question carries 0.346 accepted decoys; a rule-only question at d ≤ 0.63 carries fewer).
7. BH at q ∈ {0.1, 0.2, 0.3} on the only calibrated statistic on record (rule_weeks, label-free marginal null) **selects 0 keys** on every seed; no rule_weeks bucket exceeds 0.233 precision, so the prec@k ≥ 1−q rule also yields k_q = 0. Discovery-level FDR (accepted decoys + bg among what enters the score) is 0.565 for the shipped system itself and 0.707 at the wave-0 point; FDR ≤ 0.3 needs p ≥ 0.47 — numerically the same bar as (a).
8. Terms the recorded numbers do not fix: the unplanted false-discovery rate for rule-only keys (bounded 0.034–0.18 from the A3/A2 f = 1 arms; 0.111 in the snapshot pool), the exchange rate λ, and the per-question cost κ under (b); every result is given at both ends.
9. Oracle bound under (a) at the ceiling conversion: +18.7 patterns (N = 10), +37.4 (N = 20), +31.5 (N = 30), −18 (N = 60) across six seeds — the most any ranker can gain.
10. **Quote:** *A ranker over the filtered pool must reach precision ≥ 0.47 at top-N = 10–30 per seed to pay for itself under budget (a), and ≥ 0.19 under budget (b).* Both figures assume the question is asked at a feasible week; at the shipped week selection no precision pays under (a).

Files: `break_even_closed_form.py` (tables, operating point, BH), `break_even_mc.py` (10,000-draw check), `closed_form_output.txt`, `mc_output.txt`. All read-only over `raw/plateau/candidate_pool.csv`, `raw/official_ce0e357/BASELINE_SEEDS_0-5.csv`, `raw/rescue/arms_seeds0-5.txt`. Dev seeds 0–5 only. No labels select anything; labels score and bound.

---

## 1. Recorded inputs — nothing else is used

| symbol | meaning | value | source |
|---|---|---|---|
| G, P | gold keys / keys in the rule-only pool | 138 / 2,831 = 0.049 | `candidate_pool.csv` |
| F7 | filtered pool rule_weeks ≥ 7: keys / gold / decoy / unplanted | 1,765 / 138 (0.078) / 317 (0.180) / 1,310 | same |
| F11 | rule_weeks ≥ 11: keys / gold / decoy / unplanted | 736 / 99 (**0.135**) / 157 (**0.213**) / 480 | same; wave-0 operating point |
| G_s | filtered gold per seed | 25, 27, 21, 21, 20, 24 | same |
| GF_s | filtered gold per seed that are F/TB_v4-feasible | 16, 16, 12, 14, 10, 13 (= 81) | same |
| c_g | P(gold key clears the gate \| asked at a feasible week) | 81/138 = **0.587** (task: 83/142 = 0.585; F11 gold: 63/99 = 0.636) | `EVIDENCE_CEILING.md` §7, pool csv |
| snapshot asked | questions in the official run | 341 = 100 gold (93 found) + 214 decoy + 27 unplanted | pool csv; ledger 55/53/60/53/60/60 |
| a_d | decoy acceptance among asked decoys | 118/214 = **0.551** (per seed 0.49, 0.58, 0.58, 0.67, 0.45, 0.56) | `BASELINE_SEEDS_0-5.csv`, pool csv |
| u_s | false discovery among asked unplanted (snapshot pool) | 3/27 = 0.111 (bg 0,1,1,0,0,1) | same |
| g_s | found per snapshot question | 0.29 × 93/98 = **0.275** (csv: 93/341 = 0.273) | task framing; pool csv |
| dacc_s | accepted decoys per snapshot question | 118/341 = **0.346** | ledger |
| bg_s | background-supported per snapshot question | 3/341 = 0.0088 | ledger |
| A3 random arm | 360 rule-only questions at random, replay | 1.6 found, 3.7 accepted decoys, 10.1 bg, 8.9 gold rows asked | `raw/rescue/arms_seeds0-5.txt`, f = 1.0 |
| c_g^ship | found per gold **key** asked at the rule layer's own snapshot week | Σgained / (0.049 × Σquestions) over the four A3 rows = 3.0 / 32.5 = **0.092** (= 1 per 222 questions) | same |
| a_d^rule | accepted per decoy key, rule-only pool at random | 3.7 / (0.119 × 360) = **0.086** | same |
| u^rule | bg per unplanted key, rule-only pool at random | 10.1 / (0.832 × 360) = 0.034 (A2 strength-selected f = 1: ≈ 0.18) | same |
| in-window | gold keys whose rule snapshot week falls inside the pattern window | 65/138 = 0.47 | `CANDIDATE_RESCUE.md` §1 |
| rank-bucket yield | new patterns found by detector rank 0–14 / 15–29 / 30–44 / 45–59 (6 seeds × 15 ranks) | 23 / 24 / 30 / 41 | `CANDIDATE_RESCUE.md` §2 (v4 replay) |
| decoy bar | accepted decoys must not exceed the baseline | 118 of 300 planted; headroom H = 0 | task; `CANDIDATE_RESCUE.md` |

Three conversions for a gold key, all recorded, bracket the single term that decides everything:

```
c_g^ceil = 81/138          = 0.587   the question is asked at a week where the pattern's own records clear TB_v4
c_g^mid  = (65/138)·0.587  = 0.276   the ranker picks the key, the shipped rule layer picks the week; independence assumed
c_g^ship = 3.0/32.5        = 0.092   recorded: random rule-only key, rule-layer week, shipped judge (A3 arms)
```

## 2. Model — every assumption as an equation

Per seed, the ranker's top-N contains gold fraction p, decoy fraction d = δ(1−p), unplanted fraction (1−δ)(1−p), with δ the decoy share of non-gold (0.246 in F11, 0.195 in F7). Each key is one question. Outcomes are independent Bernoulli draws with the recorded rates:

```
E[found    | gold asked]      = c_g                      (A1)
E[accepted | decoy asked]     = a_d                      (A2)
E[bg       | unplanted asked] = u                        (A3)
E[found, accepted, bg | displaced snapshot question] = (g_s, dacc_s, bg_s) = (0.275, 0.346, 0.0088)   (A4)
utility per question          = Δfound − λ·Δaccepted − κ   (λ = gold-value of one avoided accepted decoy, κ = cost of a question in found-gold units)   (A5)
pool cap                      = p ≤ G_s / N  (gold drawn without replacement; a seed has 20–27 gold)   (A6)
```

(A4) treats the displaced question as an average snapshot question, as the task specifies. The recorded marginal cost is higher: in the v4 replay the last 15 ranks (45–59) yielded 41/90 = 0.456 found per question, so g_s^marg(N) = 0.456 (N = 10), 0.425 (20), 0.394 (30), 0.328 (60). Both are tabulated.

### Budget (a): displacement

```
ΔG_a(N) = N·(p·c_g − g_s)                       ΔD_a(N) = N·(d·a_d − dacc_s)        ΔU_a(N) = N·((1−p−d)·u − bg_s)
p*_a    = [g_s + λ·(δ·a_d − dacc_s)] / [c_g + λ·δ·a_d]                                            (B1)
λ = 0:   p*_a = g_s / c_g                                                                        (B2)
```

(B2) does **not** depend on N or on a_d. a_d enters only through λ; and because a snapshot question carries 0.346 accepted decoys while a rule-only question carries d·a_d ≤ 0.213 × 0.551 = 0.117, every λ > 0 *lowers* p\*_a (the rule-only pool is decoy-cheaper per question than the pool it displaces). Under λ = 0 the decoy side is a constraint, ΔD_a ≤ 0 ⇔ d·a_d ≤ 0.346 ⇔ d ≤ 0.63 at a_d = 0.551 — never binding for any top-N below the snapshot pool's own 63 % decoy share.

### Budget (b): addition

```
ΔG_b(N) = N·p·c_g                 ΔD_b(N) = N·d·a_d                ΔU_b(N) = N·(1−p−d)·u
p*_b    = (κ + λ·δ·a_d) / (c_g + λ·δ·a_d)                                                       (B3)
κ = λ = 0:  p*_b = 0.    Decoy bar as a hard constraint with headroom H:  N·d·a_d ≤ H;  H = 0 ⇒ d = 0.   (B4)
P(no added accepted decoy across six seeds) = (1 − d·a_d)^(6N)                                  (B5)
```

κ is not recorded. Two defensible anchors: κ = 1/220 (the question must beat random asking from the pool) gives p\*_b = 0.008; κ = g_s = 0.275 (the question must match the shipped average question) gives p\*_b = 0.469 = p\*_a.

## 3. p\*(N) — the table

Precision is top-N gold precision per seed; N is questions per seed (six-seed totals are 6N). Minimum-one-find: p ≥ 1/(N·c_g).

| N | p\*_a (λ=0, g_s=0.275, c_g=0.587) | p\*_a with recorded marginal g_s(N) | p\*_a at c_g^mid=0.276 | p\*_a at c_g^ship=0.092 | pool cap G_s/N (mean / worst seed) | attainable? | p ≥ 1/(N c_g) | p\*_b λ=1, a_d=0.551 | p\*_b λ=1, a_d=0.086 |
|---|---|---|---|---|---|---|---|---|---|
| 10 | **0.469** | 0.776 | 0.995 | 2.98 | 1.00 / 1.00 | yes | 0.170 | 0.188 | 0.035 |
| 20 | **0.469** | 0.724 | 0.995 | 2.98 | 1.00 / 1.00 | yes | 0.085 | 0.188 | 0.035 |
| 30 | **0.469** | 0.672 | 0.995 | 2.98 | 0.77 / 0.67 | yes | 0.057 | 0.188 | 0.035 |
| 60 | 0.469 | 0.558 | 0.995 | 2.98 | **0.38 / 0.33** | **no — even an oracle loses** | 0.028 | 0.188 | 0.035 |

p\*_a with λ > 0 (c_g = 0.587, δ = 0.246): λ = 0.5 → 0.260; λ = 1 → 0.090. p\*_b with λ = 2: 0.317 (a_d = 0.551), 0.067 (a_d = 0.086). At c_g^mid, p\*_b(λ=1) = 0.330; at c_g^ship, 0.595.

Largest N at which break-even is attainable under (a): N ≤ G_s / p\*_a = 43 (seed 4, 20 gold) to 58 (seed 1, 27 gold); with feasibility, the gain saturates at GF_s = 10–16 found per seed, so the useful range is N ≈ 10–30.

## 4. Oracle ceilings under (a) — what any ranker is bounded by

Labels used to bound, never to select. Six-seed net found at c_g = 0.587, g_s = 0.275:

| N | gold-oracle (ranks gold perfectly, blind to feasibility) | feasibility-oracle (ranks feasible gold first) |
|---|---|---|
| 10 | +18.7 (+7.8 pts) | +43.5 (+18.1 pts) |
| 20 | +37.4 (+15.6 pts) | +48.0 (+20.0 pts) |
| 30 | +31.5 (+13.1 pts) | +31.5 |
| 60 | −18.1 (linear) / **−12.0 (MC: 81 feasible − 93 displaced)** | same |

The 85 % target needs +111 patterns on 240; the best any ranker can do under the shipped budget is +48, and that requires knowing feasibility, which is the gate's own private information.

## 5. Expected net change at the wave-0 operating point

Ranker = "rule_weeks ≥ 11", p = 0.135, d = 0.213, unplanted 0.652 (F11 gold feasibility 63/99 = 0.636). Six-seed totals, closed form (MC in §7 agrees to within 0.4):

| N | rates | (a) Δfound | (a) Δaccepted decoys | (a) Δbg | (b) Δfound | (b) Δaccepted decoys | (b) Δbg |
|---|---|---|---|---|---|---|---|
| 10 | ceiling c_g=0.636, a_d=0.551, u=0.111 | **−11.4** | −13.7 | +3.8 | **+5.1 (+2.1 pts)** | **+7.1** (118 → 125) | +4.3 |
| 20 | same | −22.8 | −27.4 | +7.6 | +10.3 (+4.3) | +14.1 | +8.7 |
| 30 | same | −34.1 | −41.1 | +11.5 | +15.4 (+6.4) | +21.2 | +13.0 |
| 60 | same | −68.3 | −82.2 | +22.9 | +30.8 (+12.8) | +42.3 (118 → 160) | +26.1 |
| 10 | recorded shipped-week c_g=0.092, a_d=0.086, u=0.034 | −15.8 | −19.7 | +0.8 | +0.7 (+0.3) | +1.1 | +1.3 |
| 60 | same | −94.6 | −118 | +4.8 | +4.5 (+1.9) | +6.6 | +7.9 |

Reading: under (a) the wave-0 filter loses 1.1–1.6 found patterns for every one it finds, on every seed (MC per-seed found 0.54–1.06 vs lost 2.26–3.21 at N = 10). It does cut accepted decoys — displacing snapshot questions is where the decoy saving comes from, not the ranker. Under (b) it buys +2 points at N = 10 only by breaching the decoy bar with probability 0.999 (B5). This reproduces the Gate-0 "+3 −14" shape and the "1 per 220" yield without fitting anything.

The official run left 19 unused questions (seeds 0, 1, 3 asked 55, 53, 53 of 60). Filling them from F11 at ceiling rates adds 1.6 found and 2.2 accepted decoys in expectation — the only truly free (b) slots, and they do not move the result.

## 6. BH-style rule — how many of the top-N may be asked at FDR q

Three forms, from strictest definition of "discovery" to the one the scorer actually uses.

**Rule 1 — classical BH on the recorded statistic, label-free null.** For key i in a seed's filtered pool, p_i = P(rule_weeks ≥ rw_i) over that pool (the marginal is a conservative null: it mixes 7.8 % gold into it). BH: ask the largest k with p_(k) ≤ k·q/m, m = 265–308. Result: **k = 0 on every seed at q = 0.1, 0.2, 0.3.** The tail of rule_weeks is 24 % decoy and 65 % unplanted, so persistence never concentrates gold enough for BH to release a single key.

**Rule 2 — running precision.** For any ranker with calibrated posterior π_i of gold, the expected FDR of asking the top-k (a non-gold ask is a false discovery) is 1 − (1/k)Σ_{i≤k} π_i, so

```
k_q = max{ k : prec@k ≥ 1 − q }      i.e. prec@k ≥ 0.9 / 0.8 / 0.7 for q = 0.1 / 0.2 / 0.3        (C1)
```

The finest calibrated statistic on record is gold rate by exact rule_weeks (six seeds pooled, filtered pool); its best bucket is rw = 12 at 0.233 and its best running prefix is 0.135. **k_q = 0 for all three q.** A hypothesis must show prec@k ≥ 0.7 somewhere on its curve before FDR 0.3 lets it ask even one key — a stricter bar than break-even's 0.47, so (C1) binds before (B2) does.

**Rule 3 — discovery-level FDR among what enters the score.** A false discovery is an accepted decoy or a background-supported unplanted key:

```
FDR_disc(p) = [δ(1−p)·a_d + (1−δ)(1−p)·u] / [p·c_g + δ(1−p)·a_d + (1−δ)(1−p)·u]                  (C2)
```

Shipped system itself: (118 + 3)/(93 + 118 + 3) = **0.565**. Wave-0 point: 0.707 (0.338 at recorded rule-only rates). Precision needed for FDR_disc ≤ q, c_g = 0.587, δ = 0.246:

| q | p needed, a_d = 0.551, u = 0.111 | p needed, recorded rule-only a_d = 0.086, u = 0.034 |
|---|---|---|
| 0.1 | 0.771 | 0.417 |
| 0.2 | 0.599 | 0.241 |
| 0.3 | **0.466** | 0.156 |

At q = 0.3 the discovery-FDR bar and the budget-(a) break-even coincide at 0.47: a ranker that pays for a displaced question is exactly one whose discoveries are 30 % false, which is still better than the shipped system's 56.5 %.

**Operational rule for the swarm.** Given a hypothesis' prec@k curve on the filtered pool (leave-one-seed-out, labels scoring only): ask the top-k with k = min( max{k : prec@k ≥ 1−q}, G_s / p\*_a ). With every statistic recorded so far, k = 0.

## 7. Monte Carlo check — 10,000 draws over the filtered pool (`break_even_mc.py`, seed 20261024)

Design: per draw and seed, real keys are sampled. A synthetic ranker fills N slots, each gold with probability p (gold drawn without replacement from the seed's filtered gold, so A6 binds), otherwise non-gold drawn without replacement from the seed's F11 non-gold (δ = 0.246). A gold key is found iff its recorded `F_TB_v4_feasible` flag is 1 (so feasibility is key-specific, marginal 0.587); a decoy is accepted w.p. 0.551; an unplanted key yields bg w.p. 0.111. Under (a), N of the seed's asked snapshot keys are displaced uniformly; their found flags are the recorded ones and their accepted decoys / bg are hypergeometric against the seed's recorded totals (17, 18, 21, 22, 18, 22 of 35, 31, 36, 33, 40, 39 decoys; 0, 1, 1, 0, 0, 1 of 5, 9, 5, 1, 1, 6 unplanted).

| check | closed form | Monte Carlo |
|---|---|---|
| break-even precision, N = 10 / 20 / 30 | 0.469 (0.465 with g_s = 93/341) | **0.465 / 0.465 / 0.465** |
| break-even, N = 60 | 0.469 but cap 0.38 | **no crossing**; realised p saturates at 0.383; Δfound_a = −12.0 ± 0.0 at p ≥ 0.7 |
| Δfound_a at p = 0.469, N = 10 / 20 / 30 | +0.15 / +0.31 / +0.46 | +0.12 ± 4.5 / +0.26 ± 5.9 / +0.44 ± 6.4; P(Δ > 0) = 0.46 / 0.49 / 0.50 |
| wave-0 point N = 10, (a) | −11.4 found, −13.7 decoys, +3.8 bg | −11.2 ± 3.8, −13.7, +3.8; P(Δfound > 0) = 0.001 |
| wave-0 point N = 10, (b) | +5.1 found, +7.1 decoys, +4.3 bg | +5.1 ± 2.1, +7.1, +4.3 |
| wave-0 point N = 60, (b) | +30.8 found, +42.3 decoys | +30.9 ± 3.8, +42.4 |
| shipped-week rates, p = 1.0, N = 10, (a) | −10.8 | −10.8 ± 3.9; P(Δ > 0) = 0.002 — confirms p\*_a > 1 |
| mid rates c_g = 0.276, (a) | p\*_a = 0.995 | p = 1.0 gives +0.2 (N = 10), +0.45 (N = 20); p = 0.9 gives −1.4, −2.8 |
| P(no added accepted decoy) under (b), N = 10, a_d = 0.551 | (1 − 0.117)^60 = 0.001 | 0.001 (0.320 at a_d = 0.086) |
| oracle p = 1.0 under (a), N = 10 / 20 / 30 | +18.9 / +37.7 / +56.6 (linear) | +18.8 / +37.5 / **+32.0** (cap: 81 feasible gold) |

One deliberate divergence: the linear closed form overshoots the displacement loss at N = 60 (0.275 × 360 = 99 > the 93 that exist); the MC, which displaces real keys, gives −93 at p = 0. The p\*(N) table is unaffected because the cap already rules out N = 60.

Per-seed spread at the wave-0 point, N = 10, (a): found 1.06, 1.04, 0.81, 0.69, 0.54, 0.97 against lost 2.35, 2.26, 2.83, 3.21, 3.17, 2.50. No seed breaks even.

## 8. What the recorded numbers do not fix — bounds both ways

| term | why it is open | low end | high end | effect |
|---|---|---|---|---|
| c_g for a ranked key | depends on whether the ranker also chooses the week; the shipped rule layer lands 47 % of gold in-window and converts 9 % at random | 0.092 (recorded) → p\*_a = 2.98 | 0.587 (feasibility) → p\*_a = 0.47 | **dominant**; a hypothesis must say how it picks the week or be scored at p\*_a ≈ 1 |
| a_d for rule-only decoys | task says use 118/214; recorded rule-only random arm shows 3.7/43 | 0.086 | 0.551 | enters only via λ; p\*_b(λ=1) = 0.035–0.188 |
| u for rule-only unplanted | snapshot pool 3/27; A3 random 10.1/300; A2 strength-selected ≈ 0.18 | 0.034 | 0.18 | bg is not on the decoy bar; +0.8 to +4.3 per 60 questions |
| λ (accepted decoy ↔ found pattern) | the bar is a constraint, not a weight; baseline sits on it | 0 (constraint non-binding under (a)) | ∞ (hard bar: (b) infeasible for d > 0) | p\*_a ∈ [0.09, 0.47]; p\*_b ∈ [0, 1] |
| κ (cost of a question under (b)) | the official run asked 341 of 347 snapshot keys, so an added question has no displaced alternative | 0 → p\*_b = 0 | 0.275 → p\*_b = 0.47 | — |
| displaced-question yield | average (0.275) vs recorded marginal (0.33–0.46, v4 replay) | 0.275 → 0.47 | 0.456 → 0.78 at N = 10 | the quoted 0.47 is the lenient end |

## 9. The sentence

> **A ranker over the filtered pool must reach precision ≥ 0.47 at top-N = 10–30 per seed to pay for itself under budget (a), and ≥ 0.19 under budget (b).**

Conditions attached to the quote: both figures assume the promoted key is asked at a gate-feasible week (c_g = 0.587); at the shipped rule-layer week selection (recorded c_g = 0.092) no precision pays under (a). Under (b) 0.19 is the λ = 1 exchange rate with a_d = 0.551; on gold alone the threshold is 0, and under the hard 118-decoy bar no finite precision clears it. At N = 60 per seed nothing clears (a), including an oracle. The wave-0 filter sits at 0.135, i.e. 0.29× the (a) bar and 0.71× the (b) bar, and BH at q ≤ 0.3 releases none of its keys.
