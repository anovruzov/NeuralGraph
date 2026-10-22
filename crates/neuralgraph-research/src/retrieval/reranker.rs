//! Top-K candidate reranking (port of `retrieval/reranker.py`).
//!
//! Three rerankers share the [`Reranker`] trait: [`LlmReranker`] asks the model for a 0–3 relevance score,
//! [`SemanticBoostReranker`] scores keyword/entity/speaker overlap offline, [`HybridReranker`] chains them.
//! [`rerank_candidates_parallel`] is what the benchmark runner calls for every question: it scores all
//! candidates concurrently with the LLM prompt and re-sorts by (score, retrieval charge).
//!
//! Python created its own `aiohttp` session inside `LLMReranker`; here the reranker borrows an [`Llm`].
//! `ScoreCache` timestamps use `time.time()`; [`ScoreCache::get_at`]/[`ScoreCache::set_at`] take the clock
//! explicitly so the TTL semantics can be pinned in tests.

use async_trait::async_trait;
use indexmap::{IndexMap, IndexSet};
use md5::{Digest, Md5};
use neuralgraph_memory::pycompat::{py_int_from_value, py_len, py_prefix};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::sync::LazyLock;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use super::data_types::NodeRef;
use super::pytext::{py_findall, py_json_loads, py_max, py_min, py_prefix_len, py_regex};
use crate::llm_backend::{llm_base_url, llm_model, GenerateOptions, Llm};

/// Reranker configuration (`RerankerConfig`).
#[derive(Clone, Debug, PartialEq)]
pub struct RerankerConfig {
    /// "llm", "semantic" or "hybrid" ("cross_encoder" was named but never implemented).
    pub reranker_type: String,
    pub llm_base_url: String,
    pub llm_model: String,
    pub llm_timeout_seconds: f64,
    pub llm_max_tokens: i64,
    /// Unused (reserved for a sentence-transformers cross-encoder).
    pub cross_encoder_model: String,
    /// Unused.
    pub batch_size: i64,
    pub max_candidates: i64,
    pub cache_size: usize,
    /// Per-candidate scoring timeout in `rerank`.
    pub async_timeout_ms: f64,
    pub original_score_weight: f64,
    pub rerank_score_weight: f64,
    /// Unused (the timeout fallback always applies).
    pub fallback_on_timeout: bool,
    pub min_score_threshold: f64,
}

impl Default for RerankerConfig {
    fn default() -> Self {
        RerankerConfig {
            reranker_type: "llm".into(),
            llm_base_url: llm_base_url(),
            llm_model: llm_model(),
            llm_timeout_seconds: 8.0,
            llm_max_tokens: 15,
            cross_encoder_model: "cross-encoder/ms-marco-MiniLM-L-6-v2".into(),
            batch_size: 10,
            max_candidates: 50,
            cache_size: 1000,
            async_timeout_ms: 50.0,
            original_score_weight: 0.3,
            rerank_score_weight: 0.7,
            fallback_on_timeout: true,
            min_score_threshold: 0.1,
        }
    }
}

/// Result of reranking one candidate (`RerankResult`).
#[derive(Clone, Debug, PartialEq)]
pub struct RerankResult {
    pub node_id: String,
    pub original_score: f64,
    pub rerank_score: f64,
    pub combined_score: f64,
    pub rerank_reason: String,
    pub latency_ms: f64,
}

/// `time.time()`.
fn time_time() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// LRU-ish score cache with a TTL (`ScoreCache`), keyed by `md5(f"{query}|||{text}")`.
///
/// Python quirks kept: an entry expired on `get` is removed from the cache but its key stays in the access
/// order; `set` appends the key to the access order even when it is already there; eviction pops the oldest
/// access-order key while the cache is at capacity (also when the key being set already exists).
#[derive(Clone, Debug)]
pub struct ScoreCache {
    cache: IndexMap<String, (f64, f64)>,
    max_size: usize,
    ttl: f64,
    access_order: Vec<String>,
}

impl ScoreCache {
    /// `ScoreCache(max_size=1000, ttl_seconds=300.0)`.
    pub fn new(max_size: usize, ttl_seconds: f64) -> Self {
        ScoreCache { cache: IndexMap::new(), max_size, ttl: ttl_seconds, access_order: Vec::new() }
    }

    /// `_make_key(query, text)`: md5 hex digest of `query|||text`.
    pub fn make_key(query: &str, text: &str) -> String {
        let digest = Md5::digest(format!("{query}|||{text}").as_bytes());
        digest.iter().map(|b| format!("{b:02x}")).collect()
    }

    pub fn get(&mut self, query: &str, text: &str) -> Option<f64> {
        self.get_at(query, text, time_time())
    }

    /// `get` with the clock reading `now` (seconds since the epoch).
    pub fn get_at(&mut self, query: &str, text: &str, now: f64) -> Option<f64> {
        let key = Self::make_key(query, text);
        let (score, timestamp) = *self.cache.get(&key)?;
        if now - timestamp > self.ttl {
            self.cache.shift_remove(&key);
            return None;
        }
        if let Some(pos) = self.access_order.iter().position(|k| *k == key) {
            self.access_order.remove(pos);
        }
        self.access_order.push(key);
        Some(score)
    }

    pub fn set(&mut self, query: &str, text: &str, score: f64) {
        self.set_at(query, text, score, time_time());
    }

    /// `set` with the clock reading `now`.
    pub fn set_at(&mut self, query: &str, text: &str, score: f64, now: f64) {
        let key = Self::make_key(query, text);
        while self.cache.len() >= self.max_size && !self.access_order.is_empty() {
            let oldest = self.access_order.remove(0);
            self.cache.shift_remove(&oldest);
        }
        self.cache.insert(key.clone(), (score, now));
        self.access_order.push(key);
    }

    pub fn clear(&mut self) {
        self.cache.clear();
        self.access_order.clear();
    }

    /// Cached keys in dict order with (score, timestamp).
    pub fn entries(&self) -> &IndexMap<String, (f64, f64)> {
        &self.cache
    }

    /// The access-order list (may hold stale or repeated keys, as in Python).
    pub fn access_order(&self) -> &[String] {
        &self.access_order
    }
}

/// Counters of `BaseReranker`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RerankerStats {
    pub total_reranks: i64,
    pub cache_hits: i64,
    pub cache_misses: i64,
    pub total_latency_ms: f64,
}

/// State every reranker carries (`BaseReranker.__init__`): config, a 300 s score cache and counters.
#[derive(Debug)]
pub struct RerankerCore {
    pub config: RerankerConfig,
    pub cache: RefCell<ScoreCache>,
    pub stats: RefCell<RerankerStats>,
}

impl RerankerCore {
    pub fn new(config: Option<RerankerConfig>) -> Self {
        let config = config.unwrap_or_default();
        let cache = ScoreCache::new(config.cache_size, 300.0);
        RerankerCore { config, cache: RefCell::new(cache), stats: RefCell::new(RerankerStats::default()) }
    }
}

/// The reranker interface (`BaseReranker`).
#[async_trait(?Send)]
pub trait Reranker {
    fn core(&self) -> &RerankerCore;

    /// Relevance of one candidate, in [0, 1] or [0, 3] depending on the implementation.
    async fn score_candidate(&self, query: &str, candidate_text: &str, speaker: &str) -> f64;

    /// Rerank `(node, original score)` candidates; returns `(node, combined score)` best first.
    async fn rerank(&self, query: &str, candidates: &[(NodeRef, f64)], limit: i64) -> Vec<(NodeRef, f64)> {
        base_rerank(self, query, candidates, limit).await
    }

    /// Release resources (the Python LLM reranker closed its HTTP session).
    async fn close(&self) {}

    fn cache_hit_rate(&self) -> f64 {
        let stats = self.core().stats.borrow();
        let total = stats.cache_hits + stats.cache_misses;
        if total > 0 {
            stats.cache_hits as f64 / total as f64
        } else {
            0.0
        }
    }

    /// `get_stats()` (`avg_latency_ms` is an int 0 before the first rerank, as in Python).
    fn get_stats(&self) -> Value {
        let stats = self.core().stats.borrow().clone();
        let avg =
            if stats.total_reranks > 0 { json!(stats.total_latency_ms / stats.total_reranks as f64) } else { json!(0) };
        json!({
            "total_reranks": stats.total_reranks,
            "cache_hits": stats.cache_hits,
            "cache_misses": stats.cache_misses,
            "cache_hit_rate": self.cache_hit_rate(),
            "avg_latency_ms": avg,
        })
    }
}

/// `BaseReranker.rerank`: cached or freshly scored (with a per-candidate timeout falling back to
/// `original * 3`), normalised to [0, 1], combined `0.3 * original + 0.7 * rerank`, sorted, cut to `limit`
/// and filtered by `min_score_threshold`.
pub async fn base_rerank<R: Reranker + ?Sized>(
    reranker: &R,
    query: &str,
    candidates: &[(NodeRef, f64)],
    limit: i64,
) -> Vec<(NodeRef, f64)> {
    if candidates.is_empty() {
        return Vec::new();
    }
    let start = Instant::now();
    let core = reranker.core();
    let config = &core.config;
    core.stats.borrow_mut().total_reranks += 1;

    let candidates = &candidates[..py_prefix_len(candidates.len(), config.max_candidates)];
    let mut results: Vec<RerankResult> = Vec::new();
    for (node, original_score) in candidates {
        let (node_id, content, speaker) = {
            let n = node.borrow();
            (n.node_id.clone(), n.content.clone(), n.speaker_id())
        };
        let cached = core.cache.borrow_mut().get(query, &content);
        let rerank_score = match cached {
            Some(score) => {
                core.stats.borrow_mut().cache_hits += 1;
                score
            }
            None => {
                core.stats.borrow_mut().cache_misses += 1;
                let timeout_s = config.async_timeout_ms / 1000.0;
                // asyncio.wait_for with a non-positive timeout cancels the task before it runs.
                let scored = if timeout_s > 0.0 {
                    tokio::time::timeout(
                        Duration::from_secs_f64(timeout_s),
                        reranker.score_candidate(query, &content, &speaker),
                    )
                    .await
                    .ok()
                } else {
                    None
                };
                match scored {
                    Some(score) => {
                        core.cache.borrow_mut().set(query, &content, score);
                        score
                    }
                    None => {
                        tracing::debug!("Rerank timeout for node {}", py_prefix(&node_id, 8));
                        original_score * 3.0
                    }
                }
            }
        };
        let normalized = if rerank_score > 1.0 { rerank_score / 3.0 } else { rerank_score };
        let combined = config.original_score_weight * original_score + config.rerank_score_weight * normalized;
        results.push(RerankResult {
            node_id,
            original_score: *original_score,
            rerank_score,
            combined_score: combined,
            rerank_reason: String::new(),
            latency_ms: 0.0,
        });
    }
    results.sort_by(|a, b| b.combined_score.partial_cmp(&a.combined_score).unwrap_or(std::cmp::Ordering::Equal));

    // {node.node_id: node}: a repeated id maps to its last node.
    let node_map: IndexMap<String, NodeRef> =
        candidates.iter().map(|(n, _)| (n.borrow().node_id.clone(), n.clone())).collect();
    let n = py_prefix_len(results.len(), limit);
    let output: Vec<(NodeRef, f64)> = results[..n]
        .iter()
        .filter(|r| r.combined_score >= config.min_score_threshold)
        .map(|r| (node_map[&r.node_id].clone(), r.combined_score))
        .collect();

    let latency = start.elapsed().as_secs_f64() * 1000.0;
    core.stats.borrow_mut().total_latency_ms += latency;
    tracing::debug!(
        "Reranked {} candidates in {:.1}ms (cache hit rate: {:.1}%)",
        candidates.len(),
        latency,
        reranker.cache_hit_rate() * 100.0
    );
    output
}

// ============================================================================
// LLM reranker
// ============================================================================

/// The relevance prompt of `LLMReranker.score_candidate` (memory text cut to 350 code points).
pub fn llm_rerank_prompt(query: &str, candidate_text: &str, speaker: &str) -> String {
    format!(
        "Score relevance 0-3 for the question vs memory.

Question: {query}
Memory from [{speaker}]: {text}

Scoring:
3 = directly answers the question
2 = strong supporting evidence
1 = weakly related
0 = unrelated / wrong entity

Return JSON only: {{\"score\": 0}} (or 1/2/3)",
        text = py_prefix(candidate_text, 350)
    )
}

/// The reply parsing of `LLMReranker.score_candidate`: `int(json.loads(resp).get("score", 1))` clamped to
/// 0..=3; if that raises (not JSON, not an object, score not int-convertible), the first character among
/// `0123` in the reply; else 1.
pub fn parse_llm_score(resp: &str) -> i64 {
    let from_json = match py_json_loads(resp) {
        Some(Value::Object(obj)) => match obj.get("score") {
            None => Some(1),
            Some(v) => py_int_from_value(v).ok(),
        },
        _ => None,
    };
    if let Some(score) = from_json {
        return score.clamp(0, 3);
    }
    resp.chars().find(|c| "0123".contains(*c)).map(|c| c as i64 - '0' as i64).unwrap_or(1)
}

/// `LLMReranker.score_candidate`: 0–3 from the model; 1 when the call fails.
pub async fn llm_score_candidate(
    llm: &dyn Llm,
    config: &RerankerConfig,
    query: &str,
    candidate_text: &str,
    speaker: &str,
) -> i64 {
    let prompt = llm_rerank_prompt(query, candidate_text, speaker);
    let opts = GenerateOptions::default()
        .model(config.llm_model.clone())
        .base_url(config.llm_base_url.clone())
        .temperature(0.0)
        .max_tokens(config.llm_max_tokens)
        .timeout(config.llm_timeout_seconds);
    match llm.generate(&prompt, &opts).await {
        Ok(resp) => parse_llm_score(&resp),
        Err(e) => {
            tracing::debug!("LLM rerank error: {e}");
            1
        }
    }
}

/// LLM relevance reranker (`LLMReranker`).
pub struct LlmReranker<'a> {
    pub core: RerankerCore,
    pub llm: &'a dyn Llm,
}

impl<'a> LlmReranker<'a> {
    pub fn new(llm: &'a dyn Llm, config: Option<RerankerConfig>) -> Self {
        LlmReranker { core: RerankerCore::new(config), llm }
    }
}

#[async_trait(?Send)]
impl Reranker for LlmReranker<'_> {
    fn core(&self) -> &RerankerCore {
        &self.core
    }

    async fn score_candidate(&self, query: &str, candidate_text: &str, speaker: &str) -> f64 {
        llm_score_candidate(self.llm, &self.core.config, query, candidate_text, speaker).await as f64
    }
}

// ============================================================================
// Semantic boost reranker
// ============================================================================

const STOPWORDS: &[&str] = &[
    "the", "a", "an", "is", "are", "was", "were", "what", "where", "who", "when", "how", "which", "do", "does", "did",
    "have", "has", "had", "be", "been", "being", "to", "of", "in", "for", "on", "with", "at", "by", "from", "about",
    "as", "into", "through",
];

static WORD_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b\w+\b"));
static CAPITALIZED_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[A-Z][a-z]+\b"));

/// `SemanticBoostReranker.score_candidate`: 1 + keyword overlap (0.5/1/1.5) + 0.5 per capitalised query word
/// found in the text + speaker bonuses − length penalties, clamped to [0, 3].
pub fn semantic_score(query: &str, candidate_text: &str, speaker: &str) -> f64 {
    let query_lower = query.to_lowercase();
    let text_lower = candidate_text.to_lowercase();
    let mut score = 1.0f64;

    let query_words: IndexSet<String> =
        py_findall(&WORD_RE, &query_lower).into_iter().filter(|w| !STOPWORDS.contains(&w.as_str())).collect();
    let text_words: IndexSet<String> = py_findall(&WORD_RE, &text_lower).into_iter().collect();
    let overlap = query_words.intersection(&text_words).count();
    if overlap >= 3 {
        score += 1.5;
    } else if overlap >= 2 {
        score += 1.0;
    } else if overlap >= 1 {
        score += 0.5;
    }

    // A Python set; only how many entities occur matters, so the order does not.
    let query_entities: IndexSet<String> = py_findall(&CAPITALIZED_RE, query).into_iter().collect();
    for entity in &query_entities {
        if text_lower.contains(entity.to_lowercase().as_str()) {
            score += 0.5;
        }
    }

    if !speaker.is_empty() {
        let speaker_lower = speaker.to_lowercase();
        if query_lower.contains(speaker_lower.as_str()) {
            score += 0.5;
        }
        if query_entities.iter().any(|e| e.to_lowercase() == speaker_lower) {
            score += 0.5;
        }
    }

    let text_len = py_len(candidate_text);
    if text_len < 20 {
        score -= 0.5;
    } else if text_len > 500 {
        score -= 0.2;
    }
    // max(0, min(3, score))
    py_max(0.0, py_min(3.0, score))
}

/// Offline keyword/entity/speaker reranker (`SemanticBoostReranker`).
pub struct SemanticBoostReranker {
    pub core: RerankerCore,
}

impl SemanticBoostReranker {
    pub fn new(config: Option<RerankerConfig>) -> Self {
        SemanticBoostReranker { core: RerankerCore::new(config) }
    }
}

#[async_trait(?Send)]
impl Reranker for SemanticBoostReranker {
    fn core(&self) -> &RerankerCore {
        &self.core
    }

    async fn score_candidate(&self, query: &str, candidate_text: &str, speaker: &str) -> f64 {
        semantic_score(query, candidate_text, speaker)
    }
}

// ============================================================================
// Hybrid reranker
// ============================================================================

/// Semantic pre-filter to `3 * limit`, then the LLM reranker on those when more than `limit` remain
/// (`HybridReranker`). Each stage keeps its own cache and counters.
pub struct HybridReranker<'a> {
    pub core: RerankerCore,
    pub semantic: SemanticBoostReranker,
    pub llm: LlmReranker<'a>,
}

impl<'a> HybridReranker<'a> {
    pub fn new(llm: &'a dyn Llm, config: Option<RerankerConfig>) -> Self {
        HybridReranker {
            core: RerankerCore::new(config.clone()),
            semantic: SemanticBoostReranker::new(config.clone()),
            llm: LlmReranker::new(llm, config),
        }
    }
}

#[async_trait(?Send)]
impl Reranker for HybridReranker<'_> {
    fn core(&self) -> &RerankerCore {
        &self.core
    }

    async fn score_candidate(&self, query: &str, candidate_text: &str, speaker: &str) -> f64 {
        self.semantic.score_candidate(query, candidate_text, speaker).await
    }

    async fn rerank(&self, query: &str, candidates: &[(NodeRef, f64)], limit: i64) -> Vec<(NodeRef, f64)> {
        if candidates.is_empty() {
            return Vec::new();
        }
        let semantic_limit = (limit * 3).min(candidates.len() as i64);
        let semantic_results = self.semantic.rerank(query, candidates, semantic_limit).await;
        if semantic_results.len() as i64 > limit {
            return self.llm.rerank(query, &semantic_results, limit).await;
        }
        semantic_results
    }

    async fn close(&self) {
        self.llm.close().await;
    }
}

// ============================================================================
// Factories and benchmark entry points
// ============================================================================

/// `create_reranker(reranker_type, **kwargs)`: `config` stands for the keyword arguments; its `reranker_type`
/// is replaced by the argument. Unknown types are the Python `ValueError`.
pub fn create_reranker<'a>(
    reranker_type: &str,
    llm: &'a dyn Llm,
    config: RerankerConfig,
) -> Result<Box<dyn Reranker + 'a>, String> {
    let config = RerankerConfig { reranker_type: reranker_type.to_string(), ..config };
    match reranker_type {
        "llm" => Ok(Box::new(LlmReranker::new(llm, Some(config)))),
        "semantic" => Ok(Box::new(SemanticBoostReranker::new(Some(config)))),
        "hybrid" => Ok(Box::new(HybridReranker::new(llm, Some(config)))),
        other => Err(format!("Unknown reranker type: {other}")),
    }
}

/// `rerank_candidates(query, candidates, reranker_type="semantic", limit=10, **config_kwargs)`.
pub async fn rerank_candidates(
    llm: &dyn Llm,
    query: &str,
    candidates: &[(NodeRef, f64)],
    reranker_type: &str,
    limit: i64,
    config: RerankerConfig,
) -> Result<Vec<(NodeRef, f64)>, String> {
    let reranker = create_reranker(reranker_type, llm, config)?;
    let out = reranker.rerank(query, candidates, limit).await;
    reranker.close().await;
    Ok(out)
}

/// Keyword arguments of `rerank_candidates_parallel` (defaults as in Python; the model and URL default to
/// `LLM_MODEL` / `LLM_BASE_URL`).
#[derive(Clone, Debug, PartialEq)]
pub struct ParallelRerankOptions {
    pub limit: i64,
    pub llm_model: String,
    pub llm_base_url: String,
    pub timeout_seconds: f64,
    pub max_candidates: i64,
    pub score_boost: f64,
}

impl Default for ParallelRerankOptions {
    fn default() -> Self {
        ParallelRerankOptions {
            limit: 15,
            llm_model: llm_model(),
            llm_base_url: llm_base_url(),
            timeout_seconds: 10.0,
            max_candidates: 50,
            score_boost: 0.2,
        }
    }
}

/// `rerank_candidates_parallel(query, candidates, ...)`: score the first `max_candidates` candidates
/// concurrently with the LLM prompt (`max_tokens` 20, temperature 0, `timeout_seconds` per call; a failed call
/// scores 1), sort by (score, charge) descending — ties keep candidate order — and return the top `limit` as
/// `(node, charge + score * score_boost)`.
pub async fn rerank_candidates_parallel(
    llm: &dyn Llm,
    query: &str,
    candidates: &[(NodeRef, f64)],
    opts: &ParallelRerankOptions,
) -> Vec<(NodeRef, f64)> {
    if candidates.is_empty() {
        return Vec::new();
    }
    let config = RerankerConfig {
        reranker_type: "llm".into(),
        llm_model: opts.llm_model.clone(),
        llm_base_url: opts.llm_base_url.clone(),
        llm_timeout_seconds: opts.timeout_seconds,
        llm_max_tokens: 20,
        max_candidates: opts.max_candidates,
        ..RerankerConfig::default()
    };
    let reranker = LlmReranker::new(llm, Some(config));
    let candidates = &candidates[..py_prefix_len(candidates.len(), opts.max_candidates)];

    let inputs: Vec<(String, String)> =
        candidates.iter().map(|(n, _)| (n.borrow().content.clone(), n.borrow().speaker_id())).collect();
    let tasks = inputs.iter().map(|(content, speaker)| reranker.score_candidate(query, content, speaker));
    let scores = futures::future::join_all(tasks).await;

    let mut scored: Vec<(NodeRef, f64, f64)> =
        candidates.iter().zip(scores).map(|((node, charge), score)| (node.clone(), *charge, score)).collect();
    scored.sort_by(|a, b| (b.2, b.1).partial_cmp(&(a.2, a.1)).unwrap_or(std::cmp::Ordering::Equal));

    let n = py_prefix_len(scored.len(), opts.limit);
    let result =
        scored[..n].iter().map(|(node, charge, score)| (node.clone(), charge + (score * opts.score_boost))).collect();
    reranker.close().await;
    result
}
