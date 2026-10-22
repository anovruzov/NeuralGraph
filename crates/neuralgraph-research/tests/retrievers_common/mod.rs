//! The Rust side of `tests/golden/_retrievers.py`: the LoCoMo graph the retriever fixtures were recorded on,
//! rebuilt from the recorded message specs with the same calls, so ids, timestamps and edges line up.
#![allow(dead_code)]

use crate::engine_common::{add_semantic_and_entity_edges, dt, strings, waves, DIM};
use indexmap::IndexMap;
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::retrieval::hierarchy::{HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::retriever::RetrieverError;
use neuralgraph_research::retrieval::temporal::TemporalChainManager;
use neuralgraph_research::retrieval::{NeuralGraphStorage, NodeRef, RetrievalResult};
use serde_json::{json, Map, Value};
use std::rc::Rc;

/// `_retrievers.add_nodes`.
pub fn add_nodes(hierarchy: &HierarchyManager, specs: &Value, session_key: &str) -> Vec<NodeRef> {
    let mut nodes = Vec::new();
    for spec in specs.as_array().expect("specs") {
        let content = spec["content"].as_str().unwrap();
        let mut metadata = Map::new();
        metadata.insert("dia_index".into(), json!(nodes.len()));
        if let Some(s) = spec["speaker"].as_str() {
            metadata.insert("speaker".into(), json!(s));
        }
        for (k, v) in spec["extra"].as_object().unwrap() {
            metadata.insert(k.clone(), v.clone());
        }
        let args = MessageNodeArgs {
            embedding: Some(fake_embedding(content, DIM)),
            wave_amplitudes: Some(waves(&spec["wave_amplitudes"])),
            entity_ids: Some(strings(&spec["entity_ids"])),
            importance_score: spec["importance_score"].as_f64().unwrap(),
            resolved_date: spec["resolved_date"].clone(),
            temporal_tokens: spec["temporal_tokens"].clone(),
            created_at: Some(dt(&spec["created_at"])),
            metadata,
            ..Default::default()
        };
        nodes.push(hierarchy.create_message_node(content, session_key, args));
    }
    nodes
}

/// The graph of `_retrievers.build_graph`.
pub struct Graph {
    pub hierarchy: HierarchyManager,
    pub temporal: TemporalChainManager,
    pub msgs: Vec<NodeRef>,
    pub episodes: Vec<NodeRef>,
    pub topics: Vec<NodeRef>,
}

/// `_retrievers.build_graph`.
pub fn build_graph(storage: Rc<dyn NeuralGraphStorage>, specs: &Value, session_key: &str) -> Graph {
    let hierarchy = HierarchyManager::new(storage.clone(), None);
    let temporal = TemporalChainManager::new(storage.clone(), None);
    let msgs = add_nodes(&hierarchy, specs, session_key);
    temporal.build_temporal_chain(&msgs, session_key).unwrap();
    add_semantic_and_entity_edges(storage.as_ref(), &msgs, 6);
    let episodes = hierarchy.auto_segment_to_episodes(session_key).unwrap();
    let topics = hierarchy.auto_cluster_to_topics(session_key).unwrap();
    for _ in 0..2 {
        temporal.record_co_activation(&msgs[..4], "seed");
        temporal.record_co_activation(&[msgs[10].clone(), msgs[20].clone(), msgs[30].clone()], "seed");
    }
    Graph { hierarchy, temporal, msgs, episodes, topics }
}

/// Wave amplitudes recorded as `null` or an object.
pub fn opt_waves(v: &Value) -> Option<IndexMap<String, f64>> {
    if v.is_null() {
        None
    } else {
        Some(waves(v))
    }
}

/// `_retrievers.result` / `_retrievers.call`.
pub fn result_json(r: Result<RetrievalResult, RetrieverError>) -> Value {
    match r {
        Ok(r) => json!({
            "nodes": crate::engine_common::scored(&r.nodes),
            "stages": r.stages_executed,
            "seen": r.total_candidates_seen,
            "co": r.co_activations_recorded,
        }),
        Err(e) => json!({"error": e.to_string()}),
    }
}
