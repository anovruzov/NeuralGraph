//! Python `datetime` values as the research code used them.
//!
//! The engine mixed naive datetimes (`datetime.now()`, the harnesses' `created_at`) with aware UTC ones
//! (`datetime.now(timezone.utc)`, every dataclass default). Python keeps the two apart: subtracting one from the
//! other raises `TypeError`, and several helpers first "assume UTC" for naive values with
//! `.replace(tzinfo=timezone.utc)`. [`PyDateTime`] keeps the same distinction so arithmetic and `isoformat()`
//! come out exactly as they did.
//!
//! "Now" comes from the product crate's clock (pinnable per thread for tests); naive local time adds the local
//! UTC offset, which tests can pin too with [`set_fixed_local_offset`].

use chrono::{DateTime, Datelike, FixedOffset, Local, NaiveDateTime, TimeDelta, TimeZone, Timelike, Utc};
use neuralgraph_memory::clock::utcnow;
use std::cell::Cell;
use std::cmp::Ordering;

thread_local! {
    static LOCAL_OFFSET: Cell<Option<i32>> = const { Cell::new(None) };
}

/// Pin (`Some(seconds east of UTC)`) or release the offset used for naive local "now" on this thread.
pub fn set_fixed_local_offset(offset_seconds: Option<i32>) {
    LOCAL_OFFSET.with(|c| c.set(offset_seconds));
}

/// `TypeError: can't subtract offset-naive and offset-aware datetimes` (and the comparison variant).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NaiveAwareMismatch;

impl std::fmt::Display for NaiveAwareMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("can't subtract offset-naive and offset-aware datetimes")
    }
}

impl std::error::Error for NaiveAwareMismatch {}

/// A Python `datetime`: wall-clock fields plus an optional fixed UTC offset (`None` = naive).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct PyDateTime {
    pub naive: NaiveDateTime,
    pub offset_seconds: Option<i32>,
}

impl PyDateTime {
    pub fn naive(naive: NaiveDateTime) -> Self {
        PyDateTime { naive, offset_seconds: None }
    }

    pub fn utc(dt: DateTime<Utc>) -> Self {
        PyDateTime { naive: dt.naive_utc(), offset_seconds: Some(0) }
    }

    /// `datetime.now(timezone.utc)`.
    pub fn now_utc() -> Self {
        PyDateTime::utc(utcnow())
    }

    /// `datetime.now()`: naive local wall time.
    pub fn now_local() -> Self {
        let now = utcnow();
        let offset = LOCAL_OFFSET
            .with(Cell::get)
            .unwrap_or_else(|| Local.offset_from_utc_datetime(&now.naive_utc()).local_minus_utc());
        PyDateTime::naive(now.naive_utc() + TimeDelta::seconds(offset as i64))
    }

    pub fn is_aware(&self) -> bool {
        self.offset_seconds.is_some()
    }

    /// The instant in UTC; naive values are taken as UTC (`.replace(tzinfo=timezone.utc)`).
    pub fn assume_utc(&self) -> DateTime<Utc> {
        let shifted = self.naive - TimeDelta::seconds(self.offset_seconds.unwrap_or(0) as i64);
        Utc.from_utc_datetime(&shifted)
    }

    /// `.replace(tzinfo=timezone.utc)` when naive, unchanged when aware.
    pub fn with_utc_if_naive(&self) -> Self {
        match self.offset_seconds {
            Some(_) => *self,
            None => PyDateTime { naive: self.naive, offset_seconds: Some(0) },
        }
    }

    /// `self - other` with Python's naive/aware rule.
    pub fn sub(&self, other: &PyDateTime) -> Result<TimeDelta, NaiveAwareMismatch> {
        match (self.offset_seconds, other.offset_seconds) {
            (None, None) => Ok(self.naive - other.naive),
            (Some(_), Some(_)) => Ok(self.assume_utc() - other.assume_utc()),
            _ => Err(NaiveAwareMismatch),
        }
    }

    /// `self < other` etc. with Python's rule (naive vs aware ordering raises).
    pub fn py_cmp(&self, other: &PyDateTime) -> Result<Ordering, NaiveAwareMismatch> {
        match (self.offset_seconds, other.offset_seconds) {
            (None, None) => Ok(self.naive.cmp(&other.naive)),
            (Some(_), Some(_)) => Ok(self.assume_utc().cmp(&other.assume_utc())),
            _ => Err(NaiveAwareMismatch),
        }
    }

    pub fn add(&self, delta: TimeDelta) -> Self {
        PyDateTime { naive: self.naive + delta, offset_seconds: self.offset_seconds }
    }

    pub fn year(&self) -> i32 {
        self.naive.year()
    }

    /// `datetime.isoformat()`.
    pub fn isoformat(&self) -> String {
        let n = &self.naive;
        let mut s = format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            n.year(),
            n.month(),
            n.day(),
            n.hour(),
            n.minute(),
            n.second()
        );
        let us = n.nanosecond() / 1000;
        if us != 0 {
            s.push_str(&format!(".{us:06}"));
        }
        if let Some(off) = self.offset_seconds {
            let sign = if off < 0 { '-' } else { '+' };
            let a = off.abs();
            s.push_str(&format!("{}{:02}:{:02}", sign, a / 3600, (a % 3600) / 60));
            if a % 60 != 0 {
                s.push_str(&format!(":{:02}", a % 60));
            }
        }
        s
    }

    /// `datetime.fromisoformat(s)` (Python 3.11 grammar, as implemented by the product crate).
    pub fn fromisoformat(s: &str) -> Option<Self> {
        let (naive, offset) = neuralgraph_memory::models::parse_iso_parts(s)?;
        Some(PyDateTime { naive, offset_seconds: offset })
    }

    /// The aware value as a chrono `DateTime<FixedOffset>` (naive taken as UTC).
    pub fn to_fixed(&self) -> DateTime<FixedOffset> {
        let tz = FixedOffset::east_opt(self.offset_seconds.unwrap_or(0)).expect("valid offset");
        tz.from_local_datetime(&self.naive).single().expect("fixed offsets are unambiguous")
    }
}

/// `timedelta.total_seconds()`.
pub fn total_seconds(delta: TimeDelta) -> f64 {
    // CPython: (days*86400 + seconds)*10**6 + microseconds, divided by 10**6
    let micros = delta.num_microseconds().unwrap_or(i64::MAX);
    micros as f64 / 1e6
}

/// `timedelta.days` (floor division of the duration into whole days).
pub fn delta_days(delta: TimeDelta) -> i64 {
    // timedelta normalises to (days, 0 <= seconds < 86400, 0 <= microseconds < 10**6), so `.days` floors
    let micros = delta.num_microseconds().expect("timedelta within range");
    micros.div_euclid(86_400_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isoformat_round_trip() {
        let d = PyDateTime::fromisoformat("2023-05-08T13:56:00.250000+00:00").unwrap();
        assert_eq!(d.isoformat(), "2023-05-08T13:56:00.250000+00:00");
        let n = PyDateTime::fromisoformat("2023-05-08T13:56:00").unwrap();
        assert!(!n.is_aware());
        assert!(d.sub(&n).is_err());
    }

    #[test]
    fn days_floor_like_python() {
        assert_eq!(delta_days(TimeDelta::hours(-1)), -1);
        assert_eq!(delta_days(TimeDelta::hours(25)), 1);
        assert_eq!(delta_days(TimeDelta::days(-2)), -2);
    }
}
