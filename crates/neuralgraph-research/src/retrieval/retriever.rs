//! Multi-stage neural retriever (port of `retrieval/retriever.py`).
//!
//! The pipeline: optional entity pre-population (and a temporal bridge from it) for entity-heavy queries, then
//! the configured stages (hybrid vector + keyword recall, then gated graph expansion over entity/semantic,
//! temporal/causal, hierarchy and co-activation edges, activating every edge it walks: LTP), CA3 pattern
//! completion, wavefront propagation, a multi-signal rerank, and finally co-activation recording between the
//! returned nodes. The Python methods were `async` only because storage was; these are synchronous and the
//! retriever is `&mut self` because, like the Python object, it remembers the current query's wave amplitudes
//! and text between stages.
//!
//! Python behaviour kept:
//! - `_fast_recall`'s temporal branch (query wave `temporal > 0.3`) read `query_temporal_tokens` and
//!   `query_target_date`, which were local variables of `retrieve`, not of `_fast_recall`: every such query
//!   raised `NameError` ([`RetrieverError::NameError`]). The code behind that line is ported too; it runs only
//!   when [`NeuralRetriever::fast_recall_temporal_names`] is set, the way the fixture generator defined the two
//!   names as module globals to pin it.
//! - `_final_rerank` read the speaker from `metadata["producer_id"]` only (`getattr(node, "speaker")` found
//!   no attribute), and called `.lower()` on it: a non-string value raised `AttributeError`.
//! - `_parse_node_date` could only see strings in `metadata["resolved_date"]` here (JSON metadata cannot hold
//!   a `datetime`); a year 0 or an impossible day in its fallback formats raised `ValueError`.
//! - Wave dimensions were unions of Python `set`s; they are iterated query keys first, then the node's (the
//!   order the fixture generator forces), so float sums over them are reproducible.
//! - `NeuralGraphService.set_gate_strictness` assigned `retriever._gate_registry`, an attribute the retriever
//!   never read (it used `_gates`): the strictness change never reached the retriever. No setter is offered.

use crate::pytime::{NaiveAwareMismatch, PyDateTime};
use crate::retrieval::data_types::{
    cosine_similarity, edge_ref, generate_edge_id, EdgeType, NeuralEdge, NeuralNode, NodeLayer, NodeRef,
    RetrievalResult, RetrievalStageConfig,
};
use crate::retrieval::gating::{create_balanced_registry, GateContext, GateRegistry};
use crate::retrieval::interference::InterferenceType;
use crate::retrieval::pattern_completion::{CA3PatternCompleter, PatternCompletionConfig};
use crate::retrieval::pytext::{py_max, py_regex, py_type_name};
use crate::retrieval::storage::NeuralGraphStorage;
use crate::retrieval::temporal::TemporalChainManager;
use crate::retrieval::wavefront::WavefrontPropagator;
use chrono::NaiveDate;
use fancy_regex::Regex;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_int_parse, py_len, py_strip, py_truthy};
use neuralgraph_memory::temporal::month_number;
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::rc::Rc;
use std::sync::LazyLock;
use std::time::Instant;

/// An exception the Python retriever raised.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RetrieverError {
    NameError(String),
    TypeError(String),
    AttributeError(String),
    ValueError(String),
    IndexError(String),
}

impl std::fmt::Display for RetrieverError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RetrieverError::NameError(m) => write!(f, "NameError: {m}"),
            RetrieverError::TypeError(m) => write!(f, "TypeError: {m}"),
            RetrieverError::AttributeError(m) => write!(f, "AttributeError: {m}"),
            RetrieverError::ValueError(m) => write!(f, "ValueError: {m}"),
            RetrieverError::IndexError(m) => write!(f, "IndexError: {m}"),
        }
    }
}

impl std::error::Error for RetrieverError {}

impl From<NaiveAwareMismatch> for RetrieverError {
    fn from(e: NaiveAwareMismatch) -> Self {
        RetrieverError::TypeError(e.to_string())
    }
}

/// Wave amplitudes (`dict[str, float]`). The service also put `"__suppress_multi_hop": True` in it, which
/// Python's arithmetic treated as 1.0.
pub type WaveAmplitudes = IndexMap<String, f64>;

/// `sorted(xs, key=score, reverse=True)`: stable, equal scores keep their order.
pub(crate) fn sort_desc<T>(xs: &mut [(T, f64)]) {
    xs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(Ordering::Equal));
}

fn py_max_merge(old: f64, new: f64) -> f64 {
    py_max(old, new)
}

// =============================================================================================================
// Configuration
// =============================================================================================================

/// Configuration for the neural retriever.
#[derive(Clone, Debug)]
pub struct RetrieverConfig {
    pub stages: Vec<RetrievalStageConfig>,
    pub final_limit: usize,
    pub record_co_activations: bool,
    pub min_score_threshold: f64,
    pub hop_decay_factor: f64,
    pub rerank_by_heat: bool,
    pub heat_weight: f64,
    pub importance_weight: f64,
    pub use_wave_amplitudes: bool,
    pub wave_amplitude_weight: f64,
    pub use_keyword_boost: bool,
    pub keyword_boost_weight: f64,
    pub use_pattern_completion: bool,
    pub pattern_completion_iterations: usize,
    pub use_wavefront_propagation: bool,
    pub wavefront_constructive_boost: f64,
    pub wavefront_destructive_penalty: f64,
}

impl Default for RetrieverConfig {
    fn default() -> Self {
        RetrieverConfig {
            stages: Vec::new(),
            final_limit: 20,
            record_co_activations: true,
            min_score_threshold: 0.1,
            hop_decay_factor: 0.88,
            rerank_by_heat: true,
            heat_weight: 0.30,
            importance_weight: 0.25,
            use_wave_amplitudes: true,
            wave_amplitude_weight: 0.18,
            use_keyword_boost: true,
            keyword_boost_weight: 0.70,
            use_pattern_completion: true,
            pattern_completion_iterations: 4,
            use_wavefront_propagation: true,
            wavefront_constructive_boost: 0.40,
            wavefront_destructive_penalty: 0.50,
        }
        .post_init()
    }
}

impl RetrieverConfig {
    /// `__post_init__`: an empty stage list becomes [`RetrieverConfig::default_stages`].
    pub fn post_init(mut self) -> Self {
        if self.stages.is_empty() {
            self.stages = Self::default_stages();
        }
        self
    }

    /// Fast recall, entity expansion, temporal chain, hierarchy traversal, co-activation boost.
    pub fn default_stages() -> Vec<RetrievalStageConfig> {
        let stage = |name: &str, max_candidates, edge_types: Vec<EdgeType>, layer_filter, max_hops, score_threshold| {
            RetrievalStageConfig {
                name: name.to_string(),
                max_candidates,
                edge_types,
                layer_filter,
                max_hops,
                score_threshold,
                use_vector_search: false,
            }
        };
        let mut fast = stage("fast_recall", 200, vec![], None, 0, 0.25);
        fast.use_vector_search = true;
        vec![
            fast,
            stage(
                "entity_expansion",
                150,
                vec![EdgeType::Entity, EdgeType::Semantic],
                Some(vec![NodeLayer::Message, NodeLayer::Episode]),
                3,
                0.20,
            ),
            stage(
                "temporal_chain",
                50,
                vec![EdgeType::Temporal, EdgeType::Causal],
                Some(vec![NodeLayer::Message]),
                3,
                0.2,
            ),
            stage("hierarchy_traversal", 30, vec![EdgeType::Hierarchy], None, 2, 0.15),
            stage("co_activation_boost", 20, vec![EdgeType::CoActivation], None, 1, 0.3),
        ]
    }
}

// =============================================================================================================
// Query analysis shared with the flash retriever (each Python module had its own identical copy)
// =============================================================================================================

static ENTITY_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[A-Z][a-z]+\b"));
static WORD_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[a-zA-Z]+\b"));
static DATE_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b"));
static YEAR_MONTH_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b(20\d{2})[-/](\d{1,2})\b"));
static MONTH_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| {
    py_regex(
        r"(?i)\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
    )
});
static YEAR_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b(20\d{2})\b"));
static YEAR_ONLY_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\A(?:\d{4})\z"));
static MONTH_NAME_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\A(?:([A-Za-z]+)\s+(\d{4}))\z"));
static DAY_MONTH_YEAR_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\A(?:(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}))\z"));

/// The retriever's stop words (`_extract_keywords`).
#[rustfmt::skip]
pub const RETRIEVER_STOP_WORDS: &[&str] = &[
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall", "can", "need", "dare", "ought", "used", "to",
    "of", "in", "for", "on", "with", "at", "by", "from", "as", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "again", "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "and", "but", "if", "or", "because", "until", "while", "although", "though",
    "what", "which", "who", "whom", "this", "that", "these", "those", "am", "it", "its", "itself", "they", "them",
    "their", "theirs", "themselves", "you", "your", "yours", "yourself", "he", "him", "his", "himself", "she", "her",
    "hers", "herself", "we", "us", "our", "ours", "ourselves", "i", "me", "my", "myself", "about",
];

fn captures<'t>(re: &Regex, text: &'t str) -> Option<fancy_regex::Captures<'t, str>> {
    re.captures(text).expect("regex backtrack limit")
}

fn group_int(caps: &fancy_regex::Captures<'_, str>, i: usize) -> i64 {
    py_int_parse(caps.get(i).expect("group").as_str()).expect("digits")
}

/// `datetime(year, month, day)` (naive), with the `ValueError` CPython raised for impossible dates.
pub fn py_date(year: i64, month: i64, day: i64) -> Result<PyDateTime, String> {
    if !(1..=9999).contains(&year) {
        return Err(format!("year {year} is out of range"));
    }
    if !(1..=12).contains(&month) {
        return Err("month must be in 1..12".into());
    }
    match NaiveDate::from_ymd_opt(year as i32, month as u32, day.clamp(0, 99) as u32).filter(|_| day >= 1) {
        Some(d) => Ok(PyDateTime::naive(d.and_hms_opt(0, 0, 0).expect("midnight"))),
        None => Err("day is out of range for month".into()),
    }
}

/// `_extract_entities`: capitalised words, first occurrence order, no duplicates.
pub fn extract_entities(text: &str) -> Vec<String> {
    let mut seen = IndexSet::new();
    for m in ENTITY_RE.find_iter(text) {
        seen.insert(m.expect("regex backtrack limit").as_str().to_string());
    }
    seen.into_iter().collect()
}

/// Lower-cased alphabetic tokens of more than two characters that are not stop words (a Python `set`; here in
/// first-occurrence order, which no caller depended on).
pub fn extract_keywords_with(text: &str, stop_words: &[&str]) -> IndexSet<String> {
    let lower = text.to_lowercase();
    WORD_RE
        .find_iter(&lower)
        .map(|m| m.expect("regex backtrack limit").as_str())
        .filter(|t| !stop_words.contains(t) && py_len(t) > 2)
        .map(str::to_string)
        .collect()
}

/// `_compute_keyword_score`: share of the query keywords that occur (as substrings) in the lower-cased content.
pub fn compute_keyword_score(content: &str, query_keywords: &IndexSet<String>) -> f64 {
    if query_keywords.is_empty() {
        return 0.0;
    }
    let content_lower = content.to_lowercase();
    let matches = query_keywords.iter().filter(|kw| content_lower.contains(kw.as_str())).count();
    matches as f64 / query_keywords.len() as f64
}

/// `_extract_temporal_tokens`: coarse tokens (`DATE_YYYY-MM-DD`, `YEAR_YYYY`, `MONTH_MM`, `MONTH_NAME`) and a
/// naive target date from the first exact date, year-month, month-name-year or year in the text.
pub fn extract_temporal_tokens(text: &str) -> (IndexSet<String>, Option<PyDateTime>) {
    let mut tokens = IndexSet::new();
    let mut target_date: Option<PyDateTime> = None;

    if let Some(c) = captures(&DATE_RE, text) {
        let (y, mo, d) = (group_int(&c, 1), group_int(&c, 2), group_int(&c, 3));
        if let Ok(dt) = py_date(y, mo, d) {
            target_date = Some(dt);
            tokens.insert(format!("DATE_{y:04}-{mo:02}-{d:02}"));
            tokens.insert(format!("YEAR_{y:04}"));
            tokens.insert(format!("MONTH_{mo:02}"));
        }
    }
    if let Some(c) = captures(&YEAR_MONTH_RE, text) {
        let (y, mo) = (group_int(&c, 1), group_int(&c, 2));
        tokens.insert(format!("YEAR_{y:04}"));
        tokens.insert(format!("MONTH_{mo:02}"));
        if target_date.is_none() {
            target_date = py_date(y, mo, 1).ok();
        }
    }
    if let Some(c) = captures(&MONTH_YEAR_RE, text) {
        let month_name = c.get(1).expect("group").as_str();
        let y = group_int(&c, 2);
        if let Some(month_idx) = month_number(&month_name.to_lowercase()) {
            tokens.insert(format!("YEAR_{y:04}"));
            tokens.insert(format!("MONTH_{month_idx:02}"));
            tokens.insert(format!("MONTH_{}", month_name.to_uppercase()));
            if target_date.is_none() {
                target_date = py_date(y, month_idx as i64, 1).ok();
            }
        }
    }
    for c in YEAR_RE.captures_iter(text) {
        let c = c.expect("regex backtrack limit");
        let y = group_int(&c, 1);
        tokens.insert(format!("YEAR_{y:04}"));
        if target_date.is_none() {
            target_date = py_date(y, 1, 1).ok();
        }
    }
    (tokens, target_date)
}

/// `_parse_node_date`: `metadata["resolved_date"]` as an ISO string, a year, "Month Year" or "Day Month
/// Year"; otherwise `created_at` (so never `None` for a node). An impossible fallback date is the `ValueError`
/// Python raised.
pub fn parse_node_date(node: &NeuralNode) -> Result<Option<PyDateTime>, RetrieverError> {
    if let Some(Value::String(resolved)) = node.metadata.get("resolved_date") {
        if let Some(dt) = PyDateTime::fromisoformat(resolved) {
            return Ok(Some(dt));
        }
        if YEAR_ONLY_RE.is_match(resolved).expect("regex") {
            let y = py_int_parse(resolved).expect("digits");
            return py_date(y, 1, 1).map(Some).map_err(RetrieverError::ValueError);
        }
        if let Some(c) = captures(&MONTH_NAME_YEAR_RE, resolved) {
            if let Some(m) = month_number(&c.get(1).expect("group").as_str().to_lowercase()) {
                return py_date(group_int(&c, 2), m as i64, 1).map(Some).map_err(RetrieverError::ValueError);
            }
        }
        if let Some(c) = captures(&DAY_MONTH_YEAR_RE, resolved) {
            if let Some(m) = month_number(&c.get(2).expect("group").as_str().to_lowercase()) {
                return py_date(group_int(&c, 3), m as i64, group_int(&c, 1))
                    .map(Some)
                    .map_err(RetrieverError::ValueError);
            }
        }
    }
    Ok(Some(node.created_at))
}

/// `abs((a.date() - b.date()).days)`.
pub fn date_distance_days(a: &PyDateTime, b: &PyDateTime) -> i64 {
    (a.naive.date() - b.naive.date()).num_days().abs()
}

/// `_compute_wave_alignment`: cosine similarity in wave space, clamped at 0. The dot product runs over the
/// union of dimensions (query's first, then the node's).
pub fn compute_wave_alignment(query_waves: &WaveAmplitudes, node_waves: &WaveAmplitudes) -> f64 {
    let all_dims: IndexSet<&String> = query_waves.keys().chain(node_waves.keys()).collect();
    if all_dims.is_empty() {
        return 0.0;
    }
    let mut dot_product = 0.0;
    for dim in &all_dims {
        dot_product += query_waves.get(*dim).copied().unwrap_or(0.0) * node_waves.get(*dim).copied().unwrap_or(0.0);
    }
    let mut q = 0.0;
    for v in query_waves.values() {
        q += v.powf(2.0);
    }
    let mut n = 0.0;
    for v in node_waves.values() {
        n += v.powf(2.0);
    }
    let (query_mag, node_mag) = (q.powf(0.5), n.powf(0.5));
    if query_mag == 0.0 || node_mag == 0.0 {
        return 0.0;
    }
    py_max(0.0, dot_product / (query_mag * node_mag))
}

/// `set(meta.get("temporal_tokens", []) or meta.get("date_tokens", []) or [])` of a node's metadata.
pub(crate) fn node_temporal_tokens(node: &NeuralNode) -> Result<IndexSet<String>, RetrieverError> {
    let pick = |k: &str| node.metadata.get(k).filter(|v| py_truthy(v));
    let Some(v) = pick("temporal_tokens").or_else(|| pick("date_tokens")) else { return Ok(IndexSet::new()) };
    match v {
        // non-string items can never equal a string token
        Value::Array(items) => Ok(items.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()),
        Value::String(s) => Ok(s.chars().map(String::from).collect()),
        Value::Object(m) => Ok(m.keys().cloned().collect()),
        other => Err(RetrieverError::TypeError(format!("'{}' object is not iterable", py_type_name(other)))),
    }
}

// =============================================================================================================
// The retriever
// =============================================================================================================

/// `retrieve_temporal_context` result: scored predecessors and successors.
#[derive(Clone, Debug, Default)]
pub struct ScoredTemporalContext {
    pub before: Vec<(NodeRef, f64)>,
    pub after: Vec<(NodeRef, f64)>,
}

/// Multi-stage retrieval pipeline.
pub struct NeuralRetriever {
    storage: Rc<dyn NeuralGraphStorage>,
    gates: Rc<dyn GateRegistry>,
    temporal: Option<TemporalChainManager>,
    config: RetrieverConfig,
    pattern_completer: Option<CA3PatternCompleter>,
    wavefront_propagator: Option<WavefrontPropagator>,
    /// `self._query_wave_amplitudes` (absent before the first `retrieve`).
    query_wave_amplitudes: Option<WaveAmplitudes>,
    /// `self._current_query_text` (absent before the first `retrieve`).
    current_query_text: Option<String>,
    /// Values for the names `query_temporal_tokens` / `query_target_date` that `_fast_recall` read but Python
    /// never defined in its scope. `None` (the Python situation): the temporal branch raises `NameError`.
    pub fast_recall_temporal_names: Option<(IndexSet<String>, Option<PyDateTime>)>,
}

impl NeuralRetriever {
    /// `NeuralRetriever(storage, gate_registry=None, temporal_manager=None, config=None)`; the default gate
    /// registry is the balanced one.
    pub fn new(
        storage: Rc<dyn NeuralGraphStorage>,
        gate_registry: Option<Rc<dyn GateRegistry>>,
        temporal_manager: Option<TemporalChainManager>,
        config: Option<RetrieverConfig>,
    ) -> Self {
        let config = config.unwrap_or_default().post_init();
        let gates = gate_registry.unwrap_or_else(|| Rc::new(create_balanced_registry()));
        let pattern_completer = config.use_pattern_completion.then(|| {
            let pc = PatternCompletionConfig {
                max_iterations: config.pattern_completion_iterations,
                ..PatternCompletionConfig::default()
            };
            CA3PatternCompleter::new(storage.clone(), Some(pc))
        });
        let wavefront_propagator =
            config.use_wavefront_propagation.then(|| WavefrontPropagator::new(storage.clone(), 0.85, 0.92, 0.15));
        NeuralRetriever {
            storage,
            gates,
            temporal: temporal_manager,
            config,
            pattern_completer,
            wavefront_propagator,
            query_wave_amplitudes: None,
            current_query_text: None,
            fast_recall_temporal_names: None,
        }
    }

    pub fn config(&self) -> &RetrieverConfig {
        &self.config
    }

    pub fn storage(&self) -> &Rc<dyn NeuralGraphStorage> {
        &self.storage
    }

    pub fn gates(&self) -> &Rc<dyn GateRegistry> {
        &self.gates
    }

    pub fn pattern_completer(&self) -> Option<&CA3PatternCompleter> {
        self.pattern_completer.as_ref()
    }

    // ---------------------------------------------------------------------------------------------------------
    // Main retrieval
    // ---------------------------------------------------------------------------------------------------------

    /// Execute the pipeline. `limit` `None` or 0 uses `config.final_limit`; `layer_filter` filters the final
    /// ranking; `query_wave_amplitudes` drives the entity/temporal routing, wavefront and rerank signals.
    pub fn retrieve(
        &mut self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: Option<usize>,
        layer_filter: Option<&[NodeLayer]>,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<RetrievalResult, RetrieverError> {
        let start = Instant::now();
        let final_limit = limit.filter(|l| *l != 0).unwrap_or(self.config.final_limit);
        let query_keywords =
            if self.config.use_keyword_boost { self.extract_keywords(query_text) } else { IndexSet::new() };
        // computed but unused: `_fast_recall` could not see them (see the module docs)
        let _query_temporal = extract_temporal_tokens(query_text);

        let mut candidates: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        let mut stages_executed: Vec<String> = Vec::new();
        let mut total_candidates_seen = 0usize;
        let waves = query_wave_amplitudes.filter(|w| !w.is_empty());

        if let Some(w) = waves.filter(|w| w.get("entity").copied().unwrap_or(0.0) > 0.3) {
            let query_entities = extract_entities(query_text);
            if !query_entities.is_empty() {
                self.entity_prepopulation(
                    w,
                    &query_entities,
                    session_key,
                    &mut candidates,
                    &mut stages_executed,
                    &mut total_candidates_seen,
                );
            }
        }

        self.query_wave_amplitudes = query_wave_amplitudes.cloned();
        self.current_query_text = Some(query_text.to_string());
        let is_factual = Self::is_factual_query(query_wave_amplitudes);

        for stage in self.config.stages.clone() {
            let stage_start = Instant::now();
            let stage_results = if stage.use_vector_search {
                let mut effective_stage = stage.clone();
                if is_factual {
                    effective_stage.max_candidates = 300;
                    effective_stage.score_threshold = 0.2;
                }
                self.fast_recall(query_embedding, session_key, &effective_stage, &query_keywords)?
            } else {
                self.expand_stage(query_embedding, &candidates, &stage, session_key)
            };
            for (node, score) in &stage_results {
                total_candidates_seen += 1;
                let id = node.borrow().node_id.clone();
                let merged = match candidates.get(&id) {
                    Some((_, old)) => py_max_merge(*old, *score),
                    None => *score,
                };
                candidates.insert(id, (node.clone(), merged));
            }
            stages_executed.push(stage.name.clone());
            tracing::debug!(
                "Stage '{}': {} results in {:.1}ms",
                stage.name,
                stage_results.len(),
                stage_start.elapsed().as_secs_f64() * 1000.0
            );
        }

        if let (Some(completer), false) = (self.pattern_completer.as_mut(), candidates.is_empty()) {
            let mut seeds: Vec<(NodeRef, f64)> = candidates.values().cloned().collect();
            sort_desc(&mut seeds);
            seeds.truncate(30);
            let all_nodes = self.storage.get_nodes_by_session(session_key, None);
            let completed = completer.complete_pattern(&seeds, session_key, Some(all_nodes));
            for (node, activation) in &completed {
                let id = node.borrow().node_id.clone();
                match candidates.get(&id) {
                    Some((_, old)) => {
                        let new_score = 0.6 * old + 0.4 * activation;
                        candidates.insert(id, (node.clone(), new_score));
                    }
                    None => {
                        candidates.insert(id, (node.clone(), activation * 0.8));
                        total_candidates_seen += 1;
                    }
                }
            }
            stages_executed.push("ca3_pattern_completion".into());
            tracing::debug!("CA3 pattern completion: {} seeds -> {} completed", seeds.len(), completed.len());
        }

        if let (Some(propagator), false, Some(w)) = (&self.wavefront_propagator, candidates.is_empty(), waves) {
            let mut ranked: Vec<(NodeRef, f64)> = candidates.values().cloned().collect();
            sort_desc(&mut ranked);
            let seed_nodes: Vec<NodeRef> = ranked.into_iter().take(20).map(|(n, _)| n).collect();
            let wf_result = propagator.propagate(w, &seed_nodes, 5, 2, None)?;
            for wf_node in &wf_result.nodes {
                let node = &wf_node.node;
                let activation = wf_node.activation;
                let itype = wf_node.interference_type;
                let id = node.borrow().node_id.clone();
                match candidates.get(&id) {
                    Some((_, old)) => {
                        let new_score = match itype {
                            InterferenceType::Constructive => {
                                old + self.config.wavefront_constructive_boost * activation
                            }
                            InterferenceType::Destructive => old * self.config.wavefront_destructive_penalty,
                            InterferenceType::Partial => old + 0.15 * activation,
                        };
                        candidates.insert(id, (node.clone(), new_score));
                    }
                    None => {
                        let mut base_score = 0.5 * activation;
                        if itype == InterferenceType::Constructive {
                            base_score += self.config.wavefront_constructive_boost * 0.5;
                        }
                        candidates.insert(id, (node.clone(), base_score));
                        total_candidates_seen += 1;
                    }
                }
            }
            stages_executed.push("wavefront_propagation".into());
            tracing::debug!(
                "Wavefront propagation: {} seeds -> {} activated (constructive={}, destructive={})",
                seed_nodes.len(),
                wf_result.nodes.len(),
                wf_result.constructive_count,
                wf_result.destructive_count
            );
        }

        let mut ranked = self.final_rerank(&candidates, query_embedding, query_wave_amplitudes, &query_keywords)?;
        if let Some(f) = layer_filter.filter(|f| !f.is_empty()) {
            ranked.retain(|(n, _)| f.contains(&n.borrow().layer));
        }
        ranked.truncate(final_limit);

        let mut co_activations = 0;
        if self.config.record_co_activations && ranked.len() > 1 {
            let nodes: Vec<NodeRef> = ranked.iter().map(|(n, _)| n.clone()).collect();
            co_activations = self.record_co_activations(&nodes);
        }
        let total_time = start.elapsed().as_secs_f64() * 1000.0;
        tracing::info!(
            "Retrieved {} nodes in {total_time:.1}ms (candidates={total_candidates_seen}, co-activations={co_activations})",
            ranked.len()
        );
        Ok(RetrievalResult {
            query_text: query_text.to_string(),
            nodes: ranked,
            stages_executed,
            total_candidates_seen,
            co_activations_recorded: co_activations,
            total_time_ms: total_time,
        })
    }

    /// Phase I: entity index matches (score 0.68), their ENTITY neighbours in both directions
    /// (`0.5 * weight`), and with a temporal query their TEMPORAL neighbours (`0.65 * weight`).
    fn entity_prepopulation(
        &self,
        waves: &WaveAmplitudes,
        query_entities: &[String],
        session_key: &str,
        candidates: &mut IndexMap<String, (NodeRef, f64)>,
        stages_executed: &mut Vec<String>,
        total_candidates_seen: &mut usize,
    ) {
        let entity_matches = self.storage.find_nodes_by_entities(query_entities, session_key);
        for node in entity_matches.iter().take(50) {
            candidates.insert(node.borrow().node_id.clone(), (node.clone(), 0.68));
            *total_candidates_seen += 1;
        }
        let mut expanded_via_edges: IndexSet<String> = IndexSet::new();
        for node in entity_matches.iter().take(30) {
            let node_id = node.borrow().node_id.clone();
            self.follow_both_ways(
                &node_id,
                EdgeType::Entity,
                10,
                0.5,
                candidates,
                &mut expanded_via_edges,
                total_candidates_seen,
            );
        }
        if !entity_matches.is_empty() || !expanded_via_edges.is_empty() {
            stages_executed.push("entity_prepopulation".into());
            tracing::debug!(
                "Entity pre-population: {} direct + {} via edges",
                entity_matches.len().min(50),
                expanded_via_edges.len()
            );
        }
        let temporal_amp = waves.get("temporal").copied().unwrap_or(0.0);
        if temporal_amp > 0.3 && !entity_matches.is_empty() {
            let mut temporal_from_entity: IndexSet<String> = IndexSet::new();
            for node in entity_matches.iter().take(20) {
                let node_id = node.borrow().node_id.clone();
                self.follow_both_ways(
                    &node_id,
                    EdgeType::Temporal,
                    5,
                    0.65,
                    candidates,
                    &mut temporal_from_entity,
                    total_candidates_seen,
                );
            }
            if !temporal_from_entity.is_empty() {
                stages_executed.push("temporal_entity_bridge".into());
                tracing::debug!("Temporal-entity bridge: {} nodes", temporal_from_entity.len());
            }
        }
    }

    /// Add the other end of the first `per_direction` outgoing and incoming edges of one type, scored
    /// `factor * effective_weight`, unless already a candidate or added.
    #[allow(clippy::too_many_arguments)]
    fn follow_both_ways(
        &self,
        node_id: &str,
        edge_type: EdgeType,
        per_direction: usize,
        factor: f64,
        candidates: &mut IndexMap<String, (NodeRef, f64)>,
        added: &mut IndexSet<String>,
        total_candidates_seen: &mut usize,
    ) {
        let types = [edge_type];
        let mut edges = self.storage.get_edges_from(node_id, Some(&types));
        edges.truncate(per_direction);
        let mut to = self.storage.get_edges_to(node_id, Some(&types));
        to.truncate(per_direction);
        edges.extend(to);
        for edge in edges {
            let other_id = {
                let e = edge.borrow();
                if e.source_id == node_id {
                    e.target_id.clone()
                } else {
                    e.source_id.clone()
                }
            };
            if candidates.contains_key(&other_id) || added.contains(&other_id) {
                continue;
            }
            if let Some(target) = self.storage.get_node(&other_id) {
                let score = factor * edge.borrow_mut().effective_weight();
                candidates.insert(other_id.clone(), (target, score));
                added.insert(other_id);
                *total_candidates_seen += 1;
            }
        }
    }

    // ---------------------------------------------------------------------------------------------------------
    // Stage 1: fast recall
    // ---------------------------------------------------------------------------------------------------------

    /// Hybrid recall: vector hits above the stage threshold, boosted (or joined) by keyword matches; for a
    /// temporal query (`temporal > 0.3` in the remembered wave amplitudes) the temporal branch, which raised
    /// `NameError` in Python (see [`NeuralRetriever::fast_recall_temporal_names`]).
    pub fn fast_recall(
        &self,
        query_embedding: &[f64],
        session_key: &str,
        stage: &RetrievalStageConfig,
        query_keywords: &IndexSet<String>,
    ) -> Result<Vec<(NodeRef, f64)>, RetrieverError> {
        let layer_filter = stage.layer_filter.as_deref().filter(|f| !f.is_empty());
        let vector_results =
            self.storage.vector_search(query_embedding, session_key, stage.max_candidates, layer_filter);
        let mut candidates: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        for (node, score) in vector_results {
            if score >= stage.score_threshold {
                let id = node.borrow().node_id.clone();
                candidates.insert(id, (node, score));
            }
        }

        if self.config.use_keyword_boost && !query_keywords.is_empty() {
            for node in self.storage.get_nodes_by_session(session_key, None) {
                let (id, layer, keyword_score) = {
                    let n = node.borrow();
                    (n.node_id.clone(), n.layer, compute_keyword_score(&n.content, query_keywords))
                };
                if layer_filter.is_some_and(|f| !f.contains(&layer)) {
                    continue;
                }
                if keyword_score > 0.2 {
                    let score = match candidates.get(&id) {
                        Some((_, old)) => old + self.config.keyword_boost_weight * keyword_score,
                        None => 0.3 + keyword_score * 0.5,
                    };
                    candidates.insert(id, (node, score));
                }
            }
        }

        if let Some(w) = self.query_wave_amplitudes.as_ref().filter(|w| !w.is_empty()) {
            if w.get("temporal").copied().unwrap_or(0.0) > 0.3 {
                let Some((tokens, target)) = &self.fast_recall_temporal_names else {
                    return Err(RetrieverError::NameError("name 'query_temporal_tokens' is not defined".into()));
                };
                self.temporal_recall(&mut candidates, tokens, target.as_ref())?;
            }
        }

        let mut sorted: Vec<(NodeRef, f64)> = candidates.into_values().collect();
        sort_desc(&mut sorted);
        sorted.truncate(stage.max_candidates);
        Ok(sorted)
    }

    /// Phase II of `_fast_recall` (only reachable with the temporal names defined): boost by temporal-token
    /// overlap and date proximity, boost/penalise by the node's temporal amplitude, then follow TEMPORAL edges
    /// from the first 20 temporal nodes.
    fn temporal_recall(
        &self,
        candidates: &mut IndexMap<String, (NodeRef, f64)>,
        query_temporal_tokens: &IndexSet<String>,
        query_target_date: Option<&PyDateTime>,
    ) -> Result<(), RetrieverError> {
        if !query_temporal_tokens.is_empty() {
            let snapshot: Vec<(String, NodeRef, f64)> =
                candidates.iter().map(|(k, (n, s))| (k.clone(), n.clone(), *s)).collect();
            for (node_id, node, score) in snapshot {
                let (overlap, node_date) = {
                    let n = node.borrow();
                    let node_tokens = node_temporal_tokens(&n)?;
                    (node_tokens.iter().filter(|t| query_temporal_tokens.contains(*t)).count(), parse_node_date(&n)?)
                };
                let mut proximity_boost = 0.0;
                if let (Some(q), Some(d)) = (query_target_date, node_date.as_ref()) {
                    let delta_days = date_distance_days(d, q);
                    if delta_days <= 1 {
                        proximity_boost = 0.25;
                    } else if delta_days <= 7 {
                        proximity_boost = 0.15;
                    } else if delta_days <= 31 {
                        proximity_boost = 0.08;
                    }
                }
                if overlap != 0 || proximity_boost != 0.0 {
                    let boost = 0.12 * overlap as f64 + proximity_boost;
                    candidates.insert(node_id, (node, score + boost));
                }
            }
        }

        let snapshot: Vec<(String, NodeRef, f64)> =
            candidates.iter().map(|(k, (n, s))| (k.clone(), n.clone(), *s)).collect();
        for (node_id, node, score) in snapshot {
            let node_temporal = node.borrow().wave_amplitudes.get("temporal").copied().unwrap_or(0.0);
            if node_temporal > 0.3 {
                candidates.insert(node_id, (node, score + 0.25 * node_temporal));
            } else if node_temporal < 0.1 {
                candidates.insert(node_id, (node, score * 0.8));
            }
        }

        let mut temporal_expanded: IndexSet<String> = IndexSet::new();
        let top_temporal_nodes: Vec<(String, f64)> = candidates
            .iter()
            .filter(|(_, (n, _))| n.borrow().wave_amplitudes.get("temporal").copied().unwrap_or(0.0) > 0.3)
            .map(|(k, (_, s))| (k.clone(), *s))
            .take(20)
            .collect();
        let types = [EdgeType::Temporal];
        for (node_id, score) in top_temporal_nodes {
            let mut edges = self.storage.get_edges_from(&node_id, Some(&types));
            edges.truncate(5);
            let mut to = self.storage.get_edges_to(&node_id, Some(&types));
            to.truncate(5);
            edges.extend(to);
            for edge in edges {
                let target_id = {
                    let e = edge.borrow();
                    if e.source_id == node_id {
                        e.target_id.clone()
                    } else {
                        e.source_id.clone()
                    }
                };
                if candidates.contains_key(&target_id) || temporal_expanded.contains(&target_id) {
                    continue;
                }
                if let Some(target) = self.storage.get_node(&target_id) {
                    let edge_score = 0.4 * edge.borrow_mut().effective_weight() * score;
                    candidates.insert(target_id.clone(), (target, edge_score));
                    temporal_expanded.insert(target_id);
                }
            }
        }
        Ok(())
    }

    // ---------------------------------------------------------------------------------------------------------
    // Stages 2-5: graph expansion
    // ---------------------------------------------------------------------------------------------------------

    /// Gated BFS from the top 50 candidates over the stage's edge types: a target scores
    /// `parent * gate * hop_decay^(hop+1)` and is kept (its edge activated and saved) when that reaches the
    /// stage threshold; each hop's frontier is the best `max_candidates // (hop + 2)`.
    pub fn expand_stage(
        &self,
        query_embedding: &[f64],
        current_candidates: &IndexMap<String, (NodeRef, f64)>,
        stage: &RetrievalStageConfig,
        _session_key: &str,
    ) -> Vec<(NodeRef, f64)> {
        let mut expanded: Vec<(NodeRef, f64)> = Vec::new();
        let mut visited: IndexSet<String> = current_candidates.keys().cloned().collect();
        let mut frontier: Vec<(NodeRef, f64)> = current_candidates.values().cloned().collect();
        sort_desc(&mut frontier);
        frontier.truncate(50);
        let embedding_json = json!(query_embedding);

        for hop in 0..stage.max_hops {
            if frontier.is_empty() {
                break;
            }
            let mut next_frontier: Vec<(NodeRef, f64)> = Vec::new();
            let hop_decay = self.config.hop_decay_factor.powf((hop + 1) as f64);
            for (node, parent_score) in &frontier {
                let node_id = node.borrow().node_id.clone();
                let edges = self.storage.get_edges_from(&node_id, Some(&stage.edge_types));
                let mut gate_context = GateContext::new();
                gate_context.insert("query_embedding".into(), embedding_json.clone());
                gate_context.insert("parent_score".into(), json!(parent_score));
                for edge in edges {
                    let target_id = edge.borrow().target_id.clone();
                    if visited.contains(&target_id) {
                        continue;
                    }
                    let gate_value = self.gates.compute_gate(&mut edge.borrow_mut(), &gate_context);
                    if gate_value < stage.score_threshold {
                        continue;
                    }
                    let Some(target) = self.storage.get_node(&target_id) else { continue };
                    if let Some(f) = stage.layer_filter.as_ref().filter(|f| !f.is_empty()) {
                        if !f.contains(&target.borrow().layer) {
                            continue;
                        }
                    }
                    let score = parent_score * gate_value * hop_decay;
                    if score >= stage.score_threshold {
                        expanded.push((target.clone(), score));
                        next_frontier.push((target, score));
                        visited.insert(target_id);
                        edge.borrow_mut().activate();
                        self.storage.save_edge(&edge);
                    }
                }
            }
            sort_desc(&mut next_frontier);
            next_frontier.truncate(stage.max_candidates / (hop + 2));
            frontier = next_frontier;
        }
        expanded.truncate(stage.max_candidates);
        expanded
    }

    // ---------------------------------------------------------------------------------------------------------
    // Final reranking
    // ---------------------------------------------------------------------------------------------------------

    /// Combine base score, heat, importance, embedding similarity, wave alignment, keyword overlap, entity
    /// overlap with the remembered query text, a wave-dimension mismatch penalty and the speaker binding;
    /// drop scores below `min_score_threshold`; best first.
    pub fn final_rerank(
        &self,
        candidates: &IndexMap<String, (NodeRef, f64)>,
        query_embedding: &[f64],
        query_wave_amplitudes: Option<&WaveAmplitudes>,
        query_keywords: &IndexSet<String>,
    ) -> Result<Vec<(NodeRef, f64)>, RetrieverError> {
        let waves = query_wave_amplitudes.filter(|w| !w.is_empty());
        let mut reranked: Vec<(NodeRef, f64)> = Vec::new();
        for (node_ref, base_score) in candidates.values() {
            let node = node_ref.borrow();
            let mut final_score = *base_score;
            if self.config.rerank_by_heat {
                let heat_normalized = f64::min(1.0, node.heat_score / 10.0);
                final_score += self.config.heat_weight * heat_normalized;
            }
            final_score += self.config.importance_weight * node.importance_score;
            if let Some(emb) = node.embedding.as_ref().filter(|e| !e.is_empty()) {
                let sim = cosine_similarity(query_embedding, emb);
                final_score = final_score * 0.7 + sim * 0.3;
            }
            if let (true, Some(w), false) = (self.config.use_wave_amplitudes, waves, node.wave_amplitudes.is_empty()) {
                final_score += self.config.wave_amplitude_weight * compute_wave_alignment(w, &node.wave_amplitudes);
            }
            if self.config.use_keyword_boost && !query_keywords.is_empty() {
                final_score += self.config.keyword_boost_weight * compute_keyword_score(&node.content, query_keywords);
            }

            let mut query_entities: Vec<String> = Vec::new();
            if let Some(text) = self.current_query_text.as_deref().filter(|t| !t.is_empty()) {
                query_entities = extract_entities(text);
                if !query_entities.is_empty() && !node.entity_ids.is_empty() {
                    let q: IndexSet<String> = query_entities.iter().map(|e| e.to_lowercase()).collect();
                    let n: IndexSet<String> = node.entity_ids.iter().map(|e| e.to_lowercase()).collect();
                    let overlap = q.iter().filter(|e| n.contains(*e)).count();
                    if overlap > 0 {
                        final_score += 0.35 * (overlap as f64 / query_entities.len() as f64);
                    }
                }
            }

            if let Some(w) = waves {
                let dominant_dims: Vec<&String> = w
                    .iter()
                    .filter(|(k, v)| {
                        **v > 0.4 && matches!(k.as_str(), "entity" | "temporal" | "action" | "spatial" | "relational")
                    })
                    .map(|(k, _)| k)
                    .collect();
                if !dominant_dims.is_empty() && !node.wave_amplitudes.is_empty() {
                    let has_signal =
                        dominant_dims.iter().any(|d| node.wave_amplitudes.get(*d).copied().unwrap_or(0.0) > 0.2);
                    if !has_signal {
                        final_score *= 0.65;
                    }
                }
            }

            if !query_entities.is_empty() {
                let q: IndexSet<String> = query_entities.iter().map(|e| e.to_lowercase()).collect();
                let speaker = match node.metadata.get("producer_id") {
                    Some(Value::String(s)) => s.clone(),
                    Some(v) if py_truthy(v) => {
                        return Err(RetrieverError::AttributeError(format!(
                            "'{}' object has no attribute 'lower'",
                            py_type_name(v)
                        )))
                    }
                    _ => String::new(),
                };
                let speaker_lower = py_strip(&speaker.to_lowercase()).to_string();
                if !speaker_lower.is_empty() && q.contains(&speaker_lower) {
                    final_score += 0.35;
                }
            }

            if final_score >= self.config.min_score_threshold {
                reranked.push((node_ref.clone(), final_score));
            }
        }
        sort_desc(&mut reranked);
        Ok(reranked)
    }

    /// `_extract_keywords` with the retriever's stop words.
    pub fn extract_keywords(&self, text: &str) -> IndexSet<String> {
        extract_keywords_with(text, RETRIEVER_STOP_WORDS)
    }

    /// `_is_factual_query`: entity above 0.3, emotional and causal below 0.4.
    pub fn is_factual_query(query_waves: Option<&WaveAmplitudes>) -> bool {
        let Some(w) = query_waves.filter(|w| !w.is_empty()) else { return false };
        let get = |k: &str| w.get(k).copied().unwrap_or(0.0);
        get("entity") > 0.3 && get("emotional") < 0.4 && get("causal") < 0.4
    }

    // ---------------------------------------------------------------------------------------------------------
    // Co-activation recording (LTP)
    // ---------------------------------------------------------------------------------------------------------

    /// Strengthen a CO_ACTIVATION edge per pair of retrieved nodes (smaller id -> larger id), through the
    /// temporal manager when there is one (which also tags new edges with `context: "retrieval"`), otherwise
    /// directly (new edges start at base weight 0.3). Returns the number of edges saved.
    pub fn record_co_activations(&self, retrieved_nodes: &[NodeRef]) -> usize {
        if let Some(t) = &self.temporal {
            return t.record_co_activation(retrieved_nodes, "retrieval");
        }
        if retrieved_nodes.len() < 2 {
            return 0;
        }
        let node_ids: Vec<String> = retrieved_nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
        let mut pairs: Vec<(String, String)> = Vec::new();
        for (i, id1) in node_ids.iter().enumerate() {
            for id2 in &node_ids[i + 1..] {
                let (a, b) = if id1 <= id2 { (id1, id2) } else { (id2, id1) };
                pairs.push((a.clone(), b.clone()));
            }
        }
        if pairs.is_empty() {
            return 0;
        }
        let existing = self.storage.get_edges_batch(&pairs, Some(EdgeType::CoActivation));
        let mut edges_to_save = Vec::new();
        for ((id1, id2), edge) in pairs.into_iter().zip(existing) {
            let edge = edge.unwrap_or_else(|| {
                edge_ref(NeuralEdge::new(generate_edge_id(), id1, id2, EdgeType::CoActivation).with_weight(0.3))
            });
            edge.borrow_mut().activate();
            edges_to_save.push(edge);
        }
        self.storage.save_edges_batch(&edges_to_save);
        edges_to_save.len()
    }

    // ---------------------------------------------------------------------------------------------------------
    // Specialised retrievers
    // ---------------------------------------------------------------------------------------------------------

    /// Nodes referencing the entities, scored by the share of `entity_ids` they carry (Python default limit 20).
    pub fn retrieve_by_entities(&self, entity_ids: &[String], session_key: &str, limit: usize) -> Vec<(NodeRef, f64)> {
        let matching = self.storage.find_nodes_by_entities(entity_ids, session_key);
        let entity_set: IndexSet<&String> = entity_ids.iter().collect();
        let mut scored: Vec<(NodeRef, f64)> = matching
            .into_iter()
            .map(|node| {
                let overlap = {
                    let n = node.borrow();
                    let ids: IndexSet<&String> = n.entity_ids.iter().collect();
                    entity_set.iter().filter(|e| ids.contains(*e)).count()
                };
                let score = if entity_ids.is_empty() { 0.0 } else { overlap as f64 / entity_ids.len() as f64 };
                (node, score)
            })
            .collect();
        sort_desc(&mut scored);
        scored.truncate(limit);
        scored
    }

    /// Predecessors and successors of a node (Python defaults 3 and 3): through the temporal manager (scores
    /// `0.8^(i+1)`, walking the chain activates its edges), or else the strongest incoming/outgoing TEMPORAL
    /// edges scored by their effective weight.
    pub fn retrieve_temporal_context(
        &self,
        node: &NodeRef,
        before_count: usize,
        after_count: usize,
    ) -> ScoredTemporalContext {
        if let Some(t) = &self.temporal {
            let context = t.get_temporal_context(node, before_count, after_count);
            let score = |v: Vec<NodeRef>| -> Vec<(NodeRef, f64)> {
                v.into_iter().enumerate().map(|(i, n)| (n, 0.8f64.powf((i + 1) as f64))).collect()
            };
            return ScoredTemporalContext { before: score(context.before), after: score(context.after) };
        }
        let node_id = node.borrow().node_id.clone();
        let types = [EdgeType::Temporal];
        let edges_to = self.storage.get_edges_to(&node_id, Some(&types));
        let edges_from = self.storage.get_edges_from(&node_id, Some(&types));
        let strongest = |edges: Vec<crate::retrieval::data_types::EdgeRef>, count: usize| {
            let mut weighted: Vec<(crate::retrieval::data_types::EdgeRef, f64)> = edges
                .into_iter()
                .map(|e| {
                    let w = e.borrow_mut().effective_weight();
                    (e, w)
                })
                .collect();
            sort_desc(&mut weighted);
            weighted.truncate(count);
            weighted
        };
        let mut result = ScoredTemporalContext::default();
        for (edge, _) in strongest(edges_to, before_count) {
            let source = edge.borrow().source_id.clone();
            if let Some(pred) = self.storage.get_node(&source) {
                let w = edge.borrow_mut().effective_weight();
                result.before.push((pred, w));
            }
        }
        for (edge, _) in strongest(edges_from, after_count) {
            let target = edge.borrow().target_id.clone();
            if let Some(succ) = self.storage.get_node(&target) {
                let w = edge.borrow_mut().effective_weight();
                result.after.push((succ, w));
            }
        }
        result
    }

    /// Message-level vector hits (`limit * 3`) climbed up to `target_layer` (x0.9 per level), best score per
    /// target node (Python defaults: EPISODE, 10).
    pub fn retrieve_with_hierarchy(
        &self,
        query_embedding: &[f64],
        session_key: &str,
        target_layer: NodeLayer,
        limit: usize,
    ) -> Vec<(NodeRef, f64)> {
        let mut messages =
            self.storage.vector_search(query_embedding, session_key, limit * 3, Some(&[NodeLayer::Message]));
        if target_layer == NodeLayer::Message {
            messages.truncate(limit);
            return messages;
        }
        let mut target_nodes: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        for (msg, score) in messages {
            let mut current = msg;
            let mut current_score = score;
            while current.borrow().layer.value() < target_layer.value() {
                let Some(parent_id) = current.borrow().parent_id.clone() else { break };
                let Some(parent) = self.storage.get_node(&parent_id) else { break };
                current = parent;
                current_score *= 0.9;
            }
            if current.borrow().layer == target_layer {
                let id = current.borrow().node_id.clone();
                let merged = match target_nodes.get(&id) {
                    Some((_, old)) => py_max_merge(*old, current_score),
                    None => current_score,
                };
                target_nodes.insert(id, (current, merged));
            }
        }
        let mut results: Vec<(NodeRef, f64)> = target_nodes.into_values().collect();
        sort_desc(&mut results);
        results.truncate(limit);
        results
    }

    // ---------------------------------------------------------------------------------------------------------
    // Query expansion
    // ---------------------------------------------------------------------------------------------------------

    /// Retrieve `limit * 2`, then `expansion_rounds` times re-retrieve with the query embedding blended
    /// (70/30) with the mean embedding of the top 5, merging at 90% (Python defaults: 2 rounds, limit 20).
    pub fn retrieve_with_expansion(
        &mut self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        expansion_rounds: i64,
        limit: usize,
    ) -> Result<RetrievalResult, RetrieverError> {
        let mut result = self.retrieve(query_text, query_embedding, session_key, Some(limit * 2), None, None)?;
        if expansion_rounds <= 0 || result.nodes.len() < 3 {
            result.nodes.truncate(limit);
            return Ok(result);
        }
        let mut all_candidates: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        for (n, s) in &result.nodes {
            all_candidates.insert(n.borrow().node_id.clone(), (n.clone(), *s));
        }
        for _round in 0..expansion_rounds {
            let mut ranked: Vec<(NodeRef, f64)> = all_candidates.values().cloned().collect();
            sort_desc(&mut ranked);
            let expansion_embeddings: Vec<Vec<f64>> = ranked
                .iter()
                .take(5)
                .filter_map(|(n, _)| n.borrow().embedding.clone().filter(|e| !e.is_empty()))
                .collect();
            if expansion_embeddings.is_empty() {
                break;
            }
            let mut expanded_embedding = query_embedding.to_vec();
            for (i, value) in expanded_embedding.iter_mut().enumerate() {
                let mut exp_sum = 0.0;
                for e in &expansion_embeddings {
                    exp_sum += *e.get(i).ok_or_else(|| RetrieverError::IndexError("list index out of range".into()))?;
                }
                *value = 0.7 * *value + 0.3 * exp_sum / expansion_embeddings.len() as f64;
            }
            let new_result = self.retrieve(query_text, &expanded_embedding, session_key, Some(limit), None, None)?;
            for (node, score) in new_result.nodes {
                let id = node.borrow().node_id.clone();
                let merged = match all_candidates.get(&id) {
                    Some((_, old)) => py_max_merge(*old, score * 0.9),
                    None => score * 0.9,
                };
                all_candidates.insert(id, (node, merged));
            }
        }
        let total = all_candidates.len();
        let mut final_nodes: Vec<(NodeRef, f64)> = all_candidates.into_values().collect();
        sort_desc(&mut final_nodes);
        final_nodes.truncate(limit);
        let mut stages = result.stages_executed.clone();
        stages.push(format!("expansion_x{expansion_rounds}"));
        Ok(RetrievalResult {
            query_text: query_text.to_string(),
            nodes: final_nodes,
            stages_executed: stages,
            total_candidates_seen: total,
            co_activations_recorded: result.co_activations_recorded,
            total_time_ms: result.total_time_ms,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDateTime;

    #[test]
    fn py_date_errors_like_cpython() {
        assert_eq!(py_date(0, 1, 1).unwrap_err(), "year 0 is out of range");
        assert_eq!(py_date(2023, 13, 1).unwrap_err(), "month must be in 1..12");
        assert_eq!(py_date(2023, 2, 30).unwrap_err(), "day is out of range for month");
        assert!(py_date(2024, 2, 29).is_ok());
    }

    #[test]
    fn naive_date_distance() {
        let a = PyDateTime::naive(NaiveDateTime::parse_from_str("2023-05-08 23:00", "%Y-%m-%d %H:%M").unwrap());
        let b = py_date(2023, 5, 1).unwrap();
        assert_eq!(date_distance_days(&a, &b), 7);
    }
}
