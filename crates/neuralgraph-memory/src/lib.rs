//! Cross-chat memory for NeuralGraph.
//!
//! Durable SQLite store of raw chat messages, LLM-derived memories, entities, typed relations and provenance,
//! plus a background worker that runs a local model (Ollama or LM Studio) in parallel to selectively turn new
//! messages into memories and relationships, and a hybrid retriever that answers questions across every chat.
//! Served over MCP (stdio and Streamable HTTP), a REST API and a live dashboard.
//!
//! This crate replaces the Python package `NeuralGraph.chat_memory`: the database schema and encodings, prompts,
//! defaults, CLI, HTTP routes and MCP tools are unchanged, and golden fixtures generated from the Python
//! implementation pin the behaviour (see `tests/golden`).

pub mod answer;
pub mod backend;
pub mod bm25;
pub mod cli;
pub mod clock;
pub mod difflib;
pub mod error;
pub mod extraction;
pub mod jsonutil;
pub mod llm;
pub mod mcp;
pub mod models;
pub mod prompts;
pub mod pycompat;
pub mod retrieval;
pub mod server;
pub mod service;
pub mod store;
pub mod temporal;
pub mod testing;
pub mod textutil;
pub mod worker;

pub use answer::{Answer, AnswerOptions, AnswerRoute};
pub use extraction::{ExtractionConfig, MemoryExtractor};
pub use llm::{fake_embedding, BackendLlmClient, FakeLlmClient, GenerateOpts, LlmClient};
pub use models::{ChatMessage, Entity, Job, Memory, MemoryLink, Relation, RetrievedMemory};
pub use retrieval::{MemoryRetriever, RetrievalConfig, SearchOptions};
pub use service::{estimate_tokens, ChatMemory, ChatMemoryConfig, MessageInput, RememberOptions};
pub use store::{ChatMemoryStore, ExtractionPlan};
pub use worker::{MemoryWorker, WorkerConfig, WorkerMetrics};
