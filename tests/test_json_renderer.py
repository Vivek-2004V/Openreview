"""Unit tests for JSON report rendering and schema validation per E2-T6."""

import json
import pytest
from openreview.reports import (
    render_json_report,
    validate_json_report_payload,
)
from openreview.schemas import (
    Finding,
    FindingOrigin,
    ReviewReport,
    SchemaValidationError,
    Severity,
)


def test_render_and_validate_json_report_roundtrip():
    """Verify JSON rendering and schema validation roundtrip."""
    f = Finding(
        title="Insecure hashing algorithm used",
        explanation="MD5 is cryptographically broken and vulnerable to collision attacks.",
        path="src/crypto.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=15,
        rule_id="S324",
        evidence="hashlib.md5(data.encode())",
        recommendation="Use hashlib.sha256 instead.",
        references=["https://cwe.mitre.org/data/definitions/327.html"],
    )

    report = ReviewReport.create(
        repo_root="/repo",
        findings=[f],
        total_files_analyzed=3,
        scanners_run=["bandit"],
        base_ref="main",
    )

    json_str = render_json_report(report)
    parsed_json = json.loads(json_str)
    assert parsed_json["schema_version"] == "1.0"
    assert parsed_json["summary"]["total_findings"] == 1
    assert parsed_json["findings"][0]["rule_id"] == "S324"

    reconstructed = validate_json_report_payload(json_str)
    assert reconstructed.report_id == report.report_id
    assert reconstructed.summary.total_findings == 1
    assert reconstructed.findings[0].title == f.title
    assert reconstructed.findings[0].finding_id == f.finding_id


def test_validate_json_rejects_unsupported_version():
    """Verify validation fails if schema_version is unsupported."""
    bad_payload = {
        "schema_version": "9.9",
        "report_id": "abc",
        "created_at": "2026-10-19T00:00:00Z",
        "repo_root": "/repo",
        "summary": {},
        "findings": [],
    }

    with pytest.raises(SchemaValidationError, match="Unsupported or missing schema_version '9.9'"):
        validate_json_report_payload(bad_payload)


def test_validate_json_rejects_count_mismatch():
    """Verify validation catches discrepancy between summary total and findings array."""
    mismatched = {
        "schema_version": "1.0",
        "report_id": "abc",
        "created_at": "2026-10-19T00:00:00Z",
        "repo_root": "/repo",
        "summary": {
            "total_findings": 5,  # Claims 5 but array has 0
        },
        "findings": [],
    }

    with pytest.raises(SchemaValidationError, match="Summary total_findings \\(5\\) does not match findings count \\(0\\)"):
        validate_json_report_payload(mismatched)
