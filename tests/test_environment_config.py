"""Comprehensive test suite for environment configuration loading, validation, and Docker sandbox enforcement."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openreview.adapters.ollama import is_loopback_endpoint
from openreview.config import (
    ConfigError,
    OpenReviewConfig,
    load_config_from_env,
    load_dotenv,
    parse_dotenv,
)
from openreview.integrations.github_webhook import verify_github_signature
from openreview.integrations.job_queue import ReviewJobQueue
from openreview.integrations.sandbox import (
    DockerSandboxRunner,
    SandboxSecurityError,
)
from openreview.integrations.worker import ReviewWorker
from openreview.patch.sandbox import SandboxPatchTester


def test_parse_dotenv_syntax():
    """Verify parsing of standard .env content, comments, and quoted strings."""
    content = """
    # Comment line
    OPENREVIEW_SERVER_PORT=9000
    OPENREVIEW_SERVER_HOST="127.0.0.1"
    GITHUB_APP_ID='987654'
    EMPTY_LINE=
    """
    parsed = parse_dotenv(content)
    assert parsed["OPENREVIEW_SERVER_PORT"] == "9000"
    assert parsed["OPENREVIEW_SERVER_HOST"] == "127.0.0.1"
    assert parsed["GITHUB_APP_ID"] == "987654"
    assert parsed["EMPTY_LINE"] == ""


def test_load_dotenv_file(tmp_path: Path):
    """Verify loading from .env file into environment."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TEST_OPENREVIEW_CUSTOM_VAR=loaded_from_test_env\n",
        encoding="utf-8",
    )
    loaded = load_dotenv(path=env_file, override=True)
    assert loaded["TEST_OPENREVIEW_CUSTOM_VAR"] == "loaded_from_test_env"
    assert os.environ.get("TEST_OPENREVIEW_CUSTOM_VAR") == "loaded_from_test_env"
    del os.environ["TEST_OPENREVIEW_CUSTOM_VAR"]


def test_load_config_from_env_all_implemented_variables():
    """Verify that all documented environment variables are consumed into config."""
    custom_env = {
        "OPENREVIEW_SERVER_HOST": "192.168.1.100",
        "OPENREVIEW_SERVER_PORT": "8080",
        "OPENREVIEW_JOBS_DB": "/custom/path/jobs.db",
        "OPENREVIEW_LOG_LEVEL": "DEBUG",
        "GITHUB_WEBHOOK_SECRET": "test_wh_secret",
        "GITHUB_APP_ID": "443322",
        "OPENREVIEW_AI_ENABLED": "true",
        "OPENREVIEW_AI_PROVIDER": "ollama",
        "OPENREVIEW_AI_MODEL": "codellama:13b",
        "OPENREVIEW_AI_ENDPOINT": "http://ollama:11434",
        "OPENREVIEW_REQUIRE_CONTAINER": "false",
        "OPENREVIEW_SANDBOX_TIMEOUT_SECONDS": "90",
        "OPENREVIEW_SANDBOX_MEMORY_LIMIT": "1g",
        "OPENREVIEW_SANDBOX_CPUS": "2.5",
        "OPENREVIEW_SANDBOX_PIDS_LIMIT": "200",
        "OPENREVIEW_CONTAINER_IMAGE": "my-scanner:v1",
        "OPENREVIEW_VALIDATOR_IMAGE": "my-validator:v1",
        "OPENREVIEW_PRIVACY_REDACT_SECRETS": "false",
        "OPENREVIEW_PRIVACY_ENFORCE_LOCAL_AI": "true",
    }

    cfg = load_config_from_env(env=custom_env)

    # Server settings
    assert cfg.server.host == "192.168.1.100"
    assert cfg.server.port == 8080
    assert cfg.server.jobs_db == "/custom/path/jobs.db"
    assert cfg.server.log_level == "DEBUG"
    assert cfg.server.webhook_secret == "test_wh_secret"
    assert cfg.server.app_id == "443322"

    # AI settings
    assert cfg.ai.enabled is True
    assert cfg.ai.provider == "ollama"
    assert cfg.ai.model == "codellama:13b"
    assert cfg.ai.endpoint == "http://ollama:11434"

    # Sandbox settings
    assert cfg.sandbox.require_container is False
    assert cfg.sandbox.timeout_seconds == 90
    assert cfg.sandbox.memory_limit == "1g"
    assert cfg.sandbox.cpus == 2.5
    assert cfg.sandbox.pids_limit == 200
    assert cfg.sandbox.container_image == "my-scanner:v1"
    assert cfg.sandbox.validator_image == "my-validator:v1"

    # Privacy settings
    assert cfg.privacy.redact_secrets is False
    assert cfg.privacy.enforce_local_ai is True


def test_reject_invalid_port():
    """Verify configuration validation rejects out-of-range port numbers."""
    with pytest.raises(ConfigError, match="Server port must be between 1 and 65535"):
        load_config_from_env(env={"OPENREVIEW_SERVER_PORT": "70000"})

    with pytest.raises(ConfigError, match="Server port must be between 1 and 65535"):
        load_config_from_env(env={"OPENREVIEW_SERVER_PORT": "0"})

    with pytest.raises(ConfigError, match="OPENREVIEW_SERVER_PORT must be an integer"):
        load_config_from_env(env={"OPENREVIEW_SERVER_PORT": "not-a-number"})


def test_reject_invalid_memory_format():
    """Verify configuration validation rejects invalid memory limit strings."""
    with pytest.raises(ConfigError, match="Invalid memory limit format"):
        load_config_from_env(env={"OPENREVIEW_SANDBOX_MEMORY_LIMIT": "unlimited"})

    with pytest.raises(ConfigError, match="Invalid memory limit format"):
        load_config_from_env(env={"OPENREVIEW_SANDBOX_MEMORY_LIMIT": "-512m"})


def test_reject_invalid_timeout_and_cpus():
    """Verify configuration validation rejects non-positive timeouts and cpus."""
    with pytest.raises(ConfigError, match="timeout_seconds must be greater than 0"):
        load_config_from_env(env={"OPENREVIEW_SANDBOX_TIMEOUT_SECONDS": "0"})

    with pytest.raises(ConfigError, match="cpus must be greater than 0.0"):
        load_config_from_env(env={"OPENREVIEW_SANDBOX_CPUS": "0.0"})


def test_missing_private_key_path_rejected():
    """Verify error when GITHUB_PRIVATE_KEY_PATH refers to a non-existent file."""
    with pytest.raises(ConfigError, match="GITHUB_PRIVATE_KEY_PATH does not exist"):
        load_config_from_env(
            env={"GITHUB_PRIVATE_KEY_PATH": "/non_existent/path/to/key.pem"}
        )


def test_docker_enforcement_fail_closed_when_docker_missing():
    """Verify that OPENREVIEW_REQUIRE_CONTAINER=true fails closed if Docker binary is missing."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(
            ConfigError, match="OPENREVIEW_REQUIRE_CONTAINER=true is set, but 'docker' executable is not found"
        ):
            load_config_from_env(env={"OPENREVIEW_REQUIRE_CONTAINER": "true"})


def test_docker_enforcement_fail_closed_when_daemon_unreachable():
    """Verify that OPENREVIEW_REQUIRE_CONTAINER=true fails closed if Docker daemon is not running."""
    mock_run = MagicMock()
    mock_run.returncode = 1
    with (
        patch("shutil.which", return_value="/usr/local/bin/docker"),
        patch("subprocess.run", return_value=mock_run),
    ):
        with pytest.raises(
            ConfigError, match="Docker daemon is not reachable"
        ):
            load_config_from_env(env={"OPENREVIEW_REQUIRE_CONTAINER": "true"})


def test_review_worker_refuses_host_fallback_when_require_container():
    """Verify ReviewWorker strictly refuses host execution if container requirement is active."""
    queue = ReviewJobQueue()
    cfg = OpenReviewConfig()

    with (
        patch("shutil.which", return_value=None),
        pytest.raises(
            SandboxSecurityError,
            match="Real container sandbox is required for ReviewWorker",
        ),
    ):
        ReviewWorker(
            queue=queue,
            config=cfg,
            require_container=True,
        )


def test_patch_tester_refuses_host_execution_when_docker_required(tmp_path: Path):
    """Verify SandboxPatchTester refuses host execution when require_docker is enabled."""
    from openreview.patch.models import PatchProposal

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    target_file = repo_dir / "app.py"
    target_file.write_text("x = 1\n", encoding="utf-8")

    proposal = PatchProposal(
        patch_id="p1",
        target_file="app.py",
        title="Fix bug",
        description="Fix x",
        original_snippet="x = 1",
        replacement_snippet="x = 2",
        unified_diff="--- app.py\n+++ app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
    )

    with patch.object(DockerSandboxRunner, "is_available", return_value=False):
        res = SandboxPatchTester.test_patch_in_sandbox(
            repo_root=repo_dir,
            proposal=proposal,
            require_docker=True,
        )
        assert res.applies_cleanly is True
        assert res.test_passed is False
        assert "Refusing to execute untrusted code on host" in (res.error_message or "")


def test_ollama_docker_networking_endpoints():
    """Verify that Docker networking endpoints (host.docker.internal, ollama) are recognized."""
    assert is_loopback_endpoint("http://host.docker.internal:11434") is True
    assert is_loopback_endpoint("http://ollama:11434") is True
    assert is_loopback_endpoint("http://127.0.0.1:11434") is True
    assert is_loopback_endpoint("http://localhost:11434") is True
    # External endpoints must still be identified as non-loopback
    assert is_loopback_endpoint("https://api.openai.com/v1") is False
    assert is_loopback_endpoint("http://external-ai.corp:11434") is False


def test_webhook_signature_verification_cases():
    """Verify HMAC-SHA256 signature verification accepts valid and rejects invalid signatures."""
    payload = b'{"action": "opened"}'
    secret = "test_super_secret"

    import hashlib
    import hmac

    valid_sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_github_signature(payload, valid_sig, secret) is True
    assert verify_github_signature(payload, "sha256=invalidhex000", secret) is False
    assert verify_github_signature(payload, None, secret) is False
    assert verify_github_signature(payload, valid_sig, "") is False


def test_secret_redaction_in_logger():
    """Verify that secret tokens and keys are never printed in plain text in logs."""
    from openreview.logging import RedactingFilter

    redacting_filter = RedactingFilter()
    secret_pat = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    record = logging.LogRecord(
        name="openreview.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg=f"Connecting with GITHUB_PAT={secret_pat} now",
        args=(),
        exc_info=None,
    )
    redacting_filter.filter(record)
    assert secret_pat not in record.msg
    assert "[REDACTED" in record.msg
