"""Ephemeral sandbox patch application and scanner validation per ADR-005."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from openreview.adapters.base import ScannerContext, ScannerStatus
from openreview.adapters.ruff import RuffAdapter
from openreview.config import OpenReviewConfig
from openreview.patch.models import PatchProposal, PatchValidationResult
from openreview.patch.validator import validate_patch_proposal_paths
from openreview.repo.git import GitError, run_git_command


class SandboxPatchTester:
    """Tests patch proposals in an isolated disposable sandbox before human presentation."""

    @staticmethod
    def test_patch_in_sandbox(
        repo_root: Path,
        proposal: PatchProposal,
        config: OpenReviewConfig | None = None,
        timeout_seconds: int = 15,
    ) -> PatchValidationResult:
        """Apply patch to a temporary disposable worktree and verify with static scanners."""
        start_time = time.perf_counter()
        resolved_root = Path(repo_root).resolve()

        # 1. Validate boundary containment
        try:
            validate_patch_proposal_paths(proposal, resolved_root)
        except Exception as exc:
            duration = time.perf_counter() - start_time
            return PatchValidationResult(
                patch_id=proposal.patch_id,
                applies_cleanly=False,
                resolved_finding=False,
                new_findings_count=0,
                scanner_status=ScannerStatus.ERROR,
                duration_seconds=duration,
                error_message=f"Path boundary validation failed: {exc}",
            )

        # 2. Setup disposable sandbox
        with tempfile.TemporaryDirectory(prefix="openreview_patch_sandbox_") as temp_dir:
            sandbox_dir = Path(temp_dir).resolve()

            # Copy target file and directory hierarchy to sandbox
            src_target = resolved_root / proposal.target_file
            if not src_target.is_file():
                duration = time.perf_counter() - start_time
                return PatchValidationResult(
                    patch_id=proposal.patch_id,
                    applies_cleanly=False,
                    resolved_finding=False,
                    new_findings_count=0,
                    scanner_status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    error_message=f"Target file not found: {proposal.target_file}",
                )

            dest_target = sandbox_dir / proposal.target_file
            dest_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_target, dest_target)

            # Write patch file into sandbox
            patch_file = sandbox_dir / "proposal.patch"
            patch_file.write_text(proposal.unified_diff, encoding="utf-8")

            # 3. Test patch application using git apply or direct replacement
            applies_cleanly = False
            try:
                # Try git apply --check first
                run_git_command(
                    ["apply", "--whitespace=nowarn", str(patch_file)],
                    cwd=sandbox_dir,
                    timeout_seconds=timeout_seconds,
                )
                applies_cleanly = True
            except (GitError, Exception):
                # Fallback: test direct string substitution
                try:
                    content = dest_target.read_text(encoding="utf-8")
                    if proposal.original_snippet in content:
                        new_content = content.replace(proposal.original_snippet, proposal.replacement_snippet, 1)
                        dest_target.write_text(new_content, encoding="utf-8")
                        applies_cleanly = True
                    else:
                        applies_cleanly = False
                except Exception:
                    applies_cleanly = False

            if not applies_cleanly:
                duration = time.perf_counter() - start_time
                return PatchValidationResult(
                    patch_id=proposal.patch_id,
                    applies_cleanly=False,
                    resolved_finding=False,
                    new_findings_count=0,
                    scanner_status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    error_message="Patch failed to apply cleanly to target file",
                )

            # 4. Run scanner verification on patched file if scanner available
            scanner_status = ScannerStatus.SUCCESS
            new_findings = []
            ruff = RuffAdapter()
            if ruff.is_available() and dest_target.suffix in ruff.supported_extensions:
                ctx = ScannerContext(
                    repo_root=sandbox_dir,
                    target_files=[proposal.target_file],
                    timeout_seconds=timeout_seconds,
                )
                scan_res = ruff.run(ctx)
                scanner_status = scan_res.status
                new_findings = scan_res.findings

            duration = time.perf_counter() - start_time
            return PatchValidationResult(
                patch_id=proposal.patch_id,
                applies_cleanly=True,
                resolved_finding=len(new_findings) == 0,
                new_findings_count=len(new_findings),
                scanner_status=scanner_status,
                duration_seconds=duration,
            )
