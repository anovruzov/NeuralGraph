//! Wall clock with a per-thread override.
//!
//! Recency priors, debounce windows and lease expiry all read "now". Tests and the golden-fixture checks pin it
//! (the Python suite monkeypatched `utcnow`); a thread-local keeps parallel tests from seeing each other's clock.
//! Everything that would otherwise call `Utc::now()` goes through [`utcnow`].

use chrono::{DateTime, Utc};
use std::cell::RefCell;

thread_local! {
    static FIXED: RefCell<Option<DateTime<Utc>>> = const { RefCell::new(None) };
}

/// Current UTC time, or the pinned instant on this thread.
pub fn utcnow() -> DateTime<Utc> {
    FIXED.with(|f| f.borrow().unwrap_or_else(Utc::now))
}

/// Pin (`Some`) or release (`None`) the clock for the current thread.
pub fn set_fixed(at: Option<DateTime<Utc>>) {
    FIXED.with(|f| *f.borrow_mut() = at);
}
