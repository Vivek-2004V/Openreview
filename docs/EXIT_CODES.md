# Documented Exit Codes Contract

**Project:** OpenReview AI  
**Specification:** TRD Section 7  
**Standard Status:** Implemented & Verified  

OpenReview AI adheres to a deterministic, POSIX-compliant exit codes contract to enable automated CI/CD gating, GitHub Actions workflows, pre-commit hooks, and terminal scripting.

---

## Exit Codes Reference Table

| Exit Code | Name | CLI / CI Meaning | Remediation / Expected Action |
| :---: | :--- | :--- | :--- |
| **`0`** | `SUCCESS` | Review completed cleanly. No actionable findings were discovered above the configured severity threshold. | Safe to merge / CI pass. |
| **`1`** | `FINDINGS_PRESENT` | Review completed successfully, but one or more actionable findings were detected. | Developer should inspect report and resolve or suppress findings. |
| **`2`** | `INVALID_INPUT_OR_CONFIG` | CLI argument syntax error, unknown configuration key, invalid type, missing config file, or target path is not a Git repository. | Check CLI arguments (`--help`), configuration file syntax, or verify the directory is a Git repository (`git init`). |
| **`3`** | `TOOL_FAILURE` | A required external scanner (e.g. Git, Ruff, ESLint) failed, timed out, or produced unparseable output, or the configured model endpoint was unreachable. | Check `openreview doctor`, verify scanner installations, ensure Ollama daemon is running, or adjust timeout settings. |
| **`4`** | `INTERNAL_ERROR` | An unhandled exception or internal system fault occurred. | Inspect `--verbose` logs and report the issue to project maintainers. |

---

## Python API Integration

```python
from openreview import ExitCode

if exit_code == ExitCode.SUCCESS:
    print("Clean review!")
elif exit_code == ExitCode.FINDINGS_PRESENT:
    print("Actionable issues found.")
elif exit_code == ExitCode.INVALID_INPUT_OR_CONFIG:
    print("Configuration or path error.")
elif exit_code == ExitCode.TOOL_FAILURE:
    print("External tool or model error.")
elif exit_code == ExitCode.INTERNAL_ERROR:
    print("Internal engine error.")
```

---

## CI/CD Usage Example (GitHub Actions)

```yaml
- name: Run OpenReview AI
  id: openreview
  run: |
    openreview review --format markdown --output review.md
  continue-on-error: true

- name: Check Exit Status
  run: |
    case ${{ steps.openreview.outputs.exit_code }} in
      0) echo "Review passed cleanly." ;;
      1) echo "Findings detected; blocking merge." && exit 1 ;;
      2) echo "Invalid configuration or path." && exit 2 ;;
      3) echo "Scanner tool error." && exit 3 ;;
      *) echo "Unexpected failure." && exit 4 ;;
    esac
```
