//! Port of `NeuralGraph/temporal_utils.py`: date tokens, explicit/duration date extraction, temporal query
//! expansion and the marker-table query-mode router the research harnesses and Tesseract use.
//!
//! `parse_datetime_flexible` and `resolve_relative_dates` were already ported (and pinned) by the product crate;
//! they are re-exported from `neuralgraph_memory::temporal` rather than duplicated. Message dates are naive
//! wall-clock datetimes (`chrono::NaiveDateTime`): every function here only reads the calendar fields, so a
//! Python aware datetime behaves like its wall time.
//!
//! Where the Python raised (an invalid calendar date such as "31 June" in `extract_explicit_date`, a date shifted
//! out of range) the Rust function returns [`TemporalError`], the error the product port already uses for the
//! same situations; callers that had `try/except` around the call treat it the same way.
//!
//! Regex notes: Python's `\s` also matches U+001C..U+001F, written `[\s\x1c-\x1f]` here; `int()` of the digit
//! groups accepts any Unicode decimal digit, as `py_int_parse` does.

use chrono::{Datelike, NaiveDateTime, TimeDelta};
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{decimal_value, py_int_parse, py_strip};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::LazyLock;

pub use neuralgraph_memory::temporal::{
    month_number, parse_datetime_flexible, resolve_relative_dates, FlexibleDateTime, TemporalError, DAY_NAMES,
    MONTH_NAMES,
};

type TResult<T> = Result<T, TemporalError>;

/// `MONTH_NAMES_REV[month]`: 1..12 -> lowercase month name (`None` outside 1..12, like `dict.get`).
pub fn month_names_rev(month: u32) -> Option<&'static str> {
    MONTH_NAMES.get((month as usize).wrapping_sub(1)).copied()
}

/// `DAY_NAMES_REV`: the upper-case day names, Monday first.
pub const DAY_NAMES_REV: [&str; 7] = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];

/// `DAY_ABBREVIATIONS`: weekday index (0 = Monday) per abbreviation, in dict order. Only
/// `resolve_relative_dates` (product crate) reads it; it is exported here because the Python module exported it.
#[rustfmt::skip]
pub const DAY_ABBREVIATIONS: [(&str, u32); 18] = [
    ("mon", 0), ("mo", 0), ("tue", 1), ("tues", 1), ("tu", 1), ("wed", 2), ("weds", 2), ("we", 2), ("thu", 3),
    ("thur", 3), ("thurs", 3), ("th", 3), ("fri", 4), ("fr", 4), ("sat", 5), ("sa", 5), ("sun", 6), ("su", 6),
];

/// `NUMBER_WORDS`.
#[rustfmt::skip]
pub const NUMBER_WORDS: [(&str, i64); 12] = [
    ("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5), ("six", 6), ("seven", 7), ("eight", 8),
    ("nine", 9), ("ten", 10), ("eleven", 11), ("twelve", 12),
];

fn number_word(w: &str) -> Option<i64> {
    NUMBER_WORDS.iter().find(|(k, _)| *k == w).map(|(_, v)| *v)
}

/// `str.isdigit()` for the tokens these regexes capture (Unicode decimal digits). Python's `isdigit` also accepts
/// superscript digits, for which the following `int()` raised; those are treated as "not a number" here.
fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| decimal_value(c).is_some())
}

/// `_parse_number_token`: a numeral or a number word (one..twelve).
pub fn parse_number_token(token: &str) -> Option<i64> {
    if token.is_empty() {
        return None;
    }
    let t = token.to_lowercase();
    let t = py_strip(&t);
    if is_digits(t) {
        return py_int_parse(t);
    }
    number_word(t)
}

fn ymd_string(d: NaiveDateTime) -> String {
    format!("{:04}-{:02}-{:02}", d.year(), d.month(), d.day())
}

/// `datetime(y, m, d).strftime("%Y-%m-%d")`, `Err` where the constructor raised `ValueError`.
fn checked_ymd(y: i64, m: i64, d: i64) -> TResult<String> {
    if !(1..=9999).contains(&y) {
        return Err(TemporalError(format!("year {y} is out of range")));
    }
    if !(1..=12).contains(&m) {
        return Err(TemporalError("month must be in 1..12".into()));
    }
    match u32::try_from(d).ok().and_then(|d| chrono::NaiveDate::from_ymd_opt(y as i32, m as u32, d)) {
        Some(date) => Ok(format!("{:04}-{:02}-{:02}", date.year(), date.month(), date.day())),
        None => Err(TemporalError("day is out of range for month".into())),
    }
}

/// `message_date + timedelta(days=days)` with Python's `OverflowError` as `Err`.
fn shift_days(d: NaiveDateTime, days: i64) -> TResult<NaiveDateTime> {
    let out = TimeDelta::try_days(days)
        .and_then(|delta| d.checked_add_signed(delta))
        .ok_or_else(|| TemporalError("date value out of range".into()))?;
    if !(1..=9999).contains(&out.year()) {
        return Err(TemporalError("date value out of range".into()));
    }
    Ok(out)
}

fn re(p: &str) -> Regex {
    Regex::new(p).unwrap_or_else(|e| panic!("bad regex {p}: {e}"))
}

static ISO_IN_TEXT: LazyLock<Regex> = LazyLock::new(|| re(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b"));
static DAY_MONTH_YEAR: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(\d{1,2})[\s\x1c-\x1f]+([A-Za-z]+)[\s\x1c-\x1f]*(\d{4})?\b"));
static MONTH_DAY_YEAR: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b([A-Za-z]+)[\s\x1c-\x1f]+(\d{1,2})(?:,?[\s\x1c-\x1f]*(\d{4}))?\b"));

/// `extract_explicit_date`: the first explicit calendar date in `content` as `YYYY-MM-DD`.
///
/// Each pattern is tried once at its first match (Python's `re.search`); a match whose word is not a month
/// moves on to the next pattern. An invalid ISO-like date is skipped (Python caught that `ValueError`), but an
/// invalid "31 June" / "June 31" raised in Python and is an `Err` here.
pub fn extract_explicit_date(content: &str, message_date: Option<NaiveDateTime>) -> TResult<Option<String>> {
    if content.is_empty() {
        return Ok(None);
    }
    if let Some(c) = ISO_IN_TEXT.captures(content) {
        let parts = (py_int_parse(&c[1]), py_int_parse(&c[2]), py_int_parse(&c[3]));
        if let (Some(y), Some(m), Some(d)) = parts {
            if let Ok(s) = checked_ymd(y, m, d) {
                return Ok(Some(s));
            }
        }
    }
    if let Some(c) = DAY_MONTH_YEAR.captures(content) {
        if let Some(month) = month_number(&c[2].to_lowercase()) {
            let year_val = match c.get(3) {
                Some(y) => py_int_parse(y.as_str()),
                None => message_date.map(|d| d.year() as i64),
            };
            if let Some(y) = year_val.filter(|y| *y != 0) {
                let day = py_int_parse(&c[1]).unwrap_or(0);
                return checked_ymd(y, month as i64, day).map(Some);
            }
        }
    }
    if let Some(c) = MONTH_DAY_YEAR.captures(content) {
        if let Some(month) = month_number(&c[1].to_lowercase()) {
            let year_val = match c.get(3) {
                Some(y) => py_int_parse(y.as_str()),
                None => message_date.map(|d| d.year() as i64),
            };
            if let Some(y) = year_val.filter(|y| *y != 0) {
                let day = py_int_parse(&c[2]).unwrap_or(0);
                return checked_ymd(y, month as i64, day).map(Some);
            }
        }
    }
    Ok(None)
}

static SINCE_YEAR: LazyLock<Regex> = LazyLock::new(|| re(r"\bsince[\s\x1c-\x1f]+(20\d{2})\b"));
static SINCE_MONTH_YEAR: LazyLock<Regex> =
    LazyLock::new(|| re(r"\bsince[\s\x1c-\x1f]+([A-Za-z]+)[\s\x1c-\x1f]+(20\d{2})\b"));
static FOR_YEARS: LazyLock<Regex> = LazyLock::new(|| re(r"\bfor[\s\x1c-\x1f]+(\w+|\d+)[\s\x1c-\x1f]+years?\b"));
static YEARS_NOW: LazyLock<Regex> = LazyLock::new(|| re(r"\b(\w+|\d+)[\s\x1c-\x1f]+years?[\s\x1c-\x1f]+now\b"));
static FOR_MONTHS: LazyLock<Regex> = LazyLock::new(|| re(r"\bfor[\s\x1c-\x1f]+(\w+|\d+)[\s\x1c-\x1f]+months?\b"));

/// `extract_duration_metadata`: "since 2016", "since June 2020", "for seven years", "for 3 months".
/// Keys appear in the order Python's dict acquired them (a later assignment keeps the key's first position).
pub fn extract_duration_metadata(content: &str, message_date: Option<NaiveDateTime>) -> Map<String, Value> {
    let mut meta = Map::new();
    if content.is_empty() {
        return meta;
    }
    let lower = content.to_lowercase();
    if let Some(c) = SINCE_YEAR.captures(&lower) {
        if let Some(y) = py_int_parse(&c[1]) {
            meta.insert("since_year".into(), json!(y));
        }
    }
    if let Some(c) = SINCE_MONTH_YEAR.captures(&lower) {
        if let (Some(month), Some(y)) = (month_number(&c[1].to_lowercase()), py_int_parse(&c[2])) {
            meta.insert("since_date".into(), json!(format!("{y:04}-{month:02}-01")));
            meta.insert("since_year".into(), json!(y));
        }
    }
    let years = FOR_YEARS.captures(&lower).or_else(|| YEARS_NOW.captures(&lower));
    if let Some(c) = years {
        if let Some(n) = parse_number_token(&c[1]).filter(|n| *n != 0) {
            meta.insert("duration_years".into(), json!(n));
            if let Some(d) = message_date {
                meta.insert("since_year".into(), json!(d.year() as i64 - n));
            }
        }
    }
    if let Some(c) = FOR_MONTHS.captures(&lower) {
        if let Some(n) = parse_number_token(&c[1]).filter(|n| *n != 0) {
            meta.insert("duration_months".into(), json!(n));
        }
    }
    meta
}

/// Result of [`generate_temporal_tokens`] (`{"date_tokens": [...], "temporal_metadata": {...}}`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct TemporalTokens {
    pub date_tokens: Vec<String>,
    pub temporal_metadata: Map<String, Value>,
}

impl TemporalTokens {
    pub fn to_dict(&self) -> Value {
        json!({"date_tokens": self.date_tokens, "temporal_metadata": self.temporal_metadata})
    }
}

/// `generate_temporal_tokens`: BM25 date tokens (`DATE_2023-05-08`, `YEAR_2023`, `MONTH_MAY`, ...) plus the
/// resolved relative dates mentioned in `content` (`REL_YESTERDAY_RESOLVED_2023-05-07`).
pub fn generate_temporal_tokens(message_date: Option<NaiveDateTime>, content: &str) -> TResult<TemporalTokens> {
    let Some(md) = message_date else { return Ok(TemporalTokens::default()) };
    let mut tokens = Vec::new();
    let mut metadata = Map::new();

    let date_iso = ymd_string(md);
    tokens.push(format!("DATE_{date_iso}"));
    metadata.insert("date_iso".into(), json!(date_iso));

    let year = md.year();
    tokens.push(format!("YEAR_{year}"));
    metadata.insert("year".into(), json!(year));

    let month = md.month();
    let month_name = month_names_rev(month).unwrap_or("").to_uppercase();
    tokens.push(format!("MONTH_{month:02}"));
    tokens.push(format!("MONTH_{month_name}"));
    metadata.insert("month".into(), json!(month));
    metadata.insert("month_name".into(), json!(month_name));

    let day = md.day();
    tokens.push(format!("DAY_{day:02}"));
    metadata.insert("day".into(), json!(day));

    let dow_name = DAY_NAMES_REV[md.weekday().num_days_from_monday() as usize];
    tokens.push(format!("DOW_{dow_name}"));
    metadata.insert("day_of_week".into(), json!(dow_name));

    let week_num = md.iso_week().week();
    tokens.push(format!("WEEK_{week_num:02}"));
    metadata.insert("week_number".into(), json!(week_num));

    let quarter = (month - 1) / 3 + 1;
    tokens.push(format!("Q{quarter}_{year}"));
    metadata.insert("quarter".into(), json!(format!("Q{quarter}")));

    let season = match month {
        12 | 1 | 2 => "WINTER",
        3..=5 => "SPRING",
        6..=8 => "SUMMER",
        _ => "FALL",
    };
    tokens.push(format!("SEASON_{season}"));
    metadata.insert("season".into(), json!(season));

    for (rel_type, resolved) in extract_relative_dates_for_tokens(content, md)? {
        tokens.push(format!("REL_{rel_type}_RESOLVED_{resolved}"));
    }
    Ok(TemporalTokens { date_tokens: tokens, temporal_metadata: metadata })
}

static DAYS_AGO_FOR_TOKENS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(\d+|two|three|four|five|six|seven)[\s\x1c-\x1f]+days?[\s\x1c-\x1f]+ago"));

/// `_extract_relative_dates_for_tokens`: `(REL type, resolved date)` pairs for the date tokens.
pub fn extract_relative_dates_for_tokens(content: &str, message_date: NaiveDateTime) -> TResult<Vec<(String, String)>> {
    let mut results = Vec::new();
    let lower = content.to_lowercase();
    if lower.contains("yesterday") {
        results.push(("YESTERDAY".into(), ymd_string(shift_days(message_date, -1)?)));
    }
    if lower.contains("last week") {
        results.push(("LAST_WEEK".into(), ymd_string(shift_days(message_date, -7)?)));
    }
    if lower.contains("last month") {
        let (m, y) = if message_date.month() == 1 {
            (12, message_date.year() - 1)
        } else {
            (message_date.month() - 1, message_date.year())
        };
        results.push(("LAST_MONTH".into(), format!("{y}-{m:02}")));
    }
    if lower.contains("weekend") {
        let since_saturday = (message_date.weekday().num_days_from_monday() as i64 + 2) % 7;
        results.push(("WEEKEND".into(), ymd_string(shift_days(message_date, -since_saturday)?)));
    }
    if let Some(c) = DAYS_AGO_FOR_TOKENS.captures(&lower) {
        let s = &c[1];
        let num = match number_word(s) {
            Some(n) => n,
            None if is_digits(s) => {
                py_int_parse(s).ok_or_else(|| TemporalError("Python int too large to convert to C int".into()))?
            }
            None => 0,
        };
        if num > 0 {
            results.push((format!("{num}_DAYS_AGO"), ymd_string(shift_days(message_date, -num)?)));
        }
    }
    Ok(results)
}

static YEAR_IN_QUERY: LazyLock<Regex> = LazyLock::new(|| re(r"\b(20\d{2})\b"));

/// `expand_temporal_query`: append `YEAR_/MONTH_/DOW_/SEASON_/REL_` tokens for the dates a query mentions
/// (plain substring tests, so "may" in "maybe" adds `MONTH_MAY`, as in Python).
pub fn expand_temporal_query(query: &str) -> String {
    let lower = query.to_lowercase();
    let mut tokens: Vec<String> = Vec::new();
    for c in YEAR_IN_QUERY.captures_iter(query) {
        tokens.push(format!("YEAR_{}", &c[1]));
    }
    for m in MONTH_NAMES {
        if lower.contains(m) {
            tokens.push(format!("MONTH_{}", m.to_uppercase()));
        }
    }
    for d in DAY_NAMES {
        if lower.contains(d) {
            tokens.push(format!("DOW_{}", d.to_uppercase()));
        }
    }
    for (season, token) in
        [("spring", "SPRING"), ("summer", "SUMMER"), ("fall", "FALL"), ("autumn", "FALL"), ("winter", "WINTER")]
    {
        if lower.contains(season) {
            tokens.push(format!("SEASON_{token}"));
        }
    }
    if lower.contains("yesterday") {
        tokens.push("REL_YESTERDAY".into());
    }
    if lower.contains("last week") {
        tokens.push("REL_LAST_WEEK".into());
    }
    if lower.contains("last month") {
        tokens.push("REL_LAST_MONTH".into());
    }
    if tokens.is_empty() {
        query.to_string()
    } else {
        format!("{query} {}", tokens.join(" "))
    }
}

// =============================================================================================================
// QUERY_MODE_MARKERS
// =============================================================================================================

/// One marker category: `(name, patterns)`. The Python values were sets (deduplicated, unordered); matching only
/// asks whether any pattern matches, so the order inside a category never changed a result.
pub type MarkerCategory = (&'static str, &'static [&'static str]);

/// `QUERY_MODE_MARKERS`: mode -> categories, in dict order. Patterns are plain substrings / prefixes (entries
/// such as `"how does .* feel"` were never regexes; they only match that literal text).
#[rustfmt::skip]
pub const QUERY_MODE_MARKERS: &[(&str, &[MarkerCategory])] = &[
    ("TEMPORAL", &[
        ("when_starters", &[
            "when ", "when's ", "when did ", "when does ", "when was ", "when were ",
            "when will ", "when is ", "when are ", "when has ", "when have ",
            "when had ", "when would ", "when could ", "when should ",
        ]),
        ("date_time_queries", &[
            "what date", "what day", "what year", "what month", "what week",
            "what time", "what hour", "what season", "what period", "what decade",
            "what century", "what era", "what quarter", "what semester",
            "on what day", "in what year", "in what month", "during what",
            "at what time", "on which day", "in which year", "in which month",
            "at which time", "on what date", "in which season", "during which",
            "what morning", "what afternoon", "what evening", "what night",
            "what day of the week", "what time of day", "what part of",
        ]),
        ("duration", &[
            "how long ", "for how long", "how much time", "how long ago",
            "how long has", "how long have", "how long did", "how long was", "how long were",
            "how long will", "how long does", "how long would", "how long should",
            "how many days", "how many weeks", "how many months", "how many years",
            "how many hours", "how many minutes", "how many seconds", "how many decades",
            "how many semesters", "how many quarters", "how many seasons",
            "length of time", "amount of time", "period of time", "span of",
            "duration of", "time frame", "time period", "time span",
        ]),
        ("sequence_order", &[
            "after what", "before what", "since when", "until when", "by when",
            "happened before", "happened after", "came before", "came after",
            "occurred before", "occurred after", "took place before", "took place after",
            "how soon", "how recently", "how early", "how late",
            "right before", "right after", "just before", "just after",
            "immediately before", "immediately after", "shortly before", "shortly after",
            "prior to", "subsequent to", "following the", "preceding the",
            "in the wake of", "leading up to", "in advance of", "ahead of",
            "in preparation for", "in anticipation of",
            "first time", "last time", "most recent", "earliest", "latest",
            "initial", "final", "beginning", "end",
        ]),
        ("relative_time", &[
            "last week", "this week", "next week", "past week", "coming week",
            "previous week", "following week", "week before", "week after",
            "last month", "this month", "next month", "past month", "coming month",
            "previous month", "following month", "month before", "month after",
            "last year", "this year", "next year", "past year", "coming year",
            "previous year", "following year", "year before", "year after",
            "yesterday", "today", "tomorrow", "day before yesterday", "day after tomorrow",
            "last night", "tonight", "last evening", "this morning", "this afternoon",
            "this evening", "earlier today", "later today",
            "ago did", "ago was", "ago were", "ago has", "ago have", "ago had",
            "years ago", "months ago", "weeks ago", "days ago", "hours ago",
            "minutes ago", "seconds ago", "moments ago", "recently",
            "the other day", "the other night", "a while ago", "a while back",
            "not long ago", "long ago", "some time ago", "sometime ago",
            "once upon", "back when", "back in", "used to",
        ]),
        ("elapsed_time", &[
            "time passed", "time elapsed", "time since", "time until", "time before",
            "passed between", "weeks passed", "days passed", "months passed", "years passed",
            "hours passed", "minutes passed", "elapsed since", "elapsed between",
            "gone by", "went by", "time gone", "time went",
        ]),
        ("schedule_deadline", &[
            "by when", "due when", "deadline", "scheduled for", "planning to",
            "scheduled to", "set to", "slated for", "arranged for", "booked for",
            "going to", "intending to", "planning on", "expecting to",
            "supposed to", "meant to", "about to", "preparing to",
            "before the", "after the", "during the", "throughout the",
            "for the duration", "until the", "up until", "as of",
        ]),
        ("age_milestone", &[
            "how old", "what age", "age of", "age when", "turned",
            "anniversary", "birthday", "celebration of",
            "for how many", "lasted how long", "been going", "been happening",
            "been in", "been at", "been with", "been doing",
            "milestone", "landmark", "turning point", "major event",
        ]),
        ("frequency", &[
            "how often", "how frequently", "how regularly", "how many times",
            "how much", "frequency of", "rate of",
            "every time", "each time", "whenever", "always", "usually",
            "sometimes", "occasionally", "rarely", "never", "once", "twice",
            "multiple times", "several times", "many times", "few times",
            "daily", "weekly", "monthly", "yearly", "annually",
            "hourly", "nightly", "seasonal", "periodic", "regular",
        ]),
        ("temporal_context", &[
            "in spring", "in summer", "in fall", "in autumn", "in winter",
            "during spring", "during summer", "during fall", "during winter",
            "in the morning", "in the afternoon", "in the evening", "at night",
            "at dawn", "at dusk", "at noon", "at midnight",
            "as a child", "as a teen", "as an adult", "growing up",
            "in childhood", "in youth", "in adulthood",
        ]),
    ]),
    ("AGGREGATION", &[
        ("activities_hobbies", &[
            "what activities", "what hobbies", "what things", "what items",
            "what tasks", "what chores", "what duties", "what responsibilities",
            "what does", "what do", "what did", "what has", "what have",
            "what would", "what will", "what can", "what could",
            "what sports", "what exercises", "what workouts", "what training",
            "what physical activities", "what fitness",
            "what art", "what crafts", "what projects", "what creations",
            "what creative", "what artistic",
            "what recreational", "what leisure", "what pastimes", "what fun",
            " like", " likes", " enjoy", " enjoys", " love", " loves",
            " prefer", " prefers", " hate", " hates", " do", " does",
            " participate in", " partake in", " engage in", " involved in",
        ]),
        ("locations_places", &[
            "where has", "where have", "where did", "where does", "where was", "where were",
            "where will", "where can", "where would", "where could",
            "what places", "which places", "what locations", "what destinations",
            "what cities", "what countries", "what regions", "what areas",
            "what towns", "what villages", "what neighborhoods", "what districts",
            "what states", "what provinces", "what territories",
            "what restaurants", "what cafes", "what bars", "what clubs",
            "what stores", "what shops", "what malls", "what markets",
            "what museums", "what galleries", "what theaters", "what venues",
            "what parks", "what beaches", "what mountains", "what trails",
            "visited", "traveled to", "been to", "gone to", "went to",
        ]),
        ("media_content", &[
            "what books", "what novels", "what stories", "what literature",
            "what magazines", "what newspapers", "what articles", "what publications",
            "what movies", "what films", "what shows", "what series",
            "what tv shows", "what television", "what documentaries", "what videos",
            "what channels", "what streaming", "what youtube",
            "what songs", "what music", "what albums", "what tracks",
            "what podcasts", "what audiobooks", "what playlists",
            "what artists", "what bands", "what musicians", "what singers",
            "what games", "what video games", "what board games", "what card games",
            "what apps", "what programs", "what software", "what websites",
            "what platforms", "what tools", "what services",
            "what plays", "what musicals", "what performances", "what concerts",
            "what operas", "what ballets",
            "read", "reads", "watched", "watches", "listened", "listens",
            "viewed", "views", "seen", "saw", "played", "plays",
            "streamed", "streams", "downloaded", "downloads",
        ]),
        ("events_occasions", &[
            "what events", "what occasions", "what ceremonies", "what celebrations",
            "what meetings", "what appointments", "what gatherings",
            " events has ", " events have ", " events did ", " events does ",
            "what festivals", "what conferences", "what competitions", "what tournaments",
            "what parties", "what weddings", "what reunions", "what get-togethers",
            "what dinners", "what lunches", "what brunches",
            "what seminars", "what workshops", "what training", "what courses",
            "what lectures", "what presentations", "what talks",
            "what concerts", "what shows", "what performances", "what exhibitions",
            "what fairs", "what expos", "what conventions",
            "what rallies", "what protests", "what demonstrations", "what marches",
            "what fundraisers", "what charity events", "what volunteer",
            "participated in", "attended", "went to", "joined", "took part in",
        ]),
        ("types_kinds", &[
            "what types", "what kinds", "what sorts", "what categories",
            "what varieties", "what forms", "what styles", "what versions",
            "what kind of", "what sort of", "what type of",
            "what brands", "what models", "what makes", "what manufacturers",
            "what flavors", "what variants", "what options", "what choices",
            "what genres", "what styles", "what formats", "what methods",
        ]),
        ("attributes_features", &[
            "what symbols", "which symbols", "what signs", "which signs",
            "what icons", "what emblems", "what badges", "what logos",
            "what traits", "what characteristics", "what qualities", "what features",
            "what properties", "what attributes", "what aspects",
            "what colors", "what sizes", "what shapes", "what patterns",
            "what designs", "what textures", "what materials",
            "what features does", "what functions", "what capabilities",
            "what specifications", "what details",
        ]),
        ("possessions_ownership", &[
            "what pets", "what animals", "what dogs", "what cats",
            "what birds", "what fish", "what livestock",
            "what items", "what things", "what stuff", "what objects",
            "what possessions", "what belongings",
            "what instruments", "what tools", "what equipment", "what devices",
            "what machines", "what appliances", "what gadgets",
            "what cars", "what vehicles", "what bikes", "what motorcycles",
            "what boats", "what planes",
            "what computers", "what phones", "what tablets", "what electronics",
            "what clothes", "what outfits", "what shoes", "what accessories",
            "what jewelry", "what watches",
            "what collection", "what collectibles", "what memorabilia",
            "what languages", "what skills", "what talents", "what abilities",
            "what certifications", "what degrees", "what qualifications",
            "has", "have", "owns", "own", "possesses", "possess",
            "carries", "carry", "holds", "hold",
        ]),
        ("food_cooking", &[
            "what foods", "what dishes", "what meals", "what recipes",
            "what cuisines", "what ingredients", "what spices",
            "what drinks", "what beverages", "what cocktails",
            "what desserts", "what snacks", "what appetizers",
            "cooked", "cooks", "prepared", "prepares", "made", "makes",
            "ate", "eats", "drank", "drinks", "tasted", "tastes",
        ]),
        ("shopping_purchases", &[
            "what bought", "what purchased", "what acquired", "what got",
            "what products", "what items purchased", "what goods",
            "bought", "buys", "purchased", "purchases", "acquired", "got",
            "shopping for", "looking for", "searching for",
        ]),
        ("achievements", &[
            "what achievements", "what accomplishments", "what awards", "what prizes",
            "what honors", "what recognition", "what medals", "what trophies",
            "what milestones", "what successes", "what wins", "what victories",
            "achieved", "accomplished", "won", "earned", "received",
        ]),
        ("problems_challenges", &[
            "what problems", "what issues", "what challenges", "what difficulties",
            "what obstacles", "what setbacks", "what struggles", "what troubles",
            "what concerns", "what worries", "what fears",
            "faced", "encountered", "experienced", "dealt with", "handled",
        ]),
        ("changes", &[
            "what changes", "what modifications", "what updates", "what improvements",
            "what adjustments", "what alterations", "what revisions",
            "changed", "modified", "updated", "improved", "adjusted", "altered",
        ]),
        ("relationships_people", &[
            "what kids", "what children", "what siblings", "what family",
            "what relatives", "what cousins", "what grandparents",
            "how many kids", "how many children", "how many siblings",
            "what friends", "what colleagues", "what coworkers", "what teammates",
            "what classmates", "what neighbors", "what acquaintances",
            "what mentors", "what teachers", "what professors", "what coaches",
            "what supervisors", "what managers", "what employees",
            "knows", "know", "met", "meets", "befriended", "befriends",
        ]),
        ("collections_lists", &[
            "list all", "list the", "name all", "name the",
            "enumerate", "complete list", "full list", "entire list",
            "what are all", "what were all", "who are all", "who were all",
            "all of the", "each of the", "every one of", "every single",
            "everything that", "all that", "everyone who", "everybody who",
            "various", "multiple", "several", "different", "diverse",
        ]),
        ("preferences_favorites", &[
            "favorite things", "favorite activities", "favorite places",
            "favorite foods", "favorite books", "favorite movies",
            "what is your favorite", "what are your favorite",
            "what's your favorite", "what were your favorite",
            "preferred", "preference for", "top choices", "main interests",
            "what do you like", "what do you enjoy", "what do you love",
            "what do you prefer", "best liked", "most enjoyed",
            "least favorite", "like the least", "dislike", "hate",
        ]),
        ("education_learning", &[
            "what courses", "what classes", "what subjects", "what topics",
            "what degrees", "what certifications", "what training",
            "what schools", "what universities", "what colleges",
            "studied", "studies", "learned", "learns", "took", "takes",
        ]),
        ("work_career", &[
            "what jobs", "what positions", "what roles", "what responsibilities",
            "what projects", "what assignments", "what tasks",
            "what companies", "what organizations", "what employers",
            "worked", "works", "employed", "contracted",
        ]),
    ]),
    ("INFERENTIAL", &[
        ("modals_possibility", &[
            "would ", "would be", "would have", "would likely", "would probably",
            "would possibly", "would seem", "would appear", "wouldn't ",
            "could ", "could be", "could have", "could likely", "could possibly",
            "couldn't ",
            "might ", "might be", "might have", "might not", "mightn't ",
            "may ", "may be", "may have", "may not",
            "should ", "should be", "should have", "shouldn't ",
            "must be", "must have", "can't be", "cannot be",
        ]),
        ("causation_reasoning", &[
            "why ", "why's ", "why did ", "why does ", "why is ", "why was ",
            "why has ", "why have ", "why would ", "why should ", "why might ",
            "how come ", "how is it that", "how does it happen",
            "what caused ", "what made ", "what led to ", "what resulted in",
            "what brought about", "what triggered", "what prompted",
            "reason for", "reasons for", "reasoning behind", "rationale for",
            "motivation for", "motivations for", "motive for",
            "purpose of", "goal of", "aim of", "objective of",
            "caused by", "due to", "because of", "thanks to", "owing to",
            "attributed to", "stemming from", "arising from",
            "impact of", "effect of", "consequence of", "result of",
            "outcome of", "implication of",
        ]),
        ("hypothetical_speculation", &[
            "is it likely", "is it possible", "is it probable", "is it plausible",
            "would it be", "could it be", "might it be",
            "what if", "if ", "suppose ", "supposing ", "imagine if",
            "assuming that", "given that", "provided that",
            "likelihood", "likelihood of", "probability", "probability of",
            "chance", "chance of", "potential", "potential for",
            "possibility", "possibility of", "risk", "risk of",
            "speculate", "speculation", "conjecture", "theory", "hypothesis",
            "will likely", "will probably", "will possibly",
            "going to", "expected to", "projected to", "anticipated to",
            "scenario where", "case where", "situation where", "instance where",
        ]),
        ("opinion_judgment", &[
            "do you think", "would you say", "would you agree",
            "in your opinion", "what do you think", "what would you say",
            "personally", "i believe", "i think", "i feel", "i suspect",
            "would you consider", "would you describe", "would you characterize",
            "how would you describe", "how would you characterize",
            "how would you rate", "how would you evaluate",
            "impression of", "view of", "perspective on", "stance on",
            "attitude toward", "opinion on", "feeling about",
            "assessment of", "evaluation of", "appraisal of", "judgment of",
        ]),
        ("inference_deduction", &[
            " likely ", " likely?", " probably ", " probably?",
            " possibly ", " possibly?", " perhaps ", " maybe ",
            " presumably ", " conceivably ", " potentially ",
            " suggest ", " suggests ", " imply ", " implies ",
            " indicate ", " indicates ", " predict ", " predicts ",
            " infer ", " infers ", " deduce ", " deduces ",
            " conclude ", " concludes ", " assume ", " assumes ",
            " suppose ", " supposes ", " presume ", " presumes ",
            " believe ", " believes ", " seem ", " seems ",
            " appear ", " appears ", " look like ", " looks like ",
            " sound like ", " sounds like ",
            "interpret", "interpretation", "understand", "understanding",
            "construe", "perceive", "perception",
        ]),
        ("personality_traits", &[
            "what kind of person", "what type of person", "what sort of person",
            "what personality", "personality type", "personality traits",
            "what traits", "what characteristics", "what qualities",
            "what attributes", "what tendencies", "what inclinations",
            "describe ", "description of", "characterize", "characterization of",
            "nature of", "temperament", "disposition", "demeanor",
            "character of", "mentality", "mindset",
        ]),
        ("career_interests", &[
            "what fields", "what field", "what career", "what careers",
            "what job", "what jobs", "what profession", "what professions",
            "what industry", "what industries", "what sector", "what domain",
            " pursue ", " pursuing ", " aspire ", " aspiring ",
            " strive ", " striving ", " aim ", " aiming ",
            "interested in", "interest in", "passion for", "passionate about",
            "suited for", "fit for", "good at", "excel at",
            "talented in", "skilled in", "gifted in",
            "future in", "plans to", "planning to", "hoping to",
            "wanting to", "seeking to", "looking to",
        ]),
        ("preferences_inclinations", &[
            "be considered ", "considered to be", "be seen as",
            "be regarded as", "be viewed as", "be thought of as",
            "be open to ", "be willing to", "be inclined to",
            "be interested in", "be attracted to", "be drawn to",
            "be likely to", "be prone to", "be apt to",
            "enjoy ", "appreciate ", "value ", "cherish ",
            "embrace ", "welcome ", "favor ", "support ",
            "lean toward", "leaning toward", "tend to", "tendency to",
        ]),
        ("advice_recommendation", &[
            "should", "shouldn't", "ought to", "ought not to",
            "supposed to", "not supposed to",
            "recommend", "recommendation", "suggested", "suggestion",
            "advise", "advice", "propose", "proposal",
            "better", "better to", "best to", "best if",
            "preferable", "advisable", "wise to", "smart to",
            "worth", "worthwhile", "beneficial", "helpful",
        ]),
        ("comparison_contrast", &[
            "more than", "less than", "better than", "worse than",
            "greater than", "lesser than", "superior to", "inferior to",
            "compared to", "in comparison", "in contrast", "versus", "vs",
            "different from", "similar to", "same as", "alike", "unlike",
            "resembles", "differs from", "contrasts with",
            "analogous to", "comparable to", "equivalent to",
            "relatively", "comparatively", "in relation to", "relative to",
        ]),
        ("emotional_mental_state", &[
            "how does .* feel", "how do .* feel", "feel about",
            "feelings about", "emotion", "emotional state",
            "what does .* think", "what do .* think", "think about",
            "thoughts on", "belief about", "believes that",
            "attitude toward", "attitudes toward", "view of", "views on",
            "opinion on", "opinions on", "stance on",
            "mindset", "perspective", "outlook", "mentality",
        ]),
        ("behavioral_prediction", &[
            "likely to do", "probably do", "tend to do", "inclined to do",
            "react to", "respond to", "handle", "deal with",
            "approach", "tackle", "address",
            "next step", "next move", "what will", "what would",
            "expected to do", "planning to do", "intending to do",
        ]),
        ("negation_opposite", &[
            " wouldn't ", " couldn't ", " shouldn't ", " mightn't ",
            " won't ", " can't ", " don't ", " doesn't ",
            "unlikely", "improbable", "doubtful", "questionable",
            "not likely", "not probable", "not possible",
            "instead of", "rather than", "as opposed to", "contrary to",
        ]),
        ("relationships_connections", &[
            "how close", "how well", "relationship with", "connection with",
            "bond with", "ties to", "link to", "association with",
            "get along with", "compatible with", "friendly with",
            "rapport with", "chemistry with",
        ]),
    ]),
    ("STRICT", &[
        ("direct_extraction", &[
            "what is ", "what's ", "what was ", "what were ",
            "who is ", "who's ", "who was ", "who were ",
            "which is ", "which was ",
            "is ", "are ", "was ", "were ",
        ]),
        ("identity", &[
            "define", "definition", "meaning", "means",
            "identity of", "name of", "called",
        ]),
        ("simple_attributes", &[
            "color", "size", "weight", "height", "age",
            "name", "address", "phone", "email",
            "job", "occupation", "profession", "role",
        ]),
    ]),
];

/// `QUERY_MODE_MARKERS[mode][category]` (empty when absent, like `.get(category, set())`).
pub fn query_mode_markers(mode: &str, category: &str) -> &'static [&'static str] {
    QUERY_MODE_MARKERS
        .iter()
        .find(|(m, _)| *m == mode)
        .and_then(|(_, cats)| cats.iter().find(|(c, _)| *c == category))
        .map(|(_, p)| *p)
        .unwrap_or(&[])
}

fn mode_categories(mode: &str) -> &'static [MarkerCategory] {
    QUERY_MODE_MARKERS.iter().find(|(m, _)| *m == mode).map(|(_, c)| *c).unwrap_or(&[])
}

static WHAT_EVENTS: LazyLock<Regex> = LazyLock::new(|| re(r"\bwhat\b.{1,30}\bevents\b"));

/// `infer_query_mode`: `"TEMPORAL"`, `"INFERENTIAL"`, `"AGGREGATION"`, `"ADVERSARIAL"` or `"STRICT"` from the
/// marker table (prefix tests for the starter categories, substring tests for the rest).
pub fn infer_query_mode(question: &str) -> &'static str {
    let lowered = question.to_lowercase();
    let q = py_strip(&lowered);

    #[rustfmt::skip]
    const NON_TEMPORAL_STARTERS: [&str; 21] = [
        "where did", "where does", "where is", "where was", "where has", "where have",
        "what did", "what does", "what has", "what have", "what was", "what is", "what are",
        "who did", "who does", "who is", "who was",
        "how did", "how does", "how has", "how was",
    ];
    let mut non_temporal = NON_TEMPORAL_STARTERS.iter().any(|s| q.starts_with(s));
    if q.contains(" about?") || q.contains(" about ") {
        non_temporal = true;
    }

    if !non_temporal {
        for category in ["when_starters", "date_time_queries"] {
            if query_mode_markers("TEMPORAL", category).iter().any(|p| q.starts_with(p)) {
                return "TEMPORAL";
            }
        }
        for category in [
            "duration",
            "sequence_order",
            "relative_time",
            "elapsed_time",
            "schedule_deadline",
            "age_milestone",
            "frequency",
            "temporal_context",
        ] {
            if query_mode_markers("TEMPORAL", category).iter().any(|p| q.contains(p)) {
                return "TEMPORAL";
            }
        }
    }

    for category in ["modals_possibility", "causation_reasoning", "hypothetical_speculation"] {
        if query_mode_markers("INFERENTIAL", category).iter().any(|p| q.starts_with(p)) {
            return "INFERENTIAL";
        }
    }
    for category in [
        "opinion_judgment",
        "inference_deduction",
        "personality_traits",
        "career_interests",
        "preferences_inclinations",
        "advice_recommendation",
        "comparison_contrast",
        "emotional_mental_state",
        "behavioral_prediction",
        "negation_opposite",
        "relationships_connections",
    ] {
        if query_mode_markers("INFERENTIAL", category).iter().any(|p| q.contains(p)) {
            return "INFERENTIAL";
        }
    }
    #[rustfmt::skip]
    const HIGH_PRIORITY_INFERENCE: [&str; 15] = [
        " might ", " could ", " may ", " would ", " should ",
        "underlying", "based on", "given", "considering",
        "alternative", "instead", "rather",
        "describe", "characterize", "personality",
    ];
    if HIGH_PRIORITY_INFERENCE.iter().any(|p| q.contains(p)) {
        return "INFERENTIAL";
    }

    for (_, patterns) in mode_categories("AGGREGATION") {
        if patterns.iter().any(|p| q.contains(p)) {
            return "AGGREGATION";
        }
    }
    if q.contains("how many") {
        let time_units = ["days", "weeks", "months", "years", "hours", "minutes", "seconds"];
        if !time_units.iter().any(|t| q.contains(t)) {
            return "AGGREGATION";
        }
    }
    if WHAT_EVENTS.is_match(q) {
        return "AGGREGATION";
    }

    #[rustfmt::skip]
    const ADVERSARIAL_PATTERNS: &[&str] = &[
        " not ", "n't ", " never ", " no ", " none ", " nothing ",
        " nobody ", " nowhere ", " neither ", " nor ",
        " except ", " other than ", " apart from ", " besides ",
        " excluding ", " but not ", " save for ",
        " without ", " lacking ", " missing ", " absent ",
        " fail", " failed ", " failing ", "didn't ", "doesn't ",
        "wasn't", "weren't", "hasn't", "haven't", "hadn't",
        "won't", "wouldn't", "can't", "couldn't", "shouldn't",
        " instead of ", " rather than ", " as opposed to ",
        " deny ", " denied ", " refuse ", " refused ",
        " reject ", " rejected ",
        " isn't ", " aren't ", " ain't ",
        " if not ", " unless ", " otherwise ",
        " really ", " actually ", " truly ", " genuinely ",
    ];
    if ADVERSARIAL_PATTERNS.iter().any(|p| q.contains(p)) {
        return "ADVERSARIAL";
    }
    "STRICT"
}

/// `preprocess_message_for_indexing`: resolved content plus appended date tokens and the structured parts.
///
/// `message_date` wins over `datetime_str`, which is parsed with [`parse_datetime_flexible`] (its `Err` for an
/// impossible date is Python's `ValueError`).
pub fn preprocess_message_for_indexing(
    content: &str,
    datetime_str: Option<&str>,
    message_date: Option<NaiveDateTime>,
) -> TResult<Map<String, Value>> {
    let mut message_date = message_date;
    if message_date.is_none() {
        if let Some(s) = datetime_str.filter(|s| !s.is_empty()) {
            message_date = parse_datetime_flexible(Some(s))?.map(|d| d.naive);
        }
    }
    let (resolved_content, resolved_dates) = resolve_relative_dates(content, message_date)?;
    let temporal = generate_temporal_tokens(message_date, content)?;
    let mut content_with_tokens = resolved_content.clone();
    if !temporal.date_tokens.is_empty() {
        content_with_tokens.push(' ');
        content_with_tokens.push_str(&temporal.date_tokens.join(" "));
    }
    let mut out = Map::new();
    out.insert("content_with_tokens".into(), json!(content_with_tokens));
    out.insert("resolved_content".into(), json!(resolved_content));
    out.insert("original_content".into(), json!(content));
    out.insert("date_tokens".into(), json!(temporal.date_tokens));
    out.insert("temporal_metadata".into(), Value::Object(temporal.temporal_metadata));
    out.insert("resolved_dates".into(), resolved_dates_value(&resolved_dates));
    Ok(out)
}

fn resolved_dates_value(resolved: &IndexMap<String, String>) -> Value {
    Value::Object(resolved.iter().map(|(k, v)| (k.clone(), json!(v))).collect())
}

static ANY_DIGIT: LazyLock<Regex> = LazyLock::new(|| re(r"\d"));

/// `generate_temporal_fields`: temporal metadata without touching the content. `resolved_date` is the explicit
/// date, else the single distinct date-like relative resolution, else the message date.
pub fn generate_temporal_fields(content: &str, message_date: Option<NaiveDateTime>) -> TResult<Map<String, Value>> {
    let (resolved_content, resolved_dates) = resolve_relative_dates(content, message_date)?;
    let temporal = generate_temporal_tokens(message_date, content)?;
    let explicit_date = extract_explicit_date(content, message_date)?;
    let duration_meta = extract_duration_metadata(content, message_date);

    let mut resolved_date: Option<String> = None;
    let mut source: Option<&str> = None;
    if let Some(e) = explicit_date.as_ref().filter(|e| !e.is_empty()) {
        resolved_date = Some(e.clone());
        source = Some("explicit");
    } else if !resolved_dates.is_empty() {
        let mut unique: Vec<&String> = Vec::new();
        for v in resolved_dates.values() {
            if ANY_DIGIT.is_match(v) && !unique.contains(&v) {
                unique.push(v);
            }
        }
        if unique.len() == 1 {
            resolved_date = Some(unique[0].clone());
            source = Some("relative");
        }
    }
    if resolved_date.as_deref().is_none_or(str::is_empty) {
        if let Some(md) = message_date {
            resolved_date = Some(ymd_string(md));
            source = Some("message");
        }
    }

    let mut out = Map::new();
    out.insert("resolved_date".into(), json!(resolved_date));
    out.insert("temporal_tokens".into(), json!(temporal.date_tokens));
    out.insert("temporal_metadata".into(), Value::Object(temporal.temporal_metadata));
    out.insert("resolved_dates".into(), resolved_dates_value(&resolved_dates));
    out.insert("resolved_content".into(), json!(resolved_content));
    out.insert("explicit_date".into(), json!(explicit_date));
    out.insert("resolved_date_source".into(), json!(source));
    for (k, v) in duration_meta {
        out.insert(k, v);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dt(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M").unwrap()
    }

    #[test]
    fn tokens_for_a_locomo_date() {
        let t = generate_temporal_tokens(Some(dt("2023-05-08 13:56")), "went camping yesterday").unwrap();
        assert_eq!(t.date_tokens[..4], ["DATE_2023-05-08", "YEAR_2023", "MONTH_05", "MONTH_MAY"]);
        assert_eq!(t.date_tokens.last().unwrap(), "REL_YESTERDAY_RESOLVED_2023-05-07");
    }

    #[test]
    fn query_modes() {
        assert_eq!(infer_query_mode("When did Caroline go camping?"), "TEMPORAL");
        assert_eq!(infer_query_mode("What was the poetry reading about?"), "AGGREGATION");
        assert_eq!(infer_query_mode("Would Melanie enjoy hiking?"), "INFERENTIAL");
    }

    #[test]
    fn explicit_date_raises_on_impossible_day() {
        assert!(extract_explicit_date("see you 31 June", Some(dt("2023-05-08 10:00"))).is_err());
        assert_eq!(extract_explicit_date("on 2023/13/40 or 8 May", None).unwrap(), None);
    }
}
