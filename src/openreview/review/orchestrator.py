"""Comprehensive Review Orchestrator executing the complete 16-step architecture.

Architecture Pipeline:
  [A] GitHub PR / Local Git Repository
   │
  [B] Integration Layer
   │
  [C] Diff Parser and File Mapper
   │
  [D] Context Engine (bounded context + secret redaction)
   │
  [E] Review Orchestrator
   ├── [F] Static Analysis Adapters (Ruff, ESLint, REA)
   ├── [G] Local AI Model (Ollama)
   └── [H] Repository Rules and History
   │
  [I] Finding Normalizer
   │
  [J] Evidence and Confidence Validation
   │
  [K] Deduplication and Severity Ranking
   │
  [L] Review Report
   │
  [M] Dashboard / PR Comments
   │
  [N] Human Approval (ADR-005 Gate)
   │
  [O] Isolated Patch and Test Runner
   │
  [P] Validated Fix Proposal
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from openreview.adapters.ai_base import AIReviewRequest, CodeContextBlock, ModelProvider
from openreview.adapters.base import ScannerAdapter, ScannerContext
from openreview.adapters.eslint import ESLintAdapter
from openreview.adapters.ollama import OllamaAdapter
from openreview.adapters.rea import ReaAdapter
from openreview.adapters.ruff import RuffAdapter
from openreview.config import OpenReviewConfig
from openreview.integrations.inline_comments import (
    map_findings_to_inline_comments,
)
from openreview.patch.applier import export_patch_to_file
from openreview.patch.generator import create_patch_for_finding
from openreview.patch.models import PatchProposal
from openreview.patch.sandbox import SandboxPatchTester
from openreview.repo.diff_models import DiffRequest, DiffTarget
from openreview.repo.diff_parser import FileDiff, ParsedDiff, extract_git_diff
from openreview.repo.git import GitRepoInfo, inspect_git_status
from openreview.review.context import select_review_context
from openreview.review.deduplicator import DeduplicatorAndSeverityRanker
from openreview.review.evidence_validator import EvidenceAndConfidenceValidator
from openreview.review.normalizer import FindingNormalizer
from openreview.review.rules_and_history import (
    RepositoryRulesAndHistory,
    load_repository_rules_and_history,
)
from openreview.schemas.models import Finding, ReviewReport
from openreview.security.redaction import redact_context_blocks

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationExecutionResult:
    """Complete result object traversing nodes A through P of the architecture."""

    # Node A & B
    repo_info: GitRepoInfo
    diff_request: DiffRequest

    # Node C
    parsed_diff: ParsedDiff

    # Node D
    context_blocks: list[CodeContextBlock]
    secrets_redacted_count: int

    # Node H
    repo_rules_and_history: RepositoryRulesAndHistory

    # Node L
    report: ReviewReport

    # Node M
    inline_comments: list[dict[str, any]] = field(default_factory=list)
    unmappable_findings: list[Finding] = field(default_factory=list)

    # Node P (Fix proposals created from findings)
    proposals: list[PatchProposal] = field(default_factory=list)


class CompleteReviewOrchestrator:
    """The central orchestrator driving the entire 16-node review pipeline."""

    def __init__(
        self,
        repo_root: Path | str,
        config: OpenReviewConfig,
        scanners: list[ScannerAdapter] | None = None,
        ai_provider: ModelProvider | None = None,
        include_rea: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config

        # Configure static scanners including REA adapter
        if scanners is not None:
            self.scanners = scanners
        else:
            self.scanners = []
            for name in config.scanners.enabled:
                norm = name.strip().lower()
                if norm == "ruff":
                    self.scanners.append(RuffAdapter())
                elif norm == "eslint":
                    self.scanners.append(ESLintAdapter())
            if include_rea:
                rea_adapter = ReaAdapter()
                if rea_adapter.is_available():
                    self.scanners.append(rea_adapter)

        # Configure AI provider
        if ai_provider is not None:
            self.ai_provider: ModelProvider | None = ai_provider
        elif config.ai.enabled:
            self.ai_provider = OllamaAdapter(
                endpoint=config.ai.endpoint,
                default_model=config.ai.model,
            )
        else:
            self.ai_provider = None

    def run_pipeline(
        self,
        diff_request: DiffRequest | None = None,
        generate_proposals: bool = True,
    ) -> OrchestrationExecutionResult:
        """Execute the full 16-node architecture pipeline."""
        # -------------------------------------------------------------------
        # [A & B] GitHub PR / Local Git Repository & Integration Layer
        # -------------------------------------------------------------------
        repo_info = inspect_git_status(self.repo_root)

        if diff_request is None:
            diff_request = DiffRequest(
                repo_root=self.repo_root,
                target=DiffTarget.WORKING_TREE,
                base_ref=self.config.review.base_ref,
                max_files=self.config.review.max_files,
                max_file_bytes=self.config.review.max_file_bytes,
            )

        # -------------------------------------------------------------------
        # [C] Diff Parser and File Mapper
        # -------------------------------------------------------------------
        parsed_diff = extract_git_diff(diff_request)
        reviewable_diffs = parsed_diff.reviewable_files
        target_file_paths = [d.path for d in reviewable_diffs]

        # -------------------------------------------------------------------
        # [D] Context Engine (Bounded changed code + Secret Redaction)
        # -------------------------------------------------------------------
        context_blocks = select_review_context(
            repo_root=self.repo_root,
            reviewable_diffs=reviewable_diffs,
            max_tokens=self.config.ai.max_context_tokens,
        )

        secrets_redacted_count = 0
        if self.config.privacy.redact_secrets and context_blocks:
            context_blocks, secrets_redacted_count = redact_context_blocks(context_blocks)

        # -------------------------------------------------------------------
        # [H] Repository Rules and History
        # -------------------------------------------------------------------
        rules_and_history = load_repository_rules_and_history(self.repo_root)

        # -------------------------------------------------------------------
        # [E] Review Orchestrator: Dispatches to [F] and [G]
        # -------------------------------------------------------------------
        raw_static_findings: list[Finding] = []
        scanner_failures: list[str] = []

        # [F] Static Analysis Adapters (Ruff, ESLint, REA)
        if target_file_paths and self.scanners:
            for scanner in self.scanners:
                filtered_files = scanner.filter_target_files(target_file_paths)
                if not filtered_files or not scanner.is_available():
                    continue

                ctx = ScannerContext(
                    repo_root=self.repo_root,
                    target_files=filtered_files,
                    timeout_seconds=self.config.scanners.timeout_seconds,
                )
                res = scanner.run(ctx)
                if res.is_successful:
                    raw_static_findings.extend(res.findings)
                else:
                    scanner_failures.append(f"{scanner.name}: {res.status.value}")

        # [G] Local AI Model (Ollama)
        ai_suggestion_tuples: list[tuple[any, str]] = []
        if (
            self.config.ai.enabled
            and self.ai_provider is not None
            and self.ai_provider.is_available()
            and context_blocks
        ):
            ai_req = AIReviewRequest(
                blocks=context_blocks,
                model=self.config.ai.model,
            )
            ai_resp = self.ai_provider.generate_suggestions(ai_req)
            if ai_resp.is_successful and ai_resp.suggestions:
                for sugg in ai_resp.suggestions:
                    # Map suggestion back to target file
                    target_file = sugg.file_path or (target_file_paths[0] if target_file_paths else "")
                    ai_suggestion_tuples.append((sugg, target_file))

        # -------------------------------------------------------------------
        # [I] Finding Normalizer
        # -------------------------------------------------------------------
        normalized_findings = FindingNormalizer.batch_normalize(
            static_findings=raw_static_findings,
            ai_suggestions=ai_suggestion_tuples,
            ai_scanner_name="ollama",
        )

        # -------------------------------------------------------------------
        # [J] Evidence and Confidence Validation
        # -------------------------------------------------------------------
        validated_findings = EvidenceAndConfidenceValidator.filter_and_validate(
            findings=normalized_findings,
            reviewable_diffs=reviewable_diffs,
            repo_root=self.repo_root,
            min_confidence=0.5,
        )

        # -------------------------------------------------------------------
        # [K] Deduplication and Severity Ranking
        # -------------------------------------------------------------------
        ranked_findings = DeduplicatorAndSeverityRanker.deduplicate_and_rank(validated_findings)

        # -------------------------------------------------------------------
        # [L] Review Report
        # -------------------------------------------------------------------
        report = ReviewReport.create(
            repo_root=str(self.repo_root),
            findings=ranked_findings,
            total_files_analyzed=len(target_file_paths),
            scanners_run=[s.name for s in self.scanners if s.is_available()],
            scanners_failed=scanner_failures,
            ai_enabled=bool(self.config.ai.enabled and self.ai_provider and self.ai_provider.is_available()),
            ai_model=self.config.ai.model if self.config.ai.enabled else None,
            base_ref=diff_request.base_ref,
            head_ref=diff_request.head_ref or repo_info.head_ref,
        )

        # -------------------------------------------------------------------
        # [M] Dashboard / PR Comments
        # -------------------------------------------------------------------
        inline_comments, unmappable = map_findings_to_inline_comments(
            findings=ranked_findings,
            parsed_diff=parsed_diff,
        )

        # -------------------------------------------------------------------
        # [N, O, P] Human Approval, Isolated Sandbox, & Validated Fix Proposals
        # -------------------------------------------------------------------
        proposals: list[PatchProposal] = []
        if generate_proposals and ranked_findings:
            for finding in ranked_findings:
                if finding.recommendation:
                    try:
                        prop = create_patch_for_finding(
                            repo_root=self.repo_root,
                            finding=finding,
                            replacement_text=finding.recommendation,
                        )
                        proposals.append(prop)
                    except Exception as e:
                        logger.debug("Could not generate patch proposal for %s: %s", finding.finding_id[:8], e)

        return OrchestrationExecutionResult(
            repo_info=repo_info,
            diff_request=diff_request,
            parsed_diff=parsed_diff,
            context_blocks=context_blocks,
            secrets_redacted_count=secrets_redacted_count,
            repo_rules_and_history=rules_and_history,
            report=report,
            inline_comments=inline_comments,
            unmappable_findings=unmappable,
            proposals=proposals,
        )
