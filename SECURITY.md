# Security Policy

## Reporting Security Vulnerabilities

We take the security of OpenReview AI very seriously. If you discover a vulnerability or security defect, please **do not open a public GitHub issue**.

Instead, please report it privately:
- **Email:** security@openreview.ai (or submit via GitHub Security Advisory)
- **Response Timeline:** We acknowledge receipts within 48 hours and aim to provide a remediation plan within 5 business days.

---

## Architecture & Security Boundaries

OpenReview AI inspects potentially untrusted code from pull requests and patch proposals. To prevent arbitrary code execution on host machines, the application adheres to strict architectural isolation controls:

### 1. Fail-Closed Execution Isolation (P0)
- **Container Boundary:** Untrusted tests, repository scripts, and patch verifications run strictly inside restricted Docker execution containers (`openreview-patch-validator`).
- **No Host Fallback:** If Docker or the validation image is unavailable, execution fails closed with `ScannerStatus.ERROR`. The application **never** falls back to executing untrusted repository code directly on the host machine.
- **Airgap Isolation:** All validation containers run with `--network none`.
- **Rootless & Capability-Free:** Containers drop all Linux capabilities (`--cap-drop ALL`), enforce `--security-opt no-new-privileges:true`, and execute as an unprivileged user (`validator`, UID `10001`).
- **Immutable Rootfs:** Containers mount the root filesystem as read-only (`--read-only`). Disposable workspaces use size-bounded RAM tmpfs (`/workspace:256m`, `/tmp:64m`).
- **Cryptographic Repo Immutability:** Original repositories are mounted strictly read-only (`ro`). Pre- and post-validation cryptographic hash trees (SHA-256) ensure original files remain 100% immutable.
- **Symlink Escape Guard:** Repository snapshots filter out any symlinks pointing outside the repository boundary before staging.

### 2. Privacy & Secret Redaction
- **Local-First Default:** AI analysis connects by default to a local Ollama instance (`http://127.0.0.1:11434`).
- **Credential Hygiene:** Prior to sending context blocks to LLMs, regex-based secret redaction masks API tokens, private keys, AWS credentials, and authorization headers.
- **Zero Host Leaks:** Host environment secrets, credentials, and `$HOME` directories are prohibited from container mounting.

---

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

