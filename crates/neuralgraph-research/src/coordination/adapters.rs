//! Private local-memory adapters for the coordination boundary (port of `coordination/adapters.py`).
//!
//! `MockMemoryNodeAdapter` serves the deterministic fixtures. `NeuralGraphMemoryAdapter` faces owner-supplied
//! code: a retrieval callback returning arbitrary Python objects, an optional lineage resolver returning "any
//! mapping", and an optional claim projection returning "anything". The Python validated all of that at run time
//! (`isinstance` checks, `str.__str__` re-exports that strip `str` subclasses, finiteness checks), and its tests
//! attack the boundary with hostile subclasses. To keep that boundary — and its error messages — observable in
//! Rust, owner-side data is modelled dynamically:
//!
//! * [`LocalValue`] is a Python value as the owner hands it over: builtins, builtin *subclasses* carrying the
//!   overrides and attributes Python would have let them smuggle ([`PySubclass`]), dict subclasses whose
//!   `items()` view disagrees with their storage, and arbitrary objects (known only by type name).
//! * [`LocalNode`] is a retrieved object read with `getattr`, implemented for real `NeuralNode`s
//!   (`RefCell<NeuralNode>`, so a storage `NodeRef` is a `Rc<dyn LocalNode>` as is).
//!
//! What crosses the boundary is always typed (`ClaimEnvelope` with JSON content, identifier `String`s), so the
//! re-export through base-class methods becomes the conversion from `LocalValue` to those types.
//!
//! Python's coroutines awaited nothing beyond the owner callbacks, which are synchronous here.

use crate::coordination::contracts::{
    AuthorizationContext, Availability, CapabilityDescriptor, ClaimEnvelope, CoordinationError, CoordinationResult,
    EvidenceExport, LearningDecision, LearningDisposition, LearningSignal, PolicyStatus, QueryRequest, RetrievalTrace,
    VerificationRequest, VerificationResult,
};
use crate::retrieval::data_types::NeuralNode;
use neuralgraph_memory::pycompat::{py_float_repr, py_repr_str, py_strip};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::any::Any;
use std::cell::RefCell;
use std::collections::{BTreeSet, HashSet};
use std::rc::Rc;

/// `clock.now`-style callable every adapter stamps its traces with.
pub type Clock = Rc<dyn Fn() -> String>;

/// The keyword arguments of `FailureView.blocked_reason`.
#[derive(Clone, Copy, Debug)]
pub struct BlockedReasonQuery<'a> {
    pub node_id: &'a str,
    pub memory_id: &'a str,
    pub source_ids: &'a [String],
    pub lineage_root_ids: &'a [String],
    pub edge_path: &'a [String],
    pub required_scope: &'a str,
    pub valid_until: Option<&'a str>,
    pub valid_at: Option<&'a str>,
}

/// What an adapter may ask about active interventions (the `FailureView` protocol).
pub trait FailureView {
    /// Why this record is unavailable, or `None`. Errors are what Python raised parsing malformed timestamps.
    fn blocked_reason(&self, query: &BlockedReasonQuery<'_>) -> CoordinationResult<Option<String>>;
}

/// The `MemoryNodeAdapter` protocol.
pub trait MemoryNodeAdapter {
    fn node_id(&self) -> &str;
    fn describe_capabilities(&self, context: &AuthorizationContext) -> CoordinationResult<Vec<CapabilityDescriptor>>;
    fn query(&self, request: &QueryRequest) -> CoordinationResult<Vec<EvidenceExport>>;
    fn verify(&self, request: &VerificationRequest) -> CoordinationResult<VerificationResult>;
    fn propose_learning(&self, signal: &LearningSignal) -> CoordinationResult<LearningDecision>;
}

/// Internal mock record. It is never returned across the adapter boundary.
#[derive(Clone, Debug, PartialEq)]
pub struct PrivateMemoryRecord {
    pub memory_id: String,
    pub capability_id: String,
    pub slot: String,
    pub value: String,
    pub confidence: f64,
    pub source_ids: Vec<String>,
    pub parent_memory_ids: Vec<String>,
    pub lineage_root_ids: Vec<String>,
    pub failure_domains: Vec<String>,
    pub edge_path: Vec<String>,
    pub required_scope: String,
    pub retrieval_operator: String,
    pub policy_mode: PolicyStatus,
    pub valid_until: Option<String>,
}

impl PrivateMemoryRecord {
    /// The record with `retrieval_operator="mock_exact_lookup"`, `policy_mode=ALLOWED`, `valid_until=None`.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        memory_id: &str,
        capability_id: &str,
        slot: &str,
        value: &str,
        confidence: f64,
        source_ids: Vec<String>,
        parent_memory_ids: Vec<String>,
        lineage_root_ids: Vec<String>,
        failure_domains: Vec<String>,
        edge_path: Vec<String>,
        required_scope: &str,
    ) -> Self {
        PrivateMemoryRecord {
            memory_id: memory_id.to_string(),
            capability_id: capability_id.to_string(),
            slot: slot.to_string(),
            value: value.to_string(),
            confidence,
            source_ids,
            parent_memory_ids,
            lineage_root_ids,
            failure_domains,
            edge_path,
            required_scope: required_scope.to_string(),
            retrieval_operator: "mock_exact_lookup".to_string(),
            policy_mode: PolicyStatus::Allowed,
            valid_until: None,
        }
    }
}

/// `kind + "_" + sha256("|".join(parts)).hexdigest()[:20]` — the opaque identifiers every adapter exports.
pub fn opaque_id(kind: &str, parts: &[&str]) -> String {
    let digest = Sha256::digest(parts.join("|").as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("{kind}_{}", &hex[..20])
}

fn sorted_unique<'a>(items: impl Iterator<Item = &'a String>) -> Vec<String> {
    items.cloned().collect::<BTreeSet<_>>().into_iter().collect()
}

/// Deterministic adapter over one node's private immutable memory records.
pub struct MockMemoryNodeAdapter {
    node_id: String,
    records: Vec<PrivateMemoryRecord>,
    failure_view: Rc<dyn FailureView>,
    clock: Clock,
    learning_policy: LearningDisposition,
}

impl MockMemoryNodeAdapter {
    pub fn new(
        node_id: &str,
        records: Vec<PrivateMemoryRecord>,
        failure_view: Rc<dyn FailureView>,
        clock: Clock,
        learning_policy: LearningDisposition,
    ) -> Self {
        MockMemoryNodeAdapter { node_id: node_id.to_string(), records, failure_view, clock, learning_policy }
    }

    /// `MockMemoryNodeAdapter._opaque_id`
    pub fn opaque_id(kind: &str, parts: &[&str]) -> String {
        opaque_id(kind, parts)
    }

    fn blocked(&self, record: &PrivateMemoryRecord, valid_at: Option<&str>) -> CoordinationResult<Option<String>> {
        self.failure_view.blocked_reason(&BlockedReasonQuery {
            node_id: &self.node_id,
            memory_id: &record.memory_id,
            source_ids: &record.source_ids,
            lineage_root_ids: &record.lineage_root_ids,
            edge_path: &record.edge_path,
            required_scope: &record.required_scope,
            valid_until: record.valid_until.as_deref(),
            valid_at,
        })
    }
}

impl MemoryNodeAdapter for MockMemoryNodeAdapter {
    fn node_id(&self) -> &str {
        &self.node_id
    }

    fn describe_capabilities(&self, context: &AuthorizationContext) -> CoordinationResult<Vec<CapabilityDescriptor>> {
        let mut descriptors = Vec::new();
        for capability_id in sorted_unique(self.records.iter().map(|r| &r.capability_id)) {
            let scopes = sorted_unique(
                self.records.iter().filter(|r| r.capability_id == capability_id).map(|r| &r.required_scope),
            );
            let availability = if scopes.iter().any(|s| context.permits(&self.node_id, s)) {
                Availability::Available
            } else {
                Availability::Unavailable
            };
            descriptors.push(
                CapabilityDescriptor {
                    description: format!("Private evidence for {capability_id}"),
                    capability_id,
                    node_id: self.node_id.clone(),
                    query_types: vec!["composition".to_string()],
                    policy_scope: scopes,
                    availability,
                }
                .validated()?,
            );
        }
        Ok(descriptors)
    }

    fn query(&self, request: &QueryRequest) -> CoordinationResult<Vec<EvidenceExport>> {
        let mut exports = Vec::new();
        for (index, record) in self.records.iter().enumerate() {
            if request.requested_capability.as_deref() != Some(record.capability_id.as_str()) {
                continue;
            }
            let started = (self.clock)();
            let trace_id = opaque_id("trace", &[&request.query_id, &record.memory_id, &index.to_string()]);
            let authorized = request.authorization.permits(&self.node_id, &record.required_scope);
            let blocked = self.blocked(record, request.valid_at.as_deref())?;
            let mut policy = record.policy_mode;
            let redaction_refused = policy == PolicyStatus::Redacted && !request.authorization.allow_redacted;
            if !authorized || blocked.as_deref().is_some_and(|b| !b.is_empty()) || redaction_refused {
                policy = PolicyStatus::Denied;
            }

            if policy == PolicyStatus::Denied {
                exports.push(EvidenceExport {
                    claims: Vec::new(),
                    trace: RetrievalTrace {
                        trace_id,
                        query_id: request.query_id.clone(),
                        node_id: self.node_id.clone(),
                        memory_ids: Vec::new(),
                        source_ids: Vec::new(),
                        parent_memory_ids: Vec::new(),
                        lineage_root_ids: Vec::new(),
                        edge_path: Vec::new(),
                        retrieval_operator: "policy_filter".to_string(),
                        policy_status: PolicyStatus::Denied,
                        started_at: started,
                        completed_at: (self.clock)(),
                    }
                    .validated()?,
                });
                continue;
            }

            // A redacted export keeps the slot but replaces the value and every structural identifier.
            let redacted = policy == PolicyStatus::Redacted;
            let mut content = Map::new();
            content.insert("slot".into(), Value::String(record.slot.clone()));
            content.insert(
                "value".into(),
                Value::String(if redacted { "[REDACTED]".to_string() } else { record.value.clone() }),
            );
            let structural = |ids: &Vec<String>| if redacted { Vec::new() } else { ids.clone() };

            let claim = ClaimEnvelope {
                claim_id: opaque_id("claim", &[&request.query_id, &self.node_id, &record.memory_id]),
                query_id: request.query_id.clone(),
                producer_node_id: self.node_id.clone(),
                content: Value::Object(content),
                confidence: record.confidence,
                evidence_refs: if redacted { Vec::new() } else { vec![opaque_id("evidence", &[&record.memory_id])] },
                source_ids: structural(&record.source_ids),
                parent_memory_ids: structural(&record.parent_memory_ids),
                lineage_root_ids: structural(&record.lineage_root_ids),
                failure_domains: structural(&record.failure_domains),
                policy_status: policy,
                derivation_operator: Some("local_policy_filtered_export".to_string()),
                created_at: (self.clock)(),
                valid_time: None,
            }
            .validated()?;
            let trace = RetrievalTrace {
                trace_id,
                query_id: request.query_id.clone(),
                node_id: self.node_id.clone(),
                memory_ids: if redacted { Vec::new() } else { vec![record.memory_id.clone()] },
                source_ids: structural(&record.source_ids),
                parent_memory_ids: structural(&record.parent_memory_ids),
                lineage_root_ids: structural(&record.lineage_root_ids),
                edge_path: structural(&record.edge_path),
                retrieval_operator: record.retrieval_operator.clone(),
                policy_status: policy,
                started_at: started,
                completed_at: (self.clock)(),
            }
            .validated()?;
            exports.push(EvidenceExport { claims: vec![claim], trace });
        }
        Ok(exports)
    }

    fn verify(&self, request: &VerificationRequest) -> CoordinationResult<VerificationResult> {
        let matching: Vec<&PrivateMemoryRecord> = self
            .records
            .iter()
            .filter(|r| {
                request.query.requested_capability.as_deref() == Some(r.capability_id.as_str())
                    && request.required_slots.contains(&r.slot)
            })
            .collect();
        let mut valid_records: Vec<&PrivateMemoryRecord> = Vec::new();
        let mut reasons: Vec<String> = Vec::new();
        for record in &matching {
            let blocked = self.blocked(record, request.query.valid_at.as_deref())?;
            let correlated = record.lineage_root_ids.iter().any(|r| request.excluded_lineage_roots.contains(r))
                || record.failure_domains.iter().any(|d| request.excluded_failure_domains.contains(d));
            let authorized = request.query.authorization.permits(&self.node_id, &record.required_scope);
            match blocked {
                _ if correlated => reasons.push("correlated support".to_string()),
                Some(reason) if !reason.is_empty() => reasons.push(reason),
                _ if !authorized => reasons.push("policy denied".to_string()),
                _ => valid_records.push(record),
            }
        }
        let valid = !valid_records.is_empty();
        Ok(VerificationResult {
            verification_id: request.verification_id.clone(),
            node_id: self.node_id.clone(),
            valid,
            lineage_root_ids: sorted_unique(matching.iter().flat_map(|r| r.lineage_root_ids.iter())),
            failure_domains: sorted_unique(matching.iter().flat_map(|r| r.failure_domains.iter())),
            supported_slots: sorted_unique(valid_records.iter().map(|r| &r.slot)),
            reason: if valid {
                "independent support available".to_string()
            } else {
                reasons.first().cloned().unwrap_or_else(|| "no matching support".to_string())
            },
        })
    }

    fn propose_learning(&self, signal: &LearningSignal) -> CoordinationResult<LearningDecision> {
        if signal.receiver_node_id != self.node_id {
            return Ok(LearningDecision {
                signal_id: signal.signal_id.clone(),
                receiver_node_id: self.node_id.clone(),
                disposition: LearningDisposition::Rejected,
                reason: "receiver mismatch".to_string(),
                committed_memory_id: None,
            });
        }
        // Acceptance is only a receiver decision. This mock intentionally does not commit a record; a separate
        // receiver-owned callback would perform that.
        Ok(LearningDecision {
            signal_id: signal.signal_id.clone(),
            receiver_node_id: self.node_id.clone(),
            disposition: self.learning_policy,
            reason: "receiver policy decision".to_string(),
            committed_memory_id: None,
        })
    }
}

// =================================================================================================================
// Owner-side values
// =================================================================================================================

/// The Python-visible behaviour of an instance of a builtin subclass (`class HostileKey(str): ...`): its type
/// name, the dunder overrides it defines and the attributes it carries. The boundary must never let any of it
/// through: it reads the stored builtin value only.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PySubclass {
    pub type_name: String,
    /// `__repr__` override.
    pub repr: Option<String>,
    /// `__str__` override.
    pub str_override: Option<String>,
    /// `__float__` override.
    pub float_override: Option<f64>,
    /// Instance attributes (`self.smuggled = ...`).
    pub attributes: Vec<(String, String)>,
}

/// A mapping as the owner returned it. `items_view` models a dict subclass whose `items()` reports something other
/// than what it stores (`.get` still reads the storage).
#[derive(Clone, Debug, PartialEq)]
pub struct LocalDict {
    /// `type(d).__name__` (`dict` unless it is a subclass).
    pub type_name: String,
    pub entries: Vec<(LocalValue, LocalValue)>,
    pub items_view: Option<Vec<(LocalValue, LocalValue)>>,
}

impl LocalDict {
    pub fn new(entries: Vec<(LocalValue, LocalValue)>) -> Self {
        LocalDict { type_name: "dict".to_string(), entries, items_view: None }
    }

    /// `d.items()`
    pub fn items(&self) -> &[(LocalValue, LocalValue)] {
        self.items_view.as_deref().unwrap_or(&self.entries)
    }

    /// `d.get(key)` for a string key (a `str` subclass key with the same value is the same key).
    pub fn get(&self, key: &str) -> Option<&LocalValue> {
        self.entries.iter().rev().find(|(k, _)| k.as_str() == Some(key)).map(|(_, v)| v)
    }
}

/// Any other object, known by its type name (and the attributes `getattr` may read).
#[derive(Clone, Debug, PartialEq)]
pub struct LocalObject {
    pub type_name: String,
    pub attributes: Vec<(String, LocalValue)>,
}

/// A Python value handed over by owner code.
#[derive(Clone, Debug, PartialEq)]
pub enum LocalValue {
    None,
    Bool(bool),
    Int(i64, Option<Box<PySubclass>>),
    Float(f64, Option<Box<PySubclass>>),
    Str(String, Option<Box<PySubclass>>),
    Bytes(Vec<u8>),
    ByteArray(Vec<u8>),
    List(Vec<LocalValue>),
    Tuple(Vec<LocalValue>),
    Set(Vec<LocalValue>),
    Dict(LocalDict),
    Object(LocalObject),
}

impl LocalValue {
    pub fn str(s: &str) -> Self {
        LocalValue::Str(s.to_string(), None)
    }

    pub fn int(i: i64) -> Self {
        LocalValue::Int(i, None)
    }

    pub fn float(f: f64) -> Self {
        LocalValue::Float(f, None)
    }

    /// A tuple of plain strings.
    pub fn tuple_of(items: &[&str]) -> Self {
        LocalValue::Tuple(items.iter().map(|s| LocalValue::str(s)).collect())
    }

    /// A list of plain strings.
    pub fn list_of(items: &[&str]) -> Self {
        LocalValue::List(items.iter().map(|s| LocalValue::str(s)).collect())
    }

    /// A plain dict with string keys, in insertion order.
    pub fn dict(pairs: Vec<(&str, LocalValue)>) -> Self {
        LocalValue::Dict(LocalDict::new(pairs.into_iter().map(|(k, v)| (LocalValue::str(k), v)).collect()))
    }

    /// `{}`
    pub fn empty_dict() -> Self {
        LocalValue::Dict(LocalDict::new(Vec::new()))
    }

    /// An instance of a class that is not a builtin (`object()`, a dataclass, a node stub).
    pub fn object(type_name: &str) -> Self {
        LocalValue::Object(LocalObject { type_name: type_name.to_string(), attributes: Vec::new() })
    }

    /// The Python value `json.loads` would have produced.
    pub fn from_json(v: &Value) -> Self {
        match v {
            Value::Null => LocalValue::None,
            Value::Bool(b) => LocalValue::Bool(*b),
            Value::Number(n) => match n.as_i64() {
                Some(i) => LocalValue::int(i),
                None => LocalValue::float(n.as_f64().unwrap_or(f64::NAN)),
            },
            Value::String(s) => LocalValue::str(s),
            Value::Array(a) => LocalValue::List(a.iter().map(LocalValue::from_json).collect()),
            Value::Object(o) => LocalValue::Dict(LocalDict::new(
                o.iter().map(|(k, v)| (LocalValue::str(k), LocalValue::from_json(v))).collect(),
            )),
        }
    }

    /// `type(value).__name__`
    pub fn type_name(&self) -> String {
        let sub =
            |s: &Option<Box<PySubclass>>, base: &str| s.as_ref().map_or(base.to_string(), |s| s.type_name.clone());
        match self {
            LocalValue::None => "NoneType".into(),
            LocalValue::Bool(_) => "bool".into(),
            LocalValue::Int(_, s) => sub(s, "int"),
            LocalValue::Float(_, s) => sub(s, "float"),
            LocalValue::Str(_, s) => sub(s, "str"),
            LocalValue::Bytes(_) => "bytes".into(),
            LocalValue::ByteArray(_) => "bytearray".into(),
            LocalValue::List(_) => "list".into(),
            LocalValue::Tuple(_) => "tuple".into(),
            LocalValue::Set(_) => "set".into(),
            LocalValue::Dict(d) => d.type_name.clone(),
            LocalValue::Object(o) => o.type_name.clone(),
        }
    }

    /// The stored text of a `str` (or `str` subclass) — what `str.__str__(value)` returns.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            LocalValue::Str(s, _) => Some(s),
            _ => None,
        }
    }

    /// `str(value)`: honours a subclass's `__str__`, as Python's `str()` does.
    pub fn py_str(&self) -> String {
        match self {
            LocalValue::Str(s, sub) => sub.as_ref().and_then(|s| s.str_override.clone()).unwrap_or_else(|| s.clone()),
            LocalValue::Int(i, sub) => {
                sub.as_ref().and_then(|s| s.str_override.clone()).unwrap_or_else(|| i.to_string())
            }
            LocalValue::Float(f, sub) => {
                sub.as_ref().and_then(|s| s.str_override.clone()).unwrap_or_else(|| py_float_repr(*f))
            }
            other => other.py_repr(),
        }
    }

    /// `repr(value)` (objects have no deterministic repr in Python; the type name stands in for it).
    pub fn py_repr(&self) -> String {
        let join = |items: &[LocalValue]| items.iter().map(LocalValue::py_repr).collect::<Vec<_>>().join(", ");
        match self {
            LocalValue::None => "None".into(),
            LocalValue::Bool(b) => if *b { "True" } else { "False" }.into(),
            LocalValue::Int(i, sub) => sub.as_ref().and_then(|s| s.repr.clone()).unwrap_or_else(|| i.to_string()),
            LocalValue::Float(f, sub) => sub.as_ref().and_then(|s| s.repr.clone()).unwrap_or_else(|| py_float_repr(*f)),
            LocalValue::Str(s, sub) => sub.as_ref().and_then(|s| s.repr.clone()).unwrap_or_else(|| py_repr_str(s)),
            LocalValue::Bytes(b) => format!("b{}", py_repr_str(&String::from_utf8_lossy(b))),
            LocalValue::ByteArray(b) => format!("bytearray(b{})", py_repr_str(&String::from_utf8_lossy(b))),
            LocalValue::List(items) => format!("[{}]", join(items)),
            LocalValue::Tuple(items) if items.len() == 1 => format!("({},)", items[0].py_repr()),
            LocalValue::Tuple(items) => format!("({})", join(items)),
            LocalValue::Set(items) => format!("{{{}}}", join(items)),
            LocalValue::Dict(d) => format!(
                "{{{}}}",
                d.items()
                    .iter()
                    .map(|(k, v)| format!("{}: {}", k.py_repr(), v.py_repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            LocalValue::Object(o) => format!("<{} object>", o.type_name),
        }
    }
}

/// A locally retrieved object, read the way the Python read it: `type(node).__name__` and `getattr`.
pub trait LocalNode {
    fn type_name(&self) -> String;
    /// `getattr(node, name, None)` (`None` for a missing attribute).
    fn getattr(&self, name: &str) -> Option<LocalValue>;
    /// For owner callbacks that know the concrete type (a `RefCell<NeuralNode>` for stored nodes).
    fn as_any(&self) -> &dyn Any;
}

/// Shared handle to a retrieved object.
pub type LocalNodeRef = Rc<dyn LocalNode>;

fn strings_value(items: &[String]) -> LocalValue {
    LocalValue::List(items.iter().map(|s| LocalValue::str(s)).collect())
}

impl LocalNode for RefCell<NeuralNode> {
    fn type_name(&self) -> String {
        "NeuralNode".to_string()
    }

    fn getattr(&self, name: &str) -> Option<LocalValue> {
        let node = self.borrow();
        Some(match name {
            "node_id" => LocalValue::str(&node.node_id),
            "content" => LocalValue::str(&node.content),
            "session_key" => LocalValue::str(&node.session_key),
            "source_memory_ids" => strings_value(&node.source_memory_ids),
            "child_ids" => strings_value(&node.child_ids),
            "entity_ids" => strings_value(&node.entity_ids),
            "parent_id" => node.parent_id.as_deref().map_or(LocalValue::None, LocalValue::str),
            "metadata" => LocalValue::from_json(&Value::Object(node.metadata.clone())),
            "embedding" => match &node.embedding {
                Some(e) => LocalValue::List(e.iter().map(|x| LocalValue::float(*x)).collect()),
                None => LocalValue::None,
            },
            "layer" => LocalValue::int(node.layer.value()),
            _ => return None,
        })
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

/// `local_retrieve(request)`: `(retrieved object, confidence)` pairs from the owner's own retrieval.
pub type LocalRetrieve = Box<dyn Fn(&QueryRequest) -> CoordinationResult<Vec<(LocalNodeRef, f64)>>>;
/// `policy_filter(node, authorization)`.
pub type PolicyFilter = Box<dyn Fn(&dyn LocalNode, &AuthorizationContext) -> PolicyStatus>;
/// `lineage_resolver(node)`: should return a mapping of identifier tuples; anything is accepted and validated.
pub type LineageResolver = Box<dyn Fn(&dyn LocalNode) -> CoordinationResult<LocalValue>>;
/// `claim_projection(node)`: should return a flat dict of scalars; anything is accepted and validated.
pub type ClaimProjection = Box<dyn Fn(&dyn LocalNode) -> LocalValue>;

/// Narrow adapter for a real local NeuralGraph retrieval callback.
///
/// The callback executes inside the owner boundary. The optional lineage resolver may return only facts the
/// local graph actually knows; absent fields remain empty and are never synthesized by this adapter.
///
/// Only `PolicyStatus::Allowed` produces a claim. Every other status, `Redacted` included, fails closed as a
/// structurally empty denial trace: this adapter owns no redaction projection, so it cannot construct a sanitized
/// payload it can prove is safe.
pub struct NeuralGraphMemoryAdapter {
    node_id: String,
    capability: CapabilityDescriptor,
    local_retrieve: LocalRetrieve,
    policy_filter: PolicyFilter,
    lineage_resolver: Option<LineageResolver>,
    claim_projection: Option<ClaimProjection>,
    clock: Clock,
}

impl NeuralGraphMemoryAdapter {
    pub fn new(
        node_id: &str,
        capability: CapabilityDescriptor,
        local_retrieve: LocalRetrieve,
        policy_filter: PolicyFilter,
        clock: Clock,
    ) -> Self {
        NeuralGraphMemoryAdapter {
            node_id: node_id.to_string(),
            capability,
            local_retrieve,
            policy_filter,
            lineage_resolver: None,
            claim_projection: None,
            clock,
        }
    }

    /// The `lineage_resolver=` keyword.
    pub fn with_lineage_resolver(mut self, resolver: LineageResolver) -> Self {
        self.lineage_resolver = Some(resolver);
        self
    }

    /// The `claim_projection=` keyword.
    pub fn with_claim_projection(mut self, projection: ClaimProjection) -> Self {
        self.claim_projection = Some(projection);
        self
    }

    /// Return the retrieved object's identifier without reading its payload.
    ///
    /// A blank or whitespace-only `node_id` is rejected exactly like a missing one: two distinct memories carrying
    /// it would derive the same `claim_id` and `trace_id`. A `str` identifier is read through `str.__str__` (its
    /// stored text), so a subclass overriding `__str__` cannot collapse two memories onto one identity. Both
    /// messages name only the local type.
    pub fn memory_id(node: &dyn LocalNode) -> CoordinationResult<String> {
        let identifier = match node.getattr("node_id") {
            None | Some(LocalValue::None) => {
                return Err(CoordinationError::type_error(format!(
                    "{} carries no node_id and cannot be exported",
                    node.type_name()
                )))
            }
            Some(v) => v,
        };
        let memory_id = match identifier.as_str() {
            Some(s) => s.to_string(),
            None => identifier.py_str(),
        };
        if py_strip(&memory_id).is_empty() {
            return Err(CoordinationError::type_error(format!(
                "{} carries a blank node_id and cannot be exported",
                node.type_name()
            )));
        }
        Ok(memory_id)
    }

    /// Reject a resolver result the adapter cannot read as a mapping (a broken resolver must not read as "no
    /// known lineage").
    pub fn exportable_lineage(known: LocalValue) -> CoordinationResult<LocalDict> {
        match known {
            LocalValue::Dict(d) => Ok(d),
            other => Err(CoordinationError::value_error(format!(
                "lineage resolver must return a mapping, got {}",
                other.type_name()
            ))),
        }
    }

    /// Validate one lineage field, naming the field but never its content.
    ///
    /// `str`/`bytes` are rejected instead of iterated (`"memory-42"` would otherwise cross as nine one-character
    /// identifiers). Entries are re-exported as their stored text. Order and multiplicity are preserved exactly:
    /// normalising them is the resolver's job.
    pub fn exportable_identifiers(value: &LocalValue, field: &str) -> CoordinationResult<Vec<String>> {
        let entries = match value {
            LocalValue::List(items) | LocalValue::Tuple(items) => items,
            other => {
                return Err(CoordinationError::value_error(format!(
                    "{field} must be a tuple or list of identifiers, got {}",
                    other.type_name()
                )))
            }
        };
        let mut identifiers = Vec::with_capacity(entries.len());
        for entry in entries {
            let Some(text) = entry.as_str() else {
                return Err(CoordinationError::value_error(format!(
                    "{field} entries must be str, got {}",
                    entry.type_name()
                )));
            };
            if py_strip(text).is_empty() {
                return Err(CoordinationError::value_error(format!("{field} entries must not be blank")));
            }
            identifiers.push(text.to_string());
        }
        Ok(identifiers)
    }

    /// Validate the retrieval operator label the resolver may override.
    pub fn exportable_operator(known: &LocalDict) -> CoordinationResult<String> {
        let default = LocalValue::str("neuralgraph_local_retrieval");
        let value = known.get("retrieval_operator").unwrap_or(&default);
        let Some(text) = value.as_str() else {
            return Err(CoordinationError::value_error(format!(
                "retrieval_operator must be a str, got {}",
                value.type_name()
            )));
        };
        if py_strip(text).is_empty() {
            return Err(CoordinationError::value_error("retrieval_operator must not be blank"));
        }
        Ok(text.to_string())
    }

    /// Build the exported claim payload: an explicit `claim_projection` wins over a resolver-supplied
    /// `claim_content`; with neither the payload is the single-field text projection `{"text": str(content)}`.
    /// Python built that default eagerly, so a node without `content` raises `AttributeError` even when the
    /// resolver supplied content.
    fn claim_content(&self, node: &dyn LocalNode, known: &LocalDict) -> CoordinationResult<Map<String, Value>> {
        let content = match &self.claim_projection {
            Some(projection) => projection(node),
            None => {
                let text = node.getattr("content").ok_or_else(|| {
                    CoordinationError::new(
                        "AttributeError",
                        format!("'{}' object has no attribute 'content'", node.type_name()),
                    )
                })?;
                let default = LocalValue::dict(vec![("text", LocalValue::str(&text.py_str()))]);
                known.get("claim_content").cloned().unwrap_or(default)
            }
        };
        Self::exportable_content(&content)
    }

    /// Reject content the boundary cannot carry, naming keys but never values.
    ///
    /// Keys and values are re-exported as exact builtins read from their stored value (`str.__str__`,
    /// `int.__index__`, `float.__float__`), so no subclass override or attribute survives. `bool` and `None`
    /// pass unchanged (and `bool` stays `true`/`false`, not `1`/`0`). Non-finite floats are refused: `NaN` and
    /// `Infinity` are not JSON. The finiteness check reads the stored double, so a subclass advertising a finite
    /// `__float__` cannot smuggle one past it. The items validated are the items exported (`items()`), so a dict
    /// subclass whose view disagrees with its storage cannot export the storage.
    pub fn exportable_content(content: &LocalValue) -> CoordinationResult<Map<String, Value>> {
        let LocalValue::Dict(dict) = content else {
            return Err(CoordinationError::value_error(format!(
                "claim content must be a dict, got {}",
                content.type_name()
            )));
        };
        let mut exportable = Map::new();
        for (key, value) in dict.items() {
            let Some(key_text) = key.as_str() else {
                return Err(CoordinationError::value_error(format!(
                    "claim content keys must be str, got {}",
                    key.type_name()
                )));
            };
            let coerced = match value {
                LocalValue::Str(s, _) => Value::String(s.clone()),
                LocalValue::None => Value::Null,
                LocalValue::Bool(b) => Value::Bool(*b),
                LocalValue::Int(i, _) => Value::from(*i),
                LocalValue::Float(f, _) => match Number::from_f64(*f) {
                    Some(n) if f.is_finite() => Value::Number(n),
                    _ => {
                        return Err(CoordinationError::value_error(format!(
                            "claim content value for key {} must be finite",
                            py_repr_str(key_text)
                        )))
                    }
                },
                other => {
                    return Err(CoordinationError::value_error(format!(
                        "claim content value for key {} is not exportable: {}",
                        py_repr_str(key_text),
                        other.type_name()
                    )))
                }
            };
            exportable.insert(key_text.to_string(), coerced);
        }
        Ok(exportable)
    }
}

/// `max(0.0, min(1.0, x))` with Python's first-wins comparison (NaN maps to 1.0).
fn clamp_confidence(x: f64) -> f64 {
    let upper = if x < 1.0 { x } else { 1.0 };
    if upper > 0.0 {
        upper
    } else {
        0.0
    }
}

impl MemoryNodeAdapter for NeuralGraphMemoryAdapter {
    fn node_id(&self) -> &str {
        &self.node_id
    }

    /// Advertise the capability only to a context holding one of its scopes. An owner-declared availability (for
    /// example `Degraded`) is preserved for an authorized context; an empty `policy_scope` stays unavailable.
    fn describe_capabilities(&self, context: &AuthorizationContext) -> CoordinationResult<Vec<CapabilityDescriptor>> {
        if self.capability.policy_scope.iter().any(|scope| context.permits(&self.node_id, scope)) {
            return Ok(vec![self.capability.clone()]);
        }
        Ok(vec![
            CapabilityDescriptor { availability: Availability::Unavailable, ..self.capability.clone() }.validated()?
        ])
    }

    fn query(&self, request: &QueryRequest) -> CoordinationResult<Vec<EvidenceExport>> {
        if request.requested_capability.as_deref() != Some(self.capability.capability_id.as_str()) {
            return Ok(Vec::new());
        }
        let results = (self.local_retrieve)(request)?;
        let mut exports: Vec<EvidenceExport> = Vec::new();
        let mut exported_memory_ids: HashSet<String> = HashSet::new();
        for (node, confidence) in results {
            // The budget bounds exports, not candidates. A repeat skipped below never occupied a slot; a denial
            // trace is an export and does consume one.
            if exports.len() as i64 >= request.budget.max_claims {
                break;
            }
            let started = (self.clock)();
            let policy = (self.policy_filter)(node.as_ref(), &request.authorization);
            // Identifier only, read without touching any payload field.
            let memory_id = Self::memory_id(node.as_ref())?;
            // One memory exports at most once per query; the first path that reached it wins.
            if !exported_memory_ids.insert(memory_id.clone()) {
                continue;
            }
            let trace_id = opaque_id("trace", &[&request.query_id, &self.node_id, &memory_id]);
            if policy != PolicyStatus::Allowed {
                exports.push(EvidenceExport {
                    claims: Vec::new(),
                    trace: RetrievalTrace {
                        trace_id,
                        query_id: request.query_id.clone(),
                        node_id: self.node_id.clone(),
                        memory_ids: Vec::new(),
                        source_ids: Vec::new(),
                        parent_memory_ids: Vec::new(),
                        lineage_root_ids: Vec::new(),
                        edge_path: Vec::new(),
                        retrieval_operator: "policy_filter".to_string(),
                        policy_status: policy,
                        started_at: started,
                        completed_at: (self.clock)(),
                    }
                    .validated()?,
                });
                continue;
            }
            let known = Self::exportable_lineage(match &self.lineage_resolver {
                Some(resolver) => resolver(node.as_ref())?,
                None => LocalValue::empty_dict(),
            })?;
            // Resolver output and the node's own stored lineage are both untrusted input here.
            let declared = node.getattr("source_memory_ids").unwrap_or(LocalValue::Tuple(Vec::new()));
            let empty = LocalValue::Tuple(Vec::new());
            let source_ids = Self::exportable_identifiers(known.get("source_ids").unwrap_or(&declared), "source_ids")?;
            let parent_ids =
                Self::exportable_identifiers(known.get("parent_memory_ids").unwrap_or(&empty), "parent_memory_ids")?;
            let root_ids =
                Self::exportable_identifiers(known.get("lineage_root_ids").unwrap_or(&empty), "lineage_root_ids")?;
            let failure_domains =
                Self::exportable_identifiers(known.get("failure_domains").unwrap_or(&empty), "failure_domains")?;
            let edge_path = Self::exportable_identifiers(known.get("edge_path").unwrap_or(&empty), "edge_path")?;
            let retrieval_operator = Self::exportable_operator(&known)?;
            let content = self.claim_content(node.as_ref(), &known)?;
            let claim = ClaimEnvelope {
                claim_id: opaque_id("claim", &[&request.query_id, &self.node_id, &memory_id]),
                query_id: request.query_id.clone(),
                producer_node_id: self.node_id.clone(),
                content: Value::Object(content),
                confidence: clamp_confidence(confidence),
                evidence_refs: vec![opaque_id("evidence", &[&memory_id])],
                source_ids: source_ids.clone(),
                parent_memory_ids: parent_ids.clone(),
                lineage_root_ids: root_ids.clone(),
                failure_domains,
                policy_status: PolicyStatus::Allowed,
                derivation_operator: Some(retrieval_operator.clone()),
                created_at: (self.clock)(),
                valid_time: None,
            }
            .validated()?;
            let trace = RetrievalTrace {
                trace_id,
                query_id: request.query_id.clone(),
                node_id: self.node_id.clone(),
                memory_ids: vec![memory_id],
                source_ids,
                parent_memory_ids: parent_ids,
                lineage_root_ids: root_ids,
                edge_path,
                retrieval_operator,
                policy_status: PolicyStatus::Allowed,
                started_at: started,
                completed_at: (self.clock)(),
            }
            .validated()?;
            exports.push(EvidenceExport { claims: vec![claim], trace });
        }
        Ok(exports)
    }

    /// The Python at `eba2f370` had no verification callback here, so this adapter never verifies support.
    fn verify(&self, request: &VerificationRequest) -> CoordinationResult<VerificationResult> {
        Ok(VerificationResult {
            verification_id: request.verification_id.clone(),
            node_id: self.node_id.clone(),
            valid: false,
            lineage_root_ids: Vec::new(),
            failure_domains: Vec::new(),
            supported_slots: Vec::new(),
            reason: "verification callback not configured".to_string(),
        })
    }

    fn propose_learning(&self, signal: &LearningSignal) -> CoordinationResult<LearningDecision> {
        Ok(LearningDecision {
            signal_id: signal.signal_id.clone(),
            receiver_node_id: self.node_id.clone(),
            disposition: LearningDisposition::Rejected,
            reason: "real adapter requires a receiver-owned learning callback".to_string(),
            committed_memory_id: None,
        })
    }
}
