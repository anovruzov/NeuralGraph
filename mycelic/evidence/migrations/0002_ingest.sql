-- Holder schema v2: multi-source ingestion (docs/mycelic/INGESTION.md §5.2, adapted to the 2026-10-08 product decisions).
-- Raw content lives only in this file. This migration only ADDS tables, indexes, triggers and columns, so a v1 holder
-- file keeps working after it runs. One store per holder; this file is shard s0 (control + data) until a split.
--
-- Adaptations to §5.2:
--   * ACLs follow the product decision: permissions = {visibility: public|members|private, member_ids | membership_ref}.
--     connector_sources and ingest_records carry it; acl_memberships resolves membership_ref at use time.
--   * connectors.ownership: 'org' connectors feed unit holders, 'personal' connectors feed user holders.
--   * domains.scope: 'tenant' (copy of the tenant taxonomy) or 'personal' (personal.<holder>.<slug>, never published).
--   * ingest_records.object_key: the cross-holder canonical identity of the provider object (same object reached through
--     an org connector and a personal connector keeps one identity and one root; each holder keeps its own row).
--   * ingest_outbox: evidence_event / ingest_result envelopes written in the effect transaction, published afterwards.
--   * ingest_stage_metrics: per-stage counters and latency (no content).
--   * domain_membership_history is append-only (enforced by triggers).

-- ======================================================================= [C] connectors, credentials, sources
CREATE TABLE connectors (
    connector_id      TEXT PRIMARY KEY,                    -- con_<hex>
    connector_type    TEXT NOT NULL,                       -- registry name, e.g. local_export
    source_app        TEXT NOT NULL,                       -- record identity family (connector type or configured app label)
    manifest_version  TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    source_account_id TEXT NOT NULL,                       -- identity namespace in which object ids are unique
    auth_account_id   TEXT NOT NULL DEFAULT '',            -- authenticated provider identity ('' for exports)
    account_label     TEXT NOT NULL DEFAULT '',            -- owner-visible only
    auth_kind         TEXT NOT NULL DEFAULT 'none',        -- pat | oauth2 | github_app_user | none
    granted_scopes    TEXT NOT NULL DEFAULT '[]',
    ownership         TEXT NOT NULL DEFAULT 'personal',    -- org (feeds a unit holder) | personal (feeds a user holder)
    mode              TEXT NOT NULL DEFAULT 'pull',        -- pull | webhook | pull+webhook | export
    status            TEXT NOT NULL DEFAULT 'pending',     -- pending | active | paused | auth_expired | revoked | quota_exceeded
                                                           -- | error | disconnecting | disconnected
    status_code       TEXT NOT NULL DEFAULT '',            -- ConnectorError.code; never provider prose
    config            TEXT NOT NULL DEFAULT '{}',          -- non-secret configuration
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_sync_at      TEXT,
    last_success_at   TEXT,
    health            TEXT NOT NULL DEFAULT '{}',
    UNIQUE (connector_type, source_app, source_account_id, auth_account_id)
);

CREATE TABLE connector_credentials (                     -- envelope-encrypted (§10.1); deleting the row shreds the DEK
    connector_id   TEXT PRIMARY KEY REFERENCES connectors(connector_id) ON DELETE CASCADE,
    kid            TEXT NOT NULL,                          -- KEK id used to wrap the DEK
    wrapped_dek    BLOB NOT NULL,                          -- 'v1' || nonce(12) || AESGCM(KEK, DEK, aad|wrap)
    ciphertext     BLOB NOT NULL,                          -- 'v1' || nonce(12) || AESGCM(DEK, json(credentials), aad)
    aad            TEXT NOT NULL,                          -- 'mycelic/cred/v1|<tenant>|<holder>|<connector>'
    expires_at     TEXT,
    refresh_after  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE connector_sources (
    source_id           TEXT PRIMARY KEY,                  -- src_<hex>
    connector_id        TEXT NOT NULL REFERENCES connectors(connector_id),
    source_type         TEXT NOT NULL,                     -- channel | dm | repo | mailbox | folder | export_file ...
    external_id         TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT '',          -- owner-visible only; never sent to coord or models
    parent_external_id  TEXT,
    selection           TEXT NOT NULL DEFAULT 'pending_review', -- included | excluded | pending_review (never auto-ingested)
    selection_reason    TEXT NOT NULL DEFAULT '',
    visibility          TEXT NOT NULL DEFAULT 'private',   -- public | members | private (source ACL)
    member_ids          TEXT NOT NULL DEFAULT '[]',        -- principal ids allowed to read (holder-local only)
    membership_ref      TEXT,                              -- resolved through acl_memberships at use time
    exportable          INTEGER NOT NULL DEFAULT 0,        -- private sources are not exportable until the owner opts in
    disclosure          TEXT,                              -- none | summary | excerpt; NULL = holder default
    default_domain_ids  TEXT NOT NULL DEFAULT '[]',        -- source mapping rule (§6.3 stage 1)
    sensitivity         TEXT NOT NULL DEFAULT 'internal',
    retention_policy    TEXT NOT NULL DEFAULT 'default',
    allow_external_models INTEGER,                         -- NULL = tenant default; 0 = no model calls over its content
    access_state        TEXT NOT NULL DEFAULT 'ok',        -- ok | lost | revoked
    access_lost_at      TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}',
    discovered_at       TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_synced_at      TEXT,
    UNIQUE (connector_id, source_type, external_id)
);
CREATE INDEX idx_sources_selection ON connector_sources(connector_id, selection);

CREATE TABLE acl_memberships (                           -- membership_ref -> principal ids (e.g. a channel's members)
    membership_ref  TEXT NOT NULL,
    member_id       TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (membership_ref, member_id)
);

CREATE TABLE source_exclusions (                         -- rules beyond per-source selection
    exclusion_id  TEXT PRIMARY KEY,
    connector_id  TEXT,                                    -- NULL = every connector of this holder
    scope         TEXT NOT NULL,                           -- source_type | source | conversation | author | label | title_regex | body_regex
    match         TEXT NOT NULL,
    action        TEXT NOT NULL DEFAULT 'exclude',         -- exclude | include_new (auto-include rule)
    reason        TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE connector_checkpoints (
    connector_id     TEXT NOT NULL REFERENCES connectors(connector_id),
    stream           TEXT NOT NULL,                        -- 'incr:<src>' | 'backfill:<src>:<start>' | 'webhook' | 'export:<sha>'
    phase            TEXT NOT NULL,                        -- incremental | backfill | export | webhook
    cursor           TEXT NOT NULL DEFAULT '{}',           -- Cursor {version, data, high_watermark}
    window_start     TEXT,
    window_end       TEXT,
    status           TEXT NOT NULL DEFAULT 'idle',         -- idle | running | done | error | paused
    items_seen       INTEGER NOT NULL DEFAULT 0,
    items_enqueued   INTEGER NOT NULL DEFAULT 0,
    last_error_code  TEXT,
    version          INTEGER NOT NULL DEFAULT 0,           -- fencing for commit_page
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (connector_id, stream)
);

-- ======================================================================= [C] durable queue
CREATE TABLE ingest_queue (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key       TEXT NOT NULL UNIQUE,                  -- identical deliveries collapse here
    record_key      TEXT NOT NULL,
    connector_id    TEXT NOT NULL,
    stream          TEXT NOT NULL,
    kind            TEXT NOT NULL,                         -- CanonicalEvent.kind
    priority_class  TEXT NOT NULL,                         -- delete | live | user | backfill | reindex | maintenance
    order_key       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',        -- queued | leased | done | dead | superseded | discarded
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 6,
    available_at    TEXT NOT NULL,
    leased_until    TEXT,
    worker_id       TEXT,
    last_error_code TEXT,                                  -- code only; never content
    outcome         TEXT,
    payload         BLOB,                                  -- zlib(json(CanonicalEvent)); NULL once done/discarded (purged)
    payload_bytes   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    finished_at     TEXT
);
CREATE INDEX idx_queue_lease ON ingest_queue(status, priority_class, available_at);
CREATE INDEX idx_queue_record ON ingest_queue(record_key, status, order_key);

CREATE TABLE ingest_deliveries (                         -- provider event ids seen on this holder (notice dedupe)
    delivery_key  TEXT PRIMARY KEY,
    connector_id  TEXT NOT NULL,
    received_at   TEXT NOT NULL
);

CREATE TABLE ingest_chunk_buffer (                       -- document_chunk assembly
    record_key   TEXT NOT NULL,
    version_key  TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    chunk_count  INTEGER NOT NULL,
    body         TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (version_key, chunk_index)
);

CREATE TABLE ingest_outbox (                             -- envelopes written with the effect, published afterwards (no loss on crash)
    msg_id      TEXT PRIMARY KEY,                          -- deterministic: evidence:<record>:<order> | ingestbatch:<holder>:<seq>
    kind        TEXT NOT NULL,                             -- evidence_event | ingest_result
    payload     TEXT NOT NULL,                             -- content-free JSON
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);
CREATE INDEX idx_outbox_pending ON ingest_outbox(sent_at, created_at);

CREATE TABLE ingest_stage_metrics (                      -- per-stage metering (counts and latency only)
    connector_id  TEXT NOT NULL,                           -- '' for holder-level stages
    stage         TEXT NOT NULL,                           -- fetch | admit | normalize | redact | enqueue | dedupe | classify | route | write | publish
    outcome       TEXT NOT NULL,
    count         INTEGER NOT NULL DEFAULT 0,
    total_ms      REAL NOT NULL DEFAULT 0,
    max_ms        REAL NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (connector_id, stage, outcome)
);

-- ======================================================================= [C] routing catalog, tombstones, shards
CREATE TABLE record_locator (                            -- dedupe authority + sticky routing
    record_key        TEXT PRIMARY KEY,
    record_id         TEXT NOT NULL UNIQUE,
    shard_id          TEXT NOT NULL,
    kind              TEXT NOT NULL,
    current_order_key TEXT,
    content_hash      TEXT,
    metadata_hash     TEXT,
    deletion_status   TEXT NOT NULL DEFAULT 'live',
    change_seq        INTEGER NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_locator_shard ON record_locator(shard_id, change_seq);

CREATE TABLE deletion_tombstones (                       -- content-free; suppresses resurrection
    record_key        TEXT PRIMARY KEY,
    record_id         TEXT NOT NULL,
    shard_id          TEXT NOT NULL,
    reason            TEXT NOT NULL,                       -- deleted_at_source | owner_deleted | owner_deleted_source | disconnect
                                                           -- | retention | access_lost_expired | redaction | excluded_after_ingest
    order_key         TEXT,
    resurrectable     INTEGER NOT NULL DEFAULT 0,
    requested_at      TEXT NOT NULL,
    purged_at         TEXT,
    propagated_at     TEXT,
    affected_ref_ids  TEXT NOT NULL DEFAULT '[]',
    expires_at        TEXT NOT NULL
);

CREATE TABLE shard_map (                                 -- holder-local authority for routing
    shard_id        TEXT PRIMARY KEY,                      -- 's0' is this file
    ordinal         INTEGER NOT NULL UNIQUE,
    file_name       TEXT NOT NULL UNIQUE,
    domain_ids      TEXT NOT NULL DEFAULT '[]',            -- [] = default/catch-all shard
    time_from       TEXT,
    time_to         TEXT,
    status          TEXT NOT NULL DEFAULT 'active',        -- provisioning | active | draining | readonly | retired
    schema_version  INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
INSERT INTO shard_map(shard_id, ordinal, file_name, domain_ids, status, schema_version, created_at, updated_at)
VALUES ('s0', 0, 'evidence.db', '[]', 'active', 2, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));

CREATE TABLE domain_shard_counts (
    domain_id  TEXT NOT NULL,
    shard_id   TEXT NOT NULL,
    records    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain_id, shard_id)
);

-- ======================================================================= [C] taxonomy copy, centroids, identities
CREATE TABLE domains (                                   -- tenant taxonomy copy + this holder's personal domains
    domain_id        TEXT PRIMARY KEY,                     -- 'legal.contracts' | 'personal.<holder>.<slug>'
    parent_id        TEXT,
    name             TEXT NOT NULL,
    path             TEXT NOT NULL,
    ancestors        TEXT NOT NULL DEFAULT '[]',
    description      TEXT NOT NULL DEFAULT '',
    rules            TEXT NOT NULL DEFAULT '{}',
    scope            TEXT NOT NULL DEFAULT 'tenant',       -- tenant | personal (personal: never published, never routes)
    status           TEXT NOT NULL DEFAULT 'active',       -- active | deprecated
    taxonomy_version INTEGER NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE domain_aliases (                            -- legacy flat holder domains -> taxonomy ids (holder copy)
    alias      TEXT PRIMARY KEY,
    domain_id  TEXT NOT NULL
);

CREATE TABLE domain_centroids (
    domain_id     TEXT NOT NULL,
    embed_model   TEXT NOT NULL,
    dim           INTEGER NOT NULL,
    vector        BLOB NOT NULL,                           -- float32, L2-normalized
    n_seed        INTEGER NOT NULL,
    n_examples    INTEGER NOT NULL DEFAULT 0,
    floor_sim     REAL NOT NULL,
    ceil_sim      REAL NOT NULL,
    version       INTEGER NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (domain_id, embed_model)
);

CREATE TABLE entity_identities (                         -- cross-app identity registry (§8.1)
    app            TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    id_kind        TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    display_name   TEXT NOT NULL DEFAULT '',
    confidence     REAL NOT NULL,
    method         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (app, id_kind, external_id)
);
CREATE INDEX idx_identities_entity ON entity_identities(entity_id);

-- ======================================================================= [D] canonical records
CREATE TABLE ingest_records (
    record_id           TEXT PRIMARY KEY,                  -- rec_<hex> == documents.doc_id
    record_key          TEXT NOT NULL UNIQUE,
    object_key          TEXT NOT NULL,                     -- cross-holder canonical identity of the provider object
    kind                TEXT NOT NULL,                     -- message | document | event | conversation
    connector_id        TEXT NOT NULL,
    source_id           TEXT,
    source_app          TEXT NOT NULL,
    source_account_id   TEXT NOT NULL,
    source_object_type  TEXT NOT NULL,
    source_object_id    TEXT NOT NULL,
    conversation_record_id TEXT,
    thread_record_id    TEXT,
    parent_record_id    TEXT,
    author_entity_id    TEXT,
    participant_entity_ids TEXT NOT NULL DEFAULT '[]',
    created_at_src      TEXT,
    updated_at_src      TEXT,
    content_changed_at  TEXT,                              -- feeds documents.observed_at / evidence freshness
    first_ingested_at   TEXT NOT NULL,
    last_ingested_at    TEXT NOT NULL,
    current_version     TEXT NOT NULL DEFAULT '',
    current_order_key   TEXT NOT NULL,
    version_count       INTEGER NOT NULL DEFAULT 1,
    content_hash        TEXT NOT NULL,
    metadata_hash       TEXT NOT NULL,
    source_root_id      TEXT,
    root_known          INTEGER NOT NULL DEFAULT 1,
    root_method         TEXT NOT NULL,                     -- content | title | explicit | pure_copy | unknown
    primary_domain_id   TEXT,
    visibility          TEXT NOT NULL DEFAULT 'private',   -- public | members | private (copy of permissions.visibility)
    permissions         TEXT NOT NULL DEFAULT '{}',        -- {visibility, member_ids, membership_ref}
    sensitivity         TEXT NOT NULL DEFAULT 'internal',
    flags               TEXT NOT NULL DEFAULT '[]',        -- suspicious_instructions | contains_secret | truncated
    retention_policy    TEXT NOT NULL DEFAULT 'default',
    retain_until        TEXT,
    deletion_status     TEXT NOT NULL DEFAULT 'live',      -- live | redacted | access_lost | deleted_at_source | purged
    normalizer_version  TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    change_seq          INTEGER NOT NULL
);
CREATE INDEX idx_records_conversation ON ingest_records(conversation_record_id, created_at_src);
CREATE INDEX idx_records_source ON ingest_records(source_id, deletion_status);
CREATE INDEX idx_records_root ON ingest_records(source_root_id);
CREATE INDEX idx_records_object ON ingest_records(object_key);
CREATE INDEX idx_records_visibility ON ingest_records(visibility, deletion_status);
CREATE INDEX idx_records_retention ON ingest_records(retain_until) WHERE retain_until IS NOT NULL;
CREATE INDEX idx_records_change ON ingest_records(change_seq);

CREATE TABLE ingest_versions (
    record_id       TEXT NOT NULL,
    version_key     TEXT NOT NULL,
    source_version  TEXT NOT NULL,
    order_key       TEXT NOT NULL,
    kind            TEXT NOT NULL,                         -- message | document | event | historical | redaction | deletion
    content_hash    TEXT NOT NULL,
    metadata_hash   TEXT NOT NULL,
    source_event_id TEXT,
    text_retained   INTEGER NOT NULL DEFAULT 0,
    doc_version     INTEGER,
    observed_at     TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (record_id, version_key)
);

CREATE TABLE applied_events (                            -- idempotency marker written with the effect
    event_key   TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL,
    outcome     TEXT NOT NULL,                             -- new | update | metadata_only | historical | delete | redaction | late_after_delete
    applied_at  TEXT NOT NULL
);

CREATE TABLE record_memories (
    record_id    TEXT NOT NULL,
    memory_id    TEXT NOT NULL,
    doc_version  INTEGER NOT NULL,
    chunk_index  INTEGER,
    origin       TEXT NOT NULL,                            -- chunk | extracted
    PRIMARY KEY (record_id, memory_id)
);
CREATE INDEX idx_record_memories_memory ON record_memories(memory_id);

CREATE TABLE record_entities (
    record_id   TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    role        TEXT NOT NULL,                             -- author | participant | mention | reference | topic | container
    confidence  REAL NOT NULL,
    method      TEXT NOT NULL,
    PRIMARY KEY (record_id, entity_id, role)
);
CREATE INDEX idx_record_entities_entity ON record_entities(entity_id, role);

CREATE TABLE relation_evidence (                         -- reference-counted provenance for NeuralGraph relations (G6)
    relation_id  TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    memory_id    TEXT,
    confidence   REAL NOT NULL,
    modality     TEXT NOT NULL,
    method       TEXT NOT NULL,
    span_start   INTEGER,
    span_end     INTEGER,
    visibility   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (relation_id, record_id, method)
);
CREATE INDEX idx_relation_evidence_record ON relation_evidence(record_id);

-- ======================================================================= [D] domain memberships
CREATE TABLE domain_memberships (
    record_id        TEXT NOT NULL,
    domain_id        TEXT NOT NULL,
    confidence       REAL NOT NULL,
    method           TEXT NOT NULL,                        -- source_mapping | label | rule | embedding | conversation_prior | llm | human | fallback
    is_primary       INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'active',       -- active | removed (removed rows keep provenance)
    model_version    TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    evidence         TEXT NOT NULL DEFAULT '{}',           -- rule ids, similarity, rationale (no record quotes)
    corrected_by     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (record_id, domain_id)
);
CREATE INDEX idx_memberships_domain ON domain_memberships(domain_id, status, confidence);

CREATE TABLE domain_membership_history (                 -- append-only rerouting history
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   TEXT NOT NULL,
    domain_id   TEXT NOT NULL,
    action      TEXT NOT NULL,                             -- add | remove | primary_change | confidence_change | reclassify
    before      TEXT NOT NULL DEFAULT '{}',
    after       TEXT NOT NULL DEFAULT '{}',
    method      TEXT NOT NULL,
    actor_type  TEXT NOT NULL,                             -- system | user
    actor_id    TEXT,
    reason      TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);
CREATE INDEX idx_membership_history_record ON domain_membership_history(record_id, id);
CREATE TRIGGER domain_membership_history_no_update BEFORE UPDATE ON domain_membership_history
BEGIN
    SELECT RAISE(ABORT, 'domain_membership_history is append-only');
END;
CREATE TRIGGER domain_membership_history_no_delete BEFORE DELETE ON domain_membership_history
BEGIN
    SELECT RAISE(ABORT, 'domain_membership_history is append-only');
END;

CREATE TABLE domain_examples (                           -- labelled examples from corrections (centroid learning)
    record_id   TEXT NOT NULL,
    domain_id   TEXT NOT NULL,
    label       INTEGER NOT NULL,                          -- +1 positive, -1 negative
    source      TEXT NOT NULL,                             -- human_correction | seed
    created_at  TEXT NOT NULL,
    PRIMARY KEY (record_id, domain_id)
);

-- ======================================================================= existing tables touched
ALTER TABLE documents ADD COLUMN root_known INTEGER NOT NULL DEFAULT 1;
-- documents.status gains 'deleted', 'redacted' and 'suspended' (TEXT column, no CHECK); documents.domains holds domain ids,
-- primary first, for compatibility with document_view and _policy_reason.
CREATE INDEX idx_documents_origin ON documents(origin_id);
