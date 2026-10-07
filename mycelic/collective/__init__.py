"""Mycelic collective: the layer that turns site records into cross-site signals without moving raw text.

This package is new work kept apart from the fabric (``mycelic/service.py`` and its siblings) so the two can be merged
independently. The fabric core imports nothing from here; ``tests/mycelic/test_collective_guards.py`` enforces that.
Everything in this package runs on the standard library alone.

Subpackages: ``inference`` (the one model client, bound to a data boundary, with its usage ledger and fakes),
``connectors`` (public-data readers), ``packs`` (domain packs: strict-JSON vocabulary, policy and world specs,
loaded into a frozen, hashed object, plus the deterministic canonicaliser, the record connector and the seeded world
generator), ``edge`` (what runs inside a site's boundary: claim extraction, the site's record store, and the
Boundary that lets only k-suppressed weekly cells and usage summaries out), ``leakage`` (the G0 canary planter and
text-leakage scanner) and ``experiments`` (founder harnesses that write ``runs/<kind>/<id>/``, and the G0 runner).
This file deliberately imports and re-exports nothing.
"""
