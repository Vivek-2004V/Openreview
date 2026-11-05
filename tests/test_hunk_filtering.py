"""Tests for Issue 4: Strict changed-line hunk filtering and category separation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
from openreview.adapters.eslint import ESLintAdapter
from openreview.adapters.ruff import RuffAdapter
from openreview.cli import main
from openreview.config import AIConfig, OpenReviewConfig, ScannersConfig
from openreview.exit_codes import EXIT_FINDINGS_PRESENT
from openreview.reports.json_report import validate_json_report_payload
from openreview.reports.markdown import render_markdown_report
from openreview.review.engine import ReviewEngine
from openreview.review.orchestrator import CompleteReviewOrchestrator
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    ReviewReport,
    Severity,
)


class MockScanner(ScannerAdapter):
    """Configurable mock scanner for hunk filtering tests."""

    def __init__(self, name: str, findings: list[Finding]) -> None:
        self._name = name
        self._findings = findings

    @property
    def name(self) -> str:
        return self._name

    @property
    def supported_extensions(self) -> set[str]:
        return {".py", ".js", ".ts"}

    def is_available(self) -> bool:
        return True

    def filter_target_files(self, files: list[str]) -> list[str]:
        return files

    def run(self, context: ScannerContext) -> ScannerResult:
        processed_findings = []
        for f in self._findings:
            if context.changed_lines_by_file is not None:
                norm_rel = f.path.replace("\\", "/").lstrip("./")
                file_lines = context.changed_lines_by_file.get(
                    norm_rel
                ) or context.changed_lines_by_file.get(f.path)
                is_pre = bool(
                    file_lines is not None
                    and f.start_line is not None
                    and f.start_line not in file_lines
                )
            else:
                is_pre = f.is_pre_existing

            processed_findings.append(
                Finding(
                    title=f.title,
                    explanation=f.explanation,
                    path=f.path,
                    severity=f.severity,
                    confidence=f.confidence,
                    origin=f.origin,
                    status=f.status,
                    start_line=f.start_line,
                    end_line=f.end_line,
                    rule_id=f.rule_id,
                    evidence=f.evidence,
                    recommendation=f.recommendation,
                    references=list(f.references),
                    is_pre_existing=is_pre,
                )
            )

        return ScannerResult(
            scanner_name=self.name,
            status=ScannerStatus.SUCCESS,
            duration_seconds=0.01,
            findings=processed_findings,
        )


class MockAIProvider(ModelProvider):
    """Deterministic mock AI provider returning preset suggestions."""

    def __init__(self, suggestions: list[AIReviewSuggestion]) -> None:
        self._suggestions = suggestions

    @property
    def name(self) -> str:
        return "mock_ai"

    def is_available(self) -> bool:
        return True

    def generate_suggestions(self, request: AIReviewRequest) -> AIReviewResponse:
        return AIReviewResponse(
            provider_name=self.name,
            model_name="mock-model",
            status=AIStatus.SUCCESS,
            duration_seconds=0.02,
            suggestions=list(self._suggestions),
        )


def _create_git_repo_with_pre_existing_and_new_code(repo_dir: Path) -> Path:
    """Creates a repo where line 10 has pre-existing code and line 100 has modified code."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo_dir, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    # 120-line file
    lines = [f"# Line {i}" for i in range(1, 121)]
    lines[9] = "eval('pre_existing_insecure_call')  # Line 10: pre-existing bug"
    lines[99] = "x = 100  # Line 100: clean initial code"

    target_file = repo_dir / "file.py"
    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "file.py"], cwd=repo_dir, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit with line 10 bug"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    # Modify line 100 in working tree
    lines[99] = (
        "eval('new_untrusted_input')  # Line 100: new security defect introduced in PR"
    )
    target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return repo_dir


def test_review_engine_filters_pre_existing_finding_outside_hunk(
    tmp_path: Path,
) -> None:
    """Verify that ReviewEngine strictly excludes pre-existing bugs at unchanged line 10

    from active findings, placing them in pre_existing_findings instead.
    """
    repo = _create_git_repo_with_pre_existing_and_new_code(tmp_path / "repo")

    # Scanner reports both line 10 (pre-existing) and line 100 (new)
    f_pre_existing = Finding(
        title="Pre-existing eval usage",
        explanation="eval detected on unchanged line 10",
        path="file.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=10,
        end_line=10,
        rule_id="SEC001",
    )
    f_new = Finding(
        title="New eval usage",
        explanation="eval introduced in current PR on line 100",
        path="file.py",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
        start_line=100,
        end_line=100,
        rule_id="SEC001",
    )

    mock_scanner = MockScanner("mock_linter", [f_pre_existing, f_new])
    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=False),
        scanners=ScannersConfig(enabled=["mock_linter"]),
    )

    engine = ReviewEngine(
        repo_root=repo,
        config=cfg,
        scanners=[mock_scanner],
    )

    report = engine.execute_review()

    # 1. Active findings must ONLY contain the new finding on line 100
    assert len(report.findings) == 1
    assert report.findings[0].start_line == 100
    assert report.findings[0].title == "New eval usage"
    assert report.findings[0].is_pre_existing is False

    # 2. new_findings list has exactly 1 finding
    assert len(report.new_findings) == 1
    assert report.new_findings[0].start_line == 100

    # 3. pre_existing_findings list has exactly 1 finding (line 10)
    assert len(report.pre_existing_findings) == 1
    assert report.pre_existing_findings[0].start_line == 10
    assert report.pre_existing_findings[0].title == "Pre-existing eval usage"
    assert report.pre_existing_findings[0].is_pre_existing is True

    # 4. Summary counts are accurate and separate
    assert report.summary.total_findings == 1
    assert report.summary.new_findings_count == 1
    assert report.summary.pre_existing_findings_count == 1
    assert report.summary.ai_suggestions_count == 0


def test_review_engine_categorizes_ai_suggestions_properly(tmp_path: Path) -> None:
    """Verify that AI suggestions on changed lines are categorized separately into ai_suggestions."""
    repo = _create_git_repo_with_pre_existing_and_new_code(tmp_path / "repo")

    f_new_static = Finding(
        title="Static issue",
        explanation="Static linter finding on line 100",
        path="file.py",
        severity=Severity.MEDIUM,
        origin=FindingOrigin.STATIC,
        start_line=100,
        end_line=100,
        rule_id="LINT01",
    )
    ai_sugg = AIReviewSuggestion(
        path="file.py",
        title="AI Security Insight",
        explanation="eval can be exploited with untrusted inputs",
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        start_line=100,
        end_line=100,
        recommendation="Replace eval with safe parsing",
    )

    scanner = MockScanner("mock_linter", [f_new_static])
    ai_provider = MockAIProvider([ai_sugg])

    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=True),
        scanners=ScannersConfig(enabled=["mock_linter"]),
    )

    engine = ReviewEngine(
        repo_root=repo,
        config=cfg,
        scanners=[scanner],
        ai_provider=ai_provider,
    )

    report = engine.execute_review()

    # Active findings includes both the static finding and the AI suggestion
    assert len(report.findings) == 2
    assert report.summary.total_findings == 2

    # Separate categories
    assert len(report.new_findings) == 1
    assert report.new_findings[0].origin == FindingOrigin.STATIC

    assert len(report.ai_suggestions) == 1
    assert report.ai_suggestions[0].origin == FindingOrigin.AI
    assert report.ai_suggestions[0].title == "AI Security Insight"

    assert len(report.pre_existing_findings) == 0

    assert report.summary.new_findings_count == 1
    assert report.summary.ai_suggestions_count == 1
    assert report.summary.pre_existing_findings_count == 0


def test_ruff_and_eslint_adapter_filter_helpers() -> None:
    """Verify RuffAdapter and ESLintAdapter static helper methods for hunk filtering."""
    f1 = Finding(
        title="Issue at L10",
        explanation="Bug",
        path="test.py",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
        start_line=10,
    )
    f2 = Finding(
        title="Issue at L100",
        explanation="Bug",
        path="test.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=100,
    )

    changed_lines = {99, 100, 101}

    # Ruff helper
    filtered_ruff = RuffAdapter.filter_findings_to_changed_lines(
        [f1, f2], changed_lines
    )
    assert len(filtered_ruff) == 1
    assert filtered_ruff[0].start_line == 100

    # ESLint helper
    filtered_eslint = ESLintAdapter.filter_findings_to_changed_lines(
        [f1, f2], changed_lines
    )
    assert len(filtered_eslint) == 1
    assert filtered_eslint[0].start_line == 100


def test_cli_findings_command_category_flags(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Verify that 'openreview findings --category [new|pre-existing|ai|all]' filters output properly."""
    f_new = Finding(
        title="New Issue",
        explanation="Newly added bug",
        path="app.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=50,
        is_pre_existing=False,
    )
    f_pre = Finding(
        title="Old Issue",
        explanation="Pre-existing bug",
        path="app.py",
        severity=Severity.MEDIUM,
        origin=FindingOrigin.STATIC,
        start_line=5,
        is_pre_existing=True,
    )
    f_ai = Finding(
        title="AI Issue",
        explanation="AI suggestion on new line",
        path="app.py",
        severity=Severity.LOW,
        origin=FindingOrigin.AI,
        start_line=52,
        is_pre_existing=False,
    )

    report = ReviewReport.create(
        repo_root=str(tmp_path),
        findings=[f_new, f_ai],
        total_files_analyzed=1,
        scanners_run=["mock"],
        pre_existing_findings=[f_pre],
    )

    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)

    monkeypatch.setattr(
        ReviewEngine,
        "execute_review",
        lambda self, diff_request=None, specific_files=None: report,
    )

    # 1. Category "new"
    capsys.readouterr()  # clear buffer
    code = main(
        ["findings", "--path", str(repo_dir), "--category", "new", "--format", "json"]
    )
    assert code == EXIT_FINDINGS_PRESENT
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["title"] == "New Issue"

    # 2. Category "pre-existing"
    code = main(
        [
            "findings",
            "--path",
            str(repo_dir),
            "--category",
            "pre-existing",
            "--format",
            "json",
        ]
    )
    assert code == EXIT_FINDINGS_PRESENT
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["title"] == "Old Issue"
    assert data[0]["is_pre_existing"] is True

    # 3. Category "ai"
    code = main(
        ["findings", "--path", str(repo_dir), "--category", "ai", "--format", "json"]
    )
    assert code == EXIT_FINDINGS_PRESENT
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["title"] == "AI Issue"
    assert data[0]["origin"] == "ai"

    # 4. Category "all"
    code = main(
        ["findings", "--path", str(repo_dir), "--category", "all", "--format", "json"]
    )
    assert code == EXIT_FINDINGS_PRESENT
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 3


def test_markdown_and_json_report_serialization(tmp_path: Path) -> None:
    """Verify markdown report renders pre-existing section and JSON report deserializes categorized lists."""
    f_new = Finding(
        title="New Bug",
        explanation="Bad code",
        path="app.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=20,
    )
    f_pre = Finding(
        title="Old Bug",
        explanation="Ancient code",
        path="app.py",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
        start_line=2,
        is_pre_existing=True,
    )

    report = ReviewReport.create(
        repo_root=str(tmp_path),
        findings=[f_new],
        total_files_analyzed=1,
        scanners_run=["mock"],
        pre_existing_findings=[f_pre],
    )

    # Markdown format check
    md = render_markdown_report(report)
    assert "## Pre-existing Findings (1)" in md
    assert "Old Bug" in md
    assert "`app.py`:L2" in md

    # JSON validation check
    d = report.to_dict()
    assert "pre_existing_findings" in d
    assert len(d["pre_existing_findings"]) == 1
    assert d["summary"]["pre_existing_findings_count"] == 1

    parsed = validate_json_report_payload(d)
    assert len(parsed.pre_existing_findings) == 1
    assert parsed.pre_existing_findings[0].title == "Old Bug"
    assert parsed.summary.pre_existing_findings_count == 1


def test_shared_pipeline_consistency(tmp_path: Path) -> None:
    """Verify ReviewEngine and CompleteReviewOrchestrator produce consistent report structures."""
    repo = _create_git_repo_with_pre_existing_and_new_code(tmp_path / "repo")

    f_pre = Finding(
        title="Old Bug",
        explanation="At L10",
        path="file.py",
        severity=Severity.MEDIUM,
        origin=FindingOrigin.STATIC,
        start_line=10,
    )
    f_new = Finding(
        title="New Bug",
        explanation="At L100",
        path="file.py",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        start_line=100,
    )

    scanner = MockScanner("mock_scanner", [f_pre, f_new])
    cfg = OpenReviewConfig(
        ai=AIConfig(enabled=False),
        scanners=ScannersConfig(enabled=["mock_scanner"]),
    )

    # 1. Execute via ReviewEngine
    engine = ReviewEngine(repo_root=repo, config=cfg, scanners=[scanner])
    engine_report = engine.execute_review()

    # 2. Execute via CompleteReviewOrchestrator
    orchestrator = CompleteReviewOrchestrator(
        repo_root=repo,
        config=cfg,
        scanners=[scanner],
        include_rea=False,
    )
    orch_result = orchestrator.run_pipeline()
    orch_report = orch_result.report

    # Both must match in finding counts and categorization
    assert len(engine_report.findings) == len(orch_report.findings) == 1
    assert len(engine_report.new_findings) == len(orch_report.new_findings) == 1
    assert (
        len(engine_report.pre_existing_findings)
        == len(orch_report.pre_existing_findings)
        == 1
    )
    assert (
        engine_report.summary.new_findings_count
        == orch_report.summary.new_findings_count
        == 1
    )
    assert (
        engine_report.summary.pre_existing_findings_count
        == orch_report.summary.pre_existing_findings_count
        == 1
    )
