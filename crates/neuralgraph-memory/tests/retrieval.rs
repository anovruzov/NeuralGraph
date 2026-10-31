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

// ------------------------------------------------------------------------------------------ context quotes

fn quote_fx(quotes: usize, quote_chars: usize) -> (Fx, String) {
    quote_fx_with(RetrievalConfig { context_quotes: quotes, context_quote_chars: quote_chars, ..Default::default() })
}

fn quote_fx_with(cfg: RetrievalConfig) -> (Fx, String) {
    use neuralgraph_memory::store::AddMessage;
    let f = fx();
    let ret = MemoryRetriever::new(f.store.clone(), Arc::new(FakeLlmClient::default()), cfg);
    let add = |speaker: &str, role: &str, text: &str, at: &str| {
        let opts = AddMessage { role: role.into(), sent_at: Some(at.into()), ..Default::default() };
        f.store.add_message("c2", speaker, text, &opts).unwrap().0.message_id
    };
    let bot = add("Bot", "assistant", "Tokyo sounds lovely, Mel.", "2026-04-01T09:01:00+00:00");
    let a = add("Mel", "user", "I live in Tokyo,\nin a small flat.", "2026-04-01T09:00:00+00:00");
    let long = add("Mel", "user", &format!("Tokyo {}", "x".repeat(40)), "2026-04-01T09:02:00+00:00");
    let dup = add("Mel", "user", "I live in Tokyo, in a small flat.", "2026-04-01T09:03:00+00:00");
    let empty = add("Mel", "user", "   ", "2026-04-01T09:04:00+00:00");
    let m = emb_memory(
        "Mel lives in Tokyo and works at a pottery studio.",
        "mel",
        "c2",
        "2026-04-01T00:00:00+00:00",
        "identity",
        0.9,
    );
    f.store.insert_memory(&m, &[bot, a, long, dup, empty], &["mel".into()]).unwrap();
    f.store.upsert_entity("mel", "Mel", "person", None, 1, &[]).unwrap();
    (Fx { ret, ..f }, m.memory_id)
}

const Q: &str = "Where does Mel live?";

#[tokio::test]
async fn test_context_quotes_zero_turns_off_and_default_is_one() {
    let (f, _) = quote_fx(0, 300);
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert!(block.contains("Mel lives in Tokyo"));
    assert!(!block.contains("    > "));
    let d = RetrievalConfig::default();
    assert_eq!((d.context_quotes, d.context_quote_chars, d.context_quotes_human_only), (1, 300, true));
}

#[tokio::test]
async fn test_context_quotes_human_only_format_order_and_dedupe() {
    let (f, _) = quote_fx(2, 300);
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    let lines: Vec<&str> = block.lines().collect();
    let at = lines.iter().position(|l| l.starts_with("- [") && l.contains("Mel lives in Tokyo")).unwrap();
    // human sources in message order, newlines flattened; the assistant message is never quoted
    assert_eq!(lines[at + 1], "    > Mel (2026-04-01): \"I live in Tokyo, in a small flat.\"");
    assert_eq!(lines[at + 2], format!("    > Mel (2026-04-01): \"Tokyo {}\"", "x".repeat(40)));
    assert!(lines.get(at + 3).map_or(true, |l| !l.starts_with("    > ")), "capped at context_quotes");
    // with room for more, the duplicate and the blank are skipped and the assistant turn is still left out
    let (f, _) = quote_fx(5, 300);
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert_eq!(block.lines().filter(|l| l.starts_with("    > ")).count(), 2);
    assert!(!block.contains("Bot ("));
    // human_only = false: assistant turns follow the human ones
    let cfg = RetrievalConfig { context_quotes: 5, context_quotes_human_only: false, ..Default::default() };
    let (f, _) = quote_fx_with(cfg);
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    let quotes: Vec<&str> = block.lines().filter(|l| l.starts_with("    > ")).collect();
    assert_eq!(quotes.len(), 3);
    assert_eq!(quotes[2], "    > Bot (2026-04-01): \"Tokyo sounds lovely, Mel.\"");
}

#[tokio::test]
async fn test_context_quotes_dedupe_across_memories() {
    use neuralgraph_memory::store::AddMessage;
    let (f, _) = quote_fx(1, 300);
    let add = |text: &str| {
        let opts =
            AddMessage { role: "user".into(), sent_at: Some("2026-04-02T09:00:00+00:00".into()), ..Default::default() };
        f.store.add_message("c2", "Mel", text, &opts).unwrap().0.message_id
    };
    // the same message (modulo case and spacing) backs a second memory, which also has a message of its own
    let echo = add("I LIVE in  Tokyo,\nin a small flat.");
    let own = add("My kiln is in the Tokyo flat.");
    let m = emb_memory("Mel keeps a kiln in Tokyo.", "mel", "c2", "2026-04-01T00:00:00+00:00", "fact", 0.8);
    f.store.insert_memory(&m, &[echo, own], &["mel".into()]).unwrap();
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    let quotes: Vec<&str> = block.lines().filter(|l| l.starts_with("    > ")).collect();
    assert_eq!(quotes.len(), 2, "{block}");
    assert_eq!(quotes.iter().filter(|l| l.to_lowercase().contains("live in")).count(), 1, "{block}");
}

#[tokio::test]
async fn test_context_quote_truncation() {
    // no sentence boundary in the first 10 characters: hard cut, trailing space dropped
    let (f, _) = quote_fx(2, 10);
    let block = f.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert!(block.contains("    > Mel (2026-04-01): \"I live in…\""), "{block}");
}

#[tokio::test]
async fn test_context_quote_sentence_boundary_truncation() {
    use neuralgraph_memory::store::AddMessage;
    let q = |text: &str, limit: usize| {
        let (f, _) = quote_fx(1, limit);
        let opts =
            AddMessage { role: "user".into(), sent_at: Some("2026-03-01T09:00:00+00:00".into()), ..Default::default() };
        let id = f.store.add_message("c3", "Mel", text, &opts).unwrap().0.message_id;
        let m = emb_memory("Mel plays the cello in Tokyo.", "mel", "c3", "2026-03-01T00:00:00+00:00", "fact", 0.95);
        f.store.insert_memory(&m, &[id], &["mel".into()]).unwrap();
        async move {
            let block =
                f.ret.context_for("Mel plays the cello", Some(1), None, &SearchOptions::default()).await.unwrap();
            block.lines().find(|l| l.starts_with("    > ")).unwrap().to_string()
        }
    };
    // boundary at index 19 (> 30 / 2): cut after the full stop, the space is dropped
    let got = q("First sentence here. Second sentence goes on and on", 30).await;
    assert_eq!(got, "    > Mel (2026-03-01): \"First sentence here.…\"");
    // "? " and "! " count too; the last boundary inside the limit wins
    let got = q("Why? Because! Then what happens next in the story", 20).await;
    assert_eq!(got, "    > Mel (2026-03-01): \"Why? Because!…\"");
    // boundary in the first half only: hard cut at the limit instead
    let got = q("Hi. Then a long run-on sentence without stops", 20).await;
    assert_eq!(got, "    > Mel (2026-03-01): \"Hi. Then a long run-…\"");
    // text within the limit is untouched
    let got = q("Short one. Done.", 16).await;
    assert_eq!(got, "    > Mel (2026-03-01): \"Short one. Done.\"");
}

#[tokio::test]
async fn test_context_quotes_never_displace_memory_lines() {
    let (plain, _) = quote_fx(0, 300);
    let (quoted, _) = quote_fx(3, 300);
    let none = SearchOptions::default();
    let base = plain.ret.context_for("Mel", Some(5), None, &none).await.unwrap();
    let base_lines = base.lines().count();
    // a budget that fits the memory lines exactly: no quote fits, every memory line is still there
    let budget = base.chars().count();
    let tight = quoted.ret.context_for("Mel", Some(5), Some(budget), &none).await.unwrap();
    assert_eq!(tight, base);
    // a little slack: fewer quotes than asked for, memory lines unchanged, never over budget
    let slack = quoted.ret.context_for("Mel", Some(5), Some(budget + 60), &none).await.unwrap();
    assert!(slack.chars().count() <= budget + 60);
    let kept: Vec<&str> = slack.lines().filter(|l| !l.starts_with("    > ")).collect();
    assert_eq!(kept.len(), base_lines);
    assert_eq!(kept, base.lines().collect::<Vec<_>>());
    assert!(slack.lines().any(|l| l.starts_with("    > ")));
    assert!(slack.lines().filter(|l| l.starts_with("    > ")).count() < 3);
}

#[test]
fn test_env_tokens_for_context_quotes() {
    let mut cfg = ChatMemoryConfig::default();
    cfg.apply_exp("context_quotes=3, context_quote_chars=200,context_k=20,context_max_chars=5000");
    assert_eq!(cfg.retrieval.context_quotes, 3);
    assert_eq!(cfg.retrieval.context_quote_chars, 200);
    assert_eq!(cfg.retrieval.context_k, 20);
    assert_eq!(cfg.retrieval.context_max_chars, 5000);
    assert!(cfg.retrieval.context_quotes_human_only);
    cfg.apply_exp("context_quotes_human_only=0");
    assert!(!cfg.retrieval.context_quotes_human_only);
    cfg.apply_exp("context_quotes_human_only=1");
    assert!(cfg.retrieval.context_quotes_human_only);
    // malformed / out-of-range tokens are ignored
    let mut cfg = ChatMemoryConfig::default();
    cfg.apply_exp("context_quotes=abc,context_k=0,context_max_chars,context_quote_chars=0,context_quotes_human_only=2");
    assert!(cfg.retrieval.context_quotes_human_only);
    assert_eq!(cfg.retrieval.context_quotes, 1);
    assert_eq!(cfg.retrieval.context_k, 15);
    assert_eq!(cfg.retrieval.context_max_chars, 6000);
    assert_eq!(cfg.retrieval.context_quote_chars, 300);
}

// ------------------------------------------------------------------------------------------ episodic recall

const RAW_HDR: &str = "Raw messages that match the question:";

/// The quote fixture plus three more chat messages that match `Q` but back no memory: two human ones (a short and
/// a long one) and one from the assistant.
fn raw_fx(cfg: RetrievalConfig) -> Fx {
    use neuralgraph_memory::store::AddMessage;
    let (f, _) = quote_fx_with(cfg);
    let add = |speaker: &str, role: &str, text: &str, at: &str| {
        let opts = AddMessage { role: role.into(), sent_at: Some(at.into()), ..Default::default() };
        f.store.add_message("c2", speaker, text, &opts).unwrap();
    };
    add("Mel", "user", "My sister asked where I live now,\nand I said Osaka.", "2026-04-05T10:00:00+00:00");
    add(
        "Mel",
        "user",
        &format!("Where I live matters. {}", "Filler words go here. ".repeat(10)),
        "2026-04-06T10:00:00+00:00",
    );
    add("Bot", "assistant", "Do you want me to note where Mel will live next year?", "2026-04-07T10:00:00+00:00");
    f
}

fn raw_cfg(n: usize) -> RetrievalConfig {
    RetrievalConfig { context_raw_messages: n, ..Default::default() }
}

fn raw_section(block: &str) -> Vec<&str> {
    let lines: Vec<&str> = block.lines().collect();
    match lines.iter().position(|l| *l == RAW_HDR) {
        Some(at) => lines[at + 1..].to_vec(),
        None => Vec::new(),
    }
}

#[tokio::test]
async fn test_context_raw_zero_is_unchanged_and_default_is_three() {
    let d = RetrievalConfig::default();
    assert_eq!((d.context_raw_messages, d.context_raw_message_chars, d.context_raw_include_assistant), (3, 400, false));
    let off = raw_fx(raw_cfg(0)).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert!(!off.contains(RAW_HDR));
    let (plain, _) = quote_fx(1, 300);
    let base = plain.ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    // the extra messages back no memory, so with raw recall off the block is the plain one
    assert_eq!(off, base);
}

#[tokio::test]
async fn test_context_raw_messages_format_and_order() {
    let on = raw_fx(raw_cfg(2)).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    let off = raw_fx(raw_cfg(0)).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    // memory and quote lines come first, untouched, then the raw section
    assert!(on.starts_with(&format!("{off}\n{RAW_HDR}\n")), "{on}");
    let raw = raw_section(&on);
    assert_eq!(raw.len(), 2, "{on}");
    assert!(raw.contains(&"- (Mel, 2026-04-05) \"My sister asked where I live now, and I said Osaka.\""), "{on}");
    assert!(raw.iter().all(|l| l.starts_with("- (Mel, 2026-04-0") && l.ends_with('"')), "{on}");
    // N=1 gives one line
    let one = raw_fx(raw_cfg(1)).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert_eq!(raw_section(&one).len(), 1);
    // truncation honours context_raw_message_chars (sentence boundary past half the limit, else hard cut)
    let cfg = RetrievalConfig { context_raw_messages: 5, context_raw_message_chars: 40, ..Default::default() };
    let cut = raw_fx(cfg).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    let long = raw_section(&cut).into_iter().find(|l| l.contains("Where I live matters")).unwrap();
    // the sentence ends at 21 chars, not past half of 40 => hard cut at 40
    assert_eq!(long, "- (Mel, 2026-04-06) \"Where I live matters. Filler words go he\u{2026}\"");
}

#[tokio::test]
async fn test_context_raw_dedupes_against_quotes() {
    let block = raw_fx(raw_cfg(10)).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    // "I live in Tokyo, in a small flat." is quoted under the memory (and its copy is the same text): not repeated
    let quoted = "I live in Tokyo, in a small flat.";
    assert!(block.lines().any(|l| l.starts_with("    > ") && l.contains(quoted)));
    assert!(raw_section(&block).iter().all(|l| !l.contains(quoted)), "{block}");
    assert!(!raw_section(&block).is_empty());
    // with quotes off nothing is quoted, so the same message may show up as a raw message
    let cfg = RetrievalConfig { context_quotes: 0, context_raw_messages: 10, ..Default::default() };
    let block = raw_fx(cfg).ret.context_for(Q, None, None, &SearchOptions::default()).await.unwrap();
    assert_eq!(raw_section(&block).iter().filter(|l| l.contains(quoted)).count(), 1, "{block}");
}

#[tokio::test]
async fn test_context_raw_assistant_filter_and_chat_speaker_filters() {
    let none = SearchOptions::default();
    let block = raw_fx(raw_cfg(10)).ret.context_for(Q, None, None, &none).await.unwrap();
    assert!(!raw_section(&block).iter().any(|l| l.contains("(Bot,")), "{block}");
    let cfg = RetrievalConfig { context_raw_messages: 10, context_raw_include_assistant: true, ..Default::default() };
    let block = raw_fx(cfg).ret.context_for(Q, None, None, &none).await.unwrap();
    assert!(
        raw_section(&block).contains(&"- (Bot, 2026-04-07) \"Do you want me to note where Mel will live next year?\""),
        "{block}"
    );
    // chat filter: another chat has no matching messages
    let other = SearchOptions { chat_id: Some("elsewhere".into()), ..Default::default() };
    let block = raw_fx(raw_cfg(10)).ret.context_for(Q, None, None, &other).await.unwrap();
    assert!(!block.contains(RAW_HDR));
    // speaker filter reaches the message store too
    let cfg = RetrievalConfig { context_raw_messages: 10, context_raw_include_assistant: true, ..Default::default() };
    let bot_only = SearchOptions { speaker: Some("Bot".into()), ..Default::default() };
    let f = raw_fx(cfg);
    let hits = f.ret.context_for(Q, None, None, &bot_only).await.unwrap();
    assert!(raw_section(&hits).iter().all(|l| l.starts_with("- (Bot,")), "{hits}");
}

#[tokio::test]
async fn test_context_raw_spends_leftover_budget_only() {
    let none = SearchOptions::default();
    let off = raw_fx(raw_cfg(0)).ret.context_for(Q, None, None, &none).await.unwrap();
    let budget = off.chars().count();
    // no room after memories and quotes: no header, no raw line, block unchanged
    let tight = raw_fx(raw_cfg(5)).ret.context_for(Q, None, Some(budget), &none).await.unwrap();
    assert_eq!(tight, off);
    // room for the header and one short message only: never over budget
    let slack = raw_fx(raw_cfg(5)).ret.context_for(Q, None, Some(budget + 130), &none).await.unwrap();
    assert!(slack.chars().count() <= budget + 130, "{slack}");
    assert!(slack.starts_with(&off));
    assert!(slack.contains(RAW_HDR), "{slack}");
    assert!(raw_section(&slack).len() < 3);
}

#[test]
fn test_env_tokens_for_context_raw() {
    let mut cfg = ChatMemoryConfig::default();
    cfg.apply_exp("context_raw_messages=3,context_raw_message_chars=250,context_raw_include_assistant=1");
    assert_eq!(cfg.retrieval.context_raw_messages, 3);
    assert_eq!(cfg.retrieval.context_raw_message_chars, 250);
    assert!(cfg.retrieval.context_raw_include_assistant);
    cfg.apply_exp("context_raw_include_assistant=0,context_raw_messages=0");
    assert!(!cfg.retrieval.context_raw_include_assistant);
    assert_eq!(cfg.retrieval.context_raw_messages, 0);
    let mut cfg = ChatMemoryConfig::default();
    cfg.apply_exp("context_raw_messages=x,context_raw_message_chars=0,context_raw_include_assistant=2");
    assert_eq!(cfg.retrieval.context_raw_messages, 3);
    assert_eq!(cfg.retrieval.context_raw_message_chars, 400);
    assert!(!cfg.retrieval.context_raw_include_assistant);
}

// ------------------------------------------------------------------------------------------ rule-based multi-query

#[test]
fn test_multi_query_rules_examples() {
    use neuralgraph_memory::retrieval::multi_query_rules;
    let cases: [(&str, &[&str]); 6] = [
        // (a) clause split, both sides keep >= 2 content words
        (
            "What is Ali's favourite hiking trail, and which pottery studio does Mel visit?",
            &["What is Ali's favourite hiking trail", "which pottery studio does Mel visit"],
        ),
        // (a) a short side is glued back, so no split
        ("Where does Mel work and live?", &[]),
        // (b) two anchors
        (
            "How many days passed between the day I bought the car and the day I sold it?",
            &["the day I bought the car", "the day I sold it", "How many days passed between the day I bought the car"],
        ),
        // (b) before/after one anchor
        ("What did I buy after I finished the pottery course?", &["I finished the pottery course"]),
        // (c) quantity: noun phrase alone
        ("How many hours of yoga did I do last week?", &["hours of yoga"]),
        // no rule applies
        ("Where does Mel live?", &[]),
    ];
    for (q, want) in cases {
        let got = multi_query_rules(q);
        eprintln!("{q:?} -> {got:?}");
        assert_eq!(got, want.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{q}");
    }
}

/// The seed corpus plus six extra "Mel lives ..." memories that crowd the first clause of `MQ_Q`.
fn mq_fx(multi: bool, context_k: usize) -> Fx {
    let f = fx();
    seed(&f.store);
    for (i, town) in ["Osaka", "Kyoto", "Nara", "Kobe", "Sapporo", "Nagoya"].iter().enumerate() {
        let text = format!("Where does Mel live? Mel lives in {town}, where Mel lives for the summer.");
        let m = emb_memory(&text, "mel", "c2", &format!("2026-04-1{i}T00:00:00+00:00"), "fact", 0.9);
        f.store.insert_memory(&m, &[], &["mel".to_string()]).unwrap();
    }
    let cfg = RetrievalConfig { context_multi_query: multi, context_k, context_raw_messages: 0, ..Default::default() };
    let ret = MemoryRetriever::new(f.store.clone(), Arc::new(FakeLlmClient::default()), cfg);
    Fx { ret, ..f }
}

const MQ_Q: &str = "Where does Mel live, and what cat does Ali have?";

#[tokio::test]
async fn test_context_multi_query_default_off_is_byte_identical() {
    assert!(!RetrievalConfig::default().context_multi_query);
    let off = mq_fx(false, 15);
    let plain_ret = MemoryRetriever::new(
        off.store.clone(),
        Arc::new(FakeLlmClient::default()),
        RetrievalConfig { context_raw_messages: 0, ..Default::default() },
    );
    for q in [MQ_Q, "Where does Mel live?", "How many hours of yoga did I do last week?", "Ali and Mel"] {
        let a = off.ret.context_for(q, None, None, &SearchOptions::default()).await.unwrap();
        let b = plain_ret.context_for(q, None, None, &SearchOptions::default()).await.unwrap();
        assert_eq!(a, b, "{q}");
    }
    // a question that yields no sub-query is unchanged with the switch on
    let on = mq_fx(true, 15);
    let q = "Where does Mel live?";
    let a = on.ret.context_for(q, None, None, &SearchOptions::default()).await.unwrap();
    let b = off.ret.context_for(q, None, None, &SearchOptions::default()).await.unwrap();
    assert_eq!(a, b);
}

#[tokio::test]
async fn test_context_multi_query_second_clause_memory_enters_block() {
    let off = mq_fx(false, 8);
    let on = mq_fx(true, 8);
    let block_off = off.ret.context_for(MQ_Q, None, None, &SearchOptions::default()).await.unwrap();
    let block_on = on.ret.context_for(MQ_Q, None, None, &SearchOptions::default()).await.unwrap();
    assert!(!block_off.contains("cat called Luna"));
    assert!(block_on.contains("cat called Luna"));
    assert!(block_on.contains("Mel lives in Kyoto"), "the first clause's memories stay");
    assert_eq!(block_on.lines().count(), 9, "header + context_k lines");
}

#[test]
fn test_env_tokens_for_context_multi_query() {
    let mut cfg = ChatMemoryConfig::default();
    assert!(!cfg.retrieval.context_multi_query);
    cfg.apply_exp("context_multi_query=1");
    assert!(cfg.retrieval.context_multi_query);
    cfg.apply_exp("context_multi_query=0");
    assert!(!cfg.retrieval.context_multi_query);
    cfg.apply_exp("context_multi_query");
    assert!(cfg.retrieval.context_multi_query);
    cfg.apply_exp("context_multi_query=2");
    assert!(cfg.retrieval.context_multi_query, "malformed value is ignored");
}

// ------------------------------------------------------------------------------------------ temporal prior

mod temporal_prior {
    use super::*;
    use chrono::NaiveDate;
    use neuralgraph_memory::temporal::{extract_query_time_cues, OrderCue};

    fn d(s: &str) -> NaiveDate {
        NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()
    }

    fn cues(q: &str) -> (Option<(String, String)>, Option<OrderCue>) {
        let c = extract_query_time_cues(q, d("2023-05-25")); // a Thursday
        (c.window.map(|(a, b)| (a.to_string(), b.to_string())), c.order)
    }

    fn win(a: &str, b: &str) -> Option<(String, String)> {
        Some((a.to_string(), b.to_string()))
    }

    #[test]
    fn test_cue_extractor_on_example_questions() {
        assert_eq!(cues("What did I buy in March?"), (win("2023-03-01", "2023-03-31"), None));
        assert_eq!(cues("What happened on April 10th?"), (win("2023-04-10", "2023-04-10"), None));
        assert_eq!(cues("Which restaurant did I visit on 2023-04-10?"), (win("2023-04-10", "2023-04-10"), None));
        assert_eq!(cues("Which trip did I take last week?"), (win("2023-05-15", "2023-05-21"), None));
        assert_eq!(cues("What did I start 3 months ago?"), (win("2023-02-10", "2023-03-12"), None));
        assert_eq!(cues("Where did I live in 2021?"), (win("2021-01-01", "2021-12-31"), None));
        assert_eq!(
            cues("What was the first game I played in June 2022?"),
            (win("2022-06-01", "2022-06-30"), Some(OrderCue::Earliest))
        );
        assert_eq!(cues("What is my most recent job title?"), (None, Some(OrderCue::Latest)));
        // more forms
        assert_eq!(cues("What did I eat yesterday?"), (win("2023-05-24", "2023-05-24"), None));
        assert_eq!(cues("What did I do two weeks ago?"), (win("2023-05-08", "2023-05-14"), None));
        assert_eq!(cues("How was my trip in December last year?").0, win("2022-12-01", "2022-12-31"));
        assert_eq!(cues("What did I do 10th of April 2022?").0, win("2022-04-10", "2022-04-10"));
        assert_eq!(cues("What did I read last month?").0, win("2023-04-01", "2023-04-30"));
        // a month in the future of `now` means last year's; a modal "may" and plain questions carry no cue
        assert_eq!(cues("What did I do in October?").0, win("2022-10-01", "2022-10-31"));
        assert_eq!(cues("May I ask what my favourite colour is?"), (None, None));
        assert_eq!(cues("Where do I work?"), (None, None));
        // first AND latest wording cancels
        assert_eq!(cues("What was the first and the latest book?").1, None);
    }

    const TP_Q: &str = "Ali visited the museum in March";

    /// Three identical-text memories: one whose event is in March 2026, one in August 2026 (more important, so it
    /// leads without the prior), and one with no event time observed in August.
    fn tp_fx(w: f64) -> (Fx, [String; 3]) {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(ChatMemoryStore::open(dir.path().join("t.db").to_str().unwrap()).unwrap());
        let cfg = RetrievalConfig { temporal_prior: w, ..Default::default() };
        let ret = MemoryRetriever::new(store.clone(), Arc::new(FakeLlmClient::default()), cfg);
        let text = "Ali visited the city museum.";
        let mut a = emb_memory(text, "ali", "c1", "2026-04-01T00:00:00+00:00", "event", 0.5);
        a.event_time = Some("2026-03-10".into());
        a.event_time_precision = "day".into();
        let mut b = emb_memory(text, "ali", "c1", "2026-09-01T00:00:00+00:00", "event", 0.9);
        b.event_time = Some("2026-08-02".into());
        b.event_time_precision = "day".into();
        let c = emb_memory(text, "ali", "c1", "2026-08-05T00:00:00+00:00", "event", 0.7);
        for m in [&a, &b, &c] {
            store.insert_memory(m, &[], &["ali".into()]).unwrap();
        }
        let ids = [a.memory_id.clone(), b.memory_id.clone(), c.memory_id.clone()];
        (Fx { _dir: dir, store, ret }, ids)
    }

    fn at(now: &str) -> SearchOptions {
        SearchOptions { k: 5, touch: false, now: Some(now.into()), ..Default::default() }
    }

    #[tokio::test]
    async fn test_memory_inside_the_window_overtakes_one_outside() {
        let (off, ids) = tp_fx(0.0);
        let base = off.ret.search(TP_Q, &at("2026-05-01")).await.unwrap();
        assert_eq!(base[0].memory.memory_id, ids[1], "without the prior the more important August memory leads");
        assert!(base.iter().all(|h| !h.channels.contains_key("temporal")));

        let (on, ids) = tp_fx(0.5);
        let hits = on.ret.search(TP_Q, &at("2026-05-01")).await.unwrap();
        assert_eq!(hits[0].memory.memory_id, ids[0], "the March memory is boosted past the August one");
        let ch = |id: &String| hits.iter().find(|h| &h.memory.memory_id == id).unwrap().channels.get("temporal").copied();
        assert_eq!(ch(&ids[0]), Some(1.5));
        assert_eq!(ch(&ids[1]), Some(0.8));
        assert_eq!(ch(&ids[2]), None, "no event time and observed outside the window: unchanged");
        assert!(hits[0].explanation.contains("temporal x1.50"), "{}", hits[0].explanation);
        // without `now` the clock is used: March of the current year or last, the 2026 memories are all outside
        // or all untouched, but the call must not fail
        on.ret.search(TP_Q, &SearchOptions { k: 5, touch: false, ..Default::default() }).await.unwrap();
    }

    #[tokio::test]
    async fn test_default_is_off_and_queries_without_cues_are_untouched() {
        assert_eq!(RetrievalConfig::default().temporal_prior, 0.0);
        let (def, _) = tp_fx(0.0);
        let (on, _) = tp_fx(0.5);
        let plain = SearchOptions { k: 5, touch: false, ..Default::default() };
        // `now` and cue words are ignored while the prior is off
        let a = def.ret.search(TP_Q, &plain).await.unwrap();
        let b = def.ret.search(TP_Q, &at("2026-05-01")).await.unwrap();
        let strip = |hs: &[neuralgraph_memory::RetrievedMemory]| {
            hs.iter().map(|h| (h.memory.memory_id.clone(), h.score, format!("{:?}", h.channels))).collect::<Vec<_>>()
        };
        // memory ids differ between fixtures, so compare within one fixture
        assert_eq!(strip(&a), strip(&b));
        // a question without time cues is identical with the prior on and off (scores, channels, explanations)
        let (on_hits, def_hits) = (on.ret.search("Ali visited the museum", &plain).await.unwrap(), {
            def.ret.search("Ali visited the museum", &plain).await.unwrap()
        });
        let shape = |hs: &[neuralgraph_memory::RetrievedMemory]| {
            hs.iter().map(|h| (h.score, format!("{:?}", h.channels), h.explanation.clone())).collect::<Vec<_>>()
        };
        assert_eq!(shape(&on_hits), shape(&def_hits));
    }

    #[tokio::test]
    async fn test_ordering_cue_tilts_toward_newest_or_oldest() {
        let (on, ids) = tp_fx(0.5);
        let opts = at("2026-12-01");
        let latest = on.ret.search("What is the most recent time Ali visited the museum", &opts).await.unwrap();
        let ch = |hs: &[neuralgraph_memory::RetrievedMemory], id: &String| {
            hs.iter().find(|h| &h.memory.memory_id == id).unwrap().channels.get("temporal").copied()
        };
        // oldest candidate (March event) gets no lift; the August event is 3 days short of the newest candidate
        assert_eq!(ch(&latest, &ids[0]), None);
        assert_eq!(ch(&latest, &ids[1]), Some(1.4899));
        assert_eq!(ch(&latest, &ids[2]), Some(1.5), "no event time: its observation time orders it");
        let earliest = on.ret.search("What is the earliest time Ali visited the museum", &opts).await.unwrap();
        assert_eq!(ch(&earliest, &ids[0]), Some(1.5));
        assert_eq!(ch(&earliest, &ids[1]), Some(1.0101));
        assert_eq!(ch(&earliest, &ids[2]), None);
    }

    #[test]
    fn test_env_token_for_temporal_prior() {
        let mut cfg = ChatMemoryConfig::default();
        assert_eq!(cfg.retrieval.temporal_prior, 0.0);
        cfg.apply_exp("temporal_prior=0.5");
        assert_eq!(cfg.retrieval.temporal_prior, 0.5);
        cfg.apply_exp("temporal_prior=abc,temporal_prior=-1,temporal_prior");
        assert_eq!(cfg.retrieval.temporal_prior, 0.5, "malformed values are ignored");
        cfg.apply_exp("temporal_prior=0");
        assert_eq!(cfg.retrieval.temporal_prior, 0.0);
    }
}
