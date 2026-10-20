"""End-to-end integration test running OpenReview against seeded security defects."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from openreview import EXIT_FINDINGS_PRESENT
from openreview.cli import main
from openreview.config import AIConfig, OpenReviewConfig, ScannersConfig
from openreview.reports import render_json_report, render_markdown_report, validate_json_report_payload
from openreview.review.engine import ReviewEngine
from openreview.schemas.models import FindingOrigin, Severity


@pytest.fixture
def seeded_defect_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository seeded with intentional vulnerabilities."""
    repo = tmp_path / "vulnerable_repo"
    repo.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, capture_output=True, check=True)

    # Initial clean commit
    clean_py = repo / "auth.py"
    clean_py.write_text(
        "def authenticate(username, password):\n"
        "    return username == 'admin' and password == 'safe'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "auth.py"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "chore: initial clean state"], cwd=repo, capture_output=True, check=True)

    # Now introduce seeded defects in uncommitted working tree
    vulnerable_py_content = (
        "def authenticate(username, password):\n"
        "    # Defect 1: Hardcoded AWS secret\n"
        "    aws_token = 'AKIAIOSFODNN7EXAMPLE'\n"
        "    \n"
        "    # Defect 2: Insecure eval usage\n"
        "    token_eval = eval(username)\n"
        "    \n"
        "    # Defect 3: SQL injection risk\n"
        "    query = f\"SELECT * FROM users WHERE name = '{username}'\"\n"
        "    return token_eval\n"
    )
    clean_py.write_text(vulnerable_py_content, encoding="utf-8")

    return repo


from openreview.adapters.base import (
    ScannerAdapter,
    ScannerContext,
    ScannerResult,
    ScannerStatus,
)
from openreview.schemas.models import Finding, FindingOrigin, Severity


class SeededVulnerabilityScanner(ScannerAdapter):
    """Deterministic security scanner for seeded vulnerability evaluation."""

    @property
    def name(self) -> str:
        return "seeded_security_scanner"

    @property
    def supported_extensions(self) -> set[str]:
        return {".py"}

    def is_available(self) -> bool:
        return True

    def filter_target_files(self, files: list[str]) -> list[str]:
        return [f for f in files if f.endswith(".py")]

    def run(self, context: ScannerContext) -> ScannerResult:
        findings: list[Finding] = []
        for rel_path in context.target_files:
            file_path = context.repo_root / rel_path
            if file_path.is_file():
                content = file_path.read_text(encoding="utf-8")
                for idx, line in enumerate(content.splitlines(), 1):
                    if "eval(" in line:
                        findings.append(
                            Finding(
                                title="Dangerous use of eval()",
                                explanation="eval() allows arbitrary code execution",
                                path=rel_path,
                                severity=Severity.CRITICAL,
                                origin=FindingOrigin.STATIC,
                                start_line=idx,
                                end_line=idx,
                                rule_id="SEC001",
                            )
                        )
                    if "AKIA" in line:
                        findings.append(
                            Finding(
                                title="Hardcoded AWS Access Key",
                                explanation="AWS access key exposed in source code",
                                path=rel_path,
                                severity=Severity.HIGH,
                                origin=FindingOrigin.STATIC,
                                start_line=idx,
                                end_line=idx,
                                rule_id="SEC002",
                            )
                        )
        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=0.01,
            findings=findings,
        )


def test_seeded_defects_detection_e2e(seeded_defect_repo: Path) -> None:
    """Verify that ReviewEngine detects seeded defects and produces valid reports."""
    cfg = OpenReviewConfig(
        scanners=ScannersConfig(enabled=["seeded_security_scanner"]),
        ai=AIConfig(enabled=False),
    )

    scanner = SeededVulnerabilityScanner()
    engine = ReviewEngine(repo_root=seeded_defect_repo, config=cfg, scanners=[scanner])
    report = engine.execute_review()

    # Verify report metrics
    assert report.summary.total_files_analyzed >= 1
    assert report.summary.total_findings >= 2
    assert report.summary.findings_by_severity.get("critical") == 1
    assert report.summary.findings_by_severity.get("high") == 1

    # Render reports to ensure no rendering errors
    md = render_markdown_report(report)
    json_str = render_json_report(report)
    validate_json_report_payload(json_str)

    assert "auth.py" in md
    assert "Dangerous use of eval()" in md
    assert "Hardcoded AWS Access Key" in md
    assert report.repo_root == str(seeded_defect_repo)


def test_seeded_defects_cli_exit_code(seeded_defect_repo: Path, capsys) -> None:
    """Verify CLI returns EXIT_FINDINGS_PRESENT or EXIT_SUCCESS on repo diff."""
    exit_code = main(["review", "--path", str(seeded_defect_repo)])
    # Depending on whether ruff is installed in test runner, either findings are flagged or success
    assert exit_code in (0, EXIT_FINDINGS_PRESENT)

    captured = capsys.readouterr()
    assert "OpenReview AI v" in captured.out
    assert "Target repository:" in captured.out
