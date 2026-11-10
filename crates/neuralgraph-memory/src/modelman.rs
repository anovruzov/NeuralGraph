//! Model manager: what the web app's Models page shows and does.
//!
//! One overview of the model server (LM Studio or Ollama): every model on disk with its size, what it can do, whether
//! it is in memory right now and which job NeuralGraph gives it; loading and unloading; downloads with progress; and
//! freeing the chat model's memory after it has sat idle.

use crate::service::ChatMemory;
use crate::setup;
use indexmap::IndexMap;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::{LazyLock, Mutex};
use std::time::Duration;

/// Minutes of idleness after which the chat model is unloaded when nothing else is configured.
pub const DEFAULT_IDLE_UNLOAD_MIN: u64 = 15;

/// `$NEURALGRAPH_IDLE_UNLOAD_MIN` (0 = never), else the default.
pub fn idle_unload_minutes() -> u64 {
    std::env::var("NEURALGRAPH_IDLE_UNLOAD_MIN")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(DEFAULT_IDLE_UNLOAD_MIN)
}

fn is_ollama(base: &str) -> bool {
    crate::backend::backend_for(base) == "ollama"
}

/// Ids of the models in memory right now.
pub async fn loaded(base: &str) -> HashSet<String> {
    let http = reqwest::Client::new();
    let base = base.trim_end_matches('/');
    let get = |url: String| {
        let http = http.clone();
        async move { http.get(url).timeout(Duration::from_secs(5)).send().await.ok()?.json::<Value>().await.ok() }
    };
    let mut out = HashSet::new();
    if is_ollama(base) {
        let v = get(format!("{base}/api/ps")).await.unwrap_or(Value::Null);
        for m in v["models"].as_array().into_iter().flatten() {
            if let Some(n) = m["name"].as_str() {
                out.insert(n.to_string());
            }
        }
    } else {
        let v = get(format!("{base}/api/v0/models")).await.unwrap_or(Value::Null);
        for m in v["data"].as_array().into_iter().flatten() {
            if m["state"] == "loaded" {
                if let Some(n) = m["id"].as_str() {
                    out.insert(n.to_string());
                }
            }
        }
    }
    out
}

/// Everything the Models page needs.
pub async fn overview(cm: &ChatMemory) -> Value {
    let llm = cm.control.current();
    let (base, chat, embed) = (llm.base_url(), llm.model(), llm.embed_model());
    let vision = crate::vision::configured_vision_model();
    let servers = setup::discover_servers(Some(base.clone())).await;
    let cur = servers.iter().find(|s| s["url"] == base.as_str()).cloned();
    let in_memory = loaded(&base).await;
    let mut models: Vec<Value> = Vec::new();
    // model files in memory: everything loaded, and the part held by models NeuralGraph has no job for
    let (mut ram_now, mut ram_other) = (0.0, 0.0);
    if let Some(s) = &cur {
        let sees: HashSet<&str> =
            s["models"]["vision"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        for (kind, key) in [("chat", "chat"), ("embedding", "embedding")] {
            for id in s["models"][key].as_array().into_iter().flatten().filter_map(Value::as_str) {
                let size = s["sizes"][id].as_f64();
                let is_loaded = in_memory.contains(id);
                let mut roles = Vec::new();
                if id == chat {
                    roles.push("chat");
                }
                if id == embed {
                    roles.push("embedding");
                }
                if vision.as_deref() == Some(id) || (id == chat && sees.contains(id) && vision.is_none()) {
                    roles.push("image");
                }
                if is_loaded {
                    ram_now += size.unwrap_or(0.0);
                    if roles.is_empty() {
                        ram_other += size.unwrap_or(0.0);
                    }
                }
                models.push(json!({
                    "id": id, "kind": kind, "vision": sees.contains(id), "size_gb": size, "loaded": is_loaded, "roles": roles,
                }));
            }
        }
    }
    json!({
        "server": cur.as_ref().map(|s| json!({"kind": s["kind"], "label": s["label"], "url": s["url"]})),
        "servers": servers.iter().map(|s| json!({"kind": s["kind"], "label": s["label"], "url": s["url"]})).collect::<Vec<_>>(),
        "configured_url": base,
        "roles": {"chat": chat, "embedding": embed, "image": vision},
        "models": models,
        "in_memory_gb": ram_now,
        "in_use_gb": ram_now - ram_other,
        "other_gb": ram_other,
        "suggested": setup::suggested_models(&base).await,
        "downloads": downloads(),
        "system": setup::system_info(),
        "idle_unload_minutes": idle_unload_minutes(),
        "paused": cm.store.is_paused(),
        "can_delete": is_ollama(&base),
    })
}

// ------------------------------------------------------------------------------------------------ downloads

#[derive(Clone, Debug)]
struct Job {
    status: &'static str, // running | done | failed
    percent: Option<f64>,
    detail: String,
}

static JOBS: Mutex<Option<IndexMap<String, Job>>> = Mutex::new(None);
static PERCENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(\d{1,3}(?:\.\d+)?)\s?%").unwrap());
static ANSI: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\x1b\[[0-9;?]*[A-Za-z]").unwrap());

fn set_job(name: &str, f: impl FnOnce(&mut Job)) {
    let mut g = JOBS.lock().unwrap_or_else(|e| e.into_inner());
    let jobs = g.get_or_insert_with(IndexMap::new);
    let job = jobs.entry(name.to_string()).or_insert(Job { status: "running", percent: None, detail: String::new() });
    f(job);
}

/// Running and finished downloads of this session.
pub fn downloads() -> Vec<Value> {
    let g = JOBS.lock().unwrap_or_else(|e| e.into_inner());
    g.iter()
        .flat_map(|j| j.iter())
        .map(|(n, j)| json!({"name": n, "status": j.status, "percent": j.percent, "detail": j.detail}))
        .collect()
}

/// Start downloading `name` in the background (`lms get` for LM Studio, the pull API for Ollama).
pub fn start_download(base: &str, name: &str) -> Result<(), String> {
    let name = name.trim().to_string();
    if name.is_empty() || name.starts_with('-') || name.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err("give the model's name, e.g. qwen2.5:7b-instruct or google/gemma-4-e4b".into());
    }
    {
        let g = JOBS.lock().unwrap_or_else(|e| e.into_inner());
        if g.as_ref().and_then(|j| j.get(&name)).is_some_and(|j| j.status == "running") {
            return Err(format!("{name} is already downloading"));
        }
    }
    set_job(&name, |j| *j = Job { status: "running", percent: Some(0.0), detail: "starting…".into() });
    let base = base.trim_end_matches('/').to_string();
    tokio::spawn(async move {
        let result = if is_ollama(&base) { pull_ollama(&base, &name).await } else { get_lmstudio(&name).await };
        set_job(&name, |j| match result {
            Ok(()) => *j = Job { status: "done", percent: Some(100.0), detail: "downloaded".into() },
            Err(e) => {
                j.status = "failed";
                j.detail = e;
            }
        });
    });
    Ok(())
}

async fn pull_ollama(base: &str, name: &str) -> Result<(), String> {
    let mut resp = reqwest::Client::new()
        .post(format!("{base}/api/pull"))
        .json(&json!({"model": name, "stream": true}))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("Ollama answered HTTP {}", resp.status().as_u16()));
    }
    let mut buf = String::new();
    let mut last_error = None;
    while let Some(chunk) = resp.chunk().await.map_err(|e| e.to_string())? {
        buf.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(nl) = buf.find('\n') {
            let line: String = buf.drain(..=nl).collect();
            let Ok(v) = serde_json::from_str::<Value>(line.trim()) else { continue };
            if let Some(e) = v["error"].as_str() {
                last_error = Some(e.to_string());
            }
            let pct = match (v["completed"].as_f64(), v["total"].as_f64()) {
                (Some(c), Some(t)) if t > 0.0 => Some(c / t * 100.0),
                _ => None,
            };
            let status = v["status"].as_str().unwrap_or("").to_string();
            set_job(name, |j| {
                if pct.is_some() {
                    j.percent = pct;
                }
                if !status.is_empty() {
                    j.detail = status;
                }
            });
        }
    }
    match last_error {
        Some(e) => Err(e),
        None => Ok(()),
    }
}

async fn get_lmstudio(name: &str) -> Result<(), String> {
    use tokio::io::AsyncReadExt;
    let lms = setup::lms_path()
        .ok_or("LM Studio's `lms` command was not found; download the model in LM Studio's search tab")?;
    let mut child = tokio::process::Command::new(lms)
        .args(["get", name, "--yes"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut out = child.stdout.take().ok_or("no output from lms")?;
    let mut err = child.stderr.take();
    let mut buf = [0u8; 4096];
    let mut tail = String::new();
    loop {
        let n = out.read(&mut buf).await.map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        tail.push_str(&String::from_utf8_lossy(&buf[..n]));
        let clean = ANSI.replace_all(&tail, "").replace('\r', "\n");
        let last = clean.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim().to_string();
        let pct = PERCENT.captures_iter(&clean).last().and_then(|c| c[1].parse::<f64>().ok());
        set_job(name, |j| {
            if let Some(p) = pct.filter(|p| *p <= 100.0) {
                j.percent = Some(p);
            }
            if !last.is_empty() {
                j.detail = last.chars().take(120).collect();
            }
        });
        if tail.len() > 16_384 {
            tail = tail.split_off(tail.len() - 4096);
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    if status.success() {
        return Ok(());
    }
    let mut why = String::new();
    if let Some(e) = err.as_mut() {
        let _ = e.read_to_string(&mut why).await;
    }
    let why = ANSI.replace_all(&why, "").trim().lines().last().unwrap_or("").to_string();
    Err(if why.is_empty() { "lms could not download that model (check the name)".into() } else { why })
}

/// Remove a model from disk (Ollama only; LM Studio removes models in its own window).
pub async fn delete(base: &str, id: &str) -> Result<(), String> {
    if !is_ollama(base) {
        return Err("LM Studio removes models in its own window (My Models → ⋯ → Delete)".into());
    }
    let r = reqwest::Client::new()
        .delete(format!("{}/api/delete", base.trim_end_matches('/')))
        .json(&json!({"model": id}))
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if r.status().is_success() {
        Ok(())
    } else {
        Err(format!("Ollama answered HTTP {}", r.status().as_u16()))
    }
}

// ------------------------------------------------------------------------------------------------ idle unload

/// Unload the chat model (and a separate image model) when nothing has used it for `minutes`. LM Studio reports each
/// loaded model's last use, so a model you are using yourself is left alone; Ollama unloads idle models on its own.
pub async fn unload_if_idle(cm: &ChatMemory, minutes: u64) -> Option<String> {
    if minutes == 0 || cm.store.is_paused() || cm.worker.metrics().busy_workers > 0 {
        return None;
    }
    let llm = cm.control.current();
    let base = llm.base_url();
    if base.is_empty() || is_ollama(&base) {
        return None;
    }
    let lms = setup::lms_path()?;
    let out = tokio::process::Command::new(&lms).args(["ps", "--json"]).output().await.ok()?;
    let v: Value = serde_json::from_slice(&out.stdout).ok()?;
    let now_ms = chrono::Utc::now().timestamp_millis() as f64;
    let mine: Vec<String> =
        [Some(llm.model()), crate::vision::configured_vision_model()].into_iter().flatten().collect();
    let mut freed = Vec::new();
    for m in v.as_array().into_iter().flatten() {
        let id = m["identifier"].as_str().unwrap_or("");
        let idle_ms = now_ms - m["lastUsedTime"].as_f64().unwrap_or(now_ms);
        if mine.iter().any(|x| x == id) && m["status"] == "idle" && idle_ms >= (minutes * 60_000) as f64 {
            let steps = crate::control::unload_models(&base, &[id.to_string()]).await;
            if steps.iter().all(|s| s.ok) {
                freed.push(id.to_string());
            }
        }
    }
    (!freed.is_empty()).then(|| freed.join(", "))
}
