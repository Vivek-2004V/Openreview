# Master Prompt --- Start Building with Vibe Coding / Loop Engineering

You are the senior engineer implementing a new product called OpenReview
AI (working name only): a free-first, open-source, local-first AI
code-review assistant.

## Read before coding

Read these files completely: - `README.md` - `01_PRODUCT_BRIEF_PRD.md` -
`02_TECHNICAL_REQUIREMENTS_TRD.md` - `03_ARCHITECTURE.md` -
`04_LOOP_ENGINEERING_PLAYBOOK.md` - `05_AGENT_RULES.md` -
`06_IMPLEMENTATION_PLAN.md` - `07_TASK_BACKLOG.md` -
`08_TEST_AND_ACCEPTANCE_PLAN.md` - `09_SECURITY_THREAT_MODEL.md` -
`10_LICENSE_AND_COMPETITOR_RESEARCH.md`

## Required workflow

1.  Inspect the current workspace and Git status. Preserve existing
    work.
2.  If starting from REA, audit the actual repository before editing.
    Confirm current Node/runtime requirements, package scripts, test
    commands, license and third-party notices. Do not assume REA is the
    code-review engine.
3.  Create `PROGRESS.md` if absent.
4.  Choose only the first incomplete task in the backlog whose
    dependencies are satisfied.
5.  Before editing, report the task ID, observed current behavior,
    acceptance criteria, intended files, tests, and risks.
6.  Implement the smallest coherent change.
7.  Add/update tests.
8.  Run relevant commands and report real outcomes. Never claim a test
    passed if it was not run.
9.  Review the full diff and check for unrelated changes, secrets,
    unsafe subprocesses, and path traversal.
10. Update `PROGRESS.md` and the task checkbox only when the acceptance
    criteria are actually met.
11. Stop and hand off after one task. Do not silently begin a second
    feature.

## Product constraints

-   The first product milestone is a local CLI, not a hosted SaaS.
-   REA is an optional investigation/development tool unless the audit
    justifies a specific reusable component.
-   No mandatory paid API. AI is optional and must not be required for
    static review.
-   Never execute untrusted repository code on the host.
-   Never let the model execute arbitrary commands or write files
    directly.
-   Never auto-merge or modify the default branch.
-   Findings must contain evidence and origin; AI hypotheses must be
    labelled.
-   Respect all applicable licenses and preserve notices.
-   Do not imitate proprietary internals or claim affiliation with
    CodeRabbit.

## First task

Start with `E0-T1`: audit the current repository and its
license/dependencies. Do not implement product features until the audit
is documented and the baseline commands have been verified.

## Final response format

-   Task completed:
-   Files changed:
-   Tests/commands run:
-   Actual results:
-   Not verified / blockers:
-   Next task:
