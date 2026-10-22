//! Parity of the LLM-facing retrieval modules with Python (`tests/golden/llm_modules.json`, written by
//! `gen_llm_modules.py`): prompts byte for byte, request parameters, response parsing and fallbacks of
//! `prompts`, `answering`, `reranker`, `speaker_profiles`, `llm_profile_extractor` and `llm_profile_query`.

mod common;

use async_trait::async_trait;
use common::{assert_json_eq, load, s, strings};
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{py_len, py_strip};
use neuralgraph_research::llm_backend::{EmbedOptions, GenerateOptions, HttpLlm, Llm};
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::answering::{self, generate_answer, get_embedding, AnsweringConfig};
use neuralgraph_research::retrieval::llm_profile_extractor::{
    self as extractor, extract_facts_parallel, extract_speaker_facts_llm, extract_speaker_facts_llm_at,
};
use neuralgraph_research::retrieval::llm_profile_query::{calculate_answer_confidence, query_profile_with_llm};
use neuralgraph_research::retrieval::prompts::{
    self, deduplicate_rewrites, format_retrieval_context, fuse_rewrite_results, generate_query_rewrites,
    get_rewrite_prompt, parse_rewrites, select_prompt_for_query, ALL_PROMPTS,
};
use neuralgraph_research::retrieval::query_router::QueryRouter;
use neuralgraph_research::retrieval::reranker::{
    create_reranker, llm_score_candidate, rerank_candidates, rerank_candidates_parallel, semantic_score,
    HybridReranker, LlmReranker, ParallelRerankOptions, Reranker, RerankerConfig, ScoreCache, SemanticBoostReranker,
};
use neuralgraph_research::retrieval::speaker_profiles::{FactExtractor, UniversalSpeakerProfiler};
use neuralgraph_research::retrieval::{node_ref, NeuralNode, NodeLayer, NodeRef};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::collections::VecDeque;
use std::sync::atomic::Ordering;
use std::sync::LazyLock;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

static FIXTURE: LazyLock<Value> = LazyLock::new(|| load("llm_modules.json"));

// ---------------------------------------------------------------------------------------------------------------
// Scripted model replaying the generator's outcomes
// ---------------------------------------------------------------------------------------------------------------

/// Answers from the fixture's outcome list (`{"text"}`, `{"error"}`, `{"sleep", ...}`, `{"vector"}`) and
/// records each call in the generator's format.
struct FixtureLlm {
    outcomes: RefCell<VecDeque<Value>>,
    calls: RefCell<Vec<Value>>,
}

impl FixtureLlm {
    fn new(outcomes: &Value) -> Self {
        let list = outcomes.as_array().cloned().unwrap_or_default();
        FixtureLlm { outcomes: RefCell::new(list.into()), calls: RefCell::new(Vec::new()) }
    }

    fn next(&self) -> Value {
        self.outcomes.borrow_mut().pop_front().expect("script ran out of outcomes")
    }

    fn assert_used_up(&self) {
        assert!(self.outcomes.borrow().is_empty(), "unused outcomes: {:?}", self.outcomes.borrow());
    }
}

#[async_trait(?Send)]
impl Llm for FixtureLlm {
    async fn generate(&self, prompt: &str, opts: &GenerateOptions) -> Result<String, String> {
        self.calls.borrow_mut().push(json!({
            "prompt": prompt,
            "model": opts.model,
            "base_url": opts.base_url,
            "temperature": opts.temperature,
            "max_tokens": opts.max_tokens,
            "timeout_seconds": opts.timeout_seconds,
        }));
        let o = self.next();
        if let Some(secs) = o.get("sleep").and_then(Value::as_f64) {
            tokio::time::sleep(std::time::Duration::from_secs_f64(secs)).await;
        }
        if let Some(e) = o.get("error") {
            return Err(s(e).to_string());
        }
        Ok(s(&o["text"]).to_string())
    }

    async fn embed(&self, text: &str, opts: &EmbedOptions) -> Result<Vec<f64>, String> {
        self.calls.borrow_mut().push(json!({
            "text": text,
            "model": opts.model,
            "base_url": opts.base_url,
            "timeout_seconds": opts.timeout_seconds,
        }));
        let o = self.next();
        if let Some(e) = o.get("error") {
            return Err(s(e).to_string());
        }
        Ok(o["vector"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect())
    }
}

/// Python passed some numeric keyword arguments as ints (`temperature=0`, `timeout_seconds=60`); the request
/// carries the same number either way, so calls are compared with every number as a float.
fn floats(v: &Value) -> Value {
    match v {
        Value::Number(n) => json!(n.as_f64().unwrap()),
        Value::Array(a) => Value::Array(a.iter().map(floats).collect()),
        Value::Object(m) => Value::Object(m.iter().map(|(k, v)| (k.clone(), floats(v))).collect()),
        other => other.clone(),
    }
}

fn assert_calls(llm: &FixtureLlm, expected: &Value, what: &str) {
    assert_json_eq(&floats(&json!(*llm.calls.borrow())), &floats(expected), what);
}

// ---------------------------------------------------------------------------------------------------------------
// Canned HTTP server (the Rust side of the generator's aiohttp server)
// ---------------------------------------------------------------------------------------------------------------

/// Serve one canned `(status, content type, body)` per connection; returns the base URL and a handle yielding
/// the recorded requests (`method`, `path`, `authorization`, `json`).
async fn serve(responses: Vec<(u16, String, String)>) -> (String, tokio::task::JoinHandle<Vec<Value>>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let handle = tokio::spawn(async move {
        let mut seen = Vec::new();
        for (status, ctype, body) in responses {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = Vec::new();
            let mut chunk = [0u8; 4096];
            let header_end = loop {
                let n = sock.read(&mut chunk).await.unwrap();
                buf.extend_from_slice(&chunk[..n]);
                if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                    break i + 4;
                }
            };
            let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
            let mut lines = head.split("\r\n");
            let request_line: Vec<&str> = lines.next().unwrap().split(' ').collect();
            let mut content_length = 0usize;
            let mut authorization = Value::Null;
            for line in lines {
                if let Some((k, v)) = line.split_once(':') {
                    match k.trim().to_ascii_lowercase().as_str() {
                        "content-length" => content_length = v.trim().parse().unwrap(),
                        "authorization" => authorization = json!(v.trim()),
                        _ => {}
                    }
                }
            }
            while buf.len() < header_end + content_length {
                let n = sock.read(&mut chunk).await.unwrap();
                buf.extend_from_slice(&chunk[..n]);
            }
            let body_in = &buf[header_end..header_end + content_length];
            let json_in: Value =
                if body_in.is_empty() { Value::Null } else { serde_json::from_slice(body_in).unwrap() };
            seen.push(json!({"method": request_line[0], "path": request_line[1], "authorization": authorization,
                             "json": json_in}));
            let reply = format!(
                "HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            sock.write_all(reply.as_bytes()).await.unwrap();
            sock.shutdown().await.ok();
        }
        seen
    });
    (base, handle)
}

fn canned(v: &Value) -> (u16, String, String) {
    (v[0].as_u64().unwrap() as u16, s(&v[1]).to_string(), s(&v[2]).to_string())
}

// ---------------------------------------------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------------------------------------------

fn node_from(v: &Value) -> NodeRef {
    let mut n = NeuralNode::new(s(&v["node_id"]), NodeLayer::Message, s(&v["content"]));
    n.metadata = v["metadata"].as_object().cloned().unwrap_or_default();
    if let Some(ts) = v.get("created_at").and_then(Value::as_str) {
        n.created_at = PyDateTime::fromisoformat(ts).unwrap();
    }
    node_ref(n)
}

fn nodes_from(v: &Value) -> Vec<NodeRef> {
    v.as_array().unwrap().iter().map(node_from).collect()
}

fn ranked(out: &[(NodeRef, f64)]) -> Value {
    json!(out.iter().map(|(n, s)| json!([n.borrow().node_id, s])).collect::<Vec<_>>())
}

fn prompt_name(text: &str) -> &'static str {
    ALL_PROMPTS.iter().find(|(_, p)| *p == text).map(|(n, _)| *n).expect("known prompt")
}

// ---------------------------------------------------------------------------------------------------------------
// prompts.py
// ---------------------------------------------------------------------------------------------------------------

#[test]
fn prompt_constants_are_byte_identical() {
    let p = &FIXTURE["prompts"]["constants"];
    assert_eq!(p.as_object().unwrap().len(), ALL_PROMPTS.len());
    for (name, text) in ALL_PROMPTS {
        assert_eq!(*text, s(&p[*name]), "{name}");
    }
    let a = &FIXTURE["answering"]["constants"];
    for (name, text) in [
        ("TEMPORAL_ANSWER_PROMPT", answering::TEMPORAL_ANSWER_PROMPT),
        ("OPEN_DOMAIN_INFER_PROMPT", answering::OPEN_DOMAIN_INFER_PROMPT),
        ("OPEN_DOMAIN_WORLD_PROMPT", answering::OPEN_DOMAIN_WORLD_PROMPT),
        ("OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT", answering::OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT),
        ("STRICT_ANSWER_PROMPT", answering::STRICT_ANSWER_PROMPT),
    ] {
        assert_eq!(text, s(&a[name]), "{name}");
    }
}

#[test]
fn format_retrieval_context_matches_python() {
    let f = &FIXTURE["prompts"];
    let pairs: Vec<(NodeRef, f64)> = nodes_from(&f["context_nodes"]).into_iter().map(|n| (n, 0.5)).collect();
    for c in f["contexts"].as_array().unwrap() {
        let got =
            format_retrieval_context(&pairs, c[0].as_i64().unwrap(), c[1].as_bool().unwrap(), c[2].as_bool().unwrap());
        assert_eq!(got, s(&c[3]), "context {} {} {}", c[0], c[1], c[2]);
    }
}

#[test]
fn rewrite_prompts_dedupe_and_fusion_match_python() {
    let f = &FIXTURE["prompts"];
    for c in f["rewrite_prompts"].as_array().unwrap() {
        let (prompt, kind) = get_rewrite_prompt(s(&c[0]), c[1].as_i64().unwrap());
        assert_eq!(prompt, s(&c[2]), "rewrite prompt {}", c[0]);
        assert_eq!(kind, s(&c[3]));
    }
    for c in f["dedupe"].as_array().unwrap() {
        let got = deduplicate_rewrites(s(&c[0]), &strings(&c[1]), c[2].as_f64().unwrap());
        assert_eq!(got, strings(&c[3]), "dedupe {c}");
    }
    for c in f["fuse"].as_array().unwrap() {
        let mut by_id: IndexMap<String, NodeRef> = IndexMap::new();
        let mut rbq: IndexMap<String, Vec<(NodeRef, f64)>> = IndexMap::new();
        for (q, rs) in c[0].as_object().unwrap() {
            let list = rs
                .as_array()
                .unwrap()
                .iter()
                .map(|r| {
                    let id = s(&r[0]).to_string();
                    let node = by_id
                        .entry(id.clone())
                        .or_insert_with(|| node_ref(NeuralNode::new(id.clone(), NodeLayer::Message, id.clone())))
                        .clone();
                    (node, r[1].as_f64().unwrap())
                })
                .collect();
            rbq.insert(q.clone(), list);
        }
        let out = fuse_rewrite_results(&rbq, "orig", c[1].as_i64().unwrap(), c[2].as_f64().unwrap());
        assert_json_eq(&ranked(&out), &c[3], &format!("fuse {c}"));
    }
    for c in f["parse_rewrites"].as_array().unwrap() {
        assert_eq!(parse_rewrites(py_strip(s(&c[0])), c[1].as_i64().unwrap()), strings(&c[2]), "parse {c}");
    }
}

#[test]
fn select_prompt_matches_python_on_every_locomo_question() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let expected = FIXTURE["prompts"]["select"].as_array().unwrap();
    let mut i = 0;
    for conv in data.as_array().unwrap() {
        let c = &conv["conversation"];
        let router = QueryRouter::new(Some([s(&c["speaker_a"]).to_string(), s(&c["speaker_b"]).to_string()].into()));
        for qa in conv["qa"].as_array().unwrap() {
            let q = s(&qa["question"]);
            let a = router.analyze(q);
            let plain = prompt_name(select_prompt_for_query(q, false, None));
            let routed = prompt_name(select_prompt_for_query(q, a.is_list_question, a.list_question_type.as_deref()));
            assert_json_eq(&json!([plain, routed]), &expected[i], &format!("select {q:?}"));
            i += 1;
        }
    }
    assert_eq!(i, expected.len());
    for c in FIXTURE["prompts"]["select_edge"].as_array().unwrap() {
        let got = prompt_name(select_prompt_for_query(s(&c[0]), c[1].as_bool().unwrap(), c[2].as_str()));
        assert_eq!(got, s(&c[3]), "select edge {c}");
    }
}

#[tokio::test]
async fn query_rewrites_send_the_same_request_and_parse_the_same() {
    let llm = HttpLlm::new();
    for c in FIXTURE["prompts"]["rewrites_http"].as_array().unwrap() {
        let (base, handle) = serve(vec![canned(&c["response"])]).await;
        let got =
            generate_query_rewrites(s(&c["query"]), &llm, &base, "rw-model", c["num_rewrites"].as_i64().unwrap(), 5.0)
                .await;
        assert_eq!(got, strings(&c["result"]), "rewrites {}", c["response"]);
        assert_json_eq(&json!(handle.await.unwrap()), &c["requests"], "rewrite request");
    }
    let refused = generate_query_rewrites("plain", &llm, "http://127.0.0.1:9", "m", 2, 2.0).await;
    assert_eq!(refused, strings(&FIXTURE["prompts"]["rewrites_refused"]));

    // A scripted model gets the same prompt and parameters; its reply stands for the `response` field.
    let c = &FIXTURE["prompts"]["rewrites_http"][0];
    let reply = serde_json::from_str::<Value>(s(&c["response"][2])).unwrap()["response"].clone();
    let fl = FixtureLlm::new(&json!([{"text": reply}]));
    let got = generate_query_rewrites(s(&c["query"]), &fl, "http://h:1", "rw-model", 2, 5.0).await;
    assert_eq!(got, strings(&c["result"]));
    let call = &fl.calls.borrow()[0];
    assert_eq!(call["prompt"], c["requests"][0]["json"]["prompt"]);
    assert_json_eq(
        &json!([call["model"], call["base_url"], call["temperature"], call["max_tokens"], call["timeout_seconds"]]),
        &json!(["rw-model", "http://h:1", 0.3, 200, 5.0]),
        "scripted rewrite call",
    );
}

// ---------------------------------------------------------------------------------------------------------------
// answering.py
// ---------------------------------------------------------------------------------------------------------------

fn answering_config() -> AnsweringConfig {
    let c = &FIXTURE["answering"]["config"];
    AnsweringConfig {
        ollama_base_url: s(&c["ollama_base_url"]).into(),
        answer_model: s(&c["answer_model"]).into(),
        embedding_model: s(&c["embedding_model"]).into(),
        answer_timeout_seconds: c["answer_timeout_seconds"].as_f64().unwrap(),
        embedding_timeout_seconds: c["embedding_timeout_seconds"].as_f64().unwrap(),
        num_predict: c["num_predict"].as_i64().unwrap(),
    }
}

#[tokio::test]
async fn generate_answer_and_embeddings_match_python() {
    let f = &FIXTURE["answering"];
    let cfg = answering_config();
    let question = "When did Caroline go to the LGBTQ support group?";
    for c in f["answers"].as_array().unwrap() {
        let llm = FixtureLlm::new(&json!([c["outcome"]]));
        let got = generate_answer(&llm, question, s(&f["context"]), s(&c["mode"]), Some(&cfg)).await;
        assert_eq!(got, s(&c["result"]), "answer mode {}", c["mode"]);
        assert_calls(&llm, &c["calls"], &format!("answer mode {}", c["mode"]));
        llm.assert_used_up();
    }
    for c in f["embeds"].as_array().unwrap() {
        let llm = FixtureLlm::new(&json!([c["outcome"]]));
        let got = get_embedding(&llm, "I went camping", Some(&cfg)).await;
        assert_json_eq(&json!(got), &floats(&c["result"]), "embedding");
        assert_calls(&llm, &c["calls"], "embedding call");
    }
    let d = AnsweringConfig::default();
    assert_json_eq(
        &json!({"answer_timeout_seconds": d.answer_timeout_seconds,
                "embedding_timeout_seconds": d.embedding_timeout_seconds, "num_predict": d.num_predict}),
        &f["defaults"],
        "AnsweringConfig defaults",
    );
    assert_eq!(d.ollama_base_url, neuralgraph_research::llm_backend::llm_base_url());
    assert_eq!(d.answer_model, neuralgraph_research::llm_backend::llm_model());
    assert_eq!(d.embedding_model, neuralgraph_research::llm_backend::embed_model());
}

// ---------------------------------------------------------------------------------------------------------------
// reranker.py
// ---------------------------------------------------------------------------------------------------------------

fn rr_config() -> RerankerConfig {
    RerankerConfig {
        llm_model: "rr-model".into(),
        llm_base_url: "http://rr-host:1".into(),
        ..RerankerConfig::default()
    }
}

fn config_with(overrides: &Value) -> RerankerConfig {
    let mut c = rr_config();
    for (k, v) in overrides.as_object().unwrap() {
        match k.as_str() {
            "max_candidates" => c.max_candidates = v.as_i64().unwrap(),
            "min_score_threshold" => c.min_score_threshold = v.as_f64().unwrap(),
            "async_timeout_ms" => c.async_timeout_ms = v.as_f64().unwrap(),
            other => panic!("unexpected override {other}"),
        }
    }
    c
}

fn stats(r: &dyn Reranker) -> Value {
    let s = r.get_stats();
    json!([s["total_reranks"], s["cache_hits"], s["cache_misses"], s["cache_hit_rate"]])
}

fn questions() -> Vec<String> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    data[0]["qa"].as_array().unwrap().iter().map(|q| s(&q["question"]).to_string()).collect()
}

#[test]
fn reranker_config_defaults_match_python() {
    let d = RerankerConfig::default();
    let want = &FIXTURE["reranker"]["config_defaults"];
    let got = json!({
        "reranker_type": d.reranker_type, "llm_timeout_seconds": d.llm_timeout_seconds,
        "llm_max_tokens": d.llm_max_tokens, "cross_encoder_model": d.cross_encoder_model,
        "batch_size": d.batch_size, "max_candidates": d.max_candidates, "cache_size": d.cache_size,
        "async_timeout_ms": d.async_timeout_ms, "original_score_weight": d.original_score_weight,
        "rerank_score_weight": d.rerank_score_weight, "fallback_on_timeout": d.fallback_on_timeout,
        "min_score_threshold": d.min_score_threshold,
    });
    assert_json_eq(&got, want, "RerankerConfig defaults");
}

#[test]
fn score_cache_ttl_and_eviction_match_python() {
    let f = &FIXTURE["reranker"];
    for k in f["keys"].as_array().unwrap() {
        assert_eq!(ScoreCache::make_key(s(&k[0]), s(&k[1])), s(&k[2]));
    }
    for case in f["caches"].as_array().unwrap() {
        let mut c = ScoreCache::new(case["max_size"].as_u64().unwrap() as usize, case["ttl"].as_f64().unwrap());
        for step in case["steps"].as_array().unwrap() {
            let op = &step[0];
            let res = match s(&op[0]) {
                "set" => {
                    c.set_at(s(&op[1]), s(&op[2]), op[3].as_f64().unwrap(), op[4].as_f64().unwrap());
                    Value::Null
                }
                "get" => json!(c.get_at(s(&op[1]), s(&op[2]), op[3].as_f64().unwrap())),
                _ => {
                    c.clear();
                    Value::Null
                }
            };
            let entries: Vec<Value> = c.entries().iter().map(|(k, (sc, ts))| json!([k, sc, ts])).collect();
            assert_json_eq(&json!([op, res, [entries, c.access_order()]]), step, &format!("cache step {op}"));
        }
    }
}

#[tokio::test]
async fn llm_score_prompt_and_parsing_match_python() {
    let f = &FIXTURE["reranker"];
    let cfg = rr_config();
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let turn3 = s(&data[0]["conversation"]["session_1"][3]["text"]).repeat(6);
    let q = questions()[0].clone();
    for c in f["llm_scores"].as_array().unwrap() {
        let outcome = if c["reply"].is_null() { json!({"error": c["error"]}) } else { json!({"text": c["reply"]}) };
        let llm = FixtureLlm::new(&json!([outcome]));
        let got = if c["reply"].is_null() {
            llm_score_candidate(&llm, &cfg, "Q?", "ünïcode text", "").await
        } else {
            llm_score_candidate(&llm, &cfg, &q, &turn3, "Caroline").await
        };
        assert_eq!(got, c["result"].as_i64().unwrap(), "reply {}", c["reply"]);
        assert_calls(&llm, &c["calls"], &format!("score call {}", c["reply"]));
    }
}

#[test]
fn semantic_scores_match_python() {
    for c in FIXTURE["reranker"]["semantic"].as_array().unwrap() {
        let got = semantic_score(s(&c[0]), s(&c[1]), s(&c[2]));
        assert_json_eq(&json!(got), &floats(&c[3]), &format!("semantic {c}"));
    }
}

#[tokio::test]
async fn rerank_semantic_llm_and_hybrid_match_python() {
    let f = &FIXTURE["reranker"];
    let qs = questions();
    let pool = nodes_from(&f["pool"]);
    let cands: Vec<(NodeRef, f64)> =
        pool.iter().cloned().zip(f["charges"].as_array().unwrap().iter().map(|c| c.as_f64().unwrap())).collect();
    for c in f["reranks"].as_array().unwrap() {
        let limit = c["limit"].as_i64().unwrap();
        let cfg = config_with(&c["config"]);
        let what = format!("{} {} {}", c["kind"], c["config"], limit);
        match s(&c["kind"]) {
            "semantic" => {
                let r = SemanticBoostReranker::new(Some(cfg));
                assert_json_eq(&ranked(&r.rerank(&qs[2], &cands, limit).await), &c["first"], &what);
                assert_json_eq(&ranked(&r.rerank(&qs[2], &cands, limit).await), &c["second"], &what);
                assert_json_eq(&stats(&r), &c["stats"], &what);
            }
            "llm" => {
                let llm = FixtureLlm::new(&c["outcomes"]);
                let r = LlmReranker::new(&llm, Some(cfg));
                assert_json_eq(&ranked(&r.rerank(&qs[5], &cands, limit).await), &c["first"], &what);
                assert_json_eq(&stats(&r), &c["stats"], &what);
                assert_calls(&llm, &c["calls"], &what);
                llm.assert_used_up();
            }
            _ => {
                let llm = FixtureLlm::new(&c["outcomes"]);
                let r = HybridReranker::new(&llm, Some(cfg));
                assert_json_eq(&ranked(&r.rerank(&qs[7], &cands, limit).await), &c["first"], &what);
                assert_json_eq(&stats(&r), &c["stats"], &what);
                assert_calls(&llm, &c["calls"], &what);
                llm.assert_used_up();
            }
        }
    }
    let llm = FixtureLlm::new(&json!([]));
    for c in f["factories"].as_array().unwrap() {
        let got = match create_reranker(s(&c[0]), &llm, RerankerConfig::default()) {
            Ok(_) => {
                let expected = match s(&c[0]) {
                    "llm" => "LLMReranker",
                    "semantic" => "SemanticBoostReranker",
                    _ => "HybridReranker",
                };
                expected.to_string()
            }
            Err(e) => format!("ValueError: {e}"),
        };
        assert_eq!(got, s(&c[1]));
    }
    let rc = rerank_candidates(&llm, &qs[3], &cands, "semantic", 10, RerankerConfig::default()).await.unwrap();
    assert_json_eq(&ranked(&rc), &f["rerank_candidates"], "rerank_candidates");
    let cfg = RerankerConfig { original_score_weight: 0.5, ..RerankerConfig::default() };
    let rc = rerank_candidates(&llm, &qs[3], &cands, "semantic", 4, cfg).await.unwrap();
    assert_json_eq(&ranked(&rc), &f["rerank_candidates_limit"], "rerank_candidates limit 4");
    let err = rerank_candidates(&llm, &qs[3], &cands, "bogus", 10, RerankerConfig::default()).await.err();
    assert_eq!(err.as_deref(), f["rerank_candidates_error"].as_str());
}

#[tokio::test]
async fn rerank_candidates_parallel_matches_python() {
    let f = &FIXTURE["reranker"];
    let qs = questions();
    let pool = nodes_from(&f["parallel_pool"]);
    let charges: Vec<f64> = f["parallel_charges"].as_array().unwrap().iter().map(|c| c.as_f64().unwrap()).collect();
    let pcands: Vec<(NodeRef, f64)> = pool.into_iter().zip(charges).collect();
    let window: Vec<NodeRef> = nodes_from(&f["window_pool"]);
    for c in f["parallel"].as_array().unwrap() {
        let kw = &c["kwargs"];
        let mut opts = ParallelRerankOptions {
            llm_model: "rr-model".into(),
            llm_base_url: "http://rr-host:1".into(),
            ..ParallelRerankOptions::default()
        };
        if let Some(v) = kw.get("limit") {
            opts.limit = v.as_i64().unwrap();
        }
        if let Some(v) = kw.get("max_candidates") {
            opts.max_candidates = v.as_i64().unwrap();
        }
        if let Some(v) = kw.get("score_boost") {
            opts.score_boost = v.as_f64().unwrap();
        }
        if let Some(v) = kw.get("timeout_seconds") {
            opts.timeout_seconds = v.as_f64().unwrap();
        }
        let (question, cands): (String, Vec<(NodeRef, f64)>) = match c.get("window_question") {
            Some(qi) => {
                let cands =
                    window.iter().enumerate().map(|(i, n)| (n.clone(), py_round4(1.0 - i as f64 * 0.017))).collect();
                (qs[qi.as_u64().unwrap() as usize].clone(), cands)
            }
            None => (qs[11].clone(), pcands[..c["n_candidates"].as_u64().unwrap() as usize].to_vec()),
        };
        let llm = FixtureLlm::new(&c["outcomes"]);
        let out = rerank_candidates_parallel(&llm, &question, &cands, &opts).await;
        let what = format!("parallel {kw}");
        assert_json_eq(&ranked(&out), &c["result"], &what);
        assert_calls(&llm, &c["calls"], &what);
        llm.assert_used_up();
    }
}

/// `round(x, 4)` as the generator computed the window charges.
fn py_round4(x: f64) -> f64 {
    neuralgraph_memory::pycompat::py_round(x, 4)
}

// ---------------------------------------------------------------------------------------------------------------
// profiles
// ---------------------------------------------------------------------------------------------------------------

fn turns() -> Vec<(String, String)> {
    FIXTURE["profiles"]["parallel"]["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| (s(&m["speaker"]).to_string(), s(&m["text"]).to_string()))
        .collect()
}

/// Every test touching the extractor runs here, one after the other: the OpenAI switch is a process-wide flag.
#[tokio::test]
async fn fact_extraction_matches_python() {
    let f = &FIXTURE["profiles"];
    let msgs = turns();
    let msg = &msgs[4].1;
    for c in f["extract"].as_array().unwrap() {
        let llm = FixtureLlm::new(&json!([c["outcome"]]));
        let got = extract_speaker_facts_llm(&llm, "Melanie", msg).await;
        assert_eq!(got, strings(&c["result"]), "extract {}", c["outcome"]);
        assert_calls(&llm, &c["calls"], &format!("extract call {}", c["outcome"]));
    }

    let p = &f["parallel"];
    let llm = FixtureLlm::new(&p["outcomes"]);
    let got = extract_facts_parallel(&llm, &msgs, 15).await.unwrap();
    assert_json_eq(&json!(got), &p["result"], "extract_facts_parallel");
    assert_calls(&llm, &p["calls"], "extract_facts_parallel calls");
    let llm = FixtureLlm::new(&json!(p["outcomes"].as_array().unwrap()[..10]));
    let got = extract_facts_parallel(&llm, &msgs[..10], 4).await.unwrap();
    assert_json_eq(&json!(got), &f["parallel_batch4"]["result"], "batch 4");
    assert_calls(&llm, &f["parallel_batch4"]["calls"], "batch 4 calls");
    assert_eq!(extract_facts_parallel(&llm, &msgs, 0).await.err().as_deref(), f["batch_zero_error"].as_str());
    assert_eq!(extract_facts_parallel(&llm, &msgs, -3).await.unwrap(), Vec::<Vec<String>>::new());

    // OpenAI path: same request to the endpoint, same handling of every response.
    extractor::USE_OPENAI_EXTRACTION.store(true, Ordering::Relaxed);
    extractor::set_openai_api_key(Some("sk-test".into()));
    let http = HttpLlm::new();
    for c in f["openai"].as_array().unwrap() {
        let (base, handle) = serve(vec![canned(&c["response"])]).await;
        let got = extract_speaker_facts_llm_at(&http, "Caroline", msg, &format!("{base}/v1/chat/completions")).await;
        assert_eq!(got, strings(&c["result"]), "openai {}", c["response"]);
        assert_json_eq(&json!(handle.await.unwrap()), &c["requests"], "openai request");
    }
    // A scripted model in OpenAI mode gets the OpenAI parameters; its reply is the message content.
    let content = serde_json::from_str::<Value>(s(&f["openai"][0]["response"][2])).unwrap()["choices"][0]["message"]
        ["content"]
        .clone();
    let llm = FixtureLlm::new(&json!([{"text": content}]));
    let got = extract_speaker_facts_llm(&llm, "Caroline", msg).await;
    assert_eq!(got, strings(&f["openai"][0]["result"]));
    let call = llm.calls.borrow()[0].clone();
    assert_eq!(call["prompt"], f["openai"][0]["requests"][0]["json"]["messages"][0]["content"]);
    assert_json_eq(
        &json!([call["model"], call["base_url"], call["temperature"], call["max_tokens"], call["timeout_seconds"]]),
        &json!(["gpt-4.1-mini", extractor::OPENAI_CHAT_URL, 0.1, 500, 30.0]),
        "scripted openai call",
    );
    extractor::USE_OPENAI_EXTRACTION.store(false, Ordering::Relaxed);
    extractor::set_openai_api_key(None);
}

#[tokio::test]
async fn profile_query_matches_python() {
    let f = &FIXTURE["profiles"];
    for c in f["profile_query"].as_array().unwrap() {
        let outcome = if c["reply"].is_null() { json!({"error": "down"}) } else { json!({"text": c["reply"]}) };
        let llm = FixtureLlm::new(&json!([outcome]));
        let profile: Map<String, Value> = c["profile"].as_object().unwrap().clone();
        let gold = if c["gold"].is_null() { None } else { Some(&c["gold"]) };
        let question = if c["reply"].is_null() { "Q?" } else { "Where did Caroline move from?" };
        let got = query_profile_with_llm(&llm, s(&c["speaker"]), &profile, question, gold).await;
        assert_json_eq(&Value::Object(got), &c["result"], &format!("profile query {}", c["reply"]));
        assert_calls(&llm, &c["calls"], &format!("profile query call {}", c["reply"]));
    }
    for c in f["confidence"].as_array().unwrap() {
        assert_json_eq(&json!(calculate_answer_confidence(s(&c[0]), &c[1])), &c[2], &format!("confidence {c}"));
    }
}

/// The generator's extractor: one fact for messages longer than 60 characters.
struct LengthExtractor;

#[async_trait(?Send)]
impl FactExtractor for LengthExtractor {
    async fn extract(&self, speaker_name: &str, text: &str) -> Result<Vec<String>, String> {
        Ok(if py_len(text) > 60 { vec![format!("{speaker_name} said {} chars", py_len(text))] } else { vec![] })
    }
}

#[tokio::test]
async fn speaker_profiler_matches_python() {
    let f = &FIXTURE["profiles"]["profiler"];
    let mut p = UniversalSpeakerProfiler::new();
    for (i, (speaker, text)) in turns().iter().take(30).enumerate() {
        let ext: Option<&dyn FactExtractor> = if i % 2 == 0 { Some(&LengthExtractor) } else { None };
        p.add_message(speaker, text, ext).await.unwrap();
    }
    assert_json_eq(&Value::Object(p.get_all_profiles()), &f["profiles"], "profiles");
    let llm = FixtureLlm::new(&json!([]));
    let no_target = p.query_single_hop(&llm, "What did the dog eat?", None).await.unwrap();
    assert_json_eq(&Value::Object(no_target), &f["no_target"], "no target speaker");
    let err = p.query_single_hop(&llm, "What is MELANIE's hobby?", None).await.err();
    assert_eq!(err.as_deref(), f["target_error"].as_str());
    let err = p.profiles["Caroline"].query(&llm, "q", None).await.err();
    assert_eq!(err.as_deref(), f["query_error"].as_str());
    assert!(llm.calls.borrow().is_empty());
}

#[test]
fn prompts_module_exports_every_template() {
    assert_eq!(prompt_name(prompts::RETRIEVAL_ANSWER_PROMPT), "RETRIEVAL_ANSWER_PROMPT");
}
