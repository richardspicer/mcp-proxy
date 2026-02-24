"""Transport adapters — translate between SDK anyio streams and asyncio queues."""

from mcp_proxy.adapters.base import TransportAdapter
from mcp_proxy.adapters.stdio import StdioClientAdapter, StdioServerAdapter

__all__ = ["StdioClientAdapter", "StdioServerAdapter", "TransportAdapter"]
