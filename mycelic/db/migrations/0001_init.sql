-- Mycelic coordination database, schema version 1.
-- Holds identities, organization, goals, inquiry artifacts, lineage objects, the durable job queue,
-- the event outbox, audit and usage. Raw evidence never lives here (see docs/mycelic/DECISIONS.md D2).
-- All timestamps are ISO-8601 UTC strings so string comparison orders them.

-- ---------------------------------------------------------------- tenancy & identity
CREATE TABLE tenants (
    tenant_id   TEXT PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE users (
    user_id       TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
    email         TEXT NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT,
    status        TEXT NOT NULL DEFAULT 'active',      -- active | disabled
    is_demo       INTEGER NOT NULL DEFAULT 0,
    settings      TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (tenant_id, email)
);

CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    kind         TEXT NOT NULL DEFAULT 'web',           -- web | api | demo
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at   TEXT,
    user_agent   TEXT,
    ip           TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE invitations (
    invitation_id    TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
    email            TEXT NOT NULL,
    token_hash       TEXT NOT NULL UNIQUE,
    role             TEXT NOT NULL,
    unit_id          TEXT,
    invited_by       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending | accepted | expired | revoked
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    accepted_at      TEXT,
    accepted_user_id TEXT
);
CREATE INDEX idx_invitations_tenant ON invitations(tenant_id, status);

CREATE TABLE api_keys (
    key_id         TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id),
    principal_type TEXT NOT NULL,                       -- user | holder | service
    principal_id   TEXT NOT NULL,
    key_hash       TEXT NOT NULL UNIQUE,
    label          TEXT NOT NULL DEFAULT '',
    scopes         TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    expires_at     TEXT,
    revoked_at     TEXT,
    last_used_at   TEXT
);
CREATE INDEX idx_api_keys_principal ON api_keys(principal_type, principal_id);

-- ---------------------------------------------------------------- organization
-- unit types (configurable per tenant, any level may be omitted):
--   executive | region | subsidiary | department | team | project
-- 'project' units are cross-functional scopes; project_scopes lists the units they span.
CREATE TABLE org_units (
    unit_id     TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(tenant_id),
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    parent_id   TEXT REFERENCES org_units(unit_id),
    path        TEXT NOT NULL,                          -- '/root_id/child_id/...' materialized ancestry
    depth       INTEGER NOT NULL DEFAULT 0,
    is_demo     INTEGER NOT NULL DEFAULT 0,
    settings    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX idx_org_units_tenant ON org_units(tenant_id, type);
CREATE INDEX idx_org_units_parent ON org_units(parent_id);
CREATE INDEX idx_org_units_path ON org_units(path);

CREATE TABLE project_scopes (
    project_id TEXT NOT NULL REFERENCES org_units(unit_id),
    unit_id    TEXT NOT NULL REFERENCES org_units(unit_id),
    PRIMARY KEY (project_id, unit_id)
);

-- roles: employee | team_lead | department_lead | subsidiary_lead | regional_lead | executive | org_admin
CREATE TABLE memberships (
    membership_id TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
    user_id       TEXT NOT NULL REFERENCES users(user_id),
    unit_id       TEXT NOT NULL REFERENCES org_units(unit_id),
    role          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',       -- active | revoked
    granted_by    TEXT,
    created_at    TEXT NOT NULL,
    revoked_at    TEXT,
    UNIQUE (user_id, unit_id, role)
);
CREATE INDEX idx_memberships_user ON memberships(user_id, status);
CREATE INDEX idx_memberships_unit ON memberships(unit_id, status);

-- explicit access grants (private evidence, or a specific artifact) — distinct from membership
CREATE TABLE grants (
    grant_id       TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id),
    grantor_id     TEXT NOT NULL,
    grantee_type   TEXT NOT NULL,                       -- user | unit
    grantee_id     TEXT NOT NULL,
    resource_type  TEXT NOT NULL,                       -- holder | claim | discovery | goal | evidence_ref
    resource_id    TEXT NOT NULL,
    level          TEXT NOT NULL DEFAULT 'read',        -- read | artifact | raw
    reason         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',      -- active | revoked | expired
    created_at     TEXT NOT NULL,
    expires_at     TEXT,
    revoked_at     TEXT
);
CREATE INDEX idx_grants_grantee ON grants(grantee_type, grantee_id, status);
CREATE INDEX idx_grants_resource ON grants(resource_type, resource_id, status);

-- evidence holders: one per employee (or per unit) — the store itself lives with the holder
CREATE TABLE holders (
    holder_id         TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    owner_type        TEXT NOT NULL,                    -- user | unit
    owner_id          TEXT NOT NULL,
    name              TEXT NOT NULL,
    key_hash          TEXT NOT NULL,                    -- authenticates holder -> core
    route_key         TEXT NOT NULL,                    -- signs core -> holder envelopes (HMAC)
    status            TEXT NOT NULL DEFAULT 'offline',  -- online | offline | revoked
    mode              TEXT NOT NULL DEFAULT 'external', -- external | embedded
    domains           TEXT NOT NULL DEFAULT '[]',       -- evidence domains it can answer about
    export_policy     TEXT NOT NULL DEFAULT '{}',
    stats             TEXT NOT NULL DEFAULT '{}',
    last_heartbeat_at TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_holders_owner ON holders(tenant_id, owner_type, owner_id);

CREATE TABLE tenant_policies (
    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id),
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,                           -- JSON
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     TEXT,
    at            TEXT NOT NULL,
    actor_type    TEXT NOT NULL,                        -- user | holder | worker | system | agent
    actor_id      TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    outcome       TEXT NOT NULL DEFAULT 'ok',           -- ok | allow | deny | error
    detail        TEXT NOT NULL DEFAULT '{}',
    request_id    TEXT
);
CREATE INDEX idx_audit_tenant_at ON audit_log(tenant_id, at);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);

-- ---------------------------------------------------------------- goals
CREATE TABLE goals (
    goal_id            TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(tenant_id),
    owner_type         TEXT NOT NULL,                   -- user | unit
    owner_id           TEXT NOT NULL,
    scope_unit_id      TEXT REFERENCES org_units(unit_id),
    parent_goal_id     TEXT REFERENCES goals(goal_id),
    title              TEXT NOT NULL,
    objective          TEXT NOT NULL,
    success_criteria   TEXT NOT NULL DEFAULT '[]',
    baseline           TEXT,                            -- JSON or NULL when unavailable
    measurement_source TEXT NOT NULL DEFAULT '{}',
    deadline           TEXT,
    priority           INTEGER NOT NULL DEFAULT 3,      -- 1 (highest) .. 5
    status             TEXT NOT NULL DEFAULT 'draft',   -- draft | active | paused | completed | archived | blocked
    permitted_actions  TEXT NOT NULL DEFAULT '[]',
    budget             TEXT NOT NULL DEFAULT '{}',      -- {tokens, usd, questions, followup_depth, model_tier_max}
    budget_spent       TEXT NOT NULL DEFAULT '{}',
    dependencies       TEXT NOT NULL DEFAULT '[]',      -- goal ids
    progress           TEXT NOT NULL DEFAULT '{"known": false}',
    assignees          TEXT NOT NULL DEFAULT '[]',      -- [{type, id}]
    is_demo            INTEGER NOT NULL DEFAULT 0,
    version            INTEGER NOT NULL DEFAULT 1,
    created_by         TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT,
    archived_at        TEXT
);
CREATE INDEX idx_goals_tenant_status ON goals(tenant_id, status);
CREATE INDEX idx_goals_scope ON goals(scope_unit_id);
CREATE INDEX idx_goals_parent ON goals(parent_goal_id);

CREATE TABLE goal_outcomes (
    outcome_id   TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),
    goal_id      TEXT NOT NULL REFERENCES goals(goal_id),
    kind         TEXT NOT NULL,                         -- measurement | milestone | action | note
    value        TEXT NOT NULL DEFAULT '{}',            -- {metric, value, unit, at}
    claim_ids    TEXT NOT NULL DEFAULT '[]',
    recorded_by  TEXT,
    recorded_at  TEXT NOT NULL
);
CREATE INDEX idx_goal_outcomes_goal ON goal_outcomes(goal_id);

CREATE TABLE goal_loops (
    goal_id           TEXT PRIMARY KEY REFERENCES goals(goal_id),
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    desired           TEXT NOT NULL DEFAULT 'active',   -- active | paused | stopped
    state             TEXT NOT NULL DEFAULT 'waiting',  -- active | waiting | paused | budget_exhausted | blocked | failed | completed | stopped
    explanation       TEXT NOT NULL DEFAULT '',
    config            TEXT NOT NULL DEFAULT '{}',       -- {check_interval_seconds, max_concurrent_questions, cooldown_seconds, max_followup_depth, question_timeout_seconds}
    last_run_at       TEXT,
    next_check_at     TEXT,
    last_worker_id    TEXT,
    last_heartbeat_at TEXT,
    run_count         INTEGER NOT NULL DEFAULT 0,
    stats             TEXT NOT NULL DEFAULT '{}',
    updated_at        TEXT NOT NULL
);

-- ---------------------------------------------------------------- inquiry
CREATE TABLE questions (
    question_id         TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(tenant_id),
    asker_type          TEXT NOT NULL,                  -- user | agent | loop
    asker_id            TEXT NOT NULL,
    goal_id             TEXT REFERENCES goals(goal_id),
    text                TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'gap',    -- gap | verification | contradiction | relationship | hypothesis | prediction
    trigger             TEXT NOT NULL DEFAULT '{}',     -- {kind, ref_type, ref_id, note}
    scope_unit_id       TEXT REFERENCES org_units(unit_id),
    valid_from          TEXT,
    valid_to            TEXT,
    uncertainty         TEXT NOT NULL DEFAULT '{}',     -- {prior, note}
    motivating_lineage  TEXT NOT NULL DEFAULT '[]',     -- [{type: claim|evidence_ref|question|conflict, id}]
    candidate_domains   TEXT NOT NULL DEFAULT '[]',
    policy              TEXT NOT NULL DEFAULT '{}',     -- {visibility, disclosure, blind_verification, min_independent_roots}
    budget              TEXT NOT NULL DEFAULT '{}',     -- {tokens, usd, holders, timeout_seconds}
    budget_spent        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'draft',  -- draft | routed | collecting | evaluating | verifying | committed | retained_uncertain | expired | failed | cancelled
    priority            REAL NOT NULL DEFAULT 0,
    priority_breakdown  TEXT NOT NULL DEFAULT '{}',     -- heuristic estimates, labelled as such
    parent_question_id  TEXT REFERENCES questions(question_id),
    depth               INTEGER NOT NULL DEFAULT 0,
    dedupe_key          TEXT,
    cooldown_until      TEXT,
    result              TEXT NOT NULL DEFAULT '{}',     -- {claim_ids, discovery_ids, summary, outcome}
    is_demo             INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    resolved_at         TEXT
);
CREATE UNIQUE INDEX idx_questions_dedupe ON questions(tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX idx_questions_goal ON questions(goal_id, status);
CREATE INDEX idx_questions_tenant_status ON questions(tenant_id, status);

CREATE TABLE question_routes (
    route_id      TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
    question_id   TEXT NOT NULL REFERENCES questions(question_id),
    holder_id     TEXT NOT NULL REFERENCES holders(holder_id),
    status        TEXT NOT NULL DEFAULT 'pending',      -- pending | delivered | answered | declined | timeout | failed | revoked
    msg_id        TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    sent_at       TEXT,
    delivered_at  TEXT,
    responded_at  TEXT,
    deadline_at   TEXT,
    error         TEXT,
    UNIQUE (question_id, holder_id)
);
CREATE INDEX idx_routes_holder ON question_routes(holder_id, status);

CREATE TABLE responses (
    response_id      TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
    question_id      TEXT NOT NULL REFERENCES questions(question_id),
    holder_id        TEXT NOT NULL REFERENCES holders(holder_id),
    route_id         TEXT,
    status           TEXT NOT NULL,                     -- answered | no_evidence | declined | error
    content          TEXT NOT NULL DEFAULT '',          -- permitted disclosure only
    evidence_ref_ids TEXT NOT NULL DEFAULT '[]',
    provenance       TEXT NOT NULL DEFAULT '{}',        -- {retrieval_operator, holder_version, channels, policy}
    confidence       REAL,
    freshness_at     TEXT,
    msg_id           TEXT NOT NULL UNIQUE,              -- transport idempotency
    received_at      TEXT NOT NULL
);
CREATE INDEX idx_responses_question ON responses(question_id);

CREATE TABLE question_runs (
    question_id   TEXT PRIMARY KEY REFERENCES questions(question_id),
    tenant_id     TEXT NOT NULL,
    step          TEXT NOT NULL,                        -- last completed step
    state         TEXT NOT NULL DEFAULT '{}',           -- checkpoint payload
    attempts      INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------- knowledge & lineage
CREATE TABLE evidence_refs (
    ref_id            TEXT PRIMARY KEY,                 -- opaque outside the holder
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    holder_id         TEXT NOT NULL REFERENCES holders(holder_id),
    source_root_id    TEXT,                             -- content fingerprint of the original source
    root_known        INTEGER NOT NULL DEFAULT 1,
    kind              TEXT NOT NULL DEFAULT 'document', -- document | note | message | conversation | record
    title             TEXT NOT NULL DEFAULT '',
    disclosed_excerpt TEXT NOT NULL DEFAULT '',         -- policy-approved disclosure
    disclosure_level  TEXT NOT NULL DEFAULT 'excerpt',  -- none | summary | excerpt
    observed_at       TEXT,
    freshness_at      TEXT,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | revised | retracted | unavailable
    version           INTEGER NOT NULL DEFAULT 1,
    meta              TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_evidence_root ON evidence_refs(tenant_id, source_root_id);
CREATE INDEX idx_evidence_holder ON evidence_refs(holder_id, status);

CREATE TABLE claims (
    claim_id            TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants(tenant_id),
    scope_unit_id       TEXT REFERENCES org_units(unit_id),
    visibility          TEXT NOT NULL DEFAULT 'unit',   -- private | unit | org
    owner_user_id       TEXT,                           -- set when visibility = private
    text                TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'finding',-- finding | hypothesis | prediction | relationship | measurement
    status              TEXT NOT NULL DEFAULT 'hypothesis', -- hypothesis | supported | contested | stale | retracted
    confidence          REAL NOT NULL DEFAULT 0.5,
    valid_from          TEXT,
    valid_to            TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    supersedes_claim_id TEXT REFERENCES claims(claim_id),
    superseded_by       TEXT,
    goal_id             TEXT REFERENCES goals(goal_id),
    question_id         TEXT REFERENCES questions(question_id),
    created_by_type     TEXT NOT NULL,                  -- user | agent | loop
    created_by_id       TEXT NOT NULL,
    support             TEXT NOT NULL DEFAULT '{}',     -- {independent_roots, copied_refs, unknown_independence, holders, contradicting}
    freshness_at        TEXT,
    is_demo             INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_claims_tenant_status ON claims(tenant_id, status);
CREATE INDEX idx_claims_scope ON claims(scope_unit_id, visibility);
CREATE INDEX idx_claims_goal ON claims(goal_id);
CREATE INDEX idx_claims_question ON claims(question_id);

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    ref_id   TEXT NOT NULL REFERENCES evidence_refs(ref_id),
    role     TEXT NOT NULL DEFAULT 'supports',           -- supports | contradicts | context
    weight   REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (claim_id, ref_id)
);
CREATE INDEX idx_claim_evidence_ref ON claim_evidence(ref_id);

CREATE TABLE derivations (
    derivation_id    TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
    claim_id         TEXT NOT NULL REFERENCES claims(claim_id),
    operator         TEXT NOT NULL,                     -- retrieve | synthesize | verify | revise | aggregate | human
    input_claim_ids  TEXT NOT NULL DEFAULT '[]',
    input_ref_ids    TEXT NOT NULL DEFAULT '[]',
    response_ids     TEXT NOT NULL DEFAULT '[]',
    model            TEXT,
    contributor_type TEXT NOT NULL,                     -- user | agent | holder | loop
    contributor_id   TEXT NOT NULL,
    rationale        TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_derivations_claim ON derivations(claim_id);

CREATE TABLE conflicts (
    conflict_id    TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(tenant_id),
    claim_a_id     TEXT NOT NULL REFERENCES claims(claim_id),
    claim_b_id     TEXT NOT NULL REFERENCES claims(claim_id),
    status         TEXT NOT NULL DEFAULT 'open',        -- open | investigating | resolved
    summary        TEXT NOT NULL DEFAULT '',
    investigation  TEXT NOT NULL DEFAULT '[]',          -- [{at, actor, step, note, question_id}]
    resolution     TEXT,                                -- {outcome: a_wins|b_wins|both_valid_scoped|both_retracted|unresolved, note}
    question_id    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    resolved_at    TEXT,
    UNIQUE (claim_a_id, claim_b_id)
);
CREATE INDEX idx_conflicts_tenant_status ON conflicts(tenant_id, status);

CREATE TABLE revisions (
    revision_id  TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),
    object_type  TEXT NOT NULL,                         -- claim | evidence_ref | discovery | goal | question | conflict
    object_id    TEXT NOT NULL,
    version      INTEGER NOT NULL,
    actor_type   TEXT NOT NULL,
    actor_id     TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    before       TEXT NOT NULL DEFAULT '{}',
    after        TEXT NOT NULL DEFAULT '{}',
    at           TEXT NOT NULL
);
CREATE INDEX idx_revisions_object ON revisions(object_type, object_id, version);

CREATE TABLE discoveries (
    discovery_id      TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    scope_unit_id     TEXT REFERENCES org_units(unit_id),
    visibility        TEXT NOT NULL DEFAULT 'unit',
    level             TEXT NOT NULL DEFAULT 'team',     -- employee | team | department | subsidiary | region | executive
    kind              TEXT NOT NULL DEFAULT 'finding',  -- finding | contradiction | relationship | hypothesis | prediction | followup
    title             TEXT NOT NULL,
    summary           TEXT NOT NULL,
    claim_ids         TEXT NOT NULL DEFAULT '[]',
    goal_id           TEXT REFERENCES goals(goal_id),
    question_id       TEXT REFERENCES questions(question_id),
    status            TEXT NOT NULL DEFAULT 'new',      -- new | reviewed | accepted | dismissed | escalated
    escalated_to      TEXT,
    followup_question_ids TEXT NOT NULL DEFAULT '[]',
    is_demo           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_discoveries_tenant ON discoveries(tenant_id, status);
CREATE INDEX idx_discoveries_scope ON discoveries(scope_unit_id, level);
CREATE INDEX idx_discoveries_goal ON discoveries(goal_id);

CREATE TABLE discovery_reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_id TEXT NOT NULL REFERENCES discoveries(discovery_id),
    user_id      TEXT NOT NULL,
    action       TEXT NOT NULL,                         -- reviewed | accepted | dismissed | escalated | comment
    note         TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL
);

-- ---------------------------------------------------------------- durable execution
CREATE TABLE jobs (
    job_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       TEXT,
    kind            TEXT NOT NULL,
    ref_type        TEXT,
    ref_id          TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'queued',     -- queued | leased | done | failed | dead | cancelled
    priority        INTEGER NOT NULL DEFAULT 5,         -- 1 (highest) .. 9
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    available_at    TEXT NOT NULL,
    leased_until    TEXT,
    worker_id       TEXT,
    last_error      TEXT,
    payload         TEXT NOT NULL DEFAULT '{}',
    result          TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    finished_at     TEXT
);
CREATE INDEX idx_jobs_status_avail ON jobs(status, priority, available_at);
CREATE INDEX idx_jobs_ref ON jobs(ref_type, ref_id);
CREATE INDEX idx_jobs_tenant_kind ON jobs(tenant_id, kind, status);

CREATE TABLE job_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(job_id),
    worker_id   TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT,                                   -- ok | error | lost_lease | cancelled
    error       TEXT
);
CREATE INDEX idx_job_attempts_job ON job_attempts(job_id);

CREATE TABLE workers (
    worker_id         TEXT PRIMARY KEY,
    role              TEXT NOT NULL DEFAULT 'discovery',-- discovery | api | holder
    hostname          TEXT,
    pid               INTEGER,
    version           TEXT,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    busy              INTEGER NOT NULL DEFAULT 0,
    stats             TEXT NOT NULL DEFAULT '{}'
);

-- transactional outbox for live updates; the API tails it and fans out over SSE after authz
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ref_type   TEXT,
    ref_id     TEXT,
    payload    TEXT NOT NULL DEFAULT '{}',
    audience   TEXT NOT NULL DEFAULT '{}',              -- {unit_ids: [], user_ids: [], visibility}
    at         TEXT NOT NULL
);
CREATE INDEX idx_events_tenant ON events(tenant_id, id);

-- SQLite transport (docs D4): outbox with bounded retention; consumers keep a cursor
CREATE TABLE transport_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject      TEXT NOT NULL,
    msg_id       TEXT NOT NULL UNIQUE,
    payload      TEXT NOT NULL,
    headers      TEXT NOT NULL DEFAULT '{}',
    published_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX idx_transport_subject ON transport_messages(subject, id);

CREATE TABLE transport_cursors (
    consumer      TEXT NOT NULL,
    subject       TEXT NOT NULL,                        -- exact subject or prefix ending in '.>'
    last_id       INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (consumer, subject)
);

CREATE TABLE transport_processed (
    consumer     TEXT NOT NULL,
    msg_id       TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (consumer, msg_id)
);

CREATE TABLE notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    user_id   TEXT NOT NULL REFERENCES users(user_id),
    kind      TEXT NOT NULL,
    title     TEXT NOT NULL,
    body      TEXT NOT NULL DEFAULT '',
    ref_type  TEXT,
    ref_id    TEXT,
    at        TEXT NOT NULL,
    read_at   TEXT
);
CREATE INDEX idx_notifications_user ON notifications(user_id, read_at);

-- ---------------------------------------------------------------- models, usage, observability
CREATE TABLE model_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id     TEXT,
    at            TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    tier          TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    goal_id       TEXT,
    question_id   TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    ok            INTEGER NOT NULL DEFAULT 1,
    error         TEXT
);
CREATE INDEX idx_model_usage_tenant_at ON model_usage(tenant_id, at);
CREATE INDEX idx_model_usage_goal ON model_usage(goal_id);

CREATE TABLE metrics_samples (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    name   TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '{}',
    value  REAL NOT NULL
);
CREATE INDEX idx_metrics_name_at ON metrics_samples(name, at);

CREATE TABLE error_reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    service    TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'error',
    message    TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    request_id TEXT,
    tenant_id  TEXT
);

-- agent chat transcripts are metadata only (who asked what, which citations) — content stays short and
-- is scoped to the user; raw evidence never enters this table
CREATE TABLE agent_chats (
    chat_id    TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id),
    user_id    TEXT NOT NULL REFERENCES users(user_id),
    agent_type TEXT NOT NULL,                           -- user | unit
    agent_id   TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE agent_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL REFERENCES agent_chats(chat_id),
    role       TEXT NOT NULL,                           -- user | assistant
    content    TEXT NOT NULL,
    citations  TEXT NOT NULL DEFAULT '[]',              -- [{type: claim|evidence_ref|discovery, id}]
    usage      TEXT NOT NULL DEFAULT '{}',
    at         TEXT NOT NULL
);
CREATE INDEX idx_agent_messages_chat ON agent_messages(chat_id, id);
