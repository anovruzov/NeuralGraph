//! Port of the pytest suite `NeuralGraph/tests/test_single_hop_regression.py` (classes TestSingleHopRegression,
//! TestRegressionMetrics, TestBaselineComparison, TestSingleHopIntegration), plus parity of its helpers with the
//! Python (`tests/golden/harness.json`, section `single_hop`).

mod common;

use common::{assert_json_eq, load, s};
use neuralgraph_research::benchmarks::pyharness::set_fixed_perf_counter;
use neuralgraph_research::benchmarks::single_hop_regression::{
    baseline_precision_1, check_answer_in_results, compare_to_baseline, regression_results_json, run_regression_suite,
    save_regression_results, single_hop_test_questions, standalone_report, RegressionResult,
    MIN_SAMPLES_FOR_REGRESSION, REGRESSION_THRESHOLD,
};
use serde_json::{json, Value};
use std::collections::BTreeSet;

fn fixture() -> Value {
    load("harness.json")["single_hop"].clone()
}

// ------------------------------------------------------------------------------------------- TestSingleHopRegression

#[test]
fn test_questions_not_empty() {
    let qs = single_hop_test_questions();
    assert!(
        qs.len() >= MIN_SAMPLES_FOR_REGRESSION,
        "Need at least {MIN_SAMPLES_FOR_REGRESSION} questions, have {}",
        qs.len()
    );
}

#[test]
fn test_question_categories() {
    let categories: BTreeSet<String> = single_hop_test_questions().into_iter().map(|q| q.category).collect();
    assert!(categories.len() >= 5, "Need at least 5 categories for diverse testing, have {}", categories.len());
}

#[test]
fn test_questions_have_entities() {
    for q in single_hop_test_questions() {
        assert!(!q.expected_entity.is_empty(), "Question missing entity: {}", q.question);
        assert!(!q.expected_answer.is_empty(), "Question missing answer: {}", q.question);
    }
}

// ------------------------------------------------------------------------------------------- TestRegressionMetrics

#[test]
fn test_precision_calculation() {
    let result = RegressionResult {
        total_questions: 10,
        correct_at_1: 7,
        correct_at_5: 9,
        correct_at_10: 10,
        ..Default::default()
    };
    assert_eq!(result.precision_at_1(), 0.7);
    assert_eq!(result.precision_at_5(), 0.9);
    assert_eq!(result.precision_at_10(), 1.0);
}

#[test]
fn test_empty_result() {
    let result = RegressionResult::default();
    assert_eq!(result.precision_at_1(), 0.0);
    assert_eq!(result.avg_latency_ms(), 0.0);
    assert_eq!(result.p95_latency_ms(), 0.0);
}

#[test]
fn test_latency_metrics() {
    let result = RegressionResult {
        total_questions: 5,
        latencies_ms: vec![10.0, 20.0, 30.0, 40.0, 100.0],
        ..Default::default()
    };
    assert_eq!(result.avg_latency_ms(), 40.0);
    assert!(result.p95_latency_ms() >= 40.0);
}

// ------------------------------------------------------------------------------------------- TestBaselineComparison

#[test]
fn test_regression_detection() {
    let baseline = baseline_precision_1().unwrap();
    let threshold = REGRESSION_THRESHOLD;
    // No regression
    let current = baseline;
    assert!(current >= baseline - threshold, "Should not detect regression");
    // Slight drop (within threshold)
    let current = baseline - 0.005;
    assert!(current >= baseline - threshold, "Small drop should be OK");
    // Regression (beyond threshold)
    let current = baseline - 0.02;
    assert!(current < baseline - threshold, "Should detect regression");
}

// ------------------------------------------------------------------------------------------- TestSingleHopIntegration

/// The Python class was skipped ("Requires full NeuralGraph setup with data") and its fixture imported
/// `..research.retrieval.service` relative to the tests package, which never resolved; the test body only checked a
/// mocked 70% against the baseline. Kept as an ignored test with the same mock so the intent is not lost.
#[test]
#[ignore = "Requires full NeuralGraph setup with data"]
fn test_precision_at_1_meets_baseline() {
    let questions = single_hop_test_questions();
    let result = RegressionResult {
        total_questions: questions.len(),
        correct_at_1: (questions.len() as f64 * 0.70) as usize, // Mock 70%
        ..Default::default()
    };
    let baseline = baseline_precision_1().unwrap();
    assert!(result.precision_at_1() >= baseline - REGRESSION_THRESHOLD, "Precision@1 regression detected");
}

// ------------------------------------------------------------------------------------------- parity with Python

#[test]
fn questions_and_answer_checks_match_python() {
    let f = fixture();
    let qs: Vec<Value> = single_hop_test_questions()
        .iter()
        .map(|q| {
            json!({"question": q.question, "expected_answer": q.expected_answer, "expected_entity": q.expected_entity,
                   "category": q.category, "difficulty": q.difficulty})
        })
        .collect();
    assert_json_eq(&Value::from(qs), &f["questions"], "SINGLE_HOP_TEST_QUESTIONS");
    let results: Vec<(String, f64)> = [
        ("The teacher Caroline", 0.9),
        ("New York, NY", 0.8),
        ("nothing", 0.5),
        ("Toyota corolla", 0.4),
        ("it is blue", 0.3),
        ("COMPUTER science", 0.2),
    ]
    .iter()
    .map(|(c, s)| (c.to_string(), *s))
    .collect();
    for c in f["checks"].as_array().unwrap() {
        let k = c["k"].as_u64().unwrap() as usize;
        assert_eq!(
            check_answer_in_results(s(&c["expected"]), &results, k),
            c["out"].as_bool().unwrap(),
            "check_answer_in_results({}, k={k})",
            c["expected"]
        );
    }
    // a result without `.content` is matched on str(node)
    assert_eq!(check_answer_in_results("42", &[("42".into(), 1.0)], 1), f["no_content"].as_bool().unwrap());
}

#[tokio::test]
async fn regression_suite_and_reports_match_python() {
    let f = fixture();
    let qs = single_hop_test_questions();
    // the generator's synthetic retriever: every 7th question errors, the hit sits at a rotating rank
    let answers = qs.clone();
    set_fixed_perf_counter(Some(0.0));
    let res = run_regression_suite(&qs, |question| {
        let i = answers.iter().position(|q| q.question == question).unwrap();
        let expected = answers[i].expected_answer.clone();
        async move {
            if i % 7 == 6 {
                return Err(format!("backend down for {i}"));
            }
            let hit = (format!("answer: {expected}"), 1.0);
            let filler: Vec<(String, f64)> = (0..12).map(|j| (format!("filler {j}"), 0.5)).collect();
            let pos = [0, 1, 4, 7, 11, 20][i % 6].min(filler.len());
            let mut out: Vec<(String, f64)> = filler[..pos].to_vec();
            out.push(hit);
            out.extend_from_slice(&filler[pos..]);
            out.truncate(12);
            Ok(out)
        }
    })
    .await;
    set_fixed_perf_counter(None);
    let mut d = res.to_dict();
    let m = d.as_object_mut().unwrap();
    m.shift_remove("avg_latency_ms");
    m.shift_remove("p95_latency_ms");
    m.insert("failed_questions".into(), Value::from(res.failed_questions.clone()));
    assert_json_eq(&d, &f["suite"], "run_regression_suite");

    let lat = RegressionResult {
        total_questions: 5,
        latencies_ms: vec![10.0, 20.0, 30.0, 40.0, 100.0],
        ..Default::default()
    };
    assert_json_eq(&lat.to_dict(), &f["latency"], "latency to_dict");
    let lat2 = RegressionResult {
        total_questions: 3,
        correct_at_1: 2,
        latencies_ms: vec![0.1, 0.2, 0.7, 5.5, 3.25, 1e-3, 2.0],
        ..Default::default()
    };
    assert_json_eq(&lat2.to_dict(), &f["latency2"], "latency2 to_dict");
    let baseline = f["baseline"].as_f64().unwrap();
    assert_eq!(regression_results_json(&lat2, baseline), s(&f["saved"]), "save_regression_results");
    if std::env::var("NEURAL_GRAPH_BASELINE_P1").is_err() {
        let dir = tempfile::tempdir().unwrap();
        save_regression_results(&lat2, &dir.path().join("r.json")).unwrap();
        assert_eq!(std::fs::read_to_string(dir.path().join("r.json")).unwrap(), s(&f["saved"]));
        assert_eq!(baseline_precision_1().unwrap(), baseline);
    }
    for c in f["compare"].as_array().unwrap() {
        let (correct, total) = (c["p1"][0].as_u64().unwrap() as usize, c["p1"][1].as_u64().unwrap() as usize);
        let r = RegressionResult { total_questions: total, correct_at_1: correct, ..Default::default() };
        let (passed, msg) = compare_to_baseline(&r, baseline);
        assert_eq!(json!([passed, msg]), c["out"], "compare_to_baseline({correct}/{total})");
    }
    assert_eq!(standalone_report(baseline), s(&f["standalone"]), "__main__ output");
}
