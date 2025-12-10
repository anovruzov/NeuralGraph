"""Domain classifier for memory sharding."""

from .data_types import Domain, DomainClassification
from .domain_classifier import DomainClassifier
from .context_engineering import (
    QueryDomain,
    QueryIntent,
    QueryDecomposition,
    ContextDomainClassifier,
    QueryDecomposer,
    SemanticRouter,
    classify_query,
    get_context_amplitudes,
)

__all__ = [
    # Original exports
    "Domain",
    "DomainClassification",
    "DomainClassifier",
    # Context engineering exports
    "QueryDomain",
    "QueryIntent",
    "QueryDecomposition",
    "ContextDomainClassifier",
    "QueryDecomposer",
    "SemanticRouter",
    "classify_query",
    "get_context_amplitudes",
]
