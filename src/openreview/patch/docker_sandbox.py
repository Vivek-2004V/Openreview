"""Restricted Docker execution sandbox for untrusted patch validation per P0 specification."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from openreview.adapters.base import ScannerStatus
from openreview.adapters.process_runner import (
    DEFAULT_MAX_OUTPUT_BYTES,
    _kill_process_tree,
    _stream_bounded_output,
)
from openreview.security.subprocess_guard import validate_subprocess_args

logger = logging.getLogger(__name__)

FORBIDDEN_SECRET_PATTERN = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|AUTH|BEARER|GITHUB_PAT|AWS_ACCESS|PRIVATE_KEY)",
    re.IGNORECASE,
)

DEFAULT_SAFE_ENV: dict[str, str] = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "CI": "true",
    "OPENREVIEW_SANDBOX": "1",
}


class SandboxSecurityError(PermissionError):
    """Raised when a container sandbox security invariant is violated."""


class DockerUnavailableError(RuntimeError):
    """Raised when Docker is required but not installed or daemon is unreachable."""


class SnapshotSizeExceededError(ValueError):
    """Raised when repository snapshot exceeds maximum permitted size before container creation."""


def get_directory_size_bytes(directory: Path) -> int:
    """Calculate the total size of regular files in a directory in bytes."""
    total = 0
    try:
        for p in directory.rglob("*"):
            if p.is_file() and not p.is_symlink():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


@dataclass(frozen=True)
class DockerSandboxConfig:
    """Security and resource limits configuration for patch validation in Docker."""

    image: str = "openreview-patch-validator:latest"
    user: str = "1000:1000"
    network_disabled: bool = True
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    no_new_privileges: bool = True
    read_only_rootfs: bool = True
    tmpfs_workspace_size: str = "256m"
    tmpfs_tmp_size: str = "64m"
    cpus: float = 1.0
    memory_limit: str = "512m"
    pids_limit: int = 100
    timeout_seconds: int = 120
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_snapshot_bytes: int = 50 * 1024 * 1024  # 50 MB limit on host snapshot
    env_vars: dict[str, str] = field(default_factory=dict)

    def validate_security_invariants(self) -> None:
        """Enforce non-negotiable security controls on the container configuration."""
        # 1. Non-root execution
        clean_user = self.user.strip().lower()
        if clean_user in ("0", "0:0", "root", "") or clean_user.startswith("0:"):
            raise SandboxSecurityError(
                f"Root execution prohibited in sandbox (user='{self.user}'). Must run as non-root."
            )

        # 2. Network isolation
        if not self.network_disabled:
            raise SandboxSecurityError(
                "Network access must be disabled during validation (network_disabled=True)."
            )

        # 3. Capability dropping
        if "ALL" not in self.cap_drop:
            raise SandboxSecurityError(
                "All Linux capabilities must be dropped (cap_drop must include 'ALL')."
            )

        # 4. Privilege escalation prevention
        if not self.no_new_privileges:
            raise SandboxSecurityError(
                "no_new_privileges must be True to prevent privilege escalation."
            )

        # 5. Read-only root filesystem
        if not self.read_only_rootfs:
            raise SandboxSecurityError(
                "Container root filesystem must be read-only (read_only_rootfs=True)."
            )

        # 6. Resource limits validation
        if self.cpus <= 0:
            raise SandboxSecurityError(f"cpus limit must be > 0, got {self.cpus}")
        if self.pids_limit <= 0:
            raise SandboxSecurityError(f"pids_limit must be > 0, got {self.pids_limit}")
        if self.timeout_seconds <= 0:
            raise SandboxSecurityError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )

        # 7. Secret leakage prevention into container environment
        for key in self.env_vars:
            if FORBIDDEN_SECRET_PATTERN.search(key):
                raise SandboxSecurityError(
                    f"Forbidden secret pattern detected in container environment variable '{key}'."
                )


@dataclass(frozen=True)
class DockerValidationResult:
    """Outcome of running validation in the Docker sandbox."""

    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    status: ScannerStatus
    container_name: str | None = None
    error_message: str | None = None
    timed_out: bool = False
    output_overflow: bool = False


class DockerSandboxRunner:
    """Restricted Docker execution runner for validating untrusted repository patches."""

    def __init__(self, config: DockerSandboxConfig | None = None) -> None:
        self.config = config or DockerSandboxConfig()
        self.config.validate_security_invariants()

    def is_available(self) -> bool:
        """Check whether 'docker' binary is installed and the Docker daemon is reachable."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        try:
            res = subprocess.run(
                [docker_bin, "info"],
                check=False,
                capture_output=True,
                timeout=5.0,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def image_exists(self, image_name: str | None = None) -> bool:
        """Check whether the required validator container image exists in local Docker daemon."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        target_image = image_name or self.config.image
        try:
            res = subprocess.run(
                [docker_bin, "image", "inspect", target_image],
                check=False,
                capture_output=True,
                timeout=5.0,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def build_docker_command(
        self,
        snapshot_path: Path,
        test_command: Sequence[str] | str | None,
        container_name: str,
    ) -> list[str]:
        """Construct deterministic docker run command with full security hardening flags."""
        self.config.validate_security_invariants()
        resolved_snapshot = snapshot_path.resolve()

        # Invariant: Never mount host home directory
        home_dir = Path.home().resolve()
        if (
            resolved_snapshot == home_dir
            or str(resolved_snapshot) == str(home_dir)
            or str(home_dir).startswith(str(resolved_snapshot))
        ):
            raise SandboxSecurityError(
                "Mounting host home directory or its parents into sandbox is strictly prohibited."
            )

        # Invariant: Snapshot size quota enforcement on host before container creation
        snapshot_size = get_directory_size_bytes(resolved_snapshot)
        if snapshot_size > self.config.max_snapshot_bytes:
            raise SnapshotSizeExceededError(
                f"Repository snapshot size ({snapshot_size} bytes) exceeds host quota "
                f"({self.config.max_snapshot_bytes} bytes). Refusing to create container."
            )

        cmd = [
            "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "--network",
            "none",
            "--user",
            self.config.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--cpus",
            str(self.config.cpus),
            "--memory",
            self.config.memory_limit,
            "--memory-swap",
            self.config.memory_limit,
            "--pids-limit",
            str(self.config.pids_limit),
        ]

        if self.config.read_only_rootfs:
            cmd.append("--read-only")

        # Size-limited tmpfs mounts (RAM workspace bounded against disk exhaustion)
        cmd.extend(
            [
                "--tmpfs",
                f"/workspace:rw,exec,nosuid,size={self.config.tmpfs_workspace_size},mode=1777",
            ]
        )
        cmd.extend(
            [
                "--tmpfs",
                f"/tmp:rw,noexec,nosuid,size={self.config.tmpfs_tmp_size},mode=1777",
            ]
        )

        # Read-only snapshot mount (original code cannot be modified)
        cmd.extend(
            [
                "--mount",
                f"type=bind,source={resolved_snapshot},target=/snapshot,readonly",
            ]
        )

        # Sanitized environment variables (strictly no host credentials)
        env_to_pass = dict(DEFAULT_SAFE_ENV)
        env_to_pass.update(self.config.env_vars)
        for k, v in env_to_pass.items():
            cmd.extend(["--env", f"{k}={v}"])

        cmd.extend(["-w", "/workspace"])
        cmd.append(self.config.image)

        # Container initialization: copy read-only snapshot into size-limited tmpfs workspace
        if test_command:
            if isinstance(test_command, str):
                parsed_cmd = shlex.split(test_command)
            else:
                parsed_cmd = list(test_command)
            quoted_cmd = " ".join(shlex.quote(arg) for arg in parsed_cmd)
            exec_script = (
                f"cp -R /snapshot/. /workspace/ && cd /workspace && exec {quoted_cmd}"
            )
        else:
            exec_script = "cp -R /snapshot/. /workspace/ && cd /workspace && echo 'Snapshot staged successfully.'"

        cmd.extend(["sh", "-c", exec_script])
        return cmd

    def run_validation(
        self,
        repository_snapshot: Path,
        test_command: Sequence[str] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
    ) -> DockerValidationResult:
        """Execute validation of repository_snapshot inside restricted Docker container.

        Fails closed with ScannerStatus.ERROR if Docker is unavailable. Never falls back to host execution.
        """
        start_time = time.perf_counter()
        effective_timeout = timeout_seconds or self.config.timeout_seconds
        effective_max_output = max_output_bytes or self.config.max_output_bytes

        # Fail-closed check 1: Docker must be available
        if not self.is_available():
            duration = time.perf_counter() - start_time
            return DockerValidationResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                status=ScannerStatus.ERROR,
                error_message=(
                    "Docker unavailable: docker binary is not installed or daemon is unreachable. "
                    "Refusing to execute untrusted repository scripts on the host."
                ),
            )

        # Fail-closed check 2: Target validation image must exist
        if not self.image_exists(self.config.image):
            duration = time.perf_counter() - start_time
            return DockerValidationResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                status=ScannerStatus.ERROR,
                error_message=(
                    f"Validator Docker image '{self.config.image}' is missing. "
                    f"Build it with 'docker build -t {self.config.image} -f Dockerfile.validator .'"
                ),
            )

        container_name = f"openreview_val_{uuid.uuid4().hex[:12]}"

        try:
            cmd = self.build_docker_command(
                snapshot_path=repository_snapshot,
                test_command=test_command,
                container_name=container_name,
            )
            validate_subprocess_args(cmd)
        except (
            SandboxSecurityError,
            SnapshotSizeExceededError,
            ValueError,
        ) as exc:
            duration = time.perf_counter() - start_time
            return DockerValidationResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                status=ScannerStatus.ERROR,
                container_name=container_name,
                error_message=f"Validation pre-check failed: {exc}",
            )

        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,  # noqa: PLW1509
            )

            (
                stdout_chunks,
                stderr_chunks,
                _stdout_len,
                _stderr_len,
                limit_exceeded,
                timed_out,
                _limit_error_msg,
            ) = _stream_bounded_output(
                proc=proc,
                max_output_bytes=effective_max_output,
                timeout_seconds=float(effective_timeout),
                start_time=start_time,
            )
            duration = time.perf_counter() - start_time
            stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")

            if timed_out or limit_exceeded:
                _kill_process_tree(proc)

            exit_code = proc.poll()
            if exit_code is None and not (timed_out or limit_exceeded):
                try:
                    proc.wait(timeout=1.0)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    _kill_process_tree(proc)
                    timed_out = True
                    exit_code = None

            if timed_out:
                status = ScannerStatus.TIMEOUT
                error_msg = f"Docker validation timed out after {effective_timeout}s"
            elif limit_exceeded:
                status = ScannerStatus.MALFORMED_OUTPUT
                error_msg = (
                    f"Docker validation exceeded output limit of {effective_max_output} bytes. "
                    "Container terminated to prevent memory exhaustion."
                )
            elif exit_code == 0:
                status = ScannerStatus.SUCCESS
                error_msg = None
            else:
                status = ScannerStatus.ERROR
                error_msg = f"Validation command failed with exit code {exit_code}"

            success = status == ScannerStatus.SUCCESS and exit_code == 0

            return DockerValidationResult(
                success=success,
                exit_code=exit_code,
                stdout=stdout_str,
                stderr=stderr_str,
                duration_seconds=duration,
                status=status,
                container_name=container_name,
                error_message=error_msg,
                timed_out=timed_out,
                output_overflow=limit_exceeded,
            )
        except Exception as exc:  # noqa: BLE001
            duration = time.perf_counter() - start_time
            if proc:
                _kill_process_tree(proc)
            return DockerValidationResult(
                success=False,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                status=ScannerStatus.ERROR,
                container_name=container_name,
                error_message=f"Docker execution failed: {exc}",
            )
        finally:
            # Forceful container cleanup: guarantees no container leakage
            self._force_cleanup_container(container_name)

    @staticmethod
    def _force_cleanup_container(container_name: str, timeout: float = 3.0) -> None:
        """Forcefully remove Docker container if it exists."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return
        try:
            subprocess.run(
                [docker_bin, "rm", "-f", container_name],
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            pass
