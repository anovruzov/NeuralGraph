//! `setup` wizard pieces that do not need a terminal: the MCP JSON merge, model and tool picking, the saved-config
//! environment mapping and the banner.

use neuralgraph_memory::setup::{
    banner_lines, is_embedding_model, merge_mcp_json, parse_pick, pick_default, SavedConfig, SERVER_NAME,
};
use serde_json::{json, Value};

#[test]
fn merge_keeps_other_servers_and_keys() {
    let existing = r#"{"theme": "dark", "mcpServers": {"other": {"command": "x"}}}"#;
    let out: Value = serde_json::from_str(&merge_mcp_json(Some(existing), "/bin/ng").unwrap()).unwrap();
    assert_eq!(out["theme"], "dark");
    assert_eq!(out["mcpServers"]["other"], json!({"command": "x"}));
    assert_eq!(out["mcpServers"][SERVER_NAME], json!({"command": "/bin/ng", "args": ["mcp"]}));
    // order preserved: the user's keys stay first
    assert_eq!(out.as_object().unwrap().keys().next().unwrap(), "theme");
}

#[test]
fn merge_creates_and_replaces() {
    let fresh: Value = serde_json::from_str(&merge_mcp_json(None, "/a").unwrap()).unwrap();
    assert_eq!(fresh, json!({"mcpServers": {SERVER_NAME: {"command": "/a", "args": ["mcp"]}}}));
    let again: Value = serde_json::from_str(&merge_mcp_json(Some(&fresh.to_string()), "/b").unwrap()).unwrap();
    assert_eq!(again["mcpServers"][SERVER_NAME]["command"], "/b");
    assert_eq!(again["mcpServers"].as_object().unwrap().len(), 1);
}

#[test]
fn merge_refuses_to_clobber_unreadable_files() {
    assert!(merge_mcp_json(Some("{not json"), "/a").is_err());
    assert!(merge_mcp_json(Some("[1, 2]"), "/a").is_err());
    assert!(merge_mcp_json(Some(r#"{"mcpServers": 3}"#), "/a").is_err());
    assert!(merge_mcp_json(Some("  \n"), "/a").is_ok(), "an empty file is treated as new");
}

#[test]
fn pick_default_prefers_saved_then_recommended_and_matches_ollama_tags() {
    let ms: Vec<String> = ["llama3:8b", "qwen2.5:7b-instruct", "nomic-embed-text:latest"].map(String::from).to_vec();
    assert_eq!(pick_default(&ms, &["llama3", "qwen2.5:7b-instruct"]), Some(0), "llama3 matches llama3:8b");
    assert_eq!(pick_default(&ms, &["", "qwen2.5:7b-instruct"]), Some(1));
    assert_eq!(pick_default(&ms, &["nomic-embed-text"]), Some(2));
    assert_eq!(pick_default(&ms, &["qwen2.5"]), Some(1), "Ollama name without its tag");
    assert_eq!(pick_default(&ms, &["qwen2"]), None, "a bare prefix needs the ':' tag separator");
}

#[test]
fn embedding_models_are_recognised() {
    for m in
        ["text-embedding-nomic-embed-text-v1.5", "nomic-embed-text:latest", "text-embedding-bge-m3", "all-MiniLM-L6"]
    {
        assert!(is_embedding_model(m), "{m}");
    }
    for m in ["google/gemma-4-e4b", "qwen2.5:7b-instruct", "qwen/qwen3-vl-8b"] {
        assert!(!is_embedding_model(m), "{m}");
    }
}

#[test]
fn tool_picks_parse_strictly() {
    assert_eq!(parse_pick("", 3), Some(vec![0, 1, 2]));
    assert_eq!(parse_pick(" ALL ", 3), Some(vec![0, 1, 2]));
    assert_eq!(parse_pick("n", 3), Some(vec![]));
    assert_eq!(parse_pick("1,3", 3), Some(vec![0, 2]));
    assert_eq!(parse_pick("2 2  1", 3), Some(vec![1, 0]));
    assert_eq!(parse_pick("4", 3), None);
    assert_eq!(parse_pick("/tmp/x2/db", 3), None, "a stray path is not a pick");
    assert_eq!(parse_pick("yes", 3), None);
}

#[test]
fn saved_config_maps_to_env_vars() {
    let cfg = SavedConfig {
        llm_base_url: Some("http://127.0.0.1:11434".into()),
        llm_model: Some("qwen2.5:7b-instruct".into()),
        embed_model: None,
        db: Some(String::new()),
        user_name: Some("Ada".into()),
        llm_backend: Some("ollama".into()),
        vision_model: None,
        idle_unload_minutes: None,
    };
    assert_eq!(
        cfg.env_pairs(),
        vec![
            ("LLM_BASE_URL", "http://127.0.0.1:11434".to_string()),
            ("LLM_MODEL", "qwen2.5:7b-instruct".to_string()),
            ("NEURALGRAPH_USER_NAME", "Ada".to_string()),
            ("LLM_BACKEND", "ollama".to_string()),
        ]
    );
    let back: SavedConfig = serde_json::from_str(&serde_json::to_string(&cfg).unwrap()).unwrap();
    assert_eq!(back, cfg);
    assert_eq!(serde_json::from_str::<SavedConfig>("{}").unwrap(), SavedConfig::default());
}

#[test]
fn banner_is_a_tidy_block() {
    let rows = banner_lines();
    assert_eq!(rows.len(), 6, "five letter rows plus the descender of the p");
    assert!(rows.iter().all(|r| r.chars().count() <= 72), "fits an 80-column terminal with the indent");
    assert!(rows[4].starts_with("|_| \\_|"), "{rows:#?}");
}
