"""GitHub App authentication, RS256 JWT generation, and installation token lifecycle management."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import jwt
    from cryptography.hazmat.primitives import serialization

    HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    jwt = None  # type: ignore
    serialization = None  # type: ignore
    HAS_CRYPTO = False

from openreview.integrations.github_client import GitHubClient

logger = logging.getLogger(__name__)


class GitHubAuthError(ValueError):
    """Raised when GitHub App authentication or token exchange fails."""


@dataclass(frozen=True)
class InstallationToken:
    """An installation access token with expiry metadata."""

    token: str
    expires_at: str
    installation_id: int
    permissions: dict[str, Any]

    @property
    def is_expired(self) -> bool:
        """True if the token is expired or within 5 minutes of expiration."""
        try:
            # Parse ISO-8601 timestamp
            exp_str = self.expires_at.replace("Z", "+00:00")
            exp_dt = datetime.fromisoformat(exp_str)
            now_dt = datetime.now(UTC)
            # 300 second buffer for proactive refresh
            return (exp_dt.timestamp() - now_dt.timestamp()) < 300
        except (ValueError, TypeError):
            return True


class GitHubAppAuth:
    """Manages GitHub App authentication, RS256 JWT generation, and token lifecycles."""

    def __init__(
        self,
        app_id: str | int,
        private_key: str | bytes | Path,
        base_url: str = "https://api.github.com",
    ) -> None:
        if not HAS_CRYPTO or jwt is None or serialization is None:
            raise GitHubAuthError(
                "GitHub App authentication requires 'PyJWT' and 'cryptography'. "
                "Install them with: pip install PyJWT cryptography"
            )
        self.app_id = str(app_id).strip()
        if not self.app_id:
            raise GitHubAuthError("app_id must be a non-empty string or integer")

        self.base_url = base_url.rstrip("/")
        self._private_key_bytes = self._load_private_key(private_key)
        self._lock = threading.Lock()
        self._token_cache: dict[int, InstallationToken] = {}

    @staticmethod
    def _load_private_key(key_input: str | bytes | Path) -> bytes:
        """Normalize private key input from file path, string, or bytes."""
        if isinstance(key_input, Path):
            if not key_input.is_file():
                raise GitHubAuthError(f"Private key file does not exist: {key_input}")
            return key_input.read_bytes()

        if isinstance(key_input, str):
            candidate_path = Path(key_input)
            if candidate_path.is_file():
                return candidate_path.read_bytes()
            # If not a file, treat as raw PEM string
            return key_input.encode("utf-8")

        if isinstance(key_input, bytes):
            return key_input

        raise GitHubAuthError(
            f"Unsupported private key type: {type(key_input).__name__}"
        )

    def generate_jwt(self, expiration_seconds: int = 600) -> str:
        """Generate an RS256 signed JSON Web Token (JWT) for GitHub App authentication."""
        now = int(time.time())
        # GitHub allows maximum 10 minute expiration (600s). Include 60s clock drift buffer.
        payload = {
            "iat": now - 60,
            "exp": now + min(expiration_seconds, 600),
            "iss": self.app_id,
        }

        try:
            # Ensure valid private key PEM format
            key = serialization.load_pem_private_key(
                self._private_key_bytes, password=None
            )
            token = jwt.encode(
                payload,
                key,
                algorithm="RS256",
                headers={"alg": "RS256", "typ": "JWT"},
            )
            return token if isinstance(token, str) else token.decode("utf-8")
        except Exception as exc:
            raise GitHubAuthError(
                f"Failed to generate RS256 GitHub App JWT: {exc}"
            ) from exc

    def get_installation_access_token(
        self,
        installation_id: int,
        force_refresh: bool = False,
    ) -> InstallationToken:
        """Obtain an installation access token, using in-memory cached token if valid."""
        with self._lock:
            cached = self._token_cache.get(installation_id)
            if cached and not cached.is_expired and not force_refresh:
                return cached

            # Generate fresh App JWT
            app_jwt = self.generate_jwt()
            url = f"{self.base_url}/app/installations/{installation_id}/access_tokens"

            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                    "User-Agent": f"OpenReview-AI-GitHub-App/{self.app_id}",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_bytes = resp.read()
                    data = json.loads(resp_bytes.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_body = (
                    exc.read().decode("utf-8", errors="replace")
                    if hasattr(exc, "read")
                    else ""
                )
                raise GitHubAuthError(
                    f"GitHub App token exchange failed with status {exc.code} for installation {installation_id}: {error_body}"
                ) from exc
            except Exception as exc:
                raise GitHubAuthError(
                    f"Network error exchanging installation token: {exc}"
                ) from exc

            token_str = data.get("token")
            expires_at = data.get("expires_at")
            permissions = data.get("permissions", {})

            if not token_str or not expires_at:
                raise GitHubAuthError(f"Malformed token response from GitHub: {data}")

            token = InstallationToken(
                token=token_str,
                expires_at=expires_at,
                installation_id=installation_id,
                permissions=permissions,
            )
            self._token_cache[installation_id] = token
            logger.info(
                "Acquired fresh GitHub installation token for installation ID %s",
                installation_id,
            )
            return token

    def get_installation_client(self, installation_id: int) -> GitHubClient:
        """Construct a GitHubClient configured with an active installation token."""
        token_info = self.get_installation_access_token(installation_id)
        return GitHubClient(token=token_info.token, base_url=self.base_url)
