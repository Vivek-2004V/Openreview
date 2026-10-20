"""JSON renderer and schema validator for OpenReview AI reports."""

from __future__ import annotations

import json
from typing import Any, Mapping

from openreview.schemas.models import (
    SCHEMA_VERSION,
    Finding,
    ReviewReport,
    ReviewSummary,
    SchemaValidationError,
)


def render_json_report(report: ReviewReport, indent: int = 2) -> str:
    """Render a ReviewReport model to a formatted JSON string."""
    data = report.to_dict()
    return json.dumps(data, indent=indent, ensure_ascii=False)


def validate_json_report_payload(payload: str | Mapping[str, Any]) -> ReviewReport:
    """Validate a raw JSON string or mapping against the ReviewReport v1.0 schema."""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(f"Invalid JSON string: {exc}") from exc
    elif isinstance(payload, Mapping):
        data = dict(payload)
    else:
        raise SchemaValidationError(f"Expected str or Mapping, got {type(payload).__name__}")

    if not isinstance(data, dict):
        raise SchemaValidationError("Root report JSON must be a key-value object")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SchemaValidationError(
            f"Unsupported or missing schema_version '{schema_version}'. Expected '{SCHEMA_VERSION}'"
        )

    for field_name in ["report_id", "created_at", "repo_root", "summary", "findings"]:
        if field_name not in data:
            raise SchemaValidationError(f"Missing required report field: '{field_name}'")

    summary_raw = data["summary"]
    if not isinstance(summary_raw, dict):
        raise SchemaValidationError("Field 'summary' must be an object")

    raw_findings = data["findings"]
    if not isinstance(raw_findings, list):
        raise SchemaValidationError("Field 'findings' must be an array")

    # Validate each finding
    parsed_findings: list[Finding] = []
    for idx, raw_f in enumerate(raw_findings):
        if not isinstance(raw_f, dict):
            raise SchemaValidationError(f"Finding at index {idx} must be an object")
        try:
            parsed_findings.append(Finding.from_dict(raw_f))
        except SchemaValidationError as exc:
            raise SchemaValidationError(f"Finding at index {idx} is invalid: {exc}") from exc

    # Cross-field consistency check
    claimed_total = summary_raw.get("total_findings")
    actual_total = len(parsed_findings)
    if claimed_total is not None and claimed_total != actual_total:
        raise SchemaValidationError(
            f"Summary total_findings ({claimed_total}) does not match findings count ({actual_total})"
        )

    summary = ReviewSummary(
        total_files_analyzed=summary_raw.get("total_files_analyzed", 0),
        total_findings=actual_total,
        findings_by_severity=summary_raw.get("findings_by_severity", {}),
        findings_by_origin=summary_raw.get("findings_by_origin", {}),
        scanners_run=list(summary_raw.get("scanners_run", [])),
        scanners_failed=list(summary_raw.get("scanners_failed", [])),
        ai_enabled=summary_raw.get("ai_enabled", False),
        ai_model=summary_raw.get("ai_model"),
    )

    return ReviewReport(
        schema_version=schema_version,
        report_id=data["report_id"],
        created_at=data["created_at"],
        repo_root=data["repo_root"],
        base_ref=data.get("base_ref"),
        head_ref=data.get("head_ref"),
        summary=summary,
        findings=parsed_findings,
        tool_diagnostics=data.get("tool_diagnostics", []),
    )
