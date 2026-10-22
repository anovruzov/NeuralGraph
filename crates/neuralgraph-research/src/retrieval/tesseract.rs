//! Port of `retrieval/tesseract.py`: the four-store "tesseract" retrieval engine behind the published LoCoMo
//! retrieval numbers, plus the question predicates the benchmark runner imports.
//!
//! A query is scored by four specialised stores over every node of the session: temporal (topic, event-year,
//! relative-time and month matching), entity (entity/speaker binding), reasoning (keyword breadth) and
//! adversarial (negation-aware). Each store's charges are normalised to [0, 1] and fused with weights from
//! [`detect_query_type`]; temporal neighbours of the first fused results get a small "momentum" boost.
//!
//! Exactness:
//! - Semantic charges use numpy float32 exactly as Python did (`np.array(..., float32)`, `np.linalg.norm`,
//!   float32 division, `np.dot`), through [`crate::numpy`]; on macOS they are bit-identical to Python's.
//! - Python iterated `set`s in two places that reach the output: the node order of the storage (ties in the
//!   charge sorts keep that order) and the novelty score, a float sum over a token set. Both orders were
//!   hash-randomised per process, so Python's own runs differed in tie order and in the last bits of the
//!   novelty term. Here sets are insertion-ordered ([`IndexSet`]): node order is the storage's insertion order,
//!   novelty tokens are summed in order of first occurrence. The golden generator runs the Python reference with
//!   the same orders (ordered storage indexes, ordered novelty token sets), so the fixtures compare exactly.
//! - `node.created_at.year` feeds the event-year logic and `reference_time - created_at` the recency term, so
//!   results depend on when nodes were created (the harnesses used `datetime.now()`): that dependence is kept.
//!
//! Python quirks kept on purpose: temporal momentum boosts the neighbours of the first `top_k` entries of the
//! fused list, which is in insertion order (temporal-store results first), not the `top_k` highest charges;
//! the temporal store extracts topic keywords and proper nouns from the *lower-cased* query, so its proper-noun
//! step never fires; `msg_year` falls back to the `datetime` metadata only when `created_at` is unset, which never
//! happens (Rust nodes always carry `created_at`), so that branch is unreachable. Where Python raised (a naive
//! `reference_time` against aware `created_at`, non-string speaker/datetime metadata, mismatched embedding
//! dimensions), [`TesseractError`] is returned.
//!
//! The four `_expand_morphology*` methods were copies of one function; they all call
//! [`expand_morphology_basic`] here.

use crate::numpy::{norm_f32, sdot};
use crate::pytime::{delta_days, PyDateTime};
use crate::retrieval::data_types::{EdgeType, NeuralNode, NodeRef};
use crate::retrieval::external_retriever::is_open_domain_query;
use crate::retrieval::storage::{Direction, NeuralGraphStorage};
use crate::retrieval::synonym_hash::expand_with_synonyms_extended as expand_with_synonyms;
use crate::temporal_utils::expand_temporal_query;
use fancy_regex::Regex as FRegex;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_int_parse, py_strip, py_truthy};
use regex::Regex;
use serde_json::{Map, Value};
use std::rc::Rc;
use std::sync::LazyLock;

/// A Python set of strings (insertion-ordered; see the module docs).
pub type StrSet = IndexSet<String>;

/// What Python raised inside the scoring code.
#[derive(Clone, Debug, PartialEq)]
pub enum TesseractError {
    /// `TypeError: can't subtract offset-naive and offset-aware datetimes` (`reference_time - created_at`).
    NaiveAwareMismatch,
    /// `AttributeError`: a speaker/producer/datetime metadata value that is truthy but not a string.
    MetadataNotString(String),
    /// `ValueError: shapes not aligned` from `np.dot` (query and node embeddings differ in length).
    ShapeMismatch { query: usize, node: usize },
}

impl std::fmt::Display for TesseractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TesseractError::NaiveAwareMismatch => f.write_str("can't subtract offset-naive and offset-aware datetimes"),
            TesseractError::MetadataNotString(k) => write!(f, "metadata {k:?} is not a string (no attribute 'lower')"),
            TesseractError::ShapeMismatch { query, node } => {
                write!(f, "shapes ({query},) and ({node},) not aligned")
            }
        }
    }
}

impl std::error::Error for TesseractError {}

/// Ranked `(node, charge)` pairs.
pub type Scored = Vec<(NodeRef, f64)>;

type TResult<T> = Result<T, TesseractError>;

/// Python's `\s` also matches U+001C..U+001F; every pattern here goes through this rewrite.
fn py_pattern(p: &str) -> String {
    p.replace(r"\s", r"[\s\x1c-\x1f]")
}

fn re(p: &str) -> Regex {
    Regex::new(&py_pattern(p)).unwrap_or_else(|e| panic!("bad regex {p}: {e}"))
}

fn fre(p: &str) -> FRegex {
    FRegex::new(&py_pattern(p)).unwrap_or_else(|e| panic!("bad regex {p}: {e}"))
}

/// `min(a, b)`: `b` only when strictly smaller (Python returns the first of equal values).
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `max(a, b)`: `b` only when strictly greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn set_of<I: IntoIterator<Item = S>, S: Into<String>>(items: I) -> StrSet {
    items.into_iter().map(Into::into).collect()
}

fn findall(r: &Regex, text: &str) -> Vec<String> {
    r.find_iter(text).map(|m| m.as_str().to_string()).collect()
}

/// `re.findall` of a one-group pattern: the group texts.
fn findall_group(r: &Regex, text: &str) -> Vec<String> {
    r.captures_iter(text).map(|c| c.get(1).map(|m| m.as_str()).unwrap_or("").to_string()).collect()
}

static WORDS3: LazyLock<Regex> = LazyLock::new(|| re(r"\b[a-z]{3,}\b"));
static CAPITALIZED: LazyLock<Regex> = LazyLock::new(|| re(r"\b[A-Z][a-z]+\b"));

// =============================================================================================================
// QUESTION PREDICATES (imported by the benchmark runner)
// =============================================================================================================

#[rustfmt::skip]
const OPEN_DOMAIN_INFER_MARKERS: &[&str] = &[
    "would ", "might ", "could ", " may ", "should ",
    "would be", "might be", "could be", "may be",
    "would likely", "might likely", "could possibly",
    "would probably", "might probably", "could potentially",
    "underlying", "based on", "given", "considering",
    "given that", "based on the", "considering the",
    "in light of", "taking into account", "judging by",
    "from the", "according to",
    "alternative", "instead", "rather than", "as opposed to",
    "what if", "suppose", "imagine", "hypothetically",
    "in place of", "other than",
    "personality", "attributes", "traits", "characteristics",
    "qualities", "nature", "temperament", "disposition",
    "character", "tendencies",
    "be considered", "describe", "characterize",
    "would you describe", "how would you",
    "be seen as", "be regarded as", "be viewed as",
    "likely to", "probably ", "possibly ", "potentially",
    "expected to", "predicted to", "anticipated to",
    "destined to",
    "suited for", "good at", "talented at", "skilled at",
    "capable of", "able to", "fit for",
    "prefer", "enjoy", "interested in", "inclined to",
    "drawn to", "attracted to", "keen on", "fond of",
    "better than", "worse than", "more than", "less than",
    "compared to", "in comparison",
    "think about", "believe about", "feel about",
    "opinion on", "view of", "stance on",
    "why ", "reason for", "because of", "caused by",
    "due to",
    "how does", "what does", "feel like", "think like",
    "emotional",
    "planning to", "hoping to", "aspiring to",
    "aiming to", "striving to",
    "similar to", "like ", "resemble", "akin to",
    "open to", "willing to", "receptive to", "amenable to",
    "impact of", "effect of", "consequence of",
];

/// `should_use_open_domain_infer`: the question contains any open-domain inference marker.
pub fn should_use_open_domain_infer(question: &str) -> bool {
    let q = question.to_lowercase();
    OPEN_DOMAIN_INFER_MARKERS.iter().any(|m| q.contains(m))
}

static OPEN_DOMAIN_HEDGE_RE: LazyLock<Regex> = LazyLock::new(|| {
    re(r"\b(likely|might|would|could|probably|potentially|presumably|suspected|possibly|may|should)\b")
});

/// `looks_open_domain_question`: a hedged question ("would X likely...", "what might Y enjoy").
pub fn looks_open_domain_question(question: &str) -> bool {
    OPEN_DOMAIN_HEDGE_RE.is_match(&question.to_lowercase())
}

static YES_NO_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(is|are|was|were|do|does|did|can|could|should|would|will|has|have|had)\b"));

/// `is_yes_no_question`: starts with an auxiliary verb.
pub fn is_yes_no_question(question: &str) -> bool {
    let lowered = question.to_lowercase();
    YES_NO_RE.is_match(py_strip(&lowered))
}

static LIST_PLURAL_PATTERNS: LazyLock<Vec<FRegex>> = LazyLock::new(|| {
    [
        r"\bwhat\s+(?!is\b|does\b|was\b|has\b|did\b|do\b|are\b|were\b|makes\b|happens\b)\w+s\b",
        r"\bwhich\s+(?!is\b|does\b|was\b|has\b)\w+s\b",
        r"\blist\s+\w+s\b",
        r"\bwhat\s+are\s+\w+s\b",
    ]
    .iter()
    .map(|p| fre(p))
    .collect()
});
static IN_COMMON_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\bin common\b"));

/// `detect_list_question_universal`: `(is_list, list_type)` with `list_type` `"plural"` or `"aggregation"`.
pub fn detect_list_question_universal(question: &str) -> (bool, Option<&'static str>) {
    let q = question.to_lowercase();
    let mut is_list = false;
    let mut list_type: Option<&'static str> = None;
    let list_markers = [
        "what books",
        "what movies",
        "what types",
        "what kinds",
        "what things",
        "which items",
        "all of",
        "list",
        "names of",
        "what are the",
        "what are some",
    ];
    if list_markers.iter().any(|m| q.contains(m)) {
        is_list = true;
        list_type = Some("plural");
    }
    if LIST_PLURAL_PATTERNS.iter().any(|p| p.is_match(&q).unwrap_or(false)) {
        is_list = true;
        list_type = Some("plural");
    }
    if q.contains("what kind of") || q.contains("what kinds of") {
        is_list = true;
        list_type = list_type.or(Some("plural"));
    }
    if IN_COMMON_RE.is_match(&q) || (q.contains("both") && !is_yes_no_question(question)) {
        is_list = true;
        list_type = Some("aggregation");
    }
    if is_list && list_type.is_none() {
        list_type = Some("plural");
    }
    (is_list, list_type)
}

/// `_expand_morphology_basic` (and the stores' identical `_expand_morphology*` copies): a word plus its
/// crude stem and `s`/`ed`/`ing`/`d` variants, keeping alphabetic variants of at least three characters.
pub fn expand_morphology_basic(word: &str) -> StrSet {
    let mut variants: StrSet = set_of([word]);
    let w: Vec<char> = word.to_lowercase().chars().collect();
    let ends = |suffix: &str| w.iter().rev().zip(suffix.chars().rev()).all(|(a, b)| *a == b) && w.len() >= suffix.len();
    let mut base: Vec<char> = w.clone();
    if ends("ed") && w.len() > 4 {
        base = w[..w.len() - 2].to_vec();
        if base.last() == Some(&'i') {
            base.pop();
            base.push('y');
        } else if base.len() >= 2 && base[base.len() - 1] == base[base.len() - 2] {
            base.pop();
        }
    } else if ends("ing") && w.len() > 5 {
        base = w[..w.len() - 3].to_vec();
        if base.len() >= 2 && base[base.len() - 1] == base[base.len() - 2] {
            base.pop();
        }
    } else if ends("ies") && w.len() > 4 {
        base = w[..w.len() - 3].to_vec();
        base.push('y');
    } else if ends("s") && !ends("ss") && w.len() > 3 {
        base = w[..w.len() - 1].to_vec();
    }
    let base_s: String = base.iter().collect();
    variants.insert(base_s.clone());
    if base.len() >= 3 {
        variants.insert(format!("{base_s}s"));
        variants.insert(format!("{base_s}ed"));
        variants.insert(format!("{base_s}ing"));
        if base.last() == Some(&'e') {
            let stem: String = base[..base.len() - 1].iter().collect();
            variants.insert(format!("{stem}ing"));
            variants.insert(format!("{base_s}d"));
        }
    }
    variants.into_iter().filter(|v| v.chars().count() >= 3 && v.chars().all(char::is_alphabetic)).collect()
}

/// `extract_topic_keywords_universal`: content words of the question with morphology and synonym expansion.
pub fn extract_topic_keywords_universal(question: &str) -> StrSet {
    let q = question.to_lowercase();
    let stopwords = [
        "what", "which", "who", "where", "when", "why", "how", "did", "does", "do", "is", "are", "was", "were", "has",
        "have", "had", "will", "would", "could", "should", "the", "a", "an", "to", "for", "in", "on", "at", "of",
        "with", "from", "types", "type", "kinds", "kind", "names", "name", "list", "all", "any",
    ];
    let mut keywords: StrSet = findall(&WORDS3, &q).into_iter().filter(|t| !stopwords.contains(&t.as_str())).collect();
    let mut expanded = StrSet::new();
    for w in &keywords {
        expanded.extend(expand_morphology_basic(w));
    }
    keywords.extend(expanded);
    let synonyms = expand_with_synonyms(&keywords);
    keywords.extend(synonyms);
    keywords
}

static PERSONAL_PRONOUN_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\b(my|our|your|his|her|their|she|he|they|we)\b"));
static DEFINITIONAL_RE: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(what|who|where|when|why|how) (is|are|was|were|do|does|did)\b"));

/// `is_open_domain_world_query`: an open-domain question about the world (no personal pronouns, no names
/// unless definitional) with confidence >= 0.6.
pub fn is_open_domain_world_query(question: &str) -> bool {
    let (is_open, confidence) = is_open_domain_query(question);
    if !is_open {
        return false;
    }
    let q = question.to_lowercase();
    if PERSONAL_PRONOUN_RE.is_match(&q) {
        return false;
    }
    let has_name = CAPITALIZED.is_match(question);
    let definitional = DEFINITIONAL_RE.is_match(&q);
    if has_name && !definitional {
        return false;
    }
    confidence >= 0.6
}

// =============================================================================================================
// NOVELTY
// =============================================================================================================

#[rustfmt::skip]
const NOVELTY_STOPWORDS: &[&str] = &[
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "what", "when", "where", "who",
    "why", "how", "did", "does", "with", "from", "that", "this", "these", "those",
    "they", "their", "them", "she", "he", "his", "its", "been", "were", "have",
    "will", "would", "could", "should", "may", "might", "into",
    "about", "over", "under", "after", "before", "during", "because", "while",
    "then", "than", "there", "here", "also", "just", "really",
];

/// Precomputed novelty weights for surprise-style boosting (`NoveltyContext`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct NoveltyContext {
    pub query_tokens: StrSet,
    pub token_idf: IndexMap<String, f64>,
    pub max_idf: f64,
}

static NOVELTY_TOKEN_RE: LazyLock<Regex> = LazyLock::new(|| re(r"\b[a-z0-9]{3,}\b"));

/// `_tokenize_for_novelty`: distinct 3+ character alphanumeric tokens minus stopwords, in order of first
/// occurrence (Python's set order was arbitrary; see the module docs).
pub fn tokenize_for_novelty(text: &str) -> StrSet {
    findall(&NOVELTY_TOKEN_RE, &text.to_lowercase())
        .into_iter()
        .filter(|w| !NOVELTY_STOPWORDS.contains(&w.as_str()))
        .collect()
}

/// `_build_novelty_context`: smoothed IDF of each query token over the session's nodes.
pub fn build_novelty_context(query_text: &str, nodes: &[NodeRef]) -> Option<NoveltyContext> {
    let query_tokens = tokenize_for_novelty(query_text);
    if query_tokens.is_empty() || nodes.is_empty() {
        return None;
    }
    let mut doc_freq: IndexMap<String, i64> = IndexMap::new();
    for node in nodes {
        for tok in tokenize_for_novelty(&node.borrow().content) {
            *doc_freq.entry(tok).or_insert(0) += 1;
        }
    }
    let total_docs = nodes.len() as f64;
    let mut token_idf = IndexMap::new();
    for tok in &query_tokens {
        let df = doc_freq.get(tok).copied().unwrap_or(0) as f64;
        token_idf.insert(tok.clone(), ((1.0 + total_docs) / (1.0 + df)).ln() + 1.0);
    }
    let max_idf = token_idf.values().copied().fold(None, |acc: Option<f64>, v| Some(acc.map_or(v, |a| py_max(a, v))));
    Some(NoveltyContext { query_tokens, token_idf, max_idf: max_idf.unwrap_or(0.0) })
}

/// `_compute_novelty_charge`: summed IDF of the query tokens present in `content`, relative to the maximum.
pub fn compute_novelty_charge(content: &str, novelty: Option<&NoveltyContext>) -> f64 {
    let Some(novelty) = novelty else { return 0.0 };
    if novelty.query_tokens.is_empty() || novelty.max_idf <= 0.0 {
        return 0.0;
    }
    let content_tokens = tokenize_for_novelty(content);
    if content_tokens.is_empty() {
        return 0.0;
    }
    let mut score = 0.0;
    for tok in &content_tokens {
        if novelty.query_tokens.contains(tok) {
            score += novelty.token_idf.get(tok).copied().unwrap_or(0.0);
        }
    }
    if score <= 0.0 {
        return 0.0;
    }
    let denom = novelty.max_idf * novelty.query_tokens.len().max(1) as f64;
    py_min(1.0, score / denom)
}

/// `_robust_similarity`: clamp to [0, 1] and squash with `tanh(1.5 x)`.
pub fn robust_similarity(similarity: f64) -> f64 {
    let s = py_max(0.0, py_min(1.0, similarity));
    (s * 1.5).tanh()
}

// =============================================================================================================
// QUERY TYPE DETECTION
// =============================================================================================================

/// Query type names (`QueryType`).
pub struct QueryType;

impl QueryType {
    pub const TEMPORAL: &'static str = "temporal";
    pub const ENTITY: &'static str = "entity";
    pub const MULTI_HOP: &'static str = "multi_hop";
    pub const ADVERSARIAL: &'static str = "adversarial";
    pub const OPEN: &'static str = "open";
}

/// Confidence per query type, in the order temporal, entity, multi_hop, adversarial, open.
pub type QueryTypeScores = IndexMap<&'static str, f64>;

struct QueryTypePatterns {
    temporal: Vec<(Regex, f64)>,
    entity: Vec<(Regex, f64)>,
    multihop: Vec<(Regex, f64)>,
    adversarial: Vec<(Regex, f64)>,
    open_definitional: Vec<(FRegex, f64)>,
    open_factual: Vec<(Regex, f64)>,
    session: Vec<(Regex, f64)>,
}

static QT: LazyLock<QueryTypePatterns> = LazyLock::new(|| {
    let plain = |ps: &[(&str, f64)]| ps.iter().map(|(p, w)| (re(p), *w)).collect::<Vec<_>>();
    QueryTypePatterns {
        temporal: plain(&[
            (r"\bwhen\b", 0.6),
            (r"\bhow long\b", 0.5),
            (r"\byesterday\b", 0.4),
            (r"\btoday\b", 0.3),
            (r"\blast week\b", 0.5),
            (r"\blast month\b", 0.5),
            (r"\blast year\b", 0.5),
            (r"\b\d+ years? ago\b", 0.6),
            (r"\b\d+ months? ago\b", 0.5),
            (r"\b\d+ days? ago\b", 0.4),
            (r"\bbefore\b", 0.3),
            (r"\bafter\b", 0.3),
            (r"\bduring\b", 0.3),
            (r"\bfirst\b", 0.2),
            (r"\brecent\b", 0.3),
        ]),
        entity: plain(&[
            (r"\bwhat is\b", 0.5),
            (r"\bwho is\b", 0.5),
            (r"\bwhat does\b", 0.4),
            (r"\bwhere is\b", 0.4),
            (r"\bwhere did\b", 0.5),
            (r"\bwhat's\b", 0.4),
            (r"\bidentity\b", 0.5),
            (r"\bname\b", 0.3),
            (r"\bcareer\b", 0.3),
            (r"\bjob\b", 0.3),
            (r"\bwork\b", 0.2),
            (r"\bfrom\b", 0.2),
        ]),
        multihop: plain(&[
            (r"\bhow many\b", 0.4),
            (r"\ball\b", 0.3),
            (r"\bevery\b", 0.3),
            (r"\bboth\b", 0.3),
            (r"\band\b.*\band\b", 0.4),
            (r"\blist\b", 0.4),
            (r"\bwhat were\b", 0.3),
        ]),
        adversarial: plain(&[
            (r"\bnot\b", 0.4),
            (r"\bnever\b", 0.5),
            (r"\bexcept\b", 0.4),
            (r"\bother than\b", 0.5),
            (r"\bdid .* not\b", 0.5),
            (r"\bdidn't\b", 0.5),
            (r"\bfail\b", 0.3),
            (r"\bwithout\b", 0.3),
        ]),
        open_definitional: [
            (r"^what is (?:a |an |the )?(?!\w+'s)", 0.7),
            (r"^what are (?!the |their |our )", 0.6),
            (r"^define\b", 0.8),
            (r"^explain (?:what|how|why)\b", 0.7),
            (r"^how does .+ work\b", 0.7),
            (r"^why does\b", 0.6),
            (r"^why do\b", 0.6),
            (r"^what causes\b", 0.8),
            (r"^what makes\b", 0.6),
            (r"^what happens when\b", 0.6),
            (r"^describe (?:what|how)\b", 0.5),
        ]
        .iter()
        .map(|(p, w)| (fre(p), *w))
        .collect(),
        open_factual: plain(&[
            (r"how many .+ (?:are there|exist)\b", 0.6),
            (r"when was .+ (?:invented|discovered|founded|created|born|built)\b", 0.7),
            (r"where is .+ located\b", 0.6),
            (r"what (?:country|city|place)\b", 0.5),
            (r"what year\b", 0.4),
            (r"capital of\b", 0.7),
            (r"(?:president|prime minister|leader) of\b", 0.6),
            (r"population of\b", 0.7),
            (r"(?:largest|smallest|oldest|youngest|tallest|shortest|longest|highest|lowest)\b", 0.5),
            (r"meaning of\b", 0.6),
            (r"origin of\b", 0.6),
            (r"history of\b", 0.6),
        ]),
        session: plain(&[
            (r"\bshe\b", 0.2),
            (r"\bhe\b", 0.2),
            (r"\bthey\b", 0.15),
            (r"\bwe\b", 0.15),
            (r"\bour\b", 0.2),
            (r"\bmy\b", 0.2),
            (r"\byour\b", 0.2),
            (r"\btold me\b", 0.3),
            (r"\bsaid\b", 0.2),
            (r"\basked\b", 0.2),
            (r"\bmentioned\b", 0.3),
            (r"\bthe meeting\b", 0.3),
            (r"\bthe conversation\b", 0.3),
            (r"\bearlier\b", 0.2),
        ]),
    }
});

#[rustfmt::skip]
const QUERY_TYPE_SCIENTIFIC_TERMS: &[&str] = &[
    "photosynthesis", "evolution", "gravity", "atom", "molecule",
    "electron", "proton", "neutron", "cell", "dna", "rna",
    "gene", "chromosome", "protein", "enzyme", "virus", "bacteria",
    "planet", "star", "galaxy", "solar", "orbit", "mass",
    "energy", "force", "momentum", "velocity", "acceleration",
    "temperature", "pressure", "volume", "density", "climate",
    "ecosystem", "species", "habitat", "genetics", "quantum",
    "algorithm", "computer", "internet", "network", "database",
];

/// `detect_query_type`: confidence in [0, 1] per [`QueryType`]; several types can be active at once.
pub fn detect_query_type(query: &str) -> QueryTypeScores {
    let q = query.to_lowercase();
    let p = &*QT;
    let sum = |patterns: &[(Regex, f64)]| {
        let mut s = 0.0;
        for (r, w) in patterns {
            if r.is_match(&q) {
                s += w;
            }
        }
        s
    };
    let mut temporal = sum(&p.temporal);
    let mut entity = sum(&p.entity);
    let mut multihop = sum(&p.multihop);
    let mut adversarial = sum(&p.adversarial);
    let mut open = 0.0;
    if let Some((_, w)) = p.open_definitional.iter().find(|(r, _)| r.is_match(&q).unwrap_or(false)) {
        open += w;
    }
    if let Some((_, w)) = p.open_factual.iter().find(|(r, _)| r.is_match(&q)) {
        open += w;
    }
    if QUERY_TYPE_SCIENTIFIC_TERMS.iter().any(|t| q.contains(t)) {
        open += 0.4;
    }
    let mut session_penalty = 0.0;
    let mut has_session_markers = false;
    for (r, w) in &p.session {
        if r.is_match(&q) {
            session_penalty += w;
            has_session_markers = true;
        }
    }
    open = py_max(0.0, open - session_penalty);
    let max_other = [entity, multihop, adversarial].into_iter().fold(temporal, py_max);
    if max_other < 0.3 && !has_session_markers {
        open += 0.3;
    }
    temporal = py_min(1.0, temporal);
    entity = py_min(1.0, entity);
    multihop = py_min(1.0, multihop);
    adversarial = py_min(1.0, adversarial);
    open = py_min(1.0, open);
    [
        (QueryType::TEMPORAL, temporal),
        (QueryType::ENTITY, entity),
        (QueryType::MULTI_HOP, multihop),
        (QueryType::ADVERSARIAL, adversarial),
        (QueryType::OPEN, open),
    ]
    .into_iter()
    .collect()
}

fn score_of(scores: &QueryTypeScores, key: &str) -> f64 {
    scores.get(key).copied().unwrap_or(0.0)
}

// =============================================================================================================
// SHARED NODE HELPERS
// =============================================================================================================

/// `StoreConfig`.
#[derive(Clone, Debug, PartialEq)]
pub struct StoreConfig {
    pub semantic_weight: f64,
    pub temporal_decay_days: f64,
    pub entity_boost: f64,
    pub speaker_boost: f64,
    pub keyword_boost: f64,
    pub novelty_boost: f64,
    pub min_charge: f64,
    pub max_results: usize,
}

impl Default for StoreConfig {
    fn default() -> Self {
        StoreConfig {
            semantic_weight: 0.5,
            temporal_decay_days: 30.0,
            entity_boost: 0.4,
            speaker_boost: 0.5,
            keyword_boost: 0.3,
            novelty_boost: 0.1,
            min_charge: 0.1,
            max_results: 30,
        }
    }
}

/// The query embedding as the stores prepared it: float32, divided by its norm when that exceeds 1e-10.
pub fn prepare_query_embedding(query_embedding: &[f64]) -> Vec<f32> {
    let q: Vec<f32> = query_embedding.iter().map(|v| *v as f32).collect();
    let norm = norm_f32(&q);
    // numpy 2 compares a float32 scalar with a Python float in float32
    if norm > 1e-10_f32 {
        q.iter().map(|v| v / norm).collect()
    } else {
        q
    }
}

/// `_robust_similarity(float(np.dot(query, node_emb / norm(node_emb))))`, 0 without a usable embedding.
pub fn semantic_charge(query: &[f32], node: &NeuralNode) -> TResult<f64> {
    let Some(emb) = node.embedding.as_ref().filter(|e| !e.is_empty()) else { return Ok(0.0) };
    let node_emb: Vec<f32> = emb.iter().map(|v| *v as f32).collect();
    let node_norm = norm_f32(&node_emb);
    if node_norm > 1e-10_f32 {
        if node_emb.len() != query.len() {
            return Err(TesseractError::ShapeMismatch { query: query.len(), node: node_emb.len() });
        }
        let unit: Vec<f32> = node_emb.iter().map(|v| v / node_norm).collect();
        return Ok(robust_similarity(sdot(query, &unit) as f64));
    }
    Ok(0.0)
}

/// `value.lower()` where Python required a string: falsy values read as `""`, other non-strings raised.
fn metadata_lower(key: &str, v: Option<&Value>) -> TResult<String> {
    match v {
        None => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.to_lowercase()),
        Some(v) if !py_truthy(v) => Ok(String::new()),
        Some(_) => Err(TesseractError::MetadataNotString(key.to_string())),
    }
}

/// `node.speaker_id.lower()`: `metadata["speaker"] or metadata["producer_id"] or ""`, lower-cased.
fn speaker_id_lower(node: &NeuralNode) -> TResult<String> {
    let m = &node.metadata;
    if m.is_empty() {
        return Ok(String::new());
    }
    for key in ["speaker", "producer_id"] {
        if let Some(v) = m.get(key).filter(|v| py_truthy(v)) {
            return metadata_lower(key, Some(v));
        }
    }
    Ok(String::new())
}

/// `(metadata.get("producer_id", "") or metadata.get("speaker", "")).lower()` (the entity-style stores read
/// the legacy key first, and a falsy `speaker` that is not a string raised).
fn producer_or_speaker_lower(metadata: &Map<String, Value>) -> TResult<String> {
    match metadata.get("producer_id") {
        Some(v) if py_truthy(v) => metadata_lower("producer_id", Some(v)),
        _ => match metadata.get("speaker") {
            None => Ok(String::new()),
            Some(Value::String(s)) => Ok(s.to_lowercase()),
            Some(_) => Err(TesseractError::MetadataNotString("speaker".into())),
        },
    }
}

/// Keep charges `>= min_charge`, sort descending (stable) and cut to `limit`.
fn rank(mut results: Scored, limit: usize) -> Scored {
    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    results.truncate(limit);
    results
}

fn nodes_or_session(
    storage: &dyn NeuralGraphStorage,
    session_key: &str,
    all_nodes: Option<&[NodeRef]>,
) -> Vec<NodeRef> {
    match all_nodes {
        Some(n) => n.to_vec(),
        None => storage.get_nodes_by_session(session_key, None),
    }
}

// =============================================================================================================
// TEMPORAL STORE
// =============================================================================================================

/// Hippocampus-inspired store for temporal/episodic retrieval (`TemporalStore`).
pub struct TemporalStore {
    storage: Rc<dyn NeuralGraphStorage>,
    pub config: StoreConfig,
}

#[rustfmt::skip]
const TEMPORAL_KEYWORD_PATTERNS: &[&str] = &[
    // basic
    "yesterday", "today", "tomorrow",
    "tonight", "this morning", "this afternoon", "this evening",
    "last night", "last evening",
    // week
    "last week", "this week", "next week",
    "past week", "previous week", "following week",
    "a week ago", "weeks ago", "couple weeks",
    "few weeks", "several weeks",
    // month
    "last month", "this month", "next month",
    "past month", "previous month", "following month",
    "a month ago", "months ago", "couple months",
    "few months", "several months",
    // year
    "last year", "this year", "next year",
    "past year", "previous year", "following year",
    "a year ago", "years ago", "couple years",
    "few years", "several years",
    r"\d{4}",
    r"(?:19|20)\d{2}s",
    // month names
    "january|jan", "february|feb", "march|mar", "april|apr",
    "may", "june|jun", "july|jul", "august|aug",
    "september|sep|sept", "october|oct", "november|nov", "december|dec",
    // day names
    "monday|mon", "tuesday|tue|tues", "wednesday|wed",
    "thursday|thu|thur|thurs", "friday|fri",
    "saturday|sat", "sunday|sun",
    // relative days
    "last monday", "last tuesday", "last wednesday", "last thursday",
    "last friday", "last saturday", "last sunday",
    "next monday", "next tuesday", "next wednesday", "next thursday",
    "next friday", "next saturday", "next sunday",
    "this monday", "this tuesday", "this wednesday", "this thursday",
    "this friday", "this saturday", "this sunday",
    // time of day
    r"\d{1,2}:\d{2}", r"\d{1,2}\s*(?:am|pm)",
    "morning", "afternoon", "evening", "night", "midnight", "noon",
    "dawn", "dusk", "sunrise", "sunset",
    // durations
    r"\d+\s*(?:day|days|week|weeks|month|months|year|years)",
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:day|days|week|weeks|month|months|year|years)",
    r"(?:a|an)\s*(?:day|week|month|year)",
    "couple of (?:days|weeks|months|years)",
    "few (?:days|weeks|months|years)",
    "several (?:days|weeks|months|years)",
    // sequence
    "before", "after", "during", "while", "since", "until",
    "prior to", "following", "preceding", "subsequent",
    "earlier", "later", "previously", "afterwards",
    // seasons
    "spring", "summer", "fall", "autumn", "winter",
    "last spring", "last summer", "last fall", "last autumn", "last winter",
    "this spring", "this summer", "this fall", "this autumn", "this winter",
    "next spring", "next summer", "next fall", "next autumn", "next winter",
    // holidays/events
    "christmas", "thanksgiving", "easter", "halloween",
    "new year", "new year's", "valentine", "independence day",
    "birthday", "anniversary", "wedding", "graduation",
    "weekend", "weekday", "holiday", "vacation",
    // date formats
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    r"\d{4}[/-]\d{2}[/-]\d{2}",
    r"\d{1,2}(?:st|nd|rd|th)",
    // frequency
    "daily", "weekly", "monthly", "yearly", "annually",
    "every day", "every week", "every month", "every year",
    "once", "twice", "thrice",
    "always", "never", "sometimes", "often", "rarely",
    "frequently", "occasionally", "regularly", "seldom",
];

static TEMPORAL_KEYWORD_RES: LazyLock<Vec<Regex>> =
    LazyLock::new(|| TEMPORAL_KEYWORD_PATTERNS.iter().map(|p| re(&format!("(?i){p}"))).collect());

const MONTHS: [&str; 12] = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
];

struct TemporalPatterns {
    explicit_year: Regex,
    explicit_year_query: Regex,
    one_year_ago: Regex,
    words_years_ago: Vec<(Regex, i64)>,
    digits_years_ago: Regex,
    back_in: Regex,
    in_year: Regex,
    during_year: Regex,
    around_year: Regex,
    decade: Regex,
    the_decade: Regex,
    when_i_was: Regex,
    in_my: Regex,
    years_back: Regex,
    how_long: Regex,
    how_old: Regex,
    asks_past: Regex,
    year_20xx: Regex,
    /// The ordered checks of `_extract_relative_time_type`: the first pattern that matches decides, so one
    /// `RegexSet` pass (lowest matching index) gives the same answer as trying them in turn.
    relative: (regex::RegexSet, Vec<String>),
}

static TP: LazyLock<TemporalPatterns> = LazyLock::new(|| {
    let year_words: [(&str, i64); 17] = [
        ("two", 2),
        ("three", 3),
        ("four", 4),
        ("five", 5),
        ("six", 6),
        ("seven", 7),
        ("eight", 8),
        ("nine", 9),
        ("ten", 10),
        ("eleven", 11),
        ("twelve", 12),
        ("fifteen", 15),
        ("twenty", 20),
        ("couple", 2),
        ("few", 3),
        ("several", 5),
        ("many", 10),
    ];
    TemporalPatterns {
        explicit_year: re(r"\b((?:19|20)\d{2})\b"),
        explicit_year_query: re(r"\b(?:19|20)\d{2}\b"),
        one_year_ago: re(r"\b(?:last year|a year ago|one year ago)\b"),
        words_years_ago: year_words.iter().map(|(w, n)| (re(&format!(r"\b{w}\s+years?\s+ago\b")), *n)).collect(),
        digits_years_ago: re(r"\b(\d+)\s+years?\s+ago\b"),
        back_in: re(r"\bback in (?:the year )?(\d{4})\b"),
        in_year: re(r"\bin (\d{4})(?:\s|,|\.|\b)"),
        during_year: re(r"\bduring (?:the year )?(\d{4})\b"),
        around_year: re(r"\b(?:around|circa|about) (\d{4})\b"),
        decade: re(r"\b(?:early|mid|late)\s*((?:19|20)\d{2})s?\b"),
        the_decade: re(r"\bthe\s*((?:19|20)\d{2})s\b"),
        when_i_was: re(r"\bwhen i was (?:younger|a child|a kid|little|growing up)\b"),
        in_my: re(r"\bin my (?:youth|childhood|teens|twenties)\b"),
        years_back: re(r"\byears?\s+(?:back|prior|earlier)\b"),
        how_long: re(r"\bhow long\b"),
        how_old: re(r"\bhow old\b|\bage\b"),
        asks_past: re(r"\b(when did|how long ago|when was)\b"),
        year_20xx: re(r"\b(20\d{2})\b"),
        relative: relative_time_patterns(),
    }
});

/// The ordered `(pattern, type)` checks of `_extract_relative_time_type`.
fn relative_time_patterns() -> (regex::RegexSet, Vec<String>) {
    let mut v: Vec<(String, String)> = vec![
        (r"\b(yesterday|last night|the night before|previous day)\b".into(), "day_before".into()),
        (r"\b(today|this morning|this afternoon|this evening|tonight)\b".into(), "today".into()),
        (r"\b(tomorrow|tomorrow morning|tomorrow night)\b".into(), "day_after".into()),
        (r"\b(day before yesterday|two days ago|2 days ago)\b".into(), "two_days_before".into()),
    ];
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] {
        v.push((format!(r"\b(?:last|past|previous)\s+{day}\b"), format!("{day}_before")));
        v.push((format!(r"\bthis\s+{day}\b"), format!("{day}_current")));
        v.push((format!(r"\bnext\s+{day}\b"), format!("{day}_after")));
    }
    let fixed: [(&str, &str); 20] = [
        (r"\b(?:last|past|previous)\s+weekend\b", "weekend_before"),
        (r"\bthis\s+weekend\b", "weekend_current"),
        (r"\bnext\s+weekend\b", "weekend_after"),
        (r"\b(?:last|past|previous)\s+week\b", "week_before"),
        (r"\bthis\s+week\b", "this_week"),
        (r"\bnext\s+week\b", "next_week"),
        (r"\b(?:a|one)\s+week\s+ago\b", "week_before"),
        (r"\b(?:two|2)\s+weeks?\s+ago\b", "two_weeks_before"),
        (r"\b(?:few|several|couple(?:\s+of)?)\s+weeks?\s+ago\b", "few_weeks_before"),
        (r"\b(?:last|past|previous)\s+month\b", "month_before"),
        (r"\bthis\s+month\b", "this_month"),
        (r"\bnext\s+month\b", "next_month"),
        (r"\b(?:a|one)\s+month\s+ago\b", "month_before"),
        (r"\b(?:two|2)\s+months?\s+ago\b", "two_months_before"),
        (r"\b(?:few|several|couple(?:\s+of)?)\s+months?\s+ago\b", "few_months_before"),
        // placeholders replaced below by the month loop; kept in order
        ("", ""),
        (r"\b(?:last|past|previous)\s+year\b", "year_before"),
        (r"\bthis\s+year\b", "this_year"),
        (r"\bnext\s+year\b", "next_year"),
        (r"\b(?:a|one)\s+year\s+ago\b", "year_before"),
    ];
    for (p, t) in fixed {
        if p.is_empty() {
            for month in MONTHS {
                v.push((format!(r"\b(?:last|past|previous)\s+{month}\b"), format!("{month}_before")));
                v.push((format!(r"\bthis\s+{month}\b"), format!("{month}_current")));
                v.push((format!(r"\bnext\s+{month}\b"), format!("{month}_after")));
                v.push((format!(r"\bin\s+{month}\b"), format!("{month}_reference")));
            }
            continue;
        }
        v.push((p.into(), t.into()));
    }
    v.push((r"\b(?:two|2)\s+years?\s+ago\b".into(), "two_years_before".into()));
    v.push((r"\b(?:few|several|couple(?:\s+of)?)\s+years?\s+ago\b".into(), "few_years_before".into()));
    for season in ["spring", "summer", "fall", "autumn", "winter"] {
        v.push((format!(r"\b(?:last|past|previous)\s+{season}\b"), format!("{season}_before")));
        v.push((format!(r"\bthis\s+{season}\b"), format!("{season}_current")));
        v.push((format!(r"\bnext\s+{season}\b"), format!("{season}_after")));
    }
    for holiday in ["christmas", "thanksgiving", "easter", "halloween", "new year"] {
        let name = holiday.replace(' ', "_");
        v.push((format!(r"\b(?:last|past|previous)\s+{holiday}\b"), format!("{name}_before")));
        v.push((format!(r"\bthis\s+{holiday}\b"), format!("{name}_current")));
        v.push((format!(r"\bnext\s+{holiday}\b"), format!("{name}_after")));
    }
    let general: [(&str, &str); 9] = [
        (r"\b(?:recently|lately|just now|just)\b", "recent"),
        (r"\b(?:soon|shortly|in a bit|later)\b", "soon"),
        (r"\b(?:long ago|ages ago|a while back|way back)\b", "long_ago"),
        (r"\b(?:earlier|before|previously|prior)\b", "earlier"),
        (r"\b(?:afterwards|after|subsequently|later on)\b", "afterwards"),
        (r"\b(?:morning|in the morning)\b", "morning"),
        (r"\b(?:afternoon|in the afternoon)\b", "afternoon"),
        (r"\b(?:evening|in the evening)\b", "evening"),
        (r"\b(?:night|at night|nighttime)\b", "night"),
    ];
    v.extend(general.iter().map(|(p, t)| (p.to_string(), t.to_string())));
    let set = regex::RegexSet::new(v.iter().map(|(p, _)| py_pattern(p))).expect("valid regexes");
    (set, v.into_iter().map(|(_, t)| t).collect())
}

impl TemporalStore {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<StoreConfig>) -> Self {
        let config = config.unwrap_or(StoreConfig {
            temporal_decay_days: 14.0,
            semantic_weight: 0.3,
            entity_boost: 0.3,
            speaker_boost: 0.4,
            ..StoreConfig::default()
        });
        TemporalStore { storage, config }
    }

    /// Retrieve with temporal weighting (topic, event year, relative time, month, entities, recency).
    #[allow(clippy::too_many_arguments)]
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        reference_time: Option<&PyDateTime>,
        limit: usize,
        all_nodes: Option<&[NodeRef]>,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<Scored> {
        let query = prepare_query_embedding(query_embedding);
        let query_lower = query_text.to_lowercase();
        let temporal_keywords = self.extract_temporal_keywords(&query_lower);
        let decay_days = self.adjust_temporal_decay(&query_lower, &temporal_keywords);
        let mut topic_keywords = self.extract_topic_keywords(&query_lower);
        let common = ["What", "When", "Where", "Who", "Why", "How", "Did", "Does", "The"];
        let query_entities: StrSet = findall(&CAPITALIZED, query_text)
            .into_iter()
            .filter(|e| !common.contains(&e.as_str()))
            .map(|e| e.to_lowercase())
            .collect();
        topic_keywords.retain(|k| !query_entities.contains(k));

        let nodes = nodes_or_session(self.storage.as_ref(), session_key, all_nodes);
        if nodes.is_empty() {
            return Ok(Vec::new());
        }
        let mut results = Vec::new();
        for node in &nodes {
            let charge = self.compute_temporal_charge(
                &node.borrow(),
                &query,
                &query_lower,
                &temporal_keywords,
                &topic_keywords,
                &query_entities,
                reference_time,
                decay_days,
                novelty_context,
            )?;
            if charge >= self.config.min_charge {
                results.push((node.clone(), charge));
            }
        }
        Ok(rank(results, limit))
    }

    /// `_extract_temporal_keywords`: every temporal expression found in the query (lower-cased matches).
    pub fn extract_temporal_keywords(&self, query: &str) -> StrSet {
        let query_lower = query.to_lowercase();
        let mut keywords = StrSet::new();
        for r in TEMPORAL_KEYWORD_RES.iter() {
            for m in r.find_iter(&query_lower) {
                keywords.insert(m.as_str().to_lowercase());
            }
        }
        keywords
    }

    /// `_adjust_temporal_decay`: longer decay for explicit dates, shorter for "today"-style recency.
    pub fn adjust_temporal_decay(&self, query_lower: &str, temporal_keywords: &StrSet) -> f64 {
        let mut decay_days = self.config.temporal_decay_days;
        let has_explicit_year = TP.explicit_year_query.is_match(query_lower);
        let month_names = [
            "january",
            "jan",
            "february",
            "feb",
            "march",
            "mar",
            "april",
            "apr",
            "may",
            "june",
            "jun",
            "july",
            "jul",
            "august",
            "aug",
            "september",
            "sep",
            "sept",
            "october",
            "oct",
            "november",
            "nov",
            "december",
            "dec",
        ];
        let has_explicit_month = month_names.iter().any(|m| temporal_keywords.contains(*m));
        if has_explicit_year
            || has_explicit_month
            || query_lower.contains("last year")
            || query_lower.contains("years ago")
        {
            decay_days *= 1.75;
        }
        let recency_terms = [
            "today",
            "yesterday",
            "this morning",
            "this afternoon",
            "this evening",
            "last night",
            "recent",
            "recently",
            "just now",
            "earlier today",
        ];
        if recency_terms.iter().any(|t| query_lower.contains(t)) {
            decay_days *= 0.6;
        }
        py_max(3.0, py_min(365.0, decay_days))
    }

    /// `_extract_event_year_from_content`: the year the event happened (explicit or resolved from "last year",
    /// "three years ago", "back in 2019", ...), `None` when the content has no year reference.
    pub fn extract_event_year_from_content(&self, content: &str, msg_year: i64) -> Option<i64> {
        let c = content.to_lowercase();
        let t = &*TP;
        let explicit: Vec<i64> = findall_group(&t.explicit_year, &c).iter().filter_map(|y| py_int_parse(y)).collect();
        if let Some(min) = explicit.iter().min() {
            return Some(*min);
        }
        if t.one_year_ago.is_match(&c) {
            return Some(msg_year - 1);
        }
        for (r, n) in &t.words_years_ago {
            if r.is_match(&c) {
                return Some(msg_year - n);
            }
        }
        if let Some(m) = t.digits_years_ago.captures(&c) {
            if let Some(n) = py_int_parse(&m[1]) {
                return Some(msg_year - n);
            }
        }
        if let Some(m) = t.back_in.captures(&c) {
            return py_int_parse(&m[1]);
        }
        if let Some(m) = t.in_year.captures(&c) {
            if let Some(year) = py_int_parse(&m[1]) {
                if year <= msg_year {
                    return Some(year);
                }
            }
        }
        for r in [&t.during_year, &t.around_year, &t.decade, &t.the_decade] {
            if let Some(m) = r.captures(&c) {
                return py_int_parse(&m[1]);
            }
        }
        if t.when_i_was.is_match(&c) {
            return Some(msg_year - 20);
        }
        if t.in_my.is_match(&c) {
            return Some(msg_year - 15);
        }
        if t.years_back.is_match(&c) {
            return Some(msg_year - 3);
        }
        None
    }

    /// `_extract_relative_time_type`: the category of the first relative time reference in `content`.
    pub fn extract_relative_time_type(&self, content: &str) -> Option<String> {
        let c = content.to_lowercase();
        let (set, types) = &TP.relative;
        set.matches(&c).iter().next().map(|i| types[i].clone())
    }

    /// `_relative_time_matches_query`: boost when the content's relative time matches what the query asks.
    pub fn relative_time_matches_query(&self, relative_type: Option<&str>, query_text: &str) -> f64 {
        let Some(rt) = relative_type.filter(|t| !t.is_empty()) else { return 0.0 };
        let q = query_text.to_lowercase();
        if q.contains("week") && rt == "week_before" {
            return 1.5;
        }
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] {
            if q.contains(day) && rt == format!("{day}_before") {
                return 2.0;
            }
        }
        if (q.contains("yesterday") || q.contains("day before")) && rt == "day_before" {
            return 1.5;
        }
        0.3
    }

    /// `_extract_topic_keywords`: the event words a temporal question is about, with time-unit anchors for
    /// duration questions, morphology and synonyms.
    pub fn extract_topic_keywords(&self, query: &str) -> StrSet {
        let stopwords = [
            "when", "did", "does", "do", "is", "was", "were", "are", "has", "have", "had", "go", "going", "went",
            "get", "got", "getting", "buy", "bought", "buying", "the", "a", "an", "to", "for", "in", "on", "at", "of",
            "with", "from", "how", "long", "what", "where", "who", "why", "which", "and", "or", "but", "she", "he",
            "her", "his", "they", "their", "it", "its", "this", "that", "plan", "planning", "planned", "join",
            "joined", "joining", "make", "made", "making", "take", "took", "taking", "give", "gave", "giving",
        ];
        let lower = query.to_lowercase();
        let mut topics: StrSet =
            findall(&WORDS3, &lower).into_iter().filter(|w| !stopwords.contains(&w.as_str())).collect();
        if TP.how_long.is_match(&lower) || TP.how_old.is_match(&lower) {
            topics.extend(set_of(["year", "years", "month", "months", "week", "weeks", "day", "days"]));
        }
        let mut expanded = StrSet::new();
        for w in &topics {
            expanded.extend(expand_morphology_basic(w));
        }
        topics.extend(expanded);
        let synonyms = expand_with_synonyms(&topics);
        topics.extend(synonyms);
        let skip = ["When", "What", "Where", "Who", "Which", "How", "Did", "Does", "The"];
        for pn in findall(&CAPITALIZED, query) {
            if !skip.contains(&pn.as_str()) {
                topics.insert(pn.to_lowercase());
            }
        }
        topics
    }

    /// `_compute_temporal_charge`.
    #[allow(clippy::too_many_arguments)]
    pub fn compute_temporal_charge(
        &self,
        node: &NeuralNode,
        query_embedding: &[f32],
        query_text: &str,
        temporal_keywords: &StrSet,
        topic_keywords: &StrSet,
        query_entities: &StrSet,
        reference_time: Option<&PyDateTime>,
        decay_days: f64,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<f64> {
        let content_lower = node.content.to_lowercase();
        let msg_datetime_str = if node.metadata.is_empty() {
            String::new()
        } else {
            metadata_lower("datetime", node.metadata.get("datetime"))?
        };

        // 1. month filter
        let mut temporal_period_charge = 0.0;
        let query_months: Vec<&str> = MONTHS.iter().copied().filter(|m| temporal_keywords.contains(*m)).collect();
        if !query_months.is_empty() && !msg_datetime_str.is_empty() {
            temporal_period_charge = if query_months.iter().any(|m| msg_datetime_str.contains(m)) { 0.5 } else { -0.3 };
        }

        // 2. topic matching
        let mut topic_charge = 0.0;
        if !topic_keywords.is_empty() {
            let topic_words: Vec<&String> = topic_keywords.iter().filter(|k| !MONTHS.contains(&k.as_str())).collect();
            if !topic_words.is_empty() {
                let matches = topic_words.iter().filter(|kw| content_lower.contains(kw.as_str())).count();
                if matches == 0 {
                    topic_charge = -1.0;
                } else {
                    let critical = [
                        "birthday",
                        "parade",
                        "concert",
                        "wedding",
                        "anniversary",
                        "funeral",
                        "graduation",
                        "ceremony",
                    ];
                    let critical_words: Vec<&&String> =
                        topic_words.iter().filter(|k| critical.contains(&k.as_str())).collect();
                    let has_critical = !critical_words.is_empty();
                    let critical_in_content = critical_words.iter().any(|k| content_lower.contains(k.as_str()));
                    topic_charge = if has_critical && !critical_in_content {
                        -1.5
                    } else if has_critical && critical_in_content {
                        3.0
                    } else {
                        py_min(2.5, matches as f64 * 1.0)
                    };
                }
            }
        }

        // 3. entity matching (content or speaker)
        let mut entity_charge = 0.0;
        if !query_entities.is_empty() {
            let speaker = speaker_id_lower(node)?;
            let entity_matches = query_entities
                .iter()
                .filter(|e| content_lower.contains(e.as_str()) || speaker.contains(e.as_str()))
                .count();
            if entity_matches > 0 {
                entity_charge = py_min(1.5, entity_matches as f64 * 0.6);
            }
        }

        // 4. semantic similarity
        let semantic_charge = semantic_charge(query_embedding, node)?;

        // 5. temporal keywords in content
        let mut temporal_charge = 0.0;
        for keyword in temporal_keywords {
            if content_lower.contains(keyword.as_str()) {
                temporal_charge += 0.2;
            }
        }
        temporal_charge = py_min(1.0, temporal_charge);

        // 6. relative time matching
        let mut grounded_charge = 0.0;
        if let Some(rt) = self.extract_relative_time_type(&content_lower) {
            grounded_charge = self.relative_time_matches_query(Some(&rt), query_text);
            let q = query_text.to_lowercase();
            if (rt == "week_before" || rt == "this_week") && q.contains("week") {
                grounded_charge += 0.5;
            }
            if rt == "day_before" && ["yesterday", "night", "day"].iter().any(|w| q.contains(w)) {
                grounded_charge += 0.5;
            }
        }

        // 7. recency
        let mut recency_charge = 0.5;
        if let Some(reference) = reference_time {
            let diff = reference.sub(&node.created_at).map_err(|_| TesseractError::NaiveAwareMismatch)?;
            let time_diff = delta_days(diff).abs();
            recency_charge = (-(time_diff as f64) / decay_days).exp();
        }

        // 7.5 novelty
        let novelty_charge = compute_novelty_charge(&node.content, novelty_context);

        // 8. event year
        let mut event_year_charge = 0.0;
        let query_years: Vec<i64> =
            findall_group(&TP.year_20xx, query_text).iter().filter_map(|y| py_int_parse(y)).collect();
        // `msg_year = 2023` / the `datetime` metadata fallback applied only to nodes without created_at.
        let msg_year = node.created_at.year() as i64;
        let event_year = self.extract_event_year_from_content(&content_lower, msg_year).filter(|y| *y != 0);
        let query_asks_past = TP.asks_past.is_match(&query_text.to_lowercase());
        if let (false, Some(ey)) = (query_years.is_empty(), event_year) {
            event_year_charge = if query_years.contains(&ey) { 1.5 } else { -0.3 };
        } else if let (Some(ey), true) = (event_year, query_asks_past) {
            event_year_charge = if ey < msg_year { 1.2 } else { 0.5 };
        } else if event_year.is_some() {
            event_year_charge = 0.4;
        } else if query_asks_past && content_lower.contains("last year") {
            event_year_charge = 0.8;
        }

        Ok(topic_charge * 0.32
            + event_year_charge * 0.18
            + grounded_charge * 0.15
            + temporal_period_charge * 0.12
            + entity_charge * 0.10
            + semantic_charge * 0.06
            + temporal_charge * 0.05
            + recency_charge * 0.02
            + novelty_charge * self.config.novelty_boost)
    }
}

// =============================================================================================================
// ENTITY STORE
// =============================================================================================================

/// Neocortex-inspired store for semantic fact retrieval (`EntityStore`).
pub struct EntityStore {
    storage: Rc<dyn NeuralGraphStorage>,
    pub config: StoreConfig,
}

impl EntityStore {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<StoreConfig>) -> Self {
        let config = config.unwrap_or(StoreConfig {
            semantic_weight: 0.35,
            entity_boost: 0.6,
            speaker_boost: 0.8,
            keyword_boost: 0.4,
            ..StoreConfig::default()
        });
        EntityStore { storage, config }
    }

    /// Retrieve with entity/speaker weighting.
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        all_nodes: Option<&[NodeRef]>,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<Scored> {
        let query = prepare_query_embedding(query_embedding);
        let query_entities = self.extract_entities(query_text);
        let query_keywords = self.extract_keywords(query_text);
        let nodes = nodes_or_session(self.storage.as_ref(), session_key, all_nodes);
        if nodes.is_empty() {
            return Ok(Vec::new());
        }
        let mut results = Vec::new();
        for node in &nodes {
            let charge =
                self.compute_entity_charge(&node.borrow(), &query, &query_entities, &query_keywords, novelty_context)?;
            if charge >= self.config.min_charge {
                results.push((node.clone(), charge));
            }
        }
        Ok(rank(results, limit))
    }

    /// `_extract_entities`: capitalised words that are not question words or function words.
    pub fn extract_entities(&self, text: &str) -> StrSet {
        let common = [
            "What", "When", "Where", "Who", "Why", "How", "Did", "Does", "Is", "Are", "Was", "Were", "Has", "Have",
            "Had", "The", "And", "But", "Or", "From", "To", "In", "On", "At", "For", "With",
        ];
        findall(&CAPITALIZED, text).into_iter().filter(|e| !common.contains(&e.as_str())).collect()
    }

    /// `_extract_keywords`: content words with morphology and synonyms, plus the query's proper nouns.
    pub fn extract_keywords(&self, text: &str) -> StrSet {
        let stopwords = [
            "the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "out",
            "has", "what", "when", "where", "who", "why", "how", "did", "does",
        ];
        let mut keywords: StrSet =
            findall(&WORDS3, &text.to_lowercase()).into_iter().filter(|w| !stopwords.contains(&w.as_str())).collect();
        let mut expanded = StrSet::new();
        for w in &keywords {
            expanded.extend(expand_morphology_basic(w));
        }
        keywords.extend(expanded);
        let synonyms = expand_with_synonyms(&keywords);
        keywords.extend(synonyms);
        let skip = [
            "What", "When", "Where", "Who", "Why", "How", "Did", "Does", "Is", "Are", "Was", "Were", "Has", "Have",
            "Had", "The",
        ];
        for pn in findall(&CAPITALIZED, text) {
            if !skip.contains(&pn.as_str()) {
                keywords.insert(pn.to_lowercase());
            }
        }
        keywords
    }

    /// `_compute_entity_charge`.
    pub fn compute_entity_charge(
        &self,
        node: &NeuralNode,
        query_embedding: &[f32],
        query_entities: &StrSet,
        query_keywords: &StrSet,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<f64> {
        let (semantic, entity, speaker, keyword) =
            binding_charges(node, query_embedding, query_entities, query_keywords)?;
        let novelty_charge = compute_novelty_charge(&node.content, novelty_context);
        let c = &self.config;
        let mut total = semantic * c.semantic_weight
            + entity * c.entity_boost
            + speaker * c.speaker_boost
            + keyword * c.keyword_boost
            + novelty_charge * c.novelty_boost;
        if speaker > 0.5 && semantic > 0.2 {
            total *= 1.5;
            if keyword > 0.3 {
                total *= 1.2;
            }
        }
        Ok(total)
    }
}

/// The semantic, entity, speaker and keyword charges shared by the entity and reasoning stores.
fn binding_charges(
    node: &NeuralNode,
    query_embedding: &[f32],
    query_entities: &StrSet,
    query_keywords: &StrSet,
) -> TResult<(f64, f64, f64, f64)> {
    let content_lower = node.content.to_lowercase();
    let semantic = semantic_charge(query_embedding, node)?;
    let (entity, speaker) = entity_and_speaker(node, &content_lower, query_entities)?;
    let mut keyword = 0.0;
    if !query_keywords.is_empty() {
        let matches = query_keywords.iter().filter(|k| content_lower.contains(k.as_str())).count();
        keyword = py_min(1.0, matches as f64 / query_keywords.len().max(1) as f64);
    }
    Ok((semantic, entity, speaker, keyword))
}

/// Entity binding (share of query entities in the content) and speaker binding (speaker is a query entity).
fn entity_and_speaker(node: &NeuralNode, content_lower: &str, query_entities: &StrSet) -> TResult<(f64, f64)> {
    let mut entity = 0.0;
    if !query_entities.is_empty() {
        let matches = query_entities.iter().filter(|e| content_lower.contains(&e.to_lowercase())).count();
        entity = py_min(1.0, matches as f64 / query_entities.len() as f64);
    }
    let mut speaker_charge = 0.0;
    if !node.metadata.is_empty() && !query_entities.is_empty() {
        let speaker = producer_or_speaker_lower(&node.metadata)?;
        if query_entities.iter().any(|e| e.to_lowercase() == speaker) {
            speaker_charge = 1.0;
        }
    }
    Ok((entity, speaker_charge))
}

// =============================================================================================================
// REASONING STORE
// =============================================================================================================

/// Prefrontal-inspired store for multi-hop breadth (`ReasoningStore`).
pub struct ReasoningStore {
    storage: Rc<dyn NeuralGraphStorage>,
    pub config: StoreConfig,
}

impl ReasoningStore {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<StoreConfig>) -> Self {
        let config = config.unwrap_or(StoreConfig {
            semantic_weight: 0.5,
            entity_boost: 0.4,
            speaker_boost: 0.3,
            keyword_boost: 0.4,
            min_charge: 0.08,
            max_results: 60,
            ..StoreConfig::default()
        });
        ReasoningStore { storage, config }
    }

    /// Retrieve with a breadth-first weighting.
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        all_nodes: Option<&[NodeRef]>,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<Scored> {
        let query = prepare_query_embedding(query_embedding);
        let query_entities = self.extract_entities(query_text);
        let query_keywords = self.extract_keywords(query_text);
        let nodes = nodes_or_session(self.storage.as_ref(), session_key, all_nodes);
        if nodes.is_empty() {
            return Ok(Vec::new());
        }
        let mut results = Vec::new();
        for node in &nodes {
            let charge = self.compute_reasoning_charge(
                &node.borrow(),
                &query,
                &query_entities,
                &query_keywords,
                novelty_context,
            )?;
            if charge >= self.config.min_charge {
                results.push((node.clone(), charge));
            }
        }
        Ok(rank(results, limit))
    }

    /// `_extract_entities`.
    pub fn extract_entities(&self, text: &str) -> StrSet {
        let common = [
            "What", "When", "Where", "Who", "Why", "How", "Did", "Does", "Is", "Are", "Was", "Were", "Has", "Have",
            "Had", "The", "And",
        ];
        findall(&CAPITALIZED, text).into_iter().filter(|e| !common.contains(&e.as_str())).collect()
    }

    /// `_extract_keywords`: content words with morphology and synonyms, plus proper nouns.
    pub fn extract_keywords(&self, text: &str) -> StrSet {
        let stopwords = ["the", "and", "for", "are", "but", "not", "you", "all"];
        let mut keywords: StrSet =
            findall(&WORDS3, &text.to_lowercase()).into_iter().filter(|w| !stopwords.contains(&w.as_str())).collect();
        let mut expanded = StrSet::new();
        for w in &keywords {
            expanded.extend(expand_morphology_basic(w));
        }
        keywords.extend(expanded);
        let synonyms = expand_with_synonyms(&keywords);
        keywords.extend(synonyms);
        let skip = ["What", "When", "Where", "Who", "Which", "How", "Did", "Does", "The", "Many"];
        for pn in findall(&CAPITALIZED, text) {
            if !skip.contains(&pn.as_str()) {
                keywords.insert(pn.to_lowercase());
            }
        }
        keywords
    }

    /// `_compute_reasoning_charge`.
    pub fn compute_reasoning_charge(
        &self,
        node: &NeuralNode,
        query_embedding: &[f32],
        query_entities: &StrSet,
        query_keywords: &StrSet,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<f64> {
        let (semantic, entity, speaker, keyword) =
            binding_charges(node, query_embedding, query_entities, query_keywords)?;
        let novelty_charge = compute_novelty_charge(&node.content, novelty_context);
        let c = &self.config;
        Ok(semantic * c.semantic_weight
            + entity * c.entity_boost
            + speaker * c.speaker_boost
            + keyword * c.keyword_boost
            + novelty_charge * c.novelty_boost)
    }
}

// =============================================================================================================
// ADVERSARIAL STORE
// =============================================================================================================

/// Orbitofrontal-inspired store for negation-aware retrieval (`AdversarialStore`).
pub struct AdversarialStore {
    storage: Rc<dyn NeuralGraphStorage>,
    pub config: StoreConfig,
}

#[rustfmt::skip]
const NEGATION_PATTERNS: &[&str] = &[
    r"not\s+(\w+)", r"never\s+(\w+)", r"no\s+(\w+)", r"none\s+(\w+)",
    r"didn't\s+(\w+)", r"doesn't\s+(\w+)", r"don't\s+(\w+)", r"wasn't\s+(\w+)", r"weren't\s+(\w+)",
    r"isn't\s+(\w+)", r"aren't\s+(\w+)", r"hasn't\s+(\w+)", r"haven't\s+(\w+)", r"hadn't\s+(\w+)",
    r"won't\s+(\w+)", r"wouldn't\s+(\w+)", r"couldn't\s+(\w+)", r"shouldn't\s+(\w+)", r"can't\s+(\w+)",
    r"except\s+(\w+)", r"except\s+for\s+(\w+)", r"other\s+than\s+(\w+)", r"apart\s+from\s+(\w+)",
    r"besides\s+(\w+)", r"excluding\s+(\w+)", r"but\s+not\s+(\w+)", r"save\s+for\s+(\w+)",
    r"without\s+(\w+)", r"lacking\s+(\w+)", r"missing\s+(\w+)", r"absent\s+(\w+)",
    r"instead\s+of\s+(\w+)", r"rather\s+than\s+(\w+)",
    r"deny\s+(\w+)", r"denied\s+(\w+)", r"refuse\s+(\w+)", r"refused\s+(\w+)", r"reject\s+(\w+)", r"rejected\s+(\w+)",
];

static NEGATION_RES: LazyLock<Vec<Regex>> = LazyLock::new(|| NEGATION_PATTERNS.iter().map(|p| re(p)).collect());

impl AdversarialStore {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<StoreConfig>) -> Self {
        let config = config.unwrap_or(StoreConfig {
            semantic_weight: 0.4,
            entity_boost: 0.5,
            speaker_boost: 0.4,
            ..StoreConfig::default()
        });
        AdversarialStore { storage, config }
    }

    /// Retrieve with adversarial weighting.
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        all_nodes: Option<&[NodeRef]>,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<Scored> {
        let query = prepare_query_embedding(query_embedding);
        let (negation_terms, positive_terms) = self.parse_adversarial(query_text);
        let query_entities = self.extract_entities(query_text);
        let nodes = nodes_or_session(self.storage.as_ref(), session_key, all_nodes);
        if nodes.is_empty() {
            return Ok(Vec::new());
        }
        let mut results = Vec::new();
        for node in &nodes {
            let charge = self.compute_adversarial_charge(
                &node.borrow(),
                &query,
                &query_entities,
                &negation_terms,
                &positive_terms,
                novelty_context,
            )?;
            if charge >= self.config.min_charge {
                results.push((node.clone(), charge));
            }
        }
        Ok(rank(results, limit))
    }

    /// `_parse_adversarial`: `(negated terms, positive terms)` of the query.
    pub fn parse_adversarial(&self, query: &str) -> (StrSet, StrSet) {
        let q = query.to_lowercase();
        let mut negation_terms = StrSet::new();
        for r in NEGATION_RES.iter() {
            negation_terms.extend(findall_group(r, &q));
        }
        let stopwords = [
            "the", "and", "for", "are", "but", "not", "you", "all", "what", "when", "where", "who", "why", "how",
            "which", "did", "does", "was", "were", "has", "have", "had", "been", "being", "will", "would", "could",
            "should", "may", "might", "never", "except", "other", "than", "without", "none", "with", "from", "into",
            "onto", "upon", "about", "over", "under", "through", "between", "among", "during", "before", "after",
            "above", "below", "that", "this", "these", "those", "then", "they", "them", "their", "there", "here",
        ];
        let positive_terms: StrSet = findall(&WORDS3, &q)
            .into_iter()
            .filter(|w| !stopwords.contains(&w.as_str()) && !negation_terms.contains(w))
            .collect();
        (negation_terms, positive_terms)
    }

    /// `_extract_entities`: capitalised words minus a broad list of capitalised non-entities.
    pub fn extract_entities(&self, text: &str) -> StrSet {
        #[rustfmt::skip]
        let common = [
            "What", "When", "Where", "Who", "Why", "How", "Which",
            "Did", "Does", "Do", "Is", "Are", "Was", "Were", "Has", "Have", "Had",
            "Will", "Would", "Could", "Should", "May", "Might", "Can", "Must",
            "The", "This", "That", "These", "Those", "Some", "Any", "All", "Each",
            "Every", "Both", "Neither", "Either", "Such",
            "And", "But", "Or", "Nor", "So", "Yet", "For", "Because", "Although",
            "Though", "While", "If", "Unless", "Until", "Since", "After", "Before",
            "Not", "Never", "Except", "Without", "None", "Nothing", "Nobody",
            "He", "She", "It", "They", "We", "You", "Me", "Us", "Them", "Him", "Her",
            "Today", "Tomorrow", "Yesterday", "Morning", "Evening", "Night",
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
            "January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December",
            "However", "Therefore", "Moreover", "Furthermore", "Nevertheless",
            "Meanwhile", "Otherwise", "Instead", "Perhaps", "Maybe", "Actually",
            "Finally", "Eventually", "Apparently", "Obviously", "Certainly",
            "Probably", "Possibly", "Definitely", "Absolutely", "Especially",
            "There", "Here", "Then", "Now", "Once", "Always", "Sometimes",
            "Yes", "No", "Please", "Thank", "Thanks", "Hello", "Goodbye",
            "Well", "First", "Second", "Third", "Last", "Next", "Many", "Much",
            "More", "Most", "Few", "Several", "Other", "Another", "Same",
        ];
        findall(&CAPITALIZED, text).into_iter().filter(|e| !common.contains(&e.as_str())).collect()
    }

    /// `_compute_adversarial_charge`.
    pub fn compute_adversarial_charge(
        &self,
        node: &NeuralNode,
        query_embedding: &[f32],
        query_entities: &StrSet,
        negation_terms: &StrSet,
        positive_terms: &StrSet,
        novelty_context: Option<&NoveltyContext>,
    ) -> TResult<f64> {
        let content_lower = node.content.to_lowercase();
        let semantic = semantic_charge(query_embedding, node)?;
        let (entity, speaker) = entity_and_speaker(node, &content_lower, query_entities)?;
        let mut negation_charge = 0.0;
        if !negation_terms.is_empty() {
            let contains_negated = negation_terms.iter().filter(|t| content_lower.contains(t.as_str())).count();
            negation_charge = if contains_negated == 0 { 0.2 } else { 0.4 };
        }
        let mut positive_charge = 0.0;
        if !positive_terms.is_empty() {
            let matches = positive_terms.iter().filter(|t| content_lower.contains(t.as_str())).count();
            positive_charge = py_min(1.0, matches as f64 / positive_terms.len().max(1) as f64);
        }
        let novelty_charge = compute_novelty_charge(&node.content, novelty_context);
        let c = &self.config;
        Ok(semantic * c.semantic_weight
            + entity * c.entity_boost
            + speaker * c.speaker_boost
            + negation_charge * 0.3
            + positive_charge * 0.3
            + novelty_charge * c.novelty_boost)
    }
}

// =============================================================================================================
// TESSERACT
// =============================================================================================================

/// The four stores and their fusion (`Tesseract`).
pub struct Tesseract {
    storage: Rc<dyn NeuralGraphStorage>,
    pub temporal_store: TemporalStore,
    pub entity_store: EntityStore,
    pub reasoning_store: ReasoningStore,
    pub adversarial_store: AdversarialStore,
}

/// The per-store results of one query, before fusion (exposed for diagnostics and tests).
#[derive(Clone, Debug)]
pub struct StoreResults {
    pub query_types: QueryTypeScores,
    pub effective_query: String,
    pub temporal: Scored,
    pub entity: Scored,
    pub reasoning: Scored,
    pub adversarial: Scored,
}

impl Tesseract {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>) -> Self {
        Tesseract {
            temporal_store: TemporalStore::new(storage.clone(), None),
            entity_store: EntityStore::new(storage.clone(), None),
            reasoning_store: ReasoningStore::new(storage.clone(), None),
            adversarial_store: AdversarialStore::new(storage.clone(), None),
            storage,
        }
    }

    /// Steps 1-2 of [`Tesseract::retrieve`]: query types, temporal expansion and the four store queries
    /// (`None` when the session has no nodes).
    pub fn query_stores(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        reference_time: Option<&PyDateTime>,
        auto_expand_temporal: bool,
    ) -> TResult<Option<StoreResults>> {
        let query_types = detect_query_type(query_text);
        let mut effective_query = query_text.to_string();
        if auto_expand_temporal && score_of(&query_types, QueryType::TEMPORAL) > 0.3 {
            effective_query = expand_temporal_query(query_text);
            tracing::debug!("Expanded temporal query: {query_text:?} -> {effective_query:?}");
        }
        tracing::info!("Query type detection: {query_types:?}");
        let all_nodes = self.storage.get_nodes_by_session(session_key, None);
        if all_nodes.is_empty() {
            return Ok(None);
        }
        let novelty = build_novelty_context(query_text, &all_nodes);
        let novelty = novelty.as_ref();
        let nodes = Some(all_nodes.as_slice());
        let temporal = self.temporal_store.retrieve(
            &effective_query,
            query_embedding,
            session_key,
            reference_time,
            40,
            nodes,
            novelty,
        )?;
        let entity = self.entity_store.retrieve(query_text, query_embedding, session_key, 50, nodes, novelty)?;
        let reasoning = self.reasoning_store.retrieve(query_text, query_embedding, session_key, 40, nodes, novelty)?;
        let adversarial =
            self.adversarial_store.retrieve(query_text, query_embedding, session_key, 30, nodes, novelty)?;
        Ok(Some(StoreResults { query_types, effective_query, temporal, entity, reasoning, adversarial }))
    }

    /// Execute 4D tesseract retrieval: detect the query type, query all stores, fuse with type-based weights,
    /// add temporal momentum and return the top `limit` results.
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        reference_time: Option<&PyDateTime>,
        limit: usize,
        auto_expand_temporal: bool,
    ) -> TResult<Scored> {
        let Some(stores) =
            self.query_stores(query_text, query_embedding, session_key, reference_time, auto_expand_temporal)?
        else {
            return Ok(Vec::new());
        };
        let fused = self.fuse_results(
            stores.temporal,
            stores.entity,
            stores.reasoning,
            stores.adversarial,
            &stores.query_types,
        );
        let fused = self.apply_temporal_momentum(fused, &stores.query_types, 6, 3);
        Ok(rank(fused, limit))
    }

    /// `_normalize_charges`: divide by the maximum charge when it is positive.
    pub fn normalize_charges(&self, results: Scored) -> Scored {
        if results.is_empty() {
            return results;
        }
        let max_charge = results.iter().map(|(_, c)| *c).fold(results[0].1, py_max);
        if max_charge <= 0.0 {
            return results;
        }
        results.into_iter().map(|(n, c)| (n, c / max_charge)).collect()
    }

    /// `_apply_temporal_momentum`: boost the temporal neighbours of the first `top_k` entries.
    pub fn apply_temporal_momentum(
        &self,
        results: Scored,
        type_weights: &QueryTypeScores,
        top_k: usize,
        max_neighbors: usize,
    ) -> Scored {
        if results.is_empty() {
            return results;
        }
        let temporal_bias =
            py_max(score_of(type_weights, QueryType::TEMPORAL), score_of(type_weights, QueryType::MULTI_HOP));
        let base_boost = 0.04 + (0.06 * temporal_bias);
        let mut combined: IndexMap<String, (NodeRef, f64)> =
            results.iter().map(|(n, c)| (n.borrow().node_id.clone(), (n.clone(), *c))).collect();
        for (idx, (node, _)) in results.iter().take(top_k).enumerate() {
            let node_id = node.borrow().node_id.clone();
            let mut neighbors = self.storage.get_neighbors(&node_id, Some(&[EdgeType::Temporal]), Direction::Both);
            if neighbors.is_empty() {
                continue;
            }
            let strength = |e: &crate::retrieval::data_types::EdgeRef| {
                let e = e.borrow();
                e.base_weight * e.ltp_boost * e.confidence
            };
            neighbors.sort_by(|a, b| strength(&b.1).partial_cmp(&strength(&a.1)).unwrap_or(std::cmp::Ordering::Equal));
            for (neighbor, edge) in neighbors.iter().take(max_neighbors) {
                let edge_strength = strength(edge);
                let distance_factor = 1.0 - (idx as f64 / top_k.max(1) as f64);
                let boost = py_min(0.2, base_boost * edge_strength * distance_factor);
                if boost <= 0.0 {
                    continue;
                }
                let neighbor_id = neighbor.borrow().node_id.clone();
                match combined.get_mut(&neighbor_id) {
                    Some(existing) => existing.1 += boost,
                    None => {
                        combined.insert(neighbor_id, (neighbor.clone(), boost));
                    }
                }
            }
        }
        combined.into_values().collect()
    }

    /// `_fuse_results`: normalise each store, weight it by the detected query types (plus a 0.05 floor) and sum
    /// per node, in order of first appearance (temporal, entity, reasoning, adversarial).
    pub fn fuse_results(
        &self,
        temporal_results: Scored,
        entity_results: Scored,
        reasoning_results: Scored,
        adversarial_results: Scored,
        type_weights: &QueryTypeScores,
    ) -> Scored {
        let temporal_results = self.normalize_charges(temporal_results);
        let entity_results = self.normalize_charges(entity_results);
        let reasoning_results = self.normalize_charges(reasoning_results);
        let adversarial_results = self.normalize_charges(adversarial_results);

        const BASE_WEIGHT: f64 = 0.05;
        let mut temporal_weight = score_of(type_weights, QueryType::TEMPORAL) + BASE_WEIGHT;
        let mut entity_weight = score_of(type_weights, QueryType::ENTITY) + BASE_WEIGHT;
        let mut multihop_weight = score_of(type_weights, QueryType::MULTI_HOP) + BASE_WEIGHT;
        let mut adversarial_weight = score_of(type_weights, QueryType::ADVERSARIAL) + BASE_WEIGHT;
        let mut open_weight = score_of(type_weights, QueryType::OPEN) + BASE_WEIGHT;
        let total = temporal_weight + entity_weight + multihop_weight + adversarial_weight + open_weight;
        if total > 0.0 {
            temporal_weight /= total;
            entity_weight /= total;
            multihop_weight /= total;
            adversarial_weight /= total;
            open_weight /= total;
        } else {
            temporal_weight = 0.2;
            entity_weight = 0.2;
            multihop_weight = 0.2;
            adversarial_weight = 0.2;
            open_weight = 0.2;
        }

        let mut combined: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        let mut add = |results: Scored, weight: f64| {
            for (node, charge) in results {
                let effective = charge * weight;
                let id = node.borrow().node_id.clone();
                match combined.get_mut(&id) {
                    Some(existing) => existing.1 += effective,
                    None => {
                        combined.insert(id, (node, effective));
                    }
                }
            }
        };
        add(temporal_results, temporal_weight + open_weight * 0.25);
        add(entity_results, entity_weight + open_weight * 0.35);
        add(reasoning_results, multihop_weight + open_weight * 0.25);
        add(adversarial_results, adversarial_weight + open_weight * 0.15);
        combined.into_values().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn morphology_variants() {
        let v: Vec<String> = expand_morphology_basic("camping").into_iter().collect();
        assert_eq!(v, ["camping", "camp", "camps", "camped"]);
        let v: Vec<String> = expand_morphology_basic("moved").into_iter().collect();
        assert_eq!(v, ["moved", "mov", "movs", "moving"]);
    }

    #[test]
    fn query_type_scores() {
        let s = detect_query_type("When did Caroline go to the LGBTQ support group?");
        assert_eq!(s[QueryType::TEMPORAL], 0.6);
        assert_eq!(s[QueryType::OPEN], 0.0);
    }
}
