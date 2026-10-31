# Assistant demos

Two runnable demos of the local memory assistant
([`crates/neuralgraph-memory`](../crates/neuralgraph-memory/README.md)), built as cargo
examples. Both work with no model installed — `--fake-llm` substitutes a deterministic
stand-in, so the whole extraction and retrieval pipeline runs offline.

**Narrated live demo** — starts the worker, dashboard, REST API and MCP server, then
streams five chats through them while you watch memory get built:

```bash
cargo run --release --example chat_memory_live_demo -- --fake-llm
```

Then open http://127.0.0.1:8765/.

**Plain cross-chat demo** — feeds the same chats in and asks questions that span them:

```bash
cargo run --release --example chat_memory_demo -- --fake-llm
```

Drop `--fake-llm` once a local model server is running to see real extraction.

`data/sample_chats.jsonl` is five chats with one person over several months — the input
both demos replay. Research harnesses and datasets live in
[`research/`](../research/README.md), not here.

The Mycelic demos (end-to-end, strategic synthesis and the live console) moved with Mycelic to
[PNurmanM/MycelicLabs](https://github.com/PNurmanM/MycelicLabs/tree/main/demo).
