"""Security pipeline interface (implemented in full by the security module task).

`build_security(detector, cfg, profile, seed, local_slm)` returns a
`SecurityPipeline` with:

    filter_batch(batch, node) -> bool mask of records to keep
    inspect_claim(claim, sender, node) -> Verdict | None
    accounting: tokens_to_cloud, bytes_exposed, text_exposed (list of strings sent to a cloud classifier)

Detectors: none | rules | central_classifier | cloud_classifier | local_slm | hybrid_local_rules | lineage_aware.
The lineage-aware verifier is *structural* (signatures, dedup, consistency, support) and fully measured; the
content classifiers use the profile's (poison_tpr, poison_fpr) operating point, an explicit assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Verdict:
    flag: bool
    score: float
    reason: str


@dataclass
class SecurityPipeline:
    name: str = "none"
    tokens_to_cloud: int = 0
    bytes_exposed: int = 0
    text_exposed: list[str] = field(default_factory=list)
    flagged_records: int = 0
    flagged_claims: int = 0

    def filter_batch(self, batch, node) -> np.ndarray:
        return np.ones(len(batch), dtype=bool)

    def inspect_claim(self, claim, sender: str, node) -> Verdict | None:
        return None


def build_security(detector: str, cfg: dict[str, Any], profile, seed: int, local_slm: bool = False) -> SecurityPipeline:
    """Placeholder until security_impl is provided; returns the no-op pipeline for 'none'."""
    try:
        from .security_impl import build_security as _impl
    except ImportError:  # pragma: no cover - module delivered by the security task
        if detector == "none" and not local_slm:
            return SecurityPipeline()
        raise
    return _impl(detector, cfg, profile, seed, local_slm)
