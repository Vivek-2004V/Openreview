"""Markdown renderer for OpenReview AI reports."""

from __future__ import annotations

from openreview.schemas.models import (
    FindingOrigin,
    ReviewReport,
    Severity,
)

SEVERITY_BADGES = {
    Severity.CRITICAL: "🔴 **CRITICAL**",
    Severity.HIGH: "🟠 **HIGH**",
    Severity.MEDIUM: "🟡 **MEDIUM**",
    Severity.LOW: "🔵 **LOW**",
    Severity.INFO: "ℹ️ **INFO**",
    Severity.UNKNOWN: "⚪ **UNKNOWN**",
}

ORIGIN_LABELS = {
    FindingOrigin.STATIC: "🔍 Deterministic Static Tool",
    FindingOrigin.AI: "🤖 Local AI Suggestion (Unverified)",
    FindingOrigin.COMBINED: "⚡ Combined Static & AI Finding",
}


def render_markdown_report(report: ReviewReport) -> str:
    """Render a validated ReviewReport object into clean, human-readable Markdown."""
    lines: list[str] = []

    # Title & Metadata Header
    lines.append("# OpenReview AI Code Review Report")
    lines.append("")
    lines.append(f"- **Report ID:** `{report.report_id}`")
    lines.append(f"- **Generated At:** {report.created_at}")
    lines.append(f"- **Repository:** `{report.repo_root}`")
    if report.base_ref:
        ref_text = f"`{report.base_ref}`"
        if report.head_ref:
            ref_text += f" ... `{report.head_ref}`"
        lines.append(f"- **Revisions:** {ref_text}")
    lines.append("")

    # Summary Metrics Table
    summary = report.summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- **Files Analyzed:** {summary.total_files_analyzed}")
    lines.append(f"- **Total Findings:** {summary.total_findings}")
    if summary.scanners_run:
        lines.append(
            f"- **Active Scanners:** {', '.join(f'`{s}`' for s in summary.scanners_run)}"
        )
    if summary.scanners_failed:
        lines.append(
            f"- **Failed Scanners:** {', '.join(f'`{s}`' for s in summary.scanners_failed)}"
        )
    if summary.ai_enabled:
        lines.append(f"- **AI Model:** `{summary.ai_model or 'default'}`")
    lines.append("")

    # Severity Breakdown Table
    lines.append("| Severity | Count |")
    lines.append("| :--- | :---: |")
    for sev in [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.INFO,
    ]:
        count = summary.findings_by_severity.get(sev.value, 0)
        lines.append(f"| {sev.value.capitalize()} | {count} |")
    lines.append("")

    # If clean run
    if not report.findings:
        lines.append("---")
        lines.append("### ✅ Clean Review")
        lines.append("No actionable security, quality, or logic defects discovered.")
        lines.append("")
        if report.pre_existing_findings:
            lines.append("### ℹ️ Pre-existing Code Issues (Outside Changed Hunks)")
            lines.append(
                f"{len(report.pre_existing_findings)} pre-existing issue(s) detected outside modified hunks:"
            )
            lines.append("")
            for f in report.pre_existing_findings:
                loc = f"`{f.path}`:L{f.start_line}" if f.start_line else f"`{f.path}`"
                rule = f"[{f.rule_id}]" if f.rule_id else ""
                lines.append(
                    f"- **{f.severity.value.upper()}** {rule} {loc} — {f.title}"
                )
            lines.append("")
        return "\n".join(lines)

    lines.append("---")
    lines.append("## Findings Details")
    lines.append("")

    # Group findings by severity
    severity_order = [
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.INFO,
        Severity.UNKNOWN,
    ]

    for sev in severity_order:
        sev_findings = [f for f in report.findings if f.severity == sev]
        if not sev_findings:
            continue

        badge = SEVERITY_BADGES.get(sev, sev.value.upper())
        lines.append(f"### {badge} ({len(sev_findings)})")
        lines.append("")

        for idx, f in enumerate(sev_findings, start=1):
            lines.append(f"#### {idx}. {f.title}")
            lines.append("")

            # Location and origin meta
            loc_str = f"`{f.path}`"
            if f.start_line is not None:
                if f.end_line is not None and f.end_line != f.start_line:
                    loc_str += f":L{f.start_line}-{f.end_line}"
                else:
                    loc_str += f":L{f.start_line}"

            lines.append(f"- **File:** {loc_str}")
            lines.append(f"- **Origin:** {ORIGIN_LABELS.get(f.origin, f.origin.value)}")
            if f.rule_id:
                lines.append(f"- **Rule ID:** `{f.rule_id}`")
            lines.append(f"- **Finding ID:** `{f.finding_id}`")
            lines.append("")

            # Explanation
            lines.append("**Explanation:**")
            lines.append(f"{f.explanation}")
            lines.append("")

            # Evidence excerpt if present
            if f.evidence:
                lines.append("**Evidence:**")
                lines.append("```")
                lines.append(f"{f.evidence}")
                lines.append("```")
                lines.append("")

            # Recommendation if present
            if f.recommendation:
                lines.append("**Suggested Remediation:**")
                lines.append(f"{f.recommendation}")
                lines.append("")

            # References if present
            if f.references:
                lines.append("**References:**")
                for ref in f.references:
                    lines.append(f"- <{ref}>")
                lines.append("")

            lines.append("---")
            lines.append("")

    if report.pre_existing_findings:
        lines.append(f"## Pre-existing Findings ({len(report.pre_existing_findings)})")
        lines.append(
            "The following issues were detected in modified files, but reside outside the lines changed in this pull request:"
        )
        lines.append("")
        for f in report.pre_existing_findings:
            loc = f"`{f.path}`:L{f.start_line}" if f.start_line else f"`{f.path}`"
            rule = f"[{f.rule_id}]" if f.rule_id else ""
            lines.append(f"- **{f.severity.value.upper()}** {rule} {loc} — {f.title}")
        lines.append("")

    return "\n".join(lines)
