"""Tests for Restricted Docker Execution Sandbox for untrusted patch validation (P0)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openreview.adapters.base import ScannerStatus
from openreview.patch.docker_sandbox import (
    DockerSandboxConfig,
    DockerSandboxRunner,
    SandboxSecurityError,
    SnapshotSizeExceededError,
)
from openreview.patch.generator import generate_unified_patch
from openreview.patch.sandbox import SandboxPatchTester

# Check whether Docker binary and daemon are live and available
DOCKER_AVAILABLE = DockerSandboxRunner().is_available()


def test_sandbox_config_security_validation_invariants() -> None:
    """Verify DockerSandboxConfig enforces non-negotiable security controls."""
    # 1. Non-root user required
    with pytest.raises(SandboxSecurityError, match="Root execution prohibited"):
        cfg = DockerSandboxConfig(user="0")
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="Root execution prohibited"):
        cfg = DockerSandboxConfig(user="root")
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="Root execution prohibited"):
        cfg = DockerSandboxConfig(user="0:1000")
        cfg.validate_security_invariants()

    # 2. Network disabled required
    with pytest.raises(SandboxSecurityError, match="Network access must be disabled"):
        cfg = DockerSandboxConfig(network_disabled=False)
        cfg.validate_security_invariants()

    # 3. Cap drop ALL required
    with pytest.raises(
        SandboxSecurityError, match="All Linux capabilities must be dropped"
    ):
        cfg = DockerSandboxConfig(cap_drop=["NET_ADMIN"])
        cfg.validate_security_invariants()

    # 4. Privilege escalation prohibited
    with pytest.raises(SandboxSecurityError, match="no_new_privileges must be True"):
        cfg = DockerSandboxConfig(no_new_privileges=False)
        cfg.validate_security_invariants()

    # 5. Read-only root filesystem required
    with pytest.raises(SandboxSecurityError, match="root filesystem must be read-only"):
        cfg = DockerSandboxConfig(read_only_rootfs=False)
        cfg.validate_security_invariants()

    # 6. Forbidden secret pattern in environment
    with pytest.raises(SandboxSecurityError, match="Forbidden secret pattern"):
        cfg = DockerSandboxConfig(env_vars={"GITHUB_PAT": "ghp_12345"})
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="Forbidden secret pattern"):
        cfg = DockerSandboxConfig(env_vars={"AWS_SECRET_ACCESS_KEY": "secret"})
        cfg.validate_security_invariants()

    # 7. Positive validation for compliant config
    valid_cfg = DockerSandboxConfig()
    valid_cfg.validate_security_invariants()


def test_build_docker_command_security_flags(tmp_path: Path) -> None:
    """Verify build_docker_command produces exact hardened flags required for sandbox isolation."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

    cfg = DockerSandboxConfig(
        image="openreview-patch-validator:latest",
        user="1000:1000",
        cpus=1.5,
        memory_limit="768m",
        pids_limit=80,
    )
    runner = DockerSandboxRunner(config=cfg)
    cmd = runner.build_docker_command(
        snapshot_path=snapshot_dir,
        test_command=["python3", "-m", "unittest"],
        container_name="test_val_container",
    )

    cmd_str = " ".join(cmd)

    # Invariants checks
    assert "--name test_val_container" in cmd_str
    assert "--rm" in cmd_str
    assert "--network none" in cmd_str
    assert "--user 1000:1000" in cmd_str
    assert "--cap-drop ALL" in cmd_str
    assert "--security-opt no-new-privileges:true" in cmd_str
    assert "--cpus 1.5" in cmd_str
    assert "--memory 768m" in cmd_str
    assert "--memory-swap 768m" in cmd_str
    assert "--pids-limit 80" in cmd_str
    assert "--read-only" in cmd_str
    assert "--tmpfs /workspace:rw,exec,nosuid,size=256m,mode=1777" in cmd_str
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777" in cmd_str
    assert (
        f"--mount type=bind,source={snapshot_dir.resolve()},target=/snapshot,readonly"
        in cmd_str
    )
    assert "openreview-patch-validator:latest" in cmd
    assert "docker.sock" not in cmd_str


def test_home_directory_mount_prohibited() -> None:
    """Verify build_docker_command strictly rejects mounting the host home directory."""
    runner = DockerSandboxRunner()
    with pytest.raises(SandboxSecurityError, match="home directory"):
        runner.build_docker_command(
            snapshot_path=Path.home(),
            test_command=["true"],
            container_name="test_home",
        )


def test_snapshot_size_limit_enforced_on_host(tmp_path: Path) -> None:
    """Verify repository snapshot size quota is checked on host before container creation."""
    snapshot_dir = tmp_path / "oversized_snapshot"
    snapshot_dir.mkdir()
    # Write 500 KB file
    (snapshot_dir / "big.bin").write_bytes(b"A" * 500_000)

    # Set 100 KB max limit
    cfg = DockerSandboxConfig(max_snapshot_bytes=100_000)
    runner = DockerSandboxRunner(config=cfg)

    with pytest.raises(SnapshotSizeExceededError, match="exceeds host quota"):
        runner.build_docker_command(
            snapshot_path=snapshot_dir,
            test_command=["true"],
            container_name="test_size",
        )


def test_docker_unavailable_fails_closed(tmp_path: Path) -> None:
    """Verify run_validation returns ScannerStatus.ERROR and does not run on host when Docker is absent."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner = DockerSandboxRunner()
    with patch.object(runner, "is_available", return_value=False):
        res = runner.run_validation(
            repository_snapshot=snapshot_dir,
            test_command=["python3", "-c", "import os; os.system('echo hacked')"],
        )

    assert res.success is False
    assert res.status == ScannerStatus.ERROR
    assert "Docker unavailable" in (res.error_message or "")
    assert "Refusing to execute untrusted repository scripts on the host" in (
        res.error_message or ""
    )


def test_sandbox_patch_tester_require_docker_fails_closed(tmp_path: Path) -> None:
    """Verify SandboxPatchTester refuses to fall back to host when require_docker=True and Docker is absent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )

    proposal = generate_unified_patch(
        target_file="calc.py",
        original_code="def add(a, b):\n    return a + b\n",
        replacement_code="def add(a, b):\n    return a * b\n",
    )

    runner = DockerSandboxRunner()
    with patch.object(runner, "is_available", return_value=False):
        res = SandboxPatchTester.test_patch_in_sandbox(
            repo_root=repo,
            proposal=proposal,
            runner=runner,
            require_docker=True,
            test_command=["python3", "-c", "import calc; assert calc.add(2, 3) == 6"],
        )

    assert res.scanner_status == ScannerStatus.ERROR
    assert "Docker unavailable on host" in (res.error_message or "")
    assert res.is_safe_to_propose is False
    assert res.sandbox_mode == "docker"


def test_timeout_termination_in_runner(tmp_path: Path) -> None:
    """Verify run_validation terminates container and marks timeout when execution exceeds limit."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner = DockerSandboxRunner(config=DockerSandboxConfig(timeout_seconds=1))

    # Mock subprocess to simulate a hanging process that times out
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.pid = 99999
    mock_proc.poll.return_value = None

    def mock_stream(*args, **kwargs):
        return ([], [b"Execution timed out"], 0, 19, False, True, "timed out")

    with (
        patch.object(runner, "is_available", return_value=True),
        patch.object(runner, "image_exists", return_value=True),
        patch("subprocess.Popen", return_value=mock_proc),
        patch(
            "openreview.patch.docker_sandbox._stream_bounded_output",
            side_effect=mock_stream,
        ),
        patch.object(runner, "_force_cleanup_container") as mock_cleanup,
    ):
        res = runner.run_validation(
            repository_snapshot=snapshot_dir,
            test_command=["sleep", "10"],
        )

    assert res.timed_out is True
    assert res.status == ScannerStatus.TIMEOUT
    assert res.success is False
    assert "timed out" in (res.error_message or "")
    mock_cleanup.assert_called_once()


def test_output_overflow_termination_in_runner(tmp_path: Path) -> None:
    """Verify run_validation terminates container when output exceeds byte limit."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner = DockerSandboxRunner(config=DockerSandboxConfig(max_output_bytes=500))

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.pid = 99999
    mock_proc.poll.return_value = None

    def mock_stream(*args, **kwargs):
        return ([b"A" * 501], [], 501, 0, True, False, "limit exceeded")

    with (
        patch.object(runner, "is_available", return_value=True),
        patch.object(runner, "image_exists", return_value=True),
        patch("subprocess.Popen", return_value=mock_proc),
        patch(
            "openreview.patch.docker_sandbox._stream_bounded_output",
            side_effect=mock_stream,
        ),
        patch.object(runner, "_force_cleanup_container") as mock_cleanup,
    ):
        res = runner.run_validation(
            repository_snapshot=snapshot_dir,
            test_command=["python3", "-c", "print('A' * 1000000)"],
        )

    assert res.output_overflow is True
    assert res.status == ScannerStatus.MALFORMED_OUTPUT
    assert res.success is False
    assert "exceeded output limit" in (res.error_message or "")
    mock_cleanup.assert_called_once()


def test_container_cleanup_always_invoked(tmp_path: Path) -> None:
    """Verify _force_cleanup_container is called in finally block even on unexpected exception."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

    runner = DockerSandboxRunner()

    with (
        patch.object(runner, "is_available", return_value=True),
        patch.object(runner, "image_exists", return_value=True),
        patch("subprocess.Popen", side_effect=RuntimeError("Subprocess fork crash")),
        patch.object(runner, "_force_cleanup_container") as mock_cleanup,
    ):
        res = runner.run_validation(
            repository_snapshot=snapshot_dir,
            test_command=["true"],
        )

    assert res.success is False
    assert res.status == ScannerStatus.ERROR
    assert "Subprocess fork crash" in (res.error_message or "")
    mock_cleanup.assert_called_once()


def test_original_repository_remains_unchanged(tmp_path: Path) -> None:
    """Verify that original repository content is preserved and never modified by validation."""
    orig_repo = tmp_path / "original_repo"
    orig_repo.mkdir()
    file_p = orig_repo / "main.py"
    original_text = "ORIGINAL_CONSTANT = 100\n"
    file_p.write_text(original_text, encoding="utf-8")

    proposal = generate_unified_patch(
        target_file="main.py",
        original_code=original_text,
        replacement_code="ORIGINAL_CONSTANT = 200\n",
    )

    runner = DockerSandboxRunner()

    # Simulate validation of snapshot
    with patch.object(runner, "is_available", return_value=False):
        SandboxPatchTester.test_patch_in_sandbox(
            repo_root=orig_repo,
            proposal=proposal,
            runner=runner,
            require_docker=True,
        )

    # Original repository MUST be untouched
    assert file_p.read_text(encoding="utf-8") == original_text


def test_resource_limits_config_rejections() -> None:
    """Verify DockerSandboxConfig rejects invalid resource limits (<= 0)."""
    with pytest.raises(SandboxSecurityError, match="cpus limit must be > 0"):
        cfg = DockerSandboxConfig(cpus=0.0)
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="pids_limit must be > 0"):
        cfg = DockerSandboxConfig(pids_limit=0)
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="timeout_seconds must be > 0"):
        cfg = DockerSandboxConfig(timeout_seconds=-5)
        cfg.validate_security_invariants()


def test_patched_snapshot_actually_tested(tmp_path: Path) -> None:
    """Verify that build_docker_command binds the patched snapshot directory and targets /snapshot."""
    snapshot_dir = tmp_path / "patched_snapshot_dir"
    snapshot_dir.mkdir()
    (snapshot_dir / "service.py").write_text("PATCHED = True\n", encoding="utf-8")

    runner = DockerSandboxRunner()
    cmd = runner.build_docker_command(
        snapshot_path=snapshot_dir,
        test_command=["python3", "-c", "import service; assert service.PATCHED"],
        container_name="test_patch_applied",
    )

    cmd_str = " ".join(cmd)
    # The container command must copy from /snapshot and execute in /workspace
    assert (
        f"--mount type=bind,source={snapshot_dir.resolve()},target=/snapshot,readonly"
        in cmd_str
    )
    assert "cp -R /snapshot/. /workspace/" in cmd_str


def test_original_repository_imports_rejected(tmp_path: Path) -> None:
    """Verify original repository is never mounted into container, preventing host imports."""
    orig_repo = tmp_path / "host_repo"
    orig_repo.mkdir()
    (orig_repo / "host_secret.py").write_text(
        "HOST_SECRET = 'stolen'\n", encoding="utf-8"
    )

    snapshot_dir = tmp_path / "disposable_snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "safe.py").write_text("SAFE = True\n", encoding="utf-8")

    runner = DockerSandboxRunner()
    cmd = runner.build_docker_command(
        snapshot_path=snapshot_dir,
        test_command=["python3", "-c", "import host_secret"],
        container_name="test_isolation",
    )

    cmd_str = " ".join(cmd)
    # Original repository directory must NOT appear anywhere in docker command
    assert str(orig_repo) not in cmd_str
    assert "host_repo" not in cmd_str


def test_malicious_test_command_quoting_safety(tmp_path: Path) -> None:
    """Verify commands with shell metacharacters are safely quoted with shlex.quote."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    runner = DockerSandboxRunner()
    malicious_cmd = ["python3", "-c", "import sys; sys.exit(0); rm -rf /"]
    cmd = runner.build_docker_command(
        snapshot_path=snapshot_dir,
        test_command=malicious_cmd,
        container_name="test_injection",
    )

    exec_script = cmd[-1]
    # In the shell exec string, the argument containing semicolons must be safely quoted
    assert "'import sys; sys.exit(0); rm -rf /'" in exec_script


def test_snapshot_prevents_symlink_escape(tmp_path: Path) -> None:
    """Verify copy_snapshot_preventing_symlink_escape blocks symlinks targeting outside repository."""
    from openreview.patch.sandbox import copy_snapshot_preventing_symlink_escape

    repo_dir = tmp_path / "repo_with_links"
    repo_dir.mkdir()
    (repo_dir / "safe.txt").write_text("safe content\n", encoding="utf-8")

    # Safe internal relative symlink
    (repo_dir / "safe_link.txt").symlink_to("safe.txt")

    # Dangerous external symlink pointing outside repo
    external_secret = tmp_path / "external_secret.txt"
    external_secret.write_text("HOST_SECRET_LEAK\n", encoding="utf-8")
    (repo_dir / "evil_link.txt").symlink_to(external_secret)

    # Destination snapshot dir
    snapshot_dir = tmp_path / "sandbox_dest"
    snapshot_dir.mkdir()

    copy_snapshot_preventing_symlink_escape(repo_dir, snapshot_dir)

    # Safe file and internal symlink must exist
    assert (snapshot_dir / "safe.txt").exists()
    assert (snapshot_dir / "safe_link.txt").exists()

    # External symlink must NEVER be copied into sandbox snapshot
    assert not (snapshot_dir / "evil_link.txt").exists()


# ============================================================================
# Live Docker Integration Tests (executed ONLY when Docker daemon is available)
# ============================================================================


@pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="Docker daemon is not available on this host"
)
def test_live_docker_network_isolation(tmp_path: Path) -> None:
    """Live Docker test: Verify outbound network calls are strictly blocked under --network none."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "test.py").write_text("print('test')\n", encoding="utf-8")

    runner = DockerSandboxRunner(config=DockerSandboxConfig(image="python:3.11-slim"))
    # Try reaching out to Google DNS
    res = runner.run_validation(
        repository_snapshot=snapshot_dir,
        test_command=[
            "python3",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://8.8.8.8', timeout=2)",
        ],
        timeout_seconds=5,
    )

    assert res.success is False
    assert res.exit_code != 0


@pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="Docker daemon is not available on this host"
)
def test_live_docker_non_root_execution(tmp_path: Path) -> None:
    """Live Docker test: Verify container executes under non-root UID 1000."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    runner = DockerSandboxRunner(
        config=DockerSandboxConfig(image="python:3.11-slim", user="1000:1000")
    )
    res = runner.run_validation(
        repository_snapshot=snapshot_dir,
        test_command=["id", "-u"],
    )

    assert res.success is True
    assert res.stdout.strip() == "1000"


@pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="Docker daemon is not available on this host"
)
def test_live_docker_read_only_rootfs(tmp_path: Path) -> None:
    """Live Docker test: Verify writes to container rootfs fail under --read-only."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()

    runner = DockerSandboxRunner(config=DockerSandboxConfig(image="python:3.11-slim"))
    res = runner.run_validation(
        repository_snapshot=snapshot_dir,
        test_command=["touch", "/etc/hacked.txt"],
    )

    assert res.success is False
    assert res.exit_code != 0
    assert "Read-only file system" in res.stderr


@pytest.mark.skipif(
    not DOCKER_AVAILABLE, reason="Docker daemon is not available on this host"
)
def test_live_docker_tmpfs_workspace_writable(tmp_path: Path) -> None:
    """Live Docker test: Verify writes inside /workspace succeed while /snapshot is read-only."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "data.txt").write_text("original\n", encoding="utf-8")

    runner = DockerSandboxRunner(config=DockerSandboxConfig(image="python:3.11-slim"))

    # Writing in /workspace succeeds; attempting write to /snapshot fails
    test_script = (
        "echo 'new_work' > /workspace/output.txt && touch /snapshot/tampered.txt"
    )
    res = runner.run_validation(
        repository_snapshot=snapshot_dir,
        test_command=["sh", "-c", test_script],
    )

    # Must fail because /snapshot is read-only
    assert res.success is False
    assert "Read-only file system" in res.stderr
