"""Built-in connectors. Each module imports only the connector contract, events, normalization, ACL and HTTP/OAuth helpers;
the pipeline never imports a module from this package (it looks connector types up in the registry)."""
from __future__ import annotations

from ..registry import ConnectorRegistry, registry
from .github import GitHubConnector
from .local_export import LocalExportConnector
from .scaffolds import SCAFFOLD_CONNECTORS
from .slack import SlackConnector

BUILTIN_CONNECTORS = (LocalExportConnector, GitHubConnector, SlackConnector)


def register_builtin(reg: ConnectorRegistry | None = None) -> ConnectorRegistry:
    """Register the built-in connector types (idempotent)."""
    reg = reg or registry
    for cls in BUILTIN_CONNECTORS:
        reg.register(cls, replace=True)
    for cls in SCAFFOLD_CONNECTORS:
        # planned apps: listed in the catalog as planned, refused by ConnectorRegistry.create; a real connector of the
        # same type registered later replaces its scaffold
        if cls.manifest.connector_type not in reg.types():
            reg.register(cls)
    return reg


__all__ = ["BUILTIN_CONNECTORS", "GitHubConnector", "LocalExportConnector", "SlackConnector", "register_builtin"]
