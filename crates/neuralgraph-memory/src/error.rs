//! One error type for the whole pipeline.
//!
//! Job rows persist failures as `"<ExceptionName>: <message>"` (the dashboard and operators read `last_error`), so
//! each variant knows the exception name the Python implementation recorded for the same failure.

use std::fmt;

#[derive(Debug)]
pub enum MemoryError {
    /// A generation/embedding call failed after all retries.
    Llm(String),
    /// The model returned no JSON object at all; the batch is retried (with variation) rather than dropped.
    UnparseableOutput(String),
    /// A worker tried to commit a plan for jobs it no longer holds (lease expired and re-leased).
    LostLease(String),
    /// Invalid caller input (Python `ValueError`).
    Value(String),
    /// SQLite failure.
    Sqlite(rusqlite::Error),
    /// Anything else, with the exception-style name to record.
    Other(&'static str, String),
}

pub type Result<T> = std::result::Result<T, MemoryError>;

impl MemoryError {
    /// The Python exception class name recorded for this failure.
    pub fn type_name(&self) -> &'static str {
        match self {
            MemoryError::Llm(_) => "LLMError",
            MemoryError::UnparseableOutput(_) => "UnparseableOutput",
            MemoryError::LostLease(_) => "LostLease",
            MemoryError::Value(_) => "ValueError",
            MemoryError::Sqlite(e) => sqlite_exception_name(e),
            MemoryError::Other(name, _) => name,
        }
    }

    /// `str(exc)`.
    pub fn message(&self) -> String {
        match self {
            MemoryError::Llm(m)
            | MemoryError::UnparseableOutput(m)
            | MemoryError::LostLease(m)
            | MemoryError::Value(m)
            | MemoryError::Other(_, m) => m.clone(),
            MemoryError::Sqlite(e) => sqlite_message(e),
        }
    }

    /// `f"{type(exc).__name__}: {exc}"`.
    pub fn qualified(&self) -> String {
        format!("{}: {}", self.type_name(), self.message())
    }
}

fn sqlite_message(e: &rusqlite::Error) -> String {
    match e {
        rusqlite::Error::SqliteFailure(_, Some(msg)) => msg.clone(),
        other => other.to_string(),
    }
}

fn sqlite_exception_name(e: &rusqlite::Error) -> &'static str {
    use rusqlite::ffi::ErrorCode;
    match e {
        rusqlite::Error::SqliteFailure(err, _) => match err.code {
            ErrorCode::ConstraintViolation => "IntegrityError",
            ErrorCode::DatabaseBusy
            | ErrorCode::DatabaseLocked
            | ErrorCode::CannotOpen
            | ErrorCode::ReadOnly
            | ErrorCode::DiskFull
            | ErrorCode::SystemIoFailure => "OperationalError",
            _ => "DatabaseError",
        },
        _ => "OperationalError",
    }
}

impl fmt::Display for MemoryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message())
    }
}

impl std::error::Error for MemoryError {}

impl From<rusqlite::Error> for MemoryError {
    fn from(e: rusqlite::Error) -> Self {
        MemoryError::Sqlite(e)
    }
}

impl From<std::io::Error> for MemoryError {
    fn from(e: std::io::Error) -> Self {
        MemoryError::Other("OSError", e.to_string())
    }
}
