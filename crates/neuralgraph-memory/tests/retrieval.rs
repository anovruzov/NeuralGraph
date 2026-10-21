//! Retrieval + facade tests: channel fusion, subject routing from metadata, filters, superseded handling, context
//! blocks with a relative cutoff, explicit remember/forget, the token ledger and the grade. Ported from
//! `test_chat_memory_retrieval.py`.

mod common;

use common::{emb_memory, make_memory};
use indexmap::IndexMap;
use neuralgraph_memory::llm::FakeLlmClient;
use neuralgraph_memory::retrieval::{rrf, MemoryRetriever, RetrievalConfig, SearchOptions};
use neuralgraph_memory::service::{estimate_tokens, ChatMemory, ChatMemoryConfig, MessageInput, RememberOptions};
use neuralgraph_memory::store::{ChatMemoryStore, MemoryQuery};
use std::collections::HashSet;
use std::sync::Arc;

struct Fx {
    _dir: tempfile::TempDir,
    store: Arc<ChatMemoryStore>,
    ret: MemoryRetriever,
}

fn fx() -> Fx {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(ChatMemoryStore::open(dir.path().join("r.db").to_str().unwrap()).unwrap());
    let ret = MemoryRetriever::new(store.clone(), Arc::new(FakeLlmClient::default()), RetrievalConfig::default());
    Fx { _dir: dir, store, ret }
}

fn seed(store: &ChatMemoryStore) -> IndexMap<&'static str, String> {
    let mut ids = IndexMap::new();
    let specs = [
        (
            "ali_city",
            "Ali lives in Berlin near Prenzlauer Berg.",
            "ali",
            "c1",
            "2026-03-01T00:00:00+00:00",
            "identity",
            0.9,
        ),
        ("ali_cat", "Ali has a cat called Luna.", "ali", "c1", "2026-03-01T00:00:00+00:00", "fact", 0.7),
        (
            "mel_city",
            "Mel lives in Tokyo and works at a pottery studio.",
            "mel",
            "c2",
            "2026-04-01T00:00:00+00:00",
            "identity",
            0.9,
        ),
        (
            "mel_trip",
            "Mel plans to visit Berlin in October 2026.",
            "mel",
            "c2",
            "2026-04-02T00:00:00+00:00",
            "plan",
            0.7,
        ),
        ("ali_old", "Ali ran a half marathon in 2019.", "ali", "c3", "2019-06-01T00:00:00+00:00", "event", 0.5),
    ];
    for (key, text, subject, chat, at, kind, imp) in specs {
        let m = emb_memory(text, subject, chat, at, kind, imp);
        let mut ents = vec![subject.to_string()];
        for (needle, e) in [("Berlin", "berlin"), ("Tokyo", "tokyo"), ("Luna", "luna")] {
            if text.contains(needle) {
                ents.push(e.into());
            }
        }
        store.insert_memory(&m, &[], &ents).unwrap();
        ids.insert(key, m.memory_id);
    }
    for (eid, name, etype) in [
        ("ali", "Ali", "person"),
        ("mel", "Mel", "person"),
        ("berlin", "Berlin", "place"),
        ("tokyo", "Tokyo", "place"),
        ("luna", "Luna", "pet"),
    ] {
        let aliases = if eid == "mel" { vec!["melanie".to_string()] } else { vec![] };
        store.upsert_entity(eid, name, etype, None, 1, &aliases).unwrap();
    }
    store.upsert_relation("ali", "has_pet", "luna", 0.6, "c1", Some(&ids["ali_cat"]), None, None).unwrap();
    store.upsert_relation("ali", "lives_in", "berlin", 0.6, "c1", Some(&ids["ali_city"]), None, None).unwrap();
    ids
}

fn k(n: usize) -> SearchOptions {
    SearchOptions { k: n, ..Default::default() }
}

async fn ids_of(ret: &MemoryRetriever, q: &str, opts: SearchOptions) -> Vec<String> {
    ret.search(q, &opts).await.unwrap().into_iter().map(|h| h.memory.memory_id).collect()
}

#[test]
fn test_rrf_shape() {
    let fused = rrf(&[vec!["a".into(), "b".into()], vec!["b".into(), "c".into()]], &[1.0, 1.0], 60);
    assert!(fused["b"] > fused["a"]);
    assert!(fused["a"] > fused["c"]);
}

#[tokio::test]
async fn test_subject_named_in_query_is_boosted() {
    let f = fx();
    let ids = seed(&f.store);
    let hits = f.ret.search("Where does Mel live?", &k(3)).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, ids["mel_city"]);
    assert!(hits[0].channels.contains_key("subject"));
    // alias "Melanie" routes to the same subject (identity from metadata, not text)
    let hits = f.ret.search("Where does Melanie live?", &k(3)).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, ids["mel_city"]);
}

#[tokio::test]
async fn test_channels_and_explanations() {
    let f = fx();
    let ids = seed(&f.store);
    // graph channel is opt-in by default
    let hits = f
        .ret
        .search("Luna cat", &SearchOptions { k: 2, channels: Some("VKG".into()), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(hits[0].memory.memory_id, ids["ali_cat"]);
    assert!(hits[0].channels.contains_key("keyword"));
    assert!(hits[0].channels.contains_key("graph"), "the entity 'luna' is matched in the query");
    assert!(hits[0].explanation.contains("keyword match"));
    assert!(hits[0].sources.is_empty() && !hits[0].entities.is_empty());
}

#[tokio::test]
async fn test_graph_hop_finds_related_memory() {
    let f = fx();
    let ids = seed(&f.store);
    let got = ids_of(&f.ret, "berlin", SearchOptions { k: 5, channels: Some("G".into()), ..Default::default() }).await;
    assert!(got.contains(&ids["ali_city"]));
    assert!(got.contains(&ids["mel_trip"]));
}

#[tokio::test]
async fn test_filters() {
    let f = fx();
    let ids = seed(&f.store);
    let o = |f: fn(&mut SearchOptions)| {
        let mut opts = SearchOptions::default();
        f(&mut opts);
        opts
    };
    assert_eq!(
        ids_of(&f.ret, "Berlin", o(|x| x.subject = Some("Mel".into()))).await,
        std::slice::from_ref(&ids["mel_trip"])
    );
    assert_eq!(
        ids_of(&f.ret, "Ali", o(|x| x.chat_id = Some("c3".into()))).await,
        std::slice::from_ref(&ids["ali_old"])
    );
    assert_eq!(
        ids_of(&f.ret, "Ali", o(|x| x.kinds = Some(vec!["fact".into()]))).await,
        std::slice::from_ref(&ids["ali_cat"])
    );
    assert_eq!(
        ids_of(&f.ret, "Ali marathon", o(|x| x.until = Some("2020-01-01".into()))).await,
        [ids["ali_old"].clone()]
    );
    assert!(ids_of(&f.ret, "Ali", o(|x| x.since = Some("2030-01-01".into()))).await.is_empty());
}

#[tokio::test]
async fn test_superseded_memories_are_hidden_unless_asked() {
    let f = fx();
    let ids = seed(&f.store);
    let new =
        emb_memory("Ali lives in Tokyo since June 2026.", "ali", "c4", "2026-07-01T00:00:00+00:00", "identity", 0.9);
    f.store.insert_memory(&new, &[], &["ali".into(), "tokyo".into()]).unwrap();
    f.store.supersede(&ids["ali_city"], &new.memory_id, "contradicts").unwrap();
    let got = ids_of(&f.ret, "Where does Ali live?", k(5)).await;
    assert!(got.contains(&new.memory_id));
    assert!(!got.contains(&ids["ali_city"]));
    let hist = f
        .ret
        .search("Where does Ali live?", &SearchOptions { k: 5, include_superseded: true, ..Default::default() })
        .await
        .unwrap();
    let old: Vec<_> = hist.iter().filter(|h| h.memory.memory_id == ids["ali_city"]).collect();
    assert_eq!(old.len(), 1);
    assert_eq!(old[0].memory.status, "superseded");
    assert!(old[0].explanation.contains("superseded by"));
}

#[tokio::test]
async fn test_time_window_query_returns_the_fact_that_was_true_then() {
    let f = fx();
    let ids = seed(&f.store);
    let mut new =
        emb_memory("Ali lives in Tokyo since June 2026.", "ali", "c4", "2026-07-01T00:00:00+00:00", "identity", 0.9);
    new.event_time = Some("2026-06".into());
    new.event_time_precision = "month".into();
    f.store.insert_memory(&new, &[], &["ali".into(), "tokyo".into()]).unwrap();
    f.store.supersede(&ids["ali_city"], &new.memory_id, "contradicts").unwrap();
    // no time filter: only the current fact
    let now_hits = ids_of(&f.ret, "Where does Ali live?", k(5)).await;
    assert!(now_hits.contains(&new.memory_id));
    assert!(!now_hits.contains(&ids["ali_city"]));
    // asking about March 2026: the Berlin fact was current then and must come back
    let opts = SearchOptions {
        k: 5,
        since: Some("2026-03-01".into()),
        until: Some("2026-03-31".into()),
        ..Default::default()
    };
    let then_hits = f.ret.search("Where did Ali live?", &opts).await.unwrap();
    let then_ids: Vec<&String> = then_hits.iter().map(|h| &h.memory.memory_id).collect();
    assert_eq!(then_ids[0], &ids["ali_city"]);
    assert!(!then_ids.contains(&&new.memory_id));
    assert!(then_hits[0].explanation.contains(&format!("superseded by {}", new.memory_id)));
}

#[tokio::test]
async fn test_index_cache_tracks_store_revision() {
    let f = fx();
    seed(&f.store);
    let before: HashSet<String> = ids_of(&f.ret, "Ali", k(50)).await.into_iter().collect();
    let extra = emb_memory("Ali bought a bike.", "ali", "c1", "2026-01-01T00:00:00+00:00", "fact", 0.7);
    f.store.insert_memory(&extra, &[], &["ali".into()]).unwrap();
    let after: HashSet<String> = ids_of(&f.ret, "Ali", k(50)).await.into_iter().collect();
    assert_eq!(after.difference(&before).cloned().collect::<HashSet<_>>(), HashSet::from([extra.memory_id.clone()]));
    // retract goes through the store revision too
    f.store.retract(&extra.memory_id, "").unwrap();
    assert!(!ids_of(&f.ret, "Ali", k(50)).await.contains(&extra.memory_id));
}

#[tokio::test]
async fn test_context_block_and_relative_cutoff() {
    let f = fx();
    seed(&f.store);
    let none = SearchOptions::default();
    let block = f.ret.context_for("Where does Mel live?", Some(5), None, &none).await.unwrap();
    assert!(block.starts_with("Relevant long-term memories"));
    assert!(block.contains("Mel lives in Tokyo"));
    assert!(!block.contains("half marathon"), "weak, unrelated hits are cut by the relative-score threshold");
    assert_eq!(f.ret.context_for("zzzz qqqq", None, None, &none).await.unwrap(), "");
    let short = f.ret.context_for("Where does Mel live?", None, Some(120), &none).await.unwrap();
    assert!(short.chars().count() <= 120);
}

#[tokio::test]
async fn test_profile_related_timeline() {
    let f = fx();
    let ids = seed(&f.store);
    let prof = f.ret.profile("Ali", 60).unwrap();
    assert_eq!(prof["memory_count"], 3);
    let preds: HashSet<&str> =
        prof["relations"].as_array().unwrap().iter().map(|r| r["predicate"].as_str().unwrap()).collect();
    assert_eq!(preds, HashSet::from(["has_pet", "lives_in"]));
    f.store.add_link(&ids["ali_cat"], &ids["ali_city"], "related", 0.5).unwrap();
    let rel = f.ret.related(&ids["ali_cat"]).unwrap();
    assert_eq!(
        rel.iter().map(|r| r["memory"]["memory_id"].as_str().unwrap()).collect::<Vec<_>>(),
        [ids["ali_city"].as_str()]
    );
    let tl = f.ret.timeline(Some("ali"), None, None, 100).unwrap();
    assert_eq!(tl[0].memory_id, ids["ali_old"]);
}

#[tokio::test]
async fn test_vector_channel_survives_dimension_mismatch() {
    let f = fx();
    seed(&f.store);
    // a different embedding model
    let weird = make_memory(
        "Ali collects stamps.",
        "ali",
        "c1",
        "2026-01-01T00:00:00+00:00",
        Some(vec![0.1, 0.2, 0.3]),
        "fact",
        0.7,
    );
    f.store.insert_memory(&weird, &[], &["ali".into()]).unwrap();
    let hits = f.ret.search("stamps", &k(3)).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, weird.memory_id, "keyword channel still finds it");
}

// ---------------------------------------------------------------------------------------- facade

struct Cm {
    _dir: tempfile::TempDir,
    cm: ChatMemory,
}

fn facade() -> Cm {
    let dir = tempfile::tempdir().unwrap();
    let cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    let cm = ChatMemory::open(dir.path().join("f.db").to_str().unwrap(), Some(Arc::new(FakeLlmClient::default())), cfg)
        .unwrap();
    Cm { _dir: dir, cm }
}

#[tokio::test]
async fn test_remember_forget_and_dedupe() {
    let f = facade();
    let opts = RememberOptions {
        subject: "Ali".into(),
        kind: "preference".into(),
        when: Some("2026-01".into()),
        ..Default::default()
    };
    let m = f.cm.remember("Ali prefers dark roast coffee.", &opts).await.unwrap();
    assert_eq!((m.subject.as_str(), m.kind.as_str(), m.event_time.as_deref()), ("ali", "preference", Some("2026-01")));
    let again = f
        .cm
        .remember("Ali prefers dark roast coffee.", &RememberOptions { subject: "Ali".into(), ..Default::default() })
        .await
        .unwrap();
    assert_eq!(again.memory_id, m.memory_id);
    assert_eq!(f.cm.memories(&MemoryQuery::default()).unwrap().len(), 1);
    let hits = f.cm.search("coffee", &SearchOptions::default()).await.unwrap();
    assert_eq!(hits[0].memory.memory_id, m.memory_id);
    assert!(f.cm.retract(&m.memory_id, "user request").unwrap());
    assert!(f.cm.search("coffee", &SearchOptions::default()).await.unwrap().is_empty());
    assert!(f.cm.remember("x", &RememberOptions::default()).await.is_err());
}

#[tokio::test]
async fn test_token_ledger_and_grade() {
    let f = facade();
    assert_eq!(estimate_tokens(&"a".repeat(40)), 10);
    let text = "I moved to Berlin in March and I love it here, the food is great and my cat Luna enjoys the balcony."
        .repeat(3);
    let msg = f.cm.add_message("c1", "Ali", &text, MessageInput::default()).unwrap();
    f.cm.store.set_message_status(&[msg.message_id], "processed").unwrap();
    let opts = RememberOptions { subject: "Ali".into(), chat_id: "c1".into(), ..Default::default() };
    f.cm.remember("Ali moved to Berlin in March 2026.", &opts).await.unwrap();
    let ledger = f.cm.token_ledger().unwrap();
    let n = |k: &str| ledger[k].as_i64().unwrap();
    assert!(n("raw_tokens_digested") > n("memory_tokens"));
    assert_eq!(n("tokens_saved"), n("raw_tokens_digested") - n("memory_tokens"));
    assert!(ledger["compression_ratio"].as_f64().unwrap() > 1.0);
    let grade = f.cm.grade().unwrap();
    let axes = grade["axes"].as_object().unwrap();
    let keys: HashSet<&str> = axes.keys().map(String::as_str).collect();
    assert_eq!(keys, HashSet::from(["coverage", "compression", "connectivity", "freshness", "reliability"]));
    assert!(axes.values().all(|v| (0.0..=100.0).contains(&v.as_f64().unwrap())));
    assert_eq!(axes["coverage"].as_f64().unwrap(), 100.0);
    assert!(axes["compression"].as_f64().unwrap() > 50.0);
    assert!("SABCD".contains(grade["letter"].as_str().unwrap()));
    let block = f.cm.context_for("Berlin", None, None, &SearchOptions::default()).await.unwrap();
    assert!(block.contains("Ali moved to Berlin"));
    let ledger = f.cm.token_ledger().unwrap();
    assert_eq!(ledger["context_queries"], 1);
    assert!(ledger["context_raw_tokens_avoided"].as_i64().unwrap() > 0);
}

#[tokio::test]
async fn test_status_shape() {
    let f = facade();
    let st = f.cm.status().unwrap();
    for key in [
        "chats",
        "messages",
        "memories",
        "entities",
        "relations",
        "links",
        "jobs",
        "worker",
        "llm",
        "tokens",
        "grade",
        "recent_memories",
        "top_entities",
        "recent_events",
    ] {
        assert!(st.get(key).is_some(), "{key}");
    }
    assert_eq!(st["worker"]["running"], false);
}

#[tokio::test]
async fn test_add_message_validates_and_infers_role() {
    let f = facade();
    assert!(f.cm.add_message("", "Ali", "x", MessageInput::default()).is_err());
    let m = f.cm.add_message("c", "assistant", "hello", MessageInput::default()).unwrap();
    assert_eq!(m.role, "assistant");
    let m2 = f.cm.add_message("c", "Ali", "hello there", MessageInput::default()).unwrap();
    assert_eq!(m2.role, "");
}
