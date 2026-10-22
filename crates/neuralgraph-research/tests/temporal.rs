//! Parity of `retrieval::temporal` with `temporal.py` (`tests/golden/temporal.json`, written by
//! `gen_temporal.py`). One test replays the generator's whole sequence: later steps depend on the edges and
//! activations of earlier ones.

mod common;
mod engine_common;

use chrono::TimeDelta;
use common::{assert_json_eq, load, s};
use engine_common::{add_messages, dt, edge_dicts, ids, install, scored, set_now};
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::hierarchy::{EngineError, HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::temporal::{TemporalChainManager, TemporalConfig};
use neuralgraph_research::retrieval::{
    edge_ref, EdgeRef, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge, NeuralGraphStorage, NodeLayer, NodeRef,
};
use serde_json::{json, Map, Value};
use std::rc::Rc;

fn chain(pairs: &[(NodeRef, EdgeRef)]) -> Value {
    json!(pairs
        .iter()
        .map(|(n, e)| json!([n.borrow().node_id.clone(), e.borrow().edge_id.clone()]))
        .collect::<Vec<_>>())
}

fn res<T: Into<Value>>(r: Result<T, EngineError>) -> Value {
    match r {
        Ok(v) => json!({"ok": v.into()}),
        Err(e) => json!({"error": e.to_string()}),
    }
}

fn opt_dt(v: &Value) -> Option<PyDateTime> {
    v.as_str().map(|x| PyDateTime::fromisoformat(x).unwrap())
}

fn opt_strings(v: &Value) -> Option<Vec<String>> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap().to_string()).collect())
}

#[test]
fn temporal_manager_sequence_matches() {
    let f = load("temporal.json");
    let nows = &f["nows"];
    install(s(&nows["t0"]));
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let h = HierarchyManager::new(storage.clone(), None);
    let tm = TemporalChainManager::new(storage.clone(), None);
    let m = add_messages(&h, &f["specs"], "t1");

    // chains
    let reversed: Vec<NodeRef> = m.iter().rev().cloned().collect();
    assert_json_eq(&edge_dicts(&tm.build_temporal_chain(&reversed, "t1").unwrap()), &f["chain"], "chain");
    assert_json_eq(&edge_dicts(&tm.build_temporal_chain(&m[..1], "t1").unwrap()), &f["chain_single"], "single");
    let last = dt(&f["specs"][f["specs"].as_array().unwrap().len() - 1]["created_at"]);
    let mut extra = Vec::new();
    for (i, minutes) in [5, 30, 2000].into_iter().enumerate() {
        let args = MessageNodeArgs { created_at: Some(last.add(TimeDelta::minutes(minutes))), ..Default::default() };
        extra.push(h.create_message_node(&format!("extra note {i}"), "t1", args));
    }
    assert_json_eq(
        &edge_dicts(&tm.auto_link_temporal("t1", NodeLayer::Message).unwrap()),
        &f["auto_link"],
        "auto link",
    );
    assert_json_eq(
        &edge_dicts(&tm.auto_link_temporal("t1", NodeLayer::Episode).unwrap()),
        &f["auto_link_episode"],
        "auto link episodes",
    );
    let at = |iso: &str| MessageNodeArgs { created_at: PyDateTime::fromisoformat(iso), ..Default::default() };
    let naive_a = h.create_message_node("naive a", "t2", at("2023-05-08T13:00:00"));
    let naive_b = h.create_message_node("naive b", "t2", at("2023-05-08T12:00:00.000500"));
    let aware_c = h.create_message_node("aware c", "t2", at("2023-05-08T14:00:00+00:00"));
    assert_json_eq(
        &tm.create_temporal_edge(&naive_a, &aware_c, "after").borrow().to_dict(),
        &f["naive_edge"],
        "naive edge",
    );
    assert_json_eq(
        &edge_dicts(&tm.build_temporal_chain(&[naive_a.clone(), naive_b.clone()], "t2").unwrap()),
        &f["naive_chain"],
        "naive chain",
    );
    let mixed = tm.build_temporal_chain(&[naive_a.clone(), aware_c.clone()], "t2").map(|e| edge_dicts(&e));
    assert_json_eq(&res(mixed), &f["mixed_chain"], "mixed chain");

    // causal
    let causal = [
        tm.create_causal_edge(&m[0], &m[1], 0.5, "causes"),
        tm.create_causal_edge(&m[5], &m[1], 0.8, "causes"),
        tm.create_causal_edge(&m[1], &m[2], 0.8, "causes"),
        tm.create_causal_edge(&m[1], &m[3], 0.95, "enables"),
        tm.create_causal_edge(&m[3], &m[4], 0.7, "prevents"),
        tm.create_causal_edge(&m[4], &m[1], 0.9, "causes"),
        tm.create_causal_edge(&naive_a, &aware_c, 0.6, "causes"),
    ];
    let causal: Vec<Value> = causal.iter().map(|e| e.as_ref().map_or(Value::Null, |e| e.borrow().to_dict())).collect();
    assert_json_eq(&json!(causal), &f["causal"], "causal edges");
    let loose = TemporalChainManager::new(
        storage.clone(),
        Some(TemporalConfig { causal_time_order_required: false, ..Default::default() }),
    );
    let loose_edge = loose.create_causal_edge(&m[6], &m[2], 0.61, "causes").unwrap();
    assert_json_eq(&loose_edge.borrow().to_dict(), &f["causal_loose"], "loose causal");
    let follow_causal = json!({
        "effects": chain(&tm.follow_causal_chain(&m[1], "effects", 3)),
        "effects_1": chain(&tm.follow_causal_chain(&m[1], "effects", 1)),
        "causes": chain(&tm.follow_causal_chain(&m[4], "causes", 5)),
        "none": chain(&tm.follow_causal_chain(&m[9], "effects", 3)),
    });
    assert_json_eq(&follow_causal, &f["follow_causal"], "follow causal");

    // temporal traversal
    let follow_temporal = json!({
        "backward": chain(&tm.follow_temporal_chain(&m[8], "backward", 3, 0.1)),
        "forward": chain(&tm.follow_temporal_chain(&m[8], "forward", 5, 0.1)),
        "strict": chain(&tm.follow_temporal_chain(&m[8], "backward", 5, 0.99999)),
        "start": chain(&tm.follow_temporal_chain(&m[0], "backward", 5, 0.1)),
    });
    assert_json_eq(&follow_temporal, &f["follow_temporal"], "follow temporal");
    let ctx = tm.get_temporal_context(&m[15], 2, 4);
    assert_json_eq(&json!({"before": ids(&ctx.before), "after": ids(&ctx.after)}), &f["context"], "context");

    // co-activation
    let co = [
        tm.record_co_activation(&[m[2].clone(), m[7].clone(), m[4].clone()], "retrieval"),
        tm.record_co_activation(&[m[7].clone(), m[2].clone(), m[11].clone(), m[2].clone()], "test"),
        tm.record_co_activation(&[m[3].clone()], "retrieval"),
    ];
    assert_json_eq(&json!(co), &f["co_activation"], "co-activation counts");
    let ltp = [
        tm.get_ltp_strength(&m[2], &m[7]),
        tm.get_ltp_strength(&m[7], &m[2]),
        tm.get_ltp_strength(&m[4], &m[11]),
        tm.get_ltp_strength(&m[0], &m[1]),
    ];
    assert_json_eq(&json!(ltp), &f["ltp_strength"], "ltp strength");

    // three hours later
    set_now(s(&nows["t3h"]));
    let mut summ_nodes: Vec<NodeRef> = m.iter().step_by(3).cloned().collect();
    summ_nodes.extend(extra.iter().cloned());
    let summation: Vec<Vec<f64>> = summ_nodes
        .iter()
        .map(|n| [None, Some(12.0), Some(0.0), Some(2.5)].iter().map(|w| tm.temporal_summation(n, *w)).collect())
        .collect();
    assert_json_eq(&json!(summation), &f["summation"], "summation");
    let threshold: Vec<Vec<bool>> = m
        .iter()
        .step_by(4)
        .map(|n| [None, Some(0.1), Some(5.0)].iter().map(|t| tm.check_summation_threshold(n, *t)).collect())
        .collect();
    assert_json_eq(&json!(threshold), &f["threshold"], "summation threshold");
    let mut stdp = Vec::new();
    for (i, dt_ms) in [15.0, -8.0, 0.0, 400.0, -1e6, 3.0].into_iter().enumerate() {
        let e = &f["chain"][i];
        stdp.push(tm.apply_stdp(s(&e["source_id"]), s(&e["target_id"]), dt_ms));
    }
    stdp.push(tm.apply_stdp(&m[0].borrow().node_id.clone(), &m[5].borrow().node_id.clone(), 5.0));
    assert_json_eq(&json!(stdp), &f["stdp"], "stdp");

    // a month later
    set_now(s(&nows["t30d"]));
    assert_eq!(json!(tm.decay_ltp("t1", None)), f["decay_ltp"]);
    assert_eq!(json!(tm.decay_ltp("t1", Some(0.5))), f["decay_ltp_fast"]);
    assert_eq!(json!(tm.decay_ltp("t2", Some(0.0))), f["decay_ltp_t2"]);
    let patterns: Map<String, Value> =
        [1, 2, 3, 500].iter().map(|k| (k.to_string(), json!(tm.find_temporal_patterns("t1", *k)))).collect();
    assert_json_eq(&Value::Object(patterns), &f["patterns"], "patterns");

    // refractory
    let r = &m[20];
    let iso = |n: &NodeRef| n.borrow().refractory_until.unwrap().isoformat();
    let mut refr = Map::new();
    refr.insert("initial".into(), json!([tm.is_in_refractory(r), tm.get_refractory_remaining(r)]));
    r.borrow_mut().activate(0.4, None);
    refr.insert("activated".into(), json!([tm.is_in_refractory(r), tm.get_refractory_remaining(r), iso(r)]));
    tm.set_refractory_period(r, Some(250.5)).unwrap();
    refr.insert("set".into(), json!([tm.is_in_refractory(r), tm.get_refractory_remaining(r), iso(r)]));
    refr.insert("default".into(), res(tm.set_refractory_period(r, None).map(|_| Value::Null)));
    refr.insert("zero".into(), res(tm.set_refractory_period(r, Some(0.0)).map(|_| Value::Null)));
    r.borrow_mut().refractory_until = PyDateTime::fromisoformat("2026-10-30T08:07:00.125000");
    refr.insert("naive".into(), json!([tm.is_in_refractory(r), tm.get_refractory_remaining(r)]));
    r.borrow_mut().refractory_until = Some(dt(&nows["t30d"]).add(TimeDelta::seconds(-1)));
    refr.insert("past".into(), json!([tm.is_in_refractory(r), tm.get_refractory_remaining(r)]));
    assert_json_eq(&Value::Object(refr), &f["refractory"], "refractory");

    // consistency boosts
    let mut stamp_meta = Map::new();
    stamp_meta.insert("timestamp".into(), json!("2023-05-08"));
    let stamped =
        h.create_message_node("nothing temporal", "t3", MessageNodeArgs { metadata: stamp_meta, ..Default::default() });
    let mut boost_nodes: Vec<NodeRef> = m.iter().step_by(4).cloned().collect();
    boost_nodes.push(stamped);
    boost_nodes.push(naive_a.clone());
    let windows: Vec<(Option<PyDateTime>, Option<PyDateTime>)> =
        f["windows"].as_array().unwrap().iter().map(|w| (opt_dt(&w[0]), opt_dt(&w[1]))).collect();
    let markers: Vec<Option<Vec<String>>> = f["markers"].as_array().unwrap().iter().map(opt_strings).collect();
    let boosts: Vec<Vec<Vec<Value>>> = boost_nodes
        .iter()
        .map(|n| {
            windows
                .iter()
                .map(|(st, en)| {
                    markers
                        .iter()
                        .map(|mk| json!(tm.compute_temporal_consistency_boost(n, *st, *en, mk.as_deref())))
                        .collect()
                })
                .collect()
        })
        .collect();
    assert_json_eq(&json!(boosts), &f["boosts"], "consistency boosts");
    let off = TemporalChainManager::new(
        storage.clone(),
        Some(TemporalConfig {
            temporal_consistency_enabled: false,
            conflict_detection_enabled: false,
            ..Default::default()
        }),
    );
    assert_json_eq(
        &json!(off.compute_temporal_consistency_boost(&m[0], windows[0].0, windows[0].1, None)),
        &f["boost_disabled"],
        "disabled boost",
    );
    let cands: Vec<(NodeRef, f64)> =
        boost_nodes.iter().enumerate().map(|(i, n)| (n.clone(), 1.0 - 0.03 * i as f64)).collect();
    let may = vec!["May".to_string()];
    let applied: Vec<Value> = windows
        .iter()
        .map(|(st, en)| scored(&tm.apply_temporal_consistency_boosts(cands.clone(), *st, *en, Some(&may))))
        .collect();
    assert_json_eq(&json!(applied), &f["applied"], "applied boosts");
    assert_json_eq(
        &scored(&off.apply_temporal_consistency_boosts(cands.clone(), windows[0].0, windows[0].1, None)),
        &f["applied_disabled"],
        "applied disabled",
    );

    // conflict decay
    let pairs: [(usize, usize, Option<&str>); 6] = [
        (12, 2, Some("before")),
        (2, 12, Some("after")),
        (2, 3, Some("after")),
        (3, 2, Some("before")),
        (0, 25, None),
        (25, 0, Some("sideways")),
    ];
    for (a, b, direction) in pairs {
        let (sid, tid) = (m[a].borrow().node_id.clone(), m[b].borrow().node_id.clone());
        let id = format!("conflict-{}-{}", &sid[sid.len() - 2..], &tid[tid.len() - 2..]);
        let mut e = NeuralEdge::new(id, sid, tid, EdgeType::Temporal).with_weight(0.8);
        if let Some(d) = direction {
            let mut meta = Map::new();
            meta.insert("direction".into(), json!(d));
            e.metadata = meta;
        }
        e.effective_weight();
        storage.save_edge(&edge_ref(e));
    }
    let t30 = dt(&nows["t30d"]);
    let conflicts = [
        tm.decay_conflicting_edges("t1", t30, 24.0),
        tm.decay_conflicting_edges("t1", PyDateTime::fromisoformat("2026-01-01T00:00:00").unwrap(), 1e6),
        tm.decay_conflicting_edges("t1", t30, 0.5),
        off.decay_conflicting_edges("t1", t30, 24.0),
    ];
    assert_json_eq(&json!(conflicts), &f["conflicts"], "conflict decay");
    assert_json_eq(&engine_common::dump_graph(storage.as_ref(), "t1"), &f["graph"], "graph t1");
    assert_json_eq(&engine_common::dump_graph(storage.as_ref(), "t2"), &f["graph_t2"], "graph t2");
}
