# Superseded runs (not used in any reported result)

Two rounds of runs were discarded during construction of this experiment. No
table, figure or sentence in `RESULTS.md` derives from either.

**Round 1 — `round1_pre_tiebreak_fix/` (raw data retained, gzipped).**
Discarded after two corrections were found:

1. *Tie-breaking bias.* Aggregation resolved ties with `argmax` over the value
   code, and code 0 is the claim's true value — so every tie resolved toward the
   correct answer. This handed all systems free accuracy, and handed more of it
   to systems whose support counts are small integers (lineage aggregation
   counts distinct origins rather than replicas, so it ties more often).
   Replaced by a per-(claim, value code) jitter drawn once per world and shared
   by every system, so ties resolve independently of which value is true.
2. *Unbounded efficiency ratios.* Redundancy and communication efficiency used
   an epsilon-guarded denominator, making B0 — which publishes nothing and sends
   no messages — appear infinitely efficient. Now reported as undefined.

**Round 2 — data deleted, never analysed.**
Restarted to add three metrics that were missing: contradiction *resolution*
accuracy (a system that resolves contradictions by re-verification lowers its own
detection recall while raising this, so both are needed), accuracy conditional on
having retrieved anything, and partition reconciliation rate.

An earlier pilot, before round 1, exposed a placement bug that was fixed before
either round: lineage placement cycled over failure domains, putting an equal
number of replicas in every domain regardless of its size. That created hot-spot
hosts in small domains which the white-box attacker exploited, and made the
lineage fabric look fragile (KS 0.44 vs 0.72 for random replication) for reasons
that had nothing to do with lineage. Hosts are now drawn uniformly over the
network and redrawn only on a failure-domain collision, so every equal-budget
policy has the same per-node load distribution. See README.md.

**Round 3 — data deleted, superseded before any result was reported.**
A determinism check (re-run the same config and compare every recorded field)
failed, and diagnosing it exposed two defects:

1. *Irreproducible seeding.* Condition and system RNGs were seeded from Python's
   built-in `hash()` of a string, which is salted per process
   (`PYTHONHASHSEED`). A run was therefore not reproducible across invocations
   even with a fixed seed list. Replaced with `zlib.crc32`, which is stable
   forever.
2. *Systems did not face the same realised failure.* A single RNG was created per
   condition and then advanced inside the loop over systems, so B0 got one random
   churn draw, B1 the next, and so on. Pairing was therefore by seed (same world,
   same workload) but not by failure realisation, which is weaker than what the
   design claims. Fixed by deriving a dedicated `failure_seed` per (seed,
   condition) and handing every system a fresh RNG from it, so all systems face
   an identical failure — except the white-box attack, which is necessarily
   computed against each system's own placement.

Both fixes are verified: `src/` now passes a determinism check (a re-run
reproduces every recorded field exactly) and an explicit assertion that the
random and correlated failure masks are byte-identical across systems while the
white-box masks differ.
