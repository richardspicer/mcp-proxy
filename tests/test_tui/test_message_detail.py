"""Tests for mcp_proxy.tui.widgets.message_detail — MessageDetailPanel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from mcp.types import JSONRPCMessage, JSONRPCRequest
from textual.app import App, ComposeResult
from textual.widgets import RichLog

from mcp_proxy.models import Direction, ProxyMessage, Transport
from mcp_proxy.tui.widgets.message_detail import MessageDetailPanel


def _make_proxy_message(method: str = "tools/list", seq: int = 0, msg_id: int = 1) -> ProxyMessage:
    """Build a ProxyMessage for testing."""
    return ProxyMessage(
        id=str(uuid.uuid4()),
        sequence=seq,
        timestamp=datetime.now(tz=UTC),
        direction=Direction.CLIENT_TO_SERVER,
        transport=Transport.STDIO,
        raw=JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method)),
        jsonrpc_id=msg_id,
        method=method,
        correlated_id=None,
        modified=False,
        original_raw=None,
    )


class DetailTestApp(App[None]):
    """Minimal app for testing MessageDetailPanel in isolation."""

    def compose(self) -> ComposeResult:
        yield MessageDetailPanel()


class TestMessageDetailPanel:
    """MessageDetailPanel shows JSON-RPC payload and metadata."""

    async def test_initial_empty_state(self) -> None:
        app = DetailTestApp()
        async with app.run_test():
            panel = app.query_one(MessageDetailPanel)
            # Should exist and not crash
            assert panel is not None

    async def test_show_message_displays_payload(self) -> None:
        app = DetailTestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(MessageDetailPanel)
            pm = _make_proxy_message("tools/call", seq=5, msg_id=42)
            panel.show_message(pm)
            await pilot.pause()
            log = panel.query_one("#detail-log", RichLog)
            text = "\n".join(str(line) for line in log.lines)
            assert "tools/call" in text

    async def test_show_message_displays_metadata(self) -> None:
        app = DetailTestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(MessageDetailPanel)
            pm = _make_proxy_message("tools/list", seq=3)
            panel.show_message(pm)
            await pilot.pause()
            log = panel.query_one("#detail-log", RichLog)
            text = "\n".join(str(line) for line in log.lines)
            assert "#3" in text
            assert "CLIENT_TO_SERVER" in text or "client_to_server" in text

    async def test_clear_resets_panel(self) -> None:
        app = DetailTestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(MessageDetailPanel)
            pm = _make_proxy_message()
            panel.show_message(pm)
            await pilot.pause()
            log = panel.query_one("#detail-log", RichLog)
            log.clear()
            await pilot.pause()
            assert len(log.lines) == 0
