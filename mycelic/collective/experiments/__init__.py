"""Founder-run experiment harnesses (STRATEGY section 11.2).

Each harness writes ``runs/<kind>/<run_id>/`` with a JSON result that carries ``measurement``, ``data_label`` and
the code stamps, and each supports ``--dry-run``. ``measurement`` is false whenever a fake was involved, so a
rehearsal can never be mistaken for a result. Nothing outside this package imports it. This file imports nothing.
"""
