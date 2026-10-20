"""Structured logging system with automatic secret redaction per Gate D."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openreview.security.redaction import redact_text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts credentials and secrets from log messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg).redacted_text

        if record.args:
            if isinstance(record.args, dict):
                cleaned_args = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        cleaned_args[k] = redact_text(v).redacted_text
                    else:
                        cleaned_args[k] = v
                record.args = cleaned_args
            elif isinstance(record.args, tuple):
                cleaned_list = []
                for v in record.args:
                    if isinstance(v, str):
                        cleaned_list.append(redact_text(v).redacted_text)
                    else:
                        cleaned_list.append(v)
                record.args = tuple(cleaned_list)
        return True


class JSONLogFormatter(logging.Formatter):
    """Formatter outputting structured JSON log entries."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def configure_logging(
    verbose: bool = False,
    log_file: Path | str | None = None,
    json_format: bool = False,
) -> None:
    """Configure openreview logger with redaction filter and appropriate formatter."""
    logger = logging.getLogger("openreview")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Remove existing handlers to avoid duplicates
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    redacting_filter = RedactingFilter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.addFilter(redacting_filter)

    if json_format:
        console_handler.setFormatter(JSONLogFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        console_handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))

    logger.addHandler(console_handler)

    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.addFilter(redacting_filter)
        if json_format:
            file_handler.setFormatter(JSONLogFormatter())
        else:
            file_fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
            file_handler.setFormatter(logging.Formatter(file_fmt))
        logger.addHandler(file_handler)
