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
| Mycelic | `.venv/bin/python -m pytest mycelic/tests -q` | **161 passed, 2 skipped**. The two skips are the NATS JetStream tests, which need a server; see the next row. |
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
- **Scenario (`test_scenario.py`).** Runs the whole simulated demonstration below as one test, in about 8 seconds.

## 2. Simulated end-to-end demonstration: `python -m mycelic scenario`

**Setup.** The run uses its own data directory and:
- the API on a random port;
- the discovery worker;
- two **separate holder processes** (Elin's and Marcus's), each with its own SQLite store and key, launched as
  subprocesses;
- eight embedded holders, the SQLite transport, the deterministic model and hash embeddings.

All data is fictional (DEMO_SCENARIO.md).

**Result: 12 of 12 checks pass.** It passed on every run of the last 8 consecutive runs, and inside `pytest` (`test_scenario.py`).

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

## 4. Live integrations

| Item | Status | Why / what is needed |
|---|---|---|
| Anthropic models (`anthropic:` tiers) | **NOT VERIFIED** | No `ANTHROPIC_API_KEY` in this environment. Request construction is unit-tested against the SDK, but no live call was made. |
| OpenAI-compatible models / embeddings | **NOT VERIFIED** | No key or endpoint configured. |
| NATS JetStream transport | **VERIFIED locally** | Real `nats-server` 2.11.4 with JetStream, single node (section 1). The distributed compose topology was not brought up. |
| Public deployment (Fly.io / Render configs in `deploy/mycelic/`) | **NOT DEPLOYED** | No Fly or Render credentials are configured. AWS credentials exist in the environment but are not an authorized target for this application; using them needs the owner's explicit decision. |
| Third-party app connectors (GitHub, Slack, Gmail, Drive, …) | **NOT IMPLEMENTED YET** | Design in `INGESTION.md`; implementation is in progress (Phase A). No platform is claimed as supported. |

## 5. Known limitations

- **Simulated, not live.** Discovery quality with real models is untested: the fake model follows documented
  deterministic rules, not judgement.
- **Hash embeddings are bag-of-words.** Semantic retrieval quality with a real embedding model is not measured here.
- **Single-node scale has not been benchmarked under load.** Scaling limits are documented in RUNBOOK.md.
- **Browser coverage.** The UI was exercised with Chromium only, at desktop width and at 390 px.
