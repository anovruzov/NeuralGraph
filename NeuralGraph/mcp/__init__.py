"""NeuralGraph as an MCP memory server for coding agents (Claude Code, Codex).

``memory.py`` is the engine: create, recall, supersede, forget, decay and
export memories over a private ``SQLiteNeuralGraphStorage``.  ``server.py``
exposes it over the Model Context Protocol on stdio.  Nothing here modifies
the graph engine; it writes through the storage base API and reads through
the same API the coordination layer uses.
"""

from .memory import HashedEmbedder, MemoryEngine, ModelEmbedder

__all__ = ["HashedEmbedder", "MemoryEngine", "ModelEmbedder"]
