"""Configuration loading, validation, and defaults for OpenReview AI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


class ConfigError(Exception):
    """Raised when configuration file is malformed or contains invalid values."""


@dataclass(frozen=True)
class ReviewConfig:
    base_ref: str | None = None
    changed_files_only: bool = True
    max_files: int = 100
    max_file_bytes: int = 1_000_000


@dataclass(frozen=True)
class ScannersConfig:
    enabled: list[str] = field(default_factory=lambda: ["ruff", "eslint"])
    timeout_seconds: int = 30


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = "qwen2.5-coder:7b"
    endpoint: str = "http://127.0.0.1:11434"
    max_context_tokens: int = 4000


@dataclass(frozen=True)
class PrivacyConfig:
    redact_secrets: bool = True


@dataclass(frozen=True)
class ReportsConfig:
    formats: list[str] = field(default_factory=lambda: ["markdown"])
    output_dir: str | None = None


@dataclass(frozen=True)
class OpenReviewConfig:
    review: ReviewConfig = field(default_factory=ReviewConfig)
    scanners: ScannersConfig = field(default_factory=ScannersConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    reports: ReportsConfig = field(default_factory=ReportsConfig)


ALLOWED_TOP_SECTIONS = {"review", "scanners", "ai", "privacy", "reports"}
ALLOWED_SECTIONS: dict[str, dict[str, type]] = {
    "review": {
        "base_ref": (str, type(None)),
        "changed_files_only": bool,
        "max_files": int,
        "max_file_bytes": int,
    },
    "scanners": {
        "enabled": list,
        "timeout_seconds": int,
    },
    "ai": {
        "enabled": bool,
        "provider": str,
        "model": str,
        "endpoint": str,
        "max_context_tokens": int,
    },
    "privacy": {
        "redact_secrets": bool,
    },
    "reports": {
        "formats": list,
        "output_dir": (str, type(None)),
    },
}


def _validate_section(section_name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    allowed_fields = ALLOWED_SECTIONS[section_name]
    validated: dict[str, Any] = {}

    for key, value in data.items():
        if key not in allowed_fields:
            raise ConfigError(
                f"Unknown configuration key '{key}' in section [{section_name}]. "
                f"Allowed keys: {sorted(allowed_fields.keys())}"
            )
        expected_type = allowed_fields[key]
        if not isinstance(value, expected_type):
            expected_name = (
                "/".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            raise ConfigError(
                f"Invalid type for [{section_name}].{key}: expected {expected_name}, got {type(value).__name__}"
            )
        validated[key] = value

    return validated


def parse_config_dict(raw: Mapping[str, Any]) -> OpenReviewConfig:
    """Validate and construct OpenReviewConfig from raw mapping."""
    for section in raw:
        if section not in ALLOWED_TOP_SECTIONS:
            raise ConfigError(
                f"Unknown top-level configuration section '{section}'. "
                f"Allowed sections: {sorted(ALLOWED_TOP_SECTIONS)}"
            )

    review_data = _validate_section("review", raw.get("review", {}))
    scanners_data = _validate_section("scanners", raw.get("scanners", {}))
    ai_data = _validate_section("ai", raw.get("ai", {}))
    privacy_data = _validate_section("privacy", raw.get("privacy", {}))
    reports_data = _validate_section("reports", raw.get("reports", {}))

    # Boundary and value validations
    if "max_files" in review_data and review_data["max_files"] <= 0:
        raise ConfigError("[review].max_files must be greater than 0")
    if "max_file_bytes" in review_data and review_data["max_file_bytes"] <= 0:
        raise ConfigError("[review].max_file_bytes must be greater than 0")
    if "timeout_seconds" in scanners_data and scanners_data["timeout_seconds"] <= 0:
        raise ConfigError("[scanners].timeout_seconds must be greater than 0")
    if "formats" in reports_data:
        for fmt in reports_data["formats"]:
            if fmt not in {"markdown", "json", "both"}:
                raise ConfigError(
                    f"Invalid report format '{fmt}'. Allowed: 'markdown', 'json', 'both'"
                )

    return OpenReviewConfig(
        review=ReviewConfig(**review_data),
        scanners=ScannersConfig(**scanners_data),
        ai=AIConfig(**ai_data),
        privacy=PrivacyConfig(**privacy_data),
        reports=ReportsConfig(**reports_data),
    )


def load_config(path: str | Path | None = None, repo_root: str | Path | None = None) -> OpenReviewConfig:
    """Load configuration from explicit path or default candidates in repo root."""
    if path is not None:
        target_path = Path(path)
        if not target_path.exists():
            raise ConfigError(f"Specified configuration file not found: {target_path}")
        return _read_config_file(target_path)

    search_dir = Path(repo_root) if repo_root else Path.cwd()
    candidate_names = [".openreview.toml", "openreview.toml", ".openreview.json", "openreview.json"]

    for name in candidate_names:
        candidate = search_dir / name
        if candidate.is_file():
            return _read_config_file(candidate)

    # Return default configuration if no config file present
    return OpenReviewConfig()


def _read_config_file(file_path: Path) -> OpenReviewConfig:
    suffix = file_path.suffix.lower()
    content = file_path.read_text(encoding="utf-8")

    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Malformed JSON in {file_path}: {exc}") from exc
    elif suffix in (".toml", ""):
        try:
            data = tomllib.loads(content)
        except Exception as exc:
            raise ConfigError(f"Malformed TOML in {file_path}: {exc}") from exc
    else:
        raise ConfigError(f"Unsupported config format '{suffix}' for {file_path}. Use .toml or .json")

    if not isinstance(data, dict):
        raise ConfigError(f"Root configuration must be a key-value mapping in {file_path}")

    return parse_config_dict(data)
