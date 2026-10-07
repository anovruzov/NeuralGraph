# Security

This page states what Mycelic enforces today, how it is verified, and what it does **not** do. It is
deliberately short on promises.

Report vulnerabilities through GitHub private vulnerability reporting on this repository (Security →
Report a vulnerability). That channel is **not enabled yet**: the repository owner must enable it before
launch. Until it is, never put vulnerability details in a public issue.

## 1. Identities

| Principal | Credential | Where it comes from |
|---|---|---|
| Administrator | `MYCELIC_ADMIN_TOKEN` (≥ 32 characters, environment only) | operator; rotating it means restarting the service |
| Agent | API key `mk_<agent_id>.<256-bit secret>` | `POST /admin/agents` / `python -m mycelic register-agent`; shown once |
| Service ↔ broker | NATS user/password (`MYCELIC_NATS_USER/PASSWORD`) | environment; the broker enforces `authorization {}` |
| Service ↔ itself across the log | `MYCELIC_EVENT_SIGNING_KEY` (HMAC-SHA256 over every published event, and through a derived subkey over every memory row); keys from before a rotation, listed in `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`, keep verifying what they signed | environment |

Only the SHA-256 of an agent key is stored (unsalted: the secret is 256 random bits, so a rainbow table is
not a threat, but a weaker secret format would need a KDF). The comparison of the secret's hash is
constant-time, but an unknown agent id is not answered in the same time as a known id with a wrong secret:
the unknown id is answered faster, because a known id first loads the agent's record (a 24–31 µs gap was
measured over loopback HTTP). Agent ids are identifiers, not secrets, and can be enumerated this way; only
the 256-bit secret authenticates. The agent id inside the key is a routing hint and never trusted.
Keys are rotated (`POST /admin/agents/{id}/rotate`) or revoked (`DELETE /admin/agents/{id}`); a revoked
agent's requests are refused with 403 and its id cannot be re-registered (register a new id instead).

An organization is the enterprise root of an agent's path. Everything an agent reads or writes is confined
to its organization; a deployment may host several.

## 2. Authorization (complete list)

| Action | Rule |
|---|---|
| `POST /memory`, `POST /events` | agents only, scope `memory:write` / `events:write`; the memory is written **as the caller** at the caller's path; a body naming another agent is refused; the admin token cannot write memories |
| `GET /memory/{id}`, `POST /query`, `GET /memories` | scope `memory:read`; an agent sees agent-layer memories of **its own team** (or ones the producer marked `visibility: org`) and derived memories of **every unit it belongs to** (the memory's unit is an ancestor-or-self of the agent's path); a query `scope` must be the caller's team or an ancestor unit (403 otherwise); results are filtered by the same rule, so a query never reveals the existence of a memory outside the caller's view, and a `GET` of one returns 404, not 403. Text of a memory that is not active (superseded or retracted) is returned only to its producer and to administrators; everyone else who may read the memory gets an empty `text` and `text_withheld` set to its status. The memory keeps every other field (its `metadata.statements` and `statement_origins` are dropped), and `GET /memories?status=superseded` or `?status=retracted` lists such memories the same way; `POST /query` searches active memories only |
| `GET /lineage/{id}` | scope `lineage:read` on a readable memory (`POST /query` embeds the lineage graph only for callers holding it); contributions the caller may not read are **redacted** (text, agent id, event ids, entity and the producer's local reference withheld; unit path, layer, timestamps, confidence kept). A readable node that is superseded or retracted, and that the caller did not produce, is **withheld**: it keeps its shape and every other field, with an empty `text` and `text_withheld` set to its status; the nodes, edges and counts are those of any other reader. A producer's local reference is shown only to that producer and to administrators |
| `GET /verify/{id}`, `POST /query` with `"verify": true` | scope `lineage:read` on a readable memory: an id that does not exist and one the caller may not read get the same 404, and a caller without the scope gets 403 whatever the id (on `POST /query` the summary is silently left out, like the lineage graph). The verdict and both answers (`derived_correctly`, `still_true`) are computed from the codes before redaction, so every viewer gets the same answer. A node the caller may not read shows only its id, layer, unit (a raw note's team, not its path, which ends in the producer's id), operator, status and `ok`; of its codes it keeps only those lineage already discloses (`node_retracted`, `node_superseded`, `missing_parent`, `cycle_detected`), every other one becomes `hidden_error`, `hidden_unverifiable` or `hidden_stale`, it carries no details, and its warnings are only counted. No detail on any node, for any viewer, carries text, statements, metadata values, agent or producer ids or row digests; key ids, `as_of` and the `log_lag` warning are shown to administrators only. `mycelic_verification_reasons_total` counts each verification's codes as its caller saw them (a hidden node's as `hidden_*`, its warnings not at all), so diffing `/metrics` around its own call tells an agent nothing its report withheld; the totals also count administrators' verifications, which see every code, so set `MYCELIC_METRICS_TOKEN` where even those totals must not reach agents (without it `/metrics` accepts any agent key) |
| `POST /memory/{id}/retract` | the producing agent or the administrator, raw observations only: a derived memory is a function of its evidence and is withdrawn (retracted, not deleted) when that evidence is retracted. Retraction withdraws a note from answers but does not erase it. Its text stays in the database, the event log and the stream, readable by its producer and administrators |
| `/admin/*`, `POST /admin/replay`, `POST /admin/reaggregate` | administrator token only; the `admin` scope cannot be granted to an agent key |
| `/metrics` | `MYCELIC_METRICS_TOKEN` if set, otherwise an admin token or any valid agent key; unauthenticated only on a loopback bind |
| `/health`, `/ready`, `/` | public, minimal (status, version, transport connected); details need the admin token |
| `/mcp` | same bearer authentication as the REST routes; the MCP identity is the agent whose key is presented, per request |

The visibility rule is written once in SQL (`MycelicStore.visible_rows`, behind `GET /memories`) and once in
Python (`auth.memory_visible`, behind every other read); `tests/mycelic/test_hierarchy_and_auth.py` checks
the Python rule and `test_api.py` checks both through the API. Retrieval computes its BM25 statistics over
the caller's view only, so memories outside it influence neither the results nor their order.

## 3. Transport and integrity

* Agents never connect to the broker. The broker port is not published by the compose file and has no
  Ingress in Kubernetes.
* Every event the service publishes carries `Mycelic-Signature: v1=<HMAC-SHA256(key, bytes)>`. With
  `MYCELIC_EVENT_SIGNING_KEY` set, the consumer terminates and counts (`mycelic_events_failed_total{stage="signature"}`)
  any message without a valid signature. Additionally a `memory.observed` event is only applied when its
  producer is a registered agent of the event's organization whose path equals the event's scope
  (`MycelicService.apply_event`). Revocation is enforced at authentication (a revoked key gets 403), not at
  apply time: an event the agent published before revocation is still applied, and its memories stay active
  until retracted (see §7, item 2). Inside the broker boundary an injected event may name any registered
  agent id, including a revoked one.
* The consumer never inserts a derived memory from the stream: a `memory.derived` event is informational (a
  duplicate when this node derived the same memory, otherwise ignored, audited and counted in
  `mycelic_events_ignored_total`), so an injected event cannot plant a conclusion, a lineage edge or a supersession.
* Duplicate deliveries are harmless: `event_id` is the JetStream `Nats-Msg-Id` and the apply step is
  idempotent by event id and memory id.
* **Memory integrity.** Every memory row carries a digest of its content (text, labels, kind, confidence, support,
  independent teams, visibility, producer, operator and rule; for a raw note also its observation time, event,
  local reference, cited events and metadata), its parents (the ids its lineage edges name) and its derivation
  metadata (roots, statements, children, slots, evidence and the other keys listed in `mycelic/integrity.py`). The
  statement that inserts the row writes the digest, and a rebuild from the stream under the same key reproduces it
  byte for byte. With `MYCELIC_EVENT_SIGNING_KEY` set the digest is an HMAC under a subkey of that key, so an edit by
  anyone who does not hold the key is detected, including one that rewrites the key id, recomputes an unkeyed hash
  or relabels the origin; without a key it is a plain SHA-256 that detects corruption only, and once a key is set
  such a row reads `downgraded`. Not covered: a memory's status, supersession and applied columns (`status`,
  `superseded_by`, `applied_at`, `apply_seq` and the `status_reason`/`reactivated_at` metadata), a derived memory's
  `created_at` and `event_id` and its metadata outside the derivation keys (`version_of`, `fragility`, candidate
  counts), the `events`, `agents`, `rules`, `applied_rules`, `audit_log` and `meta` tables, and lineage edges'
  `contributed_by` and `parent_layer`. Rows that existed before schema 4 are signed at the first start after the upgrade by the
  start-up backfill, with origin `backfill` and a subkey of their own: they prove integrity from the backfill
  onward only. The backfill never signs a row after the first row that still carries a digest signed at insert
  (origin other than `backfill`): a later row without a digest had it removed, so it stays without one and is
  logged as an error and audited as `refused`. That bound is read from the database, so whoever can write to it
  can move it: deleting the completion flag (`meta.integrity_backfill_complete`) and removing the digests of every
  row signed at insert up to and including a row gets that row signed again at the next start, as `backfill`, with
  whatever it was edited to, and it then reads `ok` (a row from before the upgrade needs only its own digest
  removed). What such an edit cannot avoid is the backfill itself: every backfill that signs rows is logged at
  WARNING, one that refuses rows at ERROR, and either is audited (`integrity.backfill`, with the rows signed, the
  rows refused and the key id) and counted (`mycelic_integrity_backfilled_total`, signed rows). The only legitimate
  backfill that signs rows follows an upgrade from schema 3 or earlier: it runs at the first start after the upgrade
  (an interrupted one resumes at the next start and is audited once, when it completes) and never refuses rows. So
  in a database created at schema 4 (it is never backfilled: its first start finds nothing to sign and writes no
  audit row), any backfill WARNING or ERROR, any `integrity.backfill` audit row and any rise of
  `mycelic_integrity_backfilled_total` means digests were removed from the database; in an upgraded database, any
  after the start that completed the upgrade's backfill does. Audit rows are pruned after
  `MYCELIC_AUDIT_RETENTION_DAYS` and can be edited by the same person, so alert on the log and the metric. Downward
  verification (`GET /verify/{id}`, §2 and §6) runs this check on every row it walks and reports the outcome as a
  reason code (`integrity_mismatch`, `integrity_downgraded`, `integrity_unknown_key`, …), never the digest.
* TLS: `MYCELIC_TLS_CERT_FILE/KEY_FILE` (direct) or a TLS-terminating proxy with `MYCELIC_ALLOWED_HOSTS`;
  `tls://` + `MYCELIC_NATS_CA_FILE` for the broker (verified, TLS ≥ 1.2). The SDK verifies server
  certificates and accepts a private CA (`MycelicClient(..., ca_file=...)`).

## 4. Input validation and limits

JSON only (415 otherwise); body ≤ `MYCELIC_MAX_BODY_BYTES` (1 MiB, 413); memory text ≤ 4000 characters;
topic/entity/local reference ≤ 200; slot names `[A-Za-z0-9_.:-]{1,100}`; metadata ≤ 4 KiB; event payload
≤ 16 KiB; ≤ 100 events per request; serialized event ≤ `MYCELIC_MAX_EVENT_BYTES` (256 KiB) so an accepted
event is always publishable; paths are `[a-z0-9][a-z0-9_-]{0,63}` segments; timestamps must parse as
ISO-8601; kinds and visibility are enumerated; metadata keys the aggregator owns (`agg_key`,
`promoted_from`, `version_of`, `contributing_agents`, `statements`, …) are stripped from agent input; cited
`source_event_ids` must be events of the caller's own organization (unknown and foreign ids get the same
error). Rate limiting is a token bucket per peer address before authentication and per principal after it (a
bad token spends the address bucket, never the claimed agent's); a JSON-RPC batch on `/mcp` is capped at
`MYCELIC_MAX_BATCH` messages and charged one token per message; when full, the least recently used tenth of
buckets is evicted, never everyone. Behind a proxy (`MYCELIC_TRUST_PROXY_HEADERS=true`) the client address
is the Nth entry from the right of `X-Forwarded-For` (`MYCELIC_TRUSTED_PROXY_HOPS`), never the leftmost one
the client can forge.

Downward verification is priced by its walk: 1 + ceil(nodes / 250) tokens from the caller's principal bucket, the
walk's share charged after the walk, which may take the bucket into debt (never deeper than one burst, so it is
repaid within (burst + 1) / rps seconds). A principal in debt is refused with 429 before anything is read. Inside a
JSON-RPC batch on `/mcp`, whose messages run back to back without the middleware between them, every message is
charged its token up front and every `mycelic_verify` message its walk, so once the bucket is in debt the remaining
verify messages get an error result without walking, and the next request is 429. The debt check and the charge
both run under the store lock, so two concurrent verifications by one principal cannot both walk on one token. A
walk reads at most `MYCELIC_VERIFY_MAX_NODES` (25,000) nodes and holds the event loop while it runs; the charge
is a brake proportional to the walk, not a CPU cap (DEPLOYMENT.md §4, "Verifying a conclusion"). `max_leaf_age` must
be an integer from 1 to 315,360,000 and a memory id must match `[A-Za-z0-9_.:-]{1,200}` (400 otherwise, also for an
id sent with `%2F` in it, which aiohttp decodes before the check).

## 5. Audit and logs

`audit_log` records agent registration/revocation/rotation, rule changes, replays, every memory ingest and
retraction (principal, action, target, organization, remote address) and rejected events. Every successful
downward verification writes `memory.verify` (principal, target, organization, verdict, nodes walked and the reason
codes before redaction, so an administrator sees why a hidden node failed; remote address); a refused one (403,
400, 404, 429) writes nothing. Unauthenticated
failures are **not** written to the database (they would let anyone grow it); they are counted in
`mycelic_auth_failures_total{reason}` and logged at most once per minute per reason and address. Rows
older than `MYCELIC_AUDIT_RETENTION_DAYS` are pruned at start. Access logging of URLs is off; no secret
is ever logged: `Settings.redacted()` masks every token, password and key (previous signing keys as their
number), signing keys appear in logs, audit rows and `meta` only as key ids (a truncated hash), credentials
inside the NATS URL are refused at start, and placeholder-looking secrets are refused too.

## 6. What is verified

`tests/mycelic/test_api.py`: 401/403/404/413/415/421/429 behaviour, non-ASCII tokens, admin cannot write,
agents cannot administer, rotate/revoke, per-scope refusals, metadata that names other teams' agents never
leaves through `/query`, the SQL visibility rule behind `GET /memories`, `X-Forwarded-For` handling, MCP
identity, MCP batch limits and per-organization MCP status. `tests/mycelic/test_datapath.py`: write-as-self, visibility and lineage redaction,
idempotent re-sends. `tests/mycelic/test_jetstream.py`: forged/unsigned events on the real broker are
rejected. `tests/smoke/mycelic_smoke.py`: a sales agent cannot read a logistics raw note (404) while an
administrator sees every contributor. `tests/mycelic/test_confidentiality.py`: no derived text or quoted
statement carries an agent id, team-visibility text never rises above its team's consolidation (skip-level
contributions and promotions included), derived text is bounded and rebuilds identically, the text of
superseded and retracted memories is withheld from everyone but the producer and administrators (GET, list,
lineage, MCP), and a database derived by the previous derivation version is re-derived at start; `test_api.py`
reproduces the withheld view over HTTP and MCP and checks the MCP tool descriptions and the sentences of this
page. `tests/mycelic/test_integrity.py`: the canonical forms and digests against fixed vectors, a digest on every
row that a rebuild reproduces, SQL edits of content, lineage, key id and origin detected, signing-key rotation
(previous keys, events in flight, a dropped key), the backfill's bound and alarm, and no digest in any API, MCP,
event or `/metrics` output; `test_jetstream.py` replays a stream signed before a rotation on the real broker, and
`test_aggregation_invariants.py` checks every row's digest after randomized sequences.
`tests/mycelic/test_verification.py`: every reason code of downward verification on the node it concerns (tampering
with each kind of row, status flips, retractions in flight, rule and `MIN_SUPPORT` changes, old leaves, missing
keys, events and digests, truncated walks, replays), redaction for viewers of other teams and regions,
authorisation (another organization's memory is a 404), determinism and rebuilds. `test_api.py` checks `GET /verify/{id}` over HTTP (401, 403
for a missing scope and a revoked agent, the same 404 for another team's note, an unknown id and another
organization's conclusion, 400 for malformed ids and `max_leaf_age`, and the four verdicts with 200, without other
teams' text or agent ids), the opt-in `"verify"` of `POST /query`, the `mycelic_verify` tool, the cost-weighted
limit over REST, concurrent requests and MCP batches, and that `/metrics` counts an agent's verification codes as
its report shows them; `tests/mycelic/test_sdk_cli.py` the SDK, the CLI's exit
statuses and the stdio proxy against a live server; the scanner of `test_integrity.py` also reads `GET /verify` (an
agent and the administrator), `POST /query` with `"verify": true` and `mycelic_verify`; and smoke steps 5 and 8
verify the answer as a sales agent (other teams' notes redacted, none of their text in the report) and as the
administrator, whose report digest is the same after the database is rebuilt from the stream.

### What downward verification proves, and what it does not

A `verified` answer at `verified_at` proves, for every node of the walk:

* shape: every parent exists, there is no cycle, a raw note has no parents, and every lineage edge agrees with its
  parent row;
* integrity: every row's digest matches the row as stored and its parents (§3); with a signing key set, an unkeyed
  digest or one under a key the deployment no longer has does not pass;
* raw notes: each one equals its `memory.observed` event in the log on every field the event carries, the events it
  cites are in the log, its producer is a registered agent at its path, and its status agrees with the retractions
  the log holds;
* derived memories: each one is recomputed from its stored parents under its stored derivation (the rule snapshot or
  `MIN_SUPPORT` it was derived under) and the result has its id, text, confidence and every field the digest covers;
  its parents lie inside its unit and are eligible evidence, and the support thresholds (`MIN_SUPPORT`, a rule's
  slots, `min_agents`, `min_teams`, `min_units`) hold;
* currency: no node is retracted or superseded, the memory's rule is still applied, enabled and unchanged,
  `MIN_SUPPORT` is the configured one, and the planner derives exactly this memory from the applied evidence now;
* with `max_leaf_age`: every raw note the caller can read was ingested by the server within that many seconds.

It does not prove:

* that an observation is true in the world: only that the derivation from what agents reported is sound and current;
* that a producer still stands by its note: there is no re-attestation in this release. Freshness is the server's
  ingest time, not the producer's `observed_at`, and raw notes the caller may not read are not judged
  (`freshness_partial` is then true);
* anything against a holder of the signing key or the broker credentials: the event log is trusted (§7, item 1),
  and without `MYCELIC_EVENT_SIGNING_KEY` a digest detects corruption, not edits (a re-hashed raw note is still
  caught by the comparison with its event, and a derived memory is still recomputed from its parents);
* anything about events not yet applied (an administrator sees the `log_lag` warning);
* anything beyond `MYCELIC_VERIFY_MAX_NODES` nodes: such a walk is `unverifiable` (`walk_truncated`);
* why a node the caller may not read failed: that caller sees `hidden_error`, and its verification adds only that
  code to `/metrics`; the code itself is in the reports of callers who may read the node (administrators among
  them), in the `/metrics` totals their verifications add (§2), and in the `memory.verify` audit row;
* rows derived by an older release: they are `unverifiable` (`legacy_derivation`) until re-aggregated.

## 7. Limitations (read these)

1. **The broker is inside the trust boundary.** Whoever holds the NATS credentials (or the signing key)
   can inject events that become organizational memory. Signing plus registry validation makes it
   detectable and hard, not impossible. Keep the broker unreachable from agents. Rotating the NATS
   credentials is safe (give the broker and the service the new pair and restart both).
   `MYCELIC_EVENT_SIGNING_KEY` is different: it is backup-critical state, like the database, because it signs
   every event and every memory digest. Rotate it only through `MYCELIC_EVENT_SIGNING_KEYS_PREVIOUS`
   (DEPLOYMENT.md §4, "Rotating the signing key"): the new key signs from then on, and the old one, listed as
   previous, keeps verifying the events and the digests it signed. Keep a previous key for as long as the stream
   holds events it signed (for the default unbounded stream, forever) and as long as the rows it signed should
   verify. Dropping it makes those rows read `unknown_key` (never verified), and a database rebuilt from the
   stream then rejects every event it signed and loses the state they carried, agent registrations included (old
   agent keys then get 401). At each start the service records the id of its current key in the database and
   logs a warning naming every recorded one that is no longer configured.
2. **Agent keys are long-lived static bearer secrets** with no expiry and no scoping by IP or time.
   Leaked key ⇒ the attacker writes as that agent until you rotate or revoke it, and whatever it wrote
   keeps feeding team and higher conclusions until retracted (`POST /memory/{id}/retract` re-derives).
3. **One administrator token**, no per-admin identity in the audit log, no SSO/OIDC, no RBAC beyond
   agent scopes.
4. **Consolidation declassifies on purpose, by visibility.** A team consolidation quotes its team's notes
   (each clipped to 220 characters, without agent ids) to that team. Consolidations above team level quote
   only notes marked `visibility: org` and rule conclusions; team-visibility notes are counted there, never
   quoted, and no consolidation adds an agent id to what it quotes. A rule whose conclusion template quotes
   `{slot:...}` publishes the quoted evidence, whatever its visibility, at the rule's target layer and,
   through consolidations of the conclusion's topic, at every layer above it. Raw text and labels are not
   scrubbed: an agent id or secret a producer writes into a note is quoted as written. MCP answers carry
   text written by other agents; treat it as untrusted data. Redaction in lineage withholds attribution and
   full text, not topic, slot, timing or counts.
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
