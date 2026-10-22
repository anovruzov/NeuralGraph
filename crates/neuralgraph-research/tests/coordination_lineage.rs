//! Lineage resolution and export tests against real NeuralGraph storage (port of
//! `NeuralGraph/tests/test_coordination_lineage.py`, same test names and assertions).
//!
//! `StorageLineageResolver` is the only part of the coordination package that reads a real graph. Everything
//! here runs against real stores holding real `NeuralNode`s and `NeuralEdge`s. Three properties are under test:
//! direction (a `HIERARCHY` edge runs PARENT -> CHILD, so derivation parents are the targets of a node's own
//! outgoing edges), honesty (truncated walks, cycles and dangling ids under-report, never invent), and
//! containment (lineage crosses the boundary as identifier tuples only).
//!
//! The Python ran the resolver and adapter-binding suites once per backend (in-memory and SQLite); so do these.

use neuralgraph_research::coordination::adapters::{
    LocalNode, LocalNodeRef, LocalRetrieve, LocalValue, MemoryNodeAdapter, NeuralGraphMemoryAdapter, PolicyFilter,
    PySubclass,
};
use neuralgraph_research::coordination::contracts::{
    strs, AuthorizationContext, Availability, CapabilityDescriptor, CoordinationError, CoordinationResult,
    PolicyStatus, QueryRequest,
};
use neuralgraph_research::coordination::core::{
    canonical_json, clock_fn, CapabilityRegistry, ClaimNormalizer, LineageAnalyzer, Router, RuleBasedSynthesizer,
    TesseractCoordinator,
};
use neuralgraph_research::coordination::fixture::{build_runtime, required_slots, FixtureRuntime, REQUIRED_SCOPE};
use neuralgraph_research::coordination::storage_adapter::{LineageSource, ResolvedLineage, StorageLineageResolver};
use neuralgraph_research::retrieval::data_types::{
    edge_ref, node_ref, ConsolidationState, EdgeRef, EdgeType, NeuralEdge, NeuralNode, NodeLayer, NodeRef,
};
use neuralgraph_research::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use serde_json::{json, Map, Value};
use std::any::Any;
use std::collections::BTreeSet;
use std::rc::Rc;

const SESSION_KEY: &str = "coordination-lineage-session";
const REAL_NODE_ID: &str = "real-node";

const MESSAGE_IDS: [&str; 2] = ["message-1", "message-2"];
const EPISODE_ID: &str = "episode-1";
const TOPIC_ID: &str = "topic-1";
const SECOND_EPISODE_ID: &str = "episode-2";
const SECOND_MESSAGE_ID: &str = "message-3";
const TOPIC_EDGE_ID: &str = "edge-topic-1-episode-1";
const EPISODE_EDGE_IDS: [&str; 2] = ["edge-episode-1-message-1", "edge-episode-1-message-2"];

// Payload markers. Only EPISODE_SUMMARY may cross the boundary, and only as the default `{"text": ...}` claim
// projection of the retrieved node.
const EPISODE_SUMMARY: &str = "EPISODE_SUMMARY_TEXT_5QV";
const SECOND_EPISODE_SUMMARY: &str = "EPISODE_SUMMARY_TEXT_2ND";
const MESSAGE_CONTENTS: [&str; 2] = ["RAW_MESSAGE_CONTENT_7X1", "RAW_MESSAGE_CONTENT_7X2"];
const SECOND_MESSAGE_CONTENT: &str = "RAW_MESSAGE_CONTENT_7X3";
const EPISODE_METADATA_NOTE: &str = "EPISODE_METADATA_3QW";
const MESSAGE_METADATA_NOTE: &str = "MESSAGE_METADATA_8ZR";
const EMBEDDING: [f64; 3] = [0.9876543, 0.1234567, 0.5];
const SECOND_EMBEDDING: [f64; 3] = [0.8765432, 0.2345678, 0.5];

const PRIVATE_PAYLOAD_MARKERS: [&str; 8] = [
    MESSAGE_CONTENTS[0],
    MESSAGE_CONTENTS[1],
    SECOND_MESSAGE_CONTENT,
    EPISODE_METADATA_NOTE,
    MESSAGE_METADATA_NOTE,
    "0.9876543",
    "0.1234567",
    "0.8765432",
];

type Storage = Rc<dyn NeuralGraphStorage>;

fn message_ids() -> Vec<String> {
    strs(&MESSAGE_IDS)
}

// =================================================================================================================
// Real graph builders (mirrors of hierarchy.py)
// =================================================================================================================

fn note(text: &str) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("note".into(), json!(text));
    m
}

fn make_message(node_id: &str, content: &str) -> NodeRef {
    let mut node = NeuralNode::new(node_id, NodeLayer::Message, content);
    node.embedding = Some(EMBEDDING.to_vec());
    node.session_key = SESSION_KEY.into();
    node.metadata = note(MESSAGE_METADATA_NOTE);
    node_ref(node)
}

/// An episode node with the fields hierarchy.py sets.
fn make_episode(
    node_id: &str,
    messages: &[NodeRef],
    summary: &str,
    embedding: &[f64],
    source_memory_ids: Option<Vec<String>>,
) -> NodeRef {
    let ids: Vec<String> = messages.iter().map(|m| m.borrow().node_id.clone()).collect();
    let mut node = NeuralNode::new(node_id, NodeLayer::Episode, summary);
    node.embedding = Some(embedding.to_vec());
    node.session_key = SESSION_KEY.into();
    node.child_ids = ids.clone();
    node.source_memory_ids = source_memory_ids.unwrap_or(ids);
    node.metadata = note(EPISODE_METADATA_NOTE);
    node_ref(node)
}

/// Write a HIERARCHY edge exactly as hierarchy.py does: PARENT -> CHILD.
fn save_hierarchy_edge(storage: &Storage, edge_id: &str, parent_id: &str, child_id: &str) {
    storage.save_edge(&edge_ref(NeuralEdge::new(edge_id, parent_id, child_id, EdgeType::Hierarchy).with_weight(1.0)));
}

/// The messages point UP at the episode through `parent_id`, while the edge runs DOWN from the episode to each
/// message. Both directions are written so a resolver that follows the wrong one is caught.
fn consolidate_into_episode(storage: &Storage, episode: &NodeRef, messages: &[NodeRef]) {
    storage.save_node(episode);
    let episode_id = episode.borrow().node_id.clone();
    for message in messages {
        {
            let mut m = message.borrow_mut();
            m.parent_id = Some(episode_id.clone());
            m.consolidation_state = ConsolidationState::Archived;
        }
        storage.save_node(message);
        let message_id = message.borrow().node_id.clone();
        save_hierarchy_edge(storage, &format!("edge-{episode_id}-{message_id}"), &episode_id, &message_id);
    }
}

/// Serialize every stored node and edge, for read-only verification (minus the fields SQLite does not persist).
fn snapshot_storage(storage: &Storage) -> (Value, Value) {
    let mut nodes = storage.get_all_nodes(SESSION_KEY);
    nodes.sort_by(|a, b| a.borrow().node_id.cmp(&b.borrow().node_id));
    let mut edges = storage.get_all_edges(SESSION_KEY);
    edges.sort_by(|a, b| a.borrow().edge_id.cmp(&b.borrow().edge_id));
    let durable = |mut v: Value, dropped: &str| {
        v.as_object_mut().unwrap().remove(dropped);
        v
    };
    (
        Value::Array(nodes.iter().map(|n| durable(n.borrow().to_dict(), "updated_at")).collect()),
        Value::Array(edges.iter().map(|e| durable(e.borrow().to_dict(), "created_at")).collect()),
    )
}

// =================================================================================================================
// Adapter helpers
// =================================================================================================================

fn allow_everything() -> PolicyFilter {
    Box::new(|_, _| PolicyStatus::Allowed)
}

fn deny_everything() -> PolicyFilter {
    Box::new(|_, _| PolicyStatus::Denied)
}

/// The fixture query, authorized for the real-storage node under test.
fn make_request(runtime: &FixtureRuntime) -> QueryRequest {
    QueryRequest {
        authorization: AuthorizationContext::new(strs(&[REQUIRED_SCOPE]), strs(&[REAL_NODE_ID]), false).unwrap(),
        ..runtime.fixture.query.clone()
    }
}

fn make_capability(runtime: &FixtureRuntime) -> CapabilityDescriptor {
    CapabilityDescriptor {
        capability_id: runtime.fixture.query.requested_capability.clone().unwrap(),
        node_id: REAL_NODE_ID.into(),
        description: "local NeuralGraph retrieval over real storage".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[REQUIRED_SCOPE]),
        availability: Availability::Available,
    }
}

fn make_adapter(runtime: &FixtureRuntime, retrieve: LocalRetrieve, policy: PolicyFilter) -> NeuralGraphMemoryAdapter {
    NeuralGraphMemoryAdapter::new(REAL_NODE_ID, make_capability(runtime), retrieve, policy, clock_fn(&runtime.clock))
}

fn resolver(storage: &Storage) -> StorageLineageResolver<dyn NeuralGraphStorage> {
    StorageLineageResolver::new(storage.clone())
}

fn with_resolver(adapter: NeuralGraphMemoryAdapter, storage: &Storage) -> NeuralGraphMemoryAdapter {
    adapter.with_lineage_resolver(resolver(storage).into_resolver())
}

fn local(nodes: Vec<(NodeRef, f64)>) -> Vec<(LocalNodeRef, f64)> {
    nodes.into_iter().map(|(n, s)| (n as LocalNodeRef, s)).collect()
}

/// Real retrieval callback: the storage backend's own vector search.
fn episode_search(storage: &Storage) -> LocalRetrieve {
    let storage = storage.clone();
    Box::new(move |_| Ok(local(storage.vector_search(&EMBEDDING, SESSION_KEY, 8, Some(&[NodeLayer::Episode])))))
}

fn retrieve_nodes(nodes: Vec<(NodeRef, f64)>) -> LocalRetrieve {
    Box::new(move |_| Ok(local(nodes.clone())))
}

fn resolve(storage: &Storage, node: &NodeRef) -> ResolvedLineage {
    resolver(storage).resolve(&**node).unwrap()
}

fn get(storage: &Storage, node_id: &str) -> NodeRef {
    storage.get_node(node_id).expect("stored node")
}

// =================================================================================================================
// Real graph builders shared by the resolver and adapter suites (RealGraphCase)
// =================================================================================================================

fn build_episode_graph(storage: &Storage, source_memory_ids: Option<Vec<String>>) -> (NodeRef, Vec<NodeRef>) {
    let messages: Vec<NodeRef> =
        MESSAGE_IDS.iter().zip(MESSAGE_CONTENTS).map(|(id, content)| make_message(id, content)).collect();
    let episode = make_episode(EPISODE_ID, &messages, EPISODE_SUMMARY, &EMBEDDING, source_memory_ids);
    consolidate_into_episode(storage, &episode, &messages);
    (episode, messages)
}

fn build_second_episode_graph(storage: &Storage) -> (NodeRef, NodeRef) {
    let message = make_message(SECOND_MESSAGE_ID, SECOND_MESSAGE_CONTENT);
    let episode = make_episode(
        SECOND_EPISODE_ID,
        std::slice::from_ref(&message),
        SECOND_EPISODE_SUMMARY,
        &SECOND_EMBEDDING,
        None,
    );
    consolidate_into_episode(storage, &episode, std::slice::from_ref(&message));
    (episode, message)
}

/// messages -> episode -> topic, exactly as hierarchy.py writes it: the topic records `child_ids` and a HIERARCHY
/// edge but no `source_memory_ids`, so edges are the only evidence of its derivation.
fn build_topic_graph(storage: &Storage) -> (NodeRef, NodeRef, Vec<NodeRef>) {
    let (episode, messages) = build_episode_graph(storage, None);
    let mut topic = NeuralNode::new(TOPIC_ID, NodeLayer::Topic, "topic summary");
    topic.embedding = Some(EMBEDDING.to_vec());
    topic.session_key = SESSION_KEY.into();
    topic.child_ids = strs(&[EPISODE_ID]);
    let topic = node_ref(topic);
    storage.save_node(&topic);
    episode.borrow_mut().parent_id = Some(TOPIC_ID.into());
    storage.save_node(&episode);
    save_hierarchy_edge(storage, TOPIC_EDGE_ID, TOPIC_ID, EPISODE_ID);
    (topic, episode, messages)
}

/// persona <- topic <- episode <- message, one derivation parent each.
fn build_derivation_chain(storage: &Storage) -> Vec<NodeRef> {
    let layers = [NodeLayer::Message, NodeLayer::Episode, NodeLayer::Topic, NodeLayer::Persona];
    let mut chain = Vec::new();
    for (index, layer) in layers.into_iter().enumerate() {
        let mut node = NeuralNode::new(format!("chain-{}", index + 1), layer, format!("chain level {}", index + 1));
        node.session_key = SESSION_KEY.into();
        let node = node_ref(node);
        storage.save_node(&node);
        chain.push(node);
    }
    for pair in chain.windows(2) {
        let (child, parent) = (pair[0].borrow().node_id.clone(), pair[1].borrow().node_id.clone());
        save_hierarchy_edge(storage, &format!("edge-{parent}-{child}"), &parent, &child);
    }
    chain
}

/// A derivation cycle: each node records the other as its parent.
fn build_cycle(storage: &Storage) {
    for node_id in ["cycle-a", "cycle-b"] {
        let mut node = NeuralNode::new(node_id, NodeLayer::Episode, format!("cycle node {node_id}"));
        node.session_key = SESSION_KEY.into();
        storage.save_node(&node_ref(node));
    }
    save_hierarchy_edge(storage, "edge-cycle-a-cycle-b", "cycle-a", "cycle-b");
    save_hierarchy_edge(storage, "edge-cycle-b-cycle-a", "cycle-b", "cycle-a");
}

// =================================================================================================================
// Resolver behaviour (LineageResolverBehaviour), run per backend
// =================================================================================================================

const RESOLVED_KEYS: [&str; 5] =
    ["edge_path", "failure_domains", "lineage_root_ids", "parent_memory_ids", "source_ids"];

fn sorted_keys(resolved: &ResolvedLineage) -> Vec<&'static str> {
    let mut keys: Vec<&'static str> = resolved.items().iter().map(|(k, _)| *k).collect();
    keys.sort();
    keys
}

fn test_episode_over_messages_resolves_exactly(storage: Storage) {
    build_episode_graph(&storage, None);
    let stored = get(&storage, EPISODE_ID);

    let resolved = resolve(&storage, &stored);

    let mut edges = strs(&EPISODE_EDGE_IDS);
    edges.sort();
    assert_eq!(
        resolved,
        ResolvedLineage {
            source_ids: message_ids(),
            parent_memory_ids: message_ids(),
            lineage_root_ids: message_ids(),
            edge_path: edges,
            failure_domains: Vec::new(),
        }
    );
}

fn test_topic_derivation_is_carried_by_edges_without_source_ids(storage: Storage) {
    build_topic_graph(&storage);
    let stored = get(&storage, TOPIC_ID);
    assert!(stored.borrow().source_memory_ids.is_empty());
    assert_eq!(stored.borrow().child_ids, strs(&[EPISODE_ID]));

    let resolved = StorageLineageResolver::with_bounds(storage.clone(), 2, 64).unwrap().resolve(&*stored).unwrap();

    assert!(resolved.source_ids.is_empty());
    assert_eq!(resolved.parent_memory_ids, strs(&[EPISODE_ID]));
    assert_eq!(resolved.lineage_root_ids, message_ids());
    let mut edges = strs(&[EPISODE_EDGE_IDS[0], EPISODE_EDGE_IDS[1], TOPIC_EDGE_ID]);
    edges.sort();
    assert_eq!(resolved.edge_path, edges);
}

fn test_topic_under_reports_roots_below_its_depth(storage: Storage) {
    build_topic_graph(&storage);
    let stored = get(&storage, TOPIC_ID);

    let resolved = StorageLineageResolver::with_bounds(storage.clone(), 1, 64).unwrap().resolve(&*stored).unwrap();

    assert_eq!(resolved.parent_memory_ids, strs(&[EPISODE_ID]));
    assert!(resolved.lineage_root_ids.is_empty());
}

fn test_origin_node_reports_no_parent_and_never_roots_itself(storage: Storage) {
    let mut origin = NeuralNode::new("origin-1", NodeLayer::Message, "origin content");
    origin.session_key = SESSION_KEY.into();
    storage.save_node(&node_ref(origin));
    let stored = get(&storage, "origin-1");

    let resolved = resolve(&storage, &stored);

    assert_eq!(sorted_keys(&resolved), RESOLVED_KEYS);
    assert!(resolved.parent_memory_ids.is_empty());
    assert!(resolved.lineage_root_ids.is_empty());
    assert!(!resolved.lineage_root_ids.contains(&"origin-1".to_string()));
}

fn test_cycle_terminates_and_repeats_identically(storage: Storage) {
    build_cycle(&storage);
    let stored = get(&storage, "cycle-a");
    let resolver = resolver(&storage);

    let first = resolver.resolve(&*stored).unwrap();
    let second = resolver.resolve(&*stored).unwrap();

    assert_eq!(first, second);
    assert_eq!(first.parent_memory_ids, strs(&["cycle-b"]));
    assert!(first.lineage_root_ids.is_empty());
    assert_eq!(first.edge_path, strs(&["edge-cycle-a-cycle-b", "edge-cycle-b-cycle-a"]));
    let mirrored = resolver.resolve(&*get(&storage, "cycle-b")).unwrap();
    assert_eq!(mirrored.parent_memory_ids, strs(&["cycle-a"]));
    assert!(mirrored.lineage_root_ids.is_empty());
}

fn test_chain_deeper_than_the_bound_under_reports_without_inventing(storage: Storage) {
    let chain = build_derivation_chain(&storage);
    let origin = get(&storage, &chain.last().unwrap().borrow().node_id.clone());

    let bounded = |depth: i64, nodes: i64| {
        StorageLineageResolver::with_bounds(storage.clone(), depth, nodes).unwrap().resolve(&*origin).unwrap()
    };
    let full = bounded(3, 64);
    let shallow = bounded(2, 64);
    let one_hop = bounded(1, 64);
    let narrow = bounded(3, 1);

    assert_eq!(full.lineage_root_ids, strs(&["chain-1"]));
    for bounded in [shallow, one_hop, narrow] {
        assert!(bounded.lineage_root_ids.is_empty());
        assert_eq!(bounded.parent_memory_ids, strs(&["chain-3"]));
        assert!(bounded.lineage_root_ids.iter().all(|r| r == "chain-1"));
    }
}

fn test_dangling_recorded_source_stays_a_parent_but_never_a_root(storage: Storage) {
    build_episode_graph(&storage, None);
    assert!(storage.delete_node("message-2"));
    let stored = get(&storage, EPISODE_ID);
    assert_eq!(stored.borrow().source_memory_ids, message_ids());
    assert!(storage.get_node("message-2").is_none());

    let resolved = resolve(&storage, &stored);

    assert_eq!(resolved.parent_memory_ids, message_ids());
    assert_eq!(resolved.lineage_root_ids, strs(&["message-1"]));
    assert_eq!(resolved.edge_path, strs(&[EPISODE_EDGE_IDS[0]]));
}

fn test_hierarchy_edge_direction_is_never_inverted(storage: Storage) {
    // A summary is not an ancestor of the message it was built from.
    build_episode_graph(&storage, None);
    let stored = get(&storage, "message-1");
    let incoming = storage.get_edges_to("message-1", Some(&[EdgeType::Hierarchy]));

    let resolved = resolve(&storage, &stored);

    // The upward links really exist; the resolver deliberately ignores them.
    assert_eq!(stored.borrow().parent_id.as_deref(), Some(EPISODE_ID));
    assert_eq!(incoming.iter().map(|e| e.borrow().source_id.clone()).collect::<Vec<_>>(), strs(&[EPISODE_ID]));
    assert!(resolved.source_ids.is_empty());
    assert!(resolved.parent_memory_ids.is_empty());
    assert!(resolved.lineage_root_ids.is_empty());
    assert!(resolved.edge_path.is_empty());
    for (field, value) in resolved.items() {
        assert!(!value.contains(&EPISODE_ID.to_string()), "{field}");
    }
}

fn test_every_field_is_sorted_deduped_and_idempotent(storage: Storage) {
    build_episode_graph(&storage, Some(strs(&["message-2", "message-1", "message-2"])));
    let stored = get(&storage, EPISODE_ID);
    assert_eq!(stored.borrow().source_memory_ids, strs(&["message-2", "message-1", "message-2"]));
    let resolver = resolver(&storage);

    let first = resolver.resolve(&*stored).unwrap();
    let second = resolver.resolve(&*stored).unwrap();

    assert_eq!(first, second);
    assert_eq!(sorted_keys(&first), RESOLVED_KEYS);
    for (field, value) in first.items() {
        let normalized: Vec<String> = value.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect();
        assert_eq!(*value, normalized, "{field}");
    }
    assert_eq!(first.source_ids, message_ids());
}

fn test_resolution_never_writes_to_the_graph(storage: Storage) {
    // Read-only in fact, not only by intent: no node or edge changes.
    let (topic, _episode, _messages) = build_topic_graph(&storage);
    let before = snapshot_storage(&storage);

    let topic_id = topic.borrow().node_id.clone();
    let resolved = resolve(&storage, &get(&storage, &topic_id));

    assert_eq!(resolved.lineage_root_ids, message_ids());
    assert_eq!(snapshot_storage(&storage), before);
}

/// A real storage instance whose node reads fail: edges still come from the real backend, and only `get_node`
/// fails, which is exactly the case where a resolver could be tempted to report empty lineage.
struct BrokenStorage {
    storage: Storage,
}

impl LineageSource for BrokenStorage {
    fn get_node(&self, _node_id: &str) -> CoordinationResult<Option<NodeRef>> {
        Err(CoordinationError::new("StorageUnavailable", "node read failed"))
    }

    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> CoordinationResult<Vec<EdgeRef>> {
        Ok(self.storage.get_edges_from(node_id, edge_types))
    }
}

fn test_storage_failures_propagate_instead_of_emptying_lineage(storage: Storage) {
    // A read error must not be reported as "this memory has no ancestors".
    build_episode_graph(&storage, None);
    let stored = get(&storage, EPISODE_ID);
    let broken = StorageLineageResolver::new(Rc::new(BrokenStorage { storage: storage.clone() }));

    let error = broken.resolve(&*stored).unwrap_err();
    assert!(error.is("StorageUnavailable"));
}

// =================================================================================================================
// Adapter bound to real storage (RealStorageAdapterBinding), run per backend
// =================================================================================================================

fn test_exported_lineage_matches_the_stored_graph(storage: Storage) {
    build_episode_graph(&storage, None);
    let runtime = build_runtime(60, "real-storage").unwrap();
    let adapter = with_resolver(make_adapter(&runtime, episode_search(&storage), allow_everything()), &storage);

    let exports = adapter.query(&make_request(&runtime)).unwrap();

    let stored = get(&storage, EPISODE_ID);
    let stored_edges = storage.get_edges_from(EPISODE_ID, Some(&[EdgeType::Hierarchy]));
    let expected_sources: Vec<String> =
        stored.borrow().source_memory_ids.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect();
    let mut expected_edges: Vec<String> = stored_edges.iter().map(|e| e.borrow().edge_id.clone()).collect();
    expected_edges.sort();
    assert_eq!(exports.len(), 1);
    let claim = &exports[0].claims[0];
    let trace = &exports[0].trace;
    assert_eq!(trace.memory_ids, strs(&[EPISODE_ID]));
    assert_eq!(claim.source_ids, expected_sources);
    assert_eq!(claim.parent_memory_ids, expected_sources);
    assert_eq!(claim.lineage_root_ids, expected_sources);
    assert!(claim.failure_domains.is_empty());
    assert_eq!(trace.source_ids, expected_sources);
    assert_eq!(trace.parent_memory_ids, expected_sources);
    assert_eq!(trace.lineage_root_ids, expected_sources);
    assert_eq!(trace.edge_path, expected_edges);
    assert_eq!(trace.retrieval_operator, "neuralgraph_local_retrieval");
    assert_eq!(claim.derivation_operator.as_deref(), Some("neuralgraph_local_retrieval"));
    assert_eq!(claim.policy_status, PolicyStatus::Allowed);
}

fn test_export_carries_no_stored_content_metadata_or_embedding(storage: Storage) {
    build_episode_graph(&storage, None);
    let runtime = build_runtime(61, "real-storage").unwrap();
    let adapter = with_resolver(make_adapter(&runtime, episode_search(&storage), allow_everything()), &storage);

    let exports = adapter.query(&make_request(&runtime)).unwrap();
    let payload = canonical_json(&exports);

    // Exactly one payload field crosses: the retrieved node's own text.
    assert_eq!(exports[0].claims[0].content, json!({"text": EPISODE_SUMMARY}));
    for marker in PRIVATE_PAYLOAD_MARKERS {
        assert!(!payload.contains(marker), "{marker}");
    }
}

fn test_denied_policy_exports_an_empty_trace_and_no_payload(storage: Storage) {
    build_episode_graph(&storage, None);
    let runtime = build_runtime(62, "real-storage").unwrap();
    let adapter = with_resolver(make_adapter(&runtime, episode_search(&storage), deny_everything()), &storage);

    let exports = adapter.query(&make_request(&runtime)).unwrap();
    let payload = canonical_json(&exports);

    assert_eq!(exports.len(), 1);
    let trace = &exports[0].trace;
    assert!(exports[0].claims.is_empty());
    assert_eq!(trace.policy_status, PolicyStatus::Denied);
    for ids in
        [&trace.memory_ids, &trace.source_ids, &trace.parent_memory_ids, &trace.lineage_root_ids, &trace.edge_path]
    {
        assert!(ids.is_empty());
    }
    for marker in PRIVATE_PAYLOAD_MARKERS.iter().chain([EPISODE_SUMMARY, EPISODE_ID].iter()).chain(MESSAGE_IDS.iter()) {
        assert!(!payload.contains(marker), "{marker}");
    }
}

fn test_two_stored_memories_export_two_distinct_identities(storage: Storage) {
    build_episode_graph(&storage, None);
    build_second_episode_graph(&storage);
    let runtime = build_runtime(63, "real-storage").unwrap();
    let adapter = with_resolver(make_adapter(&runtime, episode_search(&storage), allow_everything()), &storage);

    let exports = adapter.query(&make_request(&runtime)).unwrap();

    assert_eq!(exports.len(), 2);
    let memory_ids: BTreeSet<Vec<String>> = exports.iter().map(|e| e.trace.memory_ids.clone()).collect();
    assert_eq!(memory_ids, BTreeSet::from([strs(&[EPISODE_ID]), strs(&[SECOND_EPISODE_ID])]));
    assert_eq!(exports.iter().map(|e| &e.trace.trace_id).collect::<BTreeSet<_>>().len(), 2);
    assert_eq!(exports.iter().flat_map(|e| e.claims.iter().map(|c| &c.claim_id)).collect::<BTreeSet<_>>().len(), 2);
    let roots = |memory_id: &str| {
        exports.iter().find(|e| e.trace.memory_ids[0] == memory_id).unwrap().claims[0].lineage_root_ids.clone()
    };
    assert_eq!(roots(EPISODE_ID), message_ids());
    assert_eq!(roots(SECOND_EPISODE_ID), strs(&[SECOND_MESSAGE_ID]));
}

fn test_one_memory_reached_twice_exports_once_with_the_same_identity(storage: Storage) {
    // A callback unioning two retrieval paths must not double-count a memory.
    build_episode_graph(&storage, None);
    let runtime = build_runtime(64, "real-storage").unwrap();
    let single = with_resolver(make_adapter(&runtime, episode_search(&storage), allow_everything()), &storage)
        .query(&make_request(&runtime))
        .unwrap();

    let searched = storage.vector_search(&EMBEDDING, SESSION_KEY, 8, Some(&[NodeLayer::Episode]));
    // A second, independently rehydrated view of the same stored memory.
    let stored_dict = get(&storage, EPISODE_ID).borrow().to_dict();
    let reloaded = node_ref(NeuralNode::from_dict(stored_dict.as_object().unwrap()).unwrap());

    let mut union = searched.clone();
    union.push((reloaded.clone(), 0.5));
    let duplicated = with_resolver(make_adapter(&runtime, retrieve_nodes(union), allow_everything()), &storage)
        .query(&make_request(&runtime))
        .unwrap();

    assert!(!Rc::ptr_eq(&reloaded, &searched[0].0));
    assert_eq!(reloaded.borrow().node_id, searched[0].0.borrow().node_id);
    assert_eq!(single.len(), 1);
    assert_eq!(duplicated.len(), 1);
    assert_eq!(duplicated[0].trace.trace_id, single[0].trace.trace_id);
    assert_eq!(duplicated[0].claims[0].claim_id, single[0].claims[0].claim_id);
    assert_eq!(duplicated[0].trace.memory_ids, strs(&[EPISODE_ID]));
    assert_eq!(duplicated[0].claims[0].lineage_root_ids, message_ids());
}

/// `count` independently rooted episodes, each with its own message.
fn build_distinct_episodes(storage: &Storage, count: usize) -> Vec<NodeRef> {
    let mut stored = Vec::new();
    for index in 0..count {
        let message = make_message(&format!("budget-message-{index}"), MESSAGE_CONTENTS[0]);
        let episode = make_episode(
            &format!("budget-episode-{index}"),
            std::slice::from_ref(&message),
            &format!("BUDGET_SUMMARY_{index}"),
            &EMBEDDING,
            None,
        );
        consolidate_into_episode(storage, &episode, &[message]);
        let episode_id = episode.borrow().node_id.clone();
        stored.push(get(storage, &episode_id));
    }
    stored
}

fn test_repeats_do_not_spend_budget_reserved_for_distinct_memories(storage: Storage) {
    // Budget bounds what is exported, not how many candidates were offered.
    let stored = build_distinct_episodes(&storage, 6);
    let runtime = build_runtime(68, "real-storage").unwrap();
    let request = make_request(&runtime);

    let mut union: Vec<(NodeRef, f64)> = stored[..3].iter().map(|n| (n.clone(), 0.9)).collect();
    union.extend(stored.iter().map(|n| (n.clone(), 0.5)));
    let exports = with_resolver(make_adapter(&runtime, retrieve_nodes(union), allow_everything()), &storage)
        .query(&request)
        .unwrap();

    let mut exported_ids: Vec<String> = exports.iter().map(|e| e.trace.memory_ids[0].clone()).collect();
    // 9 candidates, 3 of them repeats, for a budget of 8 export slots.
    assert_eq!(request.budget.max_claims, 8);
    assert!(exports.len() as i64 <= request.budget.max_claims);
    exported_ids.sort();
    let mut expected: Vec<String> = stored.iter().map(|n| n.borrow().node_id.clone()).collect();
    expected.sort();
    assert_eq!(exported_ids, expected);
    assert_eq!(exported_ids.iter().collect::<BTreeSet<_>>().len(), stored.len());
    assert_eq!(exports.iter().map(|e| &e.trace.trace_id).collect::<BTreeSet<_>>().len(), stored.len());
}

fn test_budget_still_bounds_exports_of_distinct_memories(storage: Storage) {
    // The bound itself is unchanged, and a denial keeps consuming a slot.
    let stored = build_distinct_episodes(&storage, 10);
    let runtime = build_runtime(69, "real-storage").unwrap();
    let request = make_request(&runtime);

    for (label, policy, expected_status) in
        [("allowed", allow_everything(), PolicyStatus::Allowed), ("denied", deny_everything(), PolicyStatus::Denied)]
    {
        let all: Vec<(NodeRef, f64)> = stored.iter().map(|n| (n.clone(), 0.9)).collect();
        let exports =
            with_resolver(make_adapter(&runtime, retrieve_nodes(all), policy), &storage).query(&request).unwrap();

        assert_eq!(exports.len() as i64, request.budget.max_claims, "{label}");
        assert!(exports.iter().all(|e| e.trace.policy_status == expected_status), "{label}");
    }
}

fn test_duplicate_is_skipped_on_the_denied_path_too(storage: Storage) {
    build_episode_graph(&storage, None);
    let runtime = build_runtime(65, "real-storage").unwrap();
    let stored = get(&storage, EPISODE_ID);
    let stored_dict = stored.borrow().to_dict();
    let reloaded = node_ref(NeuralNode::from_dict(stored_dict.as_object().unwrap()).unwrap());

    let exports = with_resolver(
        make_adapter(&runtime, retrieve_nodes(vec![(stored, 0.9), (reloaded, 0.5)]), deny_everything()),
        &storage,
    )
    .query(&make_request(&runtime))
    .unwrap();

    assert_eq!(exports.len(), 1);
    assert_eq!(exports[0].trace.policy_status, PolicyStatus::Denied);
}

/// Instantiate the per-backend suites (the Python test classes `InMemory...Tests` / `SQLite...Tests`).
macro_rules! backend_suite {
    ($module:ident, $backend:expr, [$($name:ident),* $(,)?]) => {
        mod $module {
            $(
                #[test]
                fn $name() {
                    let backend = $backend;
                    super::$name(backend.storage.clone());
                }
            )*
        }
    };
}

macro_rules! resolver_suite {
    ($module:ident, $backend:expr) => {
        backend_suite!(
            $module,
            $backend,
            [
                test_episode_over_messages_resolves_exactly,
                test_topic_derivation_is_carried_by_edges_without_source_ids,
                test_topic_under_reports_roots_below_its_depth,
                test_origin_node_reports_no_parent_and_never_roots_itself,
                test_cycle_terminates_and_repeats_identically,
                test_chain_deeper_than_the_bound_under_reports_without_inventing,
                test_dangling_recorded_source_stays_a_parent_but_never_a_root,
                test_hierarchy_edge_direction_is_never_inverted,
                test_every_field_is_sorted_deduped_and_idempotent,
                test_resolution_never_writes_to_the_graph,
                test_storage_failures_propagate_instead_of_emptying_lineage,
            ]
        );
    };
}

macro_rules! adapter_suite {
    ($module:ident, $backend:expr) => {
        backend_suite!(
            $module,
            $backend,
            [
                test_exported_lineage_matches_the_stored_graph,
                test_export_carries_no_stored_content_metadata_or_embedding,
                test_denied_policy_exports_an_empty_trace_and_no_payload,
                test_two_stored_memories_export_two_distinct_identities,
                test_one_memory_reached_twice_exports_once_with_the_same_identity,
                test_repeats_do_not_spend_budget_reserved_for_distinct_memories,
                test_budget_still_bounds_exports_of_distinct_memories,
                test_duplicate_is_skipped_on_the_denied_path_too,
            ]
        );
    };
}

/// One test's store (and, for SQLite, the temporary directory that must outlive it).
struct Backend {
    storage: Storage,
    #[allow(dead_code)]
    tmpdir: Option<tempfile::TempDir>,
}

fn in_memory_backend() -> Backend {
    Backend { storage: Rc::new(InMemoryNeuralGraphStorage::default()), tmpdir: None }
}

resolver_suite!(in_memory_lineage_resolver_tests, super::in_memory_backend());
adapter_suite!(in_memory_real_adapter_tests, super::in_memory_backend());

/// The SQLite instantiations (`SQLiteLineageResolverTests`, `SQLiteRealAdapterTests`). Every store gets an
/// explicit temporary `db_path`: the Python default points at `~/.memmachine/memories.db`.
mod sqlite_backend {
    use super::*;
    use neuralgraph_research::retrieval::sqlite_storage::SqliteNeuralGraphStorage;

    pub fn sqlite_backend() -> Backend {
        let tmpdir = tempfile::tempdir().unwrap();
        let db_path = tmpdir.path().join("coordination_lineage_test.db");
        Backend { storage: Rc::new(SqliteNeuralGraphStorage::new(&db_path)), tmpdir: Some(tmpdir) }
    }

    resolver_suite!(sqlite_lineage_resolver_tests, super::sqlite_backend());
    adapter_suite!(sqlite_real_adapter_tests, super::sqlite_backend());
}

// =================================================================================================================
// Resolver output is untrusted boundary input (ResolverBoundaryValidationTests)
// =================================================================================================================

const LINEAGE_FIELDS: [&str; 5] =
    ["source_ids", "parent_memory_ids", "lineage_root_ids", "failure_domains", "edge_path"];
const LEAK_MARKER: &str = "RAW_PRIVATE_CONTENT_7X";
const LEAK_METADATA_MARKER: &str = "SECRET_META_3Q";

/// `LeakyRecord(content=..., metadata=...)`: a dataclass `to_jsonable` would fully expand, so it must never be
/// exported. Only its type name may appear in an error.
fn leaky_record(content: &str, metadata: LocalValue) -> LocalValue {
    LocalValue::Object(neuralgraph_research::coordination::adapters::LocalObject {
        type_name: "LeakyRecord".into(),
        attributes: vec![("content".into(), LocalValue::str(content)), ("metadata".into(), metadata)],
    })
}

/// `HostileIdentifier(value)`: `str` subclass smuggling payload through `repr` and an attribute.
fn hostile_identifier(value: &str) -> LocalValue {
    LocalValue::Str(
        value.into(),
        Some(Box::new(PySubclass {
            type_name: "HostileIdentifier".into(),
            repr: Some("SECRET_FROM_ROOT_REPR_2K".into()),
            attributes: vec![("smuggled".into(), "SMUGGLED_ROOT_ATTR_8W".into())],
            ..PySubclass::default()
        })),
    )
}

fn static_resolver(known: LocalValue) -> neuralgraph_research::coordination::adapters::LineageResolver {
    Box::new(move |_| Ok(known.clone()))
}

/// `asyncSetUp`: a real episode in a real in-memory store; only the resolver is hostile.
struct ValidationCase {
    storage: Storage,
    runtime: FixtureRuntime,
    node: NodeRef,
}

impl ValidationCase {
    fn new() -> Self {
        let storage: Storage = Rc::new(InMemoryNeuralGraphStorage::default());
        let runtime = build_runtime(66, "resolver-boundary").unwrap();
        storage.save_node(&make_episode(EPISODE_ID, &[], EPISODE_SUMMARY, &EMBEDDING, Some(Vec::new())));
        let node = get(&storage, EPISODE_ID);
        ValidationCase { storage, runtime, node }
    }

    fn retrieve_stored(&self, node: Option<LocalNodeRef>) -> LocalRetrieve {
        let target: LocalNodeRef = node.unwrap_or_else(|| self.node.clone());
        Box::new(move |_| Ok(vec![(target.clone(), 0.9)]))
    }

    fn query_with(
        &self,
        resolver: neuralgraph_research::coordination::adapters::LineageResolver,
    ) -> CoordinationResult<Vec<neuralgraph_research::coordination::contracts::EvidenceExport>> {
        make_adapter(&self.runtime, self.retrieve_stored(None), allow_everything())
            .with_lineage_resolver(resolver)
            .query(&make_request(&self.runtime))
    }

    fn assert_rejected(
        &self,
        expected_fragments: &[&str],
        forbidden: &[&str],
        resolver: neuralgraph_research::coordination::adapters::LineageResolver,
    ) -> String {
        let error = self.query_with(resolver).unwrap_err();
        assert!(error.is("ValueError"), "{error:?}");
        let message = format!("{error}|{}", error.repr());
        for fragment in expected_fragments {
            assert!(message.contains(fragment), "{fragment} not in {message}");
        }
        for leak in forbidden {
            assert!(!message.contains(leak), "{leak} in {message}");
        }
        message
    }
}

#[test]
fn test_bare_string_is_rejected_instead_of_exploded_into_characters() {
    let case = ValidationCase::new();
    for field in LINEAGE_FIELDS {
        case.assert_rejected(
            &[field, "str"],
            &["memory-42"],
            static_resolver(LocalValue::dict(vec![(field, LocalValue::str("memory-42"))])),
        );
    }
}

#[test]
fn test_non_sequence_lineage_values_are_rejected() {
    let case = ValidationCase::new();
    let rejected = [
        ("bytes", LocalValue::Bytes(b"memory-42".to_vec())),
        ("set", LocalValue::Set(vec![LocalValue::str("memory-42")])),
        ("mapping", LocalValue::dict(vec![("memory-42", LocalValue::int(1))])),
        ("integer", LocalValue::int(42)),
    ];
    for (label, value) in rejected {
        let message = case.assert_rejected(
            &["lineage_root_ids"],
            &["memory-42"],
            static_resolver(LocalValue::dict(vec![("lineage_root_ids", value)])),
        );
        assert!(!message.is_empty(), "{label}");
    }
}

#[test]
fn test_object_entry_is_rejected_without_serializing_it() {
    let case = ValidationCase::new();
    let leaky = leaky_record(LEAK_MARKER, LocalValue::dict(vec![("k", LocalValue::str(LEAK_METADATA_MARKER))]));

    case.assert_rejected(
        &["lineage_root_ids", "LeakyRecord"],
        &[LEAK_MARKER, LEAK_METADATA_MARKER],
        static_resolver(LocalValue::dict(vec![("lineage_root_ids", LocalValue::Tuple(vec![leaky]))])),
    );
}

#[test]
fn test_blank_entries_are_rejected() {
    let case = ValidationCase::new();
    for (_label, entry) in [("empty", ""), ("whitespace", "   ")] {
        case.assert_rejected(
            &["parent_memory_ids", "blank"],
            &[EPISODE_SUMMARY],
            static_resolver(LocalValue::dict(vec![("parent_memory_ids", LocalValue::tuple_of(&[entry]))])),
        );
    }
}

#[test]
fn test_non_mapping_resolver_result_is_rejected() {
    let case = ValidationCase::new();
    for (_label, result) in [
        ("list", LocalValue::list_of(&["lineage_root_ids"])),
        ("tuple", LocalValue::tuple_of(&["lineage_root_ids"])),
        ("none", LocalValue::None),
        ("string", LocalValue::str("lineage_root_ids")),
    ] {
        case.assert_rejected(&["lineage resolver must return a mapping"], &[EPISODE_SUMMARY], static_resolver(result));
    }
}

#[test]
fn test_retrieval_operator_must_be_a_non_blank_string() {
    let case = ValidationCase::new();
    for (_label, value) in [
        ("object", leaky_record(LEAK_MARKER, LocalValue::empty_dict())),
        ("integer", LocalValue::int(7)),
        ("blank", LocalValue::str("   ")),
    ] {
        case.assert_rejected(
            &["retrieval_operator"],
            &[LEAK_MARKER, EPISODE_SUMMARY],
            static_resolver(LocalValue::dict(vec![("retrieval_operator", value)])),
        );
    }
}

#[test]
fn test_valid_retrieval_operator_is_exported_as_a_plain_string() {
    let case = ValidationCase::new();
    let exports = case
        .query_with(static_resolver(LocalValue::dict(vec![(
            "retrieval_operator",
            hostile_identifier("owner_declared_operator"),
        )])))
        .unwrap();

    let operator = &exports[0].trace.retrieval_operator;
    // `type(operator) is str` with no `smuggled` attribute: it is a plain String.
    assert_eq!(operator, "owner_declared_operator");
    assert_eq!(exports[0].claims[0].derivation_operator.as_deref(), Some("owner_declared_operator"));
    assert!(!canonical_json(&exports).contains("SMUGGLED_ROOT_ATTR_8W"));
}

#[test]
fn test_string_subclass_entry_crosses_as_a_plain_string() {
    let case = ValidationCase::new();
    let exports = case
        .query_with(static_resolver(LocalValue::dict(vec![(
            "lineage_root_ids",
            LocalValue::Tuple(vec![hostile_identifier("root-1")]),
        )])))
        .unwrap();

    let claim = &exports[0].claims[0];
    let payload = canonical_json(&exports);

    assert_eq!(claim.lineage_root_ids, strs(&["root-1"]));
    assert_eq!(exports[0].trace.lineage_root_ids, strs(&["root-1"]));
    for marker in ["SMUGGLED_ROOT_ATTR_8W", "SECRET_FROM_ROOT_REPR_2K"] {
        assert!(!payload.contains(marker), "{marker}");
        assert!(!format!("{exports:?}").contains(marker), "{marker}");
    }
}

#[test]
fn test_order_and_multiplicity_are_preserved_exactly() {
    // The adapter validates; it does not normalize what the owner reported.
    let case = ValidationCase::new();
    let exports = case
        .query_with(static_resolver(LocalValue::dict(vec![
            ("parent_memory_ids", LocalValue::tuple_of(&["memory-b", "memory-a", "memory-b"])),
            ("lineage_root_ids", LocalValue::list_of(&["root-b", "root-a"])),
            ("edge_path", LocalValue::tuple_of(&["edge-2", "edge-1"])),
            ("source_ids", LocalValue::tuple_of(&["source-2", "source-1"])),
            ("failure_domains", LocalValue::tuple_of(&["domain-2", "domain-1"])),
        ])))
        .unwrap();

    let claim = &exports[0].claims[0];
    let trace = &exports[0].trace;
    assert_eq!(claim.parent_memory_ids, strs(&["memory-b", "memory-a", "memory-b"]));
    assert_eq!(claim.lineage_root_ids, strs(&["root-b", "root-a"]));
    assert_eq!(claim.source_ids, strs(&["source-2", "source-1"]));
    assert_eq!(claim.failure_domains, strs(&["domain-2", "domain-1"]));
    assert_eq!(trace.edge_path, strs(&["edge-2", "edge-1"]));
    assert_eq!(trace.parent_memory_ids, strs(&["memory-b", "memory-a", "memory-b"]));
}

/// A stored node whose `source_memory_ids` was rehydrated from a legacy row. Python assigned the raw value to the
/// node and saved it; a Rust `NeuralNode` cannot hold a bare string or an int there, so the row's other fields
/// come from the real stored node and only this attribute carries the legacy value.
struct LegacyRow {
    node: NodeRef,
    source_memory_ids: LocalValue,
}

impl LocalNode for LegacyRow {
    fn type_name(&self) -> String {
        "NeuralNode".into()
    }

    fn getattr(&self, name: &str) -> Option<LocalValue> {
        if name == "source_memory_ids" {
            return Some(self.source_memory_ids.clone());
        }
        self.node.getattr(name)
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

#[test]
fn test_stored_lineage_fallback_is_validated_too() {
    // A rehydrated row is untrusted: legacy JSON can hold anything.
    let case = ValidationCase::new();
    let cases: [(&str, LocalValue, &[&str], &[&str]); 3] = [
        ("bare_string", LocalValue::str("memory-42"), &["source_ids", "str"], &["memory-42"]),
        ("non_string_entry", LocalValue::List(vec![LocalValue::int(7)]), &["source_ids", "int"], &[]),
        ("blank_entry", LocalValue::list_of(&[" "]), &["source_ids", "blank"], &[]),
    ];
    for (label, stored_value, expected, forbidden) in cases {
        let node = make_episode(&format!("legacy-{label}"), &[], EPISODE_SUMMARY, &EMBEDDING, Some(Vec::new()));
        case.storage.save_node(&node);
        let loaded = get(&case.storage, &format!("legacy-{label}"));
        let row: LocalNodeRef = Rc::new(LegacyRow { node: loaded, source_memory_ids: stored_value });
        let adapter = make_adapter(&case.runtime, case.retrieve_stored(Some(row)), allow_everything());

        let error = adapter.query(&make_request(&case.runtime)).unwrap_err();
        assert!(error.is("ValueError"), "{label}");
        let message = format!("{error}|{}", error.repr());
        for fragment in expected {
            assert!(message.contains(fragment), "{label}: {fragment} not in {message}");
        }
        for leak in forbidden.iter().chain([EPISODE_SUMMARY, EPISODE_METADATA_NOTE].iter()) {
            assert!(!message.contains(leak), "{label}: {leak}");
        }
    }
}

#[test]
fn test_a_valid_resolver_still_exports_normally() {
    // Positive control: validation rejects malformed input, not all input.
    let case = ValidationCase::new();
    let exports = case.query_with(resolver(&case.storage).into_resolver()).unwrap();

    assert_eq!(exports[0].trace.memory_ids, strs(&[EPISODE_ID]));
    assert!(exports[0].claims[0].lineage_root_ids.is_empty());
    assert_eq!(exports[0].claims[0].content, json!({"text": EPISODE_SUMMARY}));
}

// =================================================================================================================
// Documented limitations of the real-storage path (RealStorageAnalysisLimitationTests)
// =================================================================================================================

/// What a real-storage run can and cannot currently say about fragility: synthesizing failure domains the
/// graph does not record would fabricate exactly the evidence the fragility metrics measure.
struct LimitationCase {
    storage: Storage,
    runtime: FixtureRuntime,
    request: QueryRequest,
}

impl LimitationCase {
    fn new() -> Self {
        let storage: Storage = Rc::new(InMemoryNeuralGraphStorage::default());
        let runtime = build_runtime(67, "real-storage-limits").unwrap();
        let request = make_request(&runtime);
        let left_message = make_message("message-left", MESSAGE_CONTENTS[0]);
        let right_message = make_message("message-right", MESSAGE_CONTENTS[1]);
        let left = make_episode("episode-left", std::slice::from_ref(&left_message), "LEFT_VALUE_1", &EMBEDDING, None);
        let right = make_episode(
            "episode-right",
            std::slice::from_ref(&right_message),
            "RIGHT_VALUE_1",
            &SECOND_EMBEDDING,
            None,
        );
        left.borrow_mut().metadata.insert("slot".into(), json!("left"));
        right.borrow_mut().metadata.insert("slot".into(), json!("right"));
        consolidate_into_episode(&storage, &left, &[left_message]);
        consolidate_into_episode(&storage, &right, &[right_message]);
        LimitationCase { storage, runtime, request }
    }

    /// An owner-supplied projection that emits the slots synthesis needs.
    fn make_slot_adapter(&self) -> NeuralGraphMemoryAdapter {
        with_resolver(make_adapter(&self.runtime, episode_search(&self.storage), allow_everything()), &self.storage)
            .with_claim_projection(Box::new(|node| {
                let metadata = node.getattr("metadata").unwrap();
                let LocalValue::Dict(metadata) = metadata else { panic!("metadata is a mapping") };
                LocalValue::dict(vec![
                    ("slot", LocalValue::str(&metadata.get("slot").unwrap().py_str())),
                    ("value", LocalValue::str(&node.getattr("content").unwrap().py_str())),
                ])
            }))
    }

    fn coordinator(&self, adapter: NeuralGraphMemoryAdapter) -> TesseractCoordinator {
        let registry = Rc::new(CapabilityRegistry::new());
        registry.register(Rc::new(adapter)).unwrap();
        TesseractCoordinator::new(
            registry.clone(),
            Router::new(registry, self.runtime.failure.clone()),
            ClaimNormalizer,
            RuleBasedSynthesizer::new(required_slots()),
            LineageAnalyzer,
            self.runtime.traces.clone(),
        )
    }
}

#[test]
fn test_absent_failure_domains_zero_every_coalition_metric() {
    let case = LimitationCase::new();
    let exports = case.make_slot_adapter().query(&case.request).unwrap();
    let raw: Vec<_> = exports.iter().flat_map(|e| e.claims.iter().cloned()).collect();
    let claims = ClaimNormalizer.normalize(&raw, &case.request.query_id);
    let synthesis = RuleBasedSynthesizer::new(required_slots()).synthesize(&claims).unwrap();
    let analyzer = LineageAnalyzer;

    let metrics = analyzer.score(&claims, &required_slots(), &synthesis);

    // Real lineage roots are known; failure domains are not modelled at all.
    assert_eq!(claims.len(), 2);
    assert!(synthesis.success);
    assert_eq!(metrics.unique_lineage_root_count, 2);
    assert_eq!(metrics.unique_failure_domain_count, 0);
    // Therefore every coalition metric reads 0. That is missing evidence, not a demonstration of robustness.
    assert!(analyzer.reconstruction_coalitions(&claims, &required_slots()).is_empty());
    assert_eq!(metrics.reconstruction_coalition_count, 0);
    assert_eq!(metrics.minimal_support_coalition_size, 0);
    assert_eq!(metrics.minimal_failure_domain_cut, 0);
}

#[test]
fn test_uncovered_slot_short_circuits_coalition_analysis() {
    let case = LimitationCase::new();
    let exports = case.make_slot_adapter().query(&case.request).unwrap();
    let raw: Vec<_> = exports.iter().flat_map(|e| e.claims.iter().cloned()).collect();
    let claims = ClaimNormalizer.normalize(&raw, &case.request.query_id);
    let left_only: Vec<_> = claims.into_iter().filter(|c| c.content["slot"] == "left").collect();

    assert_eq!(left_only.len(), 1);
    assert!(LineageAnalyzer.reconstruction_coalitions(&left_only, &required_slots()).is_empty());
}

#[test]
fn test_default_text_projection_is_dropped_before_synthesis() {
    // The default `{"text": ...}` claim carries no slot, so it is normalized away.
    let case = LimitationCase::new();
    let coordinator = case.coordinator(with_resolver(
        make_adapter(&case.runtime, episode_search(&case.storage), allow_everything()),
        &case.storage,
    ));

    let execution = coordinator.execute(&case.request, "real-storage", None).unwrap();

    // The evidence really was exported and really carries lineage; the drop happens at normalization.
    assert_eq!(execution.route, strs(&[REAL_NODE_ID]));
    assert_eq!(execution.exports.len(), 2);
    let exported: Vec<_> = execution.exports.iter().flat_map(|e| e.claims.iter()).collect();
    assert_eq!(exported.len(), 2);
    let texts: BTreeSet<&str> = exported.iter().map(|c| c.content["text"].as_str().unwrap()).collect();
    assert_eq!(texts, BTreeSet::from(["LEFT_VALUE_1", "RIGHT_VALUE_1"]));
    assert!(exported.iter().all(|c| !c.lineage_root_ids.is_empty()));
    assert!(execution.claims.is_empty());
    assert!(!execution.synthesis.success);
    assert_eq!(execution.metrics.unique_lineage_root_count, 0);
}

#[test]
fn test_slot_projection_lets_the_same_evidence_reach_synthesis() {
    // Positive control: the drop is caused by the projection, not the adapter.
    let case = LimitationCase::new();
    let coordinator = case.coordinator(case.make_slot_adapter());

    let execution = coordinator.execute(&case.request, "real-storage", None).unwrap();

    assert_eq!(execution.claims.len(), 2);
    assert!(execution.synthesis.success);
    assert_eq!(execution.synthesis.answer.as_deref(), Some("LEFT_VALUE_1:RIGHT_VALUE_1"));
    assert_eq!(execution.metrics.unique_lineage_root_count, 2);
    assert_eq!(execution.metrics.reconstruction_coalition_count, 0);
}
