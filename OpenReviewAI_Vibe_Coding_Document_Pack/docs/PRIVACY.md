# OpenReview AI Privacy & Security Architecture

## 1. Core Privacy Position & Boundary Disclaimers

OpenReview AI is designed with a **privacy-conscious, local-first engineering philosophy**. However, security and privacy boundaries vary significantly depending on user configuration, operational environment, and model deployment choices.

### What OpenReview AI Is:
- **Local-first:** The core review pipeline is engineered to execute directly on the developer's workstation or on self-hosted infrastructure.
- **Local AI by default:** The default AI configuration targets a local loopback Ollama daemon (`http://127.0.0.1:11434`), requiring no cloud API accounts, third-party subscriptions, or proprietary model access tokens.
- **Capable of operating offline for its core components:** The Git diff parser, rule engine, static scanners (Ruff, ESLint, REA binary integrity checker), evidence validation, and report generators function 100% offline without network connectivity.

### What OpenReview AI Is NOT Automatically Guaranteed To Be:
- **NOT guaranteed to be completely private under every configuration:**
  The configuration file (`.openreview.toml`) allows users to specify an arbitrary AI endpoint (`[ai] endpoint = "http://..."`). If pointed to an external or remote endpoint, **source code context will be transmitted over the network to that remote server**.
- **NOT guaranteed to be completely secret-free before model dispatch:**
  Secret redaction is an automated, regex-based heuristic filter. It **cannot guarantee detection of every credential, proprietary value, bespoke token, or sensitive identifier**. Developers must not rely on redaction as an absolute guarantee of confidentiality.
- **NOT guaranteed to be air-gapped in every execution mode:**
  Operating OpenReview AI in connected environments—such as a GitHub App webhook server, PR reporting integrations, or CI/CD pipelines pulling external container images—involves network transmission and interaction with external APIs.

---

## 2. AI Model Communication & Endpoint Boundaries

### Default Loopback Behavior
When AI review is enabled, OpenReview AI defaults to querying Ollama at:
```text
http://127.0.0.1:11434
```
At this default loopback address, communication occurs entirely through the local host's network stack without packets traversing an external network interface.

### Remote Endpoint Risk & Network Transmission
Configuration permits arbitrary endpoints:
```toml
[ai]
enabled = true
endpoint = "http://remote-server.internal.corp:11434"
```
When configured with a non-loopback endpoint:
1. Source code hunks, file paths, and review context are serialized into JSON and transmitted over HTTP to the specified remote host.
2. The execution is **no longer air-gapped or strictly local**.
3. The remote server may log requests, retain prompts, or expose data to network eavesdropping if unencrypted HTTP is used.

### Loopback Verification & Enforcement Controls
To protect against unintended data exfiltration:
- **Automatic Endpoint Inspection:** `OllamaAdapter` inspects the configured endpoint using `is_loopback_endpoint()`. It verifies whether the target hostname resolves to a loopback address (`localhost`, `127.0.0.0/8`, `::1`, or `0.0.0.0`).
- **Remote Endpoint Warning:** When a non-loopback endpoint is detected, OpenReview AI emits a prominent warning log:
  ```text
  WARNING: Ollama endpoint 'http://remote-server:11434' is not a local loopback address (127.0.0.1/localhost). Source code context will be transmitted across the network to a remote host. This configuration is NOT air-gapped.
  ```
- **Strict Loopback Enforcement (`enforce_local_ai`):**
  Users who require strict loopback isolation can set:
  ```toml
  [privacy]
  redact_secrets = true
  enforce_local_ai = true
  ```
  When `enforce_local_ai = true`, any attempt to configure a remote or non-loopback endpoint causes an immediate rejection (`ValueError`), preventing accidental network transmission.

---

## 3. Secret Redaction: Capabilities & Inherent Limitations

Before code context blocks are passed to the AI provider, they pass through an automated redaction filter (`src/openreview/security/redaction.py`).

### Standard Patterns Redacted
The filter uses regular expressions to detect and substitute common credential patterns with `[REDACTED_SECRET]`:
- **Cloud Provider Keys:** AWS Access Key IDs (`AKIA...`, `ASIA...`, `AROA...`) and secret keys
- **Private Key Blocks:** PEM-encoded RSA, EC, DSA, and OpenSSH private keys (`-----BEGIN ... PRIVATE KEY-----`)
- **API Tokens & PATs:** GitHub Personal Access Tokens (`ghp_...`, `github_pat_...`), Slack tokens (`xoxb-...`), GitLab tokens
- **Authentication Headers:** Standard Bearer tokens and JSON Web Tokens (`eyJ...`)
- **Common Password & Secret Assignments:** Variables matching `password`, `passwd`, `api_key`, or `client_secret` assignments

### Inherent Limitations of Regex Redaction
Regex redaction is a **defense-in-depth safety net, not an absolute cryptographic or privacy guarantee**:
1. **Bespoke / Custom Formats:** High-entropy random strings, proprietary internal API keys, database connection strings, and tokens without recognized structural prefixes are not detected.
2. **Proprietary & Business Logic:** Confidential business logic, intellectual property, domain-specific algorithms, and internal URLs cannot be classified as secrets by static patterns.
3. **Evasion & Obfuscation:** String concatenation, encoding (base64, hex), unusual variable naming, or split tokens will bypass regex matchers.
4. **No Guarantee of Secret-Free Context:** Code sent to local or remote AI models may still contain sensitive context.

> [!IMPORTANT]
> **Best Practice:** Keep secrets out of code. Use environment variables, secret managers (e.g., Vault, AWS Secrets Manager), and `.gitignore` configurations. Never rely solely on client-side regex redaction to safeguard sensitive credentials.

---

## 4. Subprocess Isolation & Sandboxing Boundaries

External scanners (Ruff, ESLint, REA) and tools operate within distinct isolation boundaries:

### CLI Execution
- **Argument Vector Validation:** External binaries are invoked via validated argument arrays (`list[str]`) without shell interpolation (`shell=False`), preventing command injection.
- **Control Character Rejection:** Arguments containing null bytes (`\0`), carriage returns (`\r`), or newlines (`\n`) are rejected before subprocess invocation.
- **Path Traversal Guards:** File paths are validated against repository boundaries (`validate_safe_path`) to prevent directory traversal (`../../`).

### GitHub PR Worker Sandboxing
Untrusted PRs and third-party scanner configurations (e.g., ESLint plugins) are isolated using dedicated container boundaries:
- **Non-Root Execution:** Scanners run as an unprivileged user (UID 1000).
- **Capability Dropping:** All Linux capabilities dropped (`--cap-drop=ALL`).
- **Filesystem Hardening:** Root filesystem mounted read-only (`--read-only`), with a size-limited temporary `tmpfs` directory for scratch files.
- **No Docker Socket Access:** The host Docker socket is never mounted into the analysis container.
- **Network Isolation:** Network access is disabled (`--network=none`) during scanner execution to prevent external code or malicious configs from exfiltrating repository contents.
- **Resource Limits:** CPU and memory limits are enforced via cgroups (`--memory`, `--cpus`).

---

## 5. Telemetry & Data Retention Policy

- **Zero Telemetry:** OpenReview AI contains **no tracking beacons, analytics services, phone-home mechanisms, or third-party error-reporting services**.
- **No Cloud Account Required:** CLI operations require no registration, telemetry opt-in, or vendor license keys.
- **Data Retention:**
  - In CLI mode, reviews are ephemeral and operate strictly in memory unless explicitly exported to a local report file.
  - In GitHub App mode, review jobs and status are tracked in a local SQLite database (`job_queue.db`) on the host machine and are not shared with external entities.

---

## 6. Component Privacy & Network Profile Matrix

| Component | Default Mode | Network Requirement | Data Transmitted Across Network | Privacy Boundary |
| :--- | :--- | :--- | :--- | :--- |
| **Git Diff Parser** | Local | None (100% Offline) | None | Local host memory |
| **Static Linters (Ruff, ESLint)** | Local subprocess | None (100% Offline) | None | Local host / container sandbox |
| **REA Binary Inspector** | Local subprocess | None (100% Offline) | None | Local host / binary inspection |
| **Report Generator (MD, JSON)** | Local | None (100% Offline) | None | Local host filesystem |
| **Local AI (Ollama)** | Local loopback | Local loopback (`127.0.0.1`) | Redacted context sent to local loopback socket | Host loopback boundary |
| **Remote AI (Custom Endpoint)** | Optional / Configured | Remote HTTP/HTTPS | Redacted context sent across external network | External network / remote server |
| **GitHub Webhook Server** | Server / App mode | Inbound HTTP | Webhook payloads from GitHub | Public or proxy network boundary |
| **GitHub PR Reporting** | Server / App mode | Outbound HTTPS | Checks API findings and inline PR comments | GitHub REST API |
| **PR Container Sandbox** | Worker execution | Disabled (`--network=none`) | None during analysis | Docker container boundary |
