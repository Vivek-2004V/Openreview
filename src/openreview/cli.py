"""CLI entry point for OpenReview AI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from openreview import (
    EXIT_FINDINGS_PRESENT,
    EXIT_INVALID_INPUT_OR_CONFIG,
    EXIT_SUCCESS,
    EXIT_TOOL_FAILURE,
    __version__,
)
from openreview.exit_codes import format_exit_codes_summary

EPILOG_HELP = f"""{format_exit_codes_summary()}

Examples:
  openreview --help
  openreview --version
  openreview doctor
  openreview review --base main
  openreview review --agent
  openreview findings --severity critical
"""


class OpenReviewArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser to enforce documented exit codes on argument errors."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"error: {message}\n")
        sys.exit(EXIT_INVALID_INPUT_OR_CONFIG)


def create_parser() -> OpenReviewArgumentParser:
    """Build and configure the CLI argument parser."""
    parser = OpenReviewArgumentParser(
        prog="openreview",
        description="OpenReview AI - Free-first, local-first code review assistant.",
        epilog=EPILOG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        title="Commands",
        description="Valid OpenReview commands",
        help="Subcommand to execute",
    )

    # Review command
    review_parser = subparsers.add_parser(
        "review",
        help="Run code review against working tree or git revisions (default).",
        epilog=EPILOG_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    review_parser.add_argument(
        "-p",
        "--path",
        default=".",
        help="Path to target git repository (default: current directory).",
    )
    review_parser.add_argument(
        "--base",
        default=None,
        help="Base git revision (e.g. main, HEAD~1). Default: HEAD for uncommitted diffs.",
    )
    review_parser.add_argument(
        "--head",
        default=None,
        help="Head git revision. Default: current working tree.",
    )
    review_parser.add_argument(
        "--all-files",
        action="store_true",
        default=False,
        help="Review all files in workspace instead of only changed files.",
    )
    review_parser.add_argument(
        "--scanners",
        default="ruff,eslint",
        help="Comma-separated list of scanners to enable (default: ruff,eslint).",
    )
    review_parser.add_argument(
        "--ai",
        action="store_true",
        default=False,
        help="Enable optional local AI review via Ollama.",
    )
    review_parser.add_argument(
        "--model",
        default="qwen2.5-coder:7b",
        help="Local AI model name for Ollama (default: qwen2.5-coder:7b).",
    )
    review_parser.add_argument(
        "-f",
        "--format",
        choices=["markdown", "json", "both"],
        default="markdown",
        help="Report output format (default: markdown).",
    )
    review_parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="File path to write review report (default: stdout).",
    )
    review_parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to project configuration file.",
    )
    review_parser.add_argument(
        "--agent",
        action="store_true",
        default=False,
        help="Agent-native mode: outputs structured JSON with findings and proposed fixes for AI agents (Antigravity, Codex, etc.).",
    )
    review_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose diagnostic logging.",
    )

    # Findings command
    findings_parser = subparsers.add_parser(
        "findings",
        help="List and filter review findings directly.",
    )
    findings_parser.add_argument(
        "-p",
        "--path",
        default=".",
        help="Path to target git repository (default: current directory).",
    )
    findings_parser.add_argument(
        "--base",
        default=None,
        help="Base git revision (default: HEAD).",
    )
    findings_parser.add_argument(
        "--severity",
        choices=["critical", "high", "medium", "low", "info"],
        default=None,
        help="Filter findings by minimum severity.",
    )
    findings_parser.add_argument(
        "--category",
        choices=["all", "new", "pre-existing", "ai"],
        default="new",
        help="Filter findings by category: new (default), pre-existing, ai, or all.",
    )
    findings_parser.add_argument(
        "-f",
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (table or json).",
    )

    # Doctor command
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect environment and tool availability (Git, linters, Ollama).",
    )
    doctor_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show detailed environment diagnosis.",
    )

    # Server command (GitHub App webhook server)
    server_parser = subparsers.add_parser(
        "server",
        help="Run production GitHub App webhook server and background review worker.",
    )
    server_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host address to bind webhook HTTP server (default: 0.0.0.0).",
    )
    server_parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=8000,
        help="Port to bind webhook HTTP server (default: 8000).",
    )
    server_parser.add_argument(
        "--secret",
        default=None,
        help="GitHub webhook HMAC secret (defaults to GITHUB_WEBHOOK_SECRET env var).",
    )
    server_parser.add_argument(
        "--db",
        default="openreview_jobs.db",
        help="Path to persistent SQLite database for review job queue (default: openreview_jobs.db).",
    )
    server_parser.add_argument(
        "--app-id",
        default=None,
        help="GitHub App ID (defaults to GITHUB_APP_ID env var).",
    )
    server_parser.add_argument(
        "--private-key",
        default=None,
        help="Path to GitHub App RSA private key .pem file (defaults to GITHUB_PRIVATE_KEY_PATH env var).",
    )
    server_parser.add_argument(
        "--worker",
        dest="worker",
        action="store_true",
        default=True,
        help="Run embedded background review worker (default: True).",
    )
    server_parser.add_argument(
        "--no-worker",
        dest="worker",
        action="store_false",
        help="Disable embedded background review worker (run webhook ingestion only).",
    )
    server_parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to OpenReview configuration file.",
    )
    server_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose diagnostic logging.",
    )

    return parser


def run_doctor(verbose: bool = False) -> int:
    """Inspect and report availability of git, scanners, and local AI."""
    import shutil

    tools = [
        ("Git", "git"),
        ("Ruff (Python)", "ruff"),
        ("ESLint (JS/TS)", "eslint"),
        ("REA (Binary Inspector)", "rea"),
        ("Bandit (Python AST)", "bandit"),
        ("Semgrep (SAST)", "semgrep"),
        ("Ollama (Local AI)", "ollama"),
    ]

    print("OpenReview AI Environment Doctor:")
    print("---------------------------------")
    all_ok = True
    for name, binary in tools:
        found_path = shutil.which(binary)
        if not found_path:
            venv_candidate = Path(sys.prefix) / "bin" / binary
            if venv_candidate.is_file():
                found_path = str(venv_candidate)
        if not found_path and binary == "rea":
            # Check local sibling rea/scripts/rea.mjs
            local_rea = (
                Path(__file__).resolve().parent.parent.parent
                / "rea"
                / "scripts"
                / "rea.mjs"
            )
            if local_rea.is_file() and shutil.which("node"):
                found_path = f"node {local_rea}"
        status = f"OK ({found_path})" if found_path else "NOT FOUND (optional/adapter)"
        print(f"  - {name:<22}: {status}")
        if name == "Git" and not found_path:
            all_ok = False

    return EXIT_SUCCESS if all_ok else EXIT_TOOL_FAILURE


def run_server(args: argparse.Namespace) -> int:
    """Run production GitHub App webhook server and optional review worker."""
    import os

    from openreview.config import ConfigError, OpenReviewConfig, load_config
    from openreview.integrations import (
        GitHubAppAuth,
        GitHubWebhookServer,
        ReviewJobQueue,
        ReviewWorker,
    )

    secret = getattr(args, "secret", None) or os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        sys.stderr.write(
            "Error: Webhook secret must be provided via --secret or GITHUB_WEBHOOK_SECRET environment variable.\n"
        )
        return EXIT_INVALID_INPUT_OR_CONFIG

    config_path = getattr(args, "config", None)
    try:
        cfg = load_config(path=config_path) if config_path else OpenReviewConfig()
    except ConfigError as exc:
        sys.stderr.write(f"Configuration error: {exc}\n")
        return EXIT_INVALID_INPUT_OR_CONFIG

    db_path = getattr(args, "db", None) or os.environ.get(
        "OPENREVIEW_JOBS_DB", "openreview_jobs.db"
    )
    queue = ReviewJobQueue(db_path=db_path)

    app_id = getattr(args, "app_id", None) or os.environ.get("GITHUB_APP_ID")
    private_key_path = getattr(args, "private_key", None) or os.environ.get(
        "GITHUB_PRIVATE_KEY_PATH"
    )

    app_auth = None
    if app_id and private_key_path:
        try:
            app_auth = GitHubAppAuth(app_id=app_id, private_key=private_key_path)
            print(f"GitHub App authentication initialized (App ID: {app_id})")
        except (ValueError, OSError) as exc:
            sys.stderr.write(
                f"Warning: Failed to initialize GitHub App authentication: {exc}\n"
            )

    worker = None
    if getattr(args, "worker", True):
        worker = ReviewWorker(
            queue=queue,
            config=cfg,
            app_auth=app_auth,
        )

    server = GitHubWebhookServer(
        queue=queue,
        webhook_secret=secret,
        host=getattr(args, "host", "0.0.0.0"),
        port=getattr(args, "port", 8000),
        worker=worker,
        app_auth=app_auth,
    )

    print(
        f"OpenReview AI GitHub App Server v{__version__} listening on {server.host}:{server.server_port}"
    )
    print(f"Job queue storage: {db_path} (pending jobs: {queue.queue_size})")

    try:
        server.start(background=False)
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.stop()

    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point."""
    if argv is None:
        argv = sys.argv[1:]

    parser = create_parser()

    # If no arguments provided, show help
    if not argv:
        parser.print_help()
        return EXIT_SUCCESS

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code

    if args.subcommand == "doctor":
        return run_doctor(verbose=args.verbose)

    if args.subcommand == "server":
        return run_server(args)

    if args.subcommand in ("review", "findings") or args.subcommand is None:
        target_path = getattr(args, "path", ".")
        config_path = getattr(args, "config", None)
        try:
            from openreview.config import ConfigError, load_config

            cfg = load_config(path=config_path, repo_root=target_path)
        except ConfigError as exc:
            sys.stderr.write(f"Configuration error: {exc}\n")
            return EXIT_INVALID_INPUT_OR_CONFIG

        try:
            from openreview.repo import (
                GitError,
                NotAGitRepositoryError,
                inspect_git_status,
            )

            repo_info = inspect_git_status(target_path)
        except NotAGitRepositoryError as exc:
            sys.stderr.write(f"Repository error: {exc}\n")
            return EXIT_INVALID_INPUT_OR_CONFIG
        except GitError as exc:
            sys.stderr.write(f"Git operation error: {exc}\n")
            return EXIT_TOOL_FAILURE

        if getattr(args, "verbose", False):
            print(f"Loaded configuration: {cfg}")
            print(f"Git Repository Root: {repo_info.root}")
            print(f"Git Branch/Ref: {repo_info.head_ref}")
            print(f"Uncommitted Changes: {len(repo_info.uncommitted_files)} files")

        is_agent_mode = getattr(args, "agent", False)
        is_findings_cmd = getattr(args, "subcommand", None) == "findings"

        if not is_agent_mode and not is_findings_cmd:
            print(f"OpenReview AI v{__version__} - Initialized.")
            print(f"Target repository: {repo_info.root} (branch: {repo_info.head_ref})")

        from openreview.config import AIConfig, OpenReviewConfig, ScannersConfig
        from openreview.repo.diff_models import DiffRequest, DiffTarget
        from openreview.reports import render_json_report, render_markdown_report
        from openreview.review.engine import ReviewEngine

        # Apply CLI overrides to config
        cli_scanners_arg = getattr(args, "scanners", None)
        scanners_list = (
            [s.strip() for s in cli_scanners_arg.split(",") if s.strip()]
            if cli_scanners_arg
            else cfg.scanners.enabled
        )
        ai_enabled = getattr(args, "ai", False) or cfg.ai.enabled
        ai_model = getattr(args, "model", cfg.ai.model)

        effective_cfg = OpenReviewConfig(
            review=cfg.review,
            scanners=ScannersConfig(
                enabled=scanners_list,
                timeout_seconds=cfg.scanners.timeout_seconds,
            ),
            ai=AIConfig(
                enabled=ai_enabled,
                provider=cfg.ai.provider,
                model=ai_model,
                endpoint=cfg.ai.endpoint,
                max_context_tokens=cfg.ai.max_context_tokens,
            ),
            privacy=cfg.privacy,
            reports=cfg.reports,
        )

        engine = ReviewEngine(repo_root=repo_info.root, config=effective_cfg)
        diff_req = DiffRequest(
            repo_root=repo_info.root,
            target=DiffTarget.WORKING_TREE,
            base_ref=getattr(args, "base", cfg.review.base_ref),
            head_ref=getattr(args, "head", None),
            max_files=cfg.review.max_files,
            max_file_bytes=cfg.review.max_file_bytes,
        )

        try:
            report = engine.execute_review(diff_request=diff_req)
        except (RuntimeError, ValueError, OSError) as exc:
            sys.stderr.write(f"Review execution error: {exc}\n")
            return EXIT_TOOL_FAILURE

        # Handle findings subcommand
        if is_findings_cmd:
            sev_filter = getattr(args, "severity", None)
            cat_filter = getattr(args, "category", "new")

            if cat_filter == "pre-existing":
                findings_to_show = list(report.pre_existing_findings)
            elif cat_filter == "ai":
                findings_to_show = list(report.ai_suggestions)
            elif cat_filter == "all":
                findings_to_show = list(report.findings) + list(
                    report.pre_existing_findings
                )
            else:  # "new"
                findings_to_show = list(report.new_findings)

            if sev_filter:
                sev_levels = {
                    "critical": 4,
                    "high": 3,
                    "medium": 2,
                    "low": 1,
                    "info": 0,
                }
                min_threshold = sev_levels.get(sev_filter.lower(), 0)
                findings_to_show = [
                    f
                    for f in findings_to_show
                    if sev_levels.get(f.severity.value.lower(), 0) >= min_threshold
                ]

            if getattr(args, "format", "table") == "json":
                import json

                print(json.dumps([f.to_dict() for f in findings_to_show], indent=2))
            else:
                if not findings_to_show:
                    print("No matching findings found.")
                else:
                    print(
                        f"{'SEVERITY':<10} {'RULE ID':<22} {'LOCATION':<30} {'TITLE'}"
                    )
                    print("-" * 80)
                    for f in findings_to_show:
                        loc = f"{f.path}:{f.start_line}" if f.start_line else f.path
                        rule = f.rule_id or "generic"
                        print(
                            f"{f.severity.value.upper():<10} {rule:<22} {loc:<30} {f.title}"
                        )

            return EXIT_FINDINGS_PRESENT if findings_to_show else EXIT_SUCCESS

        # Agent-native mode: output pure JSON without decorative logs
        if is_agent_mode:
            print(render_json_report(report))
            return EXIT_FINDINGS_PRESENT if report.findings else EXIT_SUCCESS

        # Render report
        fmt = getattr(args, "format", "markdown")
        out_path = getattr(args, "output", None)

        rendered_sections = []
        if fmt in ("markdown", "both"):
            rendered_sections.append(render_markdown_report(report))
        if fmt in ("json", "both"):
            rendered_sections.append(render_json_report(report))

        final_text = "\n\n".join(rendered_sections)
        if out_path:
            try:
                Path(out_path).write_text(final_text, encoding="utf-8")
                print(f"Report successfully saved to {out_path}")
            except OSError as exc:
                sys.stderr.write(f"Failed to write report to {out_path}: {exc}\n")
                return EXIT_TOOL_FAILURE
        else:
            print(final_text)

        if report.findings:
            return EXIT_FINDINGS_PRESENT
        return EXIT_SUCCESS

    parser.print_help()
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
