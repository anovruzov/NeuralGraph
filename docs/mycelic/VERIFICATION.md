# Mycelic verification record

What was verified, how, and what was not. Three kinds of evidence are kept apart on purpose:

1. **Deterministic automated tests.** Reproducible on any machine, using the fake model and the hash embedder.
2. **The simulated end-to-end demonstration.** Real processes, a real HTTP API and real stores, with fictional data and
   the deterministic model.
3. **Live integrations.** These use real model providers, real third-party apps and a public deployment. A live item is
   marked **VERIFIED** only when it actually ran against the real service; otherwise it is **NOT VERIFIED**, with the
   reason.

Date of this record: 2026-10-08. Branch `claude/mycelic-implementation-vr034p`.

---

## 1. Deterministic automated tests

| Suite | Command | Result |
|---|---|---|
| Mycelic | `.venv/bin/python -m pytest mycelic/tests -q` | **277 passed, 2 skipped**. The two skips are the NATS JetStream tests, which need a server; see the next row. |
| Mycelic, NATS transport | `MYCELIC_TEST_NATS_URL=nats://127.0.0.1:4333 .venv/bin/python -m pytest mycelic/tests/test_transport.py -k nats` against a local `nats-server` 2.11.4 started with `-js` | **3 passed**: publish, subscribe and request round trip; a durable consumer survives a reconnect |
| NeuralGraph library | `.venv/bin/python -m pytest NeuralGraph/tests -q` | **222 passed, 1 skipped, 119 subtests passed** |
| Frontend types | `cd frontend && npx tsc --noEmit` (strict) | passes |
| Frontend build | `cd frontend && npm run build` | passes |

Coverage by area (test files under `mycelic/tests/`):

- **Identity and authorization (`test_api.py`).**
  - registration, invitations, sessions and API keys
  - scoped roles, tenant isolation (cross-tenant reads return 404), admin vs evidence access
  - question routing, raw-evidence grants
- **Holders (`test_holder.py`, `test_evidence.py`).**
  - separate stores and keys; signed envelopes, with tampering and replay rejected
  - export policies: disclosure levels, deny patterns; an invalid policy causes declines; title redaction
  - embedded holders and reconciliation with the registry
- **Knowledge and goals (`test_knowledge_goals.py`).**
  - commit-gate statuses, independent roots vs copies, conflicts, revisions
  - goal lifecycle, delegation, progress
- **Discovery loop (`test_loop_engine.py`).**
  - end to end with scripted holders: questions, routing, evaluation, commit, discoveries, follow-ups
  - replay, crash and restart without duplicates; isolation
  - quiet scheduled checks spend no tokens
- **Review regressions (`test_review_regressions.py`)**, one test per finding:
  - gate demotions are never recounted
  - revised evidence keeps its root and the claim waits for re-verification
  - a three-way disagreement contests the majority
  - no conflict resolution on a timeout
  - freshness is taken from observation time
  - blind verification never reveals the claim
  - pause and archive stop the pipeline
  - an unroutable question does not block a loop
  - concurrency caps hold
  - an "unresolved" conflict outcome and the sub-goal roll-up
  - metered embeddings, malformed citations, goal fields as the UI sends them
- **Transport (`test_transport.py`).**
  - SQLite outbox: at-least-once delivery, dedupe, cursors, retention
  - content scrubbing once the destination has handled a message
  - `reply_to` is covered by the signature
  - NATS JetStream (see above)
- **Models (`test_models.py`).**
  - the deterministic task catalogue; schema repair; data escaping against prompt injection
  - Anthropic SDK request building: capability rules, refusal fallback, timeouts
  - OpenAI-compatible provider; usage ledger and prices
- **Observability (`test_observability.py`).** Health, readiness, metrics, structured logs.
- **Seed (`test_seed.py`).**
  - the exact demonstration goal, labelled `[Demo]`
  - every hierarchy level and role, a team member in a cross-functional project, the second tenant
  - copied and stale evidence; idempotence
- **Ingestion, Phase A (`test_ingest_*.py`, `test_evidence_events.py`).** All offline; no third-party app is contacted.
  - canonical events, normalization, ACL inheritance and audience filtering, token encryption, the durable ingest queue
  - the taxonomy, personal domains, classification and corrections; the shard registry; holder migrations
  - deletion that purges content and derived text; revised, unavailable and restored evidence; kind caps on status
  - the coordinator side: question audience, routing by nested and aliased domains, domains published from heartbeats,
    and a finding that never counts support from its own contradiction
- **Integrations (`test_integrations_api.py`, `test_ingest_linking.py`, `test_extract.py`, `test_review_phase_a.py`).**
  - who may connect what: personal connectors for the owner only; unit connectors for leads or an org admin
  - sources, sync, disconnect with purge, the admin metadata view (no source names), audit without content
  - file paths confined to the holder's import directory; export uploads; the tenant taxonomy
  - webhooks: signature on the raw body, rejected signatures audited, deliveries de-duplicated, content-free notices
  - sealed credential transfer: opened once by the holder, never stored in outcomes, bound to the envelope
  - cross-app linking: shared entities across apps, modality of edges, restricted edges not traversable, deletion
    withdraws evidence
  - "why this domain": record domains with method and evidence, sticky corrections
  - uploads: DOCX, PDF, CSV, JSON and JSONL with their bounds
  - one regression test per finding of the independent Phase A review
- **Scenario (`test_scenario.py`).** Runs the whole simulated demonstration below as one test, in about 8 seconds.

## 2. Simulated end-to-end demonstration: `python -m mycelic scenario`

**Setup.** The run uses its own data directory and:
- the API on a random port;
- the discovery worker;
- two **separate holder processes** (Elin's and Marcus's), each with its own SQLite store and key, launched as
  subprocesses;
- eight embedded holders, the SQLite transport, the deterministic model and hash embeddings.

All data is fictional (DEMO_SCENARIO.md).

**Result: 13 of 13 checks pass.** It passed on each of the last 3 consecutive fresh runs after the ingestion integration, and inside `pytest` (`test_scenario.py`). `--data-dir` must be new or empty: a reused directory is refused, because the idempotent seed would keep the earlier holder keys.

| Check | What it proves |
|---|---|
| a | An authorized regional lead creates and activates the demo goal through the API; the loop is on by default. |
| b | The loop generates and routes a relevant question automatically, with a heuristic priority breakdown. |
| c | Separate holder processes answer from their own stores: distinct PIDs, files and keys, and opaque reference ids only. |
| d | A supported discovery with two or more independent roots, and a useful follow-up question with its lineage. |
| e | Each level sees the appropriate abstraction: employee → team → department → subsidiary → region → executive; the admin sees no evidence. |
| f | Private evidence is inaccessible. The owner gets raw text from the holder process; an artifact grant gets 403; the admin gets 403; the other tenant gets 404 everywhere. |
| g | Copied evidence does not inflate support: 3 references, 2 independent roots, 1 copied. |
| h | A source revision (through the holder's local API) marks the reference revised and the claim stale, and schedules a blind re-verification. |
| i | A worker crash after writing: the lease is lost, the job is replayed, and nothing is duplicated (claims, discoveries). |
| j | Pause, resume, budget exhaustion (with its renewal time), restore and stop, through the loop endpoints. |
| k | The worker makes progress with no HTTP client connected, within budget, with a healthy loop, no dead jobs and no job errors. |
| l | A fresh login inspects the discovery and its permitted lineage; a team employee gets 403. |
| m | GitHub and Slack, connected organization-wide by the Platform lead to the Platform team's memory **against offline mocks of both APIs**: both apps are ingested; the Slack thread and issue #482 share entities (`component:httpclient`, `version:httpclient@4.2`, `service:checkout`, `symptom:timeout`); a question gets one answer whose claim cites both apps with 3 independent roots (supported) and a discovery; a tampered Slack event gets 401, the signed edit is routed to the holder, the claim goes `supported → stale → supported` through a blind re-verification question. This is not a live verification of GitHub or Slack. |

## 3. Container and browser verification (local)

**Image**
- `docker build -f deploy/mycelic/Dockerfile` builds a 415 MB image.
- Docker Hub returned HTTP 429 to anonymous pulls here, so the base images came through Google's Docker Hub mirror,
  using the new `NODE_IMAGE` / `PYTHON_IMAGE` build arguments.
- The egress proxy's CA was passed as a BuildKit secret. The image history contains no proxy URL and no CA.

**Single-node container** (`serve` with the in-process worker, embedded holders, SQLite transport and demo mode):
- `/healthz` → 200; `/readyz` → ok, with the worker heartbeat and no pending migrations.
- `seed` ran after start-up. The API picked up the new holders through the new registry reconciliation; without it the
  holders never answered (a bug found here and fixed).
- The loop then produced 13 holder answers, committed questions, supported claims, and discoveries at team and region
  level.

**Browser** (Chromium via Playwright, signing in through the UI as Elin, Petra, Ingrid and Tomas):
- Every visited page renders its heading: home, private memory, goals, the demo goal, a discovery, network,
  executive, administration.
- No horizontal scrolling at 390 px width.
- Bugs found and fixed:
  - the goal page crashed, because the baseline and measurement source objects were rendered as text;
  - the top bar overflowed at phone width;
  - goal-form values (string targets, `up`/`down`) prevented any progress calculation for goals created in the UI.
- One expected 401 remains: the login page probes `/api/auth/me` before anyone has signed in.

**Distributed topology** (`deploy/mycelic/docker-compose.yml`, containers: NATS JetStream 2.11, API hosting the
embedded holders, standalone worker, and standalone `holder-a` and `holder-b`, each with its own volume and NATS user):
- Started from clean volumes and seeded with `docker compose exec api python -m mycelic seed`.
- Both standalone holders answered questions over NATS (3 answers each). 13 questions were committed; discoveries were
  produced at team, region and executive level; no job died or recorded an error.
- With holder A's credentials, the NATS server refused a subscription to holder B's subjects, a publish into holder
  B's inbox, and stream creation.
- Six deployment defects were found on the way and fixed:
  1. **Password variable names.** `nats.conf` referenced password variables the compose file did not pass, including the
     system account's, so the server would not start.
  2. **NATS users and subjects.** `nats.conf` permitted the users `holder_a`/`holder_b` on subjects of holder ids
     `holder_a`/`holder_b`. Compose connects as `holder-a`/`holder-b` with holder ids `hold_demo_a`/`hold_demo_b`.
  3. **Duplicate server flags.** The compose command repeated `-sd`/`-m` from the config, which nats-server 2.11 rejects.
  4. **Stream ownership.** Holder processes tried to create the stream, which their NATS user may not do; they now only
     check that it exists.
  5. **Embedded holders.** No process hosted them in this topology; the API now does.
  6. **Demo hooks and documents.**
     - The standalone worker lacked the demonstration's job handler.
     - The seed left the standalone holders' documents undelivered; it now sends them over the durable transport.
  - Hardening: a holder (embedded or standalone) whose tenant or signing key changes in the registry restarts with a
    fresh bootstrap.

**Integrations UI** (Chromium on a freshly seeded instance, Elin then Tomas):
- Connected apps: connecting the local export connector through the drawer, reviewing and including its source, and
  syncing showed 3 records.
- Records and domains: each record shows its domains; the drawer explains the domain (method, rationale,
  confidence), the cross-app entities and the typed edges with their modality.
- The admin Integrations (connector metadata) and Domains (taxonomy) pages render.
- No horizontal scrolling at 390 px; no console errors besides the login page's expected session probe.

**Simulated demo apps in the container** (`docker-compose.local.yml` with `MYCELIC_DEMO_APPS=1`, rebuilt image, clean
volume, `seed` run next to the server):
- The server hosts the GitHub and Slack mocks on loopback and connects the Platform team memory, organization-wide, as
  Priya: 12 GitHub and 10 Slack records, both connections named "simulated demo data", no dead queue items.
- With each included source mapped to a domain (repo → `engineering`, `#deployments` → `infrastructure.ci-cd`), the
  holder published both as routable domains (8 and 9 records) through its heartbeat.
- Signed in as Priya: Connected apps, the records list and the record drawer render. Signed in as Tomas: the admin
  Integrations and Domains pages render.
- Bugs found here and fixed:
  - `mycelic seed` running next to the server also tried to host the mocks and failed on the taken ports; only the
    server process hosts them now;
  - long URLs in record snippets widened the page at 390 px; card-table cells now wrap;
  - without a source mapping, 11 of 17 classified records stayed `unclassified` under the deterministic classifier
    (hash embeddings, fake model), so no domain reached the publication threshold (see limitations).

## 4. Live integrations

| Item | Status | Why / what is needed |
|---|---|---|
| Anthropic models (`anthropic:` tiers) | **NOT VERIFIED** | No `ANTHROPIC_API_KEY` in this environment. Request construction is unit-tested against the SDK, but no live call was made. |
| OpenAI-compatible models / embeddings | **NOT VERIFIED** | No key or endpoint configured. |
| NATS JetStream transport | **VERIFIED locally** | A real `nats-server` 2.11 with JetStream, in the transport tests and in the full distributed compose topology (section 3). This was on one machine, not across hosts. |
| Public deployment (Fly.io / Render configs in `deploy/mycelic/`) | **NOT DEPLOYED** | No Fly or Render credentials are configured. AWS credentials exist in the environment but are not an authorized target for this application; using them needs the owner's explicit decision. |
| GitHub connector | **NOT VERIFIED (live)**; tested offline | REST, webhooks, OAuth with PKCE and ACL mapping are exercised against a mock built from GitHub's published OpenAPI description (`github_openapi_subset.json`); a live test runs only with `MYCELIC_LIVE_GITHUB_TOKEN` and `MYCELIC_LIVE_GITHUB_REPO`, which are not set here. |
| Slack connector | **NOT VERIFIED (live)**; tested offline | Web API, Events API signatures (Slack's documented test vector), OAuth v2 with PKCE and rate-limit classes are exercised against a mock; a live test needs `MYCELIC_LIVE_SLACK_TOKEN` and `MYCELIC_LIVE_SLACK_CHANNEL`. |
| Gmail connector | **NOT VERIFIED (live)**; tested offline | History API sync, label-scoped sources (spam, trash and drafts excluded; the inbox never auto-included), Pub/Sub push with a URL token (through the API), OAuth with PKCE, Google's quota errors, forwards deduplicated with their original, against a mock; a live test needs `MYCELIC_LIVE_GMAIL_TOKEN` and `MYCELIC_LIVE_GMAIL_LABEL`. Not implemented: Pub/Sub OIDC JWT verification, automatic `users.watch` renewal (polling keeps it current). |
| Google Drive connector | **NOT VERIFIED (live)**; tested offline | Shared drives and folders, Docs export and PDF/DOCX/CSV/JSON extraction, the changes feed, moves and trash as deletions, permissions (anyone/domain, users, groups as membership references), against a mock; a live test needs `MYCELIC_LIVE_DRIVE_TOKEN` and `MYCELIC_LIVE_DRIVE_FOLDER`. Not implemented: creating or renewing `changes.watch` channels from the API (polling keeps it current), Sheets, Slides, images, revisions. |
| Other apps (Teams, Outlook, SharePoint, OneDrive, Notion, Confluence, Jira, Linear, Salesforce, Calendar, Docs, databases, S3) | **NOT IMPLEMENTED** | Listed as planned (scaffolds) in the catalog and refused when connecting. |

## 5. Known limitations

- **Simulated, not live.** Discovery quality with real models is untested: the fake model follows documented
  deterministic rules, not judgement.
- **Hash embeddings are bag-of-words.** Semantic retrieval quality with a real embedding model is not measured here.
- **Domain classification of short messages.** In deterministic mode (hash embeddings, fake model) rules accept a
  domain only with several keyword hits, so short chat messages and comments often stay `unclassified` unless their
  source is mapped to a domain. Classification accuracy with a real embedding model and a real light-tier model has
  not been measured.
- **Single-node scale has not been benchmarked under load.** Scaling limits are documented in RUNBOOK.md.
- **Browser coverage.** The UI was exercised with Chromium only, at desktop width and at 390 px.
