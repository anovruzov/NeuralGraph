//! Date parsing and relative-date resolution (port of the product's use of `NeuralGraph/temporal_utils.py`).
//!
//! The extractor annotates relative dates in the messages it shows the model ("yesterday [= 7 May 2023]") so the
//! model copies absolute dates, and it resolves a model-supplied `when` like "last month" against the message
//! date. Both have to produce exactly the strings the Python pipeline produced: the annotations are part of the
//! prompt, and the resolved dates end up in `event_time`. Several patterns use negative lookahead, so this module
//! uses `fancy_regex` where Python's `re` relied on it.

use chrono::{Datelike, NaiveDate, NaiveDateTime, NaiveTime, TimeDelta, Timelike};
use fancy_regex::{Captures, Regex as FRegex};
use indexmap::IndexMap;
use regex::Regex;
use std::sync::LazyLock;

#[rustfmt::skip]
pub const MONTH_NAMES: [&str; 12] = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december",
];
pub const DAY_NAMES: [&str; 7] = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

/// Weekday index (0 = Monday) per abbreviation, in the original dict order.
#[rustfmt::skip]
const DAY_ABBREVIATIONS: [(&str, u32); 18] = [
    ("mon", 0), ("mo", 0), ("tue", 1), ("tues", 1), ("tu", 1), ("wed", 2), ("weds", 2), ("we", 2), ("thu", 3),
    ("thur", 3), ("thurs", 3), ("th", 3), ("fri", 4), ("fr", 4), ("sat", 5), ("sa", 5), ("sun", 6), ("su", 6),
];

#[rustfmt::skip]
const NUMBER_WORDS: [(&str, i64); 12] = [
    ("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5), ("six", 6), ("seven", 7), ("eight", 8),
    ("nine", 9), ("ten", 10), ("eleven", 11), ("twelve", 12),
];

/// Full month name -> 1..12 (the `MONTH_NAMES` dict of the Python module).
pub fn month_number(name: &str) -> Option<u32> {
    MONTH_NAMES.iter().position(|m| *m == name).map(|i| i as u32 + 1)
}

fn title(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

fn month_title(m: u32) -> String {
    title(MONTH_NAMES[(m - 1) as usize])
}

/// A Python exception in the original code path (invalid date, overflow). Callers treat it like the
/// `except Exception` blocks around the original calls did.
#[derive(Debug, Clone, PartialEq)]
pub struct TemporalError(pub String);

type TResult<T> = Result<T, TemporalError>;

fn err<T>(msg: &str) -> TResult<T> {
    Err(TemporalError(msg.to_string()))
}

fn ymd(y: i64, m: u32, d: i64) -> TResult<NaiveDate> {
    if !(1..=9999).contains(&y) {
        return err("year out of range");
    }
    let d = u32::try_from(d).map_err(|_| TemporalError("day is out of range for month".into()))?;
    NaiveDate::from_ymd_opt(y as i32, m, d).ok_or_else(|| TemporalError("day is out of range for month".into()))
}

static LOCOMO_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(?:(\d{1,2}):(\d{2})[\s\x1c-\x1f]*(am|pm)[\s\x1c-\x1f]+on[\s\x1c-\x1f]+(\d{1,2})[\s\x1c-\x1f]+(\w+),?[\s\x1c-\x1f]*(\d{4}))").unwrap()
});
static ISO_PREFIX_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(?:\d{4}-\d{2}-\d{2})").unwrap());
static SIMPLE_DATE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(?:(\d{1,2})[\s\x1c-\x1f]+(\w+)[\s\x1c-\x1f]+(\d{4}))").unwrap());
static US_DATE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(?:(\w+)[\s\x1c-\x1f]+(\d{1,2}),?[\s\x1c-\x1f]*(\d{4}))").unwrap());

fn num(s: &str) -> TResult<i64> {
    s.parse::<i64>().map_err(|_| TemporalError(format!("invalid literal for int() with base 10: '{s}'")))
}

/// Parse "1:56 pm on 8 May, 2023", ISO ("2023-05-08T13:56:00", "2023-05-08"), "8 May 2023" or "May 8, 2023".
///
/// `Ok(None)` when nothing matches; `Err` where Python's `datetime(...)` would have raised (e.g. 31 February).
/// ISO values keep only their wall-clock time; the offset is returned separately when present.
pub fn parse_datetime_flexible(dt_str: Option<&str>) -> TResult<Option<FlexibleDateTime>> {
    let Some(s) = dt_str else { return Ok(None) };
    if s.is_empty() {
        return Ok(None);
    }
    let s = crate::pycompat::py_strip(s);

    if let Some(c) = LOCOMO_RE.captures(s) {
        let month = month_number(&c[5].to_lowercase());
        if let Some(month) = month {
            let mut hour = num(&c[1])?;
            let ampm = c[3].to_lowercase();
            if ampm == "pm" && hour != 12 {
                hour += 12;
            } else if ampm == "am" && hour == 12 {
                hour = 0;
            }
            let date = ymd(num(&c[6])?, month, num(&c[4])?)?;
            let minute = num(&c[2])?;
            let time = NaiveTime::from_hms_opt(hour as u32, minute as u32, 0)
                .ok_or_else(|| TemporalError("hour must be in 0..23".into()))?;
            return Ok(Some(FlexibleDateTime { naive: date.and_time(time), offset_seconds: None }));
        }
    }

    // ISO: Python only tries fromisoformat when the text contains 'T' or starts with YYYY-MM-DD.
    if s.contains('T') || ISO_PREFIX_RE.is_match(s) {
        if let Some((naive, offset)) = crate::models::parse_iso_parts(s) {
            return Ok(Some(FlexibleDateTime { naive, offset_seconds: offset }));
        }
    }

    if let Some(c) = SIMPLE_DATE_RE.captures(s) {
        if let Some(month) = month_number(&c[2].to_lowercase()) {
            let d = ymd(num(&c[3])?, month, num(&c[1])?)?;
            return Ok(Some(FlexibleDateTime { naive: d.and_time(NaiveTime::MIN), offset_seconds: None }));
        }
    }
    if let Some(c) = US_DATE_RE.captures(s) {
        if let Some(month) = month_number(&c[1].to_lowercase()) {
            let d = ymd(num(&c[3])?, month, num(&c[2])?)?;
            return Ok(Some(FlexibleDateTime { naive: d.and_time(NaiveTime::MIN), offset_seconds: None }));
        }
    }
    Ok(None)
}

/// Result of [`parse_datetime_flexible`]: wall-clock datetime plus the UTC offset if the input carried one.
#[derive(Clone, Debug, PartialEq)]
pub struct FlexibleDateTime {
    pub naive: NaiveDateTime,
    pub offset_seconds: Option<i32>,
}

impl FlexibleDateTime {
    /// `datetime.isoformat()` of the parsed value.
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

    /// `strftime("%Y-%m-%d")`.
    pub fn ymd(&self) -> String {
        format!("{:04}-{:02}-{:02}", self.naive.year(), self.naive.month(), self.naive.day())
    }
}

fn fre(p: &str) -> FRegex {
    FRegex::new(p).unwrap_or_else(|e| panic!("bad regex {p}: {e}"))
}

fn search(p: &FRegex, s: &str) -> bool {
    p.is_match(s).unwrap_or(false)
}

/// `re.sub(pattern, replacement, text, flags=re.IGNORECASE)`; `count=1` replaces only the first match.
fn sub_with<F: Fn(&Captures<'_, str>) -> String>(p: &FRegex, text: &str, first_only: bool, f: F) -> String {
    let mut out = String::with_capacity(text.len() + 32);
    let mut last = 0;
    let mut pos = 0;
    while pos <= text.len() {
        let caps = match p.captures_from_pos(text, pos) {
            Ok(Some(c)) => c,
            _ => break,
        };
        let m = caps.get(0).expect("group 0");
        out.push_str(&text[last..m.start()]);
        out.push_str(&f(&caps));
        last = m.end();
        if first_only {
            break;
        }
        pos = if m.end() == m.start() {
            // advance past an empty match by one character
            match text[m.end()..].chars().next() {
                Some(c) => m.end() + c.len_utf8(),
                None => break,
            }
        } else {
            m.end()
        };
    }
    out.push_str(&text[last..]);
    out
}

fn sub(p: &FRegex, text: &str, replacement: &str) -> String {
    sub_with(p, text, false, |_| replacement.to_string())
}

fn day_str(d: NaiveDateTime) -> String {
    format!("{} {} {}", d.day(), month_title(d.month()), d.year())
}

fn shift(d: NaiveDateTime, days: i64) -> TResult<NaiveDateTime> {
    let delta = TimeDelta::try_days(days).ok_or_else(|| TemporalError("date value out of range".into()))?;
    let out = d.checked_add_signed(delta).ok_or_else(|| TemporalError("date value out of range".into()))?;
    if !(1..=9999).contains(&out.year()) {
        return err("date value out of range");
    }
    Ok(out)
}

fn weekday(d: NaiveDateTime) -> i64 {
    d.weekday().num_days_from_monday() as i64
}

struct Patterns {
    yesterday: FRegex,
    last_night: FRegex,
    last_weekend: FRegex,
    last_week_search: FRegex,
    last_week: FRegex,
    this_week: FRegex,
    last_year: FRegex,
    last_month: FRegex,
    this_month: FRegex,
    this_year: FRegex,
    next_month: FRegex,
    few_weeks: FRegex,
    two_weeks: FRegex,
    days_ago: FRegex,
    two_days: FRegex,
    last_full_day: Vec<FRegex>,
    last_day: FRegex,
    this_day: FRegex,
    next_day: FRegex,
    on_day: FRegex,
    today: FRegex,
    tonight: FRegex,
    tomorrow: FRegex,
    this_period: Vec<(&'static str, FRegex)>,
    earlier_today: FRegex,
    other_day: FRegex,
    couple_days: FRegex,
    day_before: FRegex,
    next_week: FRegex,
    over_weekend: FRegex,
    word_days: Vec<(&'static str, i64, FRegex)>,
    a_week_ago: FRegex,
    years_ago: FRegex,
}

/// `(abbreviations sorted by length desc, stable) | full day names`, as built by the Python code.
fn all_days_pattern() -> String {
    let mut abbrevs: Vec<&str> = DAY_ABBREVIATIONS.iter().map(|(a, _)| *a).collect();
    abbrevs.sort_by_key(|a| std::cmp::Reverse(a.len()));
    format!("({}|{})", abbrevs.join("|"), DAY_NAMES.join("|"))
}

static P: LazyLock<Patterns> = LazyLock::new(|| {
    let days = all_days_pattern();
    Patterns {
        yesterday: fre(r"(?i)\byesterday\b"),
        last_night: fre(r"(?i)\blast night\b"),
        last_weekend: fre(r"(?i)\blast weekend\b"),
        last_week_search: fre(r"\blast week\b(?!end)"),
        last_week: fre(r"(?i)\blast week\b(?!end)"),
        this_week: fre(r"(?i)\bthis week\b"),
        last_year: fre(r"(?i)\blast year\b"),
        last_month: fre(r"(?i)\blast month\b"),
        this_month: fre(r"(?i)\bthis month\b"),
        this_year: fre(r"(?i)\bthis year\b"),
        next_month: fre(r"(?i)\bnext month\b"),
        few_weeks: fre(r"(?i)\b(a )?few weeks? ago\b"),
        two_weeks: fre(r"(?i)\b(two|2) weeks? ago\b"),
        days_ago: fre(r"(?i)\b([0-9]+) days? ago\b"),
        two_days: fre(r"(?i)\btwo days? ago\b"),
        last_full_day: DAY_NAMES.iter().map(|d| fre(&format!(r"(?i)\blast {d}\b"))).collect(),
        last_day: fre(&format!(r"\blast[\s\x1c-\x1f]+{days}\.?\b(?![\s\x1c-\x1f]*\[=)")),
        this_day: fre(&format!(r"\bthis[\s\x1c-\x1f]+{days}\.?\b(?![\s\x1c-\x1f]*\[=)")),
        next_day: fre(&format!(r"\bnext[\s\x1c-\x1f]+{days}\.?\b(?![\s\x1c-\x1f]*\[=)")),
        on_day: fre(&format!(r"\bon[\s\x1c-\x1f]+{days}\.?\b(?![\s\x1c-\x1f]*\[=)")),
        today: fre(r"(?i)\btoday\b(?![\s\x1c-\x1f]*\[=)"),
        tonight: fre(r"(?i)\btonight\b(?![\s\x1c-\x1f]*\[=)"),
        tomorrow: fre(r"(?i)\btomorrow\b(?![\s\x1c-\x1f]*\[=)"),
        this_period: ["morning", "afternoon", "evening"]
            .into_iter()
            .map(|p| (p, fre(&format!(r"(?i)\bthis {p}\b(?![\s\x1c-\x1f]*\[=)"))))
            .collect(),
        earlier_today: fre(r"(?i)\bearlier today\b(?![\s\x1c-\x1f]*\[=)"),
        other_day: fre(r"(?i)\bthe other day\b(?![\s\x1c-\x1f]*\[=)"),
        couple_days: fre(r"(?i)\ba? ?couple (?:of )?days? ago\b(?![\s\x1c-\x1f]*\[=)"),
        day_before: fre(r"(?i)\b(?:the )?day before yesterday\b(?![\s\x1c-\x1f]*\[=)"),
        next_week: fre(r"(?i)\bnext week\b(?![\s\x1c-\x1f]*\[=)"),
        over_weekend: fre(r"(?i)\b(over|during) the weekend\b(?![\s\x1c-\x1f]*\[=)"),
        word_days: [("three", 3), ("four", 4), ("five", 5), ("six", 6), ("seven", 7)]
            .into_iter()
            .map(|(w, n)| (w, n, fre(&format!(r"(?i)\b{w} days? ago\b(?![\s\x1c-\x1f]*\[=)"))))
            .collect(),
        a_week_ago: fre(r"(?i)\ba week ago\b(?![\s\x1c-\x1f]*\[=)"),
        years_ago: fre(r"(?i)\b(ten|[0-9]+) years? ago\b"),
    }
});

fn weekday_of(day_match: &str) -> Option<u32> {
    DAY_ABBREVIATIONS
        .iter()
        .find(|(a, _)| *a == day_match)
        .map(|(_, i)| *i)
        .or_else(|| DAY_NAMES.iter().position(|d| *d == day_match).map(|i| i as u32))
}

fn escape(s: &str) -> String {
    fancy_regex::escape(s).into_owned()
}

/// Annotate every relative date reference in `content` with its resolved date (`yesterday [= 7 May 2023]`) and
/// return the resolved values (pattern key -> date string, in detection order).
pub fn resolve_relative_dates(
    content: &str,
    message_date: Option<NaiveDateTime>,
) -> TResult<(String, IndexMap<String, String>)> {
    let mut resolved: IndexMap<String, String> = IndexMap::new();
    let Some(md) = message_date else { return Ok((content.to_string(), resolved)) };
    if content.is_empty() {
        return Ok((content.to_string(), resolved));
    }
    let p = &*P;
    let mut result = content.to_string();
    let lower = content.to_lowercase();

    // 1. "yesterday" / "last night"
    if lower.contains("yesterday") || lower.contains("last night") {
        let y = shift(md, -1)?;
        let ds = day_str(y);
        resolved.insert("yesterday".into(), ds.clone());
        result = sub(&p.yesterday, &result, &format!("yesterday [= {ds}]"));
        result = sub(&p.last_night, &result, &format!("last night [= {ds}]"));
    }
    // 2. "last weekend"
    if lower.contains("last weekend") {
        let mut since_sat = (weekday(md) + 2) % 7;
        if since_sat == 0 {
            since_sat = 7;
        }
        let sat = shift(md, -since_sat)?;
        let sun = shift(sat, 1)?;
        let ds = format!("{}-{} {} {}", sat.day(), sun.day(), month_title(sat.month()), sat.year());
        resolved.insert("last_weekend".into(), ds.clone());
        result = sub(&p.last_weekend, &result, &format!("last weekend [= {ds}]"));
    }
    // 3. "last week" (not "last weekend")
    if search(&p.last_week_search, &lower) {
        let lw = shift(md, -7)?;
        let ws = shift(lw, -weekday(lw))?;
        let ds = format!("week of {}", day_str(ws));
        resolved.insert("last_week".into(), ds.clone());
        result = sub(&p.last_week, &result, &format!("last week [= {ds}]"));
    }
    // 4. "this week"
    if lower.contains("this week") {
        let ws = shift(md, -weekday(md))?;
        let ds = format!("week of {}", day_str(ws));
        resolved.insert("this_week".into(), ds.clone());
        result = sub(&p.this_week, &result, &format!("this week [= {ds}]"));
    }
    // 5. "last year"
    if lower.contains("last year") {
        let ly = md.year() as i64 - 1;
        resolved.insert("last_year".into(), ly.to_string());
        result = sub(&p.last_year, &result, &format!("last year [= {ly}]"));
    }
    // 6. "last month"
    if lower.contains("last month") {
        let (m, y) = if md.month() == 1 { (12, md.year() - 1) } else { (md.month() - 1, md.year()) };
        let ds = format!("{} {}", month_title(m), y);
        resolved.insert("last_month".into(), ds.clone());
        result = sub(&p.last_month, &result, &format!("last month [= {ds}]"));
    }
    // 6b. "this month"
    if lower.contains("this month") {
        let ds = format!("{} {}", month_title(md.month()), md.year());
        resolved.insert("this_month".into(), ds.clone());
        result = sub(&p.this_month, &result, &format!("this month [= {ds}]"));
    }
    // 6c. "this year"
    if lower.contains("this year") {
        let ds = md.year().to_string();
        resolved.insert("this_year".into(), ds.clone());
        result = sub(&p.this_year, &result, &format!("this year [= {ds}]"));
    }
    // 7. "next month"
    if lower.contains("next month") {
        let (m, y) = if md.month() == 12 { (1, md.year() + 1) } else { (md.month() + 1, md.year()) };
        let ds = format!("{} {}", month_title(m), y);
        resolved.insert("next_month".into(), ds.clone());
        result = sub(&p.next_month, &result, &format!("next month [= {ds}]"));
    }
    // 8. "a few weeks ago"
    if search(&p.few_weeks, &lower) {
        let d = shift(md, -21)?;
        let ds = format!("around {}", day_str(d));
        resolved.insert("few_weeks_ago".into(), ds.clone());
        result = sub(&p.few_weeks, &result, &format!("a few weeks ago [= {ds}]"));
    }
    // 9. "two weeks ago" / "2 weeks ago"
    if search(&p.two_weeks, &lower) {
        let d = shift(md, -14)?;
        let ds = format!("around {}", day_str(d));
        resolved.insert("two_weeks_ago".into(), ds.clone());
        result = sub(&p.two_weeks, &result, &format!("two weeks ago [= {ds}]"));
    }
    // 10. "X days ago" (numeric): the FIRST number decides the date used for every occurrence
    if let Ok(Some(c)) = p.days_ago.captures(lower.as_str()) {
        let n: i64 = c[1].parse().map_err(|_| TemporalError("Python int too large to convert to C int".into()))?;
        let d = shift(md, -n)?;
        let ds = day_str(d);
        resolved.insert(format!("{n}_days_ago"), ds.clone());
        result = sub_with(&p.days_ago, &result, false, |m| format!("{} days ago [= {ds}]", &m[1]));
    }
    // 11. "two days ago" (word)
    if lower.contains("two days ago") {
        let d = shift(md, -2)?;
        let ds = day_str(d);
        resolved.insert("two_days_ago".into(), ds.clone());
        result = sub(&p.two_days, &result, &format!("two days ago [= {ds}]"));
    }
    // 12. "last Monday" ... (full names)
    for (i, day) in DAY_NAMES.iter().enumerate() {
        let pat = &p.last_full_day[i];
        if search(pat, &lower) {
            let mut back = (weekday(md) - i as i64).rem_euclid(7);
            if back == 0 {
                back = 7;
            }
            let d = shift(md, -back)?;
            let ds = day_str(d);
            resolved.insert(format!("last_{day}"), ds.clone());
            result = sub(pat, &result, &format!("last {day} [= {ds}]"));
        }
    }
    // 13a-d. "last/this/next/on <day>" incl. abbreviations, one occurrence per detected match
    for (prefix, scan, direction) in [
        ("last", &p.last_day, Direction::Back),
        ("this", &p.this_day, Direction::This),
        ("next", &p.next_day, Direction::Next),
        ("on", &p.on_day, Direction::Back),
    ] {
        let matches: Vec<String> = scan
            .captures_iter(&lower)
            .filter_map(|c| c.ok())
            .map(|c| c.get(1).map(|m| m.as_str().to_lowercase()).unwrap_or_default())
            .collect();
        for day_match in matches {
            let Some(idx) = weekday_of(&day_match) else { continue };
            let idx = idx as i64;
            let target = match direction {
                Direction::Back => {
                    let mut back = (weekday(md) - idx).rem_euclid(7);
                    if back == 0 {
                        back = 7;
                    }
                    shift(md, -back)?
                }
                Direction::This => shift(md, (idx - weekday(md)).rem_euclid(7))?,
                Direction::Next => {
                    let mut fwd = (idx - weekday(md)).rem_euclid(7);
                    if fwd == 0 {
                        fwd = 7;
                    }
                    shift(md, fwd + 7)?
                }
            };
            let ds = day_str(target);
            let full = DAY_NAMES[idx as usize];
            resolved.insert(format!("{prefix}_{full}"), ds.clone());
            let one = fre(&format!(r"(?i)\b{prefix}[\s\x1c-\x1f]+{}\.?\b(?![\s\x1c-\x1f]*\[=)", escape(&day_match)));
            result = sub_with(&one, &result, true, |_| format!("{prefix} {full} [= {ds}]"));
        }
    }
    let today_str = day_str(md);
    // 13e. "today" and "tonight"
    if search(&p.today, &lower) {
        resolved.insert("today".into(), today_str.clone());
        result = sub(&p.today, &result, &format!("today [= {today_str}]"));
    }
    if search(&p.tonight, &lower) {
        resolved.insert("tonight".into(), today_str.clone());
        result = sub(&p.tonight, &result, &format!("tonight [= {today_str}]"));
    }
    // 13f. "tomorrow"
    if search(&p.tomorrow, &lower) {
        let ds = day_str(shift(md, 1)?);
        resolved.insert("tomorrow".into(), ds.clone());
        result = sub(&p.tomorrow, &result, &format!("tomorrow [= {ds}]"));
    }
    // 13g. "this morning/afternoon/evening"
    for (period, pat) in &p.this_period {
        if search(pat, &lower) {
            resolved.insert(format!("this_{period}"), today_str.clone());
            result = sub(pat, &result, &format!("this {period} [= {today_str}]"));
        }
    }
    // 13h. "earlier today"
    if search(&p.earlier_today, &lower) {
        resolved.insert("earlier_today".into(), today_str.clone());
        result = sub(&p.earlier_today, &result, &format!("earlier today [= {today_str}]"));
    }
    // 13i. "the other day"
    if search(&p.other_day, &lower) {
        let ds = format!("around {}", day_str(shift(md, -2)?));
        resolved.insert("the_other_day".into(), ds.clone());
        result = sub(&p.other_day, &result, &format!("the other day [= {ds}]"));
    }
    // 13j. "a couple days ago"
    if search(&p.couple_days, &lower) {
        let ds = day_str(shift(md, -2)?);
        resolved.insert("couple_days_ago".into(), ds.clone());
        result = sub(&p.couple_days, &result, &format!("a couple days ago [= {ds}]"));
    }
    // 13k. "day before yesterday"
    if search(&p.day_before, &lower) {
        let ds = day_str(shift(md, -2)?);
        resolved.insert("day_before_yesterday".into(), ds.clone());
        result = sub(&p.day_before, &result, &format!("day before yesterday [= {ds}]"));
    }
    // 13l. "next week"
    if search(&p.next_week, &lower) {
        let nw = shift(md, 7)?;
        let ws = shift(nw, -weekday(nw))?;
        let ds = format!("week of {}", day_str(ws));
        resolved.insert("next_week".into(), ds.clone());
        result = sub(&p.next_week, &result, &format!("next week [= {ds}]"));
    }
    // 13m. "over/during the weekend"
    if search(&p.over_weekend, &lower) {
        let mut since_sat = (weekday(md) + 2) % 7;
        if since_sat == 0 {
            since_sat = 7;
        }
        let sat = shift(md, -since_sat)?;
        let sun = shift(sat, 1)?;
        let ds = format!("{}-{} {} {}", sat.day(), sun.day(), month_title(sat.month()), sat.year());
        resolved.insert("over_weekend".into(), ds.clone());
        result = sub_with(&p.over_weekend, &result, false, |m| format!("{} the weekend [= {ds}]", &m[1]));
    }
    // 13n. "three..seven days ago" (words)
    for (word, n, pat) in &p.word_days {
        if search(pat, &lower) {
            let ds = day_str(shift(md, -n)?);
            resolved.insert(format!("{word}_days_ago"), ds.clone());
            result = sub(pat, &result, &format!("{word} days ago [= {ds}]"));
        }
    }
    // 13o. "a week ago"
    if search(&p.a_week_ago, &lower) {
        let ds = day_str(shift(md, -7)?);
        resolved.insert("a_week_ago".into(), ds.clone());
        result = sub(&p.a_week_ago, &result, &format!("a week ago [= {ds}]"));
    }
    // 14. "ten years ago", "X years ago"
    if let Ok(Some(c)) = p.years_ago.captures(lower.as_str()) {
        let num_str = c[1].to_string();
        let n: i128 = match NUMBER_WORDS.iter().find(|(w, _)| *w == num_str) {
            Some((_, v)) => *v as i128,
            None if num_str.bytes().all(|b| b.is_ascii_digit()) => num_str.parse().unwrap_or(0),
            None => 0,
        };
        if n != 0 {
            let target = md.year() as i128 - n;
            resolved.insert(format!("{n}_years_ago"), target.to_string());
            result = sub_with(&p.years_ago, &result, false, |m| format!("{} years ago [= {target}]", &m[1]));
        }
    }
    Ok((result, resolved))
}

static MORE_AGO_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|[0-9]{1,3})[\s\x1c-\x1f]+(week|month|year)s?[\s\x1c-\x1f]+ago\b")
        .unwrap()
});

/// Second pass of [`resolve_relative_dates`] for "N weeks/months/years ago" (digits, number words, "a"/"an"),
/// which that function only handles for a few fixed forms. Phrases already followed by `[=` are left alone, so
/// running it after the first pass never annotates twice. Weeks give "around 4 May 2023", months a month
/// ("February 2023"), years a year. Off by default (`ExtractionConfig::annotate_more_relative`).
pub fn resolve_more_relative_dates(content: &str, md: NaiveDateTime) -> String {
    let mut out = String::with_capacity(content.len() + 32);
    let mut last = 0;
    for c in MORE_AGO_RE.captures_iter(content) {
        let whole = c.get(0).expect("group 0");
        if content[whole.end()..]
            .trim_start_matches(|ch: char| ch.is_whitespace() || ('\x1c'..='\x1f').contains(&ch))
            .starts_with("[=")
        {
            continue;
        }
        let word = c[1].to_lowercase();
        let n = match word.as_str() {
            "a" | "an" | "one" => 1,
            w => NUMBER_WORDS.iter().find(|(x, _)| *x == w).map(|(_, v)| *v).or_else(|| w.parse().ok()).unwrap_or(0),
        };
        let unit = c[2].to_lowercase();
        let target = match unit.as_str() {
            "week" => shift(md, -7 * n).ok().map(|d| format!("around {}", day_str(d))),
            "month" => md
                .checked_sub_months(chrono::Months::new(n as u32))
                .map(|d| format!("{} {}", month_title(d.month()), d.year())),
            _ => md.checked_sub_months(chrono::Months::new(12 * n as u32)).map(|d| d.year().to_string()),
        };
        let Some(target) = target.filter(|_| n > 0) else { continue };
        out.push_str(&content[last..whole.end()]);
        out.push_str(&format!(" [= {target}]"));
        last = whole.end();
    }
    out.push_str(&content[last..]);
    out
}

// ------------------------------------------------------------------------------------------ query time cues

/// "first / earliest" or "most recent / latest" wording in a question.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OrderCue {
    Earliest,
    Latest,
}

/// What [`extract_query_time_cues`] found in a question: an inclusive date window and/or an ordering cue.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QueryTimeCues {
    /// inclusive `[start, end]` calendar days
    pub window: Option<(NaiveDate, NaiveDate)>,
    /// which rule produced the window (`"month"`, `"day"`, `"year"`, `"last week"`, `"3 months ago"`, ...)
    pub label: String,
    pub order: Option<OrderCue>,
}

const MONTH_ALT: &str = "january|february|march|april|may|june|july|august|september|october|november|december|\
                         jan|feb|apr|jun|jul|aug|sept|sep|oct|nov|dec";
const COUNT_ALT: &str = "[0-9]{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a couple of|\
                         a couple|couple of|a few|few";

static Q_ISO_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b((?:19|20)\d{2})[-/]([0-9]{1,2})[-/]([0-9]{1,2})\b").unwrap());
static Q_MONTH_DAY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"(?i)\b({MONTH_ALT})\.?\s+([0-9]{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+((?:19|20)\d{{2}})\b)?"))
        .unwrap()
});
static Q_DAY_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"(?i)\b([0-9]{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_ALT})\b\.?(?:,?\s+((?:19|20)\d{{2}})\b)?"
    ))
    .unwrap()
});
static Q_MONTH_YEAR_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"(?i)\b({MONTH_ALT})\.?,?\s+((?:19|20)\d{{2}})\b")).unwrap());
static Q_BARE_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"(?i)\b(in|during|of|for|early|late|mid|around|last|this)\s+(?:the\s+)?(?:month\s+of\s+)?({MONTH_ALT})\b"
    ))
    .unwrap()
});
static Q_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:in|during|of|throughout|for|year)\s+((?:19|20)\d{2})\b").unwrap()
});
static Q_AGO_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"(?i)\b({COUNT_ALT})[\s-]+(day|week|month|year)s?\s+ago\b")).unwrap());
static Q_PAST_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"(?i)\b(?:past|last)\s+({COUNT_ALT})\s+(day|week|month|year)s\b")).unwrap()
});
static Q_YESTERDAY_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\byesterday\b").unwrap());
static Q_LAST_WEEK_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\blast\s+week\b").unwrap());
static Q_THIS_WEEK_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bthis\s+week\b").unwrap());
static Q_LAST_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\blast\s+month\b").unwrap());
static Q_THIS_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bthis\s+month\b").unwrap());
static Q_LAST_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\blast\s+year\b").unwrap());
static Q_THIS_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bthis\s+year\b").unwrap());
static Q_LATEST_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:most\s+recent(?:ly)?|latest|newest|last\s+time)\b").unwrap());
static Q_EARLIEST_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(?:first|earliest|initially)\b").unwrap());

fn query_month(name: &str, original: &str) -> Option<u32> {
    let n = name.to_lowercase();
    // "may" is also a modal verb: only the capitalised word counts
    if n == "may" && original != "May" {
        return None;
    }
    let full = MONTH_NAMES.iter().position(|m| *m == n || (n.len() <= 4 && n != "may" && m.starts_with(n.as_str())));
    full.map(|i| i as u32 + 1)
}

fn count_word(w: &str) -> Option<i64> {
    let w = w.to_lowercase();
    match w.as_str() {
        "a" | "an" => Some(1),
        "a couple" | "a couple of" | "couple of" => Some(2),
        "a few" | "few" => Some(3),
        _ => NUMBER_WORDS.iter().find(|(x, _)| *x == w).map(|(_, v)| *v).or_else(|| w.parse().ok()),
    }
}

fn month_span(y: i32, m: u32) -> Option<(NaiveDate, NaiveDate)> {
    let first = NaiveDate::from_ymd_opt(y, m, 1)?;
    let next = if m == 12 { NaiveDate::from_ymd_opt(y + 1, 1, 1)? } else { NaiveDate::from_ymd_opt(y, m + 1, 1)? };
    Some((first, next.pred_opt()?))
}

fn year_span(y: i32) -> Option<(NaiveDate, NaiveDate)> {
    Some((NaiveDate::from_ymd_opt(y, 1, 1)?, NaiveDate::from_ymd_opt(y, 12, 31)?))
}

fn days_around(center: NaiveDate, radius: i64) -> Option<(NaiveDate, NaiveDate)> {
    let r = TimeDelta::try_days(radius)?;
    Some((center.checked_sub_signed(r)?, center.checked_add_signed(r)?))
}

/// A month named without a year: its most recent occurrence not after `now` (strictly before the current month
/// when `strictly_past`, i.e. "last March").
fn month_without_year(now: NaiveDate, m: u32, strictly_past: bool) -> Option<(NaiveDate, NaiveDate)> {
    let before = if strictly_past { m >= now.month() } else { m > now.month() };
    month_span(if before { now.year() - 1 } else { now.year() }, m)
}

/// A month/day without a year: the most recent occurrence not after `now`.
fn day_without_year(now: NaiveDate, m: u32, d: u32) -> Option<NaiveDate> {
    let this = NaiveDate::from_ymd_opt(now.year(), m, d)?;
    if this > now {
        NaiveDate::from_ymd_opt(now.year() - 1, m, d)
    } else {
        Some(this)
    }
}

fn parse_year(s: Option<regex::Match<'_>>) -> Option<i32> {
    s.and_then(|m| m.as_str().parse().ok())
}

fn window_cue(q: &str, now: NaiveDate) -> Option<(String, (NaiveDate, NaiveDate))> {
    // 1. numeric date
    if let Some(c) = Q_ISO_RE.captures(q) {
        let (y, m, d) = (c[1].parse().ok()?, c[2].parse().ok()?, c[3].parse().ok()?);
        if let Some(day) = NaiveDate::from_ymd_opt(y, m, d) {
            return Some(("day".into(), (day, day)));
        }
    }
    // 2. "April 10th", "March 3, 2023", "10th of April"
    for (re, month_first) in [(&*Q_MONTH_DAY_RE, true), (&*Q_DAY_MONTH_RE, false)] {
        for c in re.captures_iter(q) {
            let (mi, di) = if month_first { (1, 2) } else { (2, 1) };
            let Some(month) = query_month(&c[mi], &c[mi]) else { continue };
            let Ok(d) = c[di].parse::<u32>() else { continue };
            let day = match parse_year(c.get(3)) {
                Some(y) => NaiveDate::from_ymd_opt(y, month, d),
                None => day_without_year(now, month, d),
            };
            if let Some(day) = day {
                return Some(("day".into(), (day, day)));
            }
        }
    }
    // 3. "March 2023"
    for c in Q_MONTH_YEAR_RE.captures_iter(q) {
        if let (Some(month), Ok(y)) = (query_month(&c[1], &c[1]), c[2].parse::<i32>()) {
            if let Some(w) = month_span(y, month) {
                return Some(("month".into(), w));
            }
        }
    }
    // 4. "in March"
    for c in Q_BARE_MONTH_RE.captures_iter(q) {
        if let Some(month) = query_month(&c[2], &c[2]) {
            if let Some(w) = month_without_year(now, month, c[1].eq_ignore_ascii_case("last")) {
                return Some(("month".into(), w));
            }
        }
    }
    // 5. "in 2023"
    if let Some(c) = Q_YEAR_RE.captures(q) {
        if let Some(w) = c[1].parse().ok().and_then(year_span) {
            return Some(("year".into(), w));
        }
    }
    // 6. relative to `now`
    if Q_YESTERDAY_RE.is_match(q) {
        let d = now.pred_opt()?;
        return Some(("yesterday".into(), (d, d)));
    }
    if let Some(c) = Q_AGO_RE.captures(q) {
        if let Some(n) = count_word(&c[1]).filter(|n| *n > 0) {
            let unit = c[2].to_lowercase();
            let label = format!("{n} {unit}s ago");
            let w = match unit.as_str() {
                "day" => days_around(now.checked_sub_signed(TimeDelta::try_days(n)?)?, 1),
                "week" => days_around(now.checked_sub_signed(TimeDelta::try_days(7 * n)?)?, 3),
                "month" => days_around(now.checked_sub_months(chrono::Months::new(u32::try_from(n).ok()?))?, 15),
                _ => year_span(now.checked_sub_months(chrono::Months::new(u32::try_from(12 * n).ok()?))?.year()),
            };
            if let Some(w) = w {
                return Some((label, w));
            }
        }
    }
    if let Some(c) = Q_PAST_RE.captures(q) {
        if let Some(n) = count_word(&c[1]).filter(|n| *n > 0) {
            let unit = c[2].to_lowercase();
            let start = match unit.as_str() {
                "day" => now.checked_sub_signed(TimeDelta::try_days(n)?),
                "week" => now.checked_sub_signed(TimeDelta::try_days(7 * n)?),
                "month" => now.checked_sub_months(chrono::Months::new(u32::try_from(n).ok()?)),
                _ => now.checked_sub_months(chrono::Months::new(u32::try_from(12 * n).ok()?)),
            };
            if let Some(start) = start {
                return Some((format!("past {n} {unit}s"), (start, now)));
            }
        }
    }
    let monday = now.checked_sub_signed(TimeDelta::try_days(now.weekday().num_days_from_monday() as i64)?)?;
    if Q_LAST_WEEK_RE.is_match(q) && !q.to_lowercase().contains("last weekend") {
        let start = monday.checked_sub_signed(TimeDelta::try_days(7)?)?;
        return Some(("last week".into(), (start, start.checked_add_signed(TimeDelta::try_days(6)?)?)));
    }
    if Q_THIS_WEEK_RE.is_match(q) {
        return Some(("this week".into(), (monday, now)));
    }
    if Q_LAST_MONTH_RE.is_match(q) {
        let prev = now.with_day(1)?.pred_opt()?;
        return Some(("last month".into(), month_span(prev.year(), prev.month())?));
    }
    if Q_THIS_MONTH_RE.is_match(q) {
        return Some(("this month".into(), (now.with_day(1)?, now)));
    }
    if Q_LAST_YEAR_RE.is_match(q) {
        return Some(("last year".into(), year_span(now.year() - 1)?));
    }
    if Q_THIS_YEAR_RE.is_match(q) {
        return Some(("this year".into(), year_span(now.year())?));
    }
    None
}

/// Rule-based time cues of a question (no model): an inclusive date window from explicit dates, months, years and
/// relative expressions resolved against `now`, and an ordering cue ("first/earliest" vs "most recent/latest").
///
/// Explicit forms win over relative ones: ISO date, "April 10th", "10th of April", "March 2023", "in March" (its
/// most recent occurrence not after `now`; "May" must be capitalised), "in 2023", then "yesterday", "N
/// days/weeks/months/years ago" (+-1 day, +-3 days, +-15 days, whole year), "past/last N units", "last/this
/// week" (calendar weeks, Monday first), "last/this month", "last/this year". When both an earliest and a latest
/// word occur the ordering cue is dropped.
pub fn extract_query_time_cues(query: &str, now: NaiveDate) -> QueryTimeCues {
    let (label, window) = match window_cue(query, now) {
        Some((label, w)) => (label, Some(w)),
        None => (String::new(), None),
    };
    let late = Q_LATEST_RE.is_match(query);
    let early = Q_EARLIEST_RE.is_match(query);
    let order = match (early, late) {
        (true, false) => Some(OrderCue::Earliest),
        (false, true) => Some(OrderCue::Latest),
        _ => None,
    };
    QueryTimeCues { window, label, order }
}

#[derive(Clone, Copy)]
enum Direction {
    Back,
    This,
    Next,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M").unwrap()
    }

    #[test]
    fn yesterday_annotation() {
        let (text, vals) = resolve_relative_dates("I went there yesterday", Some(dt("2023-05-08 10:00"))).unwrap();
        assert_eq!(text, "I went there yesterday [= 7 May 2023]");
        assert_eq!(vals["yesterday"], "7 May 2023");
    }

    #[test]
    fn earlier_today_is_not_annotated_twice() {
        let (text, _) = resolve_relative_dates("earlier today I ran", Some(dt("2023-05-08 10:00"))).unwrap();
        assert_eq!(text, "earlier today [= 8 May 2023] I ran");
    }

    #[test]
    fn more_relative_dates() {
        let md = dt("2023-05-25 10:00");
        let f = |t: &str| resolve_more_relative_dates(t, md);
        assert_eq!(f("started three months ago"), "started three months ago [= February 2023]");
        assert_eq!(
            f("a month ago and 3 weeks ago"),
            "a month ago [= April 2023] and 3 weeks ago [= around 4 May 2023]"
        );
        assert_eq!(f("about a year ago"), "about a year ago [= 2022]");
        assert_eq!(f("5 Years ago"), "5 Years ago [= 2018]");
        // the first pass already annotated these: no second annotation
        let (first, _) = resolve_relative_dates("2 weeks ago and a week ago", Some(md)).unwrap();
        assert_eq!(f(&first), first);
        assert_eq!(f("no relative time"), "no relative time");
    }

    #[test]
    fn flexible_formats() {
        let p = |s| parse_datetime_flexible(Some(s)).unwrap().unwrap().naive;
        assert_eq!(p("1:56 pm on 8 May, 2023"), dt("2023-05-08 13:56"));
        assert_eq!(p("8 May 2023"), dt("2023-05-08 00:00"));
        assert_eq!(p("May 8, 2023"), dt("2023-05-08 00:00"));
        assert!(parse_datetime_flexible(Some("31 February 2023")).is_err());
    }
}
