//! Shared helpers for the integration tests: fixture loading, database dumps in the generator's format, a JSON
//! comparison that reports the first differing path, and small constructors mirroring the Python test helpers.
#![allow(dead_code)]

use neuralgraph_memory::clock;
use neuralgraph_memory::llm::fake_embedding;
use neuralgraph_memory::models::{new_id, now_iso, set_deterministic_ids, Memory};
use neuralgraph_memory::textutil::content_hash;
use rusqlite::types::ValueRef;
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub fn golden_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("golden")
}

pub fn load(name: &str) -> Value {
    let text = std::fs::read_to_string(golden_dir().join(name)).unwrap_or_else(|e| panic!("read {name}: {e}"));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {name}: {e}"))
}

/// A float from a fixture (`{"float": "inf"}` encodes non-finite values).
pub fn fixture_f64(v: &Value) -> f64 {
    match v {
        Value::Object(o) => match o.get("float").and_then(Value::as_str) {
            Some("inf") => f64::INFINITY,
            Some("-inf") => f64::NEG_INFINITY,
            _ => f64::NAN,
        },
        other => other.as_f64().expect("number"),
    }
}

/// Pin the clock and id counters for this thread (what the generator does to the Python modules).
pub fn pin(now: &str, id_start: u64) {
    let dt = chrono::DateTime::parse_from_rfc3339(now).expect("rfc3339").with_timezone(&chrono::Utc);
    clock::set_fixed(Some(dt));
    set_deterministic_ids(Some(id_start));
}

pub fn unpin() {
    clock::set_fixed(None);
    set_deterministic_ids(None);
}

const TABLES: [(&str, &str); 13] = [
    ("meta", "key"),
    ("chats", "chat_id"),
    ("messages", "rid"),
    ("memories", "rid"),
    ("memory_sources", "memory_id, message_id"),
    ("entities", "entity_id"),
    ("entity_aliases", "alias"),
    ("memory_entities", "memory_id, entity_id"),
    ("relations", "relation_id"),
    ("memory_links", "source_id, target_id, link_type"),
    ("jobs", "job_id"),
    ("audit_log", "id"),
    ("sqlite_sequence", "name"),
];

/// Every table as a list of row objects, blobs as hex (the generator's `dump_db`).
pub fn dump_db(path: &Path) -> Value {
    let conn = rusqlite::Connection::open(path).expect("open dump");
    let mut out = Map::new();
    for (table, order) in TABLES {
        let mut st = conn.prepare(&format!("SELECT * FROM {table} ORDER BY {order}")).expect("prepare");
        let cols: Vec<String> = st.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows = st.query([]).expect("query");
        let mut list = Vec::new();
        while let Some(r) = rows.next().expect("row") {
            let mut o = Map::new();
            for (i, c) in cols.iter().enumerate() {
                let v = match r.get_ref(i).expect("cell") {
                    ValueRef::Null => Value::Null,
                    ValueRef::Integer(n) => Value::from(n),
                    ValueRef::Real(f) => serde_json::Number::from_f64(f).map(Value::Number).unwrap_or(Value::Null),
                    ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).into_owned()),
                    ValueRef::Blob(b) => Value::String(b.iter().map(|x| format!("{x:02x}")).collect()),
                };
                o.insert(c.clone(), v);
            }
            list.push(Value::Object(o));
        }
        out.insert(table.to_string(), Value::Array(list));
    }
    let n: i64 = conn.query_row("SELECT COUNT(*) FROM memories_fts", [], |r| r.get(0)).expect("fts count");
    out.insert("memories_fts_count".into(), Value::from(n));
    Value::Object(out)
}

/// First difference between two JSON values, as "path: left != right".
pub fn json_diff(a: &Value, b: &Value, path: &str, float_tol: f64) -> Option<String> {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            let kx: Vec<&String> = x.keys().collect();
            let ky: Vec<&String> = y.keys().collect();
            if kx != ky {
                return Some(format!("{path}: keys {kx:?} != {ky:?}"));
            }
            for (k, v) in x {
                if let Some(d) = json_diff(v, &y[k], &format!("{path}.{k}"), float_tol) {
                    return Some(d);
                }
            }
            None
        }
        (Value::Array(x), Value::Array(y)) => {
            if x.len() != y.len() {
                return Some(format!(
                    "{path}: length {} != {}\n left={}\nright={}",
                    x.len(),
                    y.len(),
                    short(a),
                    short(b)
                ));
            }
            for (i, (p, q)) in x.iter().zip(y).enumerate() {
                if let Some(d) = json_diff(p, q, &format!("{path}[{i}]"), float_tol) {
                    return Some(d);
                }
            }
            None
        }
        (Value::Number(p), Value::Number(q)) => {
            let (fp, fq) = (p.as_f64().unwrap_or(f64::NAN), q.as_f64().unwrap_or(f64::NAN));
            if p == q || (float_tol > 0.0 && (fp - fq).abs() <= float_tol) || (fp == fq && p.is_f64() == q.is_f64()) {
                None
            } else {
                Some(format!("{path}: {p} != {q}"))
            }
        }
        _ => {
            if a == b {
                None
            } else {
                Some(format!("{path}: {} != {}", short(a), short(b)))
            }
        }
    }
}

fn short(v: &Value) -> String {
    let s = v.to_string();
    if s.chars().count() > 400 {
        format!("{}…", s.chars().take(400).collect::<String>())
    } else {
        s
    }
}

#[track_caller]
pub fn assert_json_eq(actual: &Value, expected: &Value, what: &str) {
    if let Some(d) = json_diff(actual, expected, "$", 0.0) {
        panic!("{what}: rust != python at {d}");
    }
}

/// The Python tests' `make_memory` helper.
#[allow(clippy::too_many_arguments)]
pub fn make_memory(
    text: &str,
    subject: &str,
    chat_id: &str,
    observed_at: &str,
    embedding: Option<Vec<f64>>,
    kind: &str,
    importance: f64,
) -> Memory {
    let now = now_iso();
    let title = title_case(subject);
    let mut m = Memory::new(
        new_id("mem"),
        text.into(),
        kind.into(),
        subject.into(),
        title.clone(),
        title,
        chat_id.into(),
        importance,
        0.9,
        observed_at.into(),
        now.clone(),
        now,
    );
    m.text_hash = content_hash(&[&text.to_lowercase()]);
    m.embedding = Some(embedding.unwrap_or_else(|| vec![1.0, 0.0, 0.0]));
    m
}

/// `make_memory(text)` with the Python defaults.
pub fn mem(text: &str) -> Memory {
    make_memory(text, "ali", "c1", "2026-01-01T00:00:00+00:00", None, "fact", 0.7)
}

/// `make_memory(text, embedding=fake_embedding(text), **kw)`.
pub fn emb_memory(text: &str, subject: &str, chat_id: &str, observed_at: &str, kind: &str, importance: f64) -> Memory {
    make_memory(text, subject, chat_id, observed_at, Some(fake_embedding(text, 256)), kind, importance)
}

/// Python `str.title()` for simple ASCII subjects.
pub fn title_case(s: &str) -> String {
    let mut out = String::new();
    let mut start = true;
    for c in s.chars() {
        if c.is_alphabetic() {
            if start {
                out.extend(c.to_uppercase());
            } else {
                out.extend(c.to_lowercase());
            }
            start = false;
        } else {
            out.push(c);
            start = true;
        }
    }
    out
}

pub fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or_default().to_string()).collect()).unwrap_or_default()
}
