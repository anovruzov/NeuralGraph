//! `call-center`: the memory pipeline on the synthetic call-center conversation (port of
//! `research/benchmarks/call_center.py`; the logic is `neuralgraph_research::benchmarks::call_center`). Answers and
//! embeddings come from the hard-coded Ollama server the script used; speaker facts from the `llm_backend` model.

use neuralgraph_research::benchmarks::call_center::{benchmark_path, output_path, run_benchmark, OllamaHttp};
use neuralgraph_research::llm_backend::HttpLlm;

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let mut stdout = std::io::stdout();
    let result =
        run_benchmark(&HttpLlm::new(), &OllamaHttp::default(), &benchmark_path(), &output_path(), &mut stdout).await;
    if let Err(e) = result {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
