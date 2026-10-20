"""Safe Git operations and repository status detection."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GitError(Exception):
    """Base exception for Git operations."""


class NotAGitRepositoryError(GitError):
    """Raised when the target directory is not within a Git repository."""


class GitCommandError(GitError):
    """Raised when a git command fails with non-zero exit code."""

    def __init__(self, command: Sequence[str], exit_code: int, stderr: str):
        self.command = list(command)
        self.exit_code = exit_code
        self.stderr = stderr.strip()
        super().__init__(f"Git command {' '.join(command)} failed ({exit_code}): {self.stderr}")


@dataclass(frozen=True)
class GitRepoInfo:
    """Snapshot of Git repository status and boundaries."""

    root: Path
    head_ref: str
    head_commit: str
    is_dirty: bool
    uncommitted_files: list[str]


def run_git_command(
    args: Sequence[str],
    cwd: str | Path | None = None,
    timeout_seconds: int = 15,
) -> str:
    """Execute a git command safely using an argument array without shell expansion."""
    from openreview.security.subprocess_guard import validate_subprocess_args

    cmd = ["git"] + list(args)
    validated_cmd = validate_subprocess_args(cmd)
    working_dir = str(cwd) if cwd else None

    try:
        process = subprocess.run(
            validated_cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Git command {' '.join(cmd)} timed out after {timeout_seconds}s") from exc
    except Exception as exc:
        raise GitError(f"Failed to execute git command: {exc}") from exc

    if process.returncode != 0:
        err = process.stderr.strip()
        if "not a git repository" in err.lower():
            raise NotAGitRepositoryError(f"Directory is not a git repository: {cwd or '.'}")
        raise GitCommandError(cmd, process.returncode, err)

    return process.stdout.strip()


def detect_repo_root(path: str | Path = ".") -> Path:
    """Discover the canonical root directory of the Git repository."""
    target_path = Path(path).resolve()
    if not target_path.exists():
        raise NotAGitRepositoryError(f"Specified path does not exist: {target_path}")

    stdout = run_git_command(["-C", str(target_path), "rev-parse", "--show-toplevel"])
    return Path(stdout).resolve()


def inspect_git_status(path: str | Path = ".") -> GitRepoInfo:
    """Collect repository status including root, HEAD, and dirty state."""
    repo_root = detect_repo_root(path)

    # HEAD revision / branch
    try:
        head_ref = run_git_command(["-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
    except GitError:
        head_ref = "UNKNOWN"

    try:
        head_commit = run_git_command(["-C", str(repo_root), "rev-parse", "HEAD"])
    except GitError:
        head_commit = "EMPTY_REPO"

    # Working tree changes
    status_output = run_git_command(["-C", str(repo_root), "status", "--porcelain=v1"])
    uncommitted = []
    if status_output:
        for line in status_output.splitlines():
            line = line.strip()
            if line:
                # Format: XY PATH
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    uncommitted.append(parts[1])

    is_dirty = len(uncommitted) > 0

    return GitRepoInfo(
        root=repo_root,
        head_ref=head_ref,
        head_commit=head_commit,
        is_dirty=is_dirty,
        uncommitted_files=uncommitted,
    )


def verify_revision_exists(revision: str, repo_root: str | Path) -> bool:
    """Verify whether a git revision (branch, tag, commit SHA) exists in the repository."""
    try:
        run_git_command(["-C", str(repo_root), "rev-parse", "--verify", f"{revision}^{{commit}}"])
        return True
    except GitCommandError:
        return False
