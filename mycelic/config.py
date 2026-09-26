"""Configuration from environment variables. Nothing secret lives in source or in the image.

Every knob has a ``MYCELIC_*`` variable; ``Settings.from_env()`` validates them once at start-up and fails fast
with a message naming the variable, which is what an operator wants from ``docker compose up``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    pass


def _int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


def _list(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # service
    host: str = "0.0.0.0"
    port: int = 8080
    db_path: str = "/data/mycelic.db"
    instance_id: str = "mycelic-main"
    log_level: str = "INFO"
    # transport
    nats_url: str | None = "nats://nats:4222"        # None disables NATS (in-process transport; tests/dev only)
    nats_user: str | None = None
    nats_password: str | None = None
    nats_token: str | None = None
    nats_ca_file: str | None = None
    nats_stream: str = "MYCELIC"
    nats_consumer: str = "mycelic-main"
    nats_max_age_seconds: int = 0                     # 0 = keep events forever (replay needs them)
    nats_max_bytes: int = -1                          # -1 = unlimited
    nats_duplicate_window_seconds: int = 7200
    nats_ack_wait_seconds: int = 30
    nats_max_deliver: int = 8
    publish_batch: int = 100
    publish_interval_seconds: float = 0.2
    consume_batch: int = 1                            # 1 = strict stream order (see transport.py)
    # security
    admin_token: str | None = None
    metrics_token: str | None = None                  # None = /metrics is open (bind it privately or set a token)
    ready_requires_nats: bool = False                 # /ready fails while NATS is down (default: outbox absorbs outages)
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)
    cors_origins: list[str] = field(default_factory=list)
    rate_limit_rps: float = 50.0                      # per principal
    rate_limit_burst: int = 100
    max_body_bytes: int = 1024 * 1024
    max_text_chars: int = 4000
    max_batch: int = 100
    max_event_bytes: int = 256 * 1024                 # serialized event; must stay below the broker's max_payload
    trust_proxy_headers: bool = False                 # use X-Forwarded-For for rate limiting (behind a trusted proxy only)
    trusted_proxy_hops: int = 1                       # how many proxies append to X-Forwarded-For (take the Nth from the right)
    audit_retention_days: int = 90
    event_signing_key: str | None = None              # HMAC key: consumer rejects events the publisher did not sign
    # aggregation
    min_support: int = 2                              # distinct child units needed for topic consolidation
    rules_file: str | None = None
    # SDK/demo conveniences
    public_url: str | None = None

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert_file and self.tls_key_file)

    @property
    def nats_enabled(self) -> bool:
        return bool(self.nats_url)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls(
            host=_str("MYCELIC_HOST", "0.0.0.0") or "0.0.0.0",
            port=_int("MYCELIC_PORT", 8080, minimum=1),
            db_path=_str("MYCELIC_DB_PATH", "/data/mycelic.db") or "/data/mycelic.db",
            instance_id=_str("MYCELIC_INSTANCE_ID", "mycelic-main") or "mycelic-main",
            log_level=(_str("MYCELIC_LOG_LEVEL", "INFO") or "INFO").upper(),
            nats_url=_str("MYCELIC_NATS_URL", "nats://nats:4222"),
            nats_user=_str("MYCELIC_NATS_USER"),
            nats_password=_str("MYCELIC_NATS_PASSWORD"),
            nats_token=_str("MYCELIC_NATS_TOKEN"),
            nats_ca_file=_str("MYCELIC_NATS_CA_FILE"),
            nats_stream=_str("MYCELIC_NATS_STREAM", "MYCELIC") or "MYCELIC",
            nats_consumer=_str("MYCELIC_NATS_CONSUMER", "mycelic-main") or "mycelic-main",
            nats_max_age_seconds=_int("MYCELIC_NATS_MAX_AGE_SECONDS", 0, minimum=0),
            nats_max_bytes=_int("MYCELIC_NATS_MAX_BYTES", -1),
            nats_duplicate_window_seconds=_int("MYCELIC_NATS_DUPLICATE_WINDOW_SECONDS", 7200, minimum=1),
            nats_ack_wait_seconds=_int("MYCELIC_NATS_ACK_WAIT_SECONDS", 30, minimum=1),
            nats_max_deliver=_int("MYCELIC_NATS_MAX_DELIVER", 8, minimum=1),
            publish_batch=_int("MYCELIC_PUBLISH_BATCH", 100, minimum=1),
            publish_interval_seconds=_float("MYCELIC_PUBLISH_INTERVAL_SECONDS", 0.2, minimum=0.01),
            consume_batch=_int("MYCELIC_CONSUME_BATCH", 1, minimum=1),
            admin_token=_str("MYCELIC_ADMIN_TOKEN"),
            metrics_token=_str("MYCELIC_METRICS_TOKEN"),
            ready_requires_nats=_bool("MYCELIC_READY_REQUIRES_NATS", False),
            tls_cert_file=_str("MYCELIC_TLS_CERT_FILE"),
            tls_key_file=_str("MYCELIC_TLS_KEY_FILE"),
            allowed_hosts=_list("MYCELIC_ALLOWED_HOSTS"),
            cors_origins=_list("MYCELIC_CORS_ORIGINS"),
            rate_limit_rps=_float("MYCELIC_RATE_LIMIT_RPS", 50.0, minimum=0.0),
            rate_limit_burst=_int("MYCELIC_RATE_LIMIT_BURST", 100, minimum=1),
            max_body_bytes=_int("MYCELIC_MAX_BODY_BYTES", 1024 * 1024, minimum=1024),
            max_text_chars=_int("MYCELIC_MAX_TEXT_CHARS", 4000, minimum=16),
            max_batch=_int("MYCELIC_MAX_BATCH", 100, minimum=1),
            max_event_bytes=_int("MYCELIC_MAX_EVENT_BYTES", 256 * 1024, minimum=4096),
            trust_proxy_headers=_bool("MYCELIC_TRUST_PROXY_HEADERS", False),
            trusted_proxy_hops=_int("MYCELIC_TRUSTED_PROXY_HOPS", 1, minimum=1),
            audit_retention_days=_int("MYCELIC_AUDIT_RETENTION_DAYS", 90, minimum=1),
            event_signing_key=_str("MYCELIC_EVENT_SIGNING_KEY"),
            min_support=_int("MYCELIC_MIN_SUPPORT", 2, minimum=1),
            rules_file=_str("MYCELIC_RULES_FILE"),
            public_url=_str("MYCELIC_PUBLIC_URL"),
        )
        s.validate()
        return s

    def validate(self) -> None:
        from urllib.parse import urlsplit

        if self.nats_url and not self.nats_url.startswith(("nats://", "tls://", "ws://", "wss://")):
            raise ConfigError("MYCELIC_NATS_URL must start with nats://, tls://, ws:// or wss://")
        if self.nats_url:
            parts = urlsplit(self.nats_url)
            if parts.username or parts.password:
                raise ConfigError("do not put credentials in MYCELIC_NATS_URL; use MYCELIC_NATS_USER / MYCELIC_NATS_PASSWORD")
        for name, value in (("MYCELIC_ADMIN_TOKEN", self.admin_token), ("MYCELIC_EVENT_SIGNING_KEY", self.event_signing_key),
                            ("MYCELIC_NATS_PASSWORD", self.nats_password), ("MYCELIC_METRICS_TOKEN", self.metrics_token)):
            if value and any(w in value.lower() for w in ("change", "example", "replace", "placeholder")):
                raise ConfigError(f"{name} looks like a placeholder; generate one with: openssl rand -hex 32")
        if self.event_signing_key is not None and len(self.event_signing_key) < 32:
            raise ConfigError("MYCELIC_EVENT_SIGNING_KEY must be at least 32 characters (openssl rand -hex 32)")
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise ConfigError("MYCELIC_TLS_CERT_FILE and MYCELIC_TLS_KEY_FILE must be set together")
        for name, path in (("MYCELIC_TLS_CERT_FILE", self.tls_cert_file), ("MYCELIC_TLS_KEY_FILE", self.tls_key_file),
                           ("MYCELIC_NATS_CA_FILE", self.nats_ca_file), ("MYCELIC_RULES_FILE", self.rules_file)):
            if path and not Path(path).is_file():
                raise ConfigError(f"{name} points to a file that does not exist: {path}")
        if self.admin_token is not None and len(self.admin_token) < 32:
            raise ConfigError("MYCELIC_ADMIN_TOKEN must be at least 32 characters (openssl rand -hex 32)")
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.admin_token:
            raise ConfigError("MYCELIC_ADMIN_TOKEN is required when binding to a non-loopback address")

    def redacted(self) -> dict[str, object]:
        """For logs and /health: every secret replaced by its presence."""
        d = dict(self.__dict__)
        for key in ("nats_password", "nats_token", "admin_token", "metrics_token", "event_signing_key"):
            d[key] = "set" if d.get(key) else None
        return d
