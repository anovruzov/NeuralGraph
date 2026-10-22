//! `retrieval-eval`: recall@10/@50/@all per retrieval variant on LoCoMo, no LLM calls (port of
//! `research/benchmarks/retrieval_eval.py`; the logic is `neuralgraph_research::benchmarks::retrieval_eval`).
//!
//! ```text
//! ONLY_CAT=single_hop ONLY_CONV=0,1 retrieval-eval
//! VARIANTS=embed,tesseract,tesseract+graph retrieval-eval
//! ```

use neuralgraph_research::benchmarks::retrieval_eval::{main_with, EvalConfig};
use neuralgraph_research::llm_backend::HttpLlm;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let result = async {
        let cfg = EvalConfig::from_env()?;
        let mut stdout = std::io::stdout();
        main_with(&cfg, &HttpLlm::new(), &mut stdout).await.map(|_| ())
    }
    .await;
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
