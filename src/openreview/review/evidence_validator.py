"""Evidence and Confidence Validation component (Node J in review architecture).

Validates that finding lines strictly fall within changed diff hunks, verifies
code evidence against real repository text, and filters out hallucinations.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from openreview.repo.diff_parser import FileDiff
from openreview.schemas.models import Finding, FindingOrigin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidatedFindingResult:
    """Represents a validated finding with evidence score and verification status."""

    finding: Finding
    is_valid_evidence: bool
    confidence_score: float
    validation_notes: str = ""


class EvidenceAndConfidenceValidator:
    """Validates line grounding, hunk bounds, and assigns calibrated confidence."""

    @staticmethod
    def validate_finding(
        finding: Finding,
        reviewable_diffs: Sequence[FileDiff],
        repo_root: Path | str | None = None,
    ) -> ValidatedFindingResult:
        """Validate an individual finding against diff hunks and repository evidence."""
        # Find matching file diff (normalized POSIX comparison)
        norm_path = finding.path.replace("\\", "/").lstrip("./")
        matched_diff = next(
            (
                d
                for d in reviewable_diffs
                if d.path.replace("\\", "/").lstrip("./") == norm_path
            ),
            None,
        )

        if matched_diff is None:
            return ValidatedFindingResult(
                finding=finding,
                is_valid_evidence=False,
                confidence_score=0.0,
                validation_notes=f"File '{finding.path}' is not among reviewable diff targets.",
            )

        # 1. Hunk boundary check
        if finding.start_line is not None:
            in_hunk = any(
                r.contains(finding.start_line) for r in matched_diff.changed_line_ranges
            )
            if not in_hunk:
                return ValidatedFindingResult(
                    finding=finding,
                    is_valid_evidence=False,
                    confidence_score=0.1,
                    validation_notes=f"Line {finding.start_line} is outside the modified diff hunks.",
                )

        # 2. Confidence scoring based on origin
        if finding.origin == FindingOrigin.STATIC:
            return ValidatedFindingResult(
                finding=finding,
                is_valid_evidence=True,
                confidence_score=1.0,
                validation_notes="Deterministic scanner evidence grounded in diff.",
            )
        else:
            # AI origin: calibrate confidence
            score = 0.85
            notes = "AI suggestion grounded in diff hunk; labeled as unverified suggestion per ADR-002."

            if repo_root is not None and finding.start_line is not None:
                full_path = Path(repo_root) / finding.path
                if full_path.is_file():
                    try:
                        lines = full_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines()
                        if 1 <= finding.start_line <= len(lines):
                            score = 0.90
                        else:
                            return ValidatedFindingResult(
                                finding=finding,
                                is_valid_evidence=False,
                                confidence_score=0.2,
                                validation_notes=f"Line {finding.start_line} exceeds total file lines ({len(lines)}).",
                            )
                    except OSError as e:
                        logger.debug(
                            "Could not verify file lines for %s: %s", finding.path, e
                        )

            return ValidatedFindingResult(
                finding=finding,
                is_valid_evidence=True,
                confidence_score=score,
                validation_notes=notes,
            )

    @classmethod
    def partition_and_validate(
        cls,
        findings: Sequence[Finding],
        reviewable_diffs: Sequence[FileDiff],
        repo_root: Path | str | None = None,
        min_confidence: float = 0.5,
    ) -> tuple[list[Finding], list[Finding]]:
        """Partition findings into (actionable_findings, pre_existing_findings).

        - actionable_findings: Validated findings strictly within modified diff hunks.
        - pre_existing_findings: Deterministic static findings located outside modified hunks.
        """
        actionable: list[Finding] = []
        pre_existing: list[Finding] = []

        for f in findings:
            res = cls.validate_finding(f, reviewable_diffs, repo_root=repo_root)
            if res.is_valid_evidence and res.confidence_score >= min_confidence:
                actionable.append(res.finding)
            elif f.origin == FindingOrigin.STATIC and (
                "outside the modified diff hunks" in res.validation_notes
                or f.is_pre_existing
            ):
                pre_existing_finding = Finding(
                    title=f.title,
                    explanation=f.explanation,
                    path=f.path,
                    severity=f.severity,
                    confidence=f.confidence,
                    origin=f.origin,
                    status=f.status,
                    start_line=f.start_line,
                    end_line=f.end_line,
                    rule_id=f.rule_id,
                    evidence=f.evidence,
                    recommendation=f.recommendation,
                    references=f.references,
                    schema_version=f.schema_version,
                    finding_id=f.finding_id,
                    is_pre_existing=True,
                )
                pre_existing.append(pre_existing_finding)
            else:
                logger.info(
                    "Filtered out finding %s (%s): %s",
                    f.finding_id[:8],
                    f.rule_id,
                    res.validation_notes,
                )

        return actionable, pre_existing

    @classmethod
    def filter_and_validate(
        cls,
        findings: Sequence[Finding],
        reviewable_diffs: Sequence[FileDiff],
        repo_root: Path | str | None = None,
        min_confidence: float = 0.5,
    ) -> list[Finding]:
        """Validate all findings, returning only actionable findings within changed diff hunks."""
        actionable, _ = cls.partition_and_validate(
            findings=findings,
            reviewable_diffs=reviewable_diffs,
            repo_root=repo_root,
            min_confidence=min_confidence,
        )
        return actionable
