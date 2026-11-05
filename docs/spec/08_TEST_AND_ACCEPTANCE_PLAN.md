# Test and Acceptance Plan

## 1. Test layers

-   Unit: diff parsing, path validation, schema validation, redaction,
    severity mapping.
-   Contract: scanner adapters against captured representative outputs.
-   Integration: CLI → Git diff → scanners → normalized findings →
    report.
-   End-to-end: known sample repository with seeded defects.
-   Security: prompt injection, path traversal, secret exposure,
    subprocess injection, resource exhaustion.
-   Performance: runtime and memory on a documented fixture.

## 2. Required fixtures

Maintain small repositories with: - known Python lint/security issues, -
known JS/TS issues, - clean code, - deleted and renamed files, - binary
file changes, - huge file/diff, - filenames containing spaces and
unusual Unicode, - malicious README/comments containing instructions to
the AI, - fake secrets used only for redaction tests, - intentionally
malformed scanner output.

## 3. Required negative tests

-   Git is missing or repository is invalid.
-   Scanner executable is absent.
-   Scanner times out or floods stdout/stderr.
-   Model endpoint is unavailable.
-   Model returns invalid JSON, wrong schema, fabricated file path, or
    impossible line number.
-   Diff contains path traversal patterns.
-   Repository contains instructions asking the agent to expose
    environment variables or run commands.
-   Output directory is not writable.
-   Partial tool failure occurs.

## 4. Acceptance gates

**Gate A --- CLI:** help, version, input validation, and exit codes
work. **Gate B --- Reports:** schema validates; Markdown/JSON agree on
finding count and identifiers. **Gate C --- Scanners:** tool errors
never look like a clean review; rule IDs and evidence preserved. **Gate
D --- AI:** optional, bounded, schema-validated, and clearly marked as
AI-origin. **Gate E --- Privacy:** no source uploads in local mode;
outbound requests are documented and tested. **Gate F --- Security:** no
host execution of untrusted repository code; timeouts and output caps
enforced. **Gate G --- Release:** clean setup works from documented
instructions; license notices included.

## 5. Benchmark methodology

-   Record fixture commit hash, tool versions, config, and model
    name/version.
-   Measure seeded issue detection and false positives separately.
-   Have a human validate whether findings are actionable.
-   Report per-category results; do not collapse everything into a
    single accuracy percentage.
-   Do not use benchmark code that violates licenses or contains real
    secrets.
-   Repeat runs to detect nondeterminism.

## 6. Release blockers

Do not release if: - a critical security test fails, - scanner failure
is silently reported as success, - report contains real secrets, - AI
can trigger arbitrary command execution, - tests claimed in
documentation were not run, - license attribution is missing, - local
mode sends source code remotely without explicit consent.
