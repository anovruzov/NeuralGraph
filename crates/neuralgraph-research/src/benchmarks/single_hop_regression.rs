//! Port of `NeuralGraph/tests/test_single_hop_regression.py`: the single-hop regression suite's question set and
//! metric helpers (its pytest classes are `tests/single_hop_regression.rs`).
//!
//! THE PLAN: "Create an automated single-hop regression suite (targeting locomo10 plus 10–20 new high-precision
//! questions) and run it in CI; fail if precision@1 drops >1% from baseline." The module holds a fixed set of
//! questions with known answers and the precision@k / latency bookkeeping; nothing in the repository ever ran it
//! against a real retriever (the integration test was a skipped stub), and the port keeps it that way.
//!
//! `NEURAL_GRAPH_BASELINE_P1` sets the baseline precision@1 target (default 0.65).

use super::pyharness::{json_indent, parse_py_float, HResult, HarnessError};
use neuralgraph_memory::pycompat::{dumps, fnum, obj};
use serde_json::Value;
use std::future::Future;
use std::path::Path;

/// Fail if precision@1 drops more than 1%.
pub const REGRESSION_THRESHOLD: f64 = 0.01;
/// Need at least this many questions.
pub const MIN_SAMPLES_FOR_REGRESSION: usize = 10;

/// `float(os.environ.get("NEURAL_GRAPH_BASELINE_P1", "0.65"))` (a malformed value was a `ValueError` at import).
pub fn baseline_precision_1() -> HResult<f64> {
    let raw = std::env::var("NEURAL_GRAPH_BASELINE_P1").unwrap_or_else(|_| "0.65".into());
    parse_py_float(&raw).map_err(|_| {
        HarnessError::new(
            "ValueError",
            format!("could not convert string to float: {}", neuralgraph_memory::pycompat::py_repr_str(&raw)),
        )
    })
}

/// A single-hop test question with expected answer.
#[derive(Clone, Debug, PartialEq)]
pub struct SingleHopQuestion {
    pub question: String,
    pub expected_answer: String,
    /// Entity the answer should be about
    pub expected_entity: String,
    /// Type of single-hop question
    pub category: String,
    pub difficulty: String,
}

impl SingleHopQuestion {
    pub fn new(question: &str, expected_answer: &str, expected_entity: &str, category: &str) -> Self {
        SingleHopQuestion {
            question: question.into(),
            expected_answer: expected_answer.into(),
            expected_entity: expected_entity.into(),
            category: category.into(),
            difficulty: "medium".into(),
        }
    }
}

/// Result of a single regression test run.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct RegressionResult {
    pub total_questions: usize,
    pub correct_at_1: usize,
    pub correct_at_5: usize,
    pub correct_at_10: usize,
    pub failed_questions: Vec<Value>,
    pub latencies_ms: Vec<f64>,
}

impl RegressionResult {
    fn ratio(&self, correct: usize) -> f64 {
        if self.total_questions != 0 {
            correct as f64 / self.total_questions as f64
        } else {
            0.0
        }
    }

    pub fn precision_at_1(&self) -> f64 {
        self.ratio(self.correct_at_1)
    }

    pub fn precision_at_5(&self) -> f64 {
        self.ratio(self.correct_at_5)
    }

    pub fn precision_at_10(&self) -> f64 {
        self.ratio(self.correct_at_10)
    }

    pub fn avg_latency_ms(&self) -> f64 {
        if self.latencies_ms.is_empty() {
            return 0.0;
        }
        let mut sum = 0.0;
        for x in &self.latencies_ms {
            sum += x;
        }
        sum / self.latencies_ms.len() as f64
    }

    /// The value at index `int(n * 0.95)` of the sorted latencies (clamped to the last).
    pub fn p95_latency_ms(&self) -> f64 {
        if self.latencies_ms.is_empty() {
            return 0.0;
        }
        let mut sorted = self.latencies_ms.clone();
        sorted.sort_by(|a, b| a.total_cmp(b));
        let idx = (sorted.len() as f64 * 0.95) as usize;
        sorted[idx.min(sorted.len() - 1)]
    }

    pub fn to_dict(&self) -> Value {
        obj([
            ("total_questions", Value::from(self.total_questions)),
            ("correct_at_1", Value::from(self.correct_at_1)),
            ("correct_at_5", Value::from(self.correct_at_5)),
            ("correct_at_10", Value::from(self.correct_at_10)),
            ("precision_at_1", fnum(self.precision_at_1())),
            ("precision_at_5", fnum(self.precision_at_5())),
            ("precision_at_10", fnum(self.precision_at_10())),
            ("avg_latency_ms", fnum(self.avg_latency_ms())),
            ("p95_latency_ms", fnum(self.p95_latency_ms())),
            ("failed_count", Value::from(self.failed_questions.len())),
        ])
    }
}

/// High-precision single-hop test questions (THE PLAN: "10-20 new high-precision questions").
pub fn single_hop_test_questions() -> Vec<SingleHopQuestion> {
    let q = SingleHopQuestion::new;
    vec![
        // Entity attribute questions ("What is X's Y?")
        q("What is Caroline's job?", "teacher", "Caroline", "entity_attribute"),
        q("What is Michael's profession?", "engineer", "Michael", "entity_attribute"),
        q("Where does Sarah live?", "New York", "Sarah", "entity_attribute"),
        q("What is David's hobby?", "photography", "David", "entity_attribute"),
        q("What is Emily's favorite color?", "blue", "Emily", "entity_attribute"),
        // Speaker-attributed facts
        q("What does John do for work?", "software developer", "John", "speaker_fact"),
        q("What language does Maria speak?", "Spanish", "Maria", "speaker_fact"),
        // Possessive questions
        q("What is Lisa's pet's name?", "Max", "Lisa", "possessive"),
        q("What is Tom's car brand?", "Toyota", "Tom", "possessive"),
        // Factual recall
        q("How old is Peter?", "35", "Peter", "factual"),
        q("When did Anna graduate?", "2019", "Anna", "factual"),
        // Location questions
        q("Where was Mark born?", "Chicago", "Mark", "location"),
        q("Where does Jennifer work?", "hospital", "Jennifer", "location"),
        // Relationship questions
        q("Who is Daniel's sister?", "Emma", "Daniel", "relationship"),
        q("Who is Rachel's boss?", "Steve", "Rachel", "relationship"),
        // Action questions
        q("What did Kevin study?", "computer science", "Kevin", "action"),
        q("What instrument does Sophie play?", "piano", "Sophie", "action"),
        // Binary/yes-no convertible
        q("Is Robert married?", "yes", "Robert", "binary"),
        q("Does Linda have children?", "two", "Linda", "binary"),
        // Temporal single-hop
        q("When did Chris move to London?", "2020", "Chris", "temporal_single"),
    ]
}

/// Check if the expected answer appears in the top-k results. Each result is `(content, score)`, where `content`
/// is the node's `content` (or `str(node)` for objects without one, as the Python fell back to).
pub fn check_answer_in_results(expected: &str, results: &[(String, f64)], top_k: usize) -> bool {
    let expected_lower = neuralgraph_memory::pycompat::py_strip(&expected.to_lowercase()).to_string();
    // Also check for partial matches
    let expected_parts: Vec<String> = expected_lower
        .replace(',', "|")
        .split('|')
        .map(|p| neuralgraph_memory::pycompat::py_strip(p).to_string())
        .filter(|p| p.chars().count() > 2)
        .collect();
    for (content, _score) in results.iter().take(top_k) {
        let content = content.to_lowercase();
        if content.contains(&expected_lower) {
            return true;
        }
        if expected_parts.iter().any(|part| content.contains(part.as_str())) {
            return true;
        }
    }
    false
}

/// Run the suite: `retrieval_fn(question)` returns `(content, score)` results or an error message; latencies come
/// from `perf_counter` around each call.
pub async fn run_regression_suite<F, Fut>(questions: &[SingleHopQuestion], mut retrieval_fn: F) -> RegressionResult
where
    F: FnMut(String) -> Fut,
    Fut: Future<Output = Result<Vec<(String, f64)>, String>>,
{
    let mut result = RegressionResult { total_questions: questions.len(), ..Default::default() };
    for q in questions {
        let start = super::pyharness::perf_counter();
        match retrieval_fn(q.question.clone()).await {
            Ok(results) => {
                let latency = (super::pyharness::perf_counter() - start) * 1000.0;
                result.latencies_ms.push(latency);
                if check_answer_in_results(&q.expected_answer, &results, 1) {
                    result.correct_at_1 += 1;
                    result.correct_at_5 += 1;
                    result.correct_at_10 += 1;
                } else if check_answer_in_results(&q.expected_answer, &results, 5) {
                    result.correct_at_5 += 1;
                    result.correct_at_10 += 1;
                } else if check_answer_in_results(&q.expected_answer, &results, 10) {
                    result.correct_at_10 += 1;
                } else {
                    result.failed_questions.push(obj([
                        ("question", Value::String(q.question.clone())),
                        ("expected", Value::String(q.expected_answer.clone())),
                        ("entity", Value::String(q.expected_entity.clone())),
                        ("category", Value::String(q.category.clone())),
                    ]));
                }
            }
            Err(e) => {
                result.failed_questions.push(obj([
                    ("question", Value::String(q.question.clone())),
                    ("expected", Value::String(q.expected_answer.clone())),
                    ("error", Value::String(e)),
                ]));
            }
        }
    }
    result
}

/// The JSON `save_regression_results` writes.
pub fn regression_results_json(result: &RegressionResult, baseline_p1: f64) -> String {
    let mut data = result.to_dict();
    let m = data.as_object_mut().expect("object");
    m.insert("failed_questions".into(), Value::Array(result.failed_questions.clone()));
    m.insert("baseline_p1".into(), fnum(baseline_p1));
    m.insert("threshold".into(), fnum(REGRESSION_THRESHOLD));
    dumps(&data, &json_indent(2))
}

/// Save regression results to JSON for tracking.
pub fn save_regression_results(result: &RegressionResult, path: &Path) -> HResult<()> {
    std::fs::write(path, regression_results_json(result, baseline_precision_1()?))?;
    Ok(())
}

/// `format(x, ".2%")`.
pub fn pct2(x: f64) -> String {
    format!("{:.2}%", x * 100.0)
}

/// Compare a result to the baseline: `(passed, message)`.
pub fn compare_to_baseline(result: &RegressionResult, baseline_p1: f64) -> (bool, String) {
    let threshold = baseline_p1 - REGRESSION_THRESHOLD;
    let p1 = result.precision_at_1();
    if p1 >= threshold {
        (
            true,
            format!(
                "PASSED: precision@1 = {} >= baseline {} (target: {})",
                pct2(p1),
                pct2(threshold),
                pct2(baseline_p1)
            ),
        )
    } else {
        (
            false,
            format!(
                "FAILED: precision@1 = {} < baseline {} (dropped {} below threshold)",
                pct2(p1),
                pct2(threshold),
                pct2(threshold - p1)
            ),
        )
    }
}

/// What running the module as a script printed (`python test_single_hop_regression.py`).
pub fn standalone_report(baseline_p1: f64) -> String {
    let questions = single_hop_test_questions();
    let mut out = String::new();
    out.push_str("Single-Hop Regression Test Suite\n");
    out.push_str("================================\n");
    out.push_str(&format!("Baseline precision@1: {}\n", pct2(baseline_p1)));
    out.push_str(&format!("Regression threshold: {}\n", pct2(REGRESSION_THRESHOLD)));
    out.push_str(&format!("Number of test questions: {}\n", questions.len()));
    out.push('\n');
    let mut categories: indexmap::IndexMap<String, usize> = indexmap::IndexMap::new();
    for q in &questions {
        *categories.entry(q.category.clone()).or_insert(0) += 1;
    }
    let mut cats: Vec<(&String, &usize)> = categories.iter().collect();
    cats.sort();
    out.push_str("Questions by category:\n");
    for (cat, count) in cats {
        out.push_str(&format!("  {cat}: {count}\n"));
    }
    out
}
