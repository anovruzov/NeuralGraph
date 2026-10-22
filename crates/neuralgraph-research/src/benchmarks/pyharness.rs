//! The bits of Python's standard library the harness scripts leaned on, shared by the harness ports: argparse's
//! `type=int` / `type=float` conversions, `pathlib.Path(...).expanduser()` and its `str()`, `json.dumps(indent=N)`,
//! `round()` over Python numbers, a pinnable `time.perf_counter()`, and the repository root the scripts resolved
//! their dataset paths against.
//!
//! None of this is harness logic; it exists so the ports can stay line-for-line with the Python and still print the
//! same bytes.

use neuralgraph_memory::error::MemoryError;
use neuralgraph_memory::pycompat::{fnum, py_float_parse, py_int_parse, py_repr_str, py_round, JsonFormat};
use serde_json::Value;
use std::cell::Cell;
use std::fmt;
use std::path::PathBuf;
use std::time::Instant;

/// A Python exception that ended a harness run, rendered `Type: message` like the last traceback line.
#[derive(Debug, Clone, PartialEq)]
pub struct HarnessError(pub String);

impl fmt::Display for HarnessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for HarnessError {}

impl HarnessError {
    pub fn new(kind: &str, message: impl fmt::Display) -> Self {
        HarnessError(format!("{kind}: {message}"))
    }
}

impl From<MemoryError> for HarnessError {
    fn from(e: MemoryError) -> Self {
        HarnessError(e.qualified())
    }
}

impl From<std::io::Error> for HarnessError {
    fn from(e: std::io::Error) -> Self {
        HarnessError::new("OSError", e)
    }
}

impl From<rusqlite::Error> for HarnessError {
    fn from(e: rusqlite::Error) -> Self {
        HarnessError::new("sqlite3.Error", e)
    }
}

impl From<serde_json::Error> for HarnessError {
    fn from(e: serde_json::Error) -> Self {
        HarnessError::new("json.JSONDecodeError", e)
    }
}

pub type HResult<T> = Result<T, HarnessError>;

/// The repository root (`Path(__file__).resolve().parents[2]` in the scripts, which lived in `research/benchmarks/`).
pub fn repo_root() -> PathBuf {
    let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    crate_dir.parent().and_then(|p| p.parent()).map(PathBuf::from).unwrap_or(crate_dir)
}

thread_local! {
    static FIXED_PERF_COUNTER: Cell<Option<f64>> = const { Cell::new(None) };
    static PERF_ORIGIN: Instant = Instant::now();
}

/// `time.perf_counter()`: seconds from an arbitrary origin, or the pinned value on this thread (tests and fixture
/// replays pin it so the timing fields of the reports are reproducible).
pub fn perf_counter() -> f64 {
    if let Some(v) = FIXED_PERF_COUNTER.with(|c| c.get()) {
        return v;
    }
    PERF_ORIGIN.with(|o| o.elapsed().as_secs_f64())
}

/// Pin (`Some`) or release (`None`) [`perf_counter`] on this thread.
pub fn set_fixed_perf_counter(value: Option<f64>) {
    FIXED_PERF_COUNTER.with(|c| c.set(value));
}

/// argparse `type=int`: `int(text)`, else argparse's "invalid int value" error.
pub fn parse_py_int(text: &str) -> Result<i64, String> {
    py_int_parse(text).ok_or_else(|| format!("invalid int value: {}", py_repr_str(text)))
}

/// argparse `type=float`: `float(text)`, else argparse's "invalid float value" error.
pub fn parse_py_float(text: &str) -> Result<f64, String> {
    py_float_parse(text).ok_or_else(|| format!("invalid float value: {}", py_repr_str(text)))
}

/// `str(PurePosixPath(text))`: empty and `.` components dropped, repeated and trailing slashes collapsed (`..` is
/// kept, exactly two leading slashes are preserved as POSIX allows), and `.` for an empty path.
pub fn pathlib_str(text: &str) -> String {
    let root = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = text.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    let joined = format!("{root}{}", parts.join("/"));
    if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

/// `str(Path(text).expanduser())`. `~` and `~/...` use `$HOME`; `~user` forms are left as written (Python looked
/// the user up in the password database, which this port does not do).
pub fn expanduser(text: &str) -> String {
    let normal = pathlib_str(text);
    let first = normal.split('/').next().unwrap_or("");
    if first == "~" {
        if let Ok(home) = std::env::var("HOME") {
            let rest = &normal[1..];
            return pathlib_str(&format!("{home}{rest}"));
        }
    }
    normal
}

/// `json.dumps(v, indent=n)`: ASCII-escaped, `","` between items and `": "` after keys.
pub fn json_indent(n: usize) -> JsonFormat {
    JsonFormat { ensure_ascii: true, sort_keys: false, indent: Some(n), item_sep: ",", key_sep: ": " }
}

/// A Python number as the harnesses summed and rounded them: `int` stays `int` until a `float` joins.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum PyNum {
    Int(i64),
    Float(f64),
}

impl PyNum {
    pub fn as_f64(self) -> f64 {
        match self {
            PyNum::Int(i) => i as f64,
            PyNum::Float(f) => f,
        }
    }

    /// `round(x, ndigits)`: an `int` comes back unchanged (and stays an `int`).
    pub fn round(self, ndigits: usize) -> PyNum {
        match self {
            PyNum::Int(i) => PyNum::Int(i),
            PyNum::Float(f) => PyNum::Float(py_round(f, ndigits)),
        }
    }

    pub fn to_value(self) -> Value {
        match self {
            PyNum::Int(i) => Value::from(i),
            PyNum::Float(f) => fnum(f),
        }
    }
}

/// A JSON value as a Python number (`True`/`False` count as 1/0, like `sum()` over bools).
pub fn py_num(v: &Value) -> HResult<PyNum> {
    match v {
        Value::Bool(b) => Ok(PyNum::Int(*b as i64)),
        Value::Number(n) => match n.as_i64() {
            Some(i) => Ok(PyNum::Int(i)),
            None => Ok(PyNum::Float(n.as_f64().unwrap_or(f64::NAN))),
        },
        other => Err(HarnessError::new(
            "TypeError",
            format!("unsupported operand type(s) for +: 'int' and '{}'", py_type_name(other)),
        )),
    }
}

/// Python 3.11 `sum(values)`: left to right from the integer 0, no compensation.
pub fn py_sum<'a>(values: impl IntoIterator<Item = &'a Value>) -> HResult<PyNum> {
    let mut acc = PyNum::Int(0);
    for v in values {
        acc = match (acc, py_num(v)?) {
            (PyNum::Int(a), PyNum::Int(b)) => PyNum::Int(a + b),
            (a, b) => PyNum::Float(a.as_f64() + b.as_f64()),
        };
    }
    Ok(acc)
}

/// `type(v).__name__` for a JSON value.
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

/// `d[key]` on a JSON object, with Python's `KeyError` when absent.
pub fn py_getitem<'a>(v: &'a Value, key: &str) -> HResult<&'a Value> {
    v.get(key).ok_or_else(|| HarnessError::new("KeyError", py_repr_str(key)))
}

/// `d[key]` that must be a `str`.
pub fn py_getstr<'a>(v: &'a Value, key: &str) -> HResult<&'a str> {
    let item = py_getitem(v, key)?;
    item.as_str().ok_or_else(|| HarnessError::new("TypeError", format!("expected str for {key:?}, got {item}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pathlib_str_normalises_like_pure_posix_path() {
        assert_eq!(pathlib_str("/tmp//a.db"), "/tmp/a.db");
        assert_eq!(pathlib_str("./x.db"), "x.db");
        assert_eq!(pathlib_str("a/./b/"), "a/b");
        assert_eq!(pathlib_str("../a"), "../a");
        assert_eq!(pathlib_str(""), ".");
        assert_eq!(pathlib_str("//srv/x"), "//srv/x");
        assert_eq!(pathlib_str("///srv/x"), "/srv/x");
    }

    #[test]
    fn py_sum_keeps_python_number_kinds() {
        let ints = [Value::from(1), Value::Bool(true)];
        assert_eq!(py_sum(&ints).unwrap(), PyNum::Int(2));
        let mixed = [Value::from(1), fnum(0.5)];
        assert_eq!(py_sum(&mixed).unwrap(), PyNum::Float(1.5));
        assert_eq!(py_sum(&[]).unwrap().round(1), PyNum::Int(0));
    }
}
