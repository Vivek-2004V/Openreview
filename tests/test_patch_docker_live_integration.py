"""Live integration tests for Docker execution sandbox (P0).

These tests run against the REAL Docker daemon and the 'openreview-patch-validator:latest'
container image. They verify real process isolation, network airgap, read-only rootfs,
resource limits, and filesystem protection.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openreview.adapters.base import ScannerStatus
from openreview.patch.docker_sandbox import DockerSandboxConfig, DockerSandboxRunner
from openreview.patch.generator import generate_unified_patch
from openreview.patch.sandbox import SandboxPatchTester

# Check whether Docker binary, daemon, and the validator image are all live
runner_check = DockerSandboxRunner()
DOCKER_LIVE = runner_check.is_available() and runner_check.image_exists()

pytestmark = pytest.mark.skipif(
    not DOCKER_LIVE,
    reason="Docker daemon or 'openreview-patch-validator:latest' image is not available",
)


def compute_repo_hash_tree(repo_dir: Path) -> dict[str, str]:
    """Compute sha256 hash map for every file in repo_dir to verify strict immutability."""
    tree: dict[str, str] = {}
    for p in sorted(repo_dir.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = str(p.relative_to(repo_dir))
            tree[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return tree


def test_docker_live_valid_patch_execution_succeeds(tmp_path: Path) -> None:
    """Verify that a valid patch is tested and executed inside real Docker sandbox."""
    repo = tmp_path / "valid_repo"
    repo.mkdir()

    app_file = repo / "calculator.py"
    orig_code = "def add(a, b):\n    return a - b  # Bug: minus instead of plus\n"
    app_file.write_text(orig_code, encoding="utf-8")

    test_file = repo / "test_calculator.py"
    test_file.write_text(
        "import calculator\n\ndef test_add():\n    assert calculator.add(2, 3) == 5\n",
        encoding="utf-8",
    )

    fixed_code = "def add(a, b):\n    return a + b\n"
    proposal = generate_unified_patch(
        target_file="calculator.py",
        original_code=orig_code,
        replacement_code=fixed_code,
    )

    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=proposal,
        test_command=["python", "-m", "pytest", "test_calculator.py"],
        require_docker=True,
    )

    assert res.applies_cleanly is True
    assert res.syntax_valid is True
    assert res.test_passed is True
    assert res.scanner_status == ScannerStatus.SUCCESS
    assert res.sandbox_mode == "docker"
    assert res.docker_result is not None
    assert res.docker_result.exit_code == 0
    assert "1 passed" in res.test_output


def test_docker_live_invalid_python_syntax_fails(tmp_path: Path) -> None:
    """Verify that malformed Python syntax is detected and rejected without running tests."""
    repo = tmp_path / "syntax_repo"
    repo.mkdir()

    src = repo / "main.py"
    orig_code = "def run():\n    return 1\n"
    src.write_text(orig_code, encoding="utf-8")

    malformed_code = "def run():\n    return ((\n"
    proposal = generate_unified_patch(
        target_file="main.py",
        original_code=orig_code,
        replacement_code=malformed_code,
    )

    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=proposal,
        test_command=["python", "main.py"],
        require_docker=True,
    )

    assert res.applies_cleanly is True
    assert res.syntax_valid is False
    assert res.test_passed is not True
    assert res.scanner_status == ScannerStatus.ERROR
    assert "syntax error" in (res.error_message or "").lower()


def test_docker_live_malicious_test_cannot_modify_original_repository(
    tmp_path: Path,
) -> None:
    """Verify that a malicious test script in container cannot modify the original host repository."""
    repo = tmp_path / "target_repo"
    repo.mkdir()

    protected_file = repo / "secure_data.py"
    protected_content = "SECRET_API_KEY = 'unchanged_original_content'\n"
    protected_file.write_text(protected_content, encoding="utf-8")

    # Write malicious test script into repo
    exploit_script = repo / "test_exploit.py"
    exploit_script.write_text(
        "import os\n"
        "try:\n"
        "    with open('/snapshot/secure_data.py', 'w') as f:\n"
        "        f.write('DEFACED')\n"
        "except Exception as e:\n"
        "    print('Snapshot write blocked:', e)\n"
        "try:\n"
        "    with open('/etc/evil.txt', 'w') as f:\n"
        "        f.write('ROOTFS_BREACH')\n"
        "except Exception as e:\n"
        "    print('Rootfs write blocked:', e)\n",
        encoding="utf-8",
    )

    initial_hashes = compute_repo_hash_tree(repo)

    # Malicious proposal: test script attempts to overwrite /snapshot and /etc
    patch = generate_unified_patch(
        target_file="secure_data.py",
        original_code=protected_content,
        replacement_code="SECRET_API_KEY = 'patched_value'\n",
    )

    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=patch,
        test_command=["python", "test_exploit.py"],
        require_docker=True,
    )

    # Verify original repository is completely unmodified
    post_hashes = compute_repo_hash_tree(repo)
    assert post_hashes == initial_hashes
    assert protected_file.read_text(encoding="utf-8") == protected_content

    # Container output should show write blocks due to readonly mounts
    assert "Snapshot write blocked" in res.test_output
    assert "Rootfs write blocked" in res.test_output


def test_docker_live_network_access_blocked(tmp_path: Path) -> None:
    """Verify that --network none blocks all outbound network access inside container."""
    repo = tmp_path / "network_repo"
    repo.mkdir()

    app_file = repo / "app.py"
    orig_code = "x = 1\n"
    app_file.write_text(orig_code, encoding="utf-8")

    net_script = repo / "test_net.py"
    net_script.write_text(
        "import socket\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(2.0)\n"
        "s.connect(('1.1.1.1', 80))\n",
        encoding="utf-8",
    )

    proposal = generate_unified_patch(
        target_file="app.py",
        original_code=orig_code,
        replacement_code="x = 2\n",
    )

    res = SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=proposal,
        test_command=["python", "test_net.py"],
        require_docker=True,
    )

    # Must fail because network is unreachable
    assert res.test_passed is False
    assert res.docker_result is not None
    assert res.docker_result.exit_code != 0
    # Common Linux network-disabled error strings
    assert any(
        err in res.test_output.lower()
        for err in ("network is unreachable", "errno 101", "timed out", "oserror")
    )


def test_docker_live_missing_image_returns_error(tmp_path: Path) -> None:
    """Verify that a missing or non-existent Docker image fails closed with explicit ERROR."""
    repo = tmp_path / "img_repo"
    repo.mkdir()
    (repo / "file.py").write_text("a = 1\n", encoding="utf-8")

    cfg = DockerSandboxConfig(image="openreview-nonexistent-image:missing_tag")
    runner = DockerSandboxRunner(config=cfg)

    res = runner.run_validation(
        repository_snapshot=repo,
        test_command=["python", "--version"],
    )

    assert res.success is False
    assert res.status == ScannerStatus.ERROR
    assert "is missing" in (res.error_message or "").lower()


def test_docker_live_timeout_limits(tmp_path: Path) -> None:
    """Verify that hanging containers are terminated after timeout."""
    repo = tmp_path / "timeout_repo"
    repo.mkdir()
    (repo / "file.py").write_text("a = 1\n", encoding="utf-8")

    cfg = DockerSandboxConfig(timeout_seconds=2)
    runner = DockerSandboxRunner(config=cfg)

    res = runner.run_validation(
        repository_snapshot=repo,
        test_command=["python", "-c", "import time; time.sleep(10)"],
    )

    assert res.success is False
    assert res.timed_out is True
    assert res.status == ScannerStatus.TIMEOUT
    assert "timed out" in (res.error_message or "").lower()


def test_docker_live_output_limits(tmp_path: Path) -> None:
    """Verify that output floods trigger early termination and MALFORMED_OUTPUT."""
    repo = tmp_path / "flood_repo"
    repo.mkdir()
    (repo / "file.py").write_text("a = 1\n", encoding="utf-8")

    cfg = DockerSandboxConfig(max_output_bytes=2048)
    runner = DockerSandboxRunner(config=cfg)

    res = runner.run_validation(
        repository_snapshot=repo,
        test_command=["python", "-c", "import sys; sys.stdout.write('X' * 500000)"],
    )

    assert res.success is False
    assert res.output_overflow is True
    assert res.status == ScannerStatus.MALFORMED_OUTPUT
    assert "exceeded output limit" in (res.error_message or "").lower()


def test_docker_live_original_repository_hash_remains_unchanged(tmp_path: Path) -> None:
    """Verify that after multiple Docker validation runs, the original repo hash is unchanged."""
    repo = tmp_path / "integrity_repo"
    repo.mkdir()

    (repo / "core.py").write_text("def core(): return 100\n", encoding="utf-8")
    (repo / "utils.py").write_text("def util(): return 200\n", encoding="utf-8")
    sub = repo / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("NESTED = 'val'\n", encoding="utf-8")

    initial_hashes = compute_repo_hash_tree(repo)

    patch = generate_unified_patch(
        target_file="core.py",
        original_code="def core(): return 100\n",
        replacement_code="def core(): return 101\n",
    )

    # Run execution in container
    SandboxPatchTester.test_patch_in_sandbox(
        repo_root=repo,
        proposal=patch,
        test_command=["python", "-c", "import core; assert core.core() == 101"],
        require_docker=True,
    )

    final_hashes = compute_repo_hash_tree(repo)
    assert final_hashes == initial_hashes
