//! Self-update pieces that need no network: version comparison, the release target, install-kind detection, and
//! salvaging model output that was cut off.

use neuralgraph_memory::jsonutil::salvage_array;
use neuralgraph_memory::update::{blocked_reason, newer, target};
use serde_json::json;

#[test]
fn versions_compare_numerically() {
    assert!(newer("v1.10.0", "1.9.3"));
    assert!(newer("1.6.0", "1.5.9"));
    assert!(newer("v2.0.0", "v1.99.99"));
    assert!(!newer("v1.5.0", "1.5.0"));
    assert!(!newer("1.4.9", "1.5.0"));
    assert!(newer("1.5.1", "1.5"));
    assert!(!newer("v1.5.0-rc1", "1.5.0"), "a pre-release suffix is ignored");
}

#[test]
fn this_machine_has_a_release_target() {
    let t = target().expect("CI and dev machines are macOS or Linux on x86_64/arm64");
    assert!(t.ends_with("apple-darwin") || t.ends_with("unknown-linux-gnu"));
}

#[test]
fn a_source_build_does_not_replace_itself() {
    // the test binary lives in target/debug/deps
    let why = blocked_reason().expect("blocked");
    assert!(why.contains("built from source"), "{why}");
}

#[test]
fn cut_off_memory_lists_keep_their_complete_items() {
    let cut = r#"```json
{"memories": [
  {"text": "Ada lives in Lyon.", "kind": "fact"},
  {"text": "Ada said \"hi {there}\" once.", "kind": "fact", "entities": [{"name": "Ada"}]},
  {"text": "Ada plans to run a mara"#;
    let v = salvage_array(cut, "memories").unwrap();
    assert_eq!(
        v,
        json!({"memories": [
            {"text": "Ada lives in Lyon.", "kind": "fact"},
            {"text": "Ada said \"hi {there}\" once.", "kind": "fact", "entities": [{"name": "Ada"}]},
        ]})
    );
    assert!(salvage_array(r#"{"memories": [{"text": "cut"#, "memories").is_none());
    assert!(salvage_array("no json here", "memories").is_none());
}
