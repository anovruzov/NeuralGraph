# `NeuralGraph.chat_memory` — Python reference (frozen)

The memory server is now Rust: [`crates/neuralgraph-memory`](../../crates/neuralgraph-memory/), with its user guide
in [`crates/neuralgraph-memory/README.md`](../../crates/neuralgraph-memory/README.md). Install it with
`cargo install --locked --path crates/neuralgraph-memory` and run `neuralgraph-memory --help`.

This directory keeps only the Python library modules (store, extraction, retrieval, worker, service, LLM client)
because the research harnesses `research/benchmarks/chat_memory_locomo.py` and
`research/benchmarks/chat_memory_longmemeval.py` drive the pipeline through `ChatMemory` in-process. It is not
shipped and receives no new features; the CLI, MCP server, dashboard, demos and tests moved to the Rust crate.

Both implementations read and write the same database files, so a store built by a research run can be served by
`neuralgraph-memory` and vice versa.
