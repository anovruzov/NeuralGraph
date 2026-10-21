//! Parity of `retrieval::query_router` with Python (`tests/golden/query_router.json`, written by
//! `gen_query_router.py`): the fuzzy/alias matchers, `QueryRouter::analyze` over every LoCoMo question and
//! `FilteredRetriever` in every mode over a real conversation's nodes.

mod common;

use common::{assert_json_eq, load, s};
use indexmap::IndexSet;
use neuralgraph_research::retrieval::query_router::{
    compute_edit_distance, create_filtered_retriever, fuzzy_match, is_alias_match, match_entity, py_capitalize,
    FilteredRetriever, QueryAnalysis, QueryRouter, SoftFilterResult, SoftResults,
};
use neuralgraph_research::retrieval::{
    node_ref, InMemoryNeuralGraphStorage, NeuralGraphStorage, NeuralNode, NodeLayer, NodeRef,
};
use serde_json::{json, Value};
use std::sync::LazyLock;

static FIXTURE: LazyLock<Value> = LazyLock::new(|| load("query_router.json"));

fn locomo() -> Value {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("locomo10.json")).expect("parse locomo")
}

fn row(a: &QueryAnalysis) -> Value {
    json!([
        a.query_type.value(),
        a.entities,
        a.primary_subject,
        a.speaker_filter,
        a.temporal_markers,
        a.temporal_direction,
        a.has_negation,
        a.confidence,
        a.strategy,
        a.filter_params,
        a.rare_terms,
        a.is_open_domain,
        a.open_domain_confidence,
        a.is_list_question,
        a.list_question_type,
        a.suppress_multi_hop
    ])
}

fn sfr(r: &SoftFilterResult) -> Value {
    json!([
        r.node_id,
        r.score_modifier,
        r.match_reason,
        r.is_speaker_match,
        r.is_content_match,
        r.is_entity_id_match,
        r.is_fuzzy_match,
        r.boost_applied
    ])
}

fn soft_json(results: &SoftResults) -> Value {
    Value::Array(results.iter().map(|(k, v)| json!([k, sfr(v)])).collect())
}

fn opt_str(v: &Value) -> Option<String> {
    v.as_str().map(str::to_string)
}

fn opt_ids(v: &Value) -> Option<Vec<String>> {
    v.as_array().map(|a| a.iter().map(|x| s(x).to_string()).collect())
}

#[test]
fn edit_distance_fuzzy_and_alias_match_python() {
    let m = &FIXTURE["matchers"];
    for c in m["edit_distance"].as_array().unwrap() {
        assert_eq!(compute_edit_distance(s(&c[0]), s(&c[1])) as u64, c[2].as_u64().unwrap(), "{c}");
    }
    for c in m["fuzzy"].as_array().unwrap() {
        let (ok, sim) = fuzzy_match(s(&c[0]), s(&c[1]), c[2].as_f64().unwrap());
        assert_json_eq(&json!([ok, sim]), &json!([c[3], c[4]]), &format!("fuzzy {c}"));
    }
    for c in m["alias"].as_array().unwrap() {
        let (ok, conf) = is_alias_match(s(&c[0]), s(&c[1]));
        assert_json_eq(&json!([ok, conf]), &json!([c[2], c[3]]), &format!("alias {c}"));
    }
}

#[test]
fn match_entity_matches_python() {
    for c in FIXTURE["match_entity"].as_array().unwrap() {
        let speaker = opt_str(&c[1]);
        let ids = opt_ids(&c[3]);
        let r = match_entity(s(&c[0]), speaker.as_deref(), s(&c[2]), ids.as_deref(), 0.7);
        assert_json_eq(&sfr(&r), &c[4], &format!("match_entity {c}"));
    }
}

#[test]
fn analyze_matches_python_on_every_locomo_question() {
    let data = locomo();
    let convs = FIXTURE["analyze"]["locomo"].as_array().unwrap();
    assert_eq!(convs.len(), data.as_array().unwrap().len());
    let mut n = 0;
    for (conv, expected) in data.as_array().unwrap().iter().zip(convs) {
        let c = &conv["conversation"];
        let known: IndexSet<String> = [s(&c["speaker_a"]).to_string(), s(&c["speaker_b"]).to_string()].into();
        let router = QueryRouter::new(Some(known));
        let rows = expected["rows"].as_array().unwrap();
        for (qa, want) in conv["qa"].as_array().unwrap().iter().zip(rows) {
            let q = s(&qa["question"]);
            let a = router.analyze(q);
            assert_eq!(a.query_text, q);
            assert_json_eq(&row(&a), want, &format!("analyze {q:?}"));
            n += 1;
        }
    }
    assert_eq!(n, 1986);
}

#[test]
fn analyze_matches_python_on_edge_cases() {
    let a = &FIXTURE["analyze"];
    let known: IndexSet<String> = a["edge_known"].as_array().unwrap().iter().map(|v| s(v).to_string()).collect();
    let router = QueryRouter::new(Some(known));
    for c in a["edge"].as_array().unwrap() {
        assert_json_eq(&row(&router.analyze(s(&c[0]))), &c[1], &format!("edge {}", c[0]));
    }
    let bare = QueryRouter::new(None);
    for c in a["edge_no_known"].as_array().unwrap() {
        assert_json_eq(&row(&bare.analyze(s(&c[0]))), &c[1], &format!("edge (no known) {}", c[0]));
    }
    let full = router.analyze("What is Caroline's job?").to_json();
    assert_eq!(full["query_text"], "What is Caroline's job?");
    assert_eq!(full["query_type"], "entity_attribute");
}

#[test]
fn capitalize_matches_python_title_rule() {
    assert_eq!(py_capitalize("bob"), "Bob");
    assert_eq!(py_capitalize("mcDONALD"), "Mcdonald");
    assert_eq!(py_capitalize("ǆemal"), "ǅemal");
    assert_eq!(py_capitalize(""), "");
}

fn build_nodes() -> Vec<NodeRef> {
    FIXTURE["filtered"]["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| {
            let mut node = NeuralNode::new(s(&n["node_id"]), NodeLayer::Message, s(&n["content"]));
            node.session_key = "s0".into();
            node.entity_ids = opt_ids(&n["entity_ids"]).unwrap();
            node.metadata = n["metadata"].as_object().unwrap().clone();
            node_ref(node)
        })
        .collect()
}

fn ids(nodes: &[NodeRef]) -> Value {
    json!(nodes.iter().map(|n| n.borrow().node_id.clone()).collect::<Vec<_>>())
}

#[test]
fn filtered_retriever_matches_python_in_every_mode() {
    let nodes = build_nodes();
    let storage = InMemoryNeuralGraphStorage::default();
    for run in FIXTURE["filtered"]["runs"].as_array().unwrap() {
        let mode = s(&run["mode"]);
        let mut fr = FilteredRetriever::new(&storage, mode, 0.2);
        fr.update_context(&nodes);
        let known: Vec<&String> = fr.router.known_entities.iter().collect();
        assert_json_eq(&json!(known), &run["known"], &format!("{mode} known entities"));
        for r in run["rows"].as_array().unwrap() {
            let q = s(&r[0]);
            let (filtered, analysis, soft) = fr.get_filtered_candidates(q, "s0", Some(nodes.clone()));
            assert_json_eq(&ids(&filtered), &r[1], &format!("{mode} {q:?} ids"));
            assert_eq!(analysis.strategy, s(&r[2]), "{mode} {q:?} strategy");
            let soft = soft.as_ref().map(soft_json).unwrap_or(Value::Null);
            assert_json_eq(&soft, &r[3], &format!("{mode} {q:?} soft results"));
        }
        assert_json_eq(&fr.get_filter_statistics().to_json(), &run["stats"], &format!("{mode} stats"));
    }
}

#[test]
fn filter_functions_match_python_with_fixed_params() {
    let nodes = build_nodes();
    let storage = InMemoryNeuralGraphStorage::default();
    let mut fr = FilteredRetriever::new(&storage, "hard", 0.1);
    for c in FIXTURE["filtered"]["direct"].as_array().unwrap() {
        let params = c["params"].as_object().unwrap();
        let sub = &nodes[..30];
        let what = format!("{params:?}");
        assert_json_eq(&soft_json(&fr.apply_entity_filter_soft(sub, params).1), &c["entity_soft"], &what);
        assert_json_eq(&ids(&fr.apply_entity_filter_hard(sub, params)), &c["entity_hard"], &what);
        assert_json_eq(&ids(&fr.apply_entity_filter_hard(&nodes[..8], params)), &c["entity_hard_small"], &what);
        assert_json_eq(&soft_json(&fr.apply_entity_filter_boost(sub, params).1), &c["entity_boost"], &what);
        assert_json_eq(&soft_json(&fr.apply_temporal_filter_soft(sub, params).1), &c["temporal_soft"], &what);
        assert_json_eq(&ids(&fr.apply_temporal_filter_hard(sub, params)), &c["temporal_hard"], &what);
        assert_json_eq(&soft_json(&fr.apply_temporal_filter_boost(sub, params).1), &c["temporal_boost"], &what);
        assert_json_eq(&fr.get_filter_statistics().to_json(), &c["stats"], &what);
    }
}

#[test]
fn storage_path_matches_python_membership() {
    // Python's layer index was a set, so the candidate order was not reproducible; compare sorted.
    let storage = InMemoryNeuralGraphStorage::default();
    for n in build_nodes() {
        storage.save_node(&n);
    }
    let mut fr = create_filtered_retriever(&storage, "soft");
    let (filtered, _, soft) = fr.get_filtered_candidates("What is Caroline's job?", "s0", None);
    let mut got: Vec<String> = filtered.iter().map(|n| n.borrow().node_id.clone()).collect();
    got.sort();
    let want = &FIXTURE["filtered"]["storage_path"];
    assert_json_eq(&json!(got), &want["ids"], "storage path ids");
    let mut soft: Vec<(String, Value)> = soft.unwrap().iter().map(|(k, v)| (k.clone(), sfr(v))).collect();
    soft.sort_by(|a, b| a.0.cmp(&b.0));
    let soft: Vec<Value> = soft.into_iter().map(|(k, v)| json!([k, v])).collect();
    assert_json_eq(&json!(soft), &want["soft"], "storage path soft results");
}
