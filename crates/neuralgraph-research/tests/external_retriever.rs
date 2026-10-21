//! Parity of `external_retriever` with Python (`tests/golden/external_retriever.json`, written by
//! `gen_external_retriever.py`). The Wikipedia retriever runs against a local HTTP server serving the same
//! canned API responses the generator served; the decoded request parameters it records must match Python's.

mod common;

use common::{assert_json_eq, load, pin_now, s, FIXED_NOW};
use neuralgraph_research::retrieval::external_retriever::{
    create_default_registry, create_wikipedia_retriever, is_open_domain_query, ExternalResult, ExternalRetriever,
    ExternalRetrieverRegistry, OpenDomainTrace, OpenDomainTracer, PersistentExternalCache, WikipediaConfig,
    WikipediaRetriever,
};
use serde_json::{json, Map, Value};
use std::io::{Read, Write};
use std::rc::Rc;
use std::sync::{Arc, Mutex};

#[test]
fn open_domain_detection_matches() {
    let f = load("external_retriever.json");
    for (q, expected) in f["is_open_domain"].as_object().unwrap() {
        let (open, conf) = is_open_domain_query(q);
        assert_eq!(json!([open, conf]), *expected, "{q:?}");
    }
}

#[test]
fn tracer_matches() {
    pin_now(FIXED_NOW);
    let f = load("external_retriever.json");
    let mut t = OpenDomainTracer::new(3);
    let mut out = Map::new();
    out.insert("stats_empty".into(), Value::Object(t.get_stats()));
    let mut a = OpenDomainTrace::new("What is gravity?");
    a.is_open_domain = true;
    a.open_domain_confidence = 0.8;
    let mut b = OpenDomainTrace::new("When did she go?");
    b.session_hits_at_threshold = 4;
    b.session_candidates_count = 10;
    let mut c = OpenDomainTrace::new("Capital of France");
    c.is_open_domain = true;
    c.external_retriever_called = true;
    c.external_in_top_k = 2;
    c.session_hits_at_threshold = 1;
    c.external_retrieval_latency_ms = 12.5;
    let mut d = OpenDomainTrace::new("Who is he?");
    d.external_retriever_called = true;
    let mut e = OpenDomainTrace::new("x".repeat(80));
    e.is_open_domain = true;
    e.strategy_used = "open_domain".into();
    for trace in [a, b, c, d, e] {
        t.record_trace(trace);
    }
    out.insert("stats".into(), Value::Object(t.get_stats()));
    out.insert("recent".into(), json!(t.get_recent_traces(2)));
    out.insert("recent_all".into(), json!(t.get_recent_traces(10)));
    assert_json_eq(&Value::Object(out), &f["tracer"], "tracer");
}

#[test]
fn results_serialise_like_python() {
    pin_now(FIXED_NOW);
    let f = load("external_retriever.json");
    let mut r = ExternalResult::new("Paris is the capital of France.", "wikipedia");
    r.title = Some("Paris".into());
    r.relevance_score = 0.9;
    r.metadata = json!({"page_id": "1"}).as_object().unwrap().clone();
    let d = r.to_dict();
    let back = ExternalResult::from_dict(d.as_object().unwrap()).unwrap();
    let minimal =
        ExternalResult::from_dict(json!({"content": "ünïcode", "source": "web"}).as_object().unwrap()).unwrap();
    let keys: Vec<String> = ["", "a", "ünïcode ✓"].iter().map(|c| ExternalResult::new(*c, "s").cache_key()).collect();
    let actual = json!({"to_dict": d, "roundtrip": back.to_dict(), "minimal": minimal.to_dict(), "keys": keys});
    assert_json_eq(&actual, &f["results"], "results");
    assert!(ExternalResult::from_dict(json!({"source": "x"}).as_object().unwrap()).is_err());
}

fn dir_files(dir: &std::path::Path) -> Value {
    let mut names: Vec<String> =
        std::fs::read_dir(dir).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
    names.sort();
    Value::Object(
        names.into_iter().map(|n| (n.clone(), json!(std::fs::read_to_string(dir.join(&n)).unwrap()))).collect(),
    )
}

#[test]
fn persistent_cache_matches() {
    pin_now(FIXED_NOW);
    let f = load("external_retriever.json");
    let tmp = tempfile::tempdir().unwrap();
    let mut cache = PersistentExternalCache::new(Some(tmp.path())).unwrap();
    let mut out = Map::new();
    out.insert("get_missing".into(), json!(cache.get("What is rain?").map(|_| ())));
    let mut r1 = ExternalResult::new("Rain is liquid water.", "wikipedia");
    r1.title = Some("Rain".into());
    r1.relevance_score = 1.0;
    let mut r2 = ExternalResult::new("Clouds…", "wikipedia");
    r2.metadata = json!({"n": 2}).as_object().unwrap().clone();
    cache.put("What is Rain?", &[r1, r2]);
    out.insert("files_after_put".into(), dir_files(tmp.path()));
    let dicts = |v: Option<Vec<ExternalResult>>| json!(v.unwrap().iter().map(|r| r.to_dict()).collect::<Vec<_>>());
    out.insert("get_hit".into(), dicts(cache.get("WHAT IS RAIN?")));
    let mut reopened = PersistentExternalCache::new(Some(tmp.path())).unwrap();
    out.insert("reopened_hit".into(), dicts(reopened.get("what is rain?")));
    out.insert("cleared".into(), json!(reopened.clear()));
    out.insert("files_after_clear".into(), dir_files(tmp.path()));
    out.insert("get_after_clear".into(), json!(reopened.get("what is rain?").map(|_| ())));
    assert_json_eq(&Value::Object(out), &f["persistent"], "persistent cache");
}

/// A tiny HTTP/1.1 server answering like the generator's canned Wikipedia API; records each request.
fn serve(requests: Arc<Mutex<Vec<Value>>>, search: Value, pages: Value) -> String {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let mut buf = Vec::new();
            let mut chunk = [0u8; 4096];
            while !buf.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break;
                }
                buf.extend_from_slice(&chunk[..n]);
            }
            let head = String::from_utf8_lossy(&buf).into_owned();
            let target = head.split_whitespace().nth(1).unwrap_or("/").to_string();
            let url = reqwest::Url::parse(&format!("http://x{target}")).unwrap();
            let params: Vec<(String, String)> =
                url.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
            let ua = head
                .lines()
                .find_map(|l| l.strip_prefix("user-agent: ").or_else(|| l.strip_prefix("User-Agent: ")))
                .map(str::to_string);
            requests.lock().unwrap().push(json!({"path": url.path(), "params": params, "ua": ua}));
            let get = |k: &str| params.iter().find(|(p, _)| p == k).map(|(_, v)| v.as_str());
            let (status, body) = if get("srsearch") == Some("fail") {
                ("500 Internal Server Error", None)
            } else if get("list") == Some("search") {
                if get("srsearch") == Some("nothing") {
                    ("200 OK", Some(json!({"query": {"search": []}})))
                } else {
                    ("200 OK", Some(search.clone()))
                }
            } else if get("meta") == Some("siteinfo") {
                ("200 OK", Some(json!({"query": {"general": {}}})))
            } else {
                ("200 OK", Some(pages.clone()))
            };
            let response = match body {
                Some(b) => {
                    let raw = neuralgraph_memory::pycompat::dumps(
                        &b,
                        &neuralgraph_memory::pycompat::JsonFormat::python_default(),
                    );
                    format!(
                        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{raw}",
                        raw.len()
                    )
                }
                None => format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
            };
            let _ = stream.write_all(response.as_bytes());
        }
    });
    base
}

fn dicts(v: &[ExternalResult]) -> Value {
    json!(v.iter().map(|r| r.to_dict()).collect::<Vec<_>>())
}

#[tokio::test]
async fn wikipedia_retriever_matches() {
    pin_now(FIXED_NOW);
    let f = load("external_retriever.json");
    let expected = &f["wikipedia"];
    let search = json!({"query": {"search": [
        {"pageid": 101, "title": "Photosynthesis", "snippet": "process <b>plants</b>", "wordcount": 9000},
        {"pageid": 202, "title": "Chlorophyll", "snippet": "green pigment"},
        {"pageid": 303, "title": "Empty page", "snippet": "", "wordcount": 3},
        {"pageid": 404, "title": "Light", "wordcount": 12},
    ]}});
    let pages = json!({"query": {"pages": {
        "101": {"pageid": 101, "extract": "Photosynthesis is a process used by plants…", "fullurl": "https://en.wikipedia.org/wiki/Photosynthesis"},
        "202": {"pageid": 202, "extract": "Chlorophyll is a green pigment.", "fullurl": "https://en.wikipedia.org/wiki/Chlorophyll"},
        "303": {"pageid": 303, "extract": ""},
        "404": {"pageid": 404, "extract": "Light is radiation."},
    }}});
    let requests = Arc::new(Mutex::new(Vec::new()));
    let base = serve(requests.clone(), search, pages);
    let api_url = format!("{base}/w/api.php");
    let config = WikipediaConfig { api_url: api_url.clone(), ..WikipediaConfig::default() };
    let w = create_wikipedia_retriever(Some(config.clone()));

    let mut out = Map::new();
    out.insert("source_name".into(), json!(w.source_name()));
    out.insert("supports_embedding_search".into(), json!(w.supports_embedding_search()));
    out.insert("available".into(), json!(w.is_available().await));
    requests.lock().unwrap().clear();
    let first = w.retrieve("What is Photosynthesis?", None, 3, 2000.0).await.unwrap();
    out.insert("first".into(), dicts(&first));
    out.insert("first_requests".into(), json!(std::mem::take(&mut *requests.lock().unwrap())));
    let again = w.retrieve("  what is photosynthesis?  ", None, 1, 2000.0).await.unwrap();
    out.insert("cached".into(), dicts(&again));
    out.insert("cached_requests".into(), json!(std::mem::take(&mut *requests.lock().unwrap())));
    out.insert("nothing".into(), dicts(&w.retrieve("nothing", None, 5, 2000.0).await.unwrap()));
    out.insert("fail".into(), dicts(&w.retrieve("fail", None, 5, 2000.0).await.unwrap()));
    out.insert("last_error_set".into(), json!(w.last_error().is_some()));

    let mut registry = ExternalRetrieverRegistry::new();
    registry.register(Rc::new(WikipediaRetriever::new(Some(config))), true);
    let names: Vec<String> = registry.get_enabled_retrievers().iter().map(|r| r.source_name()).collect();
    out.insert("registry_enabled".into(), json!(names));
    let all = registry.retrieve_all("What is Photosynthesis?", None, 2, 3000.0).await;
    out.insert("registry_all".into(), Value::Object(all.iter().map(|(k, v)| (k.clone(), dicts(v))).collect()));
    registry.set_enabled("wikipedia", false);
    out.insert("registry_disabled".into(), json!(registry.retrieve_all("x", None, 5, 3000.0).await.len()));
    out.insert("unregister".into(), json!([registry.unregister("wikipedia"), registry.unregister("wikipedia")]));
    let down = WikipediaRetriever::new(Some(WikipediaConfig {
        api_url: "http://127.0.0.1:9/w/api.php".into(),
        ..WikipediaConfig::default()
    }));
    out.insert("down_available".into(), json!([down.is_available().await, down.is_available().await]));
    out.insert("down_retrieve".into(), dicts(&down.retrieve("q", None, 5, 2000.0).await.unwrap()));

    let mut expected = expected.clone();
    expected["registry_disabled"] = json!(expected["registry_disabled"].as_object().unwrap().len());
    assert_json_eq(&Value::Object(out), &expected, "wikipedia");
    assert_eq!(create_default_registry().get_enabled_retrievers().len(), 1);
    let cfg = WikipediaConfig::default();
    let fc = &f["config"];
    assert_eq!(cfg.api_url, s(&fc["api_url"]));
    assert_eq!(json!(cfg.search_limit), fc["search_limit"]);
    assert_eq!(json!(cfg.extract_chars), fc["extract_chars"]);
    assert_eq!(json!(cfg.timeout_seconds), fc["timeout_seconds"]);
    assert_eq!(json!(cfg.cache_ttl_hours), fc["cache_ttl_hours"]);
    assert_eq!(cfg.user_agent, s(&fc["user_agent"]));
}
