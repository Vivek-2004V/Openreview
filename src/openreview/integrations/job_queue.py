"""Idempotent review job queue for GitHub pull request events per TRD Section 8."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from openreview.integrations.github_webhook import PullRequestEventData


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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempts: int = 0
    error_message: str | None = None
    report_summary: dict[str, Any] | None = None


class ReviewJobQueue:
    """Thread-safe, in-memory review job queue with deduplication and idempotency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ReviewJob] = {}
        self._queue: list[str] = []
        self._processed_deliveries: set[str] = set()

    @staticmethod
    def derive_idempotency_key(repo_full_name: str, pr_number: int, head_sha: str) -> str:
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
        key = self.derive_idempotency_key(pr_data.repo_full_name, pr_data.pr_number, pr_data.head_sha)

        with self._lock:
            # Check delivery deduplication
            if delivery_id in self._processed_deliveries:
                existing = self._jobs.get(key)
                if existing:
                    return existing, False

            # Check revision idempotency
            if key in self._jobs:
                existing = self._jobs[key]
                if existing.status in (JobStatus.PENDING, JobStatus.PROCESSING, JobStatus.COMPLETED):
                    return existing, False

            # Create new job
            job = ReviewJob(
                job_id=key,
                delivery_id=delivery_id,
                repo_full_name=pr_data.repo_full_name,
                pr_number=pr_data.pr_number,
                head_sha=pr_data.head_sha,
                base_sha=pr_data.base_sha,
                clone_url=pr_data.clone_url,
                status=JobStatus.PENDING,
            )
            self._jobs[key] = job
            self._queue.append(key)
            self._processed_deliveries.add(delivery_id)
            return job, True

    def claim_next_job(self) -> ReviewJob | None:
        """Atomically pop the next pending job and mark as processing."""
        with self._lock:
            while self._queue:
                job_id = self._queue.pop(0)
                job = self._jobs.get(job_id)
                if job and job.status == JobStatus.PENDING:
                    job.status = JobStatus.PROCESSING
                    job.attempts += 1
                    return job
            return None

    def complete_job(self, job_id: str, report_summary: dict[str, Any] | None = None) -> None:
        """Mark a job as successfully completed."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.COMPLETED
                job.report_summary = report_summary

    def fail_job(self, job_id: str, error_message: str) -> None:
        """Mark a job as failed with an error message."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = error_message

    def get_job(self, job_id: str) -> ReviewJob | None:
        """Retrieve job by id."""
        with self._lock:
            return self._jobs.get(job_id)

    @property
    def queue_size(self) -> int:
        """Count of pending jobs in queue."""
        with self._lock:
            return len(self._queue)
