"""Tests verifying the 16-node review architecture pipeline and REA adapter."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from openreview.adapters.rea import ReaAdapter
from openreview.config import OpenReviewConfig
from openreview.repo.diff_parser import FileDiff, Hunk, LineRange
from openreview.review.deduplicator import DeduplicatorAndSeverityRanker
from openreview.review.evidence_validator import EvidenceAndConfidenceValidator
from openreview.review.normalizer import FindingNormalizer
from openreview.review.orchestrator import CompleteReviewOrchestrator
from openreview.review.rules_and_history import (
    load_repository_rules_and_history,
)
from openreview.schemas.models import Finding, FindingOrigin, FindingStatus, Severity


def test_rea_adapter_filtering_and_availability():
    """Verify ReaAdapter correctly targets compiled/binary extensions."""
    adapter = ReaAdapter()
    assert adapter.name == "rea"
    assert adapter.is_available() is True

    targets = [
        "src/main.py",
        "assets/engine.wasm",
        "lib/native.node",
        "build/libcalc.so",
        "README.md",
    ]
    filtered = adapter.filter_target_files(targets)
    assert set(filtered) == {"assets/engine.wasm", "lib/native.node", "build/libcalc.so"}


def test_repository_rules_and_history_loading():
    """Verify Node H extracts rules and recent git history."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        rule_file = root / "REVIEW_GUIDELINES.md"
        rule_file.write_text("# Review Rules\nNever use eval(). Avoid hardcoded tokens.\n", encoding="utf-8")

        res = load_repository_rules_and_history(root)
        assert res.has_rules is True
        assert "REVIEW_GUIDELINES.md" in res.rules_sources
        assert "Never use eval()" in res.rules_text


def test_finding_normalizer_and_sha256_id():
    """Verify Node I normalizes findings with deterministic IDs."""
    f1 = FindingNormalizer.normalize_scanner_finding(
        scanner="ruff",
        rule_id="S105",
        path="auth.py",
        title="Hardcoded Secret Key",
        explanation="Hardcoded password detected in source",
        start_line=15,
        end_line=15,
        severity="critical",
    )
    assert f1.severity == Severity.CRITICAL
    assert f1.origin == FindingOrigin.STATIC
    assert len(f1.finding_id) == 16  # Canonical ID length


def test_evidence_validator_filters_out_of_hunk_findings():
    """Verify Node J filters out findings where line number falls outside modified hunks."""
    from openreview.repo.diff_parser import FileChangeType

    mock_diff = FileDiff(
        path="service.py",
        old_path=None,
        change_type=FileChangeType.MODIFIED,
        is_binary=False,
        hunks=[
            Hunk(
                old_start=10,
                old_length=5,
                new_start=10,
                new_length=8,
                header="@@ -10,5 +10,8 @@",
                lines=[],
            )
        ],
        changed_line_ranges=[LineRange(start=10, end=18)],
        raw_patch="",
    )

    valid_finding = Finding(
        title="Valid Finding in Hunk",
        explanation="Detailed description",
        path="service.py",
        start_line=12,
        rule_id="RULE-1",
        severity=Severity.HIGH,
        origin=FindingOrigin.STATIC,
        status=FindingStatus.OPEN,
    )

    out_of_hunk_finding = Finding(
        title="Hallucinated Finding outside Hunk",
        explanation="Detailed description",
        path="service.py",
        start_line=99,
        rule_id="RULE-2",
        severity=Severity.HIGH,
        origin=FindingOrigin.AI,
        status=FindingStatus.UNVERIFIED,
    )

    results = EvidenceAndConfidenceValidator.filter_and_validate(
        findings=[valid_finding, out_of_hunk_finding],
        reviewable_diffs=[mock_diff],
    )

    assert len(results) == 1
    assert results[0].start_line == 12


def test_deduplicator_and_severity_ranking():
    """Verify Node K deduplicates identical findings and orders by critical first."""
    f_crit = Finding(
        title="Critical Vulnerability",
        explanation="High severity buffer flaw",
        path="a.py",
        start_line=10,
        rule_id="R001",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
    )
    f_warn = Finding(
        title="Style Warning",
        explanation="Missing docstring",
        path="b.py",
        start_line=5,
        rule_id="R002",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
    )
    f_duplicate = Finding(
        title="Critical Vulnerability",
        explanation="High severity buffer flaw",
        path="a.py",
        start_line=10,
        rule_id="R001",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
    )

    ranked = DeduplicatorAndSeverityRanker.deduplicate_and_rank([f_warn, f_duplicate, f_crit])
    assert len(ranked) == 2
    assert ranked[0].severity == Severity.CRITICAL
    assert ranked[1].severity == Severity.LOW


def test_complete_review_orchestrator_execution(tmp_path):
    """Verify CompleteReviewOrchestrator runs end-to-end pipeline (Nodes A through P)."""
    # Create git repository
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), check=True)

    # Initial commit
    file1 = tmp_path / "app.py"
    file1.write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # Modify file
    file1.write_text("def hello():\n    password = 'unsafe_secret_123'\n    return 'hello'\n", encoding="utf-8")

    config = OpenReviewConfig()
    orchestrator = CompleteReviewOrchestrator(
        repo_root=tmp_path,
        config=config,
        include_rea=True,
    )

    result = orchestrator.run_pipeline()

    # Validate pipeline outputs
    assert result.repo_info is not None
    assert result.parsed_diff is not None
    assert result.report is not None
    assert isinstance(result.inline_comments, list)
    assert result.report.summary.total_files_analyzed == 1
