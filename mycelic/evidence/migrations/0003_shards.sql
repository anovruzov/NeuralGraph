-- Holder schema v3: per-holder domain sharding (docs/mycelic/INGESTION.md §7.2-§7.9, product decision 1).
-- One store per holder: s0 (evidence.db) is the control shard and the default data shard. A domain subtree is split into
-- its own file (holders/<holder_id>/shd_<id>.db) only when measured thresholds trip. Every shard file carries the full
-- holder schema (§5.1); the control tables below are used in s0 only, the intent log in data shards only. Content-free.

-- [D] Control effects a data shard owes s0. SQLite cannot commit two files atomically, so a write to a data shard records
-- what s0 must do next (locator, queue ack, outbox, tombstone, export scrubs ...) in the SAME transaction as the shard
-- effect; the ops are applied to s0 right after the shard commits and the row is deleted afterwards. Every op is
-- idempotent, so a crash between the two commits is repaired by applying whatever is still pending (§7.1, §9.1).
CREATE TABLE shard_control_intents (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id   TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,                         -- tx (ops of one shard transaction)
    payload     TEXT NOT NULL DEFAULT '{}',            -- {"ops": [{"op": ..., ...}]}; ids, hashes and counts only
    created_at  TEXT NOT NULL
);

-- [C] Split migrations this holder runs (§7.7), mirrored to coord.shard_migrations through the heartbeat.
CREATE TABLE shard_migrations (
    migration_id  TEXT PRIMARY KEY,                    -- smg_<hex>
    kind          TEXT NOT NULL,                       -- split
    from_shard    TEXT NOT NULL,
    to_shard      TEXT NOT NULL,
    partition     TEXT NOT NULL DEFAULT '{}',          -- {"domain_ids": [...]}
    state         TEXT NOT NULL,                       -- planned | provisioning | copying | catching_up | cutover | cleanup | done
                                                       -- | aborted | failed
    checkpoint    TEXT NOT NULL DEFAULT '{}',          -- {last_rowid, copy_start_seq, caught_up_seq, copied, moved, cleaned, passes}
    requested_by  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    started_at    TEXT,
    cutover_at    TEXT,
    finished_at   TEXT,
    error_code    TEXT
);
CREATE INDEX idx_shard_migrations_state ON shard_migrations(state);

-- [C] The records a migration moves (resumable checkpoint): copied -> moved (locator switched) -> cleaned (source purged).
CREATE TABLE shard_migration_records (
    migration_id  TEXT NOT NULL,
    record_id     TEXT NOT NULL,
    record_key    TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'copied',      -- copied | moved | cleaned
    fingerprint   TEXT NOT NULL DEFAULT '',            -- state of the source copy when it was last copied
    history_max   INTEGER NOT NULL DEFAULT 0,          -- domain_membership_history is append-only: rows copied up to this id
    PRIMARY KEY (migration_id, record_id)
);
CREATE INDEX idx_shard_migration_records_state ON shard_migration_records(migration_id, state);

-- [C] Threshold signals per shard (§7.3): a split is recommended only when a signal has held for the sustain window.
CREATE TABLE shard_signals (
    shard_id       TEXT NOT NULL,
    signal         TEXT NOT NULL,                      -- matrix_bytes | active_memories | file_bytes | write_p95 | query_p95
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    value          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (shard_id, signal)
);

-- [C] Last collected ShardStats (counts, bytes, p95s; no content) and the last online backup of each shard file.
ALTER TABLE shard_map ADD COLUMN stats TEXT NOT NULL DEFAULT '{}';
ALTER TABLE shard_map ADD COLUMN stats_at TEXT;
ALTER TABLE shard_map ADD COLUMN last_backup_at TEXT;
