"""Safe subprocess execution helper with timeouts, output caps, and error isolation."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from openreview.adapters.base import ScannerStatus
from openreview.security.subprocess_guard import validate_subprocess_args


@dataclass(frozen=True)
class ProcessExecutionResult:
    """Raw result of subprocess execution with timing and status classification."""

    status: ScannerStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    error_message: str | None = None


def run_scanner_process(
    cmd: Sequence[str],
    cwd: Path | str,
    timeout_seconds: int = 30,
    max_output_bytes: int = 1_000_000,
) -> ProcessExecutionResult:
    """Execute scanner command with rigid timeout and output buffer limits."""
    start_time = time.monotonic()

    try:
        validated_cmd = validate_subprocess_args(cmd)
    except Exception as exc:
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=f"Invalid subprocess arguments: {exc}",
        )

    try:
        process = subprocess.run(
            validated_cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.monotonic() - start_time
    except FileNotFoundError:
        duration = time.monotonic() - start_time
        return ProcessExecutionResult(
            status=ScannerStatus.NOT_FOUND,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=f"Executable '{cmd[0]}' not found in system PATH.",
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start_time
        return ProcessExecutionResult(
            status=ScannerStatus.TIMEOUT,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=f"Process '{cmd[0]}' timed out after {timeout_seconds} seconds.",
        )
    except Exception as exc:
        duration = time.monotonic() - start_time
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=f"Subprocess execution failed: {exc}",
        )

    # Check output size limits
    stdout_bytes = len(process.stdout.encode("utf-8"))
    if stdout_bytes > max_output_bytes:
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=process.returncode,
            stdout=process.stdout[:1000],
            stderr=process.stderr,
            duration_seconds=duration,
            error_message=f"Scanner output exceeded limit ({stdout_bytes} > {max_output_bytes} bytes)",
        )

    return ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        duration_seconds=duration,
    )
