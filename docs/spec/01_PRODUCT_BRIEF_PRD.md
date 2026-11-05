# Product Requirements Document (PRD)

## 1. Product summary

**Working name:** OpenReview AI (placeholder; conduct naming/trademark
checks before launch).

Build a free-first code-review assistant that developers can run locally
and later connect to GitHub pull requests. It combines deterministic
tools with an optional local AI model to identify potential bugs,
security issues, maintainability problems, and test gaps. It must
provide evidence and safe, reviewable suggestions rather than promise
perfect detection.

## 2. Problem

Developers want useful PR feedback but may face hosted-tool costs,
privacy concerns, false positives, or lack of control over models and
review policies. A free alternative must be more than a thin LLM
wrapper: it needs deterministic analysis, context-aware review,
explainable findings, and a validation workflow.

## 3. Target users

-   Individual developers and students who need free code review.
-   Open-source maintainers reviewing contributions.
-   Small teams that prefer local/self-hosted analysis.
-   Security-conscious developers who do not want source code sent to a
    hosted AI service.

## 4. Product principles

1.  Local-first by default; no source upload unless the user explicitly
    configures a remote integration.
2.  Free core functionality and no mandatory paid AI API.
3.  Evidence before confidence: show file, line, rule/tool, and
    rationale.
4.  Human control: findings and patches are proposals, not commands.
5.  Secure execution: repository content is untrusted input.
6.  Honest limitations: never promise "bug-free", "unhackable", or
    complete security coverage.
7.  Open development: document third-party licenses and supported
    versions.

## 5. MVP user stories

-   As a developer, I can run a command against a Git repository and
    receive a review report.
-   As a developer, I can limit review to changed files or a Git diff.
-   As a developer, I can see each finding's severity, file, line range,
    evidence, explanation, and suggested remediation.
-   As a developer, I can run deterministic scanners without configuring
    an AI model.
-   As a developer, I can optionally use a local model through a
    provider adapter.
-   As a developer, I can export results to Markdown and JSON.
-   As a developer, I can suppress a finding with a reason and retain
    that decision locally.
-   As a maintainer, I can run the test suite and reproduce the same
    scanner findings.

## 6. Out of scope for MVP

-   Full CodeRabbit feature parity.
-   Public hosted multi-tenant SaaS.
-   Billing, teams, SSO, enterprise dashboards.
-   Automatic commits or merges.
-   Arbitrary command execution by the LLM.
-   Automatic support for every programming language.
-   Reverse engineering of private CodeRabbit internals or unauthorized
    access to any service.

## 7. Later releases

**M1:** GitHub App/webhook integration and inline PR comments. **M2:**
Sandboxed test execution and patch proposal flow. **M3:** Team policy
files, local history, baseline/delta reporting. **M4:** Optional
self-hosted team server, audit logs, repository controls. **M5:** More
language adapters based on benchmark evidence.

## 8. MVP acceptance criteria

-   A documented command scans a sample repo from a clean setup.
-   Scanner failures are visible and do not silently become "no issues".
-   JSON output validates against a versioned schema.
-   Findings include source and line evidence where the underlying tool
    provides it.
-   AI-generated findings are marked separately from deterministic
    scanner findings.
-   Local mode makes no outbound network requests except those
    explicitly enabled by the user (e.g. pulling a model or installing
    dependencies).
-   Tests cover empty diffs, deleted files, binary files, scanner
    timeout, malformed output, and model unavailable.
-   No secrets are written to logs or report output.
-   A review never modifies the target repository unless the user
    explicitly invokes an approved patch action.

## 9. Success metrics

Track these on a known benchmark corpus: - Precision of actionable
findings. - Recall on seeded, known defects (with caveat that benchmark
coverage is limited). - False-positive rate. - Review runtime and peak
memory. - Percentage of findings with verifiable evidence. - Patch
test-pass rate. - Reproducibility across repeated runs. - User-dismissal
rate and reasons.

Do not set arbitrary "99% accurate" claims. Establish a baseline first
and publish the test methodology.

## 10. Competitive differentiation hypothesis

The strongest initial wedge is not "free CodeRabbit clone"; it is
local-first, no mandatory paid API, transparent evidence, and
user-controlled safe fixes. Validate this with actual developers before
investing in a hosted platform.
