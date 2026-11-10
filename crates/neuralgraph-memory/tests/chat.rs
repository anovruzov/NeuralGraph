//! Chat: citations, follow-up search queries, the answer prompt, direct queries with no model, and a full reply with
//! evidence and stats against a scripted model.

mod common;

use neuralgraph_memory::chat::{answer_prompt, cited_numbers, reply, retrieval_query};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, RememberOptions};
use neuralgraph_memory::testing::scripted_fake_llm;
use std::sync::Arc;

#[test]
fn citations_are_single_or_grouped() {
    assert_eq!(cited_numbers("Lyon [2]. Tea [1][2], cats [3, 5].", 5), [1, 2, 3, 5]);
    assert_eq!(
        cited_numbers("See [9] and [0] and [abc].", 5),
        Vec::<usize>::new(),
        "numbers outside the evidence are ignored"
    );
}

#[test]
fn short_follow_ups_borrow_the_previous_question() {
    let h = vec![("What cameras do I own?".to_string(), "Three.".to_string())];
    assert_eq!(retrieval_query("and the oldest?", &h), "What cameras do I own? and the oldest?");
    assert_eq!(
        retrieval_query("Which of them shoots raw video on the weekend trips?", &h),
        "What cameras do I own? Which of them shoots raw video on the weekend trips?"
    );
    let standalone = "Where does my sister Lea live and what is her job?";
    assert_eq!(retrieval_query(standalone, &h), standalone);
    assert_eq!(retrieval_query("and the oldest?", &[]), "and the oldest?");
}

#[tokio::test]
async fn replies_carry_evidence_and_stats() {
    let dir = tempfile::tempdir().unwrap();
    let cm = ChatMemory::open(
        dir.path().join("m.db").to_str().unwrap(),
        Some(Arc::new(scripted_fake_llm())),
        ChatMemoryConfig::default(),
    )
    .unwrap();
    let ali = RememberOptions { subject: "Ali".into(), chat_id: "c1".into(), ..Default::default() };
    cm.remember("Ali has a cat called Luna.", &ali).await.unwrap();
    cm.remember("Ali lives in Berlin.", &ali).await.unwrap();

    // search only: the memories themselves, no model call
    let r = reply(&cm, "Luna", false, &[]).await.unwrap();
    assert_eq!(r["kind"], "search");
    assert_eq!(r["stats"]["model_calls"], 0);
    let ev = r["evidence"].as_array().unwrap();
    assert!(ev.iter().any(|e| e["text"] == "Ali has a cat called Luna."), "{ev:?}");
    assert!(ev[0]["n"] == 1 && ev[0]["chat_id"] == "c1" && ev[0]["date"].as_str().unwrap().len() == 10);
    assert!(r["stats"]["retrieval_ms"].is_number() && r["stats"]["memories_searched"] == 2);

    // an answer: the prompt holds the numbered memories; the reply has evidence and both timings
    let results = cm.search("Where does Ali live?", &Default::default()).await.unwrap();
    let prompt = answer_prompt("Where does Ali live?", &results, &[("q0".into(), "a0".into())], "2026-11-10");
    assert!(prompt.contains("[1] (") && prompt.contains("Ali lives in Berlin."));
    assert!(prompt.contains("EARLIER QUESTION: q0") && prompt.ends_with("QUESTION: Where does Ali live?\nANSWER:"));
    let r = reply(&cm, "Where does Ali live?", true, &[]).await.unwrap();
    assert!(["answer", "search"].contains(&r["kind"].as_str().unwrap()));
    assert!(r["stats"]["answer_ms"].is_number());
    assert_eq!(r["stats"]["mode"], "ai");
    assert!(!r["evidence"].as_array().unwrap().is_empty());

    // paused: no model, still the matching memories
    cm.store.set_paused(true).unwrap();
    let r = reply(&cm, "Berlin", true, &[]).await.unwrap();
    assert_eq!(r["kind"], "search");
    assert_eq!(r["stats"]["mode"], "paused");
    assert!(r["note"].as_str().unwrap().contains("paused"));
    cm.store.set_paused(false).unwrap();

    // direct queries
    let r = reply(&cm, "/who Ali", true, &[]).await.unwrap();
    assert_eq!(r["kind"], "entity");
    assert_eq!(r["evidence"].as_array().unwrap().len(), 2);
    let r = reply(&cm, "/recent 1", true, &[]).await.unwrap();
    assert_eq!(r["evidence"].as_array().unwrap().len(), 1);
    let r = reply(&cm, "/stats", true, &[]).await.unwrap();
    assert!(r["answer"].as_str().unwrap().contains("2 memories"));
    assert_eq!(reply(&cm, "/nonsense", true, &[]).await.unwrap()["kind"], "help");
    cm.close().await;
}
