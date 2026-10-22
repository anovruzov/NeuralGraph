//! Parity of `retrieval::gating` with `gating.py` (`tests/golden/gating.json`, written by `gen_gating.py`).

mod common;
mod engine_common;

use common::{assert_json_eq, load, FIXED_NOW};
use neuralgraph_research::retrieval::gating::{
    create_balanced_registry, create_multi_hop_registry, create_permissive_registry, create_profiled_registry,
    create_single_hop_registry, create_strict_registry, gate_profiles, get_profile, inhibitory_gate, leaky_relu,
    multi_hop_profile, open_domain_profile, relu, sigmoid, soft_threshold, temporal_query_profile, AdaptiveGate,
    CompositeGate, EdgeGateRegistry, GateConfig, GateContext, GateFn, GateProfile, GateRegistry, ProfiledGateRegistry,
};
use neuralgraph_research::retrieval::NeuralEdge;
use serde_json::{json, Value};
use std::rc::Rc;

fn edges(f: &Value) -> Vec<NeuralEdge> {
    f["edges"].as_array().unwrap().iter().map(|d| NeuralEdge::from_dict(d.as_object().unwrap()).unwrap()).collect()
}

fn contexts(f: &Value) -> Vec<GateContext> {
    f["contexts"].as_array().unwrap().iter().map(|c| c.as_object().unwrap().clone()).collect()
}

fn config_json(c: Option<&GateConfig>) -> Value {
    match c {
        None => Value::Null,
        Some(c) => json!({
            "threshold": c.threshold,
            "steepness": c.steepness,
            "recency_half_life_days": c.recency_half_life_days,
            "ltp_weight": c.ltp_weight,
            "confidence_weight": c.confidence_weight,
        }),
    }
}

fn profile_json(p: &GateProfile) -> Value {
    json!({
        "name": p.name,
        "semantic_weight": p.semantic_weight,
        "entity_weight": p.entity_weight,
        "temporal_weight": p.temporal_weight,
        "causal_weight": p.causal_weight,
        "hierarchy_weight": p.hierarchy_weight,
        "co_activation_weight": p.co_activation_weight,
        "base_config": config_json(p.base_config.as_ref()),
        "exploration_factor": p.exploration_factor,
    })
}

#[test]
fn scalar_activation_functions_match() {
    let f = load("gating.json");
    let sc = &f["scalars"];
    let xs = common::f64s(&sc["xs"]);
    let grid = |params: &[(f64, f64)], g: &dyn Fn(f64, f64, f64) -> f64| -> Value {
        json!(params.iter().map(|(a, b)| xs.iter().map(|x| g(*x, *a, *b)).collect::<Vec<_>>()).collect::<Vec<_>>())
    };
    assert_json_eq(&grid(&[(5.0, 0.0), (1.0, 0.3), (10.0, 0.4), (2.0, 0.15)], &sigmoid), &sc["sigmoid"], "sigmoid");
    assert_json_eq(&grid(&[(0.0, 0.0), (0.4, 0.0)], &|x, t, _| relu(x, t)), &sc["relu"], "relu");
    assert_json_eq(&grid(&[(0.0, 0.1), (0.3, 0.2)], &leaky_relu), &sc["leaky_relu"], "leaky_relu");
    assert_json_eq(
        &grid(&[(0.3, 0.1), (0.2, 0.0), (0.5, -1.0), (0.1, 0.5)], &soft_threshold),
        &sc["soft_threshold"],
        "soft_threshold",
    );
}

#[test]
fn registry_gates_match_on_the_grid() {
    common::pin_now(FIXED_NOW);
    let f = load("gating.json");
    let ctxs = contexts(&f);
    let mut es = edges(&f);
    let registries: Vec<(&str, Box<dyn GateRegistry>)> = vec![
        ("balanced", Box::new(create_balanced_registry())),
        ("strict", Box::new(create_strict_registry())),
        ("permissive", Box::new(create_permissive_registry())),
        ("profiled_default", Box::new(ProfiledGateRegistry::new(None))),
        ("single_hop", Box::new(create_single_hop_registry())),
        ("multi_hop", Box::new(create_multi_hop_registry())),
        ("temporal", Box::new(ProfiledGateRegistry::new(Some(temporal_query_profile())))),
        ("open_domain", Box::new(ProfiledGateRegistry::new(Some(open_domain_profile())))),
        ("default_profile", Box::new(ProfiledGateRegistry::new(Some(get_profile("default"))))),
        ("profiled_plain_multi_hop", Box::new(create_profiled_registry(&multi_hop_profile()))),
        ("edge_registry_none", Box::new(EdgeGateRegistry::new(None))),
    ];
    for (name, reg) in &registries {
        let got: Value = json!(es
            .iter_mut()
            .map(|e| ctxs.iter().map(|c| reg.compute_gate(e, c)).collect::<Vec<_>>())
            .collect::<Vec<_>>());
        assert_json_eq(&got, &f["gates"][*name], name);
        assert_json_eq(&config_json(Some(reg.config())), &f["registry_configs"][*name], "config");
    }
    let balanced = create_balanced_registry();
    let direct: Value = json!(es
        .iter_mut()
        .map(|e| {
            let t = e.edge_type;
            ctxs.iter().map(|c| balanced.get_gate(t)(e, c)).collect::<Vec<_>>()
        })
        .collect::<Vec<_>>());
    assert_json_eq(&direct, &f["direct"], "get_gate");
    let empty = GateContext::new();
    let default_gate: Value = json!(es.iter_mut().map(|e| balanced.default_gate(e, &empty)).collect::<Vec<_>>());
    assert_json_eq(&default_gate, &f["default_gate"], "default gate");
    let inhibitory: Value = json!(es
        .iter_mut()
        .map(|e| ctxs.iter().map(|c| inhibitory_gate(e, c)).collect::<Vec<_>>())
        .collect::<Vec<_>>());
    assert_json_eq(&inhibitory, &f["inhibitory"], "inhibitory gate");
    let none_context: Value = json!(es.iter_mut().map(|e| balanced.compute_gate(e, &empty)).collect::<Vec<_>>());
    assert_json_eq(&none_context, &f["none_context"], "context None");
}

#[test]
fn profiles_and_switching_match() {
    common::pin_now(FIXED_NOW);
    let f = load("gating.json");
    let profiles: serde_json::Map<String, Value> =
        gate_profiles().iter().map(|(k, p)| (k.to_string(), profile_json(p))).collect();
    assert_json_eq(&Value::Object(profiles), &f["profiles"], "GATE_PROFILES");
    for (q, name) in f["get_profile"].as_object().unwrap() {
        assert_eq!(get_profile(q).name, name.as_str().unwrap(), "get_profile({q})");
    }
    let mut es = edges(&f);
    let mut reg = ProfiledGateRegistry::new(None);
    let empty = GateContext::new();
    let mut got = Vec::new();
    for name in ["multi_hop", "default", "bogus", "temporal", "single_hop"] {
        reg.set_profile(name);
        let values: Vec<f64> = es.iter_mut().step_by(7).map(|e| reg.compute_gate(e, &empty)).collect();
        got.push(json!({
            "name": name,
            "profile": reg.profile().name,
            "config": config_json(Some(GateRegistry::config(&reg))),
            "values": values,
        }));
    }
    assert_json_eq(&json!(got), &f["switching"], "set_profile");
}

#[test]
fn composite_and_adaptive_gates_match() {
    common::pin_now(FIXED_NOW);
    let f = load("gating.json");
    let mut es = edges(&f);
    let balanced = Rc::new(create_balanced_registry());
    let strict = Rc::new(create_strict_registry());
    let b = balanced.clone();
    let balanced_fn: GateFn = Rc::new(move |e: &mut NeuralEdge, c: &GateContext| b.compute_gate(e, c));
    let s = strict.clone();
    let strict_fn: GateFn = Rc::new(move |e: &mut NeuralEdge, c: &GateContext| s.compute_gate(e, c));
    let inhibitory_fn: GateFn = Rc::new(inhibitory_gate);
    let mut ctx = GateContext::new();
    ctx.insert("inhibition_strength".into(), json!(0.7));
    let mut got = Vec::new();
    for combo in ["multiply", "min", "max", "mean", "other"] {
        let mut cg = CompositeGate::new(combo);
        cg.add_gate(balanced_fn.clone()).add_gate(inhibitory_fn.clone()).add_gate(strict_fn.clone());
        let values: Vec<f64> = es.iter_mut().map(|e| cg.call(e, &ctx)).collect();
        got.push(json!({"combination": combo, "values": values}));
    }
    let empty_gate = CompositeGate::new("multiply");
    let empty = GateContext::new();
    let values: Vec<f64> = es.iter_mut().take(5).map(|e| empty_gate.call(e, &empty)).collect();
    got.push(json!({"combination": "empty", "values": values}));
    assert_json_eq(&json!(got), &f["composites"], "composite gates");

    let mut ag = AdaptiveGate::new(balanced_fn, 0.1, 0.7);
    let mut got = Vec::new();
    for case in f["adaptive"].as_array().unwrap() {
        let (count, target) = (case["count"].as_i64().unwrap(), case["target"].as_i64().unwrap());
        ag.adapt_threshold(count, target);
        let values: Vec<f64> = es.iter_mut().step_by(5).map(|e| ag.call(e, &empty)).collect();
        got.push(json!({"count": count, "target": target, "threshold": ag.current_threshold(), "values": values}));
    }
    assert_json_eq(&json!(got), &f["adaptive"], "adaptive gate");

    let permissive = Rc::new(create_permissive_registry());
    let p = permissive.clone();
    let ag2 = AdaptiveGate::new(Rc::new(move |e: &mut NeuralEdge, c: &GateContext| p.compute_gate(e, c)), 0.2, 0.4);
    let mut ctx = GateContext::new();
    ctx.insert("similarity_threshold".into(), json!(0.1));
    let values: Vec<f64> = es.iter_mut().map(|e| ag2.call(e, &ctx)).collect();
    assert_json_eq(&json!({"threshold": ag2.current_threshold(), "values": values}), &f["adaptive2"], "adaptive 2");
}
