"""Data models for safe patch proposals per ADR-005 and Epic E7."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from openreview.adapters.base import ScannerStatus


class HumanApprovalRequiredError(PermissionError):
    """Raised when an attempt is made to apply a patch without explicit human approval."""


class PatchValidationError(ValueError):
    """Raised when a patch proposal fails safety, boundary, or diff validation."""


class ConcurrentModificationError(PatchValidationError):
    """Raised when target file was modified after proposal generation, preventing dirty overwrite."""


@dataclass(frozen=True)
class PatchProposal:
    """A proposed code modification represented strictly as a unified diff."""

    patch_id: str
    target_file: str
    title: str
    description: str
    unified_diff: str
    original_snippet: str
    replacement_snippet: str
    finding_id: str | None = None
    original_file_sha256: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not self.target_file.strip():
            raise PatchValidationError("target_file cannot be empty")
        if not self.unified_diff.strip():
            raise PatchValidationError("unified_diff cannot be empty")
        if not self.patch_id:
            h = hashlib.sha256(
                f"{self.target_file}:{self.unified_diff}".encode()
            ).hexdigest()[:12]
            object.__setattr__(self, "patch_id", f"patch_{h}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "target_file": self.target_file,
            "title": self.title,
            "description": self.description,
            "unified_diff": self.unified_diff,
            "finding_id": self.finding_id,
            "original_file_sha256": self.original_file_sha256,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class PatchValidationResult:
    """Outcome of applying and verifying a patch proposal inside a disposable sandbox."""

    patch_id: str
    applies_cleanly: bool
    resolved_finding: bool
    new_findings_count: int
    scanner_status: ScannerStatus
    duration_seconds: float
    error_message: str | None = None
    output_diff: str | None = None
    syntax_valid: bool = True
    test_passed: bool | None = None
    test_command: str | None = None
    test_output: str | None = None
    scanners_run: list[str] = field(default_factory=list)
    sandbox_mode: str = "repo"
    docker_result: Any | None = None

    @property
    def is_safe_to_propose(self) -> bool:
        """True if the patch applies without conflicts, syntax is valid, introduces no new defects, and passes scanners/tests."""
        return (
            self.applies_cleanly
            and self.syntax_valid
            and self.new_findings_count == 0
            and (self.test_passed is not False)
            and self.scanner_status in (ScannerStatus.SUCCESS, ScannerStatus.NOT_FOUND)
        )
