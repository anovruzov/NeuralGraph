//! Port of `research/benchmarks/two_agent_eval.py`: two-agent memory probes on LoCoMo, retrieval only, no LLM
//! calls (binary `two-agent-eval`). Each LoCoMo speaker is an agent with its own memory store.
//!
//! - `MODE=split` (default): H9a, the per-agent memory split (pooled vs local = route to the named agent vs
//!   federated = top 25 of each agent's store, merged) for Tesseract and pure-embedding retrieval; H9b, the
//!   speaker-swap probe (single_hop questions naming exactly one speaker, rewritten with the other speaker's name:
//!   how much does recall drop?).
//! - `MODE=combined`: per-agent routing plus question+reply pair nodes and the mega-search fusions
//!   (`retrieval::mega_search`), recall@10 / @50 / @all per variant.
//!
//! Environment: `MODE`, `ONLY_CONV`, `OUT_JSON` (per-question rows), `EMB_CACHE_DIR` (and the runner's variables, read
//! because the Python imported it). Swapped-question embeddings are cached as `conv<i>_questions_swapped`, pair
//! embeddings as `conv<i>_pairs2`; `research/results/kg_cache/conv<i>.json` enables the KG variants.
//!
//! Set order: Python's `evidence_speakers` / `substring_gold_speakers` sets only reach the rows sorted or as the
//! single member of a one-element set, so nothing depends on their order. The mega-search graph channel summed
//! PageRank mass in set order (see `retrieval::mega_search`); the row "KG: graph channel only" ranks by that channel
//! alone and was the one variant whose numbers changed with the hash seed in Python.

use super::pyharness::{repo_root, HResult, HarnessError};
use super::retrieval_eval::{cache_dir_from_env, cached_embeddings, ingest, qa_of, question_texts};
use super::runner::{
    category_name, check_gold_in_memories_substring, cosine_topk, flatten_messages, make_pair_nodes,
    pair_texts_and_members, parse_int_set, sort_by_charge_desc, unpair, Message, Ranked, RunnerConfig, TOP_K,
};
use crate::llm_backend::Llm;
use crate::retrieval::data_types::NodeRef;
use crate::retrieval::mega_search::MegaIndex;
use crate::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use crate::retrieval::tesseract::Tesseract;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{dumps, obj, py_str, py_strip, JsonFormat};
use regex::Regex;
use serde_json::{Map, Value};
use std::io::Write;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::LazyLock;

pub const JUDGE_CATS: [&str; 3] = ["single_hop", "multi_hop", "open_domain"];
/// per-agent slice merged in "federated"
pub const FED_K: usize = 25;
/// back-fill budget appended after a top-50 (same as embed_union / H3 pairs)
pub const PAIR_EXTRA: usize = 30;
/// same as retrieval_eval "tesseract+speaker"
pub const SPEAKER_BOOST: f64 = 1.5;

/// The evaluator's settings.
#[derive(Clone, Debug, PartialEq)]
pub struct TwoAgentConfig {
    pub mode: String,
    pub only_conv: IndexSet<i64>,
    pub out_json: String,
    pub cache_dir: PathBuf,
    pub dataset: PathBuf,
    pub kg_cache_dir: PathBuf,
}

impl TwoAgentConfig {
    /// From the environment (the runner's and the retrieval evaluator's module-level reads included).
    pub fn from_env() -> HResult<Self> {
        let runner = RunnerConfig::from_env()?;
        let cache_dir = cache_dir_from_env();
        std::fs::create_dir_all(&cache_dir)?;
        Ok(TwoAgentConfig {
            mode: std::env::var("MODE").unwrap_or_else(|_| "split".into()),
            only_conv: parse_int_set(&std::env::var("ONLY_CONV").unwrap_or_default())?,
            out_json: std::env::var("OUT_JSON").unwrap_or_default(),
            cache_dir,
            dataset: runner.dataset,
            kg_cache_dir: repo_root().join("research").join("results").join("kg_cache"),
        })
    }
}

// ============================================================================================================
// helpers
// ============================================================================================================

/// `named_speakers(question, speakers)`: same rule as retrieval_eval "tesseract+speaker", a lower-case substring.
pub fn named_speakers(question: &str, speakers: &[String]) -> Vec<String> {
    super::runner::named_in(question, speakers)
}

/// `str.isupper()`: at least one cased character and no lower-case one.
fn py_isupper(s: &str) -> bool {
    let mut cased = false;
    for c in s.chars() {
        if c.is_lowercase() {
            return false;
        }
        if c.is_uppercase() {
            cased = true;
        }
    }
    cased
}

/// `swap_name(question, src, dst)`: case-preserving whole-word, case-insensitive replacement of `src` by `dst`
/// (all-caps stays all-caps, a capitalised word gets `dst` capitalised, anything else lower case).
pub fn swap_name(question: &str, src: &str, dst: &str) -> String {
    let re = crate::retrieval::pytext::py_regex(&format!(r"(?i)\b{}\b", fancy_regex::escape(src)));
    let mut out = String::new();
    let mut last = 0;
    for m in re.find_iter(question).flatten() {
        out.push_str(&question[last..m.start()]);
        let w = m.as_str();
        let replacement = if py_isupper(w) {
            dst.to_uppercase()
        } else if w.chars().next().is_some_and(char::is_uppercase) {
            let mut cs = dst.chars();
            match cs.next() {
                Some(first) => format!("{}{}", first.to_uppercase(), cs.as_str().to_lowercase()),
                None => String::new(),
            }
        } else {
            dst.to_lowercase()
        };
        out.push_str(&replacement);
        last = m.end();
    }
    out.push_str(&question[last..]);
    out
}

/// `gold_parts(gold)`: the gold answer split on "," and " and " (parts longer than two characters), or the whole.
pub fn gold_parts(gold: &Value) -> Vec<String> {
    let g = py_strip(&py_str(gold).to_lowercase()).to_string();
    let parts: Vec<String> = g
        .replace(',', "|")
        .replace(" and ", "|")
        .split('|')
        .map(|p| py_strip(p).to_string())
        .filter(|p| p.chars().count() > 2)
        .collect();
    if parts.is_empty() {
        vec![g]
    } else {
        parts
    }
}

fn texts(res: &[(NodeRef, f64)]) -> Vec<String> {
    res.iter().map(|(n, _)| n.borrow().content.clone()).collect()
}

/// `hits(res, gold)`: recall@10, recall@50.
pub fn hits(res: &[(NodeRef, f64)], gold: &Value) -> (bool, bool) {
    let mems = texts(res);
    (check_gold_in_memories_substring(gold, &mems, 10).0, check_gold_in_memories_substring(gold, &mems, 50).0)
}

/// `hits3(res, gold)`: recall@10, @50, @all as 0/1.
pub fn hits3(res: &[(NodeRef, f64)], gold: &Value) -> [i64; 3] {
    let mems = texts(res);
    let n = mems.len() as i64;
    [10, 50, n].map(|k| check_gold_in_memories_substring(gold, &mems, k).0 as i64)
}

/// `speaker_boost(res, speaker)`: x1.5 for the speaker's own messages, re-sorted by charge.
pub fn speaker_boost(res: &[(NodeRef, f64)], speaker: &str) -> Ranked {
    let mut out: Ranked = res
        .iter()
        .map(|(n, c)| {
            let own = n.borrow().speaker_id().to_lowercase() == speaker;
            (n.clone(), c * if own { SPEAKER_BOOST } else { 1.0 })
        })
        .collect();
    sort_by_charge_desc(&mut out);
    out
}

fn id_of(n: &NodeRef) -> String {
    n.borrow().node_id.clone()
}

/// `merge_by_charge(a, b, k)`: both lists sorted together by charge (stable), each node once, top `k`.
pub fn merge_by_charge(a: &[(NodeRef, f64)], b: &[(NodeRef, f64)], k: usize) -> Ranked {
    let mut all: Ranked = a.iter().chain(b).cloned().collect();
    sort_by_charge_desc(&mut all);
    let mut seen = IndexSet::new();
    all.into_iter().filter(|(n, _)| seen.insert(id_of(n))).take(k).collect()
}

/// `merge_round_robin(a, b, k)`: alternate a[0], b[0], a[1], ..., each node once, top `k`.
pub fn merge_round_robin(a: &[(NodeRef, f64)], b: &[(NodeRef, f64)], k: usize) -> Ranked {
    let mut seen = IndexSet::new();
    let mut out = Vec::new();
    for i in 0..a.len().max(b.len()) {
        for lst in [a, b] {
            if let Some(item) = lst.get(i) {
                if seen.insert(id_of(&item.0)) {
                    out.push(item.clone());
                }
            }
        }
    }
    out.truncate(k);
    out
}

/// `build_agent_store(nodes)`: a per-agent store holding the same node objects, no edges (Tesseract only needs the
/// nodes and the session index).
pub fn build_agent_store(nodes: &[NodeRef]) -> Rc<InMemoryNeuralGraphStorage> {
    let st = Rc::new(InMemoryNeuralGraphStorage::default());
    for n in nodes {
        st.save_node(n);
    }
    st
}

/// `tess(t, q, qvec, conv_idx)`: Tesseract top 50.
pub fn tess(t: &Tesseract, q: &str, qvec: &[f64], conv_idx: usize) -> HResult<Ranked> {
    t.retrieve(q, qvec, &format!("conv_{conv_idx}"), None, TOP_K, true)
        .map_err(|e| HarnessError::new("TesseractError", format!("{e:?}")))
}

static DIA_ID_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"D[0-9]+:[0-9]+").unwrap());

/// `evidence_speakers(qa, dia_speaker)`: the (lower-case) speakers of the evidence messages (`D<session>:<turn>`
/// ids found in the evidence strings), sorted (the Python set was only ever used sorted or as its single member).
pub fn evidence_speakers(qa: &Value, dia_speaker: &IndexMap<String, String>) -> Vec<String> {
    let ev: Vec<String> = match qa.get("evidence") {
        None => Vec::new(),
        Some(Value::String(s)) => vec![s.clone()],
        Some(Value::Array(items)) => items.iter().map(py_str).collect(),
        Some(other) => vec![py_str(other)],
    };
    let joined = ev.join(" ");
    let set: IndexSet<String> =
        py_dia_ids(&joined).into_iter().filter_map(|id| dia_speaker.get(&id).map(|s| s.to_lowercase())).collect();
    let mut out: Vec<String> = set.into_iter().collect();
    out.sort();
    out
}

/// `re.findall(r"D\d+:\d+", text)` with Python's `\d` (any Unicode decimal digit).
fn py_dia_ids(text: &str) -> Vec<String> {
    static PY_DIA: LazyLock<fancy_regex::Regex> = LazyLock::new(|| crate::retrieval::pytext::py_regex(r"D\d+:\d+"));
    let _ = &*DIA_ID_RE;
    crate::retrieval::pytext::py_findall(&PY_DIA, text)
}

/// `substring_gold_speakers(gold, nodes)`: speakers (lower-case) of the messages containing a gold part or the
/// whole gold answer, sorted.
pub fn substring_gold_speakers(gold: &Value, nodes: &[NodeRef]) -> Vec<String> {
    let parts = gold_parts(gold);
    let g = py_strip(&py_str(gold).to_lowercase()).to_string();
    let mut out: IndexSet<String> = IndexSet::new();
    for n in nodes {
        let n = n.borrow();
        let t = n.content.to_lowercase();
        if parts.iter().any(|p| t.contains(p.as_str())) || t.contains(g.as_str()) {
            out.insert(n.speaker_id().to_lowercase());
        }
    }
    let mut v: Vec<String> = out.into_iter().collect();
    v.sort();
    v
}

/// `{m["dia_id"]: m["speaker"]}` over every session list of the conversation.
pub fn dia_speakers(c: &Value) -> IndexMap<String, String> {
    let mut out = IndexMap::new();
    if let Some(m) = c.as_object() {
        for (k, v) in m {
            let Some(items) = v.as_array().filter(|_| k.starts_with("session_")) else { continue };
            for msg in items {
                if let Some(id) = msg.get("dia_id") {
                    out.insert(py_str(id), msg.get("speaker").map(py_str).unwrap_or_default());
                }
            }
        }
    }
    out
}

fn conv_str(c: &Value, key: &str) -> HResult<String> {
    c.get(key).map(py_str).ok_or_else(|| HarnessError::new("KeyError", format!("'{key}'")))
}

fn judge_cat(qa: &Value) -> Option<&'static str> {
    qa.get("category").and_then(category_name).filter(|c| JUDGE_CATS.contains(c))
}

fn pair(r: (bool, bool)) -> Value {
    Value::from(vec![r.0 as i64, r.1 as i64])
}

fn json_list(v: &[String]) -> Value {
    Value::from(v.to_vec())
}

// ============================================================================================================
// H9a / H9b (MODE=split)
// ============================================================================================================

/// `main()`: H9a and H9b over the selected conversations; prints progress and the report, writes `OUT_JSON`.
/// Returns `(rows_a, rows_b)`.
pub async fn main_split(cfg: &TwoAgentConfig, llm: &dyn Llm, out: &mut dyn Write) -> HResult<(Vec<Value>, Vec<Value>)> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&cfg.dataset)?)?;
    let convs = data.as_array().ok_or_else(|| HarnessError::new("TypeError", "dataset is not a list"))?;
    let (mut rows_a, mut rows_b) = (Vec::new(), Vec::new());
    let t0 = std::time::Instant::now();
    for (conv_idx, conv) in convs.iter().enumerate() {
        if !cfg.only_conv.is_empty() && !cfg.only_conv.contains(&(conv_idx as i64)) {
            continue;
        }
        let ing = ingest(llm, &cfg.cache_dir, conv_idx, conv).await?;
        let speakers = &ing.speakers;
        let nodes = &ing.nodes;
        let c = conv.get("conversation").ok_or_else(|| HarnessError::new("KeyError", "'conversation'"))?;
        let (sp_a, sp_b) = (conv_str(c, "speaker_a")?, conv_str(c, "speaker_b")?);
        let expected: IndexSet<String> = [sp_a.to_lowercase(), sp_b.to_lowercase()].into_iter().collect();
        let got: IndexSet<String> = speakers.iter().cloned().collect();
        if expected.len() != got.len() || !expected.iter().all(|s| got.contains(s)) {
            return Err(HarnessError::new("AssertionError", format!("({speakers:?}, {sp_a:?}, {sp_b:?})")));
        }
        let dia_speaker = dia_speakers(c);

        let mut by_sp: IndexMap<String, Vec<NodeRef>> = IndexMap::new();
        for sp in [sp_a.to_lowercase(), sp_b.to_lowercase()] {
            let own = nodes.iter().filter(|n| n.borrow().speaker_id().to_lowercase() == sp).cloned().collect();
            by_sp.insert(sp, own);
        }
        let t_pool = Tesseract::new(ing.storage.clone());
        let t_ag: IndexMap<String, Tesseract> =
            by_sp.iter().map(|(sp, ns)| (sp.clone(), Tesseract::new(build_agent_store(ns)))).collect();

        let qas: Vec<&Value> = qa_of(conv).iter().filter(|qa| judge_cat(qa).is_some()).collect();
        let questions = question_texts(conv)?;
        let qvecs =
            cached_embeddings(llm, &cfg.cache_dir, &format!("conv{conv_idx}_questions_all"), &questions).await?;
        let qvec_by_text: IndexMap<&String, &Vec<f64>> = questions.iter().zip(&qvecs).collect();
        let qvec_of = |q: &String| qvec_by_text.get(q).copied().ok_or_else(|| HarnessError::new("KeyError", q));

        let mut pooled_t_cache: IndexMap<String, Ranked> = IndexMap::new();
        // ---------------- H9a
        for qa in &qas {
            let q = qa.get("question").map(py_str).unwrap_or_default();
            let gold = qa.get("answer").cloned().unwrap_or_else(|| Value::from(""));
            let cat = judge_cat(qa).expect("filtered");
            let qvec = qvec_of(&q)?;
            let named = named_speakers(&q, speakers);
            let ev_sp = evidence_speakers(qa, &dia_speaker);
            let sub_sp = substring_gold_speakers(&gold, nodes);

            let pooled_t = tess(&t_pool, &q, qvec, conv_idx)?;
            pooled_t_cache.insert(q.clone(), pooled_t.clone());
            let mut ag_t: IndexMap<&String, Ranked> = IndexMap::new();
            for sp in speakers {
                let t = t_ag.get(sp).ok_or_else(|| HarnessError::new("KeyError", sp))?;
                ag_t.insert(sp, tess(t, &q, qvec, conv_idx)?);
            }
            let pooled_e = cosine_topk(nodes, qvec, TOP_K)?;
            let mut ag_e: IndexMap<&String, Ranked> = IndexMap::new();
            for sp in speakers {
                let own = by_sp.get(sp).ok_or_else(|| HarnessError::new("KeyError", sp))?;
                ag_e.insert(sp, cosine_topk(own, qvec, TOP_K)?);
            }
            let (a, b) = match speakers.as_slice() {
                [a, b, ..] => (a, b),
                _ => return Err(HarnessError::new("IndexError", "list index out of range")),
            };
            let get = |m: &IndexMap<&String, Ranked>, k: &String| {
                m.get(k).cloned().ok_or_else(|| HarnessError::new("KeyError", k))
            };
            let fed = |m: &IndexMap<&String, Ranked>| -> HResult<(Ranked, Ranked)> {
                let (x, y) = (get(m, a)?, get(m, b)?);
                Ok((x.into_iter().take(FED_K).collect(), y.into_iter().take(FED_K).collect()))
            };
            let (ta, tb) = fed(&ag_t)?;
            let (ea, eb) = fed(&ag_e)?;
            let variants: Vec<(&str, Ranked)> = vec![
                ("tess_pooled", pooled_t.clone()),
                ("tess_local", if named.len() == 1 { get(&ag_t, &named[0])? } else { pooled_t.clone() }),
                ("tess_local_oracle", if ev_sp.len() == 1 { get(&ag_t, &ev_sp[0])? } else { pooled_t.clone() }),
                ("tess_federated", merge_by_charge(&ta, &tb, TOP_K)),
                ("tess_federated_rr", merge_round_robin(&ta, &tb, TOP_K)),
                ("embed_pooled", pooled_e.clone()),
                ("embed_local", if named.len() == 1 { get(&ag_e, &named[0])? } else { pooled_e.clone() }),
                ("embed_local_oracle", if ev_sp.len() == 1 { get(&ag_e, &ev_sp[0])? } else { pooled_e.clone() }),
                ("embed_federated", merge_by_charge(&ea, &eb, TOP_K)),
            ];
            let mut row = obj([
                ("conv", Value::from(conv_idx)),
                ("cat", Value::from(cat)),
                ("q", Value::from(q.clone())),
                ("gold", gold.clone()),
                ("named", json_list(&named)),
                ("evidence_speakers", json_list(&ev_sp)),
                ("substring_gold_speakers", json_list(&sub_sp)),
            ]);
            let m = row.as_object_mut().expect("object");
            for (name, res) in &variants {
                m.insert(name.to_string(), pair(hits(res, &gold)));
            }
            rows_a.push(row);
        }

        // ---------------- H9b (single_hop, exactly one named speaker)
        let probe: Vec<&Value> = qas
            .iter()
            .copied()
            .filter(|qa| {
                judge_cat(qa) == Some("single_hop")
                    && named_speakers(&qa.get("question").map(py_str).unwrap_or_default(), speakers).len() == 1
            })
            .collect();
        let orig_name: IndexMap<String, &String> =
            [(sp_a.to_lowercase(), &sp_a), (sp_b.to_lowercase(), &sp_b)].into_iter().collect();
        let mut swapped_qs = Vec::new();
        for qa in &probe {
            let q = qa.get("question").map(py_str).unwrap_or_default();
            let nm = named_speakers(&q, speakers).remove(0);
            let other = if nm == sp_a.to_lowercase() { &sp_b } else { &sp_a };
            let src = orig_name.get(&nm).ok_or_else(|| HarnessError::new("KeyError", &nm))?;
            swapped_qs.push(swap_name(&q, src, other));
        }
        let svecs = if probe.is_empty() {
            Vec::new()
        } else {
            cached_embeddings(llm, &cfg.cache_dir, &format!("conv{conv_idx}_questions_swapped"), &swapped_qs).await?
        };
        for ((qa, sq), svec) in probe.iter().zip(&swapped_qs).zip(&svecs) {
            let q = qa.get("question").map(py_str).unwrap_or_default();
            let gold = qa.get("answer").cloned().unwrap_or_else(|| Value::from(""));
            let qvec = qvec_of(&q)?;
            let nm = named_speakers(&q, speakers).remove(0);
            let other = if nm == sp_a.to_lowercase() { sp_b.to_lowercase() } else { sp_a.to_lowercase() };
            let o_t = pooled_t_cache.get(&q).cloned().ok_or_else(|| HarnessError::new("KeyError", &q))?;
            let s_t = tess(&t_pool, sq, svec, conv_idx)?;
            let o_e = cosine_topk(nodes, qvec, TOP_K)?;
            let s_e = cosine_topk(nodes, svec, TOP_K)?;
            let mut row = obj([
                ("conv", Value::from(conv_idx)),
                ("q", Value::from(q.clone())),
                ("swapped_q", Value::from(sq.clone())),
                ("gold", gold.clone()),
                ("named", Value::from(nm.clone())),
                ("evidence_speakers", json_list(&evidence_speakers(qa, &dia_speaker))),
            ]);
            let m = row.as_object_mut().expect("object");
            for (name, (o, s)) in [
                ("embed", (o_e, s_e)),
                ("tesseract", (o_t.clone(), s_t.clone())),
                ("tesseract+speaker", (speaker_boost(&o_t, &nm), speaker_boost(&s_t, &other))),
            ] {
                m.insert(format!("{name}_orig"), pair(hits(&o, &gold)));
                m.insert(format!("{name}_swap"), pair(hits(&s, &gold)));
            }
            rows_b.push(row);
        }
        writeln!(
            out,
            "conv {conv_idx}: {} H9a questions, {} H9b probes ({:.0}s)",
            qas.len(),
            probe.len(),
            t0.elapsed().as_secs_f64()
        )?;
    }
    write!(out, "{}", report(&rows_a, &rows_b))?;
    if !cfg.out_json.is_empty() {
        let doc = obj([("h9a", Value::Array(rows_a.clone())), ("h9b", Value::Array(rows_b.clone()))]);
        std::fs::write(&cfg.out_json, dumps(&doc, &JsonFormat::python_default()))?;
    }
    Ok((rows_a, rows_b))
}

/// `pct(num, den)`: `"{:5.1f}"` of the percentage, `"  n/a"` for an empty denominator.
pub fn pct(num: i64, den: i64) -> String {
    if den != 0 {
        format!("{:5.1}", 100.0 * num as f64 / den as f64)
    } else {
        "  n/a".into()
    }
}

fn int_at(r: &Value, key: &str, i: usize) -> i64 {
    r.get(key).and_then(|v| v.get(i)).and_then(Value::as_i64).unwrap_or(0)
}

fn strs(r: &Value, key: &str) -> Vec<String> {
    r.get(key).and_then(Value::as_array).map(|a| a.iter().map(py_str).collect()).unwrap_or_default()
}

fn cat_rows<'a>(rows: &'a [Value], cat: Option<&str>) -> Vec<&'a Value> {
    rows.iter().filter(|r| cat.is_none_or(|c| r.get("cat").and_then(Value::as_str) == Some(c))).collect()
}

const SPLIT_VARIANTS: [&str; 9] = [
    "tess_pooled",
    "tess_local",
    "tess_local_oracle",
    "tess_federated",
    "tess_federated_rr",
    "embed_pooled",
    "embed_local",
    "embed_local_oracle",
    "embed_federated",
];

fn cats_and_all() -> [Option<&'static str>; 4] {
    [Some(JUDGE_CATS[0]), Some(JUDGE_CATS[1]), Some(JUDGE_CATS[2]), None]
}

fn variant_line(v: &str, rows: &[Value], only_one_named: bool) -> String {
    let mut line = format!("{v:20}");
    for cat in cats_and_all() {
        let rs: Vec<&Value> =
            cat_rows(rows, cat).into_iter().filter(|r| !only_one_named || strs(r, "named").len() == 1).collect();
        let n = rs.len() as i64;
        let a: i64 = rs.iter().map(|r| int_at(r, v, 0)).sum();
        let b: i64 = rs.iter().map(|r| int_at(r, v, 1)).sum();
        line.push_str(&format!("{:>28}", format!("{} / {} (n={n})", pct(a, n), pct(b, n))));
    }
    line
}

/// `report(rows_a, rows_b)`: the H9a tables, the routing statistics and the H9b table.
pub fn report(rows_a: &[Value], rows_b: &[Value]) -> String {
    let mut s = String::new();
    s.push_str("\n=== H9a per-agent memory split (recall@10 / recall@50) ===\n");
    s.push_str(&format!("{:20}", "variant"));
    for c in JUDGE_CATS {
        s.push_str(&format!("{c:>28}"));
    }
    s.push_str(&format!("{:>28}\n", "ALL"));
    for v in SPLIT_VARIANTS {
        s.push_str(&variant_line(v, rows_a, false));
        s.push('\n');
    }

    s.push_str("\n--- routing statistics (questions naming exactly one speaker) ---\n");
    s.push_str(&format!(
        "{:12}{:>6}{:>9}{:>9}{:>9}{:>26}{:>26}\n",
        "category", "n", "names 1", "names 0", "names 2", "gold by OTHER (evidence)", "gold only OTHER (substr)"
    ));
    for cat in cats_and_all() {
        let rs = cat_rows(rows_a, cat);
        let one: Vec<&&Value> = rs.iter().filter(|r| strs(r, "named").len() == 1).collect();
        let ev: Vec<&&&Value> = one.iter().filter(|r| !strs(r, "evidence_speakers").is_empty()).collect();
        let other_ev = ev.iter().filter(|r| !strs(r, "evidence_speakers").contains(&strs(r, "named")[0])).count();
        let sub: Vec<&&&Value> = one.iter().filter(|r| !strs(r, "substring_gold_speakers").is_empty()).collect();
        let other_sub =
            sub.iter().filter(|r| !strs(r, "substring_gold_speakers").contains(&strs(r, "named")[0])).count();
        let names0 = rs.iter().filter(|r| strs(r, "named").is_empty()).count();
        let names2 = rs.iter().filter(|r| strs(r, "named").len() == 2).count();
        s.push_str(&format!(
            "{:12}{:6}{:9}{:9}{:9}{:>26}{:>26}\n",
            cat.unwrap_or("ALL"),
            rs.len(),
            one.len(),
            names0,
            names2,
            format!("{}% of {}", pct(other_ev as i64, ev.len() as i64), ev.len()),
            format!("{}% of {}", pct(other_sub as i64, sub.len() as i64), sub.len()),
        ));
    }

    s.push_str("\n--- H9a restricted to questions naming exactly one speaker ---\n");
    for v in SPLIT_VARIANTS {
        s.push_str(&variant_line(v, rows_a, true));
        s.push('\n');
    }

    s.push_str("\n=== H9b speaker-swap probe (single_hop, exactly one named speaker) ===\n");
    let n = rows_b.len() as i64;
    s.push_str(&format!("n = {n}\n"));
    s.push_str(&format!(
        "{:20}{:>11}{:>11}{:>9}{:>11}{:>11}{:>9}{:>32}\n",
        "variant",
        "orig r@10",
        "swap r@10",
        "gap@10",
        "orig r@50",
        "swap r@50",
        "gap@50",
        "blind@10 (swap hit | orig hit)"
    ));
    for v in ["embed", "tesseract", "tesseract+speaker"] {
        let (ok, sk) = (format!("{v}_orig"), format!("{v}_swap"));
        let sum = |k: &str, i: usize| rows_b.iter().map(|r| int_at(r, k, i)).sum::<i64>();
        let (o10, s10, o50, s50) = (sum(&ok, 0), sum(&sk, 0), sum(&ok, 1), sum(&sk, 1));
        let both = rows_b.iter().filter(|r| int_at(r, &ok, 0) != 0 && int_at(r, &sk, 0) != 0).count() as i64;
        s.push_str(&format!(
            "{v:20}{:>11}{:>11}{:>9}{:>11}{:>11}{:>9}{:>32}\n",
            pct(o10, n),
            pct(s10, n),
            pct(o10 - s10, n),
            pct(o50, n),
            pct(s50, n),
            pct(o50 - s50, n),
            format!("{}% of {o10}", pct(both, o10))
        ));
    }
    let ev_other = rows_b
        .iter()
        .filter(|r| {
            let ev = strs(r, "evidence_speakers");
            let named = r.get("named").map(py_str).unwrap_or_default();
            !ev.is_empty() && !ev.contains(&named)
        })
        .count();
    s.push_str(&format!("probes whose evidence message is spoken by the OTHER (un-named) speaker: {ev_other}/{n}\n"));
    s
}

// ============================================================================================================
// combined stack (MODE=combined)
// ============================================================================================================

/// `build_pair_nodes(http, conv_idx, messages, nodes, window=2)` of the combined stack: PAIR nodes
/// `"[prev speaker] prev\n[speaker] this"` (embeddings cached as `conv<i>_pairs<window>`), id
/// `pair<window>_<conv>_<k>`; the anchor (replier) is the message whose index ends the first member's id.
pub async fn build_pair_nodes(
    llm: &dyn Llm,
    cache_dir: &std::path::Path,
    conv_idx: usize,
    messages: &[Message],
    nodes: &[NodeRef],
    window: usize,
) -> HResult<Vec<NodeRef>> {
    let (texts, members) = pair_texts_and_members(messages, nodes)?;
    let embs = cached_embeddings(llm, cache_dir, &format!("conv{conv_idx}_pairs{window}"), &texts).await?;
    make_pair_nodes(&format!("pair{window}"), conv_idx, &texts, embs, &members, |_, mem| {
        let idx_text = mem[0].rsplit('_').next().unwrap_or("");
        let idx = neuralgraph_memory::pycompat::py_int_parse(idx_text).ok_or_else(|| {
            HarnessError::new("ValueError", format!("invalid literal for int() with base 10: '{idx_text}'"))
        })?;
        let i = if idx < 0 { idx + nodes.len() as i64 } else { idx };
        usize::try_from(i)
            .ok()
            .and_then(|i| nodes.get(i))
            .cloned()
            .ok_or_else(|| HarnessError::new("IndexError", "list index out of range"))
    })
}

/// `append_extra(base, extra_ranked, k)`: `base` plus the first `k` candidates of `extra_ranked` not in it.
pub fn append_extra(base: &[(NodeRef, f64)], extra_ranked: &[(NodeRef, f64)], k: usize) -> Ranked {
    let have: IndexSet<String> = base.iter().map(|(n, _)| id_of(n)).collect();
    let mut out: Ranked = base.to_vec();
    out.extend(extra_ranked.iter().filter(|(n, _)| !have.contains(&id_of(n))).take(k).cloned());
    out
}

/// The combined-stack variants, in report order.
pub const COMBINED_VARIANTS: [&str; 19] = [
    "KG+regex union: VG+tess50 + pairs",
    "KG: graph channel only",
    "KG: mega_VG50 + pairs",
    "KG: mega_VG+tess50 + pairs",
    "mega_VG50 + pair backfill",
    "mega_VG+tess50 + pair backfill",
    "mega_VK (vec+bm25 rrf)",
    "mega_VG (vec+graph rrf)",
    "mega_VKG (vec+bm25+graph)",
    "mega_VKG graph x2",
    "mega_VKG + tess channel",
    "embed_local",
    "embed_local+pairs",
    "tess_local",
    "tess_local+pairs",
    "stack_embedpairs_then_tess",
    "stack_tess_then_embedpairs",
    "tess_local+embed_union",
    "embed_local_top80",
];

fn mega_err(e: String) -> HarnessError {
    HarnessError::new("KeyError", e)
}

/// `combined_main()`: the combined stack over the selected conversations; prints progress and the table, writes
/// `OUT_JSON`. Returns the rows.
pub async fn main_combined(cfg: &TwoAgentConfig, llm: &dyn Llm, out: &mut dyn Write) -> HResult<Vec<Value>> {
    let data: Value = serde_json::from_str(&std::fs::read_to_string(&cfg.dataset)?)?;
    let convs = data.as_array().ok_or_else(|| HarnessError::new("TypeError", "dataset is not a list"))?;
    let mut rows = Vec::new();
    let t0 = std::time::Instant::now();
    let w = TOP_K + PAIR_EXTRA;
    for (conv_idx, conv) in convs.iter().enumerate() {
        if !cfg.only_conv.is_empty() && !cfg.only_conv.contains(&(conv_idx as i64)) {
            continue;
        }
        let ing = ingest(llm, &cfg.cache_dir, conv_idx, conv).await?;
        let (nodes, speakers) = (&ing.nodes, &ing.speakers);
        let messages = flatten_messages(conv);
        let pairs = build_pair_nodes(llm, &cfg.cache_dir, conv_idx, &messages, nodes, 2).await?;
        let node_by_id: IndexMap<String, NodeRef> = nodes.iter().map(|n| (id_of(n), n.clone())).collect();
        let speaker_of = |n: &NodeRef| n.borrow().speaker_id().to_lowercase();
        let by_sp: IndexMap<&String, Vec<NodeRef>> =
            speakers.iter().map(|sp| (sp, nodes.iter().filter(|n| speaker_of(n) == *sp).cloned().collect())).collect();
        let pairs_by_sp: IndexMap<&String, Vec<NodeRef>> =
            speakers.iter().map(|sp| (sp, pairs.iter().filter(|n| speaker_of(n) == *sp).cloned().collect())).collect();
        let t_pool = Tesseract::new(ing.storage.clone());
        let t_ag: IndexMap<&String, Tesseract> =
            speakers.iter().map(|sp| (sp, Tesseract::new(build_agent_store(&by_sp[sp])))).collect();
        let mega = MegaIndex::new(nodes, &pairs, None, 0.5).map_err(mega_err)?;
        let kg_path = cfg.kg_cache_dir.join(format!("conv{conv_idx}.json"));
        let (mega_kg, mega_kgu) = if kg_path.exists() {
            let kg: Value = serde_json::from_str(&std::fs::read_to_string(&kg_path)?)?;
            let mut kgu = kg.clone();
            match kgu.as_object_mut() {
                Some(m) => {
                    m.insert("_union_regex".into(), Value::Bool(true));
                }
                None => return Err(HarnessError::new("TypeError", "unsupported operand type(s) for |")),
            }
            (
                Some(MegaIndex::new(nodes, &pairs, Some(&kg), 0.5).map_err(mega_err)?),
                Some(MegaIndex::new(nodes, &pairs, Some(&kgu), 0.5).map_err(mega_err)?),
            )
        } else {
            (None, None)
        };

        let qas: Vec<&Value> = qa_of(conv).iter().filter(|qa| judge_cat(qa).is_some()).collect();
        let questions = question_texts(conv)?;
        let qvecs =
            cached_embeddings(llm, &cfg.cache_dir, &format!("conv{conv_idx}_questions_all"), &questions).await?;
        let qvec_by_text: IndexMap<&String, &Vec<f64>> = questions.iter().zip(&qvecs).collect();
        for qa in &qas {
            let q = qa.get("question").map(py_str).unwrap_or_default();
            let gold = qa.get("answer").cloned().unwrap_or_else(|| Value::from(""));
            let cat = judge_cat(qa).expect("filtered");
            let qvec: &[f64] = qvec_by_text.get(&q).ok_or_else(|| HarnessError::new("KeyError", &q))?;
            let named = named_speakers(&q, speakers);
            let (pool_nodes, pool_pairs, tsr): (&[NodeRef], &[NodeRef], &Tesseract) = if named.len() == 1 {
                let sp = &named[0];
                (&by_sp[sp], &pairs_by_sp[sp], &t_ag[sp]) // local pool
            } else {
                (nodes, &pairs, &t_pool) // pooled fallback (0 or 2 speakers named)
            };
            let embed_local = cosine_topk(pool_nodes, qvec, TOP_K)?;
            let both: Vec<NodeRef> = pool_nodes.iter().chain(pool_pairs).cloned().collect();
            let mut embed_pairs = unpair(&cosine_topk(&both, qvec, 2 * TOP_K)?, &node_by_id, &mut IndexSet::new())?;
            embed_pairs.truncate(TOP_K);
            let mut tess_local = tess(tsr, &q, qvec, conv_idx)?;
            tess_local.truncate(TOP_K);
            let backfill = |base: &Ranked| -> HResult<Ranked> {
                let mut seen: IndexSet<String> = base.iter().map(|(n, _)| id_of(n)).collect();
                let mut extra = unpair(&cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA)?, &node_by_id, &mut seen)?;
                extra.truncate(PAIR_EXTRA);
                Ok(extra)
            };
            let pair_backfill = {
                let mut seen: IndexSet<String> = tess_local.iter().map(|(n, _)| id_of(n)).collect();
                unpair(&cosine_topk(pool_pairs, qvec, 2 * PAIR_EXTRA)?, &node_by_id, &mut seen)?
            };
            let pool_ids: IndexSet<String> = pool_nodes.iter().map(id_of).collect();
            let tess_ids: Vec<String> = tess_local.iter().map(|(n, _)| id_of(n)).collect();
            let extra_tess = [tess_ids.clone()];
            let g2: IndexMap<String, f64> = [("G".to_string(), 2.0)].into_iter().collect();
            let run =
                |m: &MegaIndex, k: usize, ch: &str, weights: Option<&IndexMap<String, f64>>, extra: &[Vec<String>]| {
                    m.retrieve(&q, qvec, Some(&pool_ids), k, ch, weights, extra, 100).map_err(mega_err)
                };
            let with_pairs = |base: Ranked| -> HResult<Ranked> {
                let extra = backfill(&base)?;
                let mut out = base;
                out.extend(extra);
                Ok(out)
            };
            let mega_vk = run(&mega, w, "VK", None, &[])?;
            let mega_vkg = run(&mega, w, "VKG", None, &[])?;
            let mega_vkg_tess = run(&mega, w, "VKG", None, &extra_tess)?;
            let mega_vg = run(&mega, w, "VG", None, &[])?;
            let mega_vkg_g2 = run(&mega, w, "VKG", Some(&g2), &[])?;
            let mega_vg_pairs = with_pairs(run(&mega, TOP_K, "VG", None, &[])?)?;
            let mega_vgt_pairs = with_pairs(run(&mega, TOP_K, "VG", None, &extra_tess)?)?;
            let (kg_vgt_pairs, kg_g_only, kg_vg_pairs) = match &mega_kg {
                Some(mk) => (
                    with_pairs(run(mk, TOP_K, "VG", None, &extra_tess)?)?,
                    run(mk, w, "G", None, &[])?,
                    with_pairs(run(mk, TOP_K, "VG", None, &[])?)?,
                ),
                None => (Vec::new(), Vec::new(), Vec::new()),
            };
            let kgu_pairs = match &mega_kgu {
                Some(mk) => with_pairs(run(mk, TOP_K, "VG", None, &extra_tess)?)?,
                None => Vec::new(),
            };
            let mut tess_local_pairs = tess_local.clone();
            tess_local_pairs.extend(pair_backfill.iter().take(PAIR_EXTRA).cloned());
            let variants: [Ranked; 19] = [
                kgu_pairs,
                kg_g_only,
                kg_vg_pairs,
                kg_vgt_pairs,
                mega_vg_pairs,
                mega_vgt_pairs,
                mega_vk,
                mega_vg,
                mega_vkg,
                mega_vkg_g2,
                mega_vkg_tess,
                embed_local.clone(),
                embed_pairs.clone(),
                tess_local.clone(),
                tess_local_pairs,
                append_extra(&embed_pairs, &tess_local, PAIR_EXTRA),
                append_extra(&tess_local, &embed_pairs, PAIR_EXTRA),
                append_extra(&tess_local, &embed_local, PAIR_EXTRA),
                cosine_topk(pool_nodes, qvec, TOP_K + PAIR_EXTRA)?,
            ];
            let mut row = obj([
                ("conv", Value::from(conv_idx)),
                ("cat", Value::from(cat)),
                ("q", Value::from(q.clone())),
                ("named", json_list(&named)),
            ]);
            let m: &mut Map<String, Value> = row.as_object_mut().expect("object");
            for (name, res) in COMBINED_VARIANTS.iter().zip(&variants) {
                m.insert(name.to_string(), Value::from(hits3(res, &gold).to_vec()));
            }
            rows.push(row);
        }
        writeln!(out, "conv {conv_idx}: {} questions ({:.0}s)", qas.len(), t0.elapsed().as_secs_f64())?;
    }
    write!(out, "{}", combined_report(&rows))?;
    if !cfg.out_json.is_empty() {
        let doc = obj([("combined", Value::Array(rows.clone()))]);
        std::fs::write(&cfg.out_json, dumps(&doc, &JsonFormat::python_default()))?;
    }
    Ok(rows)
}

/// The combined-stack table.
pub fn combined_report(rows: &[Value]) -> String {
    let mut s = String::from(
        "\n=== Combined stack (recall@10 / @50 / @all; local pool if exactly one speaker named, else pooled) ===\n",
    );
    s.push_str(&format!("{:28}", "variant"));
    for c in JUDGE_CATS {
        s.push_str(&format!("{c:>30}"));
    }
    s.push_str(&format!("{:>30}\n", "ALL"));
    for v in COMBINED_VARIANTS {
        let mut line = format!("{v:28}");
        for cat in cats_and_all() {
            let rs = cat_rows(rows, cat);
            let n = rs.len() as i64;
            let cell = (0..3).map(|i| pct(rs.iter().map(|r| int_at(r, v, i)).sum(), n)).collect::<Vec<_>>().join(" / ");
            line.push_str(&format!("{:>30}", format!("{cell} (n={n})")));
        }
        s.push_str(&line);
        s.push('\n');
    }
    s
}

/// The script entry: `combined_main()` when `MODE=combined`, else `main()`.
pub async fn main_with(cfg: &TwoAgentConfig, llm: &dyn Llm, out: &mut dyn Write) -> HResult<()> {
    if cfg.mode == "combined" {
        main_combined(cfg, llm, out).await.map(|_| ())
    } else {
        main_split(cfg, llm, out).await.map(|_| ())
    }
}
