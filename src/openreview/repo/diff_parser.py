"""Unified diff parser for changed files, hunks, and line ranges."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from openreview.repo.diff_models import DiffRequest, DiffTarget
from openreview.repo.git import run_git_command


class FileChangeType(str, Enum):
    """Classification of file change."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True)
class LineRange:
    """Inclusive line range [start, end] in the new file version."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ValueError(f"LineRange start must be >= 1, got {self.start}")
        if self.end < self.start:
            raise ValueError(f"LineRange end ({self.end}) cannot be less than start ({self.start})")

    def contains(self, line_number: int) -> bool:
        return self.start <= line_number <= self.end


@dataclass(frozen=True)
class Hunk:
    """A single unified diff hunk."""

    old_start: int
    old_length: int
    new_start: int
    new_length: int
    header: str
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileDiff:
    """Representation of changes to a single file."""

    path: str
    old_path: str | None
    change_type: FileChangeType
    is_binary: bool
    hunks: list[Hunk]
    changed_line_ranges: list[LineRange]
    raw_patch: str
    is_oversized: bool = False
    warning: str | None = None

    @property
    def is_reviewable(self) -> bool:
        """True if the file can be inspected by text linters and code-review models."""
        return not self.is_binary and not self.is_oversized and self.change_type != FileChangeType.DELETED


@dataclass(frozen=True)
class ParsedDiff:
    """Complete collection of parsed file diffs and summary statistics."""

    files: list[FileDiff]
    total_files: int
    total_additions: int
    total_deletions: int
    is_truncated: bool = False
    truncation_reason: str | None = None

    def get_file_diff(self, path: str) -> FileDiff | None:
        for f in self.files:
            if f.path == path or f.old_path == path:
                return f
        return None

    @property
    def changed_paths(self) -> list[str]:
        """All paths that were touched (excluding deleted files)."""
        return [f.path for f in self.files if f.change_type != FileChangeType.DELETED]

    @property
    def reviewable_files(self) -> list[FileDiff]:
        """Files that are text-based, not deleted, and within size boundaries."""
        return [f for f in self.files if f.is_reviewable]

    @property
    def reviewable_paths(self) -> list[str]:
        """POSIX relative paths of reviewable files."""
        return [f.path for f in self.reviewable_files]


# Regex for hunk header: @@ -1,5 +1,6 @@ optional section header
HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")


def parse_unified_diff(
    raw_diff: str,
    max_files: int = 100,
    max_file_bytes: int = 1_000_000,
) -> ParsedDiff:
    """Parse raw unified diff text into structured ParsedDiff model."""
    if not raw_diff or not raw_diff.strip():
        return ParsedDiff(
            files=[],
            total_files=0,
            total_additions=0,
            total_deletions=0,
        )

    files: list[FileDiff] = []
    total_additions = 0
    total_deletions = 0
    is_truncated = False
    truncation_reason = None

    # Split by file boundary: "diff --git "
    diff_blocks = raw_diff.split("diff --git ")
    for block in diff_blocks:
        if not block.strip():
            continue

        if len(files) >= max_files:
            is_truncated = True
            truncation_reason = f"Diff exceeds maximum file limit of {max_files}"
            break

        lines = block.splitlines()
        header_line = lines[0]  # e.g. "a/src/file.py b/src/file.py"

        # Determine old and new paths
        paths = header_line.split(" ")
        old_path = paths[0].removeprefix("a/") if len(paths) >= 1 else None
        new_path = paths[1].removeprefix("b/") if len(paths) >= 2 else old_path

        change_type = FileChangeType.MODIFIED
        is_binary = False
        raw_patch_lines: list[str] = [f"diff --git {block}"]

        for line in lines[1:]:
            if line.startswith("new file mode"):
                change_type = FileChangeType.ADDED
                old_path = None
            elif line.startswith("deleted file mode"):
                change_type = FileChangeType.DELETED
                new_path = old_path
            elif line.startswith("similarity index"):
                change_type = FileChangeType.RENAMED
            elif line.startswith("rename from"):
                old_path = line.removeprefix("rename from ").strip()
                change_type = FileChangeType.RENAMED
            elif line.startswith("rename to"):
                new_path = line.removeprefix("rename to ").strip()
                change_type = FileChangeType.RENAMED
            elif "Binary files" in line or "GIT binary patch" in line:
                is_binary = True

        block_bytes = len(block.encode("utf-8"))
        is_oversized = block_bytes > max_file_bytes
        warning = (
            f"File diff size exceeds limit ({block_bytes} > {max_file_bytes} bytes)"
            if is_oversized
            else None
        )

        hunks: list[Hunk] = []
        changed_line_numbers: set[int] = set()

        if not is_binary and not is_oversized:
            current_hunk_header = ""
            current_hunk_lines: list[str] = []
            old_start, old_len, new_start, new_len = 0, 0, 0, 0
            current_new_line = 0

            for line in lines:
                hunk_match = HUNK_HEADER_RE.match(line)
                if hunk_match:
                    if current_hunk_header:
                        hunks.append(
                            Hunk(
                                old_start=old_start,
                                old_length=old_len,
                                new_start=new_start,
                                new_length=new_len,
                                header=current_hunk_header,
                                lines=current_hunk_lines,
                            )
                        )
                    current_hunk_header = line
                    current_hunk_lines = []
                    old_start = int(hunk_match.group(1))
                    old_len = int(hunk_match.group(2) or 1)
                    new_start = int(hunk_match.group(3))
                    new_len = int(hunk_match.group(4) or 1)
                    current_new_line = new_start
                elif current_hunk_header:
                    current_hunk_lines.append(line)
                    if line.startswith("+") and not line.startswith("+++"):
                        changed_line_numbers.add(current_new_line)
                        total_additions += 1
                        current_new_line += 1
                    elif line.startswith("-") and not line.startswith("---"):
                        total_deletions += 1
                    elif line.startswith(" ") or line == "":
                        current_new_line += 1

            if current_hunk_header:
                hunks.append(
                    Hunk(
                        old_start=old_start,
                        old_length=old_len,
                        new_start=new_start,
                        new_length=new_len,
                        header=current_hunk_header,
                        lines=current_hunk_lines,
                    )
                )

        # Merge changed lines into contiguous ranges
        changed_ranges: list[LineRange] = []
        if changed_line_numbers:
            sorted_lines = sorted(changed_line_numbers)
            range_start = sorted_lines[0]
            prev_line = sorted_lines[0]

            for line in sorted_lines[1:]:
                if line == prev_line + 1:
                    prev_line = line
                else:
                    changed_ranges.append(LineRange(start=range_start, end=prev_line))
                    range_start = line
                    prev_line = line
            changed_ranges.append(LineRange(start=range_start, end=prev_line))

        primary_path = new_path or old_path or "unknown"
        files.append(
            FileDiff(
                path=primary_path,
                old_path=old_path,
                change_type=change_type,
                is_binary=is_binary,
                hunks=hunks,
                changed_line_ranges=changed_ranges,
                raw_patch="\n".join(raw_patch_lines),
                is_oversized=is_oversized,
                warning=warning,
            )
        )

    return ParsedDiff(
        files=files,
        total_files=len(files),
        total_additions=total_additions,
        total_deletions=total_deletions,
        is_truncated=is_truncated,
        truncation_reason=truncation_reason,
    )


def extract_git_diff(request: DiffRequest) -> ParsedDiff:
    """Execute git diff according to DiffRequest parameters and return parsed model."""
    cmd_args = [
        "-C",
        str(request.repo_root),
        "diff",
        f"-U{request.context_lines}",
        "--no-color",
        "--find-renames",
    ]

    if request.target == DiffTarget.COMMITS_RANGE:
        if request.head_ref:
            cmd_args.append(f"{request.base_ref}...{request.head_ref}")
        else:
            cmd_args.append(str(request.base_ref))
    elif request.target == DiffTarget.STAGED:
        cmd_args.append("--cached")
    elif request.target == DiffTarget.WORKING_TREE:
        if request.base_ref:
            cmd_args.append(str(request.base_ref))

    if request.paths:
        cmd_args.append("--")
        cmd_args.extend(request.paths)

    raw_diff = run_git_command(cmd_args)
    return parse_unified_diff(
        raw_diff,
        max_files=request.max_files,
        max_file_bytes=request.max_file_bytes,
    )
