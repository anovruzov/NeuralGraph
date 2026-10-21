//! LLM fact extraction for speaker profiles (port of `retrieval/llm_profile_extractor.py`).
//!
//! The local path goes through [`Llm::generate`] with the `llm_backend` model and URL. The optional OpenAI path
//! (`USE_OPENAI_EXTRACTION` with a non-empty `OPENAI_API_KEY`; the runner switches it off) posted to the OpenAI
//! chat endpoint directly: with an HTTP-backed [`Llm`] the same request is sent; a scripted model (no HTTP
//! client) is asked through [`Llm::generate`] with the same parameters, its reply standing for the message
//! content.
//!
//! Every failure yields an empty fact list, as in Python.

use neuralgraph_memory::pycompat::py_strip;
use parking_lot::Mutex;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};

use super::pytext::{aiohttp_accepts_json, py_json_loads};
use crate::llm_backend::{llm_base_url, llm_model, GenerateOptions, Llm};

/// `USE_OPENAI_EXTRACTION` (module flag, default off; callers set it as the Python runner did).
pub static USE_OPENAI_EXTRACTION: AtomicBool = AtomicBool::new(false);

/// `OPENAI_EXTRACTION_MODEL`.
pub const OPENAI_EXTRACTION_MODEL: &str = "gpt-4.1-mini";

/// The OpenAI endpoint of the extraction path.
pub const OPENAI_CHAT_URL: &str = "https://api.openai.com/v1/chat/completions";

static OPENAI_API_KEY_OVERRIDE: Mutex<Option<String>> = Mutex::new(None);

/// `OPENAI_API_KEY`: the module value (set with [`set_openai_api_key`]) or the environment variable.
pub fn openai_api_key() -> String {
    OPENAI_API_KEY_OVERRIDE.lock().clone().unwrap_or_else(|| std::env::var("OPENAI_API_KEY").unwrap_or_default())
}

/// Override the module's `OPENAI_API_KEY` (`None` reads the environment again).
pub fn set_openai_api_key(key: Option<String>) {
    *OPENAI_API_KEY_OVERRIDE.lock() = key;
}

/// `OLLAMA_BASE_URL` (= `llm_backend.LLM_BASE_URL`).
pub fn ollama_base_url() -> String {
    llm_base_url()
}

/// `OLLAMA_MODEL` (= `llm_backend.LLM_MODEL`).
pub fn ollama_model() -> String {
    llm_model()
}

/// The extraction prompt for one message.
pub fn extraction_prompt(speaker_name: &str, message_text: &str) -> String {
    format!(
        "Extract any notable FACTS about {speaker_name} from this message.

MESSAGE:
{message_text}

WHAT TO EXTRACT:
- Concrete, verifiable information about {speaker_name}
- Actions they performed/will perform
- Things they own, read, visited, attended
- Preferences, goals, characteristics
- Any details that could help answer future questions about {speaker_name}

WHAT NOT TO EXTRACT:
- Opinions about others
- General statements not about {speaker_name}
- Vague or unverifiable claims

FORMAT:
- Short, precise fact phrases (5-10 words each)
- Use past/present tense naturally
- Preserve specific names, titles, locations
- Each fact should be self-contained

Return ONLY valid JSON:
{{
  \"facts\": [\"fact1\", \"fact2\", \"fact3\"]
}}

If no notable facts about {speaker_name}, return {{\"facts\": []}}"
    )
}

/// Strip a leading Markdown code fence (```` ```json ... ``` ````) the way both profile modules did:
/// the text between the first two fences, minus a `json` tag, stripped.
pub fn strip_code_fence(text: &str) -> String {
    if !text.starts_with("```") {
        return text.to_string();
    }
    let inner = text.split("```").nth(1).unwrap_or("");
    let inner = inner.strip_prefix("json").unwrap_or(inner);
    py_strip(inner).to_string()
}

/// The fact-list parsing: `json.loads(...)["facts"]` as stripped non-empty strings; anything else → `[]`.
pub fn parse_facts(response_text: &str) -> Vec<String> {
    let text = strip_code_fence(response_text);
    let Some(Value::Object(extracted)) = py_json_loads(&text) else { return Vec::new() };
    match extracted.get("facts") {
        None => Vec::new(),
        Some(Value::Array(facts)) => {
            facts.iter().filter_map(Value::as_str).map(py_strip).filter(|f| !f.is_empty()).map(str::to_string).collect()
        }
        Some(_) => Vec::new(),
    }
}

/// The OpenAI request; `Ok(None)` stands for "return []" (non-200 status, unexpected body).
async fn openai_extract(client: &reqwest::Client, url: &str, api_key: &str, prompt: &str) -> Option<String> {
    let body = json!({
        "model": OPENAI_EXTRACTION_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 500,
    });
    let resp = client
        .post(url)
        .header("Authorization", format!("Bearer {api_key}"))
        .header("Content-Type", "application/json")
        .json(&body)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .ok()?;
    if resp.status().as_u16() != 200 {
        return None;
    }
    let ctype =
        resp.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let text = resp.text().await.ok()?;
    if !aiohttp_accepts_json(&ctype) {
        return None;
    }
    let data = py_json_loads(&text)?;
    // data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    let choices = match data.as_object()?.get("choices") {
        None => json!([{}]),
        Some(v) => v.clone(),
    };
    let first = choices.as_array()?.first()?.as_object()?.clone();
    let message = match first.get("message") {
        None => json!({}),
        Some(v) => v.clone(),
    };
    match message.as_object()?.get("content") {
        None => Some(String::new()),
        Some(Value::String(s)) => Some(py_strip(s).to_string()),
        Some(_) => None,
    }
}

/// `extract_speaker_facts_llm(session, speaker_name, message_text)` with the OpenAI URL as a parameter (tests
/// point it at a local server).
pub async fn extract_speaker_facts_llm_at(
    llm: &dyn Llm,
    speaker_name: &str,
    message_text: &str,
    openai_url: &str,
) -> Vec<String> {
    let prompt = extraction_prompt(speaker_name, message_text);
    let api_key = openai_api_key();
    let response_text = if USE_OPENAI_EXTRACTION.load(Ordering::Relaxed) && !api_key.is_empty() {
        match llm.http() {
            Some(client) => match openai_extract(client, openai_url, &api_key, &prompt).await {
                Some(text) => text,
                None => return Vec::new(),
            },
            None => {
                let opts = GenerateOptions::default()
                    .model(OPENAI_EXTRACTION_MODEL)
                    .base_url(openai_url)
                    .temperature(0.1)
                    .max_tokens(500)
                    .timeout(30.0);
                match llm.generate(&prompt, &opts).await {
                    Ok(text) => py_strip(&text).to_string(),
                    Err(_) => return Vec::new(),
                }
            }
        }
    } else {
        let opts = GenerateOptions::default()
            .model(ollama_model())
            .base_url(ollama_base_url())
            .temperature(0.1)
            .max_tokens(500)
            .timeout(60.0);
        match llm.generate(&prompt, &opts).await {
            Ok(text) => text,
            Err(_) => return Vec::new(),
        }
    };
    parse_facts(&response_text)
}

/// `extract_speaker_facts_llm(session, speaker_name, message_text)`: short fact phrases about the speaker
/// (empty on any failure).
pub async fn extract_speaker_facts_llm(llm: &dyn Llm, speaker_name: &str, message_text: &str) -> Vec<String> {
    extract_speaker_facts_llm_at(llm, speaker_name, message_text, OPENAI_CHAT_URL).await
}

/// `extract_facts_parallel(session, messages, batch_size=15)`: facts for every `(speaker, text)` message, in
/// order, extracting each batch of `batch_size` messages concurrently. A zero batch size is Python's
/// `range()` error; a negative one extracts nothing.
pub async fn extract_facts_parallel<S: AsRef<str>, T: AsRef<str>>(
    llm: &dyn Llm,
    messages: &[(S, T)],
    batch_size: i64,
) -> Result<Vec<Vec<String>>, String> {
    if batch_size == 0 {
        return Err("range() arg 3 must not be zero".into());
    }
    let mut results = Vec::with_capacity(messages.len());
    if batch_size < 0 {
        return Ok(results);
    }
    for batch in messages.chunks(batch_size as usize) {
        let tasks = batch.iter().map(|(speaker, text)| extract_speaker_facts_llm(llm, speaker.as_ref(), text.as_ref()));
        results.extend(futures::future::join_all(tasks).await);
    }
    Ok(results)
}

/// The module's `__main__` demo: extract facts from one sample message and print them; returns the lines it
/// printed.
pub async fn run_example(llm: &dyn Llm) -> Vec<String> {
    let message = "I went camping at the beach last weekend with my kids. We also did some pottery in the pottery class I signed up for. I've been reading 'Charlotte's Web' to them at night.";
    let mut out = vec!["Testing UNIVERSAL fact extraction...".to_string(), format!("Message: {message}\n")];
    let facts = extract_speaker_facts_llm(llm, "Melanie", message).await;
    out.push("Extracted facts:".into());
    out.extend(facts.iter().map(|f| format!("  - {f}")));
    for line in &out {
        println!("{line}");
    }
    out
}
