//! Disk IVF vector index, read pool and keyword term pruning: opt-in paths must agree with the shipped ones.

use neuralgraph_memory::{fake_embedding, ChatMemoryStore, FakeLlmClient, LlmClient, Memory, MemoryRetriever, RetrievalConfig, SearchOptions};
use serde_json::json;
use std::sync::Arc;

const DIM: usize = 64;

fn memory(i: usize, text: &str) -> Memory {
    let t = format!("2025-01-{:02}T12:00:00+00:00", 1 + i % 28);
    Memory {
        memory_id: format!("m{i:05}"),
        text: text.to_string(),
        kind: "fact".into(),
        subject: format!("person {}", i % 37),
        subject_name: format!("Person {}", i % 37),
        speaker: "user".into(),
        chat_id: "c1".into(),
        importance: 0.5,
        confidence: 0.9,
        event_time: None,
        event_time_precision: "none".into(),
        observed_at: t.clone(),
        created_at: t.clone(),
        updated_at: t,
        status: "active".into(),
        superseded_by: None,
        version: 1,
        text_hash: String::new(),
        embedding: Some(fake_embedding(text, DIM)),
        access_count: 0,
        last_accessed_at: None,
        metadata: json!({}),
        source_message_ids: Vec::new(),
        entity_ids: Vec::new(),
    }
}

const TOPICS: [&str; 8] = ["router firmware", "gym schedule", "favorite food", "home city", "laptop model", "project codename", "pet name", "commute"];

fn text(i: usize) -> String {
    format!("Person {} mentioned the {} number {} during review {}", i % 37, TOPICS[i % 8], i % 11, i)
}

fn seeded_store(dir: &tempfile::TempDir, n: usize) -> Arc<ChatMemoryStore> {
    let store = Arc::new(ChatMemoryStore::open(&dir.path().join("m.db").to_string_lossy()).unwrap());
    let items: Vec<_> = (0..n).map(|i| (memory(i, &text(i)), Vec::new(), Vec::new())).collect();
    store.insert_memories_bulk(&items).unwrap();
    store
}

fn retriever(store: &Arc<ChatMemoryStore>, index: &str, nprobe: usize) -> MemoryRetriever {
    let llm: Arc<dyn LlmClient> = Arc::new(FakeLlmClient::with_default(""));
    let cfg = RetrievalConfig { vector_index: index.into(), ivf_nprobe: nprobe, ..Default::default() };
    MemoryRetriever::new(store.clone(), llm, cfg)
}

fn ids(r: &MemoryRetriever, q: &str, channels: &str) -> Vec<String> {
    let opts = SearchOptions {
        k: 10,
        touch: false,
        with_sources: false,
        query_embedding: Some(fake_embedding(q, DIM)),
        channels: Some(channels.into()),
        ..Default::default()
    };
    futures::executor::block_on(r.search(q, &opts)).unwrap().into_iter().map(|m| m.memory.memory_id).collect()
}

#[test]
fn ivf_probing_every_list_matches_exact_ranking() {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded_store(&dir, 600);
    let exact = retriever(&store, "exact", 0);
    let ivf = retriever(&store, "ivf", 1000);
    let stats = ivf.build_ivf(12, 5).unwrap();
    assert_eq!(stats.n, 600);
    assert_eq!(stats.nlist, 12);
    for q in ["router firmware number 3", "Person 5 gym schedule", "favorite food review 77", "commute number 9"] {
        assert_eq!(ids(&exact, q, "V"), ids(&ivf, q, "V"), "vector channel differs for {q:?}");
        assert_eq!(ids(&exact, q, "VK"), ids(&ivf, q, "VK"), "fused ranking differs for {q:?}");
    }
}

#[test]
fn ivf_keeps_covered_embeddings_out_of_memory_and_scans_newer_rows_exactly() {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded_store(&dir, 300);
    let ivf = retriever(&store, "ivf", 1000);
    ivf.build_ivf(6, 4).unwrap();
    let index = ivf.get_index().unwrap();
    assert!(index.dims().is_empty(), "covered rows must not carry resident embeddings, got dims {:?}", index.dims());
    // a row written after the build is found through the exact delta
    let late = "Person 99 adopted a tortoise named Atlas yesterday";
    // the index refreshes incrementally by updated_at, which real writes stamp with the current time
    let mut m = memory(5000, late);
    m.updated_at = "2026-10-31T00:00:00+00:00".into();
    store.insert_memory(&m, &[], &[]).unwrap();
    assert_eq!(ivf.get_index().unwrap().dims(), vec![DIM]);
    assert_eq!(ids(&ivf, late, "V").first().map(String::as_str), Some("m05000"));
}

#[test]
fn ivf_falls_back_to_exact_without_an_index_file() {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded_store(&dir, 200);
    let exact = retriever(&store, "exact", 0);
    let ivf = retriever(&store, "ivf", 8);
    assert_eq!(ids(&exact, "laptop model number 4", "VK"), ids(&ivf, "laptop model number 4", "VK"));
}

#[test]
fn read_pool_returns_the_same_results() {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded_store(&dir, 400);
    let r = retriever(&store, "exact", 0);
    let before: Vec<_> = TOPICS.iter().map(|q| ids(&r, q, "VK")).collect();
    store.set_read_pool(true);
    let after: Vec<_> = TOPICS.iter().map(|q| ids(&r, q, "VK")).collect();
    assert_eq!(before, after);
}

#[test]
fn keyword_pruning_drops_common_terms_and_keeps_the_rarest() {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded_store(&dir, 400);
    let f = neuralgraph_memory::store::ChannelFilter::default();
    // "mentioned" is in every memory, "review 123" only in one
    let all = store.keyword_candidates("mentioned review 123", 1000, "active", &f).unwrap();
    store.set_keyword_max_df(0.05);
    let pruned = store.keyword_candidates("mentioned review 123", 1000, "active", &f).unwrap();
    assert_eq!(all.len(), 400);
    assert_eq!(pruned.iter().map(|(m, _)| m.as_str()).collect::<Vec<_>>(), vec!["m00123"]);
    // a query made only of common terms keeps its rarest term rather than going empty
    assert!(!store.keyword_candidates("mentioned during", 1000, "active", &f).unwrap().is_empty());
}
