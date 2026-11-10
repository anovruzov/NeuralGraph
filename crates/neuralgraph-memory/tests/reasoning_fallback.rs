//! A reasoning model that ignores "do not reason" answers with nothing once its reply budget is spent on thinking.
//! The backend notices the hidden reasoning, asks again with an empty `<think>` block prefilled, and keeps doing so
//! for that model. Its own test binary: it relies on `LLM_NO_THINK_PREFILL` being unset.

use axum::{extract::State, routing::post, Json, Router};
use neuralgraph_memory::backend::{llm_generate, llm_generate_think, needs_prefill};
use neuralgraph_memory::setup::test_models;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};

type Bodies = Arc<Mutex<Vec<Value>>>;

/// `thinker` reasons unless the reply is prefilled; `chatty` reasons but still answers; `mute` says nothing at all.
async fn chat(State(b): State<Bodies>, Json(v): Json<Value>) -> Json<Value> {
    let prefilled = v["messages"].as_array().is_some_and(|m| m.len() == 2);
    let message = match (v["model"].as_str().unwrap_or(""), prefilled) {
        ("thinker", false) => json!({"content": "", "reasoning_content": "Thinking Process: 1."}),
        ("chatty", false) => json!({"content": "slow", "reasoning_content": "Hmm."}),
        ("mute", _) => json!({"content": ""}),
        _ => json!({"content": "ready"}),
    };
    b.lock().unwrap().push(v);
    Json(json!({"choices": [{"message": message}]}))
}

async fn embeddings(Json(_): Json<Value>) -> Json<Value> {
    Json(json!({"data": [{"embedding": [0.1, 0.2, 0.3]}]}))
}

#[tokio::test]
async fn a_model_that_reasons_anyway_is_asked_again_without_reasoning() {
    std::env::remove_var("LLM_NO_THINK_PREFILL");
    std::env::remove_var("LLM_BACKEND");
    let bodies: Bodies = Arc::default();
    let app = Router::new()
        .route("/v1/chat/completions", post(chat))
        .route("/v1/embeddings", post(embeddings))
        .with_state(bodies.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let http = reqwest::Client::new();
    let prefills = |from: usize| -> Vec<usize> {
        bodies.lock().unwrap()[from..].iter().map(|b| b["messages"].as_array().unwrap().len() - 1).collect()
    };

    // empty reply plus reasoning: one retry with the prefill, and the model is remembered
    assert!(!needs_prefill("thinker"));
    assert_eq!(llm_generate(&http, "p", "thinker", &url, 0.0, 16, 30.0).await.unwrap(), "ready");
    assert_eq!(prefills(0), [0, 1]);
    assert!(needs_prefill("thinker"));
    assert_eq!(llm_generate(&http, "p", "thinker", &url, 0.0, 16, 30.0).await.unwrap(), "ready");
    assert_eq!(prefills(2), [1], "later calls prefill at once");

    // a call that wants reasoning is left alone
    assert_eq!(llm_generate_think(&http, "p", "thinker", &url, 0.0, 16, 30.0, true).await.unwrap(), "");
    assert_eq!(prefills(3), [0]);

    // it answered, but reasoned first: the answer is kept and the next call skips the reasoning
    assert_eq!(llm_generate(&http, "p", "chatty", &url, 0.0, 16, 30.0).await.unwrap(), "slow");
    assert_eq!(llm_generate(&http, "p", "chatty", &url, 0.0, 16, 30.0).await.unwrap(), "ready");
    assert_eq!(prefills(4), [0, 1]);

    // an ordinary model is never prefilled, and an empty reply without reasoning is not retried
    assert_eq!(llm_generate(&http, "p", "plain", &url, 0.0, 16, 30.0).await.unwrap(), "ready");
    assert_eq!(llm_generate(&http, "p", "mute", &url, 0.0, 16, 30.0).await.unwrap(), "");
    assert_eq!(prefills(6), [0, 0]);
    assert!(!needs_prefill("plain") && !needs_prefill("mute"));

    // the Models page test: a model that says nothing is reported as not working
    let t = test_models(&url, "thinker", "e").await;
    assert_eq!(t["chat"]["ok"], true, "{t}");
    assert_eq!(t["embed"]["ok"], true, "{t}");
    let t = test_models(&url, "mute", "e").await;
    assert_eq!(t["chat"]["ok"], false, "{t}");
    assert!(t["chat"]["text"].as_str().unwrap().contains("answered with nothing"), "{t}");
}
