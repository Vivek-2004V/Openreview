"""Production HTTP webhook server for receiving and processing GitHub App events."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self

from openreview import __version__
from openreview.integrations.github_app_auth import GitHubAppAuth
from openreview.integrations.github_webhook import (
    WebhookPayloadError,
    WebhookSignatureError,
    extract_pr_event_data,
    parse_and_verify_webhook_event,
)
from openreview.integrations.job_queue import ReviewJobQueue
from openreview.integrations.worker import ReviewWorker
from openreview.security.auth import (
    AuthenticationError,
    AuthorizationError,
    authorize_job_access,
    verify_access_token,
)
from openreview.security.rate_limiter import (
    SlidingWindowRateLimiter,
    get_client_ip,
)

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB limit to prevent memory exhaustion DoS


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for GitHub webhook deliveries and health checks."""

    server: WebhookHTTPServer  # type annotation for custom server

    def _send_json_response(
        self,
        status_code: int,
        data: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Send formatted JSON HTTP response with production security headers."""
        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.send_header(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp_bytes)

    def _check_rate_limit(self) -> bool:
        """Enforce rate limits per client IP if rate limiter is configured."""
        if getattr(self.server, "rate_limiter", None) is None:
            return True

        client_ip = get_client_ip(dict(self.headers), self.client_address)
        allowed, remaining, retry_after = self.server.rate_limiter.is_allowed(client_ip)
        if not allowed:
            self._send_json_response(
                429,
                {"error": "Too Many Requests", "retry_after": retry_after},
                extra_headers={"Retry-After": str(int(retry_after) or 1)},
            )
            return False
        return True

    def do_GET(self) -> None:
        """Handle health check and status probes."""
        if not self._check_rate_limit():
            return

        path = self.path.split("?")[0].rstrip("/")

        if path in ("/health", "/healthz", ""):
            self._send_json_response(
                200,
                {
                    "status": "healthy",
                    "version": __version__,
                    "queue_size": self.server.queue.queue_size,
                    "storage": "persistent"
                    if self.server.queue.is_persistent
                    else "memory",
                },
            )
            return

        if path == "/status" or path == "/metrics":
            all_jobs = self.server.queue.get_all_jobs()
            by_status: dict[str, int] = {}
            for j in all_jobs:
                by_status[j.status.value] = by_status.get(j.status.value, 0) + 1

            self._send_json_response(
                200,
                {
                    "version": __version__,
                    "queue_size": self.server.queue.queue_size,
                    "total_jobs": len(all_jobs),
                    "jobs_by_status": by_status,
                },
            )
            return

        if path.startswith("/jobs/"):
            job_id = path[len("/jobs/") :]
            job = self.server.queue.get_job(job_id)
            if not job:
                self._send_json_response(404, {"error": f"Job not found: {job_id}"})
                return

            if getattr(self.server, "api_secret", None):
                auth_header = self.headers.get("Authorization", "")
                if not auth_header.startswith("Bearer "):
                    self._send_json_response(
                        401, {"error": "Missing Bearer authorization token"}
                    )
                    return
                token = auth_header[len("Bearer ") :].strip()
                try:
                    claims = verify_access_token(token, self.server.api_secret)
                    if not authorize_job_access(claims, job_id, job.repo_full_name):
                        self._send_json_response(
                            403, {"error": "Forbidden: Access denied to requested job"}
                        )
                        return
                except AuthenticationError as exc:
                    self._send_json_response(401, {"error": f"Unauthorized: {exc}"})
                    return
                except AuthorizationError as exc:
                    self._send_json_response(403, {"error": f"Forbidden: {exc}"})
                    return

            self._send_json_response(200, job.to_dict())
            return

        self._send_json_response(404, {"error": "Not found"})

    def do_POST(self) -> None:
        """Handle incoming GitHub webhook POST deliveries."""
        if not self._check_rate_limit():
            return

        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/webhook", "/api/webhook", ""):
            self._send_json_response(
                404, {"error": f"Invalid webhook endpoint: {path}"}
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json_response(400, {"error": "Invalid Content-Length header"})
            return

        if content_length <= 0:
            self._send_json_response(400, {"error": "Empty webhook payload"})
            return

        if content_length > MAX_PAYLOAD_BYTES:
            self._send_json_response(
                413,
                {
                    "error": f"Payload Too Large: {content_length} bytes exceeds maximum allowed limit of {MAX_PAYLOAD_BYTES} bytes"
                },
            )
            return

        payload_bytes = self.rfile.read(content_length)

        # Enforce authentication unconditionally
        try:
            event = parse_and_verify_webhook_event(
                headers=dict(self.headers),
                payload_bytes=payload_bytes,
                secret=self.server.webhook_secret,
            )
        except WebhookSignatureError as exc:
            logger.warning("Rejected unauthenticated webhook request: %s", exc)
            self._send_json_response(401, {"error": f"Unauthorized: {exc}"})
            return
        except WebhookPayloadError as exc:
            logger.warning("Rejected malformed webhook payload: %s", exc)
            self._send_json_response(400, {"error": f"Bad Request: {exc}"})
            return
        except (ValueError, OSError) as exc:
            logger.exception("Unexpected error parsing webhook request: %s", exc)
            self._send_json_response(500, {"error": "Internal server error"})
            return

        # 1. Handle ping event
        if event.event_type == "ping":
            logger.info("Received GitHub ping webhook delivery: %s", event.delivery_id)
            self._send_json_response(
                200,
                {
                    "status": "pong",
                    "delivery_id": event.delivery_id,
                    "zen": event.payload.get("zen", ""),
                },
            )
            return

        # 2. Handle pull_request event
        if event.event_type == "pull_request":
            try:
                pr_data = extract_pr_event_data(event)
            except WebhookPayloadError as exc:
                self._send_json_response(
                    400, {"error": f"Invalid pull_request payload: {exc}"}
                )
                return

            if not pr_data.is_reviewable_action:
                logger.info(
                    "Skipping non-reviewable pull_request action '%s' for PR #%s",
                    pr_data.action,
                    pr_data.pr_number,
                )
                self._send_json_response(
                    200,
                    {
                        "status": "ignored",
                        "action": pr_data.action,
                        "pr_number": pr_data.pr_number,
                    },
                )
                return

            job, is_new = self.server.queue.enqueue_pr_event(
                pr_data=pr_data,
                delivery_id=event.delivery_id,
            )

            if is_new:
                logger.info(
                    "Queued new review job %s for PR #%s (%s)",
                    job.job_id,
                    pr_data.pr_number,
                    pr_data.repo_full_name,
                )
                self._send_json_response(
                    202,
                    {
                        "status": "queued",
                        "job_id": job.job_id,
                        "delivery_id": event.delivery_id,
                        "repo": pr_data.repo_full_name,
                        "pr_number": pr_data.pr_number,
                    },
                )
            else:
                logger.info("Deduplicated repeated delivery for job %s", job.job_id)
                self._send_json_response(
                    200,
                    {
                        "status": "duplicate_skipped",
                        "job_id": job.job_id,
                        "delivery_id": event.delivery_id,
                    },
                )
            return

        # Other events acknowledged
        self._send_json_response(
            200,
            {
                "status": "acknowledged",
                "event_type": event.event_type,
                "delivery_id": event.delivery_id,
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        """Route HTTP server logs through Python logging system."""
        logger.debug(
            "%s - - [%s] %s",
            self.address_string(),
            self.log_date_time_string(),
            format % args,
        )


class WebhookHTTPServer(ThreadingHTTPServer):
    """Custom ThreadingHTTPServer holding queue, rate limiter, and authentication context."""

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        queue: ReviewJobQueue,
        webhook_secret: str,
        app_auth: GitHubAppAuth | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        api_secret: str | None = None,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.queue = queue
        self.webhook_secret = webhook_secret
        self.app_auth = app_auth
        self.rate_limiter = rate_limiter
        self.api_secret = api_secret


class GitHubWebhookServer:
    """Manages lifecycle of the production GitHub App webhook HTTP server and background worker."""

    def __init__(
        self,
        queue: ReviewJobQueue,
        webhook_secret: str,
        host: str = "0.0.0.0",
        port: int = 8000,
        worker: ReviewWorker | None = None,
        app_auth: GitHubAppAuth | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.queue = queue
        self.webhook_secret = webhook_secret
        self.host = host
        self.port = port
        self.worker = worker
        self.app_auth = app_auth
        self.rate_limiter = rate_limiter
        self.api_secret = api_secret

        if not self.webhook_secret or not self.webhook_secret.strip():
            raise ValueError(
                "webhook_secret must be a non-empty string for production webhook server"
            )

        self._server = WebhookHTTPServer(
            server_address=(self.host, self.port),
            RequestHandlerClass=WebhookRequestHandler,
            queue=self.queue,
            webhook_secret=self.webhook_secret,
            app_auth=self.app_auth,
            rate_limiter=self.rate_limiter,
            api_secret=self.api_secret,
        )

        self.server_port = self._server.server_port
        self._server_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, background: bool = True) -> None:
        """Start the webhook server and optional background worker thread."""
        logger.info(
            "Starting OpenReview GitHub webhook server on %s:%d",
            self.host,
            self.server_port,
        )
        self._stop_event.clear()

        if background:
            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="GitHubWebhookServerThread",
                daemon=True,
            )
            self._server_thread.start()

            if self.worker is not None:
                self._worker_thread = threading.Thread(
                    target=self.worker.run_worker_loop,
                    kwargs={"poll_interval": 0.5, "stop_event": self._stop_event},
                    name="GitHubReviewWorkerThread",
                    daemon=True,
                )
                self._worker_thread.start()
        else:
            self._server.serve_forever()

    def stop(self) -> None:
        """Shut down the webhook server and stop background worker threads."""
        logger.info("Stopping OpenReview GitHub webhook server")
        self._stop_event.set()
        self._server.shutdown()
        self._server.server_close()

        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)

    def __enter__(self) -> Self:
        self.start(background=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.stop()
