# Changelog

All notable changes to OpenReview AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-11-05

### Added
- **Restricted Docker Execution Sandbox (P0):**
  - Dedicated `DockerSandboxRunner` enforcing `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges:true`, `--read-only`, and unprivileged execution (`validator`, UID 10001).
  - Memory-backed bounded workspace (`/workspace:256m`, `/tmp:64m`) with size-limited disposable snapshot staging.
  - Fail-closed execution: untrusted patch scripts and tests strictly refuse to run on the host when Docker is unavailable.
  - Dedicated live Docker integration test suite (`tests/integration/test_docker_sandbox.py`).
  - Symlink escape prevention in snapshot copying (`copy_snapshot_preventing_symlink_escape`).
- **Production-Ready GitHub App Integration:**
  - Standardized webhook server (`WebhookServer`) with mandatory HMAC-SHA256 signature verification.
  - GitHub App JWT generator with RS256 cryptography and ephemeral installation access token management.
  - SQLite persistent job queue (`SQLiteJobQueue`) supporting crash recovery, lease-based concurrency, and retry limits.
  - Isolated review worker with container isolation and multi-format reporting.
- **Changed-Hunk Evidence Filtering:**
  - Unified finding normalizer mapping static findings strictly to modified diff line ranges.
  - Distinct categorization for newly introduced findings, pre-existing findings, and AI suggestions.
- **Deep Binary Inspection (REA Adapter):**
  - Real invocation of REA binary inspection with target file arguments.
  - Structural integrity parsing, insecure RWX segment detection, and compiler mitigation audits.
- **Process Runner Memory Protection:**
  - Real-time incremental bounded streaming for subprocess stdout/stderr.
  - Process tree termination on output overflow and POSIX OS-level memory limits (`setrlimit`).
- **CI & Deployment:**
  - GitHub Actions CI workflow for multi-version Python testing.
  - Docker sandbox security workflow executing live container verification.
  - Production multi-stage `Dockerfile` and `docker-compose.yml`.

