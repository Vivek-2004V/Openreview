"""Ephemeral repository-level sandbox patch application and multi-scanner validation per ADR-005."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from openreview.adapters.base import ScannerContext, ScannerStatus
from openreview.adapters.eslint import ESLintAdapter
from openreview.adapters.ruff import RuffAdapter
from openreview.config import OpenReviewConfig
from openreview.patch.docker_sandbox import DockerSandboxRunner
from openreview.patch.models import PatchProposal, PatchValidationResult
from openreview.patch.validator import validate_patch_proposal_paths
from openreview.repo.git import GitError, run_git_command
from openreview.security.path_guard import validate_safe_path

logger = logging.getLogger(__name__)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".tox",
    ".idea",
    ".vscode",
}


def _ignore_repo_patterns(path: str, names: list[str]) -> set[str]:
    """Filter out version control, virtualenvs, caches, and build artifacts from sandbox replication."""
    ignored = set()
    for name in names:
        if name in EXCLUDED_DIR_NAMES or name.endswith((".pyc", ".egg-info")):
            ignored.add(name)
    return ignored


def copy_snapshot_preventing_symlink_escape(src_root: Path, dest_root: Path) -> None:
    """Safely replicate repository tree into sandbox directory, preventing symlink traversal and escapes."""
    resolved_src = Path(src_root).resolve()
    resolved_dest = Path(dest_root).resolve()

    for root, dirs, files in os.walk(resolved_src, followlinks=False):
        current_dir = Path(root)
        try:
            rel_dir = current_dir.relative_to(resolved_src)
        except ValueError:
            continue

        # Filter out ignored directories in-place
        dirs[:] = [
            d
            for d in dirs
            if d not in EXCLUDED_DIR_NAMES and not d.endswith((".egg-info",))
        ]

        target_dir = resolved_dest / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for file_name in files:
            if file_name.endswith((".pyc", ".pyo")):
                continue

            src_file = current_dir / file_name
            dest_file = target_dir / file_name

            if src_file.is_symlink():
                try:
                    resolved_link = src_file.resolve()
                    # Verify target stays inside resolved_src
                    resolved_link.relative_to(resolved_src)
                    # Safe relative symlink inside repo
                    rel_target = os.path.relpath(resolved_link, current_dir)
                    dest_file.symlink_to(rel_target)
                except (ValueError, OSError):
                    logger.warning(
                        "Skipping external symlink '%s' during repository snapshot creation",
                        src_file,
                    )
                    continue
            elif src_file.is_file():
                try:
                    rel_to_dest = dest_file.relative_to(resolved_dest)
                    validate_safe_path(str(rel_to_dest), resolved_dest)
                    shutil.copy2(src_file, dest_file)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping file '%s' due to copy error: %s", src_file, exc)


class SandboxPatchTester:
    """Tests patch proposals in an isolated disposable sandbox before human presentation."""

    @staticmethod
    def test_patch_in_sandbox(
        repo_root: Path,
        proposal: PatchProposal,
        config: OpenReviewConfig | None = None,
        timeout_seconds: int = 15,
        full_repo: bool = True,
        test_command: Sequence[str] | str | None = None,
        runner: DockerSandboxRunner | None = None,
        require_docker: bool = False,
    ) -> PatchValidationResult:
        """Apply patch to a temporary disposable worktree and verify with static scanners and tests."""
        start_time = time.perf_counter()
        resolved_root = Path(repo_root).resolve()

        # 1. Validate boundary containment
        try:
            validate_patch_proposal_paths(proposal, resolved_root)
        except Exception as exc:  # noqa: BLE001
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

        # 2. Check original file hash if provided
        if proposal.original_file_sha256:
            current_sha256 = hashlib.sha256(src_target.read_bytes()).hexdigest()
            if current_sha256 != proposal.original_file_sha256:
                duration = time.perf_counter() - start_time
                return PatchValidationResult(
                    patch_id=proposal.patch_id,
                    applies_cleanly=False,
                    resolved_finding=False,
                    new_findings_count=0,
                    scanner_status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    error_message=(
                        f"Target file '{proposal.target_file}' was modified since patch generation "
                        f"(expected SHA256 {proposal.original_file_sha256}, current {current_sha256})"
                    ),
                )

        # 3. Setup disposable sandbox (recreating full repository environment by default)
        with tempfile.TemporaryDirectory(
            prefix="openreview_patch_sandbox_"
        ) as temp_dir:
            sandbox_dir = Path(temp_dir).resolve()
            dest_target = sandbox_dir / proposal.target_file
            sandbox_mode = "repo"

            if full_repo:
                try:
                    copy_snapshot_preventing_symlink_escape(resolved_root, sandbox_dir)
                except (OSError, shutil.Error):
                    # Graceful fallback to single-file copy if directory copy fails
                    sandbox_mode = "file"
                    dest_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_target, dest_target)
            else:
                sandbox_mode = "file"
                dest_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_target, dest_target)

            # Write patch file into sandbox
            patch_file = sandbox_dir / "proposal.patch"
            patch_file.write_text(proposal.unified_diff, encoding="utf-8")

            # 4. Test patch application using git apply or direct replacement
            applies_cleanly = False
            try:
                run_git_command(
                    ["apply", "--whitespace=nowarn", str(patch_file)],
                    cwd=sandbox_dir,
                    timeout_seconds=timeout_seconds,
                )
                applies_cleanly = True
            except (GitError, OSError):
                # Fallback: test direct string substitution
                try:
                    content = dest_target.read_text(encoding="utf-8")
                    if proposal.original_snippet in content:
                        new_content = content.replace(
                            proposal.original_snippet,
                            proposal.replacement_snippet,
                            1,
                        )
                        dest_target.write_text(new_content, encoding="utf-8")
                        applies_cleanly = True
                    else:
                        applies_cleanly = False
                except (OSError, UnicodeDecodeError):
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
                    sandbox_mode=sandbox_mode,
                )

            # 5. Syntax and language structural parsing validation
            syntax_valid = True
            syntax_error = None
            if dest_target.suffix == ".py":
                try:
                    ast.parse(
                        dest_target.read_text(encoding="utf-8"),
                        filename=str(dest_target),
                    )
                except SyntaxError as exc:
                    syntax_valid = False
                    syntax_error = f"Python syntax error in patched file: {exc}"
            elif dest_target.suffix == ".json":
                try:
                    json.loads(dest_target.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    syntax_valid = False
                    syntax_error = f"JSON decode error in patched file: {exc}"

            if not syntax_valid:
                duration = time.perf_counter() - start_time
                return PatchValidationResult(
                    patch_id=proposal.patch_id,
                    applies_cleanly=True,
                    resolved_finding=False,
                    new_findings_count=1,
                    scanner_status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    error_message=syntax_error,
                    syntax_valid=False,
                    sandbox_mode=sandbox_mode,
                )

            # 6. Multi-scanner verification on patched file
            scanner_status = ScannerStatus.SUCCESS
            new_findings = []
            scanners_run = []

            # Python: Ruff scanner
            ruff = RuffAdapter()
            if ruff.is_available() and dest_target.suffix in ruff.supported_extensions:
                scanners_run.append("ruff")
                ctx = ScannerContext(
                    repo_root=sandbox_dir,
                    target_files=[proposal.target_file],
                    timeout_seconds=timeout_seconds,
                )
                scan_res = ruff.run(ctx)
                scanner_status = scan_res.status
                new_findings.extend(scan_res.findings)

            # JavaScript / TypeScript: ESLint scanner
            eslint = ESLintAdapter()
            if (
                eslint.is_available()
                and dest_target.suffix in eslint.supported_extensions
            ):
                scanners_run.append("eslint")
                ctx = ScannerContext(
                    repo_root=sandbox_dir,
                    target_files=[proposal.target_file],
                    timeout_seconds=timeout_seconds,
                )
                scan_res = eslint.run(ctx)
                if scan_res.status != ScannerStatus.SUCCESS:
                    scanner_status = scan_res.status
                new_findings.extend(scan_res.findings)

            # 7. Validation Execution (Docker Sandbox for any untrusted test execution)
            if require_docker or runner is not None or test_command is not None:
                docker_runner = runner or DockerSandboxRunner()
                if not docker_runner.is_available():
                    duration = time.perf_counter() - start_time
                    return PatchValidationResult(
                        patch_id=proposal.patch_id,
                        applies_cleanly=True,
                        resolved_finding=False,
                        new_findings_count=len(new_findings),
                        scanner_status=ScannerStatus.ERROR,
                        duration_seconds=duration,
                        error_message=(
                            "Docker unavailable on host. Restricted container execution is required "
                            "for untrusted patch validation. Refusing to execute untrusted code on host."
                        ),
                        syntax_valid=syntax_valid,
                        test_passed=False,
                        scanners_run=scanners_run,
                        sandbox_mode="docker",
                    )

                if not docker_runner.image_exists():
                    duration = time.perf_counter() - start_time
                    return PatchValidationResult(
                        patch_id=proposal.patch_id,
                        applies_cleanly=True,
                        resolved_finding=False,
                        new_findings_count=len(new_findings),
                        scanner_status=ScannerStatus.ERROR,
                        duration_seconds=duration,
                        error_message=(
                            f"Validator Docker image '{docker_runner.config.image}' is missing. "
                            f"Build it with 'docker build -t {docker_runner.config.image} -f Dockerfile.validator .'"
                        ),
                        syntax_valid=syntax_valid,
                        test_passed=False,
                        scanners_run=scanners_run,
                        sandbox_mode="docker",
                    )

                docker_res = docker_runner.run_validation(
                    repository_snapshot=sandbox_dir,
                    test_command=test_command,
                    timeout_seconds=timeout_seconds,
                )
                duration = time.perf_counter() - start_time
                return PatchValidationResult(
                    patch_id=proposal.patch_id,
                    applies_cleanly=True,
                    resolved_finding=docker_res.success and len(new_findings) == 0,
                    new_findings_count=len(new_findings),
                    scanner_status=docker_res.status,
                    duration_seconds=duration,
                    error_message=docker_res.error_message,
                    syntax_valid=syntax_valid,
                    test_passed=docker_res.success,
                    test_command=docker_res.container_name,
                    test_output=(docker_res.stdout + "\n" + docker_res.stderr).strip(),
                    scanners_run=scanners_run,
                    sandbox_mode="docker",
                    docker_result=docker_res,
                )

            # Static-only validation result (no dynamic test command requested, no host execution performed)
            duration = time.perf_counter() - start_time
            return PatchValidationResult(
                patch_id=proposal.patch_id,
                applies_cleanly=True,
                resolved_finding=len(new_findings) == 0,
                new_findings_count=len(new_findings),
                scanner_status=scanner_status,
                duration_seconds=duration,
                error_message=None,
                syntax_valid=syntax_valid,
                test_passed=None,
                test_command=None,
                test_output=None,
                scanners_run=scanners_run,
                sandbox_mode=sandbox_mode,
            )
