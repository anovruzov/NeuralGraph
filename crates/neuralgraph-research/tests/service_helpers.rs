//! Parity of the parsing helpers of `retrieval/service.py` with Python (`tests/golden/service_helpers.json`,
//! written by `gen_service_helpers.py`): every LoCoMo question, the messages and evidence memories of two
//! conversations, the semantic extractors with a scripted embedder, and edge cases.

mod common;

use common::{assert_json_eq, load, s};
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::llm_backend::{LlmCall, ScriptedLlm};
use neuralgraph_research::retrieval::service::{
    clean_candidate, cosine_similarity_prefix, dedupe_items, extract_candidate_spans, extract_count_answer,
    extract_duration_answer, extract_keywords, extract_list_items, extract_list_items_semantic,
    extract_month_year_from_text, extract_name_candidates, extract_relationship_status, extract_span_answer_semantic,
    extract_topic_keywords_for_temporal, is_bad_candidate, is_temporal_question, normalize_item,
};
use serde_json::{json, Value};

const EMBED_DIM: usize = 64;

fn per_message(text: &str) -> Value {
    let spans = extract_candidate_spans(text);
    let cleaned: Vec<Value> = spans
        .iter()
        .map(|c| {
            let n = normalize_item(c);
            let cl = clean_candidate(&n);
            let bad = is_bad_candidate(&cl);
            json!([n, cl, bad])
        })
        .collect();
    let (m, y) = extract_month_year_from_text(text);
    json!({
        "keywords": extract_keywords(text),
        "spans": spans,
        "cleaned": cleaned,
        "names": extract_name_candidates(text),
        "month_year": [m, y],
    })
}

fn embedder() -> ScriptedLlm {
    ScriptedLlm::new(|_| Err("no generate".into())).with_embedder(|t| {
        if t.contains("zz") {
            Err("scripted embedding failure".into())
        } else {
            Ok(fake_embedding(t, EMBED_DIM))
        }
    })
}

fn embedded(llm: &ScriptedLlm) -> Vec<String> {
    llm.calls
        .borrow()
        .iter()
        .filter_map(|c| match c {
            LlmCall::Embed { text } => Some(text.clone()),
            LlmCall::Generate { .. } => None,
        })
        .collect()
}

async fn semantic(q: &str, mems: &[Value], use_embedding: bool) -> Value {
    let qemb = if use_embedding { fake_embedding(q, EMBED_DIM) } else { Vec::new() };
    let llm = embedder();
    let span = extract_span_answer_semantic(&llm, q, &qemb, mems, 30, None).await;
    let span_calls = embedded(&llm);
    let llm = embedder();
    let items = extract_list_items_semantic(&llm, q, &qemb, mems, 6, None).await;
    json!({"span": span, "span_embedded": span_calls, "items": items, "items_embedded": embedded(&llm)})
}

async fn per_question(q: &str, mems: &[Value], with_semantic: bool) -> Value {
    let tk = extract_topic_keywords_for_temporal(q);
    let mut out = json!({
        "count": extract_count_answer(q, mems),
        "relationship": extract_relationship_status(q, mems),
        "list_items": extract_list_items(q, mems, None),
        "list_items_topic": extract_list_items(q, mems, Some(&tk)),
        "duration": extract_duration_answer(q, mems),
    });
    if with_semantic {
        out["semantic"] = semantic(q, mems, true).await;
        out["semantic_no_embedding"] = semantic(q, mems, false).await;
    }
    out
}

#[test]
fn every_locomo_question_matches() {
    let f = load("service_helpers.json");
    for c in f["questions"].as_array().unwrap() {
        let (q, answer) = (s(&c[0]), s(&c[1]));
        let mut tk: Vec<String> = extract_topic_keywords_for_temporal(q).into_iter().collect();
        tk.sort();
        let (m, y) = extract_month_year_from_text(answer);
        let got = json!([q, answer, is_temporal_question(q), tk, extract_keywords(q), [m, y]]);
        assert_json_eq(&got, c, &format!("question {q:?}"));
    }
}

#[test]
fn locomo_messages_match() {
    let f = load("service_helpers.json");
    for c in f["messages"].as_array().unwrap() {
        assert_json_eq(&per_message(s(&c[0])), &c[1], &format!("message {:?}", c[0]));
    }
    for c in f["edge_texts"].as_array().unwrap() {
        assert_json_eq(&per_message(s(&c[0])), &c[1], &format!("edge text {:?}", c[0]));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn evidence_memories_match() {
    let f = load("service_helpers.json");
    for (i, c) in f["qa_cases"].as_array().unwrap().iter().enumerate() {
        let q = s(&c["question"]);
        let mems = c["memories"].as_array().unwrap();
        let got = per_question(q, mems, c["result"].get("semantic").is_some()).await;
        assert_json_eq(&got, &c["result"], &format!("qa {i} {q:?}"));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn edge_questions_match() {
    let f = load("service_helpers.json");
    let mems = f["edge_memories"].as_array().unwrap();
    for c in f["edge_questions"].as_array().unwrap() {
        let q = s(&c["question"]);
        assert_eq!(json!(is_temporal_question(q)), c["is_temporal"], "{q:?}");
        let got = per_question(q, mems, true).await;
        assert_json_eq(&got, &c["result"], &format!("edge question {q:?}"));
    }
}

#[test]
fn item_helpers_match() {
    let f = load("service_helpers.json");
    for c in f["items"].as_array().unwrap() {
        let x = s(&c[0]);
        let got = json!([x, normalize_item(x), clean_candidate(x), is_bad_candidate(x)]);
        assert_json_eq(&got, c, &format!("item {x:?}"));
    }
    let items: Vec<String> = ["A", "a", "", "B", "b ", "B", "c"].iter().map(|x| x.to_string()).collect();
    assert_json_eq(&json!(dedupe_items(&items)), &f["dedupe"], "dedupe");
    for c in f["cosine"].as_array().unwrap() {
        let (a, b) = (common::f64s(&c[0]), common::f64s(&c[1]));
        assert_json_eq(&json!(cosine_similarity_prefix(&a, &b)), &c[2], "cosine");
    }
}
