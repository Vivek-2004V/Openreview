"""Thread-safe sliding-window rate limiting for HTTP endpoints and APIs."""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Mapping
from typing import Any


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter tracking requests per client identifier."""

    def __init__(self, limit: int = 60, window_seconds: float = 60.0) -> None:
        if limit <= 0:
            raise ValueError("Rate limit must be greater than 0")
        if window_seconds <= 0:
            raise ValueError("Window seconds must be greater than 0")

        self.limit = limit
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._records: dict[str, collections.deque[float]] = collections.defaultdict(
            collections.deque
        )

    def is_allowed(self, client_id: str) -> tuple[bool, int, float]:
        """Check if request from client_id is allowed under the current rate limit.

        Returns:
            (allowed: bool, remaining: int, retry_after: float)
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._records[client_id]

            # Evict timestamps outside sliding window
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) < self.limit:
                timestamps.append(now)
                remaining = self.limit - len(timestamps)
                return True, remaining, 0.0

            # Exceeded limit: calculate retry-after duration based on oldest entry in window
            oldest = timestamps[0]
            retry_after = max(0.1, round((oldest + self.window_seconds) - now, 2))
            return False, 0, retry_after

    def reset(self) -> None:
        """Clear all tracking state."""
        with self._lock:
            self._records.clear()


def get_client_ip(
    headers: Mapping[str, Any],
    client_address: tuple[str, int] | str | None = None,
) -> str:
    """Extract client IP from request headers or socket address safely."""
    # Look for proxy headers (case-insensitive lookup)
    header_map = {k.lower(): v for k, v in headers.items()}

    x_forwarded_for = header_map.get("x-forwarded-for")
    if x_forwarded_for:
        # Take first IP in comma-separated chain
        client_ip = str(x_forwarded_for).split(",")[0].strip()
        if client_ip:
            return client_ip

    x_real_ip = header_map.get("x-real-ip")
    if x_real_ip:
        client_ip = str(x_real_ip).strip()
        if client_ip:
            return client_ip

    if client_address:
        if isinstance(client_address, tuple) and len(client_address) > 0:
            return str(client_address[0])
        return str(client_address)

    return "unknown"
