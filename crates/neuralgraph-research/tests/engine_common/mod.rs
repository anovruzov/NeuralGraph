//! Shared helpers for the engine parity tests (gating ... consolidation): the Rust side of
//! `tests/golden/_engine.py`. Graphs are rebuilt from the message specs recorded in the fixtures with the same
//! calls the generators made, so ids, timestamps and every derived value line up.
#![allow(dead_code)]

use indexmap::IndexMap;
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::hierarchy::{HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::{
    cosine_similarity, edge_ref, generate_edge_id, set_deterministic_uuids, EdgeRef, EdgeType, NeuralEdge,
    NeuralGraphStorage, NodeRef,
};
use serde_json::{json, Map, Value};

/// Embedding size the generators use with `fake_embedding`.
pub const DIM: usize = 32;

/// Pin "now" and restart the uuid counter (`_engine.install()`).
pub fn install(now: &str) {
    crate::common::pin_now(now);
    set_deterministic_uuids(Some(0));
}

/// `_engine.set_now(now)`.
pub fn set_now(now: &str) {
    crate::common::pin_now(now);
}

pub fn dt(v: &Value) -> PyDateTime {
    PyDateTime::fromisoformat(v.as_str().expect("iso string")).expect("isoformat")
}

pub fn waves(v: &Value) -> IndexMap<String, f64> {
    v.as_object().expect("object").iter().map(|(k, x)| (k.clone(), x.as_f64().expect("number"))).collect()
}

pub fn strings(v: &Value) -> Vec<String> {
    v.as_array().expect("array").iter().map(|x| x.as_str().expect("string").to_string()).collect()
}

pub fn obj(v: &Value) -> Map<String, Value> {
    v.as_object().expect("object").clone()
}

/// `_engine.add_messages`.
pub fn add_messages(hierarchy: &HierarchyManager, specs: &Value, session_key: &str) -> Vec<NodeRef> {
    let mut nodes = Vec::new();
    for spec in specs.as_array().expect("specs") {
        let content = spec["content"].as_str().unwrap();
        let mut metadata = Map::new();
        metadata.insert("dia_index".into(), json!(nodes.len()));
        if let Some(s) = spec["speaker"].as_str() {
            metadata.insert("speaker".into(), json!(s));
        }
        let args = MessageNodeArgs {
            embedding: Some(fake_embedding(content, DIM)),
            wave_amplitudes: Some(waves(&spec["wave_amplitudes"])),
            entity_ids: Some(strings(&spec["entity_ids"])),
            importance_score: spec["importance_score"].as_f64().unwrap(),
            created_at: Some(dt(&spec["created_at"])),
            metadata,
            ..Default::default()
        };
        nodes.push(hierarchy.create_message_node(content, session_key, args));
    }
    nodes
}

/// `_engine.add_semantic_and_entity_edges`.
pub fn add_semantic_and_entity_edges(storage: &dyn NeuralGraphStorage, nodes: &[NodeRef], window: usize) {
    for (i, a) in nodes.iter().enumerate() {
        for (j, b) in nodes.iter().enumerate().take((i + 1 + window).min(nodes.len())).skip(i + 1) {
            let (sim, shared, aid, bid) = {
                let (x, y) = (a.borrow(), b.borrow());
                let sim = cosine_similarity(x.embedding.as_ref().unwrap(), y.embedding.as_ref().unwrap());
                let shared: Vec<String> = x.entity_ids.iter().filter(|e| y.entity_ids.contains(e)).cloned().collect();
                (sim, shared, x.node_id.clone(), y.node_id.clone())
            };
            if sim >= 0.3 {
                let e =
                    NeuralEdge::new(generate_edge_id(), aid.clone(), bid.clone(), EdgeType::Semantic).with_weight(sim);
                storage.save_edge(&edge_ref(e));
            }
            if !shared.is_empty() {
                let mut e = NeuralEdge::new(generate_edge_id(), aid.clone(), bid.clone(), EdgeType::Entity)
                    .with_weight(py_round6(0.9 / (j - i) as f64));
                e.confidence = 0.9;
                let mut m = Map::new();
                m.insert("message_distance".into(), json!(j - i));
                m.insert("entity".into(), json!(shared[0]));
                e.metadata = m;
                storage.save_edge(&edge_ref(e));
            }
        }
    }
}

/// `round(x, 6)`.
pub fn py_round6(x: f64) -> f64 {
    neuralgraph_memory::pycompat::py_round(x, 6)
}

/// `_engine.dump_graph`.
pub fn dump_graph(storage: &dyn NeuralGraphStorage, session_key: &str) -> Value {
    json!({
        "nodes": storage.get_nodes_by_session(session_key, None).iter().map(|n| n.borrow().to_dict()).collect::<Vec<_>>(),
        "edges": storage.get_all_edges(session_key).iter().map(|e| e.borrow().to_dict()).collect::<Vec<_>>(),
    })
}

pub fn ids(nodes: &[NodeRef]) -> Value {
    json!(nodes.iter().map(|n| n.borrow().node_id.clone()).collect::<Vec<_>>())
}

pub fn edge_ids(edges: &[EdgeRef]) -> Value {
    json!(edges.iter().map(|e| e.borrow().edge_id.clone()).collect::<Vec<_>>())
}

pub fn scored(pairs: &[(NodeRef, f64)]) -> Value {
    json!(pairs.iter().map(|(n, s)| json!([n.borrow().node_id.clone(), s])).collect::<Vec<_>>())
}

pub fn node_dicts(nodes: &[NodeRef]) -> Value {
    json!(nodes.iter().map(|n| n.borrow().to_dict()).collect::<Vec<_>>())
}

pub fn edge_dicts(edges: &[EdgeRef]) -> Value {
    json!(edges.iter().map(|e| e.borrow().to_dict()).collect::<Vec<_>>())
}
