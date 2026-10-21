//! Cross-chat memory demo.
//!
//! Feeds demo/data/sample_chats.jsonl (five chats, one person, several months) into the memory system, lets the
//! background worker digest them, then asks questions that span chats.
//!
//! ```text
//! # with a real local model (LM Studio or Ollama; see LLM_BASE_URL / LLM_MODEL / EMBED_MODEL)
//! LLM_BASE_URL=http://localhost:11434 LLM_MODEL=qwen2.5:7b-instruct EMBED_MODEL=nomic-embed-text \
//!     cargo run --release --example chat_memory_demo -- --user-name "Ali Novruzov"
//!
//! # without any server (deterministic scripted fake)
//! cargo run --release --example chat_memory_demo -- --fake-llm
//!
//! # keep the dashboard + MCP endpoint up after the demo
//! cargo run --release --example chat_memory_demo -- --fake-llm --serve
//! ```

use clap::Parser;
use neuralgraph_memory::llm::{FakeLlmClient, LlmClient};
use neuralgraph_memory::pycompat::{dumps, py_fixed, JsonFormat};
use neuralgraph_memory::retrieval::SearchOptions;
use neuralgraph_memory::server::{run_server, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, MessageInput};
use neuralgraph_memory::store::MemoryQuery;
use neuralgraph_memory::testing::scripted_fake_llm;
use serde_json::Value;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

const QUESTIONS: [&str; 6] = [
    "Where does Ali live now?",
    "What is Ali allergic to?",
    "Who is Jonas?",
    "When is Ali's mother's birthday?",
    "What are Ali's plans for the autumn?",
    "Tell me about Luna",
];

#[derive(Parser)]
struct Args {
    /// SQLite path (default: a temp file)
    #[arg(long)]
    db: Option<String>,
    #[arg(long = "fake-llm")]
    fake_llm: bool,
    #[arg(long = "user-name", default_value = "Ali Novruzov")]
    user_name: String,
    /// keep the dashboard at http://127.0.0.1:8765 running afterwards
    #[arg(long)]
    serve: bool,
    #[arg(long, default_value_t = 4)]
    parallel: usize,
}

fn sample_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../demo/data/sample_chats.jsonl")
}

/// `json.dumps(v, indent=1)`.
fn indent1(v: &Value) -> String {
    dumps(v, &JsonFormat { ensure_ascii: true, ..JsonFormat::pretty() })
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, llm_max_parallel: args.parallel, ..Default::default() };
    cfg.extraction.speaker_names.insert("user".into(), args.user_name.clone());
    cfg.worker.concurrency = args.parallel.min(4);
    let llm: Option<Arc<dyn LlmClient>> =
        if args.fake_llm { Some(Arc::new(FakeLlmClient { latency: 0.05, ..scripted_fake_llm() })) } else { None };
    let tmp = tempfile::tempdir()?;
    let db = args.db.clone().unwrap_or_else(|| tmp.path().join("chat_memory_demo.db").to_string_lossy().into_owned());
    let cm = Arc::new(ChatMemory::open(&db, llm, cfg)?);
    cm.start().await?;

    let t0 = Instant::now();
    let mut n = 0;
    for line in std::fs::read_to_string(sample_path())?.lines().filter(|l| !l.trim().is_empty()) {
        let rec: Value = serde_json::from_str(line)?;
        let input = MessageInput {
            role: rec["role"].as_str().unwrap_or("").into(),
            sent_at: rec["sent_at"].as_str().map(str::to_string),
            ..Default::default()
        };
        cm.add_message(
            rec["chat_id"].as_str().unwrap_or(""),
            rec["speaker"].as_str().unwrap_or(""),
            rec["text"].as_str().unwrap_or(""),
            input,
        )?;
        n += 1;
    }
    println!(
        "queued {n} messages in {:.0} ms (the chat path never waits for the model)",
        t0.elapsed().as_secs_f64() * 1000.0
    );
    println!("worker is digesting in the background ...");
    let t1 = Instant::now();
    while !cm.wait_until_idle(Some(5.0)).await? {
        let m = cm.worker.metrics();
        println!(
            "  {} batches, {} memories, {} relations, {} failures",
            m.batches, m.memories_added, m.relations, m.failures
        );
    }
    let m = cm.worker.metrics();
    println!(
        "digested in {:.1}s: {} processed, {} skipped, {} memories, {} superseded, {} duplicates, {} relations\n",
        t1.elapsed().as_secs_f64(),
        m.messages_processed,
        m.messages_skipped,
        m.memories_added,
        m.memories_superseded,
        m.duplicates,
        m.relations
    );

    println!("== memories ==");
    for mem in cm.memories(&MemoryQuery { limit: Some(100), order: "observed_at ASC".into(), ..Default::default() })? {
        let when = mem.event_time.as_ref().map(|t| format!(" [{t}]")).unwrap_or_default();
        println!("  ({}) {}{}  <- {}", mem.kind, mem.text, when, mem.chat_id);
    }
    println!("\n== relations ==");
    for e in cm.store.top_entities(5, None)? {
        for r in cm.store.relations_for(&e.entity_id, Some("active"), 8)? {
            println!(
                "  {} --{}--> {}  (x{}, {})",
                r.subject_id, r.predicate, r.object_id, r.observation_count, r.chat_id
            );
        }
    }
    println!("\n== questions across chats ==");
    for q in QUESTIONS {
        let hits = cm.search(q, &SearchOptions { k: 3, ..Default::default() }).await?;
        println!("Q: {q}");
        for h in hits {
            println!("   {} {}  ({}; {})", py_fixed(h.score, 3), h.memory.text, h.memory.chat_id, h.explanation);
        }
    }
    println!("\n== context block for a new chat ==");
    println!(
        "{}",
        cm.context_for("planning a trip and a dinner with the family", None, None, &SearchOptions::default()).await?
    );
    println!("\n== ledger & grade ==");
    println!("{}", indent1(&cm.token_ledger()?));
    println!("{}", indent1(&cm.grade()?));

    if args.serve {
        let runner = run_server(cm.clone(), "127.0.0.1", 8765, ServerOptions::default()).await?;
        println!("\ndashboard: http://127.0.0.1:8765/   MCP: http://127.0.0.1:8765/mcp   (Ctrl-C to stop)");
        let _ = tokio::signal::ctrl_c().await;
        runner.cleanup().await;
    }
    cm.close().await;
    println!("\ndb: {db}");
    if args.db.is_none() {
        // keep the temporary database for inspection, as the Python demo did (mkdtemp is never cleaned up)
        let _ = tmp.keep();
    }
    Ok(())
}
