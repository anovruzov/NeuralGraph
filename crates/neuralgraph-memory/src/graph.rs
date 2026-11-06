//! Entity-graph views shared by the dashboard, the `graph` CLI subcommand and the standalone HTML export.
//!
//! [`graph_value`] is the `{nodes, edges, chats}` JSON that `GET /api/graph` serves and the renderer in
//! `assets/graph.html` draws. [`export_html`] bakes that JSON into the page, so the file opens without a server.
//! [`ascii_tree`] prints an entity's neighbourhood for the terminal.

use crate::error::Result;
use crate::store::ChatMemoryStore;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;

/// The graph explorer page: force layout, local graph, filters, time-lapse (no external assets).
pub const GRAPH_HTML: &str = include_str!("../assets/graph.html");

/// Placeholder in [`GRAPH_HTML`] that [`export_html`] replaces with the graph JSON.
const DATA_SLOT: &str = "/*__GRAPH_DATA__*/null";

/// Options for [`graph_value`].
#[derive(Clone, Debug)]
pub struct GraphOptions {
    /// most-mentioned entities to include (clamped to 1..=500)
    pub limit: i64,
    /// only entities, relations and memories from this chat
    pub chat_id: Option<String>,
    /// memory texts per entity for the side panel
    pub snippets: usize,
}

impl Default for GraphOptions {
    fn default() -> Self {
        Self { limit: 150, chat_id: None, snippets: 5 }
    }
}

/// `{nodes, edges, chats, scope}` for the renderer. Every edge's ends are in `nodes`.
pub fn graph_value(store: &ChatMemoryStore, opts: &GraphOptions) -> Result<Value> {
    let limit = opts.limit.clamp(1, 500);
    let g = store.entity_graph(limit, limit * 4, opts.snippets, opts.chat_id.as_deref())?;
    let nodes: Vec<Value> = g
        .entities
        .iter()
        .map(|e| {
            let snippets: Vec<Value> = g
                .snippets
                .get(&e.entity_id)
                .into_iter()
                .flatten()
                .map(|s| json!({"memory_id": s.memory_id, "text": s.text, "kind": s.kind, "chat_id": s.chat_id, "when": s.when}))
                .collect();
            json!({
                "id": e.entity_id,
                "name": e.name,
                "type": e.etype,
                "mentions": e.mention_count,
                "memories": g.memory_counts.get(&e.entity_id).copied().unwrap_or(0),
                // ingestion clock throughout, so the time-lapse order is consistent
                "born_at": g.first_memory_at.get(&e.entity_id).cloned().or_else(|| e.first_seen_at.clone()),
                "snippets": snippets,
            })
        })
        .collect();
    let mut edges: Vec<Value> = g
        .relations
        .iter()
        .map(|r| {
            json!({
                "source": r.subject_id, "target": r.object_id, "kind": "relation", "label": r.predicate,
                "weight": r.observation_count, "confidence": r.confidence, "born_at": r.created_at,
            })
        })
        .collect();
    edges.extend(g.co_mentions.iter().map(|m| {
        json!({"source": m.a, "target": m.b, "kind": "co_mention", "label": "", "weight": m.memories, "born_at": m.first_at})
    }));
    let chats: Vec<Value> = store.list_chats(100, 0)?.into_iter().filter_map(|c| c.get("chat_id").cloned()).collect();
    Ok(json!({"nodes": nodes, "edges": edges, "chats": chats, "scope": {"chat_id": opts.chat_id, "limit": limit}}))
}

/// The explorer page with `graph` baked in: a single file that opens from disk, no server needed.
pub fn export_html(graph: &Value) -> String {
    let mut graph = graph.clone();
    if let Some(o) = graph.as_object_mut() {
        o.insert("exported_at".into(), Value::String(crate::models::now_iso()));
    }
    // `</` inside a <script> would end it early; `<\/` is the same string to JSON
    let data = graph.to_string().replace("</", "<\\/");
    GRAPH_HTML.replacen(DATA_SLOT, &data, 1)
}

/// Terminal view: with `root`, that entity's neighbourhood to `depth` hops; without, the top hubs one hop out.
/// Returns `None` when `root` matches no entity (by id or case-insensitive name).
pub fn ascii_tree(graph: &Value, root: Option<&str>, depth: usize, hubs: usize) -> Option<String> {
    let empty = Vec::new();
    let nodes = graph["nodes"].as_array().unwrap_or(&empty);
    let edges = graph["edges"].as_array().unwrap_or(&empty);
    let name: HashMap<&str, &str> =
        nodes.iter().filter_map(|n| Some((n["id"].as_str()?, n["name"].as_str().unwrap_or("?")))).collect();
    let etype: HashMap<&str, &str> =
        nodes.iter().filter_map(|n| Some((n["id"].as_str()?, n["type"].as_str().unwrap_or("")))).collect();
    // id -> edges out of it; typed relations first, then co-mentions, each by weight
    let mut adj: HashMap<&str, Vec<Step>> = HashMap::new();
    for e in edges {
        let (Some(s), Some(t)) = (e["source"].as_str(), e["target"].as_str()) else { continue };
        let w = e["weight"].as_i64().unwrap_or(1);
        let step = |to, how: String, after: String, rank| Step { to, how, after, rank };
        if e["kind"] == "relation" {
            let l = e["label"].as_str().unwrap_or("");
            adj.entry(s).or_default().push(step(t, format!("{l} →"), String::new(), 1_000_000 + w));
            adj.entry(t).or_default().push(step(s, format!("← {l} ·"), String::new(), 1_000_000 + w));
        } else {
            let after = format!("  ({w} memor{})", if w == 1 { "y" } else { "ies" });
            adj.entry(s).or_default().push(step(t, "~".into(), after.clone(), w));
            adj.entry(t).or_default().push(step(s, "~".into(), after, w));
        }
    }
    for v in adj.values_mut() {
        v.sort_by(|a, b| b.rank.cmp(&a.rank).then(a.to.cmp(b.to)));
    }
    let label = |id: &str| {
        let t = etype.get(id).copied().unwrap_or("");
        let n = name.get(id).copied().unwrap_or(id);
        if t.is_empty() {
            n.to_string()
        } else {
            format!("{n} [{t}]")
        }
    };
    let mut out = String::new();
    let roots: Vec<&str> = match root {
        Some(r) => {
            let r_low = r.to_lowercase();
            let hit = nodes
                .iter()
                .filter_map(|n| n["id"].as_str())
                .find(|id| *id == r || name.get(id).is_some_and(|n| n.to_lowercase() == r_low))?;
            vec![hit]
        }
        None => {
            let mut ids: Vec<&str> = nodes.iter().filter_map(|n| n["id"].as_str()).collect();
            ids.sort_by_key(|id| std::cmp::Reverse(adj.get(id).map_or(0, Vec::len)));
            ids.truncate(hubs);
            ids
        }
    };
    if roots.is_empty() {
        out.push_str("(no entities yet)\n");
        return Some(out);
    }
    let depth = if root.is_some() { depth.max(1) } else { 1 };
    for (i, r) in roots.iter().enumerate() {
        if i > 0 {
            out.push('\n');
        }
        let degree = adj.get(r).map_or(0, Vec::len);
        let _ = writeln!(out, "{} · {degree} connections", label(r));
        let mut seen: HashSet<&str> = HashSet::from([*r]);
        walk(&adj, &label, r, depth, "", &mut seen, &mut out);
    }
    out.push_str("\n→ relation from it · ← relation to it · ~ named in the same memories\n");
    Some(out)
}

/// One edge out of a node in the terminal tree: `{how} {neighbour}{after}`.
struct Step<'a> {
    to: &'a str,
    how: String,
    after: String,
    rank: i64,
}

/// Max children printed under one node before "… N more".
const FANOUT: usize = 12;

fn walk<'a>(
    adj: &HashMap<&'a str, Vec<Step<'a>>>,
    label: &dyn Fn(&str) -> String,
    id: &'a str,
    depth: usize,
    prefix: &str,
    seen: &mut HashSet<&'a str>,
    out: &mut String,
) {
    if depth == 0 {
        return;
    }
    // a neighbour joined by several relations is listed once per relation but expanded once
    let kids: Vec<&Step> = adj.get(id).map(|v| v.iter().filter(|s| !seen.contains(s.to)).collect()).unwrap_or_default();
    kids.iter().for_each(|s| {
        seen.insert(s.to);
    });
    let mut expanded: HashSet<&str> = HashSet::new();
    let shown = kids.len().min(FANOUT);
    let more = kids.len() - shown;
    for (i, s) in kids.iter().take(shown).enumerate() {
        let last = i + 1 == shown && more == 0;
        let _ = writeln!(out, "{prefix}{} {} {}{}", if last { "└─" } else { "├─" }, s.how, label(s.to), s.after);
        if expanded.insert(s.to) {
            let child_prefix = format!("{prefix}{}", if last { "   " } else { "│  " });
            walk(adj, label, s.to, depth - 1, &child_prefix, seen, out);
        }
    }
    if more > 0 {
        let _ = writeln!(out, "{prefix}└─ … {more} more");
    }
}
