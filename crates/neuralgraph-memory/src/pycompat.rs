//! Python-compatible primitives.
//!
//! The memory pipeline was first written in Python, and existing databases, prompts and JSON outputs encode
//! Python's exact semantics: `len()` counts code points, `str.split()` knows a few more whitespace characters
//! than Rust, `round()` is round-half-even on the exact binary value, `float()` accepts `"1_000"` and `"inf"`,
//! and `json.dumps` has its own separators and float repr. Every place that must stay byte-identical with what
//! the Python version wrote or prompted goes through these helpers instead of the nearest Rust idiom.

use serde_json::{Map, Number, Value};

/// Python's `str.isspace()` for one character: Unicode White_Space plus the four ASCII information separators
/// (U+001C..U+001F), which Python treats as whitespace and Rust does not.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `len(s)` in code points.
pub fn py_len(s: &str) -> usize {
    s.chars().count()
}

/// Byte offset of the `n`-th code point (or `s.len()` past the end).
fn char_offset(s: &str, n: usize) -> usize {
    s.char_indices().nth(n).map(|(i, _)| i).unwrap_or(s.len())
}

/// `s[:n]` in code points.
pub fn py_prefix(s: &str, n: usize) -> &str {
    &s[..char_offset(s, n)]
}

/// `s[start:end]` in code points (`end=None` means to the end).
pub fn py_slice(s: &str, start: usize, end: Option<usize>) -> &str {
    let a = char_offset(s, start);
    let b = match end {
        Some(e) if e <= start => return "",
        Some(e) => char_offset(s, e),
        None => s.len(),
    };
    &s[a..b]
}

/// `s[-n:]` in code points.
pub fn py_suffix(s: &str, n: usize) -> &str {
    let len = py_len(s);
    if n >= len {
        s
    } else {
        py_slice(s, len - n, None)
    }
}

/// `s.strip()` with Python's whitespace definition.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// `s.rstrip()` with Python's whitespace definition.
pub fn py_rstrip(s: &str) -> &str {
    s.trim_end_matches(is_py_space)
}

/// `s.strip(chars)`.
pub fn py_strip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_matches(|c| chars.contains(c))
}

/// `s.split()` (no separator): runs of Python whitespace, no empty pieces.
pub fn py_split_whitespace(s: &str) -> Vec<&str> {
    s.split(is_py_space).filter(|p| !p.is_empty()).collect()
}

/// Python truthiness of a JSON value (`None`, `False`, `0`, `0.0`, `""`, `[]`, `{}` are falsy).
pub fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b` on JSON values: the first truthy one, else the last.
pub fn py_or<'a>(values: &[Option<&'a Value>]) -> Option<&'a Value> {
    let mut last = None;
    for v in values {
        last = *v;
        if let Some(x) = v {
            if py_truthy(x) {
                return Some(x);
            }
        }
    }
    last
}

/// `repr(float)` — shortest round-trip digits, fixed notation for decimal exponents in (-4, 16], otherwise
/// `1e-05` / `1.5e+16` style.
pub fn py_float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if x == 0.0 {
        return if x.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    // Rust's `{:e}` gives the shortest round-trip digits as d.ddde±N.
    let sci = format!("{:e}", x.abs());
    let (mant, exp) = sci.split_once('e').expect("exponent");
    let exp: i32 = exp.parse().expect("exponent int");
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let decpt = exp + 1; // position of the decimal point relative to the digit string
    let sign = if x < 0.0 { "-" } else { "" };
    let n = digits.len() as i32;
    let body = if decpt > -4 && decpt <= 16 {
        if decpt <= 0 {
            format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
        } else if decpt >= n {
            format!("{}{}.0", digits, "0".repeat((decpt - n) as usize))
        } else {
            format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
        }
    } else {
        let e = decpt - 1;
        let m = if digits.len() > 1 { format!("{}.{}", &digits[..1], &digits[1..]) } else { digits.clone() };
        format!("{}e{}{:02}", m, if e < 0 { '-' } else { '+' }, e.abs())
    };
    format!("{sign}{body}")
}

/// `round(x, ndigits)` for floats: correctly rounded, half-to-even on the exact binary value (Rust's
/// fixed-precision formatting implements exactly that, then the decimal string is parsed back like CPython does).
pub fn py_round(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits, x).parse().unwrap_or(x)
}

/// `int(round(x))` — banker's rounding to an integer.
pub fn py_round_int(x: f64) -> i64 {
    x.round_ties_even() as i64
}

/// `format(x, ".Nf")`.
pub fn py_fixed(x: f64, ndigits: usize) -> String {
    format!("{:.*}", ndigits, x)
}

/// First code point (the zero) of every run of Unicode decimal digits (category Nd, Unicode 14 as in Python 3.11).
#[rustfmt::skip]
const DECIMAL_ZEROS: [u32; 66] = [
    0x30, 0x660, 0x6F0, 0x7C0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66, 0xDE6, 0xE50, 0xED0,
    0xF20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80, 0x1A90, 0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620,
    0xA8D0, 0xA900, 0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10, 0x104A0, 0x10D30, 0x11066, 0x110F0, 0x11136, 0x111D0,
    0x112F0, 0x11450, 0x114D0, 0x11650, 0x116C0, 0x11730, 0x118E0, 0x11950, 0x11C50, 0x11D50, 0x11DA0, 0x16A60,
    0x16AC0, 0x16B50, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6, 0x1E140, 0x1E2F0, 0x1E950, 0x1FBF0,
];

/// The value of a Unicode decimal digit (`unicodedata.decimal`), any script.
pub fn decimal_value(c: char) -> Option<u32> {
    let cp = c as u32;
    DECIMAL_ZEROS.iter().find(|z| cp >= **z && cp <= **z + 9).map(|z| cp - z)
}

/// Python's numeric parsers first map every Unicode decimal digit to ASCII and Unicode whitespace to spaces.
fn ascii_digits(s: &str) -> String {
    s.chars()
        .map(|c| match decimal_value(c) {
            Some(d) if !c.is_ascii() => char::from_digit(d, 10).expect("digit"),
            _ if is_py_space(c) => ' ',
            _ => c,
        })
        .collect()
}

/// `float(s)` for strings: surrounding whitespace, a sign, `inf`/`infinity`/`nan` in any case, and `_`
/// digit separators are accepted; anything else is a `ValueError` (`None`).
pub fn py_float_parse(s: &str) -> Option<f64> {
    let s = ascii_digits(s);
    let t = py_strip(&s);
    if t.is_empty() {
        return None;
    }
    let cleaned: String = if t.contains('_') {
        let chars: Vec<char> = t.chars().collect();
        for (i, c) in chars.iter().enumerate() {
            if *c == '_' {
                let ok = i > 0 && i + 1 < chars.len() && chars[i - 1].is_ascii_digit() && chars[i + 1].is_ascii_digit();
                if !ok {
                    return None;
                }
            }
        }
        chars.into_iter().filter(|c| *c != '_').collect()
    } else {
        t.to_string()
    };
    let lower = cleaned.to_ascii_lowercase();
    let unsigned = lower.trim_start_matches(['+', '-']);
    if lower.len() - unsigned.len() > 1 {
        return None;
    }
    match unsigned {
        "inf" | "infinity" => {
            return Some(if lower.starts_with('-') { f64::NEG_INFINITY } else { f64::INFINITY });
        }
        "nan" => return Some(f64::NAN),
        _ => {}
    }
    if !unsigned.chars().all(|c| c.is_ascii_digit() || matches!(c, '.' | 'e' | '+' | '-')) {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

/// `float(v)` for a JSON value; `None` means Python would raise `TypeError`/`ValueError`.
pub fn py_float_from_value(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => py_float_parse(s),
        _ => None,
    }
}

/// `int(s)` for strings: whitespace, sign, `_` separators between digits.
pub fn py_int_parse(s: &str) -> Option<i64> {
    let s = ascii_digits(s);
    let t = py_strip(&s);
    let (neg, digits) = match t.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
        return None;
    }
    if !digits.chars().all(|c| c.is_ascii_digit() || c == '_') {
        return None;
    }
    let clean: String = digits.chars().filter(|c| *c != '_').collect();
    let v: i64 = clean.parse().ok()?;
    Some(if neg { -v } else { v })
}

/// Why `int(v)` failed, phrased like CPython (used for tool-argument errors).
pub enum PyIntError {
    /// `ValueError: invalid literal for int() with base 10: '...'`
    Value(String),
    /// `TypeError: int() argument must be ...`
    Type(String),
}

/// `int(v)` for a JSON value: ints as-is, floats truncate, bools are 0/1, strings parse.
pub fn py_int_from_value(v: &Value) -> Result<i64, PyIntError> {
    match v {
        Value::Bool(b) => Ok(*b as i64),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if let Some(f) = n.as_f64() {
                if f.is_finite() {
                    Ok(f.trunc() as i64)
                } else {
                    Err(PyIntError::Value(format!("cannot convert float {} to integer", py_float_repr(f))))
                }
            } else {
                Err(PyIntError::Value("integer out of range".into()))
            }
        }
        Value::String(s) => py_int_parse(s)
            .ok_or_else(|| PyIntError::Value(format!("invalid literal for int() with base 10: {}", py_repr_str(s)))),
        Value::Null => Err(PyIntError::Type(
            "int() argument must be a string, a bytes-like object or a real number, not 'NoneType'".into(),
        )),
        Value::Array(_) => Err(PyIntError::Type(
            "int() argument must be a string, a bytes-like object or a real number, not 'list'".into(),
        )),
        Value::Object(_) => Err(PyIntError::Type(
            "int() argument must be a string, a bytes-like object or a real number, not 'dict'".into(),
        )),
    }
}

/// `str(v)` for a JSON scalar (`True`, `None`, `1.0`, ...); containers fall back to `json.dumps`.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => number_repr(n),
        Value::String(s) => s.clone(),
        other => dumps(other, &JsonFormat::python_default()),
    }
}

/// `repr(s)` for a string: single quotes unless the text contains `'` and no `"`.
pub fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if is_unprintable(c) => {
                let cp = c as u32;
                if cp <= 0xff {
                    out.push_str(&format!("\\x{cp:02x}"));
                } else if cp <= 0xffff {
                    out.push_str(&format!("\\u{cp:04x}"));
                } else {
                    out.push_str(&format!("\\U{cp:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Approximation of `not str.isprintable()` for non-ASCII code points: C0/C1 controls, separators other than
/// the plain space, and unassigned/format characters that matter in practice.
fn is_unprintable(c: char) -> bool {
    let cp = c as u32;
    (0x80..=0xa0).contains(&cp)
        || cp == 0xad
        || (0x2000..=0x200f).contains(&cp)
        || (0x2028..=0x202f).contains(&cp)
        || (0x205f..=0x206f).contains(&cp)
        || cp == 0x3000
        || cp == 0xfeff
        || (0xfff9..=0xfffb).contains(&cp)
        || (0xd800..=0xdfff).contains(&cp)
}

/// Python-style repr of a `{str: int}` mapping, e.g. `{'queued': 18}` (CLI output).
pub fn py_repr_counts(m: &indexmap::IndexMap<String, i64>) -> String {
    let parts: Vec<String> = m.iter().map(|(k, v)| format!("{}: {}", py_repr_str(k), v)).collect();
    format!("{{{}}}", parts.join(", "))
}

fn number_repr(n: &Number) -> String {
    if let Some(i) = n.as_i64() {
        i.to_string()
    } else if let Some(u) = n.as_u64() {
        u.to_string()
    } else {
        py_float_repr(n.as_f64().unwrap_or(f64::NAN))
    }
}

// ---------------------------------------------------------------------------------------------
// json.dumps
// ---------------------------------------------------------------------------------------------

/// The `json.dumps` knobs the Python code used.
#[derive(Clone, Debug)]
pub struct JsonFormat {
    pub ensure_ascii: bool,
    pub sort_keys: bool,
    pub indent: Option<usize>,
    pub item_sep: &'static str,
    pub key_sep: &'static str,
}

impl JsonFormat {
    /// `json.dumps(v)`
    pub fn python_default() -> Self {
        JsonFormat { ensure_ascii: true, sort_keys: false, indent: None, item_sep: ", ", key_sep: ": " }
    }
    /// `json.dumps(v, ensure_ascii=False)`
    pub fn unicode() -> Self {
        JsonFormat { ensure_ascii: false, ..Self::python_default() }
    }
    /// `json.dumps(v, ensure_ascii=False, sort_keys=True)` — how the store writes every JSON column.
    pub fn stored() -> Self {
        JsonFormat { ensure_ascii: false, sort_keys: true, ..Self::python_default() }
    }
    /// `json.dumps(v, ensure_ascii=False, indent=1)` — CLI and MCP text output.
    pub fn pretty() -> Self {
        JsonFormat { ensure_ascii: false, sort_keys: false, indent: Some(1), item_sep: ",", key_sep: ": " }
    }
    /// `json.dumps(v, ensure_ascii=False, separators=(",", ":"))` — MCP stdio lines.
    pub fn compact() -> Self {
        JsonFormat { ensure_ascii: false, sort_keys: false, indent: None, item_sep: ",", key_sep: ":" }
    }
}

/// Serialize like CPython's `json.dumps` with the given options.
pub fn dumps(v: &Value, fmt: &JsonFormat) -> String {
    let mut out = String::new();
    write_value(&mut out, v, fmt, 0);
    out
}

fn write_value(out: &mut String, v: &Value, fmt: &JsonFormat, level: usize) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if n.is_f64() {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f.is_nan() {
                    out.push_str("NaN");
                } else if f.is_infinite() {
                    out.push_str(if f > 0.0 { "Infinity" } else { "-Infinity" });
                } else {
                    out.push_str(&py_float_repr(f));
                }
            } else {
                out.push_str(&number_repr(n));
            }
        }
        Value::String(s) => write_string(out, s, fmt.ensure_ascii),
        Value::Array(a) => {
            if a.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            for (i, item) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(fmt.item_sep);
                }
                newline_indent(out, fmt, level + 1);
                write_value(out, item, fmt, level + 1);
            }
            newline_indent(out, fmt, level);
            out.push(']');
        }
        Value::Object(o) => {
            if o.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push('{');
            let mut entries: Vec<(&String, &Value)> = o.iter().collect();
            if fmt.sort_keys {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            for (i, (k, val)) in entries.into_iter().enumerate() {
                if i > 0 {
                    out.push_str(fmt.item_sep);
                }
                newline_indent(out, fmt, level + 1);
                write_string(out, k, fmt.ensure_ascii);
                out.push_str(fmt.key_sep);
                write_value(out, val, fmt, level + 1);
            }
            newline_indent(out, fmt, level);
            out.push('}');
        }
    }
}

fn newline_indent(out: &mut String, fmt: &JsonFormat, level: usize) {
    if let Some(n) = fmt.indent {
        out.push('\n');
        out.push_str(&" ".repeat(n * level));
    }
}

fn write_string(out: &mut String, s: &str, ensure_ascii: bool) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ensure_ascii && (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `json.loads` for the cases the pipeline relies on (strict JSON, objects keep key order, duplicate keys keep
/// the first position and the last value — exactly what a Python dict does).
pub fn loads(s: &str) -> Option<Value> {
    serde_json::from_str(s).ok()
}

/// Build a JSON object from `(key, value)` pairs, preserving order.
pub fn obj<I, K>(pairs: I) -> Value
where
    I: IntoIterator<Item = (K, Value)>,
    K: Into<String>,
{
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert(k.into(), v);
    }
    Value::Object(m)
}

/// A JSON float (Python `float`), keeping NaN/inf out of serde's way.
pub fn fnum(x: f64) -> Value {
    Number::from_f64(x).map(Value::Number).unwrap_or(Value::Null)
}

/// `str`/`None` → JSON.
pub fn opt_str(s: &Option<String>) -> Value {
    match s {
        Some(v) => Value::String(v.clone()),
        None => Value::Null,
    }
}

/// Expand a Python `re.sub` replacement template (`\1`, `\g<name>`, `\n`, ...). Unknown escapes of ASCII
/// letters are errors, as in Python (`re.error: bad escape`).
pub fn py_expand_template(template: &str, caps: &regex::Captures) -> Result<String, String> {
    let chars: Vec<char> = template.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c != '\\' {
            out.push(c);
            i += 1;
            continue;
        }
        if i + 1 >= chars.len() {
            return Err("bad escape (end of pattern)".into());
        }
        let n = chars[i + 1];
        match n {
            'n' => out.push('\n'),
            't' => out.push('\t'),
            'r' => out.push('\r'),
            'f' => out.push('\u{0c}'),
            'v' => out.push('\u{0b}'),
            'a' => out.push('\u{07}'),
            'b' => out.push('\u{08}'),
            '\\' => out.push('\\'),
            'g' => {
                let rest: String = chars[i + 2..].iter().collect();
                if !rest.starts_with('<') {
                    return Err("missing <".into());
                }
                let end = rest.find('>').ok_or("missing >")?;
                let name = &rest[1..end];
                let text = match name.parse::<usize>() {
                    Ok(idx) => caps.get(idx).map(|m| m.as_str()),
                    Err(_) => caps.name(name).map(|m| m.as_str()),
                };
                out.push_str(text.unwrap_or(""));
                i += 2 + rest[..=end].chars().count();
                continue;
            }
            d if d.is_ascii_digit() => {
                let mut j = i + 1;
                let mut num = String::new();
                while j < chars.len() && chars[j].is_ascii_digit() && num.len() < 2 {
                    num.push(chars[j]);
                    j += 1;
                }
                let idx: usize = num.parse().unwrap_or(0);
                out.push_str(caps.get(idx).map(|m| m.as_str()).unwrap_or(""));
                i = j;
                continue;
            }
            l if l.is_ascii_alphabetic() => return Err(format!("bad escape \\{l}")),
            other => {
                out.push('\\');
                out.push(other);
            }
        }
        i += 2;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_repr_matches_python() {
        let cases = [
            (0.1, "0.1"),
            (1.0, "1.0"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (123456789012345678.0, "1.2345678901234568e+17"),
            (-2.5, "-2.5"),
            (0.30000000000000004, "0.30000000000000004"),
        ];
        for (x, want) in cases {
            assert_eq!(py_float_repr(x), want, "{x}");
        }
    }

    #[test]
    fn json_formats() {
        let v: Value = serde_json::from_str(r#"{"b": [1, 2.0, "é"], "a": {}}"#).unwrap();
        assert_eq!(dumps(&v, &JsonFormat::python_default()), "{\"b\": [1, 2.0, \"\\u00e9\"], \"a\": {}}");
        assert_eq!(dumps(&v, &JsonFormat::stored()), r#"{"a": {}, "b": [1, 2.0, "é"]}"#);
        assert_eq!(dumps(&v, &JsonFormat::pretty()), "{\n \"b\": [\n  1,\n  2.0,\n  \"é\"\n ],\n \"a\": {}\n}");
    }

    #[test]
    fn float_parse_like_python() {
        assert_eq!(py_float_parse(" 0.9 "), Some(0.9));
        assert_eq!(py_float_parse("1_000"), Some(1000.0));
        assert!(py_float_parse("1__0").is_none());
        assert!(py_float_parse("high").is_none());
        assert_eq!(py_float_parse("-Infinity"), Some(f64::NEG_INFINITY));
        assert!(py_float_parse("nan").unwrap().is_nan());
    }
}
