//! Domain-neutral contracts for coordination across private memory nodes (port of `coordination/contracts.py`).
//!
//! These objects deliberately contain no NeuralGraph node or edge instances. They are the complete trust boundary
//! between a local memory owner and Tesseract's coordination plane.
//!
//! Python's frozen dataclasses validated themselves in `__post_init__`, which also ran on every
//! `dataclasses.replace`. Here every contract is a plain struct with public fields plus a `validate()` that
//! performs exactly those checks; the constructors that the Python call sites used (`new`) run it. Python
//! exceptions become [`CoordinationError`], which keeps the exception type name and message so callers (and the
//! ported tests) can assert on what Python raised.
//!
//! `to_jsonable` (in `core`) turned a dataclass into `asdict` order with enums replaced by their values; the
//! [`ToJsonable`] implementations here produce the same structures. Every serialised output sorts its keys, so
//! the field order only matters for readability.

use crate::pytime::PyDateTime;
use neuralgraph_memory::pycompat::{fnum, py_repr_str, py_strip};
use serde_json::{json, Map, Value};
use std::fmt;

/// A Python exception: the exception class name and `str(exc)`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CoordinationError {
    /// `type(exc).__name__`, e.g. `ValueError`.
    pub kind: String,
    /// `str(exc)`.
    pub message: String,
}

impl CoordinationError {
    pub fn new(kind: &str, message: impl Into<String>) -> Self {
        CoordinationError { kind: kind.to_string(), message: message.into() }
    }

    pub fn value_error(message: impl Into<String>) -> Self {
        Self::new("ValueError", message)
    }

    pub fn type_error(message: impl Into<String>) -> Self {
        Self::new("TypeError", message)
    }

    /// `KeyError(key)`: Python renders the key with `repr`, so `str(exc)` is `'node-x'`.
    pub fn key_error(key: &str) -> Self {
        Self::new("KeyError", py_repr_str(key))
    }

    /// `repr(exc)`, e.g. `ValueError('lineage resolver must return a mapping, got list')`.
    pub fn repr(&self) -> String {
        format!("{}({})", self.kind, py_repr_str(&self.message))
    }

    /// `isinstance(exc, kind)` for the exception classes this package raises.
    pub fn is(&self, kind: &str) -> bool {
        self.kind == kind
    }
}

impl fmt::Display for CoordinationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CoordinationError {}

pub type CoordinationResult<T> = Result<T, CoordinationError>;

/// `to_jsonable(value)`: the JSON-compatible structure of a contract.
pub trait ToJsonable {
    fn to_jsonable(&self) -> Value;
}

impl ToJsonable for Value {
    fn to_jsonable(&self) -> Value {
        self.clone()
    }
}

impl ToJsonable for String {
    fn to_jsonable(&self) -> Value {
        Value::String(self.clone())
    }
}

impl ToJsonable for str {
    fn to_jsonable(&self) -> Value {
        Value::String(self.to_string())
    }
}

impl ToJsonable for bool {
    fn to_jsonable(&self) -> Value {
        Value::Bool(*self)
    }
}

impl ToJsonable for i64 {
    fn to_jsonable(&self) -> Value {
        json!(*self)
    }
}

impl ToJsonable for usize {
    fn to_jsonable(&self) -> Value {
        json!(*self)
    }
}

impl ToJsonable for f64 {
    fn to_jsonable(&self) -> Value {
        fnum(*self)
    }
}

impl<T: ToJsonable> ToJsonable for Option<T> {
    fn to_jsonable(&self) -> Value {
        match self {
            Some(v) => v.to_jsonable(),
            None => Value::Null,
        }
    }
}

impl<T: ToJsonable> ToJsonable for [T] {
    fn to_jsonable(&self) -> Value {
        Value::Array(self.iter().map(ToJsonable::to_jsonable).collect())
    }
}

impl<T: ToJsonable> ToJsonable for Vec<T> {
    fn to_jsonable(&self) -> Value {
        self.as_slice().to_jsonable()
    }
}

impl<T: ToJsonable + ?Sized> ToJsonable for &T {
    fn to_jsonable(&self) -> Value {
        (**self).to_jsonable()
    }
}

/// A list of identifiers as a JSON array.
pub fn str_list(items: &[String]) -> Value {
    Value::Array(items.iter().map(|s| Value::String(s.clone())).collect())
}

/// A dataclass as `to_jsonable` returns it: its own fields in declaration order, every value below them with
/// sorted keys. (Python's `asdict` turned nested dataclasses into plain dicts first, and `to_jsonable` sorts the
/// keys of every plain dict it meets.)
pub(crate) fn fields(pairs: Vec<(&str, Value)>) -> Value {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert(k.to_string(), sort_keys_deep(v));
    }
    Value::Object(m)
}

/// `to_jsonable(dict)`: keys sorted, recursively.
pub fn sort_keys_deep(v: Value) -> Value {
    match v {
        Value::Object(o) => {
            let mut entries: Vec<(String, Value)> = o.into_iter().collect();
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            Value::Object(entries.into_iter().map(|(k, v)| (k, sort_keys_deep(v))).collect())
        }
        Value::Array(a) => Value::Array(a.into_iter().map(sort_keys_deep).collect()),
        other => other,
    }
}

/// `_require(value, name)`: a non-empty string after `strip()`.
fn require(value: &str, name: &str) -> CoordinationResult<()> {
    if py_strip(value).is_empty() {
        return Err(CoordinationError::value_error(format!("{name} must be a non-empty string")));
    }
    Ok(())
}

/// `_require_iso(value, name)`: `None`, or a string `datetime.fromisoformat` accepts once `Z` means UTC.
fn require_iso(value: Option<&str>, name: &str) -> CoordinationResult<()> {
    let Some(value) = value else { return Ok(()) };
    require(value, name)?;
    if PyDateTime::fromisoformat(&value.replace('Z', "+00:00")).is_none() {
        return Err(CoordinationError::value_error(format!("{name} must be an ISO-8601 timestamp")));
    }
    Ok(())
}

macro_rules! str_enum {
    ($(#[$doc:meta])* $name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        $(#[$doc])*
        #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            /// Every member, in definition order (Python's enum iteration order).
            pub const ALL: &'static [$name] = &[$($name::$variant),+];

            /// `.value`
            pub fn value(self) -> &'static str {
                match self {
                    $($name::$variant => $value),+
                }
            }

            /// `Enum(value)`
            pub fn from_value(value: &str) -> Option<$name> {
                Self::ALL.iter().copied().find(|v| v.value() == value)
            }
        }

        impl ToJsonable for $name {
            fn to_jsonable(&self) -> Value {
                Value::String(self.value().to_string())
            }
        }
    };
}

str_enum!(Availability {
    Available => "available",
    Degraded => "degraded",
    Unavailable => "unavailable",
});

str_enum!(PolicyStatus {
    Allowed => "allowed",
    Redacted => "redacted",
    Denied => "denied",
});

str_enum!(LearningDisposition {
    Accepted => "accepted",
    Rejected => "rejected",
    Tentative => "tentative",
});

str_enum!(InterventionTarget {
    Node => "node",
    Memory => "memory",
    Source => "source",
    LineageRoot => "lineage_root",
    Edge => "edge",
    AuthorizationScope => "authorization_scope",
    Route => "route",
    Evidence => "evidence",
});

str_enum!(TraceEventType {
    QueryReceived => "query_received",
    RouteSelected => "route_selected",
    RetrievalSelected => "retrieval_selected",
    PolicyBlock => "policy_block",
    EvidenceExported => "evidence_exported",
    ClaimSynthesized => "claim_synthesized",
    VerificationRequested => "verification_requested",
    VerificationResult => "verification_result",
    FragilityScored => "fragility_scored",
    FailureInjected => "failure_injected",
    CapabilityEvaluated => "capability_evaluated",
    RepairRequested => "repair_requested",
    RepairCompleted => "repair_completed",
    LearningProposed => "learning_proposed",
    MemoryUpdated => "memory_updated",
});

/// Owned copies of string literals, for building identifier tuples.
pub fn strs(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

#[derive(Clone, Debug, PartialEq)]
pub struct AuthorizationContext {
    pub scopes: Vec<String>,
    pub allowed_node_ids: Vec<String>,
    pub allow_redacted: bool,
}

impl AuthorizationContext {
    pub fn new(scopes: Vec<String>, allowed_node_ids: Vec<String>, allow_redacted: bool) -> CoordinationResult<Self> {
        let context = AuthorizationContext { scopes, allowed_node_ids, allow_redacted };
        context.validate()?;
        Ok(context)
    }

    /// `AuthorizationContext(scopes=...)` with the other fields at their defaults.
    pub fn with_scopes(scopes: &[&str]) -> CoordinationResult<Self> {
        Self::new(strs(scopes), Vec::new(), false)
    }

    pub fn validate(&self) -> CoordinationResult<()> {
        if self.scopes.is_empty() {
            return Err(CoordinationError::value_error("authorization scopes must not be empty"));
        }
        Ok(())
    }

    pub fn permits(&self, node_id: &str, scope: &str) -> bool {
        let node_allowed = self.allowed_node_ids.is_empty() || self.allowed_node_ids.iter().any(|n| n == node_id);
        node_allowed && self.scopes.iter().any(|s| s == scope)
    }
}

impl ToJsonable for AuthorizationContext {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("scopes", str_list(&self.scopes)),
            ("allowed_node_ids", str_list(&self.allowed_node_ids)),
            ("allow_redacted", Value::Bool(self.allow_redacted)),
        ])
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct QueryBudget {
    pub max_nodes: i64,
    pub max_claims: i64,
    pub max_verifications: i64,
}

impl Default for QueryBudget {
    fn default() -> Self {
        QueryBudget { max_nodes: 3, max_claims: 8, max_verifications: 2 }
    }
}

impl QueryBudget {
    pub fn new(max_nodes: i64, max_claims: i64, max_verifications: i64) -> CoordinationResult<Self> {
        let budget = QueryBudget { max_nodes, max_claims, max_verifications };
        budget.validate()?;
        Ok(budget)
    }

    pub fn validate(&self) -> CoordinationResult<()> {
        if self.max_nodes.min(self.max_claims).min(self.max_verifications) < 0 {
            return Err(CoordinationError::value_error("query budget values must be non-negative"));
        }
        Ok(())
    }
}

impl ToJsonable for QueryBudget {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("max_nodes", json!(self.max_nodes)),
            ("max_claims", json!(self.max_claims)),
            ("max_verifications", json!(self.max_verifications)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct QueryRequest {
    pub query_id: String,
    pub content: String,
    pub requester_id: String,
    pub issued_at: String,
    pub authorization: AuthorizationContext,
    pub requested_capability: Option<String>,
    pub valid_at: Option<String>,
    pub budget: QueryBudget,
}

impl QueryRequest {
    pub fn validate(&self) -> CoordinationResult<()> {
        require(&self.query_id, "query_id")?;
        require(&self.content, "content")?;
        require(&self.requester_id, "requester_id")?;
        require_iso(Some(&self.issued_at), "issued_at")?;
        require_iso(self.valid_at.as_deref(), "valid_at")?;
        Ok(())
    }

    /// Construct and validate, like the dataclass constructor.
    pub fn validated(self) -> CoordinationResult<Self> {
        self.validate()?;
        Ok(self)
    }
}

impl ToJsonable for QueryRequest {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("query_id", json!(self.query_id)),
            ("content", json!(self.content)),
            ("requester_id", json!(self.requester_id)),
            ("issued_at", json!(self.issued_at)),
            ("authorization", self.authorization.to_jsonable()),
            ("requested_capability", json!(self.requested_capability)),
            ("valid_at", json!(self.valid_at)),
            ("budget", self.budget.to_jsonable()),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct CapabilityDescriptor {
    pub capability_id: String,
    pub node_id: String,
    pub description: String,
    pub query_types: Vec<String>,
    pub policy_scope: Vec<String>,
    pub availability: Availability,
}

impl CapabilityDescriptor {
    pub fn validate(&self) -> CoordinationResult<()> {
        require(&self.capability_id, "capability_id")?;
        require(&self.node_id, "node_id")?;
        require(&self.description, "description")?;
        Ok(())
    }

    pub fn validated(self) -> CoordinationResult<Self> {
        self.validate()?;
        Ok(self)
    }
}

impl ToJsonable for CapabilityDescriptor {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("capability_id", json!(self.capability_id)),
            ("node_id", json!(self.node_id)),
            ("description", json!(self.description)),
            ("query_types", str_list(&self.query_types)),
            ("policy_scope", str_list(&self.policy_scope)),
            ("availability", self.availability.to_jsonable()),
        ])
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ValidTime {
    pub from_time: Option<String>,
    pub to_time: Option<String>,
}

impl ValidTime {
    pub fn validate(&self) -> CoordinationResult<()> {
        require_iso(self.from_time.as_deref(), "valid_time.from")?;
        require_iso(self.to_time.as_deref(), "valid_time.to")?;
        Ok(())
    }
}

impl ToJsonable for ValidTime {
    fn to_jsonable(&self) -> Value {
        fields(vec![("from_time", json!(self.from_time)), ("to_time", json!(self.to_time))])
    }
}

/// One exported claim. `content` is whatever the producer exported; after the adapters it is always a flat
/// mapping of JSON scalars, so it is held as JSON.
#[derive(Clone, Debug, PartialEq)]
pub struct ClaimEnvelope {
    pub claim_id: String,
    pub query_id: String,
    pub producer_node_id: String,
    pub content: Value,
    pub confidence: f64,
    pub evidence_refs: Vec<String>,
    pub source_ids: Vec<String>,
    pub parent_memory_ids: Vec<String>,
    pub lineage_root_ids: Vec<String>,
    pub failure_domains: Vec<String>,
    pub policy_status: PolicyStatus,
    pub created_at: String,
    pub valid_time: Option<ValidTime>,
    pub derivation_operator: Option<String>,
}

impl ClaimEnvelope {
    pub fn validate(&self) -> CoordinationResult<()> {
        require(&self.claim_id, "claim_id")?;
        require(&self.query_id, "query_id")?;
        require(&self.producer_node_id, "producer_node_id")?;
        // `not 0.0 <= confidence <= 1.0` is also true for NaN
        if !(0.0 <= self.confidence && self.confidence <= 1.0) {
            return Err(CoordinationError::value_error("claim confidence must be between 0 and 1"));
        }
        if self.policy_status == PolicyStatus::Denied {
            return Err(CoordinationError::value_error("denied evidence must not be exported as a claim"));
        }
        require_iso(Some(&self.created_at), "created_at")?;
        Ok(())
    }

    pub fn validated(self) -> CoordinationResult<Self> {
        self.validate()?;
        Ok(self)
    }

    /// `claim.content.get(key)` on a mapping content (`None` for a missing key or a non-mapping content).
    pub fn content_get(&self, key: &str) -> Option<&Value> {
        self.content.as_object().and_then(|m| m.get(key))
    }

    /// `claim.content.get("slot")` compared to a slot name.
    pub fn slot_is(&self, slot: &str) -> bool {
        matches!(self.content_get("slot"), Some(Value::String(s)) if s == slot)
    }
}

impl ToJsonable for ClaimEnvelope {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("claim_id", json!(self.claim_id)),
            ("query_id", json!(self.query_id)),
            ("producer_node_id", json!(self.producer_node_id)),
            ("content", self.content.clone()),
            ("confidence", fnum(self.confidence)),
            ("evidence_refs", str_list(&self.evidence_refs)),
            ("source_ids", str_list(&self.source_ids)),
            ("parent_memory_ids", str_list(&self.parent_memory_ids)),
            ("lineage_root_ids", str_list(&self.lineage_root_ids)),
            ("failure_domains", str_list(&self.failure_domains)),
            ("policy_status", self.policy_status.to_jsonable()),
            ("created_at", json!(self.created_at)),
            ("valid_time", self.valid_time.to_jsonable()),
            ("derivation_operator", json!(self.derivation_operator)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct RetrievalTrace {
    pub trace_id: String,
    pub query_id: String,
    pub node_id: String,
    pub memory_ids: Vec<String>,
    pub source_ids: Vec<String>,
    pub parent_memory_ids: Vec<String>,
    pub lineage_root_ids: Vec<String>,
    pub edge_path: Vec<String>,
    pub retrieval_operator: String,
    pub policy_status: PolicyStatus,
    pub started_at: String,
    pub completed_at: String,
}

impl RetrievalTrace {
    pub fn validate(&self) -> CoordinationResult<()> {
        require(&self.trace_id, "trace_id")?;
        require(&self.query_id, "query_id")?;
        require(&self.node_id, "node_id")?;
        require(&self.retrieval_operator, "retrieval_operator")?;
        require_iso(Some(&self.started_at), "started_at")?;
        require_iso(Some(&self.completed_at), "completed_at")?;
        let exposes_structure = !self.memory_ids.is_empty()
            || !self.source_ids.is_empty()
            || !self.parent_memory_ids.is_empty()
            || !self.lineage_root_ids.is_empty()
            || !self.edge_path.is_empty();
        if self.policy_status != PolicyStatus::Allowed && exposes_structure {
            return Err(CoordinationError::value_error(
                "blocked or redacted traces must not expose structural identifiers",
            ));
        }
        Ok(())
    }

    pub fn validated(self) -> CoordinationResult<Self> {
        self.validate()?;
        Ok(self)
    }
}

impl ToJsonable for RetrievalTrace {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("trace_id", json!(self.trace_id)),
            ("query_id", json!(self.query_id)),
            ("node_id", json!(self.node_id)),
            ("memory_ids", str_list(&self.memory_ids)),
            ("source_ids", str_list(&self.source_ids)),
            ("parent_memory_ids", str_list(&self.parent_memory_ids)),
            ("lineage_root_ids", str_list(&self.lineage_root_ids)),
            ("edge_path", str_list(&self.edge_path)),
            ("retrieval_operator", json!(self.retrieval_operator)),
            ("policy_status", self.policy_status.to_jsonable()),
            ("started_at", json!(self.started_at)),
            ("completed_at", json!(self.completed_at)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct EvidenceExport {
    pub claims: Vec<ClaimEnvelope>,
    pub trace: RetrievalTrace,
}

impl ToJsonable for EvidenceExport {
    fn to_jsonable(&self) -> Value {
        fields(vec![("claims", self.claims.to_jsonable()), ("trace", self.trace.to_jsonable())])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VerificationRequest {
    pub verification_id: String,
    pub query: QueryRequest,
    pub required_slots: Vec<String>,
    pub excluded_lineage_roots: Vec<String>,
    pub excluded_failure_domains: Vec<String>,
}

impl ToJsonable for VerificationRequest {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("verification_id", json!(self.verification_id)),
            ("query", self.query.to_jsonable()),
            ("required_slots", str_list(&self.required_slots)),
            ("excluded_lineage_roots", str_list(&self.excluded_lineage_roots)),
            ("excluded_failure_domains", str_list(&self.excluded_failure_domains)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VerificationResult {
    pub verification_id: String,
    pub node_id: String,
    pub valid: bool,
    pub lineage_root_ids: Vec<String>,
    pub failure_domains: Vec<String>,
    pub supported_slots: Vec<String>,
    pub reason: String,
}

impl ToJsonable for VerificationResult {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("verification_id", json!(self.verification_id)),
            ("node_id", json!(self.node_id)),
            ("valid", json!(self.valid)),
            ("lineage_root_ids", str_list(&self.lineage_root_ids)),
            ("failure_domains", str_list(&self.failure_domains)),
            ("supported_slots", str_list(&self.supported_slots)),
            ("reason", json!(self.reason)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct LearningSignal {
    pub signal_id: String,
    pub sender_node_id: String,
    pub receiver_node_id: String,
    pub proposed_content: Value,
    pub evidence_refs: Vec<String>,
}

impl ToJsonable for LearningSignal {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("signal_id", json!(self.signal_id)),
            ("sender_node_id", json!(self.sender_node_id)),
            ("receiver_node_id", json!(self.receiver_node_id)),
            ("proposed_content", self.proposed_content.clone()),
            ("evidence_refs", str_list(&self.evidence_refs)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct LearningDecision {
    pub signal_id: String,
    pub receiver_node_id: String,
    pub disposition: LearningDisposition,
    pub reason: String,
    pub committed_memory_id: Option<String>,
}

impl ToJsonable for LearningDecision {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("signal_id", json!(self.signal_id)),
            ("receiver_node_id", json!(self.receiver_node_id)),
            ("disposition", self.disposition.to_jsonable()),
            ("reason", json!(self.reason)),
            ("committed_memory_id", json!(self.committed_memory_id)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TraceEvent {
    pub schema_version: String,
    pub event_id: String,
    pub run_id: String,
    pub trace_id: String,
    pub event_type: TraceEventType,
    pub sequence: i64,
    pub occurred_at: String,
    /// Already `to_jsonable`'d (a key-sorted mapping) when the event is emitted.
    pub details: Value,
}

impl TraceEvent {
    /// `event.details[key]`
    pub fn detail(&self, key: &str) -> Option<&Value> {
        self.details.get(key)
    }
}

impl ToJsonable for TraceEvent {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("schema_version", json!(self.schema_version)),
            ("event_id", json!(self.event_id)),
            ("run_id", json!(self.run_id)),
            ("trace_id", json!(self.trace_id)),
            ("event_type", self.event_type.to_jsonable()),
            ("sequence", json!(self.sequence)),
            ("occurred_at", json!(self.occurred_at)),
            ("details", self.details.clone()),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct InterventionRecord {
    pub run_id: String,
    pub intervention_id: String,
    pub target_type: InterventionTarget,
    pub target_id: String,
    pub started_at: String,
    pub reason: String,
    pub random_seed: i64,
    pub expected_effect: Option<String>,
    pub ended_at: Option<String>,
}

impl ToJsonable for InterventionRecord {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("run_id", json!(self.run_id)),
            ("intervention_id", json!(self.intervention_id)),
            ("target_type", self.target_type.to_jsonable()),
            ("target_id", json!(self.target_id)),
            ("started_at", json!(self.started_at)),
            ("reason", json!(self.reason)),
            ("random_seed", json!(self.random_seed)),
            ("expected_effect", json!(self.expected_effect)),
            ("ended_at", json!(self.ended_at)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ExperimentManifest {
    pub run_id: String,
    pub seed: i64,
    pub started_at: String,
    pub fixture_version: String,
    pub strategy: String,
    pub budget: QueryBudget,
    pub interventions: Vec<InterventionRecord>,
    pub code_version: Option<String>,
}

impl ToJsonable for ExperimentManifest {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("run_id", json!(self.run_id)),
            ("seed", json!(self.seed)),
            ("started_at", json!(self.started_at)),
            ("fixture_version", json!(self.fixture_version)),
            ("strategy", json!(self.strategy)),
            ("budget", self.budget.to_jsonable()),
            ("interventions", self.interventions.to_jsonable()),
            ("code_version", json!(self.code_version)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct FragilityMetrics {
    pub apparent_replica_count: usize,
    pub unique_lineage_root_count: usize,
    pub unique_failure_domain_count: usize,
    pub reconstruction_coalition_count: usize,
    pub minimal_support_coalition_size: usize,
    pub minimal_failure_domain_cut: usize,
    pub current_task_success: bool,
    pub current_confidence: f64,
    pub valid_support_coverage: f64,
    pub confidence_valid_support_gap: f64,
}

impl ToJsonable for FragilityMetrics {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("apparent_replica_count", json!(self.apparent_replica_count)),
            ("unique_lineage_root_count", json!(self.unique_lineage_root_count)),
            ("unique_failure_domain_count", json!(self.unique_failure_domain_count)),
            ("reconstruction_coalition_count", json!(self.reconstruction_coalition_count)),
            ("minimal_support_coalition_size", json!(self.minimal_support_coalition_size)),
            ("minimal_failure_domain_cut", json!(self.minimal_failure_domain_cut)),
            ("current_task_success", json!(self.current_task_success)),
            ("current_confidence", fnum(self.current_confidence)),
            ("valid_support_coverage", fnum(self.valid_support_coverage)),
            ("confidence_valid_support_gap", fnum(self.confidence_valid_support_gap)),
        ])
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SynthesisResult {
    pub success: bool,
    pub answer: Option<String>,
    pub confidence: f64,
    pub selected_claim_ids: Vec<String>,
    pub selected_node_ids: Vec<String>,
    pub covered_slots: Vec<String>,
}

impl ToJsonable for SynthesisResult {
    fn to_jsonable(&self) -> Value {
        fields(vec![
            ("success", json!(self.success)),
            ("answer", json!(self.answer)),
            ("confidence", fnum(self.confidence)),
            ("selected_claim_ids", str_list(&self.selected_claim_ids)),
            ("selected_node_ids", str_list(&self.selected_node_ids)),
            ("covered_slots", str_list(&self.covered_slots)),
        ])
    }
}
