# OpenReview AI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Tests: 126 Passing](https://img.shields.io/badge/tests-126%20passing-brightgreen)](tests/)

A **free-first, open-source, local-first AI code-review assistant** designed as a privacy-preserving alternative to SaaS tools like CodeRabbit.

OpenReview AI runs deterministic static linters (Ruff, ESLint, Bandit, Semgrep, REA) and optional local LLMs (via Ollama) entirely on your developer workstation or self-hosted CI/CD without telemetry, proprietary API paywalls, or data exfiltration.

---

## Product Roadmap

### 1. Core Review Engine (First Priority - Completed & Live)
- **Local Git Repository Review:** Seamless review of local working tree (staged & unstaged) and commit ranges.
- **Changed Lines & Diff Analysis:** Line-level hunk mapping and validation ensuring reviews focus strictly on changed code.
- **Bugs, Code Quality & Security Findings:** Deterministic scanner adapters (Ruff, ESLint, Bandit, Semgrep, REA binary inspector) + optional local AI (Ollama) with hallucination prevention.
- **Terminal & JSON Output:** Rich terminal markdown reports, direct findings filters (`openreview findings`), and agent-native JSON (`openreview review --agent`) for coding agents (Antigravity, Codex).

---

## Key Features

- **100% Local & Air-Gapped Ready:** Source code never leaves your workstation. Zero remote telemetry.
- **Deterministic-First:** Hard compiler/linter diagnostics (`origin: static`) are strictly separated from local AI suggestions (`origin: ai`, `status: unverified`).
- **Secret Redaction:** High-risk credentials (AWS keys, GitHub PATs, JWTs, private keys, passwords) are automatically redacted before model dispatch and logging.
- **Path Traversal & Command Injection Defense:** Strict boundary containment blocks directory traversal and command injection.
- **Performance Optimized:** Sub-20ms engine overhead. Diff parsing, hashing, and report rendering execute in single-digit milliseconds.
- **GitHub App & Webhook Integration:** Constant-time HMAC-SHA256 signature verification, idempotent review queue, isolated sandbox worker, and diff-mapped inline comments.
- **Safe Patch Proposals:** Unified diff proposals tested in disposable sandboxes with mandatory human approval gating per ADR-005.

---

## Quickstart

### 1. Installation

```bash
# Clone repository
git clone https://github.com/openreview/openreview-ai.git
cd openreview-ai

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install with development dependencies
pip install -e ".[dev]"
```

Or install the pre-built distribution wheel:
```bash
pip install dist/openreview_ai-0.1.0-py3-none-any.whl
```

### 2. Verify Tooling Environment

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
  - Bandit (Python AST)   : NOT FOUND (optional/adapter)
  - Semgrep (SAST)        : NOT FOUND (optional/adapter)
  - Ollama (Local AI)     : OK (/usr/local/bin/ollama)
```

### 3. Run a Code Review

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

## Configuration (`.openreview.toml`)

Create `.openreview.toml` in your repository root:

```toml
[review]
base_ref = "main"
changed_files_only = true
max_files = 100
max_file_bytes = 1000000

[scanners]
enabled = ["ruff", "eslint"]
timeout_seconds = 30

[ai]
enabled = false
provider = "ollama"
model = "qwen2.5-coder:7b"
endpoint = "http://127.0.0.1:11434"
max_context_tokens = 4000

[privacy]
redact_secrets = true

[reports]
formats = ["markdown"]
```

---

## Architecture & Epics Roadmap

OpenReview AI was developed using strict **Loop Engineering** and verified across 8 core epics:

| Epic | Module | Description | Test Coverage |
|---|---|---|:---:|
| **E0** | Foundation | Audited repository boundaries, established ADR-001 to ADR-006, and license inventory | 4 tests |
| **E1** | CLI Engine | Entrypoint, config parser, safe git status detection, exit codes 0-4 | 22 tests |
| **E2** | Diff & Schema | Unified diff parser, Finding v1.0 schema, Markdown/JSON renderers, golden fixtures | 23 tests |
| **E3** | Scanners | Deterministic Ruff & ESLint adapters, process timeout protection, output limits | 7 tests |
| **E4** | Local AI | Ollama adapter, secret redaction, context selection, hallucination rejection, static fallback | 14 tests |
| **E5** | Quality & Safety | Path traversal guards, redacting logger, performance benchmark, seeded defect evaluation | 30 tests |
| **E6** | GitHub Integration | HMAC webhooks, idempotent job queue, isolated sandbox worker, inline diff mapping | 8 tests |
| **E7** | Patch Proposals | Unified diff generation, sandbox validation, explicit human approval gating | 9 tests |
| **Total** | **Full Platform** | **All Backlog Tasks 100% Completed** | **117 Passed** |

---

## Complete Documentation Index

- [Setup & Installation Guide](docs/SETUP.md)
- [Configuration Reference](docs/CONFIGURATION.md)
- [Privacy & Security Architecture](docs/PRIVACY.md)
- [Troubleshooting & Exit Codes](docs/TROUBLESHOOTING.md)
- [Performance Baseline & Benchmarks](docs/BENCHMARK.md)
- [GitHub App Integration Architecture](docs/GITHUB_INTEGRATION.md)
- [GitHub Integration Security Review](docs/SECURITY_REVIEW_GITHUB.md)
- [End-to-End Evaluation Report](docs/E2E_EVALUATION.md)
- [Loop Engineering Process Template](docs/LOOP_TEMPLATE.md)
- [Architecture Decision Records (ADR)](docs/ADR.md)
- [Dependency & License Inventory](docs/DEPENDENCIES_AND_LICENSES.md)
- [REA Audit Report](docs/REA_AUDIT.md)

---

## Running the Automated Test Suite

```bash
.venv/bin/pytest
# ============================= 126 passed in 2.26s ==============================
```

## License

OpenReview AI is licensed under the [MIT License](LICENSE).
