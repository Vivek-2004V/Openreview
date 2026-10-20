"""Repository operations: Git status, diff parsing, path normalization."""

from openreview.repo.diff_models import (
    DiffRequest,
    DiffRequestError,
    DiffTarget,
)
from openreview.repo.diff_parser import (
    FileChangeType,
    FileDiff,
    Hunk,
    LineRange,
    ParsedDiff,
    extract_git_diff,
    parse_unified_diff,
)
from openreview.repo.git import (
    GitCommandError,
    GitError,
    GitRepoInfo,
    NotAGitRepositoryError,
    detect_repo_root,
    inspect_git_status,
    run_git_command,
    verify_revision_exists,
)

__all__ = [
    "DiffRequest",
    "DiffRequestError",
    "DiffTarget",
    "FileChangeType",
    "FileDiff",
    "Hunk",
    "LineRange",
    "ParsedDiff",
    "extract_git_diff",
    "parse_unified_diff",
    "GitCommandError",
    "GitError",
    "GitRepoInfo",
    "NotAGitRepositoryError",
    "detect_repo_root",
    "inspect_git_status",
    "run_git_command",
    "verify_revision_exists",
]
