# Mycelic setup

How to run Mycelic on a laptop, connect real models, switch on NATS, run holders on other machines and work on
the frontend. Operations (backups, health, upgrades) are in [RUNBOOK.md](RUNBOOK.md); the container and
cloud files are in [`deploy/mycelic/`](../../deploy/mycelic/README.md).

Every command below is `python -m mycelic <command>`:

| Command | What it does |
|---|---|
| `serve` | API + SSE + static UI. With `MYCELIC_RUN_WORKER_IN_API=1` (default) also the discovery worker; with `MYCELIC_EMBEDDED_HOLDERS=1` (default) holders run in-process. |
| `worker` | Discovery worker only (shares `coord.db` with the API). |
| `holder --holder-id ID --key KEY --core-url URL --data-dir DIR [--local-port N]` | A standalone evidence holder: its own store, its own credential. |
| `migrate [--status]` | Apply pending schema migrations to `coord.db`, or show applied/pending. |
| `seed [--tenant demo] [--reset]` | Create the demonstration organization (people, units, holders, goals, evidence). |
| `scenario` | Run the verification scenario end to end and print a report. |
| `backup --out FILE.tar.gz` / `restore --from FILE.tar.gz [--force]` | Consistent online backup of `coord.db` and every holder `evidence.db`; restore refuses to overwrite unless forced. |
| `health` | Readiness check from the command line (exit code 0 when ready). |

## 1. Local setup (no Docker, no model, no NATS)

Prerequisites: Python 3.11+ (macOS ships 3.9; use `python3.11`), Node 22+ only if you build or develop the
frontend.

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
python3 -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-mycelic.txt                      # aiohttp, numpy, rank-bm25, nats-py, pytest

python -m mycelic migrate            # creates ~/.mycelic/coord.db and applies mycelic/db/migrations/*.sql
python -m mycelic seed               # the demo organization (tenant "demo"), holders and evidence
python -m mycelic serve              # http://127.0.0.1:8780
```

With nothing configured this runs the `fake` model provider (deterministic), `hash` embeddings, the SQLite
transport, embedded holders and the in-process worker. Nothing leaves the machine.

Open **http://127.0.0.1:8780**. In demo mode (`MYCELIC_DEMO_MODE=1`, the default) the login page offers the
seeded personas: pick an employee, a team lead, a department lead, an executive or the administrator, and
switch between them from the banner at the top of every page (`POST /api/demo/switch`). Each switch issues a
real session for that account; every permission check afterwards is the production path, so what a persona
sees is exactly what that role would see. The banner is permanent for demo data.

The UI needs the built frontend. `serve` serves `frontend/dist` when it exists; until the frontend is built
(section 6, or the Docker image which builds it) the API answers under `/api/*` and `/` reports that the UI is
not built.

Useful next steps:

```bash
python -m mycelic scenario           # the verification scenario: prints what was asked, routed, verified, committed
python -m mycelic migrate --status   # schema version
python -m mycelic health             # readiness from the shell
.venv/bin/python -m pytest mycelic/tests -q
```

### Data directory

```
~/.mycelic/                          MYCELIC_DATA_DIR
  coord.db  (+ -wal, -shm)           coordination DB: identities, org, goals, questions, claims, jobs, events, audit, usage
  holders/<holder_id>/evidence.db    one NeuralGraph store per embedded holder: raw documents and memories
```

`MYCELIC_COORD_DB` and `MYCELIC_HOLDERS_DIR` override the two paths. A standalone holder (section 5) keeps its
store at `<--data-dir>/<holder_id>/evidence.db`, and `--data-dir` defaults to `<MYCELIC_DATA_DIR>/holders`, so
a holder machine ends up with the same `holders/<holder_id>/evidence.db` layout minus `coord.db`. Delete the
directory to start over (`seed --reset` only resets the demo tenant's data).

### Configuration

Everything is an environment variable with a default; [`deploy/mycelic/.env.example`](../../deploy/mycelic/.env.example)
lists all of them with one-line explanations. For a shell session:

```bash
cp deploy/mycelic/.env.example deploy/mycelic/.env     # edit
set -a; source deploy/mycelic/.env; set +a
python -m mycelic serve
```

Production minimum: `MYCELIC_DEMO_MODE=0`, `MYCELIC_SECRET_KEY` set (`python -c "import secrets;print(secrets.token_urlsafe(32))"`),
`MYCELIC_PUBLIC_URL` and `MYCELIC_ALLOWED_HOSTS` set to the public host, `MYCELIC_SECURE_COOKIES=1` behind TLS,
real model tiers (section 2), and a backup schedule (RUNBOOK).

## 2. Model providers

Model work is routed by tier (`light` drafting and classification, `standard` evaluation and verification,
`heavy` synthesis). Each tier is `provider:model`; providers are `fake`, `anthropic` and `openai`
(any OpenAI-compatible server). Keys stay on the server: the UI only ever sees provider and model names, and
every call is recorded in `model_usage` with its cost (Admin > Models, Admin > Budgets).

### Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export MYCELIC_MODEL_LIGHT=anthropic:claude-haiku-4-5
export MYCELIC_MODEL_STANDARD=anthropic:claude-opus-5
export MYCELIC_MODEL_HEAVY=anthropic:claude-opus-5
```

`claude-sonnet-5` is the usual choice for `standard` when volume matters more than depth. The Anthropic API
has no embedding endpoint, so embeddings come from section "Embeddings" below (the `hash` default works
without one).

### OpenAI-compatible (OpenAI, Ollama, LM Studio, vLLM, ...)

```bash
export OPENAI_API_KEY=sk-...                          # Ollama and LM Studio accept any non-empty value
export OPENAI_BASE_URL=https://api.openai.com/v1      # Ollama: http://127.0.0.1:11434/v1   LM Studio: http://127.0.0.1:1234/v1
export MYCELIC_MODEL_LIGHT=openai:gpt-4.1-mini
export MYCELIC_MODEL_STANDARD=openai:gpt-4.1
export MYCELIC_MODEL_HEAVY=openai:gpt-4.1
```

Fully local with Ollama:

```bash
ollama pull qwen2.5:7b-instruct && ollama pull nomic-embed-text
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_API_KEY=ollama
export MYCELIC_MODEL_LIGHT=openai:qwen2.5:7b-instruct MYCELIC_MODEL_STANDARD=openai:qwen2.5:7b-instruct MYCELIC_MODEL_HEAVY=openai:qwen2.5:7b-instruct
export MYCELIC_EMBED_PROVIDER=openai MYCELIC_EMBED_MODEL=nomic-embed-text
```

Set `OLLAMA_NUM_PARALLEL` at least as high as `MYCELIC_MODEL_MAX_PARALLEL` (default 4) or calls queue inside
Ollama. Tiers can mix providers (`light` on a local model, `heavy` on Anthropic).

### Embeddings

`MYCELIC_EMBED_PROVIDER=hash` (default) is deterministic and needs no network; retrieval then relies more on
FTS and the entity graph. `MYCELIC_EMBED_PROVIDER=openai` uses `OPENAI_BASE_URL/embeddings` with
`MYCELIC_EMBED_MODEL` (`text-embedding-3-small` on OpenAI, `nomic-embed-text` on Ollama). Changing the
embedding model after evidence has been ingested changes the vector space: re-ingest, or keep the model.

### Timeouts, parallelism, budgets

`MYCELIC_MODEL_TIMEOUT_SECONDS` (120) and `MYCELIC_MODEL_MAX_PARALLEL` (4) bound every provider. Per-goal
budgets (`tokens`, `usd`, `questions`, `followup_depth`) are set on the goal; a loop that exhausts its budget
stops with state `budget_exhausted` and says so (RUNBOOK, "Budget exhaustion").

## 3. NATS mode

The SQLite transport is an outbox table in `coord.db`: perfect for one machine, useless across machines. NATS
JetStream is the transport when holders run elsewhere.

```bash
# server (or the nats service in deploy/mycelic/docker-compose.yml)
export NATS_CORE_PASSWORD=... NATS_HOLDER_A_PASSWORD=... NATS_HOLDER_B_PASSWORD=...
nats-server -c deploy/mycelic/nats.conf -js -sd /var/lib/nats

# api and worker
export MYCELIC_TRANSPORT=nats MYCELIC_NATS_URL=nats://nats.internal:4222
export MYCELIC_NATS_USER=core MYCELIC_NATS_PASSWORD=$NATS_CORE_PASSWORD
export MYCELIC_EMBEDDED_HOLDERS=0
python -m mycelic serve
```

One stream (`MYCELIC_NATS_STREAM`, default `MYCELIC`) carries every subject under `mycelic.>`; durable pull
consumers give at-least-once delivery, `Nats-Msg-Id` de-duplicates, and `nats.conf` gives each holder user
permission only for its own inbox/ingest/raw subjects plus the shared response subjects. Retention is
`MYCELIC_TRANSPORT_RETENTION_SECONDS` (7 days): the stream is a transport, not a memory; every effect is
committed to `coord.db` or a holder store before a message is acknowledged.

`docker compose up` in `deploy/mycelic/` runs this topology with NATS, the API, a worker and two holders on
separate volumes (`deploy/mycelic/up.sh`).

## 4. Registering holders

A holder is an evidence store with its own credential. Embedded holders (`MYCELIC_EMBEDDED_HOLDERS=1`) are
created from the UI (My memory > New holder, or Admin > Integrations for unit holders) and live under
`holders/` in the data directory. External holders are registered the same way with `mode: external`:

```bash
curl -s -X POST http://127.0.0.1:8780/api/holders -H 'Authorization: Bearer <session or mk_ key>' \
     -H 'Content-Type: application/json' -H 'Origin: http://127.0.0.1:8780' \
     -d '{"name":"Ada laptop","mode":"external","domains":["sales","emea"]}'
# -> {"holder": {"holder_id": "hold_...", ...}, "key": "..."}      the key is shown once
```

The demo seed registers two external holders (`hold_demo_a`, `hold_demo_b`) using `MYCELIC_HOLDER_KEY_A/B`
from the environment when set, which is what the compose file relies on.

## 5. Standalone holders

On the holder's machine (the employee's laptop, a team server):

```bash
pip install -r requirements-mycelic.txt
export MYCELIC_TRANSPORT=nats MYCELIC_NATS_URL=nats://nats.internal:4222
export MYCELIC_NATS_USER=holder-a MYCELIC_NATS_PASSWORD=...
export MYCELIC_HOLDER_KEY='<key shown once>'      # keep the key out of argv (visible to every local user via ps)
python -m mycelic holder --holder-id hold_... \
       --core-url https://mycelic.example.com --data-dir ~/.mycelic-holder/holders --local-port 8781
```

Pass the key through `MYCELIC_HOLDER_KEY`, not `--key`: command-line arguments are readable by other local users.
If you must use the flag, write `--key=VALUE` (a key may start with `-`). Every flag has an environment fallback:
`MYCELIC_HOLDER_ID`, `MYCELIC_HOLDER_KEY`, `MYCELIC_CORE_URL`,
`MYCELIC_HOLDER_DATA_DIR`, `MYCELIC_HOLDER_LOCAL_PORT`, `MYCELIC_HOLDER_LOCAL_HOST` (the compose file uses
them). `--data-dir` defaults to `<MYCELIC_DATA_DIR>/holders`; the store is `<data-dir>/<holder_id>/evidence.db`.

On start the process fetches its bootstrap from the core (`GET /api/holders/{id}/bootstrap`, bearer = holder
key: tenant, route key, export policy, domains, heartbeat interval, and optionally transport settings that
override the local `MYCELIC_TRANSPORT`/`MYCELIC_NATS_*` variables), joins the transport and subscribes to its
inbox. Every 20 s it posts `POST /api/holders/{id}/heartbeat` with its stats; the reply may carry an updated
export policy, applied at once. Questions arrive as envelopes signed with the holder's route key; the holder
verifies the signature, retrieves from its own store, applies its export policy and publishes a response that
carries only what the policy allows. On SIGTERM it sends an `offline` heartbeat and exits.

`--local-port` starts a local-only API (bound to `--local-host`, default 127.0.0.1; bearer = holder key) so
the owner can add and search evidence on the machine that holds it: `POST /documents`, `GET /documents`,
`GET /search?q=&k=`, `GET /stats`. Without it, documents are added through the core UI
(`POST /api/holders/{id}/documents`) and forwarded over the transport.

With `MYCELIC_TRANSPORT=sqlite` a standalone holder must share `coord.db` with the API (same volume,
`MYCELIC_COORD_DB`); that is only useful for testing the holder process itself.

Rotating or revoking a holder's key: RUNBOOK, "Rotating holder keys" and "Revoking access".

## 6. Frontend

The UI is a React + Vite + TypeScript application in `frontend/`, built into `frontend/dist` and served by
`serve`.

```bash
cd frontend
npm ci
npm run build                        # -> frontend/dist, picked up by `python -m mycelic serve`
```

Development with hot reload:

```bash
# terminal 1: API on 8780
python -m mycelic serve
# terminal 2: Vite on 5173; its dev server proxies /api, /healthz and /readyz to 127.0.0.1:8780 (frontend/vite.config.ts)
cd frontend && npm run dev
```

Open http://localhost:5173. Sessions are cookies and state-changing requests pass a same-origin check, so
the dev server proxies the API with the `Host` header preserved (`changeOrigin: false`): the browser and the
API agree on the origin and no `MYCELIC_CORS_ORIGINS` entry is needed. Only when a page on another origin
calls the API directly must that origin be listed in `MYCELIC_CORS_ORIGINS`.

## 7. Docker

```bash
deploy/mycelic/up.sh local           # one container, one volume; http://localhost:8780
deploy/mycelic/up.sh                 # NATS + api + worker + holder-a + holder-b
docker build -f deploy/mycelic/Dockerfile -t mycelic:local .     # the image alone (from the repo root)
```

The image builds the frontend, so no Node toolchain is needed on the host. `up.sh` writes `deploy/mycelic/.env`
from the template with generated secrets on the first run; add model keys there. The data lives in named
volumes (`mycelic-data`, `holder-a-data`, `holder-b-data`, `nats-data`). Seed the demo inside the container:

```bash
docker compose -f deploy/mycelic/docker-compose.yml exec api python -m mycelic seed
```

Cloud targets: `deploy/mycelic/fly.toml` (Fly.io, one machine + volume) and `deploy/mycelic/render.yaml`
(Render, one web service + disk); the comments in each file are the deployment instructions.
