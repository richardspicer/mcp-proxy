"""CLI entry point for mcp-proxy."""

from __future__ import annotations

from pathlib import Path

import click

from mcp_proxy.models import Transport


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
    # Validate transport/target combinations
    if transport == "stdio" and not target_command:
        raise click.UsageError("--target-command is required for stdio transport.")
    if transport in ("sse", "streamable-http") and not target_url:
        raise click.UsageError("--target-url is required for SSE/HTTP transport.")

    from mcp_proxy.tui.app import ProxyApp

    transport_enum = Transport(transport.replace("-", "_"))
    app = ProxyApp(
        transport=transport_enum,
        server_command=target_command,
        server_url=target_url,
        intercept=intercept,
        session_file=Path(session_file) if session_file else None,
    )
    app.run()


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
    from mcp_proxy.session_store import SessionStore

    try:
        store = SessionStore.load(Path(session_file))
    except Exception as exc:
        raise click.ClickException(f"Failed to load session: {exc}") from exc

    output_path = Path(output)
    try:
        store.save(output_path)
    except Exception as exc:
        raise click.ClickException(f"Failed to write export: {exc}") from exc

    messages = store.get_messages()
    click.echo(f"Exported {len(messages)} messages to {output_path}")


@main.command()
@click.option("--session-file", type=click.Path(exists=True), required=True)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full JSON payloads.")
def inspect(session_file: str, verbose: bool) -> None:
    """Print session contents to stdout (non-interactive)."""
    import json

    from mcp_proxy.session_store import SessionStore

    try:
        store = SessionStore.load(Path(session_file))
    except Exception as exc:
        raise click.ClickException(f"Failed to load session: {exc}") from exc

    session = store.to_proxy_session()
    messages = store.get_messages()

    # Session header
    click.echo(f"Session: {session.id}")
    click.echo(f"Transport: {session.transport.value}")
    if session.server_command:
        click.echo(f"Server command: {session.server_command}")
    if session.server_url:
        click.echo(f"Server URL: {session.server_url}")
    click.echo(f"Started: {session.started_at.isoformat()}")
    click.echo(f"Messages: {len(messages)}")
    if session.metadata:
        click.echo(f"Metadata: {json.dumps(session.metadata)}")
    click.echo("---")

    # Message list
    for msg in messages:
        direction = "→" if msg.direction.value == "client_to_server" else "←"
        method_str = msg.method or "(response)"
        id_str = f" id={msg.jsonrpc_id}" if msg.jsonrpc_id is not None else ""
        modified_str = " [MODIFIED]" if msg.modified else ""
        corr_str = f" corr={msg.correlated_id[:8]}..." if msg.correlated_id else ""

        click.echo(
            f"  #{msg.sequence:03d} {direction} {method_str}{id_str}"
            f"{corr_str}{modified_str}"
        )

        if verbose:
            payload = msg.raw.model_dump(by_alias=True, exclude_none=True)
            click.echo(f"       {json.dumps(payload, indent=2)}")
            if msg.original_raw is not None:
                original = msg.original_raw.model_dump(by_alias=True, exclude_none=True)
                click.echo(f"       [original] {json.dumps(original, indent=2)}")
