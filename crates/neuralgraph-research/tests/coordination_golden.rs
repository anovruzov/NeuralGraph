//! Parity of the coordination simulator with the Python (`tests/golden/coordination.json`, written by
//! `gen_coordination.py`): the logical clock, opaque ids, canonical exports of both adapters, the storage lineage
//! resolver, the real adapter's error messages (`str` and `repr`), and the experiment, benchmark, sweep and scale
//! outputs at seeds the pinned artifacts do not cover — compared as JSON and as the exact artifact bytes (SHA256).

mod common;

use common::{assert_json_eq, load};
use neuralgraph_research::coordination::adapters::{
    LocalNode, LocalNodeRef, LocalValue, MemoryNodeAdapter, MockMemoryNodeAdapter, NeuralGraphMemoryAdapter,
    PrivateMemoryRecord,
};
use neuralgraph_research::coordination::contracts::{
    strs, AuthorizationContext, Availability, CapabilityDescriptor, LearningDisposition, PolicyStatus, QueryRequest,
};
use neuralgraph_research::coordination::core::{artifact_json, canonical_json, clock_fn, LogicalClock};
use neuralgraph_research::coordination::experiment::run_experiment;
use neuralgraph_research::coordination::fixture::{build_runtime, REQUIRED_SCOPE};
use neuralgraph_research::coordination::storage_adapter::StorageLineageResolver;
use neuralgraph_research::coordination::{benchmark, scale};
use neuralgraph_research::retrieval::data_types::{edge_ref, node_ref, EdgeType, NeuralEdge, NeuralNode, NodeLayer};
use neuralgraph_research::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::any::Any;
use std::rc::Rc;

fn sha256(text: &str) -> String {
    Sha256::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

#[test]
fn logical_clock_matches() {
    let f = load("coordination.json");
    for case in f["clock"].as_array().unwrap() {
        let clock = LogicalClock::new(case["seed"].as_i64().unwrap());
        if let Some(now) = case.get("now") {
            let got: Vec<String> = (0..3).map(|_| clock.now()).collect();
            assert_eq!(json!(got), *now, "seed {}", case["seed"]);
        } else {
            let ticks: Vec<String> = (0..1001).map(|_| clock.now()).collect();
            assert_eq!(ticks[999], s(&case["tick_999"]));
            assert_eq!(ticks[1000], s(&case["tick_1000"]));
        }
    }
}

#[test]
fn opaque_ids_match() {
    let f = load("coordination.json");
    for case in f["opaque_ids"].as_array().unwrap() {
        let parts: Vec<&str> = case["parts"].as_array().unwrap().iter().map(s).collect();
        assert_eq!(MockMemoryNodeAdapter::opaque_id(s(&case["kind"]), &parts), s(&case["id"]));
    }
}

#[test]
fn mock_exports_and_trace_events_match() {
    let f = load("coordination.json");
    let golden = &f["mock_exports"];
    let runtime = build_runtime(77, "golden").unwrap();
    let query = &runtime.fixture.query;
    let allowed = runtime.registry.adapter_for("node-b").unwrap().query(query).unwrap();
    let denied_request =
        QueryRequest { authorization: AuthorizationContext::with_scopes(&["wrong-scope"]).unwrap(), ..query.clone() };
    let denied = runtime.registry.adapter_for("node-a").unwrap().query(&denied_request).unwrap();
    let record = PrivateMemoryRecord {
        policy_mode: PolicyStatus::Redacted,
        ..PrivateMemoryRecord::new(
            "secret-memory",
            "secret-capability",
            "secret-slot",
            "V",
            0.8,
            strs(&["s"]),
            strs(&["p"]),
            strs(&["r"]),
            strs(&["d"]),
            strs(&["e"]),
            "secret-scope",
        )
    };
    let adapter = MockMemoryNodeAdapter::new(
        "redacted-node",
        vec![record],
        runtime.failure.clone(),
        clock_fn(&runtime.clock),
        LearningDisposition::Rejected,
    );
    let redacted_request = QueryRequest {
        requested_capability: Some("secret-capability".into()),
        authorization: AuthorizationContext::new(strs(&["secret-scope"]), strs(&["redacted-node"]), true).unwrap(),
        ..query.clone()
    };
    let redacted = adapter.query(&redacted_request).unwrap();
    let refused_request = QueryRequest {
        authorization: AuthorizationContext::new(strs(&["secret-scope"]), strs(&["redacted-node"]), false).unwrap(),
        ..redacted_request.clone()
    };
    let refused = adapter.query(&refused_request).unwrap();
    assert_eq!(canonical_json(&allowed), s(&golden["allowed"]));
    assert_eq!(canonical_json(&denied), s(&golden["denied"]));
    assert_eq!(canonical_json(&redacted), s(&golden["redacted"]));
    assert_eq!(canonical_json(&refused), s(&golden["redaction_refused"]));
    assert_eq!(canonical_json(&runtime.traces.events()), s(&golden["trace_events"]));
}

const SESSION: &str = "golden-session";

/// messages -> episodes -> topic, plus a dangling source and a cycle (the generator's `lineage_graph`).
fn lineage_graph() -> Rc<InMemoryNeuralGraphStorage> {
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let node = |id: &str, layer, content: &str, embedding: Option<Vec<f64>>, sources: &[&str], slot: Option<&str>| {
        let mut n = NeuralNode::new(id, layer, content);
        n.embedding = embedding;
        n.session_key = SESSION.into();
        n.source_memory_ids = strs(sources);
        if let Some(slot) = slot {
            n.metadata.insert("slot".into(), json!(slot));
        }
        node_ref(n)
    };
    for i in 0..4 {
        storage.save_node(&node(
            &format!("m{i}"),
            NodeLayer::Message,
            &format!("raw {i}"),
            Some(vec![0.1 * (i + 1) as f64, 0.2, 0.3]),
            &[],
            None,
        ));
    }
    storage.save_node(&node(
        "e1",
        NodeLayer::Episode,
        "episode one",
        Some(vec![0.9, 0.1, 0.05]),
        &["m0", "m1"],
        Some("left"),
    ));
    storage.save_node(&node(
        "e2",
        NodeLayer::Episode,
        "episode two",
        Some(vec![0.7, 0.3, 0.1]),
        &["m2", "gone"],
        Some("right"),
    ));
    storage.save_node(&node("t1", NodeLayer::Topic, "topic", Some(vec![0.5, 0.5, 0.5]), &[], None));
    for (parent, child) in
        [("e1", "m0"), ("e1", "m1"), ("e2", "m2"), ("e2", "m3"), ("t1", "e1"), ("t1", "e2"), ("m3", "e2")]
    {
        storage.save_edge(&edge_ref(NeuralEdge::new(
            format!("edge-{parent}-{child}"),
            parent,
            child,
            EdgeType::Hierarchy,
        )));
    }
    storage.save_edge(&edge_ref(NeuralEdge::new("sem-e1-e2", "e1", "e2", EdgeType::Semantic)));
    storage
}

#[test]
fn storage_lineage_resolver_matches() {
    let f = load("coordination.json");
    let storage = lineage_graph();
    for case in f["lineage"].as_array().unwrap() {
        let resolver = StorageLineageResolver::with_bounds(
            storage.clone(),
            case["max_depth"].as_i64().unwrap(),
            case["max_nodes"].as_i64().unwrap(),
        )
        .unwrap();
        let node = storage.get_node(s(&case["node"])).unwrap();
        let resolved = resolver.resolve(&*node).unwrap();
        let mut got = serde_json::Map::new();
        for (k, v) in resolved.items() {
            got.insert(k.to_string(), json!(v));
        }
        assert_json_eq(&Value::Object(got), &case["resolved"], &format!("{case}"));
    }
}

#[test]
fn real_adapter_exports_match() {
    let f = load("coordination.json");
    let storage = lineage_graph();
    let runtime = build_runtime(78, "golden-real").unwrap();
    let capability = CapabilityDescriptor {
        capability_id: runtime.fixture.query.requested_capability.clone().unwrap(),
        node_id: "real-node".into(),
        description: "golden".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[REQUIRED_SCOPE]),
        availability: Availability::Available,
    };
    let request = QueryRequest {
        authorization: AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&["real-node"]), false).unwrap(),
        ..runtime.fixture.query.clone()
    };
    for (label, policy, resolver, projection) in [
        ("default", PolicyStatus::Allowed, false, false),
        ("resolver", PolicyStatus::Allowed, true, false),
        ("projection", PolicyStatus::Allowed, true, true),
        ("denied", PolicyStatus::Denied, true, false),
    ] {
        let search = storage.clone();
        let mut adapter = NeuralGraphMemoryAdapter::new(
            "real-node",
            capability.clone(),
            Box::new(move |_| {
                Ok(search
                    .vector_search(&[0.8, 0.2, 0.1], SESSION, 8, Some(&[NodeLayer::Episode]))
                    .into_iter()
                    .map(|(n, score)| (n as LocalNodeRef, score))
                    .collect())
            }),
            Box::new(move |_, _| policy),
            clock_fn(&runtime.clock),
        );
        if resolver {
            adapter = adapter.with_lineage_resolver(StorageLineageResolver::new(storage.clone()).into_resolver());
        }
        if projection {
            adapter = adapter.with_claim_projection(Box::new(|node| {
                let LocalValue::Dict(metadata) = node.getattr("metadata").unwrap() else { panic!("mapping") };
                LocalValue::dict(vec![
                    ("slot", LocalValue::str(&metadata.get("slot").unwrap().py_str())),
                    ("value", LocalValue::str(&node.getattr("content").unwrap().py_str())),
                    ("n", LocalValue::int(3)),
                    ("x", LocalValue::float(0.25)),
                    ("ok", LocalValue::Bool(true)),
                ])
            }));
        }
        let exports = adapter.query(&request).unwrap();
        assert_eq!(canonical_json(&exports), s(&f["real_adapter"][label]), "{label}");
    }
}

/// The generator's `Stub` / `NoId` objects.
struct Stub {
    type_name: &'static str,
    node_id: Option<&'static str>,
    source_memory_ids: Vec<&'static str>,
}

impl LocalNode for Stub {
    fn type_name(&self) -> String {
        self.type_name.into()
    }

    fn getattr(&self, name: &str) -> Option<LocalValue> {
        match name {
            "node_id" => self.node_id.map(LocalValue::str),
            "content" => Some(LocalValue::str(if self.type_name == "NoId" { "x" } else { "locally held fact" })),
            "source_memory_ids" if self.type_name == "Stub" => Some(LocalValue::list_of(&self.source_memory_ids)),
            _ => None,
        }
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

fn stub() -> Stub {
    Stub { type_name: "Stub", node_id: Some("local-memory-1"), source_memory_ids: Vec::new() }
}

enum Spec {
    None,
    Resolver(LocalValue),
    Projection(LocalValue),
}

#[test]
fn real_adapter_error_messages_match() {
    let f = load("coordination.json");
    let runtime = build_runtime(79, "golden-errors").unwrap();
    let capability = CapabilityDescriptor {
        capability_id: runtime.fixture.query.requested_capability.clone().unwrap(),
        node_id: "real-node".into(),
        description: "golden".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[REQUIRED_SCOPE]),
        availability: Availability::Available,
    };
    let resolver = |pairs: Vec<(&str, LocalValue)>| Spec::Resolver(LocalValue::dict(pairs));
    let leaky = LocalValue::object("Leaky");
    let cases: Vec<(&str, Stub, Spec)> = vec![
        ("no_node_id", Stub { type_name: "NoId", node_id: None, source_memory_ids: Vec::new() }, Spec::None),
        ("blank_node_id", Stub { node_id: Some("  "), ..stub() }, Spec::None),
        ("resolver_list", stub(), Spec::Resolver(LocalValue::list_of(&["x"]))),
        ("resolver_none", stub(), Spec::Resolver(LocalValue::None)),
        ("bare_string", stub(), resolver(vec![("source_ids", LocalValue::str("memory-42"))])),
        ("bytes", stub(), resolver(vec![("lineage_root_ids", LocalValue::Bytes(b"m".to_vec()))])),
        ("set", stub(), resolver(vec![("edge_path", LocalValue::Set(vec![LocalValue::str("m")]))])),
        ("int_entry", stub(), resolver(vec![("parent_memory_ids", LocalValue::Tuple(vec![LocalValue::int(7)]))])),
        ("object_entry", stub(), resolver(vec![("failure_domains", LocalValue::Tuple(vec![leaky]))])),
        ("blank_entry", stub(), resolver(vec![("parent_memory_ids", LocalValue::tuple_of(&[" "]))])),
        ("operator_int", stub(), resolver(vec![("retrieval_operator", LocalValue::int(7))])),
        ("operator_blank", stub(), resolver(vec![("retrieval_operator", LocalValue::str(""))])),
        ("content_not_dict", stub(), Spec::Projection(LocalValue::str("text"))),
        (
            "content_key",
            stub(),
            Spec::Projection(LocalValue::Dict(neuralgraph_research::coordination::adapters::LocalDict::new(vec![(
                LocalValue::int(7),
                LocalValue::str("v"),
            )]))),
        ),
        (
            "content_value",
            stub(),
            Spec::Projection(LocalValue::dict(vec![("text", LocalValue::List(vec![LocalValue::int(1)]))])),
        ),
        ("content_quote_key", stub(), Spec::Projection(LocalValue::dict(vec![("it's", LocalValue::empty_dict())]))),
        ("content_nan", stub(), Spec::Projection(LocalValue::dict(vec![("score", LocalValue::float(f64::NAN))]))),
        (
            "content_inf",
            stub(),
            Spec::Projection(LocalValue::dict(vec![("score", LocalValue::float(f64::NEG_INFINITY))])),
        ),
        ("stored_lineage", Stub { source_memory_ids: vec![""], ..stub() }, Spec::None),
    ];
    let golden = f["adapter_errors"].as_array().unwrap();
    assert_eq!(golden.len(), cases.len());
    for ((label, node, spec), expected) in cases.into_iter().zip(golden) {
        assert_eq!(label, s(&expected["case"]));
        let node: LocalNodeRef = Rc::new(node);
        let mut adapter = NeuralGraphMemoryAdapter::new(
            "real-node",
            capability.clone(),
            Box::new(move |_| Ok(vec![(node.clone(), 0.9)])),
            Box::new(|_, _| PolicyStatus::Allowed),
            clock_fn(&runtime.clock),
        );
        match spec {
            Spec::None => {}
            Spec::Resolver(value) => adapter = adapter.with_lineage_resolver(Box::new(move |_| Ok(value.clone()))),
            Spec::Projection(value) => adapter = adapter.with_claim_projection(Box::new(move |_| value.clone())),
        }
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert_eq!(error.kind, s(&expected["type"]), "{label}");
        assert_eq!(error.message, s(&expected["str"]), "{label}");
        assert_eq!(error.repr(), s(&expected["repr"]), "{label}");
    }
}

#[test]
fn experiments_at_other_seeds_match() {
    let f = load("coordination.json");
    for case in f["experiments"].as_array().unwrap() {
        let seed = case["seed"].as_i64().unwrap();
        let result = run_experiment(seed).unwrap();
        assert_json_eq(&result, &case["result"], &format!("experiment seed {seed}"));
        assert_eq!(sha256(&artifact_json(&result)), s(&case["sha256"]), "experiment bytes seed {seed}");
    }
}

#[test]
fn benchmark_at_another_seed_matches() {
    let f = load("coordination.json");
    let golden = &f["benchmark"];
    let result = benchmark::run_benchmark(1).unwrap();
    assert_json_eq(&result, &golden["seed1"], "benchmark seed 1");
    assert_eq!(sha256(&artifact_json(&result)), s(&golden["seed1_sha256"]));
    assert_eq!(benchmark::render_markdown(&result), s(&golden["markdown"]["1"]));
}

#[test]
fn single_seed_benchmark_markdown_matches() {
    // `benchmark --format markdown` has no pinned artifact; this pins it for the published seed.
    let f = load("coordination.json");
    let result = benchmark::run_benchmark(20260813).unwrap();
    assert_eq!(benchmark::render_markdown(&result), s(&f["benchmark"]["markdown"]["20260813"]));
}

#[test]
fn short_sweep_matches() {
    let f = load("coordination.json");
    let golden = &f["benchmark"];
    let result = benchmark::run_sweep(5, 3).unwrap();
    assert_json_eq(&result, &golden["sweep_5_3"], "sweep 5x3");
    assert_eq!(sha256(&artifact_json(&result)), s(&golden["sweep_5_3_sha256"]));
    assert_eq!(benchmark::render_sweep_markdown(&result), s(&golden["sweep_5_3_markdown"]));
}

#[test]
fn scale_sweep_at_another_seed_matches() {
    let f = load("coordination.json");
    let golden = &f["scale"];
    let result = scale::run_scale_sweep(11, &scale::default_specs()).unwrap();
    assert_json_eq(&result, &golden["seed11"], "scale seed 11");
    assert_eq!(sha256(&artifact_json(&result)), s(&golden["seed11_sha256"]));
    assert_eq!(scale::render_markdown(&result), s(&golden["seed11_markdown"]));
}

#[test]
fn sweep_of_no_trials_raises_like_python() {
    // `run_sweep(seed, 0)` divided by a zero trial count in Python.
    let error = benchmark::run_sweep(1, 0).unwrap_err();
    assert_eq!((error.kind.as_str(), error.message.as_str()), ("ZeroDivisionError", "division by zero"));
}
