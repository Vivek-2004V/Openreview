# Loop Engineering Playbook for Vibe Coding

## Goal

Use short, evidence-driven coding loops. The agent must understand the
repository, make one bounded change, verify it, inspect the diff, and
record the outcome before starting the next task.

## The loop

### 0. Orient

-   Read `README.md`, `AGENTS.md`/agent rules, PRD, TRD, architecture,
    backlog, and latest progress log.
-   Inspect `git status`; do not overwrite unrelated changes.
-   Identify the exact task ID and acceptance criteria.
-   If repository facts are unknown, inspect files and commands rather
    than guessing.

### 1. Plan

Before editing, write: - current behavior, - expected behavior, - files
likely to change, - smallest implementation, - tests to add/run, - risks
and assumptions.

If a critical requirement is ambiguous, ask one focused question.
Otherwise state a reversible assumption and continue.

### 2. Implement one slice

-   Implement only the selected task.
-   Avoid unrelated refactors, broad formatting, or speculative
    abstractions.
-   Prefer small functions, typed data structures, and explicit errors.
-   Do not add dependencies without explaining why and checking
    license/maintenance.
-   Never fabricate tool outputs or claim commands passed without
    running them.

### 3. Verify

Run the narrowest relevant tests first, then broader gates: 1. focused
unit tests, 2. type checks/lint, 3. integration tests, 4. build/package
check where relevant, 5. security checks for changed boundaries.

If a command cannot run, state the exact reason and leave it unverified.

### 4. Review

Inspect `git diff --check` and the full diff. Ask: - Did this meet
acceptance criteria? - Did it alter unrelated behavior? - Are paths,
shell calls, subprocess output, errors, and secrets handled safely? -
Are new branches tested? - Does documentation match actual behavior?

### 5. Report

Update `PROGRESS.md` with: - task ID and summary, - files changed, -
commands run and actual results, - remaining failures/blockers, - next
smallest task.

Provide a concise handoff: implemented, verified, not verified, risks,
next task.

## Loop limits

-   One task per loop; aim for one independently testable change.
-   If a task is larger than one loop, split it before coding.
-   If tests fail, do not pile on new features. Diagnose the first
    meaningful failure.
-   If the same approach fails twice, stop and reassess assumptions
    instead of repeating it.
-   Do not "fix" failing tests by weakening/removing them without a
    justified requirement change.
-   Do not silently skip tests.
-   Do not declare completion while acceptance criteria remain unmet.

## Definition of done per loop

-   Acceptance criteria met.
-   Relevant tests added/updated and run.
-   Existing tests relevant to the change pass.
-   Diff reviewed; no accidental secrets or unrelated edits.
-   Documentation/progress log updated.
-   Known limitations clearly reported.

## Suggested cadence

`Inspect → Plan → Implement → Test → Diff review → Update docs → Commit/next task`

A commit is optional per loop but recommended for stable checkpoints.
Never rewrite shared history without explicit instruction.
