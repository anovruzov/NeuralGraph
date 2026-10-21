//! Seeded opaque four-node fixture for collective capability experiments (port of `coordination/fixture.py`).
//!
//! The opaque symbols come from CPython's `random.Random(seed).choice` ([`PyRandom`]), so a seed yields the same
//! symbols as in Python and the pinned experiment artifact reproduces byte for byte.

use crate::coordination::adapters::{FailureView, MemoryNodeAdapter, MockMemoryNodeAdapter, PrivateMemoryRecord};
use crate::coordination::contracts::{
    strs, AuthorizationContext, CoordinationResult, LearningDisposition, QueryBudget, QueryRequest,
};
use crate::coordination::core::{clock_fn, CapabilityRegistry, FailureInjector, LogicalClock, TraceLogger};
use crate::pyrandom::PyRandom;
use indexmap::IndexMap;
use std::collections::HashSet;
use std::rc::Rc;

pub const FIXTURE_VERSION: &str = "collective-capability-v1";
pub const CAPABILITY_ID: &str = "opaque_pair_reconstruction";
pub const REQUIRED_SCOPE: &str = "capability:opaque-pair";
pub const REQUIRED_SLOTS: [&str; 2] = ["left", "right"];

/// `REQUIRED_SLOTS` as owned strings.
pub fn required_slots() -> Vec<String> {
    strs(&REQUIRED_SLOTS)
}

#[derive(Clone, Debug)]
pub struct OpaqueFixture {
    pub seed: i64,
    pub run_id: String,
    pub query: QueryRequest,
    pub expected_answer: String,
    pub left_symbol: String,
    pub right_symbol: String,
    pub distractor_symbols: Vec<String>,
    /// Node id -> that node's private records (a separate collection per node).
    pub records_by_node: IndexMap<String, Vec<PrivateMemoryRecord>>,
}

pub struct FixtureRuntime {
    pub fixture: OpaqueFixture,
    pub clock: Rc<LogicalClock>,
    pub traces: Rc<TraceLogger>,
    pub failure: Rc<FailureInjector>,
    pub registry: Rc<CapabilityRegistry>,
}

const OPAQUE_ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/// `_opaque(rng, prefix)`: `prefix_` plus 14 characters of `string.ascii_uppercase + string.digits`.
pub fn opaque(rng: &mut PyRandom, prefix: &str) -> String {
    let body: String = (0..14).map(|_| *rng.choice(OPAQUE_ALPHABET) as char).collect();
    format!("{prefix}_{body}")
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
        REQUIRED_SCOPE,
    )
}

pub fn generate_fixture(seed: i64, strategy: &str) -> CoordinationResult<OpaqueFixture> {
    let mut rng = PyRandom::new(seed as i128);
    let left = opaque(&mut rng, "LX");
    let right = opaque(&mut rng, "RX");
    let distractors: Vec<String> = (0..4).map(|_| opaque(&mut rng, "DX")).collect();
    let query_nonce = opaque(&mut rng, "QX");
    let run_id = format!("run_{seed}_{strategy}");
    let issued_at = LogicalClock::new(seed).now();
    let query = QueryRequest {
        query_id: format!("query_{seed}"),
        content: format!("Reconstruct the two-part capability identified by {query_nonce}"),
        requested_capability: Some(CAPABILITY_ID.to_string()),
        requester_id: "experiment-controller".to_string(),
        issued_at: issued_at.clone(),
        valid_at: Some(issued_at),
        authorization: AuthorizationContext::new(
            strs(&[REQUIRED_SCOPE]),
            strs(&["node-a", "node-b", "node-c", "node-d"]),
            false,
        )?,
        budget: QueryBudget::new(3, 8, 2)?,
    }
    .validated()?;
    let mut records: IndexMap<String, Vec<PrivateMemoryRecord>> = IndexMap::new();
    records.insert("node-a".into(), vec![record("a", "left", &left, "memory-a", "source-1", "R1", "FD1", 0.96)]);
    records.insert("node-b".into(), vec![record("b", "right", &right, "memory-b", "source-2", "R2", "FD2", 0.93)]);
    // C is an apparent replica, not an independent reconstruction path.
    records.insert("node-c".into(), vec![record("c", "right", &right, "memory-c", "source-2-copy", "R2", "FD2", 0.91)]);
    // D has equivalent support through independent source/root/domain R3/FD3.
    records.insert("node-d".into(), vec![record("d", "right", &right, "memory-d", "source-3", "R3", "FD3", 0.94)]);
    Ok(OpaqueFixture {
        seed,
        run_id,
        query,
        expected_answer: format!("{left}:{right}"),
        left_symbol: left,
        right_symbol: right,
        distractor_symbols: distractors,
        records_by_node: records,
    })
}

pub fn build_runtime(seed: i64, strategy: &str) -> CoordinationResult<FixtureRuntime> {
    let fixture = generate_fixture(seed, strategy)?;
    let clock = Rc::new(LogicalClock::new(seed));
    let traces = Rc::new(TraceLogger::new(fixture.run_id.clone(), clock.clone()));
    let failure = Rc::new(FailureInjector::new(fixture.run_id.clone(), seed, clock.clone(), traces.clone()));
    let registry = Rc::new(CapabilityRegistry::new());
    for node_id in ["node-a", "node-b", "node-c", "node-d"] {
        let failure_view: Rc<dyn FailureView> = failure.clone();
        let adapter: Rc<dyn MemoryNodeAdapter> = Rc::new(MockMemoryNodeAdapter::new(
            node_id,
            fixture.records_by_node[node_id].clone(),
            failure_view,
            clock_fn(&clock),
            if node_id == "node-d" { LearningDisposition::Tentative } else { LearningDisposition::Rejected },
        ));
        registry.register(adapter)?;
    }
    Ok(FixtureRuntime { fixture, clock, traces, failure, registry })
}

/// The privacy properties the experiment reports, by name (Python returned a dict in this order).
pub fn assert_fixture_privacy(fixture: &OpaqueFixture) -> IndexMap<String, bool> {
    let mut all_values = vec![fixture.left_symbol.clone(), fixture.right_symbol.clone()];
    all_values.extend(fixture.distractor_symbols.iter().cloned());
    let unrelated = &fixture.distractor_symbols;
    let mut out = IndexMap::new();
    out.insert(
        "answer_absent_from_query".to_string(),
        all_values.iter().all(|symbol| !fixture.query.content.contains(symbol.as_str())),
    );
    out.insert(
        "complete_answer_absent_from_every_memory".to_string(),
        fixture.records_by_node.values().flatten().all(|r| fixture.expected_answer != r.value),
    );
    out.insert(
        "answer_symbols_absent_from_distractors".to_string(),
        unrelated
            .iter()
            .all(|v| !v.contains(fixture.left_symbol.as_str()) && !v.contains(fixture.right_symbol.as_str())),
    );
    // `len({id(records)}) == 4`: four distinct collection objects. Each Vec owns its buffer, so the buffer
    // address is the identity (records are never empty here, so no two share the dangling empty pointer).
    let identities: HashSet<*const PrivateMemoryRecord> =
        fixture.records_by_node.values().map(|v| v.as_ptr()).collect();
    out.insert("nodes_have_separate_record_collections".to_string(), identities.len() == 4);
    out
}
