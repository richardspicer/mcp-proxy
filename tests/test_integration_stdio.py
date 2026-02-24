"""Integration smoke tests — proxy pipeline with a real FastMCP server via stdio.

Verifies end-to-end message flow through StdioServerAdapter with a live
FastMCP subprocess. Uses an IntegrationClientAdapter that separates message
injection from connection closure, allowing the server time to process
requests and relay responses.

Root cause of previous failures: the unit-test MockAdapter's enqueue() pushes
messages then a None sentinel. When the client-to-server forward loop reads
None, it raises, which triggers asyncio.TaskGroup to cancel the server-to-client
loop before the real server has time to process requests and send responses.

Fix: IntegrationClientAdapter.enqueue() adds messages without closing. The test
calls shutdown() explicitly after collecting expected responses.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

from mcp_proxy.adapters.stdio import StdioServerAdapter
from mcp_proxy.intercept import InterceptEngine
from mcp_proxy.models import (
    Direction,
    HeldMessage,
    InterceptAction,
    InterceptMode,
    Transport,
)
from mcp_proxy.pipeline import PipelineSession, run_pipeline
from mcp_proxy.session_store import SessionStore

# Path to the FastMCP fixture server
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "vuln_injection.py"

# Timeout for waiting on server responses (seconds)
RESPONSE_TIMEOUT = 15

# Timeout for pipeline shutdown after close signal (seconds)
SHUTDOWN_TIMEOUT = 5


# ---------------------------------------------------------------------------
# Integration client adapter
# ---------------------------------------------------------------------------


class IntegrationClientAdapter:
    """Mock client adapter with separate enqueue and close lifecycle.

    Unlike the unit-test MockAdapter, this does NOT immediately send a
    close sentinel after enqueuing messages. The test explicitly calls
    shutdown() after collecting responses, giving the real server time
    to process requests and send responses back through the pipeline.

    Example:
        adapter = IntegrationClientAdapter()
        adapter.enqueue(request_msg)
        # ... pipeline runs, server responds ...
        response = await adapter.write_queue.get()
        adapter.shutdown()
    """

    def __init__(self) -> None:
        self.read_queue: asyncio.Queue[SessionMessage | None] = asyncio.Queue()
        self.write_queue: asyncio.Queue[SessionMessage] = asyncio.Queue()

    async def read(self) -> SessionMessage:
        """Read next message. Raises on close sentinel.

        Returns:
            The next SessionMessage from the read queue.

        Raises:
            RuntimeError: When the close sentinel is received.
        """
        item = await self.read_queue.get()
        if item is None:
            raise RuntimeError("IntegrationClientAdapter closed")
        return item

    async def write(self, message: SessionMessage) -> None:
        """Store message for test inspection.

        Args:
            message: The SessionMessage written by the pipeline.
        """
        await self.write_queue.put(message)

    async def close(self) -> None:
        """No-op — shutdown is handled explicitly via shutdown()."""

    def enqueue(self, *messages: SessionMessage) -> None:
        """Add messages to the read queue without sending a close sentinel.

        Args:
            messages: SessionMessage objects to queue for reading.
        """
        for msg in messages:
            self.read_queue.put_nowait(msg)

    def shutdown(self) -> None:
        """Send close sentinel to terminate the client-to-server forward loop."""
        self.read_queue.put_nowait(None)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _make_initialize_request(msg_id: int = 1) -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(
            JSONRPCRequest(
                jsonrpc="2.0",
                id=msg_id,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1.0"},
                },
            )
        )
    )


def _make_initialized_notification() -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(
            JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized")
        )
    )


def _make_tools_list_request(msg_id: int = 2) -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(
            JSONRPCRequest(jsonrpc="2.0", id=msg_id, method="tools/list")
        )
    )


def _make_pipeline_session(**overrides: Any) -> PipelineSession:
    """Build a PipelineSession with sensible defaults."""
    defaults: dict[str, Any] = {
        "session_store": SessionStore(
            session_id=str(uuid.uuid4()), transport=Transport.STDIO
        ),
        "intercept_engine": InterceptEngine(mode=InterceptMode.PASSTHROUGH),
        "transport": Transport.STDIO,
        "on_message": None,
        "on_held": None,
        "on_forwarded": None,
    }
    defaults.update(overrides)
    return PipelineSession(**defaults)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _shutdown_pipeline(
    client: IntegrationClientAdapter,
    pipeline_task: asyncio.Task[None],
) -> None:
    """Cleanly shut down the pipeline. Safe even if already finished."""
    client.shutdown()
    try:
        await asyncio.wait_for(pipeline_task, timeout=SHUTDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        pipeline_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pipeline_task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitializeHandshake:
    """Send initialize + notifications/initialized, verify response."""

    async def test_initialize_handshake(self) -> None:
        client = IntegrationClientAdapter()
        client.enqueue(
            _make_initialize_request(),
            _make_initialized_notification(),
        )

        async with StdioServerAdapter(
            command=sys.executable,
            args=[str(FIXTURE_PATH)],
        ) as server:
            session = _make_pipeline_session()
            pipeline_task = asyncio.create_task(
                run_pipeline(client, server, session)
            )

            try:
                response = await asyncio.wait_for(
                    client.write_queue.get(), timeout=RESPONSE_TIMEOUT
                )

                result = response.message.root.result
                assert "serverInfo" in result

                # Session store captured both directions
                messages = session.session_store.get_messages()
                directions = {m.direction for m in messages}
                assert Direction.CLIENT_TO_SERVER in directions
                assert Direction.SERVER_TO_CLIENT in directions
            finally:
                await _shutdown_pipeline(client, pipeline_task)


class TestToolsList:
    """Full handshake + tools/list, verify tool definitions."""

    async def test_tools_list(self) -> None:
        client = IntegrationClientAdapter()
        client.enqueue(
            _make_initialize_request(),
            _make_initialized_notification(),
        )

        async with StdioServerAdapter(
            command=sys.executable,
            args=[str(FIXTURE_PATH)],
        ) as server:
            session = _make_pipeline_session()
            pipeline_task = asyncio.create_task(
                run_pipeline(client, server, session)
            )

            try:
                # Collect initialize response first
                init_resp = await asyncio.wait_for(
                    client.write_queue.get(), timeout=RESPONSE_TIMEOUT
                )
                assert "serverInfo" in init_resp.message.root.result

                # Send tools/list after handshake completes
                client.enqueue(_make_tools_list_request())

                tools_resp = await asyncio.wait_for(
                    client.write_queue.get(), timeout=RESPONSE_TIMEOUT
                )
                result = tools_resp.message.root.result
                assert "tools" in result
                tool_names = [t["name"] for t in result["tools"]]
                assert "file_search" in tool_names
                assert "safe_echo" in tool_names
            finally:
                await _shutdown_pipeline(client, pipeline_task)


class TestInterceptHoldAndForward:
    """Intercept mode holds messages, auto-forwards, responses still arrive."""

    async def test_intercept_hold_and_forward(self) -> None:
        client = IntegrationClientAdapter()
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        held_messages: list[HeldMessage] = []

        def auto_forward(held: HeldMessage) -> None:
            held_messages.append(held)
            engine.release(held, InterceptAction.FORWARD)

        client.enqueue(
            _make_initialize_request(),
            _make_initialized_notification(),
        )

        async with StdioServerAdapter(
            command=sys.executable,
            args=[str(FIXTURE_PATH)],
        ) as server:
            session = _make_pipeline_session(
                intercept_engine=engine, on_held=auto_forward
            )
            pipeline_task = asyncio.create_task(
                run_pipeline(client, server, session)
            )

            try:
                response = await asyncio.wait_for(
                    client.write_queue.get(), timeout=RESPONSE_TIMEOUT
                )

                result = response.message.root.result
                assert "serverInfo" in result

                # Messages were intercepted (at least C->S requests + S->C response)
                assert len(held_messages) >= 2
            finally:
                await _shutdown_pipeline(client, pipeline_task)
