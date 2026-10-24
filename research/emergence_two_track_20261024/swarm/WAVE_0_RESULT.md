# Wave 0 — one free check, run before spawning anything

Agent 3's prompt says: *compute the per-family gold rate first; if every family sits near 4.9% the rule identity
is uninformative and this family dies immediately.* That check costs one query over
`raw/plateau/candidate_pool.csv`, so it was run before spending an agent. It did not die. It also did not
survive intact.

## The signal

Gold rate in the rule-only pool (2,831 keys, 138 gold, base rate 4.9%), bucketed by how many weeks the key's
rule fired:

| rule weeks | keys | gold | gold rate |
|---|---|---|---|
| 1 | 143 | 0 | 0.000 |
| 2 | 142 | 0 | 0.000 |
| 3 | 165 | 0 | 0.000 |
| 4 | 201 | 0 | 0.000 |
| 5 | 181 | 0 | 0.000 |
| 6 | 234 | 0 | 0.000 |
| 7–10 | 1,029 | 39 | 0.038 |
| **11+** | **736** | **99** | **0.135** |

**1,066 keys — 38% of the pool — contain not one gold pattern between them.**

It survives leave-one-seed-out, which is where most things this shape die:

| seed | pool | kept at ≥11 | gold kept | gold total | precision | recall |
|---|---|---|---|---|---|---|
| 0 | 484 | 139 | 20 | 25 | 0.144 | 0.800 |
| 1 | 486 | 115 | 20 | 27 | 0.174 | 0.741 |
| 2 | 432 | 110 | 14 | 21 | 0.127 | 0.667 |
| 3 | 498 | 130 | 13 | 21 | 0.100 | 0.619 |
| 4 | 470 | 129 | 16 | 20 | 0.124 | 0.800 |
| 5 | 461 | 113 | 16 | 24 | 0.142 | 0.667 |
| **all** | 2,831 | 736 | 99 | 138 | **0.135** | **0.717** |

No seed fails. Precision 0.100–0.174, recall 0.619–0.800. It is not best-of-many: one threshold, one feature,
chosen because the buckets are empty below it.

## Why it is the wrong kind of signal anyway

| rule weeks | gold | decoy | unplanted |
|---|---|---|---|
| ≤6 | 0.000 | 0.020 | 0.980 |
| 7–10 | 0.038 | 0.155 | 0.807 |
| 11+ | **0.135** | **0.213** | 0.652 |

Rule-week count is a **persistence** statistic, and decoys persist too — by construction, since that is what
makes them decoys. Pool base rates are gold 4.9% and decoy 11.9%, so the ≥11 cut lifts gold 2.8× and decoy
1.8×. The mix improves (gold:decoy goes from 0.41 to 0.63) and is still decoy-heavy at 1.58 decoys per gold.

That is almost certainly why the Gate-0 session's rule-only promotion arm came back **+3 −14**: persistence
finds more of everything, and the decoys it finds are scored against you.

## What is actually worth taking from it

**Use it as a negative filter, not a ranker.** `rule_weeks ≤ 6` removes 1,066 of 2,831 keys at **zero gold
cost**, measured on every seed. That does not by itself buy a single pattern, but it shrinks the candidate space
38% for free, and every statistic in waves 1–3 should be evaluated on the 1,765 that remain — a 4.9% base rate
becomes 7.8%, and a hypothesis that cannot beat chance there was never going to pay.

**Revised assignment for the swarm.** The question is no longer "does anything separate the pool". Something
does, it is monotone in persistence, and it is not enough. The question is sharper:

> Is there a statistic that separates gold from **decoys** — not from unplanted noise — inside the surviving
> 1,765 keys?

Spectral structure (agent 1) is now the most interesting family rather than merely the most concrete, because a
shared latent cause is exactly what a planted pattern has and a decoy, assembled to look persistent, does not.
Agent 8's null must now be fitted on the filtered pool, or it will look good for the same trivial reason.

## Status

Not a result about the system. A result about the pool, from committed artifacts, costing one query and no
agent. It is wave 0 because it changes what waves 1–3 should be told.
