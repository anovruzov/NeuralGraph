# Large-Scale Agentic Web Stress Test

Decisive experiment for *3D Lineage-First Mycelial Fabric — Continual Discovery*
(NeurIPS 2026 Workshop on Agentic Web).

The question this suite is built to answer, adversarially:

> When thousands of agents continuously create, question, revise, lose and
> reconstruct knowledge, does **lineage-aware** knowledge distribution preserve
> useful knowledge under failure more efficiently than replication-based
> alternatives — and where does it stop helping?

The suite is designed to *attack* that hypothesis. Baselines are given every
structural advantage that is defensible, the adversary is white-box, storage
budgets are matched, and every condition in which the proposed architecture
loses is enumerated in `tables/table8_negative_results.csv` and in `RESULTS.md`.

---

## Reproduce

```bash
./run_all.sh              # N = 1e2, 1e3, 1e4 + every ablation + analysis
FULL=1 ./run_all.sh       # also runs N = 1e5   (~1 h, ~6 GB peak RSS)
QUICK=1 ./run_all.sh      # smoke run, < 1 min, checks the pipeline end to end
```

Dependencies: `numpy`, `scipy`, `matplotlib` (`requirements.txt`). No network
access, no API keys, no GPU. Deterministic given the seed list in `seeds.json`.

Outputs:

| path | contents |
|---|---|
| `results/<experiment_id>/rows.csv[.gz]` | one row per (seed, condition, system) — raw, never overwritten (gzipped for version control; the analysis reads either form) |
| `results/<experiment_id>/manifest.json` | git commit, full config, seeds, environment, runtime |
| `results/index.jsonl` | append-only index mapping logical run names to directories |
| `results/summary.json` | every number quoted in `RESULTS.md` |
| `tables/`, `figures/` | derived tables and publication figures |

---

## What is simulated, and what is not

**This is a simulation. There are no LLM calls anywhere in this experiment.**
Semantic reasoning is abstracted: a claim's value is a symbol, and an answer is
correct iff it equals the claim's current true value. This is a deliberate
scope decision — the hypothesis under test is about *evidence topology under
failure*, which is a property of the network and the provenance graph, not of
any language model — and it is also a hard constraint of the environment this
was run in (no model credentials were available). The consequences are stated
plainly in `RESULTS.md` under *Limitations*: nothing here measures whether an
LLM agent can actually formulate a good question, extract evidence from text, or
recognise that two differently-worded claims are the same claim. Every result
below is a claim about the distribution layer, not about end-to-end agents.

What *is* simulated at full fidelity: the network topology, the provenance
graph including correlated copies, placement policies, message accounting,
failure and adversary models, revision and staleness, corruption propagation
along lineage, partitions and reconciliation, and the retrieval protocol.

---

## Design

### World (`src/world.py`)

`N` agents in a heterogeneous topology: agent → team → failure domain →
organisation, with regions cutting across organisations, plus a knowledge
specialty per agent. Knowledge is **not** globally available: each evidence
item has a home agent, and derived copies cluster near their origin.

A knowledge object is `K = (c, E, L, t)`:

* `c` — the claim, whose current true value may have been **revised** at some point in time;
* `E` — evidence items, each held by a home agent;
* `L` — lineage: every item descends from exactly one **origin source** (a lineage root);
* `t` — each origin observed the world at some time, so origins that observed
  before a revision carry the stale value, and so do all of their descendants.

Family sizes are heavy-tailed, so a minority of origins account for most of the
apparent support. Two designated claim classes isolate the section-7 contrast:

* `correlated` — 1 origin, 100 derived copies (`e0 → e1..e100`);
* `independent` — 3 origins, 1 copy each.

~35% of claims have only a single origin: a regime where lineage awareness
*cannot* help by construction, kept in the population on purpose.

### Systems (`src/systems.py`, `src/questioning.py`)

Every agent keeps its own local memory. A *system* decides what to additionally
publish into the fabric and where; its retrievable index is exactly its own
placement set. Cost is measured as published replicas (storage) plus the
messages used to publish and to probe them.

| id | placement | probe order | aggregation |
|---|---|---|---|
| B0 | none (local memory only) | — | count |
| B1 | central cluster, 3 mirrors in distinct regions | random | count |
| B2 | every item to 16 random agents | random | count |
| B3 | k replicas/claim, items drawn uniformly, random hosts | random | count |
| B4 | every item to 4 peers in the home's locality (gossip) | random | count |
| B5 | k replicas/claim from the **largest** lineage family | most-corroborated first | count |
| B6 | k replicas/claim, round-robin over **distinct origins**, hosts uniform subject to distinct failure domains | provenance-diverse first | lineage |
| B7 | B6 + continual questioning | provenance-diverse first | lineage |

Ablation cells: `B3L` (random placement + lineage aggregation), `B6C` (lineage
placement + count aggregation), `B2L`, and `B3Q` (**questioning on a
count-based system**, which tests whether continual discovery is lineage-specific
at all). `B3Q` is not a lineage-free system — re-verifying a stored replica
requires knowing which origin produced it, so the provenance pointer remains.
What it removes is lineage-aware *placement* and lineage-aware *aggregation*.

Two design decisions exist specifically to avoid rigging the comparison:

1. **Identical confidence formula for every system.** `conf = (support_max /
   support_total) · (1 − 2^−support_max)`. The only difference is what
   `support` counts: retrieved replicas (count) or distinct lineage roots
   (lineage). No system gets a tuned confidence curve.
2. **Load-matched placement.** B6 draws hosts uniformly over the whole network
   and *redraws* only on a failure-domain collision. An earlier version cycled
   over failure domains, which put an equal number of replicas in every domain
   regardless of its size and created hot-spot agents in small domains; the
   white-box attacker then destroyed the lineage system for reasons that had
   nothing to do with lineage (KS 0.44 vs 0.72 for random replication). With
   uniform-load placement the two have identical load distributions.

Contradiction detection uses the *same* prediction rule for all systems ("did
the evidence I retrieved disagree?"), so differences come only from what each
system holds and probes.

### Retrieval

A querier walks its system's ordered candidate list for a claim and fetches
until it has `max_probe = 8` successes or the list is exhausted. **Every
attempt costs a message, including attempts on dead hosts** — so broad
replication buys availability and pays for it in probe traffic. Outside the
partition regime all live agents are mutually reachable, which is a *conservative*
assumption for the hypothesis: it maximises the benefit of having replicas
anywhere at all, favouring B2 and B4.

### Continual discovery (`src/questioning.py`)

Questions are budgeted and each one costs messages (and, unless storage is
equalised, storage):

* **REVERIFY** — "has this fact changed since it was last verified?" Re-contacts
  the origins of the claim's stored roots. *If the origin is corrupt,
  re-verification installs the false value* — questioning can make things worse,
  and that is measured. 10% of re-verifications fail outright.
* **ADD_SUPPORT** — "which independent source confirms this?" / "which dependency
  is unknown?" Publishes a replica from an origin not yet covered.
* **RESOLVE** — "why does A report x and B report y?" Re-verifies and adds one
  independent freshly-verified replica.

Targeting strategies: `strategic` (importance × lineage fragility), `temporal`
(importance × age of the oldest stored observation), `hybrid`, `random`, and a
flooding regime. With `question_equalize_storage=true` (the default) the base
placement budget is reduced by exactly the number of replicas questioning adds,
so **B7 is compared with B6 at identical storage**.

### Failure regimes (`src/failures.py`)

* `random` churn at 0/10/30/50/70/90%;
* `correlated_domain`, `correlated_org`, `correlated_region` — whole failure
  domains, organisations, or regions disappear, trimmed to the exact same
  severity as the random regime;
* `targeted_world` — a system-agnostic attacker removes the hosts of the world's
  scarcest independent evidence for the most important claims (one identical
  node set for every system, so architectures are comparable);
* `targeted_whitebox` — a greedy attacker that **knows the system's own
  placement** and removes, in 8 recomputed rounds, the hosts whose loss destroys
  the most independent support. Recomputed per system, so no architecture is
  attacked with another's weak spots. This is the hardest condition in the suite;
* `partition` — the network splits into 2 or 4 components, facts are updated
  independently inside them, and the network reconnects; measured both *during*
  the split and *after reconnection*;
* corruption at 1/5/10/20% — coordinated node corruption (all corrupt agents
  assert the *same* false value, the worst case for majority voting),
  uncoordinated node corruption, and **origin-source corruption**, where every
  derived copy of a corrupted origin inherits the false value.

### Workload (`src/workload.py`)

Category labels are assigned once and stored; they are never re-derived, so a
question's category cannot drift between systems or conditions.

`single_hop`, `multi_hop` (2–3 hop conjunctions), `temporal`,
`revision_sensitive` (the hard subset: claims where most of the world's evidence
is still pre-revision), `reconstruction` (claims whose evidence neighbourhood
was destroyed by the failure), `contradiction` (binary, P/R/F1),
`evidence_verification` (binary: ≥2 *independent* sources), `strategic`
(ranking: "which claims have the weakest independent support?").

Headline accuracy is the **macro average over the five answer-producing
categories only**. `evidence_verification` is excluded from it and reported
separately, because replica-counting systems cannot represent the
replica/independent-source distinction at all — winning that category is
tautological for a lineage system and would inflate the headline number.

### Metrics (`src/workload.py`, `src/stats.py`)

Knowledge survival (recoverable *and correct*), availability (recoverable at
all), accuracy conditional on answering, task accuracy per category,
independent-support survival (ISS), retrieval rounds (a `T_R` proxy in logical
rounds, not wall-clock), redundancy efficiency (KS / storage per claim),
communication efficiency (recoveries / message), contradiction P/R/F1,
**contradiction resolution** (accuracy on claims whose evidence genuinely
disagrees), partition **reconciliation rate**, ECE, **ECE after held-out
histogram recalibration**, and confidence AUROC.

Two of these need a word of explanation. Raw ECE partly reflects the arbitrary
scale of a confidence formula, so the recalibrated variant and the
calibration-free AUROC are reported alongside it. And contradiction *detection*
and *resolution* move in opposite directions for a questioning system by
construction: re-verification removes disagreement from the fabric, so there is
less left to detect even as more contradictory claims end up answered correctly.
Reporting only one of the two would be misleading, so both are reported
everywhere.

### Statistics

30 seeds at N ≤ 10^4 (10 at N = 10^5, reported as such); 15 seeds for the
ablation sweeps. Every headline comparison is **paired by seed** — same world,
same workload, same failure draw for every system. Reported: mean, sd, 95%
t-interval, bootstrap CI of the paired difference, Cohen's d_z, paired t-test
and Wilcoxon signed-rank, with Holm–Bonferroni correction over each metric's
comparison family.

---

## Layout

```
configs/    declared experiment grids (one JSON per scale / ablation)
src/        world, systems, questioning, failures, evaluation, workload,
            statistics, figures, runner, analysis
results/    raw append-only outputs + index.jsonl + summary.json
tables/     derived CSV tables (table1..table8 + full appendix grid)
figures/    publication figures (png + pdf)
logs/       run logs
seeds.json  the fixed seed list
environment.txt  dependency and machine versions of the last run
RESULTS.md  the full report
RESULTS.pdf full report rendered, with all figures
PAPER_SECTION.md  ~500-word section, long form, with captions
SECTION_INSERT.pdf  the same result compressed to ONE camera-ready page
MycelialFabric_with_stress_test.pdf  the paper with that page spliced in before
            the references (body becomes 10pp; trim one page to return to the
            9-page limit)
```

## Figure design

Nine systems is more colour than any categorical palette can carry: validated
against the six-check colour formula with `--pairs all`, seven or more chromatic
slots fail CVD separation outright. The figures therefore facet by *role*. The
four equal-budget policies the paper's claim is actually about (`B3`, `B5`, `B6`,
`B7`) get four hues — `#0059A0 #C24400 #3D8FC8 #0F7A5A`, which pass lightness
band, chroma floor, all-pairs CVD separation, normal-vision floor and 3:1
contrast — and the systems that serve as cost or performance envelopes are drawn
as neutral reference lines with distinct dash patterns and direct labels, so
identity never rests on colour alone. Survival and accuracy axes always span the
full [0, 1]; nothing is truncated.

The seed-to-seed spread is far narrower than that axis, so a single marker per
system would hide the distribution. Figure 2 and the insert figure therefore plot
**every run as its own point** on a zoomed companion panel — 180-210 dots — which
is what makes it visible that the three equal-budget policies genuinely coincide
while questioning separates.
