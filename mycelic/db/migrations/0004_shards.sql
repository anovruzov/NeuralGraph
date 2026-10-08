-- Mycelic coordination database, schema version 4: the shard registry keyed by holder (docs/mycelic/INGESTION.md §5.3,
-- §7.2, §7.9). Every holder has its own control shard 's0', so a shard is identified by (holder_id, shard_id); data
-- shards are 'shd_<hex>' and never reused. Rows mirror each holder's shard_map through its heartbeat: counts, bytes and
-- latencies only, never content. The table had no writer before this version, so it is rebuilt rather than altered.
DROP INDEX IF EXISTS idx_shards_tenant;
ALTER TABLE shards RENAME TO shards_v3;
CREATE TABLE shards (
    shard_id           TEXT NOT NULL,                      -- 's0' | 'shd_<hex>'
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
    recommendation     TEXT,                               -- split recommendation for this shard (tenant domain ids, signals, load)
    last_backup_at     TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (holder_id, shard_id),
    UNIQUE (holder_id, ordinal)
);
INSERT INTO shards(shard_id, tenant_id, holder_id, ordinal, partition, placement, status, schema_version, writer_id, writer_lease_until, stats,
                   health, last_backup_at, created_at, updated_at)
SELECT shard_id, tenant_id, holder_id, ordinal, partition, placement, status, schema_version, writer_id, writer_lease_until, stats, health,
       last_backup_at, created_at, updated_at FROM shards_v3;
DROP TABLE shards_v3;
CREATE INDEX idx_shards_tenant ON shards(tenant_id, holder_id, status);

CREATE INDEX idx_shard_migrations_holder ON shard_migrations(tenant_id, holder_id, state);
ALTER TABLE shard_migrations ADD COLUMN updated_at TEXT;
