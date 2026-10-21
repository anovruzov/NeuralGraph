//! SQLite persistence for cross-chat memory: raw messages, derived memories, entities, relations, links,
//! provenance, and a durable job queue for the background worker.
//!
//! Design notes
//! * One SQLite connection behind a mutex, WAL mode. Every multi-statement write runs inside one
//!   `BEGIN IMMEDIATE ... COMMIT` while holding the mutex, so writes are atomic and never interleave.
//! * The worker computes an extraction *plan* with the LLM first and then commits it with
//!   [`ChatMemoryStore::apply_plan`] in ONE transaction that also marks the messages processed and the jobs done.
//!   A crash before that commit leaves nothing behind, so a retried job starts clean.
//! * Job leasing preserves per-chat order (a job is only leasable when no earlier-`seq` job of the same chat is
//!   still queued/leased) while different chats proceed in parallel.
//! * Memory text is indexed in FTS5 (`memories_fts`) when the SQLite build has it; otherwise the keyword channel
//!   falls back to an in-process BM25 over active memories.
//! * `revision` increases on every memory/entity/relation write; retrieval caches key on it.
//!
//! The schema, column encodings (JSON text columns with sorted keys, little-endian float32 embedding blobs) and
//! every SQL statement are the ones the Python implementation used, so databases created by either version open
//! and work in the other.

use crate::bm25::Bm25Okapi;
use crate::error::{MemoryError, Result};
use crate::models::{
    iso, new_id, now_iso, precise_iso, utcnow, ChatMessage, Entity, Job, Memory, MemoryLink, Relation,
};
use crate::pycompat::{dumps, fnum, loads, obj, JsonFormat};
use crate::textutil::{content_hash, fts_match_string, norm_entity, normalize_ws, tokenize, word_count};
use chrono::TimeDelta;
use indexmap::{IndexMap, IndexSet};
use rusqlite::types::Value as SqlValue;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde_json::{Map, Value};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;

pub const SCHEMA_VERSION: i64 = 1;

const DDL: &str = r#"
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id       TEXT PRIMARY KEY,
    title         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    participants  TEXT NOT NULL DEFAULT '[]',
    message_count INTEGER NOT NULL DEFAULT 0,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    rid          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL UNIQUE,
    chat_id      TEXT NOT NULL REFERENCES chats(chat_id),
    seq          INTEGER NOT NULL,
    speaker      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    sent_at      TEXT,
    ingested_at  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    processed_at TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',
    UNIQUE(chat_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_seq ON messages(chat_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_hash ON messages(content_hash);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS memories (
    rid                  INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id            TEXT NOT NULL UNIQUE,
    text                 TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    subject              TEXT NOT NULL,
    subject_name         TEXT NOT NULL,
    speaker              TEXT NOT NULL,
    chat_id              TEXT NOT NULL,
    importance           REAL NOT NULL,
    confidence           REAL NOT NULL,
    event_time           TEXT,
    event_time_precision TEXT NOT NULL DEFAULT 'none',
    observed_at          TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'active',
    superseded_by        TEXT,
    version              INTEGER NOT NULL DEFAULT 1,
    text_hash            TEXT NOT NULL,
    embedding            BLOB,
    embedding_dim        INTEGER,
    access_count         INTEGER NOT NULL DEFAULT 0,
    last_accessed_at     TEXT,
    metadata             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject, status);
CREATE INDEX IF NOT EXISTS idx_memories_chat ON memories(chat_id);
CREATE INDEX IF NOT EXISTS idx_memories_text_hash ON memories(text_hash);
CREATE INDEX IF NOT EXISTS idx_memories_observed ON memories(observed_at);

CREATE TABLE IF NOT EXISTS memory_sources (
    memory_id  TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (memory_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_sources_msg ON memory_sources(message_id);

CREATE TABLE IF NOT EXISTS entities (
    entity_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT '',
    mention_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at  TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entities_mentions ON entities(mention_count DESC);

CREATE TABLE IF NOT EXISTS entity_aliases (
    alias     TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(entity_id)
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'mention',
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(entity_id);

CREATE TABLE IF NOT EXISTS relations (
    relation_id       TEXT PRIMARY KEY,
    subject_id        TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object_id         TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 0.5,
    chat_id           TEXT NOT NULL,
    memory_id         TEXT,
    message_id        TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    observation_count INTEGER NOT NULL DEFAULT 1,
    observed_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    metadata          TEXT NOT NULL DEFAULT '{}',
    UNIQUE (subject_id, predicate, object_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);

CREATE TABLE IF NOT EXISTS memory_links (
    source_id  TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    link_type  TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, link_type)
);
CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    chat_id      TEXT,
    ref_id       TEXT,
    seq          INTEGER,
    dedupe_key   TEXT UNIQUE,
    status       TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TEXT NOT NULL,
    leased_until TEXT,
    worker_id    TEXT,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    finished_at  TEXT,
    payload      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_avail ON jobs(status, available_at);
CREATE INDEX IF NOT EXISTS idx_jobs_chat_seq ON jobs(chat_id, seq);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,
    ref    TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
"#;

const FTS_DDL: &str = r#"
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, content='memories', content_rowid='rid', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rid, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='rid', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.rid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rid, old.text);
END;
"#;

/// Predicates that hold one value at a time: a newer observation supersedes the old object.
#[rustfmt::skip]
pub const FUNCTIONAL_PREDICATES: [&str; 23] = [
    "lives_in", "works_at", "works_as", "married_to", "employed_by", "age", "birthday", "born_in", "based_in",
    "relationship_status", "current_project", "current_job", "nationality", "primary_language", "timezone",
    "studies_at", "job_title", "phone", "email", "home_city", "hometown", "partner_of", "dating",
];

/// Embeddings are stored as native little-endian float32 (Python `struct.pack(f"{n}f")`).
pub fn pack(vec: &[f64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for x in vec {
        out.extend_from_slice(&(*x as f32).to_le_bytes());
    }
    out
}

pub fn unpack(blob: &[u8]) -> Vec<f64> {
    let (chunks, _) = blob.as_chunks::<4>();
    chunks.iter().map(|c| f32::from_le_bytes(*c) as f64).collect()
}

/// The JSON text written to every JSON column: sorted keys, non-ASCII kept, `None` stored as `{}`.
pub fn j(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "{}".to_string(),
        Some(v) => dumps(v, &JsonFormat::stored()),
    }
}

/// Decode a JSON column, falling back to `default` when empty or invalid.
fn jl(s: Option<String>, default: Value) -> Value {
    match s {
        Some(text) if !text.is_empty() => loads(&text).unwrap_or(default),
        _ => default,
    }
}

fn empty_obj() -> Value {
    Value::Object(Map::new())
}

/// Expand date-only / month / year `since`/`until` to full ISO bounds (inclusive of the whole period).
pub fn query_bounds(since: Option<&str>, until: Option<&str>) -> (String, String) {
    let mut lo = since.unwrap_or("").to_string();
    let mut hi = until.unwrap_or("").to_string();
    let lo_len = crate::pycompat::py_len(&lo);
    if !lo.is_empty() && lo_len == 10 {
        lo.push_str("T00:00:00");
    } else if !lo.is_empty() && lo_len == 7 {
        lo.push_str("-01T00:00:00");
    } else if !lo.is_empty() && lo_len == 4 {
        lo.push_str("-01-01T00:00:00");
    }
    let hi_len = crate::pycompat::py_len(&hi);
    if !hi.is_empty() && hi_len == 10 {
        hi.push_str("T23:59:59");
    } else if !hi.is_empty() && hi_len == 7 {
        hi.push_str("-31T23:59:59");
    } else if !hi.is_empty() && hi_len == 4 {
        hi.push_str("-12-31T23:59:59");
    }
    (crate::pycompat::py_prefix(&lo, 19).to_string(), crate::pycompat::py_prefix(&hi, 19).to_string())
}

// SQL twins of retrieval's time bounds: a memory's [start, end] given the precision of its event_time
const MEM_START_SQL: &str = "(CASE WHEN m.event_time IS NULL OR m.event_time = '' THEN substr(m.observed_at, 1, 19) WHEN length(m.event_time) = 4 THEN m.event_time || '-01-01T00:00:00' WHEN length(m.event_time) = 7 THEN m.event_time || '-01T00:00:00' WHEN length(m.event_time) = 10 THEN m.event_time || 'T00:00:00' ELSE substr(m.event_time, 1, 19) END)";
const MEM_END_SQL: &str = "(CASE WHEN m.event_time IS NULL OR m.event_time = '' THEN substr(m.observed_at, 1, 19) WHEN length(m.event_time) = 4 THEN m.event_time || '-12-31T23:59:59' WHEN length(m.event_time) = 7 THEN m.event_time || '-31T23:59:59' WHEN length(m.event_time) = 10 THEN m.event_time || 'T23:59:59' ELSE substr(m.event_time, 1, 19) END)";

/// SQL fragment (starting with " AND ...") restricting rows whose time interval overlaps [since, until].
fn time_predicates(since: Option<&str>, until: Option<&str>, args: &mut Vec<SqlValue>) -> String {
    let (lo, hi) = query_bounds(since, until);
    let mut sql = String::new();
    if !lo.is_empty() {
        sql.push_str(&format!(" AND {MEM_END_SQL} >= ?"));
        args.push(SqlValue::Text(lo));
    }
    if !hi.is_empty() {
        sql.push_str(&format!(" AND {MEM_START_SQL} <= ?"));
        args.push(SqlValue::Text(hi));
    }
    sql
}

fn placeholders(n: usize) -> String {
    vec!["?"; n].join(",")
}

fn text(v: &str) -> SqlValue {
    SqlValue::Text(v.to_string())
}

fn opt_text(v: Option<&str>) -> SqlValue {
    match v {
        Some(s) => SqlValue::Text(s.to_string()),
        None => SqlValue::Null,
    }
}

fn dedup<I: IntoIterator<Item = S>, S: Into<String>>(items: I) -> Vec<String> {
    let set: IndexSet<String> = items.into_iter().map(Into::into).collect();
    set.into_iter().collect()
}

/// Options for [`ChatMemoryStore::add_message`] (the Python keyword arguments, same defaults).
#[derive(Clone, Debug)]
pub struct AddMessage {
    pub role: String,
    pub sent_at: Option<String>,
    pub message_id: Option<String>,
    pub metadata: Option<Value>,
    pub enqueue: bool,
    pub debounce_seconds: f64,
    pub max_attempts: i64,
}

impl Default for AddMessage {
    fn default() -> Self {
        AddMessage {
            role: String::new(),
            sent_at: None,
            message_id: None,
            metadata: None,
            enqueue: true,
            debounce_seconds: 0.0,
            max_attempts: 5,
        }
    }
}

/// Filters for [`ChatMemoryStore::list_memories`]. `status: None` returns every status.
#[derive(Clone, Debug)]
pub struct MemoryQuery {
    pub status: Option<String>,
    pub subject: Option<String>,
    pub speaker: Option<String>,
    pub chat_id: Option<String>,
    pub kinds: Option<Vec<String>>,
    pub since: Option<String>,
    pub until: Option<String>,
    pub limit: Option<i64>,
    pub offset: i64,
    pub order: String,
    pub with_sources: bool,
}

impl Default for MemoryQuery {
    fn default() -> Self {
        MemoryQuery {
            status: Some("active".into()),
            subject: None,
            speaker: None,
            chat_id: None,
            kinds: None,
            since: None,
            until: None,
            limit: None,
            offset: 0,
            order: "observed_at DESC".into(),
            with_sources: false,
        }
    }
}

/// Filters pushed into the keyword and graph channels so a filtered search is not starved by the global LIMIT.
#[derive(Clone, Debug, Default)]
pub struct ChannelFilter {
    pub subject: Option<String>,
    pub chat_id: Option<String>,
    pub speaker: Option<String>,
    pub kinds: Option<Vec<String>>,
    pub since: Option<String>,
    pub until: Option<String>,
}

impl ChannelFilter {
    /// Python truthiness of `subject or chat_id or speaker or kinds or since or until`.
    fn any(&self) -> bool {
        let set = |s: &Option<String>| s.as_deref().is_some_and(|v| !v.is_empty());
        set(&self.subject)
            || set(&self.chat_id)
            || set(&self.speaker)
            || self.kinds.as_ref().is_some_and(|k| !k.is_empty())
            || set(&self.since)
            || set(&self.until)
    }
}

/// One lightweight row for the retrieval index.
#[derive(Clone, Debug)]
pub struct IndexRow {
    pub memory_id: String,
    pub subject: String,
    pub speaker: String,
    pub chat_id: String,
    pub kind: String,
    pub importance: f64,
    pub observed_at: String,
    pub event_time: Option<String>,
    pub event_time_precision: Option<String>,
    pub status: String,
    pub updated_at: String,
    pub embedding: Option<Vec<f64>>,
}

/// A field change for [`ChatMemoryStore::update_memory`].
#[derive(Clone, Debug)]
pub enum MemoryField {
    Text(String),
    Kind(String),
    Subject(String),
    SubjectName(String),
    Importance(f64),
    Confidence(f64),
    EventTime(Option<String>),
    EventTimePrecision(String),
    Status(String),
    SupersededBy(Option<String>),
    Version(i64),
    Embedding(Option<Vec<f64>>),
    Metadata(Value),
    ObservedAt(String),
}

/// An entity to upsert as part of a plan.
#[derive(Clone, Debug)]
pub struct PlanEntity {
    pub entity_id: String,
    pub name: String,
    pub etype: String,
    pub seen_at: Option<String>,
    pub mentions: i64,
    pub aliases: Vec<String>,
}

impl PlanEntity {
    pub fn new(entity_id: &str, name: &str) -> Self {
        PlanEntity {
            entity_id: entity_id.into(),
            name: name.into(),
            etype: String::new(),
            seen_at: None,
            mentions: 1,
            aliases: Vec::new(),
        }
    }
}

/// A typed triple to upsert as part of a plan.
#[derive(Clone, Debug, PartialEq)]
pub struct PlanRelation {
    pub subject: String,
    pub predicate: String,
    pub object: String,
    pub confidence: f64,
    pub memory_id: Option<String>,
    pub message_id: Option<String>,
    pub observed_at: Option<String>,
}

/// Everything the worker decided for one batch of messages, applied atomically by
/// [`ChatMemoryStore::apply_plan`].
#[derive(Clone, Debug, Default)]
pub struct ExtractionPlan {
    pub chat_id: String,
    pub job_ids: Vec<i64>,
    pub entities: Vec<PlanEntity>,
    /// (memory, source_message_ids, entity_ids)
    pub new_memories: Vec<(Memory, Vec<String>, Vec<String>)>,
    /// (old_id, new_id, link_type)
    pub supersedes: Vec<(String, String, String)>,
    /// (existing_memory_id, source_message_ids)
    pub duplicate_sources: Vec<(String, Vec<String>)>,
    pub relations: Vec<PlanRelation>,
    /// (source_id, target_id, link_type, weight)
    pub links: Vec<(String, String, String, f64)>,
    pub processed_message_ids: Vec<String>,
    pub skipped_message_ids: Vec<String>,
    /// (kind, ref, detail)
    pub audit: Vec<(String, Option<String>, Value)>,
}

impl ExtractionPlan {
    pub fn new(chat_id: &str, job_ids: &[i64]) -> Self {
        ExtractionPlan { chat_id: chat_id.to_string(), job_ids: job_ids.to_vec(), ..Default::default() }
    }

    pub fn summary(&self) -> Value {
        obj([
            ("new_memories", Value::from(self.new_memories.len())),
            ("supersedes", Value::from(self.supersedes.len())),
            ("duplicates", Value::from(self.duplicate_sources.len())),
            ("relations", Value::from(self.relations.len())),
            ("links", Value::from(self.links.len())),
            ("entities", Value::from(self.entities.len())),
            ("processed", Value::from(self.processed_message_ids.len())),
            ("skipped", Value::from(self.skipped_message_ids.len())),
        ])
    }
}

/// Counts returned by [`ChatMemoryStore::apply_plan`].
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PlanCounts {
    pub memories_added: i64,
    pub memories_superseded: i64,
    pub duplicates: i64,
    pub entities: i64,
    pub relations: i64,
    pub links: i64,
}

type Bm25Cache = ((u64, i64), Vec<String>, Option<Bm25Okapi>);
/// attempts, max_attempts, ref_id, kind, status, worker_id
type JobFailRow = (i64, i64, Option<String>, String, String, Option<String>);
/// entity id -> [(neighbour id, predicate, confidence)]
pub type Neighbors = IndexMap<String, Vec<(String, String, f64)>>;

/// SQLite-backed store. See module docs.
pub struct ChatMemoryStore {
    pub db_path: String,
    conn: Mutex<Option<Connection>>,
    revision: AtomicU64,
    has_fts: AtomicBool,
    bm25_cache: Mutex<Option<Bm25Cache>>,
}

/// `str(Path(path).expanduser())`: `~` expands to `$HOME`, `.` components and repeated or trailing slashes are
/// dropped (so the path printed in stats and logs is the one the Python version printed).
fn expand_user(path: &str) -> PathBuf {
    let expanded = if path == "~" || path.starts_with("~/") {
        match std::env::var_os("HOME") {
            Some(home) => format!("{}{}", home.to_string_lossy(), &path[1..]),
            None => path.to_string(),
        }
    } else {
        path.to_string()
    };
    let absolute = expanded.starts_with('/');
    let parts: Vec<&str> = expanded.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let joined = parts.join("/");
    PathBuf::from(match (absolute, joined.is_empty()) {
        (true, _) => format!("/{joined}"),
        (false, true) => ".".to_string(),
        (false, false) => joined,
    })
}

impl ChatMemoryStore {
    /// Open (creating if needed) the database at `db_path` (`~` expands; `":memory:"` is in-memory).
    pub fn open(db_path: &str) -> Result<Self> {
        let path = if db_path == ":memory:" {
            ":memory:".to_string()
        } else {
            let p = expand_user(db_path);
            if let Some(parent) = p.parent() {
                if !parent.as_os_str().is_empty() {
                    std::fs::create_dir_all(parent)?;
                }
            }
            p.to_string_lossy().into_owned()
        };
        let conn = Connection::open(&path)?;
        let store = ChatMemoryStore {
            db_path: path,
            conn: Mutex::new(Some(conn)),
            revision: AtomicU64::new(0),
            has_fts: AtomicBool::new(false),
            bm25_cache: Mutex::new(None),
        };
        store.init_schema()?;
        Ok(store)
    }

    fn init_schema(&self) -> Result<()> {
        self.with_conn(|c| {
            if self.db_path != ":memory:" {
                let _ = c.query_row("PRAGMA journal_mode=WAL", [], |r| r.get::<_, String>(0));
            }
            c.execute_batch("PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;")?;
            c.execute_batch(DDL)?;
            match c.execute_batch(FTS_DDL) {
                Ok(()) => self.has_fts.store(true, Ordering::SeqCst),
                Err(e) => {
                    tracing::warn!("FTS5 unavailable ({e}); keyword search falls back to in-process BM25");
                    self.has_fts.store(false, Ordering::SeqCst);
                }
            }
            let row: Option<String> =
                c.query_row("SELECT value FROM meta WHERE key='schema_version'", [], |r| r.get(0)).optional()?;
            match row {
                None => {
                    c.execute(
                        "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                        [SCHEMA_VERSION.to_string()],
                    )?;
                }
                Some(v) => {
                    let found: i64 = v.trim().parse().unwrap_or(0);
                    if found > SCHEMA_VERSION {
                        return Err(MemoryError::Other(
                            "RuntimeError",
                            format!("database schema {v} is newer than this code ({SCHEMA_VERSION})"),
                        ));
                    }
                }
            }
            Ok(())
        })
    }

    /// Whether keyword search uses FTS5 (settable so tests can exercise the BM25 fallback).
    pub fn has_fts(&self) -> bool {
        self.has_fts.load(Ordering::SeqCst)
    }

    pub fn set_has_fts(&self, value: bool) {
        self.has_fts.store(value, Ordering::SeqCst);
    }

    pub fn revision(&self) -> u64 {
        self.revision.load(Ordering::SeqCst)
    }

    pub fn close(&self) {
        let mut guard = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }

    /// Run `f` with the connection (holding the store mutex).
    pub fn with_conn<T>(&self, f: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        let guard = self.conn.lock().unwrap_or_else(|e| e.into_inner());
        match guard.as_ref() {
            Some(c) => f(c),
            None => Err(MemoryError::Other("ProgrammingError", "Cannot operate on a closed database.".into())),
        }
    }

    /// `BEGIN IMMEDIATE ... COMMIT` around `f`; rolls back on error.
    fn tx<T>(&self, f: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        self.with_conn(|c| {
            c.execute_batch("BEGIN IMMEDIATE")?;
            match f(c) {
                Ok(v) => {
                    c.execute_batch("COMMIT")?;
                    Ok(v)
                }
                Err(e) => {
                    let _ = c.execute_batch("ROLLBACK");
                    Err(e)
                }
            }
        })
    }

    fn bump(&self) {
        self.revision.fetch_add(1, Ordering::SeqCst);
        *self.bm25_cache.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }

    /// Key for read caches: local write counter + SQLite `data_version` (changes when *another*
    /// connection/process commits), so a second process sharing the file never serves stale indexes.
    pub fn cache_key(&self) -> (u64, i64) {
        let dv = self.with_conn(|c| Ok(c.query_row("PRAGMA data_version", [], |r| r.get::<_, i64>(0))?)).unwrap_or(0);
        (self.revision(), dv)
    }

    // ------------------------------------------------------------------ meta
    pub fn get_meta(&self, key: &str) -> Result<Option<String>> {
        self.with_conn(|c| Ok(c.query_row("SELECT value FROM meta WHERE key=?", [key], |r| r.get(0)).optional()?))
    }

    pub fn set_meta(&self, key: &str, value: &str) -> Result<()> {
        self.tx(|c| {
            c.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![key, value],
            )?;
            Ok(())
        })
    }

    // ------------------------------------------------------------------ chats & messages
    fn row_message(r: &Row) -> rusqlite::Result<ChatMessage> {
        Ok(ChatMessage {
            message_id: r.get("message_id")?,
            chat_id: r.get("chat_id")?,
            seq: r.get("seq")?,
            speaker: r.get("speaker")?,
            role: r.get("role")?,
            text: r.get("text")?,
            sent_at: r.get("sent_at")?,
            ingested_at: r.get("ingested_at")?,
            content_hash: r.get("content_hash")?,
            status: r.get("status")?,
            processed_at: r.get("processed_at")?,
            metadata: jl(r.get("metadata")?, empty_obj()),
        })
    }

    fn query_messages(c: &Connection, sql: &str, args: &[SqlValue]) -> Result<Vec<ChatMessage>> {
        let mut st = c.prepare(sql)?;
        let rows = st.query_map(params_from_iter(args.iter()), Self::row_message)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn upsert_chat(&self, chat_id: &str, title: Option<&str>, metadata: Option<&Value>) -> Result<()> {
        self.tx(|c| Self::upsert_chat_sync(c, chat_id, title, metadata))
    }

    fn upsert_chat_sync(c: &Connection, chat_id: &str, title: Option<&str>, metadata: Option<&Value>) -> Result<()> {
        let now = now_iso();
        c.execute(
            "INSERT INTO chats(chat_id, title, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 title = COALESCE(excluded.title, chats.title),
                 updated_at = excluded.updated_at,
                 metadata = CASE WHEN excluded.metadata = '{}' THEN chats.metadata ELSE excluded.metadata END",
            params![chat_id, title, now, now, j(metadata)],
        )?;
        Ok(())
    }

    /// Append a message to a chat and (optionally) enqueue its extraction job. Never calls the LLM.
    ///
    /// Idempotency (a client retrying a send must not duplicate the message, but a person really saying "yes"
    /// twice must not be dropped):
    /// * an explicit `message_id` that already exists returns the stored row;
    /// * otherwise the same (chat, speaker, text, sent_at) with a `sent_at` given is a resend;
    /// * without `sent_at` or `message_id` only an immediate repeat of the chat's *last* message (same speaker and
    ///   text) is treated as a resend.
    ///
    /// Returns `(message, created)`.
    pub fn add_message(
        &self,
        chat_id: &str,
        speaker: &str,
        text: &str,
        opts: &AddMessage,
    ) -> Result<(ChatMessage, bool)> {
        let speaker_norm = normalize_ws(speaker);
        let speaker = if speaker_norm.is_empty() { "unknown".to_string() } else { speaker_norm };
        let sent_at = opts.sent_at.as_deref();
        let chash =
            content_hash(&[chat_id, &speaker, text, sent_at.unwrap_or(""), opts.message_id.as_deref().unwrap_or("")]);
        self.tx(|c| {
            if let Some(mid) = &opts.message_id {
                let dup = Self::query_messages(c, "SELECT * FROM messages WHERE message_id=?", &[text_v(mid)])?;
                if let Some(m) = dup.into_iter().next() {
                    return Ok((m, false));
                }
            } else if sent_at.is_some_and(|s| !s.is_empty()) {
                let existing =
                    Self::query_messages(c, "SELECT * FROM messages WHERE content_hash=? LIMIT 1", &[text_v(&chash)])?;
                if let Some(m) = existing.into_iter().next() {
                    return Ok((m, false));
                }
            } else {
                let last = Self::query_messages(
                    c,
                    "SELECT * FROM messages WHERE chat_id=? ORDER BY seq DESC LIMIT 1",
                    &[text_v(chat_id)],
                )?;
                if let Some(m) = last.into_iter().next() {
                    if m.speaker == speaker && m.text == text && m.sent_at.as_deref().unwrap_or("").is_empty() {
                        return Ok((m, false));
                    }
                }
            }
            Self::upsert_chat_sync(c, chat_id, None, None)?;
            let seq: i64 = c.query_row(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS nxt FROM messages WHERE chat_id=?",
                [chat_id],
                |r| r.get(0),
            )?;
            let mid = opts.message_id.clone().filter(|m| !m.is_empty()).unwrap_or_else(|| new_id("msg"));
            let now = now_iso();
            c.execute(
                "INSERT INTO messages(message_id, chat_id, seq, speaker, role, text, sent_at, ingested_at,
                                            content_hash, status, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                params![mid, chat_id, seq, speaker, opts.role, text, sent_at, now, chash, j(opts.metadata.as_ref())],
            )?;
            c.execute(
                "UPDATE chats SET message_count = message_count + 1, updated_at = ?,
                       participants = (SELECT json_group_array(DISTINCT speaker) FROM messages WHERE chat_id = ?)
                       WHERE chat_id = ?",
                params![now, chat_id, chat_id],
            )?;
            if opts.enqueue {
                let ingest = utcnow();
                let debounce = opts.debounce_seconds.max(0.0);
                let avail = precise_iso(ingest + seconds(debounce));
                Self::enqueue_sync(
                    c,
                    "extract",
                    Some(chat_id),
                    Some(&mid),
                    Some(seq),
                    Some(&format!("extract:{mid}")),
                    Some(&avail),
                    opts.max_attempts,
                    None,
                )?;
                if opts.debounce_seconds > 0.0 {
                    // pull-forward debounce: earlier queued messages of this chat that are still inside their
                    // debounce window wait for this one, so a turn and its reply are extracted together
                    c.execute(
                        "UPDATE jobs SET available_at=? WHERE chat_id=? AND kind='extract' AND status='queued' AND available_at > ? AND available_at < ?",
                        params![avail, chat_id, precise_iso(ingest), avail],
                    )?;
                }
            }
            let msg = Self::query_messages(c, "SELECT * FROM messages WHERE message_id=?", &[text_v(&mid)])?
                .into_iter()
                .next()
                .ok_or_else(|| MemoryError::Other("RuntimeError", "message vanished".into()))?;
            Ok((msg, true))
        })
    }

    pub fn get_message(&self, message_id: &str) -> Result<Option<ChatMessage>> {
        self.with_conn(|c| {
            Ok(Self::query_messages(c, "SELECT * FROM messages WHERE message_id=?", &[text_v(message_id)])?
                .into_iter()
                .next())
        })
    }

    pub fn get_messages(
        &self,
        chat_id: &str,
        after_seq: Option<i64>,
        before_seq: Option<i64>,
        limit: Option<i64>,
        newest_first: bool,
    ) -> Result<Vec<ChatMessage>> {
        let mut sql = "SELECT * FROM messages WHERE chat_id=?".to_string();
        let mut args = vec![text_v(chat_id)];
        if let Some(a) = after_seq {
            sql.push_str(" AND seq > ?");
            args.push(SqlValue::Integer(a));
        }
        if let Some(b) = before_seq {
            sql.push_str(" AND seq < ?");
            args.push(SqlValue::Integer(b));
        }
        sql.push_str(if newest_first { " ORDER BY seq DESC" } else { " ORDER BY seq ASC" });
        if let Some(l) = limit {
            sql.push_str(" LIMIT ?");
            args.push(SqlValue::Integer(l));
        }
        self.with_conn(|c| Self::query_messages(c, &sql, &args))
    }

    /// Messages by id, in the order of the (deduplicated) ids given.
    pub fn get_messages_by_ids(&self, message_ids: &[String]) -> Result<Vec<ChatMessage>> {
        let ids = dedup(message_ids.iter().cloned());
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        self.with_conn(|c| {
            for chunk in ids.chunks(500) {
                let sql = format!("SELECT * FROM messages WHERE message_id IN ({})", placeholders(chunk.len()));
                let args: Vec<SqlValue> = chunk.iter().map(|s| text_v(s)).collect();
                out.extend(Self::query_messages(c, &sql, &args)?);
            }
            Ok(())
        })?;
        let order: IndexMap<&str, usize> = ids.iter().enumerate().map(|(i, m)| (m.as_str(), i)).collect();
        out.sort_by_key(|m| order.get(m.message_id.as_str()).copied().unwrap_or(0));
        Ok(out)
    }

    /// The `n` messages preceding `seq` in the chat, oldest first.
    pub fn context_before(&self, chat_id: &str, seq: i64, n: i64) -> Result<Vec<ChatMessage>> {
        if n <= 0 {
            return Ok(Vec::new());
        }
        let mut rows = self.with_conn(|c| {
            Self::query_messages(
                c,
                "SELECT * FROM messages WHERE chat_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                &[text_v(chat_id), SqlValue::Integer(seq), SqlValue::Integer(n)],
            )
        })?;
        rows.reverse();
        Ok(rows)
    }

    pub fn set_message_status(&self, message_ids: &[String], status: &str) -> Result<()> {
        if message_ids.is_empty() {
            return Ok(());
        }
        let now = if matches!(status, "processed" | "skipped" | "failed") { Some(now_iso()) } else { None };
        self.tx(|c| {
            let mut st =
                c.prepare("UPDATE messages SET status=?, processed_at=COALESCE(?, processed_at) WHERE message_id=?")?;
            for m in message_ids {
                st.execute(params![status, now, m])?;
            }
            Ok(())
        })
    }

    pub fn list_chats(&self, limit: i64, offset: i64) -> Result<Vec<Value>> {
        self.with_conn(|c| {
            let mut st = c.prepare("SELECT * FROM chats ORDER BY updated_at DESC LIMIT ? OFFSET ?")?;
            let rows = st.query_map(params![limit, offset], |r| {
                Ok(obj([
                    ("chat_id", Value::String(r.get("chat_id")?)),
                    ("title", r.get::<_, Option<String>>("title")?.map(Value::String).unwrap_or(Value::Null)),
                    ("created_at", Value::String(r.get("created_at")?)),
                    ("updated_at", Value::String(r.get("updated_at")?)),
                    ("participants", jl(r.get("participants")?, Value::Array(vec![]))),
                    ("message_count", Value::from(r.get::<_, i64>("message_count")?)),
                    ("metadata", jl(r.get("metadata")?, empty_obj())),
                ]))
            })?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    fn group_counts(&self, sql: &str) -> Result<IndexMap<String, i64>> {
        self.with_conn(|c| {
            let mut st = c.prepare(sql)?;
            let rows = st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
            Ok(rows.collect::<rusqlite::Result<IndexMap<_, _>>>()?)
        })
    }

    pub fn message_counts(&self) -> Result<IndexMap<String, i64>> {
        self.group_counts("SELECT status, COUNT(*) AS n FROM messages GROUP BY status")
    }

    /// Keyword search over raw messages (FTS5; falls back to LIKE).
    pub fn search_messages(&self, query: &str, limit: i64, chat_id: Option<&str>) -> Result<Vec<(ChatMessage, f64)>> {
        let toks = tokenize(query);
        if toks.is_empty() {
            return Ok(Vec::new());
        }
        if self.has_fts() {
            let m = fts_match_string(query);
            if m.is_empty() {
                return Ok(Vec::new());
            }
            let mut sql = "SELECT m.*, bm25(messages_fts) AS s FROM messages_fts f JOIN messages m ON m.rid = f.rowid WHERE messages_fts MATCH ?".to_string();
            let mut args = vec![SqlValue::Text(m)];
            if let Some(cid) = chat_id.filter(|s| !s.is_empty()) {
                sql.push_str(" AND m.chat_id = ?");
                args.push(text_v(cid));
            }
            sql.push_str(" ORDER BY s LIMIT ?");
            args.push(SqlValue::Integer(limit));
            return self.with_conn(|c| {
                let mut st = c.prepare(&sql)?;
                let rows = st.query_map(params_from_iter(args.iter()), |r| {
                    Ok((Self::row_message(r)?, -r.get::<_, f64>("s")?))
                })?;
                Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
            });
        }
        let mut sql = format!("SELECT * FROM messages WHERE {}", vec!["text LIKE ?"; toks.len()].join(" OR "));
        let mut args: Vec<SqlValue> = toks.iter().map(|t| SqlValue::Text(format!("%{t}%"))).collect();
        if let Some(cid) = chat_id.filter(|s| !s.is_empty()) {
            sql.push_str(" AND chat_id = ?");
            args.push(text_v(cid));
        }
        sql.push_str(" LIMIT ?");
        args.push(SqlValue::Integer(limit));
        Ok(self.with_conn(|c| Self::query_messages(c, &sql, &args))?.into_iter().map(|m| (m, 1.0)).collect())
    }

    // ------------------------------------------------------------------ jobs
    fn row_job(r: &Row) -> rusqlite::Result<Job> {
        Ok(Job {
            job_id: r.get("job_id")?,
            kind: r.get("kind")?,
            chat_id: r.get("chat_id")?,
            ref_id: r.get("ref_id")?,
            seq: r.get("seq")?,
            dedupe_key: r.get("dedupe_key")?,
            status: r.get("status")?,
            attempts: r.get("attempts")?,
            max_attempts: r.get("max_attempts")?,
            available_at: r.get("available_at")?,
            leased_until: r.get("leased_until")?,
            worker_id: r.get("worker_id")?,
            last_error: r.get("last_error")?,
            created_at: r.get("created_at")?,
            updated_at: r.get("updated_at")?,
            finished_at: r.get("finished_at")?,
            payload: jl(r.get("payload")?, empty_obj()),
        })
    }

    fn query_jobs(c: &Connection, sql: &str, args: &[SqlValue]) -> Result<Vec<Job>> {
        let mut st = c.prepare(sql)?;
        let rows = st.query_map(params_from_iter(args.iter()), Self::row_job)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    #[allow(clippy::too_many_arguments)]
    fn enqueue_sync(
        c: &Connection,
        kind: &str,
        chat_id: Option<&str>,
        ref_id: Option<&str>,
        seq: Option<i64>,
        dedupe_key: Option<&str>,
        available_at: Option<&str>,
        max_attempts: i64,
        payload: Option<&Value>,
    ) -> Result<Option<i64>> {
        let now = now_iso();
        let avail = available_at.filter(|s| !s.is_empty()).map(str::to_string).unwrap_or_else(|| now.clone());
        let n = c.execute(
            "INSERT INTO jobs(kind, chat_id, ref_id, seq, dedupe_key, status, available_at, max_attempts,
                                created_at, updated_at, payload)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
               ON CONFLICT(dedupe_key) DO NOTHING",
            params![kind, chat_id, ref_id, seq, dedupe_key, avail, max_attempts, now, now, j(payload)],
        )?;
        Ok(if n > 0 { Some(c.last_insert_rowid()) } else { None })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn enqueue_job(
        &self,
        kind: &str,
        chat_id: Option<&str>,
        ref_id: Option<&str>,
        seq: Option<i64>,
        dedupe_key: Option<&str>,
        available_at: Option<&str>,
        max_attempts: i64,
        payload: Option<&Value>,
    ) -> Result<Option<i64>> {
        self.tx(|c| Self::enqueue_sync(c, kind, chat_id, ref_id, seq, dedupe_key, available_at, max_attempts, payload))
    }

    /// Lease the oldest runnable job, plus (for `extract` jobs) up to `batch_size-1` directly following queued
    /// messages of the same chat (contiguous `seq`), so one LLM pass covers a turn.
    ///
    /// Per-chat ordering: a job is runnable only when no earlier-seq job of the same chat is queued or leased.
    /// Returns `[]` when nothing is runnable.
    pub fn lease_job(
        &self,
        worker_id: &str,
        lease_seconds: f64,
        kinds: Option<&[&str]>,
        batch_size: i64,
        batch_words: i64,
        batch_max_messages: i64,
    ) -> Result<Vec<Job>> {
        let now = now_iso();
        let runnable_at = precise_iso(utcnow());
        let until = iso(utcnow() + seconds(lease_seconds));
        self.tx(|c| {
            let mut sql = "SELECT j.* FROM jobs j
                         WHERE j.status = 'queued' AND j.available_at <= ?
                           AND (j.chat_id IS NULL OR NOT EXISTS (
                                 SELECT 1 FROM jobs j2
                                 WHERE j2.chat_id = j.chat_id AND j2.job_id <> j.job_id
                                   AND j2.status IN ('queued', 'leased') AND j2.seq < j.seq))"
                .to_string();
            let mut args = vec![text_v(&runnable_at)];
            if let Some(ks) = kinds.filter(|k| !k.is_empty()) {
                sql.push_str(&format!(" AND j.kind IN ({})", placeholders(ks.len())));
                args.extend(ks.iter().map(|k| text_v(k)));
            }
            sql.push_str(" ORDER BY j.available_at, j.job_id LIMIT 1");
            let Some(head) = Self::query_jobs(c, &sql, &args)?.into_iter().next() else {
                return Ok(Vec::new());
            };
            let mut leased = vec![head.job_id];
            // a job that already failed is retried with a smaller batch (halved per attempt) so one bad message
            // cannot poison its neighbours and the retried prompt differs from the failed one
            let shift = |v: i64| if head.attempts >= 63 || head.attempts < 0 { 0 } else { v >> head.attempts };
            let eff_batch = shift(batch_size).max(1);
            let eff_words = shift(batch_words).max(0);
            if head.kind == "extract" && eff_words > 0 && head.chat_id.is_some() {
                // word mode: keep pulling contiguous queued messages until the batch holds eff_words words
                let wtext: Option<String> = c
                    .query_row("SELECT text FROM messages WHERE message_id=?", [head.ref_id.as_deref()], |r| r.get(0))
                    .optional()?;
                let mut words = word_count(wtext.as_deref().unwrap_or("")) as i64;
                let mut st = c.prepare(
                    "SELECT j.job_id, j.seq, m.text AS _text FROM jobs j JOIN messages m ON m.message_id = j.ref_id
                           WHERE j.kind='extract' AND j.chat_id=? AND j.status='queued'
                           AND j.seq > ? AND j.available_at <= ? ORDER BY j.seq LIMIT ?",
                )?;
                let rows: Vec<(i64, i64, Option<String>)> = st
                    .query_map(
                        params![head.chat_id, head.seq, runnable_at, (batch_max_messages - 1).max(1)],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                    )?
                    .collect::<rusqlite::Result<_>>()?;
                let mut prev = head.seq.unwrap_or(0);
                for (job_id, seq, t) in rows {
                    if seq != prev + 1 || words >= eff_words {
                        break;
                    }
                    leased.push(job_id);
                    words += word_count(t.as_deref().unwrap_or("")) as i64;
                    prev = seq;
                }
            } else if head.kind == "extract" && eff_batch > 1 && head.chat_id.is_some() {
                let mut st = c.prepare(
                    "SELECT job_id, seq FROM jobs WHERE kind='extract' AND chat_id=? AND status='queued'
                           AND seq > ? AND available_at <= ? ORDER BY seq LIMIT ?",
                )?;
                let rows: Vec<(i64, i64)> = st
                    .query_map(params![head.chat_id, head.seq, runnable_at, eff_batch - 1], |r| {
                        Ok((r.get(0)?, r.get(1)?))
                    })?
                    .collect::<rusqlite::Result<_>>()?;
                let mut prev = head.seq.unwrap_or(0);
                for (job_id, seq) in rows {
                    if seq != prev + 1 {
                        break;
                    }
                    leased.push(job_id);
                    prev = seq;
                }
            }
            let mut upd = c.prepare(
                "UPDATE jobs SET status='leased', leased_until=?, worker_id=?, attempts=attempts+1, updated_at=? WHERE job_id=?",
            )?;
            for id in &leased {
                upd.execute(params![until, worker_id, now, id])?;
            }
            let mut out = Vec::with_capacity(leased.len());
            for id in &leased {
                out.extend(Self::query_jobs(c, "SELECT * FROM jobs WHERE job_id=?", &[SqlValue::Integer(*id)])?);
            }
            Ok(out)
        })
    }

    /// Return leased jobs to the queue immediately (graceful shutdown); the attempt stays charged. Fenced on
    /// `worker_id` when given.
    pub fn release_jobs(&self, job_ids: &[i64], worker_id: Option<&str>) -> Result<()> {
        if job_ids.is_empty() {
            return Ok(());
        }
        self.tx(|c| {
            for id in job_ids {
                match worker_id {
                    None => c.execute(
                        "UPDATE jobs SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=? WHERE job_id=? AND status='leased'",
                        params![now_iso(), id],
                    )?,
                    Some(w) => c.execute(
                        "UPDATE jobs SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=? WHERE job_id=? AND status='leased' AND worker_id=?",
                        params![now_iso(), id, w],
                    )?,
                };
            }
            Ok(())
        })
    }

    pub fn heartbeat(&self, job_ids: &[i64], lease_seconds: f64) -> Result<()> {
        if job_ids.is_empty() {
            return Ok(());
        }
        let until = iso(utcnow() + seconds(lease_seconds));
        self.tx(|c| {
            for id in job_ids {
                c.execute(
                    "UPDATE jobs SET leased_until=?, updated_at=? WHERE job_id=? AND status='leased'",
                    params![until, now_iso(), id],
                )?;
            }
            Ok(())
        })
    }

    pub fn complete_jobs(&self, job_ids: &[i64]) -> Result<()> {
        if job_ids.is_empty() {
            return Ok(());
        }
        self.tx(|c| Self::complete_jobs_sync(c, job_ids))
    }

    fn complete_jobs_sync(c: &Connection, ids: &[i64]) -> Result<()> {
        let now = now_iso();
        let mut st =
            c.prepare("UPDATE jobs SET status='done', finished_at=?, updated_at=?, leased_until=NULL WHERE job_id=?")?;
        for id in ids {
            st.execute(params![now, now, id])?;
        }
        Ok(())
    }

    /// Mark leased jobs failed: requeue with backoff, or `dead` when attempts are exhausted.
    ///
    /// Fenced: with `worker_id` given, a job that is no longer leased by that worker is left alone (a zombie whose
    /// lease expired cannot dead-letter or requeue a job another worker now holds). Returns
    /// `{job_id: new_status}` (`"lost"` for jobs the caller no longer holds).
    pub fn fail_jobs(
        &self,
        job_ids: &[i64],
        error: &str,
        backoff_seconds: f64,
        worker_id: Option<&str>,
    ) -> Result<IndexMap<i64, String>> {
        let mut result = IndexMap::new();
        if job_ids.is_empty() {
            return Ok(result);
        }
        let now = now_iso();
        let avail = precise_iso(utcnow() + seconds(backoff_seconds.max(0.0)));
        let err = crate::pycompat::py_prefix(error, 2000).to_string();
        self.tx(|c| {
            for id in job_ids {
                let row: Option<JobFailRow> = c
                    .query_row(
                        "SELECT attempts, max_attempts, ref_id, kind, status, worker_id FROM jobs WHERE job_id=?",
                        [id],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
                    )
                    .optional()?;
                let Some((attempts, max_attempts, ref_id, kind, status, holder)) = row else { continue };
                if let Some(w) = worker_id {
                    if status != "leased" || holder.as_deref() != Some(w) {
                        result.insert(*id, "lost".to_string());
                        continue;
                    }
                }
                if attempts >= max_attempts {
                    c.execute(
                        "UPDATE jobs SET status='dead', last_error=?, finished_at=?, updated_at=?, leased_until=NULL WHERE job_id=?",
                        params![err, now, now, id],
                    )?;
                    if kind == "extract" && ref_id.as_deref().is_some_and(|r| !r.is_empty()) {
                        c.execute(
                            "UPDATE messages SET status='failed', processed_at=? WHERE message_id=? AND status IN ('pending','processing')",
                            params![now, ref_id],
                        )?;
                    }
                    result.insert(*id, "dead".to_string());
                } else {
                    c.execute(
                        "UPDATE jobs SET status='queued', last_error=?, available_at=?, updated_at=?, leased_until=NULL, worker_id=NULL WHERE job_id=?",
                        params![err, avail, now, id],
                    )?;
                    result.insert(*id, "queued".to_string());
                }
            }
            Ok(())
        })?;
        Ok(result)
    }

    /// Return leased jobs whose lease expired (worker crash) to the queue.
    pub fn requeue_expired_leases(&self) -> Result<usize> {
        let now = now_iso();
        self.tx(|c| {
            let n = c.execute(
                "UPDATE jobs SET status='queued', leased_until=NULL, worker_id=NULL, updated_at=?, last_error=COALESCE(last_error, 'lease expired') WHERE status='leased' AND leased_until < ?",
                params![now, now],
            )?;
            if n > 0 {
                c.execute(
                    "UPDATE messages SET status='pending' WHERE status='processing' AND message_id IN (SELECT ref_id FROM jobs WHERE status='queued' AND kind='extract')",
                    [],
                )?;
            }
            Ok(n)
        })
    }

    pub fn job_counts(&self) -> Result<IndexMap<String, i64>> {
        self.group_counts("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    }

    pub fn pending_jobs(&self, kinds: Option<&[&str]>) -> Result<i64> {
        let mut sql = "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','leased')".to_string();
        let mut args = Vec::new();
        if let Some(ks) = kinds.filter(|k| !k.is_empty()) {
            sql.push_str(&format!(" AND kind IN ({})", placeholders(ks.len())));
            args.extend(ks.iter().map(|k| text_v(k)));
        }
        self.with_conn(|c| Ok(c.query_row(&sql, params_from_iter(args.iter()), |r| r.get(0))?))
    }

    pub fn next_available_at(&self) -> Result<Option<String>> {
        self.with_conn(|c| {
            Ok(c.query_row("SELECT MIN(available_at) AS a FROM jobs WHERE status='queued'", [], |r| r.get(0))?)
        })
    }

    pub fn get_job(&self, job_id: i64) -> Result<Option<Job>> {
        self.with_conn(|c| {
            Ok(Self::query_jobs(c, "SELECT * FROM jobs WHERE job_id=?", &[SqlValue::Integer(job_id)])?
                .into_iter()
                .next())
        })
    }

    pub fn list_jobs(&self, status: Option<&str>, limit: i64) -> Result<Vec<Job>> {
        self.with_conn(|c| match status.filter(|s| !s.is_empty()) {
            Some(s) => Self::query_jobs(
                c,
                "SELECT * FROM jobs WHERE status=? ORDER BY job_id DESC LIMIT ?",
                &[text_v(s), SqlValue::Integer(limit)],
            ),
            None => Self::query_jobs(c, "SELECT * FROM jobs ORDER BY job_id DESC LIMIT ?", &[SqlValue::Integer(limit)]),
        })
    }

    pub fn retry_dead_jobs(&self, limit: i64) -> Result<usize> {
        let now = now_iso();
        self.tx(|c| {
            let n = c.execute(
                "UPDATE jobs SET status='queued', attempts=0, available_at=?, updated_at=?, finished_at=NULL WHERE job_id IN (SELECT job_id FROM jobs WHERE status='dead' ORDER BY job_id LIMIT ?)",
                params![now, now, limit],
            )?;
            c.execute(
                "UPDATE messages SET status='pending' WHERE status='failed' AND message_id IN (SELECT ref_id FROM jobs WHERE status='queued' AND kind='extract')",
                [],
            )?;
            Ok(n)
        })
    }

    /// Delete audit rows older than `older_than_days` while always keeping the `keep_last` newest.
    pub fn prune_audit(&self, older_than_days: f64, keep_last: i64) -> Result<usize> {
        let cutoff = iso(utcnow() - seconds(older_than_days * 86400.0));
        self.tx(|c| {
            Ok(c.execute(
                "DELETE FROM audit_log WHERE at < ? AND id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
                params![cutoff, keep_last],
            )?)
        })
    }

    pub fn chat_text_chars(&self, chat_ids: &[String]) -> Result<i64> {
        let ids = dedup(chat_ids.iter().cloned());
        if ids.is_empty() {
            return Ok(0);
        }
        let mut total = 0;
        self.with_conn(|c| {
            for chunk in ids.chunks(500) {
                let sql = format!(
                    "SELECT COALESCE(SUM(length(text)), 0) AS n FROM messages WHERE chat_id IN ({})",
                    placeholders(chunk.len())
                );
                let n: i64 = c.query_row(&sql, params_from_iter(chunk.iter()), |r| r.get(0))?;
                total += n;
            }
            Ok(())
        })?;
        Ok(total)
    }

    pub fn memories_with_embedding_dim_other_than(&self, dim: i64, limit: i64) -> Result<Vec<Memory>> {
        self.with_conn(|c| {
            Self::query_memories(
                c,
                "SELECT * FROM memories WHERE status='active' AND embedding IS NOT NULL AND embedding_dim <> ? ORDER BY rid LIMIT ?",
                &[SqlValue::Integer(dim), SqlValue::Integer(limit)],
            )
        })
    }

    pub fn prune_jobs(&self, older_than_days: f64, statuses: &[&str]) -> Result<usize> {
        let cutoff = iso(utcnow() - seconds(older_than_days * 86400.0));
        let sql = format!("DELETE FROM jobs WHERE status IN ({}) AND finished_at < ?", placeholders(statuses.len()));
        let mut args: Vec<SqlValue> = statuses.iter().map(|s| text_v(s)).collect();
        args.push(SqlValue::Text(cutoff));
        self.tx(|c| Ok(c.execute(&sql, params_from_iter(args.iter()))?))
    }

    // ------------------------------------------------------------------ memories
    fn row_memory(r: &Row) -> rusqlite::Result<Memory> {
        let blob: Option<Vec<u8>> = r.get("embedding")?;
        Ok(Memory {
            memory_id: r.get("memory_id")?,
            text: r.get("text")?,
            kind: r.get("kind")?,
            subject: r.get("subject")?,
            subject_name: r.get("subject_name")?,
            speaker: r.get("speaker")?,
            chat_id: r.get("chat_id")?,
            importance: r.get("importance")?,
            confidence: r.get("confidence")?,
            event_time: r.get("event_time")?,
            event_time_precision: r.get("event_time_precision")?,
            observed_at: r.get("observed_at")?,
            created_at: r.get("created_at")?,
            updated_at: r.get("updated_at")?,
            status: r.get("status")?,
            superseded_by: r.get("superseded_by")?,
            version: r.get("version")?,
            text_hash: r.get("text_hash")?,
            embedding: blob.map(|b| unpack(&b)),
            access_count: r.get("access_count")?,
            last_accessed_at: r.get("last_accessed_at")?,
            metadata: jl(r.get("metadata")?, empty_obj()),
            source_message_ids: Vec::new(),
            entity_ids: Vec::new(),
        })
    }

    fn query_memories(c: &Connection, sql: &str, args: &[SqlValue]) -> Result<Vec<Memory>> {
        let mut st = c.prepare(sql)?;
        let rows = st.query_map(params_from_iter(args.iter()), Self::row_memory)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    fn insert_memory_sync(
        c: &Connection,
        m: &Memory,
        source_message_ids: &[String],
        entity_ids: &[String],
    ) -> Result<()> {
        let text_hash =
            if m.text_hash.is_empty() { content_hash(&[&m.text.to_lowercase()]) } else { m.text_hash.clone() };
        let (blob, dim) = match &m.embedding {
            None => (None, None),
            Some(e) => (Some(pack(e)), if e.is_empty() { None } else { Some(e.len() as i64) }),
        };
        let precision = if m.event_time_precision.is_empty() { "none" } else { m.event_time_precision.as_str() };
        c.execute(
            "INSERT INTO memories(memory_id, text, kind, subject, subject_name, speaker, chat_id, importance, confidence,
                                    event_time, event_time_precision, observed_at, created_at, updated_at, status,
                                    superseded_by, version, text_hash, embedding, embedding_dim, access_count,
                                    last_accessed_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                m.memory_id,
                m.text,
                m.kind,
                m.subject,
                m.subject_name,
                m.speaker,
                m.chat_id,
                m.importance,
                m.confidence,
                m.event_time,
                precision,
                m.observed_at,
                m.created_at,
                m.updated_at,
                m.status,
                m.superseded_by,
                m.version,
                text_hash,
                blob,
                dim,
                m.access_count,
                m.last_accessed_at,
                j(Some(&m.metadata)),
            ],
        )?;
        let mut st = c.prepare("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)")?;
        for s in dedup(source_message_ids.iter().cloned()) {
            st.execute(params![m.memory_id, s])?;
        }
        let mut st = c.prepare("INSERT OR IGNORE INTO memory_entities(memory_id, entity_id) VALUES (?, ?)")?;
        for e in dedup(entity_ids.iter().cloned()) {
            if !e.is_empty() {
                st.execute(params![m.memory_id, e])?;
            }
        }
        Ok(())
    }

    pub fn insert_memory(&self, m: &Memory, source_message_ids: &[String], entity_ids: &[String]) -> Result<()> {
        self.tx(|c| Self::insert_memory_sync(c, m, source_message_ids, entity_ids))?;
        self.bump();
        Ok(())
    }

    pub fn get_memory(&self, memory_id: &str, with_sources: bool) -> Result<Option<Memory>> {
        self.with_conn(|c| {
            let mut found = Self::query_memories(c, "SELECT * FROM memories WHERE memory_id=?", &[text_v(memory_id)])?;
            if with_sources {
                Self::attach_sources_sync(c, &mut found)?;
            }
            Ok(found.into_iter().next())
        })
    }

    fn attach_sources_sync(c: &Connection, mems: &mut [Memory]) -> Result<()> {
        let mut src = c.prepare(
            "SELECT ms.message_id FROM memory_sources ms JOIN messages msg ON msg.message_id = ms.message_id WHERE ms.memory_id=? ORDER BY msg.chat_id, msg.seq",
        )?;
        let mut ents = c.prepare("SELECT entity_id FROM memory_entities WHERE memory_id=? ORDER BY entity_id")?;
        for m in mems.iter_mut() {
            m.source_message_ids =
                src.query_map([&m.memory_id], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>()?;
            m.entity_ids = ents.query_map([&m.memory_id], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>()?;
        }
        Ok(())
    }

    /// Memories by id, in the order of the (deduplicated) ids given.
    pub fn get_memories_by_ids(&self, memory_ids: &[String], with_sources: bool) -> Result<Vec<Memory>> {
        let ids = dedup(memory_ids.iter().cloned());
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        self.with_conn(|c| {
            let mut out = Vec::new();
            for chunk in ids.chunks(500) {
                let sql = format!("SELECT * FROM memories WHERE memory_id IN ({})", placeholders(chunk.len()));
                let args: Vec<SqlValue> = chunk.iter().map(|s| text_v(s)).collect();
                out.extend(Self::query_memories(c, &sql, &args)?);
            }
            let order: IndexMap<&str, usize> = ids.iter().enumerate().map(|(i, m)| (m.as_str(), i)).collect();
            out.sort_by_key(|m| order.get(m.memory_id.as_str()).copied().unwrap_or(0));
            if with_sources {
                Self::attach_sources_sync(c, &mut out)?;
            }
            Ok(out)
        })
    }

    pub fn find_active_by_text_hash(&self, text_hash: &str) -> Result<Option<Memory>> {
        self.with_conn(|c| {
            Ok(Self::query_memories(
                c,
                "SELECT * FROM memories WHERE text_hash=? AND status='active' LIMIT 1",
                &[text_v(text_hash)],
            )?
            .into_iter()
            .next())
        })
    }

    pub fn update_memory(&self, memory_id: &str, fields: &[MemoryField]) -> Result<()> {
        if fields.is_empty() {
            return Ok(());
        }
        let mut sets: Vec<&str> = Vec::new();
        let mut args: Vec<SqlValue> = Vec::new();
        for f in fields {
            match f {
                MemoryField::Embedding(v) => {
                    sets.push("embedding=?");
                    args.push(match v {
                        Some(e) => SqlValue::Blob(pack(e)),
                        None => SqlValue::Null,
                    });
                    sets.push("embedding_dim=?");
                    args.push(match v {
                        Some(e) if !e.is_empty() => SqlValue::Integer(e.len() as i64),
                        _ => SqlValue::Null,
                    });
                }
                MemoryField::Metadata(v) => {
                    sets.push("metadata=?");
                    args.push(SqlValue::Text(j(Some(v))));
                }
                MemoryField::Text(t) => {
                    sets.push("text=?");
                    args.push(text_v(t));
                    sets.push("text_hash=?");
                    args.push(SqlValue::Text(content_hash(&[&normalize_ws(t).to_lowercase()])));
                }
                MemoryField::Kind(v) => {
                    sets.push("kind=?");
                    args.push(text_v(v));
                }
                MemoryField::Subject(v) => {
                    sets.push("subject=?");
                    args.push(text_v(v));
                }
                MemoryField::SubjectName(v) => {
                    sets.push("subject_name=?");
                    args.push(text_v(v));
                }
                MemoryField::Importance(v) => {
                    sets.push("importance=?");
                    args.push(SqlValue::Real(*v));
                }
                MemoryField::Confidence(v) => {
                    sets.push("confidence=?");
                    args.push(SqlValue::Real(*v));
                }
                MemoryField::EventTime(v) => {
                    sets.push("event_time=?");
                    args.push(opt_text(v.as_deref()));
                }
                MemoryField::EventTimePrecision(v) => {
                    sets.push("event_time_precision=?");
                    args.push(text_v(v));
                }
                MemoryField::Status(v) => {
                    sets.push("status=?");
                    args.push(text_v(v));
                }
                MemoryField::SupersededBy(v) => {
                    sets.push("superseded_by=?");
                    args.push(opt_text(v.as_deref()));
                }
                MemoryField::Version(v) => {
                    sets.push("version=?");
                    args.push(SqlValue::Integer(*v));
                }
                MemoryField::ObservedAt(v) => {
                    sets.push("observed_at=?");
                    args.push(text_v(v));
                }
            }
        }
        sets.push("updated_at=?");
        args.push(SqlValue::Text(now_iso()));
        args.push(text_v(memory_id));
        let sql = format!("UPDATE memories SET {} WHERE memory_id=?", sets.join(", "));
        self.tx(|c| {
            c.execute(&sql, params_from_iter(args.iter()))?;
            Ok(())
        })?;
        self.bump();
        Ok(())
    }

    /// Follow `superseded_by` to the memory that currently replaces `memory_id` (itself if active).
    fn current_head_sync(c: &Connection, memory_id: &str, limit: usize) -> Result<String> {
        let mut cur = memory_id.to_string();
        for _ in 0..limit {
            let row: Option<(String, Option<String>)> = c
                .query_row("SELECT status, superseded_by FROM memories WHERE memory_id=?", [&cur], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .optional()?;
            match row {
                Some((status, Some(next))) if status == "superseded" && !next.is_empty() && next != cur => cur = next,
                _ => return Ok(cur),
            }
        }
        Ok(cur)
    }

    fn supersede_sync(c: &Connection, old_id: &str, new_id: &str, link_type: &str) -> Result<()> {
        let now = now_iso();
        c.execute(
            "UPDATE memories SET status='superseded', superseded_by=?, updated_at=? WHERE memory_id=? AND status='active'",
            params![new_id, now, old_id],
        )?;
        c.execute(
            "INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, 1.0, ?)",
            params![new_id, old_id, link_type, now],
        )?;
        // relations that were grounded only in the old memory follow it into history
        c.execute(
            "UPDATE relations SET status='superseded', updated_at=? WHERE memory_id=? AND status='active' AND observation_count <= 1",
            params![now, old_id],
        )?;
        Ok(())
    }

    pub fn supersede(&self, old_id: &str, new_id: &str, link_type: &str) -> Result<()> {
        self.tx(|c| Self::supersede_sync(c, old_id, new_id, link_type))?;
        self.bump();
        Ok(())
    }

    pub fn retract(&self, memory_id: &str, reason: &str) -> Result<bool> {
        let r = self.tx(|c| {
            let n = c.execute(
                "UPDATE memories SET status='retracted', updated_at=? WHERE memory_id=? AND status<>'retracted'",
                params![now_iso(), memory_id],
            )?;
            c.execute(
                "UPDATE relations SET status='retracted', updated_at=? WHERE memory_id=?",
                params![now_iso(), memory_id],
            )?;
            if !reason.is_empty() {
                Self::log_sync(c, "retract", Some(memory_id), Some(&obj([("reason", Value::String(reason.into()))])))?;
            }
            Ok(n > 0)
        })?;
        self.bump();
        Ok(r)
    }

    pub fn add_link(&self, source_id: &str, target_id: &str, link_type: &str, weight: f64) -> Result<()> {
        self.tx(|c| {
            c.execute(
                "INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
                params![source_id, target_id, link_type, weight, now_iso()],
            )?;
            Ok(())
        })?;
        self.bump();
        Ok(())
    }

    pub fn links_for(&self, memory_id: &str) -> Result<Vec<MemoryLink>> {
        self.with_conn(|c| {
            let mut st = c.prepare(
                "SELECT * FROM memory_links WHERE source_id=? OR target_id=? ORDER BY weight DESC, created_at",
            )?;
            let rows = st.query_map(params![memory_id, memory_id], |r| {
                Ok(MemoryLink {
                    source_id: r.get("source_id")?,
                    target_id: r.get("target_id")?,
                    link_type: r.get("link_type")?,
                    weight: r.get("weight")?,
                    created_at: r.get("created_at")?,
                })
            })?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    pub fn add_memory_sources(&self, memory_id: &str, message_ids: &[String]) -> Result<()> {
        self.tx(|c| {
            let mut st = c.prepare("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)")?;
            for s in dedup(message_ids.iter().cloned()) {
                st.execute(params![memory_id, s])?;
            }
            Ok(())
        })
    }

    pub fn touch_access(&self, memory_ids: &[String]) -> Result<()> {
        let ids = dedup(memory_ids.iter().cloned());
        if ids.is_empty() {
            return Ok(());
        }
        let now = now_iso();
        self.tx(|c| {
            let mut st =
                c.prepare("UPDATE memories SET access_count=access_count+1, last_accessed_at=? WHERE memory_id=?")?;
            for id in &ids {
                st.execute(params![now, id])?;
            }
            Ok(())
        })
    }

    pub fn list_memories(&self, q: &MemoryQuery) -> Result<Vec<Memory>> {
        const ORDERS: [&str; 5] =
            ["observed_at DESC", "observed_at ASC", "importance DESC", "created_at DESC", "created_at ASC"];
        let mut sql = "SELECT m.* FROM memories m WHERE 1=1".to_string();
        let mut args: Vec<SqlValue> = Vec::new();
        if let Some(s) = q.status.as_deref().filter(|s| !s.is_empty()) {
            sql.push_str(" AND status=?");
            args.push(text_v(s));
        }
        if let Some(s) = q.subject.as_deref().filter(|s| !s.is_empty()) {
            sql.push_str(" AND subject=?");
            let resolved = self.resolve_alias(s)?.unwrap_or_else(|| norm_entity(s));
            args.push(SqlValue::Text(resolved));
        }
        if let Some(s) = q.speaker.as_deref().filter(|s| !s.is_empty()) {
            sql.push_str(" AND speaker=?");
            args.push(text_v(s));
        }
        if let Some(s) = q.chat_id.as_deref().filter(|s| !s.is_empty()) {
            sql.push_str(" AND chat_id=?");
            args.push(text_v(s));
        }
        if let Some(kl) = q.kinds.as_ref().filter(|k| !k.is_empty()) {
            sql.push_str(&format!(" AND kind IN ({})", placeholders(kl.len())));
            args.extend(kl.iter().map(|k| text_v(k)));
        }
        sql.push_str(&time_predicates(non_empty(&q.since), non_empty(&q.until), &mut args));
        if !ORDERS.contains(&q.order.as_str()) {
            return Err(MemoryError::Value("unsupported order".into()));
        }
        sql.push_str(&format!(" ORDER BY {}, rid", q.order));
        if let Some(l) = q.limit {
            sql.push_str(" LIMIT ? OFFSET ?");
            args.push(SqlValue::Integer(l));
            args.push(SqlValue::Integer(q.offset));
        }
        self.with_conn(|c| {
            let mut out = Self::query_memories(c, &sql, &args)?;
            if q.with_sources {
                Self::attach_sources_sync(c, &mut out)?;
            }
            Ok(out)
        })
    }

    pub fn memory_counts(&self) -> Result<Value> {
        let to_obj = |m: IndexMap<String, i64>| obj(m.into_iter().map(|(k, v)| (k, Value::from(v))));
        Ok(obj([
            ("by_status", to_obj(self.group_counts("SELECT status, COUNT(*) AS n FROM memories GROUP BY status")?)),
            (
                "active_by_kind",
                to_obj(self.group_counts("SELECT kind, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY kind")?),
            ),
            (
                "active_by_subject",
                to_obj(self.group_counts(
                    "SELECT subject, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY subject ORDER BY n DESC LIMIT 20",
                )?),
            ),
        ]))
    }

    /// (memory_id, subject, embedding) for active memories with embeddings, in insertion order.
    pub fn active_embedding_rows(&self) -> Result<Vec<(String, String, Vec<f64>)>> {
        self.with_conn(|c| {
            let mut st = c.prepare(
                "SELECT memory_id, subject, speaker, chat_id, kind, COALESCE(event_time, observed_at) AS t, embedding FROM memories WHERE status='active' AND embedding IS NOT NULL ORDER BY rid",
            )?;
            let rows = st.query_map([], |r| {
                let blob: Vec<u8> = r.get(6)?;
                Ok((r.get(0)?, r.get(1)?, unpack(&blob)))
            })?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    /// Light-weight rows for the retrieval index (embedding may be None). `status=None` returns every status;
    /// `updated_after` returns only rows written since that ISO timestamp (for incremental index refresh).
    pub fn index_rows(&self, status: Option<&str>, updated_after: Option<&str>) -> Result<Vec<IndexRow>> {
        let mut sql = "SELECT memory_id, subject, speaker, chat_id, kind, importance, observed_at, event_time, event_time_precision, status, updated_at, embedding FROM memories WHERE 1=1".to_string();
        let mut args = Vec::new();
        if let Some(s) = status.filter(|s| !s.is_empty()) {
            sql.push_str(" AND status=?");
            args.push(text_v(s));
        }
        if let Some(u) = updated_after.filter(|s| !s.is_empty()) {
            sql.push_str(" AND updated_at >= ?");
            args.push(text_v(u));
        }
        sql.push_str(" ORDER BY rid");
        self.with_conn(|c| {
            let mut st = c.prepare(&sql)?;
            let rows = st.query_map(params_from_iter(args.iter()), |r| {
                let blob: Option<Vec<u8>> = r.get(11)?;
                Ok(IndexRow {
                    memory_id: r.get(0)?,
                    subject: r.get(1)?,
                    speaker: r.get(2)?,
                    chat_id: r.get(3)?,
                    kind: r.get(4)?,
                    importance: r.get(5)?,
                    observed_at: r.get(6)?,
                    event_time: r.get(7)?,
                    event_time_precision: r.get(8)?,
                    status: r.get(9)?,
                    updated_at: r.get(10)?,
                    embedding: blob.map(|b| unpack(&b)),
                })
            })?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    pub fn entity_ids_for_memories(&self, memory_ids: &[String]) -> Result<IndexMap<String, Vec<String>>> {
        let ids = dedup(memory_ids.iter().cloned());
        let mut out: IndexMap<String, Vec<String>> = ids.iter().map(|i| (i.clone(), Vec::new())).collect();
        self.with_conn(|c| {
            for chunk in ids.chunks(500) {
                let sql = format!(
                    "SELECT memory_id, entity_id FROM memory_entities WHERE memory_id IN ({}) ORDER BY entity_id",
                    placeholders(chunk.len())
                );
                let mut st = c.prepare(&sql)?;
                let rows = st.query_map(params_from_iter(chunk.iter()), |r| {
                    Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
                })?;
                for row in rows {
                    let (m, e) = row?;
                    if let Some(v) = out.get_mut(&m) {
                        v.push(e);
                    }
                }
            }
            Ok(())
        })?;
        Ok(out)
    }

    /// Chain of memories this one superseded (newest first), following 'supersedes'/'contradicts' links.
    pub fn history(&self, memory_id: &str, limit: usize) -> Result<Vec<Memory>> {
        let mut out = Vec::new();
        let mut cur = memory_id.to_string();
        let mut seen: IndexSet<String> = IndexSet::new();
        seen.insert(cur.clone());
        while out.len() < limit {
            let target: Option<String> = self.with_conn(|c| {
                Ok(c.query_row(
                    "SELECT target_id FROM memory_links WHERE source_id=? AND link_type IN ('supersedes','contradicts') ORDER BY created_at DESC LIMIT 1",
                    [&cur],
                    |r| r.get(0),
                )
                .optional()?)
            })?;
            let Some(t) = target else { break };
            if seen.contains(&t) {
                break;
            }
            let Some(m) = self.get_memory(&t, false)? else { break };
            seen.insert(m.memory_id.clone());
            cur = m.memory_id.clone();
            if m.status != "retracted" {
                // forgotten memories stay forgotten
                out.push(m);
            }
        }
        Ok(out)
    }

    pub fn memories_missing_embedding(&self, limit: i64) -> Result<Vec<Memory>> {
        self.with_conn(|c| {
            Self::query_memories(
                c,
                "SELECT * FROM memories WHERE status='active' AND embedding IS NULL ORDER BY rid LIMIT ?",
                &[SqlValue::Integer(limit)],
            )
        })
    }

    /// Keyword channel over memory text: FTS5 bm25 when available, else in-process BM25. Filters are pushed into
    /// the query so a filtered search is not starved by the global LIMIT.
    pub fn keyword_candidates(
        &self,
        query: &str,
        limit: i64,
        status: &str,
        f: &ChannelFilter,
    ) -> Result<Vec<(String, f64)>> {
        let toks = tokenize(query);
        if toks.is_empty() {
            return Ok(Vec::new());
        }
        if self.has_fts() {
            let m = fts_match_string(query);
            if m.is_empty() {
                return Ok(Vec::new());
            }
            let mut sql = "SELECT m.memory_id AS id, bm25(memories_fts) AS s FROM memories_fts f JOIN memories m ON m.rid = f.rowid WHERE memories_fts MATCH ? AND m.status = ?".to_string();
            let mut args = vec![SqlValue::Text(m), text_v(status)];
            if let Some(s) = non_empty(&f.subject) {
                sql.push_str(" AND m.subject = ?");
                args.push(text_v(s));
            }
            if let Some(s) = non_empty(&f.chat_id) {
                sql.push_str(" AND m.chat_id = ?");
                args.push(text_v(s));
            }
            if let Some(s) = non_empty(&f.speaker) {
                sql.push_str(" AND m.speaker = ?");
                args.push(text_v(s));
            }
            if let Some(kl) = f.kinds.as_ref().filter(|k| !k.is_empty()) {
                sql.push_str(&format!(" AND m.kind IN ({})", placeholders(kl.len())));
                args.extend(kl.iter().map(|k| text_v(k)));
            }
            sql.push_str(&time_predicates(non_empty(&f.since), non_empty(&f.until), &mut args));
            sql.push_str(" ORDER BY s LIMIT ?");
            args.push(SqlValue::Integer(limit));
            return self.with_conn(|c| {
                let mut st = c.prepare(&sql)?;
                let rows = st
                    .query_map(params_from_iter(args.iter()), |r| Ok((r.get::<_, String>(0)?, -r.get::<_, f64>(1)?)))?;
                Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
            });
        }
        // fallback: BM25 over memories of `status`, cached per cache key (as before, the cache is not keyed on
        // status, so a superseded-status query right after an active one reuses the active corpus)
        let key = self.cache_key();
        let needs_build = {
            let cache = self.bm25_cache.lock().unwrap_or_else(|e| e.into_inner());
            cache.as_ref().is_none_or(|(k, _, _)| *k != key)
        };
        if needs_build {
            let rows: Vec<(String, String)> = self.with_conn(|c| {
                let mut st = c.prepare("SELECT memory_id, text FROM memories WHERE status=? ORDER BY rid")?;
                let rows = st.query_map([status], |r| Ok((r.get(0)?, r.get(1)?)))?;
                Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
            })?;
            let ids: Vec<String> = rows.iter().map(|r| r.0.clone()).collect();
            let docs: Vec<Vec<String>> = rows.iter().map(|r| tokenize(&r.1)).collect();
            let bm = if docs.is_empty() { None } else { Bm25Okapi::new(&docs) };
            *self.bm25_cache.lock().unwrap_or_else(|e| e.into_inner()) = Some((key, ids, bm));
        }
        let allowed: Option<IndexSet<String>> = if f.any() {
            let q = MemoryQuery {
                status: Some(status.to_string()),
                subject: f.subject.clone(),
                chat_id: f.chat_id.clone(),
                speaker: f.speaker.clone(),
                kinds: f.kinds.clone(),
                since: f.since.clone(),
                until: f.until.clone(),
                ..Default::default()
            };
            Some(self.list_memories(&q)?.into_iter().map(|m| m.memory_id).collect())
        } else {
            None
        };
        let cache = self.bm25_cache.lock().unwrap_or_else(|e| e.into_inner());
        let Some((_, ids, Some(bm))) = cache.as_ref() else { return Ok(Vec::new()) };
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let ok = |id: &String| allowed.as_ref().is_none_or(|a| a.contains(id));
        let scores = bm.get_scores(&toks);
        let mut ranked: Vec<(String, f64)> = ids
            .iter()
            .zip(scores.iter())
            .filter(|(id, s)| **s > 0.0 && ok(id))
            .map(|(id, s)| (id.clone(), *s))
            .collect();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        if ranked.is_empty() {
            // tiny corpora: BM25's idf goes non-positive; fall back to plain token containment
            let qset: IndexSet<&String> = toks.iter().collect();
            let mut alt: Vec<(String, f64)> = ids
                .iter()
                .enumerate()
                .filter(|(_, id)| ok(id))
                .map(|(i, id)| {
                    let n = bm.doc_freqs[i].keys().filter(|k| qset.contains(k)).count();
                    (id.clone(), n as f64)
                })
                .filter(|(_, s)| *s > 0.0)
                .collect();
            alt.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            ranked = alt;
        }
        ranked.truncate(limit.max(0) as usize);
        Ok(ranked)
    }

    // ------------------------------------------------------------------ entities & relations
    fn row_entity(r: &Row) -> rusqlite::Result<Entity> {
        Ok(Entity {
            entity_id: r.get("entity_id")?,
            name: r.get("name")?,
            etype: r.get("type")?,
            mention_count: r.get("mention_count")?,
            first_seen_at: r.get("first_seen_at")?,
            last_seen_at: r.get("last_seen_at")?,
            metadata: jl(r.get("metadata")?, empty_obj()),
            aliases: Vec::new(),
        })
    }

    fn query_entities(c: &Connection, sql: &str, args: &[SqlValue]) -> Result<Vec<Entity>> {
        let mut st = c.prepare(sql)?;
        let rows = st.query_map(params_from_iter(args.iter()), Self::row_entity)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    #[allow(clippy::too_many_arguments)]
    fn upsert_entity_sync(
        c: &Connection,
        entity_id: &str,
        name: &str,
        etype: &str,
        seen_at: Option<&str>,
        mentions: i64,
        aliases: &[String],
    ) -> Result<()> {
        let now = now_iso();
        let seen = seen_at.filter(|s| !s.is_empty()).map(str::to_string).unwrap_or(now);
        c.execute(
            "INSERT INTO entities(entity_id, name, type, mention_count, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET
                 mention_count = entities.mention_count + excluded.mention_count,
                 type = CASE WHEN entities.type = '' THEN excluded.type ELSE entities.type END,
                 name = CASE WHEN length(excluded.name) > length(entities.name) AND lower(excluded.name) = lower(entities.name)
                             THEN excluded.name ELSE entities.name END,
                 first_seen_at = CASE WHEN entities.first_seen_at IS NULL OR excluded.first_seen_at < entities.first_seen_at
                                      THEN excluded.first_seen_at ELSE entities.first_seen_at END,
                 last_seen_at = CASE WHEN entities.last_seen_at IS NULL OR excluded.last_seen_at > entities.last_seen_at
                                     THEN excluded.last_seen_at ELSE entities.last_seen_at END",
            params![entity_id, name, etype, mentions, seen, seen],
        )?;
        for a in aliases {
            let a = norm_entity(a);
            if !a.is_empty() && a != entity_id {
                c.execute(
                    "INSERT OR IGNORE INTO entity_aliases(alias, entity_id) VALUES (?, ?)",
                    params![a, entity_id],
                )?;
            }
        }
        Ok(())
    }

    pub fn upsert_entity(
        &self,
        entity_id: &str,
        name: &str,
        etype: &str,
        seen_at: Option<&str>,
        mentions: i64,
        aliases: &[String],
    ) -> Result<Option<Entity>> {
        self.tx(|c| Self::upsert_entity_sync(c, entity_id, name, etype, seen_at, mentions, aliases))?;
        self.bump();
        self.get_entity(entity_id)
    }

    pub fn get_entity(&self, entity_id: &str) -> Result<Option<Entity>> {
        self.with_conn(|c| {
            let Some(mut e) =
                Self::query_entities(c, "SELECT * FROM entities WHERE entity_id=?", &[text_v(entity_id)])?
                    .into_iter()
                    .next()
            else {
                return Ok(None);
            };
            let mut st = c.prepare("SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias")?;
            e.aliases = st.query_map([entity_id], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>()?;
            Ok(Some(e))
        })
    }

    pub fn resolve_alias(&self, alias: &str) -> Result<Option<String>> {
        let key = norm_entity(alias);
        if key.is_empty() {
            return Ok(None);
        }
        self.with_conn(|c| {
            if let Some(id) =
                c.query_row("SELECT entity_id FROM entities WHERE entity_id=?", [&key], |r| r.get(0)).optional()?
            {
                return Ok(Some(id));
            }
            Ok(c.query_row("SELECT entity_id FROM entity_aliases WHERE alias=?", [&key], |r| r.get(0)).optional()?)
        })
    }

    pub fn add_alias(&self, alias: &str, entity_id: &str) -> Result<()> {
        self.tx(|c| {
            c.execute(
                "INSERT OR REPLACE INTO entity_aliases(alias, entity_id) VALUES (?, ?)",
                params![norm_entity(alias), entity_id],
            )?;
            Ok(())
        })?;
        self.bump();
        Ok(())
    }

    pub fn top_entities(&self, limit: i64, etype: Option<&str>) -> Result<Vec<Entity>> {
        self.with_conn(|c| match etype.filter(|s| !s.is_empty()) {
            Some(t) => Self::query_entities(
                c,
                "SELECT * FROM entities WHERE type=? ORDER BY mention_count DESC, entity_id LIMIT ?",
                &[text_v(t), SqlValue::Integer(limit)],
            ),
            None => Self::query_entities(
                c,
                "SELECT * FROM entities ORDER BY mention_count DESC, entity_id LIMIT ?",
                &[SqlValue::Integer(limit)],
            ),
        })
    }

    pub fn entities_for_chat(&self, chat_id: &str, limit: i64) -> Result<Vec<Entity>> {
        self.with_conn(|c| {
            Self::query_entities(
                c,
                "SELECT e.*, COUNT(*) AS n FROM entities e
               JOIN memory_entities me ON me.entity_id = e.entity_id
               JOIN memories m ON m.memory_id = me.memory_id
               WHERE m.chat_id = ? GROUP BY e.entity_id ORDER BY n DESC, e.mention_count DESC LIMIT ?",
                &[text_v(chat_id), SqlValue::Integer(limit)],
            )
        })
    }

    /// {alias_or_key: entity_id} for every entity and alias (for in-query entity matching).
    pub fn all_entity_keys(&self) -> Result<IndexMap<String, String>> {
        self.with_conn(|c| {
            let mut out: IndexMap<String, String> = IndexMap::new();
            let mut st = c.prepare("SELECT entity_id FROM entities")?;
            for id in st.query_map([], |r| r.get::<_, String>(0))? {
                let id = id?;
                out.insert(id.clone(), id);
            }
            let mut st = c.prepare("SELECT alias, entity_id FROM entity_aliases")?;
            for row in st.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
                let (a, e) = row?;
                out.insert(a, e);
            }
            Ok(out)
        })
    }

    pub fn memories_for_entities(
        &self,
        entity_ids: &[String],
        limit: i64,
        status: &str,
        f: &ChannelFilter,
    ) -> Result<Vec<(String, String)>> {
        let ids = dedup(entity_ids.iter().filter(|e| !e.is_empty()).cloned());
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut sql = format!(
            "SELECT me.memory_id, me.entity_id FROM memory_entities me JOIN memories m ON m.memory_id = me.memory_id WHERE me.entity_id IN ({}) AND m.status = ?",
            placeholders(ids.len())
        );
        let mut args: Vec<SqlValue> = ids.iter().map(|s| text_v(s)).collect();
        args.push(text_v(status));
        if let Some(s) = non_empty(&f.subject) {
            sql.push_str(" AND m.subject = ?");
            args.push(text_v(s));
        }
        if let Some(s) = non_empty(&f.chat_id) {
            sql.push_str(" AND m.chat_id = ?");
            args.push(text_v(s));
        }
        if let Some(kl) = f.kinds.as_ref().filter(|k| !k.is_empty()) {
            sql.push_str(&format!(" AND m.kind IN ({})", placeholders(kl.len())));
            args.extend(kl.iter().map(|k| text_v(k)));
        }
        sql.push_str(&time_predicates(non_empty(&f.since), non_empty(&f.until), &mut args));
        sql.push_str(" ORDER BY m.importance DESC, m.observed_at DESC LIMIT ?");
        args.push(SqlValue::Integer(limit));
        self.with_conn(|c| {
            let mut st = c.prepare(&sql)?;
            let rows = st.query_map(params_from_iter(args.iter()), |r| Ok((r.get(0)?, r.get(1)?)))?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    pub fn entity_memory_counts(&self, entity_ids: &[String]) -> Result<IndexMap<String, i64>> {
        let ids = dedup(entity_ids.iter().filter(|e| !e.is_empty()).cloned());
        if ids.is_empty() {
            return Ok(IndexMap::new());
        }
        let sql = format!(
            "SELECT me.entity_id, COUNT(*) AS n FROM memory_entities me JOIN memories m ON m.memory_id = me.memory_id
                WHERE me.entity_id IN ({}) AND m.status='active' GROUP BY me.entity_id",
            placeholders(ids.len())
        );
        self.with_conn(|c| {
            let mut st = c.prepare(&sql)?;
            let rows = st.query_map(params_from_iter(ids.iter()), |r| Ok((r.get(0)?, r.get(1)?)))?;
            Ok(rows.collect::<rusqlite::Result<IndexMap<_, _>>>()?)
        })
    }

    fn row_relation(r: &Row) -> rusqlite::Result<Relation> {
        Ok(Relation {
            relation_id: r.get("relation_id")?,
            subject_id: r.get("subject_id")?,
            predicate: r.get("predicate")?,
            object_id: r.get("object_id")?,
            confidence: r.get("confidence")?,
            chat_id: r.get("chat_id")?,
            memory_id: r.get("memory_id")?,
            message_id: r.get("message_id")?,
            status: r.get("status")?,
            observation_count: r.get("observation_count")?,
            observed_at: r.get("observed_at")?,
            created_at: r.get("created_at")?,
            updated_at: r.get("updated_at")?,
            metadata: jl(r.get("metadata")?, empty_obj()),
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn upsert_relation_sync(
        c: &Connection,
        subject_id: &str,
        predicate: &str,
        object_id: &str,
        confidence: f64,
        chat_id: &str,
        memory_id: Option<&str>,
        message_id: Option<&str>,
        observed_at: Option<&str>,
    ) -> Result<String> {
        let now = now_iso();
        let observed = observed_at.filter(|s| !s.is_empty()).map(str::to_string).unwrap_or_else(|| now.clone());
        let rid = new_id("rel");
        let functional = FUNCTIONAL_PREDICATES.contains(&predicate);
        // single-valued predicate: the observation with the LATEST source time wins, whatever the ingestion order
        let mut newer_exists = false;
        if functional {
            let t: Option<String> = c.query_row(
                "SELECT MAX(COALESCE(observed_at, created_at)) AS t FROM relations WHERE subject_id=? AND predicate=? AND object_id<>? AND status='active'",
                params![subject_id, predicate, object_id],
                |r| r.get(0),
            )?;
            newer_exists = t.is_some_and(|t| !t.is_empty() && t > observed);
        }
        let status = if newer_exists { "superseded" } else { "active" };
        c.execute(
            "INSERT INTO relations(relation_id, subject_id, predicate, object_id, confidence, chat_id, memory_id, message_id,
                                     status, observation_count, observed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(subject_id, predicate, object_id) DO UPDATE SET
                 observation_count = relations.observation_count + 1,
                 confidence = MAX(relations.confidence, excluded.confidence),
                 status = CASE WHEN excluded.status = 'superseded' THEN relations.status ELSE 'active' END,
                 memory_id = COALESCE(excluded.memory_id, relations.memory_id),
                 message_id = COALESCE(excluded.message_id, relations.message_id),
                 observed_at = MAX(COALESCE(relations.observed_at, ''), excluded.observed_at),
                 updated_at = excluded.updated_at",
            params![rid, subject_id, predicate, object_id, confidence, chat_id, memory_id, message_id, status, observed, now, now],
        )?;
        let id: String = c.query_row(
            "SELECT relation_id FROM relations WHERE subject_id=? AND predicate=? AND object_id=?",
            params![subject_id, predicate, object_id],
            |r| r.get(0),
        )?;
        if functional && !newer_exists {
            c.execute(
                "UPDATE relations SET status='superseded', updated_at=? WHERE subject_id=? AND predicate=? AND object_id<>? AND status='active' AND COALESCE(observed_at, created_at) <= ?",
                params![now, subject_id, predicate, object_id, observed],
            )?;
        }
        Ok(id)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn upsert_relation(
        &self,
        subject_id: &str,
        predicate: &str,
        object_id: &str,
        confidence: f64,
        chat_id: &str,
        memory_id: Option<&str>,
        message_id: Option<&str>,
        observed_at: Option<&str>,
    ) -> Result<Relation> {
        let rid = self.tx(|c| {
            Self::upsert_relation_sync(
                c,
                subject_id,
                predicate,
                object_id,
                confidence,
                chat_id,
                memory_id,
                message_id,
                observed_at,
            )
        })?;
        self.bump();
        self.with_conn(|c| {
            Ok(c.query_row("SELECT * FROM relations WHERE relation_id=?", [&rid], Self::row_relation)?)
        })
    }

    pub fn relations_for(&self, entity_id: &str, status: Option<&str>, limit: i64) -> Result<Vec<Relation>> {
        let mut sql = "SELECT * FROM relations WHERE (subject_id=? OR object_id=?)".to_string();
        let mut args = vec![text_v(entity_id), text_v(entity_id)];
        if let Some(s) = status.filter(|s| !s.is_empty()) {
            sql.push_str(" AND status=?");
            args.push(text_v(s));
        }
        sql.push_str(" ORDER BY observation_count DESC, confidence DESC, updated_at DESC LIMIT ?");
        args.push(SqlValue::Integer(limit));
        self.with_conn(|c| {
            let mut st = c.prepare(&sql)?;
            let rows = st.query_map(params_from_iter(args.iter()), Self::row_relation)?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    /// 1-hop typed neighbourhood: {entity_id: [(neighbour_id, predicate, confidence)]} over active relations.
    pub fn neighbors(&self, entity_ids: &[String], limit: i64) -> Result<Neighbors> {
        let ids = dedup(entity_ids.iter().filter(|e| !e.is_empty()).cloned());
        if ids.is_empty() {
            return Ok(IndexMap::new());
        }
        let ph = placeholders(ids.len());
        let sql = format!(
            "SELECT subject_id, predicate, object_id, confidence FROM relations WHERE status='active' AND (subject_id IN ({ph}) OR object_id IN ({ph})) ORDER BY observation_count DESC, confidence DESC LIMIT ?"
        );
        let mut args: Vec<SqlValue> = ids.iter().map(|s| text_v(s)).collect();
        args.extend(ids.iter().map(|s| text_v(s)));
        args.push(SqlValue::Integer(limit));
        let rows: Vec<(String, String, String, f64)> = self.with_conn(|c| {
            let mut st = c.prepare(&sql)?;
            let rows =
                st.query_map(params_from_iter(args.iter()), |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })?;
        let mut out: IndexMap<String, Vec<(String, String, f64)>> =
            ids.iter().map(|i| (i.clone(), Vec::new())).collect();
        for (s, p, o, conf) in rows {
            if let Some(v) = out.get_mut(&s) {
                v.push((o.clone(), p.clone(), conf));
            }
            if o != s {
                if let Some(v) = out.get_mut(&o) {
                    v.push((s.clone(), format!("inverse:{p}"), conf));
                }
            }
        }
        Ok(out)
    }

    pub fn relation_counts(&self) -> Result<IndexMap<String, i64>> {
        self.group_counts("SELECT status, COUNT(*) AS n FROM relations GROUP BY status")
    }

    pub fn entity_count(&self) -> Result<i64> {
        self.with_conn(|c| Ok(c.query_row("SELECT COUNT(*) AS n FROM entities", [], |r| r.get(0))?))
    }

    // ------------------------------------------------------------------ atomic plan application
    /// Commit one extraction plan atomically (memories, entities, relations, links, sources, message + job status).
    ///
    /// This is the only write path the worker uses for extraction results, which is what makes a crash mid-job
    /// harmless: either everything below is visible, or nothing is. When `worker_id` is given the commit is
    /// fenced: every job in the plan must still be leased by that worker, otherwise nothing is written and
    /// [`MemoryError::LostLease`] is returned (the job was re-leased by someone else after this worker's lease
    /// expired, and that other worker owns the result now).
    pub fn apply_plan(&self, plan: &ExtractionPlan, worker_id: Option<&str>) -> Result<PlanCounts> {
        let mut counts = PlanCounts::default();
        let now = now_iso();
        let r = self.tx(|c| {
            if let (Some(w), false) = (worker_id, plan.job_ids.is_empty()) {
                let sql = format!(
                    "SELECT COUNT(*) AS n FROM jobs WHERE job_id IN ({}) AND status='leased' AND worker_id=?",
                    placeholders(plan.job_ids.len())
                );
                let mut args: Vec<SqlValue> = plan.job_ids.iter().map(|j| SqlValue::Integer(*j)).collect();
                args.push(text_v(w));
                let held: i64 = c.query_row(&sql, params_from_iter(args.iter()), |r| r.get(0))?;
                if held != plan.job_ids.len() as i64 {
                    return Err(MemoryError::LostLease(format!(
                        "{} of {} jobs no longer leased by {}",
                        plan.job_ids.len() as i64 - held,
                        plan.job_ids.len(),
                        w
                    )));
                }
            }
            for e in &plan.entities {
                Self::upsert_entity_sync(c, &e.entity_id, &e.name, &e.etype, e.seen_at.as_deref(), e.mentions, &e.aliases)?;
                counts.entities += 1;
            }
            for (m, src_ids, ent_ids) in &plan.new_memories {
                Self::insert_memory_sync(c, m, src_ids, ent_ids)?;
                counts.memories_added += 1;
            }
            for (old_id, new_mid, link_type) in &plan.supersedes {
                // the target may have been superseded by another chat's plan while this one was being computed:
                // follow the chain to the current head and decide by observation time
                let mut head = Self::current_head_sync(c, old_id, 50)?;
                if &head != old_id {
                    let ours: Option<Option<String>> = c
                        .query_row("SELECT observed_at FROM memories WHERE memory_id=?", [new_mid], |r| r.get(0))
                        .optional()?;
                    let theirs: Option<(Option<String>, String)> = c
                        .query_row("SELECT observed_at, status FROM memories WHERE memory_id=?", [&head], |r| {
                            Ok((r.get(0)?, r.get(1)?))
                        })
                        .optional()?;
                    match theirs {
                        None => head = old_id.clone(),
                        Some((_, st)) if st != "active" => head = old_id.clone(),
                        Some((their_obs, _)) => {
                            if let Some(our_obs) = ours {
                                if our_obs.unwrap_or_default() < their_obs.unwrap_or_default() {
                                    // theirs is newer: ours becomes history behind their head
                                    c.execute(
                                        "UPDATE memories SET status='superseded', superseded_by=?, updated_at=? WHERE memory_id=?",
                                        params![head, now, new_mid],
                                    )?;
                                    c.execute(
                                        "INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, 1.0, ?)",
                                        params![head, new_mid, link_type, now],
                                    )?;
                                    Self::log_sync(
                                        c,
                                        "supersede_race",
                                        Some(new_mid),
                                        Some(&obj([
                                            ("kept", Value::String(head.clone())),
                                            ("target", Value::String(old_id.clone())),
                                        ])),
                                    )?;
                                    counts.memories_superseded += 1;
                                    continue;
                                }
                            }
                        }
                    }
                }
                Self::supersede_sync(c, &head, new_mid, link_type)?;
                counts.memories_superseded += 1;
            }
            for (mem_id, src_ids) in &plan.duplicate_sources {
                let mut st = c.prepare("INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)")?;
                for s in dedup(src_ids.iter().cloned()) {
                    st.execute(params![mem_id, s])?;
                }
                c.execute(
                    "UPDATE memories SET updated_at=?, metadata=json_set(metadata, '$.observations', COALESCE(json_extract(metadata, '$.observations'), 1) + 1) WHERE memory_id=?",
                    params![now, mem_id],
                )?;
                counts.duplicates += 1;
            }
            for rel in &plan.relations {
                Self::upsert_relation_sync(
                    c,
                    &rel.subject,
                    &rel.predicate,
                    &rel.object,
                    rel.confidence,
                    &plan.chat_id,
                    rel.memory_id.as_deref(),
                    rel.message_id.as_deref(),
                    rel.observed_at.as_deref(),
                )?;
                counts.relations += 1;
            }
            for (a, b, lt, w) in &plan.links {
                c.execute(
                    "INSERT OR REPLACE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
                    params![a, b, lt, w, now],
                )?;
                counts.links += 1;
            }
            if !plan.processed_message_ids.is_empty() {
                let mut st = c.prepare("UPDATE messages SET status='processed', processed_at=? WHERE message_id=?")?;
                for m in &plan.processed_message_ids {
                    st.execute(params![now, m])?;
                }
            }
            if !plan.skipped_message_ids.is_empty() {
                let mut st = c.prepare("UPDATE messages SET status='skipped', processed_at=? WHERE message_id=?")?;
                for m in &plan.skipped_message_ids {
                    st.execute(params![now, m])?;
                }
            }
            if !plan.job_ids.is_empty() {
                Self::complete_jobs_sync(c, &plan.job_ids)?;
            }
            for (kind, r, detail) in &plan.audit {
                Self::log_sync(c, kind, r.as_deref(), Some(detail))?;
            }
            Ok(())
        });
        r?;
        self.bump();
        Ok(counts)
    }

    // ------------------------------------------------------------------ audit & stats
    fn log_sync(c: &Connection, kind: &str, r: Option<&str>, detail: Option<&Value>) -> Result<()> {
        c.execute(
            "INSERT INTO audit_log(at, kind, ref, detail) VALUES (?, ?, ?, ?)",
            params![now_iso(), kind, r, j(detail)],
        )?;
        Ok(())
    }

    pub fn log_event(&self, kind: &str, r: Option<&str>, detail: Option<&Value>) -> Result<()> {
        self.tx(|c| Self::log_sync(c, kind, r, detail))
    }

    pub fn recent_events(&self, limit: i64, kind: Option<&str>) -> Result<Vec<Value>> {
        self.with_conn(|c| {
            let (sql, args) = match kind.filter(|k| !k.is_empty()) {
                Some(k) => (
                    "SELECT * FROM audit_log WHERE kind=? ORDER BY id DESC LIMIT ?",
                    vec![text_v(k), SqlValue::Integer(limit)],
                ),
                None => ("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", vec![SqlValue::Integer(limit)]),
            };
            let mut st = c.prepare(sql)?;
            let rows = st.query_map(params_from_iter(args.iter()), |r| {
                Ok(obj([
                    ("id", Value::from(r.get::<_, i64>("id")?)),
                    ("at", Value::String(r.get("at")?)),
                    ("kind", Value::String(r.get("kind")?)),
                    ("ref", r.get::<_, Option<String>>("ref")?.map(Value::String).unwrap_or(Value::Null)),
                    ("detail", jl(r.get("detail")?, empty_obj())),
                ]))
            })?;
            Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
        })
    }

    pub fn stats(&self) -> Result<Value> {
        let chats: i64 = self.with_conn(|c| Ok(c.query_row("SELECT COUNT(*) AS n FROM chats", [], |r| r.get(0))?))?;
        let links: i64 =
            self.with_conn(|c| Ok(c.query_row("SELECT COUNT(*) AS n FROM memory_links", [], |r| r.get(0))?))?;
        let counts = |m: IndexMap<String, i64>| obj(m.into_iter().map(|(k, v)| (k, Value::from(v))));
        Ok(obj([
            ("db_path", Value::String(self.db_path.clone())),
            ("has_fts", Value::Bool(self.has_fts())),
            ("revision", Value::from(self.revision())),
            ("chats", Value::from(chats)),
            ("messages", counts(self.message_counts()?)),
            ("memories", self.memory_counts()?),
            ("entities", Value::from(self.entity_count()?)),
            ("relations", counts(self.relation_counts()?)),
            ("links", Value::from(links)),
            ("jobs", counts(self.job_counts()?)),
        ]))
    }
}

fn text_v(s: &str) -> SqlValue {
    text(s)
}

fn non_empty(s: &Option<String>) -> Option<&str> {
    s.as_deref().filter(|v| !v.is_empty())
}

/// `timedelta(seconds=x)`: microsecond resolution, rounded half-to-even like CPython.
pub fn seconds(x: f64) -> TimeDelta {
    TimeDelta::microseconds((x * 1e6).round_ties_even() as i64)
}

/// The JSON value of a float column (helper for status payloads).
pub fn float_value(x: f64) -> Value {
    fnum(x)
}
