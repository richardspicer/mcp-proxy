"""Main Textual app for mcp-proxy TUI.

ProxyApp owns the event loop, composes the layout, runs the pipeline
as a background worker, and wires pipeline callbacks to widget updates.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header
from textual.worker import Worker

from mcp_proxy.adapters.base import TransportAdapter
from mcp_proxy.intercept import InterceptEngine
from mcp_proxy.models import HeldMessage, InterceptMode, ProxyMessage, Transport
from mcp_proxy.pipeline import PipelineSession, run_pipeline
from mcp_proxy.session_store import SessionStore
from mcp_proxy.tui.messages import (
    MessageForwarded,
    MessageHeld,
    MessageReceived,
    PipelineError,
    PipelineStopped,
)
from mcp_proxy.tui.widgets.message_detail import MessageDetailPanel
from mcp_proxy.tui.widgets.message_list import MessageListPanel, MessageSelected
from mcp_proxy.tui.widgets.status_bar import ProxyStatusBar

logger = logging.getLogger(__name__)


class ProxyApp(App[None]):
    """Textual application for interactive MCP traffic inspection.

    Args:
        transport: The MCP transport type (STDIO, SSE, STREAMABLE_HTTP).
        server_command: For stdio transport, the command to launch the server.
        server_url: For SSE/HTTP transport, the server endpoint URL.
        intercept: Whether to start in intercept mode.
        session_file: Optional path to auto-save the session on exit.
        run_pipeline_on_mount: If False, skip launching the pipeline worker
            on mount (used in tests to control lifecycle manually).
    """

    CSS_PATH = "app.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        transport: Transport = Transport.STDIO,
        server_command: str | None = None,
        server_url: str | None = None,
        intercept: bool = False,
        session_file: Path | None = None,
        run_pipeline_on_mount: bool = True,
    ) -> None:
        super().__init__()
        self.transport = transport
        self.server_command = server_command
        self.server_url = server_url
        self.intercept = intercept
        self.session_file = session_file
        self._run_pipeline_on_mount = run_pipeline_on_mount
        self._pipeline_worker: Worker[None] | None = None

        # Pipeline infrastructure
        initial_mode = InterceptMode.INTERCEPT if intercept else InterceptMode.PASSTHROUGH
        self.intercept_engine = InterceptEngine(mode=initial_mode)
        self.session_store = SessionStore(
            session_id=str(uuid.uuid4()),
            transport=transport,
            server_command=server_command,
            server_url=server_url,
        )

        # Set the title
        target = server_command or server_url or "unknown"
        self.title = f"mcp-proxy \u2014 {transport.value} \u2014 {target}"

    def compose(self) -> ComposeResult:
        """Compose the main layout.

        Returns:
            The composed widget tree.
        """
        yield Header()
        with Horizontal(id="main-container"):
            yield MessageListPanel()
            yield MessageDetailPanel()
        yield ProxyStatusBar()
        yield Footer()

    def on_mount(self) -> None:
        """Start the pipeline worker on mount if configured."""
        bar = self.query_one(ProxyStatusBar)
        bar.mode = self.intercept_engine.mode
        if self._run_pipeline_on_mount:
            self._launch_pipeline()

    def start_pipeline_worker(
        self,
        client_adapter: TransportAdapter,
        server_adapter: TransportAdapter,
    ) -> None:
        """Start the pipeline with pre-built adapters (for testing).

        Args:
            client_adapter: The client-facing adapter.
            server_adapter: The server-facing adapter.
        """
        self._pipeline_worker = self.run_worker(
            self._run_proxy_with_adapters(client_adapter, server_adapter),
            name="pipeline",
            exclusive=True,
        )

    def _launch_pipeline(self) -> None:
        """Build adapters and start the pipeline worker."""
        if self.transport == Transport.STDIO:
            self._pipeline_worker = self.run_worker(
                self._run_proxy_stdio(),
                name="pipeline",
                exclusive=True,
            )
        else:
            self.notify(
                f"Transport {self.transport.value} not yet implemented",
                severity="error",
            )

    async def _run_proxy_stdio(self) -> None:
        """Pipeline worker for stdio transport.

        Builds StdioServerAdapter and StdioClientAdapter, enters their
        contexts, and runs the pipeline until either side disconnects.
        """
        from mcp_proxy.adapters.stdio import StdioClientAdapter, StdioServerAdapter

        if not self.server_command:
            self.post_message(PipelineError(ValueError("No server command")))
            return

        # Parse command into executable + args (shlex handles quoting)
        parts = shlex.split(self.server_command)
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        try:
            async with (
                StdioServerAdapter(command=command, args=args) as server_adapter,
                StdioClientAdapter() as client_adapter,
            ):
                session = self._build_pipeline_session()
                await run_pipeline(client_adapter, server_adapter, session)
        except Exception as exc:
            logger.error("Pipeline error: %s", exc, exc_info=True)
            self.post_message(PipelineError(exc))
        finally:
            self.post_message(PipelineStopped())

    async def _run_proxy_with_adapters(
        self,
        client_adapter: TransportAdapter,
        server_adapter: TransportAdapter,
    ) -> None:
        """Pipeline worker with pre-built adapters (for testing).

        Args:
            client_adapter: The client-facing adapter.
            server_adapter: The server-facing adapter.
        """
        try:
            session = self._build_pipeline_session()
            await run_pipeline(client_adapter, server_adapter, session)
        except Exception as exc:
            logger.error("Pipeline error: %s", exc, exc_info=True)
            self.post_message(PipelineError(exc))
        finally:
            self.post_message(PipelineStopped())

    def _build_pipeline_session(self) -> PipelineSession:
        """Build a PipelineSession wired to TUI callbacks.

        Returns:
            A configured PipelineSession.
        """
        return PipelineSession(
            session_store=self.session_store,
            intercept_engine=self.intercept_engine,
            transport=self.transport,
            on_message=self._on_pipeline_message,
            on_held=self._on_pipeline_held,
            on_forwarded=self._on_pipeline_forwarded,
        )

    # ------------------------------------------------------------------
    # Pipeline callback wrappers (discard post_message return value)
    # ------------------------------------------------------------------

    def _on_pipeline_message(self, pm: ProxyMessage) -> None:
        self.post_message(MessageReceived(pm))

    def _on_pipeline_held(self, held: HeldMessage) -> None:
        self.post_message(MessageHeld(held))

    def _on_pipeline_forwarded(self, pm: ProxyMessage) -> None:
        self.post_message(MessageForwarded(pm))

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_message_received(self, event: MessageReceived) -> None:
        """Handle a new message from the pipeline.

        Args:
            event: The MessageReceived event containing the ProxyMessage.
        """
        panel = self.query_one(MessageListPanel)
        panel.add_message(event.proxy_message)
        bar = self.query_one(ProxyStatusBar)
        bar.message_count = len(panel.messages)

    def on_message_held(self, event: MessageHeld) -> None:
        """Handle a held message from the pipeline.

        Args:
            event: The MessageHeld event containing the HeldMessage.
        """
        panel = self.query_one(MessageListPanel)
        panel.mark_held(event.held_message.proxy_message.id)
        bar = self.query_one(ProxyStatusBar)
        bar.held_count = len(self.intercept_engine.get_held())

    def on_message_forwarded(self, event: MessageForwarded) -> None:
        """Handle a forwarded message notification.

        Args:
            event: The MessageForwarded event.
        """
        bar = self.query_one(ProxyStatusBar)
        bar.held_count = len(self.intercept_engine.get_held())

    def on_message_selected(self, event: MessageSelected) -> None:
        """Handle message selection — update detail panel.

        Args:
            event: The MessageSelected event from the list panel.
        """
        detail = self.query_one(MessageDetailPanel)
        detail.show_message(event.proxy_message)

    def on_pipeline_error(self, event: PipelineError) -> None:
        """Handle pipeline error — update status bar.

        Args:
            event: The PipelineError event.
        """
        bar = self.query_one(ProxyStatusBar)
        bar.connection_status = f"ERROR: {event.error}"

    def on_pipeline_stopped(self, event: PipelineStopped) -> None:
        """Handle pipeline stopped — update status bar.

        Args:
            event: The PipelineStopped event.
        """
        bar = self.query_one(ProxyStatusBar)
        bar.connection_status = "DISCONNECTED"
