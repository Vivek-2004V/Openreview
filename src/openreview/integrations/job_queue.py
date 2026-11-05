"""Idempotent, crash-resilient review job queue for GitHub pull request events per TRD Section 8.

Supports both in-memory and persistent SQLite storage with durable recovery across restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from openreview.integrations.github_webhook import PullRequestEventData

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Lifecycle status of a review job in queue."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE_SKIPPED = "duplicate_skipped"


@dataclass
class ReviewJob:
    """A single queued review execution task."""

    job_id: str
    delivery_id: str
    repo_full_name: str
    pr_number: int
    head_sha: str
    base_sha: str
    clone_url: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    attempts: int = 0
    error_message: str | None = None
    report_summary: dict[str, Any] | None = None
    head_ref: str | None = None
    base_ref: str | None = None
    installation_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize job to dictionary."""
        return {
            "job_id": self.job_id,
            "delivery_id": self.delivery_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "clone_url": self.clone_url,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts,
            "error_message": self.error_message,
            "report_summary": self.report_summary,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "installation_id": self.installation_id,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ReviewJob:
        """Construct ReviewJob from SQLite row."""
        data = dict(row)
        summary = None
        if data.get("report_summary"):
            try:
                summary = json.loads(data["report_summary"])
            except (json.JSONDecodeError, TypeError):
                summary = None

        return cls(
            job_id=data["job_id"],
            delivery_id=data["delivery_id"],
            repo_full_name=data["repo_full_name"],
            pr_number=data["pr_number"],
            head_sha=data["head_sha"],
            base_sha=data["base_sha"],
            clone_url=data["clone_url"],
            status=JobStatus(data["status"]),
            created_at=data["created_at"],
            updated_at=data.get("updated_at", data["created_at"]),
            attempts=data["attempts"],
            error_message=data.get("error_message"),
            report_summary=summary,
            head_ref=data.get("head_ref"),
            base_ref=data.get("base_ref"),
            installation_id=data.get("installation_id"),
        )


class ReviewJobQueue:
    """Thread-safe, crash-resilient review job queue with deduplication and SQLite persistence."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        auto_recover: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._db_path = str(db_path) if db_path is not None else ":memory:"
        self.is_persistent = self._db_path != ":memory:"

        if self.is_persistent:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit mode, transactions handled explicitly
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

        # In-memory mirror for fast inspection and backward compatibility
        self._jobs: dict[str, ReviewJob] = {}
        self._queue: list[str] = []
        self._processed_deliveries: set[str] = set()
        self._sync_memory_cache()

        if auto_recover and self.is_persistent:
            recovered = self.recover_stale_jobs()
            if recovered:
                logger.info(
                    "Durable job recovery restored %d uncompleted jobs to PENDING",
                    len(recovered),
                )

    def _init_db(self) -> None:
        """Initialize database schema with WAL mode and indices."""
        with self._lock:
            cur = self._conn.cursor()
            if self.is_persistent:
                cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    delivery_id TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    clone_url TEXT NOT NULL,
                    head_ref TEXT,
                    base_ref TEXT,
                    installation_id INTEGER,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    report_summary TEXT
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_delivery ON jobs(delivery_id);"
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL
                );
                """
            )

    def _sync_memory_cache(self) -> None:
        """Populate internal memory mirrors from SQLite state."""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM jobs ORDER BY created_at ASC;")
        self._jobs.clear()
        self._queue.clear()
        for row in cur.fetchall():
            job = ReviewJob.from_row(row)
            self._jobs[job.job_id] = job
            if job.status == JobStatus.PENDING:
                self._queue.append(job.job_id)

        cur.execute("SELECT delivery_id FROM processed_deliveries;")
        self._processed_deliveries = {r["delivery_id"] for r in cur.fetchall()}

    @staticmethod
    def derive_idempotency_key(
        repo_full_name: str, pr_number: int, head_sha: str
    ) -> str:
        """Derive canonical idempotency key for PR commit revision."""
        return f"{repo_full_name}#{pr_number}@{head_sha}"

    def enqueue_pr_event(
        self,
        pr_data: PullRequestEventData,
        delivery_id: str,
    ) -> tuple[ReviewJob, bool]:
        """Enqueue review job idempotently.

        Returns (job, is_new).
        If the exact delivery or PR revision has already been processed or queued,
        returns existing job with is_new=False.
        """
        key = self.derive_idempotency_key(
            pr_data.repo_full_name, pr_data.pr_number, pr_data.head_sha
        )
        now_iso = datetime.now(UTC).isoformat()

        with self._lock:
            cur = self._conn.cursor()

            # 1. Delivery deduplication
            if delivery_id in self._processed_deliveries:
                cur.execute("SELECT * FROM jobs WHERE job_id = ?;", (key,))
                row = cur.fetchone()
                if row:
                    job = ReviewJob.from_row(row)
                    self._jobs[key] = job
                    return job, False

            # 2. Revision idempotency check
            cur.execute("SELECT * FROM jobs WHERE job_id = ?;", (key,))
            row = cur.fetchone()
            if row:
                job = ReviewJob.from_row(row)
                if job.status in (
                    JobStatus.PENDING,
                    JobStatus.PROCESSING,
                    JobStatus.COMPLETED,
                ):
                    self._jobs[key] = job
                    return job, False

            # 3. Insert or update job
            cur.execute("BEGIN IMMEDIATE;")
            try:
                cur.execute(
                    """
                    INSERT INTO processed_deliveries (delivery_id, received_at)
                    VALUES (?, ?)
                    ON CONFLICT(delivery_id) DO NOTHING;
                    """,
                    (delivery_id, now_iso),
                )
                cur.execute(
                    """
                    INSERT INTO jobs (
                        job_id, delivery_id, repo_full_name, pr_number,
                        head_sha, base_sha, clone_url, head_ref, base_ref,
                        installation_id, status, created_at, updated_at,
                        attempts, error_message, report_summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        delivery_id = excluded.delivery_id,
                        status = 'pending',
                        updated_at = excluded.updated_at,
                        error_message = NULL;
                    """,
                    (
                        key,
                        delivery_id,
                        pr_data.repo_full_name,
                        pr_data.pr_number,
                        pr_data.head_sha,
                        pr_data.base_sha,
                        pr_data.clone_url,
                        pr_data.head_ref,
                        pr_data.base_ref,
                        pr_data.installation_id,
                        JobStatus.PENDING.value,
                        now_iso,
                        now_iso,
                        0,
                        None,
                        None,
                    ),
                )
                cur.execute("COMMIT;")
            except sqlite3.Error:
                cur.execute("ROLLBACK;")
                raise

            job = ReviewJob(
                job_id=key,
                delivery_id=delivery_id,
                repo_full_name=pr_data.repo_full_name,
                pr_number=pr_data.pr_number,
                head_sha=pr_data.head_sha,
                base_sha=pr_data.base_sha,
                clone_url=pr_data.clone_url,
                head_ref=pr_data.head_ref,
                base_ref=pr_data.base_ref,
                installation_id=pr_data.installation_id,
                status=JobStatus.PENDING,
                created_at=now_iso,
                updated_at=now_iso,
            )
            self._jobs[key] = job
            if key not in self._queue:
                self._queue.append(key)
            self._processed_deliveries.add(delivery_id)
            return job, True

    def claim_next_job(self) -> ReviewJob | None:
        """Atomically claim the oldest pending job and mark as processing."""
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE;")
            try:
                cur.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                    ORDER BY created_at ASC
                    LIMIT 1;
                    """,
                    (JobStatus.PENDING.value,),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute("COMMIT;")
                    return None

                job_id = row["job_id"]
                new_attempts = row["attempts"] + 1
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = ?, attempts = ?, updated_at = ?
                    WHERE job_id = ?;
                    """,
                    (JobStatus.PROCESSING.value, new_attempts, now_iso, job_id),
                )
                cur.execute("COMMIT;")
            except sqlite3.Error:
                cur.execute("ROLLBACK;")
                raise

            if job_id in self._jobs:
                job = self._jobs[job_id]
                job.status = JobStatus.PROCESSING
                job.attempts = new_attempts
                job.updated_at = now_iso
            else:
                job = ReviewJob.from_row(row)
                job.status = JobStatus.PROCESSING
                job.attempts = new_attempts
                job.updated_at = now_iso
                self._jobs[job_id] = job
            if job_id in self._queue:
                self._queue.remove(job_id)
            return job

    def complete_job(
        self, job_id: str, report_summary: dict[str, Any] | None = None
    ) -> None:
        """Mark a job as successfully completed."""
        now_iso = datetime.now(UTC).isoformat()
        summary_json = (
            json.dumps(report_summary) if report_summary is not None else None
        )
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE jobs
                SET status = ?, report_summary = ?, updated_at = ?
                WHERE job_id = ?;
                """,
                (JobStatus.COMPLETED.value, summary_json, now_iso, job_id),
            )
            if job_id in self._jobs:
                self._jobs[job_id].status = JobStatus.COMPLETED
                self._jobs[job_id].report_summary = report_summary
                self._jobs[job_id].updated_at = now_iso
            if job_id in self._queue:
                self._queue.remove(job_id)

    def fail_job(self, job_id: str, error_message: str) -> None:
        """Mark a job as failed with an error message."""
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                UPDATE jobs
                SET status = ?, error_message = ?, updated_at = ?
                WHERE job_id = ?;
                """,
                (JobStatus.FAILED.value, error_message, now_iso, job_id),
            )
            if job_id in self._jobs:
                self._jobs[job_id].status = JobStatus.FAILED
                self._jobs[job_id].error_message = error_message
                self._jobs[job_id].updated_at = now_iso
            if job_id in self._queue:
                self._queue.remove(job_id)

    def recover_stale_jobs(self, max_retries: int = 3) -> list[ReviewJob]:
        """Durable recovery: recovers jobs stuck in PROCESSING after process crash or restart.

        Jobs with attempts < max_retries are reset to PENDING so workers resume processing.
        Jobs with attempts >= max_retries are marked as FAILED.
        """
        now_iso = datetime.now(UTC).isoformat()
        recovered_jobs: list[ReviewJob] = []

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM jobs WHERE status = ?;", (JobStatus.PROCESSING.value,)
            )
            rows = cur.fetchall()

            cur.execute("BEGIN IMMEDIATE;")
            try:
                for row in rows:
                    job = ReviewJob.from_row(row)
                    if job.attempts < max_retries:
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status = ?, updated_at = ?
                            WHERE job_id = ?;
                            """,
                            (JobStatus.PENDING.value, now_iso, job.job_id),
                        )
                        job.status = JobStatus.PENDING
                        job.updated_at = now_iso
                        self._jobs[job.job_id] = job
                        if job.job_id not in self._queue:
                            self._queue.append(job.job_id)
                        recovered_jobs.append(job)
                    else:
                        cur.execute(
                            """
                            UPDATE jobs
                            SET status = ?, error_message = ?, updated_at = ?
                            WHERE job_id = ?;
                            """,
                            (
                                JobStatus.FAILED.value,
                                "Process terminated unexpectedly; max retries exceeded during recovery",
                                now_iso,
                                job.job_id,
                            ),
                        )
                        job.status = JobStatus.FAILED
                        job.error_message = "Process terminated unexpectedly; max retries exceeded during recovery"
                        job.updated_at = now_iso
                        self._jobs[job.job_id] = job
                cur.execute("COMMIT;")
            except sqlite3.Error:
                cur.execute("ROLLBACK;")
                raise

        return recovered_jobs

    def get_job(self, job_id: str) -> ReviewJob | None:
        """Retrieve job by id."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,))
            row = cur.fetchone()
            if row:
                job = ReviewJob.from_row(row)
                self._jobs[job_id] = job
                return job
            return None

    def get_all_jobs(self) -> list[ReviewJob]:
        """Retrieve all recorded review jobs ordered by creation time."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM jobs ORDER BY created_at ASC;")
            return [ReviewJob.from_row(r) for r in cur.fetchall()]

    def get_jobs_by_status(self, status: JobStatus) -> list[ReviewJob]:
        """Retrieve jobs filtered by status."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC;",
                (status.value,),
            )
            return [ReviewJob.from_row(r) for r in cur.fetchall()]

    @property
    def queue_size(self) -> int:
        """Count of pending jobs currently awaiting worker processing."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ?;",
                (JobStatus.PENDING.value,),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0

    def clear(self) -> None:
        """Clear all queued jobs and deliveries (useful for testing)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM jobs;")
            cur.execute("DELETE FROM processed_deliveries;")
            self._jobs.clear()
            self._queue.clear()
            self._processed_deliveries.clear()

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()
