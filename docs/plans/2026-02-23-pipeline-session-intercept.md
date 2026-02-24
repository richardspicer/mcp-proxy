# Pipeline, Session Store, Intercept Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the core message pipeline that routes traffic between adapters, plus the session store and intercept engine it depends on.

**Architecture:** Two concurrent `_forward_loop` tasks in an `asyncio.TaskGroup` relay `SessionMessage` objects between a source and destination `TransportAdapter`. Each message is wrapped in a `ProxyMessage` envelope (UUID, sequence, timestamp, direction), captured by a `SessionStore`, checked against an `InterceptEngine` (hold/release/drop/modify), and forwarded. Request-response correlation uses JSON-RPC `id` fields.

**Tech Stack:** asyncio (stdlib), itertools.count, uuid4, Pydantic (ProxySession serialization), mcp SDK types (JSONRPCMessage, SessionMessage), existing correlation helpers.

---

## Task 1: Intercept Engine — Passthrough and Hold Logic

**Files:**
- Create: `src/mcp_proxy/intercept.py`
- Test: `tests/test_intercept.py`

### Step 1: Write the failing tests for intercept engine

```python
# tests/test_intercept.py
"""Tests for mcp_proxy.intercept — InterceptEngine."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from mcp.types import JSONRPCMessage, JSONRPCRequest

from mcp_proxy.intercept import InterceptEngine
from mcp_proxy.models import (
    Direction,
    HeldMessage,
    InterceptAction,
    InterceptMode,
    ProxyMessage,
    Transport,
)


def _make_proxy_message(
    method: str = "tools/list",
    msg_id: int = 1,
    direction: Direction = Direction.CLIENT_TO_SERVER,
    sequence: int = 1,
) -> ProxyMessage:
    """Build a ProxyMessage wrapping a JSON-RPC request."""
    raw = JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))
    return ProxyMessage(
        id=str(uuid.uuid4()),
        sequence=sequence,
        timestamp=datetime.now(tz=UTC),
        direction=direction,
        transport=Transport.STDIO,
        raw=raw,
        jsonrpc_id=msg_id,
        method=method,
        correlated_id=None,
        modified=False,
        original_raw=None,
    )


class TestShouldHold:
    """InterceptEngine.should_hold() based on mode."""

    def test_passthrough_returns_false(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.PASSTHROUGH)
        msg = _make_proxy_message()
        assert engine.should_hold(msg) is False

    def test_intercept_returns_true(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        assert engine.should_hold(msg) is True


class TestHold:
    """InterceptEngine.hold() creates HeldMessage."""

    def test_hold_creates_held_message(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        held = engine.hold(msg)
        assert isinstance(held, HeldMessage)
        assert held.proxy_message is msg
        assert held.action is None
        assert held.modified_raw is None
        assert not held.release.is_set()

    def test_hold_adds_to_held_list(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg1 = _make_proxy_message(sequence=1)
        msg2 = _make_proxy_message(sequence=2)
        engine.hold(msg1)
        engine.hold(msg2)
        assert len(engine.get_held()) == 2


class TestRelease:
    """InterceptEngine.release() sets action and fires event."""

    def test_release_forward(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        held = engine.hold(msg)
        engine.release(held, InterceptAction.FORWARD)
        assert held.action == InterceptAction.FORWARD
        assert held.release.is_set()
        assert held.modified_raw is None
        assert len(engine.get_held()) == 0

    def test_release_drop(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        held = engine.hold(msg)
        engine.release(held, InterceptAction.DROP)
        assert held.action == InterceptAction.DROP
        assert held.release.is_set()
        assert len(engine.get_held()) == 0

    def test_release_modify(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        held = engine.hold(msg)
        modified_raw = JSONRPCMessage(
            JSONRPCRequest(jsonrpc="2.0", id=99, method="tools/call")
        )
        engine.release(held, InterceptAction.MODIFY, modified_raw=modified_raw)
        assert held.action == InterceptAction.MODIFY
        assert held.modified_raw is modified_raw
        assert held.release.is_set()
        assert len(engine.get_held()) == 0


class TestSetMode:
    """InterceptEngine.set_mode() toggles and auto-releases."""

    def test_switch_to_passthrough_releases_all_held(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg1 = _make_proxy_message(sequence=1)
        msg2 = _make_proxy_message(sequence=2)
        held1 = engine.hold(msg1)
        held2 = engine.hold(msg2)
        engine.set_mode(InterceptMode.PASSTHROUGH)
        assert engine.mode == InterceptMode.PASSTHROUGH
        assert held1.release.is_set()
        assert held1.action == InterceptAction.FORWARD
        assert held2.release.is_set()
        assert held2.action == InterceptAction.FORWARD
        assert len(engine.get_held()) == 0

    def test_switch_to_intercept(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.PASSTHROUGH)
        engine.set_mode(InterceptMode.INTERCEPT)
        assert engine.mode == InterceptMode.INTERCEPT


class TestGetState:
    """InterceptEngine.get_state() returns snapshot."""

    def test_empty_state(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.PASSTHROUGH)
        state = engine.get_state()
        assert state.mode == InterceptMode.PASSTHROUGH
        assert state.held_messages == []

    def test_state_with_held_messages(self) -> None:
        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        msg = _make_proxy_message()
        engine.hold(msg)
        state = engine.get_state()
        assert state.mode == InterceptMode.INTERCEPT
        assert len(state.held_messages) == 1
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/test_intercept.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_proxy.intercept'`

### Step 3: Write the intercept engine implementation

```python
# src/mcp_proxy/intercept.py
"""Intercept engine for mcp-proxy.

Manages hold/release state for the message pipeline. When in INTERCEPT
mode, messages are held for user inspection. The user can then FORWARD,
MODIFY, or DROP each held message.
"""

from __future__ import annotations

import asyncio

from mcp.types import JSONRPCMessage

from mcp_proxy.models import (
    HeldMessage,
    InterceptAction,
    InterceptMode,
    InterceptState,
    ProxyMessage,
)


class InterceptEngine:
    """Controls whether messages are held for inspection or passed through.

    Args:
        mode: Initial intercept mode. Defaults to PASSTHROUGH.

    Example:
        >>> engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        >>> if engine.should_hold(proxy_msg):
        ...     held = engine.hold(proxy_msg)
        ...     await held.release.wait()
    """

    def __init__(self, mode: InterceptMode = InterceptMode.PASSTHROUGH) -> None:
        self._mode = mode
        self._held: list[HeldMessage] = []

    @property
    def mode(self) -> InterceptMode:
        """Current intercept mode."""
        return self._mode

    def set_mode(self, mode: InterceptMode) -> None:
        """Change the intercept mode.

        Args:
            mode: The new mode. When switching to PASSTHROUGH, all
                currently held messages are released with FORWARD action.
        """
        self._mode = mode
        if mode == InterceptMode.PASSTHROUGH:
            for held in list(self._held):
                self.release(held, InterceptAction.FORWARD)

    def should_hold(self, message: ProxyMessage) -> bool:
        """Check whether a message should be held for inspection.

        Args:
            message: The proxy message to check.

        Returns:
            True if the engine is in INTERCEPT mode.
        """
        return self._mode == InterceptMode.INTERCEPT

    def hold(self, message: ProxyMessage) -> HeldMessage:
        """Hold a message for user inspection.

        Args:
            message: The proxy message to hold.

        Returns:
            A HeldMessage with an unset release Event.
        """
        held = HeldMessage(
            proxy_message=message,
            release=asyncio.Event(),
            action=None,
            modified_raw=None,
        )
        self._held.append(held)
        return held

    def release(
        self,
        held: HeldMessage,
        action: InterceptAction,
        modified_raw: JSONRPCMessage | None = None,
    ) -> None:
        """Release a held message with the specified action.

        Args:
            held: The held message to release.
            action: FORWARD, DROP, or MODIFY.
            modified_raw: If action is MODIFY, the edited JSON-RPC message.
        """
        held.action = action
        held.modified_raw = modified_raw
        held.release.set()
        if held in self._held:
            self._held.remove(held)

    def get_held(self) -> list[HeldMessage]:
        """Return the list of currently held messages.

        Returns:
            List of HeldMessage objects awaiting user action.
        """
        return list(self._held)

    def get_state(self) -> InterceptState:
        """Return a snapshot of the current intercept state.

        Returns:
            InterceptState with current mode and held messages.
        """
        return InterceptState(mode=self._mode, held_messages=list(self._held))
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest tests/test_intercept.py -v`
Expected: All 11 tests PASS

### Step 5: Lint and type-check

Run: `uv run ruff check src/mcp_proxy/intercept.py tests/test_intercept.py && uv run ruff format --check src/mcp_proxy/intercept.py tests/test_intercept.py && uv run mypy src/mcp_proxy/intercept.py`
Expected: Clean

### Step 6: Commit

```
feat(intercept): add InterceptEngine with hold/release/mode management
```

---

## Task 2: Session Store — In-Memory Capture and Serialization

**Files:**
- Create: `src/mcp_proxy/session_store.py`
- Test: `tests/test_session_store.py`

### Step 1: Write the failing tests for session store

```python
# tests/test_session_store.py
"""Tests for mcp_proxy.session_store — SessionStore."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from mcp.types import (
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

from mcp_proxy.models import Direction, ProxyMessage, Transport
from mcp_proxy.session_store import SessionStore


def _make_proxy_message(
    method: str = "tools/list",
    msg_id: int = 1,
    direction: Direction = Direction.CLIENT_TO_SERVER,
    sequence: int = 1,
    proxy_id: str | None = None,
) -> ProxyMessage:
    """Build a ProxyMessage wrapping a JSON-RPC request."""
    raw = JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))
    return ProxyMessage(
        id=proxy_id or str(uuid.uuid4()),
        sequence=sequence,
        timestamp=datetime.now(tz=UTC),
        direction=direction,
        transport=Transport.STDIO,
        raw=raw,
        jsonrpc_id=msg_id,
        method=method,
        correlated_id=None,
        modified=False,
        original_raw=None,
    )


def _make_notification_message(
    method: str = "notifications/initialized",
    direction: Direction = Direction.CLIENT_TO_SERVER,
    sequence: int = 1,
) -> ProxyMessage:
    """Build a ProxyMessage wrapping a JSON-RPC notification."""
    raw = JSONRPCMessage(JSONRPCNotification(jsonrpc="2.0", method=method))
    return ProxyMessage(
        id=str(uuid.uuid4()),
        sequence=sequence,
        timestamp=datetime.now(tz=UTC),
        direction=direction,
        transport=Transport.STDIO,
        raw=raw,
        jsonrpc_id=None,
        method=method,
        correlated_id=None,
        modified=False,
        original_raw=None,
    )


def _make_response_message(
    msg_id: int = 1,
    direction: Direction = Direction.SERVER_TO_CLIENT,
    sequence: int = 2,
    correlated_id: str | None = None,
) -> ProxyMessage:
    """Build a ProxyMessage wrapping a JSON-RPC response."""
    raw = JSONRPCMessage(JSONRPCResponse(jsonrpc="2.0", id=msg_id, result={}))
    return ProxyMessage(
        id=str(uuid.uuid4()),
        sequence=sequence,
        timestamp=datetime.now(tz=UTC),
        direction=direction,
        transport=Transport.STDIO,
        raw=raw,
        jsonrpc_id=msg_id,
        method=None,
        correlated_id=correlated_id,
        modified=False,
        original_raw=None,
    )


class TestAppendAndRetrieve:
    """SessionStore.append() and get_messages()."""

    def test_append_and_get_messages(self) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        msg1 = _make_proxy_message(sequence=1)
        msg2 = _make_proxy_message(sequence=2, msg_id=2)
        store.append(msg1)
        store.append(msg2)
        messages = store.get_messages()
        assert len(messages) == 2
        assert messages[0] is msg1
        assert messages[1] is msg2

    def test_get_messages_returns_copy(self) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        msg = _make_proxy_message()
        store.append(msg)
        messages = store.get_messages()
        messages.clear()
        assert len(store.get_messages()) == 1


class TestGetById:
    """SessionStore.get_by_id() lookup."""

    def test_get_by_id_found(self) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        msg = _make_proxy_message(proxy_id="known-id")
        store.append(msg)
        result = store.get_by_id("known-id")
        assert result is msg

    def test_get_by_id_not_found(self) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        result = store.get_by_id("nonexistent")
        assert result is None


class TestSerialization:
    """SessionStore.to_proxy_session() and round-trip."""

    def test_to_proxy_session_basic(self) -> None:
        store = SessionStore(
            session_id="s1",
            transport=Transport.STDIO,
            server_command="python server.py",
        )
        msg = _make_proxy_message(proxy_id="msg-1", method="initialize", msg_id=1)
        store.append(msg)
        session = store.to_proxy_session()
        assert session.id == "s1"
        assert session.transport == Transport.STDIO
        assert session.server_command == "python server.py"
        assert len(session.messages) == 1
        m = session.messages[0]
        assert m["proxy_id"] == "msg-1"
        assert m["direction"] == "client_to_server"
        assert m["method"] == "initialize"

    def test_to_proxy_session_empty(self) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        session = store.to_proxy_session()
        assert session.messages == []

    def test_roundtrip_json(self) -> None:
        store = SessionStore(
            session_id="s1",
            transport=Transport.STDIO,
            server_command="python server.py",
            metadata={"note": "test"},
        )
        msg = _make_proxy_message(proxy_id="msg-1", method="initialize", msg_id=1)
        store.append(msg)
        session = store.to_proxy_session()
        json_str = session.model_dump_json(indent=2)
        parsed = json.loads(json_str)
        assert parsed["id"] == "s1"
        assert len(parsed["messages"]) == 1
        assert parsed["messages"][0]["proxy_id"] == "msg-1"
        assert parsed["metadata"] == {"note": "test"}


class TestSaveLoad:
    """SessionStore.save() and SessionStore.load() to filesystem."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        store = SessionStore(
            session_id="s1",
            transport=Transport.STDIO,
            server_command="python server.py",
            metadata={"note": "save-load test"},
        )
        req = _make_proxy_message(proxy_id="req-1", method="tools/list", msg_id=1)
        resp = _make_response_message(msg_id=1, correlated_id="req-1")
        notif = _make_notification_message(method="notifications/initialized")
        store.append(req)
        store.append(resp)
        store.append(notif)

        file_path = tmp_path / "session.json"
        store.save(file_path)
        assert file_path.exists()

        loaded = SessionStore.load(file_path)
        assert loaded.session_id == "s1"
        messages = loaded.get_messages()
        assert len(messages) == 3
        assert messages[0].id == "req-1"
        assert messages[0].method == "tools/list"
        assert messages[0].jsonrpc_id == 1
        assert messages[1].correlated_id == "req-1"
        assert messages[2].jsonrpc_id is None
        assert messages[2].method == "notifications/initialized"

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        store = SessionStore(session_id="s1", transport=Transport.STDIO)
        file_path = tmp_path / "subdir" / "session.json"
        store.save(file_path)
        assert file_path.exists()
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/test_session_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_proxy.session_store'`

### Step 3: Write the session store implementation

```python
# src/mcp_proxy/session_store.py
"""In-memory session capture for mcp-proxy.

Stores all ProxyMessage objects in a session, with save/load to JSON
via the ProxySession Pydantic model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.types import JSONRPCMessage

from mcp_proxy.models import Direction, ProxyMessage, ProxySession, Transport


class SessionStore:
    """In-memory capture of all proxied messages in a session.

    Args:
        session_id: Unique session identifier (UUID string).
        transport: Transport type for this session.
        server_command: For stdio sessions, the server launch command.
        server_url: For SSE/HTTP sessions, the server endpoint URL.
        metadata: Arbitrary session metadata.

    Example:
        >>> store = SessionStore(session_id="abc", transport=Transport.STDIO)
        >>> store.append(proxy_msg)
        >>> store.save(Path("session.json"))
    """

    def __init__(
        self,
        session_id: str,
        transport: Transport,
        server_command: str | None = None,
        server_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> None:
        self.session_id = session_id
        self.transport = transport
        self.server_command = server_command
        self.server_url = server_url
        self.metadata = metadata or {}
        self.started_at = started_at or datetime.now(tz=UTC)
        self._messages: list[ProxyMessage] = []
        self._index: dict[str, ProxyMessage] = {}

    def append(self, message: ProxyMessage) -> None:
        """Add a message to the session capture.

        Args:
            message: The proxy message to store.
        """
        self._messages.append(message)
        self._index[message.id] = message

    def get_messages(self) -> list[ProxyMessage]:
        """Return all captured messages in order.

        Returns:
            A copy of the message list.
        """
        return list(self._messages)

    def get_by_id(self, proxy_id: str) -> ProxyMessage | None:
        """Look up a message by its proxy-assigned ID.

        Args:
            proxy_id: The ProxyMessage.id to search for.

        Returns:
            The matching ProxyMessage, or None if not found.
        """
        return self._index.get(proxy_id)

    def to_proxy_session(self) -> ProxySession:
        """Convert to a ProxySession Pydantic model for serialization.

        Returns:
            A ProxySession containing all captured messages.
        """
        serialized_messages: list[dict[str, Any]] = []
        for msg in self._messages:
            entry: dict[str, Any] = {
                "proxy_id": msg.id,
                "sequence": msg.sequence,
                "timestamp": msg.timestamp.isoformat(),
                "direction": msg.direction.value,
                "transport": msg.transport.value,
                "jsonrpc_id": msg.jsonrpc_id,
                "method": msg.method,
                "correlated_id": msg.correlated_id,
                "modified": msg.modified,
                "payload": msg.raw.model_dump(by_alias=True, exclude_none=True),
            }
            if msg.original_raw is not None:
                entry["original_payload"] = msg.original_raw.model_dump(
                    by_alias=True, exclude_none=True
                )
            serialized_messages.append(entry)

        return ProxySession(
            id=self.session_id,
            started_at=self.started_at,
            ended_at=None,
            transport=self.transport,
            server_command=self.server_command,
            server_url=self.server_url,
            messages=serialized_messages,
            metadata=self.metadata,
        )

    def save(self, path: Path) -> None:
        """Save the session to a JSON file.

        Args:
            path: File path to write. Parent directories are created
                if they do not exist.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        session = self.to_proxy_session()
        path.write_text(session.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SessionStore:
        """Load a session from a JSON file.

        Args:
            path: File path to read.

        Returns:
            A SessionStore reconstructed from the saved session.
        """
        json_text = path.read_text(encoding="utf-8")
        session = ProxySession.model_validate_json(json_text)
        store = cls(
            session_id=session.id,
            transport=session.transport,
            server_command=session.server_command,
            server_url=session.server_url,
            metadata=session.metadata,
            started_at=session.started_at,
        )
        for entry in session.messages:
            raw = JSONRPCMessage.model_validate(entry["payload"])
            original_raw = None
            if "original_payload" in entry:
                original_raw = JSONRPCMessage.model_validate(entry["original_payload"])
            msg = ProxyMessage(
                id=entry["proxy_id"],
                sequence=entry["sequence"],
                timestamp=datetime.fromisoformat(entry["timestamp"]),
                direction=Direction(entry["direction"]),
                transport=Transport(entry["transport"]),
                raw=raw,
                jsonrpc_id=entry.get("jsonrpc_id"),
                method=entry.get("method"),
                correlated_id=entry.get("correlated_id"),
                modified=entry.get("modified", False),
                original_raw=original_raw,
            )
            store.append(msg)
        return store
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest tests/test_session_store.py -v`
Expected: All 8 tests PASS

### Step 5: Lint and type-check

Run: `uv run ruff check src/mcp_proxy/session_store.py tests/test_session_store.py && uv run ruff format --check src/mcp_proxy/session_store.py tests/test_session_store.py && uv run mypy src/mcp_proxy/session_store.py`
Expected: Clean

### Step 6: Commit

```
feat(session): add SessionStore with in-memory capture and JSON save/load
```

---

## Task 3: Pipeline — Message Forwarding with Correlation

**Files:**
- Create: `src/mcp_proxy/pipeline.py`
- Test: `tests/test_pipeline.py`

This is the largest task. The pipeline depends on both the intercept engine and session store from Tasks 1–2.

### Step 1: Write the failing tests for the pipeline

```python
# tests/test_pipeline.py
"""Tests for mcp_proxy.pipeline — run_pipeline and _forward_loop."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import (
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
)

from mcp_proxy.intercept import InterceptEngine
from mcp_proxy.models import (
    Direction,
    HeldMessage,
    InterceptAction,
    InterceptMode,
    ProxyMessage,
    Transport,
)
from mcp_proxy.pipeline import PipelineSession, run_pipeline
from mcp_proxy.session_store import SessionStore


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


class MockAdapter:
    """Mock TransportAdapter backed by asyncio.Queue.

    Pre-load messages into ``read_queue`` for the pipeline to consume.
    Inspect ``write_queue`` for messages the pipeline forwarded.
    """

    def __init__(self) -> None:
        self.read_queue: asyncio.Queue[SessionMessage | None] = asyncio.Queue()
        self.write_queue: asyncio.Queue[SessionMessage] = asyncio.Queue()
        self._closed = False

    async def read(self) -> SessionMessage:
        """Return next message, or raise when None (signals close)."""
        item = await self.read_queue.get()
        if item is None:
            raise Exception("Connection closed")
        return item

    async def write(self, message: SessionMessage) -> None:
        """Store forwarded message for test inspection."""
        await self.write_queue.put(message)

    async def close(self) -> None:
        """Mark as closed."""
        self._closed = True

    def enqueue(self, *messages: SessionMessage) -> None:
        """Pre-load messages and a close sentinel."""
        for msg in messages:
            self.read_queue.put_nowait(msg)
        self.read_queue.put_nowait(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(method: str = "tools/list", msg_id: int = 1) -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(JSONRPCRequest(jsonrpc="2.0", id=msg_id, method=method))
    )


def _make_response(msg_id: int = 1) -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(JSONRPCResponse(jsonrpc="2.0", id=msg_id, result={}))
    )


def _make_notification(method: str = "notifications/initialized") -> SessionMessage:
    return SessionMessage(
        message=JSONRPCMessage(JSONRPCNotification(jsonrpc="2.0", method=method))
    )


def _make_pipeline_session(**overrides: Any) -> PipelineSession:
    """Build a PipelineSession with sensible defaults."""
    defaults: dict[str, Any] = {
        "session_store": SessionStore(session_id=str(uuid.uuid4()), transport=Transport.STDIO),
        "intercept_engine": InterceptEngine(mode=InterceptMode.PASSTHROUGH),
        "transport": Transport.STDIO,
        "on_message": None,
        "on_held": None,
        "on_forwarded": None,
    }
    defaults.update(overrides)
    return PipelineSession(**defaults)


# ---------------------------------------------------------------------------
# Tests: Basic forwarding
# ---------------------------------------------------------------------------


class TestClientToServerForwarding:
    """Messages from client adapter reach server adapter."""

    async def test_single_request_forwarded(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()  # server sends nothing, closes immediately

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        forwarded = server.write_queue.get_nowait()
        assert forwarded.message.root.method == "tools/list"

    async def test_multiple_requests_forwarded_in_order(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req1 = _make_request("tools/list", msg_id=1)
        req2 = _make_request("tools/call", msg_id=2)
        client.enqueue(req1, req2)
        server.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        fwd1 = server.write_queue.get_nowait()
        fwd2 = server.write_queue.get_nowait()
        assert fwd1.message.root.method == "tools/list"
        assert fwd2.message.root.method == "tools/call"


class TestServerToClientForwarding:
    """Messages from server adapter reach client adapter."""

    async def test_response_forwarded(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        resp = _make_response(msg_id=1)
        server.enqueue(resp)
        client.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        forwarded = client.write_queue.get_nowait()
        assert forwarded.message.root.id == 1


class TestBidirectionalForwarding:
    """Messages flow both directions concurrently."""

    async def test_bidirectional(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        resp = _make_response(msg_id=1)
        client.enqueue(req)
        server.enqueue(resp)

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        server_got = server.write_queue.get_nowait()
        client_got = client.write_queue.get_nowait()
        assert server_got.message.root.method == "tools/list"
        assert client_got.message.root.id == 1


# ---------------------------------------------------------------------------
# Tests: ProxyMessage wrapping
# ---------------------------------------------------------------------------


class TestProxyMessageWrapping:
    """Messages are wrapped as ProxyMessage with correct metadata."""

    async def test_metadata_fields(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        messages = session.session_store.get_messages()
        assert len(messages) >= 1
        msg = messages[0]
        assert msg.direction == Direction.CLIENT_TO_SERVER
        assert msg.transport == Transport.STDIO
        assert msg.jsonrpc_id == 1
        assert msg.method == "tools/list"
        assert msg.modified is False
        assert msg.original_raw is None
        # UUID format
        uuid.UUID(msg.id)
        # Sequence starts at 0
        assert msg.sequence == 0

    async def test_sequence_monotonic(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req1 = _make_request("tools/list", msg_id=1)
        req2 = _make_request("tools/call", msg_id=2)
        client.enqueue(req1, req2)
        server.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        messages = session.session_store.get_messages()
        client_msgs = [m for m in messages if m.direction == Direction.CLIENT_TO_SERVER]
        assert len(client_msgs) == 2
        assert client_msgs[0].sequence < client_msgs[1].sequence


# ---------------------------------------------------------------------------
# Tests: Correlation
# ---------------------------------------------------------------------------


class TestCorrelation:
    """Request-response correlation by JSON-RPC id."""

    async def test_response_correlated_to_request(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=42)
        resp = _make_response(msg_id=42)
        client.enqueue(req)
        server.enqueue(resp)

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        messages = session.session_store.get_messages()
        request_msg = next(m for m in messages if m.method == "tools/list")
        response_msg = next(m for m in messages if m.method is None and m.jsonrpc_id == 42)
        assert response_msg.correlated_id == request_msg.id

    async def test_notification_not_correlated(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        notif = _make_notification("notifications/initialized")
        client.enqueue(notif)
        server.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)

        messages = session.session_store.get_messages()
        notif_msg = next(m for m in messages if m.method == "notifications/initialized")
        assert notif_msg.correlated_id is None


# ---------------------------------------------------------------------------
# Tests: Pipeline exits cleanly on adapter disconnect
# ---------------------------------------------------------------------------


class TestCleanShutdown:
    """Pipeline exits cleanly when an adapter raises."""

    async def test_exits_when_client_disconnects(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        client.enqueue()  # immediate close
        server.enqueue()

        session = _make_pipeline_session()
        # Should not raise
        await run_pipeline(client, server, session)

    async def test_exits_when_server_disconnects(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        client.enqueue()
        server.enqueue()

        session = _make_pipeline_session()
        await run_pipeline(client, server, session)


# ---------------------------------------------------------------------------
# Tests: Intercept mode
# ---------------------------------------------------------------------------


class TestInterceptHoldAndForward:
    """Intercept mode holds messages until released with FORWARD."""

    async def test_message_held_then_forwarded(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        held_messages: list[HeldMessage] = []

        def capture_held(held: HeldMessage) -> None:
            held_messages.append(held)
            # Auto-release after capture
            engine.release(held, InterceptAction.FORWARD)

        session = _make_pipeline_session(
            intercept_engine=engine,
            on_held=capture_held,
        )
        await run_pipeline(client, server, session)

        assert len(held_messages) == 1
        forwarded = server.write_queue.get_nowait()
        assert forwarded.message.root.method == "tools/list"


class TestInterceptDrop:
    """Intercept mode drops messages when released with DROP."""

    async def test_dropped_message_not_forwarded(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)

        def drop_all(held: HeldMessage) -> None:
            engine.release(held, InterceptAction.DROP)

        session = _make_pipeline_session(
            intercept_engine=engine,
            on_held=drop_all,
        )
        await run_pipeline(client, server, session)

        assert server.write_queue.empty()


class TestInterceptModify:
    """Intercept mode forwards modified payload when released with MODIFY."""

    async def test_modified_message_forwarded(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        modified_raw = JSONRPCMessage(
            JSONRPCRequest(jsonrpc="2.0", id=1, method="tools/call")
        )

        def modify_all(held: HeldMessage) -> None:
            engine.release(held, InterceptAction.MODIFY, modified_raw=modified_raw)

        session = _make_pipeline_session(
            intercept_engine=engine,
            on_held=modify_all,
        )
        await run_pipeline(client, server, session)

        forwarded = server.write_queue.get_nowait()
        assert forwarded.message.root.method == "tools/call"


# ---------------------------------------------------------------------------
# Tests: Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    """Pipeline callbacks fire at correct points."""

    async def test_on_message_fires(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        received: list[ProxyMessage] = []
        session = _make_pipeline_session(on_message=lambda m: received.append(m))
        await run_pipeline(client, server, session)

        assert len(received) >= 1
        assert received[0].method == "tools/list"

    async def test_on_forwarded_fires(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        forwarded: list[ProxyMessage] = []
        session = _make_pipeline_session(on_forwarded=lambda m: forwarded.append(m))
        await run_pipeline(client, server, session)

        assert len(forwarded) >= 1
        assert forwarded[0].method == "tools/list"

    async def test_on_held_fires_in_intercept_mode(self) -> None:
        client = MockAdapter()
        server = MockAdapter()
        req = _make_request("tools/list", msg_id=1)
        client.enqueue(req)
        server.enqueue()

        engine = InterceptEngine(mode=InterceptMode.INTERCEPT)
        held_notifications: list[HeldMessage] = []

        def on_held(held: HeldMessage) -> None:
            held_notifications.append(held)
            engine.release(held, InterceptAction.FORWARD)

        session = _make_pipeline_session(
            intercept_engine=engine,
            on_held=on_held,
        )
        await run_pipeline(client, server, session)

        assert len(held_notifications) == 1
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_proxy.pipeline'`

### Step 3: Write the pipeline implementation

```python
# src/mcp_proxy/pipeline.py
"""Core message pipeline for mcp-proxy.

Routes traffic between client and server transport adapters. Two
concurrent forward loops relay messages bidirectionally, wrapping
each in a ProxyMessage envelope, capturing to the session store,
and checking the intercept engine.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from mcp_proxy.adapters.base import TransportAdapter
from mcp_proxy.correlation import (
    extract_jsonrpc_id,
    extract_method,
    is_request,
    is_response,
)
from mcp_proxy.intercept import InterceptEngine
from mcp_proxy.models import (
    Direction,
    HeldMessage,
    InterceptAction,
    ProxyMessage,
    Transport,
)
from mcp_proxy.session_store import SessionStore


@dataclass
class PipelineSession:
    """Dependencies and callbacks for a pipeline run.

    Args:
        session_store: Captures all proxied messages.
        intercept_engine: Controls hold/release behavior.
        transport: The transport type for this session.
        on_message: Called when a message is received (before intercept).
        on_held: Called when a message is held by the intercept engine.
        on_forwarded: Called after a message is forwarded to its destination.
    """

    session_store: SessionStore
    intercept_engine: InterceptEngine
    transport: Transport
    on_message: Callable[[ProxyMessage], None] | None = None
    on_held: Callable[[HeldMessage], None] | None = None
    on_forwarded: Callable[[ProxyMessage], None] | None = None


async def run_pipeline(
    client_adapter: TransportAdapter,
    server_adapter: TransportAdapter,
    session: PipelineSession,
) -> None:
    """Run the bidirectional message pipeline.

    Launches two concurrent forward loops: client-to-server and
    server-to-client. Exits when either adapter disconnects.

    Args:
        client_adapter: The client-facing transport adapter.
        server_adapter: The server-facing transport adapter.
        session: Pipeline dependencies and callbacks.
    """
    seq = itertools.count()
    correlation_map: dict[str | int, str] = {}

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                _forward_loop(
                    source=client_adapter,
                    destination=server_adapter,
                    direction=Direction.CLIENT_TO_SERVER,
                    session=session,
                    seq=seq,
                    correlation_map=correlation_map,
                )
            )
            tg.create_task(
                _forward_loop(
                    source=server_adapter,
                    destination=client_adapter,
                    direction=Direction.SERVER_TO_CLIENT,
                    session=session,
                    seq=seq,
                    correlation_map=correlation_map,
                )
            )
    except* Exception:
        # One or both forward loops raised (adapter closed).
        # This is normal shutdown — both loops stop when either side disconnects.
        pass


async def _forward_loop(
    source: TransportAdapter,
    destination: TransportAdapter,
    direction: Direction,
    session: PipelineSession,
    seq: itertools.count[int],
    correlation_map: dict[str | int, str],
) -> None:
    """Forward messages from source to destination.

    Args:
        source: Adapter to read from.
        destination: Adapter to write to.
        direction: CLIENT_TO_SERVER or SERVER_TO_CLIENT.
        session: Pipeline dependencies and callbacks.
        seq: Shared monotonic sequence counter.
        correlation_map: Shared JSON-RPC id to ProxyMessage.id mapping.
    """
    while True:
        session_message = await source.read()
        proxy_msg = _wrap_message(session_message, direction, session.transport, seq)

        # Correlate responses to requests
        if is_request(session_message.message) and proxy_msg.jsonrpc_id is not None:
            correlation_map[proxy_msg.jsonrpc_id] = proxy_msg.id
        elif is_response(session_message.message) and proxy_msg.jsonrpc_id is not None:
            correlated = correlation_map.pop(proxy_msg.jsonrpc_id, None)
            if correlated is not None:
                proxy_msg.correlated_id = correlated

        # Capture
        session.session_store.append(proxy_msg)

        # Notify
        if session.on_message is not None:
            session.on_message(proxy_msg)

        # Intercept check
        if session.intercept_engine.should_hold(proxy_msg):
            held = session.intercept_engine.hold(proxy_msg)
            if session.on_held is not None:
                session.on_held(held)
            await held.release.wait()

            if held.action == InterceptAction.DROP:
                continue

            if held.action == InterceptAction.MODIFY and held.modified_raw is not None:
                session_message = SessionMessage(message=held.modified_raw)

        # Forward
        await destination.write(session_message)

        # Notify forwarded
        if session.on_forwarded is not None:
            session.on_forwarded(proxy_msg)


def _wrap_message(
    session_message: SessionMessage,
    direction: Direction,
    transport: Transport,
    seq: itertools.count[int],
) -> ProxyMessage:
    """Wrap a SessionMessage in a ProxyMessage envelope.

    Args:
        session_message: The raw SDK message.
        direction: CLIENT_TO_SERVER or SERVER_TO_CLIENT.
        transport: The transport type.
        seq: Shared monotonic sequence counter.

    Returns:
        A ProxyMessage with UUID, sequence, timestamp, and extracted fields.
    """
    raw = session_message.message
    return ProxyMessage(
        id=str(uuid.uuid4()),
        sequence=next(seq),
        timestamp=datetime.now(tz=UTC),
        direction=direction,
        transport=transport,
        raw=raw,
        jsonrpc_id=extract_jsonrpc_id(raw),
        method=extract_method(raw),
        correlated_id=None,
        modified=False,
        original_raw=None,
    )
```

### Step 4: Run tests to verify they pass

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: All 15 tests PASS

### Step 5: Lint and type-check

Run: `uv run ruff check src/mcp_proxy/pipeline.py tests/test_pipeline.py && uv run ruff format --check src/mcp_proxy/pipeline.py tests/test_pipeline.py && uv run mypy src/mcp_proxy/pipeline.py`
Expected: Clean

### Step 6: Commit

```
feat(pipeline): add message pipeline with correlation and intercept support
```

---

## Task 4: Full Verification and Final Commit

**Files:**
- All files from Tasks 1–3

### Step 1: Run full test suite

Run: `uv run pytest tests/ -q`
Expected: All tests PASS (existing + new)

### Step 2: Run linter on all source and test files

Run: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
Expected: Clean

### Step 3: Run type checker

Run: `uv run mypy src/`
Expected: Clean

### Step 4: CLI smoke test

Run: `uv run mcp-proxy --help`
Expected: Help output, exit 0

### Step 5: Push branch

```
git push -u origin feature/pipeline
```
