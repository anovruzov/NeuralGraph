# Mycelic architecture — what actually runs

This document describes the deployed system as it exists in this repository, module by module, and
names the test that verifies each claim. Nothing here describes a planned component.

## 1. Runtime topology

Two long-running processes and two persistent volumes. Agents are separate processes owned by the
customer; they never talk to the broker.

```
                     agent processes (10–100)                      operators / Claude Code
              ┌──────────────┐  ┌──────────────┐                 ┌────────────────────────┐
              │ LocalMemory  │  │ LocalMemory  │  ...            │ admin token / MCP client│
              │ (sqlite)     │  │ (sqlite)     │                 └───────────┬────────────┘
              │ MycelicClient│  │ MycelicClient│                             │
              └──────┬───────┘  └──────┬───────┘                             │
                     │ HTTPS, Bearer mk_<agent>.<secret>                     │ HTTPS, Bearer
                     ▼                 ▼                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  mycelic  (container / process; python -m mycelic serve)                                    │
│                                                                                             │
│   aiohttp API ── auth ── rate/size limits ── audit ──► MycelicService                         │
│   /memory /events /query /memory/{id} /lineage/{id}      │                                   │
│   /verify/{id} /health /ready /metrics /admin/* /mcp     │                                   │
│                                                          ▼                                   │
│   SQLite (WAL, synchronous=FULL)  /data/mycelic.db  ◄── store: agents, memories,             │
│                                                          lineage_edges, events(outbox),       │
│                     ▲                 │                  rules, audit_log                     │
│   consumer loop ────┘                 └────► publisher loop                                  │
│   (durable pull, ack after commit,           (outbox rows → js.publish, Nats-Msg-Id,          │
│    apply = record + aggregate + derive)        HMAC signature header)                          │
└──────────────┬────────────────────────────────────────────┬─────────────────────────────────┘
               │ fetch                                       │ publish
               ▼                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│  nats-server -js   (container / process)   stream MYCELIC, subjects mycelic.<org>.<kind>     │
│  file store on /data (volume nats-data), user/password auth, port 4222 not published         │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Deployment | Definition | Verified how |
|---|---|---|
| Docker Compose | `deploy/mycelic/docker-compose.yml` (services `nats`, `mycelic`, optional `prometheus`) | `tests/smoke/mycelic_smoke.py --driver compose` built the image and passed all ten steps in this repository's sandbox; CI runs it (`.github/workflows/mycelic.yml`) |
| Local processes | `mycelic/harness.py` `ProcessDriver` (nats-server binary + `python -m mycelic serve`) | `tests/smoke/mycelic_smoke.py --driver process` (also run under pytest by `tests/mycelic/test_smoke_process.py`); the service itself against a real broker: `tests/mycelic/test_jetstream.py` |
| Kubernetes | `deploy/mycelic/k8s/` (kustomize: two StatefulSets, Service, Ingress, ConfigMaps, Secret template) | `kubectl kustomize` renders; **not applied to a cluster in this repository** |

## 2. Modules

| Module | Responsibility |
|---|---|
| `mycelic/hierarchy.py` | Layers `agent → team → department → subsidiary → region → enterprise`; unit paths; ancestor/subtree relations |
| `mycelic/config.py` | `MYCELIC_*` environment variables, validated at start (secrets never in source; placeholder values refused) |
| `mycelic/store.py` | SQLite schema (version 4: per-row digests; forward-only migrations in one transaction, including label normalisation of stored observations and rules) and transactions (`BEGIN IMMEDIATE`, one writer, `synchronous=FULL`) |
| `mycelic/integrity.py` | Canonical form of a memory row (content, parents and derivation metadata), its keyed digest, written by the insert and reproduced by a rebuild, and the keyring of current and previous signing keys that signs digests and events |
| `mycelic/transport.py` | `JetStreamTransport` (nats-py) and `InProcessTransport` (unit tests) |
| `mycelic/aggregation.py` | Topic consolidation and slot-composition rules: read-only planners (`plan_consolidation`, `plan_rule`) over pure builders (`build_consolidation`, `build_conclusion`: no store, no clock); deterministic derived ids; supersession |
| `mycelic/lineage.py` | Lineage graph reconstruction with per-principal redaction |
| `mycelic/verification.py` | Downward verification (§7): walks a memory's derivation DAG to its raw notes, checks shape, digests, raw notes against the log, re-derivation from stored parents, support thresholds, currency and freshness; one reason code per finding, redacted per caller; deterministic report and digests |
| `mycelic/retrieval.py` | BM25 (positive IDF) over visible memories, layer boost |
| `mycelic/auth.py` | API keys, principals, the visibility rule, token-bucket rate limiter (with after-the-fact charges and debt, for verification) |
| `mycelic/service.py` | Ingest, apply, query, lineage, verification, admin operations, publisher/consumer loops, recovery |
| `mycelic/api.py` | aiohttp routes and middleware |
| `mycelic/mcp.py` | MCP tools over the `chat_memory` protocol core; per-request identity; stdio proxy |
| `mycelic/metrics.py` | Prometheus metrics |
| `mycelic/sdk/` | `MycelicClient` (urllib, no dependencies), `LocalMemory`, reference agent process |
| `mycelic/harness.py` | Process and Compose drivers for the smoke test and the demo |

Reused from the existing repository: `NeuralGraph.research.coordination` (`ClaimEnvelope`,
`RuleBasedSynthesizer`, `LineageAnalyzer`), `NeuralGraph.chat_memory.mcp_server` (JSON-RPC/MCP core and
Streamable HTTP transport, generalised to take another tool set), `NeuralGraph.chat_memory.textutil`
(tokenizer), and the store/transaction pattern of `NeuralGraph.chat_memory.store`.

## 3. The data path

```
agent ──► LocalMemory.note()                       private sqlite on the agent's disk
      ──► LocalMemory.share() → POST /memory       idempotency_key = local note id
service   txn: INSERT memory(layer=agent, scope=agent path) + INSERT event(memory.observed, pending)   ──► 202
publisher pending events → js.publish("mycelic.<org>.memory-observed", wire, Nats-Msg-Id=event_id, Mycelic-Signature)
consumer  fetch(1) → verify signature → apply(event):
            txn: record event; producer must be a registered agent for that scope;
                 insert memory if absent; run aggregation over the rules and the agent registry
                 as the log has applied them (applied_rules, agents.log_status):
                   topic consolidation for every ancestor unit of the memory's scope
                   slot-composition rules whose slots the memory fills
                 derived memories + lineage edges + memory.derived events (pending)
            ack after commit
query     POST /query → BM25 over what the caller may read, in the requested unit's subtree
          → answer (highest-ranked, higher layers boosted) + lineage summary + full lineage
lineage   GET /lineage/{id} → walk lineage_edges upward to the raw observations; redact what the caller
          may not read; check every root and its source events still exist
verify    GET /verify/{id} → walk the same edges down to the raw notes under the store lock; check every node,
          recompute every derived memory from its stored parents; verdict + reasons, redacted per caller
```

Every hop is a real service call; the unit tests replace only the broker (with an in-process log that
still goes through the same outbox/apply code), and `tests/mycelic/test_jetstream.py` plus the smoke test
use the real broker.

## 4. Event log

| Subject | Kind | Produced by | Applied as |
|---|---|---|---|
| `mycelic.<org>.memory-observed` | `memory.observed` | `POST /memory`, `POST /events` (embedded memory), MCP `mycelic_remember` | insert memory if absent, aggregate; a producer's update (`metadata.version_of`, from `supersedes`) first supersedes the note it names when that is still the producer's active raw note (`superseded_by`, reason `updated by producer`), retires what rested on it and re-derives on the new note, else applies as a plain note (audit `memory.update_conflict`) |
| `mycelic.<org>.memory-derived` | `memory.derived` | the consumer, for every derived memory | informational: `duplicate` if this node derived it, else ignored and counted (`mycelic_events_ignored_total{reason="derived_not_reproduced"}`), never inserted |
| `mycelic.<org>.memory-retracted` | `memory.retracted` | `POST /memory/{id}/retract`; the expiry sweep (reason `expired`, `by` `mycelic`, event id `evt_x…` derived from the memory id, so a note gets one however often it is swept) | retract, retire dependents, re-derive |
| `mycelic.<org>.memory-attested` | `memory.attested` | `POST /memory/{id}/attest` with `still_true: true`, MCP `mycelic_attest` | set the raw note's `attested_at` to the attestation's time and sign the row again (`Tx.set_attested`), when the note is the attesting producer's active raw note, the time is not before its ingest and is after its last attestation, and its digest checks; otherwise ignored, audited (`memory.attest_ignored`) and counted (`mycelic_events_ignored_total{reason="attestation_target"|"attestation_not_newer"|"attestation_integrity"}`). Verification's freshness takes the later of ingest and attestation |
| `mycelic.<org>.agent-event` | `agent.event` | `POST /events` | recorded (evidence) |
| `mycelic.<org>.agent-registered` / `agent-revoked` / `agent-key-rotated` | admin operations | `/admin/agents` | upsert the registry (key **hashes**, never keys); `agents.log_status` records the registry as applied, which is what aggregation counts (the API writes `status` at once, for authentication and the admin views); a registration or revocation that changes how many child units a unit above the agent has re-plans that unit's promotions and everything above them |
| `mycelic.<org>.agent-removed` | `agent.removed` | `DELETE /admin/agents/{id}?retract=1` | revoke the agent and set `agents.log_status` to `removed` (terminal: a later revocation or a replayed registration leaves it), then retract every active note of the agent in this one apply (`Aggregator.retract_notes`: each note's dependents retired, each topic and rule key re-derived once, the retired memories re-evaluated), then re-plan the promotions of the units it left; a `memory.observed` of the agent applied after it is inserted retracted and never aggregated |
| `mycelic.<org>.rule-upserted` / `rule-deleted` | admin operations | `/admin/rules` | update `applied_rules`, which aggregation evaluates, then apply the rule to everything applied before it: an upsert reconciles the rule's active conclusions (withdrawn, kept or superseded) and composes the rule wherever evidence could satisfy it; a delete withdraws its conclusions; either way dependents are retired and re-evaluated. The admin table `rules` is written by the API at once, and by an event only when the event came from the stream alone and no newer local change of that rule is still unapplied |

Stream `MYCELIC`: file storage, `retention=limits`, `discard=new`, no age/size/count limit by default
(a bounded stream is logged as an error at connect), duplicate window 2 h, one replica. Consumer
`mycelic-main`: durable, pull, explicit ack, `max_ack_pending = MYCELIC_CONSUME_BATCH` (default 1, so
events are applied strictly in stream order and a failed event is redelivered in place), `max_deliver` 8
then `term`.

## 5. Aggregation semantics

* **Topic consolidation.** Unit U gets a memory on topic T when at least `MYCELIC_MIN_SUPPORT` (default 2)
  of its direct children contribute on T. A child's contribution is its own consolidation on T if it has
  one, otherwise the best material in its subtree, down to raw observations. Confidence is the noisy-OR of
  the strongest contribution per child; `support` counts distinct agents and `independent_teams` distinct
  teams in the lineage. A unit with fewer registered child units than the threshold *promotes* its child's
  consolidation unchanged (never a raw note), so the top layers of a small organization are not empty.
* **What a consolidation says.** Its readers are all members of its unit. Consolidations above team level quote
  only notes marked `visibility: org` and rule conclusions; team-visibility notes are counted there, never quoted,
  and no consolidation adds an agent id to what it quotes. The text is a head and the quoted statements
  (`aggregation.consolidation_statements`). At team level the head is `<topic> — team '<team>': <n> agents.` and the
  statements are the team's notes, team-visibility and org-visible alike, unprefixed, org-visible ones first so
  team-private notes never crowd out what may travel upward. Above team the head is `<topic> — <layer> '<unit>': <n>
  <child layer> sources, <n> agents, <n> team-private observations not quoted.` (the last clause only when there are
  some) and each statement is prefixed with the child unit it came from, `[<child>] <statement>`, joined by `; `.
  Every statement is clipped to 220 characters (whitespace runs collapsed, by code point, ending in `…`), and
  statements that read the same apart from case are quoted once. A consolidation quotes at most 12 statements, and
  above team at most `max(3, 12 // children)` per child (children in sorted order, later children drop first). The
  quoted statements are kept, unprefixed, as `metadata.statements` with `metadata.statement_origins` (`team`, `org`
  or `rule`), and `metadata.private_observations` counts the team-visibility notes beneath it. A consolidation that
  is a parent contributes its statements, never its text (one derived before statements existed contributes
  nothing), so a promotion keeps its child's statements under a new head, and a department promoting its one team
  keeps only the org and rule statements. `(+N more)` ends the text when N > 0, where N is the number of distinct
  statements its parents offer at this layer that are not shown. Every derived text, consolidation or conclusion, is
  at most 2,000 characters: statements are dropped from the end until it fits. The text and statements are a
  function of the parent set alone: parents are ordered by confidence, then a raw note's `observed_at`, then id,
  never by apply order or the apply-time clock, so a rebuild and a re-derivation reproduce them
  (`tests/mycelic/test_confidentiality.py`).
* **Slot composition.** A rule (`deploy/mycelic/rules.json` or `POST /admin/rules`) names required slots,
  a target layer, a topic prefix and minimum distinct agents/teams. The conclusion exists only when every
  slot is covered for the same entity inside the target unit; slot selection is `RuleBasedSynthesizer`
  (highest confidence per slot), confidence is the minimum over selected slots, and `LineageAnalyzer`
  fragility metrics (unique roots, independent failure domains = teams, minimal cut) are stored with it.
* **Composition (strategic synthesis).** A rule's ``sources`` may include other rules' conclusions and
  consolidations; a conclusion carries ``emits_slot``/``emits_topic`` so a higher rule can consume it;
  ``min_units`` demands per-slot corroboration across units (``{"supply_risk": {"region": 2}}``), counted
  over the memories that become parents (only the strongest per slot unless ``corroborate`` is set), and
  ``corroborate`` keeps every memory filling a slot as evidence. Derivations cascade upward (bounded depth,
  truncation logged), a rule never consumes its own output even transitively (`rule_chain` metadata; cyclic
  rule sets are refused at the admin entry points), and finer-grained evidence is preferred over a
  consolidation that restates it. Fragility metrics score at most six claims per slot. After a retraction,
  a withdrawal for lost support, or a supersession that leaves a dependent behind, dependents are retired and
  re-evaluated on the remaining evidence (`Aggregator.reevaluate`). Verified by `tests/mycelic/test_strategic.py` and
  `demo/mycelic_strategic_demo.py` (run under pytest by `tests/mycelic/test_strategic_demo_process.py`).
* **Label normalisation.** Topics, slots and entities are stored and compared in one spelling: NFKC, case-folded,
  NFKC again, whitespace runs collapsed to one space and trimmed (`models.canonical_label`; a label that is only
  whitespace is no label). `SD-9`, ` sd-9 ` and the full-width `ＳＤ－９` are one entity; Cyrillic is lower-cased and
  CJK is kept as it is, never stripped. A slot must still match `[A-Za-z0-9_.:-]{1,100}` after normalisation, and
  lengths are checked on the normalised form. The same rule applies at every entry point (`POST /memory`,
  `POST /events`, MCP, `/query` filters), to a rule's slots, topic prefix, emitted slot and topic, `min_units`
  slots (two that become one keep the larger count) and `{slot:...}` placeholders, and to `memory.observed`
  payloads at apply (so an older log replays into the same state). Verified by `tests/mycelic/test_labels.py`.
* **Log-applied rules and agents.** Aggregation reads `applied_rules` and the registry as of the event being
  applied (`agents.log_status`), never what the API has already written but the consumer has not applied, so a
  live node behind its log and a rebuild of that log derive the same history
  (`test_rules_and_registry_are_read_in_log_order_and_rebuild_identically`). A conclusion appears when the event
  that makes it hold applies and disappears when the event that ends it applies, whatever kind of event that is.
* **Rule lifecycle at apply.** A `rule.upserted` event first records the rule in `applied_rules`, then evaluates it
  against everything applied so far (`Aggregator.evaluate_rule`): every active conclusion of the rule is reconciled,
  and the rule is composed at every (unit, entity) where applied evidence could fill one of its slots, so a rule
  added after its evidence concludes at its own apply, with no new observation. Reconciling withdraws a conclusion
  whose rule is now disabled (`status_reason` `rule disabled`), no longer covers its organization or no longer
  targets its layer (`rule no longer applies here`; new conclusions appear at the new layer or only in the remaining
  organization), or whose support the new thresholds no longer reach (`support below threshold`); it supersedes a
  conclusion that the rule now derives differently (a new template, threshold, slot, topic or source: a new id, the
  old one `superseded` with `version_of` kept); and it leaves alone one that the rule derives exactly as before.
  A `rule.deleted` event withdraws every active conclusion of the rule (`rule deleted`, `Aggregator.withdraw_rule`).
  Whatever rested on a withdrawn or superseded conclusion (a strategy on regional conclusions, a consolidation of
  their topic) is retired and re-evaluated, so nothing active rests on a conclusion that is not; a new emitted topic
  or slot therefore moves the consolidations of the conclusions to the new topic and takes them away from the rules
  that consumed the old slot. Disabling then
  re-enabling a rule, or deleting and re-creating it identically, reactivates the same ids. Re-evaluating a retired
  conclusion uses the rule only where it still applies (`Aggregator.rule_for`), so a conclusion is never rebuilt at a
  unit whose layer its rule no longer targets. Two upserts of one rule that both reach the log before the first
  applies are applied in order: the first is evaluated and then superseded by the second, so a key gets at most one
  intermediate version, and a rebuild reproduces it (`tests/mycelic/test_rule_lifecycle.py`).
* **Registry changes.** Only promotion depends on how many registered child units a unit has. When an
  `agent.registered` or `agent.revoked` event changes that number for a unit above the agent (department and above;
  a team's children are agents and a team never promotes), the topics the unit promotes or could promote (those of
  its direct children's consolidations) are re-planned from the agent's team up to the enterprise
  (`Aggregator.registry_changed`). A second team in a department withdraws the department's promotion and the
  promotions above it; an agent in a brand-new region withdraws the enterprise's promotion; revoking the last agent
  of the new unit reactivates the same ids. A revoked agent's notes stay evidence, so contributions do not change. A
  removed agent's notes are retracted by the apply that records the removal, after the registry change, so what they
  re-derive already counts the units without the agent and a promotion that the removal makes possible appears in
  that apply, without an intermediate withdrawal.
  A registration into an existing unit, a duplicate registration and a revocation that is replayed or names an
  unknown agent change no count and do no aggregation work.
* **Candidates.** Each consolidation and rule reads the newest `max_candidates` (5,000) matching memories in apply
  order (`memories.apply_seq`, which numbers rows in the order the consumer applied them on every node); a child
  unit with its own consolidation contributes only that, so its notes are not read. A full candidate set means
  older evidence was left out: `mycelic_aggregation_truncated_total{what="candidates"}` counts it and a warning
  is logged once per unit and key (`test_candidate_cap_does_not_freeze_team_or_upper_layers`).
* **`*` conclusions.** A rule evaluated without an entity concludes only when evidence that names no entity is
  among the selected memories; such a `rule:*` conclusion is re-evaluated when entity-specific evidence arrives
  (`test_wildcard_conclusion_is_refreshed_by_entity_evidence`) and when evidence goes away, a retracted note or a
  withdrawn consolidation or conclusion, since the next strongest memory may name no entity.
* **Blocking candidates.** A rule selects the strongest memory per slot, so a strong memory that adds no agent can
  keep a conclusion below its threshold without the conclusion resting on it. When such a memory stops filling the
  slot, because it is retracted or withdrawn or because it is superseded by a version with another slot, entity or
  topic (a rule changed what it emits, a note with another slot joined a consolidation), the rules its slot fed are
  composed again under its old slot, entity and topic (`Aggregator._withdraw`, `Aggregator._superseded`). The
  conclusion it blocked then appears at once and is offered to everything above it
  (`test_superseded_candidate_unblocks_conclusions`, `test_withdrawn_candidate_unblocks_conclusions`,
  `test_consolidation_losing_its_slot_or_entity_unblocks_conclusions`). A version superseded by one that is the
  same candidate (same slot, entity, topic and rule chain: a note joining a consolidation, a conclusion gaining
  evidence, by far the most common case) is not composed again, because offering its successor upward has just
  composed exactly those rule keys (`test_same_candidate_successor_is_not_composed_twice`).
* **Expiry.** A raw note may carry `expires_at`. Aggregation never reads the clock, so an expired note counts in
  every consolidation and conclusion until its retraction applies: the expiry sweep (`MycelicService.sweep_expired`,
  every `MYCELIC_EXPIRY_SWEEP_SECONDS`, 100 notes per sweep) appends a `memory.retracted` event for it (reason
  `expired`), and the apply is that of any retraction, so a rebuild reproduces it without a clock. Retrieval leaves an
  expired note out at once, and verification reports it as `leaf_expired` until the retraction applies.
* **Cascades.** Retiring dependents walks active memories only (nearest first, in id order, at most 100,000);
  a cut walk and a cascade cut at depth 8 are logged as errors and counted
  (`mycelic_aggregation_truncated_total{what="dependents"|"cascade"}`). A derived id already taken by an unrelated
  row, or an earlier version that comes back with different text, is reported
  (`mycelic_aggregation_inconsistency_total{kind}`, audit `aggregation.inconsistency` with text hashes) instead of
  failing the event.
* **Determinism and versioned ids.** A derived memory's id is `sha256(operator, unit, versioned key, sorted parent
  ids)`. The versioned key of a consolidation is `<topic>|v<DERIVATION_VERSION>|ms<MIN_SUPPORT>`; that of a
  conclusion is `<rule_id>:<entity or *>|v<DERIVATION_VERSION>|r<rule digest>`, where the digest is the first 16
  hex characters of a hash of the rule's deriving fields (`models.rule_snapshot`: `rule_id` and every field except
  `enabled`, `metadata` and `org_id`, which do not change what a rule derives, so switching a rule off and on,
  deleting and re-creating it, narrowing it to one organization or editing its metadata keeps its ids). Each derived
  memory records how it was derived in `metadata.derivation`: `{v, min_support}` for a consolidation, `{v,
  rule_digest, rule}` for a conclusion (`rule` is the snapshot; agents see `{v, rule_digest}`, administrators the
  snapshot too; agents cannot set the key). `metadata.agg_key` stays the topic or `rule_id:entity`, so one version is
  active per key and a new derivation supersedes the old one across versions. `DERIVATION_VERSION` (2) is bumped
  whenever a released builder's output changes; the re-aggregation job then converges stored state. Recomputing the
  parent set from currently active evidence either leaves the active memory as is, supersedes it with a new version
  (old one readable as `previous_versions`), reactivates an earlier version whose exact coalition returned, or
  retracts it when support falls below the threshold. At most one active derived memory per (unit, operator, key)
  is enforced by a unique index.
* **Re-aggregation job.** Local maintenance, not an event (`MycelicService._reaggregate_job`,
  `Aggregator.reaggregate_step`). It starts in the background at start-up when the database was derived under
  another `DERIVATION_VERSION` or `MIN_SUPPORT` than this node runs with (`meta.derivation_version`,
  `meta.min_support`), or a migration flagged it (`meta.reaggregate_pending`), and on demand
  (`POST /admin/reaggregate`, `python -m mycelic reaggregate [--org]`). A database with no memories records the meta
  at start without a job, so a fresh volume and a rebuild never run one. Per organization it goes through three
  phases: `derived` (every active consolidation and conclusion, lower layers first, is reconciled: legacy ids are
  superseded by their new version, conclusions of deleted, disabled or moved rules and consolidations under legacy
  label spellings are withdrawn; a legacy row resting on one superseded before it, and not replaced along with it, is
  retracted as `evidence superseded` and derived again without a `version_of` link; no row is deleted), `topics` (every unit above applied evidence is consolidated, which creates what
  should exist, for example after `MIN_SUPPORT` is lowered) and `rules` (every enabled rule is composed wherever it
  has evidence). Each step reconciles at most `reaggregate_batch` (5) items in one transaction and then yields to
  the event loop, so probes, the API and the consumer keep running in between (at 5,000 notes in one organization a
  step's work took at most 0.3 s and its slowest item 0.24 s, and a full run forced by a `MIN_SUPPORT` change
  108–136 s in 1,528 steps; whole steps usually took at most 0.42 s, and the rare slower ones, up to 1.2 s, are the
  commit waiting on the disk's fsync (`synchronous=FULL`; one instrumented commit took 0.57 s); batches of 10 took up
  to 0.5 s a step and batches of 50 up to 1.3 s). Every enumeration
  is recomputed at each step and resumed after the last key processed, so applies between steps are safe; the price
  is that a run's duration grows faster than linearly with the size of the organization. While a replay is in
  progress the job runs no step (`checks.reaggregation.state` = `waiting_for_replay`) and starts again from the first
  organization afterwards. A complete run over all organizations records the meta and clears the pending flag; an
  organization-scoped run never does; an interrupted run (`interrupted`) or a failed one (`failed`, with the error;
  the node keeps serving and stays ready) leaves the meta, so the next start runs again from the beginning. Each
  organization's run writes one audit row `aggregation.reaggregate` (`org_id`, `changed`, `steps`, `reason`) and every
  step counts `mycelic_reaggregation_steps_total`. The derived events a run emits are not reproduced by a rebuild: a
  rebuild derives the converged state directly and counts those events as duplicates or ignored
  (`tests/mycelic/test_reaggregate.py`).
* **Fixed point.** `tests/mycelic/test_aggregation_invariants.py` applies seeded random sequences of registrations
  (full, sparse and in a second organization), revocations, notes with random labels, retractions and rule upserts,
  disables, deletes, re-creations, layer and organization moves, and checks after each operation (or once at the end)
  that one memory is active per key, every active derived memory rests on active parents, consolidations stand for
  enough children of their unit, support is that of the roots, re-planning reproduces every active derived memory
  and every key that holds has its memory, notes reach every unit above them under `MIN_SUPPORT` 1, a rebuild of the
  log reproduces the history, and a full re-aggregation pass changes nothing (`MYCELIC_INVARIANT_SEEDS` sets the
  number of seeds).

Verified by `tests/mycelic/test_datapath.py` (consolidation, rule firing, supersession, reactivation,
retraction) and by the smoke test on a live deployment.

## 6. Lineage semantics

`GET /lineage/{id}` answers, for any memory the caller may read:

| Question | Field |
|---|---|
| Where did this come from? | `roots` (raw observations), `nodes`, `edges` |
| Which memories contributed? | `nodes`/`edges` (the full DAG), `previous_versions` |
| Which agents / teams contributed? | `contributing_agents` (visible ones), `contributing_teams`, `redacted_contributions` |
| Which layers transformed it? | `layers`, `transformations` (operator, rule, from/to layer, time) |
| When was it produced? | `timeline.first_observed_at`, `last_observed_at`, `produced_at` |
| What confidence/support exists? | `support.confidence/agents/teams/roots`, `support.fragility` |
| Can the evidence still be reconstructed? | `evidence.reconstructable` (all roots present and not retracted, all cited source events in the log), `events_missing`, `missing_parents`, `retracted_roots` |

Contributions the caller may not read are **redacted, not dropped**: text, agent id, event ids and
entity are withheld, the unit (team) path, layer, timestamps and confidence remain.

Text of a memory that is not active (superseded or retracted) is returned only to its producer and to
administrators; everyone else who may read the memory gets an empty `text` and `text_withheld` set to its status.
In a lineage such a node keeps its shape and every other field (`Principal.can_read_text`, passed to
`lineage.reconstruct` as `readable_text`), so the nodes, edges and counts are the same for every reader; redacted
nodes stay as they are (`text` null, no `text_withheld`). `GET /memory/{id}`, `GET /memories?status=` and the MCP
tools apply the same rule and also drop `metadata.statements` and `statement_origins`; `POST /query` searches active
memories only.

## 7. Downward verification

`mycelic/verification.py` answers "was this memory derived correctly, and is it still true?" for anyone who may
read the memory, by walking its derivation DAG (`lineage_edges`) down to the raw notes and checking every node.
It is aggregation run backwards: the same pure builders that derived a consolidation or a conclusion
(`build_consolidation`, `build_conclusion`) recompute it from its stored parents, and the same planner that derives
memories now (`Aggregator.planner()`, which counts nothing in the aggregation metrics) says whether it is still the
current one. `MycelicService.verify` runs it under the store lock, so the walk only sees committed states.

| Surface | Form |
|---|---|
| REST | `GET /verify/{id}[?max_leaf_age=N]` (N from 1 to 315,360,000 seconds): the report as the body, 200 for every verdict |
| REST | `POST /query` with `"verify": true`: `answer.verification` holds `verdict`, `derived_correctly`, `still_true` and the report-level `reasons`; without the flag (or with `false`) the response is unchanged |
| MCP | `mycelic_verify(memory_id, max_leaf_age_seconds?)`, over HTTP and through the stdio proxy |
| SDK | `MycelicClient.verify(memory_id, max_leaf_age=None)`, `MycelicClient.query(..., verify=True)` |
| CLI | `python -m mycelic verify MEMORY_ID [--max-leaf-age N] [--json]` (exit 0 verified, 3 stale, 4 failed, 5 unverifiable, 1 on an HTTP error, 2 on a usage error), `python -m mycelic query --verify` |

Every surface needs `lineage:read` (403 without it) on a memory the caller may read: an id that does not exist and
one the caller may not read get the same 404, an id outside `[A-Za-z0-9_.:-]{1,200}` a 400.

**The walk and the checks.** Breadth first from the memory, one batched read of edges and one of rows per level, each
frontier in sorted order, at most `MYCELIC_VERIFY_MAX_NODES` (25,000) rows. Then, in order: each row's digest
(`integrity.check_memory`); the shape (missing parents, cycles, raw notes with parents, edge fields against the
parent row); each raw note against its `memory.observed` event, the events it cites, its producer's registration, the
retractions in the log and the removals of its producer, the update that superseded it (when it is superseded),
pending updates, and its `expires_at` against the time of the check; every node's status; each derived memory whose
parents passed their own integrity check, recomputed from its stored parents under its stored derivation
(`metadata.derivation`: the `MIN_SUPPORT` or the rule snapshot and digest it was derived under) and compared on id,
text, confidence and every covered field, with its unit, eligibility and support thresholds; the currency of each
active derived memory (its rule as applied, the configured `MIN_SUPPORT`, the planner's answer now); with
`max_leaf_age`, how long ago the server ingested each raw note the caller can read or its producer last re-attested
it (`attested_at`), whichever is later; and the report-level codes. A child whose parent failed its integrity check
is not recomputed: the parent's code stands, and the child is not blamed for it.

**Verdict.** `failed` on any E code, else `unverifiable` on any U, else `stale` on any S, else `verified`; warnings
(W) never change it. `derived_correctly` is false on an E, null on a U, else true; `still_true` is null on an E or a
U, false on an S, else true. All three are computed from the codes before redaction, so every viewer gets the same
answer. A conclusion retracted or superseded between a `POST /query` and its verification reads `stale`.

**Reason codes.** Each finding is one code with exactly one severity, at most once per node (E error: the
derivation is wrong or was tampered with; U unverifiable: something needed to check it is not available; S stale:
derived correctly but no longer current; W warning).

| Code | Severity | Meaning |
|---|---|---|
| `missing_parent` | E | a lineage edge names a parent row that does not exist |
| `cycle_detected` | E | the derivation graph has a cycle |
| `unexpected_parent` | E | a raw note has lineage parents |
| `edge_mismatch` | E | an edge's `parent_layer` or `contributed_by` disagrees with its parent row (the two edge fields no digest covers) |
| `integrity_mismatch` | E | the row's digest does not match the row as stored and its parents |
| `integrity_downgraded` | E | an unkeyed digest where a signing key is set |
| `integrity_unknown_key_forged` | E | a digest under a key id this database never recorded |
| `integrity_missing` | E | no digest, although the start-up backfill completed |
| `integrity_unknown_key` | U | a digest under a recorded key that is no longer configured |
| `integrity_not_backfilled` | U | no digest yet: the start-up backfill has not completed |
| `integrity_backfilled` | W | signed by the start-up backfill, so integrity is proved from the backfill onward |
| `source_event_mismatch` | E | a raw note differs from its `memory.observed` event on a field the event carries |
| `producer_unregistered` | E | a raw note's producer is not a registered agent at the note's path in its organization |
| `status_inconsistent` | E | a raw note's status disagrees with the retractions in the log, or the removal of its producer, or it is superseded other than by its producer's applied update |
| `source_event_missing` | U | a raw note's own event is not in the log |
| `cited_event_missing` | U | an event a raw note cites (`source_event_ids`) is not in the log |
| `retraction_pending` | S | a retraction of the raw note, or a removal of its producer, is in the log but not applied yet |
| `update_pending` | S | an update of the active raw note by its producer is in the log but not applied yet |
| `producer_revoked` | W | the raw note's producer has been revoked since |
| `not_applied` | W | the raw note has not been applied by the consumer yet |
| `parent_outside_unit` | E | a parent lies outside the derived memory's unit or organization, or not below its layer |
| `topic_mismatch` | E | a consolidation's parent has another topic |
| `parent_ineligible` | E | a parent is not evidence the operator or the rule accepts (operator, slot, topic prefix, entity, or the rule's own chain) |
| `slot_uncovered` | E | a conclusion has no evidence for one of its rule's required slots |
| `below_min_support` | E | a consolidation has fewer contributing child units than its `MIN_SUPPORT`, or is a promotion that should not be |
| `below_min_agents` | E | a conclusion's evidence comes from fewer distinct agents than the rule's `min_agents` |
| `below_min_teams` | E | a conclusion's evidence comes from fewer distinct teams than the rule's `min_teams` |
| `below_min_units` | E | a slot's evidence spans fewer units than the rule's `min_units` |
| `rule_snapshot_mismatch` | E | the rule snapshot stored with a conclusion does not match its stored rule digest |
| `id_mismatch` | E | recomputing the memory from its parents gives another id |
| `text_mismatch` | E | recomputing it gives another text |
| `confidence_mismatch` | E | recomputing it gives another confidence |
| `content_mismatch` | E | recomputing it differs on another covered field or derivation metadata key |
| `not_derivable` | E | nothing is derived from these parents any more (or the operator is unknown) |
| `legacy_derivation` | U | derived by an older release, without derivation-version-2 metadata: unverifiable until re-aggregated |
| `node_retracted` | S | the node is retracted |
| `node_superseded` | S | the node is superseded |
| `leaf_expired` | S | an active raw note is past its `expires_at`: its retraction by the expiry sweep has not applied yet (judged for every raw note, so a hidden one shows `hidden_stale`) |
| `leaf_stale` | S | with `max_leaf_age`: a raw note the caller can read was ingested, and last re-attested by its producer, longer ago than that |
| `min_support_changed` | S | a consolidation was derived under another `MIN_SUPPORT` than the configured one |
| `rule_deleted` | S | the conclusion's rule is deleted, as applied from the log |
| `rule_disabled` | S | the conclusion's rule is disabled |
| `rule_changed` | S | the conclusion's rule changed (its digest, target layer or organization) |
| `not_current` | S | planning the memory's unit and key on the applied evidence now derives something else |
| `walk_truncated` | U | the walk stopped at `MYCELIC_VERIFY_MAX_NODES` (report level) |
| `replay_in_progress` | U | a replay from the log is running (report level) |
| `log_lag` | W | events of the organization are not applied yet (report level; administrators only) |
| `hidden_error` | E | an E code on a node the caller may not read |
| `hidden_unverifiable` | U | a U code on a node the caller may not read |
| `hidden_stale` | S | an S code on a node the caller may not read |

**The report.** `memory_id`, `verdict`, `derived_correctly`, `still_true`, `reasons` (report level: code, severity,
count of nodes), `warnings`, `summary` (`nodes`, `derived`, `leaves`, `redacted`, `failed_nodes`,
`unverifiable_nodes`, `stale_nodes`, `unexplored`, `hidden_warnings`), `superseded_by`, `current_version`,
`freshness_partial`, `valid_until` (the earliest `expires_at` of the active raw notes the caller can read in the walk,
null without one), `valid_until_partial` (true when the walk was truncated or holds raw notes the caller may not
read, whose expiries `valid_until` does not cover), `integrity_mode` (`keyed` or `unkeyed`), `verified_at`,
`max_leaf_age`, `nodes` (each with its `memory_id`, `layer`, `scope`, `operator`, `status`, `redacted`, `ok` and
`reasons`), `dag_digest` and `report_digest`; an administrator's report also has `as_of.last_applied_seq`.

**Redaction.** Every node shows its id, layer, unit (a raw note's team: its path would end in its producer's id),
operator, status and `ok`. A node the caller may not read (`Principal.can_read`) shows nothing more; of its codes it
keeps only those lineage already discloses (`node_retracted`, `node_superseded`, `missing_parent`,
`cycle_detected`), every other one becomes `hidden_error`, `hidden_unverifiable` or `hidden_stale`, it carries no
details, and its warnings are only counted (`summary.hidden_warnings`). No detail on any node, for any viewer,
carries text, statements, metadata values, agent or producer ids or row digests; key ids, `as_of` and `log_lag` go
to administrators only. Freshness does not judge raw notes the caller may not read (`freshness_partial`).

**Determinism.** Frontiers are sorted, so a truncated walk cuts at the same ids every time. `dag_digest` hashes the
DAG's shape (ids, layers, edges, missing parents, truncation): the same for every viewer and after a rebuild from
the log. `report_digest` hashes the verdict, both answers, `max_leaf_age`, the DAG digest, the report-level reasons
and each node's status, `ok` and codes as the caller sees them; it leaves out details, warnings, times and pointers,
so two calls agree, a rebuild agrees, and a row re-signed by the start-up backfill changes nothing. What a report
shows depends on who asks, so digests are compared caller against caller: smoke step 8 compares the administrator's
before and after a rebuild.

**Cost and limits.** About 0.1 ms per node plus one planner run per active derived memory, and one read of the
organization's retractions logged since the oldest raw note's event, one of its agent removals and one of its updates
not applied yet; measured through `MycelicService.verify` in
this repository's idle sandbox, 0.10–0.20 s for 1,005 nodes and 0.56–0.85 s for 5,005 nodes. The walk holds the store lock
and the event loop. `MYCELIC_VERIFY_MAX_NODES` bounds it (`walk_truncated` beyond). A verification costs the caller
1 + ceil(nodes / 250) rate-limit tokens: the request's token at the door and the walk's share after the walk
(`RateLimiter.take`), which may put the principal's bucket into debt, at most one burst deep; a principal in debt
is refused before anything is read (`RateLimiter.in_debt`; 429, or an error result inside an MCP batch), so a
JSON-RPC batch of verify calls walks at most once past solvency. The debt check and the charge run under the store
lock. Each successful verification is counted (§9) and audited (`memory.verify`, with the codes before redaction);
refused ones are neither. The reason counter takes the codes of the caller's own report (`hidden_*` for a node it
may not read, none of that node's warnings): `/metrics` accepts any agent key while `MYCELIC_METRICS_TOKEN` is unset,
and diffing it around a verification must not tell an agent what its report withheld.

Verified by `tests/mycelic/test_verification.py` (every code on its node, redaction, authorisation, determinism and
rebuilds, a 5,005-node fan-in), `tests/mycelic/test_api.py` (the route's errors and four verdicts, the `POST /query`
flag, the MCP tool, cost-weighted limiting over REST, concurrent requests and MCP batches, the reason counters as
the caller saw them, and this table against `verification.REASONS`), `tests/mycelic/test_sdk_cli.py` (SDK, CLI exit
statuses, stdio proxy against new and old servers), `tests/mycelic/test_integrity.py` (no digest in any verification output) and smoke steps 5 and 8.

## 8. Persistence and recovery

| Failure | Mechanism | Test |
|---|---|---|
| service crash / restart | durable consumer resumes at its ack floor; SQLite is the read model | `test_publish_consume_and_survive_service_restart`, smoke step 6 |
| broker outage | writes go to the outbox (`events.status='pending'`), `/health` reports `degraded`, `/ready` stays 200 by default; publisher flushes on reconnect | `test_broker_outage_is_absorbed_by_the_outbox`, smoke step 7 |
| broker restart | JetStream file store keeps the stream and the consumer | same tests (the broker is SIGKILLed) |
| lost service database | fresh database + non-empty stream ⇒ consumer reset to sequence 1, full replay rebuilds memories, lineage, derived state, agent registry (key hashes) and rules; replay never re-publishes derived events; the replay target is persisted so a crash mid-rebuild resumes it | `test_lost_database_is_rebuilt_from_the_stream`, `test_unfinished_replay_resumes_after_a_crash`, smoke step 8 (which also checks that the administrator's verification of the answer has the same report digest) |
| database restored from backup | `last_applied_seq` behind the consumer's ack floor ⇒ consumer recreated at `last_applied_seq + 1` | `test_restored_backup_receives_the_events_it_missed` |
| durable consumer lost | a brand-new consumer facing a database that applied events ⇒ recreated at `last_applied_seq + 1` | `test_lost_consumer_is_recreated_after_the_last_applied_event` |
| forced replay, poison events | `POST /admin/replay` re-delivers everything idempotently; an event that fails `max_deliver` times, or fails the signature check, is terminated, recorded and counted as consumed so the replay still completes | `test_forced_replay_is_idempotent`, `test_poison_event_is_terminated_and_replay_completes` |
| agent restart | local notes persist; re-sending uses the local id as idempotency key | smoke step 9 |
| forged / unsigned events on the stream | HMAC-SHA256 signature checked before apply; producer must match the registry | `test_unsigned_events_are_rejected` |
| lost NATS volume with intact database | **not covered**: the database keeps serving, but the log cannot be replayed until new events accumulate (see DEPLOYMENT.md, backups) | — |

## 9. Observability

`GET /metrics` (Prometheus): `mycelic_memories_ingested_total`, `mycelic_events_received_total`,
`mycelic_events_published_total{kind}`, `mycelic_events_applied_total{kind,result}`,
`mycelic_events_failed_total{stage}`, `mycelic_replay_events_total`, `mycelic_recovery_total{kind}`,
`mycelic_memories_derived_total{layer,operator}`, `mycelic_events_ignored_total{reason}` (`derived_not_reproduced`,
`retraction_target`, `unknown_kind`, `attestation_target`, `attestation_not_newer`, `attestation_integrity`),
`mycelic_aggregation_inconsistency_total{kind}` (`id_collision`, `reactivation_mismatch`),
`mycelic_aggregation_truncated_total{what}` (`candidates`, `dependents`, `cascade`), `mycelic_retrieval_latency_seconds`,
`mycelic_aggregation_latency_seconds`, `mycelic_reaggregation_steps_total` (re-aggregation job steps, one
transaction each), `mycelic_lineage_latency_seconds`,
`mycelic_lineage_reconstruction_total{result}`, `mycelic_verifications_total{verdict}`,
`mycelic_verification_reasons_total{reason}` (each code once per verification, as the caller saw it),
`mycelic_verification_latency_seconds`, `mycelic_memories_expired_total` (expired notes the sweep queued a
retraction for, each once), `mycelic_memories_expiry_overdue` (active notes past their `expires_at`: a backlog when it
stays up), `mycelic_http_requests_total{route,status}`,
`mycelic_auth_failures_total{reason}`, `mycelic_active_agents`, `mycelic_registered_agents`,
`mycelic_memories{layer}`, `mycelic_outbox_pending`, `mycelic_transport_connected`,
`mycelic_consumer_pending` (read from the broker at scrape time), `mycelic_build_info{version}`.

`GET /health` (public: status, version, transport connected, consumer running), `GET /ready`,
`GET /admin/status` (full checks, stream/consumer positions, masked settings, and `checks.reaggregation`: the
re-aggregation job's `state` (`idle`, `running`, `waiting_for_replay`, `done`, `interrupted`, `failed`), `reason`,
`scope`, current `org_id` and `phase`, `steps`, `changed`, `started_at`, `finished_at`, `error`; and `checks.expiry`:
`enabled`, `interval_seconds`, `overdue`, `last_sweep_at`, `last_queued`), `GET /admin/audit`, `GET /admin/events`.
The job never affects `/ready`: the node serves while it converges. Every successful downward
verification writes the audit row `memory.verify` (principal, target, organization, verdict, nodes walked and the
reason codes before redaction).

## 10. Mermaid version of the topology

```mermaid
flowchart LR
  subgraph Agents["agent processes (customer-owned)"]
    A1[agent + LocalMemory] -- HTTPS bearer --> API
    A2[agent + LocalMemory] -- HTTPS bearer --> API
  end
  subgraph Mycelic["mycelic container"]
    API[aiohttp API + MCP] --> SVC[MycelicService]
    SVC --> DB[(SQLite /data/mycelic.db)]
    SVC --> PUB[publisher]
    CONS[consumer / aggregator] --> SVC
  end
  PUB -- publish, signed --> JS[(NATS JetStream\nstream MYCELIC, file store)]
  JS -- durable pull --> CONS
  OPS[operator / Claude Code] -- admin token / MCP --> API
```
