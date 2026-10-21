//! MCP server tests: JSON-RPC/MCP protocol core (initialize negotiation, tools/list, tools/call result and error
//! shapes, resources, prompts, batches, notifications), the stdio transport driven through in-memory streams, and
//! the Streamable HTTP transport + dashboard API through the axum router (auth, session id, SSE responses, CORS,
//! 405/202 semantics). Ported from `test_chat_memory_mcp.py`.

mod common;

use axum::body::Body;
use axum::http::Request;
use http_body_util::BodyExt;
use neuralgraph_memory::mcp::{
    serve_stdio_with, tools, McpProtocol, JSONRPC_INVALID_PARAMS, JSONRPC_METHOD_NOT_FOUND, LATEST_PROTOCOL_VERSION,
};
use neuralgraph_memory::server::{create_app, ServerOptions};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig, RememberOptions};
use neuralgraph_memory::testing::scripted_fake_llm;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::Arc;
use tokio::io::AsyncReadExt;
use tower::ServiceExt;

struct Fx {
    _dir: tempfile::TempDir,
    cm: Arc<ChatMemory>,
}

fn fx() -> Fx {
    let dir = tempfile::tempdir().unwrap();
    let mut cfg = ChatMemoryConfig { debounce_seconds: 0.0, ..Default::default() };
    cfg.worker.poll_interval = 0.02;
    let cm = Arc::new(
        ChatMemory::open(dir.path().join("mcp.db").to_str().unwrap(), Some(Arc::new(scripted_fake_llm())), cfg)
            .unwrap(),
    );
    Fx { _dir: dir, cm }
}

async fn seed(cm: &ChatMemory) {
    let ali = RememberOptions { subject: "Ali".into(), chat_id: "c1".into(), ..Default::default() };
    cm.remember("Ali has a cat called Luna.", &ali).await.unwrap();
    cm.remember("Ali lives in Berlin.", &RememberOptions { kind: "identity".into(), ..ali }).await.unwrap();
}

async fn call(p: &McpProtocol, msg: Value) -> Value {
    p.handle(&msg).await.unwrap()
}

#[tokio::test]
async fn test_initialize_negotiates_version() {
    let f = fx();
    let p = McpProtocol::new(f.cm.clone());
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})).await;
    assert_eq!(r["result"]["protocolVersion"], "2025-03-26");
    assert!(r["result"]["capabilities"].get("tools").is_some());
    assert_eq!(r["result"]["serverInfo"]["name"], "neuralgraph-chat-memory");
    let r = call(
        &p,
        json!({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}),
    )
    .await;
    assert_eq!(r["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION);
    assert!(p.handle(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).await.is_none());
    assert_eq!(call(&p, json!({"jsonrpc": "2.0", "id": 3, "method": "ping"})).await["result"], json!({}));
}

#[tokio::test]
async fn test_tools_list_schema_is_well_formed() {
    let f = fx();
    let r = call(&McpProtocol::new(f.cm.clone()), json!({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})).await;
    let listed = r["result"]["tools"].as_array().unwrap().clone();
    let names = |v: &[Value]| v.iter().map(|t| t["name"].as_str().unwrap().to_string()).collect::<Vec<_>>();
    assert_eq!(names(&listed), names(tools().as_array().unwrap()));
    for t in &listed {
        assert_eq!(t["inputSchema"]["type"], "object");
        let props: HashSet<&String> = t["inputSchema"]["properties"].as_object().unwrap().keys().collect();
        for req in t["inputSchema"]["required"].as_array().unwrap() {
            assert!(props.contains(&req.as_str().unwrap().to_string()));
        }
        assert!(t["annotations"].get("readOnlyHint").is_some());
    }
    assert_eq!(names(&listed).into_iter().collect::<HashSet<_>>().len(), listed.len());
}

#[tokio::test]
async fn test_tools_call_success_error_and_unknown() {
    let f = fx();
    seed(&f.cm).await;
    let p = McpProtocol::new(f.cm.clone());
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Luna"}}})).await;
    let res = &r["result"];
    assert_eq!(res["isError"], false);
    assert_eq!(res["content"][0]["type"], "text");
    assert_eq!(res["structuredContent"]["count"], 1);
    let text: Value = serde_json::from_str(res["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(text["count"], 1);
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_related", "arguments": {"memory_id": "nope"}}})).await;
    assert_eq!(r["result"]["isError"], true);
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"nonsense": 1}}})).await;
    assert_eq!(r["result"]["isError"], true, "missing required argument surfaces as a tool error, not a crash");
    let r = call(
        &p,
        json!({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}}),
    )
    .await;
    assert_eq!(r["error"]["code"], JSONRPC_INVALID_PARAMS);
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 5, "method": "does/not/exist"})).await;
    assert_eq!(r["error"]["code"], JSONRPC_METHOD_NOT_FOUND);
    assert_eq!(call(&p, json!({"jsonrpc": "1.0", "id": 6})).await["error"]["code"], -32600);
}

#[tokio::test]
async fn test_context_add_remember_forget_status_roundtrip() {
    let f = fx();
    let p = McpProtocol::new(f.cm.clone());
    let tool = |id: i64, name: &str, args: Value| json!({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": args}});
    let add = call(&p, tool(1, "memory_add_message", json!({"chat_id": "s1", "speaker": "Ali", "text": "I moved to Berlin in March and my cat Luna is 3 years old.", "sent_at": "2026-04-01T10:00:00+00:00"}))).await;
    assert_eq!(add["result"]["isError"], false);
    assert_eq!(add["result"]["structuredContent"]["status"], "pending");
    f.cm.process_pending(None).await.unwrap();
    let ctx = call(&p, tool(2, "memory_context", json!({"query": "Ali cat"}))).await;
    assert!(ctx["result"]["content"][0]["text"].as_str().unwrap().contains("Luna"));
    let rem = call(&p, tool(3, "memory_remember", json!({"text": "Ali prefers tea.", "subject": "Ali"}))).await;
    let mid = rem["result"]["structuredContent"]["memory"]["memory_id"].clone();
    let forget = call(&p, tool(4, "memory_forget", json!({"memory_id": mid}))).await;
    assert_eq!(forget["result"]["structuredContent"]["retracted"], true);
    let st = call(&p, tool(5, "memory_status", json!({}))).await;
    assert!(st["result"]["structuredContent"].get("grade").is_some());
    let ents = call(&p, tool(6, "memory_entities", json!({"entity": "Ali"}))).await;
    assert_eq!(ents["result"]["structuredContent"]["found"], true);
}

#[tokio::test]
async fn test_resources_prompts_and_batch() {
    let f = fx();
    seed(&f.cm).await;
    let p = McpProtocol::new(f.cm.clone());
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})).await;
    let uris: Vec<&str> =
        r["result"]["resources"].as_array().unwrap().iter().map(|x| x["uri"].as_str().unwrap()).collect();
    assert_eq!(uris, ["memory://status", "memory://entities"]);
    let r = call(
        &p,
        json!({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "memory://profile/Ali"}}),
    )
    .await;
    let prof: Value = serde_json::from_str(r["result"]["contents"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(prof["memory_count"], 2);
    let r = call(&p, json!({"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "recall", "arguments": {"topic": "Luna"}}})).await;
    assert!(r["result"]["messages"][0]["content"]["text"].as_str().unwrap().contains("Luna"));
    let batch = p
        .handle_payload(&json!([{"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}]))
        .await
        .unwrap();
    assert_eq!(batch.as_array().unwrap().iter().map(|b| b["id"].clone()).collect::<Vec<_>>(), [json!(4)]);
    assert!(p.handle_payload(&json!({"jsonrpc": "2.0", "method": "notifications/cancelled"})).await.is_none());
}

#[tokio::test]
async fn test_stdio_loop_end_to_end() {
    let f = fx();
    seed(&f.cm).await;
    let msgs = [
        json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}),
        json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Berlin"}}}),
    ];
    let mut input = String::new();
    for m in &msgs {
        input.push_str(&m.to_string());
        input.push('\n');
    }
    input.push_str("not json\n");
    let (writer, mut reader) = tokio::io::duplex(1 << 20);
    serve_stdio_with(f.cm.clone(), input.as_bytes(), writer, 64 * 1024 * 1024).await.unwrap();
    let mut out = String::new();
    reader.read_to_string(&mut out).await.unwrap();
    let lines: Vec<Value> = out.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    let by_id = |id: Value| lines.iter().find(|l| l["id"] == id).unwrap().clone();
    assert_eq!(by_id(json!(1))["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION);
    assert_eq!(by_id(json!(2))["result"]["structuredContent"]["count"], 1);
    assert_eq!(by_id(Value::Null)["error"]["code"], -32700);
    assert_eq!(lines.len(), 3, "notifications produce no output line");
}

// ---------------------------------------------------------------------------------------- HTTP

async fn send(
    app: &axum::Router,
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
    body: Option<String>,
) -> (u16, axum::http::HeaderMap, String) {
    let mut b = Request::builder().method(method).uri(path).header("Host", "127.0.0.1:8765");
    for (k, v) in headers {
        b = b.header(*k, *v);
    }
    let req = b.body(body.map(Body::from).unwrap_or_else(Body::empty)).unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status().as_u16();
    let headers = resp.headers().clone();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (status, headers, String::from_utf8_lossy(&bytes).into_owned())
}

async fn post_json(
    app: &axum::Router,
    path: &str,
    headers: &[(&str, &str)],
    body: Value,
) -> (u16, axum::http::HeaderMap, String) {
    let mut h = vec![("Content-Type", "application/json")];
    h.extend_from_slice(headers);
    send(app, "POST", path, &h, Some(body.to_string())).await
}

fn hdr(h: &axum::http::HeaderMap, k: &str) -> String {
    h.get(k).map(|v| v.to_str().unwrap().to_string()).unwrap_or_default()
}

async fn http_fx() -> (Fx, axum::Router) {
    let f = fx();
    seed(&f.cm).await;
    let app = create_app(f.cm.clone(), ServerOptions { mcp_token: Some("secret".into()), ..Default::default() });
    (f, app)
}

#[tokio::test]
async fn test_auth_session_and_json_response() {
    let (_f, app) = http_fx().await;
    let init = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}});
    let (st, h, _) = post_json(&app, "/mcp", &[], init.clone()).await;
    assert_eq!(st, 401);
    assert!(hdr(&h, "WWW-Authenticate").contains("Bearer"));
    let auth = [("Authorization", "Bearer secret"), ("Accept", "application/json, text/event-stream")];
    let (st, h, body) = post_json(&app, "/mcp", &auth, init).await;
    assert_eq!(st, 200);
    let sid = hdr(&h, "Mcp-Session-Id");
    assert!(!sid.is_empty());
    assert_eq!(hdr(&h, "MCP-Protocol-Version"), LATEST_PROTOCOL_VERSION);
    let body: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(body["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION);
    let with_sid = [auth[0], auth[1], ("Mcp-Session-Id", sid.as_str())];
    let (st, _, _) =
        post_json(&app, "/mcp", &with_sid, json!({"jsonrpc": "2.0", "method": "notifications/initialized"})).await;
    assert_eq!(st, 202);
    let (_, h, body) = post_json(
        &app,
        "/mcp",
        &with_sid,
        json!({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"query": "Luna"}}}),
    )
    .await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["result"]["structuredContent"]["count"], 1);
    assert_eq!(hdr(&h, "Mcp-Session-Id"), sid);
    let (st, _, _) = send(&app, "DELETE", "/mcp", &with_sid, None).await;
    assert_eq!(st, 204);
}

#[tokio::test]
async fn test_sse_get_and_method_semantics() {
    let (_f, app) = http_fx().await;
    let h = [("Authorization", "Bearer secret"), ("Accept", "text/event-stream")];
    let (st, headers, text) = post_json(&app, "/mcp", &h, json!({"jsonrpc": "2.0", "id": 9, "method": "ping"})).await;
    assert_eq!(st, 200);
    assert!(hdr(&headers, "content-type").starts_with("text/event-stream"));
    assert!(text.starts_with("event: message\ndata: "));
    let data: Value = serde_json::from_str(text.split_once("data: ").unwrap().1.trim()).unwrap();
    assert_eq!(data["id"], 9);
    let (st, _, _) =
        send(&app, "GET", "/mcp", &[("Authorization", "Bearer secret"), ("Accept", "application/json")], None).await;
    assert_eq!(st, 405);
    let (st, _, body) = send(
        &app,
        "POST",
        "/mcp",
        &[("Authorization", "Bearer secret"), ("Content-Type", "application/json")],
        Some("{not json".into()),
    )
    .await;
    assert_eq!(st, 400);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["error"]["code"], -32700);
    // CORS is off by default: a cross-site browser page gets no allow headers (and no preflight)
    let (st, _, _) = send(&app, "OPTIONS", "/mcp", &[("Origin", "https://evil.example")], None).await;
    assert_eq!(st, 403);
    let (_, headers, _) = send(&app, "GET", "/api/status", &[("Origin", "https://evil.example")], None).await;
    assert!(headers.get("Access-Control-Allow-Origin").is_none());
    // the keep-alive stream opens with a comment frame
    let req = Request::builder()
        .method("GET")
        .uri("/mcp")
        .header("Host", "127.0.0.1:8765")
        .header("Authorization", "Bearer secret")
        .header("Accept", "text/event-stream")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status().as_u16(), 200);
    let mut body = resp.into_body();
    let frame = body.frame().await.unwrap().unwrap().into_data().unwrap();
    assert_eq!(&frame[..], b": keep-alive\n\n");
}

#[tokio::test]
async fn test_dashboard_and_api() {
    let (_f, app) = http_fx().await;
    let (st, _, html) = send(&app, "GET", "/", &[], None).await;
    assert_eq!(st, 200);
    assert!(html.contains("Pentagon grade"));
    let (_, _, body) = send(&app, "GET", "/api/status", &[], None).await;
    let st: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(st["memories"]["by_status"]["active"], 2);
    assert!(st.get("grade").is_some());
    let (code, _, body) = post_json(
        &app,
        "/api/messages",
        &[],
        json!({"chat_id": "api", "speaker": "Ali", "text": "I adopted a dog called Rex in Berlin."}),
    )
    .await;
    assert_eq!(code, 202);
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["queued"], true);
    let (code, _, _) =
        post_json(&app, "/api/messages", &[], json!({"chat_id": "api", "speaker": "", "text": "x"})).await;
    assert_eq!(code, 400);
    let (_, _, body) = post_json(
        &app,
        "/api/messages/batch",
        &[],
        json!({"chat_id": "api", "messages": [{"speaker": "Ali", "text": "I also like tea."}]}),
    )
    .await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["queued"], 1);
    let (_, _, body) = send(&app, "GET", "/api/search?q=Luna", &[], None).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["results"].as_array().unwrap().len(), 1);
    let (code, _, _) = send(&app, "GET", "/api/search", &[], None).await;
    assert_eq!(code, 400);
    let (_, _, body) = send(&app, "GET", "/api/context?q=Berlin", &[], None).await;
    assert!(serde_json::from_str::<Value>(&body).unwrap()["context"].as_str().unwrap().contains("Berlin"));
    let (_, _, body) = send(&app, "GET", "/api/profile/Ali", &[], None).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["memory_count"], 2);
    let (_, _, body) = send(&app, "GET", "/api/entities", &[], None).await;
    let names: Vec<String> = serde_json::from_str::<Value>(&body).unwrap()["entities"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["name"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(names, ["Ali"]);
    let (code, _, body) =
        post_json(&app, "/api/remember", &[], json!({"text": "Ali likes jazz.", "subject": "Ali"})).await;
    assert_eq!(code, 201);
    let mid = serde_json::from_str::<Value>(&body).unwrap()["memory"]["memory_id"].clone();
    let (_, _, body) = post_json(&app, "/api/forget", &[], json!({"memory_id": mid})).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["retracted"], true);
    let (_, _, body) = send(&app, "GET", "/api/memories?subject=Ali", &[], None).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["memories"].as_array().unwrap().len(), 2);
    let (_, _, body) = send(&app, "GET", "/healthz", &[], None).await;
    assert_eq!(serde_json::from_str::<Value>(&body).unwrap()["ok"], true);
}

#[tokio::test]
async fn test_cors_allow_list_and_host_check() {
    let (f, _) = http_fx().await;
    let app = create_app(
        f.cm.clone(),
        ServerOptions {
            cors_origins: Some(vec!["https://claude.ai".into()]),
            allowed_hosts: Some(vec!["localhost".into(), "127.0.0.1".into()]),
            ..Default::default()
        },
    );
    let (st, h, _) = send(&app, "OPTIONS", "/mcp", &[("Origin", "https://claude.ai")], None).await;
    assert_eq!(st, 204);
    assert_eq!(hdr(&h, "Access-Control-Allow-Origin"), "https://claude.ai");
    assert!(hdr(&h, "Access-Control-Allow-Headers").contains("Mcp-Session-Id"));
    let (_, h, _) = send(&app, "GET", "/api/status", &[("Origin", "https://other.example")], None).await;
    assert!(h.get("Access-Control-Allow-Origin").is_none());
    // DNS rebinding: a request that reached us with a foreign Host header is refused
    let req = |host: &str| {
        Request::builder().method("GET").uri("/api/status").header("Host", host).body(Body::empty()).unwrap()
    };
    assert_eq!(app.clone().oneshot(req("attacker.example")).await.unwrap().status().as_u16(), 421);
    assert_eq!(app.clone().oneshot(req("localhost:8765")).await.unwrap().status().as_u16(), 200);
}

#[tokio::test]
async fn test_api_token_when_configured() {
    let (f, _) = http_fx().await;
    let app = create_app(f.cm.clone(), ServerOptions { api_token: Some("api-secret".into()), ..Default::default() });
    assert_eq!(send(&app, "GET", "/api/status", &[], None).await.0, 401);
    assert_eq!(send(&app, "GET", "/api/status", &[("Authorization", "Bearer api-secret")], None).await.0, 200);
    assert_eq!(send(&app, "GET", "/", &[], None).await.0, 200, "the dashboard page itself is not gated");
}

#[tokio::test]
async fn test_real_socket_server_round_trip() {
    let (f, _) = http_fx().await;
    let handle =
        neuralgraph_memory::server::run_server(f.cm.clone(), "127.0.0.1", 0, ServerOptions::default()).await.unwrap();
    let url = format!("http://127.0.0.1:{}/healthz", handle.addr.port());
    let body: Value = reqwest::get(&url).await.unwrap().json().await.unwrap();
    assert_eq!(body["ok"], true);
    handle.cleanup().await;
}
