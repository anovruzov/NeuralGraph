//! NeuralGraph research track.
//!
//! The Rust port of what was the Python research code: the LoCoMo retrieval engine (`retrieval`, Tesseract
//! multi-store retrieval, reranking, speaker profiles, entity-graph extraction, mega-search fusion), the
//! coordination simulator (`coordination`) and the benchmark harnesses (`benchmarks`, with one binary per
//! harness under `src/bin`). Nothing here is needed to run the assistant, which is the `neuralgraph-memory`
//! crate; this crate depends on that one, never the other way round.
//!
//! The port is pinned to the Python implementation (last Python commit `eba2f370`) by golden fixtures in
//! `tests/golden/`. Where Python's behaviour was deterministic the Rust output is identical; the compatibility
//! modules (`numpy`, `pyrandom`, `pytime`) carry the pieces of Python and numpy semantics that requires.

pub mod numpy;
pub mod pyrandom;
pub mod pyre;
pub mod pytime;

pub mod llm_backend;
pub mod temporal_utils;

pub mod benchmarks;
pub mod coordination;
pub mod retrieval;
