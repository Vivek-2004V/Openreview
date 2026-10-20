"""Data models for safe patch proposals per ADR-005 and Epic E7."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from openreview.adapters.base import ScannerStatus
from openreview.schemas.models import Finding


class HumanApprovalRequiredError(PermissionError):
    """Raised when an attempt is made to apply a patch without explicit human approval."""


class PatchValidationError(ValueError):
    """Raised when a patch proposal fails safety, boundary, or diff validation."""


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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if not self.target_file.strip():
            raise PatchValidationError("target_file cannot be empty")
        if not self.unified_diff.strip():
            raise PatchValidationError("unified_diff cannot be empty")
        if not self.patch_id:
            h = hashlib.sha256(f"{self.target_file}:{self.unified_diff}".encode("utf-8")).hexdigest()[:12]
            object.__setattr__(self, "patch_id", f"patch_{h}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "target_file": self.target_file,
            "title": self.title,
            "description": self.description,
            "unified_diff": self.unified_diff,
            "finding_id": self.finding_id,
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

    @property
    def is_safe_to_propose(self) -> bool:
        """True if the patch applies without conflicts, introduces no new defects, and passes scanners."""
        return self.applies_cleanly and self.new_findings_count == 0 and self.scanner_status == ScannerStatus.SUCCESS
