# Mycelic-E2E run report

split **dev** - size **S** - seed **1** - contract v1. Every row carries its provider label; accuracy is correct / ALL tasks of the frozen set with a 95 % Wilson interval.

| run | provider | gate | n | correct | accuracy | 95% CI | disclosures | decoy accepted | latency p50 / p95 (s) | model calls |
|---|---|---|---:|---:|---:|---|---:|---:|---|---:|
| system (C1-S1) | deterministic-provider | valid | 120 | 118 | 98.3% | [94.1%, 99.5%] | 0 | 0.0% | 2.0 / 5.0 | 15274 |
| system (C4-S1) | deterministic-provider | valid | 120 | 103 | 85.8% | [78.5%, 91.0%] | 0 | 0.0% | 7.0 / 21.3 | 21075 |
| system (C6-S1) | deterministic-provider | valid | 120 | 96 | 80.0% | [72.0%, 86.2%] | 1039 | 0.0% | 5.0 / 7.0 | 35576 |
| system (smoke-cap40) | deterministic-provider | NOT RUN | 120 | 87 | 72.5% | [63.9%, 79.7%] | 0 | 0.0% | 3.0 / 13.55 | 14789 |
| baseline (source, primary) (C1-S1-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 117 | 97.5% | [92.9%, 99.1%] | 0 | 0.0% | 0.02 / 0.04 | 231 |
| baseline (source, primary) (C4-S1-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 116 | 96.7% | [91.7%, 98.7%] | 0 | 0.0% | 0.03 / 0.05 | 228 |
| baseline (source, primary) (C6-S1-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 116 | 96.7% | [91.7%, 98.7%] | 0 | 0.0% | 0.03 / 0.05 | 228 |
| baseline (source, primary) (S1-baseline-source) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 102 | 85.0% | [77.5%, 90.3%] | 0 | 1.7% | 0.02 / 0.04 | 232 |
| baseline (single, secondary) (C1-S1-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 109 | 90.8% | [84.3%, 94.8%] | 0 | 0.0% | 0.02 / 0.03 | 231 |
| baseline (single, secondary) (C4-S1-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 69 | 57.5% | [48.6%, 66.0%] | 0 | 0.0% | 0.03 / 0.04 | 228 |
| baseline (single, secondary) (C6-S1-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 69 | 57.5% | [48.6%, 66.0%] | 0 | 0.0% | 0.03 / 0.06 | 228 |
| baseline (single, secondary) (S1-baseline-single) | deterministic-provider | n/a (baseline has no coordinator) | 120 | 102 | 85.0% | [77.5%, 90.3%] | 0 | 1.7% | 0.02 / 0.03 | 232 |
| S1 | - | - | - | - | - | unscorable: ScoreError: 120 views carry no tenant_id (e.g. ['dev-000', 'dev-001', 'dev-002']): the cross-tenant check cannot run; the runner must record the asker's tenant id | - | - | - | - |
| ablation A1 (C6abl-A1-S1) | deterministic-provider | valid | 120 | 57 | 47.5% | [38.8%, 56.4%] | 344 | 0.0% | 3.0 / 4.0 | 23898 |
| ablation A2 (C6abl-A2-S1) | deterministic-provider | INVALID: G3 | 120 | 96 | 80.0% | [72.0%, 86.2%] | 1094 | 2.5% | 5.0 / 7.0 | 35522 |
| ablation A3 (C6abl-A3-S1) | deterministic-provider | valid | 120 | 120 | 100.0% | [96.9%, 100.0%] | 0 | 0.0% | 1.0 / 2.0 | 6743 |
| ablation A4 (C6abl-A4-S1) | deterministic-provider | valid | 120 | 77 | 64.2% | [55.3%, 72.2%] | 2712 | 0.0% | 6.0 / 9.0 | 51018 |

## Accuracy per task class (correct / n)

| class | system | system | system | system | baseline (source, primary) | baseline (source, primary) | baseline (source, primary) | baseline (source, primary) | baseline (single, secondary) | baseline (single, secondary) | baseline (single, secondary) | baseline (single, secondary) | ablation A1 | ablation A2 | ablation A3 | ablation A4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| coincidence | 10/10 | 10/10 | 3/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 3/10 | 10/10 | 1/10 |
| common_origin_copies | 7/7 | 7/7 | 2/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 6/7 | 2/7 | 7/7 | 0/7 |
| common_origin_pos | 8/8 | 8/8 | 8/8 | 4/8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 1/8 | 8/8 | 8/8 | 8/8 |
| contradiction | 10/10 | 10/10 | 10/10 | 6/10 | 10/10 | 10/10 | 10/10 | 7/10 | 10/10 | 10/10 | 10/10 | 7/10 | 2/10 | 10/10 | 10/10 | 10/10 |
| cross_domain | 38/40 | 28/40 | 40/40 | 23/40 | 38/40 | 37/40 | 37/40 | 28/40 | 30/40 | 0/40 | 0/40 | 28/40 | 16/40 | 40/40 | 40/40 | 40/40 |
| cross_tenant | 5/5 | 5/5 | 4/5 | 5/5 | 5/5 | 5/5 | 5/5 | 3/5 | 5/5 | 5/5 | 5/5 | 3/5 | 4/5 | 4/5 | 5/5 | 1/5 |
| denied | 10/10 | 10/10 | 7/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 | 7/10 | 10/10 | 5/10 |
| fault | 10/10 | 5/10 | 10/10 | 7/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 0/10 | 0/10 | 10/10 | 2/10 | 10/10 | 10/10 | 10/10 |
| single_domain | 10/10 | 10/10 | 2/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 7/10 | 2/10 | 10/10 | 0/10 |
| temporal | 10/10 | 10/10 | 10/10 | 5/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 0/10 | 10/10 | 10/10 | 2/10 |

## Architecture gate and counts

### system (C1-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1001 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (171 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (1320/1320 questions routed; rank_method {'domains': 49, 'hypergraph': 1271}; as-of replay denials 0 (current domains differ from route-time domains on 275 of 12946 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (1685 claims, 906 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 15274})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (1685 claims, 6822 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 402 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 101/101 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (906 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 41; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=117; holders_activated=101; holders_routed=127; routes=12946; records_ingested=1013; applied_events_outcomes={'delete': 12, 'new': 1013, 'update': 12}; claims_by_status={'contested': 1221, 'hypothesis': 293, 'supported': 171}; hyperedges_by_kind={'conflict': 988, 'discovery': 906, 'lineage': 1685, 'support': 1685}

### system (C4-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (142 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (1708/1708 questions routed; rank_method {'hypergraph': 1702, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 16753 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (1683 claims, 855 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 21075})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (1683 claims, 18168 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (855 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=111; holders_routed=125; routes=16753; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'contested': 1420, 'hypothesis': 121, 'supported': 142}; hyperedges_by_kind={'conflict': 1690, 'discovery': 855, 'lineage': 1683, 'support': 1683}

### system (C6-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (1500 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (2516/2516 questions routed; rank_method {'hypergraph': 2510, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 24824 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (6220 claims, 1807 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 35576})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (6220 claims, 31980 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (1807 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=111; holders_routed=120; routes=24824; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'contested': 3495, 'hypothesis': 1225, 'supported': 1500}; hyperedges_by_kind={'conflict': 3672, 'discovery': 1807, 'lineage': 6220, 'support': 6220}

### system (smoke-cap40) - gate: NOT RUN
- no gate report for this run

### baseline (source, primary) (C1-S1-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (source, primary) (C4-S1-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (source, primary) (C6-S1-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (source, primary) (S1-baseline-source) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (C1-S1-baseline-single) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (C4-S1-baseline-single) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (C6-S1-baseline-single) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### baseline (single, secondary) (S1-baseline-single) - gate: n/a (baseline has no coordinator)
- no gate report for this run

### ablation A1 (C6abl-A1-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (718 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (1919/1919 questions routed; rank_method {'ablated': 1919}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 18854 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (4108 claims, 1732 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 23898})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (4108 claims, 10423 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (1732 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=79; holders_routed=100; routes=18854; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'contested': 1118, 'hypothesis': 2272, 'supported': 718}; hyperedges_by_kind={'conflict': 770, 'discovery': 1732, 'lineage': 4108, 'support': 4108}

### ablation A2 (C6abl-A2-S1) - gate: INVALID: G3
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **fail** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (1607 supported claims)
    - claim_b3f9c971260547d1bafc: stored independent_roots 5 != recomputed 4
    - claim_c6c4f8774116466abcfa: recomputed independent roots 1 < policy 2
- G4 **pass** - routing is audited, authorized, within budget and ranked (2504/2504 questions routed; rank_method {'hypergraph': 2498, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 24704 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (6290 claims, 1815 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 35522})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (6290 claims, 32059 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (1815 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=111; holders_routed=120; routes=24704; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'contested': 3522, 'hypothesis': 1161, 'supported': 1607}; hyperedges_by_kind={'conflict': 3639, 'discovery': 1815, 'lineage': 6290, 'support': 6290}

### ablation A3 (C6abl-A3-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (974 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (500/500 questions routed; rank_method {'hypergraph': 495, 'domains': 5}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 4860 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (1412 claims, 495 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 6743})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (1412 claims, 2659 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (495 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 56; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=101; holders_routed=115; routes=4860; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'hypothesis': 438, 'supported': 974}; hyperedges_by_kind={'discovery': 495, 'lineage': 1412, 'support': 1412}

### ablation A4 (C6abl-A4-S1) - gate: valid
- G1 **pass** - every live record came through the connector path (ingest_records + applied_events + benchmark connector) (1536 live documents across 136 holder files)
- G2 **pass** - holder stores are distinct and records live in the right holder (136 distinct holder files, 0 registered holders without a file)
- G3 **pass** - every supported claim has independent roots >= policy, resolvable root-known refs, and a matching hypergraph support edge (1566 supported claims)
- G4 **pass** - routing is audited, authorized, within budget and ranked (3799/3799 questions routed; rank_method {'domains': 3258, 'hypergraph': 541}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 37654 routes))
- G5 **pass** - every claim has a claim.gate audit row and a revision; every discovery is idempotent (8714 claims, 3001 discoveries)
- G6 **pass** - all model usage names the declared provider and the run carries its label (label='deterministic-provider', providers={'fake': 51018})
- G7 **pass** - claims, discoveries and evidence were produced by the system, not written by the harness (8714 claims, 40009 evidence refs traced to holder responses)
- G8 **pass** - hypergraph consistent; entity and term indexes hold exactly what holders may publish (privacy) and cover it (entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holders (100%), 0 violating)
- G9 **pass** - lineage resolves for every discovery: discovery -> claims -> refs -> holders (and question/response) (3001 discoveries)
- G10 **pass** - fault dispositions: duplicates, malformed lines, deletions, replay and restart are visible in the stores (injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings checked)
- counts: holders_created=136; holders_with_records=136; holders_activated=110; holders_routed=125; routes=37654; records_ingested=1548; applied_events_outcomes={'delete': 12, 'new': 1548, 'update': 12}; claims_by_status={'contested': 4253, 'hypothesis': 2895, 'supported': 1566}; hyperedges_by_kind={'conflict': 3677, 'discovery': 3001, 'lineage': 8714, 'support': 8714}

## Per-class detail (primary run)

```
split=dev mode=system ablation=None provider=deterministic-provider n=120
accuracy = 118/120 = 0.983  95% Wilson [0.941, 0.995]  errors=0
disclosures = 0 (foreign refs 0, forbidden markers 0, raw open 0, raw unchecked 0)
class                      n  ok    acc   CI
coincidence               10  10  1.000   [0.72, 1.00]
common_origin_copies       7   7  1.000   [0.65, 1.00]
common_origin_pos          8   8  1.000   [0.68, 1.00]
contradiction             10  10  1.000   [0.72, 1.00]
cross_domain              40  38  0.950   [0.83, 0.99]
cross_tenant               5   5  1.000   [0.57, 1.00]
denied                    10  10  1.000   [0.72, 1.00]
fault                     10  10  1.000   [0.72, 1.00]
single_domain             10  10  1.000   [0.72, 1.00]
temporal                  10  10  1.000   [0.72, 1.00]
```
