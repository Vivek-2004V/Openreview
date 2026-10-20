"""Golden fixture tests verifying report schema consistency and renderer determinism per E2-T7."""

from pathlib import Path
import pytest
from openreview.reports import (
    render_json_report,
    render_markdown_report,
    validate_json_report_payload,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "golden"


def test_golden_clean_report():
    """Verify clean review golden fixture loads and renders cleanly."""
    json_path = FIXTURES_DIR / "sample_clean_report.json"
    assert json_path.exists(), "Golden clean report fixture missing"

    payload = json_path.read_text(encoding="utf-8")
    report = validate_json_report_payload(payload)

    assert report.report_id == "rep_golden_clean"
    assert report.summary.total_findings == 0
    assert report.summary.total_files_analyzed == 8
    assert report.findings == []

    # Markdown rendering
    md = render_markdown_report(report)
    assert "Clean Review" in md
    assert "No actionable security, quality, or logic defects discovered." in md
    assert "- **Files Analyzed:** 8" in md

    # Determinism: Re-rendering produces bit-for-bit identical string
    md2 = render_markdown_report(report)
    assert md == md2


def test_golden_findings_report():
    """Verify findings golden fixture validates and Markdown/JSON agree on all metrics."""
    json_path = FIXTURES_DIR / "sample_findings_report.json"
    assert json_path.exists(), "Golden findings report fixture missing"

    payload = json_path.read_text(encoding="utf-8")
    report = validate_json_report_payload(payload)

    assert report.report_id == "rep_golden_findings"
    assert report.summary.total_findings == 2
    assert report.summary.findings_by_severity["critical"] == 1
    assert report.summary.findings_by_severity["info"] == 1
    assert len(report.findings) == 2

    # Render to Markdown
    md = render_markdown_report(report)

    # Check Markdown and JSON agreement on finding count and IDs
    assert f"Total Findings:** {report.summary.total_findings}" in md
    for f in report.findings:
        assert f.finding_id in md, f"Finding ID {f.finding_id} missing from Markdown"
        assert f.path in md, f"Finding path {f.path} missing from Markdown"
        assert f.title in md, f"Finding title {f.title} missing from Markdown"

    # Determinism: JSON and Markdown reproducibility
    json_out1 = render_json_report(report)
    json_out2 = render_json_report(report)
    assert json_out1 == json_out2

    md_out1 = render_markdown_report(report)
    md_out2 = render_markdown_report(report)
    assert md_out1 == md_out2
