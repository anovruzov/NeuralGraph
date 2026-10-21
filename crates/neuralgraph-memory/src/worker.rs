//! Background worker: runs the model continuously and in parallel over the durable job queue.
//!
//! * `concurrency` tasks each lease work from [`ChatMemoryStore`] (`lease_job`); the SQL lease guarantees per-chat
//!   message order while different chats are processed in parallel. Each extraction pass issues two LLM calls
//!   concurrently (memories + relations) plus one reconcile call per ambiguous candidate, so effective LLM
//!   parallelism is higher than `concurrency`.
//! * A lease is renewed by a heartbeat while a batch is in flight; a worker that dies leaves an expired lease that
//!   [`ChatMemoryStore::requeue_expired_leases`] returns to the queue on the next start or sweep.
//! * Failures back off exponentially and dead-letter after `max_attempts`; the message keeps its raw text and
//!   status `failed` so nothing is lost.
//! * Periodic maintenance (`maintain` jobs): embed memories whose embedding is missing, sweep expired leases, prune
//!   old done jobs, and merge near-duplicate active memories that slipped through when two chats produced the
//!   same fact at the same time.

use crate::error::{MemoryError, Result};
use crate::extraction::{cos, MemoryExtractor};
use crate::llm::LlmClient;
use crate::models::{iso, utcnow, Job};
use crate::pycompat::{fnum, obj, py_prefix, py_round};
use crate::store::{ChatMemoryStore, MemoryField};
use indexmap::{IndexMap, IndexSet};
use serde_json::Value;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};
use tokio::sync::Notify;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

/// Worker settings (defaults are the measured ones).
#[derive(Clone, Debug)]
pub struct WorkerConfig {
    /// chats processed in parallel (each pass = 2+ concurrent LLM calls)
    pub concurrency: usize,
    /// consecutive messages of one chat per extraction pass. Measured on LoCoMo
    /// (research/benchmarks/chat_memory_locomo.py): the extractor emits ~2 memories per call whatever the window,
    /// so 2-message batches recover 15 pts more evidence than 6-message batches, at ~2.5x extraction time.
    pub batch_size: i64,
    /// above 0: instead of a fixed message count, pull contiguous messages until this many words are queued (capped by
    /// `batch_words_max_messages`)
    pub batch_words: i64,
    /// hard cap on messages per pass in word mode
    pub batch_words_max_messages: i64,
    /// seconds between queue checks while work may be pending
    pub poll_interval: f64,
    /// seconds between checks when the queue was empty
    pub idle_poll_interval: f64,
    pub lease_seconds: f64,
    pub heartbeat_seconds: f64,
    pub backoff_base: f64,
    pub backoff_max: f64,
    /// seconds; 0 disables periodic maintenance
    pub maintenance_interval: f64,
    /// expired-lease sweep
    pub sweep_interval: f64,
    pub embed_missing_limit: i64,
    pub merge_duplicates_limit: usize,
    pub merge_cosine: f64,
    pub prune_done_after_days: f64,
    pub prune_audit_after_days: f64,
    /// memories re-embedded per maintenance run after an embedding-model change
    pub reembed_limit: i64,
}

impl Default for WorkerConfig {
    fn default() -> Self {
        WorkerConfig {
            concurrency: 2,
            batch_size: 2,
            batch_words: 0,
            batch_words_max_messages: 40,
            poll_interval: 0.25,
            idle_poll_interval: 2.0,
            lease_seconds: 300.0,
            heartbeat_seconds: 30.0,
            backoff_base: 5.0,
            backoff_max: 600.0,
            maintenance_interval: 900.0,
            sweep_interval: 60.0,
            embed_missing_limit: 50,
            merge_duplicates_limit: 200,
            merge_cosine: 0.96,
            prune_done_after_days: 7.0,
            prune_audit_after_days: 30.0,
            reembed_limit: 50,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct WorkerMetrics {
    pub started_at: Option<String>,
    pub batches: i64,
    pub messages_processed: i64,
    pub messages_skipped: i64,
    pub memories_added: i64,
    pub memories_superseded: i64,
    pub duplicates: i64,
    pub relations: i64,
    pub links: i64,
    pub failures: i64,
    pub dead_jobs: i64,
    pub maintenance_runs: i64,
    pub last_error: Option<String>,
    pub last_batch_at: Option<String>,
    pub last_batch_seconds: f64,
    pub busy_workers: i64,
    /// ring buffer of recent decisions
    pub recent: Vec<Value>,
}

impl WorkerMetrics {
    pub fn note(&mut self, kind: &str, detail: Value) {
        let mut entry = serde_json::Map::new();
        entry.insert("at".into(), Value::String(iso(utcnow())));
        entry.insert("kind".into(), Value::String(kind.into()));
        if let Value::Object(d) = detail {
            for (k, v) in d {
                entry.insert(k, v);
            }
        }
        self.recent.push(Value::Object(entry));
        if self.recent.len() > 200 {
            let excess = self.recent.len() - 200;
            self.recent.drain(..excess);
        }
    }

    pub fn as_value(&self) -> Value {
        let opt = |s: &Option<String>| s.clone().map(Value::String).unwrap_or(Value::Null);
        let start = self.recent.len().saturating_sub(50);
        obj([
            ("started_at", opt(&self.started_at)),
            ("batches", Value::from(self.batches)),
            ("messages_processed", Value::from(self.messages_processed)),
            ("messages_skipped", Value::from(self.messages_skipped)),
            ("memories_added", Value::from(self.memories_added)),
            ("memories_superseded", Value::from(self.memories_superseded)),
            ("duplicates", Value::from(self.duplicates)),
            ("relations", Value::from(self.relations)),
            ("links", Value::from(self.links)),
            ("failures", Value::from(self.failures)),
            ("dead_jobs", Value::from(self.dead_jobs)),
            ("maintenance_runs", Value::from(self.maintenance_runs)),
            ("last_error", opt(&self.last_error)),
            ("last_batch_at", opt(&self.last_batch_at)),
            ("last_batch_seconds", fnum(self.last_batch_seconds)),
            ("busy_workers", Value::from(self.busy_workers)),
            ("recent", Value::Array(self.recent[start..].to_vec())),
        ])
    }
}

/// asyncio.Event equivalent: a flag that stays set until cleared, with async waiting.
#[derive(Default)]
pub struct Event {
    flag: AtomicBool,
    notify: Notify,
}

impl Event {
    pub fn set(&self) {
        self.flag.store(true, Ordering::SeqCst);
        self.notify.notify_waiters();
    }

    pub fn clear(&self) {
        self.flag.store(false, Ordering::SeqCst);
    }

    pub fn is_set(&self) -> bool {
        self.flag.load(Ordering::SeqCst)
    }

    pub async fn wait(&self) {
        loop {
            let notified = self.notify.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            if self.is_set() {
                return;
            }
            notified.await;
        }
    }

    /// Wait at most `seconds`; true when the event was set.
    pub async fn wait_timeout(&self, seconds: f64) -> bool {
        tokio::time::timeout(Duration::from_secs_f64(seconds.max(0.0)), self.wait()).await.is_ok()
    }
}

pub struct MemoryWorker {
    pub store: Arc<ChatMemoryStore>,
    llm: RwLock<Arc<dyn LlmClient>>,
    pub extractor: Arc<MemoryExtractor>,
    pub config: WorkerConfig,
    metrics: Mutex<WorkerMetrics>,
    pub worker_id: String,
    tasks: Mutex<Vec<JoinHandle<()>>>,
    stop_event: Event,
    wake_event: Event,
    idle_event: Event,
    busy: AtomicI64,
    last_sweep: Mutex<Option<Instant>>,
    last_maint: Mutex<Instant>,
    running: AtomicBool,
    embed_dim: Mutex<Option<String>>,
    cancel: Mutex<CancellationToken>,
}

/// Outcome of processing one leased batch.
enum Processed {
    Done,
    Cancelled,
}

impl MemoryWorker {
    pub fn new(
        store: Arc<ChatMemoryStore>,
        llm: Arc<dyn LlmClient>,
        extractor: Arc<MemoryExtractor>,
        config: WorkerConfig,
    ) -> Self {
        let hex = uuid::Uuid::new_v4().simple().to_string();
        let w = MemoryWorker {
            store,
            llm: RwLock::new(llm),
            extractor,
            config,
            metrics: Mutex::new(WorkerMetrics::default()),
            worker_id: format!("w-{}", &hex[..8]),
            tasks: Mutex::new(Vec::new()),
            stop_event: Event::default(),
            wake_event: Event::default(),
            idle_event: Event::default(),
            busy: AtomicI64::new(0),
            last_sweep: Mutex::new(None),
            last_maint: Mutex::new(Instant::now()),
            running: AtomicBool::new(false),
            embed_dim: Mutex::new(None),
            cancel: Mutex::new(CancellationToken::new()),
        };
        w.idle_event.set();
        w
    }

    /// The model used by maintenance (swappable, e.g. after an embedding-model change).
    pub fn llm(&self) -> Arc<dyn LlmClient> {
        self.llm.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn set_llm(&self, llm: Arc<dyn LlmClient>) {
        *self.llm.write().unwrap_or_else(|e| e.into_inner()) = llm;
    }

    pub fn metrics(&self) -> WorkerMetrics {
        self.metrics.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn with_metrics<T>(&self, f: impl FnOnce(&mut WorkerMetrics) -> T) -> T {
        f(&mut self.metrics.lock().unwrap_or_else(|e| e.into_inner()))
    }

    // ------------------------------------------------------------------ lifecycle
    pub fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Signal that new work was enqueued (called by the service after `add_message`).
    pub fn wake(&self) {
        self.wake_event.set();
    }

    pub async fn start(self: &Arc<Self>) -> Result<()> {
        if self.running() {
            return Ok(());
        }
        self.stop_event.clear();
        *self.cancel.lock().unwrap_or_else(|e| e.into_inner()) = CancellationToken::new();
        self.running.store(true, Ordering::SeqCst);
        self.with_metrics(|m| m.started_at = Some(iso(utcnow())));
        let n = self.store.requeue_expired_leases()?;
        if n > 0 {
            tracing::info!("requeued {n} expired leases from a previous run");
        }
        let mut tasks = self.tasks.lock().unwrap_or_else(|e| e.into_inner());
        for i in 0..self.config.concurrency.max(1) {
            let me = Arc::clone(self);
            tasks.push(tokio::spawn(async move { me.run_loop(i).await }));
        }
        Ok(())
    }

    /// Stop after in-flight batches finish (or cancel them after `timeout`): a cancelled batch is returned to the
    /// queue immediately (its attempt stays charged) and its messages go back to `pending`.
    pub async fn stop(&self, timeout: f64) {
        if !self.running() {
            return;
        }
        self.stop_event.set();
        self.wake_event.set();
        let mut tasks: Vec<JoinHandle<()>> = std::mem::take(&mut *self.tasks.lock().unwrap_or_else(|e| e.into_inner()));
        let deadline = tokio::time::Instant::now() + Duration::from_secs_f64(timeout.max(0.0));
        let mut pending = Vec::new();
        for t in tasks.drain(..) {
            let mut t = t;
            match tokio::time::timeout_at(deadline, &mut t).await {
                Ok(_) => {}
                Err(_) => pending.push(t),
            }
        }
        if !pending.is_empty() {
            self.cancel.lock().unwrap_or_else(|e| e.into_inner()).cancel();
            for t in pending {
                let _ = t.await;
            }
        }
        self.running.store(false, Ordering::SeqCst);
    }

    /// Wait until the queue is empty and no batch is in flight. Returns false on timeout.
    pub async fn wait_idle(&self, timeout: Option<f64>) -> Result<bool> {
        let deadline = timeout.map(|t| Instant::now() + Duration::from_secs_f64(t.max(0.0)));
        loop {
            let pending = self.store.pending_jobs(None)?;
            if pending == 0 && self.busy.load(Ordering::SeqCst) == 0 {
                return Ok(true);
            }
            if !self.running() {
                return Ok(pending == 0);
            }
            let remaining = deadline.map(|d| d.saturating_duration_since(Instant::now()).as_secs_f64());
            if remaining.is_some_and(|r| r <= 0.0) {
                return Ok(false);
            }
            self.wake_event.set();
            let wait = remaining.map(|r| r.min(0.2)).unwrap_or(0.2);
            if self.busy.load(Ordering::SeqCst) > 0 {
                self.idle_event.wait_timeout(wait).await;
            } else {
                // queued but not leased yet (debounce, backoff, a worker about to lease): the idle event is already
                // set, so poll briefly instead of spinning
                tokio::time::sleep(Duration::from_secs_f64(wait.min(0.01))).await;
            }
        }
    }

    fn lease(&self) -> Result<Vec<Job>> {
        let cfg = &self.config;
        self.store.lease_job(
            &self.worker_id,
            cfg.lease_seconds,
            None,
            cfg.batch_size,
            cfg.batch_words,
            cfg.batch_words_max_messages,
        )
    }

    /// Lease and process one batch in the current task. Returns false if the queue was empty.
    pub async fn run_once(&self) -> Result<bool> {
        let jobs = self.lease()?;
        if jobs.is_empty() {
            return Ok(false);
        }
        self.process(jobs).await?;
        Ok(true)
    }

    /// Process until the queue is empty (CLI batch ingestion and tests). Returns batches processed.
    pub async fn drain(&self, max_batches: Option<i64>) -> Result<i64> {
        let mut n = 0;
        self.store.requeue_expired_leases()?;
        while max_batches.is_none_or(|m| n < m) {
            if !self.run_once().await? {
                // nothing runnable now: maybe backing off, or a lease left behind by a crashed process
                if self.store.pending_jobs(None)? == 0 {
                    break;
                }
                if self.store.requeue_expired_leases()? > 0 {
                    continue;
                }
                let nxt = self.store.next_available_at()?;
                if nxt.is_none() {
                    break; // only foreign live leases remain; nothing this drain can do
                }
                tokio::time::sleep(Duration::from_secs_f64(self.config.poll_interval.min(1.0))).await;
                continue;
            }
            n += 1;
        }
        Ok(n)
    }

    // ------------------------------------------------------------------ main loop
    async fn run_loop(self: Arc<Self>, idx: usize) {
        let cancel = self.cancel.lock().unwrap_or_else(|e| e.into_inner()).clone();
        while !self.stop_event.is_set() && !cancel.is_cancelled() {
            match self.loop_step().await {
                Ok(Some(Processed::Cancelled)) => return,
                Ok(_) => {}
                Err(exc) => {
                    // never let a worker die
                    tracing::error!("worker {idx} loop error: {exc}");
                    let msg = py_prefix(&exc.message(), 500).to_string();
                    self.with_metrics(|m| m.last_error = Some(msg));
                    tokio::select! {
                        _ = tokio::time::sleep(Duration::from_secs(1)) => {}
                        _ = cancel.cancelled() => return,
                    }
                }
            }
        }
    }

    async fn loop_step(&self) -> Result<Option<Processed>> {
        let cfg = &self.config;
        self.periodic()?;
        let jobs = self.lease()?;
        if jobs.is_empty() {
            self.wake_event.clear();
            let pending = self.store.pending_jobs(None)?;
            let wait = if pending > 0 { cfg.poll_interval } else { cfg.idle_poll_interval };
            let cancel = self.cancel.lock().unwrap_or_else(|e| e.into_inner()).clone();
            tokio::select! {
                _ = self.wake_event.wait_timeout(wait) => {}
                _ = cancel.cancelled() => return Ok(Some(Processed::Cancelled)),
            }
            return Ok(None);
        }
        let busy = self.busy.fetch_add(1, Ordering::SeqCst) + 1;
        self.idle_event.clear();
        self.with_metrics(|m| m.busy_workers = busy);
        let result = self.process(jobs).await;
        let busy = self.busy.fetch_sub(1, Ordering::SeqCst) - 1;
        self.with_metrics(|m| m.busy_workers = busy);
        if busy == 0 {
            self.idle_event.set();
        }
        result.map(Some)
    }

    fn periodic(&self) -> Result<()> {
        let cfg = &self.config;
        let now = Instant::now();
        let sweep_due = {
            let mut last = self.last_sweep.lock().unwrap_or_else(|e| e.into_inner());
            let due = last.is_none_or(|t| now.duration_since(t).as_secs_f64() >= cfg.sweep_interval);
            if due {
                *last = Some(now);
            }
            due
        };
        if sweep_due {
            let n = self.store.requeue_expired_leases()?;
            if n > 0 {
                tracing::warn!("requeued {n} expired leases");
            }
        }
        if cfg.maintenance_interval > 0.0 {
            let due = {
                let mut last = self.last_maint.lock().unwrap_or_else(|e| e.into_inner());
                let due = now.duration_since(*last).as_secs_f64() >= cfg.maintenance_interval;
                if due {
                    *last = now;
                }
                due
            };
            if due {
                let epoch = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);
                let bucket = (epoch / cfg.maintenance_interval).floor() as i64;
                self.store.enqueue_job(
                    "maintain",
                    None,
                    None,
                    None,
                    Some(&format!("maintain:{bucket}")),
                    None,
                    2,
                    None,
                )?;
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------ processing
    /// Process jobs this worker already leased, in the current task (what a loop iteration does after
    /// `lease_job`; exposed so a zombie worker committing after its lease expired can be simulated).
    pub async fn process_leased(&self, jobs: Vec<Job>) -> Result<()> {
        self.process(jobs).await.map(|_| ())
    }

    async fn process(&self, jobs: Vec<Job>) -> Result<Processed> {
        if jobs[0].kind == "maintain" {
            // a cancelled maintenance pass leaves its job leased until the lease expires, as before
            let cancel = self.cancel.lock().unwrap_or_else(|e| e.into_inner()).clone();
            return Ok(tokio::select! {
                _ = self.run_maintenance(&jobs) => Processed::Done,
                _ = cancel.cancelled() => Processed::Cancelled,
            });
        }
        let cfg = &self.config;
        let t0 = Instant::now();
        let ids: Vec<i64> = jobs.iter().map(|j| j.job_id).collect();
        let message_ids: Vec<String> = jobs.iter().filter_map(|j| j.ref_id.clone().filter(|r| !r.is_empty())).collect();
        let messages: Vec<_> = self
            .store
            .get_messages_by_ids(&message_ids)?
            .into_iter()
            .filter(|m| matches!(m.status.as_str(), "pending" | "processing" | "failed"))
            .collect();
        if messages.is_empty() {
            self.store.complete_jobs(&ids)?;
            return Ok(Processed::Done);
        }
        let msg_ids: Vec<String> = messages.iter().map(|m| m.message_id.clone()).collect();
        self.store.set_message_status(&msg_ids, "processing")?;
        let hb = {
            let store = Arc::clone(&self.store);
            let ids = ids.clone();
            let (every, lease) = (cfg.heartbeat_seconds, cfg.lease_seconds);
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_secs_f64(every.max(0.001))).await;
                    if let Err(exc) = store.heartbeat(&ids, lease) {
                        // a transient store error must not silently drop the lease renewal
                        tracing::warn!("heartbeat failed (will retry): {exc}");
                    }
                }
            })
        };
        let attempt = jobs.iter().map(|j| j.attempts).max().unwrap_or(1).max(1);
        let cancel = self.cancel.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let built = tokio::select! {
            r = self.extractor.build_plan(&messages, &ids, attempt) => Some(r),
            _ = cancel.cancelled() => None,
        };
        let outcome = match built {
            None => {
                hb.abort();
                self.store.set_message_status(&msg_ids, "pending")?;
                self.store.release_jobs(&ids, Some(&self.worker_id))?;
                return Ok(Processed::Cancelled);
            }
            Some(Ok(plan)) => self.store.apply_plan(&plan, Some(&self.worker_id)).map(|c| (plan, c)),
            Some(Err(e)) => Err(e),
        };
        hb.abort();
        let (plan, counts) = match outcome {
            Ok(v) => v,
            Err(MemoryError::LostLease(detail)) => {
                // our lease expired and another worker took the batch: their commit wins, ours is discarded
                let chat = jobs[0].chat_id.clone();
                self.with_metrics(|m| {
                    m.note(
                        "lost_lease",
                        obj([
                            ("chat_id", chat.clone().map(Value::String).unwrap_or(Value::Null)),
                            ("detail", Value::String(detail.clone())),
                        ]),
                    )
                });
                tracing::warn!("discarding result for chat {}: {detail}", chat.unwrap_or_default());
                return Ok(Processed::Done);
            }
            Err(exc) => {
                let attempts = jobs.iter().map(|j| j.attempts).max().unwrap_or(0);
                let exp = (attempts - 1).clamp(0, 1000) as i32;
                let jitter = 0.8 + 0.4 * rand::random::<f64>();
                let delay = cfg.backoff_max.min(cfg.backoff_base * 2f64.powi(exp)) * jitter;
                let result = self.store.fail_jobs(&ids, &exc.qualified(), delay, Some(&self.worker_id))?;
                let dead = result.values().filter(|s| *s == "dead").count() as i64;
                let requeued: Vec<String> = messages
                    .iter()
                    .zip(jobs.iter())
                    .filter(|(_, j)| result.get(&j.job_id).map(String::as_str) == Some("queued"))
                    .map(|(m, _)| m.message_id.clone())
                    .collect();
                if !requeued.is_empty() {
                    self.store.set_message_status(&requeued, "pending")?;
                }
                let chat = jobs[0].chat_id.clone();
                let qualified = exc.qualified();
                self.with_metrics(|m| {
                    m.failures += 1;
                    m.dead_jobs += dead;
                    m.last_error = Some(py_prefix(&qualified, 500).to_string());
                    m.note(
                        "failure",
                        obj([
                            ("chat_id", chat.clone().map(Value::String).unwrap_or(Value::Null)),
                            ("error", Value::String(py_prefix(&exc.message(), 200).to_string())),
                            ("dead", Value::from(dead)),
                            ("retry_in", fnum(py_round(delay, 1))),
                        ]),
                    );
                });
                let what = if dead > 0 { "dead-lettered".to_string() } else { format!("retry in {delay:.0}s") };
                tracing::warn!(
                    "batch failed (chat {}, {} msgs): {}; {what}",
                    chat.unwrap_or_default(),
                    messages.len(),
                    exc.message()
                );
                return Ok(Processed::Done);
            }
        };
        let dt = t0.elapsed().as_secs_f64();
        let dims: IndexSet<usize> = plan
            .new_memories
            .iter()
            .filter_map(|(m, _, _)| m.embedding.as_ref().filter(|e| !e.is_empty()).map(Vec::len))
            .collect();
        if dims.len() == 1 {
            let d = dims[0].to_string();
            let changed = {
                let mut cur = self.embed_dim.lock().unwrap_or_else(|e| e.into_inner());
                if cur.as_deref() != Some(d.as_str()) {
                    *cur = Some(d.clone());
                    true
                } else {
                    false
                }
            };
            if changed {
                self.store.set_meta("embed_dim", &d)?;
            }
        }
        self.with_metrics(|m| {
            m.batches += 1;
            m.messages_processed += plan.processed_message_ids.len() as i64;
            m.messages_skipped += plan.skipped_message_ids.len() as i64;
            m.memories_added += counts.memories_added;
            m.memories_superseded += counts.memories_superseded;
            m.duplicates += counts.duplicates;
            m.relations += counts.relations;
            m.links += counts.links;
            m.last_batch_at = Some(iso(utcnow()));
            m.last_batch_seconds = dt;
            for (mem, _, _) in &plan.new_memories {
                let decision = mem.metadata.get("decision").cloned().unwrap_or_else(|| Value::from("ADD"));
                m.note(
                    "memory",
                    obj([
                        ("chat_id", Value::String(plan.chat_id.clone())),
                        ("memory_id", Value::String(mem.memory_id.clone())),
                        ("text", Value::String(py_prefix(&mem.text, 160).to_string())),
                        ("kind", Value::String(mem.kind.clone())),
                        ("subject", Value::String(mem.subject_name.clone())),
                        ("decision", decision),
                    ]),
                );
            }
            if !plan.skipped_message_ids.is_empty() && plan.new_memories.is_empty() {
                m.note(
                    "skip",
                    obj([
                        ("chat_id", Value::String(plan.chat_id.clone())),
                        ("messages", Value::from(plan.skipped_message_ids.len())),
                    ]),
                );
            }
        });
        tracing::info!("chat {}: {} msgs -> {} in {dt:.1}s", plan.chat_id, messages.len(), plan.summary());
        Ok(Processed::Done)
    }

    // ------------------------------------------------------------------ maintenance
    async fn run_maintenance(&self, jobs: &[Job]) {
        let ids: Vec<i64> = jobs.iter().map(|j| j.job_id).collect();
        let result = match self.maintain().await {
            Ok(report) => self.store.complete_jobs(&ids).map(|_| report),
            Err(e) => Err(e),
        };
        match result {
            Ok(report) => self.with_metrics(|m| {
                m.maintenance_runs += 1;
                m.note("maintenance", report);
            }),
            Err(exc) => {
                let _ = self.store.fail_jobs(
                    &ids,
                    &format!("maintenance: {}", exc.message()),
                    self.config.backoff_base,
                    Some(&self.worker_id),
                );
                let msg = py_prefix(&format!("maintenance: {}", exc.message()), 500).to_string();
                self.with_metrics(|m| m.last_error = Some(msg));
                tracing::warn!("maintenance failed: {exc}");
            }
        }
    }

    /// One maintenance pass (also callable directly). Returns a small report.
    pub async fn maintain(&self) -> Result<Value> {
        let cfg = &self.config;
        let mut errors: Vec<Value> = Vec::new();
        let requeued = self.store.requeue_expired_leases()?;
        // embedding back-fill: an embedding outage must not abort the rest of maintenance
        let (embedded, reembedded) = match self.backfill_embeddings().await {
            Ok(v) => v,
            Err((partial, exc)) => {
                errors.push(Value::String(py_prefix(&format!("embed: {}", exc.message()), 200).to_string()));
                tracing::warn!("maintenance: embedding back-fill skipped: {exc}");
                partial
            }
        };
        // merge exact/near duplicates among recent active memories of the same subject
        let rows = self.store.active_embedding_rows()?;
        let start = rows.len().saturating_sub(cfg.merge_duplicates_limit * 4);
        let mut by_subject: IndexMap<String, Vec<(String, Vec<f64>)>> = IndexMap::new();
        for (mid, subject, emb) in rows.into_iter().skip(start) {
            by_subject.entry(subject).or_default().push((mid, emb));
        }
        let mut merged = 0usize;
        for (_, items) in by_subject {
            let first = items.len().saturating_sub(cfg.merge_duplicates_limit);
            let items = &items[first..];
            for i in 0..items.len() {
                for j in (i + 1)..items.len() {
                    if merged >= cfg.merge_duplicates_limit {
                        break;
                    }
                    let (a, b) = (&items[i], &items[j]);
                    if cos(Some(&a.1), Some(&b.1)) >= cfg.merge_cosine {
                        let (Some(mut older), Some(mut newer)) =
                            (self.store.get_memory(&a.0, false)?, self.store.get_memory(&b.0, false)?)
                        else {
                            continue;
                        };
                        if older.status != "active" || newer.status != "active" {
                            continue;
                        }
                        if older.observed_at > newer.observed_at {
                            std::mem::swap(&mut older, &mut newer);
                        }
                        self.store.supersede(&older.memory_id, &newer.memory_id, "supersedes")?;
                        let sources = self
                            .store
                            .get_memory(&older.memory_id, true)?
                            .map(|m| m.source_message_ids)
                            .unwrap_or_default();
                        self.store.add_memory_sources(&newer.memory_id, &sources)?;
                        merged += 1;
                    }
                }
            }
        }
        let pruned = self.store.prune_jobs(cfg.prune_done_after_days, &["done"])?;
        let audit_pruned = self.store.prune_audit(cfg.prune_audit_after_days, 5000)?;
        Ok(obj([
            ("embedded", Value::from(embedded)),
            ("reembedded", Value::from(reembedded)),
            ("merged", Value::from(merged)),
            ("requeued", Value::from(requeued)),
            ("pruned", Value::from(pruned)),
            ("audit_pruned", Value::from(audit_pruned)),
            ("errors", Value::Array(errors)),
        ]))
    }

    /// Embed memories that have no vector, learn/probe the embedding dimension, and re-embed rows of another
    /// dimension. On failure returns the counts reached so far with the error.
    async fn backfill_embeddings(&self) -> std::result::Result<(i64, i64), ((i64, i64), MemoryError)> {
        let cfg = &self.config;
        let llm = self.llm();
        let mut embedded = 0;
        let mut reembedded = 0;
        macro_rules! tryp {
            ($e:expr) => {
                match $e {
                    Ok(v) => v,
                    Err(err) => return Err(((embedded, reembedded), err)),
                }
            };
        }
        let missing = tryp!(self.store.memories_missing_embedding(cfg.embed_missing_limit));
        if !missing.is_empty() {
            let texts: Vec<String> = missing.iter().map(|m| m.text.clone()).collect();
            let vecs = tryp!(llm.embed_many(&texts).await);
            for (m, v) in missing.iter().zip(vecs) {
                if !v.is_empty() {
                    let len = v.len();
                    tryp!(self.store.update_memory(&m.memory_id, &[MemoryField::Embedding(Some(v))]));
                    embedded += 1;
                    tryp!(self.store.set_meta("embed_dim", &len.to_string()));
                }
            }
        }
        let mut dim = tryp!(self.store.get_meta("embed_dim")).filter(|d| !d.is_empty());
        if dim.is_none() {
            // nothing learned yet (e.g. every memory so far came from another process): one probe call
            let probe = tryp!(llm.embed_many(&["embedding dimension probe".to_string()]).await);
            if let Some(p) = probe.first().filter(|p| !p.is_empty()) {
                let d = p.len().to_string();
                tryp!(self.store.set_meta("embed_dim", &d));
                dim = Some(d);
            }
        }
        if let Some(d) = dim {
            let want: i64 = match d.trim().parse() {
                Ok(v) => v,
                Err(_) => {
                    let msg = format!("invalid literal for int() with base 10: {}", crate::pycompat::py_repr_str(&d));
                    return Err(((embedded, reembedded), MemoryError::Value(msg)));
                }
            };
            let stale = tryp!(self.store.memories_with_embedding_dim_other_than(want, cfg.reembed_limit));
            if !stale.is_empty() {
                let texts: Vec<String> = stale.iter().map(|m| m.text.clone()).collect();
                let vecs = tryp!(llm.embed_many(&texts).await);
                for (m, v) in stale.iter().zip(vecs) {
                    if !v.is_empty() && v.len() as i64 == want {
                        tryp!(self.store.update_memory(&m.memory_id, &[MemoryField::Embedding(Some(v))]));
                        reembedded += 1;
                    }
                }
            }
        }
        Ok((embedded, reembedded))
    }
}
