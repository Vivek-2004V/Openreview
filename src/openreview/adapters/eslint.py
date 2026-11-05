"""ESLint deterministic JavaScript/TypeScript linter adapter."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

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


class ESLintAdapter(ScannerAdapter):
    """Deterministic static analysis adapter for ESLint (JS/TS)."""

    name: str = "eslint"
    supported_extensions: ClassVar[set[str]] = {
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
    }

    def __init__(self, binary_path: str | None = None):
        self._binary_path = binary_path

    def _resolve_binary(self) -> list[str] | None:
        if self._binary_path and shutil.which(self._binary_path):
            return [self._binary_path]
        if shutil.which("eslint"):
            return ["eslint"]
        if shutil.which("npx"):
            return ["npx", "--no-install", "eslint"]
        return None

    def is_available(self) -> bool:
        return self._resolve_binary() is not None

    def filter_target_files(self, files: Sequence[str]) -> list[str]:
        return [f for f in files if Path(f).suffix.lower() in self.supported_extensions]

    def run(self, context: ScannerContext) -> ScannerResult:
        """Run ESLint on target JS/TS files and normalize into Finding models."""
        target_files = self.filter_target_files(context.target_files)
        if not target_files:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.SUCCESS,
                duration_seconds=0.0,
                findings=[],
                raw_output="",
            )

        cmd_prefix = self._resolve_binary()
        if not cmd_prefix:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.NOT_FOUND,
                duration_seconds=0.0,
                findings=[],
                error_message="Neither 'eslint' nor 'npx' executable found in PATH.",
            )

        cmd = cmd_prefix + ["--format=json"] + context.extra_args + target_files

        exec_res = run_scanner_process(
            cmd=cmd,
            cwd=context.repo_root,
            timeout_seconds=context.timeout_seconds,
            max_output_bytes=context.max_output_bytes,
            max_memory_bytes=context.max_memory_bytes,
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

        findings: list[Finding] = []
        raw_output = exec_res.stdout
        if raw_output.strip():
            try:
                file_reports = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.MALFORMED_OUTPUT,
                    duration_seconds=exec_res.duration_seconds,
                    findings=[],
                    exit_code=exec_res.exit_code,
                    raw_output=raw_output[:500],
                    error_message=f"ESLint produced malformed JSON output: {exc}",
                )

            if isinstance(file_reports, list):
                for file_entry in file_reports:
                    if not isinstance(file_entry, dict):
                        continue
                    file_path_raw = file_entry.get("filePath", "")
                    try:
                        file_path = Path(file_path_raw)
                        if file_path.is_absolute():
                            rel_path = file_path.relative_to(
                                context.repo_root.resolve()
                            ).as_posix()
                        else:
                            rel_path = file_path.as_posix()
                    except ValueError:
                        rel_path = file_path_raw

                    messages = file_entry.get("messages", [])
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        rule_id = msg.get("ruleId") or "eslint"
                        message_text = msg.get("message", "Lint issue")
                        line_num = msg.get("line")
                        end_line_num = msg.get("endLine", line_num)
                        eslint_sev = msg.get("severity", 1)  # 1 = warning, 2 = error
                        severity = Severity.HIGH if eslint_sev == 2 else Severity.MEDIUM

                        is_pre_existing = False
                        if (
                            context.changed_lines_by_file is not None
                            and line_num is not None
                        ):
                            norm_rel = rel_path.replace("\\", "/").lstrip("./")
                            file_lines = context.changed_lines_by_file.get(
                                norm_rel
                            ) or context.changed_lines_by_file.get(rel_path)
                            if file_lines is not None and line_num not in file_lines:
                                is_pre_existing = True

                        findings.append(
                            Finding(
                                title=f"[{rule_id}] {message_text}",
                                explanation=message_text,
                                path=rel_path,
                                severity=severity,
                                confidence=Confidence.HIGH,
                                origin=FindingOrigin.STATIC,
                                start_line=line_num,
                                end_line=end_line_num,
                                rule_id=rule_id,
                                is_pre_existing=is_pre_existing,
                            )
                        )

        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=exec_res.duration_seconds,
            findings=findings,
            exit_code=exec_res.exit_code,
            raw_output=raw_output,
        )

    @staticmethod
    def filter_findings_to_changed_lines(
        findings: Sequence[Finding],
        changed_lines: set[int],
    ) -> list[Finding]:
        """Filter a list of findings to only those whose start_line falls in changed_lines."""
        return [
            f
            for f in findings
            if f.start_line is not None and f.start_line in changed_lines
        ]
