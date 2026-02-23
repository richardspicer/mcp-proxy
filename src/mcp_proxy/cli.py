"""CLI entry point for mcp-proxy."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Interactive MCP traffic interceptor for security research."""


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"], case_sensitive=False),
    required=True,
    help="MCP transport type.",
)
@click.option("--target-command", type=str, help="Server command (stdio only).")
@click.option("--target-url", type=str, help="Server URL (SSE/HTTP only).")
@click.option("--intercept", is_flag=True, default=False, help="Start in intercept mode.")
@click.option("--listen-port", type=int, default=8888, help="Local port for SSE/HTTP.")
@click.option("--session-file", type=click.Path(), help="Auto-save session to this file.")
def proxy(
    transport: str,
    target_command: str | None,
    target_url: str | None,
    intercept: bool,
    listen_port: int,
    session_file: str | None,
) -> None:
    """Start the proxy with TUI."""
    click.echo(f"mcp-proxy: transport={transport}, intercept={intercept}")
    click.echo("Not yet implemented.")


@main.command()
@click.option("--session-file", type=click.Path(exists=True), required=True)
@click.option("--target-command", type=str, help="Server command for replay.")
@click.option("--target-url", type=str, help="Server URL for replay.")
def replay(session_file: str, target_command: str | None, target_url: str | None) -> None:
    """Replay a saved session against a live server."""
    click.echo(f"Replay: {session_file}")
    click.echo("Not yet implemented.")


@main.command(name="export")
@click.option("--session-file", type=click.Path(exists=True), required=True)
@click.option("--output", type=click.Path(), required=True, help="Output file path.")
@click.option(
    "--output-format",
    type=click.Choice(["json"], case_sensitive=False),
    default="json",
    help="Export format.",
)
def export_session(session_file: str, output: str, output_format: str) -> None:
    """Export a session to JSON."""
    click.echo(f"Export: {session_file} -> {output} ({output_format})")
    click.echo("Not yet implemented.")


@main.command()
@click.option("--session-file", type=click.Path(exists=True), required=True)
def inspect(session_file: str) -> None:
    """Print session contents to stdout (non-interactive)."""
    click.echo(f"Inspect: {session_file}")
    click.echo("Not yet implemented.")
