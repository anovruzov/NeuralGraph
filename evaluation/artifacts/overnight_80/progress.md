# Overnight LoCoMo >=80 — progress

## 2026-08-25 · Phase 0 + Phase 1 + v2 adjudication + revert

```text
Time:                       2026-08-25 (session)
Candidate:                  v1 baseline (frozen) + v2 abstention gate (adjudicated, REVERTED)
Batch IDs:                  validation eval set, digest 8077c40ba2e7cff2, all 60
Cache hits / new local / new paid: baseline 60 local + 60 judge (cached); v2 60 local + 60 judge
Tests:                      303 passed, 1 skipped, 3382 subtests (python3.11 3.11.14)
Previous score:             none (first frozen baseline)
Current score:              v1 24/60 = 0.400 ; v2 29/60 = 0.483
Improved / regressed / unchanged: real 7 / real 2 / 51
Item-F1 delta:              -0.0137 (v2 vs v1)
Unsupported-item delta:     -2 (22 -> 20)
Operational errors:         0 / 120 = 0.00%
Budget used / cap / reserve: 48 paid judge requests / 40 default cap / EXCEEDED - paid calls halted
Accepted:                   NO - v2 reverted (cea7605)
Next action:                Candidate B (completeness) blocked on paid-call authorisation
```

### Why v2 was rejected despite +0.083

Its stated mechanism never fired: 0 temporal false abstentions recovered, because
the Qwen baseline abstains on 0 of 15 temporal questions. The gain is attributable
to an unintended global chronological re-ordering (60/60 questions, rank-1 excerpt
to median position 11), concentrated in multi_hop (+0.267) while single_hop lost
0.133. McNemar p = 0.18; paired bootstrap CI [-0.033, +0.200] spans zero;
deterministic metrics moved against the judged metric.
