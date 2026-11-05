"""Tests for structured logging and automatic secret redaction."""

from __future__ import annotations

import json
import logging

from openreview.logging import JSONLogFormatter, RedactingFilter
from openreview.security.redaction import REDACTED_PLACEHOLDER


def test_redacting_filter_on_message() -> None:
    """Ensure filter redacts secrets contained directly inside message strings."""
    filter_instance = RedactingFilter()
    record = logging.LogRecord(
        name="openreview.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Connecting using token ghp_1234567890abcdefghijklmnopqrstuvwxyz now",
        args=(),
        exc_info=None,
    )

    filter_instance.filter(record)
    assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in record.msg
    assert REDACTED_PLACEHOLDER in record.msg


def test_redacting_filter_on_args() -> None:
    """Ensure filter redacts secrets passed as formatting arguments."""
    filter_instance = RedactingFilter()
    secret_key = "AKIA1234567890EXAMPLE"
    record = logging.LogRecord(
        name="openreview.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Using AWS key: %s",
        args=(secret_key,),
        exc_info=None,
    )

    filter_instance.filter(record)
    formatted = record.getMessage()
    assert secret_key not in formatted
    assert REDACTED_PLACEHOLDER in formatted


def test_json_log_formatter() -> None:
    """Ensure JSONLogFormatter emits valid JSON with timestamp, level, and message."""
    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="openreview.cli",
        level=logging.WARNING,
        pathname="cli.py",
        lineno=42,
        msg="Scanner timeout occurred",
        args=(),
        exc_info=None,
    )

    out = formatter.format(record)
    parsed = json.loads(out)
    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "openreview.cli"
    assert parsed["message"] == "Scanner timeout occurred"
    assert "timestamp" in parsed
