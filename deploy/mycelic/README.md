# Mycelic deployment package

One image, one command per role. `python -m mycelic <command>` is the entrypoint everywhere: the image, the
compose files, Fly.io and Render all run `serve`, `worker` or `holder` from the same build.

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build: Node 22 builds `frontend/`, Python 3.11 runs `mycelic` with the built UI at `/app/frontend/dist`. Build from the repository root. |
| `docker-compose.yml` | Distributed topology: NATS JetStream, `api`, `worker`, `holder-a`, `holder-b`. Holders have their own volumes and credentials. |
| `docker-compose.local.yml` | Single process: `api` with in-process worker and embedded holders, one volume, SQLite transport. |
| `up.sh` | `./up.sh` (distributed) or `./up.sh local`; creates `.env` from `.env.example` with generated secrets on first run. |
| `.env.example` | Every configuration variable with its default and a one-line explanation. Secrets are empty. |
| `nats.conf` | NATS server configuration: users `core`, `holder-a`, `holder-b` and their subject permissions (maintained with the transport code). |
| `fly.toml` | Fly.io app: one machine, one volume, `serve` with the worker in-process. |
| `render.yaml` | Render blueprint: one Docker web service with a persistent disk. |

## Start

Without Docker (development, or a single always-on machine):

```bash
python -m mycelic migrate && python -m mycelic seed && python -m mycelic serve
```

With Docker:

```bash
deploy/mycelic/up.sh local     # one container
deploy/mycelic/up.sh           # NATS + api + worker + two holders
```

Both open http://localhost:8780. Setup details: `docs/mycelic/SETUP.md`. Operations (health, metrics,
backups, restore, migrations, rollback, troubleshooting): `docs/mycelic/RUNBOOK.md`.

## Rules of the road

* `coord.db` has one writer node: the API and every worker share one volume (or one machine). Holders are
  the part that scales out, over NATS.
* Back up before you migrate or change the image tag: `python -m mycelic backup --out FILE.tar.gz`.
* Model keys, `MYCELIC_SECRET_KEY`, NATS passwords and holder keys are environment secrets; they are never
  written to the database in clear text, never shown in the UI, and never belong in git.

## Notes from verification (2026-10-08)

- `docker compose exec api python -m mycelic seed` seeds the distributed stack:
  - the API hosts the embedded holders;
  - `holder-a` / `holder-b` receive their documents over NATS when they connect.
- Where Docker Hub rate-limits anonymous pulls, use a mirror:
  - `NATS_IMAGE=mirror.gcr.io/library/nats:2.11-alpine docker compose up -d`
  - build with `--build-arg NODE_IMAGE=mirror.gcr.io/library/node:22-alpine --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.11-slim`
- Behind a TLS-intercepting proxy, add `--network=host --secret id=build_ca,src=<proxy CA bundle>` and the standard
  `--build-arg HTTPS_PROXY`. The CA never lands in an image layer.
- See `docs/mycelic/VERIFICATION.md` for what was verified and how.
