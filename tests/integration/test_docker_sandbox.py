"""Integration tests for Docker execution sandbox.

Alias to test_patch_docker_live_integration for standard test path compatibility.
"""

from __future__ import annotations

import os

import pytest

from openreview.patch.docker_sandbox import DockerSandboxRunner
from tests.test_patch_docker_live_integration import (
    test_docker_live_invalid_python_syntax_fails,
    test_docker_live_malicious_test_cannot_modify_original_repository,
    test_docker_live_missing_image_returns_error,
    test_docker_live_network_access_blocked,
    test_docker_live_original_repository_hash_remains_unchanged,
    test_docker_live_output_limits,
    test_docker_live_timeout_limits,
    test_docker_live_valid_patch_execution_succeeds,
)

runner_check = DockerSandboxRunner()
DOCKER_LIVE = runner_check.is_available() and runner_check.image_exists()
LIVE_REQUIRED = os.environ.get("OPENREVIEW_REQUIRE_DOCKER", "0").lower() in ("1", "true", "yes")

if LIVE_REQUIRED and not DOCKER_LIVE:
    pytest.fail(
        "Docker daemon or 'openreview-patch-validator:latest' image is required by "
        "OPENREVIEW_REQUIRE_DOCKER=1 but is not available or not running. "
        "Failing fast to prevent silent CI bypass."
    )

pytestmark = pytest.mark.skipif(
    not DOCKER_LIVE,
    reason="Docker daemon or 'openreview-patch-validator:latest' image is not available",
)

__all__ = [
    "test_docker_live_invalid_python_syntax_fails",
    "test_docker_live_malicious_test_cannot_modify_original_repository",
    "test_docker_live_missing_image_returns_error",
    "test_docker_live_network_access_blocked",
    "test_docker_live_original_repository_hash_remains_unchanged",
    "test_docker_live_output_limits",
    "test_docker_live_timeout_limits",
    "test_docker_live_valid_patch_execution_succeeds",
]
