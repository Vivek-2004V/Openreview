"""Isolated review worker executing review tasks in ephemeral workspaces."""

from __future__ import annotations

import logging
import tempfile
import threading
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openreview.config import OpenReviewConfig
from openreview.integrations.github_client import GitHubClient, PRReportingOrchestrator
from openreview.integrations.job_queue import ReviewJob, ReviewJobQueue
from openreview.integrations.sandbox import (
    ContainerSandboxConfig,
    DockerSandboxRunner,
    LocalSandboxRunner,
    SandboxRunner,
    SandboxSecurityError,
)
from openreview.repo.git import run_git_command
from openreview.schemas.models import ReviewReport

logger = logging.getLogger(__name__)


class ReviewWorker:
    """Worker that pulls review jobs from queue and executes them in isolated sandboxes."""

    def __init__(
        self,
        queue: ReviewJobQueue,
        config: OpenReviewConfig,
        repo_fetcher: Callable[[ReviewJob, Path], None] | None = None,
        sandbox_runner: SandboxRunner | None = None,
        sandbox_config: ContainerSandboxConfig | None = None,
        require_container: bool | None = None,
        github_client: GitHubClient | None = None,
        app_auth: Any | None = None,
    ) -> None:
        self.queue = queue
        self.config = config
        self._repo_fetcher = repo_fetcher or self._default_fetch_repo
        self.sandbox_config = sandbox_config or ContainerSandboxConfig()

        if require_container is not None:
            self.require_container = require_container
        else:
            self.require_container = getattr(
                getattr(config, "sandbox", None), "require_container", False
            )

        self.github_client = github_client
        self.app_auth = app_auth

        if sandbox_runner is not None:
            self.sandbox_runner = sandbox_runner
        else:
            docker_runner = DockerSandboxRunner(config=self.sandbox_config)
            is_ready = docker_runner.is_available() and docker_runner.image_exists()
            if is_ready:
                self.sandbox_runner = docker_runner
            elif self.require_container:
                if not docker_runner.is_available():
                    raise SandboxSecurityError(
                        "Real container sandbox is required for ReviewWorker (OPENREVIEW_REQUIRE_CONTAINER=true), "
                        "but Docker runtime is not installed or daemon is unreachable. "
                        "Refusing to execute untrusted code on host."
                    )
                else:
                    raise SandboxSecurityError(
                        f"Real container sandbox is required for ReviewWorker (OPENREVIEW_REQUIRE_CONTAINER=true), "
                        f"but container image '{self.sandbox_config.image}' is missing in local Docker cache. "
                        f"Refusing to execute untrusted code on host."
                    )
            else:
                logger.warning(
                    "Docker runtime or container image '%s' not available. "
                    "ReviewWorker falling back to LocalSandboxRunner. "
                    "For untrusted/public PRs, a container isolation runtime (Docker/gVisor) is required.",
                    self.sandbox_config.image,
                )
                self.sandbox_runner = LocalSandboxRunner()

    def _get_client_for_job(self, job: ReviewJob) -> GitHubClient | None:
        """Resolve active GitHubClient for a job (either explicit client or via installation token)."""
        if self.github_client is not None:
            return self.github_client

        if self.app_auth is not None and job.installation_id is not None:
            try:
                return self.app_auth.get_installation_client(job.installation_id)
            except (ValueError, OSError) as exc:
                logger.warning(
                    "Could not obtain installation client for installation ID %s: %s",
                    job.installation_id,
                    exc,
                )
        return None

    @staticmethod
    def _default_fetch_repo(job: ReviewJob, target_dir: Path) -> None:
        """Fetch repository commit range into target directory."""
        run_git_command(["clone", "--no-checkout", job.clone_url, str(target_dir)])
        run_git_command(["checkout", job.head_sha], cwd=target_dir)

    def process_job(self, job: ReviewJob) -> ReviewReport:
        """Execute a review job inside an ephemeral directory and return ReviewReport."""
        with tempfile.TemporaryDirectory(prefix="openreview_worker_") as temp_dir:
            sandbox_path = Path(temp_dir).resolve()
            try:
                # 1. Fetch repo into staging directory (Host process holds credentials)
                self._repo_fetcher(job, sandbox_path)

                # 2. Execute review in real isolated sandbox (Docker/container or injected runner)
                # Host credentials and secrets are NEVER passed to the sandbox
                report = self.sandbox_runner.run_review_in_sandbox(
                    sandbox_path=sandbox_path,
                    base_sha=job.base_sha,
                    head_sha=job.head_sha,
                    config=self.config,
                    timeout_seconds=self.sandbox_config.timeout_seconds,
                )

                # 3. Post review results back to GitHub if client is configured
                client = self._get_client_for_job(job)
                if client is not None:
                    try:
                        orchestrator = PRReportingOrchestrator(client)
                        parsed_diff = None
                        try:
                            from openreview.repo.diff_models import (
                                DiffRequest,
                                DiffTarget,
                            )
                            from openreview.repo.diff_parser import extract_git_diff

                            diff_req = DiffRequest(
                                repo_root=sandbox_path,
                                target=DiffTarget.COMMITS,
                                base_ref=job.base_sha,
                                head_ref=job.head_sha,
                            )
                            parsed_diff = extract_git_diff(diff_req)
                        except (ValueError, OSError) as diff_err:
                            logger.debug(
                                "Could not parse diff for inline comments: %s", diff_err
                            )

                        orchestrator.report_review_result(
                            repo_full_name=job.repo_full_name,
                            pr_number=job.pr_number,
                            head_sha=job.head_sha,
                            report=report,
                            parsed_diff=parsed_diff,
                        )
                    except (urllib.error.URLError, ValueError, OSError) as report_err:
                        logger.error(
                            "Failed to post GitHub PR review report for %s: %s",
                            job.job_id,
                            report_err,
                        )

                # 4. Complete job in queue
                self.queue.complete_job(job.job_id, report.to_dict()["summary"])
                return report
            except Exception as exc:
                self.queue.fail_job(job.job_id, str(exc))
                logger.error("Review worker failed on job %s: %s", job.job_id, exc)

                client = self._get_client_for_job(job)
                if client is not None:
                    try:
                        client.create_check_run(
                            repo_full_name=job.repo_full_name,
                            head_sha=job.head_sha,
                            status="completed",
                            conclusion="failure",
                            output={
                                "title": "OpenReview AI Execution Failure",
                                "summary": f"Review failed during analysis: {exc}",
                            },
                        )
                    except (urllib.error.URLError, ValueError, OSError) as check_err:
                        logger.debug("Failed to post check run failure: %s", check_err)
                raise

    def process_next_job(self) -> ReviewReport | None:
        """Claim and process the next pending job in the queue if available."""
        job = self.queue.claim_next_job()
        if not job:
            return None
        return self.process_job(job)

    def run_worker_loop(
        self,
        poll_interval: float = 1.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Continuously poll queue and process review tasks until stop_event is signaled."""
        logger.info(
            "Starting OpenReview worker processing loop (poll interval: %.2fs)",
            poll_interval,
        )
        while stop_event is None or not stop_event.is_set():
            try:
                processed = self.process_next_job()
                if not processed:
                    time.sleep(poll_interval)
            except (ValueError, OSError) as exc:
                logger.error("Error in worker processing loop: %s", exc)
                time.sleep(poll_interval)
