//! Single LLM/embedding backend (port of `NeuralGraph/llm_backend.py`).
//!
//! Supports two servers behind one interface:
//! - LM Studio / any OpenAI-compatible server (default): POST /v1/chat/completions, /v1/embeddings
//! - Ollama: POST /api/generate, /api/embeddings
//!
//! The backend is chosen from the base URL (port 11434 => Ollama) unless `LLM_BACKEND` is set. Settings come from
//! the same environment variables as before:
//!     LLM_BASE_URL   (default http://127.0.0.1:1234)
//!     LLM_MODEL      (default google/gemma-4-e4b)
//!     EMBED_MODEL    (default text-embedding-nomic-embed-text-v1.5)
//!     LLM_BACKEND    ("openai" | "ollama", default inferred)
//!     LLM_REASONING_EFFORT (default "none"; disables thinking on Gemma-4/Qwen-3 in LM Studio)
//!     LLM_NO_THINK_PREFILL (default off; "1" prefills an empty <think> block for Qwen builds that ignore the above)

use regex::Regex;
use serde_json::{json, Value};
use std::sync::LazyLock;
use std::time::Duration;

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// `LLM_BASE_URL` (trailing slashes removed).
pub fn default_base_url() -> String {
    env_or("LLM_BASE_URL", "http://127.0.0.1:1234").trim_end_matches('/').to_string()
}

/// `LLM_MODEL`.
pub fn default_model() -> String {
    env_or("LLM_MODEL", "google/gemma-4-e4b")
}

/// `EMBED_MODEL`.
pub fn default_embed_model() -> String {
    env_or("EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
}

fn reasoning_effort() -> String {
    env_or("LLM_REASONING_EFFORT", "none")
}

fn no_think_prefill() -> bool {
    !matches!(std::env::var("LLM_NO_THINK_PREFILL").as_deref(), Err(_) | Ok("") | Ok("0") | Ok("false"))
}

/// "ollama" or "openai".
pub fn backend_for(base_url: &str) -> String {
    match std::env::var("LLM_BACKEND") {
        Ok(forced) if !forced.is_empty() => forced,
        _ => {
            if base_url.contains(":11434") {
                "ollama".into()
            } else {
                "openai".into()
            }
        }
    }
}

static THINK_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<think>.*?</think>[\s\x1c-\x1f]*").unwrap());

pub fn strip_thinking(text: &str) -> String {
    crate::pycompat::py_strip(&THINK_RE.replace_all(text, "")).to_string()
}

async fn post_json(client: &reqwest::Client, url: &str, body: &Value, timeout_seconds: f64) -> Result<Value, String> {
    let resp = client
        .post(url)
        .timeout(Duration::from_secs_f64(timeout_seconds.max(0.001)))
        .json(body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = resp.status();
    if !status.is_success() {
        return Err(format!(
            "{}, message='{}', url='{}'",
            status.as_u16(),
            status.canonical_reason().unwrap_or(""),
            url
        ));
    }
    resp.json::<Value>().await.map_err(|e| e.to_string())
}

/// The model's text for a single-turn prompt. Errors on HTTP/network failure.
pub async fn llm_generate(
    client: &reqwest::Client,
    prompt: &str,
    model: &str,
    base_url: &str,
    temperature: f64,
    max_tokens: i64,
    timeout_seconds: f64,
) -> Result<String, String> {
    let base_url = base_url.trim_end_matches('/');
    if backend_for(base_url) == "ollama" {
        let body = json!({
            "model": model,
            "prompt": prompt,
            "stream": false,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        });
        let data = post_json(client, &format!("{base_url}/api/generate"), &body, timeout_seconds).await?;
        let text = data.get("response").and_then(Value::as_str).unwrap_or("");
        return Ok(strip_thinking(text));
    }
    let mut messages = vec![json!({"role": "user", "content": prompt})];
    if no_think_prefill() {
        messages.push(json!({"role": "assistant", "content": "<think>\n\n</think>\n\n"}));
    }
    let body = json!({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": false,
        // Gemma/Qwen "thinking" models burn the token budget on reasoning; turn it off.
        "reasoning_effort": reasoning_effort(),
    });
    let data = post_json(client, &format!("{base_url}/v1/chat/completions"), &body, timeout_seconds).await?;
    let choice = match data.get("choices") {
        Some(Value::Array(a)) => a.first().cloned().ok_or("list index out of range")?,
        Some(_) => return Err("choices is not a list".into()),
        None => json!({}),
    };
    let content = choice.get("message").and_then(|m| m.get("content")).and_then(Value::as_str).unwrap_or("");
    Ok(strip_thinking(content))
}

/// An embedding vector. Errors on HTTP/network failure.
pub async fn llm_embed(
    client: &reqwest::Client,
    text: &str,
    model: &str,
    base_url: &str,
    timeout_seconds: f64,
) -> Result<Vec<f64>, String> {
    let base_url = base_url.trim_end_matches('/');
    let (url, body, ollama) = if backend_for(base_url) == "ollama" {
        (format!("{base_url}/api/embeddings"), json!({"model": model, "prompt": text}), true)
    } else {
        (format!("{base_url}/v1/embeddings"), json!({"model": model, "input": text}), false)
    };
    let data = post_json(client, &url, &body, timeout_seconds).await?;
    let vec = if ollama {
        data.get("embedding").cloned().unwrap_or(json!([]))
    } else {
        match data.get("data") {
            Some(Value::Array(a)) => {
                a.first().ok_or("list index out of range")?.get("embedding").cloned().unwrap_or(json!([]))
            }
            _ => json!([]),
        }
    };
    match vec {
        Value::Array(items) => items
            .iter()
            .map(|x| crate::pycompat::py_float_from_value(x).ok_or_else(|| "could not convert to float".to_string()))
            .collect(),
        _ => Err("embedding is not a list".into()),
    }
}
