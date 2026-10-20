"""Human approval gating and safe patch application per ADR-005."""

from __future__ import annotations

from pathlib import Path

from openreview.patch.models import HumanApprovalRequiredError, PatchProposal
from openreview.patch.validator import validate_patch_proposal_paths


def export_patch_to_file(proposal: PatchProposal, output_path: str | Path) -> Path:
    """Export unified diff patch proposal to a file for human inspection."""
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proposal.unified_diff, encoding="utf-8")
    return out


def apply_patch_with_approval(
    repo_root: Path,
    proposal: PatchProposal,
    approved: bool = False,
) -> bool:
    """Apply a validated patch proposal to the repository working tree ONLY with human approval.

    Raises:
    - HumanApprovalRequiredError: if approved is False.
    - PathTraversalError: if patch target escapes repository boundaries.
    - ValueError: if original code snippet is not found in target file.
    """
    if not approved:
        raise HumanApprovalRequiredError(
            "Explicit human approval (--approve) is strictly required to apply patch proposals per ADR-005."
        )

    resolved_root = Path(repo_root).resolve()
    target_file = validate_patch_proposal_paths(proposal, resolved_root)

    content = target_file.read_text(encoding="utf-8")
    if proposal.original_snippet not in content:
        raise ValueError(
            f"Original code snippet for patch '{proposal.patch_id}' was not found in target file '{proposal.target_file}'."
        )

    new_content = content.replace(proposal.original_snippet, proposal.replacement_snippet, 1)
    target_file.write_text(new_content, encoding="utf-8")
    return True
