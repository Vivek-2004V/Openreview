# Scanner Evaluation, Rule Coverage, and Licensing

**Project:** OpenReview AI  
**Specification:** TRD Section 1 & Epic E3 Task E3-T6  
**Status:** Evaluated & Documented  

This document evaluates static analysis tools and linters for inclusion in OpenReview AI, documenting their rule coverage, performance profile, and license compatibility.

---

## 1. Supported Adapters (Phase 3 Core)

### 1.1 Ruff (Python)
- **Ecosystem:** Python 3.7+
- **License:** MIT / Apache-2.0
- **Execution:** `ruff check --output-format=json --no-fix <files>`
- **Rule Coverage:** Replaces Flake8, pycodestyle (`E`/`W`), Pyflakes (`F`), isort (`I`), pydocstyle (`D`), flake8-bugbear (`B`), and flake8-bandit (`S`).
- **Performance:** 10x–100x faster than traditional Python linters (written in Rust).
- **Decision:** **Primary default scanner for Python.**

### 1.2 ESLint (JavaScript / TypeScript)
- **Ecosystem:** Node.js, Web, Front-end & Back-end JS/TS
- **License:** MIT
- **Execution:** `eslint --format=json <files>` or `npx --no-install eslint`
- **Rule Coverage:** Comprehensive code quality, ES6+ idioms, TypeScript type safety, React/Vue rules.
- **Performance:** Standard Node AST execution; cached by project configuration.
- **Decision:** **Primary default scanner for JS/TS.**

---

## 2. Additional Scanners Evaluated for Next Milestones

| Scanner | Target Languages | Declared License | Primary Strengths | Licensing & Integration Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Bandit** | Python | Apache-2.0 | Dedicated Python AST security analysis (CWE mapping, shell injection, weak crypto, insecure deserialization). | Safe to invoke via `bandit -f json -r`. Permissive license. |
| **Semgrep Community** | Polyglot (Python, JS, Go, Java, C, Ruby) | LGPL-2.1 | Community-driven OWASP Top 10 security rules; semantic pattern matching. | **Subprocess isolation only.** OpenReview AI invokes the standalone CLI binary; no dynamic/static library linking. |
| **Gitleaks** | All file types | MIT | Ultra-fast regex and Shannon entropy secret scanning (AWS keys, GitHub PATs, private keys). | Permissive MIT. Zero copyleft risk. Recommended for default inclusion. |
| **Biome** | JS, TS, JSX, JSON | MIT / Apache-2.0 | Next-generation Rust linter for web ecosystems; zero-config alternative to ESLint. | Fast, permissive. Great fallback if Node.js/npm is not installed. |
| **Clippy** | Rust | Apache-2.0 / MIT | Compiler-grade linting and idiomatic Rust checks. | Permissive; standard toolchain via `cargo clippy`. |
| **golangci-lint** | Go | GPL-3.0 | Aggregator of 40+ Go linters (`govet`, `errcheck`, `staticcheck`). | **Strict subprocess isolation.** Executed as independent external tool. |

---

## 3. Subprocess & Licensing Isolation Policy

To guarantee OpenReview AI's core codebase remains strictly **MIT-licensed**:
1. **No Static or Dynamic Linking:** Scanners are never imported as Python C-extensions or statically compiled into the binary.
2. **Subprocess Boundary:** All scanners run via `run_scanner_process` with argument arrays, non-zero exit code tolerance, and process isolation.
3. **User-Supplied Executables:** Users install tools according to their own license permissions (e.g. `pip install ruff` or `brew install gitleaks`).
