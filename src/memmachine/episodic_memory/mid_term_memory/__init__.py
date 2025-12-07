"""Mid-term memory module (MemoryOS three-tier architecture).

Implements heat-based memory lifecycle for segment consolidation
and forgetting based on the Ebbinghaus forgetting curve.
"""

from .data_types import (
    EvictionResult,
    HeatScore,
    MemorySegment,
    MidTermMemoryStats,
    PromotionResult,
    SegmentStatus,
)
from .mid_term_memory import MidTermMemory, MidTermMemoryConfig
from .storage import InMemoryMidTermMemoryStorage, MidTermMemoryStorage

__all__ = [
    # Data types
    "SegmentStatus",
    "HeatScore",
    "MemorySegment",
    "PromotionResult",
    "EvictionResult",
    "MidTermMemoryStats",
    # Storage
    "MidTermMemoryStorage",
    "InMemoryMidTermMemoryStorage",
    # Core
    "MidTermMemory",
    "MidTermMemoryConfig",
]
