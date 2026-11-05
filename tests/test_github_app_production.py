"""Production GitHub App integration tests:

- Unconditional webhook HMAC signature verification
- GitHub App RS256 JWT generation and installation token lifecycle
- Persistent SQLite job queue and durable crash recovery
- Production HTTP webhook server and health probes
- Complete PR reporting orchestration (Checks API + Review comments + 422 fallback)
- CLI server subcommand
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from openreview import __version__
from openreview.cli import main
from openreview.exit_codes import EXIT_INVALID_INPUT_OR_CONFIG, EXIT_SUCCESS
from openreview.integrations.github_app_auth import (
    GitHubAppAuth,
)
from openreview.integrations.github_client import (
    GitHubClient,
    PRReportingOrchestrator,
)
from openreview.integrations.github_webhook import (
    PullRequestEventData,
    WebhookSignatureError,
    parse_and_verify_webhook_event,
    parse_webhook_event,
)
from openreview.integrations.job_queue import JobStatus, ReviewJobQueue
from openreview.integrations.webhook_server import (
    GitHubWebhookServer,
    WebhookHTTPServer,
    WebhookRequestHandler,
)
from openreview.repo.diff_parser import FileChangeType, FileDiff, LineRange, ParsedDiff
from openreview.schemas.models import Finding, FindingOrigin, ReviewReport, Severity


class DummySocket:
    """In-memory mock socket simulating HTTP network transfer without opening OS sockets."""

    def __init__(self, data: bytes) -> None:
        self._r = io.BytesIO(data)
        self._w = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        return self._r if "r" in mode else self._w

    def sendall(self, data: bytes) -> None:
        self._w.write(data)


def _simulate_http_request(
    server: WebhookHTTPServer,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    """Execute in-memory HTTP request against WebhookRequestHandler without network sockets."""
    hdrs = dict(headers or {})
    req_lines = [f"{method} {path} HTTP/1.1", "Host: localhost"]
    if body and "Content-Length" not in hdrs:
        hdrs["Content-Length"] = str(len(body))
    for k, v in hdrs.items():
        req_lines.append(f"{k}: {v}")
    req_data = "\r\n".join(req_lines).encode("utf-8") + b"\r\n\r\n" + body

    sock = DummySocket(req_data)
    WebhookRequestHandler(sock, ("127.0.0.1", 8000), server)

    resp_bytes = sock._w.getvalue()
    parts = resp_bytes.split(b"\r\n\r\n", 1)
    header_block = parts[0].decode("utf-8", errors="replace")
    resp_body = parts[1] if len(parts) > 1 else b""

    status_line = header_block.splitlines()[0]
    status_code = int(status_line.split(" ")[1])

    resp_headers = {}
    for line in header_block.splitlines()[1:]:
        if ": " in line:
            hk, hv = line.split(": ", 1)
            resp_headers[hk.lower()] = hv

    return status_code, resp_headers, resp_body


@pytest.fixture
def rsa_key_pem(tmp_path: Path) -> tuple[Path, bytes]:
    """Generate a temporary RSA 2048 private key in PEM format."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "app_private_key.pem"
    key_file.write_bytes(pem_bytes)
    return key_file, pem_bytes


def _sign_payload(secret: str, body: bytes) -> str:
    """Helper to compute valid GitHub X-Hub-Signature-256 header."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ==============================================================================
# 1. Unconditional Webhook Signature Verification
# ==============================================================================


def test_webhook_unconditional_signature_verification_enforced() -> None:
    """Ensure production webhook parsing enforces signature verification unconditionally."""
    body = b'{"action": "opened"}'
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "deliv-123",
    }

    # Calling without secret must raise WebhookSignatureError
    with pytest.raises(WebhookSignatureError, match="Webhook secret must be provided"):
        parse_webhook_event(headers, body, secret=None, allow_unauthenticated=False)

    # Calling with empty secret must raise WebhookSignatureError
    with pytest.raises(WebhookSignatureError, match="Webhook secret must be provided"):
        parse_webhook_event(headers, body, secret="   ", allow_unauthenticated=False)

    # Calling with secret but missing header must raise WebhookSignatureError
    with pytest.raises(
        WebhookSignatureError, match="Invalid or missing X-Hub-Signature-256"
    ):
        parse_webhook_event(headers, body, secret="my_secret")

    # Calling parse_and_verify_webhook_event with empty secret
    with pytest.raises(WebhookSignatureError, match="Webhook secret must be non-empty"):
        parse_and_verify_webhook_event(headers, body, secret="")


def test_webhook_signature_verification_succeeds_with_valid_signature() -> None:
    """Verify that a properly signed payload parses successfully."""
    secret = "production_secret_32bytes_value_here"
    payload = {
        "action": "opened",
        "number": 10,
        "pull_request": {
            "number": 10,
            "head": {"sha": "headsha123", "ref": "feat"},
            "base": {"sha": "basesha456", "ref": "main"},
        },
        "repository": {
            "full_name": "acme/repo",
            "clone_url": "https://github.com/acme/repo.git",
        },
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = _sign_payload(secret, body_bytes)
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-prod-001",
        "X-Hub-Signature-256": sig,
    }

    event = parse_and_verify_webhook_event(headers, body_bytes, secret=secret)
    assert event.event_type == "pull_request"
    assert event.delivery_id == "delivery-prod-001"
    assert event.action == "opened"


# ==============================================================================
# 2. GitHub App JWT Generation & Installation Access-Token Lifecycle
# ==============================================================================


def test_github_app_jwt_generation(rsa_key_pem: tuple[Path, bytes]) -> None:
    """Verify that GitHubAppAuth generates valid RS256 JWTs with expected claims."""
    key_path, pem_bytes = rsa_key_pem
    app_auth = GitHubAppAuth(app_id="123456", private_key=key_path)

    token = app_auth.generate_jwt(expiration_seconds=300)
    assert isinstance(token, str)

    # Verify and decode with public key
    key = serialization.load_pem_private_key(pem_bytes, password=None)
    decoded = jwt.decode(token, key.public_key(), algorithms=["RS256"])

    assert decoded["iss"] == "123456"
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_installation_access_token_caching_and_refresh(
    rsa_key_pem: tuple[Path, bytes],
) -> None:
    """Verify installation access token caching and proactive renewal."""
    key_path, _ = rsa_key_pem
    app_auth = GitHubAppAuth(app_id=7890, private_key=key_path)

    # Mock GitHub /app/installations/{inst_id}/access_tokens response
    mock_token_data = {
        "token": "ghs_test_installation_token_abc123",
        "expires_at": "2030-01-01T00:00:00Z",  # Far in future
        "permissions": {"pull_requests": "write", "checks": "write"},
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_token_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First call: exchanges JWT for token
        t1 = app_auth.get_installation_access_token(installation_id=999)
        assert t1.token == "ghs_test_installation_token_abc123"
        assert t1.is_expired is False
        assert mock_urlopen.call_count == 1

        # Second call: returns cached token without hitting network
        t2 = app_auth.get_installation_access_token(installation_id=999)
        assert t2.token == t1.token
        assert mock_urlopen.call_count == 1

        # Force refresh: hits network again
        t3 = app_auth.get_installation_access_token(
            installation_id=999, force_refresh=True
        )
        assert t3.token == t1.token
        assert mock_urlopen.call_count == 2

        # Factory method get_installation_client
        client = app_auth.get_installation_client(installation_id=999)
        assert isinstance(client, GitHubClient)
        assert client.token == "ghs_test_installation_token_abc123"


# ==============================================================================
# 3. Persistent SQLite Job Queue & Durable Crash Recovery
# ==============================================================================


def test_persistent_sqlite_job_queue_and_crash_recovery(tmp_path: Path) -> None:
    """Verify that ReviewJobQueue persists jobs across restarts and recovers orphaned processing jobs."""
    db_file = tmp_path / "jobs.db"

    # Session 1: Create queue and add job
    q1 = ReviewJobQueue(db_path=db_file)
    assert q1.is_persistent is True

    pr_data = PullRequestEventData(
        action="opened",
        pr_number=5,
        repo_full_name="org/persistent-repo",
        clone_url="https://github.com/org/persistent-repo.git",
        head_sha="head_commit_111",
        base_sha="base_commit_000",
        head_ref="feature",
        base_ref="main",
        installation_id=5555,
    )

    _job1, is_new1 = q1.enqueue_pr_event(pr_data, delivery_id="deliv-sqlite-1")
    assert is_new1 is True
    assert q1.queue_size == 1

    # Claim job (transitions to PROCESSING)
    claimed = q1.claim_next_job()
    assert claimed is not None
    assert claimed.status == JobStatus.PROCESSING
    assert claimed.attempts == 1
    assert q1.queue_size == 0

    # Simulate process crash without completing the job!
    q1.close()

    # Session 2: Process restarts. Initialize queue from the same SQLite file.
    q2 = ReviewJobQueue(db_path=db_file, auto_recover=True)

    # Durable recovery must have restored the crashed PROCESSING job back to PENDING!
    recovered_job = q2.get_job(claimed.job_id)
    assert recovered_job is not None
    assert recovered_job.status == JobStatus.PENDING
    assert recovered_job.attempts == 1  # attempts count preserved
    assert q2.queue_size == 1

    # Claim and complete job
    claimed_again = q2.claim_next_job()
    assert claimed_again is not None
    assert claimed_again.status == JobStatus.PROCESSING
    assert claimed_again.attempts == 2

    q2.complete_job(claimed_again.job_id, report_summary={"total_findings": 0})
    completed = q2.get_job(claimed_again.job_id)
    assert completed is not None
    assert completed.status == JobStatus.COMPLETED

    q2.close()


def test_durable_recovery_marks_failed_when_max_retries_exceeded(
    tmp_path: Path,
) -> None:
    """Verify that durable recovery marks jobs as FAILED if crash attempts exceed max_retries."""
    db_file = tmp_path / "jobs_retries.db"
    q = ReviewJobQueue(db_path=db_file)

    pr_data = PullRequestEventData(
        action="synchronize",
        pr_number=7,
        repo_full_name="org/flaky-repo",
        clone_url="https://github.com/org/flaky-repo.git",
        head_sha="head_flaky",
        base_sha="base_flaky",
        head_ref="branch",
        base_ref="main",
    )
    job, _ = q.enqueue_pr_event(pr_data, delivery_id="deliv-flaky-1")

    # Manually set attempts to 3 and status to PROCESSING in SQLite
    cur = q._conn.cursor()
    cur.execute(
        "UPDATE jobs SET status = 'processing', attempts = 3 WHERE job_id = ?;",
        (job.job_id,),
    )
    q.close()

    # Restart with recovery
    q_restarted = ReviewJobQueue(db_path=db_file, auto_recover=True)
    job_recovered = q_restarted.get_job(job.job_id)
    assert job_recovered is not None
    assert job_recovered.status == JobStatus.FAILED
    assert "max retries exceeded" in (job_recovered.error_message or "")
    q_restarted.close()


# ==============================================================================
# 4. Production HTTP Webhook Server & Health Probes
# ==============================================================================


def test_http_webhook_server_health_and_rejection_probes(tmp_path: Path) -> None:
    """Verify that WebhookRequestHandler serves health checks, rejects unauthenticated calls, and processes webhooks."""
    secret = "webhook_secret_key_prod_test"
    db_path = tmp_path / "server_jobs.db"
    queue = ReviewJobQueue(db_path=db_path)

    # Server instance
    server = GitHubWebhookServer(
        queue=queue,
        webhook_secret=secret,
        host="127.0.0.1",
        port=8000,
    )

    # 1. GET /health
    code, _, body = _simulate_http_request(server._server, "GET", "/health")
    assert code == 200
    health_data = json.loads(body.decode("utf-8"))
    assert health_data["status"] == "healthy"
    assert health_data["version"] == __version__
    assert health_data["storage"] == "persistent"

    # 2. GET /status
    code, _, body = _simulate_http_request(server._server, "GET", "/status")
    assert code == 200
    status_data = json.loads(body.decode("utf-8"))
    assert "total_jobs" in status_data
    assert "queue_size" in status_data

    # 3. POST /webhook without signature -> 401 Unauthorized
    code, _, body = _simulate_http_request(
        server._server,
        "POST",
        "/webhook",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d-1"},
        body=b'{"action": "opened"}',
    )
    assert code == 401
    assert "Unauthorized" in body.decode("utf-8")

    # 4. POST /webhook with ping event and valid signature -> 200 OK
    ping_body = json.dumps({"zen": "Keep it logically awesome."}).encode("utf-8")
    ping_sig = _sign_payload(secret, ping_body)
    code, _, body = _simulate_http_request(
        server._server,
        "POST",
        "/webhook",
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "ping-deliv-001",
            "X-Hub-Signature-256": ping_sig,
        },
        body=ping_body,
    )
    assert code == 200
    ping_resp = json.loads(body.decode("utf-8"))
    assert ping_resp["status"] == "pong"

    # 5. POST /webhook with pull_request event -> 202 Accepted
    pr_payload = {
        "action": "opened",
        "number": 101,
        "pull_request": {
            "number": 101,
            "head": {"sha": "headsha999", "ref": "feat/api"},
            "base": {"sha": "basesha888", "ref": "main"},
        },
        "repository": {
            "full_name": "corp/backend",
            "clone_url": "https://github.com/corp/backend.git",
        },
        "installation": {"id": 12345},
    }
    pr_bytes = json.dumps(pr_payload).encode("utf-8")
    pr_sig = _sign_payload(secret, pr_bytes)
    code, _, body = _simulate_http_request(
        server._server,
        "POST",
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "pr-deliv-101",
            "X-Hub-Signature-256": pr_sig,
        },
        body=pr_bytes,
    )
    assert code == 202
    pr_resp = json.loads(body.decode("utf-8"))
    assert pr_resp["status"] == "queued"
    assert "job_id" in pr_resp

    # 6. POST duplicate delivery -> 200 OK duplicate_skipped
    code, _, body = _simulate_http_request(
        server._server,
        "POST",
        "/webhook",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "pr-deliv-101",
            "X-Hub-Signature-256": pr_sig,
        },
        body=pr_bytes,
    )
    assert code == 200
    dup_resp = json.loads(body.decode("utf-8"))
    assert dup_resp["status"] == "duplicate_skipped"

    # 7. GET /jobs/{job_id}
    job_id = pr_resp["job_id"]
    code, _, body = _simulate_http_request(server._server, "GET", f"/jobs/{job_id}")
    assert code == 200
    job_data = json.loads(body.decode("utf-8"))
    assert job_data["job_id"] == job_id
    assert job_data["repo_full_name"] == "corp/backend"


# ==============================================================================
# 5. Complete PR Reporting Orchestration (Reviews + Checks + 422 Fallback)
# ==============================================================================


def test_pr_reporting_orchestrator_success() -> None:
    """Verify that PRReportingOrchestrator creates Checks API run and posts PR review."""
    client = MagicMock(spec=GitHubClient)
    client.create_pr_review.return_value = {"id": 1, "state": "CHANGES_REQUESTED"}
    client.create_check_run.return_value = {"id": 100, "status": "completed"}

    orchestrator = PRReportingOrchestrator(client)

    f_crit = Finding(
        title="SQL Injection",
        explanation="Untrusted input passed to raw SQL query",
        path="src/db.py",
        severity=Severity.CRITICAL,
        origin=FindingOrigin.STATIC,
        start_line=15,
        end_line=15,
    )
    report = ReviewReport.create(
        repo_root="/repo",
        findings=[f_crit],
        total_files_analyzed=1,
    )

    fdiff = FileDiff(
        path="src/db.py",
        old_path=None,
        change_type=FileChangeType.MODIFIED,
        is_binary=False,
        hunks=[],
        changed_line_ranges=[LineRange(10, 20)],
        raw_patch="",
    )
    parsed_diff = ParsedDiff(
        files=[fdiff], total_files=1, total_additions=10, total_deletions=0
    )

    result = orchestrator.report_review_result(
        repo_full_name="org/app",
        pr_number=50,
        head_sha="sha_crit_123",
        report=report,
        parsed_diff=parsed_diff,
    )

    assert result["event"] == "REQUEST_CHANGES"
    assert result["inline_comments_count"] == 1
    assert client.create_pr_review.call_count == 1
    assert client.create_check_run.call_count == 1


def test_pr_reporting_orchestrator_422_fallback() -> None:
    """Verify that if GitHub rejects inline comments with 422 Unprocessable Entity,

    the orchestrator falls back to posting review without inline comments.
    """
    client = MagicMock(spec=GitHubClient)

    # First call fails with HTTP 422
    err_422 = urllib.error.HTTPError(
        url="https://api.github.com/repos/org/app/pulls/1/reviews",
        code=422,
        msg="Unprocessable Entity",
        hdrs={},
        fp=None,
    )
    client.create_pr_review.side_effect = [err_422, {"id": 2, "state": "COMMENTED"}]
    client.create_check_run.return_value = {"id": 101}

    orchestrator = PRReportingOrchestrator(client)

    f = Finding(
        title="Code style defect",
        explanation="Line length exceeded",
        path="src/main.py",
        severity=Severity.LOW,
        origin=FindingOrigin.STATIC,
        start_line=5,
    )
    report = ReviewReport.create(
        repo_root="/repo", findings=[f], total_files_analyzed=1
    )

    fdiff = FileDiff(
        path="src/main.py",
        old_path=None,
        change_type=FileChangeType.MODIFIED,
        is_binary=False,
        hunks=[],
        changed_line_ranges=[LineRange(1, 10)],
        raw_patch="",
    )
    parsed_diff = ParsedDiff(
        files=[fdiff], total_files=1, total_additions=5, total_deletions=0
    )

    result = orchestrator.report_review_result(
        repo_full_name="org/app",
        pr_number=1,
        head_sha="head_sha_fallback",
        report=report,
        parsed_diff=parsed_diff,
    )
    assert result["event"] == "COMMENT"

    # Must have retried with empty comments list
    assert client.create_pr_review.call_count == 2
    retry_kwargs = client.create_pr_review.call_args_list[1][1]
    assert retry_kwargs["comments"] == []


# ==============================================================================
# 6. CLI Server Subcommand
# ==============================================================================


def test_cli_server_subcommand_missing_secret(capsys) -> None:
    """Verify that 'openreview server' fails with clear error if webhook secret is missing."""
    with patch.dict("os.environ", {}, clear=True):
        code = main(["server", "--port", "9999"])
        assert code == EXIT_INVALID_INPUT_OR_CONFIG

    captured = capsys.readouterr()
    assert "Webhook secret must be provided" in captured.err


def test_cli_server_subcommand_help_flag(capsys) -> None:
    """Verify that 'openreview server --help' lists all expected flags."""
    code = main(["server", "--help"])
    assert code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "openreview server" in captured.out
    assert "--secret" in captured.out
    assert "--port" in captured.out
    assert "--db" in captured.out
    assert "--app-id" in captured.out
    assert "--private-key" in captured.out
