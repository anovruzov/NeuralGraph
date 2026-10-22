//! The coordination track's published, SHA256-pinned artifacts (`research/coordination/artifacts/`, copied from
//! `13a1729`) regenerate byte for byte from the Rust port, the claims docs/BENCHMARKS.md Track A lists as
//! invariants still hold, and the figures are true to the data (the figure tests port
//! `NeuralGraph/tests/test_figures.py` at `13a1729`, same names and assertions).
//!
//! If one of these fails, the response is never to refresh an artifact until it is green: find out why the
//! output moved, then re-pin deliberately with the reason in the commit message.

use indexmap::IndexMap;
use neuralgraph_research::coordination::core::artifact_json;
use neuralgraph_research::coordination::fixture::generate_fixture;
use neuralgraph_research::coordination::{benchmark, experiment, figures, scale};
use regex::Regex;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::OnceLock;

const SEED: i64 = 20260813;

fn artifacts() -> PathBuf {
    figures::artifacts_dir()
}

fn figure_dir() -> PathBuf {
    artifacts().join("figures")
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

fn artifact(name: &str) -> Value {
    figures::load(&artifacts(), name).unwrap()
}

fn read(name: &str) -> String {
    std::fs::read_to_string(artifacts().join(name)).unwrap_or_else(|e| panic!("{name}: {e}"))
}

/// `SHA256SUMS`: artifact name -> digest (a `*` binary marker is stripped, as the Python did).
fn sha256sums() -> BTreeMap<String, String> {
    read("SHA256SUMS")
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            let mut parts = line.split_whitespace();
            let digest = parts.next().unwrap().to_string();
            let name = parts.next().unwrap().trim_start_matches('*').to_string();
            (name, digest)
        })
        .collect()
}

/// Everything the four entry points write, regenerated once in-process.
struct Regenerated {
    files: BTreeMap<String, String>,
    figures: IndexMap<String, String>,
}

fn regenerated() -> &'static Regenerated {
    static CELL: OnceLock<Regenerated> = OnceLock::new();
    CELL.get_or_init(|| {
        let experiment = experiment::run_experiment(SEED).unwrap();
        let single = benchmark::run_benchmark(SEED).unwrap();
        let sweep = benchmark::run_sweep(SEED, 30).unwrap();
        let scale = scale::run_scale_sweep(SEED, &scale::default_specs()).unwrap();
        let mut files = BTreeMap::new();
        files.insert("experiment_seed20260813.json".to_string(), artifact_json(&experiment));
        files.insert("benchmark_seed20260813.json".to_string(), artifact_json(&single));
        files.insert("benchmark_sweep30_seed20260813.json".to_string(), artifact_json(&sweep));
        files.insert("scale_seed20260813.json".to_string(), artifact_json(&scale));
        files.insert("RESULTS.md".to_string(), benchmark::render_sweep_markdown(&sweep));
        files.insert("SCALE.md".to_string(), scale::render_markdown(&scale));
        // The figures are drawn from the regenerated JSON as written to disk, so a byte match here means the
        // whole chain (simulation -> artifact -> figure) reproduces, not just the drawing code.
        let parse = |name: &str| serde_json::from_str::<Value>(&files[name]).unwrap();
        let figures = figures::render_from(
            &parse("benchmark_sweep30_seed20260813.json"),
            &parse("benchmark_seed20260813.json"),
            &parse("scale_seed20260813.json"),
        )
        .unwrap();
        Regenerated { files, figures }
    })
}

// =================================================================================================================
// Bitwise reproducibility
// =================================================================================================================

#[test]
fn sha256sums_pins_exactly_the_four_json_artifacts_and_five_figures() {
    let names: Vec<String> = sha256sums().into_keys().collect();
    assert_eq!(
        names,
        vec![
            "benchmark_seed20260813.json",
            "benchmark_sweep30_seed20260813.json",
            "experiment_seed20260813.json",
            "figures/fig1_fixture.svg",
            "figures/fig2_survival_matrix.svg",
            "figures/fig3_pareto_survival_vs_storage.svg",
            "figures/fig4_scale_invariance.svg",
            "figures/fig5_silent_forgetting.svg",
            "scale_seed20260813.json",
        ]
    );
}

#[test]
fn committed_artifacts_match_their_sha256sums() {
    for (name, digest) in sha256sums() {
        let bytes = std::fs::read(artifacts().join(&name)).unwrap();
        assert_eq!(sha256(&bytes), digest, "{name} on disk no longer matches SHA256SUMS");
    }
}

fn assert_regenerated(name: &str) {
    let regenerated = &regenerated().files[name];
    assert_eq!(sha256(regenerated.as_bytes()), sha256sums()[name], "{name}: regenerated bytes differ from the pin");
}

#[test]
fn experiment_artifact_regenerates_byte_identically() {
    assert_regenerated("experiment_seed20260813.json");
}

#[test]
fn benchmark_artifact_regenerates_byte_identically() {
    assert_regenerated("benchmark_seed20260813.json");
}

#[test]
fn sweep_artifact_regenerates_byte_identically() {
    assert_regenerated("benchmark_sweep30_seed20260813.json");
}

#[test]
fn scale_artifact_regenerates_byte_identically() {
    assert_regenerated("scale_seed20260813.json");
}

#[test]
fn figures_regenerate_byte_identically_from_regenerated_artifacts() {
    let sums = sha256sums();
    for (name, svg) in &regenerated().figures {
        assert_eq!(sha256(svg.as_bytes()), sums[&format!("figures/{name}")], "{name}");
    }
}

#[test]
fn results_md_is_the_sweep_markdown() {
    // RESULTS.md is not in SHA256SUMS; it is `benchmark --sweep 30 --format markdown`, byte for byte.
    assert_eq!(regenerated().files["RESULTS.md"], read("RESULTS.md"));
}

#[test]
fn scale_md_is_the_scale_markdown() {
    // SCALE.md is `scale --format markdown`, byte for byte.
    assert_eq!(regenerated().files["SCALE.md"], read("SCALE.md"));
}

#[test]
fn cli_writes_the_pinned_experiment_bytes() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_coordination")).arg("experiment").output().unwrap();
    assert!(output.status.success());
    assert_eq!(sha256(&output.stdout), sha256sums()["experiment_seed20260813.json"]);
}

// =================================================================================================================
// docs/BENCHMARKS.md Track A: "Invariants -- if one moves, a claim about the world changed"
// =================================================================================================================

fn regenerated_json(name: &str) -> Value {
    serde_json::from_str(&regenerated().files[name]).unwrap()
}

#[test]
fn invariant_replica_counting_survives_lineage_root_failure_at_zero() {
    let sweep = regenerated_json("benchmark_sweep30_seed20260813.json");
    for strategy in ["fixed_distributed_replication", "source_count_repair", "full_replication"] {
        assert_eq!(sweep["survival_matrix"][strategy]["lineage_root_failure"], 0.0, "{strategy}");
    }
}

#[test]
fn invariant_lineage_aware_and_oracle_mean_survival() {
    let sweep = regenerated_json("benchmark_sweep30_seed20260813.json");
    assert_eq!(sweep["per_strategy"]["lineage_aware_repair"]["capability_survival_rate"], 0.777778);
    assert_eq!(sweep["per_strategy"]["oracle_min_cut"]["capability_survival_rate"], 0.888889);
}

#[test]
fn invariant_only_min_cut_two_survives_worst_single_domain_failure() {
    let sweep = regenerated_json("benchmark_sweep30_seed20260813.json");
    let single = regenerated_json("benchmark_seed20260813.json");
    for strategy in benchmark::STRATEGIES {
        let survives = sweep["survival_matrix"][strategy]["worst_single_domain_failure"].as_f64().unwrap();
        let min_cut = single["per_strategy"][strategy]["structural_min_cut"].as_i64().unwrap();
        assert_eq!(survives, if strategy == "oracle_min_cut" { 1.0 } else { 0.0 }, "{strategy}");
        assert_eq!(min_cut == 2, strategy == "oracle_min_cut", "{strategy}");
    }
}

#[test]
fn invariant_every_scale_verdict_is_invariant_across_k_and_h() {
    let scale = regenerated_json("scale_seed20260813.json");
    let ks: BTreeSet<i64> =
        scale["points"].as_array().unwrap().iter().map(|p| p["spec"]["slots"].as_i64().unwrap()).collect();
    let hs: BTreeSet<i64> =
        scale["points"].as_array().unwrap().iter().map(|p| p["spec"]["holders_per_slot"].as_i64().unwrap()).collect();
    assert_eq!(ks, BTreeSet::from([2, 3, 5, 8]));
    assert_eq!(hs, BTreeSet::from([2, 3]));
    for (key, row) in scale["survival_by_independence"].as_object().unwrap() {
        assert_eq!(row["invariant_across_scale"], true, "{key}");
    }
}

#[test]
fn invariant_lineage_aware_loses_to_source_count_in_exactly_one_of_six_cells() {
    let scale = regenerated_json("scale_seed20260813.json");
    let by = &scale["survival_by_independence"];
    let mut losses = Vec::new();
    for intervention in scale::SCALE_INTERVENTIONS {
        for independent in [1, 2] {
            let lineage =
                by[format!("lineage_aware::{intervention}::I{independent}")]["survival_rate"].as_f64().unwrap();
            let source = by[format!("source_count::{intervention}::I{independent}")]["survival_rate"].as_f64().unwrap();
            if lineage < source {
                losses.push(format!("{intervention}, I={independent}"));
            }
        }
    }
    assert_eq!(losses, vec!["node_failure, I=1"]);
}

#[test]
fn invariant_published_tables_are_the_sweep() {
    // The two Track A tables: mean survival per strategy, unrecovered silent forgetting and mean bytes.
    let sweep = regenerated_json("benchmark_sweep30_seed20260813.json");
    let table = [
        ("isolated_local", "0.000", "0.000", "1,371"),
        ("centralized", "0.111", "0.667", "3,020"),
        ("fixed_distributed_replication", "0.556", "0.444", "4,943"),
        ("source_count_repair", "0.556", "0.444", "4,941"),
        ("full_replication", "0.667", "0.333", "9,877"),
        ("random_path_diversification", "0.704", "0.296", "5,327"),
        ("lineage_aware_repair", "0.778", "0.222", "5,448"),
        ("oracle_min_cut", "0.889", "0.111", "6,461"),
    ];
    for (strategy, survival, forgetting, bytes) in table {
        let s = &sweep["per_strategy"][strategy];
        assert_eq!(format!("{:.3}", s["capability_survival_rate"].as_f64().unwrap()), survival, "{strategy}");
        assert_eq!(
            format!("{:.3}", s["unrecovered_silent_forgetting_rate"].as_f64().unwrap()),
            forgetting,
            "{strategy}"
        );
        let mean = format!("{:.0}", s["mean_bytes_moved"].as_f64().unwrap());
        assert_eq!(mean, bytes.replace(',', ""), "{strategy}");
    }
    // random_path_diversification is the only stochastic strategy: 0.67 on the two lineage-correlated cells.
    for intervention in ["lineage_root_failure", "authorization_revocation"] {
        let rate = sweep["survival_matrix"]["random_path_diversification"][intervention].as_f64().unwrap();
        assert_eq!(format!("{rate:.2}"), "0.67", "{intervention}");
    }
}

// =================================================================================================================
// test_figures.py
// =================================================================================================================

fn texts(svg: &str) -> Vec<String> {
    let re = Regex::new(r"<text[^>]*>([^<]*)</text>").unwrap();
    re.captures_iter(svg).map(|c| c[1].to_string()).collect()
}

fn render_all() -> IndexMap<String, String> {
    figures::render_all(&artifacts()).unwrap()
}

fn sorted(items: &[&str]) -> Vec<String> {
    let mut v: Vec<String> = items.iter().map(|s| s.to_string()).collect();
    v.sort();
    v
}

fn strings(v: &Value) -> Vec<String> {
    let mut out: Vec<String> = v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect();
    out.sort();
    out
}

// DeclaredOrderTests

#[test]
fn test_intervention_order_covers_the_benchmark_exactly() {
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    assert_eq!(
        sorted(&figures::INTERVENTION_ORDER),
        strings(&sweep["interventions"]),
        "an intervention was added to the benchmark but not to the figure"
    );
}

#[test]
fn test_strategy_order_covers_the_benchmark_exactly() {
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    assert_eq!(sorted(&figures::STRATEGY_ORDER), strings(&sweep["strategies"]));
}

#[test]
fn test_every_strategy_has_a_short_name() {
    let names: Vec<&str> = figures::SHORT_NAME.iter().map(|(k, _)| *k).collect();
    assert_eq!(sorted(&names), sorted(&figures::STRATEGY_ORDER));
}

#[test]
fn test_strategies_are_ordered_by_survival() {
    // The heatmap's staircase is a claim about ordering, not a coincidence.
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    let rates: Vec<f64> = figures::STRATEGY_ORDER
        .iter()
        .map(|s| sweep["per_strategy"][s]["capability_survival_rate"].as_f64().unwrap())
        .collect();
    let mut ordered = rates.clone();
    ordered.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(rates, ordered);
}

// FixtureSchematicTests: figure 1 is drawn from the fixture, so it must not drift from it.

#[test]
fn test_shape_is_identical_across_seeds() {
    // Only structure is drawn; the seeded symbols must not reach it.
    let baseline = figures::fixture_shape(20260813).unwrap();
    for seed in [1, 7, 4242, 99991] {
        assert_eq!(figures::fixture_shape(seed).unwrap(), baseline, "seed {seed}");
    }
}

#[test]
fn test_shape_records_every_node() {
    let shape = figures::fixture_shape(20260813).unwrap();
    assert_eq!(shape.iter().map(|e| e.node.as_str()).collect::<Vec<_>>(), vec!["node-a", "node-b", "node-c", "node-d"]);
}

#[test]
fn test_the_apparent_replica_shares_its_root_and_domain() {
    // The whole point of the figure: C is not a third chance.
    let shape = figures::fixture_shape(20260813).unwrap();
    let by_node = |node: &str| shape.iter().find(|e| e.node == node).unwrap().clone();
    let (b, c, d) = (by_node("node-b"), by_node("node-c"), by_node("node-d"));
    assert_eq!(c.root, b.root);
    assert_eq!(c.domain, b.domain);
    assert_ne!(d.root, b.root);
    assert_ne!(d.domain, b.domain);
}

#[test]
fn test_no_opaque_symbol_reaches_the_figure() {
    // Printing a premise value in a figure would leak the held-out answer.
    let svg = figures::figure_fixture(&figures::fixture_shape(20260813).unwrap());
    let fixture = generate_fixture(20260813, "schematic").unwrap();
    for symbol in [&fixture.left_symbol, &fixture.right_symbol, &fixture.expected_answer] {
        assert!(!svg.contains(symbol.as_str()), "{symbol}");
    }
}

// DeterminismTests

#[test]
fn test_rendering_twice_is_byte_identical() {
    assert_eq!(render_all(), render_all());
}

#[test]
fn test_committed_figures_match_a_fresh_render() {
    for (name, svg) in render_all() {
        let path = figure_dir().join(&name);
        assert!(path.exists(), "{name} is not committed");
        assert_eq!(std::fs::read_to_string(path).unwrap(), svg, "{name} no longer matches the artifact it plots");
    }
}

#[test]
fn test_check_mode_passes_against_the_committed_figures() {
    let output =
        std::process::Command::new(env!("CARGO_BIN_EXE_coordination")).args(["figures", "--check"]).output().unwrap();
    assert_eq!(output.status.code(), Some(0), "{}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(String::from_utf8_lossy(&output.stdout), "5 figures match their artifacts.\n");
}

#[test]
fn test_no_host_dependent_content() {
    // No path, timestamp or version string may reach the output.
    let timestamp = Regex::new(r"20\d\d-\d\d-\d\dT").unwrap();
    for (name, svg) in render_all() {
        assert!(!svg.contains("/home/"), "{name}");
        assert!(!svg.contains("/Users/"), "{name}");
        // The SVG namespace is the one URL allowed, and it is never fetched.
        assert_eq!(svg.matches("http").count(), 1, "{name}");
        assert!(svg.contains("xmlns=\"http://www.w3.org/2000/svg\""), "{name}");
        assert!(!timestamp.is_match(&svg), "{name}");
    }
}

/// A minimal XML reader for these generated SVGs (no dependency): every element as (tag, attributes), checking
/// that tags nest and close properly and that text holds no unescaped markup.
/// An element: tag name and attributes.
type Element = (String, BTreeMap<String, String>);

fn elements(svg: &str) -> Result<Vec<Element>, String> {
    let tag = Regex::new(r#"<(/?)([A-Za-z][\w:-]*)((?:\s+[\w:-]+="[^"<]*")*)\s*(/?)>"#).unwrap();
    let attribute = Regex::new(r#"([\w:-]+)="([^"]*)""#).unwrap();
    let mut stack: Vec<String> = Vec::new();
    let mut out = Vec::new();
    let mut last = 0;
    for m in tag.captures_iter(svg) {
        let whole = m.get(0).unwrap();
        let between = &svg[last..whole.start()];
        if between.contains('<') || between.contains('>') {
            return Err(format!("stray markup before {}", whole.as_str()));
        }
        if between.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "").contains('&') {
            return Err(format!("unescaped & before {}", whole.as_str()));
        }
        last = whole.end();
        let name = m[2].to_string();
        if &m[1] == "/" {
            if stack.pop().as_deref() != Some(name.as_str()) {
                return Err(format!("mismatched </{name}>"));
            }
            continue;
        }
        let attributes = attribute.captures_iter(&m[3]).map(|a| (a[1].to_string(), a[2].to_string())).collect();
        out.push((name.clone(), attributes));
        if &m[4] != "/" {
            stack.push(name);
        }
    }
    if !svg[last..].trim().is_empty() || !stack.is_empty() {
        return Err(format!("unclosed {stack:?}"));
    }
    Ok(out)
}

// WellFormednessTests

#[test]
fn test_every_figure_parses_as_xml() {
    for (name, svg) in render_all() {
        let elements = elements(&svg).unwrap_or_else(|e| panic!("{name}: {e}"));
        assert!(elements[0].0.ends_with("svg"), "{name}");
    }
}

#[test]
fn test_no_figure_references_an_external_resource() {
    // A figure that fetches anything is not reproducible in a paper build.
    for (name, svg) in render_all() {
        assert!(!svg.contains("<image"), "{name}");
        assert!(!svg.contains("xlink:href"), "{name}");
        assert!(!svg.contains("@import"), "{name}");
    }
}

#[test]
fn test_marks_stay_inside_the_canvas() {
    // Cheap guard against a figure silently growing off its own viewBox.
    for (name, svg) in render_all() {
        let elements = elements(&svg).unwrap();
        let width: f64 = elements[0].1["width"].parse().unwrap();
        let height: f64 = elements[0].1["height"].parse().unwrap();
        for (tag, attributes) in &elements {
            if !["text", "circle", "rect", "line"].contains(&tag.as_str()) {
                continue;
            }
            for (attribute, limit) in [
                ("x", width),
                ("cx", width),
                ("x1", width),
                ("x2", width),
                ("y", height),
                ("cy", height),
                ("y1", height),
                ("y2", height),
            ] {
                if let Some(value) = attributes.get(attribute) {
                    assert!(value.parse::<f64>().unwrap() <= limit, "{name}: {tag} {attribute}={value}");
                }
            }
        }
    }
}

// FidelityTests: the drawn numbers must be the artifact's numbers, not a retelling.

#[test]
fn test_heatmap_prints_every_cell_of_the_sweep() {
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    let printed = texts(&figures::figure_survival_matrix(&sweep).unwrap());
    for strategy in figures::STRATEGY_ORDER {
        for intervention in figures::INTERVENTION_ORDER {
            let rate = sweep["cells"][format!("{strategy}::{intervention}")]["survival_rate"].as_f64().unwrap();
            assert!(printed.contains(&format!("{rate:.2}")), "{strategy}::{intervention}");
        }
    }
    let rate_like = Regex::new(r"^[01]\.\d\d$").unwrap();
    assert_eq!(
        printed.iter().filter(|t| rate_like.is_match(t)).count(),
        figures::STRATEGY_ORDER.len() * (figures::INTERVENTION_ORDER.len() + 1),
        "every cell plus one row mean per strategy"
    );
}

#[test]
fn test_heatmap_row_means_match_per_strategy() {
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    let printed = texts(&figures::figure_survival_matrix(&sweep).unwrap());
    for strategy in figures::STRATEGY_ORDER {
        let rate = sweep["per_strategy"][strategy]["capability_survival_rate"].as_f64().unwrap();
        assert!(printed.contains(&format!("{rate:.2}")), "{strategy}");
    }
}

#[test]
fn test_pareto_places_lineage_aware_above_and_left_of_full_replication() {
    // C4 is a dominance claim; if the geometry inverts, the figure lies.
    let sweep = artifact("benchmark_sweep30_seed20260813.json");
    let lineage = &sweep["per_strategy"]["lineage_aware_repair"];
    let full = &sweep["per_strategy"]["full_replication"];
    assert!(lineage["capability_survival_rate"].as_f64() > full["capability_survival_rate"].as_f64());
    assert!(lineage["mean_bytes_moved"].as_f64() < full["mean_bytes_moved"].as_f64());
}

#[test]
fn test_scale_grid_draws_one_mark_per_recorded_point() {
    let scale = artifact("scale_seed20260813.json");
    let svg = figures::figure_scale(&scale).unwrap();
    assert_eq!(svg.matches("<circle").count(), scale["points"].as_array().unwrap().len());
}

#[test]
fn test_scale_grid_rows_are_uniform_exactly_where_the_artifact_says() {
    let scale = artifact("scale_seed20260813.json");
    let mut rows: BTreeMap<(String, String, i64), BTreeSet<bool>> = BTreeMap::new();
    for point in scale["points"].as_array().unwrap() {
        let key = (
            point["repair_policy"].as_str().unwrap().to_string(),
            point["intervention"].as_str().unwrap().to_string(),
            point["spec"]["independent_roots"].as_i64().unwrap(),
        );
        rows.entry(key).or_default().insert(point["capability_survival"] == true);
    }
    for (key, verdicts) in rows {
        assert_eq!(verdicts.len(), 1, "{key:?}: a row is not invariant across K and H");
    }
}

#[test]
fn test_silent_forgetting_lanes_partition_the_cells() {
    let single = artifact("benchmark_seed20260813.json");
    let svg = figures::figure_silent_forgetting(&single).unwrap();
    let counts: Vec<usize> = texts(&svg)
        .iter()
        .filter(|t| t.ends_with(" cells"))
        .map(|t| t.split(' ').next().unwrap().parse().unwrap())
        .collect();
    assert_eq!(counts.iter().sum::<usize>(), single["cells"].as_object().unwrap().len());
}

#[test]
fn test_the_lost_lane_reaches_the_highest_confidence_drawn() {
    // The figure's headline. If this inverts, the claim is gone.
    let single = artifact("benchmark_seed20260813.json");
    let cells: Vec<&Value> = single["cells"].as_object().unwrap().values().collect();
    let confidence = |c: &Value| c["post_intervention_confidence"].as_f64().unwrap();
    let kept: Vec<f64> = cells.iter().filter(|c| c["capability_survival"] == true).map(|c| confidence(c)).collect();
    let lost: Vec<f64> = cells
        .iter()
        .filter(|c| c["capability_survival"] != true && confidence(c) > 0.0)
        .map(|c| confidence(c))
        .collect();
    assert!(!lost.is_empty());
    assert!(lost.iter().cloned().fold(f64::MIN, f64::max) >= kept.iter().cloned().fold(f64::MIN, f64::max));
}

// PinningTests

#[test]
fn test_sha256sums_covers_every_committed_figure() {
    let recorded: BTreeSet<String> = sha256sums().into_keys().filter(|n| n.starts_with("figures/")).collect();
    let on_disk: BTreeSet<String> = std::fs::read_dir(figure_dir())
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .filter(|n| n.ends_with(".svg"))
        .map(|n| format!("figures/{n}"))
        .collect();
    assert_eq!(on_disk, recorded);
}

#[test]
fn test_every_figure_matches_its_recorded_digest() {
    for (name, digest) in sha256sums() {
        if !name.starts_with("figures/") {
            continue;
        }
        assert_eq!(sha256(&std::fs::read(artifacts().join(&name)).unwrap()), digest, "{name}");
    }
}
