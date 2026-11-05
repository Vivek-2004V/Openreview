# GitHub App Integration Architecture & Webhook Contract

This document specifies the GitHub App permissions, webhook event contract, idempotency model, and security policies for OpenReview AI per Epic E6.

---

## 1. GitHub App Permissions (Least Privilege)

OpenReview AI operates strictly under the principle of least privilege. The GitHub App requires only the following scoped permissions:

| Permission | Access Level | Purpose |
|---|---|---|
| **Pull Requests** | `Read & Write` | Post top-level review summaries and line-specific inline comments |
| **Checks** | `Read & Write` | Report review pass/fail status and finding summaries to commit status |
| **Contents** | `Read-only` | Fetch commit diffs, tree metadata, and configuration files |
| **Metadata** | `Read-only` | Mandatory GitHub App default |

### Prohibited Permissions
OpenReview AI **NEVER** requests:
- Administration or Repository permissions
- Workflows or Actions write permissions
- Secrets or Environments access
- Organization management access

---

## 2. Webhook Event Subscriptions

The app subscribes only to essential lifecycle events:

1. `pull_request`:
   - `opened`: Initial PR review triggered.
   - `synchronize`: New commits pushed to PR branch; triggers incremental review.
   - `reopened`: Re-evaluates open status.
2. `installation`:
   - `created`: Onboards organization/user repositories.
   - `deleted`: Purges cached workspace tokens and queues cleanly.

---

## 3. Webhook Contract & Headers

Incoming HTTP POST requests must include:

| Header | Description |
|---|---|
| `X-GitHub-Event` | Name of the event (`pull_request`, `installation`) |
| `X-GitHub-Delivery` | Unique GUID identifier for the webhook delivery |
| `X-Hub-Signature-256` | HMAC-SHA256 signature calculated with webhook secret: `sha256=<hex_digest>` |
| `Content-Type` | `application/json` |

---

## 4. Webhook Payload Schema

### Pull Request Event (`pull_request`)
```json
{
  "action": "opened",
  "number": 42,
  "pull_request": {
    "id": 123456789,
    "number": 42,
    "title": "feat: add user authentication",
    "head": {
      "sha": "a1b2c3d4e5f678901234567890abcdef12345678",
      "ref": "feature/auth"
    },
    "base": {
      "sha": "0987654321fedcba0987654321fedcba09876543",
      "ref": "main"
    }
  },
  "repository": {
    "id": 987654,
    "name": "sample-repo",
    "full_name": "acme/sample-repo",
    "clone_url": "https://github.com/acme/sample-repo.git"
  },
  "installation": {
    "id": 554433
  }
}
```

---

## 5. Security & Threat Mitigation

1. **Signature Verification:** Constant-time `hmac.compare_digest` prevents timing attacks on signatures.
2. **Replay Protection:** Deduplication cache keys on `X-GitHub-Delivery` and `(repo_id, pr_number, head_sha)`.
3. **Untrusted Forks (Container Sandbox Isolation):** PRs from external forks execute inside a strictly hardened container sandbox (`DockerSandboxRunner`):
   - Non-root UID/GID (`1000:1000`) with `--cap-drop ALL` and `no-new-privileges:true`.
   - Complete network airgap (`--network none`) preventing exfiltration and unverified package downloads.
   - Resource bounding: `--cpus 1.0`, `--memory 512m`, `--pids-limit 100`, and strict execution timeouts.
   - Read-only source checkout mount (`:readonly`) and read-only container root (`--read-only`).
   - Writable storage restricted to size-capped, non-executable tmpfs (`/tmp:rw,noexec,nosuid,size=64m`).
   - Strict credential separation: GitHub App tokens and reporting credentials reside on the host worker and are never accessible within the scanner container. Zero Docker socket access.
4. **Token Rotation:** GitHub App installation access tokens expire after 60 minutes and are never persisted to disk.

---

## 6. Production GitHub App Runtime & Authentication

### 6.1 GitHub App RS256 JWT Generation & Installation Tokens
OpenReview AI authenticates as an official GitHub App per RFC 7519 / GitHub App Specifications:
- **JWT Generation:** Signs RS256 JSON Web Tokens with the App ID as issuer (`iss`), 60-second clock drift allowance (`iat`), and maximum 10-minute validity (`exp`).
- **Token Exchange:** Calls `POST /app/installations/{installation_id}/access_tokens` using the bearer JWT.
- **In-Memory Token Cache:** Cached tokens are proactively refreshed whenever within 5 minutes of expiration, ensuring zero API interruption while avoiding disk persistence.

### 6.2 Persistent Job Queue & Durable Crash Recovery
The review queue (`ReviewJobQueue`) provides SQLite-backed ACID persistence:
- **Persistent Storage:** Queued tasks, delivery tracking, and execution histories survive worker restarts in `openreview_jobs.db`.
- **Durable Recovery:** Upon restart, any tasks left in `PROCESSING` status are inspected. If retries remain (`attempts < max_retries`), they are automatically re-queued to `PENDING` to prevent dropped reviews.

### 6.3 End-to-End Reporting Orchestration
Reviews and Checks API runs are orchestrated synchronously via `PRReportingOrchestrator`:
1. **Checks API Synchronization:** Creates check runs marked `in_progress` and updates them to `completed` (`success` or `failure`).
2. **Review Decision Determination:** Dynamically selects `APPROVE`, `REQUEST_CHANGES`, or `COMMENT` based on severity metrics.
3. **Diff-Hunk Inline Mapping:** Anchors comments strictly within PR diff hunks. In the event of a GitHub `422 Unprocessable Entity` response, gracefully falls back to appending findings to the top-level review body.

---

## 7. Production Deployment & Operations

### CLI Server Invocation
```bash
# Start webhook server and embedded worker
openreview server \
  --host 0.0.0.0 \
  --port 8000 \
  --secret "$GITHUB_WEBHOOK_SECRET" \
  --app-id "$GITHUB_APP_ID" \
  --private-key /path/to/private-key.pem \
  --db /data/openreview_jobs.db \
  --worker
```

### Docker Compose Deployment
```bash
docker compose -f deploy/docker-compose.yml up -d
```

### Server Endpoints
- `POST /webhook`: GitHub App delivery receiver (enforces HMAC signature unconditionally).
- `GET /health` or `GET /healthz`: Health probe returning status and queue metrics.
- `GET /status`: Job status and throughput metrics.
- `GET /jobs/{job_id}`: Job inspection endpoint.
