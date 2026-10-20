"""OpenReview AI - Free-first, local-first code review assistant."""

from openreview.exit_codes import (
    ExitCode,
    EXIT_SUCCESS,
    EXIT_FINDINGS_PRESENT,
    EXIT_INVALID_INPUT_OR_CONFIG,
    EXIT_TOOL_FAILURE,
    EXIT_INTERNAL_ERROR,
)

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "ExitCode",
    "EXIT_SUCCESS",
    "EXIT_FINDINGS_PRESENT",
    "EXIT_INVALID_INPUT_OR_CONFIG",
    "EXIT_TOOL_FAILURE",
    "EXIT_INTERNAL_ERROR",
]
