# Mycelic vNext — architecture

_The design as frozen for the final rerun. Every mechanism named here is implemented in `research/mycelic/{systems,ops,calibrator,runner}.py` and exercised by the benchmark; nothing described is a proposal unless marked as such._

![topology](fig_topology.svg)

## 1. Topology and node responsibilities

The org chart is the topology: USER → TEAM → DEPT → SITE → REGION → ENTERPRISE kernel. vNext keeps it, not for branding but because the loss accounting found no stage where the levels themselves lose gold: the losses were in the kernel's policies. The level-marginal experiment (E3b) in the main report is the evidence that a level can be removed only at a cost in routing precision.

| node | holds | computes | sends up | answers down |
|---|---|---|---|---|
| USER | its own raw records (never leave) and its local claim index | local extraction to claims; targeted reads from its own index (metered as index lookup + matched records) | claims (knowledge objects) with lineage | the records that match (entity, target predicates); nothing when a strict read matches nothing |
| TEAM / DEPT | knowledge objects of its subtree; anchor index (optionally a Bloom filter) | merge, dedupe by signature, adaptive abstraction | merged objects, entity index | routes a question to the children whose index holds the entity |
| SITE | the site sketch: per (entity, predicate) a support-thresholded bit (support ≥ 2 witnesses), total mentions, first/last time | sketch construction (no model call) | sketch entries (≤ 4,000 per site) + counts | routes down; is the seed of a descent when the kernel's triage marks it foreign for the entity |
| REGION | merged sketch of its sites | bit union with per-site provenance | merged sketch | routing |
| KERNEL | the pool of claims it has been sent, the merged enterprise sketch, the question ledger, the candidate list, the register | triage, question policy, synthesis, verification, ranking | — | the questions |

## 2. Data structures

```
SketchEntry(site, entity) = (total_mentions, bitmask over predicates with support>=2, t0, t1)
                           # + weak mask (support 1) when sketch_weak_bits is on (OFF in vNext)
KO (knowledge object)     = (pred, anchor, polarity, tmin, tmax, sigs: set[witness signature],
                             branches: {level: set[node]}, n_raw, importance, q_tag, neg_tmax, pos_tmax)
Hypothesis                = (anchor, chain, preds[], links[] (per-link support, regions, lag),
                             conf, verified, contra, n_indep, feat: {45 kernel-side features})
Question                  = (anchor, target_preds[], expected_gain, cost_est, branch_hint,
                             well_targeted, answered, n_new_evidence)
Register                  = top-K hypotheses by conf, K = min(6000, max(600, n_entities))
Ledgers (batched metering)= route_ledger[node] -> #questions routed this round;
                             read_ledger[user] -> [(index_size, matched, anchor)]
```

## 3. Messages

**Upward.** Claims (KOs) carry a predicate, an entity, a polarity, a time interval, the set of witness signatures and the branch lineage — never the record text. Sketch entries carry counts and bits. The benchmark's exposure metrics count the upward pass: raw text leaving a node is 0.0 and the fraction of claims leaving their node on that pass is unchanged by vNext (0.138 at 10k), because no change touched what moves up. Descent returns are a second channel the counters do not observe: a queried user answers with per-user claim objects, and the wider budget roughly doubles the objects the kernel holds (about 30k → 53k at 10k, 61k → 127k at 50k). Extending the counter to that channel is queued.

**Downward.** A question names an entity and, optionally, target predicates; it is routed by the kernel's triage (foreign sites for the entity, home site avoided), then by each node's anchor index, with per-parent quotas (descent fan-out 3; 5/5/5/8 children per level). A strict read (the lean arm) returns only the target predicates; the default soft read falls back to every record on the entity when nothing matches.

## 4. Routing

```
descend(anchor, target_preds, budget_nodes, start_nodes, avoid):
    frontier = start_nodes or [root]              # triage seeds: the entity's foreign sites
    for level in SITE, DEPT, TEAM, USER:
        children = [c for n in frontier for c in index_children(n, anchor)] - avoid
        rank children by (index hit count, importance prior); keep per-parent quota
        route_ledger[n] += 1 for each parent n          # one routing call per node per round
        frontier = kept children
    for u in frontier at USER:
        sel = u.index[anchor]; if target_preds: sel = sel[pred in target_preds] (strict) or fallback
        read_ledger[u].append(len(sel)); return claims(sel) with lineage
flush_descent_ledger(): meter one call per routed node and one read per queried user
```

## 5. Question policy

```
question_round(hyps, pool):
    Q = []
    for h in hyps by conf desc:                          # 1. hypothesis-driven questions
        missing = chain predicates adjacent to h.preds not in h.preds
        Q += Question(h.anchor, missing, gain=f(conf, n_indep, contra), well_targeted=True)
    for entity in merged sketch:                         # 2. sketch triage (no model call)
        foreign = sites holding <= 35% of the entity's mentions
        if |foreign| < 2 or |regions(foreign)| < 2: continue
        span, chain = longest single-chain run in OR(foreign bits)
        if span < 2: continue
        gain = (0.6*span + 0.35*|regions| + 0.3*[unseen]) / (1 + 0.25*log1p(total))
        Q += Question(entity, target_preds=[] | chain preds (lean), gain, seeds=foreign, avoid=home)
    Q.sort(by gain / cost); nq = min(4000, max(220, 0.65 * |Q|))        # frozen budget
    for q in Q[:nq]: pool += descend(q.anchor, q.target_preds, fanout=3, q.seeds, q.avoid)
    flush_descent_ledger()
    hyps = synthesize(pool, link_time='hybrid'); annotate_anchor_context(hyps); apply_ranker(hyps)
```
The budget is a fraction of the triage queue so it scales with the number of entities the sketch flags (about 30× more at 50k than at 2k). 0.65 was chosen because the paired sweep saturates there.

## 6. Candidate generation (kernel)

```
synthesize(pool):
    group claims by (entity after entity-check, predicate)
    for entity, for each causal chain with >= 2 of its predicates present:
        links = chain-ordered predicates; drop links below min support (dedup check)
        # hybrid timing (vNext): cluster each link's claims into <= 3-day windows;
        # a link with a cluster of >= 2 independent witnesses gets ONE state at that
        # cluster's time; a link whose witnesses disagree gets one state per cluster
        states = [(link, t_cluster, w = log1p(support) + 0.35)]
        path = heaviest chain-ordered, time-ordered path over states (t_j <= t_i + 3)
        if staleness (retracted after asserted): drop
        emit Hypothesis(entity, chain, path[:synth_depth], features)
    hallucinations at the tier's rate; anchor-level unverified lump when the causal check fails
```

## 7. Verification and ranking

Four checks with tier-dependent pass probabilities (causal, temporal, dedup, entity), the optional evidence verification round of `J_mycelic_verified` (which writes the attribution feature), then the ranker: `k = #candidates with hand_conf ≥ 0.5`; candidates are sorted by the learned probability `p`; the top k get `conf = 0.5 + 0.5p`, the rest `0.5p`; the register keeps the top K by conf. The ranker is a logistic (l2 and interaction set chosen leave-one-seed-out on the calibration seeds) over 45 features the kernel already holds; the depth-2 boosted trees lost to it every time and were not adopted.

## 8. Privacy boundaries

- Raw records: user node only. Re-extraction (rejected) would have re-read them locally; nothing proposed moves them.
- Claims: leave the user node as (predicate, entity, polarity, interval, witness signatures, lineage). This is the benchmark's `claim_exposure_fraction`.
- Sketch entries: counts and bits; no entity text beyond the entity id the enterprise already shares.
- Ranker features: every feature is a function of the claims and sketch the kernel already holds; the anchor-context features are kernel aggregates (how many candidates share the entity, its triage gain), not per-user data.
- No centralisation is hidden in vNext: the controls that centralise (A2, B4, Y) are run as such and labelled. What the kernel does hold is per-user claim objects returned by descents, and vNext doubles them; the exposure counters do not yet count that channel (section 3).

## 9. Model allocation, caching, complexity, failure handling

- **Allocation**: back-loaded tiers (small models at USER/TEAM extraction, frontier at the kernel) as in v1; E3 in the main report is the ablation.
- **Caching / batching**: routing and user reads are metered once per node per round (the ledgers); the kernel re-reads its pool once per synthesis. Decisions and per-record tokens are identical to unbatched metering — that is why calls fall ~65–70% while compute rises with the budget.
- **Complexity**: triage is O(entities in sketch) integer work; questions O(nq × fan-out × levels) routing calls; synthesis O(pool + Σ states²) with ≤ ~15 states per (entity, chain); ranking O(candidates × 45). The kernel reads its whole pool in one call — 1.3–1.5M tokens at 10k and 3.2–3.4M at 50k in the frozen configuration, above the modelled tier's 1M context, which the simulator enforces only for the flat controls; a chunked kernel read is queued before any deployment claim.
- **Failure handling**: unavailable branches and malicious nodes are E5 in the main report (unchanged by vNext); the lean arm's failure mode — the sketch names the wrong chain for ~16% of gold entities and a strict read then returns nothing — is why it is a separate arm and not the default.

![loop](fig_loop.svg)

## 10. What is proposed but not implemented

Register-forecast budgeting with fan-out 1 at 50k, the sketch-corroboration ranker feature, budget-aware weak sketch bits, and a pairwise ranking objective — see the ranked queue in `NEXT_RESEARCH_REPORT.md`.
