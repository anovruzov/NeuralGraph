//! `chat-memory-context`: build the context block the shipped pipeline would hand the model, for questions whose
//! databases already exist, and write it as JSON rows for a downstream answer/judge script. No ingestion, no answer
//! model.
//!
//! ```text
//! chat-memory-context lme --db-dir .context/overnight/db/lme_base --n 50 --seed 1 --out ctx.json
//! chat-memory-context locomo --db /tmp/a.db --conv 0 --sessions 8 --with-sources --out ctx.json
//! ```
//!
//! The retrieval knobs (`--k`, `--max-chars`, `--channels`, `--retrieval key=val,...`) override the product defaults
//! so a reader experiment can vary them against a fixed database.

use super::chat_memory_locomo::{self as locomo, evidence_ids, sess_of};
use super::chat_memory_longmemeval::{self as lme, stratified_sample};
use super::pyharness::{expanduser, py_getitem, py_getstr, HResult, HarnessError};
use clap::{Args, Parser, Subcommand};
use neuralgraph_memory::pycompat::{py_prefix, py_str};
use neuralgraph_memory::retrieval::DEFAULT_HEADER;
use neuralgraph_memory::{ChatMemory, ChatMemoryConfig, RetrievalConfig, RetrievedMemory, SearchOptions};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::Path;

#[derive(Parser, Debug, Clone)]
#[command(name = "chat-memory-context", infer_long_args = true)]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

#[derive(Subcommand, Debug, Clone)]
pub enum Command {
    /// one database per question at `<db-dir>/<question_id>.db`
    Lme(LmeArgs),
    /// one database for a conversation
    Locomo(LocomoArgs),
}

// Options both subcommands take (a plain comment: a doc comment would become the subcommands' help text).
#[derive(Args, Debug, Clone)]
pub struct Common {
    #[arg(long)]
    pub out: String,
    #[arg(long, default_value = "qwen3.8-9b-mlx")]
    pub model: String,
    #[arg(long, default_value = "http://127.0.0.1:1234")]
    pub base_url: String,
    #[arg(long)]
    pub embed_model: Option<String>,
    /// override context_k
    #[arg(long)]
    pub k: Option<usize>,
    /// override context_max_chars
    #[arg(long)]
    pub max_chars: Option<usize>,
    /// channels override: VKG, VK, V or K
    #[arg(long)]
    pub channels: Option<String>,
    /// also emit the verbatim source messages of every included memory
    #[arg(long)]
    pub with_sources: bool,
    #[arg(long, default_value = "400")]
    pub quote_chars: usize,
    /// RetrievalConfig overrides, e.g. "depth=50,weight_graph=0.3,context_min_vector_sim=0.2"
    #[arg(long)]
    pub retrieval: Option<String>,
}

#[derive(Args, Debug, Clone)]
pub struct LmeArgs {
    #[command(flatten)]
    pub common: Common,
    #[arg(long)]
    pub db_dir: String,
    #[arg(long, default_value_t = lme::default_file().to_string_lossy().into_owned())]
    pub file: String,
    /// stratified sample size (0 = all)
    #[arg(long, default_value = "50")]
    pub n: i64,
    #[arg(long, default_value = "1")]
    pub seed: i64,
    /// comma-separated question_type filter applied after sampling
    #[arg(long)]
    pub types: Option<String>,
}

#[derive(Args, Debug, Clone)]
pub struct LocomoArgs {
    #[command(flatten)]
    pub common: Common,
    #[arg(long)]
    pub db: String,
    #[arg(long, default_value = "0")]
    pub conv: i64,
    #[arg(long, default_value = "8")]
    pub sessions: i64,
}

/// Apply `key=val,key=val` to a `RetrievalConfig`, parsing each value by the field's type.
pub fn apply_retrieval(rc: &mut RetrievalConfig, spec: &str) -> HResult<()> {
    for pair in spec.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let (key, val) = pair
            .split_once('=')
            .ok_or_else(|| HarnessError::new("ValueError", format!("--retrieval item {pair:?} is not key=val")))?;
        let (key, val) = (key.trim(), val.trim());
        let bad = |what: &str| HarnessError::new("ValueError", format!("--retrieval {key}={val:?}: not {what}"));
        let float = || val.parse::<f64>().map_err(|_| bad("a number"));
        let count = || val.parse::<usize>().map_err(|_| bad("a non-negative integer"));
        match key {
            "depth" => rc.depth = count()?,
            "rrf_k" => rc.rrf_k = val.parse().map_err(|_| bad("an integer"))?,
            "weight_vector" => rc.weight_vector = float()?,
            "weight_keyword" => rc.weight_keyword = float()?,
            "weight_graph" => rc.weight_graph = float()?,
            "subject_boost" => rc.subject_boost = float()?,
            "entity_boost" => rc.entity_boost = float()?,
            "importance_weight" => rc.importance_weight = float()?,
            "recency_weight" => rc.recency_weight = float()?,
            "recency_half_life_days" => rc.recency_half_life_days = float()?,
            "min_channel_hits" => rc.min_channel_hits = count()?,
            "context_max_chars" => rc.context_max_chars = count()?,
            "context_k" => rc.context_k = count()?,
            "context_min_relative_score" => rc.context_min_relative_score = float()?,
            "vector_min_sim" => rc.vector_min_sim = float()?,
            "context_min_vector_sim" => rc.context_min_vector_sim = float()?,
            "temporal_prior" => rc.temporal_prior = float()?,
            _ => {
                return Err(HarnessError::new(
                    "ValueError",
                    format!(
                        "--retrieval: unknown key {key:?} (depth, rrf_k, weight_vector, weight_keyword, weight_graph, \
                         subject_boost, entity_boost, importance_weight, recency_weight, recency_half_life_days, \
                         min_channel_hits, context_max_chars, context_k, context_min_relative_score, vector_min_sim, \
                         context_min_vector_sim, temporal_prior)"
                    ),
                ))
            }
        }
    }
    Ok(())
}

// The config is edited field by field, so it reads like the harnesses' `make_config`.
#[allow(clippy::field_reassign_with_default)]
fn make_config(c: &Common) -> HResult<ChatMemoryConfig> {
    let mut cfg = ChatMemoryConfig::default();
    cfg.llm_model = Some(c.model.clone());
    cfg.llm_base_url = Some(c.base_url.clone());
    cfg.embed_model = c.embed_model.clone();
    cfg.worker.maintenance_interval = 0.0;
    if let Some(spec) = &c.retrieval {
        apply_retrieval(&mut cfg.retrieval, spec)?;
    }
    Ok(cfg)
}

/// `memory_id -> {message_id}` over active memories, straight from the database file.
fn source_map(db: &Path) -> HResult<HashMap<String, HashSet<String>>> {
    let conn = rusqlite::Connection::open_with_flags(db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let mut stmt = conn.prepare(
        "SELECT ms.memory_id, ms.message_id FROM memory_sources ms JOIN memories m ON m.memory_id=ms.memory_id WHERE m.status='active'",
    )?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
    let mut map: HashMap<String, HashSet<String>> = HashMap::new();
    for row in rows {
        let (mid, msg) = row?;
        map.entry(mid).or_default().insert(msg);
    }
    Ok(map)
}

/// What the product shows for one question.
struct Context {
    block: String,
    hits: Vec<RetrievedMemory>,
    /// `[{memory_id, quotes: [{speaker, sent_at, text}]}]`, when `--with-sources`
    quotes: Option<Vec<Value>>,
}

/// The context block exactly as `context_with_results` builds it, plus (optionally) the sources of its memories.
///
/// `now` is the question date (an ISO or LoCoMo-style date); it is only handed to the search when the temporal prior
/// is on, so the dumps of every other configuration stay identical.
async fn retrieve(cm: &ChatMemory, c: &Common, question: &str, now: Option<String>) -> HResult<Context> {
    let r = &cm.retriever;
    let opts = SearchOptions {
        touch: false,
        now: now.filter(|_| r.config.temporal_prior > 0.0),
        channels: c.channels.clone(),
        query_embedding: Some(cm.llm().embed(question).await?), // embed once for both calls
        ..Default::default()
    };
    let (block, hits) = r.context_with_results(question, c.k, c.max_chars, DEFAULT_HEADER, &opts).await?;
    let mut quotes = None;
    if c.with_sources {
        // the same search the block came from, with sources loaded; keep the memories the block included, in order
        let k = c.k.filter(|k| *k > 0).unwrap_or(r.config.context_k);
        let full = r.search(question, &SearchOptions { k, with_sources: true, ..opts }).await?;
        let by_id: HashMap<&str, &RetrievedMemory> = full.iter().map(|h| (h.memory.memory_id.as_str(), h)).collect();
        quotes = Some(
            hits.iter()
                .map(|h| {
                    let id = h.memory.memory_id.as_str();
                    let srcs = by_id.get(id).map(|f| f.sources.as_slice()).unwrap_or_default();
                    let quotes: Vec<Value> = srcs
                        .iter()
                        .map(|m| json!({"speaker": m.speaker, "sent_at": m.sent_at, "text": py_prefix(&m.text, c.quote_chars)}))
                        .collect();
                    json!({"memory_id": id, "quotes": quotes})
                })
                .collect(),
        );
    }
    Ok(Context { block, hits, quotes })
}

/// A question's row; `evidence` is the set of gold message ids.
#[allow(clippy::too_many_arguments)]
fn make_row(
    (id, typ, abstention): (String, String, Option<bool>),
    question: &str,
    gold: String,
    question_date: String,
    ctx: Context,
    src: &HashMap<String, HashSet<String>>,
    evidence: &HashSet<String>,
) -> Value {
    let cites_gold = |h: &RetrievedMemory| src.get(&h.memory.memory_id).is_some_and(|s| s.iter().any(|m| evidence.contains(m)));
    let mut row = Map::new();
    row.insert("id".into(), id.into());
    row.insert("type".into(), typ.into());
    if let Some(a) = abstention {
        row.insert("abstention".into(), a.into());
    }
    row.insert("question".into(), question.into());
    row.insert("gold".into(), gold.into());
    row.insert("question_date".into(), question_date.into());
    row.insert("n_ctx".into(), ctx.hits.len().into());
    row.insert("ctx_chars".into(), ctx.block.chars().count().into());
    row.insert("memory_ids".into(), ctx.hits.iter().map(|h| h.memory.memory_id.clone()).collect::<Vec<_>>().into());
    row.insert("evidence_retrieved".into(), ctx.hits.iter().any(cites_gold).into());
    row.insert("context".into(), ctx.block.into());
    if let Some(q) = ctx.quotes {
        row.insert("source_quotes".into(), q.into());
    }
    Value::Object(row)
}

fn progress(done: usize, total: usize, stdout: &mut dyn Write) -> HResult<()> {
    if done.is_multiple_of(10) || done == total {
        writeln!(stdout, "[{done}/{total}] rows")?;
        stdout.flush()?;
    }
    Ok(())
}

fn write_out(c: &Common, config: Value, rows: Vec<Value>) -> HResult<()> {
    let doc = json!({"config": config, "n": rows.len(), "rows": rows});
    std::fs::write(expanduser(&c.out), serde_json::to_string_pretty(&doc)?)?;
    Ok(())
}

fn common_config(c: &Common) -> Map<String, Value> {
    let Value::Object(m) = json!({
        "out": c.out, "model": c.model, "base_url": c.base_url, "embed_model": c.embed_model, "k": c.k,
        "max_chars": c.max_chars, "channels": c.channels, "with_sources": c.with_sources,
        "quote_chars": c.quote_chars, "retrieval": c.retrieval,
    }) else {
        unreachable!()
    };
    m
}

pub async fn run_lme(a: &LmeArgs, stdout: &mut dyn Write) -> HResult<()> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&a.file)?)?;
    let data = data.as_array().cloned().unwrap_or_default();
    // the sample and the --types filter are lme::main_async's, so the questions match the published 50
    let mut sample = if a.n != 0 { stratified_sample(&data, a.n, a.seed as i128, 3)? } else { data };
    if let Some(types) = a.types.as_deref().filter(|t| !t.is_empty()) {
        let keep: HashSet<&str> = types.split(',').map(str::trim).collect();
        sample.retain(|q| q.get("question_type").and_then(Value::as_str).is_some_and(|t| keep.contains(t)));
    }
    let db_dir = expanduser(&a.db_dir);
    let mut rows = Vec::new();
    for (i, q) in sample.iter().enumerate() {
        let qid = py_getstr(q, "question_id")?;
        let db = Path::new(&db_dir).join(format!("{qid}.db"));
        if !db.exists() {
            eprintln!("warning: {} missing, skipping {qid}", db.display());
            continue;
        }
        // gold turns: message ids `<session_id>:<turn index>` with has_answer, as the ingest harness numbers them
        let mut gold_turns = HashSet::new();
        let sids = py_getitem(q, "haystack_session_ids")?.as_array().cloned().unwrap_or_default();
        let sessions = py_getitem(q, "haystack_sessions")?.as_array().cloned().unwrap_or_default();
        for (sid, turns) in sids.iter().zip(&sessions) {
            for (t, turn) in turns.as_array().map(Vec::as_slice).unwrap_or_default().iter().enumerate() {
                if turn.get("has_answer").and_then(Value::as_bool).unwrap_or(false) {
                    gold_turns.insert(format!("{}:{t}", py_str(sid)));
                }
            }
        }
        let question = py_getstr(q, "question")?;
        let cm = ChatMemory::open(&db.to_string_lossy(), None, make_config(&a.common)?)?;
        let now = lme::parse_lme_date(q.get("question_date").and_then(Value::as_str)).ok().flatten();
        let ctx = retrieve(&cm, &a.common, question, now).await?;
        cm.close().await;
        let src = source_map(&db)?;
        let is_abs = qid.ends_with("_abs");
        let typ = if is_abs { "abstention".to_string() } else { py_getstr(q, "question_type")?.to_string() };
        let date = q.get("question_date").map(py_str).unwrap_or_default();
        let meta = (qid.to_string(), typ, Some(is_abs));
        rows.push(make_row(meta, question, py_str(py_getitem(q, "answer")?), date, ctx, &src, &gold_turns));
        progress(i + 1, sample.len(), stdout)?;
    }
    let mut cfg = common_config(&a.common);
    cfg.extend([
        ("db_dir".into(), a.db_dir.clone().into()),
        ("file".into(), a.file.clone().into()),
        ("n".into(), a.n.into()),
        ("seed".into(), a.seed.into()),
        ("types".into(), a.types.clone().into()),
    ]);
    write_out(&a.common, cfg.into(), rows)
}

/// Tolerant variant of `locomo::eligible_qa` for this bin: evidence tokens whose session number does not parse
/// (e.g. an empty `D:` head from a stray separator) are dropped with one stderr warning each instead of aborting.
/// Questions left with no parseable evidence are skipped. Each kept question is paired with its valid evidence ids.
fn eligible_qa_tolerant(qa: &[Value], max_session: i64) -> HResult<Vec<(Value, Vec<String>)>> {
    let mut out = Vec::new();
    let mut bad = 0usize;
    for (qi, q) in qa.iter().enumerate() {
        let adversarial = q.get("category").and_then(Value::as_f64) == Some(5.0);
        if adversarial || !q.get("evidence").is_some_and(neuralgraph_memory::pycompat::py_truthy) {
            continue;
        }
        let mut good = Vec::new();
        for e in evidence_ids(q)? {
            match sess_of(&e) {
                Ok(s) => good.push((e, s)),
                Err(_) => {
                    bad += 1;
                    eprintln!("warning: qa[{qi}]: unparseable evidence token {e:?} (raw evidence: {}); ignored", q.get("evidence").map(|v| v.to_string()).unwrap_or_default());
                }
            }
        }
        if !good.is_empty() && good.iter().all(|(_, s)| *s <= max_session) {
            out.push((q.clone(), good.into_iter().map(|(e, _)| e).collect()));
        }
    }
    if bad > 0 {
        eprintln!("warning: ignored {bad} unparseable LoCoMo evidence token(s)");
    }
    Ok(out)
}

pub async fn run_locomo(a: &LocomoArgs, stdout: &mut dyn Write) -> HResult<()> {
    let (conv, qa) = locomo::load_conv(a.conv)?;
    let qs = eligible_qa_tolerant(qa.as_array().map(Vec::as_slice).unwrap_or_default(), a.sessions)?;
    let db = expanduser(&a.db);
    if !Path::new(&db).exists() {
        return Err(HarnessError::new("FileNotFoundError", format!("{db} does not exist")));
    }
    let src = source_map(Path::new(&db))?;
    // the date of the last ingested session is "today" for the reader
    let date = conv.get(format!("session_{}_date_time", a.sessions)).map(py_str).unwrap_or_default();
    let cm = ChatMemory::open(&db, None, make_config(&a.common)?)?;
    let mut rows = Vec::new();
    for (i, (q, ids)) in qs.iter().enumerate() {
        let question = py_getstr(q, "question")?;
        let ev: HashSet<String> = ids.iter().map(|e| format!("locomo-{}-{e}", a.conv)).collect();
        let typ = match q.get("category").and_then(Value::as_i64) {
            Some(1) => "multi-hop".to_string(),
            Some(2) => "temporal".to_string(),
            Some(3) => "open-domain".to_string(),
            Some(4) => "single-hop".to_string(),
            _ => py_str(py_getitem(q, "category")?),
        };
        let ctx = retrieve(&cm, &a.common, question, Some(date.clone()).filter(|d| !d.is_empty())).await?;
        let meta = (format!("locomo-{}-q{i}", a.conv), typ, None);
        rows.push(make_row(meta, question, py_str(py_getitem(q, "answer")?), date.clone(), ctx, &src, &ev));
        progress(i + 1, qs.len(), stdout)?;
    }
    cm.close().await;
    let mut cfg = common_config(&a.common);
    cfg.extend([
        ("db".into(), a.db.clone().into()),
        ("conv".into(), a.conv.into()),
        ("sessions".into(), a.sessions.into()),
    ]);
    write_out(&a.common, cfg.into(), rows)
}

/// Entry point of the binary.
pub async fn run(cli: &Cli, stdout: &mut dyn Write) -> HResult<()> {
    match &cli.cmd {
        Command::Lme(a) => run_lme(a, stdout).await,
        Command::Locomo(a) => run_locomo(a, stdout).await,
    }
}
