# Security

This page states what Mycelic enforces today, how it is verified, and what it does **not** do. It is
deliberately short on promises. Report vulnerabilities through GitHub security advisories on this
repository.

## 1. Identities

| Principal | Credential | Where it comes from |
|---|---|---|
| Administrator | `MYCELIC_ADMIN_TOKEN` (≥ 32 characters, environment only) | operator; rotating it means restarting the service |
| Agent | API key `mk_<agent_id>.<256-bit secret>` | `POST /admin/agents` / `python -m mycelic register-agent`; shown once |
| Service ↔ broker | NATS user/password (`MYCELIC_NATS_USER/PASSWORD`) | environment; the broker enforces `authorization {}` |
| Service ↔ itself across the log | `MYCELIC_EVENT_SIGNING_KEY` (HMAC-SHA256 over every published event) | environment |

Only the SHA-256 of an agent key is stored (unsalted: the secret is 256 random bits, so a rainbow table is
not a threat, but a weaker secret format would need a KDF). Comparison is constant-time; an unknown agent
id costs the same as a wrong secret. The agent id inside the key is a routing hint and never trusted.
Keys are rotated (`POST /admin/agents/{id}/rotate`) or revoked (`DELETE /admin/agents/{id}`); a revoked
agent's requests are refused with 403 and its id cannot be re-registered (register a new id instead).

An organization is the enterprise root of an agent's path. Everything an agent reads or writes is confined
to its organization; a deployment may host several.

## 2. Authorization (complete list)

| Action | Rule |
|---|---|
| `POST /memory`, `POST /events` | agents only, scope `memory:write` / `events:write`; the memory is written **as the caller** at the caller's path; a body naming another agent is refused; the admin token cannot write memories |
| `GET /memory/{id}`, `POST /query`, `GET /memories` | scope `memory:read`; an agent sees agent-layer memories of **its own team** (or ones the producer marked `visibility: org`) and derived memories of **every unit it belongs to** (the memory's unit is an ancestor-or-self of the agent's path); a query `scope` must be the caller's team or an ancestor unit (403 otherwise); results are filtered by the same rule, so a query never reveals the existence of a memory outside the caller's view, and a `GET` of one returns 404, not 403 |
| `GET /lineage/{id}` | scope `lineage:read` on a readable memory; contributions the caller may not read are **redacted** (text, agent id, event ids, entity withheld; unit path, layer, timestamps, confidence kept) |
| `POST /memory/{id}/retract` | the producing agent or the administrator |
| `/admin/*`, `POST /admin/replay` | administrator token only; the `admin` scope cannot be granted to an agent key |
| `/metrics` | `MYCELIC_METRICS_TOKEN` if set, otherwise an admin token or any valid agent key; unauthenticated only on a loopback bind |
| `/health`, `/ready`, `/` | public, minimal (status, version, transport connected); details need the admin token |
| `/mcp` | same bearer authentication as the REST routes; the MCP identity is the agent whose key is presented, per request |

The visibility rule is written once in SQL (`MycelicStore.visible_rows`) and once in Python
(`auth.memory_visible`), and `tests/mycelic/test_hierarchy_and_auth.py` and `test_api.py` check both.

## 3. Transport and integrity

* Agents never connect to the broker. The broker port is not published by the compose file and has no
  Ingress in Kubernetes.
* Every event the service publishes carries `Mycelic-Signature: v1=<HMAC-SHA256(key, bytes)>`. With
  `MYCELIC_EVENT_SIGNING_KEY` set, the consumer terminates and counts (`mycelic_events_failed_total{stage="signature"}`)
  any message without a valid signature. Additionally a `memory.observed` event is only applied when its
  producer is a registered, active agent whose path equals the event's scope.
* Duplicate deliveries are harmless: `event_id` is the JetStream `Nats-Msg-Id` and the apply step is
  idempotent by event id and memory id.
* TLS: `MYCELIC_TLS_CERT_FILE/KEY_FILE` (direct) or a TLS-terminating proxy with `MYCELIC_ALLOWED_HOSTS`;
  `tls://` + `MYCELIC_NATS_CA_FILE` for the broker (verified, TLS ≥ 1.2). The SDK verifies server
  certificates and accepts a private CA (`MycelicClient(..., ca_file=...)`).

## 4. Input validation and limits

JSON only (415 otherwise); body ≤ `MYCELIC_MAX_BODY_BYTES` (1 MiB, 413); memory text ≤ 4000 characters;
topic/entity/local reference ≤ 200; slot names `[A-Za-z0-9_.:-]{1,100}`; metadata ≤ 4 KiB; event payload
≤ 16 KiB; ≤ 100 events per request; serialized event ≤ `MYCELIC_MAX_EVENT_BYTES` (256 KiB) so an accepted
event is always publishable; paths are `[a-z0-9][a-z0-9_-]{0,63}` segments; timestamps must parse as
ISO-8601; kinds and visibility are enumerated. Rate limiting is a token bucket per peer address before
authentication and per principal after it (a bad token spends the address bucket, never the claimed
agent's); when full, the least recently used tenth of buckets is evicted, never everyone.

## 5. Audit and logs

`audit_log` records agent registration/revocation/rotation, rule changes, replays, every memory ingest and
retraction (principal, action, target, organization, remote address) and rejected events. Unauthenticated
failures are **not** written to the database (they would let anyone grow it); they are counted in
`mycelic_auth_failures_total{reason}` and logged at most once per minute per reason and address. Rows
older than `MYCELIC_AUDIT_RETENTION_DAYS` are pruned at start. Access logging of URLs is off; no secret
is ever logged: `Settings.redacted()` masks every token and password, credentials inside the NATS URL are
refused at start, and placeholder-looking secrets are refused too.

## 6. What is verified

`tests/mycelic/test_api.py`: 401/403/404/413/415/429 behaviour, non-ASCII tokens, admin cannot write,
agents cannot administer, rotate/revoke, metadata that names other teams' agents never leaves through
`/query`, MCP identity. `tests/mycelic/test_datapath.py`: write-as-self, visibility and lineage redaction,
idempotent re-sends. `tests/mycelic/test_jetstream.py`: forged/unsigned events on the real broker are
rejected. `tests/smoke/mycelic_smoke.py`: a sales agent cannot read a logistics raw note (404) while an
administrator sees every contributor.

## 7. Limitations (read these)

1. **The broker is inside the trust boundary.** Whoever holds the NATS credentials (or the signing key)
   can inject events that become organizational memory. Signing plus registry validation makes it
   detectable and hard, not impossible. Keep the broker unreachable from agents and rotate both secrets
   together.
2. **Agent keys are long-lived static bearer secrets** with no expiry and no scoping by IP or time.
   Leaked key ⇒ the attacker writes as that agent until you rotate or revoke it, and whatever it wrote
   keeps feeding team and higher conclusions until retracted (`POST /memory/{id}/retract` re-derives).
3. **One administrator token**, no per-admin identity in the audit log, no SSO/OIDC, no RBAC beyond
   agent scopes.
4. **Consolidation declassifies on purpose.** A team/department/… memory quotes each contributing
   observation (clipped) to everyone in that unit; a producer can also mark a raw note `visibility: org`.
   Redaction in lineage withholds attribution and full text, not topic, slot, timing or counts.
5. **Key hashes travel through the event log** (`agent.registered`, `agent.key_rotated`) so a rebuilt
   database keeps working; treat the `nats-data` volume as sensitive as the database.
6. **No encryption at rest** for SQLite, the JetStream store or agents' local memory files; use encrypted
   volumes.
7. **Rate limiting is in-process**: it resets on restart and, behind a proxy without
   `MYCELIC_TRUST_PROXY_HEADERS`, all clients share one address bucket. It is a brake, not a DDoS defence.
8. **The audit log has no integrity protection**; an attacker with database access can edit it.
9. **MCP over HTTP shares the REST identity model**: anyone holding an agent key can act as that agent
   through MCP; there is no OAuth flow.
10. **No per-agent broker credentials, no multi-tenant broker isolation**: all organizations share one
    stream, separated by subject and by the service's authorization, not by NATS accounts.
11. Single administrator token rotation requires a restart; there is no token versioning.
