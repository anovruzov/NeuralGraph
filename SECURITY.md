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
Revocation stops the key and leaves the agent's notes as evidence; removal (`DELETE /admin/agents/{id}?retract=1`)
revokes the key and also retracts every note the agent has (§3). Either way a request authenticated just before the
revocation committed cannot store a note or an event after it: storing one checks the agent's status again inside its
own transaction.

An organization is the enterprise root of an agent's path. Everything an agent reads or writes is confined
to its organization; a deployment may host several.

## 2. Authorization (complete list)

| Action | Rule |
|---|---|
| `POST /memory`, `POST /events` | agents only, scope `memory:write` / `events:write`; the memory is written **as the caller** at the caller's path; a body naming another agent is refused; the admin token cannot write memories |
| `GET /memory/{id}`, `POST /query`, `GET /memories` | scope `memory:read`; an agent sees agent-layer memories of **its own team** (or ones the producer marked `visibility: org`) and derived memories of **every unit it belongs to** (the memory's unit is an ancestor-or-self of the agent's path); a query `scope` must be the caller's team or an ancestor unit (403 otherwise); results are filtered by the same rule, so a query never reveals the existence of a memory outside the caller's view, and a `GET` of one returns 404, not 403. Text of a memory that is not active (superseded or retracted) is returned only to its producer and to administrators; everyone else who may read the memory gets an empty `text` and `text_withheld` set to its status. The memory keeps every other field (its `metadata.statements` and `statement_origins` are dropped), and `GET /memories?status=superseded` or `?status=retracted` lists such memories the same way; `POST /query` searches active memories only; a note's `expires_at` is shown to whoever may read the note, and `POST /query` leaves a note out once that time has passed |
| `GET /lineage/{id}` | scope `lineage:read` on a readable memory (`POST /query` embeds the lineage graph only for callers holding it); contributions the caller may not read are **redacted** (text, agent id, event ids, entity and the producer's local reference withheld; unit path, layer, timestamps, confidence kept). A readable node that is superseded or retracted, and that the caller did not produce, is **withheld**: it keeps its shape and every other field, with an empty `text` and `text_withheld` set to its status; the nodes, edges and counts are those of any other reader. A producer's local reference is shown only to that producer and to administrators |
| `GET /verify/{id}`, `POST /query` with `"verify": true` | scope `lineage:read` on a readable memory: an id that does not exist and one the caller may not read get the same 404, and a caller without the scope gets 403 whatever the id (on `POST /query` the summary is silently left out, like the lineage graph). The verdict and both answers (`derived_correctly`, `still_true`) are computed from the codes before redaction, so every viewer gets the same answer. A node the caller may not read shows only its id, layer, unit (a raw note's team, not its path, which ends in the producer's id), operator, status and `ok`; of its codes it keeps only those lineage already discloses (`node_retracted`, `node_superseded`, `missing_parent`, `cycle_detected`), every other one becomes `hidden_error`, `hidden_unverifiable` or `hidden_stale` (an expired note the caller may not read shows only `hidden_stale`, never its expiry), it carries no details, and its warnings are only counted; `valid_until` is the earliest expiry among the notes the caller can read, and `valid_until_partial` says that others were not counted. No detail on any node, for any viewer, carries text, statements, metadata values, agent or producer ids or row digests; key ids, `as_of` and the `log_lag` warning are shown to administrators only. `mycelic_verification_reasons_total` counts each verification's codes as its caller saw them (a hidden node's as `hidden_*`, its warnings not at all), so diffing `/metrics` around its own call tells an agent nothing its report withheld; the totals also count administrators' verifications, which see every code, so set `MYCELIC_METRICS_TOKEN` where even those totals must not reach agents (without it `/metrics` accepts any agent key) |
| `POST /memory` with `supersedes` | the producing agent only, on its own active raw note: an id that does not exist and one the caller may not read get the same 404; a derived memory is 400, another agent's note 403, a note that is no longer active 400, and 409 while a retraction (an expiry's included) or another update of it is still on its way through the log; `supersedes` on an embedded memory of `POST /events` is 400. A request whose idempotency key already identifies the target is 400. The update is a complete note written as the caller; the old note is superseded only when the update's event applies and it is still the producer's active raw note |
| `POST /memory/{id}/attest`, `GET /attestations/due` | the producing agent only, scope `memory:write`, on its own active raw note that has not expired: an id that does not exist and one the caller may not read get the same 404, another agent's note (and any call by the administrator) 403, a derived, inactive or expired note 400. `still_true: false` retracts the note as `POST /memory/{id}/retract` does. The due list (scope `memory:read`, agents only) holds the caller's own notes only |
| `POST /memory/{id}/retract` | the producing agent or the administrator, raw observations only: a derived memory is a function of its evidence and is withdrawn (retracted, not deleted) when that evidence is retracted. Retraction withdraws a note from answers but does not erase it. Its text stays in the database, the event log and the stream, readable by its producer and administrators |
| `DELETE /admin/agents/{id}`, `?retract=1` | administrator token only. Revokes the key (403 from then on); with `retract=1` (or `true`) the agent is removed: one `agent.removed` event retracts every note it has when it applies, and the response adds `retracted`, the notes active when the call was made. Any other `retract` value is 400, an unknown id 404 (`{"agent_id": …, "revoked": false}`); without `retract` the response is the one of earlier releases |
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
  (`MycelicService.apply_event`). Revocation is enforced at authentication (a revoked key gets 403) and again
  inside each write's transaction, not at apply time: an event the agent published before revocation is still
  applied, and its memories stay active until retracted, or until the agent is removed with `?retract=1`, which
  retracts them all; a note logged after the removal is applied retracted (see §7, item 2). Inside the broker
  boundary an injected event may name any registered agent id, including a revoked one.
* A replay (a rebuild into a fresh database, a restored backup catching up, `POST /admin/replay`) counts, in the
  transaction that consumed each one, the events it consumed and those whose signature it rejected. When it completes
  with more than `MYCELIC_REPLAY_MAX_REJECT_RATIO` (default 0.01) of them rejected, it records a block in the database
  and `/ready` stays 503 with `"reason": "signature_rejections: …"` until a rebuild into a fresh database with the
  corrected keyring (or the ratio is raised to the recorded one, which is rounded up so the value shown is enough, and
  the service restarted); `/health` stays 200 (`degraded`). A replay into the same database never lifts the block.
  Fewer rejections are logged at WARNING and audited, and each rejected event is audited as `event.rejected` either
  way.
* The consumer never inserts a derived memory from the stream: a `memory.derived` event is informational (a
  duplicate when this node derived the same memory, otherwise ignored, audited and counted in
  `mycelic_events_ignored_total`), so an injected event cannot plant a conclusion, a lineage edge or a supersession.
* Duplicate deliveries are harmless: `event_id` is the JetStream `Nats-Msg-Id` and the apply step is
  idempotent by event id and memory id.
* **Memory integrity.** Every memory row carries a digest of its content (text, labels, kind, confidence, support,
  independent teams, visibility, producer, operator and rule; for a raw note also its observation time, event,
  local reference, cited events and metadata, and its expiry and last re-attestation when it has them), its parents
  (the ids its lineage edges name) and its derivation metadata (roots, statements, children, slots, evidence and the
  other keys listed in `mycelic/integrity.py`), and its lifecycle: its `status` unless it is `active`, and
  `superseded_by` (schema 6). The statement that inserts the row writes the digest, and a rebuild from the stream
  under the same key reproduces it byte for byte; a status change (a retraction, a supersession, a reactivation) and a
  producer's re-attestation are the later writes of covered content, and each signs the row again only after the
  row's digest checked as it was stored (`Tx.set_memory_status`, `Tx.reactivate_memory`, `Tx.set_attested`), so an
  edited row is never re-signed and a status set in the database without the key reads `integrity_mismatch`. With
  `MYCELIC_EVENT_SIGNING_KEY` set the digest is an HMAC under a subkey of that key, so an edit by
  anyone who does not hold the key that gives a row a state the key never signed is detected, including one that
  rewrites the key id, recomputes an unkeyed hash or relabels the origin. A rollback is not: a row set back, digest
  columns included, to an earlier state the key did sign (from a backup or any earlier copy of the database) checks
  `ok`, so whoever can write the database and kept such a copy can bring back a retracted note and the conclusion
  built on it, and, by deleting the retraction's row from the `events` table, have both verify (§6, "It does not
  prove"); restrict write access to the database file and its backups accordingly. Without a key it is a plain
  SHA-256 that detects corruption only, and once a key is set such a row reads `downgraded`. Not covered: a memory's
  applied columns (`applied_at`, `apply_seq`) and the `status_reason`/`reactivated_at` metadata, a derived memory's
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
  in a database created at schema 4 or later (it is never backfilled: its first start finds nothing to sign and
  writes no audit row), any backfill WARNING or ERROR, any `integrity.backfill` audit row and any rise of
  `mycelic_integrity_backfilled_total` means digests were removed from the database; in an upgraded database, any
  after the start that completed the upgrade's backfill does. Audit rows are pruned after
  `MYCELIC_AUDIT_RETENTION_DAYS` and can be edited by the same person, so alert on the log and the metric. The
  upgrade to schema 6 signs again, once, the lifecycle of every row that existed before it and was not active, each
  only if its digest checks in the form it was signed in; its bound (`meta.lifecycle_resign_below`) is in the database
  too, so whoever can write it can record it again and have such a row (one from before the upgrade) signed with an
  edited status at the next start, which is logged at WARNING and audited (`integrity.lifecycle_resign`): after the
  upgrade's own, any such line or audit row means the database was edited. Downward
  verification (`GET /verify/{id}`, §2 and §6) runs this check on every row it walks and reports the outcome as a
  reason code (`integrity_mismatch`, `integrity_downgraded`, `integrity_unknown_key`, …), never the digest.
* TLS: `MYCELIC_TLS_CERT_FILE/KEY_FILE` (direct) or a TLS-terminating proxy with `MYCELIC_ALLOWED_HOSTS` (a Host
  that is not listed is answered 421, except on the probes, on a token-checked `/metrics`, which a scrape reaches by
  the pod or service address, and for a loopback Host, which only a client on the node sends: every other route still
  needs its token);
  `tls://` + `MYCELIC_NATS_CA_FILE` for the broker (verified, TLS ≥ 1.2). The SDK verifies server
  certificates and accepts a private CA (`MycelicClient(..., ca_file=...)`).

## 4. Input validation and limits

JSON only (415 otherwise); body ≤ `MYCELIC_MAX_BODY_BYTES` (1 MiB, 413); memory text ≤ 4000 characters;
topic/entity/local reference/`value` ≤ 200; slot names `[A-Za-z0-9_.:-]{1,100}`; metadata ≤ 4 KiB; event payload
≤ 16 KiB; ≤ 100 events per request; serialized event ≤ `MYCELIC_MAX_EVENT_BYTES` (256 KiB) so an accepted
event is always publishable; paths are `[a-z0-9][a-z0-9_-]{0,63}` segments; timestamps must parse as
ISO-8601; kinds and visibility are enumerated; metadata keys the aggregator owns (`agg_key`,
`promoted_from`, `version_of`, `contributing_agents`, `statements`, …) are stripped from agent input
(`metadata.value` and `metadata.conflict` are not: they stay the agent's own, a raw note's `conflict` flags nothing
and its `value` claims only when it is a string of at most 200 characters); cited
`source_event_ids` must be events of the caller's own organization (unknown and foreign ids get the same
error); a note's `expires_at` must be a string that parses as ISO-8601 (at most 40 characters; without an offset it
is taken as UTC), it is stored in UTC to the second, and the stored value must lie in the future and at most
315,360,000 seconds (ten years) ahead when the note is created (a resend of an existing note is not checked again;
an embedded memory of `POST /events` that fails refuses the whole batch). Rate limiting is a token bucket per peer
address before authentication and per principal after it (a bad token spends the address bucket, never the claimed
agent's); a JSON-RPC batch on `/mcp` is capped at `MYCELIC_MAX_BATCH` messages and charged one token per message;
when full, the least recently used tenth of buckets is evicted, never everyone. Behind a proxy
(`MYCELIC_TRUST_PROXY_HEADERS=true`) the client address is the Nth entry from the right of `X-Forwarded-For`
(`MYCELIC_TRUSTED_PROXY_HOPS`), never the leftmost one the client can forge.

Volume: with `MYCELIC_MAX_ACTIVE_MEMORIES_PER_ORG` set (the shipped compose file and ConfigMap set it), a new note that
would take its organization past that many active raw notes is refused with 507 (an MCP error result), checked inside
the write's transaction so concurrent writes cannot pass it together; a `POST /events` batch that would pass it is
refused whole. Resends of stored notes and updates are never refused, notes count until their retraction applies,
and nothing the consumer applies is capped, so a rebuild is never cut short (DEPLOYMENT.md §4, "Volume cap").

Downward verification is priced by its walk: the request's token, then the larger of ceil(nodes / 250) and
ceil(2 × seconds walked × rps) tokens from the caller's principal bucket, charged after the walk, which may take the
bucket into debt as deep as the charge (a walk that ran T seconds keeps its caller refused for about 2T seconds, also
when its request was abandoned). A caller that keeps walking pays twice what its bucket refills meanwhile, however long
its walks run. A principal in debt is refused with 429 before anything is read, and again when its turn to walk
comes. Inside a JSON-RPC batch on `/mcp`, whose messages run back to back without the
middleware between them, every message is charged its token up front and every `mycelic_verify` message its walk, so
once the bucket is in debt the remaining verify messages get an error result without walking, and the next request is
429. A principal walks one verification at a time, at most two walks run at once per organization and four for
everyone, an organization's walks waiting for each other before they wait for the shared slots, so two concurrent
verifications by one principal cannot both walk on one token and one organization's agents cannot hold up another
organization's walks. A walk whose turn has not come within 5 s is refused with 503 (`Retry-After: 1`), and a request
whose client went away is cancelled before it walks. A walk reads at most
`MYCELIC_VERIFY_MAX_NODES` (25,000) nodes, in a worker thread on a read-only snapshot of the database: it never holds
the event loop, so health probes and every other request keep answering while it runs (DEPLOYMENT.md §4, "Verifying a
conclusion"). Lineage walks (`GET /lineage/{id}`, MCP `mycelic_lineage` and the answer of every `POST /query` and MCP
`mycelic_query`) run the same way, with slots of their own, one per principal at a time among its walks, load at
most 2,000 memories (an MCP `mycelic_lineage` result lists at most 20 of them unless `detail` is `full`) and are
priced after the walk at the larger of floor(nodes / 250) and floor(2 × seconds walked × rps) tokens, so a small
lineage costs only its request's token (docs/MYCELIC_ARCHITECTURE.md §6). `max_leaf_age` must
be an integer from 1 to 315,360,000 and a memory id must match `[A-Za-z0-9_.:-]{1,200}` (400 otherwise, also for an
id sent with `%2F` in it, which aiohttp decodes before the check).

## 5. Audit and logs

`audit_log` records agent registration/revocation/rotation, rule changes, replays, every memory ingest and
retraction (principal, action, target, organization, remote address) and rejected events. The expiry sweep writes
`memory.expired` (principal `mycelic`, the note, its organization, its `expires_at` and the event id) once per note,
when it queues the note's retraction. An agent's removal writes `agent.remove` (the administrator's call, with the
notes active then), `agent.removed` (when it applies, with the notes retracted and the derivations made) and
`memory.observed_after_removal` for each note that reached the log after the removal and was applied retracted. A
re-attestation writes `memory.attest` (the producer's call; one with `still_true: false` writes `memory.retract`),
then `memory.attested` when it sets `attested_at`, or `memory.attest_ignored` (with the reason: `attestation_target`,
`attestation_not_newer` or `attestation_integrity`) when it applies without effect. A producer's update writes
`memory.update` (the agent's call, with the note it supersedes) instead of `memory.ingest`, then `memory.updated` when
it supersedes the old note, or `memory.update_conflict` (with the reason) when it applies as a plain note because its
target was gone, no longer active, not raw or another producer's. A replay that rejected signatures writes `recovery.signature_rejections` (principal `mycelic`: the
events it consumed and rejected, the ratio, the configured ratio and whether it blocked readiness) when it completes,
and a `replay` row whose replay ran while readiness was blocked carries the `warning` the caller got. Every successful
downward verification writes
`memory.verify` (principal, target, organization, verdict, nodes walked and the reason codes before redaction, so an
administrator sees why a hidden node failed; remote address); a refused one (403, 400, 404, 429) writes nothing.
Unauthenticated failures are **not** written to the database (they would let anyone grow it); they are counted in
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
statuses and the stdio proxy against a live server; `tests/mycelic/test_lifecycle.py` an agent's removal (one event
retracts every note in one apply, the key and writes already authenticated are refused at once, a note logged after
the removal is applied retracted, a rebuild reproduces it), expiry (validation, notes left out of answers and their
BM25 statistics at once, `leaf_expired` and `hidden_stale`, one retraction per note through the log during an
outage, status and metrics), producer updates (every refusal, 409 while one is pending, conflicts at apply,
supersession chains, forged links and status flips) and how verification reads all three;
`tests/mycelic/test_attestation.py` re-attestation (freshness for `max_leaf_age`, withdrawal, every refusal, never
backwards or after a retraction, never on a row whose digest fails, the due list, rebuilds and the surfaces); the
scanner of `test_integrity.py` also reads `GET /verify` (an agent and the administrator), `POST /query` with
`"verify": true` and `mycelic_verify`; and smoke steps 5 and 8 verify the answer as a sales agent (other teams' notes
redacted, none of their text in the report) and as the
administrator, whose report digest is the same after the database is rebuilt from the stream.

### What downward verification proves, and what it does not

A `verified` answer at `verified_at` proves, for every node of the walk:

* shape: every parent exists, there is no cycle, a raw note has no parents, and every lineage edge agrees with its
  parent row;
* integrity: every row's digest matches the row as stored and its parents (§3); with a signing key set, an unkeyed
  digest or one under a key the deployment no longer has does not pass;
* raw notes: each one equals its `memory.observed` event in the log on every field the event carries, the events it
  cites are in the log, its producer is a registered agent at its path, its status agrees with the retractions and
  removals of its producer the log holds, and a superseded note was superseded by its producer's update (the
  successor's row and its logged event both name it in `version_of`, and the log applied that event);
* derived memories: each one is recomputed from its stored parents under its stored derivation (the rule snapshot or
  `MIN_SUPPORT` it was derived under) and the result has its id, text, confidence and every field the digest covers;
  its parents lie inside its unit and are eligible evidence, and the support thresholds (`MIN_SUPPORT`, a rule's
  slots, `min_agents`, `min_teams`, `min_units`) hold;
* currency: no node is retracted or superseded, the memory's rule is still applied, enabled and unchanged,
  `MIN_SUPPORT` is the configured one, and the planner derives exactly this memory from the applied evidence now;
* expiry: no raw note of the walk, readable by the caller or not, is past its `expires_at` (one that is, while its
  retraction by the expiry sweep has not applied yet, is `leaf_expired`);
* with `max_leaf_age`: every raw note of the walk, readable by the caller or not, was ingested by the server, or
  re-attested by its producer, within that many seconds (a note the caller may not read shows `hidden_stale`, without
  its age, so every viewer gets the same verdict).

It does not prove:

* that an observation is true in the world: only that the derivation from what agents reported is sound and current;
* that a producer's note is true because the producer re-attested it: an attestation (`POST /memory/{id}/attest`) is
  the producer's own statement, self-attestation, so it adds freshness, not independent assurance. Freshness is the
  later of the server's ingest time and the producer's last re-attestation (`attested_at`), never the producer's
  `observed_at`. Raw notes the caller may not read are judged too, so a caller who may verify a conclusion learns,
  for each such note, whether it was last confirmed within the `max_leaf_age` it chose (shown as `hidden_stale`, never
  the age); repeating the call with other bounds narrows that time down, as the `created_at` and `applied_at` that
  lineage shows for such a note already do for its ingest. `freshness_partial` is true only when some raw note could
  not be judged (a truncated walk, a note whose event row is gone).
  `attested_at` is covered by the row's digest (an edit reads `integrity_mismatch`) but is not compared with the
  `memory.attested` events in the log, so without `MYCELIC_EVENT_SIGNING_KEY` whoever can write the database can make a
  note look freshly attested and re-hash it;
* anything against a holder of the signing key or the broker credentials: the event log is trusted (§7, item 1),
  and without `MYCELIC_EVENT_SIGNING_KEY` a digest detects corruption, not edits (a re-hashed raw note is still
  caught by the comparison with its event, and a derived memory is still recomputed from its parents);
* anything about events not yet applied (an administrator sees the `log_lag` warning);
* anything beyond `MYCELIC_VERIFY_MAX_NODES` nodes: such a walk is `unverifiable` (`walk_truncated`);
* why a node the caller may not read failed: that caller sees `hidden_error`, and its verification adds only that
  code to `/metrics`; the code itself is in the reports of callers who may read the node (administrators among
  them), in the `/metrics` totals their verifications add (§2), and in the `memory.verify` audit row;
* rows derived by an older release: they are `unverifiable` (`legacy_derivation`) until re-aggregated;
* that a row was not set back, as a whole, to an earlier state it was signed in: the digests cover each row's
  content and lifecycle as it is now, not its history, so a row whose status and digest columns are both restored
  from an earlier copy of the database (a backup) reads `ok`. A status set in the database alone (a retracted note or
  conclusion set active again, a superseded note with its `superseded_by` cleared) breaks the row's digest
  (`integrity_mismatch`), and the retraction and removal events that a raw note's status is checked against are rows
  of the `events` table, which no digest covers. So someone who can write the database, without the key, can restore
  a retracted note and the conclusions built on it from an earlier copy, mark the retraction's event `failed` or delete
  it, and have them verify (`verified`, `still_true` true, no reason), for every caller and in `POST /query` with
  `"verify": true`; a re-aggregation changes nothing, because the note is active again. Nothing in the database can
  tell such a rollback from the state it restores: the stream (JetStream) still holds the retraction, so rebuilding the
  database from the stream ("database lost or corrupt", DEPLOYMENT.md section 4) brings the retraction back. Keep the
  database file and its backups writable by the service only;
* that two notes agree: only notes that carry a `value` for the same slot and entity are compared, and those with
  different values are flagged (`metadata.conflict`, on consolidations and rule conclusions alike, and the warning
  `disputed` in a verification report) and never corroborate each other (docs/MYCELIC_ARCHITECTURE.md §5); free text
  is never compared. The value of a team-visibility note is compared at every layer above its team
  (as a digest in `metadata.claims`, shown to administrators only) but never published there, so readers above a
  team learn one bit from it: that some note beneath disagrees (`metadata.conflict`, the lower confidence and the
  missing slot).

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
   agent keys then get 401). Such a rebuild is not silent: when more than `MYCELIC_REPLAY_MAX_REJECT_RATIO` of the
   events it consumed were rejected, `/ready` returns 503 (`signature_rejections`) until a rebuild into a fresh
   database with the key listed again (DEPLOYMENT.md §4, "Signing-key mistakes"). At each start the service records
   the id of its current key in the database and logs a warning naming every recorded one that is no longer
   configured.
2. **Agent keys are long-lived static bearer secrets** with no expiry and no scoping by IP or time.
   Leaked key ⇒ the attacker writes as that agent until you rotate or revoke it, and whatever it wrote
   keeps feeding team and higher conclusions until retracted (`POST /memory/{id}/retract` re-derives). When
   you cannot tell its notes from the agent's own, remove the agent (`python -m mycelic revoke-agent --agent-id X
   --retract`, `DELETE /admin/agents/{id}?retract=1`): every note it has is retracted in one step and whatever
   rested on them is re-derived without them; register a new id for the legitimate agent and let it re-share.
   A rollback to an earlier release undoes a removal made since the snapshots it restores: revoke the agent again
   there (DEPLOYMENT.md §4a).
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
