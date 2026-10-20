"""Tests for safe patch proposals, boundary validation, sandbox application, and human approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from openreview.patch.applier import apply_patch_with_approval, export_patch_to_file
from openreview.patch.generator import create_patch_for_finding, generate_unified_patch
from openreview.patch.models import (
    HumanApprovalRequiredError,
    PatchProposal,
    PatchValidationError,
)
from openreview.patch.sandbox import SandboxPatchTester
from openreview.patch.validator import validate_patch_proposal_paths
from openreview.schemas.models import Finding, FindingOrigin, Severity
from openreview.security.path_guard import PathTraversalError


def test_generate_unified_patch_success() -> None:
    """Ensure unified diff patch is generated with valid git headers."""
    orig = "def add(a, b):\n    return a - b\n"
    fixed = "def add(a, b):\n    return a + b\n"

    patch = generate_unified_patch(
        target_file="math_util.py",
        original_code=orig,
        replacement_code=fixed,
        title="Fix subtraction bug",
    )

    assert patch.target_file == "math_util.py"
    assert "--- a/math_util.py" in patch.unified_diff
    assert "+++ b/math_util.py" in patch.unified_diff
    assert "-    return a - b" in patch.unified_diff
    assert "+    return a + b" in patch.unified_diff
    assert patch.patch_id.startswith("patch_")


def test_generate_unified_patch_no_diff_rejected() -> None:
    """Ensure generator rejects identical original and replacement code."""
    code = "x = 10\n"
    with pytest.raises(PatchValidationError, match="No difference"):
        generate_unified_patch("app.py", code, code)


def test_create_patch_for_finding(tmp_path: Path) -> None:
    """Ensure patch can be constructed directly from a Finding object."""
    src_file = tmp_path / "app.py"
    src_file.write_text("line1 = 1\neval(input)\nline3 = 3\n", encoding="utf-8")

    finding = Finding(
        title="Avoid eval",
        explanation="eval is dangerous",
        path="app.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=2,
        end_line=2,
    )

    proposal = create_patch_for_finding(
        repo_root=tmp_path,
        finding=finding,
        replacement_snippet="safe_parse(input)\n",
    )

    assert proposal.target_file == "app.py"
    assert "-eval(input)" in proposal.unified_diff
    assert "+safe_parse(input)" in proposal.unified_diff


def test_patch_path_traversal_rejection(tmp_path: Path) -> None:
    """Ensure patches attempting path traversal outside repository are rejected."""
    bad_proposal = PatchProposal(
        patch_id="",
        target_file="../../etc/passwd",
        title="Malicious patch",
        description="Escapes root",
        unified_diff="--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-root\n+hacked\n",
        original_snippet="root",
        replacement_snippet="hacked",
    )

    with pytest.raises(PathTraversalError):
        validate_patch_proposal_paths(bad_proposal, tmp_path)


def test_sandbox_patch_tester_clean_apply(tmp_path: Path) -> None:
    """Ensure SandboxPatchTester applies patch in disposable sandbox without touching workspace."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "calculator.py"
    target.write_text("def mul(a, b):\n    return a + b\n", encoding="utf-8")

    orig_content = target.read_text(encoding="utf-8")
    proposal = generate_unified_patch(
        target_file="calculator.py",
        original_code=orig_content,
        replacement_code="def mul(a, b):\n    return a * b\n",
    )

    res = SandboxPatchTester.test_patch_in_sandbox(repo, proposal)
    assert res.applies_cleanly is True

    # Real workspace must NOT be modified
    assert target.read_text(encoding="utf-8") == orig_content


def test_sandbox_patch_tester_conflict_handling(tmp_path: Path) -> None:
    """Ensure SandboxPatchTester handles conflicts gracefully when original code does not match."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "calc.py"
    target.write_text("completely_different_code = True\n", encoding="utf-8")

    proposal = PatchProposal(
        patch_id="patch_conflict",
        target_file="calc.py",
        title="Outdated patch",
        description="Conflicts with current state",
        unified_diff="--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-old_line\n+new_line\n",
        original_snippet="old_line",
        replacement_snippet="new_line",
    )

    res = SandboxPatchTester.test_patch_in_sandbox(repo, proposal)
    assert res.applies_cleanly is False
    assert res.is_safe_to_propose is False


def test_human_approval_required_to_apply(tmp_path: Path) -> None:
    """Ensure apply_patch_with_approval rejects application if approved is False (ADR-005)."""
    target = tmp_path / "main.py"
    target.write_text("val = 1\n", encoding="utf-8")

    proposal = generate_unified_patch("main.py", "val = 1\n", "val = 2\n")

    # Call with approved=False (default)
    with pytest.raises(HumanApprovalRequiredError, match="Explicit human approval"):
        apply_patch_with_approval(tmp_path, proposal, approved=False)

    # Workspace untouched
    assert target.read_text(encoding="utf-8") == "val = 1\n"


def test_apply_patch_with_approval_success(tmp_path: Path) -> None:
    """Ensure apply_patch_with_approval modifies target file when approved=True."""
    target = tmp_path / "main.py"
    target.write_text("val = 1\n", encoding="utf-8")

    proposal = generate_unified_patch("main.py", "val = 1\n", "val = 2\n")

    success = apply_patch_with_approval(tmp_path, proposal, approved=True)
    assert success is True
    assert target.read_text(encoding="utf-8") == "val = 2\n"


def test_export_patch_to_file(tmp_path: Path) -> None:
    """Ensure export_patch_to_file writes unified diff to specified path."""
    proposal = generate_unified_patch("test.py", "a = 1\n", "a = 2\n")
    out_file = tmp_path / "patches" / "fix.patch"

    saved_path = export_patch_to_file(proposal, out_file)
    assert saved_path.is_file()
    assert "--- a/test.py" in saved_path.read_text(encoding="utf-8")
