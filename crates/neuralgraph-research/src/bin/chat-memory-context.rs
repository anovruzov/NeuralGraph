//! `chat-memory-context`: dump the context block the shipped pipeline builds for each question of databases already
//! built by `chat-memory-longmemeval` / `chat-memory-locomo ingest` (the logic is
//! `neuralgraph_research::benchmarks::chat_memory_context`).

use clap::Parser;
use neuralgraph_research::benchmarks::chat_memory_context::{run, Cli};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let cli = Cli::parse();
    let mut stdout = std::io::stdout();
    if let Err(e) = run(&cli, &mut stdout).await {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
