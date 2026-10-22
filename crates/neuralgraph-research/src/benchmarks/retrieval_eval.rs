//! Port of `research/benchmarks/retrieval_eval.py`: recall@10 / @50 / @all per retrieval variant on LoCoMo, no LLM
//! calls (binary `retrieval-eval`). Embeddings are cached, so retrieval changes can be iterated in seconds before
//! paying for a full end-to-end run with reranking, answering and judging.
//!
//! ```text
//! ONLY_CAT=single_hop ONLY_CONV=0,1 retrieval-eval
//! VARIANTS=embed,tesseract,tesseract+graph retrieval-eval
//! ```
//!
//! Recall uses the benchmark's substring check (gold split on "," / " and "). Environment: `ONLY_CAT`, `ONLY_CONV`,
//! `VARIANTS` (default `embed,tesseract,tesseract+graph`; also `tesseract+graph_append`, `tesseract+embed_union`,
//! `tesseract+speaker+embed_union`, `tesseract+speaker`), `EMB_CACHE_DIR` (default `research/results/emb_cache`).
//! The Python imported `runner`, so `GRAPH_SEED_K` / `GRAPH_EDGE_DECAY` (the graph variants) and the rest of the
//! runner's environment are read as well, and a malformed runner variable fails here too.
//!
//! The embedding cache keeps the Python format and semantics: `<key>.json` is a JSON list of vectors; it is reused
//! when it holds as many vectors as there are texts, otherwise every text is embedded again and the file rewritten.

use super::pyharness::{repo_root, HResult, HarnessError};
use super::runner::{
    add_conversation_edges, build_message_node, category_name, check_gold_in_memories_substring, embedding_values,
    expand_via_graph, flatten_messages, named_in, openai_embed_batch, parse_int_set, parse_str_set,
    sort_by_charge_desc, Message, Ranked, RunnerConfig, TOP_K,
};
use crate::llm_backend::{backend_for, llm_base_url, EmbedOptions, Llm};
use crate::retrieval::data_types::{node_ref, NodeRef};
use crate::retrieval::dialogue_linker::DialogueLinker;
use crate::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use crate::retrieval::tesseract::Tesseract;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{dumps, JsonFormat};
use serde_json::Value;
use std::cell::RefCell;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::rc::Rc;

pub use super::runner::cosine_topk;

/// The evaluator's settings (`CACHE_DIR`, `ONLY_CAT`, `ONLY_CONV`, `VARIANTS`) plus the runner's graph knobs.
#[derive(Clone, Debug, PartialEq)]
pub struct EvalConfig {
    pub cache_dir: PathBuf,
    pub only_cat: IndexSet<String>,
    pub only_conv: IndexSet<i64>,
    pub variants: Vec<String>,
    pub graph_seed_k: i64,
    pub graph_edge_decay: f64,
    pub dataset: PathBuf,
}

/// `EMB_CACHE_DIR`, default `research/results/emb_cache`.
pub fn cache_dir_from_env() -> PathBuf {
    std::env::var("EMB_CACHE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo_root().join("research").join("results").join("emb_cache"))
}

impl EvalConfig {
    /// The configuration from the environment; also evaluates the runner's (the Python imported it) and creates
    /// the cache directory.
    pub fn from_env() -> HResult<Self> {
        let runner = RunnerConfig::from_env()?;
        let cache_dir = cache_dir_from_env();
        std::fs::create_dir_all(&cache_dir)?;
        let variants = std::env::var("VARIANTS").unwrap_or_else(|_| "embed,tesseract,tesseract+graph".into());
        Ok(EvalConfig {
            cache_dir,
            only_cat: parse_str_set(&std::env::var("ONLY_CAT").unwrap_or_default()),
            only_conv: parse_int_set(&std::env::var("ONLY_CONV").unwrap_or_default())?,
            variants: variants.split(',').map(|v| neuralgraph_memory::pycompat::py_strip(v).to_string()).collect(),
            graph_seed_k: runner.graph_seed_k,
            graph_edge_decay: runner.graph_edge_decay,
            dataset: runner.dataset,
        })
    }
}

/// `embed_batch(http, texts)` of the evaluator: batched on OpenAI-style servers (64 per request), otherwise one
/// `llm_embed` per text, whose failure is an error (unlike the runner's, which yields an empty vector). A model
/// without an HTTP client (tests) embeds one text at a time.
pub async fn embed_batch(llm: &dyn Llm, texts: &[String]) -> HResult<Vec<Vec<f64>>> {
    if backend_for(&llm_base_url()) == "openai" {
        if let Some(client) = llm.http() {
            return openai_embed_batch(client, texts).await;
        }
    }
    let mut out = Vec::with_capacity(texts.len());
    for t in texts {
        out.push(
            llm.embed(t, &EmbedOptions::default()).await.map_err(|e| HarnessError::new("aiohttp.ClientError", e))?,
        );
    }
    Ok(out)
}

/// `cached_embeddings(http, key, texts)`: `<cache_dir>/<key>.json` if it holds one vector per text, else embed
/// every text and (re)write the file with `json.dump`.
pub async fn cached_embeddings(llm: &dyn Llm, cache_dir: &Path, key: &str, texts: &[String]) -> HResult<Vec<Vec<f64>>> {
    let path = cache_dir.join(format!("{key}.json"));
    if path.exists() {
        let cached: Value = serde_json::from_str(&std::fs::read_to_string(&path)?)?;
        // (a cache file that is not a list is treated like a length mismatch)
        if let Value::Array(rows) = &cached {
            if rows.len() == texts.len() {
                return rows.iter().map(embedding_values).collect();
            }
        }
    }
    let vecs = embed_batch(llm, texts).await?;
    let value = Value::Array(vecs.iter().map(|v| Value::from(v.clone())).collect());
    std::fs::write(&path, dumps(&value, &JsonFormat::python_default()))?;
    Ok(vecs)
}

/// An ingested conversation: the pooled storage, the dialogue links, the message nodes in order and the speakers
/// (lower-case, first-appearance order).
pub struct Ingested {
    pub storage: Rc<InMemoryNeuralGraphStorage>,
    pub linker: DialogueLinker,
    pub nodes: Vec<NodeRef>,
    pub speakers: Vec<String>,
}

/// `ingest(http, conv_idx, conv)`: mirror of the runner's ingestion (same node metadata, same edges, same dialogue
/// links), with the message embeddings from the cache (`conv<i>_msgs`).
pub async fn ingest(llm: &dyn Llm, cache_dir: &Path, conv_idx: usize, conv: &Value) -> HResult<Ingested> {
    let messages: Vec<Message> = flatten_messages(conv);
    let texts: Vec<String> = messages.iter().map(|m| m.text.clone()).collect();
    let embeddings = cached_embeddings(llm, cache_dir, &format!("conv{conv_idx}_msgs"), &texts).await?;
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let mut speaker_nodes: IndexMap<String, Vec<String>> = IndexMap::new();
    let mut all_ids: Vec<String> = Vec::new();
    for (idx, (msg, emb)) in messages.iter().zip(embeddings).enumerate() {
        let node = build_message_node(conv_idx, idx, msg, emb)?;
        let node_id = node.node_id.clone();
        storage.save_node(&node_ref(node));
        all_ids.push(node_id.clone());
        speaker_nodes.entry(msg.speaker.to_lowercase()).or_default().push(node_id);
    }
    add_conversation_edges(&*storage, &speaker_nodes, &all_ids, false);
    let mut linker = DialogueLinker::new(&*storage);
    let nodes: Vec<NodeRef> = all_ids
        .iter()
        .map(|id| storage.get_node(id).ok_or_else(|| HarnessError::new("AttributeError", "'NoneType' object")))
        .collect::<HResult<_>>()?;
    linker
        .process_dialogue_sequence(&nodes, &format!("conv_{conv_idx}"))
        .map_err(|e| HarnessError::new("IndexError", e))?;
    Ok(Ingested { storage, linker, nodes, speakers: speaker_nodes.keys().cloned().collect() })
}

/// The Tesseract result of the last `(conversation, question)`, shared by the tesseract variants (the slow part).
#[derive(Default)]
pub struct TesseractCache {
    entry: RefCell<Option<((usize, String), Ranked)>>,
}

impl TesseractCache {
    fn get(&self, tesseract: &Tesseract, conv_idx: usize, question: &str, qvec: &[f64]) -> HResult<Ranked> {
        let key = (conv_idx, question.to_string());
        if let Some((k, v)) = &*self.entry.borrow() {
            if *k == key {
                return Ok(v.clone());
            }
        }
        let res = tesseract
            .retrieve(question, qvec, &format!("conv_{conv_idx}"), None, TOP_K, true)
            .map_err(|e| HarnessError::new("TesseractError", format!("{e:?}")))?;
        *self.entry.borrow_mut() = Some((key, res.clone()));
        Ok(res)
    }
}

/// Boost the messages of the only named speaker by `factor` and re-sort (no-op unless exactly one is named).
fn boost_named(mut res: Ranked, question: &str, speakers: &[String], factor: f64) -> Ranked {
    let named = named_in(question, speakers);
    if named.len() == 1 {
        for (n, c) in res.iter_mut() {
            if n.borrow().speaker_id().to_lowercase() == named[0] {
                *c *= factor;
            }
        }
        sort_by_charge_desc(&mut res);
    }
    res
}

/// `retrieve(variant, ...)`: one variant's ranked candidates. `Err` is `ValueError(variant)` for an unknown name.
#[allow(clippy::too_many_arguments)]
pub fn retrieve(
    cfg: &EvalConfig,
    variant: &str,
    question: &str,
    qvec: &[f64],
    ing: &Ingested,
    tesseract: &Tesseract,
    conv_idx: usize,
    cache: &TesseractCache,
) -> HResult<Ranked> {
    if variant == "embed" {
        return cosine_topk(&ing.nodes, qvec, TOP_K);
    }
    if !variant.starts_with("tesseract") {
        return Err(HarnessError::new("ValueError", variant));
    }
    let mut res = cache.get(tesseract, conv_idx, question, qvec)?;
    match variant {
        "tesseract+graph" => {
            let mut expanded =
                expand_via_graph(&*ing.storage, &ing.linker, &res, cfg.graph_seed_k, cfg.graph_edge_decay);
            expanded.truncate(TOP_K);
            Ok(expanded)
        }
        "tesseract+graph_append" => {
            // the flat top-K stays intact; graph neighbours are appended after it (rerank window grows to 80)
            let flat_ids: IndexSet<String> = res.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
            let expanded = expand_via_graph(&*ing.storage, &ing.linker, &res, cfg.graph_seed_k, cfg.graph_edge_decay);
            res.truncate(TOP_K);
            res.extend(expanded.into_iter().filter(|(n, _)| !flat_ids.contains(&n.borrow().node_id)).take(30));
            Ok(res)
        }
        "tesseract+embed_union" | "tesseract+speaker+embed_union" => {
            // Tesseract's min_charge filters drop some gold messages outright; back-fill with pure-embedding hits
            // it never saw (the extra candidates go after the flat top-K)
            if variant.contains("speaker") {
                res = boost_named(res, question, &ing.speakers, 1.5);
            }
            res.truncate(TOP_K);
            let have: IndexSet<String> = res.iter().map(|(n, _)| n.borrow().node_id.clone()).collect();
            let extra = cosine_topk(&ing.nodes, qvec, TOP_K)?;
            res.extend(extra.into_iter().filter(|(n, _)| !have.contains(&n.borrow().node_id)).take(30));
            Ok(res)
        }
        "tesseract+speaker" => {
            // if the question names exactly one participant, favour that participant's own messages
            let mut res = boost_named(res, question, &ing.speakers, 1.5);
            res.truncate(TOP_K);
            Ok(res)
        }
        _ => {
            res.truncate(TOP_K);
            Ok(res)
        }
    }
}

/// Recall counters of one (variant, category).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Hits {
    pub r10: i64,
    pub r50: i64,
    pub rall: i64,
    pub n: i64,
}

/// The QA list of a conversation as a slice.
pub fn qa_of(conv: &Value) -> &[Value] {
    conv.get("qa").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default()
}

/// The question texts of every QA entry (`[qa["question"] for qa in conv.get("qa", [])]`).
pub fn question_texts(conv: &Value) -> HResult<Vec<String>> {
    qa_of(conv)
        .iter()
        .map(|qa| {
            qa.get("question")
                .map(neuralgraph_memory::pycompat::py_str)
                .ok_or_else(|| HarnessError::new("KeyError", "'question'"))
        })
        .collect()
}

/// `main()`: evaluate every variant on every selected question; returns the per-variant counters. Progress lines
/// and the final table go to `out`.
pub async fn main_with(
    cfg: &EvalConfig,
    llm: &dyn Llm,
    out: &mut dyn Write,
) -> HResult<IndexMap<String, IndexMap<String, Hits>>> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&cfg.dataset)?)?;
    let convs = data.as_array().ok_or_else(|| HarnessError::new("TypeError", "dataset is not a list"))?;
    let mut hits: IndexMap<String, IndexMap<String, Hits>> =
        cfg.variants.iter().map(|v| (v.clone(), IndexMap::new())).collect();
    let t0 = std::time::Instant::now();
    let cache = TesseractCache::default();
    for (conv_idx, conv) in convs.iter().enumerate() {
        if !cfg.only_conv.is_empty() && !cfg.only_conv.contains(&(conv_idx as i64)) {
            continue;
        }
        let ing = ingest(llm, &cfg.cache_dir, conv_idx, conv).await?;
        let tesseract = Tesseract::new(ing.storage.clone());
        let qas: Vec<&Value> = qa_of(conv)
            .iter()
            .filter(|qa| {
                qa.get("category")
                    .and_then(category_name)
                    .is_some_and(|c| cfg.only_cat.is_empty() || cfg.only_cat.contains(c))
            })
            .collect();
        let questions = question_texts(conv)?;
        let qvecs =
            cached_embeddings(llm, &cfg.cache_dir, &format!("conv{conv_idx}_questions_all"), &questions).await?;
        let qvec_by_text: IndexMap<&String, &Vec<f64>> = questions.iter().zip(&qvecs).collect();
        for qa in &qas {
            let cat = qa.get("category").and_then(category_name).expect("filtered");
            let gold = qa.get("answer").cloned().unwrap_or_else(|| Value::from(""));
            let q = qa.get("question").map(neuralgraph_memory::pycompat::py_str).unwrap_or_default();
            let qvec = qvec_by_text.get(&q).ok_or_else(|| HarnessError::new("KeyError", &q))?;
            for v in &cfg.variants {
                let res = retrieve(cfg, v, &q, qvec, &ing, &tesseract, conv_idx, &cache)?;
                let mems: Vec<String> = res.iter().map(|(n, _)| n.borrow().content.clone()).collect();
                let (r10, _) = check_gold_in_memories_substring(&gold, &mems, 10);
                let (r50, _) = check_gold_in_memories_substring(&gold, &mems, 50);
                let (rall, _) = check_gold_in_memories_substring(&gold, &mems, mems.len() as i64);
                let h = hits.get_mut(v).expect("variant").entry(cat.to_string()).or_default();
                h.n += 1;
                h.r10 += r10 as i64;
                h.r50 += r50 as i64;
                h.rall += rall as i64;
            }
        }
        writeln!(out, "conv {conv_idx}: {} questions done ({:.0}s)", qas.len(), t0.elapsed().as_secs_f64())?;
    }
    write!(out, "{}", report(&cfg.variants, &hits))?;
    Ok(hits)
}

/// The final table.
pub fn report(variants: &[String], hits: &IndexMap<String, IndexMap<String, Hits>>) -> String {
    let mut s = format!(
        "\n{:24} {:12} {:>5} {:>10} {:>10} {:>11}\n",
        "variant", "category", "n", "recall@10", "recall@50", "recall@all"
    );
    let pct = |x: i64, n: i64| 100.0 * x as f64 / n as f64;
    for v in variants {
        let per_cat = &hits[v];
        let mut cats: Vec<(&String, &Hits)> = per_cat.iter().collect();
        cats.sort_by(|a, b| a.0.cmp(b.0));
        for (cat, h) in cats {
            s.push_str(&format!(
                "{v:24} {cat:12} {:5} {:9.1}% {:9.1}% {:10.1}%\n",
                h.n,
                pct(h.r10, h.n),
                pct(h.r50, h.n),
                pct(h.rall, h.n)
            ));
        }
        let tot = per_cat.values().fold(Hits::default(), |a, h| Hits {
            r10: a.r10 + h.r10,
            r50: a.r50 + h.r50,
            rall: a.rall + h.rall,
            n: a.n + h.n,
        });
        if tot.n != 0 {
            s.push_str(&format!(
                "{v:24} {:12} {:5} {:9.1}% {:9.1}% {:10.1}%\n",
                "ALL",
                tot.n,
                pct(tot.r10, tot.n),
                pct(tot.r50, tot.n),
                pct(tot.rall, tot.n)
            ));
        }
        s.push('\n');
    }
    s
}
