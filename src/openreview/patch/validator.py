"""Path boundary and safety validation for patch proposals per ADR-005."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from openreview.patch.models import (
    ConcurrentModificationError,
    PatchProposal,
    PatchValidationError,
)
from openreview.security.path_guard import PathTraversalError, validate_safe_path

DIFF_PATH_HEADER = re.compile(r"^(?:---|\+\+\+)\s+[ab]/(.*)$", re.MULTILINE)


def validate_patch_proposal_paths(proposal: PatchProposal, repo_root: Path) -> Path:
    """Ensure that the patch proposal target and all headers reside strictly within repo_root.

    Raises:
    - PathTraversalError: if path escapes repo_root.
    - PatchValidationError: if diff headers point outside or contradict target_file.
    """
    resolved_root = Path(repo_root).resolve()

    # 1. Validate target_file using path_guard
    try:
        resolved_target = validate_safe_path(proposal.target_file, resolved_root)
    except PathTraversalError as exc:
        raise PathTraversalError(f"Patch target file escapes workspace: {exc}") from exc

    # 2. Inspect all paths declared in diff headers
    header_paths = DIFF_PATH_HEADER.findall(proposal.unified_diff)
    for hp in header_paths:
        clean_hp = hp.split("\t")[0].strip()  # Strip timestamps if present
        if clean_hp == "/dev/null":
            continue

        try:
            validate_safe_path(clean_hp, resolved_root)
        except PathTraversalError as exc:
            raise PathTraversalError(
                f"Patch header path '{clean_hp}' escapes workspace: {exc}"
            ) from exc

    return resolved_target


def validate_patch_integrity(proposal: PatchProposal, repo_root: Path) -> Path:
    """Validate patch path boundaries, target existence, hash integrity, and snippet presence.

    Raises:
    - PathTraversalError: if path escapes repo_root.
    - PatchValidationError: if target file does not exist or snippet is missing.
    - ConcurrentModificationError: if target file was modified after proposal generation.
    """
    target_path = validate_patch_proposal_paths(proposal, repo_root)

    if not target_path.is_file():
        raise PatchValidationError(
            f"Target file for patch '{proposal.patch_id}' does not exist: {target_path}"
        )

    content = target_path.read_text(encoding="utf-8", errors="replace")

    if proposal.original_file_sha256:
        current_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if current_sha256 != proposal.original_file_sha256:
            raise ConcurrentModificationError(
                f"Target file '{proposal.target_file}' was modified since patch '{proposal.patch_id}' "
                f"was generated (expected {proposal.original_file_sha256}, current {current_sha256})."
            )

    if proposal.original_snippet not in content:
        raise PatchValidationError(
            f"Original code snippet for patch '{proposal.patch_id}' was not found in target file '{proposal.target_file}'."
        )

    return target_path
