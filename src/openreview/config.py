"""Configuration loading, validation, and defaults for OpenReview AI."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):  # noqa: UP036
    import tomllib
else:
    import tomli as tomllib  # type: ignore

logger = logging.getLogger(__name__)

MEMORY_LIMIT_PATTERN = re.compile(r"^\d+([kKmMgG]b?|[kKmMgG])?$")
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Raised when configuration file or environment variable is malformed or invalid."""


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
    enforce_local_ai: bool = False


@dataclass(frozen=True)
class ReportsConfig:
    formats: list[str] = field(default_factory=lambda: ["markdown"])
    output_dir: str | None = None


@dataclass(frozen=True)
class ServerConfig:
    """GitHub App webhook server and queue persistence settings."""

    host: str = "0.0.0.0"
    port: int = 8000
    jobs_db: str = "openreview_jobs.db"
    log_level: str = "INFO"
    webhook_secret: str = ""
    app_id: str | None = None
    private_key_path: str | None = None


@dataclass(frozen=True)
class SandboxConfig:
    """Security boundaries and Docker execution sandbox settings."""

    require_container: bool = False
    timeout_seconds: int = 120
    memory_limit: str = "512m"
    cpus: float = 1.0
    pids_limit: int = 100
    container_image: str = "openreview-scanner:latest"
    validator_image: str = "openreview-patch-validator:latest"
    network_disabled: bool = True
    read_only_rootfs: bool = True


@dataclass(frozen=True)
class OpenReviewConfig:
    review: ReviewConfig = field(default_factory=ReviewConfig)
    scanners: ScannersConfig = field(default_factory=ScannersConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)


ALLOWED_TOP_SECTIONS = {
    "review",
    "scanners",
    "ai",
    "privacy",
    "reports",
    "server",
    "sandbox",
}

ALLOWED_SECTIONS: dict[str, dict[str, type | tuple[type, ...]]] = {
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
        "enforce_local_ai": bool,
    },
    "reports": {
        "formats": list,
        "output_dir": (str, type(None)),
    },
    "server": {
        "host": str,
        "port": int,
        "jobs_db": str,
        "log_level": str,
        "webhook_secret": str,
        "app_id": (str, type(None)),
        "private_key_path": (str, type(None)),
    },
    "sandbox": {
        "require_container": bool,
        "timeout_seconds": int,
        "memory_limit": str,
        "cpus": (int, float),
        "pids_limit": int,
        "container_image": str,
        "validator_image": str,
        "network_disabled": bool,
        "read_only_rootfs": bool,
    },
}


def parse_dotenv(content: str) -> dict[str, str]:
    """Parse .env file contents into a dictionary of key-value pairs without third-party dependencies."""
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()

        # Strip surrounding double or single quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        parsed[key] = val
    return parsed


def load_dotenv(
    path: Path | str | None = None,
    override: bool = False,
) -> dict[str, str]:
    """Load environment variables from a .env file into os.environ if present.

    Does not overwrite existing environment variables unless override=True.
    """
    env_file = Path(path) if path else Path.cwd() / ".env"
    if not env_file.is_file():
        return {}

    try:
        content = env_file.read_text(encoding="utf-8")
        parsed = parse_dotenv(content)
        for k, v in parsed.items():
            if override or k not in os.environ:
                os.environ[k] = v
        return parsed
    except OSError as exc:
        logger.warning("Could not read .env file at %s: %s", env_file, exc)
        return {}


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


def _parse_bool(val: Any) -> bool:
    """Convert string, int, or bool representations to boolean."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        clean = val.strip().lower()
        if clean in ("true", "1", "yes", "on", "enable", "enabled"):
            return True
        if clean in ("false", "0", "no", "off", "disable", "disabled", ""):
            return False
    raise ConfigError(f"Cannot parse boolean value from: {val!r}")


def validate_config(
    config: OpenReviewConfig,
    enforce_production: bool = False,
) -> None:
    """Validate all field boundaries, security limits, and production requirements."""
    # 1. Review boundaries
    if config.review.max_files <= 0:
        raise ConfigError("[review].max_files must be greater than 0")
    if config.review.max_file_bytes <= 0:
        raise ConfigError("[review].max_file_bytes must be greater than 0")

    # 2. Scanner boundaries
    if config.scanners.timeout_seconds <= 0:
        raise ConfigError("[scanners].timeout_seconds must be greater than 0")

    # 3. Report formats
    for fmt in config.reports.formats:
        if fmt not in {"markdown", "json", "both"}:
            raise ConfigError(
                f"Invalid report format '{fmt}'. Allowed: 'markdown', 'json', 'both'"
            )

    # 4. Server configuration
    if config.server.port < 1 or config.server.port > 65535:
        raise ConfigError(
            f"Server port must be between 1 and 65535, got {config.server.port}"
        )

    log_level_upper = config.server.log_level.strip().upper()
    if log_level_upper not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"Invalid log level '{config.server.log_level}'. "
            f"Allowed: {sorted(VALID_LOG_LEVELS)}"
        )

    if config.server.private_key_path:
        pk_path = Path(config.server.private_key_path)
        if not pk_path.is_file():
            raise ConfigError(
                f"Configured GITHUB_PRIVATE_KEY_PATH does not exist: {pk_path}"
            )

    # 5. Sandbox security & resource boundaries
    if config.sandbox.timeout_seconds <= 0:
        raise ConfigError("[sandbox].timeout_seconds must be greater than 0")
    if config.sandbox.cpus <= 0.0:
        raise ConfigError("[sandbox].cpus must be greater than 0.0")
    if config.sandbox.pids_limit <= 0:
        raise ConfigError("[sandbox].pids_limit must be greater than 0")

    if not MEMORY_LIMIT_PATTERN.match(config.sandbox.memory_limit.strip()):
        raise ConfigError(
            f"Invalid memory limit format: '{config.sandbox.memory_limit}'. "
            "Examples: '512m', '1g', '256M'"
        )

    # 6. Container enforcement checks
    if config.sandbox.require_container:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            raise ConfigError(
                "OPENREVIEW_REQUIRE_CONTAINER=true is set, but 'docker' executable is not found in PATH. "
                "Refusing to execute in unsafe host mode."
            )
        try:
            res = subprocess.run(
                [docker_bin, "info"],
                capture_output=True,
                check=False,
                timeout=3.0,
            )
            if res.returncode != 0:
                raise ConfigError(
                    "OPENREVIEW_REQUIRE_CONTAINER=true is set, but Docker daemon is not reachable. "
                    "Refusing to execute in unsafe host mode."
                )
        except subprocess.TimeoutExpired as exc:
            raise ConfigError(
                "OPENREVIEW_REQUIRE_CONTAINER=true is set, but Docker daemon timed out responding. "
                "Refusing to execute in unsafe host mode."
            ) from exc
        except OSError as exc:
            raise ConfigError(
                f"OPENREVIEW_REQUIRE_CONTAINER=true is set, but failed to connect to Docker: {exc}"
            ) from exc

    # 7. Production enforcement (when running production server/worker)
    if enforce_production:
        if not config.server.webhook_secret:
            raise ConfigError(
                "GITHUB_WEBHOOK_SECRET must be set and non-empty in production"
            )


def load_config_from_env(
    base_config: OpenReviewConfig | None = None,
    env: Mapping[str, str] | None = None,
) -> OpenReviewConfig:
    """Construct an OpenReviewConfig by overlaying environment variables on a base configuration."""
    cfg = base_config or OpenReviewConfig()
    e = env if env is not None else os.environ

    # 1. Review settings
    review_base_ref = e.get("OPENREVIEW_BASE_REF", cfg.review.base_ref)
    review_changed = (
        _parse_bool(e["OPENREVIEW_CHANGED_FILES_ONLY"])
        if "OPENREVIEW_CHANGED_FILES_ONLY" in e
        else cfg.review.changed_files_only
    )
    review_max_files = (
        int(e["OPENREVIEW_MAX_FILES"])
        if "OPENREVIEW_MAX_FILES" in e
        else cfg.review.max_files
    )
    review_max_bytes = (
        int(e["OPENREVIEW_MAX_FILE_BYTES"])
        if "OPENREVIEW_MAX_FILE_BYTES" in e
        else cfg.review.max_file_bytes
    )

    review_cfg = ReviewConfig(
        base_ref=review_base_ref,
        changed_files_only=review_changed,
        max_files=review_max_files,
        max_file_bytes=review_max_bytes,
    )

    # 2. Scanner settings
    scanners_enabled = (
        [s.strip() for s in e["OPENREVIEW_SCANNERS"].split(",") if s.strip()]
        if "OPENREVIEW_SCANNERS" in e
        else cfg.scanners.enabled
    )
    scanners_timeout = (
        int(e["OPENREVIEW_SCANNERS_TIMEOUT"])
        if "OPENREVIEW_SCANNERS_TIMEOUT" in e
        else cfg.scanners.timeout_seconds
    )
    scanners_cfg = ScannersConfig(
        enabled=scanners_enabled,
        timeout_seconds=scanners_timeout,
    )

    # 3. AI settings
    ai_enabled = (
        _parse_bool(e["OPENREVIEW_AI_ENABLED"])
        if "OPENREVIEW_AI_ENABLED" in e
        else cfg.ai.enabled
    )
    ai_provider = e.get("OPENREVIEW_AI_PROVIDER", cfg.ai.provider)
    ai_model = e.get("OPENREVIEW_AI_MODEL", cfg.ai.model)
    ai_endpoint = e.get("OPENREVIEW_AI_ENDPOINT", cfg.ai.endpoint)
    ai_context_tokens = (
        int(e["OPENREVIEW_AI_MAX_CONTEXT_TOKENS"])
        if "OPENREVIEW_AI_MAX_CONTEXT_TOKENS" in e
        else cfg.ai.max_context_tokens
    )
    ai_cfg = AIConfig(
        enabled=ai_enabled,
        provider=ai_provider,
        model=ai_model,
        endpoint=ai_endpoint,
        max_context_tokens=ai_context_tokens,
    )

    # 4. Privacy settings
    privacy_redact = (
        _parse_bool(e["OPENREVIEW_PRIVACY_REDACT_SECRETS"])
        if "OPENREVIEW_PRIVACY_REDACT_SECRETS" in e
        else cfg.privacy.redact_secrets
    )
    privacy_local = (
        _parse_bool(e["OPENREVIEW_PRIVACY_ENFORCE_LOCAL_AI"])
        if "OPENREVIEW_PRIVACY_ENFORCE_LOCAL_AI" in e
        else cfg.privacy.enforce_local_ai
    )
    privacy_cfg = PrivacyConfig(
        redact_secrets=privacy_redact,
        enforce_local_ai=privacy_local,
    )

    # 5. Reports settings
    reports_formats = (
        [fmt.strip() for fmt in e["OPENREVIEW_REPORT_FORMATS"].split(",") if fmt.strip()]
        if "OPENREVIEW_REPORT_FORMATS" in e
        else cfg.reports.formats
    )
    reports_output_dir = e.get("OPENREVIEW_REPORT_OUTPUT_DIR", cfg.reports.output_dir)
    reports_cfg = ReportsConfig(
        formats=reports_formats,
        output_dir=reports_output_dir,
    )

    # 6. Server settings
    server_host = e.get("OPENREVIEW_SERVER_HOST", cfg.server.host)
    try:
        server_port = (
            int(e["OPENREVIEW_SERVER_PORT"])
            if "OPENREVIEW_SERVER_PORT" in e
            else cfg.server.port
        )
    except ValueError as exc:
        raise ConfigError(
            f"OPENREVIEW_SERVER_PORT must be an integer, got: {e['OPENREVIEW_SERVER_PORT']!r}"
        ) from exc

    server_db = e.get("OPENREVIEW_JOBS_DB", cfg.server.jobs_db)
    server_log_level = e.get("OPENREVIEW_LOG_LEVEL", cfg.server.log_level)
    webhook_secret = e.get("GITHUB_WEBHOOK_SECRET", cfg.server.webhook_secret)
    app_id = e.get("GITHUB_APP_ID", cfg.server.app_id)
    private_key_path = e.get("GITHUB_PRIVATE_KEY_PATH", cfg.server.private_key_path)

    server_cfg = ServerConfig(
        host=server_host,
        port=server_port,
        jobs_db=server_db,
        log_level=server_log_level,
        webhook_secret=webhook_secret,
        app_id=app_id,
        private_key_path=private_key_path,
    )

    # 7. Sandbox settings
    sandbox_require = (
        _parse_bool(e["OPENREVIEW_REQUIRE_CONTAINER"])
        if "OPENREVIEW_REQUIRE_CONTAINER" in e
        else cfg.sandbox.require_container
    )
    sandbox_timeout = (
        int(e["OPENREVIEW_SANDBOX_TIMEOUT_SECONDS"])
        if "OPENREVIEW_SANDBOX_TIMEOUT_SECONDS" in e
        else cfg.sandbox.timeout_seconds
    )
    sandbox_memory = e.get(
        "OPENREVIEW_SANDBOX_MEMORY_LIMIT", cfg.sandbox.memory_limit
    )
    sandbox_cpus = (
        float(e["OPENREVIEW_SANDBOX_CPUS"])
        if "OPENREVIEW_SANDBOX_CPUS" in e
        else cfg.sandbox.cpus
    )
    sandbox_pids = (
        int(e["OPENREVIEW_SANDBOX_PIDS_LIMIT"])
        if "OPENREVIEW_SANDBOX_PIDS_LIMIT" in e
        else cfg.sandbox.pids_limit
    )
    container_image = e.get(
        "OPENREVIEW_CONTAINER_IMAGE", cfg.sandbox.container_image
    )
    validator_image = e.get(
        "OPENREVIEW_VALIDATOR_IMAGE", cfg.sandbox.validator_image
    )

    sandbox_cfg = SandboxConfig(
        require_container=sandbox_require,
        timeout_seconds=sandbox_timeout,
        memory_limit=sandbox_memory,
        cpus=sandbox_cpus,
        pids_limit=sandbox_pids,
        container_image=container_image,
        validator_image=validator_image,
        network_disabled=cfg.sandbox.network_disabled,
        read_only_rootfs=cfg.sandbox.read_only_rootfs,
    )

    new_cfg = OpenReviewConfig(
        review=review_cfg,
        scanners=scanners_cfg,
        ai=ai_cfg,
        privacy=privacy_cfg,
        reports=reports_cfg,
        server=server_cfg,
        sandbox=sandbox_cfg,
    )

    validate_config(new_cfg)
    return new_cfg


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
    server_data = _validate_section("server", raw.get("server", {}))
    sandbox_data = _validate_section("sandbox", raw.get("sandbox", {}))

    config = OpenReviewConfig(
        review=ReviewConfig(**review_data),
        scanners=ScannersConfig(**scanners_data),
        ai=AIConfig(**ai_data),
        privacy=PrivacyConfig(**privacy_data),
        reports=ReportsConfig(**reports_data),
        server=ServerConfig(**server_data),
        sandbox=SandboxConfig(**sandbox_data),
    )
    validate_config(config)
    return config


def load_config(
    path: str | Path | None = None,
    repo_root: str | Path | None = None,
    load_env: bool = True,
) -> OpenReviewConfig:
    """Load configuration from explicit path or candidates in repo root, overlaid with environment variables."""
    root_path = Path(repo_root) if repo_root else Path.cwd()

    if load_env:
        # Load .env if present
        load_dotenv(root_path / ".env")

    base_config: OpenReviewConfig | None = None

    if path is not None:
        target_path = Path(path)
        if not target_path.exists():
            raise ConfigError(f"Specified configuration file not found: {target_path}")
        base_config = _read_config_file(target_path)
    else:
        candidate_names = [
            ".openreview.toml",
            "openreview.toml",
            ".openreview.json",
            "openreview.json",
        ]

        for name in candidate_names:
            candidate = root_path / name
            if candidate.is_file():
                base_config = _read_config_file(candidate)
                break

    if base_config is None:
        base_config = OpenReviewConfig()

    # Overlay environment variables
    return load_config_from_env(base_config=base_config)


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
        raise ConfigError(
            f"Unsupported config format '{suffix}' for {file_path}. Use .toml or .json"
        )

    if not isinstance(data, dict):
        raise ConfigError(
            f"Root configuration must be a key-value mapping in {file_path}"
        )

    return parse_config_dict(data)
