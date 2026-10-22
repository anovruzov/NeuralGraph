//! Parity of `retrieval::hierarchy` with `hierarchy.py` (`tests/golden/hierarchy.json`, written by
//! `gen_hierarchy.py`).

mod common;
mod engine_common;

use chrono::TimeDelta;
use common::{assert_json_eq, load, FIXED_NOW};
use engine_common::{add_messages, dt, ids, install, node_dicts, DIM};
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::hierarchy::{EngineError, HierarchyConfig, HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::{InMemoryNeuralGraphStorage, NeuralGraphStorage, NodeRef};
use serde_json::{json, Map, Value};
use std::rc::Rc;

fn err<T>(r: Result<T, EngineError>) -> Value {
    match r {
        Ok(_) => Value::Null,
        Err(e) => json!(e.to_string()),
    }
}

fn meta(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
}

fn id(n: &NodeRef) -> String {
    n.borrow().node_id.clone()
}

#[test]
fn hierarchy_build_and_traversal_match() {
    install(FIXED_NOW);
    let f = load("hierarchy.json");
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let h = HierarchyManager::new(
        storage.clone(),
        Some(HierarchyConfig { topic_similarity_threshold: 0.35, ..Default::default() }),
    );
    let msgs = add_messages(&h, &f["specs"], "h1");
    let text = "We met on 8 May 2023 and talked for two years.";
    let special = h.create_message_node(
        text,
        "h1",
        MessageNodeArgs {
            embedding: Some(fake_embedding(text, DIM)),
            resolved_date: json!("2023-05-08"),
            temporal_tokens: json!([]),
            temporal_metadata: json!({"year": 2023}),
            resolved_dates: json!({}),
            explicit_date: json!(""),
            duration_years: json!(0),
            duration_months: json!(3),
            since_year: json!(2021),
            since_date: Value::Null,
            metadata: meta(&[("speaker", json!("Caroline")), ("note", json!("special"))]),
            ..Default::default()
        },
    );
    assert_json_eq(&special.borrow().to_dict(), &f["special"], "special message node");
    let plain = h.create_message_node("no embedding here", "h1", MessageNodeArgs::default());
    assert_json_eq(&plain.borrow().to_dict(), &f["plain"], "plain message node");
    assert_json_eq(&ids(&h.auto_segment_to_episodes("empty-session").unwrap()), &f["too_few_segment"], "too few");
    let episodes = h.auto_segment_to_episodes("h1").unwrap();
    assert_json_eq(&ids(&episodes), &f["episodes"], "episodes");
    let topics = h.auto_cluster_to_topics("h1").unwrap();
    assert_json_eq(&ids(&topics), &f["topics"], "topics");
    let mut persona_ids: Vec<String> = topics.iter().map(id).collect();
    persona_ids.push(id(&episodes[0]));
    persona_ids.push("missing".into());
    let persona =
        h.synthesize_persona(&persona_ids, "interests", "h1", None, meta(&[("tone", json!("warm"))])).unwrap();
    assert_eq!(id(&persona), f["persona"].as_str().unwrap());
    let persona2 = h.synthesize_persona(&[id(&topics[0])], "solo", "h1", Some("Just one topic"), Map::new()).unwrap();
    assert_eq!(id(&persona2), f["persona2"].as_str().unwrap());

    let ancestors: Map<String, Value> =
        msgs.iter().step_by(5).map(|m| (id(m), ids(&h.get_ancestors(&id(m))))).collect();
    assert_json_eq(&Value::Object(ancestors), &f["ancestors"], "ancestors");
    assert_json_eq(&ids(&h.get_ancestors("missing")), &f["ancestors_missing"], "ancestors of missing");
    let descendants: Map<String, Value> =
        (0..5).map(|d| (d.to_string(), ids(&h.get_descendants(&id(&persona), d)))).collect();
    assert_json_eq(&Value::Object(descendants), &f["descendants"], "descendants");
    let mut sib_nodes: Vec<NodeRef> = msgs.iter().step_by(7).cloned().collect();
    sib_nodes.push(persona.clone());
    sib_nodes.push(plain.clone());
    let siblings: Map<String, Value> = sib_nodes.iter().map(|m| (id(m), ids(&h.get_siblings(&id(m))))).collect();
    assert_json_eq(&Value::Object(siblings), &f["siblings"], "siblings");
    assert_json_eq(&h.get_layer_summary("h1"), &f["summary"], "layer summary");
    assert_json_eq(&engine_common::dump_graph(storage.as_ref(), "h1"), &f["graph"], "graph");

    let errors = json!({
        "episode": err(h.consolidate_to_episode(&[id(&persona), "missing".into()], "h1", None, Map::new())),
        "topic": err(h.cluster_to_topic(&[id(&msgs[0])], "x", "h1", None, Map::new())),
        "persona": err(h.synthesize_persona(&[], "x", "h1", None, Map::new())),
    });
    assert_json_eq(&errors, &f["errors"], "errors");
}

#[test]
fn explicit_consolidation_paths_match() {
    install(FIXED_NOW);
    let f = load("hierarchy.json");
    // the generator's first scenario minted one uuid per node and edge of its graph before this one
    let skipped = f["graph"]["nodes"].as_array().unwrap().len() + f["graph"]["edges"].as_array().unwrap().len();
    neuralgraph_research::retrieval::set_deterministic_uuids(Some(skipped as u64));

    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let h2 = HierarchyManager::new(
        storage.clone(),
        Some(HierarchyConfig {
            min_messages_per_episode: 2,
            max_messages_per_episode: 4,
            min_episodes_per_topic: 1,
            topic_similarity_threshold: 0.9,
            ..Default::default()
        }),
    );
    let batch_specs: Vec<Map<String, Value>> = serde_json::from_value(json!([
        {"content": "zero weight one", "embedding": [0.0, 1.0, 2.0], "importance_score": 0.0,
         "wave_amplitudes": {"entity": 0.4, "action": 0.2}, "entity_ids": ["e1"],
         "metadata": {"resolved_date": "2023-01-02", "speaker": "A"}},
        {"content": "zero weight two", "embedding": [1.0, 3.0, -2.0], "importance_score": 0.0,
         "wave_amplitudes": {"action": 0.7, "temporal": 0.1}, "entity_ids": ["e2", "e1"]},
        {"content": "no embedding", "importance_score": 0.9},
        {},
    ]))
    .unwrap();
    let batch = h2.create_message_nodes_batch(&batch_specs, "h2");
    assert_json_eq(&node_dicts(&batch), &f["batch"], "batch");
    let batch_ids: Vec<String> = batch.iter().map(id).collect();
    let ep_mean = h2.consolidate_to_episode(&batch_ids, "h2", Some(""), meta(&[("origin", json!("manual"))])).unwrap();
    assert_json_eq(&ep_mean.borrow().to_dict(), &f["episode_mean"], "mean-pooled episode");
    let ep_none = h2.consolidate_to_episode(&[id(&batch[2])], "h2", None, Map::new()).unwrap();
    assert_json_eq(&ep_none.borrow().to_dict(), &f["episode_none"], "episode without embeddings");
    let topic = h2
        .cluster_to_topic(
            &[id(&ep_mean), id(&ep_none), id(&batch[0])],
            "label",
            "h2",
            Some(""),
            meta(&[("extra", json!(1))]),
        )
        .unwrap();
    assert_json_eq(&topic.borrow().to_dict(), &f["topic_explicit"], "explicit topic");
    let topic_noemb = h2.cluster_to_topic(&[id(&ep_none)], "solo", "h2", None, Map::new()).unwrap();
    assert_json_eq(&topic_noemb.borrow().to_dict(), &f["topic_noemb"], "topic without embedding");
    let persona_noemb = h2.synthesize_persona(&[id(&topic_noemb)], "p", "h2", None, Map::new()).unwrap();
    assert_json_eq(&persona_noemb.borrow().to_dict(), &f["persona_noemb"], "persona without embedding");

    let t0 = dt(&f["specs"][0]["created_at"]);
    for i in 0..11i64 {
        let text = format!("segment message {i} about pottery");
        let minutes = if i < 7 { i } else { 600 + i };
        h2.create_message_node(
            &text,
            "h3",
            MessageNodeArgs {
                embedding: Some(fake_embedding(&text, DIM)),
                created_at: Some(t0.add(TimeDelta::minutes(minutes))),
                ..Default::default()
            },
        );
    }
    let seg = h2.auto_segment_to_episodes("h3").unwrap();
    assert_json_eq(&node_dicts(&seg), &f["segments"], "size-limited segments");
    assert_json_eq(&node_dicts(&h2.auto_cluster_to_topics("h3").unwrap()), &f["segment_topics"], "segment topics");
    assert_json_eq(&h2.get_layer_summary("h3"), &f["summary_h3"], "summary h3");

    let st3 = Rc::new(InMemoryNeuralGraphStorage::default());
    let h3 =
        HierarchyManager::new(st3.clone(), Some(HierarchyConfig { min_messages_per_episode: 2, ..Default::default() }));
    for i in 0..3 {
        h3.create_message_node(&format!("aware {i}"), "mixed", MessageNodeArgs::default());
    }
    let naive = PyDateTime::fromisoformat("2023-05-08T13:00:00").unwrap();
    h3.create_message_node("naive", "mixed", MessageNodeArgs { created_at: Some(naive), ..Default::default() });
    assert_json_eq(&err(h3.auto_segment_to_episodes("mixed")), &f["mixed_error"], "mixed timestamps");
    assert_eq!(st3.get_nodes_by_session("mixed", None).len(), 4);
}
