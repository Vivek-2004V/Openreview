"""GitHub webhook signature verification and payload normalization per TRD Section 8."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class WebhookSignatureError(ValueError):
    """Raised when GitHub webhook signature verification fails."""


class WebhookPayloadError(ValueError):
    """Raised when required webhook payload fields are missing or malformed."""


def verify_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify GitHub webhook payload against HMAC-SHA256 signature in constant time."""
    if not signature_header or not secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header[7:].strip()
    calculated_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(calculated_signature, expected_signature)


@dataclass(frozen=True)
class WebhookEvent:
    """Normalized GitHub webhook event envelope."""

    event_type: str
    delivery_id: str
    action: str
    repo_full_name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PullRequestEventData:
    """Normalized pull request payload fields needed for review execution."""

    action: str
    pr_number: int
    repo_full_name: str
    clone_url: str
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    installation_id: int | None = None

    @property
    def is_reviewable_action(self) -> bool:
        """True if action should trigger an automated review."""
        return self.action in ("opened", "synchronize", "reopened")


@dataclass(frozen=True)
class InstallationEventData:
    """Normalized installation event for repository onboarding/offboarding."""

    action: str
    installation_id: int
    sender: str
    repositories: list[str] = field(default_factory=list)


def parse_webhook_event(
    headers: Mapping[str, str],
    payload_bytes: bytes,
    secret: str | None = None,
    allow_unauthenticated: bool = False,
) -> WebhookEvent:
    """Parse, authenticate, and validate an incoming GitHub webhook request.

    By default, requires a valid HMAC-SHA256 signature calculated against a non-empty secret.
    Unauthenticated parsing is strictly prohibited unless explicitly enabled via
    allow_unauthenticated=True (e.g. for synthetic unit tests).
    """
    # Case-insensitive header lookup
    normalized_headers = {k.lower(): v for k, v in headers.items()}

    event_type = normalized_headers.get("x-github-event")
    if not event_type:
        raise WebhookPayloadError("Missing X-GitHub-Event header")

    delivery_id = normalized_headers.get("x-github-delivery")
    if not delivery_id:
        raise WebhookPayloadError("Missing X-GitHub-Delivery header")

    # Enforce signature verification
    if not allow_unauthenticated:
        if not secret or not secret.strip():
            raise WebhookSignatureError(
                "Webhook secret must be provided; unauthenticated webhook requests are strictly prohibited in production"
            )
        sig_header = normalized_headers.get("x-hub-signature-256")
        if not sig_header or not verify_github_signature(
            payload_bytes, sig_header, secret
        ):
            raise WebhookSignatureError(
                "Invalid or missing X-Hub-Signature-256 signature"
            )
    elif secret is not None:
        sig_header = normalized_headers.get("x-hub-signature-256")
        if not verify_github_signature(payload_bytes, sig_header, secret):
            raise WebhookSignatureError(
                "Invalid or missing X-Hub-Signature-256 signature"
            )

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebhookPayloadError(f"Malformed JSON payload: {exc}") from exc

    action = str(payload.get("action", ""))
    repo_data = payload.get("repository", {})
    repo_name = str(repo_data.get("full_name", ""))

    return WebhookEvent(
        event_type=event_type,
        delivery_id=delivery_id,
        action=action,
        repo_full_name=repo_name,
        payload=payload,
    )


def parse_and_verify_webhook_event(
    headers: Mapping[str, str],
    payload_bytes: bytes,
    secret: str,
) -> WebhookEvent:
    """Production helper: strictly parses and verifies a GitHub webhook event.

    Unconditionally requires a non-empty secret and valid HMAC-SHA256 signature.
    """
    if not secret or not secret.strip():
        raise WebhookSignatureError("Webhook secret must be non-empty")
    return parse_webhook_event(
        headers, payload_bytes, secret=secret, allow_unauthenticated=False
    )


def extract_pr_event_data(event: WebhookEvent) -> PullRequestEventData:
    """Extract and validate pull request details from a WebhookEvent."""
    if event.event_type != "pull_request":
        raise WebhookPayloadError(
            f"Expected event_type 'pull_request', got '{event.event_type}'"
        )

    pr_dict = event.payload.get("pull_request")
    if not pr_dict:
        raise WebhookPayloadError("Payload missing 'pull_request' object")

    repo_dict = event.payload.get("repository", {})
    clone_url = str(repo_dict.get("clone_url", ""))
    repo_name = str(repo_dict.get("full_name", ""))
    pr_number = int(event.payload.get("number") or pr_dict.get("number", 0))

    head_info = pr_dict.get("head", {})
    base_info = pr_dict.get("base", {})

    head_sha = str(head_info.get("sha", "")).strip()
    base_sha = str(base_info.get("sha", "")).strip()
    head_ref = str(head_info.get("ref", "")).strip()
    base_ref = str(base_info.get("ref", "")).strip()

    if not head_sha or not base_sha:
        raise WebhookPayloadError(
            "Missing head or base commit SHA in pull request payload"
        )

    installation = event.payload.get("installation", {})
    inst_id = installation.get("id") if isinstance(installation, dict) else None

    return PullRequestEventData(
        action=event.action,
        pr_number=pr_number,
        repo_full_name=repo_name,
        clone_url=clone_url,
        head_sha=head_sha,
        base_sha=base_sha,
        head_ref=head_ref,
        base_ref=base_ref,
        installation_id=int(inst_id) if inst_id else None,
    )
