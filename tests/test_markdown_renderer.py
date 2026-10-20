"""Unit tests for Markdown report renderer per E2-T5."""

import pytest
from openreview.reports import render_markdown_report
from openreview.schemas import (
    Finding,
    FindingOrigin,
    ReviewReport,
    Severity,
)


def test_render_clean_markdown_report():
    """Verify clean review report markdown structure."""
    report = ReviewReport.create(
        repo_root="/workspace/repo",
        findings=[],
        total_files_analyzed=10,
        scanners_run=["ruff"],
    )

    md = render_markdown_report(report)
    assert "# OpenReview AI Code Review Report" in md
    assert "Clean Review" in md
    assert "No actionable security, quality, or logic defects discovered." in md
    assert "Files Analyzed:** 10" in md
    assert "Total Findings:** 0" in md


def test_render_findings_markdown_report():
    """Verify detailed findings rendering with badges, evidence, and remediation."""
    f1 = Finding(
        title="Command injection in subprocess call",
        explanation="Shell=True is used with unvalidated user input string.",
        path="src/executor.py",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
        start_line=25,
        end_line=28,
        rule_id="S602",
        evidence="subprocess.Popen(user_cmd, shell=True)",
        recommendation="Use an argument list without shell=True.",
        references=["https://cwe.mitre.org/data/definitions/78.html"],
    )

    f2 = Finding(
        title="Consider list comprehension",
        explanation="Can simplify loop logic.",
        path="src/helper.py",
        severity=Severity.INFO,
        origin=FindingOrigin.AI,
        start_line=10,
    )

    report = ReviewReport.create(
        repo_root="/workspace/repo",
        findings=[f1, f2],
        total_files_analyzed=5,
        scanners_run=["ruff", "bandit"],
        ai_enabled=True,
        ai_model="qwen2.5-coder:7b",
        base_ref="main",
        head_ref="feature",
    )

    md = render_markdown_report(report)

    # Check headers
    assert "Report ID:" in md
    assert "CRITICAL" in md
    assert "Command injection in subprocess call" in md
    assert "`src/executor.py`:L25-28" in md
    assert "Deterministic Static Tool" in md
    assert "subprocess.Popen(user_cmd, shell=True)" in md
    assert "Suggested Remediation:" in md
    assert "https://cwe.mitre.org/data/definitions/78.html" in md

    # Check AI origin finding
    assert "Local AI Suggestion (Unverified)" in md
    assert "Consider list comprehension" in md
    assert "`src/helper.py`:L10" in md
