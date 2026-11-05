"""Unit tests for OpenReview configuration loading and validation."""

import pytest
from openreview.config import (
    ConfigError,
    OpenReviewConfig,
    load_config,
    parse_config_dict,
)
from openreview.cli import EXIT_INVALID_INPUT_OR_CONFIG, main


def test_default_config():
    """Verify default configuration has expected default values."""
    cfg = OpenReviewConfig()
    assert cfg.review.changed_files_only is True
    assert cfg.review.max_files == 100
    assert cfg.scanners.enabled == ["ruff", "eslint"]
    assert cfg.scanners.timeout_seconds == 30
    assert cfg.ai.enabled is False
    assert cfg.ai.model == "qwen2.5-coder:7b"
    assert cfg.privacy.redact_secrets is True
    assert cfg.reports.formats == ["markdown"]


def test_parse_valid_toml_config(tmp_path):
    """Verify loading a valid TOML configuration file."""
    toml_file = tmp_path / ".openreview.toml"
    toml_file.write_text(
        """
[review]
base_ref = "origin/main"
changed_files_only = false
max_files = 50

[scanners]
enabled = ["ruff", "bandit"]
timeout_seconds = 45

[ai]
enabled = true
model = "deepseek-coder:6.7b"
""",
        encoding="utf-8",
    )

    cfg = load_config(path=toml_file)
    assert cfg.review.base_ref == "origin/main"
    assert cfg.review.changed_files_only is False
    assert cfg.review.max_files == 50
    assert cfg.scanners.enabled == ["ruff", "bandit"]
    assert cfg.scanners.timeout_seconds == 45
    assert cfg.ai.enabled is True
    assert cfg.ai.model == "deepseek-coder:6.7b"


def test_parse_valid_json_config(tmp_path):
    """Verify loading a valid JSON configuration file."""
    json_file = tmp_path / "openreview.json"
    json_file.write_text(
        """
{
    "review": {
        "max_files": 25
    },
    "reports": {
        "formats": ["markdown", "json"]
    }
}
""",
        encoding="utf-8",
    )

    cfg = load_config(path=json_file)
    assert cfg.review.max_files == 25
    assert cfg.reports.formats == ["markdown", "json"]


def test_reject_unknown_top_level_section():
    """Verify rejection of unapproved top-level sections."""
    with pytest.raises(ConfigError, match="Unknown top-level configuration section 'malicious'"):
        parse_config_dict({"malicious": {"run_command": "rm -rf"}})


def test_reject_unknown_key_in_section():
    """Verify rejection of unallowlisted keys within a valid section."""
    with pytest.raises(ConfigError, match="Unknown configuration key 'dangerous_flag' in section \\[ai\\]"):
        parse_config_dict({"ai": {"dangerous_flag": True}})


def test_reject_invalid_field_type():
    """Verify type checking for configuration fields."""
    with pytest.raises(ConfigError, match="Invalid type for \\[scanners\\].timeout_seconds: expected int, got str"):
        parse_config_dict({"scanners": {"timeout_seconds": "thirty"}})


def test_reject_out_of_bounds_values():
    """Verify rejection of non-positive integer values for bounds."""
    with pytest.raises(ConfigError, match="\\[review\\].max_files must be greater than 0"):
        parse_config_dict({"review": {"max_files": 0}})

    with pytest.raises(ConfigError, match="\\[scanners\\].timeout_seconds must be greater than 0"):
        parse_config_dict({"scanners": {"timeout_seconds": -5}})


def test_reject_invalid_report_format():
    """Verify rejection of unknown report format string."""
    with pytest.raises(ConfigError, match="Invalid report format 'pdf'"):
        parse_config_dict({"reports": {"formats": ["pdf"]}})


def test_cli_handles_invalid_config_file(tmp_path, capsys):
    """Verify CLI exits with code 2 on invalid config path or invalid content."""
    exit_code_missing = main(["review", "--config", str(tmp_path / "non_existent.toml")])
    assert exit_code_missing == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Configuration error" in captured.err

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("invalid [ [ toml syntax", encoding="utf-8")
    exit_code_syntax = main(["review", "--config", str(bad_toml)])
    assert exit_code_syntax == EXIT_INVALID_INPUT_OR_CONFIG
