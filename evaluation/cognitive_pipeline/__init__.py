"""
Cognitive Pipeline Module for MemMachine Full-Pipeline Benchmark.

This module provides components for testing the complete MemMachine
cognitive architecture with all 6 phases enabled:
- Phase 1: Privacy Sanitization
- Phase 2: Emotional Encoding
- Phase 3: Mid-term Memory
- Phase 4: Knowledge Graph
- Phase 5: Granularity Router
- Phase 6: Domain Classifier
"""

from .pipeline_config import CognitivePipelineConfig
from .metrics_collector import (
    CognitiveMetrics,
    IngestMetrics,
    QueryMetrics,
    MetricsCollector,
)
from .cognitive_episodic_memory import CognitiveEpisodicMemory
from .result_analyzer import ResultAnalyzer

__all__ = [
    "CognitivePipelineConfig",
    "CognitiveMetrics",
    "IngestMetrics",
    "QueryMetrics",
    "MetricsCollector",
    "CognitiveEpisodicMemory",
    "ResultAnalyzer",
]
