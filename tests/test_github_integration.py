"""Tests for GitHub integration: webhooks, queue idempotency, review formatters, and inline mapping."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from openreview.config import OpenReviewConfig
from openreview.integrations.github_client import (
    determine_github_review_event,
    format_check_run_payload,
    format_github_review_body,
)
from openreview.integrations.github_webhook import (
    PullRequestEventData,
    WebhookPayloadError,
    WebhookSignatureError,
    extract_pr_event_data,
    parse_webhook_event,
    verify_github_signature,
)
from openreview.integrations.inline_comments import (
    format_inline_comment_body,
    map_findings_to_inline_comments,
)
from openreview.integrations.job_queue import JobStatus, ReviewJobQueue
from openreview.integrations.worker import ReviewWorker
from openreview.repo.diff_parser import FileChangeType, FileDiff, LineRange, ParsedDiff
from openreview.schemas.models import Finding, FindingOrigin, ReviewReport, Severity


def compute_test_sig(secret: str, body: bytes) -> str:
    """Helper to compute valid GitHub webhook signature header."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_signature_success() -> None:
    """Ensure valid HMAC SHA-256 signature passes verification."""
    secret = "super_secret_webhook_key_123"
    body = b'{"action": "opened", "number": 1}'
    sig = compute_test_sig(secret, body)

    assert verify_github_signature(body, sig, secret) is True


def test_verify_github_signature_invalid() -> None:
    """Ensure modified payload or bad secret fails verification."""
    secret = "secret_one"
    body = b'{"action": "opened"}'
    sig = compute_test_sig(secret, body)

    # Tampered body
    assert verify_github_signature(b'{"action": "tampered"}', sig, secret) is False
    # Bad secret
    assert verify_github_signature(body, sig, "wrong_secret") is False
    # Malformed header
    assert verify_github_signature(body, "bad_format", secret) is False
    assert verify_github_signature(body, None, secret) is False


def test_parse_webhook_event_and_pr_extraction() -> None:
    """Ensure webhook parser extracts pull_request event data correctly."""
    secret = "test_key"
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "head": {"sha": "head12345", "ref": "feat/login"},
            "base": {"sha": "base67890", "ref": "main"},
        },
        "repository": {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git",
        },
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = compute_test_sig(secret, body_bytes)

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-uuid-999",
        "X-Hub-Signature-256": sig,
    }

    event = parse_webhook_event(headers, body_bytes, secret=secret)
    assert event.event_type == "pull_request"
    assert event.delivery_id == "delivery-uuid-999"

    pr_data = extract_pr_event_data(event)
    assert pr_data.pr_number == 42
    assert pr_data.head_sha == "head12345"
    assert pr_data.base_sha == "base67890"
    assert pr_data.is_reviewable_action is True


def test_parse_webhook_event_signature_error() -> None:
    """Ensure invalid signature raises WebhookSignatureError."""
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "deliv-1",
        "X-Hub-Signature-256": "sha256=invalidhex",
    }
    with pytest.raises(WebhookSignatureError):
        parse_webhook_event(headers, b"{}", secret="expected_secret")


def test_job_queue_idempotency_and_deduplication() -> None:
    """Verify that duplicate webhook deliveries or identical PR commits are deduplicated."""
    queue = ReviewJobQueue()
    pr_data = PullRequestEventData(
        action="synchronize",
        pr_number=10,
        repo_full_name="acme/web",
        clone_url="https://github.com/acme/web.git",
        head_sha="commit_sha_abc",
        base_sha="commit_sha_base",
        head_ref="branch",
        base_ref="main",
    )

    # First enqueue
    job1, is_new1 = queue.enqueue_pr_event(pr_data, delivery_id="delivery-001")
    assert is_new1 is True
    assert job1.status == JobStatus.PENDING
    assert queue.queue_size == 1

    # Second enqueue with same delivery_id
    job2, is_new2 = queue.enqueue_pr_event(pr_data, delivery_id="delivery-001")
    assert is_new2 is False
    assert job2.job_id == job1.job_id
    assert queue.queue_size == 1

    # Third enqueue with different delivery_id but same revision commit
    job3, is_new3 = queue.enqueue_pr_event(pr_data, delivery_id="delivery-002")
    assert is_new3 is False
    assert queue.queue_size == 1

    # Claim job
    claimed = queue.claim_next_job()
    assert claimed is not None
    assert claimed.status == JobStatus.PROCESSING
    assert queue.queue_size == 0

    # Complete job
    queue.complete_job(claimed.job_id, {"total_findings": 0})
    completed = queue.get_job(claimed.job_id)
    assert completed is not None
    assert completed.status == JobStatus.COMPLETED


def test_github_review_event_and_body_formatting() -> None:
    """Verify that review events (APPROVE, REQUEST_CHANGES) and markdown summaries match findings."""
    # Clean report -> APPROVE
    clean_report = ReviewReport.create(
        repo_root="/repo",
        findings=[],
        total_files_analyzed=5,
    )
    assert determine_github_review_event(clean_report) == "APPROVE"
    clean_body = format_github_review_body(clean_report)
    assert "Review Passed" in clean_body

    # Critical finding -> REQUEST_CHANGES
    crit_finding = Finding(
        title="Remote Code Execution",
        explanation="eval on input",
        path="src/api.py",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
        start_line=10,
    )
    crit_report = ReviewReport.create(
        repo_root="/repo",
        findings=[crit_finding],
        total_files_analyzed=1,
    )
    assert determine_github_review_event(crit_report) == "REQUEST_CHANGES"
    crit_body = format_github_review_body(crit_report)
    assert "Action Required" in crit_body
    assert "Remote Code Execution" in crit_body

    # Checks API payload
    check_payload = format_check_run_payload(crit_report, head_sha="sha123")
    assert check_payload["conclusion"] == "failure"
    assert check_payload["head_sha"] == "sha123"


def test_inline_comment_mapping_within_diff_hunks() -> None:
    """Verify that findings within diff hunks are mapped to inline comments and out-of-hunk findings are separated."""
    fdiff = FileDiff(
        path="src/calc.py",
        old_path=None,
        change_type=FileChangeType.MODIFIED,
        is_binary=False,
        hunks=[],
        changed_line_ranges=[LineRange(10, 15)],
        raw_patch="",
    )
    parsed_diff = ParsedDiff(
        files=[fdiff],
        total_files=1,
        total_additions=5,
        total_deletions=0,
    )

    finding_in_diff = Finding(
        title="Zero division check missing",
        explanation="Denominator could be zero",
        path="src/calc.py",
        severity=Severity.MEDIUM,
        origin=FindingOrigin.STATIC,
        start_line=12,
        end_line=14,
    )

    finding_outside_diff = Finding(
        title="Old legacy flaw",
        explanation="Line 99 was not touched in this PR",
        path="src/calc.py",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
        start_line=99,
    )

    comments, unmappable = map_findings_to_inline_comments(
        findings=[finding_in_diff, finding_outside_diff],
        parsed_diff=parsed_diff,
    )

    assert len(comments) == 1
    assert comments[0]["path"] == "src/calc.py"
    assert comments[0]["line"] == 14
    assert comments[0]["side"] == "RIGHT"
    assert "Zero division check missing" in comments[0]["body"]

    # Line 99 is safely filtered out to prevent GitHub 422 API error
    assert len(unmappable) == 1
    assert unmappable[0].title == "Old legacy flaw"


def test_isolated_worker_processing(tmp_path: Path) -> None:
    """Ensure ReviewWorker processes job in sandbox and completes job status."""
    import subprocess

    src_repo = tmp_path / "src_repo"
    src_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=src_repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Worker"], cwd=src_repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=src_repo, capture_output=True, check=True)

    (src_repo / "index.py").write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "index.py"], cwd=src_repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "commit 1"], cwd=src_repo, capture_output=True, check=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src_repo, capture_output=True, text=True, check=True).stdout.strip()

    (src_repo / "index.py").write_text("print('v2')\n", encoding="utf-8")
    subprocess.run(["git", "add", "index.py"], cwd=src_repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "commit 2"], cwd=src_repo, capture_output=True, check=True)
    head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src_repo, capture_output=True, text=True, check=True).stdout.strip()

    queue = ReviewJobQueue()
    pr_data = PullRequestEventData(
        action="opened",
        pr_number=1,
        repo_full_name="org/test",
        clone_url=str(src_repo),
        head_sha=head_sha,
        base_sha=base_sha,
        head_ref="feat",
        base_ref="main",
    )
    job, _ = queue.enqueue_pr_event(pr_data, delivery_id="deliv-worker-test")

    def mock_fetcher(job: Any, target_dir: Path) -> None:
        subprocess.run(["git", "clone", str(src_repo), str(target_dir)], capture_output=True, check=True)

    worker = ReviewWorker(
        queue=queue,
        config=OpenReviewConfig(),
        repo_fetcher=mock_fetcher,
    )

    report = worker.process_next_job()
    assert report is not None
    assert job.status == JobStatus.COMPLETED
    assert job.report_summary is not None
