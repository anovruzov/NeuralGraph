//! The single LLM/embedding backend of the research code (port of `NeuralGraph/llm_backend.py`).
//!
//! The HTTP protocol, environment variables and response handling are the product crate's
//! (`neuralgraph_memory::backend`, itself a port of the same Python module); this module adds the defaults the
//! Python keyword arguments had and an [`Llm`] trait so the research code can be driven by a scripted model in
//! tests. The Python passed an `aiohttp.ClientSession` around; here an `&dyn Llm` takes its place.
//!
//! Environment (read on every call, as the product crate does):
//!     LLM_BASE_URL (default http://127.0.0.1:1234), LLM_MODEL (default google/gemma-4-e4b),
//!     EMBED_MODEL (default text-embedding-nomic-embed-text-v1.5), LLM_BACKEND ("openai" | "ollama"),
//!     LLM_REASONING_EFFORT (default "none"), LLM_NO_THINK_PREFILL (default off).

use async_trait::async_trait;
use std::cell::RefCell;

pub use neuralgraph_memory::backend::{backend_for, strip_thinking};

/// `LLM_BASE_URL` (trailing slashes removed).
pub fn llm_base_url() -> String {
    neuralgraph_memory::backend::default_base_url()
}

/// `LLM_MODEL`.
pub fn llm_model() -> String {
    neuralgraph_memory::backend::default_model()
}

/// `EMBED_MODEL`.
pub fn embed_model() -> String {
    neuralgraph_memory::backend::default_embed_model()
}

/// Keyword arguments of `llm_generate` (defaults as in Python).
#[derive(Clone, Debug, PartialEq)]
pub struct GenerateOptions {
    pub model: Option<String>,
    pub base_url: Option<String>,
    pub temperature: f64,
    pub max_tokens: i64,
    pub timeout_seconds: f64,
}

impl Default for GenerateOptions {
    fn default() -> Self {
        GenerateOptions { model: None, base_url: None, temperature: 0.0, max_tokens: 256, timeout_seconds: 60.0 }
    }
}

impl GenerateOptions {
    pub fn max_tokens(mut self, n: i64) -> Self {
        self.max_tokens = n;
        self
    }
    pub fn temperature(mut self, t: f64) -> Self {
        self.temperature = t;
        self
    }
    pub fn timeout(mut self, seconds: f64) -> Self {
        self.timeout_seconds = seconds;
        self
    }
    pub fn model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }
    pub fn base_url(mut self, url: impl Into<String>) -> Self {
        self.base_url = Some(url.into());
        self
    }
}

/// Keyword arguments of `llm_embed`.
#[derive(Clone, Debug, PartialEq)]
pub struct EmbedOptions {
    pub model: Option<String>,
    pub base_url: Option<String>,
    pub timeout_seconds: f64,
}

impl Default for EmbedOptions {
    fn default() -> Self {
        EmbedOptions { model: None, base_url: None, timeout_seconds: 30.0 }
    }
}

/// What the research code needs from a model server. `Err` carries the message of the exception Python raised.
#[async_trait(?Send)]
pub trait Llm {
    async fn generate(&self, prompt: &str, opts: &GenerateOptions) -> Result<String, String>;
    async fn embed(&self, text: &str, opts: &EmbedOptions) -> Result<Vec<f64>, String>;
    /// The HTTP client, for the few harness calls that talk to an endpoint directly (batch embeddings, the
    /// OpenAI judge). `None` for scripted models.
    fn http(&self) -> Option<&reqwest::Client> {
        None
    }
}

/// The real backend over HTTP (LM Studio / OpenAI-compatible, or Ollama).
#[derive(Clone, Default)]
pub struct HttpLlm {
    pub client: reqwest::Client,
}

impl HttpLlm {
    pub fn new() -> Self {
        HttpLlm { client: reqwest::Client::new() }
    }
}

#[async_trait(?Send)]
impl Llm for HttpLlm {
    async fn generate(&self, prompt: &str, opts: &GenerateOptions) -> Result<String, String> {
        llm_generate(&self.client, prompt, opts).await
    }

    async fn embed(&self, text: &str, opts: &EmbedOptions) -> Result<Vec<f64>, String> {
        llm_embed(&self.client, text, opts).await
    }

    fn http(&self) -> Option<&reqwest::Client> {
        Some(&self.client)
    }
}

/// `llm_generate(session, prompt, ...)`: the model's text with any `<think>` block stripped.
pub async fn llm_generate(client: &reqwest::Client, prompt: &str, opts: &GenerateOptions) -> Result<String, String> {
    let base_url = opts.base_url.clone().unwrap_or_else(llm_base_url);
    let model = opts.model.clone().unwrap_or_else(llm_model);
    neuralgraph_memory::backend::llm_generate(
        client,
        prompt,
        &model,
        &base_url,
        opts.temperature,
        opts.max_tokens,
        opts.timeout_seconds,
    )
    .await
}

/// `llm_embed(session, text, ...)`.
pub async fn llm_embed(client: &reqwest::Client, text: &str, opts: &EmbedOptions) -> Result<Vec<f64>, String> {
    let base_url = opts.base_url.clone().unwrap_or_else(llm_base_url);
    let model = opts.model.clone().unwrap_or_else(embed_model);
    neuralgraph_memory::backend::llm_embed(client, text, &model, &base_url, opts.timeout_seconds).await
}

/// One recorded call to a [`ScriptedLlm`].
#[derive(Clone, Debug, PartialEq)]
pub enum LlmCall {
    Generate { prompt: String, opts: GenerateOptions },
    Embed { text: String },
}

type Responder = Box<dyn Fn(&str) -> Result<String, String>>;
type Embedder = Box<dyn Fn(&str) -> Result<Vec<f64>, String>>;

/// A deterministic stand-in model for tests: answers through closures and records every call, so prompt
/// construction and response parsing can be compared with what the Python sent and parsed.
pub struct ScriptedLlm {
    respond: Responder,
    embedder: Embedder,
    pub calls: RefCell<Vec<LlmCall>>,
}

impl ScriptedLlm {
    pub fn new(respond: impl Fn(&str) -> Result<String, String> + 'static) -> Self {
        ScriptedLlm {
            respond: Box::new(respond),
            embedder: Box::new(|_| Err("no embedder scripted".into())),
            calls: RefCell::new(Vec::new()),
        }
    }

    /// Answer prompts in order from a list (cycling is an error: the script ran out).
    pub fn sequence(responses: Vec<String>) -> Self {
        let queue = RefCell::new(std::collections::VecDeque::from(responses));
        Self::new(move |_| queue.borrow_mut().pop_front().ok_or_else(|| "scripted model ran out of responses".into()))
    }

    pub fn with_embedder(mut self, embed: impl Fn(&str) -> Result<Vec<f64>, String> + 'static) -> Self {
        self.embedder = Box::new(embed);
        self
    }

    /// Prompts of every `generate` call so far, in order.
    pub fn prompts(&self) -> Vec<String> {
        self.calls
            .borrow()
            .iter()
            .filter_map(|c| match c {
                LlmCall::Generate { prompt, .. } => Some(prompt.clone()),
                LlmCall::Embed { .. } => None,
            })
            .collect()
    }
}

#[async_trait(?Send)]
impl Llm for ScriptedLlm {
    async fn generate(&self, prompt: &str, opts: &GenerateOptions) -> Result<String, String> {
        self.calls.borrow_mut().push(LlmCall::Generate { prompt: prompt.to_string(), opts: opts.clone() });
        (self.respond)(prompt)
    }

    async fn embed(&self, text: &str, _opts: &EmbedOptions) -> Result<Vec<f64>, String> {
        self.calls.borrow_mut().push(LlmCall::Embed { text: text.to_string() });
        (self.embedder)(text)
    }
}
