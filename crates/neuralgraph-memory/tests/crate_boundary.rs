//! The product crate must not depend on the research crate, in any dependency kind.
//!
//! The research track (`crates/neuralgraph-research`) builds on this crate; the reverse would make the shipped
//! server pull in experimental code. This replaces the Python-era check that `NeuralGraph.chat_memory` imported
//! nothing from `NeuralGraph.research`.

use serde_json::Value;
use std::process::Command;

#[test]
fn product_crate_does_not_depend_on_research_crate() {
    let out = Command::new(env!("CARGO"))
        .args(["metadata", "--format-version", "1", "--no-deps", "--offline"])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .expect("run cargo metadata");
    assert!(out.status.success(), "cargo metadata failed: {}", String::from_utf8_lossy(&out.stderr));
    let meta: Value = serde_json::from_slice(&out.stdout).expect("metadata json");
    let packages = meta["packages"].as_array().expect("packages");
    let product = packages.iter().find(|p| p["name"] == "neuralgraph-memory").expect("product package in workspace");
    let deps: Vec<&str> =
        product["dependencies"].as_array().unwrap().iter().filter_map(|d| d["name"].as_str()).collect();
    assert!(!deps.is_empty(), "metadata lists the product's dependencies");
    let research: Vec<&str> = deps.iter().copied().filter(|d| d.starts_with("neuralgraph-research")).collect();
    assert!(research.is_empty(), "neuralgraph-memory depends on {research:?}; the research crate must stay downstream");
    // and the research crate does sit downstream (so the check above is not vacuous)
    let research_pkg = packages.iter().find(|p| p["name"] == "neuralgraph-research").expect("research package");
    assert!(research_pkg["dependencies"].as_array().unwrap().iter().any(|d| d["name"] == "neuralgraph-memory"));
}
