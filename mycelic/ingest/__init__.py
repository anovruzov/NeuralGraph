"""Universal ingestion layer, holder side (docs/mycelic/INGESTION.md, Phase A).

Modules: ``contract`` (connector ABC, manifest, errors, context), ``events`` (CanonicalEvent v1, identity keys, hashes,
roots), ``normalize`` (body splitting, secret and injection detectors), ``acl`` (source ACLs and audience checks),
``crypto`` (credential envelope encryption), ``domains`` (taxonomy, classifier, memberships), ``queue`` (holder-local
durable queue), ``shards`` (write routing), ``store`` (catalog accessors), ``pipeline`` (stages), ``service`` (owner
operations), ``registry`` (connector types) and ``connectors`` (built-in adapters).

This package is imported lazily: ``mycelic.evidence`` uses ``acl``, ``domains``, ``events`` and ``normalize``, so nothing
here imports the pipeline at package import time. ``mycelic.discovery``, ``mycelic.inquiry``, ``mycelic.knowledge`` and
``mycelic.goals`` never import this package.
"""
