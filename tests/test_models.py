"""Tests for mcp_proxy.models."""

import uuid
from datetime import UTC, datetime

from mcp.types import JSONRPCMessage, JSONRPCRequest

from mcp_proxy.models import Direction, InterceptAction, InterceptMode, ProxyMessage, Transport


class TestDirection:
    def test_values(self) -> None:
        assert Direction.CLIENT_TO_SERVER == "client_to_server"
        assert Direction.SERVER_TO_CLIENT == "server_to_client"

    def test_string_serialization(self) -> None:
        assert str(Direction.CLIENT_TO_SERVER) == "client_to_server"
        assert Direction.CLIENT_TO_SERVER.value == "client_to_server"

    def test_members_count(self) -> None:
        assert len(Direction) == 2


class TestTransport:
    def test_values(self) -> None:
        assert Transport.STDIO == "stdio"
        assert Transport.SSE == "sse"
        assert Transport.STREAMABLE_HTTP == "streamable_http"

    def test_members_count(self) -> None:
        assert len(Transport) == 3


class TestInterceptMode:
    def test_values(self) -> None:
        assert InterceptMode.PASSTHROUGH == "passthrough"
        assert InterceptMode.INTERCEPT == "intercept"

    def test_members_count(self) -> None:
        assert len(InterceptMode) == 2


class TestInterceptAction:
    def test_values(self) -> None:
        assert InterceptAction.FORWARD == "forward"
        assert InterceptAction.MODIFY == "modify"
        assert InterceptAction.DROP == "drop"

    def test_members_count(self) -> None:
        assert len(InterceptAction) == 3


def _make_request(method: str = "tools/list", msg_id: int = 1) -> JSONRPCMessage:
    """Helper to build a JSONRPCMessage wrapping a request."""
    return JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))


class TestProxyMessage:
    def test_creation(self) -> None:
        raw = _make_request()
        msg = ProxyMessage(
            id=str(uuid.uuid4()),
            sequence=1,
            timestamp=datetime.now(tz=UTC),
            direction=Direction.CLIENT_TO_SERVER,
            transport=Transport.STDIO,
            raw=raw,
            jsonrpc_id=1,
            method="tools/list",
            correlated_id=None,
            modified=False,
            original_raw=None,
        )
        assert msg.sequence == 1
        assert msg.direction == Direction.CLIENT_TO_SERVER
        assert msg.transport == Transport.STDIO
        assert msg.jsonrpc_id == 1
        assert msg.method == "tools/list"
        assert msg.modified is False
        assert msg.original_raw is None

    def test_modified_preserves_original(self) -> None:
        original = _make_request("tools/call", msg_id=2)
        modified = _make_request("tools/call", msg_id=2)
        msg = ProxyMessage(
            id=str(uuid.uuid4()),
            sequence=2,
            timestamp=datetime.now(tz=UTC),
            direction=Direction.CLIENT_TO_SERVER,
            transport=Transport.STDIO,
            raw=modified,
            jsonrpc_id=2,
            method="tools/call",
            correlated_id=None,
            modified=True,
            original_raw=original,
        )
        assert msg.modified is True
        assert msg.original_raw is not None
        assert msg.original_raw is original
