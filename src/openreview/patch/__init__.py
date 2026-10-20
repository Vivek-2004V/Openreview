"""Safe patch proposals, sandbox validation, and human-approved application."""

from openreview.patch.applier import apply_patch_with_approval, export_patch_to_file
from openreview.patch.generator import (
    create_patch_for_finding,
    generate_unified_patch,
)
from openreview.patch.models import (
    HumanApprovalRequiredError,
    PatchProposal,
    PatchValidationError,
    PatchValidationResult,
)
from openreview.patch.sandbox import SandboxPatchTester
from openreview.patch.validator import validate_patch_proposal_paths

__all__ = [
    "HumanApprovalRequiredError",
    "PatchProposal",
    "PatchValidationError",
    "PatchValidationResult",
    "SandboxPatchTester",
    "apply_patch_with_approval",
    "create_patch_for_finding",
    "export_patch_to_file",
    "generate_unified_patch",
    "validate_patch_proposal_paths",
]
