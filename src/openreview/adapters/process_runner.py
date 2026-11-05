"""Safe subprocess execution helper with timeouts, incremental output limits, and OS-level memory caps."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

try:
    import resource

    HAS_RESOURCE = True
except ImportError:
    resource = None  # type: ignore[assignment]
    HAS_RESOURCE = False

from openreview.adapters.base import ScannerStatus
from openreview.security.subprocess_guard import (
    SubprocessArgumentError,
    validate_subprocess_args,
)

DEFAULT_MAX_OUTPUT_BYTES = 1_000_000  # 1 MB
DEFAULT_MAX_MEMORY_BYTES = 1024 * 1024 * 1024  # 1 GiB
DEFAULT_TIMEOUT_SECONDS = 30
CHUNK_SIZE = 8192


@dataclass(frozen=True)
class ProcessExecutionResult:
    """Raw result of subprocess execution with timing and status classification."""

    status: ScannerStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    error_message: str | None = None


def _apply_os_memory_limit(max_memory_bytes: int | None) -> None:
    """Apply OS-level memory limit in the child process via resource.setrlimit if supported."""
    if not max_memory_bytes or not HAS_RESOURCE or resource is None:
        return

    # Attempt RLIMIT_AS (virtual address space) first, then RLIMIT_DATA
    for limit_name in ("RLIMIT_AS", "RLIMIT_DATA"):
        if hasattr(resource, limit_name):
            try:
                res_const = getattr(resource, limit_name)
                resource.setrlimit(res_const, (max_memory_bytes, max_memory_bytes))
                return
            except (ValueError, OSError):
                continue


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Forcefully terminate subprocess and any child processes in its process group."""
    pid = proc.pid
    # Kill process group if supported (enabled via os.setsid in preexec_fn)
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    # Direct process kill fallback
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass

    # Reaping child process
    try:
        proc.wait(timeout=1.0)
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Close pipes safely
    for pipe in (proc.stdout, proc.stderr):
        if pipe and not pipe.closed:
            try:
                pipe.close()
            except OSError:
                pass


def _stream_bounded_output(
    proc: subprocess.Popen[bytes],
    max_output_bytes: int,
    timeout_seconds: float,
    start_time: float,
) -> tuple[list[bytes], list[bytes], int, int, bool, bool, str | None]:
    """Incrementally read stdout and stderr with real-time byte caps and timeout.

    Returns:
        (stdout_chunks, stderr_chunks, stdout_len, stderr_len, limit_exceeded, timed_out, error_message)
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_len = 0
    stderr_len = 0
    limit_exceeded = False
    timed_out = False
    error_message: str | None = None
    deadline = start_time + timeout_seconds

    # Try selector-based multiplexing first (efficient & non-blocking on POSIX)
    use_selector = False
    sel = selectors.DefaultSelector()
    try:
        if proc.stdout and proc.stderr:
            sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
            sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")
            if hasattr(os, "set_blocking"):
                try:
                    os.set_blocking(proc.stdout.fileno(), False)
                    os.set_blocking(proc.stderr.fileno(), False)
                except OSError:
                    pass
            use_selector = True
    except (OSError, ValueError):
        use_selector = False
        sel.close()
        # Restore blocking mode if set_blocking was applied
        if hasattr(os, "set_blocking"):
            try:
                if proc.stdout:
                    os.set_blocking(proc.stdout.fileno(), True)
                if proc.stderr:
                    os.set_blocking(proc.stderr.fileno(), True)
            except OSError:
                pass

    if use_selector:
        try:
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break

                events = sel.select(timeout=min(remaining, 0.1))
                if not events:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    if proc.poll() is not None:
                        break
                    continue

                for key, _ in events:
                    try:
                        chunk = os.read(key.fd, CHUNK_SIZE)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        chunk = b""

                    if not chunk:
                        sel.unregister(key.fileobj)
                        try:
                            key.fileobj.close()
                        except OSError:
                            pass
                        continue

                    if key.data == "stdout":
                        stdout_len += len(chunk)
                        stdout_chunks.append(chunk)
                        if stdout_len > max_output_bytes:
                            limit_exceeded = True
                            error_message = f"Scanner output exceeded limit ({stdout_len} > {max_output_bytes} bytes)"
                            break
                    else:
                        stderr_len += len(chunk)
                        stderr_chunks.append(chunk)
                        if stderr_len > max_output_bytes:
                            limit_exceeded = True
                            error_message = f"Scanner stderr output exceeded limit ({stderr_len} > {max_output_bytes} bytes)"
                            break

                    if (stdout_len + stderr_len) > max_output_bytes:
                        limit_exceeded = True
                        error_message = f"Scanner output exceeded limit ({stdout_len + stderr_len} > {max_output_bytes} bytes)"
                        break

                if limit_exceeded:
                    break
        finally:
            sel.close()
    else:
        # Fallback to threaded reader for platforms where selectors cannot poll pipes (e.g. Windows)
        limit_event = threading.Event()

        def _reader(pipe: IO[bytes], chunks_acc: list[bytes], is_stdout: bool) -> None:
            nonlocal limit_exceeded, error_message, stdout_len, stderr_len
            try:
                while not limit_event.is_set():
                    chunk = pipe.read(CHUNK_SIZE)
                    if chunk is None:
                        time.sleep(0.005)
                        continue
                    if chunk == b"":
                        break
                    chunks_acc.append(chunk)
                    if is_stdout:
                        stdout_len += len(chunk)
                        if stdout_len > max_output_bytes:
                            limit_exceeded = True
                            error_message = f"Scanner output exceeded limit ({stdout_len} > {max_output_bytes} bytes)"
                            limit_event.set()
                            break
                    else:
                        stderr_len += len(chunk)
                        if stderr_len > max_output_bytes:
                            limit_exceeded = True
                            error_message = f"Scanner stderr output exceeded limit ({stderr_len} > {max_output_bytes} bytes)"
                            limit_event.set()
                            break

                    if (stdout_len + stderr_len) > max_output_bytes:
                        limit_exceeded = True
                        error_message = f"Scanner output exceeded limit ({stdout_len + stderr_len} > {max_output_bytes} bytes)"
                        limit_event.set()
                        break
            except (OSError, ValueError):
                pass

        if proc.stdout and proc.stderr:
            t_out = threading.Thread(
                target=_reader, args=(proc.stdout, stdout_chunks, True), daemon=True
            )
            t_err = threading.Thread(
                target=_reader, args=(proc.stderr, stderr_chunks, False), daemon=True
            )
            t_out.start()
            t_err.start()

            while proc.poll() is None:
                if limit_event.is_set():
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.01)

            t_out.join(timeout=0.2)
            t_err.join(timeout=0.2)

    return (
        stdout_chunks,
        stderr_chunks,
        stdout_len,
        stderr_len,
        limit_exceeded,
        timed_out,
        error_message,
    )


def run_scanner_process(
    cmd: Sequence[str],
    cwd: Path | str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
) -> ProcessExecutionResult:
    """Execute scanner command with streaming bounded output, rigid timeout, and OS memory limits."""
    start_time = time.monotonic()

    if timeout_seconds <= 0:
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=f"timeout_seconds must be positive, got {timeout_seconds}",
        )
    if max_output_bytes <= 0:
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=f"max_output_bytes must be positive, got {max_output_bytes}",
        )

    try:
        validated_cmd = validate_subprocess_args(cmd)
    except (SubprocessArgumentError, ValueError) as exc:
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_message=f"Invalid subprocess arguments: {exc}",
        )

    def _preexec() -> None:
        if hasattr(os, "setsid"):
            try:
                os.setsid()
            except OSError:
                pass
        _apply_os_memory_limit(max_memory_bytes)

    try:
        process = subprocess.Popen(
            validated_cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=_preexec if os.name != "nt" else None,  # noqa: PLW1509
        )
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
    except OSError as exc:
        duration = time.monotonic() - start_time
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=duration,
            error_message=f"Subprocess execution failed: {exc}",
        )

    (
        stdout_chunks,
        stderr_chunks,
        _stdout_len,
        _stderr_len,
        limit_exceeded,
        timed_out,
        limit_error_msg,
    ) = _stream_bounded_output(
        process,
        max_output_bytes=max_output_bytes,
        timeout_seconds=float(timeout_seconds),
        start_time=start_time,
    )

    if limit_exceeded or timed_out:
        _kill_process_tree(process)
        duration = time.monotonic() - start_time

        if timed_out:
            return ProcessExecutionResult(
                status=ScannerStatus.TIMEOUT,
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=duration,
                error_message=f"Process '{cmd[0]}' timed out after {timeout_seconds} seconds.",
            )

        # Output limit exceeded: return capped diagnostic preview and error status
        stdout_preview = b"".join(stdout_chunks).decode("utf-8", errors="replace")[
            :1000
        ]
        stderr_preview = b"".join(stderr_chunks).decode("utf-8", errors="replace")[
            :1000
        ]
        return ProcessExecutionResult(
            status=ScannerStatus.ERROR,
            exit_code=process.returncode,
            stdout=stdout_preview,
            stderr=stderr_preview,
            duration_seconds=duration,
            error_message=limit_error_msg
            or f"Scanner output exceeded limit (> {max_output_bytes} bytes)",
        )

    # Process completed within limits
    try:
        process.wait(timeout=2.0)
    except (subprocess.TimeoutExpired, OSError):
        _kill_process_tree(process)

    duration = time.monotonic() - start_time
    stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    return ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=process.returncode,
        stdout=stdout_str,
        stderr=stderr_str,
        duration_seconds=duration,
    )
