"""NeuralGraph — persistent graph memory for AI agents.

The shipped assistant — the local-first, long-term memory server exposed over MCP — is the Rust crate
``crates/neuralgraph-memory`` at the repository root, not part of this Python package. What lives here:

``NeuralGraph.research``
    The public research tracks — the LoCoMo retrieval engine
    (``research.retrieval``) and the coordination simulator
    (``research.coordination``). Experimental, benchmark-facing, and not
    required to run the assistant.

``NeuralGraph.chat_memory``
    The frozen Python reference of the assistant's pipeline, kept only for the
    research harnesses that drive it in-process. It does not import the
    research code.

Two modules are shared: :mod:`NeuralGraph.llm_backend` (one shim over Ollama
and LM Studio) and :mod:`NeuralGraph.temporal_utils` (date parsing and
relative-date resolution).

This module stays deliberately empty of imports so that neither side pays for
the other. Import what you need from the subpackage that owns it.
"""

__version__ = "1.0.0"
__all__: list[str] = []
