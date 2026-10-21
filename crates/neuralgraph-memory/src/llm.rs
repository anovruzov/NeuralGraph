//! LLM client abstraction for the chat-memory worker.
//!
//! The worker never talks to the HTTP backend directly; it talks to an [`LlmClient`]. That gives us two things:
//!
//! * [`BackendLlmClient`] wraps [`crate::backend`] (LM Studio or Ollama) with one shared HTTP client, a semaphore
//!   that bounds how many model calls run in parallel, and bounded retries with exponential backoff for transient
//!   HTTP/network failures.
//! * [`FakeLlmClient`] is a deterministic, server-free stand-in used by `--fake-llm`, the demos and the tests. It
//!   answers prompts from a script keyed on a prompt marker, and produces hash-based embeddings whose cosine
//!   similarity behaves sensibly (identical text -> 1.0, shared words -> higher).

use crate::error::{MemoryError, Result};
use crate::pycompat::{fnum, obj};
use crate::textutil::stem;
use async_trait::async_trait;
use blake2::digest::consts::U8;
use blake2::{Blake2b, Digest};
use futures::future::BoxFuture;
use regex::Regex;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::Semaphore;

/// Minimal async interface the worker and retriever depend on.
#[async_trait]
pub trait LlmClient: Send + Sync {
    async fn generate(&self, prompt: &str, max_tokens: i64, temperature: f64, timeout: f64) -> Result<String>;
    async fn embed(&self, text: &str) -> Result<Vec<f64>>;
    async fn embed_many(&self, texts: &[String]) -> Result<Vec<Vec<f64>>>;
    async fn close(&self);
    /// Call/latency counters for the dashboard (`None` when the client keeps none).
    fn stats(&self) -> Option<Value> {
        None
    }
    fn model(&self) -> String {
        "fake".into()
    }
    fn embed_model(&self) -> String {
        "fake".into()
    }
    fn base_url(&self) -> String {
        String::new()
    }
}

#[derive(Clone, Debug, Default)]
pub struct LlmStats {
    pub generate_calls: i64,
    pub generate_failures: i64,
    pub generate_seconds: f64,
    pub embed_calls: i64,
    pub embed_failures: i64,
    pub embed_seconds: f64,
    pub prompt_chars: i64,
    pub completion_chars: i64,
}

impl LlmStats {
    pub fn as_value(&self) -> Value {
        obj([
            ("generate_calls", Value::from(self.generate_calls)),
            ("generate_failures", Value::from(self.generate_failures)),
            ("generate_seconds", fnum(self.generate_seconds)),
            ("embed_calls", Value::from(self.embed_calls)),
            ("embed_failures", Value::from(self.embed_failures)),
            ("embed_seconds", fnum(self.embed_seconds)),
            ("prompt_chars", Value::from(self.prompt_chars)),
            ("completion_chars", Value::from(self.completion_chars)),
        ])
    }
}

// ---------------------------------------------------------------------------------------------
// Real backend
// ---------------------------------------------------------------------------------------------

/// LLM/embedding client over [`crate::backend`].
///
/// `max_parallel` bounds concurrent generate+embed HTTP calls (match it to the server: Ollama's
/// `OLLAMA_NUM_PARALLEL`, LM Studio's parallel-request setting); `retries` are additional attempts after the first
/// failure (network / 5xx / timeout); `backoff_seconds` is the base of the exponential backoff between attempts.
pub struct BackendLlmClient {
    pub base_url: String,
    pub model: String,
    pub embed_model: String,
    pub max_parallel: usize,
    pub retries: u32,
    pub backoff_seconds: f64,
    sem: Semaphore,
    http: reqwest::Client,
    stats: Mutex<LlmStats>,
}

impl BackendLlmClient {
    pub fn new(
        base_url: Option<String>,
        model: Option<String>,
        embed_model: Option<String>,
        max_parallel: usize,
    ) -> Self {
        let max_parallel = max_parallel.max(1);
        BackendLlmClient {
            base_url: base_url
                .filter(|s| !s.is_empty())
                .unwrap_or_else(crate::backend::default_base_url)
                .trim_end_matches('/')
                .to_string(),
            model: model.filter(|s| !s.is_empty()).unwrap_or_else(crate::backend::default_model),
            embed_model: embed_model.filter(|s| !s.is_empty()).unwrap_or_else(crate::backend::default_embed_model),
            max_parallel,
            retries: 2,
            backoff_seconds: 1.0,
            sem: Semaphore::new(max_parallel),
            http: reqwest::Client::new(),
            stats: Mutex::new(LlmStats::default()),
        }
    }

    async fn with_retries<T, F, Fut>(&self, what: &str, mut call: F) -> Result<T>
    where
        F: FnMut() -> Fut,
        Fut: std::future::Future<Output = std::result::Result<T, String>>,
    {
        let mut last = String::new();
        for attempt in 0..=self.retries {
            let outcome = {
                let _permit = self.sem.acquire().await.expect("semaphore never closed");
                call().await
            };
            match outcome {
                Ok(v) => return Ok(v),
                Err(e) => {
                    last = e;
                    if attempt < self.retries {
                        let delay = self.backoff_seconds * 2f64.powi(attempt as i32);
                        tracing::warn!(
                            "{what} failed (attempt {}/{}): {last}; retrying in {delay:.1}s",
                            attempt + 1,
                            self.retries + 1
                        );
                        tokio::time::sleep(Duration::from_secs_f64(delay)).await;
                    }
                }
            }
        }
        Err(MemoryError::Llm(format!("{what} failed after {} attempts: {last}", self.retries + 1)))
    }

    fn stats_mut(&self) -> std::sync::MutexGuard<'_, LlmStats> {
        self.stats.lock().unwrap_or_else(|e| e.into_inner())
    }
}

#[async_trait]
impl LlmClient for BackendLlmClient {
    async fn generate(&self, prompt: &str, max_tokens: i64, temperature: f64, timeout: f64) -> Result<String> {
        let t0 = Instant::now();
        {
            let mut s = self.stats_mut();
            s.generate_calls += 1;
            s.prompt_chars += crate::pycompat::py_len(prompt) as i64;
        }
        let out = self
            .with_retries("generate", || {
                crate::backend::llm_generate(
                    &self.http,
                    prompt,
                    &self.model,
                    &self.base_url,
                    temperature,
                    max_tokens,
                    timeout,
                )
            })
            .await;
        let mut s = self.stats_mut();
        s.generate_seconds += t0.elapsed().as_secs_f64();
        match out {
            Ok(text) => {
                s.completion_chars += crate::pycompat::py_len(&text) as i64;
                Ok(text)
            }
            Err(e) => {
                s.generate_failures += 1;
                Err(e)
            }
        }
    }

    async fn embed(&self, text: &str) -> Result<Vec<f64>> {
        let t0 = Instant::now();
        self.stats_mut().embed_calls += 1;
        let out = self
            .with_retries("embed", || async {
                let v = crate::backend::llm_embed(&self.http, text, &self.embed_model, &self.base_url, 60.0).await?;
                if v.is_empty() {
                    return Err("empty embedding returned".to_string());
                }
                Ok(v)
            })
            .await;
        let mut s = self.stats_mut();
        s.embed_seconds += t0.elapsed().as_secs_f64();
        if out.is_err() {
            s.embed_failures += 1;
        }
        out
    }

    async fn embed_many(&self, texts: &[String]) -> Result<Vec<Vec<f64>>> {
        // bounded by the semaphore inside embed(); join_all keeps ordering
        let results = futures::future::join_all(texts.iter().map(|t| self.embed(t))).await;
        results.into_iter().collect()
    }

    async fn close(&self) {}

    fn stats(&self) -> Option<Value> {
        Some(self.stats_mut().as_value())
    }

    fn model(&self) -> String {
        self.model.clone()
    }

    fn embed_model(&self) -> String {
        self.embed_model.clone()
    }

    fn base_url(&self) -> String {
        self.base_url.clone()
    }
}

// ---------------------------------------------------------------------------------------------
// Deterministic fake
// ---------------------------------------------------------------------------------------------

static WORD_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[a-z0-9]+").unwrap());

/// Deterministic bag-of-words hashing embedding (unit norm). Shared words push vectors together, so cosine
/// similarity is a usable proxy for lexical overlap. Identical text -> identical vector.
pub fn fake_embedding(text: &str, dim: usize) -> Vec<f64> {
    let mut vec = vec![0.0f64; dim];
    let lower = text.to_lowercase();
    let words: Vec<String> = WORD_RE.find_iter(&lower).map(|m| stem(m.as_str())).collect();
    if words.is_empty() {
        vec[0] = 1.0;
        return vec;
    }
    for w in &words {
        let d = Blake2b::<U8>::digest(w.as_bytes());
        let idx = (u32::from_be_bytes([d[0], d[1], d[2], d[3]]) as usize) % dim;
        let sign = if d[4] % 2 == 0 { 1.0 } else { -1.0 };
        vec[idx] += sign;
    }
    let mut sq = 0.0f64;
    for x in &vec {
        sq += x * x;
    }
    let mut norm = sq.sqrt();
    if norm == 0.0 {
        norm = 1.0;
    }
    vec.iter().map(|x| x / norm).collect()
}

/// Computes a scripted completion from the prompt (may fail like a Python responder raising).
pub type Responder = Arc<dyn Fn(&str) -> Result<String> + Send + Sync>;
/// Replaces `generate` entirely (tests that block or slow the model).
pub type GenerateHook = Arc<dyn Fn(String) -> BoxFuture<'static, Result<String>> + Send + Sync>;

/// A script entry: when `marker` occurs in the prompt, answer with `response`.
#[derive(Clone)]
pub enum ScriptResponse {
    Text(String),
    Func(Responder),
}

/// Scripted LLM for deterministic runs.
///
/// `responder` is called with the prompt and must return the completion text. Without one, `script` is consulted:
/// the first marker found in the prompt wins; when nothing matches, `default_response` is returned.
/// `fail_times` makes the first N `generate` calls fail (retry/backoff tests); `latency` adds a sleep per call so
/// parallelism can be observed.
pub struct FakeLlmClient {
    pub script: Vec<(String, ScriptResponse)>,
    pub default_response: String,
    pub responder: Option<Responder>,
    pub dim: usize,
    pub fail_times: AtomicI64,
    pub latency: f64,
    pub prompts: Mutex<Vec<String>>,
    pub embedded: Mutex<Vec<String>>,
    pub stats: Mutex<LlmStats>,
    /// calls currently inside `generate` (observes parallelism)
    pub in_flight: AtomicI64,
    pub max_in_flight: AtomicI64,
    pub closed: AtomicBool,
    /// When set, `generate` is this function and nothing else (no recording, no stats).
    pub generate_hook: Option<GenerateHook>,
    /// When set, `embed`/`embed_many` fail with this message (embedding server down).
    pub embed_error: Option<String>,
}

impl Default for FakeLlmClient {
    fn default() -> Self {
        FakeLlmClient {
            script: Vec::new(),
            default_response: "{}".into(),
            responder: None,
            dim: 256,
            fail_times: AtomicI64::new(0),
            latency: 0.0,
            prompts: Mutex::new(Vec::new()),
            embedded: Mutex::new(Vec::new()),
            stats: Mutex::new(LlmStats::default()),
            in_flight: AtomicI64::new(0),
            max_in_flight: AtomicI64::new(0),
            closed: AtomicBool::new(false),
            generate_hook: None,
            embed_error: None,
        }
    }
}

impl FakeLlmClient {
    /// Fake answering from `(marker, response)` pairs.
    pub fn with_script(script: Vec<(&str, String)>) -> Self {
        FakeLlmClient {
            script: script.into_iter().map(|(m, r)| (m.to_string(), ScriptResponse::Text(r))).collect(),
            ..Default::default()
        }
    }

    pub fn with_default(response: &str) -> Self {
        FakeLlmClient { default_response: response.to_string(), ..Default::default() }
    }

    pub fn max_in_flight(&self) -> i64 {
        self.max_in_flight.load(Ordering::SeqCst)
    }

    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    pub fn generate_calls(&self) -> i64 {
        self.stats.lock().unwrap_or_else(|e| e.into_inner()).generate_calls
    }

    pub fn prompts(&self) -> Vec<String> {
        self.prompts.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn set_fail_times(&self, n: i64) {
        self.fail_times.store(n, Ordering::SeqCst);
    }

    fn answer(&self, prompt: &str) -> Result<String> {
        if let Some(r) = &self.responder {
            return r(prompt);
        }
        for (marker, response) in &self.script {
            if prompt.contains(marker.as_str()) {
                return match response {
                    ScriptResponse::Text(t) => Ok(t.clone()),
                    ScriptResponse::Func(f) => f(prompt),
                };
            }
        }
        Ok(self.default_response.clone())
    }
}

struct InFlight<'a>(&'a AtomicI64);

impl Drop for InFlight<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

#[async_trait]
impl LlmClient for FakeLlmClient {
    async fn generate(&self, prompt: &str, _max_tokens: i64, _temperature: f64, _timeout: f64) -> Result<String> {
        if let Some(hook) = &self.generate_hook {
            return hook(prompt.to_string()).await;
        }
        self.prompts.lock().unwrap_or_else(|e| e.into_inner()).push(prompt.to_string());
        self.stats.lock().unwrap_or_else(|e| e.into_inner()).generate_calls += 1;
        let now = self.in_flight.fetch_add(1, Ordering::SeqCst) + 1;
        self.max_in_flight.fetch_max(now, Ordering::SeqCst);
        let _guard = InFlight(&self.in_flight);
        if self.latency > 0.0 {
            tokio::time::sleep(Duration::from_secs_f64(self.latency)).await;
        }
        if self.fail_times.load(Ordering::SeqCst) > 0 {
            self.fail_times.fetch_sub(1, Ordering::SeqCst);
            self.stats.lock().unwrap_or_else(|e| e.into_inner()).generate_failures += 1;
            return Err(MemoryError::Llm("scripted failure".into()));
        }
        self.answer(prompt)
    }

    async fn embed(&self, text: &str) -> Result<Vec<f64>> {
        if let Some(e) = &self.embed_error {
            return Err(MemoryError::Other("RuntimeError", e.clone()));
        }
        self.embedded.lock().unwrap_or_else(|e| e.into_inner()).push(text.to_string());
        self.stats.lock().unwrap_or_else(|e| e.into_inner()).embed_calls += 1;
        if self.latency > 0.0 {
            tokio::time::sleep(Duration::from_secs_f64(self.latency / 4.0)).await;
        }
        Ok(fake_embedding(text, self.dim))
    }

    async fn embed_many(&self, texts: &[String]) -> Result<Vec<Vec<f64>>> {
        if let Some(e) = &self.embed_error {
            return Err(MemoryError::Other("RuntimeError", e.clone()));
        }
        let mut out = Vec::with_capacity(texts.len());
        for t in texts {
            out.push(self.embed(t).await?);
        }
        Ok(out)
    }

    async fn close(&self) {
        self.closed.store(true, Ordering::SeqCst);
    }

    fn stats(&self) -> Option<Value> {
        Some(self.stats.lock().unwrap_or_else(|e| e.into_inner()).as_value())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fake_embedding_is_deterministic_and_lexical() {
        let a = fake_embedding("Ali moved to Berlin", 256);
        let b = fake_embedding("Ali moved to Berlin", 256);
        let c = fake_embedding("weather nice", 256);
        assert_eq!(a, b);
        let dot = |x: &[f64], y: &[f64]| x.iter().zip(y).map(|(i, j)| i * j).sum::<f64>();
        assert!((dot(&a, &a) - 1.0).abs() < 1e-6);
        assert!(dot(&a, &c) < 0.5);
    }
}
