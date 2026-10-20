"""Path boundary and safety validation for patch proposals per ADR-005."""

from __future__ import annotations

import re
from pathlib import Path

from openreview.patch.models import PatchProposal, PatchValidationError
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
            raise PathTraversalError(f"Patch header path '{clean_hp}' escapes workspace: {exc}") from exc

    return resolved_target
