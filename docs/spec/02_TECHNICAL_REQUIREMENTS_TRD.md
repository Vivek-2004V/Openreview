# Technical Requirements Document (TRD)

## 1. Proposed stack (MVP)

-   CLI/runtime: Python 3.12+ (pin exact supported version in project
    configuration).
-   API later: FastAPI.
-   Web UI later: Next.js + TypeScript.
-   Git operations: system Git via a safe subprocess wrapper; no
    shell-string concatenation.
-   Static analysis adapters: start with Ruff for Python and ESLint for
    JS/TS; optionally add Semgrep Community and Bandit after license,
    rule coverage, and installation checks.
-   Local AI: optional Ollama HTTP adapter; model names are
    configuration, not hard-coded assumptions.
-   Storage: SQLite for local review history only when persistence is
    needed.
-   Testing: pytest, type checking, linting, and integration fixtures.
-   Packaging: pyproject.toml and a reproducible environment; Docker
    only for isolated execution/integration tests initially.

Stack is a proposal, not a mandate. Confirm versions and licenses before
implementation.

## 2. Functional requirements

**FR-01:** Detect repository root and Git status. **FR-02:** Accept
base/head revisions or a working-tree diff. **FR-03:** Identify changed
files and changed line ranges. **FR-04:** Apply allowlisted scanner
adapters to supported file types. **FR-05:** Normalize scanner output to
a versioned `Finding` schema. **FR-06:** Optionally send minimal,
relevant code context to a configured local model. **FR-07:** Label
origin: `static`, `ai`, or `combined`. **FR-08:** Produce Markdown and
JSON reports. **FR-09:** Provide deterministic exit codes for clean run,
findings, configuration error, and tool failure. **FR-10:** Record run
metadata without storing source contents by default. **FR-11:** Provide
timeouts, output-size limits, cancellation, and clear errors for every
external tool. **FR-12:** No automatic repository writes in MVP.

## 3. Non-functional requirements

-   Secure defaults: no arbitrary shell commands from model output.
-   Reliability: partial scanner failure is reported explicitly.
-   Portability: macOS and Linux first; Windows support only after CI
    proves it.
-   Observability: structured logs with secret redaction.
-   Performance: establish baseline on a small/medium fixture before
    optimization.
-   Accessibility: future UI must support keyboard navigation and clear
    severity labels.
-   Privacy: source contents stay local unless user opts into a remote
    provider.
-   Maintainability: adapters are isolated; adding a scanner should not
    change report schema.

## 4. Finding schema (version 1)

Required fields: - `schema_version`: string, e.g. `"1.0"`. -
`finding_id`: stable identifier derived from rule/tool + path +
normalized location. - `title`: concise summary. - `severity`:
`critical | high | medium | low | info | unknown`. - `confidence`:
`high | medium | low | unknown`. - `origin`: `static | ai | combined`. -
`path`: repository-relative POSIX path. - `start_line`: integer or
null. - `end_line`: integer or null. - `rule_id`: string or null. -
`evidence`: short excerpt or tool evidence, subject to secret
redaction. - `explanation`: what may be wrong and why. -
`recommendation`: proposed remediation, not automatically applied. -
`references`: list of URLs or identifiers, if available. - `status`:
`open | suppressed | accepted_risk | fixed | unverified`.

Never fabricate a line number, scanner rule, test result, CVE, or
reference. Null is better than invented evidence.

## 5. Suggested module boundaries

-   `repo/`: Git status, diff parsing, path normalization.
-   `review/`: orchestration and review-run lifecycle.
-   `adapters/`: scanner and model provider interfaces.
-   `schemas/`: versioned models and serialization.
-   `reports/`: Markdown and JSON renderers.
-   `security/`: redaction, path validation, process limits.
-   `storage/`: optional SQLite history.
-   `tests/fixtures/`: seeded repositories and known findings.

## 6. Interfaces

`ScannerAdapter.run(context) -> ScannerResult` - Context contains repo
root, allowed file list, timeout, output limit, and config. - Result
contains findings, tool status, duration, and sanitized diagnostics.

`ModelProvider.review(context) -> list[AIReviewSuggestion]` - Receives
only selected diff/context. - Must not receive secrets or unrelated
files by default. - Model output is untrusted and schema-validated. -
The provider cannot execute tools or change repository state.

`ReviewRunner.run(request) -> ReviewReport` - Collects diff, invokes
enabled adapters, normalizes results, deduplicates carefully, writes
report only to chosen output path.

## 7. Exit codes

-   `0`: completed, no findings above configured threshold.
-   `1`: completed, actionable findings present.
-   `2`: invalid configuration or input.
-   `3`: one or more required tools failed or timed out.
-   `4`: internal error. Document exact behavior in CLI help and tests.

## 8. Configuration

Use a project-level config file with explicit allowlisted settings.
Suggested fields: - `review.base_ref` - `review.changed_files_only` -
`review.max_files` - `review.max_file_bytes` - `scanners.enabled` -
`scanners.timeout_seconds` - `ai.enabled` - `ai.provider` - `ai.model` -
`ai.max_context_tokens` - `privacy.redact_secrets` - `reports.formats` -
`reports.output_dir`

Never permit config from an untrusted PR to silently loosen sandboxing
or grant secrets.
