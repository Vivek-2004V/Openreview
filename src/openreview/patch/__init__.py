"""Safe patch proposals, sandbox validation, and human-approved application."""

from openreview.patch.applier import (
    apply_patch_with_approval,
    atomic_write_text,
    export_patch_to_file,
)
from openreview.patch.docker_sandbox import (
    DockerSandboxConfig,
    DockerSandboxRunner,
    DockerUnavailableError,
    DockerValidationResult,
    SnapshotSizeExceededError,
)
from openreview.patch.generator import (
    create_patch_for_finding,
    generate_unified_patch,
)
from openreview.patch.models import (
    ConcurrentModificationError,
    HumanApprovalRequiredError,
    PatchProposal,
    PatchValidationError,
    PatchValidationResult,
)
from openreview.patch.sandbox import SandboxPatchTester
from openreview.patch.validator import (
    validate_patch_integrity,
    validate_patch_proposal_paths,
)

__all__ = [
    "ConcurrentModificationError",
    "DockerSandboxConfig",
    "DockerSandboxRunner",
    "DockerUnavailableError",
    "DockerValidationResult",
    "HumanApprovalRequiredError",
    "PatchProposal",
    "PatchValidationError",
    "PatchValidationResult",
    "SandboxPatchTester",
    "SnapshotSizeExceededError",
    "apply_patch_with_approval",
    "atomic_write_text",
    "create_patch_for_finding",
    "export_patch_to_file",
    "generate_unified_patch",
    "validate_patch_integrity",
    "validate_patch_proposal_paths",
]
