"""Documented process exit codes for OpenReview AI per TRD Section 7."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Formal exit codes contract for OpenReview AI.

    Exit codes enable CI/CD pipelines, GitHub Actions, and terminal users to
    reliably distinguish between clean runs, security/quality findings, configuration
    errors, and internal failures.
    """

    SUCCESS = 0
    FINDINGS_PRESENT = 1
    INVALID_INPUT_OR_CONFIG = 2
    TOOL_FAILURE = 3
    INTERNAL_ERROR = 4

    @property
    def description(self) -> str:
        descriptions = {
            ExitCode.SUCCESS: "Review completed cleanly, no actionable findings above threshold.",
            ExitCode.FINDINGS_PRESENT: "Review completed, actionable findings present.",
            ExitCode.INVALID_INPUT_OR_CONFIG: "Invalid configuration, CLI arguments, or repository path.",
            ExitCode.TOOL_FAILURE: "One or more required scanner tools or model endpoints failed or timed out.",
            ExitCode.INTERNAL_ERROR: "Internal unexpected error.",
        }
        return descriptions[self]


# Legacy constants for backwards compatibility
EXIT_SUCCESS = int(ExitCode.SUCCESS)
EXIT_FINDINGS_PRESENT = int(ExitCode.FINDINGS_PRESENT)
EXIT_INVALID_INPUT_OR_CONFIG = int(ExitCode.INVALID_INPUT_OR_CONFIG)
EXIT_TOOL_FAILURE = int(ExitCode.TOOL_FAILURE)
EXIT_INTERNAL_ERROR = int(ExitCode.INTERNAL_ERROR)


def format_exit_codes_summary() -> str:
    """Format human-readable summary of exit codes for CLI help and documentation."""
    lines = ["Exit Codes:"]
    for code in ExitCode:
        lines.append(f"  {code.value:<2} {code.description}")
    return "\n".join(lines)
