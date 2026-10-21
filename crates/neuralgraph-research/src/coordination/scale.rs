//! Scale and generalization sweep: does the mechanism hold beyond the 4-node pair? (port of
//! `NeuralGraph/coordination/scale.py` at `13a1729`; `run_scale_sweep(20260813)` is the pinned
//! `scale_seed20260813.json`).
//!
//! The headline benchmark fixes one capability shape — two premises, four nodes. This sweeps the fixture family
//! `CapabilitySpec(slots=K, holders_per_slot=H, independent_roots=I)`: K premises must all be recovered, each is
//! held by H nodes, and those H holders draw from I distinct lineage roots (I=1 is the correlated-replica case).
//!
//! Everything runs through [`benchmark::run_cell`] — the same coordinator, router, repair planner and metrics —
//! rather than a parallel harness that could drift. Routing stays bounded but scales with the capability
//! (`max_nodes = K + 1`), with an interleaved preference so a bounded route covers all K premises; the
//! verification budget is ample on purpose, so survival answers "is independent support findable at all" and
//! `recovery_steps` answers "how efficiently".
//!
//! The prediction under test, stated before the numbers: if lineage independence is what buys survival, survival
//! depends on I and is flat in K and H.

use crate::coordination::adapters::PrivateMemoryRecord;
use crate::coordination::benchmark::{
    generate_symbols, py_div, run_cell, scope_for, structural_diversity, Placement, Symbols, CAPABILITY_ID,
};
use crate::coordination::contracts::{CoordinationError, CoordinationResult};
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::py_round;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

pub const SCALE_VERSION: &str = "scale-generalization-v1";

/// Interventions worth sweeping at scale. `node_failure` is the control (replication genuinely should help);
/// the other two are the discriminating ones, where correlated copies all fail together.
pub const SCALE_INTERVENTIONS: [&str; 3] = ["node_failure", "lineage_root_failure", "authorization_revocation"];

pub const REPAIR_POLICIES: [&str; 2] = ["source_count", "lineage_aware"];

/// One point in the fixture family.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CapabilitySpec {
    pub slots: i64,
    pub holders_per_slot: i64,
    pub independent_roots: i64,
}

impl CapabilitySpec {
    pub fn new(slots: i64, holders_per_slot: i64, independent_roots: i64) -> CoordinationResult<Self> {
        if slots < 2 {
            return Err(CoordinationError::value_error("a reconstruction capability needs at least two premises"));
        }
        if holders_per_slot < 1 {
            return Err(CoordinationError::value_error("each premise needs at least one holder"));
        }
        if !(1 <= independent_roots && independent_roots <= holders_per_slot) {
            return Err(CoordinationError::value_error("independent_roots must be between 1 and holders_per_slot"));
        }
        Ok(CapabilitySpec { slots, holders_per_slot, independent_roots })
    }

    pub fn label(&self) -> String {
        format!("K{}_H{}_I{}", self.slots, self.holders_per_slot, self.independent_roots)
    }

    pub fn slot_names(&self) -> Vec<String> {
        (0..self.slots).map(|index| format!("slot{index}")).collect()
    }

    pub fn node_count(&self) -> i64 {
        self.slots * self.holders_per_slot
    }
}

/// A distinct opaque value per premise, so a synthesizer cannot satisfy premise 3 with premise 1's answer.
fn slot_value(symbols: &Symbols, slot_index: i64) -> String {
    let base = if slot_index % 2 == 0 { &symbols.left } else { &symbols.right };
    format!("{base}_S{slot_index}")
}

fn node_id(slot_index: i64, holder: i64) -> String {
    format!("node-s{slot_index:02}-h{holder:02}")
}

/// The placement for one (spec, repair policy) point. Holder `j` of premise `i` is rooted at `R{i}_{j % I}`: the
/// first I holders get distinct roots and any further holders are correlated copies, so H grows replica count
/// while I alone grows independence.
pub fn build_placement(spec: &CapabilitySpec, seed: i64, repair_policy: &str) -> Placement {
    let symbols = generate_symbols(seed);
    let mut records_by_node: IndexMap<String, Vec<PrivateMemoryRecord>> = IndexMap::new();
    for (slot_index, slot) in spec.slot_names().iter().enumerate() {
        let slot_index = slot_index as i64;
        let value = slot_value(&symbols, slot_index);
        for holder in 0..spec.holders_per_slot {
            let root_index = holder % spec.independent_roots;
            let root = format!("R{slot_index}_{root_index}");
            let domain = format!("FD{slot_index}_{root_index}");
            let node = node_id(slot_index, holder);
            let memory_id = format!("memory-{slot_index}-{holder}");
            records_by_node.insert(
                node.clone(),
                vec![PrivateMemoryRecord::new(
                    &memory_id,
                    CAPABILITY_ID,
                    slot,
                    &value,
                    py_round(0.95 - 0.01 * holder as f64, 6),
                    vec![format!("source-{slot_index}-{root_index}")],
                    vec![format!("parent-{memory_id}")],
                    vec![root.clone()],
                    vec![domain],
                    vec![format!("edge-{node}")],
                    &scope_for(&root),
                )],
            );
        }
    }
    Placement {
        name: format!("{}_{repair_policy}", spec.label()),
        description: format!(
            "{} premises, {} holders each, {} independent root(s) per premise, {repair_policy} repair",
            spec.slots, spec.holders_per_slot, spec.independent_roots
        ),
        records_by_node,
        repair_policy: repair_policy.to_string(),
        // One holder per premise, plus one spare: bounded, and scaling with the capability.
        max_nodes: spec.slots + 1,
        coordinates: true,
        slots: spec.slot_names(),
        // Ample: every holder may be verified (a budget fixed at K while the uncontacted pool grows as K*(H-1)
        // would report the budget choice as a scaling limit of the mechanism).
        max_verifications: spec.node_count(),
    }
}

/// Interleaved preference: the first holder of every premise, then the second, ... so a bounded route can cover
/// all K premises.
pub fn route_preference(spec: &CapabilitySpec) -> Vec<String> {
    (0..spec.holders_per_slot).flat_map(|holder| (0..spec.slots).map(move |slot| node_id(slot, holder))).collect()
}

/// One (spec, policy, intervention) point, through the standard machinery.
pub fn run_point(
    spec: &CapabilitySpec,
    repair_policy: &str,
    intervention: &str,
    seed: i64,
) -> CoordinationResult<Value> {
    let placement = build_placement(spec, seed, repair_policy);
    let name = placement.name.clone();
    let diversity = structural_diversity(&placement);
    let cell = run_cell(seed, &name, intervention, Some(placement), Some(&route_preference(spec)))?;
    Ok(json!({
        "spec": {
            "slots": spec.slots,
            "holders_per_slot": spec.holders_per_slot,
            "independent_roots": spec.independent_roots,
            "label": spec.label(),
            "node_count": spec.node_count(),
        },
        "repair_policy": repair_policy,
        "intervention": intervention,
        "baseline_correct": cell["baseline_correct"],
        "capability_survival": cell["capability_survival"],
        "unrecovered_silent_forgetting": cell["unrecovered_silent_forgetting"],
        "recovery_steps": cell["recovery_steps"],
        "bytes_moved": cell["bytes_moved"],
        "record_count": cell["record_count"],
        "structural_min_cut": diversity["structural_min_cut"],
        "route_size": cell["phases"]["before_intervention"]["route"].as_array().map_or(0, Vec::len),
    }))
}

/// `DEFAULT_SPECS`: K in (2, 3, 5, 8) x H in (2, 3) x I in (1, 2).
pub fn default_specs() -> Vec<CapabilitySpec> {
    let mut specs = Vec::new();
    for k in [2, 3, 5, 8] {
        for h in [2, 3] {
            for i in [1, 2] {
                specs.push(CapabilitySpec { slots: k, holders_per_slot: h, independent_roots: i });
            }
        }
    }
    specs
}

fn int(v: &Value) -> i64 {
    v.as_i64().unwrap_or(0)
}

fn survival_rate(rows: &[&Value]) -> CoordinationResult<f64> {
    Ok(py_round(py_div(rows.iter().filter(|r| r["capability_survival"] == true).count() as f64, rows.len() as f64)?, 6))
}

fn mean_of(rows: &[&Value], key: &str) -> CoordinationResult<f64> {
    Ok(py_round(py_div(rows.iter().map(|r| int(&r[key])).sum::<i64>() as f64, rows.len() as f64)?, 6))
}

pub fn run_scale_sweep(seed: i64, specs: &[CapabilitySpec]) -> CoordinationResult<Value> {
    let mut points: Vec<Value> = Vec::new();
    for spec in specs {
        for policy in REPAIR_POLICIES {
            for intervention in SCALE_INTERVENTIONS {
                points.push(run_point(spec, policy, intervention, seed)?);
            }
        }
    }

    // Does survival depend on independence, and only on independence?
    let mut by_independence = Map::new();
    for policy in REPAIR_POLICIES {
        for intervention in SCALE_INTERVENTIONS {
            for independent in [1, 2] {
                let rows: Vec<&Value> = points
                    .iter()
                    .filter(|p| {
                        p["repair_policy"] == policy
                            && p["intervention"] == intervention
                            && p["spec"]["independent_roots"] == independent
                    })
                    .collect();
                // evaluated in the Python dict's order, so an empty selection raises what Python raised first
                let rate = survival_rate(&rows)?;
                let verdicts: BTreeSet<bool> = rows.iter().map(|r| r["capability_survival"] == true).collect();
                let mean_steps = mean_of(&rows, "recovery_steps")?;
                let max_steps = rows
                    .iter()
                    .map(|r| int(&r["recovery_steps"]))
                    .max()
                    .ok_or_else(|| CoordinationError::value_error("max() arg is an empty sequence"))?;
                by_independence.insert(
                    format!("{policy}::{intervention}::I{independent}"),
                    json!({
                        "points": rows.len(),
                        "survival_rate": rate,
                        "invariant_across_scale": verdicts.len() == 1,
                        "mean_recovery_steps": mean_steps,
                        "max_recovery_steps": max_steps,
                    }),
                );
            }
        }
    }

    // Does raising replica count with correlated lineage buy anything?
    let mut replication_effect = Map::new();
    for policy in REPAIR_POLICIES {
        for holders in [2, 3] {
            let rows: Vec<&Value> = points
                .iter()
                .filter(|p| {
                    p["repair_policy"] == policy
                        && p["spec"]["holders_per_slot"] == holders
                        && p["spec"]["independent_roots"] == 1
                        && p["intervention"] == "lineage_root_failure"
                })
                .collect();
            replication_effect.insert(format!("{policy}::H{holders}::I1"), json!(survival_rate(&rows)?));
        }
    }

    let mut cost_by_arity = Map::new();
    let arities: BTreeSet<i64> = points.iter().map(|p| int(&p["spec"]["slots"])).collect();
    for slots in arities {
        let rows: Vec<&Value> = points.iter().filter(|p| p["spec"]["slots"] == slots).collect();
        cost_by_arity.insert(
            format!("K{slots}"),
            json!({
                "mean_bytes_moved": mean_of(&rows, "bytes_moved")?,
                "mean_record_count": mean_of(&rows, "record_count")?,
                "mean_route_size": mean_of(&rows, "route_size")?,
                "all_baselines_correct": rows.iter().all(|r| r["baseline_correct"] == true),
            }),
        );
    }

    Ok(json!({
        "schema_version": "1.0",
        "scale_version": SCALE_VERSION,
        "seed": seed,
        "interventions": SCALE_INTERVENTIONS,
        "repair_policies": REPAIR_POLICIES,
        "specs": specs.iter().map(CapabilitySpec::label).collect::<Vec<_>>(),
        "survival_by_independence": by_independence,
        "replication_effect_under_correlated_lineage": replication_effect,
        "cost_by_arity": cost_by_arity,
        "points": points,
    }))
}

fn f(v: &Value) -> f64 {
    v.as_f64().unwrap_or(f64::NAN)
}

fn len_of(v: &Value) -> usize {
    v.as_array().map_or(0, Vec::len)
}

/// `render_markdown`: the human-readable sweep summary (`SCALE.md`).
pub fn render_markdown(result: &Value) -> String {
    let mut lines: Vec<String> = Vec::new();
    lines.push(format!("# Scale and generalization sweep (seed {})", result["seed"]));
    lines.push(String::new());
    lines.push(format!(
        "`{}` · {} capability shapes × {} repair policies × {} interventions = {} points",
        result["scale_version"].as_str().unwrap_or_default(),
        len_of(&result["specs"]),
        len_of(&result["repair_policies"]),
        len_of(&result["interventions"]),
        len_of(&result["points"]),
    ));
    lines.push(String::new());
    lines.push("## Survival depends on independence, not on scale".into());
    lines.push(String::new());
    lines.push(
        "| repair policy | intervention | independent roots | survival | invariant across K and H | mean verifications |"
            .into(),
    );
    lines.push("|---|---|---|---|---|---|".into());
    let empty = Map::new();
    let by_independence = result["survival_by_independence"].as_object().unwrap_or(&empty);
    let mut keys: Vec<&String> = by_independence.keys().collect();
    keys.sort();
    for key in keys {
        let parts: Vec<&str> = key.split("::").collect();
        let row = &by_independence[key];
        let flat = if row["invariant_across_scale"] == true { "yes" } else { "**no**" };
        lines.push(format!(
            "| `{}` | `{}` | {} | {:.2} | {flat} | {:.1} |",
            parts[0],
            parts[1],
            parts[2],
            f(&row["survival_rate"]),
            f(&row["mean_recovery_steps"])
        ));
    }
    lines.push(String::new());
    lines.push("## Raising replica count under correlated lineage".into());
    lines.push(String::new());
    lines.push("| policy · holders per premise · 1 root | survival vs lineage-root failure |".into());
    lines.push("|---|---|".into());
    let effect = result["replication_effect_under_correlated_lineage"].as_object().unwrap_or(&empty);
    let mut keys: Vec<&String> = effect.keys().collect();
    keys.sort();
    for key in keys {
        lines.push(format!("| `{key}` | {:.2} |", f(&effect[key])));
    }
    lines.push(String::new());
    lines.push("## Cost grows with capability arity".into());
    lines.push(String::new());
    lines.push("| premises | mean bytes moved | mean records | mean route size | baselines correct |".into());
    lines.push("|---|---|---|---|---|".into());
    let costs = result["cost_by_arity"].as_object().unwrap_or(&empty);
    let mut keys: Vec<&String> = costs.keys().collect();
    keys.sort_by_key(|k| k[1..].parse::<i64>().unwrap_or(0));
    for key in keys {
        let row = &costs[key];
        lines.push(format!(
            "| {} | {:.0} | {:.1} | {:.1} | {} |",
            &key[1..],
            f(&row["mean_bytes_moved"]),
            f(&row["mean_record_count"]),
            f(&row["mean_route_size"]),
            if row["all_baselines_correct"] == true { "yes" } else { "NO" }
        ));
    }
    lines.push(String::new());
    lines.join("\n")
}
