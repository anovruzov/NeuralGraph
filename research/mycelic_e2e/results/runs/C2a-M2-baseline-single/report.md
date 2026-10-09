# Mycelic-E2E run report

split **dev** - size **M** - seed **2** - contract v1. Every row carries its provider label; accuracy is correct / ALL tasks of the frozen set with a 95 % Wilson interval.

| run | provider | gate | n | correct | accuracy | 95% CI | disclosures | decoy accepted | latency p50 / p95 (s) | model calls |
|---|---|---|---:|---:|---:|---|---:|---:|---|---:|
| system (C2a-M2) | deterministic-provider | INVALID: G8 | 120 | 120 | 100.0% | [96.9%, 100.0%] | 0 | 0.0% | 3.0 / 4.0 | 8924 |
| baseline (source, primary) (C2a-M2-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 119 | 99.2% | [95.4%, 99.9%] | 0 | 0.0% | 0.04 / 0.47 | 237 |
| baseline (single, secondary) (C2a-M2-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 110 | 91.7% | [85.3%, 95.4%] | 0 | 0.0% | 0.04 / 0.47 | 237 |

## Accuracy per task class (correct / n)

| class | system | baseline (source, primary) | baseline (single, secondary) |
|---|---:|---:|---:|
| coincidence | 10/10 | 10/10 | 10/10 |
| common_origin_copies | 7/7 | 7/7 | 7/7 |
| common_origin_pos | 8/8 | 8/8 | 8/8 |
| contradiction | 10/10 | 10/10 | 10/10 |
| cross_domain | 40/40 | 39/40 | 30/40 |
| cross_tenant | 5/5 | 5/5 | 5/5 |
| denied | 10/10 | 10/10 | 10/10 |
| fault | 10/10 | 10/10 | 10/10 |
| single_domain | 10/10 | 10/10 | 10/10 |
| temporal | 10/10 | 10/10 | 10/10 |

## Architecture gate and counts

### system (C2a-M2) - gate: INVALID: G8
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (2241 live documents across 1120 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (1120 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (246 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (1154/1154 questions routed; rank_method {'domains': 339, 'hypergraph': 815}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 11278 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (1161 claims, 552 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 8924})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (1161 claims, 2482 evidence refs traced to holder responses)
- G8 **fail** - hypergraph present and consistent; entity index covers the publishable entities (entity coverage 0/140)
    - entity index covers 0/140 = 0.00 of the publishable entities (< 0.95)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (552 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 2255; 12 deleted strings checked)
- counts: holders_created=1120; holders_with_records=307; holders_activated=250; holders_routed=331; routes=11278; records_ingested=2253; applied_events_outcomes={'delete': 12, 'new': 2253, 'update': 12}; claims_by_status={'contested': 695, 'hypothesis': 220, 'supported': 246}; hyperedges_by_kind={'conflict': 452, 'discovery': 552, 'lineage': 1161, 'support': 1161}

### baseline (source, primary) (C2a-M2-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (C2a-M2-baseline-single) - gate: n/a (baseline has no coordinator)
- no gate report for this run

## Per-class detail (primary run)

```
split=dev mode=system ablation=None provider=deterministic-provider n=120
accuracy = 120/120 = 1.000  95% Wilson [0.969, 1.000]  errors=0
disclosures = 0 (foreign refs 0, forbidden markers 0, raw open 0, raw unchecked 0)
class                      n  ok    acc   CI
coincidence               10  10  1.000   [0.72, 1.00]
common_origin_copies       7   7  1.000   [0.65, 1.00]
common_origin_pos          8   8  1.000   [0.68, 1.00]
contradiction             10  10  1.000   [0.72, 1.00]
cross_domain              40  40  1.000   [0.91, 1.00]
cross_tenant               5   5  1.000   [0.57, 1.00]
denied                    10  10  1.000   [0.72, 1.00]
fault                     10  10  1.000   [0.72, 1.00]
single_domain             10  10  1.000   [0.72, 1.00]
temporal                  10  10  1.000   [0.72, 1.00]
```
