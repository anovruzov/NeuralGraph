"""What runs at HQ: detection over the k-suppressed weekly cells that left the sites through the Boundary.

``org`` is the organisation config (sites, their unit paths, the decision unit of a set of sites), ``store`` is HQ's
own SQLite file (``collective.sqlite3``: the immutable cells and bundles received, rejections, the org log and the
saved detection runs; the only SQL on the HQ side), ``rules`` the hand-written rules channel (pure) and
``detectors`` the model-free detectors D2 to D7, the weekly walk with its alert budget and cooldown, and the result
JSON. Detection reads no model, no clock and no record text; nothing here imports ``edge.records``, ``edge.site``,
``edge.extract``, ``packs.generator``, ``evaluate``, ``leakage``, ``experiments`` or any inference module beyond the
error kinds the Boundary's validator names. This file imports nothing.
"""
