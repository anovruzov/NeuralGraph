"""The reproducible demonstration organization (docs/mycelic/DEMO_SCENARIO.md).

* :mod:`mycelic.seed.demo` creates the two fictional tenants, their people, hierarchy, holders, evidence and the
  demonstration goal (``run_seed``), and exposes the document table as data (``demo_documents``).
* :mod:`mycelic.seed.simulate` is the ``demo.simulate`` job: bounded, clearly labelled activity for the demo tenant.
* :mod:`mycelic.seed.scenario` is the end-to-end verification (``python -m mycelic scenario``); it is imported
  explicitly by its callers because it pulls in aiohttp and subprocess management.
"""
from .demo import DEMO_PASSWORD, HOLDER_A, HOLDER_B, MERIDIAN_SLUG, ORBITAL_SLUG, demo_documents, reset_demo, run_seed
from .simulate import install

__all__ = ["DEMO_PASSWORD", "HOLDER_A", "HOLDER_B", "MERIDIAN_SLUG", "ORBITAL_SLUG", "demo_documents", "install", "reset_demo", "run_seed"]
