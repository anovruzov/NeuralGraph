"""Adapter interface for job discovery."""
from __future__ import annotations

import abc
import html
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import httpx

from ..config.logging_setup import get_logger
from ..config.settings import DiscoveryConfig
from ..database.models import Job

log = get_logger("discovery")

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|br)\s*>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_WS = re.compile(r"[ \t\x0b\f\r]+")
_NEWLINES = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """Strip HTML to readable text, preserving block structure (cheaper prompts)."""
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = _BR.sub("\n", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _NEWLINES.sub("\n\n", text).strip()


def guess_remote_status(location: str, description: str = "") -> str:
    text = f"{location} {description[:2000]}".lower()
    if re.search(r"\bhybrid\b", text):
        return "hybrid"
    if re.search(r"\bremote\b|\bwork from home\b|\bdistributed\b", text):
        return "remote"
    if re.search(r"\bon[- ]?site\b|\bin[- ]office\b", text):
        return "onsite"
    return "unknown"


def parse_salary(text: str) -> tuple[Optional[int], Optional[int]]:
    """Pull an annual USD salary range out of free text. Conservative: returns
    None unless the figures look like annual compensation."""
    if not text:
        return None, None
    pattern = re.compile(
        r"\$\s?(\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(k)?\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(k)?",
        re.I,
    )
    match = pattern.search(text)
    if not match:
        return None, None

    def to_int(value: str, k_suffix: Optional[str]) -> Optional[int]:
        try:
            num = float(value.replace(",", ""))
        except ValueError:
            return None
        if k_suffix:
            num *= 1000
        elif num < 1000:          # "$150 - $200" without k is not a salary range
            return None
        return int(num)

    low = to_int(match.group(1), match.group(2))
    high = to_int(match.group(3), match.group(4))
    if low and high and 20000 <= low <= 1_000_000 and low <= high:
        return low, high
    return None, None


class DiscoveryAdapter(abc.ABC):
    """Fetches public job listings from one source and normalizes them."""

    name: str = "base"
    ats_type: str = "unknown"

    def __init__(self, config: DiscoveryConfig,
                 client: Optional[httpx.Client] = None) -> None:
        self.config = config
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": config.user_agent,
                     "Accept": "application/json, text/html;q=0.9"},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    @abc.abstractmethod
    def discover(self, target: str) -> Iterator[Job]:
        """Yield normalized jobs for one target (board token, company slug, or URL)."""

    def get_json(self, url: str) -> Any:
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str) -> str:
        if url.startswith("file://"):
            # Local fixtures and offline testing; httpx has no file transport.
            from urllib.parse import unquote, urlsplit
            return Path(unquote(urlsplit(url).path)).read_text(errors="ignore")
        resp = self.client.get(url)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def matches_targets(title: str, target_roles: Iterable[str]) -> bool:
        """Cheap title gate before any network fetch of a full description.

        Deliberately generous: an unfamiliar-but-relevant title should reach the
        model rather than be dropped here.
        """
        t = (title or "").lower()
        tokens = {"engineer", "scientist", "researcher", "research", "developer",
                  "mle", "ml", "ai", "llm", "nlp"}
        if any(role.lower() in t for role in target_roles):
            return True
        words = set(re.findall(r"[a-z]+", t))
        return bool(words & tokens)
