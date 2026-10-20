"""Isolated review worker executing review tasks in ephemeral workspaces."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from openreview.config import OpenReviewConfig
from openreview.integrations.job_queue import JobStatus, ReviewJob, ReviewJobQueue
from openreview.repo.diff_models import DiffRequest, DiffTarget
from openreview.repo.git import run_git_command
from openreview.review.engine import ReviewEngine
from openreview.schemas.models import ReviewReport

logger = logging.getLogger(__name__)


class ReviewWorker:
    """Worker that pulls review jobs from queue and executes them in isolated sandboxes."""

    def __init__(
        self,
        queue: ReviewJobQueue,
        config: OpenReviewConfig,
        repo_fetcher: Callable[[ReviewJob, Path], None] | None = None,
    ) -> None:
        self.queue = queue
        self.config = config
        self._repo_fetcher = repo_fetcher or self._default_fetch_repo

    @staticmethod
    def _default_fetch_repo(job: ReviewJob, target_dir: Path) -> None:
        """Fetch repository commit range into target directory."""
        # Standard git clone or fetch
        run_git_command(["clone", "--no-checkout", job.clone_url, str(target_dir)])
        run_git_command(["checkout", job.head_sha], cwd=target_dir)

    def process_job(self, job: ReviewJob) -> ReviewReport:
        """Execute a review job inside an ephemeral directory and return ReviewReport."""
        with tempfile.TemporaryDirectory(prefix="openreview_worker_") as temp_dir:
            sandbox_path = Path(temp_dir).resolve()
            try:
                # 1. Fetch repo into sandbox
                self._repo_fetcher(job, sandbox_path)

                # 2. Configure review engine
                engine = ReviewEngine(repo_root=sandbox_path, config=self.config)
                diff_req = DiffRequest(
                    repo_root=sandbox_path,
                    target=DiffTarget.COMMITS_RANGE,
                    base_ref=job.base_sha,
                    head_ref=job.head_sha,
                    max_files=self.config.review.max_files,
                    max_file_bytes=self.config.review.max_file_bytes,
                )

                # 3. Execute review
                report = engine.execute_review(diff_request=diff_req)
                self.queue.complete_job(job.job_id, report.to_dict()["summary"])
                return report
            except Exception as exc:
                self.queue.fail_job(job.job_id, str(exc))
                logger.error("Review worker failed on job %s: %s", job.job_id, exc)
                raise

    def process_next_job(self) -> ReviewReport | None:
        """Claim and process the next pending job in the queue if available."""
        job = self.queue.claim_next_job()
        if not job:
            return None
        return self.process_job(job)
