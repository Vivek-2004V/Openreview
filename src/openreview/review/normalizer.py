"""Finding Normalizer component (Node I in review architecture).

Normalizes raw diagnostics from static scanners (Ruff, ESLint, REA) and
structured AI suggestions into the canonical versioned Finding model.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from openreview.adapters.ai_base import AIReviewSuggestion
from openreview.schemas.models import (
    Confidence,
    Finding,
    FindingOrigin,
    FindingStatus,
    Severity,
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
        sev = _parse_severity(getattr(suggestion, "severity", Severity.MEDIUM))
        start_line = getattr(suggestion, "start_line", None) or getattr(
            suggestion, "line", None
        )
        end_line = getattr(suggestion, "end_line", None) or start_line
        line = max(1, start_line) if start_line is not None else 1

        rule_id = getattr(suggestion, "rule_id", None)
        title = getattr(suggestion, "title", None) or (
            f"{rule_id}: AI Review Suggestion" if rule_id else "AI Review Suggestion"
        )
        explanation = (
            getattr(suggestion, "explanation", None)
            or getattr(suggestion, "message", "")
        ).strip()
        recommendation = getattr(suggestion, "recommendation", None) or getattr(
            suggestion, "suggestion", None
        )
        target_path = (
            path.strip()
            or getattr(suggestion, "path", "")
            or getattr(suggestion, "file_path", "")
        )

        return Finding(
            title=title.strip(),
            explanation=explanation,
            path=target_path.strip(),
            severity=sev,
            confidence=getattr(suggestion, "confidence", Confidence.MEDIUM),
            origin=FindingOrigin.AI,
            status=FindingStatus.UNVERIFIED,
            start_line=line,
            end_line=end_line or line,
            rule_id=rule_id.strip() if rule_id else None,
            recommendation=recommendation.strip() if recommendation else None,
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
            normalized.append(cls.normalize_ai_suggestion(sugg, path=target_path))

        return normalized
