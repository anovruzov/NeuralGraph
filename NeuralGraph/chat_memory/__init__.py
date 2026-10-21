"""Python reference of the NeuralGraph memory pipeline (frozen).

The memory server is the Rust crate ``crates/neuralgraph-memory`` (binary ``neuralgraph-memory``). This package keeps
the library modules only because the research harnesses ``research/benchmarks/chat_memory_*.py`` drive the pipeline
in-process through :class:`ChatMemory`; it is not shipped and receives no new features. Both implementations share
the database format.

    from NeuralGraph.chat_memory import ChatMemory

    async with ChatMemory("/tmp/research.db") as cm:      # worker runs in the background
        await cm.add_message("chat-1", "user", "I moved to Berlin last month")
        ...
        print(await cm.context_for("where does the user live?"))
"""
from .extraction import ExtractionConfig, MemoryExtractor
from .llm import BackendLLMClient, FakeLLMClient, LLMClient, LLMError, fake_embedding
from .models import ChatMessage, Entity, Job, Memory, MemoryLink, Relation, RetrievedMemory
from .retrieval import MemoryRetriever, RetrievalConfig
from .service import ChatMemory, ChatMemoryConfig, estimate_tokens
from .store import ChatMemoryStore, ExtractionPlan, LostLease
from .worker import MemoryWorker, WorkerConfig, WorkerMetrics

__all__ = [
    "BackendLLMClient", "ChatMemory", "ChatMemoryConfig", "ChatMemoryStore", "ChatMessage", "Entity",
    "ExtractionConfig", "ExtractionPlan", "FakeLLMClient", "Job", "LLMClient", "LLMError", "LostLease", "Memory",
    "MemoryExtractor", "MemoryLink", "MemoryRetriever", "MemoryWorker", "Relation", "RetrievalConfig",
    "RetrievedMemory", "WorkerConfig", "WorkerMetrics", "estimate_tokens", "fake_embedding",
]
