//! [`ChatMemory`]: the one object applications use.
//!
//! ```no_run
//! # async fn demo() -> neuralgraph_memory::error::Result<()> {
//! use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, MessageInput};
//! let cm = ChatMemory::open("~/.neuralgraph/chat_memory.db", None, ChatMemoryConfig::default())?;
//! cm.start().await?;                                                     // background worker (non-blocking)
//! cm.add_message("chat-42", "user", "I moved to Berlin last month", MessageInput::default())?; // returns at once
//! let block = cm.context_for("where does the user live?", None, None, &Default::default()).await?;
//! cm.close().await;
//! # Ok(()) }
//! ```
//!
//! The chat path (`add_message`) only writes the raw message and a queue entry; every LLM call happens in the
//! worker. `ChatMemory` also keeps the token ledger behind the dashboard's "tokens saved" figure and computes the
//! five-axis grade.

use crate::error::{MemoryError, Result};
use crate::extraction::{cos, parse_when, ExtractionConfig, MemoryExtractor};
use crate::llm::{BackendLlmClient, LlmClient};
use crate::models::{
    new_id, now_iso, parse_iso, total_seconds, utcnow, ChatMessage, Memory, RetrievedMemory, MEMORY_KINDS,
};
use crate::pycompat::{fnum, obj, py_len, py_prefix, py_round, py_round_int, py_str, py_truthy};
use crate::retrieval::{MemoryRetriever, RetrievalConfig, SearchOptions, DEFAULT_HEADER};
use crate::store::{AddMessage, ChatMemoryStore, MemoryQuery};
use crate::textutil::{content_hash, norm_entity, normalize_ws};
use crate::worker::{MemoryWorker, WorkerConfig};
use indexmap::IndexSet;
use serde_json::Value;
use std::sync::{Arc, Mutex, RwLock};

/// Cheap tokenizer-free estimate (~4 chars per token for English chat text).
pub fn estimate_tokens(text: &str) -> i64 {
    if text.is_empty() {
        return 0;
    }
    py_round_int(py_len(text) as f64 / 4.0).max(1)
}

fn chars_to_tokens(n: i64) -> i64 {
    if n == 0 {
        0
    } else {
        py_round_int(n as f64 / 4.0).max(1)
    }
}

#[derive(Clone, Debug)]
pub struct ChatMemoryConfig {
    /// let a user turn and its reply land in the same batch
    pub debounce_seconds: f64,
    pub max_attempts: i64,
    pub extraction: ExtractionConfig,
    pub worker: WorkerConfig,
    pub retrieval: RetrievalConfig,
    pub llm_max_parallel: usize,
    pub llm_model: Option<String>,
    pub llm_base_url: Option<String>,
    pub embed_model: Option<String>,
}

impl Default for ChatMemoryConfig {
    fn default() -> Self {
        ChatMemoryConfig {
            debounce_seconds: 1.0,
            max_attempts: 5,
            extraction: ExtractionConfig::default(),
            worker: WorkerConfig::default(),
            retrieval: RetrievalConfig::default(),
            llm_max_parallel: 4,
            llm_model: None,
            llm_base_url: None,
            embed_model: None,
        }
    }
}

/// Optional fields of a message.
#[derive(Clone, Debug, Default)]
pub struct MessageInput {
    pub role: String,
    pub sent_at: Option<String>,
    pub message_id: Option<String>,
    pub metadata: Option<Value>,
}

/// Options for [`ChatMemory::remember`] (same defaults as before).
#[derive(Clone, Debug)]
pub struct RememberOptions {
    pub subject: String,
    pub kind: String,
    pub importance: f64,
    pub confidence: f64,
    pub chat_id: String,
    pub when: Option<String>,
    pub speaker: Option<String>,
}

impl Default for RememberOptions {
    fn default() -> Self {
        RememberOptions {
            subject: "user".into(),
            kind: "fact".into(),
            importance: 0.8,
            confidence: 1.0,
            chat_id: "manual".into(),
            when: None,
            speaker: None,
        }
    }
}

#[derive(Default)]
struct ContextLedger {
    queries: i64,
    context_tokens: i64,
    raw_tokens_avoided: i64,
}

pub struct ChatMemory {
    pub config: ChatMemoryConfig,
    pub store: Arc<ChatMemoryStore>,
    llm: RwLock<Arc<dyn LlmClient>>,
    pub extractor: Arc<MemoryExtractor>,
    pub worker: Arc<MemoryWorker>,
    pub retriever: Arc<MemoryRetriever>,
    ledger: Mutex<ContextLedger>,
}

/// A JSON value as an optional string (`null`/missing -> None, non-strings via `str()`).
fn opt_string(v: Option<&Value>) -> Option<String> {
    match v {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => Some(py_str(other)),
    }
}

impl ChatMemory {
    /// Open the store at `db_path` with `llm` (or the configured local model server).
    pub fn open(db_path: &str, llm: Option<Arc<dyn LlmClient>>, config: ChatMemoryConfig) -> Result<Self> {
        let store = Arc::new(ChatMemoryStore::open(db_path)?);
        let llm: Arc<dyn LlmClient> = match llm {
            Some(l) => l,
            None => Arc::new(BackendLlmClient::new(
                config.llm_base_url.clone(),
                config.llm_model.clone(),
                config.embed_model.clone(),
                config.llm_max_parallel,
            )),
        };
        let extractor = Arc::new(MemoryExtractor::new(store.clone(), llm.clone(), config.extraction.clone()));
        let worker = Arc::new(MemoryWorker::new(store.clone(), llm.clone(), extractor.clone(), config.worker.clone()));
        let retriever = Arc::new(MemoryRetriever::new(store.clone(), llm.clone(), config.retrieval.clone()));
        Ok(ChatMemory {
            config,
            store,
            llm: RwLock::new(llm),
            extractor,
            worker,
            retriever,
            ledger: Mutex::new(ContextLedger::default()),
        })
    }

    pub fn llm(&self) -> Arc<dyn LlmClient> {
        self.llm.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Replace the model used for status and maintenance (the extractor and retriever keep theirs).
    pub fn set_llm(&self, llm: Arc<dyn LlmClient>) {
        *self.llm.write().unwrap_or_else(|e| e.into_inner()) = llm.clone();
        self.worker.set_llm(llm);
    }

    // ------------------------------------------------------------------ lifecycle
    pub async fn start(&self) -> Result<()> {
        self.worker.start().await
    }

    pub async fn stop(&self, timeout: f64) {
        self.worker.stop(timeout).await;
    }

    pub async fn close(&self) {
        self.stop(30.0).await;
        self.llm().close().await;
        self.store.close();
    }

    // ------------------------------------------------------------------ ingestion (never blocks on the LLM)
    pub fn add_message(&self, chat_id: &str, speaker: &str, text: &str, input: MessageInput) -> Result<ChatMessage> {
        if chat_id.is_empty() {
            return Err(MemoryError::Value("chat_id must be a non-empty string".into()));
        }
        let mut role = input.role.clone();
        if role.is_empty() {
            role = match speaker.to_lowercase().as_str() {
                "assistant" => "assistant".into(),
                "user" => "user".into(),
                _ => String::new(),
            };
        }
        let opts = AddMessage {
            role,
            sent_at: input.sent_at,
            message_id: input.message_id,
            metadata: input.metadata,
            enqueue: true,
            debounce_seconds: self.config.debounce_seconds,
            max_attempts: self.config.max_attempts,
        };
        let (msg, created) = self.store.add_message(chat_id, speaker, text, &opts)?;
        if created {
            self.worker.wake();
        }
        Ok(msg)
    }

    /// Bulk import: each item is {"speaker", "text", "role"?, "sent_at"?, "message_id"?, "metadata"?}.
    pub fn add_messages(&self, chat_id: &str, messages: &[Value]) -> Result<Vec<ChatMessage>> {
        let mut out = Vec::with_capacity(messages.len());
        for m in messages {
            let get = |k: &str| m.get(k);
            let speaker = crate::pycompat::py_or(&[get("speaker"), get("role")])
                .filter(|v| py_truthy(v))
                .map(py_str)
                .unwrap_or_else(|| "user".into());
            let text = match get("text") {
                None => String::new(),
                Some(Value::String(s)) => s.clone(),
                Some(other) => py_str(other),
            };
            let input = MessageInput {
                role: opt_string(get("role")).unwrap_or_default(),
                sent_at: opt_string(get("sent_at")),
                message_id: opt_string(get("message_id")),
                metadata: get("metadata").filter(|v| !v.is_null()).cloned(),
            };
            out.push(self.add_message(chat_id, &speaker, &text, input)?);
        }
        Ok(out)
    }

    pub async fn wait_until_idle(&self, timeout: Option<f64>) -> Result<bool> {
        self.worker.wait_idle(timeout).await
    }

    /// Synchronously process queued work in the current task (for scripts and tests).
    pub async fn process_pending(&self, max_batches: Option<i64>) -> Result<i64> {
        self.worker.drain(max_batches).await
    }

    // ------------------------------------------------------------------ retrieval
    pub async fn search(&self, query: &str, opts: &SearchOptions) -> Result<Vec<RetrievedMemory>> {
        self.retriever.search(query, opts).await
    }

    pub async fn context_for(
        &self,
        query: &str,
        k: Option<usize>,
        max_chars: Option<usize>,
        filters: &SearchOptions,
    ) -> Result<String> {
        let (block, results) =
            self.retriever.context_with_results(query, k, max_chars, DEFAULT_HEADER, filters).await?;
        if !block.is_empty() {
            // ledger: what would it have cost to re-read the source chats instead?
            let chats: IndexSet<String> = results.iter().map(|r| r.memory.chat_id.clone()).collect();
            let raw = if chats.is_empty() {
                0
            } else {
                let ids: Vec<String> = chats.into_iter().collect();
                py_round_int(self.store.chat_text_chars(&ids)? as f64 / 4.0).max(1)
            };
            let mut l = self.ledger.lock().unwrap_or_else(|e| e.into_inner());
            l.queries += 1;
            l.context_tokens += estimate_tokens(&block);
            l.raw_tokens_avoided += raw;
        }
        Ok(block)
    }

    pub fn profile(&self, subject: &str, limit: i64) -> Result<Value> {
        self.retriever.profile(subject, limit)
    }

    pub fn related(&self, memory_id: &str) -> Result<Vec<Value>> {
        self.retriever.related(memory_id)
    }

    pub fn timeline(
        &self,
        subject: Option<&str>,
        since: Option<&str>,
        until: Option<&str>,
        limit: i64,
    ) -> Result<Vec<Memory>> {
        self.retriever.timeline(subject, since, until, limit)
    }

    pub fn memories(&self, q: &MemoryQuery) -> Result<Vec<Memory>> {
        self.store.list_memories(q)
    }

    /// Store an explicit memory immediately (no LLM extraction), e.g. "remember that ...".
    ///
    /// Still deduplicated against existing memories of the subject (exact text, or cosine >= dedupe threshold).
    pub async fn remember(&self, text: &str, opts: &RememberOptions) -> Result<Memory> {
        let text = normalize_ws(text);
        if py_len(&text) < 3 {
            return Err(MemoryError::Value("memory text too short".into()));
        }
        let subject_name =
            self.config.extraction.speaker_names.get(&opts.subject).cloned().unwrap_or_else(|| opts.subject.clone());
        let mut sid = norm_entity(&subject_name);
        if sid.is_empty() {
            sid = "user".into();
        }
        let thash = content_hash(&[&text.to_lowercase()]);
        if let Some(existing) = self.store.find_active_by_text_hash(&thash)? {
            return Ok(existing);
        }
        let emb = self.llm().embed(&text).await.ok();
        if let Some(e) = emb.as_ref().filter(|e| !e.is_empty()) {
            let q = MemoryQuery { subject: Some(sid.clone()), limit: Some(2000), ..Default::default() };
            for m in self.store.list_memories(&q)? {
                if m.embedding.as_ref().is_some_and(|me| !me.is_empty())
                    && cos(Some(e), m.embedding.as_deref()) >= self.config.extraction.dedupe_cosine
                {
                    self.store.add_memory_sources(&m.memory_id, &[])?;
                    return Ok(m);
                }
            }
        }
        let now = now_iso();
        let (event_time, precision) = parse_when(opts.when.as_deref().unwrap_or(""), Some(&now));
        let kind = if MEMORY_KINDS.contains(&opts.kind.as_str()) { opts.kind.clone() } else { "fact".into() };
        let mut mem = Memory::new(
            new_id("mem"),
            text.clone(),
            kind,
            sid.clone(),
            subject_name.clone(),
            opts.speaker.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| subject_name.clone()),
            opts.chat_id.clone(),
            0f64.max(1f64.min(opts.importance)),
            0f64.max(1f64.min(opts.confidence)),
            now.clone(),
            now.clone(),
            now.clone(),
        );
        mem.event_time = event_time;
        mem.event_time_precision = precision;
        mem.text_hash = thash;
        mem.embedding = emb;
        mem.metadata = obj([("decision", Value::from("MANUAL")), ("source_kind", Value::from("explicit"))]);
        self.store.upsert_entity(&sid, &subject_name, "person", Some(&now), 1, &[])?;
        self.store.insert_memory(&mem, &[], std::slice::from_ref(&sid))?;
        self.store.log_event(
            "remember",
            Some(&mem.memory_id),
            Some(&obj([
                ("text", Value::String(py_prefix(&text, 120).to_string())),
                ("chat_id", Value::String(opts.chat_id.clone())),
            ])),
        )?;
        self.retriever.invalidate();
        Ok(mem)
    }

    pub fn retract(&self, memory_id: &str, reason: &str) -> Result<bool> {
        let ok = self.store.retract(memory_id, reason)?;
        self.retriever.invalidate();
        Ok(ok)
    }

    pub async fn maintain(&self) -> Result<Value> {
        self.worker.maintain().await
    }

    // ------------------------------------------------------------------ accounting & grading
    fn scalar(&self, sql: &str) -> Result<i64> {
        self.store.with_conn(|c| Ok(c.query_row(sql, [], |r| r.get(0))?))
    }

    fn scalar_text(&self, sql: &str) -> Result<Option<String>> {
        self.store.with_conn(|c| Ok(c.query_row(sql, [], |r| r.get(0))?))
    }

    /// Tokens of raw chat text the memory layer has digested vs. tokens of the memories that replace it.
    pub fn token_ledger(&self) -> Result<Value> {
        let raw_processed = self.scalar(
            "SELECT COALESCE(SUM(length(text)), 0) AS n FROM messages WHERE status IN ('processed','skipped')",
        )?;
        let raw_all = self.scalar("SELECT COALESCE(SUM(length(text)), 0) AS n FROM messages")?;
        let mem_active =
            self.scalar("SELECT COALESCE(SUM(length(text)), 0) AS n FROM memories WHERE status='active'")?;
        let raw_tokens = chars_to_tokens(raw_processed);
        let mem_tokens = chars_to_tokens(mem_active);
        let saved = (raw_tokens - mem_tokens).max(0);
        let l = self.ledger.lock().unwrap_or_else(|e| e.into_inner());
        Ok(obj([
            ("raw_tokens_total", Value::from(chars_to_tokens(raw_all))),
            ("raw_tokens_digested", Value::from(raw_tokens)),
            ("memory_tokens", Value::from(mem_tokens)),
            ("tokens_saved", Value::from(saved)),
            (
                "compression_ratio",
                if mem_tokens != 0 { fnum(py_round(raw_tokens as f64 / mem_tokens as f64, 2)) } else { Value::Null },
            ),
            ("context_queries", Value::from(l.queries)),
            ("context_tokens_served", Value::from(l.context_tokens)),
            ("context_raw_tokens_avoided", Value::from(l.raw_tokens_avoided)),
        ]))
    }

    /// Five-axis grade (0-100 each) + letter. Axes: coverage, compression, connectivity, freshness, reliability.
    pub fn grade(&self) -> Result<Value> {
        let st = self.store.stats()?;
        let ledger = self.token_ledger()?;
        let num = |v: &Value| v.as_f64().unwrap_or(0.0);
        let msgs = st["messages"].as_object().cloned().unwrap_or_default();
        let total: f64 = msgs.values().map(num).sum();
        let done = msgs.get("processed").map(num).unwrap_or(0.0) + msgs.get("skipped").map(num).unwrap_or(0.0);
        let coverage = if total != 0.0 { 100.0 * done / total } else { 100.0 };
        let raw = num(&ledger["raw_tokens_digested"]);
        let mem = num(&ledger["memory_tokens"]);
        // log scale: 1x -> 0, 2x -> 50, 4x or better -> 100 (nothing digested yet -> 100, nothing kept -> 100)
        let compression = if raw == 0.0 || mem == 0.0 {
            100.0
        } else {
            100.0 * 0f64.max(1f64.min((1e-9f64.max(raw / mem)).log2() / 2.0))
        };
        let active = st["memories"]["by_status"].get("active").map(num).unwrap_or(0.0);
        let rel = st["relations"].get("active").map(num).unwrap_or(0.0);
        let links = num(&st["links"]);
        let connectivity = if active != 0.0 { 100.0 * 1f64.min((rel + links) / (1.5 * active)) } else { 0.0 };
        // freshness: how far behind the newest ingested message the worker is
        let newest = self.scalar_text("SELECT MAX(ingested_at) AS t FROM messages")?;
        let oldest_pending =
            self.scalar_text("SELECT MIN(ingested_at) AS t FROM messages WHERE status IN ('pending','processing')")?;
        let freshness = match (newest.filter(|s| !s.is_empty()), oldest_pending.filter(|s| !s.is_empty())) {
            (Some(_), Some(op)) => {
                let now = utcnow();
                let lag = match parse_iso(Some(&op)) {
                    Some(t) => total_seconds(now.signed_duration_since(t)),
                    None => 0.0,
                };
                100.0 * (-lag / 600.0).exp() // 10-minute e-folding
            }
            _ => 100.0,
        };
        let m = self.worker.metrics();
        let attempts = m.batches + m.failures;
        let fail_rate = if attempts != 0 { m.failures as f64 / attempts as f64 } else { 0.0 };
        let dead = self.scalar("SELECT COUNT(*) AS n FROM jobs WHERE status='dead' AND kind='extract'")?;
        let reliability = 100.0 * 0f64.max(1.0 - fail_rate - 0.05 * dead as f64);
        let axes = [
            ("coverage", py_round(coverage, 1)),
            ("compression", py_round(compression, 1)),
            ("connectivity", py_round(connectivity, 1)),
            ("freshness", py_round(freshness, 1)),
            ("reliability", py_round(reliability, 1)),
        ];
        let mut sum = 0.0;
        for (_, v) in &axes {
            sum += v;
        }
        let overall = sum / 5.0;
        let letter = if overall >= 92.0 {
            "S"
        } else if overall >= 82.0 {
            "A"
        } else if overall >= 70.0 {
            "B"
        } else if overall >= 55.0 {
            "C"
        } else {
            "D"
        };
        Ok(obj([
            ("axes", obj(axes.iter().map(|(k, v)| (*k, fnum(*v))))),
            ("overall", fnum(py_round(overall, 1))),
            ("letter", Value::from(letter)),
        ]))
    }

    /// Everything the dashboard shows, in one call.
    pub fn status(&self) -> Result<Value> {
        let mut st = self.store.stats()?;
        let metrics = self.worker.metrics().as_value();
        let mut worker = serde_json::Map::new();
        worker.insert("running".into(), Value::Bool(self.worker.running()));
        worker.insert("worker_id".into(), Value::String(self.worker.worker_id.clone()));
        worker.insert("concurrency".into(), Value::from(self.config.worker.concurrency));
        if let Value::Object(m) = metrics {
            for (k, v) in m {
                worker.insert(k, v);
            }
        }
        let llm = self.llm();
        let mut llm_info = match llm.stats() {
            Some(Value::Object(m)) => m,
            _ => serde_json::Map::new(),
        };
        llm_info.insert("model".into(), Value::String(llm.model()));
        llm_info.insert("embed_model".into(), Value::String(llm.embed_model()));
        llm_info.insert("base_url".into(), Value::String(llm.base_url()));
        let recent = self.store.list_memories(&MemoryQuery {
            limit: Some(12),
            order: "created_at DESC".into(),
            ..Default::default()
        })?;
        let o = st.as_object_mut().expect("stats is an object");
        o.insert("worker".into(), Value::Object(worker));
        o.insert("llm".into(), Value::Object(llm_info));
        o.insert("tokens".into(), self.token_ledger()?);
        o.insert("grade".into(), self.grade()?);
        o.insert("recent_memories".into(), Value::Array(recent.iter().map(|m| m.to_value(false)).collect()));
        o.insert(
            "top_entities".into(),
            Value::Array(self.store.top_entities(15, None)?.iter().map(|e| e.to_value()).collect()),
        );
        o.insert("recent_events".into(), Value::Array(self.store.recent_events(15, None)?));
        Ok(st)
    }
}
