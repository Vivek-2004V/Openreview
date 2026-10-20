"""Unit tests for versioned Finding and ReviewReport schemas per E2-T4."""

import pytest
from openreview.schemas import (
    SCHEMA_VERSION,
    Confidence,
    Finding,
    FindingOrigin,
    FindingStatus,
    ReviewReport,
    SchemaValidationError,
    Severity,
    compute_finding_id,
)


def test_finding_defaults_and_stable_id():
    """Verify default values and deterministic ID generation."""
    f = Finding(
        title="SQL injection risk in user input query",
        explanation="Query formatted with raw string concatenation.",
        path="src/db.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=42,
        end_line=45,
        rule_id="S608",
    )

    assert f.schema_version == SCHEMA_VERSION
    assert len(f.finding_id) == 16
    assert f.severity == Severity.HIGH
    assert f.origin == FindingOrigin.STATIC
    assert f.status == FindingStatus.OPEN

    # Verifying ID stability
    expected_id = compute_finding_id(
        path="src/db.py",
        rule_id="S608",
        start_line=42,
        title="SQL injection risk in user input query",
    )
    assert f.finding_id == expected_id


def test_finding_validation_errors():
    """Verify validation boundaries for findings."""
    with pytest.raises(SchemaValidationError, match="Finding title cannot be empty"):
        Finding(title=" ", explanation="test", path="src/app.py")

    with pytest.raises(SchemaValidationError, match="Finding path cannot be empty"):
        Finding(title="Valid", explanation="test", path=" ")

    with pytest.raises(SchemaValidationError, match="start_line must be >= 1"):
        Finding(title="Valid", explanation="test", path="src/app.py", start_line=0)

    with pytest.raises(SchemaValidationError, match="end_line .* cannot be less than start_line"):
        Finding(title="Valid", explanation="test", path="src/app.py", start_line=10, end_line=5)


def test_finding_dict_roundtrip():
    """Verify to_dict and from_dict preserve all schema properties."""
    f = Finding(
        title="Unused variable 'x'",
        explanation="Variable x is assigned but never referenced.",
        path="tests/test_x.py",
        severity=Severity.LOW,
        confidence=Confidence.HIGH,
        origin=FindingOrigin.STATIC,
        status=FindingStatus.OPEN,
        start_line=12,
        end_line=12,
        rule_id="F841",
        evidence="x = 10",
        recommendation="Remove unused variable or prefix with underscore.",
        references=["https://docs.astral.sh/ruff/rules/unused-variable/"],
    )

    as_dict = f.to_dict()
    assert as_dict["schema_version"] == SCHEMA_VERSION
    assert as_dict["rule_id"] == "F841"
    assert as_dict["severity"] == "low"

    rebuilt = Finding.from_dict(as_dict)
    assert rebuilt == f


def test_review_report_creation_and_metrics():
    """Verify ReviewReport computes aggregated metrics accurately."""
    f1 = Finding(
        title="Hardcoded API key",
        explanation="Potential secret token found.",
        path="config.py",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
        start_line=5,
    )
    f2 = Finding(
        title="Simplify loop comprehension",
        explanation="Can use list comprehension instead of manual append.",
        path="utils.py",
        severity=Severity.INFO,
        origin=FindingOrigin.AI,
        start_line=20,
    )

    report = ReviewReport.create(
        repo_root="/workspace/project",
        findings=[f1, f2],
        total_files_analyzed=15,
        scanners_run=["ruff", "bandit"],
        ai_enabled=True,
        ai_model="qwen2.5-coder:7b",
        base_ref="main",
    )

    assert report.schema_version == SCHEMA_VERSION
    assert report.summary.total_files_analyzed == 15
    assert report.summary.total_findings == 2
    assert report.summary.findings_by_severity["critical"] == 1
    assert report.summary.findings_by_severity["info"] == 1
    assert report.summary.findings_by_origin["static"] == 1
    assert report.summary.findings_by_origin["ai"] == 1
    assert report.summary.ai_enabled is True

    report_dict = report.to_dict()
    assert report_dict["summary"]["total_findings"] == 2
    assert len(report_dict["findings"]) == 2
