# Mycelic HTTP API contract

Base path `/api`. JSON in, JSON out (`Content-Type: application/json`). Errors: `{"error": "<message>", "code": "<short>"}`
with 400 (validation), 401 (no session), 403 (forbidden; audited), 404, 409 (conflict/idempotent replay), 429 (budget).

Authentication: session cookie `mycelic_session` (HttpOnly, SameSite=Lax) set by login/register/accept/demo-switch, or
`Authorization: Bearer <session token | mk_ api key | holder key>`. State-changing requests from browsers must carry an
`Origin` that is same-origin or on `MYCELIC_CORS_ORIGINS` (CSRF). Every response carries `X-Request-Id`.

Timestamps are ISO-8601 UTC strings. IDs are prefixed (`usr_`, `unit_`, `goal_`, `q_`, `claim_`, `ev_`, `disc_`, `hold_`).
List endpoints accept `limit` (default 50, max 500) and return `{"items": [...], "total": n}` unless stated.

Visibility is enforced server-side: every list is already filtered for the caller; a forbidden detail read is 403.

## Auth and onboarding

| Method | Path | Body → Response |
|---|---|---|
| POST | `/auth/register` | `{org_name, slug, email, name, password}` → `{tenant, user, principal}` + cookie. Creates the executive root unit; first user is `org_admin` + `executive`. |
| POST | `/auth/login` | `{email, password, tenant_slug?}` → `{user, principal}` + cookie |
| POST | `/auth/logout` | → `{ok: true}` |
| GET | `/auth/me` | → `{user, tenant:{tenant_id, name, slug, is_demo}, principal, levels:[...], holders:[...], unread_notifications:n, demo_mode:bool, onboarding:{complete:bool, steps:{...}}}` |
| GET | `/auth/invitation/{token}` | → `{invitation:{email, role, unit_name, org_name, status}}` |
| POST | `/auth/invitation/{token}/accept` | `{name, password}` → `{user, principal}` + cookie |
| GET | `/demo/personas` | demo mode only → `{items:[{user_id, name, email, roles:[...], unit_names:[...], level}]}` |
| POST | `/demo/switch` | demo mode only, `{user_id}` → `{user, principal}` + cookie (a real session for a demo account; authorization is the production path) |

`principal` = `Principal.to_dict()`: `{tenant_id, kind, id, name, is_admin, is_demo, highest_level, roles, holder_ids, memberships:[{unit_id, role, unit_type, unit_name, level}]}`.

`onboarding.steps` = `{organization:bool, members:bool, holder:bool, model:bool, goal:bool, loop_active:bool}`.

## Organization (admin unless stated)

| Method | Path | Notes |
|---|---|---|
| GET | `/org` | any member → `{tenant, units:[unit...], tree:[{unit, children:[...]}], counts:{users, memberships, holders}}`. `unit` = `{unit_id, type, level, name, parent_id, path, depth, settings, archived_at, member_count}` |
| POST | `/org/units` | `{type, name, parent_id?, settings?}` → `{unit}` |
| PATCH | `/org/units/{unit_id}` | `{name?, parent_id?, archived?}` → `{unit}` |
| PUT | `/org/units/{unit_id}/scope` | project only: `{unit_ids:[...]}` → `{unit_ids}` |
| GET | `/org/units/{unit_id}` | member/lead → `{unit, members:[{user_id, user_name, email, role}], children:[unit], projects:[unit], holders:[holder]}` |
| GET | `/org/users` | admin → `{items:[user]}`; `user` = `{user_id, email, name, status, is_demo, created_at, memberships:[...]}` |
| PATCH | `/org/users/{user_id}` | admin `{status: active|disabled, name?}` |
| GET | `/org/memberships` | admin → `{items:[{membership_id, user_id, user_name, email, unit_id, unit_name, unit_type, role, status, created_at}]}` |
| POST | `/org/memberships` | admin `{user_id, unit_id, role}` → `{membership}` |
| DELETE | `/org/memberships` | admin `{user_id, unit_id, role?}` → `{revoked:n}` (pending routes to that user's holders are revoked for units they lost) |
| GET | `/org/invitations` | admin → `{items:[invitation]}` |
| POST | `/org/invitations` | admin `{email, role, unit_id?}` → `{invitation, accept_url}` (token only shown once, in `accept_url`) |
| DELETE | `/org/invitations/{invitation_id}` | admin |
| GET | `/org/policies` | admin → `{policies:{min_independent_roots, freshness_days, default_goal_budget, priority_weights, model_tiers, holder_default_export}}` |
| PUT | `/org/policies` | admin `{key, value}` → `{policies}` |
| GET | `/org/network?view=hierarchy|collaboration|lineage&goal_id=&unit_id=` | any member → `{nodes:[{id, type: unit|user|holder|goal|question|claim|discovery|evidence, label, level?, unit_id?, status?, meta}], edges:[{source, target, kind: parent|member|leads|owns|routed|responded|supports|derived|conflicts|scoped|project}]}` filtered by visibility |

## Grants and holders

| Method | Path | Notes |
|---|---|---|
| GET | `/grants` | → `{given:[grant], received:[grant]}` |
| POST | `/grants` | `{grantee_type: user|unit, grantee_id, resource_type: holder|claim|discovery|goal|evidence_ref, resource_id, level: read|artifact|raw, reason?, expires_in_seconds?}` — caller must own the resource (holder owner, claim creator, goal manager) |
| DELETE | `/grants/{grant_id}` | grantor or admin |
| GET | `/holders` | → `{items:[holder]}` visible holders (own, unit-owned in visible units; admin sees all). `holder` = `{holder_id, owner_type, owner_id, owner_name, name, status, mode, domains, export_policy, last_heartbeat_at, stats:{documents, memories, questions_answered, ...}}` |
| POST | `/holders` | `{name, owner_type?: user (default self) | unit, owner_id?, domains?, export_policy?, mode?: embedded|external}` → `{holder, key}` (key shown once) |
| PATCH | `/holders/{holder_id}` | owner/admin: `{export_policy?, domains?, name?, status?: revoked}` |
| POST | `/holders/{holder_id}/rotate-key` | owner/admin → `{key}` |
| POST | `/holders/{holder_id}/documents` | owner (or artifact grant): JSON `{title, text, kind?, observed_at?, domains?, origin_id?}` or multipart `file` (.txt/.md/.json/.csv; PDF not supported) → `{document:{doc_id, title, kind, source_root_id, observed_at, status, version, chars, chunks, domains}}` (202 when ingest is asynchronous, then `document.status = "indexing"`) |
| GET | `/holders/{holder_id}/documents` | owner/granted → `{items:[document]}` |
| GET | `/holders/{holder_id}/documents/{doc_id}` | owner/raw grant → `{document, text}` |
| POST | `/holders/{holder_id}/documents/{doc_id}/revise` | owner `{text, title?, observed_at?}` → new version; affected claims re-verified |
| POST | `/holders/{holder_id}/documents/{doc_id}/retract` | owner `{reason}` |
| GET | `/holders/{holder_id}/search?q=&k=` | owner/artifact grant → `{results:[{memory_id, text, kind, score, observed_at, sources:[...], doc_id, title}]}` |
| GET | `/holders/{holder_id}/stats` | → `{documents, memories, entities, queue, last_ingest_at, status}` |
| GET | `/me/memory/search?q=&k=` | across the caller's own holders → `{results:[...]}` |
| GET | `/me/memory/recent?limit=` | → `{items:[memory]}` |

Holder process endpoints (bearer = holder key): `POST /holders/{holder_id}/heartbeat {stats}`; the rest of holder ↔ core traffic goes over the transport.
When `stats.ingest.domains` (`{domain_id: count}`, counts only) names tenant-taxonomy domains the holder does not list yet, they are added to its
`domains` (so questions about them can reach it) and audited as `holder.domains_published`; personal and unknown domains are ignored, a holder
lists at most 64 domains, and `export_policy.auto_domains: false` keeps a hand-curated list. Question routing matches domains through the
tenant taxonomy: a question about `engineering` reaches a holder in `engineering.dependencies`, and legacy names resolve through aliases.

Question envelope delivered to a holder (transport kind `question`): `{question_id, text, kind, goal_id, scope_unit_id, audience:{principal_ids, complete, owner},
candidate_domains, valid_from, valid_to, policy, budget, asked_at}`. `audience` lists every user who will be able to read what the question produces;
a holder discloses a member-restricted record only when that list lies inside the source's members.

## Agent chat

| Method | Path | Notes |
|---|---|---|
| GET | `/chats` | → `{items:[{chat_id, agent_type, agent_id, agent_name, title, updated_at}]}` |
| POST | `/chats` | `{agent_type: user|unit, agent_id}` (unit agents require membership/lead) → `{chat}` |
| GET | `/chats/{chat_id}` | → `{chat, messages:[{id, role, content, citations:[{type, id, label}], at}]}` |
| POST | `/chats/{chat_id}/messages` | `{text}` → `{message, citations, context_summary:{claims:n, discoveries:n, memories:n}}` |

## Goals and loops

`goal` = `{goal_id, owner_type, owner_id, owner_name, scope_unit_id, scope_unit_name, parent_goal_id, title, objective, success_criteria:[{metric, target, direction?}], baseline, measurement_source, deadline, priority, status, permitted_actions, budget:{tokens, usd, questions, followup_depth}, budget_spent:{tokens, usd, questions}, dependencies:[goal_id], progress:{known:bool, value?:0..1, method?, computed_at?, note?}, assignees:[{type, id, name}], is_demo, version, created_by, created_at, updated_at, loop?:loop, counts:{questions, open_questions, discoveries, claims}}`

`loop` = `{goal_id, desired, state, explanation, active_indicator:{active:bool, heartbeat_age_seconds, worker_id}, config, last_run_at, next_check_at, run_count, stats:{questions_asked, discoveries, tokens, usd}, budget_remaining:{tokens, usd, questions}}`

| Method | Path | Notes |
|---|---|---|
| GET | `/goals?scope_unit_id=&status=&mine=1&parent_goal_id=&include_archived=` | → `{items:[goal]}` |
| POST | `/goals` | `{title, objective, owner_type?, owner_id?, scope_unit_id?, parent_goal_id?, success_criteria?, baseline?, measurement_source?, deadline?, priority?, permitted_actions?, budget?, dependencies?, assignees?, activate?:bool}` → `{goal}`; `activate: true` activates and starts the loop (default true when model + holder are configured) |
| GET | `/goals/{goal_id}` | → `{goal, subgoals:[goal], parent:goal?, loop, questions:[question(summary)], discoveries:[discovery(summary)], outcomes:[outcome], usage:{tokens, usd, calls}}` |
| PATCH | `/goals/{goal_id}` | any goal field except status → `{goal}` (writes a revision) |
| POST | `/goals/{goal_id}/actions` | `{action: activate|pause|resume|complete|archive|assign|decompose|prioritize|delegate, ...}`; `assign:{assignees}`, `decompose:{subgoals:[{title, objective, owner_type, owner_id, scope_unit_id?}]}`, `prioritize:{priority}`, `delegate:{owner_type, owner_id}` (ownership/permissions preserved: original owner keeps manage rights via a grant) → `{goal, subgoals?}` |
| POST | `/goals/{goal_id}/outcomes` | `{kind, value:{metric, value, unit?, at?}, claim_ids?}` → `{outcome, progress}` |
| GET | `/goals/{goal_id}/loop` | → `{loop}` |
| POST | `/goals/{goal_id}/loop` | `{action: start|pause|resume|run_now|stop}` → `{loop}` (`run_now` enqueues an immediate tick) |

## Questions

`question` = `{question_id, asker:{type, id, name}, goal_id, goal_title, text, kind, trigger, scope_unit_id, scope_unit_name, valid_from, valid_to, uncertainty, motivating_lineage:[{type, id, label}], candidate_domains, policy, budget, budget_spent, status, priority, priority_breakdown:{method:"heuristic", goal_value, uncertainty, impact, missing_evidence, information_gain, cost, weights}, parent_question_id, depth, cooldown_until, result, created_at, updated_at, resolved_at, needs_my_input:bool, routes:[{route_id, holder_id, holder_name, status, sent_at, responded_at, deadline_at}]}`

| Method | Path | Notes |
|---|---|---|
| GET | `/questions?goal_id=&status=&needs_input=1&scope_unit_id=` | → `{items:[question]}` (`needs_input=1`: routed to a holder the caller owns, unanswered) |
| POST | `/questions` | user-initiated: `{text, goal_id?, scope_unit_id?, kind?, candidate_domains?, valid_from?, valid_to?, policy?, budget?}` → `{question}` (deduplicated: 409 with `existing_question_id` when a live duplicate exists) |
| GET | `/questions/{question_id}` | → `{question, responses:[response], claims:[claim(summary)], discoveries:[...], lineage:{...}}`; `response` = `{response_id, holder_id, holder_name, status, content, evidence:[evidence_ref(disclosed)], confidence, freshness_at, received_at}` filtered by visibility |
| POST | `/questions/{question_id}/respond` | a person answering from their own holder: `{content, holder_id?, doc_ids?:[...], no_evidence?:bool}` → `{response}` |
| POST | `/questions/{question_id}/cancel` | asker/goal manager |

## Discoveries, claims, evidence, conflicts

Evidence changes reported by holders (`evidence_event.event`): `revised` (the reference keeps its root and stops
counting; citing claims go `stale` and are re-verified), `retracted` (claims left without active support are
retracted), `deleted` (as `retracted`, and the coordinator's copy of the content is purged: excerpt, title, the content
of responses that cited it, and per tenant policy `deletion.derived_text` — `purge_if_unsupported` (default),
`purge_always` or `retain` — the text of claims retracted because of it), `unavailable` / `restored` (access lost or
regained: the reference stops or resumes counting). Claim kinds `hypothesis` and `prediction`, and claims marked
`causal`, never become `supported`.

`discovery` = `{discovery_id, scope_unit_id, scope_unit_name, visibility, level, kind, title, summary, claim_ids, claims:[claim(summary)], goal_id, goal_title, question_id, status, escalated_to, followup_question_ids, reviews:[{user_id, user_name, action, note, at}], support:{independent_roots, copied_refs, unknown_independence, holders}, freshness_at, is_demo, created_at, updated_at, evidence_health:{status: healthy|degraded|unsupported, active_refs, inactive_refs, claims:{supported, hypothesis, contested, stale, retracted}}}`

`claim` = `{claim_id, scope_unit_id, visibility, text, kind, status, confidence, valid_from, valid_to, version, supersedes_claim_id, superseded_by, goal_id, question_id, created_by:{type, id, name}, support:{independent_roots:n, copied_refs:n, unknown_independence:n, holders:[holder_id], roots:[source_root_id]}, freshness_at, created_at, updated_at}`

`evidence_ref` (disclosed view) = `{ref_id, holder_id, holder_name, source_root_id, root_known, kind, title, disclosed_excerpt, disclosure_level, observed_at, freshness_at, status: active|revised|retracted|unavailable, version, copies_of_same_root:n, role: supports|contradicts|context|superseded, weight, demoted?: reason the gate counts it as context, revised_root_id?}` — only `active` references with role `supports` count as support; `freshness_at` is the observation time

| Method | Path | Notes |
|---|---|---|
| GET | `/discoveries?level=&scope_unit_id=&status=&goal_id=&kind=` | → `{items:[discovery]}` |
| GET | `/discoveries/{id}` | → `{discovery, claims:[claim], evidence:[evidence_ref], conflicts:[conflict], lineage:{nodes, edges}, followups:[question(summary)], revisions:[revision]}` |
| POST | `/discoveries/{id}/review` | `{action: reviewed|accepted|dismissed|escalated|comment, note?}` → `{discovery}` (escalate → creates a copy one level up if the caller leads that unit or the parent) |
| GET | `/claims?scope_unit_id=&status=&goal_id=&q=` | → `{items:[claim]}` |
| GET | `/claims/{id}` | → `{claim, evidence:[evidence_ref], derivations:[derivation], conflicts:[conflict], revisions:[revision], history:[claim], dependencies:{roots:[{source_root_id, ref_count, holders}], unknown:[ref_id], inactive:[ref_id]}, support_notes:[string], freshness:{freshness_at, age_days, stale:bool}}` |
| POST | `/claims/{id}/retract` | creator or unit lead `{reason}` |
| GET | `/evidence/{ref_id}` | → `{evidence_ref, claims:[claim(summary)]}` |
| GET | `/evidence/{ref_id}/raw` | owner or raw grant → `{ref_id, holder_id, doc_id, title, text, observed_at, version}` fetched from the holder over the transport |
| GET | `/conflicts?status=&scope_unit_id=` | → `{items:[conflict]}`; `conflict` = `{conflict_id, claim_a:claim(summary), claim_b:claim(summary), status, summary, investigation:[...], resolution, question_id, created_at, resolved_at}` |
| POST | `/conflicts/{id}/resolve` | lead of the scope `{outcome: a_wins|b_wins|both_valid_scoped|both_retracted|unresolved, note}` |
| POST | `/conflicts/{id}/investigate` | creates a contradiction question → `{conflict, question}` |

## Workspaces (aggregated reads; all visibility-filtered)

| Method | Path | Response |
|---|---|---|
| GET | `/workspace/employee` | `{goals:[goal], questions_needing_input:[question], discoveries:[discovery], holders:[holder], recent_memory:[memory], shared:[grant], loop_states:[loop]}` |
| GET | `/workspace/unit/{unit_id}` | `{unit, level, goals:[goal], discoveries:[discovery], open_questions:[question], conflicts:[conflict], dependencies:[{goal_id, depends_on, status}], progress:[{goal_id, progress}], synthesis:{summary, recurring_problems, constraints, conflicting_findings, opportunities, escalations, material_uncertainties, decisions_needed, computed_at, method:"model"|"none"}, children:[{unit, discoveries:n, open_questions:n, goals:n}], members:[...] (leads only), comparisons:[{unit_id, name, discoveries, supported_claims, open_conflicts, stale_claims}]}` — `level` decides which sections the UI renders |
| GET | `/workspace/executive` | same shape as unit for the executive root plus `strategic:[discovery]`, `material_uncertainties:[claim]`, `decisions:[...]` |
| GET | `/admin/overview` | admin → `{workers:[{worker_id, role, hostname, last_heartbeat_at, heartbeat_age_seconds, busy, stats}], jobs:{queued, leased, done, dead, failed}, failed_jobs:[job], holders:[holder], usage:{today:{calls, tokens, usd}, month:{...}, by_tier:{...}}, budgets:[{goal_id, title, budget, spent}], loops:[loop], deployment:{version, settings, migrations:{current, latest, pending}, transport, uptime_seconds}, metrics:{queue_backlog, failed_routes, question_outcomes:{...}, evidence_freshness:{stale, fresh, median_age_days}}}` |
| GET | `/admin/jobs?status=&kind=` / POST `/admin/jobs/{id}/retry` / POST `/admin/jobs/retry-dead` | admin |
| GET | `/admin/audit?limit=&action=&resource_id=` | admin → `{items:[audit]}` |
| GET | `/admin/usage?since=` | admin → `{items:[usage row], totals}` |
| GET | `/admin/models` | admin → `{tiers:{light:{provider, model}, ...}, embedding:{provider, model}, prices:{...}, policy_tiers:{task: tier}}` |
| PUT | `/admin/models` | admin `{policy_tiers}` (provider keys are environment-only) |
| GET | `/admin/workers` | admin |
| GET | `/notifications?unread=1` / POST `/notifications/read {ids?}` | any user |

## Integrations (connected apps)

Connectors run inside the holder they feed; these endpoints ask the holder to act (an embedded holder in process, an
external one through a signed, short-lived `connector_control` envelope). A personal holder's connectors belong to
its owner only; a unit holder's to the unit's leads or an org admin (an organization install). Source names are shown to
those managers only; the admin view is metadata and counts.

`connector` = `{connector_id, connector_type, display_name, account_label, source_account_id, auth_kind, ownership, mode, status, status_code,
granted_scopes, created_at, updated_at, last_sync_at, last_success_at, health, config, counts:{sources_included, sources_pending, sources_excluded, records}}`
`source` = `{source_id, connector_id, source_type, external_id, name, selection: included|excluded|pending_review, selection_reason, visibility: public|members|private,
exportable, disclosure, default_domain_ids, sensitivity, access_state, members}` (`members` is a count; member ids stay in the holder)

| Method | Path | Notes |
|---|---|---|
| GET | `/integrations/catalog` | → `{items:[{connector_type, display_name, version, status: scaffold\|implemented\|tested-offline\|live-verified, auth_kinds, modes, source_types, scopes:[{scope, required, reason}], capabilities, terms_notes, ownership, enabled_for_tenant, connectable}]}`. Planned connectors (`scaffold`) are listed and cannot be connected. |
| GET | `/holders/{h}/connectors` | manager → `{items:[connector]}` |
| POST | `/holders/{h}/connectors` | manager: `{connector_type, auth?:{kind:'pat', token} \| {kind:'oauth2'}, config?, display_name?}` → 201 `{connector, discovered, next:{action:'select_sources'}}`, or `{next:{action:'redirect', url}}` for OAuth. The token goes to the holder once (sealed for it in transit) and is never echoed, stored by the coordinator or audited. File-based connectors read only inside the holder's `imports/` directory. |
| GET / PATCH / DELETE | `/holders/{h}/connectors/{c}` | get; `{status?: active\|paused, config?}`; `?data=keep\|delete&revoke=1` disconnects (credentials shredded; `delete` purges what it ingested) |
| GET | `/holders/{h}/connectors/{c}/sources?selection=` | → `{items:[source]}` |
| POST | `/holders/{h}/connectors/{c}/sources/discover` | → `{discovered, new, pending_review, included, excluded}` (new sources wait for review; DMs default excluded) |
| PATCH | `/holders/{h}/connectors/{c}/sources` | `{changes:[{source_id, selection?, exportable?, default_domain_ids?, disclosure?, sensitivity?}], existing_records?: keep\|delete}` → `{items:[source]}` |
| POST | `/holders/{h}/connectors/{c}/sources/{s}/delete` | → `{records_deleted}` |
| POST | `/holders/{h}/connectors/{c}/sync` | `{mode: incremental\|backfill}` → 202 `{report, processed, published}` |
| POST | `/holders/{h}/connectors/{c}/webhook` | manager: `{signing_secret?}` (Slack: the app's signing secret) → 201 `{endpoint_id, url, connector_type, secret?}` (a generated secret is shown once) |
| GET | `/holders/{h}/ingest/queue` | manager → `{classes, dead:[{item_id, connector_id, kind, priority_class, attempts, last_error_code, updated_at}]}` |
| GET | `/integrations/oauth/{type}/callback?code&state` | the signed-in user who started the flow; single-use state, PKCE verifier sealed server-side; 302 → `/app/memory/integrations?connected=<id>` |
| POST | `/webhooks/{type}/{endpoint_id}` | public: signature checked on the raw body (401 + audit `webhook.rejected` otherwise), deliveries de-duplicated, reduced to content-free notices routed to the holders that hold the connector (`connector_notice`); Slack `url_verification` → `{challenge}` after verification |
| GET | `/admin/integrations` | admin → `{items:[{connector_id, holder_id, holder_name, scope, connector_type, status, status_code, mode, sources_included, sources_pending, records, last_sync_at, last_success_at, created_at, granted_scopes}], totals:{by_type, by_status}}` |
| GET | `/domains` | → `{taxonomy_version, configured, items:[{domain_id, parent_id, name, path, description, status}], aliases:[{alias, domain_id}]}` (tenant taxonomy; personal domains never appear) |
| PUT | `/admin/domains` | admin `{upsert:[{domain_id, name?, description?}], deprecate:[domain_id], aliases:[{alias, domain_id}]}` → the taxonomy; the first change copies the defaults into the tenant |

Documents: `POST /holders/{h}/documents` multipart also accepts `.jsonl`, `.docx` and `.pdf` (text PDFs; scans are refused with a
reason). CSV rows become `column: value` lines and JSON leaves `path: value` lines.

## Live updates

`GET /api/events/stream?since=<id>` — Server-Sent Events. Each event: `id: <n>`, `event: <kind>`, `data: {tenant_id, kind, ref_type, ref_id, payload, at}`. Kinds:
`goal.updated`, `loop.state`, `question.created|routed|responded|resolved`, `discovery.created|updated`, `claim.committed|revised|stale|retracted`,
`conflict.opened|resolved`, `holder.status`, `document.ingested|revised|retracted`, `job.progress`, `membership.changed`, `grant.changed`,
`org.changed`, `policy.updated`, `notification`. Delivered only to viewers authorized for the event's audience. A `ping`
comment is sent every 15 s.

## Health and metrics (no auth)

`GET /healthz` → `{ok, service, version}`; `GET /readyz` → `{ok, db, transport, worker_heartbeat_age_seconds, migrations_pending}` (503 when not ready);
`GET /metrics` → Prometheus text (`mycelic_model_calls_total`, `mycelic_model_cost_usd_total`, `mycelic_jobs_backlog`, `mycelic_jobs_dead`,
`mycelic_questions_total{outcome}`, `mycelic_routes_failed_total`, `mycelic_claims{status}`, `mycelic_evidence_stale`, `mycelic_worker_heartbeat_age_seconds`,
`mycelic_http_requests_total{method,route,status}`, `mycelic_http_request_seconds`).

## Frontend routes

`/login` `/register` `/invite/:token` `/onboarding` `/app` (employee) `/app/memory` `/app/chat` `/app/goals` `/app/goals/:id`
`/app/questions/:id` `/app/discoveries/:id` `/app/claims/:id` `/app/unit/:unitId` (team/department/subsidiary/region by the unit's level)
`/app/executive` `/app/network` `/app/admin` (`/members` `/hierarchy` `/policies` `/models` `/budgets` `/integrations` `/workers` `/audit` `/deployment`).
