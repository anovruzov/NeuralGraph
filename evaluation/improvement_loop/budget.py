"""Paid-call governor. Checks the cap BEFORE a request, and counts retries.

The previous ledger was advisory and updated after the fact, so an A/A stability
sweep spent 48 requests against a 40-request ceiling before anyone looked. Two
design errors caused that, and both are fixed here:

* The check ran after the spend. `reserve()` now must be called *before* the
  request and raises `BudgetExceeded` rather than returning a warning.
* Retries were invisible. Every attempt consumes a request, so a retried call
  costs two. `reserve()` is called per attempt, not per logical question.

The governor persists atomically (temp file + rename) after every reservation,
so a crash cannot lose the record of money already spent -- which would let the
next process overspend the same cap again.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

LEDGER = Path("evaluation/artifacts/overnight_80/BUDGET_LEDGER.json")


class BudgetExceeded(RuntimeError):
    """Raised before a request that would breach a ceiling."""


@dataclass
class Ceilings:
    requests: int = 40
    tokens: int = 120_000
    usd: float = 3.00
    # Stop at 75% so retries cannot exhaust the true cap.
    stop_fraction: float = 0.75

    def effective(self) -> dict[str, float]:
        return {
            "requests": self.requests * self.stop_fraction,
            "tokens": self.tokens * self.stop_fraction,
            "usd": self.usd * self.stop_fraction,
        }


class BudgetGovernor:
    """Single point through which every paid request must pass."""

    def __init__(self, path: Path = LEDGER, ceilings: Ceilings | None = None,
                 enabled: bool = False) -> None:
        self.path = path
        self.ceilings = ceilings or Ceilings()
        self._lock = threading.Lock()
        self.enabled = enabled          # paid calls permitted at all
        self.state: dict[str, Any] = {
            "consumed_requests": 0,
            "consumed_attempts": 0,
            "consumed_tokens": 0,
            "consumed_usd": 0.0,
            "denied": 0,
        }
        if self.path.exists():
            try:
                prior = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"CORRUPTED LEDGER at {self.path}: {exc}. Do not delete it; it "
                    "records money already spent."
                ) from exc
            for key in self.state:
                if key in prior:
                    self.state[key] = prior[key]
            # Older ledgers used different key names; carry the spend forward
            # rather than resetting it to zero, which would re-authorise it.
            self.state["consumed_requests"] = max(
                self.state["consumed_requests"], int(prior.get("consumed_requests", 0))
            )
            self.state["consumed_tokens"] = max(
                self.state["consumed_tokens"], int(prior.get("consumed_tokens_est", 0))
            )
            self.state["consumed_usd"] = max(
                self.state["consumed_usd"], float(prior.get("consumed_cost_est_usd", 0.0))
            )
            self.enabled = bool(prior.get("paid_calls_permitted", enabled))

    def remaining(self) -> dict[str, float]:
        eff = self.ceilings.effective()
        return {
            "requests": eff["requests"] - self.state["consumed_requests"],
            "tokens": eff["tokens"] - self.state["consumed_tokens"],
            "usd": eff["usd"] - self.state["consumed_usd"],
        }

    def reserve(self, est_tokens: int = 0, est_usd: float = 0.0,
                is_retry: bool = False) -> None:
        """Call BEFORE every paid attempt. Raises rather than warning.

        A retry is a full attempt and consumes a request, because the provider
        bills it. Treating retries as free is how a bounded ceiling silently
        becomes unbounded.
        """
        with self._lock:
            if not self.enabled:
                self.state["denied"] += 1
                self._flush()
                raise BudgetExceeded(
                    "paid calls are not permitted (paid_calls_permitted=false). "
                    "Raise the ceiling explicitly before spending."
                )
            rem = self.remaining()
            if rem["requests"] < 1:
                self.state["denied"] += 1
                self._flush()
                raise BudgetExceeded(
                    f"request ceiling reached: {self.state['consumed_requests']} used, "
                    f"stop point {self.ceilings.effective()['requests']:.0f} "
                    f"(75% of {self.ceilings.requests})"
                )
            if est_tokens and rem["tokens"] < est_tokens:
                self.state["denied"] += 1
                self._flush()
                raise BudgetExceeded(
                    f"token ceiling would be breached: need {est_tokens}, "
                    f"{rem['tokens']:.0f} remain"
                )
            if est_usd and rem["usd"] < est_usd:
                self.state["denied"] += 1
                self._flush()
                raise BudgetExceeded(
                    f"dollar ceiling would be breached: need ${est_usd:.4f}, "
                    f"${rem['usd']:.4f} remains"
                )
            self.state["consumed_requests"] += 1
            self.state["consumed_attempts"] += 1
            if is_retry:
                self.state.setdefault("retries", 0)
                self.state["retries"] += 1
            self._flush()

    def record_actual(self, tokens: int, usd: float) -> None:
        """Reconcile after the response returns."""
        with self._lock:
            self.state["consumed_tokens"] += int(tokens)
            self.state["consumed_usd"] = round(self.state["consumed_usd"] + usd, 6)
            self._flush()

    def _flush(self) -> None:
        payload = {
            **self.state,
            "paid_calls_permitted": self.enabled,
            "ceilings": asdict(self.ceilings),
            "effective_stop_points": self.ceilings.effective(),
            "remaining": self.remaining(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(self.path)
