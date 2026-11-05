# Implementation Plan

## Phase 0 --- Repository and product audit

**Goal:** Establish facts before coding. - Clone/fork REA only through
the normal authorized public source. - Inspect repository structure,
package metadata, license file, third-party notices, supported runtime,
tests, and build process. - Record what REA actually does and what it
does not do. - Create a separate product workspace or clearly isolated
package. - Decide whether any REA component is reused; default is no
runtime coupling until justified.

**Exit gate:** Audit notes, license inventory, architecture decision
records, clean build/test status recorded.

## Phase 1 --- Local CLI skeleton

-   Create project package and CLI entry point.
-   Add `--help`, `--version`, config validation, and consistent exit
    codes.
-   Detect Git repository root and report status safely.
-   Add unit tests for invalid paths and non-Git folders.

**Exit gate:** CLI runs from a clean environment and errors are
readable.

## Phase 2 --- Diff and report schema

-   Parse working-tree and base/head diffs.
-   Handle added, modified, renamed, deleted, binary, and very large
    files.
-   Add versioned `Finding` and `ReviewReport` models.
-   Export valid JSON and readable Markdown.

**Exit gate:** Schema tests and golden report fixtures pass.

## Phase 3 --- Deterministic scanners

-   Add one scanner adapter at a time (start with Ruff, then ESLint if
    available).
-   Check tool presence/version and show installation guidance.
-   Normalize output; preserve scanner rule IDs and line evidence.
-   Test missing executable, timeout, malformed output, and non-zero
    exits.

**Exit gate:** Scanner failure is never misreported as a clean review.

## Phase 4 --- Optional local AI

-   Add an Ollama-compatible provider adapter behind a feature flag.
-   Require model and endpoint configuration to be explicit.
-   Send minimal changed-code context only; redact likely secrets.
-   Validate structured response against schema; reject malformed
    output.
-   Clearly label AI-only findings as unverified suggestions.
-   Add tests for unavailable model, malformed response, prompt
    injection, and oversized context.

**Exit gate:** Static-only mode works when AI is disabled or
unavailable.

## Phase 5 --- CLI user experience

-   Add concise summary, severity filters, output path, and
    configuration docs.
-   Add baseline support only if it can be implemented without hiding
    new findings.
-   Add sample repository and end-to-end test.

**Exit gate:** A new developer can install, run, and understand one
review using only docs.

## Phase 6 --- Security hardening

-   Threat-model subprocesses, paths, logs, model input, and report
    output.
-   Add secret redaction, path traversal tests, resource caps, and
    dependency/license inventory.
-   Test malicious repository instructions and malformed tool output.
-   Document sandbox limitations.

**Exit gate:** No untrusted code execution in host process; known
high-risk gaps are resolved or feature is disabled.

## Phase 7 --- GitHub integration (after local MVP)

-   Create a GitHub App with least-privilege permissions.
-   Validate webhook signatures and event IDs.
-   Queue reviews in isolated workers.
-   Publish a summary first; add inline comments only after stable
    path/line mapping.
-   Add idempotency, rate-limit handling, token rotation, and
    installation deletion handling.

**Exit gate:** Security review and test repository workflow pass. No
public SaaS launch before this gate.

## Phase 8 --- Patch proposals

-   Generate unified diff only; do not apply automatically.
-   Validate patch paths and reject paths outside workspace.
-   Apply in disposable worktree/container.
-   Run tests and scanners with resource limits.
-   Require human approval before copying patch to the user's working
    tree.

**Exit gate:** Regression tests prove patches cannot escape workspace or
access secrets.

## Phase 9 --- Beta and launch

-   Recruit a small group of developers.
-   Collect opt-in anonymized metrics only; default telemetry off.
-   Publish benchmark methodology, known limitations, supported
    languages, license notices, privacy policy, and security reporting
    process.
-   Add hosted service only after compute economics and threat model are
    understood.
