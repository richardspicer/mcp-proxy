# Data Models + Transport Adapter Protocol Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the core data models, transport adapter protocol, and correlation helpers that all other mcp-proxy components depend on.

**Architecture:** Four enums (Direction, Transport, InterceptMode, InterceptAction), two dataclasses (ProxyMessage, HeldMessage), one dataclass (InterceptState), one Pydantic model (ProxySession), a Protocol class (TransportAdapter), and correlation utility functions. JSONRPCMessage is a Pydantic RootModel from `mcp.types`; SessionMessage is a dataclass from `mcp.shared.message`. Access underlying JSON-RPC types via `JSONRPCMessage.root`.

**Tech Stack:** Python 3.11+, mcp SDK v1.x, Pydantic v2, pytest + pytest-asyncio

---

### Task 1: Create `models.py` — Enums

**Files:**
- Create: `src/mcp_proxy/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing tests for enums**

```python
"""Tests for mcp_proxy.models."""

from mcp_proxy.models import Direction, InterceptAction, InterceptMode, Transport


class TestDirection:
    def test_values(self) -> None:
        assert Direction.CLIENT_TO_SERVER == "client_to_server"
        assert Direction.SERVER_TO_CLIENT == "server_to_client"

    def test_string_serialization(self) -> None:
        assert str(Direction.CLIENT_TO_SERVER) == "Direction.CLIENT_TO_SERVER"
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
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_proxy.models'`

**Step 3: Write minimal implementation — enums only**

```python
"""Core data models for mcp-proxy.

Defines the envelope types that wrap every intercepted JSON-RPC message,
session containers for capture/export, and intercept engine state.
"""

from enum import Enum


class Direction(str, Enum):
    """Direction of a proxied message relative to the MCP client."""

    CLIENT_TO_SERVER = "client_to_server"
    SERVER_TO_CLIENT = "server_to_client"


class Transport(str, Enum):
    """MCP transport type in use for a proxy session."""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"


class InterceptMode(str, Enum):
    """Operating mode for the intercept engine."""

    PASSTHROUGH = "passthrough"
    INTERCEPT = "intercept"


class InterceptAction(str, Enum):
    """User action on a held message."""

    FORWARD = "forward"
    MODIFY = "modify"
    DROP = "drop"
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
echo "feat(models): add Direction, Transport, InterceptMode, InterceptAction enums" > .commitmsg
git add src/mcp_proxy/models.py tests/test_models.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 2: Add `ProxyMessage` dataclass to `models.py`

**Files:**
- Modify: `src/mcp_proxy/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write the failing tests for ProxyMessage**

Append to `tests/test_models.py`:

```python
import uuid
from datetime import datetime, timezone

from mcp.types import JSONRPCMessage, JSONRPCRequest

from mcp_proxy.models import ProxyMessage


def _make_request(method: str = "tools/list", msg_id: int = 1) -> JSONRPCMessage:
    """Helper to build a JSONRPCMessage wrapping a request."""
    return JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))


class TestProxyMessage:
    def test_creation(self) -> None:
        raw = _make_request()
        msg = ProxyMessage(
            id=str(uuid.uuid4()),
            sequence=1,
            timestamp=datetime.now(tz=timezone.utc),
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
            timestamp=datetime.now(tz=timezone.utc),
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
```

**Step 2: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_models.py::TestProxyMessage -v`
Expected: FAIL — `ImportError: cannot import name 'ProxyMessage'`

**Step 3: Add ProxyMessage to models.py**

Add imports and class after the enums:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from mcp.types import JSONRPCMessage

# ... enums stay as-is ...

@dataclass
class ProxyMessage:
    """A single intercepted MCP JSON-RPC message with proxy metadata.

    Args:
        id: Unique proxy-assigned ID (UUID string).
        sequence: Monotonic sequence number within the session.
        timestamp: When the proxy received this message.
        direction: CLIENT_TO_SERVER or SERVER_TO_CLIENT.
        transport: The transport type (STDIO, SSE, STREAMABLE_HTTP).
        raw: The actual JSON-RPC message (MCP SDK type).
        jsonrpc_id: JSON-RPC id field (None for notifications).
        method: JSON-RPC method (None for responses).
        correlated_id: Proxy ID of the request this response correlates to.
        modified: True if the user modified this message before forwarding.
        original_raw: Pre-modification snapshot (populated when modified=True).
    """

    id: str
    sequence: int
    timestamp: datetime
    direction: Direction
    transport: Transport
    raw: JSONRPCMessage
    jsonrpc_id: str | int | None
    method: str | None
    correlated_id: str | None
    modified: bool
    original_raw: JSONRPCMessage | None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All 10 tests PASS

**Step 5: Commit**

```bash
echo "feat(models): add ProxyMessage dataclass" > .commitmsg
git add src/mcp_proxy/models.py tests/test_models.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 3: Add `HeldMessage` and `InterceptState` dataclasses to `models.py`

**Files:**
- Modify: `src/mcp_proxy/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
import asyncio

from mcp_proxy.models import HeldMessage, InterceptState


class TestHeldMessage:
    def test_creation(self) -> None:
        raw = _make_request()
        proxy_msg = ProxyMessage(
            id="test-id",
            sequence=1,
            timestamp=datetime.now(tz=timezone.utc),
            direction=Direction.CLIENT_TO_SERVER,
            transport=Transport.STDIO,
            raw=raw,
            jsonrpc_id=1,
            method="tools/list",
            correlated_id=None,
            modified=False,
            original_raw=None,
        )
        held = HeldMessage(
            proxy_message=proxy_msg,
            release=asyncio.Event(),
            action=None,
            modified_raw=None,
        )
        assert held.proxy_message is proxy_msg
        assert held.action is None
        assert held.modified_raw is None
        assert not held.release.is_set()

    async def test_event_signaling(self) -> None:
        raw = _make_request()
        proxy_msg = ProxyMessage(
            id="test-id",
            sequence=1,
            timestamp=datetime.now(tz=timezone.utc),
            direction=Direction.CLIENT_TO_SERVER,
            transport=Transport.STDIO,
            raw=raw,
            jsonrpc_id=1,
            method="tools/list",
            correlated_id=None,
            modified=False,
            original_raw=None,
        )
        held = HeldMessage(
            proxy_message=proxy_msg,
            release=asyncio.Event(),
            action=None,
            modified_raw=None,
        )
        assert not held.release.is_set()
        held.action = InterceptAction.FORWARD
        held.release.set()
        assert held.release.is_set()
        assert held.action == InterceptAction.FORWARD


class TestInterceptState:
    def test_default_passthrough(self) -> None:
        state = InterceptState(mode=InterceptMode.PASSTHROUGH)
        assert state.mode == InterceptMode.PASSTHROUGH
        assert state.held_messages == []

    def test_with_held_messages(self) -> None:
        raw = _make_request()
        proxy_msg = ProxyMessage(
            id="test-id",
            sequence=1,
            timestamp=datetime.now(tz=timezone.utc),
            direction=Direction.CLIENT_TO_SERVER,
            transport=Transport.STDIO,
            raw=raw,
            jsonrpc_id=1,
            method="tools/list",
            correlated_id=None,
            modified=False,
            original_raw=None,
        )
        held = HeldMessage(
            proxy_message=proxy_msg,
            release=asyncio.Event(),
            action=None,
            modified_raw=None,
        )
        state = InterceptState(
            mode=InterceptMode.INTERCEPT,
            held_messages=[held],
        )
        assert state.mode == InterceptMode.INTERCEPT
        assert len(state.held_messages) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestHeldMessage tests/test_models.py::TestInterceptState -v`
Expected: FAIL — `ImportError: cannot import name 'HeldMessage'`

**Step 3: Add HeldMessage and InterceptState to models.py**

```python
@dataclass
class HeldMessage:
    """A message held by the intercept engine, awaiting user action.

    Args:
        proxy_message: The intercepted message.
        release: Event set when the user acts (forward/modify/drop).
        action: The user's chosen action (populated before setting release).
        modified_raw: If action is MODIFY, the edited JSON-RPC message.
    """

    proxy_message: ProxyMessage
    release: asyncio.Event
    action: InterceptAction | None
    modified_raw: JSONRPCMessage | None


@dataclass
class InterceptState:
    """Current state of the intercept engine.

    Args:
        mode: PASSTHROUGH (forward all) or INTERCEPT (hold all).
        held_messages: Messages currently waiting for user action.
    """

    mode: InterceptMode
    held_messages: list[HeldMessage] = field(default_factory=list)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All 14 tests PASS

**Step 5: Commit**

```bash
echo "feat(models): add HeldMessage and InterceptState dataclasses" > .commitmsg
git add src/mcp_proxy/models.py tests/test_models.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 4: Add `ProxySession` Pydantic model to `models.py`

**Files:**
- Modify: `src/mcp_proxy/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
from mcp_proxy.models import ProxySession


class TestProxySession:
    def test_creation(self) -> None:
        session = ProxySession(
            id="session-123",
            started_at=datetime.now(tz=timezone.utc),
            ended_at=None,
            transport=Transport.STDIO,
            server_command="python server.py",
            server_url=None,
            messages=[],
            metadata={},
        )
        assert session.id == "session-123"
        assert session.ended_at is None
        assert session.server_command == "python server.py"
        assert session.messages == []

    def test_serialization_roundtrip(self) -> None:
        now = datetime.now(tz=timezone.utc)
        session = ProxySession(
            id="session-456",
            started_at=now,
            ended_at=now,
            transport=Transport.SSE,
            server_command=None,
            server_url="http://localhost:8080/sse",
            messages=[],
            metadata={"target": "test-server", "notes": "integration test"},
        )
        json_str = session.model_dump_json()
        restored = ProxySession.model_validate_json(json_str)
        assert restored.id == session.id
        assert restored.started_at == session.started_at
        assert restored.ended_at == session.ended_at
        assert restored.transport == Transport.SSE
        assert restored.server_url == "http://localhost:8080/sse"
        assert restored.metadata == {"target": "test-server", "notes": "integration test"}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestProxySession -v`
Expected: FAIL — `ImportError: cannot import name 'ProxySession'`

**Step 3: Add ProxySession Pydantic model to models.py**

Add `from pydantic import BaseModel` to imports. Add class:

```python
class ProxySession(BaseModel):
    """A captured proxy session — the unit of evidence.

    Serialized to JSON for session export and replay. Uses Pydantic
    for reliable serialization/deserialization.

    Args:
        id: Session UUID string.
        started_at: When the session started.
        ended_at: When the session ended (None if still active).
        transport: The transport type used for this session.
        server_command: For stdio: the command that launched the server.
        server_url: For SSE/HTTP: the server endpoint URL.
        messages: Ordered sequence of serialized proxy messages.
        metadata: Arbitrary session metadata (target name, notes, etc.).
    """

    id: str
    started_at: datetime
    ended_at: datetime | None
    transport: Transport
    server_command: str | None
    server_url: str | None
    messages: list[dict[str, Any]]
    metadata: dict[str, Any]
```

**Note:** `messages` is `list[dict[str, Any]]` rather than `list[ProxyMessage]` because ProxyMessage is a dataclass containing non-serializable SDK types (JSONRPCMessage). The session store will handle ProxyMessage → dict conversion before populating this field.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All 16 tests PASS

**Step 5: Commit**

```bash
echo "feat(models): add ProxySession Pydantic model for session export" > .commitmsg
git add src/mcp_proxy/models.py tests/test_models.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 5: Create `adapters/base.py` — TransportAdapter Protocol

**Files:**
- Create: `src/mcp_proxy/adapters/base.py`
- Modify: `src/mcp_proxy/adapters/__init__.py`

**Step 1: Write `adapters/base.py`**

```python
"""Transport adapter protocol for mcp-proxy.

All transport adapters (stdio, SSE, streamable HTTP) implement this
protocol. The message pipeline interacts only with this interface —
it never sees transport-specific details or anyio streams.
"""

from typing import Protocol

from mcp.shared.message import SessionMessage


class TransportAdapter(Protocol):
    """Interface for transport adapters.

    Adapters translate between MCP SDK anyio streams and the asyncio-based
    pipeline. Each transport requires a matched pair: one client-facing
    (proxy acts as server) and one server-facing (proxy acts as client).

    The pipeline calls read() to receive the next message from one side
    and write() to send it to the other side. close() shuts down the
    transport connection.
    """

    async def read(self) -> SessionMessage:
        """Read the next message from this side of the connection.

        Returns:
            The next SessionMessage from the transport stream.

        Raises:
            Exception: If the connection is closed or broken.
        """
        ...

    async def write(self, message: SessionMessage) -> None:
        """Write a message to this side of the connection.

        Args:
            message: The SessionMessage to send over the transport.

        Raises:
            Exception: If the connection is closed or broken.
        """
        ...

    async def close(self) -> None:
        """Shut down this side of the connection.

        Releases any resources held by the adapter (subprocesses,
        network connections, streams). Safe to call multiple times.
        """
        ...
```

**Step 2: Update `adapters/__init__.py` to re-export**

```python
"""Transport adapters — translate between SDK anyio streams and asyncio queues."""

from mcp_proxy.adapters.base import TransportAdapter

__all__ = ["TransportAdapter"]
```

**Step 3: Verify import works**

Run: `uv run python -c "from mcp_proxy.adapters import TransportAdapter; print(TransportAdapter)"`
Expected: Prints the TransportAdapter class

**Step 4: Commit**

```bash
echo "feat(adapters): add TransportAdapter protocol interface" > .commitmsg
git add src/mcp_proxy/adapters/base.py src/mcp_proxy/adapters/__init__.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 6: Create `correlation.py` — JSON-RPC Field Extraction Helpers

**Files:**
- Create: `src/mcp_proxy/correlation.py`
- Create: `tests/test_correlation.py`

**Step 1: Write the failing tests**

```python
"""Tests for mcp_proxy.correlation."""

from mcp.types import (
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

from mcp_proxy.correlation import (
    extract_jsonrpc_id,
    extract_method,
    is_notification,
    is_request,
    is_response,
)


def _request(method: str = "tools/list", msg_id: int = 1) -> JSONRPCMessage:
    return JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))


def _response(msg_id: int = 1) -> JSONRPCMessage:
    return JSONRPCMessage(JSONRPCResponse(jsonrpc="2.0", id=msg_id, result={}))


def _error(msg_id: int = 1) -> JSONRPCMessage:
    return JSONRPCMessage(
        JSONRPCError(
            jsonrpc="2.0",
            id=msg_id,
            error={"code": -32600, "message": "Invalid Request"},
        )
    )


def _notification(method: str = "notifications/progress") -> JSONRPCMessage:
    return JSONRPCMessage(JSONRPCNotification(jsonrpc="2.0", method=method))


class TestExtractJsonrpcId:
    def test_request_id(self) -> None:
        assert extract_jsonrpc_id(_request(msg_id=42)) == 42

    def test_response_id(self) -> None:
        assert extract_jsonrpc_id(_response(msg_id=7)) == 7

    def test_error_id(self) -> None:
        assert extract_jsonrpc_id(_error(msg_id=99)) == 99

    def test_notification_has_no_id(self) -> None:
        assert extract_jsonrpc_id(_notification()) is None

    def test_string_id(self) -> None:
        msg = JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id="abc-123", method="test"))
        assert extract_jsonrpc_id(msg) == "abc-123"


class TestExtractMethod:
    def test_request_method(self) -> None:
        assert extract_method(_request("tools/call")) == "tools/call"

    def test_notification_method(self) -> None:
        assert extract_method(_notification("notifications/cancelled")) == "notifications/cancelled"

    def test_response_has_no_method(self) -> None:
        assert extract_method(_response()) is None

    def test_error_has_no_method(self) -> None:
        assert extract_method(_error()) is None


class TestMessageClassification:
    def test_request(self) -> None:
        msg = _request()
        assert is_request(msg) is True
        assert is_response(msg) is False
        assert is_notification(msg) is False

    def test_response(self) -> None:
        msg = _response()
        assert is_request(msg) is False
        assert is_response(msg) is True
        assert is_notification(msg) is False

    def test_error_is_response(self) -> None:
        msg = _error()
        assert is_request(msg) is False
        assert is_response(msg) is True
        assert is_notification(msg) is False

    def test_notification(self) -> None:
        msg = _notification()
        assert is_request(msg) is False
        assert is_response(msg) is False
        assert is_notification(msg) is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_correlation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_proxy.correlation'`

**Step 3: Write the implementation**

```python
"""JSON-RPC field extraction and message classification utilities.

Insulates the rest of the codebase from the MCP SDK's JSONRPCMessage
internal structure. The pipeline, session store, and correlation logic
use these helpers instead of reaching into raw message internals.
"""

from mcp.types import (
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)


def extract_jsonrpc_id(message: JSONRPCMessage) -> str | int | None:
    """Extract the JSON-RPC id field from a message.

    Args:
        message: A JSONRPCMessage (RootModel wrapping a request, response,
            notification, or error).

    Returns:
        The id value (str or int) for requests, responses, and errors.
        None for notifications (which have no id field).
    """
    root = message.root
    if isinstance(root, JSONRPCRequest | JSONRPCResponse | JSONRPCError):
        return root.id
    return None


def extract_method(message: JSONRPCMessage) -> str | None:
    """Extract the JSON-RPC method field from a message.

    Args:
        message: A JSONRPCMessage (RootModel wrapping a request, response,
            notification, or error).

    Returns:
        The method string for requests and notifications.
        None for responses and errors (which have no method field).
    """
    root = message.root
    if isinstance(root, JSONRPCRequest | JSONRPCNotification):
        return root.method
    return None


def is_request(message: JSONRPCMessage) -> bool:
    """Check if the message is a JSON-RPC request (has id and method).

    Args:
        message: A JSONRPCMessage to classify.

    Returns:
        True if the message is a request.
    """
    return isinstance(message.root, JSONRPCRequest)


def is_response(message: JSONRPCMessage) -> bool:
    """Check if the message is a JSON-RPC response or error (has id, no method).

    Args:
        message: A JSONRPCMessage to classify.

    Returns:
        True if the message is a response or error.
    """
    return isinstance(message.root, JSONRPCResponse | JSONRPCError)


def is_notification(message: JSONRPCMessage) -> bool:
    """Check if the message is a JSON-RPC notification (has method, no id).

    Args:
        message: A JSONRPCMessage to classify.

    Returns:
        True if the message is a notification.
    """
    return isinstance(message.root, JSONRPCNotification)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_correlation.py -v`
Expected: All 13 tests PASS

**Step 5: Commit**

```bash
echo "feat(correlation): add JSON-RPC field extraction and classification helpers" > .commitmsg
git add src/mcp_proxy/correlation.py tests/test_correlation.py
git commit -F .commitmsg
rm .commitmsg
```

---

### Task 7: Full Verification

**Step 1: Run full test suite**

Run: `uv run pytest -q`
Expected: All tests pass (existing CLI tests + new model/correlation tests)

**Step 2: Run linter**

Run: `uv run ruff check .`
Expected: No errors

**Step 3: Run formatter check**

Run: `uv run ruff format --check .`
Expected: All files formatted correctly

**Step 4: Run type checker**

Run: `uv run mypy src/mcp_proxy/`
Expected: No errors (note: `ignore_missing_imports = true` in pyproject.toml)

**Step 5: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: All hooks pass

**Step 6: CLI smoke test**

Run: `uv run mcp-proxy --help`
Expected: Help output with "Interactive MCP traffic interceptor"

**Step 7: Push branch**

```bash
git push -u origin feature/data-models-adapters
```
