//! Selective memory + relationship extraction with a small instruction model (Qwen-7B class).
//!
//! Pipeline for one batch of consecutive messages from one chat (see [`MemoryExtractor::build_plan`]):
//!
//! 1. **Gate** — messages that are almost certainly chit-chat ([`is_probable_chitchat`]) are marked skipped
//!    without any LLM call.
//! 2. **Extract** — two LLM calls run concurrently: `MEMORY_PROMPT` (self-contained third-person memories with
//!    subject/kind/when/importance/confidence/sources/entities) and `RELATION_PROMPT` (entities and typed triples,
//!    alias-resolved against the cross-chat entity registry).
//! 3. **Normalise** — names resolve through [`EntityRegistry`] (speakers, aliases, known entities, prefix/fuzzy
//!    match); `when` becomes an ISO `event_time` + precision; weak candidates are dropped.
//! 4. **Reconcile** — every surviving candidate is embedded and compared with existing active memories of the
//!    same subject: cosine >= `dedupe_cosine` is a duplicate (no LLM); cosine >= `reconcile_cosine` asks the LLM
//!    (`RECONCILE_PROMPT`) to choose ADD / DUPLICATE / UPDATE / CONTRADICT; otherwise ADD.
//! 5. **Plan** — the result is an [`ExtractionPlan`] that the store applies in one transaction.
//!
//! Nothing here writes to the store; that keeps retries idempotent.

use crate::difflib::get_close_matches;
use crate::error::{MemoryError, Result};
use crate::jsonutil::{as_list, as_str, as_str_text, as_unit, parse_json_object};
use crate::llm::LlmClient;
use crate::models::{new_id, now_iso, parse_iso, ChatMessage, Memory, MEMORY_KINDS};
use crate::prompts;
use crate::pycompat::{
    fnum, obj, py_float_repr, py_int_from_value, py_len, py_prefix, py_repr_str, py_round, py_strip,
};
use crate::store::{ChatMemoryStore, ExtractionPlan, MemoryQuery, PlanEntity, PlanRelation};
use crate::temporal::{parse_datetime_flexible, resolve_relative_dates};
use crate::textutil::{clip, content_hash, is_probable_chitchat, norm_entity, norm_relation, normalize_ws, tokenize};
use indexmap::{IndexMap, IndexSet};
use regex::Regex;
use serde_json::Value;
use std::sync::{Arc, LazyLock};

/// Python `str.format` for the prompt templates: `{name}` is replaced, `{{`/`}}` become literal braces.
pub fn py_format(template: &str, values: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len() + 256);
    let mut chars = template.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '{' if chars.peek() == Some(&'{') => {
                chars.next();
                out.push('{');
            }
            '}' if chars.peek() == Some(&'}') => {
                chars.next();
                out.push('}');
            }
            '{' => {
                let mut name = String::new();
                for n in chars.by_ref() {
                    if n == '}' {
                        break;
                    }
                    name.push(n);
                }
                let v = values.iter().find(|(k, _)| *k == name).map(|(_, v)| *v).unwrap_or("");
                out.push_str(v);
            }
            other => out.push(other),
        }
    }
    out
}

#[rustfmt::skip]
const TYPE_MAP: [(&str, &str); 47] = [
    ("person", "person"), ("people", "person"), ("human", "person"), ("user", "person"), ("friend", "person"),
    ("family", "person"), ("place", "place"), ("location", "place"), ("city", "place"), ("country", "place"),
    ("address", "place"), ("venue", "place"), ("org", "org"), ("organisation", "org"), ("organization", "org"),
    ("company", "org"), ("employer", "org"), ("school", "org"), ("university", "org"), ("team", "org"),
    ("band", "org"), ("pet", "pet"), ("animal", "pet"), ("dog", "pet"), ("cat", "pet"), ("product", "product"),
    ("device", "product"), ("software", "product"), ("tool", "product"), ("app", "product"), ("brand", "product"),
    ("project", "project"), ("event", "event"), ("activity", "event"), ("trip", "event"), ("hobby", "other"),
    ("job", "other"), ("media", "product"), ("book", "product"), ("movie", "product"), ("film", "product"),
    ("game", "product"), ("show", "product"), ("health", "other"), ("condition", "other"), ("date", "other"),
    ("time", "other"),
];

fn type_map(key: &str) -> Option<&'static str> {
    TYPE_MAP.iter().find(|(k, _)| *k == key).map(|(_, v)| *v)
}

const FIRST_PERSON: [&str; 8] = ["i", "me", "my", "myself", "mine", "the user", "user", "speaker"];
static ANNOTATION_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]*\[=[\s\x1c-\x1f]*[^\]]*\]").unwrap());
/// mentions that are never entities on their own (dates, generic words the model likes to tag)
#[rustfmt::skip]
const BLOCKED_ENTITY_KEYS: [&str; 52] = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "yesterday",
    "tomorrow", "week", "month", "year", "weekend", "morning", "evening", "night", "afternoon", "spring", "summer",
    "autumn", "fall", "winter", "update", "thing", "things", "stuff", "someone", "something", "everyone", "nothing",
    "none", "null", "unknown", "n a", "user", "assistant", "ai", "bot", "system",
];
const BLOCKED_EXTRA: [&str; 3] = ["chat", "message", "conversation"];
#[rustfmt::skip]
const COMMON_CAPS: [&str; 73] = [
    "the", "she", "they", "his", "her", "their", "this", "that", "there", "these", "those", "when", "where", "what",
    "who", "how", "after", "before", "since", "during", "every", "each", "both", "also", "then", "now", "however",
    "although", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "new",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "christmas", "easter", "english",
    "spanish", "french", "german", "italian", "chinese", "japanese", "covid", "internet", "instagram", "youtube",
    "monday", "the",
];

fn is_blocked_key(key: &str) -> bool {
    BLOCKED_ENTITY_KEYS.contains(&key) || BLOCKED_EXTRA.contains(&key)
}

/// Canonical entity type for a model-supplied type string ("" when none).
pub fn norm_type(t: &str) -> String {
    let key = norm_entity(&as_str_text(t, 40));
    if key.is_empty() {
        return String::new();
    }
    if let Some(v) = type_map(&key) {
        return v.to_string();
    }
    let first = key.split(' ').next().unwrap_or("");
    type_map(first).unwrap_or("other").to_string()
}

// ---------------------------------------------------------------------------------------------
// Entity registry (alias resolution across chats)
// ---------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct RegistryEntry {
    pub name: String,
    pub etype: String,
    pub count: i64,
}

/// In-memory view of known entities for one extraction pass.
///
/// Built from the chat participants, the globally most-mentioned entities, this chat's entities and all aliases.
/// `resolve` maps a raw mention to a canonical `entity_id` (new ids are returned for genuinely new entities and
/// recorded so that the same batch reuses them).
#[derive(Clone, Debug)]
pub struct EntityRegistry {
    /// entity_id -> entry
    pub entries: IndexMap<String, RegistryEntry>,
    /// alias key -> entity_id
    pub aliases: IndexMap<String, String>,
    pub fuzzy_cutoff: f64,
    pub new_entities: IndexMap<String, RegistryEntry>,
}

impl Default for EntityRegistry {
    fn default() -> Self {
        EntityRegistry {
            entries: IndexMap::new(),
            aliases: IndexMap::new(),
            fuzzy_cutoff: 0.9,
            new_entities: IndexMap::new(),
        }
    }
}

impl EntityRegistry {
    pub fn add(&mut self, entity_id: &str, name: &str, etype: &str, count: i64, aliases: &[String]) {
        if entity_id.is_empty() {
            return;
        }
        match self.entries.get_mut(entity_id) {
            None => {
                self.entries.insert(
                    entity_id.to_string(),
                    RegistryEntry {
                        name: if name.is_empty() { entity_id.to_string() } else { name.to_string() },
                        etype: etype.to_string(),
                        count,
                    },
                );
            }
            Some(cur) => {
                cur.count = cur.count.max(count);
                if cur.etype.is_empty() && !etype.is_empty() {
                    cur.etype = etype.to_string();
                }
            }
        }
        for a in aliases {
            let k = norm_entity(a);
            if !k.is_empty() && k != entity_id {
                self.aliases.entry(k).or_insert_with(|| entity_id.to_string());
            }
        }
    }

    pub fn load(
        store: &ChatMemoryStore,
        chat_id: &str,
        speakers: &[String],
        speaker_names: &IndexMap<String, String>,
        limit: i64,
    ) -> Result<Self> {
        let mut reg = EntityRegistry::default();
        for sp in speakers {
            let display = speaker_names.get(sp).cloned().unwrap_or_else(|| sp.clone());
            let key = norm_entity(&display);
            if !key.is_empty() {
                let aliases = if *sp != display { vec![sp.clone()] } else { Vec::new() };
                reg.add(&key, &display, "person", 1, &aliases);
            }
        }
        for e in store.top_entities(limit, None)? {
            reg.add(&e.entity_id, &e.name, &e.etype, e.mention_count, &[]);
        }
        for e in store.entities_for_chat(chat_id, 60)? {
            reg.add(&e.entity_id, &e.name, &e.etype, e.mention_count, &[]);
        }
        for (k, v) in store.all_entity_keys()? {
            if k != v {
                reg.aliases.insert(k, v);
            }
        }
        Ok(reg)
    }

    pub fn prompt_text(&self, limit: usize) -> String {
        let mut top: Vec<(&String, &RegistryEntry)> = self.entries.iter().collect();
        top.sort_by_key(|(_, v)| std::cmp::Reverse(v.count));
        top.truncate(limit);
        if top.is_empty() {
            return "(none yet)".into();
        }
        top.iter()
            .map(|(_, v)| if v.etype.is_empty() { v.name.clone() } else { format!("{} ({})", v.name, v.etype) })
            .collect::<Vec<_>>()
            .join(", ")
    }

    pub fn display(&self, entity_id: &str) -> String {
        self.entries
            .get(entity_id)
            .or_else(|| self.new_entities.get(entity_id))
            .map(|e| e.name.clone())
            .unwrap_or_else(|| entity_id.to_string())
    }

    pub fn type_of(&self, entity_id: &str) -> String {
        self.entries
            .get(entity_id)
            .or_else(|| self.new_entities.get(entity_id))
            .map(|e| e.etype.clone())
            .unwrap_or_default()
    }

    fn knows(&self, key: &str) -> bool {
        self.entries.contains_key(key) || self.new_entities.contains_key(key)
    }

    /// Map a mention to an entity id. `None` for empty/unusable mentions (or first person without a speaker).
    pub fn resolve(
        &mut self,
        mention: Option<&Value>,
        speaker_id: Option<&str>,
        etype: &str,
        create: bool,
    ) -> Option<String> {
        let raw = as_str(mention, 80);
        let key = norm_entity(&raw);
        if key.is_empty() {
            return None;
        }
        if FIRST_PERSON.contains(&key.as_str()) {
            return speaker_id.map(str::to_string);
        }
        let numeric_only = key.chars().all(|c| c.is_ascii_digit() || c == ' ' || c == '\'' || c == '-');
        if is_blocked_key(&key) || numeric_only || py_len(&key) < 2 {
            return None;
        }
        if self.knows(&key) {
            return Some(key);
        }
        if let Some(id) = self.aliases.get(&key) {
            return Some(id.clone());
        }
        // "melanie's" / "dr. smith" style variants are handled by norm_entity; now try structural matches
        let people: Vec<&String> = self.entries.iter().filter(|(_, v)| v.etype == "person").map(|(k, _)| k).collect();
        // first-name / prefix match against people (either direction, min 3 chars, unambiguous)
        if py_len(&key) >= 3 {
            // a short mention ("Mel", "Melanie") may name a known longer person ("melanie carter"); a LONGER mention
            // ("Alex Jones") never collapses onto a shorter known key ("alex")
            let prefix = format!("{key} ");
            let cands: Vec<&&String> = people.iter().filter(|eid| eid.starts_with(&prefix)).collect();
            if cands.len() == 1 {
                return Some((*cands[0]).clone());
            }
            // a bare first name that matches exactly one person's first token
            let cands: Vec<&&String> =
                people.iter().filter(|eid| eid.split(' ').next() == Some(key.as_str())).collect();
            if cands.len() == 1 {
                return Some((*cands[0]).clone());
            }
        }
        if py_len(&key) >= 5 {
            let pool =
                self.entries.keys().chain(self.new_entities.keys()).chain(self.aliases.keys()).map(String::as_str);
            if let Some(best) = get_close_matches(&key, pool, 1, self.fuzzy_cutoff).into_iter().next() {
                return Some(self.aliases.get(&best).cloned().unwrap_or(best));
            }
        }
        if !create {
            return None;
        }
        let stripped = py_strip(&raw);
        self.new_entities.insert(
            key.clone(),
            RegistryEntry {
                name: if stripped.is_empty() { key.clone() } else { stripped.to_string() },
                etype: norm_type(etype),
                count: 0,
            },
        );
        Some(key)
    }

    /// `resolve` for a plain string mention.
    pub fn resolve_str(
        &mut self,
        mention: &str,
        speaker_id: Option<&str>,
        etype: &str,
        create: bool,
    ) -> Option<String> {
        self.resolve(Some(&Value::String(mention.to_string())), speaker_id, etype, create)
    }
}

// ---------------------------------------------------------------------------------------------
// Temporal normalisation of the "when" field
// ---------------------------------------------------------------------------------------------

/// Full month names and their three-letter forms -> month number.
fn month_lookup(name: &str) -> Option<u32> {
    crate::temporal::month_number(name)
        .or_else(|| crate::temporal::MONTH_NAMES.iter().position(|m| &m[..3] == name).map(|i| i as u32 + 1))
}

static ISO_DAY_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[t ].*)?$").unwrap());
static ISO_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([0-9]{4})-([0-9]{2})$").unwrap());
static YEAR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([0-9]{4})$").unwrap());
static MONTH_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([a-z]+)\.?[\s\x1c-\x1f]+([0-9]{4})$").unwrap());
static DAY_MONTH_YEAR_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([0-9]{1,2})[\s\x1c-\x1f]+([a-z]+)\.?,?[\s\x1c-\x1f]+([0-9]{4})$").unwrap());
static MONTH_DAY_YEAR_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([a-z]+)\.?[\s\x1c-\x1f]+([0-9]{1,2}),?[\s\x1c-\x1f]+([0-9]{4})$").unwrap());
static ANY_DIGIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d").unwrap());
static YM_SEARCH_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"([a-z]+)[\s\x1c-\x1f]+(\d{4})").unwrap());
static DMY_PLAIN_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([0-9]{1,2})[\s\x1c-\x1f]+([a-z]+)[\s\x1c-\x1f]+([0-9]{4})$").unwrap());

fn ymd_string(y: i64, m: i64, d: i64) -> Option<String> {
    if !(1..=9999).contains(&y) {
        return None;
    }
    chrono::NaiveDate::from_ymd_opt(y as i32, m as u32, d as u32)?;
    Some(format!("{y:04}-{m:02}-{d:02}"))
}

/// Turn a model-supplied `when` into (ISO event_time, precision) or (None, "none").
pub fn parse_when(when: &str, observed_at: Option<&str>) -> (Option<String>, String) {
    let none = (None, "none".to_string());
    let collapsed = as_str_text(when, 60);
    let s = py_strip(&collapsed).trim_matches('"').to_lowercase();
    if s.is_empty() || matches!(s.as_str(), "null" | "none" | "unknown" | "n/a" | "na" | "-") {
        return none;
    }
    let int = |x: &str| x.parse::<i64>().unwrap_or(0);
    if let Some(m) = ISO_DAY_RE.captures(&s) {
        return match ymd_string(int(&m[1]), int(&m[2]), int(&m[3])) {
            Some(d) => (Some(d), "day".into()),
            None => none,
        };
    }
    if let Some(m) = ISO_MONTH_RE.captures(&s) {
        let mo = int(&m[2]);
        if (1..=12).contains(&mo) {
            return (Some(format!("{:04}-{:02}", int(&m[1]), mo)), "month".into());
        }
    }
    if let Some(m) = YEAR_RE.captures(&s) {
        if (1900..=2100).contains(&int(&m[1])) {
            return (Some(m[1].to_string()), "year".into());
        }
    }
    if let Some(m) = MONTH_YEAR_RE.captures(&s) {
        if let Some(mo) = month_lookup(&m[1]) {
            return (Some(format!("{:04}-{:02}", int(&m[2]), mo)), "month".into());
        }
    }
    if let Some(m) = DAY_MONTH_YEAR_RE.captures(&s) {
        if let Some(mo) = month_lookup(&m[2]) {
            return match ymd_string(int(&m[3]), mo as i64, int(&m[1])) {
                Some(d) => (Some(d), "day".into()),
                None => none,
            };
        }
    }
    if let Some(m) = MONTH_DAY_YEAR_RE.captures(&s) {
        if let Some(mo) = month_lookup(&m[1]) {
            return match ymd_string(int(&m[3]), mo as i64, int(&m[2])) {
                Some(d) => (Some(d), "day".into()),
                None => none,
            };
        }
    }
    // relative phrases: resolve with the temporal utilities against the message date
    let Some(base) = parse_iso(observed_at) else { return none };
    let yr = match s.as_str() {
        "this year" => Some(0),
        "last year" => Some(-1),
        "next year" => Some(1),
        "a year ago" => Some(-1),
        "two years ago" => Some(-2),
        _ => None,
    };
    if let Some(delta) = yr {
        use chrono::Datelike;
        return (Some((base.year() as i64 + delta).to_string()), "year".into());
    }
    resolve_relative_when(&s, base.naive_local()).unwrap_or(none)
}

/// The `try:` block of the relative branch; `Err`/`None` both mean "no date" (the original swallowed exceptions).
fn resolve_relative_when(s: &str, base: chrono::NaiveDateTime) -> Option<(Option<String>, String)> {
    let (_, resolved) = resolve_relative_dates(s, Some(base)).ok()?;
    let vals: Vec<&String> = resolved.values().filter(|v| ANY_DIGIT_RE.is_match(v)).collect();
    if vals.len() == 1 {
        match parse_datetime_flexible(Some(vals[0])) {
            Err(_) => return None,
            Ok(Some(dt)) => return Some((Some(dt.ymd()), "day".into())),
            Ok(None) => {}
        }
        let lower = vals[0].to_lowercase();
        if let Some(ym) = YM_SEARCH_RE.captures(&lower) {
            if let Some(mo) = month_lookup(&ym[1]) {
                let y: i64 = ym[2].parse().ok()?;
                return Some((Some(format!("{y:04}-{mo:02}")), "month".into()));
            }
        }
    }
    if DMY_PLAIN_RE.is_match(s) {
        match parse_datetime_flexible(Some(s)) {
            Err(_) => return None,
            Ok(Some(dt)) => return Some((Some(dt.ymd()), "day".into())),
            Ok(None) => {}
        }
    }
    None
}

// ---------------------------------------------------------------------------------------------
// Candidates and configuration
// ---------------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct CandidateMemory {
    pub text: String,
    pub kind: String,
    pub subject_raw: String,
    pub importance: f64,
    pub confidence: f64,
    pub when_raw: String,
    pub source_indices: Vec<usize>,
    /// (name, type)
    pub entities: Vec<(String, String)>,
    // filled during normalisation
    pub subject_id: String,
    pub subject_name: String,
    pub speaker: String,
    pub source_message_ids: Vec<String>,
    pub observed_at: String,
    pub event_time: Option<String>,
    pub event_time_precision: String,
    pub entity_ids: Vec<String>,
    pub embedding: Option<Vec<f64>>,
    pub text_hash: String,
}

/// Extraction settings (defaults are the measured ones).
#[derive(Clone, Debug)]
pub struct ExtractionConfig {
    pub context_messages: i64,
    /// raised to `max_assistant_message_chars` for assistant turns when they are sources
    pub max_message_chars: usize,
    pub max_assistant_message_chars: usize,
    /// measured: the model under-extracts; 12 with the 'keep every detail' rule
    pub max_memories_per_batch: usize,
    pub max_triples_per_batch: usize,
    pub registry_prompt_entities: usize,
    pub known_memories_in_prompt: usize,
    pub min_importance: f64,
    pub min_confidence: f64,
    pub dedupe_cosine: f64,
    pub reconcile_cosine: f64,
    pub related_cosine: f64,
    pub reconcile_candidates: usize,
    pub max_related_links: usize,
    pub extract_relations: bool,
    pub gate_chitchat: bool,
    /// 12 memories x ~150 tokens of JSON plus headroom
    pub generate_max_tokens: i64,
    pub reconcile_max_tokens: i64,
    pub llm_timeout: f64,
    /// e.g. {"user": "Ali Novruzov"}
    pub speaker_names: IndexMap<String, String>,
    /// never the *subject* of a memory
    pub ignore_speakers: Vec<String>,
    /// assistant turns are sources too (facts the assistant gave the user, stored from the user's side). Measured
    /// on LongMemEval: 87.0 -> 93.5 turn@15 at ~1.85x memories. False = assistant turns are context only.
    pub extract_assistant: bool,
    /// "yesterday [= 7 May 2023]" so the model copies absolute dates
    pub annotate_relative_dates: bool,
    /// share of a memory's content tokens that must appear in the window
    pub min_grounding_overlap: f64,
    /// capitalised names in a memory must occur in the window/registry
    pub require_name_grounding: bool,
}

impl Default for ExtractionConfig {
    fn default() -> Self {
        ExtractionConfig {
            context_messages: 6,
            max_message_chars: 1500,
            max_assistant_message_chars: 3000,
            max_memories_per_batch: 12,
            max_triples_per_batch: 12,
            registry_prompt_entities: 80,
            known_memories_in_prompt: 12,
            min_importance: 0.3,
            min_confidence: 0.45,
            dedupe_cosine: 0.94,
            reconcile_cosine: 0.62,
            related_cosine: 0.55,
            reconcile_candidates: 4,
            max_related_links: 3,
            extract_relations: true,
            gate_chitchat: true,
            generate_max_tokens: 3000,
            reconcile_max_tokens: 220,
            llm_timeout: 120.0,
            speaker_names: IndexMap::new(),
            ignore_speakers: vec!["assistant".into(), "system".into()],
            extract_assistant: true,
            annotate_relative_dates: true,
            min_grounding_overlap: 0.2,
            require_name_grounding: true,
        }
    }
}

/// True when observation time `a` is strictly earlier than `b` (timezone-aware; string fallback).
pub fn older(a: Option<&str>, b: Option<&str>) -> bool {
    let (Some(a), Some(b)) = (a.filter(|s| !s.is_empty()), b.filter(|s| !s.is_empty())) else { return false };
    match (parse_iso(Some(a)), parse_iso(Some(b))) {
        (Some(da), Some(db)) => da < db,
        _ => a < b,
    }
}

/// Cosine similarity in f64 (0 for missing or mismatched vectors).
pub fn cos(a: Option<&[f64]>, b: Option<&[f64]>) -> f64 {
    let (Some(a), Some(b)) = (a, b) else { return 0.0 };
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }
    let mut dot = 0.0;
    let mut sa = 0.0;
    let mut sb = 0.0;
    for (x, y) in a.iter().zip(b) {
        dot += x * y;
    }
    for x in a {
        sa += x * x;
    }
    for y in b {
        sb += y * y;
    }
    let na = sa.powf(0.5);
    let nb = sb.powf(0.5);
    if na != 0.0 && nb != 0.0 {
        dot / (na * nb)
    } else {
        0.0
    }
}

fn annotate_dates(text: &str, sent_at: Option<&str>) -> String {
    let Some(base) = parse_iso(sent_at) else { return text.to_string() };
    match resolve_relative_dates(text, Some(base.naive_local())) {
        Ok((annotated, _)) => annotated,
        Err(_) => text.to_string(),
    }
}

fn fmt_messages(
    msgs: &[ChatMessage],
    names: &IndexMap<String, String>,
    max_chars: usize,
    numbered: bool,
    assistant_roles: &[String],
    annotate: bool,
) -> String {
    let mut lines = Vec::with_capacity(msgs.len());
    for (i, m) in msgs.iter().enumerate() {
        let who = names.get(&m.speaker).cloned().unwrap_or_else(|| m.speaker.clone());
        let date = py_prefix(m.sent_at.as_deref().unwrap_or(""), 10);
        let prefix = if numbered { format!("{}. ", i + 1) } else { String::new() };
        let stamp = if !date.is_empty() && !numbered { format!(" ({date})") } else { String::new() };
        let role = if m.role.is_empty() { &m.speaker } else { &m.role };
        let tag = if assistant_roles.contains(&role.to_lowercase()) { " (assistant)" } else { "" };
        let mut body = clip(&normalize_ws(&m.text), max_chars);
        if annotate {
            body = annotate_dates(&body, m.sent_at.as_deref());
        }
        lines.push(format!("{prefix}[{who}{tag}]{stamp}: {body}"));
    }
    if lines.is_empty() {
        "(none)".into()
    } else {
        lines.join("\n")
    }
}

/// The decision taken for one reconciled candidate.
type Decision = (String, Option<Memory>, Option<String>);
/// A candidate with its shortlist and the decision phase 1 reached ("ADD", "ASK", "DUPLICATE") plus its target.
type Pending = (CandidateMemory, Vec<(f64, Memory)>, &'static str, Option<Memory>);

/// Turns a batch of messages into an [`ExtractionPlan`] using the LLM. Read-only with respect to the store.
pub struct MemoryExtractor {
    pub store: Arc<ChatMemoryStore>,
    pub llm: Arc<dyn LlmClient>,
    pub config: ExtractionConfig,
}

static FIRST_PERSON_START_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(?:i|you|he|she|they|we)\b").unwrap());
static FIRST_PERSON_SUB_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^(?:i|you|he|she|they|we)\b").unwrap());
static CAP_NAME_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b[A-Z][a-z]{2,}\b").unwrap());
static DECISION_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(ADD|DUPLICATE|UPDATE|CONTRADICT)\b").unwrap());

impl MemoryExtractor {
    pub fn new(store: Arc<ChatMemoryStore>, llm: Arc<dyn LlmClient>, config: ExtractionConfig) -> Self {
        MemoryExtractor { store, llm, config }
    }

    fn roles(&self) -> Vec<String> {
        self.config.ignore_speakers.iter().map(|r| r.to_lowercase()).collect()
    }

    fn is_assistant(&self, m: &ChatMessage) -> bool {
        let roles = self.roles();
        roles.contains(&m.role.to_lowercase()) || roles.contains(&m.speaker.to_lowercase())
    }

    fn ignored(&self, id: &str) -> bool {
        self.config.ignore_speakers.iter().any(|s| *s == id.to_lowercase())
    }

    /// `attempt` (1-based) comes from the job queue: retries get a repair instruction and more output room, so a
    /// failed prompt is never re-sent byte-identical at temperature 0.
    pub async fn build_plan(&self, batch: &[ChatMessage], job_ids: &[i64], attempt: i64) -> Result<ExtractionPlan> {
        let cfg = &self.config;
        let first = batch.first().ok_or_else(|| MemoryError::Other("AssertionError", "empty batch".into()))?;
        let chat_id = first.chat_id.clone();
        let mut plan = ExtractionPlan::new(&chat_id, job_ids);
        let names = cfg.speaker_names.clone();

        // everything the model sees as NEW MESSAGES (incl. assistant turns)
        let mut worthy: Vec<ChatMessage> = Vec::new();
        for m in batch {
            if cfg.gate_chitchat && is_probable_chitchat(&m.text) {
                plan.skipped_message_ids.push(m.message_id.clone());
                plan.audit.push((
                    "gate_skip".into(),
                    Some(m.message_id.clone()),
                    obj([("reason", Value::from("chitchat")), ("text", Value::String(clip(&m.text, 80)))]),
                ));
            } else {
                worthy.push(m.clone());
            }
        }
        let has_target = worthy.iter().any(|m| cfg.extract_assistant || !self.is_assistant(m));
        if !has_target {
            // only assistant/system turns (or nothing) left: no LLM call, keep them as processed context
            plan.processed_message_ids.extend(worthy.iter().map(|m| m.message_id.clone()));
            if !worthy.is_empty() {
                plan.audit.push((
                    "gate_skip".into(),
                    Some(chat_id.clone()),
                    obj([("reason", Value::from("assistant_only")), ("messages", Value::from(worthy.len()))]),
                ));
            }
            return Ok(plan);
        }

        let context = self.store.context_before(&chat_id, worthy[0].seq, cfg.context_messages)?;
        let speakers: Vec<String> = {
            let set: IndexSet<String> =
                context.iter().map(|m| m.speaker.clone()).chain(batch.iter().map(|m| m.speaker.clone())).collect();
            set.into_iter().collect()
        };
        let mut registry = EntityRegistry::load(&self.store, &chat_id, &speakers, &names, 120)?;
        let speaker_ids: IndexMap<String, String> = speakers
            .iter()
            .map(|sp| (sp.clone(), norm_entity(names.get(sp).map(String::as_str).unwrap_or(sp))))
            .collect();
        let human_speakers: Vec<String> =
            speakers.iter().filter(|sp| !cfg.ignore_speakers.contains(&sp.to_lowercase())).cloned().collect();

        let known = self.known_memories(&speaker_ids, &human_speakers)?;
        let last_sent = worthy.last().and_then(|m| m.sent_at.clone()).filter(|s| !s.is_empty());
        let first_sent = worthy[0].sent_at.clone().filter(|s| !s.is_empty());
        let date = py_prefix(&last_sent.or(first_sent).unwrap_or_else(now_iso), 10).to_string();
        let roles = self.roles();
        let msg_chars = if cfg.extract_assistant {
            cfg.max_message_chars.max(cfg.max_assistant_message_chars)
        } else {
            cfg.max_message_chars
        };
        let ctx_txt = fmt_messages(&context, &names, cfg.max_message_chars, false, &roles, false);
        let new_txt = fmt_messages(&worthy, &names, msg_chars, true, &roles, cfg.annotate_relative_dates);
        let repair = if attempt > 1 { py_format(prompts::REPAIR_SUFFIX, &[]) } else { String::new() };
        let max_tokens = (cfg.generate_max_tokens as f64 * (1.0 + 0.5 * ((attempt - 1).min(3) as f64))) as i64;
        let display = |s: &String| names.get(s).cloned().unwrap_or_else(|| s.clone());
        let speakers_list = speakers.iter().map(display).collect::<Vec<_>>();
        let kinds = MEMORY_KINDS.join(", ");
        let max_memories = cfg.max_memories_per_batch.to_string();
        let registry_text = registry.prompt_text(cfg.registry_prompt_entities);
        let known_text = if known.is_empty() {
            "(none)".to_string()
        } else {
            known.iter().map(|k| format!("- {k}")).collect::<Vec<_>>().join("\n")
        };
        let speakers_comma = if speakers_list.is_empty() { "unknown".to_string() } else { speakers_list.join(", ") };
        let mem_prompt = py_format(
            prompts::MEMORY_PROMPT,
            &[
                ("kinds", &kinds),
                ("max_memories", &max_memories),
                ("speakers", &speakers_comma),
                ("registry", &registry_text),
                ("known_memories", &known_text),
                ("context", &ctx_txt),
                ("messages", &new_txt),
                ("date", &date),
                (
                    "assistant_rule",
                    if cfg.extract_assistant { prompts::ASSISTANT_RULE_ON } else { prompts::ASSISTANT_RULE_OFF },
                ),
                (
                    "assistant_skip",
                    if cfg.extract_assistant { prompts::ASSISTANT_SKIP_ON } else { prompts::ASSISTANT_SKIP_OFF },
                ),
            ],
        ) + &repair;
        let mem_call = self.llm.generate(&mem_prompt, max_tokens, 0.0, cfg.llm_timeout);
        let (mem_out, rel_out) = if cfg.extract_relations {
            let speakers_and =
                if speakers_list.is_empty() { "unknown".to_string() } else { speakers_list.join(" and ") };
            let max_triples = cfg.max_triples_per_batch.to_string();
            let rel_prompt = py_format(
                prompts::RELATION_PROMPT,
                &[
                    ("speakers", &speakers_and),
                    ("max_triples", &max_triples),
                    ("registry", &registry_text),
                    ("context", &ctx_txt),
                    ("messages", &new_txt),
                    ("date", &date),
                ],
            ) + &repair;
            // both calls run to completion (like asyncio.gather); the first failure is reported
            let (a, b) = tokio::join!(mem_call, self.llm.generate(&rel_prompt, max_tokens, 0.0, cfg.llm_timeout));
            (a?, b?)
        } else {
            (mem_call.await?, String::new())
        };

        if parse_json_object(&mem_out).is_none() {
            // no JSON at all (prose, refusal, empty): let the queue retry with variation instead of losing the facts
            return Err(MemoryError::UnparseableOutput(format!(
                "memory extraction returned no JSON (attempt {attempt}): {}",
                py_repr_str(&clip(&mem_out, 160))
            )));
        }
        let candidates = self.parse_memories(&mem_out, &worthy);
        let mut pending_audit = Vec::new();
        let candidates =
            self.normalise(candidates, &worthy, &mut registry, &speaker_ids, &names, &mut pending_audit)?;
        plan.audit.extend(pending_audit);
        let mut candidates = self.ground(candidates, &worthy, &context, &registry, &names, &mut plan);
        plan.audit.push((
            "extract".into(),
            Some(chat_id.clone()),
            obj([
                ("messages", Value::from(worthy.iter().map(|m| m.message_id.clone()).collect::<Vec<_>>())),
                ("raw_candidates", Value::from(candidates.len())),
            ]),
        ));

        // relations (independent of memory reconciliation; provenance attached below)
        let (rel_entities, triples) = if rel_out.is_empty() {
            (Vec::new(), Vec::new())
        } else {
            self.parse_relations(&rel_out, &worthy, &mut registry, &speaker_ids, &context)
        };

        // reconcile candidates against the existing store
        self.reconcile(&mut candidates, &mut plan).await?;

        // entities: everything referenced by kept memories + relation endpoints
        let seen_at = worthy.last().and_then(|m| m.sent_at.clone()).filter(|s| !s.is_empty()).unwrap_or_else(now_iso);
        let mut ent_mentions: IndexMap<String, i64> = IndexMap::new();
        for (_, _, ent_ids) in &plan.new_memories {
            for e in ent_ids {
                *ent_mentions.entry(e.clone()).or_insert(0) += 1;
            }
        }
        for t in &triples {
            for e in [&t.0, &t.2] {
                *ent_mentions.entry(e.clone()).or_insert(0) += 1;
            }
        }
        for e in &rel_entities {
            ent_mentions.entry(e.clone()).or_insert(0);
        }
        for (eid, n) in &ent_mentions {
            plan.entities.push(PlanEntity {
                entity_id: eid.clone(),
                name: registry.display(eid),
                etype: registry.type_of(eid),
                seen_at: Some(seen_at.clone()),
                mentions: (*n).max(1),
                aliases: Vec::new(),
            });
        }
        // attach relation provenance: a memory that mentions both endpoints from the same source message
        let msg_time: IndexMap<String, String> = worthy
            .iter()
            .map(|m| {
                let t = m.sent_at.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| m.ingested_at.clone());
                (m.message_id.clone(), t)
            })
            .collect();
        for (s, p, o, conf, msg_id) in triples {
            let mut mem_id = None;
            for (mem, srcs, ent_ids) in &plan.new_memories {
                if ent_ids.contains(&s) && ent_ids.contains(&o) && (srcs.contains(&msg_id) || msg_id.is_empty()) {
                    mem_id = Some(mem.memory_id.clone());
                    break;
                }
            }
            plan.relations.push(PlanRelation {
                subject: s,
                predicate: p,
                object: o,
                confidence: conf,
                memory_id: mem_id,
                observed_at: msg_time.get(&msg_id).cloned(),
                message_id: Some(msg_id),
            });
        }

        plan.processed_message_ids.extend(worthy.iter().map(|m| m.message_id.clone()));
        Ok(plan)
    }

    /// Drop candidates the window does not support: assistant-only sources, hallucinated names, no overlap.
    ///
    /// Names are checked against what was known BEFORE this batch plus the window text: the model's own new entity
    /// list never vouches for a name it invented. Entities attached to a memory must occur in the window too. The
    /// lexical-overlap check is skipped for very short memories (identity facts like "Ali is 34").
    fn ground(
        &self,
        cands: Vec<CandidateMemory>,
        worthy: &[ChatMessage],
        context: &[ChatMessage],
        registry: &EntityRegistry,
        names: &IndexMap<String, String>,
        plan: &mut ExtractionPlan,
    ) -> Vec<CandidateMemory> {
        let cfg = &self.config;
        let window: Vec<&ChatMessage> = worthy.iter().chain(context.iter()).collect();
        let speaker_names: Vec<String> =
            window.iter().map(|m| names.get(&m.speaker).cloned().unwrap_or_else(|| m.speaker.clone())).collect();
        let mut parts: Vec<String> = window.iter().map(|m| m.text.clone()).collect();
        parts.extend(speaker_names.iter().cloned());
        let window_text = parts.join(" ");
        let window_tokens: IndexSet<String> = tokenize(&window_text).into_iter().collect();
        let window_lower = format!(" {} ", norm_entity(&window_text));
        let mut known_names: IndexSet<String> = IndexSet::new();
        for (eid, v) in &registry.entries {
            known_names.extend(norm_entity(&v.name).split(' ').filter(|s| !s.is_empty()).map(str::to_string));
            known_names.extend(eid.split(crate::pycompat::is_py_space).filter(|s| !s.is_empty()).map(str::to_string));
        }
        for a in registry.aliases.keys() {
            known_names.extend(a.split(crate::pycompat::is_py_space).filter(|s| !s.is_empty()).map(str::to_string));
        }
        for sp in names.values().chain(speaker_names.iter()) {
            known_names.extend(norm_entity(sp).split(' ').filter(|s| !s.is_empty()).map(str::to_string));
        }
        let mut kept = Vec::new();
        for mut c in cands {
            let srcs: Vec<&ChatMessage> =
                worthy.iter().filter(|m| c.source_message_ids.contains(&m.message_id)).collect();
            if !srcs.is_empty() && srcs.iter().all(|m| self.is_assistant(m)) && !cfg.extract_assistant {
                plan.audit.push((
                    "reject".into(),
                    None,
                    obj([("reason", Value::from("assistant_source")), ("text", Value::String(clip(&c.text, 100)))]),
                ));
                continue;
            }
            if cfg.require_name_grounding {
                let caps: Vec<&str> = CAP_NAME_RE.find_iter(&c.text).map(|m| m.as_str()).collect();
                let bad: Vec<&str> = caps
                    .into_iter()
                    .filter(|w| {
                        let n = norm_entity(w);
                        !known_names.contains(&n) && !window_lower.contains(&format!(" {n} "))
                    })
                    // allow ordinary sentence-initial words ("She", "The") and month names
                    .filter(|w| !COMMON_CAPS.contains(&w.to_lowercase().as_str()))
                    .collect();
                if !bad.is_empty() {
                    let names3: Vec<Value> = bad.iter().take(3).map(|w| Value::from(*w)).collect();
                    plan.audit.push((
                        "reject".into(),
                        None,
                        obj([
                            ("reason", Value::from("unknown_name")),
                            ("names", Value::Array(names3)),
                            ("text", Value::String(clip(&c.text, 100))),
                        ]),
                    ));
                    continue;
                }
                let subject_id = c.subject_id.clone();
                c.entity_ids.retain(|e| {
                    let toks = tokenize(e);
                    *e == subject_id
                        || registry.entries.contains_key(e)
                        || window_lower.contains(&format!(" {e} "))
                        || (!toks.is_empty() && toks.iter().all(|t| window_tokens.contains(t)))
                });
            }
            let subj_tokens: IndexSet<String> = tokenize(&c.subject_name).into_iter().collect();
            let toks: IndexSet<String> = tokenize(&c.text).into_iter().filter(|t| !subj_tokens.contains(t)).collect();
            if toks.len() >= 3 && cfg.min_grounding_overlap > 0.0 {
                let hit = toks.iter().filter(|t| window_tokens.contains(*t)).count();
                let overlap = hit as f64 / toks.len() as f64;
                if overlap < cfg.min_grounding_overlap {
                    plan.audit.push((
                        "reject".into(),
                        None,
                        obj([
                            ("reason", Value::from("ungrounded")),
                            ("overlap", fnum(py_round(overlap, 2))),
                            ("text", Value::String(clip(&c.text, 100))),
                        ]),
                    ));
                    continue;
                }
            }
            kept.push(c);
        }
        kept
    }

    fn known_memories(&self, speaker_ids: &IndexMap<String, String>, human_speakers: &[String]) -> Result<Vec<String>> {
        let mut out = Vec::new();
        let per = (self.config.known_memories_in_prompt / human_speakers.len().max(1)).max(3);
        for sp in human_speakers {
            let Some(sid) = speaker_ids.get(sp).filter(|s| !s.is_empty()) else { continue };
            let q = MemoryQuery {
                subject: Some(sid.clone()),
                limit: Some(per as i64),
                order: "importance DESC".into(),
                ..Default::default()
            };
            for m in self.store.list_memories(&q)? {
                out.push(clip(&m.text, 160));
            }
        }
        out.truncate(self.config.known_memories_in_prompt);
        Ok(out)
    }

    fn parse_memories(&self, raw: &str, worthy: &[ChatMessage]) -> Vec<CandidateMemory> {
        let obj_ = parse_json_object(raw).unwrap_or_default();
        let items = as_list(obj_.get("memories"));
        let mut out = Vec::new();
        for it in items {
            let Value::Object(it) = it else { continue };
            let text_v = crate::pycompat::py_or(&[it.get("text"), it.get("memory"), it.get("fact")]);
            let text = normalize_ws(&ANNOTATION_RE.replace_all(&as_str(text_v, 600), ""));
            if py_len(&text) < 8 {
                continue;
            }
            let mut kind = norm_entity(&as_str(it.get("kind"), 30));
            if kind.is_empty() {
                kind = "other".into();
            }
            if !MEMORY_KINDS.contains(&kind.as_str()) {
                kind = match kind.as_str() {
                    "facts" => "fact",
                    "events" => "event",
                    "plans" => "plan",
                    "preferences" => "preference",
                    "relation" | "relationships" => "relationship",
                    "identity" => "identity",
                    "goal" => "plan",
                    "habit" => "preference",
                    "belief" => "opinion",
                    "todo" => "task",
                    _ => "other",
                }
                .into();
            }
            let mut srcs: Vec<usize> = Vec::new();
            for s in as_list(crate::pycompat::py_or(&[it.get("sources"), it.get("source")])) {
                let Ok(i) = py_int_from_value(&s) else { continue };
                if i >= 1 && (i as usize) <= worthy.len() && !srcs.contains(&(i as usize)) {
                    srcs.push(i as usize);
                }
            }
            let mut ents = Vec::new();
            for e in as_list(it.get("entities")) {
                let (name, etype) = match &e {
                    Value::Object(o) => (as_str(o.get("name"), 80), as_str(o.get("type"), 30)),
                    other => (as_str(Some(other), 80), String::new()),
                };
                if !name.is_empty() {
                    ents.push((name, etype));
                }
            }
            out.push(CandidateMemory {
                text,
                kind,
                subject_raw: as_str(it.get("subject"), 80),
                importance: as_unit(it.get("importance"), 0.5),
                confidence: as_unit(it.get("confidence"), 0.8),
                when_raw: as_str(it.get("when"), 60),
                source_indices: srcs,
                entities: ents,
                subject_id: String::new(),
                subject_name: String::new(),
                speaker: String::new(),
                source_message_ids: Vec::new(),
                observed_at: String::new(),
                event_time: None,
                event_time_precision: "none".into(),
                entity_ids: Vec::new(),
                embedding: None,
                text_hash: String::new(),
            });
            if out.len() >= self.config.max_memories_per_batch {
                break;
            }
        }
        out
    }

    fn reject(
        audit: &mut Vec<(String, Option<String>, Value)>,
        c: &CandidateMemory,
        reason: &str,
        extra: Vec<(&str, Value)>,
    ) {
        let mut pairs = vec![("reason", Value::from(reason)), ("text", Value::String(clip(&c.text, 100)))];
        pairs.extend(extra);
        audit.push(("reject".into(), None, obj(pairs)));
    }

    fn normalise(
        &self,
        cands: Vec<CandidateMemory>,
        worthy: &[ChatMessage],
        registry: &mut EntityRegistry,
        speaker_ids: &IndexMap<String, String>,
        names: &IndexMap<String, String>,
        audit: &mut Vec<(String, Option<String>, Value)>,
    ) -> Result<Vec<CandidateMemory>> {
        let cfg = &self.config;
        let mut kept = Vec::new();
        let mut seen_hash: IndexSet<String> = IndexSet::new();
        for mut c in cands {
            if c.importance < cfg.min_importance || c.confidence < cfg.min_confidence {
                Self::reject(
                    audit,
                    &c,
                    "low_importance",
                    vec![("importance", fnum(c.importance)), ("confidence", fnum(c.confidence))],
                );
                continue;
            }
            if FIRST_PERSON_START_RE.is_match(&c.text.to_lowercase()) {
                // third-person rule violated; still usable if a subject is given, rewrite the pronoun
                if !c.subject_raw.is_empty() {
                    let subj = c.subject_raw.clone();
                    c.text = FIRST_PERSON_SUB_RE.replacen(&c.text, 1, regex::NoExpand(&subj)).into_owned();
                } else {
                    Self::reject(audit, &c, "first_person", vec![]);
                    continue;
                }
            }
            if !c.subject_raw.is_empty() {
                let s = norm_entity(&c.subject_raw);
                let assistantish = ["assistant", "ai", "bot", "system", "the assistant"];
                if cfg.ignore_speakers.contains(&s) || assistantish.contains(&s.as_str()) {
                    Self::reject(audit, &c, "assistant_subject", vec![]);
                    continue;
                }
            }
            let humans: Vec<&ChatMessage> = {
                let h: Vec<&ChatMessage> = worthy.iter().filter(|m| !self.is_assistant(m)).collect();
                if h.is_empty() {
                    worthy.iter().collect()
                } else {
                    h
                }
            };
            let last_human = *humans.last().expect("worthy is non-empty");
            let src_msgs: Vec<&ChatMessage> = if c.source_indices.is_empty() {
                vec![last_human]
            } else {
                c.source_indices.iter().map(|i| &worthy[i - 1]).collect()
            };
            let primary = src_msgs[0];
            c.speaker = primary.speaker.clone();
            c.source_message_ids = src_msgs.iter().map(|m| m.message_id.clone()).collect();
            c.observed_at =
                primary.sent_at.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| primary.ingested_at.clone());
            let speaker_eid = speaker_ids
                .get(&primary.speaker)
                .filter(|s| !s.is_empty())
                .cloned()
                .unwrap_or_else(|| norm_entity(&primary.speaker));
            // first-person subjects ("I", "the user") resolve to the *human* the memory is about: for a memory sourced
            // from an assistant turn that is the person it was said to, never the assistant itself
            let mut subject_speaker = speaker_eid.clone();
            if self.is_assistant(primary) && cfg.extract_assistant {
                let human_primary = src_msgs.iter().find(|m| !self.is_assistant(m)).copied().unwrap_or(last_human);
                subject_speaker =
                    speaker_ids.get(&human_primary.speaker).filter(|s| !s.is_empty()).cloned().unwrap_or_else(|| {
                        norm_entity(
                            names.get(&human_primary.speaker).map(String::as_str).unwrap_or(&human_primary.speaker),
                        )
                    });
            }
            let mut sid = if c.subject_raw.is_empty() {
                None
            } else {
                registry.resolve_str(&c.subject_raw, Some(&subject_speaker), "person", true)
            };
            if sid.as_deref().is_none_or(str::is_empty) {
                sid = Some(subject_speaker.clone());
            }
            let sid = sid.unwrap_or_default();
            if (!sid.is_empty() && self.ignored(&sid))
                || (self.is_assistant(primary) && sid == speaker_eid && !cfg.extract_assistant)
            {
                // a memory about the assistant itself is never stored
                Self::reject(audit, &c, "assistant_subject", vec![]);
                continue;
            }
            c.subject_id = sid.clone();
            c.subject_name = if registry.knows(&sid) {
                registry.display(&sid)
            } else if !c.subject_raw.is_empty() {
                c.subject_raw.clone()
            } else {
                names.get(&primary.speaker).cloned().unwrap_or_else(|| primary.speaker.clone())
            };
            let subject_name = c.subject_name.clone();
            registry.add(&sid, &subject_name, "person", 0, &[]);
            let (et, prec) = parse_when(&c.when_raw, Some(&c.observed_at));
            c.event_time = et;
            c.event_time_precision = prec;
            let mut ent_ids = vec![sid.clone()];
            for (name, etype) in &c.entities {
                if let Some(eid) = registry.resolve_str(name, Some(&speaker_eid), etype, true) {
                    if !eid.is_empty() && !ent_ids.contains(&eid) && !self.ignored(&eid) {
                        ent_ids.push(eid);
                    }
                }
            }
            c.entity_ids = ent_ids;
            c.text_hash = content_hash(&[&normalize_ws(&c.text).to_lowercase()]);
            if seen_hash.contains(&c.text_hash) {
                continue;
            }
            seen_hash.insert(c.text_hash.clone());
            kept.push(c);
        }
        Ok(kept)
    }

    #[allow(clippy::type_complexity)]
    fn parse_relations(
        &self,
        raw: &str,
        worthy: &[ChatMessage],
        registry: &mut EntityRegistry,
        speaker_ids: &IndexMap<String, String>,
        context: &[ChatMessage],
    ) -> (Vec<String>, Vec<(String, String, String, f64, String)>) {
        let cfg = &self.config;
        let o = parse_json_object(raw).unwrap_or_default();
        let joined: String = worthy.iter().chain(context.iter()).map(|m| m.text.as_str()).collect::<Vec<_>>().join(" ");
        let window_lower = format!(" {} ", norm_entity(&joined));
        let window_tokens: IndexSet<String> = tokenize(&joined).into_iter().collect();
        let known_before: IndexSet<String> = registry
            .entries
            .keys()
            .cloned()
            .chain(registry.aliases.keys().cloned())
            .chain(speaker_ids.values().cloned())
            .collect();

        // an endpoint must be a speaker/known entity or literally occur in the window
        let grounded = |mention: Option<&Value>, eid: &str| -> bool {
            if known_before.contains(eid) {
                return true;
            }
            let m = norm_entity(&as_str(mention, 80));
            if !m.is_empty() && window_lower.contains(&format!(" {m} ")) {
                return true;
            }
            let mt = tokenize(&m);
            !mt.is_empty() && mt.iter().all(|t| window_tokens.contains(t))
        };

        let mut ent_ids: Vec<String> = Vec::new();
        for e in as_list(o.get("entities")) {
            let (name, etype) = match &e {
                Value::Object(m) => (as_str(m.get("name"), 80), as_str(m.get("type"), 30)),
                other => (as_str(Some(other), 80), String::new()),
            };
            if name.is_empty() {
                continue;
            }
            let name_v = Value::String(name.clone());
            let mut eid = registry.resolve(Some(&name_v), None, &etype, false);
            if eid.is_none() && grounded(Some(&name_v), &norm_entity(&name)) {
                eid = registry.resolve(Some(&name_v), None, &etype, true);
            }
            if let Some(eid) = eid.filter(|e| !e.is_empty()) {
                if !ent_ids.contains(&eid) && !self.ignored(&eid) && grounded(Some(&name_v), &eid) {
                    ent_ids.push(eid);
                }
            }
        }
        let mut triples = Vec::new();
        let mut seen: IndexSet<(String, String, String)> = IndexSet::new();
        for t in as_list(o.get("triples")) {
            let (s, r, ob, src, conf) = match &t {
                Value::Object(m) => (
                    crate::pycompat::py_or(&[m.get("s"), m.get("subject")]).cloned(),
                    crate::pycompat::py_or(&[m.get("r"), m.get("relation"), m.get("predicate")]).cloned(),
                    crate::pycompat::py_or(&[m.get("o"), m.get("object")]).cloned(),
                    m.get("source").cloned(),
                    as_unit(m.get("confidence"), 0.7),
                ),
                Value::Array(a) if a.len() >= 3 => {
                    (Some(a[0].clone()), Some(a[1].clone()), Some(a[2].clone()), None, 0.7)
                }
                _ => continue,
            };
            let idx = match &src {
                None | Some(Value::Null) => None,
                Some(v) => py_int_from_value(v).ok(),
            };
            let humans: Vec<&ChatMessage> = {
                let h: Vec<&ChatMessage> = worthy.iter().filter(|m| !self.is_assistant(m)).collect();
                if h.is_empty() {
                    worthy.iter().collect()
                } else {
                    h
                }
            };
            let msg = match idx {
                Some(i) if i >= 1 && (i as usize) <= worthy.len() => &worthy[i as usize - 1],
                _ => *humans.last().expect("worthy is non-empty"),
            };
            let speaker_eid = speaker_ids
                .get(&msg.speaker)
                .filter(|s| !s.is_empty())
                .cloned()
                .unwrap_or_else(|| norm_entity(&msg.speaker));
            let mut sid = registry.resolve(s.as_ref(), Some(&speaker_eid), "", false);
            if sid.is_none() && grounded(s.as_ref(), &norm_entity(&as_str(s.as_ref(), 80))) {
                sid = registry.resolve(s.as_ref(), Some(&speaker_eid), "", true);
            }
            let mut oid = registry.resolve(ob.as_ref(), Some(&speaker_eid), "", false);
            if oid.is_none() && grounded(ob.as_ref(), &norm_entity(&as_str(ob.as_ref(), 80))) {
                oid = registry.resolve(ob.as_ref(), Some(&speaker_eid), "", true);
            }
            let pred = norm_relation(&as_str(r.as_ref(), 60));
            let (Some(sid), Some(oid)) = (sid.filter(|x| !x.is_empty()), oid.filter(|x| !x.is_empty())) else {
                continue;
            };
            if pred.is_empty() || sid == oid {
                continue;
            }
            if !grounded(s.as_ref(), &sid) || !grounded(ob.as_ref(), &oid) {
                continue;
            }
            if self.ignored(&sid) || self.ignored(&oid) {
                continue;
            }
            let key = (sid.clone(), pred.clone(), oid.clone());
            if seen.contains(&key) {
                continue;
            }
            seen.insert(key);
            triples.push((sid, pred, oid, conf, msg.message_id.clone()));
            if triples.len() >= cfg.max_triples_per_batch {
                break;
            }
        }
        (ent_ids, triples)
    }

    async fn reconcile(&self, cands: &mut [CandidateMemory], plan: &mut ExtractionPlan) -> Result<()> {
        let cfg = &self.config;
        if cands.is_empty() {
            return Ok(());
        }
        let texts: Vec<String> = cands.iter().map(|c| c.text.clone()).collect();
        let embs: Vec<Option<Vec<f64>>> = match self.llm.embed_many(&texts).await {
            Ok(v) => v.into_iter().map(Some).collect(),
            Err(exc) => {
                // embedding server down: keep going, maintenance back-fills vectors later
                tracing::warn!("embedding failed for {} candidates ({exc}); storing without vectors", cands.len());
                plan.audit.push((
                    "embed_failed".into(),
                    Some(plan.chat_id.clone()),
                    obj([
                        ("count", Value::from(cands.len())),
                        ("error", Value::String(py_prefix(&exc.message(), 200).to_string())),
                    ]),
                ));
                vec![None; cands.len()]
            }
        };
        for (c, e) in cands.iter_mut().zip(embs) {
            c.embedding = e.filter(|v| !v.is_empty());
        }
        // existing active memories per subject (with embeddings), fetched once per subject
        let mut by_subject: IndexMap<String, Vec<Memory>> = IndexMap::new();
        for c in cands.iter() {
            if !by_subject.contains_key(&c.subject_id) {
                let q = MemoryQuery {
                    subject: Some(c.subject_id.clone()),
                    limit: Some(2000),
                    order: "observed_at DESC".into(),
                    ..Default::default()
                };
                by_subject.insert(c.subject_id.clone(), self.store.list_memories(&q)?);
            }
        }

        // Phase 1 (in order, no LLM): exact and intra-batch duplicates, and the per-candidate shortlist.
        // Phase 2 (concurrent): the RECONCILE_PROMPT calls, which are independent of each other.
        // Phase 3 (in order): apply decisions, so same-target corrections still chain deterministically.
        let mut accepted: Vec<Option<Vec<f64>>> = Vec::new();
        let mut pending: Vec<Pending> = Vec::new();
        for c in cands.iter() {
            // exact text duplicate anywhere
            if let Some(exact) = self.store.find_active_by_text_hash(&c.text_hash)? {
                plan.duplicate_sources.push((exact.memory_id.clone(), c.source_message_ids.clone()));
                plan.audit.push((
                    "duplicate".into(),
                    Some(exact.memory_id.clone()),
                    obj([("text", Value::String(clip(&c.text, 120))), ("how", Value::from("exact"))]),
                ));
                continue;
            }
            // intra-batch near-duplicate (against everything not already dropped as a duplicate)
            if accepted.iter().any(|a| cos(c.embedding.as_deref(), a.as_deref()) >= cfg.dedupe_cosine) {
                plan.audit.push((
                    "duplicate".into(),
                    None,
                    obj([("text", Value::String(clip(&c.text, 120))), ("how", Value::from("intra_batch"))]),
                ));
                continue;
            }
            let pool = by_subject.get(&c.subject_id).cloned().unwrap_or_default();
            let top: Vec<(f64, Memory)> = if c.embedding.is_some() {
                let mut sims: Vec<(f64, Memory)> = pool
                    .iter()
                    .filter(|m| m.embedding.as_ref().is_some_and(|e| !e.is_empty()))
                    .map(|m| (cos(c.embedding.as_deref(), m.embedding.as_deref()), m.clone()))
                    .collect();
                sims.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
                let floor = cfg.related_cosine.min(cfg.reconcile_cosine);
                sims.into_iter().take(cfg.reconcile_candidates).filter(|(s, _)| *s >= floor).collect()
            } else {
                // no vector: use keyword overlap among the subject's memories as the pre-filter
                let by_id: IndexMap<&str, &Memory> = pool.iter().map(|m| (m.memory_id.as_str(), m)).collect();
                let kw = self.store.keyword_candidates(
                    &c.text,
                    (cfg.reconcile_candidates * 4) as i64,
                    "active",
                    &Default::default(),
                )?;
                kw.iter()
                    .filter_map(|(mid, _)| by_id.get(mid.as_str()).map(|m| (cfg.reconcile_cosine, (*m).clone())))
                    .take(cfg.reconcile_candidates)
                    .collect()
            };
            match top.first().map(|t| t.0) {
                Some(s) if s >= cfg.dedupe_cosine => {
                    let target = top[0].1.clone();
                    pending.push((c.clone(), top, "DUPLICATE", Some(target)));
                }
                Some(s) if s >= cfg.reconcile_cosine => {
                    pending.push((c.clone(), top, "ASK", None));
                    accepted.push(c.embedding.clone());
                }
                _ => {
                    pending.push((c.clone(), top, "ADD", None));
                    accepted.push(c.embedding.clone());
                }
            }
        }

        let asks: Vec<usize> = pending.iter().enumerate().filter(|(_, p)| p.2 == "ASK").map(|(i, _)| i).collect();
        let answers = futures::future::join_all(asks.iter().map(|&i| {
            let (c, top, _, _) = &pending[i];
            let existing: Vec<Memory> = top.iter().map(|(_, m)| m.clone()).collect();
            self.ask_reconcile(c, existing)
        }))
        .await;
        let resolved: IndexMap<usize, Decision> = asks.into_iter().zip(answers).collect();

        // target id -> the new memory that replaced it in this plan
        let mut superseded_in_plan: IndexMap<String, Memory> = IndexMap::new();
        for (i, (c, top, d, target)) in pending.into_iter().enumerate() {
            let (decision, mut target, merged_text) = if d == "ASK" {
                resolved.get(&i).cloned().unwrap_or(("ADD".into(), None, None))
            } else {
                (d.to_string(), target, None)
            };
            if decision == "UPDATE" || decision == "CONTRADICT" {
                if let Some(t) = &target {
                    if let Some(replacement) = superseded_in_plan.get(&t.memory_id) {
                        // an earlier candidate of this batch already replaced that target: chain onto the replacement
                        target = Some(replacement.clone());
                    }
                }
            }
            self.apply_decision(&c, &decision, target, merged_text, &top, plan, &mut superseded_in_plan).await?;
        }
        Ok(())
    }

    async fn ask_reconcile(&self, c: &CandidateMemory, existing: Vec<Memory>) -> Decision {
        const LETTERS: &str = "ABCDEFGH";
        let listing = existing
            .iter()
            .enumerate()
            .map(|(i, m)| {
                format!(
                    "{}. (observed {}) \"{}\"",
                    LETTERS.chars().nth(i).unwrap_or('?'),
                    py_prefix(&m.observed_at, 10),
                    clip(&m.text, 300)
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        let new_date = py_prefix(&c.observed_at, 10).to_string();
        let new_text = clip(&c.text, 400);
        let prompt = py_format(
            prompts::RECONCILE_PROMPT,
            &[("new_date", &new_date), ("new_text", &new_text), ("existing", &listing)],
        );
        let raw = match self.llm.generate(&prompt, self.config.reconcile_max_tokens, 0.0, self.config.llm_timeout).await
        {
            Ok(r) => r,
            Err(exc) => {
                // LLM failure: fall back to ADD (dedupe already handled the obvious case)
                tracing::warn!("reconcile call failed, defaulting to ADD: {exc}");
                return ("ADD".into(), None, None);
            }
        };
        let o = parse_json_object(&raw).unwrap_or_default();
        let mut decision = py_strip(&as_str(o.get("decision"), 20).to_uppercase()).to_string();
        if !matches!(decision.as_str(), "ADD" | "DUPLICATE" | "UPDATE" | "CONTRADICT") {
            decision =
                DECISION_RE.captures(&raw.to_uppercase()).map(|m| m[1].to_string()).unwrap_or_else(|| "ADD".into());
        }
        let mut target = None;
        let t_upper = as_str(o.get("target"), 5).to_uppercase();
        let t = py_strip(&t_upper).trim_end_matches('.');
        if let Some(first) = t.chars().next() {
            let allowed: Vec<char> = LETTERS.chars().take(existing.len()).collect();
            if let Some(pos) = allowed.iter().position(|l| *l == first) {
                target = Some(existing[pos].clone());
            }
        }
        if decision != "ADD" && target.is_none() {
            target = existing.first().cloned();
        }
        let mut text = Some(normalize_ws(&as_str(o.get("text"), 600))).filter(|t| !t.is_empty());
        if decision == "UPDATE" && text.as_ref().is_none_or(|t| py_len(t) < 8) {
            text = Some(c.text.clone());
        }
        (decision, target, text)
    }

    #[allow(clippy::too_many_arguments)]
    async fn apply_decision(
        &self,
        c: &CandidateMemory,
        decision: &str,
        target: Option<Memory>,
        merged_text: Option<String>,
        top: &[(f64, Memory)],
        plan: &mut ExtractionPlan,
        superseded_in_plan: &mut IndexMap<String, Memory>,
    ) -> Result<()> {
        let cfg = &self.config;
        let now = now_iso();
        if decision == "DUPLICATE" {
            if let Some(t) = &target {
                plan.duplicate_sources.push((t.memory_id.clone(), c.source_message_ids.clone()));
                plan.audit.push((
                    "duplicate".into(),
                    Some(t.memory_id.clone()),
                    obj([("text", Value::String(clip(&c.text, 120))), ("how", Value::from("reconcile"))]),
                ));
                return Ok(());
            }
        }
        let mut text = c.text.clone();
        let mut version = 1;
        let mut status = "active".to_string();
        let mut superseded_by = None;
        let (mut importance, mut kind, mut event_time, mut precision) =
            (c.importance, c.kind.clone(), c.event_time.clone(), c.event_time_precision.clone());
        let mut sources = c.source_message_ids.clone();
        let mut entity_ids = c.entity_ids.clone();
        let mut embedding = c.embedding.clone();
        let replaces = matches!(decision, "UPDATE" | "CONTRADICT");
        if let (true, Some(t)) = (replaces, &target) {
            version = t.version + 1;
            if decision == "UPDATE" {
                if let Some(m) = &merged_text {
                    text = m.clone();
                }
                // a refinement keeps everything the old memory had that the new sentence does not restate
                importance = importance.max(t.importance);
                if event_time.is_none() && t.event_time.as_deref().is_some_and(|e| !e.is_empty()) {
                    event_time = t.event_time.clone();
                    precision = t.event_time_precision.clone();
                }
                if kind == "other" {
                    kind = t.kind.clone();
                }
                if let Some(full) = self.store.get_memory(&t.memory_id, true)? {
                    let s: IndexSet<String> = sources.into_iter().chain(full.source_message_ids).collect();
                    sources = s.into_iter().collect();
                    let e: IndexSet<String> = entity_ids.into_iter().chain(full.entity_ids).collect();
                    entity_ids = e.into_iter().collect();
                }
                if text != c.text {
                    embedding = match self.llm.embed(&text).await {
                        Ok(v) => Some(v),
                        Err(_) => c.embedding.clone(),
                    };
                }
            }
            // temporal order: a candidate observed EARLIER than the existing memory cannot supersede it
            if older(Some(&c.observed_at), Some(&t.observed_at)) {
                status = "superseded".into();
                superseded_by = Some(t.memory_id.clone());
            }
        }
        let observed = if c.observed_at.is_empty() { now.clone() } else { c.observed_at.clone() };
        let mut mem = Memory::new(
            new_id("mem"),
            text.clone(),
            kind,
            c.subject_id.clone(),
            c.subject_name.clone(),
            c.speaker.clone(),
            plan.chat_id.clone(),
            importance,
            c.confidence,
            observed,
            now.clone(),
            now.clone(),
        );
        mem.event_time = event_time;
        mem.event_time_precision = precision;
        mem.status = status.clone();
        mem.superseded_by = superseded_by;
        mem.version = version;
        mem.text_hash = content_hash(&[&normalize_ws(&text).to_lowercase()]);
        mem.embedding = embedding;
        mem.metadata = obj([
            ("decision", Value::from(decision)),
            ("source_kind", Value::from("chat")),
            ("when_raw", if c.when_raw.is_empty() { Value::Null } else { Value::String(c.when_raw.clone()) }),
        ]);
        let mem_id = mem.memory_id.clone();
        plan.new_memories.push((mem.clone(), sources, entity_ids));
        match (replaces, &target) {
            (true, Some(t)) => {
                let link = if decision == "UPDATE" { "supersedes" } else { "contradicts" };
                if status == "active" {
                    plan.supersedes.push((t.memory_id.clone(), mem_id.clone(), link.into()));
                    superseded_in_plan.insert(t.memory_id.clone(), mem);
                } else {
                    // older observation: link for history only, existing memory stays current
                    plan.links.push((t.memory_id.clone(), mem_id.clone(), link.into(), 1.0));
                }
                plan.audit.push((
                    decision.to_lowercase(),
                    Some(mem_id.clone()),
                    obj([("target", Value::String(t.memory_id.clone())), ("text", Value::String(clip(&text, 120)))]),
                ));
            }
            _ => {
                plan.audit.push(("add".into(), Some(mem_id.clone()), obj([("text", Value::String(clip(&text, 120)))])));
            }
        }
        // related links to the nearest neighbours that were not superseded
        let mut n = 0;
        for (sim, m) in top {
            if target.as_ref().is_some_and(|t| t.memory_id == m.memory_id) {
                continue;
            }
            if *sim >= cfg.related_cosine && n < cfg.max_related_links {
                plan.links.push((mem_id.clone(), m.memory_id.clone(), "related".into(), py_round(*sim, 4)));
                n += 1;
            }
        }
        Ok(())
    }
}

/// `repr()` of a float, for tests that format scores.
pub fn float_repr(x: f64) -> String {
    py_float_repr(x)
}
