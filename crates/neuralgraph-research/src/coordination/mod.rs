//! Deterministic multi-agent coordination simulator (port of `NeuralGraph/research/coordination/`).

pub mod adapters;
pub mod contracts;
pub mod core;
pub mod experiment;
pub mod fixture;
pub mod storage_adapter;

// Published with the pinned artifacts at `13a1729`; they never landed on main.
pub mod benchmark;
pub mod figures;
pub mod scale;
