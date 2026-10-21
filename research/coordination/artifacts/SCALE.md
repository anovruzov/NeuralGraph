# Scale and generalization sweep (seed 20260813)

`scale-generalization-v1` · 16 capability shapes × 2 repair policies × 3 interventions = 96 points

## Survival depends on independence, not on scale

| repair policy | intervention | independent roots | survival | invariant across K and H | mean verifications |
|---|---|---|---|---|---|
| `lineage_aware` | `authorization_revocation` | I1 | 0.00 | yes | 7.8 |
| `lineage_aware` | `authorization_revocation` | I2 | 1.00 | yes | 5.2 |
| `lineage_aware` | `lineage_root_failure` | I1 | 0.00 | yes | 7.8 |
| `lineage_aware` | `lineage_root_failure` | I2 | 1.00 | yes | 5.2 |
| `lineage_aware` | `node_failure` | I1 | 0.00 | yes | 7.8 |
| `lineage_aware` | `node_failure` | I2 | 1.00 | yes | 5.2 |
| `source_count` | `authorization_revocation` | I1 | 0.00 | yes | 7.8 |
| `source_count` | `authorization_revocation` | I2 | 1.00 | yes | 6.2 |
| `source_count` | `lineage_root_failure` | I1 | 0.00 | yes | 7.8 |
| `source_count` | `lineage_root_failure` | I2 | 1.00 | yes | 6.2 |
| `source_count` | `node_failure` | I1 | 1.00 | yes | 6.2 |
| `source_count` | `node_failure` | I2 | 1.00 | yes | 6.2 |

## Raising replica count under correlated lineage

| policy · holders per premise · 1 root | survival vs lineage-root failure |
|---|---|
| `lineage_aware::H2::I1` | 0.00 |
| `lineage_aware::H3::I1` | 0.00 |
| `source_count::H2::I1` | 0.00 |
| `source_count::H3::I1` | 0.00 |

## Cost grows with capability arity

| premises | mean bytes moved | mean records | mean route size | baselines correct |
|---|---|---|---|---|
| 2 | 6600 | 5.0 | 3.0 | yes |
| 3 | 9111 | 7.5 | 4.0 | yes |
| 5 | 14133 | 12.5 | 6.0 | yes |
| 8 | 21666 | 20.0 | 9.0 | yes |
