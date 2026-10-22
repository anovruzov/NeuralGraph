//! Port of `research/benchmarks/call_center.py`: the NeuralGraph memory pipeline on a synthetic call-center
//! conversation (`research/datasets/call_center_benchmark.json`), to check that it generalises beyond LoCoMo
//! (binary `call-center`). Writes `research/results/call_center_results.json`.
//!
//! The Python talked to a hard-coded Ollama server (`http://localhost:11434`, `qwen2.5:7b-instruct`,
//! `nomic-embed-text`) through raw `/api/embeddings` and `/api/generate` calls, independent of `llm_backend`; that
//! is [`OllamaHttp`] here, behind the [`CallCenterModel`] trait so tests can script it. Speaker facts went through
//! `llm_profile_extractor` (the `llm_backend` settings), which is the `&dyn Llm`. The script's `OPENAI_API_KEY` was
//! the constant `None`, so its GPT-4o judging branch never ran; it is ported but unreachable.
//!
//! Python bugs kept:
//! - the "dialogue link" expansion iterated `get_linked_messages`, which yields `(id, strength, type)` tuples, and
//!   looked each tuple up as a node id: never found, so nothing was ever added. The port adds nothing either.
//! - with no answered question, printing the average latency divided by zero (`ZeroDivisionError`).

use super::pyharness::{json_indent, perf_counter, repo_root, HResult, HarnessError};
use super::runner::{add_conversation_edges, sort_by_charge_desc, Ranked, TOP_K};
use crate::llm_backend::Llm;
use crate::pytime::PyDateTime;
use crate::retrieval::data_types::{node_ref, NeuralNode, NodeLayer, NodeRef};
use crate::retrieval::dialogue_linker::DialogueLinker;
use crate::retrieval::llm_profile_extractor::extract_facts_parallel;
use crate::retrieval::pytext::{py_findall, py_regex};
use crate::retrieval::speaker_profiles::UniversalSpeakerProfiler;
use crate::retrieval::storage::{InMemoryNeuralGraphStorage, NeuralGraphStorage};
use crate::retrieval::tesseract::Tesseract;
use crate::temporal_utils::{
    generate_temporal_tokens, infer_query_mode, parse_datetime_flexible, resolve_relative_dates,
};
use async_trait::async_trait;
use indexmap::{IndexMap, IndexSet};
use neuralgraph_memory::pycompat::{dumps, fnum, obj, py_prefix, py_round, py_str, py_strip};
use serde_json::{json, Map, Value};
use std::io::Write;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::LazyLock;

pub const OLLAMA_BASE_URL: &str = "http://localhost:11434";
pub const OLLAMA_MODEL: &str = "qwen2.5:7b-instruct";
pub const EMBEDDING_MODEL: &str = "nomic-embed-text";
/// `OPENAI_API_KEY = None`: GPT-4o judging is off.
pub const OPENAI_API_KEY: Option<&str> = None;

/// `BENCHMARK_PATH`
pub fn benchmark_path() -> PathBuf {
    repo_root().join("research").join("datasets").join("call_center_benchmark.json")
}

/// `OUTPUT_PATH`
pub fn output_path() -> PathBuf {
    repo_root().join("research").join("results").join("call_center_results.json")
}

/// The model calls of the script.
#[async_trait(?Send)]
pub trait CallCenterModel {
    /// `POST /api/embeddings`: the response's `embedding` (`[]` when absent); `Err` on a transport/JSON failure.
    async fn embed(&self, text: &str) -> Result<Vec<f64>, String>;
    /// `POST /api/generate` (temperature 0, 150 tokens): the response's `response`, stripped.
    async fn generate(&self, prompt: &str) -> Result<String, String>;
    /// The GPT-4o judge call (unreachable: `OPENAI_API_KEY` is `None`).
    async fn openai_judge(&self, _prompt: &str) -> Result<String, String> {
        Err("no OpenAI key".into())
    }
}

/// The real Ollama server at [`OLLAMA_BASE_URL`].
#[derive(Default)]
pub struct OllamaHttp {
    pub client: reqwest::Client,
}

async fn post(client: &reqwest::Client, url: &str, body: &Value, timeout: u64) -> Result<Value, String> {
    let resp = client
        .post(url)
        .timeout(std::time::Duration::from_secs(timeout))
        .json(body)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    resp.json::<Value>().await.map_err(|e| e.to_string())
}

#[async_trait(?Send)]
impl CallCenterModel for OllamaHttp {
    async fn embed(&self, text: &str) -> Result<Vec<f64>, String> {
        let body = json!({"model": EMBEDDING_MODEL, "prompt": text});
        let result = post(&self.client, &format!("{OLLAMA_BASE_URL}/api/embeddings"), &body, 30).await?;
        match result.get("embedding") {
            None => Ok(Vec::new()),
            Some(Value::Array(a)) => Ok(a.iter().filter_map(Value::as_f64).collect()),
            Some(other) => Err(format!("embedding is not a list: {other}")),
        }
    }

    async fn generate(&self, prompt: &str) -> Result<String, String> {
        let body = json!({"model": OLLAMA_MODEL, "prompt": prompt, "stream": false,
                          "options": {"temperature": 0, "num_predict": 150}});
        let result = post(&self.client, &format!("{OLLAMA_BASE_URL}/api/generate"), &body, 60).await?;
        Ok(py_strip(result.get("response").and_then(Value::as_str).unwrap_or("")).to_string())
    }

    async fn openai_judge(&self, prompt: &str) -> Result<String, String> {
        let body = json!({"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 10, "temperature": 0});
        let resp = self
            .client
            .post("https://api.openai.com/v1/chat/completions")
            .header("Authorization", format!("Bearer {}", OPENAI_API_KEY.unwrap_or("")))
            .timeout(std::time::Duration::from_secs(30))
            .json(&body)
            .send()
            .await
            .map_err(|e| e.to_string())?;
        let result: Value = resp.json().await.map_err(|e| e.to_string())?;
        let content = result["choices"][0]["message"]["content"].as_str().ok_or("KeyError: 'choices'")?;
        Ok(py_strip(content).to_uppercase())
    }
}

static KEYWORD_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[a-z]{3,}\b"));
static CAPITALISED_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[A-Z][a-z]+\b"));
static KEY_TERM_RE: LazyLock<fancy_regex::Regex> = LazyLock::new(|| py_regex(r"\b[a-z0-9]{3,}\b"));

/// `extract_keywords(text)`: the script's own copy (lower-case words of three or more letters minus stopwords).
pub fn extract_keywords(text: &str) -> Vec<String> {
    const STOP: [&str; 14] =
        ["the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our"];
    py_findall(&KEYWORD_RE, &text.to_lowercase()).into_iter().filter(|w| !STOP.contains(&w.as_str())).collect()
}

/// `get_embedding(session, text)`: the embedding, or `[]` after printing `Embedding error: ...`.
pub async fn get_embedding(model: &dyn CallCenterModel, text: &str, out: &mut dyn Write) -> HResult<Vec<f64>> {
    match model.embed(text).await {
        Ok(v) => Ok(v),
        Err(e) => {
            writeln!(out, "Embedding error: {e}")?;
            Ok(Vec::new())
        }
    }
}

/// The answer prompt per query mode (TEMPORAL, AGGREGATION, anything else).
pub fn answer_prompt(question: &str, context: &str, mode: &str) -> String {
    match mode {
        "TEMPORAL" => format!(
            "Answer using ONLY the memories below.\n\nTEMPORAL RULES:\n1. If the event date is explicitly resolved in text, use that.\n2. Output ONLY the date/time period (concise).\n3. If the date cannot be determined, say \"Not mentioned in the memories\".\n\nMEMORIES:\n{context}\n\nQUESTION: {question}\n\nAnswer (date/time only):"
        ),
        "AGGREGATION" => format!(
            "Answer by AGGREGATING information from the memories below.\n\nAGGREGATION RULES:\n1. SCAN ALL PROVIDED MEMORIES - the answer may be scattered across multiple memories.\n2. BE SPECIFIC - extract exact names, places, items.\n3. BE COMPLETE - find ALL items mentioned.\n4. If not found, say \"Not mentioned in the memories\".\n\nMEMORIES:\n{context}\n\nQUESTION: {question}\n\nAnswer (be specific and complete):"
        ),
        _ => format!(
            "Answer the question using ONLY the memories below.\n\nRULES:\n1. Speaker metadata shows WHO said what.\n2. Use only facts EXPLICITLY supported by the provided memories.\n3. Keep answer short: 1-2 sentences.\n4. If not found, say \"Not mentioned in the memories\".\n\nMEMORIES:\n{context}\n\nQUESTION: {question}\n\nAnswer:"
        ),
    }
}

/// `generate_answer(session, question, context, mode)`: the answer, or `Error: ...`.
pub async fn generate_answer(model: &dyn CallCenterModel, question: &str, context: &str, mode: &str) -> String {
    match model.generate(&answer_prompt(question, context, mode)).await {
        Ok(a) => a,
        Err(e) => format!("Error: {e}"),
    }
}

/// The GPT-4o judge prompt.
pub fn judge_prompt(question: &str, expected: &str, actual: &str) -> String {
    format!(
        "Judge if the answer is semantically correct.\n\nQuestion: {question}\nExpected: {expected}\nActual: {actual}\n\nReply with ONLY \"CORRECT\" or \"INCORRECT\"."
    )
}

/// `judge_answer(session, question, expected, actual)`: `(correct, how)`: the expected answer as a substring
/// (`exact_match`), else at least 60% of its key terms (`key_terms (m/n)`), else GPT-4o (never: no key), else
/// `no_match`.
pub async fn judge_answer(model: &dyn CallCenterModel, question: &str, expected: &str, actual: &str) -> (bool, String) {
    let expected_lower = py_strip(&expected.to_lowercase()).to_string();
    let actual_lower = py_strip(&actual.to_lowercase()).to_string();
    if actual_lower.contains(expected_lower.as_str()) {
        return (true, "exact_match".into());
    }
    const STOP: [&str; 8] = ["the", "and", "for", "are", "but", "not", "you", "all"];
    let key_terms: Vec<String> =
        py_findall(&KEY_TERM_RE, &expected_lower).into_iter().filter(|t| !STOP.contains(&t.as_str())).collect();
    if !key_terms.is_empty() {
        let matches = key_terms.iter().filter(|t| actual_lower.contains(t.as_str())).count();
        if matches as f64 >= key_terms.len() as f64 * 0.6 {
            return (true, format!("key_terms ({matches}/{})", key_terms.len()));
        }
    }
    if OPENAI_API_KEY.is_some() {
        if let Ok(judgment) = model.openai_judge(&judge_prompt(question, expected, actual)).await {
            return (judgment.contains("CORRECT"), "gpt4o".into());
        }
    }
    (false, "no_match".into())
}

/// One prepared message.
struct CcMessage {
    speaker: String,
    text: String,
    datetime: String,
}

/// `Agent_Sarah` -> `Sarah`, `Customer_David` -> `David` (only the first occurrence of the prefix is replaced,
/// as `str.replace` on a string starting with it).
pub fn display_speaker(speaker: &str) -> String {
    for prefix in ["Agent_", "Customer_"] {
        if speaker.starts_with(prefix) {
            return speaker.replace(prefix, "");
        }
    }
    speaker.to_string()
}

#[derive(Default, Clone, Copy)]
struct Tally {
    correct: i64,
    total: i64,
}

fn tally_json(m: &IndexMap<String, Tally>) -> Value {
    Value::Object(
        m.iter()
            .map(|(k, t)| (k.clone(), obj([("correct", Value::from(t.correct)), ("total", Value::from(t.total))])))
            .collect(),
    )
}

fn field<'a>(v: &'a Value, key: &str) -> HResult<&'a Value> {
    v.get(key).ok_or_else(|| HarnessError::new("KeyError", format!("'{key}'")))
}

/// `run_benchmark()`: the whole script. Returns the report document it wrote to `output`.
pub async fn run_benchmark(
    llm: &dyn Llm,
    model: &dyn CallCenterModel,
    benchmark: &std::path::Path,
    output: &std::path::Path,
    out: &mut dyn Write,
) -> HResult<Value> {
    let bar = "=".repeat(70);
    writeln!(out, "{bar}")?;
    writeln!(out, "CALL CENTER BENCHMARK - NeuralGraph Memory System")?;
    writeln!(out, "{bar}")?;
    writeln!(out, "Started: {}", PyDateTime::now_local().naive.format("%Y-%m-%d %H:%M:%S"))?;
    writeln!(out, "Purpose: Verify system generalizes beyond LoCoMo benchmark")?;
    writeln!(out)?;

    let data: Value = serde_json::from_str(&std::fs::read_to_string(benchmark)?)?;
    let empty = Vec::new();
    let conversation = field(&data, "conversation")?.as_array().unwrap_or(&empty);
    let questions = field(&data, "questions")?.as_array().unwrap_or(&empty);
    writeln!(out, "Conversation turns: {}", conversation.len())?;
    writeln!(out, "Test questions: {}", questions.len())?;
    writeln!(out)?;

    writeln!(out, "Initializing NeuralGraph components...")?;
    let storage = Rc::new(InMemoryNeuralGraphStorage::default());
    let tesseract = Tesseract::new(storage.clone());
    let mut dialogue_linker = DialogueLinker::new(&*storage);
    let mut speaker_profiler = UniversalSpeakerProfiler::new();

    let base_time = PyDateTime::now_local().naive.format("%Y-%m-%d %H:%M:%S").to_string();
    let mut messages = Vec::new();
    for turn in conversation {
        let speaker = py_str(field(turn, "speaker")?);
        if speaker == "System" {
            continue;
        }
        messages.push(CcMessage {
            speaker: display_speaker(&speaker),
            text: py_str(field(turn, "text")?),
            datetime: base_time.clone(),
        });
    }
    writeln!(out, "Prepared {} messages for ingestion", messages.len())?;

    writeln!(out, "Extracting speaker facts...")?;
    let extraction_start = perf_counter();
    let pairs: Vec<(&str, &str)> = messages.iter().map(|m| (m.speaker.as_str(), m.text.as_str())).collect();
    let all_facts = extract_facts_parallel(llm, &pairs, 10).await.map_err(|e| HarnessError::new("ValueError", e))?;
    writeln!(out, "  Extracted facts in {:.1}s", perf_counter() - extraction_start)?;
    for (msg, facts) in messages.iter().zip(&all_facts) {
        speaker_profiler
            .add_message(&msg.speaker, &msg.text, None)
            .await
            .map_err(|e| HarnessError::new("Exception", e))?;
        if !facts.is_empty() {
            if let Some(p) = speaker_profiler.profiles.get_mut(&msg.speaker) {
                p.extracted_facts.extend(facts.iter().cloned());
            }
        }
    }

    writeln!(out, "Indexing messages...")?;
    let session_key = "call_center_001";
    let mut all_node_ids: Vec<String> = Vec::new();
    let mut speaker_nodes: IndexMap<String, Vec<String>> = IndexMap::new();
    for (msg_idx, msg) in messages.iter().enumerate() {
        let embedding = get_embedding(model, &msg.text, out).await?;
        if embedding.is_empty() {
            continue;
        }
        let keywords = extract_keywords(&msg.text);
        // a Python set (order unobservable): first-occurrence order here
        let entities: IndexSet<String> = py_findall(&CAPITALISED_RE, &msg.text).into_iter().collect();
        let terr = |e: crate::temporal_utils::TemporalError| HarnessError::new("ValueError", e.0);
        let message_date = parse_datetime_flexible(Some(&msg.datetime)).map_err(terr)?.map(|d| d.naive);
        let temporal = generate_temporal_tokens(message_date, &msg.text).map_err(terr)?;
        let (resolved_content, _resolved_dates) = resolve_relative_dates(&msg.text, message_date).map_err(terr)?;

        let node_id = format!("cc_msg_{msg_idx}");
        let mut node = NeuralNode::new(node_id.clone(), NodeLayer::Message, resolved_content.clone());
        node.session_key = session_key.into();
        node.embedding = Some(embedding);
        node.created_at = PyDateTime::now_local();
        let mut meta = Map::new();
        meta.insert("speaker".into(), Value::from(msg.speaker.clone()));
        meta.insert("datetime".into(), Value::from(msg.datetime.clone()));
        meta.insert("keywords".into(), Value::from(keywords));
        meta.insert("entities".into(), Value::from(entities.into_iter().collect::<Vec<_>>()));
        meta.insert("date_tokens".into(), Value::from(temporal.date_tokens.clone()));
        meta.insert("temporal".into(), Value::Object(temporal.temporal_metadata.clone()));
        meta.insert("original_content".into(), Value::from(msg.text.clone()));
        meta.insert("resolved_content".into(), Value::from(resolved_content));
        node.metadata = meta;
        storage.save_node(&node_ref(node));
        all_node_ids.push(node_id.clone());
        speaker_nodes.entry(msg.speaker.to_lowercase()).or_default().push(node_id);
    }
    let (edge_count, temporal_edge_count) = add_conversation_edges(&*storage, &speaker_nodes, &all_node_ids, true);

    let all_nodes: Vec<NodeRef> = all_node_ids.iter().filter_map(|id| storage.get_node(id)).collect();
    let (aggregators, bindings) = dialogue_linker
        .process_dialogue_sequence(&all_nodes, session_key)
        .map_err(|e| HarnessError::new("IndexError", e))?;
    writeln!(out, "Indexed {} memories", all_node_ids.len())?;
    writeln!(out, "Created {edge_count} speaker edges, {temporal_edge_count} temporal edges")?;
    writeln!(out, "Dialogue links: {} aggregators, {} bindings", aggregators.len(), bindings.len())?;
    writeln!(out)?;

    writeln!(out, "Running test queries...")?;
    writeln!(out, "{}", "-".repeat(70))?;
    let (mut n_correct, mut n_incorrect) = (0i64, 0i64);
    let mut by_type: IndexMap<String, Tally> = IndexMap::new();
    let mut by_category: IndexMap<String, Tally> = IndexMap::new();
    let mut details = Vec::new();
    let mut total_query_time = 0.0;

    for q in questions {
        let qid = py_str(field(q, "id")?);
        let question = py_str(field(q, "question")?);
        let expected = py_str(field(q, "answer")?);
        let qtype = py_str(field(q, "type")?);
        let category = py_str(field(q, "category")?);
        by_type.entry(qtype.clone()).or_default();
        by_category.entry(category.clone()).or_default();

        let query_start = perf_counter();
        let query_emb = get_embedding(model, &question, out).await?;
        if query_emb.is_empty() {
            continue;
        }
        let retrieved = tesseract
            .retrieve(&question, &query_emb, session_key, None, TOP_K, true)
            .map_err(|e| HarnessError::new("TesseractError", format!("{e:?}")))?;

        // "expand with dialogue links": the links are (id, strength, type) tuples looked up as ids, so none is
        // ever found (see the module docs); only the de-duplication and the re-sort remain
        let mut expanded: Ranked = Vec::new();
        let mut seen: IndexSet<String> = IndexSet::new();
        for (node, charge) in &retrieved {
            let id = node.borrow().node_id.clone();
            if seen.insert(id.clone()) {
                expanded.push((node.clone(), *charge));
            }
            let _ = dialogue_linker.get_linked_messages(&id);
        }
        sort_by_charge_desc(&mut expanded);

        let query_mode = infer_query_mode(&question);
        let num_memories = if query_mode == "AGGREGATION" { 30 } else { 15 };
        let context = expanded
            .iter()
            .take(num_memories)
            .map(|(node, _)| {
                let n = node.borrow();
                let speaker = n.metadata.get("speaker").map(py_str).unwrap_or_else(|| "Unknown".into());
                let text = n.metadata.get("original_content").map(py_str).unwrap_or_else(|| n.content.clone());
                format!("[{speaker}] {text}")
            })
            .collect::<Vec<_>>()
            .join("\n");

        let answer = generate_answer(model, &question, &context, query_mode).await;
        let query_time = perf_counter() - query_start;
        total_query_time += query_time;

        let (is_correct, judgment) = judge_answer(model, &question, &expected, &answer).await;
        by_type[&qtype].total += 1;
        by_category[&category].total += 1;
        let status = if is_correct {
            n_correct += 1;
            by_type[&qtype].correct += 1;
            by_category[&category].correct += 1;
            "OK"
        } else {
            n_incorrect += 1;
            "FAIL"
        };
        details.push(obj([
            ("id", q["id"].clone()),
            ("question", Value::from(question.clone())),
            ("expected", Value::from(expected.clone())),
            ("actual", Value::from(answer.clone())),
            ("correct", Value::Bool(is_correct)),
            ("type", Value::from(qtype)),
            ("category", Value::from(category)),
            ("judgment", Value::from(judgment)),
            ("latency_ms", fnum(py_round(query_time * 1000.0, 1))),
        ]));
        writeln!(out, "[{status}] {qid}: {}...", py_prefix(&question, 50))?;
        if !is_correct {
            writeln!(out, "      Expected: {}...", py_prefix(&expected, 50))?;
            writeln!(out, "      Got: {}...", py_prefix(&answer, 50))?;
        }
    }

    writeln!(out)?;
    writeln!(out, "{bar}")?;
    writeln!(out, "RESULTS SUMMARY")?;
    writeln!(out, "{bar}")?;
    let total = n_correct + n_incorrect;
    let accuracy = if total > 0 { n_correct as f64 / total as f64 * 100.0 } else { 0.0 };
    writeln!(out, "\nOverall Accuracy: {n_correct}/{total} ({accuracy:.1}%)")?;
    if total == 0 {
        return Err(HarnessError::new("ZeroDivisionError", "float division by zero"));
    }
    writeln!(out, "Average Latency: {:.0}ms per query", total_query_time / total as f64 * 1000.0)?;
    for (title, table) in [("By Question Type", &by_type), ("By Category", &by_category)] {
        writeln!(out, "\n{title}:")?;
        let mut rows: Vec<(&String, &Tally)> = table.iter().collect();
        rows.sort_by(|a, b| a.0.cmp(b.0));
        for (k, t) in rows {
            let acc = if t.total > 0 { t.correct as f64 / t.total as f64 * 100.0 } else { 0.0 };
            writeln!(out, "  {k}: {}/{} ({acc:.1}%)", t.correct, t.total)?;
        }
    }

    let doc = obj([
        (
            "metadata",
            obj([
                ("benchmark", Value::from("call_center")),
                ("timestamp", Value::from(PyDateTime::now_local().isoformat())),
                ("purpose", Value::from("Verify generalization beyond LoCoMo")),
            ]),
        ),
        (
            "summary",
            obj([
                ("accuracy", fnum(py_round(accuracy, 1))),
                ("correct", Value::from(n_correct)),
                ("total", Value::from(total)),
                ("avg_latency_ms", fnum(py_round(total_query_time / total as f64 * 1000.0, 1))),
            ]),
        ),
        ("by_type", tally_json(&by_type)),
        ("by_category", tally_json(&by_category)),
        ("details", Value::Array(details)),
    ]);
    std::fs::write(output, dumps(&doc, &json_indent(2)))?;
    writeln!(out, "\nResults saved to: {}", output.display())?;

    writeln!(out)?;
    writeln!(out, "{bar}")?;
    if accuracy >= 80.0 {
        writeln!(out, "VERDICT: PASS - System generalizes beyond LoCoMo!")?;
        writeln!(out, "The memory system works for call center use cases.")?;
    } else if accuracy >= 60.0 {
        writeln!(out, "VERDICT: PARTIAL - Some generalization, needs improvement")?;
        writeln!(out, "Review failed questions to identify gaps.")?;
    } else {
        writeln!(out, "VERDICT: FAIL - System may be overfit to LoCoMo")?;
        writeln!(out, "Architecture changes may be needed for real-world use.")?;
    }
    writeln!(out, "{bar}")?;
    Ok(doc)
}
