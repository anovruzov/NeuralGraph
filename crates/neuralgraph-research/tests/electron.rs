//! Parity of `retrieval::electron` with the Python (`tests/golden/electron.json`, written by `gen_electron.py`).
//! Electron ids come from the shared uuid counter, so every construction replays in the generator's order.

mod common;
mod engine_common;
mod retrievers_common;

use common::{assert_json_eq, load, FIXED_NOW};
use engine_common::{ids, install, DIM};
use indexmap::IndexMap;
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::retrieval::dialogue_linker::{set_deterministic_hex_ids, DialogueLinker};
use neuralgraph_research::retrieval::electron::{
    calculate_resonance, AccumulatedCharge, Electron, ElectronPool, ElectronRetriever, ElectronRetrieverConfig,
};
use neuralgraph_research::retrieval::{EdgeType, InMemoryNeuralGraphStorage, NeuralEdge, NeuralGraphStorage};
use retrievers_common::{build_graph, opt_waves, result_json};
use serde_json::{json, Value};
use std::rc::Rc;

type Waves = IndexMap<String, f64>;

fn edump(e: &Electron) -> Value {
    json!([
        e.electron_id,
        e.charge,
        e.energy,
        e.frequency,
        e.phase,
        e.source_node_id,
        e.current_node_id,
        e.birth_time_ms,
        e.arrival_time_ms,
        e.hop_count,
        e.path,
        e.wave_signature,
        e.decay_factor
    ])
}

fn cdump(c: &AccumulatedCharge) -> Value {
    json!([
        c.node_id,
        c.excitatory_charge,
        c.inhibitory_charge,
        c.electron_count,
        c.last_arrival_ms,
        c.phase_coherence,
        c.net_charge(),
        c.total_energy(),
        c.exceeds_threshold(0.5),
        c.exceeds_threshold(0.05)
    ])
}

fn pool_state(p: &ElectronPool) -> Value {
    json!({
        "electrons": p.electrons().iter().map(edump).collect::<Vec<_>>(),
        "charge": p.node_charge().values().map(cdump).collect::<Vec<_>>(),
        "fired": p.fired_nodes().iter().map(|(a, b, c)| json!([a, b, c])).collect::<Vec<_>>(),
        "refractory": p.refractory_until(),
        "time": p.current_time_ms(),
        "waves_cached": p.node_wave_amplitudes().keys().collect::<Vec<_>>(),
    })
}

fn pairs(v: &[(String, f64)]) -> Value {
    json!(v.iter().map(|(a, b)| json!([a, b])).collect::<Vec<_>>())
}

fn ok_or_err<T>(r: Result<T, impl std::fmt::Display>, f: impl Fn(T) -> Value) -> Value {
    match r {
        Ok(v) => json!({"ok": f(v)}),
        Err(e) => json!({"error": e.to_string()}),
    }
}

fn waves_obj(pairs: &[(&str, f64)]) -> Waves {
    pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
}

fn check_pure(f: &Value, waves: &[Option<Waves>]) {
    let p = &f["pure"];
    let mut sigs: Vec<Waves> = waves.iter().flatten().cloned().collect();
    sigs.push(waves_obj(&[("entity", 0.0005)]));
    sigs.push(waves_obj(&[("temporal", -0.9), ("entity", 0.1)]));
    sigs.push(waves_obj(&[("action", 0.3), ("state", 0.3), ("spatial", 0.3), ("causal", 0.3)]));
    assert_json_eq(&json!(sigs), &p["sigs"], "signatures");
    let res: Vec<Vec<f64>> =
        sigs.iter().map(|a| sigs.iter().map(|b| calculate_resonance(Some(a), b)).collect()).collect();
    assert_json_eq(&json!(res), &p["resonance"], "resonance");
    assert_json_eq(&json!(calculate_resonance(None, &sigs[2])), &p["resonance_none"], "resonance none");

    let mk = |id: &str, s: &str, t: &str, ty: EdgeType, w: f64| NeuralEdge::new(id, s, t, ty).with_weight(w);
    let mut e1 = mk("e1", "a", "b", EdgeType::Temporal, 0.8);
    e1.propagation_delay_ms = 40.0;
    let mut e2 = mk("e2", "b", "c", EdgeType::Entity, 0.6);
    e2.sign = -1.0;
    e2.confidence = 0.7;
    e2.propagation_delay_ms = 500.0;
    let mut e3 = mk("e3", "c", "d", EdgeType::Semantic, 1.3);
    e3.ltp_boost = 1.6;
    let mut edges = [e1, e2, e3];
    assert_json_eq(&json!(edges.iter().map(|e| e.to_dict()).collect::<Vec<_>>()), &p["edges"], "edges");
    let e0 = Electron {
        charge: 1.0,
        energy: 0.9,
        frequency: 14.1,
        phase: 6.0,
        source_node_id: "a".into(),
        current_node_id: "a".into(),
        path: vec!["a".into()],
        wave_signature: Some(waves_obj(&[("entity", 0.8), ("temporal", 0.1)])),
        ..Electron::default()
    };
    let mut chain = vec![e0];
    let steps: [(usize, Option<Waves>, f64); 4] = [
        (0, Some(waves_obj(&[("temporal", 0.5), ("spatial", 0.2)])), 0.3),
        (1, Some(Waves::new()), 0.3),
        (2, Some(waves_obj(&[("entity", 1.0)])), 0.75),
        (0, None, 0.5),
    ];
    for (ei, src, blend) in steps {
        let next = chain.last().unwrap().propagate(&mut edges[ei], src.as_ref(), blend).unwrap();
        chain.push(next);
    }
    assert_json_eq(&json!(chain.iter().map(edump).collect::<Vec<_>>()), &p["chain"], "electron chain");
    let interfere: Vec<Vec<f64>> = chain.iter().map(|a| chain.iter().map(|b| a.interfere_with(b)).collect()).collect();
    assert_json_eq(&json!(interfere), &p["interfere"], "interference");
    let flags: Vec<Value> =
        chain.iter().map(|e| json!([e.is_excitatory(), e.is_inhibitory(), e.effective_charge()])).collect();
    assert_json_eq(&json!(flags), &p["flags"], "flags");
    let blends = vec![
        Electron::blend_wave_signatures(&waves_obj(&[("entity", 0.8)]), &waves_obj(&[("temporal", 0.5)]), 0.3),
        Electron::blend_wave_signatures(&Waves::new(), &waves_obj(&[("x", 1.0)]), 0.5),
        Electron::blend_wave_signatures(&waves_obj(&[("a", 1.0), ("b", 2.0)]), &waves_obj(&[("b", 1.0)]), 0.0),
    ];
    assert_json_eq(&json!(blends), &p["blend"], "blend");
    let none_sig = Electron { wave_signature: None, current_node_id: "a".into(), ..Electron::default() };
    let none = vec![
        ok_or_err(none_sig.propagate(&mut edges[0], None, 0.3), |e| edump(&e)),
        ok_or_err(none_sig.propagate(&mut edges[0], Some(&waves_obj(&[("x", 1.0)])), 0.3), |e| edump(&e)),
    ];
    assert_json_eq(&json!(none), &p["none_sig"], "signature-less electron");
    let acc = AccumulatedCharge { excitatory_charge: 0.7, inhibitory_charge: -0.3, ..AccumulatedCharge::new("n") };
    assert_json_eq(&cdump(&acc), &p["acc"], "accumulated charge");
    assert_json_eq(&edump(&Electron::default()), &p["default_electron"], "default electron");
}

fn check_pool(f: &Value, st: &Rc<dyn NeuralGraphStorage>, waves: &[Option<Waves>]) {
    let p = &f["pool"];
    let seeds = common::strings(&p["seeds"]);
    let mut pool = ElectronPool::with_defaults(st.clone());
    let injected = pool.inject_query_burst(waves[2].as_ref(), &seeds, 1.0);
    let mut steps =
        vec![json!({"injected": injected.iter().map(edump).collect::<Vec<_>>(), "state": pool_state(&pool)})];
    for _ in 0..4 {
        let n = pool.propagate_step().unwrap();
        steps.push(json!({"created": n, "state": pool_state(&pool)}));
    }
    assert_json_eq(&json!(steps), &p["steps"], "pool steps");
    assert_json_eq(&pairs(&pool.get_accumulated_charges()), &p["accumulated"], "accumulated");
    let reinject = pool.inject_query_burst(waves[3].as_ref(), &seeds, 0.6);
    assert_json_eq(&json!(reinject.iter().map(edump).collect::<Vec<_>>()), &p["reinject"], "reinject");
    assert_json_eq(&pairs(&pool.propagate_until_settled(30).unwrap()), &p["settled"], "settled");
    assert_json_eq(&pool_state(&pool), &p["after_settled"], "after settled");
    pool.accumulate_charge(&Electron {
        charge: -1.0,
        energy: 0.4,
        current_node_id: seeds[0].clone(),
        arrival_time_ms: 9.0,
        ..Electron::default()
    });
    pool.accumulate_charge(&Electron {
        charge: 1.0,
        energy: 0.25,
        current_node_id: "fresh".into(),
        arrival_time_ms: 3.0,
        ..Electron::default()
    });
    let legacy = json!([cdump(&pool.node_charge()[&seeds[0]]), cdump(&pool.node_charge()["fresh"])]);
    assert_json_eq(&legacy, &p["legacy_charge"], "legacy accumulate");
    pool.reset();
    assert_json_eq(&pool_state(&pool), &p["after_reset"], "after reset");

    let every_other: Vec<String> = seeds.iter().step_by(2).cloned().collect();
    for v in p["variants"].as_array().unwrap() {
        let params = &v["params"];
        let get = |k: &str, d: f64| params.get(k).and_then(Value::as_f64).unwrap_or(d);
        let mut pool = ElectronPool::new(
            st.clone(),
            get("theta_frequency_hz", 6.0),
            get("firing_threshold", 0.5),
            get("refractory_ms", 2.0),
            params.get("max_hops").and_then(Value::as_i64).unwrap_or(5),
            get("energy_floor", 0.1),
        );
        pool.inject_query_burst(waves[v["w"].as_u64().unwrap() as usize].as_ref(), &every_other, 1.2);
        let settled = pool.propagate_until_settled(v["max_steps"].as_u64().unwrap() as usize).unwrap();
        let got = json!({
            "params": params, "w": v["w"], "max_steps": v["max_steps"], "settled": pairs(&settled),
            "accumulated": pairs(&pool.get_accumulated_charges()), "time": pool.current_time_ms(),
            "fired": pool.fired_nodes().iter().map(|(a, b, c)| json!([a, b, c])).collect::<Vec<_>>(),
        });
        assert_json_eq(&got, v, &format!("pool variant {params} w{} steps {}", v["w"], v["max_steps"]));
    }
    let mut none_pool = ElectronPool::with_defaults(st.clone());
    none_pool.inject_query_burst(None, &seeds[..2], 1.0);
    assert_json_eq(&pool_state(&none_pool), &p["none_pool_state"], "signature-less pool");
    assert_json_eq(&ok_or_err(none_pool.propagate_step(), |n| json!(n)), &p["none_pool_step"], "signature-less step");
}

#[test]
fn electron_matches_python() {
    install(FIXED_NOW);
    set_deterministic_hex_ids(Some(0));
    let f = load("electron.json");
    let waves: Vec<Option<Waves>> = vec![
        None,
        Some(Waves::new()),
        Some(waves_obj(&[("entity", 0.8), ("action", 0.5), ("emotional", 0.2)])),
        Some(waves_obj(&[("temporal", 0.9), ("causal", 0.4)])),
        Some(waves_obj(&[("emotional", 0.7), ("relational", 0.7), ("quantitative", 0.1)])),
        Some(waves_obj(&[("entity", 0.9), ("temporal", 0.35), ("__suppress_multi_hop", 1.0)])),
        Some(waves_obj(&[("unknown", 0.6), ("entity", -0.4)])),
    ];
    check_pure(&f, &waves);

    let st: Rc<dyn NeuralGraphStorage> = Rc::new(InMemoryNeuralGraphStorage::default());
    let g = build_graph(st.clone(), &f["specs"], "e1");
    assert_json_eq(&ids(&g.episodes), &f["episodes"], "episodes");
    assert_json_eq(&ids(&g.topics), &f["topics"], "topics");
    check_pool(&f, &st, &waves);

    let questions: Vec<String> = {
        let data: Value = serde_json::from_str(
            &std::fs::read_to_string(common::golden_dir().join("../../../../research/datasets/locomo10.json")).unwrap(),
        )
        .unwrap();
        let qa = data[1]["qa"].as_array().unwrap().clone();
        let mut q: Vec<String> =
            [0, 2, 5, 8, 11].iter().map(|i| qa[*i]["question"].as_str().unwrap().to_string()).collect();
        q.push("What does Gina think about Jon's dance studio?".into());
        q
    };
    let mut retrievers: IndexMap<&str, ElectronRetriever> = IndexMap::new();
    retrievers.insert("default", ElectronRetriever::new(st.clone(), None));
    retrievers.insert(
        "custom",
        ElectronRetriever::new(
            st.clone(),
            Some(ElectronRetrieverConfig {
                seed_count: 6,
                firing_threshold: 0.3,
                max_hops: 3,
                final_limit: 8,
                max_propagation_steps: 4,
                initial_energy: 0.8,
                ..Default::default()
            }),
        ),
    );
    for run in f["runs"].as_array().unwrap() {
        let r = retrievers.get_mut(run["r"].as_str().unwrap()).unwrap();
        let q = &questions[run["q"].as_u64().unwrap() as usize];
        let w = &waves[run["w"].as_u64().unwrap() as usize];
        let limit = run.get("limit").and_then(Value::as_u64).map(|l| l as usize);
        let got = result_json(r.retrieve(q, &fake_embedding(q, DIM), "e1", limit, w.as_ref()));
        let what = format!("electron {} q{} w{} limit {:?}", run["r"], run["q"], run["w"], limit);
        assert_json_eq(&got, &run["result"], &what);
        let fired: Vec<Value> = r.pool().fired_nodes().iter().map(|(a, b, c)| json!([a, b, c])).collect();
        assert_json_eq(&json!(fired), &run["fired"], &what);
    }
    let d = retrievers.get_mut("default").unwrap();
    let no_seeds = d.retrieve("anything", &fake_embedding("x", DIM), "empty-session", None, None);
    assert_json_eq(&result_json(no_seeds), &f["no_seeds"], "no seeds");

    let fired: Vec<(String, f64)> = f["fired"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| (p[0].as_str().unwrap().to_string(), p[1].as_f64().unwrap()))
        .collect();
    let boosts: Vec<Value> = [vec!["Gina"], vec!["JON", "dance"], vec![], vec!["zzz"]]
        .iter()
        .map(|ents| {
            let ents: Vec<String> = ents.iter().map(|s| s.to_string()).collect();
            pairs(&d.apply_entity_boost(&fired, &ents))
        })
        .collect();
    assert_json_eq(&json!(boosts), &f["entity_boost"], "entity boost");

    let mut linker = DialogueLinker::new(st.as_ref());
    linker.process_dialogue_sequence(&g.msgs, "e1").unwrap();
    let linked: serde_json::Map<String, Value> = g.msgs[..6]
        .iter()
        .map(|m| {
            let id = m.borrow().node_id.clone();
            let l = linker.get_linked_messages(&id);
            (id, json!(l.iter().map(|(a, b, c)| json!([a, b, c])).collect::<Vec<_>>()))
        })
        .collect();
    assert_json_eq(&Value::Object(linked), &f["linked"], "linked messages");
    let all: Vec<(String, f64)> =
        g.msgs.iter().enumerate().map(|(i, m)| (m.borrow().node_id.clone(), 1.0 / (i + 1) as f64)).collect();
    let expand = json!([
        pairs(&d.expand_with_dialogue_links(&fired, Some(&linker))),
        pairs(&d.expand_with_dialogue_links(&fired, None)),
        pairs(&d.expand_with_dialogue_links(&[], Some(&linker))),
        pairs(&d.expand_with_dialogue_links(&all, Some(&linker))),
    ]);
    assert_json_eq(&expand, &f["expand"], "dialogue expansion");
    let next = neuralgraph_research::retrieval::uuid4();
    assert_eq!(next, format!("00000000-0000-4000-0000-{:012x}", f["uuid_counter"].as_u64().unwrap() + 1), "uuid count");
    let _ = opt_waves;
}
