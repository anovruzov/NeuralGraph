# Capability survival sweep (30 seeds from 20260813)

Mean capability survival rate per (strategy, intervention).

| strategy | node failure | memory deletion | lineage root failure | authorization revocation | route removal | edge corruption | stale knowledge | network partition | worst single domain failure | mean |
|---|---|---|---|---|---|---|---|---|---|---|
| `isolated_local` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.000** |
| `centralized` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 | **0.111** |
| `full_replication` | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | **0.667** |
| `fixed_distributed_replication` | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| `source_count_repair` | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.556** |
| `random_path_diversification` | 1.00 | 1.00 | 0.67 | 0.67 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.704** |
| `lineage_aware_repair` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 | **0.778** |
| `oracle_min_cut` | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 | **0.889** |

| strategy | survival | unrecovered silent forgetting | mean bytes | deterministic |
|---|---|---|---|---|
| `isolated_local` | 0.000 | 0.000 | 1371 | yes |
| `centralized` | 0.111 | 0.667 | 3020 | yes |
| `full_replication` | 0.667 | 0.333 | 9877 | yes |
| `fixed_distributed_replication` | 0.556 | 0.444 | 4943 | yes |
| `source_count_repair` | 0.556 | 0.444 | 4941 | yes |
| `random_path_diversification` | 0.704 | 0.296 | 5327 | NO: authorization_revocation, lineage_root_failure |
| `lineage_aware_repair` | 0.778 | 0.222 | 5448 | yes |
| `oracle_min_cut` | 0.889 | 0.111 | 6461 | yes |
