# Security Threat Model

## Assets

-   Source code and proprietary intellectual property.
-   GitHub installation tokens and webhook secrets.
-   Local credentials and environment variables.
-   Review reports and security findings.
-   Build/test environment integrity.

## Trust boundaries

-   User's local workspace.
-   Repository content (untrusted).
-   Scanner subprocesses.
-   Local model provider.
-   GitHub webhook/API boundary.
-   Future isolated worker/container boundary.

## Threats and controls

### Prompt injection in source files

**Threat:** A README/comment tells the model to reveal secrets or ignore
instructions. **Controls:** Treat all repository text as data; delimit
context; restrict model tools; never expose secrets; validate output; no
model-driven commands.

### Malicious build/test scripts

**Threat:** Running package scripts or tests exfiltrates secrets or
modifies host files. **Controls:** Do not run untrusted code on host.
For later test execution, use ephemeral sandbox, non-root user,
read-only source mount where possible, no secrets, restricted network,
CPU/memory/process/time limits, and destroy after run.

### Path traversal

**Threat:** A filename or patch targets files outside repository.
**Controls:** Normalize paths, resolve symlinks carefully, enforce
workspace boundary, reject absolute/parent traversal paths, test
platform-specific path edge cases.

### Command injection

**Threat:** Repository-controlled values are interpreted by shell.
**Controls:** Use subprocess argument arrays, allowlisted executables,
fixed argument construction, no `shell=True`, strict timeouts and output
caps.

### Secret leakage

**Threat:** Tokens or source snippets leak into logs/model
prompts/reports. **Controls:** Redact likely secrets, minimize context,
keep logs structured, do not dump environment, no telemetry by default,
protect output files.

### GitHub token misuse

**Threat:** Token has excessive rights or leaks from worker.
**Controls:** GitHub App installation tokens, least privilege,
short-lived credentials, no secrets in PR code environment, redact logs,
rotate/revoke support.

### Denial of service

**Threat:** Huge diffs/files or scanner output exhaust resources.
**Controls:** File count/size limits, diff truncation with explicit
warning, concurrency caps, timeouts, output limits, cancellation.

### False positives/false negatives

**Threat:** User trusts a wrong finding or misses a real issue.
**Controls:** Separate deterministic scanner findings from AI
suggestions; provide evidence/confidence; publish limits; never claim
complete security.

## Security release checklist

-   [ ] No secrets in repo, logs, fixtures, or sample reports.
-   [ ] No arbitrary LLM command execution.
-   [ ] Subprocess timeouts/output limits tested.
-   [ ] Path traversal tests pass.
-   [ ] Prompt-injection fixtures tested.
-   [ ] Local mode outbound network behavior documented.
-   [ ] Dependencies and licenses reviewed.
-   [ ] Sandboxing implemented before running untrusted code.
-   [ ] GitHub permissions documented and minimized before integration.
-   [ ] Responsible vulnerability reporting contact documented before
    public release.
