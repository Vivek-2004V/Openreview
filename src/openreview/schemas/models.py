"""Versioned findings and report schemas for OpenReview AI per TRD Section 4."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.0"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class FindingOrigin(str, Enum):
    STATIC = "static"
    AI = "ai"
    COMBINED = "combined"


class FindingStatus(str, Enum):
    OPEN = "open"
    SUPPRESSED = "suppressed"
    ACCEPTED_RISK = "accepted_risk"
    FIXED = "fixed"
    UNVERIFIED = "unverified"


class SchemaValidationError(Exception):
    """Raised when data fails schema validation rules."""


def compute_finding_id(
    path: str,
    rule_id: str | None,
    start_line: int | None,
    title: str,
) -> str:
    """Derive a stable, deterministic identifier for a finding."""
    normalized_path = path.replace("\\", "/").strip().lower()
    norm_rule = (rule_id or "generic").strip().lower()
    line_str = str(start_line) if start_line is not None else "global"
    seed = f"{norm_rule}:{normalized_path}:{line_str}:{title.strip().lower()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Finding:
    """A single code review finding, security issue, or suggestion."""

    title: str
    explanation: str
    path: str
    severity: Severity = Severity.MEDIUM
    confidence: Confidence = Confidence.MEDIUM
    origin: FindingOrigin = FindingOrigin.STATIC
    status: FindingStatus = FindingStatus.OPEN
    start_line: int | None = None
    end_line: int | None = None
    rule_id: str | None = None
    evidence: str | None = None
    recommendation: str | None = None
    references: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    finding_id: str = field(default="")
    is_pre_existing: bool = False

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise SchemaValidationError("Finding title cannot be empty")
        if not self.path.strip():
            raise SchemaValidationError("Finding path cannot be empty")
        if self.start_line is not None and self.start_line < 1:
            raise SchemaValidationError(
                f"start_line must be >= 1, got {self.start_line}"
            )
        if self.end_line is not None:
            if self.start_line is None:
                raise SchemaValidationError(
                    "start_line must be specified if end_line is set"
                )
            if self.end_line < self.start_line:
                raise SchemaValidationError(
                    f"end_line ({self.end_line}) cannot be less than start_line ({self.start_line})"
                )

        # Automatically compute stable finding_id if not explicitly provided
        if not self.finding_id:
            fid = compute_finding_id(
                self.path, self.rule_id, self.start_line, self.title
            )
            object.__setattr__(self, "finding_id", fid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "finding_id": self.finding_id,
            "title": self.title,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "origin": self.origin.value,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "rule_id": self.rule_id,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "references": list(self.references),
            "status": self.status.value,
            "is_pre_existing": self.is_pre_existing,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Finding:
        try:
            return cls(
                schema_version=data.get("schema_version", SCHEMA_VERSION),
                finding_id=data.get("finding_id", ""),
                title=data["title"],
                severity=Severity(data.get("severity", "medium")),
                confidence=Confidence(data.get("confidence", "medium")),
                origin=FindingOrigin(data.get("origin", "static")),
                path=data["path"],
                start_line=data.get("start_line"),
                end_line=data.get("end_line"),
                rule_id=data.get("rule_id"),
                evidence=data.get("evidence"),
                explanation=data.get("explanation", ""),
                recommendation=data.get("recommendation"),
                references=list(data.get("references", [])),
                status=FindingStatus(data.get("status", "open")),
                is_pre_existing=bool(data.get("is_pre_existing", False)),
            )
        except KeyError as exc:
            raise SchemaValidationError(
                f"Missing required finding field: {exc}"
            ) from exc
        except ValueError as exc:
            raise SchemaValidationError(
                f"Invalid enum value in finding: {exc}"
            ) from exc


@dataclass(frozen=True)
class ReviewSummary:
    """Aggregated metrics for a review run."""

    total_files_analyzed: int
    total_findings: int
    findings_by_severity: dict[str, int]
    findings_by_origin: dict[str, int]
    scanners_run: list[str]
    scanners_failed: list[str]
    ai_enabled: bool
    ai_model: str | None
    new_findings_count: int = 0
    pre_existing_findings_count: int = 0
    ai_suggestions_count: int = 0


@dataclass(frozen=True)
class ReviewReport:
    """Full review report model containing summary and findings."""

    report_id: str
    created_at: str
    repo_root: str
    summary: ReviewSummary
    findings: list[Finding]
    base_ref: str | None = None
    head_ref: str | None = None
    tool_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    new_findings: list[Finding] = field(default_factory=list)
    pre_existing_findings: list[Finding] = field(default_factory=list)
    ai_suggestions: list[Finding] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        repo_root: str,
        findings: Sequence[Finding],
        total_files_analyzed: int,
        scanners_run: Sequence[str] | None = None,
        scanners_failed: Sequence[str] | None = None,
        ai_enabled: bool = False,
        ai_model: str | None = None,
        base_ref: str | None = None,
        head_ref: str | None = None,
        tool_diagnostics: Sequence[dict[str, Any]] | None = None,
        pre_existing_findings: Sequence[Finding] | None = None,
    ) -> ReviewReport:
        """Construct a complete ReviewReport with computed summary metrics."""
        now_iso = datetime.now(UTC).isoformat()
        report_id = hashlib.sha256(
            f"{repo_root}:{now_iso}:{len(findings)}".encode()
        ).hexdigest()[:12]

        all_pre_existing = list(
            pre_existing_findings or [f for f in findings if f.is_pre_existing]
        )
        active_findings = [f for f in findings if not f.is_pre_existing]
        new_findings = [f for f in active_findings if f.origin != FindingOrigin.AI]
        ai_suggestions = [f for f in active_findings if f.origin == FindingOrigin.AI]

        by_severity: dict[str, int] = {s.value: 0 for s in Severity}
        by_origin: dict[str, int] = {o.value: 0 for o in FindingOrigin}

        for f in active_findings:
            by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1
            by_origin[f.origin.value] = by_origin.get(f.origin.value, 0) + 1

        summary = ReviewSummary(
            total_files_analyzed=total_files_analyzed,
            total_findings=len(active_findings),
            findings_by_severity=by_severity,
            findings_by_origin=by_origin,
            scanners_run=list(scanners_run or []),
            scanners_failed=list(scanners_failed or []),
            ai_enabled=ai_enabled,
            ai_model=ai_model,
            new_findings_count=len(new_findings),
            pre_existing_findings_count=len(all_pre_existing),
            ai_suggestions_count=len(ai_suggestions),
        )

        return cls(
            report_id=report_id,
            created_at=now_iso,
            repo_root=repo_root,
            summary=summary,
            findings=active_findings,
            base_ref=base_ref,
            head_ref=head_ref,
            tool_diagnostics=list(tool_diagnostics or []),
            schema_version=SCHEMA_VERSION,
            new_findings=new_findings,
            pre_existing_findings=all_pre_existing,
            ai_suggestions=ai_suggestions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "created_at": self.created_at,
            "repo_root": self.repo_root,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "summary": {
                "total_files_analyzed": self.summary.total_files_analyzed,
                "total_findings": self.summary.total_findings,
                "findings_by_severity": self.summary.findings_by_severity,
                "findings_by_origin": self.summary.findings_by_origin,
                "scanners_run": self.summary.scanners_run,
                "scanners_failed": self.summary.scanners_failed,
                "ai_enabled": self.summary.ai_enabled,
                "ai_model": self.summary.ai_model,
                "new_findings": len(self.new_findings),
                "pre_existing_findings": len(self.pre_existing_findings),
                "ai_suggestions": len(self.ai_suggestions),
                "new_findings_count": self.summary.new_findings_count,
                "pre_existing_findings_count": self.summary.pre_existing_findings_count,
                "ai_suggestions_count": self.summary.ai_suggestions_count,
            },
            "findings": [f.to_dict() for f in self.findings],
            "new_findings": [f.to_dict() for f in self.new_findings],
            "pre_existing_findings": [f.to_dict() for f in self.pre_existing_findings],
            "ai_suggestions": [f.to_dict() for f in self.ai_suggestions],
            "tool_diagnostics": self.tool_diagnostics,
        }
