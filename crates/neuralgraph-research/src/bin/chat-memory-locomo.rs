//! `chat-memory-locomo`: retrieval-only LoCoMo harness for the shipped chat_memory pipeline (port of
//! `research/benchmarks/chat_memory_locomo.py`; the logic is `neuralgraph_research::benchmarks::chat_memory_locomo`).

use clap::Parser;
use neuralgraph_research::benchmarks::chat_memory_locomo::{main_with, Cli};

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let cli = Cli::parse();
    let mut stdout = std::io::stdout();
    if let Err(e) = main_with(cli, &mut stdout).await {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
