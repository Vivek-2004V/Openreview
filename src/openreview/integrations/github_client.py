"""GitHub API client and payload formatters for PR reviews and check runs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from openreview import __version__
from openreview.schemas.models import FindingOrigin, ReviewReport, Severity


def determine_github_review_event(report: ReviewReport) -> str:
    """Determine GitHub review action: APPROVE, REQUEST_CHANGES, or COMMENT."""
    crit_count = report.summary.findings_by_severity.get(Severity.CRITICAL.value, 0)
    high_count = report.summary.findings_by_severity.get(Severity.HIGH.value, 0)

    if crit_count > 0 or high_count > 0:
        return "REQUEST_CHANGES"
    elif report.summary.total_findings > 0:
        return "COMMENT"
    return "APPROVE"


def format_github_review_body(report: ReviewReport) -> str:
    """Format PR top-level review comment matching GitHub Markdown flavor."""
    crit_count = report.summary.findings_by_severity.get(Severity.CRITICAL.value, 0)
    high_count = report.summary.findings_by_severity.get(Severity.HIGH.value, 0)
    med_count = report.summary.findings_by_severity.get(Severity.MEDIUM.value, 0)
    low_count = report.summary.findings_by_severity.get(Severity.LOW.value, 0)

    if report.summary.total_findings == 0:
        status_line = "### ✅ OpenReview AI — Review Passed\nNo security vulnerabilities, compiler defects, or logic errors discovered."
    elif crit_count > 0 or high_count > 0:
        status_line = f"### ⚠️ OpenReview AI — Action Required\nFound **{crit_count} critical** and **{high_count} high** severity issues requiring review."
    else:
        status_line = f"### ℹ️ OpenReview AI — Suggestions Found\nFound **{report.summary.total_findings}** code quality suggestions."

    lines = [
        status_line,
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Files Analyzed** | {report.summary.total_files_analyzed} |",
        f"| **Total Findings** | {report.summary.total_findings} |",
        f"| **Critical / High** | {crit_count} / {high_count} |",
        f"| **Medium / Low** | {med_count} / {low_count} |",
        "",
    ]

    if report.findings:
        lines.append("### Detailed Findings Summary")
        for idx, f in enumerate(report.findings, 1):
            origin_badge = (
                "`[Deterministic Scanner]`"
                if f.origin == FindingOrigin.STATIC
                else "`[Local AI Suggestion (Unverified)]`"
            )
            loc = f"{f.path}:{f.start_line}" if f.start_line else f.path
            lines.append(f"{idx}. **[{f.severity.value.upper()}]** `{loc}`: **{f.title}** {origin_badge}")
            lines.append(f"   > {f.explanation}")
            if f.recommendation:
                lines.append(f"   *Fix Recommendation:* {f.recommendation}")
            lines.append("")

    lines.append("---")
    lines.append(f"*Automated review by OpenReview AI v{__version__} | Report ID: `{report.report_id}`*")
    return "\n".join(lines)


def format_check_run_payload(
    report: ReviewReport,
    head_sha: str,
    name: str = "OpenReview AI",
) -> dict[str, Any]:
    """Format payload for GitHub Checks API create/update call."""
    crit_count = report.summary.findings_by_severity.get(Severity.CRITICAL.value, 0)
    high_count = report.summary.findings_by_severity.get(Severity.HIGH.value, 0)

    if crit_count > 0 or high_count > 0:
        conclusion = "failure"
        title = f"Action Required: {crit_count} critical, {high_count} high findings"
    else:
        conclusion = "success"
        title = "Review Passed: No high-risk defects discovered"

    summary_text = format_github_review_body(report)

    return {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": title,
            "summary": summary_text,
        },
    }


class GitHubClient:
    """Lightweight, zero-external-dependency GitHub REST API client."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def create_pr_review(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post a PR review via POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews."""
        url = f"{self.base_url}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload = {
            "body": body,
            "event": event,
            "comments": comments or [],
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
