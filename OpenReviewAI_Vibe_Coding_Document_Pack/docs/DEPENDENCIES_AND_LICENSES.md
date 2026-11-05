# Dependency and License Inventory

**Project:** OpenReview AI (`openreview-ai`)  
**License:** MIT License  
**Audit Date:** 2026-10-18  

This inventory documents all direct runtime dependencies, development dependencies, external scanners, and model runtimes, along with their licensing terms, usage rationale, and alternatives.

---

## 1. Core Runtime Dependencies

OpenReview AI follows a **minimal dependency** philosophy. Core functionality relies primarily on the Python 3.11+ standard library.

| Package | Version Range | License | Rationale | Alternatives Evaluated |
| :--- | :--- | :--- | :--- | :--- |
| **Python Standard Library** (`pathlib`, `subprocess`, `json`, `argparse`, `dataclasses`, `hashlib`, `typing`) | Python 3.11+ | PSF License | Zero installation overhead, guaranteed stability, safe subprocess array execution. | Custom shell scripts (less portable, higher injection risk). |
| **`pydantic`** (Epic E2+) | `>=2.6.0` | MIT | High-performance schema validation for `Finding` and `ReviewReport` models; JSON serialization. | Python `dataclasses` with manual validation (more error-prone for nested schemas). |

---

## 2. Development & Test Dependencies

Development dependencies are used solely for building, testing, and linting OpenReview AI. They are not bundled into production distributions.

| Package | Version Range | License | Rationale |
| :--- | :--- | :--- | :--- |
| **`pytest`** | `>=8.0.0` | MIT | Standard unit and integration test runner. |
| **`setuptools`** | `>=68.0.0` | MIT | Standard build backend for editable packaging (`pyproject.toml`). |
| **`wheel`** | `>=0.42.0` | MIT | Standard wheel packaging format. |

---

## 3. External Scanner Tooling (User-Supplied Subprocesses)

OpenReview AI orchestrates scanners as external subprocesses. It never links or compiles against them directly, preserving license separation.

| Tool | Ecosystem | Declared License | Subprocess Calling Pattern | License Compatibility Note |
| :--- | :--- | :--- | :--- | :--- |
| **Ruff** | Python | MIT / Apache-2.0 | `ruff check --output-format=json <path>` | Fully compatible; permissive open source. |
| **ESLint** | JavaScript / TypeScript | MIT | `npx eslint --format json <path>` | Fully compatible; permissive open source. |
| **Bandit** | Python (Security AST) | Apache-2.0 | `bandit -f json -r <path>` | Fully compatible; permissive open source. |
| **Semgrep Community** | Multi-language SAST | LGPL-2.1 | `semgrep --json --config auto <path>` | Executed strictly via CLI subprocess; no library linking. |
| **Gitleaks** | Secret Scanning | MIT | `gitleaks detect --no-git --report-format json` | Fully compatible; permissive open source. |

---

## 4. Local AI Model Runtimes

| Runtime / Engine | License | Communication Protocol | Privacy Profile |
| :--- | :--- | :--- | :--- |
| **Ollama** | MIT | Local HTTP REST (`http://127.0.0.1:11434/api/generate`) | Local compute by default on loopback. If configured with a remote endpoint, network transmission of code context occurs. |

---

## 5. Compliance & Attribution Checklist

- [x] OpenReview AI core code is released under the **MIT License**.
- [x] All runtime libraries have permissive licenses (PSF, MIT, Apache-2.0).
- [x] No GPL/AGPL copyleft libraries are imported or statically linked.
- [x] External CLI tools (e.g. Semgrep, Unblob) are executed exclusively through isolated subprocesses.
- [x] Trademark compliance: OpenReview AI is an independent product and claims no affiliation with CodeRabbit, GitHub, or any commercial vendor.
