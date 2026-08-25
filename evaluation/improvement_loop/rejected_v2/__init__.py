"""Rejected experiment: the v2 global temporal abstention gate.

Preserved verbatim, unreachable from the production evaluation path.

Adjudicated over all 60 frozen validation questions
(`evaluation/artifacts/locomo_loop/V2_ROOT_CAUSE.md`):

* Its stated mechanism did nothing: **0** temporal false abstentions recovered,
  because the Qwen baseline abstained on 0 of 15 temporal questions. The 8
  development examples that motivated it came from the recorded GPT-4o-era
  pipeline, whose abstention behaviour does not transfer to this answerer.
* Judged accuracy rose 24 -> 29 (+0.083), but the deterministic metrics moved
  the other way (item recall -0.029, item F1 -0.014), McNemar p = 0.18 and the
  paired bootstrap CI [-0.033, +0.200] spans zero.
* Any gain is attributable to an *unintended* second mechanism the patch
  bundled in: global chronological re-ordering, which changed evidence order on
  60/60 questions and landed almost entirely on multi_hop (+0.267) while
  costing single_hop (-0.133).

Rejected because a change whose stated mechanism provably did not fire, whose
real effect is an unrelated global re-ordering, and whose significance test
fails, is not an accepted result regardless of the headline number.
"""
