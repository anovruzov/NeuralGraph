//! Parity of `retrieval::attribution` with the Python (`tests/golden/attribution.json`, written by
//! `gen_attribution.py`). The generator replaced `time.perf_counter` with a counter (start 1000.0, step 0.0125);
//! the tests pin [`pin_perf_counter`] identically, so latencies are compared exactly too.

mod common;

use common::{assert_json_eq, load, pin_now, FIXED_NOW};
use neuralgraph_memory::pycompat::py_prefix;
use neuralgraph_research::retrieval::attribution::{
    create_attribution, get_attribution_logger, perf_counter, pin_perf_counter, reset_attribution_logger,
    AttributionLogger, RetrievalAttribution, ScoreBreakdown,
};
use neuralgraph_research::retrieval::{node_ref, NeuralNode, NodeLayer, NodeRef};
use serde_json::{json, Map, Value};
use std::rc::Rc;

fn nodes_of(specs: &Value) -> Vec<NodeRef> {
    specs
        .as_array()
        .unwrap()
        .iter()
        .map(|s| {
            let mut n =
                NeuralNode::new(s["node_id"].as_str().unwrap(), NodeLayer::Message, s["content"].as_str().unwrap());
            n.metadata = s["metadata"].as_object().unwrap().clone();
            node_ref(n)
        })
        .collect()
}

fn breakdowns(v: &Value) -> Vec<ScoreBreakdown> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|b| {
            let f = |k: &str| b[k].as_f64().unwrap();
            ScoreBreakdown {
                node_id: b["node_id"].as_str().unwrap().into(),
                content_preview: b["content_preview"].as_str().unwrap().into(),
                speaker: b["speaker"].as_str().unwrap().into(),
                rank: b["rank"].as_i64().unwrap(),
                base_score: f("base_score"),
                semantic_score: f("semantic_score"),
                entity_score: f("entity_score"),
                keyword_score: f("keyword_score"),
                temporal_score: f("temporal_score"),
                speaker_boost: f("speaker_boost"),
                specificity_bonus: f("specificity_bonus"),
                rerank_delta: f("rerank_delta"),
                final_score: f("final_score"),
            }
        })
        .collect()
}

fn meta(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

fn run_scenario(sc: &Value) {
    let nodes = nodes_of(&sc["specs"]);
    let texts: Vec<String> = nodes.iter().map(|n| n.borrow().content.clone()).collect();
    for (qi, rec) in sc["records"].as_array().unwrap().iter().enumerate() {
        let question = rec["question"].as_str().unwrap();
        let answer = rec["answer"].as_str().unwrap();
        let mut attr = create_attribution(&format!("conv-{}", sc["conv_index"]), &question.repeat(1 + qi % 2));
        let bds = breakdowns(&rec["breakdowns"]);
        attr.log_stage("vector_search", 0, 50, 0.81 + qi as f64 / 100.0, 12.5 + qi as f64, None, Map::new());
        attr.log_stage(
            "entity_filter",
            50,
            45,
            0.79,
            2.25,
            Some(&bds),
            meta(&[
                ("category", rec["category"].clone()),
                ("evidence", rec["evidence"].clone()),
                ("note", json!("ok")),
            ]),
        );
        if qi % 2 == 0 {
            attr.log_stage("rerank", 45, 10, 0.92 - qi as f64 / 50.0, 45.0, Some(&[]), Map::new());
        }
        if qi % 4 == 1 {
            attr.log_stage("vector_search", 10, 10, 0.5, 1.0, None, Map::new());
        }
        let markers = if question.to_lowercase().starts_with("when") { vec!["when".to_string()] } else { vec![] };
        attr.log_router(
            ["semantic", "entity_filter", "temporal_filter"][qi % 3],
            0.25 * (qi % 4) as f64,
            strings(&rec["entities"]),
            markers,
            ["temporal", "single_hop", "multi_hop"][qi % 3],
        );
        let before: Vec<(NodeRef, f64)> =
            nodes.iter().enumerate().map(|(i, n)| (n.clone(), 1.0 - 0.03 * i as f64)).collect();
        let order: Vec<usize> = (0..nodes.len()).collect();
        let mut rot: Vec<usize> = order[qi % 5..].to_vec();
        rot.extend(&order[..qi % 5]);
        let after: Vec<(NodeRef, f64)> = rot
            .iter()
            .enumerate()
            .map(|(k, i)| (nodes[*i].clone(), 1.0 - 0.03 * k as f64 + if k % 3 == 0 { 0.005 } else { 0.02 }))
            .collect();
        attr.log_rerank(&before, &after);
        let quoted = &texts[(qi * 3) % texts.len()];
        let generated = format!("Based on the conversation: {} ... so the answer is {answer}.", py_prefix(quoted, 60));
        attr.log_evidence(&after[..after.len().min(22)], if qi % 3 != 0 { &generated } else { "" });
        let top_k = [10, 3, 25][qi % 3];
        let mut retrieved: Vec<String> = texts[qi % 4..].to_vec();
        retrieved.push(format!("The answer is {answer}."));
        let gold = if qi % 5 != 0 { answer.to_string() } else { answer.to_uppercase() };
        attr.log_gold_position(&gold, &retrieved, top_k);
        attr.generated_answer = generated.repeat(4);
        attr.gold_answer = answer.to_string();
        attr.gate_profile = ["default", "single_hop"][qi % 2].to_string();
        attr.is_list_question = answer.contains(',');
        let before_final = attr.get_summary();
        if !rec["summary_before_finalize"].is_null() {
            assert_json_eq(&before_final, &rec["summary_before_finalize"], "summary before finalize");
        }
        attr.finalize(after.len() as i64 - qi as i64, qi % 3 != 1);
        assert_json_eq(&attr.get_summary(), &rec["summary"], &format!("summary {question}"));
        if !rec["json"].is_null() {
            assert_eq!(attr.to_json(), rec["json"].as_str().unwrap(), "to_json");
        }
        assert_json_eq(&json!(attr.total_latency_ms()), &rec["latency"], "latency");
        assert_json_eq(&json!(attr.stage_latencies()), &rec["stage_latencies"], "stage latencies");
    }
}

fn logger_runs(tmp: &std::path::Path, expected: &Value) {
    let path = tmp.join("nested").join("dir").join("attr.jsonl");
    let mut logger = AttributionLogger::new(Some(&path), true, 3).unwrap();
    assert_json_eq(&logger.get_summary(), &expected["empty_summary"], "empty summary");
    let mut sizes = Vec::new();
    for i in 0..8usize {
        let mut attr = create_attribution(&format!("sess-{}", i % 2), &format!("Question number {i} about Caroline"));
        attr.query_type = ["temporal", "single_hop", "multi_hop"][i % 3].into();
        attr.router_strategy = ["semantic", "entity_filter"][i % 2].into();
        for s in 0..(if i < 4 { 25 } else { 2 }) {
            let top = if i == 5 && s % 3 == 0 { 0.0 } else { 0.5 + 0.01 * s as f64 + 0.03 * i as f64 };
            let latency = ((s * 37 + i * 11) % 23) as f64 + 0.5;
            attr.log_stage(["vector_search", "rerank", "flash_retrieve"][s % 3], 10, 5, top, latency, None, Map::new());
        }
        attr.gold_rank = i as i64;
        attr.gold_in_top_1 = i == 1;
        attr.gold_in_top_5 = 0 < i && i <= 5;
        attr.gold_in_top_10 = 0 < i && i <= 10;
        attr.answer_correct = i % 3 == 0;
        if i != 6 {
            let correct = attr.answer_correct;
            attr.finalize(i as i64, correct);
        }
        logger.add(attr.into_ref());
        sizes.push(logger.buffered().len());
    }
    assert_json_eq(&json!(sizes), &expected["buffer_sizes"], "buffer sizes");
    let lines = |p: &std::path::Path| -> Vec<String> {
        std::fs::read_to_string(p).unwrap().lines().map(str::to_string).collect()
    };
    assert_eq!(json!(lines(&path)), expected["file_after_adds"], "file after adds");
    logger.close();
    assert_eq!(json!(lines(&path)), expected["file_after_close"], "file after close");
    logger.close();
    let precision: Vec<Value> = [1, 5, 10, 3].iter().map(|k| json!(logger.get_precision_at_k(*k))).collect();
    assert_json_eq(&json!(precision), &expected["precision"], "precision");
    assert_json_eq(&json!(logger.get_latency_stats()), &expected["latency_stats"], "latency stats");
    assert_json_eq(&json!(logger.get_strategy_effectiveness()), &expected["strategy"], "strategy");
    assert_json_eq(&logger.get_rerank_gain(), &expected["rerank_gain"], "rerank gain");
    assert_json_eq(&logger.get_summary(), &expected["summary"], "logger summary");

    let mut big = AttributionLogger::new(None, true, 10).unwrap();
    for i in 0..6usize {
        let mut attr = create_attribution("big", "q");
        for s in 0..20usize {
            attr.log_stage("vector_search", 0, 0, 0.0, ((s * 53 + i * 7) % 101) as f64 / 4.0, None, Map::new());
        }
        big.add(attr.into_ref());
    }
    assert_json_eq(&json!(big.get_latency_stats()), &expected["big_latency"], "big latency");
    assert_json_eq(&big.get_rerank_gain(), &expected["big_rerank_gain"], "big rerank gain");
    big.append_to_log(&create_attribution("x", "y"));

    let disabled_path = tmp.join("disabled.jsonl");
    let mut disabled = AttributionLogger::new(Some(&disabled_path), false, 10).unwrap();
    disabled.add(create_attribution("x", "y").into_ref());
    assert_json_eq(
        &json!([disabled.attributions().len(), disabled.get_summary(), disabled_path.exists()]),
        &expected["disabled"],
        "disabled",
    );

    let single_path = tmp.join("single.jsonl");
    let mut single = AttributionLogger::new(Some(&single_path), true, 100).unwrap();
    let mut a = create_attribution("one", "Wörld “quotes” ü");
    a.finalize(1, true);
    single.append_to_log(&a);
    single.add(a.into_ref());
    assert_eq!(json!(lines(&single_path)), expected["single_before_flush"], "single before flush");
    single.flush();
    assert_eq!(json!(lines(&single_path)), expected["single_after_flush"], "single after flush");

    let g1 = get_attribution_logger();
    g1.borrow_mut().add(create_attribution("g", "q").into_ref());
    let g2 = get_attribution_logger();
    let same = Rc::ptr_eq(&g1, &g2);
    reset_attribution_logger();
    let g3 = get_attribution_logger();
    let got = json!([
        same,
        Rc::ptr_eq(&g3, &g1),
        g2.borrow().attributions().len(),
        g3.borrow().attributions().len(),
        g3.borrow().log_path().is_none()
    ]);
    assert_json_eq(&got, &expected["global"], "global logger");
    reset_attribution_logger();
}

#[test]
fn attribution_matches_python() {
    pin_now(FIXED_NOW);
    pin_perf_counter(Some((1000.0, 0.0125)));
    let f = load("attribution.json");
    for sc in f["scenarios"].as_array().unwrap() {
        run_scenario(sc);
    }

    let gold = &f["gold"];
    let texts = strings(&gold["texts"]);
    for case in gold["cases"].as_array().unwrap() {
        let mut attr = RetrievalAttribution::with_start_time("s", "q", 5.0);
        attr.log_gold_position(case["gold"].as_str().unwrap(), &texts, case["top_k"].as_u64().unwrap() as usize);
        let got = json!({
            "gold": case["gold"], "top_k": case["top_k"], "rank": attr.gold_rank,
            "flags": [attr.gold_in_top_1, attr.gold_in_top_5, attr.gold_in_top_10],
        });
        assert_json_eq(&got, case, "gold position");
    }

    let tmp = tempfile::tempdir().unwrap();
    logger_runs(tmp.path(), &f["logger"]);
    // every clock read happened in the same order as in Python
    assert_json_eq(&json!(perf_counter()), &f["perf_counter_next"], "counter");
    pin_perf_counter(None);
}
