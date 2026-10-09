# Architecture audit — Mycelic implementation vs. the benchmarks that claimed to measure it

Version 1 (2026-10-09 ~06:15 UTC). Evidence sources: runtime trace `audit/TRACE_FINDINGS.md` + `audit/trace_output.json`
(ENGINEER-A, reproducible with `python research/mycelic_e2e/audit/trace_one_event.py`), custody audit
`custody/OLD_BENCHMARKS.md` (REVIEWER-1), stage map `plan/PLAN_v1.md` §A (PLANNER), and code reads by the orchestrator.
This file is updated with the architecture-gate reports of the scored runs (§4).
Version 2 (2026-10-09 ~10:36 UTC): §4 filled from the scored runs' gate reports, holdout included; §5 extended with the gaps
found overnight.

## 1. Verdict on the hypothesis

> "Current benchmarks bypass ingestion, individual NeuralGraphs, hypergraph routing, or other core runtime pieces."

**Confirmed for every published accuracy number.**

- The `research/mycelic` enterprise benchmark (E1–E12, vNext v3/v4/v5) imports no implementation code. Its holders are
  numpy rows, its routing is a random draw, and its extraction reads the ground truth. It is simulation-only.
- The live Qwen3-1.7B harness replaces only the extraction step and then runs the same simulator. It is partial-path.
- `research/ingest_bench` runs real ingestion into one holder, with no coordinator, loop or gate. It is component-only.
- The seed scenario does run the whole system, but its documents mostly bypass the connector path
  (`EvidenceStore.ingest_document`), and it checks pass/fail, not accuracy.
- LoCoMo measures a different retriever (Tesseract), which the implementation's holders do not use.

The **hypergraph** does not exist in any branch. There was nothing for a benchmark to bypass: it had to be built
(§3, WP1).

## 2. Coverage table (required component → evidence)

"Old" = the published benchmark path. "E2E v1" = the successor harness (`bench/`). The "runtime trace" and "persisted effect"
columns come from the single-event trace. They are replaced by gate counts per scored run once runs exist.

| required component | production entry point | old benchmark call path | E2E v1 call path | runtime trace (single event) | persisted effect observed | gap / status |
|---|---|---|---|---|---|---|
| raw event → adapter/normalization | `ingest/connectors/local_export.py:normalize` → `ingest/events.py:CanonicalEvent.create/compute_root` | none (simulator rows) | JSONL in holder import root → `POST /api/holders/{id}/connectors` + `/sync` | sync report `raw_items 1, enqueued 1` | `ingest_records` row with record_key, object_key, source_root_id, author entity, created_at_src | rejected lines leave no per-line disposition (DEFECT e → ENGINEER-D) |
| tenant/domain/authz/shard | `pipeline.py:_event_problems`, `acl.py:narrow`, `domains.py:DomainClassifier`, `shards.py:route_write_sync` | none | implicit in sync | domain `operations.logistics` (method llm/fake), shard s0 | `domain_memberships`, `record_locator` | same topic classified 3 ways across holders. `published_domains` needs ≥5 records per domain |
| durable idempotent ingestion | `queue.py:IngestQueue`, `pipeline.py:dedupe/_finish_sync` | none | implicit; fault files | dup/replay/out-of-order probes | `applied_events` outcomes new/update/historical/duplicate | PASS on duplicates, replay, out-of-order. Undated record looks fresh (DEFECT → ENGINEER-D) |
| the correct individual NeuralGraph | `holder/embedded.py:EmbeddedHolders.ensure` (one `EvidenceStore` = NeuralGraph `chat_memory` store per holder) | none (numpy rows) | one user holder per user, one unit holder per department/team | memory written in the owner's holder file only | `documents/messages/memories/FTS` in `<holder>.db` | NeuralGraph LLM extraction off by default. Pattern entity linking (`ingest/linking.py`) on |
| graph construction / index updates | `evidence/service.py:_write_chunks_sync`, `ingest/linking.py:link_record` | none | implicit | 3 entities (document, domain, person) | entity graph rows, relations for recognised vocabulary | entities limited to the linker's vocabulary (services, components, symptoms, tracker keys, email domains) |
| hypergraph membership & lineage | **none before tonight**. WP1: `knowledge/hypergraph.py`, migration `0005_hypergraph.sql` | none | written inside `commit_claim`, conflicts, discoveries, heartbeats | n/a (did not exist) | n/a | BUILT TONIGHT (WP1), under review |
| authorized cross-domain routing | `inquiry/service.py:candidate_holders/route` + `authz.py:can_route` | coin flip in simulator | loop questions and asker questions routed by the worker | 4 routes (authorized) | `question_routes`, `question.route` audit | **unranked `holders[:10]` in registry order** (`inquiry/service.py:369`). WP1 adds `inquiry/routing.py:rank_holders` |
| retrieval & evidence reconstruction | `evidence/service.py:answer_question` (NeuralGraph hybrid retrieval) → `inquiry/service.py:handle_response` → `knowledge` refs | none | implicit | memory retrieved (1 hit) but answer rejected | `exports` row ↔ coordinator `evidence_refs` (root, object_key) | **FIRST LOSS POINT**: deterministic `answer_from_evidence` needs ≥2 shared tokens. The loop's templated question shares 0 with a plainly worded record |
| inquiry / continual-discovery loop | `discovery/engine.py:LoopEngine.tick/route_question/collect/evaluate/commit` | none | goals + questions via API, worker drains | 2 gap questions, 4 routes, 4 responses | `questions`, `responses`, `question_runs` | a domain is never re-asked after one gap question. No candidate domains → loop idle |
| evidence / commit gate | `knowledge/gate.py:CommitGate.check`, `knowledge/support.py:compute_support` | none | implicit | "1 independent source root(s); policy needs 2" → hypothesis | `claims`, `claim_evidence`, `derivations`, `claim.gate` audit | forwards with own commentary counted as independent (DEFECT f → ENGINEER-D). Cross-department requirement absent (O3 → WP1) |
| final answer / discovery | `knowledge/service.py:create_discovery`, API reads | simulator register | asker-view dumps scored by `bench/score.py` | discovery `kind hypothesis` | `discoveries`, revisions | — |
| tenant isolation & disclosure | `authz.py` everywhere | simulated privacy counters | cross-tenant + denied-access task classes | 403/404 everywhere. Members-only content withheld | audit rows | refused asker can read opaque holder ids and rejection reasons of units it cannot see (DEFECT h2) |
| deletion / retention | `ingest/service.py:delete_record`, `knowledge/service.py:on_evidence_event` | none | delete faults | holder purge complete | tombstones, withdrawals | verbatim text survives in 7 coordinator tables after deletion (DEFECT d, open) |

## 3. Confirmed causes (evidence-backed), ordered by impact on end-to-end accuracy

1. **Routing is unranked at scale.** `QuestionService.route` keeps the first `budget.holders` (10) authorized holders in
   registry order. With ~1,000 authorized holders in scope, evidence held by 3 specific people is reached by chance.
   Code: `mycelic/inquiry/service.py:369-370`.
2. **The deterministic answer step is lexical.** A holder answers only if the question shares ≥2 stemmed content tokens
   with a retrieved chunk (`models/fake.py:answer_from_evidence`). The loop's own questions are templates naming a domain,
   so plainly worded evidence is retrieved and then dropped (trace stage 6–7). This is a property of the deterministic
   provider. All deterministic results are labelled as such.
3. **No cross-unit corroboration rule.** The gate counts independent roots but not how many departments they come from,
   so a single-department cluster passes as a cross-department finding.
4. **Common origin partly lost.** `forwarded_from` is used for the root only when the origin is a local record.
   Forwards with commentary in different holders count as independent support.
5. **Rejections are silent.** Malformed or refused input leaves only a counter. There is no per-item disposition.

## 4. Gate reports of scored runs

The table below is generated from each run's `arch_gate.json` (`bench/arch_gate.py`; copies in `results/runs/<run>/` for dev
runs). For the holdout run only the gate verdicts and aggregate counts are shown.

What the gates establish for a valid run:
- **G1.** Every live document came through the connector path into a holder file.
- **G2.** One store per registered holder, with no shared files.
- **G3.** Support and source roots are consistent with the hypergraph support edges.
- **G4.** Every question was routed, through authorized holders only, by the audited rank method. An as-of replay of routing
  against the domains published at route time finds no denial.
- **G5.** Every claim has gate and revision rows.
- **G6.** The provider label is truthful.
- **G7.** Every scored view traces to coordinator rows, and the harness wrote nothing to coordinator or holder data.
- **G8.** The hypergraph is present and consistent. The entity and term indexes hold exactly what each holder may publish.
- **G9.** Lineage resolves.
- **G10.** Every injected fault (duplicate lines, malformed lines, deletes, restart, replay) has its disposition.

Which gates are load-bearing, by ablation (dev S/1, candidate code):
- A2 (roots off) fails G3.
- A6 (dedupe off) fails G10.
- The corrected A5 (authorization off at `Authorizer._can_route`) fails G4, with 13,554 unauthorized routes in the as-of
  replay. The first A5 patched a method the routing path does not call, so it changed nothing; it is reported as an
  ineffective ablation (`plan/ANALYSIS_A5.md`).
- A4 (index publication off) does **not** fail G8; see §5 and `plan/ANALYSIS_A4.md`.
- A1 and A3 have no gate they are expected to break: G4's ranker assertion is skipped under A1 by design.

**The holdout run (H-M101) fails G7.** Three hypothesis claims in the asker views had fewer independent roots than the final
database, with no revision to explain the change. The cause is a product audit-trail gap: `sync_support_sync` updates
`claims.support` without a version bump or revision row (§5). Under contract §8 that run's score is not published as
full-system accuracy.


| run | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | G10 | valid |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H-M101 | pass | pass | pass | pass | pass | pass | **FAIL** | pass | pass | pass | no: G7 |
| C7-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C7abl-A1-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C7abl-A2-S1 | pass | pass | **FAIL** | pass | pass | pass | pass | pass | pass | pass | no: G3 |
| C7abl-A3-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C7abl-A4-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C7abl-A5-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C7abl-A5fix-S1 | pass | pass | pass | **FAIL** | pass | pass | pass | pass | pass | pass | no: G4 |
| C7abl-A6-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | **FAIL** | no: G10 |
| C6-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C4-S1 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C3-L3 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |
| C2a-M2 | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | yes |

Key counts from the same reports:

| run | holders | with records | routed to | activated | questions | routes | records ingested | rejections | hyperedges (support / lineage / conflict / discovery) | rank methods (G4) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| H-M101 | 1120 | 1117 | 555 | 488 | 2030 | 19921 | 6325 | 4 | 4128 / 4128 / 1106 / 1160 | rank_method {'hypergraph': 1826, 'domains': 204} |
| C7-S1 | 136 | 136 | 116 | 111 | 2604 | 25704 | 1548 | 4 | 6220 / 6220 / 3833 / 1839 | rank_method {'hypergraph': 2598, 'domains': 6} |
| C7abl-A1-S1 | 136 | 136 | 100 | 79 | 1923 | 18894 | 1548 | 4 | 4101 / 4101 / 776 / 1732 | rank_method {'ablated': 1923} |
| C7abl-A2-S1 | 136 | 136 | 117 | 111 | 2505 | 24714 | 1548 | 4 | 6202 / 6202 / 3628 / 1817 | rank_method {'hypergraph': 2499, 'domains': 6} |
| C7abl-A3-S1 | 136 | 136 | 114 | 101 | 500 | 4860 | 1548 | 4 | 1412 / 1412 / 0 / 495 | rank_method {'hypergraph': 495, 'domains': 5} |
| C7abl-A4-S1 | 136 | 136 | 125 | 111 | 3177 | 31434 | 1548 | 4 | 7540 / 7540 / 3464 / 2696 | rank_method {'domains': 2711, 'hypergraph': 466} |
| C7abl-A5-S1 | 136 | 136 | 118 | 111 | 2534 | 25004 | 1548 | 4 | 6181 / 6181 / 3731 / 1815 | rank_method {'hypergraph': 2528, 'domains': 6} |
| C7abl-A5fix-S1 | 136 | 136 | 111 | 110 | 2742 | 27420 | 1548 | 4 | 5007 / 5007 / 2884 / 1892 | rank_method {'hypergraph': 2742} |
| C7abl-A6-S1 | 136 | 136 | 119 | 111 | 2812 | 27784 | 1548 | 4 | 6742 / 6742 / 4317 / 1945 | rank_method {'hypergraph': 2806, 'domains': 6} |
| C6-S1 | 136 | 136 | 120 | 111 | 2516 | 24824 | 1548 | 4 | 6220 / 6220 / 3672 / 1807 | rank_method {'hypergraph': 2510, 'domains': 6} |
| C4-S1 | 136 | 136 | 125 | 111 | 1708 | 16753 | 1548 | 4 | 1683 / 1683 / 1690 / 855 | rank_method {'hypergraph': 1702, 'domains': 6} |
| C3-L3 | 10648 | 417 | 482 | 319 | 1508 | 14822 | 3043 | 4 | 1615 / 1615 / 205 / 902 | rank_method {'domains': 351, 'hypergraph': 1157} |
| C2a-M2 | 1120 | 307 | 331 | 250 | 1154 | 11278 | 2253 | 4 | 1161 / 1161 / 452 / 552 | rank_method {'domains': 339, 'hypergraph': 815} |

Gate details (dev runs; truncated to 140 characters per gate):

- **C7-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1373 supported claims; G4 2604/2604 questions routed; rank_method {'hypergraph': 2598, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 6220 claims, 1839 discoveries; G6 label='deterministic-provider', providers={'fake': 36621}; G7 6220 claims, 33047 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1839 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C7abl-A1-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 712 supported claims; G4 1923/1923 questions routed; rank_method {'ablated': 1923}; as-of replay denials 0 (current domains differ from route-time domains on 0 of 18; G5 4101 claims, 1732 discoveries; G6 label='deterministic-provider', providers={'fake': 23933}; G7 4101 claims, 10498 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1732 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 78; 12 deleted strings chec
- **C7abl-A2-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1515 supported claims; G4 2505/2505 questions routed; rank_method {'hypergraph': 2499, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 6202 claims, 1817 discoveries; G6 label='deterministic-provider', providers={'fake': 35453}; G7 6202 claims, 31681 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1817 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C7abl-A3-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 974 supported claims; G4 500/500 questions routed; rank_method {'hypergraph': 495, 'domains': 5}; as-of replay denials 0 (current domains differ from route-time doma; G5 1412 claims, 495 discoveries; G6 label='deterministic-provider', providers={'fake': 6752}; G7 1412 claims, 2659 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 495 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 61; 12 deleted strings chec
- **C7abl-A4-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 938 supported claims; G4 3177/3177 questions routed; rank_method {'domains': 2711, 'hypergraph': 466}; as-of replay denials 0 (current domains differ from route-time; G5 7540 claims, 2696 discoveries; G6 label='deterministic-provider', providers={'fake': 43002}; G7 7540 claims, 31253 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 2696 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C7abl-A5-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1431 supported claims; G4 2534/2534 questions routed; rank_method {'hypergraph': 2528, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 6181 claims, 1815 discoveries; G6 label='deterministic-provider', providers={'fake': 35788}; G7 6181 claims, 32052 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1815 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C7abl-A5fix-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1283 supported claims; G4 2742/2742 questions routed; rank_method {'hypergraph': 2742}; as-of replay denials 13554 (current domains differ from route-time domains on ; G5 5007 claims, 1892 discoveries; G6 label='deterministic-provider', providers={'fake': 29176}; G7 5007 claims, 29474 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1892 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C7abl-A6-S1**: G1 1548 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1360 supported claims; G4 2812/2812 questions routed; rank_method {'hypergraph': 2806, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 6742 claims, 1945 discoveries; G6 label='deterministic-provider', providers={'fake': 39418}; G7 6742 claims, 36831 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 906 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1945 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1569; 12 deleted strings ch
- **C6-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 1500 supported claims; G4 2516/2516 questions routed; rank_method {'hypergraph': 2510, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 6220 claims, 1807 discoveries; G6 label='deterministic-provider', providers={'fake': 35576}; G7 6220 claims, 31980 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 1807 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C4-S1**: G1 1536 live documents across 136 holder files; G2 136 distinct holder files, 0 registered holders without a file; G3 142 supported claims; G4 1708/1708 questions routed; rank_method {'hypergraph': 1702, 'domains': 6}; as-of replay denials 0 (current domains differ from route-time d; G5 1683 claims, 855 discoveries; G6 label='deterministic-provider', providers={'fake': 21075}; G7 1683 claims, 18168 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 892 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 112/112 holde; G9 855 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 1547; 12 deleted strings ch
- **C3-L3**: G1 3031 live documents across 518 holder files; G2 518 distinct holder files, 10130 registered holders without a file; G3 522 supported claims; G4 1508/1508 questions routed; rank_method {'domains': 351, 'hypergraph': 1157}; as-of replay denials 0 (current domains differ from route-time; G5 1615 claims, 902 discoveries; G6 label='deterministic-provider', providers={'fake': 14290}; G7 1615 claims, 2796 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 402 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 374/374 holde; G9 902 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 3044; 12 deleted strings ch
- **C2a-M2**: G1 2241 live documents across 1120 holder files; G2 1120 distinct holder files, 0 registered holders without a file; G3 246 supported claims; G4 1154/1154 questions routed; rank_method {'domains': 339, 'hypergraph': 815}; as-of replay denials 0 (current domains differ from route-time ; G5 1161 claims, 552 discoveries; G6 label='deterministic-provider', providers={'fake': 8924}; G7 1161 claims, 2482 evidence refs traced to holder responses; G8 entities 0/0 (n/a; 402 holder-entity pairs below entity_min_records, correctly unpublished), 0 outside the expected set; terms 265/265 holde; G9 552 discoveries; G10 injected {'dup_lines': 2, 'malformed': 4, 'deletes': 12, 'restart': 1, 'replay': 1}; duplicate dispositions seen 2255; 12 deleted strings ch

## 5. Remaining gaps (not repaired tonight unless listed in MORNING_HANDOFF.md)

- NATS transport and separate holder processes are not exercised by the E2E harness (single process, SQLite outbox).
  No distributed-transport or distributed-recovery claim is made.
- Coordinator text retention after deletion (DEFECT d) and the h2 metadata disclosure.
- NeuralGraph LLM extraction is off in the production composition, so graph construction relies on the pattern
  linker.
- **Silent claim-support updates (found by gate G7 on the holdout run, ledger X072).**
  - `KnowledgeService.sync_support_sync` (`mycelic/knowledge/service.py:537`), written tonight for the hypergraph support
    invariant, rewrites `claims.support` (for example independent roots) without bumping `claims.version` or writing a
    revision row.
  - A reader therefore cannot tell from the audit trail why a claim's support changed.
  - Fix: bump the version and write a `claim.support_changed` revision in the same transaction.
- **Heartbeat cost at 10,000 active people** (found 2026-10-09 ~09:07 UTC, ledger X046). Every heartbeat of every open holder
  makes the coordinator re-derive that holder's term index and shard mirror, through
  `org.holder_heartbeat → hypergraph.upsert_term_index_sync / entities.sanitize_term_counts / shard_registry.mirror_shards_sync`.
  - The work runs on the event loop.
  - When every person has records, the feed slows from ~8 to ~0.7 sources/s once ~1,300 holders are open.
  - Next fix: compare a digest of the snapshot and skip unchanged ones, or move the work off the loop.
- **Quadratic `observe()` query.** Fixed tonight in `4c27744`. A related linear per-deleted-ref `LIKE` scan of a holder's
  responses remains in `knowledge/service.py:635`; it runs on deletion only.
- **D19 product caveats** (REVIEWER-2, C5 review):
  - "Competing" should require same-kind subjects.
  - Related or aliased subjects should keep the conflict.
  - The verdict must not depend on unrelated tenant tracker keys.
  - The competing pair should be persisted as a hypergraph relation.
- **A5 vs G4.**
  - The first A5 replaced `Authorizer.can_route`; routing calls `Authorizer._can_route` directly.
  - Corrected after the holdout: the ablation now replaces `_can_route` and records how often it was reached, and G4 fails as
    it should.
  - The `denied` task class tests record-level visibility, not routing reach. A task class whose only evidence holder is
    outside the asker's scope would make routing leaks matter for accuracy.
- **A4 vs G8.**
  - The A4 ablation empties the holder heartbeat's entity and term statistics, but the coordinator's term index is still
    fully populated (G8: terms 112/112 holders). So G8 cannot detect A4 as written.
  - A4 still changes routing: in dev run C6abl-A4-S1, 3,258 of 3,799 questions fell back to domain routing, against 6 in the full
    run.
  - See `plan/ANALYSIS_A4.md`.
- **Harness measurement limits.**
  - The asker view includes at most 60 goal claims and 30 goal discoveries, so the disclosure check cannot see anything
    beyond those caps.
  - A failed `run_now` or a failed status poll is counted (`api_errors`), not scored. Only goal-only positive tasks call
    `run_now`.
