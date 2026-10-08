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
install -m 600 deploy/mycelic/.env.example deploy/mycelic/.env   # every secret: readable by you alone
sed -i "s|^MYCELIC_ADMIN_TOKEN=.*|MYCELIC_ADMIN_TOKEN=$(openssl rand -hex 32)|" deploy/mycelic/.env
# the signing key is set once, at first setup; never regenerate it in place: rotate it as SECURITY.md §7 describes
sed -i "s|^MYCELIC_EVENT_SIGNING_KEY=.*|MYCELIC_EVENT_SIGNING_KEY=$(openssl rand -hex 32)|" deploy/mycelic/.env
sed -i "s|^NATS_PASSWORD=.*|NATS_PASSWORD=n$(openssl rand -hex 32)|" deploy/mycelic/.env   # must start with a letter

# 2. run
docker compose -f deploy/mycelic/docker-compose.yml up -d --build

# 3. check
curl -s http://localhost:8080/health
# {"status": "ok", "version": "0.2.0", "transport_connected": true, "consumer_running": true}
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

res = client.query("Rotterdam strike sd-9", scope="northwind")   # keyword search: ask with words the notes use
if res["answer"] is None:
    print("nothing relevant visible to this agent yet")
else:
    print(res["answer"]["text"], res["answer"]["lineage"])
    graph = client.lineage(res["answer"]["memory_id"])
    report = client.verify(res["answer"]["memory_id"])       # derived correctly, and still true?
    print(report["verdict"])
```

A local note leaves the agent's disk only through `local.share(...)`: a note tagged `private` is refused, and a local
id names one note for good (`local.note` with the id of a different note raises). `share` records the request before
sending it, so `local.pending()` lists the shares the server never acknowledged, with the visibility, expiry and
`supersedes` they were asked with, and the reference agent re-sends exactly those at start. A local store written by an
earlier SDK has no such record: its unacknowledged notes stay local until the agent shares them again.

Or from the shell: `python -m mycelic query "delivery risk sd-9" --scope northwind --lineage`
(`MYCELIC_API_KEY` set); its lineage line ends with the answer's memory id, which `python -m mycelic verify <id>`
checks. Retrieval matches the words of a question against the notes' text, topic, entity and slot, so a question
about something no shared note mentions has no answer (`answer` is null, and the CLI says so and exits 1). A complete agent process is `python -m mycelic.sdk.agent --help`.

Claude Code / any MCP client (the agent key is the Bearer token; identity is per request):

```bash
claude mcp add --transport http mycelic http://localhost:8080/mcp --header "Authorization: Bearer $MYCELIC_API_KEY"
# stdio for desktop clients:
python -m mycelic mcp --url http://localhost:8080 --api-key "$MYCELIC_API_KEY"
```

Tools: `mycelic_query`, `mycelic_remember`, `mycelic_lineage`, `mycelic_verify`, `mycelic_get_memory`, `mycelic_status`, `mycelic_attest`.

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
  that is about two notes per second at most), and make rule changes in quiet periods. The shipped
  `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=5000` enforces that limit, measured again for this release: a `/query` right
  after a write stays at or under 1 s at the 95th percentile up to 5,000 notes and not at 10,000 (section 4, "Volume
  cap"). A re-aggregation run
  enumerates its organization's keys again at every step, so its duration grows faster than the organization; it
  yields between steps, so the node keeps serving meanwhile.
* **One NATS server** with a file-backed JetStream store. A 3-node JetStream cluster is a drop-in change on
  the broker side (`num_replicas` is a stream setting; Mycelic sets 1) and is not covered by this release.
* **TLS.** Terminate at a reverse proxy or Ingress and set `MYCELIC_ALLOWED_HOSTS` to its hostname and
  `MYCELIC_TRUST_PROXY_HEADERS=true` (with `MYCELIC_TRUSTED_PROXY_HOPS` = the number of proxies that append
  to `X-Forwarded-For`, default 1) so rate limiting sees client addresses; only do this when the proxy is the
  only thing that can reach the service. Any other `Host` is answered 421, except on `/`, `/health` and `/ready`
  (probes send the pod or container address), on `/metrics` whenever it checks a token (a Prometheus scrape sends the
  pod or service address: a ServiceMonitor `<pod IP>:8080`, the compose profile `mycelic:8080`) and for a loopback
  `Host` (`localhost`, `127.0.0.1`, `[::1]`: `kubectl port-forward`, the CLI inside the container), none of which can
  send the public name. Alternatively let Mycelic serve TLS itself with
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
| `REPLAY_MAX_REJECT_RATIO` | `0.01` | share of the events a replay consumes whose signature may be rejected (0 to 1); above it `/ready` stays 503 until a rebuild with the corrected keyring (section 4, "Signing-key mistakes"). `0`: any rejection blocks; `1`: never. Set in `.env`, the compose file and the ConfigMap |
| `METRICS_TOKEN` | | bearer for `/metrics`; unset ⇒ admin token or agent key required off loopback |
| `READY_REQUIRES_NATS` | `false` | `/ready` fails during a broker outage when true. Without it a node stays ready through an outage, also when it (re)starts during one, provided its database has applied the log before: it waits at most 10 s for the broker, then serves from the database and keeps connecting. A node with a fresh database is not ready until the broker answers (only the stream says whether a log must be replayed into it) |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | | serve HTTPS directly |
| `ALLOWED_HOSTS`, `CORS_ORIGINS` | | Host allow-list (421 otherwise; not applied to the probes, a token-checked `/metrics` or a loopback `Host`, see "TLS" in section 2); browser origins (off by default) |
| `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_HOPS` | `false`, `1` | use the Nth-from-the-right `X-Forwarded-For` entry for rate limiting and audit (only behind a proxy that is the sole path in) |
| `RATE_LIMIT_RPS`, `RATE_LIMIT_BURST` | `50`, `100` | per peer address before auth and per principal after |
| `MAX_BODY_BYTES`, `MAX_TEXT_CHARS`, `MAX_BATCH`, `MAX_EVENT_BYTES` | `1 MiB`, `4000`, `100`, `256 KiB` | input limits |
| `AUDIT_RETENTION_DAYS` | `90` | audit rows older than this are pruned at start |
| `MAX_ACTIVE_MEMORIES_PER_ORG` | `0` (no limit) | active raw notes one organization may hold; a new note past it is refused with 507 until retractions apply (section 4, "Volume cap", which measured the value `.env.example`, the compose file and the ConfigMap ship) |
| `MIN_SUPPORT` | `2` | distinct child units needed for a consolidated memory. Changing it re-derives every consolidation at the next start (the re-aggregation job, section 4). It is deployment configuration, not part of the event log, so a rebuild from the log uses the value the rebuilding node runs with |
| `VERIFY_MAX_NODES` | `25000` | nodes one downward verification walks at most; beyond it the verdict is `unverifiable` (`walk_truncated`). Set in `.env`, the compose file and the ConfigMap |
| `EXPIRY_SWEEP_SECONDS` | `30` | how often the expiry sweep queues the retraction of notes past their `expires_at`, at most 100 per sweep (section 4, "Expiry"); `0` disables it (expired notes are still left out of answers, but stay evidence until retracted). Set in `.env`, the compose file and the ConfigMap |
| `DERIVED_RETENTION_DAYS` | `7` | days a previous version of a consolidation or conclusion (superseded or retracted) is kept; a sweep every 60 s then deletes it with its lineage edges and its derived event row (section 4, "Retention"). Fractions are allowed; `0` keeps every version forever, and the database then grows without bound. Set in `.env`, the compose file and the ConfigMap |
| `RULES_FILE` | | JSON file of slot-composition rules re-applied at every start (`deploy/mycelic/rules.json`); a file rule overrides an API edit to the same `rule_id`, and the override is appended to the event log so a rebuild ends with the same rules. Rule fields are listed in section 3a |
| `PUBLIC_URL` | | informational |

### 3a. Rules

A rule (`deploy/mycelic/rules.json`, or `POST /admin/rules`) is a conclusion that exists only once every
required slot is covered inside its target unit. A rule evaluated without an entity (a `*` conclusion, which takes
evidence that names no entity, such as a context note "demand is committed") rests on evidence about one entity at
most next to that: evidence about two entities is never stitched into one conclusion, and a `*` conclusion exists only
when its selection includes evidence that names no entity. With `corroborate`, its evidence is all of that: every memory
that names no entity and every memory about the one entity whose pool is best (the strongest selection, then the first
entity in sort order), whichever of them is the strongest:

| Field | Meaning |
|---|---|
| `rule_id`, `target_layer` | identity; the layer of the unit the conclusion belongs to (`team` … `enterprise`) |
| `required_slots` | slots that must all be filled for the same `entity` |
| `conclusion` | template with `{entity}` and `{slot:<name>}` placeholders |
| `topic_prefix` | only evidence whose topic starts with it counts |
| `min_agents`, `min_teams` | distinct agents / teams behind the evidence. Without `corroborate` the evidence is one memory per slot: the strongest of each slot, or, when those miss a threshold (one agent is the strongest in several slots), the selection that meets the thresholds whose weakest memory is strongest, so a note that agrees with the evidence never withdraws a conclusion. The search for that selection tries at most 20,000 selections per evaluation (a `*` evaluation runs one search for every entity's pool, not one per entity); when it stops there, the best one it found stands, and the rule does not hold only if it found none (a warning is logged once per rule; docs/MYCELIC_ARCHITECTURE.md §5, "Slot composition") |
| `sources` | operators that may fill a slot: `agent_observation` (default), `slot_composition` (other rules' conclusions), `topic_consolidation` |
| `emits_slot`, `emits_topic` | what the conclusion carries, so a higher rule can consume it |
| `min_units` | corroboration per slot, e.g. `{"supply_risk": {"region": 2}}`: the units behind the evidence filling that slot must include two regions. The count is taken over the memories that become parents, so without `corroborate` it is the units behind the one memory selected for the slot; a count above 1 therefore needs `corroborate: true` unless that one memory itself spans the units (a consolidation or an already corroborated conclusion) |
| `corroborate` | every memory filling a required slot becomes evidence (lineage and support include all of them); confidence per slot is the noisy-OR over the units filling it, unless they claim different values for it ("Disputed claims", section 4): then the strongest one counts and the conclusion carries `metadata.conflict` (a rule without `corroborate` flags such a slot too) |
| `kind`, `org_id`, `enabled`, `metadata` | memory kind of the conclusion; restrict to one organization (its enterprise segment, `[a-z0-9][a-z0-9_-]{0,63}`: anything else, `Northwind` or `north wind` say, would match no organization and is refused with 400, in the rules file too, where it stops the start); switch off; free-form |

Labels are normalised wherever they enter: topics, slots and entities of notes and query filters, and a rule's
`required_slots`, `emits_slot`, `topic_prefix`, `emits_topic`, `min_units` slots and the `{slot:<name>}`
placeholders of its `conclusion` (NFKC, case-folded, whitespace collapsed; `SD-9`, ` sd-9 ` and `Sd-9` are one
entity, `{slot:Transport_Disruption}` is `{slot:transport_disruption}`). Slots must still match
`[A-Za-z0-9_.:-]{1,100}` afterwards, so `Transport Disruption` is refused with a 400. `GET /admin/rules` returns
the stored, normalised form. A rule with neither `emits_topic` nor `topic_prefix` gives its conclusions the
normalised `rule_id` as topic.

Quoting evidence is a publication decision. A rule whose conclusion template quotes `{slot:...}` publishes the
quoted evidence, whatever its visibility, at the rule's target layer and, through consolidations of the
conclusion's topic, at every layer above it. A template that names only `{entity}` publishes no note's text. A
conclusion reaches those consolidations next to its own unit's consolidation of the same topic, never instead of it,
also when the conclusion's topic is the topic its evidence is written on (a `topic_prefix` equal to that topic and no
`emits_topic`).

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

**Health.** `GET /health` is liveness (200 while the database is open and every background loop runs; `status` is
`ok` or `degraded`, the latter when the broker is unreachable, the broker reports an error (`checks.transport.error`,
the stream missing after a broker state reset, say), a publish keeps failing (`checks.publisher.last_error` and
`failing_seconds`), the database keeps failing the consumer's applies (`checks.consumer.last_error` and
`failing_seconds`) or readiness is blocked; 503 and `failing` when the database fails or a loop task ended although the
service was not stopping, `checks.dead_loops`, so the liveness probe restarts the process). The publisher, consumer and
transport loops never end on an error they did not expect (a full disk, an I/O error): each logs it at ERROR with its
traceback, counts `mycelic_loop_errors_total{loop}` (alert on any increase) and backs off; after such an error while
handling a delivery the consumer resynchronises with the database exactly as at a start, so an event the broker
settled but the database did not record is delivered again. `GET /ready` is readiness (database
open, loops running, no replay in progress); it is also 503, with `"reason": "signature_rejections: …"`, while a
replay that rejected too many signatures blocks it ("Signing-key mistakes" below). Both answer from a status snapshot that a background task refreshes every
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
   runs and copy the backup out of the volume ("Restoring the database" below has the commands), or snapshot the
   volume while the service is stopped;
2. the JetStream store (`nats-data` volume): snapshot the volume while the broker is stopped, or use the
   `nats` CLI (`nats stream backup MYCELIC <dir>`) against port 4222 from inside the network;
3. the signing keys, `MYCELIC_EVENT_SIGNING_KEY` and `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`: store them with every
   database backup. Without the keys that signed them, the digests of a restored database read `unknown_key` and a
   rebuild from the stream rejects every event (SECURITY.md §7), which keeps `/ready` at 503 ("Signing-key
   mistakes" below). Section 4a has the paired snapshot of both volumes that a rollback needs.

Restore order matters: a database older than the stream is caught up automatically (the service detects
that its `last_applied_seq` is behind the consumer's ack floor and re-delivers from the next sequence). A
missing database is rebuilt in full from the stream. A missing stream with an intact database keeps
serving, but nothing can be replayed until new events accumulate; restore the stream from its backup
before restoring an older database.

**Restoring the database.** Stop the service, **remove `mycelic.db-wal` and `mycelic.db-shm`**, copy the backup in as
`mycelic.db` owned by uid 10001 (the image's user), start. After a crash or a kill those two files hold the
write-ahead log of the database being replaced, and SQLite replays it onto whatever file is called `mycelic.db`: onto a
restored backup that gives a mix of pages of both, neither the backup nor the lost state, whose `last_applied_seq`
hides the gap from recovery. Every start runs SQLite's `quick_check`, which reads the whole file (0.3 s for a 370 MB database
already in the page cache in this repository's sandbox, plus the time to read it from a cold disk), and refuses such a
database: the service exits with `database error: database /data/mycelic.db
fails SQLite's quick_check (...)` and writes nothing to it (`test_a_backup_restored_over_a_stale_wal_is_refused_and_the_documented_restore_works`).
A log that happens to hold every page it touched replays cleanly instead and silently undoes the restore, so remove
the two files every time. The backup holds every note's text: keep it outside the checkout, readable only by you.

```bash
# Compose: back up while the service runs, then copy the backup out of the volume
BACKUP_DIR="$HOME/mycelic-backup/$(date -u +%Y%m%dT%H%M%SZ)"; (umask 077 && mkdir -p "$BACKUP_DIR")
docker compose -f deploy/mycelic/docker-compose.yml exec mycelic python -c "import sqlite3; s=sqlite3.connect('/data/mycelic.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
docker compose -f deploy/mycelic/docker-compose.yml cp mycelic:/data/backup.db "$BACKUP_DIR/mycelic.db" && chmod 600 "$BACKUP_DIR/mycelic.db"
docker compose -f deploy/mycelic/docker-compose.yml exec mycelic rm /data/backup.db

# Compose: restore it (the volume is mycelic_mycelic-data, after the compose project)
BACKUP_DIR="$HOME/mycelic-backup/<the backup to restore>"
docker compose -f deploy/mycelic/docker-compose.yml stop mycelic
docker run --rm -v mycelic_mycelic-data:/v -v "$BACKUP_DIR":/b alpine sh -c 'rm -f /v/mycelic.db-wal /v/mycelic.db-shm && cp /b/mycelic.db /v/mycelic.db && chown 10001:10001 /v/mycelic.db'
docker compose -f deploy/mycelic/docker-compose.yml start mycelic

# Kubernetes (not executed against a cluster): the same, with kubectl cp and a one-off pod on the data volume
kubectl -n mycelic exec mycelic-0 -- python -c "import sqlite3; s=sqlite3.connect('/data/mycelic.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
kubectl -n mycelic cp mycelic-0:/data/backup.db "$BACKUP_DIR/mycelic.db" && chmod 600 "$BACKUP_DIR/mycelic.db"
kubectl -n mycelic exec mycelic-0 -- rm /data/backup.db
kubectl -n mycelic scale statefulset/mycelic --replicas=0
kubectl -n mycelic run mycelic-restore --image=busybox --restart=Never --overrides='{"apiVersion": "v1", "spec": {"securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001}, "containers": [{"name": "mycelic-restore", "image": "busybox", "command": ["sh", "-c", "rm -f /data/mycelic.db-wal /data/mycelic.db-shm && sleep 3600"], "volumeMounts": [{"name": "data", "mountPath": "/data"}]}], "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data-mycelic-0"}}]}}'
kubectl -n mycelic wait --for=condition=Ready pod/mycelic-restore
kubectl -n mycelic cp "$BACKUP_DIR/mycelic.db" mycelic-restore:/data/mycelic.db
kubectl -n mycelic delete pod mycelic-restore
kubectl -n mycelic scale statefulset/mycelic --replicas=1
```

At its start the restored node catches up from the stream ("stale database restored from backup" below). A
database that fails `quick_check` for another reason is restored the same way, or moved aside to be rebuilt from the
stream ("database lost or corrupt" below). The Compose commands were rehearsed in this repository's sandbox on a scratch
project (its volume name in place of `mycelic_mycelic-data`): 3 notes before the backup, 40 after it, the service
killed (it left a 4 MB `mycelic.db-wal`), then the restore: `/ready` answered 200, the log said "database applied up to
seq 7 but the consumer acknowledged up to 47 (restored backup?): re-delivering from 8", and all 43 notes were there.

**Recovery procedures** (service crash, broker outage and database loss are exercised by
`tests/smoke/mycelic_smoke.py` steps 6–8; the other rows by the named tests in `tests/mycelic/test_jetstream.py`):

| Situation | What to do |
|---|---|
| service crashed | `docker compose -f deploy/mycelic/docker-compose.yml start mycelic` (or let `restart: unless-stopped` do it); it resumes from the durable consumer |
| broker down | nothing: writes are accepted and queued in the outbox; `/health` reports `degraded`; the queue flushes on reconnect. `mycelic_outbox_pending` shows the depth |
| database lost or corrupt | stop the service, remove `/data/mycelic.db*`, start it: it logs "fresh database but the stream holds N events: replaying" and rebuilds memories, lineage, agents (their keys keep working) and rules. `mycelic_replay_events_total` counts progress; `/ready` is 503 until the replay finishes, and the target is stored in the database so a crash mid-rebuild resumes it (`test_lost_database_is_rebuilt_from_the_stream`, `test_unfinished_replay_resumes_after_a_crash`). The replay deletes previous versions as it goes, on the log's clock (section 4, "Retention"), so the rebuilt database needs about the room the lost one had, not room for the log's whole history |
| stale database restored from backup | restore it as "Restoring the database" above says (the service stopped, `mycelic.db-wal` and `mycelic.db-shm` removed), then just start it: missing events are re-delivered (`mycelic_recovery_total{kind="replay_restored_backup"}`; `test_restored_backup_receives_the_events_it_missed`) |
| the service exits with `database error: … fails SQLite's quick_check` | the database file is damaged, most often a backup copied over it with the `-wal` and `-shm` of the crashed node left in place: restore the backup again as "Restoring the database" above says, or move the database aside to rebuild it from the stream (row "database lost or corrupt") |
| durable consumer lost (broker state reset) | nothing, whether the service is running or starts afterwards: the consumer is recreated after the last applied sequence (`kind="consumer_recreated"`; `test_lost_consumer_is_recreated_after_the_last_applied_event`) |
| stream shorter than the database (purged or recreated) | the database keeps serving; the log restarts from the new sequence and `kind="stream_behind_database"` is counted; restore the stream backup first when you can. Only a stream length the broker reported counts: while it cannot say (a timeout, JetStream not ready yet after a restart) nothing is judged, the consumer logs "did not report the stream's state", fetches nothing and retries with backoff (`test_a_resync_while_the_broker_cannot_report_its_stream_judges_nothing`, `test_a_start_while_the_broker_cannot_report_its_stream_judges_once_it_can`) |
| broker came back without its JetStream state (volume or PVC lost, stream deleted) while the service runs | nothing: the reconnect (or a publish or status read that finds no stream or consumer) makes the service recreate the stream and the consumer, audit `recovery.broker_state_reset`, count `kind="broker_state_reset"` and continue as for a shorter stream (row above); the outbox drains without a restart. Until then `/health` is `degraded` with `checks.transport.error` and `checks.publisher.last_error` (`test_broker_state_reset_is_recovered_without_a_restart`) |
| the database failed under a loop (disk full, I/O error) | free the space or fix the disk; nothing else: the loops back off and retry (`mycelic_loop_errors_total`), and nothing the broker delivered meanwhile is lost. An event whose apply the database failed is never counted toward `NATS_MAX_DELIVER` or terminated: the consumer is recreated at that event, so it is delivered again until the database takes it, and `/health` is `degraded` with `checks.consumer.last_error` meanwhile (`LoopResilienceTests`, `test_a_database_error_while_applying_never_drops_the_event`, `test_a_database_error_while_terminating_loses_no_event`) |
| force a replay | `python -m mycelic replay` or `POST /admin/replay` (`test_forced_replay_is_idempotent`) |
| force a re-aggregation | `python -m mycelic reaggregate [--org ORG]` or `POST /admin/reaggregate` with `{"org_id": …}`, or with no body or a null `org_id` for every organization (an empty `org_id` is refused with 400): re-derives every consolidation and conclusion of one organization or all of them in the background while the node keeps serving; `{"started": false}` while a run is in progress. Progress: `GET /admin/status` → `checks.reaggregation`, `mycelic_reaggregation_steps_total`, audit `aggregation.reaggregate` per organization (`test_admin_route_sdk_and_cli`) |
| replay rejected signatures (wrong or missing key) | `/ready` is 503 with `"reason": "signature_rejections: rebuild with the corrected keyring required"`, `/health` 200 and `degraded`, audit `recovery.signature_rejections`, `mycelic_recovery_total{kind="replay_signature_rejections"}`: follow "Signing-key mistakes" below (`test_wrong_signing_key_replay_blocks_readiness_until_fresh_rebuild`) |
| poison or rejected event | an event that fails for any reason but the database's own after `NATS_MAX_DELIVER` attempts is terminated and recorded (`GET /admin/events?org=…&status=failed`, audit `event.failed`, `mycelic_events_failed_total{stage="apply"}`: alert on any increase); a terminated or unsigned event counts as consumed, so a replay still completes (`test_poison_event_is_terminated_and_replay_completes`); a replay judges the signatures it rejected, see the row above. Once the cause is fixed (a code fix, say), `POST /admin/replay` applies the terminated events again |
| an event the stream can never take (outbox stuck behind it) | an event whose subject is not one the stream takes (an earlier release accepted a rule whose `org_id` was no organization name, `north wind` say) is marked failed at once (audit `event.unpublishable`, `mycelic_events_failed_total{stage="publish_permanent"}`), so the outbox behind it drains; delete or fix the rule (`DELETE /admin/rules/{id}`, then upsert it with a valid `org_id`) (`RuleOrganizationTests`) |

**Volume cap.** `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG` caps the active raw notes of each organization. A new note
that would pass it is refused with 507 and `{"error": "organization '<org>' is at its limit of <cap> active notes
(MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG; <n> active now): retract notes it no longer needs (a retraction counts once it
has been applied) or ask the operator to raise the limit"}`; MCP `mycelic_remember` returns an error result with the
same text, and the SDK raises `MycelicError` 507 at once (it retries only 429, 502, 503 and 504). The count is taken
inside the write's transaction, so concurrent writes never pass the limit together. It includes every active raw note
of the organization whether its event has been applied yet or not, the notes of revoked agents, and expired notes
until the sweep's retraction applies; a retraction, an expiry or an agent removal frees room when its event applies,
not when it is accepted. Never counted or refused: a resend of a stored note (200), an embedded memory of `POST
/events` that already exists, plain events, derived memories and updates (`supersedes`). An update replaces an active
note, at most one update per note can be pending (409), and the count is back where it was once it applies; an update
that races a retraction of its target and applies as a plain note (`memory.update_conflict`) can leave the
organization one note over the limit. While the consumer lags (a broker outage, say), a pending update is counted
beside the note it replaces, and an agent may update its own pending update, so the count can run above the limit by
the number of pending updates; it falls back once they apply. A `POST /events` batch whose new embedded memories would pass the limit is
refused whole: none of its events, memories or audit rows is written. In a JSON-RPC batch on `/mcp` each
`mycelic_remember` is its own write: the calls past the limit get error results, the earlier ones succeed. Nothing
the consumer applies is capped (the apply path, replays, the expiry sweep, retractions and attestations), so a rebuild
applies the whole log; lowering the limit deletes nothing and refuses new notes until the count is under it again.
`mycelic_quota_rejections_total` counts the refused requests. `0` turns the cap off (the code default).

The shipped value was measured with `tests/perf/mycelic_volume.py` (not collected by CI) on this repository's 4-CPU
sandbox (Python 3.11.15). It builds one organization of 96 agents in 32 teams across 2 regions through the consumer's
apply path, with the rules of `deploy/mycelic/rules.json` and a signing key, fed notes over 3 slots and about 50
entities, and saves the database at 2,000, 5,000 and 10,000 active notes, timing the apply of the last 200 notes
before each. Then it serves each saved database with `nats-server` and `python -m mycelic serve` (same key and rules,
`MYCELIC_RATE_LIMIT_RPS=1000`) and times 200 `POST /query` calls at the client, each sent right after a `POST /memory`
by a random agent, so a query waits for the note before it to apply. The build took 46 minutes, with other processes
sharing the machine for part of it, and the serve phase 8 minutes:

```bash
python tests/perf/mycelic_volume.py --workdir /tmp/mycelic-volume --phase build
python tests/perf/mycelic_volume.py --workdir /tmp/mycelic-volume --phase serve --out /tmp/mycelic-volume.json
```

| active raw notes | DB MB | apply p50 / p95 / max (s, last 200 notes) | /query after a write p50 / p95 / max (s, 200 runs) | qualifies |
|---|---|---|---|---|
| 2,000 | 65 | 0.113 / 0.165 / 0.241 | 0.251 / 0.411 / 0.525 | yes |
| 5,000 | 178 | 0.293 / 0.374 / 0.474 | 0.507 / 0.983 / 1.311 | yes |
| 10,000 | 387 | 0.456 / 0.586 / 0.795 | 0.922 / 1.605 / 1.872 | no |

A level qualifies when both p95s are at most 1.0 s, and the shipped value is the largest level that does. 5,000 passes
with little room (a `/query` p95 of 0.983 s), so treat it as a ceiling on hardware like this, not a target; the
`--phase all` default runs both phases in one command.

Shipped value: `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG=5000`

That run spread its notes over about 150 topics. Notes concentrated on one topic cost more: every note applied on a
topic leaves a previous version of each consolidation above it behind (a team, a department and every unit up to the
enterprise), whose roots and lineage edges grow with the topic, so the versions kept grow with the square of the
topic's size. An audit build of the same organization with every note org-visible on one topic measured 116 MB at
1,000 notes and 1,787 MB at 5,000, and 1,000 notes churned (one retracted and one shared per cycle) added 328 KB per
cycle, before the two bounds below existed. "Retention" says what bounds them now.

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
cannot be registered again (only a rollback to an earlier release undoes a removal: see section 4a).
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

**Disputed claims.** A note may carry `value`, what it claims for its slot and entity (`POST /memory`, an embedded
memory of `POST /events`, MCP `mycelic_remember`, `client.remember(..., value=...)`, `LocalMemory.note(..., value=...)`
(which `share` sends, also when it re-sends an unacknowledged share), and `"value"` in the reference agent's
observations, next to `"expires_at"`; stored as `metadata.value`,
normalised like a label, at most 200 characters; a `metadata.value` sent next to it must agree with it). Values are
compared in that normalised form, so "Open" and "open" agree. `metadata` stays free-form: a `metadata.value` sent
without `value` is stored exactly as sent and never refused, and it is the note's claim only when it is a string of
at most 200 characters once normalised (a number, an object or a longer string claims nothing). A raw note's
`metadata` is shown only to its producer and administrators, as before. Notes whose values
differ for one slot and entity dispute each other: every consolidation built on them carries `metadata.conflict: true`,
takes the confidence of its strongest contribution instead of raising it, and claims no slot, so no rule takes it as
evidence. Every rule compares the values of the memories that could fill each slot and flags its conclusion
`metadata.conflict` when one slot holds two values for one entity: a rule with `corroborate` does not raise that slot's
confidence; a rule without it rests on the strongest memory claiming each value next to its selection, so both sides
are in the lineage and in `support`, and the flag goes when one side is retracted. A conclusion resting on a disputed
conclusion is flagged too, and downward verification warns `disputed` (it never changes the verdict; a query answer
says so in `conflict`, and with `"verify": true` in `verification.disputed`). A consolidation whose value-carrying parents agree carries their `value`. Free text
is never compared: notes without a `value` never dispute anything, so give a note a value whenever its slot is a status
that can be contradicted ("open", "closed"). Every value is compared at every layer above its note, whatever the
note's visibility, so two teams that disagree are flagged above them also when their notes are team-visibility (the
default); the value of a team-visibility note is never published above its team (no `metadata.value` there), so the
flag is all that leaves it (SECURITY.md §6). `support` still counts the agents on both sides.

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
(`client.query(..., verify=True)`, `python -m mycelic query --verify`, MCP `mycelic_query` with `verify: true`) adds
`answer.verification` with the verdict, both answers, the reason codes, the warnings counted per code and
`disputed`: true when notes beneath the answer claim different values ("Disputed claims" above). A dispute is a
warning, so its verdict stays `verified`; do not act on a disputed answer before the dispute is resolved (the CLI
prints `, disputed` after the verdict, and every answer, verified or not, carries `conflict`). MCP clients call
`mycelic_verify`.
Over MCP the report is bounded by default (`detail` `summary`): every key of the report, but `nodes` lists only the
nodes that did not pass and `warnings` the first ones, at most 50 of each, with `nodes_omitted` and
`warnings_omitted` counting the rest, so a conclusion over thousands of notes still fits an MCP client's result
limit; `detail: "full"` lists every node, as `GET /verify` does. `max_leaf_age` (1 to 315,360,000
seconds, optional) also requires every raw note of the walk, readable by the caller or not, to have been ingested by
the server, or re-attested by its producer, within that many seconds: a note the caller may not read is judged too and
shows `hidden_stale`, so a reader above every contributing team gets the same verdict as an administrator.
`freshness_partial` is true only when some raw note could not be judged (the walk was truncated, or a note's event row
is gone); the CLI prints it.

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

Limits. A walk reads at most `MYCELIC_VERIFY_MAX_NODES` (25,000) nodes. It runs in a worker thread on a read-only
snapshot of the database, never on the event loop, so `/health`, `/ready`, the API and the consumer keep answering
while it runs. Walks take turns: one at a time per caller, at most two at once per organization and four for all
organizations together, an organization's walks waiting for each other first, so one organization's agents never take
the slots another's need. A walk whose turn has not come within 5 s is refused with 503 and `Retry-After: 1` (an MCP
error result; the SDK retries it; `mycelic_walks_refused_total{kind}`), so the queue never grows longer than that. A
request whose client went away (an SDK timeout, say) is cancelled: a walk it queued never runs, and one already
running holds its slot until its thread ends, unanswered and unaudited. It costs the caller its request's token at
the door and, after the walk, the larger of ceil(nodes / 250) and ceil(2 × seconds walked ×
`MYCELIC_RATE_LIMIT_RPS`) tokens from its principal bucket, which may take the bucket into debt, as deep as the charge
(an abandoned walk pays for its time). A caller that keeps walking therefore pays twice what its bucket refills
meanwhile, however long its walks run. A principal in debt is refused with 429 (an MCP error result inside a JSON-RPC
batch) before anything is read, and again when its turn to walk comes, until its bucket refills. Walk times measured in this repository's sandbox with
`service.verify` over one team's org-visible notes: 0.10–0.20 s for 1,005 nodes and 0.56–0.85 s for 5,005 nodes
(five runs of five calls each on an otherwise idle 4-CPU machine; slower while other work runs). Measured in-process
at the shipped limits (rps 50, burst 100, 5 s queue) on a 5,005-node conclusion, one MCP batch of 100 `mycelic_verify`
calls (`detail` `full`) and eight agents of the same organization looping `GET /verify` for 10 s, in three runs (the
range across them; other jobs kept the 4-CPU machine's load average at 3.30–3.45 as each run started): the batch walked
0 and refused all 100 as busy (MCP error results, the 503 of REST), without walking or paying for a walk, because the
eight looping agents' walks held, and queued for, the organization's two slots, so none of the batch's turns came
within 5 s; the loops got 6–9 reports, 2,314–4,982 × 429 and 5–8 × 503, and `/health`, polled every 0.25 s, answered
all 35–37 probes, the slowest in 0.165–0.285 s (`test_verification_never_holds_the_event_loop` checks the loop keeps
turning during walks).
Lineage walks (`GET /lineage/{id}`, MCP `mycelic_lineage`, and the answer of every `POST /query` and MCP
`mycelic_query`) run the same way, with turns of their own: in a worker thread, one per caller at a time (lineage and
verification alike), at most two at once per organization and four for all of them, refused with 503 after 5 s
without a turn, at most 2,000 memories each (`complete: false` beyond), priced after the walk at the larger of floor(nodes / 250) and
floor(2 × seconds walked × `MYCELIC_RATE_LIMIT_RPS`) tokens, so a lineage of fewer than 250 nodes walked in under
10 ms (at rps 50) costs only its request's token. The SDK retries the 429 a caller in debt gets, with backoff
(`test_lineage_walks_never_hold_the_event_loop_and_are_priced`).

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

**Signing-key mistakes.** A rebuild verifies every event of the stream against the configured keys. When the key was
regenerated in place, or a previous key was dropped, every event those keys signed is rejected (each audited as
`event.rejected` with its `seq` and `subject`) and everything it carried is missing: notes, rules and agent
registrations (old agent keys then get 401). A replay counts the events it consumes and those whose signature it
rejected, in the transaction that consumed each one (a crash in the middle keeps the counts). When it completes with
more than `MYCELIC_REPLAY_MAX_REJECT_RATIO` (1%) rejected, it records a block in the database: `/ready` answers 503
with `"reason": "signature_rejections: rebuild with the corrected keyring required"`, so Kubernetes (or a load
balancer that checks it) sends it no traffic, while `/health` stays 200 with `status` `degraded`, so the startup and
liveness probes never restart it. The replay is
audited as `recovery.signature_rejections` (events consumed, rejected, ratio, configured ratio, blocked), counted in
`mycelic_recovery_total{kind="replay_signature_rejections"}` (alert on any increase), logged at ERROR with the remedy,
and shown in `GET /admin/status` as `checks.consumer.ready_block`. A replay under the ratio only warns and audits.
The block survives restarts, and a replay into the same database (`POST /admin/replay`, `python -m mycelic replay`)
cannot repair it: it runs and answers 202 with a `warning`, because events that depended on the rejected ones (a note
whose producer's registration was rejected, say) may have been applied without them, and an applied event is never
applied again. The remedy is a rebuild into a fresh database with the corrected keyring:

```bash
# Compose: stop the service, keep the current key and add the key that signed the rejected events to the previous ones
env=deploy/mycelic/.env; old='<the key that signed the rejected events>'
docker compose -f deploy/mycelic/docker-compose.yml stop mycelic
grep -q '^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=' "$env" || echo 'MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=' >> "$env"
prev=$(sed -n 's/^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=//p' "$env")
sed -i "s|^MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=.*|MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS=${prev:+$prev,}$old|" "$env"
# move the blocked database aside (keep it: the audit rows of API calls are not in the stream), then start to rebuild
docker compose -f deploy/mycelic/docker-compose.yml run --rm --no-deps --entrypoint sh mycelic -c 'mkdir -p /data/aside && mv /data/mycelic.db* /data/aside/'
docker compose -f deploy/mycelic/docker-compose.yml up -d mycelic

# Kubernetes (not executed against a cluster): the same steps, with a one-off pod on the data volume
old='<the key that signed the rejected events>'
prev=$(kubectl -n mycelic get secret mycelic-secrets -o jsonpath='{.data.MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS}' | base64 -d)
kubectl -n mycelic patch secret mycelic-secrets --type merge -p "{\"stringData\": {\"MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS\": \"${prev:+$prev,}$old\"}}"
kubectl -n mycelic scale statefulset/mycelic --replicas=0
kubectl -n mycelic run mycelic-aside --image=busybox --restart=Never --overrides='{"apiVersion": "v1", "spec": {"securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001}, "containers": [{"name": "mycelic-aside", "image": "busybox", "command": ["sh", "-c", "mkdir -p /data/aside && mv /data/mycelic.db* /data/aside/"], "volumeMounts": [{"name": "data", "mountPath": "/data"}]}], "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "data-mycelic-0"}}]}}'
kubectl -n mycelic wait --for=jsonpath='{.status.phase}'=Succeeded pod/mycelic-aside && kubectl -n mycelic delete pod mycelic-aside
kubectl -n mycelic scale statefulset/mycelic --replicas=1
```

`/ready` turns 200 once the rebuild has replayed the stream. A blocked pod is unready, so the Service and the Ingress
do not reach it; reach it with `kubectl -n mycelic port-forward pod/mycelic-0 8080:8080` and
`curl -H "Authorization: Bearer $MYCELIC_ADMIN_TOKEN" http://localhost:8080/admin/status`. When the rejected events
really are forgeries (the `event.rejected` audit rows say which), there is nothing to restore: set
`MYCELIC_REPLAY_MAX_REJECT_RATIO` to at least the recorded `ratio` (in `checks.consumer.ready_block`, the audit row and
the ERROR log; rejected/consumed rounded up to six decimal places, so the value as shown is enough: 2 of 7 is recorded
as `0.285715`) and restart. The block no longer holds at that ratio (it stays recorded, and a lower ratio brings it back); `1`
disables the check, and `0` blocks on any rejection. An unkeyed service never rejects a signature, so it never
blocks. Events published while no key was set carry no signature, so a keyed rebuild rejects them too, and with more
than the ratio of them it blocks: accept losing what they carried (raise the ratio as above), or rebuild without a
key, whose rows then read `downgraded` once a key is set. Setting the key before the first event avoids both.

**Upgrades.** Build the new image, `docker compose -f deploy/mycelic/docker-compose.yml up -d --build`. The schema version is stored in the
database; a newer schema than the code refuses to start. Replays are idempotent across versions. Derived ids embed
the derivation version, `MIN_SUPPORT` and each rule's digest, so after an upgrade that changes how memories are
derived, or a change of `MIN_SUPPORT`, the conclusions converge automatically: the first start runs the
re-aggregation job in the background (the node serves and stays ready meanwhile; it waits while a replay runs). Old
rows stay readable by id for `MYCELIC_DERIVED_RETENTION_DAYS` after they stop being active ("Retention" below). Most are superseded by their new version (`previous_versions` in the
lineage of the successor); a row that rested on a superseded one and was not replaced along with it is retracted
(`status_reason` `evidence superseded`) and derived again as a new memory with no `version_of` link. Until
`GET /admin/status` shows `checks.reaggregation.state` = `done` (and `mycelic_reaggregation_steps_total` stops
rising), answers may mix memories derived by the old and the new version. An interrupted or failed run is
retried at the next start; `python -m mycelic reaggregate` re-runs it on demand. Its derived events are not
reproduced by a rebuild from the log, which derives the converged state directly.

**Responses changed in this release** (derivation version 5):

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
* Lineage walks are priced and bounded: `GET /lineage/{id}`, MCP `mycelic_lineage` and the answer of `POST /query` and
  MCP `mycelic_query` load at most 2,000 memories, also when one consolidation has more parents than that (before, one
  wide level was returned whole; now `complete` is false), and a walk of 250 nodes or more, or one that takes longer
  than 1 / (2 × rps) seconds, costs extra rate-limit tokens, so a caller that keeps walking large lineages gets 429
  (the SDK retries it) until its bucket refills ("Verifying a conclusion", Limits).
* `answer.lineage.contributing_agents` of `POST /query` and `mycelic_query` now counts the distinct agents beneath the
  answer (its `support`) for every viewer. For a caller who could not read some of the notes it used to add every
  redacted node, derived ones included, and over-counted.
* Downward verification only adds: `GET /verify/{id}`, the opt-in `"verify": true` of `POST /query` and of MCP
  `mycelic_query` (without it, or with `false`, the response is unchanged; with it the answer's `verification` holds the
  verdict, both answers, the reason counts, the warning counts (`warnings`: `[{"code", "count"}]`) and `disputed`, true
  when notes beneath the answer claim different values, which leaves the verdict `verified`), the MCP tool
  `mycelic_verify` and `python -m mycelic verify`. Every answer of `POST /query` and `mycelic_query` also carries
  `conflict` (the same dispute, without verifying). Existing responses are unchanged otherwise. `mycelic_verify` answers a bounded report by default
  (`detail` `summary`: `nodes` lists only the nodes that did not pass, at most 50, plus `detail`, `nodes_omitted` and
  `warnings_omitted`); pass `detail: "full"` for every node.
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
* MCP results are bounded: `mycelic_lineage` answers a summary by default (`detail` `summary`: at most 20 memories,
  the edges between them and the first 50 of each list that grows with the evidence, with `detail` and `omitted`);
  pass `detail: "full"` for the whole graph. `mycelic_get_memory` and the results of `mycelic_query` list at most 50
  items of a metadata list (`roots`: one id per raw note beneath) with `omitted` counting the rest. REST responses are
  unchanged.
* Walks queue per organization: a verification, a lineage walk or the answer of a query whose turn does not come within
  5 s (the organization's walks, or everyone's, all taken) is refused with 503 and `Retry-After: 1` (the SDK retries
  it); a walk's rate-limit charge is no longer capped at one burst ("Verifying a conclusion", Limits).
* `memory.derived` events carry no `parents` list and only the metadata keys that do not grow with the evidence
  (`parent_count` replaces the list; "Retention" in section 4). The consumer never read more than their `memory_id`.
* Previous versions of consolidations and conclusions are deleted `MYCELIC_DERIVED_RETENTION_DAYS` (7) after they stop
  being active; their ids then answer 404.
* A rule whose `org_id` is not an organization name (an enterprise segment, `[a-z0-9][a-z0-9_-]{0,63}`) is refused with
  400, at `POST /admin/rules` and in the rules file (before, it was stored and stopped the outbox of every
  organization).
* The volume cap only adds: `POST /memory` and `POST /events` answer 507 when the organization is at
  `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG` ("Volume cap"), and `mycelic_remember` returns an error result then. The
  shipped compose file and ConfigMap set the cap, also for an `.env` from an earlier release that lacks the line.
* Aggregation reaches every layer: a rule conclusion on a topic now reaches every consolidation of that topic above
  its unit next to the unit's own consolidation (before, either one could hide the other), and a unit with fewer
  registered children than `MIN_SUPPORT` keeps what it promotes when a sibling adds evidence (the promotion then also
  has that evidence as parents). Consolidations affected get a new version at the first start (schema 6 above).
* Disputes: `value` on notes is new (`metadata.value`, also `LocalMemory.note(..., value=...)` and `"value"` in the
  reference agent's observations, whose local store gains a `value` column at its first open), and so are
  `metadata.conflict` and `metadata.value` on derived memories ("Disputed claims", section 4). A consolidation or corroborated conclusion whose evidence claims different
  values (team-visibility notes included) is less confident than before and claims no slot. Requests that earlier releases accepted are accepted
  unchanged, `metadata.value` and `metadata.conflict` included, and stored as sent. One reading changes: a note that
  already carries a string `metadata.value` (at most 200 characters) and has a slot and an entity now claims that
  value, compared case-insensitively, so if earlier clients used `metadata.value` for something else, notes on one
  slot and entity whose strings differ are flagged as a dispute once the first start has derived them again
  (`DERIVATION_VERSION` below). A raw note's own `metadata.conflict` flags nothing.
* `/health` answers 503 (`failing`, `checks.dead_loops`) when a background loop task ended while the service was not
  stopping, and `degraded` while publishes keep failing (`checks.publisher.last_error`) or the broker reports an error
  (`checks.transport.error`).
* Readiness only adds: `/ready` may answer 503 with a `reason` (signature rejections, "Signing-key mistakes"), `POST
  /admin/replay` then adds a `warning`, and `GET /admin/status` has `checks.consumer.ready_block`. Without a block,
  `/ready` and `/admin/replay` answer exactly as before, with one difference: a node that (re)starts while the broker
  is unreachable and whose database has applied the log before is ready after at most 10 s (`degraded`), where it
  stayed 503 until the broker returned; a fresh database still waits for the broker. `/health`, `GET /` and the MCP server info report version
  `0.2.0`.
* Rule conclusions: a rule without `corroborate` concludes whenever some selection of one memory per slot meets its
  thresholds (before, only the strongest memory of each slot was tried, so a more confident note from an agent who
  already contributed could withdraw a conclusion), flags a slot whose candidates claim different values for one
  entity (`metadata.conflict`, both sides become parents and count in `support`), and a `*` conclusion never rests on
  evidence about two entities. Verification reports may carry the warning `disputed`, and report the evidence of a `*`
  conclusion an earlier build stitched from two entities as `parent_ineligible`.
* `DERIVATION_VERSION` is 5 (2 before disputes; 3 in pre-release builds whose consolidations did not compare the values
  of team-visibility notes; 4 in pre-release builds whose rules tried only the strongest memory of each slot), so the
  first start re-derives everything, also a database written
  under 2 by schema 5 (`checks.reaggregation.reason` = `pending`, set by the upgrade to schema 6 below, or
  `derivation_version`). Until `checks.reaggregation.state` = `done`, consolidations derived by the earlier release
  keep their old text, which may quote team-visibility notes and agent ids above team level, and they are what
  agents read. Hold agent traffic until then if that matters; once they are superseded their text is withheld
  from agents like that of any other inactive memory.

Upgrading to schema 7 (this release) adds one column to `memories`, `retired_at` (when a row stopped being active;
not covered by digests, so every digest still checks), and the partial index `idx_memories_retired`, in one
transaction at the first start. Rows that are not active get the time of the upgrade, so the retention of previous
versions ("Retention" above) counts from it: the first versions are deleted `MYCELIC_DERIVED_RETENTION_DAYS` after the
upgrade. Nothing is re-derived. Like every schema upgrade it is one-way, so **back up the database first**; an earlier
release refuses the upgraded database (`database schema 7 is newer than this code`).

Upgrading to schema 6 changes no column: a memory's digest now also covers its lifecycle (`status`
unless it is `active`, and `superseded_by`), and every status change signs the row again (SECURITY.md §3). The first
start signs again, before the consumer starts, every row that existed before the upgrade and is not active, each only
if its digest checks in the form it was signed in (`/health` answers meanwhile, `/ready` does not); it is logged at
WARNING, audited once as `integrity.lifecycle_resign` (rows signed, rows left) and never runs again, so any later such
audit row or log line means the database was edited. A row that does not check is left as it is and logged at ERROR.
When the database holds derived memories, the first start also re-aggregates in the background
(`checks.reaggregation.reason` = `pending`): a rule conclusion now reaches every consolidation of its topic above its
unit next to that unit's own consolidation, a unit with fewer registered children than `MIN_SUPPORT` keeps what it
promotes when a sibling adds evidence (docs/MYCELIC_ARCHITECTURE.md §5), and every derived memory is derived again
under `DERIVATION_VERSION` 5, which reads claimed values ("Disputed claims", section 4), so none of them keeps a
derivation that verification would now recompute differently. Like every schema upgrade it is one-way, so
**back up the database first**.

Upgrading to schema 5 adds two columns to `memories`, `expires_at` and `attested_at`, and the partial
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
volume accordingly: an event is a few hundred bytes to a few kilobytes. A `memory.derived` event is informational (the
consumer never inserts it), so it carries the memory's labels, text, counts, `parent_count` and a few bounded metadata
keys, never its roots, contributing agents or parent edges: it stays a few kilobytes however many notes the memory
rests on (`GET /memory/{id}` and `GET /lineage/{id}` have the rest).

The database keeps every raw note (they are the log's), every active memory and, for
`MYCELIC_DERIVED_RETENTION_DAYS` (7 by default) after it stopped being active, each previous version of a
consolidation or conclusion: until then `GET /memory/{id}`, `GET /lineage/{id}` and `GET /verify/{id}` answer for it
(`stale`, with `current_version`). A sweep every 60 s then deletes the versions retired before that, each only once
nothing rests on it any more (so the lineage of every row kept stays whole) and never one whose derived event is still
in the outbox, with its lineage edges and its derived event row, at most 5,000 rows per sweep in transactions of 100;
afterwards those ids answer 404. One audit row `memory.pruned` per sweep counts what it deleted
(`mycelic_memories_pruned_total`, and `GET /admin/status` → `checks.retention`). It is local maintenance, like audit
pruning, and a rebuild from the stream ends where the live node is. A version counts as retired from the log time of
the event that retired it (its `created_at`), on the live node and in a rebuild alike. During a replay the wall-clock
sweep is off and the replay sweeps on the log's clock instead: every 60 s of log time, and again at the next event while
a sweep fills its 5,000 rows. So a rebuild derives each deleted version again and deletes it again as it passes the
point of the log where the live node had, and it needs about the room the live database needed, never room for every
version the log ever derived; its first sweep after the replay leaves the rows, digests and lineage edges the live node
holds (`RetentionTests`). Measured in this repository's sandbox on a log of 3,955 events over 30 days (6 agents in 3
teams, 10 cycles a day of one note retracted and one shared on one topic, the live node sweeping daily): the live node
held 600 previous versions in 6.0 MB of pages; a rebuild held 700 in 6.6 MB when its replay ended (it had deleted
2,582 on the way) and 600 after its first sweep; before this change the same rebuild held all 3,282 in 25.5 MB and
its first sweep deleted none. SQLite reuses the freed pages, so the file stops growing rather than shrinking. What the
retention bounds is the versions of the last `MYCELIC_DERIVED_RETENTION_DAYS` days: a busy topic still keeps, within
that window, versions whose size grows with the topic, so a burst of thousands of notes on one topic in one window
takes room in proportion to the square of the burst until the window has passed. `0` keeps every version forever and the database then grows without bound.
Measured in this repository's sandbox with the topology of `tests/perf/mycelic_volume.py` (96 agents in 32 teams, 2
regions, signing key on), 1,000 org-visible notes on one topic and then 300 cycles of one note retracted and one shared:
the database held 66 MB of pages after the 1,000 notes (106 MB before these changes), the largest `memory.derived`
payload was 1.6 KB (33 KB before), and within the retention window each cycle added 190 KB (328 KB before). With the
sweep run after the notes and after every 100 cycles as if the window had passed, the 1,000 notes took 4.0 MB, each
cycle added 3 KB (the retracted note and its two events, which the log keeps), and a sweep deleted the 4,877 versions
the 1,000 notes had left in 1.9 s, 1,000 versions in 0.3 to 0.4 s.


### 4a. Rollback

A rollback returns to a point where the database, the stream and the signing keys belong together, so take that
point before every upgrade.

**(a) The rollback point.** Stop the stack, snapshot both volumes together, and keep the signing keys
(`MYCELIC_EVENT_SIGNING_KEY` and `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS` in `.env`) and the image with the two archives.
The archives hold every note's text, the agents' key hashes, the audit log and the whole event log, and the keys file
holds every secret, so they go to a directory outside the checkout that only you can read (never into the working tree,
where a `git add` would pick them up). Compose names the volumes after its project, `mycelic`:

```bash
docker compose -f deploy/mycelic/docker-compose.yml stop
ROLLBACK_DIR="$HOME/mycelic-rollback/$(date -u +%Y%m%dT%H%M%SZ)"    # outside the checkout; keep it on a backed-up disk
(umask 077 && mkdir -p "$ROLLBACK_DIR")
docker run --rm -v mycelic_mycelic-data:/v -v "$ROLLBACK_DIR":/b alpine sh -c 'umask 077 && tar czf /b/mycelic-data.tgz -C /v .'
docker run --rm -v mycelic_nats-data:/v -v "$ROLLBACK_DIR":/b alpine sh -c 'umask 077 && tar czf /b/nats-data.tgz -C /v .'
install -m 600 deploy/mycelic/.env "$ROLLBACK_DIR/mycelic-keys.env"    # the keys that signed both
docker tag mycelic:local "mycelic:rollback-$(basename "$ROLLBACK_DIR")"  # the image that wrote them
docker compose -f deploy/mycelic/docker-compose.yml start
```

To return to that point, restore both archives, never one alone, while the stack is down:

```bash
ROLLBACK_DIR="$HOME/mycelic-rollback/<the snapshot to restore>"
docker compose -f deploy/mycelic/docker-compose.yml down        # keeps the volumes (down -v deletes them)
docker run --rm -v mycelic_mycelic-data:/v -v "$ROLLBACK_DIR":/b alpine sh -c 'find /v -mindepth 1 -delete && tar xzf /b/mycelic-data.tgz -C /v'
docker run --rm -v mycelic_nats-data:/v -v "$ROLLBACK_DIR":/b alpine sh -c 'find /v -mindepth 1 -delete && tar xzf /b/nats-data.tgz -C /v'
docker compose -f deploy/mycelic/docker-compose.yml up -d       # with the .env whose keys signed the snapshot
```

The archives come out mode 600, owned by root (the container writes them), and the keys file mode 600, owned by you.
The repository's `.gitignore` also lists the three file names, in case an older copy of these commands left them in a
checkout. Rehearsed in this repository's sandbox on two scratch volumes: the directory and its parent came out mode
700, all three files mode 600, `git status` showed nothing new in the checkout, and the restore commands brought back
both volumes' files after one had been changed.

Rehearsed in this repository's sandbox with the form these commands had in the previous release, which wrote the
archives and the keys file into the current directory (the snapshot and the restore are otherwise the same): 4 agents shared 4 notes (7 active memories with
the enterprise conclusion, 8 lineage edges), the pair was taken, one more note followed (8 active and 3 superseded
memories, 17 edges), `down -v` deleted both volumes, and after the restore and `up -d` `/ready` answered 200 with the
memories by layer and status, the lineage edges and the agents of the snapshot; an old agent key still got the
enterprise conclusion. (The sandbox could not pull images, so `alpine` there was the Alpine-based `nats` image already
present.)

**(b) Back to 0.1.0 (schema 2).** Restore the pair you took before upgrading from 0.1.0, with the keys kept with it
(0.1.0 reads `MYCELIC_EVENT_SIGNING_KEY` only), and start the 0.1.0 image on it: the image you kept (tag it before the
upgrade, `docker tag mycelic:local mycelic:0.1.0`, and set `image: mycelic:0.1.0` in the compose file you start it
with). Rebuilding the 0.1.0 tree is not the same image: 0.1.0 pinned neither its base image nor its dependencies, so a
build today gets this year's releases of both, which 0.1.0 was never tested with. From this release on the tree pins
both (`requirements-lock.txt`, the base image digest in the Dockerfile), so `up -d --build` of a release tree
reproduces its image. Neither this release's database nor its stream can be used instead:

* 0.1.0 refuses to open this release's database: `database schema 7 is newer than this code (2)`.
* 0.1.0 must never consume a stream this release wrote, because it inserts the `memory.derived` events it reads instead
  of deriving them. Applying, in order on a fresh 0.1.0 database, a log written by this release (4 agent
  registrations, a rule, 4 notes, a retraction and the 2 derived events they caused) applied the registrations, the
  rule, the notes and the retraction, and both derived events raised `IntegrityError: UNIQUE constraint failed:
  memories.org_id, memories.operator, memories.scope, memories.agg_key`: both releases key a consolidation by its
  topic, so 0.1.0's own derivation already holds the key. 0.1.0's consumer retries such an event `NATS_MAX_DELIVER`
  (8) times, about 35 s, before it terminates it, and a derived event for a key it has not derived itself would be
  inserted with this release's text.

What is lost is everything since the snapshot. An agent that keeps a `LocalMemory` shares again what it shared since
(`LocalMemory.share` sends the local id as the idempotency key, so each note gets the memory id it had before).
Revoke, or remove with `?retract=1`, any agent revoked or removed since the snapshot, and repeat rule changes and key
rotations made since.

**(c) Back to this release from a later one.** Stop the later release, move its database aside (as in "Signing-key
mistakes" above), keep the stream and the same keys, and start this image: it rebuilds a fresh database from the
stream. It never inserts a derived event; it derives everything from the evidence itself. An event kind it does not
know is applied as `ignored`: counted in `mycelic_events_ignored_total{reason="unknown_kind"}`, marked applied and
never visited again, so its effect is absent from this database, and rolling forward again needs a fresh rebuild by
the later release (move this database aside the same way). Payload fields it does not know are ignored, and fields a
payload leaves out take their defaults, a note's time and event those of the event that carried it, so a rebuild
reproduces every row (`test_unknown_kinds_and_missing_optional_fields_apply_for_rollback`).

**(d) Forward from 0.1.0** is the normal upgrade ("Upgrades" above): take the rollback point first.

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
(the JetStream and smoke modules skip themselves without the binary), and the tests of the pre-pilot collective
layer (`test_collective_*`, which the service does not depend on). The steps of this page that use Docker (the
compose smoke test and demo, `docker compose … config`, the snapshot of section 4a) need a Docker daemon: they are
release checks, and CI runs them in its compose job.

## 6. Kubernetes

`deploy/mycelic/k8s/` is a kustomize base: namespace, NATS StatefulSet + headless Service, Mycelic
StatefulSet (`replicas: 1`, never scale) + Service, ConfigMaps (`nats.conf`, settings, `rules.json`) and an
Ingress with TLS. Probes: startup `/health`, readiness `/ready`, liveness `/health`, all with 5 s timeouts;
a replay (a rebuild may take minutes), or a signature-rejection block (section 4), keeps the pod unready and never
restarts it. Both pods run as non-root with `fsGroup` so their PVCs are writable.

```bash
# 1. image
docker build -f deploy/mycelic/Dockerfile -t ghcr.io/your-org/mycelic:0.2.0 . && docker push ghcr.io/your-org/mycelic:0.2.0
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
| `nats-server: … interface conversion: interface {} is int64` | `NATS_PASSWORD` starts with a digit, so nats-server reads it as a number; regenerate it starting with a letter: `n$(openssl rand -hex 32)` |
| `/health` says `degraded`, `transport_connected: false` | broker unreachable or wrong credentials; writes are queued (`mycelic_outbox_pending`) |
| `/health` says `degraded`, `transport_connected: true`, `checks.publisher.last_error` set | the broker refuses publishes; `stream not found` after a broker state reset recovers by itself within a few seconds (section 4); anything else: read the error and the broker's log. An event the stream can never take is not retried: it is marked failed (audit `event.unpublishable`) and the outbox goes on |
| `/health` says `degraded`, `checks.consumer.last_error` set (`OperationalError: database or disk is full`, say) | the database fails the consumer's applies: free the space or fix the disk; the event is delivered again until it applies, never dropped (section 4, recovery table) |
| 503 `too many walks are running or waiting for this organization; retry in a few seconds` (`Retry-After: 1`) | that organization's verifications, lineage walks or query answers are all taken for longer than 5 s, or everyone's are; the SDK retries; `mycelic_walks_refused_total{kind}` counts them. Other organizations are not held up ("Verifying a conclusion", Limits) |
| 400 `'org_id' must match …` on `POST /admin/rules` or at start (rules file) | a rule's `org_id` must be an organization's enterprise segment, lowercase, as agents were registered with (section 3a) |
| `/health` 503 `failing` with `checks.dead_loops` | a background loop ended (a defect: each loop catches its own errors); the liveness probe restarts the process; report the traceback in the log |
| `mycelic_loop_errors_total` rising, ERROR "the … loop failed" in the log | the database (disk full, I/O error) or the broker failed under a loop; the loop backs off and retries: fix the cause |
| `mycelic_events_failed_total{stage="apply"}` rising | events that fail for a reason of their own were terminated after `NATS_MAX_DELIVER` attempts: `GET /admin/events?org=…&status=failed` lists them with the error; fix the cause, then `POST /admin/replay` applies them again |
| `/ready` 503 after a restart | a replay is running (`replaying_to_seq` in `/admin/status`); wait |
| `/ready` 503 with `"reason": "signature_rejections: rebuild with the corrected keyring required"` | a replay rejected the signature of more than `MYCELIC_REPLAY_MAX_REJECT_RATIO` of the events it consumed (a regenerated or dropped signing key): add the old key to `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`, move the database aside and start (section 4, "Signing-key mistakes"); a replay into the same database does not lift it |
| 507 `organization '…' is at its limit of N active notes` | the organization is at `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG`: retract notes it no longer needs (they count until the retraction applies) or raise the limit (section 4, "Volume cap") |
| `database schema 7 is newer than this code` | an earlier release started on this release's database; restore the snapshot pair you took before upgrading (section 4a) |
| `configuration error: another Mycelic process is using …` | a second instance on the same database, or the previous one is still shutting down; stop it or wait (at most 2 × `MYCELIC_SHUTDOWN_TIMEOUT_SECONDS`). Never `docker compose up --scale mycelic=N` |
| 401 with a key that used to work | key rotated or agent revoked (`GET /admin/agents?all=1`) |
| 403 on `/query` with a `scope` | agents may only query their own team or an ancestor unit |
| 404 on `/memory/{id}` that exists | not visible to this agent (other team's raw note); the API does not reveal existence |
| `event is N bytes; the limit is …` | shrink `metadata` / `payload`; the limit keeps events publishable (`MYCELIC_MAX_EVENT_BYTES` < broker `max_payload`) |
| `stream MYCELIC is bounded … a rebuild from replay may be partial` | the stream was created with limits; remove them or accept partial rebuilds |
