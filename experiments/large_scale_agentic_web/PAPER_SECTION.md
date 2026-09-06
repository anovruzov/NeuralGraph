## Large-Scale Agentic Web Stress Test

**Design.** We stress-test lineage-first knowledge distribution on a simulated
agentic web of N ∈ {10^2, 10^3, 10^4, 10^5} agents
organised into teams, failure domains, organisations and regions. Knowledge is
partial and local: every evidence item has a home agent, and each item descends
from exactly one origin observation. Family sizes are heavy-tailed and ~35% of
claims have a single origin, so replica count and independent evidential support
come apart by construction. We evaluate 127,374 labelled questions per seed
(single-hop, multi-hop, temporal, revision-sensitive, reconstruction,
contradiction, evidence-verification, strategic ranking) against eight systems:
isolated memory, a centralized store, full replication, random replication,
gossip, a diversity-blind replica-count policy, the lineage-aware fabric, and the
fabric with continual questioning. Equal-budget systems publish the identical
number of replicas per claim (4); every probe attempt is charged as a
message, including attempts on dead hosts. Failures escalate from 10–90% random
churn to correlated loss of whole failure domains, organisations and regions, to a
**white-box adversary** that knows each system's own placement and greedily
removes the hosts of its scarcest surviving independent evidence, to partitions
with independent updates, to corruption of up to 20% of nodes or of origin
sources. 30 seeds, paired by seed, Holm-corrected.

**Result.** Under the white-box attack at N = 10,000 removing half the agents,
knowledge survival is statistically indistinguishable across equal-budget
policies (0.679 lineage, 0.678 random, 0.677 diversity-blind), but the
lineage fabric is better on every provenance-sensitive quantity: task accuracy
+0.010
(d_z = 2.29, Holm p <1e-6),
independent-support survival +0.046
(d_z = 21.30), and contradiction F1
+0.155. The gap widens under origin-source
corruption, where a corrupted origin's false value is inherited by all of its
copies: accuracy +0.019
(Holm p <1e-6). Full replication survives better
(0.870) at 194 replicas per claim — 48× the storage.
The frontier (Fig. 2) therefore reads: at matched budget the three equal-budget
policies coincide on survival and separate only on evidential quality, and the
margin that broad replication holds over all of them is bought with two orders of
magnitude more storage rather than with a better use of it.

**Questioning ablation.** At *identical storage* — the base budget is reduced by
exactly the replicas questioning adds — continual questioning raises accuracy by
+0.189
(d_z = 37.16, Holm p <1e-6)
for a 1.81×
increase in messages, and drives post-partition reconciliation
(+0.314 accuracy after reconnection).

**Negative findings.** The same questioning machinery on a *count-based* system
gains +0.245: continual discovery is
largely orthogonal to lineage, not a consequence of it. Under corruption,
re-verification can pull the false value from a corrupt origin
(+0.165 accuracy). Lineage awareness does
not improve raw survival under random churn
(+0.000), cannot help the ~35% of claims
with a single origin, and the placement × aggregation factorial shows that
essentially all of the benefit comes from *which evidence is published*, not from
lineage-weighted voting.

---

### Table 1 (main results)

**Table 1. Knowledge survival and downstream accuracy under four failure conditions at N = 10,000 (30 seeds, paired).** KS is the fraction of valid knowledge that is both recoverable and answered correctly; ISS is the fraction of original independent evidence paths that survive in the system's own stored set; storage counts published replicas per claim and messages count every publication, question and probe attempt. B3, B5, B6 and B7 share an identical storage budget; B1, B2 and B4 do not, and their cost columns show what their margin is bought with. 95% intervals and all remaining conditions are in the appendix.

| failure condition | system | KS | accuracy | ISS | contr. F1 | storage/claim | msgs/claim |
|---|---|---|---|---|---|---|---|
| 50% random churn | B1 | 0.730 | 0.499 | 0.833 | 0.781 | 36.328 | 42.475 |
| 50% random churn | B2 | 0.876 | 0.605 | 1.000 | 0.900 | 193.749 | 209.462 |
| 50% random churn | B3 | 0.820 | 0.562 | 0.622 | 0.459 | 4.000 | 8.000 |
| 50% random churn | B4 | 0.864 | 0.586 | 0.960 | 0.896 | 48.437 | 99.588 |
| 50% random churn | B5 | 0.820 | 0.551 | 0.530 | 0.000 | 4.000 | 8.000 |
| 50% random churn | B6 | 0.821 | 0.574 | 0.686 | 0.638 | 4.000 | 8.000 |
| 50% random churn | B7 | 0.881 | 0.763 | 0.686 | 0.486 | 4.000 | 14.517 |
| 50% loss, whole organisations | B1 | 0.789 | 0.539 | 0.900 | 0.843 | 36.328 | 41.998 |
| 50% loss, whole organisations | B2 | 0.877 | 0.605 | 1.000 | 0.899 | 193.749 | 209.472 |
| 50% loss, whole organisations | B3 | 0.820 | 0.561 | 0.622 | 0.462 | 4.000 | 8.000 |
| 50% loss, whole organisations | B4 | 0.787 | 0.494 | 0.750 | 0.772 | 48.437 | 102.565 |
| 50% loss, whole organisations | B5 | 0.820 | 0.550 | 0.530 | 0.000 | 4.000 | 8.000 |
| 50% loss, whole organisations | B6 | 0.822 | 0.575 | 0.687 | 0.639 | 4.000 | 8.000 |
| 50% loss, whole organisations | B7 | 0.883 | 0.764 | 0.687 | 0.489 | 4.000 | 14.517 |
| 50% targeted lineage attack | B1 | 0.876 | 0.600 | 1.000 | 0.937 | 36.328 | 41.286 |
| 50% targeted lineage attack | B2 | 0.870 | 0.597 | 0.993 | 0.895 | 193.749 | 210.089 |
| 50% targeted lineage attack | B3 | 0.678 | 0.441 | 0.504 | 0.351 | 4.000 | 8.000 |
| 50% targeted lineage attack | B4 | 0.749 | 0.472 | 0.691 | 0.747 | 48.437 | 105.090 |
| 50% targeted lineage attack | B5 | 0.677 | 0.429 | 0.437 | 0.000 | 4.000 | 8.000 |
| 50% targeted lineage attack | B6 | 0.679 | 0.451 | 0.550 | 0.506 | 4.000 | 8.000 |
| 50% targeted lineage attack | B7 | 0.726 | 0.594 | 0.550 | 0.398 | 4.000 | 14.517 |
| 20% origin-source corruption | B1 | 0.675 | 0.431 | 0.933 | 0.870 | 36.328 | 41.763 |
| 20% origin-source corruption | B2 | 0.719 | 0.464 | 1.000 | 0.894 | 193.749 | 205.168 |
| 20% origin-source corruption | B3 | 0.702 | 0.456 | 0.706 | 0.628 | 4.000 | 8.000 |
| 20% origin-source corruption | B4 | 0.719 | 0.461 | 0.994 | 0.901 | 48.437 | 96.845 |
| 20% origin-source corruption | B5 | 0.695 | 0.444 | 0.561 | 0.000 | 4.000 | 8.000 |
| 20% origin-source corruption | B6 | 0.717 | 0.475 | 0.810 | 0.827 | 4.000 | 8.000 |
| 20% origin-source corruption | B7 | 0.770 | 0.640 | 0.810 | 0.794 | 4.000 | 14.517 |

### Figure captions

**Figure 1. Knowledge survival and task accuracy as failure escalates (N = 10,000, 30 seeds, bands are 95% CIs).** Columns are three failure structures at matched node-level severity: uniform random churn, correlated loss of whole organisations, and a white-box adversary that knows each system's placement and removes the hosts of its scarcest surviving independent evidence first. Axes span the full [0, 1] range. The equal-budget policies (B3, B5, B6) separate on accuracy rather than on availability; the systems that stay flat at extreme severity (B2, B4) do so at one to two orders of magnitude more storage, quantified in Figure 2.

**Figure 2. Resilience–efficiency frontier under the 50% white-box attack (N = 10,000).** Points are the eight systems; dashed lines sweep the storage budget k ∈ {1, 2, 4, 8, 16, 32} for the three equal-budget policies, so the comparison does not depend on one arbitrary budget. Left: knowledge survival against storage cost. Right: against storage plus communication. Error bars are 95% CIs; the x-axis is logarithmic because the systems differ by two orders of magnitude in cost.

### Appendix table

**Table A1. Storage-budget sweep under the 50% white-box attack (N = 10,000, 15 seeds).** Each equal-budget policy is run at k ∈ {1, 2, 4, 8, 16, 32} published replicas per claim. Knowledge survival converges as k grows — availability is set by replica count — while independent-support survival does not: the diversity-blind policy saturates well below the lineage policy at every budget, because additional replicas of one origin add no independent support.

| system | budget k | storage/claim | msgs/claim | KS | accuracy | ISS |
|---|---|---|---|---|---|---|
| B3 | 1 | 1.000 | 2.000 | 0.205 | 0.115 | 0.133 |
| B3 | 2 | 2.000 | 4.000 | 0.435 | 0.261 | 0.297 |
| B3 | 4 | 4.000 | 8.000 | 0.679 | 0.442 | 0.504 |
| B3 | 8 | 8.000 | 16.000 | 0.829 | 0.567 | 0.689 |
| B3 | 16 | 16.000 | 30.936 | 0.875 | 0.606 | 0.813 |
| B3 | 32 | 32.000 | 49.350 | 0.877 | 0.606 | 0.892 |
| B5 | 1 | 1.000 | 2.000 | 0.207 | 0.113 | 0.133 |
| B5 | 2 | 2.000 | 4.000 | 0.438 | 0.256 | 0.283 |
| B5 | 4 | 4.000 | 8.000 | 0.677 | 0.430 | 0.437 |
| B5 | 8 | 8.000 | 16.000 | 0.826 | 0.557 | 0.533 |
| B5 | 16 | 16.000 | 30.976 | 0.872 | 0.598 | 0.563 |
| B5 | 32 | 32.000 | 49.691 | 0.876 | 0.602 | 0.566 |
| B6 | 1 | 1.000 | 2.000 | 0.205 | 0.119 | 0.133 |
| B6 | 2 | 2.000 | 4.000 | 0.438 | 0.270 | 0.315 |
| B6 | 4 | 4.000 | 8.000 | 0.679 | 0.452 | 0.550 |
| B6 | 8 | 8.000 | 16.000 | 0.829 | 0.578 | 0.773 |
| B6 | 16 | 16.000 | 30.961 | 0.873 | 0.616 | 0.918 |
| B6 | 32 | 32.000 | 49.618 | 0.876 | 0.618 | 0.982 |

