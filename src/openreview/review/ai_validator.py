"""Validation and hallucination filtering for AI-generated review suggestions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from openreview.adapters.ai_base import AIReviewSuggestion
from openreview.repo.diff_parser import FileDiff
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    FindingStatus,
    Severity,
)
from openreview.security.ai_safety import validate_ai_finding_content

logger = logging.getLogger(__name__)


def normalize_relpath(path_str: str) -> str:
    """Normalize file path for cross-platform comparison."""
    return path_str.replace("\\", "/").strip().lstrip("./")


def is_line_in_diff_or_file(
    line: int,
    file_diff: FileDiff,
    total_file_lines: int | None = None,
    tolerance: int = 10,
) -> bool:
    """Check whether a line number is plausible within the file diff and its context."""
    if line < 1:
        return False

    if total_file_lines is not None and line > total_file_lines:
        return False

    # Check if line is within any changed line ranges (expanded by tolerance)
    if not file_diff.changed_line_ranges:
        return True

    for r in file_diff.changed_line_ranges:
        if (r.start - tolerance) <= line <= (r.end + tolerance):
            return True

    return False


def validate_and_convert_ai_suggestions(
    suggestions: Sequence[AIReviewSuggestion],
    reviewable_diffs: Sequence[FileDiff],
    repo_root: Path | None = None,
) -> list[Finding]:
    """Validate AI suggestions against actual changed files and produce canonical Findings.

    Drops suggestions with:
    - Hallucinated paths not present in the diff
    - Hallucinated line numbers far outside file boundaries
    - Empty title or explanation

    All accepted findings are tagged with:
    - origin: FindingOrigin.AI
    - status: FindingStatus.UNVERIFIED
    """
    valid_diff_map: dict[str, FileDiff] = {}
    for d in reviewable_diffs:
        valid_diff_map[normalize_relpath(d.path)] = d
        if d.old_path:
            valid_diff_map[normalize_relpath(d.old_path)] = d

    # Pre-calculate file line counts if repo_root is provided
    file_line_counts: dict[str, int] = {}
    if repo_root is not None:
        for norm_path, d in valid_diff_map.items():
            disk_path = repo_root / d.path
            if disk_path.is_file():
                try:
                    count = len(disk_path.read_text(encoding="utf-8", errors="replace").splitlines())
                    file_line_counts[norm_path] = count
                except Exception:
                    pass

    validated_findings: list[Finding] = []

    for sugg in suggestions:
        norm_sugg_path = normalize_relpath(sugg.path)
        matching_diff = valid_diff_map.get(norm_sugg_path)

        # 1. Reject hallucinated file paths
        if not matching_diff:
            continue

        # 2. Reject empty title or explanation
        title = sugg.title.strip()
        explanation = sugg.explanation.strip()
        if not title or not explanation:
            continue

        # 2b. AI Safety: Reject findings echoing prompt injection or dangerous shell execution
        is_safe, safety_reason = validate_ai_finding_content(
            title=title,
            explanation=explanation,
            recommendation=sugg.recommendation,
        )
        if not is_safe:
            logger.warning(
                "Dropping unsafe AI suggestion for '%s': %s",
                matching_diff.path,
                safety_reason,
            )
            continue

        start_line = sugg.start_line
        end_line = sugg.end_line
        total_lines = file_line_counts.get(norm_sugg_path)

        # 3. Check line boundaries
        if start_line is not None:
            if not is_line_in_diff_or_file(start_line, matching_diff, total_file_lines=total_lines):
                # If start_line is invalid or outside file, unset it to avoid bogus report links
                start_line = None
                end_line = None
            elif end_line is not None and total_lines is not None and end_line > total_lines:
                end_line = total_lines

        # 4. Construct canonical Finding with AI origin and UNVERIFIED status
        rule_id = sugg.rule_id if sugg.rule_id else "ai/suggestion"

        finding = Finding(
            title=title,
            explanation=explanation,
            path=matching_diff.path,  # canonical repo path
            severity=sugg.severity if isinstance(sugg.severity, Severity) else Severity.MEDIUM,
            confidence=sugg.confidence if isinstance(sugg.confidence, Confidence) else Confidence.MEDIUM,
            origin=FindingOrigin.AI,
            status=FindingStatus.UNVERIFIED,
            start_line=start_line,
            end_line=end_line,
            rule_id=rule_id,
            recommendation=sugg.recommendation,
        )
        validated_findings.append(finding)

    return validated_findings
