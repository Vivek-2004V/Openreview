"""Unit tests for unified diff parsing and line range calculation."""

import subprocess
import pytest
from openreview.repo import (
    DiffRequest,
    FileChangeType,
    LineRange,
    extract_git_diff,
    parse_unified_diff,
)


SAMPLE_MODIFIED_DIFF = """diff --git a/src/app.py b/src/app.py
index e69de29..b201a45 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,4 +10,6 @@ def example():
     print("old line")
+    print("new line 1")
+    print("new line 2")
     return True
"""

SAMPLE_MULTI_HUNK_DIFF = """diff --git a/main.py b/main.py
index e69de29..b201a45 100644
--- a/main.py
+++ b/main.py
@@ -5,3 +5,4 @@ import sys
+import os
 import time
@@ -20,3 +21,5 @@ def run():
-    pass
+    val = compute()
+    return val
"""


def test_line_range_bounds():
    """Verify LineRange validation and contains method."""
    lr = LineRange(10, 15)
    assert lr.start == 10
    assert lr.end == 15
    assert lr.contains(10) is True
    assert lr.contains(12) is True
    assert lr.contains(15) is True
    assert lr.contains(9) is False
    assert lr.contains(16) is False

    with pytest.raises(ValueError, match="start must be >= 1"):
        LineRange(0, 5)

    with pytest.raises(ValueError, match="cannot be less than start"):
        LineRange(10, 5)


def test_parse_empty_diff():
    """Verify parsing empty diff returns 0 files and 0 stats."""
    parsed = parse_unified_diff("")
    assert parsed.total_files == 0
    assert parsed.total_additions == 0
    assert parsed.total_deletions == 0
    assert parsed.files == []


def test_parse_single_file_modification():
    """Verify single file modification with additions."""
    parsed = parse_unified_diff(SAMPLE_MODIFIED_DIFF)
    assert parsed.total_files == 1
    assert parsed.total_additions == 2
    assert parsed.total_deletions == 0

    file_diff = parsed.files[0]
    assert file_diff.path == "src/app.py"
    assert file_diff.change_type == FileChangeType.MODIFIED
    assert len(file_diff.hunks) == 1
    assert len(file_diff.changed_line_ranges) == 1
    assert file_diff.changed_line_ranges[0] == LineRange(start=11, end=12)


def test_parse_multi_hunk_diff():
    """Verify multiple hunks produce separate line ranges."""
    parsed = parse_unified_diff(SAMPLE_MULTI_HUNK_DIFF)
    assert parsed.total_files == 1
    assert parsed.total_additions == 3
    assert parsed.total_deletions == 1

    file_diff = parsed.files[0]
    assert len(file_diff.hunks) == 2
    assert len(file_diff.changed_line_ranges) == 2
    assert file_diff.changed_line_ranges[0] == LineRange(start=5, end=5)
    assert file_diff.changed_line_ranges[1] == LineRange(start=21, end=22)


def test_extract_git_diff_live_repo(tmp_path):
    """Verify live git diff extraction on a real Git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)

    file_a = repo / "hello.py"
    file_a.write_text("line 1\nline 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.py"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

    # Modify file_a in working tree
    file_a.write_text("line 1\nline 2 modified\nline 3 added\n", encoding="utf-8")

    req = DiffRequest.for_working_tree(repo_root=repo)
    parsed = extract_git_diff(req)

    assert parsed.total_files == 1
    assert parsed.files[0].path == "hello.py"
    assert len(parsed.files[0].changed_line_ranges) >= 1
    assert parsed.changed_paths == ["hello.py"]
