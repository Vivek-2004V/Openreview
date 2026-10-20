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
3. **Untrusted Forks:** PRs from external forks execute in ephemeral, unprivileged sandbox worktrees with network access restricted.
4. **Token Rotation:** GitHub App installation access tokens expire after 60 minutes and are never persisted to disk.
