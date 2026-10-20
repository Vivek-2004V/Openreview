"""Repository rules and commit history context provider (Node H in review architecture)."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from openreview.security.path_guard import validate_safe_path
from openreview.security.subprocess_guard import validate_subprocess_args

logger = logging.getLogger(__name__)

RULE_FILE_CANDIDATES = [
    "REVIEW_GUIDELINES.md",
    ".github/copilot-instructions.md",
    ".cursorrules",
    "CONTRIBUTING.md",
]


@dataclass(frozen=True)
class RepositoryRulesAndHistory:
    """Encapsulates repository rules and recent commit history for the orchestrator."""

    rules_text: str = ""
    rules_sources: list[str] = field(default_factory=list)
    recent_commit_logs: list[str] = field(default_factory=list)

    @property
    def has_rules(self) -> bool:
        return bool(self.rules_text.strip())

    @property
    def has_history(self) -> bool:
        return bool(self.recent_commit_logs)


def load_repository_rules_and_history(
    repo_root: Path | str,
    max_history_commits: int = 5,
) -> RepositoryRulesAndHistory:
    """Extract repository-specific review guidelines and recent git commit history."""
    root = Path(repo_root).resolve()
    rules_parts: list[str] = []
    rules_sources: list[str] = []

    # 1. Inspect repository rule files
    for candidate in RULE_FILE_CANDIDATES:
        cand_path = root / candidate
        if cand_path.is_file():
            try:
                safe_cand = validate_safe_path(cand_path, root)
                content = safe_cand.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    rules_parts.append(f"### Rules from {candidate}:\n{content[:2000]}")
                    rules_sources.append(candidate)
            except Exception as e:
                logger.debug("Could not read rule file %s: %s", candidate, e)

    combined_rules = "\n\n".join(rules_parts)

    # 2. Extract recent git commit history safely
    recent_commits: list[str] = []
    git_dir = root / ".git"
    if git_dir.exists():
        cmd = ["git", "log", f"-n{max_history_commits}", "--oneline", "--no-merges"]
        try:
            validate_subprocess_args(cmd)
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                recent_commits = lines
        except Exception as e:
            logger.debug("Failed to retrieve git commit history: %s", e)

    return RepositoryRulesAndHistory(
        rules_text=combined_rules,
        rules_sources=rules_sources,
        recent_commit_logs=recent_commits,
    )
