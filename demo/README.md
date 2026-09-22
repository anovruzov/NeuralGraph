# Assistant demos

Two runnable demos of the local memory assistant
([`NeuralGraph/chat_memory`](../NeuralGraph/chat_memory/README.md)). Both work with no
model installed — `--fake-llm` substitutes a deterministic stand-in, so the whole
extraction and retrieval pipeline runs offline.

**Narrated live demo** — starts the worker, dashboard, REST API and MCP server, then
streams five chats through them while you watch memory get built:

```bash
python demo/chat_memory_live_demo.py --fake-llm
# then open http://127.0.0.1:8765/
```

**Plain cross-chat demo** — feeds the same chats in and asks questions that span them:

```bash
python demo/chat_memory_demo.py --fake-llm
```

Drop `--fake-llm` once a local model server is running to see real extraction.

`data/sample_chats.jsonl` is five chats with one person over several months — the input
both demos replay. Research harnesses and datasets live in
[`research/`](../research/README.md), not here.
