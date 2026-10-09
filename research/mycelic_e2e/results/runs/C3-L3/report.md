# Mycelic-E2E run report

split **dev** - size **L** - seed **3** - contract v1. Every row carries its provider label; accuracy is correct / ALL tasks of the frozen set with a 95 % Wilson interval.

| run | provider | gate | n | correct | accuracy | 95% CI | disclosures | decoy accepted | latency p50 / p95 (s) | model calls |
|---|---|---|---:|---:|---:|---|---:|---:|---|---:|
| system (C3-L3) | deterministic-provider | valid | 120 | 120 | 100.0% | [96.9%, 100.0%] | 0 | 0.0% | 7.0 / 13.0 | 14290 |
| baseline (source, primary) (C3-L3-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 119 | 99.2% | [95.4%, 99.9%] | 0 | 0.0% | 0.06 / 12.35 | 237 |
| baseline (single, secondary) (C3-L3-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 109 | 90.8% | [84.3%, 94.8%] | 0 | 0.0% | 0.06 / 12.38 | 237 |

## Accuracy per task class (correct / n)

| class | system | baseline (source, primary) | baseline (single, secondary) |
|---|---:|---:|---:|
| coincidence | 10/10 | 10/10 | 10/10 |
| common_origin_copies | 7/7 | 7/7 | 7/7 |
| common_origin_pos | 8/8 | 8/8 | 8/8 |
| contradiction | 10/10 | 9/10 | 9/10 |
| cross_domain | 40/40 | 40/40 | 30/40 |
| cross_tenant | 5/5 | 5/5 | 5/5 |
| denied | 10/10 | 10/10 | 10/10 |
| fault | 10/10 | 10/10 | 10/10 |
| single_domain | 10/10 | 10/10 | 10/10 |
| temporal | 10/10 | 10/10 | 10/10 |

## Architecture gate and counts

### system (C3-L3) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (3031 live documents across 518 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (518 distinct holder files, 10130 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (522 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (1508/1508 questions routed; rank_method {'domains': 351, 'hypergraph': 1157}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 14822 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (1615 claims, 902 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 14290})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (1615 claims, 2796 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 402 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 374/374 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (902 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 3044; 12 deleted strings checked)
- counts: holders_created=10648; holders_with_records=417; holders_activated=319; holders_routed=482; routes=14822; records_ingested=3043; applied_events_outcomes={'delete': 12, 'new': 3043, 'update': 12}; claims_by_status={'contested': 410, 'hypothesis': 683, 'supported': 522}; hyperedges_by_kind={'conflict': 205, 'discovery': 902, 'lineage': 1615, 'support': 1615}

### baseline (source, primary) (C3-L3-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (C3-L3-baseline-single) - gate: n/a (baseline has no coordinator)
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
