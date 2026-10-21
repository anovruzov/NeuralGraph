//! Worker tests: the chat path never blocks on the LLM, chats are processed in parallel while each chat stays
//! ordered, failures back off and dead-letter, expired leases are recovered on restart, a zombie worker cannot
//! double-commit, maintenance back-fills embeddings and merges duplicates, and shutdown is graceful. Uses the
//! scripted fake LLM; no server. Ported from `test_chat_memory_worker.py`.

mod common;

use common::make_memory;
use neuralgraph_memory::llm::{fake_embedding, FakeLlmClient, GenerateHook};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, MessageInput};
use neuralgraph_memory::store::{AddMessage, ChatMemoryStore, MemoryQuery};
use neuralgraph_memory::testing::scripted_fake_llm;
use neuralgraph_memory::worker::Event;
use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

fn fast_config(f: impl FnOnce(&mut ChatMemoryConfig)) -> ChatMemoryConfig {
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    cfg.worker.poll_interval = 0.02;
    cfg.worker.idle_poll_interval = 0.05;
    cfg.worker.backoff_base = 0.05;
    cfg.worker.backoff_max = 0.2;
    cfg.worker.sweep_interval = 0.1;
    cfg.worker.maintenance_interval = 0.0;
    f(&mut cfg);
    cfg
}

struct Fx {
    dir: tempfile::TempDir,
}

impl Fx {
    fn new() -> Self {
        Fx { dir: tempfile::tempdir().unwrap() }
    }

    fn db(&self) -> String {
        self.dir.path().join("worker.db").to_str().unwrap().to_string()
    }

    fn make(&self, llm: Arc<FakeLlmClient>, f: impl FnOnce(&mut ChatMemoryConfig)) -> Arc<ChatMemory> {
        Arc::new(ChatMemory::open(&self.db(), Some(llm), fast_config(f)).unwrap())
    }
}

fn at(sent_at: &str) -> MessageInput {
    MessageInput { sent_at: Some(sent_at.into()), ..Default::default() }
}

fn status(cm: &ChatMemory, which: &str) -> i64 {
    cm.store.message_counts().unwrap().get(which).copied().unwrap_or(0)
}

fn jobs(cm: &ChatMemory) -> Vec<(String, i64)> {
    cm.store.job_counts().unwrap().into_iter().collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn test_add_message_never_waits_for_the_llm() {
    let gate = Arc::new(Event::default());
    let g = gate.clone();
    let hook: GenerateHook = Arc::new(move |_p| {
        let g = g.clone();
        Box::pin(async move {
            g.wait().await;
            Ok("{}".to_string())
        })
    });
    let fx = Fx::new();
    let cm = fx.make(Arc::new(FakeLlmClient { generate_hook: Some(hook), ..Default::default() }), |c| {
        c.worker.concurrency = 2
    });
    cm.start().await.unwrap();
    let t0 = Instant::now();
    for i in 0..20 {
        cm.add_message("chat", "Ali", &format!("I bought item number {i} in Berlin today."), MessageInput::default())
            .unwrap();
    }
    let elapsed = t0.elapsed().as_secs_f64();
    assert!(elapsed < 1.0, "20 add_message calls took {elapsed:.2}s while the LLM was blocked");
    assert_eq!(status(&cm, "pending") + status(&cm, "processing"), 20);
    gate.set();
    cm.close().await;
}

#[tokio::test]
async fn test_parallel_across_chats_ordered_within_chat() {
    let llm = Arc::new(FakeLlmClient { latency: 0.05, ..scripted_fake_llm() });
    let fx = Fx::new();
    let cm = fx.make(llm.clone(), |c| {
        c.worker.concurrency = 3;
        c.worker.batch_size = 1;
    });
    cm.start().await.unwrap();
    for c in ["A", "B", "C"] {
        for i in 0..3 {
            let text = format!("Fact {i} about chat {c}: I visited place number {i} in Berlin.");
            cm.add_message(c, "Ali", &text, at(&format!("2026-01-0{}T10:00:00+00:00", i + 1))).unwrap();
        }
    }
    assert!(cm.wait_until_idle(Some(20.0)).await.unwrap());
    assert!(llm.max_in_flight() >= 3, "three chats should extract concurrently");
    // per-chat order: memories from chat A were created in message order
    for c in ["A", "B", "C"] {
        let q = MemoryQuery { chat_id: Some(c.into()), order: "created_at ASC".into(), ..Default::default() };
        let idx: Vec<u32> = cm
            .memories(&q)
            .unwrap()
            .iter()
            .filter_map(|m| m.text.split("Fact ").nth(1).and_then(|r| r.chars().next()).and_then(|ch| ch.to_digit(10)))
            .collect();
        let mut sorted = idx.clone();
        sorted.sort();
        assert_eq!(idx, sorted);
    }
    assert_eq!(status(&cm, "processed"), 9);
    assert_eq!(jobs(&cm), vec![("done".to_string(), 9)]);
    cm.close().await;
}

#[tokio::test]
async fn test_batches_coalesce_a_turn() {
    let llm = Arc::new(scripted_fake_llm());
    let fx = Fx::new();
    let cm = fx.make(llm.clone(), |c| {
        c.worker.concurrency = 1;
        c.worker.batch_size = 6;
    });
    cm.add_message("t", "Ali", "I moved to Berlin in March.", at("2026-04-01T10:00:00+00:00")).unwrap();
    cm.add_message(
        "t",
        "assistant",
        "Nice! Berlin is lovely.",
        MessageInput { role: "assistant".into(), ..Default::default() },
    )
    .unwrap();
    cm.add_message("t", "Ali", "And my cat Luna is 3 years old.", at("2026-04-01T10:01:00+00:00")).unwrap();
    let n = cm.process_pending(None).await.unwrap();
    assert_eq!(n, 1, "three messages of one chat -> one extraction pass");
    assert_eq!(llm.generate_calls(), 2, "memory + relation prompt, once");
    cm.close().await;
}

#[tokio::test]
async fn test_retry_with_backoff_then_success() {
    // each pass issues 2 concurrent calls: 2 failed passes, then success
    let llm = Arc::new(scripted_fake_llm());
    llm.set_fail_times(3);
    let fx = Fx::new();
    let cm = fx.make(llm, |c| c.worker.concurrency = 1);
    cm.add_message("f", "Ali", "I started a new job at Charite in Berlin.", MessageInput::default()).unwrap();
    cm.start().await.unwrap();
    assert!(cm.wait_until_idle(Some(20.0)).await.unwrap());
    let m = cm.worker.metrics();
    assert_eq!(m.failures, 2);
    assert_eq!(m.batches, 1);
    assert_eq!(status(&cm, "processed"), 1);
    let job = cm.store.list_jobs(Some("done"), 100).unwrap().remove(0);
    assert_eq!(job.attempts, 3);
    assert!(job.last_error.unwrap().contains("scripted failure"));
    cm.close().await;
}

#[tokio::test]
async fn test_dead_letter_after_max_attempts() {
    let llm = Arc::new(FakeLlmClient::default());
    llm.set_fail_times(100);
    let fx = Fx::new();
    let cm = fx.make(llm.clone(), |c| {
        c.worker.concurrency = 1;
        c.max_attempts = 2;
    });
    let msg = cm
        .add_message("d", "Ali", "Something the model cannot process about Berlin.", MessageInput::default())
        .unwrap();
    cm.start().await.unwrap();
    for _ in 0..200 {
        if cm.store.job_counts().unwrap().get("dead").is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert_eq!(jobs(&cm), vec![("dead".to_string(), 1)]);
    assert_eq!(cm.store.get_message(&msg.message_id).unwrap().unwrap().status, "failed");
    assert_eq!(cm.worker.metrics().dead_jobs, 1);
    // the raw message survives and later messages of the chat still flow
    llm.set_fail_times(0);
    cm.add_message("d", "Ali", "I adopted a dog called Rex in Berlin.", MessageInput::default()).unwrap();
    assert!(cm.wait_until_idle(Some(10.0)).await.unwrap());
    assert_eq!(status(&cm, "processed"), 1);
    cm.close().await;
}

#[tokio::test]
async fn test_restart_recovers_expired_leases() {
    let fx = Fx::new();
    let (m, jobs0) = {
        let store = ChatMemoryStore::open(&fx.db()).unwrap();
        let (m, _) =
            store.add_message("r", "Ali", "I live in Berlin with my cat Luna.", &AddMessage::default()).unwrap();
        // crashed mid-flight, lease already expired
        let jobs = store.lease_job("crashed-worker", -1.0, None, 1, 0, 40).unwrap();
        store.set_message_status(std::slice::from_ref(&m.message_id), "processing").unwrap();
        store.close();
        (m, jobs)
    };
    let cm = fx.make(Arc::new(scripted_fake_llm()), |c| c.worker.concurrency = 1);
    cm.start().await.unwrap();
    assert!(cm.wait_until_idle(Some(10.0)).await.unwrap());
    assert_eq!(jobs(&cm), vec![("done".to_string(), 1)]);
    assert_eq!(cm.store.get_message(&m.message_id).unwrap().unwrap().status, "processed");
    assert_eq!(cm.store.get_job(jobs0[0].job_id).unwrap().unwrap().attempts, 2);
    cm.close().await;
}

#[tokio::test]
async fn test_zombie_worker_result_is_discarded() {
    let fx = Fx::new();
    let cm = fx.make(Arc::new(scripted_fake_llm()), |c| c.worker.concurrency = 1);
    cm.add_message("z", "Ali", "I moved to Berlin in March.", MessageInput::default()).unwrap();
    // our lease has already expired
    let jobs0 = cm.store.lease_job(&cm.worker.worker_id, -1.0, None, 1, 0, 40).unwrap();
    cm.store.requeue_expired_leases().unwrap();
    let other = cm.store.lease_job("other-worker", 60.0, None, 1, 0, 40).unwrap(); // someone else took it
                                                                                   // our (zombie) worker finishes its computation and tries to commit
    cm.worker.process_leased(jobs0).await.unwrap();
    let m = cm.worker.metrics();
    assert_eq!(m.batches, 0);
    assert_eq!(m.recent.last().unwrap()["kind"], "lost_lease");
    assert!(cm.memories(&MemoryQuery::default()).unwrap().is_empty(), "nothing committed by the zombie");
    assert_eq!(cm.store.get_job(other[0].job_id).unwrap().unwrap().status, "leased");
    cm.close().await;
}

#[tokio::test]
async fn test_graceful_stop_returns_in_flight_work_to_queue() {
    let started = Arc::new(Event::default());
    let s = started.clone();
    let hook: GenerateHook = Arc::new(move |_p| {
        let s = s.clone();
        Box::pin(async move {
            s.set();
            tokio::time::sleep(Duration::from_secs(30)).await;
            Ok("{}".to_string())
        })
    });
    let fx = Fx::new();
    let cm = fx.make(Arc::new(FakeLlmClient { generate_hook: Some(hook), ..Default::default() }), |c| {
        c.worker.concurrency = 1
    });
    cm.add_message("g", "Ali", "I moved to Berlin in March.", MessageInput::default()).unwrap();
    cm.start().await.unwrap();
    tokio::time::timeout(Duration::from_secs(5), started.wait()).await.unwrap();
    cm.stop(0.2).await; // cancels the in-flight batch
    assert!(!cm.worker.running());
    assert_eq!(status(&cm, "pending"), 1);
    assert_eq!(jobs(&cm), vec![("queued".to_string(), 1)], "the lease is released immediately on graceful stop");
    cm.close().await;
}

#[tokio::test]
async fn test_maintenance_backfills_embeddings_and_merges_duplicates() {
    let fx = Fx::new();
    let cm = fx.make(Arc::new(scripted_fake_llm()), |c| c.worker.concurrency = 1);
    // two near-identical active memories of the same subject (as if produced by two chats at once)
    let t = "2026-01-01T00:00:00+00:00";
    let a = make_memory(
        "Ali has a cat called Luna.",
        "ali",
        "c1",
        t,
        Some(fake_embedding("Ali has a cat called Luna.", 256)),
        "fact",
        0.7,
    );
    let b = make_memory(
        "Ali has a cat called Luna!",
        "ali",
        "c1",
        "2026-02-01T00:00:00+00:00",
        Some(fake_embedding("Ali has a cat called Luna!", 256)),
        "fact",
        0.7,
    );
    let mut c = make_memory("Ali likes tea.", "ali", "c1", "2026-02-01T00:00:00+00:00", None, "fact", 0.7);
    c.embedding = None; // as if the embedding server was down when it was stored
    for m in [&a, &b, &c] {
        cm.store.insert_memory(m, &[], &["ali".into()]).unwrap();
    }
    let report = cm.maintain().await.unwrap();
    assert_eq!(report["embedded"], 1);
    assert_eq!(report["merged"], 1);
    let active: HashSet<String> =
        cm.memories(&MemoryQuery::default()).unwrap().into_iter().map(|m| m.memory_id).collect();
    assert_eq!(
        active,
        HashSet::from([b.memory_id.clone(), c.memory_id.clone()]),
        "the older duplicate is superseded by the newer one"
    );
    assert!(cm.store.get_memory(&c.memory_id, false).unwrap().unwrap().embedding.is_some());
    cm.close().await;
}

#[tokio::test]
async fn test_periodic_maintenance_job_is_enqueued_once_per_bucket() {
    let fx = Fx::new();
    let cm = fx.make(Arc::new(scripted_fake_llm()), |c| {
        c.worker.concurrency = 1;
        c.worker.maintenance_interval = 0.05;
    });
    cm.start().await.unwrap();
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert!(cm.wait_until_idle(Some(5.0)).await.unwrap());
    assert!(cm.worker.metrics().maintenance_runs >= 1);
    assert_eq!(cm.store.job_counts().unwrap().get("dead").copied().unwrap_or(0), 0);
    cm.close().await;
}
