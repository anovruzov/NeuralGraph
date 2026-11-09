//! `neuralgraph-memory hook claude-code`: automatic memory for Claude Code.
//!
//! Claude Code runs this command on two hook events (installed by `setup` into `~/.claude/settings.json`), passing
//! a JSON payload on stdin:
//!
//! - **Stop** (after every reply): read the session transcript from where the last run stopped and queue the new
//!   turns — the user's prompts and Claude's final answer of each turn — as chat messages. The background worker
//!   of any running `mcp`/`serve` process turns them into memories. Tool calls and results, thinking, system
//!   reminders and subagent transcripts are skipped. Each message keeps Claude Code's own id, so re-reading a
//!   transcript never stores anything twice.
//! - **UserPromptSubmit** (before Claude sees a prompt): print the memories relevant to the prompt. Claude Code adds
//!   a hook's stdout to the prompt's context, so recall needs no tool call.
//!
//! A hook must never get in the way: every failure is logged to stderr and the command still exits 0.

use crate::retrieval::SearchOptions;
use crate::service::{ChatMemory, MessageInput};
use regex::Regex;
use serde_json::Value;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::Duration;

/// One conversation turn worth remembering.
#[derive(Clone, Debug, PartialEq)]
pub struct Turn {
    /// Claude Code's message uuid (stable across re-reads)
    pub id: String,
    /// "user" or "assistant"
    pub role: String,
    pub text: String,
    pub timestamp: Option<String>,
}

/// How long a Claude Code turn waits before it is learned. Every new turn of the chat pushes the earlier ones back
/// (the queue's pull-forward debounce), so a working session is read in one go, ten minutes after it goes quiet,
/// in batches of up to [`crate::store::CODING_BATCH`] messages, instead of two messages at a time.
pub const CODING_DEBOUNCE_SECONDS: f64 = 600.0;

/// Longest text kept per message; the rest is cut (code and logs rarely hold memories).
const MAX_CHARS: usize = 2500;

static NOISE: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        // wrappers Claude Code and its harnesses put around user turns
        r"(?s)<system-reminder>.*?</system-reminder>",
        r"(?s)<system_instruction>.*?</system_instruction>",
        r"(?s)<command-(?:name|message|args)>.*?</command-(?:name|message|args)>",
        r"(?s)<local-command-(?:stdout|stderr|caveat)>.*?</local-command-(?:stdout|stderr|caveat)>",
        r"(?s)<task-notification>.*?</task-notification>",
        r"(?s)<neuralgraph-memory>.*?</neuralgraph-memory>",
        // fenced code: rarely a memory, often thousands of tokens
        r"(?s)```.*?```",
    ]
    .iter()
    .map(|p| Regex::new(p).expect("noise regex"))
    .collect()
});

/// Text with harness wrappers and code fences removed and whitespace tidied; empty when nothing human is left.
pub fn clean_text(raw: &str) -> String {
    let mut s = raw.to_string();
    for (i, re) in NOISE.iter().enumerate() {
        let with = if i + 1 == NOISE.len() { "[code]" } else { "" };
        s = re.replace_all(&s, with).into_owned();
    }
    let s = s.lines().map(str::trim_end).collect::<Vec<_>>().join("\n");
    let s = s.trim();
    if s.is_empty() || s == "[code]" || s.starts_with("[Request interrupted") {
        return String::new();
    }
    if s.chars().count() > MAX_CHARS {
        let cut: String = s.chars().take(MAX_CHARS).collect();
        return format!("{cut}…");
    }
    s.to_string()
}

/// The text blocks of a transcript `message.content` (a string, or a list of typed blocks).
fn content_text(content: &Value) -> Option<String> {
    match content {
        Value::String(s) => Some(s.clone()),
        Value::Array(blocks) => {
            // a user "message" made only of tool results is the harness talking, not the user
            let texts: Vec<&str> = blocks
                .iter()
                .filter(|b| b.get("type").and_then(Value::as_str) == Some("text"))
                .filter_map(|b| b.get("text").and_then(Value::as_str))
                .collect();
            (!texts.is_empty()).then(|| texts.join("\n"))
        }
        _ => None,
    }
}

/// The turns in transcript JSONL lines: each user prompt, and the last text Claude wrote before the next prompt
/// (its answer; the narration in between is dropped).
pub fn parse_transcript<'a>(lines: impl IntoIterator<Item = &'a str>) -> Vec<Turn> {
    let mut out: Vec<Turn> = Vec::new();
    let mut pending_answer: Option<Turn> = None;
    for line in lines {
        let Ok(d) = serde_json::from_str::<Value>(line) else { continue };
        let kind = d.get("type").and_then(Value::as_str).unwrap_or("");
        if !matches!(kind, "user" | "assistant") || d.get("isSidechain") == Some(&Value::Bool(true)) {
            continue;
        }
        if d.get("isMeta") == Some(&Value::Bool(true)) {
            continue;
        }
        let Some(id) = d.get("uuid").and_then(Value::as_str) else { continue };
        let Some(raw) = d.get("message").and_then(|m| m.get("content")).and_then(content_text) else { continue };
        let text = clean_text(&raw);
        if text.is_empty() {
            continue;
        }
        let turn = Turn {
            id: id.to_string(),
            role: kind.to_string(),
            text,
            timestamp: d.get("timestamp").and_then(Value::as_str).map(str::to_string),
        };
        if kind == "user" {
            out.extend(pending_answer.take());
            out.push(turn);
        } else {
            pending_answer = Some(turn);
        }
    }
    out.extend(pending_answer);
    out
}

/// Where the byte offset already read from a session's transcript is kept.
fn offset_file(session: &str) -> PathBuf {
    let base = crate::setup::config_path().parent().map(Path::to_path_buf).unwrap_or_default();
    let safe: String = session.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '-').collect();
    base.join("hooks").join("claude-code").join(format!("{safe}.offset"))
}

/// The complete lines appended to `path` since `offset`, and the offset after the last complete line.
fn read_new_lines(path: &Path, offset: u64) -> std::io::Result<(Vec<String>, u64)> {
    let mut f = std::fs::File::open(path)?;
    let len = f.metadata()?.len();
    // a transcript that shrank was rewritten: start over (message ids keep this idempotent)
    let start = if offset > len { 0 } else { offset };
    f.seek(SeekFrom::Start(start))?;
    let mut buf = String::new();
    f.read_to_string(&mut buf)?;
    // keep a half-written last line for next time
    let complete = buf.rfind('\n').map_or(0, |i| i + 1);
    let lines = buf[..complete].lines().map(str::to_string).collect();
    Ok((lines, start + complete as u64))
}

/// Queue the new turns of `transcript` as messages of chat `claude-code-<session>`. Returns how many were new.
pub fn ingest_transcript(
    cm: &ChatMemory,
    transcript: &Path,
    session: &str,
    cwd: Option<&str>,
    wait_for_reply: bool,
) -> crate::error::Result<usize> {
    let state = offset_file(session);
    let offset = std::fs::read_to_string(&state).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let (mut lines, mut next) = read_new_lines(transcript, offset)?;
    let mut turns = parse_transcript(lines.iter().map(String::as_str));
    // Claude Code can run the Stop hook a moment before it writes the reply to the transcript: when the newest turn
    // is still the user's, give the reply a second to land so it is not saved one turn late
    if wait_for_reply && turns.last().is_some_and(|t| t.role == "user") {
        for _ in 0..4 {
            std::thread::sleep(Duration::from_millis(250));
            let (l2, n2) = read_new_lines(transcript, offset)?;
            if n2 > next {
                (lines, next) = (l2, n2);
                turns = parse_transcript(lines.iter().map(String::as_str));
                if turns.last().is_some_and(|t| t.role == "assistant") {
                    break;
                }
            }
        }
    }
    let chat_id = format!("claude-code-{}", session.chars().take(8).collect::<String>());
    if !turns.is_empty() {
        let project = cwd.and_then(|c| Path::new(c).file_name()).map(|n| n.to_string_lossy().into_owned());
        let title = format!("Claude Code · {}", project.as_deref().unwrap_or("session"));
        cm.store.upsert_chat(
            &chat_id,
            Some(&title),
            Some(&serde_json::json!({"source": "claude-code", "session_id": session, "cwd": cwd})),
        )?;
    }
    let mut added = 0;
    for t in &turns {
        let speaker = if t.role == "user" { "user" } else { "Claude" };
        let input = MessageInput {
            role: t.role.clone(),
            sent_at: t.timestamp.clone(),
            message_id: Some(format!("cc-{}", t.id)),
            metadata: None,
        };
        if cm.add_message_checked(&chat_id, speaker, &t.text, input)?.1 {
            added += 1;
        }
    }
    if let Some(dir) = state.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(&state, next.to_string())?;
    Ok(added)
}

/// Prompts this short (or slash commands) get no recall: "ok", "continue", "/clear".
fn worth_recalling(prompt: &str) -> bool {
    let p = prompt.trim();
    p.chars().count() >= 12 && !p.starts_with('/')
}

/// Lowest cosine similarity (query vs memory) a memory needs to be added to a prompt. Measured with
/// nomic-embed-text on real data: on-topic memories score 0.58-0.82, unrelated ones 0.38-0.56.
/// `NEURALGRAPH_RECALL_MIN_SIM` overrides it (other embedding models spread their scores differently).
pub const RECALL_MIN_SIM: f64 = 0.57;
/// Most memories added to one prompt.
const RECALL_MAX: usize = 5;

fn recall_min_sim() -> f64 {
    std::env::var("NEURALGRAPH_RECALL_MIN_SIM").ok().and_then(|v| v.parse().ok()).unwrap_or(RECALL_MIN_SIM)
}

/// The memories worth adding to `prompt`: semantically close ones only. Keyword-only hits are left out (a shared
/// common word is not relevance), so nothing is recalled while the embedding model is unavailable.
pub fn pick_relevant(
    results: Vec<crate::models::RetrievedMemory>,
    min_sim: f64,
) -> Vec<crate::models::RetrievedMemory> {
    let mut hits: Vec<(f64, crate::models::RetrievedMemory)> = results
        .into_iter()
        .filter_map(|r| r.channels.get("vector").copied().filter(|s| *s >= min_sim).map(|s| (s, r)))
        .collect();
    hits.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    hits.into_iter().take(RECALL_MAX).map(|(_, r)| r).collect()
}

/// The block printed for UserPromptSubmit, or `None` when nothing relevant is stored.
pub async fn recall(cm: &ChatMemory, prompt: &str) -> Option<String> {
    if !worth_recalling(prompt) {
        return None;
    }
    // long prompts: the first part carries the topic, and embedding it is faster
    let query: String = prompt.chars().take(600).collect();
    let opts = SearchOptions { k: 12, with_sources: false, ..Default::default() };
    let results = tokio::time::timeout(Duration::from_secs(8), cm.search(&query, &opts)).await.ok()?.ok()?;
    let picked = pick_relevant(results, recall_min_sim());
    if picked.is_empty() {
        return None;
    }
    let lines: Vec<String> = picked
        .iter()
        .map(|r| {
            let when = r.memory.event_time.as_deref().unwrap_or(&r.memory.observed_at);
            format!("- {} ({})", r.memory.text, when.chars().take(10).collect::<String>())
        })
        .collect();
    Some(format!(
        "<neuralgraph-memory>\nWhat you know about the user from earlier chats (local NeuralGraph memory, recalled automatically; ignore anything unrelated):\n{}\n</neuralgraph-memory>",
        lines.join("\n")
    ))
}

/// Entry point for `neuralgraph-memory hook claude-code`. Always returns exit code 0.
pub async fn run_claude_code_hook(cm: &ChatMemory) -> i32 {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        return 0;
    }
    let Ok(payload) = serde_json::from_str::<Value>(&raw) else {
        eprintln!("neuralgraph-memory hook: stdin is not JSON");
        return 0;
    };
    let s = |k: &str| payload.get(k).and_then(Value::as_str);
    match s("hook_event_name").unwrap_or("") {
        "Stop" | "SessionEnd" | "PreCompact" => {
            if let (Some(path), Some(session)) = (s("transcript_path"), s("session_id")) {
                let stop = s("hook_event_name") == Some("Stop");
                if let Err(e) = ingest_transcript(cm, Path::new(path), session, s("cwd"), stop) {
                    eprintln!("neuralgraph-memory hook: could not save this turn: {e}");
                }
            }
        }
        "UserPromptSubmit" => {
            if let Some(block) = recall(cm, s("prompt").unwrap_or("")).await {
                println!("{block}");
            }
        }
        other => eprintln!("neuralgraph-memory hook: ignoring event {other:?}"),
    }
    0
}
