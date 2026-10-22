//! Reproduction of the published LoCoMo retrieval numbers from the Rust port, on the real (nomic) embeddings.
//!
//! These tests need the gitignored caches (`research/results/emb_cache`, or `EMB_CACHE_DIR`, and
//! `research/results/kg_cache`), so they are `#[ignore]`d and skip with a message when the caches are absent. Run
//! them in release mode:
//!
//! ```text
//! cargo test --release -p neuralgraph-research --test locomo_reproduction -- --ignored --nocapture
//! ```
//!
//! - `runner_retrieval_only_reproduces_committed_retrieval_metrics`: `RETRIEVAL_ONLY` runs of the runner in the
//!   configurations of the ten committed result files reproduce their `metadata.retrieval_metrics` exactly. With
//!   `PY_RO_DIR` pointing at the Python retrieval-only driver's reports (`py_ro_<name>.json`), every row is also
//!   compared with Python's (retrieved memories, modes, direct answers).
//! - `evaluator_tables_match_python`: `two-agent-eval` (combined and split) and `retrieval-eval` print the Python
//!   tables of `golden/locomo_tables.json` (see `gen_locomo_tables.py` for the two set-order-dependent lines). With
//!   `PY_BASELINE_DIR` holding the Python `OUT_JSON` rows (`combined_seed0.json`, `split_seed0.json`), the per-question
//!   rows are compared too.

mod common;

use indexmap::IndexSet;
use neuralgraph_research::benchmarks::pyharness::repo_root;
use neuralgraph_research::benchmarks::retrieval_eval::{self, EvalConfig};
use neuralgraph_research::benchmarks::runner::{run_benchmark, RunnerConfig};
use neuralgraph_research::benchmarks::two_agent_eval::{self as ta, TwoAgentConfig};
use neuralgraph_research::llm_backend::ScriptedLlm;
use serde_json::Value;
use std::path::PathBuf;
use std::time::Instant;

fn cache_dir() -> PathBuf {
    std::env::var("EMB_CACHE_DIR").map(PathBuf::from).unwrap_or_else(|_| repo_root().join("research/results/emb_cache"))
}

fn caches_present() -> bool {
    let ok = cache_dir().join("conv9_pairs2.json").exists()
        && cache_dir().join("conv9_questions_swapped.json").exists()
        && repo_root().join("research/results/kg_cache/conv9.json").exists();
    if !ok {
        eprintln!("SKIPPED: the embedding/KG caches are not present (see the module docs)");
    }
    ok
}

/// A model that fails every call: the retrieval-only paths must not make any.
fn no_model() -> ScriptedLlm {
    ScriptedLlm::new(|_| Err("model call in a retrieval-only run".into()))
        .with_embedder(|_| Err("model call in a retrieval-only run".into()))
}

fn load(path: &std::path::Path) -> Value {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))).unwrap()
}

/// (committed file, ONLY_CAT, ONLY_CONV, MAX_QUESTIONS, RETRIEVAL_MODE, KG)
const RUNS: [(&str, &str, &str, i64, &str, bool); 10] = [
    ("flat_single_hop", "single_hop", "", 2000, "flat", false),
    ("graph_single_hop", "single_hop", "", 2000, "graph", false),
    ("hybrid_single_hop", "single_hop", "", 2000, "hybrid", false),
    ("local_pairs_single_hop", "single_hop", "", 2000, "local_pairs", false),
    ("mega_single_hop_100", "single_hop", "", 100, "mega", false),
    ("mega_multi_hop_100", "multi_hop", "", 100, "mega", false),
    ("kg_single_hop_100", "single_hop", "", 100, "mega", true),
    ("kg_multi_hop_100", "multi_hop", "", 100, "mega", true),
    ("capstone_full", "", "0,1,2,3,4", 590, "local_pairs", false),
    ("capstone_rest", "", "4", 160, "local_pairs", false),
];

fn split_set(s: &str) -> IndexSet<String> {
    s.split(',').filter(|x| !x.is_empty()).map(str::to_string).collect()
}

/// Rows of a Rust retrieval-only report that differ from the Python driver's (`generated_answer` "x" there stands
/// for a model answer, empty here).
fn differing_rows(py: &Value, rs: &Value) -> Vec<(i64, Vec<String>)> {
    let mut out = Vec::new();
    for (p, r) in py["results"].as_array().unwrap().iter().zip(rs["results"].as_array().unwrap()) {
        let mut keys = Vec::new();
        for (k, v) in p.as_object().unwrap() {
            if k.ends_with("latency_ms") {
                continue;
            }
            let same = if k == "generated_answer" { v == &r[k] || (v == "x" && r[k] == "") } else { v == &r[k] };
            if !same {
                keys.push(k.clone());
            }
        }
        if !keys.is_empty() {
            out.push((p["id"].as_i64().unwrap(), keys));
        }
    }
    out
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "needs the gitignored embedding and KG caches; run with --release -- --ignored"]
async fn runner_retrieval_only_reproduces_committed_retrieval_metrics() {
    if !caches_present() {
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    let results = repo_root().join("research/results");
    let py_ro = std::env::var("PY_RO_DIR").ok().map(PathBuf::from);
    for (name, cats, convs, max, mode, kg) in RUNS {
        let mut cfg = RunnerConfig::from_env().unwrap().with_mode(mode);
        cfg.run_name = name.into();
        cfg.output_path = dir.path().join(format!("{name}.json"));
        cfg.only_cat = split_set(cats);
        cfg.only_conv = split_set(convs).iter().map(|c| c.parse().unwrap()).collect();
        cfg.max_questions = max;
        cfg.kg = kg;
        cfg.retrieval_only = true;
        cfg.use_speaker_profiles = false;
        cfg.emb_cache_dir = cache_dir();
        let start = Instant::now();
        let mut sink = Vec::new();
        run_benchmark(&cfg, &no_model(), &mut sink).await.unwrap();
        let elapsed = start.elapsed().as_secs_f64();
        let report = load(&cfg.output_path);
        let committed = load(&results.join(format!("{name}.json")));
        let got = &report["metadata"]["retrieval_metrics"];
        println!("{name}: {elapsed:.2}s {got}");
        assert_eq!(got, &committed["metadata"]["retrieval_metrics"], "retrieval_metrics of {name}");
        if let Some(d) = &py_ro {
            let py = load(&d.join(format!("py_ro_{name}.json")));
            let diff = differing_rows(&py, &report);
            println!("  rows differing from Python: {} of {}", diff.len(), py["results"].as_array().unwrap().len());
            // only mega search's graph channel depended on Python's set order (see retrieval::mega_search)
            if mode == "mega" {
                assert!(diff.iter().all(|(_, keys)| keys == &["retrieved_memories".to_string()]), "{name}: {diff:?}");
            } else {
                assert!(diff.is_empty(), "{name}: {diff:?}");
            }
        }
    }
}

fn drop_progress(text: &str) -> String {
    text.lines().filter(|l| !l.starts_with("conv ")).map(|l| format!("{l}\n")).collect()
}

/// Lines of `rust` that differ from `python`, which must all start with one of `allowed`.
fn assert_same_but(rust: &str, python: &str, allowed: &[&str], what: &str) -> Vec<String> {
    let (r, p): (Vec<&str>, Vec<&str>) = (rust.lines().collect(), python.lines().collect());
    assert_eq!(r.len(), p.len(), "{what}: line count");
    let mut differing = Vec::new();
    for (a, b) in r.iter().zip(&p) {
        if a != b {
            assert!(allowed.iter().any(|x| a.starts_with(x)), "{what}: rust {a:?} != python {b:?}");
            differing.push(format!("rust   {a}\npython {b}"));
        }
    }
    differing
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "needs the gitignored embedding and KG caches; run with --release -- --ignored"]
async fn evaluator_tables_match_python() {
    if !caches_present() {
        return;
    }
    let tables = common::load("locomo_tables.json");
    let dir = tempfile::tempdir().unwrap();
    let baseline = std::env::var("PY_BASELINE_DIR").ok().map(PathBuf::from);
    for mode in ["combined", "split"] {
        let cfg = TwoAgentConfig {
            mode: mode.into(),
            only_conv: IndexSet::new(),
            out_json: dir.path().join(format!("{mode}.json")).display().to_string(),
            cache_dir: cache_dir(),
            dataset: repo_root().join("research/datasets/locomo10.json"),
            kg_cache_dir: repo_root().join("research/results/kg_cache"),
        };
        let start = Instant::now();
        let mut out = Vec::new();
        ta::main_with(&cfg, &no_model(), &mut out).await.unwrap();
        println!("two-agent-eval MODE={mode}: {:.1}s", start.elapsed().as_secs_f64());
        let text = drop_progress(&String::from_utf8(out).unwrap());
        assert_eq!(text, tables[mode]["ordered"].as_str().unwrap(), "{mode} vs ordered-set Python");
        for d in assert_same_but(&text, tables[mode]["python"].as_str().unwrap(), &["KG: graph channel only"], mode) {
            println!("{d}");
        }
        if let Some(b) = &baseline {
            let py = load(&b.join(format!("{mode}_seed0.json")));
            let rs = load(std::path::Path::new(&cfg.out_json));
            for (key, rows) in py.as_object().unwrap() {
                let mut n = 0;
                for (p, r) in rows.as_array().unwrap().iter().zip(rs[key].as_array().unwrap()) {
                    if p != r {
                        n += 1;
                        for (k, v) in p.as_object().unwrap() {
                            assert!(v == &r[k] || k == "KG: graph channel only", "{key} row {p} differs at {k}");
                        }
                    }
                }
                println!(
                    "  {key}: {n} of {} rows differ from Python (KG graph channel only)",
                    rows.as_array().unwrap().len()
                );
            }
        }
    }
    let cfg = EvalConfig {
        cache_dir: cache_dir(),
        only_cat: IndexSet::new(),
        only_conv: IndexSet::new(),
        variants: vec!["embed".into(), "tesseract".into(), "tesseract+graph".into()],
        graph_seed_k: 10,
        graph_edge_decay: 0.8,
        dataset: repo_root().join("research/datasets/locomo10.json"),
    };
    let start = Instant::now();
    let mut out = Vec::new();
    retrieval_eval::main_with(&cfg, &no_model(), &mut out).await.unwrap();
    println!("retrieval-eval: {:.1}s", start.elapsed().as_secs_f64());
    let text = drop_progress(&String::from_utf8(out).unwrap());
    assert_eq!(text, tables["retrieval_eval"]["ordered"].as_str().unwrap(), "retrieval_eval vs ordered-set Python");
    let python = tables["retrieval_eval"]["python"].as_str().unwrap();
    for d in assert_same_but(&text, python, &["tesseract+graph"], "retrieval_eval") {
        println!("{d}");
    }
}
