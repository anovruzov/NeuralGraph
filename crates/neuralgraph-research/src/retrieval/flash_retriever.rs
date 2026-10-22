//! Flash retrieval: every resonance signal scored in one pass (port of `retrieval/flash_retriever.py`).
//!
//! [`FlashRetriever`] scores every node of the session at once (entity, keyword, declarative-pattern, semantic,
//! wave, temporal-metadata, speaker, heat and rare-term "specificity" signals); [`HybridFlashRetriever`] merges
//! that with a vector search and the entity index, then follows ENTITY/SEMANTIC edges one hop from the top 10.
//! The Python ran the three tracks with `asyncio.gather`; storage never suspended, so they ran one after the
//! other in the order written, which is what this does.
//!
//! The query-analysis helpers (`_extract_entities`, `_extract_temporal_tokens`, `_parse_node_date`,
//! `_compute_wave_alignment`) were copies of the retriever's and are shared with [`crate::retrieval::retriever`];
//! the keyword stop-word list is this module's own (shorter) one.
//!
//! Python behaviour kept:
//! - Wave interference used `InterferenceScorer.score_memory_match` with its default reference (2020-01-01 UTC),
//!   so a node with a naive `created_at` raised `TypeError` ([`RetrieverError::TypeError`]).
//! - The speaker binding called `.lower()` on `node.speaker_id`, which returned the raw metadata value: a
//!   non-string speaker raised `AttributeError`.
//! - The multi-hop expansion was wrapped in `try/except Exception: pass`; nothing in it can fail, so there is no
//!   error path to swallow here.

use crate::pytime::PyDateTime;
use crate::retrieval::data_types::{cosine_similarity, EdgeType, NeuralNode, NodeRef, RetrievalResult};
use crate::retrieval::interference::{InterferenceScorer, InterferenceType};
use crate::retrieval::pytext::{py_regex, py_search, py_type_name};
use crate::retrieval::retriever::{
    compute_keyword_score, compute_wave_alignment, date_distance_days, extract_entities, extract_keywords_with,
    extract_temporal_tokens, node_temporal_tokens, parse_node_date, sort_desc, RetrieverError, WaveAmplitudes,
};
use crate::retrieval::storage::NeuralGraphStorage;
use fancy_regex::Regex;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_len, py_strip, py_truthy};
use serde_json::Value;
use std::rc::Rc;
use std::sync::LazyLock;
use std::time::Instant;

/// Configuration for flash retrieval.
#[derive(Clone, Debug, PartialEq)]
pub struct FlashConfig {
    pub final_limit: usize,
    pub semantic_weight: f64,
    pub entity_weight: f64,
    pub keyword_weight: f64,
    pub wave_weight: f64,
    pub temporal_weight: f64,
    pub min_score: f64,
    pub entity_match_boost: f64,
    pub speaker_match_boost: f64,
}

impl Default for FlashConfig {
    fn default() -> Self {
        FlashConfig {
            final_limit: 20,
            semantic_weight: 0.40,
            entity_weight: 0.25,
            keyword_weight: 0.20,
            wave_weight: 0.10,
            temporal_weight: 0.15,
            min_score: 0.10,
            entity_match_boost: 0.40,
            speaker_match_boost: 0.35,
        }
    }
}

/// The flash retriever's stop words.
#[rustfmt::skip]
pub const FLASH_STOP_WORDS: &[&str] = &[
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "shall", "can", "to", "of", "in", "for", "on", "with",
    "at", "by", "from", "as", "into", "through", "during", "before", "after", "above", "below", "between", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "and", "but", "if", "or", "because", "until", "while", "what", "which", "who", "whom", "this", "that", "these",
    "those", "it", "its", "they", "them", "their", "you", "your", "he", "him", "his", "she", "her", "we", "us", "our",
    "i", "me", "my", "about",
];

/// Declarative "fact" patterns and their boost; the first that matches the lower-cased content counts.
static FACTUAL_PATTERNS: LazyLock<Vec<(Regex, f64)>> = LazyLock::new(|| {
    [
        (r"\bi am\b", 0.15),
        (r"\bi'm\b", 0.15),
        (r"\bmy \w+ is\b", 0.12),
        (r"\b(?:is|was|are|were) (?:a |an |the )?\w+", 0.10),
        (r"\bi (?:do|did|went|go|like|love|enjoy)\b", 0.08),
        (r"\b(?:from|moved from|came from|live in|lives in)\b", 0.12),
        (r"\b(?:single|married|dating|relationship|partner)\b", 0.15),
    ]
    .into_iter()
    .map(|(p, b)| (py_regex(p), b))
    .collect()
});

static PROPER_NOUN_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"(?<=[a-z]\s)[A-Z][a-zA-Z]+\b"));
static CAPITALISED_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[A-Z][a-z]{2,}\b"));
static DOUBLE_QUOTED_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r#""([^"]+)""#));
static SINGLE_QUOTED_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"'([^']{2,})'"));
static NUMBER_RES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"\b\d+\s*(?:years?|months?|weeks?|days?|hours?|minutes?|times?)\b",
        r"\b\d+(?:st|nd|rd|th)\b",
        r"\b(?:19|20)\d{2}\b",
        r"\$\d+(?:,\d{3})*(?:\.\d{2})?",
        r"\b\d+(?:\.\d+)?\s*(?:kg|lb|km|mi|m|ft|cm|mm)\b",
    ]
    .into_iter()
    .map(py_regex)
    .collect()
});
static HYPHENATED_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[a-zA-Z]+-[a-zA-Z]+\b"));
static ACRONYM_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[A-Z]{2,}[+]?\b"));
static POSSESSIVE_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b([A-Z][a-z]+)'s\b"));
static TITLE_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"));
static LONG_WORD_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"\b[a-zA-Z]{4,}\b"));
static DOUBLE_VOWEL_RE: LazyLock<Regex> = LazyLock::new(|| py_regex(r"[aeiou]{2,}"));

#[rustfmt::skip]
const COMMON_CAPS: &[&str] = &[
    "the", "what", "when", "where", "who", "how", "why", "which", "this", "that", "there", "here", "would", "could",
    "should",
];

/// `re.findall` for a pattern without groups.
fn findall(re: &Regex, text: &str) -> Vec<String> {
    re.find_iter(text).map(|m| m.expect("regex backtrack limit").as_str().to_string()).collect()
}

/// `re.findall` for a pattern with one group.
fn findall_group(re: &Regex, text: &str) -> Vec<String> {
    re.captures_iter(text)
        .map(|c| c.expect("regex backtrack limit").get(1).map(|m| m.as_str().to_string()).unwrap_or_default())
        .collect()
}

/// `node.speaker_id.lower()`: `speaker_id` returned `metadata["speaker"] or metadata["producer_id"] or ""` as
/// stored, so a non-string value had no `.lower()`.
fn speaker_lower(node: &NeuralNode) -> Result<String, RetrieverError> {
    let pick = |k: &str| node.metadata.get(k).filter(|v| py_truthy(v));
    match pick("speaker").or_else(|| pick("producer_id")) {
        None => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.to_lowercase()),
        Some(v) => {
            Err(RetrieverError::AttributeError(format!("'{}' object has no attribute 'lower'", py_type_name(v))))
        }
    }
}

/// Query signals extracted once per retrieval.
struct QuerySignals {
    entities_lower: IndexSet<String>,
    keywords: IndexSet<String>,
    specifics: IndexSet<String>,
    temporal_tokens: IndexSet<String>,
    target_date: Option<PyDateTime>,
}

/// One-pass resonance retrieval over all nodes of a session.
pub struct FlashRetriever {
    storage: Rc<dyn NeuralGraphStorage>,
    config: FlashConfig,
}

impl FlashRetriever {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<FlashConfig>) -> Self {
        FlashRetriever { storage, config: config.unwrap_or_default() }
    }

    pub fn config(&self) -> &FlashConfig {
        &self.config
    }

    /// Score every node of the session, keep those reaching `min_score`, best first (`limit` `None`/0: the
    /// configured `final_limit`).
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: Option<usize>,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<RetrievalResult, RetrieverError> {
        let start = Instant::now();
        let final_limit = limit.filter(|l| *l != 0).unwrap_or(self.config.final_limit);
        let (temporal_tokens, target_date) = extract_temporal_tokens(query_text);
        let signals = QuerySignals {
            entities_lower: extract_entities(query_text).iter().map(|e| e.to_lowercase()).collect(),
            keywords: self.extract_keywords(query_text),
            specifics: self.extract_specifics(query_text),
            temporal_tokens,
            target_date,
        };
        let all_nodes = self.storage.get_nodes_by_session(session_key, None);
        let mut scored_nodes: Vec<(NodeRef, f64)> = Vec::new();
        for node in &all_nodes {
            let score = self.resonance(&node.borrow(), query_embedding, &signals, query_wave_amplitudes)?;
            if score >= self.config.min_score {
                scored_nodes.push((node.clone(), score));
            }
        }
        sort_desc(&mut scored_nodes);
        scored_nodes.truncate(final_limit);
        Ok(RetrievalResult {
            query_text: query_text.to_string(),
            nodes: scored_nodes,
            stages_executed: vec!["flash_resonance".into()],
            total_candidates_seen: all_nodes.len(),
            co_activations_recorded: 0,
            total_time_ms: start.elapsed().as_secs_f64() * 1000.0,
        })
    }

    /// `_compute_resonance` with the query signals extracted from `query_text` (the Python took them
    /// pre-extracted; this entry point exists so a single node can be scored).
    pub fn compute_resonance(
        &self,
        node: &NeuralNode,
        query_text: &str,
        query_embedding: &[f64],
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<f64, RetrieverError> {
        let (temporal_tokens, target_date) = extract_temporal_tokens(query_text);
        let signals = QuerySignals {
            entities_lower: extract_entities(query_text).iter().map(|e| e.to_lowercase()).collect(),
            keywords: self.extract_keywords(query_text),
            specifics: self.extract_specifics(query_text),
            temporal_tokens,
            target_date,
        };
        self.resonance(node, query_embedding, &signals, query_wave_amplitudes)
    }

    fn resonance(
        &self,
        node: &NeuralNode,
        query_embedding: &[f64],
        q: &QuerySignals,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<f64, RetrieverError> {
        let cfg = &self.config;
        let mut score = 0.0;
        let node_embedding = node.embedding.as_ref().filter(|e| !e.is_empty());
        let has_semantic = node_embedding.is_some() && !query_embedding.is_empty();
        let waves = query_wave_amplitudes.filter(|w| !w.is_empty());
        let has_wave = waves.is_some() && !node.wave_amplitudes.is_empty();
        let (mut entity_weight, mut keyword_weight) = (cfg.entity_weight, cfg.keyword_weight);
        if !has_semantic && !has_wave {
            entity_weight = 0.50;
            keyword_weight = 0.40;
        }
        let content_lower = node.content.to_lowercase();

        // 1. entity resonance (entity ids, plus query entities found in the content)
        if !q.entities_lower.is_empty() {
            let mut node_entities: IndexSet<String> = node.entity_ids.iter().map(|e| e.to_lowercase()).collect();
            for entity in &q.entities_lower {
                if content_lower.contains(entity.as_str()) {
                    node_entities.insert(entity.clone());
                }
            }
            let overlap = q.entities_lower.iter().filter(|e| node_entities.contains(*e)).count();
            if overlap > 0 {
                score += entity_weight * (overlap as f64 / q.entities_lower.len() as f64);
                if overlap == q.entities_lower.len() {
                    score += cfg.entity_match_boost;
                }
            }
        }

        // 2. keyword resonance
        if !q.keywords.is_empty() {
            let matches = q.keywords.iter().filter(|kw| content_lower.contains(kw.as_str())).count();
            if matches > 0 {
                score += keyword_weight * (matches as f64 / q.keywords.len() as f64);
            }
        }

        // 2.5 declarative patterns
        for (pattern, boost) in FACTUAL_PATTERNS.iter() {
            if py_search(pattern, &content_lower) {
                score += boost;
                break;
            }
        }

        // 3. semantic resonance
        if let (Some(emb), false) = (node_embedding, query_embedding.is_empty()) {
            let sim = cosine_similarity(query_embedding, emb);
            if sim > 0.2 {
                if sim > 0.6 {
                    score += cfg.semantic_weight * sim * 1.5;
                } else {
                    score += cfg.semantic_weight * sim;
                }
            }
        }

        // 4. wave resonance and interference
        if let (Some(w), false) = (waves, node.wave_amplitudes.is_empty()) {
            score += cfg.wave_weight * compute_wave_alignment(w, &node.wave_amplitudes);
            let (activation, itype) = InterferenceScorer::score_memory_match(w, node, None)?;
            if itype == InterferenceType::Constructive {
                score += 0.15 * activation;
            }
        }

        // 4.5 temporal metadata: token overlap and date proximity
        if !q.temporal_tokens.is_empty() {
            let node_tokens = node_temporal_tokens(node)?;
            let overlap = node_tokens.iter().filter(|t| q.temporal_tokens.contains(*t)).count();
            if overlap != 0 {
                let overlap_ratio = overlap as f64 / q.temporal_tokens.len().max(1) as f64;
                score += cfg.temporal_weight * overlap_ratio;
            }
            let node_date = parse_node_date(node)?;
            if let (Some(qd), Some(nd)) = (q.target_date.as_ref(), node_date.as_ref()) {
                let delta_days = date_distance_days(nd, qd);
                if delta_days <= 1 {
                    score += 0.12;
                } else if delta_days <= 7 {
                    score += 0.08;
                } else if delta_days <= 31 {
                    score += 0.04;
                }
            }
        }

        // 5. speaker is the queried entity
        if !q.entities_lower.is_empty() {
            let speaker = speaker_lower(node)?;
            let speaker = py_strip(&speaker);
            if !speaker.is_empty() && q.entities_lower.contains(speaker) {
                score += cfg.speaker_match_boost;
            }
        }

        // 6. heat
        if node.heat_score > 0.0 {
            score += cfg.temporal_weight * f64::min(1.0, node.heat_score / 5.0);
        }

        // 7. rare-term specificity
        if !q.specifics.is_empty() {
            score += Self::compute_specificity_bonus(&q.specifics, &node.content);
        }
        Ok(score)
    }

    /// `_extract_keywords` with this module's stop words.
    pub fn extract_keywords(&self, text: &str) -> IndexSet<String> {
        extract_keywords_with(text, FLASH_STOP_WORDS)
    }

    /// `_extract_entities`.
    pub fn extract_entities(&self, text: &str) -> Vec<String> {
        extract_entities(text)
    }

    /// `_extract_specifics`: proper nouns, quoted strings, numbers with units/ordinals/years/money,
    /// hyphenated compounds, acronyms, possessives, title-like sequences and "unusual" long words.
    pub fn extract_specifics(&self, text: &str) -> IndexSet<String> {
        let mut specifics: IndexSet<String> = IndexSet::new();
        let text_lower = text.to_lowercase();
        for w in findall(&PROPER_NOUN_RE, text) {
            if py_len(&w) > 2 {
                specifics.insert(w.to_lowercase());
            }
        }
        for w in findall(&CAPITALISED_RE, text) {
            let lower = w.to_lowercase();
            if !COMMON_CAPS.contains(&lower.as_str()) {
                specifics.insert(lower);
            }
        }
        for q in findall_group(&DOUBLE_QUOTED_RE, text) {
            specifics.insert(q.to_lowercase());
        }
        for q in findall_group(&SINGLE_QUOTED_RE, text) {
            specifics.insert(q.to_lowercase());
        }
        for re in NUMBER_RES.iter() {
            specifics.extend(findall(re, &text_lower));
        }
        for h in findall(&HYPHENATED_RE, text) {
            specifics.insert(h.to_lowercase());
        }
        for a in findall(&ACRONYM_RE, text) {
            specifics.insert(a.to_lowercase());
        }
        for p in findall_group(&POSSESSIVE_RE, text) {
            specifics.insert(p.to_lowercase());
        }
        for t in findall_group(&TITLE_RE, text) {
            specifics.insert(t.to_lowercase());
        }
        for token in findall(&LONG_WORD_RE, &text_lower) {
            if FLASH_STOP_WORDS.contains(&token.as_str()) {
                continue;
            }
            if token.contains(['x', 'z', 'q'])
                || py_search(&DOUBLE_VOWEL_RE, &token)
                || ["tion", "sion", "ment", "ness", "ship"].iter().any(|s| token.ends_with(s))
            {
                specifics.insert(token);
            }
        }
        specifics
    }

    /// `_compute_specificity_bonus`: up to 0.35 for the share of query specifics found in the content.
    pub fn compute_specificity_bonus(query_specifics: &IndexSet<String>, node_content: &str) -> f64 {
        if query_specifics.is_empty() {
            return 0.0;
        }
        let matches = compute_keyword_score(node_content, query_specifics);
        if matches == 0.0 {
            return 0.0;
        }
        matches * 0.35
    }
}

/// Flash resonance + vector search + entity index, merged, with a one-hop ENTITY/SEMANTIC expansion.
pub struct HybridFlashRetriever {
    storage: Rc<dyn NeuralGraphStorage>,
    config: FlashConfig,
    flash: FlashRetriever,
}

impl HybridFlashRetriever {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<FlashConfig>) -> Self {
        let config = config.unwrap_or_default();
        let flash = FlashRetriever::new(storage.clone(), Some(config.clone()));
        HybridFlashRetriever { storage, config, flash }
    }

    pub fn config(&self) -> &FlashConfig {
        &self.config
    }

    pub fn flash(&self) -> &FlashRetriever {
        &self.flash
    }

    /// Merge the three tracks: flash results (`2 * limit`), vector hits (`+0.5 * sim`, or `2 * semantic_weight *
    /// sim` when new), entity-index hits (`+0.25`, or 0.5 plus the speaker boost when new); then unless
    /// `query_wave_amplitudes["__suppress_multi_hop"]` is set, follow the first 5 ENTITY and 5 SEMANTIC
    /// edges of the top 10 (`0.4 * score * weight`, kept above `min_score`).
    pub fn retrieve(
        &self,
        query_text: &str,
        query_embedding: &[f64],
        session_key: &str,
        limit: Option<usize>,
        query_wave_amplitudes: Option<&WaveAmplitudes>,
    ) -> Result<RetrievalResult, RetrieverError> {
        let start = Instant::now();
        let final_limit = limit.filter(|l| *l != 0).unwrap_or(self.config.final_limit);
        let query_entities = self.flash.extract_entities(query_text);

        let flash_result = self.flash.retrieve(
            query_text,
            query_embedding,
            session_key,
            Some(final_limit * 2),
            query_wave_amplitudes,
        )?;
        let vector_task = !query_embedding.is_empty();
        let vector_nodes = if vector_task {
            self.storage.vector_search(query_embedding, session_key, final_limit, None)
        } else {
            vec![]
        };
        let entity_task = !query_entities.is_empty();
        let entity_nodes =
            if entity_task { self.storage.find_nodes_by_entities(&query_entities, session_key) } else { vec![] };

        let mut candidates: IndexMap<String, (NodeRef, f64)> = IndexMap::new();
        for (node, score) in &flash_result.nodes {
            candidates.insert(node.borrow().node_id.clone(), (node.clone(), *score));
        }
        for (node, sim) in &vector_nodes {
            let id = node.borrow().node_id.clone();
            let score = match candidates.get(&id) {
                Some((_, old)) => old + sim * 0.5,
                None => sim * self.config.semantic_weight * 2.0,
            };
            candidates.insert(id, (node.clone(), score));
        }
        if !query_entities.is_empty() {
            let entities_lower: IndexSet<String> = query_entities.iter().map(|e| e.to_lowercase()).collect();
            for node in entity_nodes.iter().take(30) {
                let id = node.borrow().node_id.clone();
                let score = match candidates.get(&id) {
                    Some((_, old)) => old + 0.25,
                    None => {
                        let mut score = 0.5;
                        if entities_lower.contains(&speaker_lower(&node.borrow())?) {
                            score += self.config.speaker_match_boost;
                        }
                        score
                    }
                };
                candidates.insert(id, (node.clone(), score));
            }
        }

        let suppress_multi_hop =
            query_wave_amplitudes.and_then(|w| w.get("__suppress_multi_hop")).is_some_and(|v| *v != 0.0);
        if !candidates.is_empty() && !suppress_multi_hop {
            let mut top_items: Vec<(String, f64)> = candidates.iter().map(|(k, (_, s))| (k.clone(), *s)).collect();
            sort_desc(&mut top_items);
            top_items.truncate(10);
            for (nid, base_score) in top_items {
                let mut all_edges = self.storage.get_edges_from(&nid, Some(&[EdgeType::Entity]));
                all_edges.truncate(5);
                let mut semantic = self.storage.get_edges_from(&nid, Some(&[EdgeType::Semantic]));
                semantic.truncate(5);
                all_edges.extend(semantic);
                for edge in all_edges {
                    let target_id = edge.borrow().target_id.clone();
                    if candidates.contains_key(&target_id) {
                        continue;
                    }
                    if let Some(target) = self.storage.get_node(&target_id) {
                        let hop_score = base_score * 0.4 * edge.borrow_mut().effective_weight();
                        if hop_score > self.config.min_score {
                            let id = target.borrow().node_id.clone();
                            candidates.insert(id, (target, hop_score));
                        }
                    }
                }
            }
        }

        let mut final_nodes: Vec<(NodeRef, f64)> = candidates.into_values().collect();
        sort_desc(&mut final_nodes);
        final_nodes.truncate(final_limit);
        let mut stages = vec!["flash_resonance".to_string()];
        if vector_task {
            stages.push("vector_search".into());
        }
        if entity_task {
            stages.push("entity_index".into());
        }
        Ok(RetrievalResult {
            query_text: query_text.to_string(),
            nodes: final_nodes,
            stages_executed: stages,
            total_candidates_seen: flash_result.total_candidates_seen + vector_nodes.len() + entity_nodes.len(),
            co_activations_recorded: 0,
            total_time_ms: start.elapsed().as_secs_f64() * 1000.0,
        })
    }
}
