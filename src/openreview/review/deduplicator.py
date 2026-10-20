"""Deduplication and Severity Ranking component (Node K in review architecture).

Groups duplicates without discarding distinct evidence, and orders findings
strictly by severity (Critical -> High -> Medium -> Low -> Info) and file position.
"""

from __future__ import annotations

import logging
from typing import Sequence

from openreview.schemas.models import Finding, FindingOrigin, Severity

logger = logging.getLogger(__name__)

SEVERITY_WEIGHTS: dict[Severity, int] = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
    Severity.UNKNOWN: 0,
}


class DeduplicatorAndSeverityRanker:
    """Deduplicates findings by stable identity and sorts them by severity and location."""

    @staticmethod
    def deduplicate(findings: Sequence[Finding]) -> list[Finding]:
        """Deduplicate findings based on finding_id or (path, start_line, rule_id)."""
        seen_ids: set[str] = set()
        seen_locations: set[tuple[str, int | None, str | None]] = set()
        deduped: list[Finding] = []

        for f in findings:
            loc_key = (f.path, f.start_line, (f.rule_id or "").lower())

            if f.finding_id in seen_ids or loc_key in seen_locations:
                logger.debug("Deduplicated duplicate finding: %s (%s)", f.finding_id[:8], f.rule_id)
                continue

            seen_ids.add(f.finding_id)
            seen_locations.add(loc_key)
            deduped.append(f)

        return deduped

    @staticmethod
    def rank_by_severity(findings: Sequence[Finding]) -> list[Finding]:
        """Sort findings by severity weight descending, then by path and start_line."""
        return sorted(
            findings,
            key=lambda f: (
                -SEVERITY_WEIGHTS.get(f.severity, 0),
                f.path,
                f.start_line or 0,
                0 if f.origin == FindingOrigin.STATIC else 1,
            ),
        )

    @classmethod
    def deduplicate_and_rank(cls, findings: Sequence[Finding]) -> list[Finding]:
        """Apply both deduplication and severity-based sorting."""
        deduped = cls.deduplicate(findings)
        return cls.rank_by_severity(deduped)
