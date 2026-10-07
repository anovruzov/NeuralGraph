# Build prompt: Mycelic universal communications ingestion

> Paste everything below the line into Claude Code (or any coding agent) at the root of this repository.
> It is written to be executed, not discussed.

---

You are a senior engineer on Mycelic, working in this repository (`anovruzov/NeuralGraph`).

## Why this exists

Today Mycelic and NeuralGraph learn **only** through Claude Code and MCP, plus the Python SDK and HTTP API.
An agent has to be explicitly told something before Mycelic knows it. That caps the product at whatever a
handful of developers type into a coding assistant.

The thesis of the company is that an organization knows things collectively that nobody knows individually.
Most of that distributed knowledge lives in communication platforms: chat, email, meetings, and threads.
Your job is to make Mycelic **observe those platforms directly**. Every channel, mailbox and meeting
becomes a local observer node that writes into the hierarchy Mycelic already has.

Do not build a search engine over messages. Do not build another Slack bot. Do not centralize raw message
text by default. Build the ingestion layer that turns communications into **local claims with lineage**, so
that the aggregation, corroboration, retraction and lineage machinery already in `mycelic/` can find things
no single channel, team or person can see.

## Read first, before writing code

1. `README.md`, `docs/MYCELIC_ARCHITECTURE.md`, `DEPLOYMENT.md`, `SECURITY.md`.
2. `mycelic/models.py` (`Memory`, `Rule`, `EventRecord`), `mycelic/hierarchy.py` (agent paths),
   `mycelic/service.py` (ingest, apply, retract), `mycelic/aggregation.py` (topic consolidation, slot
   composition, corroboration with `min_units`), `mycelic/lineage.py`, `mycelic/auth.py`.
3. `mycelic/sdk/__init__.py` (`MycelicClient.remember`, `LocalMemory.note/share`) and `mycelic/sdk/agent.py`.
4. `NeuralGraph/chat_memory/extraction.py` and `worker.py`. These hold the existing gate → extract → ground
   pipeline that turns conversation turns into grounded memories. **Reuse it. Do not write a second one.**
5. `tests/mycelic/` and `tests/smoke/mycelic_smoke.py`, so new work follows the same testing discipline.

Write a short `docs/COMMS_INGESTION_PLAN.md` before coding. It should cover what you will reuse, what you
will add, and every assumption you could not verify.

## What to build

### 1. A normalized communications event, `CommsEvent`

Add `mycelic/comms/models.py`. Every platform adapter emits exactly this shape and nothing else
downstream knows which platform a message came from.

| field | meaning |
|---|---|
| `platform` | `slack`, `teams`, `gmail`, `outlook`, `google_chat`, `discord`, `zoom`, `meet`, `mattermost`, `matrix`, `telegram`, `whatsapp`, `imap`, `export` |
| `tenant` | workspace, tenant or domain id |
| `conversation_id`, `conversation_kind` | channel, DM, group DM, email thread, or meeting; also public or private |
| `thread_id`, `message_id`, `parent_message_id` | threading, so a reply is never mistaken for an independent report |
| `author` | a resolved `Identity` (see §3), never a raw display name |
| `sent_at`, `edited_at`, `deleted_at` | source time, used by supersession and retraction |
| `text`, `attachments_meta` | body plus attachment names, types and sizes; never attachment bytes in v1 |
| `quoted_message_ids`, `forwarded_from`, `crossposted_from` | **copy lineage**, which independence accounting needs (§5) |
| `permalink_hash`, `source_ref` | an opaque, verifiable pointer back to the original; never the raw URL in shared memories |
| `acl` | who can see this in the source system (§6) |
| `version` | an edit counter; the idempotency key is `platform:tenant:message_id:version` |

Edits must become **supersession** of the claims derived from the old version. Deletes must become
**retraction**. Mycelic already cascades retraction to every dependent conclusion. Wire into that cascade,
and add a test proving that deleting the source message withdraws an enterprise-level conclusion that
depended on it.

### 2. Platform adapters, in this priority order

Each adapter has two paths. **Backfill** pages through history with checkpointing. **Live** consumes the
platform's push mechanism. Both emit `CommsEvent` into a local durable queue, using the outbox pattern
already used in `mycelic/service.py`.

**Phase 1. Ship these first; they cover the large majority of companies.**
- **Export import (zero-auth pilot path).** Accept a Slack workspace export ZIP, a Microsoft Teams
  export, `mbox` and `.eml` files, and Google Takeout mail. This is how a pilot customer tries Mycelic in
  one afternoon without an IT security review. Build it first.
- **Slack.** Events API over HTTP plus Socket Mode for live; `conversations.history` and
  `conversations.replies` for backfill. Handle message edits, deletes, thread replies, `files.shared` and
  channel joins.
- **Microsoft 365 via Microsoft Graph.** Teams channel and chat messages, Outlook mail, and Teams meeting
  transcripts. Use change notifications for live and delta queries for backfill. Use one Graph client for
  all of it.
- **Google Workspace.** Gmail through `users.history` with Pub/Sub watch for live, and Google Chat. Pull
  Meet transcripts through Drive and Docs where the tenant enables them.
- **Generic IMAP.** IMAP IDLE plus a UID checkpoint, as the fallback for any mail server.

**Phase 2.** Zoom (meeting transcripts via webhooks), Discord (gateway plus history), Mattermost, and
Matrix/Element.

**Phase 3.** Telegram (Bot API, groups the bot is added to), WhatsApp Business Cloud API, and Twilio SMS.

**Build vs buy.** Evaluate unified-API vendors (Nango, Merge, Paragon, Unified.to, Composio) for OAuth and
token refresh only. Record the decision in the plan doc. Mycelic must own the event normalization, the
copy lineage and the local extraction, because those are the product. A vendor may own auth plumbing at
most.

**Verify current platform terms before building each adapter, and write what you find into the plan doc
with links.** Slack in particular has tightened API rate limits and data-use terms for non-Marketplace
apps (including history-endpoint limits and restrictions on bulk storage and LLM use of API data).
Microsoft Graph has metered APIs for Teams export. Gmail restricted scopes require a security assessment
for public apps. These constraints decide whether live sync is viable or whether the export path and
customer-hosted deployment are the real product. Do not hand-wave them.

### 3. Identity and the org hierarchy

Mycelic's hierarchy is `agent → team → department → subsidiary → region → enterprise`. Communications only
help if each author lands in the right place.

- Add `mycelic/comms/identity.py`. Resolve platform users to one `Identity` across Slack, Microsoft 365,
  Google and email addresses. Match on verified email first, then directory id, then an admin-confirmed
  manual mapping. Never match on display name.
- Import the org structure from a directory: Microsoft Entra ID, the Google Directory API, a SCIM feed, or a
  CSV upload as the fallback. Map department, team and manager chain into agent paths.
- **Each person becomes an observer node.** Their messages are their local observations, kept in their
  own `LocalMemory`. Each channel or mailing list is also a node whose scope is the team that owns it.
- Unmapped identities, such as external customers and vendors, go to an `external` branch keyed by email
  domain. **This is where the most valuable cross-team signal lives**, for example one customer complaining
  to support, sales and engineering separately. Keep them; do not drop them.

### 4. Local extraction: message → claims

Add `mycelic/comms/extract.py`. It must reuse the NeuralGraph `chat_memory` gate → extract → ground
pipeline and the existing `llm_backend` shim, so it runs against a local model (Ollama or LM Studio) or a
configured API model. It must also work with `--fake-llm`, deterministically, for tests.

For each thread window, not each single message, produce zero or more **claims**:

```json
{
  "text": "Customer acme-corp reports CSV export timing out since the 3.2 release",
  "entity": "customer:acme-corp",
  "topic": "product.export",
  "slot": "customer_reported_defect",
  "kind": "observation",
  "polarity": "positive",
  "valid_from": "2026-09-30T14:02:00Z",
  "confidence": 0.7,
  "evidence": [{"source_ref": "<opaque>", "message_id": "...", "author_identity": "...", "quote_span": [12, 64]}],
  "copy_of": null
}
```

Rules for the extractor prompt, which you also write and version in `mycelic/comms/prompts/`:

1. **Gate first.** Most messages contain no organizational fact: greetings, scheduling, emoji, and "thanks!".
   The gate must reject them before any expensive call. Report the gate's pass rate as a metric.
2. **Grounded or rejected.** Every claim must cite a span of the source text that supports it. A claim the
   span does not support is dropped, which is the same rule `chat_memory` already enforces.
3. **Entities are canonical.** Canonical forms are `customer:<domain-or-name>`, `product:<area>`,
   `service:<name>`, `vendor:<name>`, `project:<key>`, `incident:<id>` and `person:<identity>`. Keep a
   per-tenant alias table so that "Acme", "ACME Corp" and "acme.com" collapse to one entity.
4. **Slots come from a small, versioned vocabulary, not free text.** This is what lets slot-composition
   rules fire across teams. Start with: `customer_reported_defect`, `customer_churn_signal`,
   `deal_risk`, `incident_symptom`, `deploy_or_change`, `dependency_risk`, `capacity_or_cost_pressure`,
   `duplicate_effort`, `decision`, `commitment_or_deadline`, `contradiction`, `security_concern`,
   `supplier_or_vendor_issue`.
5. **Never invent independence.** If the text is a quote, forward, cross-post, or "as X said", set `copy_of`
   to the original message's claim. Do not emit it as a fresh observation.
6. **Respect time.** `valid_from` is the source time, never ingestion time. Statements about the future,
   such as "we will ship Friday", are `commitment_or_deadline` with their own validity.

### 5. Independence accounting, which is the moat

Corroboration is worthless if one complaint forwarded to five channels counts as five witnesses.

- Build `mycelic/comms/independence.py`. Two claims are **not** independent if any of these holds: one is
  in the other's quote, forward or cross-post lineage; they share a root message; the same author made
  both; or near-duplicate text (MinHash or SimHash) appears within a short window from authors who were in
  the same conversation beforehand.
- Feed an `independence_class` into the memory metadata. Make `support` and `independent_teams` in
  `aggregation.py` count **independent root observations**, not raw memories. Add the rule
  `min_independent_roots` alongside `min_agents`.
- Test this adversarially. Seed one rumor copied across ten channels against three genuinely independent
  reports. The rumor must not reach a corroborated conclusion; the three independent reports must.

### 6. Privacy, permissions and consent

- **Default scope** is public channels and shared mailboxes the admin explicitly enables. DMs, private
  channels and personal mailboxes are **off** unless the tenant admin enables them **and** the individual
  opts in.
- **Raw text stays local.** Messages live only in the per-node `LocalMemory` on customer infrastructure.
  The Mycelic service receives claims, evidence pointers (`source_ref` and `permalink_hash`) and short
  quote spans only when policy allows. Make "claims only, no quote spans" a single configuration flag.
- **Source ACLs travel with the claim.** A claim derived from a private channel inherits that channel's
  visibility. The existing per-principal redaction in `mycelic/lineage.py` must enforce it on query and
  lineage. Add a test where a user outside a private channel queries a conclusion and gets redacted
  lineage.
- Add a `/admin/comms/audit` endpoint listing what was ingested, from where, what left the node, and why.
- Honour deletion and right-to-be-forgotten requests through retraction, and test the cascade.
- Store secrets only through the existing `MYCELIC_*` configuration validation. Never commit tokens or
  tenant ids.

### 7. Discovery rules that use communications

Ship a starter rule pack in `mycelic/comms/rules/` as data, not code, using the existing `Rule` model.
Each rule must fire **only** on independent evidence from at least two organizational units.

- **Silent product defect.** `customer_reported_defect` from support, plus `deal_risk` or
  `customer_churn_signal` from sales, plus `deploy_or_change` from engineering, all on the same `product:`
  area within 14 days, produces "probable regression nobody has filed".
- **Account at risk.** `customer_churn_signal` from two of support, success and sales on the same
  `customer:`, with no `decision` that addresses it.
- **Duplicate effort.** `duplicate_effort` or two `project:` entities with overlapping topics, owned by
  different teams, with no shared conversation between the owners.
- **Organizational contradiction.** Two `commitment_or_deadline` or `decision` claims on the same entity
  with incompatible content from different units.
- **Emerging vendor risk.** `supplier_or_vendor_issue` from at least two departments on the same `vendor:`.

When a rule is one slot short, it should emit a **question** routed to the identities most likely to hold
the missing evidence. Route by who has talked about the entity, using the existing lineage and entity
index. Expose questions through MCP and as an optional Slack, Teams or email digest. Questions are
**never** auto-sent in v1; a human approves them.

### 8. Surfaces

- **MCP.** Add the tools `comms_sources`, `comms_discoveries` (corroborated conclusions with lineage),
  `comms_open_questions`, and `comms_explain(memory_id)`. The explain tool returns the lineage tree down to
  opaque message pointers and shows which roots were counted as independent and which were collapsed.
- **CLI.** Add `python -m mycelic comms import <export.zip|mbox>`, `comms connect slack|graph|gmail|imap`,
  `comms status`, and `comms backfill --since`.
- **Digest.** Add a weekly "what the company knows that nobody wrote down" report as Markdown or HTML.
  Every item links to its lineage.

### 9. Tests and evidence (non-negotiable)

- **Offline fakes for every adapter.** Use recorded and redacted payload fixtures under
  `tests/mycelic/comms/fixtures/` and replay them. CI must need no network and no tokens.
- **Contract tests** per adapter: pagination, rate-limit backoff, edit, delete, thread reply, cross-post,
  forward, and checkpoint resume after a crash.
- **End-to-end tests.** One Slack export plus one mbox with planted cross-team facts goes in, and the
  expected corroborated discovery comes out with correct lineage. Delete the source message and the
  discovery is withdrawn. Copy the rumor ten times and it does not corroborate.
- **A real-data benchmark** in `research/comms_bench/`, using public corpora, such as the Enron email
  corpus or Apache project mailing lists joined with their JIRA issues and commits. In those, a problem
  later recorded in an issue tracker was first discussed in scattered threads. Measure precision, recall,
  and time-to-discovery against the issue's creation date. Compare against these baselines:
  1. centralized RAG over all messages;
  2. a single long-context model over chunked messages;
  3. per-channel agents with no aggregation;
  4. Mycelic.
  **Publish it even if Mycelic loses**, as this repository already does for LoCoMo and the enterprise
  benchmark. Report bytes of raw text that left each node per approach.
- Extend `tests/smoke/mycelic_smoke.py` with an export import step.
- Run `python -m pytest tests/mycelic -q` and the smoke test before every commit. Never skip or disable a
  test to get green.

### 10. Demo, to ship within 7 days of starting

Build `demo/mycelic_comms_demo.py`, which runs offline with `--fake-llm` and with a real local model:

1. Import a synthetic but realistic 40-person company: a Slack export plus an mbox. It has support, sales,
   engineering and finance channels across two offices, three months of traffic, and around 20k messages.
   The noise is realistic and includes decoys.
2. Three planted facts exist only across teams:
   - a regression that support sees as complaints, sales sees as a stalled renewal, and engineering
     shipped as a harmless config change;
   - two teams building the same internal tool;
   - a vendor price increase mentioned separately in finance and engineering.
3. Also plant one viral rumor cross-posted to eight channels.
4. Show the following:
   - Mycelic surfaces all three findings with lineage to the exact messages.
   - It refuses to corroborate the rumor and explains why.
   - It asks one targeted question to fill a missing slot.
   - Deleting a source message withdraws the dependent conclusion.
   - No raw message text left any node.
5. Print the same task's result from the centralized RAG baseline side by side, honestly, including where
   the baseline wins.

## Constraints

- Python 3.11+, the existing dependency style (stdlib-first SDK, `aiohttp`, `nats-py`), and no heavy
  frameworks. Platform SDKs are optional extras (`pip install mycelic[slack]`), never core dependencies.
- Follow `CONTRIBUTING.md` and `SECURITY.md`. Make small commits with clear messages.
- Mark every claim in docs as **PROVEN** (a test you ran), **MEASURED**, **INFERRED**, or **UNVERIFIED**,
  matching the repository's existing honesty convention.
- If a platform's terms make live sync non-viable for a commercially distributed app, say so in the plan
  doc. Then make the export path and customer-hosted connector the primary product rather than pretending.

## Definition of done

- [ ] The plan doc is written, platform terms are verified with links, and the build-vs-buy decision is recorded.
- [ ] `CommsEvent`, identity resolution and the directory-to-hierarchy mapping are merged with tests.
- [ ] Export import (Slack ZIP, mbox/eml) works end to end offline.
- [ ] Slack, Microsoft Graph, Gmail and IMAP adapters pass contract tests against fixtures.
- [ ] Extraction reuses `chat_memory`, is grounded, has a gate, and has a versioned prompt.
- [ ] Independence accounting counts root observations, and the rumor test passes.
- [ ] Privacy defaults, ACL inheritance, redaction and the deletion cascade are tested.
- [ ] The starter rule pack fires on planted cross-team facts and nowhere else in the demo corpus.
- [ ] MCP tools, the CLI and the digest work.
- [ ] The demo runs offline, and the real-data benchmark is published with all baselines, including losses.
