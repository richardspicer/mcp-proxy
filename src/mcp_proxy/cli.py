"""CLI entry point for mcp-proxy."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from mcp_proxy.models import Transport

if TYPE_CHECKING:
    from mcp_proxy.replay import ReplayResult, ReplaySessionResult


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
@click.option("--target-command", type=str, help="Server command for replay (stdio).")
@click.option("--target-url", type=str, help="Server URL for replay (SSE/HTTP, future).")
@click.option("--output", type=click.Path(), help="Save replay results to JSON.")
@click.option("--timeout", type=float, default=10.0, help="Per-message response timeout (seconds).")
@click.option(
    "--no-handshake",
    is_flag=True,
    default=False,
    help="Skip auto-handshake (if session already includes initialize).",
)
def replay(
    session_file: str,
    target_command: str | None,
    target_url: str | None,
    output: str | None,
    timeout: float,
    no_handshake: bool,
) -> None:
    """Replay a saved session against a live server."""
    import asyncio
    import shlex

    from mcp_proxy.models import Direction
    from mcp_proxy.session_store import SessionStore

    if not target_command and not target_url:
        raise click.UsageError("Either --target-command or --target-url is required.")

    if target_url:
        raise click.UsageError("SSE/HTTP replay is not yet implemented. Use --target-command.")

    # Load session
    try:
        store = SessionStore.load(Path(session_file))
    except Exception as exc:
        raise click.ClickException(f"Failed to load session: {exc}") from exc

    messages = store.get_messages()
    c2s_messages = [m for m in messages if m.direction == Direction.CLIENT_TO_SERVER]

    if not c2s_messages:
        click.echo("No client-to-server messages to replay.")
        return

    # Parse target command
    assert target_command is not None
    parts = shlex.split(target_command)
    command = parts[0]
    args = parts[1:] if len(parts) > 1 else []

    click.echo(f'Replaying {len(c2s_messages)} messages against "{target_command}"...')

    # Run replay
    session_result = asyncio.run(_run_replay(command, args, messages, timeout, not no_handshake))

    # Print summary
    succeeded = 0
    failed = 0
    for i, r in enumerate(session_result.results):
        method = r.original_request.method or "(response)"
        msg_id = r.original_request.jsonrpc_id

        if r.original_request.jsonrpc_id is not None:
            id_str = f" (id={msg_id})"
        else:
            id_str = " (notification)"

        if r.error:
            click.echo(f"  #{i:03d} → {method}{id_str} ✗ {r.error}")
            failed += 1
        elif r.response is not None:
            click.echo(f"  #{i:03d} → {method}{id_str} ✓ {r.duration_ms:.0f}ms")
            succeeded += 1
        else:
            # Notification (no response expected)
            click.echo(f"  #{i:03d} → {method}{id_str} ✓")
            succeeded += 1

    click.echo("")
    total = succeeded + failed
    parts_summary = [f"{succeeded}/{total} succeeded"]
    if failed:
        parts_summary.append(f"{failed} failed")
    click.echo(f"Results: {', '.join(parts_summary)}")

    # Save JSON output if requested
    if output:
        output_path = Path(output)
        _save_replay_results(session_result, output_path, target_command)
        click.echo(f"Results saved to {output_path}")


def _save_replay_results(
    session_result: ReplaySessionResult,
    output_path: Path,
    target_command: str | None,
) -> None:
    """Serialize replay results to JSON.

    Args:
        session_result: The replay session results.
        output_path: Path to write JSON output.
        target_command: The server command used for replay.
    """
    import json
    from typing import Any

    def _serialize_result(r: ReplayResult) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "original_request": r.original_request.raw.model_dump(by_alias=True, exclude_none=True),
            "sent_message": r.sent_message.message.model_dump(by_alias=True, exclude_none=True),
            "response": (
                r.response.message.model_dump(by_alias=True, exclude_none=True)
                if r.response
                else None
            ),
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        return entry

    data = {
        "target_command": target_command,
        "target_url": session_result.target_url,
        "results": [_serialize_result(r) for r in session_result.results],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _run_replay(
    command: str,
    args: list[str],
    messages: list,
    timeout: float,
    auto_handshake: bool,
) -> ReplaySessionResult:
    """Run the replay against a stdio server adapter.

    Args:
        command: Server executable.
        args: Server command arguments.
        messages: All ProxyMessages from the session.
        timeout: Per-message response timeout.
        auto_handshake: Whether to send synthetic handshake.

    Returns:
        ReplaySessionResult with all replay results.
    """
    from mcp_proxy.adapters.stdio import StdioServerAdapter
    from mcp_proxy.replay import ReplaySessionResult, replay_messages

    async with StdioServerAdapter(command=command, args=args) as adapter:
        results = await replay_messages(
            messages, adapter, timeout=timeout, auto_handshake=auto_handshake
        )
    return ReplaySessionResult(
        results=results,
        target_command=f"{command} {' '.join(args)}".strip(),
    )


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
            f"  #{msg.sequence:03d} {direction} {method_str}{id_str}{corr_str}{modified_str}"
        )

        if verbose:
            payload = msg.raw.model_dump(by_alias=True, exclude_none=True)
            click.echo(f"       {json.dumps(payload, indent=2)}")
            if msg.original_raw is not None:
                original = msg.original_raw.model_dump(by_alias=True, exclude_none=True)
                click.echo(f"       [original] {json.dumps(original, indent=2)}")
