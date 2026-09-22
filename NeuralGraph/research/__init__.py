"""Public research tracks.

``retrieval``
    The LoCoMo retrieval engine: Tesseract multi-store retrieval, reranking,
    speaker profiles, entity-graph extraction and the mega-search fusion.
    Benchmarks, results and reports live in the top-level ``research/``
    directory.

``coordination``
    A deterministic multi-agent simulator measuring which repair strategy
    keeps capabilities alive under node failure, memory deletion, auth
    revocation and partition.

Nothing here is needed to run the assistant (``NeuralGraph.chat_memory``).
These modules are experimental and their interfaces change with the
experiments.
"""

__all__ = ["coordination", "retrieval"]
