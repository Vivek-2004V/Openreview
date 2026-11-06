"""AI safety defenses: prompt injection detection, input sanitization, and output bounds per P3."""

from __future__ import annotations

import re

# High-confidence prompt injection signatures commonly planted in code comments or PR metadata
PROMPT_INJECTION_PATTERNS = [
    (re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b"), "ignore_previous_instructions"),
    (re.compile(r"(?i)\bdisregard\s+(all\s+)?(previous|prior|system)\s+instructions\b"), "disregard_instructions"),
    (re.compile(r"(?i)\bsystem\s+override\b"), "system_override"),
    (re.compile(r"(?i)\byou\s+are\s+now\s+(in\s+)?(debug|developer|jailbreak|unrestricted|god)\s+mode\b"), "mode_switch_jailbreak"),
    (re.compile(r"(?i)\b(do\s+not|never)\s+(report|flag|warn|fail)\s+(about\s+)?(any\s+)?(bugs|vulnerabilities|issues|errors)\b"), "suppress_findings_injection"),
    (re.compile(r"(?i)\b(always\s+)?(approve|accept)\s+this\s+(pr|pull\s+request|code|patch)\b"), "forced_approval_injection"),
    (re.compile(r"(?i)<\s*(system|assistant|im_start|im_end)\b"), "tag_injection"),
    (re.compile(r"(?i)\bpretend\s+(you\s+are|to\s+be)\s+a\s+(different|helpful)\b"), "roleplay_bypass"),
    (re.compile(r"(?i)\bbypass\s+(all\s+)?(safety|security)\s+(filters|rules|checks)\b"), "bypass_security"),
]

MAX_PROMPT_INJECTION_LOG = 5


def detect_prompt_injection(text: str) -> list[str]:
    """Scan text for common adversarial prompt injection patterns.

    Returns a list of matched pattern identifiers.
    """
    if not text or not text.strip():
        return []

    matched: list[str] = []
    for pattern, name in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            matched.append(name)
            if len(matched) >= MAX_PROMPT_INJECTION_LOG:
                break

    return matched


def has_prompt_injection(text: str) -> bool:
    """Return True if any known prompt injection pattern is detected in text."""
    if not text:
        return False
    for pattern, _ in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def sanitize_prompt_text(text: str) -> str:
    """Defang known prompt injection tags and directives to neutralize untrusted input."""
    if not text:
        return ""

    sanitized = text
    # Defang structural control tags
    sanitized = re.sub(r"(?i)<\s*(system|assistant|im_start|im_end)\b", "[defanged_tag]", sanitized)
    return sanitized


def validate_ai_finding_content(
    title: str,
    explanation: str,
    recommendation: str | None = None,
) -> tuple[bool, str]:
    """Verify that AI finding output does not echo adversarial directives or shell commands.

    Returns (is_safe: bool, reason: str).
    """
    combined = f"{title}\n{explanation}\n{recommendation or ''}"

    # Check for direct piped or process-substituted remote shell execution instructions
    shell_exec_match = (
        re.search(r"(?i)\b(curl|wget)\b[^|\n]*\|\s*(bash|sh|zsh|python|perl)\b", combined)
        or re.search(r"(?i)\b(bash|sh|zsh)\s*<\s*\(\s*(curl|wget)\b", combined)
    )
    if shell_exec_match:
        return False, "Finding contains dangerous piped shell command execution advice"

    # Check if finding appears to echo an injection attempt
    injections = detect_prompt_injection(combined)
    if injections:
        return False, f"Finding contains prompt injection signature: {', '.join(injections)}"

    return True, ""
