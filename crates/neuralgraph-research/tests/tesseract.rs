//! Parity of the Tesseract engine with Python (`tests/golden/tesseract_predicates.json` and
//! `tesseract_retrieve.json`, written by `gen_tesseract.py`): every predicate and query parser over all LoCoMo
//! questions, the content helpers over all LoCoMo messages, and full `Tesseract.retrieve` rankings (node + charge)
//! plus per-store results on two LoCoMo conversations ingested like `retrieval_eval.py::ingest`.
//!
//! Charges contain float32 BLAS dot products, bit-identical to numpy on macOS only; elsewhere the rankings are
//! compared by charge per rank with a tolerance (see `numpy::bitwise_numpy()`).

mod common;

use chrono::NaiveDate;
use common::{assert_json_close, assert_json_eq, load, pin_now, s, FIXED_NOW};
use indexmap::IndexSet;
use neuralgraph_memory::fake_embedding;
use neuralgraph_research::numpy::bitwise_numpy;
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::retrieval::external_retriever::is_open_domain_query;
use neuralgraph_research::retrieval::tesseract::{
    build_novelty_context, detect_list_question_universal, detect_query_type, expand_morphology_basic,
    extract_topic_keywords_universal, is_open_domain_world_query, is_yes_no_question, looks_open_domain_question,
    robust_similarity, should_use_open_domain_infer, tokenize_for_novelty, Scored, Tesseract,
};
use neuralgraph_research::retrieval::{
    edge_ref, generate_edge_id, node_ref, set_deterministic_uuids, EdgeType, InMemoryNeuralGraphStorage, NeuralEdge,
    NeuralGraphStorage, NeuralNode, NodeLayer,
};
use neuralgraph_research::temporal_utils::{
    extract_duration_metadata, extract_explicit_date, generate_temporal_tokens, parse_datetime_flexible,
    resolve_relative_dates,
};
use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::rc::Rc;

const EMBED_DIM: usize = 256;

/// One flattened LoCoMo message: `(speaker, text, session datetime)`.
type Message = (String, String, String);

fn locomo() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("locomo10.json")).unwrap()
}

/// `flatten_messages` of the harnesses.
fn flatten(conv: &Value) -> Vec<Message> {
    let c = conv.get("conversation").unwrap_or(conv);
    let mut out = Vec::new();
    let mut i = 1;
    while let Some(session) = c.get(format!("session_{i}")) {
        let dt = c.get(format!("session_{i}_date_time")).and_then(Value::as_str).unwrap_or("").to_string();
        for m in session.as_array().unwrap() {
            let speaker = m.get("speaker").and_then(Value::as_str).unwrap_or("Unknown").to_string();
            let text = m.get("text").and_then(Value::as_str).unwrap_or("").to_string();
            out.push((speaker, text, dt.clone()));
        }
        i += 1;
    }
    out
}

fn digest(set: &IndexSet<String>) -> String {
    let mut v: Vec<&str> = set.iter().map(String::as_str).collect();
    v.sort_unstable();
    Sha256::digest(v.join(" ").as_bytes()).iter().take(8).map(|b| format!("{b:02x}")).collect()
}

fn sorted(set: &IndexSet<String>) -> Value {
    let mut v: Vec<&str> = set.iter().map(String::as_str).collect();
    v.sort_unstable();
    json!(v)
}

fn predicates(q: &str) -> Value {
    let qt = detect_query_type(q);
    let (open, conf) = is_open_domain_query(q);
    let (is_list, list_type) = detect_list_question_universal(q);
    json!([
        qt.values().collect::<Vec<_>>(),
        [open, conf],
        should_use_open_domain_infer(q),
        looks_open_domain_question(q),
        is_yes_no_question(q),
        [is_list, list_type],
        is_open_domain_world_query(q),
    ])
}

fn parsers(q: &str, t: &Tesseract) -> Value {
    let ql = q.to_lowercase();
    let tk = t.temporal_store.extract_temporal_keywords(&ql);
    let (neg, pos) = t.adversarial_store.parse_adversarial(q);
    json!({
        "topic_universal": digest(&extract_topic_keywords_universal(q)),
        "temporal_keywords": sorted(&tk),
        "decay": t.temporal_store.adjust_temporal_decay(&ql, &tk),
        "temporal_topics": digest(&t.temporal_store.extract_topic_keywords(&ql)),
        "entity_entities": sorted(&t.entity_store.extract_entities(q)),
        "entity_keywords": digest(&t.entity_store.extract_keywords(q)),
        "reasoning_entities": sorted(&t.reasoning_store.extract_entities(q)),
        "reasoning_keywords": digest(&t.reasoning_store.extract_keywords(q)),
        "adversarial": [sorted(&neg), sorted(&pos), sorted(&t.adversarial_store.extract_entities(q))],
        "novelty_tokens": tokenize_for_novelty(q).into_iter().collect::<Vec<_>>(),
    })
}

#[test]
fn question_predicates_and_parsers_match() {
    let f = load("tesseract_predicates.json");
    let data = locomo();
    let probe = Tesseract::new(Rc::new(InMemoryNeuralGraphStorage::default()));
    let mut expected = f["questions"].as_array().unwrap().iter();
    for conv in data.as_array().unwrap() {
        for qa in conv["qa"].as_array().unwrap() {
            let q = s(&qa["question"]);
            let e = expected.next().expect("question count");
            assert_json_eq(&predicates(q), &e["p"], &format!("predicates of {q:?}"));
            assert_json_eq(&parsers(q, &probe), &e["s"], &format!("parsers of {q:?}"));
        }
    }
    assert!(expected.next().is_none());
}

#[test]
fn message_helpers_match() {
    let f = load("tesseract_predicates.json");
    let data = locomo();
    let probe = Tesseract::new(Rc::new(InMemoryNeuralGraphStorage::default()));
    let t = &probe.temporal_store;
    let mut actual = Vec::new();
    for (ci, conv) in data.as_array().unwrap().iter().enumerate() {
        for (mi, (_, text, _)) in flatten(conv).iter().enumerate() {
            let lower = text.to_lowercase();
            let rel = t.extract_relative_time_type(&lower);
            let year = t.extract_event_year_from_content(&lower, 2023);
            if rel.is_some() || year.is_some() {
                actual.push(json!([ci, mi, rel, year]));
            }
        }
    }
    assert_json_eq(&json!(actual), &f["messages"], "relative time types and event years");
    for (w, expected) in f["morphology"].as_object().unwrap() {
        assert_eq!(sorted(&expand_morphology_basic(w)), *expected, "morphology {w:?}");
    }
    for pair in f["robust"].as_array().unwrap() {
        assert_eq!(json!(robust_similarity(pair[0].as_f64().unwrap())), pair[1], "robust {}", pair[0]);
    }
}

/// `retrieval_eval.ingest` minus the `keywords` metadata and the DialogueLinker (see the generator's docstring).
fn ingest(conv_idx: usize, conv: &Value) -> Rc<InMemoryNeuralGraphStorage> {
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let created_at = PyDateTime::naive(NaiveDate::from_ymd_opt(2026, 9, 30).unwrap().and_hms_opt(8, 0, 0).unwrap());
    let capitalized = Regex::new(r"\b[A-Z][a-z]+\b").unwrap();
    let digit = Regex::new(r"\d").unwrap();
    let mut speaker_nodes: indexmap::IndexMap<String, Vec<String>> = indexmap::IndexMap::new();
    let mut all_ids = Vec::new();
    for (idx, (speaker, text, dt)) in flatten(conv).iter().enumerate() {
        let message_date = parse_datetime_flexible(Some(dt)).unwrap().map(|d| d.naive);
        let temporal = generate_temporal_tokens(message_date, text).unwrap();
        let (_, resolved_dates) = resolve_relative_dates(text, message_date).unwrap();
        let explicit_date = extract_explicit_date(text, message_date).unwrap();
        let duration = extract_duration_metadata(text, message_date);
        let (mut resolved_date, mut source): (Option<String>, Option<&str>) = (None, None);
        if let Some(e) = explicit_date.clone().filter(|e| !e.is_empty()) {
            (resolved_date, source) = (Some(e), Some("explicit"));
        } else if !resolved_dates.is_empty() {
            let vals: IndexSet<&String> = resolved_dates.values().filter(|v| digit.is_match(v)).collect();
            if vals.len() == 1 {
                (resolved_date, source) = (Some(vals[0].clone()), Some("relative"));
            }
        }
        if resolved_date.is_none() {
            if let Some(md) = message_date {
                (resolved_date, source) = (Some(md.format("%Y-%m-%d").to_string()), Some("message"));
            }
        }
        let entities: IndexSet<&str> = capitalized.find_iter(text).map(|m| m.as_str()).filter(|e| *e != "I").collect();
        let node_id = format!("msg_{conv_idx}_{idx}");
        let mut node = NeuralNode::new(node_id.clone(), NodeLayer::Message, text.clone());
        node.session_key = format!("conv_{conv_idx}");
        node.embedding = Some(fake_embedding(text, EMBED_DIM));
        node.created_at = created_at;
        let tm = Value::Object(temporal.temporal_metadata.clone());
        let resolved: Map<String, Value> = resolved_dates.iter().map(|(k, v)| (k.clone(), json!(v))).collect();
        node.metadata = json!({
            "speaker": speaker, "datetime": dt, "entities": entities,
            "temporal_tokens": temporal.date_tokens, "temporal_metadata": tm,
            "resolved_date": resolved_date, "resolved_date_source": source, "explicit_date": explicit_date,
            "duration_years": duration.get("duration_years"), "duration_months": duration.get("duration_months"),
            "since_year": duration.get("since_year"), "since_date": duration.get("since_date"),
            "date_tokens": temporal.date_tokens, "temporal": tm,
            "resolved_dates": resolved,
        })
        .as_object()
        .unwrap()
        .clone();
        storage.save_node(&node_ref(node));
        all_ids.push(node_id.clone());
        speaker_nodes.entry(speaker.to_lowercase()).or_default().push(node_id);
    }
    for ids in speaker_nodes.values() {
        for (i, src) in ids.iter().enumerate() {
            for target in ids.iter().take((i + 4).min(ids.len())).skip(i + 1) {
                let e =
                    NeuralEdge::new(generate_edge_id(), src.clone(), target.clone(), EdgeType::Entity).with_weight(0.8);
                storage.save_edge(&edge_ref(e));
            }
        }
    }
    for pair in all_ids.windows(2) {
        let e = NeuralEdge::new(generate_edge_id(), pair[0].clone(), pair[1].clone(), EdgeType::Temporal)
            .with_weight(0.85)
            .with_metadata(json!({"direction": "before"}).as_object().unwrap().clone());
        storage.save_edge(&edge_ref(e));
    }
    storage
}

fn ranked(results: &Scored) -> Value {
    json!(results
        .iter()
        .map(|(n, c)| {
            let id = n.borrow().node_id.clone();
            json!([id.rsplit('_').next().unwrap().parse::<u64>().unwrap(), c])
        })
        .collect::<Vec<_>>())
}

/// Exact on macOS; elsewhere (other BLAS) the charge at each rank within a tolerance, ids not compared.
#[track_caller]
fn assert_ranked(actual: &Value, expected: &Value, what: &str) {
    if bitwise_numpy() {
        assert_json_eq(actual, expected, what);
        return;
    }
    let charges = |v: &Value| json!(v.as_array().unwrap().iter().map(|p| p[1].clone()).collect::<Vec<_>>());
    assert_json_close(&charges(actual), &charges(expected), 1e-5, what);
}

#[test]
fn tesseract_retrieve_matches() {
    pin_now(FIXED_NOW);
    set_deterministic_uuids(Some(0));
    let f = load("tesseract_retrieve.json");
    let data = locomo();
    for conv_out in f["retrieval"].as_array().unwrap() {
        let ci = conv_out["conv"].as_u64().unwrap() as usize;
        let conv = &data[ci];
        let storage = ingest(ci, conv);
        let tess = Tesseract::new(storage.clone());
        let session = format!("conv_{ci}");
        let qas = conv["qa"].as_array().unwrap();
        let expected_questions = conv_out["questions"].as_array().unwrap();
        assert_eq!(qas.len(), expected_questions.len());
        for (k, (qa, expected)) in qas.iter().zip(expected_questions).enumerate() {
            let q = s(&qa["question"]);
            let qvec = fake_embedding(q, EMBED_DIM);
            let res = tess.retrieve(q, &qvec, &session, None, 50, true).unwrap();
            assert_ranked(&ranked(&res), expected, &format!("conv {ci} question {k}: {q:?}"));

            if let Some(exp) = conv_out["stores"].get(k) {
                let stores = tess.query_stores(q, &qvec, &session, None, true).unwrap().unwrap();
                let all_nodes = storage.get_nodes_by_session(&session, None);
                let novelty = build_novelty_context(q, &all_nodes)
                    .map(|n| json!([n.query_tokens, n.token_idf, n.max_idf]))
                    .unwrap_or(Value::Null);
                assert_eq!(stores.effective_query, s(&exp["effective"]), "effective query {q:?}");
                assert_json_eq(&novelty, &exp["novelty"], &format!("novelty {q:?}"));
                for (name, got) in [
                    ("temporal", &stores.temporal),
                    ("entity", &stores.entity),
                    ("reasoning", &stores.reasoning),
                    ("adversarial", &stores.adversarial),
                ] {
                    assert_ranked(&ranked(got), &exp[name], &format!("{name} store, conv {ci} question {k}: {q:?}"));
                }
            }

            if let Some(exp) = conv_out["variants"].get(k) {
                let naive_ref =
                    PyDateTime::naive(NaiveDate::from_ymd_opt(2026, 7, 1).unwrap().and_hms_opt(12, 0, 0).unwrap());
                let aware_ref = PyDateTime::fromisoformat("2026-07-01T00:00:00+00:00").unwrap();
                let no_expand = tess.retrieve(q, &qvec, &session, None, 80, false).unwrap();
                assert_ranked(&ranked(&no_expand), &exp["no_expand"], &format!("no_expand {q:?}"));
                let with_ref = tess.retrieve(q, &qvec, &session, Some(&naive_ref), 20, true).unwrap();
                assert_ranked(&ranked(&with_ref), &exp["reference_naive"], &format!("reference_naive {q:?}"));
                assert!(tess.retrieve(q, &qvec, &session, Some(&aware_ref), 20, true).is_err());
                assert_eq!(exp["reference_aware"], json!({"error": "TypeError"}));
                let unknown = tess.retrieve(q, &qvec, "nope", None, 20, true).unwrap();
                assert_json_eq(&ranked(&unknown), &exp["unknown_session"], "unknown session");
            }
        }
    }
}
