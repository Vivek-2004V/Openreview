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

logger = logging.getLogger(__name__)


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for GitHub webhook deliveries and health checks."""

    server: WebhookHTTPServer  # type annotation for custom server

    def _send_json_response(self, status_code: int, data: dict[str, Any]) -> None:
        """Send formatted JSON HTTP response."""
        resp_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def do_GET(self) -> None:
        """Handle health check and status probes."""
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
            if job:
                self._send_json_response(200, job.to_dict())
            else:
                self._send_json_response(404, {"error": f"Job not found: {job_id}"})
            return

        self._send_json_response(404, {"error": "Not found"})

    def do_POST(self) -> None:
        """Handle incoming GitHub webhook POST deliveries."""
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
            logger.error("Unexpected error parsing webhook request: %s", exc)
            self._send_json_response(500, {"error": f"Internal server error: {exc}"})
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
    """Custom ThreadingHTTPServer holding queue and authentication context."""

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        queue: ReviewJobQueue,
        webhook_secret: str,
        app_auth: GitHubAppAuth | None = None,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.queue = queue
        self.webhook_secret = webhook_secret
        self.app_auth = app_auth


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
    ) -> None:
        self.queue = queue
        self.webhook_secret = webhook_secret
        self.host = host
        self.port = port
        self.worker = worker
        self.app_auth = app_auth

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
