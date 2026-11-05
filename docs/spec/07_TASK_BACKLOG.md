# Task Backlog

Tasks are intentionally small. Complete them in order unless
dependencies justify a documented change.

## Epic E0 --- Audit and foundation

-   [x] `E0-T1` Inspect REA repository structure, package metadata,
    license, third-party notices, runtime and test commands.
-   [x] `E0-T2` Write `docs/REA_AUDIT.md` with verified facts and open
    questions.
-   [x] `E0-T3` Create independent product workspace/package; preserve
    REA source and user changes.
-   [x] `E0-T4` Add architecture decision records and dependency/license
    inventory.
-   [x] `E0-T5` Establish baseline tests/build and record actual results
    in `PROGRESS.md`.

## Epic E1 --- CLI

-   [x] `E1-T1` Create CLI entry point and `--help`.
-   [x] `E1-T2` Implement config loading and schema validation.
-   [x] `E1-T3` Implement safe Git root/status detection.
-   [x] `E1-T4` Define documented exit codes.
-   [x] `E1-T5` Add tests for invalid path, no Git repo, bad config, and
    clean invocation.

## Epic E2 --- Diff and schema

-   [x] `E2-T1` Implement diff request model.
-   [x] `E2-T2` Parse changed paths and line ranges.
-   [x] `E2-T3` Handle rename/delete/binary/large files.
-   [x] `E2-T4` Define versioned Finding and ReviewReport schema.
-   [x] `E2-T5` Add Markdown renderer.
-   [x] `E2-T6` Add JSON renderer and schema validation.
-   [x] `E2-T7` Add golden fixture tests.

## Epic E3 --- Scanners

-   [x] `E3-T1` Define ScannerAdapter protocol and ScannerResult model.
-   [x] `E3-T2` Add Ruff adapter.
-   [x] `E3-T3` Add scanner timeout/output cap.
-   [x] `E3-T4` Add missing-tool and malformed-output tests.
-   [x] `E3-T5` Add ESLint adapter after confirming JS/TS fixture needs.
-   [x] `E3-T6` Evaluate additional scanners and document
    licensing/coverage.

## Epic E4 --- Optional local AI

-   [x] `E4-T1` Define ModelProvider protocol.
-   [x] `E4-T2` Add optional Ollama adapter.
-   [x] `E4-T3` Build bounded context selection for changed code.
-   [x] `E4-T4` Add secret redaction before model calls.
-   [x] `E4-T5` Validate AI response schema and reject unsupported
    claims.
-   [x] `E4-T6` Add prompt-injection and unavailable-model tests.
-   [x] `E4-T7` Ensure static-only mode works without AI.

## Epic E5 --- Quality and safety

-   [x] `E5-T1` Add `PROGRESS.md` and loop template.
-   [x] `E5-T2` Add path traversal and subprocess argument tests.
-   [x] `E5-T3` Add structured logging with secret redaction.
-   [x] `E5-T4` Add performance baseline on fixture repository.
-   [x] `E5-T5` Write setup, configuration, privacy, and troubleshooting
    docs.
-   [x] `E5-T6` Run end-to-end review against seeded defects and publish
    measured results.

## Epic E6 --- GitHub integration (defer)

-   [x] `E6-T1` Document GitHub App permissions and webhook contract.
-   [x] `E6-T2` Implement signed webhook validation.
-   [x] `E6-T3` Implement idempotent review job queue.
-   [x] `E6-T4` Implement isolated worker.
-   [x] `E6-T5` Publish summary review.
-   [x] `E6-T6` Add inline comments with robust diff line mapping.
-   [x] `E6-T7` Security review before any public deployment.

## Epic E7 --- Safe patch proposal (defer)

-   [x] `E7-T1` Generate unified diff proposals.
-   [x] `E7-T2` Validate patch path and workspace boundaries.
-   [x] `E7-T3` Apply patches only in disposable worktree/sandbox.
-   [x] `E7-T4` Run tests/scanners with limits.
-   [x] `E7-T5` Require explicit human approval before export/apply.
