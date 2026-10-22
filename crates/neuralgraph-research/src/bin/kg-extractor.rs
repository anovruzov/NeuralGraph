//! `kg-extractor [CONV_IDX ...]`: extract the LLM entity graph of LoCoMo conversations (port of
//! `python -m NeuralGraph.research.retrieval.kg_extractor`).
//!
//! All requested conversations run concurrently, each sequentially inside (the registry grows message by
//! message), against the configured LLM backend (`LLM_BASE_URL`, `LLM_MODEL`, ...). With no arguments every
//! conversation is extracted.
//!
//! The Python resolved its paths from its own location, `NeuralGraph/research/`: it read
//! `NeuralGraph/research/evaluation/locomo/locomo10.json` (a path that no longer existed, so it failed with
//! `FileNotFoundError`) and wrote `NeuralGraph/research/demo/results/kg_cache/conv<i>.json`, while the harnesses
//! read `research/results/kg_cache/`. Those stale paths remain the defaults so the behaviour is unchanged;
//! `--dataset` and `--cache-dir` point the tool where the data and the harnesses actually are, e.g.
//! `kg-extractor --dataset research/datasets/locomo10.json --cache-dir research/results/kg_cache 0 1`.

use clap::Parser;
use neuralgraph_research::llm_backend::HttpLlm;
use neuralgraph_research::retrieval::kg_extractor::{extract_conversation, flatten};
use serde_json::Value;
use std::collections::BTreeSet;
use std::path::PathBuf;

/// `Path(kg_extractor.__file__).resolve().parent.parent`
fn python_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../NeuralGraph/research")
}

#[derive(Parser)]
#[command(name = "kg-extractor", about = "Extract the LLM entity graph of LoCoMo conversations into kg_cache/")]
struct Args {
    /// Conversation indices to extract (default: all).
    #[arg(allow_negative_numbers = true)]
    conversations: Vec<String>,
    /// LoCoMo dataset (default: the Python's `NeuralGraph/research/evaluation/locomo/locomo10.json`).
    #[arg(long)]
    dataset: Option<PathBuf>,
    /// Output directory (default: the Python's `NeuralGraph/research/demo/results/kg_cache`).
    #[arg(long)]
    cache_dir: Option<PathBuf>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), String> {
    let args = Args::parse();
    let root = python_root();
    let dataset = args.dataset.unwrap_or_else(|| root.join("evaluation").join("locomo").join("locomo10.json"));
    let cache_dir = args.cache_dir.unwrap_or_else(|| root.join("demo").join("results").join("kg_cache"));
    let text = std::fs::read_to_string(&dataset).map_err(|e| {
        format!("FileNotFoundError: [Errno 2] No such file or directory: '{}' ({e})", dataset.display())
    })?;
    let data: Value = serde_json::from_str(&text).map_err(|e| format!("JSONDecodeError: {e}"))?;
    let convs = data.as_array().ok_or("dataset is not a list")?;
    let only: BTreeSet<i64> = if args.conversations.is_empty() {
        (0..convs.len() as i64).collect()
    } else {
        args.conversations
            .iter()
            .map(|x| {
                neuralgraph_memory::pycompat::py_int_parse(x)
                    .ok_or_else(|| format!("ValueError: invalid literal for int() with base 10: '{x}'"))
            })
            .collect::<Result<_, _>>()?
    };

    let llm = HttpLlm::new();
    let jobs = convs.iter().enumerate().filter(|(ci, _)| only.contains(&(*ci as i64))).map(|(ci, conv)| {
        let llm = &llm;
        let cache = cache_dir.join(format!("conv{ci}.json"));
        async move {
            let msgs = flatten(conv);
            let speakers: Vec<String> =
                msgs.iter().map(|m| m.speaker.clone()).collect::<BTreeSet<_>>().into_iter().collect();
            let res = extract_conversation(llm, &msgs, &speakers, Some(&cache), 120, 50).await?;
            let n_tr: usize = res["messages"]
                .as_array()
                .map(|ms| ms.iter().map(|m| m["triples"].as_array().map_or(0, Vec::len)).sum())
                .unwrap_or(0);
            let n_ent = res["registry"].as_object().map_or(0, |r| r.len());
            println!("conv {ci}: {} msgs -> {n_ent} entities, {n_tr} triples", msgs.len());
            Ok::<(), String>(())
        }
    });
    for r in futures::future::join_all(jobs).await {
        r?;
    }
    Ok(())
}
