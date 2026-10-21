//! Self-running, narrated demo of cross-chat memory: watch the worker learn while chats stream in.
//!
//! What it does
//! 1. Starts the memory system: background worker + dashboard + REST + MCP (Streamable HTTP) on one port.
//! 2. Replays five sample chats (`demo/data/sample_chats.jsonl`) with human pacing, so the dashboard animates: the
//!    avatar "learns", memories appear in the stream, entities and relations grow, the pentagon grade moves.
//! 3. Before each new chat it does what Claude would do through MCP: calls `memory_context` over the HTTP endpoint
//!    and prints what the assistant would know from the earlier chats. After the last chat it asks a few cross-chat
//!    questions through `memory_search`.
//! 4. Optionally saves dashboard screenshots along the way (`--screenshots`, needs a Chromium binary).
//! 5. Keeps serving at the end so you can click around and point Claude Code / Claude Desktop at it.
//!
//! ```text
//! # no model server needed (deterministic fake model)
//! cargo run --release --example chat_memory_live_demo -- --fake-llm
//!
//! # your local Qwen (Ollama) or LM Studio, configured through LLM_BASE_URL / LLM_MODEL / EMBED_MODEL
//! LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text \
//!     cargo run --release --example chat_memory_live_demo -- --user-name "Ali Novruzov"
//!
//! # faster / slower replay, extra volume from LoCoMo, exit when done
//! cargo run --release --example chat_memory_live_demo -- --fake-llm --pace 0.3 --locomo 0 --no-serve --screenshots
//! ```
//!
//! Then open http://127.0.0.1:8765/ (the demo prints the URL). MCP for Claude: http://127.0.0.1:8765/mcp

use clap::Parser;
use indexmap::IndexMap;
use neuralgraph_memory::llm::{FakeLlmClient, LlmClient};
use neuralgraph_memory::pycompat::{py_fixed, py_float_repr, py_len, py_prefix, py_repr_str};
use neuralgraph_memory::server::{run_server, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig};
use neuralgraph_memory::store::MemoryQuery;
use neuralgraph_memory::testing::scripted_fake_llm;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

const QUESTIONS: [&str; 6] = [
    "Where does Ali live now?",
    "What can't Ali eat?",
    "Who is Jonas?",
    "When is Ali's mother's birthday?",
    "What is Ali planning for the autumn?",
    "Tell me about Luna",
];

#[derive(Parser)]
#[command(about = "Self-running, narrated demo of cross-chat memory")]
struct Args {
    /// SQLite path (default: a fresh temp file)
    #[arg(long)]
    db: Option<String>,
    /// deterministic fake model, no server needed
    #[arg(long = "fake-llm")]
    fake_llm: bool,
    #[arg(long = "user-name", default_value = "Ali Novruzov")]
    user_name: String,
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8765)]
    port: u16,
    /// seconds between replayed messages
    #[arg(long, default_value_t = 1.2)]
    pace: f64,
    #[arg(long, default_value_t = 4)]
    parallel: usize,
    /// also stream this LoCoMo conversation (0-9) for volume
    #[arg(long)]
    locomo: Option<usize>,
    /// save dashboard screenshots to demo/results/chat_memory_demo
    #[arg(long)]
    screenshots: bool,
    /// exit when the story ends instead of keeping the server up
    #[arg(long = "no-serve")]
    no_serve: bool,
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn say(kind: &str, text: &str) {
    let prefix = match kind {
        "chat" => "\x1b[2m   chat\x1b[0m",
        "mem" => "\x1b[32m   memory\x1b[0m",
        "claude" => "\x1b[1;36m   claude\x1b[0m",
        "head" => "\n\x1b[1m===\x1b[0m",
        _ => "\x1b[33m   note\x1b[0m",
    };
    println!("{prefix} {text}");
}

fn load_sample() -> IndexMap<String, Vec<Value>> {
    let mut chats: IndexMap<String, Vec<Value>> = IndexMap::new();
    let raw = std::fs::read_to_string(root().join("demo/data/sample_chats.jsonl")).expect("sample chats");
    for line in raw.lines().filter(|l| !l.trim().is_empty()) {
        let rec: Value = serde_json::from_str(line).expect("jsonl");
        chats.entry(rec["chat_id"].as_str().unwrap_or("").to_string()).or_default().push(rec);
    }
    chats
}

fn load_locomo(index: usize) -> Vec<Value> {
    let path = root().join("research/datasets/locomo10.json");
    let found =
        neuralgraph_memory::cli::locomo_messages(path.to_str().unwrap_or(""), Some(&[index as i64])).expect("locomo");
    found
        .into_iter()
        .map(|(chat_id, speaker, text, sent_at, _)| json!({"chat_id": chat_id, "speaker": speaker, "text": text, "sent_at": sent_at}))
        .collect()
}

/// Minimal MCP Streamable HTTP client (what Claude does when it calls our tools).
struct McpClient {
    url: String,
    http: reqwest::Client,
    sid: Option<String>,
    n: i64,
    server: String,
}

impl McpClient {
    async fn connect(url: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let mut c = McpClient { url: url.into(), http: reqwest::Client::new(), sid: None, n: 0, server: String::new() };
        let r = c
            .rpc("initialize", json!({"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo", "version": "1"}}))
            .await?;
        c.http
            .post(&c.url)
            .headers(c.headers())
            .json(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            .send()
            .await?;
        c.server = r["serverInfo"]["name"].as_str().unwrap_or_default().to_string();
        Ok(c)
    }

    fn headers(&self) -> reqwest::header::HeaderMap {
        let mut h = reqwest::header::HeaderMap::new();
        h.insert("Accept", "application/json, text/event-stream".parse().expect("header"));
        if let Some(sid) = &self.sid {
            h.insert("Mcp-Session-Id", sid.parse().expect("header"));
        }
        h
    }

    async fn rpc(&mut self, method: &str, params: Value) -> Result<Value, Box<dyn std::error::Error>> {
        self.n += 1;
        let resp = self
            .http
            .post(&self.url)
            .headers(self.headers())
            .json(&json!({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}))
            .send()
            .await?;
        if let Some(sid) = resp.headers().get("Mcp-Session-Id") {
            self.sid = Some(sid.to_str()?.to_string());
        }
        let body: Value = resp.json().await?;
        if let Some(err) = body.get("error") {
            return Err(err.to_string().into());
        }
        Ok(body["result"].clone())
    }

    async fn call(&mut self, name: &str, args: Value) -> Result<Value, Box<dyn std::error::Error>> {
        let res = self.rpc("tools/call", json!({"name": name, "arguments": args})).await?;
        Ok(match res.get("structuredContent") {
            Some(v) if !v.is_null() => v.clone(),
            _ => json!({"text": res["content"][0]["text"]}),
        })
    }
}

fn find_chromium() -> Option<String> {
    let mut cands: Vec<String> = Vec::new();
    if let Ok(c) = std::env::var("CHROMIUM") {
        cands.push(c);
    }
    for name in ["chromium", "chromium-browser", "google-chrome"] {
        if let Some(paths) = std::env::var_os("PATH") {
            for dir in std::env::split_paths(&paths) {
                cands.push(dir.join(name).to_string_lossy().into_owned());
            }
        }
    }
    cands.push("/opt/pw-browsers/chromium".into());
    cands.push("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome".into());
    cands.into_iter().find(|c| Path::new(c).is_file())
}

/// Headless Chromium screenshot, run as a subprocess without blocking the server that renders the page.
async fn screenshot(chromium: Option<&str>, url: &str, path: &Path, height: u32) -> bool {
    let Some(chromium) = chromium else { return false };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let child = tokio::process::Command::new(chromium)
        .args([
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            &format!("--window-size=1280,{height}"),
            "--timeout=8000",
            &format!("--screenshot={}", path.display()),
            url,
        ])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    match tokio::time::timeout(Duration::from_secs(60), child).await {
        Ok(Ok(_)) => std::fs::metadata(path).map(|m| m.len() > 20_000).unwrap_or(false),
        _ => false,
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let out_dir = root().join("demo/results/chat_memory_demo");
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.8, llm_max_parallel: args.parallel, ..Default::default() };
    cfg.extraction.speaker_names.insert("user".into(), args.user_name.clone());
    cfg.worker.concurrency = args.parallel.min(4);
    // a little latency so the avatar visibly "learns"
    let llm: Option<Arc<dyn LlmClient>> =
        if args.fake_llm { Some(Arc::new(FakeLlmClient { latency: 0.6, ..scripted_fake_llm() })) } else { None };
    let tmp = tempfile::tempdir()?;
    let db =
        args.db.clone().unwrap_or_else(|| tmp.path().join("chat_memory_live_demo.db").to_string_lossy().into_owned());
    let cm = Arc::new(ChatMemory::open(&db, llm, cfg)?);
    cm.start().await?;
    let runner = run_server(cm.clone(), &args.host, args.port, ServerOptions::default()).await?;
    let base = format!("http://{}:{}", args.host, args.port);
    let chromium = if args.screenshots { find_chromium() } else { None };
    let mut shots = 0;

    macro_rules! shot {
        ($name:expr) => {
            if args.screenshots {
                // let the dashboard poll once more so the picture shows the latest state
                tokio::time::sleep(Duration::from_secs_f64(2.5)).await;
                let path = out_dir.join(format!("{:02}_{}.png", shots, $name));
                if screenshot(chromium.as_deref(), &format!("{base}/"), &path, 1500).await {
                    shots += 1;
                    say("note", &format!("screenshot saved: {}", path.display()));
                } else if chromium.is_none() {
                    say("note", "no Chromium binary found; set CHROMIUM=/path/to/chrome to enable screenshots");
                }
            }
        };
    }

    let model = if args.fake_llm { "fake model" } else { "real model via LLM_BASE_URL" };
    say("head", &format!("NeuralGraph cross-chat memory — live demo ({model})"));
    println!("   dashboard  \x1b[1m{base}/\x1b[0m\n   MCP (HTTP) {base}/mcp\n   db         {db}\n");
    say("note", "open the dashboard now and keep it visible; the story starts in 3 s");
    tokio::time::sleep(Duration::from_secs(3)).await;
    shot!("empty");

    let chats = load_sample();
    let mut mcp = McpClient::connect(&format!("{base}/mcp")).await?;
    say("note", &format!("connected to MCP server '{}' the way Claude would", mcp.server));
    let total = chats.len();
    for (n, (chat_id, msgs)) in chats.iter().enumerate() {
        let n = n + 1;
        say("head", &format!("chat {n}/{total}: {chat_id}"));
        if n > 1 {
            let first_user = msgs.iter().find(|m| m["role"] == "user").unwrap_or(&msgs[0])["text"]
                .as_str()
                .unwrap_or("")
                .to_string();
            let ctx = mcp.call("memory_context", json!({"query": first_user, "k": 6, "max_chars": 900})).await?;
            match ctx["context"].as_str().filter(|c| !c.is_empty()) {
                Some(block) => {
                    say("claude", "memory_context(...) before replying — what earlier chats taught me:");
                    for line in block.lines().skip(1) {
                        println!("            {line}");
                    }
                }
                None => say("claude", "memory_context(...) → nothing relevant yet"),
            }
        }
        for m in msgs {
            let speaker = m["speaker"].as_str().unwrap_or("");
            let who = if speaker == "user" { args.user_name.as_str() } else { speaker };
            let text = m["text"].as_str().unwrap_or("");
            let ell = if py_len(text) > 110 { "…" } else { "" };
            say("chat", &format!("{who}: {}{ell}", py_prefix(text, 110)));
            mcp.call(
                "memory_add_message",
                json!({"chat_id": chat_id, "speaker": speaker, "text": text, "role": m["role"].as_str().unwrap_or(""), "sent_at": m["sent_at"]}),
            )
            .await?;
            tokio::time::sleep(Duration::from_secs_f64(args.pace)).await;
        }
        // let the worker digest this chat and show what it learned
        let before: std::collections::HashSet<String> = cm
            .memories(&MemoryQuery { limit: Some(1000), ..Default::default() })?
            .into_iter()
            .map(|x| x.memory_id)
            .collect();
        cm.wait_until_idle(Some(120.0)).await?;
        for x in
            cm.memories(&MemoryQuery { limit: Some(1000), order: "created_at ASC".into(), ..Default::default() })?
        {
            if !before.contains(&x.memory_id) {
                let decision = x.metadata.get("decision").and_then(Value::as_str).unwrap_or("ADD");
                let tag = if decision == "ADD" { String::new() } else { format!(" ({})", decision.to_lowercase()) };
                say("mem", &format!("[{}] {}{tag}", x.kind, x.text));
            }
        }
        let st = mcp.call("memory_status", json!({})).await?;
        say(
            "note",
            &format!(
                "{} active memories, {} entities, {} relations · tokens saved {} · grade {}",
                st["memories"]["by_status"].get("active").cloned().unwrap_or(json!(0)),
                st["entities"],
                st["relations"].get("active").cloned().unwrap_or(json!(0)),
                st["tokens"]["tokens_saved"],
                st["grade"]["letter"].as_str().unwrap_or("")
            ),
        );
        shot!(format!("after_chat_{n}"));
    }

    if let Some(index) = args.locomo {
        say("head", &format!("volume: streaming LoCoMo conversation {index} as one more chat"));
        for m in load_locomo(index) {
            mcp.call(
                "memory_add_message",
                json!({"chat_id": m["chat_id"], "speaker": m["speaker"], "text": m["text"], "sent_at": m["sent_at"]}),
            )
            .await?;
            tokio::time::sleep(Duration::from_secs_f64(args.pace.min(0.15))).await;
        }
        cm.wait_until_idle(Some(600.0)).await?;
        shot!("after_locomo");
    }

    say("head", "questions that need more than one chat to answer");
    for q in QUESTIONS {
        let res = mcp.call("memory_search", json!({"query": q, "k": 3})).await?;
        say("claude", &format!("memory_search({})", py_repr_str(q)));
        for r in res["results"].as_array().into_iter().flatten() {
            println!(
                "            {}  {}   ← {} ({})",
                py_fixed(r["score"].as_f64().unwrap_or(0.0), 3),
                r["text"].as_str().unwrap_or(""),
                r["chat_id"].as_str().unwrap_or(""),
                r["why"].as_str().unwrap_or("")
            );
        }
    }
    let prof = mcp.call("memory_profile", json!({"subject": args.user_name})).await?;
    let rels = prof["relations"].as_array().cloned().unwrap_or_default();
    say(
        "claude",
        &format!(
            "memory_profile({}) → {} memories, {} relations",
            py_repr_str(&args.user_name),
            prof["memory_count"],
            rels.len()
        ),
    );
    for r in rels.iter().take(8) {
        println!(
            "            {} —{}→ {}",
            r["subject_id"].as_str().unwrap_or(""),
            r["predicate"].as_str().unwrap_or(""),
            r["object_id"].as_str().unwrap_or("")
        );
    }
    say("claude", "memory_remember(\"Ali prefers dark roast coffee.\")");
    mcp.call(
        "memory_remember",
        json!({"text": "Ali prefers dark roast coffee.", "subject": args.user_name, "kind": "preference"}),
    )
    .await?;
    let res = mcp.call("memory_search", json!({"query": "coffee", "k": 1})).await?;
    let recalled =
        res["results"].as_array().and_then(|r| r.first()).and_then(|r| r["text"].as_str()).unwrap_or("(nothing)");
    say("mem", &format!("recalled: {recalled}"));
    shot!("final");
    let _ = shots;

    let st = cm.status()?;
    say("head", "done");
    // printed like the Python dict it used to be: {'coverage': 100.0, ...}
    let axes = st["grade"]["axes"]
        .as_object()
        .map(|o| {
            let parts: Vec<String> = o
                .iter()
                .map(|(k, v)| format!("{}: {}", py_repr_str(k), py_float_repr(v.as_f64().unwrap_or(0.0))))
                .collect();
            format!("{{{}}}", parts.join(", "))
        })
        .unwrap_or_default();
    println!(
        "   grade {} ({}) · axes {}\n   tokens: {} raw → {} memory, {} saved\n   worker: {} batches, {} model calls, {} failures",
        st["grade"]["letter"].as_str().unwrap_or(""),
        st["grade"]["overall"],
        axes,
        st["tokens"]["raw_tokens_digested"],
        st["tokens"]["memory_tokens"],
        st["tokens"]["tokens_saved"],
        st["worker"]["batches"],
        st["llm"].get("generate_calls").cloned().unwrap_or(json!(0)),
        st["worker"]["failures"]
    );
    if args.no_serve {
        runner.cleanup().await;
        cm.close().await;
        if args.db.is_none() {
            let _ = tmp.keep();
        }
        return Ok(());
    }
    println!(
        "\n   still serving. Dashboard: {base}/   MCP: {base}/mcp\n   Claude Code:  claude mcp add --transport http neuralgraph-memory {base}/mcp\n   Ctrl-C to stop."
    );
    let _ = tokio::signal::ctrl_c().await;
    runner.cleanup().await;
    cm.close().await;
    if args.db.is_none() {
        let _ = tmp.keep();
    }
    Ok(())
}
