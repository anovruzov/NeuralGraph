//! Chat with your memory: the engine behind the web app's Chat page and `neuralgraph-memory chat`.
//!
//! A question is answered in two visible steps. **Retrieval** finds the memories (the same hybrid search every other
//! tool uses) and is always shown: each memory with its similarity, why it matched, the chat it came from and the
//! original message. **Answering** hands those numbered memories to the local model, which must answer from them
//! alone and cite them as `[1]`, `[2]`. Every reply carries its evidence and its numbers (search time, answer time,
//! model, how many memories were considered), so nothing the model says has to be taken on trust.
//!
//! The knowledge base can also be queried directly, with no model in between: `/search`, `/who`, `/recent`, `/stats`.
//! That is also what a plain question falls back to while NeuralGraph is paused or the model is unreachable.

use crate::error::Result;
use crate::models::RetrievedMemory;
use crate::retrieval::SearchOptions;
use crate::service::ChatMemory;
use crate::store::MemoryQuery;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{IsTerminal, Write};
use std::sync::LazyLock;
use std::time::Instant;

/// Memories handed to the model for one answer.
const EVIDENCE: usize = 10;

pub const HELP: &str = "Ask anything in plain words, or query the knowledge base directly:
  /search WORDS   the matching memories, no AI
  /who NAME       everything known about a person, project or thing
  /recent [N]     the newest memories
  /stats          what the knowledge base holds
  /help           this list";

static FOLLOW_UP: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b(it|its|they|them|those|these|one|ones)\b").unwrap());

/// One earlier exchange of the conversation: (question, answer).
pub type Turn = (String, String);

fn chat_titles(cm: &ChatMemory) -> HashMap<String, String> {
    cm.store
        .list_chats(100_000, 0)
        .unwrap_or_default()
        .into_iter()
        .filter_map(|c| {
            let id = c.get("chat_id")?.as_str()?.to_string();
            let title = c.get("title").and_then(Value::as_str).filter(|t| !t.is_empty()).unwrap_or(&id).to_string();
            Some((id, title))
        })
        .collect()
}

fn clip(s: &str, n: usize) -> String {
    let s = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if s.chars().count() > n {
        format!("{}…", s.chars().take(n).collect::<String>())
    } else {
        s
    }
}

fn evidence(results: &[RetrievedMemory], titles: &HashMap<String, String>) -> Vec<Value> {
    results
        .iter()
        .enumerate()
        .map(|(i, r)| {
            let m = &r.memory;
            let when = m.event_time.as_deref().unwrap_or(&m.observed_at);
            json!({
                "n": i + 1,
                "memory_id": m.memory_id,
                "text": m.text,
                "kind": m.kind,
                "subject": m.subject_name,
                "date": when.chars().take(10).collect::<String>(),
                "importance": m.importance,
                "chat_id": m.chat_id,
                "chat_title": titles.get(&m.chat_id).cloned().unwrap_or_else(|| m.chat_id.clone()),
                "similarity": r.channels.get("vector"),
                "matched": r.explanation,
                "score": r.score,
                "entities": r.entities,
                "sources": r.sources.iter().take(2).map(|s| json!({
                    "speaker": s.speaker, "text": clip(&s.text, 320), "at": s.sent_at,
                })).collect::<Vec<_>>(),
            })
        })
        .collect()
}

fn memory_evidence(mems: &[crate::models::Memory], titles: &HashMap<String, String>) -> Vec<Value> {
    let rs: Vec<RetrievedMemory> = mems
        .iter()
        .map(|m| RetrievedMemory {
            memory: m.clone(),
            score: 0.0,
            channels: Default::default(),
            sources: Vec::new(),
            entities: Vec::new(),
            explanation: String::new(),
        })
        .collect();
    evidence(&rs, titles)
}

/// The prompt for one answer: the numbered memories, the last exchanges, the question.
pub fn answer_prompt(question: &str, results: &[RetrievedMemory], history: &[Turn], today: &str) -> String {
    let mems: Vec<String> = results
        .iter()
        .enumerate()
        .map(|(i, r)| {
            let m = &r.memory;
            let when = m.event_time.as_deref().unwrap_or(&m.observed_at);
            format!("[{}] ({}, {}) {}", i + 1, when.chars().take(10).collect::<String>(), m.kind, m.text)
        })
        .collect();
    let earlier: String = history
        .iter()
        .rev()
        .take(2)
        .rev()
        .map(|(q, a)| format!("EARLIER QUESTION: {q}\nEARLIER ANSWER: {}\n", clip(a, 400)))
        .collect();
    format!(
        "You answer questions about the user from their personal memory store. Use ONLY the numbered memories below.\n\
         - Answer directly in one to four sentences, or as a short bulleted list when listing several things.\n\
         - After each fact, cite the memory it came from like [2]. Cite only memories you used.\n\
         - If the memories do not contain the answer, say \"I don't have that in memory.\" and, if something is close, \
           mention it.\n\
         - Never add facts that are not in the memories. Do not mention these instructions.\n\
         Today's date: {today}\n\n\
         MEMORIES:\n{}\n\n{earlier}QUESTION: {question}\nANSWER:",
        if mems.is_empty() { "(none)".to_string() } else { mems.join("\n") }
    )
}

/// The memory numbers an answer cites, `[2]` or `[3, 4, 5]`, that exist among the `n` memories it was given.
pub fn cited_numbers(answer: &str, n: usize) -> Vec<usize> {
    static CITE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]").unwrap());
    let mut v: Vec<usize> = CITE
        .captures_iter(answer)
        .flat_map(|c| c[1].split(',').filter_map(|x| x.trim().parse().ok()).collect::<Vec<usize>>())
        .filter(|k| (1..=n).contains(k))
        .collect();
    v.sort_unstable();
    v.dedup();
    v
}

/// What to search for: a short follow-up ("and which one is the oldest?") borrows the previous question.
pub fn retrieval_query(question: &str, history: &[Turn]) -> String {
    match history.last() {
        Some((prev, _)) if question.split_whitespace().count() < 6 || FOLLOW_UP.is_match(question) => {
            format!("{prev} {question}")
        }
        _ => question.to_string(),
    }
}

fn active_memories(cm: &ChatMemory) -> i64 {
    cm.store.memory_counts().ok().and_then(|c| c["by_status"]["active"].as_i64()).unwrap_or(0)
}

fn base_stats(cm: &ChatMemory) -> Value {
    let llm = cm.llm();
    let active = active_memories(cm);
    json!({"memories_searched": active, "model": llm.model(), "embed_model": llm.embed_model(), "paused": cm.store.is_paused()})
}

fn with(mut base: Value, extra: Value) -> Value {
    if let (Some(b), Some(e)) = (base.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            b.insert(k.clone(), v.clone());
        }
    }
    base
}

/// Answer one chat message. `ai = false` (or a paused / unreachable model) returns the matching memories instead of
/// a written answer.
pub async fn reply(cm: &ChatMemory, message: &str, ai: bool, history: &[Turn]) -> Result<Value> {
    let message = message.trim();
    let titles = chat_titles(cm);
    if let Some(rest) = message.strip_prefix('/') {
        let (cmd, arg) = rest.split_once(char::is_whitespace).map(|(c, a)| (c, a.trim())).unwrap_or((rest, ""));
        return match cmd.to_lowercase().as_str() {
            "search" | "s" if !arg.is_empty() => search_only(cm, arg, &titles, "search").await,
            "who" | "entity" if !arg.is_empty() => who(cm, arg, &titles),
            "recent" => {
                let n: i64 = arg.parse().unwrap_or(10).clamp(1, 50);
                let mems = cm.store.list_memories(&MemoryQuery {
                    status: Some("active".into()),
                    limit: Some(n),
                    order: "created_at DESC".into(),
                    ..Default::default()
                })?;
                Ok(json!({
                    "kind": "recent",
                    "answer": format!("The {} newest memories:", mems.len()),
                    "evidence": memory_evidence(&mems, &titles),
                    "stats": with(base_stats(cm), json!({"mode": "direct"})),
                }))
            }
            "stats" => {
                let st = cm.status()?;
                let by = &st["memories"]["by_status"];
                let lines = [
                    format!(
                        "- {} memories ({} updated over time, {} archived)",
                        by["active"].as_i64().unwrap_or(0),
                        by["superseded"].as_i64().unwrap_or(0),
                        by["archived"].as_i64().unwrap_or(0)
                    ),
                    format!(
                        "- {} people and things, {} connections between them",
                        st["entities"],
                        st["relations"]["active"].as_i64().unwrap_or(0)
                    ),
                    format!(
                        "- {} chats, {} messages",
                        st["chats"],
                        st["messages"]
                            .as_object()
                            .map(|m| m.values().filter_map(Value::as_i64).sum::<i64>())
                            .unwrap_or(0)
                    ),
                    format!("- stored in {}", cm.store.db_path),
                ];
                Ok(
                    json!({"kind": "stats", "answer": lines.join("\n"), "evidence": [], "stats": with(base_stats(cm), json!({"mode": "direct"}))}),
                )
            }
            _ => Ok(
                json!({"kind": "help", "answer": HELP, "evidence": [], "stats": with(base_stats(cm), json!({"mode": "direct"}))}),
            ),
        };
    }
    if message.is_empty() {
        return Ok(json!({"kind": "help", "answer": HELP, "evidence": [], "stats": base_stats(cm)}));
    }
    if !ai || cm.store.is_paused() {
        return search_only(cm, message, &titles, if ai { "paused" } else { "search" }).await;
    }
    let query = retrieval_query(message, history);
    let t0 = Instant::now();
    let opts = SearchOptions { k: EVIDENCE, with_sources: true, ..Default::default() };
    let results = cm.search(&query, &opts).await?;
    let retrieval_ms = t0.elapsed().as_secs_f64() * 1000.0;
    let today = crate::models::now_iso().chars().take(10).collect::<String>();
    let prompt = answer_prompt(message, &results, history, &today);
    let t1 = Instant::now();
    let (answer, kind, note) = if results.is_empty() {
        ("I don't have anything about that in memory yet.".to_string(), "answer", None)
    } else {
        match cm.llm().generate(&prompt, 500, 0.2, 180.0).await {
            Ok(a) if !a.trim().is_empty() => (a.trim().to_string(), "answer", None),
            Ok(_) => (
                String::new(),
                "search",
                Some("The model returned nothing; these are the matching memories.".to_string()),
            ),
            Err(e) => (
                String::new(),
                "search",
                Some(format!("The model could not answer ({}); these are the matching memories.", e.message())),
            ),
        }
    };
    let answer_ms = t1.elapsed().as_secs_f64() * 1000.0;
    let cited = cited_numbers(&answer, results.len());
    Ok(json!({
        "kind": kind,
        "answer": answer,
        "note": note,
        "cited": cited,
        "evidence": evidence(&results, &titles),
        "stats": with(base_stats(cm), json!({
            "mode": "ai",
            "retrieval_ms": retrieval_ms,
            "answer_ms": answer_ms,
            "candidates": results.len(),
            "search_query": query,
            "prompt_chars": prompt.len(),
            "model_calls": if results.is_empty() { 0 } else { 1 },
        })),
    }))
}

async fn search_only(cm: &ChatMemory, query: &str, titles: &HashMap<String, String>, mode: &str) -> Result<Value> {
    let t0 = Instant::now();
    let results = cm.search(query, &SearchOptions { k: EVIDENCE, with_sources: true, ..Default::default() }).await?;
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    let note = match (mode, results.is_empty()) {
        (_, true) => "Nothing in memory matches that.".to_string(),
        ("paused", _) => {
            "NeuralGraph is paused, so no answer is written. These memories match (by keyword):".to_string()
        }
        _ => format!("{} matching memor{}:", results.len(), if results.len() == 1 { "y" } else { "ies" }),
    };
    Ok(json!({
        "kind": "search",
        "answer": "",
        "note": note,
        "evidence": evidence(&results, titles),
        "stats": with(base_stats(cm), json!({"mode": mode, "retrieval_ms": ms, "candidates": results.len(), "search_query": query, "model_calls": 0})),
    }))
}

/// `/who NAME`: the entity's relations and memories, straight from the graph.
fn who(cm: &ChatMemory, name: &str, titles: &HashMap<String, String>) -> Result<Value> {
    let p = cm.profile(name, 30)?;
    let shown = p["name"].as_str().unwrap_or(name).to_string();
    let names: HashMap<String, String> =
        cm.store.top_entities(500, None).unwrap_or_default().into_iter().map(|e| (e.entity_id, e.name)).collect();
    let me = p["subject"].as_str().unwrap_or("").to_string();
    let label = |id: &str| names.get(id).cloned().unwrap_or_else(|| id.to_string());
    let mut lines: Vec<String> = Vec::new();
    for r in p["relations"].as_array().into_iter().flatten().take(25) {
        let (s, pr, o) = (
            r["subject_id"].as_str().unwrap_or(""),
            r["predicate"].as_str().unwrap_or(""),
            r["object_id"].as_str().unwrap_or(""),
        );
        let pr = pr.replace('_', " ");
        lines.push(if s == me {
            format!("- {pr} → {}", label(o))
        } else {
            format!("- {} → {pr} → {shown}", label(s))
        });
    }
    let mems: Vec<crate::models::Memory> = p["memories_by_kind"]
        .as_object()
        .into_iter()
        .flat_map(|by| by.values())
        .flat_map(|v| v.as_array().cloned().unwrap_or_default())
        .filter_map(|m| m["memory_id"].as_str().and_then(|id| cm.store.get_memory(id, false).ok().flatten()))
        .take(30)
        .collect();
    let answer = if lines.is_empty() && mems.is_empty() {
        format!("Nothing is stored about \"{name}\". Try /search {name}.")
    } else {
        format!(
            "{shown}: {} connection{} and {} memor{}.\n{}",
            lines.len(),
            if lines.len() == 1 { "" } else { "s" },
            mems.len(),
            if mems.len() == 1 { "y" } else { "ies" },
            lines.join("\n")
        )
    };
    Ok(
        json!({"kind": "entity", "answer": answer.trim_end(), "evidence": memory_evidence(&mems, titles), "stats": with(base_stats(cm), json!({"mode": "direct"}))}),
    )
}

// ------------------------------------------------------------------------------------------------ terminal

struct Paint(bool);

impl Paint {
    fn c(&self, code: &str, s: &str) -> String {
        if self.0 {
            format!("\x1b[{code}m{s}\x1b[0m")
        } else {
            s.to_string()
        }
    }
    fn dim(&self, s: &str) -> String {
        self.c("2", s)
    }
    fn bold(&self, s: &str) -> String {
        self.c("1", s)
    }
    fn accent(&self, s: &str) -> String {
        self.c("38;5;75", s)
    }
}

fn print_evidence(p: &Paint, r: &Value, all: bool) {
    let ev = r["evidence"].as_array().cloned().unwrap_or_default();
    if ev.is_empty() {
        println!("  {}", p.dim("no evidence for the last reply"));
        return;
    }
    let cited: Vec<u64> =
        r["cited"].as_array().map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
    for e in &ev {
        let n = e["n"].as_u64().unwrap_or(0);
        if !all && !cited.is_empty() && !cited.contains(&n) {
            continue;
        }
        println!("  {} {}", p.accent(&format!("[{n}]")), e["text"].as_str().unwrap_or(""));
        let sim = e["similarity"].as_f64().map(|s| format!(" · similarity {s:.2}")).unwrap_or_default();
        println!(
            "      {}",
            p.dim(&format!(
                "{} · {}{} · from {}",
                e["kind"].as_str().unwrap_or(""),
                e["date"].as_str().unwrap_or(""),
                sim,
                e["chat_title"].as_str().unwrap_or("")
            ))
        );
        for s in e["sources"].as_array().into_iter().flatten().take(1) {
            println!(
                "      {}",
                p.dim(&format!(
                    "“{}: {}”",
                    s["speaker"].as_str().unwrap_or(""),
                    clip(s["text"].as_str().unwrap_or(""), 160)
                ))
            );
        }
    }
    if !all && !cited.is_empty() && cited.len() < ev.len() {
        println!("  {}", p.dim(&format!("{} more were considered: /e all", ev.len() - cited.len())));
    }
}

fn print_stats(p: &Paint, r: &Value) {
    let s = &r["stats"];
    let ms = |k: &str| {
        s[k].as_f64().map(|v| if v >= 1000.0 { format!("{:.1} s", v / 1000.0) } else { format!("{v:.0} ms") })
    };
    let mut rows: Vec<(String, String)> = Vec::new();
    if let Some(v) = ms("retrieval_ms") {
        rows.push(("search".into(), format!("{v} over {} memories, {} kept", s["memories_searched"], s["candidates"])));
    }
    if let Some(v) = ms("answer_ms") {
        rows.push(("answer".into(), format!("{v} with {}", s["model"].as_str().unwrap_or("?"))));
    }
    if let Some(q) = s["search_query"].as_str() {
        rows.push(("searched for".into(), clip(q, 90)));
    }
    rows.push(("embeddings".into(), s["embed_model"].as_str().unwrap_or("?").to_string()));
    rows.push(("model calls".into(), s["model_calls"].as_i64().unwrap_or(0).to_string()));
    for (k, v) in rows {
        println!("  {} {v}", p.dim(&format!("{k:<13}")));
    }
}

fn print_reply(p: &Paint, r: &Value) {
    if let Some(note) = r["note"].as_str() {
        println!("{}", p.dim(note));
    }
    let answer = r["answer"].as_str().unwrap_or("");
    if !answer.is_empty() {
        println!("{answer}");
    }
    let kind = r["kind"].as_str().unwrap_or("");
    if matches!(kind, "search" | "recent" | "entity") {
        print_evidence(p, r, true);
    }
    if kind == "answer" {
        let s = &r["stats"];
        let n = r["cited"].as_array().map_or(0, Vec::len);
        println!(
            "{}",
            p.dim(&format!(
                "— {} cited of {} found · search {:.0} ms · answer {:.1} s · /e evidence · /s stats",
                n,
                s["candidates"],
                s["retrieval_ms"].as_f64().unwrap_or(0.0),
                s["answer_ms"].as_f64().unwrap_or(0.0) / 1000.0
            ))
        );
    }
}

/// `neuralgraph-memory chat`: one question when `one_shot` is given, otherwise an interactive session.
pub async fn run_terminal(cm: &ChatMemory, one_shot: Option<String>, ai: bool, json_out: bool) -> Result<i32> {
    let p = Paint(std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none());
    if let Some(q) = one_shot {
        let r = reply(cm, &q, ai, &[]).await?;
        if json_out {
            println!("{}", serde_json::to_string_pretty(&r).unwrap_or_default());
        } else {
            print_reply(&p, &r);
        }
        return Ok(0);
    }
    let active = active_memories(cm);
    println!("{} {}", p.accent("●──●──●"), p.bold("NeuralGraph chat"));
    println!(
        "{}",
        p.dim(&format!(
            "{active} memories · {} · Ask anything. /help for direct queries, /q to quit.",
            if cm.store.is_paused() { "paused (search only)".to_string() } else { cm.llm().model() }
        ))
    );
    let mut history: Vec<Turn> = Vec::new();
    let mut last = Value::Null;
    let mut use_ai = ai;
    loop {
        print!("\n{} ", p.accent("›"));
        let _ = std::io::stdout().flush();
        let mut line = String::new();
        if std::io::stdin().read_line(&mut line).unwrap_or(0) == 0 {
            break;
        }
        let line = line.trim();
        match line {
            "" => continue,
            "/q" | "/quit" | "/exit" | "exit" | "quit" => break,
            "/e" | "/evidence" => print_evidence(&p, &last, false),
            "/e all" | "/evidence all" => print_evidence(&p, &last, true),
            "/s" => print_stats(&p, &last),
            "/ai on" | "/ai off" => {
                use_ai = line.ends_with("on");
                println!(
                    "{}",
                    p.dim(if use_ai {
                        "Answers are written by the model again."
                    } else {
                        "Search only: no model is used."
                    })
                );
            }
            "/clear" => {
                history.clear();
                println!("{}", p.dim("Conversation cleared."));
            }
            _ => match reply(cm, line, use_ai, &history).await {
                Ok(r) => {
                    print_reply(&p, &r);
                    if r["kind"] == "answer" {
                        history.push((line.to_string(), r["answer"].as_str().unwrap_or("").to_string()));
                    }
                    if r["kind"] == "help" {
                        println!("{}", p.dim("  /e [all]        evidence of the last answer\n  /s              search and answer numbers of the last answer\n  /ai on|off      write answers with the model, or search only\n  /clear          forget this conversation\n  /q              quit"));
                    }
                    last = r;
                }
                Err(e) => println!("{}", p.c("31", &e.message())),
            },
        }
    }
    Ok(0)
}
