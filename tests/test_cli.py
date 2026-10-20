"""Tests for OpenReview AI CLI entry point and --help."""

import pytest
from openreview import __version__
from openreview.cli import (
    EXIT_SUCCESS,
    EXIT_INVALID_INPUT_OR_CONFIG,
    EXIT_TOOL_FAILURE,
    main,
    create_parser,
)


def test_cli_help_flag(capsys):
    """Verify openreview --help displays description, usage, options, and exit codes."""
    exit_code = main(["--help"])
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "OpenReview AI - Free-first, local-first code review assistant" in captured.out
    assert "Exit Codes:" in captured.out
    assert "0  Review completed cleanly" in captured.out
    assert "1  Review completed, actionable findings present" in captured.out
    assert "2  Invalid configuration" in captured.out


def test_cli_no_args_displays_help(capsys):
    """Verify running without arguments prints help and exits with 0."""
    exit_code = main([])
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "usage: openreview" in captured.out
    assert "Commands" in captured.out


def test_cli_version_flags(capsys):
    """Verify --version and -V output version string."""
    exit_code_long = main(["--version"])
    assert exit_code_long == EXIT_SUCCESS
    captured_long = capsys.readouterr()
    assert f"openreview {__version__}" in captured_long.out

    exit_code_short = main(["-V"])
    assert exit_code_short == EXIT_SUCCESS
    captured_short = capsys.readouterr()
    assert f"openreview {__version__}" in captured_short.out


def test_cli_review_subcommand_help(capsys):
    """Verify openreview review --help shows review options."""
    exit_code = main(["review", "--help"])
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "--base" in captured.out
    assert "--head" in captured.out
    assert "--scanners" in captured.out
    assert "--ai" in captured.out
    assert "--model" in captured.out
    assert "--format" in captured.out


def test_cli_doctor_subcommand(capsys):
    """Verify openreview doctor command runs diagnostic checks."""
    exit_code = main(["doctor"])
    assert exit_code in [EXIT_SUCCESS, EXIT_TOOL_FAILURE]
    captured = capsys.readouterr()
    assert "OpenReview AI Environment Doctor" in captured.out
    assert "Git" in captured.out
    assert "Ollama" in captured.out


def test_cli_invalid_argument_exit_code(capsys):
    """Verify unknown flags trigger documented exit code 2."""
    exit_code = main(["--non-existent-flag"])
    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "unrecognized arguments: --non-existent-flag" in captured.err


def test_cli_findings_subcommand_help(capsys):
    """Verify openreview findings --help displays options."""
    exit_code = main(["findings", "--help"])
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "--severity" in captured.out
    assert "--format" in captured.out


def test_cli_review_agent_mode(temp_git_repo, capsys):
    """Verify openreview review --agent outputs valid JSON directly."""
    import json
    exit_code = main(["review", "--agent", "--path", str(temp_git_repo)])
    assert exit_code in [EXIT_SUCCESS, 1]
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "schema_version" in data
    assert "findings" in data
    assert "summary" in data


def test_cli_findings_execution_json(temp_git_repo, capsys):
    """Verify openreview findings --format json outputs valid JSON list."""
    import json
    exit_code = main(["findings", "--format", "json", "--path", str(temp_git_repo)])
    assert exit_code in [EXIT_SUCCESS, 1]
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)

