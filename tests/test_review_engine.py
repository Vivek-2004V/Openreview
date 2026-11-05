"""Integration tests for ReviewEngine and static-only execution without AI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openreview import (
    EXIT_FINDINGS_PRESENT,
    EXIT_SUCCESS,
)
from openreview.adapters.ai_base import (
    AIReviewRequest,
    AIReviewResponse,
    AIReviewSuggestion,
    AIStatus,
    ModelProvider,
)
from openreview.adapters.base import (
    ScannerAdapter,
    ScannerContext,
    ScannerResult,
    ScannerStatus,
)
from openreview.cli import main
from openreview.config import AIConfig, OpenReviewConfig, ScannersConfig
from openreview.review.engine import ReviewEngine
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    FindingStatus,
    Severity,
)


class DummyScanner(ScannerAdapter):
    """Deterministic test scanner for testing ReviewEngine."""

    def __init__(self, findings: list[Finding] | None = None) -> None:
        self._findings = findings or []

    @property
    def name(self) -> str:
        return "dummy_scanner"

    @property
    def supported_extensions(self) -> set[str]:
        return {".py"}

    def is_available(self) -> bool:
        return True

    def filter_target_files(self, files: list[str]) -> list[str]:
        return [f for f in files if f.endswith(".py")]

    def run(self, context: ScannerContext) -> ScannerResult:
        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=0.01,
            findings=list(self._findings),
        )


class DummyAIProvider(ModelProvider):
    """Deterministic test AI provider."""

    def __init__(self, available: bool = True, suggestions: list[AIReviewSuggestion] | None = None) -> None:
        self._available = available
        self._suggestions = suggestions or []

    @property
    def name(self) -> str:
        return "dummy_ai"

    def is_available(self) -> bool:
        return self._available

    def generate_suggestions(self, request: AIReviewRequest) -> AIReviewResponse:
        return AIReviewResponse(
            provider_name=self.name,
            model_name="test-model",
            status=AIStatus.SUCCESS,
            duration_seconds=0.05,
            suggestions=list(self._suggestions),
        )


@pytest.fixture
def repo_with_changed_python_file(tmp_path: Path) -> Path:
    """Fixture providing a git repository with an uncommitted modified Python file."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)

    app_file = repo / "app.py"
    app_file.write_text("def hello():\n    return 'initial'\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, capture_output=True, check=True)

    # Modify the file without committing
    app_file.write_text("def hello():\n    eval('dangerous_change')\n", encoding="utf-8")
    return repo


def test_static_only_mode_without_ai(repo_with_changed_python_file: Path) -> None:
    """Ensure that with AI disabled, review runs statically and produces only static findings."""
    static_finding = Finding(
        title="Use of dangerous eval",
        explanation="eval can execute arbitrary code",
        path="app.py",
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=2,
        end_line=2,
        rule_id="S307",
    )

    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=False),
        scanners=ScannersConfig(enabled=["dummy_scanner"]),
    )

    scanner = DummyScanner(findings=[static_finding])
    engine = ReviewEngine(
        repo_root=repo_with_changed_python_file,
        config=cfg,
        scanners=[scanner],
    )

    report = engine.execute_review()

    assert len(report.findings) == 1
    assert report.findings[0].title == "Use of dangerous eval"
    assert report.findings[0].origin == FindingOrigin.STATIC
    assert report.summary.total_findings == 1
    assert report.summary.findings_by_origin.get("static") == 1
    assert report.summary.findings_by_origin.get("ai", 0) == 0


def test_static_only_mode_when_ai_provider_is_offline(repo_with_changed_python_file: Path) -> None:
    """Ensure that if AI is enabled but the provider is unavailable, review succeeds static-only."""
    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=True),
        scanners=ScannersConfig(enabled=["dummy_scanner"]),
    )

    scanner = DummyScanner(findings=[])
    offline_ai = DummyAIProvider(available=False)

    engine = ReviewEngine(
        repo_root=repo_with_changed_python_file,
        config=cfg,
        scanners=[scanner],
        ai_provider=offline_ai,
    )

    report = engine.execute_review()

    assert report.summary.total_findings == 0
    assert len(report.findings) == 0


def test_review_with_ai_enabled_and_available(repo_with_changed_python_file: Path) -> None:
    """Ensure that when AI is enabled and available, AI suggestions are validated and merged."""
    static_finding = Finding(
        title="Static finding",
        explanation="Found by static linter",
        path="app.py",
        severity=Severity.MEDIUM,
        origin=FindingOrigin.STATIC,
        start_line=2,
    )

    ai_sugg = AIReviewSuggestion(
        path="app.py",
        title="Potential code injection",
        explanation="eval can execute attacker payloads",
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        start_line=2,
        end_line=2,
        recommendation="Refactor to avoid eval",
    )

    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=True),
        scanners=ScannersConfig(enabled=["dummy_scanner"]),
    )

    scanner = DummyScanner(findings=[static_finding])
    available_ai = DummyAIProvider(available=True, suggestions=[ai_sugg])

    engine = ReviewEngine(
        repo_root=repo_with_changed_python_file,
        config=cfg,
        scanners=[scanner],
        ai_provider=available_ai,
    )

    report = engine.execute_review()

    assert report.summary.total_findings == 2
    assert report.summary.findings_by_origin.get("static") == 1
    assert report.summary.findings_by_origin.get("ai") == 1

    # Check that AI finding is tagged properly
    ai_finding = next(f for f in report.findings if f.origin == FindingOrigin.AI)
    assert ai_finding.title == "Potential code injection"
    assert ai_finding.status == FindingStatus.UNVERIFIED


def test_cli_review_runs_end_to_end_on_repo(repo_with_changed_python_file: Path, capsys) -> None:
    """Verify that CLI review command runs end-to-end and outputs formatted report."""
    exit_code = main(["review", "--path", str(repo_with_changed_python_file)])
    assert exit_code in (EXIT_SUCCESS, EXIT_FINDINGS_PRESENT)

    captured = capsys.readouterr()
    assert "OpenReview AI v" in captured.out
    assert "Target repository:" in captured.out
    # Report contains executive summary or table
    assert "Code Review Report" in captured.out or "Review Summary" in captured.out
