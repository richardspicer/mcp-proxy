"""Core data models for mcp-proxy.

Defines the envelope types that wrap every intercepted JSON-RPC message,
session containers for capture/export, and intercept engine state.
"""

from enum import StrEnum


class Direction(StrEnum):
    """Direction of a proxied message relative to the MCP client.

    Attributes:
        CLIENT_TO_SERVER: Message flowing from the MCP client to the server.
        SERVER_TO_CLIENT: Message flowing from the MCP server to the client.
    """

    CLIENT_TO_SERVER = "client_to_server"
    SERVER_TO_CLIENT = "server_to_client"


class Transport(StrEnum):
    """MCP transport type in use for a proxy session.

    Attributes:
        STDIO: Standard input/output transport.
        SSE: Server-Sent Events transport.
        STREAMABLE_HTTP: Streamable HTTP transport.
    """

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class InterceptMode(StrEnum):
    """Operating mode for the intercept engine.

    Attributes:
        PASSTHROUGH: Messages flow through without being held for inspection.
        INTERCEPT: Messages are held for user inspection before forwarding.
    """

    PASSTHROUGH = "passthrough"
    INTERCEPT = "intercept"


class InterceptAction(StrEnum):
    """User action on a held message.

    Attributes:
        FORWARD: Forward the message to its destination unchanged.
        MODIFY: Forward the message with user modifications applied.
        DROP: Discard the message without forwarding.
    """

    FORWARD = "forward"
    MODIFY = "modify"
    DROP = "drop"
