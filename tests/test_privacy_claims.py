"""Tests for Issue 6: Privacy claims, loopback endpoint enforcement, and redaction limitations."""

from __future__ import annotations

import logging

import pytest

from openreview.adapters.ai_base import AIReviewRequest, AIStatus, CodeContextBlock
from openreview.adapters.ollama import OllamaAdapter, is_loopback_endpoint
from openreview.config import (
    AIConfig,
    OpenReviewConfig,
    PrivacyConfig,
    load_config,
)
from openreview.review.orchestrator import CompleteReviewOrchestrator
from openreview.security.redaction import REDACTED_PLACEHOLDER, redact_text


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://127.0.0.1", True),
        ("http://127.0.2.1:11434", True),
        ("http://localhost:11434", True),
        ("http://localhost", True),
        ("http://[::1]:11434", True),
        ("http://[::1]", True),
        ("http://0.0.0.0:11434", True),
        ("127.0.0.1:11434", True),
        ("localhost:11434", True),
        ("http://remote-server:11434", False),
        ("http://ollama.internal.corp:11434", False),
        ("http://192.168.1.50:11434", False),
        ("http://10.0.0.1:11434", False),
        ("https://api.openai.com/v1", False),
        ("", False),
        ("not-a-valid-url", False),
    ],
)
def test_is_loopback_endpoint_detection(endpoint: str, expected: bool) -> None:
    """Verify loopback addresses are accurately recognized while remote and LAN hosts are rejected."""
    assert is_loopback_endpoint(endpoint) is expected


def test_ollama_adapter_defaults_to_loopback() -> None:
    """Verify default OllamaAdapter connects to loopback and flags is_loopback as True."""
    adapter = OllamaAdapter()
    assert adapter.is_loopback is True
    assert adapter.is_local is True
    assert adapter.endpoint == "http://127.0.0.1:11434"


def test_ollama_adapter_warns_on_remote_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ensure a warning is logged when OllamaAdapter is configured with a non-loopback endpoint."""
    caplog.set_level(logging.WARNING)
    remote_url = "http://remote-gpu-server.corp:11434"
    adapter = OllamaAdapter(endpoint=remote_url, enforce_loopback=False)

    assert adapter.is_loopback is False
    assert adapter.is_local is False
    assert any(
        "is not a local loopback address" in record.message
        and remote_url in record.message
        for record in caplog.records
    )


def test_ollama_adapter_enforce_loopback_rejects_remote_endpoint() -> None:
    """Verify that when enforce_loopback=True, non-loopback endpoints raise ValueError."""
    remote_url = "http://remote-server:11434"
    with pytest.raises(ValueError, match="Privacy violation: Remote AI endpoint"):
        OllamaAdapter(endpoint=remote_url, enforce_loopback=True)


def test_ollama_adapter_enforce_loopback_permits_loopback() -> None:
    """Verify that when enforce_loopback=True, valid loopback endpoints are accepted."""
    adapter = OllamaAdapter(endpoint="http://127.0.0.1:11434", enforce_loopback=True)
    assert adapter.is_loopback is True

    adapter_ipv6 = OllamaAdapter(endpoint="http://[::1]:11434", enforce_loopback=True)
    assert adapter_ipv6.is_loopback is True


def test_ollama_adapter_runtime_block_if_endpoint_modified() -> None:
    """Verify generate_suggestions returns ERROR if enforce_loopback is active on non-loopback."""
    adapter = OllamaAdapter(endpoint="http://127.0.0.1:11434", enforce_loopback=True)
    # Simulate post-init tampering or state mutation
    adapter.endpoint = "http://remote-server:11434"
    adapter._is_loopback = False

    req = AIReviewRequest(
        blocks=[
            CodeContextBlock(
                path="src/app.py", start_line=1, end_line=5, content="val = 1"
            )
        ],
        model="qwen2.5-coder:7b",
    )
    res = adapter.generate_suggestions(req)
    assert res.status == AIStatus.ERROR
    assert "Privacy violation" in (res.error_message or "")


def test_config_parsing_enforce_local_ai(tmp_path) -> None:
    """Verify parsing enforce_local_ai setting in [privacy] section."""
    config_file = tmp_path / ".openreview.toml"
    config_file.write_text(
        """
[privacy]
redact_secrets = true
enforce_local_ai = true

[ai]
enabled = true
endpoint = "http://127.0.0.1:11434"
""",
        encoding="utf-8",
    )
    cfg = load_config(config_file)
    assert cfg.privacy.enforce_local_ai is True
    assert cfg.privacy.redact_secrets is True


def test_orchestrator_enforces_local_ai_setting(tmp_path) -> None:
    """Verify orchestrator rejects remote AI endpoint when enforce_local_ai=True."""
    cfg = OpenReviewConfig(
        privacy=PrivacyConfig(enforce_local_ai=True),
        ai=AIConfig(enabled=True, endpoint="http://remote-server:11434"),
    )
    with pytest.raises(ValueError, match="Privacy violation"):
        CompleteReviewOrchestrator(repo_root=tmp_path, config=cfg)


def test_orchestrator_allows_loopback_ai_setting(tmp_path) -> None:
    """Verify orchestrator accepts loopback AI endpoint when enforce_local_ai=True."""
    cfg = OpenReviewConfig(
        privacy=PrivacyConfig(enforce_local_ai=True),
        ai=AIConfig(enabled=True, endpoint="http://127.0.0.1:11434"),
    )
    orch = CompleteReviewOrchestrator(repo_root=tmp_path, config=cfg)
    assert orch.ai_provider is not None
    assert getattr(orch.ai_provider, "is_loopback", False) is True


def test_secret_redaction_heuristics_and_limitations() -> None:
    """Demonstrate both the capability and the inherent limitation of regex redaction.

    Standard secrets are redacted, but bespoke identifiers without recognized
    structural prefixes cannot be detected, demonstrating why 100% secret-free guarantees
    are factually impossible with regexes alone.
    """
    # 1. Standard recognizable secrets: REDACTED
    standard_sample = (
        'aws_key = "AKIA1234567890ABCDEF"\n'
        'gh_token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n'
        'bearer_header = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.xyz"\n'
    )
    res_std = redact_text(standard_sample)
    assert res_std.total_redactions >= 3
    assert "AKIA1234567890ABCDEF" not in res_std.redacted_text
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in res_std.redacted_text
    assert REDACTED_PLACEHOLDER in res_std.redacted_text

    # 2. Bespoke tokens, arbitrary custom keys, or proprietary internal secrets: NOT REDACTED
    # Regex cannot detect these without a prefix or assignment keyword
    bespoke_sample = (
        'internal_hash = "7f8b9c1d2e3f4a5b6c7d8e9f0a1b2c3d"\n'
        'custom_blob = "qR4$zP9#wL2!mK8*"\n'
        'internal_service_url = "https://payroll.intranet.internal/v2/salaries"\n'
    )
    res_bespoke = redact_text(bespoke_sample)
    # The regex does not match these bespoke keys, proving why 100% secret-free guarantees are overstated
    assert "7f8b9c1d2e3f4a5b6c7d8e9f0a1b2c3d" in res_bespoke.redacted_text
    assert "qR4$zP9#wL2!mK8*" in res_bespoke.redacted_text
    assert "payroll.intranet.internal" in res_bespoke.redacted_text
