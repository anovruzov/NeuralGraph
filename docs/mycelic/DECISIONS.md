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

## D12. Commit gate statuses

`hypothesis` (fewer independent roots than policy requires, or model-only synthesis),
`supported` (meets the policy's minimum independent roots, no open conflict, evidence fresh
enough), `contested` (an open conflict object references it), `stale` (evidence revised or
older than the freshness policy; re-verification scheduled), `retracted` (source retracted or
a person retracted it). Every transition writes a `revisions` row.
