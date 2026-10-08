-- Mycelic coordination database, schema version 3: durable evidence withdrawals (review finding: a response or a
-- holder's replayed outcome that arrives after a 'deleted' / 'retracted' event must not bring the reference back).
-- One row per reference a holder withdrew, written even when the coordinator has not seen the reference yet. Content-free.
CREATE TABLE evidence_withdrawals (
    ref_id       TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    holder_id    TEXT NOT NULL,
    event        TEXT NOT NULL,                            -- deleted | retracted
    reason       TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL
);
CREATE INDEX idx_evidence_withdrawals_holder ON evidence_withdrawals(tenant_id, holder_id);

-- Domains a holder's ingested records belong to, as its heartbeats last reported them (tenant taxonomy ids only, counts
-- above the holder's publication threshold). Replaced on every heartbeat, so domains of purged records stop routing;
-- the owner's hand-curated ``holders.domains`` is kept apart. Routing uses both.
ALTER TABLE holders ADD COLUMN published_domains TEXT NOT NULL DEFAULT '[]';
