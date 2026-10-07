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
# the signing key is set once, at first setup; never regenerate it in place: rotate it as SECURITY.md §7 describes
sed -i "s|^MYCELIC_EVENT_SIGNING_KEY=.*|MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32)|" deploy/mycelic/.env
sed -i "s|^NATS_PASSWORD=.*|NATS_PASSWORD=n$(openssl rand -hex 32)|" deploy/mycelic/.env   # must start with a letter

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

Tools: `mycelic_query`, `mycelic_remember`, `mycelic_lineage`, `mycelic_verify`, `mycelic_get_memory`, `mycelic_status`.

### Run the canonical demonstration

```bash
# the production smoke test against the compose stack you just started (keeps your data: it uses its own project)
python tests/smoke/mycelic_smoke.py --driver compose
# the narrated demo (local processes need the nats-server binary on PATH: https://github.com/nats-io/nats-server/releases)
python demo/mycelic_demo.py --driver compose
```

The smoke test registers agents in three teams, runs them as separate processes, checks the enterprise
conclusion and its lineage, verifies the conclusion downward (as a sales agent, who sees other teams' notes
redacted, and as the administrator), then kills the service, kills the broker, deletes the service database, and
checks that the same knowledge and lineage come back and that the administrator's verification of the answer has
the same report digest. It exits 0 only when every step held. With the process driver, all ten steps passed in
this repository's sandbox in 8.0–8.3 s with 6 agents (five runs) and 14.2–15.2 s with 99 agents (three runs,
`--agents-per-team 33`), and with the compose driver in 26.0–26.1 s (two runs, 6 agents, the image built from the
same tree with its dependency layer cached). CI runs both drivers (`.github/workflows/mycelic.yml`).

## 2. Production topology

```
                    ┌────────────────────────── your network boundary ──────────────────────────┐
 agents (HTTPS) ──► │ reverse proxy / Ingress (TLS) ──► mycelic:8080 ──► nats:4222 (internal only) │
 operators, MCP ──► │                                   │ /data (SQLite)      │ /data (JetStream)   │
                    └───────────────────────────────────┴─────────────────────┴───────────────────┘
```

* **One Mycelic instance per deployment.** State is a single SQLite file with one writer; the durable
  consumer name (`MYCELIC_NATS_CONSUMER`) is bound to that instance. Do not run two replicas against one
  database or one consumer. The service enforces this for the database: it holds an advisory lock on
  `<db>.lock` (`/data/mycelic.db.lock`) and a second process on the same file exits with
  `configuration error: another Mycelic process is using …` before it touches the schema. The lock needs
  a local filesystem: NFS and other `ReadWriteMany` storage are unsupported. Never delete the `.lock` file
  while the service runs (a new process could then lock a fresh file next to the running one); a stale one
  left by a crash never blocks, because the kernel releases the lock when its holder dies. The online
  backup of section 4 opens its own `sqlite3` connection and still works while the lock is held. Vertical
  capacity is what this slice targets: 10–100 agents, low thousands of memories per day. The scale-out path
  (Postgres for the read model, one consumer per shard) is not part of this release.
* **Size per organization.** Aggregation runs inside the consumer's apply transaction, on the event loop, so
  nothing else is served while an event applies, and its cost grows with the evidence of the event's organization
  and with the rules that read it. Measured at 5,000 active notes in one organization (96 agents in 32 teams across
  2 regions, 30–50 entities):
  * with the three rules of `deploy/mycelic/rules.json` (the default `MYCELIC_RULES_FILE`, a regional rule feeding a
    strategic one), applying one note took a median of 0.41–0.48 s and at most 1.85 s (about 0.3–0.4 s, at most
    1.2 s, at 3,000 notes); with `component_supply_risk` alone, 0.21 s and at most 0.59 s;
  * with `component_supply_risk` alone (about 8,100 active memories), a rule change took 1.3–1.9 s for a changed
    template, which re-derives every conclusion of the rule, and 1.4–2.0 s to re-enable it (0.4–0.7 s for an
    identical upsert, under 0.2 s to disable it); about 60% of that is scoring the fragility of each conclusion it
    derives again. A commit waits for the disk (`synchronous=FULL`), so a slow fsync adds to any apply: expect an
    occasional rule change above 2 s;
  * a full re-aggregation run took 108–136 s in 1,528 steps. A step's own work (reading, reconciling, emitting)
    took at most 0.3 s and whole steps usually at most 0.42 s; the rare slower ones (up to 1.2 s) are the commit
    waiting on the disk: in instrumented runs no step's work exceeded 0.3 s, while one commit took 0.57 s.

  Keep each organization at or below about 5,000 active notes per node in this release (with the shipped rules
  that is about two notes per second at most), and make rule changes in quiet periods. A re-aggregation run
  enumerates its organization's keys again at every step, so its duration grows faster than the organization; it
  yields between steps, so the node keeps serving meanwhile.
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
| `SHUTDOWN_TIMEOUT_SECONDS` | `10` | budget for draining HTTP requests/streams and then the broker connection on SIGTERM; total shutdown ≤ 2× this; keep below the grace period (compose 30 s, k8s 30 s) |
| `NATS_URL` | `nats://nats:4222` | empty disables the broker (in-process transport, development only) |
| `NATS_USER`, `NATS_PASSWORD`, `NATS_TOKEN`, `NATS_CA_FILE` | | broker credentials and CA; credentials in the URL are refused |
| `NATS_STREAM`, `NATS_CONSUMER` | `MYCELIC`, `mycelic-main` | stream and durable consumer names |
| `NATS_MAX_AGE_SECONDS`, `NATS_MAX_BYTES` | `0`, `-1` | keep the stream unbounded unless you accept partial rebuilds |
| `NATS_DUPLICATE_WINDOW_SECONDS` | `7200` | `Nats-Msg-Id` dedup window |
| `NATS_ACK_WAIT_SECONDS`, `NATS_MAX_DELIVER` | `30`, `8` | redelivery before an event is terminated as poison |
| `PUBLISH_BATCH`, `PUBLISH_INTERVAL_SECONDS` | `100`, `0.2` | outbox flushing |
| `CONSUME_BATCH` | `1` | 1 = strictly ordered application; raise only if ordering on failure may relax |
| `ADMIN_TOKEN` | | required off loopback, ≥ 32 characters |
| `EVENT_SIGNING_KEY` | | HMAC key for events and memory digests; unsigned/invalid events are rejected when set (**set it**). Backup-critical: change it only by rotating (section 4), never in place |
| `EVENT_SIGNING_KEYS_PREVIOUS` | | comma-separated keys from before a rotation (each ≥ 32 characters; requires `EVENT_SIGNING_KEY`; a key containing a comma cannot be listed, and generated hex keys never contain one): they still verify the events and memory digests they signed. Keep each one as long as the stream holds events it signed |
| `METRICS_TOKEN` | | bearer for `/metrics`; unset ⇒ admin token or agent key required off loopback |
| `READY_REQUIRES_NATS` | `false` | `/ready` fails during a broker outage when true |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | | serve HTTPS directly |
| `ALLOWED_HOSTS`, `CORS_ORIGINS` | | Host allow-list; browser origins (off by default) |
| `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_HOPS` | `false`, `1` | use the Nth-from-the-right `X-Forwarded-For` entry for rate limiting and audit (only behind a proxy that is the sole path in) |
| `RATE_LIMIT_RPS`, `RATE_LIMIT_BURST` | `50`, `100` | per peer address before auth and per principal after |
| `MAX_BODY_BYTES`, `MAX_TEXT_CHARS`, `MAX_BATCH`, `MAX_EVENT_BYTES` | `1 MiB`, `4000`, `100`, `256 KiB` | input limits |
| `AUDIT_RETENTION_DAYS` | `90` | audit rows older than this are pruned at start |
| `MIN_SUPPORT` | `2` | distinct child units needed for a consolidated memory. Changing it re-derives every consolidation at the next start (the re-aggregation job, section 4). It is deployment configuration, not part of the event log, so a rebuild from the log uses the value the rebuilding node runs with |
| `VERIFY_MAX_NODES` | `25000` | nodes one downward verification walks at most; beyond it the verdict is `unverifiable` (`walk_truncated`). Not set by the shipped compose file or manifests: to change it add `MYCELIC_VERIFY_MAX_NODES` to the `environment` of the `mycelic` service (compose) or to `mycelic-configmap.yaml` (Kubernetes) |
| `EXPIRY_SWEEP_SECONDS` | `30` | how often the expiry sweep queues the retraction of notes past their `expires_at`, at most 100 per sweep (section 4, "Expiry"); `0` disables it (expired notes are still left out of answers, but stay evidence until retracted). Not set by the shipped compose file or manifests |
| `RULES_FILE` | | JSON file of slot-composition rules re-applied at every start (`deploy/mycelic/rules.json`); a file rule overrides an API edit to the same `rule_id`, and the override is appended to the event log so a rebuild ends with the same rules. Rule fields are listed in section 3a |
| `PUBLIC_URL` | | informational |

### 3a. Rules

A rule (`deploy/mycelic/rules.json`, or `POST /admin/rules`) is a conclusion that exists only once every
required slot is covered inside its target unit:

| Field | Meaning |
|---|---|
| `rule_id`, `target_layer` | identity; the layer of the unit the conclusion belongs to (`team` … `enterprise`) |
| `required_slots` | slots that must all be filled for the same `entity` |
| `conclusion` | template with `{entity}` and `{slot:<name>}` placeholders |
| `topic_prefix` | only evidence whose topic starts with it counts |
| `min_agents`, `min_teams` | distinct agents / teams behind the evidence |
| `sources` | operators that may fill a slot: `agent_observation` (default), `slot_composition` (other rules' conclusions), `topic_consolidation` |
| `emits_slot`, `emits_topic` | what the conclusion carries, so a higher rule can consume it |
| `min_units` | corroboration per slot, e.g. `{"supply_risk": {"region": 2}}`: the units behind the evidence filling that slot must include two regions. The count is taken over the memories that become parents, so without `corroborate` it is the units behind the single strongest memory per slot; a count above 1 therefore needs `corroborate: true` unless that one memory itself spans the units (a consolidation or an already corroborated conclusion) |
| `corroborate` | every memory filling a required slot becomes evidence (lineage and support include all of them); confidence per slot is the noisy-OR over the units filling it |
| `kind`, `org_id`, `enabled`, `metadata` | memory kind of the conclusion; restrict to one organization; switch off; free-form |

Labels are normalised wherever they enter: topics, slots and entities of notes and query filters, and a rule's
`required_slots`, `emits_slot`, `topic_prefix`, `emits_topic`, `min_units` slots and the `{slot:<name>}`
placeholders of its `conclusion` (NFKC, case-folded, whitespace collapsed; `SD-9`, ` sd-9 ` and `Sd-9` are one
entity, `{slot:Transport_Disruption}` is `{slot:transport_disruption}`). Slots must still match
`[A-Za-z0-9_.:-]{1,100}` afterwards, so `Transport Disruption` is refused with a 400. `GET /admin/rules` returns
the stored, normalised form. A rule with neither `emits_topic` nor `topic_prefix` gives its conclusions the
normalised `rule_id` as topic.

Quoting evidence is a publication decision. A rule whose conclusion template quotes `{slot:...}` publishes the
quoted evidence, whatever its visibility, at the rule's target layer and, through consolidations of the
conclusion's topic, at every layer above it. A template that names only `{entity}` publishes no note's text.

An upsert or delete shows in `GET /admin/rules` at once, but aggregation uses it only when the consumer applies its
event, in log order with the notes around it. When the event applies, the rule is applied to everything applied
before it: a new rule concludes at once on the evidence already there (no new note is needed); a changed rule
re-derives its conclusions (a new version, the old one kept as `superseded`, when it now derives differently; a
withdrawal, `support below threshold`, when tighter thresholds no longer hold; nothing at all when only `metadata`
changed); a disabled or deleted rule withdraws its conclusions (`rule disabled`, `rule deleted`); a rule moved to
another `target_layer` or narrowed to one `org_id` withdraws the conclusions it no longer covers (`rule no longer
applies here`) and concludes at its new layer. Whatever rested on a withdrawn conclusion (a strategy on regional
conclusions, say) is withdrawn and re-evaluated with it. Re-enabling a rule, or re-creating a deleted one
identically, brings back the same conclusions with the same ids. Two edits of one rule that reach the log before the
first is applied are applied in turn, so a conclusion can get one intermediate version. The same holds for agents:
a unit's registered children, which decide whether it promotes a single child's consolidation, are counted from the
registrations and revocations the consumer has applied, and the registration of a unit's second child withdraws its
promotion (and those above it) when it applies; revoking that child's last agent restores them.

Rules compose and cascade: a conclusion is offered to the rules and consolidations above it as soon as it
is derived, a rule never feeds on its own conclusions (directly or through other rules; a rule set that would
form a cycle is refused at `POST /admin/rules` and in the rules file), and finer-grained evidence wins over a
consolidation that merely restates it. A rule names at most 6 slots. When an observation is retracted, or a
conclusion loses its support, every dependent conclusion is withdrawn and then re-evaluated on the evidence
that remains: a conclusion corroborated by three regions survives losing one, and an exact earlier coalition
is reactivated (`tests/mycelic/test_strategic.py::test_strategy_follows_evidence`).
`demo/mycelic_strategic_demo.py` shows the two-region case: retracting APAC's supplier notes withdraws the
regional conclusion and the strategy, and fresh evidence brings both back as a new version.

## 4. Operations

**Health.** `GET /health` is liveness (200 while the database is open; `status` is `ok` or `degraded`, the
latter when the broker is unreachable or a loop is down). `GET /ready` is readiness (database open, loops
running, no replay in progress). Both answer from a status snapshot that a background task refreshes every
2 s, so they never wait on the broker or the database: a stalled broker cannot time out a probe. Whether the
broker is connected is read live, so a broker that goes away shows at once. The admin view of `/health`
shows `checks.transport.stale` and `age_seconds`: the snapshot is stale when the last broker call failed or
took longer than 2 s, or the last good refresh is more than 6 s old, and stale counts as `degraded`.
`GET /admin/status` (admin token) is live: it refreshes the snapshot first, bounded to 2 s per broker call,
and shows stream and consumer positions, outbox depth, reconnect count and the masked settings.

**Shutdown.** On SIGTERM the service stops accepting connections and gives running requests up to
`MYCELIC_SHUTDOWN_TIMEOUT_SECONDS` (10 s) to finish or be cancelled (an open `GET /mcp` stream never
finishes on its own and takes the whole budget), then stops its loops and drains the broker connection
within the same budget again; a broker that does not answer is dropped. A SIGTERM that arrives while the
service is still starting (for example waiting for a broker that is down) cancels the start-up.
Nothing is lost by cutting either step short: an event is acknowledged only after the transaction that
applied it committed, the outbox is durable, and whatever was in flight is redelivered and applied as an
idempotent duplicate. The whole shutdown takes at most about 2 × 10 s, inside the 30 s grace period of
both the compose file (`stop_grace_period`) and the StatefulSet (`terminationGracePeriodSeconds`).

**Backups.** Three things hold state:

1. the Mycelic database (`mycelic-data` volume). Back it up with SQLite's online backup while the service
   runs: `docker compose -f deploy/mycelic/docker-compose.yml exec mycelic python -c "import sqlite3; s=sqlite3.connect('/data/mycelic.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"`
   then copy `/data/backup.db` out of the volume; or snapshot the volume while the service is stopped;
2. the JetStream store (`nats-data` volume): snapshot the volume while the broker is stopped, or use the
   `nats` CLI (`nats stream backup MYCELIC <dir>`) against port 4222 from inside the network;
3. the signing keys, `MYCELIC_EVENT_SIGNING_KEY` and `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`: store them with every
   database backup. Without the keys that signed them, the digests of a restored database read `unknown_key` and a
   rebuild from the stream rejects every event (SECURITY.md §7).

Restore order matters: a database older than the stream is caught up automatically (the service detects
that its `last_applied_seq` is behind the consumer's ack floor and re-delivers from the next sequence). A
missing database is rebuilt in full from the stream. A missing stream with an intact database keeps
serving, but nothing can be replayed until new events accumulate; restore the stream from its backup
before restoring an older database.

**Recovery procedures** (service crash, broker outage and database loss are exercised by
`tests/smoke/mycelic_smoke.py` steps 6–8; the other rows by the named tests in `tests/mycelic/test_jetstream.py`):

| Situation | What to do |
|---|---|
| service crashed | `docker compose -f deploy/mycelic/docker-compose.yml start mycelic` (or let `restart: unless-stopped` do it); it resumes from the durable consumer |
| broker down | nothing: writes are accepted and queued in the outbox; `/health` reports `degraded`; the queue flushes on reconnect. `mycelic_outbox_pending` shows the depth |
| database lost or corrupt | stop the service, remove `/data/mycelic.db*`, start it: it logs "fresh database but the stream holds N events: replaying" and rebuilds memories, lineage, agents (their keys keep working) and rules. `mycelic_replay_events_total` counts progress; `/ready` is 503 until the replay finishes, and the target is stored in the database so a crash mid-rebuild resumes it (`test_lost_database_is_rebuilt_from_the_stream`, `test_unfinished_replay_resumes_after_a_crash`) |
| stale database restored from backup | just start it: missing events are re-delivered (`mycelic_recovery_total{kind="replay_restored_backup"}`; `test_restored_backup_receives_the_events_it_missed`) |
| durable consumer lost (broker state reset) | just start it: the consumer is recreated after the last applied sequence (`kind="consumer_recreated"`; `test_lost_consumer_is_recreated_after_the_last_applied_event`) |
| stream shorter than the database (purged or recreated) | the database keeps serving; the log restarts from the new sequence and `kind="stream_behind_database"` is counted; restore the stream backup first when you can |
| force a replay | `python -m mycelic replay` or `POST /admin/replay` (`test_forced_replay_is_idempotent`) |
| force a re-aggregation | `python -m mycelic reaggregate [--org ORG]` or `POST /admin/reaggregate` with `{"org_id": …}`, or with no body or a null `org_id` for every organization (an empty `org_id` is refused with 400): re-derives every consolidation and conclusion of one organization or all of them in the background while the node keeps serving; `{"started": false}` while a run is in progress. Progress: `GET /admin/status` → `checks.reaggregation`, `mycelic_reaggregation_steps_total`, audit `aggregation.reaggregate` per organization (`test_admin_route_sdk_and_cli`) |
| poison or rejected event | after `NATS_MAX_DELIVER` attempts it is terminated and recorded (`GET /admin/events?org=…&status=failed`, audit `event.failed`); a terminated or unsigned event counts as consumed, so a replay still completes (`test_poison_event_is_terminated_and_replay_completes`) |

**Removing or moving an agent.** Revoking an agent (`python -m mycelic revoke-agent --agent-id X`, `DELETE
/admin/agents/X`) refuses its key at once (403) and leaves its notes as evidence. Removing it (`python -m mycelic
revoke-agent --agent-id X --retract`, `DELETE /admin/agents/X?retract=1`, `client.revoke_agent("X", retract=True)`)
also retracts every note it has. The call appends one `agent.removed` event and answers `{"agent_id": "X", "revoked":
true, "retracted": N}`, N being the notes active when the call was made (those still on their way through the log
included; a repeat once the removal has applied answers 0). The key gets 403 at once, and a note or an event sent
with a request that was already authenticated is refused inside its own transaction. When the event applies, every
note of the agent is retracted (`status_reason` `agent removed`) and everything that rested on them is re-derived
without them, in that one apply and with at most one new version per consolidation or conclusion; the units the agent
leaves re-plan their promotions, so a department left with one team promotes it directly. A note of the agent that
reaches the log after the removal (a write that raced it) is applied retracted (audit
`memory.observed_after_removal`). A removed agent stays removed: a later revocation does not change that, and its id
cannot be registered again (only a rollback to an earlier release undoes a removal: see "What a rollback loses").
Removing an agent that was only revoked retracts its notes too. Measured in this repository's sandbox with
`DEMO_RULE` active and five other agents present, the apply of one removal of an agent with 500 notes on one topic
took 0.40 s median and 0.46 s at most over five runs, and with 500 notes on 500 topics (each also noted by a
teammate, so 500 team consolidations are withdrawn) 1.28 s median and 1.40 s at most, and with 2,000 notes on 2,000
topics 4.99 s median and 5.54 s at most over three runs. The apply runs on the event loop, so for that long every
request waits, `/health` and `/ready` included. The Kubernetes liveness probe (section 6) restarts the pod after
three failed probes 15 s apart, so an apply longer than about 35 s (roughly 13,000 topics at the rate above) would be
rolled back and then repeated at every redelivery: remove an agent whose notes span that many topics off-peak, with
the probe's `failureThreshold` raised for the time. To move an agent to another unit, register a new id at the new
path, let the agent re-share its notes with the new key (`LocalMemory.share` sends each local id as the idempotency
key, which under the new key gives new memory ids), then remove the old id with `--retract`. While both ids are
active, support counts both.

**Expiry.** A note may carry `expires_at` (`POST /memory`, an embedded memory of `POST /events`, MCP
`mycelic_remember`, `client.remember(..., expires_at=...)`, `LocalMemory.share(..., expires_at=...)`): an ISO-8601 time
in the future and at most ten years ahead, stored in UTC to the second (a time without an offset is taken as UTC;
anything else is a 400). From that moment `POST /query` and MCP answers leave the note out, and the verification of
anything that rests on it reports `leaf_expired` (stale). The expiry sweep, every `MYCELIC_EXPIRY_SWEEP_SECONDS`
(30 s), appends one `memory.retracted` event (reason `expired`) for each expired note, at most 100 per sweep (200 a
minute by default), and its apply retracts the note and re-derives what rested on it exactly like any other
retraction. Aggregation never reads the clock, so until that retraction applies the note still counts in
consolidations and conclusions. The event id is a function of the memory id, so a note gets one retraction however
often it is swept: during a broker outage the events wait in the outbox and the sweep moves on to the next notes. A
backlog shows as `checks.expiry.overdue` in `GET /admin/status` (with `last_sweep_at` and `last_queued`) and as
`mycelic_memories_expiry_overdue`; `mycelic_memories_expired_total` counts the retractions queued, and each one is
audited (`memory.expired`). Measured in this repository's sandbox with notes on 10 topics in two teams under
`DEMO_RULE`: one sweep over 100 expired notes took 5.4 ms median and 8.1 ms at most over five runs, and applying the
100 retractions it queued 0.99 s median in all (11 ms median per event, 23 ms at most). Like any retraction, expiry
withdraws a note from answers but does not erase it: its text stays in the database, the event log and the stream.

**Correcting a note.** An agent corrects one of its own active notes by sending the corrected note with
`supersedes` set to the old note's id (`POST /memory`, MCP `mycelic_remember`, `client.remember(..., supersedes=...)`,
`LocalMemory.share(..., supersedes=...)`). The correction is a complete note: nothing is inherited from the old one,
so it repeats the topic, slot, entity and expiry it should have, and it needs an idempotency key of its own (with
`LocalMemory`, write the correction as a new local note). The response adds `supersedes`. When its event applies, the
old note is superseded (`superseded_by` the new id, `status_reason` `updated by producer`), everything that rested
on the old note is re-derived on the new one (a consolidation or conclusion moves to the new note's topic, slot or
entity, or is withdrawn when nothing else covers it), and the lineage of the new note lists the old one in
`previous_versions`. Only the producer may update a note, and only an active raw note (404 for an id the caller may
not read or that does not exist, 400 for a derived memory or a note that is no longer active, 403 for another agent's
note, 400 for `supersedes` on an embedded memory of `POST /events`). While a retraction of the note (an expiry's
included) or another update of it is still on its way through the log, an update is refused with 409; retry once it
has applied (a resend with the same idempotency key returns the stored update at any time). An update whose target
is no longer the producer's active note when it applies (a write that raced a retraction) is applied as a plain note
and audited as `memory.update_conflict`. Nothing reactivates: after A → B → C, retracting C leaves A and B superseded.
Measured in this repository's sandbox with `DEMO_RULE` active and 52 notes on the topic, the apply of an update took
33 ms median (37 ms at most over five runs) against 18 ms (29 ms) for a plain note on the same labels.

**Re-attesting notes.** Verification's `max_leaf_age` judges how long ago each raw note was last confirmed: when the
server ingested it or, later, when its producer re-attested it. An agent re-attests one of its own active notes with
`POST /memory/{id}/attest` and `{"still_true": true}` (MCP `mycelic_attest`, `client.attest(memory_id)`); the answer
is 202, and once its `memory.attested` event applies the note's `attested_at` is set and the row is signed again (an
attestation never moves `attested_at` backwards, never applies to a note that was retracted or superseded first, and
is ignored on a row whose digest does not check, so it never re-signs an edited row). `{"still_true": false,
"reason": …}` retracts the note instead, exactly as `POST /memory/{id}/retract`. Only the producer may attest, and
only an active, unexpired raw note (403 for anyone else, the administrator included; 404 for an id the caller may not
read; 400 for a derived, inactive or expired note or a `still_true` that is not a boolean); an attestation never
extends an expiry. Only a row whose digest checks `ok` takes an attestation: besides an edited row, one signed by a
key that is no longer configured (`unknown_key`) or signed without a key before one was set (`downgraded`) cannot be
re-attested, and its attestation is ignored (`mycelic_events_ignored_total{reason="attestation_integrity"}`). `GET
/attestations/due?older_than=N&limit=M` (`client.due_attestations(older_than=N)`) lists the caller's own active notes
that an active consolidation or conclusion rests on and that were last ingested or attested at least N seconds ago (0
by default), stalest first, at most M (50 by default, 1 to 500). It is self-attestation: it adds freshness, not
independent assurance (SECURITY.md §6). Measured in this repository's sandbox, the apply of one attestation (digest
check, `attested_at` and the new digest) took 0.88 ms median and 1.41 ms at most over five runs.

**Verifying a conclusion.** Before acting on a conclusion, anyone who may read it and holds `lineage:read` (every
agent key by default, and the admin token) can ask whether it was derived correctly and whether it is still true.
The service walks the conclusion's derivation down to the raw notes and checks every node on the way: each
contributing memory exists and its digest matches its content, each raw note agrees with its `memory.observed`
event, each consolidation and rule conclusion recomputes from its stored parents with its support thresholds still
holding, and nothing beneath it was retracted or superseded, nor its rule or `MIN_SUPPORT` changed. SECURITY.md §6
says exactly what that proves and what it does not.

```bash
python -m mycelic verify <memory_id> [--max-leaf-age 604800] [--json]     # MYCELIC_API_KEY, or the admin token
curl -H "Authorization: Bearer $MYCELIC_API_KEY" "http://localhost:8080/verify/<memory_id>?max_leaf_age=604800"
```

From Python, `client.verify(memory_id, max_leaf_age=604800)`. `POST /query` with `"verify": true`
(`client.query(..., verify=True)`, `python -m mycelic query --verify`) adds `answer.verification` with the verdict,
both answers and the reason codes, and MCP clients call `mycelic_verify`. `max_leaf_age` (1 to 315,360,000
seconds, optional) also requires every raw note the caller can read to have been ingested by the server within
that many seconds.

| Verdict | Meaning | `derived_correctly` / `still_true` | CLI exit |
|---|---|---|---|
| `verified` | derived correctly and still current | `true` / `true` | 0 |
| `stale` | derived correctly, but something beneath it was retracted, superseded or changed since, a raw note is past its `expires_at`, or one is older than `max_leaf_age` | `true` / `false` | 3 |
| `failed` | a contribution is missing, was edited, or does not recompute to what is stored | `false` / `null` | 4 |
| `unverifiable` | something needed for the check is unavailable: a signing key no longer configured, a missing event, a walk beyond `MYCELIC_VERIFY_MAX_NODES`, a replay in progress, a row derived by an older release | `null` / `null` | 5 |

The CLI exits 1 on an HTTP or connection error and 2 on a usage error. `GET /verify/{id}` answers 200 with the
report for every verdict; otherwise 400 for a malformed id or `max_leaf_age`, 401 without a valid token, 403 without
`lineage:read` or for a revoked agent, 404 for an id that does not exist or that the caller may not read (one and
the same answer) and 429 when rate limited. The report lists reason codes per node and for the whole walk
(docs/MYCELIC_ARCHITECTURE.md §7 has the table); contributions the caller may not read are redacted (SECURITY.md §2).

Each successful verification is counted (`mycelic_verifications_total{verdict}`,
`mycelic_verification_reasons_total{reason}`, `mycelic_verification_latency_seconds`) and audited (`memory.verify`,
with the reason codes before redaction, for administrators); a refused one is neither. The reason counter takes the
codes as the caller saw them: an agent's verification counts `hidden_error` for another team's node that failed,
not the code its report withheld, while an administrator's counts every code. Without `MYCELIC_METRICS_TOKEN`,
`/metrics` accepts any agent key, so set it where even those totals must stay away from agents (SECURITY.md §2).
Alert on any increase of `mycelic_verifications_total{verdict="failed"}`: something a conclusion rests on is missing
or was edited; the audit row says what.

Limits. A walk reads at most `MYCELIC_VERIFY_MAX_NODES` (25,000) nodes. It costs the caller 1 + ceil(nodes / 250)
rate-limit tokens from its principal bucket: the request's own token at the door, the walk's share after it ran,
which may take the bucket into debt, at most one burst deep. A principal in debt is refused with 429 (an MCP error
result inside a JSON-RPC batch) before anything is read, until its bucket refills. The walk holds the store lock
and the event loop while it runs, so nothing else is served meanwhile: measured in this repository's sandbox with
`service.verify` over one team's org-visible notes, 0.10–0.20 s for 1,005 nodes and 0.56–0.85 s for 5,005 nodes
(five runs of five calls each on an otherwise idle 4-CPU machine; slower while other work runs). The charge is a
brake proportional to the walk, not a CPU cap: at the default `MYCELIC_RATE_LIMIT_RPS` of 50 a principal still
earns 12,500 nodes of walk a second, more than the service walks in a second at those rates, so where agents may
verify large conclusions often, lower `MYCELIC_RATE_LIMIT_RPS` or `MYCELIC_VERIFY_MAX_NODES`.

**Rotating the signing key.** Never regenerate `MYCELIC_EVENT_SIGNING_KEY` in place: make the current key a
previous one and add a new current key. Events in flight and every memory digest signed by the old key keep
verifying, new ones are signed by the new key, and a database rebuilt from the stream later still verifies the old
events. Back up the keys with the database before and after.

```bash
# Compose: the old key joins MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS (an .env from an earlier release lacks the line)
env=deploy/mycelic/.env
grep -q '^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=' "$env" || echo 'MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=' >> "$env"
old=$(sed -n 's/^MYCELIC_EVENT_SIGNING_KEY=//p' "$env"); prev=$(sed -n 's/^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=//p' "$env")
sed -i "s|^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=.*|MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=${prev:+$prev,}$old|" "$env"
sed -i "s|^MYCELIC_EVENT_SIGNING_KEY=.*|MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32)|" "$env"
docker compose -f deploy/mycelic/docker-compose.yml up -d mycelic

# Kubernetes: the same change in the Secret, then a restart
old=$(kubectl -n mycelic get secret mycelic-secrets -o jsonpath='{.data.MYCELIC_EVENT_SIGNING_KEY}' | base64 -d)
prev=$(kubectl -n mycelic get secret mycelic-secrets -o jsonpath='{.data.MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS}' | base64 -d)
kubectl -n mycelic patch secret mycelic-secrets --type merge -p "{\"stringData\": {\"MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS\": \"${prev:+$prev,}$old\", \"MYCELIC_EVENT_SIGNING_KEY\": \"$(openssl rand -hex 32)\"}}"
kubectl -n mycelic rollout restart statefulset/mycelic
```

Keep every previous key for as long as the stream holds events it signed (with the default unbounded stream,
for good). The database records the id of the key that is current at each start (`meta.known_key_ids`; a key id
is a truncated hash, not the key), and the service logs a warning at start naming each one that is no longer
configured: rows it signed read `unknown_key` and events it signed are rejected until it is listed again. A key
retired before this release first started on the database (upgrading and rotating in one step) is never recorded
and never warned about, one more reason to rotate only after the upgrade has started once. Previous keys are read
with surrounding whitespace removed, while the current key is used exactly as given, so a current key with a
leading or trailing space or newline (a Secret created from a file that ends in a newline) cannot be moved to
the previous list verbatim: generate keys with `openssl rand -hex 32` as shown, without whitespace.

**Upgrades.** Build the new image, `docker compose -f deploy/mycelic/docker-compose.yml up -d --build`. The schema version is stored in the
database; a newer schema than the code refuses to start. Replays are idempotent across versions. Derived ids embed
the derivation version, `MIN_SUPPORT` and each rule's digest, so after an upgrade that changes how memories are
derived, or a change of `MIN_SUPPORT`, the conclusions converge automatically: the first start runs the
re-aggregation job in the background (the node serves and stays ready meanwhile; it waits while a replay runs). Old
rows are never deleted and stay readable by id. Most are superseded by their new version (`previous_versions` in the
lineage of the successor); a row that rested on a superseded one and was not replaced along with it is retracted
(`status_reason` `evidence superseded`) and derived again as a new memory with no `version_of` link. Until
`GET /admin/status` shows `checks.reaggregation.state` = `done` (and `mycelic_reaggregation_steps_total` stops
rising), answers may mix memories derived by the old and the new version. An interrupted or failed run is
retried at the next start; `python -m mycelic reaggregate` re-runs it on demand. Its derived events are not
reproduced by a rebuild from the log, which derives the converged state directly.

**Responses changed in this release** (derivation version 2):

* Consolidation text has a new format and no agent ids: `<topic> — team '<team>': <n> agents. <statement>; …` at
  team level, `<topic> — <layer> '<unit>': <n> <child layer> sources, <n> agents, <n> team-private observations not
  quoted. [<child>] <statement>; … (+N more)` above it (see `docs/MYCELIC_ARCHITECTURE.md` §5). The quoted statements
  are in `metadata.statements`, with `metadata.statement_origins` (`team`, `org`, `rule`) and
  `metadata.private_observations`; every derived text is at most 2,000 characters, and above team nothing is
  quoted except notes marked `visibility: org` and rule conclusions. Clients that parsed the old
  head must read the new one or `metadata.statements`.
* Text of a memory that is not active (superseded or retracted) is returned only to its producer and to
  administrators; everyone else who may read the memory gets an empty `text` and `text_withheld` set to its
  status. This applies to `GET /memory/{id}`, `GET /memories?status=superseded|retracted` (which also drop
  `metadata.statements` and `statement_origins`), the nodes of `GET /lineage/{id}` (which keep their shape) and
  the MCP tools. `text` stays a string, and `text_withheld` is absent whenever the text is shown.
* Downward verification only adds: `GET /verify/{id}`, the opt-in `"verify": true` of `POST /query` (without it,
  or with `false`, the response is unchanged), the MCP tool `mycelic_verify` and `python -m mycelic verify`. Existing
  responses are unchanged.
* Re-attestation only adds: `POST /memory/{id}/attest`, `GET /attestations/due`, `client.attest` and
  `client.due_attestations`, and the MCP tool `mycelic_attest` (`tools/list` now lists seven tools, `mycelic_verify`
  and `mycelic_attest` among them); `GET /` lists the two routes among its endpoints.
* Agent removal only adds: `DELETE /admin/agents/{id}?retract=1` (or `true`) answers `{"agent_id", "revoked": true,
  "retracted": N}` and any other `retract` value is 400; without `retract` the request and its response are unchanged.
  `python -m mycelic revoke-agent` is new. A note or an event whose agent was revoked after the request was
  authenticated is now refused with 403 instead of being stored.
* Expiry only adds: `expires_at` on `POST /memory`, on embedded memories of `POST /events` and on
  `mycelic_remember` (a note without it behaves exactly as before); every memory object carries two new keys,
  `expires_at` and `attested_at` (null unless set; `attested_at` is set once the producer re-attests the note); a
  verification report carries `valid_until` (the earliest `expires_at` of the active raw notes the caller can read in
  the walk, or null) and `valid_until_partial`, and may carry the stale code `leaf_expired`; `GET /admin/status` has
  `checks.expiry` and `stats.expiry_overdue`.
* Producer updates only add: `supersedes` on `POST /memory` and `mycelic_remember` (the response then adds
  `supersedes`; without it the request and its response are unchanged), the status 409 for an update while a
  retraction or another update of its target is pending, and the stale code `update_pending` in verification reports.
* `DERIVATION_VERSION` is 2, so the first start re-derives everything (`checks.reaggregation.reason` =
  `derivation_version`). Until `checks.reaggregation.state` = `done`, consolidations derived by the earlier release
  keep their old text, which may quote team-visibility notes and agent ids above team level, and they are what
  agents read. Hold agent traffic until then if that matters; once they are superseded their text is withheld
  from agents like that of any other inactive memory.

**Rolling back and forward again.** This release writes schema 5, and earlier releases refuse to open a schema-5
database (`database schema 5 is newer than this code`). To roll back, stop the service, restore the database backup
taken before the upgrade (see Backups) and start the earlier image on it. It re-delivers from the stream what it
missed since the backup and applies it with its own derivation, provided every event since then is signed by its
key: an earlier release verifies with one key only, so rotate the signing key only after deciding to stay on this
release. An earlier release re-derives with its own renderer, which may quote team-visibility notes and agent ids
above team level. Rolling forward again migrates the restored database to schema 5, signs its rows at the first
start and re-aggregates whatever `meta.derivation_version` says was derived differently. A release from before
derivation versions leaves that key as it found it while deriving ids without a version, so after rolling forward
from one, run `python -m mycelic reaggregate` (or `POST /admin/reaggregate`) once and wait for
`checks.reaggregation.state` = `done`.

**What a rollback loses.** An earlier release applies only what it knows. It marks `agent.removed` and
`memory.attested` events applied without effect (`unknown_kind`), applies a correction as a plain note, so the old
version stays active next to it, and drops `expires_at`, so it neither hides nor sweeps expired notes (expiry
retractions already in the stream still apply); it ignores `supersedes` and `expires_at` in a request the same way.
After a rollback, an agent removed since the backup is therefore active again: its key authenticates and its notes
count. Revoke each such agent at once with a plain `DELETE /admin/agents/{id}`, which earlier releases apply. Because
the earlier release counts those events as applied, starting this release on its database again does not recover
them, and a note logged with `expires_at` since the backup then has none and verifies `failed`
(`source_event_mismatch`). So roll forward by rebuilding from the stream (Recovery procedures, "database lost or
corrupt"; move the old database aside rather than deleting it, because the audit rows of API calls are not in the
stream): the rebuild applies the whole log with this release's semantics. On the restored database instead, remove
each such agent again with `?retract=1`, retract the old version of each correction (`POST /memory/{id}/retract` on
the id in the correction's `metadata.version_of`) and have producers attest their notes again; a note logged with
`expires_at` since the backup stays `failed` until a rebuild.

Upgrading to schema 5 (this release) adds two columns to `memories`, `expires_at` and `attested_at`, and the partial
index `idx_memories_expiry`, in one transaction at the first start; existing rows get NULL in both, so every digest
written before the upgrade still checks (a digest covers `expires_at` only when a row has one) and nothing is
re-derived. Like every schema upgrade it is one-way, so **back up the database first**.

Upgrading to schema 4 adds three columns to `memories` (`digest`, `digest_key_id`,
`digest_origin`) in one transaction. The first start then signs every existing memory (origin `backfill`, see
SECURITY.md §3) before it starts the consumer: `/health` answers meanwhile and `/ready` stays 503 until it is done.
Measured on a 22,685-row database (18,685 derived rows, up to 400 roots each, 248 MB): about 7,800 rows/s through the
service in batches of 500 rows (2.9 s in all, at most 0.11 s per batch); at that rate a million rows take about two
minutes. It writes one `integrity.backfill` audit row and counts `mycelic_integrity_backfilled_total`;
an interrupted backfill (SIGTERM, crash) keeps what it committed and resumes at the next start. This is the only
backfill that signs rows in a healthy database (in one created at schema 4 or later, none ever does): after it
completes, any backfill WARNING or ERROR in the log, any further `integrity.backfill` audit row and any rise of
`mycelic_integrity_backfilled_total` means digests were removed from the database (SECURITY.md §3), so alert on
the log and the metric. Like the schema-3 upgrade it is one-way, so **back up the database first**.

Upgrading to schema 3 is one-way: the first start migrates the database in one transaction
(normalised labels on stored notes and rules, applied-rule and registry state, apply order), and older code cannot
open it afterwards, so **back up the database first** (see Backups). Consolidations and conclusions derived before
the upgrade keep the label spellings they were built under until they are re-aggregated; the migration records that
need as `reaggregate_pending` in the `meta` table, and the re-aggregation job at the first start withdraws them and
derives the canonical ones (an empty database records nothing to do and runs no job). A rebuild from an older stream applies
its notes in normalised form and ignores the old derived events (one `event.ignored` audit row with their count).

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
Ingress with TLS. Probes: startup `/health`, readiness `/ready`, liveness `/health`, all with 5 s timeouts;
a replay (a rebuild may take minutes) keeps the pod unready and never restarts it. Both pods run as
non-root with `fsGroup` so their PVCs are writable.

```bash
# 1. image
docker build -f deploy/mycelic/Dockerfile -t ghcr.io/your-org/mycelic:0.1.0 . && docker push ghcr.io/your-org/mycelic:0.1.0
#    then set images[0].newName/newTag in deploy/mycelic/k8s/kustomization.yaml
# 2. secrets (never commit them; secret.example.yaml documents the keys)
kubectl create namespace mycelic
kubectl -n mycelic create secret generic mycelic-secrets \
  --from-literal=MYCELIC_ADMIN_TOKEN=$(openssl rand -hex 32) \
  --from-literal=MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32) \
  --from-literal=NATS_USER=mycelic --from-literal=NATS_PASSWORD=n$(openssl rand -hex 32) \
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
| `configuration error: another Mycelic process is using …` | a second instance on the same database, or the previous one is still shutting down; stop it or wait (at most 2 × `MYCELIC_SHUTDOWN_TIMEOUT_SECONDS`). Never `docker compose up --scale mycelic=N` |
| 401 with a key that used to work | key rotated or agent revoked (`GET /admin/agents?all=1`) |
| 403 on `/query` with a `scope` | agents may only query their own team or an ancestor unit |
| 404 on `/memory/{id}` that exists | not visible to this agent (other team's raw note); the API does not reveal existence |
| `event is N bytes; the limit is …` | shrink `metadata` / `payload`; the limit keeps events publishable (`MYCELIC_MAX_EVENT_BYTES` < broker `max_payload`) |
| `stream MYCELIC is bounded … a rebuild from replay may be partial` | the stream was created with limits; remove them or accept partial rebuilds |
