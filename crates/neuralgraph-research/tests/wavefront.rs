//! Parity of `retrieval::interference` and `retrieval::wavefront` with the Python (`tests/golden/wavefront.json`,
//! written by `gen_wavefront.py`).

mod common;
mod engine_common;

use common::{assert_json_eq, load, FIXED_NOW};
use engine_common::{add_messages, add_semantic_and_entity_edges, ids, install, scored, waves};
use indexmap::IndexMap;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::hierarchy::{HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::interference::{InterferenceResult, InterferenceScorer, ResonanceAmplifier};
use neuralgraph_research::retrieval::temporal::TemporalChainManager;
use neuralgraph_research::retrieval::wavefront::{DiagonalSweepRetriever, WavefrontPropagator, WavefrontResult};
use neuralgraph_research::retrieval::{InMemoryNeuralGraphStorage, NeuralGraphStorage, NodeRef};
use serde_json::{json, Value};
use std::rc::Rc;

fn result_json(r: &InterferenceResult) -> Value {
    json!([r.activation, r.interference_type.value(), r.phase_diff, r.amplitude_product])
}

fn wf_json(r: &WavefrontResult) -> Value {
    json!({
        "nodes": r.nodes.iter().map(|w| json!([
            w.node.borrow().node_id.clone(), w.activation, w.phase, w.interference_type.value(), w.hop_depth, w.path,
        ])).collect::<Vec<_>>(),
        "total_propagations": r.total_propagations,
        "constructive_count": r.constructive_count,
        "destructive_count": r.destructive_count,
        "partial_count": r.partial_count,
        "max_depth_reached": r.max_depth_reached,
    })
}

fn wave_list(v: &Value) -> Vec<IndexMap<String, f64>> {
    v.as_array().unwrap().iter().map(waves).collect()
}

#[test]
fn interference_matches() {
    let f = load("wavefront.json");
    let it = &f["interference"];
    let queries = wave_list(&it["queries"]);
    let memories = wave_list(&it["memories"]);
    let phases = common::f64s(&it["phases"]);
    let grid: Vec<Vec<Vec<Value>>> = queries
        .iter()
        .map(|q| {
            memories
                .iter()
                .map(|m| {
                    phases
                        .iter()
                        .zip(phases.iter().rev())
                        .map(|(qp, mp)| result_json(&InterferenceScorer::compute_interference(q, m, *qp, *mp)))
                        .collect()
                })
                .collect()
        })
        .collect();
    assert_json_eq(&json!(grid), &it["grid"], "interference grid");
    let pairs: Vec<Value> = phases
        .iter()
        .map(|p| result_json(&InterferenceScorer::compute_interference(&queries[0], &memories[0], 0.0, *p)))
        .collect();
    assert_json_eq(&json!([pairs]), &it["phase_pairs"], "phase pairs");

    let parse = |v: &Value| v.as_str().map(|s| PyDateTime::fromisoformat(s).unwrap());
    let phases: Vec<Vec<Value>> = it["stamps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            it["refs"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| match InterferenceScorer::compute_temporal_phase(&parse(s).unwrap(), parse(r).as_ref()) {
                    Ok(p) => json!({"ok": p}),
                    Err(e) => json!({"error": format!("TypeError: {e}")}),
                })
                .collect()
        })
        .collect();
    assert_json_eq(&json!(phases), &it["temporal_phase"], "temporal phase");

    let mut amps = queries.clone();
    amps.extend(memories.clone());
    amps.push(IndexMap::from([("entity".into(), 2.0), ("relational".into(), 1.0), ("action".into(), 3.0)]));
    let trunks: Vec<IndexMap<String, f64>> = amps.iter().map(ResonanceAmplifier::compress_to_trunk).collect();
    assert_json_eq(&json!(trunks), &it["trunks"], "trunks");
    let alignment: Vec<Vec<f64>> = trunks
        .iter()
        .map(|a| trunks.iter().map(|b| ResonanceAmplifier::compute_trunk_alignment(a, b)).collect())
        .collect();
    assert_json_eq(&json!(alignment), &it["alignment"], "trunk alignment");
    let partial = [
        ResonanceAmplifier::compute_trunk_alignment(&IndexMap::from([("entity_trunk".into(), 0.5)]), &IndexMap::new()),
        ResonanceAmplifier::compute_trunk_alignment(
            &IndexMap::from([("entity_trunk".into(), 0.5), ("x".into(), 1.0)]),
            &IndexMap::from([("entity_trunk".into(), 0.25)]),
        ),
    ];
    assert_json_eq(&json!(partial), &it["alignment_partial"], "partial alignment");
}

#[test]
fn wavefront_propagation_matches() {
    install(FIXED_NOW);
    let f = load("wavefront.json");
    let queries = wave_list(&f["interference"]["queries"]);
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let h = HierarchyManager::new(storage.clone(), None);
    let tm = TemporalChainManager::new(storage.clone(), None);
    let msgs = add_messages(&h, &f["specs"], "w1");
    tm.build_temporal_chain(&msgs, "w1").unwrap();
    add_semantic_and_entity_edges(storage.as_ref(), &msgs, 6);
    let episodes = h.auto_segment_to_episodes("w1").unwrap();
    assert_json_eq(&ids(&episodes), &f["episodes"], "episodes");
    let reference = PyDateTime::fromisoformat("2023-05-08T14:00:00+00:00").unwrap();
    let matches: Vec<Vec<Value>> = msgs
        .iter()
        .step_by(4)
        .map(|n| {
            let mut row = Vec::new();
            for q in &queries {
                for r in [None, Some(&reference)] {
                    let (a, t) = InterferenceScorer::score_memory_match(q, &n.borrow(), r).unwrap();
                    row.push(json!([a, t.value()]));
                }
            }
            row
        })
        .collect();
    assert_json_eq(&json!(matches), &f["match"], "memory match");

    let prop = WavefrontPropagator::with_defaults(storage.clone());
    let loose = WavefrontPropagator::new(storage.clone(), 0.95, 0.9, 0.01);
    let seeds_sets: Vec<Vec<NodeRef>> = vec![
        vec![msgs[0].clone()],
        vec![msgs[5].clone(), msgs[5].clone(), msgs[12].clone()],
        vec![episodes[0].clone()],
        vec![episodes[1].clone(), msgs[25].clone()],
        msgs.iter().step_by(6).cloned().collect(),
        vec![],
    ];
    assert_json_eq(&json!(seeds_sets.iter().map(|s| ids(s)).collect::<Vec<_>>()), &f["seeds_sets"], "seed sets");
    let mut runs = Vec::new();
    for (qi, q) in queries.iter().enumerate() {
        for (si, seeds) in seeds_sets.iter().enumerate() {
            let combos: [(&str, &WavefrontPropagator, i64, i64); 8] = [
                ("default", &prop, 5, 2),
                ("default", &prop, 1, 0),
                ("default", &prop, 0, 1),
                ("default", &prop, 3, 3),
                ("loose", &loose, 5, 2),
                ("loose", &loose, 1, 0),
                ("loose", &loose, 0, 1),
                ("loose", &loose, 2, 4),
            ];
            for (name, p, th, hh) in combos {
                let r = p.propagate(q, seeds, th, hh, None).unwrap();
                runs.push(json!({
                    "propagator": name, "query": qi, "seeds": si, "time_hops": th, "hierarchy_hops": hh,
                    "result": wf_json(&r),
                }));
            }
        }
    }
    assert_json_eq(&json!(runs), &f["runs"], "propagation runs");
    let r = prop.propagate(&queries[0], &[msgs[3].clone()], 5, 2, Some(reference)).unwrap();
    assert_json_eq(&wf_json(&r), &f["run_ref"], "explicit reference time");
    let tight = WavefrontPropagator::new(storage.clone(), 0.5, 0.99, 0.4);
    let every3: Vec<NodeRef> = msgs.iter().step_by(3).cloned().collect();
    assert_json_eq(&wf_json(&tight.propagate(&queries[1], &every3, 5, 2, None).unwrap()), &f["run_tight"], "tight");
    let naive_time = PyDateTime::fromisoformat("2023-05-08T13:00:00").unwrap();
    let naive =
        h.create_message_node("naive", "w1", MessageNodeArgs { created_at: Some(naive_time), ..Default::default() });
    let e = prop.propagate(&queries[0], &[naive], 5, 2, None).unwrap_err();
    assert_eq!(format!("TypeError: {e}"), f["naive_error"].as_str().unwrap());

    let every5: Vec<NodeRef> = msgs.iter().step_by(5).cloned().collect();
    let loose_retriever = DiagonalSweepRetriever::new(loose.clone(), 0.4, 0.5);
    let retrieve_loose: Vec<Value> =
        queries.iter().map(|q| scored(&loose_retriever.retrieve(q, &every5, 25).unwrap())).collect();
    assert_json_eq(&json!(retrieve_loose), &f["retrieve_loose"], "loose retrieval");
    let retriever = DiagonalSweepRetriever::new(prop.clone(), 0.4, 0.5);
    let mut retrieved = Vec::new();
    for q in &queries {
        for seeds in &seeds_sets[..5] {
            retrieved.push(scored(&retriever.retrieve(q, seeds, 20).unwrap()));
        }
    }
    assert_json_eq(&json!(retrieved), &f["retrieve"], "retrieval");
    let custom = DiagonalSweepRetriever::new(tight, 0.1, 0.9);
    let every2: Vec<NodeRef> = msgs.iter().step_by(2).cloned().collect();
    let retrieve_custom = json!([
        scored(&custom.retrieve(&queries[0], &every2, 7).unwrap()),
        scored(&custom.retrieve(&queries[2], &msgs, 0).unwrap()),
    ]);
    assert_json_eq(&retrieve_custom, &f["retrieve_custom"], "custom retrieval");
    assert_json_eq(&engine_common::dump_graph(storage.as_ref(), "w1"), &f["graph"], "graph");
    assert_eq!(storage.get_nodes_by_session("w1", None).len(), f["graph"]["nodes"].as_array().unwrap().len());
}
