//! Plain data records for the chat-memory store.
//!
//! Datetimes are ISO-8601 strings in UTC (`"2026-09-17T08:00:00+00:00"`) everywhere they are persisted so that
//! SQLite string comparison orders them correctly. `to_value` reproduces the Python `to_dict` shapes field for
//! field (the REST API, MCP tools and CLI `--json` output are built from them).

use crate::clock;
use crate::pycompat::{fnum, obj, opt_str};
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, NaiveTime, TimeDelta, TimeZone, Utc};
use serde_json::{Map, Value};

pub const MEMORY_KINDS: [&str; 9] =
    ["fact", "preference", "event", "plan", "relationship", "opinion", "identity", "task", "other"];
pub const MEMORY_STATUS: [&str; 3] = ["active", "superseded", "retracted"];
pub const MESSAGE_STATUS: [&str; 5] = ["pending", "processing", "processed", "skipped", "failed"];
pub const JOB_STATUS: [&str; 5] = ["queued", "leased", "done", "failed", "dead"];
pub const LINK_TYPES: [&str; 5] = ["supersedes", "contradicts", "related", "elaborates", "same_event"];

pub fn utcnow() -> DateTime<Utc> {
    clock::utcnow()
}

/// `isoformat(timespec="seconds")` in UTC (sub-seconds are truncated, not rounded).
pub fn iso(dt: DateTime<Utc>) -> String {
    dt.format("%Y-%m-%dT%H:%M:%S+00:00").to_string()
}

/// `isoformat(timespec="microseconds")` in UTC, used for job scheduling.
pub fn precise_iso(dt: DateTime<Utc>) -> String {
    dt.format("%Y-%m-%dT%H:%M:%S%.6f+00:00").to_string()
}

pub fn now_iso() -> String {
    iso(utcnow())
}

/// `datetime.fromisoformat(s.replace("Z", "+00:00"))` with naive values taken as UTC; `None` when unparseable.
///
/// Accepts what Python 3.11 accepts in practice: `YYYY-MM-DD` / `YYYYMMDD`, then an optional single separator
/// character and `HH[:MM[:SS[.ffffff]]]` (or the basic `HHMMSS` form), then `Z` / `±HH[:MM[:SS]]`.
pub fn parse_iso(s: Option<&str>) -> Option<DateTime<FixedOffset>> {
    let s = s?;
    if s.is_empty() {
        return None;
    }
    let s = s.replace('Z', "+00:00");
    let (naive, offset) = parse_iso_parts(&s)?;
    let tz = match offset {
        Some(secs) => FixedOffset::east_opt(secs)?,
        None => FixedOffset::east_opt(0)?,
    };
    tz.from_local_datetime(&naive).single()
}

/// Naive wall time + optional UTC offset in seconds.
pub fn parse_iso_parts(s: &str) -> Option<(NaiveDateTime, Option<i32>)> {
    if !s.is_ascii() {
        return None;
    }
    let b = s.as_bytes();
    let digits = |r: std::ops::Range<usize>| -> Option<u32> {
        if r.end > b.len() || !b[r.clone()].iter().all(|c| c.is_ascii_digit()) {
            return None;
        }
        s[r].parse().ok()
    };
    // date part
    let (date, mut i) = if b.len() >= 10 && b[4] == b'-' && b[7] == b'-' {
        (NaiveDate::from_ymd_opt(digits(0..4)? as i32, digits(5..7)?, digits(8..10)?)?, 10)
    } else if b.len() >= 8 && digits(0..8).is_some() {
        (NaiveDate::from_ymd_opt(digits(0..4)? as i32, digits(4..6)?, digits(6..8)?)?, 8)
    } else {
        return None;
    };
    if i == b.len() {
        return Some((date.and_time(NaiveTime::MIN), None));
    }
    i += 1; // any single separator character
    let rest = &s[i..];
    // split off the offset
    let off_pos = rest.find(['+', '-']);
    let (time_s, off_s) = match off_pos {
        Some(p) => (&rest[..p], Some(&rest[p..])),
        None => (rest, None),
    };
    let time = parse_time(time_s)?;
    let offset = match off_s {
        None => None,
        Some(o) => Some(parse_offset(o)?),
    };
    Some((date.and_time(time), offset))
}

fn parse_time(t: &str) -> Option<NaiveTime> {
    let (main, frac) = match t.split_once(['.', ',']) {
        Some((m, f)) => (m, Some(f)),
        None => (t, None),
    };
    let nums: Vec<u32> = if main.contains(':') {
        let parts: Vec<&str> = main.split(':').collect();
        if parts.len() > 3 || parts.iter().any(|p| p.len() != 2 || !p.bytes().all(|c| c.is_ascii_digit())) {
            return None;
        }
        parts.iter().map(|p| p.parse().unwrap()).collect()
    } else {
        if main.is_empty() || main.len() % 2 != 0 || main.len() > 6 || !main.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        (0..main.len() / 2).map(|k| main[2 * k..2 * k + 2].parse().unwrap()).collect()
    };
    if frac.is_some() && nums.len() != 3 {
        return None;
    }
    let micros = match frac {
        None => 0,
        Some(f) => {
            if f.is_empty() || !f.bytes().all(|c| c.is_ascii_digit()) {
                return None;
            }
            let mut padded: String = f.chars().take(6).collect();
            while padded.len() < 6 {
                padded.push('0');
            }
            padded.parse().ok()?
        }
    };
    let h = *nums.first()?;
    let m = nums.get(1).copied().unwrap_or(0);
    let sec = nums.get(2).copied().unwrap_or(0);
    NaiveTime::from_hms_micro_opt(h, m, sec, micros)
}

fn parse_offset(o: &str) -> Option<i32> {
    let sign = if o.starts_with('-') { -1 } else { 1 };
    let body = &o[1..];
    let (main, frac) = match body.split_once('.') {
        Some((m, f)) => (m, Some(f)),
        None => (body, None),
    };
    let parts: Vec<u32> = if main.contains(':') {
        let p: Vec<&str> = main.split(':').collect();
        if p.iter().any(|x| x.len() != 2 || !x.bytes().all(|c| c.is_ascii_digit())) {
            return None;
        }
        p.iter().map(|x| x.parse().unwrap()).collect()
    } else {
        if main.is_empty() || main.len() % 2 != 0 || main.len() > 6 || !main.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        (0..main.len() / 2).map(|k| main[2 * k..2 * k + 2].parse().unwrap()).collect()
    };
    if parts.len() > 3 || frac.is_some() && parts.len() != 3 {
        return None;
    }
    let h = *parts.first()? as i32;
    let m = parts.get(1).copied().unwrap_or(0) as i32;
    let sec = parts.get(2).copied().unwrap_or(0) as i32;
    if h >= 24 || m >= 60 || sec >= 60 {
        return None;
    }
    Some(sign * (h * 3600 + m * 60 + sec))
}

thread_local! {
    /// Per-prefix counters when ids are pinned (golden-fixture replays); `None` = random ids.
    static ID_COUNTERS: std::cell::RefCell<Option<(u64, std::collections::HashMap<String, u64>)>> =
        const { std::cell::RefCell::new(None) };
}

/// Make [`new_id`] deterministic on this thread: `"{prefix}_{n:020}"` with `n` counting up from `start` per
/// prefix (the fixture generator patches the Python `new_id` the same way). `None` restores random ids.
pub fn set_deterministic_ids(start: Option<u64>) {
    ID_COUNTERS.with(|c| *c.borrow_mut() = start.map(|s| (s, std::collections::HashMap::new())));
}

/// `f"{prefix}_{uuid4().hex[:20]}"`.
pub fn new_id(prefix: &str) -> String {
    let pinned = ID_COUNTERS.with(|c| {
        c.borrow_mut().as_mut().map(|(start, counters)| {
            let n = counters.entry(prefix.to_string()).or_insert(*start);
            *n += 1;
            *n
        })
    });
    if let Some(n) = pinned {
        return format!("{prefix}_{n:020}");
    }
    let hex = uuid::Uuid::new_v4().simple().to_string();
    format!("{}_{}", prefix, &hex[..20])
}

/// `(a - b).total_seconds()` with microsecond precision.
pub fn total_seconds(d: TimeDelta) -> f64 {
    match d.num_microseconds() {
        Some(us) => us as f64 / 1e6,
        None => d.num_milliseconds() as f64 / 1e3,
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ChatMessage {
    pub message_id: String,
    pub chat_id: String,
    pub seq: i64,
    pub speaker: String,
    pub role: String,
    pub text: String,
    pub sent_at: Option<String>,
    pub ingested_at: String,
    pub content_hash: String,
    pub status: String,
    pub processed_at: Option<String>,
    pub metadata: Value,
}

impl ChatMessage {
    pub fn to_value(&self) -> Value {
        obj([
            ("message_id", Value::String(self.message_id.clone())),
            ("chat_id", Value::String(self.chat_id.clone())),
            ("seq", Value::from(self.seq)),
            ("speaker", Value::String(self.speaker.clone())),
            ("role", Value::String(self.role.clone())),
            ("text", Value::String(self.text.clone())),
            ("sent_at", opt_str(&self.sent_at)),
            ("ingested_at", Value::String(self.ingested_at.clone())),
            ("content_hash", Value::String(self.content_hash.clone())),
            ("status", Value::String(self.status.clone())),
            ("processed_at", opt_str(&self.processed_at)),
            ("metadata", self.metadata.clone()),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Memory {
    pub memory_id: String,
    pub text: String,
    pub kind: String,
    /// canonical entity key the memory is about
    pub subject: String,
    /// display name for the subject
    pub subject_name: String,
    /// who said it
    pub speaker: String,
    /// chat where it was (first) observed
    pub chat_id: String,
    pub importance: f64,
    pub confidence: f64,
    /// ISO date or datetime the memory is about, if any
    pub event_time: Option<String>,
    /// "day" | "month" | "year" | "none"
    pub event_time_precision: String,
    /// sent_at of the source message (or ingest time)
    pub observed_at: String,
    pub created_at: String,
    pub updated_at: String,
    pub status: String,
    pub superseded_by: Option<String>,
    pub version: i64,
    pub text_hash: String,
    /// `None` = NULL; `Some(empty)` = an empty blob (kept distinct, as the Python store did).
    pub embedding: Option<Vec<f64>>,
    pub access_count: i64,
    pub last_accessed_at: Option<String>,
    pub metadata: Value,
    /// populated on read when requested
    pub source_message_ids: Vec<String>,
    pub entity_ids: Vec<String>,
}

impl Memory {
    /// A fresh active memory with the dataclass defaults for every optional field.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        memory_id: String,
        text: String,
        kind: String,
        subject: String,
        subject_name: String,
        speaker: String,
        chat_id: String,
        importance: f64,
        confidence: f64,
        observed_at: String,
        created_at: String,
        updated_at: String,
    ) -> Self {
        Memory {
            memory_id,
            text,
            kind,
            subject,
            subject_name,
            speaker,
            chat_id,
            importance,
            confidence,
            event_time: None,
            event_time_precision: "none".into(),
            observed_at,
            created_at,
            updated_at,
            status: "active".into(),
            superseded_by: None,
            version: 1,
            text_hash: String::new(),
            embedding: None,
            access_count: 0,
            last_accessed_at: None,
            metadata: Value::Object(Map::new()),
            source_message_ids: Vec::new(),
            entity_ids: Vec::new(),
        }
    }

    pub fn to_value(&self, include_embedding: bool) -> Value {
        let mut pairs: Vec<(&str, Value)> = vec![
            ("memory_id", Value::String(self.memory_id.clone())),
            ("text", Value::String(self.text.clone())),
            ("kind", Value::String(self.kind.clone())),
            ("subject", Value::String(self.subject.clone())),
            ("subject_name", Value::String(self.subject_name.clone())),
            ("speaker", Value::String(self.speaker.clone())),
            ("chat_id", Value::String(self.chat_id.clone())),
            ("importance", fnum(self.importance)),
            ("confidence", fnum(self.confidence)),
            ("event_time", opt_str(&self.event_time)),
            ("event_time_precision", Value::String(self.event_time_precision.clone())),
            ("observed_at", Value::String(self.observed_at.clone())),
            ("created_at", Value::String(self.created_at.clone())),
            ("updated_at", Value::String(self.updated_at.clone())),
            ("status", Value::String(self.status.clone())),
            ("superseded_by", opt_str(&self.superseded_by)),
            ("version", Value::from(self.version)),
            ("text_hash", Value::String(self.text_hash.clone())),
        ];
        if include_embedding {
            pairs.push((
                "embedding",
                match &self.embedding {
                    Some(e) => Value::Array(e.iter().map(|x| fnum(*x)).collect()),
                    None => Value::Null,
                },
            ));
        }
        pairs.extend([
            ("access_count", Value::from(self.access_count)),
            ("last_accessed_at", opt_str(&self.last_accessed_at)),
            ("metadata", self.metadata.clone()),
            ("source_message_ids", Value::from(self.source_message_ids.clone())),
            ("entity_ids", Value::from(self.entity_ids.clone())),
        ]);
        obj(pairs)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Entity {
    pub entity_id: String,
    pub name: String,
    pub etype: String,
    pub mention_count: i64,
    pub first_seen_at: Option<String>,
    pub last_seen_at: Option<String>,
    pub metadata: Value,
    pub aliases: Vec<String>,
}

impl Entity {
    pub fn to_value(&self) -> Value {
        obj([
            ("entity_id", Value::String(self.entity_id.clone())),
            ("name", Value::String(self.name.clone())),
            ("type", Value::String(self.etype.clone())),
            ("mention_count", Value::from(self.mention_count)),
            ("first_seen_at", opt_str(&self.first_seen_at)),
            ("last_seen_at", opt_str(&self.last_seen_at)),
            ("metadata", self.metadata.clone()),
            ("aliases", Value::from(self.aliases.clone())),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Relation {
    pub relation_id: String,
    pub subject_id: String,
    pub predicate: String,
    pub object_id: String,
    pub confidence: f64,
    pub chat_id: String,
    pub memory_id: Option<String>,
    pub message_id: Option<String>,
    pub status: String,
    pub observation_count: i64,
    pub observed_at: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub metadata: Value,
}

impl Relation {
    pub fn to_value(&self) -> Value {
        obj([
            ("relation_id", Value::String(self.relation_id.clone())),
            ("subject_id", Value::String(self.subject_id.clone())),
            ("predicate", Value::String(self.predicate.clone())),
            ("object_id", Value::String(self.object_id.clone())),
            ("confidence", fnum(self.confidence)),
            ("chat_id", Value::String(self.chat_id.clone())),
            ("memory_id", opt_str(&self.memory_id)),
            ("message_id", opt_str(&self.message_id)),
            ("status", Value::String(self.status.clone())),
            ("observation_count", Value::from(self.observation_count)),
            ("observed_at", opt_str(&self.observed_at)),
            ("created_at", Value::String(self.created_at.clone())),
            ("updated_at", Value::String(self.updated_at.clone())),
            ("metadata", self.metadata.clone()),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MemoryLink {
    pub source_id: String,
    pub target_id: String,
    pub link_type: String,
    pub weight: f64,
    pub created_at: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Job {
    pub job_id: i64,
    /// "extract" | "maintain"
    pub kind: String,
    pub chat_id: Option<String>,
    /// message_id for extract jobs
    pub ref_id: Option<String>,
    pub seq: Option<i64>,
    pub dedupe_key: Option<String>,
    pub status: String,
    pub attempts: i64,
    pub max_attempts: i64,
    pub available_at: String,
    pub leased_until: Option<String>,
    pub worker_id: Option<String>,
    pub last_error: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub finished_at: Option<String>,
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RetrievedMemory {
    pub memory: Memory,
    pub score: f64,
    /// per-channel contribution, in insertion order
    pub channels: indexmap::IndexMap<String, f64>,
    /// provenance
    pub sources: Vec<ChatMessage>,
    pub entities: Vec<String>,
    pub explanation: String,
}

impl RetrievedMemory {
    pub fn channels_value(&self) -> Value {
        obj(self.channels.iter().map(|(k, v)| (k.clone(), fnum(*v))))
    }

    pub fn to_value(&self) -> Value {
        obj([
            ("memory", self.memory.to_value(false)),
            ("score", fnum(self.score)),
            ("channels", self.channels_value()),
            ("sources", Value::Array(self.sources.iter().map(|s| s.to_value()).collect())),
            ("entities", Value::from(self.entities.clone())),
            ("explanation", Value::String(self.explanation.clone())),
        ])
    }
}

/// UTC `DateTime` from a naive one (helper for tests and fixtures).
pub fn utc(naive: NaiveDateTime) -> DateTime<Utc> {
    Utc.from_utc_datetime(&naive)
}
