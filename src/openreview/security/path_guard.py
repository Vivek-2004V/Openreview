"""Path traversal validation and repository boundary containment per Gate D."""

from __future__ import annotations

from pathlib import Path


class PathTraversalError(ValueError):
    """Raised when a file path escapes the configured workspace boundary."""


def validate_safe_path(target_path: str | Path, base_dir: str | Path) -> Path:
    """Validate that target_path resides safely within base_dir without escaping.

    Raises PathTraversalError if:
    - Path contains null bytes
    - Path traverses outside base_dir using parent directories (..)
    - Path resolves to a target outside base_dir (including via symlinks)
    """
    path_str = str(target_path)
    if "\0" in path_str:
        raise PathTraversalError("Path contains prohibited null byte")

    base_resolved = Path(base_dir).resolve()

    # If target is relative, resolve relative to base_resolved
    raw_path = Path(target_path)
    if not raw_path.is_absolute():
        candidate = (base_resolved / raw_path).resolve()
    else:
        candidate = raw_path.resolve()

    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise PathTraversalError(
            f"Path '{target_path}' escapes repository boundary '{base_resolved}'"
        )

    return candidate


def sanitize_relative_path(path: str | Path) -> str:
    """Normalize relative path for reports and diff models, stripping leading slashes and drive letters."""
    p_str = str(path).replace("\\", "/").strip()
    if "\0" in p_str:
        raise PathTraversalError("Path contains prohibited null byte")

    # Disallow directory traversal sequences in relative identifiers
    parts = p_str.split("/")
    if ".." in parts:
        raise PathTraversalError(f"Directory traversal component '..' found in path '{path}'")

    cleaned = p_str.lstrip("./").lstrip("/")
    return cleaned
