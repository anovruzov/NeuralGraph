-- Job harness persistent state. Applied idempotently at startup.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    company             TEXT NOT NULL,
    title               TEXT NOT NULL,
    normalized_title    TEXT NOT NULL,
    normalized_company  TEXT NOT NULL,
    location            TEXT,
    remote_status       TEXT,
    employment_type     TEXT,
    posted_date         TEXT,
    salary_min          INTEGER,
    salary_max          INTEGER,
    description         TEXT,
    description_hash    TEXT,
    source_url          TEXT,
    canonical_apply_url TEXT NOT NULL,
    ats_type            TEXT,
    ats_job_key         TEXT,
    discovered_at       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'DISCOVERED',
    score               INTEGER,
    score_breakdown     TEXT,
    score_reason        TEXT,
    scored_at           TEXT,
    raw                 TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_canonical ON jobs(canonical_apply_url);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_identity  ON jobs(normalized_company, normalized_title, COALESCE(ats_job_key, canonical_apply_url));
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score   ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(normalized_company);

CREATE TABLE IF NOT EXISTS applications (
    application_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    run_id              TEXT,
    status              TEXT NOT NULL,
    attempt             INTEGER NOT NULL DEFAULT 1,
    dry_run             INTEGER NOT NULL DEFAULT 1,
    started_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    submitted_at        TEXT,
    verified_at         TEXT,
    ats_application_id  TEXT,
    confirmation_text   TEXT,
    confirmation_url    TEXT,
    evidence            TEXT,
    blocker_type        TEXT,
    blocker_detail      TEXT,
    error               TEXT,
    checkpoint          TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_job    ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
-- One terminal-success record per job: guards against double submission across restarts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_submitted_once
    ON applications(job_id) WHERE status IN ('SUBMITTED','VERIFIED');
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_ats_id
    ON applications(ats_application_id) WHERE ats_application_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS form_answers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER REFERENCES applications(application_id) ON DELETE CASCADE,
    job_id          TEXT,
    field_key       TEXT NOT NULL,
    label           TEXT,
    field_type      TEXT,
    answer          TEXT,
    confidence      REAL,
    source          TEXT,
    resolver        TEXT,
    safe_to_submit  INTEGER,
    filled          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_answers_app ON form_answers(application_id);
CREATE INDEX IF NOT EXISTS idx_answers_key ON form_answers(field_key);

CREATE TABLE IF NOT EXISTS qwen_calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT,
    function            TEXT NOT NULL,
    model               TEXT,
    cache_key           TEXT,
    cache_hit           INTEGER NOT NULL DEFAULT 0,
    prompt_tokens       INTEGER DEFAULT 0,
    completion_tokens   INTEGER DEFAULT 0,
    total_tokens        INTEGER DEFAULT 0,
    estimated_cost_usd  REAL DEFAULT 0,
    latency_ms          INTEGER,
    attempts            INTEGER DEFAULT 1,
    ok                  INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qwen_fn  ON qwen_calls(function);
CREATE INDEX IF NOT EXISTS idx_qwen_run ON qwen_calls(run_id);

CREATE TABLE IF NOT EXISTS qwen_cache (
    cache_key   TEXT PRIMARY KEY,
    function    TEXT,
    model       TEXT,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    profile_dir     TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    pages_opened    INTEGER DEFAULT 0,
    crashes         INTEGER DEFAULT 0,
    user_agent      TEXT,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    job_id      TEXT,
    stage       TEXT NOT NULL,
    kind        TEXT,
    message     TEXT,
    traceback   TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_errors_job ON errors(job_id);

CREATE TABLE IF NOT EXISTS run_statistics (
    run_id              TEXT PRIMARY KEY,
    mode                TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    state               TEXT NOT NULL DEFAULT 'RUNNING',
    discovered          INTEGER DEFAULT 0,
    scored              INTEGER DEFAULT 0,
    skipped             INTEGER DEFAULT 0,
    queued              INTEGER DEFAULT 0,
    opened              INTEGER DEFAULT 0,
    submitted           INTEGER DEFAULT 0,
    verified            INTEGER DEFAULT 0,
    blocked             INTEGER DEFAULT 0,
    failed              INTEGER DEFAULT 0,
    duplicates          INTEGER DEFAULT 0,
    ready_to_submit     INTEGER DEFAULT 0,
    qwen_requests       INTEGER DEFAULT 0,
    qwen_tokens         INTEGER DEFAULT 0,
    qwen_cost_usd       REAL DEFAULT 0,
    current_company     TEXT,
    current_role        TEXT,
    current_state       TEXT,
    config_snapshot     TEXT
);

CREATE TABLE IF NOT EXISTS control (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
