//! The HTTP backend's per-call `think` switch, observed on the wire. One test only: it sets process-wide environment
//! variables (`LLM_NO_THINK_PREFILL`), so it lives in its own test binary.

use axum::{extract::State, routing::post, Json, Router};
use neuralgraph_memory::llm::{BackendLlmClient, GenerateOpts, LlmClient};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};

#[tokio::test]
async fn think_switch_reaches_the_request_body() {
    std::env::set_var("LLM_NO_THINK_PREFILL", "1");
    std::env::remove_var("LLM_BACKEND");
    let bodies: Arc<Mutex<Vec<Value>>> = Arc::default();
    async fn handler(State(b): State<Arc<Mutex<Vec<Value>>>>, Json(v): Json<Value>) -> Json<Value> {
        b.lock().unwrap().push(v);
        Json(json!({"choices": [{"message": {"content": "<think>x</think>ok"}}]}))
    }
    let app = Router::new().route("/v1/chat/completions", post(handler)).with_state(bodies.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let llm = BackendLlmClient::new(Some(url), Some("m".into()), None, 1);
    let off = GenerateOpts { max_tokens: 160, ..Default::default() };
    assert_eq!(llm.generate_with("p", &off).await.unwrap(), "ok");
    assert_eq!(llm.generate("p", 160, 0.0, 30.0).await.unwrap(), "ok");
    let on = GenerateOpts { think: true, max_tokens: 2500, ..off.clone() };
    assert_eq!(llm.generate_with("p", &on).await.unwrap(), "ok");

    let b = bodies.lock().unwrap().clone();
    assert_eq!(b.len(), 3);
    for body in &b[..2] {
        assert_eq!(body["reasoning_effort"], "none");
        assert_eq!(body["messages"].as_array().unwrap().len(), 2, "empty <think> prefill when thinking is off");
        assert_eq!(body["messages"][1]["role"], "assistant");
        assert_eq!(body["max_tokens"], 160);
    }
    assert!(b[2].get("reasoning_effort").is_none());
    assert_eq!(b[2]["messages"].as_array().unwrap().len(), 1, "no prefill when thinking");
    assert_eq!(b[2]["max_tokens"], 2500);
}
