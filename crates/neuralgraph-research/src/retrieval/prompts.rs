//! Schema-guided answer prompts, query rewriting and prompt selection (port of `retrieval/prompts.py`).
//!
//! Every prompt constant is byte-identical to the Python string (pinned by the fixture). Templates are filled
//! with [`py_format`], which substitutes `{context}`/`{question}`/`{num_rewrites}` in one pass like
//! `str.format`.
//!
//! `generate_query_rewrites` posted to an Ollama `/api/generate` endpoint directly (no `llm_backend`): with an
//! HTTP-backed [`Llm`] the same request is sent; a scripted model (no HTTP client) is asked through
//! [`Llm::generate`] with the same parameters, its reply standing for the `response` field.

use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{py_len, py_split_whitespace, py_strip, py_strip_chars, py_truthy};
use serde_json::{json, Value};
use std::sync::LazyLock;

use super::data_types::NodeRef;
use super::pytext::{
    aiohttp_accepts_json, py_format, py_json_loads, py_max, py_prefix_len, py_regex, py_search, py_str_value,
};
use crate::llm_backend::{GenerateOptions, Llm};

// =============================================================================
// RETRIEVAL ANSWER PROMPT - Schema-Guided Reasoning
// =============================================================================

pub const RETRIEVAL_ANSWER_PROMPT: &str =
    "You are a memory retrieval system. Your task is to find and synthesize information from memories.

REASONING PROCESS (do this mentally, don't write it out):
1. WHO is the question about? Identify the person/entity.
2. WHAT is being asked? Identify the topic/activity/relationship.
3. SCAN all memories for this person + topic combination.
4. CONNECT related memories - they may describe the same thing differently.
5. SYNTHESIZE a complete answer from ALL relevant memories.

CRITICAL EVIDENCE REQUIREMENTS:
- Every factual claim in your answer MUST be supported by specific text in the memories
- If a fact is NOT explicitly stated in the memories, do NOT include it
- If the memories don't contain enough information, say \"Based on available memories, I cannot determine...\"
- DO NOT infer or guess information not directly stated

CRITICAL FOR MULTI-HOP QUESTIONS:
- If asking \"how does X do Y\", look for ALL instances of X doing Y across memories
- If asking about preferences/habits, combine multiple examples into one answer
- Different memories may describe the SAME activity - recognize these patterns

Memories:
{context}

Question: {question}

Answer (only include facts directly supported by the memories above):";

// =============================================================================
// RETRIEVAL CONTEXT FORMATTER
// =============================================================================

/// `format_retrieval_context(nodes, max_chars=15000, include_timestamps=True, include_speaker=True)`: one line
/// per node (`[date=...] [timestamp=...] speaker: content`), stopping before the first line that would exceed
/// `max_chars` code points in total. Scores are ignored.
pub fn format_retrieval_context(
    nodes: &[(NodeRef, f64)],
    max_chars: i64,
    include_timestamps: bool,
    include_speaker: bool,
) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut total_chars: i64 = 0;
    for (node, _score) in nodes {
        let n = node.borrow();
        let mut parts: Vec<String> = Vec::new();
        // `node.created_at` is always set (a datetime is truthy).
        if include_timestamps {
            let resolved_date = n.metadata.get("resolved_date").filter(|v| py_truthy(v));
            if let Some(date) = resolved_date {
                parts.push(format!("[date={}]", py_str_value(date)));
            }
            parts.push(format!("[timestamp={}]", n.created_at.naive.format("%Y-%m-%d %H:%M")));
        }
        if include_speaker {
            // metadata.get("producer_id", metadata.get("speaker", ""))
            let speaker = match n.metadata.get("producer_id") {
                Some(v) => Some(v),
                None => n.metadata.get("speaker"),
            };
            if let Some(speaker) = speaker.filter(|v| py_truthy(v)) {
                parts.push(format!("{}:", py_str_value(speaker)));
            }
        }
        parts.push(n.content.clone());
        let line = parts.join(" ");
        let len = py_len(&line) as i64;
        if total_chars + len > max_chars {
            break;
        }
        lines.push(line);
        total_chars += len;
    }
    lines.join("\n")
}

// =============================================================================
// SPECIALIZED PROMPTS FOR DIFFERENT QUERY TYPES
// =============================================================================

pub const SINGLE_HOP_ANSWER_PROMPT: &str = "You are a memory retrieval system answering a simple factual question.

STRICT EVIDENCE RULES:
1. Your answer MUST come directly from the memories provided
2. DO NOT make inferences, assumptions, or fill in missing information
3. If the specific answer is NOT in the memories, respond: \"Not found in memories\"
4. Quote or paraphrase specific text from memories to support your answer

REASONING PROCESS:
1. Find the entity being asked about
2. Find the specific attribute/fact being requested
3. Locate EXACTLY where in the memories this fact is stated
4. Return ONLY what is explicitly stated

Memories:
{context}

Question: {question}

Answer (state only what is explicitly in the memories, nothing more):";

pub const LIST_QUESTION_ANSWER_PROMPT: &str = "Extract ALL items from the memories that answer this question.

RULES:
1. Output ONLY the items - comma-separated, no explanations.
2. Find ALL distinct items across ALL memories.
3. Do NOT generalize (if memories say \"bowls\" and \"cups\", say \"bowls, cups\" not \"pottery\").

EXAMPLES:
Q: What has Tom painted? → sunset, horse, landscape
Q: What activities does Jane do? → pottery, camping, swimming

Memories:
{context}

Question: {question}

Answer:";

pub const AGGREGATION_ANSWER_PROMPT: &str = "Find what is shared between the entities in this question.

RULES:
1. Output ONLY shared items - comma-separated, no explanations.
2. Only include facts stated about ALL entities asked about.
3. If nothing is shared, output: Not found

EXAMPLE:
Q: What do John and Mary both like? → hiking, coffee

Memories:
{context}

Question: {question}

Answer:";

pub const TEMPORAL_QUERY_PROMPT: &str = "You are a memory retrieval system specialized in temporal queries.

For questions about WHEN something happened:
1. Use metadata fields when available (resolved_date, timestamp) to answer
2. Look for explicit dates, months, years in the memories
3. Convert relative references (e.g., \"last year\" in a May 2023 memory = 2022)
4. Prefer resolved_date over text if both are present
5. If multiple time references exist, identify which one answers the specific question

Memories:
{context}

Question: {question}

Answer (provide the specific date/time):";

pub const MULTI_HOP_QUERY_PROMPT: &str = "You are a memory retrieval system specialized in multi-hop reasoning.

This question requires connecting information across multiple memories.

REASONING STEPS:
1. Identify ALL entities mentioned in the question
2. Find memories about each entity separately
3. Find connections between those entities
4. Synthesize information from multiple memories into one answer

KEY: The answer is NOT in any single memory - you must COMBINE information.

Memories:
{context}

Question: {question}

Answer (synthesize from multiple memories):";

pub const OPEN_DOMAIN_QUERY_PROMPT: &str = "You are answering an open-ended inference question.

First decide the question type:
- If it is about the people or events in the conversation, infer ONLY from the memories.
- If it is general knowledge (definitions, how things work, public facts), ignore memories and answer from your own knowledge.

REASONING APPROACH:
1. For conversation-based questions, gather the best clues in the memories.
2. Look for patterns, recurring themes, stated preferences.
3. Make a reasonable inference; do not copy timestamps or metadata.
4. Match the question type:
   - Yes/No questions: answer \"Yes\" or \"No\" plus a short reason.
   - A-or-B questions: choose one option and give a short reason.
   - Otherwise: give a concise phrase or 1 sentence.
5. Do NOT answer with a date unless the question explicitly asks \"when/what date\".

Memories:
{context}

Question: {question}

Answer:";

// =============================================================================
// QUERY REWRITING PROMPTS
// =============================================================================

pub const QUERY_REWRITE_PROMPT: &str = "You are a query rewriting assistant. Rewrite the question in {num_rewrites} alternative ways to improve memory retrieval.

REWRITING RULES:
1. Keep the core meaning EXACTLY the same
2. Use different phrasings (synonyms, restructure)
3. For single-hop questions: focus on the entity and attribute
4. Make questions more specific when possible
5. Include the main entity name in each rewrite

Original question: {question}

Return ONLY a JSON array of rewrites:
[\"rewrite 1\", \"rewrite 2\", ...]

JSON array:";

pub const SINGLE_HOP_REWRITE_PROMPT: &str =
    "Rewrite this single-hop factual question in {num_rewrites} alternative ways.

SINGLE-HOP FOCUS:
- Keep the target entity explicit
- Vary the phrasing of the attribute/property being asked
- Include possessive forms and alternative constructions
- Example: \"What is John's job?\" → [\"What does John do for work?\", \"What profession does John have?\"]

Original: {question}

Return only a JSON array:";

pub const TEMPORAL_REWRITE_PROMPT: &str = "Rewrite this temporal question in {num_rewrites} alternative ways.

TEMPORAL FOCUS:
- Keep date/time references intact
- Vary how the temporal aspect is asked
- Include explicit and relative time phrasings
- Example: \"When did Mary visit Paris?\" → [\"What date did Mary go to Paris?\", \"In what month did Mary travel to Paris?\"]

Original: {question}

Return only a JSON array:";

// =============================================================================
// QUERY REWRITING FUNCTIONS
// =============================================================================

/// `get_rewrite_prompt(query, num_rewrites=2)` → (filled prompt, "temporal" | "single_hop" | "general").
pub fn get_rewrite_prompt(query: &str, num_rewrites: i64) -> (String, &'static str) {
    let query_lower = query.to_lowercase();
    let n = num_rewrites.to_string();
    let fields = [("question", query), ("num_rewrites", n.as_str())];

    let temporal_words = ["when", "what time", "what date", "which year", "which month", "how long ago"];
    if temporal_words.iter().any(|w| query_lower.contains(w)) {
        return (py_format(TEMPORAL_REWRITE_PROMPT, &fields), "temporal");
    }
    let single_hop_patterns = [
        "what is",
        "what's",
        "what does",
        "what did",
        "who is",
        "who's",
        "who does",
        "where is",
        "where does",
        "where did",
        "how does",
        "how did",
        "'s ",
    ];
    if single_hop_patterns.iter().any(|p| query_lower.contains(p)) {
        return (py_format(SINGLE_HOP_REWRITE_PROMPT, &fields), "single_hop");
    }
    (py_format(QUERY_REWRITE_PROMPT, &fields), "general")
}

/// The Ollama request `generate_query_rewrites` sent, returning `result["response"]` (or why it failed).
async fn post_ollama_generate(
    client: &reqwest::Client,
    base_url: &str,
    model: &str,
    prompt: &str,
    timeout_seconds: f64,
) -> Result<Value, String> {
    let body = json!({
        "model": model,
        "prompt": prompt,
        "stream": false,
        "options": {"temperature": 0.3, "num_predict": 200},
    });
    let resp = client
        .post(format!("{base_url}/api/generate"))
        .json(&body)
        .timeout(std::time::Duration::from_secs_f64(timeout_seconds))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    // No raise_for_status in Python: any status with a JSON body is read.
    let ctype =
        resp.headers().get(reqwest::header::CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    if !aiohttp_accepts_json(&ctype) {
        return Err(format!("Attempt to decode JSON with unexpected mimetype: {ctype}"));
    }
    let text = String::from_utf8_lossy(&bytes);
    if py_strip(&text).is_empty() {
        return Ok(Value::Null);
    }
    py_json_loads(&text).ok_or_else(|| "invalid JSON body".to_string())
}

/// `generate_query_rewrites(query, llm_client, base_url, model, num_rewrites=2, timeout_seconds=5.0)`: up to
/// `num_rewrites` alternative phrasings (empty on any failure).
pub async fn generate_query_rewrites(
    query: &str,
    llm: &dyn Llm,
    base_url: &str,
    model: &str,
    num_rewrites: i64,
    timeout_seconds: f64,
) -> Vec<String> {
    let (prompt, _prompt_type) = get_rewrite_prompt(query, num_rewrites);
    let response = match llm.http() {
        Some(client) => match post_ollama_generate(client, base_url, model, &prompt, timeout_seconds).await {
            // result.get("response", "").strip(): a non-dict result or non-str response raised.
            Ok(Value::Object(result)) => match result.get("response") {
                None => Ok(String::new()),
                Some(Value::String(s)) => Ok(s.clone()),
                Some(_) => Err(()),
            },
            _ => Err(()),
        },
        None => {
            let opts = GenerateOptions::default()
                .model(model)
                .base_url(base_url)
                .temperature(0.3)
                .max_tokens(200)
                .timeout(timeout_seconds);
            llm.generate(&prompt, &opts).await.map_err(|_| ())
        }
    };
    let Ok(response) = response else {
        tracing::debug!("Query rewrite failed");
        return Vec::new();
    };
    parse_rewrites(py_strip(&response), num_rewrites)
}

/// The response parsing of `generate_query_rewrites`: the outermost `[...]` as a JSON array of strings longer
/// than 5 characters, else one rewrite per non-empty line not starting with `[`.
pub fn parse_rewrites(resp: &str, num_rewrites: i64) -> Vec<String> {
    let start = resp.find('[');
    let end = resp.rfind(']').map(|i| i + 1);
    if let (Some(start), Some(end)) = (start, end) {
        if end > start {
            if let Some(Value::Array(rewrites)) = py_json_loads(&resp[start..end]) {
                let valid: Vec<String> =
                    rewrites.iter().filter_map(|r| r.as_str()).filter(|r| py_len(r) > 5).map(str::to_string).collect();
                let n = py_prefix_len(valid.len(), num_rewrites);
                return valid[..n].to_vec();
            }
        }
    }
    let lines: Vec<String> = resp
        .split('\n')
        .filter(|line| {
            let stripped = py_strip(line);
            !stripped.is_empty() && !stripped.starts_with('[')
        })
        .map(|line| py_strip_chars(py_strip_chars(py_strip_chars(py_strip(line), "\""), "'"), ",").to_string())
        .collect();
    let n = py_prefix_len(lines.len(), num_rewrites);
    lines[..n].to_vec()
}

/// `deduplicate_rewrites(original, rewrites, similarity_threshold=0.85)`: drop rewrites whose word-set Jaccard
/// overlap with the original or an accepted rewrite reaches the threshold.
pub fn deduplicate_rewrites(original: &str, rewrites: &[String], similarity_threshold: f64) -> Vec<String> {
    if rewrites.is_empty() {
        return Vec::new();
    }
    fn word_overlap(s1: &str, s2: &str) -> f64 {
        let l1 = s1.to_lowercase();
        let l2 = s2.to_lowercase();
        let words1: IndexSet<&str> = py_split_whitespace(&l1).into_iter().collect();
        let words2: IndexSet<&str> = py_split_whitespace(&l2).into_iter().collect();
        if words1.is_empty() || words2.is_empty() {
            return 0.0;
        }
        let intersection = words1.intersection(&words2).count();
        let union = words1.union(&words2).count();
        if union > 0 {
            intersection as f64 / union as f64
        } else {
            0.0
        }
    }
    let mut filtered: Vec<String> = Vec::new();
    for rewrite in rewrites {
        if word_overlap(original, rewrite) < similarity_threshold {
            let too_similar = filtered.iter().any(|accepted| word_overlap(rewrite, accepted) >= similarity_threshold);
            if !too_similar {
                filtered.push(rewrite.clone());
            }
        }
    }
    filtered
}

/// `fuse_rewrite_results(results_by_query, original_query, limit=10, duplicate_penalty=0.7)`: best score per
/// node, the original query's results first; a node seen again gets `max(old, score * 1.1)` the first time and
/// `max(old, score * duplicate_penalty)` afterwards.
pub fn fuse_rewrite_results(
    results_by_query: &IndexMap<String, Vec<(NodeRef, f64)>>,
    original_query: &str,
    limit: i64,
    duplicate_penalty: f64,
) -> Vec<(NodeRef, f64)> {
    let mut node_scores: IndexMap<String, (f64, NodeRef)> = IndexMap::new();
    let mut node_occurrence_count: IndexMap<String, i64> = IndexMap::new();

    if let Some(results) = results_by_query.get(original_query) {
        for (node, score) in results {
            let id = node.borrow().node_id.clone();
            node_scores.insert(id.clone(), (*score, node.clone()));
            node_occurrence_count.insert(id, 1);
        }
    }
    for (query, results) in results_by_query {
        if query == original_query {
            continue;
        }
        for (node, score) in results {
            let id = node.borrow().node_id.clone();
            let count = node_occurrence_count.get(&id).copied().unwrap_or(0);
            if let Some((old_score, _)) = node_scores.get(&id) {
                let new_score = if count < 2 {
                    py_max(*old_score, score * (1.0 + 0.1))
                } else {
                    py_max(*old_score, score * duplicate_penalty)
                };
                node_scores.insert(id.clone(), (new_score, node.clone()));
            } else {
                node_scores.insert(id.clone(), (*score, node.clone()));
            }
            node_occurrence_count.insert(id, count + 1);
        }
    }
    let mut ranked: Vec<(NodeRef, f64)> = node_scores.into_values().map(|(score, node)| (node, score)).collect();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    let n = py_prefix_len(ranked.len(), limit);
    ranked.truncate(n);
    ranked
}

// =============================================================================
// PROMPT SELECTOR
// =============================================================================

static SINGLE_HOP_RES: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| {
    [
        r"what is \w+'s",
        r"what does \w+ ",
        r"where is \w+",
        r"where does \w+",
        r"who is \w+'s",
        r"how does \w+",
        r"what did \w+ ",
    ]
    .iter()
    .map(|p| py_regex(p))
    .collect()
});

/// `select_prompt_for_query(query, is_list_question=False, list_type=None)`: the answer template for a query.
///
/// Python bug kept: "do .* and .* have" and the "has .* read"-style entries look like regexes but are tested as
/// plain substrings, so they only match text containing a literal ".*".
pub fn select_prompt_for_query(query: &str, is_list_question: bool, list_type: Option<&str>) -> &'static str {
    let query_lower = query.to_lowercase();
    let contains_any = |words: &[&str]| words.iter().any(|w| query_lower.contains(w));

    if contains_any(&["in common", "both", "share", "do .* and .* have"]) {
        return AGGREGATION_ANSWER_PROMPT;
    }
    if list_type == Some("aggregation") {
        return AGGREGATION_ANSWER_PROMPT;
    }
    if is_list_question && list_type == Some("plural") {
        return LIST_QUESTION_ANSWER_PROMPT;
    }
    if contains_any(&[
        "what books",
        "what movies",
        "what types",
        "what kinds",
        "what things",
        "which items",
        "how many",
        "all of",
        "has .* read",
        "has .* done",
        "has .* made",
        "has .* painted",
        "have they",
    ]) {
        return LIST_QUESTION_ANSWER_PROMPT;
    }
    if contains_any(&["when", "what time", "what date", "how long ago", "which year", "which month"]) {
        return TEMPORAL_QUERY_PROMPT;
    }
    if contains_any(&["together", "compared to"]) {
        return MULTI_HOP_QUERY_PROMPT;
    }
    if contains_any(&[
        "likely", "probably", "would", "might", "could", "think", "feel", "predict", "expect", "pursue", "future",
    ]) {
        return OPEN_DOMAIN_QUERY_PROMPT;
    }
    if SINGLE_HOP_RES.iter().any(|re| py_search(re, &query_lower)) {
        return SINGLE_HOP_ANSWER_PROMPT;
    }
    RETRIEVAL_ANSWER_PROMPT
}

/// Every exported prompt constant by its Python name (`__all__` order).
pub const ALL_PROMPTS: &[(&str, &str)] = &[
    ("RETRIEVAL_ANSWER_PROMPT", RETRIEVAL_ANSWER_PROMPT),
    ("SINGLE_HOP_ANSWER_PROMPT", SINGLE_HOP_ANSWER_PROMPT),
    ("LIST_QUESTION_ANSWER_PROMPT", LIST_QUESTION_ANSWER_PROMPT),
    ("AGGREGATION_ANSWER_PROMPT", AGGREGATION_ANSWER_PROMPT),
    ("TEMPORAL_QUERY_PROMPT", TEMPORAL_QUERY_PROMPT),
    ("MULTI_HOP_QUERY_PROMPT", MULTI_HOP_QUERY_PROMPT),
    ("OPEN_DOMAIN_QUERY_PROMPT", OPEN_DOMAIN_QUERY_PROMPT),
    ("QUERY_REWRITE_PROMPT", QUERY_REWRITE_PROMPT),
    ("SINGLE_HOP_REWRITE_PROMPT", SINGLE_HOP_REWRITE_PROMPT),
    ("TEMPORAL_REWRITE_PROMPT", TEMPORAL_REWRITE_PROMPT),
];
