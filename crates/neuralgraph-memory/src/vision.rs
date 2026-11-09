//! Reading images (screenshot import) without overloading the machine.
//!
//! Many recommended chat models can see (LM Studio's `google/gemma-4-e4b` is a vision model), and then images go to
//! the chat model and nothing else is loaded. When the chat model is text-only, a small separate vision model is used
//! (`NEURALGRAPH_VISION_MODEL`, chosen by `setup`), and the two are swapped rather than loaded together: the queue is
//! held, the chat model unloaded, the vision model reads the images and is unloaded, and the chat model comes back.

use crate::control::Step;
use crate::error::{MemoryError, Result};
use crate::service::ChatMemory;
use serde_json::{json, Value};
use std::time::{Duration, Instant};

/// Recommended small vision model per server kind, with its download size in GB (shown before installing).
pub const LMSTUDIO_VISION: (&str, &str, f64) = ("qwen/qwen3-vl-4b", "qwen/qwen3-vl-4b", 3.3);
pub const OLLAMA_VISION: (&str, &str, f64) = ("qwen2.5vl:3b", "qwen2.5vl:3b", 3.2);

fn is_ollama(base: &str) -> bool {
    crate::backend::backend_for(base) == "ollama"
}

/// Whether `model` on the server at `base` accepts images (None when the server does not say).
pub async fn can_see(base: &str, model: &str) -> Option<bool> {
    let http = reqwest::Client::new();
    let base = base.trim_end_matches('/');
    if is_ollama(base) {
        let v: Value = http
            .post(format!("{base}/api/show"))
            .json(&json!({"model": model}))
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        let caps = v.get("capabilities")?.as_array()?;
        return Some(caps.iter().any(|c| c.as_str() == Some("vision")));
    }
    // LM Studio's REST API tells "vlm" (vision) apart from "llm"
    let v: Value = http
        .get(format!("{base}/api/v0/models"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let m = v.get("data")?.as_array()?.iter().find(|m| m.get("id").and_then(Value::as_str) == Some(model))?;
    Some(m.get("type").and_then(Value::as_str) == Some("vlm"))
}

/// Whether `model` is loaded right now (LM Studio `/api/v0/models` state, Ollama `/api/ps`).
pub async fn is_loaded(base: &str, model: &str) -> Option<bool> {
    let http = reqwest::Client::new();
    let base = base.trim_end_matches('/');
    if is_ollama(base) {
        let v: Value =
            http.get(format!("{base}/api/ps")).timeout(Duration::from_secs(5)).send().await.ok()?.json().await.ok()?;
        let running = v.get("models")?.as_array()?;
        return Some(running.iter().any(|m| {
            let n = m.get("name").and_then(Value::as_str).unwrap_or("");
            n == model || n.strip_prefix(model).is_some_and(|r| r.starts_with(':'))
        }));
    }
    let v: Value = http
        .get(format!("{base}/api/v0/models"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let m = v.get("data")?.as_array()?.iter().find(|m| m.get("id").and_then(Value::as_str) == Some(model))?;
    Some(m.get("state").and_then(Value::as_str) == Some("loaded"))
}

/// The configured separate vision model, if any.
pub fn configured_vision_model() -> Option<String> {
    std::env::var("NEURALGRAPH_VISION_MODEL").ok().filter(|v| !v.trim().is_empty())
}

/// Which model reads images right now, and whether it is the chat model itself.
pub async fn image_model(cm: &ChatMemory) -> Result<(String, bool)> {
    let llm = cm.control.current();
    let (base, chat) = (llm.base_url(), llm.model());
    if can_see(&base, &chat).await == Some(true) {
        return Ok((chat, true));
    }
    match configured_vision_model() {
        Some(v) => Ok((v, false)),
        None => Err(MemoryError::Other(
            "NoVisionModel",
            format!(
                "{chat} cannot read images and no image model is set. Choose one under Settings → Models (or run \
                 `neuralgraph-memory setup`), e.g. {} for LM Studio or {} for Ollama.",
                LMSTUDIO_VISION.0, OLLAMA_VISION.0
            ),
        )),
    }
}

/// Read every image with the image model, swapping models when it is not the chat model. Returns each image's text
/// (or error) and what was done to the loaded models.
pub async fn read_images(
    cm: &ChatMemory,
    images: &[(String, Vec<u8>)],
) -> Result<(Vec<(String, Result<String>)>, Vec<Step>)> {
    if cm.store.is_paused() {
        return Err(MemoryError::Other("Paused", "NeuralGraph is paused; turn it on to read images".into()));
    }
    let (model, same) = image_model(cm).await?;
    let llm = cm.control.current();
    let base = llm.base_url();
    let chat = llm.model();
    let mut steps = Vec::new();
    // put back only what was there: a chat model that was not loaded stays unloaded
    let chat_was_loaded = !same && is_loaded(&base, &chat).await != Some(false);
    if !same {
        // hold the queue so no extraction needs the chat model while it is away; give a running batch a moment
        cm.store.hold_queue(600)?;
        let t = Instant::now();
        while cm.worker.metrics().busy_workers > 0 && t.elapsed() < Duration::from_secs(90) {
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        steps.extend(crate::control::unload_models(&base, std::slice::from_ref(&chat)).await);
        steps.extend(crate::control::load_models(&base, std::slice::from_ref(&model)).await);
    }
    let mut out = Vec::new();
    for (name, bytes) in images {
        out.push((name.clone(), crate::import::transcribe_image(&base, name, bytes, &model).await));
    }
    if !same {
        steps.extend(crate::control::unload_models(&base, std::slice::from_ref(&model)).await);
        if chat_was_loaded {
            steps.extend(crate::control::load_models(&base, std::slice::from_ref(&chat)).await);
        }
        cm.store.hold_queue(0)?;
        cm.worker.wake();
    }
    Ok((out, steps))
}
