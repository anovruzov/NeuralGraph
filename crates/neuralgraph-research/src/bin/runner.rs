//! `runner`: the LoCoMo end-to-end benchmark (port of `research/benchmarks/runner.py`; the logic is
//! `neuralgraph_research::benchmarks::runner`, which documents the environment variables). Writes
//! `research/results/<RUN_NAME>.json`.
//!
//! ```text
//! RUN_NAME=flat_single_hop ONLY_CAT=single_hop RETRIEVAL_MODE=flat runner
//! RUN_NAME=x ONLY_CAT=single_hop RETRIEVAL_MODE=local_pairs RETRIEVAL_ONLY=1 runner   # no model calls
//! ```

use neuralgraph_research::benchmarks::runner::{run_benchmark, RunnerConfig};
use neuralgraph_research::llm_backend::HttpLlm;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let result = async {
        let cfg = RunnerConfig::from_env()?;
        let llm = HttpLlm::new();
        let mut stdout = std::io::stdout();
        run_benchmark(&cfg, &llm, &mut stdout).await
    }
    .await;
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
