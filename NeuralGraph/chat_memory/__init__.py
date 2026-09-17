"""Cross-chat memory for NeuralGraph.

Durable SQLite store of raw chat messages, LLM-derived memories, entities, typed relations and
provenance, plus a background worker that runs Qwen (via ``NeuralGraph.llm_backend``) in parallel to
selectively turn new messages into memories and relationships, and a hybrid retriever that answers
questions across every chat.

Quick start::

    from NeuralGraph.chat_memory import ChatMemory

    async with ChatMemory("~/.neuralgraph/chat_memory.db") as cm:      # worker runs in the background
        await cm.add_message("chat-1", "user", "I moved to Berlin last month")
        ...
        print(await cm.context_for("where does the user live?"))

Run ``python -m NeuralGraph.chat_memory --help`` for the CLI (ingest, serve with dashboard, search, ...).
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
