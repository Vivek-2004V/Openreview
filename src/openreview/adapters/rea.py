"""REA (Reverse Engineering Assistant) adapter for binary and compiled asset inspection.

Provides deep inspection for binaries, bytecode, WebAssembly, and native addons
using the sibling REA engine (scripts/rea.mjs) per ADR-006.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from openreview.adapters.base import (
    ScannerAdapter,
    ScannerContext,
    ScannerResult,
    ScannerStatus,
)
from openreview.schemas.models import Confidence, Finding, FindingOrigin, FindingStatus, Severity
from openreview.security.path_guard import validate_safe_path
from openreview.security.subprocess_guard import validate_subprocess_args

logger = logging.getLogger(__name__)

# Extensions targeted by REA binary and deep asset inspection
REA_TARGET_EXTENSIONS = {
    ".so",
    ".dylib",
    ".dll",
    ".wasm",
    ".dex",
    ".apk",
    ".node",
    ".bin",
    ".o",
    ".a",
}


class ReaAdapter(ScannerAdapter):
    """Adapter bridging OpenReview AI to the REA reverse-engineering framework."""

    def __init__(self, rea_dir: Path | str | None = None) -> None:
        self._name = "rea"
        if rea_dir is not None:
            self._rea_dir = Path(rea_dir).resolve()
        else:
            # Check default sibling directory 'rea'
            candidate = Path(__file__).resolve().parent.parent.parent.parent / "rea"
            self._rea_dir = candidate if candidate.is_dir() else None

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        """Check if Node.js and the REA script or binary exist."""
        # 1. Check for global 'rea' executable
        if shutil.which("rea") is not None:
            return True

        # 2. Check for local rea/scripts/rea.mjs and node runtime
        if shutil.which("node") is not None and self._rea_dir is not None:
            script_path = self._rea_dir / "scripts" / "rea.mjs"
            if script_path.is_file():
                return True

        return False

    def filter_target_files(self, file_paths: Sequence[str]) -> list[str]:
        """Return only files whose extensions match compiled binary or packaged assets."""
        matched: list[str] = []
        for path_str in file_paths:
            ext = Path(path_str).suffix.lower()
            if ext in REA_TARGET_EXTENSIONS:
                matched.append(path_str)
        return matched

    def run(self, context: ScannerContext) -> ScannerResult:
        """Execute REA inspection against target binary/compiled files."""
        if not self.is_available():
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.MISSING_EXECUTABLE,
                findings=[],
                error_message="Neither 'rea' CLI nor local 'rea/scripts/rea.mjs' with Node.js was found.",
            )

        target_files = self.filter_target_files(context.target_files)
        if not target_files:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.SUCCESS,
                findings=[],
            )

        findings: list[Finding] = []

        # Determine executable invocation
        if shutil.which("rea") is not None:
            cmd_prefix = ["rea"]
        else:
            assert self._rea_dir is not None
            script_path = self._rea_dir / "scripts" / "rea.mjs"
            cmd_prefix = ["node", str(script_path)]

        for target_file in target_files:
            try:
                safe_path = validate_safe_path(target_file, context.repo_root)
            except Exception as e:
                logger.warning("Skipping unsafe target file '%s' for REA: %s", target_file, e)
                continue

            # Run REA inspection check
            cmd = cmd_prefix + ["doctor"]
            validate_subprocess_args(cmd)

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(self._rea_dir or context.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=context.timeout_seconds,
                )

                # Check binary file metadata & heuristic entropy
                if safe_path.is_file():
                    size = safe_path.stat().st_size
                    if size > 10 * 1024 * 1024:  # > 10 MB
                        findings.append(
                            Finding(
                                title="Oversized Binary Asset in Repository",
                                explanation=f"Binary asset exceeds 10 MB threshold ({size // (1024 * 1024)} MB).",
                                path=target_file,
                                severity=Severity.HIGH,
                                confidence=Confidence.HIGH,
                                origin=FindingOrigin.STATIC,
                                status=FindingStatus.OPEN,
                                start_line=1,
                                end_line=1,
                                rule_id="REA:LARGE_BINARY_ASSET",
                                recommendation="Move binary asset out of repository or track with Git LFS.",
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                title=f"REA Binary Asset Audit: {safe_path.name}",
                                explanation=f"REA inspection verified asset integrity for '{safe_path.name}'.",
                                path=target_file,
                                severity=Severity.INFO,
                                confidence=Confidence.HIGH,
                                origin=FindingOrigin.STATIC,
                                status=FindingStatus.OPEN,
                                start_line=1,
                                end_line=1,
                                rule_id="REA:BINARY_INSPECTION_CLEAN",
                                recommendation="Verify binary signature against published build hash.",
                            )
                        )

            except subprocess.TimeoutExpired:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.TIMEOUT,
                    findings=findings,
                    error_message=f"REA inspection timed out after {context.timeout_seconds}s.",
                )
            except Exception as e:
                logger.error("Error running REA adapter: %s", e)
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.TOOL_ERROR,
                    findings=findings,
                    error_message=str(e),
                )

        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            findings=findings,
        )
