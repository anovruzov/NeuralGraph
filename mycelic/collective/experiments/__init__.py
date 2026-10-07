"""Founder-run experiment harnesses (STRATEGY section 11.2).

Each harness writes ``runs/<kind>/<run_id>/`` with a JSON result that carries ``measurement``, ``data_label`` and
the code stamps, and each supports ``--dry-run``. ``measurement`` is false whenever a fake was involved, so a
rehearsal can never be mistaken for a result. The harnesses: E3 (latency), N1 (narratives), E1 (extraction), G0
(canaries, with the edge and pushdown stages), the openFDA replay and E2 (pushdown verification, G6). It is
imported only by experiments and by evaluate.harness, which shares common's run-file helpers. This file imports
nothing.
"""
