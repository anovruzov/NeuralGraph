//! The index snapshot is a base segment plus a delta of rows changed since the last compaction: a retriever that
//! refreshed through any mix of writes must answer exactly like one that loaded the same store from scratch.

use neuralgraph_memory::models::now_iso;
use neuralgraph_memory::{fake_embedding, ChatMemoryStore, FakeLlmClient, LlmClient, Memory, MemoryRetriever, RetrievalConfig, SearchOptions};
use serde_json::json;
use std::sync::Arc;

const DIM: usize = 64;
const TOPICS: [&str; 8] = ["router firmware", "gym schedule", "favorite food", "home city", "laptop model", "project codename", "pet name", "commute"];

fn text(i: usize) -> String {
    format!("Person {} mentioned the {} number {} during review {}", i % 37, TOPICS[i % 8], i % 11, i)
}

fn memory(id: &str, i: usize, text: &str, updated_at: String) -> Memory {
    let t = format!("2025-01-{:02}T12:00:00+00:00", 1 + i % 28);
    Memory {
        memory_id: id.to_string(),
        text: text.to_string(),
        kind: if i % 5 == 0 { "preference".into() } else { "fact".into() },
        subject: format!("person {}", i % 37),
        subject_name: format!("Person {}", i % 37),
        speaker: "user".into(),
        chat_id: format!("c{}", i % 3),
        importance: 0.3 + (i % 7) as f64 / 10.0,
        confidence: 0.9,
        event_time: None,
        event_time_precision: "none".into(),
        observed_at: t.clone(),
        created_at: t,
        updated_at,
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

fn retriever(store: &Arc<ChatMemoryStore>, index: &str) -> MemoryRetriever {
    let llm: Arc<dyn LlmClient> = Arc::new(FakeLlmClient::with_default(""));
    let cfg = RetrievalConfig { vector_index: index.into(), ivf_nprobe: 1000, ..Default::default() };
    MemoryRetriever::new(store.clone(), llm, cfg)
}

/// Every ranked id with its score and per-channel evidence, for a spread of queries, channels and filters.
fn answers(r: &MemoryRetriever) -> Vec<String> {
    let mut out = Vec::new();
    // the texts `write_mix` duplicates: their original and their copy tie exactly on cosine
    let dup_texts: Vec<String> = (0..60).step_by(3).map(text).collect();
    let queries: Vec<&str> =
        TOPICS.iter().copied().chain(["Person 4 home city number 2", "review 17", "Person 12 commute"]).chain(dup_texts.iter().map(String::as_str)).collect();
    for (qi, q) in queries.iter().enumerate() {
        for channels in ["V", "K", "VK"] {
            for include_superseded in [false, true] {
                let opts = SearchOptions {
                    k: 15,
                    touch: false,
                    with_sources: false,
                    include_superseded,
                    chat_id: (qi % 4 == 3).then(|| "c1".to_string()),
                    query_embedding: Some(fake_embedding(q, DIM)),
                    channels: Some(channels.into()),
                    ..Default::default()
                };
                for m in futures::executor::block_on(r.search(q, &opts)).unwrap() {
                    out.push(format!("{q}|{channels}|{include_superseded}|{}|{:?}|{:?}", m.memory.memory_id, m.score, m.channels));
                }
            }
        }
    }
    out
}

/// Inserts (some exact duplicates of existing texts, so vector ties span base and delta), supersessions and
/// retractions, all stamped with the current time as the worker stamps them.
fn write_mix(store: &ChatMemoryStore, from: usize, count: usize) {
    for j in from..from + count {
        let id = format!("n{j:05}");
        let body = if j % 3 == 0 { text(j % 200) } else { format!("{} (follow-up {j})", text(j)) };
        store.insert_memory(&memory(&id, j, &body, now_iso()), &[], &[]).unwrap();
        match j % 4 {
            1 => store.supersede(&format!("m{:05}", j % 200), &id, "supersedes").unwrap(),
            2 => {
                store.retract(&format!("m{:05}", (j * 7) % 200), "test").unwrap();
            }
            3 if j > from => {
                store.retract(&format!("n{:05}", j - 1), "test").unwrap();
            }
            _ => {}
        }
    }
}

fn seeded(dir: &tempfile::TempDir, n: usize) -> Arc<ChatMemoryStore> {
    let store = Arc::new(ChatMemoryStore::open(&dir.path().join("m.db").to_string_lossy()).unwrap());
    // the last 40 rows repeat the first 40 texts: base rows that tie, one of each pair later rewritten into the delta.
    // Distinct updated_at values, so a refresh re-reads only the rows actually written (it is inclusive of the
    // high-water mark: rows sharing it are read again, which would hide ordering bugs behind a full re-read).
    let items: Vec<_> = (0..n)
        .map(|i| {
            let body = if i >= n - 40 { text(i - (n - 40)) } else { text(i) };
            (memory(&format!("m{i:05}"), i, &body, format!("2025-02-01T{:02}:{:02}:00+00:00", i / 60, i % 60)), Vec::new(), Vec::new())
        })
        .collect();
    store.insert_memories_bulk(&items).unwrap();
    store
}

fn check_mode(index: &str) {
    let dir = tempfile::tempdir().unwrap();
    let store = seeded(&dir, 600);
    if index == "ivf" {
        retriever(&store, index).build_ivf(8, 4).unwrap();
    }
    let live = retriever(&store, index);
    assert_eq!(answers(&live), answers(&retriever(&store, index)), "{index}: before any write");
    // small batches with a search between each: the snapshot is re-assembled from base + delta every time
    for round in 0..6 {
        write_mix(&store, round * 40, 40);
        assert_eq!(answers(&live), answers(&retriever(&store, index)), "{index}: after write round {round}");
    }
    // enough changes to force a compaction, then more writes on top of the new base
    write_mix(&store, 1000, 2100);
    assert_eq!(answers(&live), answers(&retriever(&store, index)), "{index}: after compaction");
    write_mix(&store, 4000, 30);
    assert_eq!(answers(&live), answers(&retriever(&store, index)), "{index}: after writes on the compacted base");
    assert_eq!(live.full_loads(), 1, "{index}: refreshes must stay incremental");
    assert_eq!(live.get_index().unwrap().len(), retriever(&store, index).get_index().unwrap().len());
}

#[test]
fn incremental_snapshot_matches_a_fresh_load_exact() {
    check_mode("exact");
}

#[test]
fn incremental_snapshot_matches_a_fresh_load_ivf() {
    check_mode("ivf");
}
