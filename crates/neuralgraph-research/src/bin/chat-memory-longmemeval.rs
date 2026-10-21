//! `chat-memory-longmemeval`: LongMemEval on the shipped chat_memory pipeline, retrieval-only (port of
//! `research/benchmarks/chat_memory_longmemeval.py`; the logic is
//! `neuralgraph_research::benchmarks::chat_memory_longmemeval`).

use clap::Parser;
use neuralgraph_research::benchmarks::chat_memory_longmemeval::{main_async, Args};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let args = Args::parse();
    let mut stdout = std::io::stdout();
    // each question's ChatMemory talks to the configured model server, like the Python
    if let Err(e) = main_async(&args, &|| None, &mut stdout).await {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
