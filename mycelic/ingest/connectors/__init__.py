"""Built-in connectors. Each module imports only the connector contract, events, normalization and ACL helpers; the
pipeline never imports a module from this package (it looks connector types up in the registry)."""
from __future__ import annotations

from ..registry import ConnectorRegistry, registry
from .local_export import LocalExportConnector

BUILTIN_CONNECTORS = (LocalExportConnector,)


def register_builtin(reg: ConnectorRegistry | None = None) -> ConnectorRegistry:
    """Register the built-in connector types (idempotent)."""
    reg = reg or registry
    for cls in BUILTIN_CONNECTORS:
        reg.register(cls, replace=True)
    return reg


__all__ = ["BUILTIN_CONNECTORS", "LocalExportConnector", "register_builtin"]
