"""Configuration: dataclasses with env + JSON file overrides.

Precedence (lowest to highest): dataclass defaults -> config JSON file ->
environment variables -> CLI flags applied by the caller.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "default_config.json"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency on python-dotenv). Never overrides real env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: Any, cast: type) -> Any:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if cast is list:
        return [x.strip() for x in raw.split(",") if x.strip()]
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


DEFAULT_TARGET_ROLES = [
    "AI Engineer", "Machine Learning Engineer", "Research Engineer",
    "Applied AI Engineer", "LLM Engineer", "Agent Engineer",
    "Agentic AI Engineer", "AI Infrastructure Engineer",
    "AI Evaluation Engineer", "Post-Training Engineer", "AI Systems Engineer",
    "RAG Engineer", "Retrieval Engineer", "ML Systems Engineer",
    "Junior Research Engineer",
]

DEFAULT_PREFERENCE_SIGNALS = [
    "remote US", "new grad", "junior", "entry level", "early career",
    "0-3 years", "research engineering", "AI startup", "frontier AI",
    "agent infrastructure", "LLM systems", "model evaluation",
    "post-training", "retrieval", "memory", "multi-agent systems",
]

# Rejected outright unless allow_senior_roles is set.
SENIOR_TITLE_MARKERS = [
    "staff", "principal", "director", "vp ", "vice president", "head of",
    "engineering manager", "em ", "manager,", "manager -", "distinguished",
    "fellow", "chief", "cto", "svp", "architect iv",
]


@dataclass
class QwenConfig:
    base_url: str = "http://localhost:8000/v1"
    api_key: str = ""
    model: str = "qwen2.5-72b-instruct"
    temperature: float = 0.0
    max_tokens: int = 1400
    timeout_seconds: float = 90.0
    max_retries: int = 2               # attempts after the first call
    cache_enabled: bool = True
    # USD per 1M tokens; used only for local cost accounting.
    price_input_per_mtok: float = 0.40
    price_output_per_mtok: float = 1.20
    # Emit `response_format={"type":"json_object"}`. Disable for endpoints that reject it.
    json_mode: bool = True


@dataclass
class ScoringConfig:
    apply_threshold: int = 65
    borderline_threshold: int = 50
    weights: dict[str, int] = field(default_factory=lambda: {
        "technical_fit": 30,
        "resume_evidence": 25,
        "ai_relevance": 20,
        "career_upside": 10,
        "company_comp": 10,
        "application_friction": 5,
    })
    allow_senior_roles: bool = False
    senior_title_markers: list[str] = field(default_factory=lambda: list(SENIOR_TITLE_MARKERS))
    max_years_experience_required: int = 5
    require_us_work_location: bool = True


@dataclass
class DiscoveryConfig:
    target_roles: list[str] = field(default_factory=lambda: list(DEFAULT_TARGET_ROLES))
    preference_signals: list[str] = field(default_factory=lambda: list(DEFAULT_PREFERENCE_SIGNALS))
    freshness_days: int = 7
    remote_only: bool = False
    countries: list[str] = field(default_factory=lambda: ["United States", "Remote"])
    # Greenhouse/Lever/Ashby board tokens to poll, e.g. {"greenhouse": ["anthropic"]}
    boards: dict[str, list[str]] = field(default_factory=dict)
    # Extra job/board URLs to crawl directly.
    seed_urls: list[str] = field(default_factory=list)
    max_jobs_per_board: int = 200
    request_timeout_seconds: float = 30.0
    user_agent: str = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


@dataclass
class BrowserConfig:
    headless: bool = True
    persistent_profile_dir: str = str(PACKAGE_ROOT / "logs" / "browser_profile")
    viewport_width: int = 1440
    viewport_height: int = 900
    navigation_timeout_ms: int = 45000
    action_timeout_ms: int = 15000
    slow_mo_ms: int = 0
    screenshot_on_blocker: bool = True
    screenshot_dir: str = str(PACKAGE_ROOT / "logs" / "screenshots")
    executable_path: str = ""            # override Chromium binary if needed
    locale: str = "en-US"
    timezone_id: str = "America/New_York"


@dataclass
class RunConfig:
    mode: str = "dry-run"                 # dry-run | apply
    max_applications: int = 25
    max_applications_per_hour: int = 12
    max_applications_per_company: int = 2
    # Hard floor applied after scoring. It defaults to the borderline threshold
    # so the documented 50-64 band (apply when the experience requirement is
    # still plausible) actually takes effect; raise it to be stricter.
    min_score: int = 50
    job_timeout_seconds: int = 300
    poll_interval_seconds: int = 30
    loop_forever: bool = False
    discovery_interval_seconds: int = 1800
    resume_path: str = str(PACKAGE_ROOT / "resumes" / "resume.pdf")
    profile_path: str = str(PACKAGE_ROOT / "profile" / "applicant.json")
    database_path: str = str(PACKAGE_ROOT / "logs" / "harness.db")
    log_dir: str = str(PACKAGE_ROOT / "logs")
    log_level: str = "INFO"
    min_field_confidence: float = 0.75


@dataclass
class DashboardConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""                  # required unless host is loopback
    refresh_seconds: int = 5


@dataclass
class Config:
    qwen: QwenConfig = field(default_factory=QwenConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    run: RunConfig = field(default_factory=RunConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    # -------------------------------------------------------------- loading

    @classmethod
    def load(cls, config_path: Optional[str | Path] = None,
             env_file: Optional[str | Path] = None) -> "Config":
        _load_dotenv(Path(env_file) if env_file else PACKAGE_ROOT.parent / ".env")
        cfg = cls()
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if path.exists():
            cfg.apply_dict(json.loads(path.read_text()))
        cfg.apply_env()
        cfg.validate()
        return cfg

    def apply_dict(self, data: dict[str, Any]) -> None:
        for section, values in (data or {}).items():
            target = getattr(self, section, None)
            if target is None or not dataclasses.is_dataclass(target):
                continue
            valid = {f.name for f in dataclasses.fields(target)}
            for key, value in (values or {}).items():
                if key in valid:
                    setattr(target, key, value)

    def apply_env(self) -> None:
        q = self.qwen
        q.base_url = _env("QWEN_BASE_URL", q.base_url, str)
        q.api_key = _env("QWEN_API_KEY", q.api_key, str)
        q.model = _env("QWEN_MODEL", q.model, str)
        q.temperature = _env("QWEN_TEMPERATURE", q.temperature, float)
        q.max_tokens = _env("QWEN_MAX_TOKENS", q.max_tokens, int)
        q.timeout_seconds = _env("QWEN_TIMEOUT", q.timeout_seconds, float)
        q.cache_enabled = _env("QWEN_CACHE", q.cache_enabled, bool)
        q.json_mode = _env("QWEN_JSON_MODE", q.json_mode, bool)
        q.price_input_per_mtok = _env("QWEN_PRICE_IN", q.price_input_per_mtok, float)
        q.price_output_per_mtok = _env("QWEN_PRICE_OUT", q.price_output_per_mtok, float)

        r = self.run
        r.max_applications = _env("MAX_APPLICATIONS", r.max_applications, int)
        r.max_applications_per_hour = _env("MAX_APPLICATIONS_PER_HOUR", r.max_applications_per_hour, int)
        r.min_score = _env("MIN_SCORE", r.min_score, int)
        r.database_path = _env("HARNESS_DB", r.database_path, str)
        r.log_dir = _env("HARNESS_LOG_DIR", r.log_dir, str)
        r.log_level = _env("HARNESS_LOG_LEVEL", r.log_level, str)
        r.resume_path = _env("RESUME_PATH", r.resume_path, str)
        r.profile_path = _env("APPLICANT_PROFILE", r.profile_path, str)
        r.min_field_confidence = _env("MIN_FIELD_CONFIDENCE", r.min_field_confidence, float)

        d = self.discovery
        d.freshness_days = _env("FRESHNESS_DAYS", d.freshness_days, int)
        d.remote_only = _env("REMOTE_ONLY", d.remote_only, bool)
        if os.environ.get("TARGET_ROLES"):
            d.target_roles = _env("TARGET_ROLES", d.target_roles, list)

        s = self.scoring
        s.apply_threshold = _env("APPLY_THRESHOLD", s.apply_threshold, int)
        s.allow_senior_roles = _env("ALLOW_SENIOR_ROLES", s.allow_senior_roles, bool)

        b = self.browser
        b.headless = _env("BROWSER_HEADLESS", b.headless, bool)
        b.persistent_profile_dir = _env("BROWSER_PROFILE_DIR", b.persistent_profile_dir, str)
        b.executable_path = _env("BROWSER_EXECUTABLE", b.executable_path, str)

        dash = self.dashboard
        dash.enabled = _env("DASHBOARD_ENABLED", dash.enabled, bool)
        dash.host = _env("DASHBOARD_HOST", dash.host, str)
        dash.port = _env("DASHBOARD_PORT", dash.port, int)
        dash.auth_token = _env("DASHBOARD_TOKEN", dash.auth_token, str)

    def validate(self) -> None:
        if self.run.mode not in ("dry-run", "apply"):
            raise ValueError(f"invalid mode: {self.run.mode}")
        if not (0 <= self.scoring.apply_threshold <= 100):
            raise ValueError("apply_threshold must be 0-100")
        if self.scoring.borderline_threshold > self.scoring.apply_threshold:
            raise ValueError("borderline_threshold must be <= apply_threshold")
        if self.run.min_score > self.scoring.apply_threshold:
            raise ValueError(
                f"run.min_score ({self.run.min_score}) is above "
                f"scoring.apply_threshold ({self.scoring.apply_threshold}), which "
                f"would discard every job the scorer accepts"
            )
        total = sum(self.scoring.weights.values())
        if total != 100:
            raise ValueError(f"scoring weights must sum to 100, got {total}")
        if self.dashboard.enabled and not self.dashboard.auth_token:
            if self.dashboard.host not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError(
                    "dashboard.auth_token is required when binding a non-loopback host; "
                    "set DASHBOARD_TOKEN"
                )

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["qwen"]["api_key"] = "***" if self.qwen.api_key else ""
        data["dashboard"]["auth_token"] = "***" if self.dashboard.auth_token else ""
        return data

    def redacted_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)
