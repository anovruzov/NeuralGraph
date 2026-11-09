//! The on/off switch and live model changes.
//!
//! [`ControlledLlm`] sits between every component and the model server. All of them (extractor, worker, retriever,
//! answers) hold the same instance, so one place decides two things:
//!
//! - **Pause.** While the database's `paused` flag is set ([`ChatMemoryStore::is_paused`], shared by `serve`, `mcp`
//!   and the hooks), every call fails fast with "NeuralGraph is paused" instead of loading a model. The worker
//!   leases nothing, retrieval falls back to keyword search, and incoming messages simply wait in the queue.
//! - **Live model changes.** [`ControlledLlm::switch`] swaps the backend behind every component at once.
//!
//! [`pause`] also unloads the models from LM Studio / Ollama so their RAM is free right away; [`resume`] clears the
//! flag and loads them again with one tiny call each.

use crate::error::{MemoryError, Result};
use crate::llm::{GenerateOpts, LlmClient};
use crate::store::ChatMemoryStore;
use async_trait::async_trait;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

pub struct ControlledLlm {
    inner: RwLock<Arc<dyn LlmClient>>,
    store: Arc<ChatMemoryStore>,
}

impl ControlledLlm {
    pub fn new(inner: Arc<dyn LlmClient>, store: Arc<ChatMemoryStore>) -> Self {
        ControlledLlm { inner: RwLock::new(inner), store }
    }

    /// The backend in use right now.
    pub fn current(&self) -> Arc<dyn LlmClient> {
        self.inner.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Route every later call to `inner` (calls already running finish on the old backend).
    pub fn switch(&self, inner: Arc<dyn LlmClient>) {
        *self.inner.write().unwrap_or_else(|e| e.into_inner()) = inner;
    }

    fn check(&self) -> Result<Arc<dyn LlmClient>> {
        if self.store.is_paused() {
            return Err(MemoryError::Other("Paused", "NeuralGraph is paused; resume it to use the model".into()));
        }
        Ok(self.current())
    }
}

#[async_trait]
impl LlmClient for ControlledLlm {
    async fn generate(&self, prompt: &str, max_tokens: i64, temperature: f64, timeout: f64) -> Result<String> {
        self.check()?.generate(prompt, max_tokens, temperature, timeout).await
    }
    async fn generate_with(&self, prompt: &str, opts: &GenerateOpts) -> Result<String> {
        self.check()?.generate_with(prompt, opts).await
    }
    async fn embed(&self, text: &str) -> Result<Vec<f64>> {
        self.check()?.embed(text).await
    }
    async fn embed_many(&self, texts: &[String]) -> Result<Vec<Vec<f64>>> {
        self.check()?.embed_many(texts).await
    }
    async fn close(&self) {
        self.current().close().await
    }
    fn stats(&self) -> Option<Value> {
        self.current().stats()
    }
    fn model(&self) -> String {
        self.current().model()
    }
    fn embed_model(&self) -> String {
        self.current().embed_model()
    }
    fn base_url(&self) -> String {
        self.current().base_url()
    }
}

/// One line of what a pause/resume did, for the UI and the CLI.
#[derive(Clone, Debug)]
pub struct Step {
    pub ok: bool,
    pub text: String,
}

impl Step {
    fn ok(text: impl Into<String>) -> Self {
        Step { ok: true, text: text.into() }
    }
    fn fail(text: impl Into<String>) -> Self {
        Step { ok: false, text: text.into() }
    }
    pub fn to_value(&self) -> Value {
        json!({"ok": self.ok, "text": self.text})
    }
}

fn lms_bin() -> Option<PathBuf> {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .map(|d| d.join("lms"))
        .chain([home.join(".lmstudio/bin/lms")])
        .find(|p| p.is_file())
}

fn is_ollama(base_url: &str) -> bool {
    crate::backend::backend_for(base_url) == "ollama"
}

/// Free the RAM the chat and embedding models hold on the model server.
async fn unload(base_url: &str, models: &[String]) -> Vec<Step> {
    let base = base_url.trim_end_matches('/');
    if is_ollama(base) {
        let http = reqwest::Client::new();
        let mut out = Vec::new();
        for (i, m) in models.iter().enumerate() {
            // keep_alive 0 is Ollama's documented way to unload a model now
            let (path, body) = if i == 0 {
                ("api/generate", json!({"model": m, "keep_alive": 0}))
            } else {
                ("api/embed", json!({"model": m, "input": "", "keep_alive": 0}))
            };
            let r = http.post(format!("{base}/{path}")).json(&body).timeout(Duration::from_secs(10)).send().await;
            out.push(match r {
                Ok(resp) if resp.status().is_success() => Step::ok(format!("unloaded {m}")),
                Ok(resp) => Step::fail(format!("could not unload {m} (HTTP {})", resp.status().as_u16())),
                Err(e) => Step::fail(format!("could not unload {m}: {e}")),
            });
        }
        return out;
    }
    let Some(lms) = lms_bin() else {
        return vec![Step::fail("LM Studio's `lms` CLI was not found; unload the models in LM Studio to free RAM")];
    };
    let mut out = Vec::new();
    for m in models {
        let o = tokio::process::Command::new(&lms).args(["unload", m]).output().await;
        out.push(match o {
            Ok(o) if o.status.success() => Step::ok(format!("unloaded {m}")),
            Ok(o) => {
                let err = String::from_utf8_lossy(&o.stderr).to_lowercase();
                if err.contains("not loaded") || err.contains("no model") || err.contains("not found") {
                    Step::ok(format!("{m} was not loaded"))
                } else {
                    Step::fail(format!("could not unload {m}"))
                }
            }
            Err(e) => Step::fail(format!("could not run lms: {e}")),
        });
    }
    out
}

/// Stop all model use and free the models' RAM. Messages keep queueing and are learned after [`resume`].
pub async fn pause(store: &ChatMemoryStore, llm: &dyn LlmClient) -> Result<Vec<Step>> {
    store.set_paused(true)?;
    let mut steps = vec![Step::ok("paused: no model calls from any NeuralGraph process")];
    let models = vec![llm.model(), llm.embed_model()];
    if llm.base_url().is_empty() {
        return Ok(steps); // a test double: nothing to unload
    }
    steps.extend(unload(&llm.base_url(), &models).await);
    Ok(steps)
}

/// Models LM Studio has loaded right now (their API identifiers).
async fn lms_loaded(lms: &std::path::Path) -> Vec<String> {
    let Ok(o) = tokio::process::Command::new(lms).args(["ps", "--json"]).output().await else { return Vec::new() };
    serde_json::from_slice::<Value>(&o.stdout)
        .ok()
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(|m| m.get("identifier").and_then(Value::as_str).map(str::to_string))
        .collect()
}

/// Load the models into LM Studio for good (`lms load`), the mirror of [`pause`]'s `lms unload`. Loading them this way,
/// instead of on first use, keeps LM Studio's "auto-evict just-in-time models" from swapping one out for the other.
async fn lms_load(models: &[String]) -> Vec<Step> {
    let Some(lms) = lms_bin() else { return Vec::new() };
    let loaded = lms_loaded(&lms).await;
    let mut out = Vec::new();
    for m in models {
        if loaded.contains(m) {
            out.push(Step::ok(format!("{m} is loaded")));
            continue;
        }
        let t = Instant::now();
        let o = tokio::process::Command::new(&lms).args(["load", m, "--yes", "--identifier", m]).output().await;
        out.push(match o {
            Ok(o) if o.status.success() => Step::ok(format!("loaded {m} in {:.1}s", t.elapsed().as_secs_f64())),
            _ => Step::fail(format!("could not load {m} with lms; it loads on first use instead")),
        });
    }
    out
}

/// Turn model use back on and load both models, so the next real call is fast.
pub async fn resume(store: &ChatMemoryStore, llm: &dyn LlmClient) -> Result<Vec<Step>> {
    store.set_paused(false)?;
    let mut steps = vec![Step::ok("resumed: learning and recall are back on")];
    let base = llm.base_url();
    if !base.is_empty() && !is_ollama(&base) {
        let loaded = lms_load(&[llm.model(), llm.embed_model()]).await;
        if !loaded.is_empty() && loaded.iter().all(|s| s.ok) {
            steps.extend(loaded);
            return Ok(steps);
        }
        steps.extend(loaded.into_iter().filter(|s| !s.ok));
    }
    // Ollama (or LM Studio without its CLI): one tiny call each loads the model
    let t = Instant::now();
    steps.push(match llm.generate("Reply with one word: ok", 8, 0.0, 180.0).await {
        Ok(_) => Step::ok(format!("loaded {} in {:.1}s", llm.model(), t.elapsed().as_secs_f64())),
        Err(e) => Step::fail(format!("could not load {}: {}", llm.model(), e.qualified())),
    });
    let t = Instant::now();
    steps.push(match llm.embed("ok").await {
        Ok(_) => Step::ok(format!("loaded {} in {:.1}s", llm.embed_model(), t.elapsed().as_secs_f64())),
        Err(e) => Step::fail(format!("could not load {}: {}", llm.embed_model(), e.qualified())),
    });
    Ok(steps)
}
