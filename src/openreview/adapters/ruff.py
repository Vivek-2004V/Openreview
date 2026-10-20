"""Ruff deterministic Python linter adapter."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Sequence

from openreview.adapters.base import (
    ScannerAdapter,
    ScannerContext,
    ScannerResult,
    ScannerStatus,
)
from openreview.adapters.process_runner import run_scanner_process
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    Severity,
)


def map_ruff_code_to_severity(code: str) -> Severity:
    """Map standard Ruff error/linter prefixes to normalized Severity."""
    code_upper = code.upper()
    if code_upper.startswith("S"):  # flake8-bandit security issues
        return Severity.HIGH
    if code_upper.startswith("F"):  # Pyflakes logic defects & syntax
        return Severity.HIGH
    if code_upper.startswith("E") or code_upper.startswith("W"):  # pycodestyle
        return Severity.LOW
    if code_upper.startswith("B"):  # flake8-bugbear design/bug risks
        return Severity.MEDIUM
    if code_upper.startswith("I") or code_upper.startswith("D"):  # isort / pydocstyle
        return Severity.INFO
    return Severity.MEDIUM


class RuffAdapter(ScannerAdapter):
    """Deterministic static analysis adapter for Ruff (Python)."""

    name: str = "ruff"
    supported_extensions: set[str] = {".py", ".pyi"}

    def __init__(self, binary_path: str = "ruff"):
        self.binary_path = binary_path

    def is_available(self) -> bool:
        return shutil.which(self.binary_path) is not None

    def filter_target_files(self, files: Sequence[str]) -> list[str]:
        return [f for f in files if Path(f).suffix.lower() in self.supported_extensions]

    def run(self, context: ScannerContext) -> ScannerResult:
        """Run Ruff on specified files and normalize output into Finding models."""
        target_files = self.filter_target_files(context.target_files)
        if not target_files:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.SUCCESS,
                duration_seconds=0.0,
                findings=[],
                raw_output="",
            )

        if not self.is_available():
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.NOT_FOUND,
                duration_seconds=0.0,
                findings=[],
                error_message="Executable 'ruff' not found in PATH. Install via 'pip install ruff'.",
            )

        cmd = [
            self.binary_path,
            "check",
            "--output-format=json",
            "--no-fix",
        ] + context.extra_args + target_files

        exec_res = run_scanner_process(
            cmd=cmd,
            cwd=context.repo_root,
            timeout_seconds=context.timeout_seconds,
            max_output_bytes=context.max_output_bytes,
        )

        if exec_res.status != ScannerStatus.SUCCESS:
            return ScannerResult(
                scanner_name=self.name,
                status=exec_res.status,
                duration_seconds=exec_res.duration_seconds,
                findings=[],
                exit_code=exec_res.exit_code,
                error_message=exec_res.error_message,
            )

        # Parse JSON output
        findings: list[Finding] = []
        raw_output = exec_res.stdout
        if raw_output.strip():
            try:
                diagnostics = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.MALFORMED_OUTPUT,
                    duration_seconds=exec_res.duration_seconds,
                    findings=[],
                    exit_code=exec_res.exit_code,
                    raw_output=raw_output[:500],
                    error_message=f"Ruff produced malformed JSON output: {exc}",
                )

            if not isinstance(diagnostics, list):
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.MALFORMED_OUTPUT,
                    duration_seconds=exec_res.duration_seconds,
                    findings=[],
                    exit_code=exec_res.exit_code,
                    error_message="Expected JSON array of diagnostics from Ruff",
                )

            for diag in diagnostics:
                finding = self._normalize_diagnostic(diag, context.repo_root)
                if finding:
                    findings.append(finding)

        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=exec_res.duration_seconds,
            findings=findings,
            exit_code=exec_res.exit_code,
            raw_output=raw_output,
        )

    def _normalize_diagnostic(self, diag: dict[str, Any], repo_root: Path) -> Finding | None:
        try:
            code = diag.get("code") or "RUFF"
            message = diag.get("message") or "Code issue detected"
            raw_filename = diag.get("filename", "")

            # Normalize to relative POSIX path
            try:
                file_path = Path(raw_filename)
                if file_path.is_absolute():
                    rel_path = file_path.relative_to(repo_root.resolve()).as_posix()
                else:
                    rel_path = file_path.as_posix()
            except ValueError:
                rel_path = raw_filename

            location = diag.get("location", {})
            start_line = location.get("row")
            end_location = diag.get("end_location", {})
            end_line = end_location.get("row", start_line)

            url = diag.get("url")
            refs = [url] if url else []

            severity = map_ruff_code_to_severity(code)

            return Finding(
                title=f"[{code}] {message}",
                explanation=message,
                path=rel_path,
                severity=severity,
                confidence=Confidence.HIGH,
                origin=FindingOrigin.STATIC,
                start_line=start_line,
                end_line=end_line,
                rule_id=code,
                references=refs,
            )
        except Exception:
            return None
