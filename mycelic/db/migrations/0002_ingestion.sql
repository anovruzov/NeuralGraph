-- Mycelic coordination database, schema version 2: universal ingestion (docs/mycelic/INGESTION.md §5.3).
-- Counts, codes, scopes and routing only. No raw content, no source names, no OAuth tokens: credentials live
-- encrypted in the holder that uses them; the only secrets here are webhook signing secrets and PKCE verifiers,
-- both encrypted with the server key.

-- ---------------------------------------------------------------- domain taxonomy (tenant-wide, admin-managed)
-- Personal domains (`personal.<holder>.<slug>`) never appear here: they live only in their holder.
CREATE TABLE domain_taxonomy (
    tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
    domain_id        TEXT NOT NULL,                        -- stable dot path ('legal.contracts'); never reused
    parent_id        TEXT,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    rules            TEXT NOT NULL DEFAULT '{}',           -- keywords / regex / label & container patterns
    status           TEXT NOT NULL DEFAULT 'active',       -- active | deprecated (members kept, no new routing)
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    updated_by       TEXT,
    PRIMARY KEY (tenant_id, domain_id)
);
CREATE TABLE domain_aliases (                            -- legacy flat holder/question domains -> taxonomy ids
    tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id),
    alias      TEXT NOT NULL,                              -- 'deployments'
    domain_id  TEXT NOT NULL,                              -- 'infrastructure.ci-cd'
    PRIMARY KEY (tenant_id, alias)
);
-- tenant_policies key 'taxonomy_version' (int) increments on every taxonomy change; holders reload on mismatch.

-- ---------------------------------------------------------------- connector installs
-- One authorization of an application: a person connecting their own account (scope 'personal', feeding their user
-- holder) or an admin / unit lead installing an organization-wide app (scope 'organization', feeding the unit holders
-- each of its sources is explicitly mapped to). Each holder it feeds gets its own connector_registry row and its own
-- encrypted copy of the credential, held by that holder.
CREATE TABLE connector_installs (
    install_id        TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
    connector_type    TEXT NOT NULL,                       -- 'github' | 'slack' | 'local_export' | ...
    scope             TEXT NOT NULL,                       -- personal | organization
    installed_by      TEXT NOT NULL REFERENCES users(user_id),
    unit_id           TEXT REFERENCES org_units(unit_id),  -- organization installs: the unit whose authority installed it
    external_account_id TEXT,                              -- provider workspace / org / installation id (not a name)
    status            TEXT NOT NULL DEFAULT 'active',      -- active | paused | revoked
    granted_scopes    TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    revoked_at        TEXT
);
CREATE INDEX idx_connector_installs_tenant ON connector_installs(tenant_id, connector_type, status);

-- ---------------------------------------------------------------- connector registry (metadata visible to owner/admin)
CREATE TABLE connector_registry (
    connector_id       TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(tenant_id),
    holder_id          TEXT NOT NULL REFERENCES holders(holder_id),
    install_id         TEXT REFERENCES connector_installs(install_id),
    scope              TEXT NOT NULL DEFAULT 'personal',   -- personal | organization (copied from the install)
    owner_user_id      TEXT,
    connector_type     TEXT NOT NULL,
    mode               TEXT NOT NULL,                      -- pull | webhook | pull+webhook | export
    status             TEXT NOT NULL,                      -- connecting | active | paused | error | revoked | quota_exceeded
    status_code        TEXT NOT NULL DEFAULT '',
    granted_scopes     TEXT NOT NULL DEFAULT '[]',
    sources_included   INTEGER NOT NULL DEFAULT 0,
    sources_pending    INTEGER NOT NULL DEFAULT 0,
    records            INTEGER NOT NULL DEFAULT 0,
    last_sync_at       TEXT,
    last_success_at    TEXT,
    lag_seconds        REAL,
    queue              TEXT NOT NULL DEFAULT '{}',         -- {live, user, backfill, reindex, dead}
    health             TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX idx_connector_registry_holder ON connector_registry(holder_id);
CREATE INDEX idx_connector_registry_tenant ON connector_registry(tenant_id, connector_type, status);

-- ---------------------------------------------------------------- OAuth flows
CREATE TABLE oauth_states (
    state_hash        TEXT PRIMARY KEY,                    -- sha256(state); the state itself only travels in the redirect
    tenant_id         TEXT NOT NULL,
    holder_id         TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    connector_type    TEXT NOT NULL,
    install_scope     TEXT NOT NULL DEFAULT 'personal',
    pkce_verifier_ct  BLOB NOT NULL,                       -- encrypted with the server key
    redirect_uri      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,                       -- +10 min; single use
    used_at           TEXT
);

-- ---------------------------------------------------------------- webhooks (notify, then the holder fetches)
CREATE TABLE webhook_endpoints (
    endpoint_id         TEXT PRIMARY KEY,                  -- whk_<hex>, part of the public URL
    tenant_id           TEXT NOT NULL REFERENCES tenants(tenant_id),
    connector_type      TEXT NOT NULL,
    scope               TEXT NOT NULL,                     -- app (one per provider app) | connector (per-connection secret)
    external_account_id TEXT,
    secret_kid          TEXT NOT NULL,
    secret_ct           BLOB NOT NULL,                     -- AES-GCM(server key) signing secret
    status              TEXT NOT NULL DEFAULT 'active',    -- active | disabled
    created_at          TEXT NOT NULL,
    rotated_at          TEXT
);
CREATE TABLE webhook_routes (
    endpoint_id         TEXT NOT NULL REFERENCES webhook_endpoints(endpoint_id),
    external_account_id TEXT NOT NULL,
    source_external_id  TEXT NOT NULL,                     -- repo id / channel id ('*' = every source of the account)
    holder_id           TEXT NOT NULL REFERENCES holders(holder_id),
    connector_id        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, external_account_id, source_external_id, holder_id)
);
CREATE TABLE webhook_deliveries (                        -- provider retries / replays; pruned after 14 days
    endpoint_id     TEXT NOT NULL,
    delivery_id     TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    status          TEXT NOT NULL,                         -- routed | ignored | rejected
    routed_holders  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (endpoint_id, delivery_id)
);

-- ---------------------------------------------------------------- shards
CREATE TABLE shards (
    shard_id           TEXT PRIMARY KEY,                   -- globally unique; never reused
    tenant_id          TEXT NOT NULL REFERENCES tenants(tenant_id),
    holder_id          TEXT NOT NULL REFERENCES holders(holder_id),
    ordinal            INTEGER NOT NULL,
    partition          TEXT NOT NULL DEFAULT '{}',          -- {domain_ids: [...], time_from, time_to}; {} = default (s0)
    placement          TEXT NOT NULL,                      -- 'embedded:<holder_id>/<file>' | 'external'
    status             TEXT NOT NULL,                      -- provisioning | active | draining | readonly | retired
    schema_version     INTEGER NOT NULL,
    writer_id          TEXT,
    writer_lease_until TEXT,
    stats              TEXT NOT NULL DEFAULT '{}',          -- ShardStats: counts, bytes, p95s; no content
    health             TEXT NOT NULL DEFAULT 'ok',          -- ok | hot | degraded | offline
    last_backup_at     TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (holder_id, ordinal)
);
CREATE INDEX idx_shards_tenant ON shards(tenant_id, holder_id, status);

CREATE TABLE shard_migrations (
    migration_id   TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    holder_id      TEXT NOT NULL,
    kind           TEXT NOT NULL,                          -- split | merge | move | rebalance
    from_shard     TEXT NOT NULL,
    to_shard       TEXT NOT NULL,
    partition      TEXT NOT NULL,
    state          TEXT NOT NULL,                          -- planned | provisioning | copying | catching_up | cutover | cleanup | done | aborted | failed
    checkpoint     TEXT NOT NULL DEFAULT '{}',
    requested_by   TEXT NOT NULL,
    started_at     TEXT,
    cutover_at     TEXT,
    finished_at    TEXT,
    error_code     TEXT
);

-- ---------------------------------------------------------------- ingestion metrics (rollups, no content)
CREATE TABLE ingest_metrics (
    tenant_id      TEXT NOT NULL,
    holder_id      TEXT NOT NULL,
    connector_id   TEXT NOT NULL,                          -- '' for holder-level stages
    stage          TEXT NOT NULL,                          -- fetch | admit | normalize | redact | dedupe | classify | route | write | link | publish
    bucket         TEXT NOT NULL,                          -- minute bucket 'YYYY-MM-DDTHH:MM'
    counts         TEXT NOT NULL DEFAULT '{}',             -- {in, ok, duplicate, excluded, error, dead}
    latency_p50_ms REAL,
    latency_p95_ms REAL,
    lag_seconds    REAL,
    PRIMARY KEY (holder_id, connector_id, stage, bucket)
);

-- ---------------------------------------------------------------- deletion ledger (restore replay; content-free)
CREATE TABLE deletion_ledger (
    tenant_id        TEXT NOT NULL,
    holder_id        TEXT NOT NULL,
    record_key_hash  TEXT NOT NULL,                        -- sha256(record_key): unlinkable outside the holder
    reason           TEXT NOT NULL,
    requested_at     TEXT NOT NULL,
    purged_at        TEXT,
    ref_ids          TEXT NOT NULL DEFAULT '[]',
    expires_at       TEXT NOT NULL,
    PRIMARY KEY (holder_id, record_key_hash)
);

-- tenant_policies keys (JSON values, no DDL): ingest_connectors, ingest_quota, ingest_model_budget, deletion,
-- retention_defaults, allow_external_models_for_connectors.
