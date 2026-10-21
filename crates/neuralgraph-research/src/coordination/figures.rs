//! Render the paper's figures from the pinned artifacts, deterministically (port of
//! `NeuralGraph/coordination/figures.py` at `13a1729`).
//!
//! `docs/PAPER.md` names five figures. Figures 2-5 plot numbers that already exist in the artifact JSON
//! (`research/coordination/artifacts/*.json`); figure 1 is the fixture schematic, drawn from
//! [`generate_fixture`] rather than by hand so it cannot drift from the code.
//!
//! Why SVG by hand: every result here is a byte-reproducible artifact checked against `SHA256SUMS`, and a figure
//! is a result. A plotting library's SVG embeds version strings and host font metrics; emitting the SVG directly
//! depends only on the artifact JSON and this source — every number is formatted at fixed precision and text is
//! positioned by anchor, never by measured width. The figures are therefore pinned exactly like the JSON.
//!
//! Every float expression below keeps the Python's operation order, because the printed coordinates are the
//! rounded results of exactly those operations.

use crate::coordination::contracts::{CoordinationError, CoordinationResult};
use crate::coordination::fixture::generate_fixture;
use indexmap::IndexMap;
use serde_json::Value;
use std::collections::BTreeSet;
use std::path::PathBuf;

/// `ARTIFACTS`: the committed artifact directory (`research/coordination/artifacts` in the repository).
pub fn artifacts_dir() -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../research/coordination/artifacts");
    dir.canonicalize().unwrap_or(dir)
}

/// `FIGURES`: where the figures are written by default.
pub fn figures_dir() -> PathBuf {
    artifacts_dir().join("figures")
}

// Print-safe palette: a single sequential ramp survives greyscale printing, and the categorical marks differ in
// shape as well as colour.
pub const INK: &str = "#1b1b1f";
pub const MUTED: &str = "#6b7280";
pub const GRID: &str = "#d8dade";
pub const ACCENT: &str = "#1f5fa8";
pub const WARN: &str = "#b3441c";
pub const RAMP_LOW: [i64; 3] = [243, 246, 249];
pub const RAMP_HIGH: [i64; 3] = [23, 78, 143];

pub const FONT: &str = "'Source Sans Pro','Helvetica Neue',Helvetica,Arial,sans-serif";

/// Interventions in a fixed reading order: the five every strategy meets the same way, the three that separate
/// the strategies, then the partition. Declared rather than sorted; a test asserts it covers the artifact.
pub const INTERVENTION_ORDER: [&str; 9] = [
    "node_failure",
    "memory_deletion",
    "route_removal",
    "edge_corruption",
    "stale_knowledge",
    "authorization_revocation",
    "lineage_root_failure",
    "worst_single_domain_failure",
    "network_partition",
];

/// Strategies ordered by survival, floor first, so the heatmap reads as a staircase.
pub const STRATEGY_ORDER: [&str; 8] = [
    "isolated_local",
    "centralized",
    "fixed_distributed_replication",
    "source_count_repair",
    "full_replication",
    "random_path_diversification",
    "lineage_aware_repair",
    "oracle_min_cut",
];

pub const SHORT_NAME: [(&str, &str); 8] = [
    ("isolated_local", "isolated local"),
    ("centralized", "centralized"),
    ("fixed_distributed_replication", "fixed replication"),
    ("source_count_repair", "source-count repair"),
    ("full_replication", "full replication"),
    ("random_path_diversification", "random diversification"),
    ("lineage_aware_repair", "lineage-aware repair"),
    ("oracle_min_cut", "oracle min-cut"),
];

fn short_name(strategy: &str) -> CoordinationResult<&'static str> {
    SHORT_NAME
        .iter()
        .find(|(k, _)| *k == strategy)
        .map(|(_, v)| *v)
        .ok_or_else(|| CoordinationError::key_error(strategy))
}

/// `_num(value, places)`: fixed precision, and never `-0.00`.
pub fn num(value: f64, places: usize) -> String {
    let text = format!("{value:.places$}");
    if text.starts_with('-') && text.parse::<f64>().is_ok_and(|v| v == 0.0) {
        return format!("0.{}", "0".repeat(places));
    }
    text
}

fn num2(value: f64) -> String {
    num(value, 2)
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// The keyword arguments of `_text` (defaults: size 11, start anchor, ink, normal weight, no rotation).
#[derive(Clone, Copy, Debug)]
pub struct TextStyle {
    pub size: f64,
    pub anchor: &'static str,
    pub fill: &'static str,
    pub weight: &'static str,
    pub rotate: Option<f64>,
}

impl Default for TextStyle {
    fn default() -> Self {
        TextStyle { size: 11.0, anchor: "start", fill: INK, weight: "normal", rotate: None }
    }
}

impl TextStyle {
    fn size(mut self, size: f64) -> Self {
        self.size = size;
        self
    }
    fn anchor(mut self, anchor: &'static str) -> Self {
        self.anchor = anchor;
        self
    }
    fn fill(mut self, fill: &'static str) -> Self {
        self.fill = fill;
        self
    }
    fn weight(mut self, weight: &'static str) -> Self {
        self.weight = weight;
        self
    }
    fn rotate(mut self, rotate: f64) -> Self {
        self.rotate = Some(rotate);
        self
    }
}

fn style() -> TextStyle {
    TextStyle::default()
}

/// `_text(x, y, body, ...)`
fn text(x: f64, y: f64, body: &str, s: TextStyle) -> String {
    let transform = match s.rotate {
        // `if rotate` is false for 0 as well as None
        Some(r) if r != 0.0 => format!(" transform=\"rotate({} {} {})\"", num(r, 1), num(x, 1), num(y, 1)),
        _ => String::new(),
    };
    format!(
        "<text x=\"{}\" y=\"{}\" font-size=\"{}\" text-anchor=\"{}\" fill=\"{}\" font-weight=\"{}\"{transform}>{}</text>",
        num(x, 1),
        num(y, 1),
        num(s.size, 1),
        s.anchor,
        s.fill,
        s.weight,
        escape(body)
    )
}

/// Sequential low→high ramp, linear in the value so cells are comparable.
pub fn ramp(value: f64) -> String {
    // max(0.0, min(1.0, value)) with Python's first-wins comparisons
    let upper = if value < 1.0 { value } else { 1.0 };
    let clamped = if upper > 0.0 { upper } else { 0.0 };
    let mut out = String::from("#");
    for (low, high) in RAMP_LOW.iter().zip(RAMP_HIGH.iter()) {
        let channel = (*low as f64 + (high - low) as f64 * clamped).round_ties_even() as i64;
        out.push_str(&format!("{channel:02x}"));
    }
    out
}

fn document(width: f64, height: f64, title: &str, body: Vec<String>) -> String {
    let mut lines = vec![
        format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{}\" height=\"{}\" viewBox=\"0 0 {} {}\" font-family=\"{FONT}\">",
            num(width, 1),
            num(height, 1),
            num(width, 1),
            num(height, 1)
        ),
        format!("<title>{}</title>", escape(title)),
        format!("<rect width=\"{}\" height=\"{}\" fill=\"#ffffff\"/>", num(width, 1), num(height, 1)),
    ];
    lines.extend(body);
    lines.push("</svg>".into());
    lines.push(String::new());
    lines.join("\n")
}

/// `mapping[key]`, raising `KeyError` like Python for a malformed artifact.
fn key<'a>(v: &'a Value, k: &str) -> CoordinationResult<&'a Value> {
    v.get(k).ok_or_else(|| CoordinationError::key_error(k))
}

fn float(v: &Value) -> CoordinationResult<f64> {
    v.as_f64().ok_or_else(|| CoordinationError::type_error(format!("expected a number, got {v}")))
}

/// `_load(name)`: one artifact JSON from `dir`.
pub fn load(dir: &std::path::Path, name: &str) -> CoordinationResult<Value> {
    let path = dir.join(name);
    let text = std::fs::read_to_string(&path)
        .map_err(|e| CoordinationError::new("FileNotFoundError", format!("{}: {}", e, path.display())))?;
    serde_json::from_str(&text).map_err(|e| CoordinationError::new("JSONDecodeError", e.to_string()))
}

// ---------------------------------------------------------------------------------------------------------------
// Figure 1 — the fixture
// ---------------------------------------------------------------------------------------------------------------

/// One record of the fixture, structural fields only.
#[derive(Clone, Debug, PartialEq)]
pub struct ShapeEntry {
    pub node: String,
    pub slot: String,
    pub root: String,
    pub domain: String,
    pub confidence: f64,
}

/// The fixture's structure, read from the fixture itself: node, slot, lineage root, failure domain, confidence —
/// never the opaque symbols, which are seeded (the figure would become seed-dependent) and would print the
/// answer the experiment is built to keep unprinted.
pub fn fixture_shape(seed: i64) -> CoordinationResult<Vec<ShapeEntry>> {
    let fixture = generate_fixture(seed, "schematic")?;
    let mut node_ids: Vec<&String> = fixture.records_by_node.keys().collect();
    node_ids.sort();
    let mut shape = Vec::new();
    for node_id in node_ids {
        for record in &fixture.records_by_node[node_id] {
            shape.push(ShapeEntry {
                node: node_id.clone(),
                slot: record.slot.clone(),
                root: record.lineage_root_ids[0].clone(),
                domain: record.failure_domains[0].clone(),
                confidence: record.confidence,
            });
        }
    }
    Ok(shape)
}

/// Why a third copy of a premise is not a third chance of answering it.
pub fn figure_fixture(shape: &[ShapeEntry]) -> String {
    let (width, height) = (760.0, 400.0);
    let (box_w, box_h) = (132.0, 96.0);
    let gap = 26.0;
    let left = 40.0;
    let top = 156.0;

    let mut body = vec![
        text(28.0, 34.0, "Three holders of the same premise, two ways to lose it", style().size(15.0).weight("600")),
        text(
            28.0,
            54.0,
            "the four-node fixture: a capability needs one 'left' and one 'right', and no node holds both",
            style().size(10.5).fill(MUTED),
        ),
    ];

    // The capability bar, drawn above the nodes so the arrows read upward into it.
    let bar_y = 92.0;
    body.push(format!(
        "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"34\" rx=\"4\" fill=\"#f3f6f9\" stroke=\"{GRID}\"/>",
        num(left, 1),
        num(bar_y, 1),
        num(width - 2.0 * left, 1)
    ));
    body.push(text(
        width / 2.0,
        bar_y + 22.0,
        "capability  =  left  ⊕  right      (answer held by no single node)",
        style().size(11.5).anchor("middle"),
    ));

    for (index, entry) in shape.iter().enumerate() {
        let x = left + (box_w + gap) * index as f64;
        let earlier = &shape[..index];
        let independent = !earlier.iter().any(|o| o.root == entry.root) && earlier.iter().any(|o| o.slot == entry.slot);
        let shared = shape.iter().any(|o| o.root == entry.root && o.node != entry.node);
        let colour = if shared { WARN } else { ACCENT };
        body.push(format!(
            "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" rx=\"5\" fill=\"#ffffff\" stroke=\"{colour}\" stroke-width=\"1.4\"/>",
            num(x, 1),
            num(top, 1),
            num(box_w, 1),
            num(box_h, 1)
        ));
        body.push(text(x + 12.0, top + 22.0, &entry.node, style().size(11.5).weight("600")));
        body.push(text(x + 12.0, top + 42.0, &format!("slot: {}", entry.slot), style().size(10.5).fill(INK)));
        body.push(text(
            x + 12.0,
            top + 60.0,
            &format!("lineage root: {}", entry.root),
            style().size(10.0).fill(colour),
        ));
        body.push(text(
            x + 12.0,
            top + 76.0,
            &format!("failure domain: {}", entry.domain),
            style().size(10.0).fill(colour),
        ));
        body.push(text(
            x + box_w - 12.0,
            top + 22.0,
            &num2(entry.confidence),
            style().size(10.5).anchor("end").fill(MUTED),
        ));
        body.push(format!(
            "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" stroke=\"{colour}\" stroke-width=\"1.2\" opacity=\"0.6\"/>",
            num(x + box_w / 2.0, 1),
            num(top, 1),
            num(x + box_w / 2.0, 1),
            num(bar_y + 34.0, 1)
        ));
        if independent {
            body.push(text(
                x + box_w / 2.0,
                top + box_h + 20.0,
                "independent path",
                style().size(10.0).anchor("middle").fill(ACCENT).weight("600"),
            ));
        }
    }

    let shared_nodes: Vec<&str> = shape
        .iter()
        .filter(|e| shape.iter().filter(|o| o.root == e.root).count() > 1)
        .map(|e| e.node.as_str())
        .collect();
    let brace_y = top + box_h + 40.0;
    body.push(text(
        28.0,
        brace_y + 14.0,
        &format!(
            "{} hold the same premise under the same lineage root and the same failure domain.",
            shared_nodes.join(" and ")
        ),
        style().size(10.5).fill(WARN),
    ));
    body.push(text(
        28.0,
        brace_y + 30.0,
        "They look like two chances of answering. They are one: whatever invalidates that root takes both, and no \
         local signal reports it.",
        style().size(10.5).fill(WARN),
    ));
    body.push(text(
        28.0,
        brace_y + 52.0,
        "Only the node on an independent root adds a second failure domain — which is what raises the minimum cut \
         from 1 to 2,",
        style().size(10.5).fill(ACCENT),
    ));
    body.push(text(
        28.0,
        brace_y + 68.0,
        "and the min cut, not the replica count, is what predicts survival (figure 2, C5).",
        style().size(10.5).fill(ACCENT),
    ));
    document(width, height, "Figure 1: the four-node fixture", body)
}

// ---------------------------------------------------------------------------------------------------------------
// Figure 2 — survival matrix
// ---------------------------------------------------------------------------------------------------------------

/// 8 strategies x 9 interventions, mean survival over the seed sweep. Values come from `cells`, not from
/// `per_strategy`, so a cell and its row mean can be checked against each other in the figure itself.
pub fn figure_survival_matrix(sweep: &Value) -> CoordinationResult<String> {
    let (left, top) = (210.0, 118.0);
    let (cell_w, cell_h) = (74.0, 34.0);
    let (rows, cols) = (STRATEGY_ORDER.len() as f64, INTERVENTION_ORDER.len() as f64);
    let width = left + cell_w * cols + 96.0;
    let height = top + cell_h * rows + 74.0;

    let mut body = vec![
        text(28.0, 34.0, "Capability survival by placement/repair strategy", style().size(15.0).weight("600")),
        text(
            28.0,
            54.0,
            "mean over 30 seeds; 1.00 = the collective still answers after the intervention",
            style().size(10.5).fill(MUTED),
        ),
    ];

    for (c, intervention) in INTERVENTION_ORDER.iter().enumerate() {
        let x = left + cell_w * c as f64 + cell_w / 2.0;
        body.push(text(
            x,
            top - 10.0,
            &intervention.replace('_', " "),
            style().size(9.5).anchor("start").fill(MUTED).rotate(-38.0),
        ));
    }

    let cells = key(sweep, "cells")?;
    for (r, strategy) in STRATEGY_ORDER.iter().enumerate() {
        let y = top + cell_h * r as f64;
        body.push(text(left - 12.0, y + cell_h / 2.0 + 4.0, short_name(strategy)?, style().size(10.5).anchor("end")));
        for (c, intervention) in INTERVENTION_ORDER.iter().enumerate() {
            let cell = key(cells, &format!("{strategy}::{intervention}"))?;
            let rate = float(key(cell, "survival_rate")?)?;
            let x = left + cell_w * c as f64;
            body.push(format!(
                "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" fill=\"{}\" stroke=\"{GRID}\"/>",
                num(x, 1),
                num(y, 1),
                num(cell_w - 2.0, 1),
                num(cell_h - 2.0, 1),
                ramp(rate)
            ));
            body.push(text(
                x + (cell_w - 2.0) / 2.0,
                y + cell_h / 2.0 + 4.0,
                &num2(rate),
                style().size(10.0).anchor("middle").fill(if rate > 0.55 { "#ffffff" } else { INK }),
            ));
        }
        let row_mean = float(key(key(key(sweep, "per_strategy")?, strategy)?, "capability_survival_rate")?)?;
        body.push(text(
            left + cell_w * cols + 10.0,
            y + cell_h / 2.0 + 4.0,
            &num2(row_mean),
            style().size(10.5).weight("600"),
        ));
    }

    body.push(text(left + cell_w * cols + 10.0, top - 10.0, "mean", style().size(9.5).fill(MUTED)));
    body.push(text(
        28.0,
        height - 30.0,
        "Replica count does not order the rows: full replication (8 records) sits below lineage-aware repair (4).",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 14.0,
        "Every strategy fails the network partition except the two that keep both premises on one node — reported \
         against interest.",
        style().size(10.0).fill(MUTED),
    ));
    Ok(document(width, height, "Figure 2: capability survival matrix", body))
}

// ---------------------------------------------------------------------------------------------------------------
// Figure 3 — Pareto: survival vs storage
// ---------------------------------------------------------------------------------------------------------------

/// Survival against bytes moved. The claim is dominance, so it must be visible.
pub fn figure_pareto(sweep: &Value) -> CoordinationResult<String> {
    let (left, right, top, bottom) = (92.0, 560.0, 96.0, 356.0);
    let (width, height) = (760.0, 470.0);

    let per_strategy = key(sweep, "per_strategy")?;
    let mut points: Vec<(&str, f64, f64)> = Vec::new();
    for strategy in STRATEGY_ORDER {
        let s = key(per_strategy, strategy)?;
        points.push((strategy, float(key(s, "mean_bytes_moved")?)?, float(key(s, "capability_survival_rate")?)?));
    }
    let x_max = 11000.0;
    let px = |bytes_moved: f64| left + (right - left) * (bytes_moved / x_max);
    let py = |rate: f64| bottom - (bottom - top) * rate;

    let mut body = vec![
        text(28.0, 34.0, "Survival is not bought with storage", style().size(15.0).weight("600")),
        text(
            28.0,
            54.0,
            "mean bytes moved per intervention vs mean capability survival, 30 seeds",
            style().size(10.5).fill(MUTED),
        ),
    ];

    for tick in 0..6 {
        let rate = tick as f64 / 5.0;
        let y = py(rate);
        body.push(format!(
            "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" stroke=\"{GRID}\"/>",
            num(left, 1),
            num(y, 1),
            num(right, 1),
            num(y, 1)
        ));
        body.push(text(left - 10.0, y + 4.0, &num(rate, 1), style().size(10.0).anchor("end").fill(MUTED)));
    }
    for tick in 0..6 {
        let value = x_max * tick as f64 / 5.0;
        let x = px(value);
        body.push(format!(
            "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" stroke=\"{GRID}\"/>",
            num(x, 1),
            num(top, 1),
            num(x, 1),
            num(bottom, 1)
        ));
        body.push(text(
            x,
            bottom + 18.0,
            &format!("{:.1}k", value / 1000.0),
            style().size(10.0).anchor("middle").fill(MUTED),
        ));
    }

    body.push(text((left + right) / 2.0, bottom + 40.0, "mean bytes moved", style().size(11.0).anchor("middle")));
    body.push(text(
        26.0,
        (top + bottom) / 2.0,
        "capability survival",
        style().size(11.0).anchor("middle").rotate(-90.0),
    ));

    // The dominance shading: everything below-right of lineage-aware repair is strictly worse on both axes.
    let lineage = *points
        .iter()
        .find(|p| p.0 == "lineage_aware_repair")
        .ok_or_else(|| CoordinationError::new("StopIteration", ""))?;
    body.push(format!(
        "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" fill=\"{ACCENT}\" opacity=\"0.06\"/>",
        num(px(lineage.1), 1),
        num(py(lineage.2), 1),
        num(right - px(lineage.1), 1),
        num(bottom - py(lineage.2), 1)
    ));
    body.push(text(
        px(lineage.1) + 10.0,
        bottom - 12.0,
        "dominated: more bytes, less survival",
        style().size(9.5).fill(ACCENT),
    ));

    let mut placed_labels: Vec<f64> = Vec::new();
    for (strategy, bytes_moved, rate) in &points {
        let (x, y) = (px(*bytes_moved), py(*rate));
        let highlight = *strategy == "lineage_aware_repair" || *strategy == "full_replication";
        let colour = if *strategy == "lineage_aware_repair" {
            ACCENT
        } else if *strategy == "full_replication" {
            WARN
        } else {
            INK
        };
        if *strategy == "oracle_min_cut" {
            body.push(format!(
                "<polygon points=\"{},{} {},{} {},{}\" fill=\"none\" stroke=\"{MUTED}\" stroke-width=\"1.4\"/>",
                num(x, 1),
                num(y - 6.0, 1),
                num(x + 6.0, 1),
                num(y + 5.0, 1),
                num(x - 6.0, 1),
                num(y + 5.0, 1)
            ));
        } else {
            body.push(format!(
                "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" fill=\"{colour}\"/>",
                num(x, 1),
                num(y, 1),
                num(if highlight { 5.5 } else { 4.0 }, 1)
            ));
        }
        let (anchor, dx) = if *bytes_moved > 6000.0 { ("end", -10.0) } else { ("start", 10.0) };
        // Two strategies sit on the same survival at nearly the same cost; nudge a label that lands within a line
        // height of one already placed.
        let mut label_y = y + 4.0;
        while placed_labels.iter().any(|placed| (label_y - placed).abs() < 12.0) {
            label_y += 12.0;
        }
        placed_labels.push(label_y);
        body.push(text(
            x + dx,
            label_y,
            short_name(strategy)?,
            style().size(10.0).anchor(anchor).fill(colour).weight(if highlight { "600" } else { "normal" }),
        ));
    }

    body.push(text(
        28.0,
        height - 46.0,
        "Lineage-aware repair reaches 0.78 at 5.4k bytes; full replication reaches 0.67 at 9.9k — twice the storage, \
         less survival.",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 30.0,
        "The oracle (open triangle) is an upper bound, not a deployable policy: it separates from lineage-aware \
         repair on one intervention.",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 14.0,
        "Survival averages 9 interventions including the partition, which no distributed strategy survives.",
        style().size(10.0).fill(MUTED),
    ));
    Ok(document(width, height, "Figure 3: survival versus storage", body))
}

// ---------------------------------------------------------------------------------------------------------------
// Figure 4 — scale invariance
// ---------------------------------------------------------------------------------------------------------------

/// Every scale point's verdict, as a grid: rows are the conditions that change the verdict, columns the scale
/// parameters that do not, so invariance shows as rows of uniform colour and one exception as one odd mark.
pub fn figure_scale(scale: &Value) -> CoordinationResult<String> {
    let points = key(scale, "points")?.as_array().cloned().unwrap_or_default();
    let int = |v: &Value| v.as_i64().unwrap_or(0);
    let mut ks = BTreeSet::new();
    let mut hs = BTreeSet::new();
    let mut policies = BTreeSet::new();
    let mut interventions = BTreeSet::new();
    let mut independences = BTreeSet::new();
    // (policy, intervention, independent roots, K, H) -> survived
    type VerdictKey = (String, String, i64, i64, i64);
    let mut verdicts: Vec<(VerdictKey, bool)> = Vec::new();
    for point in &points {
        let spec = key(point, "spec")?;
        let (slots, holders, independent) =
            (int(key(spec, "slots")?), int(key(spec, "holders_per_slot")?), int(key(spec, "independent_roots")?));
        let policy = key(point, "repair_policy")?.as_str().unwrap_or_default().to_string();
        let intervention = key(point, "intervention")?.as_str().unwrap_or_default().to_string();
        ks.insert(slots);
        hs.insert(holders);
        policies.insert(policy.clone());
        interventions.insert(intervention.clone());
        independences.insert(independent);
        let survived = *key(point, "capability_survival")? == true;
        let k = (policy, intervention, independent, slots, holders);
        // a dict assignment: a repeated key keeps its slot and takes the last value
        match verdicts.iter_mut().find(|(existing, _)| *existing == k) {
            Some(entry) => entry.1 = survived,
            None => verdicts.push((k, survived)),
        }
    }
    let verdict = |policy: &str, intervention: &str, independence: i64, k: i64, h: i64| -> CoordinationResult<bool> {
        verdicts
            .iter()
            .find(|((p, i, ind, kk, hh), _)| {
                p == policy && i == intervention && *ind == independence && *kk == k && *hh == h
            })
            .map(|(_, v)| *v)
            .ok_or_else(|| {
                CoordinationError::new("KeyError", format!("({policy}, {intervention}, {independence}, {k}, {h})"))
            })
    };

    let mut rows: Vec<(&String, &String, i64)> = Vec::new();
    for policy in &policies {
        for independence in &independences {
            for intervention in &interventions {
                rows.push((policy, intervention, *independence));
            }
        }
    }
    let cols: Vec<(i64, i64)> = ks.iter().flat_map(|k| hs.iter().map(move |h| (*k, *h))).collect();

    let (left, top) = (268.0, 132.0);
    let cell = 30.0;
    let width = left + cell * cols.len() as f64 + 150.0;
    let height = top + cell * rows.len() as f64 + 92.0;

    let mut body = vec![
        text(28.0, 34.0, "Every verdict is invariant in scale", style().size(15.0).weight("600")),
        text(
            28.0,
            54.0,
            "one mark per run: filled = capability survived the intervention, hollow = lost",
            style().size(10.5).fill(MUTED),
        ),
    ];

    for (index, (k, h)) in cols.iter().enumerate() {
        let x = left + cell * index as f64 + cell / 2.0;
        body.push(text(x, top - 26.0, &format!("K={k}"), style().size(10.0).anchor("middle").fill(MUTED)));
        body.push(text(x, top - 12.0, &format!("H={h}"), style().size(9.5).anchor("middle").fill(MUTED)));
    }

    let max_independence = independences.iter().max().copied();
    for (index, (policy, intervention, independence)) in rows.iter().enumerate() {
        let y = top + cell * index as f64;
        let label = format!("{} · {}", policy.replace('_', "-"), intervention.replace('_', " "));
        body.push(text(left - 46.0, y + cell / 2.0 + 4.0, &label, style().size(10.0).anchor("end")));
        let colour = if Some(*independence) == max_independence { ACCENT } else { WARN };
        body.push(text(
            left - 12.0,
            y + cell / 2.0 + 4.0,
            &format!("I{independence}"),
            style().size(10.0).anchor("end").fill(colour).weight("600"),
        ));
        let mut survived_count = 0usize;
        for (col, (k, h)) in cols.iter().enumerate() {
            let survived = verdict(policy, intervention, *independence, *k, *h)?;
            let (cx, cy) = (left + cell * col as f64 + cell / 2.0, y + cell / 2.0);
            body.push(format!(
                "<circle cx=\"{}\" cy=\"{}\" r=\"7\" fill=\"{}\" stroke=\"{colour}\" stroke-width=\"1.3\"/>",
                num(cx, 1),
                num(cy, 1),
                if survived { colour } else { "#ffffff" }
            ));
        }
        for (k, h) in &cols {
            if verdict(policy, intervention, *independence, *k, *h)? {
                survived_count += 1;
            }
        }
        let rate = survived_count as f64 / cols.len() as f64;
        body.push(text(
            left + cell * cols.len() as f64 + 14.0,
            y + cell / 2.0 + 4.0,
            &num2(rate),
            style().size(10.5).weight("600"),
        ));
    }

    body.push(text(left + cell * cols.len() as f64 + 14.0, top - 12.0, "survival", style().size(9.5).fill(MUTED)));
    body.push(text(
        28.0,
        height - 62.0,
        "Rows are policy x intervention x lineage independence; columns are premises per capability (K) and holders \
         per premise (H).",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 46.0,
        "Every row is uniform: the verdict is set by lineage independence, never by K or H. One exception would show \
         as a single odd mark.",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 30.0,
        "The one I1 row that survives is source-count repair under node failure: a correlated replica serves when the \
         node, not the lineage, is what failed.",
        style().size(10.0).fill(WARN),
    ));
    body.push(text(
        28.0,
        height - 14.0,
        &format!(
            "{} runs, seed {}, from scale_seed20260813.json.",
            rows.len() * cols.len(),
            crate::coordination::core::py_str_value(key(scale, "seed")?)
        ),
        style().size(10.0).fill(MUTED),
    ));
    Ok(document(width, height, "Figure 4: scale invariance", body))
}

// ---------------------------------------------------------------------------------------------------------------
// Figure 5 — silent forgetting
// ---------------------------------------------------------------------------------------------------------------

/// Where the coordinator's confidence sits when the capability is gone: a strip plot of post-intervention
/// confidence split by outcome, because the claim is an ordering (lost cells report higher confidence than kept
/// ones), which a confidence-vs-coverage scatter would hide under overlapping marks.
pub fn figure_silent_forgetting(benchmark: &Value) -> CoordinationResult<String> {
    let (left, right) = (250.0, 600.0);
    let (width, height) = (760.0, 372.0);
    let (x_min, x_max) = (0.88, 1.0);

    let lanes: [(&str, &str, &'static str); 3] = [
        ("capability intact", "kept", ACCENT),
        ("lost, silently: still confident", "silent", WARN),
        ("lost, visibly: no answer offered", "visible", MUTED),
    ];
    // (lane, confidence) -> count, in first-seen order (Python dict; float keys compare with ==)
    let mut counts: Vec<(&str, f64, usize)> = Vec::new();
    let cells = key(benchmark, "cells")?.as_object().cloned().unwrap_or_default();
    let mut cell_keys: Vec<&String> = cells.keys().collect();
    cell_keys.sort();
    for cell_key in cell_keys {
        let cell = &cells[cell_key];
        let confidence = float(key(cell, "post_intervention_confidence")?)?;
        let lane = if *key(cell, "capability_survival")? == true {
            "kept"
        } else if confidence <= 0.0 {
            "visible"
        } else {
            "silent"
        };
        match counts.iter_mut().find(|(l, c, _)| *l == lane && *c == confidence) {
            Some(entry) => entry.2 += 1,
            None => counts.push((lane, confidence, 1)),
        }
    }

    let px = |confidence: f64| left + (right - left) * (confidence - x_min) / (x_max - x_min);

    let mut body = vec![
        text(28.0, 34.0, "Lost capabilities report the highest confidence of all", style().size(15.0).weight("600")),
        text(
            28.0,
            54.0,
            "post-intervention confidence per strategy x intervention cell, single pinned seed",
            style().size(10.5).fill(MUTED),
        ),
    ];

    let top = 118.0;
    let lane_h = 46.0;
    let lane_count = lanes.len() as f64;
    for tick in 0..7 {
        let confidence = x_min + (x_max - x_min) * tick as f64 / 6.0;
        let x = px(confidence);
        body.push(format!(
            "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" stroke=\"{GRID}\"/>",
            num(x, 1),
            num(top - 24.0, 1),
            num(x, 1),
            num(top + lane_h * lane_count - 12.0, 1)
        ));
        body.push(text(
            x,
            top + lane_h * lane_count + 6.0,
            &num2(confidence),
            style().size(10.0).anchor("middle").fill(MUTED),
        ));
    }

    for (index, (label, lane, colour)) in lanes.iter().enumerate() {
        let y = top + lane_h * index as f64;
        body.push(text(
            left - 20.0,
            y + 4.0,
            label,
            style().size(10.5).anchor("end").fill(colour).weight(if *lane == "silent" { "600" } else { "normal" }),
        ));
        let mut marks: Vec<(f64, usize)> =
            counts.iter().filter(|(l, _, _)| l == lane).map(|(_, c, n)| (*c, *n)).collect();
        marks.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1)));
        let total: usize = marks.iter().map(|(_, n)| n).sum();
        body.push(text(left - 20.0, y + 19.0, &format!("{total} cells"), style().size(9.5).anchor("end").fill(MUTED)));
        for (confidence, count) in marks {
            if confidence <= 0.0 {
                // Off-scale by construction: these cells answer nothing at all, the honest failure. Park them at
                // the axis start rather than stretching the axis to zero.
                let x = left - 2.0;
                body.push(text(
                    x,
                    y + 4.0,
                    &format!("{count} at confidence 0.00"),
                    style().size(10.0).anchor("start").fill(MUTED),
                ));
                continue;
            }
            let x = px(confidence);
            body.push(format!(
                "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" fill=\"{colour}\" opacity=\"0.55\"/>",
                num(x, 1),
                num(y, 1),
                num(6.0 + count as f64 * 0.55, 1)
            ));
            body.push(text(x, y + 3.5, &count.to_string(), style().size(9.0).anchor("middle").fill("#ffffff")));
        }
    }

    body.push(text(
        (left + right) / 2.0,
        top + lane_h * lane_count + 30.0,
        "post-intervention confidence",
        style().size(11.0).anchor("middle"),
    ));
    body.push(text(
        28.0,
        height - 62.0,
        "Marks are sized and labelled by the number of cells sharing that confidence.",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 46.0,
        "The two lanes occupy the same range, 0.91-0.96, and the highest confidence in the figure is reported by cells \
         that lost the capability.",
        style().size(10.0).fill(MUTED),
    ));
    body.push(text(
        28.0,
        height - 30.0,
        "No confidence threshold separates the two lanes, so no monitor reading confidence can detect the loss.",
        style().size(10.0).fill(WARN),
    ));
    body.push(text(
        28.0,
        height - 14.0,
        "The visibly lost cells are the isolated-local baseline: it offers no answer, which is a failure a monitor can \
         see.",
        style().size(10.0).fill(MUTED),
    ));
    Ok(document(width, height, "Figure 5: silent forgetting", body))
}

// ---------------------------------------------------------------------------------------------------------------

/// Every figure from already-loaded artifacts, keyed by filename, in the order the paper uses them.
pub fn render_from(sweep: &Value, benchmark: &Value, scale: &Value) -> CoordinationResult<IndexMap<String, String>> {
    let mut out = IndexMap::new();
    out.insert("fig1_fixture.svg".to_string(), figure_fixture(&fixture_shape(20260813)?));
    out.insert("fig2_survival_matrix.svg".to_string(), figure_survival_matrix(sweep)?);
    out.insert("fig3_pareto_survival_vs_storage.svg".to_string(), figure_pareto(sweep)?);
    out.insert("fig4_scale_invariance.svg".to_string(), figure_scale(scale)?);
    out.insert("fig5_silent_forgetting.svg".to_string(), figure_silent_forgetting(benchmark)?);
    Ok(out)
}

/// `render_all()`: every figure from the artifact JSON in `artifacts`.
pub fn render_all(artifacts: &std::path::Path) -> CoordinationResult<IndexMap<String, String>> {
    let sweep = load(artifacts, "benchmark_sweep30_seed20260813.json")?;
    let benchmark = load(artifacts, "benchmark_seed20260813.json")?;
    let scale = load(artifacts, "scale_seed20260813.json")?;
    render_from(&sweep, &benchmark, &scale)
}
