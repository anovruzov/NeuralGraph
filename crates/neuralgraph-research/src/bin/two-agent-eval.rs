//! `two-agent-eval`: two-agent memory probes on LoCoMo, retrieval only (port of
//! `research/benchmarks/two_agent_eval.py`; the logic is `neuralgraph_research::benchmarks::two_agent_eval`).
//!
//! ```text
//! MODE=split ONLY_CONV=0,1 OUT_JSON=/tmp/rows.json two-agent-eval
//! MODE=combined two-agent-eval
//! ```

use neuralgraph_research::benchmarks::two_agent_eval::{main_with, TwoAgentConfig};
use neuralgraph_research::llm_backend::HttpLlm;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let result = async {
        let cfg = TwoAgentConfig::from_env()?;
        let mut stdout = std::io::stdout();
        main_with(&cfg, &HttpLlm::new(), &mut stdout).await
    }
    .await;
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
