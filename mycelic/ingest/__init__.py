"""Universal ingestion layer, holder side (docs/mycelic/INGESTION.md, Phase A).

Modules: ``contract`` (connector ABC, manifest, errors, context), ``events`` (CanonicalEvent v1, identity keys, hashes,
roots), ``normalize`` (body splitting, secret and injection detectors), ``acl`` (source ACLs and audience checks),
``crypto`` (credential envelope encryption), ``domains`` (taxonomy, classifier, memberships), ``queue`` (holder-local
durable queue), ``shards`` (shard map, routing, writer gates, stats and split recommendations), ``reshard`` (split
migrations), ``intents`` (control effects a data shard owes s0), ``fanout`` (cross-shard retrieval and traversal),
``shard_backup`` (per-shard backup and restore), ``store`` (catalog accessors), ``pipeline`` (stages), ``service`` (owner
operations), ``registry`` (connector types) and ``connectors`` (built-in adapters).

This package is imported lazily: ``mycelic.evidence`` uses ``acl``, ``domains``, ``events`` and ``normalize`` (and, inside
functions, ``shards``, ``fanout`` and ``intents``), so nothing here imports the pipeline at package import time. ``mycelic.discovery``, ``mycelic.inquiry``, ``mycelic.knowledge`` and
``mycelic.goals`` never import this package.
"""
