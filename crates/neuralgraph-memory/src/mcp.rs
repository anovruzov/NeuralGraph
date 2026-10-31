//! MCP (Model Context Protocol) server exposing chat memory to Claude.
//!
//! Two transports, one tool set:
//!
//! * **stdio** (edge / local): `neuralgraph-memory mcp --db ~/.neuralgraph/chat_memory.db`. Register it with Claude
//!   Code (`claude mcp add neuralgraph-memory -- neuralgraph-memory mcp`) or Claude Desktop
//!   (`claude_desktop_config.json` -> `mcpServers`). Runs next to a local Ollama/LM Studio.
//! * **Streamable HTTP** (cloud / remote): mounted at `/mcp` by the `serve` command next to the dashboard (see
//!   [`crate::server`]). Add it to claude.ai as a custom connector, to Claude Code with
//!   `claude mcp add --transport http`, or to the Messages API via the MCP connector. Optional bearer-token auth
//!   (`--mcp-token` / `NEURALGRAPH_MCP_TOKEN`).
//!
//! The protocol layer is a small JSON-RPC 2.0 implementation of the MCP server methods Claude clients use
//! (`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, `resources/list`,
//! `resources/read`, `prompts/list`). Protocol version negotiation follows the spec: the server answers with the
//! client's version when it is supported, otherwise with its latest. Tool names, schemas, result shapes and error
//! codes are unchanged from the Python server.

use crate::answer::AnswerOptions;
use crate::error::MemoryError;
use crate::pycompat::{
    dumps, fnum, obj, py_float_from_value, py_int_from_value, py_prefix, py_str, py_truthy, JsonFormat, PyIntError,
};
use crate::retrieval::SearchOptions;
use crate::service::{ChatMemory, MessageInput, RememberOptions};
use serde_json::{json, Map, Value};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::Mutex as AsyncMutex;

pub const SUPPORTED_PROTOCOL_VERSIONS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];
pub const LATEST_PROTOCOL_VERSION: &str = SUPPORTED_PROTOCOL_VERSIONS[0];
/// one JSON-RPC line may carry a whole document
pub const STDIO_LINE_LIMIT: usize = 64 * 1024 * 1024;
pub const MAX_HTTP_SESSIONS: usize = 10_000;

pub const JSONRPC_PARSE_ERROR: i64 = -32700;
pub const JSONRPC_INVALID_REQUEST: i64 = -32600;
pub const JSONRPC_METHOD_NOT_FOUND: i64 = -32601;
pub const JSONRPC_INVALID_PARAMS: i64 = -32602;
pub const JSONRPC_INTERNAL_ERROR: i64 = -32603;

pub fn server_info() -> Value {
    json!({"name": "neuralgraph-chat-memory", "version": "1.0.0"})
}

pub const INSTRUCTIONS: &str = "Long-term memory across chats. Call memory_context or memory_search before answering questions about the user's life, preferences, people, projects or past conversations; call memory_add_message to record conversation turns so the background worker can learn from them; memory_remember stores an explicit fact.";

fn schema(props: Value, required: &[&str]) -> Value {
    json!({"type": "object", "properties": props, "required": required, "additionalProperties": false})
}

/// The `tools/list` payload.
pub fn tools() -> Value {
    json!([
        {
            "name": "memory_search",
            "title": "Search memories",
            "description": "Search long-term memories collected across all past chats. Hybrid semantic + keyword + entity-graph retrieval. Returns the current version of each fact with provenance. Use it before answering anything about the user's life, preferences, history, people or projects.",
            "inputSchema": schema(json!({
                "query": {"type": "string", "description": "What to look for, in natural language."},
                "k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15},
                "subject": {"type": "string", "description": "Only memories about this person/entity (name)."},
                "chat_id": {"type": "string", "description": "Only memories first observed in this chat."},
                "kinds": {"type": "array", "items": {"type": "string"}, "description": "fact, preference, event, plan, relationship, opinion, identity, task, other"},
                "since": {"type": "string", "description": "ISO date lower bound on the memory's date."},
                "until": {"type": "string", "description": "ISO date upper bound on the memory's date."},
                "include_superseded": {"type": "boolean", "default": false, "description": "Also return the history of facts that were later updated."},
            }), &["query"]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_context",
            "title": "Memory context block",
            "description": "Return a compact block of the memories most relevant to a query, ready to paste into a prompt or read before replying. Empty string when nothing relevant is stored.",
            "inputSchema": schema(json!({
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 30, "default": 15},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 3600},
                "subject": {"type": "string"},
            }), &["query"]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_add_message",
            "title": "Record a chat message",
            "description": "Store one message of a conversation so the background worker (a local Qwen model) can turn it into memories and relationships. Returns immediately; extraction happens asynchronously. Use the same chat_id for all messages of one conversation.",
            "inputSchema": schema(json!({
                "chat_id": {"type": "string"},
                "speaker": {"type": "string", "description": "Who said it: the person's name, or 'user' / 'assistant'."},
                "text": {"type": "string"},
                "role": {"type": "string", "enum": ["user", "assistant", "system", ""], "default": ""},
                "sent_at": {"type": "string", "description": "ISO-8601 timestamp of the message (defaults to now)."},
                "message_id": {"type": "string", "description": "Optional stable id for idempotent re-sends."},
            }), &["chat_id", "speaker", "text"]),
            "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_remember",
            "title": "Remember a fact now",
            "description": "Store an explicit memory immediately (no extraction step), e.g. when the user says 'remember that ...'. The text should be one self-contained third-person sentence.",
            "inputSchema": schema(json!({
                "text": {"type": "string"},
                "subject": {"type": "string", "description": "Who/what the memory is about (defaults to 'user')."},
                "kind": {"type": "string", "default": "fact"},
                "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
                "chat_id": {"type": "string", "default": "mcp"},
                "when": {"type": "string", "description": "Date the memory is about (YYYY-MM-DD, YYYY-MM or YYYY)."},
            }), &["text"]),
            "annotations": {"readOnlyHint": false, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_profile",
            "title": "Profile of a person or entity",
            "description": "Everything currently known about a subject: memories grouped by kind and the entity's relations.",
            "inputSchema": schema(json!({"subject": {"type": "string"}, "limit": {"type": "integer", "default": 60, "minimum": 1, "maximum": 500}}), &["subject"]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_related",
            "title": "Related memories",
            "description": "Memories linked to a given memory id (supersedes, contradicts, related, elaborates) and its history.",
            "inputSchema": schema(json!({"memory_id": {"type": "string"}}), &["memory_id"]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_entities",
            "title": "Entity graph",
            "description": "Top entities by mentions, or the typed relations of one entity (1-hop neighbourhood).",
            "inputSchema": schema(json!({"entity": {"type": "string", "description": "Entity name; omit for the top list."},
                                         "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 500}}), &[]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_forget",
            "title": "Forget a memory",
            "description": "Retract a memory (and the relations grounded in it) at the user's request. Keeps an audit entry.",
            "inputSchema": schema(json!({"memory_id": {"type": "string"}, "reason": {"type": "string", "default": "user request"}}), &["memory_id"]),
            "annotations": {"readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_status",
            "title": "Memory system status",
            "description": "Counts, worker/queue state, token savings and the five-axis grade of the memory system.",
            "inputSchema": schema(json!({}), &[]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
        {
            "name": "memory_answer",
            "title": "Answer a question from memory",
            "description": "Answer a question from the stored memories with the measured reader protocol: the context block is built, count/duration/date questions are worked out step by step (a slower, thinking call), the rest are answered concisely, and a first answer of \"I don't know\" is retried with the compute prompt. Returns the answer text, whether it was routed to the compute prompt or cascaded, the context the reader saw, the number of model calls and the seconds taken. Slower than memory_context (one or more local-model calls); use memory_context when you will read the memories yourself.",
            "inputSchema": schema(json!({
                "question": {"type": "string"},
                "today": {"type": "string", "description": "The date the question is asked on (YYYY-MM-DD); defaults to today (UTC). Durations and relative dates are computed against it."},
            }), &["question"]),
            "annotations": {"readOnlyHint": true, "openWorldHint": false},
        },
    ])
}

/// How a tool call failed, mirroring the Python exception classes the protocol layer distinguished.
#[derive(Debug)]
pub enum ToolFailure {
    /// `ToolError`: the message is shown to the model as is.
    Tool(String),
    /// `TypeError`: bad or missing tool arguments.
    Type(String),
    /// Any other exception: `"<ExceptionName>: <message>"`.
    Other(String, String),
}

impl From<MemoryError> for ToolFailure {
    fn from(e: MemoryError) -> Self {
        ToolFailure::Other(e.type_name().to_string(), e.message())
    }
}

type ToolResult = std::result::Result<Value, ToolFailure>;

fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Keyword arguments of one tool call, checked against the Python signature.
struct Args<'a> {
    map: &'a Map<String, Value>,
}

impl<'a> Args<'a> {
    /// Reject unknown keywords and missing required ones with CPython's `TypeError` wording.
    fn bind(fname: &str, map: &'a Map<String, Value>, params: &[&str], required: &[&str]) -> Result<Self, ToolFailure> {
        let qual = format!("MemoryTools.{fname}()");
        if let Some(bad) = map.keys().find(|k| !params.contains(&k.as_str())) {
            return Err(ToolFailure::Type(format!("{qual} got an unexpected keyword argument '{bad}'")));
        }
        let missing: Vec<&&str> = required.iter().filter(|r| !map.contains_key(**r)).collect();
        if !missing.is_empty() {
            let names: Vec<String> = missing.iter().map(|m| format!("'{m}'")).collect();
            let list = match names.len() {
                1 => names[0].clone(),
                2 => format!("{} and {}", names[0], names[1]),
                _ => format!("{}, and {}", names[..names.len() - 1].join(", "), names[names.len() - 1]),
            };
            let noun = if missing.len() == 1 { "argument" } else { "arguments" };
            return Err(ToolFailure::Type(format!(
                "{qual} missing {} required positional {noun}: {list}",
                missing.len()
            )));
        }
        Ok(Args { map })
    }

    fn get(&self, key: &str) -> Option<&'a Value> {
        self.map.get(key)
    }

    /// `int(value)` with the default when the keyword is absent.
    fn int(&self, key: &str, default: i64) -> Result<i64, ToolFailure> {
        match self.get(key) {
            None => Ok(default),
            Some(v) => py_int_from_value(v).map_err(|e| match e {
                PyIntError::Value(m) => ToolFailure::Other("ValueError".into(), m),
                PyIntError::Type(m) => ToolFailure::Type(m),
            }),
        }
    }

    /// `float(value)` with the default when the keyword is absent.
    fn float(&self, key: &str, default: f64) -> Result<f64, ToolFailure> {
        match self.get(key) {
            None => Ok(default),
            Some(v) => py_float_from_value(v).ok_or_else(|| match v {
                Value::String(s) => ToolFailure::Other(
                    "ValueError".into(),
                    format!("could not convert string to float: {}", crate::pycompat::py_repr_str(s)),
                ),
                other => ToolFailure::Type(format!(
                    "float() argument must be a string or a real number, not '{}'",
                    py_type_name(other)
                )),
            }),
        }
    }

    /// A value that the Python code passed through `str()`-tolerant helpers (names, ids): falsy -> None.
    fn loose_str(&self, key: &str) -> Option<String> {
        match self.get(key) {
            None => None,
            Some(v) if !py_truthy(v) => None,
            Some(v) => Some(py_str(v)),
        }
    }

    /// A value the Python code called string methods on: non-strings that are truthy raise `AttributeError`.
    fn text(&self, key: &str, method: &str) -> Result<Option<String>, ToolFailure> {
        match self.get(key) {
            None => Ok(None),
            Some(Value::String(s)) => Ok(Some(s.clone())),
            Some(v) if !py_truthy(v) => Ok(None),
            Some(v) => Err(ToolFailure::Other(
                "AttributeError".into(),
                format!("'{}' object has no attribute '{method}'", py_type_name(v)),
            )),
        }
    }

    /// A date bound: Python took `len()` of it.
    fn bound(&self, key: &str) -> Result<Option<String>, ToolFailure> {
        match self.get(key) {
            None => Ok(None),
            Some(Value::String(s)) => Ok(Some(s.clone()).filter(|s| !s.is_empty())),
            Some(v) if !py_truthy(v) => Ok(None),
            Some(v) => Err(ToolFailure::Type(format!("object of type '{}' has no len()", py_type_name(v)))),
        }
    }
}

fn mem_brief(m: &Value) -> Value {
    const KEYS: [&str; 13] = [
        "memory_id",
        "text",
        "kind",
        "subject_name",
        "speaker",
        "chat_id",
        "importance",
        "confidence",
        "event_time",
        "observed_at",
        "status",
        "version",
        "source_message_ids",
    ];
    let mut out = Map::new();
    if let Value::Object(o) = m {
        for k in KEYS {
            if let Some(v) = o.get(k) {
                out.insert(k.to_string(), v.clone());
            }
        }
    }
    Value::Object(out)
}

/// Tool implementations over [`ChatMemory`].
pub struct MemoryTools {
    pub cm: Arc<ChatMemory>,
}

impl MemoryTools {
    pub async fn call(&self, name: &str, args: &Map<String, Value>) -> ToolResult {
        match name {
            "memory_search" => self.memory_search(args).await,
            "memory_context" => self.memory_context(args).await,
            "memory_add_message" => self.memory_add_message(args),
            "memory_remember" => self.memory_remember(args).await,
            "memory_profile" => self.memory_profile(args),
            "memory_related" => self.memory_related(args),
            "memory_entities" => self.memory_entities(args),
            "memory_forget" => self.memory_forget(args),
            "memory_status" => self.memory_status(args),
            "memory_answer" => self.memory_answer(args).await,
            other => Err(ToolFailure::Tool(format!("unknown tool: {other}"))),
        }
    }

    async fn memory_search(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind(
            "tool_memory_search",
            map,
            &["query", "k", "subject", "chat_id", "kinds", "since", "until", "include_superseded"],
            &["query"],
        )?;
        let query_v = a.get("query").cloned().unwrap_or(Value::Null);
        let k = a.int("k", 15)?;
        let query = a.text("query", "strip")?.unwrap_or_default();
        let kinds = match a.get("kinds") {
            None => None,
            Some(v) if !py_truthy(v) => None,
            Some(Value::Array(items)) => Some(items.iter().map(py_str).collect()),
            Some(Value::String(s)) => Some(s.chars().map(|c| c.to_string()).collect()),
            Some(Value::Object(o)) => Some(o.keys().cloned().collect()),
            Some(v) => return Err(ToolFailure::Type(format!("'{}' object is not iterable", py_type_name(v)))),
        };
        let opts = SearchOptions {
            k: k.max(0) as usize,
            subject: a.loose_str("subject"),
            chat_id: a.loose_str("chat_id"),
            kinds,
            since: a.bound("since")?,
            until: a.bound("until")?,
            include_superseded: a.get("include_superseded").is_some_and(py_truthy),
            ..Default::default()
        };
        let hits = self.cm.search(&query, &opts).await?;
        let results: Vec<Value> = hits
            .iter()
            .map(|h| {
                let mut b = mem_brief(&h.memory.to_value(false));
                let o = b.as_object_mut().expect("object");
                o.insert("score".into(), fnum(h.score));
                o.insert("why".into(), Value::String(h.explanation.clone()));
                o.insert("entities".into(), Value::from(h.entities.clone()));
                o.insert(
                    "sources".into(),
                    Value::Array(
                        h.sources
                            .iter()
                            .take(3)
                            .map(|s| {
                                obj([
                                    ("chat_id", Value::String(s.chat_id.clone())),
                                    ("speaker", Value::String(s.speaker.clone())),
                                    ("sent_at", s.sent_at.clone().map(Value::String).unwrap_or(Value::Null)),
                                    ("text", Value::String(py_prefix(&s.text, 300).to_string())),
                                ])
                            })
                            .collect(),
                    ),
                );
                b
            })
            .collect();
        Ok(obj([("query", query_v), ("count", Value::from(hits.len())), ("results", Value::Array(results))]))
    }

    async fn memory_context(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_context", map, &["query", "k", "max_chars", "subject"], &["query"])?;
        let query_v = a.get("query").cloned().unwrap_or(Value::Null);
        let k = a.int("k", 15)?;
        let max_chars = a.int("max_chars", 3600)?;
        let query = a.text("query", "strip")?.unwrap_or_default();
        let filters = SearchOptions { subject: a.loose_str("subject"), ..Default::default() };
        let block =
            self.cm.context_for(&query, Some(k.max(0) as usize), Some(max_chars.max(0) as usize), &filters).await?;
        Ok(obj([("query", query_v), ("empty", Value::Bool(block.is_empty())), ("context", Value::String(block))]))
            .map(|v| reorder(v, &["query", "context", "empty"]))
    }

    fn memory_add_message(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind(
            "tool_memory_add_message",
            map,
            &["chat_id", "speaker", "text", "role", "sent_at", "message_id"],
            &["chat_id", "speaker", "text"],
        )?;
        let chat_id = match a.get("chat_id") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => return Err(ToolFailure::Other("ValueError".into(), "chat_id must be a non-empty string".into())),
        };
        let speaker = match a.get("speaker") {
            Some(Value::String(s)) => s.clone(),
            Some(v) => {
                return Err(ToolFailure::Other(
                    "AttributeError".into(),
                    format!("'{}' object has no attribute 'lower'", py_type_name(v)),
                ))
            }
            None => String::new(),
        };
        let text = match a.get("text") {
            Some(Value::String(s)) => s.clone(),
            Some(v) => py_str(v),
            None => String::new(),
        };
        let input = MessageInput {
            role: a.loose_str("role").unwrap_or_default(),
            sent_at: a.text("sent_at", "encode")?,
            message_id: match a.get("message_id") {
                Some(Value::String(s)) => Some(s.clone()),
                Some(v) if py_truthy(v) => {
                    return Err(ToolFailure::Other(
                        "AttributeError".into(),
                        format!("'{}' object has no attribute 'encode'", py_type_name(v)),
                    ))
                }
                _ => None,
            },
            metadata: None,
        };
        let msg = self.cm.add_message(&chat_id, &speaker, &text, input)?;
        Ok(obj([
            ("message_id", Value::String(msg.message_id)),
            ("chat_id", Value::String(msg.chat_id)),
            ("seq", Value::from(msg.seq)),
            ("status", Value::String(msg.status)),
            ("note", Value::from("queued for background extraction")),
        ]))
    }

    async fn memory_remember(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind(
            "tool_memory_remember",
            map,
            &["text", "subject", "kind", "importance", "chat_id", "when"],
            &["text"],
        )?;
        let importance = a.float("importance", 0.8)?;
        let text = a.text("text", "strip")?.unwrap_or_default();
        let opts = RememberOptions {
            subject: a.loose_str("subject").unwrap_or_else(|| "user".into()),
            kind: match a.get("kind") {
                None => "fact".into(),
                Some(Value::String(s)) => s.clone(),
                Some(_) => "fact".into(),
            },
            importance,
            chat_id: match a.get("chat_id") {
                None => "mcp".into(),
                Some(v) => py_str(v),
            },
            when: a.get("when").filter(|v| !v.is_null()).map(py_str),
            ..Default::default()
        };
        let mem = self.cm.remember(&text, &opts).await?;
        Ok(obj([("memory", mem_brief(&mem.to_value(false)))]))
    }

    fn memory_profile(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_profile", map, &["subject", "limit"], &["subject"])?;
        let limit = a.int("limit", 60)?;
        let subject = a.get("subject").map(py_str_loose).unwrap_or_default();
        let mut prof = self.cm.profile(&subject, limit)?;
        brief_profile(&mut prof);
        Ok(prof)
    }

    fn memory_related(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_related", map, &["memory_id"], &["memory_id"])?;
        let memory_id = a.get("memory_id").map(py_str).unwrap_or_default();
        let Some(mem) = self.cm.store.get_memory(&memory_id, true)? else {
            return Err(ToolFailure::Tool(format!("no memory with id {memory_id}")));
        };
        let rel = self.cm.related(&memory_id)?;
        let hist = self.cm.store.history(&memory_id, 20)?;
        Ok(obj([
            ("memory", mem_brief(&mem.to_value(false))),
            (
                "links",
                Value::Array(
                    rel.iter()
                        .map(|r| {
                            obj([
                                ("link_type", r["link_type"].clone()),
                                ("weight", r["weight"].clone()),
                                ("direction", r["direction"].clone()),
                                ("memory", mem_brief(&r["memory"])),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("history", Value::Array(hist.iter().map(|h| mem_brief(&h.to_value(false))).collect())),
        ]))
    }

    fn memory_entities(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_entities", map, &["entity", "limit"], &[])?;
        if let Some(entity_v) = a.get("entity").filter(|v| py_truthy(v)) {
            let entity = py_str(entity_v);
            let Some(eid) = self.cm.store.resolve_alias(&entity)? else {
                return Ok(obj([
                    ("entity", entity_v.clone()),
                    ("found", Value::Bool(false)),
                    ("relations", Value::Array(vec![])),
                ]));
            };
            let ent = self.cm.store.get_entity(&eid)?;
            let limit = a.int("limit", 30)?;
            let rels = self.cm.store.relations_for(&eid, Some("active"), limit)?;
            return Ok(obj([
                ("entity", ent.map(|e| e.to_value()).unwrap_or_else(|| json!({"entity_id": eid}))),
                ("found", Value::Bool(true)),
                (
                    "relations",
                    Value::Array(
                        rels.iter()
                            .map(|r| {
                                obj([
                                    ("subject", Value::String(r.subject_id.clone())),
                                    ("predicate", Value::String(r.predicate.clone())),
                                    ("object", Value::String(r.object_id.clone())),
                                    ("confidence", fnum(r.confidence)),
                                    ("observations", Value::from(r.observation_count)),
                                    ("chat_id", Value::String(r.chat_id.clone())),
                                ])
                            })
                            .collect(),
                    ),
                ),
            ]));
        }
        let limit = a.int("limit", 30)?;
        let ents = self.cm.store.top_entities(limit, None)?;
        Ok(obj([("entities", Value::Array(ents.iter().map(|e| e.to_value()).collect()))]))
    }

    fn memory_forget(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_forget", map, &["memory_id", "reason"], &["memory_id"])?;
        let memory_id_v = a.get("memory_id").cloned().unwrap_or(Value::Null);
        let memory_id = py_str(&memory_id_v);
        let reason = match a.get("reason") {
            None => "user request".to_string(),
            Some(v) if !py_truthy(v) => String::new(),
            Some(v) => py_str(v),
        };
        let ok = self.cm.retract(&memory_id, &reason)?;
        Ok(obj([("memory_id", memory_id_v), ("retracted", Value::Bool(ok))]))
    }

    async fn memory_answer(&self, map: &Map<String, Value>) -> ToolResult {
        let a = Args::bind("tool_memory_answer", map, &["question", "today"], &["question"])?;
        let question = a.text("question", "strip")?.unwrap_or_default();
        let opts = AnswerOptions { today: a.loose_str("today"), ..Default::default() };
        let ans = self.cm.answer(question.trim(), &opts).await?;
        Ok(ans.to_value())
    }

    fn memory_status(&self, map: &Map<String, Value>) -> ToolResult {
        Args::bind("tool_memory_status", map, &[], &[])?;
        Ok(self.status_value()?)
    }

    pub fn status_value(&self) -> crate::error::Result<Value> {
        let st = self.cm.status()?;
        let mut out = Map::new();
        for k in ["chats", "messages", "memories", "entities", "relations", "links", "jobs", "tokens", "grade"] {
            out.insert(k.into(), st[k].clone());
        }
        let mut w = Map::new();
        for k in ["running", "busy_workers", "batches", "memories_added", "failures", "dead_jobs", "last_error"] {
            w.insert(k.into(), st["worker"][k].clone());
        }
        out.insert("worker".into(), Value::Object(w));
        Ok(Value::Object(out))
    }

    /// `memory://entities`: the default top list.
    pub fn entities_value(&self) -> ToolResult {
        self.memory_entities(&Map::new())
    }

    /// `memory://profile/{subject}`.
    pub fn profile_value(&self, subject: &str) -> ToolResult {
        let mut prof = self.cm.profile(subject, 60)?;
        brief_profile(&mut prof);
        Ok(prof)
    }
}

fn py_str_loose(v: &Value) -> String {
    if py_truthy(v) {
        py_str(v)
    } else {
        String::new()
    }
}

fn brief_profile(prof: &mut Value) {
    if let Some(Value::Object(by_kind)) = prof.get_mut("memories_by_kind") {
        for v in by_kind.values_mut() {
            if let Value::Array(items) = v {
                *items = items.iter().map(mem_brief).collect();
            }
        }
    }
}

/// Reorder an object's keys (shape parity with dict literals whose values were computed in another order).
fn reorder(v: Value, keys: &[&str]) -> Value {
    let Value::Object(mut m) = v else { return v };
    let mut out = Map::new();
    for k in keys {
        if let Some(x) = m.shift_remove(*k) {
            out.insert((*k).to_string(), x);
        }
    }
    for (k, x) in m {
        out.insert(k, x);
    }
    Value::Object(out)
}

// ---------------------------------------------------------------------------------------------
// JSON-RPC / MCP protocol core (transport-agnostic)
// ---------------------------------------------------------------------------------------------

pub struct McpProtocol {
    pub tools: MemoryTools,
    tool_defs: Value,
}

pub fn rpc_result(id: Value, result: Value) -> Value {
    obj([("jsonrpc", Value::from("2.0")), ("id", id), ("result", result)])
}

pub fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    obj([
        ("jsonrpc", Value::from("2.0")),
        ("id", id),
        ("error", obj([("code", Value::from(code)), ("message", Value::from(message))])),
    ])
}

/// A failure inside a method, mapped to a JSON-RPC error like the Python `except` chain did.
enum MethodFailure {
    Tool(String),
    Type(String),
    Other(String, String),
}

impl From<MemoryError> for MethodFailure {
    fn from(e: MemoryError) -> Self {
        MethodFailure::Other(e.type_name().to_string(), e.message())
    }
}

impl From<ToolFailure> for MethodFailure {
    fn from(e: ToolFailure) -> Self {
        match e {
            ToolFailure::Tool(m) => MethodFailure::Tool(m),
            ToolFailure::Type(m) => MethodFailure::Type(m),
            ToolFailure::Other(n, m) => MethodFailure::Other(n, m),
        }
    }
}

impl McpProtocol {
    pub fn new(cm: Arc<ChatMemory>) -> Self {
        McpProtocol { tools: MemoryTools { cm }, tool_defs: tools() }
    }

    /// Process one JSON-RPC message (request or notification). Returns a response or `None`.
    pub async fn handle(&self, message: &Value) -> Option<Value> {
        let Value::Object(msg) = message else {
            return Some(rpc_error(Value::Null, JSONRPC_INVALID_REQUEST, "invalid request"));
        };
        let raw_id = msg.get("id").cloned().unwrap_or(Value::Null);
        let method = match (msg.get("jsonrpc"), msg.get("method")) {
            (Some(Value::String(v)), Some(Value::String(m))) if v == "2.0" => m.clone(),
            _ => return Some(rpc_error(raw_id, JSONRPC_INVALID_REQUEST, "invalid request")),
        };
        let params = match msg.get("params") {
            None | Some(Value::Null) => Map::new(),
            Some(Value::Object(p)) => p.clone(),
            Some(_) => return Some(rpc_error(raw_id, JSONRPC_INVALID_PARAMS, "params must be an object")),
        };
        let id_ok = match &raw_id {
            Value::Null | Value::String(_) | Value::Bool(_) => true,
            Value::Number(n) => !n.is_f64(),
            _ => false,
        };
        if !id_ok {
            return Some(rpc_error(Value::Null, JSONRPC_INVALID_REQUEST, "id must be a string or number"));
        }
        let is_notification = !msg.contains_key("id");
        let outcome: Result<Value, MethodFailure> = match method.as_str() {
            "initialize" => Ok(self.initialize(&params)),
            m if m == "notifications/initialized" || m.starts_with("notifications/") => return None,
            "ping" => Ok(json!({})),
            "tools/list" => Ok(obj([("tools", self.tool_defs.clone())])),
            "tools/call" => self.tools_call(&params).await,
            "resources/list" => Ok(json!({"resources": [
                {"uri": "memory://status", "name": "Memory system status", "mimeType": "application/json",
                 "description": "Counts, queue, tokens saved and grade."},
                {"uri": "memory://entities", "name": "Top entities", "mimeType": "application/json"},
            ]})),
            "resources/read" => self.resources_read(&params).await,
            "resources/templates/list" => Ok(json!({"resourceTemplates": [
                {"uriTemplate": "memory://profile/{subject}", "name": "Subject profile", "mimeType": "application/json"}]})),
            "prompts/list" => Ok(
                json!({"prompts": [{"name": "recall", "description": "Recall what is known about a topic before answering.",
                                                     "arguments": [{"name": "topic", "required": true}]}]}),
            ),
            "prompts/get" => self.prompts_get(&params).await,
            "logging/setLevel" => Ok(json!({})),
            other => {
                if is_notification {
                    return None;
                }
                return Some(rpc_error(raw_id, JSONRPC_METHOD_NOT_FOUND, &format!("method not found: {other}")));
            }
        };
        let response = match outcome {
            Ok(result) => {
                if is_notification {
                    return None;
                }
                rpc_result(raw_id, result)
            }
            Err(MethodFailure::Tool(m)) => rpc_error(raw_id, JSONRPC_INVALID_PARAMS, &m),
            Err(MethodFailure::Type(m)) => rpc_error(raw_id, JSONRPC_INVALID_PARAMS, &format!("invalid params: {m}")),
            Err(MethodFailure::Other(name, m)) => {
                tracing::error!("MCP {method} failed: {name}: {m}");
                rpc_error(raw_id, JSONRPC_INTERNAL_ERROR, &format!("{name}: {m}"))
            }
        };
        Some(response)
    }

    fn initialize(&self, params: &Map<String, Value>) -> Value {
        let requested = match params.get("protocolVersion") {
            Some(v) if py_truthy(v) => py_str(v),
            _ => String::new(),
        };
        let version = if SUPPORTED_PROTOCOL_VERSIONS.contains(&requested.as_str()) {
            requested
        } else {
            LATEST_PROTOCOL_VERSION.to_string()
        };
        obj([
            ("protocolVersion", Value::String(version)),
            (
                "capabilities",
                json!({"tools": {"listChanged": false}, "resources": {"subscribe": false, "listChanged": false},
                       "prompts": {"listChanged": false}, "logging": {}}),
            ),
            ("serverInfo", server_info()),
            ("instructions", Value::from(INSTRUCTIONS)),
        ])
    }

    async fn tools_call(&self, params: &Map<String, Value>) -> Result<Value, MethodFailure> {
        let name = params.get("name");
        let known = |n: &str| self.tool_defs.as_array().is_some_and(|a| a.iter().any(|t| t["name"] == n));
        let name = match name {
            Some(Value::String(n)) if known(n) => n.clone(),
            other => {
                let shown = other.map(py_str).unwrap_or_else(|| "None".into());
                return Err(MethodFailure::Tool(format!("unknown tool: {shown}")));
            }
        };
        let args = match params.get("arguments") {
            None => Map::new(),
            Some(v) if !py_truthy(v) => Map::new(),
            Some(Value::Object(m)) => m.clone(),
            Some(_) => return Err(MethodFailure::Tool("arguments must be an object".into())),
        };
        let out = match self.tools.call(&name, &args).await {
            Ok(v) => v,
            Err(ToolFailure::Tool(m)) => return Ok(tool_error_result(&m)),
            Err(ToolFailure::Type(m)) => return Ok(tool_error_result(&format!("invalid arguments: {m}"))),
            Err(ToolFailure::Other(n, m)) => {
                tracing::error!("tool {name} failed: {n}: {m}");
                return Ok(tool_error_result(&format!("{n}: {m}")));
            }
        };
        let text = if name == "memory_context" {
            out["context"].as_str().unwrap_or("").to_string()
        } else {
            dumps(&out, &JsonFormat::pretty())
        };
        Ok(obj([
            ("content", json!([{"type": "text", "text": text}])),
            ("structuredContent", out),
            ("isError", Value::Bool(false)),
        ]))
    }

    async fn resources_read(&self, params: &Map<String, Value>) -> Result<Value, MethodFailure> {
        let uri = match params.get("uri") {
            Some(v) if py_truthy(v) => py_str(v),
            _ => String::new(),
        };
        let data = if uri == "memory://status" {
            self.tools.status_value()?
        } else if uri == "memory://entities" {
            self.tools.entities_value()?
        } else if let Some(subject) = uri.strip_prefix("memory://profile/") {
            self.tools.profile_value(subject)?
        } else {
            return Err(MethodFailure::Tool(format!("unknown resource: {uri}")));
        };
        Ok(
            json!({"contents": [{"uri": uri, "mimeType": "application/json", "text": dumps(&data, &JsonFormat::unicode())}]}),
        )
    }

    async fn prompts_get(&self, params: &Map<String, Value>) -> Result<Value, MethodFailure> {
        let topic = match params.get("arguments") {
            None => Value::from(""),
            Some(v) if !py_truthy(v) => Value::from(""),
            Some(Value::Object(m)) => m.get("topic").cloned().unwrap_or_else(|| Value::from("")),
            Some(v) => {
                return Err(MethodFailure::Other(
                    "AttributeError".into(),
                    format!("'{}' object has no attribute 'get'", py_type_name(v)),
                ))
            }
        };
        let block = if py_truthy(&topic) {
            let q = match &topic {
                Value::String(s) => s.clone(),
                v => {
                    return Err(MethodFailure::Other(
                        "AttributeError".into(),
                        format!("'{}' object has no attribute 'strip'", py_type_name(v)),
                    ))
                }
            };
            self.tools.cm.context_for(&q, None, None, &SearchOptions::default()).await?
        } else {
            String::new()
        };
        let shown = if block.is_empty() { "(nothing stored)".to_string() } else { block };
        let text =
            format!("Before answering about '{}', here is what long-term memory holds:\n{shown}", py_str(&topic));
        Ok(json!({"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}))
    }

    /// Handle a decoded payload that may be a single message or a batch. Returns the response payload or `None`.
    pub async fn handle_payload(&self, payload: &Value) -> Option<Value> {
        match payload {
            Value::Array(items) => {
                if items.is_empty() {
                    return Some(rpc_error(Value::Null, JSONRPC_INVALID_REQUEST, "empty batch"));
                }
                let responses: Vec<Value> = futures::future::join_all(items.iter().map(|m| self.handle(m)))
                    .await
                    .into_iter()
                    .flatten()
                    .collect();
                if responses.is_empty() {
                    None
                } else {
                    Some(Value::Array(responses))
                }
            }
            other => self.handle(other).await,
        }
    }
}

fn tool_error_result(text: &str) -> Value {
    json!({"content": [{"type": "text", "text": text}], "isError": true})
}

// ---------------------------------------------------------------------------------------------
// stdio transport (newline-delimited JSON-RPC over stdin/stdout)
// ---------------------------------------------------------------------------------------------

/// Reads one line (including the newline). `Ok(Err(()))` means the line exceeded `limit` and was discarded.
async fn read_line_limited<R: tokio::io::AsyncBufRead + Unpin>(
    reader: &mut R,
    limit: usize,
) -> std::io::Result<Option<Result<Vec<u8>, ()>>> {
    let mut line = Vec::new();
    let mut too_large = false;
    loop {
        let buf = reader.fill_buf().await?;
        if buf.is_empty() {
            return Ok(if line.is_empty() && !too_large {
                None
            } else if too_large {
                Some(Err(()))
            } else {
                Some(Ok(line))
            });
        }
        let (chunk, done) = match buf.iter().position(|b| *b == b'\n') {
            Some(i) => (&buf[..=i], true),
            None => (buf, false),
        };
        let n = chunk.len();
        if !too_large {
            line.extend_from_slice(chunk);
            if line.len() > limit {
                too_large = true;
                line = Vec::new();
            }
        }
        reader.consume(n);
        if done {
            return Ok(Some(if too_large { Err(()) } else { Ok(line) }));
        }
    }
}

/// Run the MCP server over the given streams until EOF. The worker keeps running in the background meanwhile.
pub async fn serve_stdio_with<R, W>(cm: Arc<ChatMemory>, reader: R, writer: W, line_limit: usize) -> std::io::Result<()>
where
    R: tokio::io::AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
{
    let proto = Arc::new(McpProtocol::new(cm));
    let writer = Arc::new(AsyncMutex::new(writer));
    let emit = |writer: Arc<AsyncMutex<W>>, v: Value| async move {
        let mut data = dumps(&v, &JsonFormat::compact());
        data.push('\n');
        let mut w = writer.lock().await;
        let _ = w.write_all(data.as_bytes()).await;
        let _ = w.flush().await;
    };
    let mut reader = tokio::io::BufReader::new(reader);
    let mut pending = tokio::task::JoinSet::new();
    loop {
        let line = match read_line_limited(&mut reader, line_limit).await {
            Ok(Some(l)) => l,
            Ok(None) => break,
            Err(_) => break,
        };
        let line = match line {
            Ok(l) => l,
            Err(()) => {
                // a single message larger than the buffer: answer with a parse error instead of dying
                emit(writer.clone(), rpc_error(Value::Null, JSONRPC_PARSE_ERROR, "message too large")).await;
                continue;
            }
        };
        if line.iter().all(|b| b.is_ascii_whitespace()) {
            continue;
        }
        let proto = proto.clone();
        let writer = writer.clone();
        pending.spawn(async move {
            let payload: Option<Value> = std::str::from_utf8(&line).ok().and_then(|s| serde_json::from_str(s).ok());
            let response = match payload {
                None => Some(rpc_error(Value::Null, JSONRPC_PARSE_ERROR, "parse error")),
                Some(p) => proto.handle_payload(&p).await,
            };
            if let Some(r) = response {
                emit(writer, r).await;
            }
        });
    }
    while pending.join_next().await.is_some() {}
    Ok(())
}

/// `serve_stdio` on the process's stdin/stdout.
pub async fn serve_stdio(cm: Arc<ChatMemory>) -> std::io::Result<()> {
    serve_stdio_with(cm, tokio::io::stdin(), tokio::io::stdout(), STDIO_LINE_LIMIT).await
}

#[allow(dead_code)]
fn _assert_bufread<R: AsyncBufReadExt>() {}
