"""SQLite persistence layer.

Every write the harness cares about is idempotent: re-running after a crash
re-reads state from here and continues, and a job that already has a
SUBMITTED/VERIFIED application can never be submitted again.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .models import Job, JobStatus, utcnow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1


class Database:
    """Thread-safe-ish SQLite wrapper.

    A single connection guarded by an RLock. The harness is single-threaded for
    browser work; the dashboard reads through its own Database instance.
    """

    def __init__(self, path: str | os.PathLike, timeout: float = 30.0) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self.migrate()

    # ---------------------------------------------------------------- core

    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_PATH.read_text())
            cur = self._conn.execute("SELECT MAX(version) AS v FROM schema_version")
            current = cur.fetchone()["v"]
            if current is None or current < SCHEMA_VERSION:
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?,?)",
                    (SCHEMA_VERSION, utcnow()),
                )
            self._conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- jobs

    def upsert_job(self, job: Job) -> tuple[str, bool]:
        """Insert a job, or return the existing one. Returns (job_id, is_new)."""
        existing = self.find_duplicate_job(job)
        if existing is not None:
            # Enrich fields discovered later (description, salary, posted date).
            updates, params = [], []
            for col in ("description", "posted_date", "salary_min", "salary_max",
                        "location", "remote_status", "ats_type", "employment_type"):
                new = getattr(job, col, None)
                if new not in (None, "") and not existing[col]:
                    updates.append(f"{col}=?")
                    params.append(new)
            if updates:
                params.append(existing["job_id"])
                self.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE job_id=?", params)
            return existing["job_id"], False

        row = job.to_row()
        cols = [
            "job_id", "company", "title", "normalized_title", "normalized_company",
            "location", "remote_status", "employment_type", "posted_date",
            "salary_min", "salary_max", "description", "description_hash",
            "source_url", "canonical_apply_url", "ats_type", "ats_job_key",
            "discovered_at", "status", "score", "score_breakdown", "score_reason",
            "scored_at", "raw",
        ]
        placeholders = ",".join("?" * len(cols))
        try:
            self.execute(
                f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders})",
                [row.get(c) for c in cols],
            )
        except sqlite3.IntegrityError:
            again = self.find_duplicate_job(job)
            if again:
                return again["job_id"], False
            raise
        return job.job_id, True

    def find_duplicate_job(self, job: Job) -> Optional[sqlite3.Row]:
        row = self.query_one("SELECT * FROM jobs WHERE job_id=?", (job.job_id,))
        if row:
            return row
        row = self.query_one(
            "SELECT * FROM jobs WHERE canonical_apply_url=?", (job.canonical_apply_url,)
        )
        if row:
            return row
        if job.ats_job_key:
            row = self.query_one(
                "SELECT * FROM jobs WHERE ats_type=? AND ats_job_key=?",
                (job.ats_type, job.ats_job_key),
            )
            if row:
                return row
        return self.query_one(
            "SELECT * FROM jobs WHERE normalized_company=? AND normalized_title=?",
            (job.normalized_company, job.normalized_title),
        )

    def get_job(self, job_id: str) -> Optional[Job]:
        row = self.query_one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return Job.from_row(row) if row else None

    def set_job_status(self, job_id: str, status: str) -> None:
        if status not in JobStatus.ALL:
            raise ValueError(f"unknown job status: {status}")
        self.execute("UPDATE jobs SET status=? WHERE job_id=?", (status, job_id))

    def save_score(self, job_id: str, score: int, breakdown: dict, reason: str,
                   status: str) -> None:
        self.execute(
            "UPDATE jobs SET score=?, score_breakdown=?, score_reason=?, scored_at=?, status=? "
            "WHERE job_id=?",
            (int(score), json.dumps(breakdown), reason, utcnow(), status, job_id),
        )

    def jobs_by_status(self, status: str | Iterable[str], limit: int = 100) -> list[Job]:
        statuses = [status] if isinstance(status, str) else list(status)
        marks = ",".join("?" * len(statuses))
        rows = self.query(
            f"SELECT * FROM jobs WHERE status IN ({marks}) "
            f"ORDER BY COALESCE(score,-1) DESC, discovered_at ASC LIMIT ?",
            [*statuses, limit],
        )
        return [Job.from_row(r) for r in rows]

    def unscored_jobs(self, limit: int = 200) -> list[Job]:
        rows = self.query(
            "SELECT * FROM jobs WHERE status='DISCOVERED' ORDER BY discovered_at ASC LIMIT ?",
            (limit,),
        )
        return [Job.from_row(r) for r in rows]

    def requeue_blocked(self, kinds: Iterable[str], min_score: int = 0) -> list[str]:
        """Put blocked jobs back in the queue, for the given blocker kinds.

        Never requeues a job that already has a submitted application, and never
        a blocker that needs the applicant personally -- the caller decides which
        kinds those are.
        """
        kinds = list(kinds)
        if not kinds:
            return []
        marks = ",".join("?" * len(kinds))
        rows = self.query(
            f"SELECT DISTINCT j.job_id FROM jobs j "
            f"JOIN applications a ON a.job_id = j.job_id "
            f"WHERE j.status = ? AND a.blocker_type IN ({marks}) "
            f"AND COALESCE(j.score, 0) >= ? "
            f"AND NOT EXISTS (SELECT 1 FROM applications s WHERE s.job_id = j.job_id "
            f"                AND s.status IN ('SUBMITTED','VERIFIED'))",
            [JobStatus.BLOCKED, *kinds, min_score],
        )
        job_ids = [row["job_id"] for row in rows]
        for job_id in job_ids:
            self.set_job_status(job_id, JobStatus.QUEUED)
        return job_ids

    def unverified_submissions(self, limit: int = 50) -> list[sqlite3.Row]:
        """Applications where Submit was clicked but nothing confirmed it."""
        return self.query(
            "SELECT a.application_id, a.job_id, a.submitted_at, a.error, "
            "j.company, j.title, j.canonical_apply_url "
            "FROM applications a JOIN jobs j ON j.job_id = a.job_id "
            "WHERE a.status = 'SUBMITTED' AND a.verified_at IS NULL "
            "ORDER BY a.submitted_at DESC LIMIT ?",
            (limit,),
        )

    def has_successful_application(self, job_id: str) -> bool:
        row = self.query_one(
            "SELECT 1 FROM applications WHERE job_id=? AND status IN ('SUBMITTED','VERIFIED')",
            (job_id,),
        )
        return row is not None

    def company_application_count(self, normalized_company: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM applications a JOIN jobs j ON j.job_id=a.job_id "
            "WHERE j.normalized_company=? AND a.status IN ('SUBMITTED','VERIFIED','READY_TO_SUBMIT')",
            (normalized_company,),
        )
        return int(row["n"]) if row else 0

    # -------------------------------------------------------- applications

    def create_application(self, job_id: str, run_id: str, dry_run: bool) -> int:
        """Start (or resume) an application attempt for a job.

        Refuses to create a second attempt if the job already succeeded.
        """
        if self.has_successful_application(job_id):
            raise DuplicateSubmission(f"job {job_id} already has a submitted application")
        prior = self.query_one(
            "SELECT MAX(attempt) AS a FROM applications WHERE job_id=?", (job_id,)
        )
        attempt = (prior["a"] or 0) + 1
        cur = self.execute(
            "INSERT INTO applications (job_id, run_id, status, attempt, dry_run, started_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, run_id, JobStatus.OPENED, attempt, int(dry_run), utcnow(), utcnow()),
        )
        return int(cur.lastrowid)

    def update_application(self, application_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "evidence" in fields and isinstance(fields["evidence"], (dict, list)):
            fields["evidence"] = json.dumps(fields["evidence"])
        if "checkpoint" in fields and isinstance(fields["checkpoint"], (dict, list)):
            fields["checkpoint"] = json.dumps(fields["checkpoint"])
        fields["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(
            f"UPDATE applications SET {sets} WHERE application_id=?",
            [*fields.values(), application_id],
        )

    def get_application(self, application_id: int) -> Optional[sqlite3.Row]:
        return self.query_one(
            "SELECT * FROM applications WHERE application_id=?", (application_id,)
        )

    def mark_submitted(self, application_id: int, ats_application_id: Optional[str] = None) -> None:
        self.update_application(
            application_id,
            status=JobStatus.SUBMITTED,
            submitted_at=utcnow(),
            ats_application_id=ats_application_id,
        )

    def mark_verified(self, application_id: int, evidence: dict,
                      ats_application_id: Optional[str] = None,
                      confirmation_text: Optional[str] = None,
                      confirmation_url: Optional[str] = None) -> None:
        self.update_application(
            application_id,
            status=JobStatus.VERIFIED,
            verified_at=utcnow(),
            evidence=evidence,
            ats_application_id=ats_application_id,
            confirmation_text=confirmation_text,
            confirmation_url=confirmation_url,
        )

    def applications_in_window(self, seconds: int) -> int:
        cutoff = time.time() - seconds
        rows = self.query(
            "SELECT submitted_at FROM applications WHERE submitted_at IS NOT NULL"
        )
        from datetime import datetime
        n = 0
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["submitted_at"]).timestamp()
            except (TypeError, ValueError):
                continue
            if ts >= cutoff:
                n += 1
        return n

    # -------------------------------------------------------- form answers

    def record_answer(self, application_id: Optional[int], job_id: Optional[str],
                      field_key: str, label: str, field_type: str, answer: Any,
                      confidence: float, source: str, resolver: str,
                      safe_to_submit: bool, filled: bool) -> None:
        self.execute(
            "INSERT INTO form_answers (application_id, job_id, field_key, label, field_type, "
            "answer, confidence, source, resolver, safe_to_submit, filled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (application_id, job_id, field_key, label, field_type,
             json.dumps(answer) if isinstance(answer, (list, dict)) else answer,
             float(confidence), source, resolver, int(bool(safe_to_submit)),
             int(bool(filled)), utcnow()),
        )

    def answers_for(self, application_id: int) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM form_answers WHERE application_id=? ORDER BY id", (application_id,)
        )

    # ----------------------------------------------------------- qwen logs

    def record_qwen_call(self, **fields: Any) -> None:
        fields.setdefault("created_at", utcnow())
        cols = ", ".join(fields)
        marks = ",".join("?" * len(fields))
        self.execute(f"INSERT INTO qwen_calls ({cols}) VALUES ({marks})", list(fields.values()))

    def cache_get(self, cache_key: str) -> Optional[dict]:
        row = self.query_one("SELECT response FROM qwen_cache WHERE cache_key=?", (cache_key,))
        if not row:
            return None
        self.execute("UPDATE qwen_cache SET hits=hits+1 WHERE cache_key=?", (cache_key,))
        try:
            return json.loads(row["response"])
        except ValueError:
            return None

    def cache_put(self, cache_key: str, function: str, model: str, response: dict) -> None:
        self.execute(
            "INSERT OR REPLACE INTO qwen_cache (cache_key, function, model, response, created_at, hits) "
            "VALUES (?,?,?,?,?,COALESCE((SELECT hits FROM qwen_cache WHERE cache_key=?),0))",
            (cache_key, function, model, json.dumps(response), utcnow(), cache_key),
        )

    # ------------------------------------------------------------- errors

    def record_error(self, run_id: Optional[str], job_id: Optional[str], stage: str,
                     exc: BaseException | str, kind: Optional[str] = None) -> None:
        if isinstance(exc, BaseException):
            kind = kind or type(exc).__name__
            message = str(exc)
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:]
        else:
            kind = kind or "message"
            message, tb = str(exc), None
        self.execute(
            "INSERT INTO errors (run_id, job_id, stage, kind, message, traceback, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, job_id, stage, kind, message, tb, utcnow()),
        )

    # ---------------------------------------------------------------- runs

    def start_run(self, run_id: str, mode: str, config_snapshot: dict) -> None:
        self.execute(
            "INSERT OR REPLACE INTO run_statistics (run_id, mode, started_at, state, config_snapshot) "
            "VALUES (?,?,?,?,?)",
            (run_id, mode, utcnow(), "RUNNING", json.dumps(config_snapshot)),
        )

    def bump_run(self, run_id: str, field: str, delta: int = 1) -> None:
        allowed = {
            "discovered", "scored", "skipped", "queued", "opened", "submitted",
            "verified", "blocked", "failed", "duplicates", "ready_to_submit",
            "qwen_requests", "qwen_tokens",
        }
        if field not in allowed:
            raise ValueError(f"unknown run stat: {field}")
        self.execute(
            f"UPDATE run_statistics SET {field}=COALESCE({field},0)+? WHERE run_id=?",
            (delta, run_id),
        )

    def add_run_cost(self, run_id: str, usd: float) -> None:
        self.execute(
            "UPDATE run_statistics SET qwen_cost_usd=COALESCE(qwen_cost_usd,0)+? WHERE run_id=?",
            (float(usd), run_id),
        )

    def set_run_context(self, run_id: str, **fields: Any) -> None:
        allowed = {"current_company", "current_role", "current_state", "state", "ended_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(
            f"UPDATE run_statistics SET {sets} WHERE run_id=?", [*fields.values(), run_id]
        )

    def get_run(self, run_id: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM run_statistics WHERE run_id=?", (run_id,))

    def latest_run(self) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM run_statistics ORDER BY started_at DESC LIMIT 1")

    # -------------------------------------------------------------- control

    def set_control(self, key: str, value: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO control (key, value, updated_at) VALUES (?,?,?)",
            (key, str(value), utcnow()),
        )

    def get_control(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM control WHERE key=?", (key,))
        return row["value"] if row else default


class DuplicateSubmission(Exception):
    """Raised when a job already has a submitted/verified application."""
