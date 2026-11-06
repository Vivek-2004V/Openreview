"""Isolated execution environments and container sandbox runners for untrusted PR reviews.

Enforces real container isolation (Docker/gVisor) per Gate D & Epic E6 security standards:
- Non-root execution
- No Docker socket access
- No GitHub secrets inside scanner environment
- Network disabled during analysis
- CPU, memory, and execution time limits
- Read-only source mount
- Restricted writable temporary directory
- Separate credentials and reporting process
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from openreview.config import OpenReviewConfig
from openreview.repo.diff_models import DiffRequest, DiffTarget
from openreview.reports.json_report import validate_json_report_payload
from openreview.schemas.models import ReviewReport
from openreview.security.subprocess_guard import validate_subprocess_args

logger = logging.getLogger(__name__)

FORBIDDEN_SECRET_PATTERN = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|AUTH|BEARER|GITHUB_PAT)",
    re.IGNORECASE,
)


def ensure_directory_accessible_to_container(path: Path) -> None:
    """Ensure directory and all its files/subdirs are readable/searchable by non-root container users."""
    try:
        resolved = Path(path).resolve()
        try:
            resolved.chmod(resolved.stat().st_mode | 0o755)
        except OSError:
            pass
        for root, dirs, files in os.walk(resolved):
            cur = Path(root)
            try:
                cur.chmod(cur.stat().st_mode | 0o755)
            except OSError:
                pass
            for f in files:
                fpath = cur / f
                try:
                    if not fpath.is_symlink():
                        fpath.chmod(fpath.stat().st_mode | 0o644)
                except OSError:
                    pass
    except OSError:
        pass


class SandboxSecurityError(PermissionError):
    """Raised when a sandbox security invariant or isolation control is violated."""


class SandboxExecutionError(RuntimeError):
    """Raised when container sandbox execution fails, times out, or produces invalid output."""


@dataclass(frozen=True)
class ContainerSandboxConfig:
    """Security and resource limits configuration for containerized review execution."""

    image: str = "openreview-scanner:latest"
    runtime: str = "runc"
    user: str = "1000:1000"
    network_disabled: bool = True
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    no_new_privileges: bool = True
    read_only_rootfs: bool = True
    read_only_source: bool = True
    tmpfs_mounts: dict[str, str] = field(
        default_factory=lambda: {"/tmp": "rw,noexec,nosuid,size=64m"}
    )
    cpus: float = 1.0
    memory_limit: str = "512m"
    pids_limit: int = 100
    timeout_seconds: int = 120
    env_vars: dict[str, str] = field(default_factory=dict)

    def validate_security_invariants(self) -> None:
        """Verify that all minimum required security controls are strictly enforced."""
        # 1. Non-root execution
        clean_user = self.user.strip().lower()
        if clean_user in ("0", "0:0", "root", "") or clean_user.startswith("0:"):
            raise SandboxSecurityError(
                f"Root execution prohibited in sandbox (user='{self.user}'). Must run as non-root."
            )

        # 2. Network disabled
        if not self.network_disabled:
            raise SandboxSecurityError(
                "Network access must be disabled during scanner analysis (network_disabled=True)."
            )

        # 3. Capability dropping
        if "ALL" not in self.cap_drop:
            raise SandboxSecurityError(
                "All Linux capabilities must be dropped (cap_drop must include 'ALL')."
            )

        # 4. Privilege escalation prevention
        if not self.no_new_privileges:
            raise SandboxSecurityError(
                "no_new_privileges must be True to prevent privilege escalation inside container."
            )

        # 5. Read-only source checkout
        if not self.read_only_source:
            raise SandboxSecurityError(
                "Untrusted repository source must be mounted read-only (read_only_source=True)."
            )

        # 6. Read-only root filesystem
        if not self.read_only_rootfs:
            raise SandboxSecurityError(
                "Container root filesystem must be read-only (read_only_rootfs=True)."
            )

        # 7. Resource limits
        if self.cpus <= 0:
            raise SandboxSecurityError(f"cpus limit must be > 0, got {self.cpus}")
        if self.pids_limit <= 0:
            raise SandboxSecurityError(f"pids_limit must be > 0, got {self.pids_limit}")
        if self.timeout_seconds <= 0:
            raise SandboxSecurityError(
                f"timeout_seconds must be > 0, got {self.timeout_seconds}"
            )

        # 8. Secret leakage prevention into container environment
        for key in self.env_vars:
            if FORBIDDEN_SECRET_PATTERN.search(key):
                raise SandboxSecurityError(
                    f"Forbidden secret pattern detected in container environment variable '{key}'."
                )

        # 9. Prohibit Docker socket in tmpfs or custom mounts
        for mount in self.tmpfs_mounts:
            if "docker.sock" in mount.lower():
                raise SandboxSecurityError(
                    "Docker socket mount is strictly prohibited."
                )


@runtime_checkable
class SandboxRunner(Protocol):
    """Protocol for executing review engine operations inside an isolation boundary."""

    def is_available(self) -> bool:
        """Return True if the execution runtime is installed and operational."""
        ...

    def run_review_in_sandbox(
        self,
        sandbox_path: Path,
        base_sha: str,
        head_sha: str,
        config: OpenReviewConfig,
        timeout_seconds: int = 120,
    ) -> ReviewReport:
        """Execute review analysis in isolation and return validated ReviewReport."""
        ...


class DockerSandboxRunner(SandboxRunner):
    """Docker container sandbox runner with restricted Linux capabilities and network airgap."""

    def __init__(self, config: ContainerSandboxConfig | None = None) -> None:
        self.sandbox_config = config or ContainerSandboxConfig()
        self.sandbox_config.validate_security_invariants()

    def is_available(self) -> bool:
        """Check whether 'docker' binary is installed and daemon is reachable."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        try:
            res = subprocess.run(
                [docker_bin, "info"],
                check=False,
                capture_output=True,
                timeout=3.0,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def image_exists(self) -> bool:
        """Check whether the configured container image is present in local docker cache."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        try:
            res = subprocess.run(
                [docker_bin, "image", "inspect", self.sandbox_config.image],
                check=False,
                capture_output=True,
                timeout=3.0,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

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

    def build_docker_command(
        self,
        sandbox_path: Path,
        output_dir: Path,
        base_sha: str,
        head_sha: str,
        config: OpenReviewConfig,
        container_name: str | None = None,
    ) -> list[str]:
        """Construct deterministic docker run command with strictly hardened security flags."""
        self.sandbox_config.validate_security_invariants()
        resolved_sandbox = sandbox_path.resolve()
        resolved_output = output_dir.resolve()

        cmd = [
            "docker",
            "run",
            "--rm",
        ]
        if container_name:
            cmd.extend(["--name", container_name])
        cmd.extend([
            "--network",
            "none",
            "--user",
            self.sandbox_config.user,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--cpus",
            str(self.sandbox_config.cpus),
            "--memory",
            self.sandbox_config.memory_limit,
            "--pids-limit",
            str(self.sandbox_config.pids_limit),
        ])

        if self.sandbox_config.read_only_rootfs:
            cmd.append("--read-only")

        # Configured tmpfs mounts (restricted writable temporary dirs)
        for mount_path, opts in self.sandbox_config.tmpfs_mounts.items():
            cmd.extend(["--tmpfs", f"{mount_path}:{opts}"])

        # Alternative runtime if specified (e.g. gVisor 'runsc')
        if self.sandbox_config.runtime and self.sandbox_config.runtime not in (
            "runc",
            "docker",
        ):
            cmd.extend(["--runtime", self.sandbox_config.runtime])

        # Sanitized environment variables (guaranteed no secrets)
        for k, v in self.sandbox_config.env_vars.items():
            cmd.extend(["--env", f"{k}={v}"])

        # Read-only source mount
        cmd.extend(
            [
                "--mount",
                f"type=bind,source={resolved_sandbox},target=/workspace,readonly",
            ]
        )

        # Isolated output mount (only the dedicated output directory is writable)
        cmd.extend(
            [
                "--mount",
                f"type=bind,source={resolved_output},target=/output",
            ]
        )

        cmd.extend(["-w", "/workspace"])

        # Container image & execution command
        cmd.append(self.sandbox_config.image)
        cmd.extend(
            [
                "openreview",
                "review",
                "--repo-root",
                "/workspace",
                "--json",
                "--output",
                "/output/report.json",
                "--base",
                base_sha,
                "--head",
                head_sha,
            ]
        )

        # Security check: verify no docker socket anywhere in command vector
        for arg in cmd:
            if "docker.sock" in arg.lower():
                raise SandboxSecurityError(
                    "Docker socket access is strictly forbidden in sandbox command."
                )

        return validate_subprocess_args(cmd)

    def run_review_in_sandbox(
        self,
        sandbox_path: Path,
        base_sha: str,
        head_sha: str,
        config: OpenReviewConfig,
        timeout_seconds: int = 120,
    ) -> ReviewReport:
        """Run isolated review execution inside container and parse verified ReviewReport."""
        if not self.is_available():
            raise SandboxExecutionError(
                "Docker executable is not installed or available in PATH. "
                "A real container sandbox runtime is required for secure review execution."
            )
        if not self.image_exists():
            raise SandboxExecutionError(
                f"Container image '{self.sandbox_config.image}' is not found in local Docker cache. "
                "Build or pull the image before running container reviews."
            )

        container_name = f"openreview_rev_{uuid.uuid4().hex[:12]}"

        with tempfile.TemporaryDirectory(prefix="openreview_sandbox_out_") as out_temp:
            out_dir = Path(out_temp).resolve()
            ensure_directory_accessible_to_container(sandbox_path)
            ensure_directory_accessible_to_container(out_dir)

            cmd = self.build_docker_command(
                sandbox_path=sandbox_path,
                output_dir=out_dir,
                base_sha=base_sha,
                head_sha=head_sha,
                config=config,
                container_name=container_name,
            )

            effective_timeout = timeout_seconds or self.sandbox_config.timeout_seconds

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._force_cleanup_container(container_name)
                raise SandboxExecutionError(
                    f"Container sandbox execution timed out after {effective_timeout} seconds."
                ) from exc
            except Exception as exc:
                self._force_cleanup_container(container_name)
                raise SandboxExecutionError(
                    f"Subprocess failed while launching container sandbox: {exc}"
                ) from exc
            finally:
                self._force_cleanup_container(container_name)

            if proc.returncode != 0:
                err_detail = (
                    proc.stderr.strip()
                    or proc.stdout.strip()
                    or f"exit code {proc.returncode}"
                )
                raise SandboxExecutionError(
                    f"Container sandbox review failed (exit {proc.returncode}): {err_detail}"
                )

            report_file = out_dir / "report.json"
            if not report_file.is_file():
                raise SandboxExecutionError(
                    f"Container sandbox completed but expected report file was not found at {report_file}."
                )

            try:
                report_text = report_file.read_text(encoding="utf-8")
                report_dict = json.loads(report_text)
                return validate_json_report_payload(report_dict)
            except Exception as exc:
                raise SandboxExecutionError(
                    f"Failed to parse validated ReviewReport from container output: {exc}"
                ) from exc


class LocalSandboxRunner(SandboxRunner):
    """Local runner executing review engine directly without container isolation.

    Intended for local development, trusted internal runs, or test fixtures.
    Logs an explicit audit warning because process and network boundaries are not enforced.
    """

    def is_available(self) -> bool:
        return True

    def run_review_in_sandbox(
        self,
        sandbox_path: Path,
        base_sha: str,
        head_sha: str,
        config: OpenReviewConfig,
        timeout_seconds: int = 120,
    ) -> ReviewReport:
        logger.warning(
            "Executing review in local process workspace without container isolation. "
            "Note: Network airgap, CPU/memory limits, and plugin boundaries are not enforced."
        )
        from openreview.review.engine import ReviewEngine

        engine = ReviewEngine(repo_root=sandbox_path, config=config)
        diff_req = DiffRequest(
            repo_root=sandbox_path,
            target=DiffTarget.COMMITS_RANGE,
            base_ref=base_sha,
            head_ref=head_sha,
            max_files=config.review.max_files,
            max_file_bytes=config.review.max_file_bytes,
        )
        return engine.execute_review(diff_request=diff_req)
