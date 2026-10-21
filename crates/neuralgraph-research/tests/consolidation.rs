//! Parity of `retrieval::consolidation` with `consolidation.py` (`tests/golden/consolidation.json`, written by
//! `gen_consolidation.py`). One test replays the generator's sequence (three graphs share the uuid counter).

mod common;
mod engine_common;

use common::{assert_json_eq, load, s};
use engine_common::{add_messages, add_semantic_and_entity_edges, dt, ids, install, set_now, DIM};
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::consolidation::{ConsolidationConfig, ConsolidationManager};
use neuralgraph_research::retrieval::hierarchy::{EngineError, HierarchyManager, MessageNodeArgs};
use neuralgraph_research::retrieval::temporal::TemporalChainManager;
use neuralgraph_research::retrieval::{
    ConsolidationResult, ConsolidationState, EdgeType, InMemoryNeuralGraphStorage, NodeRef,
};
use serde_json::{json, Map, Value};
use std::rc::Rc;

/// One `DUPLICATES` entry of the generator: (source spec, content override, entity ids, speaker).
type Duplicate = (usize, Option<&'static str>, &'static [&'static str], Option<&'static str>);

const DUPLICATES: [Duplicate; 3] = [
    (0, Some("Hey Mel, good to see you!! How have you been?"), &["ent:friends"], None),
    (3, None, &["ent:kids", "ent:school"], Some("Melanie")),
    (3, None, &[], None),
];

fn cycle(r: &ConsolidationResult) -> Value {
    let mut d = r.to_dict();
    d.as_object_mut().unwrap().shift_remove("total_time_ms");
    d
}

fn res(r: Result<Option<NodeRef>, EngineError>) -> Value {
    match r {
        Ok(n) => json!({"ok": n.map(|n| n.borrow().node_id.clone())}),
        Err(e) => json!({"error": e.to_string()}),
    }
}

fn build(
    storage: &Rc<InMemoryNeuralGraphStorage>,
    h: &HierarchyManager,
    tm: &TemporalChainManager,
    specs: &[Value],
    session: &str,
) -> Vec<NodeRef> {
    let specs_value = Value::Array(specs.to_vec());
    let mut msgs = add_messages(h, &specs_value, session);
    for (src, content, ents, speaker) in DUPLICATES {
        let base = specs[src]["content"].as_str().unwrap();
        let text = content.map(str::to_string).unwrap_or_else(|| format!("{base} Really."));
        let mut metadata = Map::new();
        if let Some(sp) = speaker {
            metadata.insert("speaker".into(), json!(sp));
        }
        let args = MessageNodeArgs {
            embedding: Some(fake_embedding(base, DIM)),
            entity_ids: Some(ents.iter().map(|e| e.to_string()).collect()),
            importance_score: 0.45,
            created_at: Some(dt(&specs[src]["created_at"])),
            metadata,
            ..Default::default()
        };
        msgs.push(h.create_message_node(&text, session, args));
    }
    let lonely = MessageNodeArgs {
        importance_score: 0.3,
        embedding: Some(fake_embedding("ok", DIM)),
        created_at: Some(dt(&specs[1]["created_at"])),
        ..Default::default()
    };
    msgs.push(h.create_message_node("ok", session, lonely));
    tm.build_temporal_chain(&msgs[..specs.len()], session).unwrap();
    add_semantic_and_entity_edges(storage.as_ref(), &msgs[..specs.len()], 6);
    msgs
}

#[test]
fn consolidation_sequence_matches() {
    let f = load("consolidation.json");
    let nows = &f["nows"];
    install(s(&nows["t0"]));
    let specs = f["specs"].as_array().unwrap().clone();

    let st = Rc::new(InMemoryNeuralGraphStorage::default());
    let h = Rc::new(HierarchyManager::new(st.clone(), None));
    let tm = Rc::new(TemporalChainManager::new(st.clone(), None));
    let msgs = build(&st, &h, &tm, &specs, "c1");
    let cm = ConsolidationManager::new(st.clone(), Some(h.clone()), Some(tm.clone()), None);
    assert_json_eq(&cm.get_consolidation_statistics("c1"), &f["stats_t0"], "stats t0");
    assert_json_eq(&cm.estimate_memory_savings("c1").unwrap(), &f["savings_t0"], "savings t0");
    let protect: Vec<bool> = msgs.iter().map(|n| cm.should_protect_factual_node(n)).collect();
    assert_json_eq(&json!(protect), &f["protect_t0"], "protection t0");

    set_now(s(&nows["t19d"]));
    for i in [4, 11, 20] {
        for _ in 0..12 {
            msgs[i].borrow_mut().activate(0.5, None);
        }
    }
    msgs[30].borrow_mut().activate(0.2, None);
    set_now(s(&nows["t20d"]));
    msgs[2].borrow_mut().consolidation_state = ConsolidationState::Consolidating;
    {
        let mut n = msgs[9].borrow_mut();
        n.consolidation_state = ConsolidationState::Consolidating;
        n.heat_score = 0.1;
    }
    {
        let mut n = msgs[16].borrow_mut();
        n.consolidation_state = ConsolidationState::Consolidating;
        n.updated_at = dt(&nows["t20d"]).add(chrono::TimeDelta::minutes(-2));
    }
    assert_json_eq(&cm.get_consolidation_statistics("c1"), &f["stats_t20"], "stats t20");
    let clusters: Vec<Value> = cm.find_dense_clusters("c1").iter().map(|c| ids(c)).collect();
    assert_json_eq(&json!(clusters), &f["clusters"], "dense clusters");
    assert_json_eq(&cycle(&cm.run_consolidation_cycle("c1").unwrap()), &f["cycle1"], "cycle 1");
    assert_json_eq(&cycle(&cm.run_consolidation_cycle("c1").unwrap()), &f["cycle1_again"], "cycle within interval");
    assert_json_eq(&engine_common::dump_graph(st.as_ref(), "c1"), &f["graph1"], "graph after cycle 1");
    assert_json_eq(&cm.get_consolidation_statistics("c1"), &f["stats_after"], "stats after");
    assert_json_eq(&cm.estimate_memory_savings("c1").unwrap(), &f["savings_after"], "savings after");

    // second graph
    let st2 = Rc::new(InMemoryNeuralGraphStorage::default());
    let h2 = HierarchyManager::new(st2.clone(), None);
    let tm2 = TemporalChainManager::new(st2.clone(), None);
    set_now(s(&nows["t0"]));
    let msgs2 = build(&st2, &h2, &tm2, &specs[..30], "c2");
    set_now(s(&nows["t60d"]));
    let cm2 = ConsolidationManager::new(
        st2.clone(),
        None,
        None,
        Some(ConsolidationConfig {
            min_cycle_interval_seconds: 0.0,
            prune_batch_size: 7,
            merge_max_per_cycle: 1,
            hub_max_per_cycle: 1,
            hub_cluster_size_threshold: 3,
            ..Default::default()
        }),
    );
    let cycles: Vec<Value> = (0..3).map(|_| cycle(&cm2.run_consolidation_cycle("c2").unwrap())).collect();
    assert_json_eq(&json!(cycles), &f["c2_cycles"], "c2 cycles");
    assert_json_eq(&engine_common::dump_graph(st2.as_ref(), "c2"), &f["graph2"], "graph 2");

    // helpers
    let st3 = Rc::new(InMemoryNeuralGraphStorage::default());
    let h3 = HierarchyManager::new(st3.clone(), None);
    let tm3 = TemporalChainManager::new(st3.clone(), None);
    set_now(s(&nows["t0"]));
    let msgs3 = build(&st3, &h3, &tm3, &specs[..20], "c3");
    set_now(s(&nows["t20d"]));
    let cm3 = ConsolidationManager::new(
        st3.clone(),
        None,
        None,
        Some(ConsolidationConfig { recency_aware_retention: false, ..Default::default() }),
    );
    let prune = [
        cm3.prune_edges_by_type("c3", EdgeType::Entity, Some(0.2)),
        cm3.prune_edges_by_type("c3", EdgeType::Semantic, None),
        cm3.prune_edges_by_type("c3", EdgeType::Temporal, Some(0.0)),
    ];
    assert_json_eq(&json!(prune), &f["prune_by_type"], "prune by type");
    let id0 = msgs3[0].borrow().node_id.clone();
    let boost = [cm3.boost_heat(&id0, 0.5), cm3.boost_heat(&id0, 20.0), cm3.boost_heat("missing", 1.0)];
    assert_json_eq(&json!(boost), &f["boost"], "boost heat");
    msgs3[5].borrow_mut().heat_score = 0.0;
    let decay = [cm3.decay_all_heat("c3", None), cm3.decay_all_heat("c3", Some(0.3))];
    assert_json_eq(&json!(decay), &f["decay_all"], "decay all heat");
    assert_json_eq(&json!(cm3.protect_session_facts("c3")), &f["protect_session"], "protect session facts");
    let (promoted, evicted) = cm3.process_heat_lifecycle("c3");
    assert_json_eq(&json!([promoted, evicted]), &f["lifecycle"], "heat lifecycle");
    assert_json_eq(&cm3.get_consolidation_statistics("nothing"), &f["empty_stats"], "empty stats");
    let empty_savings = match cm3.estimate_memory_savings("nothing") {
        Ok(v) => json!({"ok": v}),
        Err(e) => json!({"error": e.to_string()}),
    };
    assert_json_eq(&empty_savings, &f["empty_savings"], "empty savings");
    assert_json_eq(&res(cm3.create_hub_for_cluster(&[], "c3")), &f["hub_empty"], "empty hub");
    let naive_args =
        MessageNodeArgs { created_at: PyDateTime::fromisoformat("2023-05-08T13:00:00"), ..Default::default() };
    let naive = h3.create_message_node("naive", "c3", naive_args);
    assert_json_eq(
        &res(cm3.create_hub_for_cluster(&[msgs3[1].clone(), naive.clone()], "c3")),
        &f["hub_mixed"],
        "mixed hub",
    );
    {
        let mut n = naive.borrow_mut();
        n.consolidation_state = ConsolidationState::Consolidating;
        n.updated_at = PyDateTime::fromisoformat("2026-01-01T00:00:00").unwrap();
    }
    let recover = match cm3.recover_orphaned_nodes("c3") {
        Ok(n) => json!({"ok": n}),
        Err(e) => json!({"error": e.to_string()}),
    };
    assert_json_eq(&recover, &f["recover_naive"], "recover with naive updated_at");
    assert_json_eq(&engine_common::dump_graph(st3.as_ref(), "c3"), &f["graph3"], "graph 3");
    assert_json_eq(&json!([msgs.len(), msgs2.len(), msgs3.len()]), &f["counts"], "counts");
}
