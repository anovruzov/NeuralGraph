//! Python text semantics shared by the LLM-facing retrieval modules (`query_router`, `prompts`, `reranker`, the
//! profile modules): `re` patterns with Python's character classes, single-pass `str.format`, `str()` of parsed
//! JSON values, list slicing with Python's negative bounds, and `max`/`min` tie rules. Not a Python module.

use neuralgraph_memory::pycompat::{py_float_repr, py_repr_str};
use serde_json::Value;

// ============================================================================
// Python regular expressions
// ============================================================================

/// Python's `\w` for `str` patterns: letters, numbers (any `N*` category) and `_`.
const PY_WORD_CLASS: &str = r"\p{L}\p{N}_";

/// Translate a Python `re` pattern to `fancy_regex` syntax with Python's character-class semantics:
/// `\w`/`\W` → letters, numbers, `_`; `\b` → a boundary defined by that class (lookarounds); `\s` → Unicode
/// whitespace plus U+001C..U+001F (Python's `str.isspace`); `\'`/`\"` → the bare quote (Rust rejects the escape).
pub fn translate_py_pattern(pattern: &str) -> String {
    let boundary = format!("(?:(?<=[{w}])(?![{w}])|(?<![{w}])(?=[{w}]))", w = PY_WORD_CLASS);
    let mut out = String::with_capacity(pattern.len() * 2);
    let mut in_class = false;
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\\' => {
                let n = chars.next().expect("dangling backslash in pattern");
                match (n, in_class) {
                    ('w', false) => out.push_str(&format!("[{PY_WORD_CLASS}]")),
                    ('w', true) => out.push_str(PY_WORD_CLASS),
                    ('W', false) => out.push_str(&format!("[^{PY_WORD_CLASS}]")),
                    ('s', false) => out.push_str(r"[\s\x1c-\x1f]"),
                    ('s', true) => out.push_str(r"\s\x1c-\x1f"),
                    ('b', false) => out.push_str(&boundary),
                    ('\'', _) | ('"', _) => out.push(n),
                    ('W', true) | ('b', true) => panic!("unsupported escape \\{n} inside a class: {pattern}"),
                    _ => {
                        out.push('\\');
                        out.push(n);
                    }
                }
            }
            '[' if !in_class => {
                in_class = true;
                out.push('[');
                if chars.peek() == Some(&'^') {
                    out.push(chars.next().expect("peeked"));
                }
            }
            ']' if in_class => {
                in_class = false;
                out.push(']');
            }
            _ => out.push(c),
        }
    }
    out
}

/// Compile a Python `re` pattern (see [`translate_py_pattern`]).
pub fn py_regex(pattern: &str) -> fancy_regex::Regex {
    let translated = translate_py_pattern(pattern);
    fancy_regex::Regex::new(&translated).unwrap_or_else(|e| panic!("pattern {pattern:?} ({translated:?}): {e}"))
}

/// `re.search(pattern, text) is not None`.
pub fn py_search(re: &fancy_regex::Regex, text: &str) -> bool {
    re.is_match(text).expect("regex backtrack limit")
}

/// `re.findall` for a pattern without groups: every match, left to right.
pub fn py_findall(re: &fancy_regex::Regex, text: &str) -> Vec<String> {
    re.find_iter(text).map(|m| m.expect("regex backtrack limit").as_str().to_string()).collect()
}

/// Group `i` of the first match (`None` if there is no match; inner `None` if the group did not participate).
pub fn search_group(re: &fancy_regex::Regex, text: &str, i: usize) -> Option<Option<String>> {
    let caps = re.captures(text).expect("regex backtrack limit")?;
    Some(caps.get(i).map(|m| m.as_str().to_string()))
}

/// `str.capitalize()`: first character title-cased, the rest lower-cased. Title case differs from upper case
/// only for the four Latin digraph letters, handled explicitly.
pub fn py_capitalize(s: &str) -> String {
    let mut chars = s.chars();
    let Some(first) = chars.next() else { return String::new() };
    let mut out = match first {
        'Ǆ' | 'ǅ' | 'ǆ' => "ǅ".to_string(),
        'Ǉ' | 'ǈ' | 'ǉ' => "ǈ".to_string(),
        'Ǌ' | 'ǋ' | 'ǌ' => "ǋ".to_string(),
        'Ǳ' | 'ǲ' | 'ǳ' => "ǲ".to_string(),
        c => c.to_uppercase().collect(),
    };
    out.push_str(&chars.as_str().to_lowercase());
    out
}

/// Python `max(a, b)` for floats: the first argument unless the second is strictly greater.
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python `min(a, b)` for floats: the first argument unless the second is strictly smaller.
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

// ============================================================================
// Formatting and values
// ============================================================================

/// `template.format(**fields)` for templates whose only replacement fields are `{name}`: a single pass, so a
/// value containing `{question}` is not substituted again; `{{`/`}}` are literal braces.
pub fn py_format(template: &str, fields: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len() + 256);
    let mut rest = template;
    while let Some(i) = rest.find(['{', '}']) {
        out.push_str(&rest[..i]);
        let tail = &rest[i..];
        if tail.starts_with("{{") || tail.starts_with("}}") {
            out.push_str(&tail[..1]);
            rest = &tail[2..];
            continue;
        }
        let close = tail.find('}').unwrap_or_else(|| panic!("unbalanced brace in template at {i}"));
        let name = &tail[1..close];
        let value = fields.iter().find(|(k, _)| *k == name).unwrap_or_else(|| panic!("KeyError: '{name}'")).1;
        out.push_str(value);
        rest = &tail[close + 1..];
    }
    out.push_str(rest);
    out
}

/// `repr(v)` of a parsed JSON value as a Python object (`'text'`, `True`, `None`, `1.5`, `[1, 'a']`, `{'k': 1}`).
pub fn py_repr_value(v: &Value) -> String {
    match v {
        Value::String(s) => py_repr_str(s),
        Value::Array(items) => format!("[{}]", items.iter().map(py_repr_value).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter().map(|(k, v)| format!("{}: {}", py_repr_str(k), py_repr_value(v))).collect::<Vec<_>>().join(", ")
        ),
        other => py_str_value(other),
    }
}

/// `str(v)` of a parsed JSON value as a Python object: strings as-is, containers as their repr.
pub fn py_str_value(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match (n.as_i64(), n.as_u64()) {
            (Some(i), _) => i.to_string(),
            (None, Some(u)) => u.to_string(),
            _ => py_float_repr(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => s.clone(),
        Value::Array(_) | Value::Object(_) => py_repr_value(v),
    }
}

/// Python's type name of a parsed JSON value (for `'list' object has no attribute 'get'` messages).
pub fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Length of `xs[:n]` for a list of `len` items with Python's slice rules (negative `n` counts from the end).
pub fn py_prefix_len(len: usize, n: i64) -> usize {
    if n >= 0 {
        (n as usize).min(len)
    } else {
        len.saturating_sub(n.unsigned_abs() as usize)
    }
}

/// `json.loads(text)` as far as the LLM-facing code relies on it: strict JSON with Python dict semantics. Python
/// also accepted the `NaN`/`Infinity` literals and lone surrogate escapes, which are rejected here (an LLM reply
/// containing them takes the parse-error path).
pub fn py_json_loads(text: &str) -> Option<Value> {
    neuralgraph_memory::pycompat::loads(text)
}

static AIOHTTP_JSON_RE: std::sync::LazyLock<fancy_regex::Regex> =
    std::sync::LazyLock::new(|| py_regex(r"^application/(?:[\w.+-]+?\+)?json"));

/// Whether aiohttp's `response.json()` accepted a `Content-Type` header value (it raised `ContentTypeError`
/// for anything else).
pub fn aiohttp_accepts_json(content_type: &str) -> bool {
    py_search(&AIOHTTP_JSON_RE, &content_type.to_lowercase())
}
