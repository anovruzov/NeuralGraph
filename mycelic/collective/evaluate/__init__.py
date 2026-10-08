"""G5 evaluation: measure the frozen detection pipeline on synthetic worlds with planted patterns and decoys, without
changing what is measured.

``plant`` reads a plant spec (strict JSON, bound to a pre-registration by its sha256), checks it against the pack and
the world, plants its records into a generated world and writes the seed-independent labels. ``baselines`` runs the
real G3 to G4 path (each site's ``EdgeSite``, the Boundary, HQ's ``CollectiveStore`` and ``detect``) and builds the
comparison channels: S, R (model-free), U (reference), single_site and rules. ``harness`` is the CLI
(``python -m mycelic.collective.evaluate.harness prereg|check-plant|run``) that pins the settings, runs the seeds and
writes the scorecard. Nothing here calls a model, and nothing under ``detect/`` imports this package. This file
imports nothing.
"""
