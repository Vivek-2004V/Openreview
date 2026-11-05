# OpenReview AI --- Vibe Coding Project Pack

> Working name only. Check trademark/domain availability before choosing
> a public product name.

## Purpose

Build a free-first, open-source, local-first AI code review product
inspired by publicly documented code-review workflows. REA
(`https://github.com/morluto/rea`) is an optional investigation tool and
possible source of reusable MIT-licensed components after an actual
repository/dependency audit. This product is **not** an official
CodeRabbit product and must not copy proprietary code, prompts, private
APIs, branding, or protected assets.

## Product promise

Review code changes, combine deterministic static-analysis results with
AI explanations, show evidence-backed findings, and suggest fixes that
are validated in an isolated environment before a human approves them.

## Documents

-   `01_PRODUCT_BRIEF_PRD.md` --- users, problem, scope, success
    criteria.
-   `02_TECHNICAL_REQUIREMENTS_TRD.md` --- stack, requirements,
    interfaces.
-   `03_ARCHITECTURE.md` --- components and data flow.
-   `04_LOOP_ENGINEERING_PLAYBOOK.md` --- repeatable vibe-coding loop.
-   `05_AGENT_RULES.md` --- instructions for the coding agent.
-   `06_IMPLEMENTATION_PLAN.md` --- milestones and order of work.
-   `07_TASK_BACKLOG.md` --- small, verifiable implementation tasks.
-   `08_TEST_AND_ACCEPTANCE_PLAN.md` --- quality gates.
-   `09_SECURITY_THREAT_MODEL.md` --- threat model and mandatory
    controls.
-   `10_LICENSE_AND_COMPETITOR_RESEARCH.md` --- responsible research and
    license checks.
-   `11_MASTER_BUILD_PROMPT.md` --- paste into your coding agent to
    begin safely.

## Non-negotiable engineering rules

1.  Inspect the existing REA repository before deciding what to reuse.
    Do not assume its internal structure from its README.
2.  Keep REA as a dependency/investigation tool unless a specific
    component is justified for reuse. Avoid coupling the product to REA
    internals.
3.  Do not start by building the full SaaS. First prove one end-to-end
    local review on a sample repository.
4.  Do not claim a finding is correct unless there is evidence. Label
    uncertain AI observations as suggestions.
5.  Never execute untrusted repository code on the host. Use a
    constrained sandbox with resource limits and no secrets.
6.  Never auto-merge or apply fixes to the user's default branch without
    explicit user approval.
7.  Every coding loop ends with tests, lint/type checks relevant to the
    change, a diff review, and an updated progress log.
8.  Keep core local usage free; document any optional hosted costs and
    limitations honestly.

## Initial MVP boundary

-   Local CLI first.
-   Git diff and changed-file awareness.
-   Python and JavaScript/TypeScript only for initial supported review.
-   Static-analysis adapters, structured findings, optional local-model
    explanations.
-   Markdown/JSON report output.
-   No accounts, billing, public multi-tenant service, auto-merge, or
    broad language support in MVP.
