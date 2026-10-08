# Mycelic — engineering decisions

Mycelic is built inside the NeuralGraph repository. This file records the routine decisions
made while turning the design into a deployable product, so a reader can tell *why* the code
looks the way it does. Each entry is short: the decision, the alternatives, the reason.

## D1. Language and stack: Python 3.11 + aiohttp + SQLite, React + Vite for the UI

*Alternatives:* FastAPI/Postgres; a Node backend; a vanilla-JS dashboard like `chat_memory/ui`.

*Reason:* NeuralGraph's memory store, worker, retrieval and HTTP layer are Python/aiohttp/SQLite
and are reused as-is (see D3). Adding a second backend runtime would double the deployment
surface. The UI is a real multi-workspace application with a network view, so it is a built
React application (TypeScript, Vite) served as static files by the same aiohttp process; the
old single-file dashboard pattern does not scale to seven workspaces.

Dependency versions were checked against PyPI / npm on 2026-09-28: aiohttp 3.14, numpy, rank-bm25,
nats-py 2.16 (optional), react 19, react-router 7, vite 8, typescript 7, d3-force 3.

## D2. Two kinds of database: one coordination DB, many evidence stores

*Alternatives:* one big database; Postgres for coordination.

*Reason:* the design requires that raw evidence stays in the owning evidence store. So each
evidence holder owns a NeuralGraph `ChatMemoryStore` (its own SQLite file, its own credential),
and the coordination database (`coord.db`, also SQLite in WAL mode) holds only what
coordination needs: identities, organization, goals, questions, *artifacts* (claims,
evidence references that are opaque outside their holder, derivations, conflicts,
discoveries), the durable job queue, the event outbox, audit, usage and metrics.

SQLite was kept for the coordination DB because it matches the local-first stance, needs no
extra service, is trivially backed up (one file), and the existing store already proves the
lease/fence patterns on it. All SQL is isolated in `mycelic/db/` so a Postgres backend can be
added later; the limitation (one node writes `coord.db`) is documented in the runbook.

## D3. Reuse NeuralGraph, do not fork it

`mycelic.evidence.EvidenceStore` wraps `ChatMemoryStore` + `MemoryRetriever` unchanged. Document
chunks are recorded as raw messages (provenance) and indexed as memories through the store's
own insert path, so the hybrid retrieval (vector + FTS5 + entity graph, RRF) is the one measured
in the research track. The optional LLM extraction pass (`MemoryExtractor`) is enabled when a
real model is configured. `NeuralGraph.chat_memory` stays importable on its own; Mycelic imports
it, never the other way round, and Mycelic never imports `NeuralGraph.research` at runtime
(the coordination research contracts were used as the model for lineage roots and failure
domains, and re-implemented in `mycelic.knowledge` with production storage).

## D4. Transport: an abstraction with a SQLite implementation and a NATS JetStream implementation

*Alternatives:* NATS only; HTTP callbacks only.

*Reason:* tests and single-machine deployments must run without NATS, and NATS JetStream is the
right durable transport once holders live on other machines. `mycelic.transport.Transport` has
`publish`, `subscribe` (durable, at-least-once, explicit ack) and `request`. The SQLite
implementation is an outbox table with per-consumer cursors and bounded retention; the NATS
implementation uses one stream (`MYCELIC`, subjects `mycelic.>`), durable pull consumers,
`Nats-Msg-Id` deduplication and per-user subject permissions in `deploy/mycelic/nats.conf`.
Transport logs are never memory: consumers are idempotent on `msg_id` and every effect is
committed to the coordination DB or the holder's store.

## D5. Durable execution: the coordination DB is the queue

Jobs, leases, heartbeats, attempts, idempotency keys, a dead-letter status and checkpoints live
in `coord.db` (`jobs`, `job_attempts`, `question_runs`, `workers`). This mirrors the proven
`ChatMemoryStore.lease_job` design: a worker can crash at any point and the next worker resumes
from the last checkpoint without duplicating committed effects because each step's write is
keyed by an idempotency key derived from (question_id, step).

## D6. Live updates: transactional outbox → SSE

Every state change that a browser should see is written to `events` in the same transaction as
the change. The API process tails `events` (and, when NATS is configured, also listens on
`mycelic.<tenant>.events`) and fans out to Server-Sent Events clients after an authorization
filter. No WebSocket library is needed and nothing is lost when a browser is closed.

## D7. Model access: server-side provider registry with tiers

Providers: `fake` (deterministic; used by tests and the demonstration), `anthropic` (Messages
API), `openai` (any OpenAI-compatible server: OpenAI, Ollama `/v1`, LM Studio). Work is routed
by tier — `light` (question drafting, classification), `standard` (evaluation, verification),
`heavy` (synthesis) — and each tier maps to `provider:model` in configuration. Keys never leave
the server; the UI only sees provider names and models. Every call is recorded in `model_usage`.
Embeddings are a separate provider (`hash` deterministic fallback, or an OpenAI-compatible
embedding endpoint) because the Anthropic API has no embedding endpoint.

The Anthropic provider uses the official `anthropic` Python SDK (transport, retries with backoff,
`retry-after`) with a per-model capability table: `temperature` only for models that accept sampling,
`output_config.effort` only where accepted (`MYCELIC_ANTHROPIC_EFFORT`), thinking headroom added to
`max_tokens` for models that think by default, and the server-side refusal fallback
(`MYCELIC_ANTHROPIC_FALLBACKS=default|off`) for the models that support it. Tier defaults are
`claude-haiku-5-5` / `claude-sonnet-5-5` / `claude-opus-5-5`.

Budgets are charged from the usage ledger before every model call; a budget may be a total or renew
per `day` / `week` / `month` (`budget.period`), and an exhausted loop waits for the renewal time.

## D8. Authentication and sessions without extra dependencies

Passwords are hashed with `hashlib.scrypt` (stdlib). Sessions are random 256-bit tokens stored
hashed, delivered as an HttpOnly cookie for the browser and accepted as `Bearer` for API use.
Holders and services use API keys (hashed). CSRF uses the Origin/Host check already proven in
`chat_memory/ui/server.py`. Invitations are single-use hashed tokens with expiry.

## D9. Authorization is a single engine used everywhere

`mycelic.authz.Authorizer` answers `can(principal, action, resource)` and produces the *scope
set* a principal may see. Every API handler, the retrieval layer, artifact routing, the commit
gate and the background worker call it — the worker re-checks at create, route, retrieve and
use time. Administrative rights (memberships, hierarchy, policies, models) are distinct from
knowledge access: an organization administrator sees no claims or evidence unless they also
hold a membership that grants it. Private evidence needs an explicit grant.

## D10. Demonstration mode is an authentication shortcut, never an authorization bypass

With `MYCELIC_DEMO_MODE=1` the seeded demo accounts can be signed into without a password
through `/api/demo/switch`. That issues an ordinary session for that user; every later check
is the production path. Demo tenants, users and data carry `is_demo=1` and the UI shows a
permanent banner. Simulated activity is a labelled job kind.

## D11. Independence of support is computed from source roots

Each evidence reference carries `source_root_id`, the content fingerprint of the original
source as computed by the holder (copies of one document share a root; a holder that cannot
determine the root sets `root_known=0`). A claim's independent support is the number of
distinct known roots across holders; copied references count once; references with unknown
roots are reported separately as *unknown independence* and never counted as independent.

Only **active** references count. A reference whose source was revised, retracted or became
unavailable is shown (`inactive_refs`) but never counted. A revision keeps the reference's original
root (its excerpt is the old content) and records the new version's root in `meta.revised_root_id`;
the new content enters as a new reference when a holder answers again.

The gate's per-reference classification (`CommitGate.effective_refs`: revoked or no-longer-authorized
holder → `context`; observed outside the validity window → `context`) is stored with the claim and
re-applied by every later recomputation (freshness sweep, verification, conflict resolution), so
evidence the gate demoted can never count again through another path.

Freshness is the time the content was **observed** (written, recorded, measured) or explicitly
re-confirmed by its holder (`meta.reconfirmed_at`) — never the upload or disclosure time.

## D12. Commit gate statuses

`hypothesis` (fewer independent roots than policy requires, or model-only synthesis),
`supported` (meets the policy's minimum independent roots, no open conflict, evidence fresh
enough), `contested` (an open conflict object references it), `stale` (evidence revised or
older than the freshness policy; re-verification scheduled), `retracted` (source retracted or
a person retracted it). Every transition writes a `revisions` row.

A claim whose supporting evidence changed at its source stays `stale` until re-verification brings
current evidence; the changed references then become `superseded` (kept for lineage, never counted).
A claim committed from evidence revised after the holder answered is born `stale`.

## D13. Bounded inquiry is enforced by the loop, not by the model

- **Targets, not text.** Verification, contradiction and deferred follow-up gaps are built
  deterministically with the id of what they target (`trigger.claim_id` / `trigger.conflict_id` /
  `trigger.followup_of`); a target is handled once a question about it exists (a stale claim once more
  after each time it became stale). The model (`identify_gap`) is consulted only for open coverage
  gaps and only once per distinct set of observations (a fingerprint stored in the loop's stats), so a
  quiet goal's scheduled checks cost no tokens. Targets that cannot become a question are retried only
  after the cooldown and at most three times.
- **Blind verification.** Every verification or contradiction question is written by
  `compose_verification_question` (topic only), tied to its target, routed away from the claim's
  supporting holders (unless its source changed and must be re-read), and rejected by
  `questions.create` if it states a number from the claim or six or more of its words in a row.
- **Concurrency.** `max_concurrent_questions` holds for every question the goal starts, including
  verification spawned at evaluation and follow-ups from synthesis; what does not fit is recorded on
  the question (`deferred_followups`, `verification_deferred`) and asked by a later tick.
- **Pause / stop.** Every question step checks the goal and its loop first. Pausing cancels queued
  question jobs and keeps the questions and the responses that still arrive; resuming re-queues each
  live question's next step (collection waits for the routes' remaining deadline). Stopping,
  completing or archiving cancels the live questions and revokes their open routes.
- **Disagreement.** A disagreement side that is already a committed finding *is* that finding (the
  majority statement becomes contested, never supported beside a copy); each distinct response set is
  committed once. Agreement is not transitive: when a model puts two responses that contradict each
  other into one finding, the side that is not the finding's own statement is committed on its own and
  contests it, so a finding never counts support from its own contradiction. An investigation retracts a record only when every holder behind it answered (a
  timeout or decline is not evidence) and the winning side brings a source root it did not already
  rest on; otherwise the conflict stays under investigation and the goal owner is asked to decide. A
  conflict closed as `unresolved` leaves both claims as hypotheses.
- **Routing failures** are recorded on the question with a cooldown; the loop becomes `blocked` only
  when no authorized holder serves its scope at all, and a blocked loop keeps its scheduled check.

## D14. Domains route through the tenant taxonomy; the coordinator never sees record content

- **One matching rule on both sides.** The coordinator (`Authorizer.can_route`) and the holder
  (`EvidenceStore._policy_reason`) both use `ingest.domains.domains_overlap` with the tenant's
  taxonomy (its `domain_taxonomy` / `domain_aliases` rows, or the default taxonomy). A domain matches
  its ancestors and descendants; flat legacy names resolve through aliases. Personal domains never
  route across the organization.
- **Domains are published as counts.** A holder reports `{domain_id: count}` for its ingested records
  in heartbeats (above its publication threshold, never personal domains). The coordinator adds known
  tenant domains to the holder's routable `domains`, so ingestion makes a holder reachable without an
  administrator retagging it; an owner can turn this off (`export_policy.auto_domains: false`).
- **The audience travels with the question.** The coordinator enumerates who may read a question's
  claims and sends that list with the question; the holder compares it with each source's members
  and withholds member-restricted records otherwise. The audience is fixed at routing time (see the
  limitation in INGESTION.md).
