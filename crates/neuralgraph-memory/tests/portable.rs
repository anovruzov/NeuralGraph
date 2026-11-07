//! Portable brain archives: export → import (restore and merge) → verify, project scoping, tamper detection and
//! the privacy scan. Every test works on temporary SQLite files.

mod common;

use common::make_memory;
use neuralgraph_memory::llm::fake_embedding;
use neuralgraph_memory::portable::{
    export_brain, import_brain, inspect, preview, verify, ExportOptions, ImportMode, ImportOptions,
};
use neuralgraph_memory::store::{AddMessage, ChatMemoryStore};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

fn emb(text: &str) -> Option<Vec<f64>> {
    Some(fake_embedding(text, 64))
}

/// Two chats, sourced memories with embeddings, entities, a relation, a link and a supersession.
fn build_source(path: &Path) -> Vec<String> {
    let s = ChatMemoryStore::open(path.to_str().unwrap()).unwrap();
    let opts = AddMessage::default();
    let (m1, _) = s.add_message("proj-alpha", "user", "I moved to Berlin last spring.", &opts).unwrap();
    let (m2, _) = s.add_message("proj-alpha", "assistant", "Noted, Berlin it is.", &opts).unwrap();
    let (m3, _) = s.add_message("personal", "user", "My sister Lina lives in Porto.", &opts).unwrap();
    let a = make_memory("Ali lives in Hamburg", "ali", "proj-alpha", "2025-01-01T00:00:00+00:00", emb("Ali lives in Hamburg"), "fact", 0.8);
    let b = make_memory("Ali lives in Berlin", "ali", "proj-alpha", "2025-05-01T00:00:00+00:00", emb("Ali lives in Berlin"), "fact", 0.9);
    let c = make_memory("Ali's sister Lina lives in Porto", "lina", "personal", "2025-06-01T00:00:00+00:00", emb("Ali's sister Lina lives in Porto"), "relationship", 0.7);
    s.upsert_entity("ali", "Ali", "person", None, 2, &[]).unwrap();
    s.upsert_entity("berlin", "Berlin", "place", None, 1, &[]).unwrap();
    s.upsert_entity("lina", "Lina", "person", None, 1, &[]).unwrap();
    s.insert_memory(&a, &[m1.message_id.clone()], &["ali".into()]).unwrap();
    s.insert_memory(&b, &[m1.message_id.clone(), m2.message_id.clone()], &["ali".into(), "berlin".into()]).unwrap();
    s.insert_memory(&c, &[m3.message_id.clone()], &["lina".into()]).unwrap();
    s.supersede(&a.memory_id, &b.memory_id, "supersedes").unwrap();
    s.add_link(&c.memory_id, &b.memory_id, "related", 0.5).unwrap();
    s.upsert_relation("ali", "lives_in", "berlin", 0.9, "proj-alpha", Some(&b.memory_id), Some(&m1.message_id), None).unwrap();
    s.upsert_relation("lina", "sibling_of", "ali", 0.7, "personal", Some(&c.memory_id), Some(&m3.message_id), None).unwrap();
    s.set_meta("embed_dim", "64").unwrap();
    s.close();
    vec![a.memory_id, b.memory_id, c.memory_id]
}

fn export(db: &Path, out: &Path, chats: &[&str]) -> serde_json::Value {
    export_brain(&ExportOptions {
        stores: vec![("brain".into(), db.to_str().unwrap().into())],
        out: out.to_path_buf(),
        chat_prefixes: chats.iter().map(|c| c.to_string()).collect(),
        include_embeddings: true,
        embed_model: Some("fake-64".into()),
        overwrite: false,
    })
    .unwrap()
    .manifest
}

fn import(archive: &Path, db: &Path, mode: ImportMode) -> neuralgraph_memory::error::Result<serde_json::Value> {
    import_brain(&ImportOptions { archive: archive.into(), db: db.to_str().unwrap().into(), mode, store: None, replace: false })
}

fn failed_checks(report: &serde_json::Value) -> Vec<String> {
    report["checks"].as_array().unwrap().iter().filter(|c| c["ok"] != true).map(|c| format!("{}: {}", c["name"], c["detail"])).collect()
}

#[tokio::test]
async fn restore_round_trip_is_exact() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("src.db");
    build_source(&src);
    let zip = dir.path().join("brain.zip");
    let manifest = export(&src, &zip, &[]);
    assert_eq!(manifest["format"], "neuralgraph-brain");
    let st = &manifest["stores"][0];
    assert_eq!(st["counts"]["memories"], 3);
    assert_eq!(st["embeddings"]["dim"], 64);
    assert_eq!(st["embeddings"]["model"], "fake-64");
    assert_eq!(st["graph"]["edges"]["superseded_by"], 1);
    let paths: Vec<&str> = manifest["files"].as_array().unwrap().iter().map(|f| f["path"].as_str().unwrap()).collect();
    for want in ["stores/brain/brain.sqlite", "stores/brain/jsonl/memories.jsonl", "stores/brain/graph.graphml", "stores/brain/memories.md", "README.md", "agents/codex-config.toml"] {
        assert!(paths.contains(&want), "missing {want}");
    }

    let fresh = dir.path().join("fresh").join("chat_memory.db");
    let r = import(&zip, &fresh, ImportMode::Restore).unwrap();
    assert_eq!(r["mode"], "restore");
    let report = verify(&zip, fresh.to_str().unwrap(), None, true, 10).await.unwrap();
    assert!(report["ok"] == true, "verify failed: {:?}", failed_checks(&report));
    assert_eq!(report["retrieval"]["identical"], report["retrieval"]["queries"]);

    // restoring onto a non-empty brain is refused unless --replace, which keeps the old files
    assert!(import(&zip, &fresh, ImportMode::Restore).is_err());
    let again = import_brain(&ImportOptions { archive: zip.clone(), db: fresh.to_str().unwrap().into(), mode: ImportMode::Restore, store: None, replace: true }).unwrap();
    let backup = again["result"]["moved_aside"].as_str().unwrap().to_string();
    assert!(Path::new(&backup).exists());
}

#[tokio::test]
async fn merge_avoids_id_collisions_and_keeps_history() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("src.db");
    let ids = build_source(&src);
    let zip = dir.path().join("brain.zip");
    export(&src, &zip, &[]);

    // the target already has a different memory under one of the archived ids, and a diverged "proj-alpha" chat
    let tgt = dir.path().join("tgt.db");
    {
        let s = ChatMemoryStore::open(tgt.to_str().unwrap()).unwrap();
        let (m, _) = s.add_message("proj-alpha", "user", "Totally different first message.", &AddMessage::default()).unwrap();
        let mut clash = make_memory("Ali likes tea", "ali", "proj-alpha", "2025-02-01T00:00:00+00:00", emb("Ali likes tea"), "preference", 0.5);
        clash.memory_id = ids[1].clone();
        s.upsert_entity("ali", "Ali", "person", None, 5, &[]).unwrap();
        s.insert_memory(&clash, &[m.message_id.clone()], &["ali".into()]).unwrap();
        s.upsert_relation("ali", "lives_in", "berlin", 0.6, "proj-alpha", None, None, None).unwrap();
        s.set_meta("embed_dim", "64").unwrap();
        s.close();
    }
    let r = import(&zip, &tgt, ImportMode::Merge).unwrap();
    let stats = &r["result"]["stats"];
    assert_eq!(stats["memories_renamed"], 1, "{r}");
    assert_eq!(stats["chats_renamed"], 1, "{r}");
    assert_eq!(stats["relations_merged"], 1, "{r}");

    let s = ChatMemoryStore::open(tgt.to_str().unwrap()).unwrap();
    let mine = s.get_memory(&ids[1], false).unwrap().unwrap();
    assert_eq!(mine.text, "Ali likes tea", "the target's own memory must be untouched");
    let renamed: (String, String, String) = s
        .with_conn(|c| Ok(c.query_row("SELECT memory_id, metadata, chat_id FROM memories WHERE text='Ali lives in Berlin'", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?))
        .unwrap();
    assert!(renamed.0.starts_with(&format!("{}.", ids[1])));
    assert!(renamed.1.contains("imported_from") && renamed.1.contains(&ids[1]));
    assert!(renamed.2.starts_with("proj-alpha."), "diverged chat imported under a new id");
    // supersession now points at the renamed memory
    let old = s.get_memory(&ids[0], false).unwrap().unwrap();
    assert_eq!(old.superseded_by.as_deref(), Some(renamed.0.as_str()));
    let obs: i64 = s
        .with_conn(|c| Ok(c.query_row("SELECT observation_count FROM relations WHERE subject_id='ali' AND predicate='lives_in'", [], |r| r.get(0))?))
        .unwrap();
    assert_eq!(obs, 2, "independent observations of a triple add up");
    s.close();

    let report = verify(&zip, tgt.to_str().unwrap(), None, false, 10).await.unwrap();
    assert!(report["ok"] == true, "verify failed: {:?}", failed_checks(&report));
    // merging the same archive twice is refused
    let again = import(&zip, &tgt, ImportMode::Merge).unwrap_err();
    assert!(again.message().contains("already imported"));
}

#[test]
fn project_scope_exports_only_that_project() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("src.db");
    build_source(&src);
    let zip = dir.path().join("alpha.zip");
    let manifest = export(&src, &zip, &["proj-"]);
    let c = &manifest["stores"][0]["counts"];
    assert_eq!(manifest["scope"]["kind"], "project");
    assert_eq!(c["memories"], 2);
    assert_eq!(c["messages"], 2);
    assert_eq!(c["chats"], 1);
    assert_eq!(c["memory_links"], 1, "the in-project supersession link stays, the link into 'personal' is dropped");
    let mut md = String::new();
    zip::ZipArchive::new(std::fs::File::open(&zip).unwrap()).unwrap().by_name("stores/brain/memories.md").unwrap().read_to_string(&mut md).unwrap();
    assert!(md.contains("Ali lives in Berlin") && !md.contains("Porto"));
}

#[test]
fn tampered_archive_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let src = dir.path().join("src.db");
    build_source(&src);
    let zip_path = dir.path().join("brain.zip");
    export(&src, &zip_path, &[]);
    assert_eq!(inspect(&zip_path).unwrap()["checksums_ok"], true);
    // rewrite the archive with one file changed
    let bad: PathBuf = dir.path().join("bad.zip");
    let mut za = zip::ZipArchive::new(std::fs::File::open(&zip_path).unwrap()).unwrap();
    let mut zw = zip::ZipWriter::new(std::fs::File::create(&bad).unwrap());
    for i in 0..za.len() {
        let mut e = za.by_index(i).unwrap();
        let name = e.name().unwrap().to_string();
        let mut buf = Vec::new();
        e.read_to_end(&mut buf).unwrap();
        if name.ends_with("memories.md") {
            buf.extend_from_slice(b"\ninjected\n");
        }
        zw.start_file(name.as_str(), zip::write::SimpleFileOptions::default()).unwrap();
        zw.write_all(&buf).unwrap();
    }
    zw.finish().unwrap();
    let e = inspect(&bad).unwrap_err();
    assert!(e.message().contains("checksum mismatch"), "{}", e.message());
}

#[test]
fn preview_flags_confidential_content() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("p.db");
    let s = ChatMemoryStore::open(db.to_str().unwrap()).unwrap();
    s.add_message("c", "user", "my api_key = sk-abcdefghijklmnopqrstuvwxyz123456", &AddMessage::default()).unwrap();
    s.close();
    let p = preview(&[("p".into(), db.to_str().unwrap().into())], &[]).unwrap();
    assert_eq!(p["confidential"], true);
}
