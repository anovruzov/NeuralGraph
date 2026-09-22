"""NeuralGraph — persistent graph memory for AI agents.

The package holds two things that are deliberately kept apart:

``NeuralGraph.chat_memory``
    The assistant. A local-first, long-term memory server exposed over MCP.
    This is the shipped product, and the only subpackage a user of the
    assistant needs. Importing it does not pull in the research code.

``NeuralGraph.research``
    The public research tracks — the LoCoMo retrieval engine
    (``research.retrieval``) and the coordination simulator
    (``research.coordination``). Experimental, benchmark-facing, and not
    required to run the assistant.

Two modules are shared by both: :mod:`NeuralGraph.llm_backend` (one shim over
Ollama and LM Studio) and :mod:`NeuralGraph.temporal_utils` (date parsing and
relative-date resolution).

This module stays deliberately empty of imports so that neither side pays for
the other. Import what you need from the subpackage that owns it.
"""

__version__ = "1.0.0"
__all__: list[str] = []
