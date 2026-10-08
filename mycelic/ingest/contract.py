"""The connector contract (docs/mycelic/INGESTION.md §3): what an app adapter must implement, and nothing more.

A connector is a stateless adapter. It knows one provider's API or export format; it never writes to a store, never
decides domains or shards, never logs content and never reads instructions from content. It yields :class:`Page` objects
of raw items; the pipeline admits, normalizes (through :meth:`Connector.normalize`), redacts and commits each page's events
together with the stream cursor in one transaction.

Connector status (``ConnectorManifest.status``) is stated honestly and shown to owners:

* ``scaffold`` — the shape exists, it does not work yet;
* ``implemented`` — it works against the provider's documented behaviour but has no offline conformance tests;
* ``tested-offline`` — it is exercised end to end by the offline test suite (fixtures or local files);
* ``live-verified`` — it was run against the real provider and the result recorded.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, ClassVar, Mapping, Protocol, Sequence

if TYPE_CHECKING:
    from .events import CanonicalEvent

CONNECTOR_STATUSES = ("scaffold", "implemented", "tested-offline", "live-verified")
AUTH_KINDS = ("pat", "oauth2", "github_app_user", "none")
MODES = ("pull", "webhook", "export")
OWNERSHIPS = ("personal", "org")


# ---------------------------------------------------------------------------------------------- errors (§3.1)
class ConnectorError(Exception):
    """Base. ``str(exc)`` and ``detail`` must never contain source content: ids, status codes and header values only."""
    code: str = "connector_error"
    retryable: bool = False

    def __init__(self, message: str = "", *, code: str | None = None, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message or self.code)
        if code:
            self.code = code
        self.detail = dict(detail or {})


class AuthExpired(ConnectorError):
    code = "auth_expired"


class AuthRevoked(ConnectorError):
    code = "auth_revoked"


class InsufficientScope(ConnectorError):
    code = "insufficient_scope"

    def __init__(self, missing: Sequence[str] = (), **kw: Any) -> None:
        super().__init__(**kw)
        self.missing = tuple(missing)


class RateLimited(ConnectorError):
    code = "rate_limited"
    retryable = True

    def __init__(self, retry_after: float, *, scope: str = "token", reset_at: float | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.retry_after = float(retry_after)
        self.scope = scope
        self.reset_at = reset_at


class TransientError(ConnectorError):
    code = "transient"
    retryable = True


class PermanentError(ConnectorError):
    code = "permanent"


class CursorInvalid(ConnectorError):
    code = "cursor_invalid"


class SourceUnavailable(ConnectorError):
    code = "source_unavailable"

    def __init__(self, reason: str = "access_lost", **kw: Any) -> None:
        super().__init__(**kw)
        self.reason = reason


class ObjectGone(ConnectorError):
    code = "object_gone"

    def __init__(self, object_id: str, *, status: int, moved_to: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.object_id = object_id
        self.status = status
        self.moved_to = moved_to


class QuotaExceeded(ConnectorError):
    code = "quota_exceeded"


# ---------------------------------------------------------------------------------------------- manifest (§3.2)
@dataclass(frozen=True)
class ScopeSpec:
    scope: str
    required: bool
    reason: str


@dataclass(frozen=True)
class Capabilities:
    edits: bool
    deletes: str                         # webhook | reconcile | none
    threads: bool
    attachments: bool
    acl: str                             # full | visibility_only | none
    exports: bool


@dataclass(frozen=True)
class RateLimitSpec:
    kind: str = "none"                   # headers | tiered | none
    default_rps: float = 0.0
    burst: int = 0
    serial_per_token: bool = False


@dataclass(frozen=True)
class ConnectorManifest:
    connector_type: str
    display_name: str
    version: str                         # semver; bump when normalize() output changes (stored per record)
    status: str                          # scaffold | implemented | tested-offline | live-verified
    auth_kinds: tuple[str, ...]
    scopes: tuple[ScopeSpec, ...]
    modes: frozenset[str]
    source_types: tuple[str, ...]
    capabilities: Capabilities
    rate_limit: RateLimitSpec = RateLimitSpec()
    allowed_hosts: tuple[str, ...] = ()  # egress allow-list (https hosts); empty for file-based connectors
    default_poll_seconds: int = 300
    terms_notes: str = ""
    ownership: tuple[str, ...] = OWNERSHIPS   # which holder kinds it may feed: personal (user holders), org (unit holders)

    def describe(self) -> dict[str, Any]:
        return {"connector_type": self.connector_type, "display_name": self.display_name, "version": self.version, "status": self.status,
                "auth_kinds": list(self.auth_kinds), "modes": sorted(self.modes), "source_types": list(self.source_types),
                "scopes": [{"scope": s.scope, "required": s.required, "reason": s.reason} for s in self.scopes],
                "capabilities": self.capabilities.__dict__, "terms_notes": self.terms_notes, "ownership": list(self.ownership)}


# ---------------------------------------------------------------------------------------------- context (§3.3)
class Secret:
    """Opaque wrapper: ``repr``/``str`` are ``***``; :meth:`reveal` is the only way to the value."""
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))


@dataclass(frozen=True)
class Credentials:
    kind: str
    access_token: Secret | None = None
    refresh_token: Secret | None = None
    expires_at: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)   # non-secret (token type, installation id)

    def to_storable(self) -> dict[str, Any]:
        return {"kind": self.kind, "access_token": self.access_token.reveal() if self.access_token else None,
                "refresh_token": self.refresh_token.reveal() if self.refresh_token else None, "expires_at": self.expires_at,
                "extra": dict(self.extra)}

    @classmethod
    def from_storable(cls, d: Mapping[str, Any]) -> "Credentials":
        return cls(kind=str(d.get("kind") or "none"), access_token=Secret(d["access_token"]) if d.get("access_token") else None,
                   refresh_token=Secret(d["refresh_token"]) if d.get("refresh_token") else None, expires_at=d.get("expires_at"),
                   extra=dict(d.get("extra") or {}))

    def __repr__(self) -> str:
        return f"Credentials(kind={self.kind!r}, access_token=***, refresh_token=***, expires_at={self.expires_at!r})"


class SecretAccessor(Protocol):
    async def get(self) -> Credentials: ...
    async def replace(self, creds: Credentials) -> None: ...
    def refresh_lock(self) -> asyncio.Lock: ...


@dataclass(frozen=True)
class Cursor:
    version: int
    data: Mapping[str, Any]
    high_watermark: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "data": dict(self.data), "high_watermark": self.high_watermark}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "Cursor | None":
        if not d or "version" not in d:
            return None
        return cls(version=int(d["version"]), data=dict(d.get("data") or {}), high_watermark=d.get("high_watermark"))


class CheckpointStore(Protocol):
    async def load(self, stream: str) -> Cursor | None: ...
    # writing is deliberately absent: cursors advance only in IngestQueue.commit_page (events + cursor, one transaction)


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes
    json: Any | None
    not_modified: bool = False
    links: Mapping[str, str] = field(default_factory=dict)


class ConnectorHttp(Protocol):
    async def request(self, method: str, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None,
                      json: Any | None = None, etag_key: str | None = None, expected: Sequence[int] = (200,),
                      max_bytes: int | None = None) -> HttpResult: ...


class NoHttp:
    """The HTTP client of connectors without network access (local exports): any request is refused."""

    async def request(self, method: str, url: str, **kw: Any) -> HttpResult:
        raise PermanentError("this connector has no network access", code="egress_denied")


@dataclass(frozen=True)
class ConnectorLimits:
    page_size: int = 100
    max_body_bytes: int = 1_000_000
    max_attachment_bytes: int = 25_000_000
    max_export_bytes: int = 5_000_000_000
    backfill_days: int = 365
    backfill_window_days: int = 30


@dataclass
class ConnectorContext:
    tenant_id: str
    holder_id: str
    connector_id: str
    connector_type: str
    source_app: str
    source_account_id: str
    auth_account_id: str
    config: Mapping[str, Any]
    limits: ConnectorLimits
    http: ConnectorHttp
    secrets: SecretAccessor
    checkpoints: CheckpointStore
    log: logging.LoggerAdapter
    clock: Callable[[], datetime]
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------------------------- data across the contract (§3.4)
@dataclass(frozen=True)
class SourceDescriptor:
    source_type: str                     # channel | dm | repo | mailbox | folder | export_file ...
    external_id: str
    name: str = ""                       # owner-visible only; never leaves the holder
    parent_external_id: str | None = None
    visibility: str = "private"          # public | members | private (source ACL, product decision 2026-10-08)
    member_ids: tuple[str, ...] = ()
    membership_ref: str | None = None
    suggested_domain_ids: tuple[str, ...] = ()
    approx_items: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackfillWindow:
    start: str                           # inclusive ISO
    end: str                             # exclusive ISO
    stream: str                          # 'backfill:<source_external_id>:<start>'


@dataclass(frozen=True)
class RawItem:
    """What normalize() consumes: provider JSON plus fetch context. Holder-local, never stored."""
    object_type: str
    payload: Any
    source: SourceDescriptor
    fetched_at: str
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    stream: str
    items: list[RawItem]
    next_cursor: Cursor | None           # None = stream finished for now
    has_more: bool
    raw_count: int = 0
    rate: Mapping[str, Any] = field(default_factory=dict)
    events: list["CanonicalEvent"] = field(default_factory=list)   # filled by the pipeline (normalize stage)

    def __post_init__(self) -> None:
        if not self.raw_count:
            self.raw_count = len(self.items)


@dataclass(frozen=True)
class WebhookNotice:
    """Thin, content-free notification: ids and an action, never titles or bodies."""
    connector_type: str
    delivery_id: str
    external_account_id: str
    source_external_id: str
    action: str
    object_refs: tuple[Mapping[str, str], ...]
    occurred_at: str | None = None


@dataclass(frozen=True)
class ConnectResult:
    source_account_id: str
    auth_account_id: str
    account_label: str
    granted_scopes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthStart:
    kind: str                            # redirect | token_entry | file_upload | none
    url: str | None = None
    instructions: str = ""
    pkce_verifier: Secret | None = None


@dataclass(frozen=True)
class HealthReport:
    status: str                          # ok | degraded | rate_limited | auth_expired | revoked | error
    checks: Mapping[str, bool]
    rate_limit: Mapping[str, Any] = field(default_factory=dict)
    code: str | None = None


# ---------------------------------------------------------------------------------------------- the ABC (§3.5)
class Connector(abc.ABC):
    """Subclass, set ``manifest``, implement the abstract methods, and register the class with
    :func:`mycelic.ingest.registry.register`. Nothing else in Mycelic changes for a new app."""

    manifest: ClassVar[ConnectorManifest]

    # ---- connection lifecycle
    @abc.abstractmethod
    async def authorize(self, *, tenant_id: str, holder_id: str, redirect_uri: str, state: str) -> AuthStart:
        """Begin authorization: an OAuth2 redirect, a token entry form, or a file upload for export connectors."""

    async def complete_authorization(self, params: Mapping[str, str], *, redirect_uri: str, pkce_verifier: Secret | None) -> Credentials:
        raise PermanentError("connector does not use OAuth2", code="not_oauth2")

    @abc.abstractmethod
    async def connect(self, ctx: ConnectorContext) -> ConnectResult:
        """Validate credentials/configuration with a cheap call; compute the identity namespace. Must not fetch content."""

    @abc.abstractmethod
    def discover_sources(self, ctx: ConnectorContext) -> AsyncIterator[SourceDescriptor]:
        """Enumerate what the grant can reach. The pipeline stores them as pending_review unless an owner rule includes them."""

    # ---- data
    @abc.abstractmethod
    def initial_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, window: BackfillWindow,
                         cursor: Cursor | None) -> AsyncIterator[Page]:
        """Historical items with source time in [window.start, window.end), resumable from ``cursor``."""

    @abc.abstractmethod
    def incremental_sync(self, ctx: ConnectorContext, source: SourceDescriptor, cursor: Cursor | None) -> AsyncIterator[Page]:
        """Changes since the cursor; duplicates are expected and removed by event_key dedupe."""

    @classmethod
    def verify_webhook(cls, headers: Mapping[str, str], body: bytes, secret: bytes, *, now: float) -> bool:
        """Constant-time signature check on the raw body. Default: no webhook support."""
        return False

    @classmethod
    def parse_webhook(cls, headers: Mapping[str, str], body: bytes) -> list[WebhookNotice]:
        """After verification: ids only, all content dropped."""
        return []

    def handle_webhook(self, ctx: ConnectorContext, notice: WebhookNotice) -> AsyncIterator[Page]:
        """Fetch the authoritative state of ``notice.object_refs`` and yield one page on stream 'webhook'. Deletions are
        confirmed (object absent while its container is still readable) before they become deletion events."""
        raise PermanentError("connector does not handle webhooks", code="no_webhooks")

    @abc.abstractmethod
    def normalize(self, raw: RawItem, ctx: ConnectorContext) -> list["CanonicalEvent"]:
        """Pure and deterministic: no I/O, no clock except ``raw.fetched_at``."""

    def checkpoint(self, page: Page, previous: Cursor | None) -> Cursor | None:
        """The cursor to persist once ``page`` is durably enqueued. Default: ``page.next_cursor``."""
        return page.next_cursor

    def plan_backfill(self, ctx: ConnectorContext, source: SourceDescriptor, *, anchor: datetime) -> list[BackfillWindow]:
        """Backfill windows, newest first (recent memory is useful first). ``anchor`` is the connector's connect time, so
        window boundaries — and therefore stream names and their checkpoints — are identical on every run and a crash
        resumes the same windows. Default: ``limits.backfill_window_days`` windows back to ``limits.backfill_days``."""
        return windows_between(source, start=anchor - timedelta(days=max(1, ctx.limits.backfill_days)), end=anchor,
                               window_days=ctx.limits.backfill_window_days)

    # ---- operations
    async def health(self, ctx: ConnectorContext) -> HealthReport:
        return HealthReport(status="ok", checks={})

    async def disconnect(self, ctx: ConnectorContext, *, revoke_at_provider: bool) -> None:
        """Provider-side cleanup (token revocation, webhooks this connector created). Data decisions are the pipeline's."""
        return None

    async def delete_source(self, ctx: ConnectorContext, source: SourceDescriptor) -> None:
        """Provider-side cleanup for one source. The pipeline tombstones and purges its records regardless."""
        return None


def windows_between(source: SourceDescriptor, *, start: datetime, end: datetime, window_days: int) -> list[BackfillWindow]:
    """``[start, end)`` cut into windows of ``window_days``, newest first, each its own stream."""
    out: list[BackfillWindow] = []
    step = timedelta(days=max(1, int(window_days)))
    hi = end
    while hi > start:
        lo = max(start, hi - step)
        s, e = lo.isoformat(timespec="microseconds"), hi.isoformat(timespec="microseconds")
        out.append(BackfillWindow(start=s, end=e, stream=f"backfill:{source.external_id}:{s}"))
        hi = lo
    return out


# ---------------------------------------------------------------------------------------------- logging (§10.5)
_TOKEN_MASKS = (
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"), re.compile(r"\bBearer\s+\S+"), re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
)
_CONTENT_EXTRA_KEYS = frozenset({"body", "text", "title", "payload", "subject", "snippet", "content", "name"})
MAX_LOGGED_ARG = 120


class RedactingFilter(logging.Filter):
    """Attached to the ``mycelic.ingest`` logger: drops content-named ``extra`` keys, masks token patterns, and replaces
    any interpolated string argument longer than 120 characters with ``<redacted:len=N>``."""

    def filter(self, record: logging.LogRecord) -> bool:
        for k in list(record.__dict__):
            if k in _CONTENT_EXTRA_KEYS:
                record.__dict__[k] = "<redacted>"
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            safe = []
            for a in args:
                if isinstance(a, str):
                    a = f"<redacted:len={len(a)}>" if len(a) > MAX_LOGGED_ARG else _mask(a)
                safe.append(a)
            record.args = tuple(safe)
        if isinstance(record.msg, str):
            record.msg = _mask(record.msg)
        return True


def _mask(s: str) -> str:
    for p in _TOKEN_MASKS:
        s = p.sub("***", s)
    return s


def get_logger(name: str) -> logging.Logger:
    """A logger with the :class:`RedactingFilter` attached. Logger filters do not apply to records propagated from child
    loggers, so every ``mycelic.ingest`` module gets its own filtered logger through this function."""
    log = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in log.filters):
        log.addFilter(RedactingFilter())
    return log


def connector_logger(connector_type: str, connector_id: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(get_logger(f"mycelic.ingest.connectors.{connector_type}"), {"connector_id": connector_id})
