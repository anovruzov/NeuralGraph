"""OAuth 2.0 authorization-code + PKCE helpers shared by provider connectors (docs/mycelic/INGESTION.md §3.5, §10.2).

The OAuth *app* (client id and secret) is server configuration, not connection configuration: ``Connector.authorize`` and
``complete_authorization`` run before a connection exists, so the API layer constructs the connector with an
:class:`OAuthAppConfig` (``GitHubConnector(oauth=...)``) or registers one per connector type with
:func:`register_oauth_app`. The client secret is a :class:`~mycelic.ingest.contract.Secret`; it is sent only in the form
body of the provider's token endpoint and never logged. Nothing here reads the environment: the caller decides where
the app credentials come from. Tests use dummy values against the offline mocks.

PKCE (RFC 7636, ``S256``): the verifier is 64 random bytes in base64url (86 characters, within 43..128); the API stores it
encrypted with the ``state`` hash (``coord.oauth_states``) and passes it back to ``complete_authorization``.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode

from .contract import PermanentError, Secret
from .http import check_egress


@dataclass(frozen=True)
class OAuthAppConfig:
    client_id: str
    client_secret: Secret | None = None       # confidential client; None for public clients (PKCE only)
    oauth_base: str = ""                      # authorize/token host, e.g. https://github.com, https://slack.com ('' = provider default)
    api_base: str = ""                        # API base used by the token endpoint where it lives on the API host (Slack)
    scopes: tuple[str, ...] | None = None     # None = the connector's least-privilege default
    allow_loopback_http: bool = False         # offline mocks only: lets the bases point at http://127.0.0.1:<port>

    def __repr__(self) -> str:
        return f"OAuthAppConfig(client_id={self.client_id!r}, client_secret=***, oauth_base={self.oauth_base!r}, api_base={self.api_base!r})"


_APPS: dict[str, OAuthAppConfig] = {}


def register_oauth_app(connector_type: str, config: OAuthAppConfig | None) -> None:
    """Process-wide OAuth app for a connector type (the API layer calls this at start-up); ``None`` removes it."""
    if config is None:
        _APPS.pop(connector_type, None)
    else:
        _APPS[connector_type] = config


def oauth_app(connector_type: str) -> OAuthAppConfig | None:
    return _APPS.get(connector_type)


def pkce_pair() -> tuple[str, str]:
    """``(verifier, S256 challenge)``."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    return verifier, pkce_challenge(verifier)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def authorize_url(base: str, path: str, params: Mapping[str, str], *, allowed_hosts: tuple[str, ...], allow_loopback_http: bool) -> str:
    """The provider's authorize URL. The base is checked against the connector's allow-list so a misconfigured base can
    never send the owner's browser (and the code) somewhere else."""
    url = base.rstrip("/") + path
    check_egress(url, allowed_hosts, allow_loopback_http=allow_loopback_http)
    return url + "?" + urlencode([(k, v) for k, v in params.items() if v not in (None, "")])


def require_code(params: Mapping[str, str]) -> str:
    """The authorization code from the redirect, or a content-free error (the provider's error code only)."""
    if params.get("error"):
        raise PermanentError("authorization was not granted", code="oauth_denied", detail={"error": str(params["error"])[:60]})
    code = str(params.get("code") or "")
    if not code or len(code) > 512:
        raise PermanentError("missing authorization code", code="oauth_no_code")
    return code


__all__ = ["OAuthAppConfig", "authorize_url", "oauth_app", "pkce_challenge", "pkce_pair", "register_oauth_app", "require_code"]
