# OpenReview AI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Tests: 225 Unit + 8 Docker Live Passing](https://img.shields.io/badge/tests-233%20passing-brightgreen)](tests/)

A **free-first, open-source, local-first AI code-review assistant** designed as a privacy-preserving alternative to SaaS tools like CodeRabbit.

OpenReview AI runs deterministic static linters (Ruff, ESLint, REA binary inspector) and optional local LLMs (via Ollama) entirely on your developer workstation or self-hosted CI/CD without telemetry, proprietary API paywalls, or data exfiltration.

---

## Key Features

- **P0 Restricted Docker Execution Sandbox:** Untrusted patch validations, test suites, and repository code execute strictly inside hardened, air-gapped Docker containers (`openreview-patch-validator`). Zero host fallback on missing runtime.
- **Local-First & Air-Gap Capable:** Zero telemetry. Core review engine, deterministic linters, and default local AI operate on-device without remote calls. (See [Privacy Architecture](docs/PRIVACY.md) for endpoint specifics).
- **Deterministic-First:** Hard compiler/linter diagnostics (`origin: static`) are strictly separated from local AI suggestions (`origin: ai`, `status: unverified`).
- **Changed-Hunk Evidence Filtering:** Reviews focus strictly on modified diff hunks. Pre-existing findings outside changed code are separately classified.
- **Deep Binary Inspection (REA Adapter):** Real binary inspection analyzing PE, ELF, and Mach-O structures, RWX segments, and compiler mitigations.
- **Secret Redaction (Defense-in-Depth):** High-risk credentials (AWS keys, GitHub PATs, JWTs, private keys) are automatically redacted before model dispatch.
- **Bounded Process Streaming:** Subprocess runners incrementally bound stdout/stderr with POSIX memory limits and process tree termination.
- **Production-Ready GitHub App:** Constant-time HMAC-SHA256 signature verification, crash-resilient SQLite persistent queue, and diff-mapped inline comments.

---

## Quickstart

### 1. Installation

```bash
# Clone repository (including submodules)
git clone --recurse-submodules https://github.com/Vivek-2004V/Openreview.git
cd Openreview

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install with development dependencies
pip install -e ".[dev]"
pip install -r requirements.txt
```

### 2. Build the Sandbox Validator Docker Image

Untrusted patch testing requires the local validator container image:

```bash
docker build -t openreview-patch-validator:latest -f Dockerfile.validator .

# Verify image security invariants (must run as unprivileged non-root user)
docker image inspect openreview-patch-validator:latest --format 'User: {{.Config.User}}'
# Expected output: User: validator (UID 10001)
```

### 3. Verify Tooling Environment

```bash
openreview doctor
```

Sample output:
```text
OpenReview AI Environment Doctor:
---------------------------------
  - Git                   : OK (/usr/bin/git)
  - Ruff (Python)         : OK (/usr/local/bin/ruff)
  - ESLint (JS/TS)        : OK (/usr/local/bin/eslint)
  - Ollama (Local AI)     : OK (/usr/local/bin/ollama)
  - Docker Sandbox        : OK (openreview-patch-validator:latest)
```

### 4. Run a Code Review

```bash
# Review uncommitted changes in current working tree
openreview review

# Review against a specific base git revision
openreview review --base main

# Output structured JSON report to a file
openreview review -f json -o report.json

# Enable optional local AI review via Ollama
openreview review --ai --model qwen2.5-coder:7b
```

---

## Docker Execution Sandbox (P0 Security Architecture)

To safely test untrusted pull requests and patch proposals without endangering the host system, OpenReview AI executes tests inside a disposable, restricted container sandbox:

```
[Host System] ──(Read-Only Snapshot Mount)──> [/snapshot:ro]
                                                     │
                                            (cp -R in tmpfs)
                                                     ▼
                                          [/workspace (tmpfs, 256MB RAM)]
                                          Executes tests as UID 10001
                                          • --network none
                                          • --cap-drop ALL
                                          • --security-opt no-new-privileges:true
                                          • --read-only rootfs
                                          • 512MB RAM / 1 CPU / 100 PIDs
```

### Sandbox Security Invariants:
1. **Network Airgap:** `--network none` strictly blocks all outbound network connections (`Errno 101: Network is unreachable`).
2. **Non-Root Execution:** Runs as user `validator` (`UID 10001:10001`). Root execution is explicitly rejected.
3. **Privilege Defense:** All Linux capabilities are dropped (`--cap-drop ALL`), and `--security-opt no-new-privileges:true` prevents privilege escalation.
4. **Immutable Filesystem:** Container root filesystem is mounted read-only (`--read-only`). Disposable workspaces use memory-backed tmpfs (`/workspace:256m,mode=1777` and `/tmp:64m,mode=1777`).
5. **No Host Fallback (Fail-Closed):** If Docker is unavailable or the image is missing, the validator returns `ScannerStatus.ERROR` and refuses to run untrusted code on the host.
6. **Symlink Escape Protection:** External symlinks resolving outside the repository root are filtered out during snapshot creation.
7. **Cryptographic Immutability:** Pre- and post-validation SHA-256 tree hashes ensure the original host repository is never modified.

---

## Running Automated Tests

```bash
# Run unit and regression test suite (225 tests)
pytest -q

# Run live Docker execution sandbox tests (launches real containers)
pytest -v tests/integration/test_docker_sandbox.py
```

---

## Repository Structure & Submodules

- **`src/openreview/`**: Core engine, patch proposals, container runners, and adapters.
- **`tests/`**: Unit tests and `tests/integration/` (live Docker execution tests).
- **`deploy/`**: Production server `Dockerfile`, `docker-compose.yml`, and `openreview.service`.
- **`rea/`**: Git submodule pointing to [`https://github.com/morluto/rea`](https://github.com/morluto/rea) (REA binary inspection engine).
- **`OpenReviewAI_Vibe_Coding_Document_Pack/`**: Initial architecture briefs, task backlogs, and loop engineering specifications preserved for reference.

---

## Known Limitations

1. **Kernel Sharing:** Docker containers share the host operating system kernel. While capabilities and syscalls are restricted, containers do not provide the hardware-level boundary of a Type-1 hypervisor.
2. **Resource & Disk Limits:** Repository snapshots are capped at 50 MB on the host, and disposable tmpfs container workspaces are capped at 256 MB in RAM.
3. **Local AI Model Dependency:** Ollama inference requires an operational local Ollama daemon; if unavailable, the engine falls back gracefully to deterministic static scanners.

---

## License

OpenReview AI is licensed under the [MIT License](LICENSE).
