"""Neural Memory Graph - Neural-inspired memory linking system.

This module implements a neural network-inspired approach to memory linking,
applying principles from six reference diagrams:

Image 1 (Dense Networks): Weighted, signed edges with distributed representation
Image 2 (Deep Networks): 4-layer hierarchical structure (Message→Episode→Topic→Persona)
Image 3 (Gated Networks): Non-linear gating functions for edge activation
Image 4 (Biological Neurons): Temporal dynamics, LTP, propagation delays
Image 5 (Compression): Pruning, merging, and summary hub creation
Image 6 (Algorithm Flow): Multi-stage retrieval pipeline

Core Principle:
"Signals propagate through a structured computation graph, are gated non-linearly,
consolidated by pruning/compression, and scheduled over time."

Usage:
    from memmachine.neural_graph import (
        NeuralNode,
        NeuralEdge,
        NodeLayer,
        EdgeType,
        InMemoryNeuralGraphStorage,
        HierarchyManager,
        TemporalChainManager,
        ConsolidationManager,
        NeuralRetriever,
        EdgeGateRegistry,
    )

    # Create storage
    storage = InMemoryNeuralGraphStorage()

    # Create managers
    hierarchy = HierarchyManager(storage)
    temporal = TemporalChainManager(storage)
    consolidation = ConsolidationManager(storage, hierarchy)
    retriever = NeuralRetriever(storage, temporal_manager=temporal)

    # Create message nodes
    node = await hierarchy.create_message_node(
        content="User message content",
        session_key="user_123",
        embedding=[0.1, 0.2, ...],
        wave_amplitudes={"temporal": 0.8, "entity": 0.6},
    )

    # Build temporal chains
    await temporal.build_temporal_chain(nodes, session_key)

    # Retrieve with multi-stage pipeline
    result = await retriever.retrieve(
        query_text="search query",
        query_embedding=[0.1, 0.2, ...],
        session_key="user_123",
    )

    # Run consolidation
    await consolidation.run_consolidation_cycle(session_key)
"""

# Data types
from .data_types import (
    # Enums
    NodeLayer,
    EdgeType,
    ConsolidationState,
    # Core types
    NeuralNode,
    NeuralEdge,
    # Result types
    RetrievalStageConfig,
    RetrievalResult,
    ConsolidationResult,
    # Utilities
    generate_node_id,
    generate_edge_id,
    cosine_similarity,
    sigmoid,
    relu,
)

# Storage
from .storage import (
    NeuralGraphStorage,
    InMemoryNeuralGraphStorage,
)
from .sqlite_storage import SQLiteNeuralGraphStorage

# Gating
from .gating import (
    EdgeGateRegistry,
    GateConfig,
    CompositeGate,
    AdaptiveGate,
    inhibitory_gate,
    create_strict_registry,
    create_permissive_registry,
    create_balanced_registry,
)

# Hierarchy
from .hierarchy import (
    HierarchyManager,
    HierarchyConfig,
)

# Temporal
from .temporal import (
    TemporalChainManager,
    TemporalConfig,
)

# Consolidation
from .consolidation import (
    ConsolidationManager,
    ConsolidationConfig,
)

# Retriever
from .retriever import (
    NeuralRetriever,
    RetrieverConfig,
)

# Service (main integration point)
from .service import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
    NeuralIngestionResult,
)

# Query Router (intelligent query analysis and pre-filtering)
# Phase 1 Fix: Now includes SoftFilterResult for graduated score modifiers
from .query_router import (
    QueryRouter,
    QueryType,
    QueryAnalysis,
    SoftFilterResult,
    FilteredRetriever,
    create_query_router,
    create_filtered_retriever,
)

# Prompts (schema-guided reasoning for SLM answering)
from .prompts import (
    RETRIEVAL_ANSWER_PROMPT,
    TEMPORAL_QUERY_PROMPT,
    MULTI_HOP_QUERY_PROMPT,
    OPEN_DOMAIN_QUERY_PROMPT,
    format_retrieval_context,
    select_prompt_for_query,
)

# Temporal utilities (date resolution, temporal tokens, query expansion)
from .temporal_utils import (
    # Constants
    MONTH_NAMES,
    MONTH_NAMES_REV,
    DAY_NAMES,
    DAY_NAMES_REV,
    # Ingestion-time functions
    parse_datetime_flexible,
    resolve_relative_dates,
    generate_temporal_tokens,
    preprocess_message_for_indexing,
    # Query-time functions
    expand_temporal_query,
    infer_query_mode,
)

__all__ = [
    # Enums
    "NodeLayer",
    "EdgeType",
    "ConsolidationState",
    # Core types
    "NeuralNode",
    "NeuralEdge",
    # Result types
    "RetrievalStageConfig",
    "RetrievalResult",
    "ConsolidationResult",
    # Utilities
    "generate_node_id",
    "generate_edge_id",
    "cosine_similarity",
    "sigmoid",
    "relu",
    # Storage
    "NeuralGraphStorage",
    "InMemoryNeuralGraphStorage",
    "SQLiteNeuralGraphStorage",
    # Gating
    "EdgeGateRegistry",
    "GateConfig",
    "CompositeGate",
    "AdaptiveGate",
    "inhibitory_gate",
    "create_strict_registry",
    "create_permissive_registry",
    "create_balanced_registry",
    # Hierarchy
    "HierarchyManager",
    "HierarchyConfig",
    # Temporal
    "TemporalChainManager",
    "TemporalConfig",
    # Consolidation
    "ConsolidationManager",
    "ConsolidationConfig",
    # Retriever
    "NeuralRetriever",
    "RetrieverConfig",
    # Service
    "NeuralGraphService",
    "NeuralGraphServiceConfig",
    "NeuralIngestionResult",
    # Query Router
    "QueryRouter",
    "QueryType",
    "QueryAnalysis",
    "SoftFilterResult",
    "FilteredRetriever",
    "create_query_router",
    "create_filtered_retriever",
    # Temporal utilities
    "MONTH_NAMES",
    "MONTH_NAMES_REV",
    "DAY_NAMES",
    "DAY_NAMES_REV",
    "parse_datetime_flexible",
    "resolve_relative_dates",
    "generate_temporal_tokens",
    "preprocess_message_for_indexing",
    "expand_temporal_query",
    "infer_query_mode",
]

__version__ = "1.0.0"
