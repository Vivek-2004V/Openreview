# OpenReview AI Privacy & Security Architecture

OpenReview AI is architected from day one to guarantee complete source code privacy, data sovereignty, and offline resilience.

---

## 1. Zero Telemetry & Air-Gapped Operation

- **No Remote Network Calls:** OpenReview AI never phones home. There are no tracking beacons, analytics endpoints, or remote error reporting services.
- **Air-Gapped Ready:** The core engine, diff parser, report renderer, and static scanners (Ruff, ESLint) function 100% offline without any internet connection.
- **Local AI Daemon Only:** When AI mode is enabled, queries are directed exclusively to the local loopback address (`http://127.0.0.1:11434`) running your self-hosted Ollama instance. No source code or prompts ever leave your machine.

---

## 2. Automatic Secret Redaction

Before code context is passed to the local AI model, it passes through an automated regex-based redaction filter (`src/openreview/security/redaction.py`).

The redaction engine detects and masks:
- **Cloud Provider Credentials:** AWS Access Key IDs (`AKIA...`), AWS Secret Keys
- **Private Keys:** RSA, EC, and OpenSSH private key blocks (`-----BEGIN ... PRIVATE KEY-----`)
- **Tokens & PATs:** GitHub Personal Access Tokens (`ghp_...`), GitLab tokens, Slack tokens (`xoxb-...`)
- **Session & Auth:** JWT tokens (`eyJ...`), Bearer authorization headers
- **Hardcoded Passwords:** Password assignments in variables and configuration files

All detected credentials are substituted with `[REDACTED_SECRET]` while preserving the surrounding variable assignment and syntax structure.

---

## 3. Strict Sandboxing & Subprocess Security

- **No `shell=True`:** All external tools (Git, Ruff, ESLint) are launched using strictly validated argument vectors (`Sequence[str]`) without shell interpolation.
- **Control Character Rejection:** Any command argument containing null bytes (`\0`), carriage returns (`\r`), or newlines (`\n`) is immediately rejected before subprocess invocation.
- **Path Traversal Guards:** File paths are validated against repository boundaries to prevent directory traversal attacks (`../../`) or symlink-based boundary escapes.

---

## 4. Unverified AI Finding Quarantine

Per ADR-002:
- Static findings from compilers and linters are classified as `origin: static` with verified status.
- AI model outputs are quarantined as `origin: ai` and `status: unverified`.
- Reports clearly badge AI findings as `[Local AI Suggestion (Unverified)]` to ensure developers never confuse speculative model suggestions with deterministic linter diagnostics.
