# NeuralGraph

**Local-first graph memory for AI agents.**

NeuralGraph gives AI assistants persistent, searchable memory across conversations. It stores memories and relationships locally, retrieves relevant context using multiple signals, and exposes the memory system through MCP so clients such as Claude Desktop and Claude Code can use it.

This README is focused on getting NeuralGraph running.

> **Research track:** [`docs/MYCELIC_ENTERPRISE.md`](docs/MYCELIC_ENTERPRISE.md)
> is a separate benchmark study on hierarchical enterprise intelligence — how
> thousands of private user-level agents should abstract, route, question and
> verify knowledge upward so an enterprise kernel can discover things no single
> part of the organisation could. Code, raw per-run metrics and the full
> research log (including everything that was measured and discarded) are in
> [`research/mycelic/`](research/mycelic/).

## Requirements

Before installing, make sure you have:

- Python 3.10+
- Git
- Ollama
- macOS, Linux, or Windows with a Python environment
- Claude Desktop or Claude Code if you want to use NeuralGraph through MCP

## 1. Clone NeuralGraph

```bash
git clone https://github.com/anovruzov/NeuralGraph.git
cd NeuralGraph
```

## 2. Create a virtual environment

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

Install the project dependencies from the repository:

```bash
pip install -r requirements.txt
```

If you are developing NeuralGraph itself, install the repository in editable mode when supported by your checkout:

```bash
pip install -e .
```

## 4. Install and start Ollama

NeuralGraph is designed to run with local models through Ollama.

Install Ollama from:

https://ollama.com/

Then download the default models:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

Start Ollama if it is not already running:

```bash
ollama serve
```

Default configuration:

| Purpose | Default |
| --- | --- |
| Answer model | `qwen2.5:7b-instruct` |
| Embedding model | `nomic-embed-text` |
| Ollama endpoint | `http://localhost:11434` |

## 5. Run the live demo

You can test the cross-chat memory system without a live model server by running the fake-LLM demo:

```bash
.venv/bin/python demo/chat_memory_live_demo.py --fake-llm
```

On Windows:

```powershell
.venv\Scripts\python.exe demo\chat_memory_live_demo.py --fake-llm
```

The demo streams example conversations into NeuralGraph and performs cross-chat memory retrieval.

## 6. Start NeuralGraph

Start the local memory service:

### macOS / Linux

```bash
.venv/bin/python -m NeuralGraph.chat_memory serve --user-name "Your Name"
```

### Windows

```powershell
.venv\Scripts\python.exe -m NeuralGraph.chat_memory serve --user-name "Your Name"
```

Then open:

```text
http://127.0.0.1:8765/
```

The local dashboard lets you inspect the running memory system.

## 7. Run the MCP server

NeuralGraph includes an MCP server named:

```text
neuralgraph-chat-memory
```

Start it locally with:

### macOS / Linux

```bash
.venv/bin/python -m NeuralGraph.chat_memory mcp --user-name "Your Name"
```

### Windows

```powershell
.venv\Scripts\python.exe -m NeuralGraph.chat_memory mcp --user-name "Your Name"
```

The MCP server communicates over stdio for local clients.

For detailed Claude Desktop and Claude Code configuration, see:

**[NeuralGraph Chat Memory User Guide](NeuralGraph/chat_memory/README.md)**

## MCP tools

Once connected, your AI client can access these memory tools:

| Tool | Purpose |
| --- | --- |
| `memory_search` | Search stored memories |
| `memory_context` | Retrieve context relevant to the current conversation |
| `memory_add_message` | Add a conversation message |
| `memory_remember` | Explicitly save something to memory |
| `memory_profile` | Retrieve remembered information about the user |
| `memory_related` | Find related memories |
| `memory_entities` | Inspect remembered entities |
| `memory_forget` | Remove memory |
| `memory_status` | Check memory-system status |

The server also exposes three MCP resources and a `recall` prompt.

## How it works

```text
Conversation
    │
    ▼
Metadata + Memory Extraction
    │
    ▼
Neural Memory Graph
    │
    ├── semantic relationships
    ├── temporal relationships
    ├── entities
    ├── speaker identity
    └── provenance
    │
    ▼
Hybrid Retrieval
    │
    ├── semantic similarity
    ├── entity overlap
    ├── keyword matching
    ├── temporal relevance
    └── speaker-aware scoring
    │
    ▼
Relevant Memory Context
    │
    ▼
AI Assistant
```

NeuralGraph uses a local SQLite store and a background worker that selectively extracts memories and relationships. Retrieval combines multiple channels instead of relying only on vector similarity.

## Data and privacy

NeuralGraph is designed to run locally.

The default chat-memory configuration uses:

- local SQLite storage
- local Ollama models
- local embeddings
- local MCP transport
- local dashboard and API

With the default local configuration, your memory database does not need to be sent to a hosted memory service.

You are responsible for the security of the machine and applications that have access to the local database and MCP server.

## Verify your installation

After setup, verify the following:

1. Ollama is running.
2. `qwen2.5:7b-instruct` is installed.
3. `nomic-embed-text` is installed.
4. NeuralGraph starts without an import error.
5. The dashboard loads at `127.0.0.1:8765`.
6. Your MCP client can see `neuralgraph-chat-memory`.
7. `memory_status` returns successfully.

## Troubleshooting

### Ollama is not reachable

Check that Ollama is running:

```bash
ollama serve
```

The default endpoint is:

```text
http://localhost:11434
```

### Model not found

List installed models:

```bash
ollama list
```

If necessary:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

### Python import errors

Make sure your virtual environment is active and dependencies are installed:

```bash
pip install -r requirements.txt
```

### MCP client cannot connect

First confirm that NeuralGraph's MCP server starts successfully from a terminal:

```bash
.venv/bin/python -m NeuralGraph.chat_memory mcp --user-name "Your Name"
```

Then verify the executable path in your MCP client configuration.

See the **[full chat-memory guide](NeuralGraph/chat_memory/README.md)** for client-specific configuration and troubleshooting.

## Documentation

- **[Chat Memory User Guide](NeuralGraph/chat_memory/README.md)**
- **[Chat Memory Design](docs/CHAT_MEMORY.md)**
- **[Planning Brief](docs/PLANNING_PROMPT.md)**

## Development status

NeuralGraph is under active development. Interfaces, configuration, and storage formats may change.

Current development focuses on retrieval quality, temporal reasoning, query routing, cross-chat memory, attribution, latency, and local-first agent integrations.

## License

No license has been selected yet. All rights are reserved until a license is added.
