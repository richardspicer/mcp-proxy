"""Message list widget for mcp-proxy TUI.

Scrollable list showing the live stream of intercepted JSON-RPC messages.
Each item displays direction, sequence number, and method/type.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Static

from mcp_proxy.models import Direction, ProxyMessage


class MessageSelected(Message):
    """Fired when the user selects a message in the list.

    Args:
        proxy_message: The selected ProxyMessage.
    """

    def __init__(self, proxy_message: ProxyMessage) -> None:
        super().__init__()
        self.proxy_message = proxy_message


class MessageListPanel(Widget):
    """Left-side panel showing the live message stream.

    Messages are displayed as: ``{icon} #{sequence} {arrow} {method_or_type}``

    - ``\\u25ba`` (right arrow) for CLIENT_TO_SERVER
    - ``\\u25c4`` (left arrow) for SERVER_TO_CLIENT
    - ``\\u23f8`` (pause icon) prefix for held messages
    - ``\\u2715`` (cross mark) prefix for dropped messages

    Attributes:
        messages: Ordered list of all ProxyMessages added.
    """

    DEFAULT_CSS = """
    MessageListPanel {
        border: solid $primary;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[ProxyMessage] = []
        self._held_ids: set[str] = set()
        self._dropped_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        """Compose the widget with an empty ListView."""
        yield ListView()

    def add_message(self, proxy_message: ProxyMessage) -> None:
        """Add a new message to the list.

        Args:
            proxy_message: The message to add.
        """
        self.messages.append(proxy_message)
        label = self._format_label(proxy_message)
        item = ListItem(Static(label), id=f"msg-{proxy_message.id}")
        self.query_one(ListView).append(item)

    def mark_held(self, proxy_id: str) -> None:
        """Mark a message as held (show pause icon).

        Args:
            proxy_id: The ProxyMessage.id to mark.
        """
        self._held_ids.add(proxy_id)
        self._update_item_label(proxy_id)

    def mark_forwarded(self, proxy_id: str) -> None:
        """Remove held indicator from a message.

        Args:
            proxy_id: The ProxyMessage.id to update.
        """
        self._held_ids.discard(proxy_id)
        self._update_item_label(proxy_id)

    def mark_dropped(self, proxy_id: str) -> None:
        """Show drop indicator on a message.

        Args:
            proxy_id: The ProxyMessage.id to mark as dropped.
        """
        self._held_ids.discard(proxy_id)
        self._dropped_ids.add(proxy_id)
        self._update_item_label(proxy_id)

    def get_selected_message(self) -> ProxyMessage | None:
        """Return the ProxyMessage for the currently highlighted item.

        Returns:
            The selected ProxyMessage, or None if nothing is highlighted.
        """
        list_view = self.query_one(ListView)
        if list_view.highlighted_child is None:
            return None
        item_id = list_view.highlighted_child.id
        if item_id is None:
            return None
        proxy_id = item_id.removeprefix("msg-")
        for pm in self.messages:
            if pm.id == proxy_id:
                return pm
        return None

    def _update_item_label(self, proxy_id: str) -> None:
        """Re-render the label for a message item.

        Args:
            proxy_id: The ProxyMessage.id to update.
        """
        for pm in self.messages:
            if pm.id == proxy_id:
                try:
                    item = self.query_one(f"#msg-{proxy_id}", ListItem)
                    label = self._format_label(pm)
                    item.query_one(Static).update(label)
                except NoMatches:
                    pass
                break

    def _format_label(self, pm: ProxyMessage, held: bool = False) -> str:
        """Format a single message line for display.

        Args:
            pm: The ProxyMessage to format.
            held: Whether the message is currently held.

        Returns:
            Formatted label string.
        """
        if pm.id in self._dropped_ids:
            prefix = "\u2715 "
        elif held or pm.id in self._held_ids:
            prefix = "\u23f8 "
        else:
            prefix = ""
        arrow = "\u25ba" if pm.direction == Direction.CLIENT_TO_SERVER else "\u25c4"
        method_label = pm.method if pm.method else "response"
        return f"{prefix}{arrow} #{pm.sequence} {method_label}"

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Handle list item highlight -- fire MessageSelected.

        Args:
            event: The Textual highlighted event from ListView.
        """
        if event.item is None:
            return
        item_id = event.item.id
        if item_id is None:
            return
        # Extract proxy_id from "msg-{uuid}"
        proxy_id = item_id.removeprefix("msg-")
        for pm in self.messages:
            if pm.id == proxy_id:
                self.post_message(MessageSelected(pm))
                break
