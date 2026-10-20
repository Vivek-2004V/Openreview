"""Unit tests for Git operations and repository status detection."""

import subprocess
import pytest
from pathlib import Path
from openreview.repo.git import (
    GitCommandError,
    GitError,
    GitRepoInfo,
    NotAGitRepositoryError,
    detect_repo_root,
    inspect_git_status,
    run_git_command,
    verify_revision_exists,
)
from openreview.cli import EXIT_INVALID_INPUT_OR_CONFIG, EXIT_SUCCESS, main


@pytest.fixture
def temp_git_repo(tmp_path):
    """Fixture that initializes a temporary Git repository with an initial commit."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    test_file = repo_dir / "README.md"
    test_file.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    return repo_dir


def test_detect_repo_root(temp_git_repo):
    """Verify repo root resolution from root and nested subdirectories."""
    nested = temp_git_repo / "sub" / "dir"
    nested.mkdir(parents=True)

    assert detect_repo_root(temp_git_repo) == temp_git_repo.resolve()
    assert detect_repo_root(nested) == temp_git_repo.resolve()


def test_detect_repo_root_non_git_dir(tmp_path):
    """Verify NotAGitRepositoryError when target path is not a git repository."""
    non_git = tmp_path / "empty_dir"
    non_git.mkdir()
    with pytest.raises(NotAGitRepositoryError, match="not a git repository"):
        detect_repo_root(non_git)


def test_detect_repo_root_non_existent_dir(tmp_path):
    """Verify NotAGitRepositoryError when path does not exist."""
    missing = tmp_path / "does_not_exist"
    with pytest.raises(NotAGitRepositoryError, match="does not exist"):
        detect_repo_root(missing)


def test_inspect_git_status_clean(temp_git_repo):
    """Verify status reporting on clean git repository."""
    info = inspect_git_status(temp_git_repo)
    assert info.root == temp_git_repo.resolve()
    assert info.head_ref == "main"
    assert len(info.head_commit) == 40
    assert info.is_dirty is False
    assert info.uncommitted_files == []


def test_inspect_git_status_dirty(temp_git_repo):
    """Verify status reporting with uncommitted changes."""
    untracked = temp_git_repo / "untracked.py"
    untracked.write_text("print('hello')\n", encoding="utf-8")

    info = inspect_git_status(temp_git_repo)
    assert info.is_dirty is True
    assert "untracked.py" in info.uncommitted_files


def test_verify_revision_exists(temp_git_repo):
    """Verify checking valid vs invalid git references."""
    assert verify_revision_exists("main", temp_git_repo) is True
    assert verify_revision_exists("HEAD", temp_git_repo) is True
    assert verify_revision_exists("non-existent-branch-12345", temp_git_repo) is False


def test_cli_review_in_git_repo(temp_git_repo, capsys):
    """Verify CLI review runs successfully in a valid git repository."""
    exit_code = main(["review", "--path", str(temp_git_repo)])
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "Target repository:" in captured.out
    assert "branch: main" in captured.out


def test_cli_review_in_non_git_dir(tmp_path, capsys):
    """Verify CLI review returns exit code 2 when run on a non-git directory."""
    non_git = tmp_path / "not_git"
    non_git.mkdir()
    exit_code = main(["review", "--path", str(non_git)])
    assert exit_code == EXIT_INVALID_INPUT_OR_CONFIG
    captured = capsys.readouterr()
    assert "Repository error:" in captured.err
