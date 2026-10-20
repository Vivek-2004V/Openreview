"""Review engine orchestrating git diff, static scanners, and optional local AI."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from openreview.adapters.ai_base import (
    AIReviewRequest,
    AIStatus,
    ModelProvider,
)
from openreview.adapters.base import ScannerAdapter, ScannerContext, ScannerStatus
from openreview.adapters.eslint import ESLintAdapter
from openreview.adapters.ollama import OllamaAdapter
from openreview.adapters.rea import ReaAdapter
from openreview.adapters.ruff import RuffAdapter
from openreview.config import OpenReviewConfig
from openreview.repo.diff_models import DiffRequest, DiffTarget
from openreview.repo.diff_parser import FileDiff, ParsedDiff, extract_git_diff
from openreview.repo.git import GitRepoInfo, inspect_git_status
from openreview.review.ai_validator import validate_and_convert_ai_suggestions
from openreview.review.context import select_review_context
from openreview.schemas.models import (
    Finding,
    FindingOrigin,
    ReviewReport,
    ReviewSummary,
)
from openreview.security.redaction import redact_context_blocks

logger = logging.getLogger(__name__)


def build_default_scanners(enabled_names: Sequence[str]) -> list[ScannerAdapter]:
    """Instantiate scanner adapters based on configuration."""
    adapters: list[ScannerAdapter] = []
    for name in enabled_names:
        norm = name.strip().lower()
        if norm == "ruff":
            adapters.append(RuffAdapter())
        elif norm == "eslint":
            adapters.append(ESLintAdapter())
        elif norm == "rea":
            adapters.append(ReaAdapter())
    return adapters


class ReviewEngine:
    """Orchestrates diff extraction, scanner runs, optional AI calls, and report assembly."""

    def __init__(
        self,
        repo_root: Path,
        config: OpenReviewConfig,
        scanners: list[ScannerAdapter] | None = None,
        ai_provider: ModelProvider | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.scanners = (
            scanners
            if scanners is not None
            else build_default_scanners(config.scanners.enabled)
        )
        if ai_provider is not None:
            self.ai_provider: ModelProvider | None = ai_provider
        elif config.ai.enabled:
            self.ai_provider = OllamaAdapter(
                endpoint=config.ai.endpoint,
                default_model=config.ai.model,
            )
        else:
            self.ai_provider = None

    def execute_review(
        self,
        diff_request: DiffRequest | None = None,
        specific_files: Sequence[str] | None = None,
    ) -> ReviewReport:
        """Run complete review pipeline and return validated ReviewReport."""
        repo_info = inspect_git_status(self.repo_root)

        # 1. Parse git diff
        if diff_request is None:
            target = DiffTarget.WORKING_TREE
            diff_request = DiffRequest(
                repo_root=self.repo_root,
                target=target,
                base_ref=self.config.review.base_ref,
                max_files=self.config.review.max_files,
                max_file_bytes=self.config.review.max_file_bytes,
            )

        parsed_diff = extract_git_diff(diff_request)
        reviewable_diffs = parsed_diff.reviewable_files

        if specific_files:
            spec_set = set(specific_files)
            reviewable_diffs = [d for d in reviewable_diffs if d.path in spec_set]

        target_file_paths = [d.path for d in reviewable_diffs]

        all_findings: list[Finding] = []
        scanner_failures: list[str] = []

        # 2. Run deterministic static scanners
        if target_file_paths and self.scanners:
            for scanner in self.scanners:
                filtered_files = scanner.filter_target_files(target_file_paths)
                if not filtered_files:
                    continue

                if not scanner.is_available():
                    # Scanner executable not found in PATH
                    continue

                ctx = ScannerContext(
                    repo_root=self.repo_root,
                    target_files=filtered_files,
                    timeout_seconds=self.config.scanners.timeout_seconds,
                )
                res = scanner.run(ctx)
                if res.is_successful:
                    all_findings.extend(res.findings)
                else:
                    scanner_failures.append(f"{scanner.name}: {res.status.value}")

        # 3. Run optional local AI review if enabled
        if self.config.ai.enabled and self.ai_provider is not None and reviewable_diffs:
            if self.ai_provider.is_available():
                # Extract bounded context
                context_blocks = select_review_context(
                    repo_root=self.repo_root,
                    reviewable_diffs=reviewable_diffs,
                    max_tokens=self.config.ai.max_context_tokens,
                )

                if context_blocks:
                    # Redact secrets if privacy enabled
                    if self.config.privacy.redact_secrets:
                        context_blocks, _ = redact_context_blocks(context_blocks)

                    ai_req = AIReviewRequest(
                        blocks=context_blocks,
                        model=self.config.ai.model,
                    )
                    ai_resp = self.ai_provider.generate_suggestions(ai_req)
                    if ai_resp.is_successful and ai_resp.suggestions:
                        ai_findings = validate_and_convert_ai_suggestions(
                            suggestions=ai_resp.suggestions,
                            reviewable_diffs=reviewable_diffs,
                            repo_root=self.repo_root,
                        )
                        all_findings.extend(ai_findings)
            else:
                logger.warning(
                    "AI review requested but provider '%s' is not reachable. Proceeding static-only.",
                    self.ai_provider.name,
                )

        # 4. Assemble summary and final ReviewReport
        report = ReviewReport.create(
            repo_root=str(self.repo_root),
            findings=all_findings,
            total_files_analyzed=len(target_file_paths),
            scanners_run=[s.name for s in self.scanners if s.is_available()],
            scanners_failed=scanner_failures,
            ai_enabled=bool(self.config.ai.enabled and self.ai_provider and self.ai_provider.is_available()),
            ai_model=self.config.ai.model if self.config.ai.enabled else None,
            base_ref=diff_request.base_ref,
            head_ref=diff_request.head_ref or repo_info.head_ref,
        )

        return report
