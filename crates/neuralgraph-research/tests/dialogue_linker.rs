//! Parity of the dialogue linker with Python (`tests/golden/dialogue_linker.json`, written by
//! `gen_dialogue_linker.py`): two full LoCoMo conversations ingested like `retrieval_eval.py::ingest`, every
//! aggregator, binding and linked-message list, the helpers one by one, and Python's `\w` class.

mod common;

use common::{assert_json_eq, load, pin_now, s};
use neuralgraph_memory::fake_embedding;
use neuralgraph_memory::pycompat::{dumps, JsonFormat};
use neuralgraph_research::pyre::{py_is_word_char, PY_WORD_CLASS};
use neuralgraph_research::retrieval::dialogue_linker::{
    set_deterministic_hex_ids, CrossMessageBinding, DialogueAggregator, DialogueLinker,
};
use neuralgraph_research::retrieval::{
    edge_ref, generate_edge_id, node_ref, set_deterministic_uuids, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge,
    NeuralGraphStorage, NeuralNode, NodeLayer, NodeRef,
};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn locomo() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("locomo10.json")).expect("json")
}

/// `hashlib.sha256(json.dumps(obj).encode()).hexdigest()`
fn sha(v: &Value) -> String {
    let text = dumps(v, &JsonFormat::python_default());
    Sha256::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// `flatten_messages(conv)`: (speaker, text, datetime) in order.
fn flatten_messages(conv: &Value) -> Vec<(String, String, String)> {
    let c = conv.get("conversation").unwrap_or(conv);
    let mut out = Vec::new();
    let mut i = 1;
    while let Some(session) = c.get(format!("session_{i}")) {
        let dt = c.get(format!("session_{i}_date_time")).and_then(Value::as_str).unwrap_or("").to_string();
        for m in session.as_array().unwrap() {
            out.push((
                m.get("speaker").and_then(Value::as_str).unwrap_or("Unknown").to_string(),
                m.get("text").and_then(Value::as_str).unwrap_or("").to_string(),
                dt.clone(),
            ));
        }
        i += 1;
    }
    out
}

fn ingest(conv_idx: usize, conv: &Value, dim: usize) -> (InMemoryNeuralGraphStorage, Vec<NodeRef>) {
    let storage = InMemoryNeuralGraphStorage::default();
    let mut speaker_nodes: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    let mut all_ids = Vec::new();
    for (idx, (speaker, text, dt)) in flatten_messages(conv).into_iter().enumerate() {
        let node_id = format!("msg_{conv_idx}_{idx}");
        let mut node = NeuralNode::new(node_id.clone(), NodeLayer::Message, text.clone());
        node.session_key = format!("conv_{conv_idx}");
        node.embedding = Some(fake_embedding(&text, dim));
        node.metadata.insert("speaker".into(), json!(speaker));
        node.metadata.insert("datetime".into(), json!(dt));
        storage.save_node(&node_ref(node));
        all_ids.push(node_id.clone());
        speaker_nodes.entry(speaker.to_lowercase()).or_default().push(node_id);
    }
    for ids in speaker_nodes.values() {
        for (i, src) in ids.iter().enumerate() {
            for tgt in ids.iter().take((i + 4).min(ids.len())).skip(i + 1) {
                let e =
                    NeuralEdge::new(generate_edge_id(), src.clone(), tgt.clone(), EdgeType::Entity).with_weight(0.8);
                storage.save_edge(&edge_ref(e));
            }
        }
    }
    for w in all_ids.windows(2) {
        let mut meta = Map::new();
        meta.insert("direction".into(), json!("before"));
        let e = NeuralEdge::new(generate_edge_id(), w[0].clone(), w[1].clone(), EdgeType::Temporal)
            .with_weight(0.85)
            .with_metadata(meta);
        storage.save_edge(&edge_ref(e));
    }
    let nodes = all_ids.iter().map(|id| storage.get_node(id).unwrap()).collect();
    (storage, nodes)
}

fn agg_json(a: &DialogueAggregator) -> Value {
    json!({
        "aggregator_id": a.aggregator_id,
        "aggregator_type": a.aggregator_type.value(),
        "session_key": a.session_key,
        "message_ids": a.message_ids,
        "topic": a.topic,
        "referent": a.referent,
        "mentions": a.mentions,
        "temporal_expression": a.temporal_expression,
        "resolved_date": a.resolved_date,
        "embedding_sha256": a.embedding.as_ref().map(|e| sha(&json!(e))),
        "binding_weights": a.binding_weights,
        "cohesion_score": a.cohesion_score,
    })
}

fn binding_json(b: &CrossMessageBinding) -> Value {
    json!({
        "binding_id": b.binding_id,
        "binding_type": b.binding_type.value(),
        "source_id": b.source_id,
        "target_id": b.target_id,
        "source_span": b.source_span,
        "target_span": b.target_span,
        "confidence": b.confidence,
        "temporal_relation": b.temporal_relation,
    })
}

fn linked_json(linker: &DialogueLinker, id: &str) -> Value {
    json!(linker.get_linked_messages(id).into_iter().map(|(i, w, t)| json!([i, w, t])).collect::<Vec<_>>())
}

#[test]
fn locomo_conversations_match() {
    pin_now(common::FIXED_NOW);
    let f = load("dialogue_linker.json");
    let dim = f["dim"].as_u64().unwrap() as usize;
    let data = locomo();
    for want in f["conversations"].as_array().unwrap() {
        let conv_idx = want["conv_idx"].as_u64().unwrap() as usize;
        let what = format!("conv {conv_idx}");
        set_deterministic_uuids(Some(0));
        let (storage, nodes) = ingest(conv_idx, &data[conv_idx], dim);
        assert_eq!(nodes.len() as u64, want["n_messages"].as_u64().unwrap(), "{what}");

        let mut edges = storage.get_all_edges(&format!("conv_{conv_idx}"));
        edges.sort_by(|a, b| a.borrow().edge_id.cmp(&b.borrow().edge_id));
        let rows: Vec<Value> = edges
            .iter()
            .map(|e| {
                let e = e.borrow();
                json!([e.edge_id, e.source_id, e.target_id, e.edge_type.value(), e.base_weight, e.metadata])
            })
            .collect();
        assert_json_eq(&json!(rows[..10]), &want["edges_head"], &format!("{what} edges"));
        assert_eq!(rows.len() as u64, want["edge_count"].as_u64().unwrap());
        assert_eq!(sha(&json!(rows)), s(&want["edges_sha256"]), "{what} edges digest");
        let div: Vec<f64> = nodes.iter().map(|n| n.borrow().diversity_score).collect();
        assert_json_eq(&json!(div), &want["diversity_scores"], &format!("{what} diversity"));

        set_deterministic_hex_ids(Some(0));
        let mut linker = DialogueLinker::new(&storage);
        let (aggs, binds) = linker.process_dialogue_sequence(&nodes, &format!("conv_{conv_idx}")).unwrap();
        set_deterministic_hex_ids(None);
        assert_json_eq(&json!(aggs.iter().map(agg_json).collect::<Vec<_>>()), &want["aggregators"], &what);
        assert_json_eq(&json!(binds.iter().map(binding_json).collect::<Vec<_>>()), &want["bindings"], &what);
        assert_json_eq(&json!(linker.aggregators().keys().collect::<Vec<_>>()), &want["stored_aggregator_ids"], &what);
        assert_eq!(linker.bindings().len() as u64, want["stored_binding_count"].as_u64().unwrap());

        let ids: Vec<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
        for (k, id) in ids.iter().enumerate() {
            let linked = linked_json(&linker, id);
            if let Some(full) = want["linked_full"].get(id) {
                assert_json_eq(&linked, full, &format!("{what} linked {id}"));
            }
            assert_eq!(linked.as_array().unwrap().len() as u64, want["linked_lengths"][k].as_u64().unwrap(), "{id}");
            assert_eq!(sha(&linked), s(&want["linked_sha256"][k]), "{what} linked digest {id}");
        }
        let pairs: Vec<Option<String>> = ids.iter().map(|i| linker.get_exchange_pair(i)).collect();
        assert_json_eq(&json!(pairs), &want["exchange_pairs"], &format!("{what} exchange pairs"));
        let mut probe: Vec<String> = ids.iter().step_by(7).cloned().collect();
        probe.extend(ids[..3].iter().cloned());
        assert_json_eq(&json!(linker.get_atomic_retrieval_units(&probe)), &want["atomic_units"], &what);
        for (id, agg_ids) in want["aggregators_for_message"].as_object().unwrap() {
            let got: Vec<&str> =
                linker.get_aggregators_for_message(id).iter().map(|a| a.aggregator_id.as_str()).collect();
            assert_json_eq(&json!(got), agg_ids, &format!("{what} aggregators of {id}"));
        }
        for case in want["topic_context"].as_array().unwrap() {
            assert_json_eq(&json!(linker.get_topic_context(s(&case[0]))), &case[1], &format!("topic {}", case[0]));
        }
        for case in want["resolve_coreference"].as_array().unwrap() {
            assert_json_eq(&json!(linker.resolve_coreference(s(&case[0]), s(&case[1]))), &case[2], "coref");
        }
    }
}

#[test]
fn helpers_match() {
    let f = load("dialogue_linker.json");
    let h = &f["helpers"];
    let storage = InMemoryNeuralGraphStorage::default();
    let linker = DialogueLinker::new(&storage);
    for c in h["is_question"].as_array().unwrap() {
        assert_eq!(json!(linker.is_question(s(&c[0]))), c[1], "is_question {:?}", c[0]);
    }
    for c in h["is_reaction"].as_array().unwrap() {
        let got = match linker.is_reaction(s(&c[0])) {
            Ok(b) => json!(b),
            Err(e) => json!(e),
        };
        assert_eq!(got, c[1], "is_reaction {:?}", c[0]);
    }
    for c in h["extract_entities"].as_array().unwrap() {
        assert_json_eq(&json!(linker.extract_entities(s(&c[0]))), &c[1], &format!("entities {:?}", c[0]));
    }
    for c in h["find_pronouns"].as_array().unwrap() {
        let got: Vec<Value> = linker.find_pronouns(s(&c[0])).into_iter().map(|(p, k)| json!([p, k])).collect();
        assert_json_eq(&json!(got), &c[1], &format!("pronouns {:?}", c[0]));
    }
    for c in h["categorize"].as_array().unwrap() {
        assert_eq!(linker.categorize_entity(s(&c[0])), s(&c[1]));
    }
    for c in h["categories_match"].as_array().unwrap() {
        assert_eq!(json!(linker.categories_match(s(&c[0]), s(&c[1]))), c[2], "{c}");
    }
    for c in h["get_speaker"].as_array().unwrap() {
        let mut node = NeuralNode::new("n", NodeLayer::Message, s(&c[1]));
        node.metadata.insert("speaker".into(), c[0].clone());
        assert_eq!(DialogueLinker::speaker_of(&node.speaker_id(), &node.content), s(&c[2]), "{c}");
    }
    for c in h["extract_topic"].as_array().unwrap() {
        assert_eq!(linker.extract_topic(s(&c[0]), s(&c[1])), s(&c[2]), "{c}");
    }
}

#[test]
fn small_sequences_match() {
    pin_now(common::FIXED_NOW);
    let f = load("dialogue_linker.json");
    let storage = InMemoryNeuralGraphStorage::default();
    let node = |i: usize, content: &str, speaker: &str, emb: bool, entity_ids: &[&str]| {
        let mut n = NeuralNode::new(format!("s{i}"), NodeLayer::Message, content);
        n.embedding = emb.then(|| fake_embedding(content, 32));
        if !speaker.is_empty() {
            n.metadata.insert("speaker".into(), json!(speaker));
        }
        n.entity_ids = entity_ids.iter().map(|e| e.to_string()).collect();
        node_ref(n)
    };
    let seqs: Vec<(&str, Vec<NodeRef>)> = vec![
        (
            "prefix_speakers",
            vec![
                node(0, "Anna: Did you see Paris yesterday?", "", true, &[]),
                node(1, "Ben: Yes, Paris was lovely.", "", true, &[]),
                node(2, "Anna: And the Tower? It was tall", "", true, &[]),
                node(3, "no speaker here, it is fine", "", true, &[]),
                node(4, "nobody either but Paris again today", "", true, &[]),
            ],
        ),
        (
            "entity_ids_and_no_embeddings",
            vec![
                node(0, "We met Joanna at the park", "A", false, &["joanna", "Park"]),
                node(1, "Joanna loves the park too", "B", true, &["Joanna"]),
                node(2, "Then Joanna left. Joanna!", "A", false, &[]),
                node(3, "She said thanks. However it rained", "B", true, &[]),
            ],
        ),
        ("whitespace_reply", vec![node(0, "Hello there?", "A", true, &[]), node(1, "   ", "B", true, &[])]),
        ("single", vec![node(0, "Just one message about Rome", "A", true, &[])]),
    ];
    let cases = f["sequences"].as_array().unwrap();
    for ((name, msgs), want) in seqs.into_iter().zip(cases) {
        assert_eq!(name, s(&want["name"]));
        set_deterministic_hex_ids(Some(0));
        let mut linker = DialogueLinker::new(&storage);
        let first = linker.process_dialogue_sequence(&msgs, "s");
        let got = match first {
            Err(e) => json!({"name": name, "error": e}),
            Ok((mut aggs, mut binds)) => {
                let reversed: Vec<NodeRef> = msgs.iter().rev().cloned().collect();
                let (a2, b2) = linker.process_dialogue_sequence(&reversed, "s").unwrap();
                aggs.extend(a2);
                binds.extend(b2);
                let mut linked = Map::new();
                for m in &msgs {
                    let id = m.borrow().node_id.clone();
                    linked.insert(id.clone(), linked_json(&linker, &id));
                }
                json!({
                    "name": name,
                    "aggregators": aggs.iter().map(agg_json).collect::<Vec<_>>(),
                    "bindings": binds.iter().map(binding_json).collect::<Vec<_>>(),
                    "linked": linked,
                    "error": null,
                })
            }
        };
        assert_json_eq(&got, want, name);
    }
    set_deterministic_hex_ids(Some(0));
    let a =
        DialogueAggregator::new("", neuralgraph_research::retrieval::dialogue_linker::AggregatorType::Exchange, "s");
    let b = CrossMessageBinding::new(
        "",
        neuralgraph_research::retrieval::dialogue_linker::BindingType::RespondsTo,
        "a",
        "b",
    );
    set_deterministic_hex_ids(None);
    assert_json_eq(&json!([a.aggregator_id, b.binding_id]), &cases[4]["ids"], "post_init ids");
}

/// Python's `\w` against [`py_is_word_char`] and [`PY_WORD_CLASS`] for every code point Unicode 14 assigns.
#[test]
fn python_word_class_matches() {
    let f = load("dialogue_linker.json");
    let ranges = |v: &Value| -> Vec<(u32, u32)> {
        v.as_array().unwrap().iter().map(|r| (r[0].as_u64().unwrap() as u32, r[1].as_u64().unwrap() as u32)).collect()
    };
    let word = ranges(&f["py_word"]["word_ranges"]);
    let unassigned = ranges(&f["py_word"]["unassigned_ranges"]);
    let in_ranges = |rs: &[(u32, u32)], cp: u32| {
        let i = rs.partition_point(|r| r.1 < cp);
        i < rs.len() && rs[i].0 <= cp
    };
    let class = regex::Regex::new(&format!("^[{PY_WORD_CLASS}]$")).unwrap();
    let mut mismatches = Vec::new();
    for cp in 0..=0x10FFFFu32 {
        let Some(c) = char::from_u32(cp) else { continue };
        if in_ranges(&unassigned, cp) {
            continue;
        }
        let want = in_ranges(&word, cp);
        let mut buf = [0u8; 4];
        if py_is_word_char(c) != want || class.is_match(c.encode_utf8(&mut buf)) != want {
            mismatches.push(format!("U+{cp:04X}"));
        }
    }
    assert!(mismatches.is_empty(), "{} mismatches: {:?}", mismatches.len(), &mismatches[..mismatches.len().min(40)]);
}
