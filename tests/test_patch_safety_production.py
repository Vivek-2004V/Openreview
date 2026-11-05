"""Comprehensive tests for production-safe patch proposal system (Issue 5: Patch Proposal Safety)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openreview.adapters.base import ScannerStatus
from openreview.patch.applier import (
    apply_patch_with_approval,
    atomic_write_text,
)
from openreview.patch.docker_sandbox import DockerValidationResult
from openreview.patch.generator import (
    create_patch_for_finding,
    generate_unified_patch,
)
from openreview.patch.models import (
    ConcurrentModificationError,
)
from openreview.patch.sandbox import SandboxPatchTester
from openreview.patch.validator import validate_patch_integrity
from openreview.schemas.models import Finding, FindingOrigin, Severity


def test_proposal_includes_original_file_sha256(tmp_path: Path) -> None:
    """Verify that patch proposals automatically compute and record the original file SHA-256."""
    file_path = tmp_path / "module.py"
    content = "def calculate(x):\n    return x * 2\n"
    file_path.write_text(content, encoding="utf-8")
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    finding = Finding(
        title="Double logic check",
        explanation="Consider checking negative inputs",
        path="module.py",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
        start_line=1,
        end_line=2,
    )

    proposal = create_patch_for_finding(
        repo_root=tmp_path,
        finding=finding,
        replacement_snippet="def calculate(x):\n    return x * 3\n",
    )

    assert proposal.original_file_sha256 == expected_hash
    assert proposal.to_dict()["original_file_sha256"] == expected_hash


def test_concurrent_modification_rejection_in_applier(tmp_path: Path) -> None:
    """Verify apply_patch_with_approval rejects patch if file was modified after proposal generation."""
    target_file = tmp_path / "service.py"
    initial_code = "SECRET_KEY = 'v1'\n"
    target_file.write_text(initial_code, encoding="utf-8")

    # Generate proposal targeting initial_code
    proposal = generate_unified_patch(
        target_file="service.py",
        original_code=initial_code,
        replacement_code="SECRET_KEY = 'v2'\n",
    )

    # Concurrently modify the file on disk
    target_file.write_text("SECRET_KEY = 'v1_concurrent_edit'\n", encoding="utf-8")

    with pytest.raises(ConcurrentModificationError) as exc_info:
        apply_patch_with_approval(tmp_path, proposal, approved=True)

    assert "was modified since patch" in str(exc_info.value)
    assert "Refusing to apply patch" in str(exc_info.value)
    # Target file must remain untouched with the concurrent modification
    assert (
        target_file.read_text(encoding="utf-8") == "SECRET_KEY = 'v1_concurrent_edit'\n"
    )


def test_atomic_write_text_replaces_atomically(tmp_path: Path) -> None:
    """Verify atomic_write_text writes completely and replaces destination without leaving temp files."""
    dest = tmp_path / "subdir" / "target.txt"
    dest.parent.mkdir(parents=True)
    dest.write_text("old version", encoding="utf-8")

    new_content = "new updated version with atomic guarantee"
    atomic_write_text(dest, new_content)

    assert dest.read_text(encoding="utf-8") == new_content

    # Ensure no leftover temporary files in directory
    temp_files = list(dest.parent.glob(".tmp_patch_*"))
    assert len(temp_files) == 0


def test_repository_level_sandbox_cross_file_validation(tmp_path: Path) -> None:
    """Verify repository-level sandbox recreates full repository so cross-file imports succeed."""
    repo = tmp_path / "full_repo"
    repo.mkdir()

    # Create module_a.py (dependency)
    dep = repo / "module_a.py"
    dep.write_text(
        '"""Module A."""\n\n\ndef helper():\n    return 42\n', encoding="utf-8"
    )

    # Create module_b.py (target to patch)
    target = repo / "module_b.py"
    target_code = '"""Module B."""\n\n\ndef run():\n    return 0\n'
    target.write_text(target_code, encoding="utf-8")

    # Proposal patches module_b to import and use module_a
    patched_code = '"""Module B."""\n\nfrom module_a import helper\n\n\ndef run():\n    return helper()\n'
    proposal = generate_unified_patch(
        target_file="module_b.py",
        original_code=target_code,
        replacement_code=patched_code,
    )

    # Mock runner simulating successful test execution inside isolated container
    mock_runner = MagicMock()
    mock_runner.is_available.return_value = True
    mock_runner.image_exists.return_value = True
    mock_runner.run_validation.return_value = DockerValidationResult(
        success=True,
        exit_code=0,
        stdout="42\n",
        stderr="",
        duration_seconds=0.1,
        status=ScannerStatus.SUCCESS,
        container_name="test_val_container",
    )

    # Test patch with verification command executing the cross-file import in sandbox
    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=proposal,
        full_repo=True,
        test_command=["python3", "-c", "import module_b; assert module_b.run() == 42"],
        runner=mock_runner,
    )

    assert res.applies_cleanly is True
    assert res.syntax_valid is True
    assert res.test_passed is True
    assert res.sandbox_mode == "docker"
    assert res.is_safe_to_propose is True


def test_sandbox_detects_python_syntax_errors(tmp_path: Path) -> None:
    """Verify SandboxPatchTester catches invalid Python syntax in patched code."""
    repo = tmp_path / "repo_syntax"
    repo.mkdir()
    target = repo / "broken.py"
    valid_code = "x = 10\n"
    target.write_text(valid_code, encoding="utf-8")

    # Malformed Python patch missing operand
    broken_code = "x = 10 +\ndef foo(\n"
    proposal = generate_unified_patch(
        target_file="broken.py",
        original_code=valid_code,
        replacement_code=broken_code,
    )

    res = SandboxPatchTester.test_patch_in_sandbox(repo_root=repo, proposal=proposal)

    assert res.applies_cleanly is True
    assert res.syntax_valid is False
    assert "Python syntax error" in (res.error_message or "")
    assert res.is_safe_to_propose is False


def test_sandbox_detects_test_suite_failures(tmp_path: Path) -> None:
    """Verify SandboxPatchTester catches test suite / build failures caused by patch."""
    repo = tmp_path / "repo_test_fail"
    repo.mkdir()
    target = repo / "calc.py"
    target.write_text("def subtract(a, b):\n    return a - b\n", encoding="utf-8")

    # Broken patch: flips subtraction to addition
    broken_patch = generate_unified_patch(
        target_file="calc.py",
        original_code="def subtract(a, b):\n    return a - b\n",
        replacement_code="def subtract(a, b):\n    return a + b\n",
    )

    # Test command expecting 5 - 2 == 3, failing due to bad patch
    mock_runner = MagicMock()
    mock_runner.is_available.return_value = True
    mock_runner.image_exists.return_value = True
    mock_runner.run_validation.return_value = DockerValidationResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="AssertionError: assert 5 + 2 == 3",
        duration_seconds=0.1,
        status=ScannerStatus.ERROR,
        error_message="Validation command failed with exit code 1",
        container_name="test_val_fail_container",
    )
    test_cmd = ["python3", "-c", "import calc; assert calc.subtract(5, 2) == 3"]
    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=broken_patch,
        test_command=test_cmd,
        runner=mock_runner,
    )

    assert res.applies_cleanly is True
    assert res.syntax_valid is True
    assert res.test_passed is False
    assert "Validation command failed" in (res.error_message or "")
    assert res.is_safe_to_propose is False


def test_validate_patch_integrity_checks_hash_and_snippet(tmp_path: Path) -> None:
    """Verify validate_patch_integrity checks paths, file existence, snippet and hash."""
    target = tmp_path / "file.py"
    target.write_text("original_content = True\n", encoding="utf-8")

    proposal = generate_unified_patch(
        target_file="file.py",
        original_code="original_content = True\n",
        replacement_code="original_content = False\n",
    )

    resolved = validate_patch_integrity(proposal, tmp_path)
    assert resolved == target.resolve()

    # Tamper with file
    target.write_text("modified_unexpected = True\n", encoding="utf-8")
    with pytest.raises(ConcurrentModificationError):
        validate_patch_integrity(proposal, tmp_path)
