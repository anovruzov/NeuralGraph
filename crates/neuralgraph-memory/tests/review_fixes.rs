//! Regression tests for the defects found by the adversarial review of the memory system. Each test names the
//! defect it pins. All deterministic; no model server. Ported from `test_chat_memory_review_fixes.py`.

mod common;

use axum::body::Body;
use axum::http::Request;
use common::make_memory;
use http_body_util::BodyExt;
use neuralgraph_memory::clock;
use neuralgraph_memory::extraction::{older, parse_when, EntityRegistry, ExtractionConfig, MemoryExtractor};
use neuralgraph_memory::llm::{fake_embedding, FakeLlmClient};
use neuralgraph_memory::mcp::{serve_stdio_with, McpProtocol};
use neuralgraph_memory::retrieval::{MemoryRetriever, RetrievalConfig, SearchOptions};
use neuralgraph_memory::server::{create_app, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, MessageInput};
use neuralgraph_memory::store::{AddMessage, ChannelFilter, ChatMemoryStore, ExtractionPlan, MemoryQuery};
use neuralgraph_memory::testing::scripted_fake_llm;
use neuralgraph_memory::textutil::{fts_match_string, tokenize};
use neuralgraph_memory::{ChatMessage, Memory};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::io::AsyncReadExt;
use tower::ServiceExt;

fn llm_with(memories: Value, relations: Option<Value>, reconcile: Option<Value>) -> Arc<FakeLlmClient> {
    Arc::new(FakeLlmClient::with_script(vec![
        ("You maintain the long-term memory", memories.to_string()),
        ("You are building a knowledge graph", relations.unwrap_or(json!({"entities": [], "triples": []})).to_string()),
        ("You maintain a memory store", reconcile.unwrap_or(json!({"decision": "ADD", "target": null})).to_string()),
    ]))
}

struct Base {
    dir: tempfile::TempDir,
    store: Arc<ChatMemoryStore>,
}

fn base() -> Base {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(ChatMemoryStore::open(dir.path().join("fix.db").to_str().unwrap()).unwrap());
    Base { dir, store }
}

impl Base {
    fn db(&self) -> String {
        self.dir.path().join("fix.db").to_str().unwrap().to_string()
    }
}

fn add(
    store: &ChatMemoryStore,
    chat: &str,
    speaker: &str,
    text: &str,
    role: &str,
    sent_at: Option<&str>,
) -> ChatMessage {
    store
        .add_message(
            chat,
            speaker,
            text,
            &AddMessage { role: role.into(), sent_at: sent_at.map(str::to_string), ..Default::default() },
        )
        .unwrap()
        .0
}

fn reasons(plan: &ExtractionPlan) -> Vec<String> {
    plan.audit
        .iter()
        .filter(|(k, _, _)| k == "reject")
        .map(|(_, _, d)| d["reason"].as_str().unwrap_or_default().to_string())
        .collect()
}

async fn plan_for(
    store: &Arc<ChatMemoryStore>,
    llm: Arc<FakeLlmClient>,
    cfg: ExtractionConfig,
    batch: &[ChatMessage],
) -> ExtractionPlan {
    MemoryExtractor::new(store.clone(), llm, cfg).build_plan(batch, &[], 1).await.unwrap()
}

const AT: &str = "2026-05-02T10:00:00+00:00";

// ---------------------------------------------------------------------------------------- extraction grounding

#[tokio::test]
async fn test_hallucinated_name_listed_in_entities_is_still_rejected() {
    let b = base();
    let m = add(&b.store, "c", "Ali", "I met someone interesting at the market yesterday.", "", Some(AT));
    let llm = llm_with(
        json!({"memories": [{"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali",
                             "importance": 0.6, "confidence": 0.9, "sources": [1],
                             "entities": [{"name": "Zorblax Quintrell", "type": "person"}]}]}),
        None,
        None,
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig::default(), &[m]).await;
    assert!(plan.new_memories.is_empty());
    assert!(reasons(&plan).contains(&"unknown_name".to_string()));
    assert!(plan.entities.iter().all(|e| !e.entity_id.contains("zorblax")));
}

#[tokio::test]
async fn test_relation_endpoints_must_be_grounded() {
    let b = base();
    let m = add(&b.store, "c", "Ali", "I work at Charite as a nurse.", "", Some(AT));
    let rel = json!({"entities": [{"name": "Charite", "type": "org"}, {"name": "Globex Corp", "type": "org"}],
                     "triples": [{"s": "I", "r": "works_at", "o": "Charite", "source": 1, "confidence": 0.9},
                                 {"s": "I", "r": "friend_of", "o": "Bob Zimmer", "source": 1, "confidence": 0.9}]});
    let plan =
        plan_for(&b.store, llm_with(json!({"memories": []}), Some(rel), None), ExtractionConfig::default(), &[m]).await;
    let rels: Vec<(String, String, String)> =
        plan.relations.iter().map(|r| (r.subject.clone(), r.predicate.clone(), r.object.clone())).collect();
    assert_eq!(rels, [("ali".to_string(), "works_at".to_string(), "charite".to_string())]);
    assert!(plan.entities.iter().all(|e| e.entity_id != "globex corp"));
}

#[tokio::test]
async fn test_short_identity_facts_pass_grounding() {
    let b = base();
    let m = add(&b.store, "c", "Ali", "I'm 34 and I have 2 kids.", "", Some(AT));
    let llm = llm_with(
        json!({"memories": [
            {"text": "Ali is 34 years old.", "kind": "identity", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]},
            {"text": "Ali has 2 kids.", "kind": "fact", "subject": "Ali", "importance": 0.9, "confidence": 1.0, "sources": [1]}]}),
        None,
        None,
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig::default(), &[m]).await;
    let mut texts: Vec<String> = plan.new_memories.iter().map(|(x, _, _)| x.text.clone()).collect();
    texts.sort();
    assert_eq!(texts, ["Ali has 2 kids.", "Ali is 34 years old."]);
}

#[test]
fn test_longer_name_never_collapses_onto_speaker() {
    let mut r = EntityRegistry::default();
    r.add("alex", "Alex", "person", 5, &[]);
    assert_eq!(r.resolve_str("Alex Jones", None, "person", true).as_deref(), Some("alex jones"));
    r.add("melanie carter", "Melanie Carter", "person", 5, &[]);
    assert_eq!(r.resolve_str("Melanie", None, "", true).as_deref(), Some("melanie carter"));
}

#[tokio::test]
async fn test_assistant_subject_is_rejected_not_reattributed() {
    let b = base();
    let m = add(
        &b.store,
        "c",
        "Ali",
        "You're really helpful and patient, thank you so much for the detailed answer.",
        "",
        Some(AT),
    );
    let llm = llm_with(
        json!({"memories": [{"text": "The assistant is helpful and patient.", "kind": "opinion", "subject": "assistant",
                             "importance": 0.6, "confidence": 0.9, "sources": [1]}]}),
        None,
        None,
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig::default(), &[m]).await;
    assert!(plan.new_memories.is_empty());
    assert!(reasons(&plan).contains(&"assistant_subject".to_string()));
}

#[tokio::test]
async fn test_missing_sources_default_to_a_human_message() {
    let b = base();
    let m1 = add(&b.store, "c", "Ali", "I adopted a dog called Rex last week.", "", Some(AT));
    let m2 = add(
        &b.store,
        "c",
        "assistant",
        "Congratulations on adopting Rex!",
        "assistant",
        Some("2026-05-02T10:00:10+00:00"),
    );
    let llm = llm_with(
        json!({"memories": [{"text": "Ali adopted a dog called Rex.", "kind": "event", "subject": "Ali", "importance": 0.7, "confidence": 0.9}]}),
        None,
        None,
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig::default(), &[m1.clone(), m2]).await;
    assert_eq!(plan.new_memories.len(), 1);
    assert_eq!(plan.new_memories[0].1, [m1.message_id]);
}

#[tokio::test]
async fn test_backslash_in_subject_does_not_crash() {
    let b = base();
    let m = add(&b.store, "c", "Ali", "I love hiking in the Alps every summer.", "", Some(AT));
    let llm = llm_with(
        json!({"memories": [{"text": "I love hiking in the Alps.", "kind": "preference", "subject": "Ali\\u00e9", "importance": 0.7, "confidence": 0.9, "sources": [1]}]}),
        None,
        None,
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig::default(), &[m]).await; // must not fail on the replacement
    assert!(plan.new_memories.len() <= 1);
}

#[test]
fn test_year_relative_phrases() {
    assert_eq!(parse_when("last year", Some("2026-09-17T10:00:00+00:00")), (Some("2025".into()), "year".into()));
    assert_eq!(parse_when("this year", Some("2026-09-17T10:00:00+00:00")), (Some("2026".into()), "year".into()));
}

// ---------------------------------------------------------------------------------------- reconcile

async fn seed_cat(store: &ChatMemoryStore) -> String {
    let mut m = make_memory(
        "Ali has a cat called Luna.",
        "ali",
        "c1",
        "2026-03-01T00:00:00+00:00",
        Some(fake_embedding("Ali has a cat called Luna.", 256)),
        "fact",
        0.7,
    );
    m.event_time = Some("2026-02".into());
    m.event_time_precision = "month".into();
    let m0 = add(store, "c0", "Ali", "I have a cat called Luna.", "", Some("2026-03-01T00:00:00+00:00"));
    store.insert_memory(&m, &[m0.message_id], &["ali".into(), "luna".into()]).unwrap();
    m.memory_id
}

#[tokio::test]
async fn test_update_keeps_target_metadata_and_reembeds_merged_text() {
    let b = base();
    seed_cat(&b.store).await;
    let m = add(&b.store, "c1", "Ali", "Luna is a grey British shorthair.", "", Some("2026-05-01T00:00:00+00:00"));
    let llm = llm_with(
        json!({"memories": [{"text": "Ali's cat Luna is a grey British shorthair.", "kind": "other", "subject": "Ali", "importance": 0.5, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]}),
        None,
        Some(json!({"decision": "UPDATE", "target": "A", "text": "Ali has a grey British shorthair cat called Luna."})),
    );
    let plan = plan_for(&b.store, llm, ExtractionConfig { reconcile_cosine: 0.2, ..Default::default() }, &[m]).await;
    let (mem, sources, ents) = &plan.new_memories[0];
    assert_eq!(mem.text, "Ali has a grey British shorthair cat called Luna.");
    assert_eq!((mem.event_time.as_deref(), mem.kind.as_str(), mem.importance), (Some("2026-02"), "fact", 0.7));
    assert_eq!(sources.len(), 2, "sources of the old memory are carried over");
    assert!(ents.contains(&"luna".to_string()));
    assert_eq!(mem.embedding.as_ref().unwrap(), &fake_embedding(&mem.text, 256), "embedding is of the merged text");
}

#[tokio::test]
async fn test_two_candidates_superseding_one_target_chain() {
    let b = base();
    seed_cat(&b.store).await;
    let m = add(
        &b.store,
        "c1",
        "Ali",
        "Actually Luna is 4, and she is a British shorthair.",
        "",
        Some("2026-05-01T00:00:00+00:00"),
    );
    let llm = llm_with(
        json!({"memories": [
            {"text": "Ali's cat Luna is 4 years old.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]},
            {"text": "Ali's cat Luna is a British shorthair.", "kind": "fact", "subject": "Ali", "importance": 0.7, "confidence": 0.9, "sources": [1]}]}),
        None,
        Some(json!({"decision": "UPDATE", "target": "A", "text": null})),
    );
    let cfg = ExtractionConfig { reconcile_cosine: 0.2, dedupe_cosine: 0.99, ..Default::default() };
    let plan = plan_for(&b.store, llm, cfg, &[m]).await;
    b.store.apply_plan(&plan, None).unwrap();
    let active = b.store.list_memories(&MemoryQuery { subject: Some("ali".into()), ..Default::default() }).unwrap();
    assert_eq!(active.len(), 1, "only the newest replacement stays active");
    assert_eq!(b.store.history(&active[0].memory_id, 20).unwrap().len(), 2);
}

#[test]
fn test_mixed_timezone_offsets_compare_correctly() {
    assert!(!older(Some("2026-05-01T12:00:00+02:00"), Some("2026-05-01T09:00:00+00:00"))); // 10:00Z vs 09:00Z: newer
    assert!(older(Some("2026-05-01T08:00:00+02:00"), Some("2026-05-01T09:00:00+00:00")));
    // 06:00Z vs 09:00Z: older
}

// ---------------------------------------------------------------------------------------- queue

#[tokio::test]
async fn test_fail_and_release_are_fenced() {
    let b = base();
    b.store.add_message("A", "x", "hello world facts", &AddMessage { max_attempts: 2, ..Default::default() }).unwrap();
    let zombie = b.store.lease_job("zombie", -1.0, None, 1, 0, 40).unwrap();
    b.store.requeue_expired_leases().unwrap();
    let live = b.store.lease_job("live", 60.0, None, 1, 0, 40).unwrap();
    let r = b.store.fail_jobs(&[zombie[0].job_id], "boom", 0.0, Some("zombie")).unwrap();
    assert_eq!(r.get(&zombie[0].job_id).map(String::as_str), Some("lost"));
    assert_eq!(
        b.store.get_job(live[0].job_id).unwrap().unwrap().status,
        "leased",
        "the live worker's lease is untouched"
    );
    b.store.release_jobs(&[zombie[0].job_id], Some("zombie")).unwrap();
    assert_eq!(b.store.get_job(live[0].job_id).unwrap().unwrap().status, "leased");
}

#[tokio::test]
async fn test_pull_forward_debounce_batches_turn_and_reply() {
    let b = base();
    b.store
        .add_message("t", "Ali", "I moved to Berlin.", &AddMessage { debounce_seconds: 5.0, ..Default::default() })
        .unwrap();
    let reply = AddMessage { role: "assistant".into(), debounce_seconds: 5.0, ..Default::default() };
    b.store.add_message("t", "assistant", "Nice!", &reply).unwrap();
    let (j1, j2) = (b.store.get_job(1).unwrap().unwrap(), b.store.get_job(2).unwrap().unwrap());
    assert_eq!(j1.available_at, j2.available_at, "the earlier job waits for the reply");
}

#[tokio::test]
async fn test_drain_recovers_a_lease_left_by_a_crashed_process() {
    let b = base();
    b.store.add_message("r", "Ali", "I live in Berlin with my cat Luna.", &AddMessage::default()).unwrap();
    b.store.lease_job("crashed", -1.0, None, 1, 0, 40).unwrap();
    b.store.close();
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    cfg.worker.poll_interval = 0.01;
    let cm = ChatMemory::open(&b.db(), Some(Arc::new(scripted_fake_llm())), cfg).unwrap();
    let n = tokio::time::timeout(std::time::Duration::from_secs(10), cm.process_pending(None)).await.unwrap().unwrap();
    assert_eq!(n, 1);
    assert_eq!(cm.store.job_counts().unwrap().into_iter().collect::<Vec<_>>(), vec![("done".to_string(), 1)]);
    cm.close().await;
}

#[tokio::test]
async fn test_supersede_race_keeps_newest_head() {
    let b = base();
    let at = |text: &str, when: &str| make_memory(text, "ali", "c1", when, None, "fact", 0.7);
    let t = at("Ali lives in Berlin.", "2026-01-01T00:00:00+00:00");
    b.store.insert_memory(&t, &[], &["ali".into()]).unwrap();
    // plan A (computed first, committed first): newer fact
    let a = at("Ali moved to Paris.", "2026-06-01T00:00:00+00:00");
    let mut pa = ExtractionPlan::new("A", &[]);
    pa.new_memories.push((a.clone(), vec![], vec!["ali".into()]));
    pa.supersedes.push((t.memory_id.clone(), a.memory_id.clone(), "contradicts".into()));
    // plan B (computed concurrently against the same snapshot, committed second): older refinement
    let bm = at("Ali lives in Berlin, Kreuzberg.", "2026-03-01T00:00:00+00:00");
    let mut pb = ExtractionPlan::new("B", &[]);
    pb.new_memories.push((bm.clone(), vec![], vec!["ali".into()]));
    pb.supersedes.push((t.memory_id.clone(), bm.memory_id.clone(), "supersedes".into()));
    b.store.apply_plan(&pa, None).unwrap();
    b.store.apply_plan(&pb, None).unwrap();
    let active: Vec<String> = b
        .store
        .list_memories(&MemoryQuery { subject: Some("ali".into()), ..Default::default() })
        .unwrap()
        .into_iter()
        .map(|m| m.memory_id)
        .collect();
    assert_eq!(active, std::slice::from_ref(&a.memory_id), "the newest observation stays the single active head");
    assert_eq!(b.store.get_memory(&bm.memory_id, false).unwrap().unwrap().superseded_by, Some(a.memory_id));
}

#[tokio::test]
async fn test_retracted_memories_never_resurface() {
    let b = base();
    let old = make_memory("Ali lived in Berlin.", "ali", "c1", "2026-01-01T00:00:00+00:00", None, "fact", 0.7);
    let new = make_memory("Ali lives in Tokyo.", "ali", "c1", "2026-06-01T00:00:00+00:00", None, "fact", 0.7);
    b.store.insert_memory(&old, &[], &["ali".into()]).unwrap();
    b.store.insert_memory(&new, &[], &["ali".into()]).unwrap();
    b.store.supersede(&old.memory_id, &new.memory_id, "supersedes").unwrap();
    b.store.retract(&old.memory_id, "forget").unwrap();
    assert!(b.store.history(&new.memory_id, 20).unwrap().is_empty());
    let ret = MemoryRetriever::new(b.store.clone(), Arc::new(FakeLlmClient::default()), RetrievalConfig::default());
    assert!(ret.related(&new.memory_id).unwrap().is_empty());
}

// ---------------------------------------------------------------------------------------- retrieval

struct Ret {
    b: Base,
    ret: MemoryRetriever,
    mel: Memory,
    trip: Memory,
    joerg: Memory,
}

fn emb(text: &str, subject: &str, embed_text: &str, kind: &str) -> Memory {
    make_memory(text, subject, "c1", "2026-01-01T00:00:00+00:00", Some(fake_embedding(embed_text, 256)), kind, 0.7)
}

fn retrieval_fixture() -> Ret {
    let b = base();
    let ret = MemoryRetriever::new(b.store.clone(), Arc::new(FakeLlmClient::default()), RetrievalConfig::default());
    let mel = emb("Mel works at a pottery studio in Tokyo.", "mel", "Mel works at a pottery studio in Tokyo.", "fact");
    b.store.insert_memory(&mel, &[], &["mel".into(), "tokyo".into()]).unwrap();
    b.store.upsert_entity("mel", "Mel", "person", None, 1, &["melanie".into()]).unwrap();
    let mut trip =
        emb("Mel plans to visit Berlin in October 2026.", "mel", "Mel plans to visit Berlin in October 2026.", "plan");
    trip.event_time = Some("2026-10".into());
    trip.event_time_precision = "month".into();
    b.store.insert_memory(&trip, &[], &["mel".into(), "berlin".into()]).unwrap();
    let joerg =
        emb("Ali's brother Jörg Müller lives in Zürich.", "ali", "Ali's brother Jörg Müller lives in Zürich.", "fact");
    b.store.insert_memory(&joerg, &[], &["ali".into(), "jorg muller".into(), "zurich".into()]).unwrap();
    Ret { b, ret, mel, trip, joerg }
}

async fn ids(ret: &MemoryRetriever, q: &str, opts: SearchOptions) -> Vec<String> {
    ret.search(q, &opts).await.unwrap().into_iter().map(|h| h.memory.memory_id).collect()
}

#[tokio::test]
async fn test_possessive_query_matches_entity_and_keyword() {
    let r = retrieval_fixture();
    assert_eq!(r.ret.query_entities("What is Mel's job?").unwrap(), ["mel"]);
    assert_eq!(fts_match_string("What is Mel's job?"), "\"mel\" OR \"job\"");
    let hits = r.ret.search("What is Mel's job?", &SearchOptions { k: 3, ..Default::default() }).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, r.mel.memory_id);
    assert!(hits[0].channels.contains_key("keyword"));
}

#[tokio::test]
async fn test_subject_filter_and_profile_resolve_aliases() {
    let r = retrieval_fixture();
    let got = ids(&r.ret, "pottery", SearchOptions { subject: Some("Melanie".into()), ..Default::default() }).await;
    assert_eq!(got, std::slice::from_ref(&r.mel.memory_id));
    assert_eq!(r.ret.profile("Melanie", 60).unwrap()["memory_count"], 2);
    assert_eq!(
        r.b.store.list_memories(&MemoryQuery { subject: Some("Melanie".into()), ..Default::default() }).unwrap().len(),
        2
    );
}

#[tokio::test]
async fn test_month_precision_and_same_day_bounds() {
    let r = retrieval_fixture();
    let opts =
        SearchOptions { since: Some("2026-10-01".into()), until: Some("2026-10-31".into()), ..Default::default() };
    assert!(ids(&r.ret, "visit Berlin", opts).await.contains(&r.trip.memory_id));
    assert!(ids(&r.ret, "visit Berlin", SearchOptions { since: Some("2026-11-01".into()), ..Default::default() })
        .await
        .is_empty());
    let day = make_memory(
        "Ali ran at dawn.",
        "ali",
        "c1",
        "2026-04-02T09:00:00+00:00",
        Some(fake_embedding("Ali ran at dawn.", 256)),
        "fact",
        0.7,
    );
    r.b.store.insert_memory(&day, &[], &["ali".into()]).unwrap();
    assert!(ids(&r.ret, "dawn", SearchOptions { until: Some("2026-04-02".into()), ..Default::default() })
        .await
        .contains(&day.memory_id));
}

#[tokio::test]
async fn test_diacritics_fold_in_keyword_channel() {
    let r = retrieval_fixture();
    assert_eq!(tokenize("Zürich Müller"), ["zurich", "muller"]);
    let hits = r.ret.search("Zürich", &SearchOptions { k: 3, ..Default::default() }).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, r.joerg.memory_id);
    assert!(hits[0].channels.contains_key("keyword"));
}

#[tokio::test]
async fn test_filtered_keyword_channel_is_not_starved() {
    let r = retrieval_fixture();
    for i in 0..300 {
        let m = make_memory(
            &format!("User note {i} about the marathon training block."),
            "user",
            "big",
            "2026-01-01T00:00:00+00:00",
            Some(fake_embedding(&format!("note {i} marathon"), 256)),
            "fact",
            0.7,
        );
        r.b.store.insert_memory(&m, &[], &["user".into()]).unwrap();
    }
    let mel_run =
        emb("Mel finished her first marathon in Osaka.", "mel", "Mel finished her first marathon in Osaka.", "event");
    r.b.store.insert_memory(&mel_run, &[], &["mel".into()]).unwrap();
    let opts = SearchOptions { subject: Some("Mel".into()), k: 5, channels: Some("K".into()), ..Default::default() };
    assert_eq!(ids(&r.ret, "marathon", opts).await, [mel_run.memory_id]);
}

#[tokio::test]
async fn test_index_is_incremental_and_handles_mixed_dimensions() {
    let r = retrieval_fixture();
    r.ret.search("pottery", &SearchOptions::default()).await.unwrap();
    let before = r.ret.index_last_updated_at();
    let weird = make_memory(
        "Ali collects stamps.",
        "ali",
        "c1",
        "2026-01-01T00:00:00+00:00",
        Some(vec![0.1, 0.2, 0.3]),
        "fact",
        0.7,
    );
    r.b.store.insert_memory(&weird, &[], &["ali".into()]).unwrap();
    r.ret.search("stamps", &SearchOptions::default()).await.unwrap();
    assert_eq!(r.ret.full_loads(), 1, "index object is refreshed in place, not rebuilt");
    assert!(r.ret.index_last_updated_at() >= before);
    let dims = r.ret.get_index().unwrap().dims();
    assert!(dims.contains(&3));
    assert!(dims.contains(&fake_embedding("x", 256).len()));
    let hits = r.ret.search("stamps", &SearchOptions { k: 2, ..Default::default() }).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, weird.memory_id);
    r.b.store.retract(&weird.memory_id, "").unwrap();
    assert!(!ids(&r.ret, "stamps", SearchOptions { k: 5, ..Default::default() }).await.contains(&weird.memory_id));
}

#[tokio::test]
async fn test_context_skips_lines_that_do_not_fit() {
    let r = retrieval_fixture();
    let text = format!("Mel {}in Tokyo.", "talked about pottery glazes at length ".repeat(12));
    let long = make_memory(
        &text,
        "mel",
        "c1",
        "2026-01-01T00:00:00+00:00",
        Some(fake_embedding("Mel pottery glazes Tokyo", 256)),
        "fact",
        0.7,
    );
    r.b.store.insert_memory(&long, &[], &["mel".into()]).unwrap();
    let block = r.ret.context_for("Mel pottery", None, Some(260), &SearchOptions::default()).await.unwrap();
    assert!(block.contains("pottery studio"), "a shorter relevant line still makes it into a tight budget");
}

#[tokio::test]
async fn test_bm25_fallback_on_tiny_corpus() {
    let r = retrieval_fixture();
    r.b.store.set_has_fts(false);
    let hits = r.b.store.keyword_candidates("pottery", 50, "active", &ChannelFilter::default()).unwrap();
    assert_eq!(hits.iter().map(|h| h.0.clone()).collect::<Vec<_>>(), std::slice::from_ref(&r.mel.memory_id));
}

// ---------------------------------------------------------------------------------------- HTTP + MCP

struct Http {
    _b: Base,
    cm: Arc<ChatMemory>,
    app: axum::Router,
}

fn http() -> Http {
    let b = base();
    let db = b.db();
    b.store.close();
    let cm = Arc::new(
        ChatMemory::open(
            &db,
            Some(Arc::new(scripted_fake_llm())),
            ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() },
        )
        .unwrap(),
    );
    let app = create_app(
        cm.clone(),
        ServerOptions { allowed_hosts: Some(vec!["localhost".into(), "127.0.0.1".into()]), ..Default::default() },
    );
    Http { _b: b, cm, app }
}

async fn post(app: &axum::Router, path: &str, headers: &[(&str, &str)], body: &[u8]) -> u16 {
    let mut b = Request::builder().method("POST").uri(path).header("Host", "127.0.0.1:8765");
    for (k, v) in headers {
        b = b.header(*k, *v);
    }
    let resp = app.clone().oneshot(b.body(Body::from(body.to_vec())).unwrap()).await.unwrap();
    let status = resp.status().as_u16();
    let _ = resp.into_body().collect().await;
    status
}

#[tokio::test]
async fn test_cross_site_writes_are_refused() {
    let h = http();
    let body = json!({"chat_id": "x", "speaker": "Ali", "text": "I moved to Berlin."}).to_string();
    let b = body.as_bytes();
    assert_eq!(
        post(&h.app, "/api/messages", &[("Origin", "https://evil.example"), ("Content-Type", "text/plain")], b).await,
        403
    );
    assert_eq!(
        post(&h.app, "/mcp", &[("Origin", "https://evil.example"), ("Content-Type", "text/plain")], b).await,
        403
    );
    assert_eq!(
        post(&h.app, "/api/messages", &[("Content-Type", "text/plain")], b).await,
        415,
        "a JSON body must be declared as JSON"
    );
    let same = [("Content-Type", "application/json"), ("Origin", "http://127.0.0.1:8765")];
    assert_eq!(post(&h.app, "/api/messages", &same, b).await, 202, "same-origin browser requests work");
    assert_eq!(h.cm.store.message_counts().unwrap().get("pending").copied(), Some(1));
}

#[tokio::test]
async fn test_bad_body_types_are_400_not_500() {
    let h = http();
    let j = [("Content-Type", "application/json")];
    let send = |v: Value| v.to_string().into_bytes();
    assert_eq!(
        post(&h.app, "/api/remember", &j, &send(json!({"text": "Ali likes tea.", "importance": "high"}))).await,
        400
    );
    assert_eq!(
        post(
            &h.app,
            "/api/messages",
            &j,
            &send(json!({"chat_id": "x", "speaker": "Ali", "text": "hi", "sent_at": 12345}))
        )
        .await,
        400
    );
    assert_eq!(
        post(&h.app, "/api/messages/batch", &j, &send(json!({"chat_id": "x", "messages": ["not an object"]}))).await,
        400
    );
}

#[tokio::test]
async fn test_mcp_malformed_shapes_and_version_header() {
    let h = http();
    let p = McpProtocol::new(h.cm.clone());
    assert_eq!(p.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": 5})).await.unwrap()["error"]["code"], -32600);
    assert_eq!(
        p.handle(&json!({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []})).await.unwrap()["error"]["code"],
        -32602
    );
    assert_eq!(p.handle_payload(&json!([])).await.unwrap()["error"]["code"], -32600);
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": "ping"}).to_string();
    let hdrs = [("Content-Type", "application/json"), ("MCP-Protocol-Version", "1999-01-01")];
    assert_eq!(post(&h.app, "/mcp", &hdrs, body.as_bytes()).await, 400);
}

#[tokio::test]
async fn test_stdio_survives_a_huge_message() {
    let h = http();
    let big = json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_add_message",
                     "arguments": {"chat_id": "big", "speaker": "Ali", "text": "x".repeat(200_000)}}});
    let input = format!("{big}\n{}\n", json!({"jsonrpc": "2.0", "id": 2, "method": "ping"}));
    let (writer, mut reader) = tokio::io::duplex(1 << 20);
    serve_stdio_with(h.cm.clone(), input.as_bytes(), writer, 64 * 1024 * 1024).await.unwrap();
    let mut out = String::new();
    reader.read_to_string(&mut out).await.unwrap();
    let mut got: Vec<i64> =
        out.lines().map(|l| serde_json::from_str::<Value>(l).unwrap()["id"].as_i64().unwrap()).collect();
    got.sort();
    assert_eq!(got, [1, 2]);
}

#[tokio::test]
async fn test_stdio_oversized_line_is_a_parse_error_not_a_crash() {
    let h = http();
    let input = format!("{}\n{}\n", "y".repeat(5000), json!({"jsonrpc": "2.0", "id": 7, "method": "ping"}));
    let (writer, mut reader) = tokio::io::duplex(1 << 16);
    serve_stdio_with(h.cm.clone(), input.as_bytes(), writer, 1024).await.unwrap();
    let mut out = String::new();
    reader.read_to_string(&mut out).await.unwrap();
    let lines: Vec<Value> = out.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(lines[0]["error"]["message"], "message too large");
    assert_eq!(lines[1]["id"], 7);
}

// ---------------------------------------------------------------------------------------- residual

#[tokio::test]
async fn test_debounce_coalesces_with_sub_second_timing() {
    // a turn at x.60 s and its reply 0.5 s later must wait for each other and be leased as one batch
    let b = base();
    let t0 = chrono::DateTime::parse_from_rfc3339("2026-09-17T10:00:00.600+00:00").unwrap().with_timezone(&chrono::Utc);
    let at = |ms: i64| clock::set_fixed(Some(t0 + chrono::TimeDelta::milliseconds(ms)));
    at(0);
    b.store
        .add_message("t", "Ali", "I moved to Berlin.", &AddMessage { debounce_seconds: 1.0, ..Default::default() })
        .unwrap();
    at(500);
    let reply = AddMessage { role: "assistant".into(), debounce_seconds: 1.0, ..Default::default() };
    b.store.add_message("t", "assistant", "Nice! Berlin is lovely.", &reply).unwrap();
    at(1200);
    assert!(
        b.store.lease_job("w", 60.0, None, 6, 0, 40).unwrap().is_empty(),
        "nothing runnable inside the reply's window"
    );
    at(1600);
    assert_eq!(b.store.lease_job("w", 60.0, None, 6, 0, 40).unwrap().len(), 2, "turn and reply leased together");
    clock::set_fixed(None);
}

#[tokio::test]
async fn test_kind_and_time_filters_reach_keyword_and_graph_channels() {
    let b = base();
    for i in 0..300 {
        let m = make_memory(
            &format!("User note {i} about the marathon training block."),
            "user",
            "big",
            "2026-02-01T00:00:00+00:00",
            Some(fake_embedding(&format!("note {i} marathon"), 256)),
            "fact",
            0.9,
        );
        b.store.insert_memory(&m, &[], &["user".into(), "marathon".into()]).unwrap();
    }
    let old = make_memory(
        "Mel ran the Osaka marathon.",
        "mel",
        "c1",
        "2019-06-01T00:00:00+00:00",
        Some(fake_embedding("Mel ran the Osaka marathon.", 256)),
        "event",
        0.3,
    );
    b.store.insert_memory(&old, &[], &["mel".into(), "marathon".into()]).unwrap();
    b.store.upsert_entity("marathon", "marathon", "event", None, 1, &[]).unwrap();
    let ret = MemoryRetriever::new(b.store.clone(), Arc::new(FakeLlmClient::default()), RetrievalConfig::default());
    let k = SearchOptions { kinds: Some(vec!["event".into()]), channels: Some("K".into()), ..Default::default() };
    assert_eq!(ids(&ret, "marathon", k).await, std::slice::from_ref(&old.memory_id));
    let g = SearchOptions { until: Some("2020-01-01".into()), channels: Some("G".into()), ..Default::default() };
    assert_eq!(ids(&ret, "marathon", g).await, std::slice::from_ref(&old.memory_id));
    // store-side list/timeline use the same interval semantics as the index
    let mut month = make_memory(
        "Mel plans a Kyoto trip.",
        "mel",
        "c1",
        "2026-01-01T00:00:00+00:00",
        Some(fake_embedding("Kyoto trip", 256)),
        "plan",
        0.7,
    );
    month.event_time = Some("2026-10".into());
    month.event_time_precision = "month".into();
    b.store.insert_memory(&month, &[], &["mel".into()]).unwrap();
    let window = |since: &str, until: &str| {
        let q = MemoryQuery {
            subject: Some("mel".into()),
            since: Some(since.into()),
            until: Some(until.into()),
            ..Default::default()
        };
        b.store.list_memories(&q).unwrap().into_iter().map(|m| m.memory_id).collect::<Vec<_>>()
    };
    assert_eq!(window("2026-10-01", "2026-10-31"), [month.memory_id]);
    assert_eq!(window("2019-01-01", "2019-12-31"), [old.memory_id]);
}

#[tokio::test]
async fn test_non_object_json_bodies_are_400() {
    let h = http();
    for path in ["/api/messages", "/api/messages/batch", "/api/remember", "/api/forget"] {
        assert_eq!(post(&h.app, path, &[("Content-Type", "application/json")], b"[1, 2]").await, 400, "{path}");
    }
    let body = json!({"chat_id": "b", "messages": [{"text": "ok one"}, {"text": "bad", "speaker": 5}]}).to_string();
    assert_eq!(
        post(&h.app, "/api/messages/batch", &[("Content-Type", "application/json")], body.as_bytes()).await,
        400
    );
    assert!(h.cm.store.message_counts().unwrap().is_empty(), "all-or-nothing: nothing inserted");
}

#[tokio::test]
async fn test_worker_learns_embedding_dimension_from_its_own_plans() {
    let h = http();
    let cm = &h.cm;
    let input = MessageInput { sent_at: Some("2026-04-01T10:00:00+00:00".into()), ..Default::default() };
    cm.add_message("d", "Ali", "I moved to Berlin in March and my cat Luna is 3 years old.", input).unwrap();
    cm.process_pending(None).await.unwrap();
    assert_eq!(cm.store.get_meta("embed_dim").unwrap(), Some(fake_embedding("x", 256).len().to_string()));
    // switch to a model with another dimension: maintenance re-embeds the old rows
    cm.set_llm(Arc::new(FakeLlmClient { dim: 128, ..scripted_fake_llm() }));
    let report = cm.maintain().await.unwrap();
    assert_eq!(report["reembedded"], 0, "dimension meta still says 256; the probe is only used when unknown");
    cm.store.set_meta("embed_dim", "128").unwrap();
    let report = cm.maintain().await.unwrap();
    assert!(report["reembedded"].as_i64().unwrap() >= 1);
    assert!(cm
        .memories(&MemoryQuery::default())
        .unwrap()
        .iter()
        .all(|m| m.embedding.as_ref().is_none_or(|e| e.len() == 128)));
}
