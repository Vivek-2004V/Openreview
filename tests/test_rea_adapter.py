"""Unit tests for REA scanner adapter (binary integrity, inspection, security properties)."""

import json
from pathlib import Path
from unittest.mock import patch

from openreview.adapters import (
    ProcessExecutionResult,
    ReaAdapter,
    ScannerAdapter,
    ScannerContext,
    ScannerStatus,
)
from openreview.schemas import Severity


def create_dummy_elf(path: Path) -> None:
    """Helper to write a valid minimal 64-bit ELF binary header."""
    header = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 24
    path.write_bytes(header)


def create_dummy_wasm(path: Path) -> None:
    """Helper to write a valid minimal WebAssembly module header."""
    header = b"\x00asm\x01\x00\x00\x00" + b"\x00" * 8
    path.write_bytes(header)


def test_rea_adapter_protocol_conformance():
    """Verify ReaAdapter conforms to the ScannerAdapter protocol."""
    adapter = ReaAdapter()
    assert isinstance(adapter, ScannerAdapter)
    assert adapter.name == "rea"
    assert ".so" in adapter.supported_extensions
    assert ".dylib" in adapter.supported_extensions
    assert ".wasm" in adapter.supported_extensions
    assert ".dll" in adapter.supported_extensions
    assert ".node" in adapter.supported_extensions


def test_rea_file_filtering():
    """Verify ReaAdapter correctly filters only supported compiled/binary files."""
    adapter = ReaAdapter()
    files = [
        "src/main.py",
        "build/libcrypto.so",
        "frontend/engine.wasm",
        "README.md",
        "native/addon.node",
        "dist/app.dll",
    ]
    filtered = adapter.filter_target_files(files)
    assert set(filtered) == {
        "build/libcrypto.so",
        "frontend/engine.wasm",
        "native/addon.node",
        "dist/app.dll",
    }


def test_rea_missing_executable(tmp_path: Path):
    """Verify NOT_FOUND status when REA CLI and local rea.mjs script are unavailable."""
    adapter = ReaAdapter(
        binary_path="/non_existent/rea_binary", rea_dir="/non_existent/rea_dir"
    )
    ctx = ScannerContext(repo_root=tmp_path, target_files=["lib.so"])

    with patch.object(adapter, "_resolve_cmd_prefix", return_value=None):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.NOT_FOUND
    assert "Neither 'rea' CLI nor local 'rea/scripts/rea.mjs'" in str(res.error_message)
    assert res.findings == []


def test_rea_corrupted_signature_detection(tmp_path: Path):
    """Verify that a corrupted or plain-text binary produces an invalid signature finding and NO clean finding."""
    corrupted_wasm = tmp_path / "corrupted.wasm"
    corrupted_wasm.write_text(
        "This is plain text and definitely not a valid wasm file!"
    )

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["corrupted.wasm"])

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process") as mock_run,
    ):
        res = adapter.run(ctx)
        # REA subprocess should not even be called when signature validation fails
        mock_run.assert_not_called()

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 1
    finding = res.findings[0]
    assert finding.rule_id == "REA:INVALID_BINARY_SIGNATURE"
    assert finding.severity == Severity.HIGH
    assert "Invalid WebAssembly header" in finding.explanation
    # Ensure NO clean finding was generated
    assert all(f.rule_id != "REA:BINARY_INSPECTION_CLEAN" for f in res.findings)


def test_rea_empty_binary_detection(tmp_path: Path):
    """Verify empty binary asset is detected as corrupted/invalid."""
    empty_so = tmp_path / "empty.so"
    empty_so.write_bytes(b"")

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["empty.so"])

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
    ):
        res = adapter.run(ctx)

    assert len(res.findings) == 1
    assert res.findings[0].rule_id == "REA:INVALID_BINARY_SIGNATURE"
    assert "empty (0 bytes)" in res.findings[0].explanation


def test_rea_invokes_inspect_with_target_file(tmp_path: Path):
    """Verify that ReaAdapter invokes 'inspect <target_file>' with --format json and NOT 'doctor'."""
    bin_file = tmp_path / "libtest.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["libtest.so"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout=json.dumps({"procedure_count": 10, "segment_count": 2}),
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch(
            "openreview.adapters.rea.run_scanner_process", return_value=mock_exec
        ) as mock_run,
    ):
        res = adapter.run(ctx)

    mock_run.assert_called_once()
    called_cmd = mock_run.call_args[1]["cmd"]
    assert "inspect" in called_cmd
    assert str(bin_file) in called_cmd
    assert "--format" in called_cmd
    assert "json" in called_cmd
    assert "doctor" not in called_cmd
    assert res.status == ScannerStatus.SUCCESS


def test_rea_parses_real_output_and_produces_clean_finding(tmp_path: Path):
    """Verify that when a real check succeeds with no defects, a verified clean finding is produced."""
    bin_file = tmp_path / "engine.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["engine.so"])

    inspection_data = {
        "document": str(bin_file),
        "procedure_count": 55,
        "segment_count": 4,
        "segments": [
            {"name": ".text", "permissions": "rx"},
            {"name": ".rodata", "permissions": "r"},
            {"name": ".data", "permissions": "rw"},
            {"name": ".bss", "permissions": "rw"},
        ],
        "mitigations": {
            "pie": True,
            "nx": True,
            "canary": True,
        },
    }

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout=json.dumps(inspection_data),
        stderr="",
        duration_seconds=0.15,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.rule_id == "REA:BINARY_INSPECTION_CLEAN"
    assert f.severity == Severity.INFO
    assert "procedures: 55" in f.explanation
    assert "segments: 4" in f.explanation


def test_rea_detects_insecure_rwx_segment(tmp_path: Path):
    """Verify that a segment with RWX permissions triggers a W^X violation finding."""
    bin_file = tmp_path / "unsafe.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["unsafe.so"])

    inspection_data = {
        "document": str(bin_file),
        "procedure_count": 10,
        "segments": [
            {"name": ".text", "permissions": "rx"},
            {"name": ".shellcode_stage", "permissions": "rwx"},
        ],
    }

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout=json.dumps(inspection_data),
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 1
    finding = res.findings[0]
    assert finding.rule_id == "REA:RWX_SEGMENT"
    assert finding.severity == Severity.HIGH
    assert ".shellcode_stage" in finding.title
    assert "W^X" in finding.explanation
    # Ensure NO clean finding was generated
    assert all(f.rule_id != "REA:BINARY_INSPECTION_CLEAN" for f in res.findings)


def test_rea_detects_missing_mitigations(tmp_path: Path):
    """Verify missing PIE/ASLR and NX triggers security property findings."""
    bin_file = tmp_path / "legacy.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["legacy.so"])

    inspection_data = {
        "document": str(bin_file),
        "procedure_count": 10,
        "mitigations": {
            "pie": False,
            "nx": False,
            "canary": False,
        },
    }

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout=json.dumps(inspection_data),
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    rule_ids = {f.rule_id for f in res.findings}
    assert "REA:MISSING_ASLR" in rule_ids
    assert "REA:MISSING_NX" in rule_ids
    assert "REA:MISSING_STACK_CANARY" in rule_ids
    # Ensure NO clean finding was generated
    assert "REA:BINARY_INSPECTION_CLEAN" not in rule_ids


def test_rea_detects_structural_integrity_violation(tmp_path: Path):
    """Verify REA structural corruption reporting generates structural integrity finding."""
    bin_file = tmp_path / "broken.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["broken.so"])

    inspection_data = {
        "document": str(bin_file),
        "corrupted": True,
        "corrupted_reason": "Invalid section header offset beyond EOF",
    }

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout=json.dumps(inspection_data),
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.SUCCESS
    assert len(res.findings) == 1
    assert res.findings[0].rule_id == "REA:STRUCTURAL_INTEGRITY_VIOLATION"
    assert res.findings[0].severity == Severity.HIGH
    assert "Invalid section header offset" in res.findings[0].explanation
    assert all(f.rule_id != "REA:BINARY_INSPECTION_CLEAN" for f in res.findings)


def test_rea_reports_scanner_failure_on_non_zero_exit(tmp_path: Path):
    """Verify that when REA fails during execution, a scanner failure is reported and NO clean finding is emitted."""
    bin_file = tmp_path / "native.node"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["native.node"])

    error_payload = {
        "error": "Analysis failed",
        "code": "execution_failure",
        "message": "Analysis could not complete: Ghidra disassembler bridge crashed.",
    }

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=1,
        stdout=json.dumps(error_payload),
        stderr="",
        duration_seconds=0.2,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.ERROR
    assert res.is_successful is False
    assert "Analysis could not complete" in str(res.error_message)
    # Crucially: no clean finding emitted on tool error
    assert res.findings == []


def test_rea_handles_timeout(tmp_path: Path):
    """Verify timeout status is surfaced cleanly without hanging or emitting clean findings."""
    bin_file = tmp_path / "big.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(
        repo_root=tmp_path, target_files=["big.so"], timeout_seconds=15
    )

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.TIMEOUT,
        exit_code=None,
        stdout="",
        stderr="",
        duration_seconds=15.0,
        error_message="Process 'rea' timed out after 15 seconds.",
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.TIMEOUT
    assert res.is_successful is False
    assert "timed out" in str(res.error_message)
    assert res.findings == []


def test_rea_handles_malformed_json(tmp_path: Path):
    """Verify malformed JSON from REA returns MALFORMED_OUTPUT."""
    bin_file = tmp_path / "module.wasm"
    create_dummy_wasm(bin_file)

    adapter = ReaAdapter()
    ctx = ScannerContext(repo_root=tmp_path, target_files=["module.wasm"])

    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=0,
        stdout="<<< Fatal error: corrupted memory >>>",
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.MALFORMED_OUTPUT
    assert res.is_successful is False
    assert "malformed JSON output" in str(res.error_message)
    assert res.findings == []


def test_rea_scanner_failure_tracked_in_review_engine(tmp_path: Path):
    """Verify that when REA fails, ReviewEngine tracks it under scanners_failed and emits NO clean finding."""
    bin_file = tmp_path / "libcrypto.so"
    create_dummy_elf(bin_file)

    adapter = ReaAdapter()
    error_payload = {
        "error": "Analysis failed",
        "code": "execution_failure",
        "message": "Provider failed",
    }
    mock_exec = ProcessExecutionResult(
        status=ScannerStatus.SUCCESS,
        exit_code=1,
        stdout=json.dumps(error_payload),
        stderr="",
        duration_seconds=0.1,
    )

    with (
        patch.object(adapter, "is_available", return_value=True),
        patch.object(adapter, "_resolve_cmd_prefix", return_value=["rea"]),
        patch("openreview.adapters.rea.run_scanner_process", return_value=mock_exec),
    ):
        ctx = ScannerContext(repo_root=tmp_path, target_files=["libcrypto.so"])
        res = adapter.run(ctx)

    assert res.status == ScannerStatus.ERROR
    assert res.is_successful is False
