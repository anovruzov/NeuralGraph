# Mycelic runbook

Operating Mycelic: what runs, how to tell it is healthy, how to back it up, restore it, upgrade it and roll it
back, and what to do when something is wrong. Setup is in [SETUP.md](SETUP.md); the deployment files are in
[`deploy/mycelic/`](../../deploy/mycelic/README.md). All commands are `python -m mycelic <command>` (in a
container: `docker compose exec api python -m mycelic <command>`; on Fly: `fly ssh console -C "python -m mycelic <command>"`).

## Processes

| Process | Command | Does | Needs |
|---|---|---|---|
| API | `serve` | HTTP API, SSE fan-out of the event outbox, static UI, `/healthz` `/readyz` `/metrics`; with `MYCELIC_RUN_WORKER_IN_API=1` also the worker; with `MYCELIC_EMBEDDED_HOLDERS=1` also the holders | `coord.db` (read/write), transport |
| Worker | `worker` | Leases jobs from `coord.db` (`loop.tick`, `question.*`, `claim.reverify`, `goal.progress`, `holder.ingest`, `demo.simulate`, `maintenance`), runs the discovery loop, heartbeats into `workers` | the same `coord.db` as the API, transport, model providers |
| Holder | `holder --holder-id ... --key ... --core-url ... --data-dir ...` | Owns one evidence store (`<data-dir>/<holder_id>/evidence.db`; `--data-dir` defaults to `<MYCELIC_DATA_DIR>/holders`), answers routed questions from it under its export policy, heartbeats to the API | transport (NATS when remote), its key |
| NATS | `nats-server -c nats.conf -js` | JetStream stream `MYCELIC`, per-user subject permissions | a volume for the stream |

Single-node shape: one `serve` with worker and holders in-process (SQLite transport). Distributed shape: one
machine with `serve` + `worker` sharing one volume, NATS, holders anywhere. The reason for "one machine" is in
"Scaling limits".

## Health endpoints

| Endpoint | Meaning | Not OK means |
|---|---|---|
| `GET /healthz` -> `{ok, service, version}` (200) | Liveness: the process is up and serving. Used by the Docker HEALTHCHECK, Fly and Render checks. | Restart the process. If it flaps, look at the logs for the crash. |
| `GET /readyz` -> `{ok, db, transport, worker_heartbeat_age_seconds, migrations_pending}` (200, or 503) | Readiness: `db` the coordination DB answers; `transport` the SQLite outbox is usable / NATS is connected and the stream exists; `worker_heartbeat_age_seconds` seconds since any worker heartbeat; `migrations_pending` number of migrations not yet applied. | `db false`: volume missing, permissions, disk full. `transport false`: NATS down or credentials wrong. `migrations_pending > 0`: run `migrate`. Heartbeat age over 3x `MYCELIC_WORKER_HEARTBEAT_SECONDS` (30 s): the worker is dead or stuck, see "Worker crash recovery". |
| `python -m mycelic health` | The same readiness view from a shell, exit code 0 when ready. Use it from cron, systemd `ExecStartPre`, or as a container healthcheck for `worker`/`holder` containers, which have no HTTP port. | As above. |

Both HTTP endpoints are unauthenticated and cheap; point the load balancer at `/healthz` and alerting at
`/readyz`. Admin > Deployment in the UI (`GET /api/admin/overview`) shows the same facts plus workers, jobs,
usage and settings (never keys).

## Metrics

`GET /metrics` (no auth, Prometheus text format, `Content-Type: text/plain; version=0.0.4`). Counters
restart from zero when the process restarts; use `rate()`/`increase()`. Gauges that come from the database
are computed at scrape time.

| Metric | Type | Labels | Meaning | Alert suggestion |
|---|---|---|---|---|
| `mycelic_model_calls_total` | counter | `provider`, `model`, `tier`, `ok` | Model provider calls | `rate(...{ok="false"}[10m]) > 0.2 * rate(...[10m])`: provider degraded |
| `mycelic_model_cost_usd_total` | counter | `provider`, `model`, `tier` | Estimated spend from the price table | `increase(...[1d]) > <daily budget>` |
| `mycelic_jobs_backlog` | gauge | | Jobs `queued` + `leased` | growing for 15 min with a live worker: add worker concurrency; with a dead worker: see below |
| `mycelic_jobs_dead` | gauge | | Jobs that exhausted `max_attempts` (the failed-job queue) | `> 0` for 10 min |
| `mycelic_questions_total` | counter | `outcome` | Questions resolved by outcome (`committed`, `retained_uncertain`, `expired`, `failed`, `cancelled`) | `expired` or `failed` share rising: holders offline or model errors |
| `mycelic_routes_failed_total` | counter | | Question routes that ended `failed` or `timeout` | `increase(...[15m]) > 0`: check holder status |
| `mycelic_claims` | gauge | `status` | Claims by commit-gate status (`hypothesis`, `supported`, `contested`, `stale`, `retracted`) | `contested` rising: open conflicts need a lead; `stale` rising: evidence not being refreshed |
| `mycelic_evidence_stale` | gauge | | Evidence refs older than the tenant freshness policy | ratio to total over 50 % |
| `mycelic_worker_heartbeat_age_seconds` | gauge | | Seconds since the newest worker heartbeat | `> 30` for 2 min (3x the 10 s heartbeat): worker down |
| `mycelic_http_requests_total` | counter | `method`, `route`, `status` | Requests served | 5xx rate `> 1 %` over 5 min |
| `mycelic_http_request_seconds` | histogram | `method`, `route` | Request latency (`_bucket`, `_sum`, `_count`) | p95 `> 2 s` for 10 min |

The registry is `mycelic.observability.metrics`; the names above are the contract in [API.md](API.md).
`/readyz` failing and `mycelic_worker_heartbeat_age_seconds` are the two alerts to have on day one.

## Logs

One JSON object per line on stderr (`MYCELIC_LOG_JSON=1`, the default; `0` gives plain text). Fields:

| Field | Always | Content |
|---|---|---|
| `ts` | yes | UTC, `2026-09-28T12:00:00.123Z` |
| `level` | yes | `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL` (`MYCELIC_LOG_LEVEL` sets the floor) |
| `logger` | yes | Python logger, e.g. `mycelic.discovery.loop`, `mycelic.api` |
| `msg` | yes | Rendered message |
| `service` | yes | `api` `worker` `holder` (`MYCELIC_SERVICE`; compose sets it per container) |
| `request_id` | when bound | The `X-Request-Id` of the HTTP request being handled; the same value is in `audit_log.request_id` and `error_reports.request_id` |
| `exc` | on exceptions | Traceback text |
| others | when given | Any `extra=` the emitting code attached: typically `tenant_id`, `goal_id`, `question_id`, `job_id`, `holder_id`, `worker_id`, `route`, `status`, `duration_ms` |

Ship stderr as-is (Docker: `docker compose logs -f api`; Fly: `fly logs`; Render: the dashboard). To trace one
request: grep its `request_id` across `api` and `worker` lines. Errors that matter to an operator are also
written to `error_reports` (Admin > Deployment) and POSTed as JSON to `MYCELIC_ERROR_WEBHOOK` when set
(5 s timeout, fire-and-forget: a dead webhook never blocks the application).

## Backup

```bash
python -m mycelic backup --out /backups/mycelic-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
```

Runs while the API and worker are up: each database is copied with SQLite's online backup API, which yields a
consistent snapshot of a WAL database under concurrent writes (a plain `cp` of a live WAL database does not).

The bundle is a `tar.gz`:

```
manifest.json                      format "mycelic-backup" v1; created_at; mycelic_version; sqlite_version; hostname;
                                   schema {current, applied[]}; files[] {path, kind coord|holder, holder_id, sha256, bytes, pages}
coord.db                           the coordination database (single self-contained file, no -wal)
holders/<holder_id>/evidence.db    every embedded holder store under MYCELIC_HOLDERS_DIR
```

Verify a bundle without restoring: `tar -xzOf FILE.tar.gz manifest.json | python -m json.tool`, then compare a
`sha256sum` of an extracted member with the manifest.

What is not in the bundle: standalone holders' stores (they live on other machines: run the same `backup` there
with `MYCELIC_DATA_DIR` set to the holder's `--data-dir`, see the note below), the NATS stream (a transport,
not memory; it is rebuilt from `coord.db` state), `MYCELIC_SECRET_KEY` and other environment secrets (keep
them in your secret store), and the built frontend (rebuilt from git).

Standalone holder machines: the bundle format is the same, but a holder machine has no `coord.db`. Run the
holder with `--data-dir <MYCELIC_DATA_DIR>/holders` (the default when `--data-dir` is omitted, and what the
compose file does) so its store sits at `<MYCELIC_DATA_DIR>/holders/<holder_id>/evidence.db`, then
`python -m mycelic backup --out FILE --holders-only` on that machine (the flag maps to
`backup_bundle(settings, FILE, include_coord=False)`; the Python one-liner works where the flag is absent).

Schedule: hourly during working hours and daily retained for 30 days is a reasonable default for one
organization; the bundle is small (the coordination DB is metadata; holder stores hold the text). Cron on the
host, or a compose sidecar:

```cron
17 * * * *  docker compose -f /srv/mycelic/deploy/mycelic/docker-compose.yml exec -T api python -m mycelic backup --out /data/backups/hourly-$(date +\%H).tar.gz
5  2 * * *  docker compose -f /srv/mycelic/deploy/mycelic/docker-compose.yml exec -T api python -m mycelic backup --out /data/backups/daily-$(date +\%F).tar.gz && rclone copy /data/backups remote:mycelic-backups
```

Copy bundles off the machine; a backup on the same volume as the data protects against bad migrations and
operator mistakes, not against losing the disk. Always take one before `migrate` and before changing the image
tag.

## Restore

1. Stop every process that has the data directory open: `docker compose stop api worker` (holders too if the
   bundle contains their stores; on Fly `fly machine stop`). A process still holding the old file would keep
   writing to a file that is no longer `coord.db`.
2. Put the bundle where the process can read it (`docker compose cp FILE.tar.gz api:/data/restore.tar.gz` works
   on a stopped container's volume through a one-off container: `docker compose run --rm --no-deps api restore --from /data/restore.tar.gz`).
3. Restore. Into an empty data directory:
   ```bash
   python -m mycelic restore --from FILE.tar.gz
   ```
   Over existing databases (`--force`): the existing files are renamed to `coord.db.pre-restore.<timestamp>`
   (and the same for each `evidence.db`), never deleted, and stale `-wal`/`-shm` sidecars are removed so the
   restored file is not overwritten by an old write-ahead log:
   ```bash
   python -m mycelic restore --from FILE.tar.gz --force
   ```
   Every file's sha256 is checked against the manifest and `PRAGMA quick_check` is run before anything is
   moved into place; a tampered or truncated bundle fails before touching the data directory.
4. `python -m mycelic migrate --status`. If the bundle's schema is older than the code, `migrate` brings it
   forward. If it is newer (restoring a backup taken by a newer version into an older image), do not start:
   deploy the version that wrote the bundle first (the manifest's `mycelic_version` and `schema.current`).
5. Start the processes, then check `/readyz`, Admin > Deployment, and that holders reconnect (`holder.status`
   events, `last_heartbeat_at`). Questions that were in flight at backup time resume from their last
   checkpoint (`question_runs`) or expire; the worker requeues their jobs.
6. Delete the `.pre-restore.*` files once satisfied.

Restoring to a new machine also needs the environment: `MYCELIC_SECRET_KEY` (if it was generated rather than
set, it lives in the old data directory; set it explicitly in production so this cannot bite), model keys,
NATS passwords, holder keys.

## Migrations

Schema changes are numbered SQL files in `mycelic/db/migrations/NNNN_name.sql`, applied in order, each in its
own transaction, recorded in `schema_migrations`. Every process applies pending migrations when it opens
`coord.db` (`CoordDB(..., migrate=True)` is the default), so the first process started from a new image
migrates; `migrate` does it explicitly and lets you look first:

```bash
python -m mycelic backup --out /data/backups/pre-migrate.tar.gz
python -m mycelic migrate --status      # applied / pending / current / latest
python -m mycelic migrate               # applies everything pending
```

Policy: forward-only. A shipped migration file is never edited; a mistake gets a new migration that corrects
it. Migrations are additive whenever possible (new tables, new nullable columns, new indexes), which is what
makes the rollback rule below work. There is no `down` migration; the way back is the backup.

## Rollback

Image tag rollback: compose `MYCELIC_IMAGE=mycelic:<previous tag>` and `docker compose up -d`; Fly
`fly releases` then `fly deploy --image <previous image>`; Render "Rollback" on the deploy in the dashboard.

The rule for data: an older image can open a newer `coord.db` only if the newer migrations were additive
(no table or column the old code needs was removed or renamed, no data was rewritten in a way the old code
misreads). Additive migrations are the norm, so a rollback of one release usually needs no data step. The
older code will show `migrations_pending = 0` (it only knows its own files) and ignore the extra tables.

When the release notes say a migration is not backward compatible, or anything looks wrong after rolling
back, restore the pre-migrate backup (previous section) and lose only what happened after it. This is why
"back up before migrate" is not optional: it is the only rollback for the data.

## Rotating holder keys

The holder key authenticates holder to core (heartbeats, ingestion results). It is stored hashed; rotation
replaces the hash, so the old key stops working at once.

1. Owner or admin: UI holder page > Rotate key, or `POST /api/holders/{holder_id}/rotate-key` -> `{key}`
   (shown once; the action is audited as `holder.rotate_key`).
2. Update the holder process: the `--key` argument / `MYCELIC_HOLDER_KEY` (compose: `MYCELIC_HOLDER_KEY_A` in
   `.env`), restart it. Until then the holder's heartbeats fail (401) and its status goes `offline`; queued
   questions wait for it up to the question timeout.
3. NATS credentials are separate: change the password in `nats.conf` (the `$NATS_HOLDER_A_PASSWORD` variable)
   and in the holder's `MYCELIC_NATS_PASSWORD`, reload NATS (`nats-server --signal reload`).

`MYCELIC_SECRET_KEY` signs the envelopes the core sends to holders. Rotate it only with all holders stopped,
then restart everything; envelopes signed before the rotation are rejected by holders after it.

## Revoking access

| Revoke | How | Effect |
|---|---|---|
| A membership | Admin > Members, or `DELETE /api/org/memberships {user_id, unit_id, role?}` | Scope shrinks immediately (the authorizer is consulted on every call); routes pending to that user's holders for the units they lost are marked `revoked`; audited |
| A grant | `DELETE /api/grants/{grant_id}` (grantor or admin) | The grantee no longer sees the resource on the next request |
| A holder | `PATCH /api/holders/{holder_id} {"status": "revoked"}` | Its key is refused, pending/delivered routes become `revoked`, it is skipped for routing. Its existing evidence references stay (lineage), `raw` reads through it fail |
| A user | `PATCH /api/org/users/{user_id} {"status": "disabled"}` | Login refused, existing sessions stop authorizing on the next request |
| A NATS user | remove it from `nats.conf`, reload | The process can no longer publish or subscribe |

## Worker crash recovery

The queue is `coord.db` (DECISIONS D5). A worker leases a job for `MYCELIC_WORKER_LEASE_SECONDS` (120) and
extends it with heartbeats; when a worker dies mid-job the lease expires and the `maintenance` sweep
(`requeue_expired`) puts the job back to `queued` with `last_error = "lease expired"`. A zombie that comes
back cannot complete or fail a job another worker now holds (fenced on `worker_id`, outcome `lost_lease` in
`job_attempts`). Question steps checkpoint into `question_runs` and every committed effect is keyed by
`(question_id, step)`, so re-running a step is a no-op: no duplicate claims, no double routing.

What you see: `mycelic_worker_heartbeat_age_seconds` climbing, loops showing "not active" in the UI (the
indicator requires a heartbeat within 3x `MYCELIC_WORKER_HEARTBEAT_SECONDS`), `/readyz` reporting a large
heartbeat age. What to do: restart the worker (`docker compose restart worker`; the API with
`MYCELIC_RUN_WORKER_IN_API=1`). Nothing else: recovery is automatic within one lease period. If the crash
repeats on the same job, it is in "Failed jobs" after `max_attempts`.

## Failed jobs

A job is retried with exponential backoff (5 s doubling to a 600 s cap, with jitter) up to `max_attempts`
(5), then marked `dead`. `mycelic_jobs_dead` counts them; Admin > Workers lists them with `last_error` and every
attempt (`GET /api/admin/jobs?status=dead`, `job_attempts`).

1. Read `last_error`. Model provider errors (401, 429, timeouts) and holder timeouts are the usual causes;
   fix the cause first (key, quota, holder online) or the retry dies again.
2. Retry one: `POST /api/admin/jobs/{id}/retry`; retry all: `POST /api/admin/jobs/retry-dead`. Attempts reset
   to 0.
3. A job that is wrong rather than unlucky (a question for a deleted goal) can be left dead; `maintenance`
   prunes `done`/`cancelled` jobs after 7 days but keeps `dead` ones until retried or cancelled by hand.

## Budget exhaustion

Every goal has a budget (`tokens`, `usd`, `questions`, `followup_depth`) and `budget_spent`; questions carry
their own. When a loop cannot reserve budget for the next question its state becomes `budget_exhausted`
with an explanation, it stops calling models, and the goal page shows it. API calls that would exceed a
budget return 429.

To continue: raise the budget (`PATCH /api/goals/{goal_id} {"budget": {...}}` or the goal page) and resume
the loop (`POST /api/goals/{goal_id}/loop {"action": "resume"}`). To see where the money went: Admin > Usage
(`GET /api/admin/usage?since=`), `model_usage` by goal and tier, `mycelic_model_cost_usd_total`. Tenant policy
`default_goal_budget` sets what new goals get.

## NATS retention

The stream `MYCELIC` keeps messages for `MYCELIC_TRANSPORT_RETENTION_SECONDS` (7 days) and de-duplicates on
`Nats-Msg-Id` inside that window. Consumers are durable pull consumers named per process, so a holder that
was offline for less than the retention window receives everything it missed on reconnect; one offline for
longer misses routes, which then `timeout` on the core side and are re-asked by the loop when still useful.
The transport is never the source of truth: everything a consumer does is committed to `coord.db` or the
holder store before the message is acknowledged.

Operations: `nats stream info MYCELIC` (messages, bytes, consumers), `nats consumer report MYCELIC` (pending
and redelivered counts; a consumer with steadily growing `unprocessed` is a holder that is down). The store
directory (`-sd`, the `nats-data` volume) needs disk for one retention window of traffic, small for this
workload. Losing the NATS volume loses nothing durable: restart the core and holders and they recreate the
stream and consumers.

## Scaling limits

* `coord.db` is SQLite in WAL mode with one writer at a time (`busy_timeout` serialises writers; every
  consumer is idempotent so a `SQLITE_BUSY` retry is safe). Every process that writes it (API, workers,
  embedded holders) must run on the machine that holds the volume. Multiple `worker` processes on that
  machine are fine (`MYCELIC_WORKER_CONCURRENCY` per process, leases across processes); a worker on another
  machine is not.
* Read scaling is not a problem at organizational scale (thousands of users, tens of thousands of claims):
  the API is async, and the heavy work (model calls, retrieval) is bounded by `MYCELIC_MODEL_MAX_PARALLEL`
  and by holders, not by the database.
* Holders scale horizontally with NATS: each is its own process and store on any machine.
* When one writer node is not enough, the path is a Postgres coordination backend (all SQL is in
  `mycelic/db/`, DECISIONS D2), not a second SQLite writer. Do not put `coord.db` on a network filesystem.
* The SSE tailer serves every browser from one process; thousands of concurrent viewers are fine, tens of
  thousands need a second API process reading the same `coord.db` (reads scale; only writes are single-node).

## Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| `/readyz` 503 with `db: false` | volume not mounted, wrong `MYCELIC_DATA_DIR`, disk full, file owned by another user | `ls -la $MYCELIC_DATA_DIR`, `df`, logs at startup |
| `/readyz` 503 with `migrations_pending > 0` | new image, migrations not applied | `migrate --status`, back up, `migrate` |
| `/readyz` 503 with `transport: false` | NATS down, wrong URL or credentials, stream missing | `nats server check`, `MYCELIC_NATS_URL/USER/PASSWORD`, NATS logs for `Authorization Violation` |
| Loops never become Active, backlog grows | no worker: `MYCELIC_RUN_WORKER_IN_API=0` and no `worker` process; or the worker crashed | `mycelic_worker_heartbeat_age_seconds`, Admin > Workers, `docker compose ps` |
| Questions all `timeout` | holders offline or on the wrong transport; holder key revoked; NATS permissions | holder `status`/`last_heartbeat_at` on Admin > Integrations, holder logs, `nats consumer report` |
| Holder logs `signature invalid` | `MYCELIC_SECRET_KEY` changed on the core, or the holder talks to a different core | set the key back or restart holders after a deliberate rotation |
| Holder heartbeats 401 | key rotated or holder revoked | "Rotating holder keys" |
| Model calls fail 401/403 | missing or wrong provider key, wrong base URL | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `OPENAI_BASE_URL` ends in `/v1`, Admin > Models shows `anthropic_configured` |
| Model calls fail with schema errors | a local model too small for JSON tasks | use a larger model for `standard`/`heavy`, keep `light` local |
| Loop state `budget_exhausted` | goal budget spent | "Budget exhaustion" |
| Claims stuck `hypothesis` | not enough independent source roots (copies of one document count once; unknown roots never count) | `GET /api/claims/{id}` `dependencies.roots`; add holders with independent sources; tenant policy `min_independent_roots` |
| Many `stale` claims | freshness policy shorter than the evidence refresh cadence | tenant policy `freshness_days`; revise documents |
| Browser: 403 on every POST | CSRF: `Origin` not same-origin and not in `MYCELIC_CORS_ORIGINS`; `MYCELIC_ALLOWED_HOSTS` does not include the host | set `MYCELIC_PUBLIC_URL`, `MYCELIC_ALLOWED_HOSTS`, `MYCELIC_CORS_ORIGINS` |
| Browser: logged out constantly behind a proxy | `MYCELIC_SECURE_COOKIES=1` without HTTPS, or the proxy drops cookies | terminate TLS, forward `Host`, or set `MYCELIC_SECURE_COOKIES=0` for plain HTTP on a LAN |
| SSE updates stop, page polls | proxy buffering `/api/events/stream` | disable buffering for that path (nginx `proxy_buffering off`), keep-alive over 15 s (the server pings every 15 s) |
| `/` says the UI is not built | `frontend/dist` missing (running from source) | `cd frontend && npm ci && npm run build`, or use the image |
| `database is locked` in logs | a long transaction (bulk seed) or two processes on a network filesystem | keep `coord.db` on local disk; retries are automatic within `busy_timeout` (10 s) |
| Restore refuses: "refusing to overwrite" | databases already exist | stop processes, `restore --force` (old files kept as `.pre-restore.*`) |
| Restore fails: "checksum mismatch" | bundle corrupted or altered in transit | fetch the bundle again; nothing was changed |
| Backup fails: "coordination database not found" | `MYCELIC_DATA_DIR` differs from the running service's | run it in the same environment (`docker compose exec api ...`) |
| Container unhealthy right after start | `start-period` too short on a slow disk while migrations run | wait, then `docker inspect --format '{{json .State.Health}}' <container>` |
