# Synthetic workforce data

Everything under `data/` is **fully synthetic**. It is produced by
`src/mycelic_bench/world.py` (`generate_world`) from the configuration in
`configs/organization.yaml` and the tier blocks of `configs/experiments.yaml`,
using only a seed. **No Mercor production data, no real worker, customer,
prompt, model output or evaluation record of any organisation is read,
sampled, paraphrased or reproduced.** Names of model families (Atlas,
Borealis, ...), workers (`W000123`), teams (`T0017`) and every text field are
generated; the "sensitive" markers are deliberately planted canary tokens
whose only purpose is to be counted when they leak.

## Files

| path | content |
|---|---|
| `synthetic_workforce/<tier>_seed<k>.jsonl.gz` | one interaction record per line (schema below) |
| `ground_truth/<tier>_seed<k>.json` | the hidden effects and the per-layer evidence distribution used by `evaluate.py` |
| `generators/generate.py` | the CLI that writes both (`python data/generators/generate.py --tier tier1 --seed 0 --out data/`) |

The committed sample is `tier1_seed0` (1,000 workers, 10,000 interactions,
30 rounds). Tier 2 (10,000 workers, 100,000 interactions) and tier 3 (50,000
workers) files are generated on demand; a tier-2 file is ~10x the tier-1
size. The experiment runners never read these files: they regenerate the
identical world from the same tier, seed and overrides (the `dataset_sha256`
in the ground-truth file equals the one in every manifest under
`results/raw/`), so the files exist for inspection, external tooling and
auditing, not as an input.

## Record schema (`synthetic_workforce/*.jsonl.gz`)

Each line is a JSON object produced by `World.record(i)` plus two bookkeeping
fields:

| field | type | meaning |
|---|---|---|
| `interaction_id` | int | index of the record in the world (what batches refer to as `idx`) |
| `worker_id` | str | `W` + 6 digits |
| `team_id`, `department_id`, `region_id` | str | `T0017`, `D003`, `R01`; the tree is worker -> team -> department -> region -> executive |
| `task_id` | int | task instance id; copies of a record (duplicator workers) share it |
| `attributes` | object | the nine structured task attributes (below), exact values |
| `prompt` | str | rendered task prompt (contains the canary when the record is sensitive) |
| `model_response` | str | rendered model output (`<family>-<version> response to task N`) |
| `worker_score` | float | 0-10, `10 - 2.5 x (#true labels) + noise`, clipped |
| `error_labels` | list[str] | the labels the **worker observed** (after the worker's recall / false-label noise and team bias), not the true labels |
| `rationale` | str | free text naming the attributes the worker mentioned (`mention_prob` of the worker type), the observed errors, the score, the canary note and, under prompt-injection attacks, the payload |
| `confidence` | float | worker confidence in [0, 1] with the worker type's bias |
| `timestamp` | str | ISO-8601, derived from `round` |
| `round` | int | simulated day, 0 .. n_rounds-1 |
| `sensitive` | bool | record carries a canary |
| `sensitive_kind` | str or null | one of the canary kinds below |

The true label mask, the generative label probabilities (`p_true`) and the
effects are **not** in the record file (they are ground truth, kept in
`ground_truth/`); systems under test only ever see the record view.

### Attributes (`vocab.py`)

| attribute | values |
|---|---|
| `model_family` | Atlas, Borealis, Cirrus, Delta, Ember, Fjord, Granite, Helix |
| `model_version` | v1, v2, v3, v4 (v2/v3/v4 are released at 25 / 50 / 75 % of the rounds) |
| `task_family` | math_reasoning, coding, tool_use, factuality, instruction_following, long_context, vision_language, data_analysis, agentic_workflow, safety, hallucination_detection, retrieval, formatting, latency_sensitive |
| `context_len` | short, medium, long, very_long |
| `input_format` | prose, table, code, json, image, multi_turn, spreadsheet |
| `domain` | general, finance, medical, legal, science, engineering, customer_support |
| `language` | en, es, zh, de, ja, fr |
| `difficulty` | easy, medium, hard |
| `tool` | none, calculator, search, code_interpreter, browser, database |

Error labels (18): arithmetic_error, unit_conversion_error, table_misread,
hallucinated_fact, hallucinated_citation, wrong_tool_call,
tool_output_ignored, format_violation, instruction_ignored, truncated_output,
context_loss, unsafe_compliance, over_refusal, stale_knowledge,
retrieval_miss, off_by_one, timeout, visual_misgrounding.

### Task families and specialisation

The 14 `task_family` values are the "task families" of the benchmark. Each
team draws a Dirichlet-perturbed specialisation mixture over task_family,
input_format, model_family, context_len and tool; departments are clusters
of teams with correlated mixtures and regions add language / domain bias.
This is what spreads the evidence for a 3- or 4-way conjunction thinly across
many teams while its lower-order marginals sit in different specialist teams
(DESIGN.md §3.2), and it is why some effects are only discoverable above the
team layer.

### Workforce quality

Worker types (`configs/organization.yaml`): expert, average, weak,
overconfident, underconfident, noisy, duplicator (copies a teammate's
rationale and labels; the copy shares the original's evidence fingerprint),
adversarial (0 % by default; attack experiments add malicious workers at run
time and never change the files here). A configured fraction of teams shares
a correlated label bias.

## Sensitivity canaries

`org.sensitive_fraction` (8 % by default) of the records are marked
sensitive with one of nine kinds: `pii`, `employee_name`, `customer_id`,
`internal_secret`, `code_snippet`, `credential`, `financial`, `hr`,
`unreleased_product`. Each inserts one unique token of the form
`CANARY-<kind>-<hex8>` into the `prompt` and the `rationale` of that record.
The tokens are the whole privacy instrument of the benchmark:

* **raw leakage** = fraction of canaries found verbatim in any artifact that
  crosses a boundary (promoted upward, sent to a cloud classifier, stored
  centrally);
* **re-identifiability** = fraction of promoted higher-order sketch cells
  with fewer than `k_anonymity` records.

Under the default DLP policy (`quote_policy: none`) no text leaves a device,
so a Mycelic run must expose zero canaries; centralized baselines ship every
record and therefore expose 100 % of them by construction. The canaries are
random hex strings, not real identifiers.

## Ground-truth schema (`ground_truth/*.json`)

```
tier, seed, overrides, generator, git_commit, generated_at, provenance,
dataset_sha256, n_interactions, n_rounds,
release_round: {v2: r, v3: r, v4: r},
config: {org: {...}, world: {...}},
summary: ground_truth_summary(world)   # counts by effect kind and minimum layer, worker-type counts, ...
effects: [ Effect.to_dict(), ... ]
```

Each effect (`Effect.to_dict()`):

| field | meaning |
|---|---|
| `effect_id`, `kind` | `local`, `cross_team`, `global`, `temporal`, `contradiction`, `decoy` (decoys have delta 0 and exist so precision is measurable) |
| `cell`, `cell_desc` | the conjunction of attribute values (flat cell id + `{attribute: value}`) |
| `label`, `delta`, `sign` | error label whose probability is raised by `delta` under the cell |
| `valid_from`, `valid_to` | round window (temporal revisions flip at version-release rounds) |
| `regions` | region-conditional effects (contradiction pairs where both sides are true) |
| `group`, `conditional`, `true_side`, `shape`, `phase` | contradiction / revision group bookkeeping |
| `scope_layer`, `scope_unit` | effects confined to one team / department |
| `order`, `min_layer`, `n_min` | cell order; the **computed** lowest layer at which some unit has enough matching records to detect the effect at the configured power; the required sample size |
| `unit_counts`, `contributing_units` | per-layer evidence distribution (max matching count per unit; units with >= 1 match) |
| `true_independent_support` | the correlation-discounted count of independent sources per layer (what lineage-aware aggregation should recover) |

## Regenerating

```
PYTHONPATH=src python3 data/generators/generate.py --tier tier1 --seed 0 --out data/
PYTHONPATH=src python3 data/generators/generate.py --tier tier2 --seed 0 --seeds 3 --out /somewhere/large
PYTHONPATH=src python3 data/generators/generate.py --tier tier1 --seed 0 --set org.n_workers=300 --out /tmp/small
```

Generation is deterministic in (tier, seed, overrides, code version); the
`git_commit` and `dataset_sha256` fields in the ground-truth file say which.
