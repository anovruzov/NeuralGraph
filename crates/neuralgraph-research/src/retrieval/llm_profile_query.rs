//! Answering a question from one speaker profile with the LLM (port of `retrieval/llm_profile_query.py`).
//!
//! The result is the Python dict (`found`, `answer`, `confidence`, `reasoning`) as a JSON object, since
//! `reasoning` is whatever JSON value the model returned.

use neuralgraph_memory::pycompat::{py_len, py_prefix, py_strip, py_strip_chars, py_truthy};
use serde_json::{json, Map, Value};

use super::llm_profile_extractor::strip_code_fence;
use super::pytext::{py_json_loads, py_str_value, py_type_name};
use crate::llm_backend::{llm_base_url, llm_model, GenerateOptions, Llm};

/// `OLLAMA_BASE_URL` (= `llm_backend.LLM_BASE_URL`).
pub fn ollama_base_url() -> String {
    llm_base_url()
}

/// `OLLAMA_MODEL` (= `llm_backend.LLM_MODEL`).
pub fn ollama_model() -> String {
    llm_model()
}

/// Items of a profile list field (`profile_dict.get(key, [])`); a string iterates by character as in Python.
fn profile_items(profile: &Map<String, Value>, key: &str) -> Vec<Value> {
    match profile.get(key) {
        Some(Value::Array(items)) => items.clone(),
        Some(Value::String(s)) => s.chars().map(|c| Value::String(c.to_string())).collect(),
        _ => Vec::new(),
    }
}

/// The query prompt: up to 100 known facts and the last 20 messages (each cut to 200 code points + "...").
pub fn profile_query_prompt(speaker_name: &str, profile_dict: &Map<String, Value>, question: &str) -> String {
    let mut profile_summary = format!("SPEAKER: {speaker_name}\n\n");

    let extracted_facts = profile_items(profile_dict, "extracted_facts");
    if !extracted_facts.is_empty() {
        profile_summary.push_str("KNOWN FACTS:\n");
        for fact in extracted_facts.iter().take(100) {
            profile_summary.push_str(&format!("  - {}\n", py_str_value(fact)));
        }
        profile_summary.push('\n');
    }

    let all_messages = profile_items(profile_dict, "all_messages");
    if !all_messages.is_empty() {
        profile_summary.push_str("RECENT MESSAGES:\n");
        let recent = if all_messages.len() > 20 { &all_messages[all_messages.len() - 20..] } else { &all_messages[..] };
        for (i, msg) in recent.iter().enumerate() {
            let msg = py_str_value(msg);
            let preview = if py_len(&msg) > 200 { format!("{}...", py_prefix(&msg, 200)) } else { msg };
            profile_summary.push_str(&format!("{}. {preview}\n", i + 1));
        }
    }

    format!(
        "{profile_summary}

QUESTION: {question}

Analyze the profile above and answer the question.

UNIVERSAL PRINCIPLES:
1. Read the question carefully - understand EXACTLY what it's asking for.
2. Filter the profile to match the question's specificity:
   - Specific question (singular)? → Return ONE answer
   - General question (plural/aggregate)? → Return ALL relevant items
   - Narrow category question? → Return ONLY items in that category
3. NEVER dump entire lists - extract ONLY what matches the question.
4. If information is NOT in profile: \"NOT_FOUND\"
5. Minimal precision: fewer, accurate items > many, irrelevant items

Return ONLY valid JSON:
{{
  \"answer\": \"precise answer matching question scope\" OR \"NOT_FOUND\",
  \"reasoning\": \"how you filtered the profile\"
}}"
    )
}

fn outcome(found: bool, answer: Option<String>, confidence: f64, reasoning: Value) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("found".into(), Value::Bool(found));
    m.insert("answer".into(), json!(answer));
    m.insert("confidence".into(), json!(confidence));
    m.insert("reasoning".into(), reasoning);
    m
}

/// The reply parsing of `query_profile_with_llm`.
pub fn parse_profile_answer(response_text: &str, gold_answer: Option<&Value>) -> Map<String, Value> {
    let text = strip_code_fence(response_text);
    let Some(result) = py_json_loads(&text) else {
        return outcome(false, None, 0.0, json!("Parse error"));
    };
    let Value::Object(result) = result else {
        // result.get(...) on a non-dict raised AttributeError, caught by the outer handler.
        let msg = format!("Error: '{}' object has no attribute 'get'", py_type_name(&result));
        return outcome(false, None, 0.0, json!(msg));
    };
    let answer = match result.get("answer") {
        None => String::new(),
        Some(Value::Array(items)) => items.iter().map(py_str_value).collect::<Vec<_>>().join(", "),
        Some(other) => py_strip(&py_str_value(other)).to_string(),
    };
    let reasoning = result.get("reasoning").cloned().unwrap_or_else(|| json!(""));
    if answer == "NOT_FOUND" || answer.is_empty() {
        return outcome(false, None, 0.0, reasoning);
    }
    let mut confidence = 1.0;
    if let Some(gold) = gold_answer.filter(|g| py_truthy(g)) {
        confidence = calculate_answer_confidence(&answer, gold);
    }
    outcome(true, Some(answer), confidence, reasoning)
}

/// `query_profile_with_llm(session, speaker_name, profile_dict, question, gold_answer=None)`.
pub async fn query_profile_with_llm(
    llm: &dyn Llm,
    speaker_name: &str,
    profile_dict: &Map<String, Value>,
    question: &str,
    gold_answer: Option<&Value>,
) -> Map<String, Value> {
    let prompt = profile_query_prompt(speaker_name, profile_dict, question);
    let opts = GenerateOptions::default()
        .model(ollama_model())
        .base_url(ollama_base_url())
        .temperature(0.1)
        .max_tokens(300)
        .timeout(40.0);
    match llm.generate(&prompt, &opts).await {
        Ok(text) => parse_profile_answer(&text, gold_answer),
        Err(_) => outcome(false, None, 0.0, json!("API error")),
    }
}

/// `_calculate_answer_confidence(answer, gold)`: share of the gold answer's parts (split on `,` and ` and `,
/// longer than 2 characters) found in the answer.
pub fn calculate_answer_confidence(answer: &str, gold: &Value) -> f64 {
    let answer_lower = answer.to_lowercase();
    let gold_lower = py_str_value(gold).to_lowercase();
    let replaced = gold_lower.replace(',', "|").replace(" and ", "|");
    let gold_parts: Vec<String> = replaced
        .split('|')
        .map(py_strip)
        .filter(|p| py_len(p) > 2)
        .map(|p| py_strip_chars(p, "\"").to_string())
        .collect();
    if gold_parts.is_empty() {
        return 0.0;
    }
    let found_count = gold_parts.iter().filter(|p| answer_lower.contains(p.as_str())).count();
    found_count as f64 / gold_parts.len() as f64
}

/// The module's `__main__` demo (three queries against a sample profile); returns the lines it printed.
pub async fn run_example(llm: &dyn Llm) -> Vec<String> {
    let profile = json!({
        "name": "Caroline",
        "extracted_facts": [
            "moved from Sweden 4 years ago", "practices pottery", "does painting", "mentors youth",
            "attended pride parade", "attended LGBTQ conference", "pursuing counseling for transgender people"
        ],
        "all_messages": [
            "I moved from Sweden 4 years ago and it was hard at first.",
            "I love pottery! Been doing it for years.",
            "Went to a pride parade yesterday - it was amazing!",
            "I'm pursuing counseling as a career, specifically for transgender people."
        ]
    });
    let profile = profile.as_object().expect("object").clone();
    let show = |v: Option<&Value>| v.map(py_str_value).unwrap_or_else(|| "None".into());
    let mut out = Vec::new();

    out.push("Test 1: Specific question".to_string());
    let gold = json!("Sweden");
    let r = query_profile_with_llm(llm, "Caroline", &profile, "Where did Caroline move from 4 years ago?", Some(&gold))
        .await;
    out.push(format!("  Answer: {}", show(r.get("answer"))));
    out.push(format!("  Confidence: {}", show(r.get("confidence"))));
    out.push(format!("  Reasoning: {}\n", show(r.get("reasoning"))));

    out.push("Test 2: Aggregation question".to_string());
    let gold = json!("pottery, painting, mentoring");
    let r = query_profile_with_llm(llm, "Caroline", &profile, "What activities does Caroline do?", Some(&gold)).await;
    out.push(format!("  Answer: {}", show(r.get("answer"))));
    out.push(format!("  Confidence: {}", show(r.get("confidence"))));
    out.push(format!("  Reasoning: {}\n", show(r.get("reasoning"))));

    out.push("Test 3: Not in profile".to_string());
    let r = query_profile_with_llm(llm, "Caroline", &profile, "What is Caroline's favorite color?", None).await;
    out.push(format!("  Found: {}", show(r.get("found"))));
    out.push(format!("  Answer: {}", show(r.get("answer"))));
    out.push(format!("  Reasoning: {}", show(r.get("reasoning"))));
    for line in &out {
        println!("{line}");
    }
    out
}
