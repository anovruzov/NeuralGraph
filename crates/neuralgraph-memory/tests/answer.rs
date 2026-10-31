//! `ChatMemory::answer` (the routed reader protocol), the `memory_answer` MCP tool and `POST /api/answer`, driven by a
//! scripted model that records every call's prompt and options.

mod common;

use async_trait::async_trait;
use axum::body::Body;
use axum::http::Request;
use http_body_util::BodyExt;
use neuralgraph_memory::answer::{is_compute_question, is_idk, parse_compute_answer, ANSWER_PROMPT, COMPUTE_PROMPT};
use neuralgraph_memory::error::Result;
use neuralgraph_memory::llm::{fake_embedding, GenerateOpts};
use neuralgraph_memory::mcp::McpProtocol;
use neuralgraph_memory::server::{create_app, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, RememberOptions};
use neuralgraph_memory::{AnswerOptions, AnswerRoute, LlmClient};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Arc, Mutex};
use tower::ServiceExt;

/// Replies in order; records `(prompt, opts)` of each model call. Embeddings are the deterministic hash ones.
#[derive(Default)]
struct ScriptedLlm {
    replies: Mutex<VecDeque<String>>,
    seen: Mutex<Vec<(String, GenerateOpts)>>,
}

impl ScriptedLlm {
    fn push(&self, replies: &[&str]) {
        self.replies.lock().unwrap().extend(replies.iter().map(|r| r.to_string()));
    }
    fn calls(&self) -> Vec<(String, GenerateOpts)> {
        self.seen.lock().unwrap().clone()
    }
    fn next(&self, prompt: &str, opts: GenerateOpts) -> Result<String> {
        self.seen.lock().unwrap().push((prompt.to_string(), opts));
        Ok(self.replies.lock().unwrap().pop_front().expect("unscripted model call"))
    }
}

#[async_trait]
impl LlmClient for ScriptedLlm {
    async fn generate(&self, prompt: &str, max_tokens: i64, temperature: f64, timeout: f64) -> Result<String> {
        self.next(prompt, GenerateOpts { max_tokens, temperature, timeout, think: false })
    }
    async fn generate_with(&self, prompt: &str, opts: &GenerateOpts) -> Result<String> {
        self.next(prompt, opts.clone())
    }
    async fn embed(&self, text: &str) -> Result<Vec<f64>> {
        Ok(fake_embedding(text, 256))
    }
    async fn embed_many(&self, texts: &[String]) -> Result<Vec<Vec<f64>>> {
        Ok(texts.iter().map(|t| fake_embedding(t, 256)).collect())
    }
    async fn close(&self) {}
}

struct Fx {
    _dir: tempfile::TempDir,
    cm: Arc<ChatMemory>,
    llm: Arc<ScriptedLlm>,
}

async fn fx() -> Fx {
    let dir = tempfile::tempdir().unwrap();
    let llm = Arc::new(ScriptedLlm::default());
    let cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    let cm = Arc::new(ChatMemory::open(dir.path().join("a.db").to_str().unwrap(), Some(llm.clone()), cfg).unwrap());
    let ali = RememberOptions { subject: "Ali".into(), chat_id: "c1".into(), ..Default::default() };
    cm.remember("Ali has a cat called Luna.", &ali).await.unwrap();
    cm.remember("Ali has a dog called Rex.", &ali).await.unwrap();
    cm.remember("Ali lives in Berlin.", &RememberOptions { kind: "identity".into(), ..ali }).await.unwrap();
    assert!(llm.calls().is_empty(), "seeding must not call the reader");
    Fx { _dir: dir, cm, llm }
}

fn today(d: &str) -> AnswerOptions {
    AnswerOptions { today: Some(d.into()), ..Default::default() }
}

#[tokio::test]
async fn concise_path_no_thinking() {
    let f = fx().await;
    f.llm.push(&["Berlin."]);
    let a = f.cm.answer("Where does Ali live in Berlin?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "Berlin.");
    assert!(!a.routed && !a.cascaded);
    assert_eq!(a.calls, 1);
    assert!(a.context.contains("Ali lives in Berlin"));
    assert!(a.seconds >= 0.0);
    let calls = f.llm.calls();
    assert_eq!(calls.len(), 1);
    let (prompt, o) = &calls[0];
    assert!(!o.think && o.max_tokens == 160 && o.temperature == 0.0);
    assert!(prompt.starts_with("You are an assistant with long-term memory. Below are memories retrieved"));
    assert!(prompt.contains("\nToday's date: 2026-01-02\n"));
    assert!(prompt.contains("Question: Where does Ali live in Berlin?\nAnswer:"));
    assert!(prompt.contains(&a.context));
}

#[tokio::test]
async fn compute_path_thinks_and_parses_answer_line() {
    let f = fx().await;
    f.llm.push(&["- Luna: 1 cat\n- Rex: 1 dog\n1 + 1 = 2\nAnswer: **2 pets**\n"]);
    let a = f.cm.answer("How many pets does Ali have in total?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "2 pets");
    assert!(a.routed && !a.cascaded);
    assert_eq!(a.calls, 1);
    let (prompt, o) = &f.llm.calls()[0];
    assert!(o.think && o.max_tokens == 2500);
    assert!(prompt.contains("5. Finish with ONE line: \"Answer: <result>\"."));
    assert!(prompt.contains("\nToday's date: 2026-01-02\n"));
    assert!(prompt.contains("Question: How many pets does Ali have in total?\n"));
}

#[tokio::test]
async fn idk_cascades_to_the_compute_prompt() {
    let f = fx().await;
    f.llm.push(&["I don't know.", "Luna is a cat.\nAnswer: Luna"]);
    let a = f.cm.answer("What is the name of the cat Ali has?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "Luna");
    assert!(!a.routed && a.cascaded);
    assert_eq!(a.calls, 2);
    let calls = f.llm.calls();
    assert!(!calls[0].1.think && calls[0].1.max_tokens == 160);
    assert!(calls[1].1.think && calls[1].1.max_tokens == 2500);
    assert!(calls[1].0.contains("Finish with ONE line"));
}

#[tokio::test]
async fn cascade_keeps_the_first_answer_when_the_second_is_also_idk() {
    let f = fx().await;
    f.llm.push(&["I don\u{2019}t know.", "Answer: I don't know"]);
    let a = f.cm.answer("What is the name of the cat Ali has?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "I don\u{2019}t know.");
    assert!(!a.cascaded);
    assert_eq!(a.calls, 2);
}

#[tokio::test]
async fn compute_falls_back_to_a_non_thinking_call_when_the_reply_is_empty() {
    let f = fx().await;
    f.llm.push(&["", "Answer: 2 pets"]);
    let a = f.cm.answer("How many pets does Ali have in total?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "2 pets");
    assert!(a.routed);
    assert_eq!(a.calls, 2);
    let calls = f.llm.calls();
    assert!(calls[0].1.think && calls[0].1.max_tokens == 2500);
    assert!(!calls[1].1.think && calls[1].1.max_tokens == 300);
    assert_eq!(calls[0].0, calls[1].0, "the fallback re-asks the same prompt");
}

#[tokio::test]
async fn unclosed_reasoning_counts_as_empty() {
    let f = fx().await;
    f.llm.push(&["<think>\nLet me count the pets", "Answer: 2"]);
    let a = f.cm.answer("How many pets does Ali have?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.text, "2");
    assert_eq!(a.calls, 2);
}

#[tokio::test]
async fn forced_routes_and_cascade_after_a_compute_idk_is_skipped() {
    let f = fx().await;
    f.llm.push(&["Two."]);
    let o = AnswerOptions { route: AnswerRoute::Concise, ..today("2026-01-02") };
    let a = f.cm.answer("How many pets does Ali have?", &o).await.unwrap();
    assert!(!a.routed && a.text == "Two.");
    assert!(!f.llm.calls()[0].1.think);

    f.llm.push(&["Answer: Berlin"]);
    let o = AnswerOptions { route: AnswerRoute::Compute, ..today("2026-01-02") };
    let a = f.cm.answer("Where does Ali live?", &o).await.unwrap();
    assert!(a.routed && a.text == "Berlin");
    assert!(f.llm.calls()[1].1.think);

    // a compute answer of "I don't know" is final: the cascade would repeat the same prompt
    f.llm.push(&["Answer: I don't know."]);
    let a = f.cm.answer("How many yachts does Ali own?", &today("2026-01-02")).await.unwrap();
    assert!(a.routed && !a.cascaded && is_idk(&a.text) && a.calls == 1);
}

#[tokio::test]
async fn default_today_is_the_pinned_clock_and_empty_question_is_rejected() {
    let f = fx().await;
    neuralgraph_memory::clock::set_fixed(Some("2031-04-05T06:07:08Z".parse().unwrap()));
    f.llm.push(&["Berlin."]);
    let a = f.cm.answer("Where does Ali live?", &AnswerOptions::default()).await.unwrap();
    neuralgraph_memory::clock::set_fixed(None);
    assert!(f.llm.calls()[0].0.contains("Today's date: 2031-04-05\n"));
    assert_eq!(a.text, "Berlin.");
    assert!(f.cm.answer("   ", &AnswerOptions::default()).await.is_err());
}

#[tokio::test]
async fn empty_context_still_asks_with_the_placeholder() {
    let dir = tempfile::tempdir().unwrap();
    let llm = Arc::new(ScriptedLlm::default());
    let cm =
        ChatMemory::open(dir.path().join("e.db").to_str().unwrap(), Some(llm.clone()), Default::default()).unwrap();
    llm.push(&["I don't know.", "Answer: I don't know"]);
    let a = cm.answer("What is the capital of Atlantis?", &today("2026-01-02")).await.unwrap();
    assert_eq!(a.context, "");
    assert!(llm.calls()[0].0.contains("\n(no relevant memories)\n"));
    assert!(is_idk(&a.text));
}

#[test]
fn compute_regex_routes_like_the_harness() {
    for q in [
        "How long had I been bird watching when I attended the workshop?",
        "how many pets do I have",
        "How much did I spend in total?",
        "What is the difference in age?",
        "Which came first, A or B? (between the two)",
        "How old was I in 2019?",
        "What did I do in the last two weeks?",
        "TOTAL cost of the trip",
    ] {
        assert!(is_compute_question(q), "{q}");
    }
    for q in
        ["Where does Ali live?", "What is my dog's name?", "Which restaurant did I pick?", "What did I buy yesterday?"]
    {
        assert!(!is_compute_question(q), "{q}");
    }
}

#[test]
fn answer_line_parsing_matches_the_harness() {
    assert_eq!(parse_compute_answer("a\nAnswer: 3 weeks ago"), "3 weeks ago");
    assert_eq!(parse_compute_answer("Answer: 1\nmore\nAnswer: **$275**"), "$275");
    assert_eq!(parse_compute_answer("**Answer:** Blue Harbor Bakery"), "Blue Harbor Bakery");
    assert_eq!(parse_compute_answer("no marker\nlast line "), "last line");
    assert_eq!(parse_compute_answer("  \n"), "");
    assert!(is_idk("  I don't know.") && is_idk("I DON'T KNOW") && !is_idk("Answer: I don't know"));
}

#[test]
fn prompts_are_the_research_prompts_verbatim() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let auto = std::fs::read_to_string(root.join("research/overnight/prompts/auto_v1n.txt")).unwrap();
    let (first, rest) = auto.split_once('\n').unwrap();
    assert!(first.starts_with("#mode:"));
    assert_eq!(COMPUTE_PROMPT, rest);

    let py = std::fs::read_to_string(root.join("research/overnight/scripts/answer_eval.py")).unwrap();
    let start = py.find("ANSWER_PROMPT = \"\"\"").unwrap() + "ANSWER_PROMPT = \"\"\"".len();
    let end = start + py[start..].find("\"\"\"").unwrap();
    assert_eq!(ANSWER_PROMPT, &py[start..end]);
    let re_line = py.split("COMPUTE_RE = re.compile(").nth(1).unwrap().split("re.I)").next().unwrap();
    let joined: String = re_line
        .lines()
        .map(|l| l.trim().trim_start_matches("r\"").trim_end_matches(|c| c == '"' || c == ',').to_string())
        .collect();
    assert_eq!(joined, neuralgraph_memory::answer::COMPUTE_RE_SRC);
}

async fn rest(app: &axum::Router, body: &str) -> (u16, Value) {
    let req = Request::builder()
        .method("POST")
        .uri("/api/answer")
        .header("Content-Type", "application/json")
        .header("Host", "127.0.0.1:8765")
        .body(Body::from(body.to_string()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status().as_u16();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
}

#[tokio::test]
async fn mcp_tool_and_rest_endpoint() {
    let f = fx().await;
    let p = McpProtocol::new(f.cm.clone());

    f.llm.push(&["Answer: 2 pets"]);
    let r = p
        .handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_answer",
            "arguments": {"question": "How many pets does Ali have?", "today": "2026-02-03"}}}))
        .await
        .unwrap();
    assert_eq!(r["result"]["isError"], false);
    let s = &r["result"]["structuredContent"];
    assert_eq!(s["text"], "2 pets");
    assert_eq!(s["routed"], true);
    assert_eq!(s["cascaded"], false);
    assert_eq!(s["calls"], 1);
    assert!(s["context"].as_str().unwrap().contains("Luna"));
    assert!(s["seconds"].is_number());
    assert!(f.llm.calls()[0].0.contains("Today's date: 2026-02-03"));

    // unknown/missing arguments are tool errors, like the other tools
    let r = p
        .handle(&json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_answer", "arguments": {}}}))
        .await
        .unwrap();
    assert_eq!(r["result"]["isError"], true);
    let listed = p.handle(&json!({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})).await.unwrap();
    let names: Vec<&str> =
        listed["result"]["tools"].as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names.last(), Some(&"memory_answer"));

    let app = create_app(f.cm.clone(), ServerOptions::default());
    f.llm.push(&["Berlin."]);
    let (st, body) = rest(&app, r#"{"question": "Where does Ali live?", "today": "2026-02-03"}"#).await;
    assert_eq!(st, 200);
    assert_eq!(body["text"], "Berlin.");
    assert_eq!(body["routed"], false);
    assert_eq!(rest(&app, r#"{"today": "2026-02-03"}"#).await.0, 400);
    assert_eq!(rest(&app, r#"{"question": "  "}"#).await.0, 400);
    assert_eq!(rest(&app, r#"{"question": "x", "today": 5}"#).await.0, 400);
    assert_eq!(rest(&app, "nope").await.0, 400);
    let get = Request::builder()
        .method("GET")
        .uri("/api/answer")
        .header("Host", "127.0.0.1:8765")
        .body(Body::empty())
        .unwrap();
    assert_eq!(app.clone().oneshot(get).await.unwrap().status().as_u16(), 405);
}
