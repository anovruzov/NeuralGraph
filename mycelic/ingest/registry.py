"""Connector type registry (docs/mycelic/INGESTION.md §3.5): adding an app is ``register(MyConnector)``, nothing else.

The pipeline only ever looks connectors up here by ``connector_type``; it never imports a concrete connector module
(checked statically by the tests). Registration validates the manifest so a malformed adapter fails at import, not at
3 a.m. on a webhook: https-only egress hosts, a reason for every scope, deletion capability consistent with the modes,
a semver version, and an honest status (``scaffold`` connectors cannot be instantiated for syncing).
"""
from __future__ import annotations

import re
from typing import Iterable

from .contract import AUTH_KINDS, CONNECTOR_STATUSES, MODES, OWNERSHIPS, Connector, ConnectorManifest

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_TYPE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_HOST = re.compile(r"^[a-z0-9.-]+(?::\d+)?$")


class ManifestError(ValueError):
    pass


def validate_manifest(m: ConnectorManifest) -> list[str]:
    problems: list[str] = []
    if not _TYPE.match(m.connector_type or ""):
        problems.append("connector_type must be lower-case letters, digits and underscores")
    if not (m.display_name or "").strip():
        problems.append("display_name is required")
    if not _SEMVER.match(m.version or ""):
        problems.append("version must be semver")
    if m.status not in CONNECTOR_STATUSES:
        problems.append(f"status must be one of {', '.join(CONNECTOR_STATUSES)}")
    if not m.auth_kinds or any(a not in AUTH_KINDS for a in m.auth_kinds):
        problems.append(f"auth_kinds must be drawn from {', '.join(AUTH_KINDS)}")
    if not m.modes or any(x not in MODES for x in m.modes):
        problems.append(f"modes must be drawn from {', '.join(MODES)}")
    if not m.source_types:
        problems.append("source_types is required")
    for s in m.scopes:
        if not (s.reason or "").strip():
            problems.append(f"scope {s.scope!r} has no reason (shown on the consent screen)")
    for h in m.allowed_hosts:
        if "://" in h and not h.startswith("https://"):
            problems.append(f"egress host {h!r} must be https")
        host = h.split("://", 1)[-1]
        if not _HOST.match(host):
            problems.append(f"egress host {h!r} is not a host name")
    if m.capabilities.deletes == "webhook" and "webhook" not in m.modes:
        problems.append("capabilities.deletes='webhook' needs the webhook mode")
    if m.capabilities.deletes not in ("webhook", "reconcile", "none"):
        problems.append("capabilities.deletes must be webhook | reconcile | none")
    if m.capabilities.acl not in ("full", "visibility_only", "none"):
        problems.append("capabilities.acl must be full | visibility_only | none")
    if not m.ownership or any(o not in OWNERSHIPS for o in m.ownership):
        problems.append(f"ownership must be drawn from {', '.join(OWNERSHIPS)}")
    return problems


class ConnectorRegistry:
    def __init__(self) -> None:
        self._types: dict[str, type[Connector]] = {}

    def register(self, cls: type[Connector], *, replace: bool = False) -> type[Connector]:
        if not isinstance(cls, type) or not issubclass(cls, Connector):
            raise ManifestError("only Connector subclasses can be registered")
        manifest = getattr(cls, "manifest", None)
        if not isinstance(manifest, ConnectorManifest):
            raise ManifestError(f"{cls.__name__} has no ConnectorManifest")
        problems = validate_manifest(manifest)
        if problems:
            raise ManifestError(f"{manifest.connector_type}: " + "; ".join(problems))
        existing = self._types.get(manifest.connector_type)
        if existing is not None and existing is not cls and not replace:
            raise ManifestError(f"connector type {manifest.connector_type!r} is already registered")
        self._types[manifest.connector_type] = cls
        return cls

    def unregister(self, connector_type: str) -> None:
        self._types.pop(connector_type, None)

    def get(self, connector_type: str) -> type[Connector]:
        try:
            return self._types[connector_type]
        except KeyError:
            raise KeyError(f"unknown connector type {connector_type!r}") from None

    def create(self, connector_type: str) -> Connector:
        cls = self.get(connector_type)
        if cls.manifest.status == "scaffold":
            raise ManifestError(f"connector type {connector_type!r} is a scaffold and does not run yet")
        return cls()

    def types(self) -> list[str]:
        return sorted(self._types)

    def catalog(self, *, enabled: Iterable[str] | None = None) -> list[dict]:
        allowed = set(enabled) if enabled is not None else None
        return [dict(self._types[t].manifest.describe(), enabled_for_tenant=allowed is None or t in allowed) for t in self.types()]


registry = ConnectorRegistry()


def register(cls: type[Connector]) -> type[Connector]:
    """Class decorator / function: add a connector type to the default registry."""
    return registry.register(cls)
