//! Parity with the Python implementation: every fixture in `tests/golden/` was recorded from the Python reference at
//! `f4311102` by `generate.py` (removed with the rest of the Python; `git show
//! eba2f370:crates/neuralgraph-memory/tests/golden/generate.py`). These tests replay the same inputs through the Rust
//! crate and require identical outputs. Fixtures change only deliberately.

mod common;

use common::{assert_json_eq, dump_db, fixture_f64, json_diff, load, pin, strings, unpin};
use indexmap::IndexMap;
use neuralgraph_memory::difflib::{get_close_matches, SequenceMatcher};
use neuralgraph_memory::extraction::{norm_type, older, parse_when, EntityRegistry, ExtractionConfig, MemoryExtractor};
use neuralgraph_memory::jsonutil::{as_float, as_list, as_str, parse_json_array, parse_json_object};
use neuralgraph_memory::llm::{fake_embedding, FakeLlmClient, LlmClient};
use neuralgraph_memory::mcp::McpProtocol;
use neuralgraph_memory::models::{now_iso, parse_iso, Memory};
use neuralgraph_memory::pycompat::{dumps, py_fixed, py_float_parse, py_float_repr, py_repr_str, py_round, JsonFormat};
use neuralgraph_memory::retrieval::SearchOptions;
use neuralgraph_memory::server::{create_app, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, MessageInput, RememberOptions};
use neuralgraph_memory::store::{AddMessage, ChannelFilter, ChatMemoryStore, ExtractionPlan, PlanCounts};
use neuralgraph_memory::temporal::{parse_datetime_flexible, resolve_relative_dates};
use neuralgraph_memory::testing::{scripted_fake_llm, scripted_responder, third_person};
use neuralgraph_memory::textutil::{
    clip, content_hash, fold, fts_match_string, is_probable_chitchat, norm_entity, norm_relation, normalize_ws, stem,
    tokenize, tokenize_opts,
};
use serde_json::{json, Value};
use std::sync::Arc;

const INGEST_NOW: &str = "2026-09-30T08:00:00+00:00";

fn s(v: &Value) -> &str {
    v.as_str().unwrap_or_default()
}

fn opt(v: &Value) -> Option<String> {
    v.as_str().map(str::to_string)
}

// ------------------------------------------------------------------------------------------- pure functions

#[test]
fn textutil_matches_python() {
    let f = load("textutil.json");
    for c in f["cases"].as_array().unwrap() {
        let t = s(&c["text"]);
        let got = json!({
            "text": t, "normalize_ws": normalize_ws(t), "fold": fold(t), "tokenize": tokenize(t),
            "tokenize_nostem": tokenize_opts(t, false, true), "tokenize_nonum": tokenize_opts(t, true, false),
            "fts_match_string": fts_match_string(t), "norm_entity": norm_entity(t), "norm_relation": norm_relation(t),
            "is_probable_chitchat": is_probable_chitchat(t),
        });
        assert_json_eq(&got, c, &format!("textutil {t:?}"));
    }
    for (w, want) in f["stem"].as_object().unwrap() {
        assert_eq!(stem(w), s(want), "stem {w}");
    }
    for (r, want) in f["norm_relation"].as_object().unwrap() {
        assert_eq!(norm_relation(r), s(want), "norm_relation {r}");
    }
    for c in f["content_hash"].as_array().unwrap() {
        let parts = strings(&c["parts"]);
        let refs: Vec<&str> = parts.iter().map(String::as_str).collect();
        assert_eq!(content_hash(&refs), s(&c["hash"]), "content_hash {parts:?}");
    }
    for c in f["clip"].as_array().unwrap() {
        assert_eq!(clip(s(&c["text"]), c["limit"].as_u64().unwrap() as usize), s(&c["out"]), "clip {c}");
    }
}

#[test]
fn jsonutil_matches_python() {
    let f = load("jsonutil.json");
    for c in f["parse"].as_array().unwrap() {
        let t = s(&c["text"]);
        let obj = parse_json_object(t).map(Value::Object).unwrap_or(Value::Null);
        let arr = parse_json_array(t).map(Value::Array).unwrap_or(Value::Null);
        assert_json_eq(&obj, &c["object"], &format!("parse_json_object {t:?}"));
        assert_json_eq(&arr, &c["array"], &format!("parse_json_array {t:?}"));
    }
    for c in f["as_str"].as_array().unwrap() {
        let limit = c["limit"].as_u64().unwrap() as usize;
        assert_eq!(as_str(Some(&c["value"]), limit), s(&c["out"]), "as_str {c}");
    }
    for c in f["as_float"].as_array().unwrap() {
        assert_eq!(as_float(Some(&c["value"]), 0.5, 0.0, 1.0), c["out"].as_f64().unwrap(), "as_float {c}");
    }
    for c in f["as_list"].as_array().unwrap() {
        assert_json_eq(&Value::Array(as_list(Some(&c["value"]))), &c["out"], &format!("as_list {c}"));
    }
}

/// `datetime.isoformat()` of an aware datetime.
fn iso_aware(dt: &chrono::DateTime<chrono::FixedOffset>) -> String {
    use chrono::Timelike;
    let n = dt.naive_local();
    let mut out = n.format("%Y-%m-%dT%H:%M:%S").to_string();
    let us = n.nanosecond() / 1000;
    if us != 0 {
        out.push_str(&format!(".{us:06}"));
    }
    let off = dt.offset().local_minus_utc();
    let a = off.abs();
    out.push_str(&format!("{}{:02}:{:02}", if off < 0 { '-' } else { '+' }, a / 3600, (a % 3600) / 60));
    out
}

#[test]
fn temporal_matches_python() {
    let f = load("temporal.json");
    for c in f["resolve"].as_array().unwrap() {
        let base = parse_iso(Some(s(&c["base"]))).unwrap().naive_local();
        let got = resolve_relative_dates(s(&c["text"]), Some(base));
        match (&got, c["error"].is_null()) {
            (Ok((text, vals)), true) => {
                assert_eq!(text, s(&c["out"]), "resolve {c}");
                let vals: Value = vals
                    .iter()
                    .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                    .collect::<serde_json::Map<_, _>>()
                    .into();
                assert_json_eq(&vals, &c["resolved"], &format!("resolved {c}"));
            }
            (Err(_), false) => {}
            _ => panic!("resolve outcome differs for {c}: {got:?}"),
        }
    }
    for c in f["flexible"].as_array().unwrap() {
        let got = parse_datetime_flexible(Some(s(&c["text"])));
        match got {
            Ok(v) => {
                assert!(c["error"].is_null(), "python raised for {c}");
                assert_eq!(v.map(|d| d.isoformat()), opt(&c["iso"]), "flexible {c}");
            }
            Err(_) => assert!(!c["error"].is_null(), "rust raised for {c}"),
        }
    }
    for c in f["parse_when"].as_array().unwrap() {
        let (t, p) = parse_when(s(&c["when"]), c["observed_at"].as_str());
        assert_json_eq(&json!([t, p]), &c["out"], &format!("parse_when {c}"));
    }
    for c in f["parse_iso"].as_array().unwrap() {
        assert_eq!(parse_iso(Some(s(&c["text"]))).map(|d| iso_aware(&d)), opt(&c["iso"]), "parse_iso {c}");
    }
    for c in f["older"].as_array().unwrap() {
        assert_eq!(older(c["a"].as_str(), c["b"].as_str()), c["out"].as_bool().unwrap(), "older {c}");
    }
}

#[test]
fn pycompat_matches_python() {
    let f = load("pycompat.json");
    for c in f["float_repr"].as_array().unwrap() {
        assert_eq!(py_float_repr(c["x"].as_f64().unwrap()), s(&c["repr"]), "repr {c}");
    }
    for c in f["round"].as_array().unwrap() {
        let x = c["x"].as_f64().unwrap();
        let n = c["n"].as_u64().unwrap() as usize;
        assert_eq!(py_round(x, n).to_bits(), c["out"].as_f64().unwrap().to_bits(), "round {c}");
        assert_eq!(py_fixed(x, n), s(&c["fixed"]), "fixed {c}");
    }
    for c in f["dumps"].as_array().unwrap() {
        let v = &c["value"];
        let v = if v.get("float").is_some() {
            serde_json::Number::from_f64(fixture_f64(v)).map(Value::Number).unwrap_or(Value::Null)
        } else {
            v.clone()
        };
        assert_eq!(dumps(&v, &JsonFormat::python_default()), s(&c["default"]), "dumps {c}");
        assert_eq!(dumps(&v, &JsonFormat::unicode()), s(&c["unicode"]), "dumps unicode {c}");
        assert_eq!(dumps(&v, &JsonFormat::stored()), s(&c["stored"]), "dumps stored {c}");
        assert_eq!(dumps(&v, &JsonFormat::pretty()), s(&c["pretty"]), "dumps pretty {c}");
        assert_eq!(dumps(&v, &JsonFormat::compact()), s(&c["compact"]), "dumps compact {c}");
    }
    for c in f["repr_str"].as_array().unwrap() {
        assert_eq!(py_repr_str(s(&c["s"])), s(&c["repr"]), "repr str {c}");
    }
    for c in f["float_parse"].as_array().unwrap() {
        let got = py_float_parse(s(&c["s"]));
        match &c["out"] {
            Value::String(e) if e == "error" => assert!(got.is_none(), "float() should fail for {c}"),
            Value::Null => assert!(got.is_some_and(f64::is_nan), "nan for {c}"),
            other => assert_eq!(got, Some(fixture_f64(other)), "float() {c}"),
        }
    }
}

#[test]
fn difflib_matches_python() {
    let f = load("difflib.json");
    for c in f["pairs"].as_array().unwrap() {
        let mut m = SequenceMatcher::new(s(&c["a"]), s(&c["b"]));
        assert_eq!(m.ratio(), c["ratio"].as_f64().unwrap(), "ratio {c}");
        assert_eq!(m.quick_ratio(), c["quick"].as_f64().unwrap(), "quick {c}");
        assert_eq!(m.real_quick_ratio(), c["real_quick"].as_f64().unwrap(), "real_quick {c}");
    }
    let words: Vec<String> = f["pairs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| s(&c["a"]).to_string())
        .collect::<indexmap::IndexSet<_>>()
        .into_iter()
        .collect();
    for c in f["close"].as_array().unwrap() {
        let got = get_close_matches(s(&c["word"]), words.iter().map(String::as_str), 3, c["cutoff"].as_f64().unwrap());
        assert_eq!(got, strings(&c["out"]), "close {c}");
    }
}

#[test]
fn registry_matches_python() {
    let f = load("registry.json");
    let mut r = EntityRegistry::default();
    for c in f["cases"].as_array().unwrap() {
        match s(&c["op"]) {
            "add" => {
                r.add(s(&c["id"]), s(&c["name"]), s(&c["type"]), c["count"].as_i64().unwrap(), &strings(&c["aliases"]))
            }
            "resolve" => {
                let got = r.resolve(
                    Some(&c["mention"]),
                    c["speaker_id"].as_str(),
                    s(&c["etype"]),
                    c["create"].as_bool().unwrap(),
                );
                assert_eq!(got, opt(&c["out"]), "resolve {c}");
            }
            _ => {
                assert_eq!(r.prompt_text(80), s(&c["prompt_text"]), "prompt_text");
                let new: serde_json::Map<String, Value> = r
                    .new_entities
                    .iter()
                    .map(|(k, v)| (k.clone(), json!({"name": v.name, "type": v.etype, "count": v.count})))
                    .collect();
                assert_json_eq(&Value::Object(new), &c["new_entities"], "new_entities");
                let aliases: serde_json::Map<String, Value> =
                    r.aliases.iter().map(|(k, v)| (k.clone(), Value::String(v.clone()))).collect();
                assert_json_eq(&Value::Object(aliases), &c["aliases"], "aliases");
            }
        }
    }
    for (t, want) in f["norm_type"].as_object().unwrap() {
        assert_eq!(norm_type(t), s(want), "norm_type {t:?}");
    }
}

#[test]
fn prompts_match_python() {
    use neuralgraph_memory::prompts::*;
    let f = load("prompts.json");
    for (name, val) in [
        ("MEMORY_PROMPT", MEMORY_PROMPT),
        ("ASSISTANT_RULE_OFF", ASSISTANT_RULE_OFF),
        ("ASSISTANT_RULE_ON", ASSISTANT_RULE_ON),
        ("ASSISTANT_SKIP_OFF", ASSISTANT_SKIP_OFF),
        ("ASSISTANT_SKIP_ON", ASSISTANT_SKIP_ON),
        ("RELATION_PROMPT", RELATION_PROMPT),
        ("RECONCILE_PROMPT", RECONCILE_PROMPT),
        ("REPAIR_SUFFIX", REPAIR_SUFFIX),
    ] {
        assert_eq!(val, s(&f[name]), "prompt constant {name}");
    }
}

#[test]
fn fake_llm_matches_python() {
    let f = load("fake_llm.json");
    for c in f["embeddings"].as_array().unwrap() {
        let got = fake_embedding(s(&c["text"]), c["dim"].as_u64().unwrap() as usize);
        let want: Vec<f64> = c["vec"].as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect();
        assert_eq!(got, want, "fake_embedding {:?} dim {}", c["text"], c["dim"]);
    }
    for c in f["responder"].as_array().unwrap() {
        let got = scripted_responder(s(&c["prompt"])).expect("responder");
        assert_eq!(got, s(&c["response"]), "scripted response for prompt:\n{}", s(&c["prompt"]));
    }
    for c in f["third_person"].as_array().unwrap() {
        assert_eq!(third_person(s(&c["text"]), s(&c["name"])).unwrap(), s(&c["out"]), "third_person {c}");
    }
}

// ------------------------------------------------------------------------------------------- ingest

/// Worker ids are random per process (`w-xxxxxxxx`) in both implementations; everything else must match.
fn mask_worker_ids(mut dump: Value) -> Value {
    if let Some(jobs) = dump.get_mut("jobs").and_then(Value::as_array_mut) {
        for j in jobs {
            if j["worker_id"].is_string() {
                j["worker_id"] = json!("<worker>");
            }
        }
    }
    dump
}

async fn ingest_like_cli(cm: &ChatMemory, path: &std::path::Path) {
    let raw = std::fs::read_to_string(path).unwrap();
    for line in raw.lines().filter(|l| !l.trim().is_empty()) {
        let rec: Value = serde_json::from_str(line).unwrap();
        let input = MessageInput {
            role: rec["role"].as_str().unwrap_or("").to_string(),
            sent_at: rec["sent_at"].as_str().map(str::to_string),
            message_id: rec["message_id"].as_str().map(str::to_string),
            metadata: None,
        };
        let speaker = rec["speaker"].as_str().or(rec["role"].as_str()).unwrap_or("user");
        cm.add_message(s(&rec["chat_id"]), speaker, rec["text"].as_str().unwrap_or(""), input).unwrap();
    }
}

fn metrics_subset(m: &Value) -> Value {
    let mut o = m.as_object().unwrap().clone();
    for k in ["recent", "started_at", "last_batch_seconds"] {
        o.shift_remove(k);
    }
    Value::Object(o)
}

#[tokio::test]
async fn ingest_matches_python() {
    let f = load("ingest.json");
    let sample = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../demo/data/sample_chats.jsonl");
    for case in f.as_array().unwrap() {
        let v = &case["variant"];
        let name = s(&v["name"]);
        pin(INGEST_NOW, 0);
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join(format!("ingest_{name}.db"));
        let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
        cfg.worker.batch_size = v["batch_size"].as_i64().unwrap();
        if let Some(u) = v["user_name"].as_str() {
            cfg.extraction.speaker_names.insert("user".into(), u.into());
        }
        cfg.extraction.extract_assistant = v["extract_assistant"].as_bool().unwrap();
        let llm = Arc::new(scripted_fake_llm());
        let cm = ChatMemory::open(db.to_str().unwrap(), Some(llm.clone()), cfg).unwrap();
        ingest_like_cli(&cm, &sample).await;
        let batches = cm.process_pending(None).await.unwrap();
        assert_eq!(batches, case["batches"].as_i64().unwrap(), "{name}: batches");
        let prompts = llm.prompts();
        let want_prompts = strings(&case["prompts"]);
        assert_eq!(prompts.len(), want_prompts.len(), "{name}: prompt count");
        for (i, (a, b)) in prompts.iter().zip(&want_prompts).enumerate() {
            assert_eq!(a, b, "{name}: prompt {i} differs");
        }
        assert_eq!(*llm.embedded.lock().unwrap(), strings(&case["embedded"]), "{name}: embedded texts");
        assert_json_eq(&metrics_subset(&cm.worker.metrics().as_value()), &case["metrics"], &format!("{name}: metrics"));
        assert_json_eq(&Value::Array(cm.worker.metrics().recent.clone()), &case["recent"], &format!("{name}: recent"));
        assert_json_eq(&cm.token_ledger().unwrap(), &case["ledger"], &format!("{name}: ledger"));
        assert_json_eq(&cm.grade().unwrap(), &case["grade"], &format!("{name}: grade"));
        assert_json_eq(&cm.status().unwrap()["llm"], &case["llm_stats"], &format!("{name}: llm stats"));
        cm.close().await;
        assert_json_eq(
            &mask_worker_ids(dump_db(&db)),
            &mask_worker_ids(case["db"].clone()),
            &format!("{name}: database"),
        );
        unpin();
    }
}

// ------------------------------------------------------------------------------------------- extraction scenarios

fn plan_dump(plan: &ExtractionPlan) -> Value {
    json!({
        "chat_id": plan.chat_id, "job_ids": plan.job_ids,
        "entities": plan.entities.iter().map(|e| json!({"entity_id": e.entity_id, "name": e.name, "type": e.etype,
            "seen_at": e.seen_at, "mentions": e.mentions, "aliases": e.aliases})).collect::<Vec<_>>(),
        "new_memories": plan.new_memories.iter().map(|(m, s, e)| json!({"memory": m.to_value(true), "sources": s, "entity_ids": e})).collect::<Vec<_>>(),
        "supersedes": plan.supersedes.iter().map(|(a, b, c)| json!([a, b, c])).collect::<Vec<_>>(),
        "duplicate_sources": plan.duplicate_sources.iter().map(|(a, b)| json!([a, b])).collect::<Vec<_>>(),
        "relations": plan.relations.iter().map(|r| json!([r.subject, r.predicate, r.object, r.confidence, r.memory_id, r.message_id, r.observed_at])).collect::<Vec<_>>(),
        "links": plan.links.iter().map(|(a, b, c, w)| json!([a, b, c, w])).collect::<Vec<_>>(),
        "processed_message_ids": plan.processed_message_ids,
        "skipped_message_ids": plan.skipped_message_ids,
        "audit": plan.audit.iter().map(|(k, r, d)| json!([k, r, d])).collect::<Vec<_>>(),
    })
}

fn counts_json(c: &PlanCounts) -> Value {
    json!({"memories_added": c.memories_added, "memories_superseded": c.memories_superseded, "duplicates": c.duplicates,
           "entities": c.entities, "relations": c.relations, "links": c.links})
}

fn extraction_config(overrides: &Value) -> ExtractionConfig {
    let mut cfg = ExtractionConfig::default();
    if let Some(o) = overrides.as_object() {
        for (k, v) in o {
            match k.as_str() {
                "extract_assistant" => cfg.extract_assistant = v.as_bool().unwrap(),
                "reconcile_cosine" => cfg.reconcile_cosine = v.as_f64().unwrap(),
                "dedupe_cosine" => cfg.dedupe_cosine = v.as_f64().unwrap(),
                "speaker_names" => {
                    cfg.speaker_names = v
                        .as_object()
                        .unwrap()
                        .iter()
                        .map(|(a, b)| (a.clone(), s(b).to_string()))
                        .collect::<IndexMap<_, _>>()
                }
                other => panic!("unsupported config override {other}"),
            }
        }
    }
    cfg
}

#[tokio::test]
async fn extraction_scenarios_match_python() {
    let f = load("extraction.json");
    for sc in f.as_array().unwrap() {
        let name = s(&sc["name"]);
        pin(INGEST_NOW, 0);
        let tmp = tempfile::tempdir().unwrap();
        let db = tmp.path().join(format!("sc_{name}.db"));
        let store = Arc::new(ChatMemoryStore::open(db.to_str().unwrap()).unwrap());
        let mut msgs = Vec::new();
        let mut results = Vec::new();
        for step in sc["steps"].as_array().unwrap() {
            match s(&step["op"]) {
                "add_message" => {
                    let opts = AddMessage {
                        role: step["role"].as_str().unwrap_or("").into(),
                        sent_at: opt(&step["sent_at"]),
                        ..Default::default()
                    };
                    let (m, _) =
                        store.add_message(s(&step["chat_id"]), s(&step["speaker"]), s(&step["text"]), &opts).unwrap();
                    msgs.push(m);
                }
                "insert_memory" => {
                    let now = now_iso();
                    let subject = s(&step["subject"]);
                    let mut m = Memory::new(
                        neuralgraph_memory::models::new_id("mem"),
                        s(&step["text"]).into(),
                        s(&step["kind"]).into(),
                        subject.into(),
                        common::title_case(subject),
                        common::title_case(subject),
                        s(&step["chat_id"]).into(),
                        step["importance"].as_f64().unwrap(),
                        0.9,
                        s(&step["observed_at"]).into(),
                        now.clone(),
                        now,
                    );
                    m.event_time = opt(&step["event_time"]);
                    m.event_time_precision = step["event_time_precision"].as_str().unwrap_or("none").into();
                    m.text_hash = content_hash(&[&s(&step["text"]).to_lowercase()]);
                    m.embedding = Some(fake_embedding(s(&step["embedding_text"]), 256));
                    let srcs: Vec<String> = step["sources_from_messages"]
                        .as_array()
                        .map(|a| a.iter().map(|i| msgs[i.as_u64().unwrap() as usize].message_id.clone()).collect())
                        .unwrap_or_default();
                    store.insert_memory(&m, &srcs, &strings(&step["entities"])).unwrap();
                }
                "build_plan" => {
                    let cfg = extraction_config(&step["config"]);
                    let mut llm = match step.get("script") {
                        Some(sc) => FakeLlmClient::with_script(
                            sc.as_array()
                                .unwrap()
                                .iter()
                                .map(|p| (p[0].as_str().unwrap(), s(&p[1]).to_string()))
                                .collect(),
                        ),
                        None => FakeLlmClient::with_default(s(&step["default_response"])),
                    };
                    if let Some(e) = step["embed_error"].as_str() {
                        llm.embed_error = Some(e.into());
                    }
                    let llm = Arc::new(llm);
                    let batch: Vec<_> = step["messages"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|i| store.get_message(&msgs[i.as_u64().unwrap() as usize].message_id).unwrap().unwrap())
                        .collect();
                    let ex = MemoryExtractor::new(store.clone(), llm.clone(), cfg);
                    let attempt = step["attempt"].as_i64().unwrap_or(1);
                    let mut res = match ex.build_plan(&batch, &[], attempt).await {
                        Ok(plan) => {
                            let mut r = json!({"plan": plan_dump(&plan), "error": null});
                            if step["apply"].as_bool().unwrap_or(false) {
                                r["counts"] = counts_json(&store.apply_plan(&plan, None).unwrap());
                            }
                            r
                        }
                        Err(e) => json!({"plan": null, "error": e.qualified()}),
                    };
                    res["prompts"] = json!(llm.prompts());
                    res["generate_calls"] = json!(llm.generate_calls());
                    results.push(res);
                }
                other => panic!("unknown step {other}"),
            }
        }
        store.close();
        for (i, (got, want)) in results.iter().zip(sc["results"].as_array().unwrap()).enumerate() {
            assert_json_eq(got, want, &format!("scenario {name} step {i}"));
        }
        assert_json_eq(&dump_db(&db), &sc["db"], &format!("scenario {name}: database"));
        unpin();
    }
}

// ------------------------------------------------------------------------------------------- retrieval

fn search_options(o: &Value) -> SearchOptions {
    let mut opts = SearchOptions::default();
    for (k, v) in o.as_object().unwrap() {
        match k.as_str() {
            "k" => opts.k = v.as_u64().unwrap() as usize,
            "channels" => opts.channels = opt(v),
            "kinds" => opts.kinds = Some(strings(v)),
            "subject" => opts.subject = opt(v),
            "speaker" => opts.speaker = opt(v),
            "chat_id" => opts.chat_id = opt(v),
            "since" => opts.since = opt(v),
            "until" => opts.until = opt(v),
            "include_superseded" => opts.include_superseded = v.as_bool().unwrap(),
            "with_sources" => opts.with_sources = v.as_bool().unwrap(),
            "query_embedding" => {
                opts.query_embedding = Some(v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect())
            }
            other => panic!("unsupported search option {other}"),
        }
    }
    opts
}

async fn apply_setup(cm: &ChatMemory, steps: &Value) {
    for st in steps.as_array().unwrap() {
        match s(&st["op"]) {
            "remember" => {
                let opts = RememberOptions {
                    subject: s(&st["subject"]).into(),
                    kind: s(&st["kind"]).into(),
                    importance: st["importance"].as_f64().unwrap_or(0.8),
                    when: opt(&st["when"]),
                    ..Default::default()
                };
                cm.remember(s(&st["text"]), &opts).await.unwrap();
            }
            "upsert_entity" => {
                cm.store
                    .upsert_entity(
                        s(&st["entity_id"]),
                        s(&st["name"]),
                        s(&st["type"]),
                        None,
                        1,
                        &strings(&st["aliases"]),
                    )
                    .unwrap();
            }
            "supersede" => cm.store.supersede(s(&st["old"]), s(&st["new"]), s(&st["link"])).unwrap(),
            "add_link" => cm
                .store
                .add_link(s(&st["source"]), s(&st["target"]), s(&st["type"]), st["weight"].as_f64().unwrap())
                .unwrap(),
            "retract" => {
                cm.retract(s(&st["memory_id"]), s(&st["reason"])).unwrap();
            }
            "upsert_relation" => {
                cm.store
                    .upsert_relation(
                        s(&st["s"]),
                        s(&st["p"]),
                        s(&st["o"]),
                        0.6,
                        s(&st["chat_id"]),
                        st["memory_id"].as_str(),
                        None,
                        None,
                    )
                    .unwrap();
            }
            other => panic!("unknown setup op {other}"),
        }
    }
}

fn copy_fixture_db(dir: &std::path::Path, name: &str) -> std::path::PathBuf {
    let db = dir.join(name);
    std::fs::copy(common::golden_dir().join("ingest_user_named.sqlite"), &db).unwrap();
    db
}

#[tokio::test]
async fn retrieval_matches_python() {
    let f = load("retrieval.json");
    pin(s(&f["now"]), 500000);
    let tmp = tempfile::tempdir().unwrap();
    let db = copy_fixture_db(tmp.path(), "retrieval.db");
    let cm =
        ChatMemory::open(db.to_str().unwrap(), Some(Arc::new(FakeLlmClient::default())), ChatMemoryConfig::default())
            .unwrap();
    apply_setup(&cm, &f["setup"]).await;
    for c in f["search"].as_array().unwrap() {
        let hits = cm.search(s(&c["query"]), &search_options(&c["opts"])).await.unwrap();
        let got = Value::Array(hits.iter().map(|h| h.to_value()).collect());
        assert_json_eq(&got, &c["results"], &format!("search {:?} {}", c["query"], c["opts"]));
    }
    for c in f["context"].as_array().unwrap() {
        let o = c["opts"].as_object().unwrap();
        let k = o.get("k").and_then(Value::as_u64).map(|v| v as usize);
        let mc = o.get("max_chars").and_then(Value::as_u64).map(|v| v as usize);
        let mut rest = c["opts"].clone();
        rest.as_object_mut().unwrap().shift_remove("k");
        rest.as_object_mut().unwrap().shift_remove("max_chars");
        let (block, res) = cm
            .retriever
            .context_with_results(
                s(&c["query"]),
                k,
                mc,
                neuralgraph_memory::retrieval::DEFAULT_HEADER,
                &search_options(&rest),
            )
            .await
            .unwrap();
        assert_eq!(block, s(&c["block"]), "context block {c}");
        let ids: Vec<String> = res.iter().map(|r| r.memory.memory_id.clone()).collect();
        assert_eq!(ids, strings(&c["ids"]), "context ids {c}");
    }
    for c in f["profile"].as_array().unwrap() {
        assert_json_eq(&cm.profile(s(&c["subject"]), 60).unwrap(), &c["out"], &format!("profile {}", c["subject"]));
    }
    for c in f["related"].as_array().unwrap() {
        assert_json_eq(
            &Value::Array(cm.related(s(&c["memory_id"])).unwrap()),
            &c["out"],
            &format!("related {}", c["memory_id"]),
        );
    }
    for c in f["timeline"].as_array().unwrap() {
        let o = &c["opts"];
        let mems = cm
            .timeline(
                o["subject"].as_str(),
                o["since"].as_str(),
                o["until"].as_str(),
                o["limit"].as_i64().unwrap_or(100),
            )
            .unwrap();
        let ids: Vec<String> = mems.iter().map(|m| m.memory_id.clone()).collect();
        assert_eq!(ids, strings(&c["ids"]), "timeline {o}");
    }
    for c in f["query_entities"].as_array().unwrap() {
        assert_eq!(cm.retriever.query_entities(s(&c["query"])).unwrap(), strings(&c["out"]), "query_entities {c}");
    }
    assert_json_eq(&cm.token_ledger().unwrap(), &f["ledger"], "ledger");
    assert_json_eq(&cm.grade().unwrap(), &f["grade"], "grade");
    cm.close().await;
    assert_json_eq(&dump_db(&db), &f["db_after"], "retrieval database after queries");
    unpin();
}

#[test]
fn bm25_fallback_matches_python() {
    let f = load("bm25.json");
    let tmp = tempfile::tempdir().unwrap();
    let db = copy_fixture_db(tmp.path(), "bm25.db");
    let store = ChatMemoryStore::open(db.to_str().unwrap()).unwrap();
    store.set_has_fts(false);
    for c in f.as_array().unwrap() {
        let fl = &c["filters"];
        let filter = ChannelFilter {
            subject: opt(&fl["subject"]),
            chat_id: opt(&fl["chat_id"]),
            kinds: fl.get("kinds").map(strings),
            since: opt(&fl["since"]),
            ..Default::default()
        };
        let status = fl["status"].as_str().unwrap_or("active");
        let got = store.keyword_candidates(s(&c["query"]), 50, status, &filter).unwrap();
        let got = Value::Array(got.into_iter().map(|(id, sc)| json!([id, sc])).collect());
        assert_json_eq(&got, &c["out"], &format!("bm25 {c}"));
    }
}

// ------------------------------------------------------------------------------------------- MCP and REST

fn mcp_chat_memory(db: &std::path::Path) -> Arc<ChatMemory> {
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    cfg.extraction.speaker_names.insert("user".into(), "Ali Novruzov".into());
    Arc::new(ChatMemory::open(db.to_str().unwrap(), Some(Arc::new(scripted_fake_llm())), cfg).unwrap())
}

#[tokio::test]
async fn mcp_matches_python() {
    let f = load("mcp.json");
    pin("2026-10-15T12:30:00+00:00", 500000);
    let tmp = tempfile::tempdir().unwrap();
    let db = copy_fixture_db(tmp.path(), "mcp.db");
    let cm = mcp_chat_memory(&db);
    let proto = McpProtocol::new(cm.clone());
    for ex in f["exchanges"].as_array().unwrap() {
        let req = &ex["request"];
        let got = proto.handle_payload(req).await.unwrap_or(Value::Null);
        assert_json_eq(&got, &ex["response"], &format!("mcp {req}"));
        if req.get("id") == Some(&json!(28)) {
            cm.process_pending(None).await.unwrap();
        }
    }
    cm.close().await;
    assert_json_eq(
        &mask_worker_ids(dump_db(&db)),
        &mask_worker_ids(f["db_after"].clone()),
        "mcp database after the session",
    );
    unpin();
}

fn mask_volatile(v: &mut Value) {
    if let Value::Object(o) = v {
        if o.contains_key("db_path") {
            o.insert("db_path".into(), json!("<db>"));
        }
        if let Some(Value::Object(w)) = o.get_mut("worker") {
            if w.contains_key("worker_id") {
                w.insert("worker_id".into(), json!("<worker>"));
            }
        }
    }
}

#[tokio::test]
async fn rest_matches_python() {
    use axum::body::Body;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    let f = load("rest.json");
    pin("2026-10-15T12:30:00+00:00", 500000);
    let tmp = tempfile::tempdir().unwrap();
    let db = copy_fixture_db(tmp.path(), "rest.db");
    let cm = mcp_chat_memory(&db);
    let strs = |v: &[&str]| Some(v.iter().map(|x| x.to_string()).collect::<Vec<_>>());
    let apps: IndexMap<&str, axum::Router> = IndexMap::from([
        ("plain", create_app(cm.clone(), ServerOptions::default())),
        ("mcp_token", create_app(cm.clone(), ServerOptions { mcp_token: Some("secret".into()), ..Default::default() })),
        (
            "cors_hosts",
            create_app(
                cm.clone(),
                ServerOptions {
                    cors_origins: strs(&["https://claude.ai"]),
                    allowed_hosts: strs(&["localhost", "127.0.0.1", "[::1]"]),
                    ..Default::default()
                },
            ),
        ),
        (
            "api_token",
            create_app(cm.clone(), ServerOptions { api_token: Some("api-secret".into()), ..Default::default() }),
        ),
        (
            "origins",
            create_app(
                cm.clone(),
                ServerOptions {
                    allowed_origins: strs(&["https://ok.example"]),
                    cors_origins: strs(&["*"]),
                    ..Default::default()
                },
            ),
        ),
    ]);
    let kept = [
        "content-type",
        "cache-control",
        "allow",
        "access-control-allow-origin",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-expose-headers",
        "vary",
        "www-authenticate",
        "mcp-protocol-version",
    ];
    for ex in f["exchanges"].as_array().unwrap() {
        let r = &ex["request"];
        let mut builder = axum::http::Request::builder().method(s(&r["method"])).uri(s(&r["path"]));
        for (k, v) in r["headers"].as_object().unwrap() {
            builder = builder.header(k.as_str(), s(v));
        }
        let body = if let Some(b) = r.get("body") {
            Body::from(dumps(b, &JsonFormat::python_default()))
        } else if let Some(raw) = r["raw"].as_str() {
            Body::from(raw.to_string())
        } else {
            Body::empty()
        };
        let resp = apps[s(&r["app"])].clone().oneshot(builder.body(body).unwrap()).await.unwrap();
        let status = resp.status().as_u16();
        let mut headers = serde_json::Map::new();
        for k in kept {
            if let Some(v) = resp.headers().get(k) {
                headers.insert(k.to_string(), json!(v.to_str().unwrap()));
            }
        }
        if let Some(sid) = resp.headers().get("mcp-session-id") {
            let echoed = r["headers"].get("Mcp-Session-Id").is_some();
            headers.insert(
                "mcp-session-id".into(),
                if echoed { json!(sid.to_str().unwrap()) } else { json!("<session>") },
            );
        }
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let text = String::from_utf8_lossy(&bytes).into_owned();
        let what = format!("{} {} {}", s(&r["app"]), s(&r["method"]), s(&r["path"]));
        assert_eq!(status as u64, ex["status"].as_u64().unwrap(), "{what}: status (body {text})");
        assert_json_eq(&Value::Object(headers), &ex["headers"], &format!("{what}: headers"));
        if s(&ex["kind"]) == "json" {
            let mut got: Value =
                serde_json::from_str(&text).unwrap_or_else(|e| panic!("{what}: body is not JSON ({e}): {text}"));
            let mut want = ex["body"].clone();
            mask_volatile(&mut got);
            mask_volatile(&mut want);
            assert_json_eq(&got, &want, &format!("{what}: body"));
        } else {
            assert_eq!(text, s(&ex["body"]), "{what}: text body");
        }
    }
    cm.close().await;
    unpin();
}

#[test]
fn float_tolerance_helper_reports_paths() {
    let d = json_diff(&json!({"a": [1.0, 2.0]}), &json!({"a": [1.0, 2.5]}), "$", 0.0).unwrap();
    assert!(d.starts_with("$.a[1]"), "{d}");
    let _ = LlmClient::model(&FakeLlmClient::default());
}
