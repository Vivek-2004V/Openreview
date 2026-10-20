"""Finding Normalizer component (Node I in review architecture).

Normalizes raw diagnostics from static scanners (Ruff, ESLint, REA) and
structured AI suggestions into the canonical versioned Finding model.
"""

from __future__ import annotations

import logging
from typing import Sequence

from openreview.adapters.ai_base import AIReviewSuggestion
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    FindingStatus,
    Severity,
    compute_finding_id,
)

logger = logging.getLogger(__name__)


def _parse_severity(sev_input: str | Severity) -> Severity:
    if isinstance(sev_input, Severity):
        return sev_input
    norm = str(sev_input).strip().lower()
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "warning": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
        "style": Severity.LOW,
    }
    return mapping.get(norm, Severity.MEDIUM)


class FindingNormalizer:
    """Normalizes heterogeneous review findings into standardized Finding models."""

    @staticmethod
    def normalize_scanner_finding(
        scanner: str,
        rule_id: str,
        path: str,
        title: str,
        explanation: str,
        start_line: int | None = None,
        end_line: int | None = None,
        severity: str | Severity = Severity.MEDIUM,
        recommendation: str | None = None,
        evidence: str | None = None,
    ) -> Finding:
        """Create a normalized static finding with stable ID."""
        sev = _parse_severity(severity)

        return Finding(
            title=title.strip(),
            explanation=explanation.strip(),
            path=path.strip(),
            severity=sev,
            confidence=Confidence.HIGH,
            origin=FindingOrigin.STATIC,
            status=FindingStatus.OPEN,
            start_line=start_line,
            end_line=end_line,
            rule_id=rule_id.strip() if rule_id else None,
            recommendation=recommendation.strip() if recommendation else None,
            evidence=evidence,
        )

    @staticmethod
    def normalize_ai_suggestion(
        suggestion: AIReviewSuggestion,
        path: str,
    ) -> Finding:
        """Normalize an AI-generated suggestion into an unverified Finding per ADR-002."""
        sev = _parse_severity(suggestion.severity)
        line = max(1, suggestion.line)

        title = f"{suggestion.rule_id}: AI Review Suggestion"
        return Finding(
            title=title,
            explanation=suggestion.message.strip(),
            path=path.strip(),
            severity=sev,
            confidence=Confidence.MEDIUM,
            origin=FindingOrigin.AI,
            status=FindingStatus.UNVERIFIED,
            start_line=line,
            end_line=line,
            rule_id=suggestion.rule_id,
            recommendation=suggestion.suggestion.strip() if suggestion.suggestion else None,
        )

    @classmethod
    def batch_normalize(
        cls,
        static_findings: Sequence[Finding],
        ai_suggestions: Sequence[tuple[AIReviewSuggestion, str]],
        ai_scanner_name: str = "ollama",
    ) -> list[Finding]:
        """Normalize a mixed batch of static findings and AI suggestions."""
        normalized: list[Finding] = list(static_findings)

        for sugg, target_path in ai_suggestions:
            normalized.append(
                cls.normalize_ai_suggestion(sugg, path=target_path)
            )

        return normalized
