"""CLI commands — ``dashboard``, ``guide``, ``serve``."""

from __future__ import annotations

from pathlib import Path

import click

from releasepilot import __version__
from releasepilot.cli.app import cli
from releasepilot.cli.helpers import (
    _build_settings,
    _common_options,
    console,
)

# ── dashboard ──────────────────────────────────────────────────────────────


@cli.command()
@_common_options
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for the HTML dashboard (default: release-dashboard.html).",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    default=False,
    help="Open the dashboard in the default browser after generation.",
)
def dashboard(output: Path | None, open_browser: bool, **kwargs):
    """Generate an interactive HTML dashboard for the release.

    Runs the full pipeline, renders the results into a self-contained HTML
    file with embedded CSS/JS, and optionally opens it in the browser.
    """
    import webbrowser

    from releasepilot.dashboard.reporter import HtmlReporter
    from releasepilot.dashboard.use_case import DashboardUseCase

    # Remove dry_run — dashboard always generates output
    kwargs.pop("dry_run", None)
    settings = _build_settings(**kwargs)
    console.print("[dim]Running pipeline…[/dim]")

    use_case = DashboardUseCase()
    data = use_case.execute(settings)

    reporter = HtmlReporter()
    html = reporter.render(data)

    if output is None:
        output = Path.cwd() / "release-dashboard.html"
    output.write_text(html, encoding="utf-8")
    console.print(f"[green]Dashboard generated:[/green] {output}")

    if open_browser:
        try:
            webbrowser.open(f"file://{output.resolve()}")
        except Exception:
            console.print(
                f"[dim]Could not open browser. Open manually: file://{output.resolve()}[/dim]"
            )


# ── guide ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("repo_path", default=".", required=False)
@click.option(
    "--reset-preferences",
    is_flag=True,
    default=False,
    help="Clear saved guided-workflow preferences and exit.",
)
def guide(repo_path: str, reset_preferences: bool):
    """Interactive guided workflow for generating release notes.

    Designed for QA, testers, and non-developer users who may not know
    exact git refs or tags. Walks through repository inspection, branch
    selection, time range, and audience step by step.

    REPO_PATH is the path to a local git repository or a remote URL
    (default: current directory). Remote URLs are cloned automatically.
    """
    if reset_preferences:
        from releasepilot.cli.preferences import reset_preferences as _reset

        _reset()
        click.echo("Preferences cleared.")
        return

    from releasepilot.cli.guide import run_guide

    run_guide(repo_path)


# ── serve ──────────────────────────────────────────────────────────────────


@cli.command()
@click.option("--repo", "-r", default=".", help="Repository path")
@click.option("--from", "from_ref", default="", help="Start ref (tag, commit, branch)")
@click.option("--to", "to_ref", default="HEAD", help="End ref (default: HEAD)")
@click.option("--since", "since_date", default="", help="Collect changes since date (YYYY-MM-DD)")
@click.option("--branch", "-b", default="", help="Git branch to analyse")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", "-p", default=8082, type=int, help="Port number")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
def serve(
    repo: str,
    from_ref: str,
    to_ref: str,
    since_date: str,
    branch: str,
    host: str,
    port: int,
    verbose: bool,
):
    """Start the interactive web dashboard server."""
    import uvicorn

    from releasepilot.config.file_config import load_config
    from releasepilot.shared.logging import configure_root_logger
    from releasepilot.web.server import create_app

    configure_root_logger(verbose)

    # Load project config file as base defaults (.releasepilot.json, etc.)
    repo_resolved = str(Path(repo).resolve())
    file_cfg = load_config(repo_resolved)
    config: dict[str, str] = {"repo_path": repo_resolved}

    # File config provides defaults for fields not specified via CLI
    if file_cfg.app_name:
        config["app_name"] = file_cfg.app_name
    if file_cfg.audience:
        config["audience"] = file_cfg.audience
    if file_cfg.format:
        config["format"] = file_cfg.format
    if file_cfg.language:
        config["language"] = file_cfg.language
    if file_cfg.title:
        config["title"] = file_cfg.title
    if file_cfg.version:
        config["version"] = file_cfg.version
    if file_cfg.branch:
        config["branch"] = file_cfg.branch
    config["show_authors"] = str(file_cfg.show_authors).lower()
    config["show_hashes"] = str(file_cfg.show_hashes).lower()
    if file_cfg.accent_color:
        config["accent_color"] = file_cfg.accent_color
    if file_cfg.output_dir:
        config["output_dir"] = file_cfg.output_dir
    if file_cfg.overwrite:
        config["overwrite"] = "true"
    if file_cfg.repos:
        config["repos"] = ",".join(file_cfg.repos)
    if file_cfg.export_formats:
        config["export_formats"] = ",".join(file_cfg.export_formats)
    if not file_cfg.gitlab_ssl_verify:
        config["gitlab_ssl_verify"] = "false"
    if not file_cfg.github_ssl_verify:
        config["github_ssl_verify"] = "false"

    # CLI args override file config
    if from_ref:
        config["from_ref"] = from_ref
    if to_ref and to_ref != "HEAD":
        config["to_ref"] = to_ref
    if since_date:
        config["since_date"] = since_date
    if branch:
        config["branch"] = branch
    web_app = create_app(config)

    console.print(f"\n[bold]ReleasePilot[/bold] v{__version__} — Web Dashboard")
    console.print(f"Repository: [cyan]{repo}[/cyan]")
    if since_date:
        console.print(f"Since: [cyan]{since_date}[/cyan]")
    elif from_ref:
        console.print(f"Range: [cyan]{from_ref}..{to_ref}[/cyan]")
    console.print(f"Server: [link=http://{host}:{port}]http://{host}:{port}[/link]\n")

    uvicorn.run(web_app, host=host, port=port, log_level="info" if verbose else "warning")
