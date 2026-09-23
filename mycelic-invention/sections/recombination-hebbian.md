## Stochastic recombination and Hebbian consolidation

The sections above describe how a candidate is generated once. This section
describes the regime the system actually runs in: each agent performing thousands
of cheap combinatorial trials per cycle, agents seeding each other, and the graph
itself changing under the results so that the swarm's second week is not a repeat
of its first.

### 5.1 The economics that force stochastic search

The combination space is not searchable by enumeration. For an activated frontier
of `n` concepts and operators of arity 2 and 3, the candidate space is on the
order of `n²` and `n³`; with a frontier in the low thousands — which is a small
frontier — the arity-3 space is already beyond exhaustive evaluation by any
process that costs a model call per candidate.

Two consequences follow, and they determine the whole design of this stage.

**Generation must be orders of magnitude cheaper than verification.** A trial
that costs a model call cannot be run ten thousand times per agent per cycle. So
the inner loop is not a model call. It is a *symbolic* operation over typed
nodes: sample a small set of concepts from the frontier, check the operator's
precondition (a type and constraint check, Section 6), and emit a structure or
fail. Most trials fail on the precondition and cost almost nothing. The model is
invoked only on the survivors of the cheap filter, and the adversarial panel
(Section 7) — by far the most expensive stage — sees only a small fraction of
those.

**The sampler cannot be uniform.** Uniform sampling over the frontier spends
almost all its trials inside single domains, because that is where most of the
mass is, and intra-domain combinations are the ones the domain's own literature
has already explored. The sampler is therefore biased toward *domain distance*:
the probability of drawing a set is weighted by how many distinct domain labels
it spans and by how weakly the drawn concepts are already connected in the graph.
Sampling a pair that already has a strong direct edge is close to worthless — the
connection is known — so the sampler explicitly down-weights it.

```
procedure RECOMBINE(frontier F, operators O, trials T):
    emitted ← ∅
    for t in 1..T:                                  # T ≈ 10³–10⁴ per agent-cycle
        k  ← sample_arity()                         # 2 or 3
        S  ← sample_concepts(F, k)                  # biased: high domain spread,
                                                    #   low existing connectivity
        op ← sample_operator(O, types(S))
        if not precondition(op, S): continue         # cheap type/constraint check
        c  ← apply(op, S)                            # symbolic, no model call
        if cheap_score(c) < θ_keep: continue         # Section 6 filter
        emitted ← emitted ∪ {c}
    return emitted                                   # typically |emitted| ≪ T
```

`T` is set per agent-cycle by the budget model of Section 5; the ratio
`|emitted| / T` is itself a monitored quantity, because a ratio that climbs
toward 1 means `θ_keep` has gone slack and the expensive stages are about to be
flooded, and a ratio near 0 means the frontier has been exhausted and the agent
should be moved.

We state plainly that the overwhelming majority of emitted structures are
nonsense. That is the intended operating point. The cheap filter is not a
judgment of merit; it is a device for keeping the expensive stages solvent.

### 5.2 Cross-agent reference

Agents do not search in isolation, and they do not share raw state. Sharing raw
frontiers would violate the Mycelic rule that bounded, typed, lineage-carrying
artifacts move rather than stores (Section 9), and it would also collapse the
diversity the swarm exists to maintain.

Instead each agent periodically publishes a **co-activation digest**: a bounded,
typed summary of which concepts fired together in its trials, with counts, the
domains spanned, and no raw text. Digests are readable by other agents and are
used in two ways.

*As additional activation seeds.* An agent whose frontier has gone cold can seed
from another agent's digest, entering a region of the graph it would not have
reached from its own starting problem.

*As chain completion.* The genuinely interesting case is a **partial candidate**:
an agent emits a structure whose operator precondition is satisfied except for
one missing element — a mechanism that needs a material with a property no
concept in that agent's frontier has. The partial is published with its unmet
requirement stated as a typed query. Another agent, working in a different domain
entirely, may hold a concept that satisfies it. The completed candidate's lineage
records both agents and both activation traces, and this is the common case for
the cross-domain results the architecture is built to find: neither agent could
have produced the candidate alone, and the lineage graph shows that as a fact
about the derivation rather than as a claim.

This is the agent-level analogue of the organisational property in Section 9, and
it is verified the same way: by inspecting which units the supporting evidence
came from, not by asserting that collaboration occurred.

### 5.3 Hebbian consolidation

If the graph's edge weights never change, the swarm's ten-thousandth cycle
samples from exactly the same distribution as its first, and nothing has been
learned. We therefore make the substrate adaptive under a Hebbian rule: concepts
that repeatedly fire together in *productive* trials strengthen the association
between them, and the strengthened association changes future activation
propagation (Section 3) and future sampling bias.

The critical design question is what counts as productive, and getting it wrong
makes the system worse than a static graph.

**The naive rule is actively harmful.** Reinforcing on co-activation alone —
strengthening an edge whenever two concepts appear together in an emitted
candidate — reinforces whatever the sampler already favours. Within a few cycles
the strengthened edges attract more activation, which produces more
co-activations along them, which strengthens them further. The result is a
rich-get-richer collapse: the swarm converges on a small, self-reinforcing
neighbourhood, generates variations of the same idea, and reports high internal
productivity while its actual coverage of the graph has fallen off a cliff. This
is the same pathology the diversity pressure of Section 5 exists to prevent, and
a naive Hebbian rule reintroduces it directly into the substrate.

**The reinforcement signal is the verification outcome, not generation.** An edge
is strengthened when a co-activation contributed to a candidate that *survived*
the adversarial panel, weighted by how hard the challenge was. Generation earns
nothing. This makes the learned structure expensive to acquire, which is
appropriate: it should take real evidence to change the substrate.

For an edge `e` and cycle `t`:

```
w(e, t+1) = clip( (1 − λ)·w(e, t)  +  η·Σ_c [ r(c) · χ(e, c) ]  ,  w_min, w_max )

  λ      decay per cycle — an association not re-earned fades
  η      learning rate
  c      ranges over candidates resolved this cycle
  χ(e,c) 1 if e was traversed in c's activation trace, else 0
  r(c)   reinforcement:
           + survived the panel unchallenged        → strong positive
           + survived with a challenge overturned   → positive
           0 never reached the panel                → none
           − killed by the feasibility challenger   → negative
           − killed as anticipated or obvious       → negative
```

Four guards are not optional:

1. **Decay (`λ`).** Every association fades unless re-earned. Without decay the
   graph only ever accumulates, and early accidents become permanent structure.
2. **Weight ceiling (`w_max`) and normalisation over a node's incident edges.** A
   node's outgoing weight is normalised so that strengthening one association
   costs another. This makes consolidation competitive and bounds hub formation
   directly, complementing the fan-out penalty and inhibition already in the
   activation rule (Section 3).
3. **Anti-Hebbian updates on refuted candidates.** This is where the negative
   knowledge of Section 4 becomes *learned* rather than merely stored. When a
   combination is killed — anticipated by a patent, refuted on physics, judged
   obvious — the traversed edges are weakened. The swarm stops re-proposing
   combinations that have already been shot down, which is otherwise one of the
   most irritating and expensive failure modes of a generate-and-test system. The
   refutation is also written back as a failure-mode node, so the knowledge is
   available both as structure (weights) and as content (a node with the
   conditions of failure).
4. **A protected exploration fraction.** A fixed fraction of each agent's trials
   samples from the *unweighted* graph, ignoring learned weights entirely. This
   is the floor that prevents consolidation from ever fully determining where the
   swarm looks, and it is what allows a region abandoned early — possibly for a
   bad reason — to be revisited.

### 5.4 What must be measured, and what would show this is not working

Consolidation is the component of this architecture most likely to produce an
impressive-looking failure, because a collapsing swarm and a converging swarm
look similar from the inside: both show rising candidate yield per cycle. The
following are therefore monitored quantities, and Section 9 specifies how they
are computed.

- **Frontier coverage over time** — the fraction of the graph reachable at
  non-trivial activation, per cycle. A monotone decline is the signature of
  collapse, and it is the primary alarm.
- **Domain-spread distribution of emitted candidates** — if consolidation is
  working, spread should hold or widen; if it is collapsing, spread narrows while
  yield rises.
- **Survival rate by edge age** — whether candidates traversing recently
  strengthened edges actually survive verification at a higher rate than those
  traversing unweighted edges. If they do not, consolidation is learning noise,
  and the honest response is to set `η` to zero and report that the mechanism did
  not work.
- **Re-proposal rate of previously refuted combinations** — the direct test of
  whether anti-Hebbian updating is doing its job.

We note the obvious risk of circularity: the panel's verdicts train the substrate,
and the substrate then shapes what the panel sees. A panel with a systematic bias
will, under this rule, have that bias amplified into the graph. The countermeasure
is the calibration procedure of Section 7 — periodically re-running resolved
candidates through a panel with no access to the consolidated weights — and the
protected exploration fraction above. We do not claim these are sufficient. We
claim they are the places to look first when the system starts confidently
producing similar ideas.
