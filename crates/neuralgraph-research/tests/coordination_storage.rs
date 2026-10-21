//! SQLite-backed coordination tests: ports of `NeuralGraph/tests/test_coordination_storage.py` (lineage fields
//! survive a durable round trip) and `NeuralGraph/tests/test_coordination_experiment_restart.py` (multi-node
//! reconstruction survives closing and reopening per-node stores), same test names and assertions.
//!
//! Every test needs `SQLiteNeuralGraphStorage`, which belongs to the `retrieval::sqlite_storage` port (a stub in
//! this tree when these were written). The file is therefore compiled out by the crate-level `cfg(any())`;
//! enabling it is deleting that attribute once the store lands. The only assumed API is
//! `SQLiteNeuralGraphStorage::new(db_path)` and `close()`, both used through `open_sqlite` / `close` below; every
//! store gets an explicit temporary `db_path` (the Python default points at `~/.memmachine/memories.db`).
#![cfg(any())]

use neuralgraph_research::coordination::adapters::{LocalNodeRef, LocalValue, NeuralGraphMemoryAdapter};
use neuralgraph_research::coordination::contracts::{
    strs, AuthorizationContext, Availability, CapabilityDescriptor, PolicyStatus, QueryBudget, QueryRequest,
};
use neuralgraph_research::coordination::core::{
    clock_fn, CapabilityRegistry, ClaimNormalizer, FailureInjector, LineageAnalyzer, LogicalClock, QueryExecution,
    Router, RuleBasedSynthesizer, TesseractCoordinator, TraceLogger,
};
use neuralgraph_research::coordination::storage_adapter::StorageLineageResolver;
use neuralgraph_research::retrieval::data_types::{
    edge_ref, node_ref, EdgeType, NeuralEdge, NeuralNode, NodeLayer, NodeRef,
};
use neuralgraph_research::retrieval::sqlite_storage::SQLiteNeuralGraphStorage;
use neuralgraph_research::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::rc::Rc;

fn open_sqlite(db_path: &Path) -> Rc<SQLiteNeuralGraphStorage> {
    Rc::new(SQLiteNeuralGraphStorage::new(db_path))
}

fn close(storage: &SQLiteNeuralGraphStorage) {
    storage.close();
}

// =================================================================================================================
// test_coordination_storage.py
// =================================================================================================================

const SESSION_KEY: &str = "coordination-storage-session";
const SOURCE_MEMORY_IDS: [&str; 3] = ["memory-root-1", "memory-root-2", "memory-root-3"];

/// A node carrying lineage the coordination adapter reads.
fn make_node(node_id: &str, source_memory_ids: Option<&[&str]>) -> NodeRef {
    let mut node = NeuralNode::new(node_id, NodeLayer::Episode, "locally held fact");
    node.embedding = Some(vec![0.25, 0.5, 0.75]);
    node.session_key = SESSION_KEY.into();
    node.source_memory_ids = strs(source_memory_ids.unwrap_or(&SOURCE_MEMORY_IDS));
    node_ref(node)
}

/// `SQLiteTempDatabaseTestCase`: one store pinned to a temporary database file.
struct SqliteCase {
    _tmpdir: tempfile::TempDir,
    db_path: PathBuf,
    storage: Rc<SQLiteNeuralGraphStorage>,
}

impl SqliteCase {
    fn new() -> Self {
        let tmpdir = tempfile::tempdir().unwrap();
        let db_path = tmpdir.path().join("coordination_storage_test.db");
        let storage = open_sqlite(&db_path);
        SqliteCase { _tmpdir: tmpdir, db_path, storage }
    }

    /// The stored JSON blob, read through an independent connection (what is durable on disk).
    fn read_raw_data_blob(&self, node_id: &str) -> Value {
        let connection = rusqlite::Connection::open(&self.db_path).unwrap();
        let row: String = connection
            .query_row("SELECT data FROM nodes WHERE node_id = ?", [node_id], |r| r.get(0))
            .unwrap_or_else(|_| panic!("node {node_id} was not persisted to {}", self.db_path.display()));
        serde_json::from_str(&row).unwrap()
    }

    const LEGACY_NODE_ID: &'static str = "legacy-node";

    /// A row whose JSON blob has the pre-fix key set (no lineage field), written through an independent
    /// connection so it is genuinely foreign to the current serializer.
    fn insert_legacy_row(&self) {
        let created_at = "2026-01-01T00:00:00+00:00";
        let legacy_data = json!({
            "node_id": Self::LEGACY_NODE_ID,
            "session_key": SESSION_KEY,
            "layer": NodeLayer::Episode.value(),
            "content": "row persisted before source_memory_ids was serialized",
            "embedding": null,
            "wave_amplitudes": {},
            "activation_level": 0.0,
            "last_activated": created_at,
            "refractory_until": null,
            "heat_score": 0.3,
            "importance_score": 0.5,
            "consolidation_state": "active",
            "parent_id": null,
            "child_ids": [],
            "entity_ids": [],
            "edge_type_counts": {},
            "dominant_dimension": "",
            "diversity_score": 0.0,
            "created_at": created_at,
            "metadata": {},
        });
        assert!(legacy_data.get("source_memory_ids").is_none());
        let connection = rusqlite::Connection::open(&self.db_path).unwrap();
        connection
            .execute(
                "INSERT INTO nodes (node_id, session_key, layer, content, embedding, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    Self::LEGACY_NODE_ID,
                    SESSION_KEY,
                    NodeLayer::Episode.value().to_string(),
                    legacy_data["content"].as_str().unwrap(),
                    Option::<String>::None,
                    neuralgraph_memory::pycompat::dumps(&legacy_data, &neuralgraph_memory::pycompat::JsonFormat::python_default()),
                    created_at,
                ],
            )
            .unwrap();
    }
}

impl Drop for SqliteCase {
    fn drop(&mut self) {
        close(&self.storage);
    }
}

fn source_ids() -> Vec<String> {
    strs(&SOURCE_MEMORY_IDS)
}

#[test]
fn test_save_node_then_get_node_preserves_source_memory_ids() {
    let case = SqliteCase::new();
    let node = make_node("node-1", None);
    case.storage.save_node(&node);
    let loaded = case.storage.get_node("node-1").expect("loaded");
    assert_eq!(loaded.borrow().source_memory_ids, source_ids());
}

#[test]
fn test_persisted_data_blob_contains_source_memory_ids() {
    let case = SqliteCase::new();
    case.storage.save_node(&make_node("node-1", None));
    let blob = case.read_raw_data_blob("node-1");
    assert!(blob.get("source_memory_ids").is_some());
    assert_eq!(blob["source_memory_ids"], json!(SOURCE_MEMORY_IDS));
}

#[test]
fn test_get_nodes_by_session_preserves_source_memory_ids() {
    let case = SqliteCase::new();
    case.storage.save_node(&make_node("node-1", None));
    let nodes = case.storage.get_nodes_by_session(SESSION_KEY, None);
    assert_eq!(nodes.iter().map(|n| n.borrow().node_id.clone()).collect::<Vec<_>>(), strs(&["node-1"]));
    assert_eq!(nodes[0].borrow().source_memory_ids, source_ids());
}

#[test]
fn test_get_all_nodes_preserves_source_memory_ids() {
    let case = SqliteCase::new();
    case.storage.save_node(&make_node("node-1", None));
    let nodes = case.storage.get_all_nodes(SESSION_KEY);
    assert_eq!(nodes.iter().map(|n| n.borrow().node_id.clone()).collect::<Vec<_>>(), strs(&["node-1"]));
    assert_eq!(nodes[0].borrow().source_memory_ids, source_ids());
}

#[test]
fn test_vector_search_preserves_source_memory_ids() {
    let case = SqliteCase::new();
    case.storage.save_node(&make_node("node-1", None));
    let results = case.storage.vector_search(&[0.25, 0.5, 0.75], SESSION_KEY, 10, None);
    assert_eq!(results.iter().map(|(n, _)| n.borrow().node_id.clone()).collect::<Vec<_>>(), strs(&["node-1"]));
    assert_eq!(results[0].0.borrow().source_memory_ids, source_ids());
}

#[test]
fn test_save_nodes_batch_preserves_source_memory_ids() {
    let case = SqliteCase::new();
    let first = make_node("batch-node-1", Some(&["memory-a"]));
    let second = make_node("batch-node-2", Some(&["memory-b", "memory-c"]));
    let saved = case.storage.save_nodes_batch(&[first, second]);
    assert_eq!(saved, 2);
    assert_eq!(case.storage.get_node("batch-node-1").unwrap().borrow().source_memory_ids, strs(&["memory-a"]));
    assert_eq!(
        case.storage.get_node("batch-node-2").unwrap().borrow().source_memory_ids,
        strs(&["memory-b", "memory-c"])
    );
}

#[test]
fn test_node_without_lineage_round_trips_as_empty_list() {
    let case = SqliteCase::new();
    case.storage.save_node(&make_node("no-lineage-node", Some(&[])));
    assert!(case.storage.get_node("no-lineage-node").unwrap().borrow().source_memory_ids.is_empty());
}

#[test]
fn test_sqlite_round_trip_matches_neuralnode_dict_round_trip() {
    let case = SqliteCase::new();
    let node = make_node("node-1", None);
    case.storage.save_node(&node);
    let from_storage = case.storage.get_node("node-1").unwrap();
    let dict = node.borrow().to_dict();
    let from_dict = NeuralNode::from_dict(dict.as_object().unwrap()).unwrap();
    assert_eq!(from_storage.borrow().source_memory_ids, from_dict.source_memory_ids);
    assert_eq!(from_storage.borrow().source_memory_ids, node.borrow().source_memory_ids);
}

#[test]
fn test_legacy_row_loads_without_raising_and_yields_empty_lineage() {
    let case = SqliteCase::new();
    case.insert_legacy_row();
    let loaded = case.storage.get_node(SqliteCase::LEGACY_NODE_ID).expect("legacy row loads");
    assert!(loaded.borrow().source_memory_ids.is_empty());
}

#[test]
fn test_legacy_row_keeps_its_other_fields() {
    // Guards against a load that "succeeds" by discarding the rest of the row.
    let case = SqliteCase::new();
    case.insert_legacy_row();
    let loaded = case.storage.get_node(SqliteCase::LEGACY_NODE_ID).unwrap();
    let loaded = loaded.borrow();
    assert_eq!(loaded.node_id, SqliteCase::LEGACY_NODE_ID);
    assert_eq!(loaded.session_key, SESSION_KEY);
    assert_eq!(loaded.layer, NodeLayer::Episode);
    assert_eq!(loaded.content, "row persisted before source_memory_ids was serialized");
}

#[test]
fn test_legacy_row_is_upgraded_once_it_is_saved_again() {
    let case = SqliteCase::new();
    case.insert_legacy_row();
    let loaded = case.storage.get_node(SqliteCase::LEGACY_NODE_ID).unwrap();
    loaded.borrow_mut().source_memory_ids = strs(&["memory-added-later"]);
    case.storage.save_node(&loaded);
    let reloaded = case.storage.get_node(SqliteCase::LEGACY_NODE_ID).unwrap();
    assert_eq!(reloaded.borrow().source_memory_ids, strs(&["memory-added-later"]));
}

#[test]
fn test_inmemory_and_sqlite_agree_on_source_memory_ids() {
    let case = SqliteCase::new();
    let node = make_node("agreement-node", None);
    let in_memory = InMemoryNeuralGraphStorage::default();
    in_memory.save_node(&node);
    case.storage.save_node(&node);
    let from_memory = in_memory.get_node("agreement-node").unwrap();
    let from_sqlite = case.storage.get_node("agreement-node").unwrap();
    assert_eq!(from_memory.borrow().source_memory_ids, source_ids());
    assert_eq!(from_sqlite.borrow().source_memory_ids, from_memory.borrow().source_memory_ids);
}

#[test]
fn test_backends_agree_on_empty_lineage() {
    let case = SqliteCase::new();
    let node = make_node("agreement-empty-node", Some(&[]));
    let in_memory = InMemoryNeuralGraphStorage::default();
    in_memory.save_node(&node);
    case.storage.save_node(&node);
    let from_memory = in_memory.get_node("agreement-empty-node").unwrap();
    let from_sqlite = case.storage.get_node("agreement-empty-node").unwrap();
    assert_eq!(from_sqlite.borrow().source_memory_ids, from_memory.borrow().source_memory_ids);
    assert!(from_sqlite.borrow().source_memory_ids.is_empty());
}

// =================================================================================================================
// test_coordination_experiment_restart.py
// =================================================================================================================

const CAPABILITY_ID: &str = "restart_pair_reconstruction";
const RESTART_SCOPE: &str = "capability:restart-pair";
const RESTART_SESSION_KEY: &str = "restart-experiment-session";
const RESTART_EMBEDDING: [f64; 3] = [0.42, 0.17, 0.9];
const NODE_A_ID: &str = "restart-node-a";
const NODE_B_ID: &str = "restart-node-b";
const EPISODE_A_ID: &str = "episode-left";
const EPISODE_B_ID: &str = "episode-right";
const ORIGIN_A_ID: &str = "message-left-origin";
const ORIGIN_B_ID: &str = "message-right-origin";
const LEFT_VALUE: &str = "LEFT_HALF_9F2Q";
const RIGHT_VALUE: &str = "RIGHT_HALF_3KX7";
const EXPECTED_ANSWER: &str = "LEFT_HALF_9F2Q:RIGHT_HALF_3KX7";

/// One derived episode plus the origin message it derives from (the consolidation shape: the episode's own
/// `source_memory_ids` names its origin and a HIERARCHY edge runs episode -> origin).
fn seed_slot_episode(storage: &SQLiteNeuralGraphStorage, episode_id: &str, origin_id: &str, slot: &str, value: &str) {
    let mut episode = NeuralNode::new(episode_id, NodeLayer::Episode, value);
    episode.embedding = Some(RESTART_EMBEDDING.to_vec());
    episode.session_key = RESTART_SESSION_KEY.into();
    episode.child_ids = strs(&[origin_id]);
    episode.source_memory_ids = strs(&[origin_id]);
    episode.metadata.insert("slot".into(), json!(slot));
    storage.save_node(&node_ref(episode));
    let mut origin = NeuralNode::new(origin_id, NodeLayer::Message, format!("raw-{value}"));
    origin.embedding = Some(RESTART_EMBEDDING.to_vec());
    origin.session_key = RESTART_SESSION_KEY.into();
    origin.parent_id = Some(episode_id.into());
    storage.save_node(&node_ref(origin));
    storage.save_edge(&edge_ref(
        NeuralEdge::new(format!("edge-{episode_id}-{origin_id}"), episode_id, origin_id, EdgeType::Hierarchy)
            .with_weight(1.0),
    ));
}

fn restart_adapter(
    node_id: &str,
    storage: Rc<SQLiteNeuralGraphStorage>,
    clock: &Rc<LogicalClock>,
) -> NeuralGraphMemoryAdapter {
    let capability = CapabilityDescriptor {
        capability_id: CAPABILITY_ID.into(),
        node_id: node_id.into(),
        description: "local NeuralGraph retrieval over real storage".into(),
        query_types: strs(&["composition"]),
        policy_scope: strs(&[RESTART_SCOPE]),
        availability: Availability::Available,
    };
    let search = storage.clone();
    NeuralGraphMemoryAdapter::new(
        node_id,
        capability,
        Box::new(move |_| {
            Ok(search
                .vector_search(&RESTART_EMBEDDING, RESTART_SESSION_KEY, 8, Some(&[NodeLayer::Episode]))
                .into_iter()
                .map(|(n, s)| (n as LocalNodeRef, s))
                .collect())
        }),
        Box::new(|_, _| PolicyStatus::Allowed),
        clock_fn(clock),
    )
    .with_lineage_resolver(StorageLineageResolver::new(storage).into_resolver())
    .with_claim_projection(Box::new(|node| {
        let LocalValue::Dict(metadata) = node.getattr("metadata").unwrap() else { panic!("mapping") };
        LocalValue::dict(vec![
            ("slot", metadata.get("slot").cloned().unwrap_or(LocalValue::None)),
            ("value", node.getattr("content").unwrap()),
        ])
    }))
}

fn restart_query(seed: i64) -> QueryRequest {
    let issued_at = LogicalClock::new(seed).now();
    QueryRequest {
        query_id: format!("restart_query_{seed}"),
        content: "Reconstruct the two-part answer split across independent nodes".into(),
        requested_capability: Some(CAPABILITY_ID.into()),
        requester_id: "experiment-controller".into(),
        issued_at: issued_at.clone(),
        valid_at: Some(issued_at),
        authorization: AuthorizationContext::new(strs(&[RESTART_SCOPE]), strs(&[NODE_A_ID, NODE_B_ID]), false).unwrap(),
        budget: QueryBudget::new(2, 8, 0).unwrap(),
    }
}

/// A fresh coordinator over the given stores, executed once: nothing is carried between calls except what lives
/// in the two SQLite files.
fn reconstruct(
    storage_a: Rc<SQLiteNeuralGraphStorage>,
    storage_b: Rc<SQLiteNeuralGraphStorage>,
    seed: i64,
    phase: &str,
) -> QueryExecution {
    let clock = Rc::new(LogicalClock::new(seed));
    let traces = Rc::new(TraceLogger::new(format!("restart_run_{seed}"), clock.clone()));
    let failure = Rc::new(FailureInjector::new(format!("restart_run_{seed}"), seed, clock.clone(), traces.clone()));
    let registry = Rc::new(CapabilityRegistry::new());
    registry.register(Rc::new(restart_adapter(NODE_A_ID, storage_a, &clock))).unwrap();
    registry.register(Rc::new(restart_adapter(NODE_B_ID, storage_b, &clock))).unwrap();
    let coordinator = TesseractCoordinator::new(
        registry.clone(),
        Router::new(registry, failure),
        ClaimNormalizer,
        RuleBasedSynthesizer::new(strs(&["left", "right"])),
        LineageAnalyzer,
        traces,
    );
    coordinator.execute(&restart_query(seed), phase, Some(&strs(&[NODE_A_ID, NODE_B_ID]))).unwrap()
}

struct RestartCase {
    _tmpdir: tempfile::TempDir,
    path_a: PathBuf,
    path_b: PathBuf,
}

impl RestartCase {
    fn new() -> Self {
        let tmpdir = tempfile::tempdir().unwrap();
        let path_a = tmpdir.path().join("node_a.db");
        let path_b = tmpdir.path().join("node_b.db");
        RestartCase { _tmpdir: tmpdir, path_a, path_b }
    }

    fn seed_both_nodes(&self) {
        let storage_a = open_sqlite(&self.path_a);
        let storage_b = open_sqlite(&self.path_b);
        seed_slot_episode(&storage_a, EPISODE_A_ID, ORIGIN_A_ID, "left", LEFT_VALUE);
        seed_slot_episode(&storage_b, EPISODE_B_ID, ORIGIN_B_ID, "right", RIGHT_VALUE);
        close(&storage_a);
        close(&storage_b);
    }
}

#[test]
fn test_no_single_node_stores_the_full_answer() {
    let case = RestartCase::new();
    case.seed_both_nodes();
    let storage_a = open_sqlite(&case.path_a);
    let storage_b = open_sqlite(&case.path_b);
    let episode_a = storage_a.get_node(EPISODE_A_ID).unwrap();
    let episode_b = storage_b.get_node(EPISODE_B_ID).unwrap();
    close(&storage_a);
    close(&storage_b);
    assert_ne!(episode_a.borrow().content, EXPECTED_ANSWER);
    assert_ne!(episode_b.borrow().content, EXPECTED_ANSWER);
    assert!(!episode_a.borrow().content.contains(RIGHT_VALUE));
    assert!(!episode_b.borrow().content.contains(LEFT_VALUE));
}

#[test]
fn test_reconstruction_survives_a_storage_restart() {
    let case = RestartCase::new();
    case.seed_both_nodes();

    let storage_a = open_sqlite(&case.path_a);
    let storage_b = open_sqlite(&case.path_b);
    let before = reconstruct(storage_a.clone(), storage_b.clone(), 7001, "before_restart");
    // The actual restart: close both connections, then open brand new stores against the same files.
    close(&storage_a);
    close(&storage_b);

    assert!(before.synthesis.success);
    assert_eq!(before.synthesis.answer.as_deref(), Some(EXPECTED_ANSWER));
    assert_eq!(before.synthesis.covered_slots, strs(&["left", "right"]));
    // No single node produced the whole answer: two distinct producers, each contributing one slot.
    assert_eq!(before.synthesis.selected_node_ids.iter().collect::<BTreeSet<_>>().len(), 2);
    assert_eq!(before.metrics.unique_lineage_root_count, 2);

    let restarted_a = open_sqlite(&case.path_a);
    let restarted_b = open_sqlite(&case.path_b);
    let after = reconstruct(restarted_a.clone(), restarted_b.clone(), 7001, "after_restart");
    close(&restarted_a);
    close(&restarted_b);

    assert!(after.synthesis.success);
    assert_eq!(after.synthesis.answer.as_deref(), Some(EXPECTED_ANSWER));
    assert_eq!(after.synthesis.confidence, before.synthesis.confidence);
    assert_eq!(after.synthesis.covered_slots, before.synthesis.covered_slots);

    // Claim and trace identity derive from (query_id, node_id, memory_id) only, so a restart with the same seed
    // must reproduce them exactly.
    let ids = |e: &QueryExecution| e.claims.iter().map(|c| c.claim_id.clone()).collect::<BTreeSet<_>>();
    assert_eq!(ids(&before), ids(&after));
    let roots = |e: &QueryExecution| {
        e.claims.iter().map(|c| (c.claim_id.clone(), c.lineage_root_ids.clone())).collect::<HashMap<_, _>>()
    };
    assert_eq!(roots(&before), roots(&after));
    let after_roots: BTreeSet<Vec<String>> = after.claims.iter().map(|c| c.lineage_root_ids.clone()).collect();
    assert_eq!(after_roots, BTreeSet::from([strs(&[ORIGIN_A_ID]), strs(&[ORIGIN_B_ID])]));
}
