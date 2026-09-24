"""Mycelic: infrastructure for distributed agent memory.

Agents keep knowledge locally; Mycelic lets the parts worth sharing propagate through an organization
(agent -> team -> department -> subsidiary -> region -> enterprise), aggregates them into higher-level memories with
full lineage, and makes them retrievable through an HTTP API, a Python SDK and MCP.

See ``DEPLOYMENT.md`` for running it and ``docs/MYCELIC_ARCHITECTURE.md`` for what actually runs.
"""
from .service import VERSION

__version__ = VERSION
__all__ = ["__version__"]
