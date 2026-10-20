"""Unit tests for documented exit codes contract."""

import pytest
from openreview import (
    ExitCode,
    EXIT_SUCCESS,
    EXIT_FINDINGS_PRESENT,
    EXIT_INVALID_INPUT_OR_CONFIG,
    EXIT_TOOL_FAILURE,
    EXIT_INTERNAL_ERROR,
)
from openreview.exit_codes import format_exit_codes_summary


def test_exit_codes_values():
    """Verify numeric values match TRD Section 7 contract."""
    assert ExitCode.SUCCESS == 0
    assert ExitCode.FINDINGS_PRESENT == 1
    assert ExitCode.INVALID_INPUT_OR_CONFIG == 2
    assert ExitCode.TOOL_FAILURE == 3
    assert ExitCode.INTERNAL_ERROR == 4

    assert EXIT_SUCCESS == 0
    assert EXIT_FINDINGS_PRESENT == 1
    assert EXIT_INVALID_INPUT_OR_CONFIG == 2
    assert EXIT_TOOL_FAILURE == 3
    assert EXIT_INTERNAL_ERROR == 4


def test_exit_codes_descriptions():
    """Verify every exit code provides a non-empty, actionable description."""
    for code in ExitCode:
        desc = code.description
        assert isinstance(desc, str)
        assert len(desc) > 10


def test_format_exit_codes_summary():
    """Verify summary string formats all exit codes cleanly."""
    summary = format_exit_codes_summary()
    assert "Exit Codes:" in summary
    assert "0  Review completed cleanly" in summary
    assert "1  Review completed, actionable findings present" in summary
    assert "2  Invalid configuration" in summary
    assert "3  One or more required scanner tools" in summary
    assert "4  Internal unexpected error" in summary
