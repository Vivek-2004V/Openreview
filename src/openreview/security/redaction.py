"""Secret and credential redaction engine per TRD Section 3.4 and Gate D."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openreview.adapters.ai_base import CodeContextBlock

REDACTED_PLACEHOLDER = "[REDACTED_SECRET]"

# Compiled regex patterns for detecting secrets
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s+[A-Z0-9_-]+\s+PRIVATE\s+KEY-----[\s\S]*?-----END\s+[A-Z0-9_-]+\s+PRIVATE\s+KEY-----",
            re.MULTILINE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"\b(AKIA|ASIA|AROA)[0-9A-Z]{16,28}\b"),
    ),
    (
        "github_pat",
        re.compile(r"\b(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82})\b"),
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"),
    ),
    (
        "jwt_token",
        re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(bearer\s+)([a-zA-Z0-9_\-\.]{24,})\b"),
    ),
    (
        "api_key_assignment",
        re.compile(
            r"""(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\s*([:=])\s*(['"])([a-zA-Z0-9_\-]{16,})(['"])"""
        ),
    ),
    (
        "password_assignment",
        re.compile(
            r"""(?i)\b(password|passwd|pwd)\s*([:=])\s*(['"])([^'"\n\r]{6,})(['"])"""
        ),
    ),
]


@dataclass(frozen=True)
class RedactionResult:
    """Outcome of secret redaction operation."""

    redacted_text: str
    total_redactions: int
    pattern_matches: dict[str, int] = field(default_factory=dict)


def redact_text(text: str) -> RedactionResult:
    """Inspect text and replace all matched secrets with [REDACTED_SECRET]."""
    if not text:
        return RedactionResult(redacted_text="", total_redactions=0)

    result = text
    total_count = 0
    pattern_counts: dict[str, int] = {}

    for pattern_name, regex in PATTERNS:
        matches = list(regex.finditer(result))
        if not matches:
            continue

        count = len(matches)
        total_count += count
        pattern_counts[pattern_name] = count

        if pattern_name == "bearer_token":
            # Preserve 'Bearer ' prefix, redact only token
            result = regex.sub(rf"\g<1>{REDACTED_PLACEHOLDER}", result)
        elif pattern_name in ("api_key_assignment", "password_assignment"):
            # Preserve key name and quotes, redact value inside
            result = regex.sub(rf"\g<1> \g<2> \g<3>{REDACTED_PLACEHOLDER}\g<5>", result)
        else:
            # Full replacement
            result = regex.sub(REDACTED_PLACEHOLDER, result)

    return RedactionResult(
        redacted_text=result,
        total_redactions=total_count,
        pattern_matches=pattern_counts,
    )


def redact_context_blocks(blocks: list[CodeContextBlock]) -> tuple[list[CodeContextBlock], int]:
    """Apply secret redaction across a list of code context blocks."""
    redacted_blocks: list[CodeContextBlock] = []
    total_redactions = 0

    for block in blocks:
        res = redact_text(block.content)
        total_redactions += res.total_redactions
        if res.total_redactions > 0:
            redacted_blocks.append(
                CodeContextBlock(
                    path=block.path,
                    start_line=block.start_line,
                    end_line=block.end_line,
                    content=res.redacted_text,
                    language=block.language,
                )
            )
        else:
            redacted_blocks.append(block)

    return redacted_blocks, total_redactions
