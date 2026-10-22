//! Port of `retrieval/service.py`.
//!
//! This file holds two independent parts: first the module-level parsing helpers at the end of the Python file
//! (`extract_keywords` onwards, shared with the benchmark harnesses), then the `NeuralGraphService` class and its
//! configuration, below the `NeuralGraphService` boundary comment (its own notes are there).
//!
//! Notes on the helpers:
//! - Every regular expression is compiled from the Python pattern by [`crate::pyre::compile`], so `\b`, `\w` and
//!   `\s` keep Python's definitions (they differ from Rust's around the zero-width joiners and variation selectors
//!   in emoji, which LoCoMo memories contain).
//! - Memories are the harnesses' dicts (`{"text": ..., "metadata": {...}}`) as JSON objects. Where the Python
//!   called `.lower()` on a `text` that was not a string it raised; that panics here with the same message.
//! - Functions that embedded candidates (`get_embedding` from `answering.py`) take an [`Llm`] and the embedding
//!   options the `AnsweringConfig` would have given (`None` = the defaults); a failed embedding is an empty
//!   vector, as in Python.
//! - `extract_topic_keywords_universal` comes from `tesseract.py`, as the Python imported it.

use crate::llm_backend::{EmbedOptions, Llm};
use crate::numpy::{norm_f32, sdot, to_f32};
use crate::pyrandom::PyRandom;
use crate::pyre::{compile, findall, findall1, search};
use crate::pytime::{total_seconds, PyDateTime};
use crate::retrieval::consolidation::{ConsolidationConfig, ConsolidationManager};
use crate::retrieval::data_types::{
    cosine_similarity, edge_ref, generate_edge_id, node_ref, ConsolidationResult, EdgeRef, EdgeType, NeuralEdge,
    NeuralNode, NodeLayer, NodeRef, RetrievalResult,
};
use crate::retrieval::dialogue_linker::{create_dialogue_links, DialogueLinker, DialogueLinkingConfig};
use crate::retrieval::electron::{ElectronRetriever, ElectronRetrieverConfig};
use crate::retrieval::external_retriever::{
    get_open_domain_tracer, is_open_domain_query, ExternalResult, ExternalRetrieverRegistry, OpenDomainTrace,
    WikipediaConfig, WikipediaRetriever,
};
use crate::retrieval::flash_retriever::{FlashConfig, HybridFlashRetriever};
use crate::retrieval::gating::{
    create_balanced_registry, create_permissive_registry, create_strict_registry, EdgeGateRegistry, GateRegistry,
};
use crate::retrieval::hierarchy::{EngineError, HierarchyConfig, HierarchyManager, MessageNodeArgs};
use crate::retrieval::query_router::{FilteredRetriever, QueryAnalysis, QueryType, SoftResults};
use crate::retrieval::retriever::{
    NeuralRetriever, RetrieverConfig, RetrieverError, ScoredTemporalContext, WaveAmplitudes,
};
use crate::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use crate::retrieval::temporal::{TemporalChainManager, TemporalConfig};
use crate::retrieval::tesseract::extract_topic_keywords_universal;
use crate::temporal_utils::generate_temporal_fields;
use fancy_regex::Regex;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_prefix, py_split_whitespace, py_str, py_strip, py_strip_chars, py_truthy};
use neuralgraph_memory::temporal::{parse_datetime_flexible, MONTH_NAMES};
use serde_json::{json, Map, Value};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::LazyLock;
use std::time::{Duration, Instant};
use tokio::sync::Semaphore;
use tokio::task::JoinHandle;

macro_rules! py_re {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Regex> = LazyLock::new(|| compile($pat, false));
    };
    ($name:ident, $pat:expr, ignore_case) => {
        static $name: LazyLock<Regex> = LazyLock::new(|| compile($pat, true));
    };
}

py_re!(KEYWORD_RE, r"\b[a-z]{3,}\b");
py_re!(QUOTE_SPACE_RE, r#"[\s"']+"#);
py_re!(NON_ITEM_RE, r"[^\w\s\-]");
py_re!(LEADING_DETERMINER_RE, r"^(?:the|a|an|my|your|our|their|his|her|some|any|each|every|another)\s+", ignore_case);
py_re!(TRAILING_FILLER_RE, r"\b(?:too|as well|recently|lately)\b$", ignore_case);
py_re!(YEAR_RE, r"\b(19|20)\d{2}\b");
py_re!(COUNT_RE, r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(kids|children|sons|daughters)\b");
py_re!(DOUBLE_QUOTED_RE, r#""([^"]+)""#);
py_re!(SINGLE_QUOTED_RE, r"'([^']+)'");
py_re!(CAP_PHRASE_RE, r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b");
py_re!(NAMED_RE, r"\bnamed\s+([A-Z][a-z]+)\b");
py_re!(CALLED_RE, r"\bcalled\s+([A-Z][a-z]+)\b");
py_re!(
    PREPOSITION_RE,
    r"\b(?:in|at|from|to|into|on|near|around|over|under|within|across)\s+([A-Za-z][\w'\-]*(?:\s+[A-Za-z][\w'\-]*){0,3})"
);
py_re!(CAP_WORD_RE, r"\b[A-Z][a-z]+\b");
py_re!(VERB_OBJECT_RE, r"\b(?:has|have|had|did|does)\s+\w+(?:'s)?\s+(\w+)\b");
py_re!(DO_LIKE_RE, r"\bdo\s+.+?\s+(like|love|enjoy|prefer)\b");
py_re!(SENTENCE_SPLIT_RE, r"[.!?;\n]+");
py_re!(CLAUSE_END_RE, r"[.!?;\n]");
py_re!(PART_SPLIT_RE, r",| and | & ");
py_re!(FLAG_RE, r"\b(?:\w+\s+){0,2}(?:flag|symbol)\b");
py_re!(MULTI_CAP_RE, r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b");
py_re!(ART_RE, r"\b(\w+(?:\s+\w+)?)\s+(art|painting|paintings)\b");
py_re!(PIC_OF_RE, r"\bpic of\s+([A-Z][a-z]+)\b", ignore_case);
py_re!(NAME_PAIR_RE, r"\b([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)\b");
py_re!(NAME_PHRASE_RE, r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b");
py_re!(TYPES_KINDS_RE, r"\b(types|kinds)\b");
py_re!(PLURAL_WORD_RE, r"\b[a-z]{3,}s\b");
py_re!(YEARS_RE, r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\b");

/// `re.sub(pattern, "", s)`
fn sub_empty(re: &Regex, s: &str) -> String {
    re.replace_all(s, "").into_owned()
}

/// `mem.get("text", "")` for the functions that called `.lower()` on it straight away.
fn lower_text(mem: &Value) -> String {
    match mem.get("text") {
        None => String::new(),
        Some(Value::String(s)) => s.to_lowercase(),
        Some(Value::Null) => panic!("AttributeError: 'NoneType' object has no attribute 'lower'"),
        Some(other) => panic!("AttributeError: object {other} has no attribute 'lower'"),
    }
}

/// `text = mem.get("text", ""); if not text: continue` — the text when truthy.
fn truthy_text(mem: &Value) -> Option<&str> {
    match mem.get("text") {
        Some(Value::String(s)) if !s.is_empty() => Some(s),
        Some(v) if py_truthy(v) => panic!("TypeError: expected string, got {v}"),
        _ => None,
    }
}

/// `extract_keywords`: lowercase words of three or more letters minus a few stopwords, in order (duplicates kept).
pub fn extract_keywords(text: &str) -> Vec<String> {
    const STOP: [&str; 14] =
        ["the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our"];
    findall1(&KEYWORD_RE, &text.to_lowercase()).into_iter().filter(|w| !STOP.contains(&w.as_str())).collect()
}

/// `_normalize_item`: quotes and whitespace runs to one space, then drop everything but word characters,
/// whitespace and hyphens.
pub fn normalize_item(item: &str) -> String {
    let spaced = QUOTE_SPACE_RE.replace_all(item, " ");
    let item = py_strip(&spaced);
    py_strip(&sub_empty(&NON_ITEM_RE, item)).to_string()
}

/// `_dedupe_items`: first occurrence of each case-insensitive value; empty ones dropped.
pub fn dedupe_items(items: &[String]) -> Vec<String> {
    let mut seen: IndexSet<String> = IndexSet::new();
    let mut result = Vec::new();
    for item in items {
        let norm = item.to_lowercase();
        if !norm.is_empty() && seen.insert(norm) {
            result.push(item.clone());
        }
    }
    result
}

/// `_clean_candidate`: drop a leading determiner and a trailing "too" / "as well" / "recently" / "lately".
pub fn clean_candidate(item: &str) -> String {
    let item = sub_empty(&LEADING_DETERMINER_RE, item);
    let item = sub_empty(&TRAILING_FILLER_RE, py_strip(&item));
    py_strip(&item).to_string()
}

/// `_is_bad_candidate`: empty, too long, conversational filler, only stopwords, or containing a generic verb.
pub fn is_bad_candidate(item: &str) -> bool {
    if item.is_empty() {
        return true;
    }
    let lower = item.to_lowercase();
    let words = py_split_whitespace(&lower);
    if words.len() > 4 {
        return true;
    }
    if words.len() == 1 {
        const GARBAGE: [&str; 69] = [
            "wow",
            "thanks",
            "thank",
            "hey",
            "hi",
            "hello",
            "bye",
            "goodbye",
            "oh",
            "ah",
            "uh",
            "um",
            "hm",
            "hmm",
            "yeah",
            "yep",
            "yes",
            "no",
            "nope",
            "ok",
            "okay",
            "sure",
            "right",
            "nice",
            "great",
            "good",
            "cool",
            "awesome",
            "amazing",
            "wonderful",
            "fantastic",
            "excellent",
            "perfect",
            "lovely",
            "glad",
            "happy",
            "sorry",
            "please",
            "well",
            "anyway",
            "actually",
            "definitely",
            "absolutely",
            "totally",
            "exactly",
            "basically",
            "honestly",
            "seriously",
            "literally",
            "probably",
            "maybe",
            "perhaps",
            "certainly",
            "obviously",
            "clearly",
            "really",
            "truly",
            "quite",
            "rather",
            "pretty",
            "look",
            "see",
            "check",
            "here",
            "yay",
            "yup",
            "nah",
            "haha",
            "lol",
        ];
        let single_word = words[0];
        if GARBAGE.contains(&single_word) {
            return true;
        }
        if single_word.chars().count() < 3 {
            return true;
        }
        if single_word.starts_with("s ") || single_word == "s" || single_word == "re" || single_word == "ve" {
            return true;
        }
    }
    const STOPWORDS: [&str; 59] = [
        "the", "a", "an", "my", "your", "our", "their", "his", "her", "some", "any", "this", "that", "these", "those",
        "just", "really", "very", "pretty", "kind", "sort", "type", "thing", "things", "stuff", "person", "people",
        "place", "places", "there", "here", "about", "with", "from", "into", "onto", "over", "under", "and", "or",
        "but", "so", "because", "after", "before", "since", "until", "when", "then", "i", "me", "we", "you", "he",
        "she", "they", "them", "it", "its",
    ];
    if words.iter().all(|w| STOPWORDS.contains(w)) {
        return true;
    }
    const BAD_VERBS: [&str; 35] = [
        "have", "has", "had", "got", "get", "getting", "made", "make", "making", "love", "like", "liked", "enjoy",
        "enjoyed", "want", "wanted", "need", "needed", "say", "said", "talk", "talked", "tell", "told", "think",
        "thought", "feel", "felt", "going", "went", "seen", "saw", "read", "watched", "visited",
    ];
    words.iter().any(|w| BAD_VERBS.contains(w))
}

/// `is_temporal_question`: does the question ask when / how long / how often?
pub fn is_temporal_question(question: &str) -> bool {
    static ALWAYS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
            r"\bwhen\b",
            r"\bwhat (date|time|year|month|day)\b",
            r"\bwhich (date|time|year|month|day)\b",
            r"\bin what (year|month|day)\b",
            r"\b(on|during)\s+which\s+(day|date|week|month|year|season)\b",
            r"\bwhat week\b",
            r"\bwhat season\b",
            r"\bhow often\b",
            r"\bhow long\b",
            r"\bhow old\b",
            r"\bhow many (years|months|weeks|days)\b",
            r"\b(age|duration)\b",
        ]
        .iter()
        .map(|p| compile(p, false))
        .collect()
    });
    static LATER: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
            r"\b(since|until|before|after|between|prior to|as of|ago)\b",
            r"\b(last|next|previous|this)\s+(year|month|week|day|summer|winter|spring|fall|autumn)\b",
            r"\b(yesterday|today|tomorrow|tonight)\b",
        ]
        .iter()
        .map(|p| compile(p, false))
        .collect()
    });
    py_re!(YEAR_ANY_RE, r"\b(19|20)\d{2}\b");
    py_re!(YEAR_PREP_RE, r"\b(in|during|before|after|since)\b");
    const NON_TEMPORAL_STARTERS: [&str; 21] = [
        "where did",
        "where does",
        "where is",
        "where was",
        "where has",
        "where have",
        "who did",
        "who does",
        "who is",
        "who was",
        "what did",
        "what does",
        "what has",
        "what have",
        "what was",
        "what is",
        "what are",
        "how did",
        "how does",
        "how has",
        "how was",
    ];

    let lower = question.to_lowercase();
    let q = py_strip(&lower);
    if ALWAYS.iter().any(|re| search(re, q)) {
        return true;
    }
    if NON_TEMPORAL_STARTERS.iter().any(|s| q.starts_with(s)) {
        return false;
    }
    if LATER.iter().any(|re| search(re, q)) {
        return true;
    }
    search(&YEAR_ANY_RE, q) && search(&YEAR_PREP_RE, q)
}

/// `extract_month_year_from_text`: the first month name (in calendar order) contained anywhere in the text — a
/// plain substring test, so "may" also matches "maybe" — and the first 19xx/20xx year.
pub fn extract_month_year_from_text(text: &str) -> (Option<u32>, Option<i64>) {
    if text.is_empty() {
        return (None, None);
    }
    let lower = text.to_lowercase();
    let month_num = MONTH_NAMES.iter().position(|m| lower.contains(m)).map(|i| i as u32 + 1);
    let year = YEAR_RE
        .find(text)
        .expect("regex backtrack limit")
        .map(|m| neuralgraph_memory::pycompat::py_int_parse(m.as_str()).expect("\\d{2} digits parse as int"));
    (month_num, year)
}

/// `extract_topic_keywords_for_temporal`: the question's words of three or more letters minus question,
/// function and time words (a set in Python; first-occurrence order here).
pub fn extract_topic_keywords_for_temporal(question: &str) -> IndexSet<String> {
    const STOP: [&str; 48] = [
        "when",
        "what",
        "which",
        "who",
        "where",
        "why",
        "how",
        "did",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
        "will",
        "would",
        "could",
        "should",
        "the",
        "a",
        "an",
        "to",
        "for",
        "in",
        "on",
        "at",
        "of",
        "with",
        "from",
        "by",
        "if",
        "before",
        "after",
        "since",
        "until",
        "ago",
        "last",
        "next",
        "this",
        "week",
        "month",
        "year",
        "day",
        "date",
        "time",
        "today",
        "yesterday",
        "tomorrow",
    ];
    findall1(&KEYWORD_RE, &question.to_lowercase()).into_iter().filter(|t| !STOP.contains(&t.as_str())).collect()
}

/// `extract_count_answer`: for "how many" questions, the number of kids/children/sons/daughters a memory states.
pub fn extract_count_answer(question: &str, memories: &[Value]) -> Option<String> {
    if !question.to_lowercase().contains("how many") {
        return None;
    }
    const NUMBER_WORDS: [&str; 10] = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
    for mem in memories {
        let text = lower_text(mem);
        if let Some(caps) = COUNT_RE.captures(&text).expect("regex backtrack limit") {
            let num = caps.get(1).unwrap().as_str();
            if let Some(i) = NUMBER_WORDS.iter().position(|w| *w == num) {
                return Some((i + 1).to_string());
            }
            // `\d+` matched, so `num.isdigit()`
            return Some(num.to_string());
        }
    }
    None
}

/// `extract_relationship_status`: the first status word (single, married, ...) found in a memory, capitalised.
pub fn extract_relationship_status(question: &str, memories: &[Value]) -> Option<String> {
    if !question.to_lowercase().contains("relationship status") {
        return None;
    }
    static TERMS: LazyLock<Vec<(&str, Regex)>> = LazyLock::new(|| {
        ["single", "married", "dating", "engaged", "divorced", "partner"]
            .into_iter()
            .map(|t| (t, compile(&format!(r"\b{t}\b"), false)))
            .collect()
    });
    for mem in memories {
        let text = lower_text(mem);
        for (term, re) in TERMS.iter() {
            if search(re, &text) {
                let mut c = term.chars();
                return Some(c.next().unwrap().to_uppercase().collect::<String>() + c.as_str());
            }
        }
    }
    None
}

/// `_extract_candidate_spans`: quoted strings, capitalised phrases, "named X" / "called X", and the phrase after
/// a preposition.
pub fn extract_candidate_spans(text: &str) -> Vec<String> {
    let mut candidates = Vec::new();
    if text.is_empty() {
        return candidates;
    }
    for re in [&*DOUBLE_QUOTED_RE, &*SINGLE_QUOTED_RE, &*CAP_PHRASE_RE, &*NAMED_RE, &*CALLED_RE, &*PREPOSITION_RE] {
        candidates.extend(findall1(re, text));
    }
    candidates
}

/// `_cosine_similarity`: cosine over the common prefix of two vectors (0 for empty or zero vectors).
pub fn cosine_similarity_prefix(vec_a: &[f64], vec_b: &[f64]) -> f64 {
    if vec_a.is_empty() || vec_b.is_empty() {
        return 0.0;
    }
    let length = vec_a.len().min(vec_b.len());
    let (mut dot, mut norm_a, mut norm_b) = (0.0, 0.0, 0.0);
    for i in 0..length {
        let (a, b) = (vec_a[i], vec_b[i]);
        dot += a * b;
        norm_a += a * a;
        norm_b += b * b;
    }
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    // `x ** 0.5` is C pow(), not sqrt()
    dot / (norm_a.powf(0.5) * norm_b.powf(0.5))
}

/// `get_embedding(session, text, config)` from `answering.py`: the embedding, or `[]` on any failure.
async fn get_embedding(llm: &dyn Llm, text: &str, opts: Option<&EmbedOptions>) -> Vec<f64> {
    let default = EmbedOptions::default();
    llm.embed(text, opts.unwrap_or(&default)).await.unwrap_or_default()
}

/// Capitalised words of the question other than question words, lowercased.
fn question_names(question: &str) -> IndexSet<String> {
    const QWORDS: [&str; 7] = ["What", "When", "Where", "Who", "Why", "How", "Which"];
    findall1(&CAP_WORD_RE, question)
        .into_iter()
        .filter(|n| !QWORDS.contains(&n.as_str()))
        .map(|n| n.to_lowercase())
        .collect()
}

/// `extract_span_answer_semantic`: the candidate span from the memories closest to the question embedding
/// (+0.02 for capitalised spans); the first candidate when there is no question embedding.
pub async fn extract_span_answer_semantic(
    llm: &dyn Llm,
    question: &str,
    question_embedding: &[f64],
    memories: &[Value],
    max_candidates: usize,
    embed_opts: Option<&EmbedOptions>,
) -> Option<String> {
    let qnames = question_names(question);
    const GENERIC: [&str; 17] = [
        "home",
        "home country",
        "hometown",
        "my country",
        "home town",
        "someone",
        "someone else",
        "somewhere",
        "some place",
        "place",
        "places",
        "city",
        "country",
        "town",
        "village",
        "region",
        "area",
    ];
    let mut candidates = Vec::new();
    for mem in memories {
        if let Some(text) = truthy_text(mem) {
            candidates.extend(extract_candidate_spans(text));
        }
    }
    let mut cleaned = Vec::new();
    for cand in &candidates {
        let normalized = clean_candidate(&normalize_item(cand));
        if normalized.is_empty() || is_bad_candidate(&normalized) {
            continue;
        }
        let lower = normalized.to_lowercase();
        if GENERIC.contains(&lower.as_str()) || (!qnames.is_empty() && qnames.contains(&lower)) {
            continue;
        }
        cleaned.push(normalized);
    }
    let mut candidates = dedupe_items(&cleaned);
    if candidates.is_empty() {
        return None;
    }
    if question_embedding.is_empty() {
        return candidates.into_iter().next();
    }
    candidates.truncate(max_candidates);
    let embeddings = futures::future::join_all(candidates.iter().map(|c| get_embedding(llm, c, embed_opts))).await;
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (cand, emb) in candidates.into_iter().zip(embeddings) {
        let mut score = cosine_similarity_prefix(question_embedding, &emb);
        if search(&CAP_WORD_RE, &cand) {
            score += 0.02;
        }
        scored.push((cand, score));
    }
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    scored.into_iter().next().map(|(c, _)| c)
}

/// `re.compile(rf"\b{re.escape(trig)}\w*\b")`, cached like Python's own pattern cache.
fn trigger_regex(trig: &str) -> Regex {
    static CACHE: LazyLock<std::sync::Mutex<std::collections::HashMap<String, Regex>>> =
        LazyLock::new(Default::default);
    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    cache
        .entry(trig.to_string())
        .or_insert_with(|| compile(&format!(r"\b{}\w*\b", fancy_regex::escape(trig)), false))
        .clone()
}

/// The items after `trig` in a lowercased sentence (`re.split(r",| and | & ", ...)` of the rest of its clause),
/// keeping parts of one to four words.
fn items_after(sent_lower: &str, trig: &str, items: &mut Vec<String>) {
    let mut after = "";
    if trig.contains(' ') {
        if let Some(idx) = sent_lower.find(trig) {
            after = &sent_lower[idx + trig.len()..];
        }
    } else {
        let re = trigger_regex(trig);
        if let Some(m) = re.find(sent_lower).expect("regex backtrack limit") {
            after = &sent_lower[m.end()..];
        }
    }
    if after.is_empty() {
        return;
    }
    let clause = CLAUSE_END_RE.split(after).next().map(|r| r.expect("regex backtrack limit")).unwrap_or("");
    push_parts(clause, items);
}

/// `for part in re.split(r",| and | & ", s): candidate = part.strip(...); if 1 <= len(candidate.split()) <= 4`
fn push_parts(s: &str, items: &mut Vec<String>) {
    for part in PART_SPLIT_RE.split(s) {
        let candidate = py_strip_chars(part.expect("regex backtrack limit"), " .;:!\"'()");
        let n = py_split_whitespace(candidate).len();
        if (1..=4).contains(&n) {
            items.push(candidate.to_string());
        }
    }
}

/// `extract_list_items`: list items the memories give for a "what X has Y" style question, from list
/// introducers ("such as", "including", ...), the question's own verbs, quotes, names and colon lists.
pub fn extract_list_items(
    question: &str,
    memories: &[Value],
    topic_keywords: Option<&IndexSet<String>>,
) -> Vec<String> {
    let q = question.to_lowercase();
    let mut items: Vec<String> = Vec::new();
    let mut triggers: Vec<String> = [
        "include",
        "includes",
        "including",
        "such as",
        "for example",
        "e.g.",
        "consist of",
        "consists of",
        "consisting of",
        "comprised of",
        "composed of",
        "feature",
        "features",
        "contain",
        "contains",
        "like",
        "love",
        "enjoy",
        "prefer",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let mut verb_matches: IndexSet<String> = findall1(&VERB_OBJECT_RE, &q).into_iter().collect();
    verb_matches.extend(findall1(&DO_LIKE_RE, &q));
    let mut sorted_verbs: Vec<String> = verb_matches.into_iter().collect();
    sorted_verbs.sort();
    triggers.extend(sorted_verbs);
    let extra: [&[&str]; 7] = [
        &["get", "got", "buy", "bought", "purchase", "purchased"],
        &["go to", "went to", "visit", "visited", "attend", "attended"],
        &["read"],
        &["watch", "watched", "see", "seen"],
        &["make", "made", "create", "created", "build", "built"],
        &["join", "joined", "participate", "participated in"],
        &["support", "supports", "help", "helps", "back", "backs"],
    ];
    static EXTRA_RES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
        [
            r"\b(buy|bought|purchase|purchased)\b",
            r"\b(visit|visited|attend|attended|go|went)\b",
            r"\b(read|reading|reads)\b",
            r"\b(watch|watched|see|seen)\b",
            r"\b(make|made|create|created|build|built)\b",
            r"\b(join|joined|participate|participated)\b",
            r"\b(support|supports|help|helps|back|backs)\b",
        ]
        .iter()
        .map(|p| compile(p, false))
        .collect()
    });
    for (words, re) in extra.iter().zip(EXTRA_RES.iter()) {
        if search(re, &q) {
            triggers.extend(words.iter().map(|w| w.to_string()));
        }
    }
    let triggers: Vec<String> = triggers.into_iter().collect::<IndexSet<String>>().into_iter().collect();
    let has_symbol = q.contains("symbol") || q.contains("flag");
    let has_artist = q.contains("artist") || q.contains("band");
    let has_art = q.contains("art");
    let mut universal: Option<IndexSet<String>> = None;

    for mem in memories {
        let Some(text) = truthy_text(mem) else { continue };
        for sentence in SENTENCE_SPLIT_RE.split(text) {
            let sentence = py_strip(sentence.expect("regex backtrack limit"));
            if sentence.is_empty() {
                continue;
            }
            let sent_lower = sentence.to_lowercase();
            if let Some(tk) = topic_keywords.filter(|t| !t.is_empty()) {
                if !tk.iter().any(|k| sent_lower.contains(k.as_str())) {
                    continue;
                }
            }
            for quoted in findall1(&DOUBLE_QUOTED_RE, sentence) {
                items.push(normalize_item(&quoted));
            }
            items.extend(findall1(&NAMED_RE, sentence));
            if has_symbol {
                items.extend(findall1(&FLAG_RE, &sent_lower));
            }
            if has_artist {
                items.extend(findall1(&MULTI_CAP_RE, sentence));
            }
            for trig in &triggers {
                items_after(&sent_lower, trig, &mut items);
            }
            if let Some((prefix, suffix)) = sentence.split_once(':') {
                let prefix_lower = prefix.to_lowercase();
                // Python recomputed the (pure) keyword set for every such sentence; once per call is the same
                let universal = universal.get_or_insert_with(|| extract_topic_keywords_universal(question));
                if q.is_empty() || universal.iter().any(|k| prefix_lower.contains(k.as_str())) {
                    push_parts(suffix, &mut items);
                }
            }
            if has_art {
                for g in findall(&ART_RE, &sent_lower) {
                    items.push(py_strip(&format!("{} {}", g[0], g[1])).to_string());
                }
            }
        }
    }
    let mut cleaned = Vec::new();
    for raw in &items {
        let normalized = clean_candidate(&normalize_item(raw));
        if !normalized.is_empty() && !is_bad_candidate(&normalized) {
            cleaned.push(normalized);
        }
    }
    let items: Vec<String> =
        cleaned.into_iter().filter(|i| !["melanie", "caroline"].contains(&i.to_lowercase().as_str())).collect();
    dedupe_items(&items)
}

/// `_extract_name_candidates`: "named X", "called X", "pic of X", "X and Y", and capitalised phrases.
pub fn extract_name_candidates(text: &str) -> Vec<String> {
    let mut candidates = Vec::new();
    candidates.extend(findall1(&NAMED_RE, text));
    candidates.extend(findall1(&CALLED_RE, text));
    candidates.extend(findall1(&PIC_OF_RE, text));
    for g in findall(&NAME_PAIR_RE, text) {
        candidates.extend(g);
    }
    candidates.extend(findall1(&NAME_PHRASE_RE, text).into_iter().filter(|m| !m.is_empty()));
    candidates
}

/// `extract_list_items_semantic`: list candidates from the memories that mention the question's topic (and
/// people), ranked by similarity to the question embedding; up to `max_items`.
pub async fn extract_list_items_semantic(
    llm: &dyn Llm,
    question: &str,
    question_embedding: &[f64],
    memories: &[Value],
    max_items: usize,
    embed_opts: Option<&EmbedOptions>,
) -> Vec<String> {
    let q = question.to_lowercase();
    let prefer_names = q.contains("name");
    let qnames = question_names(question);
    let topic_keywords = extract_topic_keywords_universal(question);

    let mut candidates: Vec<String> = Vec::new();
    for mem in memories {
        let empty = Value::Object(Default::default());
        let meta = mem.get("metadata").unwrap_or(&empty);
        let Some(text) = truthy_text(mem) else { continue };
        let text_lower = text.to_lowercase();
        if !topic_keywords.is_empty()
            && !prefer_names
            && !topic_keywords.iter().any(|k| text_lower.contains(k.as_str()))
        {
            continue;
        }
        if !qnames.is_empty() {
            let speaker_value = neuralgraph_memory::pycompat::py_or(&[meta.get("speaker"), meta.get("producer_id")]);
            let speaker = speaker_value.map(py_str).unwrap_or_default().to_lowercase();
            if !speaker.is_empty()
                && !qnames.contains(&speaker)
                && !qnames.iter().any(|n| text_lower.contains(n.as_str()))
            {
                continue;
            }
        }
        candidates.extend(findall1(&DOUBLE_QUOTED_RE, text));
        candidates.extend(findall1(&SINGLE_QUOTED_RE, text));
        if prefer_names {
            candidates.extend(extract_name_candidates(text));
        } else {
            let single = [serde_json::json!({"text": text})];
            candidates.extend(extract_list_items(question, &single, Some(&topic_keywords)));
            if search(&TYPES_KINDS_RE, &q) {
                candidates.extend(findall1(&PLURAL_WORD_RE, &text_lower));
            }
        }
    }
    let mut cleaned = Vec::new();
    for cand in &candidates {
        let normalized = clean_candidate(&normalize_item(cand));
        if !normalized.is_empty() && !is_bad_candidate(&normalized) {
            cleaned.push(normalized);
        }
    }
    let mut candidates = cleaned;
    if !qnames.is_empty() {
        candidates.retain(|c| !qnames.contains(&c.to_lowercase()));
    }
    if prefer_names {
        candidates.retain(|c| search(&CAP_WORD_RE, c));
    }
    candidates.retain(|c| !c.is_empty());
    let mut candidates = dedupe_items(&candidates);
    if candidates.is_empty() {
        return Vec::new();
    }
    if question_embedding.is_empty() {
        candidates.truncate(max_items);
        return candidates;
    }
    candidates.truncate(40);
    let embeddings = futures::future::join_all(candidates.iter().map(|c| get_embedding(llm, c, embed_opts))).await;
    let mut scored: Vec<(String, f64)> = Vec::new();
    for (cand, emb) in candidates.into_iter().zip(embeddings) {
        let mut score = cosine_similarity_prefix(question_embedding, &emb);
        if prefer_names && search(&CAP_WORD_RE, &cand) {
            score += 0.05;
        }
        scored.push((cand, score));
    }
    scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let mut filtered: Vec<String> = scored.iter().filter(|(_, s)| *s >= 0.2).map(|(c, _)| c.clone()).collect();
    if filtered.is_empty() {
        filtered = scored.iter().take(max_items).map(|(c, _)| c.clone()).collect();
    }
    let mut out = dedupe_items(&filtered);
    out.truncate(max_items);
    out
}

/// `extract_duration_answer`: "how long / how old" answers from duration metadata, else "<n> years" in the text.
pub fn extract_duration_answer(question: &str, memories: &[Value]) -> Option<String> {
    let q = question.to_lowercase();
    if !q.contains("how long") && !q.contains("how long ago") && !q.contains("how old") {
        return None;
    }
    let ago = q.contains("how long ago");
    for mem in memories {
        let empty = Value::Object(Default::default());
        let meta = mem.get("metadata").unwrap_or(&empty);
        let since_year = meta.get("since_year").filter(|v| py_truthy(v));
        let duration_years = meta.get("duration_years").filter(|v| py_truthy(v));
        if let (true, Some(since)) = (q.contains("since"), since_year) {
            return Some(format!("Since {}", py_str(since)));
        }
        if let Some(d) = duration_years {
            return Some(if ago { format!("{} years ago", py_str(d)) } else { format!("{} years", py_str(d)) });
        }
    }
    for mem in memories {
        let text = lower_text(mem);
        if let Some(caps) = YEARS_RE.captures(&text).expect("regex backtrack limit") {
            let n = caps.get(1).unwrap().as_str();
            return Some(if ago { format!("{n} years ago") } else { format!("{n} years") });
        }
    }
    None
}

// ---- NeuralGraphService (ported separately) ----
//
// Port of the first part of `retrieval/service.py`: `NeuralGraphServiceConfig`, the two preset configurations,
// `NeuralIngestionResult` and the `NeuralGraphService` class. What a reader needs to know:
//
// - Synchronous vs async. The Python methods were `async` because the storage was; nothing but the external
//   retrievers (HTTP) is really awaited, so only `retrieve`, `retrieve_with_context`, `stop_auto_consolidation`
//   and `close` are `async` here. Everything takes `&self` (counters, caches, the per-session linkers and the
//   retrievers use interior mutability) so several retrievals can be driven concurrently, as with `gather`.
// - Duck-typed inputs. The service read attributes of objects from the `memmachine` package, which is not part of
//   this repository (`Episode`, `MemoryWave` with its signals, `ExtractionResult` with its entities and
//   relations; only `TYPE_CHECKING` imports). They are plain structs here carrying exactly the attributes the
//   service touched; see the notes on each.
// - Randomness. `_apply_temporal_dynamics` called `random.random()` on the *global* `random` module. The service
//   owns a `PyRandom` instead (`with_rng`/`set_rng`/`seed_rng` inject or reseed it; `new` seeds it from the clock
//   like Python seeds from the OS). A Python process that called `random.seed(n)` and consumed nothing else from
//   `random` draws the same sequence as `seed_rng(n)`; the golden fixture pins it by giving the reference module
//   its own `random.Random(n)`.
// - Concurrency protection. Python guarded retrieval against a running consolidation with an `asyncio.Lock`, but
//   consolidation never suspended (the storage never yielded), so no other task could ever observe the lock held.
//   Consolidation is synchronous here and the lock is gone; the 10-slot retrieval semaphore is kept
//   (`tokio::sync::Semaphore`), which only matters while retrievals are suspended on an external source.
// - Auto-consolidation. `asyncio.create_task(loop)` becomes `tokio::task::spawn_local`: the engine types are
//   `!Send`, so `start_auto_consolidation` must be called from inside a `tokio::task::LocalSet`, and the loop
//   (sleep, then one cycle unless closed, errors logged and survived) runs on it while the caller awaits.
//   `stop_auto_consolidation` aborts the task and awaits its end.
//
// Python behaviour kept (all of it observable and pinned by `service.json`):
// - The gate profile chosen from the query type in `retrieve` was only ever logged: no retriever received it.
//   It is computed (`gate_profile_for_query_type`) and logged, nothing more.
// - `set_gate_strictness` replaced the service's registry and tried to update `retriever._gate_registry`, an
//   attribute the retriever does not have (it keeps its own `_gates`), so the strictness never reached the
//   retriever; `adapt_gate_threshold` asked the registry for an `adapt_threshold` method only `AdaptiveGate` has,
//   so it always just logged. Both are kept: the registry is stored and readable, and nothing uses it.
// - The `__suppress_multi_hop` flag was written into the caller's wave-amplitude dict when it was non-empty (and
//   into a fresh one otherwise): `retrieve` takes `Option<&mut WaveAmplitudes>` to keep that side effect.
// - A second `process_episodes` call replaces the session's dialogue linker with one built from the second call's
//   nodes only, and the filtered retriever of a session learns its known entities once, on its first query.
// - `process_episodes` counts only entity edges in `_total_edges_created`.
// - `_save_edge_with_fairness` updates the source node's edge counts although `save_edge` already did.
// - `_apply_temporal_dynamics` subtracts creation times outside any `try`: a naive node next to an aware one made
//   `retrieve` raise `TypeError`.

/// Configuration for [`NeuralGraphService`] (`NeuralGraphServiceConfig`).
///
/// The reranker, deduplication, retention, query-rewriting, attribution and latency settings are documented
/// "knobs" the Python service never read; they are kept so configurations round-trip.
#[derive(Clone, Debug)]
pub struct NeuralGraphServiceConfig {
    // core enable/disable flags
    pub enabled: bool,
    pub hierarchy_enabled: bool,
    pub temporal_enabled: bool,
    pub consolidation_enabled: bool,
    // auto-processing
    pub auto_consolidation_interval_seconds: f64,
    pub auto_temporal_linking: bool,
    pub auto_episode_segmentation: bool,
    pub auto_consolidation_on_ingestion: bool,
    /// STDP and LTP decay after retrieval.
    pub apply_temporal_dynamics: bool,
    /// Chance per retrieval that LTP decay runs.
    pub temporal_dynamics_probability: f64,
    // component configs
    pub hierarchy_config: Option<HierarchyConfig>,
    pub temporal_config: Option<TemporalConfig>,
    pub consolidation_config: Option<ConsolidationConfig>,
    pub retriever_config: Option<RetrieverConfig>,
    // retriever selection
    pub use_flash_retriever: bool,
    pub flash_config: Option<FlashConfig>,
    pub use_electron_retriever: bool,
    pub electron_config: Option<ElectronRetrieverConfig>,
    // dialogue linking
    pub dialogue_linking_enabled: bool,
    // query routing: "boost" (matches boosted), "soft" (non-matches penalised) or "hard" (non-matches removed)
    pub query_routing_enabled: bool,
    pub query_routing_mode: String,
    // single-hop optimisation knobs
    pub reranker_enabled: bool,
    pub reranker_type: String,
    pub reranker_top_k: i64,
    /// "single_hop", "multi_hop", "temporal" or "default".
    pub gate_profile: String,
    pub node_dedup_enabled: bool,
    pub node_dedup_threshold: f64,
    pub recency_retention_enabled: bool,
    pub temporal_consistency_enabled: bool,
    pub query_rewriting_enabled: bool,
    pub query_rewriting_num_rewrites: i64,
    // external retrieval for open-domain queries
    pub external_retrieval_enabled: bool,
    pub external_retrieval_confidence_threshold: f64,
    pub external_retrieval_min_session_hits: i64,
    pub external_retrieval_timeout_ms: f64,
    pub external_result_weight: f64,
    pub external_max_results: usize,
    pub wikipedia_enabled: bool,
    pub wikipedia_extract_chars: i64,
    pub open_domain_tracing_enabled: bool,
    // attribution and debugging
    pub attribution_logging_enabled: bool,
    pub attribution_log_path: Option<String>,
    pub attribution_buffer_size: i64,
    pub attribution_include_breakdowns: bool,
    // retrieval settings
    pub use_neural_retrieval: bool,
    pub neural_retrieval_weight: f64,
    // latency constraints
    pub max_retrieval_latency_ms: f64,
    pub max_rerank_latency_ms: f64,
    pub fallback_on_timeout: bool,
}

impl Default for NeuralGraphServiceConfig {
    fn default() -> Self {
        NeuralGraphServiceConfig {
            enabled: true,
            hierarchy_enabled: true,
            temporal_enabled: true,
            consolidation_enabled: true,
            auto_consolidation_interval_seconds: 300.0,
            auto_temporal_linking: true,
            auto_episode_segmentation: true,
            auto_consolidation_on_ingestion: true,
            apply_temporal_dynamics: true,
            temporal_dynamics_probability: 0.1,
            hierarchy_config: None,
            temporal_config: None,
            consolidation_config: None,
            retriever_config: None,
            use_flash_retriever: true,
            flash_config: None,
            use_electron_retriever: false,
            electron_config: None,
            dialogue_linking_enabled: true,
            query_routing_enabled: true,
            query_routing_mode: "boost".into(),
            reranker_enabled: true,
            reranker_type: "semantic".into(),
            reranker_top_k: 20,
            gate_profile: "single_hop".into(),
            node_dedup_enabled: true,
            node_dedup_threshold: 0.92,
            recency_retention_enabled: true,
            temporal_consistency_enabled: true,
            query_rewriting_enabled: false,
            query_rewriting_num_rewrites: 2,
            external_retrieval_enabled: false,
            external_retrieval_confidence_threshold: 0.5,
            external_retrieval_min_session_hits: 3,
            external_retrieval_timeout_ms: 2000.0,
            external_result_weight: 0.7,
            external_max_results: 5,
            wikipedia_enabled: false,
            wikipedia_extract_chars: 800,
            open_domain_tracing_enabled: false,
            attribution_logging_enabled: false,
            attribution_log_path: None,
            attribution_buffer_size: 10,
            attribution_include_breakdowns: true,
            use_neural_retrieval: true,
            neural_retrieval_weight: 0.5,
            max_retrieval_latency_ms: 200.0,
            max_rerank_latency_ms: 50.0,
            fallback_on_timeout: true,
        }
    }
}

/// `create_single_hop_optimized_config`: boost-mode routing, semantic reranking, the single-hop gate profile,
/// deduplication, retention and temporal consistency on, query rewriting off (these are the defaults anyway).
pub fn create_single_hop_optimized_config() -> NeuralGraphServiceConfig {
    NeuralGraphServiceConfig {
        query_routing_mode: "boost".into(),
        reranker_enabled: true,
        reranker_type: "semantic".into(),
        gate_profile: "single_hop".into(),
        node_dedup_enabled: true,
        recency_retention_enabled: true,
        temporal_consistency_enabled: true,
        query_rewriting_enabled: false,
        ..Default::default()
    }
}

/// `create_multi_hop_optimized_config`: soft-mode routing (keeps more candidates), wider reranking, the
/// multi-hop gate profile, a less aggressive deduplication threshold.
pub fn create_multi_hop_optimized_config() -> NeuralGraphServiceConfig {
    NeuralGraphServiceConfig {
        query_routing_mode: "soft".into(),
        reranker_enabled: true,
        reranker_type: "semantic".into(),
        reranker_top_k: 30,
        gate_profile: "multi_hop".into(),
        node_dedup_enabled: true,
        node_dedup_threshold: 0.95,
        temporal_consistency_enabled: true,
        ..Default::default()
    }
}

/// Result of neural memory ingestion (`NeuralIngestionResult`).
#[derive(Clone, Debug, Default)]
pub struct NeuralIngestionResult {
    pub nodes_created: usize,
    pub temporal_edges_created: usize,
    pub entity_edges_created: usize,
    pub semantic_edges_created: usize,
    pub processing_time_ms: f64,
    /// Messages of the exceptions Python collected (`str(e)`).
    pub errors: Vec<String>,
    /// Created message nodes, for the Sun linkage integration (episode nodes are counted, not listed).
    pub nodes: Vec<NodeRef>,
}

/// How an episode's `created_at` arrived: a `datetime`, or a string that is parsed with
/// `parse_datetime_flexible`. Any other Python type was ignored like `None`.
#[derive(Clone, Debug, PartialEq)]
pub enum EpisodeTime {
    DateTime(PyDateTime),
    Text(String),
}

/// The attributes `process_episodes` read from a `memmachine` `Episode`.
#[derive(Clone, Debug)]
pub struct Episode {
    pub uid: String,
    pub content: String,
    /// `getattr(episode, "created_at", None)`.
    pub created_at: Option<EpisodeTime>,
    /// `episode.filterable_metadata`; only its `entity_ids` list of strings is read (any other value there is
    /// treated as no entities, where Python would have stored it as is).
    pub filterable_metadata: Option<Map<String, Value>>,
    /// `getattr(episode, "producer_id", None)`: the speaker.
    pub producer_id: Option<String>,
    /// `getattr(episode, "importance_score", 0.5)`.
    pub importance_score: f64,
}

impl Episode {
    /// An episode with the attribute defaults (no timestamp, no metadata, no producer, importance 0.5).
    pub fn new(uid: impl Into<String>, content: impl Into<String>) -> Self {
        Episode {
            uid: uid.into(),
            content: content.into(),
            created_at: None,
            filterable_metadata: None,
            producer_id: None,
            importance_score: 0.5,
        }
    }
}

/// Kind of a wave signal (`signal.signal_type.value`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SignalType {
    Temporal,
    Entity,
    Relation,
    Causal,
    /// Any other `value`.
    Other(String),
}

impl SignalType {
    pub fn value(&self) -> &str {
        match self {
            SignalType::Temporal => "temporal",
            SignalType::Entity => "entity",
            SignalType::Relation => "relation",
            SignalType::Causal => "causal",
            SignalType::Other(v) => v,
        }
    }
}

/// One signal of a wave memory, with the attributes the service read from each kind.
#[derive(Clone, Debug)]
pub struct WaveSignal {
    pub signal_type: SignalType,
    /// Temporal signals: `raw_expression`.
    pub raw_expression: String,
    /// Temporal signals: `resolved_date` (stored as its `isoformat()`).
    pub resolved_date: Option<PyDateTime>,
    /// Entity signals: `entity_name` (empty names are skipped).
    pub entity_name: String,
    /// Causal signals: `getattr(signal, "cause", "")`.
    pub cause: String,
    /// Causal signals: `getattr(signal, "effect", "")`.
    pub effect: String,
    /// Causal signals: `getattr(signal, "amplitude", 0.5)`.
    pub amplitude: f64,
    /// Causal signals: the embedding used to find the effect node (`hasattr(signal, "embedding")` and truthy).
    pub embedding: Option<Vec<f64>>,
}

impl WaveSignal {
    /// A signal of the given kind with every attribute at its neutral value.
    pub fn new(signal_type: SignalType) -> Self {
        WaveSignal {
            signal_type,
            raw_expression: String::new(),
            resolved_date: None,
            entity_name: String::new(),
            cause: String::new(),
            effect: String::new(),
            amplitude: 0.5,
            embedding: None,
        }
    }
}

/// The attributes `process_wave_memory` read from a `memmachine` `MemoryWave`. `get_temporal_signals()` and its
/// siblings are the signals of that kind, in order (the package is not available to confirm; the service only
/// iterated the results).
#[derive(Clone, Debug)]
pub struct MemoryWave {
    pub wave_id: String,
    pub content: String,
    pub embedding: Option<Vec<f64>>,
    /// `wave.amplitudes.to_dict()`; `None` when the wave had no `amplitudes` attribute.
    pub amplitudes: Option<IndexMap<String, f64>>,
    pub signals: Vec<WaveSignal>,
}

impl MemoryWave {
    fn signals_of(&self, kind: SignalType) -> Vec<&WaveSignal> {
        self.signals.iter().filter(|s| s.signal_type == kind).collect()
    }

    pub fn get_temporal_signals(&self) -> Vec<&WaveSignal> {
        self.signals_of(SignalType::Temporal)
    }

    pub fn get_entity_signals(&self) -> Vec<&WaveSignal> {
        self.signals_of(SignalType::Entity)
    }

    pub fn get_relation_signals(&self) -> Vec<&WaveSignal> {
        self.signals_of(SignalType::Relation)
    }
}

/// A knowledge-graph entity (`Entity`): the attributes `process_extraction_result` read.
#[derive(Clone, Debug)]
pub struct Entity {
    pub uid: String,
    pub name: String,
    /// `entity.entity_type.value`
    pub entity_type: String,
    pub mekb_score: f64,
}

/// A knowledge-graph relation (`Relation`).
#[derive(Clone, Debug)]
pub struct Relation {
    pub uid: String,
    pub source_entity_uid: String,
    pub target_entity_uid: String,
    /// `relation.relation_type.value`
    pub relation_type: String,
    /// 0-10.
    pub weight: f64,
    pub confidence: f64,
}

/// A knowledge-graph extraction result (`ExtractionResult`).
#[derive(Clone, Debug)]
pub struct ExtractionResult {
    pub entities: Vec<Entity>,
    pub relations: Vec<Relation>,
    pub confidence: f64,
}

/// An exception that escaped a service call (the ones Python let propagate).
#[derive(Clone, Debug, PartialEq)]
pub enum ServiceError {
    Retriever(RetrieverError),
    Engine(EngineError),
}

impl std::fmt::Display for ServiceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ServiceError::Retriever(e) => e.fmt(f),
            ServiceError::Engine(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for ServiceError {}

impl From<RetrieverError> for ServiceError {
    fn from(e: RetrieverError) -> Self {
        ServiceError::Retriever(e)
    }
}

impl From<EngineError> for ServiceError {
    fn from(e: EngineError) -> Self {
        ServiceError::Engine(e)
    }
}

/// `str(exception)` of an [`EngineError`].
fn engine_message(e: &EngineError) -> &str {
    match e {
        EngineError::ValueError(m)
        | EngineError::TypeError(m)
        | EngineError::AttributeError(m)
        | EngineError::KeyError(m) => m,
    }
}

/// What [`NeuralGraphService::retrieve_with_context`] returns.
#[derive(Clone, Debug)]
pub struct RetrievalContext {
    pub nodes: Vec<(NodeRef, f64)>,
    pub stages_executed: Vec<String>,
    pub total_time_ms: f64,
    /// Predecessors and successors of the best node (`{}` when there were no results or no temporal manager).
    pub temporal_context: Option<ScoredTemporalContext>,
    /// Per-layer summary (`{}` without a hierarchy manager).
    pub hierarchy_summary: Option<Value>,
}

impl RetrievalContext {
    /// The dict Python returned.
    pub fn to_dict(&self) -> Value {
        let scored =
            |v: &[(NodeRef, f64)]| -> Vec<Value> { v.iter().map(|(n, s)| json!([n.borrow().to_dict(), s])).collect() };
        json!({
            "nodes": scored(&self.nodes),
            "stages_executed": self.stages_executed,
            "total_time_ms": self.total_time_ms,
            "temporal_context": match &self.temporal_context {
                Some(c) => json!({"before": scored(&c.before), "after": scored(&c.after)}),
                None => json!({}),
            },
            "hierarchy_summary": self.hierarchy_summary.clone().unwrap_or_else(|| json!({})),
        })
    }
}

/// The gate profile `retrieve` selected for a query type (`"single_hop"`, `"multi_hop"`, `"temporal"`,
/// `"open_domain"`), or the configured one for types without an override. It was only ever logged.
pub fn gate_profile_for_query_type(configured: &str, query_type: QueryType) -> String {
    match query_type {
        QueryType::EntityAttribute | QueryType::EntityAction | QueryType::EntityRelation => "single_hop",
        QueryType::MultiHop => "multi_hop",
        QueryType::TemporalWhen | QueryType::TemporalSequence | QueryType::TemporalDuration => "temporal",
        QueryType::OpenDomain => "open_domain",
        QueryType::Adversarial => configured,
    }
    .to_string()
}

/// A seed for the default random generator, from the clock (Python seeds `random` from the OS).
fn entropy_seed() -> i128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as i128).unwrap_or(0)
}

/// `Duration` for an `asyncio.sleep` argument (negative: no wait).
fn sleep_duration(seconds: f64) -> Duration {
    Duration::try_from_secs_f64(seconds.max(0.0)).unwrap_or(Duration::MAX)
}

/// High-level service coordinating the neural graph components (`NeuralGraphService`): hierarchy, temporal
/// chains, consolidation, the retrievers, dialogue linking, query routing and external retrieval.
pub struct NeuralGraphService {
    config: NeuralGraphServiceConfig,
    storage: Rc<dyn NeuralGraphStorage>,
    gate_registry: RefCell<Rc<EdgeGateRegistry>>,
    hierarchy: Option<Rc<HierarchyManager>>,
    temporal: Option<Rc<TemporalChainManager>>,
    consolidation: Option<Rc<ConsolidationManager>>,
    retriever: RefCell<NeuralRetriever>,
    flash_retriever: Option<HybridFlashRetriever>,
    electron_retriever: Option<RefCell<ElectronRetriever>>,
    /// Session key -> linker of the last `process_episodes` batch.
    dialogue_linkers: RefCell<IndexMap<String, DialogueLinker>>,
    /// Session key -> filtered retriever, created on the session's first query.
    filtered_retrievers: RefCell<IndexMap<String, FilteredRetriever<'static>>>,
    external_registry: Option<RefCell<ExternalRetrieverRegistry>>,
    /// The Wikipedia source registered at construction, kept to expose its configuration.
    wikipedia_retriever: Option<Rc<WikipediaRetriever>>,
    /// `self._open_domain_tracer is not None`; the tracer itself is the process-wide one.
    open_domain_tracing: bool,
    consolidation_task: RefCell<Option<JoinHandle<()>>>,
    closed: Rc<Cell<bool>>,
    retrieval_semaphore: Semaphore,
    total_nodes_created: Cell<usize>,
    total_edges_created: Cell<usize>,
    total_retrievals: Cell<usize>,
    rng: RefCell<PyRandom>,
}

impl NeuralGraphService {
    /// Maximum concurrent retrievals.
    pub const MAX_CONCURRENT_RETRIEVALS: usize = 10;

    /// `NeuralGraphService(storage, config)`; a missing storage is a fresh in-memory one. The random generator
    /// is seeded from the clock.
    pub fn new(storage: Option<Rc<dyn NeuralGraphStorage>>, config: Option<NeuralGraphServiceConfig>) -> Self {
        Self::with_rng(storage, config, PyRandom::new(entropy_seed()))
    }

    /// [`NeuralGraphService::new`] with the generator `_apply_temporal_dynamics` draws from.
    pub fn with_rng(
        storage: Option<Rc<dyn NeuralGraphStorage>>,
        config: Option<NeuralGraphServiceConfig>,
        rng: PyRandom,
    ) -> Self {
        let config = config.unwrap_or_default();
        let storage: Rc<dyn NeuralGraphStorage> =
            storage.unwrap_or_else(|| Rc::new(InMemoryNeuralGraphStorage::default()));
        let gate_registry = Rc::new(create_balanced_registry());

        let hierarchy = config
            .hierarchy_enabled
            .then(|| Rc::new(HierarchyManager::new(storage.clone(), config.hierarchy_config.clone())));
        let temporal = config
            .temporal_enabled
            .then(|| Rc::new(TemporalChainManager::new(storage.clone(), config.temporal_config.clone())));
        let consolidation = config.consolidation_enabled.then(|| {
            Rc::new(ConsolidationManager::new(
                storage.clone(),
                hierarchy.clone(),
                temporal.clone(),
                config.consolidation_config.clone(),
            ))
        });
        let retriever = NeuralRetriever::new(
            storage.clone(),
            Some(gate_registry.clone() as Rc<dyn GateRegistry>),
            temporal.as_deref().cloned(),
            config.retriever_config.clone(),
        );
        let flash_retriever =
            config.use_flash_retriever.then(|| HybridFlashRetriever::new(storage.clone(), config.flash_config.clone()));
        let electron_retriever = config
            .use_electron_retriever
            .then(|| RefCell::new(ElectronRetriever::new(storage.clone(), config.electron_config.clone())));

        let mut wikipedia_retriever = None;
        let external_registry = config.external_retrieval_enabled.then(|| {
            let mut registry = ExternalRetrieverRegistry::new();
            if config.wikipedia_enabled {
                let wiki_config = WikipediaConfig {
                    extract_chars: config.wikipedia_extract_chars,
                    timeout_seconds: config.external_retrieval_timeout_ms / 1000.0,
                    ..WikipediaConfig::default()
                };
                let wikipedia = Rc::new(WikipediaRetriever::new(Some(wiki_config)));
                registry.register(wikipedia.clone(), true);
                wikipedia_retriever = Some(wikipedia);
                tracing::info!("External retrieval enabled with Wikipedia");
            }
            RefCell::new(registry)
        });
        let open_domain_tracing = config.open_domain_tracing_enabled;

        NeuralGraphService {
            config,
            storage,
            gate_registry: RefCell::new(gate_registry),
            hierarchy,
            temporal,
            consolidation,
            retriever: RefCell::new(retriever),
            flash_retriever,
            electron_retriever,
            dialogue_linkers: RefCell::new(IndexMap::new()),
            filtered_retrievers: RefCell::new(IndexMap::new()),
            external_registry,
            wikipedia_retriever,
            open_domain_tracing,
            consolidation_task: RefCell::new(None),
            closed: Rc::new(Cell::new(false)),
            retrieval_semaphore: Semaphore::new(Self::MAX_CONCURRENT_RETRIEVALS),
            total_nodes_created: Cell::new(0),
            total_edges_created: Cell::new(0),
            total_retrievals: Cell::new(0),
            rng: RefCell::new(rng),
        }
    }

    /// Replace the generator used for the probabilistic LTP decay.
    pub fn set_rng(&self, rng: PyRandom) {
        *self.rng.borrow_mut() = rng;
    }

    /// `random.seed(seed)` for this service's generator.
    pub fn seed_rng(&self, seed: i128) {
        self.set_rng(PyRandom::new(seed));
    }

    pub fn config(&self) -> &NeuralGraphServiceConfig {
        &self.config
    }

    pub fn enabled(&self) -> bool {
        self.config.enabled
    }

    pub fn storage(&self) -> &Rc<dyn NeuralGraphStorage> {
        &self.storage
    }

    pub fn hierarchy(&self) -> Option<&Rc<HierarchyManager>> {
        self.hierarchy.as_ref()
    }

    pub fn temporal(&self) -> Option<&Rc<TemporalChainManager>> {
        self.temporal.as_ref()
    }

    pub fn consolidation(&self) -> Option<&Rc<ConsolidationManager>> {
        self.consolidation.as_ref()
    }

    pub fn retriever(&self) -> &RefCell<NeuralRetriever> {
        &self.retriever
    }

    /// The gate registry (see the module notes: nothing reads it after construction).
    pub fn gate_registry(&self) -> Rc<EdgeGateRegistry> {
        self.gate_registry.borrow().clone()
    }

    /// The external retriever registry, when external retrieval is enabled (register sources here).
    pub fn external_registry(&self) -> Option<&RefCell<ExternalRetrieverRegistry>> {
        self.external_registry.as_ref()
    }

    /// The Wikipedia source registered when `wikipedia_enabled` (it is also in the registry).
    pub fn wikipedia_retriever(&self) -> Option<&Rc<WikipediaRetriever>> {
        self.wikipedia_retriever.as_ref()
    }

    /// The dialogue linker of the session's last ingestion batch.
    pub fn dialogue_linker(&self, session_key: &str) -> Option<std::cell::Ref<'_, DialogueLinker>> {
        std::cell::Ref::filter_map(self.dialogue_linkers.borrow(), |m| m.get(session_key)).ok()
    }

    /// `_closed`.
    pub fn closed(&self) -> bool {
        self.closed.get()
    }

    /// Whether a background consolidation task has been started and not yet stopped (`_consolidation_task is
    /// not None`), and if so whether it has already run to its end.
    pub fn consolidation_task_finished(&self) -> Option<bool> {
        self.consolidation_task.borrow().as_ref().map(JoinHandle::is_finished)
    }

    /// `_total_nodes_created`, `_total_edges_created`, `_total_retrievals`.
    pub fn totals(&self) -> (usize, usize, usize) {
        (self.total_nodes_created.get(), self.total_edges_created.get(), self.total_retrievals.get())
    }

    // =========================================================================================================
    // Fairness helper
    // =========================================================================================================

    /// Save an edge and count its type on the source node (`_save_edge_with_fairness`). The storage's own
    /// `save_edge` already counted it once; the second count is the Python's.
    fn save_edge_with_fairness(&self, edge: &EdgeRef) {
        self.storage.save_edge(edge);
        let (source_id, edge_type) = {
            let e = edge.borrow();
            (e.source_id.clone(), e.edge_type)
        };
        if let Some(source_node) = self.storage.get_node(&source_id) {
            source_node.borrow_mut().update_edge_counts(edge_type.value());
            self.storage.update_node(&source_node);
        }
    }

    // =========================================================================================================
    // Episode ingestion
    // =========================================================================================================

    /// Process episodes into neural graph nodes: one MESSAGE node per episode, then temporal chains, automatic
    /// episode segmentation, entity edges, semantic edges, dialogue links and a post-ingestion consolidation
    /// check. Failures are collected in `errors` (or only logged) and never abort the batch.
    pub fn process_episodes(
        &self,
        episodes: &[Episode],
        session_key: &str,
        embeddings: Option<&[Vec<f64>]>,
        wave_amplitudes_list: Option<&[IndexMap<String, f64>]>,
    ) -> NeuralIngestionResult {
        if !self.config.enabled {
            return NeuralIngestionResult::default();
        }
        let start_time = Instant::now();
        let mut result = NeuralIngestionResult::default();
        if episodes.is_empty() {
            return result;
        }

        let mut nodes: Vec<NodeRef> = Vec::new();
        for (i, episode) in episodes.iter().enumerate() {
            let embedding = embeddings.and_then(|e| e.get(i)).cloned();
            let wave_amps = wave_amplitudes_list.and_then(|w| w.get(i)).cloned().unwrap_or_default();
            match self.create_episode_node(episode, session_key, embedding, wave_amps) {
                Ok(Some(node)) => {
                    nodes.push(node);
                    result.nodes_created += 1;
                }
                Ok(None) => {}
                Err(message) => {
                    tracing::error!("Failed to create node for episode {}: {message}", episode.uid);
                    result.errors.push(message);
                }
            }
        }

        if self.config.auto_temporal_linking && nodes.len() >= 2 {
            if let Some(temporal) = &self.temporal {
                match temporal.build_temporal_chain(&nodes, session_key) {
                    Ok(edges) => result.temporal_edges_created = edges.len(),
                    Err(e) => {
                        tracing::error!("Failed to build temporal chain: {}", engine_message(&e));
                        result.errors.push(format!("Temporal chain error: {}", engine_message(&e)));
                    }
                }
            }
        }

        if self.config.auto_episode_segmentation {
            if let Some(hierarchy) = &self.hierarchy {
                match hierarchy.auto_segment_to_episodes(session_key) {
                    Ok(episode_nodes) => result.nodes_created += episode_nodes.len(),
                    Err(e) => tracing::warn!("Auto episode segmentation failed: {}", engine_message(&e)),
                }
            }
        }

        for node in &nodes {
            result.entity_edges_created += self.create_entity_edges(node, session_key);
        }

        let semantic_edges = self.create_semantic_edges_batch(&nodes, session_key, 0.7, 5);
        result.semantic_edges_created = semantic_edges;
        tracing::info!("Created {semantic_edges} semantic edges for horizontal linking");

        if self.config.dialogue_linking_enabled && nodes.len() >= 2 {
            match create_dialogue_links(&nodes, session_key, self.storage.as_ref()) {
                Ok(linker) => {
                    tracing::info!(
                        "Created dialogue links: {} aggregators, {} bindings",
                        linker.aggregators().len(),
                        linker.bindings().len()
                    );
                    self.dialogue_linkers.borrow_mut().insert(session_key.to_string(), linker);
                }
                Err(e) => tracing::warn!("Dialogue linking failed: {e}"),
            }
        }

        result.processing_time_ms = start_time.elapsed().as_secs_f64() * 1000.0;
        result.nodes = nodes;
        self.total_nodes_created.set(self.total_nodes_created.get() + result.nodes_created);
        self.total_edges_created.set(self.total_edges_created.get() + result.entity_edges_created);
        tracing::info!(
            "Neural ingestion: {} nodes, {} temporal edges, {} entity edges in {:.1}ms",
            result.nodes_created,
            result.temporal_edges_created,
            result.entity_edges_created,
            result.processing_time_ms
        );

        if self.config.auto_consolidation_on_ingestion {
            if let Some(consolidation) = &self.consolidation {
                let stats = consolidation.get_consolidation_statistics(session_key);
                let heat = stats.get("heat");
                let count = |key: &str| heat.and_then(|h| h.get(key)).and_then(Value::as_f64).unwrap_or(0.0);
                let (ready_for_promotion, ready_for_eviction) =
                    (count("ready_for_promotion"), count("ready_for_eviction"));
                if ready_for_promotion > 0.0 || ready_for_eviction > 0.0 {
                    tracing::info!(
                        "Post-ingestion consolidation triggered: {ready_for_promotion} ready for promotion, {ready_for_eviction} ready for eviction"
                    );
                    match self.run_consolidation(session_key) {
                        Ok(r) => tracing::info!(
                            "Post-ingestion consolidation complete: {} promoted, {} evicted",
                            r.nodes_promoted,
                            r.nodes_evicted
                        ),
                        Err(e) => tracing::warn!("Post-ingestion consolidation check failed: {}", engine_message(&e)),
                    }
                }
            }
        }
        result
    }

    /// The per-episode body of `process_episodes`' first loop. `Ok(None)` when there is no hierarchy manager
    /// (Python created nothing), `Err` with the exception's message otherwise.
    fn create_episode_node(
        &self,
        episode: &Episode,
        session_key: &str,
        embedding: Option<Vec<f64>>,
        wave_amps: IndexMap<String, f64>,
    ) -> Result<Option<NodeRef>, String> {
        let message_date: Option<PyDateTime> = match &episode.created_at {
            Some(EpisodeTime::DateTime(d)) => Some(*d),
            Some(EpisodeTime::Text(raw)) => parse_datetime_flexible(Some(raw))
                .map_err(|e| e.0)?
                .map(|f| PyDateTime { naive: f.naive, offset_seconds: f.offset_seconds }),
            None => None,
        };
        let temporal_fields =
            generate_temporal_fields(&episode.content, message_date.map(|d| d.naive)).map_err(|e| e.0)?;

        let entity_ids: Vec<String> = episode
            .filterable_metadata
            .as_ref()
            .and_then(|m| m.get("entity_ids"))
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
            .unwrap_or_default();

        let Some(hierarchy) = &self.hierarchy else { return Ok(None) };
        // SPEAKER BINDING: both the canonical (`speaker`) and the legacy (`producer_id`) key
        let speaker_value = episode.producer_id.as_ref().map_or(Value::Null, |p| json!(p));
        let mut metadata = Map::new();
        metadata.insert("episode_uid".into(), json!(episode.uid));
        metadata.insert("speaker".into(), speaker_value.clone());
        metadata.insert("producer_id".into(), speaker_value);
        let field = |key: &str| temporal_fields.get(key).cloned().unwrap_or(Value::Null);
        let args = MessageNodeArgs {
            embedding,
            wave_amplitudes: Some(wave_amps),
            entity_ids: Some(entity_ids),
            importance_score: episode.importance_score,
            resolved_date: field("resolved_date"),
            temporal_tokens: field("temporal_tokens"),
            temporal_metadata: field("temporal_metadata"),
            resolved_dates: field("resolved_dates"),
            explicit_date: field("explicit_date"),
            duration_years: field("duration_years"),
            duration_months: field("duration_months"),
            since_year: field("since_year"),
            since_date: field("since_date"),
            created_at: message_date,
            metadata,
        };
        let node = hierarchy.create_message_node(&episode.content, session_key, args);

        // FAIRNESS: the dominant dimension for query-aware routing, persisted
        node.borrow_mut().update_dominant_dimension();
        self.storage.update_node(&node);
        Ok(Some(node))
    }

    /// Connect a new node to the nodes sharing an entity, with a weight that decays continuously with the
    /// message distance (`exp(-distance / 15)`, edges below 0.1 are not worth keeping). Returns the number of
    /// new edges; existing ones are activated and keep the larger weight.
    pub fn create_entity_edges(&self, node: &NodeRef, session_key: &str) -> usize {
        let (node_id, entity_ids) = {
            let n = node.borrow();
            (n.node_id.clone(), n.entity_ids.clone())
        };
        if entity_ids.is_empty() {
            return 0;
        }
        let mut edges_created = 0;
        let all_nodes = self.storage.get_nodes_by_session(session_key, None);
        let node_index: IndexMap<String, i64> =
            all_nodes.iter().enumerate().map(|(i, n)| (n.borrow().node_id.clone(), i as i64)).collect();
        let current_idx = node_index.get(&node_id).copied().unwrap_or(all_nodes.len() as i64 - 1);
        const ENTITY_CONTEXT_WINDOW: f64 = 15.0;

        for entity_id in &entity_ids {
            let related = self.storage.find_nodes_by_entities(std::slice::from_ref(entity_id), session_key);
            for other in related {
                let other_id = other.borrow().node_id.clone();
                if other_id == node_id {
                    continue;
                }
                let other_idx = node_index.get(&other_id).copied().unwrap_or(0);
                let message_distance = (current_idx - other_idx).abs();
                let decay_weight = (-(message_distance as f64) / ENTITY_CONTEXT_WINDOW).exp();
                if decay_weight < 0.1 {
                    continue;
                }
                // canonical ordering avoids duplicate edges
                let (source_id, target_id) =
                    if node_id <= other_id { (&node_id, &other_id) } else { (&other_id, &node_id) };
                match self.storage.get_edge_between(source_id, target_id, Some(EdgeType::Entity)) {
                    Some(existing) => {
                        {
                            let mut e = existing.borrow_mut();
                            e.activate();
                            if decay_weight > e.base_weight {
                                e.base_weight = decay_weight;
                            }
                        }
                        self.save_edge_with_fairness(&existing);
                    }
                    None => {
                        let mut edge = NeuralEdge::new(generate_edge_id(), source_id, target_id, EdgeType::Entity);
                        edge.base_weight = decay_weight;
                        edge.confidence = decay_weight;
                        edge.metadata.insert("shared_entity".into(), json!(entity_id));
                        edge.metadata.insert("message_distance".into(), json!(message_distance));
                        self.save_edge_with_fairness(&edge_ref(edge));
                        edges_created += 1;
                    }
                }
            }
        }
        edges_created
    }

    /// Horizontal linking: SEMANTIC edges between nodes whose embeddings are at least `similarity_threshold`
    /// similar, the `max_edges_per_node` best per node, weighted by the similarity. Existing edges are
    /// activated. Returns the number of new edges.
    pub fn create_semantic_edges_batch(
        &self,
        nodes: &[NodeRef],
        _session_key: &str,
        similarity_threshold: f64,
        max_edges_per_node: usize,
    ) -> usize {
        let mut edges_created = 0;
        let nodes_with_emb: Vec<&NodeRef> =
            nodes.iter().filter(|n| n.borrow().embedding.as_ref().is_some_and(|e| !e.is_empty())).collect();
        if nodes_with_emb.len() < 2 {
            return 0;
        }
        let mut seen_pairs: IndexSet<(String, String)> = IndexSet::new();

        for (i, node1) in nodes_with_emb.iter().enumerate() {
            let mut edges_for_node = 0;
            let mut similarities: Vec<(&NodeRef, f64)> = Vec::new();
            for (j, node2) in nodes_with_emb.iter().enumerate() {
                if i >= j {
                    continue;
                }
                let sim = {
                    let (a, b) = (node1.borrow(), node2.borrow());
                    cosine_similarity(a.embedding.as_deref().unwrap_or(&[]), b.embedding.as_deref().unwrap_or(&[]))
                };
                if sim >= similarity_threshold {
                    similarities.push((node2, sim));
                }
            }
            // `sort(key=sim, reverse=True)` is stable
            similarities.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

            for (node2, sim) in similarities.into_iter().take(max_edges_per_node) {
                let (id1, id2) = (node1.borrow().node_id.clone(), node2.borrow().node_id.clone());
                let (source_id, target_id) = if id1 <= id2 { (id1, id2) } else { (id2, id1) };
                if !seen_pairs.insert((source_id.clone(), target_id.clone())) {
                    continue;
                }
                match self.storage.get_edge_between(&source_id, &target_id, Some(EdgeType::Semantic)) {
                    Some(existing) => {
                        existing.borrow_mut().activate();
                        self.save_edge_with_fairness(&existing);
                    }
                    None => {
                        let mut edge = NeuralEdge::new(generate_edge_id(), source_id, target_id, EdgeType::Semantic);
                        edge.base_weight = sim;
                        edge.confidence = sim;
                        edge.metadata.insert("similarity".into(), json!(sim));
                        self.save_edge_with_fairness(&edge_ref(edge));
                        edges_created += 1;
                        edges_for_node += 1;
                    }
                }
                if edges_for_node >= max_edges_per_node {
                    break;
                }
            }
        }
        edges_created
    }

    /// Process a wave memory into a node (importance = the entity amplitude) and the edges its signals imply.
    pub fn process_wave_memory(
        &self,
        wave: &MemoryWave,
        session_key: &str,
        episode_uid: Option<&str>,
    ) -> Option<NodeRef> {
        if !self.config.enabled {
            return None;
        }
        let hierarchy = self.hierarchy.as_ref()?;
        let wave_amps = wave.amplitudes.clone().unwrap_or_default();
        let mut metadata = Map::new();
        metadata.insert("wave_id".into(), json!(wave.wave_id));
        metadata.insert("episode_uid".into(), episode_uid.map_or(Value::Null, |u| json!(u)));
        let args = MessageNodeArgs {
            embedding: wave.embedding.clone(),
            importance_score: wave_amps.get("entity").copied().unwrap_or(0.5),
            wave_amplitudes: Some(wave_amps),
            metadata,
            ..MessageNodeArgs::default()
        };
        let node = hierarchy.create_message_node(&wave.content, session_key, args);
        self.process_wave_signals(&node, wave, session_key);
        Some(node)
    }

    /// Edges from wave signals: temporal and entity signals become node metadata / entity ids, causal signals
    /// become CAUSAL edges to the best-matching node (similarity at least 0.5). Returns the edges created.
    fn process_wave_signals(&self, node: &NodeRef, wave: &MemoryWave, session_key: &str) -> usize {
        let mut edges_created = 0;
        let temporal_signals = wave.get_temporal_signals();
        let entity_signals = wave.get_entity_signals();
        let node_id = node.borrow().node_id.clone();

        if !temporal_signals.is_empty() {
            let signals: Vec<Value> = temporal_signals
                .iter()
                .map(|s| json!({"raw": s.raw_expression, "resolved": s.resolved_date.map(|d| d.isoformat())}))
                .collect();
            node.borrow_mut().metadata.insert("temporal_signals".into(), Value::Array(signals));
        }
        if !entity_signals.is_empty() {
            let mut n = node.borrow_mut();
            // `list(set(...))`: first-seen order here, an unspecified one in Python
            let mut unique: IndexSet<String> = n.entity_ids.iter().cloned().collect();
            unique.extend(entity_signals.iter().filter(|s| !s.entity_name.is_empty()).map(|s| s.entity_name.clone()));
            n.entity_ids = unique.into_iter().collect();
            drop(n);
            self.storage.save_node(node);
        }

        for signal in wave.signals.iter().filter(|s| s.signal_type.value() == "causal") {
            node.borrow_mut()
                .metadata
                .insert("causal_signal".into(), json!({"cause": signal.cause, "effect": signal.effect}));

            if !signal.effect.is_empty() && signal.embedding.as_ref().is_some_and(|e| !e.is_empty()) {
                let embedding = signal.embedding.as_deref().unwrap_or(&[]);
                for (effect_node, similarity) in self.storage.vector_search(embedding, session_key, 3, None) {
                    let effect_id = effect_node.borrow().node_id.clone();
                    if effect_id == node_id || similarity < 0.5 {
                        continue;
                    }
                    match self.storage.get_edge_between(&node_id, &effect_id, Some(EdgeType::Causal)) {
                        Some(existing) => {
                            existing.borrow_mut().activate();
                            self.save_edge_with_fairness(&existing);
                        }
                        None => {
                            let mut edge = NeuralEdge::new(generate_edge_id(), &node_id, &effect_id, EdgeType::Causal);
                            edge.base_weight = signal.amplitude * similarity;
                            edge.confidence = similarity;
                            edge.metadata.insert("cause".into(), json!(signal.cause));
                            edge.metadata.insert("effect".into(), json!(signal.effect));
                            edge.metadata.insert("causal_type".into(), json!("causes"));
                            self.save_edge_with_fairness(&edge_ref(edge));
                            edges_created += 1;
                            tracing::debug!(
                                "Created causal edge: {} -> {} (similarity={similarity:.2})",
                                py_prefix(&node_id, 8),
                                py_prefix(&effect_id, 8)
                            );
                            break; // only link to the best match
                        }
                    }
                }
            }
            self.storage.save_node(node);
        }
        edges_created
    }

    // =========================================================================================================
    // Knowledge graph integration
    // =========================================================================================================

    /// Entity nodes and SEMANTIC edges for the extracted relations (weight/10), plus ENTITY edges from
    /// `source_node_id` to every entity node. Returns the number of edges created.
    pub fn process_extraction_result(
        &self,
        extraction: &ExtractionResult,
        session_key: &str,
        source_node_id: Option<&str>,
    ) -> usize {
        if !self.config.enabled {
            return 0;
        }
        let mut edges_created = 0;
        let mut entity_node_map: IndexMap<String, String> = IndexMap::new();

        for entity in &extraction.entities {
            let existing = self.storage.find_nodes_by_entities(std::slice::from_ref(&entity.uid), session_key);
            if let Some(first) = existing.first() {
                entity_node_map.insert(entity.uid.clone(), first.borrow().node_id.clone());
            } else if let Some(hierarchy) = &self.hierarchy {
                let mut metadata = Map::new();
                metadata.insert("entity_name".into(), json!(entity.name));
                metadata.insert("entity_type".into(), json!(entity.entity_type));
                let args = MessageNodeArgs {
                    entity_ids: Some(vec![entity.uid.clone()]),
                    importance_score: entity.mekb_score,
                    metadata,
                    ..MessageNodeArgs::default()
                };
                let content = format!("Entity: {} ({})", entity.name, entity.entity_type);
                let entity_node = hierarchy.create_message_node(&content, session_key, args);
                entity_node_map.insert(entity.uid.clone(), entity_node.borrow().node_id.clone());
            }
        }

        for relation in &extraction.relations {
            let source = entity_node_map.get(&relation.source_entity_uid).filter(|s| !s.is_empty());
            let target = entity_node_map.get(&relation.target_entity_uid).filter(|s| !s.is_empty());
            if let (Some(source), Some(target)) = (source, target) {
                let mut edge = NeuralEdge::new(generate_edge_id(), source, target, EdgeType::Semantic);
                edge.base_weight = relation.weight / 10.0;
                edge.confidence = relation.confidence;
                edge.metadata.insert("relation_type".into(), json!(relation.relation_type));
                edge.metadata.insert("kg_relation_uid".into(), json!(relation.uid));
                self.save_edge_with_fairness(&edge_ref(edge));
                edges_created += 1;
            }
        }

        if let Some(source_node_id) = source_node_id.filter(|s| !s.is_empty()) {
            for entity_node_id in entity_node_map.values() {
                if entity_node_id != source_node_id {
                    let mut edge =
                        NeuralEdge::new(generate_edge_id(), source_node_id, entity_node_id, EdgeType::Entity);
                    edge.base_weight = 0.8;
                    edge.confidence = extraction.confidence;
                    self.save_edge_with_fairness(&edge_ref(edge));
                    edges_created += 1;
                }
            }
        }

        self.total_edges_created.set(self.total_edges_created.get() + edges_created);
        edges_created
    }

    // =========================================================================================================
    // Retrieval
    // =========================================================================================================

    /// Multi-stage neural retrieval: query routing (filter verdicts), then the electron, flash or sequential
    /// retriever, soft-boost score adjustment, dialogue-link expansion, temporal dynamics and the external
    /// retrieval blend. `query_wave_amplitudes` may be modified (see the module notes).
    pub async fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        layer_filter: Option<&[NodeLayer]>,
        query_wave_amplitudes: Option<&mut WaveAmplitudes>,
    ) -> Result<RetrievalResult, ServiceError> {
        if !self.config.enabled || !self.config.use_neural_retrieval {
            return Ok(RetrievalResult {
                query_text: query_text.to_string(),
                nodes: Vec::new(),
                stages_executed: Vec::new(),
                total_candidates_seen: 0,
                co_activations_recorded: 0,
                total_time_ms: 0.0,
            });
        }

        let _permit = self.retrieval_semaphore.acquire().await.expect("the retrieval semaphore is never closed");
        self.total_retrievals.set(self.total_retrievals.get() + 1);

        // QUERY ROUTING: analyse the query and get the filter verdicts
        let mut query_analysis: Option<QueryAnalysis> = None;
        let mut filtered_node_ids: Option<IndexSet<String>> = None;
        let mut soft_filter_results: Option<SoftResults> = None;

        if self.config.query_routing_enabled {
            let mut retrievers = self.filtered_retrievers.borrow_mut();
            if !retrievers.contains_key(session_key) {
                // the context (known speakers and entities) is learned once, from the session as it is now
                let mut filtered =
                    FilteredRetriever::new_shared(self.storage.clone(), &self.config.query_routing_mode, 0.2);
                filtered.update_context(&self.storage.get_nodes_by_layer(session_key, NodeLayer::Message));
                retrievers.insert(session_key.to_string(), filtered);
            }
            let filtered_retriever = retrievers.get_mut(session_key).expect("inserted above");
            let (filtered_candidates, analysis, soft) =
                filtered_retriever.get_filtered_candidates(query_text, session_key, None);
            drop(retrievers);

            if analysis.strategy != "semantic" {
                tracing::debug!(
                    "Query routing: type={}, strategy={}, subject={:?}, mode={}",
                    analysis.query_type.value(),
                    analysis.strategy,
                    analysis.primary_subject,
                    self.config.query_routing_mode
                );
            }
            // hard filtering: the retained ids get a binary boost below
            if self.config.query_routing_mode == "hard"
                && analysis.strategy != "semantic"
                && !filtered_candidates.is_empty()
            {
                let ids: IndexSet<String> = filtered_candidates.iter().map(|n| n.borrow().node_id.clone()).collect();
                tracing::debug!("Hard filter: retained {} candidates", ids.len());
                filtered_node_ids = Some(ids);
            }
            soft_filter_results = soft;
            query_analysis = Some(analysis);
        }

        // GATE PROFILE by query type: computed and logged, never passed on (see the module notes)
        let mut owned_waves = WaveAmplitudes::new();
        let mut waves: Option<&mut WaveAmplitudes> = query_wave_amplitudes;
        if let Some(analysis) = &query_analysis {
            let gate_profile_name = gate_profile_for_query_type(&self.config.gate_profile, analysis.query_type);
            tracing::debug!(
                "Gate profile selected: {gate_profile_name} for query type {}",
                analysis.query_type.value()
            );
            if analysis.suppress_multi_hop {
                // `query_wave_amplitudes = query_wave_amplitudes or {}`: a non-empty caller dict is written to
                let target: &mut WaveAmplitudes = match waves.take() {
                    Some(w) if !w.is_empty() => w,
                    _ => &mut owned_waves,
                };
                target.insert("__suppress_multi_hop".into(), 1.0);
                waves = Some(target);
            }
        }
        let waves: Option<&WaveAmplitudes> = waves.map(|w| &*w);

        let mut result = if let Some(electron) = &self.electron_retriever {
            // ELECTRON RETRIEVER (highest priority)
            let mut result =
                electron.borrow_mut().retrieve(query_text, query_embedding, session_key, Some(limit), waves)?;
            if self.config.dialogue_linking_enabled && !result.nodes.is_empty() {
                let linkers = self.dialogue_linkers.borrow();
                if let Some(linker) = linkers.get(session_key) {
                    let fired: Vec<(String, f64)> =
                        result.nodes.iter().map(|(n, score)| (n.borrow().node_id.clone(), *score)).collect();
                    let expanded = electron.borrow().expand_with_dialogue_links(&fired, Some(linker));
                    let mut expanded_nodes = Vec::new();
                    for (node_id, charge) in expanded.iter().take(limit) {
                        if let Some(node) = self.storage.get_node(node_id) {
                            expanded_nodes.push((node, *charge));
                        }
                    }
                    let mut stages = result.stages_executed.clone();
                    stages.push("dialogue_link_expansion".into());
                    result = RetrievalResult {
                        query_text: query_text.to_string(),
                        nodes: expanded_nodes,
                        stages_executed: stages,
                        total_candidates_seen: expanded.len(),
                        co_activations_recorded: result.co_activations_recorded,
                        total_time_ms: result.total_time_ms,
                    };
                }
            }
            result
        } else if let Some(flash) = &self.flash_retriever {
            // FLASH RETRIEVER: parallel resonance
            flash.retrieve(query_text, query_embedding, session_key, Some(limit), waves)?
        } else {
            self.retriever.borrow_mut().retrieve(
                query_text,
                query_embedding,
                session_key,
                Some(limit),
                layer_filter,
                waves,
            )?
        };

        // QUERY ROUTING SCORE ADJUSTMENT: graduated modifiers from the soft verdicts, else a binary boost
        let have_soft = soft_filter_results.as_ref().is_some_and(|r| !r.is_empty());
        let have_ids = filtered_node_ids.as_ref().is_some_and(|ids| !ids.is_empty());
        if !result.nodes.is_empty() && (have_soft || have_ids) {
            let mut boosted_nodes: Vec<(NodeRef, f64)> = Vec::new();
            if let (true, Some(soft)) = (have_soft, &soft_filter_results) {
                for (node, score) in &result.nodes {
                    let node_id = node.borrow().node_id.clone();
                    match soft.get(&node_id) {
                        Some(filter_result) => {
                            boosted_nodes.push((node.clone(), score * filter_result.score_modifier));
                            if filter_result.score_modifier >= 0.8 {
                                tracing::debug!(
                                    "Soft filter boost: {} modifier={:.2} reason={}",
                                    py_prefix(&node_id, 8),
                                    filter_result.score_modifier,
                                    filter_result.match_reason
                                );
                            }
                        }
                        None => boosted_nodes.push((node.clone(), *score)),
                    }
                }
            } else if let Some(ids) = &filtered_node_ids {
                const FILTER_BOOST: f64 = 1.5;
                for (node, score) in &result.nodes {
                    if ids.contains(&node.borrow().node_id) {
                        boosted_nodes.push((node.clone(), score * FILTER_BOOST));
                    } else {
                        boosted_nodes.push((node.clone(), *score));
                    }
                }
            }
            boosted_nodes.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
            boosted_nodes.truncate(limit);
            let mut stages = result.stages_executed.clone();
            stages.push("query_routing_soft_boost".into());
            result = RetrievalResult {
                query_text: query_text.to_string(),
                nodes: boosted_nodes,
                stages_executed: stages,
                total_candidates_seen: result.total_candidates_seen,
                co_activations_recorded: result.co_activations_recorded,
                total_time_ms: result.total_time_ms,
            };
        }

        // DIALOGUE LINK EXPANSION for the retrievers other than the electron one (which did its own)
        if self.config.dialogue_linking_enabled && self.electron_retriever.is_none() && !result.nodes.is_empty() {
            let linkers = self.dialogue_linkers.borrow();
            if let Some(linker) = linkers.get(session_key) {
                let fired: Vec<(String, f64)> =
                    result.nodes.iter().map(|(n, score)| (n.borrow().node_id.clone(), *score)).collect();
                let expanded = self.expand_with_dialogue_links(&fired, linker, limit);
                if !expanded.is_empty() {
                    let mut stages = result.stages_executed.clone();
                    stages.push("dialogue_link_expansion".into());
                    result = RetrievalResult {
                        query_text: query_text.to_string(),
                        total_candidates_seen: result.total_candidates_seen + expanded.len(),
                        nodes: expanded,
                        stages_executed: stages,
                        co_activations_recorded: result.co_activations_recorded,
                        total_time_ms: result.total_time_ms,
                    };
                }
            }
        }

        // STDP and LTP decay after retrieval
        if self.config.apply_temporal_dynamics && self.temporal.is_some() && !result.nodes.is_empty() {
            self.apply_temporal_dynamics(session_key, &result.nodes)?;
        }

        // EXTERNAL RETRIEVAL for open-domain queries
        Ok(self.maybe_external_retrieval(query_text, query_embedding, result, query_analysis.as_ref(), limit).await)
    }

    /// Expand results through the cross-message dialogue links: Q/A exchange partners at near-full score
    /// (`score * 1.2 * 0.95`), other linked messages at `score * link_strength * 0.85`, the best score per
    /// message, the top `limit` that still exist.
    pub fn expand_with_dialogue_links(
        &self,
        fired_nodes: &[(String, f64)],
        linker: &DialogueLinker,
        limit: usize,
    ) -> Vec<(NodeRef, f64)> {
        let link_boost = DialogueLinkingConfig::LINK_BOOST;
        let mut expanded: IndexMap<String, f64> = IndexMap::new();

        for (node_id, score) in fired_nodes {
            expanded.insert(node_id.clone(), *score);
            // ATOMIC PAIR RETRIEVAL
            if let Some(pair_id) = linker.get_exchange_pair(node_id).filter(|p| !p.is_empty()) {
                let pair_score = score * DialogueLinkingConfig::ATOMIC_PAIR_BOOST * 0.95;
                if expanded.get(&pair_id).is_none_or(|old| *old < pair_score) {
                    tracing::debug!("Atomic pair inclusion: {} -> {}", py_prefix(node_id, 8), py_prefix(&pair_id, 8));
                    expanded.insert(pair_id, pair_score);
                }
            }
        }
        for (node_id, score) in fired_nodes {
            for (linked_id, link_strength, _link_type) in linker.get_linked_messages(node_id) {
                let derived_score = score * link_strength * link_boost;
                if expanded.get(&linked_id).is_none_or(|old| *old < derived_score) {
                    expanded.insert(linked_id, derived_score);
                }
            }
        }

        let mut sorted_items: Vec<(String, f64)> = expanded.into_iter().collect();
        sorted_items.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let mut result = Vec::new();
        for (node_id, score) in sorted_items.into_iter().take(limit) {
            if let Some(node) = self.storage.get_node(&node_id) {
                result.push((node, score));
            }
        }
        result
    }

    /// Fan out to the external retrievers for open-domain queries, or when the session has few hits, and blend
    /// what comes back. Any failure returns the session result untouched.
    #[allow(clippy::await_holding_refcell_ref)] // the registry is only borrowed mutably to register sources
    async fn maybe_external_retrieval(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_result: RetrievalResult,
        query_analysis: Option<&QueryAnalysis>,
        limit: usize,
    ) -> RetrievalResult {
        if !self.config.external_retrieval_enabled {
            return session_result;
        }
        let Some(registry) = &self.external_registry else { return session_result };

        let record = |trace: OpenDomainTrace| {
            if self.open_domain_tracing {
                get_open_domain_tracer().record_trace(trace);
            }
        };

        let mut trace = OpenDomainTrace::new(query_text);
        let (is_open, open_confidence) = is_open_domain_query(query_text);
        trace.is_open_domain = is_open;
        trace.open_domain_confidence = open_confidence;
        if let Some(analysis) = query_analysis {
            trace.detected_query_type = analysis.query_type.value().to_string();
            trace.strategy_used = analysis.strategy.clone();
            trace.bypassed_entity_filter = analysis.strategy == "semantic";
            trace.bypassed_temporal_filter = analysis.strategy == "semantic";
        }

        const RELEVANCE_THRESHOLD: f64 = 0.3;
        trace.session_candidates_count = session_result.nodes.len() as i64;
        trace.session_hits_at_threshold =
            session_result.nodes.iter().filter(|(_, score)| *score >= RELEVANCE_THRESHOLD).count() as i64;

        let mut should_call_external = false;
        if open_confidence >= self.config.external_retrieval_confidence_threshold {
            should_call_external = true;
            tracing::debug!("External retrieval triggered by open-domain confidence: {open_confidence:.2}");
        }
        if trace.session_hits_at_threshold < self.config.external_retrieval_min_session_hits {
            should_call_external = true;
            tracing::debug!(
                "External retrieval triggered by low session hits: {} < {}",
                trace.session_hits_at_threshold,
                self.config.external_retrieval_min_session_hits
            );
        }
        if !should_call_external {
            record(trace);
            return session_result;
        }

        trace.external_retriever_called = true;
        let start_time = Instant::now();
        let external_results = registry
            .borrow()
            .retrieve_all(
                query_text,
                Some(query_embedding),
                self.config.external_max_results,
                self.config.external_retrieval_timeout_ms,
            )
            .await;
        let elapsed_ms = start_time.elapsed().as_secs_f64() * 1000.0;
        trace.external_retrieval_latency_ms = elapsed_ms;

        let all_external: Vec<ExternalResult> = external_results.into_values().flatten().collect();
        trace.external_results_count = all_external.len() as i64;
        if all_external.is_empty() {
            tracing::debug!("External retrieval returned no results for: {}...", py_prefix(query_text, 30));
            record(trace);
            return session_result;
        }

        match self.blend_external_results(&session_result, &all_external, query_embedding, limit) {
            Ok(blended) => {
                trace.final_results_count = blended.nodes.len() as i64;
                let external_in_top = blended
                    .nodes
                    .iter()
                    .take(limit)
                    .filter(|(node, _)| {
                        let n = node.borrow();
                        !n.metadata.is_empty() && n.metadata.get("source").and_then(Value::as_str) == Some("external")
                    })
                    .count();
                trace.external_in_top_k = external_in_top as i64;
                tracing::info!(
                    "External retrieval complete: {} external results, {external_in_top} in top-{limit}, latency={elapsed_ms:.1}ms",
                    all_external.len()
                );
                record(trace);
                blended
            }
            Err(e) => {
                tracing::warn!("External retrieval failed: {e}");
                trace.external_retrieval_latency_ms = start_time.elapsed().as_secs_f64() * 1000.0;
                record(trace);
                session_result
            }
        }
    }

    /// Blend external results with the session results: external results become pseudo-nodes scored by
    /// `(0.4 * relevance + 0.6 * embedding similarity) * external_result_weight` (the relevance alone, weighted,
    /// without an embedding), those duplicating a session node are skipped, and the union is re-sorted.
    ///
    /// The similarity is numpy float32 arithmetic (`np.linalg.norm`, division, `np.dot`); embeddings of
    /// different lengths were a numpy `ValueError`, returned as `Err` (the caller logs it and keeps the session
    /// results).
    pub fn blend_external_results(
        &self,
        session_result: &RetrievalResult,
        external_results: &[ExternalResult],
        query_embedding: &[f64],
        limit: usize,
    ) -> Result<RetrievalResult, String> {
        let mut query_emb = to_f32(query_embedding);
        let query_norm = norm_f32(&query_emb);
        if query_norm > 1e-10_f32 {
            query_emb = query_emb.iter().map(|x| x / query_norm).collect();
        }

        let mut blended: Vec<(NodeRef, f64)> = session_result.nodes.clone();
        for ext_result in external_results {
            let mut pseudo = NeuralNode::new(
                format!("external_{}", ext_result.cache_key()),
                NodeLayer::Message,
                ext_result.content.clone(),
            );
            pseudo.session_key = "__external__".into();
            pseudo.embedding = ext_result.embedding.clone();
            pseudo.metadata.insert("source".into(), json!("external"));
            pseudo.metadata.insert("external_source".into(), json!(ext_result.source));
            pseudo.metadata.insert("source_url".into(), json!(ext_result.source_url));
            pseudo.metadata.insert("title".into(), json!(ext_result.title));
            pseudo.metadata.insert("original_relevance".into(), json!(ext_result.relevance_score));

            let mut score = ext_result.relevance_score * self.config.external_result_weight;
            if let Some(embedding) = ext_result.embedding.as_ref().filter(|e| !e.is_empty()) {
                let ext_emb = to_f32(embedding);
                let ext_norm = norm_f32(&ext_emb);
                if ext_norm > 1e-10_f32 {
                    if ext_emb.len() != query_emb.len() {
                        return Err(format!(
                            "shapes ({},) and ({},) not aligned: {} (dim 0) != {} (dim 0)",
                            query_emb.len(),
                            ext_emb.len(),
                            query_emb.len(),
                            ext_emb.len()
                        ));
                    }
                    let normalized: Vec<f32> = ext_emb.iter().map(|x| x / ext_norm).collect();
                    let similarity = sdot(&query_emb, &normalized) as f64;
                    score = (ext_result.relevance_score * 0.4 + similarity * 0.6) * self.config.external_result_weight;
                }
            }

            let mut is_duplicate = false;
            for (session_node, _) in &session_result.nodes {
                if self.is_content_duplicate(&pseudo.content, &session_node.borrow().content, 0.8) {
                    is_duplicate = true;
                    tracing::debug!("Skipping duplicate external result: {:?}", ext_result.title);
                    break;
                }
            }
            if !is_duplicate {
                blended.push((node_ref(pseudo), score));
            }
        }

        blended.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        blended.truncate(limit);
        let mut stages = session_result.stages_executed.clone();
        stages.push("external_retrieval_blend".into());
        Ok(RetrievalResult {
            query_text: session_result.query_text.clone(),
            nodes: blended,
            stages_executed: stages,
            total_candidates_seen: session_result.total_candidates_seen + external_results.len(),
            co_activations_recorded: session_result.co_activations_recorded,
            total_time_ms: session_result.total_time_ms,
        })
    }

    /// Jaccard similarity of the lower-cased word sets at least `threshold` (empty text is never a duplicate).
    /// The Python default threshold is 0.8.
    pub fn is_content_duplicate(&self, content1: &str, content2: &str, threshold: f64) -> bool {
        if content1.is_empty() || content2.is_empty() {
            return false;
        }
        let (lower1, lower2) = (content1.to_lowercase(), content2.to_lowercase());
        let words1: IndexSet<&str> = py_split_whitespace(&lower1).into_iter().collect();
        let words2: IndexSet<&str> = py_split_whitespace(&lower2).into_iter().collect();
        if words1.is_empty() || words2.is_empty() {
            return false;
        }
        let intersection = words1.intersection(&words2).count();
        let union = words1.union(&words2).count();
        union > 0 && (intersection as f64 / union as f64) >= threshold
    }

    /// Retrieve with the temporal neighbourhood of the best hit and the per-layer hierarchy summary.
    pub async fn retrieve_with_context(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
    ) -> Result<RetrievalContext, ServiceError> {
        let result = self.retrieve(query_text, query_embedding, session_key, limit, None, None).await?;
        let mut context = RetrievalContext {
            nodes: result.nodes.clone(),
            stages_executed: result.stages_executed.clone(),
            total_time_ms: result.total_time_ms,
            temporal_context: None,
            hierarchy_summary: None,
        };
        if let (Some((top_node, _)), Some(_)) = (result.nodes.first(), &self.temporal) {
            context.temporal_context = Some(self.retriever.borrow().retrieve_temporal_context(top_node, 3, 3));
        }
        if let Some(hierarchy) = &self.hierarchy {
            context.hierarchy_summary = Some(hierarchy.get_layer_summary(session_key));
        }
        Ok(context)
    }

    /// STDP on every pair of co-retrieved nodes (in retrieval order, the milliseconds between their creation
    /// times) and, with probability `temporal_dynamics_probability`, LTP decay. Mixing naive and aware creation
    /// times raises, as in Python.
    fn apply_temporal_dynamics(
        &self,
        session_key: &str,
        retrieved_nodes: &[(NodeRef, f64)],
    ) -> Result<(), ServiceError> {
        let Some(temporal) = &self.temporal else { return Ok(()) };
        if retrieved_nodes.len() < 2 {
            return Ok(());
        }
        let nodes: Vec<&NodeRef> = retrieved_nodes.iter().map(|(n, _)| n).collect();
        let mut stdp_applied = 0;
        for (i, node1) in nodes.iter().enumerate() {
            for node2 in &nodes[i + 1..] {
                let (id1, created1) = {
                    let n = node1.borrow();
                    (n.node_id.clone(), n.created_at)
                };
                let (id2, created2) = {
                    let n = node2.borrow();
                    (n.node_id.clone(), n.created_at)
                };
                let delta = created2.sub(&created1).map_err(EngineError::from)?;
                let delta_ms = total_seconds(delta) * 1000.0;
                if temporal.apply_stdp(&id1, &id2, delta_ms) {
                    stdp_applied += 1;
                }
            }
        }
        if stdp_applied > 0 {
            tracing::debug!("Applied STDP to {stdp_applied} temporal edges");
        }
        if self.rng.borrow_mut().random() < self.config.temporal_dynamics_probability {
            let decayed = temporal.decay_ltp(session_key, None);
            if decayed > 0 {
                tracing::debug!("LTP decay applied to {decayed} edges");
            }
        }
        Ok(())
    }

    // =========================================================================================================
    // Consolidation
    // =========================================================================================================

    /// Run a manual consolidation cycle (all zeros without a consolidation manager).
    pub fn run_consolidation(&self, session_key: &str) -> Result<ConsolidationResult, EngineError> {
        match &self.consolidation {
            Some(consolidation) if self.config.consolidation_enabled => {
                consolidation.run_consolidation_cycle(session_key)
            }
            _ => Ok(ConsolidationResult { session_key: session_key.to_string(), ..Default::default() }),
        }
    }

    /// Start the background task that sleeps `auto_consolidation_interval_seconds` and runs one cycle, until
    /// the service is closed. Does nothing when a task is already running. Must be called from within a
    /// `tokio::task::LocalSet` (see the module notes).
    pub fn start_auto_consolidation(&self, session_key: &str) {
        if self.consolidation_task.borrow().is_some() {
            return;
        }
        let closed = self.closed.clone();
        let consolidation = self.consolidation.clone();
        let interval = self.config.auto_consolidation_interval_seconds;
        let task_session_key = session_key.to_string();
        let handle = tokio::task::spawn_local(async move {
            while !closed.get() {
                if interval > 0.0 {
                    tokio::time::sleep(sleep_duration(interval)).await;
                } else {
                    // asyncio.sleep(0) yields once
                    tokio::task::yield_now().await;
                }
                if !closed.get() {
                    if let Some(consolidation) = &consolidation {
                        if let Err(e) = consolidation.run_consolidation_cycle(&task_session_key) {
                            tracing::error!("Consolidation cycle error: {}", engine_message(&e));
                        }
                    }
                }
            }
        });
        *self.consolidation_task.borrow_mut() = Some(handle);
        tracing::info!("Started auto-consolidation for session {session_key}");
    }

    /// Cancel the background consolidation task and wait for it to end.
    pub async fn stop_auto_consolidation(&self) {
        let handle = self.consolidation_task.borrow_mut().take();
        if let Some(handle) = handle {
            handle.abort();
            let _ = handle.await;
            tracing::info!("Stopped auto-consolidation");
        }
    }

    // =========================================================================================================
    // Utility methods
    // =========================================================================================================

    /// Storage statistics plus the service counters, consolidation and hierarchy statistics and the query
    /// routing mode (with the session's filter statistics once it has been queried).
    pub fn get_statistics(&self, session_key: &str) -> Value {
        let mut stats = match self.storage.get_session_statistics(session_key) {
            Value::Object(m) => m,
            other => panic!("AttributeError: '{}' object has no attribute 'update'", py_type_name_of(&other)),
        };
        stats.insert(
            "service".into(),
            json!({
                "enabled": self.config.enabled,
                "total_nodes_created": self.total_nodes_created.get(),
                "total_edges_created": self.total_edges_created.get(),
                "total_retrievals": self.total_retrievals.get(),
            }),
        );
        if let Some(consolidation) = &self.consolidation {
            stats.insert("consolidation".into(), consolidation.get_consolidation_statistics(session_key));
        }
        if let Some(hierarchy) = &self.hierarchy {
            stats.insert("hierarchy".into(), hierarchy.get_layer_summary(session_key));
        }
        let retrievers = self.filtered_retrievers.borrow();
        let routing = match retrievers.get(session_key) {
            Some(filtered) if self.config.query_routing_enabled => {
                let mut routing = Map::new();
                routing.insert("enabled".into(), json!(true));
                routing.insert("mode".into(), json!(self.config.query_routing_mode));
                if let Value::Object(filter_stats) = filtered.get_filter_statistics().to_json() {
                    routing.extend(filter_stats);
                }
                Value::Object(routing)
            }
            _ => json!({"enabled": self.config.query_routing_enabled, "mode": self.config.query_routing_mode}),
        };
        stats.insert("query_routing".into(), routing);
        Value::Object(stats)
    }

    /// Clear all neural graph data of a session; the number of items cleared.
    pub fn clear_session(&self, session_key: &str) -> usize {
        self.storage.clear_session(session_key)
    }

    /// Close the service: stop the background task and make any loop iteration still pending a no-op.
    pub async fn close(&self) {
        self.closed.set(true);
        self.stop_auto_consolidation().await;
        tracing::info!("NeuralGraphService closed");
    }

    // =========================================================================================================
    // Integration helpers
    // =========================================================================================================

    /// Episode uid to node id mapping: a placeholder in Python, always empty.
    pub fn get_episode_node_ids(&self, _session_key: &str) -> Vec<String> {
        Vec::new()
    }

    /// Link a node to existing memory ids that are nodes (weight 0.5, Python default type SEMANTIC).
    pub fn link_to_existing_memories(&self, node_id: &str, memory_ids: &[String], edge_type: EdgeType) -> usize {
        let mut edges_created = 0;
        for memory_id in memory_ids {
            if self.storage.get_node(memory_id).is_some() {
                let mut edge = NeuralEdge::new(generate_edge_id(), node_id, memory_id, edge_type);
                edge.base_weight = 0.5;
                self.save_edge_with_fairness(&edge_ref(edge));
                edges_created += 1;
            }
        }
        edges_created
    }

    // =========================================================================================================
    // Exposed temporal dynamics
    // =========================================================================================================

    /// Spike-timing dependent plasticity on the temporal edge source -> target (`false` without a temporal
    /// manager or such an edge).
    pub fn apply_stdp(&self, source_id: &str, target_id: &str, time_delta_ms: f64) -> bool {
        match &self.temporal {
            Some(temporal) => temporal.apply_stdp(source_id, target_id, time_delta_ms),
            None => false,
        }
    }

    /// Temporal summation of the activation signals reaching a node (0.0 without a temporal manager or node).
    pub fn get_temporal_summation(&self, node_id: &str) -> f64 {
        let Some(temporal) = &self.temporal else { return 0.0 };
        match self.storage.get_node(node_id) {
            Some(node) => temporal.temporal_summation(&node, None),
            None => 0.0,
        }
    }

    /// Recurring temporal patterns of the session (transitions occurring at least twice).
    pub fn analyze_temporal_patterns(&self, session_key: &str) -> Vec<Value> {
        match &self.temporal {
            Some(temporal) => temporal.find_temporal_patterns(session_key, 2),
            None => Vec::new(),
        }
    }

    /// `{"in_refractory", "remaining_ms", "last_activation"}` of a node; without a temporal manager or node
    /// only the first two (with `remaining_ms` the integer 0, as in Python).
    pub fn get_refractory_state(&self, node_id: &str) -> Value {
        let unknown = || json!({"in_refractory": false, "remaining_ms": 0});
        let Some(temporal) = &self.temporal else { return unknown() };
        let Some(node) = self.storage.get_node(node_id) else { return unknown() };
        let in_refractory = temporal.is_in_refractory(&node);
        let remaining = temporal.get_refractory_remaining(&node);
        let last_activation = node.borrow().last_activated.isoformat();
        json!({"in_refractory": in_refractory, "remaining_ms": remaining, "last_activation": last_activation})
    }

    // =========================================================================================================
    // Exposed heat management
    // =========================================================================================================

    /// Add heat to a node (capped at 10); the new heat, 0.0 without a consolidation manager or node.
    pub fn boost_node_heat(&self, node_id: &str, amount: f64) -> f64 {
        match &self.consolidation {
            Some(consolidation) => consolidation.boost_heat(node_id, amount),
            None => 0.0,
        }
    }

    /// Decay the heat of every node of a session; the number of nodes decayed.
    pub fn decay_all_heat(&self, session_key: &str) -> usize {
        match &self.consolidation {
            Some(consolidation) => consolidation.decay_all_heat(session_key, None),
            None => 0,
        }
    }

    /// Nodes with `min_heat <= heat <= max_heat`, hottest first (Python defaults 0.0, 10.0 and 50).
    pub fn get_nodes_by_heat(
        &self,
        session_key: &str,
        min_heat: f64,
        max_heat: f64,
        limit: usize,
    ) -> Vec<(NodeRef, f64)> {
        let mut filtered: Vec<(NodeRef, f64)> = self
            .storage
            .get_nodes_by_session(session_key, None)
            .into_iter()
            .map(|n| {
                let heat = n.borrow().heat_score;
                (n, heat)
            })
            .filter(|(_, heat)| min_heat <= *heat && *heat <= max_heat)
            .collect();
        filtered.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        filtered.truncate(limit);
        filtered
    }

    // =========================================================================================================
    // Exposed gate tuning
    // =========================================================================================================

    /// Adapt the gating thresholds to the last retrieval. The registry has no `adapt_threshold` (only
    /// `AdaptiveGate` does), so, as in Python, this only logs.
    pub fn adapt_gate_threshold(&self, _candidate_count: i64, _target_count: i64) {
        tracing::debug!("Gate registry does not support threshold adaptation");
    }

    /// Set the overall gate strictness: "strict", "permissive", anything else balanced. Only the service's own
    /// registry changes (see the module notes).
    pub fn set_gate_strictness(&self, strictness: &str) {
        let registry = match strictness {
            "strict" => create_strict_registry(),
            "permissive" => create_permissive_registry(),
            _ => create_balanced_registry(),
        };
        *self.gate_registry.borrow_mut() = Rc::new(registry);
        tracing::info!("Gate strictness set to: {strictness}");
    }
}

/// Python type name of a JSON value, for the `AttributeError` text of a storage that returned a non-dict.
fn py_type_name_of(v: &Value) -> &'static str {
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
