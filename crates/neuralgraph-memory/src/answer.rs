//! The measured routed reader protocol as a product call: [`ChatMemory::answer`].
//!
//! The memory delivers the evidence; the model that reads the context block decides how much of it reaches the answer.
//! The protocol (docs/CHAT_MEMORY.md, "Recommended reader protocol"; measured in `research/overnight`):
//!
//! 1. Build the context block with the configured defaults.
//! 2. Classify the question with [`COMPUTE_RE`] (a port of `COMPUTE_RE` in `research/overnight/scripts/answer_eval.py`:
//!    how long / how many / total / between / days / months ...).
//! 3. No match: the concise prompt ([`ANSWER_PROMPT`]), no thinking, 160 tokens. Match: the compute prompt
//!    ([`COMPUTE_PROMPT`], `research/overnight/prompts/auto_v1n.txt`) with thinking on and a 2500-token cap; the last
//!    `Answer:` line is the result, and an empty reply is re-asked once without thinking.
//! 4. IDK cascade: a concise answer that starts with "I don't know" is re-asked with the compute prompt (thinking on, same
//!    cap and fallback) and replaced by that answer unless it is also "I don't know". A question that already went to the
//!    compute prompt is not cascaded (it would repeat the same prompt).

use crate::error::{MemoryError, Result};
use crate::llm::GenerateOpts;
use crate::retrieval::SearchOptions;
use crate::service::ChatMemory;
use regex::Regex;
use serde_json::{json, Value};
use std::sync::LazyLock;
use std::time::Instant;

/// The concise prompt (`ANSWER_PROMPT` in `answer_eval.py`). Placeholders: `{date_line}`, `{memories}`, `{question}`.
pub const ANSWER_PROMPT: &str =
    "You are an assistant with long-term memory. Below are memories retrieved for the user's question.
Answer the question using ONLY these memories. Be specific and concise (one sentence or a short list).
If the memories do not contain the information needed, reply exactly: I don't know.
{date_line}
{memories}

Question: {question}
Answer:";

/// The compute prompt (`research/overnight/prompts/auto_v1n.txt` without its `#mode: auto` header line).
/// Placeholders: `{date_line}`, `{memories}`, `{question}`.
pub const COMPUTE_PROMPT: &str = r##"You are an assistant with long-term memory. The memories below were retrieved for the user's question, most relevant first.
Each line looks like: - [event date if known, kind] fact (from chat ID, DATE the user said it).
Rules:
1. Use ONLY these memories. Words like "a month ago", "now", "last week", "recently" inside a memory are relative to the DATE the user said it, not to today.
2. If the answer is stated in a memory, copy it. If it must be worked out (a duration, the difference between two dates, a total, a count of items, a unit conversion, which value is the latest), first write at most 6 short lines: list the exact numbers or dates you use, then calculate. Never use a memory's number as the answer when the question asks for a difference or a total.
3. If memories give different values for the same thing, use the one with the later DATE.
4. Say "I don't know" only if the specific thing asked about is neither stated nor derivable. A related but different fact (a 12-foot ladder when asked about a 20-foot ladder) does not count. If a memory gives the value, or the pieces to compute it, you must answer; never refuse because a more recent or extra detail is missing.
5. Finish with ONE line: "Answer: <result>". The result is exactly what was asked, with its unit or noun (for example "45 minutes", "$275", "3 weeks ago", "Blue Harbor Bakery"), includes every item the question asks for (all list members, all parts of a total), and has no "about/around/approximately" unless the memory itself only gives an estimate. No explanation on that line.
{date_line}
{memories}

Question: {question}
"##;

/// Questions that need arithmetic over several memories (durations, counts, totals, date differences). The text is the
/// one of `COMPUTE_RE` in `answer_eval.py`; matching is case-insensitive.
pub const COMPUTE_RE_SRC: &str = r"\b(?:how (?:long|many|much|old)|in total|total|altogether|combined|sum of|number of|difference|between|days|weeks|months|years)\b";

static COMPUTE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!("(?i){COMPUTE_RE_SRC}")).expect("COMPUTE_RE compiles"));

static ANSWER_LINE_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"Answer:\s*\**\s*(.+)").unwrap());
static EMPTY_RE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s*_`#>~|\-]+").unwrap());

/// Concise first pass: one sentence, no thinking.
pub const CONCISE_MAX_TOKENS: i64 = 160;
/// Compute call with thinking: the reasoning counts against this cap.
pub const COMPUTE_MAX_TOKENS: i64 = 2500;
/// The non-thinking re-ask after an empty compute reply.
pub const FALLBACK_MAX_TOKENS: i64 = 300;
const CONCISE_TIMEOUT: f64 = 120.0;
const COMPUTE_TIMEOUT: f64 = 600.0;

/// True when the question asks for a duration, count, total or date difference ([`COMPUTE_RE_SRC`]).
pub fn is_compute_question(question: &str) -> bool {
    COMPUTE_RE.is_match(question)
}

/// "I don't know" (straight or curly apostrophe), case-insensitive, at the start of the answer.
pub fn is_idk(answer: &str) -> bool {
    answer.trim().to_lowercase().replace('\u{2019}', "'").starts_with("i don't know")
}

/// Drop closed `<think>..</think>` blocks, then anything up to a stray closing tag or from an unclosed `<think>` on.
fn strip_think(text: &str) -> String {
    static CLOSED: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<think>.*?</think>").unwrap());
    static OPEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)<think>.*").unwrap());
    let mut t = CLOSED.replace_all(text, "").into_owned();
    if let Some(i) = t.rfind("</think>") {
        t = t[i + "</think>".len()..].to_string();
    }
    OPEN.replace_all(&t, "").trim().to_string()
}

/// The result of a compute reply: the first line after the LAST `Answer:` (tolerates `**Answer:** x` and `Answer: **x**`),
/// else the reply's last line; surrounding `*` removed.
pub fn parse_compute_answer(raw: &str) -> String {
    let last = ANSWER_LINE_RE.captures_iter(raw).last().map(|c| c[1].to_string());
    let ans = last.unwrap_or_else(|| raw.trim().split('\n').next_back().unwrap_or("").to_string());
    let ans = ans.trim();
    let stripped = ans.trim_matches('*').trim();
    if stripped.is_empty() {
        ans.to_string()
    } else {
        stripped.to_string()
    }
}

/// Anything left after dropping think tags, whitespace and markdown punctuation.
fn has_content(text: &str) -> bool {
    !EMPTY_RE.replace_all(&strip_think(text), "").is_empty()
}

/// Which prompt answers the question.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum AnswerRoute {
    /// classify with [`is_compute_question`] (the measured protocol)
    #[default]
    Auto,
    /// always the concise prompt (the IDK cascade still applies)
    Concise,
    /// always the compute prompt with thinking
    Compute,
}

impl AnswerRoute {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "auto" => Some(AnswerRoute::Auto),
            "concise" => Some(AnswerRoute::Concise),
            "compute" => Some(AnswerRoute::Compute),
            _ => None,
        }
    }
}

/// Per-call options of [`ChatMemory::answer`].
#[derive(Clone, Debug, Default)]
pub struct AnswerOptions {
    /// "Today" for the prompt's `Today's date:` line (typically `YYYY-MM-DD`); the UTC date now when absent.
    pub today: Option<String>,
    pub route: AnswerRoute,
    /// retrieval filters for the context block (subject, chat, kinds, date window); `k`/`max_chars` stay at the defaults
    pub filters: SearchOptions,
}

/// The answer and how it was reached.
#[derive(Clone, Debug)]
pub struct Answer {
    pub text: String,
    /// the question went to the compute prompt (matched [`COMPUTE_RE_SRC`], or `route = Compute`)
    pub routed: bool,
    /// the concise answer was "I don't know" and the compute prompt's answer replaced it
    pub cascaded: bool,
    /// the memory context block the reader saw (empty when nothing relevant is stored)
    pub context: String,
    /// model calls made, thinking and fallback calls included
    pub calls: u32,
    /// wall time of the whole call, retrieval included
    pub seconds: f64,
}

impl Answer {
    pub fn to_value(&self) -> Value {
        json!({
            "text": self.text,
            "routed": self.routed,
            "cascaded": self.cascaded,
            "context": self.context,
            "calls": self.calls,
            "seconds": self.seconds,
        })
    }
}

/// `str.format` for the three placeholders, in one pass (text inside a memory that looks like a placeholder stays text).
fn fill(template: &str, date_line: &str, memories: &str, question: &str) -> String {
    static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{(date_line|memories|question)\}").unwrap());
    PLACEHOLDER
        .replace_all(template, |c: &regex::Captures| match &c[1] {
            "date_line" => date_line.to_string(),
            "memories" => memories.to_string(),
            _ => question.to_string(),
        })
        .into_owned()
}

impl ChatMemory {
    /// Answer `question` from the stored memories with the measured routed reader protocol (see the module docs).
    pub async fn answer(&self, question: &str, opts: &AnswerOptions) -> Result<Answer> {
        let t0 = Instant::now();
        let question = question.trim();
        if question.is_empty() {
            return Err(MemoryError::Value("question must not be empty".into()));
        }
        let mut filters = opts.filters.clone();
        if filters.now.is_none() {
            filters.now = opts.today.clone().filter(|t| !t.is_empty());
        }
        let context = self.context_for(question, None, None, &filters).await?;
        let today = match opts.today.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
            Some(t) => t.to_string(),
            None => crate::clock::utcnow().format("%Y-%m-%d").to_string(),
        };
        let date_line = format!("Today's date: {today}");
        let memories = if context.is_empty() { "(no relevant memories)" } else { context.as_str() };
        let llm = self.llm();
        let mut calls = 0u32;

        let routed = match opts.route {
            AnswerRoute::Auto => is_compute_question(question),
            AnswerRoute::Concise => false,
            AnswerRoute::Compute => true,
        };
        let compute_prompt = fill(COMPUTE_PROMPT, &date_line, memories, question);

        let mut cascaded = false;
        let text = if routed {
            self.compute_call(&compute_prompt, &mut calls).await?
        } else {
            let prompt = fill(ANSWER_PROMPT, &date_line, memories, question);
            let o = GenerateOpts {
                max_tokens: CONCISE_MAX_TOKENS,
                temperature: 0.0,
                timeout: CONCISE_TIMEOUT,
                think: false,
            };
            calls += 1;
            let mut text = llm.generate_with(&prompt, &o).await?.trim().to_string();
            if is_idk(&text) {
                // A failed cascade call must not lose the concise answer.
                match self.compute_call(&compute_prompt, &mut calls).await {
                    Ok(second) if !is_idk(&second) && !second.trim().is_empty() => {
                        text = second;
                        cascaded = true;
                    }
                    Ok(_) => {}
                    Err(e) => tracing::warn!("answer: IDK cascade failed, keeping the concise answer: {e}"),
                }
            }
            text
        };
        Ok(Answer { text, routed, cascaded, context, calls, seconds: t0.elapsed().as_secs_f64() })
    }

    /// The compute prompt with thinking on (2500-token cap); an empty reply is re-asked once without thinking.
    async fn compute_call(&self, prompt: &str, calls: &mut u32) -> Result<String> {
        let llm = self.llm();
        let o =
            GenerateOpts { max_tokens: COMPUTE_MAX_TOKENS, temperature: 0.0, timeout: COMPUTE_TIMEOUT, think: true };
        *calls += 1;
        let raw = strip_think(&llm.generate_with(prompt, &o).await?);
        let ans = parse_compute_answer(&raw);
        if has_content(&ans) {
            return Ok(ans);
        }
        let o = GenerateOpts { max_tokens: FALLBACK_MAX_TOKENS, think: false, ..o };
        *calls += 1;
        let raw = strip_think(&llm.generate_with(prompt, &o).await?);
        Ok(parse_compute_answer(&raw))
    }
}
