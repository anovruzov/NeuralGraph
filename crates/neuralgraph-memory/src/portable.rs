//! Portable brain archives: export a whole memory store (or one project of it) to a ZIP, import it on another
//! machine, and verify that the imported store holds the same knowledge with its provenance intact.
//!
//! Archive layout (format `neuralgraph-brain`, version 1):
//!
//! ```text
//! manifest.json                     versioned manifest: scope, counts, embedding model/dim, privacy, SHA-256 of every file
//! SHA256SUMS                        `sha256sum -c` compatible list (covers manifest.json too)
//! README.md                         what is inside and how to import / connect an agent
//! agents/*.md|json|toml             MCP config snippets for Claude Code, Gemini CLI, Codex and generic MCP clients
//! stores/<name>/brain.sqlite        consistent SQLite snapshot (the authoritative copy; restore copies it verbatim)
//! stores/<name>/jsonl/<table>.jsonl every row of every table, BLOBs as {"$b64": ...} (embeddings are float32 LE)
//! stores/<name>/graph.graphml       entities, memories and messages as nodes; relations, mentions, links, sources,
//!                                   supersession as edges
//! stores/<name>/memories.md         human-readable memories with sources, quotes, entities and history
//! ```
//!
//! Nothing here touches the network: an archive is only ever written to the path the caller names.

use crate::error::{MemoryError, Result};
use crate::models::now_iso;
use crate::retrieval::SearchOptions;
use crate::service::{ChatMemory, ChatMemoryConfig};
use crate::store::{unpack, ChatMemoryStore, SCHEMA_VERSION};
use base64::Engine as _;
use indexmap::IndexMap;
use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub const FORMAT: &str = "neuralgraph-brain";
pub const FORMAT_VERSION: i64 = 1;

/// Tables every store has; anything else found in a snapshot is exported too.
const CORE_TABLES: [&str; 13] = [
    "meta",
    "chats",
    "messages",
    "memories",
    "memory_sources",
    "entities",
    "entity_aliases",
    "memory_entities",
    "relations",
    "memory_links",
    "jobs",
    "audit_log",
    "sqlite_sequence",
];

fn err(msg: impl Into<String>) -> MemoryError {
    MemoryError::Value(msg.into())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn sha256_hex(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

fn expand_home(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(rest),
        None => PathBuf::from(p),
    }
}

fn scratch_dir(label: &str) -> Result<PathBuf> {
    let dir = std::env::temp_dir().join(format!("ng-brain-{label}-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// Removes a scratch directory when dropped.
struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

// ------------------------------------------------------------------------------------------------ export

#[derive(Debug, Clone)]
pub struct ExportOptions {
    /// (store name, SQLite path); names must be unique
    pub stores: Vec<(String, String)>,
    pub out: PathBuf,
    /// project scope: keep only chats whose id equals or starts with one of these (empty = whole brain)
    pub chat_prefixes: Vec<String>,
    pub include_embeddings: bool,
    /// the embedding model that produced the stored vectors (recorded in the manifest)
    pub embed_model: Option<String>,
    pub overwrite: bool,
}

/// Store name from a DB path: file stem, made unique by the caller.
pub fn store_name_for(path: &str) -> String {
    let stem = Path::new(path).file_stem().and_then(|s| s.to_str()).unwrap_or("brain");
    let clean: String = stem.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect();
    if clean.is_empty() { "brain".into() } else { clean }
}

/// Copy a live store into `dest` as one consistent snapshot (works while the store is open elsewhere).
fn snapshot(db_path: &Path, dest: &Path) -> Result<()> {
    if !db_path.exists() {
        return Err(err(format!("no memory database at {}", db_path.display())));
    }
    let c = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    c.busy_timeout(std::time::Duration::from_secs(10))?;
    c.execute("VACUUM INTO ?", [dest.to_string_lossy()])?;
    Ok(())
}

fn user_tables(c: &Connection) -> Result<Vec<String>> {
    let mut st = c.prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
           AND name NOT LIKE '%_fts' AND name NOT LIKE '%_fts_%' ORDER BY name",
    )?;
    let names = st.query_map([], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(names)
}

fn table_exists(c: &Connection, name: &str) -> Result<bool> {
    Ok(c.query_row("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name], |_| Ok(()))
        .optional()?
        .is_some())
}

fn count(c: &Connection, table: &str) -> Result<i64> {
    Ok(c.query_row(&format!("SELECT COUNT(*) FROM \"{table}\""), [], |r| r.get(0))?)
}

/// Delete everything outside the project scope from a snapshot.
fn apply_scope(c: &Connection, prefixes: &[String]) -> Result<()> {
    c.execute_batch(
        "CREATE TEMP TABLE all_chats(chat_id TEXT PRIMARY KEY);
         INSERT OR IGNORE INTO all_chats SELECT chat_id FROM chats;
         INSERT OR IGNORE INTO all_chats SELECT chat_id FROM messages;
         INSERT OR IGNORE INTO all_chats SELECT chat_id FROM memories;
         INSERT OR IGNORE INTO all_chats SELECT chat_id FROM relations;
         CREATE TEMP TABLE keep_chats(chat_id TEXT PRIMARY KEY);",
    )?;
    let all: Vec<String> = {
        let mut st = c.prepare("SELECT chat_id FROM all_chats")?;
        let v = st.query_map([], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
        v
    };
    for chat in all.iter().filter(|ch| prefixes.iter().any(|p| ch.as_str() == p || ch.starts_with(p.as_str()))) {
        c.execute("INSERT OR IGNORE INTO keep_chats VALUES (?)", [chat])?;
    }
    c.execute_batch(
        "DELETE FROM messages WHERE chat_id NOT IN (SELECT chat_id FROM keep_chats);
         DELETE FROM chats WHERE chat_id NOT IN (SELECT chat_id FROM keep_chats);
         DELETE FROM memories WHERE chat_id NOT IN (SELECT chat_id FROM keep_chats);
         DELETE FROM relations WHERE chat_id NOT IN (SELECT chat_id FROM keep_chats);
         DELETE FROM memory_sources WHERE memory_id NOT IN (SELECT memory_id FROM memories)
             OR message_id NOT IN (SELECT message_id FROM messages);
         DELETE FROM memory_entities WHERE memory_id NOT IN (SELECT memory_id FROM memories);
         DELETE FROM memory_links WHERE source_id NOT IN (SELECT memory_id FROM memories)
             OR target_id NOT IN (SELECT memory_id FROM memories);
         DELETE FROM entities WHERE entity_id NOT IN (SELECT entity_id FROM memory_entities)
             AND entity_id NOT IN (SELECT subject_id FROM relations)
             AND entity_id NOT IN (SELECT object_id FROM relations);
         DELETE FROM entity_aliases WHERE entity_id NOT IN (SELECT entity_id FROM entities);
         DELETE FROM jobs WHERE chat_id IS NULL OR chat_id NOT IN (SELECT chat_id FROM keep_chats);
         DELETE FROM audit_log WHERE ref IS NULL OR ref NOT IN (
             SELECT memory_id FROM memories UNION SELECT message_id FROM messages
             UNION SELECT relation_id FROM relations UNION SELECT chat_id FROM chats);
         DROP TABLE all_chats; DROP TABLE keep_chats;",
    )?;
    Ok(())
}

fn sql_to_json(v: &Sql) -> Value {
    match v {
        Sql::Null => Value::Null,
        Sql::Integer(i) => json!(i),
        Sql::Real(f) => json!(f),
        Sql::Text(s) => Value::String(s.clone()),
        Sql::Blob(b) => json!({"$b64": base64::engine::general_purpose::STANDARD.encode(b)}),
    }
}

/// Every table as JSONL (rowid order). Returns (table, bytes, rows).
fn dump_jsonl(c: &Connection) -> Result<Vec<(String, Vec<u8>, i64)>> {
    let mut out = Vec::new();
    for table in user_tables(c)? {
        let mut st = c.prepare(&format!("SELECT * FROM \"{table}\" ORDER BY rowid"))?;
        let cols: Vec<String> = st.column_names().iter().map(|s| s.to_string()).collect();
        let mut rows = st.query([])?;
        let mut buf = Vec::new();
        let mut n = 0;
        while let Some(r) = rows.next()? {
            let mut obj = Map::new();
            for (i, col) in cols.iter().enumerate() {
                obj.insert(col.clone(), sql_to_json(&r.get::<_, Sql>(i)?));
            }
            serde_json::to_writer(&mut buf, &Value::Object(obj)).map_err(|e| err(e.to_string()))?;
            buf.push(b'\n');
            n += 1;
        }
        out.push((table, buf, n));
    }
    Ok(out)
}

fn xml_escape(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '&' => o.push_str("&amp;"),
            '<' => o.push_str("&lt;"),
            '>' => o.push_str("&gt;"),
            '"' => o.push_str("&quot;"),
            '\'' => o.push_str("&apos;"),
            c if (c as u32) < 0x20 && !matches!(c, '\t' | '\n' | '\r') => {}
            c => o.push(c),
        }
    }
    o
}

#[derive(Default, Debug, Clone, PartialEq)]
pub struct GraphCounts {
    pub nodes: IndexMap<String, i64>,
    pub edges: IndexMap<String, i64>,
}

impl GraphCounts {
    pub fn to_value(&self) -> Value {
        json!({
            "nodes": self.nodes, "edges": self.edges,
            "node_total": self.nodes.values().sum::<i64>(), "edge_total": self.edges.values().sum::<i64>(),
        })
    }
}

fn strings(c: &Connection, sql: &str) -> Result<Vec<Vec<Option<String>>>> {
    let mut st = c.prepare(sql)?;
    let n = st.column_count();
    let rows = st
        .query_map([], |r| (0..n).map(|i| r.get::<_, Option<String>>(i)).collect::<rusqlite::Result<Vec<_>>>())?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(rows)
}

/// GraphML of the knowledge graph plus its node/edge counts.
pub fn build_graph(c: &Connection) -> Result<(String, GraphCounts)> {
    let mut nodes = String::new();
    let mut edges = String::new();
    let mut gc = GraphCounts::default();
    let mut seen: HashSet<String> = HashSet::new();
    let node = |nodes: &mut String, id: &str, ntype: &str, label: &str, extra: &[(&str, Option<String>)]| {
        let mut s = format!(
            "    <node id=\"{}\"><data key=\"type\">{ntype}</data><data key=\"label\">{}</data>",
            xml_escape(id),
            xml_escape(label)
        );
        for (k, v) in extra {
            if let Some(v) = v {
                s.push_str(&format!("<data key=\"{k}\">{}</data>", xml_escape(v)));
            }
        }
        s.push_str("</node>\n");
        nodes.push_str(&s);
    };
    let add_count = |m: &mut IndexMap<String, i64>, k: &str| *m.entry(k.to_string()).or_insert(0) += 1;

    for r in strings(c, "SELECT entity_id, name, type, CAST(mention_count AS TEXT) FROM entities ORDER BY entity_id")? {
        let id = r[0].clone().unwrap_or_default();
        node(&mut nodes, &format!("entity:{id}"), "entity", r[1].as_deref().unwrap_or(""), &[("entity_type", r[2].clone()), ("mentions", r[3].clone())]);
        seen.insert(id);
        add_count(&mut gc.nodes, "entity");
    }
    let rels = strings(
        c,
        "SELECT relation_id, subject_id, predicate, object_id, CAST(confidence AS TEXT), status, memory_id, message_id,
                CAST(observation_count AS TEXT), observed_at FROM relations ORDER BY relation_id",
    )?;
    // relation endpoints that never became entities still need a node
    for r in &rels {
        for end in [&r[1], &r[3]] {
            let id = end.clone().unwrap_or_default();
            if seen.insert(id.clone()) {
                node(&mut nodes, &format!("entity:{id}"), "entity", &id, &[("implicit", Some("true".into()))]);
                add_count(&mut gc.nodes, "entity");
            }
        }
    }
    let mems = strings(
        c,
        "SELECT memory_id, text, kind, subject, status, superseded_by, CAST(version AS TEXT), CAST(confidence AS TEXT),
                observed_at, event_time, chat_id FROM memories ORDER BY rid",
    )?;
    for r in &mems {
        node(
            &mut nodes,
            &format!("memory:{}", r[0].as_deref().unwrap_or("")),
            "memory",
            r[1].as_deref().unwrap_or(""),
            &[
                ("kind", r[2].clone()),
                ("subject", r[3].clone()),
                ("status", r[4].clone()),
                ("version", r[6].clone()),
                ("confidence", r[7].clone()),
                ("observed_at", r[8].clone()),
                ("event_time", r[9].clone()),
                ("chat_id", r[10].clone()),
            ],
        );
        add_count(&mut gc.nodes, "memory");
    }
    for r in strings(c, "SELECT message_id, speaker, text, chat_id, sent_at, CAST(seq AS TEXT) FROM messages ORDER BY rid")? {
        node(
            &mut nodes,
            &format!("message:{}", r[0].as_deref().unwrap_or("")),
            "message",
            r[2].as_deref().unwrap_or(""),
            &[("speaker", r[1].clone()), ("chat_id", r[3].clone()), ("sent_at", r[4].clone()), ("seq", r[5].clone())],
        );
        add_count(&mut gc.nodes, "message");
    }
    let mut eid = 0usize;
    let mut edge = |edges: &mut String, src: &str, dst: &str, etype: &str, extra: &[(&str, Option<String>)]| {
        eid += 1;
        let mut s = format!(
            "    <edge id=\"e{eid}\" source=\"{}\" target=\"{}\"><data key=\"type\">{etype}</data>",
            xml_escape(src),
            xml_escape(dst)
        );
        for (k, v) in extra {
            if let Some(v) = v {
                s.push_str(&format!("<data key=\"{k}\">{}</data>", xml_escape(v)));
            }
        }
        s.push_str("</edge>\n");
        edges.push_str(&s);
    };
    for r in &rels {
        edge(
            &mut edges,
            &format!("entity:{}", r[1].as_deref().unwrap_or("")),
            &format!("entity:{}", r[3].as_deref().unwrap_or("")),
            "relation",
            &[
                ("relation_id", r[0].clone()),
                ("predicate", r[2].clone()),
                ("confidence", r[4].clone()),
                ("status", r[5].clone()),
                ("memory_id", r[6].clone()),
                ("message_id", r[7].clone()),
                ("observations", r[8].clone()),
                ("observed_at", r[9].clone()),
            ],
        );
        add_count(&mut gc.edges, "relation");
    }
    for r in strings(c, "SELECT memory_id, entity_id, role FROM memory_entities ORDER BY memory_id, entity_id")? {
        edge(&mut edges, &format!("memory:{}", r[0].as_deref().unwrap_or("")), &format!("entity:{}", r[1].as_deref().unwrap_or("")), "mentions", &[("role", r[2].clone())]);
        add_count(&mut gc.edges, "mentions");
    }
    for r in strings(c, "SELECT source_id, target_id, link_type, CAST(weight AS TEXT) FROM memory_links ORDER BY source_id, target_id, link_type")? {
        edge(&mut edges, &format!("memory:{}", r[0].as_deref().unwrap_or("")), &format!("memory:{}", r[1].as_deref().unwrap_or("")), "link", &[("link_type", r[2].clone()), ("weight", r[3].clone())]);
        add_count(&mut gc.edges, "link");
    }
    for r in strings(c, "SELECT memory_id, message_id FROM memory_sources ORDER BY memory_id, message_id")? {
        edge(&mut edges, &format!("memory:{}", r[0].as_deref().unwrap_or("")), &format!("message:{}", r[1].as_deref().unwrap_or("")), "derived_from", &[]);
        add_count(&mut gc.edges, "derived_from");
    }
    let mem_ids: HashSet<&str> = mems.iter().filter_map(|r| r[0].as_deref()).collect();
    for r in &mems {
        if let (Some(old), Some(new)) = (r[0].as_deref(), r[5].as_deref()) {
            if mem_ids.contains(new) {
                edge(&mut edges, &format!("memory:{old}"), &format!("memory:{new}"), "superseded_by", &[]);
                add_count(&mut gc.edges, "superseded_by");
            }
        }
    }
    let keys = [
        ("node", "type"), ("node", "label"), ("node", "entity_type"), ("node", "mentions"), ("node", "implicit"),
        ("node", "kind"), ("node", "subject"), ("node", "status"), ("node", "version"), ("node", "confidence"),
        ("node", "observed_at"), ("node", "event_time"), ("node", "chat_id"), ("node", "speaker"), ("node", "sent_at"),
        ("node", "seq"), ("edge", "relation_id"), ("edge", "predicate"), ("edge", "memory_id"), ("edge", "message_id"),
        ("edge", "observations"), ("edge", "role"), ("edge", "link_type"), ("edge", "weight"),
    ];
    let mut xml = String::from(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<graphml xmlns=\"http://graphml.graphdrawing.org/xmlns\">\n",
    );
    let mut declared = HashSet::new();
    for (domain, k) in keys {
        // a key used on both nodes and edges is declared once with for="all"
        if declared.insert(k) {
            let both = keys.iter().filter(|(_, kk)| *kk == k).count() > 1
                || matches!(k, "type" | "confidence" | "status" | "observed_at");
            let dom = if both { "all" } else { domain };
            xml.push_str(&format!("  <key id=\"{k}\" for=\"{dom}\" attr.name=\"{k}\" attr.type=\"string\"/>\n"));
        }
    }
    xml.push_str("  <graph id=\"neuralgraph\" edgedefault=\"directed\">\n");
    xml.push_str(&nodes);
    xml.push_str(&edges);
    xml.push_str("  </graph>\n</graphml>\n");
    Ok((xml, gc))
}

fn md_line(s: &str) -> String {
    s.replace('\n', " ").replace('\r', " ")
}

fn build_markdown(c: &Connection, store: &str) -> Result<String> {
    let mut md = format!("# NeuralGraph memories: {store}\n\n");
    let msgs: HashMap<String, Vec<Option<String>>> =
        strings(c, "SELECT message_id, speaker, chat_id, sent_at, text FROM messages")?
            .into_iter()
            .map(|r| (r[0].clone().unwrap_or_default(), r))
            .collect();
    let mut sources: HashMap<String, Vec<String>> = HashMap::new();
    for r in strings(c, "SELECT memory_id, message_id FROM memory_sources ORDER BY message_id")? {
        sources.entry(r[0].clone().unwrap_or_default()).or_default().push(r[1].clone().unwrap_or_default());
    }
    let mut ents: HashMap<String, Vec<String>> = HashMap::new();
    for r in strings(c, "SELECT me.memory_id, COALESCE(e.name, me.entity_id) FROM memory_entities me LEFT JOIN entities e USING(entity_id)")? {
        ents.entry(r[0].clone().unwrap_or_default()).or_default().push(r[1].clone().unwrap_or_default());
    }
    let mems = strings(
        c,
        "SELECT memory_id, text, kind, subject_name, status, superseded_by, CAST(version AS TEXT), CAST(confidence AS TEXT),
                observed_at, event_time, chat_id, speaker, metadata FROM memories
          ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, subject_name, observed_at",
    )?;
    let mut last_section = String::new();
    for r in &mems {
        let g = |i: usize| r[i].clone().unwrap_or_default();
        let section = if g(4) == "active" { "Active memories".to_string() } else { "History (superseded or retracted)".to_string() };
        if section != last_section {
            md.push_str(&format!("## {section}\n\n"));
            last_section = section;
        }
        md.push_str(&format!("### {}\n\n", md_line(&g(1))));
        md.push_str(&format!(
            "- id: `{}` · kind: {} · subject: {} · status: {} · version: {} · confidence: {}\n",
            g(0), g(2), g(3), g(4), g(6), g(7)
        ));
        md.push_str(&format!("- observed: {} · event time: {} · chat: `{}` · speaker: {}\n", g(8), g(9), g(10), g(11)));
        if !g(5).is_empty() {
            md.push_str(&format!("- superseded by: `{}`\n", g(5)));
        }
        if let Some(e) = ents.get(&g(0)) {
            md.push_str(&format!("- entities: {}\n", e.join(", ")));
        }
        if g(12) != "{}" && !g(12).is_empty() {
            md.push_str(&format!("- metadata: `{}`\n", md_line(&g(12))));
        }
        for mid in sources.get(&g(0)).into_iter().flatten() {
            match msgs.get(mid) {
                Some(m) => {
                    let text: String = m[4].clone().unwrap_or_default().chars().take(400).collect();
                    md.push_str(&format!(
                        "- source `{mid}` ({}, chat `{}`, {}):\n  > {}\n",
                        m[1].clone().unwrap_or_default(),
                        m[2].clone().unwrap_or_default(),
                        m[3].clone().unwrap_or_else(|| "undated".into()),
                        md_line(&text)
                    ));
                }
                None => md.push_str(&format!("- source `{mid}` (message not in this archive)\n")),
            }
        }
        md.push('\n');
    }
    let rels = strings(
        c,
        "SELECT subject_id, predicate, object_id, status, CAST(confidence AS TEXT), memory_id FROM relations ORDER BY subject_id, predicate",
    )?;
    if !rels.is_empty() {
        md.push_str("## Relations\n\n| subject | predicate | object | status | confidence | memory |\n|---|---|---|---|---|---|\n");
        for r in rels {
            let g = |i: usize| md_line(&r[i].clone().unwrap_or_default()).replace('|', "\\|");
            md.push_str(&format!("| {} | {} | {} | {} | {} | {} |\n", g(0), g(1), g(2), g(3), g(4), g(5)));
        }
        md.push('\n');
    }
    Ok(md)
}

/// Patterns that suggest secrets or confidential content (only counts and ids are reported, never the text).
fn privacy_scan(c: &Connection) -> Result<Vec<Value>> {
    let rules: [(&str, &str); 6] = [
        ("credential", r"(?i)\b(api[_-]?key|secret|passw(or)?d|access[_-]?token|bearer)\b\s*[:=]\s*\S{6,}"),
        ("api_key", r"\b(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"),
        ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        ("confidential_marker", r"(?i)\b(confidential|do not share|internal only|under nda|proprietary)\b"),
        ("email_address", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        ("phone_number", r"(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b"),
    ];
    let compiled: Vec<(&str, regex::Regex)> =
        rules.iter().map(|(k, p)| (*k, regex::Regex::new(p).expect("privacy regex"))).collect();
    let mut hits: IndexMap<&str, (i64, Vec<String>)> = IndexMap::new();
    for (sql, _) in [("SELECT memory_id, text FROM memories", "memory"), ("SELECT message_id, text FROM messages", "message")] {
        for r in strings(c, sql)? {
            let text = r[1].clone().unwrap_or_default();
            for (k, re) in &compiled {
                if re.is_match(&text) {
                    let e = hits.entry(k).or_insert((0, Vec::new()));
                    e.0 += 1;
                    if e.1.len() < 10 {
                        e.1.push(r[0].clone().unwrap_or_default());
                    }
                }
            }
        }
    }
    Ok(hits.into_iter().map(|(k, (n, ids))| json!({"category": k, "count": n, "example_ids": ids})).collect())
}

fn agent_files(store: &str) -> Vec<(String, String)> {
    let db = format!("~/.neuralgraph/{store}.db");
    vec![
        (
            "agents/README.md".into(),
            format!(
                "# Connecting an AI agent to this brain\n\n\
                 After `neuralgraph-memory brain import <archive> --db {db}`, any MCP-capable agent can use the imported\n\
                 knowledge through the `neuralgraph-memory mcp` server. Search results carry their source messages\n\
                 (`sources`), so answers stay attributable. Replace `/usr/local/bin/neuralgraph-memory` with the path of\n\
                 your binary (`which neuralgraph-memory`). `brain import` prints these snippets with the real paths.\n\n\
                 ## Claude Code\n\n```bash\nclaude mcp add neuralgraph-memory -- /usr/local/bin/neuralgraph-memory mcp --db {db}\n```\n\n\
                 ## Gemini CLI\n\nMerge `gemini-settings.json` into `~/.gemini/settings.json`.\n\n\
                 ## Codex\n\nAppend `codex-config.toml` to `~/.codex/config.toml`.\n\n\
                 ## Any other MCP client\n\nUse `mcp.json` (stdio transport), or run `neuralgraph-memory serve --db {db}` and\n\
                 point the client at `http://127.0.0.1:8765/mcp` (Streamable HTTP).\n"
            ),
        ),
        ("agents/mcp.json".into(), mcp_json("/usr/local/bin/neuralgraph-memory", &db)),
        ("agents/gemini-settings.json".into(), mcp_json("/usr/local/bin/neuralgraph-memory", &db)),
        ("agents/codex-config.toml".into(), codex_toml("/usr/local/bin/neuralgraph-memory", &db)),
    ]
}

pub fn mcp_json(bin: &str, db: &str) -> String {
    serde_json::to_string_pretty(&json!({
        "mcpServers": {"neuralgraph-memory": {"command": bin, "args": ["mcp", "--db", db]}}
    }))
    .unwrap_or_default()
        + "\n"
}

pub fn codex_toml(bin: &str, db: &str) -> String {
    format!("[mcp_servers.neuralgraph-memory]\ncommand = \"{bin}\"\nargs = [\"mcp\", \"--db\", \"{db}\"]\n")
}

/// Connection snippets for the agents, with real paths (printed after import and shown by the desktop app).
pub fn agent_snippets(bin: &str, db: &str) -> Value {
    json!({
        "claude_code": format!("claude mcp add neuralgraph-memory -- {bin} mcp --db {db}"),
        "gemini_settings_json": mcp_json(bin, db),
        "codex_config_toml": codex_toml(bin, db),
        "generic_mcp_json": mcp_json(bin, db),
        "http_mcp": "neuralgraph-memory serve, then http://127.0.0.1:8765/mcp",
    })
}

fn readme(manifest: &Value) -> String {
    let mut s = String::from(
        "# NeuralGraph brain archive\n\n\
         This archive is a complete, model-independent copy of a NeuralGraph memory store. It was written only to the\n\
         location you chose; NeuralGraph never uploads it anywhere.\n\n\
         - `manifest.json`: format version, scope, counts, embedding model and dimension, privacy findings and the\n  SHA-256 of every file. `SHA256SUMS` repeats the checksums (`shasum -a 256 -c SHA256SUMS`).\n\
         - `stores/<name>/brain.sqlite`: the authoritative SQLite snapshot (open with any SQLite tool).\n\
         - `stores/<name>/jsonl/`: every row of every table as JSON Lines; BLOBs are `{\"$b64\": ...}`, embeddings\n  are little-endian float32 vectors.\n\
         - `stores/<name>/graph.graphml`: the knowledge graph for Gephi, yEd, NetworkX, Neo4j, ...\n\
         - `stores/<name>/memories.md`: every memory with its sources, quotes, entities and history.\n\
         - `agents/`: MCP configuration for Claude Code, Gemini CLI, Codex and other agents.\n\n\
         ## Import\n\n```bash\n\
         neuralgraph-memory brain import brain.zip --db ~/.neuralgraph/chat_memory.db            # restore into a fresh install\n\
         neuralgraph-memory brain import brain.zip --db ~/.neuralgraph/chat_memory.db --merge    # merge into an existing brain\n\
         neuralgraph-memory brain verify brain.zip --db ~/.neuralgraph/chat_memory.db            # round-trip check\n```\n\n",
    );
    if manifest["privacy"]["confidential"].as_bool() == Some(true) {
        s.push_str("**Warning:** this archive may contain confidential content (see `privacy` in manifest.json).\n\n");
    }
    s
}

fn configured_embed_model(meta_model: Option<String>, explicit: Option<String>) -> (String, &'static str) {
    if let Some(m) = explicit.filter(|m| !m.is_empty()) {
        return (m, "explicit");
    }
    if let Some(m) = meta_model.filter(|m| !m.is_empty()) {
        return (m, "store meta");
    }
    match std::env::var("EMBED_MODEL").ok().filter(|m| !m.is_empty()) {
        Some(m) => (m, "EMBED_MODEL"),
        None => (crate::backend::default_embed_model(), "default"),
    }
}

/// Preview what an export would contain (counts and privacy findings) without writing anything.
pub fn preview(stores: &[(String, String)], chat_prefixes: &[String]) -> Result<Value> {
    let scratch = Scratch(scratch_dir("preview")?);
    let mut out = Vec::new();
    for (name, path) in stores {
        let snap = scratch.0.join(format!("{name}.sqlite"));
        snapshot(&expand_home(path), &snap)?;
        let c = Connection::open(&snap)?;
        if !chat_prefixes.is_empty() {
            apply_scope(&c, chat_prefixes)?;
        }
        let mut counts = IndexMap::new();
        for t in user_tables(&c)? {
            counts.insert(t.clone(), count(&c, &t)?);
        }
        let chats = strings(&c, "SELECT chat_id, COALESCE(title, ''), CAST(message_count AS TEXT) FROM chats ORDER BY updated_at DESC")?
            .into_iter()
            .map(|r| json!({"chat_id": r[0], "title": r[1], "messages": r[2]}))
            .collect::<Vec<_>>();
        out.push(json!({"name": name, "counts": counts, "chats": chats, "privacy": privacy_scan(&c)?}));
    }
    let confidential = out.iter().any(|s| s["privacy"].as_array().is_some_and(|a| a.iter().any(|f| f["category"] != "email_address" && f["category"] != "phone_number")));
    Ok(json!({"stores": out, "confidential": confidential}))
}

#[derive(Debug, Clone)]
pub struct ExportReport {
    pub path: PathBuf,
    pub manifest: Value,
    pub bytes: u64,
}

pub fn export_brain(opts: &ExportOptions) -> Result<ExportReport> {
    if opts.stores.is_empty() {
        return Err(err("nothing to export: no stores given"));
    }
    let names: HashSet<&String> = opts.stores.iter().map(|(n, _)| n).collect();
    if names.len() != opts.stores.len() {
        return Err(err("store names must be unique"));
    }
    if opts.out.exists() && !opts.overwrite {
        return Err(err(format!("{} already exists (pass --overwrite to replace it)", opts.out.display())));
    }
    let scratch = Scratch(scratch_dir("export")?);
    let archive_id = uuid::Uuid::new_v4().simple().to_string();
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    let mut stores_meta = Vec::new();
    let mut privacy_all = Vec::new();
    for (name, path) in &opts.stores {
        let src = expand_home(path);
        let snap = scratch.0.join(format!("{name}.sqlite"));
        snapshot(&src, &snap)?;
        {
            let c = Connection::open(&snap)?;
            c.execute_batch("PRAGMA journal_mode=DELETE;")?;
            if !opts.chat_prefixes.is_empty() {
                apply_scope(&c, &opts.chat_prefixes)?;
            }
            if !opts.include_embeddings {
                c.execute("UPDATE memories SET embedding = NULL, embedding_dim = NULL", [])?;
            }
            if !opts.chat_prefixes.is_empty() || !opts.include_embeddings {
                for fts in ["memories_fts", "messages_fts"] {
                    if table_exists(&c, fts)? {
                        c.execute(&format!("INSERT INTO {fts}({fts}) VALUES('rebuild')"), [])?;
                    }
                }
                c.execute_batch("VACUUM;")?;
            }
        }
        let c = Connection::open_with_flags(&snap, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let schema: i64 = c
            .query_row("SELECT value FROM meta WHERE key='schema_version'", [], |r| r.get::<_, String>(0))
            .optional()?
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or(0);
        let meta_dim: Option<i64> = c
            .query_row("SELECT value FROM meta WHERE key='embed_dim'", [], |r| r.get::<_, String>(0))
            .optional()?
            .and_then(|v| v.trim().parse().ok());
        let meta_model: Option<String> =
            c.query_row("SELECT value FROM meta WHERE key='embed_model'", [], |r| r.get(0)).optional()?;
        let dims = strings(&c, "SELECT CAST(embedding_dim AS TEXT), CAST(COUNT(*) AS TEXT) FROM memories WHERE embedding IS NOT NULL GROUP BY embedding_dim")?;
        let embedded: i64 = dims.iter().filter_map(|r| r[1].as_deref()?.parse::<i64>().ok()).sum();
        let (model, model_source) = configured_embed_model(meta_model, opts.embed_model.clone());
        let mut counts = IndexMap::new();
        for t in user_tables(&c)? {
            counts.insert(t.clone(), count(&c, &t)?);
        }
        let (graphml, gc) = build_graph(&c)?;
        let md = build_markdown(&c, name)?;
        let privacy = privacy_scan(&c)?;
        let base = format!("stores/{name}");
        for (table, bytes, _) in dump_jsonl(&c)? {
            files.push((format!("{base}/jsonl/{table}.jsonl"), bytes));
        }
        files.push((format!("{base}/graph.graphml"), graphml.into_bytes()));
        files.push((format!("{base}/memories.md"), md.into_bytes()));
        drop(c);
        files.push((format!("{base}/brain.sqlite"), std::fs::read(&snap)?));
        for p in &privacy {
            privacy_all.push(json!({"store": name, "category": p["category"], "count": p["count"], "example_ids": p["example_ids"]}));
        }
        stores_meta.push(json!({
            "name": name,
            "origin_file": src.file_name().map(|f| f.to_string_lossy().to_string()),
            "schema_version": schema,
            "sqlite": format!("{base}/brain.sqlite"),
            "jsonl_dir": format!("{base}/jsonl/"),
            "graphml": format!("{base}/graph.graphml"),
            "markdown": format!("{base}/memories.md"),
            "counts": counts,
            "graph": gc.to_value(),
            "embeddings": {
                "included": opts.include_embeddings,
                "count": embedded,
                "dim": meta_dim.or_else(|| dims.first().and_then(|r| r[0].as_deref()?.parse().ok())),
                "dims_present": dims.iter().map(|r| json!({"dim": r[0], "count": r[1]})).collect::<Vec<_>>(),
                "model": model,
                "model_source": model_source,
                "encoding": "float32-le",
                "portable_note": "vectors are only comparable with the same model; on a different model, import drops them and the worker re-embeds",
            },
            "derived_not_included": ["<db>.ivf disk vector index (rebuilt from embeddings)", "FTS5 shadow tables (rebuilt on open)"],
        }));
    }
    let confidential = privacy_all.iter().any(|f| f["category"] != "email_address" && f["category"] != "phone_number");
    for (n, _) in &opts.stores {
        files.extend(agent_files(n).into_iter().map(|(p, b)| (p, b.into_bytes())));
        break;
    }
    let mut manifest = json!({
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "archive_id": archive_id,
        "created_at": now_iso(),
        "created_by": {"tool": "neuralgraph-memory", "version": env!("CARGO_PKG_VERSION"), "store_schema_version": SCHEMA_VERSION},
        "scope": if opts.chat_prefixes.is_empty() { json!({"kind": "full"}) } else { json!({"kind": "project", "chat_prefixes": opts.chat_prefixes}) },
        "stores": stores_meta,
        "privacy": {
            "contains_raw_messages": true,
            "confidential": confidential,
            "findings": privacy_all,
            "notice": "This archive holds your raw chat messages and memories. NeuralGraph never uploads it; keep it somewhere private.",
        },
    });
    let readme_bytes = readme(&manifest).into_bytes();
    files.push(("README.md".into(), readme_bytes));
    let listing: Vec<Value> = files
        .iter()
        .map(|(p, b)| json!({"path": p, "bytes": b.len(), "sha256": sha256_hex(b)}))
        .collect();
    manifest["files"] = Value::Array(listing);
    let manifest_bytes = (serde_json::to_string_pretty(&manifest).map_err(|e| err(e.to_string()))? + "\n").into_bytes();
    let mut sums = String::new();
    for (p, b) in &files {
        sums.push_str(&format!("{}  {p}\n", sha256_hex(b)));
    }
    sums.push_str(&format!("{}  manifest.json\n", sha256_hex(&manifest_bytes)));

    if let Some(parent) = opts.out.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let partial = opts.out.with_extension("zip.partial");
    {
        let f = std::fs::File::create(&partial)?;
        let mut zw = zip::ZipWriter::new(std::io::BufWriter::new(f));
        use chrono::{Datelike, Timelike};
        let now = chrono::Local::now();
        let stamp = zip::DateTime::from_date_and_time(
            now.year() as u16, now.month() as u8, now.day() as u8, now.hour() as u8, now.minute() as u8, now.second() as u8,
        )
        .unwrap_or_default();
        let o = zip::write::SimpleFileOptions::default()
            .last_modified_time(stamp)
            .compression_method(zip::CompressionMethod::Deflated)
            .large_file(true)
            .unix_permissions(0o600);
        let zerr = |e: zip::result::ZipError| err(format!("zip: {e}"));
        zw.start_file("manifest.json", o).map_err(zerr)?;
        zw.write_all(&manifest_bytes)?;
        zw.start_file("SHA256SUMS", o).map_err(zerr)?;
        zw.write_all(sums.as_bytes())?;
        for (p, b) in &files {
            zw.start_file(p.as_str(), o).map_err(zerr)?;
            zw.write_all(b)?;
        }
        zw.finish().map_err(zerr)?.flush()?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&partial, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&partial, &opts.out)?;
    let bytes = std::fs::metadata(&opts.out)?.len();
    Ok(ExportReport { path: opts.out.clone(), manifest, bytes })
}

// ------------------------------------------------------------------------------------------------ open / inspect

/// An archive whose checksums have been verified.
pub struct Archive {
    pub manifest: Value,
    files: HashMap<String, Vec<u8>>,
}

impl Archive {
    pub fn open(path: &Path) -> Result<Self> {
        let f = std::fs::File::open(path).map_err(|e| err(format!("cannot open {}: {e}", path.display())))?;
        let mut za = zip::ZipArchive::new(std::io::BufReader::new(f)).map_err(|e| err(format!("not a ZIP archive: {e}")))?;
        let mut files = HashMap::new();
        for i in 0..za.len() {
            let mut entry = za.by_index(i).map_err(|e| err(format!("zip: {e}")))?;
            if entry.is_dir() {
                continue;
            }
            let name = entry.name().map_err(|e| err(format!("zip: {e}")))?.to_string();
            if name.contains("..") || name.starts_with('/') {
                return Err(err(format!("unsafe path in archive: {name}")));
            }
            let mut buf = Vec::with_capacity(entry.size() as usize);
            entry.read_to_end(&mut buf)?;
            files.insert(name, buf);
        }
        let manifest: Value = serde_json::from_slice(
            files.get("manifest.json").ok_or_else(|| err("archive has no manifest.json"))?,
        )
        .map_err(|e| err(format!("manifest.json is not valid JSON: {e}")))?;
        if manifest["format"] != FORMAT {
            return Err(err(format!("not a NeuralGraph brain archive (format {})", manifest["format"])));
        }
        let ver = manifest["format_version"].as_i64().unwrap_or(0);
        if ver > FORMAT_VERSION {
            return Err(err(format!("archive format {ver} is newer than this NeuralGraph ({FORMAT_VERSION}); update first")));
        }
        let listed = manifest["files"].as_array().cloned().unwrap_or_default();
        if listed.is_empty() {
            return Err(err("manifest lists no files"));
        }
        let mut bad = Vec::new();
        for f in &listed {
            let p = f["path"].as_str().unwrap_or("");
            match files.get(p) {
                None => bad.push(format!("{p}: missing")),
                Some(b) if f["sha256"].as_str() != Some(sha256_hex(b).as_str()) => bad.push(format!("{p}: checksum mismatch")),
                _ => {}
            }
        }
        if let Some(sums) = files.get("SHA256SUMS") {
            let text = String::from_utf8_lossy(sums);
            if let Some(line) = text.lines().find(|l| l.ends_with("  manifest.json")) {
                if !line.starts_with(&sha256_hex(&files["manifest.json"])) {
                    bad.push("manifest.json: checksum mismatch".into());
                }
            }
        }
        if !bad.is_empty() {
            return Err(err(format!("archive is corrupt or was modified: {}", bad.join("; "))));
        }
        Ok(Archive { manifest, files })
    }

    pub fn store_names(&self) -> Vec<String> {
        self.manifest["stores"].as_array().into_iter().flatten().filter_map(|s| s["name"].as_str().map(str::to_string)).collect()
    }

    fn pick_store(&self, name: Option<&str>) -> Result<Value> {
        let stores = self.manifest["stores"].as_array().cloned().unwrap_or_default();
        match name {
            Some(n) => stores.into_iter().find(|s| s["name"] == n).ok_or_else(|| err(format!("archive has no store '{n}' (has: {})", self.store_names().join(", ")))),
            None if stores.len() == 1 => Ok(stores[0].clone()),
            None => Err(err(format!("archive has several stores; pick one with --store ({})", self.store_names().join(", ")))),
        }
    }

    /// Write a store's SQLite snapshot to `dest`.
    fn extract_sqlite(&self, store: &Value, dest: &Path) -> Result<()> {
        let p = store["sqlite"].as_str().unwrap_or("");
        let bytes = self.files.get(p).ok_or_else(|| err(format!("archive is missing {p}")))?;
        std::fs::write(dest, bytes)?;
        Ok(())
    }
}

/// Manifest plus checksum status, for `brain inspect` and the desktop import dialog.
pub fn inspect(path: &Path) -> Result<Value> {
    let a = Archive::open(path)?;
    let mut m = a.manifest.clone();
    m["checksums_ok"] = json!(true);
    if let Some(o) = m.as_object_mut() {
        o.remove("files");
    }
    m["file_count"] = json!(a.files.len());
    Ok(m)
}

// ------------------------------------------------------------------------------------------------ import

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ImportMode {
    /// copy the archived store verbatim into an empty (or explicitly replaced) location
    Restore,
    /// add the archived knowledge to an existing store without ID collisions, keeping both histories
    Merge,
}

#[derive(Debug, Clone)]
pub struct ImportOptions {
    pub archive: PathBuf,
    pub db: String,
    pub mode: ImportMode,
    pub store: Option<String>,
    /// restore over a non-empty store (the old files are moved aside, never deleted)
    pub replace: bool,
}

fn target_memory_count(db: &Path) -> Result<i64> {
    if !db.exists() {
        return Ok(0);
    }
    let c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    if !table_exists(&c, "memories")? {
        return Ok(0);
    }
    Ok(count(&c, "memories")? + if table_exists(&c, "messages")? { count(&c, "messages")? } else { 0 })
}

fn sidecars(db: &Path) -> Vec<PathBuf> {
    ["-wal", "-shm", ".ivf", ".ivf.ids"]
        .iter()
        .map(|s| PathBuf::from(format!("{}{s}", db.display())))
        .collect()
}

fn record_import(c: &Connection, manifest: &Value, store: &str, mode: &str, summary: &Value) -> Result<()> {
    let prev: Option<String> = c.query_row("SELECT value FROM meta WHERE key='brain_imports'", [], |r| r.get(0)).optional()?;
    let mut list: Vec<Value> = prev.and_then(|p| serde_json::from_str(&p).ok()).unwrap_or_default();
    let entry = json!({
        "archive_id": manifest["archive_id"], "archive_created_at": manifest["created_at"], "store": store,
        "mode": mode, "imported_at": now_iso(), "summary": summary,
    });
    list.push(entry.clone());
    c.execute(
        "INSERT INTO meta(key, value) VALUES ('brain_imports', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [serde_json::to_string(&list).unwrap_or_default()],
    )?;
    c.execute(
        "INSERT INTO audit_log(at, kind, ref, detail) VALUES (?, 'brain_import', ?, ?)",
        params![now_iso(), manifest["archive_id"].as_str(), serde_json::to_string(&entry).unwrap_or_default()],
    )?;
    Ok(())
}

pub fn import_brain(opts: &ImportOptions) -> Result<Value> {
    let archive = Archive::open(&opts.archive)?;
    let store = archive.pick_store(opts.store.as_deref())?;
    let store_name = store["name"].as_str().unwrap_or("brain").to_string();
    let schema = store["schema_version"].as_i64().unwrap_or(0);
    if schema > SCHEMA_VERSION {
        return Err(err(format!("archived store schema {schema} is newer than this NeuralGraph ({SCHEMA_VERSION})")));
    }
    let db = expand_home(&opts.db);
    if let Some(parent) = db.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let scratch = Scratch(scratch_dir("import")?);
    let snap = scratch.0.join("src.sqlite");
    archive.extract_sqlite(&store, &snap)?;
    match opts.mode {
        ImportMode::Restore => restore(&archive.manifest, &store_name, &snap, &db, opts.replace),
        ImportMode::Merge => merge(&archive.manifest, &store, &snap, &db),
    }
}

fn restore(manifest: &Value, store: &str, snap: &Path, db: &Path, replace: bool) -> Result<Value> {
    let mut moved_aside = None;
    if target_memory_count(db)? > 0 {
        if !replace {
            return Err(err(format!(
                "{} already holds memories; use merge, or restore with --replace (the current files are kept as a backup)",
                db.display()
            )));
        }
        let stamp = chrono::Utc::now().format("%Y%m%dT%H%M%SZ");
        let backup = PathBuf::from(format!("{}.before-import-{stamp}", db.display()));
        std::fs::rename(db, &backup)?;
        for (side, s) in sidecars(db).into_iter().zip(["-wal", "-shm", ".ivf", ".ivf.ids"]) {
            if side.exists() {
                std::fs::rename(&side, format!("{}{s}", backup.display()))?;
            }
        }
        moved_aside = Some(backup.display().to_string());
    } else {
        for side in sidecars(db) {
            let _ = std::fs::remove_file(side);
        }
        if db.exists() {
            std::fs::remove_file(db)?;
        }
    }
    let tmp = PathBuf::from(format!("{}.importing", db.display()));
    std::fs::copy(snap, &tmp)?;
    std::fs::rename(&tmp, db)?;
    // open once through the store so WAL, FTS and schema checks run exactly as in normal use
    let s = ChatMemoryStore::open(&db.to_string_lossy())?;
    let summary = s.with_conn(|c| {
        let mut counts = IndexMap::new();
        for t in user_tables(c)? {
            counts.insert(t.clone(), count(c, &t)?);
        }
        let summary = json!({"counts": counts, "moved_aside": moved_aside});
        record_import(c, manifest, store, "restore", &summary)?;
        Ok(summary)
    })?;
    s.close();
    Ok(json!({"mode": "restore", "db": db.display().to_string(), "store": store, "result": summary}))
}

type Row = Vec<(String, Sql)>;

fn rows(c: &Connection, sql: &str) -> Result<Vec<Row>> {
    let mut st = c.prepare(sql)?;
    let cols: Vec<String> = st.column_names().iter().map(|s| s.to_string()).collect();
    let mut q = st.query([])?;
    let mut out = Vec::new();
    while let Some(r) = q.next()? {
        let mut row = Vec::with_capacity(cols.len());
        for (i, col) in cols.iter().enumerate() {
            row.push((col.clone(), r.get::<_, Sql>(i)?));
        }
        out.push(row);
    }
    Ok(out)
}

fn get<'a>(row: &'a Row, col: &str) -> &'a Sql {
    row.iter().find(|(c, _)| c == col).map(|(_, v)| v).unwrap_or(&Sql::Null)
}

fn get_s(row: &Row, col: &str) -> Option<String> {
    match get(row, col) {
        Sql::Text(s) => Some(s.clone()),
        Sql::Integer(i) => Some(i.to_string()),
        Sql::Real(f) => Some(f.to_string()),
        _ => None,
    }
}

fn set(row: &mut Row, col: &str, v: Sql) {
    if let Some(slot) = row.iter_mut().find(|(c, _)| c == col) {
        slot.1 = v;
    }
}

fn insert(c: &Connection, table: &str, row: &Row, skip: &[&str], verb: &str) -> Result<usize> {
    let cols: Vec<&(String, Sql)> = row.iter().filter(|(k, _)| !skip.contains(&k.as_str())).collect();
    let sql = format!(
        "INSERT {verb} INTO {table} ({}) VALUES ({})",
        cols.iter().map(|(k, _)| format!("\"{k}\"")).collect::<Vec<_>>().join(", "),
        vec!["?"; cols.len()].join(", ")
    );
    Ok(c.execute(&sql, rusqlite::params_from_iter(cols.iter().map(|(_, v)| v)))?)
}

fn with_import_note(metadata: Option<String>, original_id: &str, archive_id: &str) -> String {
    let mut m: Map<String, Value> = metadata.and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default();
    m.insert("imported_from".into(), json!({"archive_id": archive_id, "original_id": original_id}));
    let sorted: std::collections::BTreeMap<String, Value> = m.into_iter().collect();
    serde_json::to_string(&sorted).unwrap_or_else(|_| "{}".into())
}

fn merge(manifest: &Value, store: &Value, snap: &Path, db: &Path) -> Result<Value> {
    let archive_id = manifest["archive_id"].as_str().unwrap_or("").to_string();
    let tag = archive_id.chars().take(8).collect::<String>();
    let store_name = store["name"].as_str().unwrap_or("brain").to_string();
    // create the schema if the target is new
    ChatMemoryStore::open(&db.to_string_lossy())?.close();
    let src = Connection::open_with_flags(snap, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut tgt = Connection::open(db)?;
    tgt.busy_timeout(std::time::Duration::from_secs(10))?;
    let prev: Option<String> = tgt.query_row("SELECT value FROM meta WHERE key='brain_imports'", [], |r| r.get(0)).optional()?;
    if prev.is_some_and(|p| p.contains(&format!("\"archive_id\":\"{archive_id}\""))) {
        return Err(err(format!("archive {archive_id} was already imported into {}", db.display())));
    }
    let tx = tgt.transaction()?;
    let mut stats: IndexMap<&str, i64> = IndexMap::new();
    let mut bump = |k: &'static str, n: i64| *stats.entry(k).or_insert(0) += n;

    // embeddings: keep them only when the dimension (and so, very likely, the model) matches
    let src_dim: Option<String> = src.query_row("SELECT value FROM meta WHERE key='embed_dim'", [], |r| r.get(0)).optional()?;
    let tgt_dim: Option<String> = tx.query_row("SELECT value FROM meta WHERE key='embed_dim'", [], |r| r.get(0)).optional()?;
    let tgt_has_vectors: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM memories WHERE embedding IS NOT NULL)", [], |r| r.get(0))?;
    let drop_vectors = matches!((&src_dim, &tgt_dim), (Some(a), Some(b)) if a != b) && tgt_has_vectors;
    for r in rows(&src, "SELECT key, value FROM meta")? {
        let k = get_s(&r, "key").unwrap_or_default();
        if k != "schema_version" && k != "brain_imports" && !(k == "embed_dim" && drop_vectors) {
            tx.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", params![k, get_s(&r, "value")])?;
        }
    }

    // chats: same id + compatible messages = same chat; a diverged chat is imported under a new id
    let mut chat_map: HashMap<String, String> = HashMap::new();
    let src_chats = rows(&src, "SELECT * FROM chats")?;
    let mut all_chat_ids: HashSet<String> = src_chats.iter().filter_map(|r| get_s(r, "chat_id")).collect();
    for t in ["messages", "memories", "relations"] {
        for r in strings(&src, &format!("SELECT DISTINCT chat_id FROM {t}"))? {
            all_chat_ids.extend(r[0].clone());
        }
    }
    for chat in &all_chat_ids {
        let mut conflict = false;
        for r in rows(&src, &format!("SELECT message_id, seq, content_hash FROM messages WHERE chat_id = '{}'", chat.replace('\'', "''")))? {
            let mid = get_s(&r, "message_id").unwrap_or_default();
            let seq = match get(&r, "seq") { Sql::Integer(i) => *i, _ => 0 };
            let hash = get_s(&r, "content_hash");
            let by_seq: Option<(String, String)> = tx
                .query_row("SELECT message_id, content_hash FROM messages WHERE chat_id=? AND seq=?", params![chat, seq], |x| Ok((x.get(0)?, x.get(1)?)))
                .optional()?;
            let by_id: Option<(String, i64, String)> = tx
                .query_row("SELECT chat_id, seq, content_hash FROM messages WHERE message_id=?", [&mid], |x| Ok((x.get(0)?, x.get(1)?, x.get(2)?)))
                .optional()?;
            if by_seq.is_some_and(|(m, h)| m != mid || Some(h) != hash)
                || by_id.is_some_and(|(ch, s, h)| ch != *chat || s != seq || Some(h) != hash)
            {
                conflict = true;
                break;
            }
        }
        let mapped = if conflict { format!("{chat}.{tag}") } else { chat.clone() };
        if conflict {
            bump("chats_renamed", 1);
        }
        chat_map.insert(chat.clone(), mapped);
    }
    let cmap = |c: &str| chat_map.get(c).cloned().unwrap_or_else(|| c.to_string());
    for mut r in src_chats {
        let old = get_s(&r, "chat_id").unwrap_or_default();
        set(&mut r, "chat_id", Sql::Text(cmap(&old)));
        let n = insert(&tx, "chats", &r, &[], "OR IGNORE")?;
        bump(if n > 0 { "chats_added" } else { "chats_existing" }, 1);
        if n == 0 {
            tx.execute(
                "UPDATE chats SET updated_at = MAX(updated_at, ?), created_at = MIN(created_at, ?) WHERE chat_id = ?",
                params![get_s(&r, "updated_at"), get_s(&r, "created_at"), cmap(&old)],
            )?;
        }
    }

    // messages
    let mut msg_map: HashMap<String, String> = HashMap::new();
    for mut r in rows(&src, "SELECT * FROM messages ORDER BY rid")? {
        let mid = get_s(&r, "message_id").unwrap_or_default();
        let chat = cmap(&get_s(&r, "chat_id").unwrap_or_default());
        let existing: Option<(String, String)> = tx
            .query_row("SELECT chat_id, content_hash FROM messages WHERE message_id=?", [&mid], |x| Ok((x.get(0)?, x.get(1)?)))
            .optional()?;
        let new_id = match existing {
            Some((ch, h)) if ch == chat && get_s(&r, "content_hash").as_deref() == Some(h.as_str()) => {
                msg_map.insert(mid.clone(), mid.clone());
                bump("messages_existing", 1);
                continue;
            }
            Some(_) => format!("{mid}.{tag}"),
            None => mid.clone(),
        };
        if new_id != mid {
            let note = with_import_note(get_s(&r, "metadata"), &mid, &archive_id);
            set(&mut r, "metadata", Sql::Text(note));
            bump("messages_renamed", 1);
        }
        set(&mut r, "message_id", Sql::Text(new_id.clone()));
        set(&mut r, "chat_id", Sql::Text(chat));
        insert(&tx, "messages", &r, &["rid"], "")?;
        msg_map.insert(mid, new_id);
        bump("messages_added", 1);
    }

    // memories (supersession pointers are rewritten after every id is known)
    let mut mem_map: HashMap<String, String> = HashMap::new();
    let mut pending: Vec<Row> = Vec::new();
    for mut r in rows(&src, "SELECT * FROM memories ORDER BY rid")? {
        let id = get_s(&r, "memory_id").unwrap_or_default();
        let existing: Option<(String, String)> = tx
            .query_row("SELECT text_hash, created_at FROM memories WHERE memory_id=?", [&id], |x| Ok((x.get(0)?, x.get(1)?)))
            .optional()?;
        let new_id = match existing {
            Some((h, c)) if get_s(&r, "text_hash").as_deref() == Some(h.as_str()) && get_s(&r, "created_at").as_deref() == Some(c.as_str()) => {
                mem_map.insert(id.clone(), id.clone());
                bump("memories_existing", 1);
                continue;
            }
            Some(_) => format!("{id}.{tag}"),
            None => id.clone(),
        };
        if new_id != id {
            let note = with_import_note(get_s(&r, "metadata"), &id, &archive_id);
            set(&mut r, "metadata", Sql::Text(note));
            bump("memories_renamed", 1);
        }
        set(&mut r, "memory_id", Sql::Text(new_id.clone()));
        let chat = cmap(&get_s(&r, "chat_id").unwrap_or_default());
        set(&mut r, "chat_id", Sql::Text(chat));
        if drop_vectors && !matches!(get(&r, "embedding"), Sql::Null) {
            set(&mut r, "embedding", Sql::Null);
            set(&mut r, "embedding_dim", Sql::Null);
            bump("embeddings_dropped_for_reembedding", 1);
        }
        mem_map.insert(id, new_id);
        pending.push(r);
    }
    let mmap = |m: &str| mem_map.get(m).cloned().unwrap_or_else(|| m.to_string());
    for mut r in pending {
        if let Some(sup) = get_s(&r, "superseded_by") {
            set(&mut r, "superseded_by", Sql::Text(mmap(&sup)));
        }
        insert(&tx, "memories", &r, &["rid"], "")?;
        bump("memories_added", 1);
    }
    let msgmap = |m: &str| msg_map.get(m).cloned().unwrap_or_else(|| m.to_string());

    for r in rows(&src, "SELECT * FROM memory_sources")? {
        let n = tx.execute(
            "INSERT OR IGNORE INTO memory_sources(memory_id, message_id) VALUES (?, ?)",
            params![mmap(&get_s(&r, "memory_id").unwrap_or_default()), msgmap(&get_s(&r, "message_id").unwrap_or_default())],
        )?;
        bump("memory_sources_added", n as i64);
    }

    // entities are keyed by their normalised name, so the same id is the same entity on both sides
    for r in rows(&src, "SELECT * FROM entities")? {
        if insert(&tx, "entities", &r, &[], "OR IGNORE")? > 0 {
            bump("entities_added", 1);
        } else {
            tx.execute(
                "UPDATE entities SET mention_count = MAX(mention_count, ?),
                     first_seen_at = CASE WHEN first_seen_at IS NULL OR (? IS NOT NULL AND ? < first_seen_at) THEN ? ELSE first_seen_at END,
                     last_seen_at = CASE WHEN last_seen_at IS NULL OR (? IS NOT NULL AND ? > last_seen_at) THEN ? ELSE last_seen_at END,
                     type = CASE WHEN type = '' THEN ? ELSE type END
                 WHERE entity_id = ?",
                params![
                    match get(&r, "mention_count") { Sql::Integer(i) => *i, _ => 0 },
                    get_s(&r, "first_seen_at"), get_s(&r, "first_seen_at"), get_s(&r, "first_seen_at"),
                    get_s(&r, "last_seen_at"), get_s(&r, "last_seen_at"), get_s(&r, "last_seen_at"),
                    get_s(&r, "type").unwrap_or_default(), get_s(&r, "entity_id"),
                ],
            )?;
            bump("entities_merged", 1);
        }
    }
    for r in rows(&src, "SELECT * FROM entity_aliases")? {
        bump("aliases_added", insert(&tx, "entity_aliases", &r, &[], "OR IGNORE")? as i64);
    }
    for r in rows(&src, "SELECT * FROM memory_entities")? {
        let n = tx.execute(
            "INSERT OR IGNORE INTO memory_entities(memory_id, entity_id, role) VALUES (?, ?, ?)",
            params![mmap(&get_s(&r, "memory_id").unwrap_or_default()), get_s(&r, "entity_id"), get_s(&r, "role").unwrap_or_else(|| "mention".into())],
        )?;
        bump("memory_entities_added", n as i64);
    }

    // relations: one row per (subject, predicate, object); independent observations of the same triple add up
    for mut r in rows(&src, "SELECT * FROM relations")? {
        let rid = get_s(&r, "relation_id").unwrap_or_default();
        if let Some(m) = get_s(&r, "memory_id") {
            set(&mut r, "memory_id", Sql::Text(mmap(&m)));
        }
        if let Some(m) = get_s(&r, "message_id") {
            set(&mut r, "message_id", Sql::Text(msgmap(&m)));
        }
        let chat = cmap(&get_s(&r, "chat_id").unwrap_or_default());
        set(&mut r, "chat_id", Sql::Text(chat));
        let (s, p, o) = (get_s(&r, "subject_id"), get_s(&r, "predicate"), get_s(&r, "object_id"));
        let same_triple: Option<String> = tx
            .query_row("SELECT relation_id FROM relations WHERE subject_id=? AND predicate=? AND object_id=?", params![s, p, o], |x| x.get(0))
            .optional()?;
        let obs = match get(&r, "observation_count") { Sql::Integer(i) => *i, _ => 1 };
        let conf = match get(&r, "confidence") { Sql::Real(f) => *f, Sql::Integer(i) => *i as f64, _ => 0.5 };
        match same_triple {
            Some(existing) => {
                let add = if existing == rid { "MAX(observation_count, ?1)" } else { "observation_count + ?1" };
                tx.execute(
                    &format!("UPDATE relations SET observation_count = {add}, confidence = MAX(confidence, ?2),
                                 updated_at = MAX(updated_at, ?3) WHERE relation_id = ?4"),
                    params![obs, conf, get_s(&r, "updated_at"), existing],
                )?;
                bump("relations_merged", 1);
            }
            None => {
                let taken: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM relations WHERE relation_id=?)", [&rid], |x| x.get(0))?;
                if taken {
                    set(&mut r, "relation_id", Sql::Text(format!("{rid}.{tag}")));
                    bump("relations_renamed", 1);
                }
                insert(&tx, "relations", &r, &[], "")?;
                bump("relations_added", 1);
            }
        }
    }
    for r in rows(&src, "SELECT * FROM memory_links")? {
        let n = tx.execute(
            "INSERT OR IGNORE INTO memory_links(source_id, target_id, link_type, weight, created_at) VALUES (?, ?, ?, ?, ?)",
            params![
                mmap(&get_s(&r, "source_id").unwrap_or_default()),
                mmap(&get_s(&r, "target_id").unwrap_or_default()),
                get_s(&r, "link_type"),
                match get(&r, "weight") { Sql::Real(f) => *f, Sql::Integer(i) => *i as f64, _ => 1.0 },
                get_s(&r, "created_at"),
            ],
        )?;
        bump("memory_links_added", n as i64);
    }

    // worker checkpoints and the audit trail come along with their references rewritten
    let ref_map = |r: &str| -> String {
        msg_map.get(r).or_else(|| mem_map.get(r)).or_else(|| chat_map.get(r)).cloned().unwrap_or_else(|| r.to_string())
    };
    for mut r in rows(&src, "SELECT * FROM jobs ORDER BY job_id")? {
        if let Some(chat) = get_s(&r, "chat_id") {
            set(&mut r, "chat_id", Sql::Text(cmap(&chat)));
        }
        if let Some(rf) = get_s(&r, "ref_id") {
            let new = ref_map(&rf);
            if let Some(dk) = get_s(&r, "dedupe_key") {
                set(&mut r, "dedupe_key", Sql::Text(dk.replace(&rf, &new)));
            }
            set(&mut r, "ref_id", Sql::Text(new));
        }
        bump("jobs_added", insert(&tx, "jobs", &r, &["job_id"], "OR IGNORE")? as i64);
    }
    for mut r in rows(&src, "SELECT * FROM audit_log ORDER BY id")? {
        if let Some(rf) = get_s(&r, "ref") {
            set(&mut r, "ref", Sql::Text(ref_map(&rf)));
        }
        bump("audit_added", insert(&tx, "audit_log", &r, &["id"], "")? as i64);
    }
    // any extra tables a newer writer added are copied as-is
    for t in user_tables(&src)? {
        if !CORE_TABLES.contains(&t.as_str()) && table_exists(&tx, &t)? {
            for r in rows(&src, &format!("SELECT * FROM \"{t}\""))? {
                insert(&tx, &t, &r, &[], "OR IGNORE")?;
            }
        }
    }
    tx.execute_batch("UPDATE chats SET message_count = (SELECT COUNT(*) FROM messages m WHERE m.chat_id = chats.chat_id);")?;
    let summary = json!({"stats": stats, "embeddings_dropped": drop_vectors, "archive_tag": tag});
    record_import(&tx, manifest, &store_name, "merge", &summary)?;
    tx.commit()?;
    // a disk vector index built before the merge no longer covers every memory
    for side in sidecars(db).into_iter().skip(2) {
        let _ = std::fs::remove_file(side);
    }
    Ok(json!({"mode": "merge", "db": db.display().to_string(), "store": store_name, "result": summary}))
}

// ------------------------------------------------------------------------------------------------ verify

#[derive(Debug, Clone)]
pub struct Check {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

/// Compare an archive with an imported store. `exact` (restore) also requires identical ids, counts and
/// retrieval results; otherwise (merge) every archived item must be present, possibly under a new id.
pub async fn verify(archive_path: &Path, db: &str, store: Option<&str>, exact: bool, retrieval_samples: usize) -> Result<Value> {
    let archive = Archive::open(archive_path)?;
    let st = archive.pick_store(store)?;
    let scratch = Scratch(scratch_dir("verify")?);
    let snap = scratch.0.join("src.sqlite");
    archive.extract_sqlite(&st, &snap)?;
    let target = expand_home(db);
    if !target.exists() {
        return Err(err(format!("no database at {}", target.display())));
    }
    // read the target from a copy so verification never writes to it
    let tgt_copy = scratch.0.join("tgt.sqlite");
    snapshot(&target, &tgt_copy)?;
    let s = Connection::open_with_flags(&snap, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let t = Connection::open_with_flags(&tgt_copy, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut checks: Vec<Check> = vec![Check { name: "checksums".into(), ok: true, detail: format!("{} files match manifest.json", archive.files.len() - 1) }];
    let mut add = |name: &str, ok: bool, detail: String| checks.push(Check { name: name.into(), ok, detail });

    // memories: matched by id when the content agrees, else by content
    let key_sql = "SELECT memory_id, text_hash || '|' || created_at || '|' || subject || '|' || kind, status,
                          superseded_by, version, embedding FROM memories";
    let load = |c: &Connection| -> Result<Vec<(String, String, String, Option<String>, i64, Option<Vec<u8>>)>> {
        let mut stmt = c.prepare(key_sql)?;
        let v = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(v)
    };
    let sm = load(&s)?;
    let tm = load(&t)?;
    let t_by_id: HashMap<&str, usize> = tm.iter().enumerate().map(|(i, r)| (r.0.as_str(), i)).collect();
    let mut t_by_key: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, r) in tm.iter().enumerate() {
        t_by_key.entry(r.1.as_str()).or_default().push(i);
    }
    let mut mem_map: HashMap<String, String> = HashMap::new();
    let (mut found, mut same_id, mut state_ok, mut vec_ok, mut vec_total) = (0, 0, 0, 0, 0);
    for r in &sm {
        let hit = t_by_id.get(r.0.as_str()).filter(|&&i| tm[i].1 == r.1).copied().or_else(|| {
            t_by_key.get(r.1.as_str()).and_then(|v| v.iter().copied().find(|&i| !mem_map.values().any(|m| m == &tm[i].0)))
        });
        if let Some(i) = hit {
            found += 1;
            if tm[i].0 == r.0 {
                same_id += 1;
            }
            mem_map.insert(r.0.clone(), tm[i].0.clone());
        }
    }
    for r in &sm {
        let Some(tid) = mem_map.get(&r.0) else { continue };
        let tr = &tm[t_by_id[tid.as_str()]];
        let sup_ok = r.3.as_ref().map(|x| mem_map.get(x).cloned().unwrap_or_else(|| x.clone())) == tr.3;
        if tr.2 == r.2 && tr.4 == r.4 && sup_ok {
            state_ok += 1;
        }
        if r.5.is_some() {
            vec_total += 1;
            if tr.5 == r.5 {
                vec_ok += 1;
            }
        }
    }
    add("memories_present", found == sm.len(), format!("{found}/{} archived memories found", sm.len()));
    add("memory_ids", !exact || same_id == sm.len(), format!("{same_id}/{} kept their original id", sm.len()));
    add("supersession_history", state_ok == found, format!("{state_ok}/{found} have the same status, version and superseded_by chain"));
    let dropped = vec_total > 0 && vec_ok == 0 && !exact;
    add(
        "embeddings",
        vec_ok == vec_total || dropped,
        if dropped {
            format!("{vec_total} vectors left out on import (different embedding dimension); the worker re-embeds them")
        } else {
            format!("{vec_ok}/{vec_total} embedding vectors byte-identical")
        },
    );

    // messages and provenance
    // messages are matched by id when the content agrees, else by (content, speaker, seq): a merge may rename them
    let load2 = |c: &Connection, sql: &str| -> Result<Vec<(String, String)>> {
        let mut stmt = c.prepare(sql)?;
        let v = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(v)
    };
    let msg_key_sql = "SELECT message_id, content_hash || '|' || speaker || '|' || seq FROM messages";
    let smsg = load2(&s, msg_key_sql)?;
    let tmsg = load2(&t, msg_key_sql)?;
    let tmsg_ids: HashMap<&str, &str> = tmsg.iter().map(|(a, b)| (a.as_str(), b.as_str())).collect();
    let mut tmsg_by_key: HashMap<&str, Vec<&str>> = HashMap::new();
    for (id, k) in &tmsg {
        tmsg_by_key.entry(k.as_str()).or_default().push(id.as_str());
    }
    let mut msg_map: HashMap<String, String> = HashMap::new();
    let mut used: HashSet<String> = HashSet::new();
    for (id, k) in &smsg {
        let hit = if tmsg_ids.get(id.as_str()) == Some(&k.as_str()) {
            Some(id.clone())
        } else {
            tmsg_by_key.get(k.as_str()).and_then(|v| v.iter().find(|x| !used.contains(**x))).map(|x| x.to_string())
        };
        if let Some(h) = hit {
            used.insert(h.clone());
            msg_map.insert(id.clone(), h);
        }
    }
    add("messages_present", msg_map.len() == smsg.len(), format!("{}/{} raw messages found", msg_map.len(), smsg.len()));
    let pairs = |c: &Connection, sql: &str| -> Result<HashSet<(String, String)>> {
        let mut stmt = c.prepare(sql)?;
        let v = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<HashSet<_>>>()?;
        Ok(v)
    };
    let mapm = |m: &str| mem_map.get(m).cloned().unwrap_or_else(|| m.to_string());
    let mapmsg = |m: &str| msg_map.get(m).cloned().unwrap_or_else(|| m.to_string());
    let tsrc = pairs(&t, "SELECT memory_id, message_id FROM memory_sources")?;
    let ssrc = pairs(&s, "SELECT memory_id, message_id FROM memory_sources")?;
    let ok_src = ssrc.iter().filter(|(m, g)| tsrc.contains(&(mapm(m), mapmsg(g)))).count();
    add("provenance_sources", ok_src == ssrc.len(), format!("{ok_src}/{} memory→message source links intact", ssrc.len()));
    let sme = pairs(&s, "SELECT memory_id, entity_id FROM memory_entities")?;
    let tme = pairs(&t, "SELECT memory_id, entity_id FROM memory_entities")?;
    let ok_me = sme.iter().filter(|(m, e)| tme.contains(&(mapm(m), e.clone()))).count();
    add("memory_entities", ok_me == sme.len(), format!("{ok_me}/{} memory→entity mentions intact", sme.len()));
    let links = |c: &Connection| -> Result<HashSet<(String, String, String)>> {
        let mut stmt = c.prepare("SELECT source_id, target_id, link_type FROM memory_links")?;
        let v = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<HashSet<_>>>()?;
        Ok(v)
    };
    let sl = links(&s)?;
    let tl = links(&t)?;
    let ok_l = sl.iter().filter(|(a, b, k)| tl.contains(&(mapm(a), mapm(b), k.clone()))).count();
    add("memory_links", ok_l == sl.len(), format!("{ok_l}/{} memory links intact", sl.len()));
    let ents = |c: &Connection| -> Result<HashSet<String>> {
        let mut stmt = c.prepare("SELECT entity_id FROM entities")?;
        let v = stmt.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<HashSet<_>>>()?;
        Ok(v)
    };
    let se = ents(&s)?;
    let te = ents(&t)?;
    add("entities", se.is_subset(&te), format!("{}/{} entities present", se.intersection(&te).count(), se.len()));
    let rel = |c: &Connection| -> Result<HashSet<(String, String, String, String)>> {
        let mut stmt = c.prepare("SELECT subject_id, predicate, object_id, status FROM relations")?;
        let v = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect::<rusqlite::Result<HashSet<_>>>()?;
        Ok(v)
    };
    let sr = rel(&s)?;
    let tr = rel(&t)?;
    add("relations", sr.is_subset(&tr), format!("{}/{} relations (subject, predicate, object, status) present", sr.intersection(&tr).count(), sr.len()));
    let rel_prov = |c: &Connection| -> Result<HashSet<(String, String, String)>> {
        let mut stmt = c.prepare("SELECT subject_id || '|' || predicate || '|' || object_id, COALESCE(memory_id, ''), COALESCE(message_id, '') FROM relations")?;
        let v = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<HashSet<_>>>()?;
        Ok(v)
    };
    let srp = rel_prov(&s)?;
    let trp: HashMap<String, (String, String)> = rel_prov(&t)?.into_iter().map(|(k, a, b)| (k, (a, b))).collect();
    let ok_rp = srp
        .iter()
        .filter(|(k, m, g)| {
            trp.get(k).is_some_and(|(tm_, tg)| (m.is_empty() || &mapm(m) == tm_ || !exact) && (g.is_empty() || &mapmsg(g) == tg || !exact))
        })
        .count();
    add("relation_provenance", ok_rp == srp.len(), format!("{ok_rp}/{} relations keep their source memory/message", srp.len()));
    let audit_s: i64 = count(&s, "audit_log")?;
    let audit_t: i64 = t.query_row("SELECT COUNT(*) FROM audit_log WHERE kind <> 'brain_import'", [], |r| r.get(0))?;
    add("audit_log", if exact { audit_t == audit_s } else { audit_t >= audit_s }, format!("{audit_t} audit entries (archive {audit_s}) plus the import record"));

    // table counts and graph shape
    let mut counts = IndexMap::new();
    let mut counts_equal = true;
    for tb in user_tables(&s)? {
        let a = count(&s, &tb)?;
        let b = if table_exists(&t, &tb)? { count(&t, &tb)? } else { 0 };
        let b_adj = if tb == "audit_log" { audit_t } else { b };
        if tb != "meta" && ((exact && a != b_adj) || (!exact && b_adj < a)) {
            counts_equal = false;
        }
        counts.insert(tb, json!({"archive": a, "imported": b}));
    }
    add("table_counts", counts_equal, if exact { "every table has the same row count".into() } else { "every table has at least the archived rows".into() });
    let (_, gs) = build_graph(&s)?;
    let (_, gt) = build_graph(&t)?;
    let gs_v = gs.to_value();
    let gt_v = gt.to_value();
    let graph_ok = if exact {
        gs == gt
    } else {
        gt_v["node_total"].as_i64() >= gs_v["node_total"].as_i64() && gt_v["edge_total"].as_i64() >= gs_v["edge_total"].as_i64()
    };
    add("graph_nodes_edges", graph_ok, format!("archive {} nodes / {} edges, imported {} / {}", gs_v["node_total"], gs_v["edge_total"], gt_v["node_total"], gt_v["edge_total"]));
    drop(s);
    drop(t);

    // retrieval: the same queries (memory texts with their stored vectors) must return the same memories
    let retrieval = compare_retrieval(&snap, &tgt_copy, &mem_map, retrieval_samples, exact).await?;
    add(
        "retrieval",
        retrieval["ok"].as_bool() == Some(true),
        format!(
            "{}/{} queries return identical top-{} ids; top-1 preserved {}/{}",
            retrieval["identical"], retrieval["queries"], retrieval["k"], retrieval["top1_same"], retrieval["queries"]
        ),
    );
    let ok = checks.iter().all(|c| c.ok);
    Ok(json!({
        "ok": ok,
        "mode": if exact { "restore (exact)" } else { "merge (contains)" },
        "archive_id": archive.manifest["archive_id"],
        "db": target.display().to_string(),
        "checks": checks.iter().map(|c| json!({"name": c.name, "ok": c.ok, "detail": c.detail})).collect::<Vec<_>>(),
        "counts": counts,
        "graph": {"archive": gs_v, "imported": gt_v},
        "retrieval": retrieval,
    }))
}

async fn compare_retrieval(src: &Path, tgt: &Path, mem_map: &HashMap<String, String>, samples: usize, exact: bool) -> Result<Value> {
    let k = 10;
    // (query text, stored vector, chat in the archive, chat in the target)
    let mut queries: Vec<(String, Option<Vec<f64>>, String, String)> = Vec::new();
    let mut vectors_dropped = false;
    {
        let c = Connection::open_with_flags(src, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let t = Connection::open_with_flags(tgt, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let n: i64 = count(&c, "memories")?;
        let step = ((n as usize) / samples.max(1)).max(1);
        let mut stmt = c.prepare("SELECT memory_id, text, embedding, chat_id FROM memories WHERE status='active' ORDER BY rid")?;
        let all = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, Option<Vec<u8>>>(2)?, r.get::<_, String>(3)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        for (id, text, e, chat) in all.into_iter().step_by(step).take(samples) {
            let tid = mem_map.get(&id).cloned().unwrap_or(id);
            let (tchat, temb): (String, Option<Vec<u8>>) = t
                .query_row("SELECT chat_id, embedding FROM memories WHERE memory_id=?", [&tid], |r| Ok((r.get(0)?, r.get(1)?)))
                .optional()?
                .unwrap_or((chat.clone(), None));
            if e.is_some() && temb.is_none() {
                vectors_dropped = true;
            }
            queries.push((text, e.map(|b| unpack(&b)), chat, tchat));
        }
    }
    // work on copies so opening (WAL, schema) never touches the originals
    let scratch = Scratch(scratch_dir("retrieval")?);
    let open = |p: &Path, name: &str, strip_vectors: bool| -> Result<Arc<ChatMemory>> {
        let dst = scratch.0.join(name);
        std::fs::copy(p, &dst)?;
        if strip_vectors {
            // the import dropped vectors for re-embedding: compare like with like
            Connection::open(&dst)?.execute("UPDATE memories SET embedding = NULL, embedding_dim = NULL", [])?;
        }
        let llm: Arc<dyn crate::llm::LlmClient> = Arc::new(crate::llm::FakeLlmClient::default());
        Ok(Arc::new(ChatMemory::open(&dst.to_string_lossy(), Some(llm), ChatMemoryConfig::default())?))
    };
    let a = open(src, "a.sqlite", vectors_dropped)?;
    let b = open(tgt, "b.sqlite", vectors_dropped)?;
    let (mut identical, mut top1, mut overlap_sum) = (0usize, 0usize, 0f64);
    let mut diffs = Vec::new();
    for (q, emb, chat, tchat) in &queries {
        // a merge target holds other knowledge too, so compare within the imported chat
        let scoped = |c: &String| if exact { None } else { Some(c.clone()) };
        let emb = if vectors_dropped { None } else { emb.clone() };
        let opts = SearchOptions { k, query_embedding: emb.clone(), touch: false, chat_id: scoped(chat), ..Default::default() };
        let topts = SearchOptions { chat_id: scoped(tchat), ..opts.clone() };
        let ra: Vec<String> = a.search(q, &opts).await?.into_iter().map(|h| mem_map.get(&h.memory.memory_id).cloned().unwrap_or(h.memory.memory_id)).collect();
        let rb: Vec<String> = b.search(q, &topts).await?.into_iter().map(|h| h.memory.memory_id).collect();
        if ra == rb {
            identical += 1;
        } else if diffs.len() < 5 {
            diffs.push(json!({"query": q.chars().take(80).collect::<String>(), "archive": ra, "imported": rb}));
        }
        if ra.first() == rb.first() {
            top1 += 1;
        }
        let sa: HashSet<&String> = ra.iter().collect();
        overlap_sum += if ra.is_empty() { 1.0 } else { rb.iter().filter(|x| sa.contains(x)).count() as f64 / ra.len() as f64 };
    }
    a.close().await;
    b.close().await;
    let nq = queries.len();
    let ok = if exact { identical == nq } else { top1 as f64 >= 0.9 * nq as f64 && overlap_sum >= 0.8 * nq as f64 };
    Ok(json!({
        "ok": ok, "queries": nq, "k": k, "identical": identical, "top1_same": top1, "vectors_dropped_for_reembedding": vectors_dropped,
        "scope": if exact { "whole store" } else { "per imported chat" },
        "mean_overlap": if nq == 0 { 1.0 } else { overlap_sum / nq as f64 }, "differences": diffs,
    }))
}
