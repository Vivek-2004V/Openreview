"""Integration tests for CLI error scenarios and clean invocation per E1-T5."""

import subprocess
import pytest
from pathlib import Path
from openreview import (
    EXIT_SUCCESS,
    EXIT_INVALID_INPUT_OR_CONFIG,
    EXIT_TOOL_FAILURE,
)
from openreview.cli import main


@pytest.fixture
def clean_git_repo(tmp_path):
    """Fixture providing an initialized, clean Git repository."""
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    (repo / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "feat: initial commit"], cwd=repo, capture_output=True, check=True)
    return repo


def test_scenario_invalid_path(capsys):
    """Test scenario: Non-existent target directory exits with code 2 and readable error."""
    invalid_path = "/non_existent/path_to_workspace_998877"
    exit_code = main(["review", "--path", invalid_path])

    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Repository error:" in captured.err
    assert "Specified path does not exist" in captured.err


def test_scenario_no_git_repo(tmp_path, capsys):
    """Test scenario: Directory without git repository exits with code 2 and readable error."""
    plain_dir = tmp_path / "plain_folder"
    plain_dir.mkdir()
    (plain_dir / "file.txt").write_text("plain file", encoding="utf-8")

    exit_code = main(["review", "--path", str(plain_dir)])
    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Repository error:" in captured.err
    assert "not a git repository" in captured.err.lower()


def test_scenario_bad_config_missing_file(clean_git_repo, capsys):
    """Test scenario: Explicitly specified config file that does not exist."""
    exit_code = main([
        "review",
        "--path", str(clean_git_repo),
        "--config", str(clean_git_repo / "missing.toml"),
    ])

    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.err
    assert "Specified configuration file not found" in captured.err


def test_scenario_bad_config_invalid_syntax(clean_git_repo, capsys):
    """Test scenario: Config file with invalid syntax exits with code 2."""
    bad_toml = clean_git_repo / "invalid.toml"
    bad_toml.write_text("[[ broken toml [ syntax", encoding="utf-8")

    exit_code = main([
        "review",
        "--path", str(clean_git_repo),
        "--config", str(bad_toml),
    ])

    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.err
    assert "Malformed TOML" in captured.err


def test_scenario_bad_config_unknown_section(clean_git_repo, capsys):
    """Test scenario: Config file containing unapproved section."""
    evil_toml = clean_git_repo / "evil.toml"
    evil_toml.write_text("[unauthorized_section]\nkey = 'value'\n", encoding="utf-8")

    exit_code = main([
        "review",
        "--path", str(clean_git_repo),
        "--config", str(evil_toml),
    ])

    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.err
    assert "Unknown top-level configuration section 'unauthorized_section'" in captured.err


def test_scenario_clean_invocation_default(clean_git_repo, capsys):
    """Test scenario: Clean invocation on valid repo exits with code 0 and proper summary."""
    exit_code = main(["review", "--path", str(clean_git_repo)])

    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "OpenReview AI v" in captured.out
    assert "Initialized." in captured.out
    assert f"Target repository: {clean_git_repo.resolve()}" in captured.out
    assert "branch: main" in captured.out
    assert captured.err == ""


def test_scenario_clean_invocation_verbose_with_config(clean_git_repo, capsys):
    """Test scenario: Clean invocation with valid config and verbose mode."""
    config_file = clean_git_repo / ".openreview.toml"
    config_file.write_text(
        """
[review]
max_files = 200

[scanners]
enabled = ["ruff"]
timeout_seconds = 20
""",
        encoding="utf-8",
    )

    exit_code = main(["review", "--path", str(clean_git_repo), "--verbose"])

    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "Loaded configuration:" in captured.out
    assert "Git Repository Root:" in captured.out
    assert "Git Branch/Ref: main" in captured.out
    assert "Uncommitted Changes: 1 files" in captured.out  # the newly created .openreview.toml
    assert captured.err == ""
