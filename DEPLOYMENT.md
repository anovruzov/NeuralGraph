# Deploying Mycelic

Mycelic is infrastructure for distributed agent memory: agents keep their notes locally, share the ones
worth propagating, and the organization gets aggregated, retrievable knowledge with reconstructable lineage.
This page is the operator's manual: exact commands, the production topology, configuration, recovery
procedures and the Kubernetes path. What runs is described in
[`docs/MYCELIC_ARCHITECTURE.md`](docs/MYCELIC_ARCHITECTURE.md); the security boundary in
[`SECURITY.md`](SECURITY.md).

Everything below was executed while building this release: the Docker Compose deployment, the process
deployment, the smoke test against both, the 99-agent run, and the demo. The Kubernetes manifests were
rendered with `kubectl kustomize` but **not applied to a cluster**.

## 1. One-command local deployment (Docker Compose)

Prerequisites: Docker 24+ with Compose v2 (`docker compose version`), `git`, `openssl`.

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph

# 1. configure: every secret is required; compose refuses to start with one missing
cp deploy/mycelic/.env.example deploy/mycelic/.env
sed -i "s|^MYCELIC_ADMIN_TOKEN=.*|MYCELIC_ADMIN_TOKEN=$(openssl rand -hex 32)|" deploy/mycelic/.env
sed -i "s|^MYCELIC_EVENT_SIGNING_KEY=.*|MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32)|" deploy/mycelic/.env
sed -i "s|^NATS_PASSWORD=.*|NATS_PASSWORD=$(openssl rand -hex 32)|" deploy/mycelic/.env

# 2. run
docker compose -f deploy/mycelic/docker-compose.yml up -d --build

# 3. check
curl -s http://localhost:8080/health
# {"status": "ok", "version": "0.1.0", "transport_connected": true, "consumer_running": true}
```

`docker compose ... config` shows the resolved configuration and fails on any missing variable before
anything starts. The stack is two containers and two named volumes:

| Service | Image | Data | Ports |
|---|---|---|---|
| `nats` | `nats:2.10.29-alpine` (pinned in `.env`); runs as root, as the official image ships (the Kubernetes StatefulSet runs it as uid 1000) | volume `nats-data` (JetStream file store) | 4222 internal only; the monitor (8222) listens on the container's loopback |
| `mycelic` | built from `deploy/mycelic/Dockerfile`, runs as uid 10001 | volume `mycelic-data` (`/data/mycelic.db`) | `${MYCELIC_BIND_ADDRESS:-127.0.0.1}:${MYCELIC_PORT}` → 8080, plain HTTP; set `MYCELIC_BIND_ADDRESS=0.0.0.0` only behind the TLS proxy of section 2 |

Optional Prometheus (`--profile observability`) scrapes `/metrics` with `MYCELIC_METRICS_TOKEN`
(set it in `.env`; it is passed as a compose secret) and listens on `127.0.0.1:9090`.

Building behind a TLS-inspecting corporate proxy: set `CA_BUNDLE_FILE=/path/to/corporate-ca.pem` in `.env`;
the Dockerfile mounts it as a build secret for `pip` only. Leave it at `/dev/null` otherwise.

### Register the first agent and connect it

Agents authenticate with per-agent API keys; the administrator token registers them.

```bash
export MYCELIC_URL=http://localhost:8080
export MYCELIC_ADMIN_TOKEN=$(grep ^MYCELIC_ADMIN_TOKEN= deploy/mycelic/.env | cut -d= -f2)

python -m mycelic register-agent --enterprise northwind --region emea --subsidiary nw-gmbh \
    --department ops --team logistics --agent-id logistics-1
# registered logistics-1 at northwind/emea/nw-gmbh/ops/logistics/logistics-1
# MYCELIC_API_KEY=mk_logistics-1.…        (shown once)
```

The same call over HTTP: `POST /admin/agents` with `{"enterprise","region","subsidiary","department","team","agent_id"}`.
Missing intermediate units default to the enterprise name, so a small organization can register agents with
`--enterprise acme --team support --agent-id bot-1`.

From the agent's side (Python, no dependencies beyond the standard library):

```python
from mycelic.sdk import MycelicClient, LocalMemory

local = LocalMemory("~/.mycelic/logistics-1.db")             # private notes on the agent's own disk
client = MycelicClient("http://localhost:8080", api_key="mk_logistics-1.…")

note = local.note("Port of Rotterdam terminal 3 strike announced for weeks 41-43",
                  topic="supply:sd-9/transport", slot="transport_disruption", entity="sd-9", confidence=0.9)
local.share(client, note)                                     # idempotent: safe to retry after a crash

res = client.query("delivery risk for RX-4 this quarter", scope="northwind")
print(res["answer"]["text"], res["answer"]["lineage"])
graph = client.lineage(res["answer"]["memory_id"])
```

Or from the shell: `python -m mycelic query "delivery risk sd-9" --scope northwind --lineage`
(`MYCELIC_API_KEY` set). A complete agent process is `python -m mycelic.sdk.agent --help`.

Claude Code / any MCP client (the agent key is the Bearer token; identity is per request):

```bash
claude mcp add --transport http mycelic http://localhost:8080/mcp --header "Authorization: Bearer $MYCELIC_API_KEY"
# stdio for desktop clients:
python -m mycelic mcp --url http://localhost:8080 --api-key "$MYCELIC_API_KEY"
```

Tools: `mycelic_query`, `mycelic_remember`, `mycelic_lineage`, `mycelic_get_memory`, `mycelic_status`.

### Run the canonical demonstration

```bash
# the production smoke test against the compose stack you just started (keeps your data: it uses its own project)
python tests/smoke/mycelic_smoke.py --driver compose
# the narrated demo (local processes need the nats-server binary on PATH: https://github.com/nats-io/nats-server/releases)
python demo/mycelic_demo.py --driver compose
```

The smoke test registers agents in three teams, runs them as separate processes, checks the enterprise
conclusion and its lineage, then kills the service, kills the broker, deletes the service database, and
verifies that the same knowledge and lineage come back. It exits 0 only when every step held. It passed in
this repository's CI sandbox with both drivers (6 agents: 8 s process / 25 s compose; 99 agents: 19 s process).

## 2. Production topology

```
                    ┌────────────────────────── your network boundary ──────────────────────────┐
 agents (HTTPS) ──► │ reverse proxy / Ingress (TLS) ──► mycelic:8080 ──► nats:4222 (internal only) │
 operators, MCP ──► │                                   │ /data (SQLite)      │ /data (JetStream)   │
                    └───────────────────────────────────┴─────────────────────┴───────────────────┘
```

* **One Mycelic instance per deployment.** State is a single SQLite file with one writer; the durable
  consumer name (`MYCELIC_NATS_CONSUMER`) is bound to that instance. Do not run two replicas against one
  database or one consumer. Vertical capacity is what this slice targets: 10–100 agents, low thousands of
  memories per day. The scale-out path (Postgres for the read model, one consumer per shard) is not part of
  this release.
* **One NATS server** with a file-backed JetStream store. A 3-node JetStream cluster is a drop-in change on
  the broker side (`num_replicas` is a stream setting; Mycelic sets 1) and is not covered by this release.
* **TLS.** Terminate at a reverse proxy or Ingress and set `MYCELIC_ALLOWED_HOSTS` to its hostname and
  `MYCELIC_TRUST_PROXY_HEADERS=true` (with `MYCELIC_TRUSTED_PROXY_HOPS` = the number of proxies that append
  to `X-Forwarded-For`, default 1) so rate limiting sees client addresses; only do this when the proxy is the
  only thing that can reach the service. Alternatively let Mycelic serve TLS itself with
  `MYCELIC_TLS_CERT_FILE` / `MYCELIC_TLS_KEY_FILE`; this is not wired into the shipped compose file or
  manifests: pass the two variables and mount the certificate and key (compose: `environment` + `volumes`;
  Kubernetes: a Secret volume on the StatefulSet) and set `scheme: HTTPS` on the three probes in
  `mycelic-statefulset.yaml`, otherwise the pod never becomes Ready. For the broker, `tls://` in
  `MYCELIC_NATS_URL` plus `MYCELIC_NATS_CA_FILE` enables verified TLS (configure `tls {}` in `nats.conf`).
* **Secrets** come only from the environment (`.env`, Kubernetes Secret). Values that look like
  placeholders are refused at start. `MYCELIC_ADMIN_TOKEN` is mandatory on any non-loopback bind.

## 3. Configuration reference

All variables have the `MYCELIC_` prefix. Validation runs at start and fails fast with the variable name.

| Variable | Default | Meaning |
|---|---|---|
| `HOST`, `PORT` | `0.0.0.0`, `8080` (image) | bind address |
| `DB_PATH` | `/data/mycelic.db` | SQLite file (WAL, `synchronous=FULL`) |
| `INSTANCE_ID` | `mycelic-main` | connection name |
| `LOG_LEVEL` | `INFO` | |
| `NATS_URL` | `nats://nats:4222` | empty disables the broker (in-process transport, development only) |
| `NATS_USER`, `NATS_PASSWORD`, `NATS_TOKEN`, `NATS_CA_FILE` | | broker credentials and CA; credentials in the URL are refused |
| `NATS_STREAM`, `NATS_CONSUMER` | `MYCELIC`, `mycelic-main` | stream and durable consumer names |
| `NATS_MAX_AGE_SECONDS`, `NATS_MAX_BYTES` | `0`, `-1` | keep the stream unbounded unless you accept partial rebuilds |
| `NATS_DUPLICATE_WINDOW_SECONDS` | `7200` | `Nats-Msg-Id` dedup window |
| `NATS_ACK_WAIT_SECONDS`, `NATS_MAX_DELIVER` | `30`, `8` | redelivery before an event is terminated as poison |
| `PUBLISH_BATCH`, `PUBLISH_INTERVAL_SECONDS` | `100`, `0.2` | outbox flushing |
| `CONSUME_BATCH` | `1` | 1 = strictly ordered application; raise only if ordering on failure may relax |
| `ADMIN_TOKEN` | | required off loopback, ≥ 32 characters |
| `EVENT_SIGNING_KEY` | | HMAC key for events; unsigned/invalid events are rejected when set (**set it**) |
| `METRICS_TOKEN` | | bearer for `/metrics`; unset ⇒ admin token or agent key required off loopback |
| `READY_REQUIRES_NATS` | `false` | `/ready` fails during a broker outage when true |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | | serve HTTPS directly |
| `ALLOWED_HOSTS`, `CORS_ORIGINS` | | Host allow-list; browser origins (off by default) |
| `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_HOPS` | `false`, `1` | use the Nth-from-the-right `X-Forwarded-For` entry for rate limiting and audit (only behind a proxy that is the sole path in) |
| `RATE_LIMIT_RPS`, `RATE_LIMIT_BURST` | `50`, `100` | per peer address before auth and per principal after |
| `MAX_BODY_BYTES`, `MAX_TEXT_CHARS`, `MAX_BATCH`, `MAX_EVENT_BYTES` | `1 MiB`, `4000`, `100`, `256 KiB` | input limits |
| `AUDIT_RETENTION_DAYS` | `90` | audit rows older than this are pruned at start |
| `MIN_SUPPORT` | `2` | distinct child units needed for a consolidated memory |
| `RULES_FILE` | | JSON file of slot-composition rules re-applied at every start (`deploy/mycelic/rules.json`); a file rule overrides an API edit to the same `rule_id`, and the override is appended to the event log so a rebuild ends with the same rules |
| `PUBLIC_URL` | | informational |

## 4. Operations

**Health.** `GET /health` is liveness (200 while the database is open; `status` is `ok` or `degraded`, the
latter when the broker is unreachable or a loop is down). `GET /ready` is readiness (database open, loops
running, no replay in progress). `GET /admin/status` (admin token) shows stream and consumer positions,
outbox depth, reconnect count and the masked settings.

**Backups.** Two things hold state:

1. the Mycelic database (`mycelic-data` volume). Back it up with SQLite's online backup while the service
   runs: `docker compose -f deploy/mycelic/docker-compose.yml exec mycelic python -c "import sqlite3; s=sqlite3.connect('/data/mycelic.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"`
   then copy `/data/backup.db` out of the volume; or snapshot the volume while the service is stopped;
2. the JetStream store (`nats-data` volume): snapshot the volume while the broker is stopped, or use the
   `nats` CLI (`nats stream backup MYCELIC <dir>`) against port 4222 from inside the network.

Restore order matters: a database older than the stream is caught up automatically (the service detects
that its `last_applied_seq` is behind the consumer's ack floor and re-delivers from the next sequence). A
missing database is rebuilt in full from the stream. A missing stream with an intact database keeps
serving, but nothing can be replayed until new events accumulate; restore the stream from its backup
before restoring an older database.

**Recovery procedures** (service crash, broker outage and database loss are exercised by
`tests/smoke/mycelic_smoke.py` steps 5–7; the other rows by the named tests in `tests/mycelic/test_jetstream.py`):

| Situation | What to do |
|---|---|
| service crashed | `docker compose -f deploy/mycelic/docker-compose.yml start mycelic` (or let `restart: unless-stopped` do it); it resumes from the durable consumer |
| broker down | nothing: writes are accepted and queued in the outbox; `/health` reports `degraded`; the queue flushes on reconnect. `mycelic_outbox_pending` shows the depth |
| database lost or corrupt | stop the service, remove `/data/mycelic.db*`, start it: it logs "fresh database but the stream holds N events: replaying" and rebuilds memories, lineage, agents (their keys keep working) and rules. `mycelic_replay_events_total` counts progress; `/ready` is 503 until the replay finishes, and the target is stored in the database so a crash mid-rebuild resumes it (`test_lost_database_is_rebuilt_from_the_stream`, `test_unfinished_replay_resumes_after_a_crash`) |
| stale database restored from backup | just start it: missing events are re-delivered (`mycelic_recovery_total{kind="replay_restored_backup"}`; `test_restored_backup_receives_the_events_it_missed`) |
| durable consumer lost (broker state reset) | just start it: the consumer is recreated after the last applied sequence (`kind="consumer_recreated"`; `test_lost_consumer_is_recreated_after_the_last_applied_event`) |
| stream shorter than the database (purged or recreated) | the database keeps serving; the log restarts from the new sequence and `kind="stream_behind_database"` is counted; restore the stream backup first when you can |
| force a replay | `python -m mycelic replay` or `POST /admin/replay` (`test_forced_replay_is_idempotent`) |
| poison or rejected event | after `NATS_MAX_DELIVER` attempts it is terminated and recorded (`GET /admin/events?org=…&status=failed`, audit `event.failed`); a terminated or unsigned event counts as consumed, so a replay still completes (`test_poison_event_is_terminated_and_replay_completes`) |

**Upgrades.** Build the new image, `docker compose -f deploy/mycelic/docker-compose.yml up -d --build`. The schema version is stored in the
database; a newer schema than the code refuses to start. Replays are idempotent across versions as long as
the aggregation rules are unchanged; changing rules changes future derivations only.

**Retention.** The stream is intentionally unbounded: bounding it (`NATS_MAX_AGE_SECONDS`,
`NATS_MAX_BYTES`) makes a rebuild partial and is logged as an error at connect. Size the `nats-data`
volume accordingly (a memory event is a few hundred bytes to a few kilobytes).

## 5. Local processes (no Docker)

Used by CI, the demo and the tests. Needs Python 3.11+, `pip install -r requirements.txt`, and the
`nats-server` binary (`MYCELIC_NATS_SERVER_BIN` or on `PATH`).

```bash
export MYCELIC_HOST=127.0.0.1 MYCELIC_PORT=8080 MYCELIC_DB_PATH=./mycelic.db \
       MYCELIC_NATS_URL=nats://127.0.0.1:4222 MYCELIC_NATS_USER=mycelic MYCELIC_NATS_PASSWORD=<pw> \
       MYCELIC_ADMIN_TOKEN=$(openssl rand -hex 32) MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32) \
       MYCELIC_RULES_FILE=deploy/mycelic/rules.json
NATS_STORE_DIR=./nats-data NATS_USER=mycelic NATS_PASSWORD=<pw> nats-server -c deploy/mycelic/nats/nats.conf &
python -m mycelic serve
```

`python -m pytest tests/mycelic -q` runs the unit, API, MCP, JetStream integration and smoke tests
(the JetStream and smoke modules skip themselves without the binary).

## 6. Kubernetes

`deploy/mycelic/k8s/` is a kustomize base: namespace, NATS StatefulSet + headless Service, Mycelic
StatefulSet (`replicas: 1`, never scale) + Service, ConfigMaps (`nats.conf`, settings, `rules.json`) and an
Ingress with TLS. Probes: startup on `/ready` with a long failure threshold (a rebuild may take minutes),
readiness `/ready`, liveness `/health`. Both pods run as non-root with `fsGroup` so their PVCs are writable.

```bash
# 1. image
docker build -f deploy/mycelic/Dockerfile -t ghcr.io/your-org/mycelic:0.1.0 . && docker push ghcr.io/your-org/mycelic:0.1.0
#    then set images[0].newName/newTag in deploy/mycelic/k8s/kustomization.yaml
# 2. secrets (never commit them; secret.example.yaml documents the keys)
kubectl create namespace mycelic
kubectl -n mycelic create secret generic mycelic-secrets \
  --from-literal=MYCELIC_ADMIN_TOKEN=$(openssl rand -hex 32) \
  --from-literal=MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32) \
  --from-literal=NATS_USER=mycelic --from-literal=NATS_PASSWORD=$(openssl rand -hex 32) \
  --from-literal=MYCELIC_METRICS_TOKEN=$(openssl rand -hex 32)
# 3. host names: edit ingress.yaml (host, ingressClassName, cert-manager issuer) and MYCELIC_ALLOWED_HOSTS in mycelic-configmap.yaml
# 4. apply
kubectl apply -k deploy/mycelic/k8s
kubectl -n mycelic rollout status statefulset/mycelic
```

Storage classes must be block storage (`ReadWriteOnce`): SQLite in WAL mode and the JetStream file store
are not safe on NFS. Requests are 10Gi (Mycelic) and 20Gi (NATS); adjust to your retention. The Ingress
terminates TLS; the Mycelic pod trusts `X-Forwarded-For` (`MYCELIC_TRUST_PROXY_HEADERS=true` in the
ConfigMap) because `networkpolicy.yaml` lets only the ingress controller's namespace (and optionally the
monitoring namespace) reach it, and lets only the Mycelic pod reach the broker; edit the namespace names in
that file to match your cluster, and note that it needs a CNI that enforces NetworkPolicy. The NATS monitor
listens on the container's loopback and is probed with `exec`.

**Metrics in Kubernetes.** `/metrics` needs a bearer token, so annotation-driven scraping does not work.
Use the Prometheus Operator: `kubectl apply -f deploy/mycelic/k8s/servicemonitor.example.yaml` (it reads
`MYCELIC_METRICS_TOKEN` from the `mycelic-secrets` Secret), or a plain scrape job with
`authorization: {type: Bearer, credentials_file: …}` like `deploy/mycelic/prometheus/prometheus.yml`.

The manifests were validated by rendering (`kubectl kustomize deploy/mycelic/k8s`), not by a deployment to
a cluster: expect to adjust storage class, ingress class and resource requests.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `configuration error: MYCELIC_ADMIN_TOKEN is required…` | set the token; ≥ 32 characters; no placeholder-looking values |
| `nats-server: … interface conversion: interface {} is int64` | `NATS_PASSWORD` is all digits; regenerate with letters (`openssl rand -hex 32`) |
| `/health` says `degraded`, `transport_connected: false` | broker unreachable or wrong credentials; writes are queued (`mycelic_outbox_pending`) |
| `/ready` 503 after a restart | a replay is running (`replaying_to_seq` in `/admin/status`); wait |
| 401 with a key that used to work | key rotated or agent revoked (`GET /admin/agents?all=1`) |
| 403 on `/query` with a `scope` | agents may only query their own team or an ancestor unit |
| 404 on `/memory/{id}` that exists | not visible to this agent (other team's raw note); the API does not reveal existence |
| `event is N bytes; the limit is …` | shrink `metadata` / `payload`; the limit keeps events publishable (`MYCELIC_MAX_EVENT_BYTES` < broker `max_payload`) |
| `stream MYCELIC is bounded … a rebuild from replay may be partial` | the stream was created with limits; remove them or accept partial rebuilds |
