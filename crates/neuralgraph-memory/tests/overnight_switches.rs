//! The default-off experiment switches (`ExtractionConfig::{hyphen_ground, numeric_dedupe_guard,
//! annotate_more_relative, memory_prompt_extra, retry_on_zero_memories, verbatim_fallback}` and the
//! `NEURALGRAPH_EXP` parser): each one changes behaviour only when on, and the default stays as before.

use neuralgraph_memory::extraction::{ExtractionConfig, MemoryExtractor};
use neuralgraph_memory::llm::{FakeLlmClient, ScriptResponse};
use neuralgraph_memory::service::ChatMemoryConfig;
use neuralgraph_memory::store::{AddMessage, ChatMemoryStore};
use neuralgraph_memory::{ChatMessage, ExtractionPlan};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

fn store() -> (tempfile::TempDir, Arc<ChatMemoryStore>) {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(ChatMemoryStore::open(dir.path().join("t.db").to_str().unwrap()).unwrap());
    (dir, store)
}

fn add(store: &ChatMemoryStore, chat: &str, text: &str, sent_at: &str) -> ChatMessage {
    let opts = AddMessage { sent_at: Some(sent_at.into()), ..Default::default() };
    store.add_message(chat, "Ali", text, &opts).unwrap().0
}

fn mem(text: &str, sources: Vec<usize>) -> Value {
    json!({"text": text, "kind": "fact", "subject": "Ali", "when": null, "importance": 0.7, "confidence": 0.9,
           "sources": sources, "entities": []})
}

fn llm(memories: Value, reconcile: Value) -> Arc<FakeLlmClient> {
    Arc::new(FakeLlmClient::with_script(vec![
        ("You maintain the long-term memory", json!({ "memories": memories }).to_string()),
        ("You are building a knowledge graph", json!({"entities": [], "triples": []}).to_string()),
        ("You maintain a memory store", reconcile.to_string()),
    ]))
}

async fn plan(
    store: &Arc<ChatMemoryStore>,
    llm: &Arc<FakeLlmClient>,
    cfg: ExtractionConfig,
    msgs: &[ChatMessage],
) -> ExtractionPlan {
    MemoryExtractor::new(store.clone(), llm.clone(), cfg).build_plan(msgs, &[], 1).await.unwrap()
}

fn texts(p: &ExtractionPlan) -> Vec<String> {
    p.new_memories.iter().map(|(m, _, _)| m.text.clone()).collect()
}

fn rejected(p: &ExtractionPlan, reason: &str) -> bool {
    p.audit.iter().any(|(k, _, d)| k == "reject" && d["reason"] == reason)
}

// ------------------------------------------------------------------------------------------ lever 5

#[tokio::test]
async fn hyphenated_names_are_grounded_only_with_the_switch() {
    let (_d, s) = store();
    let m = add(&s, "c", "I saw a Red-headed Woodpecker at the Perky-Pet feeder.", "2023-05-25T10:00:00+00:00");
    let text = "Ali saw a Red-headed Woodpecker at the Perky-Pet feeder.";
    let l = llm(json!([mem(text, vec![1])]), json!({"decision": "ADD", "target": null}));
    let off = plan(&s, &l, ExtractionConfig::default(), std::slice::from_ref(&m)).await;
    assert!(texts(&off).is_empty() && rejected(&off, "unknown_name"));
    let on = plan(&s, &l, ExtractionConfig { hyphen_ground: true, ..Default::default() }, &[m]).await;
    assert_eq!(texts(&on), [text]);
}

#[tokio::test]
async fn hyphen_switch_still_rejects_names_absent_from_the_window() {
    let (_d, s) = store();
    let m = add(&s, "c", "I saw a Red-headed Woodpecker.", "2023-05-25T10:00:00+00:00");
    let l = llm(json!([mem("Ali saw a Blue-headed Zorblax.", vec![1])]), json!({"decision": "ADD", "target": null}));
    let p = plan(&s, &l, ExtractionConfig { hyphen_ground: true, ..Default::default() }, &[m]).await;
    assert!(texts(&p).is_empty() && rejected(&p, "unknown_name"));
}

// ------------------------------------------------------------------------------------------ lever 6

/// Stores "needs 120 stars" and returns the plan for a later "needs 125 stars" under `cfg`.
async fn stars_plan(cfg: ExtractionConfig, verdict: &str) -> (ExtractionPlan, Arc<FakeLlmClient>) {
    let (_d, s) = store();
    let first = add(&s, "c1", "I need 120 stars for the badge.", "2023-05-01T10:00:00+00:00");
    let l1 =
        llm(json!([mem("Ali needs 120 stars for the badge.", vec![1])]), json!({"decision": "ADD", "target": null}));
    s.apply_plan(&plan(&s, &l1, ExtractionConfig::default(), &[first]).await, None).unwrap();
    let second = add(&s, "c2", "I need 125 stars for the badge.", "2023-05-20T10:00:00+00:00");
    let l2 = llm(
        json!([mem("Ali needs 125 stars for the badge.", vec![1])]),
        json!({"decision": verdict, "target": "A", "text": null}),
    );
    (plan(&s, &l2, cfg, &[second]).await, l2)
}

fn near_dup_cfg(guard: bool) -> ExtractionConfig {
    // the fake embeddings are lexical: a low dedupe threshold makes the one-digit change a "cosine duplicate"
    ExtractionConfig { dedupe_cosine: 0.5, reconcile_cosine: 0.3, numeric_dedupe_guard: guard, ..Default::default() }
}

#[tokio::test]
async fn different_numbers_are_dropped_as_duplicate_by_default() {
    let (p, l) = stars_plan(near_dup_cfg(false), "ADD").await;
    assert!(texts(&p).is_empty() && p.duplicate_sources.len() == 1);
    assert!(!l.prompts().iter().any(|x| x.contains("You maintain a memory store")));
}

#[tokio::test]
async fn numeric_guard_sends_different_numbers_to_reconcile() {
    let (p, l) = stars_plan(near_dup_cfg(true), "ADD").await;
    assert_eq!(texts(&p), ["Ali needs 125 stars for the badge."]);
    assert!(p.duplicate_sources.is_empty());
    let reconcile = l.prompts().into_iter().find(|x| x.contains("You maintain a memory store")).unwrap();
    assert!(reconcile.contains("NOTE: if NEW and an existing memory give different numbers"));
    assert!(reconcile.trim_end().ends_with(r#""reason": "short"}"#), "the suffix sits before the JSON line");
}

#[tokio::test]
async fn numeric_guard_turns_a_duplicate_verdict_on_new_numbers_into_update() {
    let (p, _) = stars_plan(near_dup_cfg(true), "DUPLICATE").await;
    assert_eq!(texts(&p), ["Ali needs 125 stars for the badge."]);
    assert_eq!(p.supersedes.len(), 1);
}

#[tokio::test]
async fn numeric_guard_keeps_true_duplicates() {
    let (_d, s) = store();
    let a = add(&s, "c1", "I need 120 stars for the badge.", "2023-05-01T10:00:00+00:00");
    let l =
        llm(json!([mem("Ali needs 120 stars for the badge.", vec![1])]), json!({"decision": "ADD", "target": null}));
    s.apply_plan(&plan(&s, &l, ExtractionConfig::default(), &[a]).await, None).unwrap();
    let b = add(&s, "c2", "I need 120 stars for this badge.", "2023-05-20T10:00:00+00:00");
    let l =
        llm(json!([mem("Ali needs 120 stars for this badge.", vec![1])]), json!({"decision": "ADD", "target": null}));
    let p = plan(&s, &l, near_dup_cfg(true), &[b]).await;
    assert!(texts(&p).is_empty() && p.duplicate_sources.len() == 1);
}

#[tokio::test]
async fn numeric_guard_ignores_a_candidate_without_digits() {
    // lme50_E56 4adc0475: "scored several goals" (no digits) must not supersede "scored 3 goals"
    let (_d, s) = store();
    let a = add(&s, "c1", "I scored 3 goals in the match.", "2023-05-01T10:00:00+00:00");
    let l = llm(json!([mem("Ali scored 3 goals in the match.", vec![1])]), json!({"decision": "ADD", "target": null}));
    s.apply_plan(&plan(&s, &l, ExtractionConfig::default(), &[a]).await, None).unwrap();
    let b = add(&s, "c2", "I scored several goals in the match.", "2023-05-20T10:00:00+00:00");
    // plain cosine duplicate: dropped, not sent to reconcile
    let l = llm(
        json!([mem("Ali scored several goals in the match.", vec![1])]),
        json!({"decision": "DUPLICATE", "target": "A", "text": null}),
    );
    let p = plan(&s, &l, near_dup_cfg(true), std::slice::from_ref(&b)).await;
    assert!(texts(&p).is_empty() && p.supersedes.is_empty() && p.duplicate_sources.len() == 1);
    // reconcile path: the model's DUPLICATE verdict is kept, not overridden to UPDATE
    let cfg = ExtractionConfig { dedupe_cosine: 0.99, reconcile_cosine: 0.3, ..Default::default() };
    let p = plan(&s, &l, cfg, &[b]).await;
    assert!(texts(&p).is_empty() && p.supersedes.is_empty() && p.duplicate_sources.len() == 1);
}

// ------------------------------------------------------------------------------------------ lever 4

#[tokio::test]
async fn months_ago_is_annotated_in_the_prompt_only_with_the_switch() {
    let (_d, s) = store();
    let m = add(&s, "c", "I started watching stand-up three months ago and 2 weeks ago.", "2023-05-25T10:00:00+00:00");
    let l = llm(json!([]), json!({}));
    plan(&s, &l, ExtractionConfig::default(), std::slice::from_ref(&m)).await;
    plan(&s, &l, ExtractionConfig { annotate_more_relative: true, ..Default::default() }, &[m]).await;
    let memory_prompts: Vec<String> =
        l.prompts().into_iter().filter(|p| p.starts_with("You maintain the long-term memory")).collect();
    assert_eq!(memory_prompts.len(), 2);
    assert!(!memory_prompts[0].contains("three months ago [="));
    assert!(memory_prompts[1].contains("three months ago [= February 2023]"));
    assert_eq!(memory_prompts[1].matches("weeks ago [=").count(), 1, "the first pass is not annotated twice");
}

// ------------------------------------------------------------------------------------------ lever 7/8

#[tokio::test]
async fn prompt_extra_goes_before_chat_participants() {
    let (_d, s) = store();
    let m = add(&s, "c", "I have a cat called Luna.", "2023-05-25T10:00:00+00:00");
    let l = llm(json!([]), json!({}));
    plan(&s, &l, ExtractionConfig::default(), std::slice::from_ref(&m)).await;
    let cfg = ExtractionConfig { memory_prompt_extra: "\n10. Copy numbers verbatim.\n".into(), ..Default::default() };
    plan(&s, &l, cfg, &[m]).await;
    let p = l.prompts();
    let (off, on) = (&p[0], &p[2]);
    assert!(!off.contains("10. Copy numbers"));
    assert!(on.contains("{\"memories\": []}.\n10. Copy numbers verbatim.\n\nCHAT PARTICIPANTS:"), "{on}");
    assert_eq!(on.replace("10. Copy numbers verbatim.\n", ""), off.as_str());
}

fn retry_llm(calls: Arc<AtomicUsize>) -> Arc<FakeLlmClient> {
    let memories = json!({"memories": [mem("Ali was shown a list of three videos about birds.", vec![1])]}).to_string();
    let responder: neuralgraph_memory::llm::Responder = Arc::new(move |prompt: &str| {
        if prompt.starts_with("You maintain the long-term memory") {
            calls.fetch_add(1, Ordering::SeqCst);
            Ok(if prompt.contains("DO contain facts") { memories.clone() } else { r#"{"memories": []}"#.into() })
        } else {
            Ok(r#"{"entities": [], "triples": [], "decision": "ADD", "target": null}"#.into())
        }
    });
    Arc::new(FakeLlmClient { script: vec![("".into(), ScriptResponse::Func(responder))], ..Default::default() })
}

#[tokio::test]
async fn zero_memory_batches_are_asked_once_more_only_with_the_switch() {
    let (_d, s) = store();
    let a =
        add(&s, "c", "Here is the list of three videos about birds that you asked for.", "2023-05-25T10:00:00+00:00");
    let b = add(
        &s,
        "c",
        "And a second message that is also long enough to count as substantive.",
        "2023-05-25T10:00:01+00:00",
    );
    let calls = Arc::new(AtomicUsize::new(0));
    let l = retry_llm(calls.clone());
    let off = plan(&s, &l, ExtractionConfig::default(), &[a.clone(), b.clone()]).await;
    assert!(texts(&off).is_empty());
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let on =
        plan(&s, &l, ExtractionConfig { retry_on_zero_memories: true, ..Default::default() }, &[a.clone(), b]).await;
    assert_eq!(texts(&on), ["Ali was shown a list of three videos about birds."]);
    assert_eq!(calls.load(Ordering::SeqCst), 3, "one first ask plus exactly one retry");
    // a single message is never retried
    let one = ExtractionConfig { retry_on_zero_memories: true, ..Default::default() };
    assert!(texts(&plan(&s, &l, one, &[a]).await).is_empty());
    assert_eq!(calls.load(Ordering::SeqCst), 4);
}

// ------------------------------------------------------------------------------------------ lever 3

#[tokio::test]
async fn verbatim_fallback_stores_sentences_with_uncovered_numbers() {
    let (_d, s) = store();
    let m = add(
        &s,
        "c",
        "I walk my dog every morning. The taxi to the airport cost $75 last Friday. I paid 1,300 for the course. I live in Berlin at number 12.",
        "2023-05-25T10:00:00+00:00",
    );
    let l = llm(json!([mem("Ali lives in Berlin at number 12.", vec![1])]), json!({"decision": "ADD", "target": null}));
    let off = plan(&s, &l, ExtractionConfig::default(), std::slice::from_ref(&m)).await;
    assert_eq!(texts(&off).len(), 1);
    let cfg = ExtractionConfig { verbatim_fallback: true, verbatim_fallback_max_per_batch: 1, ..Default::default() };
    let capped = plan(&s, &l, cfg, std::slice::from_ref(&m)).await;
    assert_eq!(texts(&capped).len(), 2, "cap of one fallback");
    let cfg = ExtractionConfig { verbatim_fallback: true, ..Default::default() };
    let on = plan(&s, &l, cfg, &[m.clone()]).await;
    assert_eq!(
        texts(&on),
        [
            "Ali lives in Berlin at number 12.",
            "Ali said: \"The taxi to the airport cost $75 last Friday.\"",
            "Ali said: \"I paid 1,300 for the course.\"",
        ]
    );
    let fb = &on.new_memories[1].0;
    assert_eq!((fb.kind.as_str(), fb.importance, fb.confidence), ("fact", 0.35, 0.9));
    assert_eq!(on.new_memories[1].1, [m.message_id]);
}

// ------------------------------------------------------------------------------------------ env parsing

#[test]
fn neuralgraph_exp_tokens_set_the_matching_fields() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("extra.txt");
    std::fs::write(&file, "10. Be exact.").unwrap();
    let mut c = ChatMemoryConfig::default();
    c.apply_exp(&format!(
        "hyphen_ground, num_dedupe,months_ago,retry_zero,verbatim_fallback,verbatim_max=5,batch_size=1,context_messages=10,prompt_extra_file={}",
        file.display()
    ));
    let e = &c.extraction;
    assert!(e.hyphen_ground && e.numeric_dedupe_guard && e.annotate_more_relative);
    assert!(e.retry_on_zero_memories && e.verbatim_fallback);
    assert_eq!((e.verbatim_fallback_max_per_batch, e.context_messages, c.worker.batch_size), (5, 10, 1));
    assert_eq!(e.memory_prompt_extra, "10. Be exact.");
}

#[test]
fn neuralgraph_exp_zero_turns_fixes_off_and_one_or_bare_turns_them_on() {
    // independent of the defaults: `=0` is false, `=1` and the bare token are true
    let mut c = ChatMemoryConfig::default();
    c.apply_exp("hyphen_ground=0,num_dedupe=0");
    assert!(!c.extraction.hyphen_ground && !c.extraction.numeric_dedupe_guard);
    c.apply_exp("hyphen_ground=1,num_dedupe");
    assert!(c.extraction.hyphen_ground && c.extraction.numeric_dedupe_guard);
    c.apply_exp("hyphen_ground=0,num_dedupe=0");
    assert!(!c.extraction.hyphen_ground && !c.extraction.numeric_dedupe_guard);
    c.apply_exp("hyphen_ground,num_dedupe=1");
    assert!(c.extraction.hyphen_ground && c.extraction.numeric_dedupe_guard);

    c.apply_exp("months_ago=1,retry_zero=1,verbatim_fallback=1");
    let e = &c.extraction;
    assert!(e.annotate_more_relative && e.retry_on_zero_memories && e.verbatim_fallback);
    c.apply_exp("months_ago=0,retry_zero=0,verbatim_fallback=0");
    let e = &c.extraction;
    assert!(!e.annotate_more_relative && !e.retry_on_zero_memories && !e.verbatim_fallback);
}

#[test]
fn neuralgraph_exp_ignores_unknown_and_malformed_tokens() {
    let mut c = ChatMemoryConfig::default();
    c.apply_exp("nonsense,verbatim_max=abc,batch_size=0,months_ago=2,hyphen_ground=yes,prompt_extra_file=/no/such/file,,");
    let reference = ChatMemoryConfig::default();
    let e = &c.extraction;
    assert!(!e.annotate_more_relative && e.memory_prompt_extra.is_empty());
    assert!(!e.hyphen_ground, "hyphen_ground=yes is malformed: the default (off) stays");
    assert_eq!(e.verbatim_fallback_max_per_batch, reference.extraction.verbatim_fallback_max_per_batch);
    assert_eq!(c.worker.batch_size, reference.worker.batch_size);
}

#[test]
fn switches_default_off() {
    let e = ExtractionConfig::default();
    assert!(!e.hyphen_ground && !e.numeric_dedupe_guard && !e.annotate_more_relative);
    assert!(!e.retry_on_zero_memories && !e.verbatim_fallback && e.memory_prompt_extra.is_empty());
    assert_eq!(e.verbatim_fallback_max_per_batch, 3);
}
