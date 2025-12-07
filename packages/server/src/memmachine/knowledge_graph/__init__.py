"""Knowledge graph module (MeKB architecture).

Implements entity extraction, graph construction, MeKB scoring,
and Think-on-Graph reasoning for cognitive memory.
"""

from .data_types import (
    Entity,
    EntityType,
    ExtractionResult,
    MeKBScore,
    ReasoningPath,
    ReasoningResult,
    ReflectiveInsight,
    Relation,
    RelationType,
)
from .entity_extractor import (
    EntityExtractor,
    HybridEntityExtractor,
    OllamaEntityExtractor,
    RuleBasedEntityExtractor,
)
from .graph_reasoner import (
    InMemoryGraphStorage,
    MeKBScorer,
    ThinkOnGraphReasoner,
)
from .entity_verifier import EntityVerifier, VerificationResult
from .knowledge_graph_service import KnowledgeGraphService

__all__ = [
    # Data types
    "EntityType",
    "RelationType",
    "Entity",
    "Relation",
    "ExtractionResult",
    "MeKBScore",
    "ReasoningPath",
    "ReasoningResult",
    "ReflectiveInsight",
    # Extractors
    "EntityExtractor",
    "OllamaEntityExtractor",
    "RuleBasedEntityExtractor",
    "HybridEntityExtractor",
    # Reasoners
    "MeKBScorer",
    "ThinkOnGraphReasoner",
    "InMemoryGraphStorage",
    # Verification
    "EntityVerifier",
    "VerificationResult",
    # Service
    "KnowledgeGraphService",
]
