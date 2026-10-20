"""Baseline tests verifying package integrity and structure."""

import importlib
import sys
import openreview
from openreview import __version__
from openreview.cli import main


def test_package_version():
    """Verify package version is defined and matches semantic versioning format."""
    assert __version__ == "0.1.0"
    assert isinstance(__version__, str)


def test_submodules_exist():
    """Verify all architectural module boundaries are importable."""
    expected_modules = [
        "openreview.repo",
        "openreview.review",
        "openreview.adapters",
        "openreview.schemas",
        "openreview.reports",
        "openreview.security",
        "openreview.storage",
    ]
    for mod_name in expected_modules:
        mod = importlib.import_module(mod_name)
        assert mod is not None


def test_cli_version_flag(capsys):
    """Verify CLI baseline --version returns version string and exits with 0."""
    exit_code = main(["--version"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert f"openreview {__version__}" in captured.out


def test_documentation_and_adrs():
    """Verify ADRs and dependency inventory exist with all core decision records."""
    from pathlib import Path

    doc_dir = Path("docs")
    adr_file = doc_dir / "ADR.md"
    dep_file = doc_dir / "DEPENDENCIES_AND_LICENSES.md"
    audit_file = doc_dir / "REA_AUDIT.md"

    assert adr_file.exists(), "docs/ADR.md must exist"
    assert dep_file.exists(), "docs/DEPENDENCIES_AND_LICENSES.md must exist"
    assert audit_file.exists(), "docs/REA_AUDIT.md must exist"

    adr_text = adr_file.read_text(encoding="utf-8")
    for adr_id in ["ADR-001", "ADR-002", "ADR-003", "ADR-004", "ADR-005", "ADR-006"]:
        assert adr_id in adr_text, f"{adr_id} missing from ADR.md"

