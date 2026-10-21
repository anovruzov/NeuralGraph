//! Port of `retrieval/external_retriever.py`: open-domain detection, the routing tracer, the pluggable external
//! retriever interface with its Wikipedia implementation (over `reqwest` instead of `aiohttp`), the persistent
//! on-disk cache and the retriever registry.
//!
//! Tesseract only calls [`is_open_domain_query`]; the rest was used by the research service. Behaviour notes:
//! - Every network or decoding failure of [`WikipediaRetriever::retrieve`] yields an empty list and records the
//!   error text in `last_error`, as the Python `except Exception` did (the texts are reqwest's, not aiohttp's).
//!   `aiohttp`'s `response.json()` refused non-JSON content types; so does this port.
//! - The Python retrievers were `async` methods on objects mutated through `self`; here they are `&self` async
//!   methods with interior mutability (`Cell`/`RefCell`), single-threaded like the asyncio code.
//! - Retrievers report failure as `Err(String)`, which [`ExternalRetrieverRegistry::retrieve_all`] maps to an
//!   empty list for that source, like `asyncio.gather(..., return_exceptions=True)` did.

use crate::pytime::PyDateTime;
use async_trait::async_trait;
use indexmap::IndexMap;
use md5::{Digest, Md5};
use neuralgraph_memory::pycompat::{dumps, py_prefix, py_str, py_strip, py_truthy, JsonFormat};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::cell::{Cell, OnceCell, RefCell};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::LazyLock;
use std::time::Duration;

fn md5_hex(s: &str) -> String {
    Md5::digest(s.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

/// `time.time()`.
fn unix_time() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

// =============================================================================================================
// INSTRUMENTATION & TRACING
// =============================================================================================================

/// One open-domain routing decision (`OpenDomainTrace`).
#[derive(Clone, Debug, PartialEq)]
pub struct OpenDomainTrace {
    pub query_text: String,
    pub timestamp: PyDateTime,
    pub is_open_domain: bool,
    pub open_domain_confidence: f64,
    pub detected_query_type: String,
    pub strategy_used: String,
    pub bypassed_entity_filter: bool,
    pub bypassed_temporal_filter: bool,
    pub session_candidates_count: i64,
    pub session_hits_at_threshold: i64,
    pub external_retriever_called: bool,
    pub external_results_count: i64,
    pub external_retrieval_latency_ms: f64,
    pub final_results_count: i64,
    pub external_in_top_k: i64,
}

impl OpenDomainTrace {
    /// `OpenDomainTrace(query_text)` with every other field at its default (timestamp = now, UTC).
    pub fn new(query_text: impl Into<String>) -> Self {
        OpenDomainTrace {
            query_text: query_text.into(),
            timestamp: PyDateTime::now_utc(),
            is_open_domain: false,
            open_domain_confidence: 0.0,
            detected_query_type: String::new(),
            strategy_used: "semantic".into(),
            bypassed_entity_filter: false,
            bypassed_temporal_filter: false,
            session_candidates_count: 0,
            session_hits_at_threshold: 0,
            external_retriever_called: false,
            external_results_count: 0,
            external_retrieval_latency_ms: 0.0,
            final_results_count: 0,
            external_in_top_k: 0,
        }
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "query_text": self.query_text,
            "timestamp": self.timestamp.isoformat(),
            "is_open_domain": self.is_open_domain,
            "open_domain_confidence": self.open_domain_confidence,
            "detected_query_type": self.detected_query_type,
            "strategy_used": self.strategy_used,
            "bypassed_entity_filter": self.bypassed_entity_filter,
            "bypassed_temporal_filter": self.bypassed_temporal_filter,
            "session_candidates_count": self.session_candidates_count,
            "session_hits_at_threshold": self.session_hits_at_threshold,
            "external_retriever_called": self.external_retriever_called,
            "external_results_count": self.external_results_count,
            "external_retrieval_latency_ms": self.external_retrieval_latency_ms,
            "final_results_count": self.final_results_count,
            "external_in_top_k": self.external_in_top_k,
        })
    }
}

/// Collects traces and aggregate counters (`OpenDomainTracer`).
#[derive(Clone, Debug)]
pub struct OpenDomainTracer {
    traces: Vec<OpenDomainTrace>,
    max_traces: usize,
    stats: IndexMap<&'static str, i64>,
}

impl Default for OpenDomainTracer {
    fn default() -> Self {
        Self::new(1000)
    }
}

impl OpenDomainTracer {
    pub fn new(max_traces: usize) -> Self {
        let stats = [
            "total_queries",
            "open_domain_queries",
            "zero_session_hits",
            "external_called",
            "external_improved_results",
        ]
        .into_iter()
        .map(|k| (k, 0))
        .collect();
        OpenDomainTracer { traces: Vec::new(), max_traces, stats }
    }

    pub fn record_trace(&mut self, trace: OpenDomainTrace) {
        let warn = trace.is_open_domain && trace.session_hits_at_threshold == 0;
        if warn {
            tracing::warn!(
                "Open-domain query with zero session hits: {}... (confidence={:.2})",
                py_prefix(&trace.query_text, 50),
                trace.open_domain_confidence
            );
        }
        let (open, zero_hits, called, improved) = (
            trace.is_open_domain,
            trace.session_hits_at_threshold == 0,
            trace.external_retriever_called,
            trace.external_in_top_k > 0,
        );
        self.traces.push(trace);
        if self.traces.len() > self.max_traces {
            let excess = self.traces.len() - self.max_traces;
            self.traces.drain(..excess);
        }
        self.stats["total_queries"] += 1;
        if open {
            self.stats["open_domain_queries"] += 1;
        }
        if zero_hits {
            self.stats["zero_session_hits"] += 1;
        }
        if called {
            self.stats["external_called"] += 1;
            if improved {
                self.stats["external_improved_results"] += 1;
            }
        }
    }

    /// Counters plus the derived ratios (present only when their denominator is non-zero).
    pub fn get_stats(&self) -> Map<String, Value> {
        let mut out: Map<String, Value> = self.stats.iter().map(|(k, v)| (k.to_string(), json!(v))).collect();
        let total = self.stats["total_queries"];
        let open = self.stats["open_domain_queries"];
        if total > 0 {
            out.insert("open_domain_ratio".into(), json!(open as f64 / total as f64));
            out.insert("zero_hit_ratio".into(), json!(self.stats["zero_session_hits"] as f64 / total as f64));
        }
        if open > 0 {
            out.insert("external_coverage".into(), json!(self.stats["external_called"] as f64 / open as f64));
        }
        out
    }

    /// `self._traces[-n:]` as dicts; like the Python slice, `n == 0` returns every trace.
    pub fn get_recent_traces(&self, n: usize) -> Vec<Value> {
        let start = if n == 0 { 0 } else { self.traces.len().saturating_sub(n) };
        self.traces[start..].iter().map(OpenDomainTrace::to_dict).collect()
    }
}

static TRACER: LazyLock<parking_lot::Mutex<OpenDomainTracer>> =
    LazyLock::new(|| parking_lot::Mutex::new(OpenDomainTracer::default()));

/// `get_open_domain_tracer()`: the process-wide tracer.
pub fn get_open_domain_tracer() -> parking_lot::MutexGuard<'static, OpenDomainTracer> {
    TRACER.lock()
}

// =============================================================================================================
// EXTERNAL RETRIEVER INTERFACE
// =============================================================================================================

/// One result from an external knowledge source (`ExternalResult`).
#[derive(Clone, Debug)]
pub struct ExternalResult {
    pub content: String,
    pub source: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub relevance_score: f64,
    pub confidence: f64,
    pub embedding: Option<Vec<f64>>,
    pub metadata: Map<String, Value>,
    pub retrieved_at: PyDateTime,
    /// `_cache_key`: computed from the content on first use and then kept, as the Python property did.
    cache_key: OnceCell<String>,
}

impl ExternalResult {
    pub fn new(content: impl Into<String>, source: impl Into<String>) -> Self {
        ExternalResult {
            content: content.into(),
            source: source.into(),
            source_url: None,
            title: None,
            relevance_score: 0.0,
            confidence: 1.0,
            embedding: None,
            metadata: Map::new(),
            retrieved_at: PyDateTime::now_utc(),
            cache_key: OnceCell::new(),
        }
    }

    /// `"<source>:<first 16 hex digits of md5(content)>"`.
    pub fn cache_key(&self) -> String {
        self.cache_key.get_or_init(|| format!("{}:{}", self.source, &md5_hex(&self.content)[..16])).clone()
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "content": self.content,
            "source": self.source,
            "source_url": self.source_url,
            "title": self.title,
            "relevance_score": self.relevance_score,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "retrieved_at": self.retrieved_at.isoformat(),
            "cache_key": self.cache_key(),
        })
    }

    /// `ExternalResult.from_dict`; `Err` where Python raised (missing `content`/`source`, bad `retrieved_at`).
    pub fn from_dict(data: &Map<String, Value>) -> Result<Self, String> {
        let req = |k: &str| match data.get(k) {
            Some(Value::String(s)) => Ok(s.clone()),
            Some(v) => Ok(py_str(v)),
            None => Err(format!("KeyError: '{k}'")),
        };
        let retrieved_at = match data.get("retrieved_at") {
            Some(Value::String(s)) => {
                PyDateTime::fromisoformat(s).ok_or_else(|| format!("ValueError: Invalid isoformat string: '{s}'"))?
            }
            None | Some(Value::Null) => PyDateTime::now_utc(),
            Some(v) => return Err(format!("AttributeError: retrieved_at {v} has no isoformat")),
        };
        let opt_str = |k: &str| data.get(k).and_then(Value::as_str).map(str::to_string);
        Ok(ExternalResult {
            content: req("content")?,
            source: req("source")?,
            source_url: opt_str("source_url"),
            title: opt_str("title"),
            relevance_score: data.get("relevance_score").and_then(Value::as_f64).unwrap_or(0.0),
            confidence: data.get("confidence").and_then(Value::as_f64).unwrap_or(1.0),
            embedding: None,
            metadata: data.get("metadata").and_then(Value::as_object).cloned().unwrap_or_default(),
            retrieved_at,
            cache_key: OnceCell::new(),
        })
    }
}

/// A pluggable external knowledge source (`ExternalRetriever`).
#[async_trait(?Send)]
pub trait ExternalRetriever {
    /// Name of this knowledge source.
    fn source_name(&self) -> String;

    /// Results for `query`, best first. `Err` is an exception the registry turns into an empty result.
    async fn retrieve(
        &self,
        query: &str,
        query_embedding: Option<&[f64]>,
        limit: usize,
        timeout_ms: f64,
    ) -> Result<Vec<ExternalResult>, String>;

    /// Whether the source is currently reachable.
    async fn is_available(&self) -> bool;

    /// Whether the source can use embeddings for search or reranking.
    fn supports_embedding_search(&self) -> bool {
        false
    }
}

// =============================================================================================================
// WIKIPEDIA RETRIEVER
// =============================================================================================================

/// `WikipediaConfig`.
#[derive(Clone, Debug, PartialEq)]
pub struct WikipediaConfig {
    pub api_url: String,
    pub search_limit: i64,
    /// Characters to extract per article.
    pub extract_chars: i64,
    pub timeout_seconds: f64,
    /// How long cached results stay valid.
    pub cache_ttl_hours: f64,
    pub user_agent: String,
}

impl Default for WikipediaConfig {
    fn default() -> Self {
        WikipediaConfig {
            api_url: "https://en.wikipedia.org/w/api.php".into(),
            search_limit: 5,
            extract_chars: 1000,
            timeout_seconds: 2.0,
            cache_ttl_hours: 24.0,
            user_agent: "NeuralGraph/1.0 (Memory System; contact@example.com)".into(),
        }
    }
}

/// Failure inside one Wikipedia call: a timeout or anything else (message).
enum WikiError {
    Timeout,
    Other(String),
}

impl From<reqwest::Error> for WikiError {
    fn from(e: reqwest::Error) -> Self {
        if e.is_timeout() {
            WikiError::Timeout
        } else {
            WikiError::Other(e.to_string())
        }
    }
}

/// `d.get(key, default)` on a JSON value that must be a dict (`AttributeError` otherwise).
fn dict_get<'a>(v: &'a Value, key: &str) -> Result<Option<&'a Value>, WikiError> {
    match v {
        Value::Object(m) => Ok(m.get(key)),
        other => Err(WikiError::Other(format!("'{}' object has no attribute 'get'", py_type(other)))),
    }
}

fn py_type(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Wikipedia search + intro extracts, with an in-memory TTL cache (`WikipediaRetriever`).
pub struct WikipediaRetriever {
    config: WikipediaConfig,
    cache: RefCell<HashMap<String, (Vec<ExternalResult>, f64)>>,
    available: Cell<bool>,
    last_error: RefCell<Option<String>>,
}

impl WikipediaRetriever {
    pub fn new(config: Option<WikipediaConfig>) -> Self {
        WikipediaRetriever {
            config: config.unwrap_or_default(),
            cache: RefCell::new(HashMap::new()),
            available: Cell::new(true),
            last_error: RefCell::new(None),
        }
    }

    pub fn config(&self) -> &WikipediaConfig {
        &self.config
    }

    /// The last error text (`"timeout"` for timeouts), if any call failed.
    pub fn last_error(&self) -> Option<String> {
        self.last_error.borrow().clone()
    }

    /// `_make_cache_key`: md5 of the lower-cased, stripped query.
    pub fn make_cache_key(&self, query: &str) -> String {
        md5_hex(py_strip(&query.to_lowercase()))
    }

    /// `_get_cached`: cached results unless older than the TTL (expired entries are dropped).
    fn get_cached(&self, cache_key: &str) -> Option<Vec<ExternalResult>> {
        let mut cache = self.cache.borrow_mut();
        let (results, timestamp) = cache.get(cache_key)?;
        let age_hours = (unix_time() - timestamp) / 3600.0;
        if age_hours > self.config.cache_ttl_hours {
            cache.remove(cache_key);
            return None;
        }
        Some(results.clone())
    }

    async fn get_json(&self, client: &reqwest::Client, params: &[(&str, String)]) -> Result<Value, WikiError> {
        let url = reqwest::Url::parse_with_params(&self.config.api_url, params.iter().map(|(k, v)| (*k, v.as_str())))
            .map_err(|e| WikiError::Other(e.to_string()))?;
        let response =
            client.get(url).header("User-Agent", &self.config.user_agent).send().await?.error_for_status()?;
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let mime = content_type.split(';').next().unwrap_or("").trim().to_lowercase();
        if mime != "application/json" {
            return Err(WikiError::Other(format!("Attempt to decode JSON with unexpected mimetype: {content_type}")));
        }
        let text = response.text().await?;
        serde_json::from_str(&text).map_err(|e| WikiError::Other(e.to_string()))
    }

    /// `_search_and_extract`: search, then fetch the intro extracts of the top `limit` hits.
    async fn search_and_extract(
        &self,
        query: &str,
        limit: usize,
        timeout_ms: f64,
    ) -> Result<Vec<ExternalResult>, WikiError> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs_f64((timeout_ms / 1000.0).max(0.0)))
            .build()
            .map_err(|e| WikiError::Other(e.to_string()))?;
        let search_params = [
            ("action", "query".to_string()),
            ("list", "search".to_string()),
            ("srsearch", query.to_string()),
            ("srlimit", (limit * 2).to_string()),
            ("format", "json".to_string()),
        ];
        let search_data = self.get_json(&client, &search_params).await?;
        let empty_obj = json!({});
        let empty_list = json!([]);
        let q = dict_get(&search_data, "query")?.unwrap_or(&empty_obj);
        let search_results = dict_get(q, "search")?.unwrap_or(&empty_list);
        if !py_truthy(search_results) {
            return Ok(Vec::new());
        }
        let Value::Array(search_results) = search_results else {
            return Err(WikiError::Other("search results are not a list".into()));
        };
        let top = &search_results[..limit.min(search_results.len())];
        let mut page_ids = Vec::new();
        for r in top {
            match dict_get(r, "pageid")? {
                Some(id) => page_ids.push(py_str(id)),
                None => return Err(WikiError::Other("'pageid'".into())),
            }
        }
        let extract_params = [
            ("action", "query".to_string()),
            ("pageids", page_ids.join("|")),
            ("prop", "extracts|info".to_string()),
            ("exintro", "true".to_string()),
            ("explaintext", "true".to_string()),
            ("excharacters", self.config.extract_chars.to_string()),
            ("inprop", "url".to_string()),
            ("format", "json".to_string()),
        ];
        let extract_data = self.get_json(&client, &extract_params).await?;
        let q = dict_get(&extract_data, "query")?.unwrap_or(&empty_obj);
        let pages = dict_get(q, "pages")?.unwrap_or(&empty_obj);

        let mut results = Vec::new();
        for (i, (search_result, page_id)) in top.iter().zip(&page_ids).enumerate() {
            let page_data = dict_get(pages, page_id)?.unwrap_or(&empty_obj);
            let extract = match dict_get(page_data, "extract")? {
                None => continue,
                Some(v) if !py_truthy(v) => continue,
                Some(Value::String(s)) => s.clone(),
                Some(v) => py_str(v),
            };
            let mut result = ExternalResult::new(extract, "wikipedia");
            result.source_url = dict_get(page_data, "fullurl")?.and_then(Value::as_str).map(str::to_string);
            result.title = dict_get(search_result, "title")?.and_then(Value::as_str).map(str::to_string);
            result.relevance_score = 1.0 - (i as f64 * 0.1);
            result.confidence = 0.9;
            let snippet = dict_get(search_result, "snippet")?.cloned().unwrap_or(json!(""));
            let word_count = dict_get(search_result, "wordcount")?.cloned().unwrap_or(json!(0));
            result.metadata = json!({"page_id": page_id, "snippet": snippet, "word_count": word_count})
                .as_object()
                .cloned()
                .unwrap_or_default();
            results.push(result);
        }
        Ok(results)
    }
}

#[async_trait(?Send)]
impl ExternalRetriever for WikipediaRetriever {
    fn source_name(&self) -> String {
        "wikipedia".into()
    }

    async fn retrieve(
        &self,
        query: &str,
        _query_embedding: Option<&[f64]>,
        limit: usize,
        timeout_ms: f64,
    ) -> Result<Vec<ExternalResult>, String> {
        let cache_key = self.make_cache_key(query);
        if let Some(cached) = self.get_cached(&cache_key) {
            tracing::debug!("Wikipedia cache hit for: {}...", py_prefix(query, 30));
            return Ok(cached.into_iter().take(limit).collect());
        }
        let start = std::time::Instant::now();
        match self.search_and_extract(query, limit, timeout_ms).await {
            Ok(results) => {
                let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
                tracing::info!(
                    "Wikipedia retrieval: {} results for '{}...' in {:.1}ms",
                    results.len(),
                    py_prefix(query, 30),
                    elapsed_ms
                );
                self.cache.borrow_mut().insert(cache_key, (results.clone(), unix_time()));
                self.available.set(true);
                Ok(results)
            }
            Err(WikiError::Timeout) => {
                tracing::warn!("Wikipedia timeout for query: {}...", py_prefix(query, 30));
                *self.last_error.borrow_mut() = Some("timeout".into());
                Ok(Vec::new())
            }
            Err(WikiError::Other(e)) => {
                tracing::error!("Wikipedia retrieval error: {e}");
                *self.last_error.borrow_mut() = Some(e);
                Ok(Vec::new())
            }
        }
    }

    async fn is_available(&self) -> bool {
        if !self.available.get() {
            return false;
        }
        let client = match reqwest::Client::builder().timeout(Duration::from_secs(1)).build() {
            Ok(c) => c,
            Err(_) => {
                self.available.set(false);
                return false;
            }
        };
        let params = [("action", "query"), ("meta", "siteinfo"), ("format", "json")];
        let Ok(url) = reqwest::Url::parse_with_params(&self.config.api_url, params) else {
            self.available.set(false);
            return false;
        };
        let response = client.get(url).header("User-Agent", &self.config.user_agent).send().await;
        let ok = matches!(response, Ok(r) if r.status().as_u16() == 200);
        self.available.set(ok);
        ok
    }
}

// =============================================================================================================
// PERSISTENT CACHE
// =============================================================================================================

/// `json.dump(obj, f, indent=2)`.
fn indent2() -> JsonFormat {
    JsonFormat { ensure_ascii: true, sort_keys: false, indent: Some(2), item_sep: ",", key_sep: ": " }
}

/// Long-lived on-disk cache of external results (`PersistentExternalCache`): one JSON file per query
/// (`<md5(query.lower())>.json`) and an `index.json`.
pub struct PersistentExternalCache {
    cache_dir: PathBuf,
    index_file: PathBuf,
    index: Map<String, Value>,
}

impl PersistentExternalCache {
    /// Open (creating) the cache directory; the default is `~/.neuralgraph/external_cache`.
    pub fn new(cache_dir: Option<&Path>) -> std::io::Result<Self> {
        let cache_dir = match cache_dir {
            Some(d) => d.to_path_buf(),
            None => {
                let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
                home.join(".neuralgraph").join("external_cache")
            }
        };
        std::fs::create_dir_all(&cache_dir)?;
        let index_file = cache_dir.join("index.json");
        let mut cache = PersistentExternalCache { cache_dir, index_file, index: Map::new() };
        cache.index = cache.load_index();
        Ok(cache)
    }

    pub fn cache_dir(&self) -> &Path {
        &self.cache_dir
    }

    /// `_load_index`. An index that is not a JSON object is treated like an unreadable one (Python kept it and
    /// failed on first use instead).
    fn load_index(&self) -> Map<String, Value> {
        if !self.index_file.exists() {
            return Map::new();
        }
        match std::fs::read_to_string(&self.index_file).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
            Some(Value::Object(m)) => m,
            _ => {
                tracing::warn!("Failed to load external cache index: {}", self.index_file.display());
                Map::new()
            }
        }
    }

    fn save_index(&self) {
        if let Err(e) = std::fs::write(&self.index_file, dumps(&Value::Object(self.index.clone()), &indent2())) {
            tracing::warn!("Failed to save external cache index: {e}");
        }
    }

    fn key(query: &str) -> String {
        md5_hex(&query.to_lowercase())
    }

    /// Cached results for `query` (case-insensitive), `None` when absent or unreadable.
    pub fn get(&mut self, query: &str) -> Option<Vec<ExternalResult>> {
        let cache_key = Self::key(query);
        if !self.index.contains_key(&cache_key) {
            return None;
        }
        let cache_file = self.cache_dir.join(format!("{cache_key}.json"));
        if !cache_file.exists() {
            self.index.remove(&cache_key);
            return None;
        }
        let loaded = (|| -> Result<Vec<ExternalResult>, String> {
            let text = std::fs::read_to_string(&cache_file).map_err(|e| e.to_string())?;
            let data: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
            let results = data.get("results").and_then(Value::as_array).ok_or("KeyError: 'results'")?;
            results.iter().map(|r| ExternalResult::from_dict(r.as_object().ok_or("result is not a dict")?)).collect()
        })();
        match loaded {
            Ok(r) => Some(r),
            Err(e) => {
                tracing::warn!("Failed to load cached results: {e}");
                None
            }
        }
    }

    /// Store `results` for `query` and update the index.
    pub fn put(&mut self, query: &str, results: &[ExternalResult]) {
        let cache_key = Self::key(query);
        let cache_file = self.cache_dir.join(format!("{cache_key}.json"));
        let cached_at = PyDateTime::now_utc().isoformat();
        let data = json!({
            "query": query,
            "cached_at": cached_at,
            "results": results.iter().map(ExternalResult::to_dict).collect::<Vec<_>>(),
        });
        if let Err(e) = std::fs::write(&cache_file, dumps(&data, &indent2())) {
            tracing::warn!("Failed to cache external results: {e}");
            return;
        }
        self.index.insert(cache_key, json!({"query": query, "cached_at": cached_at, "result_count": results.len()}));
        self.save_index();
    }

    /// Delete every cached file; returns how many index entries there were.
    pub fn clear(&mut self) -> usize {
        let count = self.index.len();
        for cache_key in self.index.keys() {
            let _ = std::fs::remove_file(self.cache_dir.join(format!("{cache_key}.json")));
        }
        self.index = Map::new();
        self.save_index();
        count
    }
}

// =============================================================================================================
// REGISTRY
// =============================================================================================================

/// Named external retrievers with per-source enable flags (`ExternalRetrieverRegistry`).
#[derive(Default)]
pub struct ExternalRetrieverRegistry {
    retrievers: IndexMap<String, Rc<dyn ExternalRetriever>>,
    enabled: IndexMap<String, bool>,
}

impl ExternalRetrieverRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, retriever: Rc<dyn ExternalRetriever>, enabled: bool) {
        let name = retriever.source_name();
        tracing::info!("Registered external retriever: {name}");
        self.retrievers.insert(name.clone(), retriever);
        self.enabled.insert(name, enabled);
    }

    pub fn unregister(&mut self, source_name: &str) -> bool {
        if self.retrievers.shift_remove(source_name).is_some() {
            self.enabled.shift_remove(source_name);
            return true;
        }
        false
    }

    pub fn set_enabled(&mut self, source_name: &str, enabled: bool) {
        if let Some(e) = self.enabled.get_mut(source_name) {
            *e = enabled;
        }
    }

    pub fn get_enabled_retrievers(&self) -> Vec<Rc<dyn ExternalRetriever>> {
        self.retrievers
            .iter()
            .filter(|(name, _)| self.enabled.get(*name).copied().unwrap_or(false))
            .map(|(_, r)| r.clone())
            .collect()
    }

    /// Query every enabled source concurrently; a failing source contributes an empty list.
    pub async fn retrieve_all(
        &self,
        query: &str,
        query_embedding: Option<&[f64]>,
        limit_per_source: usize,
        timeout_ms: f64,
    ) -> IndexMap<String, Vec<ExternalResult>> {
        let enabled = self.get_enabled_retrievers();
        let mut output = IndexMap::new();
        if enabled.is_empty() {
            return output;
        }
        let results = futures::future::join_all(
            enabled.iter().map(|r| r.retrieve(query, query_embedding, limit_per_source, timeout_ms)),
        )
        .await;
        for (retriever, result) in enabled.iter().zip(results) {
            let name = retriever.source_name();
            match result {
                Ok(r) => {
                    output.insert(name, r);
                }
                Err(e) => {
                    tracing::warn!("External retriever {name} failed: {e}");
                    output.insert(name, Vec::new());
                }
            }
        }
        output
    }
}

/// `create_wikipedia_retriever(config)`.
pub fn create_wikipedia_retriever(config: Option<WikipediaConfig>) -> WikipediaRetriever {
    WikipediaRetriever::new(config)
}

/// `create_default_registry()`: Wikipedia, enabled.
pub fn create_default_registry() -> ExternalRetrieverRegistry {
    let mut registry = ExternalRetrieverRegistry::new();
    registry.register(Rc::new(WikipediaRetriever::new(None)), true);
    registry
}

// =============================================================================================================
// OPEN-DOMAIN DETECTION
// =============================================================================================================

struct OpenDomainPatterns {
    definitional: Vec<Regex>,
    factual: Vec<Regex>,
    conversational: Vec<Regex>,
}

static OPEN_DOMAIN: LazyLock<OpenDomainPatterns> = LazyLock::new(|| {
    let compile = |ps: &[&str]| ps.iter().map(|p| Regex::new(p).expect("valid regex")).collect::<Vec<_>>();
    OpenDomainPatterns {
        definitional: compile(&[
            r"^what is (?:a |an |the )?",
            r"^what are ",
            r"^who is ",
            r"^who was ",
            r"^define ",
            r"^explain ",
            r"^how does .* work",
            r"^why does ",
            r"^why do ",
            r"^what causes ",
            r"^what makes ",
        ]),
        factual: compile(&[
            r"^how many .* are there",
            r"^how much ",
            r"^when was .* invented",
            r"^when was .* discovered",
            r"^where is ",
            r"^what country ",
            r"^what year ",
            r"capital of",
            r"president of",
            r"population of",
            r"largest ",
            r"smallest ",
            r"oldest ",
            r"youngest ",
        ]),
        conversational: compile(&[
            r"\bshe\b",
            r"\bhe\b",
            r"\bthey\b",
            r"\bwe\b",
            r"\byesterday\b",
            r"\blast week\b",
            r"\btoday\b",
            r"\bour\b",
            r"\bmy\b",
            r"\byour\b",
            r"\bthe meeting\b",
            r"\bthe conversation\b",
        ]),
    }
});

/// Scientific terms of `is_open_domain_query` (substring tests).
pub const OPEN_DOMAIN_SCIENTIFIC_TERMS: [&str; 32] = [
    "photosynthesis",
    "evolution",
    "gravity",
    "atom",
    "molecule",
    "electron",
    "proton",
    "neutron",
    "cell",
    "dna",
    "rna",
    "gene",
    "chromosome",
    "protein",
    "enzyme",
    "virus",
    "bacteria",
    "planet",
    "star",
    "galaxy",
    "solar",
    "orbit",
    "mass",
    "energy",
    "force",
    "momentum",
    "velocity",
    "acceleration",
    "temperature",
    "pressure",
    "volume",
    "density",
];

/// `is_open_domain_query`: `(is_open_domain, confidence)` from definitional/factual patterns, scientific terms
/// and the absence of conversational markers.
pub fn is_open_domain_query(query: &str) -> (bool, f64) {
    let lowered = query.to_lowercase();
    let q = py_strip(&lowered);
    let p = &*OPEN_DOMAIN;
    let mut confidence: f64 = 0.0;
    if p.definitional.iter().any(|r| r.is_match(q)) {
        confidence = confidence.max(0.8);
    }
    if p.factual.iter().any(|r| r.is_match(q)) {
        confidence = confidence.max(0.7);
    }
    if OPEN_DOMAIN_SCIENTIFIC_TERMS.iter().any(|t| q.contains(t)) {
        confidence = confidence.max(0.6);
    }
    let has_conversational_marker = p.conversational.iter().any(|r| r.is_match(q));
    if !has_conversational_marker && confidence < 0.5 {
        confidence = confidence.max(0.4);
    }
    (confidence >= 0.5, confidence)
}
