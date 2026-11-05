"""Unit tests for DiffRequest and DiffTarget models."""

import pytest
from openreview.repo import (
    DiffRequest,
    DiffRequestError,
    DiffTarget,
)


def test_diff_request_defaults(tmp_path):
    """Verify default values for DiffRequest."""
    req = DiffRequest(repo_root=tmp_path)
    assert req.repo_root == tmp_path
    assert req.target == DiffTarget.WORKING_TREE
    assert req.base_ref is None
    assert req.head_ref is None
    assert req.paths == []
    assert req.max_files == 100
    assert req.max_file_bytes == 1_000_000
    assert req.context_lines == 3


def test_diff_request_for_working_tree(tmp_path):
    """Verify DiffRequest.for_working_tree factory method."""
    req = DiffRequest.for_working_tree(tmp_path, base_ref="HEAD~1", paths=["src/"])
    assert req.target == DiffTarget.WORKING_TREE
    assert req.base_ref == "HEAD~1"
    assert req.paths == ["src/"]


def test_diff_request_for_commits(tmp_path):
    """Verify DiffRequest.for_commits factory method."""
    req = DiffRequest.for_commits(tmp_path, base_ref="main", head_ref="feature-1")
    assert req.target == DiffTarget.COMMITS_RANGE
    assert req.base_ref == "main"
    assert req.head_ref == "feature-1"


def test_diff_request_validation_errors(tmp_path):
    """Verify bounds checking and validation rules."""
    with pytest.raises(DiffRequestError, match="max_files must be greater than 0"):
        DiffRequest(repo_root=tmp_path, max_files=0)

    with pytest.raises(DiffRequestError, match="max_file_bytes must be greater than 0"):
        DiffRequest(repo_root=tmp_path, max_file_bytes=-10)

    with pytest.raises(DiffRequestError, match="context_lines must be >= 0"):
        DiffRequest(repo_root=tmp_path, context_lines=-1)

    with pytest.raises(DiffRequestError, match="base_ref is required when target is COMMITS_RANGE"):
        DiffRequest(repo_root=tmp_path, target=DiffTarget.COMMITS_RANGE, base_ref=None)
