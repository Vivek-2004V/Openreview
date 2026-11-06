"""Authentication, authorization, and cryptographic password hashing per Gate D & P2."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import jwt

logger = logging.getLogger(__name__)

ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


class AuthenticationError(Exception):
    """Raised when authentication credentials or tokens are invalid, missing, or expired."""


class AuthorizationError(Exception):
    """Raised when an authenticated principal lacks required permissions for a resource."""


def hash_password(password: str) -> str:
    """Hash password using cryptographic scrypt with a unique random salt.

    Format: scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
    """
    if not password:
        raise ValueError("Password cannot be empty")

    salt = os.urandom(16)
    n = 16384
    r = 8
    p = 1
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${n}${r}${p}${salt.hex()}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against scrypt or PBKDF2 hash using constant-time comparison."""
    if not password or not hashed:
        return False

    parts = hashed.split("$")
    if len(parts) < 4:
        return False

    scheme = parts[0]
    try:
        if scheme == "scrypt" and len(parts) == 6:
            _, n_str, r_str, p_str, salt_hex, key_hex = parts
            n = int(n_str)
            r = int(r_str)
            p = int(p_str)
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(key_hex)
            derived = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=n,
                r=r,
                p=p,
                maxmem=64 * 1024 * 1024,
            )
            return hmac.compare_digest(derived, expected_key)

        if scheme == "pbkdf2" and len(parts) == 4:
            _, iter_str, salt_hex, key_hex = parts
            iterations = int(iter_str)
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(key_hex)
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                iterations,
            )
            return hmac.compare_digest(derived, expected_key)
    except Exception as exc:
        logger.debug("Password verification failed with error: %s", exc)
        return False

    return False


def create_access_token(
    subject: str,
    secret_key: str,
    expires_in_seconds: int = 3600,
    scopes: list[str] | None = None,
    claims: dict[str, Any] | None = None,
    algorithm: str = "HS256",
) -> str:
    """Generate a signed JWT access token with expiration, subject, and scope claims.

    Rejects algorithm 'none' and unapproved signing algorithms.
    """
    if not secret_key or not secret_key.strip():
        raise ValueError("secret_key must be a non-empty string")
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty string")

    algo_upper = algorithm.upper()
    if algo_upper == "NONE" or algo_upper not in ALLOWED_JWT_ALGORITHMS:
        raise ValueError(
            f"Algorithm '{algorithm}' is not permitted. Must be one of: {sorted(ALLOWED_JWT_ALGORITHMS)}"
        )

    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + max(1, expires_in_seconds),
        "scopes": scopes or ["read"],
    }
    if claims:
        # Prevent overriding critical standard claims
        for k, v in claims.items():
            if k not in ("sub", "iat", "exp"):
                payload[k] = v

    return jwt.encode(payload, secret_key, algorithm=algo_upper)


def verify_access_token(
    token: str,
    secret_key: str,
    required_scope: str | None = None,
    allowed_algorithms: list[str] | None = None,
) -> dict[str, Any]:
    """Verify and decode a JWT access token, enforcing expiration, algorithm safety, and scopes.

    Raises AuthenticationError if invalid or expired.
    Raises AuthorizationError if required_scope is missing.
    """
    if not token or not token.strip():
        raise AuthenticationError("Authorization token is missing or empty")
    if not secret_key or not secret_key.strip():
        raise AuthenticationError("Server secret_key is not configured")

    algos = allowed_algorithms or ["HS256"]
    # Explicitly check algorithms list does not contain 'none'
    if any(a.lower() == "none" for a in algos):
        raise AuthenticationError("Prohibited algorithm 'none' specified in allowed list")

    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=algos,
            options={
                "require": ["exp", "sub", "iat"],
                "verify_exp": True,
                "verify_iat": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid authentication token: {exc}") from exc

    if required_scope:
        token_scopes = payload.get("scopes", [])
        if not isinstance(token_scopes, list) or required_scope not in token_scopes:
            raise AuthorizationError(
                f"Token lacks required scope '{required_scope}'"
            )

    return payload


def authorize_job_access(
    claims: dict[str, Any],
    job_id: str,
    job_repo: str | None = None,
) -> bool:
    """Verify whether authenticated token claims authorize access to a specific job."""
    # 1. Admin bypass
    scopes = claims.get("scopes", [])
    if isinstance(scopes, list) and ("admin" in scopes or "*" in scopes):
        return True
    if claims.get("role") == "admin":
        return True

    # 2. Specific job ID assignment
    allowed_jobs = claims.get("allowed_jobs", [])
    if isinstance(allowed_jobs, list) and (job_id in allowed_jobs or "*" in allowed_jobs):
        return True

    # 3. Repository authorization
    if job_repo:
        allowed_repos = claims.get("allowed_repos", [])
        if isinstance(allowed_repos, list) and (job_repo in allowed_repos or "*" in allowed_repos):
            return True

    return False


def authorize_repo_access(
    claims: dict[str, Any],
    repo_full_name: str,
    required_scope: str = "read",
) -> bool:
    """Verify whether authenticated token claims authorize access to a given repository."""
    scopes = claims.get("scopes", [])
    if not isinstance(scopes, list):
        return False

    if "admin" in scopes or "*" in scopes:
        return True

    if required_scope not in scopes:
        return False

    allowed_repos = claims.get("allowed_repos", [])
    if isinstance(allowed_repos, list) and (repo_full_name in allowed_repos or "*" in allowed_repos):
        return True

    return False
