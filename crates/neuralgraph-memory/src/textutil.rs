//! Small, dependency-free text helpers shared by extraction, storage and retrieval.
//!
//! The tokenizer, stemmer, entity normaliser and hashes decide which memories are duplicates, which keyword
//! queries reach FTS5 and which entity keys exist in a database, so they reproduce the original Python rules
//! exactly (golden fixtures pin them).

use crate::pycompat::{is_py_space, py_len, py_prefix, py_rstrip, py_strip, py_strip_chars};
use blake2::digest::consts::U10;
use blake2::{Blake2b, Digest};
use regex::Regex;
use std::sync::LazyLock;
use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;

#[rustfmt::skip]
const STOP: &[&str] = &[
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "with", "that",
    "this", "have", "from", "they", "what", "when", "where", "which", "who", "how", "did", "does", "has", "his", "she",
    "him", "them", "been", "were", "will", "would", "could", "should", "about", "into", "your", "just", "like", "some",
    "than", "then", "there", "their", "also", "very", "really", "much", "many", "more", "yeah", "yes", "hey", "thanks",
    "thank", "sounds", "sure", "good", "great", "awesome", "love", "know", "its", "it's", "i'm", "don't", "can't",
    "get", "got", "let", "lets", "let's", "okay", "well", "still",
];

pub fn is_stop(t: &str) -> bool {
    STOP.contains(&t)
}

static TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z0-9][a-z0-9'\-]*").unwrap());
static WS_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s\x1c-\x1f]+").unwrap());
static POSSESSIVE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"'s\b|'\b").unwrap());
static NON_ALNUM_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]").unwrap());
static ENTITY_STRIP_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9 '\-]").unwrap());
static RELATION_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

/// Collapse whitespace runs to one space and strip the ends.
pub fn normalize_ws(text: &str) -> String {
    WS_RE.replace_all(py_strip(text), " ").into_owned()
}

fn is_vowel(c: char) -> bool {
    matches!(c, 'a' | 'e' | 'i' | 'o' | 'u')
}

fn cvc_short(w: &[char]) -> bool {
    w.len() == 3 && !is_vowel(w[0]) && is_vowel(w[1]) && !is_vowel(w[2]) && !matches!(w[2], 'w' | 'x' | 'y')
}

/// Porter step-1b clean-up after removing -ed / -ing.
fn after_strip(w: &[char]) -> String {
    let s: String = w.iter().collect();
    if s.ends_with("at") || s.ends_with("bl") || s.ends_with("iz") {
        return s + "e";
    }
    let n = w.len();
    if n > 2 && w[n - 1] == w[n - 2] && !matches!(w[n - 1], 'l' | 's' | 'z') && !is_vowel(w[n - 1]) {
        return w[..n - 1].iter().collect();
    }
    if cvc_short(w) {
        return s + "e";
    }
    s
}

/// Small Porter-style stemmer (lives -> live, moved -> move, running -> run, cities -> city). The SQLite FTS5
/// index uses the real Porter stemmer; this keeps the in-process paths roughly aligned.
pub fn stem(word: &str) -> String {
    let w: Vec<char> = word.chars().collect();
    let n = w.len();
    let ends = |suffix: &str| word.ends_with(suffix);
    if n > 4 && ends("ies") {
        return w[..n - 3].iter().collect::<String>() + "y";
    }
    if n > 5 && ends("ing") && w[..n - 3].iter().any(|c| is_vowel(*c)) {
        return after_strip(&w[..n - 3]);
    }
    if n > 4 && ends("ed") && w[..n - 2].iter().any(|c| is_vowel(*c)) {
        return after_strip(&w[..n - 2]);
    }
    if n > 3 && ends("es") && matches!(w[n - 3], 's' | 'x' | 'z' | 'h') {
        return w[..n - 2].iter().collect();
    }
    if n > 3 && ends("s") && !ends("ss") {
        return w[..n - 1].iter().collect();
    }
    word.to_string()
}

/// Strip combining marks after NFKD (Zürich -> Zurich) without changing case.
fn strip_diacritics(text: &str) -> String {
    text.nfkd().filter(|c| canonical_combining_class(*c) == 0).collect()
}

/// Lower-case and strip diacritics (Zürich -> zurich) so keyword matching survives accents.
pub fn fold(text: &str) -> String {
    strip_diacritics(text).to_lowercase()
}

/// `^\d+$` on a token (tokens are ASCII by construction).
fn is_number(t: &str) -> bool {
    !t.is_empty() && t.bytes().all(|c| c.is_ascii_digit())
}

/// Lower-cased, accent-folded content tokens (>= 3 chars or any number, stop-words removed, possessives
/// stripped, lightly stemmed). Used for BM25, grounding overlap and the fake embeddings.
pub fn tokenize(text: &str) -> Vec<String> {
    tokenize_opts(text, true, true)
}

pub fn tokenize_opts(text: &str, do_stem: bool, keep_numbers: bool) -> Vec<String> {
    let folded = fold(text);
    let cleaned = POSSESSIVE_RE.replace_all(&folded, "");
    let mut out = Vec::new();
    for m in TOKEN_RE.find_iter(&cleaned) {
        let t = py_strip_chars(m.as_str(), "'-");
        if t.is_empty() || is_stop(t) {
            continue;
        }
        let numeric = is_number(t);
        if py_len(t) < 3 && !(keep_numbers && numeric) {
            continue;
        }
        out.push(if do_stem && !numeric { stem(t) } else { t.to_string() });
    }
    out
}

/// Build a safe FTS5 MATCH expression: quoted alphanumeric tokens joined by OR (no possessives, no operators, no
/// quotes). Returns '' when the query has no usable token.
pub fn fts_match_string(query: &str) -> String {
    let folded = fold(query);
    let cleaned = POSSESSIVE_RE.replace_all(&folded, "");
    let mut toks: Vec<String> = Vec::new();
    for m in TOKEN_RE.find_iter(&cleaned) {
        let t = NON_ALNUM_RE.replace_all(m.as_str(), "").into_owned();
        if !t.is_empty() && !is_stop(&t) && (py_len(&t) >= 3 || is_number(&t)) && !toks.contains(&t) {
            toks.push(t);
        }
    }
    toks.iter().map(|t| format!("\"{t}\"")).collect::<Vec<_>>().join(" OR ")
}

/// Stable 20-hex-char digest of the given strings (idempotency keys): BLAKE2b with a 10-byte digest over each
/// part followed by 0x1f.
pub fn content_hash(parts: &[&str]) -> String {
    let mut h = Blake2b::<U10>::new();
    for p in parts {
        h.update(p.as_bytes());
        h.update(b"\x1f");
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Canonical entity key: NFKD, lower, alnum/space/'/-, single spaces, possessive stripped.
pub fn norm_entity(name: &str) -> String {
    let s = strip_diacritics(name).to_lowercase();
    let s = ENTITY_STRIP_RE.replace_all(&s, " ");
    let mut s = normalize_ws(&s);
    if s.ends_with("'s") {
        s = py_strip(&s[..s.len() - 2]).to_string();
    }
    let s = py_strip_chars(&s, "'- ");
    match s.strip_prefix("the ") {
        Some(rest) => rest.to_string(),
        None => s.to_string(),
    }
}

/// Canonical relation key: lower snake_case verb phrase.
pub fn norm_relation(rel: &str) -> String {
    let lower = rel.to_lowercase();
    let s = RELATION_RE.replace_all(&lower, "_");
    py_prefix(py_strip_chars(&s, "_"), 60).to_string()
}

// ---------------------------------------------------------------------------------------------
// Cheap "is this worth an LLM call?" gate. Conservative: it only skips text that is almost certainly
// chit-chat; everything else goes to the model, which makes the final call.
// ---------------------------------------------------------------------------------------------

static CHITCHAT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)^(?:(?:ok(?:ay)?|k|sure|yes|yeah|yep|nope?|no|thanks?|thank you|thx|ty|cool|nice|great|awesome|perfect|",
        r"lol|haha+|hmm+|hi|hello|hey|yo|good (?:morning|night|evening|afternoon)|bye|see you|cheers|",
        r"sounds good|got it|will do|makes sense|understood|noted|np|no problem|you're welcome|welcome|",
        r"same here|me too|right|exactly|true|indeed|alright|fine|good luck|take care|good to see you|",
        r"how are you|how have you been|what's up|whats up|long time no see)[\s\x1c-\x1f!.,?:)]*)+$",
    ))
    .unwrap()
});
static ASSISTANT_BOILERPLATE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)^(?:(?:sure|certainly|of course|absolutely|great question|happy to help|you're welcome|",
        r"let me know if (?:you|there)|is there anything else|i hope (?:this|that) helps|glad (?:i|to)|",
        r"feel free to ask)[^\n]{0,80})$",
    ))
    .unwrap()
});
static INFO_HINT_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\d|\b(?:i|my|we|our|he|she|they|his|her|their|me)\b|[A-Z][a-z]+").unwrap());

/// True when the message is almost certainly not worth remembering.
///
/// Rules (all conservative): empty / whitespace; shorter than `min_chars` AND carries no digit, pronoun or
/// capitalised token; matches the greeting / acknowledgement pattern entirely; assistant boilerplate one-liners
/// ("Sure! Let me know if ...").
pub fn is_probable_chitchat(text: &str) -> bool {
    is_probable_chitchat_min(text, 12)
}

pub fn is_probable_chitchat_min(text: &str, min_chars: usize) -> bool {
    let t = normalize_ws(text);
    if t.is_empty() {
        return true;
    }
    if CHITCHAT_RE.is_match(&t) {
        return true;
    }
    let n = py_len(&t);
    if n < min_chars && !INFO_HINT_RE.is_match(&t) {
        return true;
    }
    n < 120 && ASSISTANT_BOILERPLATE_RE.is_match(&t)
}

/// Truncate to `limit` code points, marking the cut with an ellipsis.
pub fn clip(text: &str, limit: usize) -> String {
    if py_len(text) <= limit {
        return text.to_string();
    }
    format!("{}…", py_rstrip(py_prefix(text, limit.saturating_sub(1))))
}

/// `str.split()` word count.
pub fn word_count(text: &str) -> usize {
    text.split(is_py_space).filter(|p| !p.is_empty()).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stems() {
        for (w, expect) in [
            ("lives", "live"),
            ("running", "run"),
            ("cities", "city"),
            ("visited", "visit"),
            ("planned", "plan"),
            ("buses", "bus"),
            ("glass", "glass"),
            ("hated", "hate"),
            ("cooking", "cook"),
        ] {
            assert_eq!(stem(w), expect, "{w}");
        }
    }

    #[test]
    fn normalisation() {
        assert_eq!(norm_entity("  Melanie's "), "melanie");
        assert_eq!(norm_entity("São Paulo"), "sao paulo");
        assert_eq!(norm_entity("The Beatles'"), "beatles");
        assert_eq!(norm_relation("Lives In"), "lives_in");
        assert_eq!(tokenize("I moved to Berlin in March 2024, it's great"), ["move", "berlin", "march", "2024"]);
        assert_eq!(tokenize("Zürich Müller"), ["zurich", "muller"]);
        assert_eq!(fts_match_string("What is Mel's job?"), "\"mel\" OR \"job\"");
    }

    #[test]
    fn chitchat_gate() {
        for s in ["ok", "thanks!", "lol", "Hi!", "Sure! Let me know if you need anything else.", "good morning", ""] {
            assert!(is_probable_chitchat(s), "{s}");
        }
        for s in ["I moved to Berlin", "My cat is called Luna", "42", "Can you remind me what Mel said about Tokyo?"] {
            assert!(!is_probable_chitchat(s), "{s}");
        }
    }
}
