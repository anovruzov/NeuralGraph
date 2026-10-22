//! Parity of the SQLite store with Python (`tests/golden/sqlite_storage.json`, written by `gen_sqlite_storage.py`):
//! the recorded operation script is replayed step by step, including byte-level dumps of the database file, plus
//! the Python unit tests of `NeuralGraph/tests/test_coordination_storage.py`.

mod common;

use common::{assert_json_eq, load, pin_now, s};
use neuralgraph_research::retrieval::sqlite_storage::SqliteNeuralGraphStorage;
use neuralgraph_research::retrieval::{
    edge_ref, node_ref, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge, NeuralGraphStorage, NeuralNode, NodeLayer,
    NodeRef,
};
use rusqlite::types::ValueRef;
use serde_json::{json, Value};
use std::path::Path;

fn node_from(v: &Value) -> NodeRef {
    node_ref(NeuralNode::from_dict(v.as_object().unwrap()).expect("node dict"))
}

fn edge_from(v: &Value) -> neuralgraph_research::retrieval::EdgeRef {
    edge_ref(NeuralEdge::from_dict(v.as_object().unwrap()).expect("edge dict"))
}

fn nd(n: Option<NodeRef>) -> Value {
    n.map(|n| n.borrow().to_dict()).unwrap_or(Value::Null)
}

fn nds(ns: Vec<NodeRef>) -> Value {
    json!(ns.into_iter().map(|n| n.borrow().to_dict()).collect::<Vec<_>>())
}

fn eds(es: Vec<neuralgraph_research::retrieval::EdgeRef>) -> Value {
    json!(es.into_iter().map(|e| e.borrow().to_dict()).collect::<Vec<_>>())
}

fn layer(v: &Value) -> Option<NodeLayer> {
    v.as_i64().map(|l| NodeLayer::from_value(l).unwrap())
}

fn edge_types(v: &Value) -> Option<Vec<EdgeType>> {
    v.as_array().map(|a| a.iter().map(|t| EdgeType::from_value(s(t)).unwrap()).collect())
}

fn opt_str(v: &Value) -> Option<&str> {
    v.as_str()
}

/// The raw database, read with an independent connection (the generator's `raw_dump`).
fn raw_dump(path: &Path) -> Value {
    let conn = rusqlite::Connection::open(path).unwrap();
    let rows = |sql: &str| -> Value {
        let mut stmt = conn.prepare(sql).unwrap();
        let n = stmt.column_count();
        let out: Vec<Value> = stmt
            .query_map([], |row| {
                Ok(json!((0..n)
                    .map(|i| match row.get_ref(i).unwrap() {
                        ValueRef::Null => Value::Null,
                        ValueRef::Integer(x) => json!(x),
                        ValueRef::Real(x) => json!(x),
                        ValueRef::Text(t) => json!(String::from_utf8(t.to_vec()).unwrap()),
                        ValueRef::Blob(b) => json!(b),
                    })
                    .collect::<Vec<_>>()))
            })
            .unwrap()
            .map(Result::unwrap)
            .collect();
        json!(out)
    };
    json!({
        "master": rows("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid"),
        "nodes": rows(
            "SELECT node_id, session_key, layer, typeof(layer), content, hex(embedding), typeof(embedding), data, \
             created_at FROM nodes ORDER BY rowid"
        ),
        "edges": rows("SELECT edge_id, source_id, target_id, edge_type, data FROM edges ORDER BY rowid"),
        "categories": rows("SELECT * FROM categories ORDER BY rowid"),
        "sqlite_sequence": rows("SELECT name, seq FROM sqlite_sequence ORDER BY rowid"),
    })
}

#[test]
fn operation_script_matches() {
    pin_now(common::FIXED_NOW);
    let f = load("sqlite_storage.json");
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("nested").join("graph.db");
    let mut store = Some(SqliteNeuralGraphStorage::new(&path));
    for (i, step) in f["steps"].as_array().unwrap().iter().enumerate() {
        let op = s(&step["op"]);
        let a = &step["args"];
        let what = format!("step {i} {op} {a}");
        let st = store.as_ref();
        let got = match op {
            "raw" => raw_dump(&path),
            "save_node" | "update_node" => {
                st.unwrap().update_node(&node_from(&a["node"]));
                Value::Null
            }
            "save_nodes_batch" => {
                let ns: Vec<NodeRef> = a["nodes"].as_array().unwrap().iter().map(node_from).collect();
                json!(st.unwrap().save_nodes_batch(&ns))
            }
            "save_edge" => {
                st.unwrap().save_edge(&edge_from(&a["edge"]));
                Value::Null
            }
            "save_edges_batch" => {
                let es: Vec<_> = a["edges"].as_array().unwrap().iter().map(edge_from).collect();
                json!(st.unwrap().save_edges_batch(&es))
            }
            "get_node" => nd(st.unwrap().get_node(s(&a["node_id"]))),
            "get_nodes_by_session" => nds(st.unwrap().get_nodes_by_session(s(&a["session_key"]), layer(&a["layer"]))),
            "get_nodes_by_layer" => {
                nds(st.unwrap().get_nodes_by_layer(s(&a["session_key"]), layer(&a["layer"]).unwrap()))
            }
            "get_all_nodes" => nds(st.unwrap().get_all_nodes(s(&a["session_key"]))),
            "get_all_edges" => eds(st.unwrap().get_all_edges(s(&a["session_key"]))),
            "get_edge" => st.unwrap().get_edge(s(&a["edge_id"])).map(|e| e.borrow().to_dict()).unwrap_or(Value::Null),
            "get_edges_from" => {
                eds(st.unwrap().get_edges_from(s(&a["node_id"]), edge_types(&a["edge_types"]).as_deref()))
            }
            "get_edges_to" => eds(st.unwrap().get_edges_to(s(&a["node_id"]), edge_types(&a["edge_types"]).as_deref())),
            "get_edge_between" => st
                .unwrap()
                .get_edge_between(
                    s(&a["source_id"]),
                    s(&a["target_id"]),
                    a["edge_type"].as_str().map(|t| EdgeType::from_value(t).unwrap()),
                )
                .map(|e| e.borrow().to_dict())
                .unwrap_or(Value::Null),
            "vector_search" => {
                let q = common::f64s(&a["query"]);
                let lf: Option<Vec<NodeLayer>> =
                    a["layer_filter"].as_array().map(|l| l.iter().map(|x| layer(x).unwrap()).collect());
                let res = st.unwrap().vector_search(
                    &q,
                    s(&a["session_key"]),
                    a["limit"].as_u64().unwrap() as usize,
                    lf.as_deref(),
                );
                json!(res.into_iter().map(|(n, sc)| json!([n.borrow().to_dict(), sc])).collect::<Vec<_>>())
            }
            "get_session_statistics" => st.unwrap().get_session_statistics(s(&a["session_key"])),
            "delete_edge" => json!(st.unwrap().delete_edge(s(&a["edge_id"]))),
            "delete_node" => json!(st.unwrap().delete_node(s(&a["node_id"]))),
            "create_category" => {
                match st.unwrap().create_category(s(&a["name"]), opt_str(&a["description"]), opt_str(&a["domain"])) {
                    Ok(id) => json!(id),
                    Err(e) => json!({"error": e}),
                }
            }
            "list_categories" => json!(st.unwrap().list_categories(opt_str(&a["domain"]))),
            "create_domain" => match st.unwrap().create_domain(s(&a["name"]), opt_str(&a["description"])) {
                Ok(id) => json!(id),
                Err(e) => json!({"error": e}),
            },
            "list_domains" => json!(st.unwrap().list_domains()),
            "list_sessions" => {
                json!(st.unwrap().list_sessions(a["limit"].as_i64().unwrap(), a["offset"].as_i64().unwrap()))
            }
            "clear_session" => json!(st.unwrap().clear_session(s(&a["session_key"]))),
            "reopen_get_node" => {
                store.take().unwrap().close();
                let reopened = SqliteNeuralGraphStorage::new(&path);
                let n = nd(reopened.get_node(s(&a["node_id"])));
                reopened.close();
                n
            }
            other => panic!("unknown op {other}"),
        };
        assert_json_eq(&got, &step["result"], &what);
    }
}

#[test]
fn legacy_rows_load() {
    pin_now(common::FIXED_NOW);
    let f = load("sqlite_storage.json");
    let leg = &f["legacy"];
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let store = SqliteNeuralGraphStorage::new(&path);
    let conn = rusqlite::Connection::open(&path).unwrap();
    for row in leg["rows"].as_array().unwrap() {
        let d = &row[0];
        let blob: Option<Vec<u8>> = row[2]
            .as_str()
            .map(|h| (0..h.len()).step_by(2).map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap()).collect());
        let data = neuralgraph_memory::pycompat::dumps(d, &neuralgraph_memory::pycompat::JsonFormat::python_default());
        conn.execute(
            "INSERT INTO nodes (node_id, session_key, layer, content, embedding, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rusqlite::params![s(&d["node_id"]), "s", s(&row[1]), s(&d["content"]), blob, data, s(&leg["created_at"])],
        )
        .unwrap();
    }
    let got = json!([nd(store.get_node("legacy-node")), nd(store.get_node("minimal"))]);
    assert_json_eq(&got, &leg["loaded"], "legacy rows");
}

// ---- NeuralGraph/tests/test_coordination_storage.py -------------------------------------------------------

const SESSION_KEY: &str = "coordination-storage-session";
const SOURCE_MEMORY_IDS: [&str; 3] = ["memory-root-1", "memory-root-2", "memory-root-3"];

fn make_node(node_id: &str, source_memory_ids: Option<&[&str]>) -> NodeRef {
    let mut n = NeuralNode::new(node_id, NodeLayer::Episode, "locally held fact");
    n.embedding = Some(vec![0.25, 0.5, 0.75]);
    n.session_key = SESSION_KEY.into();
    n.source_memory_ids = source_memory_ids.unwrap_or(&SOURCE_MEMORY_IDS).iter().map(|s| s.to_string()).collect();
    node_ref(n)
}

struct TempDb {
    _dir: tempfile::TempDir,
    path: std::path::PathBuf,
    storage: SqliteNeuralGraphStorage,
}

fn temp_db() -> TempDb {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("coordination_storage_test.db");
    let storage = SqliteNeuralGraphStorage::new(&path);
    TempDb { _dir: dir, path, storage }
}

fn ids(n: &NodeRef) -> Vec<String> {
    n.borrow().source_memory_ids.clone()
}

fn want(ids: &[&str]) -> Vec<String> {
    ids.iter().map(|s| s.to_string()).collect()
}

#[test]
fn save_node_then_get_node_preserves_source_memory_ids() {
    let db = temp_db();
    db.storage.save_node(&make_node("node-1", None));
    assert_eq!(ids(&db.storage.get_node("node-1").unwrap()), want(&SOURCE_MEMORY_IDS));
}

#[test]
fn persisted_data_blob_contains_source_memory_ids() {
    let db = temp_db();
    db.storage.save_node(&make_node("node-1", None));
    let conn = rusqlite::Connection::open(&db.path).unwrap();
    let data: String = conn.query_row("SELECT data FROM nodes WHERE node_id = ?", ["node-1"], |r| r.get(0)).unwrap();
    let blob: Value = serde_json::from_str(&data).unwrap();
    assert_eq!(blob["source_memory_ids"], json!(SOURCE_MEMORY_IDS));
}

#[test]
fn session_reads_and_vector_search_preserve_source_memory_ids() {
    let db = temp_db();
    db.storage.save_node(&make_node("node-1", None));
    let nodes = db.storage.get_nodes_by_session(SESSION_KEY, None);
    assert_eq!(nodes.len(), 1);
    assert_eq!(ids(&nodes[0]), want(&SOURCE_MEMORY_IDS));
    let all = db.storage.get_all_nodes(SESSION_KEY);
    assert_eq!(all[0].borrow().node_id, "node-1");
    assert_eq!(ids(&all[0]), want(&SOURCE_MEMORY_IDS));
    let res = db.storage.vector_search(&[0.25, 0.5, 0.75], SESSION_KEY, 50, None);
    assert_eq!(res.iter().map(|(n, _)| n.borrow().node_id.clone()).collect::<Vec<_>>(), vec!["node-1"]);
    assert_eq!(ids(&res[0].0), want(&SOURCE_MEMORY_IDS));
}

#[test]
fn save_nodes_batch_preserves_source_memory_ids() {
    let db = temp_db();
    let saved = db.storage.save_nodes_batch(&[
        make_node("batch-node-1", Some(&["memory-a"])),
        make_node("batch-node-2", Some(&["memory-b", "memory-c"])),
    ]);
    assert_eq!(saved, 2);
    assert_eq!(ids(&db.storage.get_node("batch-node-1").unwrap()), want(&["memory-a"]));
    assert_eq!(ids(&db.storage.get_node("batch-node-2").unwrap()), want(&["memory-b", "memory-c"]));
}

#[test]
fn node_without_lineage_round_trips_as_empty_list() {
    let db = temp_db();
    db.storage.save_node(&make_node("no-lineage-node", Some(&[])));
    assert!(ids(&db.storage.get_node("no-lineage-node").unwrap()).is_empty());
}

#[test]
fn sqlite_round_trip_matches_dict_round_trip() {
    let db = temp_db();
    let node = make_node("node-1", None);
    db.storage.save_node(&node);
    let from_dict = NeuralNode::from_dict(node.borrow().to_dict().as_object().unwrap()).unwrap();
    let from_storage = db.storage.get_node("node-1").unwrap();
    assert_eq!(ids(&from_storage), from_dict.source_memory_ids);
    assert_eq!(ids(&from_storage), ids(&node));
}

fn insert_legacy_row(path: &Path) {
    let created_at = "2026-01-01T00:00:00+00:00";
    let legacy = json!({
        "node_id": "legacy-node", "session_key": SESSION_KEY, "layer": 1,
        "content": "row persisted before source_memory_ids was serialized", "embedding": null,
        "wave_amplitudes": {}, "activation_level": 0.0, "last_activated": created_at, "refractory_until": null,
        "heat_score": 0.3, "importance_score": 0.5, "consolidation_state": "active", "parent_id": null,
        "child_ids": [], "entity_ids": [], "edge_type_counts": {}, "dominant_dimension": "", "diversity_score": 0.0,
        "created_at": created_at, "metadata": {},
    });
    assert!(legacy.get("source_memory_ids").is_none());
    let conn = rusqlite::Connection::open(path).unwrap();
    conn.execute(
        "INSERT INTO nodes (node_id, session_key, layer, content, embedding, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rusqlite::params![
            "legacy-node",
            SESSION_KEY,
            "1",
            s(&legacy["content"]),
            Option::<Vec<u8>>::None,
            legacy.to_string(),
            created_at
        ],
    )
    .unwrap();
}

#[test]
fn legacy_row_loads_keeps_fields_and_upgrades() {
    let db = temp_db();
    insert_legacy_row(&db.path);
    let loaded = db.storage.get_node("legacy-node").unwrap();
    assert!(ids(&loaded).is_empty());
    {
        let n = loaded.borrow();
        assert_eq!(n.node_id, "legacy-node");
        assert_eq!(n.session_key, SESSION_KEY);
        assert_eq!(n.layer, NodeLayer::Episode);
        assert_eq!(n.content, "row persisted before source_memory_ids was serialized");
    }
    loaded.borrow_mut().source_memory_ids = vec!["memory-added-later".into()];
    db.storage.save_node(&loaded);
    assert_eq!(ids(&db.storage.get_node("legacy-node").unwrap()), want(&["memory-added-later"]));
}

#[test]
fn backends_agree_on_source_memory_ids() {
    for lineage in [None, Some(&[][..])] {
        let db = temp_db();
        let node = make_node("agreement-node", lineage);
        let in_memory = InMemoryNeuralGraphStorage::default();
        in_memory.save_node(&node);
        db.storage.save_node(&node);
        let from_memory = in_memory.get_node("agreement-node").unwrap();
        let from_sqlite = db.storage.get_node("agreement-node").unwrap();
        assert_eq!(ids(&from_memory), want(lineage.unwrap_or(&SOURCE_MEMORY_IDS)));
        assert_eq!(ids(&from_sqlite), ids(&from_memory));
    }
}
