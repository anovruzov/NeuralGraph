//! Parity of `retrieval::pattern_completion` with `pattern_completion.py` (`tests/golden/pattern_completion.json`,
//! written by `gen_pattern_completion.py`). Connection weights are numpy float64 BLAS results: bit-exact on
//! macOS (`numpy::BITWISE_NUMPY`); elsewhere they and everything downstream are compared with a tolerance.

mod common;
mod engine_common;

use common::{assert_json_close, assert_json_eq, load, FIXED_NOW};
use engine_common::{add_messages, add_semantic_and_entity_edges, install, scored, DIM};
use indexmap::IndexMap;
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::numpy::BITWISE_NUMPY;
use neuralgraph_research::retrieval::hierarchy::{HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::pattern_completion::{
    ActivationState, CA3PatternCompleter, PatternCompletionConfig, ThetaGammaOrganizer,
};
use neuralgraph_research::retrieval::{
    edge_ref, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge, NeuralGraphStorage, NodeRef,
};
use serde_json::{json, Value};
use std::rc::Rc;

/// Exact on macOS, 1e-12 relative elsewhere (BLAS summation order differs).
#[track_caller]
fn check(actual: &Value, expected: &Value, what: &str) {
    if BITWISE_NUMPY {
        assert_json_eq(actual, expected, what);
    } else {
        assert_json_close(actual, expected, 1e-12, what);
    }
}

fn state_of(pc: &CA3PatternCompleter, seeds: usize, nodes: usize) -> Value {
    let st = pc.last_state();
    json!({"seeds": seeds, "nodes": nodes, "iterations": st.iteration + 1, "converged": st.converged})
}

#[test]
fn connection_weights_match_numpy() {
    let f = load("pattern_completion.json");
    let storage: Rc<dyn NeuralGraphStorage> = Rc::new(InMemoryNeuralGraphStorage::default());
    let pc = CA3PatternCompleter::new(storage, None);
    let weights: Vec<f64> = f["vectors"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| pc.compute_connection_weight(&common::f64s(&p[0]), &common::f64s(&p[1])))
        .collect();
    check(&json!(weights), &f["weights"], "connection weights");
    let embs: Vec<Vec<f64>> =
        f["specs"].as_array().unwrap().iter().map(|s| fake_embedding(s["content"].as_str().unwrap(), DIM)).collect();
    let fake: Vec<Vec<f64>> = (0..embs.len())
        .step_by(5)
        .map(|i| (0..embs.len()).step_by(3).map(|j| pc.compute_connection_weight(&embs[i], &embs[j])).collect())
        .collect();
    check(&json!(fake), &f["weights_fake"], "fake embedding weights");
}

#[test]
fn completion_and_internals_match() {
    install(FIXED_NOW);
    let f = load("pattern_completion.json");
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let h = HierarchyManager::new(storage.clone(), None);
    let msgs = add_messages(&h, &f["specs"], "p1");
    add_semantic_and_entity_edges(storage.as_ref(), &msgs, 8);
    for (a, b) in [(30usize, 2usize), (25, 4), (12, 3)] {
        let t = if a % 2 == 1 { EdgeType::Entity } else { EdgeType::Semantic };
        let (sid, tid) = (msgs[a].borrow().node_id.clone(), msgs[b].borrow().node_id.clone());
        storage.save_edge(&edge_ref(NeuralEdge::new(format!("back-{a}-{b}"), sid, tid, t).with_weight(0.45)));
    }
    let dyn_storage: Rc<dyn NeuralGraphStorage> = storage.clone();

    let configs: Vec<(&str, Option<PatternCompletionConfig>)> = vec![
        ("default", None),
        (
            "long",
            Some(PatternCompletionConfig {
                max_iterations: 6,
                convergence_threshold: 0.0,
                sparsity_k: 5,
                ..Default::default()
            }),
        ),
        (
            "wide",
            Some(PatternCompletionConfig {
                connectivity_radius: 0.1,
                spreading_factor: 0.9,
                activation_decay: 0.5,
                use_energy_function: false,
                ..Default::default()
            }),
        ),
        (
            "converging",
            Some(PatternCompletionConfig { max_iterations: 10, convergence_threshold: 0.05, ..Default::default() }),
        ),
    ];
    let mut completions = Vec::new();
    for (name, cfg) in configs {
        let mut comp = CA3PatternCompleter::new(dyn_storage.clone(), cfg);
        for q in f["queries"].as_array().unwrap() {
            let q = q.as_str().unwrap();
            let seeds = storage.vector_search(&fake_embedding(q, DIM), "p1", 5, None);
            let result = comp.complete_pattern(&seeds, "p1", None);
            completions.push(json!({
                "config": name, "query": q, "seeds": scored(&seeds), "result": scored(&result),
                "state": state_of(&comp, seeds.len(), result.len()),
            }));
        }
        let subset: Vec<NodeRef> = msgs.iter().step_by(2).cloned().collect();
        let seeds = vec![(msgs[0].clone(), 0.9), (msgs[1].clone(), 1.7), (msgs[40].clone(), -0.2)];
        let result = comp.complete_pattern(&seeds, "p1", Some(subset));
        completions.push(json!({
            "config": name, "query": "subset", "seeds": scored(&seeds), "result": scored(&result),
            "state": state_of(&comp, seeds.len(), result.len()),
        }));
    }
    check(&json!(completions), &f["completions"], "completions");

    let mut pc = CA3PatternCompleter::new(dyn_storage.clone(), None);
    assert_json_eq(&scored(&pc.complete_pattern(&[], "p1", None)), &f["empty_seeds"], "empty seeds");
    assert_json_eq(
        &scored(&pc.complete_pattern(&[(msgs[0].clone(), 0.5)], "empty-session", None)),
        &f["no_nodes"],
        "no nodes",
    );

    pc.build_edge_cache(&msgs);
    let mut with_plain = msgs.clone();
    with_plain.push(h.create_message_node("no embedding", "p2", MessageNodeArgs::default()));
    pc.build_embedding_cache(&with_plain);
    let edge_cache: serde_json::Map<String, Value> = pc
        .edge_cache()
        .iter()
        .map(|(k, v)| (k.clone(), json!(v.iter().map(|(t, w)| json!([t, w])).collect::<Vec<_>>())))
        .collect();
    assert_json_eq(&Value::Object(edge_cache), &f["edge_cache"], "edge cache");
    assert_json_eq(
        &json!(pc.embedding_cache().keys().collect::<Vec<_>>()),
        &f["embedding_cache_ids"],
        "embedding cache",
    );
    let state = pc.initialize_state(&[
        (msgs[3].clone(), 0.8),
        (msgs[10].clone(), 0.05),
        (msgs[17].clone(), 1.4),
        (msgs[22].clone(), 0.4),
    ]);
    assert_json_eq(&json!(state.activations), &f["state"], "initial state");
    let hybrid = pc.spread_activation_hybrid(&state, &msgs);
    let plain = pc.spread_activation(&state, &msgs);
    check(&json!(hybrid), &f["hybrid"], "hybrid spreading");
    check(&json!(plain), &f["plain"], "embedding spreading");
    let quiet = ActivationState {
        activations: IndexMap::from([(msgs[0].borrow().node_id.clone(), 0.05), ("ghost".to_string(), 0.1)]),
        ..Default::default()
    };
    assert_json_eq(&json!(pc.spread_activation_hybrid(&quiet, &msgs)), &f["hybrid_quiet"], "hybrid quiet");
    assert_json_eq(&json!(pc.spread_activation(&quiet, &msgs)), &f["plain_quiet"], "plain quiet");
    let delta = [
        pc.compute_activation_delta(&state.activations, &hybrid),
        pc.compute_activation_delta(&IndexMap::new(), &IndexMap::new()),
        pc.compute_activation_delta(&hybrid, &IndexMap::from([("x".to_string(), 0.25)])),
    ];
    check(&json!(delta), &f["delta"], "activation delta");
    let sparsity = json!([pc.enforce_sparsity(hybrid.clone()), pc.enforce_sparsity(state.activations.clone())]);
    check(&sparsity, &f["sparsity"], "sparsity");
    let energy = [
        pc.compute_energy(&hybrid, &msgs),
        pc.compute_energy(&plain, &msgs),
        pc.compute_energy(
            &IndexMap::from([("ghost".to_string(), 1.0), (msgs[0].borrow().node_id.clone(), 0.5)]),
            &msgs,
        ),
    ];
    check(&json!(energy), &f["energy"], "energy");
    let built = pc.build_result(&ActivationState { activations: hybrid.clone(), ..Default::default() }, &msgs[5..]);
    check(&scored(&built), &f["build_result"], "build result");

    let nowave = h.create_message_node("no waves", "p1", MessageNodeArgs::default());
    let mut ranked: Vec<(NodeRef, f64)> =
        msgs.iter().take(20).enumerate().map(|(i, n)| (n.clone(), 1.0 - 0.01 * i as f64)).collect();
    ranked.push((nowave, 1.0 - 0.01 * 20.0));
    let query = IndexMap::from([("entity".to_string(), 0.5)]);
    let mut tg = Vec::new();
    for slots in [7usize, 3, 1] {
        let org = ThetaGammaOrganizer::new(slots);
        let organized = org.organize_by_phase(&ranked, Some(&query));
        tg.push(json!({
            "slots": slots,
            "organized": organized.iter().map(|s| scored(s)).collect::<Vec<_>>(),
            "assignment": ranked.iter().map(|(n, _)| org.compute_slot_assignment(n, None)).collect::<Vec<_>>(),
            "flat": scored(&org.flatten_organized(&organized)),
        }));
    }
    assert_json_eq(&json!(tg), &f["theta_gamma"], "theta-gamma");
    let org = ThetaGammaOrganizer::default();
    assert_json_eq(
        &json!([org.organize_by_phase(&[], None).len(), scored(&org.flatten_organized(&[]))]),
        &f["theta_gamma_empty"],
        "theta-gamma empty",
    );
}
