//! Deterministic tests for the Tesseract coordination vertical slice (port of
//! `NeuralGraph/tests/test_coordination.py`, same test names and assertions).
//!
//! The Python attacked the real adapter's boundary with hostile builtin subclasses (a `str` with a lying
//! `__repr__`, an `int` carrying an attribute, a `dict` whose `items()` disagrees with its storage). Those inputs
//! are built here as `LocalValue`s carrying the same overrides, and the assertions are the same: the exported
//! claim holds plain values, and no marker reaches the serialisation, the debug representation or an error.

use neuralgraph_research::coordination::adapters::{
    ClaimProjection, LocalDict, LocalNode, LocalNodeRef, LocalRetrieve, LocalValue, MemoryNodeAdapter,
    MockMemoryNodeAdapter, NeuralGraphMemoryAdapter, PolicyFilter, PrivateMemoryRecord, PySubclass,
};
use neuralgraph_research::coordination::contracts::{
    strs, AuthorizationContext, Availability, CapabilityDescriptor, CoordinationError, InterventionTarget,
    LearningDisposition, LearningSignal, PolicyStatus, QueryRequest, TraceEventType,
};
use neuralgraph_research::coordination::core::{
    canonical_json, clock_fn, py_repr_value, CapabilityRegistry, ClaimNormalizer, LineageAnalyzer, Router,
    RuleBasedSynthesizer, TesseractCoordinator,
};
use neuralgraph_research::coordination::experiment::{run_experiment, run_strategy};
use neuralgraph_research::coordination::fixture::{
    build_runtime, generate_fixture, required_slots, FixtureRuntime, REQUIRED_SCOPE,
};
use serde_json::{json, Value};
use std::any::Any;
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

fn make_coordinator(runtime: &FixtureRuntime) -> TesseractCoordinator {
    TesseractCoordinator::new(
        runtime.registry.clone(),
        Router::new(runtime.registry.clone(), runtime.failure.clone()),
        ClaimNormalizer,
        RuleBasedSynthesizer::new(required_slots()),
        LineageAnalyzer,
        runtime.traces.clone(),
    )
}

fn runtime(seed: i64, strategy: &str) -> FixtureRuntime {
    build_runtime(seed, strategy).expect("fixture runtime")
}

fn adapter(runtime: &FixtureRuntime, node_id: &str) -> Rc<dyn MemoryNodeAdapter> {
    runtime.registry.adapter_for(node_id).expect("registered node")
}

/// `f"{exc}|{exc!r}"`
fn message(e: &CoordinationError) -> String {
    format!("{e}|{}", e.repr())
}

// =================================================================================================================
// AdapterBoundaryTests
// =================================================================================================================

#[test]
fn test_node_can_only_query_its_own_private_records() {
    let runtime = runtime(11, "boundary");
    let exports = adapter(&runtime, "node-a").query(&runtime.fixture.query).unwrap();
    let claims: Vec<_> = exports.iter().flat_map(|e| e.claims.iter()).collect();
    let producers: BTreeSet<&str> = claims.iter().map(|c| c.producer_node_id.as_str()).collect();
    assert_eq!(producers, BTreeSet::from(["node-a"]));
    let slots: BTreeSet<String> = claims.iter().map(|c| c.content["slot"].to_string()).collect();
    assert_eq!(slots, BTreeSet::from([json!("left").to_string()]));
    assert!(!canonical_json(&exports).contains(&runtime.fixture.right_symbol));
}

#[test]
fn test_coordination_export_contains_contracts_not_graph_objects() {
    let runtime = runtime(12, "boundary");
    let execution = make_coordinator(&runtime).execute(&runtime.fixture.query, "boundary", None).unwrap();
    // `isinstance(claim, ClaimEnvelope)` holds by construction: `execution.claims` is a `Vec<ClaimEnvelope>`.
    let payload = canonical_json(&execution.exports);
    assert!(!payload.contains("NeuralNode"));
    assert!(!payload.contains("NeuralEdge"));
    assert!(!payload.contains("embedding"));
}

#[test]
fn test_denied_evidence_is_not_exported() {
    let runtime = runtime(13, "boundary");
    let denied = QueryRequest {
        authorization: AuthorizationContext::with_scopes(&["wrong-scope"]).unwrap(),
        ..runtime.fixture.query.clone()
    };
    let exports = adapter(&runtime, "node-a").query(&denied).unwrap();
    assert!(!exports.is_empty());
    assert!(exports.iter().all(|e| e.claims.is_empty()));
    assert!(exports.iter().all(|e| e.trace.policy_status == PolicyStatus::Denied));
}

#[test]
fn test_redaction_does_not_leak_content_or_trace_metadata() {
    let runtime = runtime(14, "boundary");
    let protected = "PROTECTED_VALUE_9YQ";
    let record = PrivateMemoryRecord {
        policy_mode: PolicyStatus::Redacted,
        ..PrivateMemoryRecord::new(
            "secret-memory",
            "secret-capability",
            "secret-slot",
            protected,
            0.8,
            strs(&["secret-source"]),
            strs(&["secret-parent"]),
            strs(&["secret-root"]),
            strs(&["secret-domain"]),
            strs(&["secret-edge"]),
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
    let request = QueryRequest {
        requested_capability: Some("secret-capability".into()),
        authorization: AuthorizationContext::new(strs(&["secret-scope"]), strs(&["redacted-node"]), true).unwrap(),
        ..runtime.fixture.query.clone()
    };
    let exports = adapter.query(&request).unwrap();
    let payload = canonical_json(&exports);
    for secret in
        [protected, "secret-memory", "secret-source", "secret-parent", "secret-root", "secret-domain", "secret-edge"]
    {
        assert!(!payload.contains(secret), "{secret}");
    }
    assert_eq!(exports[0].claims[0].content["value"], "[REDACTED]");
    assert!(exports[0].trace.memory_ids.is_empty());
}

#[test]
fn test_learning_requires_receiver_decision_and_never_auto_commits() {
    let runtime = runtime(15, "boundary");
    let signal = LearningSignal {
        signal_id: "signal-1".into(),
        sender_node_id: "node-a".into(),
        receiver_node_id: "node-d".into(),
        proposed_content: json!({"candidate": "value"}),
        evidence_refs: strs(&["evidence-1"]),
    };
    let decision = adapter(&runtime, "node-d").propose_learning(&signal).unwrap();
    assert_eq!(decision.disposition, LearningDisposition::Tentative);
    assert!(decision.committed_memory_id.is_none());
    let mismatch = adapter(&runtime, "node-a").propose_learning(&signal).unwrap();
    assert_eq!(mismatch.disposition, LearningDisposition::Rejected);
}

#[test]
fn test_real_adapter_does_not_invent_missing_lineage() {
    let runtime = runtime(16, "boundary");
    let node: LocalNodeRef = Rc::new(LocalNodeStub {
        source_memory_ids: LocalValue::list_of(&["known-source-memory"]),
        ..LocalNodeStub::default()
    });
    let capability = CapabilityDescriptor {
        capability_id: runtime.fixture.query.requested_capability.clone().unwrap(),
        node_id: "real-node".into(),
        description: "local NeuralGraph retrieval".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[REQUIRED_SCOPE]),
        availability: Availability::Available,
    };
    let adapter = NeuralGraphMemoryAdapter::new(
        "real-node",
        capability,
        retrieve_all(vec![node], 0.88),
        allow_everything(),
        clock_fn(&runtime.clock),
    );
    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let claim = &exports[0].claims[0];
    assert_eq!(claim.source_ids, strs(&["known-source-memory"]));
    assert!(claim.lineage_root_ids.is_empty());
    assert!(claim.failure_domains.is_empty());
    assert!(exports[0].trace.edge_path.is_empty());
}

// =================================================================================================================
// FailureAndLineageTests
// =================================================================================================================

#[test]
fn test_correlated_copies_collapse_to_one_independent_root_coalition() {
    let runtime = runtime(21, "lineage");
    let execution = make_coordinator(&runtime).execute(&runtime.fixture.query, "before", None).unwrap();
    assert_eq!(execution.metrics.apparent_replica_count, 3);
    assert_eq!(execution.metrics.unique_lineage_root_count, 2);
    assert_eq!(execution.metrics.reconstruction_coalition_count, 1);
    assert_eq!(execution.metrics.minimal_support_coalition_size, 2);
    assert_eq!(execution.metrics.minimal_failure_domain_cut, 1);
}

#[test]
fn test_independent_root_remains_distinct() {
    let runtime = runtime(22, "lineage");
    let execution = make_coordinator(&runtime)
        .execute(&runtime.fixture.query, "independent", Some(&strs(&["node-a", "node-c", "node-d"])))
        .unwrap();
    assert_eq!(execution.metrics.unique_lineage_root_count, 3);
    assert_eq!(execution.metrics.unique_failure_domain_count, 3);
    assert_eq!(execution.metrics.reconstruction_coalition_count, 2);
}

#[test]
fn test_removing_shared_root_invalidates_every_dependent_copy() {
    let runtime = runtime(23, "lineage");
    runtime.failure.apply(InterventionTarget::LineageRoot, "R2", "test", None).unwrap();
    let execution = make_coordinator(&runtime).execute(&runtime.fixture.query, "failed", None).unwrap();
    let roots: BTreeSet<&str> =
        execution.claims.iter().flat_map(|c| c.lineage_root_ids.iter().map(String::as_str)).collect();
    assert_eq!(roots, BTreeSet::from(["R1"]));
    assert!(!execution.synthesis.success);
    let blocked: BTreeSet<String> = runtime
        .traces
        .events()
        .iter()
        .filter(|e| e.event_type == TraceEventType::PolicyBlock)
        .map(|e| e.details["node_id"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(blocked, BTreeSet::from(["node-b".to_string(), "node-c".to_string()]));
}

#[test]
fn test_all_failure_toggles_are_reversible_and_non_destructive() {
    let cases = [
        (InterventionTarget::Node, "node-b"),
        (InterventionTarget::Memory, "memory-b"),
        (InterventionTarget::Source, "source-2"),
        (InterventionTarget::LineageRoot, "R2"),
        (InterventionTarget::Edge, "edge-b-right"),
        (InterventionTarget::AuthorizationScope, REQUIRED_SCOPE),
        (InterventionTarget::Evidence, "memory-b"),
    ];
    for (index, (target_type, target_id)) in cases.into_iter().enumerate() {
        let runtime = runtime(30 + index as i64, "failure");
        let node_b = adapter(&runtime, "node-b");
        runtime.failure.apply(target_type, target_id, "test", None).unwrap();
        let blocked = node_b.query(&runtime.fixture.query).unwrap();
        assert!(blocked.iter().all(|e| e.claims.is_empty()), "{target_type:?}");
        runtime.failure.clear(target_type, target_id);
        let restored = node_b.query(&runtime.fixture.query).unwrap();
        assert!(restored.iter().any(|e| !e.claims.is_empty()), "{target_type:?}");
        assert!(runtime.failure.records()[0].ended_at.is_some());
    }

    let runtime = runtime(39, "route");
    runtime.failure.apply(InterventionTarget::Route, "node-b", "test", None).unwrap();
    let route =
        Router::new(runtime.registry.clone(), runtime.failure.clone()).select(&runtime.fixture.query, None).unwrap();
    assert!(!route.contains(&"node-b".to_string()));
    runtime.failure.clear(InterventionTarget::Route, "node-b");
    let restored_route =
        Router::new(runtime.registry.clone(), runtime.failure.clone()).select(&runtime.fixture.query, None).unwrap();
    assert!(restored_route.contains(&"node-b".to_string()));
}

// =================================================================================================================
// EndToEndExperimentTests
// =================================================================================================================

#[test]
fn test_success_loss_and_independent_recovery() {
    let result = run_strategy(20260813, "lineage_targeted_repair").unwrap();
    let phases = &result["phases"];
    assert_eq!(phases["before_failure"]["synthesis"]["success"], true);
    assert_eq!(phases["after_failure"]["synthesis"]["success"], false);
    assert_eq!(phases["after_repair"]["synthesis"]["success"], true);
    assert_eq!(result["repair"]["selected_node_id"], "node-d");
    let verifications = &result["repair"]["verification_results"];
    assert_eq!(verifications[0]["valid"], false);
    assert_eq!(verifications[0]["lineage_root_ids"], json!(["R2"]));
    assert_eq!(verifications[0]["reason"], "correlated support");
    assert_eq!(verifications[1]["lineage_root_ids"], json!(["R3"]));
    assert!(result["privacy_assertions"].as_object().unwrap().values().all(|v| *v == true));
}

#[test]
fn test_replica_count_repair_does_not_restore_capability() {
    let result = run_strategy(20260813, "replica_count").unwrap();
    assert!(result["repair"]["selected_node_id"].is_null());
    assert_eq!(result["phases"]["after_repair"]["synthesis"]["success"], false);
    assert!(result["repair"]["verification_results"]
        .as_array()
        .unwrap()
        .iter()
        .all(|v| v["lineage_root_ids"] == json!(["R2"])));
}

#[test]
fn test_trace_explains_route_failure_selection_and_repair() {
    let result = run_strategy(55, "lineage_targeted_repair").unwrap();
    let events = result["trace_events"].as_array().unwrap();
    let types: Vec<&str> = events.iter().map(|e| e["event_type"].as_str().unwrap()).collect();
    for expected in [
        "route_selected",
        "failure_injected",
        "policy_block",
        "verification_requested",
        "verification_result",
        "repair_completed",
        "capability_evaluated",
    ] {
        assert!(types.contains(&expected), "{expected}");
    }
    let failure = events.iter().find(|e| e["event_type"] == "failure_injected").unwrap();
    assert_eq!(failure["details"]["target_id"], "R2");
    let repaired = events.iter().find(|e| e["event_type"] == "repair_completed").unwrap();
    assert_eq!(repaired["details"]["selected_node_id"], "node-d");
}

#[test]
fn test_same_seed_replays_exactly() {
    let first = run_experiment(101).unwrap();
    let second = run_experiment(101).unwrap();
    assert_eq!(canonical_json(&first), canonical_json(&second));
}

#[test]
fn test_different_seeds_rotate_symbols_but_preserve_structure() {
    let first = generate_fixture(101, "seed").unwrap();
    let second = generate_fixture(102, "seed").unwrap();
    assert_ne!(first.expected_answer, second.expected_answer);
    assert_eq!(first.records_by_node.keys().collect::<Vec<_>>(), second.records_by_node.keys().collect::<Vec<_>>());
    let one = run_experiment(101).unwrap();
    let two = run_experiment(102).unwrap();
    assert_eq!(one["strategy_summary"], two["strategy_summary"]);
    assert!(!first.query.content.contains(&first.left_symbol));
    assert!(!first.query.content.contains(&first.right_symbol));
}

// =================================================================================================================
// Real-adapter helpers
// =================================================================================================================

const REAL_NODE_ID: &str = "real-node";

/// Stand-in for a locally owned NeuralGraph node: the attribute names the adapter reads from a real `NeuralNode`
/// (`node_id`, `content`, `source_memory_ids`) plus the two payload fields that must never cross the boundary.
#[derive(Clone, Debug)]
struct LocalNodeStub {
    node_id: LocalValue,
    content: LocalValue,
    source_memory_ids: LocalValue,
    metadata: LocalValue,
    embedding: LocalValue,
}

impl Default for LocalNodeStub {
    fn default() -> Self {
        LocalNodeStub {
            node_id: LocalValue::str("local-memory-1"),
            content: LocalValue::str("locally held fact"),
            source_memory_ids: LocalValue::List(Vec::new()),
            metadata: LocalValue::empty_dict(),
            embedding: LocalValue::List(Vec::new()),
        }
    }
}

impl LocalNodeStub {
    fn new(node_id: &str, content: &str) -> Self {
        LocalNodeStub { node_id: LocalValue::str(node_id), content: LocalValue::str(content), ..Self::default() }
    }

    fn with_metadata_and_embedding(metadata: LocalValue, embedding: &[f64]) -> Self {
        LocalNodeStub {
            metadata,
            embedding: LocalValue::List(embedding.iter().map(|x| LocalValue::float(*x)).collect()),
            ..Self::default()
        }
    }
}

impl LocalNode for LocalNodeStub {
    fn type_name(&self) -> String {
        "LocalNodeStub".into()
    }

    fn getattr(&self, name: &str) -> Option<LocalValue> {
        match name {
            "node_id" => Some(self.node_id.clone()),
            "content" => Some(self.content.clone()),
            "source_memory_ids" => Some(self.source_memory_ids.clone()),
            "metadata" => Some(self.metadata.clone()),
            "embedding" => Some(self.embedding.clone()),
            _ => None,
        }
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

/// Locally retrieved object without the identifier the adapter requires.
struct NotAMemoryNode;

impl LocalNode for NotAMemoryNode {
    fn type_name(&self) -> String {
        "NotAMemoryNode".into()
    }

    fn getattr(&self, name: &str) -> Option<LocalValue> {
        (name == "content").then(|| LocalValue::str("malformed payload MUST_NOT_LEAK_2F8"))
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

fn allow_everything() -> PolicyFilter {
    Box::new(|_, _| PolicyStatus::Allowed)
}

fn policy_of(status: PolicyStatus) -> PolicyFilter {
    Box::new(move |_, _| status)
}

fn make_real_capability(runtime: &FixtureRuntime) -> CapabilityDescriptor {
    CapabilityDescriptor {
        capability_id: runtime.fixture.query.requested_capability.clone().unwrap(),
        node_id: REAL_NODE_ID.into(),
        description: "local NeuralGraph retrieval".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[REQUIRED_SCOPE]),
        availability: Availability::Available,
    }
}

fn make_real_adapter(
    runtime: &FixtureRuntime,
    retrieve: LocalRetrieve,
    policy: PolicyFilter,
) -> NeuralGraphMemoryAdapter {
    NeuralGraphMemoryAdapter::new(
        REAL_NODE_ID,
        make_real_capability(runtime),
        retrieve,
        policy,
        clock_fn(&runtime.clock),
    )
}

fn retrieve_all(nodes: Vec<LocalNodeRef>, confidence: f64) -> LocalRetrieve {
    Box::new(move |_| Ok(nodes.iter().map(|n| (n.clone(), confidence)).collect()))
}

fn stub_retrieve(stub: LocalNodeStub) -> LocalRetrieve {
    retrieve_all(vec![Rc::new(stub)], 0.9)
}

fn subclass(type_name: &str, repr: &str, attribute: (&str, &str)) -> Option<Box<PySubclass>> {
    Some(Box::new(PySubclass {
        type_name: type_name.into(),
        repr: Some(repr.into()),
        attributes: vec![(attribute.0.into(), attribute.1.into())],
        ..PySubclass::default()
    }))
}

/// `HostileReprKey(value)`: `str` subclass smuggling payload through `repr` and an attribute.
fn hostile_repr_key(value: &str) -> LocalValue {
    LocalValue::Str(
        value.into(),
        subclass("HostileReprKey", "SECRET_FROM_REPR_9Z", ("smuggled", "SECRET_ON_KEY_ATTR_4T")),
    )
}

/// `HostileReprValue(value)`
fn hostile_repr_value(value: &str) -> LocalValue {
    LocalValue::Str(
        value.into(),
        subclass("HostileReprValue", "SECRET_FROM_VALUE_REPR_1M", ("smuggled", "SECRET_ON_VALUE_ATTR_6R")),
    )
}

/// `HostileReprInt(value)`
fn hostile_repr_int(value: i64) -> LocalValue {
    LocalValue::Int(value, subclass("HostileReprInt", "SECRET_FROM_INT_REPR_7K", ("hidden", "SECRET_ON_INT_ATTR_3N")))
}

/// `HostileReprFloat(value)`
fn hostile_repr_float(value: f64) -> LocalValue {
    LocalValue::Float(
        value,
        subclass("HostileReprFloat", "SECRET_FROM_FLOAT_REPR_2H", ("hidden", "SECRET_ON_FLOAT_ATTR_5W")),
    )
}

fn projection(f: impl Fn(&dyn LocalNode) -> LocalValue + 'static) -> ClaimProjection {
    Box::new(f)
}

fn content_of(node: &dyn LocalNode) -> String {
    node.getattr("content").unwrap().py_str()
}

// =================================================================================================================
// RealAdapterBoundaryTests
// =================================================================================================================

#[test]
fn test_query_for_another_capability_retrieves_and_exports_nothing() {
    let runtime = runtime(41, "real-adapter");
    let calls: Rc<RefCell<Vec<String>>> = Rc::default();
    let recorded = calls.clone();
    let retrieve: LocalRetrieve = Box::new(move |request| {
        recorded.borrow_mut().push(request.query_id.clone());
        Ok(vec![(Rc::new(LocalNodeStub::default()) as LocalNodeRef, 0.9)])
    });
    let adapter = make_real_adapter(&runtime, retrieve, allow_everything());
    for requested in [Some("some-other-capability".to_string()), None] {
        let mismatched = QueryRequest { requested_capability: requested.clone(), ..runtime.fixture.query.clone() };
        assert!(adapter.query(&mismatched).unwrap().is_empty(), "{requested:?}");
    }
    assert!(calls.borrow().is_empty());
    let matched = adapter.query(&runtime.fixture.query).unwrap();
    assert_eq!(matched.len(), 1);
    assert_eq!(*calls.borrow(), vec![runtime.fixture.query.query_id.clone()]);
}

#[test]
fn test_capability_is_unavailable_to_an_unauthorized_context() {
    let runtime = runtime(42, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything());
    let capability = make_real_capability(&runtime);
    let authorized = AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&[REAL_NODE_ID]), false).unwrap();
    let unauthorized = AuthorizationContext::with_scopes(&["some-other-scope"]).unwrap();
    let wrong_node = AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&["node-a"]), false).unwrap();

    assert_eq!(adapter.describe_capabilities(&authorized).unwrap(), vec![capability.clone()]);
    for context in [unauthorized, wrong_node] {
        let descriptors = adapter.describe_capabilities(&context).unwrap();
        assert_eq!(descriptors.len(), 1);
        assert_eq!(descriptors[0].availability, Availability::Unavailable);
        assert_eq!(descriptors[0].capability_id, capability.capability_id);
        assert_eq!(descriptors[0].policy_scope, capability.policy_scope);
    }
}

#[test]
fn test_router_excludes_a_node_that_is_unavailable_to_the_requester() {
    let runtime = runtime(43, "real-adapter");
    let registry = Rc::new(CapabilityRegistry::new());
    registry
        .register(Rc::new(make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())))
        .unwrap();
    let router = Router::new(registry, runtime.failure.clone());
    let authorized = QueryRequest {
        authorization: AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&[REAL_NODE_ID]), false).unwrap(),
        ..runtime.fixture.query.clone()
    };
    let unauthorized = QueryRequest {
        authorization: AuthorizationContext::with_scopes(&["some-other-scope"]).unwrap(),
        ..runtime.fixture.query.clone()
    };

    assert_eq!(router.select(&authorized, None).unwrap(), strs(&[REAL_NODE_ID]));
    assert!(router.select(&unauthorized, None).unwrap().is_empty());
}

#[test]
fn test_trace_id_stays_bound_to_its_memory_under_reversed_order() {
    let runtime = runtime(44, "real-adapter");
    let first: LocalNodeRef = Rc::new(LocalNodeStub::new("local-memory-1", "fact one"));
    let second: LocalNodeRef = Rc::new(LocalNodeStub::new("local-memory-2", "fact two"));
    let retrieved: Rc<RefCell<Vec<(LocalNodeRef, f64)>>> = Rc::new(RefCell::new(vec![(first, 0.91), (second, 0.82)]));
    let source = retrieved.clone();
    let adapter = make_real_adapter(&runtime, Box::new(move |_| Ok(source.borrow().clone())), allow_everything());
    let forward = adapter.query(&runtime.fixture.query).unwrap();
    retrieved.borrow_mut().reverse();
    let backward = adapter.query(&runtime.fixture.query).unwrap();

    let binding = |exports: &[neuralgraph_research::coordination::contracts::EvidenceExport]| {
        exports.iter().map(|e| (e.trace.memory_ids[0].clone(), e.trace.trace_id.clone())).collect::<HashMap<_, _>>()
    };
    let forward_binding = binding(&forward);
    let backward_binding = binding(&backward);
    let mut keys: Vec<&String> = forward_binding.keys().collect();
    keys.sort();
    assert_eq!(keys, vec!["local-memory-1", "local-memory-2"]);
    assert_eq!(forward_binding.values().collect::<BTreeSet<_>>().len(), 2);
    assert_eq!(forward_binding, backward_binding);
}

#[test]
fn test_trace_id_is_memory_bound_on_allowed_and_denied_paths() {
    let runtime = runtime(45, "real-adapter");
    let node = LocalNodeStub::new("local-memory-1", "fact one");
    let expected =
        MockMemoryNodeAdapter::opaque_id("trace", &[&runtime.fixture.query.query_id, REAL_NODE_ID, "local-memory-1"]);

    let allowed = make_real_adapter(&runtime, stub_retrieve(node.clone()), allow_everything())
        .query(&runtime.fixture.query)
        .unwrap();
    let denied = make_real_adapter(&runtime, stub_retrieve(node), policy_of(PolicyStatus::Denied))
        .query(&runtime.fixture.query)
        .unwrap();

    assert_eq!(allowed[0].trace.trace_id, expected);
    assert_eq!(denied[0].trace.trace_id, expected);
}

#[test]
fn test_denied_export_leaks_neither_content_nor_identifiers() {
    let runtime = runtime(46, "real-adapter");
    let node = LocalNodeStub {
        node_id: LocalValue::str("PRIVATE_MEMORY_ID_4KQ"),
        content: LocalValue::str("PRIVATE_CONTENT_8ZR"),
        source_memory_ids: LocalValue::list_of(&["PRIVATE_SOURCE_1WB"]),
        metadata: LocalValue::dict(vec![("note", LocalValue::str("PRIVATE_METADATA_5XT"))]),
        embedding: LocalValue::List(vec![LocalValue::float(0.9876543), LocalValue::float(0.1234567)]),
    };
    let adapter = make_real_adapter(&runtime, stub_retrieve(node), policy_of(PolicyStatus::Denied));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let payload = canonical_json(&exports);

    for secret in
        ["PRIVATE_MEMORY_ID_4KQ", "PRIVATE_CONTENT_8ZR", "PRIVATE_SOURCE_1WB", "PRIVATE_METADATA_5XT", "0.9876543"]
    {
        assert!(!payload.contains(secret), "{secret}");
    }
    let trace = &exports[0].trace;
    assert!(exports[0].claims.is_empty());
    assert_eq!(trace.policy_status, PolicyStatus::Denied);
    for ids in
        [&trace.memory_ids, &trace.source_ids, &trace.parent_memory_ids, &trace.lineage_root_ids, &trace.edge_path]
    {
        assert!(ids.is_empty());
    }
}

#[test]
fn test_redacted_policy_fails_closed_without_a_claim() {
    // Documents current behaviour: only ALLOWED produces a claim.
    let runtime = runtime(47, "real-adapter");
    let node = LocalNodeStub { content: LocalValue::str("PRIVATE_CONTENT_8ZR"), ..LocalNodeStub::default() };
    let adapter = make_real_adapter(&runtime, stub_retrieve(node), policy_of(PolicyStatus::Redacted));

    let exports = adapter.query(&runtime.fixture.query).unwrap();

    assert!(exports[0].claims.is_empty());
    assert_eq!(exports[0].trace.policy_status, PolicyStatus::Redacted);
    assert!(exports[0].trace.memory_ids.is_empty());
    assert!(!canonical_json(&exports).contains("PRIVATE_CONTENT_8ZR"));
}

#[test]
fn test_claim_projection_may_not_export_raw_node_state() {
    let runtime = runtime(48, "real-adapter");
    let secret = "PROTECTED_VALUE_7KQ";
    let node = LocalNodeStub::with_metadata_and_embedding(
        LocalValue::dict(vec![("note", LocalValue::str(secret))]),
        &[0.9876543, 0.1234567],
    );
    let projections: Vec<(&str, ClaimProjection)> = vec![
        (
            "metadata",
            projection(|n| {
                LocalValue::dict(vec![
                    ("text", LocalValue::str(&content_of(n))),
                    ("metadata", n.getattr("metadata").unwrap()),
                ])
            }),
        ),
        (
            "embedding",
            projection(|n| {
                LocalValue::dict(vec![
                    ("text", LocalValue::str(&content_of(n))),
                    ("embedding", n.getattr("embedding").unwrap()),
                ])
            }),
        ),
        ("node", projection(|n| LocalValue::dict(vec![("node", LocalValue::object(&n.type_name()))]))),
    ];

    for (key, projection) in projections {
        let adapter = make_real_adapter(&runtime, stub_retrieve(node.clone()), allow_everything())
            .with_claim_projection(projection);
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("ValueError"));
        let message = message(&error);
        assert!(message.contains(key), "{message}");
        for leak in [secret, "0.9876543", "locally held fact", "note"] {
            assert!(!message.contains(leak), "{leak} in {message}");
        }
    }
}

#[test]
fn test_claim_content_must_be_a_flat_mapping_of_exportable_scalars() {
    let runtime = runtime(49, "real-adapter");
    let node = LocalNodeStub {
        metadata: LocalValue::dict(vec![("note", LocalValue::str("PROTECTED_VALUE_7KQ"))]),
        ..LocalNodeStub::default()
    };

    type Configure = Box<dyn Fn(NeuralGraphMemoryAdapter) -> NeuralGraphMemoryAdapter>;
    let rejected: Vec<(&str, Configure)> = vec![
        ("not_a_dict", Box::new(|a| a.with_claim_projection(projection(|n| LocalValue::str(&content_of(n)))))),
        (
            "non_string_key",
            Box::new(|a| {
                a.with_claim_projection(projection(|n| {
                    LocalValue::Dict(LocalDict::new(vec![(
                        LocalValue::object(&n.type_name()),
                        LocalValue::str("value"),
                    )]))
                }))
            }),
        ),
        (
            "nested_mapping",
            Box::new(|a| {
                a.with_claim_projection(projection(|_| {
                    LocalValue::dict(vec![("nested", LocalValue::dict(vec![("a", LocalValue::int(1))]))])
                }))
            }),
        ),
        (
            "resolver_supplied",
            Box::new(|a| {
                a.with_lineage_resolver(Box::new(|local_node| {
                    Ok(LocalValue::dict(vec![(
                        "claim_content",
                        LocalValue::dict(vec![("blob", local_node.getattr("metadata").unwrap())]),
                    )]))
                }))
            }),
        ),
    ];
    for (case, configure) in rejected {
        let adapter = configure(make_real_adapter(&runtime, stub_retrieve(node.clone()), allow_everything()));
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("ValueError"), "{case}: {error:?}");
        let message = message(&error);
        assert!(!message.contains("PROTECTED_VALUE_7KQ"), "{case}");
        assert!(!message.contains("locally held fact"), "{case}");
    }

    let accepted = || {
        LocalValue::dict(vec![
            ("text", LocalValue::str("value")),
            ("count", LocalValue::int(2)),
            ("score", LocalValue::float(0.5)),
            ("flag", LocalValue::Bool(true)),
            ("absent", LocalValue::None),
        ])
    };
    let adapter = make_real_adapter(&runtime, stub_retrieve(node), allow_everything())
        .with_claim_projection(projection(move |_| accepted()));
    let exports = adapter.query(&runtime.fixture.query).unwrap();
    assert_eq!(
        exports[0].claims[0].content,
        json!({"text": "value", "count": 2, "score": 0.5, "flag": true, "absent": null})
    );
}

#[test]
fn test_default_claim_content_is_the_unchanged_text_projection() {
    let runtime = runtime(50, "real-adapter");
    let node = LocalNodeStub { content: LocalValue::str("locally held fact"), ..LocalNodeStub::default() };

    let exports =
        make_real_adapter(&runtime, stub_retrieve(node), allow_everything()).query(&runtime.fixture.query).unwrap();

    assert_eq!(exports[0].claims[0].content, json!({"text": "locally held fact"}));
}

#[test]
fn test_claim_projection_replaces_the_default_content() {
    let runtime = runtime(51, "real-adapter");
    let node = LocalNodeStub { content: LocalValue::str("locally held fact"), ..LocalNodeStub::default() };
    let adapter =
        make_real_adapter(&runtime, stub_retrieve(node), allow_everything()).with_claim_projection(projection(|n| {
            let text = content_of(n);
            LocalValue::dict(vec![
                ("slot", LocalValue::str("left")),
                ("value", LocalValue::str(neuralgraph_memory::pycompat::py_prefix(&text, 7))),
            ])
        }));

    let exports = adapter.query(&runtime.fixture.query).unwrap();

    assert_eq!(exports[0].claims[0].content, json!({"slot": "left", "value": "locally"}));
}

#[test]
fn test_async_lineage_resolver_is_awaited_and_omissions_stay_empty() {
    let runtime = runtime(52, "real-adapter");
    let node =
        LocalNodeStub { source_memory_ids: LocalValue::list_of(&["known-source-memory"]), ..LocalNodeStub::default() };
    let resolved: Rc<RefCell<Vec<String>>> = Rc::default();
    let seen = resolved.clone();
    let adapter = make_real_adapter(&runtime, stub_retrieve(node), allow_everything()).with_lineage_resolver(Box::new(
        move |local_node| {
            seen.borrow_mut().push(local_node.getattr("node_id").unwrap().py_str());
            Ok(LocalValue::dict(vec![
                ("lineage_root_ids", LocalValue::tuple_of(&["root-1"])),
                ("edge_path", LocalValue::tuple_of(&["edge-1"])),
            ]))
        },
    ));
    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let claim = &exports[0].claims[0];

    assert_eq!(*resolved.borrow(), vec!["local-memory-1".to_string()]);
    assert_eq!(claim.lineage_root_ids, strs(&["root-1"]));
    assert_eq!(exports[0].trace.edge_path, strs(&["edge-1"]));
    assert_eq!(claim.source_ids, strs(&["known-source-memory"]));
    assert!(claim.parent_memory_ids.is_empty());
    assert!(claim.failure_domains.is_empty());
}

#[test]
fn test_object_without_node_id_is_rejected_without_leaking_payload() {
    let runtime = runtime(53, "real-adapter");
    for (name, policy) in [("allowed", allow_everything()), ("denied", policy_of(PolicyStatus::Denied))] {
        let adapter = make_real_adapter(&runtime, retrieve_all(vec![Rc::new(NotAMemoryNode)], 0.9), policy);
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("TypeError"), "{name}");
        let message = message(&error);
        assert!(message.contains("NotAMemoryNode"), "{name}: {message}");
        assert!(!message.contains("MUST_NOT_LEAK_2F8"), "{name}");
    }
}

// =================================================================================================================
// RealAdapterProjectionIntegrityTests
// =================================================================================================================

/// `DivergentViewContent`: dict subclass whose `items()` view disagrees with its stored pairs.
fn divergent_view_content() -> LocalValue {
    LocalValue::Dict(LocalDict {
        type_name: "DivergentViewContent".into(),
        entries: vec![
            (
                LocalValue::str("embedding"),
                LocalValue::List(vec![LocalValue::float(0.9876543), LocalValue::float(0.1234567)]),
            ),
            (LocalValue::str("metadata_note"), LocalValue::str("PROTECTED_VALUE_7KQ")),
        ],
        items_view: Some(vec![(LocalValue::str("text"), LocalValue::str("sanitized projection"))]),
    })
}

#[test]
fn test_exported_content_is_the_validated_view_not_hidden_storage() {
    let runtime = runtime(54, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| divergent_view_content()));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = &exports[0].claims[0].content;
    let payload = canonical_json(&exports);

    assert_eq!(*content, json!({"text": "sanitized projection"}));
    // `type(content) is dict`: the exported content is a plain JSON object.
    assert!(content.is_object());
    for leak in ["PROTECTED_VALUE_7KQ", "0.9876543", "0.1234567", "embedding", "metadata_note"] {
        assert!(!payload.contains(leak), "{leak}");
    }
}

#[test]
fn test_rejection_message_ignores_a_caller_controlled_key_repr() {
    let runtime = runtime(55, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| {
            LocalValue::Dict(LocalDict::new(vec![(hostile_repr_key("text"), LocalValue::object("object"))]))
        }));

    let error = adapter.query(&runtime.fixture.query).unwrap_err();
    assert!(error.is("ValueError"));
    let message = message(&error);
    assert!(!message.contains("SECRET_FROM_REPR_9Z"));
    assert!(message.contains("'text'"), "{message}");
    assert!(message.contains("object"), "{message}");
}

#[test]
fn test_exported_keys_are_plain_strings_carrying_no_hidden_state() {
    let runtime = runtime(55, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| {
            LocalValue::Dict(LocalDict::new(vec![(hostile_repr_key("text"), LocalValue::str("sanitized"))]))
        }));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = exports[0].claims[0].content.as_object().unwrap();
    let keys: Vec<&String> = content.keys().collect();

    assert_eq!(Value::Object(content.clone()), json!({"text": "sanitized"}));
    // `type(exported_key) is str` and it carries no `smuggled` attribute: a JSON key is a plain String.
    assert_eq!(keys, vec!["text"]);
    assert!(!canonical_json(&exports).contains("SECRET_ON_KEY_ATTR_4T"));
}

#[test]
fn test_blank_node_identifier_is_rejected_like_a_missing_one() {
    // A blank id would give two distinct memories one claim_id and trace_id.
    let runtime = runtime(56, "real-adapter");
    for (label, node_id) in [("empty", ""), ("whitespace", "   ")] {
        let adapter = make_real_adapter(
            &runtime,
            stub_retrieve(LocalNodeStub::new(node_id, "BLANK_ID_CONTENT_3JX")),
            allow_everything(),
        );
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("TypeError"), "{label}");
        let message = message(&error);
        assert!(message.contains("LocalNodeStub"), "{label}: {message}");
        assert!(!message.contains("BLANK_ID_CONTENT_3JX"), "{label}");
    }
}

// =================================================================================================================
// RealAdapterValueBoundaryTests
// =================================================================================================================

fn coordinated_real_run(
    runtime: &FixtureRuntime,
    content: impl Fn() -> LocalValue + 'static,
) -> neuralgraph_research::coordination::core::QueryExecution {
    let request = QueryRequest {
        authorization: AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&[REAL_NODE_ID]), false).unwrap(),
        ..runtime.fixture.query.clone()
    };
    let registry = Rc::new(CapabilityRegistry::new());
    registry
        .register(Rc::new(
            make_real_adapter(runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
                .with_claim_projection(projection(move |_| content())),
        ))
        .unwrap();
    let coordinator = TesseractCoordinator::new(
        registry.clone(),
        Router::new(registry, runtime.failure.clone()),
        ClaimNormalizer,
        RuleBasedSynthesizer::new(required_slots()),
        LineageAnalyzer,
        runtime.traces.clone(),
    );
    coordinator.execute(&request, "real-adapter", None).unwrap()
}

#[test]
fn test_exported_values_are_plain_strings_carrying_no_hidden_state() {
    let runtime = runtime(57, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| LocalValue::dict(vec![("value", hostile_repr_value("sanitized"))])));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = &exports[0].claims[0].content;

    assert_eq!(*content, json!({"value": "sanitized"}));
    assert!(content["value"].is_string());
    for marker in ["SECRET_ON_VALUE_ATTR_6R", "SECRET_FROM_VALUE_REPR_1M"] {
        assert!(!canonical_json(&exports).contains(marker));
        assert!(!format!("{exports:?}").contains(marker));
    }
}

#[test]
fn test_hostile_value_repr_cannot_leak_through_a_coordinated_run() {
    let runtime = runtime(57, "real-adapter");
    let execution = coordinated_real_run(&runtime, || {
        LocalValue::dict(vec![("slot", LocalValue::str("left")), ("value", hostile_repr_value("sanitized"))])
    });
    let value = &execution.claims[0].content["value"];

    assert_eq!(*value, json!("sanitized"));
    for marker in ["SECRET_ON_VALUE_ATTR_6R", "SECRET_FROM_VALUE_REPR_1M"] {
        assert!(!format!("{execution:?}").contains(marker));
        assert!(!canonical_json(&execution).contains(marker));
    }
}

#[test]
fn test_non_finite_content_values_are_rejected() {
    // NaN and Infinity are not JSON; a conforming parser rejects them.
    let runtime = runtime(58, "real-adapter");
    for (label, value) in [("nan", f64::NAN), ("infinity", f64::INFINITY), ("negative_infinity", f64::NEG_INFINITY)] {
        let adapter = make_real_adapter(
            &runtime,
            stub_retrieve(LocalNodeStub {
                content: LocalValue::str("NON_FINITE_CONTENT_5PQ"),
                ..LocalNodeStub::default()
            }),
            allow_everything(),
        )
        .with_claim_projection(projection(move |_| LocalValue::dict(vec![("score", LocalValue::float(value))])));
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("ValueError"), "{label}");
        let message = message(&error);
        assert!(message.contains("'score'"), "{label}: {message}");
        assert!(message.contains("finite"), "{label}: {message}");
        assert!(!message.contains("NON_FINITE_CONTENT_5PQ"), "{label}");
    }
}

#[test]
fn test_finite_scalars_and_bools_still_cross_the_boundary() {
    // Positive control: coercion rejects hidden state, not ordinary values.
    let runtime = runtime(59, "real-adapter");
    let payload = || {
        LocalValue::dict(vec![
            ("flag", LocalValue::Bool(true)),
            ("off", LocalValue::Bool(false)),
            ("count", LocalValue::int(3)),
            ("score", LocalValue::float(0.5)),
            ("text", LocalValue::str("ok")),
            ("missing", LocalValue::None),
        ])
    };
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(move |_| payload()));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = &exports[0].claims[0].content;

    assert_eq!(*content, json!({"flag": true, "off": false, "count": 3, "score": 0.5, "text": "ok", "missing": null}));
    assert_eq!(content["flag"], Value::Bool(true));
    assert_eq!(content["off"], Value::Bool(false));
    assert!(content["count"].is_i64());
    assert!(content["score"].is_f64());
    assert!(content["text"].is_string());
    // A conforming parser accepts the serialization the boundary produces (serde_json rejects NaN/Infinity).
    let serialized = canonical_json(&exports);
    assert!(serde_json::from_str::<Value>(&serialized).is_ok());
    assert!(!serialized.contains("NaN") && !serialized.contains("Infinity"));
}

#[test]
fn test_exported_int_values_are_plain_ints_carrying_no_hidden_state() {
    let runtime = runtime(60, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| LocalValue::dict(vec![("count", hostile_repr_int(7))])));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = &exports[0].claims[0].content;

    assert_eq!(*content, json!({"count": 7}));
    assert!(content["count"].is_i64());
    for marker in ["SECRET_ON_INT_ATTR_3N", "SECRET_FROM_INT_REPR_7K"] {
        assert!(!canonical_json(&exports).contains(marker));
        assert!(!format!("{exports:?}").contains(marker));
        assert!(!py_repr_value(content).contains(marker));
    }
}

#[test]
fn test_exported_float_values_are_plain_floats_carrying_no_hidden_state() {
    let runtime = runtime(61, "real-adapter");
    let adapter = make_real_adapter(&runtime, stub_retrieve(LocalNodeStub::default()), allow_everything())
        .with_claim_projection(projection(|_| LocalValue::dict(vec![("score", hostile_repr_float(1.5))])));

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let content = &exports[0].claims[0].content;

    assert_eq!(*content, json!({"score": 1.5}));
    assert!(content["score"].is_f64());
    for marker in ["SECRET_ON_FLOAT_ATTR_5W", "SECRET_FROM_FLOAT_REPR_2H"] {
        assert!(!canonical_json(&exports).contains(marker));
        assert!(!format!("{exports:?}").contains(marker));
        assert!(!py_repr_value(content).contains(marker));
    }
}

#[test]
fn test_hostile_numeric_subclasses_cannot_survive_a_coordinated_run() {
    let runtime = runtime(62, "real-adapter");
    let execution = coordinated_real_run(&runtime, || {
        LocalValue::dict(vec![
            ("slot", LocalValue::str("left")),
            ("value", LocalValue::str("sanitized")),
            ("count", hostile_repr_int(7)),
            ("score", hostile_repr_float(1.5)),
        ])
    });
    let content = &execution.claims[0].content;

    assert_eq!((content["count"].clone(), content["score"].clone()), (json!(7), json!(1.5)));
    assert!(content["count"].is_i64());
    assert!(content["score"].is_f64());
    for marker in
        ["SECRET_ON_INT_ATTR_3N", "SECRET_FROM_INT_REPR_7K", "SECRET_ON_FLOAT_ATTR_5W", "SECRET_FROM_FLOAT_REPR_2H"]
    {
        assert!(!format!("{execution:?}").contains(marker));
        assert!(!canonical_json(&execution).contains(marker));
        assert!(!py_repr_value(content).contains(marker));
    }
}

#[test]
fn test_float_subclass_hiding_a_non_finite_value_is_still_rejected() {
    // `LyingFiniteFloat`: its `__float__` claims 1.5 while the stored double is NaN/Infinity.
    let runtime = runtime(63, "real-adapter");
    for (label, raw) in [("nan", f64::NAN), ("infinity", f64::INFINITY)] {
        let lying = LocalValue::Float(
            raw,
            Some(Box::new(PySubclass {
                type_name: "LyingFiniteFloat".into(),
                float_override: Some(1.5),
                ..PySubclass::default()
            })),
        );
        let adapter = make_real_adapter(
            &runtime,
            stub_retrieve(LocalNodeStub {
                content: LocalValue::str("LYING_FLOAT_CONTENT_9VD"),
                ..LocalNodeStub::default()
            }),
            allow_everything(),
        )
        .with_claim_projection(projection(move |_| LocalValue::dict(vec![("score", lying.clone())])));
        let error = adapter.query(&runtime.fixture.query).unwrap_err();
        assert!(error.is("ValueError"), "{label}");
        let message = message(&error);
        assert!(message.contains("'score'"), "{label}: {message}");
        assert!(message.contains("finite"), "{label}: {message}");
        assert!(!message.contains("LYING_FLOAT_CONTENT_9VD"), "{label}");
    }
}

#[test]
fn test_collapsing_identifier_str_cannot_merge_two_memories() {
    // Two memories keep two identities even if `__str__` collapses them.
    let runtime = runtime(59, "real-adapter");
    let collapsing = |value: &str| {
        LocalValue::Str(
            value.into(),
            Some(Box::new(PySubclass {
                type_name: "CollapsingIdentifier".into(),
                str_override: Some("COLLAPSED_TO_ONE_ID".into()),
                ..PySubclass::default()
            })),
        )
    };
    let left = LocalNodeStub {
        node_id: collapsing("memory-left"),
        content: LocalValue::str("LEFT_1"),
        ..LocalNodeStub::default()
    };
    let right = LocalNodeStub {
        node_id: collapsing("memory-right"),
        content: LocalValue::str("RIGHT_1"),
        ..LocalNodeStub::default()
    };
    let adapter =
        make_real_adapter(&runtime, retrieve_all(vec![Rc::new(left), Rc::new(right)], 0.9), allow_everything());

    let exports = adapter.query(&runtime.fixture.query).unwrap();
    let memory_ids: BTreeSet<&str> = exports.iter().map(|e| e.trace.memory_ids[0].as_str()).collect();

    assert_eq!(exports.len(), 2);
    assert_eq!(memory_ids, BTreeSet::from(["memory-left", "memory-right"]));
    assert_eq!(exports.iter().map(|e| &e.trace.trace_id).collect::<BTreeSet<_>>().len(), 2);
    assert_eq!(exports.iter().flat_map(|e| e.claims.iter().map(|c| &c.claim_id)).collect::<BTreeSet<_>>().len(), 2);
    assert!(!canonical_json(&exports).contains("COLLAPSED_TO_ONE_ID"));
}
