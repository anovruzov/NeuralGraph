//! Lineage resolution over a real NeuralGraph store (port of `coordination/storage_adapter.py`).
//!
//! This module is the single import boundary between the coordination package and NeuralGraph's storage layer:
//! `contracts`, `core` and `adapters` stay free of storage imports so the coordination contracts remain
//! domain-neutral, and this file exports nothing but identifier tuples.
//!
//! Direction is load-bearing. A `HIERARCHY` edge runs PARENT -> CHILD: the summary node is the edge's
//! `source_id` and the node the summary was built FROM is its `target_id`. `NeuralNode.parent_id` points the
//! other way, UP to the summary. The *derivation* parents of X are therefore the targets of X's own outgoing
//! `HIERARCHY` edges, plus X's self-declared `source_memory_ids`. Reading `parent_id` or `get_edges_to` instead
//! would invert lineage and report a summary as an ancestor of its own inputs.
//!
//! Only `get_node` and `get_edges_from` are used, both read-only ([`LineageSource`]). The storage trait is
//! infallible; `LineageSource` keeps the Python's "storage errors propagate" contract for stores that can fail.

use crate::coordination::adapters::{LineageResolver, LocalNode, LocalValue};
use crate::coordination::contracts::{CoordinationError, CoordinationResult};
use crate::retrieval::data_types::{EdgeRef, EdgeType, NodeRef};
use crate::retrieval::storage::NeuralGraphStorage;
use neuralgraph_memory::pycompat::py_strip;
use std::collections::BTreeSet;
use std::rc::Rc;

/// The two read-only storage calls the resolver makes. Every [`NeuralGraphStorage`] is one (and never fails);
/// a store that can fail reports the error, which the resolver propagates instead of reading it as "no
/// ancestors".
pub trait LineageSource {
    fn get_node(&self, node_id: &str) -> CoordinationResult<Option<NodeRef>>;
    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> CoordinationResult<Vec<EdgeRef>>;
}

impl<S: NeuralGraphStorage + ?Sized> LineageSource for S {
    fn get_node(&self, node_id: &str) -> CoordinationResult<Option<NodeRef>> {
        Ok(NeuralGraphStorage::get_node(self, node_id))
    }

    fn get_edges_from(&self, node_id: &str, edge_types: Option<&[EdgeType]>) -> CoordinationResult<Vec<EdgeRef>> {
        Ok(NeuralGraphStorage::get_edges_from(self, node_id, edge_types))
    }
}

/// `_identifier(value, field)`: a stored value as a plain, non-blank identifier. The message names the field and
/// the offending type only.
fn identifier(value: Option<&LocalValue>, field: &str) -> CoordinationResult<String> {
    let value = value.unwrap_or(&LocalValue::None);
    let Some(text) = value.as_str() else {
        return Err(CoordinationError::value_error(format!("{field} must be a str, got {}", value.type_name())));
    };
    identifier_text(text, field)
}

fn identifier_text(text: &str, field: &str) -> CoordinationResult<String> {
    if py_strip(text).is_empty() {
        return Err(CoordinationError::value_error(format!("{field} must not be blank")));
    }
    Ok(text.to_string())
}

/// `_identifiers(values, field)`: a stored id collection as plain identifiers (`str`/`bytes` are rejected rather
/// than exploded into characters).
fn identifiers(values: &LocalValue, field: &str) -> CoordinationResult<Vec<String>> {
    match values {
        LocalValue::List(items) | LocalValue::Tuple(items) => {
            items.iter().map(|v| identifier(Some(v), field)).collect()
        }
        other => Err(CoordinationError::value_error(format!(
            "{field} must be a tuple or list of identifiers, got {}",
            other.type_name()
        ))),
    }
}

/// The resolved derivation facts of one node, each sorted and de-duplicated.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ResolvedLineage {
    pub source_ids: Vec<String>,
    pub parent_memory_ids: Vec<String>,
    pub lineage_root_ids: Vec<String>,
    pub edge_path: Vec<String>,
    /// Always empty: this resolver has no failure-domain model, and reporting `()` keeps that absence visible
    /// instead of synthesising a domain the graph does not record.
    pub failure_domains: Vec<String>,
}

impl ResolvedLineage {
    /// The mapping Python returned: these five keys, in this order, as tuples.
    pub fn to_local_value(&self) -> LocalValue {
        let tuple = |items: &[String]| LocalValue::Tuple(items.iter().map(|s| LocalValue::str(s)).collect());
        LocalValue::dict(vec![
            ("source_ids", tuple(&self.source_ids)),
            ("parent_memory_ids", tuple(&self.parent_memory_ids)),
            ("lineage_root_ids", tuple(&self.lineage_root_ids)),
            ("edge_path", tuple(&self.edge_path)),
            ("failure_domains", tuple(&self.failure_domains)),
        ])
    }

    /// The fields by name, in the Python dict's order.
    pub fn items(&self) -> [(&'static str, &Vec<String>); 5] {
        [
            ("source_ids", &self.source_ids),
            ("parent_memory_ids", &self.parent_memory_ids),
            ("lineage_root_ids", &self.lineage_root_ids),
            ("edge_path", &self.edge_path),
            ("failure_domains", &self.failure_domains),
        ]
    }
}

/// Report the derivation facts a real local graph actually records.
///
/// Nothing is invented: a truncated or dangling walk under-reports rather than guessing, which keeps correlated
/// copies distinguishable from independent roots. Bounds are explicit so a hostile or cyclic graph cannot make
/// one query unbounded: the walk expands at most `max_nodes` nodes and follows at most `max_depth` hops.
pub struct StorageLineageResolver<S: LineageSource + ?Sized> {
    storage: Rc<S>,
    max_depth: i64,
    max_nodes: i64,
}

impl<S: LineageSource + ?Sized + 'static> StorageLineageResolver<S> {
    /// `StorageLineageResolver(storage)` with `max_depth=3`, `max_nodes=64`.
    pub fn new(storage: Rc<S>) -> Self {
        StorageLineageResolver { storage, max_depth: 3, max_nodes: 64 }
    }

    pub fn with_bounds(storage: Rc<S>, max_depth: i64, max_nodes: i64) -> CoordinationResult<Self> {
        if max_depth < 0 {
            return Err(CoordinationError::value_error("max_depth must not be negative"));
        }
        if max_nodes < 0 {
            return Err(CoordinationError::value_error("max_nodes must not be negative"));
        }
        Ok(StorageLineageResolver { storage, max_depth, max_nodes })
    }

    /// `resolver(node)`: resolve the derivation facts recorded for one retrieved node.
    ///
    /// `source_ids` are what the node declares about itself. `parent_memory_ids` are its direct derivation
    /// parents as recorded, including ids whose node no longer resolves. `lineage_root_ids` are only nodes the walk
    /// reached and resolved that record no derivation parent. `edge_path` is every `HIERARCHY` edge the walk read.
    pub fn resolve(&self, node: &dyn LocalNode) -> CoordinationResult<ResolvedLineage> {
        let origin_id = identifier(node.getattr("node_id").as_ref(), "node_id")?;
        let declared = identifiers(
            &node.getattr("source_memory_ids").unwrap_or(LocalValue::Tuple(Vec::new())),
            "source_memory_ids",
        )?;
        let mut edge_ids: BTreeSet<String> = BTreeSet::new();
        let parents = self.direct_parents(&origin_id, &declared, &mut edge_ids)?;
        let roots = self.walk(&origin_id, &parents, &mut edge_ids)?;
        Ok(ResolvedLineage {
            source_ids: declared.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect(),
            parent_memory_ids: parents.into_iter().collect(),
            lineage_root_ids: roots.into_iter().collect(),
            edge_path: edge_ids.into_iter().collect(),
            failure_domains: Vec::new(),
        })
    }

    /// This resolver as the adapter's `lineage_resolver` callback.
    pub fn into_resolver(self) -> LineageResolver {
        Box::new(move |node| self.resolve(node).map(|r| r.to_local_value()))
    }

    /// The union of the node's own `source_memory_ids` and the targets of its outgoing `HIERARCHY` edges.
    /// `child_ids` is deliberately unused: it records containment, not derivation.
    fn direct_parents(
        &self,
        node_id: &str,
        declared: &[String],
        edge_ids: &mut BTreeSet<String>,
    ) -> CoordinationResult<BTreeSet<String>> {
        let edges = self.storage.get_edges_from(node_id, Some(&[EdgeType::Hierarchy]))?;
        let mut observed: Vec<(String, String)> = Vec::with_capacity(edges.len());
        for edge in &edges {
            let edge = edge.borrow();
            observed.push((identifier_text(&edge.edge_id, "edge_id")?, identifier_text(&edge.target_id, "target_id")?));
        }
        observed.sort();
        let mut parents: BTreeSet<String> = declared.iter().cloned().collect();
        for (edge_id, target_id) in observed {
            edge_ids.insert(edge_id);
            parents.insert(target_id);
        }
        Ok(parents)
    }

    /// Breadth-first search upward for nodes that record no parent. The origin is seeded as visited, so a cycle
    /// terminates and the origin never roots itself; each frontier is expanded in sorted id order, so a walk
    /// truncated by `max_nodes` truncates identically on every run and backend.
    fn walk(
        &self,
        origin_id: &str,
        parents: &BTreeSet<String>,
        edge_ids: &mut BTreeSet<String>,
    ) -> CoordinationResult<BTreeSet<String>> {
        let mut roots: BTreeSet<String> = BTreeSet::new();
        let mut visited: BTreeSet<String> = BTreeSet::from([origin_id.to_string()]);
        let mut frontier: Vec<String> = parents.iter().filter(|p| !visited.contains(*p)).cloned().collect();
        visited.extend(frontier.iter().cloned());
        let mut expansions: i64 = 0;
        let mut depth: i64 = 0;
        while !frontier.is_empty() && depth < self.max_depth && expansions < self.max_nodes {
            depth += 1;
            let mut next_frontier: Vec<String> = Vec::new();
            for candidate in &frontier {
                if expansions >= self.max_nodes {
                    break;
                }
                expansions += 1;
                let Some(reached) = self.storage.get_node(candidate)? else {
                    // A recorded id whose node is gone stays a parent, but cannot be confirmed as a root.
                    continue;
                };
                let declared = {
                    let node = reached.borrow();
                    node.source_memory_ids
                        .iter()
                        .map(|s| identifier_text(s, "source_memory_ids"))
                        .collect::<CoordinationResult<Vec<_>>>()?
                };
                let candidate_parents = self.direct_parents(candidate, &declared, edge_ids)?;
                if candidate_parents.is_empty() {
                    roots.insert(candidate.clone());
                    continue;
                }
                for parent in candidate_parents {
                    if visited.insert(parent.clone()) {
                        next_frontier.push(parent);
                    }
                }
            }
            next_frontier.sort();
            frontier = next_frontier;
        }
        Ok(roots)
    }
}
