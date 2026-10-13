"""Fixtures for the end-to-end ingestion tests: a real keyed fabric under ``E2EProcessDriver`` and a hand-built HQ
store in the shape the pushdown orchestrator writes (``tests/mycelic/test_collective_pushdown.py``).

Nothing here skips: a missing nats-server fails the test (``docs/collective/E2E-INGEST.md`` M11). Every world is
synthetic; no record text exists in these fixtures (the verdict bodies are the Boundary's egress artifacts, built
with the pack's own schema and checked with ``check_artifact``).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mycelic.collective.detect.store import CollectiveStore, ConclusionRow, QuestionRow, RouteRow
from mycelic.collective.edge.egress import check_artifact, verdict_id_of
from mycelic.collective.evaluate.baselines import org_for_sites
from mycelic.collective.jsonio import canonical_bytes, sha256_hex
from mycelic.collective.packs.loader import load_pack
from mycelic.collective.pushdown import gate
from mycelic.collective.pushdown.questions import build_question, question_window, render_text
from mycelic.models import content_hash
from mycelic.sdk import MycelicClient
from research.e2e_ingest import drills
from research.e2e_ingest.publish_api import AGENT_ID, ORG_ID
from research.e2e_ingest.suites import SUITES

ROOT = Path(__file__).resolve().parents[2]
LOCAL_NATS = Path("/root/.local/bin/nats-server")
DQ = load_pack("device_quality")
AS_OF = "2026-04-26"                       # closes 2026-W15 for the device pack: the window is 2026-W10..2026-W15
SNAPSHOT_WEEK = "2026-W15"
SITES = ("site-a", "site-b", "site-c", "site-d")
RUN_ID = "e2e-test-run"
ENTERPRISE = "t"


def nats_server_bin() -> str:
    """``MYCELIC_NATS_SERVER_BIN``, then PATH, then the sandbox's local install; a miss is a failure, never a skip."""
    for candidate in (os.environ.get("MYCELIC_NATS_SERVER_BIN"), shutil.which("nats-server"), str(LOCAL_NATS)):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise AssertionError("nats-server not found: set MYCELIC_NATS_SERVER_BIN or install it; tests/e2e_ingest never "
                         "skip for a missing broker (E2E-INGEST.md M11)")


def nats_binary_version(nats_bin: str) -> str:
    """The numeric part of ``nats-server --version`` (``v2.10.22`` -> ``2.10.22``)."""
    import subprocess

    out = subprocess.run([nats_bin, "--version"], capture_output=True, text=True, timeout=20, check=True).stdout
    return out.strip().split()[-1].lstrip("v")


def suite_rows(sha: str) -> list[dict[str, Any]]:
    """M2: one green, junit-bound row per suite at its floor, naming ``sha`` (what ``run --suites DIR`` embeds)."""
    return [{"suite": s.name, "slug": s.slug, "group": s.group, "floor": s.floor, "sha": sha, "tests": s.floor,
             "failures": 0, "errors": 0, "skipped": 0, "skipped_in_scope": 0 if s.zero_skip_scope is not None else None,
             "duration_s": 1.0, "junit_sha256": "0" * 64, "junit_file": f"{s.slug}.xml", "junit_present": True,
             "junit_bound": True, "exit_code": 0} for s in SUITES]


def expected_event_id(idempotency_key: str, agent_id: str = AGENT_ID) -> str:
    """``service.py:1220``: the agent's event id for an idempotency key."""
    return f"evt_{content_hash(agent_id, 'event', idempotency_key)[:22]}"


def expected_memory_id(idempotency_key: str, agent_id: str = AGENT_ID) -> str:
    """``service.py:1232``: the embedded memory's id for an event's idempotency key."""
    return f"mem_{content_hash(agent_id, 'event-memory', idempotency_key)[:22]}"


class LiveFabric:
    """A real nats-server and service under :class:`drills.E2EProcessDriver`, with ``hq`` registered in
    ``emergence``; ``admin`` and ``hq`` are SDK clients."""

    def __init__(self, workdir: Path) -> None:
        self.driver = drills.E2EProcessDriver(workdir=workdir, nats_bin=nats_server_bin())
        self.driver.up()
        try:
            self.admin = MycelicClient(self.driver.base_url, self.driver.admin_token)
            registered = self.admin.register_agent(agent_id=AGENT_ID, enterprise=ORG_ID)
            self.agent_path: str = registered["agent"]["path"]
            self.hq = MycelicClient(self.driver.base_url, registered["api_key"])
        except BaseException:
            self.driver.down()
            raise

    def status(self) -> dict[str, Any]:
        return self.admin.status()

    def counts(self) -> dict[str, Any]:
        return drills.fabric_counts(self.status())

    def wait_idle(self, timeout: float = 120.0) -> dict[str, Any]:
        return drills.wait_idle(self.admin, timeout)

    def signature_failures(self) -> int:
        return drills.signature_failures(self.driver.base_url, self.driver.admin_token)

    def events(self) -> list[dict[str, Any]]:
        return self.admin.events(ORG_ID, limit=1000)

    def close(self) -> None:
        self.driver.down()


# --------------------------------------------------------------------------------------------------- the HQ store

class Clock:
    """An ISO 8601 clock the store accepts, one second later at every call, so ``created_at`` orders the rows."""

    def __init__(self, start: str = f"{AS_OF}T08:00:00Z") -> None:
        self._t = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    def __call__(self) -> str:
        self._t += timedelta(seconds=1)
        return self._t.strftime("%Y-%m-%dT%H:%M:%SZ")


def verdict_body(pack: Any, question: dict[str, Any], site: str, kind: str) -> dict[str, Any]:
    """A valid site verdict for ``question`` (``edge/egress.py`` verdict schema and cross-field checks)."""
    confirm = kind == "confirm"
    body: dict[str, Any] = {
        "schema_version": 1, "pack": pack.id, "pack_hash": pack.config_hash, "site": site,
        "question_id": question["question_id"], "verdict": kind, "reason": None, "window": dict(question["window"]),
        "support_bucket": "3-9" if confirm else None, "roots_bucket": "3-9" if confirm else None,
        "reporters_bucket": "3-9" if confirm else None, "entity_records_bucket": None if confirm else "3-9",
        "newest_week": question["window"]["end_week"] if confirm else None, "truncated": False, "quality": "ok",
        "secret_mode": "seeded-demo"}
    body["verdict_id"] = verdict_id_of(body)
    body["evidence_ref"] = sha256_hex(f"{site}|{question['question_id']}|{kind}")[:16]
    problem = check_artifact(pack, site, "verdict", body)
    if problem is not None:
        raise AssertionError(f"fixture verdict fails the schema at {problem}")
    return body


@dataclass
class StoreSpec:
    path: Path
    pack: Any
    run_id: str | None
    questions: list[dict[str, Any]] = field(default_factory=list)        # question bodies, in creation order
    routes: dict[str, tuple[str, ...]] = field(default_factory=dict)       # question id -> routed sites (rank order)
    statuses: dict[str, str] = field(default_factory=dict)                 # question id -> the gate's status
    verdict_sources: dict[str, dict[str, str]] = field(default_factory=dict)   # question id -> site -> source
    verdict_rows: int = 0
    conclusions: int = 0

    @property
    def publishes(self) -> int:
        """N: one POST per ``pd_verdicts`` row plus one per ``pd_conclusions`` row (section 3.5)."""
        return self.verdict_rows + self.conclusions

    def conclusion_id(self, question_id: str) -> str:
        return "c-" + question_id[:32]


def build_hq_store(tmp: Path, *, questions: int = 20, sites_per_question: int = 3, hq_record_every: int = 7,
                   run_id: str | None = RUN_ID, save_run: bool = True) -> StoreSpec:
    """An HQ store in the orchestrator's shape: ``questions`` questions on distinct products, each routed to
    ``sites_per_question`` sites (two contributing, the rest siblings), one verdict row per routed site (confirm,
    confirm, refute; every ``hq_record_every``-th question has an HQ error record instead of its second verdict),
    one conclusion version each with the gate's own status and body, and the detection run whose candidates carry
    the snapshot week. ``run_id`` None leaves ``pd_questions.run_id`` empty; ``save_run`` False saves no run."""
    org = org_for_sites(list(SITES), ENTERPRISE)
    clock = Clock()
    path = tmp / "hqdb" / "collective.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = StoreSpec(path=path, pack=DQ, run_id=run_id)
    store = CollectiveStore(path, DQ, org, clock=clock)
    try:
        window = question_window(DQ, as_of=AS_OF)
        candidates = []
        for i in range(questions):
            entity = f"SD-{i + 1}"
            q = build_question(DQ, entity_type="product", entity_id=entity, predicate="crack", window=window,
                               as_of=AS_OF)
            qid = q["question_id"]
            route = tuple(SITES[(i + j) % len(SITES)] for j in range(sites_per_question))
            roles = {s: ("contributing" if j < 2 else "sibling") for j, s in enumerate(route)}
            store.add_question(QuestionRow(question_id=qid, candidate_key=q["candidate_key"], run_id=run_id,
                                           template_id=q["template_id"], as_of=AS_OF,
                                           window_start=window["start_week"], window_end=window["end_week"],
                                           pack_hash=DQ.config_hash, body=canonical_bytes(q),
                                           display_text=render_text(DQ, q), created_at=clock()),
                               tuple(RouteRow(qid, s, roles[s], rank) for rank, s in enumerate(route, start=1)))
            records = []
            sources: dict[str, str] = {}
            for j, site in enumerate(route):
                if hq_record_every and i % hq_record_every == hq_record_every - 1 and j == 1:
                    body: dict[str, Any] = gate.hq_record_body(qid, site, "error")
                    source, verdict, reason = "hq", "unknown", "error"
                else:
                    verdict = "refute" if j == len(route) - 1 else "confirm"
                    body = verdict_body(DQ, q, site, verdict)
                    source, reason = "site", None
                outcome = store.append_verdict(question_id=qid, site=site, source=source, verdict=verdict,
                                               reason=reason, body=canonical_bytes(body), received_as_of=AS_OF)
                if outcome != "accepted":
                    raise AssertionError(f"fixture verdict for {site} was {outcome}")
                records.append(gate.VerdictRecord(site, 1, source, body, AS_OF))
                sources[site] = source
                spec.verdict_rows += 1
            result = gate.evaluate(DQ, q, roles, records, as_of=AS_OF)
            doc = canonical_bytes(result.to_dict())
            store.add_conclusion(ConclusionRow(conclusion_id=spec.conclusion_id(qid), version=1, question_id=qid,
                                               as_of=AS_OF, status=result.status, body=doc, sha256=sha256_hex(doc),
                                               created_at=clock()))
            spec.conclusions += 1
            spec.questions.append(q)
            spec.routes[qid] = route
            spec.statuses[qid] = result.status
            spec.verdict_sources[qid] = sources
            candidates.append({"key": q["candidate_key"], "entity_type": "product", "entity_id": entity,
                               "predicate": "crack", "run_channel": "X", "channels": ["detector"],
                               "detectors": ["burst"], "first_candidate_week": "2026-W13",
                               "candidate_weeks": ["2026-W13", "2026-W14", "2026-W15"],
                               "alert_weeks": [SNAPSHOT_WEEK], "detection_week": SNAPSHOT_WEEK,
                               "decision_unit": ENTERPRISE, "config_hash": DQ.config_hash,
                               "detector_hash": DQ.detector_hash, "org_hash": org.org_hash,
                               "snapshot": {"week": SNAPSHOT_WEEK, "as_of": AS_OF, "score": 1.0,
                                            "window": [window["start_week"], window["end_week"]],
                                            "supporting_sites": list(route[:2]), "decision_unit": ENTERPRISE},
                               "rule": None})
        if save_run and run_id is not None:
            store.save_run({"run_id": run_id, "run_channel": "X", "as_of": AS_OF, "last_week": window["end_week"],
                            "tie_salt": "e2e-test", "config_hash": DQ.config_hash, "detector_hash": DQ.detector_hash,
                            "org_hash": org.org_hash, "candidates": candidates, "rule_hits": []})
    finally:
        store.close()
    return spec
