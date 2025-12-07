"""Multi-granularity routing module (MemGAS architecture).

Implements entropy-based granularity selection for adaptive retrieval.
"""

from .data_types import (
    ChunkingConfig,
    EntropyScore,
    GranularChunk,
    GranularityLevel,
    GranularityStats,
    RetrievalResult,
    RoutingDecision,
)
from .entropy_calculator import (
    AdaptiveEntropyCalculator,
    EntropyCalculator,
)
from .granularity_router import (
    GranularityRouter,
    MultiGranularityChunker,
)

__all__ = [
    # Data types
    "GranularityLevel",
    "EntropyScore",
    "GranularChunk",
    "RoutingDecision",
    "ChunkingConfig",
    "RetrievalResult",
    "GranularityStats",
    # Entropy
    "EntropyCalculator",
    "AdaptiveEntropyCalculator",
    # Router
    "GranularityRouter",
    "MultiGranularityChunker",
]
