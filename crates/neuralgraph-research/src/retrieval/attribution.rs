//! Attribution logging for the retrieval pipeline (port of `retrieval/attribution.py`).
//!
//! A [`RetrievalAttribution`] records one query's journey (per-stage candidate counts, scores and latencies,
//! router decision, rerank deltas, evidence snippets, where the gold answer ranked); an [`AttributionLogger`]
//! aggregates many of them (precision@k per category, latency percentiles per stage, strategy effectiveness,
//! reranker gain) and appends their summaries to a JSONL file in batches.
//!
//! Python details kept:
//! - The logger holds the attribution *objects* it was given, so later changes to an attribution show up in the
//!   aggregates: [`AttributionRef`] (`Rc<RefCell<_>>`) keeps that sharing.
//! - Timing uses `time.perf_counter()`; [`perf_counter`] can be pinned per thread ([`pin_perf_counter`]) so
//!   latencies are reproducible in tests, the way the fixture generator replaced `time.perf_counter`.
//! - `get_rerank_gain` returns `{"rerank_gain": 0.0, "samples": 0}` without reranked queries but
//!   `{"rerank_gain_pct": ..., "samples": n}` otherwise (two different keys, as in Python).
//! - The global logger was a module global; Rust's is per thread (the engine types are `!Send`).
//! - Nothing in the Python called this module (the service only had config flags for it); it is ported whole.

use crate::pytime::PyDateTime;
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{dumps, py_len, py_prefix, py_round, py_split_whitespace, py_strip, JsonFormat};
use serde_json::{json, Map, Value};
use std::cell::{Cell, RefCell};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::OnceLock;
use std::time::Instant;

use super::data_types::NodeRef;

thread_local! {
    /// `(next value, step)` while pinned.
    static PERF_COUNTER_PIN: Cell<Option<(f64, f64)>> = const { Cell::new(None) };
}

/// Make [`perf_counter`] return `start`, `start + step`, `start + 2*step`, ... on this thread (`None`: real
/// clock again). The fixture generator replaces `time.perf_counter` with the same counter.
pub fn pin_perf_counter(start_step: Option<(f64, f64)>) {
    PERF_COUNTER_PIN.with(|c| c.set(start_step));
}

/// `time.perf_counter()`: seconds on a monotonic clock (pinnable for tests).
pub fn perf_counter() -> f64 {
    if let Some((next, step)) = PERF_COUNTER_PIN.with(Cell::get) {
        PERF_COUNTER_PIN.with(|c| c.set(Some((next + step, step))));
        return next;
    }
    static EPOCH: OnceLock<Instant> = OnceLock::new();
    EPOCH.get_or_init(Instant::now).elapsed().as_secs_f64()
}

/// Detailed score breakdown for a single candidate.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ScoreBreakdown {
    pub node_id: String,
    /// First 100 characters of the content (the caller truncates).
    pub content_preview: String,
    pub speaker: String,
    pub rank: i64,
    pub base_score: f64,
    pub semantic_score: f64,
    pub entity_score: f64,
    pub keyword_score: f64,
    pub temporal_score: f64,
    pub speaker_boost: f64,
    pub specificity_bonus: f64,
    pub rerank_delta: f64,
    pub final_score: f64,
}

impl ScoreBreakdown {
    /// `ScoreBreakdown(node_id, content_preview, speaker, rank)` with every score 0.0.
    pub fn new(
        node_id: impl Into<String>,
        content_preview: impl Into<String>,
        speaker: impl Into<String>,
        rank: i64,
    ) -> Self {
        ScoreBreakdown {
            node_id: node_id.into(),
            content_preview: content_preview.into(),
            speaker: speaker.into(),
            rank,
            ..Default::default()
        }
    }

    pub fn to_dict(&self) -> Value {
        json!({
            "node_id": py_prefix(&self.node_id, 12),
            "content": self.content_preview,
            "speaker": self.speaker,
            "rank": self.rank,
            "scores": {
                "base": py_round(self.base_score, 4),
                "semantic": py_round(self.semantic_score, 4),
                "entity": py_round(self.entity_score, 4),
                "keyword": py_round(self.keyword_score, 4),
                "temporal": py_round(self.temporal_score, 4),
                "speaker_boost": py_round(self.speaker_boost, 4),
                "specificity": py_round(self.specificity_bonus, 4),
                "rerank_delta": py_round(self.rerank_delta, 4),
                "final": py_round(self.final_score, 4),
            }
        })
    }
}

/// An evidence snippet used in answer generation.
#[derive(Clone, Debug, PartialEq)]
pub struct EvidenceSnippet {
    pub node_id: String,
    /// `metadata.get("producer_id", metadata.get("speaker", ""))`: whatever JSON value was stored there.
    pub speaker: Value,
    pub content: String,
    pub score: f64,
    pub used_in_answer: bool,
    pub quoted_text: String,
}

impl EvidenceSnippet {
    pub fn to_dict(&self) -> Value {
        json!({
            "node_id": py_prefix(&self.node_id, 12),
            "speaker": self.speaker,
            "content": py_prefix(&self.content, 200),
            "score": py_round(self.score, 4),
            "used": self.used_in_answer,
            "quoted": if self.quoted_text.is_empty() { "" } else { py_prefix(&self.quoted_text, 100) },
        })
    }
}

/// Metrics for a single retrieval stage.
#[derive(Clone, Debug)]
pub struct StageMetrics {
    pub stage_name: String,
    pub candidates_in: i64,
    pub candidates_out: i64,
    pub top_score: f64,
    pub latency_ms: f64,
    pub metadata: Map<String, Value>,
    pub timestamp: PyDateTime,
    /// Detailed score breakdowns for the top 50 candidates.
    pub top_50_breakdowns: Vec<ScoreBreakdown>,
}

/// Attribution across the stages of one retrieval.
#[derive(Clone, Debug)]
pub struct RetrievalAttribution {
    pub session_key: String,
    pub query_text: String,
    pub query_type: String,
    pub stages: Vec<StageMetrics>,
    /// `time.perf_counter()` at creation.
    pub start_time: f64,
    pub end_time: Option<f64>,
    pub final_candidates: i64,
    pub gold_in_top_1: bool,
    pub gold_in_top_5: bool,
    pub gold_in_top_10: bool,
    /// -1 when the gold answer was not found.
    pub gold_rank: i64,
    pub answer_correct: bool,
    pub router_strategy: String,
    pub router_confidence: f64,
    pub entities_detected: Vec<String>,
    pub temporal_markers: Vec<String>,
    pub evidence_snippets: Vec<EvidenceSnippet>,
    pub pre_rerank_order: Vec<String>,
    pub post_rerank_order: Vec<String>,
    /// Truncated node id -> score change (only changes above 0.01).
    pub rerank_deltas: IndexMap<String, f64>,
    pub generated_answer: String,
    pub gold_answer: String,
    pub gate_profile: String,
    pub is_list_question: bool,
}

/// A shared attribution (the logger keeps references, like the Python list of objects).
pub type AttributionRef = Rc<RefCell<RetrievalAttribution>>;

impl RetrievalAttribution {
    /// `RetrievalAttribution(session_key, query_text)` with every other field at its default.
    pub fn new(session_key: impl Into<String>, query_text: impl Into<String>) -> Self {
        Self::with_start_time(session_key, query_text, perf_counter())
    }

    /// `RetrievalAttribution(session_key, query_text, start_time=...)`: an explicit start (no clock read).
    pub fn with_start_time(session_key: impl Into<String>, query_text: impl Into<String>, start_time: f64) -> Self {
        RetrievalAttribution {
            session_key: session_key.into(),
            query_text: query_text.into(),
            query_type: "unknown".into(),
            stages: Vec::new(),
            start_time,
            end_time: None,
            final_candidates: 0,
            gold_in_top_1: false,
            gold_in_top_5: false,
            gold_in_top_10: false,
            gold_rank: -1,
            answer_correct: false,
            router_strategy: "semantic".into(),
            router_confidence: 0.5,
            entities_detected: Vec::new(),
            temporal_markers: Vec::new(),
            evidence_snippets: Vec::new(),
            pre_rerank_order: Vec::new(),
            post_rerank_order: Vec::new(),
            rerank_deltas: IndexMap::new(),
            generated_answer: String::new(),
            gold_answer: String::new(),
            gate_profile: "default".into(),
            is_list_question: false,
        }
    }

    /// Wrap into a shared reference for [`AttributionLogger::add`].
    pub fn into_ref(self) -> AttributionRef {
        Rc::new(RefCell::new(self))
    }

    /// Record a stage; `breakdowns` keeps its first 50 entries, `metadata` is Python's `**metadata`.
    #[allow(clippy::too_many_arguments)]
    pub fn log_stage(
        &mut self,
        stage_name: &str,
        candidates_in: i64,
        candidates_out: i64,
        top_score: f64,
        latency_ms: f64,
        breakdowns: Option<&[ScoreBreakdown]>,
        metadata: Map<String, Value>,
    ) {
        let top_50 = breakdowns.map(|b| b.iter().take(50).cloned().collect()).unwrap_or_default();
        self.stages.push(StageMetrics {
            stage_name: stage_name.to_string(),
            candidates_in,
            candidates_out,
            top_score,
            latency_ms,
            metadata,
            timestamp: PyDateTime::now_utc(),
            top_50_breakdowns: top_50,
        });
        tracing::debug!(
            "[Attribution] {stage_name}: {candidates_in} -> {candidates_out} (top={top_score:.3}, {latency_ms:.1}ms)"
        );
    }

    /// Record up to 20 context nodes; a node counts as "used" when three consecutive words of its first 15
    /// (lower-cased) appear in the answer.
    pub fn log_evidence(&mut self, nodes: &[(NodeRef, f64)], answer: &str) {
        let answer_lower = answer.to_lowercase();
        for (node, score) in nodes.iter().take(20) {
            let n = node.borrow();
            let speaker = if n.metadata.is_empty() {
                json!("")
            } else {
                match n.metadata.get("producer_id") {
                    Some(v) => v.clone(),
                    None => n.metadata.get("speaker").cloned().unwrap_or(json!("")),
                }
            };
            let content_lower = n.content.to_lowercase();
            let content_words: Vec<&str> = py_split_whitespace(&content_lower).into_iter().take(15).collect();
            let mut used = false;
            let mut quoted = String::new();
            for window in content_words.windows(3) {
                let phrase = window.join(" ");
                if answer_lower.contains(&phrase) {
                    used = true;
                    quoted = phrase;
                    break;
                }
            }
            self.evidence_snippets.push(EvidenceSnippet {
                node_id: n.node_id.clone(),
                speaker,
                content: py_prefix(&n.content, 200).to_string(),
                score: *score,
                used_in_answer: used,
                quoted_text: quoted,
            });
        }
    }

    /// Record the order before and after reranking (first 50 of each) and every score change above 0.01.
    pub fn log_rerank(&mut self, before: &[(NodeRef, f64)], after: &[(NodeRef, f64)]) {
        self.pre_rerank_order = before.iter().take(50).map(|(n, _)| n.borrow().node_id.clone()).collect();
        self.post_rerank_order = after.iter().take(50).map(|(n, _)| n.borrow().node_id.clone()).collect();
        let mut before_scores: IndexMap<String, f64> = IndexMap::new();
        for (n, s) in before {
            before_scores.insert(n.borrow().node_id.clone(), *s);
        }
        for (node, after_score) in after.iter().take(50) {
            let id = node.borrow().node_id.clone();
            let before_score = before_scores.get(&id).copied().unwrap_or(0.0);
            let delta = after_score - before_score;
            if delta.abs() > 0.01 {
                self.rerank_deltas.insert(py_prefix(&id, 12).to_string(), py_round(delta, 4));
            }
        }
    }

    /// Record the query router's decision.
    pub fn log_router(
        &mut self,
        strategy: &str,
        confidence: f64,
        entities: Vec<String>,
        temporal_markers: Vec<String>,
        query_type: &str,
    ) {
        self.router_strategy = strategy.to_string();
        self.router_confidence = confidence;
        self.entities_detected = entities;
        self.temporal_markers = temporal_markers;
        self.query_type = query_type.to_string();
        tracing::debug!(
            "[Attribution] Router: strategy={strategy}, type={query_type}, confidence={confidence:.2}, entities={:?}",
            self.entities_detected
        );
    }

    /// Find the first of the top `top_k` texts containing the gold answer (or one of its comma/"and"
    /// separated parts longer than two characters) and record its rank (Python default `top_k` 10).
    pub fn log_gold_position(&mut self, gold_answer: &str, retrieved_texts: &[String], top_k: usize) {
        if gold_answer.is_empty() {
            return;
        }
        let gold_lower = py_strip(&gold_answer.to_lowercase()).to_string();
        let replaced = gold_lower.replace(',', "|").replace(" and ", "|");
        let gold_parts: Vec<String> =
            replaced.split('|').map(|p| py_strip(p).to_string()).filter(|p| py_len(p) > 2).collect();
        for (i, text) in retrieved_texts.iter().take(top_k).enumerate() {
            let rank = i as i64 + 1;
            let text_lower = text.to_lowercase();
            let found = text_lower.contains(&gold_lower) || gold_parts.iter().any(|p| text_lower.contains(p.as_str()));
            if found {
                self.gold_rank = rank;
                self.gold_in_top_1 = rank == 1;
                self.gold_in_top_5 = rank <= 5;
                self.gold_in_top_10 = rank <= 10;
                break;
            }
        }
    }

    /// Stop the clock and record the outcome.
    pub fn finalize(&mut self, final_candidates: i64, answer_correct: bool) {
        self.end_time = Some(perf_counter());
        self.final_candidates = final_candidates;
        self.answer_correct = answer_correct;
    }

    /// Total latency in ms (up to now while not finalized).
    pub fn total_latency_ms(&self) -> f64 {
        match self.end_time {
            None => (perf_counter() - self.start_time) * 1000.0,
            Some(end) => (end - self.start_time) * 1000.0,
        }
    }

    /// Stage name -> latency (a repeated name keeps its first position and its last latency).
    pub fn stage_latencies(&self) -> IndexMap<String, f64> {
        let mut out = IndexMap::new();
        for s in &self.stages {
            out.insert(s.stage_name.clone(), s.latency_ms);
        }
        out
    }

    /// The complete attribution record.
    pub fn get_summary(&self) -> Value {
        let stages: Vec<Value> = self
            .stages
            .iter()
            .map(|s| {
                json!({
                    "name": s.stage_name,
                    "candidates_in": s.candidates_in,
                    "candidates_out": s.candidates_out,
                    "top_score": s.top_score,
                    "latency_ms": s.latency_ms,
                    "metadata": s.metadata,
                    "top_50": s.top_50_breakdowns.iter().take(50).map(ScoreBreakdown::to_dict).collect::<Vec<_>>(),
                })
            })
            .collect();
        json!({
            "session_key": self.session_key,
            "query_text": py_prefix(&self.query_text, 100),
            "query_type": self.query_type,
            "gate_profile": self.gate_profile,
            "is_list_question": self.is_list_question,
            "router": {
                "strategy": self.router_strategy,
                "confidence": self.router_confidence,
                "entities": self.entities_detected,
                "temporal_markers": self.temporal_markers,
            },
            "stages": stages,
            "rerank": {
                "pre_order": self.pre_rerank_order.iter().take(20).collect::<Vec<_>>(),
                "post_order": self.post_rerank_order.iter().take(20).collect::<Vec<_>>(),
                "score_deltas": self.rerank_deltas,
            },
            "evidence": self.evidence_snippets.iter().map(EvidenceSnippet::to_dict).collect::<Vec<_>>(),
            "total_latency_ms": self.total_latency_ms(),
            "stage_latencies": self.stage_latencies(),
            "final_candidates": self.final_candidates,
            "gold_rank": self.gold_rank,
            "gold_in_top_1": self.gold_in_top_1,
            "gold_in_top_5": self.gold_in_top_5,
            "gold_in_top_10": self.gold_in_top_10,
            "answer_correct": self.answer_correct,
            "generated_answer": py_prefix(&self.generated_answer, 200),
            "gold_answer": self.gold_answer,
        })
    }

    /// `json.dumps(summary, indent=2, default=str)`.
    pub fn to_json(&self) -> String {
        let fmt = JsonFormat { ensure_ascii: true, sort_keys: false, indent: Some(2), item_sep: ",", key_sep: ": " };
        dumps(&self.get_summary(), &fmt)
    }
}

/// Aggregates attributions across queries and appends their summaries to a JSONL file.
pub struct AttributionLogger {
    attributions: Vec<AttributionRef>,
    log_path: Option<PathBuf>,
    pub enabled: bool,
    buffer_size: usize,
    buffer: Vec<Value>,
    by_category: IndexMap<String, Vec<AttributionRef>>,
    by_strategy: IndexMap<String, Vec<AttributionRef>>,
}

impl AttributionLogger {
    /// Python defaults: no log file, enabled, buffer of 10. The log file's directory is created here; failing
    /// to create it is the error Python's `mkdir` raised from the constructor. An empty path means no file
    /// (`Path(log_path) if log_path else None`).
    pub fn new(log_path: Option<&Path>, enabled: bool, buffer_size: usize) -> std::io::Result<Self> {
        let log_path = log_path.filter(|p| !p.as_os_str().is_empty()).map(Path::to_path_buf);
        if let Some(parent) = log_path.as_ref().and_then(|p| p.parent()) {
            std::fs::create_dir_all(parent)?;
        }
        Ok(AttributionLogger {
            attributions: Vec::new(),
            log_path,
            enabled,
            buffer_size,
            buffer: Vec::new(),
            by_category: IndexMap::new(),
            by_strategy: IndexMap::new(),
        })
    }

    pub fn attributions(&self) -> &[AttributionRef] {
        &self.attributions
    }

    pub fn log_path(&self) -> Option<&Path> {
        self.log_path.as_deref()
    }

    /// Summaries waiting for the next flush.
    pub fn buffered(&self) -> &[Value] {
        &self.buffer
    }

    /// Add a record (ignored when disabled); with a log file its summary is buffered and the buffer flushed
    /// once it holds `buffer_size` records.
    pub fn add(&mut self, attribution: AttributionRef) {
        if !self.enabled {
            return;
        }
        self.attributions.push(attribution.clone());
        let (cat, strat) = {
            let a = attribution.borrow();
            (a.query_type.clone(), a.router_strategy.clone())
        };
        self.by_category.entry(cat).or_default().push(attribution.clone());
        self.by_strategy.entry(strat).or_default().push(attribution.clone());
        if self.log_path.is_some() {
            self.buffer.push(attribution.borrow().get_summary());
            if self.buffer.len() >= self.buffer_size {
                self.flush();
            }
        }
    }

    /// Append the buffered summaries to the log file, one compact JSON object per line
    /// (`json.dumps(record, ensure_ascii=False)`). Write errors are logged; the buffer is cleared either way.
    pub fn flush(&mut self) {
        let Some(path) = self.log_path.clone() else { return };
        if self.buffer.is_empty() {
            return;
        }
        let result = (|| -> std::io::Result<()> {
            let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
            for record in &self.buffer {
                let line = dumps(record, &JsonFormat::unicode()) + "\n";
                f.write_all(line.as_bytes())?;
            }
            Ok(())
        })();
        match result {
            Ok(()) => tracing::debug!("Flushed {} attribution records to {}", self.buffer.len(), path.display()),
            Err(e) => tracing::warn!("Failed to write attribution log: {e}"),
        }
        self.buffer.clear();
    }

    /// Flush what is left.
    pub fn close(&mut self) {
        self.flush();
    }

    /// `_append_to_log` (deprecated in Python, unused): append one summary immediately. Without a log file
    /// Python's `open(None)` raised `TypeError`, which was caught and logged.
    pub fn append_to_log(&self, attribution: &RetrievalAttribution) {
        let result = match &self.log_path {
            None => Err(std::io::Error::other("expected str, bytes or os.PathLike object, not NoneType")),
            Some(path) => std::fs::OpenOptions::new().create(true).append(true).open(path).and_then(|mut f| {
                let line = dumps(&attribution.get_summary(), &JsonFormat::unicode()) + "\n";
                f.write_all(line.as_bytes())
            }),
        };
        if let Err(e) = result {
            tracing::warn!("Failed to write attribution log: {e}");
        }
    }

    /// Category -> share of queries with the gold answer in the top `k` (1, 5, anything else = 10).
    pub fn get_precision_at_k(&self, k: usize) -> IndexMap<String, f64> {
        let mut results = IndexMap::new();
        for (category, attrs) in &self.by_category {
            if attrs.is_empty() {
                continue;
            }
            let hits = attrs
                .iter()
                .filter(|a| {
                    let a = a.borrow();
                    match k {
                        1 => a.gold_in_top_1,
                        5 => a.gold_in_top_5,
                        _ => a.gold_in_top_10,
                    }
                })
                .count();
            results.insert(category.clone(), hits as f64 / attrs.len() as f64);
        }
        results
    }

    /// Stage -> `{mean, p50, p95, p99, count}` of its latencies (p95/p99 fall back to the maximum below 20/100
    /// samples).
    pub fn get_latency_stats(&self) -> IndexMap<String, Value> {
        let mut stage_latencies: IndexMap<String, Vec<f64>> = IndexMap::new();
        for attr in &self.attributions {
            for stage in &attr.borrow().stages {
                stage_latencies.entry(stage.stage_name.clone()).or_default().push(stage.latency_ms);
            }
        }
        let mut results = IndexMap::new();
        for (stage, latencies) in stage_latencies {
            if latencies.is_empty() {
                continue;
            }
            let mut sorted_lat = latencies.clone();
            sorted_lat.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let n = sorted_lat.len();
            let mut sum = 0.0;
            for l in &latencies {
                sum += l;
            }
            let last = sorted_lat[n - 1];
            results.insert(
                stage,
                json!({
                    "mean": sum / n as f64,
                    "p50": sorted_lat[n / 2],
                    "p95": if n >= 20 { sorted_lat[(n as f64 * 0.95) as usize] } else { last },
                    "p99": if n >= 100 { sorted_lat[(n as f64 * 0.99) as usize] } else { last },
                    "count": n,
                }),
            );
        }
        results
    }

    /// Strategy -> `{count, accuracy, precision_at_1, avg_latency_ms}`.
    pub fn get_strategy_effectiveness(&self) -> IndexMap<String, Value> {
        let mut results = IndexMap::new();
        for (strategy, attrs) in &self.by_strategy {
            if attrs.is_empty() {
                continue;
            }
            let n = attrs.len() as f64;
            let correct = attrs.iter().filter(|a| a.borrow().answer_correct).count();
            let top1 = attrs.iter().filter(|a| a.borrow().gold_in_top_1).count();
            let mut latency = 0.0;
            for a in attrs {
                latency += a.borrow().total_latency_ms();
            }
            results.insert(
                strategy.clone(),
                json!({
                    "count": attrs.len(),
                    "accuracy": correct as f64 / n,
                    "precision_at_1": top1 as f64 / n,
                    "avg_latency_ms": latency / n,
                }),
            );
        }
        results
    }

    /// Mean relative change of the top score from the first-pass stage to the "rerank" stage, in percent.
    pub fn get_rerank_gain(&self) -> Value {
        let with_rerank: Vec<&AttributionRef> =
            self.attributions.iter().filter(|a| a.borrow().stages.iter().any(|s| s.stage_name == "rerank")).collect();
        if with_rerank.is_empty() {
            return json!({"rerank_gain": 0.0, "samples": 0});
        }
        let mut total_gain = 0.0;
        for attr in &with_rerank {
            let (mut pre_score, mut post_score) = (0.0, 0.0);
            for stage in &attr.borrow().stages {
                if matches!(stage.stage_name.as_str(), "vector_search" | "flash_retrieve" | "neural_retrieve") {
                    pre_score = stage.top_score;
                } else if stage.stage_name == "rerank" {
                    post_score = stage.top_score;
                }
            }
            if pre_score > 0.0 {
                total_gain += (post_score - pre_score) / pre_score;
            }
        }
        json!({
            "rerank_gain_pct": (total_gain / with_rerank.len() as f64) * 100.0,
            "samples": with_rerank.len(),
        })
    }

    /// Everything above in one dictionary.
    pub fn get_summary(&self) -> Value {
        json!({
            "total_queries": self.attributions.len(),
            "precision_at_1": self.get_precision_at_k(1),
            "precision_at_5": self.get_precision_at_k(5),
            "precision_at_10": self.get_precision_at_k(10),
            "latency_stats": self.get_latency_stats(),
            "strategy_effectiveness": self.get_strategy_effectiveness(),
            "rerank_gain": self.get_rerank_gain(),
            "by_category_count": self.by_category.iter().map(|(k, v)| (k.clone(), json!(v.len()))).collect::<Map<_, _>>(),
            "by_strategy_count": self.by_strategy.iter().map(|(k, v)| (k.clone(), json!(v.len()))).collect::<Map<_, _>>(),
        })
    }
}

thread_local! {
    static GLOBAL_LOGGER: RefCell<Option<Rc<RefCell<AttributionLogger>>>> = const { RefCell::new(None) };
}

/// The global attribution logger, created on first use (no log file, enabled, buffer 10).
pub fn get_attribution_logger() -> Rc<RefCell<AttributionLogger>> {
    GLOBAL_LOGGER.with(|g| {
        g.borrow_mut()
            .get_or_insert_with(|| {
                Rc::new(RefCell::new(AttributionLogger::new(None, true, 10).expect("no log file, nothing to create")))
            })
            .clone()
    })
}

/// Drop the global logger; the next [`get_attribution_logger`] creates a fresh one.
pub fn reset_attribution_logger() {
    GLOBAL_LOGGER.with(|g| *g.borrow_mut() = None);
}

/// A new attribution tracker for a query.
pub fn create_attribution(session_key: &str, query_text: &str) -> RetrievalAttribution {
    RetrievalAttribution::new(session_key, query_text)
}
