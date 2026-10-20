"""Diff request and target models for OpenReview AI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence


class DiffTarget(str, Enum):
    """Target source for diff extraction."""

    WORKING_TREE = "working_tree"
    STAGED = "staged"
    COMMITS_RANGE = "commits_range"


class DiffRequestError(Exception):
    """Raised when a DiffRequest is invalid or has inconsistent arguments."""


@dataclass(frozen=True)
class DiffRequest:
    """Specification of diff extraction parameters and bounds."""

    repo_root: Path
    target: DiffTarget = DiffTarget.WORKING_TREE
    base_ref: str | None = None
    head_ref: str | None = None
    paths: list[str] = field(default_factory=list)
    max_files: int = 100
    max_file_bytes: int = 1_000_000
    context_lines: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.repo_root, Path):
            object.__setattr__(self, "repo_root", Path(self.repo_root))

        if self.max_files <= 0:
            raise DiffRequestError(f"max_files must be greater than 0, got {self.max_files}")

        if self.max_file_bytes <= 0:
            raise DiffRequestError(f"max_file_bytes must be greater than 0, got {self.max_file_bytes}")

        if self.context_lines < 0:
            raise DiffRequestError(f"context_lines must be >= 0, got {self.context_lines}")

        if self.target == DiffTarget.COMMITS_RANGE and not self.base_ref:
            raise DiffRequestError("base_ref is required when target is COMMITS_RANGE")

    @classmethod
    def for_working_tree(
        cls,
        repo_root: str | Path,
        base_ref: str | None = None,
        paths: Sequence[str] | None = None,
        max_files: int = 100,
        max_file_bytes: int = 1_000_000,
        context_lines: int = 3,
    ) -> DiffRequest:
        """Helper to create a DiffRequest for uncommitted working tree changes."""
        return cls(
            repo_root=Path(repo_root),
            target=DiffTarget.WORKING_TREE,
            base_ref=base_ref,
            head_ref=None,
            paths=list(paths or []),
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            context_lines=context_lines,
        )

    @classmethod
    def for_commits(
        cls,
        repo_root: str | Path,
        base_ref: str,
        head_ref: str = "HEAD",
        paths: Sequence[str] | None = None,
        max_files: int = 100,
        max_file_bytes: int = 1_000_000,
        context_lines: int = 3,
    ) -> DiffRequest:
        """Helper to create a DiffRequest comparing two Git commits or branches."""
        return cls(
            repo_root=Path(repo_root),
            target=DiffTarget.COMMITS_RANGE,
            base_ref=base_ref,
            head_ref=head_ref,
            paths=list(paths or []),
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            context_lines=context_lines,
        )
