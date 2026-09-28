"""Small shared helpers: ids, time, JSON, hashing. Re-exports NeuralGraph's conventions so both halves agree."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from NeuralGraph.chat_memory.models import iso, new_id, now_iso, parse_iso, utcnow  # noqa: F401  (re-export)

__all__ = ["iso", "new_id", "now_iso", "parse_iso", "utcnow", "plus_seconds", "now_precise", "precise_iso", "j", "jl", "sha256", "token",
           "token_hash", "hmac_sign", "hmac_verify", "canonical_json", "fingerprint"]


def precise_iso(dt: datetime) -> str:
    """Microsecond ISO timestamp (sorts correctly against second-precision strings of the same convention)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def now_precise() -> str:
    return precise_iso(utcnow())


def plus_seconds(seconds: float, *, start: datetime | None = None) -> str:
    """Deadline strings (leases, backoffs, cooldowns) keep microseconds so short delays are honoured exactly."""
    return precise_iso((start or utcnow()) + timedelta(seconds=seconds))


def j(value: Any) -> str:
    """JSON for storage: stable key order, no ASCII escaping, ``None`` -> ``{}``."""
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def jl(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def fingerprint(text: str) -> str:
    """Content fingerprint used as a lineage *source root*: whitespace- and case-normalized SHA-256."""
    norm = " ".join(text.split()).strip().lower()
    return "root_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def hmac_sign(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_verify(key: str, message: str, signature: str) -> bool:
    return hmac.compare_digest(hmac_sign(key, message), signature or "")
