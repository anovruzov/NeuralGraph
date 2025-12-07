"""
Configuration for the Cognitive Pipeline.

Defines all configuration options for the 6-phase cognitive architecture:
1. Privacy Sanitization - PII detection & redaction
2. Emotional Encoding - Plutchik emotions & mood tracking
3. Mid-term Memory - Heat-based consolidation
4. Knowledge Graph - Entity extraction & reasoning
5. Granularity Router - Entropy-based adaptive retrieval
6. Domain Classifier - Semantic memory sharding
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class EmotionDetectorType(Enum):
    """Type of emotion detector to use."""
    RULE = "rule"
    OLLAMA = "ollama"
    HYBRID = "hybrid"


@dataclass
class PrivacyConfig:
    """Configuration for Privacy Sanitization (Phase 1)."""
    enabled: bool = True
    block_critical: bool = True
    redact_high: bool = True
    allow_low_sensitivity: bool = True
    compliance_mode: Literal["gdpr", "ccpa", "pipl", "all"] = "all"


@dataclass
class EmotionalConfig:
    """Configuration for Emotional Encoding (Phase 2)."""
    enabled: bool = True
    detector_type: EmotionDetectorType = EmotionDetectorType.HYBRID
    mood_half_life_hours: float = 4.0
    resonance_weight: float = 0.3
    min_confidence_threshold: float = 0.3


@dataclass
class MidTermMemoryConfig:
    """Configuration for Mid-term Memory (Phase 3)."""
    enabled: bool = True
    promotion_threshold: float = 5.0
    eviction_threshold: float = 0.5
    heat_strength_days: float = 30.0
    visit_weight: float = 0.4
    interaction_weight: float = 0.3
    recency_weight: float = 0.3


@dataclass
class KnowledgeGraphConfig:
    """Configuration for Knowledge Graph (Phase 4)."""
    enabled: bool = True
    max_hops: int = 3
    beam_width: int = 5
    min_mekb_score: float = 0.5
    extract_entities: bool = True
    extract_relations: bool = True


@dataclass
class GranularityConfig:
    """Configuration for Granularity Router (Phase 5)."""
    enabled: bool = True
    adaptive: bool = True
    low_entropy_threshold: float = 0.3
    high_entropy_threshold: float = 0.7
    default_granularity: Literal["fine", "medium", "coarse"] = "medium"


@dataclass
class DomainConfig:
    """Configuration for Domain Classifier (Phase 6)."""
    enabled: bool = True
    min_confidence: float = 0.5
    use_embedding_fallback: bool = True
    domains: list[str] = field(default_factory=lambda: [
        "RELATIONSHIPS",
        "EVENTS",
        "PERSONAL",
        "HOBBIES",
        "PROFESSIONAL",
        "HEALTH",
        "PREFERENCES",
        "GOALS",
        "OTHER",
    ])


@dataclass
class ShardedMemoryConfig:
    """Configuration for Sharded Memory with Hash Pointers (Phase 7)."""
    enabled: bool = True
    num_shards: int = 16
    virtual_nodes_per_shard: int = 150
    max_hops: int = 2  # Graph traversal depth
    follow_domain_pointers: bool = True
    enable_domain_hints: bool = True  # Extract domains from query text
    # Retrieval scoring weights
    embedding_weight: float = 0.4
    importance_weight: float = 0.25
    recency_weight: float = 0.2
    access_weight: float = 0.1
    path_weight: float = 0.05


@dataclass
class LLMConfig:
    """Configuration for LLM services."""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    judge_model: str = "deepseek-r1:14b"
    embedding_model: str = "nomic-embed-text"
    temperature: float = 0.0
    timeout_seconds: int = 60


@dataclass
class CognitivePipelineConfig:
    """
    Master configuration for the full cognitive pipeline.

    This configuration enables/disables and tunes all 7 phases of the
    MemMachine cognitive architecture for benchmark testing.
    """
    # Phase configurations
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    emotional: EmotionalConfig = field(default_factory=EmotionalConfig)
    mid_term_memory: MidTermMemoryConfig = field(default_factory=MidTermMemoryConfig)
    knowledge_graph: KnowledgeGraphConfig = field(default_factory=KnowledgeGraphConfig)
    granularity: GranularityConfig = field(default_factory=GranularityConfig)
    domain: DomainConfig = field(default_factory=DomainConfig)
    sharded_memory: ShardedMemoryConfig = field(default_factory=ShardedMemoryConfig)

    # LLM configuration
    llm: LLMConfig = field(default_factory=LLMConfig)

    # Benchmark settings
    concurrent_questions: int = 4
    embedding_batch_size: int = 10
    context_limit: int = 12000
    top_k_retrieval: int = 30
    correct_threshold: int = 8
    partial_threshold: int = 5

    def get_enabled_phases(self) -> list[str]:
        """Return list of enabled phase names."""
        phases = []
        if self.privacy.enabled:
            phases.append("Privacy Sanitization")
        if self.emotional.enabled:
            phases.append("Emotional Encoding")
        if self.mid_term_memory.enabled:
            phases.append("Mid-term Memory")
        if self.knowledge_graph.enabled:
            phases.append("Knowledge Graph")
        if self.granularity.enabled:
            phases.append("Granularity Router")
        if self.domain.enabled:
            phases.append("Domain Classifier")
        return phases

    def __str__(self) -> str:
        """Format configuration for display."""
        lines = [
            "=== Cognitive Pipeline Configuration ===",
            f"  Privacy: {'ENABLED' if self.privacy.enabled else 'DISABLED'}"
            f" (block_critical={self.privacy.block_critical})",
            f"  Emotions: {'ENABLED' if self.emotional.enabled else 'DISABLED'}"
            f" ({self.emotional.detector_type.value} detector)",
            f"  MTM: {'ENABLED' if self.mid_term_memory.enabled else 'DISABLED'}"
            f" (promotion_threshold={self.mid_term_memory.promotion_threshold})",
            f"  Knowledge Graph: {'ENABLED' if self.knowledge_graph.enabled else 'DISABLED'}"
            f" (max_hops={self.knowledge_graph.max_hops})",
            f"  Granularity: {'ENABLED' if self.granularity.enabled else 'DISABLED'}"
            f" (adaptive={self.granularity.adaptive})",
            f"  Domain: {'ENABLED' if self.domain.enabled else 'DISABLED'}",
            f"  LLM: {self.llm.ollama_model} @ {self.llm.ollama_base_url}",
        ]
        return "\n".join(lines)


def create_minimal_config() -> CognitivePipelineConfig:
    """Create config with only essential features enabled."""
    return CognitivePipelineConfig(
        privacy=PrivacyConfig(enabled=False),
        emotional=EmotionalConfig(enabled=False),
        mid_term_memory=MidTermMemoryConfig(enabled=False),
        knowledge_graph=KnowledgeGraphConfig(enabled=False),
        granularity=GranularityConfig(enabled=False),
        domain=DomainConfig(enabled=False),
    )


def create_full_config() -> CognitivePipelineConfig:
    """Create config with all features enabled (default)."""
    return CognitivePipelineConfig()


def create_hybrid_config() -> CognitivePipelineConfig:
    """Create config with hybrid LLM approach for emotion detection."""
    config = CognitivePipelineConfig()
    config.emotional.detector_type = EmotionDetectorType.HYBRID
    return config
