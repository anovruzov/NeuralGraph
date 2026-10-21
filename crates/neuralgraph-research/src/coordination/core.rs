//! Deterministic coordination, lineage analysis, failure, and repair logic (port of `coordination/core.py`).
//!
//! The Python coroutines here awaited nothing but other in-process coroutines (the mock and real adapters, whose
//! callbacks are synchronous in every caller), so the port is synchronous. Objects that Python shared by
//! reference between collaborators — the logical clock, the trace log, the failure injector and the registry —
//! are `Rc`s with interior mutability, so one tick of the clock or one injected failure is seen by everyone that
//! holds it, exactly as before.

use crate::coordination::adapters::{BlockedReasonQuery, FailureView, MemoryNodeAdapter};
use crate::coordination::contracts::{
    fields, str_list, ClaimEnvelope, CoordinationError, CoordinationResult, EvidenceExport, FragilityMetrics,
    InterventionRecord, InterventionTarget, PolicyStatus, QueryRequest, SynthesisResult, ToJsonable, TraceEvent,
    TraceEventType, VerificationRequest, VerificationResult,
};
use crate::pytime::PyDateTime;
use chrono::{DateTime, TimeDelta, TimeZone, Utc};
use indexmap::IndexMap;
use neuralgraph_memory::pycompat::{dumps, py_round, JsonFormat};
use serde_json::{json, Map, Value};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::rc::Rc;

/// `to_jsonable(value)`: kept as a free function so call sites read like the Python.
pub fn to_jsonable<T: ToJsonable + ?Sized>(value: &T) -> Value {
    value.to_jsonable()
}

/// `json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))` (ASCII-escaped, like Python).
pub fn canonical_json<T: ToJsonable + ?Sized>(value: &T) -> String {
    dumps(&value.to_jsonable(), &canonical_format())
}

/// The `json.dumps` options of [`canonical_json`].
pub fn canonical_format() -> JsonFormat {
    JsonFormat { ensure_ascii: true, sort_keys: true, indent: None, item_sep: ",", key_sep: ":" }
}

/// `json.dumps(result, sort_keys=True, indent=2)` — the format of every artifact (the CLI appends `"\n"`).
/// With an indent, Python's item separator drops its trailing space.
pub fn artifact_format() -> JsonFormat {
    JsonFormat { ensure_ascii: true, sort_keys: true, indent: Some(2), item_sep: ",", key_sep: ": " }
}

/// `json.dumps(result, sort_keys=True, indent=2) + "\n"`, the bytes the entry points write.
pub fn artifact_json(result: &Value) -> String {
    let mut payload = dumps(result, &artifact_format());
    payload.push('\n');
    payload
}

/// `sequence[:n]` for a Python int `n` (negative counts from the end).
pub fn py_head<T>(items: &[T], n: i64) -> &[T] {
    let len = items.len() as i64;
    let end = if n >= 0 { n.min(len) } else { (len + n).max(0) };
    &items[..end as usize]
}

/// Truthiness of an optional string (`if selected:` in Python is false for `None` and for `""`).
pub fn py_truthy_str(value: &Option<String>) -> bool {
    value.as_deref().is_some_and(|s| !s.is_empty())
}

/// Seed-derived clock used to make experiment replay byte-stable.
#[derive(Debug)]
pub struct LogicalClock {
    current: Cell<DateTime<Utc>>,
}

impl LogicalClock {
    pub fn new(seed: i64) -> Self {
        let start = Utc.with_ymd_and_hms(2025, 1, 1, 0, 0, 0).single().expect("valid epoch")
            + TimeDelta::seconds(seed.rem_euclid(86400));
        LogicalClock { current: Cell::new(start) }
    }

    /// The current instant as `isoformat()` with `+00:00` written `Z`, then advance one millisecond.
    pub fn now(&self) -> String {
        let current = self.current.get();
        let value = PyDateTime::utc(current).isoformat().replace("+00:00", "Z");
        self.current.set(current + TimeDelta::milliseconds(1));
        value
    }
}

/// A `clock.now` bound method, the shape the adapters take.
pub fn clock_fn(clock: &Rc<LogicalClock>) -> Rc<dyn Fn() -> String> {
    let clock = clock.clone();
    Rc::new(move || clock.now())
}

#[derive(Debug)]
pub struct TraceLogger {
    pub run_id: String,
    clock: Rc<LogicalClock>,
    events: RefCell<Vec<TraceEvent>>,
}

impl TraceLogger {
    pub const SCHEMA_VERSION: &'static str = "1.0";

    pub fn new(run_id: impl Into<String>, clock: Rc<LogicalClock>) -> Self {
        TraceLogger { run_id: run_id.into(), clock, events: RefCell::new(Vec::new()) }
    }

    /// `events` (a snapshot tuple in Python).
    pub fn events(&self) -> Vec<TraceEvent> {
        self.events.borrow().clone()
    }

    pub fn len(&self) -> usize {
        self.events.borrow().len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.borrow().is_empty()
    }

    /// `emit(event_type, trace_id, **details)`; `details` is `to_jsonable`'d, which sorts its keys.
    pub fn emit(&self, event_type: TraceEventType, trace_id: &str, details: Vec<(&str, Value)>) -> TraceEvent {
        let sequence = self.events.borrow().len() as i64 + 1;
        let sorted: BTreeMap<&str, Value> = details.into_iter().collect();
        let mut map = Map::new();
        for (k, v) in sorted {
            map.insert(k.to_string(), v);
        }
        let event = TraceEvent {
            schema_version: Self::SCHEMA_VERSION.to_string(),
            event_id: format!("event_{sequence:04}"),
            run_id: self.run_id.clone(),
            trace_id: trace_id.to_string(),
            event_type,
            sequence,
            occurred_at: self.clock.now(),
            details: Value::Object(map),
        };
        self.events.borrow_mut().push(event.clone());
        event
    }
}

/// Reversible experiment masks; no local memory is deleted or mutated.
#[derive(Debug)]
pub struct FailureInjector {
    run_id: String,
    seed: i64,
    clock: Rc<LogicalClock>,
    traces: Rc<TraceLogger>,
    active: RefCell<HashMap<InterventionTarget, BTreeSet<String>>>,
    records: RefCell<Vec<InterventionRecord>>,
}

impl FailureInjector {
    pub fn new(run_id: impl Into<String>, seed: i64, clock: Rc<LogicalClock>, traces: Rc<TraceLogger>) -> Self {
        FailureInjector {
            run_id: run_id.into(),
            seed,
            clock,
            traces,
            active: RefCell::new(InterventionTarget::ALL.iter().map(|t| (*t, BTreeSet::new())).collect()),
            records: RefCell::new(Vec::new()),
        }
    }

    pub fn records(&self) -> Vec<InterventionRecord> {
        self.records.borrow().clone()
    }

    pub fn apply(
        &self,
        target_type: InterventionTarget,
        target_id: &str,
        reason: &str,
        expected_effect: Option<&str>,
    ) -> CoordinationResult<InterventionRecord> {
        if self.is_active(target_type, target_id) {
            return Err(CoordinationError::value_error(format!(
                "intervention already active: {}/{target_id}",
                target_type.value()
            )));
        }
        self.active.borrow_mut().entry(target_type).or_default().insert(target_id.to_string());
        let count = self.records.borrow().len();
        let record = InterventionRecord {
            run_id: self.run_id.clone(),
            intervention_id: format!("intervention_{:03}", count + 1),
            target_type,
            target_id: target_id.to_string(),
            started_at: self.clock.now(),
            reason: reason.to_string(),
            random_seed: self.seed,
            expected_effect: expected_effect.map(str::to_string),
            ended_at: None,
        };
        self.records.borrow_mut().push(record.clone());
        self.traces.emit(
            TraceEventType::FailureInjected,
            &record.intervention_id,
            vec![
                ("intervention_id", json!(record.intervention_id)),
                ("target_type", json!(target_type.value())),
                ("target_id", json!(target_id)),
                ("reason", json!(reason)),
                ("expected_effect", json!(expected_effect)),
            ],
        );
        Ok(record)
    }

    /// Lift a mask and close the latest open record for it (the clock ticks only when one is closed).
    pub fn clear(&self, target_type: InterventionTarget, target_id: &str) {
        self.active.borrow_mut().entry(target_type).or_default().remove(target_id);
        let mut records = self.records.borrow_mut();
        for record in records.iter_mut().rev() {
            if record.target_type == target_type && record.target_id == target_id && record.ended_at.is_none() {
                record.ended_at = Some(self.clock.now());
                return;
            }
        }
    }

    pub fn is_active(&self, target_type: InterventionTarget, target_id: &str) -> bool {
        self.active.borrow().get(&target_type).is_some_and(|s| s.contains(target_id))
    }

    pub fn route_available(&self, node_id: &str) -> bool {
        !self.is_active(InterventionTarget::Route, node_id)
    }
}

/// `datetime.fromisoformat(value.replace("Z", "+00:00"))`, raising what Python raised for a malformed value.
fn parse_iso(value: &str) -> CoordinationResult<PyDateTime> {
    let normalized = value.replace('Z', "+00:00");
    PyDateTime::fromisoformat(&normalized).ok_or_else(|| {
        CoordinationError::value_error(format!(
            "Invalid isoformat string: {}",
            neuralgraph_memory::pycompat::py_repr_str(&normalized)
        ))
    })
}

impl FailureView for FailureInjector {
    fn blocked_reason(&self, q: &BlockedReasonQuery<'_>) -> CoordinationResult<Option<String>> {
        let checks: [(InterventionTarget, Vec<&str>, &str); 7] = [
            (InterventionTarget::Node, vec![q.node_id], "node unavailable"),
            (InterventionTarget::Memory, vec![q.memory_id], "memory unavailable"),
            (InterventionTarget::Source, q.source_ids.iter().map(String::as_str).collect(), "source unavailable"),
            (
                InterventionTarget::LineageRoot,
                q.lineage_root_ids.iter().map(String::as_str).collect(),
                "lineage root unavailable",
            ),
            (InterventionTarget::Edge, q.edge_path.iter().map(String::as_str).collect(), "edge unavailable"),
            (InterventionTarget::AuthorizationScope, vec![q.required_scope], "authorization scope revoked"),
            (InterventionTarget::Evidence, vec![q.memory_id], "evidence stale or expired"),
        ];
        for (target_type, identifiers, reason) in checks {
            if identifiers.iter().any(|id| self.is_active(target_type, id)) {
                return Ok(Some(reason.to_string()));
            }
        }
        if let (Some(valid_until), Some(valid_at)) = (q.valid_until, q.valid_at) {
            if !valid_until.is_empty() && !valid_at.is_empty() {
                let until = parse_iso(valid_until)?;
                let at = parse_iso(valid_at)?;
                let order = at.py_cmp(&until).map_err(|_| {
                    CoordinationError::type_error("can't compare offset-naive and offset-aware datetimes")
                })?;
                if order == std::cmp::Ordering::Greater {
                    return Ok(Some("evidence expired".to_string()));
                }
            }
        }
        Ok(None)
    }
}

#[derive(Default)]
pub struct CapabilityRegistry {
    adapters: RefCell<IndexMap<String, Rc<dyn MemoryNodeAdapter>>>,
}

impl CapabilityRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&self, adapter: Rc<dyn MemoryNodeAdapter>) -> CoordinationResult<()> {
        let node_id = adapter.node_id().to_string();
        let mut adapters = self.adapters.borrow_mut();
        if adapters.contains_key(&node_id) {
            return Err(CoordinationError::value_error(format!("node already registered: {node_id}")));
        }
        adapters.insert(node_id, adapter);
        Ok(())
    }

    pub fn adapter_for(&self, node_id: &str) -> CoordinationResult<Rc<dyn MemoryNodeAdapter>> {
        self.adapters.borrow().get(node_id).cloned().ok_or_else(|| CoordinationError::key_error(node_id))
    }

    pub fn node_ids(&self) -> Vec<String> {
        self.adapters.borrow().keys().cloned().collect()
    }

    pub fn eligible_nodes(&self, request: &QueryRequest, failure: &FailureInjector) -> CoordinationResult<Vec<String>> {
        let adapters: Vec<(String, Rc<dyn MemoryNodeAdapter>)> =
            self.adapters.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let mut eligible = Vec::new();
        for (node_id, adapter) in adapters {
            if !failure.route_available(&node_id) {
                continue;
            }
            let descriptors = adapter.describe_capabilities(&request.authorization)?;
            if descriptors.iter().any(|d| {
                Some(&d.capability_id) == request.requested_capability.as_ref()
                    && d.availability.value() != "unavailable"
            }) {
                eligible.push(node_id);
            }
        }
        Ok(eligible)
    }
}

pub struct Router {
    registry: Rc<CapabilityRegistry>,
    failure: Rc<FailureInjector>,
}

impl Router {
    pub fn new(registry: Rc<CapabilityRegistry>, failure: Rc<FailureInjector>) -> Self {
        Router { registry, failure }
    }

    pub fn select(
        &self,
        request: &QueryRequest,
        preferred_node_ids: Option<&[String]>,
    ) -> CoordinationResult<Vec<String>> {
        let eligible = self.registry.eligible_nodes(request, &self.failure)?;
        let ordered: Vec<String> = match preferred_node_ids {
            None => eligible,
            Some(preferred_ids) => {
                let preferred: Vec<String> = preferred_ids.iter().filter(|n| eligible.contains(n)).cloned().collect();
                let rest: Vec<String> = eligible.iter().filter(|n| !preferred.contains(n)).cloned().collect();
                preferred.into_iter().chain(rest).collect()
            }
        };
        Ok(py_head(&ordered, request.budget.max_nodes).to_vec())
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ClaimNormalizer;

impl ClaimNormalizer {
    /// Keep allowed claims for this query whose content is a mapping with `slot` and `value`; one per claim id
    /// (the last one wins, like a dict assignment), sorted by claim id.
    pub fn normalize(&self, claims: &[ClaimEnvelope], query_id: &str) -> Vec<ClaimEnvelope> {
        let mut by_id: IndexMap<String, ClaimEnvelope> = IndexMap::new();
        for claim in claims {
            if claim.query_id != query_id || claim.policy_status != PolicyStatus::Allowed {
                continue;
            }
            let Some(content) = claim.content.as_object() else { continue };
            if !(content.contains_key("slot") && content.contains_key("value")) {
                continue;
            }
            by_id.insert(claim.claim_id.clone(), claim.clone());
        }
        let mut out: Vec<ClaimEnvelope> = by_id.into_values().collect();
        out.sort_by(|a, b| a.claim_id.cmp(&b.claim_id));
        out
    }
}

/// `str(value)` for a claim value: Python's `str()` of a JSON-compatible object (containers use `repr`).
pub fn py_str_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => py_repr_value(other),
    }
}

/// `repr()` of a JSON-compatible Python object (`{'a': [1, True, None]}`).
pub fn py_repr_value(v: &Value) -> String {
    use neuralgraph_memory::pycompat::{py_repr_str, py_str};
    match v {
        Value::String(s) => py_repr_str(s),
        Value::Array(a) => format!("[{}]", a.iter().map(py_repr_value).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter().map(|(k, v)| format!("{}: {}", py_repr_str(k), py_repr_value(v))).collect::<Vec<_>>().join(", ")
        ),
        scalar => py_str(scalar),
    }
}

/// Fixture-neutral slot composition; no model output is involved.
#[derive(Clone, Debug)]
pub struct RuleBasedSynthesizer {
    pub required_slots: Vec<String>,
}

impl RuleBasedSynthesizer {
    pub fn new(required_slots: Vec<String>) -> Self {
        RuleBasedSynthesizer { required_slots }
    }

    /// Pick the most confident claim per required slot. With no required slots Python reached `min()` of an
    /// empty selection and raised `ValueError`; that error is kept.
    pub fn synthesize(&self, claims: &[ClaimEnvelope]) -> CoordinationResult<SynthesisResult> {
        let mut selected: Vec<&ClaimEnvelope> = Vec::new();
        let mut covered: Vec<String> = Vec::new();
        for slot in &self.required_slots {
            let mut candidates: Vec<&ClaimEnvelope> = claims
                .iter()
                .filter(|c| c.slot_is(slot) && c.content_get("value") != Some(&Value::String("[REDACTED]".into())))
                .collect();
            if candidates.is_empty() {
                continue;
            }
            candidates.sort_by(|a, b| {
                (-a.confidence)
                    .partial_cmp(&-b.confidence)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| a.producer_node_id.cmp(&b.producer_node_id))
                    .then_with(|| a.claim_id.cmp(&b.claim_id))
            });
            selected.push(candidates[0]);
            covered.push(slot.clone());
        }
        let success = covered.len() == self.required_slots.len();
        let mut answer = None;
        // max(..., default=0.0): the first maximal value
        let mut confidence = claims.iter().map(|c| c.confidence).fold(None, |best: Option<f64>, c| match best {
            Some(b) if c.partial_cmp(&b) != Some(std::cmp::Ordering::Greater) => Some(b),
            _ => Some(c),
        });
        if success {
            answer = Some(
                selected
                    .iter()
                    .map(|c| py_str_value(c.content_get("value").unwrap_or(&Value::Null)))
                    .collect::<Vec<_>>()
                    .join(":"),
            );
            // min(...): the first minimal value
            confidence = selected.iter().map(|c| c.confidence).fold(None, |best: Option<f64>, c| match best {
                Some(b) if c.partial_cmp(&b) != Some(std::cmp::Ordering::Less) => Some(b),
                _ => Some(c),
            });
            if confidence.is_none() {
                return Err(CoordinationError::value_error("min() arg is an empty sequence"));
            }
        }
        Ok(SynthesisResult {
            success,
            answer,
            confidence: py_round(confidence.unwrap_or(0.0), 6),
            selected_claim_ids: selected.iter().map(|c| c.claim_id.clone()).collect(),
            selected_node_ids: selected.iter().map(|c| c.producer_node_id.clone()).collect(),
            covered_slots: covered,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ReconstructionCoalition {
    pub lineage_root_ids: Vec<String>,
    pub failure_domains: Vec<String>,
    pub claim_ids: Vec<String>,
}

impl ToJsonable for ReconstructionCoalition {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("lineage_root_ids", str_list(&self.lineage_root_ids)),
            ("failure_domains", str_list(&self.failure_domains)),
            ("claim_ids", str_list(&self.claim_ids)),
        ])
    }
}

/// `itertools.product(*pools)`: index tuples, the last position varying fastest.
pub fn product_indices(sizes: &[usize]) -> Vec<Vec<usize>> {
    if sizes.contains(&0) {
        return Vec::new();
    }
    let mut out = vec![Vec::new()];
    for &size in sizes {
        let mut next = Vec::with_capacity(out.len() * size);
        for prefix in &out {
            for i in 0..size {
                let mut tuple = prefix.clone();
                tuple.push(i);
                next.push(tuple);
            }
        }
        out = next;
    }
    out
}

/// `itertools.combinations(range(n), k)`, lazily and in lexicographic order.
pub struct Combinations {
    n: usize,
    indices: Vec<usize>,
    first: bool,
    done: bool,
}

impl Combinations {
    pub fn new(n: usize, k: usize) -> Self {
        Combinations { n, indices: (0..k).collect(), first: true, done: k > n }
    }
}

impl Iterator for Combinations {
    type Item = Vec<usize>;

    fn next(&mut self) -> Option<Vec<usize>> {
        if self.done {
            return None;
        }
        if self.first {
            self.first = false;
            return Some(self.indices.clone());
        }
        let k = self.indices.len();
        let mut i = k;
        loop {
            if i == 0 {
                self.done = true;
                return None;
            }
            i -= 1;
            if self.indices[i] != i + self.n - k {
                break;
            }
        }
        self.indices[i] += 1;
        for j in i + 1..k {
            self.indices[j] = self.indices[j - 1] + 1;
        }
        Some(self.indices.clone())
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct LineageAnalyzer;

impl LineageAnalyzer {
    pub fn reconstruction_coalitions(
        &self,
        claims: &[ClaimEnvelope],
        required_slots: &[String],
    ) -> Vec<ReconstructionCoalition> {
        let per_slot: Vec<Vec<&ClaimEnvelope>> =
            required_slots.iter().map(|slot| claims.iter().filter(|c| c.slot_is(slot)).collect()).collect();
        if per_slot.iter().any(Vec::is_empty) {
            return Vec::new();
        }
        let sizes: Vec<usize> = per_slot.iter().map(Vec::len).collect();
        let mut canonical: BTreeMap<(Vec<String>, Vec<String>), ReconstructionCoalition> = BTreeMap::new();
        for pick in product_indices(&sizes) {
            let selected: Vec<&ClaimEnvelope> = pick.iter().enumerate().map(|(slot, i)| per_slot[slot][*i]).collect();
            let roots: Vec<String> = selected
                .iter()
                .flat_map(|c| c.lineage_root_ids.iter().cloned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            let domains: Vec<String> = selected
                .iter()
                .flat_map(|c| c.failure_domains.iter().cloned())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            if roots.is_empty() || domains.is_empty() {
                continue;
            }
            let mut claim_ids: Vec<String> = selected.iter().map(|c| c.claim_id.clone()).collect();
            claim_ids.sort();
            let key = (roots.clone(), domains.clone());
            let replace = match canonical.get(&key) {
                None => true,
                Some(existing) => claim_ids < existing.claim_ids,
            };
            if replace {
                canonical.insert(
                    key,
                    ReconstructionCoalition { lineage_root_ids: roots, failure_domains: domains, claim_ids },
                );
            }
        }
        canonical.into_values().collect()
    }

    pub fn score(
        &self,
        claims: &[ClaimEnvelope],
        required_slots: &[String],
        synthesis: &SynthesisResult,
    ) -> FragilityMetrics {
        let coalitions = self.reconstruction_coalitions(claims, required_slots);
        let roots: BTreeSet<&String> = claims.iter().flat_map(|c| c.lineage_root_ids.iter()).collect();
        let domains: BTreeSet<&String> = claims.iter().flat_map(|c| c.failure_domains.iter()).collect();
        let min_size = coalitions.iter().map(|c| c.lineage_root_ids.len()).min().unwrap_or(0);
        let min_cut = Self::minimum_domain_cut(&coalitions);
        let coverage = if required_slots.is_empty() {
            1.0
        } else {
            synthesis.covered_slots.len() as f64 / required_slots.len() as f64
        };
        let difference = synthesis.confidence - coverage;
        // max(0.0, x) keeps the first argument unless x is strictly greater
        let gap = if difference > 0.0 { difference } else { 0.0 };
        FragilityMetrics {
            apparent_replica_count: claims.len(),
            unique_lineage_root_count: roots.len(),
            unique_failure_domain_count: domains.len(),
            reconstruction_coalition_count: coalitions.len(),
            minimal_support_coalition_size: min_size,
            minimal_failure_domain_cut: min_cut,
            current_task_success: synthesis.success,
            current_confidence: py_round(synthesis.confidence, 6),
            valid_support_coverage: py_round(coverage, 6),
            confidence_valid_support_gap: py_round(gap, 6),
        }
    }

    /// The smallest set of failure domains that intersects every coalition (0 when there is none).
    pub fn minimum_domain_cut(coalitions: &[ReconstructionCoalition]) -> usize {
        if coalitions.is_empty() {
            return 0;
        }
        let domains: Vec<&String> =
            coalitions.iter().flat_map(|c| c.failure_domains.iter()).collect::<BTreeSet<_>>().into_iter().collect();
        let coalition_domains: Vec<BTreeSet<&String>> =
            coalitions.iter().map(|c| c.failure_domains.iter().collect()).collect();
        for size in 1..=domains.len() {
            for candidate in Combinations::new(domains.len(), size) {
                if coalition_domains.iter().all(|set| candidate.iter().any(|i| set.contains(domains[*i]))) {
                    return size;
                }
            }
        }
        0
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct QueryExecution {
    pub phase: String,
    pub route: Vec<String>,
    pub claims: Vec<ClaimEnvelope>,
    pub exports: Vec<EvidenceExport>,
    pub synthesis: SynthesisResult,
    pub metrics: FragilityMetrics,
    pub exported_bytes: usize,
    pub policy_violations: usize,
}

impl ToJsonable for QueryExecution {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("phase", json!(self.phase)),
            ("route", str_list(&self.route)),
            ("claims", self.claims.to_jsonable()),
            ("exports", self.exports.to_jsonable()),
            ("synthesis", self.synthesis.to_jsonable()),
            ("metrics", self.metrics.to_jsonable()),
            ("exported_bytes", json!(self.exported_bytes)),
            ("policy_violations", json!(self.policy_violations)),
        ])
    }
}

pub struct TesseractCoordinator {
    pub registry: Rc<CapabilityRegistry>,
    pub router: Router,
    pub normalizer: ClaimNormalizer,
    pub synthesizer: RuleBasedSynthesizer,
    pub analyzer: LineageAnalyzer,
    pub traces: Rc<TraceLogger>,
}

impl TesseractCoordinator {
    pub fn new(
        registry: Rc<CapabilityRegistry>,
        router: Router,
        normalizer: ClaimNormalizer,
        synthesizer: RuleBasedSynthesizer,
        analyzer: LineageAnalyzer,
        traces: Rc<TraceLogger>,
    ) -> Self {
        TesseractCoordinator { registry, router, normalizer, synthesizer, analyzer, traces }
    }

    pub fn execute(
        &self,
        request: &QueryRequest,
        phase: &str,
        preferred_node_ids: Option<&[String]>,
    ) -> CoordinationResult<QueryExecution> {
        let trace_id = format!("query_trace_{}_{phase}", request.query_id);
        self.traces.emit(
            TraceEventType::QueryReceived,
            &trace_id,
            vec![("query_id", json!(request.query_id)), ("phase", json!(phase))],
        );
        let route = self.router.select(request, preferred_node_ids)?;
        self.traces.emit(
            TraceEventType::RouteSelected,
            &trace_id,
            vec![("node_ids", str_list(&route)), ("phase", json!(phase))],
        );
        let mut exports: Vec<EvidenceExport> = Vec::new();
        let mut raw_claims: Vec<ClaimEnvelope> = Vec::new();
        for node_id in &route {
            let node_exports = self.registry.adapter_for(node_id)?.query(request)?;
            for export in node_exports {
                self.traces.emit(
                    TraceEventType::RetrievalSelected,
                    &trace_id,
                    vec![
                        ("node_id", json!(node_id)),
                        ("local_trace_id", json!(export.trace.trace_id)),
                        ("retrieval_operator", json!(export.trace.retrieval_operator)),
                        ("policy_status", json!(export.trace.policy_status.value())),
                        ("lineage_root_ids", str_list(&export.trace.lineage_root_ids)),
                    ],
                );
                if export.trace.policy_status != PolicyStatus::Allowed {
                    self.traces.emit(
                        TraceEventType::PolicyBlock,
                        &trace_id,
                        vec![("node_id", json!(node_id)), ("policy_status", json!(export.trace.policy_status.value()))],
                    );
                }
                for claim in &export.claims {
                    raw_claims.push(claim.clone());
                    self.traces.emit(
                        TraceEventType::EvidenceExported,
                        &trace_id,
                        vec![
                            ("node_id", json!(node_id)),
                            ("claim_id", json!(claim.claim_id)),
                            ("lineage_root_ids", str_list(&claim.lineage_root_ids)),
                            ("failure_domains", str_list(&claim.failure_domains)),
                        ],
                    );
                }
                exports.push(export);
            }
        }
        let claims = self.normalizer.normalize(py_head(&raw_claims, request.budget.max_claims), &request.query_id);
        let synthesis = self.synthesizer.synthesize(&claims)?;
        self.traces.emit(
            TraceEventType::ClaimSynthesized,
            &trace_id,
            vec![
                ("success", json!(synthesis.success)),
                ("selected_claim_ids", str_list(&synthesis.selected_claim_ids)),
                ("selected_node_ids", str_list(&synthesis.selected_node_ids)),
            ],
        );
        let metrics = self.analyzer.score(&claims, &self.synthesizer.required_slots, &synthesis);
        self.traces.emit(TraceEventType::FragilityScored, &trace_id, vec![("metrics", metrics.to_jsonable())]);
        self.traces.emit(
            TraceEventType::CapabilityEvaluated,
            &trace_id,
            vec![
                ("phase", json!(phase)),
                ("success", json!(synthesis.success)),
                ("confidence", synthesis.confidence.to_jsonable()),
            ],
        );
        let exported_bytes = canonical_json(&exports).len();
        let policy_violations =
            exports.iter().filter(|e| e.trace.policy_status != PolicyStatus::Allowed && !e.claims.is_empty()).count();
        Ok(QueryExecution {
            phase: phase.to_string(),
            route,
            claims,
            exports,
            synthesis,
            metrics,
            exported_bytes,
            policy_violations,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RepairDecision {
    pub selected_node_id: Option<String>,
    pub verification_results: Vec<VerificationResult>,
    pub steps: usize,
    pub reason: String,
}

impl ToJsonable for RepairDecision {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("selected_node_id", json!(self.selected_node_id)),
            ("verification_results", self.verification_results.to_jsonable()),
            ("steps", json!(self.steps)),
            ("reason", json!(self.reason)),
        ])
    }
}

/// Select the first verified path independent of current and failed support.
pub struct RepairPlanner {
    registry: Rc<CapabilityRegistry>,
    traces: Rc<TraceLogger>,
}

impl RepairPlanner {
    pub fn new(registry: Rc<CapabilityRegistry>, traces: Rc<TraceLogger>) -> Self {
        RepairPlanner { registry, traces }
    }

    pub fn plan(
        &self,
        request: &QueryRequest,
        missing_slots: &[String],
        candidate_node_ids: &[String],
        excluded_roots: &[String],
        excluded_failure_domains: &[String],
    ) -> CoordinationResult<RepairDecision> {
        let trace_id = format!("repair_{}", request.query_id);
        self.traces.emit(
            TraceEventType::RepairRequested,
            &trace_id,
            vec![
                ("missing_slots", str_list(missing_slots)),
                ("candidate_node_ids", str_list(candidate_node_ids)),
                ("excluded_roots", str_list(excluded_roots)),
                ("excluded_failure_domains", str_list(excluded_failure_domains)),
            ],
        );
        let mut results = Vec::new();
        let mut selected: Option<String> = None;
        for (index, node_id) in py_head(candidate_node_ids, request.budget.max_verifications).iter().enumerate() {
            let step = index + 1;
            let verification = VerificationRequest {
                verification_id: format!("verification_{}_{step:02}", request.query_id),
                query: request.clone(),
                required_slots: missing_slots.to_vec(),
                excluded_lineage_roots: excluded_roots.to_vec(),
                excluded_failure_domains: excluded_failure_domains.to_vec(),
            };
            self.traces.emit(
                TraceEventType::VerificationRequested,
                &trace_id,
                vec![("verification_id", json!(verification.verification_id)), ("node_id", json!(node_id))],
            );
            let result = self.registry.adapter_for(node_id)?.verify(&verification)?;
            self.traces.emit(TraceEventType::VerificationResult, &trace_id, vec![("result", result.to_jsonable())]);
            let valid = result.valid;
            results.push(result);
            if valid {
                selected = Some(node_id.clone());
                break;
            }
        }
        let reason = if py_truthy_str(&selected) {
            "independent support selected"
        } else {
            "no independent support within budget"
        };
        self.traces.emit(
            TraceEventType::RepairCompleted,
            &trace_id,
            vec![("selected_node_id", json!(selected)), ("reason", json!(reason)), ("steps", json!(results.len()))],
        );
        Ok(RepairDecision {
            selected_node_id: selected,
            steps: results.len(),
            verification_results: results,
            reason: reason.into(),
        })
    }
}
