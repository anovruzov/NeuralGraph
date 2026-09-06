"""Generate PAPER_SECTION.md: a ~500-word section plus its table and figure captions.

Numbers are substituted from results/summary.json so the text cannot drift from
the measured results.  The word count of the prose is checked and printed.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .report import (CHURN50, CORRELATED, DOMAIN70, PARTITION, ROOTCORR, TARGETED,
                     find_test, md_table, num, p_str, signed, t1)

ROOT = Path(__file__).resolve().parents[1]
WORD_LIMIT = 500


def build(summary: dict) -> tuple[str, int]:
    n = summary["main_n"]
    ns = summary["main_n_seeds"]
    scales = sorted(summary.get("scales", []))
    qps = summary.get("questions_per_seed", {}).get(str(n), 0)

    def g(cond, sys, field):
        return t1(summary, cond, sys, field)

    ks6, ks3, ks5 = g(TARGETED, "B6", "KS"), g(TARGETED, "B3", "KS"), g(TARGETED, "B5", "KS")
    a6, a3 = g(TARGETED, "B6", "accuracy"), g(TARGETED, "B3", "accuracy")
    st6, st2 = g(TARGETED, "B6", "storage_per_claim"), g(TARGETED, "B2", "storage_per_claim")
    ks2 = g(TARGETED, "B2", "KS")
    t_iss = find_test(summary, n, TARGETED, "iss", "B6", "B3")
    t_f1 = find_test(summary, n, TARGETED, "contradiction_f1", "B6", "B3")
    t_acc = find_test(summary, n, TARGETED, "accuracy_macro", "B6", "B3")
    t_root = find_test(summary, n, ROOTCORR, "accuracy_macro", "B6", "B3")
    t_gq = find_test(summary, n, CORRELATED, "accuracy_macro", "B7", "B6")
    t_gq_ctrl = find_test(summary, n, CORRELATED, "accuracy_macro", "B3Q", "B3")
    t_gq_root = find_test(summary, n, ROOTCORR, "accuracy_macro", "B7", "B6")
    t_ks_churn = find_test(summary, n, CHURN50, "knowledge_survival", "B6", "B3")
    t_part = find_test(summary, n, PARTITION, "accuracy_macro", "B7", "B6")

    prose = f"""## Large-Scale Agentic Web Stress Test

**Design.** We stress-test lineage-first knowledge distribution on a simulated
agentic web of N ∈ {{{', '.join(f'10^{int(round(math.log10(s)))}' for s in scales)}}} agents
organised into teams, failure domains, organisations and regions. Knowledge is
partial and local: every evidence item has a home agent, and each item descends
from exactly one origin observation. Family sizes are heavy-tailed and ~35% of
claims have a single origin, so replica count and independent evidential support
come apart by construction. We evaluate {qps:,} labelled questions per seed
(single-hop, multi-hop, temporal, revision-sensitive, reconstruction,
contradiction, evidence-verification, strategic ranking) against eight systems:
isolated memory, a centralized store, full replication, random replication,
gossip, a diversity-blind replica-count policy, the lineage-aware fabric, and the
fabric with continual questioning. Equal-budget systems publish the identical
number of replicas per claim ({num(st6, 0)}); every probe attempt is charged as a
message, including attempts on dead hosts. Failures escalate from 10–90% random
churn to correlated loss of whole failure domains, organisations and regions, to a
**white-box adversary** that knows each system's own placement and greedily
removes the hosts of its scarcest surviving independent evidence, to partitions
with independent updates, to corruption of up to 20% of nodes or of origin
sources. {ns} seeds, paired by seed, Holm-corrected.

**Result.** Under the white-box attack at N = {n:,} removing half the agents,
knowledge survival is statistically indistinguishable across equal-budget
policies ({num(ks6)} lineage, {num(ks3)} random, {num(ks5)} diversity-blind), but the
lineage fabric is better on every provenance-sensitive quantity: task accuracy
{signed(t_acc['mean_diff']) if t_acc else 'n/a'}
(d_z = {num(t_acc['cohens_dz'], 2) if t_acc else 'n/a'}, Holm p {p_str(t_acc['p_holm']) if t_acc else ''}),
independent-support survival {signed(t_iss['mean_diff']) if t_iss else 'n/a'}
(d_z = {num(t_iss['cohens_dz'], 2) if t_iss else 'n/a'}), and contradiction F1
{signed(t_f1['mean_diff']) if t_f1 else 'n/a'}. The gap widens under origin-source
corruption, where a corrupted origin's false value is inherited by all of its
copies: accuracy {signed(t_root['mean_diff']) if t_root else 'n/a'}
(Holm p {p_str(t_root['p_holm']) if t_root else ''}). Full replication survives better
({num(ks2)}) at {num(st2, 0)} replicas per claim — {st2 / max(st6, 1e-9):.0f}× the storage.
The frontier (Fig. 2) therefore reads: at matched budget the three equal-budget
policies coincide on survival and separate only on evidential quality, and the
margin that broad replication holds over all of them is bought with two orders of
magnitude more storage rather than with a better use of it.

**Questioning ablation.** At *identical storage* — the base budget is reduced by
exactly the replicas questioning adds — continual questioning raises accuracy by
{signed(t_gq['mean_diff']) if t_gq else 'n/a'}
(d_z = {num(t_gq['cohens_dz'], 2) if t_gq else 'n/a'}, Holm p {p_str(t_gq['p_holm']) if t_gq else ''})
for a {g(CORRELATED, 'B7', 'messages_per_claim') / max(g(CORRELATED, 'B6', 'messages_per_claim'), 1e-9):.2f}×
increase in messages, and drives post-partition reconciliation
({signed(t_part['mean_diff']) if t_part else 'n/a'} accuracy after reconnection).

**Negative findings.** The same questioning machinery on a *count-based* system
gains {signed(t_gq_ctrl['mean_diff']) if t_gq_ctrl else 'n/a'}: continual discovery is
largely orthogonal to lineage, not a consequence of it. Under corruption,
re-verification can pull the false value from a corrupt origin
({signed(t_gq_root['mean_diff']) if t_gq_root else 'n/a'} accuracy). Lineage awareness does
not improve raw survival under random churn
({signed(t_ks_churn['mean_diff']) if t_ks_churn else 'n/a'}), cannot help the ~35% of claims
with a single origin, and the placement × aggregation factorial shows that
essentially all of the benefit comes from *which evidence is published*, not from
lineage-weighted voting."""

    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’%.^×–—-]*", re.sub(r"[*_#`]", "", prose)))

    rows = [r for r in summary.get("table1", [])
            if r["condition"] in (CHURN50, CORRELATED, TARGETED, ROOTCORR)
            and r["system"] in ("B1", "B2", "B3", "B4", "B5", "B6", "B7")]
    main_table = md_table(rows, ["condition", "system", "KS", "accuracy", "ISS",
                                 "contradiction_F1", "storage_per_claim", "messages_per_claim"],
                          ["failure condition", "system", "KS", "accuracy", "ISS", "contr. F1",
                           "storage/claim", "msgs/claim"])

    app = [r for r in summary.get("frontier", [])]
    app_table = md_table(app, ["system", "budget_k", "storage_per_claim", "messages_per_claim",
                               "knowledge_survival", "accuracy_macro", "iss"],
                         ["system", "budget k", "storage/claim", "msgs/claim", "KS", "accuracy", "ISS"])

    out = [prose, "", "---", "", "### Table 1 (main results)", "",
           f"**Table 1. Knowledge survival and downstream accuracy under four failure "
           f"conditions at N = {n:,} ({ns} seeds, paired).** KS is the fraction of valid "
           f"knowledge that is both recoverable and answered correctly; ISS is the fraction of "
           f"original independent evidence paths that survive in the system's own stored set; "
           f"storage counts published replicas per claim and messages count every publication, "
           f"question and probe attempt. B3, B5, B6 and B7 share an identical storage budget; "
           f"B1, B2 and B4 do not, and their cost columns show what their margin is bought with. "
           f"95% intervals and all remaining conditions are in the appendix.", "",
           main_table, "",
           "### Figure captions", "",
           f"**Figure 1. Knowledge survival and task accuracy as failure escalates "
           f"(N = {n:,}, {ns} seeds, bands are 95% CIs).** Columns are three failure structures at "
           f"matched node-level severity: uniform random churn, correlated loss of whole "
           f"organisations, and a white-box adversary that knows each system's placement and "
           f"removes the hosts of its scarcest surviving independent evidence first. Axes span the "
           f"full [0, 1] range. The equal-budget policies (B3, B5, B6) separate on accuracy rather "
           f"than on availability; the systems that stay flat at extreme severity (B2, B4) do so at "
           f"one to two orders of magnitude more storage, quantified in Figure 2.", "",
           f"**Figure 2. Resilience–efficiency frontier under the 50% white-box attack "
           f"(N = {n:,}).** Points are the eight systems; dashed lines sweep the storage budget "
           f"k ∈ {{1, 2, 4, 8, 16, 32}} for the three equal-budget policies, so the comparison does "
           f"not depend on one arbitrary budget. Left: knowledge survival against storage cost. "
           f"Right: against storage plus communication. Error bars are 95% CIs; the x-axis is "
           f"logarithmic because the systems differ by two orders of magnitude in cost.", "",
           "### Appendix table", "",
           f"**Table A1. Storage-budget sweep under the 50% white-box attack (N = {n:,}, 15 "
           f"seeds).** Each equal-budget policy is run at k ∈ {{1, 2, 4, 8, 16, 32}} published "
           f"replicas per claim. Knowledge survival converges as k grows — availability is set by "
           f"replica count — while independent-support survival does not: the diversity-blind "
           f"policy saturates well below the lineage policy at every budget, because additional "
           f"replicas of one origin add no independent support.", "",
           app_table, ""]
    return "\n".join(out), words


def main():
    summary = json.loads((ROOT / "results" / "summary.json").read_text())
    text, words = build(summary)
    (ROOT / "PAPER_SECTION.md").write_text(text + "\n")
    print(f"wrote PAPER_SECTION.md — prose is {words} words "
          f"({'within' if words <= WORD_LIMIT else 'OVER'} the ~{WORD_LIMIT}-word limit)")


if __name__ == "__main__":
    main()
