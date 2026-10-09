# Analysis: ablation A4 (index publication off) vs. architecture gate G8

Analyst: ANALYST (`claude-sonnet-5-5`), read-only, dev runs only (C6-S1 vs C6abl-A4-S1, code `4c27744`, S/1, heartbeat 2 s).
Written 2026-10-09 ~09:50 UTC. The analyst's Write tool refused report files, so the orchestrator transcribed the report
verbatim. SQL helpers: orchestrator scratch `an_a4/q1.py`–`q5.py`. `7d2e63b` is identical to `4c27744` in every file
involved; only `run.py` differs (API-error isolation), so the findings carry over to the candidate.

## Summary
1. **A4 patches only one of the three feeders of `OrgService.holder_heartbeat`.** The raw transport heartbeat
   (holder/service.py:155-160 → discovery/engine.py:1333) and the shutdown beat (embedded.py:220) bypass `_heartbeat_stats`.
2. **The term index flickers.** Every ~60 s the transport beat re-adds the terms and the next patched 2 s beat retracts them,
   so the term index is populated for only ~2-8 s of each minute. The stop-time beat refills it at the end. G8 reads only
   that final state (112/112).
3. **At runtime A4 is a partial A1 (ranker off).**
   - `rank_method` hypergraph routes: 2,510 of 2,516 (99.8 %) in the full run, 541 of 3,799 (14 %) under A4.
   - The accuracy drop (96 → 77) on positives is entirely temporal: 10/10 → 2/10.
   - Independent-support correctness: 76/78 → 22/70.
4. **G8 cannot detect A4 as written.** It judges only the final coord.db, and its entity half is vacuous in this world
   (0 expected holder-entity pairs under the default `entity_min_records` = 5).
5. **Fix:** patch the sink instead (`hypergraph.upsert_term_index_sync` / `upsert_entity_index_sync` → no-ops, or wrap
   `OrgService.holder_heartbeat`). G8's existing term-coverage check then fails with 0/112.

## Details
- **Sink.**
  - All index writes go through `OrgService.holder_heartbeat` (org.py:503): entities at org.py:567-583 →
    `hypergraph.upsert_entity_index_sync` (:417); terms at org.py:584-591 → `upsert_term_index_sync` (:482, which retracts
    only when `snapshot_complete`).
  - The only other caller is the withdraw helper (org.py:616).
- **Feeders.**
  - (a) The `heartbeat_once` callback goes through embedded.py:298-299 `_heartbeat_stats`. This is the path A4 patches.
  - (b) The same `heartbeat_once` also publishes the raw stats as a transport heartbeat envelope, once per holder per minute
    (msg_id `hb:<holder>:<minute>`, service.py:72), applied unfiltered by `LoopEngine` (engine.py:1333-1334). Not patched.
  - (c) The shutdown beat (embedded.py:220) is not patched.
  - The dormant-close beat (embedded.py:500) uses the patched function, but it never ran: `max_open_holders` = 0.
- **Evidence (A4 coord.db).**
  - term_index: 7,079 rows, 112 holders, 887 terms, identical to C6-S1.
  - `holder.terms_published` audit rows: 3,024 under A4 vs 134 in C6-S1. Each holder has 27 events: 13 add/retract pairs,
    one per minute, plus a final add at stop.
  - The first publish came 54 s after routing started.
- **Routing audit** (C6-S1 → A4):
  - routes 24,824 → 37,654; `no_evidence` responses 20 % → 38 %;
  - questions 2,516 → 3,799 (contradiction 1,340 → 1,942, verification 676 → 1,357, gap 500 → 500);
  - model calls 35,576 → 51,018.
  - The domains-only ranking is a fixed first-10-of-24 order, i.e. A1 behaviour for 86 % of routes.
- **Support quality** (C6-S1 → A4):
  - lineage exact 72/78 → 22/70;
  - mean independent roots: temporal 3.0 → 0.4 (3 genuine), fault 4.0 → 2.3, contradiction 3.9 → 2.7,
    cross_domain 2.98 → 2.35.
  - Inference, not checked per holder: the domains-only pick misses the holders with rare-term evidence, so fewer
    independent roots are reached; temporal tasks need all 3.
- **Optional G8 hardening:**
  - flag index instability (`holder.terms_published` events per holder);
  - require a minimum share of hypergraph-ranked routes (healthy 99.8 %, A4 14 %).
- **Related harness gap.** run.py:239-256 (readiness: "two identical heartbeat snapshots") hashes `ingest.entities` /
  `ingest.terms` from stored stats. But org.py:541-546 strips those keys and keeps only `entities_reported` /
  `terms_reported`, so the readiness check never covers index content. Hash the `*_reported` counts or query term_index
  per holder instead.
