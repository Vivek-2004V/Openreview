"""Review engine orchestrating git diff, static scanners, and optional local AI."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from openreview.adapters.ai_base import ModelProvider
from openreview.adapters.base import ScannerAdapter
from openreview.adapters.eslint import ESLintAdapter
from openreview.adapters.rea import ReaAdapter
from openreview.adapters.ruff import RuffAdapter
from openreview.config import OpenReviewConfig
from openreview.repo.diff_models import DiffRequest
from openreview.schemas.models import ReviewReport

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
    """Orchestrates diff extraction, scanner runs, optional AI calls, and report assembly.

    Uses CompleteReviewOrchestrator under the hood so the CLI, workers, and orchestrator
    share one unified 16-node review pipeline with strict line-level hunk filtering.
    """

    def __init__(
        self,
        repo_root: Path | str,
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
        self.ai_provider = ai_provider

        # Shared orchestrator pipeline
        from openreview.review.orchestrator import CompleteReviewOrchestrator

        has_explicit_rea = "rea" in [s.strip().lower() for s in config.scanners.enabled]
        self._orchestrator = CompleteReviewOrchestrator(
            repo_root=self.repo_root,
            config=self.config,
            scanners=self.scanners,
            ai_provider=self.ai_provider,
            include_rea=False if scanners is not None else has_explicit_rea,
        )

    def execute_review(
        self,
        diff_request: DiffRequest | None = None,
        specific_files: Sequence[str] | None = None,
    ) -> ReviewReport:
        """Run complete review pipeline and return validated ReviewReport."""
        result = self._orchestrator.run_pipeline(
            diff_request=diff_request,
            generate_proposals=False,
            specific_files=specific_files,
        )
        return result.report
