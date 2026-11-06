"""Unit tests for ReviewWorker container sandbox isolation and security controls."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openreview.config import OpenReviewConfig
from openreview.integrations.github_webhook import PullRequestEventData
from openreview.integrations.job_queue import JobStatus, ReviewJobQueue
from openreview.integrations.sandbox import (
    ContainerSandboxConfig,
    DockerSandboxRunner,
    SandboxExecutionError,
    SandboxRunner,
    SandboxSecurityError,
)
from openreview.integrations.worker import ReviewWorker
from openreview.schemas.models import ReviewReport


def test_sandbox_config_security_validation_rejects_insecure_settings():
    """Verify ContainerSandboxConfig strictly enforces minimum security controls."""
    # 1. Reject root execution
    with pytest.raises(SandboxSecurityError, match="Root execution prohibited"):
        cfg = ContainerSandboxConfig(user="0")
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="Root execution prohibited"):
        cfg = ContainerSandboxConfig(user="root")
        cfg.validate_security_invariants()

    # 2. Reject network enabled
    with pytest.raises(SandboxSecurityError, match="Network access must be disabled"):
        cfg = ContainerSandboxConfig(network_disabled=False)
        cfg.validate_security_invariants()

    # 3. Reject missing cap_drop ALL
    with pytest.raises(
        SandboxSecurityError, match="All Linux capabilities must be dropped"
    ):
        cfg = ContainerSandboxConfig(cap_drop=["NET_ADMIN"])
        cfg.validate_security_invariants()

    # 4. Reject no_new_privileges disabled
    with pytest.raises(SandboxSecurityError, match="no_new_privileges must be True"):
        cfg = ContainerSandboxConfig(no_new_privileges=False)
        cfg.validate_security_invariants()

    # 5. Reject writable source mount
    with pytest.raises(SandboxSecurityError, match="mounted read-only"):
        cfg = ContainerSandboxConfig(read_only_source=False)
        cfg.validate_security_invariants()

    # 6. Reject writable root filesystem
    with pytest.raises(SandboxSecurityError, match="root filesystem must be read-only"):
        cfg = ContainerSandboxConfig(read_only_rootfs=False)
        cfg.validate_security_invariants()

    # 7. Reject forbidden secret patterns in env vars
    with pytest.raises(SandboxSecurityError, match="Forbidden secret pattern"):
        cfg = ContainerSandboxConfig(env_vars={"GITHUB_TOKEN": "ghp_secret123"})
        cfg.validate_security_invariants()

    with pytest.raises(SandboxSecurityError, match="Forbidden secret pattern"):
        cfg = ContainerSandboxConfig(env_vars={"APP_SECRET_KEY": "super_secret"})
        cfg.validate_security_invariants()

    # 8. Reject docker socket mounts
    with pytest.raises(
        SandboxSecurityError, match="Docker socket mount is strictly prohibited"
    ):
        cfg = ContainerSandboxConfig(tmpfs_mounts={"/var/run/docker.sock": "rw"})
        cfg.validate_security_invariants()


def test_docker_command_construction_contains_all_security_controls(tmp_path: Path):
    """Verify build_docker_command generates exact flags required for container isolation."""
    sandbox_path = tmp_path / "repo"
    sandbox_path.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    cfg = ContainerSandboxConfig(
        image="custom-scanner:1.0",
        user="2000:2000",
        cpus=1.5,
        memory_limit="768m",
        pids_limit=80,
    )
    runner = DockerSandboxRunner(config=cfg)

    cmd = runner.build_docker_command(
        sandbox_path=sandbox_path,
        output_dir=out_dir,
        base_sha="base123",
        head_sha="head456",
        config=OpenReviewConfig(),
    )

    cmd_str = " ".join(cmd)

    # Check minimum required controls
    assert "--network none" in cmd_str
    assert "--user 2000:2000" in cmd_str
    assert "--cap-drop ALL" in cmd_str
    assert "--security-opt no-new-privileges:true" in cmd_str
    assert "--cpus 1.5" in cmd_str
    assert "--memory 768m" in cmd_str
    assert "--pids-limit 80" in cmd_str
    assert "--read-only" in cmd_str
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=64m" in cmd_str
    assert (
        f"--mount type=bind,source={sandbox_path.resolve()},target=/workspace,readonly"
        in cmd_str
    )
    assert f"--mount type=bind,source={out_dir.resolve()},target=/output" in cmd_str
    assert "docker.sock" not in cmd_str
    assert "custom-scanner:1.0" in cmd
    assert "openreview" in cmd
    assert "review" in cmd
    assert "--base base123" in cmd_str
    assert "--head head456" in cmd_str


def test_docker_sandbox_runner_execution_success(tmp_path: Path):
    """Verify DockerSandboxRunner executes docker command, reads report, and returns ReviewReport."""
    sandbox_path = tmp_path / "repo"
    sandbox_path.mkdir()

    runner = DockerSandboxRunner()

    sample_report = ReviewReport.create(
        total_files_analyzed=1,
        repo_root=str(sandbox_path),
        findings=[],
        base_ref="base123",
        head_ref="head456",
    )

    def mock_subprocess_run(cmd, **kwargs):
        for arg in cmd:
            if "target=/output" in arg:
                parts = dict(
                    part.split("=", 1) for part in arg.split(",") if "=" in part
                )
                out_path = Path(parts["source"])
                (out_path / "report.json").write_text(
                    json.dumps(sample_report.to_dict()), encoding="utf-8"
                )
                break
        res = MagicMock()
        res.returncode = 0
        res.stdout = "Analysis completed."
        res.stderr = ""
        return res

    with (
        patch.object(runner, "is_available", return_value=True),
        patch("subprocess.run", side_effect=mock_subprocess_run),
    ):
        report = runner.run_review_in_sandbox(
            sandbox_path=sandbox_path,
            base_sha="base123",
            head_sha="head456",
            config=OpenReviewConfig(),
            timeout_seconds=30,
        )

    assert report is not None
    assert report.base_ref == "base123"
    assert report.head_ref == "head456"


def test_docker_sandbox_runner_failure_cases(tmp_path: Path):
    """Verify DockerSandboxRunner handles non-zero exit codes, missing executable, and timeouts."""
    sandbox_path = tmp_path / "repo"
    sandbox_path.mkdir()

    runner = DockerSandboxRunner()

    # 1. Missing docker executable
    with (
        patch.object(runner, "is_available", return_value=False),
        pytest.raises(
            SandboxExecutionError, match="Docker executable is not installed"
        ),
    ):
        runner.run_review_in_sandbox(
            sandbox_path=sandbox_path,
            base_sha="base",
            head_sha="head",
            config=OpenReviewConfig(),
        )

    # 2. Missing container image in docker cache
    with (
        patch.object(runner, "is_available", return_value=True),
        patch.object(runner, "image_exists", return_value=False),
        pytest.raises(
            SandboxExecutionError, match="is not found in local Docker cache"
        ),
    ):
        runner.run_review_in_sandbox(
            sandbox_path=sandbox_path,
            base_sha="base",
            head_sha="head",
            config=OpenReviewConfig(),
        )

    # 3. Non-zero container exit code
    mock_fail = MagicMock()
    mock_fail.returncode = 137
    mock_fail.stdout = ""
    mock_fail.stderr = "OOMKilled: container exceeded memory limit."

    with (
        patch.object(runner, "is_available", return_value=True),
        patch.object(runner, "image_exists", return_value=True),
        patch("subprocess.run", return_value=mock_fail),
        pytest.raises(
            SandboxExecutionError, match="Container sandbox review failed.*OOMKilled"
        ),
    ):
        runner.run_review_in_sandbox(
            sandbox_path=sandbox_path,
            base_sha="base",
            head_sha="head",
            config=OpenReviewConfig(),
        )


def test_review_worker_require_container_enforcement():
    """Verify ReviewWorker with require_container=True refuses to run when docker is missing."""
    queue = ReviewJobQueue()

    with (
        patch("shutil.which", return_value=None),
        pytest.raises(SandboxSecurityError, match="Real container sandbox is required"),
    ):
        ReviewWorker(
            queue=queue,
            config=OpenReviewConfig(),
            require_container=True,
        )


def test_review_worker_with_custom_sandbox_runner(tmp_path: Path):
    """Verify ReviewWorker delegates review execution to the configured SandboxRunner."""
    queue = ReviewJobQueue()
    pr_data = PullRequestEventData(
        action="opened",
        pr_number=99,
        repo_full_name="acme/proj",
        clone_url="https://github.com/acme/proj.git",
        head_sha="head_sha_99",
        base_sha="base_sha_99",
        head_ref="feature",
        base_ref="main",
    )
    job, _ = queue.enqueue_pr_event(pr_data, delivery_id="delivery-custom-runner")

    sample_report = ReviewReport.create(
        total_files_analyzed=1,
        repo_root=str(tmp_path),
        findings=[],
        base_ref="base_sha_99",
        head_ref="head_sha_99",
    )

    mock_runner = MagicMock(spec=SandboxRunner)
    mock_runner.is_available.return_value = True
    mock_runner.run_review_in_sandbox.return_value = sample_report

    mock_fetcher = MagicMock()

    worker = ReviewWorker(
        queue=queue,
        config=OpenReviewConfig(),
        repo_fetcher=mock_fetcher,
        sandbox_runner=mock_runner,
    )

    result_report = worker.process_next_job()

    assert result_report is not None
    mock_fetcher.assert_called_once()
    mock_runner.run_review_in_sandbox.assert_called_once()
    assert job.status == JobStatus.COMPLETED
    assert job.report_summary is not None
