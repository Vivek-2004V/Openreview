"""GitHub API client and payload formatters for PR reviews and check runs."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from openreview import __version__
from openreview.integrations.inline_comments import map_findings_to_inline_comments
from openreview.repo.diff_parser import ParsedDiff
from openreview.schemas.models import FindingOrigin, ReviewReport, Severity

logger = logging.getLogger(__name__)


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
            lines.append(
                f"{idx}. **[{f.severity.value.upper()}]** `{loc}`: **{f.title}** {origin_badge}"
            )
            lines.append(f"   > {f.explanation}")
            if f.recommendation:
                lines.append(f"   *Fix Recommendation:* {f.recommendation}")
            lines.append("")

    if report.pre_existing_findings:
        lines.append(
            f"### Pre-existing Code Quality Notes ({len(report.pre_existing_findings)})"
        )
        lines.append(
            "_The following findings were detected on unchanged lines within modified files and do not block this PR:_"
        )
        for f in report.pre_existing_findings:
            loc = f"{f.path}:{f.start_line}" if f.start_line else f.path
            rule = f"`{f.rule_id}`: " if f.rule_id else ""
            lines.append(
                f"- **[{f.severity.value.upper()}]** {rule}`{loc}` — {f.title}"
            )
        lines.append("")

    lines.append("---")
    lines.append(
        f"*Automated review by OpenReview AI v{__version__} | Report ID: `{report.report_id}`*"
    )
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

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> Any:
        """Internal helper to dispatch HTTP requests with authorization and error handling."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        req_headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": f"OpenReview-AI/{__version__}",
        }
        if headers:
            req_headers.update(headers)

        req_body = None
        if data is not None:
            req_body = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            url,
            data=req_body,
            headers=req_headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type or (
                    resp_bytes and resp_bytes.strip().startswith((b"{", b"["))
                ):
                    return json.loads(resp_bytes.decode("utf-8"))
                return resp_bytes.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = (
                exc.read().decode("utf-8", errors="replace")
                if hasattr(exc, "read")
                else ""
            )
            logger.error(
                "GitHub API %s %s returned HTTP %s: %s",
                method,
                path,
                exc.code,
                error_body,
            )
            raise

    def create_pr_review(
        self,
        repo_full_name: str,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Post a PR review via POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews."""
        path = f"repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload = {
            "body": body,
            "event": event,
            "comments": comments or [],
        }
        return self._request("POST", path, data=payload)

    def create_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        name: str = "OpenReview AI",
        status: str = "in_progress",
        conclusion: str | None = None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a check run via POST /repos/{owner}/{repo}/check-runs."""
        path = f"repos/{repo_full_name}/check-runs"
        payload: dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion:
            payload["conclusion"] = conclusion
        if output:
            payload["output"] = output

        return self._request(
            "POST",
            path,
            data=payload,
            headers={"Accept": "application/vnd.github.v3+json"},
        )

    def update_check_run(
        self,
        repo_full_name: str,
        check_run_id: int,
        status: str = "completed",
        conclusion: str = "success",
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update an existing check run via PATCH /repos/{owner}/{repo}/check-runs/{check_run_id}."""
        path = f"repos/{repo_full_name}/check-runs/{check_run_id}"
        payload: dict[str, Any] = {
            "status": status,
            "conclusion": conclusion,
        }
        if output:
            payload["output"] = output

        return self._request(
            "PATCH",
            path,
            data=payload,
            headers={"Accept": "application/vnd.github.v3+json"},
        )

    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch raw unified diff string for a PR."""
        path = f"repos/{repo_full_name}/pulls/{pr_number}"
        return self._request(
            "GET",
            path,
            headers={"Accept": "application/vnd.github.v3.diff"},
        )


class PRReportingOrchestrator:
    """Orchestrates end-to-end GitHub reporting (Checks API and PR reviews with inline comments)."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def report_review_result(
        self,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        report: ReviewReport,
        parsed_diff: ParsedDiff | None = None,
        check_run_id: int | None = None,
    ) -> dict[str, Any]:
        """Submit comprehensive PR review and Checks API updates.

        Handles:
        1. Determining review action (APPROVE, REQUEST_CHANGES, COMMENT)
        2. Hunk-bounded inline comment mapping with safe 422 fallback
        3. Checks API conclusion synchronization
        """
        review_event = determine_github_review_event(report)
        body = format_github_review_body(report)

        # 1. Map inline comments if diff is provided
        inline_comments: list[dict[str, Any]] = []
        if parsed_diff is not None:
            inline_comments, _unmappable = map_findings_to_inline_comments(
                findings=report.findings,
                parsed_diff=parsed_diff,
            )

        # 2. Post PR Review with inline comments, falling back to top-level if 422 occurs
        review_resp: dict[str, Any] = {}
        try:
            review_resp = self.client.create_pr_review(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=body,
                event=review_event,
                comments=inline_comments,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 422 and inline_comments:
                logger.warning(
                    "GitHub API rejected inline comments with 422 Unprocessable Entity. "
                    "Falling back to posting review without inline comments."
                )
                review_resp = self.client.create_pr_review(
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    body=body,
                    event=review_event,
                    comments=[],
                )
            else:
                raise

        # 3. Synchronize Checks API run
        check_run_payload = format_check_run_payload(report, head_sha=head_sha)
        check_resp: dict[str, Any] = {}
        try:
            if check_run_id is not None:
                check_resp = self.client.update_check_run(
                    repo_full_name=repo_full_name,
                    check_run_id=check_run_id,
                    status="completed",
                    conclusion=check_run_payload["conclusion"],
                    output=check_run_payload["output"],
                )
            else:
                check_resp = self.client.create_check_run(
                    repo_full_name=repo_full_name,
                    head_sha=head_sha,
                    status="completed",
                    conclusion=check_run_payload["conclusion"],
                    output=check_run_payload["output"],
                )
        except (urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning(
                "Failed to update GitHub Checks API for %s: %s", repo_full_name, exc
            )

        return {
            "review": review_resp,
            "check_run": check_resp,
            "event": review_event,
            "inline_comments_count": len(inline_comments),
        }
