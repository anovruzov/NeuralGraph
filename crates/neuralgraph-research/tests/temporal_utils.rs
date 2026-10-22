//! Parity of `temporal_utils` with Python (`tests/golden/temporal_utils.json`, written by
//! `gen_temporal_utils.py`): every LoCoMo message and question plus edge cases.

mod common;

use chrono::NaiveDateTime;
use common::{assert_json_eq, load, s};
use neuralgraph_research::pytime::PyDateTime;
use neuralgraph_research::temporal_utils::{
    expand_temporal_query, extract_duration_metadata, extract_explicit_date, generate_temporal_fields,
    generate_temporal_tokens, infer_query_mode, month_names_rev, parse_datetime_flexible, parse_number_token,
    preprocess_message_for_indexing, TemporalError, DAY_ABBREVIATIONS, DAY_NAMES_REV, NUMBER_WORDS, QUERY_MODE_MARKERS,
};
use serde_json::{json, Map, Value};
use std::path::Path;

/// One flattened LoCoMo message: `(speaker, text, session datetime)`.
type Message = (String, String, String);

/// The LoCoMo dataset, flattened like the harnesses' `flatten_messages`: `(speaker, text, session datetime)`.
fn locomo() -> (Value, Vec<Vec<Message>>) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../research/datasets/locomo10.json");
    let data: Value = serde_json::from_str(&std::fs::read_to_string(path).expect("locomo10.json")).unwrap();
    let mut convs = Vec::new();
    for conv in data.as_array().unwrap() {
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
        convs.push(out);
    }
    (data, convs)
}

fn date(v: &Value) -> Option<NaiveDateTime> {
    v.as_str().map(|s| PyDateTime::fromisoformat(s).expect("iso").naive)
}

fn iso(d: Option<NaiveDateTime>) -> Value {
    d.map(|d| json!(PyDateTime::naive(d).isoformat())).unwrap_or(Value::Null)
}

/// Python exceptions were recorded as `{"error": "<type>"}`; only "it raised" is compared.
fn normalize_errors(v: &Value) -> Value {
    match v {
        Value::Object(m) if m.len() == 1 && m.contains_key("error") => json!({"error": "E"}),
        Value::Object(m) => Value::Object(m.iter().map(|(k, x)| (k.clone(), normalize_errors(x))).collect()),
        Value::Array(a) => Value::Array(a.iter().map(normalize_errors).collect()),
        other => other.clone(),
    }
}

fn res(r: Result<Value, TemporalError>) -> Value {
    r.unwrap_or_else(|_| json!({"error": "E"}))
}

fn compact_fields(text: &str, d: Option<NaiveDateTime>) -> Map<String, Value> {
    let f = match generate_temporal_fields(text, d) {
        Ok(f) => f,
        Err(_) => return json!({"error": "E"}).as_object().unwrap().clone(),
    };
    let mut out = Map::new();
    let base = if d.is_some() { 10 } else { 0 };
    let tokens = f["temporal_tokens"].as_array().unwrap();
    if tokens.len() > base {
        out.insert("rel".into(), json!(tokens[base..]));
    }
    if f["resolved_content"] != json!(text) {
        out.insert("rc".into(), f["resolved_content"].clone());
    }
    if !f["resolved_dates"].as_object().unwrap().is_empty() {
        out.insert("rds".into(), f["resolved_dates"].clone());
    }
    if !f["explicit_date"].is_null() {
        out.insert("ex".into(), f["explicit_date"].clone());
    }
    let default_source = if d.is_some() { json!("message") } else { Value::Null };
    if f["resolved_date_source"] != default_source {
        out.insert("rd".into(), json!([f["resolved_date"], f["resolved_date_source"]]));
    }
    let fixed = [
        "resolved_date",
        "temporal_tokens",
        "temporal_metadata",
        "resolved_dates",
        "resolved_content",
        "explicit_date",
        "resolved_date_source",
    ];
    let extra: Map<String, Value> =
        f.iter().filter(|(k, _)| !fixed.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect();
    if !extra.is_empty() {
        out.insert("dur".into(), Value::Object(extra));
    }
    out
}

#[test]
fn locomo_messages_match() {
    let f = load("temporal_utils.json");
    let (_, convs) = locomo();
    for (dt, expected) in f["dates"].as_object().unwrap() {
        let parsed = parse_datetime_flexible(Some(dt)).unwrap().map(|d| d.naive);
        assert_eq!(iso(parsed), expected["parsed"], "parse {dt}");
        let tokens = generate_temporal_tokens(parsed, "").unwrap().to_dict();
        assert_json_eq(&tokens, &expected["tokens"], &format!("date tokens of {dt}"));
    }
    let mut expected_iter = f["messages"].as_array().unwrap().iter();
    for (ci, conv) in convs.iter().enumerate() {
        for (mi, (_, text, dt)) in conv.iter().enumerate() {
            let expected = expected_iter.next().expect("message count");
            let d = parse_datetime_flexible(Some(dt)).unwrap().map(|d| d.naive);
            let mut rec = compact_fields(text, d);
            let mode = infer_query_mode(text);
            let actual = if rec.is_empty() {
                json!(mode)
            } else {
                rec.insert("mode".into(), json!(mode));
                rec.insert("c".into(), json!(ci));
                rec.insert("i".into(), json!(mi));
                Value::Object(rec)
            };
            assert_json_eq(&actual, &normalize_errors(expected), &format!("message {ci}/{mi}: {text}"));
        }
    }
    assert!(expected_iter.next().is_none());
}

#[test]
fn locomo_questions_match() {
    let f = load("temporal_utils.json");
    let (data, _) = locomo();
    let mut expected_iter = f["questions"].as_array().unwrap().iter();
    for conv in data.as_array().unwrap() {
        for qa in conv["qa"].as_array().unwrap() {
            let q = s(&qa["question"]);
            let expected = expected_iter.next().expect("question count");
            let exp = expand_temporal_query(q);
            let suffix = match exp.strip_prefix(q) {
                Some(rest) => json!(rest),
                None => json!({"full": exp}),
            };
            assert_json_eq(&json!([infer_query_mode(q), suffix]), expected, q);
        }
    }
    assert!(expected_iter.next().is_none());
    for c in f["queries"].as_array().unwrap() {
        let q = s(&c["q"]);
        assert_eq!(infer_query_mode(q), s(&c["mode"]), "mode {q:?}");
        assert_eq!(expand_temporal_query(q), s(&c["expanded"]), "expand {q:?}");
    }
}

#[test]
fn edge_cases_match() {
    let f = load("temporal_utils.json");
    for c in f["edges"].as_array().unwrap() {
        let d = date(&c["date"]);
        let t = s(&c["text"]);
        let what = format!("{:?} @ {:?}", t, c["date"]);
        let actual = json!({
            "date": c["date"],
            "text": t,
            "tokens": res(generate_temporal_tokens(d, t).map(|x| x.to_dict())),
            "explicit": res(extract_explicit_date(t, d).map(|x| json!(x))),
            "duration": Value::Object(extract_duration_metadata(t, d)),
            "fields": res(generate_temporal_fields(t, d).map(Value::Object)),
            "pre": res(preprocess_message_for_indexing(t, None, d).map(Value::Object)),
        });
        assert_json_eq(&actual, &normalize_errors(c), &what);
    }
    for c in f["pre_from_string"].as_array().unwrap() {
        let out = res(preprocess_message_for_indexing(s(&c["text"]), c["dt"].as_str(), None).map(Value::Object));
        assert_json_eq(&out, &normalize_errors(&c["out"]), &format!("preprocess {c}"));
    }
    for (token, expected) in f["number_tokens"].as_object().unwrap() {
        assert_eq!(json!(parse_number_token(token)), *expected, "number token {token:?}");
    }
}

#[test]
fn constants_match() {
    let f = load("temporal_utils.json");
    let c = &f["constants"];
    let rev: Map<String, Value> = (1..=12).map(|m| (m.to_string(), json!(month_names_rev(m).unwrap()))).collect();
    assert_json_eq(&Value::Object(rev), &c["MONTH_NAMES_REV"], "MONTH_NAMES_REV");
    assert_eq!(json!(DAY_NAMES_REV), c["DAY_NAMES_REV"]);
    let abbrevs: Map<String, Value> = DAY_ABBREVIATIONS.iter().map(|(k, v)| (k.to_string(), json!(v))).collect();
    assert_json_eq(&Value::Object(abbrevs), &c["DAY_ABBREVIATIONS"], "DAY_ABBREVIATIONS");
    let words: Map<String, Value> = NUMBER_WORDS.iter().map(|(k, v)| (k.to_string(), json!(v))).collect();
    assert_json_eq(&Value::Object(words), &c["NUMBER_WORDS"], "NUMBER_WORDS");
    let mut markers = Map::new();
    for (mode, cats) in QUERY_MODE_MARKERS {
        let mut m = Map::new();
        for (cat, patterns) in *cats {
            let mut p: Vec<&str> = patterns.to_vec();
            p.sort_unstable();
            p.dedup();
            m.insert(cat.to_string(), json!(p));
        }
        markers.insert(mode.to_string(), Value::Object(m));
    }
    assert_json_eq(&Value::Object(markers), &c["QUERY_MODE_MARKERS"], "QUERY_MODE_MARKERS");
}
