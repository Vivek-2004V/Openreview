"""Unified diff patch proposal generator per ADR-005."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from openreview.patch.models import PatchProposal, PatchValidationError
from openreview.schemas.models import Finding


def generate_unified_patch(
    target_file: str,
    original_code: str,
    replacement_code: str,
    title: str = "",
    description: str = "",
    finding_id: str | None = None,
    original_file_sha256: str | None = None,
) -> PatchProposal:
    """Generate a standard Git-compatible unified diff proposal between original and modified code."""
    norm_path = target_file.replace("\\", "/").strip().lstrip("./")
    if not norm_path:
        raise PatchValidationError("Target file path cannot be empty")

    original_lines = original_code.splitlines(keepends=True)
    replacement_lines = replacement_code.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            original_lines,
            replacement_lines,
            fromfile=f"a/{norm_path}",
            tofile=f"b/{norm_path}",
        )
    )

    if not diff_lines:
        raise PatchValidationError(
            "No difference between original and replacement code"
        )

    diff_text = "".join(diff_lines)
    patch_title = title or f"Fix proposed for {norm_path}"
    patch_desc = (
        description or f"Automated patch addressing finding {finding_id or 'generic'}"
    )

    computed_sha256 = (
        original_file_sha256
        or hashlib.sha256(original_code.encode("utf-8")).hexdigest()
    )

    return PatchProposal(
        patch_id="",  # Auto-generated in __post_init__
        target_file=norm_path,
        title=patch_title,
        description=patch_desc,
        unified_diff=diff_text,
        original_snippet=original_code,
        replacement_snippet=replacement_code,
        finding_id=finding_id,
        original_file_sha256=computed_sha256,
    )


def create_patch_for_finding(
    repo_root: Path,
    finding: Finding,
    replacement_snippet: str,
    title: str = "",
    description: str = "",
) -> PatchProposal:
    """Construct a PatchProposal targeting the specific line range of a review finding."""
    file_path = repo_root / finding.path
    if not file_path.is_file():
        raise PatchValidationError(
            f"Target file for finding does not exist: {file_path}"
        )

    full_content = file_path.read_text(encoding="utf-8", errors="replace")
    original_sha256 = hashlib.sha256(full_content.encode("utf-8")).hexdigest()
    lines = full_content.splitlines(keepends=True)

    start_line = finding.start_line or 1
    end_line = finding.end_line or start_line

    if start_line < 1 or start_line > len(lines):
        raise PatchValidationError(
            f"Finding start_line {start_line} is outside file lines ({len(lines)})"
        )

    # 1-indexed to 0-indexed slice
    slice_start = start_line - 1
    slice_end = min(len(lines), end_line)

    before_lines = lines[:slice_start]
    after_lines = lines[slice_end:]

    # Ensure replacement has newline if original had
    clean_replacement = replacement_snippet
    if not clean_replacement.endswith("\n") and (lines and lines[-1].endswith("\n")):
        clean_replacement += "\n"

    new_full_content = "".join(before_lines) + clean_replacement + "".join(after_lines)

    return generate_unified_patch(
        target_file=finding.path,
        original_code=full_content,
        replacement_code=new_full_content,
        title=title or f"Fix: {finding.title}",
        description=description or (finding.recommendation or finding.explanation),
        finding_id=finding.finding_id,
        original_file_sha256=original_sha256,
    )
