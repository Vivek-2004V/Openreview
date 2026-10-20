"""Inline diff comment mapping and boundary filtering for GitHub Pull Requests."""

from __future__ import annotations

from typing import Any, Sequence

from openreview.repo.diff_parser import FileDiff, ParsedDiff
from openreview.schemas.models import Finding, FindingOrigin


def format_inline_comment_body(finding: Finding) -> str:
    """Format an individual inline review comment with badges and fix advice."""
    origin_badge = (
        "🔍 **Deterministic Scanner**"
        if finding.origin == FindingOrigin.STATIC
        else "🤖 **Local AI Suggestion (Unverified)**"
    )

    parts = [
        f"**[{finding.severity.value.upper()}] {finding.title}**",
        "",
        finding.explanation,
        "",
    ]

    if finding.recommendation:
        parts.append(f"**Recommendation:**\n{finding.recommendation}\n")

    if finding.rule_id:
        parts.append(f"*Rule:* `{finding.rule_id}` | {origin_badge}")
    else:
        parts.append(origin_badge)

    return "\n".join(parts)


def is_line_in_diff_hunks(path: str, line: int, parsed_diff: ParsedDiff) -> bool:
    """Verify that a target line number falls strictly within modified diff hunks."""
    fdiff = parsed_diff.get_file_diff(path)
    if not fdiff or not fdiff.is_reviewable:
        return False

    for r in fdiff.changed_line_ranges:
        if r.contains(line):
            return True

    return False


def map_findings_to_inline_comments(
    findings: Sequence[Finding],
    parsed_diff: ParsedDiff,
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """Map findings to GitHub inline comment payloads, preventing 422 unprocessable entity errors.

    Returns:
    - inline_comments: List of GitHub review comment dicts safe to submit.
    - unmappable_findings: Findings that cannot be placed inline (e.g. global or outside diff ranges).
    """
    inline_comments: list[dict[str, Any]] = []
    unmappable_findings: list[Finding] = []

    for f in findings:
        if not f.start_line:
            unmappable_findings.append(f)
            continue

        target_line = f.end_line or f.start_line

        # Ensure line is inside actual PR diff hunks
        if not is_line_in_diff_hunks(f.path, target_line, parsed_diff):
            unmappable_findings.append(f)
            continue

        body = format_inline_comment_body(f)

        comment_dict: dict[str, Any] = {
            "path": f.path,
            "line": target_line,
            "side": "RIGHT",
            "body": body,
        }

        # Multi-line comment support if start_line != target_line
        if f.end_line and f.start_line and f.end_line > f.start_line:
            # Check if start_line is also in diff
            if is_line_in_diff_hunks(f.path, f.start_line, parsed_diff):
                comment_dict["start_line"] = f.start_line
                comment_dict["start_side"] = "RIGHT"

        inline_comments.append(comment_dict)

    return inline_comments, unmappable_findings
