//! Extraction-layer tests: JSON parsing of small-model output, the chit-chat gate, entity alias resolution,
//! temporal normalisation, grounding validation, and the reconcile decisions (ADD / DUPLICATE / UPDATE /
//! CONTRADICT) driven by a scripted fake LLM. Ported from `test_chat_memory_extraction.py`.

mod common;

use neuralgraph_memory::error::MemoryError;
use neuralgraph_memory::extraction::{parse_when, EntityRegistry, ExtractionConfig, MemoryExtractor};
use neuralgraph_memory::jsonutil::{parse_json_array, parse_json_object};
use neuralgraph_memory::llm::{fake_embedding, FakeLlmClient};
use neuralgraph_memory::store::{AddMessage, ChatMemoryStore, MemoryQuery};
use neuralgraph_memory::textutil::{is_probable_chitchat, norm_entity, norm_relation, stem, tokenize};
use neuralgraph_memory::ChatMessage;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::Arc;

// ---------------------------------------------------------------------------------------- json / text

#[test]
fn test_fenced_prefixed_trailing_commas() {
    let raw = "Sure! Here is the JSON:\n```json\n{\"memories\": [{\"text\": \"a\",}, ],}\n```\nHope this helps.";
    assert_eq!(Value::Object(parse_json_object(raw).unwrap()), json!({"memories": [{"text": "a"}]}));
}

#[test]
fn test_think_blocks_and_truncation() {
    let raw = r#"<think>reasoning</think>{"memories": [{"text": "one"}, {"text": "tw"#;
    assert_eq!(Value::Object(parse_json_object(raw).unwrap()), json!({"memories": [{"text": "one"}, {"text": "tw"}]}));
}

#[test]
fn test_array_and_garbage() {
    assert_eq!(parse_json_array(r#"[["a","b","c"]] trailing"#).unwrap(), vec![json!(["a", "b", "c"])]);
    assert!(parse_json_object("no json here").is_none());
    assert!(parse_json_array("{}").is_none());
}

#[test]
fn test_nested_braces_in_strings() {
    let raw = r#"{"text": "he said {hi} and \"bye\"", "n": 1}"#;
    assert_eq!(Value::Object(parse_json_object(raw).unwrap()), json!({"text": "he said {hi} and \"bye\"", "n": 1}));
}

#[test]
fn test_chitchat_gate() {
    for s in ["ok", "thanks!", "lol", "Hi!", "Sure! Let me know if you need anything else.", "good morning", ""] {
        assert!(is_probable_chitchat(s), "{s}");
    }
    for s in ["I moved to Berlin", "My cat is called Luna", "42", "Can you remind me what Mel said about Tokyo?"] {
        assert!(!is_probable_chitchat(s), "{s}");
    }
}

#[test]
fn test_normalisation() {
    assert_eq!(norm_entity("  Melanie's "), "melanie");
    assert_eq!(norm_entity("São Paulo"), "sao paulo");
    assert_eq!(norm_entity("The Beatles'"), "beatles");
    assert_eq!(norm_relation("Lives In"), "lives_in");
    assert_eq!(tokenize("I moved to Berlin in March 2024, it's great"), ["move", "berlin", "march", "2024"]);
    for (w, expect) in [
        ("lives", "live"),
        ("running", "run"),
        ("cities", "city"),
        ("visited", "visit"),
        ("planned", "plan"),
        ("buses", "bus"),
        ("glass", "glass"),
        ("hated", "hate"),
        ("cooking", "cook"),
    ] {
        assert_eq!(stem(w), expect, "{w}");
    }
}

#[test]
fn test_fake_embedding_is_deterministic_and_lexical() {
    let (a, b, c) = (
        fake_embedding("Ali moved to Berlin", 256),
        fake_embedding("Ali moved to Berlin", 256),
        fake_embedding("weather nice", 256),
    );
    assert_eq!(a, b);
    let dot = |x: &[f64], y: &[f64]| x.iter().zip(y).map(|(i, j)| i * j).sum::<f64>();
    assert!((dot(&a, &a) - 1.0).abs() < 1e-6);
    assert!(dot(&a, &c) < 0.5);
}

// ---------------------------------------------------------------------------------------- when parsing

fn when(s: &str, at: Option<&str>) -> (Option<String>, String) {
    parse_when(s, at)
}

#[test]
fn test_absolute_forms() {
    assert_eq!(when("2024-03-15", None), (Some("2024-03-15".into()), "day".into()));
    assert_eq!(when("2024-03", None), (Some("2024-03".into()), "month".into()));
    assert_eq!(when("2024", None), (Some("2024".into()), "year".into()));
    assert_eq!(when("March 2024", None), (Some("2024-03".into()), "month".into()));
    assert_eq!(when("8 May 2023", None), (Some("2023-05-08".into()), "day".into()));
    assert_eq!(when("May 8, 2023", None), (Some("2023-05-08".into()), "day".into()));
    assert_eq!(when("null", None), (None, "none".into()));
    assert_eq!(when("2024-13-01", None), (None, "none".into()));
}

#[test]
fn test_relative_forms_resolve_against_message_date() {
    assert_eq!(when("yesterday", Some("2026-09-17T10:00:00+00:00")), (Some("2026-09-16".into()), "day".into()));
    assert_eq!(when("next summer", Some("2026-09-17T10:00:00+00:00")), (None, "none".into()));
}

// ---------------------------------------------------------------------------------------- registry

#[test]
fn test_alias_prefix_fuzzy_and_first_person() {
    let mut r = EntityRegistry::default();
    r.add("melanie carter", "Melanie Carter", "person", 10, &["mel".into()]);
    r.add("berlin", "Berlin", "place", 5, &[]);
    assert_eq!(r.resolve_str("Mel", None, "", true).as_deref(), Some("melanie carter"));
    assert_eq!(r.resolve_str("Melanie", None, "", true).as_deref(), Some("melanie carter"));
    assert_eq!(r.resolve_str("melanie carter's", None, "", true).as_deref(), Some("melanie carter"));
    assert_eq!(r.resolve_str("I", Some("ali"), "", true).as_deref(), Some("ali"));
    assert_eq!(r.resolve_str("Berlín", None, "", true).as_deref(), Some("berlin"));
    assert_eq!(r.resolve_str("Tokyo", None, "city", true).as_deref(), Some("tokyo"));
    assert_eq!(r.new_entities["tokyo"].etype, "place");
    assert_eq!(r.resolve_str("May", None, "", true), None, "month names never become entities");
    assert_eq!(r.resolve_str("2024", None, "", true), None);
    assert_eq!(r.resolve_str("", None, "", true), None);
}

#[test]
fn test_ambiguous_first_name_is_not_forced() {
    let mut r = EntityRegistry::default();
    r.add("alex smith", "Alex Smith", "person", 3, &[]);
    r.add("alex jones", "Alex Jones", "person", 3, &[]);
    assert_eq!(
        r.resolve_str("Alex", None, "", true).as_deref(),
        Some("alex"),
        "two candidates -> new entity rather than a guess"
    );
}

// ---------------------------------------------------------------------------------------- extractor

fn memory_json() -> Value {
    json!({"memories": [
        {"text": "Ali moved to Berlin in March 2026.", "kind": "event", "subject": "Ali", "when": "2026-03", "importance": 0.9,
         "confidence": 1.0, "sources": [1], "entities": [{"name": "Berlin", "type": "place"}]},
        {"text": "Ali has a cat called Luna.", "kind": "fact", "subject": "Ali", "when": null, "importance": 0.7,
         "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]},
        {"text": "Ali is tired today.", "kind": "other", "subject": "Ali", "when": null, "importance": 0.1, "confidence": 0.9, "sources": [1]},
        {"text": "Ali met Zorblax Quintrell at the market.", "kind": "event", "subject": "Ali", "when": null, "importance": 0.6, "confidence": 0.9, "sources": [1]},
        {"text": "The assistant recommends visiting museums.", "kind": "other", "subject": "assistant", "importance": 0.5, "confidence": 0.9, "sources": [2]},
    ]})
}

fn relation_json() -> Value {
    json!({"entities": [{"name": "Berlin", "type": "place"}, {"name": "Luna", "type": "pet"}],
           "triples": [{"s": "I", "r": "lives in", "o": "Berlin", "source": 1, "confidence": 0.9},
                       {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.9},
                       {"s": "Ali", "r": "has_pet", "o": "Luna", "source": 1, "confidence": 0.5}]})
}

fn make_llm(memories: Value, relations: Value, reconcile: Option<Value>) -> Arc<FakeLlmClient> {
    Arc::new(FakeLlmClient::with_script(vec![
        ("You maintain the long-term memory", memories.to_string()),
        ("You are building a knowledge graph", relations.to_string()),
        ("You maintain a memory store", reconcile.unwrap_or(json!({"decision": "ADD", "target": null})).to_string()),
    ]))
}

fn default_llm() -> Arc<FakeLlmClient> {
    make_llm(memory_json(), relation_json(), None)
}

struct Fx {
    _dir: tempfile::TempDir,
    store: Arc<ChatMemoryStore>,
}

fn fx() -> Fx {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(ChatMemoryStore::open(dir.path().join("t.db").to_str().unwrap()).unwrap());
    Fx { _dir: dir, store }
}

fn add(
    store: &ChatMemoryStore,
    chat: &str,
    speaker: &str,
    text: &str,
    role: &str,
    sent_at: Option<&str>,
) -> ChatMessage {
    let opts = AddMessage { role: role.into(), sent_at: sent_at.map(str::to_string), ..Default::default() };
    store.add_message(chat, speaker, text, &opts).unwrap().0
}

fn seed(store: &ChatMemoryStore) -> (ChatMessage, ChatMessage) {
    let m1 = add(
        store,
        "c1",
        "Ali",
        "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.",
        "",
        Some("2026-04-01T10:00:00+00:00"),
    );
    let m2 = add(
        store,
        "c1",
        "assistant",
        "Berlin has great museums, you should visit them.",
        "assistant",
        Some("2026-04-01T10:00:10+00:00"),
    );
    (m1, m2)
}

fn ex(store: &Arc<ChatMemoryStore>, llm: &Arc<FakeLlmClient>, cfg: ExtractionConfig) -> MemoryExtractor {
    MemoryExtractor::new(store.clone(), llm.clone(), cfg)
}

fn reasons(plan: &neuralgraph_memory::ExtractionPlan) -> Vec<String> {
    plan.audit
        .iter()
        .filter(|(k, _, _)| k == "reject")
        .map(|(_, _, d)| d["reason"].as_str().unwrap_or_default().to_string())
        .collect()
}

fn subject_texts(store: &ChatMemoryStore, subject: &str) -> Vec<String> {
    store
        .list_memories(&MemoryQuery { subject: Some(subject.into()), ..Default::default() })
        .unwrap()
        .into_iter()
        .map(|m| m.text)
        .collect()
}

#[tokio::test]
async fn test_plan_from_scripted_llm() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let llm = default_llm();
    let plan =
        ex(&f.store, &llm, ExtractionConfig::default()).build_plan(&[m1.clone(), m2.clone()], &[], 1).await.unwrap();
    let texts: Vec<&str> = plan.new_memories.iter().map(|(m, _, _)| m.text.as_str()).collect();
    assert_eq!(texts, ["Ali moved to Berlin in March 2026.", "Ali has a cat called Luna."]);
    let r = reasons(&plan);
    assert!(r.contains(&"unknown_name".to_string()), "hallucinated 'Zorblax Quintrell' must be rejected");
    assert!(
        r.iter().any(|x| x == "assistant_source" || x == "assistant_subject"),
        "assistant memory must be rejected: {r:?}"
    );
    assert!(r.contains(&"low_importance".to_string()));
    let mem = &plan.new_memories[0].0;
    assert_eq!(
        (mem.subject.as_str(), mem.event_time.as_deref(), mem.event_time_precision.as_str(), mem.kind.as_str()),
        ("ali", Some("2026-03"), "month", "event")
    );
    assert_eq!(plan.new_memories[0].2, ["ali", "berlin"]);
    assert_eq!(plan.new_memories[0].1, std::slice::from_ref(&m1.message_id));
    let rels: HashSet<(String, String, String)> =
        plan.relations.iter().map(|r| (r.subject.clone(), r.predicate.clone(), r.object.clone())).collect();
    let want: HashSet<(String, String, String)> = [("ali", "lives_in", "berlin"), ("ali", "has_pet", "luna")]
        .iter()
        .map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
        .collect();
    assert_eq!(rels, want);
    let ents: HashSet<&str> = plan.entities.iter().map(|e| e.entity_id.as_str()).collect();
    assert_eq!(ents, HashSet::from(["ali", "berlin", "luna"]));
    let mut processed = plan.processed_message_ids.clone();
    processed.sort();
    let mut both = vec![m1.message_id, m2.message_id];
    both.sort();
    assert_eq!(processed, both);
    assert_eq!(llm.generate_calls(), 2, "memory + relation prompts, no reconcile needed on an empty store");
    let prompt = &llm.prompts()[0];
    assert!(prompt.contains("yesterday [= 31 March 2026]"), "relative dates are annotated for the model");
    assert!(prompt.contains("(assistant)"));
}

#[tokio::test]
async fn test_gate_skips_chitchat_and_assistant_only_without_llm() {
    let f = fx();
    // with extract_assistant=False (the opt-out) assistant turns are context only
    let cfg = ExtractionConfig { extract_assistant: false, ..Default::default() };
    let m1 = add(&f.store, "c1", "Ali", "thanks!!", "", None);
    let m2 = add(&f.store, "c1", "assistant", "You're welcome, anything else?", "assistant", None);
    let llm = default_llm();
    let plan = ex(&f.store, &llm, cfg.clone()).build_plan(&[m1.clone(), m2.clone()], &[], 1).await.unwrap();
    assert_eq!(llm.generate_calls(), 0);
    assert!(plan.skipped_message_ids.contains(&m1.message_id));
    let all: HashSet<&String> = plan.skipped_message_ids.iter().chain(&plan.processed_message_ids).collect();
    assert_eq!(all, HashSet::from([&m1.message_id, &m2.message_id]));
    assert!(plan.new_memories.is_empty());
    // an assistant turn with real content is context only: still no LLM call, marked processed
    let m3 = add(&f.store, "c1", "assistant", "Berlin has three opera houses and a famous zoo.", "assistant", None);
    let plan = ex(&f.store, &llm, cfg).build_plan(std::slice::from_ref(&m3), &[], 1).await.unwrap();
    assert_eq!(llm.generate_calls(), 0);
    assert_eq!(plan.processed_message_ids, std::slice::from_ref(&m3.message_id));
    // with the default (extract_assistant=True) the same assistant turn IS extracted: the gate still drops the
    // boilerplate reply, but the content-bearing one reaches the model
    ex(&f.store, &llm, ExtractionConfig::default()).build_plan(&[m2], &[], 1).await.unwrap();
    assert_eq!(llm.generate_calls(), 0, "boilerplate assistant reply is gated");
    ex(&f.store, &llm, ExtractionConfig::default()).build_plan(&[m3], &[], 1).await.unwrap();
    assert!(llm.generate_calls() > 0, "content-bearing assistant turn is a source by default");
}

#[tokio::test]
async fn test_duplicate_exact_and_embedding() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let e = ex(&f.store, &default_llm(), ExtractionConfig::default());
    let plan = e.build_plan(&[m1, m2], &[], 1).await.unwrap();
    f.store.apply_plan(&plan, None).unwrap();
    // same messages again in another chat -> exact duplicates, no new memories, sources attached
    let m3 = add(
        &f.store,
        "c2",
        "Ali",
        "I moved to Berlin in March 2026 and yesterday my cat Luna turned 3.",
        "",
        Some("2026-05-01T10:00:00+00:00"),
    );
    let plan2 = e.build_plan(std::slice::from_ref(&m3), &[], 1).await.unwrap();
    assert!(plan2.new_memories.is_empty());
    assert_eq!(plan2.duplicate_sources.len(), 2);
    f.store.apply_plan(&plan2, None).unwrap();
    let q = MemoryQuery { subject: Some("ali".into()), order: "observed_at ASC".into(), ..Default::default() };
    let first = f.store.list_memories(&q).unwrap().remove(0);
    let full = f.store.get_memory(&first.memory_id, true).unwrap().unwrap();
    assert!(full.source_message_ids.contains(&m3.message_id));
}

#[tokio::test]
async fn test_contradict_supersedes_when_newer() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let plan = ex(&f.store, &default_llm(), ExtractionConfig::default()).build_plan(&[m1, m2], &[], 1).await.unwrap();
    f.store.apply_plan(&plan, None).unwrap();
    let m3 = add(&f.store, "c2", "Ali", "I moved to Tokyo in June 2026.", "", Some("2026-07-01T10:00:00+00:00"));
    let newer = json!({"memories": [{"text": "Ali moved to Tokyo in June 2026.", "kind": "event", "subject": "Ali", "when": "2026-06",
                       "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Tokyo", "type": "place"}]}]});
    let cfg = ExtractionConfig { reconcile_cosine: 0.3, ..Default::default() }; // fake embeddings: consult the LLM
    let llm = make_llm(
        newer,
        json!({"entities": [], "triples": []}),
        Some(json!({"decision": "CONTRADICT", "target": "A", "text": null, "reason": "moved again"})),
    );
    let plan = ex(&f.store, &llm, cfg).build_plan(&[m3], &[], 1).await.unwrap();
    assert_eq!(plan.new_memories.len(), 1);
    assert_eq!(plan.supersedes.len(), 1);
    assert_eq!(plan.supersedes[0].2, "contradicts");
    assert!(llm.prompts().last().unwrap().contains("You maintain a memory store"));
    f.store.apply_plan(&plan, None).unwrap();
    let active = subject_texts(&f.store, "ali");
    assert!(active.contains(&"Ali moved to Tokyo in June 2026.".to_string()));
    assert!(!active.contains(&"Ali moved to Berlin in March 2026.".to_string()));
    assert_eq!(plan.new_memories[0].0.version, 2);
}

#[tokio::test]
async fn test_older_observation_cannot_supersede_newer_fact() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let plan = ex(&f.store, &default_llm(), ExtractionConfig::default()).build_plan(&[m1, m2], &[], 1).await.unwrap();
    f.store.apply_plan(&plan, None).unwrap();
    // an OLD chat imported later says something else about the same fact
    let m3 = add(&f.store, "old-chat", "Ali", "I moved to Paris in 2020.", "", Some("2020-05-01T10:00:00+00:00"));
    let older = json!({"memories": [{"text": "Ali moved to Paris in 2020.", "kind": "event", "subject": "Ali", "when": "2020",
                       "importance": 0.9, "confidence": 1.0, "sources": [1], "entities": [{"name": "Paris", "type": "place"}]}]});
    let llm =
        make_llm(older, json!({"entities": [], "triples": []}), Some(json!({"decision": "CONTRADICT", "target": "A"})));
    let cfg = ExtractionConfig { reconcile_cosine: 0.3, ..Default::default() };
    let plan = ex(&f.store, &llm, cfg).build_plan(&[m3], &[], 1).await.unwrap();
    assert!(plan.supersedes.is_empty(), "an earlier observation must not replace the current fact");
    assert_eq!(plan.new_memories[0].0.status, "superseded");
    f.store.apply_plan(&plan, None).unwrap();
    let q = MemoryQuery { subject: Some("ali".into()), kinds: Some(vec!["event".into()]), ..Default::default() };
    let events: Vec<String> = f.store.list_memories(&q).unwrap().into_iter().map(|m| m.text).collect();
    assert_eq!(events, ["Ali moved to Berlin in March 2026."]);
}

#[tokio::test]
async fn test_update_merges_text() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let plan = ex(&f.store, &default_llm(), ExtractionConfig::default()).build_plan(&[m1, m2], &[], 1).await.unwrap();
    f.store.apply_plan(&plan, None).unwrap();
    let m3 =
        add(&f.store, "c2", "Ali", "My cat Luna is a grey British shorthair.", "", Some("2026-05-01T10:00:00+00:00"));
    let upd = json!({"memories": [{"text": "Ali has a cat called Luna, a grey British shorthair.", "kind": "fact", "subject": "Ali",
                     "importance": 0.7, "confidence": 0.9, "sources": [1], "entities": [{"name": "Luna", "type": "pet"}]}]});
    let llm = make_llm(
        upd,
        json!({"entities": [], "triples": []}),
        Some(json!({"decision": "UPDATE", "target": "B", "text": "Ali has a grey British shorthair cat called Luna."})),
    );
    let cfg = ExtractionConfig { reconcile_cosine: 0.3, ..Default::default() };
    let plan = ex(&f.store, &llm, cfg).build_plan(&[m3], &[], 1).await.unwrap();
    f.store.apply_plan(&plan, None).unwrap();
    let texts = subject_texts(&f.store, "ali");
    assert!(texts.contains(&"Ali has a grey British shorthair cat called Luna.".to_string()));
    assert!(!texts.contains(&"Ali has a cat called Luna.".to_string()));
    let new = f
        .store
        .list_memories(&MemoryQuery { subject: Some("ali".into()), ..Default::default() })
        .unwrap()
        .into_iter()
        .find(|m| m.text.contains("shorthair"))
        .unwrap();
    assert_eq!(new.version, 2);
    assert_eq!(f.store.history(&new.memory_id, 20).unwrap().len(), 1);
}

#[tokio::test]
async fn test_embedding_outage_does_not_lose_memories() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let mut llm = FakeLlmClient::with_script(vec![
        ("You maintain the long-term memory", memory_json().to_string()),
        ("You are building a knowledge graph", relation_json().to_string()),
    ]);
    llm.embed_error = Some("embedding server down".into());
    let llm = Arc::new(llm);
    let plan = ex(&f.store, &llm, ExtractionConfig::default()).build_plan(&[m1, m2], &[], 1).await.unwrap();
    assert_eq!(plan.new_memories.len(), 2);
    assert!(plan.new_memories.iter().all(|(m, _, _)| m.embedding.is_none()));
    f.store.apply_plan(&plan, None).unwrap();
    assert_eq!(f.store.memories_missing_embedding(100).unwrap().len(), 2);
}

#[tokio::test]
async fn test_unparseable_output_raises_for_retry_and_retries_vary() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let llm = Arc::new(FakeLlmClient::with_default("I cannot help with that."));
    let e = ex(&f.store, &llm, ExtractionConfig::default());
    let batch = [m1.clone(), m2.clone()];
    assert!(matches!(e.build_plan(&batch, &[], 1).await, Err(MemoryError::UnparseableOutput(_))));
    assert!(matches!(e.build_plan(&batch, &[], 2).await, Err(MemoryError::UnparseableOutput(_))));
    let prompts = llm.prompts();
    let (first, second) = (&prompts[0], &prompts[2]);
    assert_ne!(first, second, "a retry must not resend the identical prompt");
    assert!(second.contains("was not valid JSON"));
    // an explicit empty answer is fine: nothing worth remembering
    let llm = Arc::new(FakeLlmClient::with_default(r#"{"memories": []}"#));
    let plan = ex(&f.store, &llm, ExtractionConfig::default()).build_plan(&batch, &[], 1).await.unwrap();
    assert!(plan.new_memories.is_empty());
    let mut processed = plan.processed_message_ids.clone();
    processed.sort();
    let mut both = vec![m1.message_id, m2.message_id];
    both.sort();
    assert_eq!(processed, both);
}

#[tokio::test]
async fn test_relations_carry_source_time() {
    let f = fx();
    let (m1, m2) = seed(&f.store);
    let plan =
        ex(&f.store, &default_llm(), ExtractionConfig::default()).build_plan(&[m1.clone(), m2], &[], 1).await.unwrap();
    assert!(!plan.relations.is_empty());
    assert!(plan.relations.iter().all(|r| r.observed_at == m1.sent_at));
}
