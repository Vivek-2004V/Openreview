"""Unit tests for handling renames, deletions, binary, and oversized files per E2-T3."""

import pytest
from openreview.repo import (
    FileChangeType,
    LineRange,
    parse_unified_diff,
)

SAMPLE_RENAMED_DIFF = """diff --git a/old_name.py b/new_name.py
similarity index 95%
rename from old_name.py
rename to new_name.py
index e69de29..b201a45 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,4 @@
 def func():
+    # renamed file tweak
     return 42
"""

SAMPLE_DELETED_DIFF = """diff --git a/obsolete.py b/obsolete.py
deleted file mode 100644
index e69de29..0000000
--- a/obsolete.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def dead_code():
-    pass
"""

SAMPLE_BINARY_DIFF = """diff --git a/assets/logo.png b/assets/logo.png
index e69de29..b201a45 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""


def test_handle_renamed_file():
    """Verify renamed file change type and old/new path resolution."""
    parsed = parse_unified_diff(SAMPLE_RENAMED_DIFF)
    assert parsed.total_files == 1
    file_diff = parsed.files[0]

    assert file_diff.change_type == FileChangeType.RENAMED
    assert file_diff.old_path == "old_name.py"
    assert file_diff.path == "new_name.py"
    assert file_diff.is_binary is False
    assert file_diff.is_reviewable is True
    assert parsed.reviewable_paths == ["new_name.py"]


def test_handle_deleted_file():
    """Verify deleted file is not included in reviewable files."""
    parsed = parse_unified_diff(SAMPLE_DELETED_DIFF)
    assert parsed.total_files == 1
    file_diff = parsed.files[0]

    assert file_diff.change_type == FileChangeType.DELETED
    assert file_diff.is_reviewable is False
    assert parsed.changed_paths == []
    assert parsed.reviewable_paths == []


def test_handle_binary_file():
    """Verify binary files are detected and excluded from text review."""
    parsed = parse_unified_diff(SAMPLE_BINARY_DIFF)
    assert parsed.total_files == 1
    file_diff = parsed.files[0]

    assert file_diff.is_binary is True
    assert file_diff.is_reviewable is False
    assert len(file_diff.hunks) == 0
    assert parsed.reviewable_paths == []


def test_handle_oversized_file():
    """Verify diffs exceeding max_file_bytes are flagged and excluded from review."""
    large_patch = (
        "diff --git a/huge.py b/huge.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/huge.py\n"
        "+++ b/huge.py\n"
        "@@ -1,1 +1,1 @@\n" + ("+" + "x" * 200 + "\n") * 10
    )

    # Set threshold low (500 bytes) to trigger limit
    parsed = parse_unified_diff(large_patch, max_file_bytes=500)
    assert parsed.total_files == 1
    file_diff = parsed.files[0]

    assert file_diff.is_oversized is True
    assert file_diff.is_reviewable is False
    assert "File diff size exceeds limit" in str(file_diff.warning)
    assert parsed.reviewable_paths == []


def test_combined_diff_reviewable_filtering():
    """Verify reviewable_files filters out deleted, binary, and oversized while keeping valid files."""
    combined = "\n".join([SAMPLE_RENAMED_DIFF, SAMPLE_DELETED_DIFF, SAMPLE_BINARY_DIFF])
    parsed = parse_unified_diff(combined)

    assert parsed.total_files == 3
    # Only the renamed file is text-reviewable
    assert len(parsed.reviewable_files) == 1
    assert parsed.reviewable_files[0].path == "new_name.py"
    assert parsed.reviewable_paths == ["new_name.py"]
