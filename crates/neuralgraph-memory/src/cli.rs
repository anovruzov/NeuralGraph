//! Command line for the cross-chat memory system.
//!
//! ```text
//!     neuralgraph-memory serve     # worker + dashboard + REST API + MCP (Streamable HTTP)
//!     neuralgraph-memory mcp       # worker + MCP over stdio (Claude Desktop / Claude Code)
//!     neuralgraph-memory ingest chats.jsonl [--process]
//!     neuralgraph-memory process   # drain the queue in the foreground
//!     neuralgraph-memory search "where does Ali live?"
//!     neuralgraph-memory context "planning a trip"
//!     neuralgraph-memory profile Ali
//!     neuralgraph-memory stats
//!     neuralgraph-memory forget mem_...
//! ```
//!
//! Common options: --db, --model, --base-url, --embed-model, --parallel, --user-name, --fake-llm (tests/demos).
//! LLM settings default to the LLM_BASE_URL, LLM_MODEL and EMBED_MODEL environment variables. Subcommands, flags,
//! environment variables and printed output are the ones of the former `python -m NeuralGraph.chat_memory`.

use crate::error::{MemoryError, Result};
use crate::llm::LlmClient;
use crate::pycompat::{dumps, py_prefix, py_repr_counts, py_str, py_truthy, JsonFormat};
use crate::retrieval::SearchOptions;
use crate::server::{run_server, ServerOptions};
use crate::service::{ChatMemory, ChatMemoryConfig, MessageInput, RememberOptions};
use crate::temporal::parse_datetime_flexible;
use clap::{Args, Parser, Subcommand};
use serde_json::Value;
use std::io::Write;
use std::sync::Arc;

pub const DEFAULT_DB_FALLBACK: &str = "~/.neuralgraph/chat_memory.db";

fn default_db() -> String {
    std::env::var("NEURALGRAPH_MEMORY_DB").unwrap_or_else(|_| DEFAULT_DB_FALLBACK.to_string())
}

const ABOUT: &str = "Command line for the cross-chat memory system.

    neuralgraph-memory serve     # worker + dashboard + REST API + MCP (Streamable HTTP)
    neuralgraph-memory mcp       # worker + MCP over stdio (Claude Desktop / Claude Code)
    neuralgraph-memory ingest chats.jsonl [--process]
    neuralgraph-memory process   # drain the queue in the foreground
    neuralgraph-memory search \"where does Ali live?\"
    neuralgraph-memory context \"planning a trip\"
    neuralgraph-memory profile Ali
    neuralgraph-memory stats
    neuralgraph-memory forget mem_...

Common options: --db, --model, --base-url, --embed-model, --parallel, --user-name, --fake-llm (tests/demos).
LLM settings default to the env vars LLM_BASE_URL, LLM_MODEL, EMBED_MODEL.";

#[derive(Parser, Debug)]
#[command(name = "neuralgraph-memory", version, about = ABOUT, infer_long_args = true)]
pub struct Cli {
    #[command(subcommand)]
    pub cmd: Command,
}

/// Options shared by every subcommand.
#[derive(Args, Debug, Clone)]
pub struct Common {
    /// SQLite file (default ~/.neuralgraph/chat_memory.db; env NEURALGRAPH_MEMORY_DB)
    #[arg(long, default_value_t = default_db())]
    pub db: String,
    /// generation model (default: LLM_MODEL env / backend default)
    #[arg(long)]
    pub model: Option<String>,
    /// LM Studio / Ollama URL (default: LLM_BASE_URL env)
    #[arg(long = "base-url")]
    pub base_url: Option<String>,
    /// embedding model (default: EMBED_MODEL env)
    #[arg(long = "embed-model")]
    pub embed_model: Option<String>,
    /// max concurrent LLM calls (match OLLAMA_NUM_PARALLEL)
    #[arg(long, default_value_t = 4)]
    pub parallel: usize,
    /// chats processed concurrently (default min(parallel,4))
    #[arg(long, default_value_t = 0)]
    pub workers: usize,
    /// messages per extraction pass (2 measured best; 6 is faster but loses recall)
    #[arg(long = "batch-size", default_value_t = 2)]
    pub batch_size: i64,
    /// seconds to wait so a turn and its reply batch together
    #[arg(long, default_value_t = 1.0)]
    pub debounce: f64,
    /// display name for speaker 'user' (e.g. your name)
    #[arg(long = "user-name")]
    pub user_name: Option<String>,
    /// extract memories from assistant turns too (the default)
    #[arg(long = "extract-assistant", overrides_with = "no_extract_assistant")]
    pub extract_assistant: bool,
    /// assistant turns are context only; only what the human says becomes memory
    #[arg(long = "no-extract-assistant", overrides_with = "extract_assistant")]
    pub no_extract_assistant: bool,
    /// deterministic fake model (demos/tests, no server)
    #[arg(long = "fake-llm")]
    pub fake_llm: bool,
    #[arg(short = 'v', long)]
    pub verbose: bool,
}

/// Options of `serve`.
#[derive(Args, Debug, Clone)]
pub struct ServeArgs {
    #[command(flatten)]
    pub common: Common,
    #[arg(long, default_value = "127.0.0.1")]
    pub host: String,
    #[arg(long, default_value_t = 8765)]
    pub port: u16,
    /// bearer token required on /mcp (env NEURALGRAPH_MCP_TOKEN)
    #[arg(long = "mcp-token")]
    pub mcp_token: Option<String>,
    /// bearer token required on /api/* (env NEURALGRAPH_API_TOKEN)
    #[arg(long = "api-token")]
    pub api_token: Option<String>,
    /// restrict browser Origin on /mcp (repeatable)
    #[arg(long = "allowed-origin")]
    pub allowed_origin: Vec<String>,
    /// browser origins allowed to call /api/* cross-site (repeatable; '*' for any). Off by default.
    #[arg(long = "cors-origin")]
    pub cors_origin: Vec<String>,
    /// accepted Host headers (repeatable). Default: loopback names when bound to 127.0.0.1
    #[arg(long = "allowed-host")]
    pub allowed_host: Vec<String>,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// run worker + dashboard + API + MCP (HTTP)
    Serve(ServeArgs),
    /// run worker + MCP server over stdio
    Mcp {
        #[command(flatten)]
        common: Common,
    },
    /// import a JSONL chat log or a LoCoMo file
    Ingest {
        #[command(flatten)]
        common: Common,
        file: String,
        /// chat id for records without one
        #[arg(long = "chat-id")]
        chat_id: Option<String>,
        /// file is LoCoMo JSON (evaluation/locomo/locomo10.json)
        #[arg(long)]
        locomo: bool,
        /// LoCoMo conversation indices, e.g. 0,1
        #[arg(long)]
        only: Option<String>,
        /// drain the queue after ingesting
        #[arg(long)]
        process: bool,
    },
    /// drain the queue in the foreground
    Process {
        #[command(flatten)]
        common: Common,
        #[arg(long = "max-batches")]
        max_batches: Option<i64>,
    },
    /// search memories
    Search {
        #[command(flatten)]
        common: Common,
        query: String,
        #[arg(short = 'k', default_value_t = 15)]
        k: usize,
        #[arg(long)]
        subject: Option<String>,
        #[arg(long)]
        history: bool,
        #[arg(long)]
        json: bool,
    },
    /// prompt-ready context block
    Context {
        #[command(flatten)]
        common: Common,
        query: String,
        #[arg(short = 'k', default_value_t = 15)]
        k: usize,
        #[arg(long = "max-chars", default_value_t = 3600)]
        max_chars: usize,
    },
    /// what is known about a subject
    Profile {
        #[command(flatten)]
        common: Common,
        subject: String,
        #[arg(long)]
        json: bool,
    },
    /// counts, queue, tokens saved, grade
    Stats {
        #[command(flatten)]
        common: Common,
    },
    /// retract a memory
    Forget {
        #[command(flatten)]
        common: Common,
        memory_id: String,
        #[arg(long, default_value = "user request")]
        reason: String,
    },
    /// store an explicit memory now
    Remember {
        #[command(flatten)]
        common: Common,
        text: String,
        #[arg(long, default_value = "user")]
        subject: String,
        #[arg(long, default_value = "fact")]
        kind: String,
        #[arg(long)]
        when: Option<String>,
    },
    /// run one maintenance pass
    Maintain {
        #[command(flatten)]
        common: Common,
    },
}

impl Command {
    pub fn common(&self) -> &Common {
        match self {
            Command::Serve(ServeArgs { common, .. })
            | Command::Mcp { common }
            | Command::Ingest { common, .. }
            | Command::Process { common, .. }
            | Command::Search { common, .. }
            | Command::Context { common, .. }
            | Command::Profile { common, .. }
            | Command::Stats { common }
            | Command::Forget { common, .. }
            | Command::Remember { common, .. }
            | Command::Maintain { common } => common,
        }
    }
}

/// The configuration the flags describe.
pub fn build_config(args: &Common) -> ChatMemoryConfig {
    let mut cfg = ChatMemoryConfig {
        llm_model: args.model.clone(),
        llm_base_url: args.base_url.clone(),
        embed_model: args.embed_model.clone(),
        llm_max_parallel: args.parallel,
        debounce_seconds: args.debounce,
        ..Default::default()
    };
    cfg.worker.concurrency = if args.workers > 0 { args.workers } else { args.parallel.clamp(1, 4) };
    cfg.worker.batch_size = args.batch_size;
    if let Some(name) = args.user_name.as_ref().filter(|n| !n.is_empty()) {
        cfg.extraction.speaker_names.insert("user".into(), name.clone());
    }
    if args.extract_assistant {
        cfg.extraction.extract_assistant = true;
    } else if args.no_extract_assistant {
        cfg.extraction.extract_assistant = false;
    }
    cfg
}

fn build(args: &Common) -> Result<Arc<ChatMemory>> {
    let llm: Option<Arc<dyn LlmClient>> =
        if args.fake_llm { Some(Arc::new(crate::testing::scripted_fake_llm())) } else { None };
    Ok(Arc::new(ChatMemory::open(&args.db, llm, build_config(args))?))
}

fn println_flush(s: &str) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{s}");
    let _ = out.flush();
}

/// A JSON value as an optional string (`null`/missing/empty -> None, other scalars via `str()`).
fn opt_text(v: Option<&Value>) -> Option<String> {
    match v {
        None | Some(Value::Null) => None,
        Some(x) if !py_truthy(x) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(x) => Some(py_str(x)),
    }
}

/// One LoCoMo turn: (chat_id, speaker, text, sent_at, message_id).
pub type LocomoMessage = (String, String, String, Option<String>, Option<String>);

/// Every turn of a LoCoMo-format file.
pub fn locomo_messages(path: &str, only: Option<&[i64]>) -> Result<Vec<LocomoMessage>> {
    let raw = std::fs::read_to_string(path)?;
    let data: Value = serde_json::from_str(&raw).map_err(|e| MemoryError::Other("JSONDecodeError", e.to_string()))?;
    let mut out = Vec::new();
    for (ci, conv) in data.as_array().cloned().unwrap_or_default().iter().enumerate() {
        if only.is_some_and(|o| !o.is_empty() && !o.contains(&(ci as i64))) {
            continue;
        }
        let c = conv.get("conversation").unwrap_or(conv);
        let mut i = 1;
        while let Some(Value::Array(session)) = c.get(format!("session_{i}")) {
            let when = c.get(format!("session_{i}_date_time")).and_then(Value::as_str).unwrap_or("");
            let dt = parse_datetime_flexible(Some(when)).map_err(|e| MemoryError::Value(e.0))?;
            for m in session {
                // LoCoMo turns may carry a shared image; its caption is often the only place a fact lives
                let mut text = opt_text(m.get("text")).unwrap_or_default();
                if let Some(cap) = opt_text(m.get("blip_caption")) {
                    text = crate::pycompat::py_strip(&format!("{text} [shared image: {cap}]")).to_string();
                }
                let speaker = match m.get("speaker") {
                    None => "Unknown".to_string(),
                    Some(v) => py_str(v),
                };
                let dia = opt_text(m.get("dia_id"));
                out.push((
                    format!("locomo-{ci}"),
                    speaker,
                    text,
                    dt.as_ref().map(|d| d.isoformat()),
                    dia.map(|d| format!("locomo-{ci}-{d}")),
                ));
            }
            i += 1;
        }
    }
    Ok(out)
}

async fn cmd_serve(args: &ServeArgs) -> Result<i32> {
    let (host, port) = (args.host.as_str(), args.port);
    let cm = build(&args.common)?;
    cm.start().await?;
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    let mcp_token = args.mcp_token.clone().filter(|t| !t.is_empty()).or_else(|| env("NEURALGRAPH_MCP_TOKEN"));
    let mut api_token = args.api_token.clone().filter(|t| !t.is_empty()).or_else(|| env("NEURALGRAPH_API_TOKEN"));
    if api_token.is_none() && !["127.0.0.1", "localhost", "::1"].contains(&host) {
        if let Some(t) = &mcp_token {
            api_token = Some(t.clone()); // never expose the REST surface unauthenticated when /mcp is protected
            println_flush("note: /api/* is protected with the MCP token (set --api-token to use a different one)");
        } else {
            println_flush(
                "WARNING: serving on a non-loopback address without --api-token/--mcp-token: the API is open to the network",
            );
        }
    }
    let none_if_empty = |v: Vec<String>| if v.is_empty() { None } else { Some(v) };
    let opts = ServerOptions {
        mcp_token,
        api_token,
        allowed_origins: none_if_empty(args.allowed_origin.clone()),
        cors_origins: none_if_empty(args.cors_origin.clone()),
        allowed_hosts: none_if_empty(args.allowed_host.clone()),
    };
    let runner = run_server(cm.clone(), host, port, opts).await?;
    println_flush(&format!(
        "dashboard: http://{host}:{port}/   MCP: http://{host}:{port}/mcp   db: {}",
        cm.store.db_path
    ));
    shutdown_signal().await;
    println_flush("shutting down…");
    runner.cleanup().await;
    cm.close().await;
    Ok(0)
}

/// Resolves on SIGINT or SIGTERM.
pub async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).expect("SIGTERM handler");
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

async fn cmd_mcp(common: &Common) -> Result<i32> {
    let cm = build(common)?;
    cm.start().await?;
    let r = crate::mcp::serve_stdio(cm.clone()).await;
    cm.close().await;
    r?;
    Ok(0)
}

async fn cmd_ingest(
    common: &Common,
    file: &str,
    chat_id_flag: Option<&str>,
    locomo: bool,
    only: Option<&str>,
    process: bool,
) -> Result<i32> {
    let cm = build(common)?;
    let mut n = 0;
    if locomo {
        let only: Option<Vec<i64>> = match only.filter(|s| !s.is_empty()) {
            Some(s) => Some(
                s.split(',')
                    .map(|x| {
                        crate::pycompat::py_int_parse(x)
                            .ok_or_else(|| MemoryError::Value(format!("invalid literal for int() with base 10: '{x}'")))
                    })
                    .collect::<Result<Vec<i64>>>()?,
            ),
            None => None,
        };
        for (chat_id, speaker, text, sent_at, message_id) in locomo_messages(file, only.as_deref())? {
            cm.add_message(
                &chat_id,
                &speaker,
                &text,
                MessageInput { role: String::new(), sent_at, message_id, metadata: None },
            )?;
            n += 1;
        }
    } else {
        let raw = std::fs::read_to_string(file)?;
        for line in raw.lines() {
            let line = crate::pycompat::py_strip(line);
            if line.is_empty() {
                continue;
            }
            let rec: Value =
                serde_json::from_str(line).map_err(|e| MemoryError::Other("JSONDecodeError", e.to_string()))?;
            let chat_id = match opt_text(rec.get("chat_id")).or_else(|| chat_id_flag.map(str::to_string)) {
                Some(c) if !c.is_empty() => c,
                _ => {
                    eprintln!("record without chat_id and no --chat-id given");
                    return Ok(2);
                }
            };
            let speaker =
                opt_text(rec.get("speaker")).or_else(|| opt_text(rec.get("role"))).unwrap_or_else(|| "user".into());
            let text = match rec.get("text") {
                None => String::new(),
                Some(Value::String(s)) => s.clone(),
                Some(v) => py_str(v),
            };
            let input = MessageInput {
                role: opt_text(rec.get("role")).unwrap_or_default(),
                sent_at: opt_text(rec.get("sent_at")).or_else(|| opt_text(rec.get("timestamp"))),
                message_id: opt_text(rec.get("message_id")).or_else(|| opt_text(rec.get("id"))),
                metadata: rec.get("metadata").filter(|v| !v.is_null()).cloned(),
            };
            cm.add_message(&chat_id, &speaker, &text, input)?;
            n += 1;
        }
    }
    println_flush(&format!(
        "ingested {n} messages into {}; queue: {}",
        cm.store.db_path,
        py_repr_counts(&cm.store.job_counts()?)
    ));
    if process {
        let batches = cm.process_pending(None).await?;
        let mut m = cm.worker.metrics().as_value();
        m.as_object_mut().expect("object").insert("recent".into(), Value::Null);
        let text = dumps(&m, &JsonFormat::python_default());
        println_flush(&format!("processed {batches} batches: {}", py_prefix(&text, 400)));
    }
    cm.close().await;
    Ok(0)
}

async fn cmd_process(common: &Common, max_batches: Option<i64>) -> Result<i32> {
    let cm = build(common)?;
    let batches = cm.process_pending(max_batches).await?;
    let m = cm.worker.metrics();
    println_flush(&format!(
        "{batches} batches, {} messages processed, {} skipped, {} memories added, {} superseded, {} duplicates, {} relations, {} failures",
        m.messages_processed, m.messages_skipped, m.memories_added, m.memories_superseded, m.duplicates, m.relations, m.failures
    ));
    cm.close().await;
    Ok(0)
}

async fn cmd_search(
    common: &Common,
    query: &str,
    k: usize,
    subject: Option<String>,
    history: bool,
    json: bool,
) -> Result<i32> {
    let cm = build(common)?;
    let opts = SearchOptions { k, subject, include_superseded: history, ..Default::default() };
    let hits = cm.search(query, &opts).await?;
    if json {
        let v = Value::Array(hits.iter().map(|h| h.to_value()).collect());
        println_flush(&dumps(&v, &JsonFormat::pretty()));
    } else {
        for h in &hits {
            let m = &h.memory;
            let when = m.event_time.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| m.observed_at.clone());
            println_flush(&format!(
                "{}  [{}] {}   ({}; chat {}; {}; {})",
                crate::pycompat::py_fixed(h.score, 3),
                m.kind,
                m.text,
                m.subject_name,
                m.chat_id,
                py_prefix(&when, 10),
                h.explanation
            ));
        }
        if hits.is_empty() {
            println_flush("(no relevant memories)");
        }
    }
    cm.close().await;
    Ok(0)
}

async fn cmd_context(common: &Common, query: &str, k: usize, max_chars: usize) -> Result<i32> {
    let cm = build(common)?;
    let block = cm.context_for(query, Some(k), Some(max_chars), &SearchOptions::default()).await?;
    println_flush(if block.is_empty() { "(nothing relevant)" } else { &block });
    cm.close().await;
    Ok(0)
}

async fn cmd_profile(common: &Common, subject: &str, json: bool) -> Result<i32> {
    let cm = build(common)?;
    let prof = cm.profile(subject, 60)?;
    if json {
        println_flush(&dumps(&prof, &JsonFormat::pretty()));
    } else {
        let s = |v: &Value| v.as_str().map(str::to_string).unwrap_or_else(|| py_str(v));
        println_flush(&format!("{} — {} memories", s(&prof["name"]), prof["memory_count"]));
        if let Some(by_kind) = prof["memories_by_kind"].as_object() {
            for (kind, mems) in by_kind {
                println_flush(&format!("  {kind}:"));
                for m in mems.as_array().into_iter().flatten() {
                    let when = [m.get("event_time"), m.get("observed_at")]
                        .into_iter()
                        .flatten()
                        .find(|v| py_truthy(v))
                        .map(s)
                        .unwrap_or_default();
                    println_flush(&format!(
                        "    - {}  ({}, chat {})",
                        s(&m["text"]),
                        py_prefix(&when, 10),
                        s(&m["chat_id"])
                    ));
                }
            }
        }
        if let Some(rels) = prof["relations"].as_array().filter(|r| !r.is_empty()) {
            println_flush("  relations:");
            for r in rels {
                println_flush(&format!(
                    "    - {} —{}→ {}  (x{})",
                    s(&r["subject_id"]),
                    s(&r["predicate"]),
                    s(&r["object_id"]),
                    r["observation_count"]
                ));
            }
        }
    }
    cm.close().await;
    Ok(0)
}

async fn cmd_stats(common: &Common) -> Result<i32> {
    let cm = build(common)?;
    let mut st = cm.status()?;
    if let Some(o) = st.as_object_mut() {
        o.shift_remove("recent_memories");
        o.shift_remove("recent_events");
        o.shift_remove("top_entities");
    }
    println_flush(&dumps(&st, &JsonFormat::pretty()));
    cm.close().await;
    Ok(0)
}

async fn cmd_forget(common: &Common, memory_id: &str, reason: &str) -> Result<i32> {
    let cm = build(common)?;
    let ok = cm.retract(memory_id, reason)?;
    println_flush(if ok { "retracted" } else { "not found" });
    cm.close().await;
    Ok(if ok { 0 } else { 1 })
}

async fn cmd_remember(common: &Common, text: &str, subject: &str, kind: &str, when: Option<String>) -> Result<i32> {
    let cm = build(common)?;
    let opts = RememberOptions { subject: subject.into(), kind: kind.into(), when, ..Default::default() };
    let m = cm.remember(text, &opts).await?;
    println_flush(&format!("{}: {}", m.memory_id, m.text));
    cm.close().await;
    Ok(0)
}

async fn cmd_maintain(common: &Common) -> Result<i32> {
    let cm = build(common)?;
    let report = cm.maintain().await?;
    println_flush(&dumps(&report, &JsonFormat::python_default()));
    cm.close().await;
    Ok(0)
}

/// Run one parsed command; returns the process exit code.
pub async fn run(cli: Cli) -> Result<i32> {
    match cli.cmd {
        Command::Serve(args) => cmd_serve(&args).await,
        Command::Mcp { common } => cmd_mcp(&common).await,
        Command::Ingest { common, file, chat_id, locomo, only, process } => {
            cmd_ingest(&common, &file, chat_id.as_deref(), locomo, only.as_deref(), process).await
        }
        Command::Process { common, max_batches } => cmd_process(&common, max_batches).await,
        Command::Search { common, query, k, subject, history, json } => {
            cmd_search(&common, &query, k, subject, history, json).await
        }
        Command::Context { common, query, k, max_chars } => cmd_context(&common, &query, k, max_chars).await,
        Command::Profile { common, subject, json } => cmd_profile(&common, &subject, json).await,
        Command::Stats { common } => cmd_stats(&common).await,
        Command::Forget { common, memory_id, reason } => cmd_forget(&common, &memory_id, &reason).await,
        Command::Remember { common, text, subject, kind, when } => {
            cmd_remember(&common, &text, &subject, &kind, when).await
        }
        Command::Maintain { common } => cmd_maintain(&common).await,
    }
}
