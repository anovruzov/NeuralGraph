# Continual Discovery — Fast Synthetic Experiment Pack

This pack is designed to produce **reproducible synthetic mechanism evidence** for the Continual Discovery extension of the 3D Lineage-First Mycelial Fabric.

It does **not** convert planned 100 / 1,000 / 10,000-agent evaluations into real-system benchmark results. The simulator outputs must be labeled **synthetic simulation** in any paper or presentation.

## What it tests

The experiment asks whether a questioning policy that is aware of lineage can acquire **independent epistemic support** more efficiently than policies that ask randomly or only when answer confidence is low.

The comparison is intentionally mechanism-focused:

- `none` — no follow-up questions.
- `random` — spends a fixed question budget on random candidate agents.
- `uncertainty` — asks only when current answer confidence is low; candidate selection is random.
- `lineage` — asks when support is lineage-concentrated and preferentially selects an unseen lineage root.
- `continual` — combines uncertainty, conflict, and lineage weakness triggers, then preferentially seeks unseen roots.

All strategies use the **same lineage-aware final aggregator**, so the main comparison isolates **question triggering / evidence acquisition**, rather than giving one strategy a better answer model.

## Why this matches the current paper

The existing measured campaign already establishes that correlated copies are not equivalent to independent support and that lineage-aware repair improves survival. This simulator extends that exact mechanism into the next question: **when should the network ask for new evidence, and whom should it ask?**

## One-command run

```bash
bash run_fast.sh
```

This runs a short sweep at 100, 1,000, and 10,000 agents, writes raw CSVs, a summary table, a LaTeX table, and three plots.

For a tiny sanity check:

```bash
bash run_smoke.sh
```

For the heavier paper sweep:

```bash
bash run_paper.sh
```

For the protocol-required sensitivity sweep (one factor at a time over the five
parameters the protocol names, ~2 minutes):

```bash
bash run_sensitivity.sh
```

To regenerate the sensitivity tables and figures without re-running the sweep:

```bash
python3 src/sensitivity_sweep.py --config configs/fast.json --out results \
  --figures figures --from-raw results/sensitivity_raw.csv
```

## Outputs

- `results/raw_runs.csv` — one row per strategy / seed / scale.
- `results/summary.csv` — mean and standard deviation by strategy / scale.
- `results/summary.md` — human-readable table.
- `results/paper_table.tex` — LaTeX-ready table with **Synthetic simulation** label.
- `figures/accuracy_vs_agents.png`
- `figures/false_consensus_vs_agents.png`
- `figures/independent_roots_per_question.png`

From the sensitivity sweep:

- `results/sensitivity_raw.csv` — one row per axis / value / seed / strategy.
- `results/sensitivity_summary.csv` — mean and standard deviation per cell.
- `results/sensitivity_comparisons.csv` — every seed-paired comparison with its verdict.
- `results/sensitivity_summary.md` — tables, verdict counts, and the negative results.
- `results/sensitivity_config.json` — the axes, base values, seeds, and claim count.
- `figures/sensitivity_<parameter>.png` — one three-panel figure per swept parameter.

## What the runs show

Headline sweep (10 seeds x 2,000 claims at each of 100 / 1,000 / 10,000 synthetic
agents) and sensitivity sweep (8 seeds x 1,500 claims per cell, 23 cells at 1,000
agents). Both are **synthetic simulation**.

- `continual` beats every baseline on accuracy in **23 of 23** sensitivity cells,
  at roughly two thirds of `random`'s question spend (about 1.9 vs 3.0 questions
  per claim at the base configuration).
- `continual` is the only policy that resolves initial epistemic gaps at a
  meaningful rate (0.26 at the base configuration, against 0.10 for `random`,
  0.01 for `uncertainty`, and 0.00 for `lineage`).
- **Negative result:** `continual` is *worse* than `random` on false confident
  consensus in 15 of 23 cells, and the gap widens as roots get more corrupt
  (0.076 vs 0.059 at `corrupt_root_prob = 0.30`). Its early stop leaves some
  wrong-but-confident claims that spending the full budget would have caught.
- **Negative result:** `lineage` alone does not improve accuracy over `random`
  (12 losses, 8 ties, 3 wins) or `uncertainty` (10 losses, 10 ties, 3 wins). Its
  value is elsewhere: it beats `uncertainty` on false confident consensus in
  23 of 23 cells, and acquires about 0.93 new roots per question against 0.23
  for random selection, at about 0.25 questions per claim.
- Both effects are flat across the three population labels, which is expected:
  the per-claim candidate coalition is bounded, so population size is a label,
  not a workload.

Read `results/sensitivity_summary.md` before quoting any of these numbers.
The narrative write-up, with the negative results and the claim boundary, is
`docs/research/CONTINUAL_DISCOVERY.md` at the repository root (rendered to PDF
alongside it).

## Recommended paper language

Safe:

> In a controlled synthetic simulation of correlated evidence lineages, we compare random, uncertainty-triggered, lineage-aware, and combined continual-questioning policies. The simulation is intended as a mechanism probe rather than a production-scale benchmark.

Unsafe:

> We validated the full system at 10,000 agents.

The simulator is deliberately separate from real repository benchmark claims.

## Scientific guardrails

1. Do not report a synthetic number without the words **simulation** or **synthetic** nearby.
2. Do not tune parameters only until lineage-aware questioning wins. Run the sensitivity sweep (`bash run_sensitivity.sh`) and report what it says.
3. Preserve negative results.
4. Keep the random seeds and full config with every reported table.
5. If a strategy loses under a regime, report it.
6. Do not merge simulator output into the measured benchmark table without a clear evidence-class column.
