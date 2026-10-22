//! Port of `research/benchmarks/runner.py`: the LoCoMo end-to-end benchmark (binary `runner`). Ingests every
//! conversation into the Tesseract engine, retrieves for each question, reranks with the LLM, answers, judges, and
//! writes `research/results/<RUN_NAME>.json` with the retrieved memories, per-category accuracy, latency statistics
//! and the retrieval metrics (gold-substring recall@10/@50 of the candidate window).
//!
//! Environment (read once at start, like the Python module constants):
//! `RUN_NAME` (default `omega`), `ONLY_CAT`, `ONLY_CONV`, `MAX_QUESTIONS` (2000), `GRAPH_EXPANSION`, `GRAPH_SEED_K`
//! (10), `GRAPH_EDGE_DECAY` (0.8), `USE_SPEAKER_PROFILES`, `RETRIEVAL_MODE` (`flat` | `graph` | `hybrid` |
//! `local_pairs` | `mega`; default `graph` when `GRAPH_EXPANSION=1`, else `flat`), `EXTRA_CANDIDATES` (30),
//! `SPEAKER_BOOST` (1.5), `OPEN_DOMAIN_KEEP_CONTEXT`, `OPEN_DOMAIN_FORCE_INFER` (0/1/2), `OPEN_DOMAIN_INFER_WORLD`,
//! `KG` (mega mode: load `research/results/kg_cache/conv<i>.json`), `OPENAI_API_KEY` (judge through OpenAI when set,
//! else the local model judges), `JUDGE_MODEL` (`gpt-4o`), and the `llm_backend` variables (`LLM_BASE_URL`,
//! `LLM_MODEL`, `EMBED_MODEL`, ...).
//!
//! **Rust addition: `RETRIEVAL_ONLY=1`.** No model calls at all: message, question and pair embeddings come from
//! the cache of the retrieval-only evaluators (`EMB_CACHE_DIR`, default `research/results/emb_cache`:
//! `conv<i>_msgs`, `conv<i>_questions_all` aligned with `conv["qa"]`, `conv<i>_pairs2`), reranking is the identity
//! (the first `limit` candidates of the rerank window), and no answer is generated or judged: the deterministic
//! extractors (resolved dates, durations, counts, relationship status) still fill `generated_answer` when they fire,
//! otherwise it is empty, and `correct` is false. `metadata.retrieval_metrics` is exactly what a full run reports,
//! since it is computed before reranking. The report goes to `research/results/scratch/<RUN_NAME>.json` so a
//! retrieval-only run never overwrites a committed result file, and its metadata ends with `"retrieval_only": true`.
//! It cannot be combined with `USE_SPEAKER_PROFILES=1` (profiles need the model). This mode replaces the
//! monkeypatching driver that reproduced the committed retrieval metrics from Python.
//!
//! Python behaviour kept, and where the port is deterministic where Python was not:
//! - `cosine_topk` cached its normalised matrix under `id(nodes)`. CPython can hand the next conversation's node
//!   list the same id, so the cached matrix of the previous conversation was used against the new list (in a hybrid
//!   run that raised `IndexError`; when the sizes matched it silently ranked with the wrong embeddings). The port
//!   keys the cache by the node ids themselves, the intended behaviour; the committed hybrid numbers are reproduced
//!   with it.
//! - `np.argsort(-sims)` does not order exactly equal similarities stably, and on arm64 numpy 2.x its tie order is
//!   not the classic introsort's either; ties keep list order here. Equal similarities come from identical texts
//!   (a few duplicated messages in conversations 6 and 7), so the content of the ranking is the same either way.
//! - The storage's neighbour lists were Python `set`s of random uuid edge ids, so `expand_via_graph` met equally
//!   charged neighbours in a different order on every run. The port uses insertion order. The graph extras sit
//!   after the flat top 50 in `graph` mode, so neither the retrieval metrics nor a retrieval-only report depend on
//!   it; only the LLM reranker saw them.
//! - The message `entities` metadata was `list(set(...))` (hash order); here first-occurrence order. Nothing reads
//!   it.
//! - `ROUTING_SAMPLES` / `INFERENTIAL_SAMPLES` were filled from the unseeded `random` module and never printed or
//!   saved; they are kept (seeded from the clock) for completeness.
//! - With `USE_SPEAKER_PROFILES=1`, `query_single_hop` raised `ModuleNotFoundError` (see `speaker_profiles`) at the
//!   first single-hop question naming a speaker and the run aborted; the port returns that error.
//! - `USE_QWEN_ORACLE` is `False`, so [`check_gold_in_memories_oracle`] is never called; it is ported anyway.

use super::pyharness::{json_indent, perf_counter, repo_root, HResult, HarnessError, PyNum};
use crate::llm_backend::{backend_for, embed_model, llm_base_url, llm_model, GenerateOptions, Llm};
use crate::numpy::{argsort_desc_f32, normalized_f32_eps, normalized_rows_f32, sgemv, to_f32};
use crate::pyrandom::PyRandom;
use crate::pytime::PyDateTime;
use crate::retrieval::answering::{generate_answer, get_embedding, AnsweringConfig};
use crate::retrieval::data_types::{generate_edge_id, node_ref, EdgeType, NeuralEdge, NeuralNode, NodeLayer, NodeRef};
use crate::retrieval::dialogue_linker::DialogueLinker;
use crate::retrieval::llm_profile_extractor::extract_facts_parallel;
use crate::retrieval::mega_search::MegaIndex;
use crate::retrieval::pytext::{py_findall, py_json_loads, py_prefix_len, py_regex, py_str_value};
use crate::retrieval::reranker::{rerank_candidates_parallel, ParallelRerankOptions};
use crate::retrieval::speaker_profiles::UniversalSpeakerProfiler;
use crate::retrieval::storage::{Direction, InMemoryNeuralGraphStorage, NeuralGraphStorage};
use crate::retrieval::tesseract::{
    detect_list_question_universal, is_open_domain_world_query, looks_open_domain_question,
    should_use_open_domain_infer, Tesseract,
};
use crate::temporal_utils::{
    extract_duration_metadata, extract_explicit_date, generate_temporal_tokens, infer_query_mode,
    parse_datetime_flexible, resolve_relative_dates,
};
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{
    decimal_value, dumps, fnum, obj, py_int_parse, py_prefix, py_repr_str, py_round, py_str, py_strip, py_truthy,
    JsonFormat,
};
use serde_json::{Map, Value};
use std::cell::RefCell;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::LazyLock;

pub use crate::retrieval::service::{
    extract_count_answer, extract_duration_answer, extract_keywords, extract_month_year_from_text,
    extract_relationship_status, extract_topic_keywords_for_temporal, is_temporal_question,
};

/// Ranked `(node, charge)` candidates.
pub type Ranked = Vec<(NodeRef, f64)>;

pub const TOP_K: usize = 50;

/// `CATEGORIES`: LoCoMo category id -> name (category 5, adversarial, is skipped by the harnesses).
pub const CATEGORIES: [(i64, &str); 4] = [(1, "single_hop"), (2, "temporal"), (3, "open_domain"), (4, "multi_hop")];

/// `CATEGORIES.get(value)`: Python dict lookup, so `1.0` and `True` find category 1 as well.
pub fn category_name(value: &Value) -> Option<&'static str> {
    let id = match value {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.fract() == 0.0).map(|f| f as i64)),
        _ => None,
    }?;
    CATEGORIES.iter().find(|(k, _)| *k == id).map(|(_, v)| *v)
}

/// `USE_QWEN_ORACLE`: off for clean recall@k metrics (no LLM grading).
pub const USE_QWEN_ORACLE: bool = false;
/// `USE_SLM_RERANKER`.
pub const USE_SLM_RERANKER: bool = true;
/// `USE_OPENAI_ANSWER` (unused by the Python too: answers always come from the local model).
pub const USE_OPENAI_ANSWER: bool = false;
/// The OpenAI chat endpoint of the judge.
pub const OPENAI_CHAT_URL: &str = "https://api.openai.com/v1/chat/completions";

/// The five retrieval modes (anything else behaves like `flat`, as in Python).
fn is_local_mode(mode: &str) -> bool {
    mode == "local_pairs" || mode == "mega"
}

// ============================================================================================================
// configuration
// ============================================================================================================

/// The module constants of `runner.py`, read from the environment.
#[derive(Clone, Debug, PartialEq)]
pub struct RunnerConfig {
    pub run_name: String,
    /// `OUTPUT_PATH`
    pub output_path: PathBuf,
    pub max_questions: i64,
    /// `ONLY_CAT` (a Python set: only membership is used)
    pub only_cat: IndexSet<String>,
    pub only_conv: IndexSet<i64>,
    pub graph_expansion: bool,
    pub graph_seed_k: i64,
    pub graph_edge_decay: f64,
    pub use_speaker_profiles: bool,
    pub retrieval_mode: String,
    pub extra_candidates: i64,
    pub rerank_window: i64,
    pub speaker_boost: f64,
    pub open_domain_keep_context: bool,
    pub open_domain_force_infer: i64,
    pub open_domain_infer_world: bool,
    /// `os.environ.get("KG") == "1"` (read per conversation by the Python; constant for a run)
    pub kg: bool,
    pub openai_api_key: String,
    pub judge_model: String,
    /// `OLLAMA_MODEL` = `llm_backend.LLM_MODEL` (also `RERANKER_MODEL`)
    pub answer_model: String,
    /// `OLLAMA_BASE_URL` = `llm_backend.LLM_BASE_URL`
    pub base_url: String,
    /// Rust addition (see the module docs).
    pub retrieval_only: bool,
    pub emb_cache_dir: PathBuf,
    /// `research/datasets/locomo10.json`
    pub dataset: PathBuf,
    /// `research/results/kg_cache`
    pub kg_cache_dir: PathBuf,
}

fn env(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

/// `int(text)`, with the `ValueError` Python raised at import time.
pub fn env_int(text: &str) -> HResult<i64> {
    py_int_parse(text).ok_or_else(|| {
        HarnessError::new("ValueError", format!("invalid literal for int() with base 10: {}", py_repr_str(text)))
    })
}

/// `float(text)`.
pub fn env_float(text: &str) -> HResult<f64> {
    neuralgraph_memory::pycompat::py_float_parse(text).ok_or_else(|| {
        HarnessError::new("ValueError", format!("could not convert string to float: {}", py_repr_str(text)))
    })
}

/// `{int(c) for c in text.split(",") if c.strip()}`.
pub fn parse_int_set(text: &str) -> HResult<IndexSet<i64>> {
    text.split(',').filter(|c| !py_strip(c).is_empty()).map(env_int).collect()
}

/// `{c.strip() for c in text.split(",") if c.strip()}`.
pub fn parse_str_set(text: &str) -> IndexSet<String> {
    text.split(',').map(py_strip).filter(|c| !c.is_empty()).map(str::to_string).collect()
}

impl RunnerConfig {
    /// The configuration from the environment (Python read it at import; a malformed number was a `ValueError`).
    pub fn from_env() -> HResult<Self> {
        let root = repo_root();
        let run_name = env("RUN_NAME").unwrap_or_else(|| "omega".into());
        let graph_expansion = env("GRAPH_EXPANSION").as_deref() == Some("1");
        let retrieval_mode =
            env("RETRIEVAL_MODE").unwrap_or_else(|| if graph_expansion { "graph" } else { "flat" }.into());
        let extra_candidates = env_int(&env("EXTRA_CANDIDATES").unwrap_or_else(|| "30".into()))?;
        let retrieval_only = env("RETRIEVAL_ONLY").as_deref() == Some("1");
        let results = root.join("research").join("results");
        let output_dir = if retrieval_only { results.join("scratch") } else { results.clone() };
        let cfg = RunnerConfig {
            output_path: output_dir.join(format!("{run_name}.json")),
            run_name,
            max_questions: env_int(&env("MAX_QUESTIONS").unwrap_or_else(|| "2000".into()))?,
            only_cat: parse_str_set(&env("ONLY_CAT").unwrap_or_default()),
            only_conv: parse_int_set(&env("ONLY_CONV").unwrap_or_default())?,
            graph_expansion,
            graph_seed_k: env_int(&env("GRAPH_SEED_K").unwrap_or_else(|| "10".into()))?,
            graph_edge_decay: env_float(&env("GRAPH_EDGE_DECAY").unwrap_or_else(|| "0.8".into()))?,
            use_speaker_profiles: env("USE_SPEAKER_PROFILES").as_deref() == Some("1"),
            rerank_window: TOP_K as i64 + if is_rerank_extended(&retrieval_mode) { extra_candidates } else { 0 },
            retrieval_mode,
            extra_candidates,
            speaker_boost: env_float(&env("SPEAKER_BOOST").unwrap_or_else(|| "1.5".into()))?,
            open_domain_keep_context: env("OPEN_DOMAIN_KEEP_CONTEXT").as_deref() == Some("1"),
            open_domain_force_infer: env_int(&env("OPEN_DOMAIN_FORCE_INFER").unwrap_or_else(|| "0".into()))?,
            open_domain_infer_world: env("OPEN_DOMAIN_INFER_WORLD").as_deref() == Some("1"),
            kg: env("KG").as_deref() == Some("1"),
            openai_api_key: env("OPENAI_API_KEY").unwrap_or_default(),
            judge_model: env("JUDGE_MODEL").unwrap_or_else(|| "gpt-4o".into()),
            answer_model: llm_model(),
            base_url: llm_base_url(),
            retrieval_only,
            emb_cache_dir: env("EMB_CACHE_DIR").map(PathBuf::from).unwrap_or_else(|| results.join("emb_cache")),
            dataset: root.join("research").join("datasets").join("locomo10.json"),
            kg_cache_dir: results.join("kg_cache"),
        };
        Ok(cfg)
    }

    /// `RERANK_WINDOW` for a mode and `EXTRA_CANDIDATES` (used when building a config by hand).
    pub fn with_mode(mut self, mode: &str) -> Self {
        self.retrieval_mode = mode.to_string();
        self.rerank_window = TOP_K as i64 + if is_rerank_extended(mode) { self.extra_candidates } else { 0 };
        self
    }

    /// `USE_OPENAI_JUDGE`: falls back to the local model as judge when no key is set.
    pub fn use_openai_judge(&self) -> bool {
        !self.openai_api_key.is_empty()
    }

    /// `RERANKER_MODEL`
    pub fn reranker_model(&self) -> &str {
        &self.answer_model
    }
}

/// Modes whose rerank window includes the appended candidates.
fn is_rerank_extended(mode: &str) -> bool {
    matches!(mode, "graph" | "hybrid" | "local_pairs" | "mega")
}

// ============================================================================================================
// metrics
// ============================================================================================================

/// One category's lists in `RETRIEVAL_METRICS`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CategoryMetrics {
    pub recall_at_10: Vec<i64>,
    pub recall_at_50: Vec<i64>,
    pub oracle_rank: Vec<i64>,
    /// never filled (kept from the Python)
    pub filter_drop_count: Vec<i64>,
    pub total_candidates: Vec<i64>,
}

/// `RETRIEVAL_METRICS`, one entry per category in `CATEGORIES` order.
pub fn new_retrieval_metrics() -> IndexMap<String, CategoryMetrics> {
    CATEGORIES.iter().map(|(_, c)| (c.to_string(), CategoryMetrics::default())).collect()
}

/// `check_gold_in_memories_substring(gold_answer, memories, top_k)`: whether a gold part (the gold answer split on
/// "," and " and ", parts longer than two characters) or the whole gold answer occurs in one of the first `top_k`
/// memory texts, and the 1-based rank of the first such memory. Memories are their texts (`mem["text"]`; the
/// Python's `str(mem)` fallback for non-dict memories was never used).
pub fn check_gold_in_memories_substring<S: AsRef<str>>(gold_answer: &Value, memories: &[S], top_k: i64) -> (bool, i64) {
    if !py_truthy(gold_answer) {
        return (false, 0);
    }
    let gold_lower = py_strip(&py_str(gold_answer).to_lowercase()).to_string();
    let gold_parts: Vec<String> = gold_lower
        .replace(',', "|")
        .replace(" and ", "|")
        .split('|')
        .map(|p| py_strip(p).to_string())
        .filter(|p| p.chars().count() > 2)
        .collect();
    let n = py_prefix_len(memories.len(), top_k);
    for (i, mem) in memories[..n].iter().enumerate() {
        let mem_text = mem.as_ref().to_lowercase();
        let rank = i as i64 + 1;
        if gold_parts.iter().any(|part| mem_text.contains(part.as_str())) {
            return (true, rank);
        }
        if mem_text.contains(gold_lower.as_str()) {
            return (true, rank);
        }
    }
    (false, 0)
}

/// The prompt of [`check_gold_in_memories_oracle`].
pub fn oracle_prompt(question: &str, gold_answer: &Value, speaker: &str, mem_text: &str) -> String {
    format!(
        "Does this memory contain evidence that could answer the question?\n\nQuestion: {question}\nExpected answer: {}\n\nMemory from [{speaker}]: {}\n\nReply with ONLY \"YES\" or \"NO\".\n- YES if the memory contains the answer or strong evidence for it\n- NO if the memory is unrelated or about a different person/topic",
        py_str(gold_answer),
        py_prefix(mem_text, 400)
    )
}

/// `check_gold_in_memories_oracle`: ask the model, memory by memory, whether it answers the question (a failed call
/// falls back to the substring test for that memory). Unused: `USE_QWEN_ORACLE` is off.
pub async fn check_gold_in_memories_oracle(
    llm: &dyn Llm,
    cfg: &RunnerConfig,
    question: &str,
    gold_answer: &Value,
    memories: &[(String, String)],
    top_k: i64,
) -> (bool, i64) {
    if !py_truthy(gold_answer) {
        return (false, 0);
    }
    let n = py_prefix_len(memories.len(), top_k);
    for (i, (mem_text, speaker)) in memories[..n].iter().enumerate() {
        let prompt = oracle_prompt(question, gold_answer, speaker, mem_text);
        let opts = GenerateOptions::default()
            .model(cfg.answer_model.clone())
            .base_url(cfg.base_url.clone())
            .temperature(0.0)
            .max_tokens(5)
            .timeout(10.0);
        match llm.generate(&prompt, &opts).await {
            Ok(resp) => {
                if resp.to_uppercase().contains("YES") {
                    return (true, i as i64 + 1);
                }
            }
            Err(_) => {
                if mem_text.to_lowercase().contains(py_str(gold_answer).to_lowercase().as_str()) {
                    return (true, i as i64 + 1);
                }
            }
        }
    }
    (false, 0)
}

/// `compute_retrieval_summary(metrics)`: per category with at least one question, recall@10/@50 in percent, their
/// difference, the mean rank of the found gold (an `int` 0 when nothing was found) and how often it was found.
pub fn compute_retrieval_summary(metrics: &IndexMap<String, CategoryMetrics>) -> Value {
    let mut summary = Map::new();
    for (cat, data) in metrics {
        if data.recall_at_10.is_empty() {
            continue;
        }
        let n = data.recall_at_10.len();
        let recall_10 = data.recall_at_10.iter().sum::<i64>() as f64 / n as f64 * 100.0;
        let recall_50 = data.recall_at_50.iter().sum::<i64>() as f64 / n as f64 * 100.0;
        let found: Vec<i64> = data.oracle_rank.iter().copied().filter(|r| *r > 0).collect();
        let avg_oracle_rank = if found.is_empty() {
            PyNum::Int(0)
        } else {
            PyNum::Float(found.iter().sum::<i64>() as f64 / found.len() as f64)
        };
        let rerank_gain = recall_50 - recall_10;
        summary.insert(
            cat.clone(),
            obj([
                ("count", Value::from(n)),
                ("recall_at_10", fnum(py_round(recall_10, 1))),
                ("recall_at_50", fnum(py_round(recall_50, 1))),
                ("rerank_gain", fnum(py_round(rerank_gain, 1))),
                ("avg_oracle_rank", avg_oracle_rank.round(1).to_value()),
                ("oracle_found_count", Value::from(found.len())),
            ]),
        );
    }
    Value::Object(summary)
}

/// `compute_latency_percentiles(latencies)` of `save_results`.
pub fn compute_latency_percentiles(latencies: &[f64]) -> Value {
    if latencies.is_empty() {
        return Value::Object(Map::new());
    }
    let mut sorted = latencies.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = sorted.len();
    let mut sum = 0.0;
    for x in latencies {
        sum += x;
    }
    let (min, max) = py_min_max(latencies);
    let at = |q: f64| sorted[((n as f64 * q) as usize).min(n - 1)];
    obj([
        ("count", Value::from(n)),
        ("mean_ms", fnum(py_round(sum / n as f64, 1))),
        ("min_ms", fnum(py_round(min, 1))),
        ("max_ms", fnum(py_round(max, 1))),
        ("p50_ms", fnum(py_round(sorted[n / 2], 1))),
        ("p90_ms", fnum(py_round(at(0.9), 1))),
        ("p95_ms", fnum(py_round(at(0.95), 1))),
        ("p99_ms", fnum(py_round(at(0.99), 1))),
    ])
}

/// `min(xs)`, `max(xs)` (first extreme wins).
fn py_min_max(xs: &[f64]) -> (f64, f64) {
    let mut min = xs[0];
    let mut max = xs[0];
    for &x in &xs[1..] {
        if x < min {
            min = x;
        }
        if x > max {
            max = x;
        }
    }
    (min, max)
}

/// `[r.get(key, 0) for r in results if r.get(key)]`
fn truthy_latencies(results: &[Value], key: &str) -> Vec<f64> {
    results.iter().filter_map(|r| r.get(key).filter(|v| py_truthy(v)).and_then(Value::as_f64)).collect()
}

/// Per-category `{"correct": c, "total": t}` of the run, in `CATEGORIES` order.
pub type CategoryStats = IndexMap<String, (i64, i64)>;

pub fn new_category_stats() -> CategoryStats {
    CATEGORIES.iter().map(|(_, c)| (c.to_string(), (0, 0))).collect()
}

/// The document `save_results` wrote (`timestamp` is `datetime.now().isoformat()`).
pub fn results_document(
    cfg: &RunnerConfig,
    results: &[Value],
    stats: &CategoryStats,
    metrics: &IndexMap<String, CategoryMetrics>,
    timestamp: &str,
) -> Value {
    let total_correct: i64 = stats.values().map(|s| s.0).sum();
    let total_questions: i64 = stats.values().map(|s| s.1).sum();
    let accuracy = if total_questions > 0 {
        PyNum::Float(100.0 * total_correct as f64 / total_questions as f64)
    } else {
        PyNum::Int(0)
    };
    let latency_stats = obj([
        ("retrieval", compute_latency_percentiles(&truthy_latencies(results, "retrieval_latency_ms"))),
        ("reranking", compute_latency_percentiles(&truthy_latencies(results, "rerank_latency_ms"))),
        ("answer_generation", compute_latency_percentiles(&truthy_latencies(results, "answer_latency_ms"))),
        ("end_to_end", compute_latency_percentiles(&truthy_latencies(results, "e2e_latency_ms"))),
    ]);
    let category_stats: Map<String, Value> = stats
        .iter()
        .map(|(cat, (correct, total))| {
            let acc =
                if *total > 0 { fnum(py_round(100.0 * *correct as f64 / *total as f64, 1)) } else { Value::from(0) };
            (cat.clone(), obj([("accuracy", acc), ("correct", Value::from(*correct)), ("total", Value::from(*total))]))
        })
        .collect();
    let mut metadata = obj([
        ("model", Value::from("Tesseract 4D Memory")),
        ("answer_model", Value::from(cfg.answer_model.clone())),
        ("reranker_model", Value::from(cfg.reranker_model())),
        ("judge", Value::from("GPT-4o (OpenAI)")),
        ("timestamp", Value::from(timestamp)),
        ("total_questions", Value::from(total_questions)),
        ("total_correct", Value::from(total_correct)),
        ("accuracy", accuracy.round(2).to_value()),
        ("latency_stats", latency_stats),
        ("category_stats", Value::Object(category_stats)),
        ("retrieval_metrics", compute_retrieval_summary(metrics)),
    ]);
    if cfg.retrieval_only {
        metadata.as_object_mut().expect("object").insert("retrieval_only".into(), Value::Bool(true));
    }
    obj([("metadata", metadata), ("results", Value::Array(results.to_vec()))])
}

/// `save_results(results, stats)`: `json.dump(output, f, indent=2)` to `OUTPUT_PATH`.
pub fn save_results(
    cfg: &RunnerConfig,
    results: &[Value],
    stats: &CategoryStats,
    metrics: &IndexMap<String, CategoryMetrics>,
) -> HResult<()> {
    let doc = results_document(cfg, results, stats, metrics, &PyDateTime::now_local().isoformat());
    std::fs::write(&cfg.output_path, dumps(&doc, &json_indent(2)))?;
    Ok(())
}

// ============================================================================================================
// judge
// ============================================================================================================

/// `ACCURACY_PROMPT`
pub const ACCURACY_PROMPT: &str = "
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like \"last Tuesday\" or \"next month\"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., \"May 7th\" vs \"7 May\"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as \"label\".
";

/// `ACCURACY_PROMPT.format(question=..., gold_answer=gold, generated_answer=...)`.
pub fn accuracy_prompt(question: &str, gold: &Value, generated: &str) -> String {
    crate::retrieval::pytext::py_format(
        ACCURACY_PROMPT,
        &[("question", question), ("gold_answer", &py_str(gold)), ("generated_answer", generated)],
    )
}

static JSON_OBJECT_RE: LazyLock<regex::Regex> = LazyLock::new(|| regex::Regex::new(r"\{[^}]+\}").unwrap());
static LABEL_RE: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r#"(?i)"label"[\s\x1c-\x1f]*:[\s\x1c-\x1f]*"(CORRECT|WRONG)""#).unwrap());

/// `str(obj.get("label", "")).strip().upper() == "CORRECT"`, or `None` where Python raised (`obj` not a dict).
fn label_is_correct(obj: &Value) -> Option<bool> {
    let map = obj.as_object()?;
    let label = map.get("label").map(py_str_value).unwrap_or_default();
    Some(py_strip(&label).to_uppercase() == "CORRECT")
}

/// `parse_judge_label(resp)`: strict JSON, then the first `{...}` in the text, then a `"label": "..."` regex;
/// `false` if nothing parses.
pub fn parse_judge_label(resp: &str) -> bool {
    if let Some(v) = py_json_loads(py_strip(resp)) {
        if let Some(b) = label_is_correct(&v) {
            return b;
        }
    }
    if let Some(m) = JSON_OBJECT_RE.find(resp) {
        if let Some(v) = py_json_loads(m.as_str()) {
            if let Some(b) = label_is_correct(&v) {
                return b;
            }
        }
    }
    if let Some(c) = LABEL_RE.captures(resp) {
        return c[1].to_uppercase() == "CORRECT";
    }
    false
}

/// The auto-pass of `judge_answer`: `Some(true)` for an exact or substring match of the gold answer, `Some(false)`
/// without a gold answer, `None` when the judge must be asked.
pub fn judge_auto_pass(generated: &str, gold: &Value) -> Option<bool> {
    let gen_lower = py_strip(&generated.to_lowercase()).to_string();
    let gold_lower = py_strip(&py_str(gold).to_lowercase()).to_string();
    if !gold_lower.is_empty() && gen_lower == gold_lower {
        return Some(true);
    }
    if !gold_lower.is_empty() && gen_lower.contains(gold_lower.as_str()) {
        return Some(true);
    }
    if gold_lower.is_empty() {
        return Some(false);
    }
    None
}

/// `judge_answer(session, question, generated, gold)`: the auto-pass, else the judge model on `ACCURACY_PROMPT`
/// (OpenAI `JUDGE_MODEL` when `OPENAI_API_KEY` is set, else the local model), parsed by [`parse_judge_label`]. A
/// failed call prints `Judge error: ...` and counts as wrong.
pub async fn judge_answer(
    llm: &dyn Llm,
    cfg: &RunnerConfig,
    question: &str,
    generated: &str,
    gold: &Value,
    out: &mut dyn Write,
) -> HResult<bool> {
    if let Some(b) = judge_auto_pass(generated, gold) {
        return Ok(b);
    }
    let prompt = accuracy_prompt(question, gold, generated);
    let resp = if cfg.use_openai_judge() {
        openai_judge(llm, cfg, &prompt).await
    } else {
        llm.generate(&prompt, &GenerateOptions::default().temperature(0.0).max_tokens(150).timeout(60.0)).await
    };
    match resp {
        Ok(text) => Ok(parse_judge_label(&text)),
        Err(e) => {
            writeln!(out, "Judge error: {e}")?;
            Ok(false)
        }
    }
}

/// The OpenAI chat call of the judge: `result.get("choices", [{}])[0].get("message", {}).get("content", "")`,
/// stripped (no status check: an error body has no `choices` and yields an empty reply). A model without an HTTP
/// client (tests) is asked through `generate` with the same parameters instead.
async fn openai_judge(llm: &dyn Llm, cfg: &RunnerConfig, prompt: &str) -> Result<String, String> {
    let Some(client) = llm.http() else {
        let opts = GenerateOptions::default().model(cfg.judge_model.clone()).temperature(0.0).max_tokens(150);
        return llm.generate(prompt, &opts.timeout(60.0)).await;
    };
    let body = serde_json::json!({
        "model": cfg.judge_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 150,
    });
    let resp = client
        .post(OPENAI_CHAT_URL)
        .header("Authorization", format!("Bearer {}", cfg.openai_api_key))
        .header("Content-Type", "application/json")
        .timeout(std::time::Duration::from_secs(60))
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let result: Value = resp.json().await.map_err(|e| e.to_string())?;
    let empty = Value::Object(Map::new());
    let choice = match result.get("choices") {
        None => empty.clone(),
        Some(Value::Array(a)) => a.first().cloned().ok_or("list index out of range")?,
        Some(other) => return Err(format!("'{}' object is not subscriptable", py_type(other))),
    };
    let content = choice.get("message").and_then(|m| m.get("content")).and_then(Value::as_str).unwrap_or("");
    Ok(py_strip(content).to_string())
}

fn py_type(v: &Value) -> &'static str {
    super::pyharness::py_type_name(v)
}

// ============================================================================================================
// embeddings
// ============================================================================================================

/// The batched OpenAI-style embeddings call of `embed_batch`: 64 texts per `POST /v1/embeddings`, failing on an
/// HTTP error status (`raise_for_status`).
pub async fn openai_embed_batch(client: &reqwest::Client, texts: &[String]) -> HResult<Vec<Vec<f64>>> {
    let base = llm_base_url();
    let mut out = Vec::with_capacity(texts.len());
    for chunk in texts.chunks(64) {
        let url = format!("{base}/v1/embeddings");
        let resp = client
            .post(&url)
            .timeout(std::time::Duration::from_secs(300))
            .json(&serde_json::json!({"model": embed_model(), "input": chunk}))
            .send()
            .await
            .map_err(|e| HarnessError::new("aiohttp.ClientError", e))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(HarnessError::new(
                "aiohttp.ClientResponseError",
                format!("{}, message='{}', url='{url}'", status.as_u16(), status.canonical_reason().unwrap_or("")),
            ));
        }
        let data: Value = resp.json().await.map_err(|e| HarnessError::new("aiohttp.ContentTypeError", e))?;
        let items =
            data.get("data").and_then(Value::as_array).ok_or_else(|| HarnessError::new("KeyError", "'data'"))?;
        for d in items {
            out.push(embedding_values(
                d.get("embedding").ok_or_else(|| HarnessError::new("KeyError", "'embedding'"))?,
            )?);
        }
    }
    Ok(out)
}

/// A JSON embedding as floats.
pub fn embedding_values(v: &Value) -> HResult<Vec<f64>> {
    v.as_array()
        .ok_or_else(|| HarnessError::new("TypeError", "embedding is not a list"))?
        .iter()
        .map(|x| x.as_f64().ok_or_else(|| HarnessError::new("TypeError", format!("not a number: {x}"))))
        .collect()
}

/// `embed_batch(http, texts)`: batched on OpenAI-style servers, one `get_embedding` per text otherwise (a failed
/// text then gets an empty vector). A model without an HTTP client (tests) embeds one text at a time.
pub async fn embed_batch(llm: &dyn Llm, texts: &[String]) -> HResult<Vec<Vec<f64>>> {
    if backend_for(&llm_base_url()) == "openai" {
        if let Some(client) = llm.http() {
            return openai_embed_batch(client, texts).await;
        }
    }
    let config = AnsweringConfig::default();
    let mut out = Vec::with_capacity(texts.len());
    for t in texts {
        out.push(get_embedding(llm, t, Some(&config)).await);
    }
    Ok(out)
}

// ============================================================================================================
// ingestion
// ============================================================================================================

/// One message of a flattened LoCoMo conversation.
#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub speaker: String,
    pub text: String,
    pub datetime: String,
}

fn str_or(v: Option<&Value>, default: &str) -> String {
    match v {
        None => default.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => py_str(other),
    }
}

/// The messages of every `session_<i>` in order, each with its session's date string (`flatten_messages`; the
/// runner inlined the same loop).
pub fn flatten_messages(conv: &Value) -> Vec<Message> {
    let conversation = conv.get("conversation").unwrap_or(conv);
    let mut messages = Vec::new();
    let mut i = 1;
    while let Some(session) = conversation.get(format!("session_{i}")) {
        let dt = str_or(conversation.get(format!("session_{i}_date_time")), "");
        for msg in session.as_array().map(Vec::as_slice).unwrap_or_default() {
            messages.push(Message {
                speaker: str_or(msg.get("speaker"), "Unknown"),
                text: str_or(msg.get("text"), ""),
                datetime: dt.clone(),
            });
        }
        i += 1;
    }
    messages
}

static CAPITALISED_WORD: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[A-Z][a-z]+\b"));

/// `set(re.findall(r"\b[A-Z][a-z]+\b", text))` minus `"I"`, in first-occurrence order (see the module docs).
pub fn content_entities(text: &str) -> Vec<String> {
    let mut seen: IndexSet<String> = py_findall(&CAPITALISED_WORD, text).into_iter().collect();
    seen.shift_remove("I");
    seen.into_iter().collect()
}

fn temporal_err(e: crate::temporal_utils::TemporalError) -> HarnessError {
    HarnessError::new("ValueError", e.0)
}

/// The message node both `runner.py` and `retrieval_eval.ingest` built: original text (no date tokens baked in),
/// the speaker and session date, keywords, capitalised entities, date tokens, and the resolved event date
/// (explicit date, else a single unambiguous relative date, else the message date) for answer-time reasoning.
pub fn build_message_node(conv_idx: usize, msg_idx: usize, msg: &Message, embedding: Vec<f64>) -> HResult<NeuralNode> {
    let keywords = extract_keywords(&msg.text);
    let entities = content_entities(&msg.text);
    let message_date = parse_datetime_flexible(Some(&msg.datetime)).map_err(temporal_err)?.map(|d| d.naive);
    let temporal = generate_temporal_tokens(message_date, &msg.text).map_err(temporal_err)?;
    let (_resolved_content, resolved_dates) = resolve_relative_dates(&msg.text, message_date).map_err(temporal_err)?;
    let explicit_date = extract_explicit_date(&msg.text, message_date).map_err(temporal_err)?;
    let duration = extract_duration_metadata(&msg.text, message_date);

    let mut resolved_date: Option<String> = None;
    let mut resolved_date_source: Option<&str> = None;
    if let Some(e) = &explicit_date {
        resolved_date = Some(e.clone());
        resolved_date_source = Some("explicit");
    } else if !resolved_dates.is_empty() {
        let unique: IndexSet<&String> =
            resolved_dates.values().filter(|v| v.chars().any(|c| decimal_value(c).is_some())).collect();
        if unique.len() == 1 {
            resolved_date = unique.first().map(|s| s.to_string());
            resolved_date_source = Some("relative");
        }
    }
    if resolved_date.as_deref().is_none_or(str::is_empty) {
        if let Some(d) = message_date {
            resolved_date = Some(d.format("%Y-%m-%d").to_string());
            resolved_date_source = Some("message");
        }
    }

    let date_tokens = Value::from(temporal.date_tokens.clone());
    let temporal_metadata = Value::Object(temporal.temporal_metadata.clone());
    let opt = |s: Option<String>| s.map(Value::String).unwrap_or(Value::Null);
    let resolved_dates_value: Map<String, Value> =
        resolved_dates.iter().map(|(k, v)| (k.clone(), Value::String(v.clone()))).collect();
    let metadata = obj([
        ("speaker", Value::String(msg.speaker.clone())),
        ("datetime", Value::String(msg.datetime.clone())),
        ("keywords", Value::from(keywords)),
        ("entities", Value::from(entities)),
        ("temporal_tokens", date_tokens.clone()),
        ("temporal_metadata", temporal_metadata.clone()),
        ("resolved_date", opt(resolved_date)),
        ("resolved_date_source", opt(resolved_date_source.map(str::to_string))),
        ("explicit_date", opt(explicit_date)),
        ("duration_years", duration.get("duration_years").cloned().unwrap_or(Value::Null)),
        ("duration_months", duration.get("duration_months").cloned().unwrap_or(Value::Null)),
        ("since_year", duration.get("since_year").cloned().unwrap_or(Value::Null)),
        ("since_date", duration.get("since_date").cloned().unwrap_or(Value::Null)),
        ("date_tokens", date_tokens),
        ("temporal", temporal_metadata),
        ("resolved_dates", Value::Object(resolved_dates_value)),
    ]);
    let mut node = NeuralNode::new(format!("msg_{conv_idx}_{msg_idx}"), NodeLayer::Message, msg.text.clone());
    node.session_key = format!("conv_{conv_idx}");
    node.embedding = Some(embedding);
    node.created_at = PyDateTime::now_local();
    node.metadata = match metadata {
        Value::Object(m) => m,
        _ => unreachable!("obj() builds an object"),
    };
    Ok(node)
}

/// ENTITY edges (same speaker, each message to that speaker's next three) and TEMPORAL edges (message to the next
/// message). Returns the two edge counts. `entity_edge_metadata` adds `{"speaker": ...}` (the runner did, the
/// retrieval evaluator did not).
pub fn add_conversation_edges(
    storage: &dyn NeuralGraphStorage,
    speaker_nodes: &IndexMap<String, Vec<String>>,
    all_node_ids: &[String],
    entity_edge_metadata: bool,
) -> (usize, usize) {
    let mut edge_count = 0;
    for (speaker, node_ids) in speaker_nodes {
        for (i, src_id) in node_ids.iter().enumerate() {
            for tgt_id in &node_ids[i + 1..(i + 4).min(node_ids.len())] {
                let mut edge = NeuralEdge::new(generate_edge_id(), src_id.clone(), tgt_id.clone(), EdgeType::Entity)
                    .with_weight(0.8);
                if entity_edge_metadata {
                    edge = edge.with_metadata(single_entry("speaker", Value::String(speaker.clone())));
                }
                storage.save_edge(&crate::retrieval::data_types::edge_ref(edge));
                edge_count += 1;
            }
        }
    }
    let mut temporal_edge_count = 0;
    for w in all_node_ids.windows(2) {
        let edge = NeuralEdge::new(generate_edge_id(), w[0].clone(), w[1].clone(), EdgeType::Temporal)
            .with_weight(0.85)
            .with_metadata(single_entry("direction", Value::from("before")));
        storage.save_edge(&crate::retrieval::data_types::edge_ref(edge));
        temporal_edge_count += 1;
    }
    (edge_count, temporal_edge_count)
}

fn single_entry(k: &str, v: Value) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert(k.to_string(), v);
    m
}

/// The text of a pair node: `"[prev speaker] prev\n[speaker] this"`.
pub fn pair_text(prev: &Message, this: &Message) -> String {
    format!("[{}] {}\n[{}] {}", prev.speaker, prev.text, this.speaker, this.text)
}

/// The pair-node texts of a conversation and their members (reply first), as `build_pair_nodes` lists them.
/// `Err` is the `IndexError` Python raised when a message had been dropped from `nodes` (no embedding).
pub fn pair_texts_and_members(messages: &[Message], nodes: &[NodeRef]) -> HResult<(Vec<String>, Vec<Vec<String>>)> {
    let mut texts = Vec::new();
    let mut members = Vec::new();
    for i in 1..messages.len() {
        texts.push(pair_text(&messages[i - 1], &messages[i]));
        let id = |j: usize| {
            nodes
                .get(j)
                .map(|n| n.borrow().node_id.clone())
                .ok_or_else(|| HarnessError::new("IndexError", "list index out of range"))
        };
        members.push(vec![id(i)?, id(i - 1)?]);
    }
    Ok((texts, members))
}

/// The pair nodes for given texts/embeddings/members (`zip` stops at the shortest). `anchor_of(k, members)` picks
/// the anchor (replier) node; the node id is `<prefix>_<conv_idx>_<k>`.
pub fn make_pair_nodes(
    id_prefix: &str,
    conv_idx: usize,
    texts: &[String],
    embs: Vec<Vec<f64>>,
    members: &[Vec<String>],
    anchor_of: impl Fn(usize, &[String]) -> HResult<NodeRef>,
) -> HResult<Vec<NodeRef>> {
    let mut out = Vec::new();
    for (k, ((text, emb), mem)) in texts.iter().zip(embs).zip(members).enumerate() {
        let anchor = anchor_of(k, mem)?;
        let (speaker, datetime) = {
            let a = anchor.borrow();
            (a.speaker_id(), a.metadata.get("datetime").cloned().unwrap_or(Value::Null))
        };
        let mut node = NeuralNode::new(format!("{id_prefix}_{conv_idx}_{k}"), NodeLayer::Message, text.clone());
        node.session_key = format!("conv_{conv_idx}");
        node.embedding = Some(emb);
        node.created_at = PyDateTime::now_local();
        let mut meta = Map::new();
        meta.insert("speaker".into(), Value::String(speaker));
        meta.insert("datetime".into(), datetime);
        meta.insert("pair_members".into(), Value::from(mem.clone()));
        node.metadata = meta;
        out.push(node_ref(node));
    }
    Ok(out)
}

/// `build_pair_nodes(http, conv_idx, messages, nodes)`: PAIR nodes `"[prev speaker] prev\n[speaker] this"`, embedded
/// as one text so a short reply is indexed together with the question it answers. Kept out of storage; metadata
/// `pair_members` maps back to the two message node ids (reply first); speaker = the replier. `embs` are the
/// texts' embeddings (`embed_batch`, or the cache in retrieval-only mode).
pub fn build_pair_nodes(
    conv_idx: usize,
    messages: &[Message],
    nodes: &[NodeRef],
    embed: impl FnOnce(&[String]) -> HResult<Vec<Vec<f64>>>,
) -> HResult<Vec<NodeRef>> {
    let (texts, members) = pair_texts_and_members(messages, nodes)?;
    let embs = embed(&texts)?;
    make_pair_nodes("pair", conv_idx, &texts, embs, &members, |k, _| {
        nodes.get(k + 1).cloned().ok_or_else(|| HarnessError::new("IndexError", "list index out of range"))
    })
}

/// `unpair(ranked, node_by_id, seen)`: expand pair hits into their member message nodes (reply first), skipping ids
/// already seen; message hits pass through. `Err` is the `KeyError` of an unknown member id.
pub fn unpair(
    ranked: &[(NodeRef, f64)],
    node_by_id: &IndexMap<String, NodeRef>,
    seen: &mut IndexSet<String>,
) -> HResult<Ranked> {
    let mut out = Vec::new();
    for (n, c) in ranked {
        let mids: Vec<String> = {
            let n = n.borrow();
            match n.metadata.get("pair_members").filter(|v| py_truthy(v)) {
                Some(Value::Array(ids)) => ids.iter().map(py_str).collect(),
                _ => vec![n.node_id.clone()],
            }
        };
        for mid in mids {
            if seen.insert(mid.clone()) {
                let node = node_by_id.get(&mid).ok_or_else(|| HarnessError::new("KeyError", py_repr_str(&mid)))?;
                out.push((node.clone(), *c));
            }
        }
    }
    Ok(out)
}

/// `cosine_topk(nodes, query_vec, k)`: pure-embedding top `k` by float32 cosine (numpy's arithmetic exactly), the
/// normalised matrix cached per node list (keyed by the node ids, see the module docs).
/// The node ids a normalised matrix was built from, and the matrix.
type CachedMatrix = (Vec<String>, Rc<Vec<f32>>);

#[derive(Default)]
pub struct CosineTopk {
    cache: RefCell<Option<CachedMatrix>>,
}

impl CosineTopk {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn topk(&self, nodes: &[NodeRef], query_vec: &[f64], k: usize) -> HResult<Ranked> {
        let key: Vec<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
        let cached = self.cache.borrow().as_ref().filter(|(ids, _)| *ids == key).map(|(_, m)| m.clone());
        let m = match cached {
            Some(m) => m,
            None => {
                let m = Rc::new(normalized_matrix(nodes)?);
                *self.cache.borrow_mut() = Some((key, m.clone()));
                m
            }
        };
        rank_by_cosine(nodes, &m, query_vec, k)
    }
}

/// `np.array([n.embedding for n in nodes], dtype=np.float32)` row-normalised (`/ (norm + 1e-9)`).
pub fn normalized_matrix(nodes: &[NodeRef]) -> HResult<Vec<f32>> {
    if nodes.is_empty() {
        return Err(HarnessError::new(
            "numpy.exceptions.AxisError",
            "axis 1 is out of bounds for array of dimension 1",
        ));
    }
    let rows: Vec<Vec<f64>> = nodes.iter().map(|n| n.borrow().embedding.clone().unwrap_or_default()).collect();
    let width = rows[0].len();
    if rows.iter().any(|r| r.len() != width) {
        return Err(HarnessError::new(
            "ValueError",
            "setting an array element with a sequence. The requested array has an inhomogeneous shape after 1 dimensions.",
        ));
    }
    let refs: Vec<&[f64]> = rows.iter().map(Vec::as_slice).collect();
    Ok(normalized_rows_f32(&refs, 1e-9).0)
}

/// The scoring half of `cosine_topk` over a prepared matrix.
pub fn rank_by_cosine(nodes: &[NodeRef], m: &[f32], query_vec: &[f64], k: usize) -> HResult<Ranked> {
    let q = normalized_f32_eps(&to_f32(query_vec), 1e-9);
    if m.len() != nodes.len() * q.len() {
        return Err(HarnessError::new(
            "ValueError",
            format!(
                "matmul: Input operand 1 has a mismatch in its core dimension 0 (size {} is different from {})",
                q.len(),
                m.len() / nodes.len().max(1)
            ),
        ));
    }
    let sims = sgemv(m, nodes.len(), &q);
    Ok(argsort_desc_f32(&sims).into_iter().take(k).map(|i| (nodes[i].clone(), sims[i] as f64)).collect())
}

/// `cosine_topk` without a cache (`retrieval_eval.cosine_topk`).
pub fn cosine_topk(nodes: &[NodeRef], query_vec: &[f64], k: usize) -> HResult<Ranked> {
    rank_by_cosine(nodes, &normalized_matrix(nodes)?, query_vec, k)
}

/// `expand_via_graph(storage, dialogue_linker, retrieved)`: the graph arm. The first `graph_seed_k` hits pull in
/// their dialogue links (0.85 x the seed charge) and their TEMPORAL/ENTITY neighbours (`edge_decay` x edge weight x
/// seed charge); the merged list (each node once, first charge kept) is sorted by charge.
pub fn expand_via_graph(
    storage: &dyn NeuralGraphStorage,
    dialogue_linker: &DialogueLinker,
    retrieved: &[(NodeRef, f64)],
    graph_seed_k: i64,
    edge_decay: f64,
) -> Ranked {
    let mut expanded: Ranked = Vec::new();
    let mut seen: IndexSet<String> = IndexSet::new();
    for (node, charge) in retrieved {
        let id = node.borrow().node_id.clone();
        if seen.insert(id) {
            expanded.push((node.clone(), *charge));
        }
    }
    for (node, charge) in &retrieved[..py_prefix_len(retrieved.len(), graph_seed_k)] {
        let node_id = node.borrow().node_id.clone();
        for (linked_id, _, _) in dialogue_linker.get_linked_messages(&node_id) {
            if seen.contains(&linked_id) {
                continue;
            }
            if let Some(linked) = storage.get_node(&linked_id) {
                expanded.push((linked, charge * 0.85));
                seen.insert(linked_id);
            }
        }
        for (nb, edge) in
            storage.get_neighbors(&node_id, Some(&[EdgeType::Temporal, EdgeType::Entity]), Direction::Both)
        {
            let nb_id = nb.borrow().node_id.clone();
            if seen.contains(&nb_id) {
                continue;
            }
            let w = edge.borrow().base_weight;
            expanded.push((nb, charge * edge_decay * w));
            seen.insert(nb_id);
        }
    }
    sort_by_charge_desc(&mut expanded);
    expanded
}

/// `list.sort(key=lambda x: x[1], reverse=True)`: stable, equal charges keep their order.
pub fn sort_by_charge_desc(xs: &mut Ranked) {
    xs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
}

// ============================================================================================================
// routing, context and direct answers
// ============================================================================================================

/// Speakers (lower-case) whose name occurs in the lower-cased question, in speaker order.
pub fn named_in<'a>(question: &str, speakers: impl IntoIterator<Item = &'a String>) -> Vec<String> {
    let q = question.to_lowercase();
    speakers.into_iter().filter(|sp| q.contains(sp.as_str())).cloned().collect()
}

/// The query-mode decision made before reranking: `(query_mode, list_question)`. Only the question text is used
/// (never the gold category): list detection, the marker router, the temporal predicate, open-domain world
/// questions that name no participant, and the H5 hedged-question override (`OPEN_DOMAIN_FORCE_INFER`).
pub fn route_question(question: &str, speakers: &[String], open_domain_force_infer: i64) -> (String, bool) {
    let (list_question, list_type) = detect_list_question_universal(question);
    let mut query_mode = infer_query_mode(question).to_string();
    if list_question {
        query_mode = if list_type == Some("aggregation") { "AGGREGATION" } else { "LIST" }.into();
    }
    if is_temporal_question(question) {
        query_mode = "TEMPORAL".into();
    } else if query_mode == "TEMPORAL" {
        query_mode = "INFERENTIAL".into();
    }
    let names_in_q = !named_in(question, speakers).is_empty();
    if is_open_domain_world_query(question) && !names_in_q {
        query_mode = "OPEN_DOMAIN_WORLD".into();
    }
    if open_domain_force_infer != 0
        && looks_open_domain_question(question)
        && (open_domain_force_infer >= 2
            || matches!(query_mode.as_str(), "STRICT" | "INFERENTIAL" | "OPEN_DOMAIN_WORLD"))
    {
        query_mode = "OPEN_DOMAIN_INFER".into();
    }
    (query_mode, list_question)
}

/// AGGREGATION/LIST questions get 30 memories (answers are scattered), the others 15.
pub fn num_memories_needed(query_mode: &str) -> usize {
    if matches!(query_mode, "AGGREGATION" | "LIST") {
        30
    } else {
        15
    }
}

/// The INFERENTIAL -> OPEN_DOMAIN_INFER upgrade made after reranking.
pub fn upgrade_query_mode(query_mode: &str, question: &str) -> String {
    if query_mode == "INFERENTIAL" && should_use_open_domain_infer(question) {
        "OPEN_DOMAIN_INFER".into()
    } else {
        query_mode.to_string()
    }
}

/// `(answer_mode, retry_mode)`: the prompt variant sent to the answer model and the one of the not-found retry.
pub fn answer_modes(query_mode: &str, keep_context: bool, infer_world: bool) -> (String, String) {
    let answer_mode = if query_mode == "OPEN_DOMAIN_WORLD" && keep_context {
        "OPEN_DOMAIN_WORLD_MEM".to_string()
    } else if query_mode == "OPEN_DOMAIN_INFER" && infer_world {
        "OPEN_DOMAIN_INFER_WORLD".to_string()
    } else {
        query_mode.to_string()
    };
    let retry_mode = if infer_world { "OPEN_DOMAIN_INFER_WORLD" } else { "OPEN_DOMAIN_INFER" };
    (answer_mode, retry_mode.to_string())
}

fn meta_get<'a>(node: &'a NeuralNode, key: &str) -> Option<&'a Value> {
    node.metadata.get(key)
}

/// What the answer step is given from the reranked memories.
#[derive(Clone, Debug, PartialEq)]
pub struct AnswerContext {
    /// one line (or two) per memory
    pub context_parts: Vec<String>,
    /// `retrieved_memories` of the result row
    pub retrieved_memories: Vec<Value>,
    /// `{"text", "metadata"}` per memory, for the direct extractors
    pub candidate_memories: Vec<Value>,
}

/// Build the context from the first `n` reranked memories. TEMPORAL questions see each memory's resolved dates
/// (`[MESSAGE_DATETIME=...] [RESOLVED_DATE=...] [RESOLVED_RELATIVE=...] [SPEAKER=...] text`), the others
/// `[speaker] text\n  (sent: datetime)`. The original text is used: the model must reason about relative dates.
pub fn build_answer_context(reranked: &[(NodeRef, f64)], n: usize, query_mode: &str) -> AnswerContext {
    let mut ctx =
        AnswerContext { context_parts: Vec::new(), retrieved_memories: Vec::new(), candidate_memories: Vec::new() };
    for (node, charge) in reranked.iter().take(n) {
        let node = node.borrow();
        let speaker = meta_get(&node, "speaker").cloned().unwrap_or_else(|| Value::from("Unknown"));
        let dt = meta_get(&node, "datetime").cloned().unwrap_or_else(|| Value::from(""));
        let original_content = node.content.clone();
        let resolved_date = meta_get(&node, "resolved_date").cloned().unwrap_or_else(|| Value::from(""));
        let resolved_relative = match meta_get(&node, "resolved_dates").filter(|v| py_truthy(v)) {
            Some(Value::Object(m)) => {
                m.iter().map(|(k, v)| format!("{k}->{}", py_str_value(v))).collect::<Vec<_>>().join(", ")
            }
            _ => String::new(),
        };
        if query_mode == "TEMPORAL" {
            ctx.context_parts.push(format!(
                "[MESSAGE_DATETIME={}] [RESOLVED_DATE={}] [RESOLVED_RELATIVE={resolved_relative}] [SPEAKER={}] {original_content}",
                py_str_value(&dt),
                py_str_value(&resolved_date),
                py_str_value(&speaker)
            ));
        } else {
            ctx.context_parts.push(format!(
                "[{}] {original_content}\n  (sent: {})",
                py_str_value(&speaker),
                py_str_value(&dt)
            ));
        }
        ctx.retrieved_memories.push(obj([
            ("speaker", speaker),
            ("text", Value::from(py_prefix(&original_content, 300))),
            ("datetime", dt),
            ("charge", fnum(py_round(*charge, 3))),
        ]));
        ctx.candidate_memories
            .push(obj([("text", Value::String(original_content)), ("metadata", Value::Object(node.metadata.clone()))]));
    }
    ctx
}

/// The answers taken straight from the memories instead of the model: for TEMPORAL questions a duration, else the
/// first explicitly or relatively resolved date of a memory matching the question's topic words and month/year; for
/// the others (except OPEN_DOMAIN_WORLD) a count of children or a relationship status. Span/list extraction is off
/// (it produced fragments).
pub fn extract_direct_answer(question: &str, query_mode: &str, candidate_memories: &[Value]) -> Option<String> {
    if query_mode == "TEMPORAL" {
        if let Some(d) = extract_duration_answer(question, candidate_memories).filter(|s| !s.is_empty()) {
            return Some(d);
        }
        let topic_keywords = extract_topic_keywords_for_temporal(question);
        let (query_month, query_year) = extract_month_year_from_text(question);
        for mem in candidate_memories {
            let meta = mem.get("metadata");
            let resolved_date = meta.and_then(|m| m.get("resolved_date"));
            let source = meta.and_then(|m| m.get("resolved_date_source")).and_then(Value::as_str);
            let Some(resolved_date) = resolved_date.filter(|v| py_truthy(v)) else { continue };
            if !matches!(source, Some("explicit" | "relative")) {
                continue;
            }
            let text_lower = mem.get("text").and_then(Value::as_str).unwrap_or("").to_lowercase();
            if !topic_keywords.is_empty() && !topic_keywords.iter().any(|k| text_lower.contains(k.as_str())) {
                continue;
            }
            let resolved = py_str(resolved_date);
            if query_month.is_some() || query_year.is_some() {
                let (mem_month, mem_year) = extract_month_year_from_text(&resolved);
                if query_month.is_some() && mem_month != query_month {
                    continue;
                }
                if query_year.is_some() && mem_year != query_year {
                    continue;
                }
            }
            return Some(resolved);
        }
        None
    } else if query_mode != "OPEN_DOMAIN_WORLD" {
        if let Some(c) = extract_count_answer(question, candidate_memories).filter(|s| !s.is_empty()) {
            return Some(c);
        }
        extract_relationship_status(question, candidate_memories).filter(|s| !s.is_empty())
    } else {
        None
    }
}

/// Phrases that mark a "not found" answer (triggering the open-domain inference retry).
pub const NOT_FOUND_PHRASES: [&str; 8] = [
    "not mentioned in the memories",
    "not found in the memories",
    "not in the memories",
    "no information",
    "cannot determine",
    "not stated",
    "not specified",
    "no mention",
];

/// `any(phrase in answer.lower() for phrase in not_found_phrases)`
pub fn is_not_found(answer: &str) -> bool {
    let lower = answer.to_lowercase();
    NOT_FOUND_PHRASES.iter().any(|p| lower.contains(p))
}

// ============================================================================================================
// the run
// ============================================================================================================

/// The embeddings of one conversation in retrieval-only mode (the evaluators' cache files).
pub struct CachedConversation {
    pub msgs: Vec<Vec<f64>>,
    pub questions: Vec<Vec<f64>>,
    pub pairs: Vec<Vec<f64>>,
}

/// Load `<key>.json` of the embedding cache, which must hold `expected` vectors.
pub fn load_cached_vectors(dir: &Path, key: &str, expected: usize) -> HResult<Vec<Vec<f64>>> {
    let path = dir.join(format!("{key}.json"));
    let text = std::fs::read_to_string(&path).map_err(|e| {
        HarnessError::new(
            "FileNotFoundError",
            format!("{} ({e}); RETRIEVAL_ONLY needs the embedding cache", path.display()),
        )
    })?;
    let v: Value = serde_json::from_str(&text)?;
    let rows =
        v.as_array().ok_or_else(|| HarnessError::new("TypeError", format!("{} is not a list", path.display())))?;
    if rows.len() != expected {
        return Err(HarnessError::new(
            "ValueError",
            format!("{} holds {} vectors, the conversation needs {expected}", path.display(), rows.len()),
        ));
    }
    rows.iter().map(embedding_values).collect()
}

impl CachedConversation {
    pub fn load(dir: &Path, conv_idx: usize, n_messages: usize, n_questions: usize) -> HResult<Self> {
        Ok(CachedConversation {
            msgs: load_cached_vectors(dir, &format!("conv{conv_idx}_msgs"), n_messages)?,
            questions: load_cached_vectors(dir, &format!("conv{conv_idx}_questions_all"), n_questions)?,
            pairs: load_cached_vectors(dir, &format!("conv{conv_idx}_pairs2"), n_messages.saturating_sub(1))?,
        })
    }
}

/// The module-level mutable state of `runner.py` (`RETRIEVAL_METRICS`, `ROUTING_STATS`, `TIMING_DATA`, ...).
pub struct RunnerState {
    pub results: Vec<Value>,
    pub stats: CategoryStats,
    pub retrieval_metrics: IndexMap<String, CategoryMetrics>,
    pub routing_stats: IndexMap<String, i64>,
    pub final_mode_stats: IndexMap<String, i64>,
    pub timing: IndexMap<String, Vec<f64>>,
    pub routing_samples: Vec<Value>,
    pub inferential_samples: Vec<Value>,
    random: PyRandom,
}

impl Default for RunnerState {
    fn default() -> Self {
        let seed =
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        RunnerState {
            results: Vec::new(),
            stats: new_category_stats(),
            retrieval_metrics: new_retrieval_metrics(),
            routing_stats: [
                "TEMPORAL",
                "STRICT",
                "INFERENTIAL",
                "AGGREGATION",
                "LIST",
                "OPEN_DOMAIN_INFER",
                "OPEN_DOMAIN_WORLD",
                "ADVERSARIAL",
            ]
            .iter()
            .map(|m| (m.to_string(), 0))
            .collect(),
            final_mode_stats: IndexMap::new(),
            timing: IndexMap::new(),
            routing_samples: Vec::new(),
            inferential_samples: Vec::new(),
            random: PyRandom::new(seed as i128),
        }
    }
}

impl RunnerState {
    fn time(&mut self, key: &str, ms: f64) {
        self.timing.entry(key.to_string()).or_default().push(ms);
    }
}

/// A Python number of milliseconds as the result rows stored them (`round(t, 1)`; an `int` 0 stays `int`).
fn ms_value(t: PyNum) -> Value {
    t.round(1).to_value()
}

fn sep(out: &mut dyn Write) -> HResult<()> {
    writeln!(out, "\n{}", "=".repeat(60))?;
    Ok(())
}

/// `run_benchmark()`: the whole run; returns the final report document. Progress and the summary go to `out`.
pub async fn run_benchmark(cfg: &RunnerConfig, llm: &dyn Llm, out: &mut dyn Write) -> HResult<RunnerState> {
    if cfg.retrieval_only && cfg.use_speaker_profiles {
        return Err(HarnessError::new(
            "ValueError",
            "RETRIEVAL_ONLY=1 cannot be combined with USE_SPEAKER_PROFILES=1 (profiles need model calls)",
        ));
    }
    if let Some(parent) = cfg.output_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&cfg.dataset)?)?;
    let convs = data.as_array().ok_or_else(|| HarnessError::new("TypeError", "dataset is not a list"))?;
    let mut st = RunnerState::default();
    let answering = AnsweringConfig {
        ollama_base_url: cfg.base_url.clone(),
        answer_model: cfg.answer_model.clone(),
        ..AnsweringConfig::default()
    };

    for (conv_idx, conv) in convs.iter().enumerate() {
        if !cfg.only_conv.is_empty() && !cfg.only_conv.contains(&(conv_idx as i64)) {
            continue;
        }
        run_conversation(cfg, llm, &answering, conv_idx, conv, &mut st, out).await?;
        if st.results.len() as i64 >= cfg.max_questions {
            writeln!(out, "\n*** NAPOLEON EXIT: {} questions reached ***", cfg.max_questions)?;
            break;
        }
    }
    save_results(cfg, &st.results, &st.stats, &st.retrieval_metrics)?;
    print_summary(cfg, &st, out)?;
    Ok(st)
}

#[allow(clippy::too_many_arguments)]
async fn run_conversation(
    cfg: &RunnerConfig,
    llm: &dyn Llm,
    answering: &AnsweringConfig,
    conv_idx: usize,
    conv: &Value,
    st: &mut RunnerState,
    out: &mut dyn Write,
) -> HResult<()> {
    sep(out)?;
    writeln!(out, "CONVERSATION {}", conv_idx + 1)?;
    writeln!(out, "{}", "=".repeat(60))?;
    let messages = flatten_messages(conv);
    writeln!(out, "Loaded {} messages", messages.len())?;
    let empty = Vec::new();
    let qa_list = conv.get("qa").and_then(Value::as_array).unwrap_or(&empty);
    let cache = if cfg.retrieval_only {
        Some(CachedConversation::load(&cfg.emb_cache_dir, conv_idx, messages.len(), qa_list.len())?)
    } else {
        None
    };

    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let tesseract = Tesseract::new(storage.clone());

    // speaker profiles for all speakers; the per-message facts are extracted in parallel batches up front
    let mut speaker_profiler = UniversalSpeakerProfiler::new();
    writeln!(out, "Extracting facts from {} messages in parallel...", messages.len())?;
    let extraction_start = perf_counter();
    let all_extracted_facts: Vec<Vec<String>> = if cfg.use_speaker_profiles {
        let pairs: Vec<(&str, &str)> = messages.iter().map(|m| (m.speaker.as_str(), m.text.as_str())).collect();
        extract_facts_parallel(llm, &pairs, 15).await.map_err(|e| HarnessError::new("ValueError", e))?
    } else {
        vec![Vec::new(); messages.len()]
    };
    let extraction_time = f64::max(perf_counter() - extraction_start, 1e-6);
    writeln!(
        out,
        "  [OK] Extracted facts in {extraction_time:.1}s ({:.1} msgs/sec)",
        messages.len() as f64 / extraction_time
    )?;
    for (msg, facts) in messages.iter().zip(&all_extracted_facts) {
        speaker_profiler
            .add_message(&msg.speaker, &msg.text, None)
            .await
            .map_err(|e| HarnessError::new("Exception", e))?;
        if !facts.is_empty() {
            if let Some(p) = speaker_profiler.profiles.get_mut(&msg.speaker) {
                p.extracted_facts.extend(facts.iter().cloned());
            }
        }
    }

    let mut speaker_nodes: IndexMap<String, Vec<String>> = IndexMap::new();
    let mut all_node_ids: Vec<String> = Vec::new();
    for (msg_idx, msg) in messages.iter().enumerate() {
        let embedding = match &cache {
            Some(c) => c.msgs[msg_idx].clone(),
            None => get_embedding(llm, &msg.text, Some(answering)).await,
        };
        if embedding.is_empty() {
            continue;
        }
        let node = build_message_node(conv_idx, msg_idx, msg, embedding)?;
        let node_id = node.node_id.clone();
        storage.save_node(&node_ref(node));
        all_node_ids.push(node_id.clone());
        speaker_nodes.entry(msg.speaker.to_lowercase()).or_default().push(node_id);
    }
    let (edge_count, temporal_edge_count) = add_conversation_edges(&*storage, &speaker_nodes, &all_node_ids, true);

    let mut dialogue_linker = DialogueLinker::new(&*storage);
    let all_nodes: Vec<NodeRef> = all_node_ids.iter().filter_map(|id| storage.get_node(id)).collect();
    let cosine = CosineTopk::new();

    let mut pair_nodes: Vec<NodeRef> = Vec::new();
    let mut local_tesseracts: IndexMap<String, Tesseract> = IndexMap::new();
    let mut mega_index: Option<MegaIndex> = None;
    if is_local_mode(&cfg.retrieval_mode) {
        pair_nodes = match &cache {
            Some(c) => build_pair_nodes(conv_idx, &messages, &all_nodes, |_| Ok(c.pairs.clone()))?,
            None => {
                let (texts, _) = pair_texts_and_members(&messages, &all_nodes)?;
                let embs = embed_batch(llm, &texts).await?;
                build_pair_nodes(conv_idx, &messages, &all_nodes, |_| Ok(embs))?
            }
        };
        for (sp, ids) in &speaker_nodes {
            let local_storage = Rc::new(InMemoryNeuralGraphStorage::default());
            for nid in ids {
                if let Some(n) = storage.get_node(nid) {
                    local_storage.save_node(&n);
                }
            }
            local_tesseracts.insert(sp.clone(), Tesseract::new(local_storage));
        }
        writeln!(out, "  Per-agent memory: {} pair nodes, {} agent stores", pair_nodes.len(), local_tesseracts.len())?;
        if cfg.retrieval_mode == "mega" {
            let mut kg: Option<Value> = None;
            if cfg.kg {
                let kg_path = cfg.kg_cache_dir.join(format!("conv{conv_idx}.json"));
                if kg_path.exists() {
                    kg = Some(serde_json::from_str(&std::fs::read_to_string(&kg_path)?)?);
                }
                match &kg {
                    Some(k) if py_truthy(k) => {
                        let registry = k.get("registry").ok_or_else(|| HarnessError::new("KeyError", "'registry'"))?;
                        let n = match registry {
                            Value::Object(m) => m.len(),
                            Value::Array(a) => a.len(),
                            Value::String(s) => s.chars().count(),
                            other => {
                                return Err(HarnessError::new(
                                    "TypeError",
                                    format!("object of type '{}' has no len()", py_type(other)),
                                ))
                            }
                        };
                        writeln!(out, "  KG: loaded {n} entities")?;
                    }
                    _ => writeln!(out, "  KG: MISSING")?,
                }
            }
            mega_index = Some(
                MegaIndex::new(&all_nodes, &pair_nodes, kg.as_ref(), 0.5)
                    .map_err(|e| HarnessError::new("Exception", e))?,
            );
        }
    }
    let node_by_id: IndexMap<String, NodeRef> =
        all_nodes.iter().map(|n| (n.borrow().node_id.clone(), n.clone())).collect();
    let (aggregators, bindings) = dialogue_linker
        .process_dialogue_sequence(&all_nodes, &format!("conv_{conv_idx}"))
        .map_err(|e| HarnessError::new("IndexError", e))?;
    writeln!(
        out,
        "Indexed {} memories, {edge_count} entity + {temporal_edge_count} temporal edges",
        all_node_ids.len()
    )?;
    writeln!(out, "  Dialogue links: {} aggregators, {} bindings", aggregators.len(), bindings.len())?;

    let speakers: Vec<String> = speaker_nodes.keys().cloned().collect();
    let session_key = format!("conv_{conv_idx}");
    let window = cfg.rerank_window;
    for (q_idx, qa) in qa_list.iter().enumerate() {
        if st.results.len() as i64 >= cfg.max_questions {
            break;
        }
        let category_value = qa.get("category").cloned().unwrap_or(Value::from(1));
        if category_value.as_f64() == Some(5.0) {
            continue;
        }
        let Some(category) = category_name(&category_value) else { continue };
        if !cfg.only_cat.is_empty() && !cfg.only_cat.contains(category) {
            continue;
        }
        let question = str_or(qa.get("question"), "");
        let gold = qa.get("answer").cloned().unwrap_or_else(|| Value::from(""));

        let query_emb = match &cache {
            Some(c) => c.questions[q_idx].clone(),
            None => get_embedding(llm, &question, Some(answering)).await,
        };
        if query_emb.is_empty() {
            continue;
        }

        let t_e2e_start = perf_counter();
        let t_retrieval_start = perf_counter();

        let named_agents = named_in(&question, &speakers);
        let active = if is_local_mode(&cfg.retrieval_mode) && named_agents.len() == 1 {
            &local_tesseracts[&named_agents[0]] // per-agent memory routing
        } else {
            &tesseract
        };
        let mut retrieved = active
            .retrieve(&question, &query_emb, &session_key, None, TOP_K, true)
            .map_err(|e| HarnessError::new("TesseractError", format!("{e:?}")))?;
        if is_local_mode(&cfg.retrieval_mode) {
            let pool: Vec<NodeRef> = if named_agents.len() == 1 {
                pair_nodes
                    .iter()
                    .filter(|p| p.borrow().speaker_id().to_lowercase() == named_agents[0])
                    .cloned()
                    .collect()
            } else {
                pair_nodes.clone()
            };
            retrieved.truncate(TOP_K);
            if let Some(mega) = &mega_index {
                let pool_ids: IndexSet<String> = all_nodes
                    .iter()
                    .filter(|n| named_agents.len() != 1 || n.borrow().speaker_id().to_lowercase() == named_agents[0])
                    .map(|n| n.borrow().node_id.clone())
                    .collect();
                let tess_ids: Vec<String> = retrieved.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
                retrieved = mega
                    .retrieve(&question, &query_emb, Some(&pool_ids), TOP_K, "VG", None, &[tess_ids], 100)
                    .map_err(|e| HarnessError::new("KeyError", e))?;
            }
            let mut seen: IndexSet<String> = retrieved.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
            if !pool.is_empty() {
                let extra = unpair(&cosine.topk(&pool, &query_emb, 2 * TOP_K)?, &node_by_id, &mut seen)?;
                let n = py_prefix_len(extra.len(), cfg.extra_candidates);
                retrieved.extend(extra.into_iter().take(n));
            }
        }
        if cfg.retrieval_mode == "graph" {
            let flat_ids: IndexSet<String> = retrieved.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
            let expanded =
                expand_via_graph(&*storage, &dialogue_linker, &retrieved, cfg.graph_seed_k, cfg.graph_edge_decay);
            let extra: Ranked = expanded.into_iter().filter(|(n, _)| !flat_ids.contains(&n.borrow().node_id)).collect();
            retrieved.truncate(TOP_K);
            let n = py_prefix_len(extra.len(), cfg.extra_candidates);
            retrieved.extend(extra.into_iter().take(n));
        } else if cfg.retrieval_mode == "hybrid" {
            let named = named_in(&question, &speakers);
            if named.len() == 1 {
                for (n, c) in retrieved.iter_mut() {
                    if n.borrow().speaker_id().to_lowercase() == named[0] {
                        *c *= cfg.speaker_boost;
                    }
                }
                sort_by_charge_desc(&mut retrieved);
            }
            retrieved.truncate(TOP_K);
            let have: IndexSet<String> = retrieved.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
            let extra: Ranked = cosine
                .topk(&all_nodes, &query_emb, TOP_K)?
                .into_iter()
                .filter(|(n, _)| !have.contains(&n.borrow().node_id))
                .collect();
            let n = py_prefix_len(extra.len(), cfg.extra_candidates);
            retrieved.extend(extra.into_iter().take(n));
        }

        // pure retrieval timing (tesseract + dialogue linking only)
        let t_retrieval = (perf_counter() - t_retrieval_start) * 1000.0;
        st.time("t_retrieval", t_retrieval);

        let window_len = py_prefix_len(retrieved.len(), window);
        let all_memories_text: Vec<String> =
            retrieved[..window_len].iter().map(|(n, _)| n.borrow().content.clone()).collect();
        let (recall_10, rank_10) =
            check_gold_in_memories(llm, cfg, &question, &gold, &retrieved[..window_len], &all_memories_text, 10).await;
        let (recall_50, rank_50) = if recall_10 {
            (true, rank_10)
        } else {
            check_gold_in_memories(llm, cfg, &question, &gold, &retrieved[..window_len], &all_memories_text, 50).await
        };
        let m = st.retrieval_metrics.get_mut(category).expect("category of CATEGORIES");
        m.recall_at_10.push(recall_10 as i64);
        m.recall_at_50.push(recall_50 as i64);
        m.oracle_rank.push(if recall_50 { rank_50 } else { 0 });
        m.total_candidates.push(retrieved.len() as i64);

        // the query mode decides how many memories to request from the reranker
        let (mut query_mode, list_question) = route_question(&question, &speakers, cfg.open_domain_force_infer);
        *st.routing_stats
            .get_mut(&query_mode)
            .ok_or_else(|| HarnessError::new("KeyError", py_repr_str(&query_mode)))? += 1;

        // speaker profiles: for single_hop, ask the profile before using retrieval (never with the gold answer)
        let mut profile_answer: Option<String> = None;
        let mut used_profile = false;
        if cfg.use_speaker_profiles && category == "single_hop" && !list_question {
            let profile_result = speaker_profiler
                .query_single_hop(llm, &question, None)
                .await
                .map_err(|e| HarnessError::new("ModuleNotFoundError", e))?;
            let found = profile_result.get("found").is_some_and(py_truthy);
            let confidence = profile_result.get("confidence").and_then(Value::as_f64).unwrap_or(0.0);
            if found && confidence >= 0.5 {
                profile_answer = profile_result.get("answer").map(py_str);
                used_profile = true;
                writeln!(out, "  [PROFILE] Using profile answer (confidence: {:.0}%)", confidence * 100.0)?;
            }
        }

        let needed = num_memories_needed(&query_mode);

        let t_rerank_start = perf_counter();
        let candidates = &retrieved[..window_len];
        let reranked: Ranked = if cfg.retrieval_only || !USE_SLM_RERANKER {
            candidates.iter().take(needed).cloned().collect()
        } else {
            let opts = ParallelRerankOptions {
                limit: needed as i64,
                llm_model: cfg.reranker_model().to_string(),
                llm_base_url: cfg.base_url.clone(),
                max_candidates: window,
                ..ParallelRerankOptions::default()
            };
            rerank_candidates_parallel(llm, &question, candidates, &opts).await
        };
        let t_rerank = (perf_counter() - t_rerank_start) * 1000.0;
        st.time("t_rerank", t_rerank);

        // open-domain-style INFERENTIAL questions need strong inference even with weak evidence
        query_mode = upgrade_query_mode(&query_mode, &question);

        let AnswerContext { context_parts, retrieved_memories, mut candidate_memories } =
            build_answer_context(&reranked, needed, &query_mode);
        let mut context = context_parts.join("\n");

        let current_id = st.results.len() + 1;
        if st.routing_samples.len() < 30 && st.random.random() < 0.1 {
            st.routing_samples.push(obj([
                ("id", Value::from(current_id)),
                ("mode", Value::from(query_mode.clone())),
                ("question", Value::from(py_prefix(&question, 60))),
                ("context_preview", Value::from(py_prefix(&context, 200))),
            ]));
        }
        if query_mode == "INFERENTIAL" && st.inferential_samples.len() < 30 && st.random.random() < 0.15 {
            st.inferential_samples.push(obj([
                ("id", Value::from(current_id)),
                ("category", Value::from(category)),
                ("question", Value::from(py_prefix(&question, 80))),
                ("first_memory", Value::from(context_parts.first().map_or("N/A", |c| py_prefix(c, 150)))),
            ]));
        }

        if query_mode == "OPEN_DOMAIN_WORLD" && !cfg.open_domain_keep_context {
            candidate_memories.clear();
            context.clear();
        }
        let (answer_mode, retry_mode) =
            answer_modes(&query_mode, cfg.open_domain_keep_context, cfg.open_domain_infer_world);
        *st.final_mode_stats.entry(answer_mode.clone()).or_insert(0) += 1;

        let extracted_answer = extract_direct_answer(&question, &query_mode, &candidate_memories);

        let t_answer_start = perf_counter();
        let (mut generated, t_answer) =
            if let (true, Some(p)) = (used_profile, profile_answer.as_ref().filter(|p| !p.is_empty())) {
                (p.clone(), PyNum::Int(0))
            } else if let Some(e) = extracted_answer {
                (e, PyNum::Int(0))
            } else if cfg.retrieval_only {
                (String::new(), PyNum::Int(0))
            } else {
                let g = generate_answer(llm, &question, &context, &answer_mode, Some(answering)).await;
                (g, PyNum::Float((perf_counter() - t_answer_start) * 1000.0))
            };
        st.time("t_answer", t_answer.as_f64());

        // fallback: a "not found" answer gets a second, inferential pass
        if !cfg.retrieval_only && is_not_found(&generated) && query_mode != "OPEN_DOMAIN_WORLD" {
            generated = generate_answer(llm, &question, &context, &retry_mode, Some(answering)).await;
        }

        let t_e2e = (perf_counter() - t_e2e_start) * 1000.0;
        st.time("t_e2e", t_e2e);

        let correct =
            if cfg.retrieval_only { false } else { judge_answer(llm, cfg, &question, &generated, &gold, out).await? };
        let s = st.stats.get_mut(category).expect("category of CATEGORIES");
        s.1 += 1;
        if correct {
            s.0 += 1;
        }

        st.results.push(obj([
            ("id", Value::from(st.results.len() + 1)),
            ("conversation", Value::from(conv_idx + 1)),
            ("category", Value::from(category)),
            ("question", Value::from(question.clone())),
            ("generated_answer", Value::from(generated)),
            ("gold_answer", gold),
            ("correct", Value::Bool(correct)),
            ("mode", Value::from(answer_mode)),
            ("used_speaker_profile", Value::Bool(used_profile)),
            ("retrieval_latency_ms", ms_value(PyNum::Float(t_retrieval))),
            ("rerank_latency_ms", ms_value(PyNum::Float(t_rerank))),
            ("answer_latency_ms", ms_value(t_answer)),
            ("e2e_latency_ms", ms_value(PyNum::Float(t_e2e))),
            ("retrieved_memories", Value::Array(retrieved_memories)),
        ]));

        let status = if correct { "OK" } else { "FAIL" };
        writeln!(out, "  [{status}] Q{} ({category}): {}...", q_idx + 1, py_prefix(&question, 50))?;
        if st.results.len().is_multiple_of(10) {
            save_results(cfg, &st.results, &st.stats, &st.retrieval_metrics)?;
            writeln!(out, "    -> Saved {} results to file", st.results.len())?;
        }
    }
    Ok(())
}

/// `check_gold_in_memories`: the substring test (the LLM oracle when `USE_QWEN_ORACLE`, which is off).
async fn check_gold_in_memories(
    llm: &dyn Llm,
    cfg: &RunnerConfig,
    question: &str,
    gold: &Value,
    candidates: &[(NodeRef, f64)],
    texts: &[String],
    top_k: i64,
) -> (bool, i64) {
    if USE_QWEN_ORACLE {
        let mems: Vec<(String, String)> = candidates
            .iter()
            .map(|(n, _)| {
                let n = n.borrow();
                (n.content.clone(), n.metadata.get("speaker").map(py_str).unwrap_or_default())
            })
            .collect();
        return check_gold_in_memories_oracle(llm, cfg, question, gold, &mems, top_k).await;
    }
    check_gold_in_memories_substring(gold, texts, top_k)
}

/// The end-of-run summary: accuracy, latency statistics, routing statistics and the retrieval metrics.
pub fn print_summary(cfg: &RunnerConfig, st: &RunnerState, out: &mut dyn Write) -> HResult<()> {
    let total_correct: i64 = st.stats.values().map(|s| s.0).sum();
    let total_questions: i64 = st.stats.values().map(|s| s.1).sum();
    let accuracy = if total_questions > 0 { 100.0 * total_correct as f64 / total_questions as f64 } else { 0.0 };
    sep(out)?;
    writeln!(out, "BENCHMARK COMPLETE (Answer: {}, Judge: GPT-4o)", cfg.answer_model)?;
    writeln!(out, "{}", "=".repeat(60))?;
    writeln!(out, "Total: {total_correct}/{total_questions} ({accuracy:.1}%)")?;
    for (cat, (c, t)) in &st.stats {
        if *t > 0 {
            writeln!(out, "  {cat}: {c}/{t} ({:.1}%)", 100.0 * *c as f64 / *t as f64)?;
        }
    }

    sep(out)?;
    writeln!(out, "LATENCY STATISTICS")?;
    writeln!(out, "{}", "=".repeat(60))?;
    let empty = Vec::new();
    let lat = |k: &str| st.timing.get(k).unwrap_or(&empty);
    print_latency_stats(out, "Memory Retrieval", lat("t_retrieval"))?;
    print_latency_stats(out, &format!("Reranking ({})", cfg.reranker_model()), lat("t_rerank"))?;
    print_latency_stats(out, &format!("Answer Generation ({})", cfg.answer_model), lat("t_answer"))?;
    print_latency_stats(out, "End-to-End", lat("t_e2e"))?;

    sep(out)?;
    writeln!(out, "ROUTING STATS")?;
    writeln!(out, "{}", "=".repeat(60))?;
    let r = |k: &str| st.routing_stats.get(k).copied().unwrap_or(0);
    writeln!(out, "TEMPORAL:    {} questions", r("TEMPORAL"))?;
    writeln!(out, "STRICT:      {} questions", r("STRICT"))?;
    writeln!(out, "INFERENTIAL: {} questions", r("INFERENTIAL"))?;
    writeln!(out, "OPEN_INFER:  {} questions", r("OPEN_DOMAIN_INFER"))?;
    writeln!(out, "OPEN_WORLD:  {} questions", r("OPEN_DOMAIN_WORLD"))?;
    writeln!(out, "LIST:        {} questions", r("LIST"))?;
    writeln!(out, "AGGREGATION: {} questions", r("AGGREGATION"))?;
    writeln!(out, "FINAL ANSWER MODES (after OPEN_DOMAIN_INFER upgrade / H5 flags):")?;
    let mut modes: Vec<(&String, &i64)> = st.final_mode_stats.iter().collect();
    modes.sort_by_key(|(_, n)| -**n);
    for (m, n) in modes {
        writeln!(out, "  {m:<24} {n} questions")?;
    }

    sep(out)?;
    writeln!(out, "RETRIEVAL METRICS")?;
    writeln!(out, "{}", "=".repeat(60))?;
    writeln!(out, "{:<15} {:<12} {:<12} {:<12}", "Category", "Recall@10", "Recall@50", "RerankGain")?;
    writeln!(out, "{}", "-".repeat(60))?;
    let summary = compute_retrieval_summary(&st.retrieval_metrics);
    for cat in ["single_hop", "temporal", "open_domain", "multi_hop"] {
        if let Some(m) = summary.get(cat) {
            let f = |k: &str| m.get(k).and_then(Value::as_f64).unwrap_or(0.0);
            writeln!(
                out,
                "{cat:<15} {:>8.1}%    {:>8.1}%    {}%",
                f("recall_at_10"),
                f("recall_at_50"),
                signed_fixed(f("rerank_gain"), 8, 1)
            )?;
        }
    }
    writeln!(out, "\nResults saved to: {}", cfg.output_path.display())?;
    Ok(())
}

/// `format(x, "+W.Pf")`.
pub fn signed_fixed(x: f64, width: usize, prec: usize) -> String {
    let body = format!("{:.*}", prec, x.abs());
    let sign = if x.is_sign_negative() { "-" } else { "+" };
    format!("{:>width$}", format!("{sign}{body}"))
}

/// `print_latency_stats(name, latencies)`.
pub fn print_latency_stats(out: &mut dyn Write, name: &str, latencies: &[f64]) -> HResult<()> {
    if latencies.is_empty() {
        writeln!(out, "{name}: No data")?;
        return Ok(());
    }
    let mut sorted = latencies.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = sorted.len();
    let mut sum = 0.0;
    for x in latencies {
        sum += x;
    }
    let (min, max) = py_min_max(latencies);
    let at = |q: f64| sorted[((n as f64 * q) as usize).min(n - 1)];
    writeln!(out, "\n{name}:")?;
    writeln!(out, "  Count:    {n} queries")?;
    writeln!(out, "  Mean:     {:.1} ms", sum / n as f64)?;
    writeln!(out, "  Min:      {min:.1} ms")?;
    writeln!(out, "  Max:      {max:.1} ms")?;
    writeln!(out, "  P50:      {:.1} ms", sorted[n / 2])?;
    writeln!(out, "  P90:      {:.1} ms", at(0.9))?;
    writeln!(out, "  P95:      {:.1} ms", at(0.95))?;
    writeln!(out, "  P99:      {:.1} ms", at(0.99))?;
    Ok(())
}

/// The output format of the report (`json.dump(..., indent=2)`).
pub fn report_format() -> JsonFormat {
    json_indent(2)
}
