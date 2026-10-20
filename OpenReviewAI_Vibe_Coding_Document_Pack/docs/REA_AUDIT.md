# REA Repository & Capability Audit

**Document Status:** Complete (Verified Grounded Facts)  
**Date of Audit:** 2026-10-18  
**Auditor:** OpenReview AI Engineering Agent  
**Target Repository:** `/Users/vivek/Desktop/untitled folder 2/rea`  
**Upstream Remote:** `https://github.com/morluto/rea.git`  
**Inspected Git Commit:** `b0b485abf` (branch: `main`, clean working tree)

---

## 1. Executive Summary

This audit establishes verified technical, structural, and legal facts about the REA repository prior to developing OpenReview AI. 

**Core Conclusion:**  
REA is an agent-native **reverse-engineering** framework and Model Context Protocol (MCP) server. It is engineered for disassembling binaries, decompiling mobile packages, driving debuggers, and inspecting live web/browser runtimes. It does **not** provide code-review, static-analysis orchestration, git-diff parsing, or inline PR-commenting features. 

In accordance with **ADR-006**, OpenReview AI will maintain a clean boundary:
- **No runtime coupling:** OpenReview AI will not import, bundle, or depend on REA at runtime.
- **Independent architecture:** OpenReview AI is developed as a separate, lightweight tool tailored to git diffs, linters (Ruff, ESLint, Bandit), and optional local LLM review (Ollama).
- **Optional external tool:** REA may serve as a separate external investigation/security tool if a developer specifically needs deep binary or compiled asset auditing.

---

## 2. Verified Repository Metadata

| Attribute | Verified Value | Grounded Evidence |
| :--- | :--- | :--- |
| **Package Name** | `rea-agents` | `package.json:2` |
| **MCP Identity** | `io.github.morluto/rea` | `package.json:3` |
| **Version** | `6.7.0` | `package.json:4` |
| **Module System** | ESM (`"type": "module"`) | `package.json:36` |
| **CLI Binaries** | `rea`, `rea-agents` (`scripts/rea.mjs`) | `package.json:41-43` |
| **Supported Engines** | Node `^22.19.0 \|\| ^24.11.0 \|\| >=26.0.0` | `package.json:45` |
| **Package Manager** | `npm@11.16.0` | `package.json:47` |
| **License** | MIT License (Copyright (c) 2026 morluto) | `LICENSE:1-22` |
| **Catalog Count** | 207 MCP tools, 6 MCP prompts, 153 CLI commands | `doctor` output & `catalogIdentity.ts` |

---

## 3. What REA Does vs. What REA Does Not Do

### 3.1 Verified Capabilities (What It Does)

1. **Binary Decompilation & Disassembly Bridges:**
   - **Hopper Disassembler:** Native bridge (`bridge/hopper_bridge.py`) for macOS Mach-O and Linux binaries.
   - **Ghidra:** Java bundle integration (`bridge/ghidra/ReaGhidraBridge.java`) supporting NativeAOT, raw binaries, MIPS soft float, and COM interfaces.
   - **IDA Pro:** External MCP registration bridge (`src/ida`).
   - **JADX & jadx-headless-mcp:** Android APK/DEX decompilation bridge (`bridge/android/ReaJadxBridge.java`).
   - **Kuna, Rizin, Cutter:** Auxiliary disassemblers and binary inspection engines.

2. **Dynamic Instrumentation & Debugging:**
   - LLDB tracer (`bridge/native/rea_lldb_tracer.py`).
   - Frida instrumentation scripts (`src/frida`).
   - GDB, Pwntools, Pwndbg integrations (`bridge/pwntools`, `bridge/pwndbg`).

3. **Web & JavaScript Runtime Observation:**
   - Chrome DevTools Protocol (CDP) client scenarios (`docs/browser-observation.md`).
   - Electron fuses inspection and active hooking (`scripts/electron-active-hook.cjs`).
   - Source map and AST reconstruction (`wakaru` integration).

4. **Agent-Native MCP Platform:**
   - Implements MCP tool registration with rigid input schemas, process timeouts, structured errors (`docs/error-contract.schema.json`), and comprehensive environment diagnostics (`rea doctor`).

### 3.2 Non-Capabilities (What REA Does NOT Do)

1. **No Code-Review Primitives:**
   - No Git diff parsing (working-tree vs. base branch, hunk/line range mapping).
   - No changed-file bounding or context pruning for PR reviews.
2. **No Linter / Static Analysis Orchestration:**
   - No adapters for Ruff, Flake8, ESLint, Biome, Bandit, or Semgrep.
   - No severity categorization for code quality (e.g. style, security, maintainability, test gaps).
3. **No PR / Code-Review Findings Schema:**
   - Findings in REA are memory addresses, symbols, decompiled blocks, or network traces—not versioned `Finding` models with line numbers, code snippets, explanations, and remediation diffs.
4. **No Model-Assisted Code Walkthrough:**
   - No prompts or templates for generating PR summaries, changelogs, or inline code suggestions.

---

## 4. License and Third-Party Dependency Audit

### 4.1 Core Codebase
The primary REA codebase is licensed under the permissive **MIT License**:
```text
MIT License
Copyright (c) 2026 morluto
```

### 4.2 Submodules and External Bridges
REA documents its external integrations in `THIRD_PARTY_NOTICES.md` and `.gitmodules`:

| Submodule / Component | Upstream Source | Declared License | Redistribution & Isolation Notes |
| :--- | :--- | :--- | :--- |
| `third_party/jadx-headless-mcp` | `1013503897/jadx-headless-mcp` | Apache-2.0 | Engine JAR is user-supplied; compatible. |
| `third_party/binwalk` | `ReFirmLabs/binwalk` | GPL-2.0 / MIT | GPL copyleft if bundled; REA invokes as external tool. |
| `third_party/unblob` | `onekey-sec/unblob` | GPL-3.0 | Strict copyleft; cannot be embedded in permissive code. |
| `third_party/ghidra-nativeaot` | `Washi1337/ghidra-nativeaot` | MIT | Permissive; attribution preserved. |
| `third_party/wakaru` | `pionxzh/wakaru` | MIT | Permissive JS decompiler. |
| `evmole@0.9.3` | `Maxim Andreev` | MIT | Permissive bytecode detector. |
| `frida@17.23.2` | `Frida Developers` | LGPL-2.0 w/ exception | Optional dynamic tracer. |

**Audit Rule Applied:** Any tool with copyleft restrictions (GPL/LGPL) must strictly remain an external, user-supplied executable or separate process. OpenReview AI will not vendor or link against copyleft binaries.

---

## 5. Build, Test, and Runtime Verification

### 5.1 Runtime Test Results
- **Node.js:** Host has `v26.0.0` (compatible with REA's engine requirement `>=26.0.0`).
- **NPM:** Host has `11.12.1`.
- **Command `npx -y rea-agents@latest doctor`:**
  - Exited with code `1` (normal for unconfigured host).
  - Detected Hopper Disassembler at `/Users/vivek/Applications/Hopper Disassembler.app/Contents/MacOS/hopper`.
  - Identified missing optional engines: Ghidra (`GHIDRA_INSTALL_DIR` unset), IDA (`REA_IDA_MCP_CONFIG` unset), JADX (`REA_JADX_MCP_JAR` unset), Kuna (`REA_KUNA_COMMAND` unset).
- **Command `npm run deps:check` inside local `rea/` clone:**
  - Exited with code `1`: `ENOENT: no such file or directory, open '.../rea/node_modules/.package-lock.json'`.
  - The local git clone does not have `node_modules` installed.

### 5.2 Host Environment for OpenReview AI
- **Python 3:**
  - System: `Python 3.9.6` (`/usr/bin/python3`).
  - Homebrew: `Python 3.11.15` (`/opt/homebrew/bin/python3.11`).
  - *Recommendation:* Build OpenReview AI with Python 3.11+ using virtual environments.
- **Git:** `2.54.0` available.
- **Local AI (Ollama):** `v0.40.1` installed and responsive.
- **Scanners (Ruff, ESLint):** Not installed globally; OpenReview AI's adapters must detect presence and surface clean remediation when absent.

---

## 6. Architecture Decision Records (ADRs) Grounded in this Audit

1. **ADR-001 (Standalone Product Package):** OpenReview AI will live in a distinct directory (`openreview/` or dedicated workspace root) completely separate from `rea/`.
2. **ADR-002 (Zero Runtime Coupling):** No imports of `rea-agents` or `scripts/rea.mjs`. OpenReview AI runs standalone using standard Python 3.11+ and safe system subprocesses.
3. **ADR-003 (Deterministic Scanners First):** Code review relies on Ruff, ESLint, Bandit, and Semgrep. AI (via Ollama) is strictly an optional enhancer for explanations, never a single point of failure.
4. **ADR-004 (Untrusted Input Discipline):** Borrow REA's security pattern: treat all repository files, diffs, and LLM outputs as untrusted data. Enforce subprocess argument arrays without `shell=True`, explicit timeouts, and path normalization.

---

## 7. Open Questions & Future Considerations

1. **Q1: Will OpenReview AI ever need REA?**  
   *Answer:* Only if a user requests binary or compiled artifact audits (e.g., verifying that an npm build artifact does not contain unintended native binary payloads). In that scenario, OpenReview AI would call REA as an external MCP server, never via code imports.
2. **Q2: Which Python runtime should OpenReview AI target?**  
   *Answer:* Target Python 3.11+ (`pyproject.toml` setting `requires-python = ">=3.11"`), taking advantage of Homebrew's installed Python 3.11.15.
3. **Q3: What is the initial scanner set?**  
   *Answer:* Start with `Ruff` (for Python) and direct AST checks, followed by `ESLint` (for JS/TS).
