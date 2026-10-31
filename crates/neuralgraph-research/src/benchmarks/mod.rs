//! Shared code of the benchmark harnesses (port of `research/benchmarks/`); each harness is a binary in
//! `src/bin/`.

pub mod call_center;
pub mod chat_memory_context;
pub mod chat_memory_locomo;
pub mod chat_memory_longmemeval;
pub mod pyharness;
pub mod rescore;
pub mod retrieval_eval;
pub mod runner;
pub mod single_hop_regression;
pub mod two_agent_eval;
