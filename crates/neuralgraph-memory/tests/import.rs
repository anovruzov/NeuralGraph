//! Import: Claude.ai and ChatGPT exports, notes and PDFs-as-text, Claude Code transcripts, the upload framing, and
//! committing a preview (twice, without storing anything twice).

use neuralgraph_memory::import::{
    commit, document, parse_chatgpt_export, parse_claude_export, parse_upload, unframe, ImportPlan,
};
use neuralgraph_memory::service::{ChatMemory, ChatMemoryConfig};
use neuralgraph_memory::testing::scripted_fake_llm;
use serde_json::json;
use std::io::Write;
use std::sync::Arc;

fn claude_export() -> serde_json::Value {
    json!([{
        "uuid": "1b2c3d4e-0000-0000-0000-000000000001", "name": "Moving to Berlin", "created_at": "2025-03-01T10:00:00Z",
        "chat_messages": [
            {"uuid": "m1", "sender": "human", "text": "I'm moving to Berlin in May with my cat Luna.", "created_at": "2025-03-01T10:00:00Z"},
            {"uuid": "m2", "sender": "assistant", "text": "", "content": [{"type": "text", "text": "Congrats! Here is a checklist."}]},
            {"uuid": "m3", "sender": "human", "text": "   "}
        ]
    }, {"uuid": "empty", "name": "", "chat_messages": []}])
}

fn chatgpt_export() -> serde_json::Value {
    json!([{
        "conversation_id": "abcdef1234", "title": "Diet", "create_time": 1735689600.0, "current_node": "n3",
        "mapping": {
            "root": {"message": null, "parent": null},
            "n1": {"parent": "root", "message": {"author": {"role": "system"}, "content": {"content_type": "text", "parts": ["sys"]}}},
            "n2": {"parent": "n1", "message": {"author": {"role": "user"}, "create_time": 1735689600.0, "content": {"content_type": "text", "parts": ["I'm vegetarian."]}}},
            "old": {"parent": "n2", "message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["dead branch"]}}},
            "n3": {"parent": "n2", "message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["Noted."]}}}
        }
    }])
}

#[test]
fn claude_export_becomes_conversations() {
    let convs = parse_claude_export(&claude_export());
    assert_eq!(convs.len(), 1, "empty conversations are left out");
    let c = &convs[0];
    assert_eq!((c.id.as_str(), c.title.as_str()), ("import-claude-1b2c3d4e", "Claude · Moving to Berlin"));
    let msgs: Vec<(&str, &str, &str)> =
        c.messages.iter().map(|m| (m.id.as_str(), m.role.as_str(), m.text.as_str())).collect();
    assert_eq!(
        msgs,
        [
            ("imp-claude-m1", "user", "I'm moving to Berlin in May with my cat Luna."),
            ("imp-claude-m2", "assistant", "Congrats! Here is a checklist."),
        ]
    );
}

#[test]
fn chatgpt_export_follows_the_current_branch() {
    let convs = parse_chatgpt_export(&chatgpt_export());
    let c = &convs[0];
    assert_eq!(c.id, "import-chatgpt-abcdef12");
    let texts: Vec<&str> = c.messages.iter().map(|m| m.text.as_str()).collect();
    assert_eq!(texts, ["I'm vegetarian.", "Noted."], "system turns and dead branches are skipped");
    assert!(c.messages[0].at.as_deref().unwrap().starts_with("2025-01-01"));
}

#[test]
fn uploads_are_detected_by_type() {
    let p = parse_upload("conversations.json", claude_export().to_string().as_bytes()).unwrap();
    assert_eq!(p.source, "claude");
    let p = parse_upload("conversations.json", chatgpt_export().to_string().as_bytes()).unwrap();
    assert_eq!(p.source, "chatgpt");
    assert!(parse_upload("x.json", b"{\"a\": 1}").is_err());
    assert!(parse_upload("x.docx", b"...").unwrap_err().contains(".docx"));

    // an export .zip, found by its conversations.json anywhere inside
    let mut zipped = Vec::new();
    {
        let mut z = zip::ZipWriter::new(std::io::Cursor::new(&mut zipped));
        z.start_file("data-2025/conversations.json", zip::write::SimpleFileOptions::default()).unwrap();
        z.write_all(claude_export().to_string().as_bytes()).unwrap();
        z.finish().unwrap();
    }
    assert_eq!(parse_upload("export.zip", &zipped).unwrap().conversations.len(), 1);

    // a notes folder
    let mut notes = Vec::new();
    {
        let mut z = zip::ZipWriter::new(std::io::Cursor::new(&mut notes));
        for (n, t) in
            [("vault/me.md", "# Me\n\nI live in Lyon."), ("vault/img.png", "x"), ("__MACOSX/vault/._me.md", "junk")]
        {
            z.start_file(n, zip::write::SimpleFileOptions::default()).unwrap();
            z.write_all(t.as_bytes()).unwrap();
        }
        z.finish().unwrap();
    }
    let p = parse_upload("vault.zip", &notes).unwrap();
    assert_eq!(p.conversations.len(), 1);
    assert_eq!(p.conversations[0].title, "me.md");
}

#[test]
fn documents_split_into_titled_sections() {
    let text = "# Me\n\nIntro line.\n\n## Work\n\n".to_string()
        + &"- I build memory systems in Rust.\n".repeat(40)
        + "\n## Photography\n\n### Cameras\n\n"
        + &"- Canon 5D Mark III, 41,000 shutter count.\n".repeat(30);
    let c = document("me.md", &text, text.as_bytes()).unwrap();
    assert!(c.id.starts_with("import-doc-") && c.messages.iter().all(|m| m.role == "user"));
    let first: Vec<&str> = c.messages.iter().map(|m| m.text.lines().next().unwrap()).collect();
    assert_eq!(first[0], "[me.md › Me]");
    assert!(first.contains(&"[me.md › Me › Work]"), "a section that continues keeps its heading: {first:?}");
    assert!(first.contains(&"[me.md › Me › Photography › Cameras]"), "{first:?}");
    assert!(c.messages.iter().all(|m| m.text.len() <= 1300), "sections stay small enough for one model call");
    let body: String = c.messages.iter().map(|m| m.text.as_str()).collect();
    assert_eq!(body.matches("Canon 5D Mark III").count(), 30, "no line is lost or repeated");
    assert_eq!(body.matches("memory systems in Rust").count(), 40);

    // a PDF page arrives as one long line: cut at sentence ends, nothing dropped
    let page = "Ada ran the Lyon marathon. ".repeat(120);
    let c = document("page.pdf", &page, page.as_bytes()).unwrap();
    assert!(c.messages.len() >= 3);
    let joined: String = c.messages.iter().map(|m| m.text.as_str()).collect();
    assert_eq!(joined.matches("Ada ran the Lyon marathon.").count(), 120);
    assert!(document("x", "  \n\n ", b"x").is_none());
    assert!(document("x", "# Only a heading\n", b"y").is_none());
}

#[test]
fn documents_are_read_one_section_per_call() {
    use neuralgraph_memory::store::{batch_for, is_digest_chat};
    assert_eq!(batch_for("import-doc-abc"), Some(1));
    assert_eq!(batch_for("import-claude-abc"), Some(20));
    assert_eq!(batch_for("claude-code-abc"), Some(20));
    assert_eq!(batch_for("my-chat"), None);
    assert!(is_digest_chat("import-chatgpt-1") && is_digest_chat("claude-code-1"));
    assert!(!is_digest_chat("import-doc-1"), "a document's sections are the user's own words: all of it counts");
}

#[test]
fn upload_framing_round_trips() {
    let mut body = Vec::new();
    for (n, d) in [("a.md", b"hello".to_vec()), ("ü.png", vec![0u8; 3])] {
        body.extend((n.len() as u32).to_be_bytes());
        body.extend(n.as_bytes());
        body.extend((d.len() as u64).to_be_bytes());
        body.extend(d);
    }
    let files = unframe(&body).unwrap();
    assert_eq!(files[0], ("a.md".to_string(), b"hello".to_vec()));
    assert_eq!(files[1].0, "ü.png");
    assert!(unframe(&body[..body.len() - 1]).is_err(), "a truncated upload is refused");
}

#[tokio::test]
async fn commit_queues_once() {
    let dir = tempfile::tempdir().unwrap();
    let cm = ChatMemory::open(
        dir.path().join("m.db").to_str().unwrap(),
        Some(Arc::new(scripted_fake_llm())),
        ChatMemoryConfig::default(),
    )
    .unwrap();
    let plan = ImportPlan {
        source: "claude".into(),
        conversations: parse_claude_export(&claude_export()),
        ..Default::default()
    };
    assert_eq!(commit(&cm, &plan, &[]).unwrap(), (1, 2, 0));
    assert_eq!(commit(&cm, &plan, &[]).unwrap(), (1, 0, 2), "importing the same export again adds nothing");
    let msgs = cm.store.get_messages("import-claude-1b2c3d4e", None, None, None, false).unwrap();
    assert_eq!(msgs[1].speaker, "Claude");
    assert_eq!(msgs[1].role, "assistant");
    cm.close().await;
}
