//! Port of `retrieval/service.py`.
//!
//! This file holds two independent parts: first the module-level parsing helpers at the end of the Python file
//! (`extract_keywords` onwards, shared with the benchmark harnesses), then the `NeuralGraphService` class, ported
//! separately below the boundary comment.
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
//! - `extract_topic_keywords_universal` belongs to `tesseract.py` (ported separately); until that port is merged
//!   a private copy of it and of the synonym expansion it uses lives at the end of this section
//!   ([`topic_keywords_shim`]). Replace it with `crate::retrieval::tesseract::extract_topic_keywords_universal`.

use crate::llm_backend::{EmbedOptions, Llm};
use crate::pyre::{compile, findall, findall1, search};
use fancy_regex::Regex;
use indexmap::IndexSet;
use neuralgraph_memory::pycompat::{py_split_whitespace, py_str, py_strip, py_strip_chars, py_truthy};
use neuralgraph_memory::temporal::MONTH_NAMES;
use serde_json::Value;
use std::sync::LazyLock;

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
                let universal =
                    universal.get_or_insert_with(|| topic_keywords_shim::extract_topic_keywords_universal(question));
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
    let topic_keywords = topic_keywords_shim::extract_topic_keywords_universal(question);

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

/// Temporary private copy of `tesseract.extract_topic_keywords_universal` and the synonym expansion it uses
/// (`synonym_hash.expand_with_synonyms_extended`), needed by [`extract_list_items`] and
/// [`extract_list_items_semantic`]. Both live in other modules of the port; once those are merged, call them and
/// delete this module. The thesaurus is read from where the Python read it.
pub mod topic_keywords_shim {
    use super::{findall1, KEYWORD_RE};
    use std::collections::{HashMap, HashSet};
    use std::sync::LazyLock;

    const THESAURUS: &str =
        concat!(env!("CARGO_MANIFEST_DIR"), "/../../NeuralGraph/research/retrieval/en_thesaurus.jsonl");

    /// `_load_thesaurus()`: word -> synonyms, both directions; empty when the file is missing.
    static SYNONYMS: LazyLock<HashMap<String, HashSet<String>>> = LazyLock::new(|| {
        let mut hash: HashMap<String, HashSet<String>> = HashMap::new();
        let Ok(text) = std::fs::read_to_string(THESAURUS) else {
            tracing::warn!("Thesaurus file not found: {THESAURUS}");
            return hash;
        };
        for line in text.lines() {
            let Ok(entry) = serde_json::from_str::<serde_json::Value>(neuralgraph_memory::pycompat::py_strip(line))
            else {
                continue;
            };
            let word = entry.get("word").and_then(|w| w.as_str()).unwrap_or("").to_lowercase().replace('_', " ");
            let synonyms = entry.get("synonyms").and_then(|s| s.as_array());
            let Some(synonyms) = synonyms.filter(|s| !s.is_empty()) else { continue };
            if word.is_empty() {
                continue;
            }
            let normalized: HashSet<String> = synonyms
                .iter()
                .filter_map(|s| s.as_str())
                .filter(|s| s.chars().count() > 1)
                .map(|s| s.to_lowercase().replace('_', " "))
                .collect();
            hash.entry(word.clone()).or_default().extend(normalized.iter().cloned());
            for syn in normalized {
                hash.entry(syn).or_default().insert(word.clone());
            }
        }
        hash
    });

    /// `DOMAIN_SYNONYMS`
    const DOMAIN_SYNONYMS: [(&str, &[&str]); 18] = [
        ("medium", &["console", "pc", "platform", "device", "system"]),
        ("mediums", &["consoles", "pcs", "platforms", "devices", "systems"]),
        ("console", &["medium", "platform", "device", "gamecube", "playstation", "xbox", "switch", "nintendo", "pc"]),
        ("tournament", &["tourney", "competition", "contest", "championship"]),
        ("tourney", &["tournament", "competition", "contest"]),
        ("stream", &["streaming", "broadcast", "live"]),
        ("animal", &["pet", "creature", "turtle", "dog", "cat"]),
        ("animals", &["pets", "creatures", "turtles", "dogs", "cats"]),
        ("pet", &["animal", "companion", "turtle", "dog", "cat"]),
        ("pets", &["animals", "companions", "turtles", "dogs", "cats"]),
        ("letter", &["letters", "mail", "correspondence", "message"]),
        ("letters", &["letter", "mail", "correspondence", "messages"]),
        ("gift", &["present", "gave", "give"]),
        ("gave", &["give", "gift", "present"]),
        ("script", &["screenplay", "writing", "movie"]),
        ("scripts", &["screenplays", "writings", "movies"]),
        ("reject", &["rejected", "declined", "denied", "refused"]),
        ("rejected", &["reject", "declined", "denied", "refused"]),
    ];

    /// `_expand_morphology_basic`
    fn expand_morphology_basic(word: &str) -> HashSet<String> {
        let mut variants: HashSet<String> = HashSet::from([word.to_string()]);
        let w = word.to_lowercase();
        let n = w.chars().count();
        let mut base = w.clone();
        let chop = |s: &str, k: usize| s.chars().take(s.chars().count() - k).collect::<String>();
        let doubled = |s: &str| {
            let c: Vec<char> = s.chars().collect();
            c.len() >= 2 && c[c.len() - 1] == c[c.len() - 2]
        };
        if w.ends_with("ed") && n > 4 {
            base = chop(&w, 2);
            if base.ends_with('i') {
                base = chop(&base, 1) + "y";
            } else if doubled(&base) {
                base = chop(&base, 1);
            }
        } else if w.ends_with("ing") && n > 5 {
            base = chop(&w, 3);
            if doubled(&base) {
                base = chop(&base, 1);
            }
        } else if w.ends_with("ies") && n > 4 {
            base = chop(&w, 3) + "y";
        } else if w.ends_with('s') && !w.ends_with("ss") && n > 3 {
            base = chop(&w, 1);
        }
        variants.insert(base.clone());
        if base.chars().count() >= 3 {
            variants.insert(format!("{base}s"));
            variants.insert(format!("{base}ed"));
            variants.insert(format!("{base}ing"));
            if base.ends_with('e') {
                variants.insert(format!("{}ing", chop(&base, 1)));
                variants.insert(format!("{base}d"));
            }
        }
        // `str.isalpha()`: non-empty and every character a letter
        variants.retain(|v| v.chars().count() >= 3 && v.chars().all(char::is_alphabetic));
        variants
    }

    /// `extract_topic_keywords_universal`. Python returned a `set` and every caller only tests membership; the
    /// keywords come back sorted.
    pub fn extract_topic_keywords_universal(question: &str) -> indexmap::IndexSet<String> {
        const STOP: [&str; 41] = [
            "what", "which", "who", "where", "when", "why", "how", "did", "does", "do", "is", "are", "was", "were",
            "has", "have", "had", "will", "would", "could", "should", "the", "a", "an", "to", "for", "in", "on", "at",
            "of", "with", "from", "types", "type", "kinds", "kind", "names", "name", "list", "all", "any",
        ];
        let mut keywords: HashSet<String> = findall1(&KEYWORD_RE, &question.to_lowercase())
            .into_iter()
            .filter(|t| !STOP.contains(&t.as_str()))
            .collect();
        let mut expanded = HashSet::new();
        for w in &keywords {
            expanded.extend(expand_morphology_basic(w));
        }
        keywords.extend(expanded);
        // expand_with_synonyms_extended
        let mut with_syns = keywords.clone();
        for word in &keywords {
            let lower = word.to_lowercase();
            if let Some(s) = SYNONYMS.get(&lower) {
                with_syns.extend(s.iter().cloned());
            }
            if let Some((_, s)) = DOMAIN_SYNONYMS.iter().find(|(k, _)| *k == lower) {
                with_syns.extend(s.iter().map(|x| x.to_string()));
            }
        }
        let mut sorted: Vec<String> = with_syns.into_iter().collect();
        sorted.sort();
        sorted.into_iter().collect()
    }
}

// ---- NeuralGraphService (ported separately) ----
