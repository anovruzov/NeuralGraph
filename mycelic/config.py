"""Configuration from environment variables. No secrets are ever written back; see deploy/mycelic/.env.example.

Every knob has a documented default so ``python -m mycelic serve`` works with nothing set (fake model,
SQLite transport, embedded holders). Production sets a data directory, a secret key, real model tiers and
NATS.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    # storage
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MYCELIC_DATA_DIR", "~/.mycelic")).expanduser())
    coord_db: str = ""                                   # derived: <data_dir>/coord.db unless MYCELIC_COORD_DB
    holders_dir: str = ""                                # derived: <data_dir>/holders
    # http
    host: str = os.environ.get("MYCELIC_HOST", "127.0.0.1")
    port: int = _int("MYCELIC_PORT", 8780)
    public_url: str = os.environ.get("MYCELIC_PUBLIC_URL", "")
    cors_origins: list[str] = field(default_factory=lambda: [o for o in os.environ.get("MYCELIC_CORS_ORIGINS", "").split(",") if o])
    allowed_hosts: list[str] = field(default_factory=lambda: [h for h in os.environ.get("MYCELIC_ALLOWED_HOSTS", "").split(",") if h])
    secure_cookies: bool = _bool("MYCELIC_SECURE_COOKIES", False)
    session_ttl_seconds: int = _int("MYCELIC_SESSION_TTL_SECONDS", 14 * 24 * 3600)
    secret_key: str = os.environ.get("MYCELIC_SECRET_KEY", "")   # signs holder envelopes; generated & persisted if empty
    # mode
    demo_mode: bool = _bool("MYCELIC_DEMO_MODE", True)
    embedded_holders: bool = _bool("MYCELIC_EMBEDDED_HOLDERS", True)
    run_worker_in_api: bool = _bool("MYCELIC_RUN_WORKER_IN_API", True)
    # transport
    transport: str = os.environ.get("MYCELIC_TRANSPORT", "sqlite")   # sqlite | nats
    nats_url: str = os.environ.get("MYCELIC_NATS_URL", "nats://127.0.0.1:4222")
    nats_user: str = os.environ.get("MYCELIC_NATS_USER", "")
    nats_password: str = os.environ.get("MYCELIC_NATS_PASSWORD", "")
    nats_stream: str = os.environ.get("MYCELIC_NATS_STREAM", "MYCELIC")
    transport_retention_seconds: int = _int("MYCELIC_TRANSPORT_RETENTION_SECONDS", 7 * 24 * 3600)
    # models (tier -> "provider:model"); provider in fake | anthropic | openai
    model_light: str = os.environ.get("MYCELIC_MODEL_LIGHT", "fake:mycelic-fake-light")
    model_standard: str = os.environ.get("MYCELIC_MODEL_STANDARD", "fake:mycelic-fake-standard")
    model_heavy: str = os.environ.get("MYCELIC_MODEL_HEAVY", "fake:mycelic-fake-heavy")
    embed_provider: str = os.environ.get("MYCELIC_EMBED_PROVIDER", "hash")   # hash | openai
    embed_model: str = os.environ.get("MYCELIC_EMBED_MODEL", "text-embedding-3-small")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url: str = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"))
    model_timeout_seconds: float = _float("MYCELIC_MODEL_TIMEOUT_SECONDS", 120.0)
    model_max_parallel: int = _int("MYCELIC_MODEL_MAX_PARALLEL", 4)
    # discovery loop defaults (tenant policy can override)
    loop_check_interval_seconds: int = _int("MYCELIC_LOOP_CHECK_INTERVAL_SECONDS", 300)
    loop_question_timeout_seconds: int = _int("MYCELIC_LOOP_QUESTION_TIMEOUT_SECONDS", 120)
    loop_max_concurrent_questions: int = _int("MYCELIC_LOOP_MAX_CONCURRENT_QUESTIONS", 2)
    loop_cooldown_seconds: int = _int("MYCELIC_LOOP_COOLDOWN_SECONDS", 900)
    loop_max_followup_depth: int = _int("MYCELIC_LOOP_MAX_FOLLOWUP_DEPTH", 3)
    worker_concurrency: int = _int("MYCELIC_WORKER_CONCURRENCY", 2)
    worker_lease_seconds: float = _float("MYCELIC_WORKER_LEASE_SECONDS", 120.0)
    worker_heartbeat_seconds: float = _float("MYCELIC_WORKER_HEARTBEAT_SECONDS", 10.0)
    worker_poll_seconds: float = _float("MYCELIC_WORKER_POLL_SECONDS", 1.0)
    # ingestion (docs/mycelic/INGESTION.md): holders run connectors; OAuth app credentials stay server-side
    ingest_enabled: bool = _bool("MYCELIC_INGEST", True)
    ingest_tick_seconds: float = _float("MYCELIC_INGEST_TICK_SECONDS", 5.0)
    github_client_id: str = os.environ.get("MYCELIC_GITHUB_CLIENT_ID", "")
    github_client_secret: str = os.environ.get("MYCELIC_GITHUB_CLIENT_SECRET", "")
    slack_client_id: str = os.environ.get("MYCELIC_SLACK_CLIENT_ID", "")
    slack_client_secret: str = os.environ.get("MYCELIC_SLACK_CLIENT_SECRET", "")
    # one Google OAuth client serves Gmail and Google Drive (each connection asks only for its own read-only scope)
    google_client_id: str = os.environ.get("MYCELIC_GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.environ.get("MYCELIC_GOOGLE_CLIENT_SECRET", "")
    # observability
    log_json: bool = _bool("MYCELIC_LOG_JSON", True)
    log_level: str = os.environ.get("MYCELIC_LOG_LEVEL", "INFO")
    error_webhook: str = os.environ.get("MYCELIC_ERROR_WEBHOOK", "")
    service_name: str = os.environ.get("MYCELIC_SERVICE", "api")

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        self.coord_db = os.environ.get("MYCELIC_COORD_DB") or str(self.data_dir / "coord.db")
        self.holders_dir = os.environ.get("MYCELIC_HOLDERS_DIR") or str(self.data_dir / "holders")

    @property
    def model_tiers(self) -> dict[str, str]:
        return {"light": self.model_light, "standard": self.model_standard, "heavy": self.model_heavy}

    def public_summary(self) -> dict:
        """What the admin UI may show: never a key."""
        return {
            "data_dir": str(self.data_dir), "transport": self.transport, "demo_mode": self.demo_mode,
            "embedded_holders": self.embedded_holders, "model_tiers": self.model_tiers,
            "embed_provider": self.embed_provider, "embed_model": self.embed_model,
            "anthropic_configured": bool(self.anthropic_api_key), "openai_configured": bool(self.openai_api_key),
            "nats_url": self.nats_url if self.transport == "nats" else None,
            "loop": {"check_interval_seconds": self.loop_check_interval_seconds,
                     "question_timeout_seconds": self.loop_question_timeout_seconds,
                     "max_concurrent_questions": self.loop_max_concurrent_questions,
                     "cooldown_seconds": self.loop_cooldown_seconds, "max_followup_depth": self.loop_max_followup_depth},
        }


def load_settings() -> Settings:
    return Settings()
