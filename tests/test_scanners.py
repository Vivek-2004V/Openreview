"""Unit tests for deterministic scanner adapters (Ruff, ESLint) per Epic E3."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from openreview.adapters import (
    ESLintAdapter,
    ProcessExecutionResult,
    RuffAdapter,
    ScannerAdapter,
    ScannerContext,
    ScannerResult,
    ScannerStatus,
    run_scanner_process,
)
from openreview.schemas import FindingOrigin, Severity


def test_scanner_adapter_protocol_conformance():
    """Verify adapters conform to ScannerAdapter protocol."""
    ruff = RuffAdapter()
    eslint = ESLintAdapter()

    assert isinstance(ruff, ScannerAdapter)
    assert isinstance(eslint, ScannerAdapter)
    assert ruff.name == "ruff"
    assert eslint.name == "eslint"
    assert ".py" in ruff.supported_extensions
    assert ".ts" in eslint.supported_extensions


def test_ruff_file_filtering():
    """Verify Ruff filters to only Python files."""
    ruff = RuffAdapter()
    files = ["src/main.py", "readme.md", "app.ts", "lib/helper.pyi"]
    filtered = ruff.filter_target_files(files)
    assert filtered == ["src/main.py", "lib/helper.pyi"]


def test_ruff_missing_executable(tmp_path):
    """Verify NOT_FOUND status when ruff executable is not in PATH."""
    ruff = RuffAdapter(binary_path="/non_existent/ruff_bin")
    ctx = ScannerContext(repo_root=tmp_path, target_files=["test.py"])

    res = ruff.run(ctx)
    assert res.status == ScannerStatus.NOT_FOUND
    assert "Executable 'ruff' not found in PATH" in str(res.error_message)
    assert res.findings == []


def test_ruff_parses_diagnostics(tmp_path):
    """Verify parsing and normalization of valid Ruff JSON output."""
    sample_diag = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "location": {"row": 3, "column": 1},
            "end_location": {"row": 3, "column": 10},
            "filename": str(tmp_path / "src/app.py"),
            "url": "https://docs.astral.sh/ruff/rules/unused-import/",
        },
        {
            "code": "S105",
            "message": "Possible hardcoded password assigned to `TOKEN`",
            "location": {"row": 15, "column": 5},
            "end_location": {"row": 15, "column": 20},
            "filename": str(tmp_path / "src/app.py"),
            "url": "https://docs.astral.sh/ruff/rules/hardcoded-password-string/",
        },
    ]

    ruff = RuffAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["src/app.py"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=1,
        stdout=json.dumps(sample_diag),
        stderr="",
        duration_seconds=0.12,
    )

    with patch("openreview.adapters.ruff.shutil.which", return_value="/usr/local/bin/ruff"), \
         patch("openreview.adapters.ruff.run_scanner_process", return_value=mock_exec):
        res = ruff.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 2

    f1 = res.findings[0]
    assert f1.rule_id == "F401"
    assert f1.title == "[F401] `os` imported but unused"
    assert f1.path == "src/app.py"
    assert f1.start_line == 3
    assert f1.severity == Severity.HIGH
    assert f1.origin == FindingOrigin.STATIC

    f2 = res.findings[1]
    assert f2.rule_id == "S105"
    assert f2.severity == Severity.HIGH  # S-codes map to security HIGH


def test_ruff_handles_malformed_json(tmp_path):
    """Verify MALFORMED_OUTPUT status when output is not valid JSON."""
    ruff = RuffAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["test.py"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=2,
        stdout="Fatal crash: segmentation fault at 0x0000",
        stderr="",
        duration_seconds=0.05,
    )

    with patch("openreview.adapters.ruff.shutil.which", return_value="/bin/ruff"), \
         patch("openreview.adapters.ruff.run_scanner_process", return_value=mock_exec):
        res = ruff.run(ctx)

    assert res.status == ScannerStatus.MALFORMED_OUTPUT
    assert "malformed JSON output" in str(res.error_message)


def test_ruff_handles_timeout(tmp_path):
    """Verify TIMEOUT status is surfaced cleanly without hanging."""
    ruff = RuffAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["test.py"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.TIMEOUT,
        exit_code=None,
        stdout="",
        stderr="",
        duration_seconds=30.0,
        error_message="Process 'ruff' timed out after 30 seconds.",
    )

    with patch("openreview.adapters.ruff.shutil.which", return_value="/bin/ruff"), \
         patch("openreview.adapters.ruff.run_scanner_process", return_value=mock_exec):
        res = ruff.run(ctx)

    assert res.status == ScannerStatus.TIMEOUT
    assert "timed out" in str(res.error_message)


def test_eslint_parses_diagnostics(tmp_path):
    """Verify parsing and normalization of ESLint JSON output."""
    sample_eslint_output = [
        {
            "filePath": str(tmp_path / "src/index.ts"),
            "messages": [
                {
                    "ruleId": "no-var",
                    "severity": 2,
                    "message": "Unexpected var, use let or const instead.",
                    "line": 10,
                    "column": 1,
                    "endLine": 10,
                    "endColumn": 4,
                }
            ],
        }
    ]

    eslint = ESLintAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["src/index.ts"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=1,
        stdout=json.dumps(sample_eslint_output),
        stderr="",
        duration_seconds=0.25,
    )

    with patch.object(eslint, "is_available", return_value=True), \
         patch.object(eslint, "_resolve_binary", return_value=["eslint"]), \
         patch("openreview.adapters.eslint.run_scanner_process", return_value=mock_exec):
        res = eslint.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.rule_id == "no-var"
    assert f.path == "src/index.ts"
    assert f.start_line == 10
    assert f.severity == Severity.HIGH  # severity 2 maps to HIGH
    assert f.origin == FindingOrigin.STATIC
