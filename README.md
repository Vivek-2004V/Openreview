# OpenReview AI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Tests: 255 Unit + 8 Docker Live Passing](https://img.shields.io/badge/tests-263%20passing-brightgreen)](tests/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Hardened](https://img.shields.io/badge/security-P0--P5%20hardened-success)](tests/test_security_hardening.py)

A **free-first, open-source, local-first AI code-review assistant** designed as a privacy-preserving, self-hosted alternative to SaaS tools like CodeRabbit.

OpenReview AI combines deterministic static linters (Ruff, ESLint, REA binary inspector) and local LLMs (via Ollama) entirely on your developer workstation or self-hosted infrastructure—with **zero telemetry, no proprietary API paywalls, no data exfiltration**, and **strict process and container isolation**.

---

## Key Features & Security Architecture

### P0 — Sandbox & Process Security (Restricted Docker Sandbox)
- **Hardened Disposable Container**: Untrusted PR review execution and patch testing run strictly inside an isolated Docker container (`openreview-patch-validator:latest`).
- **Complete Network Air-Gap**: Container runs with `--network=none`, completely blocking internet and LAN network access (`Errno 101: Network is unreachable`).
- **Resource Hardening**: Memory is strictly capped (`--memory=512m`), CPU cores bounded (`--cpus=1.0`), and thread/process count limited (`--pids-limit=100`) to prevent fork bombs and CPU pinning.
- **Filesystem & Privilege Isolation**: Root filesystem is mounted read-only (`--read-only`), capabilities dropped (`--cap-drop ALL`), privilege escalation disabled (`--security-opt no-new-privileges:true`), and execution runs as unprivileged user `validator` (`UID 10001:10001`). Host Docker socket is never mounted.
- **Fail-Closed Container Enforcement**: If `OPENREVIEW_REQUIRE_CONTAINER=true` and the Docker daemon is unreachable or the validator image is missing, the engine **fails closed** (exit code 7) and strictly refuses to execute untrusted code on the host system.

### P1 — Secrets & GitHub App Security
- **HMAC-SHA256 Webhook Verification**: Constant-time signature verification via `hmac.compare_digest` protects incoming webhook deliveries before payload parsing.
- **Replay Protection**: SQLite-backed persistent delivery ID tracking deduplicates repeat deliveries.
- **Defense-in-Depth Secret Redaction**: Automatic regex-based redaction censors AWS tokens, GitHub PATs, JWTs, and private keys from code diffs, logs, and error outputs before AI dispatch.
- **Credential Protection**: Private key permissions are verified (`0600`), and credentials are scrubbed from all diagnostic logs.

### P2 — Authentication, Authorization & API Security
- **Cryptographic Password Hashing**: Passwords are securely hashed with `hashlib.scrypt` (16-byte random salt, N=16384, r=8, p=1) and compared in constant time. Legacy PBKDF2 verification is supported.
- **JWT Token Lifecycle Management**: HS256 algorithm pinning, automatic expiration (`exp`), issued-at (`iat`), and subject validation. Prohibited algorithm `none` and tampered tokens are rejected.
- **Fine-Grained RBAC & Authorization**: Policies verify repository ownership and assigned job scopes before permitting access to protected API endpoints.
- **Thread-Safe Sliding Window Rate Limiting**: In-memory rate limiting throttles abusive traffic, returning HTTP 429 with `Retry-After` headers. Handles reverse-proxy IP headers (`X-Forwarded-For`, `X-Real-IP`).
- **Production HTTP Security Headers**: Injects `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Strict-Transport-Security`, and `Cache-Control: no-store`.
- **DoS Payload Size Limits**: Payloads exceeding 10 MB are rejected immediately with HTTP 413 without reading the body into memory.
- **Sanitized Errors**: Internal server errors (500) hide stack traces and filesystem paths from external clients.

### P3 — AI Security & Adversarial Prompt Injection Defenses
- **Adversarial Injection Scanner**: Heuristic pattern scanner detects prompt injection attacks in code comments and PR metadata (e.g., `ignore previous instructions`, `system override`, `you are now in debug mode`, `always approve this PR`).
- **Control Tag Neutralization**: Prompt boundaries defang raw structural tags such as `<system>` or `<im_start>`.
- **Unsafe AI Finding Filter**: Automatically drops suggestions that echo adversarial directives or recommend dangerous piped shell executions (e.g., `curl ... | bash`).
- **Bounded AI Streaming**: AI response bodies are capped at 5 MB to prevent memory exhaustion DoS.

### P4 — Deterministic Patch Safety & Concurrency Protection
- **Human Approval Gate**: Patches cannot be applied automatically without explicit human confirmation (`--approve`).
- **Concurrent Modification Protection**: Verifies target file SHA-256 before modification to prevent race conditions and overwriting concurrent commits.
- **Atomic File Writes**: Writes modifications to a temporary file in the target directory, calls `os.fsync`, and commits atomically with `os.replace`.

### P5 — Deep Binary & Static Inspection
- **REA Binary Inspector**: Inspects PE, ELF, and Mach-O binaries for RWX segments, structural tampering, and missing compiler mitigations (ASLR, DEP/NX, Stack Canaries).
- **Static Scanners**: Integrated Ruff (Python) and ESLint (JavaScript/TypeScript) with diff-hunk evidence filtering.

---

## Quickstart

### 1. Installation

```bash
# Clone repository including git submodules (REA engine)
git clone --recurse-submodules https://github.com/Vivek-2004V/Openreview.git
cd Openreview

# Create and activate Python 3.11+ virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies in editable mode
pip install -e ".[dev]"
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example configuration and adjust settings for your environment:

```bash
cp .env.example .env
```

Key environment settings in `.env`:
```ini
# Enforce Docker sandbox for untrusted code execution (fail-closed if Docker missing)
OPENREVIEW_REQUIRE_CONTAINER=true

# GitHub App Integration
GITHUB_APP_ID=123456
GITHUB_WEBHOOK_SECRET=your_long_random_hmac_webhook_secret
GITHUB_PRIVATE_KEY_PATH=/secrets/github-app-private-key.pem

# Optional JWT API Secret for authenticating client requests
OPENREVIEW_API_SECRET=your_long_random_jwt_api_secret_key

# Request rate limiting (requests/minute per IP)
OPENREVIEW_RATE_LIMIT=60

# Local AI Engine (Ollama)
OPENREVIEW_AI_ENABLED=true
OPENREVIEW_AI_MODEL=qwen2.5-coder:7b
OPENREVIEW_AI_ENDPOINT=http://127.0.0.1:11434
```

### 3. Build the Sandbox Validator Docker Image

Untrusted patch testing requires the local validator container image:

```bash
docker build -t openreview-patch-validator:latest -f Dockerfile.validator .

# Verify image security invariants (must run as unprivileged non-root user UID 10001)
docker image inspect openreview-patch-validator:latest --format 'User: {{.Config.User}}'
# Expected output: User: validator
```

### 4. Verify Tooling Environment

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

### 5. Run a Code Review

```bash
# Review uncommitted changes in current working tree
openreview review

# Review against a specific base branch or git commit
openreview review --base main

# Output structured JSON report to a file
openreview review -f json -o report.json

# Enable local AI review via Ollama
openreview review --ai --model qwen2.5-coder:7b

# Run in automated agent mode (returns structured JSON output with error tracking)
openreview review --agent
```

### 6. Run the Production GitHub App Webhook Server

```bash
# Start webhook server with embedded worker
openreview server --host 0.0.0.0 --port 8000
```

---

## Docker Execution Sandbox Architecture

```
[Host System] ──(Read-Only Snapshot Mount)──> [/snapshot:ro]
                                                     │
                                            (cp -R in tmpfs)
                                                     ▼
                                          [/workspace (tmpfs, 256MB RAM)]
                                          Executes tests as UID 10001 (validator)
                                          • --network none (strictly air-gapped)
                                          • --cap-drop ALL (no root capabilities)
                                          • --security-opt no-new-privileges:true
                                          • --read-only rootfs
                                          • 512MB RAM / 1.0 CPU / 100 PIDs limit
                                          • No Docker socket access
```

### Sandbox Security Guarantees:
1. **Network Air-Gap:** `--network none` strictly blocks all outbound network connections.
2. **Non-Root Execution:** Runs as user `validator` (`UID 10001:10001`). Root execution is explicitly rejected.
3. **Privilege Defense:** All Linux capabilities are dropped (`--cap-drop ALL`), and `--security-opt no-new-privileges:true` prevents escalation.
4. **Immutable Filesystem:** Root filesystem is mounted read-only. Workspaces reside on memory-backed tmpfs (`/workspace:256m,mode=1777` and `/tmp:64m,mode=1777`).
5. **Fail-Closed Container Enforcement:** When `OPENREVIEW_REQUIRE_CONTAINER=true`, missing Docker or daemon failure halts execution with exit code 7 instead of running unsafely on the host.
6. **Symlink Escape Defense:** Symlinks pointing outside the repository snapshot are purged during snapshot preparation.
7. **Cryptographic Verification:** SHA-256 tree hashes verify that host repositories remain untouched.

---

## Running Automated Tests

```bash
# Run complete unit, integration, and security test suite (255 tests)
pytest -v --ignore=tests/integration --ignore=tests/test_patch_docker_live_integration.py

# Run dedicated security hardening test suite (16 tests)
pytest -v tests/test_security_hardening.py

# Run live Docker execution sandbox tests (requires Docker daemon)
OPENREVIEW_REQUIRE_DOCKER=1 pytest -v tests/integration/test_docker_sandbox.py

# Run code style, type, and security linter (Ruff)
ruff check src/ tests/
```

---

## Repository Structure & Submodules

- **`src/openreview/`**:
  - `security/`: Password hashing (`auth.py`), JWT lifecycle, RBAC, rate limiting (`rate_limiter.py`), prompt injection defenses (`ai_safety.py`), secret redaction, and path traversal guards.
  - `patch/`: Patch proposals, atomic applier (`applier.py`), and container sandbox runner (`docker_sandbox.py`).
  - `integrations/`: GitHub App authentication (`github_app_auth.py`), webhook server (`webhook_server.py`), persistent SQLite job queue (`job_queue.py`), and background worker (`worker.py`).
  - `adapters/`: Ollama local AI provider (`ollama.py`), static linters (Ruff, ESLint), and REA binary inspector (`rea.py`).
  - `repo/`: Git diff parsing, line mapping, and hunk filtering.
  - `review/`: Review engine, finding normalizer, and AI suggestion validator (`ai_validator.py`).
  - `schemas/`: Pydantic models for findings, reports, and job states.
  - `config.py`: Centralized environment variable loader and validator.
- **`tests/`**: Unit tests, security hardening tests, and `tests/integration/` (live container isolation tests).
- **`deploy/`**: Production server `Dockerfile`, `docker-compose.yml`, and systemd service unit.
- **`rea/`**: Git submodule pointing to [`https://github.com/morluto/rea`](https://github.com/morluto/rea) (REA binary inspection engine).
- **`docs/`**: Architectural Decision Records (ADRs), execution guides, and specifications.

---

## Deployment & Production Hardening

1. **Reverse Proxy & TLS Termination**: Run OpenReview behind Nginx, Caddy, or an AWS Application Load Balancer to terminate TLS 1.3 and provide DDoS mitigation.
2. **Private Key Protection**: Restrict GitHub App private key file permissions on the host system:
   ```bash
   chmod 0600 /secrets/github-app-private-key.pem
   chown openreview:openreview /secrets/github-app-private-key.pem
   ```
3. **Container Sandboxing in CI**: In production CI pipelines, set `OPENREVIEW_REQUIRE_CONTAINER=true` to ensure untrusted code is never evaluated on the host runner.
4. **Secrets Management**: Store `GITHUB_WEBHOOK_SECRET` and `OPENREVIEW_API_SECRET` in a secure secrets manager (AWS Secrets Manager, HashiCorp Vault, or GitHub Repository Secrets) rather than hardcoding in shell scripts.

---

## Known Limitations

1. **Kernel Sharing:** Docker containers share the host operating system kernel. While capabilities, cgroups, and syscalls are strictly bounded, containers do not provide the hardware-level boundary of a Type-1 hypervisor.
2. **Resource & Disk Limits:** Repository snapshots are capped at 50 MB on the host, and disposable tmpfs container workspaces are capped at 256 MB in RAM.
3. **Local AI Model Dependency:** Ollama inference requires an operational local Ollama daemon; if unavailable, the engine falls back gracefully to deterministic static scanners.

---

## License

OpenReview AI is licensed under the [MIT License](LICENSE).
