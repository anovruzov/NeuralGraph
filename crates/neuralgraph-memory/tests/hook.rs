//! Claude Code hooks: which transcript lines become messages, incremental and idempotent ingestion, and the
//! settings.json merge that installs or removes the hooks.

mod common;

use neuralgraph_memory::hook::{clean_text, ingest_transcript, parse_transcript, pick_relevant, RECALL_MIN_SIM};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig};
use neuralgraph_memory::setup::merge_claude_hooks;
use neuralgraph_memory::testing::scripted_fake_llm;
use serde_json::{json, Value};
use std::io::Write;
use std::sync::Arc;

fn line(v: Value) -> String {
    v.to_string()
}

fn user(id: &str, content: Value) -> String {
    line(
        json!({"type": "user", "uuid": id, "timestamp": "2026-11-08T10:00:00Z", "message": {"role": "user", "content": content}}),
    )
}

fn assistant(id: &str, blocks: Value) -> String {
    line(
        json!({"type": "assistant", "uuid": id, "timestamp": "2026-11-08T10:00:05Z", "message": {"role": "assistant", "content": blocks}}),
    )
}

fn sample() -> Vec<String> {
    vec![
        line(json!({"type": "queue-operation", "sessionId": "s"})),
        user("u1", json!("<system-reminder>ignore me</system-reminder>I moved to Berlin in March.")),
        assistant("a1", json!([{"type": "thinking", "thinking": "hmm"}])),
        assistant(
            "a2",
            json!([{"type": "text", "text": "Checking your files."}, {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
        ),
        user("tr", json!([{"type": "tool_result", "tool_use_id": "t", "content": "ls output"}])),
        assistant("a3", json!([{"type": "text", "text": "Noted: you live in Berlin now."}])),
        line(json!({"type": "user", "uuid": "meta", "isMeta": true, "message": {"role": "user", "content": "caveat"}})),
        line(
            json!({"type": "assistant", "uuid": "side", "isSidechain": true, "message": {"content": [{"type": "text", "text": "subagent"}]}}),
        ),
        user("u2", json!([{"type": "text", "text": "My cat Luna is 3."}])),
        assistant("a4", json!([{"type": "text", "text": "Here:\n```rust\nfn main() {}\n```\nLuna is 3."}])),
    ]
}

#[test]
fn transcript_keeps_prompts_and_final_answers_only() {
    let lines = sample();
    let turns = parse_transcript(lines.iter().map(String::as_str));
    let got: Vec<(&str, &str, &str)> =
        turns.iter().map(|t| (t.id.as_str(), t.role.as_str(), t.text.as_str())).collect();
    assert_eq!(
        got,
        [
            ("u1", "user", "I moved to Berlin in March."),
            ("a3", "assistant", "Noted: you live in Berlin now."),
            ("u2", "user", "My cat Luna is 3."),
            ("a4", "assistant", "Here:\n[code]\nLuna is 3."),
        ]
    );
}

#[test]
fn clean_text_drops_harness_noise() {
    assert_eq!(clean_text("<command-name>/clear</command-name>"), "");
    assert_eq!(clean_text("[Request interrupted by user]"), "");
    assert_eq!(clean_text("<neuralgraph-memory>old recall</neuralgraph-memory>  hi there  "), "hi there");
    let long = "x".repeat(3000);
    assert_eq!(clean_text(&long).chars().count(), 2501, "cut to 2500 chars plus an ellipsis");
}

#[tokio::test]
async fn ingestion_is_incremental_and_idempotent() {
    let dir = tempfile::tempdir().unwrap();
    // offsets live next to the config file
    std::env::set_var("NEURALGRAPH_CONFIG", dir.path().join("config.json"));
    let cm = ChatMemory::open(
        dir.path().join("m.db").to_str().unwrap(),
        Some(Arc::new(scripted_fake_llm())),
        ChatMemoryConfig::default(),
    )
    .unwrap();
    let path = dir.path().join("session.jsonl");
    let lines = sample();
    let mut f = std::fs::File::create(&path).unwrap();
    for l in &lines[..6] {
        writeln!(f, "{l}").unwrap();
    }
    write!(f, "{{\"type\": \"user\", \"half-written").unwrap(); // a line still being written
    drop(f);
    let session = "abcdef12-3456";
    assert_eq!(ingest_transcript(&cm, &path, session, Some("/work/proj"), false).unwrap(), 2);
    assert_eq!(ingest_transcript(&cm, &path, session, Some("/work/proj"), false).unwrap(), 0, "nothing new");

    // the rest of the session arrives (the half line is completed as a real one)
    let mut f = std::fs::OpenOptions::new().write(true).truncate(true).open(&path).unwrap();
    for l in &lines {
        writeln!(f, "{l}").unwrap();
    }
    drop(f);
    // same bytes up to the saved offset, so only the new lines are read
    let added = ingest_transcript(&cm, &path, session, Some("/work/proj"), false).unwrap();
    std::fs::remove_file(dir.path().join("hooks/claude-code/abcdef12-3456.offset")).unwrap();
    let again = ingest_transcript(&cm, &path, session, Some("/work/proj"), false).unwrap();
    assert_eq!(again, 0, "a full re-read stores nothing twice");
    assert_eq!(added, 2, "the second prompt and its answer");

    let msgs = cm.store.get_messages("claude-code-abcdef12", None, None, None, false).unwrap();
    let texts: Vec<(&str, &str)> = msgs.iter().map(|m| (m.speaker.as_str(), m.text.as_str())).collect();
    assert_eq!(
        texts,
        [
            ("user", "I moved to Berlin in March."),
            ("Claude", "Noted: you live in Berlin now."),
            ("user", "My cat Luna is 3."),
            ("Claude", "Here:\n[code]\nLuna is 3."),
        ]
    );
    assert_eq!(msgs[0].role, "user");
    assert_eq!(msgs[1].role, "assistant");
    let chats = cm.store.list_chats(10, 0).unwrap();
    assert_eq!(chats[0]["title"], "Claude Code · proj");
    cm.close().await;
}

#[test]
fn hooks_merge_installs_once_and_keeps_other_settings() {
    let existing = r#"{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}"#;
    let once = merge_claude_hooks(Some(existing), Some("/a/neuralgraph-memory hook claude-code")).unwrap();
    let twice = merge_claude_hooks(Some(&once), Some("/b/neuralgraph-memory hook claude-code")).unwrap();
    let v: Value = serde_json::from_str(&twice).unwrap();
    assert_eq!(v["model"], "opus");
    let stop = v["hooks"]["Stop"].as_array().unwrap();
    assert_eq!(stop.len(), 2, "the user's hook plus exactly one of ours");
    assert_eq!(stop[0]["hooks"][0]["command"], "say done");
    assert_eq!(stop[1]["hooks"][0]["command"], "/b/neuralgraph-memory hook claude-code");
    assert_eq!(v["hooks"]["UserPromptSubmit"].as_array().unwrap().len(), 1);

    let off: Value = serde_json::from_str(&merge_claude_hooks(Some(&twice), None).unwrap()).unwrap();
    assert_eq!(
        off,
        json!({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}})
    );
    let clean: Value =
        serde_json::from_str(&merge_claude_hooks(Some(&once.replace("say done", "x")), None).unwrap()).unwrap();
    assert!(clean["hooks"]["UserPromptSubmit"].is_null());
    let fresh: Value = serde_json::from_str(&merge_claude_hooks(None, None).unwrap()).unwrap();
    assert_eq!(fresh, json!({}));
    assert!(merge_claude_hooks(Some("{bad"), None).is_err());
}

#[test]
fn recall_keeps_only_semantically_close_memories() {
    use neuralgraph_memory::models::RetrievedMemory;
    let hit = |text: &str, sim: Option<f64>| {
        let mut channels = indexmap::IndexMap::new();
        if let Some(s) = sim {
            channels.insert("vector".to_string(), s);
        }
        channels.insert("keyword".to_string(), 1.0);
        RetrievedMemory {
            memory: common::mem(text),
            score: 0.03,
            channels,
            sources: vec![],
            entities: vec![],
            explanation: String::new(),
        }
    };
    let results = vec![
        hit("keyword only", None),
        hit("weak", Some(0.50)),
        hit("good", Some(0.61)),
        hit("best", Some(0.83)),
        hit("border", Some(RECALL_MIN_SIM)),
    ];
    let texts: Vec<String> = pick_relevant(results, RECALL_MIN_SIM).into_iter().map(|r| r.memory.text).collect();
    assert_eq!(texts, ["best", "good", "border"], "sorted by similarity; keyword-only and weak hits are dropped");
    let many: Vec<RetrievedMemory> = (0..9).map(|i| hit(&format!("m{i}"), Some(0.9 - i as f64 * 0.01))).collect();
    assert_eq!(pick_relevant(many, RECALL_MIN_SIM).len(), 5, "at most five per prompt");
}
