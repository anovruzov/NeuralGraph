"""
Factual Memory Store (Sino-Slavic-Iranian Architecture - Phase 4)

Chinese MemoryOS implementation for immutable fact storage.
Separates factual (immutable) from reflective (evolving) memories.
"""

from .factual_store import (
    FactualTriple,
    FactualMemoryStore,
)

__all__ = [
    "FactualTriple",
    "FactualMemoryStore",
]
