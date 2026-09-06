"""Discovery orchestration: run adapters, normalize, dedupe, persist."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import httpx

from ..config.logging_setup import get_logger
from ..config.settings import Config
from ..database.db import Database
from ..database.models import Job, JobStatus
from .ashby import AshbyAdapter
from .base import DiscoveryAdapter
from .dedupe import Deduplicator
from .generic_url import GenericUrlAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workable import WorkableAdapter
from .workday import WorkdayAdapter

log = get_logger("discovery.engine")

ADAPTERS: dict[str, type[DiscoveryAdapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workday": WorkdayAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "workable": WorkableAdapter,
    "generic_url": GenericUrlAdapter,
}


def register_adapter(name: str, adapter: type[DiscoveryAdapter]) -> None:
    """Extension point for additional ATS providers."""
    ADAPTERS[name] = adapter


@dataclass
class DiscoveryReport:
    discovered: int = 0
    duplicates: int = 0
    errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    new_job_ids: list[str] = field(default_factory=list)


class DiscoveryEngine:
    def __init__(self, config: Config, db: Database, qwen: Any = None,
                 run_id: Optional[str] = None) -> None:
        self.config = config
        self.db = db
        self.qwen = qwen
        self.run_id = run_id
        self.dedupe = Deduplicator(db, qwen)
        self._client = httpx.Client(
            timeout=config.discovery.request_timeout_seconds,
            headers={"User-Agent": config.discovery.user_agent,
                     "Accept": "application/json, text/html;q=0.9"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def targets(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for source, tokens in (self.config.discovery.boards or {}).items():
            if source not in ADAPTERS:
                log.warning("unknown discovery source in config", extra={"source": source})
                continue
            for token in tokens or []:
                pairs.append((source, token))
        for url in self.config.discovery.seed_urls or []:
            pairs.append(("generic_url", url))
        return pairs

    def run(self, targets: Optional[Iterable[tuple[str, str]]] = None) -> DiscoveryReport:
        report = DiscoveryReport()
        for source, target in (targets if targets is not None else self.targets()):
            adapter_cls = ADAPTERS.get(source)
            if adapter_cls is None:
                continue
            adapter = adapter_cls(self.config.discovery, client=self._client)
            try:
                for job in adapter.discover(target):
                    self._ingest(job, source, report)
            except Exception as exc:
                report.errors += 1
                log.error("discovery adapter failed",
                          extra={"source": source, "target": target, "error": str(exc)[:300]})
                self.db.record_error(self.run_id, None, f"discovery:{source}", exc)
        log.info("discovery complete", extra={"discovered": report.discovered,
                                              "duplicates": report.duplicates,
                                              "errors": report.errors})
        return report

    def _ingest(self, job: Job, source: str, report: DiscoveryReport) -> None:
        if not job.canonical_apply_url or not job.title or not job.company:
            return
        if not DiscoveryAdapter.matches_targets(job.title, self.config.discovery.target_roles):
            return
        verdict = self.dedupe.check(job)
        if verdict.is_duplicate:
            report.duplicates += 1
            if self.run_id:
                self.db.bump_run(self.run_id, "duplicates")
            log.debug("duplicate job skipped",
                      extra={"title": job.title, "company": job.company, "rule": verdict.rule})
            return
        job_id, is_new = self.db.upsert_job(job)
        if is_new:
            report.discovered += 1
            report.new_job_ids.append(job_id)
            report.by_source[source] = report.by_source.get(source, 0) + 1
            if self.run_id:
                self.db.bump_run(self.run_id, "discovered")
            log.info("discovered job", extra={"job_id": job_id, "company": job.company,
                                              "title": job.title, "source": source})
        else:
            report.duplicates += 1
            if self.run_id:
                self.db.bump_run(self.run_id, "duplicates")
