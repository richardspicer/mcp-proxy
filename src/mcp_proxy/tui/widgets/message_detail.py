"""Message detail panel for mcp-proxy TUI.

Displays the full JSON-RPC payload and proxy metadata for a selected
message. Uses RichLog for scrollable, formatted output.
"""

from __future__ import annotations

import json

from textual.widgets import RichLog

from mcp_proxy.models import ProxyMessage


class MessageDetailPanel(RichLog):
    """Right-side panel showing JSON-RPC payload and metadata.

    Displays formatted JSON of the selected message along with proxy
    metadata (sequence, direction, timestamp, correlation).

    Example:
        panel.show_message(proxy_message)
        panel.clear()
    """

    DEFAULT_CSS = """
    MessageDetailPanel {
        border: solid $primary;
        padding: 0 1;
    }
    """

    def show_message(self, proxy_message: ProxyMessage) -> None:
        """Display a ProxyMessage's payload and metadata.

        Args:
            proxy_message: The message to display.
        """
        self.clear()

        # Metadata header
        self.write(f"--- Message #{proxy_message.sequence} ---")
        self.write(f"Direction: {proxy_message.direction.value}")
        self.write(f"Timestamp: {proxy_message.timestamp.isoformat()}")
        if proxy_message.method:
            self.write(f"Method: {proxy_message.method}")
        if proxy_message.jsonrpc_id is not None:
            self.write(f"JSON-RPC ID: {proxy_message.jsonrpc_id}")
        if proxy_message.correlated_id:
            self.write(f"Correlated to: {proxy_message.correlated_id}")
        if proxy_message.modified:
            self.write("[Modified]")
        self.write("")

        # JSON payload
        payload = proxy_message.raw.model_dump(by_alias=True, exclude_none=True)
        formatted = json.dumps(payload, indent=2)
        self.write(formatted)
