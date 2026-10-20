# OpenReview AI Configuration Guide

OpenReview AI supports project-level configuration via `.openreview.toml` (recommended) or `.openreview.json` located at the root of your Git repository, or specified via the `--config` CLI flag.

---

## Configuration Schema

```toml
# .openreview.toml

[review]
# Base git revision for diff calculation (default: None -> HEAD)
base_ref = "main"

# Only review files that were modified in the diff
changed_files_only = true

# Maximum number of files to process per review
max_files = 100

# Maximum file size in bytes before skipping (default: 1,000,000 = 1MB)
max_file_bytes = 1000000

[scanners]
# Deterministic static linters to run
enabled = ["ruff", "eslint"]

# Per-scanner execution timeout in seconds
timeout_seconds = 30

[ai]
# Enable optional local AI review
enabled = false

# Model provider (currently 'ollama')
provider = "ollama"

# Model tag installed in Ollama
model = "qwen2.5-coder:7b"

# Local Ollama daemon endpoint
endpoint = "http://127.0.0.1:11434"

# Maximum code context tokens to send to the model
max_context_tokens = 4000

[privacy]
# Automatically redact API keys, passwords, and tokens before model dispatch
redact_secrets = true

[reports]
# Default report format(s): "markdown", "json", or "both"
formats = ["markdown"]

# Optional directory to write review reports
output_dir = "reports"
```

---

## CLI Overrides

Any configuration option can be overridden at runtime via CLI flags:

| Config Key | CLI Option | Description |
|---|---|---|
| `review.base_ref` | `--base <ref>` | Base git revision |
| `scanners.enabled` | `--scanners <list>` | Comma-separated list of scanners |
| `ai.enabled` | `--ai` | Enable local AI review |
| `ai.model` | `--model <name>` | Model tag for Ollama |
| `reports.formats` | `-f, --format <fmt>` | `markdown`, `json`, or `both` |
| `reports.output_dir` | `-o, --output <file>` | Path to save output report |
