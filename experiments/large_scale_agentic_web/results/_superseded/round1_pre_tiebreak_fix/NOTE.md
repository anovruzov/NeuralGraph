# Superseded pilot runs

These runs were produced before two corrections and are kept only for the audit
trail.  They are **not** used in `RESULTS.md`, in any table, or in any figure.

1. **Tie-breaking bias.** Aggregation resolved ties by `argmax` over the value
   code, and code 0 is the claim's true value -- so every tie resolved toward
   the correct answer.  This handed all systems free accuracy and handed more of
   it to systems whose support counts are small integers (lineage aggregation,
   which counts distinct origins rather than replicas).  Replaced by a
   per-(claim, code) jitter drawn once per world and shared by every system.
2. **Unbounded efficiency ratios.** Redundancy and communication efficiency were
   computed with an epsilon-guarded denominator, making B0 (which publishes
   nothing and sends no messages) appear infinitely efficient.  Now reported as
   undefined.

An earlier pilot also revealed a placement bug that was fixed before these runs:
lineage placement cycled over failure domains, putting an equal number of
replicas in every domain regardless of size, which created hot-spot hosts in
small domains that the white-box attacker exploited.  See README.md.
