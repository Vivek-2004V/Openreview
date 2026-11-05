"""Bounded context selection for changed code per TRD Section 3.4."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from openreview.adapters.ai_base import CodeContextBlock
from openreview.repo.diff_parser import FileDiff, LineRange


def estimate_tokens(text: str) -> int:
    """Fast approximation of token count based on whitespace and punctuation."""
    # Standard heuristic: ~4 characters per token on average for source code
    return max(1, len(text) // 4)


def merge_overlapping_ranges(ranges: list[LineRange], context_padding: int = 3, max_line: int = 10_000_000) -> list[tuple[int, int]]:
    """Expand line ranges with context padding and merge overlapping intervals."""
    if not ranges:
        return []

    expanded = []
    for r in ranges:
        start = max(1, r.start - context_padding)
        end = min(max_line, r.end + context_padding)
        expanded.append((start, end))

    expanded.sort(key=lambda x: x[0])
    merged: list[tuple[int, int]] = [expanded[0]]

    for current in expanded[1:]:
        prev_start, prev_end = merged[-1]
        if current[0] <= prev_end + 1:
            # Overlapping or contiguous
            merged[-1] = (prev_start, max(prev_end, current[1]))
        else:
            merged.append(current)

    return merged


def extract_file_context(
    repo_root: Path,
    file_diff: FileDiff,
    context_padding: int = 3,
    max_lines_per_block: int = 300,
) -> list[CodeContextBlock]:
    """Extract bounded code blocks for a single reviewable file diff."""
    if not file_diff.is_reviewable:
        return []

    file_path = repo_root / file_diff.path
    if not file_path.is_file():
        # Fallback to hunks in diff if file does not exist on disk
        return extract_diff_hunks_context(file_diff)

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return extract_diff_hunks_context(file_diff)

    lines = content.splitlines(keepends=False)
    total_lines = len(lines)
    if total_lines == 0:
        return []

    if not file_diff.changed_line_ranges:
        # If no explicit ranges parsed, fallback to hunk ranges or first 100 lines
        return extract_diff_hunks_context(file_diff)

    merged_ranges = merge_overlapping_ranges(
        file_diff.changed_line_ranges,
        context_padding=context_padding,
        max_line=total_lines,
    )

    blocks: list[CodeContextBlock] = []
    for start, end in merged_ranges:
        # 1-indexed to 0-indexed slice
        slice_start = max(0, start - 1)
        slice_end = min(total_lines, end)

        if slice_end - slice_start > max_lines_per_block:
            # Truncate to upper bound to avoid blowing up context
            slice_end = slice_start + max_lines_per_block

        block_lines = lines[slice_start:slice_end]
        block_content = "\n".join(
            f"{slice_start + idx + 1:4d} | {line}" for idx, line in enumerate(block_lines)
        )

        ext = Path(file_diff.path).suffix.lstrip(".")
        blocks.append(
            CodeContextBlock(
                path=file_diff.path,
                start_line=slice_start + 1,
                end_line=slice_end,
                content=block_content,
                language=ext,
            )
        )

    return blocks


def extract_diff_hunks_context(file_diff: FileDiff) -> list[CodeContextBlock]:
    """Fallback: extract code context directly from raw diff hunks."""
    blocks: list[CodeContextBlock] = []
    ext = Path(file_diff.path).suffix.lstrip(".")

    for hunk in file_diff.hunks:
        content = "\n".join(hunk.lines)
        start = hunk.new_start if hunk.new_start > 0 else 1
        end = start + max(1, hunk.new_length) - 1
        blocks.append(
            CodeContextBlock(
                path=file_diff.path,
                start_line=start,
                end_line=end,
                content=content,
                language=ext,
            )
        )
    return blocks


def select_review_context(
    repo_root: Path,
    reviewable_diffs: Sequence[FileDiff],
    max_tokens: int = 4000,
    context_padding: int = 3,
) -> list[CodeContextBlock]:
    """Select bounded review context across all changed files within the token budget."""
    all_blocks: list[CodeContextBlock] = []
    accumulated_tokens = 0

    for fdiff in reviewable_diffs:
        file_blocks = extract_file_context(
            repo_root,
            fdiff,
            context_padding=context_padding,
        )
        for block in file_blocks:
            block_tokens = estimate_tokens(block.content)
            if accumulated_tokens + block_tokens > max_tokens and all_blocks:
                # Stop if adding this block would exceed token budget
                break
            all_blocks.append(block)
            accumulated_tokens += block_tokens
            if accumulated_tokens >= max_tokens:
                break
        if accumulated_tokens >= max_tokens:
            break

    return all_blocks
