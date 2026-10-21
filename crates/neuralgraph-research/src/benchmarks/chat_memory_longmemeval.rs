//! Port of `research/benchmarks/chat_memory_longmemeval.py`: LongMemEval (Wu et al., ICLR 2025) on the SHIPPED
//! chat_memory pipeline, retrieval-only, fully local (binary `chat-memory-longmemeval`).
//!
//! Each question has its own chat history (a list of user/assistant sessions with dates). For every sampled
//! question this builds a fresh database, ingests each session as its own chat through the real extractor/worker of
//! the product crate, then asks the question through `MemoryRetriever` and checks whether a retrieved memory cites a
//! turn the dataset marks `has_answer: true`. No answer model, no judge.
//!
//! ```text
//! chat-memory-longmemeval --n 50 --seed 1 --out .context/exp/lme_oracle_default.json
//! chat-memory-longmemeval --n 50 --seed 1 --extract-assistant --out ...
//! ```
//!
//! Metrics per question type:
//! * `turn@k`      a retrieved memory (k = shipped context_k) cites a has_answer turn           (exact, provenance)
//! * `session@k`   a retrieved memory cites any turn of an answer session                       (looser)
//! * `ceiling`     some memory in the DB cites a has_answer turn at all (extraction, not retrieval)
//! * `abstain`     for `*_abs` questions: context_for returned an empty block (the right behaviour is "nothing
//!   relevant")
//!
//! Dataset: `research/datasets/longmemeval/longmemeval_oracle.json` (HF xiaowu0162/longmemeval-cleaned, MIT).
//! The stratified sample uses CPython's `random.Random` ([`PyRandom`]), so `--n 50 --seed 1` selects exactly the
//! published Track D questions in the same order.

use super::pyharness::{
    expanduser, json_indent, parse_py_int, perf_counter, py_getitem, py_getstr, py_sum, repo_root, HResult,
    HarnessError, PyNum,
};
use crate::pyrandom::PyRandom;
use chrono::NaiveDate;
use clap::Parser;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{
    decimal_value, dumps, fnum, obj, py_prefix, py_repr_counts, py_round, py_round_int, py_str, py_strip, py_truthy,
};
use neuralgraph_memory::retrieval::DEFAULT_HEADER;
use neuralgraph_memory::{ChatMemory, ChatMemoryConfig, LlmClient, RetrievedMemory, SearchOptions};
use regex::Regex;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock};

/// `research/datasets/longmemeval/longmemeval_oracle.json` under the repository root.
pub fn default_file() -> PathBuf {
    repo_root().join("research/datasets/longmemeval/longmemeval_oracle.json")
}

static DATE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(\d{4})/(\d{2})/(\d{2})[\s\x1c-\x1f]*(?:\([A-Za-z]{3}\))?[\s\x1c-\x1f]*(\d{1,2}):(\d{2})").unwrap()
});

/// `int()` of a run of Unicode decimal digits (what Python's `\d` matched).
fn digits_value(s: &str) -> i64 {
    s.chars().fold(0i64, |acc, c| acc * 10 + decimal_value(c).unwrap_or(0) as i64)
}

/// `'2023/04/10 (Mon) 17:50'` -> `'2023-04-10T17:50:00'`; `None` when the text has no such date. Out-of-range
/// fields are the `ValueError` Python's `datetime(...)` raised.
pub fn parse_lme_date(s: Option<&str>) -> HResult<Option<String>> {
    let Some(c) = DATE_RE.captures(s.unwrap_or("")) else { return Ok(None) };
    let [y, mo, d, hh, mm] = [1, 2, 3, 4, 5].map(|i| digits_value(&c[i]));
    let invalid = |msg: String| Err(HarnessError::new("ValueError", msg));
    if !(1..=9999).contains(&y) {
        return invalid(format!("year {y} is out of range"));
    }
    if !(1..=12).contains(&mo) {
        return invalid("month must be in 1..12".into());
    }
    if d < 1 || NaiveDate::from_ymd_opt(y as i32, mo as u32, d as u32).is_none() {
        return invalid("day is out of range for month".into());
    }
    if !(0..=23).contains(&hh) {
        return invalid("hour must be in 0..23".into());
    }
    if !(0..=59).contains(&mm) {
        return invalid("minute must be in 0..59".into());
    }
    Ok(Some(format!("{y:04}-{mo:02}-{d:02}T{hh:02}:{mm:02}:00")))
}

/// `stratified_sample(data, n, seed, min_per_type)`: per question type (sorted), `max(min_per_type,
/// round(n * share))` questions drawn with `random.Random(seed).sample`, then the whole draw shuffled with the same
/// generator and cut to `n`.
pub fn stratified_sample(data: &[Value], n: i64, seed: i128, min_per_type: i64) -> HResult<Vec<Value>> {
    let mut by_type: IndexMap<String, Vec<Value>> = IndexMap::new();
    for q in data {
        by_type.entry(py_getstr(q, "question_type")?.to_string()).or_default().push(q.clone());
    }
    let mut rng = PyRandom::new(seed);
    let total = data.len();
    let mut out: Vec<Value> = Vec::new();
    let mut types: Vec<&String> = by_type.keys().collect();
    types.sort();
    for t in types {
        let qs = &by_type[t];
        let k = min_per_type.max(py_round_int((n * qs.len() as i64) as f64 / total as f64));
        let take = k.min(qs.len() as i64);
        if take < 0 {
            return Err(HarnessError::new("ValueError", "Sample larger than population or is negative"));
        }
        out.extend(rng.sample(qs, take as usize));
    }
    rng.shuffle(&mut out);
    let len = out.len() as i64;
    // `out[:max(n, len(out))] if len(out) <= n else out[:n]` (a negative n slices from the end)
    let stop = if len <= n {
        len
    } else if n >= 0 {
        n
    } else {
        (len + n).max(0)
    };
    out.truncate(stop as usize);
    Ok(out)
}

#[derive(Parser, Debug, Clone)]
#[command(name = "chat-memory-longmemeval", infer_long_args = true)]
pub struct Args {
    #[arg(long, default_value_t = default_file().to_string_lossy().into_owned())]
    pub file: String,
    /// stratified sample size (0 = all)
    #[arg(long, default_value = "50", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub n: i64,
    #[arg(long, default_value = "1", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub seed: i64,
    #[arg(long, default_value = "/tmp/lme")]
    pub db_dir: String,
    #[arg(long)]
    pub out: Option<String>,
    #[arg(long)]
    pub resume: bool,
    #[arg(long, default_value = "qwen3.8-9b-mlx")]
    pub model: String,
    #[arg(long, default_value = "http://127.0.0.1:1234")]
    pub base_url: String,
    #[arg(long, default_value = "4", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub parallel: i64,
    /// sessions (chats) extracted in parallel
    #[arg(long, default_value = "2", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub concurrency: i64,
    /// 0 = shipped default
    #[arg(long, default_value = "0", value_parser = parse_py_int, allow_hyphen_values = true)]
    pub batch_size: i64,
    #[arg(long = "extract-assistant", overrides_with = "no_extract_assistant")]
    pub extract_assistant_flag: bool,
    #[arg(long = "no-extract-assistant", overrides_with = "extract_assistant_flag")]
    pub no_extract_assistant: bool,
    #[arg(long, default_value = "The user")]
    pub user_name: String,
    /// comma-separated question_type filter applied after sampling
    #[arg(long)]
    pub types: Option<String>,
}

impl Args {
    /// argparse's shared `dest="extract_assistant"`: `None` unless one of the two flags was given (the last wins).
    pub fn extract_assistant(&self) -> Option<bool> {
        if self.extract_assistant_flag {
            Some(true)
        } else if self.no_extract_assistant {
            Some(false)
        } else {
            None
        }
    }

    /// `vars(args)`: every option in definition order.
    pub fn to_value(&self) -> Value {
        let opt = |s: &Option<String>| s.clone().map(Value::String).unwrap_or(Value::Null);
        obj([
            ("file", Value::String(self.file.clone())),
            ("n", Value::from(self.n)),
            ("seed", Value::from(self.seed)),
            ("db_dir", Value::String(self.db_dir.clone())),
            ("out", opt(&self.out)),
            ("resume", Value::Bool(self.resume)),
            ("model", Value::String(self.model.clone())),
            ("base_url", Value::String(self.base_url.clone())),
            ("parallel", Value::from(self.parallel)),
            ("concurrency", Value::from(self.concurrency)),
            ("batch_size", Value::from(self.batch_size)),
            ("extract_assistant", self.extract_assistant().map(Value::Bool).unwrap_or(Value::Null)),
            ("user_name", Value::String(self.user_name.clone())),
            ("types", opt(&self.types)),
        ])
    }
}

fn unsigned(name: &str, v: i64) -> HResult<usize> {
    usize::try_from(v).map_err(|_| {
        HarnessError::new("ValueError", format!("{name}={v}: the Rust product config needs a non-negative value"))
    })
}

/// The pipeline configuration of one question's database.
// The config is edited field by field in the Python's order, so the two read side by side.
#[allow(clippy::field_reassign_with_default)]
pub fn make_config(args: &Args) -> HResult<ChatMemoryConfig> {
    let mut cfg = ChatMemoryConfig::default();
    cfg.debounce_seconds = 0.0;
    cfg.llm_model = Some(args.model.clone());
    cfg.llm_base_url = Some(args.base_url.clone());
    cfg.llm_max_parallel = unsigned("parallel", args.parallel)?;
    cfg.worker.concurrency = unsigned("concurrency", args.concurrency)?;
    cfg.worker.maintenance_interval = 0.0;
    cfg.extraction.llm_timeout = 600.0;
    if let Some(v) = args.extract_assistant() {
        cfg.extraction.extract_assistant = v;
    }
    cfg.extraction.speaker_names = IndexMap::from([("user".to_string(), args.user_name.clone())]);
    if args.batch_size != 0 {
        cfg.worker.batch_size = args.batch_size;
    }
    Ok(cfg)
}

/// Makes the model client of each question's `ChatMemory` (`None`: the configured server, as in the Python).
pub type LlmFactory<'a> = &'a dyn Fn() -> Option<Arc<dyn LlmClient>>;

fn as_list(v: Option<&Value>) -> &[Value] {
    v.and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default()
}

/// `session_id` of a message id `<session_id>:<turn index>`.
pub fn sess_of(mid: &str) -> &str {
    mid.rsplit_once(':').map(|(s, _)| s).unwrap_or(mid)
}

/// Ingest one question's history into a fresh database, retrieve, and score.
pub async fn run_question(q: &Value, args: &Args, db_dir: &Path, llm: LlmFactory<'_>) -> HResult<Value> {
    let qid = py_getstr(q, "question_id")?;
    let db = db_dir.join(format!("{qid}.db"));
    if db.exists() {
        std::fs::remove_file(&db)?;
    }
    let db_str = db.to_string_lossy().into_owned();
    let cm = ChatMemory::open(&db_str, llm(), make_config(args)?)?;
    // message ids: <session_id>:<turn index>; has_answer turns are the gold set
    let mut gold_turns: HashSet<String> = HashSet::new();
    let answer_sessions: HashSet<String> =
        as_list(q.get("answer_session_ids").filter(|v| py_truthy(v))).iter().map(py_str).collect();
    let mut n_msgs = 0usize;
    let t0 = perf_counter();
    let sids = as_list(Some(py_getitem(q, "haystack_session_ids")?));
    let dates = as_list(Some(py_getitem(q, "haystack_dates")?));
    let sessions = as_list(Some(py_getitem(q, "haystack_sessions")?));
    for ((sid, sdate), turns) in sids.iter().zip(dates).zip(sessions) {
        let sent_at = parse_lme_date(sdate.as_str())?;
        let sid = py_str(sid);
        let mut batch = Vec::new();
        for (i, t) in as_list(Some(turns)).iter().enumerate() {
            let mid = format!("{sid}:{i}");
            let role = match t.get("role").filter(|v| py_truthy(v)) {
                Some(r) => py_str(r).to_lowercase(),
                None => "user".to_string(),
            };
            let text = t.get("content").filter(|v| py_truthy(v)).cloned().unwrap_or(Value::String(String::new()));
            batch.push(obj([
                ("speaker", Value::from(if role == "assistant" { "assistant" } else { "user" })),
                ("role", Value::String(role.clone())),
                ("text", text),
                ("sent_at", sent_at.clone().map(Value::String).unwrap_or(Value::Null)),
                ("message_id", Value::String(mid.clone())),
            ]));
            if t.get("has_answer").is_some_and(py_truthy) {
                gold_turns.insert(mid);
            }
        }
        cm.add_messages(&sid, &batch)?;
        n_msgs += batch.len();
    }
    let batches = cm.process_pending(None).await?;
    let t_extract = perf_counter() - t0;
    let m = cm.worker.metrics();

    // retrieval with the shipped defaults (context_with_results: k, channels, floors from config)
    let r = cm.retriever.clone();
    let question = py_getstr(q, "question")?;
    let quiet = SearchOptions { touch: false, ..Default::default() };
    let (block, hits) = r.context_with_results(question, None, None, DEFAULT_HEADER, &quiet).await?;
    let search_opts = SearchOptions { k: r.config.context_k, with_sources: false, touch: false, ..Default::default() };
    let hits_search = r.search(question, &search_opts).await?;
    cm.close().await;

    let conn = rusqlite::Connection::open(&db)?;
    let mut src: HashMap<String, HashSet<String>> = HashMap::new();
    {
        let mut stmt = conn.prepare(
            "SELECT ms.memory_id, ms.message_id FROM memory_sources ms JOIN memories m ON m.memory_id=ms.memory_id WHERE m.status='active'",
        )?;
        let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
        for row in rows {
            let (mid, msg) = row?;
            src.entry(mid).or_default().insert(msg);
        }
    }
    let n_mem: i64 = conn.query_row("SELECT COUNT(*) FROM memories WHERE status='active'", [], |r| r.get(0))?;
    drop(conn);
    let cited: HashSet<&String> = src.values().flatten().collect();

    let empty = HashSet::new();
    let sources = |h: &RetrievedMemory| src.get(&h.memory.memory_id).unwrap_or(&empty);
    let turn_hit = |hs: &[RetrievedMemory]| hs.iter().position(|h| sources(h).iter().any(|x| gold_turns.contains(x)));
    let sess_hit = |hs: &[RetrievedMemory]| {
        hs.iter().position(|h| sources(h).iter().any(|x| answer_sessions.contains(sess_of(x))))
    };
    let rank = |r: Option<usize>| r.map(Value::from).unwrap_or(Value::Null);

    let is_abs = qid.ends_with("_abs");
    Ok(obj([
        ("question_id", Value::String(qid.to_string())),
        ("type", py_getitem(q, "question_type")?.clone()),
        ("abstention", Value::Bool(is_abs)),
        ("question", Value::String(question.to_string())),
        ("answer", py_getitem(q, "answer")?.clone()),
        ("sessions", Value::from(sessions.len())),
        ("messages", Value::from(n_msgs)),
        ("gold_turns", Value::from(gold_turns.len())),
        ("memories", Value::from(n_mem)),
        ("batches", Value::from(batches)),
        ("extract_seconds", fnum(py_round(t_extract, 1))),
        ("skipped", Value::from(m.messages_skipped)),
        ("failures", Value::from(m.failures)),
        ("dead", Value::from(m.dead_jobs)),
        ("ceiling_turn", Value::Bool(cited.iter().any(|x| gold_turns.contains(*x)))),
        ("ceiling_session", Value::Bool(cited.iter().any(|x| answer_sessions.contains(sess_of(x))))),
        ("ctx_turn_rank", rank(turn_hit(&hits))),
        ("ctx_session_rank", rank(sess_hit(&hits))),
        ("ctx_n", Value::from(hits.len())),
        ("ctx_empty", Value::Bool(block.is_empty())),
        ("search_turn_rank", rank(turn_hit(&hits_search))),
        ("search_session_rank", rank(sess_hit(&hits_search))),
        ("context_preview", Value::String(py_prefix(&block, 400).to_string())),
    ]))
}

fn field<'a>(r: &'a Value, key: &str) -> HResult<&'a Value> {
    py_getitem(r, key)
}

/// `round(100 * sum(...) / max(1, len(rows)), 1)` over a per-row flag.
fn pct(rows: &[&Value], flag: impl Fn(&Value) -> HResult<bool>, denom: usize) -> HResult<Value> {
    let mut hits = 0i64;
    for r in rows {
        hits += flag(r)? as i64;
    }
    Ok(fnum(py_round((100 * hits) as f64 / denom as f64, 1)))
}

fn truthy_field(key: &'static str) -> impl Fn(&Value) -> HResult<bool> {
    move |r| Ok(py_truthy(field(r, key)?))
}

fn not_none_field(key: &'static str) -> impl Fn(&Value) -> HResult<bool> {
    move |r| Ok(!field(r, key)?.is_null())
}

fn py_sum_field(rows: &[&Value], key: &str) -> HResult<PyNum> {
    let vals: Vec<&Value> = rows.iter().map(|r| field(r, key)).collect::<HResult<_>>()?;
    py_sum(vals)
}

/// Rows split into (non-abstention, abstention).
fn split_abstention<'a>(rs: &[&'a Value]) -> HResult<(Vec<&'a Value>, Vec<&'a Value>)> {
    let mut non_abs = Vec::new();
    let mut abs_rows = Vec::new();
    for r in rs {
        if py_truthy(field(r, "abstention")?) {
            abs_rows.push(*r);
        } else {
            non_abs.push(*r);
        }
    }
    Ok((non_abs, abs_rows))
}

/// Per-type and overall report over the scored rows (fresh or resumed from an earlier `--out` file).
pub fn summarize(rows: &[Value]) -> HResult<Value> {
    let mut by_type: IndexMap<String, Vec<&Value>> = IndexMap::new();
    for r in rows {
        by_type.entry(py_getstr(r, "type")?.to_string()).or_default().push(r);
    }
    let mut types: Vec<&String> = by_type.keys().collect();
    types.sort();
    let abstain_ok = |abs_rows: &[&Value]| -> HResult<Value> {
        if abs_rows.is_empty() {
            Ok(Value::Null)
        } else {
            pct(abs_rows, truthy_field("ctx_empty"), abs_rows.len())
        }
    };
    let mut table = Vec::new();
    for t in types {
        let rs = &by_type[t];
        let n = rs.len();
        let (non_abs, abs_rows) = split_abstention(rs)?;
        let d = non_abs.len().max(1);
        table.push(obj([
            ("type", Value::String(t.clone())),
            ("n", Value::from(n)),
            ("turn@k", pct(&non_abs, not_none_field("ctx_turn_rank"), d)?),
            ("session@k", pct(&non_abs, not_none_field("ctx_session_rank"), d)?),
            ("ceiling_turn", pct(&non_abs, truthy_field("ceiling_turn"), d)?),
            ("abstain_ok", abstain_ok(&abs_rows)?),
            ("n_abs", Value::from(abs_rows.len())),
            ("mean_memories", fnum(py_round(py_sum_field(rs, "memories")?.as_f64() / n as f64, 1))),
            ("mean_extract_s", fnum(py_round(py_sum_field(rs, "extract_seconds")?.as_f64() / n as f64, 1))),
        ]));
    }
    let all: Vec<&Value> = rows.iter().collect();
    let (non_abs, abs_rows) = split_abstention(&all)?;
    let d = non_abs.len().max(1);
    let overall = obj([
        ("n", Value::from(rows.len())),
        ("n_non_abstention", Value::from(non_abs.len())),
        ("turn@k", pct(&non_abs, not_none_field("ctx_turn_rank"), d)?),
        ("session@k", pct(&non_abs, not_none_field("ctx_session_rank"), d)?),
        ("ceiling_turn", pct(&non_abs, truthy_field("ceiling_turn"), d)?),
        ("abstain_ok", abstain_ok(&abs_rows)?),
        ("total_messages", py_sum_field(&all, "messages")?.to_value()),
        ("total_memories", py_sum_field(&all, "memories")?.to_value()),
        ("total_extract_s", py_sum_field(&all, "extract_seconds")?.round(1).to_value()),
    ]);
    Ok(obj([("overall", overall), ("by_type", Value::Array(table))]))
}

/// The per-question progress line.
pub fn progress_line(i: usize, total: usize, row: &Value) -> String {
    let rank = |k: &str| match row.get(k) {
        Some(Value::Number(n)) => n.to_string(),
        _ => "None".to_string(),
    };
    let flag = |k: &str| if row.get(k).is_some_and(py_truthy) { "True" } else { "False" };
    format!(
        "[{}/{}] {:<26} msgs={:>3} mem={:>3} {:>6.1}s  ceiling={} ctx_turn={} ctx_sess={} empty={}",
        i + 1,
        total,
        row["type"].as_str().unwrap_or_default(),
        row["messages"].to_string(),
        row["memories"].to_string(),
        row["extract_seconds"].as_f64().unwrap_or(0.0),
        if row.get("ceiling_turn").is_some_and(py_truthy) { "Y" } else { "-" },
        rank("ctx_turn_rank"),
        rank("ctx_session_rank"),
        flag("ctx_empty"),
    )
}

/// The whole run: sample, filter, (resume,) ingest + score each question, print progress and the summary, and
/// rewrite `--out` after every question. Returns the final summary.
pub async fn main_async(args: &Args, llm: LlmFactory<'_>, stdout: &mut dyn Write) -> HResult<Value> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&args.file)?)?;
    let data = data.as_array().cloned().unwrap_or_default();
    let mut sample = if args.n != 0 { stratified_sample(&data, args.n, args.seed as i128, 3)? } else { data.clone() };
    if let Some(types) = args.types.as_deref().filter(|t| !t.is_empty()) {
        let keep: HashSet<&str> = types.split(',').map(py_strip).collect();
        let mut kept = Vec::new();
        for q in sample {
            if keep.contains(py_getstr(&q, "question_type")?) {
                kept.push(q);
            }
        }
        sample = kept;
    }
    let db_dir = PathBuf::from(expanduser(&args.db_dir));
    std::fs::create_dir_all(&db_dir)?;
    let out = args.out.as_deref().filter(|o| !o.is_empty()).map(PathBuf::from);
    let mut rows: Vec<Value> = Vec::new();
    let mut done: IndexSet<String> = IndexSet::new();
    if let Some(o) = out.as_ref().filter(|o| o.exists() && args.resume) {
        let prev: Value = serde_json::from_str(&std::fs::read_to_string(o)?)?;
        rows = prev.get("rows").and_then(Value::as_array).cloned().unwrap_or_default();
        for r in &rows {
            done.insert(py_str(py_getitem(r, "question_id")?));
        }
    }
    let mut type_counts: IndexMap<String, i64> = IndexMap::new();
    for q in &sample {
        *type_counts.entry(py_getstr(q, "question_type")?.to_string()).or_insert(0) += 1;
    }
    writeln!(
        stdout,
        "{} questions ({} already done); types: {}",
        sample.len(),
        done.len(),
        py_repr_counts(&type_counts)
    )?;
    stdout.flush()?;
    for (i, q) in sample.iter().enumerate() {
        if done.contains(py_getstr(q, "question_id")?) {
            continue;
        }
        let row = run_question(q, args, &db_dir, llm).await?;
        rows.push(row);
        writeln!(stdout, "{}", progress_line(i, sample.len(), rows.last().expect("row")))?;
        stdout.flush()?;
        if let Some(o) = &out {
            let doc = obj([
                ("config", args.to_value()),
                ("summary", summarize(&rows)?),
                ("rows", Value::Array(rows.clone())),
            ]);
            std::fs::write(o, dumps(&doc, &json_indent(2)))?;
        }
    }
    let s = summarize(&rows)?;
    writeln!(stdout, "{}", dumps(&s, &json_indent(2)))?;
    Ok(s)
}

/// Question ids of a sample, in order (what the fixtures pin).
pub fn question_ids(sample: &[Value]) -> Vec<String> {
    sample.iter().map(|q| py_str(&q["question_id"])).collect()
}
