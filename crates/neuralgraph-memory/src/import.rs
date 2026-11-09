//! Import: bring existing history into memory.
//!
//! Every source becomes a list of conversations ([`ImportPlan`]); the user previews it and picks what to keep; the
//! picked conversations are queued as chat messages and the worker learns them like any chat. Sources:
//!
//! - Claude.ai and ChatGPT data exports (the `.zip`, or the `conversations.json` inside)
//! - Claude Code sessions on this machine (`~/.claude/projects/*/*.jsonl`) and single transcript files
//! - notes and documents: Markdown, plain text, PDF (also a `.zip` of Markdown notes, e.g. an Obsidian vault)
//! - screenshots and photos: a vision model on the user's model server transcribes them first
//! - pasted text
//!
//! Imported chats are named `import-…` (Claude Code sessions keep their `claude-code-…` name), so the queue reads them
//! in big batches with the "durable facts only" rules. Every message carries a stable id derived from its source,
//! so importing the same file twice stores nothing twice.

use crate::error::{MemoryError, Result};
use crate::hook::{clean_text, parse_transcript};
use crate::service::{ChatMemory, MessageInput};
use base64::Engine;
use serde_json::{json, Value};
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};

/// Chats created by an import start with this (see `store::digest_kind`).
pub const IMPORT_CHAT_PREFIX: &str = "import-";
/// Characters per section when a document is split up: small enough that one model call (at most 12 memories) can
/// record every fact in it.
const DOC_CHUNK: usize = 900;
/// Largest upload the import endpoint accepts.
pub const MAX_UPLOAD: usize = 512 * 1024 * 1024;
/// Media type of an upload: files framed as `[u32 name length][name][u64 size][bytes]…`, big-endian. A browser can
/// send it to its own server, while another site cannot without a CORS preflight.
pub const UPLOAD_MEDIA_TYPE: &str = "application/x-neuralgraph-files";

/// Split an upload body into `(file name, bytes)`.
pub fn unframe(mut body: &[u8]) -> std::result::Result<Vec<(String, Vec<u8>)>, String> {
    fn take<'a>(b: &mut &'a [u8], n: usize) -> std::result::Result<&'a [u8], String> {
        if b.len() < n {
            return Err("the upload is cut short".into());
        }
        let (head, rest) = b.split_at(n);
        *b = rest;
        Ok(head)
    }
    let mut out = Vec::new();
    while !body.is_empty() {
        let n = u32::from_be_bytes(take(&mut body, 4)?.try_into().expect("4 bytes")) as usize;
        let name = String::from_utf8(take(&mut body, n)?.to_vec()).map_err(|_| "a file name is not UTF-8")?;
        let size = u64::from_be_bytes(take(&mut body, 8)?.try_into().expect("8 bytes")) as usize;
        out.push((name, take(&mut body, size)?.to_vec()));
    }
    Ok(out)
}

#[derive(Clone, Debug)]
pub struct ImportMsg {
    /// stable id: re-importing the same source skips messages already stored
    pub id: String,
    /// "user" or "assistant"
    pub role: String,
    pub text: String,
    pub at: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ImportConv {
    /// the chat id the messages go to
    pub id: String,
    pub title: String,
    pub created_at: Option<String>,
    pub messages: Vec<ImportMsg>,
    /// speaker name for assistant turns ("Claude", "ChatGPT")
    pub assistant: String,
}

#[derive(Clone, Debug, Default)]
pub struct ImportPlan {
    /// "claude", "chatgpt", "claude-code", "document", "image", "text"
    pub source: String,
    pub label: String,
    pub conversations: Vec<ImportConv>,
    pub warnings: Vec<String>,
}

fn short_hash(data: &[u8]) -> String {
    crate::textutil::content_hash(&[&String::from_utf8_lossy(data)]).chars().take(10).collect()
}

fn slug(s: &str, n: usize) -> String {
    s.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '-').take(n).collect()
}

fn epoch_iso(v: &Value) -> Option<String> {
    let secs = v.as_f64()?;
    chrono::DateTime::from_timestamp(secs as i64, 0).map(|d| d.to_rfc3339())
}

/// A message's text cleaned like a Claude Code turn (harness wrappers and code blocks out, long text cut).
fn msg(id: String, role: &str, raw: &str, at: Option<String>) -> Option<ImportMsg> {
    let text = clean_text(raw);
    (!text.is_empty()).then(|| ImportMsg { id, role: role.to_string(), text, at })
}

// ------------------------------------------------------------------------------------------------ chat exports

/// Claude.ai export `conversations.json`: `[{uuid, name, created_at, chat_messages: [{uuid, sender, text, content,
/// created_at}]}]`.
pub fn parse_claude_export(v: &Value) -> Vec<ImportConv> {
    let empty = Vec::new();
    v.as_array()
        .unwrap_or(&empty)
        .iter()
        .filter_map(|c| {
            let uuid = c.get("uuid")?.as_str()?;
            let messages: Vec<ImportMsg> = c
                .get("chat_messages")?
                .as_array()?
                .iter()
                .filter_map(|m| {
                    let role = match m.get("sender")?.as_str()? {
                        "human" => "user",
                        "assistant" => "assistant",
                        _ => return None,
                    };
                    let mut text = m.get("text").and_then(Value::as_str).unwrap_or("").to_string();
                    if text.trim().is_empty() {
                        text = m
                            .get("content")
                            .and_then(Value::as_array)
                            .map(|blocks| {
                                blocks
                                    .iter()
                                    .filter(|b| b.get("type").and_then(Value::as_str) == Some("text"))
                                    .filter_map(|b| b.get("text").and_then(Value::as_str))
                                    .collect::<Vec<_>>()
                                    .join("\n")
                            })
                            .unwrap_or_default();
                    }
                    let id = m.get("uuid").and_then(Value::as_str)?;
                    let at = m.get("created_at").and_then(Value::as_str).map(str::to_string);
                    msg(format!("imp-claude-{id}"), role, &text, at)
                })
                .collect();
            let name =
                c.get("name").and_then(Value::as_str).filter(|n| !n.trim().is_empty()).unwrap_or("Untitled chat");
            Some(ImportConv {
                id: format!("{IMPORT_CHAT_PREFIX}claude-{}", slug(uuid, 8)),
                title: format!("Claude · {name}"),
                created_at: c.get("created_at").and_then(Value::as_str).map(str::to_string),
                messages,
                assistant: "Claude".into(),
            })
        })
        .filter(|c| !c.messages.is_empty())
        .collect()
}

/// ChatGPT export `conversations.json`: `[{id, title, create_time, mapping: {node: {message, parent}}, current_node}]`.
/// Only the branch the user ended on is read (edits and regenerations leave dead branches in `mapping`).
pub fn parse_chatgpt_export(v: &Value) -> Vec<ImportConv> {
    let empty = Vec::new();
    v.as_array()
        .unwrap_or(&empty)
        .iter()
        .filter_map(|c| {
            let mapping = c.get("mapping")?.as_object()?;
            let conv_id = c
                .get("conversation_id")
                .or_else(|| c.get("id"))
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| short_hash(c.to_string().as_bytes()));
            let mut node = c.get("current_node").and_then(Value::as_str).map(str::to_string);
            let mut chain = Vec::new();
            let mut guard = 0;
            while let Some(id) = node.take() {
                guard += 1;
                if guard > 100_000 {
                    break;
                }
                let Some(n) = mapping.get(&id) else { break };
                chain.push((id.clone(), n.clone()));
                node = n.get("parent").and_then(Value::as_str).map(str::to_string);
            }
            chain.reverse();
            let messages: Vec<ImportMsg> = chain
                .iter()
                .filter_map(|(id, n)| {
                    let m = n.get("message")?;
                    let role = match m.get("author")?.get("role")?.as_str()? {
                        "user" => "user",
                        "assistant" => "assistant",
                        _ => return None,
                    };
                    let content = m.get("content")?;
                    let text: String = content
                        .get("parts")
                        .and_then(Value::as_array)
                        .map(|parts| parts.iter().filter_map(Value::as_str).collect::<Vec<_>>().join("\n"))
                        .unwrap_or_default();
                    msg(format!("imp-chatgpt-{id}"), role, &text, m.get("create_time").and_then(epoch_iso))
                })
                .collect();
            let title =
                c.get("title").and_then(Value::as_str).filter(|t| !t.trim().is_empty()).unwrap_or("Untitled chat");
            Some(ImportConv {
                id: format!("{IMPORT_CHAT_PREFIX}chatgpt-{}", slug(&conv_id, 8)),
                title: format!("ChatGPT · {title}"),
                created_at: c.get("create_time").and_then(epoch_iso),
                messages,
                assistant: "ChatGPT".into(),
            })
        })
        .filter(|c| !c.messages.is_empty())
        .collect()
}

/// Which export a `conversations.json` is.
fn parse_conversations_json(bytes: &[u8]) -> std::result::Result<ImportPlan, String> {
    let v: Value = serde_json::from_slice(bytes).map_err(|e| format!("conversations.json is not valid JSON: {e}"))?;
    let first = v.as_array().and_then(|a| a.first()).cloned().unwrap_or(Value::Null);
    if first.get("chat_messages").is_some() {
        let convs = parse_claude_export(&v);
        Ok(ImportPlan {
            source: "claude".into(),
            label: "Claude.ai history".into(),
            conversations: convs,
            ..Default::default()
        })
    } else if first.get("mapping").is_some() {
        let convs = parse_chatgpt_export(&v);
        Ok(ImportPlan {
            source: "chatgpt".into(),
            label: "ChatGPT history".into(),
            conversations: convs,
            ..Default::default()
        })
    } else if v.as_array().is_some_and(Vec::is_empty) {
        Ok(ImportPlan { source: "claude".into(), label: "Empty export".into(), ..Default::default() })
    } else {
        Err("this JSON is not a Claude.ai or ChatGPT conversations export".into())
    }
}

// ------------------------------------------------------------------------------------------------ documents

/// Chats made from documents (notes, PDFs, screenshots, pasted text): `import-doc-…`.
pub const DOC_CHAT_PREFIX: &str = "import-doc-";

/// Split a document into sections of about [`DOC_CHUNK`] characters. A new section starts at a Markdown heading (once
/// the current one has some body) or when the size is reached, always at a line break; every section opens with
/// `[title › heading › subheading]` so a fact keeps its context ("Cameras" under "Photography").
pub fn chunk_document(title: &str, text: &str) -> Vec<String> {
    let mut path: Vec<(usize, String)> = Vec::new();
    let mut chunks: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut cur_path = String::new();
    let label = |path: &[(usize, String)]| {
        let mut parts = vec![title.to_string()];
        parts.extend(path.iter().map(|(_, h)| h.clone()));
        format!("[{}]", parts.join(" › "))
    };
    let flush = |cur: &mut String, cur_path: &str, chunks: &mut Vec<String>| {
        let body = cur.trim();
        // a section that is only headings says nothing
        if body.lines().any(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#')) {
            chunks.push(format!("{cur_path}\n{body}"));
        }
        cur.clear();
    };
    for raw in text.lines() {
        let line = raw.trim_end();
        let heading = line.trim_start().strip_prefix('#').map(|rest| {
            let level = 1 + rest.chars().take_while(|c| *c == '#').count();
            (level, rest.trim_start_matches('#').trim().to_string())
        });
        if let Some((level, name)) = heading.filter(|(l, n)| *l <= 4 && !n.is_empty()) {
            if cur.len() >= 300 {
                flush(&mut cur, &cur_path, &mut chunks);
            }
            path.retain(|(l, _)| *l < level);
            path.push((level, name));
            if cur.is_empty() {
                cur_path = label(&path);
            }
            cur.push_str(line);
            cur.push('\n');
            continue;
        }
        // very long lines (a PDF paragraph) are cut at sentence ends
        let mut rest = line;
        loop {
            if cur.is_empty() {
                cur_path = label(&path);
            }
            let room = DOC_CHUNK.saturating_sub(cur.len());
            if rest.len() <= room.max(200) {
                cur.push_str(rest);
                cur.push('\n');
                break;
            }
            if cur.len() >= 300 {
                flush(&mut cur, &cur_path, &mut chunks);
                continue;
            }
            let mut cut =
                rest.char_indices().map(|(i, _)| i).take_while(|i| *i <= DOC_CHUNK).last().unwrap_or(rest.len());
            if let Some(dot) = rest[..cut].rfind(". ") {
                if dot > DOC_CHUNK / 3 {
                    cut = dot + 1;
                }
            }
            cur.push_str(&rest[..cut]);
            flush(&mut cur, &cur_path, &mut chunks);
            rest = rest[cut..].trim_start();
            if rest.is_empty() {
                break;
            }
        }
        if cur.len() >= DOC_CHUNK {
            flush(&mut cur, &cur_path, &mut chunks);
        }
    }
    flush(&mut cur, &cur_path, &mut chunks);
    chunks
}

/// A document as one conversation in the user's own words, one message per section.
pub fn document(title: &str, text: &str, key: &[u8]) -> Option<ImportConv> {
    // "s2": the second sectioning scheme, so files imported by 1.5.0 (ten big parts in one call) can be read again
    let hash = short_hash(&[key, b"s2"].concat());
    let messages: Vec<ImportMsg> = chunk_document(title, text)
        .into_iter()
        .enumerate()
        .map(|(i, text)| ImportMsg { id: format!("imp-doc-{hash}-{i}"), role: "user".into(), text, at: None })
        .collect();
    (!messages.is_empty()).then(|| ImportConv {
        id: format!("{DOC_CHAT_PREFIX}{hash}"),
        title: title.to_string(),
        created_at: None,
        messages,
        assistant: "assistant".into(),
    })
}

fn pdf_text(bytes: &[u8]) -> std::result::Result<String, String> {
    pdf_extract::extract_text_from_mem(bytes).map_err(|e| format!("could not read the PDF: {e}"))
}

// ------------------------------------------------------------------------------------------------ Claude Code

fn claude_projects_dir() -> PathBuf {
    std::env::var_os("CLAUDE_CONFIG_DIR")
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".claude"))
        .join("projects")
}

/// One Claude Code transcript file as a conversation, under the same chat id and message ids the hook uses, so
/// sessions the hook already saved are not stored twice.
pub fn claude_code_session(path: &Path, raw: &str) -> Option<ImportConv> {
    let session = path.file_stem()?.to_string_lossy().to_string();
    let turns = parse_transcript(raw.lines());
    if turns.len() < 2 {
        return None;
    }
    let cwd = raw
        .lines()
        .take(50)
        .find_map(|l| serde_json::from_str::<Value>(l).ok()?.get("cwd")?.as_str().map(str::to_string));
    let project = cwd.as_deref().and_then(|c| Path::new(c).file_name()).map(|n| n.to_string_lossy().into_owned());
    let first_at = turns.iter().find_map(|t| t.timestamp.clone());
    Some(ImportConv {
        id: format!("claude-code-{}", session.chars().take(8).collect::<String>()),
        title: format!("Claude Code · {}", project.as_deref().unwrap_or("session")),
        created_at: first_at,
        messages: turns
            .into_iter()
            .map(|t| ImportMsg { id: format!("cc-{}", t.id), role: t.role, text: t.text, at: t.timestamp })
            .collect(),
        assistant: "Claude".into(),
    })
}

/// Every Claude Code session on this machine.
pub fn scan_claude_code() -> ImportPlan {
    let mut convs = Vec::new();
    let mut warnings = Vec::new();
    let dir = claude_projects_dir();
    let projects = std::fs::read_dir(&dir).map(|r| r.flatten().collect::<Vec<_>>()).unwrap_or_default();
    for p in projects {
        let files = std::fs::read_dir(p.path()).map(|r| r.flatten().collect::<Vec<_>>()).unwrap_or_default();
        for f in files {
            let path = f.path();
            if path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
                continue;
            }
            match std::fs::read_to_string(&path) {
                Ok(raw) => convs.extend(claude_code_session(&path, &raw)),
                Err(e) => warnings.push(format!("skipped {}: {e}", path.display())),
            }
        }
    }
    convs.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    let label = if convs.is_empty() {
        format!("No Claude Code sessions found in {}", dir.display())
    } else {
        "Claude Code sessions on this Mac".into()
    };
    ImportPlan { source: "claude-code".into(), label, conversations: convs, warnings }
}

// ------------------------------------------------------------------------------------------------ uploads

fn ext(name: &str) -> String {
    Path::new(name).extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase()
}

pub fn is_image(name: &str) -> bool {
    matches!(ext(name).as_str(), "png" | "jpg" | "jpeg" | "webp" | "gif" | "heic")
}

/// Turn an uploaded file into a plan (images go through [`transcribe_image`] first).
pub fn parse_upload(name: &str, bytes: &[u8]) -> std::result::Result<ImportPlan, String> {
    let doc_plan = |label: &str, convs: Vec<ImportConv>| ImportPlan {
        source: "document".into(),
        label: label.to_string(),
        conversations: convs,
        ..Default::default()
    };
    match ext(name).as_str() {
        "zip" => parse_zip(name, bytes),
        "json" => parse_conversations_json(bytes),
        "jsonl" => {
            let raw = String::from_utf8_lossy(bytes);
            let conv = claude_code_session(Path::new(name), &raw).ok_or("no conversation turns in this transcript")?;
            Ok(ImportPlan { source: "claude-code".into(), label: "Claude Code session".into(), conversations: vec![conv], ..Default::default() })
        }
        "md" | "markdown" | "txt" | "text" | "org" | "rst" => {
            let text = String::from_utf8_lossy(bytes);
            Ok(doc_plan("Document", document(name, &text, bytes).into_iter().collect()))
        }
        "pdf" => {
            let text = pdf_text(bytes)?;
            if text.trim().is_empty() {
                return Err("this PDF has no text layer (a scan?): export its pages as images and import those".into());
            }
            Ok(doc_plan("PDF", document(name, &text, bytes).into_iter().collect()))
        }
        e => Err(format!(
            "can't import .{e} files yet: use a Claude.ai or ChatGPT export (.zip), .md, .txt, .pdf, a Claude Code .jsonl, or an image"
        )),
    }
}

/// An export `.zip` (Claude.ai, ChatGPT) or a `.zip` of notes (Markdown / text / PDF files).
fn parse_zip(name: &str, bytes: &[u8]) -> std::result::Result<ImportPlan, String> {
    let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).map_err(|e| format!("not a readable zip: {e}"))?;
    let names: Vec<String> = (0..zip.len())
        .filter_map(|i| zip.by_index(i).ok().and_then(|f| f.name().ok().map(|n| n.to_string())))
        .collect();
    let read = |zip: &mut zip::ZipArchive<Cursor<&[u8]>>, n: &str| -> std::result::Result<Vec<u8>, String> {
        let mut f = zip.by_name(n).map_err(|e| e.to_string())?;
        let mut buf = Vec::new();
        f.read_to_end(&mut buf).map_err(|e| e.to_string())?;
        Ok(buf)
    };
    if let Some(conv) = names.iter().find(|n| n.ends_with("conversations.json")) {
        let data = read(&mut zip, conv)?;
        return parse_conversations_json(&data);
    }
    // a folder of notes
    let mut convs = Vec::new();
    let mut warnings = Vec::new();
    for n in names.iter().filter(|n| !n.ends_with('/') && !n.contains("__MACOSX")) {
        let e = ext(n);
        if !matches!(e.as_str(), "md" | "markdown" | "txt" | "pdf") {
            continue;
        }
        let data = read(&mut zip, n)?;
        let text = if e == "pdf" {
            match pdf_text(&data) {
                Ok(t) => t,
                Err(err) => {
                    warnings.push(format!("{n}: {err}"));
                    continue;
                }
            }
        } else {
            String::from_utf8_lossy(&data).into_owned()
        };
        let title = Path::new(n).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_else(|| n.clone());
        convs.extend(document(&title, &text, &data));
    }
    if convs.is_empty() {
        return Err(format!("{name} has no conversations.json and no notes (.md, .txt, .pdf) inside"));
    }
    Ok(ImportPlan { source: "document".into(), label: "Notes".into(), conversations: convs, warnings })
}

/// Ask a vision-capable model on the user's model server to write out everything an image says.
pub async fn transcribe_image(base: &str, name: &str, bytes: &[u8], model: &str) -> Result<String> {
    let base = base.trim_end_matches('/');
    let mime = match ext(name).as_str() {
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "heic" => "image/heic",
        _ => "image/png",
    };
    let b64 = base64::engine::general_purpose::STANDARD.encode(bytes);
    let prompt = "Write out all the text in this image exactly as it appears, top to bottom. If it shows a chat, \
                  put each message on its own line as `Name: message`. Then add one short paragraph describing \
                  anything important in the image that is not text. Output only that, no commentary.";
    let http = reqwest::Client::new();
    let ollama = crate::backend::backend_for(base) == "ollama";
    let (url, body) = if ollama {
        (format!("{base}/api/generate"), json!({"model": model, "prompt": prompt, "images": [b64], "stream": false}))
    } else {
        (
            format!("{base}/v1/chat/completions"),
            json!({"model": model, "temperature": 0, "max_tokens": 2000, "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": format!("data:{mime};base64,{b64}")}},
            ]}]}),
        )
    };
    let resp = http
        .post(url)
        .json(&body)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(|e| MemoryError::Other("ImageError", format!("could not reach the model server: {e}")))?;
    let status = resp.status();
    let v: Value = resp.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        let why = v.pointer("/error/message").or_else(|| v.get("error")).map(|e| e.to_string()).unwrap_or_default();
        return Err(MemoryError::Other(
            "ImageError",
            format!(
                "{model} could not read the image (HTTP {}): {why}. Pick a vision model such as qwen3-vl.",
                status.as_u16()
            ),
        ));
    }
    let text = if ollama {
        v.get("response").and_then(Value::as_str).unwrap_or("").to_string()
    } else {
        v.pointer("/choices/0/message/content").and_then(Value::as_str).unwrap_or("").to_string()
    };
    let text = crate::backend::strip_thinking(&text);
    if text.trim().is_empty() {
        return Err(MemoryError::Other("ImageError", format!("{model} returned no text for this image")));
    }
    Ok(text)
}

// ------------------------------------------------------------------------------------------------ preview + commit

/// Model calls to learn one conversation: documents one section per call, chats 20 messages per call.
pub fn calls_for(c: &ImportConv) -> usize {
    c.messages.len().div_ceil(crate::store::batch_for(&c.id).unwrap_or(2) as usize)
}

/// Model calls to learn `convs`.
pub fn estimate_calls(convs: &[&ImportConv]) -> usize {
    convs.iter().map(|c| calls_for(c)).sum()
}

/// What the preview shows: every conversation with its size and whether it is already in memory.
pub fn preview(cm: &ChatMemory, plan: &ImportPlan, token: &str) -> Value {
    let known: std::collections::HashSet<String> = cm
        .store
        .list_chats(100_000, 0)
        .unwrap_or_default()
        .into_iter()
        .filter_map(|c| c.get("chat_id").and_then(Value::as_str).map(str::to_string))
        .collect();
    let all: Vec<&ImportConv> = plan.conversations.iter().collect();
    let messages: usize = all.iter().map(|c| c.messages.len()).sum();
    json!({
        "token": token,
        "source": plan.source,
        "label": plan.label,
        "warnings": plan.warnings,
        "messages": messages,
        "calls": estimate_calls(&all),
        "conversations": plan.conversations.iter().map(|c| json!({
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at,
            "messages": c.messages.len(),
            "calls": calls_for(c),
            "document": c.id.starts_with(DOC_CHAT_PREFIX),
            "chars": c.messages.iter().map(|m| m.text.len()).sum::<usize>(),
            "imported": known.contains(&c.id),
        })).collect::<Vec<_>>(),
    })
}

/// Queue the chosen conversations (all when `ids` is empty). Returns (chats, new messages, messages already stored).
pub fn commit(cm: &ChatMemory, plan: &ImportPlan, ids: &[String]) -> Result<(usize, usize, usize)> {
    let (mut chats, mut added, mut skipped) = (0, 0, 0);
    for c in plan.conversations.iter().filter(|c| ids.is_empty() || ids.contains(&c.id)) {
        cm.store.upsert_chat(&c.id, Some(&c.title), Some(&json!({"source": format!("import:{}", plan.source)})))?;
        chats += 1;
        for m in &c.messages {
            let speaker = if m.role == "user" { "user" } else { c.assistant.as_str() };
            let input = MessageInput {
                role: m.role.clone(),
                sent_at: m.at.clone(),
                message_id: Some(m.id.clone()),
                metadata: None,
            };
            if cm.add_message_checked(&c.id, speaker, &m.text, input)?.1 {
                added += 1;
            } else {
                skipped += 1;
            }
        }
    }
    Ok((chats, added, skipped))
}
