"""Performance baseline and latency budget benchmarks for OpenReview AI per Gate E."""

from __future__ import annotations

import time

from openreview.repo.diff_parser import parse_unified_diff
from openreview.reports.json_report import render_json_report, validate_json_report_payload
from openreview.reports.markdown import render_markdown_report
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    ReviewReport,
    Severity,
    compute_finding_id,
)
from openreview.security.redaction import redact_text


def test_diff_parser_performance() -> None:
    """Benchmark: Parsing a multi-file diff of 50 files should complete in < 200ms."""
    diff_chunks = []
    for i in range(50):
        diff_chunks.append(
            f"diff --git a/src/module_{i}.py b/src/module_{i}.py\n"
            f"index 1111111..2222222 100644\n"
            f"--- a/src/module_{i}.py\n"
            f"+++ b/src/module_{i}.py\n"
            f"@@ -10,5 +10,7 @@\n"
            f" line_before = True\n"
            f"-old_value = {i}\n"
            f"+new_value = {i} * 2\n"
            f"+extra_check = True\n"
            f" line_after = True\n"
        )
    large_diff_text = "\n".join(diff_chunks)

    start = time.perf_counter()
    parsed = parse_unified_diff(large_diff_text)
    duration = time.perf_counter() - start

    assert len(parsed.files) == 50
    assert duration < 0.20, f"Diff parsing took too long: {duration:.4f}s"


def test_finding_id_computation_performance() -> None:
    """Benchmark: Computing 1,000 stable finding IDs should complete in < 50ms."""
    start = time.perf_counter()
    for i in range(1000):
        compute_finding_id(
            path=f"src/components/button_{i}.tsx",
            rule_id="security/xss-risk",
            start_line=i,
            title=f"Potential XSS injection in component {i}",
        )
    duration = time.perf_counter() - start
    assert duration < 0.10, f"Finding ID generation took too long: {duration:.4f}s"


def test_report_rendering_performance() -> None:
    """Benchmark: Rendering Markdown and JSON reports for 50 findings should complete in < 50ms."""
    findings = [
        Finding(
            title=f"Issue #{i} in codebase",
            explanation=f"Detailed analysis of issue #{i} with code references",
            path=f"src/file_{i}.py",
            severity=Severity.HIGH if i % 2 == 0 else Severity.MEDIUM,
            confidence=Confidence.HIGH,
            origin=FindingOrigin.STATIC,
            start_line=i + 1,
            end_line=i + 5,
            rule_id=f"rule-{i}",
        )
        for i in range(50)
    ]

    report = ReviewReport.create(
        repo_root="/test/repo",
        findings=findings,
        total_files_analyzed=50,
        scanners_run=["ruff", "eslint"],
    )

    # Markdown rendering
    start_md = time.perf_counter()
    md_output = render_markdown_report(report)
    duration_md = time.perf_counter() - start_md
    assert duration_md < 0.05, f"Markdown rendering took {duration_md:.4f}s"
    assert "Issue #49" in md_output

    # JSON rendering and schema validation
    start_json = time.perf_counter()
    json_output = render_json_report(report)
    validate_json_report_payload(json_output)
    duration_json = time.perf_counter() - start_json
    assert duration_json < 0.05, f"JSON rendering and validation took {duration_json:.4f}s"


def test_secret_redaction_throughput() -> None:
    """Benchmark: Scanning and redacting 20,000 characters of source code should complete in < 20ms."""
    chunk = (
        "def connect_db():\n"
        "    aws_key = 'AKIA1234567890EXAMPLE'\n"
        "    token = 'ghp_1234567890abcdefghijklmnopqrstuvwxyz'\n"
        "    password = 'SuperSecret123456'\n"
        "    return aws_key + token\n"
    )
    large_code = chunk * 100  # ~15,000 chars

    start = time.perf_counter()
    res = redact_text(large_code)
    duration = time.perf_counter() - start

    assert res.total_redactions >= 300
    assert duration < 0.05, f"Redaction throughput too low: {duration:.4f}s"
