"""Founder-run experiment harnesses (STRATEGY section 11.2).

Each harness writes ``runs/<kind>/<run_id>/`` with a JSON result that carries ``measurement``, ``data_label`` and
the code stamps, and each supports ``--dry-run``. ``measurement`` is false whenever a fake was involved, so a
rehearsal can never be mistaken for a result. The harnesses: E3 (latency), N1 (narratives), E1 (extraction), G0
(canaries, with the edge, pushdown and follow-up stages), the openFDA replay, E2 (pushdown verification, G6), the
E5 injection smoke (G7; a synthetic plumbing smoke, not E5) and X5 (leakage beyond text, B3). It is
imported only by experiments and by evaluate.harness, which shares common's run-file helpers. This file imports
nothing.
"""
