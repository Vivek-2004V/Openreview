"""Security test suite validating path traversal defense and command argument boundaries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openreview.security.path_guard import (
    PathTraversalError,
    sanitize_relative_path,
    validate_safe_path,
)
from openreview.security.subprocess_guard import (
    SubprocessArgumentError,
    validate_subprocess_args,
)


def test_validate_safe_path_valid(tmp_path: Path) -> None:
    """Ensure safe paths inside workspace are resolved correctly."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    child_file = repo_dir / "src" / "main.py"
    child_file.parent.mkdir()
    child_file.write_text("print(1)")

    resolved = validate_safe_path("src/main.py", repo_dir)
    assert resolved == child_file.resolve()


def test_validate_safe_path_traversal_rejected(tmp_path: Path) -> None:
    """Ensure parent traversal sequences escaping base directory are blocked."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Traversal escaping repo
    with pytest.raises(PathTraversalError, match="escapes repository boundary"):
        validate_safe_path("../../etc/passwd", repo_dir)

    with pytest.raises(PathTraversalError, match="escapes repository boundary"):
        validate_safe_path("../other_folder/file.py", repo_dir)


def test_validate_safe_path_null_byte_rejected(tmp_path: Path) -> None:
    """Ensure null bytes in paths raise PathTraversalError."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with pytest.raises(PathTraversalError, match="null byte"):
        validate_safe_path("src/main.py\0.txt", repo_dir)


def test_sanitize_relative_path() -> None:
    """Ensure relative path sanitization strips leading slashes and rejects traversal."""
    assert sanitize_relative_path("/src/app.py") == "src/app.py"
    assert sanitize_relative_path("./src/app.py") == "src/app.py"
    assert sanitize_relative_path(r"src\app.py") == "src/app.py"

    with pytest.raises(PathTraversalError, match="Directory traversal component"):
        sanitize_relative_path("src/../../secret.txt")


def test_validate_subprocess_args_valid() -> None:
    """Ensure clean command lists pass validation."""
    cmd = ["git", "diff", "--no-color", "HEAD~1"]
    validated = validate_subprocess_args(cmd)
    assert validated == cmd


def test_validate_subprocess_args_empty_rejected() -> None:
    """Ensure empty command list raises error."""
    with pytest.raises(SubprocessArgumentError, match="cannot be empty"):
        validate_subprocess_args([])


def test_validate_subprocess_args_null_byte_or_control_chars_rejected() -> None:
    """Ensure null bytes and newline injection in arguments raise error."""
    with pytest.raises(SubprocessArgumentError, match="control or null characters"):
        validate_subprocess_args(["git", "diff", "path\0injection"])

    with pytest.raises(SubprocessArgumentError, match="control or null characters"):
        validate_subprocess_args(["git", "commit", "-m", "line1\nline2\rcarriage"])


def test_validate_subprocess_args_empty_binary_rejected() -> None:
    """Ensure empty binary name raises error."""
    with pytest.raises(SubprocessArgumentError, match="Executable command name cannot be empty"):
        validate_subprocess_args(["   ", "arg1"])
