"""Human approval gating and safe patch application per ADR-005."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from openreview.patch.models import (
    ConcurrentModificationError,
    HumanApprovalRequiredError,
    PatchProposal,
    PatchValidationError,
)
from openreview.patch.validator import validate_patch_proposal_paths


def atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write text to file_path using a temporary file in the same directory and os.replace."""
    resolved_path = Path(file_path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(resolved_path.parent),
        prefix=".tmp_patch_",
        delete=False,
        encoding=encoding,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())

    try:
        # Preserve permissions if file already exists
        if resolved_path.exists():
            shutil.copymode(resolved_path, temp_path)

        os.replace(temp_path, resolved_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def export_patch_to_file(proposal: PatchProposal, output_path: str | Path) -> Path:
    """Export unified diff patch proposal to a file for human inspection."""
    out = Path(output_path).resolve()
    atomic_write_text(out, proposal.unified_diff, encoding="utf-8")
    return out


def apply_patch_with_approval(
    repo_root: Path,
    proposal: PatchProposal,
    approved: bool = False,
) -> bool:
    """Apply a validated patch proposal to the repository working tree ONLY with human approval.

    Guarantees:
    1. Human Approval Gate: Blocks application unless approved is explicitly True (ADR-005).
    2. Boundary Containment: Verifies target path does not escape repository boundary.
    3. Concurrent Modification Protection: Computes target file SHA-256 and verifies it matches
       the proposal's original_file_sha256 to prevent overwriting concurrent edits.
    4. Code Snippet Verification: Confirms original snippet is present in the target file.
    5. Atomic Write: Writes modification via atomic file replacement (os.replace) to prevent partial writes.

    Raises:
    - HumanApprovalRequiredError: if approved is False.
    - PathTraversalError: if patch target escapes repository boundaries.
    - ConcurrentModificationError: if target file was modified after proposal generation.
    - PatchValidationError: if original code snippet is not found in target file.
    """
    if not approved:
        raise HumanApprovalRequiredError(
            "Explicit human approval (--approve) is strictly required to apply patch proposals per ADR-005."
        )

    resolved_root = Path(repo_root).resolve()
    target_file = validate_patch_proposal_paths(proposal, resolved_root)

    if not target_file.is_file():
        raise PatchValidationError(
            f"Target file for patch '{proposal.patch_id}' does not exist: {target_file}"
        )

    content = target_file.read_text(encoding="utf-8")

    # Concurrent modification verification
    if proposal.original_file_sha256:
        current_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if current_sha256 != proposal.original_file_sha256:
            raise ConcurrentModificationError(
                f"Target file '{proposal.target_file}' was modified since patch '{proposal.patch_id}' "
                f"was generated (expected SHA256 {proposal.original_file_sha256}, got {current_sha256}). "
                "Refusing to apply patch to prevent overwriting concurrent modifications."
            )

    if proposal.original_snippet not in content:
        raise PatchValidationError(
            f"Original code snippet for patch '{proposal.patch_id}' was not found in target file '{proposal.target_file}'."
        )

    new_content = content.replace(
        proposal.original_snippet, proposal.replacement_snippet, 1
    )
    atomic_write_text(target_file, new_content, encoding="utf-8")
    return True
