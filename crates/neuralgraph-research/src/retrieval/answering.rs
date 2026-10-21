//! Answer generation and embedding helpers for the benchmark runner and the service (port of
//! `retrieval/answering.py`).
//!
//! Prompt constants are byte-identical to the Python strings; templates are filled in one pass like
//! `str.format`. Failures never propagate: an embedding error yields an empty vector, an answer error the text
//! `"Error: <message>"`, as in Python.

use super::prompts::{AGGREGATION_ANSWER_PROMPT, LIST_QUESTION_ANSWER_PROMPT};
use super::pytext::py_format;
use crate::llm_backend::{embed_model, llm_base_url, llm_model, EmbedOptions, GenerateOptions, Llm};

/// Configuration for LLM answering and embeddings (`AnsweringConfig`).
#[derive(Clone, Debug, PartialEq)]
pub struct AnsweringConfig {
    /// Any OpenAI-compatible or Ollama URL.
    pub ollama_base_url: String,
    pub answer_model: String,
    pub embedding_model: String,
    pub answer_timeout_seconds: f64,
    pub embedding_timeout_seconds: f64,
    /// `max_tokens` of an answer (60 truncated LIST/AGGREGATION answers mid-list).
    pub num_predict: i64,
}

impl Default for AnsweringConfig {
    /// The `llm_backend` settings (`LLM_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL`) and Python's defaults.
    fn default() -> Self {
        AnsweringConfig {
            ollama_base_url: llm_base_url(),
            answer_model: llm_model(),
            embedding_model: embed_model(),
            answer_timeout_seconds: 60.0,
            embedding_timeout_seconds: 30.0,
            num_predict: 120,
        }
    }
}

pub const TEMPORAL_ANSWER_PROMPT: &str = "Answer using ONLY the memories below.

TEMPORAL REASONING RULES:
1. Each memory has [MESSAGE_DATETIME=...] showing WHEN it was said.
2. If present, [RESOLVED_DATE=...] provides a canonical date for the event.
3. If present, [RESOLVED_RELATIVE=...] provides precomputed resolutions for relative phrases.
4. If the memory says \"yesterday\" or \"last week\", compute the actual date using MESSAGE_DATETIME.
   - MESSAGE_DATETIME=2023-05-08 + \"yesterday\" = May 7, 2023
   - MESSAGE_DATETIME=2023-05-08 + \"last week\" = early May 2023
5. If the memory states an explicit date (e.g., \"on May 5th\"), use that date.
6. Do NOT just return MESSAGE_DATETIME - that's when the message was sent, not when the event happened.
7. If the date cannot be computed from the memories, say \"Not mentioned in the memories\".
8. Output ONLY the date/time period (concise).
9. Match the granularity: if memory says \"in May\" -> answer \"May 2023\", not a specific day.

MEMORIES:
{context}

QUESTION: {question}

Answer:";

pub const OPEN_DOMAIN_INFER_PROMPT: &str = "Infer the answer from these memories.

RULES:
1. Output ONLY the answer - no explanations, no \"Based on...\", no bullets.
2. For Yes/No: just \"Yes\" or \"No\"
3. For lists: comma-separated format
4. Use ONLY evidence from memories, do not guess.
5. If unclear, give your best inference in 1-5 words.

MEMORIES:
{context}

QUESTION: {question}

Answer:";

pub const OPEN_DOMAIN_WORLD_PROMPT: &str = "Answer this as a general knowledge question.

RULES:
1. Ignore the conversation memories entirely.
2. Be concise: 1-2 sentences.
3. If uncertain, say \"I'm not sure.\"
4. If the question is A-or-B or Yes/No, answer in that format.

QUESTION: {question}

Answer:";

/// H5: open-domain questions need the memories plus world knowledge. Used for mode `OPEN_DOMAIN_WORLD_MEM`
/// (OPEN_DOMAIN_WORLD with OPEN_DOMAIN_KEEP_CONTEXT=1) and `OPEN_DOMAIN_INFER_WORLD`.
pub const OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT: &str =
    "Answer the question about the people in these conversation memories.

RULES:
1. The memories are your PRIMARY evidence: base the answer on what they say about the person.
2. You MAY combine them with general world knowledge (e.g. what a hobby, book, place, or brand implies).
3. Give a short, direct answer: a few words, or Yes/No, optionally followed by one brief reason.
4. Never say you are unsure, never say the answer is not in the memories: always commit to the most likely answer.
5. No preamble, no bullets, no \"Based on the memories\".

MEMORIES:
{context}

QUESTION: {question}

Answer:";

pub const STRICT_ANSWER_PROMPT: &str = "Extract the answer from the memories below.

RULES:
1. Output ONLY the answer - no explanations, no \"Based on...\", no bullets.
2. If multiple items, use comma-separated format: item1, item2, item3
3. Match the person asked about (check speaker metadata).
4. If not found, output exactly: Not found

EXAMPLES:
Q: What is John's job? → Nurse
Q: What are Mary's pets' names? → Luna, Bailey
Q: What has Tom painted? → Sunset, horse
Q: Where did Sarah move from? → Sweden

MEMORIES:
{context}

QUESTION: {question}

Answer:";

/// `get_embedding(session, text, config=None)`: the embedding, or an empty vector on any error.
pub async fn get_embedding(llm: &dyn Llm, text: &str, config: Option<&AnsweringConfig>) -> Vec<f64> {
    let default;
    let cfg = match config {
        Some(c) => c,
        None => {
            default = AnsweringConfig::default();
            &default
        }
    };
    let opts = EmbedOptions {
        model: Some(cfg.embedding_model.clone()),
        base_url: Some(cfg.ollama_base_url.clone()),
        timeout_seconds: cfg.embedding_timeout_seconds,
    };
    llm.embed(text, &opts).await.unwrap_or_default()
}

/// The filled answer prompt for a mode (`TEMPORAL`, `LIST`, `AGGREGATION`, `OPEN_DOMAIN_INFER`,
/// `OPEN_DOMAIN_WORLD`, `OPEN_DOMAIN_WORLD_MEM`, `OPEN_DOMAIN_INFER_WORLD`; anything else is `STRICT`).
pub fn answer_prompt(question: &str, context: &str, mode: &str) -> String {
    let fields = [("context", context), ("question", question)];
    match mode {
        "TEMPORAL" => py_format(TEMPORAL_ANSWER_PROMPT, &fields),
        "LIST" => py_format(LIST_QUESTION_ANSWER_PROMPT, &fields),
        "AGGREGATION" => py_format(AGGREGATION_ANSWER_PROMPT, &fields),
        "OPEN_DOMAIN_INFER" => py_format(OPEN_DOMAIN_INFER_PROMPT, &fields),
        "OPEN_DOMAIN_WORLD" => py_format(OPEN_DOMAIN_WORLD_PROMPT, &[("question", question)]),
        "OPEN_DOMAIN_WORLD_MEM" | "OPEN_DOMAIN_INFER_WORLD" => {
            py_format(OPEN_DOMAIN_WORLD_WITH_MEMORIES_PROMPT, &fields)
        }
        _ => py_format(STRICT_ANSWER_PROMPT, &fields),
    }
}

/// `generate_answer(session, question, context, mode="STRICT", config=None)`: the model's answer at
/// temperature 0 with `num_predict` tokens, or `"Error: <message>"` if the call failed.
pub async fn generate_answer(
    llm: &dyn Llm,
    question: &str,
    context: &str,
    mode: &str,
    config: Option<&AnsweringConfig>,
) -> String {
    let default;
    let cfg = match config {
        Some(c) => c,
        None => {
            default = AnsweringConfig::default();
            &default
        }
    };
    let prompt = answer_prompt(question, context, mode);
    let opts = GenerateOptions::default()
        .model(cfg.answer_model.clone())
        .base_url(cfg.ollama_base_url.clone())
        .temperature(0.0)
        .max_tokens(cfg.num_predict)
        .timeout(cfg.answer_timeout_seconds);
    match llm.generate(&prompt, &opts).await {
        Ok(text) => text,
        Err(e) => format!("Error: {e}"),
    }
}
