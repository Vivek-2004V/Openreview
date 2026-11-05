"""Unit tests for safe process runner with bounded streaming and resource caps."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from openreview.adapters import ScannerStatus
from openreview.adapters.process_runner import (
    _apply_os_memory_limit,
    _kill_process_tree,
    run_scanner_process,
)


def test_process_runner_normal_execution(tmp_path: Path) -> None:
    """Verify normal command execution captures stdout and stderr cleanly."""
    script = tmp_path / "hello.py"
    script.write_text(
        "import sys\nsys.stdout.write('hello stdout\\n')\nsys.stderr.write('hello stderr\\n')\n"
    )
    res = run_scanner_process(
        cmd=[sys.executable, str(script)], cwd=tmp_path, timeout_seconds=5
    )

    assert res.status == ScannerStatus.SUCCESS
    assert res.exit_code == 0
    assert "hello stdout" in res.stdout
    assert "hello stderr" in res.stderr
    assert res.duration_seconds > 0.0
    assert res.error_message is None


def test_process_runner_non_zero_exit_code(tmp_path: Path) -> None:
    """Verify non-zero exit codes are preserved for adapter classification."""
    script = tmp_path / "exit42.py"
    script.write_text("import sys\nsys.exit(42)\n")
    res = run_scanner_process(
        cmd=[sys.executable, str(script)], cwd=tmp_path, timeout_seconds=5
    )

    assert res.status == ScannerStatus.SUCCESS
    assert res.exit_code == 42
    assert res.stdout == ""


def test_process_runner_executable_not_found(tmp_path: Path) -> None:
    """Verify NOT_FOUND status when binary is missing."""
    res = run_scanner_process(
        cmd=["/non_existent_binary_openreview_xyz123"],
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert res.status == ScannerStatus.NOT_FOUND
    assert res.exit_code is None
    assert "not found in system PATH" in str(res.error_message)


def test_process_runner_invalid_arguments(tmp_path: Path) -> None:
    """Verify invalid subprocess arguments are rejected immediately."""
    res = run_scanner_process(cmd=["echo", "bad\x00arg"], cwd=tmp_path)
    assert res.status == ScannerStatus.ERROR
    assert res.exit_code is None
    assert "Invalid subprocess arguments" in str(res.error_message)

    res_empty = run_scanner_process(cmd=[], cwd=tmp_path)
    assert res_empty.status == ScannerStatus.ERROR
    assert "Invalid subprocess arguments" in str(res_empty.error_message)


def test_process_runner_invalid_limits(tmp_path: Path) -> None:
    """Verify non-positive limits are rejected gracefully."""
    res_timeout = run_scanner_process(
        cmd=["echo", "hi"], cwd=tmp_path, timeout_seconds=0
    )
    assert res_timeout.status == ScannerStatus.ERROR
    assert "timeout_seconds must be positive" in str(res_timeout.error_message)

    res_bytes = run_scanner_process(
        cmd=["echo", "hi"], cwd=tmp_path, max_output_bytes=-5
    )
    assert res_bytes.status == ScannerStatus.ERROR
    assert "max_output_bytes must be positive" in str(res_bytes.error_message)


def test_process_runner_timeout_termination(tmp_path: Path) -> None:
    """Verify hanging commands are terminated at deadline without blocking."""
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(10)\n")
    res = run_scanner_process(
        cmd=[sys.executable, str(script)], cwd=tmp_path, timeout_seconds=1
    )

    assert res.status == ScannerStatus.TIMEOUT
    assert res.exit_code is None
    assert "timed out after 1 seconds" in str(res.error_message)
    assert res.duration_seconds >= 0.9


def test_process_runner_bounded_streaming_terminates_on_stdout_flood(
    tmp_path: Path,
) -> None:
    """Verify unbounded stdout stream is terminated immediately when limit is exceeded."""
    flood_script = tmp_path / "stdout_flood.py"
    flood_script.write_text(
        "import sys\nwhile True:\n    sys.stdout.write('X' * 10000)\n    sys.stdout.flush()\n"
    )

    max_bytes = 20_000
    res = run_scanner_process(
        cmd=[sys.executable, str(flood_script)],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_bytes=max_bytes,
    )

    assert res.status == ScannerStatus.ERROR
    assert "exceeded limit" in str(res.error_message)
    # Output returned should be truncated diagnostic preview (at most 1000 chars)
    assert len(res.stdout) <= 1000
    # Process must have been stopped promptly
    assert res.duration_seconds < 3.0


def test_process_runner_bounded_streaming_terminates_on_stderr_flood(
    tmp_path: Path,
) -> None:
    """Verify unbounded stderr stream is terminated immediately when limit is exceeded."""
    flood_script = tmp_path / "stderr_flood.py"
    flood_script.write_text(
        "import sys\nwhile True:\n    sys.stderr.write('E' * 10000)\n    sys.stderr.flush()\n"
    )

    max_bytes = 15_000
    res = run_scanner_process(
        cmd=[sys.executable, str(flood_script)],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_bytes=max_bytes,
    )

    assert res.status == ScannerStatus.ERROR
    assert "stderr output exceeded limit" in str(res.error_message)
    assert len(res.stderr) <= 1000


def test_process_runner_bounded_streaming_terminates_on_combined_output(
    tmp_path: Path,
) -> None:
    """Verify combined stdout + stderr streams terminate when aggregate limit is exceeded."""
    flood_script = tmp_path / "combined_flood.py"
    flood_script.write_text(
        "import sys\nwhile True:\n    sys.stdout.write('O' * 5000)\n    sys.stderr.write('E' * 5000)\n    sys.stdout.flush()\n    sys.stderr.flush()\n"
    )

    max_bytes = 15_000
    res = run_scanner_process(
        cmd=[sys.executable, str(flood_script)],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_bytes=max_bytes,
    )

    assert res.status == ScannerStatus.ERROR
    assert "exceeded limit" in str(res.error_message)


def test_process_runner_threaded_fallback_streaming(tmp_path: Path) -> None:
    """Verify fallback threaded reader operates identically when selector registration fails."""
    # Simulate platform where selectors fail on pipes (like Windows)
    with patch(
        "openreview.adapters.process_runner.selectors.DefaultSelector"
    ) as mock_sel_cls:
        mock_sel = MagicMock()
        mock_sel.register.side_effect = OSError("Unsupported pipe descriptor")
        mock_sel_cls.return_value = mock_sel

        # Normal execution via thread fallback
        script = tmp_path / "msg.py"
        script.write_text("print('threaded success')\n")
        res = run_scanner_process(
            cmd=[sys.executable, str(script)], cwd=tmp_path, timeout_seconds=5
        )
        assert res.status == ScannerStatus.SUCCESS
        assert "threaded success" in res.stdout

        # Flood termination via thread fallback
        flood_script = tmp_path / "flood_thread.py"
        flood_script.write_text(
            "import sys\nwhile True:\n    sys.stdout.write('Z' * 10000)\n    sys.stdout.flush()\n"
        )
        res_flood = run_scanner_process(
            cmd=[sys.executable, str(flood_script)],
            cwd=tmp_path,
            timeout_seconds=5,
            max_output_bytes=20_000,
        )
        assert res_flood.status == ScannerStatus.ERROR
        assert "exceeded limit" in str(res_flood.error_message)


def test_apply_os_memory_limit_with_resource() -> None:
    """Verify OS memory limit invokes resource.setrlimit when available."""
    mock_resource = MagicMock()
    mock_resource.RLIMIT_AS = 5
    mock_resource.RLIMIT_DATA = 9

    with (
        patch("openreview.adapters.process_runner.HAS_RESOURCE", True),
        patch("openreview.adapters.process_runner.resource", mock_resource),
    ):
        _apply_os_memory_limit(512 * 1024 * 1024)
        mock_resource.setrlimit.assert_called_once_with(
            5, (512 * 1024 * 1024, 512 * 1024 * 1024)
        )


def test_apply_os_memory_limit_handles_exceptions_gracefully() -> None:
    """Verify OS memory limit gracefully handles unsupported platforms or permission errors."""
    mock_resource = MagicMock()
    mock_resource.RLIMIT_AS = 5
    mock_resource.setrlimit.side_effect = ValueError(
        "current limit exceeds maximum limit"
    )

    with (
        patch("openreview.adapters.process_runner.HAS_RESOURCE", True),
        patch("openreview.adapters.process_runner.resource", mock_resource),
    ):
        # Should not raise exception
        _apply_os_memory_limit(256 * 1024 * 1024)


def test_apply_os_memory_limit_none_or_missing_resource() -> None:
    """Verify None limit or missing resource module does nothing."""
    with patch("openreview.adapters.process_runner.HAS_RESOURCE", False):
        _apply_os_memory_limit(512 * 1024 * 1024)

    with patch("openreview.adapters.process_runner.HAS_RESOURCE", True):
        _apply_os_memory_limit(None)


def test_kill_process_tree_calls_killpg() -> None:
    """Verify _kill_process_tree kills entire process group."""
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch("os.killpg") as mock_killpg, patch("os.getpgid", return_value=99999):
        _kill_process_tree(mock_proc)
        mock_killpg.assert_called_once()
        mock_proc.kill.assert_called_once()
