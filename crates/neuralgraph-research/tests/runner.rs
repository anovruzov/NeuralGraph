//! The LoCoMo harnesses (`benchmarks::runner`, `two_agent_eval`, `retrieval_eval`, `call_center`) against the
//! Python reference: `golden/runner.json` and `golden/runner_routing.json` from `golden/gen_runner.py`.
//!
//! The end-to-end replays run the whole `run_benchmark` of the runner (every retrieval mode, the H5 flags, the
//! OpenAI judge path) and of the call-center script with a scripted model, and compare stdout, the report and the
//! sequence of prompts with Python's. The cosine rankings carry float32 values computed through Accelerate, so the
//! fixture comparisons are exact on macOS only ([`neuralgraph_research::numpy::BITWISE_NUMPY`]).

mod common;

use async_trait::async_trait;
use common::{assert_json_eq, load, pin_now, FIXED_NOW};
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::fake_embedding;
use neuralgraph_memory::pycompat::{dumps, JsonFormat};
use neuralgraph_research::benchmarks::call_center::{self, CallCenterModel};
use neuralgraph_research::benchmarks::pyharness::{json_indent, set_fixed_perf_counter};
use neuralgraph_research::benchmarks::retrieval_eval::cached_embeddings;
use neuralgraph_research::benchmarks::runner::*;
use neuralgraph_research::benchmarks::two_agent_eval as ta;
use neuralgraph_research::llm_backend::ScriptedLlm;
use neuralgraph_research::numpy::BITWISE_NUMPY;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::data_types::{node_ref, NodeRef};
use neuralgraph_research::retrieval::dialogue_linker::DialogueLinker;
use neuralgraph_research::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use neuralgraph_research::retrieval::tesseract::Tesseract;
use serde_json::Value;
use sha1::{Digest, Sha1};
use std::rc::Rc;
use std::sync::LazyLock;

static FX: LazyLock<Value> = LazyLock::new(|| load("runner.json"));
static LOCOMO: LazyLock<Value> = LazyLock::new(|| {
    let path = neuralgraph_research::benchmarks::pyharness::repo_root().join("research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("dataset")).expect("json")
});

fn sha1_hex(text: &str) -> String {
    Sha1::digest(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

fn jsha(v: &Value) -> String {
    sha1_hex(&dumps(v, &JsonFormat::python_default()))
}

fn dim() -> usize {
    FX["dim"].as_u64().unwrap() as usize
}

/// The scripted model of the generator: replies picked by the sha1 of the prompt.
fn scripted_reply(prompt: &str) -> String {
    let replies = FX["replies"].as_array().unwrap();
    let h = u64::from_str_radix(&sha1_hex(prompt)[..8], 16).unwrap();
    replies[(h % replies.len() as u64) as usize].as_str().unwrap().to_string()
}

fn scripted_llm() -> ScriptedLlm {
    let d = dim();
    ScriptedLlm::new(|p| Ok(scripted_reply(p))).with_embedder(move |t| Ok(fake_embedding(t, d)))
}

fn conv(i: usize) -> &'static Value {
    &LOCOMO[i]
}

fn ids(res: &[(NodeRef, f64)]) -> Value {
    Value::from(res.iter().map(|(n, c)| serde_json::json!([n.borrow().node_id.clone(), c])).collect::<Vec<_>>())
}

fn runner_nodes(conv_idx: usize) -> Vec<NodeRef> {
    flatten_messages(conv(conv_idx))
        .iter()
        .enumerate()
        .map(|(i, m)| node_ref(build_message_node(conv_idx, i, m, fake_embedding(&m.text, dim())).unwrap()))
        .collect()
}

#[test]
fn check_gold_in_memories_substring_matches_python() {
    let cases = FX["gold"]["locomo"].as_array().unwrap();
    let mut texts_by_conv = IndexMap::new();
    for c in cases {
        let ci = c["conv"].as_u64().unwrap() as usize;
        let msgs = texts_by_conv.entry(ci).or_insert_with(|| flatten_messages(conv(ci)));
        let mems: Vec<String> =
            c["idx"].as_array().unwrap().iter().map(|i| msgs[i.as_u64().unwrap() as usize].text.clone()).collect();
        let got: Vec<Value> = [1, 5, 10, 50]
            .iter()
            .map(|k| {
                let (f, r) = check_gold_in_memories_substring(&c["gold"], &mems, *k);
                serde_json::json!([f, r])
            })
            .collect();
        assert_json_eq(&Value::from(got), &c["out"], "check_gold locomo");
    }
    let edge_texts: Vec<String> =
        FX["gold"]["edge_texts"].as_array().unwrap().iter().map(|t| t.as_str().unwrap().to_string()).collect();
    for c in FX["gold"]["edges"].as_array().unwrap() {
        let got: Vec<Value> = [1, 3, 6, 10]
            .iter()
            .map(|k| {
                let (f, r) = check_gold_in_memories_substring(&c["gold"], &edge_texts, *k);
                serde_json::json!([f, r])
            })
            .collect();
        assert_json_eq(&Value::from(got), &c["out"], &format!("check_gold edge {}", c["gold"]));
    }
}

fn metrics_of(v: &Value) -> IndexMap<String, CategoryMetrics> {
    let list = |m: &Value, k: &str| m[k].as_array().unwrap().iter().map(|x| x.as_i64().unwrap()).collect();
    v.as_object()
        .unwrap()
        .iter()
        .map(|(cat, m)| {
            (
                cat.clone(),
                CategoryMetrics {
                    recall_at_10: list(m, "recall_at_10"),
                    recall_at_50: list(m, "recall_at_50"),
                    oracle_rank: list(m, "oracle_rank"),
                    filter_drop_count: list(m, "filter_drop_count"),
                    total_candidates: list(m, "total_candidates"),
                },
            )
        })
        .collect()
}

#[test]
fn compute_retrieval_summary_matches_python() {
    for c in FX["summary"].as_array().unwrap() {
        assert_json_eq(&compute_retrieval_summary(&metrics_of(&c["metrics"])), &c["summary"], "summary");
    }
}

fn test_config(output: std::path::PathBuf) -> RunnerConfig {
    let root = neuralgraph_research::benchmarks::pyharness::repo_root();
    RunnerConfig {
        run_name: "test".into(),
        output_path: output,
        max_questions: 2000,
        only_cat: IndexSet::new(),
        only_conv: IndexSet::new(),
        graph_expansion: false,
        graph_seed_k: 10,
        graph_edge_decay: 0.8,
        use_speaker_profiles: false,
        retrieval_mode: "flat".into(),
        extra_candidates: 30,
        rerank_window: 50,
        speaker_boost: 1.5,
        open_domain_keep_context: false,
        open_domain_force_infer: 0,
        open_domain_infer_world: false,
        kg: false,
        openai_api_key: String::new(),
        judge_model: "gpt-4o".into(),
        answer_model: FX["answer_model"].as_str().unwrap().into(),
        base_url: FX["base_url"].as_str().unwrap().into(),
        retrieval_only: false,
        emb_cache_dir: root.join("research/results/emb_cache"),
        dataset: root.join("research/datasets/locomo10.json"),
        kg_cache_dir: root.join("research/results/kg_cache"),
    }
}

#[test]
fn save_results_document_matches_python() {
    pin_now(FIXED_NOW);
    let cfg = test_config("unused.json".into());
    for c in FX["save_results"].as_array().unwrap() {
        let results = c["results"].as_array().unwrap();
        let stats: CategoryStats = c["stats"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, s)| (k.clone(), (s["correct"].as_i64().unwrap(), s["total"].as_i64().unwrap())))
            .collect();
        let doc =
            results_document(&cfg, results, &stats, &metrics_of(&c["metrics"]), &PyDateTime::now_local().isoformat());
        assert_eq!(dumps(&doc, &json_indent(2)), c["file"].as_str().unwrap());
    }
}

#[tokio::test(flavor = "current_thread")]
async fn judge_label_parsing_and_auto_pass_match_python() {
    for c in FX["judge"]["labels"].as_array().unwrap() {
        assert_eq!(parse_judge_label(c[0].as_str().unwrap()), c[1].as_bool().unwrap(), "label {}", c[0]);
    }
    let cfg = test_config("unused.json".into());
    for c in FX["judge"]["judge"].as_array().unwrap() {
        let llm = scripted_llm();
        let mut out = Vec::new();
        let got =
            judge_answer(&llm, &cfg, c["q"].as_str().unwrap(), c["generated"].as_str().unwrap(), &c["gold"], &mut out)
                .await
                .unwrap();
        assert_eq!(got, c["correct"].as_bool().unwrap(), "judge {c}");
        assert_json_eq(&Value::from(llm.prompts()), &c["prompts"], "judge prompts");
    }
}

#[test]
fn routing_decisions_match_python_for_every_locomo_question() {
    let fx = load("runner_routing.json");
    for (ci, entry) in fx["routing"].as_array().unwrap().iter().enumerate() {
        let speakers: Vec<String> =
            entry["speakers"].as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_string()).collect();
        let qas = conv(ci)["qa"].as_array().unwrap();
        for (qa, expected) in qas.iter().zip(entry["questions"].as_array().unwrap()) {
            let q = qa["question"].as_str().unwrap();
            for (f, exp) in expected.as_array().unwrap().iter().enumerate() {
                let (before, list) = route_question(q, &speakers, f as i64);
                let after = upgrade_query_mode(&before, q);
                let mut got = vec![
                    Value::from(before.clone()),
                    Value::from(list),
                    Value::from(num_memories_needed(&before)),
                    Value::from(after.clone()),
                ];
                if f == 0 {
                    let mut modes = Vec::new();
                    for keep in [false, true] {
                        for world in [false, true] {
                            let (a, r) = answer_modes(&after, keep, world);
                            modes.push(serde_json::json!([a, r]));
                        }
                    }
                    got.push(Value::from(modes));
                }
                assert_json_eq(&Value::from(got), exp, &format!("routing conv {ci} {q:?} force {f}"));
            }
        }
    }
}

#[test]
fn message_nodes_context_and_direct_answers_match_python() {
    pin_now(FIXED_NOW);
    let fx = load("runner_routing.json");
    for entry in fx["context"].as_array().unwrap() {
        let ci = entry["conv"].as_u64().unwrap() as usize;
        let nodes = runner_nodes(ci);
        for (i, n) in nodes.iter().enumerate() {
            let meta = Value::Object(n.borrow().metadata.clone());
            if let Some(full) = entry["metadata_full"].get(i.to_string()) {
                assert_json_eq(&meta, full, &format!("metadata conv {ci} node {i}"));
            }
            assert_eq!(jsha(&meta), entry["metadata_sha"][i].as_str().unwrap(), "metadata sha conv {ci} node {i}");
        }
        for row in entry["questions"].as_array().unwrap() {
            let q = row["q"].as_str().unwrap();
            let reranked: Vec<(NodeRef, f64)> = row["idx"]
                .as_array()
                .unwrap()
                .iter()
                .zip(row["charges"].as_array().unwrap())
                .map(|(i, c)| (nodes[i.as_u64().unwrap() as usize].clone(), c.as_f64().unwrap()))
                .collect();
            for (mode, k) in [("TEMPORAL", 15), ("STRICT", 30)] {
                let ctx = build_answer_context(&reranked, k, mode);
                let context = ctx.context_parts.join("\n");
                let mems = Value::from(ctx.retrieved_memories.clone());
                let exp = &row[mode];
                if let Some(full) = exp.get("context") {
                    assert_eq!(&context, full.as_str().unwrap(), "context {q:?} {mode}");
                    assert_json_eq(&mems, &exp["memories"], "memories");
                }
                assert_eq!(sha1_hex(&context), exp["context_sha"].as_str().unwrap(), "context {q:?} {mode}");
                assert_eq!(jsha(&mems), exp["memories_sha"].as_str().unwrap(), "memories {q:?} {mode}");
                let direct = extract_direct_answer(q, mode, &ctx.candidate_memories);
                assert_json_eq(&serde_json::json!(direct), &exp["direct"], &format!("direct {q:?} {mode}"));
            }
            let ctx = build_answer_context(&reranked, 15, "X");
            let direct = extract_direct_answer(q, "OPEN_DOMAIN_WORLD", &ctx.candidate_memories);
            assert_json_eq(&serde_json::json!(direct), &row["OPEN_DOMAIN_WORLD"], "direct world");
        }
    }
}

#[test]
fn graph_expansion_pairs_and_cosine_match_python() {
    pin_now(FIXED_NOW);
    let fx = &FX["graph"];
    let ci = fx["conv"].as_u64().unwrap() as usize;
    let messages = flatten_messages(conv(ci));
    let nodes = runner_nodes(ci);
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let mut speaker_nodes: IndexMap<String, Vec<String>> = IndexMap::new();
    for n in &nodes {
        storage.save_node(n);
        let (sp, id) = (n.borrow().speaker_id().to_lowercase(), n.borrow().node_id.clone());
        speaker_nodes.entry(sp).or_default().push(id);
    }
    let all_ids: Vec<String> = nodes.iter().map(|n| n.borrow().node_id.clone()).collect();
    add_conversation_edges(&*storage, &speaker_nodes, &all_ids, true);
    let mut linker = DialogueLinker::new(&*storage);
    linker.process_dialogue_sequence(&nodes, &format!("conv_{ci}")).unwrap();
    let tess = Tesseract::new(storage.clone());
    let d = dim();
    let pairs =
        build_pair_nodes(ci, &messages, &nodes, |texts| Ok(texts.iter().map(|t| fake_embedding(t, d)).collect()))
            .unwrap();
    for (p, exp) in pairs.iter().zip(fx["pairs"].as_array().unwrap()) {
        let p = p.borrow();
        let got = serde_json::json!([p.node_id, p.content, Value::Object(p.metadata.clone())]);
        assert_json_eq(&got, exp, "pair node");
    }
    let node_by_id: IndexMap<String, NodeRef> = nodes.iter().map(|n| (n.borrow().node_id.clone(), n.clone())).collect();
    let first_speaker = messages[0].speaker.to_lowercase();
    for row in fx["questions"].as_array().unwrap() {
        let q = row["q"].as_str().unwrap();
        let qvec = fake_embedding(q, d);
        let res = tess.retrieve(q, &qvec, &format!("conv_{ci}"), None, 50, true).unwrap();
        for (seed_k, decay) in [(10, "0.8"), (3, "0.5"), (0, "0.8")] {
            let got = expand_via_graph(&*storage, &linker, &res, seed_k, decay.parse().unwrap());
            assert_json_eq(&ids(&got), &row[format!("graph_{seed_k}_{decay}")], &format!("graph {q:?} {seed_k}"));
        }
        if !BITWISE_NUMPY {
            continue;
        }
        let cos = cosine_topk(&nodes, &qvec, 20).unwrap();
        let got: Vec<Value> =
            cos.iter().map(|(n, c)| serde_json::json!([n.borrow().node_id.clone(), (*c as f32).to_bits()])).collect();
        assert_json_eq(&Value::from(got), &row["cosine"], "cosine");
        let pool: Vec<NodeRef> =
            pairs.iter().filter(|p| p.borrow().speaker_id().to_lowercase() == first_speaker).cloned().collect();
        let mut seen: IndexSet<String> = res.iter().take(10).map(|(n, _)| n.borrow().node_id.clone()).collect();
        let got = unpair(&cosine_topk(&pool, &qvec, 30).unwrap(), &node_by_id, &mut seen).unwrap();
        assert_json_eq(&ids(&got), &row["unpair"], "unpair");
        let both: Vec<NodeRef> = pairs.iter().chain(&nodes).cloned().collect();
        let got = unpair(&cosine_topk(&both, &qvec, 40).unwrap(), &node_by_id, &mut IndexSet::new()).unwrap();
        assert_json_eq(&ids(&got), &row["unpair_all"], "unpair all");
    }
}

#[tokio::test(flavor = "current_thread")]
async fn two_agent_helpers_match_python() {
    pin_now(FIXED_NOW);
    let fx = &FX["two_agent"];
    let (mut named_i, mut swap_i, mut ev_i, mut gp_i) = (0, 0, 0, 0);
    for ci in 0..LOCOMO.as_array().unwrap().len() {
        let c = &conv(ci)["conversation"];
        let speakers: Vec<String> = flatten_messages(conv(ci))
            .iter()
            .map(|m| m.speaker.to_lowercase())
            .collect::<IndexSet<_>>()
            .into_iter()
            .collect();
        let dia = ta::dia_speakers(c);
        let (sp_a, sp_b) = (c["speaker_a"].as_str().unwrap(), c["speaker_b"].as_str().unwrap());
        for qa in conv(ci)["qa"].as_array().unwrap() {
            let q = qa["question"].as_str().unwrap();
            let named = ta::named_speakers(q, &speakers);
            assert_json_eq(&Value::from(named.clone()), &fx["named"][named_i], "named");
            named_i += 1;
            if named.len() == 1 {
                let (src, other) = if named[0] == sp_a.to_lowercase() { (sp_a, sp_b) } else { (sp_b, sp_a) };
                let exp = &fx["swap"][swap_i];
                assert_eq!(exp[0].as_str().unwrap(), q);
                assert_eq!(ta::swap_name(q, src, other), exp[3].as_str().unwrap(), "swap {q:?}");
                swap_i += 1;
            }
            assert_json_eq(&Value::from(ta::evidence_speakers(qa, &dia)), &fx["evidence"][ev_i], "evidence");
            ev_i += 1;
            if ci < 2 {
                let gold = qa.get("answer").cloned().unwrap_or(Value::from(""));
                assert_json_eq(&Value::from(ta::gold_parts(&gold)), &fx["gold_parts"][gp_i][1], "gold_parts");
                gp_i += 1;
            }
        }
    }
    for exp in &fx["swap"].as_array().unwrap()[swap_i..] {
        let s = |i: usize| exp[i].as_str().unwrap();
        assert_eq!(ta::swap_name(s(0), s(1), s(2)), s(3), "swap edge {exp}");
    }
    for exp in &fx["gold_parts"].as_array().unwrap()[gp_i..] {
        assert_json_eq(&Value::from(ta::gold_parts(&exp[0])), &exp[1], "gold_parts edge");
    }
    let nodes = runner_nodes(0);
    for exp in fx["substring"].as_array().unwrap() {
        assert_json_eq(&Value::from(ta::substring_gold_speakers(&exp[0], &nodes)), &exp[1], "substring speakers");
    }
    let m = &fx["merge"];
    let mk = |v: &Value| -> Vec<(NodeRef, f64)> {
        v.as_array()
            .unwrap()
            .iter()
            .map(|p| (nodes[p[0].as_u64().unwrap() as usize].clone(), p[1].as_f64().unwrap()))
            .collect()
    };
    let (a, b) = (mk(&m["a"]), mk(&m["b"]));
    assert_json_eq(&ids(&ta::merge_by_charge(&a, &b, 50)), &m["by_charge"], "by_charge");
    assert_json_eq(&ids(&ta::merge_by_charge(&a, &b, 3)), &m["by_charge_3"], "by_charge_3");
    assert_json_eq(&ids(&ta::merge_round_robin(&a, &b, 50)), &m["round_robin"], "rr");
    assert_json_eq(&ids(&ta::merge_round_robin(&a, &b, 4)), &m["round_robin_4"], "rr4");
    let ab: Vec<(NodeRef, f64)> = a.iter().chain(&b).cloned().collect();
    let sp0 = nodes[0].borrow().speaker_id().to_lowercase();
    assert_json_eq(&ids(&ta::speaker_boost(&ab, &sp0)), &m["boost"], "boost");
    assert_json_eq(&ids(&ta::append_extra(&a, &b, 30)), &m["append"], "append");
    assert_json_eq(&ids(&ta::append_extra(&a, &b, 1)), &m["append_1"], "append_1");
    for (qa, exp) in conv(0)["qa"].as_array().unwrap().iter().zip(m["hits3"].as_array().unwrap()) {
        let gold = qa.get("answer").cloned().unwrap_or(Value::from(""));
        assert_json_eq(&Value::from(ta::hits3(&ab, &gold).to_vec()), exp, "hits3");
    }

    // two_agent_eval.build_pair_nodes through a cache file holding the pair embeddings
    let dir = tempfile::tempdir().unwrap();
    let messages = flatten_messages(conv(0));
    let (texts, _) = pair_texts_and_members(&messages, &nodes).unwrap();
    let vecs = Value::from(texts.iter().map(|t| Value::from(fake_embedding(t, dim()))).collect::<Vec<_>>());
    std::fs::write(dir.path().join("conv0_pairs2.json"), dumps(&vecs, &JsonFormat::python_default())).unwrap();
    let llm = ScriptedLlm::new(|_| Err("no model".into()));
    let pairs = ta::build_pair_nodes(&llm, dir.path(), 0, &messages, &nodes, 2).await.unwrap();
    for (p, exp) in pairs.iter().zip(fx["pairs2"].as_array().unwrap()) {
        let p = p.borrow();
        assert_json_eq(&serde_json::json!([p.node_id, p.content, Value::Object(p.metadata.clone())]), exp, "pairs2");
    }

    // what cached_embeddings writes, and that a cache of the wrong length is re-embedded
    let vectors = [vec![0.1], vec![0.1, -2.5e-07], vec![0.1, -2.5e-07, 3.0]];
    let llm = ScriptedLlm::new(|_| Err("no".into()))
        .with_embedder(move |t| Ok(vectors[["a", "b", "c"].iter().position(|x| *x == t).unwrap()].clone()));
    let texts = ["a".to_string(), "b".to_string(), "c".to_string()];
    std::fs::write(dir.path().join("k.json"), "[[1.0]]").unwrap();
    cached_embeddings(&llm, dir.path(), "k", &texts).await.unwrap();
    assert_eq!(
        std::fs::read_to_string(dir.path().join("k.json")).unwrap().trim_end(),
        fx["cache_file"].as_str().unwrap()
    );
    let again = ScriptedLlm::new(|_| Err("no".into()));
    assert_eq!(cached_embeddings(&again, dir.path(), "k", &texts).await.unwrap()[2], vec![0.1, -2.5e-07, 3.0]);
    assert!(again.calls.borrow().is_empty(), "a cache of the right length is reused");
}

fn prompt_shas(llm: &ScriptedLlm) -> Value {
    Value::from(llm.prompts().iter().map(|p| sha1_hex(p)).collect::<Vec<_>>())
}

#[tokio::test(flavor = "current_thread")]
async fn runner_end_to_end_replays_match_python() {
    if !BITWISE_NUMPY {
        return; // cosine back-fills and mega's vector channel rank by Accelerate float32 values
    }
    pin_now(FIXED_NOW);
    set_fixed_perf_counter(Some(0.0));
    let dir = tempfile::tempdir().unwrap();
    for run in FX["e2e"].as_array().unwrap() {
        let name = run["name"].as_str().unwrap();
        let mode = run["mode"].as_str().unwrap();
        let out_path = dir.path().join(format!("{name}.json"));
        let mut cfg = test_config(out_path.clone()).with_mode(mode);
        cfg.only_conv = [FX["e2e_conv"].as_i64().unwrap()].into_iter().collect();
        cfg.max_questions = FX["e2e_max"].as_i64().unwrap();
        if let Some(cats) = run.get("cats") {
            cfg.only_cat = cats.as_array().unwrap().iter().map(|c| c.as_str().unwrap().to_string()).collect();
        }
        cfg.open_domain_keep_context = run.get("keep").is_some_and(|v| v.as_bool().unwrap());
        cfg.open_domain_force_infer = run.get("force").map_or(0, |v| v.as_i64().unwrap());
        cfg.open_domain_infer_world = run.get("world").is_some_and(|v| v.as_bool().unwrap());
        if run.get("openai").is_some_and(|v| v.as_bool().unwrap()) {
            cfg.openai_api_key = "sk-test".into();
        }
        let llm = scripted_llm();
        let mut out = Vec::new();
        run_benchmark(&cfg, &llm, &mut out).await.unwrap();
        let stdout = String::from_utf8(out).unwrap().replace(&out_path.display().to_string(), "<OUT>");
        assert_eq!(stdout, run["stdout"].as_str().unwrap(), "stdout of {name}");
        let report: Value = serde_json::from_str(&std::fs::read_to_string(&out_path).unwrap()).unwrap();
        assert_json_eq(&report, &run["report"], &format!("report of {name}"));
        assert_json_eq(&prompt_shas(&llm), &run["prompts"], &format!("prompts of {name}"));
    }
    set_fixed_perf_counter(None);
}

struct ScriptedOllama;

#[async_trait(?Send)]
impl CallCenterModel for ScriptedOllama {
    async fn embed(&self, text: &str) -> Result<Vec<f64>, String> {
        Ok(fake_embedding(text, dim()))
    }
    async fn generate(&self, prompt: &str) -> Result<String, String> {
        PROMPTS.with(|p| p.borrow_mut().push(prompt.to_string()));
        Ok(scripted_reply(prompt))
    }
}

thread_local! {
    static PROMPTS: std::cell::RefCell<Vec<String>> = const { std::cell::RefCell::new(Vec::new()) };
}

#[tokio::test(flavor = "current_thread")]
async fn call_center_end_to_end_replay_matches_python() {
    pin_now(FIXED_NOW);
    set_fixed_perf_counter(Some(0.0));
    let dir = tempfile::tempdir().unwrap();
    let out_path = dir.path().join("cc.json");
    let llm = scripted_llm();
    let mut out = Vec::new();
    call_center::run_benchmark(&llm, &ScriptedOllama, &call_center::benchmark_path(), &out_path, &mut out)
        .await
        .unwrap();
    set_fixed_perf_counter(None);
    let fx = &FX["call_center"];
    let stdout = String::from_utf8(out).unwrap().replace(&out_path.display().to_string(), "<OUT>");
    assert_eq!(stdout, fx["stdout"].as_str().unwrap());
    let report: Value = serde_json::from_str(&std::fs::read_to_string(&out_path).unwrap()).unwrap();
    assert_json_eq(&report, &fx["report"], "call center report");
    // the fact-extraction prompts (llm_backend model) come first, then the answer prompts (Ollama)
    let mut all = llm.prompts();
    all.extend(PROMPTS.with(|p| p.borrow().clone()));
    let shas = Value::from(all.iter().map(|p| sha1_hex(p)).collect::<Vec<_>>());
    assert_json_eq(&shas, &fx["prompts"], "call center prompts");
}
