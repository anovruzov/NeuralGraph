//! HTTP application: live dashboard, JSON API, and the MCP Streamable HTTP endpoint.
//!
//! Routes
//! ------
//! GET  /                      dashboard (single self-contained HTML page, no external assets)
//! GET  /healthz               liveness
//! GET  /api/status            everything the dashboard shows (auto-polled)
//! GET  /api/search?q=..&k=..  hybrid search across chats
//! GET  /api/context?q=..      prompt-ready context block
//! POST /api/answer {question, today?}  answer from memory (routed reader protocol)
//! GET  /api/memories          recent memories (?subject=&kind=&limit=)
//! GET  /api/entities          top entities (?limit=)
//! GET  /graph                 graph explorer page (force layout, local graph, time-lapse; embedded in /)
//! GET  /api/graph             entity graph: {nodes, edges, chats} (?limit=&chat_id=)
//! GET  /api/profile/{subject}
//! POST /api/messages          {"chat_id", "speaker", "text", "role"?, "sent_at"?, "message_id"?}  -> queued
//! POST /api/messages/batch    {"chat_id", "messages": [...]}
//! POST /api/remember          {"text", "subject"?, "kind"?, "importance"?, "when"?}
//! POST /api/forget            {"memory_id", "reason"?}
//! POST /api/maintain          run one maintenance pass now
//! GET  /api/control           {paused, models}; POST {action: "pause"|"resume"} stops or restarts all model use
//! GET  /api/settings          saved settings, model servers and their models, tools and their connection state
//! POST /api/settings          {llm_base_url?, llm_model?, embed_model?, user_name?}: save + switch models live
//! POST /api/settings/test     {llm_base_url, llm_model, embed_model}: one chat and one embedding call
//! POST /api/connect           {client}: register the MCP server with Claude Code / Codex / Claude Desktop / Cursor
//! POST /api/auto-memory       {on}: install or remove the Claude Code hooks
//! GET  /api/chats             chats with message counts
//! POST /api/chats/relearn     {chat_id}: archive the chat's memories and learn it again with the current rules
//! POST /api/cleanup           {all_minor?}: merge near-duplicates, archive minor memories nobody recalled
//! POST /api/memories/restore  {memory_id}: bring an archived memory back
//! POST /api/import/upload     files (application/x-neuralgraph-files): exports, notes, PDFs, images -> preview
//! POST /api/import/claude-code  Claude Code sessions on this machine -> preview
//! POST /api/import/text       {title?, text}: pasted text -> preview
//! POST /api/import/commit     {token, ids?}: queue the chosen conversations of a preview
//! *    /mcp                   MCP Streamable HTTP transport (see [`crate::mcp`])
//!
//! The API is what a chat application calls after every turn ("all automatic"): POST the message, forget about
//! it; the worker learns in the background and the dashboard shows it happening. Routing, status codes, headers
//! and JSON bodies match the previous aiohttp server, including its 404/405 text responses.

use crate::graph::{graph_value, GraphOptions, GRAPH_HTML};
use crate::mcp::{
    rpc_error, McpProtocol, JSONRPC_PARSE_ERROR, LATEST_PROTOCOL_VERSION, MAX_HTTP_SESSIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
};
use crate::pycompat::{dumps, py_float_from_value, py_int_parse, py_strip, py_truthy, JsonFormat};
use crate::retrieval::SearchOptions;
use crate::service::{ChatMemory, MessageInput, RememberOptions};
use crate::setup;
use crate::store::MemoryQuery;
use axum::body::{Body, Bytes};
use axum::extract::{Request, State};
use axum::http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode};
use axum::response::Response;
use axum::Router;
use indexmap::IndexMap;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// The dashboard page, embedded at build time.
pub const DASHBOARD_HTML: &str = include_str!("../assets/dashboard.html");
const MAX_BODY: usize = 8 * 1024 * 1024;
pub const LOOPBACK_HOSTS: [&str; 5] = ["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"];

/// Security options of the HTTP server.
#[derive(Clone, Debug, Default)]
pub struct ServerOptions {
    /// bearer token required on /mcp
    pub mcp_token: Option<String>,
    /// bearer token required on /api/*
    pub api_token: Option<String>,
    /// browser `Origin`s accepted on /mcp (None = any)
    pub allowed_origins: Option<Vec<String>>,
    /// origins allowed to call /api/* cross-site ("*" for any); CORS is off when empty
    pub cors_origins: Option<Vec<String>>,
    /// accepted Host header names (None = any)
    pub allowed_hosts: Option<Vec<String>>,
}

/// MCP Streamable HTTP (2025-03-26+): one endpoint, POST for client->server messages.
///
/// * Responses are returned as `application/json` (single response) or, when the client only accepts
///   `text/event-stream`, as one SSE `message` event followed by stream end.
/// * A session id is issued on `initialize` (`Mcp-Session-Id`); sessions are lightweight here (no server-initiated
///   messages), so an unknown/missing session is tolerated for stateless clients.
/// * `GET` opens an SSE stream that only carries keep-alives (the server never pushes requests); `DELETE` ends a
///   session.
/// * Optional bearer-token auth via `token`; `Origin` is validated against `allowed_origins` when set
///   (DNS-rebinding protection for local deployments).
pub struct StreamableHttpTransport {
    proto: McpProtocol,
    token: Option<String>,
    allowed_origins: Option<Vec<String>>,
    sessions: Mutex<IndexMap<String, std::time::Instant>>,
}

struct AppState {
    cm: Arc<ChatMemory>,
    mcp: StreamableHttpTransport,
    api_token: Option<String>,
    cors: Vec<String>,
    hosts: Option<Vec<String>>,
    /// parsed imports waiting for the user to confirm: token -> (created, plan)
    imports: Mutex<IndexMap<String, (std::time::Instant, crate::import::ImportPlan)>>,
}

/// Constant-time comparison (`hmac.compare_digest`).
fn compare_digest(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn bearer_ok(headers: &HeaderMap, token: &str) -> bool {
    let auth = header(headers, "Authorization").unwrap_or_default();
    match auth.strip_prefix("Bearer ") {
        Some(rest) => compare_digest(py_strip(rest).as_bytes(), token.as_bytes()),
        None => false,
    }
}

fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers.get(name).map(|v| String::from_utf8_lossy(v.as_bytes()).into_owned())
}

fn response(status: u16, content_type: Option<&str>, body: impl Into<Body>) -> Response {
    let mut r = Response::new(body.into());
    *r.status_mut() = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    if let Some(ct) = content_type {
        r.headers_mut().insert("content-type", HeaderValue::from_str(ct).expect("valid content type"));
    }
    r
}

/// The API's JSON response: non-ASCII kept, `Cache-Control: no-store`.
fn json_resp(data: &Value, status: u16) -> Response {
    let mut r = response(status, Some("application/json; charset=utf-8"), dumps(data, &JsonFormat::unicode()));
    r.headers_mut().insert("cache-control", HeaderValue::from_static("no-store"));
    r
}

fn bad(msg: &str, status: u16) -> Response {
    json_resp(&json!({"error": msg}), status)
}

/// `web.json_response`: ASCII-escaped JSON.
fn mcp_json(data: &Value, status: u16) -> Response {
    response(status, Some("application/json; charset=utf-8"), dumps(data, &JsonFormat::python_default()))
}

fn text_error(status: u16, text: &str) -> Response {
    response(status, Some("text/plain; charset=utf-8"), text.to_string())
}

fn set_header(r: &mut Response, name: &str, value: &str) {
    if let (Ok(n), Ok(v)) = (HeaderName::from_bytes(name.as_bytes()), HeaderValue::from_str(value)) {
        r.headers_mut().insert(n, v);
    }
}

fn cors_headers(origin: &str) -> Vec<(&'static str, String)> {
    vec![
        ("Access-Control-Allow-Origin", origin.to_string()),
        ("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS".into()),
        (
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Accept".into(),
        ),
        ("Access-Control-Expose-Headers", "Mcp-Session-Id, MCP-Protocol-Version".into()),
        ("Vary", "Origin".into()),
    ]
}

/// Media type as aiohttp reports it: lower-cased `type/subtype`, `application/octet-stream` when absent and
/// `text/plain` when malformed.
fn media_type(headers: &HeaderMap) -> String {
    match header(headers, "Content-Type") {
        None => "application/octet-stream".into(),
        Some(ct) => {
            let main = py_strip(ct.split(';').next().unwrap_or("")).to_lowercase();
            if main.matches('/').count() != 1 {
                "text/plain".into()
            } else {
                main
            }
        }
    }
}

fn has_body(headers: &HeaderMap, body: &[u8]) -> bool {
    !body.is_empty() || headers.contains_key("transfer-encoding")
}

impl AppState {
    fn host_allowed(&self, headers: &HeaderMap) -> bool {
        let Some(hosts) = &self.hosts else { return true };
        let host = py_strip(&header(headers, "Host").unwrap_or_default()).to_lowercase();
        let name = if host.starts_with('[') {
            format!("{}]", host.split(']').next().unwrap_or(""))
        } else {
            host.split(':').next().unwrap_or("").to_string()
        };
        hosts.contains(&name)
    }

    /// Same-origin, no Origin (non-browser client), or an origin on the allow-list.
    fn origin_allowed(&self, headers: &HeaderMap) -> bool {
        let Some(origin) = header(headers, "Origin") else { return true };
        if origin.is_empty() {
            return true;
        }
        let o = origin.to_lowercase();
        if self.cors.iter().any(|c| c == "*" || *c == o) {
            return true;
        }
        let host = header(headers, "Host").unwrap_or_default().to_lowercase();
        let tail = match o.split_once("://") {
            Some((_, rest)) => rest.to_string(),
            None => o.clone(),
        };
        !host.is_empty() && tail == host
    }

    fn cors_for(&self, headers: &HeaderMap) -> Vec<(&'static str, String)> {
        let Some(origin) = header(headers, "Origin").filter(|o| !o.is_empty()) else { return Vec::new() };
        if self.cors.is_empty() || !self.cors.iter().any(|c| c == "*" || *c == origin.to_lowercase()) {
            return Vec::new();
        }
        cors_headers(&origin)
    }
}

/// Build the application.
///
/// Security defaults: no CORS headers unless `cors_origins` lists the browser origins allowed to call the API
/// (`"*"` for any); the MCP endpoint additionally validates `Origin` against `allowed_origins`; `allowed_hosts`
/// (when given) rejects requests whose `Host` header is not in the list, which blocks DNS rebinding against a
/// server bound to localhost ([`run_server`] sets it automatically for loopback binds).
pub fn create_app(cm: Arc<ChatMemory>, opts: ServerOptions) -> Router {
    let state = Arc::new(AppState {
        mcp: StreamableHttpTransport {
            proto: McpProtocol::new(cm.clone()),
            token: opts.mcp_token.filter(|t| !t.is_empty()),
            allowed_origins: opts.allowed_origins.filter(|o| !o.is_empty()),
            sessions: Mutex::new(IndexMap::new()),
        },
        cm,
        imports: Mutex::new(IndexMap::new()),
        api_token: opts.api_token.filter(|t| !t.is_empty()),
        cors: opts.cors_origins.unwrap_or_default().iter().map(|o| o.to_lowercase()).collect(),
        hosts: opts.allowed_hosts.filter(|h| !h.is_empty()).map(|h| h.iter().map(|x| x.to_lowercase()).collect()),
    });
    Router::new().fallback(dispatch).with_state(state)
}

/// Query string as aiohttp parses it (`+` is a space; the first value of a repeated key wins).
fn query_params(uri: &axum::http::Uri) -> IndexMap<String, String> {
    let mut out = IndexMap::new();
    if let Some(q) = uri.query() {
        for (k, v) in url::form_urlencoded::parse(q.as_bytes()) {
            out.entry(k.into_owned()).or_insert_with(|| v.into_owned());
        }
    }
    out
}

fn percent_decode(s: &str) -> String {
    url::form_urlencoded::parse(format!("x={}", s.replace('+', "%2B")).as_bytes())
        .next()
        .map(|(_, v)| v.into_owned())
        .unwrap_or_default()
}

async fn dispatch(State(st): State<Arc<AppState>>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let headers = parts.headers.clone();
    let method = parts.method.clone();
    let path = percent_decode(parts.uri.path());
    let query = query_params(&parts.uri);
    // the import upload carries whole chat exports (hundreds of MB); everything else stays small
    let upload = path == "/api/import/upload";
    let body = match axum::body::to_bytes(body, if upload { crate::import::MAX_UPLOAD } else { MAX_BODY }).await {
        Ok(b) => b,
        Err(_) => return text_error(413, "413: Request Entity Too Large"),
    };

    if !st.host_allowed(&headers) {
        return bad("host not allowed", 421);
    }
    if method == Method::OPTIONS {
        let h = st.cors_for(&headers);
        let mut r = response(if h.is_empty() { 403 } else { 204 }, None, Body::empty());
        for (k, v) in h {
            set_header(&mut r, k, &v);
        }
        return r;
    }
    let protected = path.starts_with("/api/") || path == "/mcp";
    if protected && matches!(method, Method::POST | Method::PUT | Method::PATCH | Method::DELETE) {
        // CSRF: a browser page on another origin cannot write, even with a text/plain body that skips preflight
        if !st.origin_allowed(&headers) {
            return bad("origin not allowed", 403);
        }
        // the upload's own type cannot be sent cross-site without a CORS preflight either
        let ok_type = media_type(&headers).starts_with("application/json")
            || (upload && media_type(&headers).starts_with(crate::import::UPLOAD_MEDIA_TYPE));
        if has_body(&headers, &body) && !ok_type {
            return bad("Content-Type must be application/json", 415);
        }
    }
    if let Some(token) = &st.api_token {
        if path.starts_with("/api/") && !bearer_ok(&headers, token) {
            return bad("unauthorized", 401);
        }
    }
    let mut resp = route(&st, &method, &path, &query, &headers, body).await;
    // router-level 404/405 bypass the CORS step (they were raised, not returned, by the previous server)
    let routed_error = matches!(resp.status().as_u16(), 404 | 405)
        && resp.headers().get("content-type").is_some_and(|c| c.as_bytes().starts_with(b"text/plain"));
    if routed_error {
        return resp;
    }
    for (k, v) in st.cors_for(&headers) {
        if !resp.headers().contains_key(k) {
            set_header(&mut resp, k, &v);
        }
    }
    resp
}

async fn route(
    st: &Arc<AppState>,
    method: &Method,
    path: &str,
    query: &IndexMap<String, String>,
    headers: &HeaderMap,
    body: Bytes,
) -> Response {
    let get_like = *method == Method::GET || *method == Method::HEAD;
    let not_allowed = |allow: &str| {
        let mut r = text_error(405, "405: Method Not Allowed");
        set_header(&mut r, "Allow", allow);
        r
    };
    match path {
        "/mcp" => return st.mcp.handle(method, headers, &body).await,
        "/" | "/healthz" | "/api/status" | "/api/search" | "/api/context" | "/api/memories" | "/api/entities"
        | "/api/graph" | "/graph" => {
            if !get_like {
                return not_allowed("GET,HEAD");
            }
        }
        "/api/control"
        | "/api/settings"
        | "/api/settings/test"
        | "/api/connect"
        | "/api/auto-memory"
        | "/api/chats"
        | "/api/cleanup"
        | "/api/chats/relearn"
        | "/api/memories/restore"
        | "/api/import/claude-code"
        | "/api/import/text"
        | "/api/import/commit" => {
            return control_route(st, method, path, headers, &body).await;
        }
        "/api/import/upload" => {
            if *method != Method::POST {
                return not_allowed("POST");
            }
            return import_upload(st, headers, &body).await;
        }
        "/api/messages" | "/api/messages/batch" | "/api/remember" | "/api/forget" | "/api/maintain" | "/api/answer" => {
            if *method != Method::POST {
                return not_allowed("POST");
            }
        }
        p if p.starts_with("/api/profile/")
            && !p["/api/profile/".len()..].is_empty()
            && !p["/api/profile/".len()..].contains('/') =>
        {
            if !get_like {
                return not_allowed("GET,HEAD");
            }
            let subject = &p["/api/profile/".len()..];
            return match st.cm.profile(subject, 60) {
                Ok(v) => json_resp(&v, 200),
                Err(e) => internal(e),
            };
        }
        _ => return text_error(404, "404: Not Found"),
    }
    let cm = &st.cm;
    let q = |k: &str| query.get(k).cloned();
    let q_or_none = |k: &str| query.get(k).filter(|v| !v.is_empty()).cloned();
    let result: Result<Response, crate::error::MemoryError> = async {
        Ok(match path {
            "/" => {
                let mut r = response(200, Some("text/html; charset=utf-8"), DASHBOARD_HTML);
                r.headers_mut().insert("cache-control", HeaderValue::from_static("no-store"));
                r
            }
            "/healthz" => json_resp(&json!({"ok": true, "worker_running": cm.worker.running()}), 200),
            "/api/status" => json_resp(&cm.status()?, 200),
            "/api/search" => {
                let query_text = py_strip(&q("q").unwrap_or_default()).to_string();
                if query_text.is_empty() {
                    return Ok(bad("q is required", 400));
                }
                let Some(k) = py_int_parse(&q("k").unwrap_or_else(|| "15".into())) else {
                    return Ok(bad("k must be an integer", 400));
                };
                let kinds: Vec<String> =
                    q("kinds").unwrap_or_default().split(',').filter(|x| !x.is_empty()).map(str::to_string).collect();
                let opts = SearchOptions {
                    k: k.clamp(1, 50) as usize,
                    subject: q_or_none("subject"),
                    chat_id: q_or_none("chat_id"),
                    kinds: if kinds.is_empty() { None } else { Some(kinds) },
                    since: q_or_none("since"),
                    until: q_or_none("until"),
                    include_superseded: q("history").as_deref() == Some("1"),
                    ..Default::default()
                };
                let hits = cm.search(&query_text, &opts).await?;
                json_resp(
                    &json!({"query": query_text, "results": hits.iter().map(|h| h.to_value()).collect::<Vec<_>>()}),
                    200,
                )
            }
            "/api/context" => {
                let query_text = py_strip(&q("q").unwrap_or_default()).to_string();
                if query_text.is_empty() {
                    return Ok(bad("q is required", 400));
                }
                let (Some(k), Some(max_chars)) = (
                    py_int_parse(&q("k").unwrap_or_else(|| "15".into())),
                    py_int_parse(&q("max_chars").unwrap_or_else(|| "3600".into())),
                ) else {
                    return Ok(bad("k/max_chars must be integers", 400));
                };
                let filters = SearchOptions { subject: q_or_none("subject"), ..Default::default() };
                let block = cm
                    .context_for(
                        &query_text,
                        Some(k.clamp(1, 30) as usize),
                        Some(max_chars.clamp(200, 20000) as usize),
                        &filters,
                    )
                    .await?;
                json_resp(&json!({"query": query_text, "context": block}), 200)
            }
            "/api/memories" => {
                let Some(limit) = py_int_parse(&q("limit").unwrap_or_else(|| "50".into())) else {
                    return Ok(bad("limit must be an integer", 400));
                };
                let kinds: Vec<String> =
                    q("kind").unwrap_or_default().split(',').filter(|x| !x.is_empty()).map(str::to_string).collect();
                let mems = cm.memories(&MemoryQuery {
                    subject: q_or_none("subject"),
                    kinds: if kinds.is_empty() { None } else { Some(kinds) },
                    chat_id: q_or_none("chat_id"),
                    status: Some(q_or_none("status").unwrap_or_else(|| "active".into())),
                    limit: Some(limit.clamp(1, 500)),
                    order: "created_at DESC".into(),
                    with_sources: true,
                    ..Default::default()
                })?;
                json_resp(&json!({"memories": mems.iter().map(|m| m.to_value(false)).collect::<Vec<_>>()}), 200)
            }
            "/api/entities" => {
                let Some(limit) = py_int_parse(&q("limit").unwrap_or_else(|| "50".into())) else {
                    return Ok(bad("limit must be an integer", 400));
                };
                let mut out = Vec::new();
                for e in cm.store.top_entities(limit.clamp(1, 500), None)? {
                    let mut d = e.to_value();
                    let rels = cm.store.relations_for(&e.entity_id, Some("active"), 20)?;
                    d.as_object_mut()
                        .expect("object")
                        .insert("relations".into(), Value::Array(rels.iter().map(|r| r.to_value()).collect()));
                    out.push(d);
                }
                json_resp(&json!({"entities": out}), 200)
            }
            "/api/graph" => {
                let Some(limit) = py_int_parse(&q("limit").unwrap_or_else(|| "150".into())) else {
                    return Ok(bad("limit must be an integer", 400));
                };
                let opts = GraphOptions { limit, chat_id: q_or_none("chat_id"), ..Default::default() };
                json_resp(&graph_value(&cm.store, &opts)?, 200)
            }
            "/graph" => {
                let mut r = response(200, Some("text/html; charset=utf-8"), GRAPH_HTML);
                r.headers_mut().insert("cache-control", HeaderValue::from_static("no-store"));
                r
            }
            "/api/messages" => {
                let b = match body_object(&body) {
                    Ok(b) => b,
                    Err(r) => return Ok(*r),
                };
                for f in ["chat_id", "speaker", "text"] {
                    match b.get(f) {
                        Some(Value::String(s)) if !py_strip(s).is_empty() => {}
                        _ => return Ok(bad(&format!("'{f}' is required"), 400)),
                    }
                }
                for f in ["role", "sent_at", "message_id"] {
                    if b.get(f).is_some_and(|v| !v.is_null() && !v.is_string()) {
                        return Ok(bad(&format!("'{f}' must be a string"), 400));
                    }
                }
                if b.get("metadata").is_some_and(|v| !v.is_null() && !v.is_object()) {
                    return Ok(bad("'metadata' must be an object", 400));
                }
                let s = |k: &str| b.get(k).and_then(Value::as_str).map(str::to_string);
                let input = MessageInput {
                    role: s("role").unwrap_or_default(),
                    sent_at: s("sent_at"),
                    message_id: s("message_id"),
                    metadata: b.get("metadata").filter(|v| !v.is_null()).cloned(),
                };
                let msg = cm.add_message(
                    &s("chat_id").unwrap_or_default(),
                    &s("speaker").unwrap_or_default(),
                    &s("text").unwrap_or_default(),
                    input,
                )?;
                json_resp(&json!({"queued": true, "message": msg.to_value()}), 202)
            }
            "/api/messages/batch" => {
                let b = match body_object(&body) {
                    Ok(b) => b,
                    Err(r) => return Ok(*r),
                };
                let (chat_id, items) = match (b.get("chat_id"), b.get("messages")) {
                    (Some(Value::String(c)), Some(Value::Array(items))) if !c.is_empty() => (c.clone(), items.clone()),
                    _ => return Ok(bad("'chat_id' and 'messages' are required", 400)),
                };
                // validate everything before inserting anything (all-or-nothing)
                for (i, it) in items.iter().enumerate() {
                    let Value::Object(o) = it else {
                        return Ok(bad(&format!("messages[{i}] must be an object with a 'text' string"), 400));
                    };
                    if !o.get("text").is_some_and(Value::is_string) {
                        return Ok(bad(&format!("messages[{i}] must be an object with a 'text' string"), 400));
                    }
                    for f in ["speaker", "role", "sent_at", "message_id"] {
                        if o.get(f).is_some_and(|v| !v.is_null() && !v.is_string()) {
                            return Ok(bad(&format!("messages[{i}].{f} must be a string"), 400));
                        }
                    }
                    if o.get("metadata").is_some_and(|v| !v.is_null() && !v.is_object()) {
                        return Ok(bad(&format!("messages[{i}].metadata must be an object"), 400));
                    }
                }
                let msgs = cm.add_messages(&chat_id, &items)?;
                json_resp(
                    &json!({"queued": msgs.len(), "message_ids": msgs.iter().map(|m| m.message_id.clone()).collect::<Vec<_>>()}),
                    202,
                )
            }
            "/api/remember" => {
                let b = match body_object(&body) {
                    Ok(b) => b,
                    Err(r) => return Ok(*r),
                };
                match b.get("text") {
                    Some(Value::String(s)) if !py_strip(s).is_empty() => {}
                    _ => return Ok(bad("'text' is required", 400)),
                }
                let importance = match b.get("importance").filter(|v| !v.is_null()) {
                    None => 0.8,
                    Some(v) => match py_float_from_value(v) {
                        Some(f) => f,
                        None => return Ok(bad("'importance' must be a number", 400)),
                    },
                };
                for f in ["subject", "kind", "chat_id", "when"] {
                    if b.get(f).is_some_and(|v| !v.is_null() && !v.is_string()) {
                        return Ok(bad(&format!("'{f}' must be a string"), 400));
                    }
                }
                let s = |k: &str| b.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string);
                let opts = RememberOptions {
                    subject: s("subject").unwrap_or_else(|| "user".into()),
                    kind: s("kind").unwrap_or_else(|| "fact".into()),
                    importance,
                    chat_id: s("chat_id").unwrap_or_else(|| "manual".into()),
                    when: b.get("when").and_then(Value::as_str).map(str::to_string),
                    ..Default::default()
                };
                let text = b["text"].as_str().unwrap_or_default().to_string();
                match cm.remember(&text, &opts).await {
                    Ok(mem) => json_resp(&json!({"memory": mem.to_value(false)}), 201),
                    Err(crate::error::MemoryError::Value(m)) => bad(&m, 400),
                    Err(e) => return Err(e),
                }
            }
            "/api/forget" => {
                let b = match body_object(&body) {
                    Ok(b) => b,
                    Err(r) => return Ok(*r),
                };
                let Some(Value::String(memory_id)) = b.get("memory_id") else {
                    return Ok(bad("'memory_id' is required", 400));
                };
                let reason = match b.get("reason") {
                    Some(v) if py_truthy(v) => crate::pycompat::py_str(v),
                    _ => "user request".into(),
                };
                let ok = cm.retract(memory_id, &reason)?;
                json_resp(&json!({"retracted": ok}), 200)
            }
            "/api/maintain" => json_resp(&cm.maintain().await?, 200),
            "/api/answer" => {
                let b = match body_object(&body) {
                    Ok(b) => b,
                    Err(r) => return Ok(*r),
                };
                let question = match b.get("question") {
                    Some(Value::String(s)) if !py_strip(s).is_empty() => py_strip(s).to_string(),
                    _ => return Ok(bad("'question' is required", 400)),
                };
                if b.get("today").is_some_and(|v| !v.is_null() && !v.is_string()) {
                    return Ok(bad("'today' must be a string", 400));
                }
                let today = b.get("today").and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_string);
                let opts = crate::answer::AnswerOptions { today, ..Default::default() };
                json_resp(&cm.answer(&question, &opts).await?.to_value(), 200)
            }
            _ => text_error(404, "404: Not Found"),
        })
    }
    .await;
    result.unwrap_or_else(internal)
}

/// Settings-changing requests must come from this page: JSON (which a cross-site form cannot send without a CORS
/// preflight) and, when the browser says where it comes from, the same origin as the server.
fn same_origin_json(headers: &HeaderMap) -> bool {
    let h = |k: &str| headers.get(k).and_then(|v| v.to_str().ok());
    let json = h("content-type").is_some_and(|v| v.starts_with("application/json"));
    let origin_ok = match h("origin") {
        None => true,
        Some(o) => h("host").is_some_and(|host| o == format!("http://{host}") || o == format!("https://{host}")),
    };
    json && origin_ok
}

/// The control-panel API: pause/resume, settings, tool connections, auto-memory, chats.
async fn control_route(st: &Arc<AppState>, method: &Method, path: &str, headers: &HeaderMap, body: &[u8]) -> Response {
    let cm = &st.cm;
    let get_like = *method == Method::GET || *method == Method::HEAD;
    let post = *method == Method::POST;
    let allowed = match path {
        "/api/control" | "/api/settings" => get_like || post,
        "/api/chats" => get_like,
        _ => post,
    };
    if !allowed {
        let mut r = text_error(405, "405: Method Not Allowed");
        let allow = match path {
            "/api/control" | "/api/settings" => "GET,HEAD,POST",
            "/api/chats" => "GET,HEAD",
            _ => "POST",
        };
        set_header(&mut r, "Allow", allow);
        return r;
    }
    if post && !same_origin_json(headers) {
        return bad("send JSON from the dashboard's own origin", 403);
    }
    let b = if post {
        match body_object(body) {
            Ok(b) => b,
            Err(r) => return *r,
        }
    } else {
        serde_json::Map::new()
    };
    let s = |k: &str| b.get(k).and_then(Value::as_str).map(str::trim).filter(|v| !v.is_empty()).map(str::to_string);
    let llm = cm.control.current();
    let models = || json!({"base_url": llm.base_url(), "chat": llm.model(), "embed": llm.embed_model()});
    let steps = |v: Vec<crate::control::Step>| Value::Array(v.iter().map(|x| x.to_value()).collect());
    match (path, post) {
        ("/api/control", false) => json_resp(&json!({"paused": cm.store.is_paused(), "models": models()}), 200),
        ("/api/control", true) => {
            let r = match s("action").as_deref() {
                Some("pause") => cm.pause().await,
                Some("resume") => cm.resume().await,
                _ => return bad("action must be \"pause\" or \"resume\"", 400),
            };
            match r {
                Ok(v) => json_resp(&json!({"paused": cm.store.is_paused(), "steps": steps(v)}), 200),
                Err(e) => internal(e),
            }
        }
        ("/api/chats", _) => match cm.store.list_chats(200, 0) {
            Ok(chats) => json_resp(&json!({"chats": chats}), 200),
            Err(e) => internal(e),
        },
        ("/api/settings", false) => {
            let path = setup::config_path();
            let saved = setup::load_saved(&path).unwrap_or_default();
            let servers = setup::discover_servers(Some(llm.base_url())).await;
            let clients = tokio::task::spawn_blocking(setup::clients_status).await.unwrap_or_default();
            let exe = setup::current_exe();
            json_resp(
                &json!({
                    "models": models(),
                    "user_name": saved.user_name.clone().or_else(|| std::env::var("NEURALGRAPH_USER_NAME").ok()),
                    "db_path": cm.store.db_path,
                    "config_path": path.display().to_string(),
                    "servers": servers,
                    "clients": clients,
                    "auto_memory": setup::claude_hooks_installed(),
                    "vision": {
                        "chat_sees": crate::vision::can_see(&llm.base_url(), &llm.model()).await,
                        "model": crate::vision::configured_vision_model(),
                    },
                    "exe": exe,
                    "exe_in_build_dir": exe.contains("/target/debug/") || exe.contains("/target/release/"),
                    "paused": cm.store.is_paused(),
                }),
                200,
            )
        }
        ("/api/settings", true) => {
            let url = s("llm_base_url");
            if url.as_deref().is_some_and(|u| !(u.starts_with("http://") || u.starts_with("https://"))) {
                return bad("llm_base_url must start with http:// or https://", 400);
            }
            let path = setup::config_path();
            let mut cfg = setup::load_saved(&path).unwrap_or_default();
            if url.is_some() && url != cfg.llm_base_url {
                cfg.llm_backend = None; // guessed again from the new URL
            }
            for (field, v) in [
                (&mut cfg.llm_base_url, url.clone()),
                (&mut cfg.llm_model, s("llm_model")),
                (&mut cfg.embed_model, s("embed_model")),
                (&mut cfg.user_name, s("user_name")),
            ] {
                if v.is_some() {
                    *field = v;
                }
            }
            if b.contains_key("vision_model") {
                // "" clears it: images then need a chat model that can see
                cfg.vision_model = s("vision_model");
                if cfg.vision_model.is_none() {
                    std::env::remove_var("NEURALGRAPH_VISION_MODEL");
                }
            }
            if let Err(e) = setup::save(&path, &cfg) {
                return internal(e.into());
            }
            for (k, v) in cfg.env_pairs() {
                std::env::set_var(k, v);
            }
            cm.switch_models(url, s("llm_model"), s("embed_model"));
            json_resp(
                &json!({"saved": true, "models": {
                "base_url": cm.control.current().base_url(),
                "chat": cm.control.current().model(),
                "embed": cm.control.current().embed_model(),
            }, "note": if s("user_name").is_some() { "A new name applies to memories extracted after the next restart." } else { "" }}),
                200,
            )
        }
        ("/api/settings/test", _) => {
            let url = s("llm_base_url").unwrap_or_else(|| llm.base_url());
            let chat = s("llm_model").unwrap_or_else(|| llm.model());
            let embed = s("embed_model").unwrap_or_else(|| llm.embed_model());
            json_resp(&setup::test_models(&url, &chat, &embed).await, 200)
        }
        ("/api/connect", _) => {
            let Some(name) = s("client") else { return bad("'client' is required", 400) };
            match tokio::task::spawn_blocking(move || setup::connect_client(&name)).await {
                Ok(Ok(msg)) => json_resp(&json!({"ok": true, "message": msg}), 200),
                Ok(Err(e)) => json_resp(&json!({"ok": false, "message": e}), 200),
                Err(e) => bad(&e.to_string(), 500),
            }
        }
        ("/api/cleanup", _) => {
            let all = b.get("all_minor").and_then(Value::as_bool).unwrap_or(true);
            match cm.cleanup(all).await {
                Ok(v) => json_resp(&v, 200),
                Err(e) => internal(e),
            }
        }
        ("/api/chats/relearn", _) => {
            let Some(chat) = s("chat_id") else { return bad("'chat_id' is required", 400) };
            match cm.relearn_chat(&chat) {
                Ok(v) => json_resp(&v, 200),
                Err(e) => internal(e),
            }
        }
        ("/api/memories/restore", _) => {
            let Some(id) = s("memory_id") else { return bad("'memory_id' is required", 400) };
            match cm.store.restore_memory(&id) {
                Ok(restored) => json_resp(&json!({"restored": restored}), 200),
                Err(e) => internal(e),
            }
        }
        ("/api/import/claude-code", _) => {
            let plan = tokio::task::spawn_blocking(crate::import::scan_claude_code).await.unwrap_or_default();
            json_resp(&stash_import(st, plan), 200)
        }
        ("/api/import/text", _) => {
            let Some(text) = s("text") else { return bad("'text' is required", 400) };
            let title = s("title").unwrap_or_else(|| "Pasted text".into());
            let conv = crate::import::document(&title, &text, text.as_bytes());
            let plan = crate::import::ImportPlan {
                source: "text".into(),
                label: "Pasted text".into(),
                conversations: conv.into_iter().collect(),
                ..Default::default()
            };
            json_resp(&stash_import(st, plan), 200)
        }
        ("/api/import/commit", _) => {
            let Some(token) = s("token") else { return bad("'token' is required", 400) };
            let ids: Vec<String> = b
                .get("ids")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default();
            let plan = st.imports.lock().unwrap_or_else(|e| e.into_inner()).get(&token).map(|(_, p)| p.clone());
            let Some(plan) = plan else { return bad("this preview expired; choose the files again", 410) };
            match crate::import::commit(cm, &plan, &ids) {
                Ok((chats, added, skipped)) => {
                    st.imports.lock().unwrap_or_else(|e| e.into_inner()).shift_remove(&token);
                    json_resp(&json!({"chats": chats, "queued": added, "already": skipped}), 200)
                }
                Err(e) => internal(e),
            }
        }
        ("/api/auto-memory", _) => {
            let Some(on) = b.get("on").and_then(Value::as_bool) else { return bad("'on' must be true or false", 400) };
            let exe = setup::current_exe();
            match setup::set_claude_hooks(on.then_some(exe.as_str())) {
                Ok(msg) => json_resp(&json!({"ok": true, "on": on, "message": msg}), 200),
                Err(e) => json_resp(&json!({"ok": false, "on": !on, "message": e}), 200),
            }
        }
        _ => text_error(404, "404: Not Found"),
    }
}

/// Keep a parsed import for the commit step (an hour, at most 8 at a time) and describe it for the preview.
fn stash_import(st: &Arc<AppState>, plan: crate::import::ImportPlan) -> Value {
    let token = uuid::Uuid::new_v4().to_string();
    let preview = crate::import::preview(&st.cm, &plan, &token);
    let mut imports = st.imports.lock().unwrap_or_else(|e| e.into_inner());
    imports.retain(|_, (at, _)| at.elapsed().as_secs() < 3600);
    while imports.len() >= 8 {
        imports.shift_remove_index(0);
    }
    imports.insert(token, (std::time::Instant::now(), plan));
    preview
}

/// `POST /api/import/upload`: one or more files in the `application/x-neuralgraph-files` framing. Images are read
/// together in one model session; every file becomes conversations of one combined preview.
async fn import_upload(st: &Arc<AppState>, headers: &HeaderMap, body: &[u8]) -> Response {
    if let Some(o) = headers.get("origin").and_then(|v| v.to_str().ok()) {
        let host = headers.get("host").and_then(|v| v.to_str().ok()).unwrap_or("");
        if o != format!("http://{host}") && o != format!("https://{host}") {
            return bad("upload from the dashboard's own origin", 403);
        }
    }
    let files = match crate::import::unframe(body) {
        Ok(f) if !f.is_empty() => f,
        Ok(_) => return bad("no files in the upload", 400),
        Err(e) => return bad(&e, 400),
    };
    let mut plan = crate::import::ImportPlan::default();
    let mut sources = Vec::new();
    let (images, others): (Vec<_>, Vec<_>) = files.into_iter().partition(|(n, _)| crate::import::is_image(n));
    for (name, bytes) in others {
        let parsed = tokio::task::spawn_blocking(move || {
            let r = crate::import::parse_upload(&name, &bytes);
            (name, r)
        })
        .await;
        match parsed {
            Ok((_, Ok(p))) => {
                sources.push(p.label.clone());
                plan.conversations.extend(p.conversations);
                plan.warnings.extend(p.warnings);
                if plan.source.is_empty() {
                    plan.source = p.source;
                }
            }
            Ok((name, Err(e))) => plan.warnings.push(format!("{name}: {e}")),
            Err(e) => plan.warnings.push(e.to_string()),
        }
    }
    if !images.is_empty() {
        match crate::vision::read_images(&st.cm, &images).await {
            Ok((texts, steps)) => {
                plan.warnings.extend(steps.into_iter().filter(|s| !s.ok).map(|s| s.text));
                for (name, r) in texts {
                    match r {
                        Ok(text) => {
                            let key = format!("{name}:{text}");
                            plan.conversations.extend(crate::import::document(&name, &text, key.as_bytes()));
                        }
                        Err(e) => plan.warnings.push(format!("{name}: {}", e.message())),
                    }
                }
                sources.push(format!("{} image{}", images.len(), if images.len() == 1 { "" } else { "s" }));
                if plan.source.is_empty() {
                    plan.source = "image".into();
                }
            }
            Err(e) => plan.warnings.push(e.message()),
        }
    }
    if plan.conversations.is_empty() {
        let why = if plan.warnings.is_empty() {
            "nothing to import in these files".to_string()
        } else {
            plan.warnings.join("; ")
        };
        return bad(&why, 422);
    }
    plan.label = sources.join(" + ");
    json_resp(&stash_import(st, plan), 200)
}

fn internal(e: crate::error::MemoryError) -> Response {
    tracing::error!("request failed: {}", e.qualified());
    text_error(500, "500 Internal Server Error\n\nServer got itself in trouble")
}

/// Decode the JSON body; the object on success, a 400 response otherwise.
fn body_object(body: &[u8]) -> Result<serde_json::Map<String, Value>, Box<Response>> {
    let parsed: Option<Value> = std::str::from_utf8(body).ok().and_then(|s| serde_json::from_str(s).ok());
    match parsed {
        None => Err(Box::new(bad("invalid JSON body", 400))),
        Some(Value::Object(m)) => Ok(m),
        Some(_) => Err(Box::new(bad("JSON body must be an object", 400))),
    }
}

impl StreamableHttpTransport {
    fn authorized(&self, headers: &HeaderMap) -> bool {
        match &self.token {
            None => true,
            Some(t) => bearer_ok(headers, t),
        }
    }

    fn origin_ok(&self, headers: &HeaderMap) -> bool {
        let Some(origin) = header(headers, "Origin").filter(|o| !o.is_empty()) else { return true };
        match &self.allowed_origins {
            None => true,
            Some(list) => list.contains(&origin),
        }
    }

    async fn handle(&self, method: &Method, headers: &HeaderMap, body: &[u8]) -> Response {
        if !self.origin_ok(headers) {
            return mcp_json(&json!({"error": "origin not allowed"}), 403);
        }
        if !self.authorized(headers) {
            let mut r = mcp_json(&json!({"error": "unauthorized"}), 401);
            set_header(&mut r, "WWW-Authenticate", "Bearer realm=\"neuralgraph-memory\"");
            return r;
        }
        if *method == Method::GET {
            return self.handle_get(headers);
        }
        if *method == Method::DELETE {
            let sid = header(headers, "Mcp-Session-Id").unwrap_or_default();
            self.sessions.lock().unwrap_or_else(|e| e.into_inner()).shift_remove(&sid);
            return response(204, None, Body::empty());
        }
        if *method != Method::POST {
            let mut r = mcp_json(&json!({"error": "method not allowed"}), 405);
            set_header(&mut r, "Allow", "GET, POST, DELETE");
            return r;
        }
        if let Some(pv) = header(headers, "MCP-Protocol-Version").filter(|v| !v.is_empty()) {
            if !SUPPORTED_PROTOCOL_VERSIONS.contains(&pv.as_str()) {
                return mcp_json(
                    &json!({"error": format!("unsupported MCP-Protocol-Version {pv}"), "supported": SUPPORTED_PROTOCOL_VERSIONS}),
                    400,
                );
            }
        }
        let payload: Value = match std::str::from_utf8(body).ok().and_then(|s| serde_json::from_str(s).ok()) {
            Some(p) => p,
            None => return mcp_json(&rpc_error(Value::Null, JSONRPC_PARSE_ERROR, "parse error"), 400),
        };
        let first = match &payload {
            Value::Array(a) if !a.is_empty() => &a[0],
            other => other,
        };
        let is_init = first.get("method").and_then(Value::as_str) == Some("initialize");
        let resp = self.proto.handle_payload(&payload).await;
        let mut extra: Vec<(&str, String)> = vec![("MCP-Protocol-Version", LATEST_PROTOCOL_VERSION.to_string())];
        if is_init {
            let sid = uuid::Uuid::new_v4().simple().to_string();
            {
                let mut sessions = self.sessions.lock().unwrap_or_else(|e| e.into_inner());
                sessions.insert(sid.clone(), std::time::Instant::now());
                while sessions.len() > MAX_HTTP_SESSIONS {
                    sessions.shift_remove_index(0);
                }
            }
            extra.push(("Mcp-Session-Id", sid));
            if let Some(v) = resp.as_ref().and_then(|r| r.get("result")).and_then(|r| r.get("protocolVersion")) {
                extra[0].1 = v.as_str().unwrap_or(LATEST_PROTOCOL_VERSION).to_string();
            }
        } else if let Some(sid) = header(headers, "Mcp-Session-Id").filter(|s| !s.is_empty()) {
            extra.push(("Mcp-Session-Id", sid));
        }
        let mut r = match resp {
            None => response(202, None, Body::empty()), // notifications / responses only
            Some(resp) => {
                let accept = header(headers, "Accept").unwrap_or_default();
                let wants_sse = accept.contains("text/event-stream") && !accept.contains("application/json");
                if wants_sse {
                    let body = format!("event: message\ndata: {}\n\n", dumps(&resp, &JsonFormat::unicode()));
                    let mut r = response(200, Some("text/event-stream; charset=utf-8"), body);
                    set_header(&mut r, "Cache-Control", "no-cache");
                    r
                } else {
                    mcp_json(&resp, 200)
                }
            }
        };
        for (k, v) in extra {
            set_header(&mut r, k, &v);
        }
        r
    }

    fn handle_get(&self, headers: &HeaderMap) -> Response {
        let accept = header(headers, "Accept").unwrap_or_default();
        if !accept.contains("text/event-stream") {
            let mut r = mcp_json(&json!({"error": "GET requires Accept: text/event-stream"}), 405);
            set_header(&mut r, "Allow", "POST");
            return r;
        }
        let stream = futures::stream::unfold(true, |first| async move {
            if !first {
                tokio::time::sleep(Duration::from_secs(15)).await;
            }
            Some((Ok::<_, std::io::Error>(Bytes::from_static(b": keep-alive\n\n")), false))
        });
        let mut r = response(200, Some("text/event-stream"), Body::from_stream(stream));
        set_header(&mut r, "Cache-Control", "no-cache");
        set_header(&mut r, "MCP-Protocol-Version", LATEST_PROTOCOL_VERSION);
        r
    }
}

/// A running HTTP server.
pub struct ServerHandle {
    pub addr: std::net::SocketAddr,
    shutdown: tokio_util::sync::CancellationToken,
    task: tokio::task::JoinHandle<()>,
}

impl ServerHandle {
    /// Stop accepting connections and wait for the server task.
    pub async fn cleanup(self) {
        self.shutdown.cancel();
        let _ = self.task.await;
    }
}

/// Start the HTTP server (call [`ServerHandle::cleanup`] to stop).
///
/// When bound to a loopback address and no `allowed_hosts` are given, only loopback Host headers are accepted
/// (DNS-rebinding protection for the local dashboard/API).
pub async fn run_server(
    cm: Arc<ChatMemory>,
    host: &str,
    port: u16,
    mut opts: ServerOptions,
) -> std::io::Result<ServerHandle> {
    if opts.allowed_hosts.is_none() && LOOPBACK_HOSTS.contains(&host) && host != "0.0.0.0" {
        let mut hosts: Vec<String> =
            LOOPBACK_HOSTS.iter().filter(|h| **h != "0.0.0.0").map(|h| h.to_string()).collect();
        hosts.sort();
        opts.allowed_hosts = Some(hosts);
    }
    let app = create_app(cm, opts);
    let bind_host = host.trim_start_matches('[').trim_end_matches(']');
    let listener = tokio::net::TcpListener::bind((bind_host, port)).await?;
    let addr = listener.local_addr()?;
    let shutdown = tokio_util::sync::CancellationToken::new();
    let token = shutdown.clone();
    let task = tokio::spawn(async move {
        let _ = axum::serve(listener, app).with_graceful_shutdown(async move { token.cancelled().await }).await;
    });
    tracing::info!("dashboard at http://{host}:{}/  MCP at http://{host}:{}/mcp", addr.port(), addr.port());
    Ok(ServerHandle { addr, shutdown, task })
}
