//! Capability-survival benchmark: placement strategies under targeted intervention (port of
//! `NeuralGraph/coordination/benchmark.py` at `13a1729`, the commit that published the pinned artifacts; it never
//! landed on main, but `benchmark_seed20260813.json` and `benchmark_sweep30_seed20260813.json` are its output).
//!
//! The question, under matched conditions: after an intervention removes some part of the system, can the
//! collective still reconstruct the correct answer that no single node holds? Eight placement/repair strategies
//! meet nine interventions; everything is deterministic (seeded symbols, a logical clock, sorted aggregation), so
//! two runs with the same seed produce byte-identical JSON.
//!
//! Design commitments that keep the comparison fair, and their limits (from the Python):
//!
//! * Routing is bounded: a query contacts at most three nodes (`max_nodes=3`, `max_claims=8`,
//!   `max_verifications=2`), identical for every coordinated strategy. Lifting the bound collapses the experiment:
//!   the independently rooted node is then reached by the ordinary query, repair never fires, and every repair
//!   policy scores identically. Only the *repair* ordering varies between strategies.
//! * Because routing is bounded, the minimum failure-domain cut is reported both as observed over one query's
//!   claims and as `structural_min_cut` over the whole placement.
//! * Strategies 4-8 place exactly four records, so their redundancy efficiency is comparable; `isolated_local`
//!   (1), `centralized` (2) and `full_replication` (8) differ by construction.
//! * Every intervention resolves its target from the *pre-failure* execution of the strategy under test.
//! * `recovery_steps` counts verification round trips and `recovery_events` trace events; no wall-clock time.
//!
//! Authorization scopes are per lineage root (`capability:opaque-pair:R2`), so `authorization_revocation` is the
//! policy-plane analogue of `lineage_root_failure`.

use crate::coordination::adapters::{FailureView, MemoryNodeAdapter, MockMemoryNodeAdapter, PrivateMemoryRecord};
use crate::coordination::contracts::{
    str_list, strs, AuthorizationContext, ClaimEnvelope, CoordinationError, CoordinationResult, InterventionTarget,
    LearningDisposition, QueryBudget, QueryRequest, ToJsonable,
};
use crate::coordination::core::{
    clock_fn, product_indices, py_truthy_str, CapabilityRegistry, ClaimNormalizer, Combinations, FailureInjector,
    LineageAnalyzer, LogicalClock, QueryExecution, RepairDecision, RepairPlanner, Router, RuleBasedSynthesizer,
    TesseractCoordinator, TraceLogger,
};
use crate::coordination::fixture::opaque;
use crate::pyrandom::PyRandom;
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{py_repr_str, py_round};
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;

pub const BENCHMARK_VERSION: &str = "capability-survival-v1";
pub const CAPABILITY_ID: &str = "opaque_pair_reconstruction";
pub const REQUIRED_SLOTS: [&str; 2] = ["left", "right"];
pub const DEFAULT_SEED: i64 = 20260813;

/// Authorization scope tied to a lineage root, not to the whole capability.
pub fn scope_for(root: &str) -> String {
    format!("capability:opaque-pair:{root}")
}

// ---------------------------------------------------------------------------------------------------------------
// Knowledge universe
// ---------------------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Symbols {
    pub left: String,
    pub right: String,
    pub distractors: Vec<String>,
    pub query_nonce: String,
}

impl Symbols {
    pub fn expected_answer(&self) -> String {
        format!("{}:{}", self.left, self.right)
    }
}

/// Seeded opaque symbols: opaque so no synthesizer can guess the answer from the query text, and so a leak is
/// detectable by substring search.
pub fn generate_symbols(seed: i64) -> Symbols {
    let mut rng = PyRandom::new(seed as i128);
    let left = opaque(&mut rng, "LX");
    let right = opaque(&mut rng, "RX");
    let distractors = (0..4).map(|_| opaque(&mut rng, "DX")).collect();
    let query_nonce = opaque(&mut rng, "QX");
    Symbols { left, right, distractors, query_nonce }
}

/// One strategy's answer to "where does knowledge live, and how do we repair".
#[derive(Clone, Debug)]
pub struct Placement {
    pub name: String,
    pub description: String,
    pub records_by_node: IndexMap<String, Vec<PrivateMemoryRecord>>,
    pub repair_policy: String,
    pub max_nodes: i64,
    pub coordinates: bool,
    /// The premises this capability requires; a placement owns this so a capability of any arity can be
    /// benchmarked by the same machinery (`scale`). The eight named strategies use the two-slot default.
    pub slots: Vec<String>,
    /// Verification round trips repair may spend (two for the two-slot fixture; `scale` scales it).
    pub max_verifications: i64,
}

impl Placement {
    /// A placement with the two-slot defaults (`slots=REQUIRED_SLOTS`, `max_verifications=2`).
    pub fn new(
        name: &str,
        description: &str,
        records_by_node: IndexMap<String, Vec<PrivateMemoryRecord>>,
        repair_policy: &str,
        max_nodes: i64,
        coordinates: bool,
    ) -> Self {
        Placement {
            name: name.to_string(),
            description: description.to_string(),
            records_by_node,
            repair_policy: repair_policy.to_string(),
            max_nodes,
            coordinates,
            slots: strs(&REQUIRED_SLOTS),
            max_verifications: 2,
        }
    }

    pub fn node_ids(&self) -> Vec<String> {
        let mut ids: Vec<String> = self.records_by_node.keys().cloned().collect();
        ids.sort();
        ids
    }

    pub fn record_count(&self) -> usize {
        self.records_by_node.values().map(Vec::len).sum()
    }

    pub fn all_scopes(&self) -> Vec<String> {
        self.records_by_node
            .values()
            .flatten()
            .map(|r| r.required_scope.clone())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    }
}

/// The correct reconstruction for a placement, in its own slot order: one value per premise (the first holder
/// in node-id order), joined with `:`.
pub fn expected_answer_for(placement: &Placement) -> String {
    let mut by_slot: HashMap<&str, &str> = HashMap::new();
    for node_id in placement.node_ids() {
        for record in &placement.records_by_node[&node_id] {
            by_slot.entry(record.slot.as_str()).or_insert(record.value.as_str());
        }
    }
    placement.slots.iter().filter_map(|slot| by_slot.get(slot.as_str()).copied()).collect::<Vec<_>>().join(":")
}

#[allow(clippy::too_many_arguments)]
fn record(
    node: &str,
    slot: &str,
    value: &str,
    memory: &str,
    source: &str,
    root: &str,
    domain: &str,
    confidence: f64,
) -> PrivateMemoryRecord {
    PrivateMemoryRecord::new(
        memory,
        CAPABILITY_ID,
        slot,
        value,
        confidence,
        vec![source.to_string()],
        vec![format!("parent-{memory}")],
        vec![root.to_string()],
        vec![domain.to_string()],
        vec![format!("edge-{node}-{slot}")],
        &scope_for(root),
    )
}

fn nodes(entries: Vec<(&str, Vec<PrivateMemoryRecord>)>) -> IndexMap<String, Vec<PrivateMemoryRecord>> {
    entries.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

// ---------------------------------------------------------------------------------------------------------------
// The eight placement / repair strategies
// ---------------------------------------------------------------------------------------------------------------

fn isolated_local(sym: &Symbols) -> Placement {
    Placement::new(
        "isolated_local",
        "One node, its own memory only, no coordination. Capability floor.",
        nodes(vec![("node-a", vec![record("a", "left", &sym.left, "memory-a", "source-1", "R1", "FD1", 0.96)])]),
        "none",
        1,
        false,
    )
}

fn centralized(sym: &Symbols) -> Placement {
    Placement::new(
        "centralized",
        "One hub node holds every premise. Succeeds trivially; holds the answer.",
        nodes(vec![(
            "node-hub",
            vec![
                record("hub", "left", &sym.left, "memory-hub-l", "source-1", "R1", "FD1", 0.96),
                record("hub", "right", &sym.right, "memory-hub-r", "source-2", "R2", "FD2", 0.93),
            ],
        )]),
        "none",
        1,
        false,
    )
}

/// Every node holds every premise — but every copy descends from one root: maximum apparent redundancy over
/// minimum lineage diversity.
fn full_replication(sym: &Symbols) -> Placement {
    let mut records = IndexMap::new();
    for node in ["a", "b", "c", "d"] {
        records.insert(
            format!("node-{node}"),
            vec![
                record(node, "left", &sym.left, &format!("memory-{node}-l"), "source-1", "R1", "FD1", 0.96),
                record(node, "right", &sym.right, &format!("memory-{node}-r"), "source-2", "R2", "FD2", 0.93),
            ],
        );
    }
    Placement::new(
        "full_replication",
        "All four nodes hold both premises; all copies share one root per slot.",
        records,
        "none",
        3,
        true,
    )
}

/// A holds left; B, C, D each hold right — all three from the same root R2.
fn correlated_replica_records(sym: &Symbols) -> IndexMap<String, Vec<PrivateMemoryRecord>> {
    nodes(vec![
        ("node-a", vec![record("a", "left", &sym.left, "memory-a", "source-1", "R1", "FD1", 0.96)]),
        ("node-b", vec![record("b", "right", &sym.right, "memory-b", "source-2", "R2", "FD2", 0.93)]),
        ("node-c", vec![record("c", "right", &sym.right, "memory-c", "source-2-copy", "R2", "FD2", 0.91)]),
        ("node-d", vec![record("d", "right", &sym.right, "memory-d", "source-2-copy2", "R2", "FD2", 0.90)]),
    ])
}

fn fixed_distributed_replication(sym: &Symbols) -> Placement {
    Placement::new(
        "fixed_distributed_replication",
        "Three replicas of the right premise across three nodes, one shared root.",
        correlated_replica_records(sym),
        "none",
        3,
        true,
    )
}

fn source_count_repair(sym: &Symbols) -> Placement {
    Placement::new(
        "source_count_repair",
        "Same placement; repair prefers the most-replicated support first.",
        correlated_replica_records(sym),
        "source_count",
        3,
        true,
    )
}

/// A holds left; B and C hold correlated right; D holds an independently rooted right.
fn diverse_records(sym: &Symbols) -> IndexMap<String, Vec<PrivateMemoryRecord>> {
    nodes(vec![
        ("node-a", vec![record("a", "left", &sym.left, "memory-a", "source-1", "R1", "FD1", 0.96)]),
        ("node-b", vec![record("b", "right", &sym.right, "memory-b", "source-2", "R2", "FD2", 0.93)]),
        ("node-c", vec![record("c", "right", &sym.right, "memory-c", "source-2-copy", "R2", "FD2", 0.91)]),
        ("node-d", vec![record("d", "right", &sym.right, "memory-d", "source-3", "R3", "FD3", 0.94)]),
    ])
}

fn random_path_diversification(sym: &Symbols) -> Placement {
    Placement::new(
        "random_path_diversification",
        "Independent support exists; repair picks a candidate at random.",
        diverse_records(sym),
        "random",
        3,
        true,
    )
}

fn lineage_aware_repair(sym: &Symbols) -> Placement {
    Placement::new(
        "lineage_aware_repair",
        "Same placement; repair excludes the failed roots and domains.",
        diverse_records(sym),
        "lineage_aware",
        3,
        true,
    )
}

/// Same four-record budget, placed to maximise the minimum failure-domain cut: both slots get two independently
/// rooted holders in distinct domains.
fn oracle_min_cut(sym: &Symbols) -> Placement {
    Placement::new(
        "oracle_min_cut",
        "Four records placed for maximum min-cut: both slots doubly rooted.",
        nodes(vec![
            ("node-a", vec![record("a", "left", &sym.left, "memory-a", "source-1", "R1", "FD1", 0.96)]),
            ("node-b", vec![record("b", "right", &sym.right, "memory-b", "source-2", "R2", "FD2", 0.93)]),
            ("node-c", vec![record("c", "left", &sym.left, "memory-c", "source-4", "R4", "FD4", 0.92)]),
            ("node-d", vec![record("d", "right", &sym.right, "memory-d", "source-3", "R3", "FD3", 0.94)]),
        ]),
        "lineage_aware",
        3,
        true,
    )
}

/// `STRATEGY_BUILDERS`, in order.
/// A strategy: its placement for a set of symbols.
pub type StrategyBuilder = fn(&Symbols) -> Placement;

pub const STRATEGY_BUILDERS: [(&str, StrategyBuilder); 8] = [
    ("isolated_local", isolated_local),
    ("centralized", centralized),
    ("full_replication", full_replication),
    ("fixed_distributed_replication", fixed_distributed_replication),
    ("source_count_repair", source_count_repair),
    ("random_path_diversification", random_path_diversification),
    ("lineage_aware_repair", lineage_aware_repair),
    ("oracle_min_cut", oracle_min_cut),
];

pub const STRATEGIES: [&str; 8] = [
    "isolated_local",
    "centralized",
    "full_replication",
    "fixed_distributed_replication",
    "source_count_repair",
    "random_path_diversification",
    "lineage_aware_repair",
    "oracle_min_cut",
];

/// `STRATEGY_BUILDERS[strategy](symbols)` (`KeyError` for an unknown name).
pub fn build_strategy(strategy: &str, symbols: &Symbols) -> CoordinationResult<Placement> {
    STRATEGY_BUILDERS
        .iter()
        .find(|(name, _)| *name == strategy)
        .map(|(_, build)| build(symbols))
        .ok_or_else(|| CoordinationError::key_error(strategy))
}

// ---------------------------------------------------------------------------------------------------------------
// Runtime assembly
// ---------------------------------------------------------------------------------------------------------------

pub struct Runtime {
    pub placement: Placement,
    pub symbols: Symbols,
    pub query: QueryRequest,
    pub clock: Rc<LogicalClock>,
    pub traces: Rc<TraceLogger>,
    pub failure: Rc<FailureInjector>,
    pub registry: Rc<CapabilityRegistry>,
    pub coordinator: TesseractCoordinator,
    pub planner: RepairPlanner,
}

/// Assemble a runtime for a named strategy, or for a caller-supplied placement (`scale` passes its own so every
/// arity runs through exactly this path).
pub fn build_runtime(seed: i64, strategy: &str, placement: Option<Placement>) -> CoordinationResult<Runtime> {
    let symbols = generate_symbols(seed);
    let placement = match placement {
        Some(p) => p,
        None => build_strategy(strategy, &symbols)?,
    };
    let run_id = format!("bench_{seed}_{strategy}");
    let clock = Rc::new(LogicalClock::new(seed));
    let issued_at = LogicalClock::new(seed).now();
    let node_ids = placement.node_ids();
    let query = QueryRequest {
        query_id: format!("query_{seed}_{strategy}"),
        content: format!("Reconstruct the two-part capability identified by {}", symbols.query_nonce),
        requested_capability: Some(CAPABILITY_ID.to_string()),
        requester_id: "benchmark-controller".to_string(),
        issued_at: issued_at.clone(),
        valid_at: Some(issued_at),
        authorization: AuthorizationContext::new(placement.all_scopes(), node_ids.clone(), false)?,
        // One claim per contacted node is the floor; the two-slot strategies keep their original 8 so their
        // pinned artifacts do not move.
        budget: QueryBudget::new(placement.max_nodes, 8.max(2 * node_ids.len() as i64), placement.max_verifications)?,
    }
    .validated()?;
    let traces = Rc::new(TraceLogger::new(run_id.clone(), clock.clone()));
    let failure = Rc::new(FailureInjector::new(run_id, seed, clock.clone(), traces.clone()));
    let registry = Rc::new(CapabilityRegistry::new());
    for node_id in &node_ids {
        let failure_view: Rc<dyn FailureView> = failure.clone();
        let adapter: Rc<dyn MemoryNodeAdapter> = Rc::new(MockMemoryNodeAdapter::new(
            node_id,
            placement.records_by_node[node_id].clone(),
            failure_view,
            clock_fn(&clock),
            LearningDisposition::Tentative,
        ));
        registry.register(adapter)?;
    }
    let coordinator = TesseractCoordinator::new(
        registry.clone(),
        Router::new(registry.clone(), failure.clone()),
        ClaimNormalizer,
        RuleBasedSynthesizer::new(placement.slots.clone()),
        LineageAnalyzer,
        traces.clone(),
    );
    let planner = RepairPlanner::new(registry.clone(), traces.clone());
    Ok(Runtime { placement, symbols, query, clock, traces, failure, registry, coordinator, planner })
}

// ---------------------------------------------------------------------------------------------------------------
// Interventions
// ---------------------------------------------------------------------------------------------------------------

/// A resolved intervention: which injector targets to apply, and why.
#[derive(Clone, Debug, PartialEq)]
pub struct InterventionPlan {
    pub name: String,
    pub description: String,
    pub applications: Vec<(InterventionTarget, String)>,
    pub note: String,
}

impl InterventionPlan {
    fn new(name: &str, description: &str, applications: Vec<(InterventionTarget, String)>, note: &str) -> Self {
        InterventionPlan {
            name: name.to_string(),
            description: description.to_string(),
            applications,
            note: note.to_string(),
        }
    }
}

/// The claim the synthesizer actually chose for the last required slot (else its first selected claim).
/// Interventions target this so each strategy is attacked where it is actually load bearing.
pub fn load_bearing_claim<'a>(execution: &'a QueryExecution, slots: &[String]) -> Option<&'a ClaimEnvelope> {
    if execution.synthesis.selected_claim_ids.is_empty() {
        return None;
    }
    let target_slot = slots.last().expect("a placement has at least one slot");
    let selected = |c: &&ClaimEnvelope| execution.synthesis.selected_claim_ids.contains(&c.claim_id);
    execution
        .claims
        .iter()
        .find(|c| selected(c) && c.slot_is(target_slot))
        .or_else(|| execution.claims.iter().find(|c| selected(c)))
}

/// The record behind an exported claim, matched by (producer node, slot) — benchmark-side knowledge, never
/// adapter-side: the claim contract deliberately does not carry the memory id.
fn record_for<'a>(runtime: &'a Runtime, claim: Option<&ClaimEnvelope>) -> Option<&'a PrivateMemoryRecord> {
    let claim = claim?;
    runtime
        .placement
        .records_by_node
        .get(&claim.producer_node_id)?
        .iter()
        .find(|r| claim.content_get("slot") == Some(&Value::String(r.slot.clone())))
}

pub fn memory_id_for(runtime: &Runtime, claim: Option<&ClaimEnvelope>) -> Option<String> {
    record_for(runtime, claim).map(|r| r.memory_id.clone())
}

pub fn edge_path_for(runtime: &Runtime, claim: Option<&ClaimEnvelope>) -> Option<String> {
    record_for(runtime, claim).and_then(|r| r.edge_path.first().cloned())
}

fn py_list_repr(items: &[String]) -> String {
    format!("[{}]", items.iter().map(|s| py_repr_str(s)).collect::<Vec<_>>().join(", "))
}

/// `x or default` for an optional identifier.
fn or_default(value: Option<String>, default: &str) -> String {
    value.filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

/// Resolve an intervention against what this strategy actually relied on.
pub fn plan_intervention(
    name: &str,
    runtime: &Runtime,
    baseline: &QueryExecution,
) -> CoordinationResult<InterventionPlan> {
    let claim = load_bearing_claim(baseline, &runtime.placement.slots);
    let node_id = match claim {
        Some(c) => c.producer_node_id.clone(),
        None => runtime.placement.node_ids()[0].clone(),
    };
    let memory_id = memory_id_for(runtime, claim);
    let edge_id = edge_path_for(runtime, claim);
    let root_id = claim.and_then(|c| c.lineage_root_ids.first().cloned()).unwrap_or_else(|| "R2".to_string());

    Ok(match name {
        "node_failure" => InterventionPlan::new(
            name,
            "The node serving the load-bearing premise goes offline.",
            vec![(InterventionTarget::Node, node_id)],
            "",
        ),
        "memory_deletion" => InterventionPlan::new(
            name,
            "The specific memory backing the load-bearing premise is gone.",
            vec![(InterventionTarget::Memory, or_default(memory_id, "memory-b"))],
            "",
        ),
        "lineage_root_failure" => InterventionPlan::new(
            name,
            "The derivation root of the load-bearing premise is invalidated.",
            vec![(InterventionTarget::LineageRoot, root_id)],
            "Removes every copy descending from this root, however many nodes hold one.",
        ),
        "authorization_revocation" => InterventionPlan::new(
            name,
            "The authorization scope covering that root is revoked.",
            vec![(InterventionTarget::AuthorizationScope, scope_for(&root_id))],
            "Policy-plane analogue of lineage_root_failure; scopes are per-root.",
        ),
        "route_removal" => InterventionPlan::new(
            name,
            "The route to the load-bearing node is withdrawn.",
            vec![(InterventionTarget::Route, node_id)],
            "",
        ),
        "edge_corruption" => InterventionPlan::new(
            name,
            "The graph edge carrying that derivation is corrupted.",
            vec![(InterventionTarget::Edge, or_default(edge_id, "edge-b-right"))],
            "",
        ),
        "stale_knowledge" => InterventionPlan::new(
            name,
            "The load-bearing memory is present but no longer valid at query time.",
            vec![(InterventionTarget::Evidence, or_default(memory_id, "memory-b"))],
            "",
        ),
        "worst_single_domain_failure" => {
            // The adversarial intervention, and the one that tests the min-cut metric rather than restating it.
            // Failing a whole cut would be tautological (every strategy scores zero), so this fails exactly ONE
            // domain, the one in the most reconstruction coalitions: min-cut 1 cannot survive it, min-cut 2 must.
            let worst = worst_single_domain(&runtime.placement);
            let targets = roots_in_domains(&runtime.placement, &worst);
            let diversity = structural_diversity(&runtime.placement);
            let note = format!(
                "domain={}, roots={}, structural min-cut={}: a min-cut of 1 predicts loss here, a min-cut of 2 \
                 predicts survival.",
                py_list_repr(&worst),
                py_list_repr(&targets),
                diversity["structural_min_cut"]
            );
            InterventionPlan::new(
                name,
                "The single failure domain carrying the most reconstruction coalitions fails.",
                targets.into_iter().map(|root| (InterventionTarget::LineageRoot, root)).collect(),
                &note,
            )
        }
        "network_partition" => {
            let node_ids = runtime.placement.node_ids();
            let reachable: Vec<String> = node_ids.iter().take(1).cloned().collect();
            let cut = node_ids
                .iter()
                .filter(|other| !reachable.contains(other))
                .map(|other| (InterventionTarget::Route, other.clone()))
                .collect();
            InterventionPlan::new(
                name,
                "The coordinator is isolated with a minority of nodes.",
                cut,
                "Routes to every node beyond the first are withdrawn simultaneously.",
            )
        }
        other => return Err(CoordinationError::value_error(format!("unknown intervention: {other}"))),
    })
}

pub const INTERVENTIONS: [&str; 9] = [
    "node_failure",
    "memory_deletion",
    "lineage_root_failure",
    "authorization_revocation",
    "route_removal",
    "edge_corruption",
    "stale_knowledge",
    "network_partition",
    "worst_single_domain_failure",
];

// ---------------------------------------------------------------------------------------------------------------
// Repair policies
// ---------------------------------------------------------------------------------------------------------------

/// Per node: (lineage roots, failure domains) it was seen to export.
pub type ObservedLineage = IndexMap<String, (BTreeSet<String>, BTreeSet<String>)>;

/// What the coordinator legitimately knows about each node's provenance: derived only from exported claims of
/// the pre-intervention query, never from `Placement` internals (a policy that peeked would be an oracle).
pub fn observed_lineage(baseline: &QueryExecution) -> ObservedLineage {
    let mut seen: ObservedLineage = IndexMap::new();
    for claim in &baseline.claims {
        let (roots, domains) = seen.entry(claim.producer_node_id.clone()).or_default();
        roots.extend(claim.lineage_root_ids.iter().cloned());
        domains.extend(claim.failure_domains.iter().cloned());
    }
    seen
}

/// Which premises each contacted node was seen to produce (from exported claims only). A node the bounded route
/// never contacted does not appear: "not observed" differs from "observed to hold nothing relevant".
pub fn observed_slots(baseline: &QueryExecution) -> IndexMap<String, BTreeSet<String>> {
    let mut seen: IndexMap<String, BTreeSet<String>> = IndexMap::new();
    for claim in &baseline.claims {
        match claim.content_get("slot") {
            None | Some(Value::Null) => {}
            Some(slot) => {
                seen.entry(claim.producer_node_id.clone())
                    .or_default()
                    .insert(crate::coordination::core::py_str_value(slot));
            }
        }
    }
    seen
}

/// 0 observed to hold a missing premise, 1 never contacted (unknown, worth a verification), 2 observed to hold
/// none of them. A tiebreaker applied within every policy's own principle, never ahead of it.
fn slot_relevance(node: &str, missing: &BTreeSet<String>, slots: &IndexMap<String, BTreeSet<String>>) -> u8 {
    match slots.get(node) {
        None => 1,
        Some(held) if held.iter().any(|s| missing.contains(s)) => 0,
        Some(_) => 2,
    }
}

/// Order candidate nodes for verification. The ordering *is* the repair policy.
#[allow(clippy::too_many_arguments)]
pub fn order_repair_candidates(
    policy: &str,
    candidates: &[String],
    lineage: &ObservedLineage,
    excluded_roots: &[String],
    excluded_domains: &[String],
    seed: i64,
    missing_slots: &[String],
    slots: Option<&IndexMap<String, BTreeSet<String>>>,
) -> Vec<String> {
    let missing: BTreeSet<String> = missing_slots.iter().cloned().collect();
    let empty_slots = IndexMap::new();
    let known = slots.unwrap_or(&empty_slots);
    let relevance: HashMap<&str, u8> =
        candidates.iter().map(|n| (n.as_str(), slot_relevance(n, &missing, known))).collect();
    let mut ordered: Vec<String> = candidates.to_vec();
    ordered.sort();
    let empty = (BTreeSet::new(), BTreeSet::new());
    let lineage_of = |node: &str| lineage.get(node).unwrap_or(&empty);
    match policy {
        "source_count" => {
            // "More copies means safer": rank by how many other nodes share a root with this one — the intuition
            // the benchmark argues against; it spends a bounded budget on correlated nodes first.
            let replicas = |node: &str| -> usize {
                let roots = &lineage_of(node).0;
                ordered.iter().filter(|other| *other != node && !lineage_of(other).0.is_disjoint(roots)).count()
            };
            let mut keyed: Vec<(std::cmp::Reverse<usize>, u8, String)> =
                ordered.iter().map(|n| (std::cmp::Reverse(replicas(n)), relevance[n.as_str()], n.clone())).collect();
            keyed.sort();
            keyed.into_iter().map(|(_, _, n)| n).collect()
        }
        "random" => {
            PyRandom::new(seed as i128).shuffle(&mut ordered);
            ordered
        }
        "lineage_aware" => {
            // Rank by independence from the support already known to be compromised: with a bounded budget a
            // lineage-blind ordering exhausts itself on correlated nodes before reaching an independent one.
            let bad_roots: BTreeSet<String> = excluded_roots.iter().cloned().collect();
            let bad_domains: BTreeSet<String> = excluded_domains.iter().cloned().collect();
            let mut keyed: Vec<(u8, u8, u8, String)> = ordered
                .iter()
                .map(|n| {
                    let (roots, domains) = lineage_of(n);
                    (
                        u8::from(!roots.is_disjoint(&bad_roots)),
                        u8::from(!domains.is_disjoint(&bad_domains)),
                        relevance[n.as_str()],
                        n.clone(),
                    )
                })
                .collect();
            keyed.sort();
            keyed.into_iter().map(|(_, _, _, n)| n).collect()
        }
        _ => ordered,
    }
}

// ---------------------------------------------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------------------------------------------

/// Does any single node hold the whole answer, and did anything leak?
fn privacy_audit(runtime: &Runtime, executions: &[&QueryExecution]) -> Value {
    let sym = &runtime.symbols;
    let required: BTreeSet<&String> = runtime.placement.slots.iter().collect();
    let single_node_holds_answer = runtime
        .placement
        .records_by_node
        .values()
        .any(|records| records.iter().map(|r| &r.slot).collect::<BTreeSet<_>>().is_superset(&required));
    let expected = sym.expected_answer();
    let answer_in_query =
        [&sym.left, &sym.right, &expected].iter().any(|symbol| runtime.query.content.contains(symbol.as_str()));
    let complete_answer_in_a_memory = runtime.placement.records_by_node.values().flatten().any(|r| r.value == expected);
    let payload_bearing_denials = executions
        .iter()
        .flat_map(|e| e.exports.iter())
        .filter(|export| {
            export.trace.policy_status.value() != "allowed"
                && (!export.claims.is_empty()
                    || !export.trace.memory_ids.is_empty()
                    || !export.trace.lineage_root_ids.is_empty())
        })
        .count();
    let policy_violations: usize = executions.iter().map(|e| e.policy_violations).sum();
    json!({
        "single_node_holds_full_answer": single_node_holds_answer,
        "answer_present_in_query": answer_in_query,
        "complete_answer_in_a_single_memory": complete_answer_in_a_memory,
        "payload_bearing_denials": payload_bearing_denials,
        "policy_violations": policy_violations,
        "leak_free": !answer_in_query && !complete_answer_in_a_memory && payload_bearing_denials == 0
            && policy_violations == 0,
    })
}

/// Holders of each required slot, in placement order (records whose slot is not required are ignored).
fn holders_by_slot(placement: &Placement) -> Vec<Vec<&PrivateMemoryRecord>> {
    let mut by_slot: IndexMap<&str, Vec<&PrivateMemoryRecord>> =
        placement.slots.iter().map(|s| (s.as_str(), Vec::new())).collect();
    for record in placement.records_by_node.values().flatten() {
        if let Some(holders) = by_slot.get_mut(record.slot.as_str()) {
            holders.push(record);
        }
    }
    // product(*(sorted(by_slot[slot], key=memory_id) for slot in placement.slots))
    placement
        .slots
        .iter()
        .map(|slot| {
            let mut holders = by_slot[slot.as_str()].clone();
            holders.sort_by(|a, b| a.memory_id.cmp(&b.memory_id));
            holders
        })
        .collect()
}

/// The sorted failure-domain set of every reconstruction coalition (one holder per slot), in product order.
fn coalition_domains(holders: &[Vec<&PrivateMemoryRecord>]) -> Vec<Vec<String>> {
    let sizes: Vec<usize> = holders.iter().map(Vec::len).collect();
    product_indices(&sizes)
        .into_iter()
        .map(|pick| {
            pick.iter()
                .enumerate()
                .flat_map(|(slot, i)| holders[slot][*i].failure_domains.iter().cloned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<String>>()
        })
        .filter(|domains| !domains.is_empty())
        .collect()
}

fn structural_root_count(placement: &Placement) -> usize {
    placement.records_by_node.values().flatten().flat_map(|r| r.lineage_root_ids.iter()).collect::<BTreeSet<_>>().len()
}

/// Lineage diversity of the placement itself, independent of any routing bound (a fixture-scale enumeration of
/// one holder per slot, not a general graph algorithm).
pub fn structural_diversity(placement: &Placement) -> Value {
    let holders = holders_by_slot(placement);
    if holders.iter().any(Vec::is_empty) {
        return json!({
            "structural_min_cut": 0,
            "structural_cut_domains": [],
            "structural_coalition_count": 0,
            "structural_root_count": structural_root_count(placement),
            "reconstructible": false,
        });
    }
    let coalitions: BTreeSet<Vec<String>> = coalition_domains(&holders).into_iter().collect();
    let all_domains: Vec<String> = coalitions.iter().flatten().cloned().collect::<BTreeSet<_>>().into_iter().collect();
    let mut min_cut = 0;
    let mut cut_domains: Vec<String> = Vec::new();
    'sizes: for size in 1..=all_domains.len() {
        for candidate in Combinations::new(all_domains.len(), size) {
            let chosen: Vec<&String> = candidate.iter().map(|i| &all_domains[*i]).collect();
            if coalitions.iter().all(|coalition| chosen.iter().any(|d| coalition.contains(d))) {
                min_cut = size;
                cut_domains = chosen.into_iter().cloned().collect();
                cut_domains.sort();
                break 'sizes;
            }
        }
    }
    json!({
        "structural_min_cut": min_cut,
        "structural_cut_domains": cut_domains,
        "structural_coalition_count": coalitions.len(),
        "structural_root_count": structural_root_count(placement),
        "reconstructible": true,
    })
}

/// The one failure domain appearing in the most reconstruction coalitions (ties break on domain id); empty when
/// the placement cannot reconstruct at all.
pub fn worst_single_domain(placement: &Placement) -> Vec<String> {
    let holders = holders_by_slot(placement);
    if holders.iter().any(Vec::is_empty) {
        return Vec::new();
    }
    let coalitions = coalition_domains(&holders);
    if coalitions.is_empty() {
        return Vec::new();
    }
    let all_domains: BTreeSet<&String> = coalitions.iter().flatten().collect();
    let mut scored: Vec<(std::cmp::Reverse<usize>, &String)> = all_domains
        .into_iter()
        .map(|domain| (std::cmp::Reverse(coalitions.iter().filter(|c| c.contains(domain)).count()), domain))
        .collect();
    scored.sort();
    vec![scored[0].1.clone()]
}

/// Every lineage root that lives in one of the given failure domains.
pub fn roots_in_domains(placement: &Placement, domains: &[String]) -> Vec<String> {
    placement
        .records_by_node
        .values()
        .flatten()
        .filter(|r| r.failure_domains.iter().any(|d| domains.contains(d)))
        .flat_map(|r| r.lineage_root_ids.iter().cloned())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn independent_support(execution: &QueryExecution) -> Value {
    let m = &execution.metrics;
    json!({
        "apparent_replica_count": m.apparent_replica_count,
        "unique_lineage_root_count": m.unique_lineage_root_count,
        "unique_failure_domain_count": m.unique_failure_domain_count,
        "reconstruction_coalition_count": m.reconstruction_coalition_count,
        "minimal_failure_domain_cut": m.minimal_failure_domain_cut,
    })
}

// ---------------------------------------------------------------------------------------------------------------
// One cell of the matrix
// ---------------------------------------------------------------------------------------------------------------

fn correct(execution: &QueryExecution, expected: &str) -> bool {
    execution.synthesis.success && execution.synthesis.answer.as_deref() == Some(expected)
}

/// Run one (strategy, intervention) pair through before / after / repair.
pub fn run_cell(
    seed: i64,
    strategy: &str,
    intervention: &str,
    placement: Option<Placement>,
    preferred_node_ids: Option<&[String]>,
) -> CoordinationResult<Value> {
    let runtime = build_runtime(seed, strategy, placement)?;
    let expected = expected_answer_for(&runtime.placement);

    // `preferred_node_ids or placement.node_ids`: an empty preference falls back too
    let route_preference: Vec<String> = match preferred_node_ids {
        Some(p) if !p.is_empty() => p.to_vec(),
        _ => runtime.placement.node_ids(),
    };
    let before = runtime.coordinator.execute(&runtime.query, "before_intervention", Some(&route_preference))?;
    let before_correct = correct(&before, &expected);

    let plan = plan_intervention(intervention, &runtime, &before)?;
    for (target_type, target_id) in &plan.applications {
        runtime.failure.apply(
            *target_type,
            target_id,
            &format!("benchmark intervention: {}", plan.name),
            Some(&plan.description),
        )?;
    }

    let events_before_repair = runtime.traces.len();
    let after = runtime.coordinator.execute(&runtime.query, "after_intervention", Some(&route_preference))?;
    let after_correct = correct(&after, &expected);

    // Silent forgetting: the system lost a capability it demonstrably had, and still reports confidence above
    // the fraction of slots it genuinely covers. A system that never had the capability has forgotten nothing.
    let silent_forgetting = before_correct && !after_correct && after.metrics.confidence_valid_support_gap > 0.0;

    let mut repair: Option<RepairDecision> = None;
    let mut repaired: Option<QueryExecution> = None;
    let mut repaired_correct = after_correct;
    let mut recovery_steps = 0;

    if !after_correct && runtime.placement.repair_policy != "none" {
        let missing: Vec<String> =
            runtime.placement.slots.iter().filter(|s| !after.synthesis.covered_slots.contains(s)).cloned().collect();
        let failed_claim = load_bearing_claim(&before, &runtime.placement.slots);
        let (mut excluded_roots, mut excluded_domains) = match failed_claim {
            Some(c) => (c.lineage_root_ids.clone(), c.failure_domains.clone()),
            None => (Vec::new(), Vec::new()),
        };
        if runtime.placement.repair_policy == "source_count" {
            // Lineage-blind by construction: it never tells the verifier which support is compromised.
            excluded_roots = Vec::new();
            excluded_domains = Vec::new();
        }
        let candidates: Vec<String> = runtime
            .placement
            .node_ids()
            .into_iter()
            .filter(|n| !after.synthesis.selected_node_ids.contains(n))
            .collect();
        let candidates = order_repair_candidates(
            &runtime.placement.repair_policy,
            &candidates,
            &observed_lineage(&before),
            &excluded_roots,
            &excluded_domains,
            seed,
            &missing,
            Some(&observed_slots(&before)),
        );
        let decision =
            runtime.planner.plan(&runtime.query, &missing, &candidates, &excluded_roots, &excluded_domains)?;
        recovery_steps = decision.steps;
        if py_truthy_str(&decision.selected_node_id) {
            let selected = decision.selected_node_id.clone().unwrap_or_default();
            let mut preferred = vec![selected.clone()];
            preferred.extend(route_preference.iter().filter(|n| **n != selected).cloned());
            let execution = runtime.coordinator.execute(&runtime.query, "after_repair", Some(&preferred))?;
            repaired_correct = correct(&execution, &expected);
            repaired = Some(execution);
        }
        repair = Some(decision);
    }

    let mut executions: Vec<&QueryExecution> = vec![&before, &after];
    if let Some(r) = &repaired {
        executions.push(r);
    }
    let recovery_events = runtime.traces.len() - events_before_repair;
    let bytes_moved: usize = executions.iter().map(|e| e.exported_bytes).sum();
    let survived = repaired_correct;
    let record_count = runtime.placement.record_count();

    let mut phases = Map::new();
    for execution in &executions {
        phases.insert(
            execution.phase.clone(),
            json!({
                "route": str_list(&execution.route),
                "success": execution.synthesis.success,
                "answer_correct": execution.synthesis.answer.as_deref() == Some(expected.as_str()),
                "confidence": execution.synthesis.confidence,
                "covered_slots": str_list(&execution.synthesis.covered_slots),
                "exported_bytes": execution.exported_bytes,
                "claim_count": execution.claims.len(),
            }),
        );
    }
    let applications: Vec<Value> = plan
        .applications
        .iter()
        .map(|(target, target_id)| json!({"target_type": target.value(), "target_id": target_id}))
        .collect();

    Ok(json!({
        "strategy": strategy,
        "intervention": intervention,
        "intervention_plan": {
            "description": plan.description,
            "note": plan.note,
            "applications": applications,
        },
        "capability_survival": survived,
        "baseline_correct": before_correct,
        "immediate_correct": after_correct,
        "repaired_correct": repaired_correct,
        "silent_forgetting": silent_forgetting,
        // Transient silent forgetting is repaired; unrecovered silent forgetting leaves the system permanently
        // wrong while still reporting confidence it has not earned.
        "unrecovered_silent_forgetting": silent_forgetting && !survived,
        "transient_silent_forgetting": silent_forgetting && survived,
        "confidence_valid_support_gap": after.metrics.confidence_valid_support_gap,
        "post_intervention_confidence": after.metrics.current_confidence,
        "post_intervention_coverage": after.metrics.valid_support_coverage,
        "recovery_steps": recovery_steps,
        "recovery_events": recovery_events,
        "repair_reason": repair.as_ref().map_or("no repair policy".to_string(), |r| r.reason.clone()),
        "repair_selected_node": repair.as_ref().and_then(|r| r.selected_node_id.clone()),
        "bytes_moved": bytes_moved,
        "record_count": record_count,
        "node_count": runtime.placement.node_ids().len(),
        "redundancy_efficiency": py_round((if survived { 1.0 } else { 0.0 }) / record_count as f64, 6),
        "structural_diversity": structural_diversity(&runtime.placement),
        "independent_support_before": independent_support(&before),
        "independent_support_after": independent_support(&after),
        "privacy": privacy_audit(&runtime, &executions),
        "phases": phases,
        "interventions_applied": runtime.failure.records().to_jsonable(),
    }))
}

// ---------------------------------------------------------------------------------------------------------------
// Full matrix
// ---------------------------------------------------------------------------------------------------------------

fn as_f64(v: &Value) -> f64 {
    v.as_f64().unwrap_or(f64::NAN)
}

fn as_i64(v: &Value) -> i64 {
    v.as_i64().unwrap_or(0)
}

fn sorted_strings(mut items: Vec<String>) -> Vec<String> {
    items.sort();
    items
}

pub fn run_benchmark(seed: i64) -> CoordinationResult<Value> {
    let mut cells = Map::new();
    for strategy in STRATEGIES {
        for intervention in INTERVENTIONS {
            cells.insert(format!("{strategy}::{intervention}"), run_cell(seed, strategy, intervention, None, None)?);
        }
    }
    let n = INTERVENTIONS.len() as f64;

    let mut per_strategy = Map::new();
    for strategy in STRATEGIES {
        let rows: Vec<&Value> = INTERVENTIONS.iter().map(|i| &cells[&format!("{strategy}::{i}")]).collect();
        let survived = rows.iter().filter(|r| r["capability_survival"] == true).count();
        let silent = rows.iter().filter(|r| r["silent_forgetting"] == true).count();
        let leak_free = rows.iter().all(|r| r["privacy"]["leak_free"] == true);
        let holds_answer = rows[0]["privacy"]["single_node_holds_full_answer"] == true;
        let steps: i64 = rows.iter().map(|r| as_i64(&r["recovery_steps"])).sum();
        let bytes: i64 = rows.iter().map(|r| as_i64(&r["bytes_moved"])).sum();
        let record_count = as_i64(&rows[0]["record_count"]);
        per_strategy.insert(
            strategy.to_string(),
            json!({
                "description": build_strategy(strategy, &generate_symbols(seed))?.description,
                "capability_survival_rate": py_round(survived as f64 / n, 6),
                "survived_interventions": sorted_strings(rows.iter().filter(|r| r["capability_survival"] == true)
                    .map(|r| r["intervention"].as_str().unwrap_or_default().to_string()).collect()),
                "lost_interventions": sorted_strings(rows.iter().filter(|r| r["capability_survival"] != true)
                    .map(|r| r["intervention"].as_str().unwrap_or_default().to_string()).collect()),
                "silent_forgetting_count": silent,
                "silent_forgetting_rate": py_round(silent as f64 / n, 6),
                "mean_recovery_steps": py_round(steps as f64 / rows.len() as f64, 6),
                "total_bytes_moved": bytes,
                "mean_bytes_moved": py_round(bytes as f64 / rows.len() as f64, 6),
                "record_count": rows[0]["record_count"],
                "node_count": rows[0]["node_count"],
                "observed_min_failure_domain_cut": rows[0]["independent_support_before"]["minimal_failure_domain_cut"],
                "structural_min_cut": rows[0]["structural_diversity"]["structural_min_cut"],
                "structural_root_count": rows[0]["structural_diversity"]["structural_root_count"],
                "unique_lineage_roots": rows[0]["independent_support_before"]["unique_lineage_root_count"],
                "redundancy_efficiency": py_round(survived as f64 / n / record_count as f64, 6),
                "privacy_preserved": leak_free && !holds_answer,
                "single_node_holds_full_answer": holds_answer,
                "leak_free": leak_free,
            }),
        );
    }

    let mut per_intervention = Map::new();
    for intervention in INTERVENTIONS {
        let rows: Vec<&Value> = STRATEGIES.iter().map(|s| &cells[&format!("{s}::{intervention}")]).collect();
        let surviving: Vec<String> = rows
            .iter()
            .filter(|r| r["capability_survival"] == true)
            .map(|r| r["strategy"].as_str().unwrap_or_default().to_string())
            .collect();
        per_intervention.insert(
            intervention.to_string(),
            json!({
                "description": rows[0]["intervention_plan"]["description"],
                "strategies_surviving": sorted_strings(surviving.clone()),
                "survival_rate": py_round(surviving.len() as f64 / STRATEGIES.len() as f64, 6),
                "silent_forgetting_count": rows.iter().filter(|r| r["silent_forgetting"] == true).count(),
            }),
        );
    }

    let mut matrix = Map::new();
    for strategy in STRATEGIES {
        let mut row = Map::new();
        for intervention in INTERVENTIONS {
            row.insert(
                intervention.to_string(),
                cells[&format!("{strategy}::{intervention}")]["capability_survival"].clone(),
            );
        }
        matrix.insert(strategy.to_string(), Value::Object(row));
    }

    Ok(json!({
        "schema_version": "1.0",
        "benchmark_version": BENCHMARK_VERSION,
        "seed": seed,
        "strategies": STRATEGIES,
        "interventions": INTERVENTIONS,
        "matrix": matrix,
        "per_strategy": per_strategy,
        "per_intervention": per_intervention,
        "cells": cells,
    }))
}

/// `a / b` for Python numbers, raising `ZeroDivisionError` like Python.
pub(crate) fn py_div(a: f64, b: f64) -> CoordinationResult<f64> {
    if b == 0.0 {
        return Err(CoordinationError::new("ZeroDivisionError", "division by zero"));
    }
    Ok(a / b)
}

/// Aggregate capability survival over `trials` consecutive seeds. A single seed cannot characterise the
/// stochastic `random_path_diversification`; deterministic strategies return identical results every trial,
/// which is itself the check that the sweep adds no noise of its own.
pub fn run_sweep(seed: i64, trials: i64) -> CoordinationResult<Value> {
    let seeds: Vec<i64> = (seed..seed.saturating_add(trials.max(0))).collect();
    let mut per_cell = Map::new();
    for strategy in STRATEGIES {
        for intervention in INTERVENTIONS {
            let mut survivals: Vec<bool> = Vec::new();
            let mut unrecovered: Vec<bool> = Vec::new();
            let mut steps: Vec<i64> = Vec::new();
            let mut byte_costs: Vec<i64> = Vec::new();
            for trial_seed in &seeds {
                let cell = run_cell(*trial_seed, strategy, intervention, None, None)?;
                survivals.push(cell["capability_survival"] == true);
                unrecovered.push(cell["unrecovered_silent_forgetting"] == true);
                steps.push(as_i64(&cell["recovery_steps"]));
                byte_costs.push(as_i64(&cell["bytes_moved"]));
            }
            let count = survivals.len();
            let survival_count = survivals.iter().filter(|s| **s).count();
            let rate = py_div(survival_count as f64, count as f64)?;
            let distinct: BTreeSet<bool> = survivals.iter().copied().collect();
            per_cell.insert(
                format!("{strategy}::{intervention}"),
                json!({
                    "strategy": strategy,
                    "intervention": intervention,
                    "trials": count,
                    "survival_count": survival_count,
                    "survival_rate": py_round(rate, 6),
                    "deterministic": distinct.len() == 1,
                    "unrecovered_silent_forgetting_rate":
                        py_round(unrecovered.iter().filter(|u| **u).count() as f64 / count as f64, 6),
                    "mean_recovery_steps": py_round(steps.iter().sum::<i64>() as f64 / count as f64, 6),
                    "mean_bytes_moved": py_round(byte_costs.iter().sum::<i64>() as f64 / count as f64, 6),
                }),
            );
        }
    }

    let mut per_strategy = Map::new();
    for strategy in STRATEGIES {
        let rows: Vec<&Value> = INTERVENTIONS.iter().map(|i| &per_cell[&format!("{strategy}::{i}")]).collect();
        let mean = |key: &str| {
            let mut total = 0.0;
            for row in &rows {
                total += as_f64(&row[key]);
            }
            py_round(total / rows.len() as f64, 6)
        };
        per_strategy.insert(
            strategy.to_string(),
            json!({
                "capability_survival_rate": mean("survival_rate"),
                "unrecovered_silent_forgetting_rate": mean("unrecovered_silent_forgetting_rate"),
                "mean_bytes_moved": mean("mean_bytes_moved"),
                "fully_deterministic": rows.iter().all(|r| r["deterministic"] == true),
                "nondeterministic_cells": sorted_strings(rows.iter().filter(|r| r["deterministic"] != true)
                    .map(|r| r["intervention"].as_str().unwrap_or_default().to_string()).collect()),
            }),
        );
    }

    let mut survival_matrix = Map::new();
    for strategy in STRATEGIES {
        let mut row = Map::new();
        for intervention in INTERVENTIONS {
            row.insert(
                intervention.to_string(),
                per_cell[&format!("{strategy}::{intervention}")]["survival_rate"].clone(),
            );
        }
        survival_matrix.insert(strategy.to_string(), Value::Object(row));
    }

    Ok(json!({
        "schema_version": "1.0",
        "benchmark_version": BENCHMARK_VERSION,
        "mode": "sweep",
        "seed_start": seed,
        "trials": trials,
        "seeds": seeds,
        "strategies": STRATEGIES,
        "interventions": INTERVENTIONS,
        "survival_matrix": survival_matrix,
        "per_strategy": per_strategy,
        "cells": per_cell,
    }))
}

fn strings_of(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or_default().to_string()).collect()).unwrap_or_default()
}

/// `str(value)` of an artifact number (ints print without a decimal point).
fn num_str(v: &Value) -> String {
    crate::coordination::core::py_str_value(v)
}

/// `render_sweep_markdown`: the human-readable sweep summary (`RESULTS.md`); the JSON stays the source of truth.
pub fn render_sweep_markdown(result: &Value) -> String {
    let strategies = strings_of(&result["strategies"]);
    let interventions = strings_of(&result["interventions"]);
    let mut lines: Vec<String> = Vec::new();
    lines.push(format!(
        "# Capability survival sweep ({} seeds from {})",
        num_str(&result["trials"]),
        num_str(&result["seed_start"])
    ));
    lines.push(String::new());
    lines.push("Mean capability survival rate per (strategy, intervention).".into());
    lines.push(String::new());
    let header: Vec<String> = interventions.iter().map(|i| i.replace('_', " ")).collect();
    lines.push(format!("| strategy | {} | mean |", header.join(" | ")));
    lines.push(format!("|{}", "---|".repeat(header.len() + 2)));
    for strategy in &strategies {
        let row = &result["survival_matrix"][strategy];
        let cells: Vec<String> = interventions.iter().map(|i| format!("{:.2}", as_f64(&row[i]))).collect();
        let mean = as_f64(&result["per_strategy"][strategy]["capability_survival_rate"]);
        lines.push(format!("| `{strategy}` | {} | **{mean:.3}** |", cells.join(" | ")));
    }
    lines.push(String::new());
    lines.push("| strategy | survival | unrecovered silent forgetting | mean bytes | deterministic |".into());
    lines.push("|---|---|---|---|---|".into());
    for strategy in &strategies {
        let s = &result["per_strategy"][strategy];
        let det = if s["fully_deterministic"] == true {
            "yes".to_string()
        } else {
            format!("NO: {}", strings_of(&s["nondeterministic_cells"]).join(", "))
        };
        lines.push(format!(
            "| `{strategy}` | {:.3} | {:.3} | {:.0} | {det} |",
            as_f64(&s["capability_survival_rate"]),
            as_f64(&s["unrecovered_silent_forgetting_rate"]),
            as_f64(&s["mean_bytes_moved"]),
        ));
    }
    lines.push(String::new());
    lines.join("\n")
}

/// `render_markdown`: human-readable summary of the single-seed matrix.
pub fn render_markdown(result: &Value) -> String {
    let strategies = strings_of(&result["strategies"]);
    let interventions = strings_of(&result["interventions"]);
    let mut lines: Vec<String> = Vec::new();
    lines.push(format!("# Capability survival benchmark (seed {})", num_str(&result["seed"])));
    lines.push(String::new());
    lines.push(format!(
        "`{}` · {} strategies × {} interventions",
        result["benchmark_version"].as_str().unwrap_or_default(),
        strategies.len(),
        interventions.len()
    ));
    lines.push(String::new());
    lines.push("## Capability survival matrix".into());
    lines.push(String::new());
    let short: Vec<String> = interventions.iter().map(|i| i.replace('_', " ")).collect();
    lines.push(format!("| strategy | {} | rate |", short.join(" | ")));
    lines.push(format!("|{}", "---|".repeat(interventions.len() + 2)));
    for strategy in &strategies {
        let row = &result["matrix"][strategy];
        let cells: Vec<&str> = interventions.iter().map(|i| if row[i] == true { "✅" } else { "❌" }).collect();
        let rate = as_f64(&result["per_strategy"][strategy]["capability_survival_rate"]);
        lines.push(format!("| `{strategy}` | {} | **{rate:.2}** |", cells.join(" | ")));
    }
    lines.push(String::new());
    lines.push("## Per-strategy summary".into());
    lines.push(String::new());
    lines.push(
        "| strategy | survival | silent forgetting | records | struct min-cut | observed min-cut | roots | mean bytes | \
         privacy preserved |"
            .into(),
    );
    lines.push(format!("|{}", "---|".repeat(9)));
    for strategy in &strategies {
        let s = &result["per_strategy"][strategy];
        lines.push(format!(
            "| `{strategy}` | {:.2} | {:.2} | {} | {} | {} | {} | {:.0} | {} |",
            as_f64(&s["capability_survival_rate"]),
            as_f64(&s["silent_forgetting_rate"]),
            num_str(&s["record_count"]),
            num_str(&s["structural_min_cut"]),
            num_str(&s["observed_min_failure_domain_cut"]),
            num_str(&s["structural_root_count"]),
            as_f64(&s["mean_bytes_moved"]),
            if s["privacy_preserved"] == true { "yes" } else { "NO" },
        ));
    }
    lines.push(String::new());
    lines.push("## Per-intervention summary".into());
    lines.push(String::new());
    lines.push("| intervention | survival rate | strategies surviving |".into());
    lines.push("|---|---|---|".into());
    for intervention in &interventions {
        let s = &result["per_intervention"][intervention];
        let surviving = strings_of(&s["strategies_surviving"]).iter().map(|x| format!("`{x}`")).collect::<Vec<_>>();
        let surviving = if surviving.is_empty() { "_none_".to_string() } else { surviving.join(", ") };
        lines.push(format!("| `{intervention}` | {:.2} | {surviving} |", as_f64(&s["survival_rate"])));
    }
    lines.push(String::new());
    lines.join("\n")
}
