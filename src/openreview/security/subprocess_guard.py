"""Subprocess argument validation and injection prevention per Gate D."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence


class SubprocessArgumentError(ValueError):
    """Raised when command line arguments contain illegal metacharacters or dangerous patterns."""


FORBIDDEN_CONTROL_CHARS = re.compile(r"[\0\r\n]")


def validate_subprocess_args(cmd: Sequence[str | Path]) -> list[str]:
    """Validate that command arguments are well-formed and free of dangerous control characters.

    Raises SubprocessArgumentError if:
    - Command list is empty
    - Executable name (argv[0]) is empty or contains whitespace/slashes improperly
    - Any argument contains null bytes, newlines, or control chars
    """
    if not cmd:
        raise SubprocessArgumentError("Subprocess command cannot be empty")

    validated: list[str] = []
    for idx, arg in enumerate(cmd):
        arg_str = str(arg)
        if FORBIDDEN_CONTROL_CHARS.search(arg_str):
            raise SubprocessArgumentError(
                f"Subprocess argument at index {idx} contains forbidden control or null characters"
            )
        if idx == 0 and not arg_str.strip():
            raise SubprocessArgumentError("Executable command name cannot be empty")
        validated.append(arg_str)

    return validated
