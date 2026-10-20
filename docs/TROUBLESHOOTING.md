# OpenReview AI Troubleshooting & Diagnostics

This guide helps resolve common setup, scanner, and runtime issues.

---

## 1. Exit Codes Quick Reference

| Code | Name | Description | Recommended Action |
|---|---|---|---|
| `0` | `SUCCESS` | Review completed with 0 findings | All checks passed cleanly. |
| `1` | `FINDINGS_PRESENT` | Code review defects or suggestions found | Inspect report and address findings. |
| `2` | `INVALID_INPUT_OR_CONFIG` | Malformed config, bad CLI arguments, or non-git directory | Check path, run in git repo, validate config. |
| `3` | `TOOL_FAILURE` | Missing or failing external tool (Git, linter) | Run `openreview doctor` to check tool paths. |
| `4` | `INTERNAL_ERROR` | Unexpected crash or uncaught runtime exception | File an issue with stack trace. |

---

## 2. Common Scenarios & Resolutions

### "Directory is not a git repository"
- **Cause:** OpenReview was executed outside of a Git workspace.
- **Resolution:** `cd` into your Git repository or pass `--path /path/to/repo`.

### "Scanner NOT FOUND (optional/adapter)"
- **Cause:** `ruff` or `eslint` is not installed or not present in your system `$PATH`.
- **Resolution:**
  - For Python: `pip install ruff`
  - For JavaScript/TypeScript: `npm install -g eslint`
  - Or disable the missing scanner via CLI: `openreview review --scanners ruff`

### "Ollama service unavailable at http://127.0.0.1:11434"
- **Cause:** Ollama daemon is not running on your machine.
- **Resolution:**
  - Start the Ollama server: `ollama serve`
  - Or run in static-only mode without `--ai` (static scanners run offline without Ollama).

### "Scanner execution timed out after 30s"
- **Cause:** An external tool hung or took too long analyzing very large repositories.
- **Resolution:** Increase timeout in `.openreview.toml`:
  ```toml
  [scanners]
  timeout_seconds = 60
  ```

---

## 3. Running Diagnostics

Run `openreview doctor` with `--verbose` to inspect full tool locations:

```bash
openreview doctor -v
```
