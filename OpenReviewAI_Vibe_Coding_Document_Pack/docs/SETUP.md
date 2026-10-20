# OpenReview AI Setup & Installation Guide

OpenReview AI is a free-first, open-source, local-first code review engine designed as a private alternative to SaaS tools like CodeRabbit.

---

## 1. Prerequisites

- **Python:** 3.10 or higher (recommended: 3.11+)
- **Git:** 2.25+ installed and available in `$PATH`
- **Linters (Optional but Recommended):**
  - **Python:** `pip install ruff`
  - **JavaScript / TypeScript:** `npm install -g eslint`
- **Local AI (Optional):**
  - [Ollama](https://ollama.ai) installed with a coding model:
    ```bash
    ollama pull qwen2.5-coder:7b
    ```

---

## 2. Installation

### From Source
```bash
git clone https://github.com/openreview/openreview-ai.git
cd openreview-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### From Wheel (PEP 517 build)
```bash
pip install dist/openreview_ai-0.1.0-py3-none-any.whl
```

---

## 3. Verify Your Environment

Run the built-in diagnostic tool to check tool availability:

```bash
openreview doctor
```

Sample output:
```text
OpenReview AI Environment Doctor:
---------------------------------
  - Git                   : OK (/usr/bin/git)
  - Ruff (Python)         : OK (/usr/local/bin/ruff)
  - ESLint (JS/TS)        : OK (/usr/local/bin/eslint)
  - Bandit (Python AST)   : NOT FOUND (optional/adapter)
  - Semgrep (SAST)        : NOT FOUND (optional/adapter)
  - Ollama (Local AI)     : OK (/usr/local/bin/ollama)
```

---

## 4. Running Your First Review

Navigate to any Git repository and run:

```bash
# Review uncommitted changes in current working tree
openreview review

# Review against a specific base branch
openreview review --base main

# Generate JSON report instead of Markdown
openreview review --format json --output report.json

# Enable local AI review via Ollama
openreview review --ai --model qwen2.5-coder:7b
```
