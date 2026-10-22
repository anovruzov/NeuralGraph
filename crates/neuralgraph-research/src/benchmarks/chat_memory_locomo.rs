//! Port of `research/benchmarks/chat_memory_locomo.py`: the retrieval-only LoCoMo harness for the SHIPPED
//! chat_memory pipeline (binary `chat-memory-locomo`).
//!
//! Unlike the research runner (which drives the Tesseract engine over raw messages), this ingests a LoCoMo
//! conversation through the real extractor/worker of the product crate (`neuralgraph_memory`), then asks the
//! questions through `MemoryRetriever` and measures whether the evidence reached the retrieved set. No answer model,
//! no judge.
//!
//! ```text
//! ingest:  chat-memory-locomo ingest --db /tmp/a.db --sessions 6 --batch-size 6
//!          chat-memory-locomo ingest --db /tmp/b.db --sessions 6 --batch-words 300
//! eval:    chat-memory-locomo eval --db /tmp/a.db --sessions 6
//! ```
//!
//! Metrics per retrieval variant (over eligible questions = non-adversarial, all evidence inside the ingested
//! sessions):
//! * `evidence@k`   a retrieved memory cites one of the gold evidence messages (provenance, exact)
//! * `gold@k`       a gold-answer part is a substring of a retrieved memory's text (loose, like the research recall@k)
//! * `ctx_evidence` same as evidence@k but through `context_with_results`, i.e. what Claude would actually see
//!
//! The Python drove the frozen copy `NeuralGraph.chat_memory` in-process; the port drives the product crate as a
//! library through the same calls (`ChatMemory::open`, `add_messages`, `process_pending`, `worker.metrics()`,
//! `retriever.search` / `context_with_results`, `llm().embed`). `do_ingest` / `do_eval` take an optional model
//! client so tests can run the whole path on the product's scripted fake model; the binary passes `None` (the
//! configured LM Studio/Ollama server, like the Python).

use super::pyharness::{
    expanduser, json_indent, parse_py_float, parse_py_int, perf_counter, py_getitem, py_getstr, repo_root, HResult,
    HarnessError,
};
use clap::{Args, Parser, Subcommand};
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{
    dumps, fnum, obj, py_int_parse, py_repr_str, py_round, py_split_whitespace, py_str, py_strip, py_strip_chars,
    py_truthy,
};
use neuralgraph_memory::retrieval::DEFAULT_HEADER;
use neuralgraph_memory::temporal::parse_datetime_flexible;
use neuralgraph_memory::{ChatMemory, ChatMemoryConfig, LlmClient, RetrievedMemory, SearchOptions};
use regex::Regex;
use rusqlite::types::Value as SqlValue;
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock};

/// `research/datasets/locomo10.json` under the repository root.
pub fn dataset() -> PathBuf {
    repo_root().join("research/datasets/locomo10.json")
}

/// `data[conv_idx]` (negative indices count from the end, like Python).
pub fn load_conv_from(path: &Path, conv_idx: i64) -> HResult<(Value, Value)> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    let items = data.as_array().ok_or_else(|| HarnessError::new("TypeError", "dataset is not a list"))?;
    let idx = if conv_idx < 0 { conv_idx + items.len() as i64 } else { conv_idx };
    let c = usize::try_from(idx)
        .ok()
        .and_then(|i| items.get(i))
        .ok_or_else(|| HarnessError::new("IndexError", "list index out of range"))?;
    Ok((py_getitem(c, "conversation")?.clone(), py_getitem(c, "qa")?.clone()))
}

/// `load_conv(conv_idx)`: the conversation and its QA list.
pub fn load_conv(conv_idx: i64) -> HResult<(Value, Value)> {
    load_conv_from(&dataset(), conv_idx)
}

/// The messages of sessions `1..=max_session` as `ChatMemory::add_messages` items (speaker, text, sent_at,
/// message_id), with the session date parsed by `parse_datetime_flexible` and image captions appended unless
/// `captions` is false.
pub fn messages_for(conv: &Value, conv_idx: i64, max_session: i64, captions: bool) -> HResult<Vec<Value>> {
    let mut out = Vec::new();
    let mut i: i64 = 1;
    while conv.get(format!("session_{i}")).is_some() && i <= max_session {
        let date = conv.get(format!("session_{i}_date_time"));
        let date_str = match date {
            None => Some(String::new()),
            Some(Value::Null) => None,
            Some(v) => Some(py_str(v)),
        };
        let dt = parse_datetime_flexible(date_str.as_deref()).map_err(|e| HarnessError::new("ValueError", e.0))?;
        let session = py_getitem(conv, &format!("session_{i}"))?;
        for m in session.as_array().map(Vec::as_slice).unwrap_or_default() {
            let raw_text = py_getitem(m, "text")?;
            let mut text = if py_truthy(raw_text) { py_str(raw_text) } else { String::new() };
            if captions {
                if let Some(cap) = m.get("blip_caption").filter(|c| py_truthy(c)) {
                    text = py_strip(&format!("{text} [shared image: {}]", py_str(cap))).to_string();
                }
            }
            let dia_id = py_str(py_getitem(m, "dia_id")?);
            out.push(obj([
                ("speaker", py_getitem(m, "speaker")?.clone()),
                ("text", Value::String(text)),
                ("sent_at", dt.as_ref().map(|d| Value::String(d.isoformat())).unwrap_or(Value::Null)),
                ("message_id", Value::String(format!("locomo-{conv_idx}-{dia_id}"))),
            ]));
        }
        i += 1;
    }
    Ok(out)
}

/// `int(ev.split(":")[0][1:])`: "D3:12" -> 3.
pub fn sess_of(ev: &str) -> HResult<i64> {
    let head = ev.split(':').next().unwrap_or("");
    let digits: String = head.chars().skip(1).collect();
    py_int_parse(&digits).ok_or_else(|| {
        HarnessError::new("ValueError", format!("invalid literal for int() with base 10: {}", py_repr_str(&digits)))
    })
}

static EVIDENCE_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[;,\s\x1c-\x1f]+").unwrap());
static GOLD_SPLIT_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r",| and |;").unwrap());

/// LoCoMo evidence lists occasionally join ids with ';' or ',' inside one string.
pub fn evidence_ids(q: &Value) -> HResult<Vec<String>> {
    let items: Vec<String> = match q.get("evidence") {
        Some(v) if py_truthy(v) => match v {
            Value::Array(a) => a
                .iter()
                .map(|e| {
                    e.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| HarnessError::new("TypeError", "sequence item: expected str instance"))
                })
                .collect::<HResult<_>>()?,
            Value::String(s) => s.chars().map(String::from).collect(),
            _ => return Err(HarnessError::new("TypeError", "can only join an iterable")),
        },
        _ => Vec::new(),
    };
    let joined = items.join(" ");
    Ok(EVIDENCE_SPLIT_RE.split(&joined).map(|e| py_strip(e).to_string()).filter(|e| !e.is_empty()).collect())
}

/// Non-adversarial questions (category != 5) with evidence, all of it inside sessions `1..=max_session`.
pub fn eligible_qa(qa: &[Value], max_session: i64) -> HResult<Vec<Value>> {
    let mut out = Vec::new();
    for q in qa {
        let adversarial = q.get("category").and_then(Value::as_f64) == Some(5.0);
        if adversarial || !q.get("evidence").is_some_and(py_truthy) {
            continue;
        }
        let mut inside = true;
        for e in evidence_ids(q)? {
            if sess_of(&e)? > max_session {
                inside = false;
                break;
            }
        }
        if inside {
            out.push(q.clone());
        }
    }
    Ok(out)
}

/// Lower-cased gold-answer parts split on ",", " and ", ";" (trimmed of spaces and dots); the whole answer when
/// nothing is left.
pub fn gold_parts(answer: &Value) -> Vec<String> {
    let s = py_strip(&py_str(answer).to_lowercase()).to_string();
    let parts: Vec<String> =
        GOLD_SPLIT_RE.split(&s).map(|p| py_strip_chars(p, " .").to_string()).filter(|p| !p.is_empty()).collect();
    if parts.is_empty() {
        vec![s]
    } else {
        parts
    }
}

// ----------------------------------------------------------------------------- CLI

#[derive(Parser, Debug, Clone)]
#[command(name = "chat-memory-locomo", infer_long_args = true)]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Subcommand, Debug, Clone)]
pub enum Command {
    Ingest(IngestArgs),
    Eval(EvalArgs),
}

// Options both subcommands take (a plain comment: a doc comment would become the subcommands' help text).
#[derive(Args, Debug, Clone)]
pub struct CommonArgs {
    #[arg(long, required = true)]
    pub db: String,
    #[arg(long, default_value = "0", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub conv: i64,
    #[arg(long, default_value = "6", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub sessions: i64,
    #[arg(long, default_value = "qwen3.8-9b-mlx")]
    pub model: String,
    #[arg(long, default_value = "http://127.0.0.1:1234")]
    pub base_url: String,
    #[arg(long)]
    pub out: Option<String>,
}

#[derive(Args, Debug, Clone)]
pub struct IngestArgs {
    #[command(flatten)]
    pub common: CommonArgs,
    #[arg(long, default_value = "6", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub batch_size: i64,
    #[arg(long, default_value = "0", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub batch_words: i64,
    #[arg(long, default_value = "8", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub max_memories: i64,
    #[arg(long, default_value = "6", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub context_messages: i64,
    #[arg(long, default_value = "3500", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub gen_tokens: i64,
    #[arg(long, default_value = "1200", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub reconcile_tokens: i64,
    #[arg(long, value_parser = parse_py_float, allow_hyphen_values = true)]
    pub min_importance: Option<f64>,
    #[arg(long)]
    pub no_gate: bool,
    /// drop LoCoMo image captions (the pre-fix behaviour)
    #[arg(long)]
    pub no_captions: bool,
    /// skip the entity/relation extraction call
    #[arg(long)]
    pub no_relations: bool,
    #[arg(long, value_parser = parse_py_float, allow_hyphen_values = true)]
    pub reconcile_cosine: Option<f64>,
    #[arg(long, default_value = "1", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub concurrency: i64,
    #[arg(long, default_value = "4", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub parallel: i64,
}

#[derive(Args, Debug, Clone)]
pub struct EvalArgs {
    #[command(flatten)]
    pub common: CommonArgs,
}

/// A count the Python config accepted as any `int` but the product crate stores unsigned.
fn unsigned(name: &str, v: i64) -> HResult<usize> {
    usize::try_from(v).map_err(|_| {
        HarnessError::new("ValueError", format!("{name}={v}: the Rust product config needs a non-negative value"))
    })
}

fn write_out(path: &Option<String>, value: &Value) -> HResult<()> {
    if let Some(p) = path.as_deref().filter(|p| !p.is_empty()) {
        std::fs::write(p, dumps(value, &json_indent(2)))?;
    }
    Ok(())
}

// ----------------------------------------------------------------------------- ingest

/// `ingest`: build a fresh database from sessions `1..=sessions` of conversation `conv`, drain the worker, print and
/// optionally save the summary. Returns the summary.
// The config is edited field by field in the Python's order, so the two read side by side.
#[allow(clippy::field_reassign_with_default)]
pub async fn do_ingest(args: &IngestArgs, llm: Option<Arc<dyn LlmClient>>, stdout: &mut dyn Write) -> HResult<Value> {
    let c = &args.common;
    let (conv, _qa) = load_conv(c.conv)?;
    let msgs = messages_for(&conv, c.conv, c.sessions, !args.no_captions)?;
    let mut cfg = ChatMemoryConfig::default();
    cfg.debounce_seconds = 0.0;
    cfg.llm_model = Some(c.model.clone());
    cfg.llm_base_url = Some(c.base_url.clone());
    cfg.llm_max_parallel = unsigned("parallel", args.parallel)?;
    cfg.worker.concurrency = unsigned("concurrency", args.concurrency)?;
    cfg.worker.batch_size = args.batch_size;
    cfg.worker.batch_words = args.batch_words;
    cfg.worker.maintenance_interval = 0.0;
    cfg.extraction.max_memories_per_batch = unsigned("max_memories", args.max_memories)?;
    cfg.extraction.max_triples_per_batch = unsigned("max_memories", 12.max(args.max_memories + 4))?;
    cfg.extraction.generate_max_tokens = args.gen_tokens;
    cfg.extraction.reconcile_max_tokens = args.reconcile_tokens;
    cfg.extraction.llm_timeout = 600.0;
    cfg.extraction.context_messages = args.context_messages;
    cfg.extraction.gate_chitchat = !args.no_gate;
    cfg.extraction.extract_relations = !args.no_relations;
    if let Some(v) = args.reconcile_cosine {
        cfg.extraction.reconcile_cosine = v;
    }
    if let Some(v) = args.min_importance {
        cfg.extraction.min_importance = v;
    }
    let reconcile_cosine = cfg.extraction.reconcile_cosine;
    let db = expanduser(&c.db);
    if Path::new(&db).exists() {
        std::fs::remove_file(&db)?;
    }
    let cm = ChatMemory::open(&db, llm, cfg)?;
    let t0 = perf_counter();
    let chat_id = format!("locomo-{}", c.conv);
    cm.add_messages(&chat_id, &msgs)?;
    let batches = cm.process_pending(None).await?;
    let dt = perf_counter() - t0;
    let m = cm.worker.metrics();
    let words: usize = msgs.iter().map(|x| py_split_whitespace(x["text"].as_str().unwrap_or_default()).len()).sum();
    // The Python dict literal names "relations" twice: the key keeps its first position (after "captions") and
    // takes the last value (the worker's relation count). serde_json's ordered map behaves the same way.
    let mut summary = Map::new();
    let pairs: Vec<(&str, Value)> = vec![
        ("db", Value::String(db.clone())),
        ("sessions", Value::from(c.sessions)),
        ("messages", Value::from(msgs.len())),
        ("words", Value::from(words)),
        ("batch_size", Value::from(args.batch_size)),
        ("batch_words", Value::from(args.batch_words)),
        ("max_memories", Value::from(args.max_memories)),
        ("context_messages", Value::from(args.context_messages)),
        ("gate", Value::Bool(!args.no_gate)),
        ("captions", Value::Bool(!args.no_captions)),
        ("relations", Value::Bool(!args.no_relations)),
        ("reconcile_cosine", fnum(reconcile_cosine)),
        ("batches", Value::from(batches)),
        ("seconds", fnum(py_round(dt, 1))),
        ("sec_per_message", fnum(py_round(dt / msgs.len().max(1) as f64, 2))),
        ("memories_added", Value::from(m.memories_added)),
        ("memories_superseded", Value::from(m.memories_superseded)),
        ("duplicates", Value::from(m.duplicates)),
        ("relations", Value::from(m.relations)),
        ("links", Value::from(m.links)),
        ("messages_processed", Value::from(m.messages_processed)),
        ("messages_skipped", Value::from(m.messages_skipped)),
        ("failures", Value::from(m.failures)),
        ("dead_jobs", Value::from(m.dead_jobs)),
        ("last_error", m.last_error.clone().map(Value::String).unwrap_or(Value::Null)),
    ];
    for (k, v) in pairs {
        summary.insert(k.to_string(), v);
    }
    cm.close().await;
    for (k, v) in db_stats(Path::new(&db))? {
        summary.insert(k, v);
    }
    let summary = Value::Object(summary);
    writeln!(stdout, "{}", dumps(&summary, &json_indent(2)))?;
    write_out(&c.out, &summary)?;
    Ok(summary)
}

/// A SQLite value used as a dict key, as `json.dumps` writes it (`None` -> "null", numbers via `str`).
fn sql_key(v: SqlValue) -> String {
    match v {
        SqlValue::Null => "null".into(),
        SqlValue::Integer(i) => i.to_string(),
        SqlValue::Real(f) => neuralgraph_memory::pycompat::py_float_repr(f),
        SqlValue::Text(s) => s,
        SqlValue::Blob(b) => String::from_utf8_lossy(&b).into_owned(),
    }
}

fn grouped_counts(c: &rusqlite::Connection, sql: &str) -> HResult<Value> {
    let mut stmt = c.prepare(sql)?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, SqlValue>(0)?, r.get::<_, i64>(1)?)))?;
    let mut m = Map::new();
    for row in rows {
        let (k, n) = row?;
        m.insert(sql_key(k), Value::from(n));
    }
    Ok(Value::Object(m))
}

/// Counts straight from the database file (memories, entities, relations, message status, audit kinds, memory
/// kinds, mean words per active memory).
pub fn db_stats(db: &Path) -> HResult<Map<String, Value>> {
    let c = rusqlite::Connection::open(db)?;
    let count = |sql: &str| -> HResult<i64> { Ok(c.query_row(sql, [], |r| r.get(0))?) };
    let mut out = Map::new();
    out.insert("memories_active".into(), count("SELECT COUNT(*) FROM memories WHERE status='active'")?.into());
    out.insert("memories_total".into(), count("SELECT COUNT(*) FROM memories")?.into());
    out.insert("entities".into(), count("SELECT COUNT(*) FROM entities")?.into());
    out.insert("relations_active".into(), count("SELECT COUNT(*) FROM relations WHERE status='active'")?.into());
    out.insert("msg_status".into(), grouped_counts(&c, "SELECT status, COUNT(*) FROM messages GROUP BY status")?);
    out.insert("audit".into(), grouped_counts(&c, "SELECT kind, COUNT(*) FROM audit_log GROUP BY kind")?);
    out.insert(
        "memory_kinds".into(),
        grouped_counts(&c, "SELECT kind, COUNT(*) FROM memories WHERE status='active' GROUP BY kind")?,
    );
    let avg: Option<f64> = c.query_row(
        "SELECT AVG(LENGTH(text)-LENGTH(REPLACE(text,' ',''))+1) FROM memories WHERE status='active'",
        [],
        |r| r.get(0),
    )?;
    // `round(avg or 0, 1)`: no active memories gives the integer 0, not 0.0
    out.insert(
        "avg_memory_words".into(),
        match avg {
            Some(a) if a != 0.0 => fnum(py_round(a, 1)),
            _ => Value::from(0),
        },
    );
    Ok(out)
}

// ----------------------------------------------------------------------------- eval

/// Running totals of one retrieval variant.
#[derive(Clone, Debug, Default)]
pub struct VariantStats {
    pub evidence: i64,
    pub gold: i64,
    pub first_rank: Vec<i64>,
    pub n_ret: Vec<i64>,
    pub by_cat: IndexMap<i64, i64>,
    pub n_by_cat: IndexMap<i64, i64>,
}

/// One question scored against one variant's hits: `(rank of the first hit citing evidence, a gold part occurs in
/// some hit's text)`.
pub fn score_hits(
    hits: &[RetrievedMemory],
    src_map: &HashMap<String, HashSet<String>>,
    ev_ids: &HashSet<String>,
    parts: &[String],
) -> (Option<usize>, bool) {
    let ev_hit = hits
        .iter()
        .position(|h| src_map.get(&h.memory.memory_id).is_some_and(|s| s.iter().any(|m| ev_ids.contains(m))));
    let texts: Vec<String> = hits.iter().map(|h| h.memory.text.to_lowercase()).collect();
    let gold_hit = parts.iter().any(|p| texts.iter().any(|t| t.contains(p.as_str())));
    (ev_hit, gold_hit)
}

impl VariantStats {
    /// Record one question (`score()` in the Python) and return its row entry `{"ev", "gold", "rank"}`.
    pub fn record(&mut self, cat: i64, n_hits: usize, ev_hit: Option<usize>, gold_hit: bool) -> Value {
        *self.n_by_cat.entry(cat).or_insert(0) += 1;
        self.n_ret.push(n_hits as i64);
        if let Some(i) = ev_hit {
            self.evidence += 1;
            *self.by_cat.entry(cat).or_insert(0) += 1;
            self.first_rank.push(i as i64 + 1);
        }
        if gold_hit {
            self.gold += 1;
        }
        obj([
            ("ev", Value::Bool(ev_hit.is_some())),
            ("gold", Value::Bool(gold_hit)),
            ("rank", ev_hit.map(Value::from).unwrap_or(Value::Null)),
        ])
    }

    /// The variant's line of the report over `n` questions (`ZeroDivisionError` when there were none, like Python).
    pub fn table_row(&self, name: &str, n: usize) -> HResult<Value> {
        if n == 0 {
            return Err(HarnessError::new("ZeroDivisionError", "division by zero"));
        }
        let nf = n as f64;
        let mean_first_rank = if self.first_rank.is_empty() {
            Value::Null
        } else {
            fnum(py_round(self.first_rank.iter().sum::<i64>() as f64 / self.first_rank.len() as f64, 2))
        };
        let mut cats: Vec<i64> = self.n_by_cat.keys().copied().collect();
        cats.sort();
        let by_cat = obj(cats.iter().map(|c| {
            let hit = self.by_cat.get(c).copied().unwrap_or(0);
            (c.to_string(), Value::String(format!("{hit}/{}", self.n_by_cat[c])))
        }));
        Ok(obj([
            ("variant", Value::String(name.to_string())),
            ("n", Value::from(n)),
            ("evidence@k", fnum(py_round((100 * self.evidence) as f64 / nf, 1))),
            ("gold@k", fnum(py_round((100 * self.gold) as f64 / nf, 1))),
            ("mean_first_rank", mean_first_rank),
            ("mean_retrieved", fnum(py_round(self.n_ret.iter().sum::<i64>() as f64 / nf, 1))),
            ("evidence_by_cat", by_cat),
        ]))
    }
}

/// The speakers named (case-insensitively) in a question.
pub fn named(speakers: &[String], question: &str) -> Vec<String> {
    let ql = question.to_lowercase();
    speakers.iter().filter(|s| ql.contains(&s.to_lowercase())).cloned().collect()
}

/// The `search` variants of the evaluation, in report order: (name, k, channels, route by the one named speaker).
pub const SEARCH_VARIANTS: [(&str, usize, &str, bool); 7] = [
    ("base_VKG_k10", 10, "VKG", false),
    ("nograph_VK_k10", 10, "VK", false),
    ("vector_only_k10", 10, "V", false),
    ("route_hard_k10", 10, "VKG", true),
    ("route_hard_nograph_k10", 10, "VK", true),
    ("base_k15", 15, "VKG", false),
    ("base_k5", 5, "VKG", false),
];

/// The `context_with_results` variants, after the search ones.
pub const CONTEXT_VARIANTS: [&str; 3] = ["ctx_k10", "ctx_route_k10", "SHIPPED_DEFAULTS"];

/// A JSON number that must be an integer (LoCoMo categories).
fn category(q: &Value) -> HResult<i64> {
    let c = py_getitem(q, "category")?;
    c.as_i64()
        .or_else(|| c.as_f64().filter(|f| f.fract() == 0.0).map(|f| f as i64))
        .ok_or_else(|| HarnessError::new("TypeError", format!("category {c} is not an integer")))
}

/// `eval`: ask every eligible question through each retrieval variant of an ingested database and print the report;
/// with `--out`, also save it with the per-question rows. Returns `(report, rows)`.
// The config is edited field by field in the Python's order, so the two read side by side.
#[allow(clippy::field_reassign_with_default)]
pub async fn do_eval(
    args: &EvalArgs,
    llm: Option<Arc<dyn LlmClient>>,
    stdout: &mut dyn Write,
) -> HResult<(Value, Vec<Value>)> {
    let c = &args.common;
    let (conv, qa) = load_conv(c.conv)?;
    let qs = eligible_qa(qa.as_array().map(Vec::as_slice).unwrap_or_default(), c.sessions)?;
    let speakers = vec![py_getstr(&conv, "speaker_a")?.to_string(), py_getstr(&conv, "speaker_b")?.to_string()];
    let mut cfg = ChatMemoryConfig::default();
    cfg.llm_model = Some(c.model.clone());
    cfg.llm_base_url = Some(c.base_url.clone());
    cfg.worker.maintenance_interval = 0.0;
    let db = expanduser(&c.db);
    let cm = ChatMemory::open(&db, llm, cfg)?; // no start(): read-only, worker idle
    let r = cm.retriever.clone();
    // provenance map straight from the DB (the retriever only loads source ids when with_sources=True, and
    // context_with_results hardcodes it off)
    let mut src_map: HashMap<String, HashSet<String>> = HashMap::new();
    {
        let conn = rusqlite::Connection::open(&db)?;
        let mut stmt = conn.prepare("SELECT memory_id, message_id FROM memory_sources")?;
        let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
        for row in rows {
            let (mid, msg) = row?;
            src_map.entry(mid).or_default().insert(msg);
        }
    }

    let mut res: IndexMap<&str, VariantStats> = IndexMap::new();
    for (name, ..) in SEARCH_VARIANTS {
        res.insert(name, VariantStats::default());
    }
    for name in CONTEXT_VARIANTS {
        res.insert(name, VariantStats::default());
    }
    let mut rows = Vec::new();
    let t0 = perf_counter();
    for q in &qs {
        let question = py_getstr(q, "question")?;
        let ev_ids: HashSet<String> = evidence_ids(q)?.into_iter().map(|e| format!("locomo-{}-{e}", c.conv)).collect();
        let answer = py_getitem(q, "answer")?;
        let parts = gold_parts(answer);
        let cat = category(q)?;
        let qemb = cm.llm().embed(question).await?;
        let names = named(&speakers, question);
        let subject = if names.len() == 1 { Some(names[0].clone()) } else { None };
        let mut row = Map::new();
        row.insert("q".into(), Value::String(question.to_string()));
        row.insert("cat".into(), q["category"].clone());
        row.insert("gold".into(), answer.clone());
        row.insert("named".into(), Value::from(names.clone()));
        let base = SearchOptions {
            query_embedding: Some(qemb.clone()),
            with_sources: false,
            touch: false,
            ..Default::default()
        };
        let mut score = |name: &str, hits: &[RetrievedMemory], row: &mut Map<String, Value>| {
            let (ev_hit, gold_hit) = score_hits(hits, &src_map, &ev_ids, &parts);
            let entry = res.get_mut(name).expect("variant").record(cat, hits.len(), ev_hit, gold_hit);
            row.insert(name.to_string(), entry);
        };
        for (name, k, channels, route) in SEARCH_VARIANTS {
            let opts = SearchOptions {
                k,
                channels: Some(channels.to_string()),
                subject: if route { subject.clone() } else { None },
                ..base.clone()
            };
            let hits = r.search(question, &opts).await?;
            score(name, &hits, &mut row);
        }
        let (_, hits) = r.context_with_results(question, Some(10), None, DEFAULT_HEADER, &base).await?;
        score("ctx_k10", &hits, &mut row);
        let routed = SearchOptions { subject: subject.clone(), ..base.clone() };
        let (_, hits) = r.context_with_results(question, Some(10), None, DEFAULT_HEADER, &routed).await?;
        score("ctx_route_k10", &hits, &mut row);
        let (_, hits) = r.context_with_results(question, None, None, DEFAULT_HEADER, &base).await?; // no overrides
        score("SHIPPED_DEFAULTS", &hits, &mut row);
        rows.push(Value::Object(row));
    }
    cm.close().await;

    let n = qs.len();
    let mut table = Vec::new();
    for (name, st) in &res {
        table.push(st.table_row(name, n)?);
    }
    let mut out = Map::new();
    out.insert("db".into(), Value::String(c.db.clone()));
    out.insert("sessions".into(), Value::from(c.sessions));
    out.insert("questions".into(), Value::from(n));
    out.insert("eval_seconds".into(), fnum(py_round(perf_counter() - t0, 1)));
    out.insert("stats".into(), Value::Object(db_stats(Path::new(&db))?));
    out.insert("variants".into(), Value::Array(table));
    let report = Value::Object(out.clone());
    writeln!(stdout, "{}", dumps(&report, &json_indent(2)))?;
    if c.out.as_deref().is_some_and(|p| !p.is_empty()) {
        let mut full = out;
        full.insert("rows".into(), Value::Array(rows.clone()));
        write_out(&c.out, &Value::Object(full))?;
    }
    Ok((report, rows))
}

/// Entry point of the binary: parse the arguments and run the subcommand (on the configured model server).
pub async fn main_with(cli: Cli, stdout: &mut dyn Write) -> HResult<()> {
    match cli.cmd {
        Command::Ingest(a) => do_ingest(&a, None, stdout).await.map(|_| ()),
        Command::Eval(a) => do_eval(&a, None, stdout).await.map(|_| ()),
    }
}
