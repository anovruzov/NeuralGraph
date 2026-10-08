"""Mycelic collective: the layer that turns site records into cross-site signals without moving raw text.

This package is new work kept apart from the fabric (``mycelic/service.py`` and its siblings) so the two can be merged
independently. The fabric core imports nothing from here; ``tests/mycelic/test_collective_guards.py`` enforces that.
Everything in this package runs on the standard library alone.

Subpackages: ``inference`` (the one model client, bound to a data boundary, with its usage ledger and fakes),
``connectors`` (public-data readers), ``packs`` (domain packs: strict-JSON vocabulary, policy and world specs,
loaded into a frozen, hashed object, plus the deterministic canonicaliser, the record connector and the seeded world
generator), ``edge`` (what runs inside a site's boundary: claim extraction, the site's record store, the
Boundary that lets only k-suppressed weekly cells, usage summaries and bucketed verdicts out and questions in, and
the site verifier that answers a question from the site's own records), ``detect`` (what runs at HQ: the org
config, the collective store of the cells received, and the model-free detectors and rules channel over them),
``pushdown`` (G6, at HQ: the narrow questions, the routing and delivery to the sites, and the commit gate that keeps
versioned conclusions), ``followup`` (G7, at HQ, built ahead of E2 and X4 and unvalidated: approval-routed follow-up
on supported conclusions, with its hash-chained ledger, T0 packets from the sites, T1 drafts, the kill switch and
the outcome check), ``evaluate`` (G5: plant specs, the baselines S, R (model-free), U, single_site and rules,
and the pre-registered X1/X2 harness and scorecard; evaluation only, never deployed), ``leakage`` (the G0 canary
planter and text-leakage scanner) and ``experiments`` (founder harnesses that write ``runs/<kind>/<id>/``, the G0
runner, the openFDA replay, E2 and the E5 injection smoke). This file deliberately imports and re-exports nothing.
``runfiles`` (G8) is the run-file contract that G0 and the collective demo (``demo/collective``) share.
"""
