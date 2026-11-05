"""Base protocols and models for deterministic scanner adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from openreview.schemas.models import Finding


class ScannerStatus(str, Enum):
    """Execution status of an external scanner."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    ERROR = "error"


@dataclass(frozen=True)
class ScannerContext:
    """Execution context and limits provided to a scanner adapter."""

    repo_root: Path
    target_files: list[str]
    timeout_seconds: int = 30
    max_output_bytes: int = 1_000_000
    max_memory_bytes: int | None = 1024 * 1024 * 1024
    extra_args: list[str] = field(default_factory=list)
    changed_lines_by_file: dict[str, set[int]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.repo_root, Path):
            object.__setattr__(self, "repo_root", Path(self.repo_root))
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {self.timeout_seconds}")
        if self.max_output_bytes <= 0:
            raise ValueError(
                f"max_output_bytes must be > 0, got {self.max_output_bytes}"
            )
        if self.max_memory_bytes is not None and self.max_memory_bytes <= 0:
            raise ValueError(
                f"max_memory_bytes must be > 0, got {self.max_memory_bytes}"
            )


@dataclass(frozen=True)
class ScannerResult:
    """Normalized result returned by a scanner adapter run."""

    scanner_name: str
    status: ScannerStatus
    duration_seconds: float
    findings: list[Finding]
    exit_code: int | None = None
    raw_output: str | None = None
    error_message: str | None = None

    @property
    def is_successful(self) -> bool:
        """True if scanner completed normally (regardless of whether findings were found)."""
        return self.status == ScannerStatus.SUCCESS


@runtime_checkable
class ScannerAdapter(Protocol):
    """Protocol that all scanner adapters must satisfy."""

    @property
    def name(self) -> str:
        """Unique identifier of the scanner (e.g. 'ruff', 'eslint', 'bandit')."""
        ...

    @property
    def supported_extensions(self) -> set[str]:
        """File extensions this scanner can analyze (e.g. {'.py', '.pyi'})."""
        ...

    def is_available(self) -> bool:
        """Check whether the required executable is installed and available in PATH."""
        ...

    def filter_target_files(self, files: Sequence[str]) -> list[str]:
        """Filter file list to only those matching supported extensions."""
        ...

    def run(self, context: ScannerContext) -> ScannerResult:
        """Execute the scanner on the target files within the provided context."""
        ...
