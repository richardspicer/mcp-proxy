"""Tests for mcp_proxy.models."""

from mcp_proxy.models import Direction, InterceptAction, InterceptMode, Transport


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
