//! SQLite-backed persistent storage of the neural memory graph (port of `retrieval/sqlite_storage.py`).
//!
//! The database format is the Python one, byte for byte, so files written by either side open in the other:
//! the same `CREATE` statements (compare `sqlite_master.sql`), embeddings as native-endian float32 blobs
//! (`struct.pack(f"{n}f", ...)`), and every node/edge additionally serialised with `json.dumps` defaults into
//! the `data` column. Queries are the same SQL text, so row orders are whatever SQLite's planner gives both.
//!
//! Behaviour kept from the Python, for readers who expect otherwise:
//! - Reads build fresh objects every time (the Python deserialised a new `NeuralNode` per call), so a loaded
//!   node is not shared with the caller's handle; `save_edge` does not touch node edge counters.
//! - `updated_at`, `NeuralEdge.created_at` and `context_embedding` are not persisted; reads default them to
//!   "now" / `None`. Rows without `heat_score` / `diversity_score` load as 0.0 (not the dataclass 0.3 / 0.5).
//! - A row whose JSON lacks `dominant_dimension` loaded as `None` in Python; the Rust field is a `String`, so it
//!   loads as "". Every row this code (or the Python) wrote has the key.
//! - SQLite errors and malformed rows raised in Python; the trait methods cannot return errors, so they panic
//!   with SQLite's message. The category/domain helpers return `Err` (an `IntegrityError` on duplicates).
//! - Python's `NeuralGraphStorage` subclass for SQLite lacked the in-memory query helpers (`get_neighbors`,
//!   `get_children`, ...); calling them raised `AttributeError`. The Rust trait supplies default
//!   implementations over the core methods for every store, so here they work.
//! - Loading sqlite-vec is attempted exactly as in Python (`has_sqlite_vec` only shows in the statistics; vector
//!   search is brute force either way).

use crate::pytime::PyDateTime;
use crate::retrieval::data_types::{
    cosine_similarity, edge_ref, node_ref, ConsolidationState, EdgeRef, EdgeType, NeuralEdge, NeuralNode, NodeLayer,
    NodeRef,
};
use crate::retrieval::storage::NeuralGraphStorage;
use neuralgraph_memory::pycompat::{dumps, JsonFormat};
use rusqlite::types::ValueRef;
use rusqlite::{params_from_iter, Connection, Row};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::path::{Path, PathBuf};

const CREATE_NODES: &str = "
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                layer TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB,
                data JSON NOT NULL,
                created_at TEXT NOT NULL
            )
        ";

const CREATE_EDGES: &str = "
            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                data JSON NOT NULL,
                FOREIGN KEY (source_id) REFERENCES nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES nodes(node_id)
            )
        ";

const CREATE_CATEGORIES: &str = "
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                domain TEXT
            )
        ";

const CREATE_DOMAINS: &str = "
            CREATE TABLE IF NOT EXISTS domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT
            )
        ";

const CREATE_INDEXES: [&str; 5] = [
    "CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_key)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_layer ON nodes(session_key, layer)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type)",
];

const INSERT_NODE: &str = "
                INSERT OR REPLACE INTO nodes (node_id, session_key, layer, content, embedding, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ";

const INSERT_EDGE: &str = "
                INSERT OR REPLACE INTO edges (edge_id, source_id, target_id, edge_type, data)
                VALUES (?, ?, ?, ?, ?)
            ";

/// Default database location (`~/.memmachine/memories.db`).
pub const DEFAULT_DB_PATH: &str = "~/.memmachine/memories.db";

/// `Path(p).expanduser()`: a leading `~` becomes `$HOME`.
fn expanduser(p: &str) -> PathBuf {
    if p == "~" || p.starts_with("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            return PathBuf::from(home).join(p.trim_start_matches('~').trim_start_matches('/'));
        }
    }
    PathBuf::from(p)
}

fn iso(dt: &PyDateTime) -> String {
    dt.isoformat()
}

/// `_serialize_node`
pub fn serialize_node(node: &NeuralNode) -> Value {
    json!({
        "node_id": node.node_id,
        "session_key": node.session_key,
        "layer": node.layer.value(),
        "content": node.content,
        "embedding": node.embedding,
        "wave_amplitudes": node.wave_amplitudes,
        "activation_level": node.activation_level,
        "last_activated": iso(&node.last_activated),
        "refractory_until": node.refractory_until.as_ref().map(iso),
        "heat_score": node.heat_score,
        "importance_score": node.importance_score,
        "consolidation_state": node.consolidation_state.value(),
        "parent_id": node.parent_id,
        "child_ids": node.child_ids,
        "entity_ids": node.entity_ids,
        "edge_type_counts": node.edge_type_counts,
        "dominant_dimension": node.dominant_dimension,
        "diversity_score": node.diversity_score,
        "created_at": iso(&node.created_at),
        "source_memory_ids": node.source_memory_ids,
        "metadata": node.metadata,
    })
}

fn get_f64(d: &Map<String, Value>, k: &str, default: f64) -> f64 {
    d.get(k).and_then(Value::as_f64).unwrap_or(default)
}

fn strings(d: &Map<String, Value>, k: &str) -> Vec<String> {
    d.get(k)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

/// `datetime.fromisoformat(data[k]) if data.get(k) else default`
fn datetime_or(d: &Map<String, Value>, k: &str, default: impl FnOnce() -> Option<PyDateTime>) -> Option<PyDateTime> {
    match d.get(k) {
        Some(Value::String(s)) if !s.is_empty() => {
            Some(PyDateTime::fromisoformat(s).unwrap_or_else(|| panic!("Invalid isoformat string: {s:?}")))
        }
        _ => default(),
    }
}

/// `_deserialize_node` (with `data["embedding"]` already replaced by the blob's values).
pub fn deserialize_node(d: &Map<String, Value>) -> NeuralNode {
    let node_id = d.get("node_id").and_then(Value::as_str).expect("KeyError: 'node_id'");
    let layer_value = d.get("layer").and_then(Value::as_i64).expect("KeyError: 'layer'");
    let layer = NodeLayer::from_value(layer_value)
        .unwrap_or_else(|| panic!("ValueError: {layer_value} is not a valid NodeLayer"));
    let content = d.get("content").and_then(Value::as_str).expect("KeyError: 'content'");
    let mut n = NeuralNode::new(node_id, layer, content);
    n.session_key = d.get("session_key").and_then(Value::as_str).expect("KeyError: 'session_key'").to_string();
    n.embedding = d.get("embedding").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_f64).collect());
    n.wave_amplitudes = d
        .get("wave_amplitudes")
        .and_then(Value::as_object)
        .map(|o| o.iter().filter_map(|(k, v)| v.as_f64().map(|f| (k.clone(), f))).collect())
        .unwrap_or_default();
    n.activation_level = get_f64(d, "activation_level", 0.0);
    n.last_activated = datetime_or(d, "last_activated", || Some(PyDateTime::now_utc())).expect("default");
    n.refractory_until = datetime_or(d, "refractory_until", || None);
    n.heat_score = get_f64(d, "heat_score", 0.0);
    n.importance_score = get_f64(d, "importance_score", 0.5);
    let state = d.get("consolidation_state").and_then(Value::as_str).unwrap_or("active");
    n.consolidation_state = ConsolidationState::from_value(state)
        .unwrap_or_else(|| panic!("ValueError: '{state}' is not a valid ConsolidationState"));
    n.parent_id = d.get("parent_id").and_then(Value::as_str).map(str::to_string);
    n.child_ids = strings(d, "child_ids");
    n.entity_ids = strings(d, "entity_ids");
    n.edge_type_counts = d
        .get("edge_type_counts")
        .and_then(Value::as_object)
        .map(|o| o.iter().filter_map(|(k, v)| v.as_i64().map(|c| (k.clone(), c))).collect())
        .unwrap_or_default();
    n.dominant_dimension = d.get("dominant_dimension").and_then(Value::as_str).unwrap_or("").to_string();
    n.diversity_score = get_f64(d, "diversity_score", 0.0);
    n.created_at = datetime_or(d, "created_at", || Some(PyDateTime::now_utc())).expect("default");
    n.source_memory_ids = strings(d, "source_memory_ids");
    n.metadata = d.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default();
    n
}

/// `_serialize_edge`
pub fn serialize_edge(edge: &NeuralEdge) -> Value {
    json!({
        "edge_id": edge.edge_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "edge_type": edge.edge_type.value(),
        "base_weight": edge.base_weight,
        "sign": edge.sign,
        "confidence": edge.confidence,
        "ltp_boost": edge.ltp_boost,
        "ltp_decay_rate": edge.ltp_decay_rate,
        "activation_count": edge.activation_count,
        "last_activated": iso(&edge.last_activated),
        "gate_threshold": edge.gate_threshold,
        "propagation_delay_ms": edge.propagation_delay_ms,
        "metadata": edge.metadata,
    })
}

/// `_deserialize_edge`
pub fn deserialize_edge(d: &Map<String, Value>) -> NeuralEdge {
    let key = |k: &str| d.get(k).and_then(Value::as_str).unwrap_or_else(|| panic!("KeyError: '{k}'")).to_string();
    let type_value = key("edge_type");
    let edge_type = EdgeType::from_value(&type_value)
        .unwrap_or_else(|| panic!("ValueError: '{type_value}' is not a valid EdgeType"));
    let mut e = NeuralEdge::new(key("edge_id"), key("source_id"), key("target_id"), edge_type);
    e.base_weight = get_f64(d, "base_weight", 1.0);
    e.sign = get_f64(d, "sign", 1.0);
    e.confidence = get_f64(d, "confidence", 1.0);
    e.ltp_boost = get_f64(d, "ltp_boost", 1.0);
    e.ltp_decay_rate = get_f64(d, "ltp_decay_rate", 0.01);
    e.activation_count = d.get("activation_count").and_then(Value::as_i64).unwrap_or(0);
    e.last_activated = datetime_or(d, "last_activated", || Some(PyDateTime::now_utc())).expect("default");
    e.gate_threshold = get_f64(d, "gate_threshold", 0.3);
    e.propagation_delay_ms = get_f64(d, "propagation_delay_ms", 0.0);
    e.metadata = d.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default();
    e
}

/// `struct.pack(f"{len(e)}f", *e)`: native-endian float32. Native-mode `struct` just casts (`(float)x`), so a
/// value beyond the float32 range becomes infinity rather than raising as the standard-size `"<f"` would.
pub fn embedding_to_blob(embedding: Option<&[f64]>) -> Option<Vec<u8>> {
    let embedding = embedding?;
    Some(embedding.iter().flat_map(|x| (*x as f32).to_ne_bytes()).collect())
}

/// `struct.unpack(f"{len(blob) // 4}f", blob)`: Python raised `struct.error` when the length is not a multiple
/// of four (`unpack` needs the exact size); that panics here.
pub fn blob_to_embedding(blob: Option<&[u8]>) -> Option<Vec<f64>> {
    let blob = blob?;
    if blob.len() % 4 != 0 {
        panic!("struct.error: unpack requires a buffer of {} bytes", blob.len() / 4 * 4);
    }
    Some(blob.as_chunks::<4>().0.iter().map(|c| f32::from_ne_bytes(*c) as f64).collect())
}

/// A column value as Python's `sqlite3` returns it, as JSON (`dict(row)`).
fn column_json(row: &Row, idx: usize) -> Value {
    match row.get_ref(idx).expect("column") {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(i) => json!(i),
        ValueRef::Real(f) => json!(f),
        ValueRef::Text(t) => json!(String::from_utf8_lossy(t)),
        ValueRef::Blob(b) => json!(b),
    }
}

fn row_dict(row: &Row) -> Value {
    let mut m = Map::new();
    for (i, name) in row.as_ref().column_names().iter().enumerate() {
        m.insert(name.to_string(), column_json(row, i));
    }
    Value::Object(m)
}

fn sqlite_fail(e: rusqlite::Error) -> ! {
    panic!("sqlite3 error: {e}")
}

fn dump(v: &Value) -> String {
    dumps(v, &JsonFormat::python_default())
}

fn parse_data(text: &str) -> Map<String, Value> {
    match serde_json::from_str::<Value>(text) {
        Ok(Value::Object(m)) => m,
        _ => panic!("json.JSONDecodeError on stored data: {text:?}"),
    }
}

/// SQLite-backed [`NeuralGraphStorage`].
pub struct SqliteNeuralGraphStorage {
    db_path: PathBuf,
    conn: RefCell<Option<Connection>>,
    has_sqlite_vec: bool,
}

impl SqliteNeuralGraphStorage {
    /// `SQLiteNeuralGraphStorage(db_path)`: open (creating parent directories and the schema if needed).
    pub fn new(db_path: impl AsRef<Path>) -> Self {
        let db_path = expanduser(&db_path.as_ref().to_string_lossy());
        if let Some(parent) = db_path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent).unwrap_or_else(|e| panic!("mkdir {}: {e}", parent.display()));
        }
        let mut storage = SqliteNeuralGraphStorage { db_path, conn: RefCell::new(None), has_sqlite_vec: false };
        storage.init_db();
        storage
    }

    /// `SQLiteNeuralGraphStorage()` at [`DEFAULT_DB_PATH`].
    pub fn open_default() -> Self {
        Self::new(DEFAULT_DB_PATH)
    }

    pub fn db_path(&self) -> &Path {
        &self.db_path
    }

    pub fn has_sqlite_vec(&self) -> bool {
        self.has_sqlite_vec
    }

    fn init_db(&mut self) {
        let conn = Connection::open(&self.db_path).unwrap_or_else(|e| sqlite_fail(e));
        // rusqlite's bundled SQLite enforces foreign keys by default; Python's sqlite3 connections do not, and
        // the store relies on that (edges may point at nodes not saved yet, or at none).
        conn.pragma_update(None, "foreign_keys", false).unwrap_or_else(|e| sqlite_fail(e));
        // Try to load sqlite-vec from the same places the Python tried.
        // SAFETY: loading an extension runs its init code, exactly like Python's `load_extension`; the
        // candidates are the sqlite-vec names the Python used.
        if unsafe { conn.load_extension_enable() }.is_ok() {
            for ext_path in ["sqlite_vec", "vec0", "/usr/local/lib/sqlite-vec/vec0"] {
                if unsafe { conn.load_extension(ext_path, None::<&str>) }.is_ok() {
                    self.has_sqlite_vec = true;
                    tracing::info!("sqlite-vec extension loaded successfully");
                    break;
                }
            }
        } else {
            tracing::warn!("Could not load sqlite-vec. Using brute-force vector search.");
        }
        for sql in [CREATE_NODES, CREATE_EDGES, CREATE_CATEGORIES, CREATE_DOMAINS].into_iter().chain(CREATE_INDEXES) {
            conn.execute(sql, []).unwrap_or_else(|e| sqlite_fail(e));
        }
        tracing::info!("SQLite database initialized at {}", self.db_path.display());
        *self.conn.borrow_mut() = Some(conn);
    }

    fn with_conn<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> T {
        let guard = self.conn.borrow();
        let conn = guard.as_ref().expect("AttributeError: 'NoneType' object has no attribute 'cursor'");
        f(conn).unwrap_or_else(|e| sqlite_fail(e))
    }

    /// Like `with_conn`, but the statements run in one transaction (Python's implicit transaction up to
    /// `commit()`).
    fn with_tx<T>(&self, f: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> T {
        self.with_conn(|conn| {
            let tx = conn.unchecked_transaction()?;
            let out = f(&tx)?;
            tx.commit()?;
            Ok(out)
        })
    }

    fn insert_node(conn: &Connection, node: &NeuralNode) -> rusqlite::Result<usize> {
        let data = serialize_node(node);
        let blob = embedding_to_blob(node.embedding.as_deref());
        conn.execute(
            INSERT_NODE,
            rusqlite::params![
                node.node_id,
                node.session_key,
                node.layer.value(),
                node.content,
                blob,
                dump(&data),
                iso(&node.created_at),
            ],
        )
    }

    fn insert_edge(conn: &Connection, edge: &NeuralEdge) -> rusqlite::Result<usize> {
        let data = serialize_edge(edge);
        conn.execute(
            INSERT_EDGE,
            rusqlite::params![edge.edge_id, edge.source_id, edge.target_id, edge.edge_type.value(), dump(&data)],
        )
    }

    fn query_nodes(&self, sql: &str, params: Vec<rusqlite::types::Value>) -> Vec<NodeRef> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(sql)?;
            let rows = stmt.query_map(params_from_iter(params), |row| {
                let data: String = row.get(0)?;
                let blob: Option<Vec<u8>> = row.get(1)?;
                Ok((data, blob))
            })?;
            let mut out = Vec::new();
            for r in rows {
                let (data, blob) = r?;
                let mut d = parse_data(&data);
                d.insert("embedding".into(), json!(blob_to_embedding(blob.as_deref())));
                out.push(node_ref(deserialize_node(&d)));
            }
            Ok(out)
        })
    }

    fn query_edges(&self, sql: &str, params: Vec<rusqlite::types::Value>) -> Vec<EdgeRef> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(sql)?;
            let rows = stmt.query_map(params_from_iter(params), |row| row.get::<_, String>(0))?;
            let mut out = Vec::new();
            for r in rows {
                out.push(edge_ref(deserialize_edge(&parse_data(&r?))));
            }
            Ok(out)
        })
    }

    fn query_dicts(&self, sql: &str, params: Vec<rusqlite::types::Value>) -> Vec<Value> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(sql)?;
            let rows = stmt.query_map(params_from_iter(params), |row| Ok(row_dict(row)))?;
            rows.collect()
        })
    }

    // ---- categories and domains (MCP-specific) ------------------------------------------------------------

    /// All categories (optionally of one domain) as `{id, name, description, domain}`.
    pub fn list_categories(&self, domain: Option<&str>) -> Vec<Value> {
        match domain.filter(|d| !d.is_empty()) {
            Some(d) => self.query_dicts(
                "SELECT id, name, description, domain FROM categories WHERE domain = ?",
                vec![d.to_string().into()],
            ),
            None => self.query_dicts("SELECT id, name, description, domain FROM categories", vec![]),
        }
    }

    /// Insert a category; its row id, or the `sqlite3.IntegrityError` message for a duplicate name.
    pub fn create_category(&self, name: &str, description: Option<&str>, domain: Option<&str>) -> Result<i64, String> {
        self.try_insert(
            "INSERT INTO categories (name, description, domain) VALUES (?, ?, ?)",
            rusqlite::params![name, description, domain],
        )
    }

    pub fn list_domains(&self) -> Vec<Value> {
        self.query_dicts("SELECT id, name, description FROM domains", vec![])
    }

    pub fn create_domain(&self, name: &str, description: Option<&str>) -> Result<i64, String> {
        self.try_insert("INSERT INTO domains (name, description) VALUES (?, ?)", rusqlite::params![name, description])
    }

    fn try_insert(&self, sql: &str, params: impl rusqlite::Params) -> Result<i64, String> {
        let guard = self.conn.borrow();
        let conn = guard.as_ref().expect("AttributeError: 'NoneType' object has no attribute 'cursor'");
        match conn.execute(sql, params) {
            Ok(_) => Ok(conn.last_insert_rowid()),
            Err(rusqlite::Error::SqliteFailure(_, Some(msg))) => Err(msg),
            Err(e) => Err(e.to_string()),
        }
    }

    /// Sessions with node counts and first/last `created_at`, most recent first.
    pub fn list_sessions(&self, limit: i64, offset: i64) -> Vec<Value> {
        self.query_dicts(
            "
            SELECT session_key, COUNT(*) as node_count, MIN(created_at) as first_memory, MAX(created_at) as last_memory
            FROM nodes
            GROUP BY session_key
            ORDER BY last_memory DESC
            LIMIT ? OFFSET ?
        ",
            vec![limit.into(), offset.into()],
        )
    }

    /// Close the connection; any later call fails like Python's `None.cursor()`.
    pub fn close(&self) {
        self.conn.borrow_mut().take();
    }
}

impl NeuralGraphStorage for SqliteNeuralGraphStorage {
    fn save_node(&self, node: &NodeRef) {
        let node = node.borrow();
        self.with_conn(|conn| Self::insert_node(conn, &node));
    }

    fn get_node(&self, node_id: &str) -> Option<NodeRef> {
        self.query_nodes("SELECT data, embedding FROM nodes WHERE node_id = ?", vec![node_id.to_string().into()])
            .into_iter()
            .next()
    }

    fn delete_node(&self, node_id: &str) -> bool {
        self.with_tx(|conn| {
            let exists = conn
                .prepare("SELECT 1 FROM nodes WHERE node_id = ?")?
                .query_map([node_id], |_| Ok(()))?
                .next()
                .is_some();
            if !exists {
                return Ok(false);
            }
            conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?", [node_id, node_id])?;
            conn.execute("DELETE FROM nodes WHERE node_id = ?", [node_id])?;
            Ok(true)
        })
    }

    fn get_nodes_by_session(&self, session_key: &str, layer: Option<NodeLayer>) -> Vec<NodeRef> {
        match layer {
            Some(l) => self.query_nodes(
                "SELECT data, embedding FROM nodes WHERE session_key = ? AND layer = ?",
                vec![session_key.to_string().into(), l.value().into()],
            ),
            None => self.query_nodes(
                "SELECT data, embedding FROM nodes WHERE session_key = ?",
                vec![session_key.to_string().into()],
            ),
        }
    }

    fn get_nodes_by_layer(&self, session_key: &str, layer: NodeLayer) -> Vec<NodeRef> {
        self.get_nodes_by_session(session_key, Some(layer))
    }

    fn save_edge(&self, edge: &EdgeRef) {
        let edge = edge.borrow();
        self.with_conn(|conn| Self::insert_edge(conn, &edge));
    }

    fn get_edge(&self, edge_id: &str) -> Option<EdgeRef> {
        self.query_edges("SELECT data FROM edges WHERE edge_id = ?", vec![edge_id.to_string().into()])
            .into_iter()
            .next()
    }

    fn delete_edge(&self, edge_id: &str) -> bool {
        self.with_conn(|conn| conn.execute("DELETE FROM edges WHERE edge_id = ?", [edge_id])) > 0
    }

    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef> {
        self.edges_by(node_id, "source_id", edge_types)
    }

    fn get_edges_to(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef> {
        self.edges_by(node_id, "target_id", edge_types)
    }

    fn get_edge_between(&self, source_id: &str, target_id: &str, edge_type: Option<EdgeType>) -> Option<EdgeRef> {
        let mut params: Vec<rusqlite::types::Value> = vec![source_id.to_string().into(), target_id.to_string().into()];
        let sql = match edge_type {
            Some(t) => {
                params.push(t.value().to_string().into());
                "SELECT data FROM edges WHERE source_id = ? AND target_id = ? AND edge_type = ?"
            }
            None => "SELECT data FROM edges WHERE source_id = ? AND target_id = ?",
        };
        self.query_edges(sql, params).into_iter().next()
    }

    fn get_all_edges(&self, session_key: &str) -> Vec<EdgeRef> {
        self.query_edges(
            "
            SELECT e.data FROM edges e
            INNER JOIN nodes n ON e.source_id = n.node_id
            WHERE n.session_key = ?
        ",
            vec![session_key.to_string().into()],
        )
    }

    /// Brute-force cosine over the session's nodes (float32-rounded embeddings, as stored), positive scores
    /// only, best first (stable for ties).
    fn vector_search(
        &self,
        query_embedding: &[f64],
        session_key: &str,
        limit: usize,
        layer_filter: Option<&[NodeLayer]>,
    ) -> Vec<(NodeRef, f64)> {
        let mut nodes = self.get_nodes_by_session(session_key, None);
        if let Some(f) = layer_filter.filter(|f| !f.is_empty()) {
            nodes.retain(|n| f.contains(&n.borrow().layer));
        }
        let mut scored = Vec::new();
        for node in nodes {
            let sim = match node.borrow().embedding.as_ref().filter(|e| !e.is_empty()) {
                Some(e) => cosine_similarity(query_embedding, e),
                None => continue,
            };
            if sim > 0.0 {
                scored.push((node, sim));
            }
        }
        scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(limit);
        scored
    }

    fn save_nodes_batch(&self, nodes: &[NodeRef]) -> usize {
        if nodes.is_empty() {
            return 0;
        }
        self.with_tx(|conn| {
            for n in nodes {
                Self::insert_node(conn, &n.borrow())?;
            }
            Ok(())
        });
        nodes.len()
    }

    fn save_edges_batch(&self, edges: &[EdgeRef]) -> usize {
        if edges.is_empty() {
            return 0;
        }
        self.with_tx(|conn| {
            for e in edges {
                Self::insert_edge(conn, &e.borrow())?;
            }
            Ok(())
        });
        edges.len()
    }

    /// Delete the session's nodes and every edge touching them; returns nodes + edges deleted.
    fn clear_session(&self, session_key: &str) -> usize {
        self.with_tx(|conn| {
            let node_ids: Vec<String> = conn
                .prepare("SELECT node_id FROM nodes WHERE session_key = ?")?
                .query_map([session_key], |row| row.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            if node_ids.is_empty() {
                return Ok(0);
            }
            let placeholders = vec!["?"; node_ids.len()].join(",");
            let edge_count = conn.execute(
                &format!("DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})"),
                params_from_iter(node_ids.iter().chain(node_ids.iter())),
            )?;
            let node_count = conn.execute("DELETE FROM nodes WHERE session_key = ?", [session_key])?;
            Ok(node_count + edge_count)
        })
    }

    fn get_session_statistics(&self, session_key: &str) -> Value {
        let layer_rows = self.query_dicts(
            "
            SELECT layer, COUNT(*) as count FROM nodes
            WHERE session_key = ? GROUP BY layer
        ",
            vec![session_key.to_string().into()],
        );
        let edge_rows = self.query_dicts(
            "
            SELECT e.edge_type, COUNT(*) as count FROM edges e
            INNER JOIN nodes n ON e.source_id = n.node_id
            WHERE n.session_key = ? GROUP BY e.edge_type
        ",
            vec![session_key.to_string().into()],
        );
        let counts = |rows: &[Value], key: &str| -> (Map<String, Value>, i64) {
            let mut m = Map::new();
            let mut total = 0;
            for r in rows {
                let k = match &r[key] {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                total += r["count"].as_i64().unwrap_or(0);
                m.insert(k, r["count"].clone());
            }
            (m, total)
        };
        let (layer_counts, total_nodes) = counts(&layer_rows, "layer");
        let (edge_type_counts, total_edges) = counts(&edge_rows, "edge_type");
        json!({
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_layer": layer_counts,
            "edges_by_type": edge_type_counts,
            "storage_type": "sqlite",
            "has_sqlite_vec": self.has_sqlite_vec,
        })
    }
}

impl SqliteNeuralGraphStorage {
    fn edges_by(&self, node_id: &str, column: &str, edge_types: Option<&[EdgeType]>) -> Vec<EdgeRef> {
        match edge_types.filter(|t| !t.is_empty()) {
            Some(types) => {
                let placeholders = vec!["?"; types.len()].join(",");
                let mut params: Vec<rusqlite::types::Value> = vec![node_id.to_string().into()];
                params.extend(types.iter().map(|t| t.value().to_string().into()));
                self.query_edges(
                    &format!("SELECT data FROM edges WHERE {column} = ? AND edge_type IN ({placeholders})"),
                    params,
                )
            }
            None => self
                .query_edges(&format!("SELECT data FROM edges WHERE {column} = ?"), vec![node_id.to_string().into()]),
        }
    }
}
