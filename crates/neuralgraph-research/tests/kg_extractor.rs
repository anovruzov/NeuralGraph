//! Parity of the LLM entity-graph extractor with Python (`tests/golden/kg_extractor.json`, written by
//! `gen_kg_extractor.py`): byte-identical prompts for scripted responses, the resulting registry and triples,
//! the cache file, and the parsing / resolution helpers on odd model output.

mod common;

use common::{assert_json_eq, load, s};
use neuralgraph_research::llm_backend::{LlmCall, ScriptedLlm};
use neuralgraph_research::retrieval::kg_extractor::{
    extract_conversation, flatten, format_template, norm, parse, resolve, KgMessage, Registry, RegistryEntry, PROMPT,
};
use serde_json::{json, Value};
use std::cell::Cell;
use std::path::Path;

fn locomo() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// A model answering with the recorded responses in order (`null` = the call failed).
fn replay(calls: &Value) -> ScriptedLlm {
    let responses: Vec<Option<String>> =
        calls.as_array().unwrap().iter().map(|c| c["response"].as_str().map(str::to_string)).collect();
    let next = Cell::new(0usize);
    ScriptedLlm::new(move |_| {
        let i = next.get();
        next.set(i + 1);
        responses.get(i).cloned().flatten().ok_or_else(|| "scripted failure".to_string())
    })
}

fn check_calls(llm: &ScriptedLlm, want: &Value, what: &str) {
    let calls = llm.calls.borrow();
    let want = want.as_array().unwrap();
    assert_eq!(calls.len(), want.len(), "{what}: number of calls");
    for (i, (c, w)) in calls.iter().zip(want).enumerate() {
        let LlmCall::Generate { prompt, opts } = c else { panic!("unexpected embed call") };
        assert_eq!(prompt, s(&w["prompt"]), "{what}: prompt {i}");
        assert_eq!(opts.temperature, w["kwargs"]["temperature"].as_f64().unwrap());
        assert_eq!(opts.max_tokens, w["kwargs"]["max_tokens"].as_i64().unwrap());
        assert_eq!(opts.timeout_seconds, w["kwargs"]["timeout_seconds"].as_f64().unwrap());
    }
}

fn messages(f: &Value, n: usize) -> Vec<KgMessage> {
    let data = locomo();
    flatten(&data[f["conv_idx"].as_u64().unwrap() as usize]).into_iter().take(n).collect()
}

#[tokio::test(flavor = "current_thread")]
async fn scripted_conversation_matches() {
    let f = load("kg_extractor.json");
    let msgs = messages(&f, f["n_messages"].as_u64().unwrap() as usize);
    let speakers = common::strings(&f["speakers"]);
    let llm = replay(&f["calls"]);
    let result = extract_conversation(&llm, &msgs, &speakers, None, 6, 0).await.unwrap();
    check_calls(&llm, &f["calls"], "conversation");
    assert_json_eq(&result, &f["result"], "conversation result");
}

#[tokio::test(flavor = "current_thread")]
async fn cache_is_written_and_reused() {
    let f = load("kg_extractor.json");
    let want = &f["cached_run"];
    let msgs = messages(&f, 12);
    let speakers = common::strings(&f["speakers"]);
    let dir = tempfile::tempdir().unwrap();
    let cache = dir.path().join("sub").join("conv1.json");
    let llm = replay(&want["calls"]);
    let result = extract_conversation(&llm, &msgs, &speakers, Some(&cache), 120, 5).await.unwrap();
    check_calls(&llm, &want["calls"], "cached run");
    assert_json_eq(&result, &want["result"], "cached run result");
    assert_eq!(std::fs::read_to_string(&cache).unwrap(), s(&want["file"]), "cache file bytes");

    let silent = ScriptedLlm::new(|_| panic!("cache hit must not call the model"));
    let hit = extract_conversation(&silent, &msgs, &speakers, Some(&cache), 120, 5).await.unwrap();
    assert_json_eq(&hit, &want["result"], "cache hit");
    // a cache of a different length is ignored
    let llm2 = replay(&want["calls"]);
    let fewer = extract_conversation(&llm2, &msgs[..3], &speakers, Some(&cache), 120, 0).await.unwrap();
    assert_eq!(fewer["messages"].as_array().unwrap().len(), 3);
}

#[tokio::test(flavor = "current_thread")]
async fn type_error_escapes_like_python() {
    let f = load("kg_extractor.json");
    let msgs = messages(&f, 2);
    let llm = ScriptedLlm::new(|_| Ok(r#"{"entities": 5}"#.to_string()));
    let err = extract_conversation(&llm, &msgs, &common::strings(&f["speakers"]), None, 120, 50).await.unwrap_err();
    assert_eq!(err, s(&f["type_error"]));
}

#[test]
fn helpers_match() {
    let f = load("kg_extractor.json");
    let u = &f["units"];
    for c in u["norm"].as_array().unwrap() {
        assert_eq!(norm(&c[0]), s(&c[1]), "norm {}", c[0]);
    }
    let mut registry: Registry = Registry::new();
    for (k, v) in u["registry"].as_object().unwrap() {
        registry.insert(
            k.clone(),
            RegistryEntry { entity_type: s(&v["type"]).to_string(), count: v["count"].as_i64().unwrap() },
        );
    }
    for c in u["resolve"].as_array().unwrap() {
        assert_eq!(resolve(&c[0], &registry, s(&c[1])), s(&c[2]), "resolve {}", c[0]);
    }
    for c in u["parse"].as_array().unwrap() {
        let got = match parse(s(&c[0])) {
            Ok(p) => json!({"entities": p.entities, "triples": p.triples}),
            Err(e) => json!({"error": e}),
        };
        assert_json_eq(&got, &c[1], &format!("parse {}", c[0]));
    }
    let formatted = format_template(
        PROMPT,
        &[("speakers", "{a} and {b}"), ("speaker", "{x}"), ("text", "{{y}}"), ("registry", "}")],
    );
    assert_eq!(formatted, s(&f["format_check"]));
}

#[test]
fn flatten_matches() {
    let f = load("kg_extractor.json");
    let data = locomo();
    let lens: Vec<usize> = data.as_array().unwrap().iter().map(|c| flatten(c).len()).collect();
    assert_eq!(json!(lens), f["flatten_lengths"]);
}
