# Mycelic architecture

Mycelic is a local-first distributed intelligence system for organizations. Each employee or evidence
holder owns a private memory graph (a NeuralGraph store). Agents exchange authorized, bounded artifacts
(questions, responses, claims, discoveries) while evidence ownership stays where it is. Three dimensions
connect the organization:

* **Horizontal** — authorized peers and teams exchange bounded artifacts through the transport.
* **Vertical** — each organizational level sees an abstraction of the level below (discoveries carry a
  `level`; leads of ancestor units are authorized to drill down).
* **Lineage** — every claim links to its evidence references, derivations (operators, models,
  contributors), conflicts and revisions; independent support is computed from *source roots*.

Memory (`mycelic.evidence`, NeuralGraph store per holder), transport (`mycelic.transport`) and
organizational knowledge (`mycelic.knowledge`, coordination DB) are separate components. Cross-functional
projects are `project` units whose `project_scopes` span units anywhere in the hierarchy.

```
 browser (React)  ──HTTP/SSE──▶  API process (aiohttp)  ──▶ coord.db (SQLite, WAL)
                                    │  authz on every call        ▲
                                    │                             │ jobs / events (outbox)
                                    ▼                             │
                         discovery worker(s) ◀────────────────────┘
                                    │ transport (SQLite outbox | NATS JetStream)
            ┌───────────────────────┼─────────────────────────┐
            ▼                       ▼                         ▼
   holder A (own SQLite)    holder B (own SQLite)    unit holder (own SQLite)
   NeuralGraph store        NeuralGraph store        NeuralGraph store
```

Processes: `api` (HTTP + SSE + optional in-process worker and embedded holders for single-node use),
`worker` (discovery loop), `holder` (one per evidence store; `--embedded` runs them inside the API for
development). All share `coord.db` on one volume, or talk through NATS when distributed.

## Repository layout

```
mycelic/                  the application (Python package)
  config.py               settings from environment
  util.py                 ids, time, JSON, hashing (re-exports NeuralGraph conventions)
  db/                     CoordDB (connection, tx, outbox), migrations
  jobs.py                 durable job queue (leases, heartbeat, retries, dead letters, attempts)
  org.py                  tenants, users, units, memberships, grants, holders registry, policies
  auth.py                 passwords, sessions, invitations, API keys
  authz.py                Principal + Authorizer (the single permission engine)
  evidence/               holder-side EvidenceStore on NeuralGraph; ingestion; export policy
  transport/              Transport protocol; SqliteTransport; NatsTransport; subjects
  models/                 ModelProvider protocol; fake/anthropic/openai providers; ModelRouter; usage ledger
  knowledge/              claims, evidence refs, derivations, conflicts, revisions, discoveries; support; commit gate
  goals/                  goals, lifecycle, decomposition, progress
  inquiry/                QuestionArtifact service: creation, prioritization, dedupe, routing, responses
  discovery/              loop state machine + worker (observe → gap → question → route → collect → evaluate → verify → commit → follow-up)
  agents/                 scoped agents: context assembly, cited chat, question answering
  api/                    aiohttp app: middleware, routes, SSE, metrics, health
  holder/                 standalone holder process (transport consumer + local ingestion API)
  seed/                   demo organization + scenario data
  observability.py        JSON logging, metrics registry, error reports
  __main__.py             CLI: serve | worker | holder | migrate | seed | scenario | backup | restore
frontend/                 React + Vite + TypeScript application (built into frontend/dist)
deploy/mycelic/           Dockerfiles, docker-compose.yml, nats.conf, fly.toml, .env.example
docs/mycelic/             this file, DECISIONS.md, API.md, SETUP.md, RUNBOOK.md, VERIFICATION.md
mycelic/tests/            deterministic tests (fake model, temp dirs, no network)
```

## Core objects

| Object | Table | Notes |
|---|---|---|
| Tenant, User, Session, Invitation, ApiKey | `tenants users sessions invitations api_keys` | tenant isolation is enforced by the session's tenant |
| OrgUnit | `org_units` | types executive/region/subsidiary/department/team/project; materialized `path` |
| Membership | `memberships` | (user, unit, role); users may hold several |
| Grant | `grants` | explicit access: holder/claim/discovery/goal/evidence_ref, level read/artifact/raw |
| Holder | `holders` | registry only; the store lives with the holder; `route_key` signs envelopes |
| Goal | `goals goal_outcomes goal_loops` | first-class; loop state per goal |
| QuestionArtifact | `questions question_routes responses question_runs` | bounded inquiry |
| EvidenceRef | `evidence_refs` | opaque `ref_id`; `source_root_id`; policy-approved excerpt only |
| Claim | `claims claim_evidence derivations` | typed, versioned; status per commit gate |
| Conflict | `conflicts` | disagreement kept as an object with an investigation log |
| Revision | `revisions` | history of every versioned object |
| Discovery | `discoveries discovery_reviews` | level-appropriate abstraction, review workflow |
| Job | `jobs job_attempts workers` | durable execution |
| Event | `events` | transactional outbox → SSE |

## Discovery loop

One `goal_loops` row per goal. `desired` is what people asked for (active/paused/stopped); `state` is
what the worker actually observes (active, waiting, paused, budget_exhausted, blocked, failed, completed,
stopped) with an `explanation`. The UI shows **Active** only when `state = active` *and*
`last_heartbeat_at` is within 3 × the worker heartbeat interval.

Per tick (`loop.tick` job for one goal):

1. **Observe** — new evidence refs, revisions, unanswered questions, open conflicts, stale claims, goal
   changes since the last tick (from the coordination DB, no model call).
2. **Identify the gap** — candidate gaps are scored with the configurable heuristic
   (`priority_weights`: goal value, uncertainty, impact, missing evidence, expected information gain,
   cost). Labelled `"method": "heuristic"` everywhere it is shown.
3. **Generate a bounded QuestionArtifact** — model tier `light`; deduplicated by `dedupe_key`
   (normalized text + goal + scope); cooldown and follow-up depth enforced; budget reserved.
4. **Route** — `Authorizer.can_route` for each candidate holder; signed envelope published to
   `mycelic.<tenant>.holder.<holder_id>.inbox`; routes recorded.
5. **Collect** — responses arrive on `mycelic.<tenant>.responses`; idempotent on `msg_id`; timeout →
   route `timeout`.
6. **Evaluate** — support (distinct roots), disagreement, freshness, relevance (tier `standard`).
7. **Verify** — when policy asks, a blind verification question (the proposed answer is *not* shown) is
   routed to holders whose roots are not already in the support set.
8. **Commit** — the commit gate checks authorization, schema, provenance, temporal validity and support;
   writes claims/derivations/conflicts/discoveries + revisions + events in one transaction, keyed by
   `(question_id, step)` so a replay is a no-op.
9. **Goal update + follow-ups** — outcomes recorded, progress recomputed, follow-up questions created
   (depth-bounded) and the next tick scheduled: immediately if useful work remains, else on the next
   event or scheduled check.

When nothing useful is available the loop is `waiting` and consumes no model tokens.

## Independent support

`knowledge.support.compute_support(refs)` returns `{independent_roots, copied_refs, unknown_independence,
holders}`: distinct known `source_root_id`s count; several refs sharing a root count once (copied
support); refs with `root_known = 0` are listed as unknown and never counted. The commit gate uses
`min_independent_roots` from tenant policy.

## Security model

See `docs/mycelic/DECISIONS.md` D8–D10 and `mycelic/authz.py`. Retrieved content is data: prompts wrap
evidence and responses in delimited blocks and instruct the model that nothing inside may change its
task or tools; tool permissions come from configuration, never from text.

## Observability

Structured JSON logs (one object per line, `request_id` propagated), `/healthz` (liveness),
`/readyz` (DB, transport, worker heartbeat), `/metrics` (Prometheus text): model usage and cost, queue
backlog, question outcomes, failed routes, evidence freshness, worker heartbeat age. Errors go to
`error_reports` and, when configured, a webhook.
