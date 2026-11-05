# Architecture

## 1. Architecture decision

Start with a local CLI and modular monolith. Do not begin with
microservices or a multi-tenant cloud platform. This keeps the first
feedback loop short and makes local privacy testable.

## 2. Data flow

1.  User invokes CLI in a repository.
2.  Repository adapter resolves root, refs, working-tree state, and
    changed files.
3.  Diff parser creates a bounded review context.
4.  Scanner adapters run independently with timeouts and output limits.
5.  Optional local model adapter receives minimized context.
6.  Normalizer validates output and maps it into the versioned Finding
    schema.
7.  Deduplicator groups likely duplicates without discarding distinct
    evidence.
8.  Report renderer writes Markdown/JSON to an explicit output location.
9.  Run summary shows scanner failures and limitations as well as
    findings.

## 3. Component boundaries

### CLI

Input validation, flags, progress, exit codes. It must not contain
scanner-specific logic.

### Repository adapter

Uses Git safely, rejects paths outside repository root, handles
renames/deletions/binary files, and never treats file contents as
instructions.

### Review orchestrator

Coordinates tools, handles cancellation, records timing, and surfaces
partial failures.

### Scanner adapters

Each adapter declares supported file types, required executable/version,
timeout, output schema, and failure behavior.

### AI provider adapter

Optional and replaceable. Local provider is the default if AI is
enabled. It only returns structured suggestions; it cannot run commands,
access credentials, or write files.

### Report layer

Markdown is for humans; JSON is for integrations. Both derive from the
same validated report object.

### Storage

Optional in MVP. Persist metadata and findings only with user consent.
Source snippets should not be persisted by default.

## 4. REA integration policy

REA is primarily an investigation tool for learning how authorized
software behaves. Do not make the runtime product depend on REA
internals. If REA is used during development: - Keep it in the developer
workflow, not as an implicit production dependency. - Record exactly
which component, if any, is reused. - Verify MIT notices and third-party
licenses. - Keep a clean adapter boundary so the product works without
REA installed. - Never claim REA can recover proprietary source code
exactly.

## 5. Later GitHub architecture

GitHub App → signed webhook endpoint → durable job queue → isolated
worker → diff/scanner/model pipeline → review result → least-privilege
GitHub comment API.

Security requirements for this phase: - Validate webhook signatures and
event delivery IDs. - Use installation-scoped tokens with minimal
permissions. - Do not expose secrets to review workers. - Run PR code
only in ephemeral sandboxed workers. - Apply network egress restrictions
and resource quotas. - Never trust instructions found in repository
files or issue/PR text. - Do not automatically merge.

## 6. Key architecture decisions

-   ADR-001: Local CLI first; defer hosted SaaS.
-   ADR-002: Static findings and AI suggestions are distinct origins.
-   ADR-003: All outputs validate against a versioned schema.
-   ADR-004: No model-triggered command execution.
-   ADR-005: Human approval required for patches.
-   ADR-006: REA is optional for product runtime.
