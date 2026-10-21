//! Store-level tests: schema, idempotent ingestion, the durable job queue (per-chat ordering, cross-chat
//! parallelism, leases, backoff, dead-letter, restart recovery), atomic plan application with lease fencing,
//! memories/entities/relations/links, and keyword search. Ported from `test_chat_memory_store.py`.
//!
//! Every test writes to a temporary SQLite file (never the default path). No LLM is involved here.

mod common;

use common::{make_memory, mem};
use neuralgraph_memory::error::MemoryError;
use neuralgraph_memory::models::{iso, now_iso, utcnow};
use neuralgraph_memory::store::{
    AddMessage, ChannelFilter, ChatMemoryStore, ExtractionPlan, MemoryQuery, PlanEntity, PlanRelation,
};
use std::collections::HashSet;

struct Fixture {
    _dir: tempfile::TempDir,
    path: std::path::PathBuf,
    store: ChatMemoryStore,
}

fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("chat_memory_test.db");
    let store = ChatMemoryStore::open(path.to_str().unwrap()).unwrap();
    Fixture { _dir: dir, path, store }
}

fn add(store: &ChatMemoryStore, chat: &str, speaker: &str, text: &str) -> (neuralgraph_memory::ChatMessage, bool) {
    store.add_message(chat, speaker, text, &AddMessage::default()).unwrap()
}

fn add_at(
    store: &ChatMemoryStore,
    chat: &str,
    speaker: &str,
    text: &str,
    sent_at: &str,
) -> (neuralgraph_memory::ChatMessage, bool) {
    store.add_message(chat, speaker, text, &AddMessage { sent_at: Some(sent_at.into()), ..Default::default() }).unwrap()
}

fn counts(store: &ChatMemoryStore) -> Vec<(String, i64)> {
    store.job_counts().unwrap().into_iter().collect()
}

fn lease(store: &ChatMemoryStore, worker: &str, secs: f64, batch: i64) -> Vec<neuralgraph_memory::Job> {
    store.lease_job(worker, secs, None, batch, 0, 40).unwrap()
}

fn ids(jobs: &[neuralgraph_memory::Job]) -> Vec<String> {
    jobs.iter().map(|j| j.ref_id.clone().unwrap()).collect()
}

// ---------------------------------------------------------------------------------------- schema & messages

#[test]
fn test_schema_created_with_fts_and_wal() {
    let f = fixture();
    assert!(f.store.has_fts());
    let conn = rusqlite::Connection::open(&f.path).unwrap();
    let mut st = conn.prepare("SELECT name FROM sqlite_master WHERE type='table'").unwrap();
    let tables: HashSet<String> = st.query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
    for t in [
        "chats",
        "messages",
        "memories",
        "memory_sources",
        "entities",
        "entity_aliases",
        "memory_entities",
        "relations",
        "memory_links",
        "jobs",
        "audit_log",
        "meta",
    ] {
        assert!(tables.contains(t), "{t}");
    }
    let mode: String = conn.query_row("PRAGMA journal_mode", [], |r| r.get(0)).unwrap();
    assert_eq!(mode, "wal");
    assert_eq!(f.store.get_meta("schema_version").unwrap().as_deref(), Some("1"));
}

#[test]
fn test_add_message_assigns_sequence_and_enqueues() {
    let f = fixture();
    let (m1, c1) = add_at(&f.store, "chat", "Ali", "first", "2026-01-01T10:00:00+00:00");
    let (m2, c2) = add(&f.store, "chat", "Mel", "second");
    assert!(c1 && c2);
    assert_eq!((m1.seq, m2.seq), (0, 1));
    assert_eq!(m1.status, "pending");
    assert_eq!(counts(&f.store), vec![("queued".to_string(), 2)]);
    let chats = f.store.list_chats(100, 0).unwrap();
    assert_eq!(chats[0]["message_count"], 2);
    let mut parts: Vec<String> =
        chats[0]["participants"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
    parts.sort();
    assert_eq!(parts, ["Ali", "Mel"]);
}

#[test]
fn test_add_message_is_idempotent() {
    let f = fixture();
    let (m1, c1) = add_at(&f.store, "chat", "Ali", "same text", "2026-01-01T10:00:00+00:00");
    let (m2, c2) = add_at(&f.store, "chat", "Ali", "same text", "2026-01-01T10:00:00+00:00");
    assert!(c1);
    assert!(!c2);
    assert_eq!(m1.message_id, m2.message_id);
    assert_eq!(counts(&f.store), vec![("queued".to_string(), 1)]);
    // a person really repeating themselves is NOT a duplicate once something else was said in between
    let (yes1, c1) = add(&f.store, "chat", "Ali", "yes");
    add(&f.store, "chat", "Mel", "are you sure?");
    let (yes2, c2) = add(&f.store, "chat", "Ali", "yes");
    assert!(c1 && c2);
    assert_ne!(yes1.message_id, yes2.message_id);
    // but an immediate resend of the last message (client retry) is
    let (yes3, c3) = add(&f.store, "chat", "Ali", "yes");
    assert!(!c3);
    assert_eq!(yes3.message_id, yes2.message_id);
    // explicit id is honoured and also idempotent
    let with_id = AddMessage { message_id: Some("m-42".into()), ..Default::default() };
    let (m3, c3) = f.store.add_message("chat", "Ali", "with id", &with_id).unwrap();
    let (_m4, c4) = f.store.add_message("chat", "Ali", "with id", &with_id).unwrap();
    assert_eq!(m3.message_id, "m-42");
    assert!(c3);
    assert!(!c4);
}

#[test]
fn test_context_before_and_search_messages() {
    let f = fixture();
    for i in 0..5 {
        let text = if i == 3 { format!("message number {i} about Berlin") } else { format!("message number {i}") };
        add(&f.store, "chat", "Ali", &text);
    }
    let ctx = f.store.context_before("chat", 4, 2).unwrap();
    assert_eq!(ctx.iter().map(|m| m.seq).collect::<Vec<_>>(), [2, 3]);
    let hits = f.store.search_messages("berlin", 20, None).unwrap();
    assert_eq!(hits.iter().map(|(m, _)| m.seq).collect::<Vec<_>>(), [3]);
}

#[test]
fn test_persistence_across_reopen() {
    let f = fixture();
    add(&f.store, "chat", "Ali", "durable");
    f.store.close();
    let reopened = ChatMemoryStore::open(f.path.to_str().unwrap()).unwrap();
    let msgs = reopened.get_messages("chat", None, None, None, false).unwrap();
    assert_eq!(msgs.iter().map(|m| m.text.as_str()).collect::<Vec<_>>(), ["durable"]);
    assert_eq!(counts(&reopened), vec![("queued".to_string(), 1)]);
}

// ---------------------------------------------------------------------------------------- job queue

#[test]
fn test_per_chat_ordering_and_cross_chat_parallelism() {
    let f = fixture();
    let (a0, _) = add(&f.store, "A", "x", "a0");
    let (a1, _) = add(&f.store, "A", "x", "a1");
    let (b0, _) = add(&f.store, "B", "x", "b0");
    let first = lease(&f.store, "w1", 60.0, 1);
    let second = lease(&f.store, "w2", 60.0, 1);
    let third = lease(&f.store, "w3", 60.0, 1);
    assert_eq!(ids(&first), std::slice::from_ref(&a0.message_id));
    assert_eq!(ids(&second), [b0.message_id], "a1 must wait for a0; B runs in parallel");
    assert!(third.is_empty());
    f.store.complete_jobs(&[first[0].job_id]).unwrap();
    let fourth = lease(&f.store, "w1", 60.0, 1);
    assert_eq!(ids(&fourth), [a1.message_id]);
}

#[test]
fn test_retry_halves_the_batch() {
    let f = fixture();
    let all: Vec<String> = (0..4).map(|i| add(&f.store, "A", "x", &format!("m{i}")).0.message_id).collect();
    let jobs = lease(&f.store, "w", 60.0, 4);
    assert_eq!(jobs.len(), 4);
    f.store.fail_jobs(&jobs.iter().map(|j| j.job_id).collect::<Vec<_>>(), "boom", 0.0, None).unwrap();
    let jobs = lease(&f.store, "w", 60.0, 4);
    assert_eq!(ids(&jobs), all[..2], "second attempt: half the batch");
    f.store.fail_jobs(&jobs.iter().map(|j| j.job_id).collect::<Vec<_>>(), "boom", 0.0, None).unwrap();
    let jobs = lease(&f.store, "w", 60.0, 4);
    assert_eq!(ids(&jobs), all[..1], "third attempt: single message");
}

#[test]
fn test_batch_lease_coalesces_contiguous_messages_only() {
    let f = fixture();
    let all: Vec<String> = (0..4).map(|i| add(&f.store, "A", "x", &format!("m{i}")).0.message_id).collect();
    // make m2 unavailable (future) so the batch must stop at the gap
    let future = iso(utcnow() + chrono::TimeDelta::seconds(60));
    f.store
        .with_conn(|c| {
            Ok(c.execute("UPDATE jobs SET available_at=? WHERE ref_id=?", [future.as_str(), all[2].as_str()])?)
        })
        .unwrap();
    let jobs = lease(&f.store, "w", 60.0, 10);
    assert_eq!(ids(&jobs), all[..2]);
    assert!(jobs.iter().all(|j| j.status == "leased" && j.attempts == 1));
}

#[test]
fn test_failure_backoff_then_dead_letter() {
    let f = fixture();
    let (m, _) = f.store.add_message("A", "x", "m0", &AddMessage { max_attempts: 2, ..Default::default() }).unwrap();
    let jobs = lease(&f.store, "w", 60.0, 1);
    let result = f.store.fail_jobs(&[jobs[0].job_id], "boom", 30.0, None).unwrap();
    assert_eq!(result.get(&jobs[0].job_id).map(String::as_str), Some("queued"));
    assert!(lease(&f.store, "w", 60.0, 1).is_empty(), "backing-off job must not be leasable yet");
    let now = now_iso();
    let id = jobs[0].job_id;
    f.store
        .with_conn(|c| Ok(c.execute("UPDATE jobs SET available_at=? WHERE job_id=?", rusqlite::params![now, id])?))
        .unwrap();
    let jobs = lease(&f.store, "w", 60.0, 1);
    assert_eq!(jobs[0].attempts, 2);
    let result = f.store.fail_jobs(&[jobs[0].job_id], "boom again", 30.0, None).unwrap();
    assert_eq!(result.get(&jobs[0].job_id).map(String::as_str), Some("dead"));
    assert_eq!(f.store.get_message(&m.message_id).unwrap().unwrap().status, "failed");
    assert_eq!(counts(&f.store), vec![("dead".to_string(), 1)]);
    // a dead job does not block later messages of the chat
    let (m1, _) = add(&f.store, "A", "x", "m1");
    assert_eq!(ids(&lease(&f.store, "w", 60.0, 1)), [m1.message_id]);
    // and can be retried by an operator
    assert_eq!(f.store.retry_dead_jobs(1000).unwrap(), 1);
    assert_eq!(f.store.get_message(&m.message_id).unwrap().unwrap().status, "pending");
}

#[test]
fn test_expired_lease_is_requeued() {
    let f = fixture();
    let (m, _) = add(&f.store, "A", "x", "m0");
    let jobs = lease(&f.store, "w", -1.0, 1); // already expired
    f.store.set_message_status(std::slice::from_ref(&m.message_id), "processing").unwrap();
    assert_eq!(f.store.requeue_expired_leases().unwrap(), 1);
    assert_eq!(f.store.get_message(&m.message_id).unwrap().unwrap().status, "pending");
    let again = lease(&f.store, "w2", 60.0, 1);
    assert_eq!(again[0].job_id, jobs[0].job_id);
    assert_eq!(again[0].attempts, 2, "an expired lease counts as an attempt");
}

#[test]
fn test_enqueue_dedupe_key() {
    let f = fixture();
    let j1 = f.store.enqueue_job("maintain", None, None, None, Some("maintain:1"), None, 5, None).unwrap();
    let j2 = f.store.enqueue_job("maintain", None, None, None, Some("maintain:1"), None, 5, None).unwrap();
    assert!(j1.is_some());
    assert!(j2.is_none());
    assert_eq!(f.store.pending_jobs(None).unwrap(), 1);
}

// ---------------------------------------------------------------------------------------- plan application

#[test]
fn test_apply_plan_is_atomic_and_marks_messages_and_jobs() {
    let f = fixture();
    let (m, _) = add_at(&f.store, "c1", "Ali", "I have a cat called Luna", "2026-01-01T10:00:00+00:00");
    let jobs = lease(&f.store, "w", 60.0, 1);
    let mut plan = ExtractionPlan::new("c1", &jobs.iter().map(|j| j.job_id).collect::<Vec<_>>());
    plan.entities.push(PlanEntity {
        etype: "person".into(),
        seen_at: m.sent_at.clone(),
        aliases: vec!["ali n".into()],
        ..PlanEntity::new("ali", "Ali")
    });
    plan.entities.push(PlanEntity { etype: "pet".into(), ..PlanEntity::new("luna", "Luna") });
    let memory = mem("Ali has a cat called Luna.");
    plan.new_memories.push((memory.clone(), vec![m.message_id.clone()], vec!["ali".into(), "luna".into()]));
    plan.relations.push(PlanRelation {
        subject: "ali".into(),
        predicate: "has_pet".into(),
        object: "luna".into(),
        confidence: 0.9,
        memory_id: Some(memory.memory_id.clone()),
        message_id: Some(m.message_id.clone()),
        observed_at: None,
    });
    plan.processed_message_ids.push(m.message_id.clone());
    let c = f.store.apply_plan(&plan, Some("w")).unwrap();
    assert_eq!(c.memories_added, 1);
    assert_eq!(c.relations, 1);
    assert_eq!(f.store.get_message(&m.message_id).unwrap().unwrap().status, "processed");
    assert_eq!(counts(&f.store), vec![("done".to_string(), 1)]);
    let stored = f.store.get_memory(&memory.memory_id, true).unwrap().unwrap();
    assert_eq!(stored.source_message_ids, [m.message_id]);
    assert_eq!(stored.entity_ids, ["ali", "luna"]);
    assert_eq!(f.store.resolve_alias("Ali N").unwrap().as_deref(), Some("ali"));
    let n = f.store.neighbors(&["ali".into()], 100).unwrap();
    assert_eq!(n["ali"], vec![("luna".to_string(), "has_pet".to_string(), 0.9)]);
}

#[test]
fn test_apply_plan_is_fenced_by_lease() {
    let f = fixture();
    let (m, _) = add(&f.store, "c1", "Ali", "hello world facts");
    let jobs = lease(&f.store, "zombie", -1.0, 1);
    f.store.requeue_expired_leases().unwrap();
    lease(&f.store, "fresh", 60.0, 1); // someone else owns it now
    let mut plan = ExtractionPlan::new("c1", &[jobs[0].job_id]);
    plan.new_memories.push((mem("Ali says hello world."), vec![m.message_id], vec!["ali".into()]));
    assert!(matches!(f.store.apply_plan(&plan, Some("zombie")), Err(MemoryError::LostLease(_))));
    assert!(f.store.list_memories(&MemoryQuery::default()).unwrap().is_empty(), "fenced commit must write nothing");
}

#[test]
fn test_apply_plan_rolls_back_on_error() {
    let f = fixture();
    let (m, _) = add(&f.store, "c1", "Ali", "x");
    let mut plan = ExtractionPlan::new("c1", &[]);
    let good = mem("Ali likes tea.");
    plan.new_memories.push((good.clone(), vec![m.message_id.clone()], vec!["ali".into()]));
    plan.new_memories.push((good, vec![m.message_id], vec!["ali".into()])); // duplicate primary key
    let err = f.store.apply_plan(&plan, None).unwrap_err();
    assert_eq!(err.type_name(), "IntegrityError");
    assert!(f.store.list_memories(&MemoryQuery::default()).unwrap().is_empty());
}

#[test]
fn test_supersede_and_history() {
    let f = fixture();
    let old = make_memory("Ali lives in Berlin.", "ali", "c1", "2026-01-01T00:00:00+00:00", None, "fact", 0.7);
    let new = make_memory("Ali lives in Tokyo.", "ali", "c1", "2026-06-01T00:00:00+00:00", None, "fact", 0.7);
    f.store.insert_memory(&old, &[], &["ali".into()]).unwrap();
    f.store.upsert_relation("ali", "lives_in", "berlin", 0.6, "c1", Some(&old.memory_id), None, None).unwrap();
    f.store.insert_memory(&new, &[], &["ali".into()]).unwrap();
    f.store.supersede(&old.memory_id, &new.memory_id, "contradicts").unwrap();
    let active: Vec<String> =
        f.store.list_memories(&MemoryQuery::default()).unwrap().into_iter().map(|m| m.memory_id).collect();
    assert_eq!(active, std::slice::from_ref(&new.memory_id));
    let stale = f.store.get_memory(&old.memory_id, false).unwrap().unwrap();
    assert_eq!((stale.status.as_str(), stale.superseded_by.as_deref()), ("superseded", Some(new.memory_id.as_str())));
    let hist: Vec<String> = f.store.history(&new.memory_id, 20).unwrap().into_iter().map(|m| m.memory_id).collect();
    assert_eq!(hist, [old.memory_id]);
    assert_eq!(
        f.store.relation_counts().unwrap().into_iter().collect::<Vec<_>>(),
        vec![("superseded".to_string(), 1)],
        "relations grounded only in the old memory follow it"
    );
}

#[test]
fn test_functional_predicate_supersession() {
    let f = fixture();
    let s = &f.store;
    s.upsert_relation("ali", "lives_in", "berlin", 0.6, "c1", None, None, Some("2026-01-01T00:00:00+00:00")).unwrap();
    s.upsert_relation("ali", "likes", "tea", 0.6, "c1", None, None, None).unwrap();
    s.upsert_relation("ali", "likes", "coffee", 0.6, "c1", None, None, None).unwrap();
    s.upsert_relation("ali", "lives_in", "tokyo", 0.6, "c2", None, None, Some("2026-06-01T00:00:00+00:00")).unwrap();
    let active: HashSet<(String, String)> =
        s.relations_for("ali", Some("active"), 200).unwrap().into_iter().map(|r| (r.predicate, r.object_id)).collect();
    let want: HashSet<(String, String)> = [("lives_in", "tokyo"), ("likes", "tea"), ("likes", "coffee")]
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
    assert_eq!(active, want);
    let again = s
        .upsert_relation("ali", "lives_in", "tokyo", 0.6, "c3", None, None, Some("2026-07-01T00:00:00+00:00"))
        .unwrap();
    assert_eq!(again.observation_count, 2);
    // an OLDER observation ingested later (bulk import of an old chat) must not replace the current value
    let older = s
        .upsert_relation("ali", "lives_in", "paris", 0.6, "old", None, None, Some("2020-01-01T00:00:00+00:00"))
        .unwrap();
    assert_eq!(older.status, "superseded");
    let active: HashSet<(String, String)> =
        s.relations_for("ali", Some("active"), 200).unwrap().into_iter().map(|r| (r.predicate, r.object_id)).collect();
    assert!(active.contains(&("lives_in".into(), "tokyo".into())));
    assert!(!active.contains(&("lives_in".into(), "paris".into())));
}

#[test]
fn test_keyword_candidates_and_retract() {
    let f = fixture();
    let a = mem("Ali adopted a golden retriever named Bailey.");
    let b =
        make_memory("Mel visited the Louvre in Paris.", "mel", "c1", "2026-01-01T00:00:00+00:00", None, "fact", 0.7);
    f.store.insert_memory(&a, &[], &[]).unwrap();
    f.store.insert_memory(&b, &[], &[]).unwrap();
    let hits = f.store.keyword_candidates("Bailey retriever", 50, "active", &ChannelFilter::default()).unwrap();
    assert_eq!(hits.iter().map(|h| h.0.clone()).collect::<Vec<_>>(), std::slice::from_ref(&a.memory_id));
    assert!(f.store.retract(&a.memory_id, "test").unwrap());
    assert!(f
        .store
        .keyword_candidates("Bailey retriever", 50, "active", &ChannelFilter::default())
        .unwrap()
        .is_empty());
    assert_eq!(f.store.recent_events(1, None).unwrap()[0]["kind"], "retract");
}

#[test]
fn test_list_memories_filters() {
    let f = fixture();
    let s = &f.store;
    s.insert_memory(
        &make_memory("Ali ran a marathon.", "ali", "c1", "2026-03-01T00:00:00+00:00", None, "event", 0.7),
        &[],
        &[],
    )
    .unwrap();
    s.insert_memory(
        &make_memory("Ali likes tea.", "ali", "c1", "2025-01-01T00:00:00+00:00", None, "preference", 0.7),
        &[],
        &[],
    )
    .unwrap();
    s.insert_memory(
        &make_memory("Mel likes pottery.", "mel", "c2", "2026-01-01T00:00:00+00:00", None, "preference", 0.7),
        &[],
        &[],
    )
    .unwrap();
    let n = |q: MemoryQuery| s.list_memories(&q).unwrap().len();
    assert_eq!(n(MemoryQuery { subject: Some("Ali".into()), ..Default::default() }), 2);
    assert_eq!(n(MemoryQuery { kinds: Some(vec!["preference".into()]), ..Default::default() }), 2);
    assert_eq!(n(MemoryQuery { since: Some("2026-01-01".into()), ..Default::default() }), 2);
    assert_eq!(n(MemoryQuery { chat_id: Some("c2".into()), ..Default::default() }), 1);
    let bad = s.list_memories(&MemoryQuery { order: "text; DROP TABLE memories".into(), ..Default::default() });
    assert!(matches!(bad, Err(MemoryError::Value(_))));
}
