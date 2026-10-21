//! Robust JSON extraction from small-model completions.
//!
//! Qwen-7B-class models mostly return valid JSON when asked, but they also wrap it in ```json fences, prefix it
//! with "Here is the JSON:", emit trailing commas, or truncate at max_tokens. Every parser in the memory pipeline
//! goes through [`parse_json_object`] / [`parse_json_array`] so those failure modes are handled in one place and
//! never panic.

use crate::pycompat::{dumps, loads, py_float_from_value, py_prefix, py_split_whitespace, py_str, JsonFormat};
use regex::Regex;
use serde_json::{Map, Value};
use std::sync::LazyLock;

static FENCE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)```(?:json|JSON)?[\s\x1c-\x1f]*(.*?)```").unwrap());
static TRAILING_COMMA_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r",[\s\x1c-\x1f]*([}\]])").unwrap());
static THINK_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<think>.*?</think>[\s\x1c-\x1f]*").unwrap());
static DANGLING_COMMA_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r",[\s\x1c-\x1f]*$").unwrap());
static DANGLING_KEY_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#""[^"]*"[\s\x1c-\x1f]*:[\s\x1c-\x1f]*$"#).unwrap());

/// Substrings likely to hold the JSON payload, most specific first.
fn candidates(text: &str) -> Vec<String> {
    let stripped = THINK_RE.replace_all(text, "");
    let text = crate::pycompat::py_strip(&stripped).to_string();
    let mut out: Vec<String> = FENCE_RE
        .captures_iter(&text)
        .map(|c| crate::pycompat::py_strip(c.get(1).map(|m| m.as_str()).unwrap_or("")).to_string())
        .collect();
    out.push(text);
    out
}

/// The first balanced `open`...`close` span, honouring JSON strings.
fn balanced(text: &str, open: char, close: char) -> Option<&str> {
    let start = text.find(open)?;
    let mut depth = 0i64;
    let mut in_str = false;
    let mut esc = false;
    for (i, ch) in text[start..].char_indices() {
        if in_str {
            if esc {
                esc = false;
            } else if ch == '\\' {
                esc = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        if ch == '"' {
            in_str = true;
        } else if ch == open {
            depth += 1;
        } else if ch == close {
            depth -= 1;
            if depth == 0 {
                let end = start + i + ch.len_utf8();
                return Some(&text[start..end]);
            }
        }
    }
    None
}

fn loads_lenient(s: &str) -> Option<Value> {
    if let Some(v) = loads(s) {
        return Some(v);
    }
    let fixed = TRAILING_COMMA_RE.replace_all(s, "$1");
    loads(&fixed)
}

/// Close open strings/brackets of a completion cut off by max_tokens (best effort).
fn repair_truncated(s: &str) -> String {
    let mut stack: Vec<char> = Vec::new();
    let mut in_str = false;
    let mut esc = false;
    for ch in s.chars() {
        if in_str {
            if esc {
                esc = false;
            } else if ch == '\\' {
                esc = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        if ch == '"' {
            in_str = true;
        } else if ch == '{' || ch == '[' {
            stack.push(if ch == '{' { '}' } else { ']' });
        } else if (ch == '}' || ch == ']') && !stack.is_empty() {
            stack.pop();
        }
    }
    let mut out = s.to_string();
    if in_str {
        out.push('"');
    }
    // drop a dangling `"key":` or trailing comma before closing
    out = DANGLING_COMMA_RE.replace_all(&out, "").into_owned();
    out = DANGLING_KEY_RE.replace_all(&out, "").into_owned();
    out = DANGLING_COMMA_RE.replace_all(&out, "").into_owned();
    while let Some(c) = stack.pop() {
        out.push(c);
    }
    out
}

fn parse_first(text: &str, open: char, close: char, want_object: bool) -> Option<Value> {
    let accept = |v: &Value| if want_object { v.is_object() } else { v.is_array() };
    for cand in candidates(text) {
        let span = balanced(&cand, open, close).map(str::to_string);
        for attempt in [span.as_deref(), Some(cand.as_str())].into_iter().flatten() {
            if attempt.is_empty() {
                continue;
            }
            if let Some(v) = loads_lenient(attempt) {
                if accept(&v) {
                    return Some(v);
                }
            }
        }
        // truncated completion: try to close it
        if let Some(start) = cand.find(open) {
            if let Some(v) = loads_lenient(&repair_truncated(&cand[start..])) {
                if accept(&v) {
                    return Some(v);
                }
            }
        }
    }
    None
}

/// Best-effort: the first JSON object found in `text`.
pub fn parse_json_object(text: &str) -> Option<Map<String, Value>> {
    match parse_first(text, '{', '}', true) {
        Some(Value::Object(m)) => Some(m),
        _ => None,
    }
}

/// Best-effort: the first JSON array found in `text`.
pub fn parse_json_array(text: &str) -> Option<Vec<Value>> {
    match parse_first(text, '[', ']', false) {
        Some(Value::Array(a)) => Some(a),
        _ => None,
    }
}

/// Coerce a JSON value to a clean string (`None`/missing -> ''); `limit` truncates in code points (0 = none).
pub fn as_str(v: Option<&Value>, limit: usize) -> String {
    let s = match v {
        None | Some(Value::Null) => return String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(v @ (Value::Object(_) | Value::Array(_))) => dumps(v, &JsonFormat::unicode()),
        Some(v) => py_str(v),
    };
    let s = py_split_whitespace(&s).join(" ");
    if limit > 0 {
        py_prefix(&s, limit).to_string()
    } else {
        s
    }
}

/// `as_str` for plain text input.
pub fn as_str_text(s: &str, limit: usize) -> String {
    as_str(Some(&Value::String(s.to_string())), limit)
}

/// Coerce to a float clamped to `[lo, hi]`; non-numbers and NaN give `default`.
pub fn as_float(v: Option<&Value>, default: f64, lo: f64, hi: f64) -> f64 {
    let f = match v.and_then(py_float_from_value) {
        Some(f) => f,
        None => return default,
    };
    if f.is_nan() {
        return default;
    }
    lo.max(hi.min(f))
}

/// `as_float(v, default)` with the default `[0, 1]` range.
pub fn as_unit(v: Option<&Value>, default: f64) -> f64 {
    as_float(v, default, 0.0, 1.0)
}

/// Lists stay lists, scalars and objects become one-element lists, null/missing becomes empty.
pub fn as_list(v: Option<&Value>) -> Vec<Value> {
    match v {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(other) => vec![other.clone()],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn fenced_prefixed_trailing_commas() {
        let raw = "Sure! Here is the JSON:\n```json\n{\"memories\": [{\"text\": \"a\",}, ],}\n```\nHope this helps.";
        assert_eq!(Value::Object(parse_json_object(raw).unwrap()), json!({"memories": [{"text": "a"}]}));
    }

    #[test]
    fn think_blocks_and_truncation() {
        let raw = r#"<think>reasoning</think>{"memories": [{"text": "one"}, {"text": "tw"#;
        assert_eq!(
            Value::Object(parse_json_object(raw).unwrap()),
            json!({"memories": [{"text": "one"}, {"text": "tw"}]})
        );
    }

    #[test]
    fn array_and_garbage() {
        assert_eq!(parse_json_array(r#"[["a","b","c"]] trailing"#).unwrap(), vec![json!(["a", "b", "c"])]);
        assert!(parse_json_object("no json here").is_none());
        assert!(parse_json_array("{}").is_none());
    }

    #[test]
    fn nested_braces_in_strings() {
        let raw = r#"{"text": "he said {hi} and \"bye\"", "n": 1}"#;
        assert_eq!(Value::Object(parse_json_object(raw).unwrap()), json!({"text": "he said {hi} and \"bye\"", "n": 1}));
    }
}
