//! Shared helpers for the integration tests: fixture loading, pinned clocks, and a JSON comparison that reports
//! the first differing path.
#![allow(dead_code)]

use serde_json::Value;
use std::path::{Path, PathBuf};

pub fn golden_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("golden")
}

pub fn load(name: &str) -> Value {
    let text = std::fs::read_to_string(golden_dir().join(name)).unwrap_or_else(|e| panic!("read {name}: {e}"));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {name}: {e}"))
}

/// The instant every generator pins Python's `datetime.now()` to (`_reference.FIXED_NOW`).
pub const FIXED_NOW: &str = "2026-09-30T08:00:00+00:00";

/// Pin "now" on this thread the way the generators froze `datetime` (naive local time == UTC wall time).
pub fn pin_now(now: &str) {
    let dt = chrono::DateTime::parse_from_rfc3339(now).expect("rfc3339").with_timezone(&chrono::Utc);
    neuralgraph_memory::clock::set_fixed(Some(dt));
    neuralgraph_research::pytime::set_fixed_local_offset(Some(0));
}

pub fn unpin_now() {
    neuralgraph_memory::clock::set_fixed(None);
    neuralgraph_research::pytime::set_fixed_local_offset(None);
}

pub fn s(v: &Value) -> &str {
    v.as_str().unwrap_or_default()
}

pub fn f64s(v: &Value) -> Vec<f64> {
    v.as_array().expect("array").iter().map(|x| x.as_f64().expect("number")).collect()
}

pub fn strings(v: &Value) -> Vec<String> {
    v.as_array().expect("array").iter().map(|x| x.as_str().expect("string").to_string()).collect()
}

/// float32 values stored as their IEEE bit patterns.
pub fn f32_bits(v: &Value) -> Vec<f32> {
    v.as_array().expect("array").iter().map(|x| f32::from_bits(x.as_u64().expect("u32") as u32)).collect()
}

/// float64 values stored as their IEEE bit patterns.
pub fn f64_bits(v: &Value) -> Vec<f64> {
    v.as_array().expect("array").iter().map(|x| f64::from_bits(x.as_u64().expect("u64"))).collect()
}

/// First difference between two JSON values, as "path: left != right". Numbers must match exactly, including
/// the int/float distinction Python's `json.dumps` preserves.
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
            let same_kind = p.is_f64() == q.is_f64();
            if (p == q || fp == fq) && same_kind {
                return None;
            }
            if float_tol > 0.0 && (fp - fq).abs() <= float_tol * fp.abs().max(fq.abs()).max(1.0) {
                return None;
            }
            Some(format!("{path}: {p} != {q}"))
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

/// Equality up to a relative tolerance, for values whose Python computation summed over a `set` (an order that
/// changed from process to process, so the last bits were never reproducible in Python either).
#[track_caller]
pub fn assert_json_close(actual: &Value, expected: &Value, rel_tol: f64, what: &str) {
    if let Some(d) = json_diff(actual, expected, "$", rel_tol) {
        panic!("{what}: rust != python at {d}");
    }
}
