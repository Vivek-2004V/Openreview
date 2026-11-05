"""REA (Reverse Engineering Assistant) adapter for binary and compiled asset inspection.

Provides deep inspection for binaries, bytecode, WebAssembly, and native addons
using the sibling REA engine (scripts/rea.mjs) per ADR-006.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

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
    FindingStatus,
    Severity,
)
from openreview.security.path_guard import PathTraversalError, validate_safe_path

logger = logging.getLogger(__name__)

# Extensions targeted by REA binary and deep asset inspection
REA_TARGET_EXTENSIONS: set[str] = {
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


def validate_binary_signature(path: Path) -> tuple[bool, str]:
    """Validate binary file header against expected format signatures.

    Returns (is_valid, error_message).
    """
    try:
        size = path.stat().st_size
    except OSError as err:
        return False, f"Could not inspect file '{path.name}': {err}"

    if size == 0:
        return False, f"Binary asset '{path.name}' is empty (0 bytes)."

    try:
        with open(path, "rb") as f:
            header = f.read(1024)
    except OSError as err:
        return False, f"Could not read binary header for '{path.name}': {err}"

    if len(header) < 4:
        return False, f"Binary asset '{path.name}' is truncated ({size} bytes)."

    ext = path.suffix.lower()

    def is_elf(h: bytes) -> bool:
        return (
            len(h) >= 16
            and h[:4] == b"\x7fELF"
            and h[4] in (1, 2)
            and h[5] in (1, 2)
            and h[6] == 1
        )

    def is_macho(h: bytes) -> bool:
        return len(h) >= 4 and h[:4] in (
            b"\xfe\xed\xfa\xce",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
        )

    def is_pe(h: bytes) -> bool:
        if len(h) < 64 or h[:2] != b"MZ":
            return False
        pe_offset = int.from_bytes(h[0x3C:0x40], "little")
        return len(h) >= pe_offset + 4 and h[pe_offset : pe_offset + 4] == b"PE\x00\x00"

    def is_wasm(h: bytes) -> bool:
        return len(h) >= 8 and h[:4] == b"\x00asm" and h[4:8] == b"\x01\x00\x00\x00"

    def is_dex(h: bytes) -> bool:
        return len(h) >= 8 and h[:4] == b"dex\n"

    def is_apk_zip(h: bytes) -> bool:
        return len(h) >= 4 and h[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

    def is_archive(h: bytes) -> bool:
        return len(h) >= 8 and h[:8] == b"!<arch>\n"

    if ext == ".wasm":
        if not is_wasm(header):
            return (
                False,
                f"Invalid WebAssembly header in '{path.name}': expected magic bytes '\\x00asm\\x01\\x00\\x00\\x00'.",
            )
    elif ext == ".dex":
        if not is_dex(header):
            return (
                False,
                f"Invalid Android DEX header in '{path.name}': expected magic bytes 'dex\\n'.",
            )
    elif ext == ".apk":
        if not is_apk_zip(header):
            return (
                False,
                f"Invalid Android APK package header in '{path.name}': expected ZIP/APK 'PK' signature.",
            )
    elif ext == ".a":
        if not is_archive(header):
            return (
                False,
                f"Invalid static library archive header in '{path.name}': expected '!<arch>\\n' signature.",
            )
    elif ext == ".dylib":
        if not is_macho(header):
            return (
                False,
                f"Invalid Mach-O dylib header in '{path.name}': missing valid Mach-O magic number.",
            )
    elif ext == ".dll":
        if not is_pe(header):
            return (
                False,
                f"Invalid Windows DLL header in '{path.name}': missing valid MZ/PE signature.",
            )
    elif ext == ".so":
        if not (is_elf(header) or is_macho(header)):
            return (
                False,
                f"Invalid shared object header in '{path.name}': expected ELF or Mach-O signature.",
            )
    elif ext == ".o":
        if not (is_elf(header) or is_macho(header)):
            return (
                False,
                f"Invalid object file header in '{path.name}': expected ELF or Mach-O signature.",
            )
    elif ext == ".node":
        if not (is_elf(header) or is_macho(header) or is_pe(header)):
            return (
                False,
                f"Invalid Node native addon header in '{path.name}': expected ELF, Mach-O, or PE signature.",
            )
    elif ext == ".bin":
        is_known = (
            is_elf(header)
            or is_macho(header)
            or is_pe(header)
            or is_wasm(header)
            or is_dex(header)
            or is_apk_zip(header)
            or is_archive(header)
        )
        if not is_known and not (b"\x00" in header or any(b > 127 for b in header)):
            return (
                False,
                f"Invalid binary asset '{path.name}': expected compiled binary format or raw binary bytes, found plain text.",
            )

    return True, ""


class ReaAdapter(ScannerAdapter):
    """Adapter bridging OpenReview AI to the REA reverse-engineering framework."""

    name: str = "rea"
    supported_extensions: ClassVar[set[str]] = REA_TARGET_EXTENSIONS

    def __init__(
        self,
        rea_dir: Path | str | None = None,
        binary_path: str | None = None,
    ) -> None:
        self.name = "rea"
        self._binary_path = binary_path
        if rea_dir is not None:
            self._rea_dir = Path(rea_dir).resolve()
        else:
            # Check default sibling directory 'rea'
            candidate = Path(__file__).resolve().parent.parent.parent.parent / "rea"
            self._rea_dir = candidate if candidate.is_dir() else None

    def _resolve_cmd_prefix(self) -> list[str] | None:
        """Resolve command prefix to execute REA."""
        # 1. Explicit binary path if provided
        if self._binary_path and shutil.which(self._binary_path):
            return [self._binary_path]

        # 2. Global 'rea' executable
        if shutil.which("rea") is not None:
            return ["rea"]

        # 3. Local rea/scripts/rea.mjs and node runtime
        if shutil.which("node") is not None and self._rea_dir is not None:
            script_path = self._rea_dir / "scripts" / "rea.mjs"
            if script_path.is_file():
                return ["node", str(script_path)]

        return None

    def is_available(self) -> bool:
        """Check if REA executable or local script with Node.js is available."""
        return self._resolve_cmd_prefix() is not None

    def filter_target_files(self, file_paths: Sequence[str]) -> list[str]:
        """Return only files whose extensions match compiled binary or packaged assets."""
        matched: list[str] = []
        for path_str in file_paths:
            ext = Path(path_str).suffix.lower()
            if ext in self.supported_extensions:
                matched.append(path_str)
        return matched

    def _evaluate_inspection_data(
        self,
        data: dict[str, Any],
        safe_path: Path,
        target_file: str,
    ) -> list[Finding]:
        """Evaluate REA's parsed output for structural integrity and security properties."""
        findings: list[Finding] = []

        # 1. File size threshold check
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

        overview = data.get("normalized_result", data) if isinstance(data, dict) else {}
        if not isinstance(overview, dict):
            overview = {}

        # 2. Structural integrity check
        if overview.get("corrupted") or overview.get("structural_integrity") is False:
            reason = (
                overview.get("corrupted_reason")
                or "Corrupted binary structure detected"
            )
            findings.append(
                Finding(
                    title=f"Structural Integrity Violation: {safe_path.name}",
                    explanation=f"Binary asset '{safe_path.name}' failed structural integrity validation: {reason}.",
                    path=target_file,
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    origin=FindingOrigin.STATIC,
                    status=FindingStatus.OPEN,
                    start_line=1,
                    end_line=1,
                    rule_id="REA:STRUCTURAL_INTEGRITY_VIOLATION",
                    recommendation="Rebuild binary asset from trusted source or remove corrupted asset.",
                )
            )

        # 3. Insecure segment permissions (W^X / RWX violation)
        segments = overview.get("segments")
        if isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, dict):
                    seg_name = seg.get("name", "unnamed")
                    perms = str(seg.get("permissions", "")).lower()
                    flags = seg.get("flags", [])
                    flags_lower = (
                        [str(f).lower() for f in flags]
                        if isinstance(flags, list)
                        else []
                    )
                    is_rwx = (
                        ("w" in perms and "x" in perms)
                        or ("write" in flags_lower and "execute" in flags_lower)
                        or seg.get("rwx") is True
                    )
                    if is_rwx:
                        findings.append(
                            Finding(
                                title=f"Insecure Segment Permissions (W^X Violation): {seg_name}",
                                explanation=(
                                    f"Segment '{seg_name}' in '{safe_path.name}' is both writable and executable, "
                                    "violating W^X (Write XOR Execute) security policy."
                                ),
                                path=target_file,
                                severity=Severity.HIGH,
                                confidence=Confidence.HIGH,
                                origin=FindingOrigin.STATIC,
                                status=FindingStatus.OPEN,
                                start_line=1,
                                end_line=1,
                                rule_id="REA:RWX_SEGMENT",
                                recommendation="Enforce W^X memory protection so no segment is simultaneously writable and executable.",
                            )
                        )

        # 4. Security mitigations check (PIE/ASLR, NX/DEP, Stack Canary)
        mitigations = overview.get("mitigations") or overview.get("security")
        if isinstance(mitigations, dict):
            if mitigations.get("pie") is False or mitigations.get("aslr") is False:
                findings.append(
                    Finding(
                        title=f"Position Independent Executable (PIE/ASLR) Disabled: {safe_path.name}",
                        explanation=f"Binary '{safe_path.name}' was compiled without PIE/ASLR protection.",
                        path=target_file,
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        origin=FindingOrigin.STATIC,
                        status=FindingStatus.OPEN,
                        start_line=1,
                        end_line=1,
                        rule_id="REA:MISSING_ASLR",
                        recommendation="Compile binary with Position Independent Executable (PIE/ASLR) enabled.",
                    )
                )
            if mitigations.get("nx") is False or mitigations.get("dep") is False:
                findings.append(
                    Finding(
                        title=f"No-Execute (NX/DEP) Protection Disabled: {safe_path.name}",
                        explanation=f"Binary '{safe_path.name}' lacks No-Execute (NX/DEP) memory protection.",
                        path=target_file,
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        origin=FindingOrigin.STATIC,
                        status=FindingStatus.OPEN,
                        start_line=1,
                        end_line=1,
                        rule_id="REA:MISSING_NX",
                        recommendation="Enable NX/DEP memory protection (-z noexecstack).",
                    )
                )
            if mitigations.get("canary") is False:
                findings.append(
                    Finding(
                        title=f"Stack Canary Protection Missing: {safe_path.name}",
                        explanation=f"Binary '{safe_path.name}' was compiled without stack canary protection.",
                        path=target_file,
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        origin=FindingOrigin.STATIC,
                        status=FindingStatus.OPEN,
                        start_line=1,
                        end_line=1,
                        rule_id="REA:MISSING_STACK_CANARY",
                        recommendation="Recompile binary with stack smash protection (-fstack-protector-strong).",
                    )
                )

        return findings

    def run(self, context: ScannerContext) -> ScannerResult:
        """Execute REA inspection against target binary/compiled files."""
        start_time = time.monotonic()

        target_files = self.filter_target_files(context.target_files)
        if not target_files:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.SUCCESS,
                duration_seconds=0.0,
                findings=[],
                raw_output="",
            )

        cmd_prefix = self._resolve_cmd_prefix()
        if not cmd_prefix:
            return ScannerResult(
                scanner_name=self.name,
                status=ScannerStatus.NOT_FOUND,
                duration_seconds=0.0,
                findings=[],
                error_message="Neither 'rea' CLI nor local 'rea/scripts/rea.mjs' with Node.js was found.",
            )

        findings: list[Finding] = []

        for target_file in target_files:
            try:
                safe_path = validate_safe_path(target_file, context.repo_root)
            except (PathTraversalError, ValueError) as e:
                logger.warning(
                    "Skipping unsafe target file '%s' for REA: %s", target_file, e
                )
                continue

            if not safe_path.is_file():
                logger.warning("Target file '%s' does not exist on disk.", safe_path)
                continue

            # 1. Validate file signature and binary header integrity
            sig_ok, sig_err = validate_binary_signature(safe_path)
            if not sig_ok:
                findings.append(
                    Finding(
                        title=f"Invalid Binary Signature: {safe_path.name}",
                        explanation=sig_err,
                        path=target_file,
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        origin=FindingOrigin.STATIC,
                        status=FindingStatus.OPEN,
                        start_line=1,
                        end_line=1,
                        rule_id="REA:INVALID_BINARY_SIGNATURE",
                        recommendation="Verify binary asset integrity; file does not match expected format for its extension.",
                    )
                )
                continue

            # 2. Invoke actual REA inspection operation with target file
            cmd = (
                cmd_prefix
                + ["inspect", str(safe_path), "--format", "json"]
                + context.extra_args
            )

            exec_res = run_scanner_process(
                cmd=cmd,
                cwd=context.repo_root,
                timeout_seconds=context.timeout_seconds,
                max_output_bytes=context.max_output_bytes,
                max_memory_bytes=context.max_memory_bytes,
            )

            duration = time.monotonic() - start_time

            if exec_res.status == ScannerStatus.TIMEOUT:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.TIMEOUT,
                    duration_seconds=duration,
                    findings=findings,
                    exit_code=None,
                    error_message=f"REA inspection timed out after {context.timeout_seconds}s on '{target_file}'.",
                )

            if exec_res.status == ScannerStatus.NOT_FOUND:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.NOT_FOUND,
                    duration_seconds=duration,
                    findings=[],
                    exit_code=None,
                    error_message=exec_res.error_message,
                )

            # Report scanner execution failure separately
            if exec_res.exit_code != 0:
                err_msg = exec_res.error_message
                if exec_res.stdout.strip():
                    try:
                        err_data = json.loads(exec_res.stdout)
                        if isinstance(err_data, dict):
                            err_msg = (
                                err_data.get("message")
                                or err_data.get("error")
                                or err_msg
                            )
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.debug("Failed to decode JSON error from REA output.")
                if not err_msg:
                    err_msg = (
                        exec_res.stderr.strip()
                        or f"Process exited with code {exec_res.exit_code}"
                    )

                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    findings=findings,
                    exit_code=exec_res.exit_code,
                    raw_output=exec_res.stdout[:500],
                    error_message=f"REA inspection failed for '{target_file}': {err_msg}",
                )

            # 3. Parse REA's real output
            raw_stdout = exec_res.stdout.strip()
            if not raw_stdout:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.MALFORMED_OUTPUT,
                    duration_seconds=duration,
                    findings=findings,
                    exit_code=exec_res.exit_code,
                    error_message=f"REA produced empty output for '{target_file}'",
                )

            try:
                data = json.loads(raw_stdout)
            except json.JSONDecodeError as exc:
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.MALFORMED_OUTPUT,
                    duration_seconds=duration,
                    findings=findings,
                    exit_code=exec_res.exit_code,
                    raw_output=raw_stdout[:500],
                    error_message=f"REA produced malformed JSON output for '{target_file}': {exc}",
                )

            # Check if JSON payload indicates error
            if isinstance(data, dict) and (
                data.get("error")
                or data.get("code")
                in ("execution_failure", "target_unavailable", "capability_unavailable")
            ):
                err_msg = (
                    data.get("message")
                    or data.get("error")
                    or "Unknown REA inspection error"
                )
                return ScannerResult(
                    scanner_name=self.name,
                    status=ScannerStatus.ERROR,
                    duration_seconds=duration,
                    findings=findings,
                    exit_code=exec_res.exit_code,
                    raw_output=raw_stdout[:500],
                    error_message=f"REA inspection failed for '{target_file}': {err_msg}",
                )

            # 4. Evaluate structural integrity and security properties
            file_findings = self._evaluate_inspection_data(data, safe_path, target_file)
            if file_findings:
                findings.extend(file_findings)
            else:
                # Produce clean finding ONLY when a real check was executed and no defects found
                overview = (
                    data.get("normalized_result", data)
                    if isinstance(data, dict)
                    else {}
                )
                proc_cnt = (
                    overview.get("procedure_count", 0)
                    if isinstance(overview, dict)
                    else 0
                )
                seg_cnt = (
                    overview.get("segment_count", len(overview.get("segments", [])))
                    if isinstance(overview, dict)
                    else 0
                )
                details = (
                    f" (procedures: {proc_cnt}, segments: {seg_cnt})"
                    if (proc_cnt or seg_cnt)
                    else ""
                )
                findings.append(
                    Finding(
                        title=f"REA Binary Asset Audit: {safe_path.name}",
                        explanation=f"REA inspection verified asset structural integrity and security properties for '{safe_path.name}'{details}.",
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

        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=time.monotonic() - start_time,
            findings=findings,
            exit_code=0,
        )
