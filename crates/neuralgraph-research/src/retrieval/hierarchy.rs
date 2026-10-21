//! The four-layer node hierarchy (port of `retrieval/hierarchy.py`).
//!
//! Messages (layer 0) are consolidated into episodes (1), episodes clustered into topics (2), topics synthesised
//! into persona facets (3); each step aggregates embeddings, wave amplitudes, entities and scores, links parent
//! and children, and adds HIERARCHY edges. The Python methods were `async` only because storage was; these are
//! synchronous and the manager shares the storage handle (`Rc`) with the rest of the engine.
//!
//! Ordering: Python collected wave dimensions and entity ids in `set`s and listed them back, an order that
//! changed with the process's hash seed. Here they keep first-seen order (`IndexSet`); the golden generator
//! patches the reference's `set` the same way.
//!
//! [`EngineError`] carries the exceptions the Python raised (`ValueError` when nothing valid was given,
//! `TypeError` when naive and aware datetimes meet); it is shared by `temporal` and `consolidation`.

use crate::pytime::{total_seconds, PyDateTime};
use crate::retrieval::data_types::{
    cosine_similarity, edge_ref, generate_edge_id, generate_node_id, node_ref, ConsolidationState, EdgeType,
    NeuralEdge, NeuralNode, NodeLayer, NodeRef,
};
use crate::retrieval::storage::NeuralGraphStorage;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::py_truthy;
use serde_json::{json, Map, Value};
use std::cmp::Ordering;
use std::rc::Rc;

/// An exception the Python engine raised.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EngineError {
    ValueError(String),
    TypeError(String),
    AttributeError(String),
    KeyError(String),
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EngineError::ValueError(m) => write!(f, "ValueError: {m}"),
            EngineError::TypeError(m) => write!(f, "TypeError: {m}"),
            EngineError::AttributeError(m) => write!(f, "AttributeError: {m}"),
            EngineError::KeyError(m) => write!(f, "KeyError: {m}"),
        }
    }
}

impl std::error::Error for EngineError {}

impl From<crate::pytime::NaiveAwareMismatch> for EngineError {
    fn from(e: crate::pytime::NaiveAwareMismatch) -> Self {
        EngineError::TypeError(e.to_string())
    }
}

/// `TypeError` of comparing a naive with an aware datetime.
pub(crate) fn compare_error() -> EngineError {
    EngineError::TypeError("can't compare offset-naive and offset-aware datetimes".into())
}

/// `list.sort(key=lambda n: n.created_at)` (stable). Sorting a list that mixes naive and aware datetimes always
/// compares the two kinds somewhere, which raised `TypeError` in Python.
pub(crate) fn sort_by_created_at(nodes: &mut [NodeRef]) -> Result<(), EngineError> {
    let times: Vec<PyDateTime> = nodes.iter().map(|n| n.borrow().created_at).collect();
    if times.len() > 1 && times.iter().any(|t| t.is_aware() != times[0].is_aware()) {
        return Err(compare_error());
    }
    nodes.sort_by(|a, b| a.borrow().created_at.py_cmp(&b.borrow().created_at).unwrap_or(Ordering::Equal));
    Ok(())
}

/// `max(values)`: the first maximal value (later values replace it only when strictly greater).
pub(crate) fn py_max_iter(values: impl Iterator<Item = f64>) -> Option<f64> {
    values.fold(None, |best, v| match best {
        Some(b) if v > b => Some(v),
        Some(b) => Some(b),
        None => Some(v),
    })
}

/// Configuration for hierarchy management.
#[derive(Clone, Debug, PartialEq)]
pub struct HierarchyConfig {
    pub min_messages_per_episode: usize,
    pub max_messages_per_episode: usize,
    pub episode_time_gap_hours: f64,
    pub min_episodes_per_topic: usize,
    pub topic_similarity_threshold: f64,
    pub min_topics_per_persona: usize,
    pub persona_stability_threshold: f64,
}

impl Default for HierarchyConfig {
    fn default() -> Self {
        HierarchyConfig {
            min_messages_per_episode: 5,
            max_messages_per_episode: 50,
            episode_time_gap_hours: 6.0,
            min_episodes_per_topic: 2,
            topic_similarity_threshold: 0.6,
            min_topics_per_persona: 3,
            persona_stability_threshold: 0.8,
        }
    }
}

/// Keyword arguments of `create_message_node` besides `content` and `session_key`.
///
/// The nine temporal fields are written into the metadata as `field or metadata.get(field)` (Python
/// truthiness: `None`, `""`, `0`, `[]`, `{}` fall back to the metadata entry, else `null`). `created_at` is the
/// `created_at` keyword the Python popped out of `**metadata` (default: now, UTC).
#[derive(Clone, Debug)]
pub struct MessageNodeArgs {
    pub embedding: Option<Vec<f64>>,
    pub wave_amplitudes: Option<IndexMap<String, f64>>,
    pub entity_ids: Option<Vec<String>>,
    pub importance_score: f64,
    pub resolved_date: Value,
    pub temporal_tokens: Value,
    pub temporal_metadata: Value,
    pub resolved_dates: Value,
    pub explicit_date: Value,
    pub duration_years: Value,
    pub duration_months: Value,
    pub since_year: Value,
    pub since_date: Value,
    pub created_at: Option<PyDateTime>,
    /// `**metadata`.
    pub metadata: Map<String, Value>,
}

impl Default for MessageNodeArgs {
    fn default() -> Self {
        MessageNodeArgs {
            embedding: None,
            wave_amplitudes: None,
            entity_ids: None,
            importance_score: 0.5,
            resolved_date: Value::Null,
            temporal_tokens: Value::Null,
            temporal_metadata: Value::Null,
            resolved_dates: Value::Null,
            explicit_date: Value::Null,
            duration_years: Value::Null,
            duration_months: Value::Null,
            since_year: Value::Null,
            since_date: Value::Null,
            created_at: None,
            metadata: Map::new(),
        }
    }
}

/// Union of wave-amplitude keys in first-seen order.
fn wave_keys(all_amps: &[IndexMap<String, f64>]) -> IndexSet<String> {
    let mut keys = IndexSet::new();
    for a in all_amps {
        keys.extend(a.keys().cloned());
    }
    keys
}

/// Mean of each wave dimension over the given amplitude maps (missing dimensions count as 0.0).
fn mean_waves(all_amps: &[IndexMap<String, f64>]) -> IndexMap<String, f64> {
    let mut agg = IndexMap::new();
    for key in wave_keys(all_amps) {
        let mut sum = 0.0;
        for a in all_amps {
            sum += a.get(&key).copied().unwrap_or(0.0);
        }
        agg.insert(key, sum / all_amps.len() as f64);
    }
    agg
}

/// Component-wise mean of embeddings (`sum(e[i] for e in embeddings) / len(embeddings)`).
fn centroid(embeddings: &[Vec<f64>]) -> Vec<f64> {
    let dim = embeddings[0].len();
    (0..dim)
        .map(|i| {
            let mut sum = 0.0;
            for e in embeddings {
                sum += e[i];
            }
            sum / embeddings.len() as f64
        })
        .collect()
}

/// Union of entity ids in first-seen order.
fn entity_union<'a>(lists: impl Iterator<Item = &'a Vec<String>>) -> Vec<String> {
    let mut all: IndexSet<String> = IndexSet::new();
    for l in lists {
        all.extend(l.iter().cloned());
    }
    all.into_iter().collect()
}

/// Sequential `sum(...) / len(...)`.
fn mean(values: impl Iterator<Item = f64>) -> f64 {
    let mut sum = 0.0;
    let mut n = 0usize;
    for v in values {
        sum += v;
        n += 1;
    }
    sum / n as f64
}

/// Manager of the message / episode / topic / persona hierarchy.
#[derive(Clone)]
pub struct HierarchyManager {
    storage: Rc<dyn NeuralGraphStorage>,
    config: HierarchyConfig,
}

impl HierarchyManager {
    pub fn new(storage: Rc<dyn NeuralGraphStorage>, config: Option<HierarchyConfig>) -> Self {
        HierarchyManager { storage, config: config.unwrap_or_default() }
    }

    pub fn config(&self) -> &HierarchyConfig {
        &self.config
    }

    pub fn storage(&self) -> &Rc<dyn NeuralGraphStorage> {
        &self.storage
    }

    fn save_hierarchy_edge(&self, parent_id: &str, child_id: &str) {
        let edge = NeuralEdge::new(generate_edge_id(), parent_id, child_id, EdgeType::Hierarchy);
        self.storage.save_edge(&edge_ref(edge));
    }

    // ---------------------------------------------------------------------------------------------
    // Layer 0: messages
    // ---------------------------------------------------------------------------------------------

    /// Create and save a message node; its initial heat equals its importance.
    pub fn create_message_node(&self, content: &str, session_key: &str, args: MessageNodeArgs) -> NodeRef {
        let MessageNodeArgs {
            embedding,
            wave_amplitudes,
            entity_ids,
            importance_score,
            resolved_date,
            temporal_tokens,
            temporal_metadata,
            resolved_dates,
            explicit_date,
            duration_years,
            duration_months,
            since_year,
            since_date,
            created_at,
            mut metadata,
        } = args;
        // `created_at` is a keyword parameter; it can only reach `**metadata` through a metadata map
        metadata.shift_remove("created_at");
        let fields = [
            ("resolved_date", resolved_date),
            ("temporal_tokens", temporal_tokens),
            ("temporal_metadata", temporal_metadata),
            ("resolved_dates", resolved_dates),
            ("explicit_date", explicit_date),
            ("duration_years", duration_years),
            ("duration_months", duration_months),
            ("since_year", since_year),
            ("since_date", since_date),
        ];
        let mut merged = metadata.clone();
        for (key, value) in fields {
            let v = if py_truthy(&value) { value } else { metadata.get(key).cloned().unwrap_or(Value::Null) };
            merged.insert(key.to_string(), v);
        }
        let mut node = NeuralNode::new(generate_node_id(), NodeLayer::Message, content);
        node.embedding = embedding;
        node.wave_amplitudes = wave_amplitudes.unwrap_or_default();
        node.entity_ids = entity_ids.unwrap_or_default();
        node.session_key = session_key.to_string();
        node.importance_score = importance_score;
        node.heat_score = importance_score;
        node.metadata = merged;
        node.created_at = created_at.unwrap_or_else(PyDateTime::now_utc);
        let node = node_ref(node);
        self.storage.save_node(&node);
        node
    }

    /// Create message nodes from dictionaries with `content`, `embedding`, `wave_amplitudes`, `entity_ids`,
    /// `importance_score` and `metadata` (whose entries are passed as keyword arguments, so `resolved_date` and
    /// friends bind to their parameters). A `created_at` metadata entry is parsed with `fromisoformat`: JSON
    /// cannot carry the `datetime` object the Python expected there.
    pub fn create_message_nodes_batch(&self, messages: &[Map<String, Value>], session_key: &str) -> Vec<NodeRef> {
        let mut nodes = Vec::new();
        for msg in messages {
            let content = msg.get("content").and_then(Value::as_str).unwrap_or("");
            let mut metadata = msg.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default();
            let mut take = |k: &str| metadata.shift_remove(k).unwrap_or(Value::Null);
            let mut args = MessageNodeArgs {
                embedding: msg
                    .get("embedding")
                    .and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(Value::as_f64).collect()),
                wave_amplitudes: msg
                    .get("wave_amplitudes")
                    .and_then(Value::as_object)
                    .map(|o| o.iter().filter_map(|(k, v)| v.as_f64().map(|f| (k.clone(), f))).collect()),
                entity_ids: msg
                    .get("entity_ids")
                    .and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect()),
                importance_score: msg.get("importance_score").and_then(Value::as_f64).unwrap_or(0.5),
                resolved_date: take("resolved_date"),
                temporal_tokens: take("temporal_tokens"),
                temporal_metadata: take("temporal_metadata"),
                resolved_dates: take("resolved_dates"),
                explicit_date: take("explicit_date"),
                duration_years: take("duration_years"),
                duration_months: take("duration_months"),
                since_year: take("since_year"),
                since_date: take("since_date"),
                created_at: None,
                metadata: Map::new(),
            };
            args.created_at =
                metadata.shift_remove("created_at").and_then(|v| v.as_str().and_then(PyDateTime::fromisoformat));
            args.metadata = metadata;
            nodes.push(self.create_message_node(content, session_key, args));
        }
        nodes
    }

    // ---------------------------------------------------------------------------------------------
    // Layer 1: episodes
    // ---------------------------------------------------------------------------------------------

    /// Consolidate messages into an episode: importance x heat weighted embedding (mean when all weights are
    /// zero), max-pooled waves, entity union, mean importance, max heat. Messages become ARCHIVED children.
    pub fn consolidate_to_episode(
        &self,
        message_ids: &[String],
        session_key: &str,
        summary: Option<&str>,
        metadata: Map<String, Value>,
    ) -> Result<NodeRef, EngineError> {
        let messages: Vec<NodeRef> = message_ids
            .iter()
            .filter_map(|mid| self.storage.get_node(mid))
            .filter(|m| m.borrow().layer == NodeLayer::Message)
            .collect();
        if messages.is_empty() {
            return Err(EngineError::ValueError("No valid message nodes found to consolidate".into()));
        }

        let with_embeddings: Vec<(Vec<f64>, f64, f64)> = messages
            .iter()
            .filter_map(|m| {
                let m = m.borrow();
                m.embedding.as_ref().filter(|e| !e.is_empty()).map(|e| (e.clone(), m.importance_score, m.heat_score))
            })
            .collect();
        let agg_embedding = if with_embeddings.is_empty() {
            None
        } else {
            let embedding_dim = with_embeddings[0].0.len();
            let mut total_weight = 0.0;
            for (_, imp, heat) in &with_embeddings {
                total_weight += imp * heat;
            }
            if total_weight > 0.0 {
                Some(
                    (0..embedding_dim)
                        .map(|i| {
                            let mut weighted_val = 0.0;
                            for (e, imp, heat) in &with_embeddings {
                                weighted_val += e[i] * (imp * heat) / total_weight;
                            }
                            weighted_val
                        })
                        .collect(),
                )
            } else {
                let embeddings: Vec<Vec<f64>> = with_embeddings.into_iter().map(|(e, _, _)| e).collect();
                Some(centroid(&embeddings))
            }
        };

        let all_amps: Vec<IndexMap<String, f64>> =
            messages.iter().map(|m| m.borrow().wave_amplitudes.clone()).filter(|a| !a.is_empty()).collect();
        let mut agg_waves = IndexMap::new();
        for key in wave_keys(&all_amps) {
            let best = py_max_iter(all_amps.iter().map(|a| a.get(&key).copied().unwrap_or(0.0)));
            agg_waves.insert(key, best.expect("non-empty"));
        }

        let entity_lists: Vec<Vec<String>> = messages.iter().map(|m| m.borrow().entity_ids.clone()).collect();
        let all_entity_ids = entity_union(entity_lists.iter());
        let summary = summary.map(str::to_string).unwrap_or_else(|| format!("Episode: {} messages", messages.len()));
        let avg_importance = mean(messages.iter().map(|m| m.borrow().importance_score));
        let max_heat = py_max_iter(messages.iter().map(|m| m.borrow().heat_score));
        let child_ids: Vec<String> = messages.iter().map(|m| m.borrow().node_id.clone()).collect();

        let mut episode = NeuralNode::new(generate_node_id(), NodeLayer::Episode, summary);
        episode.embedding = agg_embedding;
        episode.wave_amplitudes = agg_waves;
        episode.entity_ids = all_entity_ids;
        episode.session_key = session_key.to_string();
        episode.importance_score = avg_importance;
        episode.heat_score = max_heat.expect("non-empty");
        episode.child_ids = child_ids.clone();
        episode.source_memory_ids = child_ids;
        episode.metadata = metadata;
        let episode_id = episode.node_id.clone();
        let episode = node_ref(episode);
        self.storage.save_node(&episode);

        for msg in &messages {
            let msg_id = {
                let mut m = msg.borrow_mut();
                m.parent_id = Some(episode_id.clone());
                m.consolidation_state = ConsolidationState::Archived;
                m.node_id.clone()
            };
            self.storage.save_node(msg);
            self.save_hierarchy_edge(&episode_id, &msg_id);
        }
        tracing::info!(
            "Consolidated {} messages into episode {}",
            messages.len(),
            &episode_id[..8.min(episode_id.len())]
        );
        Ok(episode)
    }

    /// Segment unassigned ACTIVE messages into episodes at time gaps above `episode_time_gap_hours` or when a
    /// segment reaches `max_messages_per_episode`; segments shorter than the minimum are dropped.
    pub fn auto_segment_to_episodes(&self, session_key: &str) -> Result<Vec<NodeRef>, EngineError> {
        let messages = self.storage.get_nodes_by_layer(session_key, NodeLayer::Message);
        let mut unassigned: Vec<NodeRef> = messages
            .into_iter()
            .filter(|m| {
                let m = m.borrow();
                m.parent_id.is_none() && m.consolidation_state == ConsolidationState::Active
            })
            .collect();
        if unassigned.len() < self.config.min_messages_per_episode {
            return Ok(Vec::new());
        }
        sort_by_created_at(&mut unassigned)?;

        let mut episodes = Vec::new();
        let mut current_segment: Vec<NodeRef> = Vec::new();
        for msg in unassigned {
            if let Some(last_msg) = current_segment.last() {
                let gap = msg.borrow().created_at.sub(&last_msg.borrow().created_at)?;
                let time_gap = total_seconds(gap) / 3600.0;
                if time_gap > self.config.episode_time_gap_hours
                    || current_segment.len() >= self.config.max_messages_per_episode
                {
                    if current_segment.len() >= self.config.min_messages_per_episode {
                        let ids: Vec<String> = current_segment.iter().map(|m| m.borrow().node_id.clone()).collect();
                        episodes.push(self.consolidate_to_episode(&ids, session_key, None, Map::new())?);
                    }
                    current_segment.clear();
                }
            }
            current_segment.push(msg);
        }
        if current_segment.len() >= self.config.min_messages_per_episode {
            let ids: Vec<String> = current_segment.iter().map(|m| m.borrow().node_id.clone()).collect();
            episodes.push(self.consolidate_to_episode(&ids, session_key, None, Map::new())?);
        }
        Ok(episodes)
    }

    // ---------------------------------------------------------------------------------------------
    // Layer 2: topics
    // ---------------------------------------------------------------------------------------------

    /// Cluster episodes into a topic: centroid embedding, mean waves, entity union, mean importance and heat.
    /// Metadata is `{"label": topic_label, **metadata}`.
    pub fn cluster_to_topic(
        &self,
        episode_ids: &[String],
        topic_label: &str,
        session_key: &str,
        description: Option<&str>,
        metadata: Map<String, Value>,
    ) -> Result<NodeRef, EngineError> {
        let episodes: Vec<NodeRef> = episode_ids
            .iter()
            .filter_map(|eid| self.storage.get_node(eid))
            .filter(|e| e.borrow().layer == NodeLayer::Episode)
            .collect();
        if episodes.is_empty() {
            return Err(EngineError::ValueError("No valid episode nodes found to cluster".into()));
        }
        let embeddings: Vec<Vec<f64>> =
            episodes.iter().filter_map(|e| e.borrow().embedding.clone().filter(|v| !v.is_empty())).collect();
        let centroid_embedding = if embeddings.is_empty() { None } else { Some(centroid(&embeddings)) };
        let all_amps: Vec<IndexMap<String, f64>> =
            episodes.iter().map(|e| e.borrow().wave_amplitudes.clone()).filter(|a| !a.is_empty()).collect();
        let agg_waves = mean_waves(&all_amps);
        let entity_lists: Vec<Vec<String>> = episodes.iter().map(|e| e.borrow().entity_ids.clone()).collect();
        let all_entity_ids = entity_union(entity_lists.iter());
        let avg_importance = mean(episodes.iter().map(|e| e.borrow().importance_score));
        let avg_heat = mean(episodes.iter().map(|e| e.borrow().heat_score));
        let content = match description {
            Some(d) if !d.is_empty() => d.to_string(),
            _ => format!("Topic: {topic_label}"),
        };
        let mut meta = Map::new();
        meta.insert("label".into(), json!(topic_label));
        for (k, v) in metadata {
            meta.insert(k, v);
        }

        let mut topic = NeuralNode::new(generate_node_id(), NodeLayer::Topic, content);
        topic.embedding = centroid_embedding;
        topic.wave_amplitudes = agg_waves;
        topic.entity_ids = all_entity_ids;
        topic.session_key = session_key.to_string();
        topic.importance_score = avg_importance;
        topic.heat_score = avg_heat;
        topic.child_ids = episodes.iter().map(|e| e.borrow().node_id.clone()).collect();
        topic.metadata = meta;
        let topic_id = topic.node_id.clone();
        let topic = node_ref(topic);
        self.storage.save_node(&topic);

        for episode in &episodes {
            let episode_id = {
                let mut e = episode.borrow_mut();
                e.parent_id = Some(topic_id.clone());
                e.node_id.clone()
            };
            self.storage.save_node(episode);
            self.save_hierarchy_edge(&topic_id, &episode_id);
        }
        Ok(topic)
    }

    /// Greedy clustering of unassigned episodes with embeddings: each unused episode collects every later
    /// unused one with cosine similarity at or above `topic_similarity_threshold`; clusters of at least
    /// `min_episodes_per_topic` become topics labelled by up to three entities (`Topic_<n>` without any).
    pub fn auto_cluster_to_topics(&self, session_key: &str) -> Result<Vec<NodeRef>, EngineError> {
        let episodes = self.storage.get_nodes_by_layer(session_key, NodeLayer::Episode);
        let unassigned: Vec<NodeRef> = episodes
            .into_iter()
            .filter(|e| {
                let e = e.borrow();
                e.parent_id.is_none() && e.embedding.is_some()
            })
            .collect();
        if unassigned.len() < self.config.min_episodes_per_topic {
            return Ok(Vec::new());
        }
        let mut clusters: Vec<Vec<NodeRef>> = Vec::new();
        let mut used: IndexSet<String> = IndexSet::new();
        for episode in &unassigned {
            let eid = episode.borrow().node_id.clone();
            if used.contains(&eid) {
                continue;
            }
            let mut cluster = vec![episode.clone()];
            used.insert(eid);
            for other in &unassigned {
                let oid = other.borrow().node_id.clone();
                if used.contains(&oid) {
                    continue;
                }
                let sim = {
                    let (e, o) = (episode.borrow(), other.borrow());
                    match (
                        e.embedding.as_ref().filter(|v| !v.is_empty()),
                        o.embedding.as_ref().filter(|v| !v.is_empty()),
                    ) {
                        (Some(a), Some(b)) => Some(cosine_similarity(a, b)),
                        _ => None,
                    }
                };
                if let Some(sim) = sim {
                    if sim >= self.config.topic_similarity_threshold {
                        cluster.push(other.clone());
                        used.insert(oid);
                    }
                }
            }
            if cluster.len() >= self.config.min_episodes_per_topic {
                clusters.push(cluster);
            }
        }

        let mut topics = Vec::new();
        for (i, cluster) in clusters.iter().enumerate() {
            let entity_lists: Vec<Vec<String>> = cluster.iter().map(|e| e.borrow().entity_ids.clone()).collect();
            let all_entities = entity_union(entity_lists.iter());
            let label = if all_entities.is_empty() {
                format!("Topic_{}", i + 1)
            } else {
                let first: Vec<&str> = all_entities.iter().take(3).map(String::as_str).collect();
                format!("Topic: {}", first.join(", "))
            };
            let ids: Vec<String> = cluster.iter().map(|e| e.borrow().node_id.clone()).collect();
            topics.push(self.cluster_to_topic(&ids, &label, session_key, None, Map::new())?);
        }
        Ok(topics)
    }

    // ---------------------------------------------------------------------------------------------
    // Layer 3: persona
    // ---------------------------------------------------------------------------------------------

    /// Synthesise a persona facet from topics: heat x importance weighted embedding, mean waves, entities
    /// present in at least half the topics, importance `min(1, mean * 1.2)`, mean heat. Metadata is
    /// `{"aspect": persona_aspect, **metadata}`.
    pub fn synthesize_persona(
        &self,
        topic_ids: &[String],
        persona_aspect: &str,
        session_key: &str,
        description: Option<&str>,
        metadata: Map<String, Value>,
    ) -> Result<NodeRef, EngineError> {
        let topics: Vec<NodeRef> = topic_ids
            .iter()
            .filter_map(|tid| self.storage.get_node(tid))
            .filter(|t| t.borrow().layer == NodeLayer::Topic)
            .collect();
        if topics.is_empty() {
            return Err(EngineError::ValueError("No valid topic nodes found to synthesize".into()));
        }
        let embeddings: Vec<(Vec<f64>, f64)> = topics
            .iter()
            .filter_map(|t| {
                let t = t.borrow();
                t.embedding.clone().filter(|e| !e.is_empty()).map(|e| (e, t.heat_score * t.importance_score))
            })
            .collect();
        let weighted_embedding = if embeddings.is_empty() {
            None
        } else {
            let mut total_weight = 0.0;
            for (_, w) in &embeddings {
                total_weight += w;
            }
            let embedding_dim = embeddings[0].0.len();
            let mut weighted = vec![0.0; embedding_dim];
            for (emb, weight) in &embeddings {
                let norm_weight =
                    if total_weight > 0.0 { weight / total_weight } else { 1.0 / embeddings.len() as f64 };
                for (i, w) in weighted.iter_mut().enumerate() {
                    *w += emb[i] * norm_weight;
                }
            }
            Some(weighted)
        };
        let all_amps: Vec<IndexMap<String, f64>> =
            topics.iter().map(|t| t.borrow().wave_amplitudes.clone()).filter(|a| !a.is_empty()).collect();
        let agg_waves = mean_waves(&all_amps);

        let mut entity_counts: IndexMap<String, usize> = IndexMap::new();
        for t in &topics {
            for eid in &t.borrow().entity_ids {
                *entity_counts.entry(eid.clone()).or_insert(0) += 1;
            }
        }
        let stable_entities: Vec<String> = entity_counts
            .into_iter()
            .filter(|(_, count)| *count as f64 >= topics.len() as f64 * 0.5)
            .map(|(eid, _)| eid)
            .collect();
        let content = match description {
            Some(d) if !d.is_empty() => d.to_string(),
            _ => format!("Persona aspect: {persona_aspect}"),
        };
        let mean_importance = {
            let mut s = 0.0;
            for t in &topics {
                s += t.borrow().importance_score;
            }
            s / topics.len() as f64 * 1.2
        };
        let importance = if mean_importance < 1.0 { mean_importance } else { 1.0 };
        let heat = mean(topics.iter().map(|t| t.borrow().heat_score));
        let mut meta = Map::new();
        meta.insert("aspect".into(), json!(persona_aspect));
        for (k, v) in metadata {
            meta.insert(k, v);
        }

        let mut persona = NeuralNode::new(generate_node_id(), NodeLayer::Persona, content);
        persona.embedding = weighted_embedding;
        persona.wave_amplitudes = agg_waves;
        persona.entity_ids = stable_entities;
        persona.session_key = session_key.to_string();
        persona.importance_score = importance;
        persona.heat_score = heat;
        persona.child_ids = topics.iter().map(|t| t.borrow().node_id.clone()).collect();
        persona.metadata = meta;
        let persona_id = persona.node_id.clone();
        let persona = node_ref(persona);
        self.storage.save_node(&persona);

        for topic in &topics {
            let topic_id = {
                let mut t = topic.borrow_mut();
                t.parent_id = Some(persona_id.clone());
                t.node_id.clone()
            };
            self.storage.save_node(topic);
            self.save_hierarchy_edge(&persona_id, &topic_id);
        }
        Ok(persona)
    }

    // ---------------------------------------------------------------------------------------------
    // Traversal
    // ---------------------------------------------------------------------------------------------

    /// Parent, grandparent, ... up to the root.
    pub fn get_ancestors(&self, node_id: &str) -> Vec<NodeRef> {
        let mut ancestors = Vec::new();
        let mut current = self.storage.get_node(node_id);
        while let Some(cur) = current {
            let Some(parent_id) = cur.borrow().parent_id.clone().filter(|p| !p.is_empty()) else { break };
            match self.storage.get_node(&parent_id) {
                Some(parent) => {
                    ancestors.push(parent.clone());
                    current = Some(parent);
                }
                None => break,
            }
        }
        ancestors
    }

    /// Breadth-first descendants down to `max_depth` levels (Python default 3).
    pub fn get_descendants(&self, node_id: &str, max_depth: usize) -> Vec<NodeRef> {
        let mut descendants = Vec::new();
        let mut to_visit: std::collections::VecDeque<(String, usize)> = [(node_id.to_string(), 0)].into();
        let mut visited: IndexSet<String> = IndexSet::new();
        while let Some((current_id, depth)) = to_visit.pop_front() {
            if visited.contains(&current_id) || depth >= max_depth {
                continue;
            }
            visited.insert(current_id.clone());
            let Some(node) = self.storage.get_node(&current_id) else { continue };
            let child_ids = node.borrow().child_ids.clone();
            for child_id in child_ids {
                if !visited.contains(&child_id) {
                    if let Some(child) = self.storage.get_node(&child_id) {
                        descendants.push(child);
                        to_visit.push_back((child_id, depth + 1));
                    }
                }
            }
        }
        descendants
    }

    /// The other children of the node's parent.
    pub fn get_siblings(&self, node_id: &str) -> Vec<NodeRef> {
        let Some(node) = self.storage.get_node(node_id) else { return Vec::new() };
        let Some(parent_id) = node.borrow().parent_id.clone() else { return Vec::new() };
        let Some(parent) = self.storage.get_node(&parent_id) else { return Vec::new() };
        let child_ids = parent.borrow().child_ids.clone();
        child_ids.iter().filter(|c| c.as_str() != node_id).filter_map(|c| self.storage.get_node(c)).collect()
    }

    /// Per layer (all five): count, mean heat and importance, how many have a parent / children.
    pub fn get_layer_summary(&self, session_key: &str) -> Value {
        let mut summary = Map::new();
        for layer in NodeLayer::ALL {
            let nodes = self.storage.get_nodes_by_layer(session_key, layer);
            let entry = if nodes.is_empty() {
                json!({"count": 0})
            } else {
                json!({
                    "count": nodes.len(),
                    "avg_heat": mean(nodes.iter().map(|n| n.borrow().heat_score)),
                    "avg_importance": mean(nodes.iter().map(|n| n.borrow().importance_score)),
                    "with_parent": nodes.iter().filter(|n| n.borrow().parent_id.as_ref().is_some_and(|p| !p.is_empty())).count(),
                    "with_children": nodes.iter().filter(|n| !n.borrow().child_ids.is_empty()).count(),
                })
            };
            summary.insert(layer.name().to_string(), entry);
        }
        Value::Object(summary)
    }
}
