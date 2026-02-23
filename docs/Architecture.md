# mcp-proxy — Design

## Architecture

mcp-proxy is a man-in-the-middle proxy that sits between an MCP client and server,
intercepting JSON-RPC traffic for inspection, modification, and replay. It operates
at the transport/message layer — not the semantic layer. It forwards what it sees
without interpreting tool semantics, which means it handles any valid (or invalid)
JSON-RPC message the server or client sends.

### Design Principle: SDK for Transport, Raw for Messages

mcp-proxy uses the MCP Python SDK (`mcp` v1.x) transport-level functions for
connection setup and stream framing, but operates on raw `JSONRPCMessage` objects
rather than routing through `ClientSession`/`ServerSession` typed dispatch.

**Why hybrid, not pure SDK:**
- SDK transport functions (`stdio_client`, `sse_client`, `streamable_http_client`)
  handle subprocess management, SSE stream parsing, and HTTP session setup — no
  reason to reimplement these.
- SDK's `ClientSession`/`ServerSession` dispatch messages through typed handlers
  (`tools/call` → `call_tool()`, etc.). A proxy must forward messages it doesn't
  understand (custom methods, protocol extensions, malformed payloads for testing).
  Session dispatch would reject or misroute these.
- The proxy reads `SessionMessage` objects from one stream pair and writes to
  another, with an interception layer in between. This is a pipeline, not a
  client-server conversation.

**Why not pure raw:**
- stdio transport requires subprocess lifecycle management (spawn, pipe setup,
  signal handling, cleanup). The SDK handles this correctly cross-platform.
- SSE transport requires event stream parsing and reconnection logic.
- Streamable HTTP transport requires session ID management and request correlation.
- Reimplementing these is wasted effort with high bug surface.

**SDK v2 risk mitigation:** The SDK v2 (anticipated Q1 2026) will change the
transport layer. mcp-proxy isolates SDK usage behind transport adapter interfaces,
so v2 migration only touches adapter implementations, not the core pipeline.

### Design Principle: asyncio Primary, anyio at the SDK Boundary

Textual owns the asyncio event loop. The proxy pipeline, intercept engine, session
store, and all application logic use asyncio primitives (`asyncio.Queue`,
`asyncio.Event`, `asyncio.TaskGroup`). anyio appears only inside transport adapters
where the SDK returns `MemoryObjectStream` pairs.

**Why not anyio everywhere:**
- Textual is asyncio-native and owns the event loop. Mixing anyio task groups into
  Textual workers adds an unnecessary abstraction layer.
- asyncio is stdlib — contributors only need to know standard Python async patterns.
- anyio's value (backend portability between asyncio and trio) is irrelevant here.
  mcp-proxy will always run on asyncio because Textual requires it.

**How the boundary works:** Transport adapters read `SessionMessage` objects from
anyio `MemoryObjectStream` (returned by SDK transport functions) and push them into
`asyncio.Queue` instances that the pipeline consumes. The adapter is the translation
layer. Everything above the adapter is pure asyncio.

```python
# Inside a transport adapter — the only place anyio streams appear
async def _reader_loop(self) -> None:
    """Read from SDK anyio stream, push to asyncio queue."""
    async for message in self._sdk_read_stream:
        await self._output_queue.put(message)
```

```
┌──────────────┐         ┌──────────────────────────────────────┐         ┌──────────────┐
│  MCP Client  │         │             mcp-proxy                │         │  MCP Server   │
│  (Claude,    │  stdio  │  ┌────────┐  ┌─────────┐  ┌──────┐ │  stdio  │  (target      │
│   Cursor,    │◄───────►│  │Client  │  │Message  │  │Server│ │◄───────►│   server)     │
│   etc.)      │  SSE    │  │Adapter │◄►│Pipeline │◄►│Adapt.│ │  SSE    │               │
│              │  HTTP   │  └────────┘  └─────────┘  └──────┘ │  HTTP   │               │
└──────────────┘         │       ▲      ▲    │    ▲            │         └──────────────┘
                         │       │      │    ▼    │            │
                         │       │  ┌─────────┐   │            │
                         │       │  │Intercept│   │            │
                         │       │  │Engine   │   │            │
                         │       │  └─────────┘   │            │
                         │       │      │    ▲    │            │
                         │       │      ▼    │    │            │
                         │  ┌────────┐ ┌─────────┐ ┌────────┐ │
                         │  │Session │ │ Replay  │ │Textual │ │
                         │  │Store   │ │ Engine  │ │  TUI   │ │
                         │  └────────┘ └─────────┘ └────────┘ │
                         └──────────────────────────────────────┘
```

### Component Descriptions

**Client Adapter** — Presents as an MCP server to the real MCP client. For stdio,
this means the proxy *is* the subprocess the client launches. For SSE/HTTP, the
proxy listens on a local port. Receives `JSONRPCMessage` objects from the client
and feeds them into the message pipeline via `asyncio.Queue`. Delivers server
responses back to the client.

**Server Adapter** — Connects to the real MCP server as a client. For stdio, spawns
the server subprocess. For SSE/HTTP, connects to the server's endpoint. Forwards
messages from the pipeline to the server and delivers responses back.

**Message Pipeline** — The core routing layer. Every message passes through here.
The pipeline:
1. Receives a message from either adapter (via `asyncio.Queue`)
2. Wraps it in a `ProxyMessage` envelope with timestamp, direction, and sequence ID
3. Writes it to the session store (logging)
4. Posts a Textual message (`MessageReceived`) for the TUI
5. Checks intercept engine for breakpoints
6. If intercepted: pauses, waits for user action via `asyncio.Event` (forward/modify/drop)
7. If not intercepted: forwards immediately to the other adapter
8. Correlates request/response pairs by JSON-RPC `id`

**Intercept Engine** — Manages breakpoint state. In passthrough mode, all messages
flow without pausing. In intercept mode, messages are held until the user acts.
v1 implements global intercept (all messages pause). Conditional breakpoints
(pause only on specific tools/methods) are a v2 enhancement. Uses `asyncio.Event`
for hold/release signaling between the pipeline and TUI.

**Session Store** — Captures all messages in a session as an ordered sequence of
`ProxyMessage` objects. Supports save/load to disk (JSON). A session starts when
the proxy launches and ends when it shuts down. Sessions are the unit of evidence
for bounty submissions.

**Replay Engine** — Takes a captured `ProxyMessage` (or a user-modified copy) and
re-sends it to the server adapter. The server's response is captured as a new
message in the session. Replay operates outside the normal pipeline flow — it
injects messages directly into the server adapter and captures responses.

**TUI (Textual)** — The primary user interface. The Textual `App` owns the asyncio
event loop. The proxy pipeline runs as a Textual `Worker` (background async task).
The TUI displays the live message stream, provides controls for
intercept/forward/modify/replay, and exposes session management (save, load,
export, filter).

Communication between the pipeline and TUI uses Textual's message system:
- Pipeline → TUI: post `MessageReceived`, `MessageForwarded` messages
- TUI → Pipeline: write user actions to `asyncio.Queue` consumed by intercept engine

```
┌─────────────────────────────────────────────────┐
│  Textual App (owns asyncio event loop)          │
│                                                 │
│  ┌──────────────────┐  ┌─────────────────────┐  │
│  │  TUI Widgets     │  │  Pipeline Worker    │  │
│  │  - Message list  │  │  (Textual Worker)   │  │
│  │  - Detail panel  │  │                     │  │
│  │  - Controls      │◄─┤  post_message()     │  │
│  │  - Filter bar    │  │  (MessageReceived)  │  │
│  │                  │──►                     │  │
│  │  (user actions)  │  │  asyncio.Queue      │  │
│  │                  │  │  (intercept cmds)   │  │
│  └──────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────┘
```

---

## Data Models

### ProxyMessage

The core envelope wrapping every intercepted JSON-RPC message.

```python
@dataclass
class ProxyMessage:
    """A single intercepted MCP JSON-RPC message with proxy metadata."""

    id: str                          # Unique proxy-assigned ID (UUID)
    sequence: int                    # Monotonic sequence number within session
    timestamp: datetime              # When the proxy received this message
    direction: Direction             # CLIENT_TO_SERVER or SERVER_TO_CLIENT
    transport: Transport             # STDIO, SSE, or STREAMABLE_HTTP
    raw: JSONRPCMessage              # The actual JSON-RPC message (SDK type)
    jsonrpc_id: str | int | None     # JSON-RPC id field (None for notifications)
    method: str | None               # JSON-RPC method (None for responses)
    correlated_id: str | None        # proxy ID of the request this responds to
    modified: bool                   # True if user modified before forwarding
    original_raw: JSONRPCMessage | None  # Pre-modification snapshot (if modified)


class Direction(str, Enum):
    CLIENT_TO_SERVER = "client_to_server"
    SERVER_TO_CLIENT = "server_to_client"


class Transport(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"
```

### ProxySession

The container for a complete proxy session.

```python
@dataclass
class ProxySession:
    """A captured proxy session — the unit of evidence."""

    id: str                          # Session UUID
    started_at: datetime
    ended_at: datetime | None
    transport: Transport
    server_command: str | None       # For stdio: the command that launched the server
    server_url: str | None           # For SSE/HTTP: the server endpoint
    messages: list[ProxyMessage]     # Ordered message sequence
    metadata: dict[str, Any]         # Arbitrary session metadata (target name, notes)
```

### InterceptState

```python
@dataclass
class InterceptState:
    """Current state of the intercept engine."""

    mode: InterceptMode              # PASSTHROUGH or INTERCEPT
    held_messages: list[HeldMessage] # Messages waiting for user action


class InterceptMode(str, Enum):
    PASSTHROUGH = "passthrough"      # Forward all messages immediately
    INTERCEPT = "intercept"          # Hold all messages for user review


@dataclass
class HeldMessage:
    """A message held by the intercept engine, awaiting user action."""

    proxy_message: ProxyMessage
    release: asyncio.Event           # Set when user acts (forward/modify/drop)
    action: InterceptAction | None   # Populated by TUI before setting release
    modified_raw: JSONRPCMessage | None  # If action is MODIFY, the edited message


class InterceptAction(str, Enum):
    FORWARD = "forward"              # Send as-is
    MODIFY = "modify"                # Send with modifications
    DROP = "drop"                    # Don't forward
```

---

## Transport Adapter Design

Each transport requires a matched pair of adapters: one client-facing (proxy acts
as server) and one server-facing (proxy acts as client).

### stdio

**Server-facing (proxy → real server):** Use SDK's `stdio_client()` context
manager. It spawns the subprocess, sets up pipes, returns `(read_stream,
write_stream)` as anyio `MemoryObjectStream` pairs. The adapter reads from the
anyio stream and pushes into an `asyncio.Queue`.

**Client-facing (real client → proxy):** The proxy itself IS the stdio subprocess
from the client's perspective. The client's MCP config points to `mcp-proxy` as
the command. The proxy reads from its own `stdin` and writes to `stdout`. This
requires implementing a minimal stdio server transport that exposes an
`asyncio.Queue` interface matching the server-facing adapter.

**stdio lifecycle:**
```
Client config:  {"command": "mcp-proxy", "args": ["--target-command", "python server.py"]}

Client ──stdin/stdout──► mcp-proxy ──stdin/stdout──► python server.py
                         (reads stdin,               (real MCP server)
                          writes stdout)
```

### SSE

**Server-facing:** Use SDK's `sse_client()` to connect to the real server's SSE
endpoint.

**Client-facing:** Run a local SSE server (Starlette + uvicorn) that the client
connects to. The proxy's SSE endpoint URL is what the client configures.

### Streamable HTTP

**Server-facing:** Use SDK's `streamable_http_client()` to connect to the real
server.

**Client-facing:** Run a local HTTP server that accepts Streamable HTTP connections
from the client.

### Adapter Interface

All adapters implement the same async interface using asyncio primitives:

```python
class TransportAdapter(Protocol):
    """Interface for transport adapters.

    Adapters translate between SDK anyio streams and asyncio queues.
    The pipeline only sees this interface — never anyio streams directly.
    """

    async def read(self) -> SessionMessage:
        """Read the next message from this side of the connection."""
        ...

    async def write(self, message: SessionMessage) -> None:
        """Write a message to this side of the connection."""
        ...

    async def close(self) -> None:
        """Shut down this side of the connection."""
        ...
```

The message pipeline doesn't know or care which transport is in use.

---

## Message Pipeline Detail

The pipeline is the central async loop. It runs two concurrent tasks within a
Textual `Worker`:

1. **Client→Server loop:** Read from client adapter → process → write to server adapter
2. **Server→Client loop:** Read from server adapter → process → write to client adapter

```python
async def run_pipeline(
    client_adapter: TransportAdapter,
    server_adapter: TransportAdapter,
    session_store: SessionStore,
    intercept_engine: InterceptEngine,
    app: App,
) -> None:
    """Main proxy pipeline — runs until either side disconnects."""
    async with asyncio.TaskGroup() as tg:
        tg.create_task(
            _forward_loop(
                client_adapter, server_adapter,
                Direction.CLIENT_TO_SERVER, session_store, intercept_engine, app
            )
        )
        tg.create_task(
            _forward_loop(
                server_adapter, client_adapter,
                Direction.SERVER_TO_CLIENT, session_store, intercept_engine, app
            )
        )
```

Each `_forward_loop`:
1. `message = await source.read()`
2. `proxy_msg = wrap_message(message, direction, session)`
3. `session_store.append(proxy_msg)`
4. `app.post_message(MessageReceived(proxy_msg))` — TUI updates
5. If `intercept_engine.should_hold(proxy_msg)`:
   - `held = intercept_engine.hold(proxy_msg)` — creates HeldMessage with asyncio.Event
   - `app.post_message(MessageHeld(held))` — TUI shows held message
   - `await held.release.wait()` — blocks until user acts
   - If action is FORWARD: continue with original message
   - If action is MODIFY: replace message with `held.modified_raw`
   - If action is DROP: skip forwarding, continue loop
6. `await destination.write(message)`
7. `app.post_message(MessageForwarded(proxy_msg))`

### Request-Response Correlation

JSON-RPC requests carry an `id` field. Responses carry the same `id`. The pipeline
maintains a `dict[str | int, str]` mapping JSON-RPC `id` → `ProxyMessage.id` so
that when a response arrives, its `correlated_id` can be set to point back at the
original request. Notifications (no `id`) are not correlated.

---

## CLI Interface

CLI uses Click — a minimal launch ramp into the TUI. Click is explicit
(decorator-based, no magic), battle-tested, and from the same ecosystem as
Textual (Textualize's Trogon bridges Click CLIs into Textual TUIs).

```
mcp-proxy
  proxy     Start the proxy with TUI (primary command)
  replay    Replay a saved session against a live server
  export    Export a session to JSON
  inspect   Print session contents to stdout (non-interactive)
```

### Primary command: `proxy`

```
mcp-proxy proxy --transport stdio --target-command "python server.py"
mcp-proxy proxy --transport sse --target-url http://localhost:8080/sse
mcp-proxy proxy --transport streamable-http --target-url http://localhost:8080/mcp
```

Launches the Textual TUI with the proxy pipeline running as a background worker.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--transport` | Choice | required | `stdio`, `sse`, `streamable-http` |
| `--target-command` | String | — | Server command (stdio only) |
| `--target-url` | String | — | Server URL (SSE/HTTP only) |
| `--intercept` | Bool | `false` | Start in intercept mode (hold all messages) |
| `--listen-port` | Int | `8888` | Local port for SSE/HTTP client-facing adapter |
| `--session-file` | Path | — | Auto-save session to this file |
| `--output-format` | Choice | `json` | Export format for session files |

---

## TUI Layout

The TUI uses Textual widgets arranged in a split-pane layout:

```
┌─────────────────────────────────────────────────────────────┐
│  mcp-proxy — stdio — python server.py          [INTERCEPT]  │
├───────────────────────────────────┬─────────────────────────┤
│  Message List                     │  Message Detail          │
│  ─────────────────────────────    │  ─────────────────────   │
│  ► #001 → initialize              │  {                       │
│  ◄ #002 ← initialize (response)  │    "jsonrpc": "2.0",    │
│  ► #003 → tools/list              │    "id": 1,             │
│  ◄ #004 ← tools/list (response)  │    "method": "tools/c…" │
│  ► #005 → tools/call [read_file]  │    "params": {          │
│  ◄ #006 ← tools/call (response)  │      "name": "read_fi…" │
│  ⏸ #007 → tools/call [exec_cmd]  │      "arguments": {     │
│                                   │        "command": "ls"   │
│                                   │      }                   │
│                                   │    }                     │
│                                   │  }                       │
├───────────────────────────────────┴─────────────────────────┤
│  [Filter: ___________]  [Passthrough] [Intercept]           │
│  [Forward] [Modify] [Drop] [Replay]  [Save] [Load] [Export] │
├─────────────────────────────────────────────────────────────┤
│  F1:Help  F2:Filter  F5:Forward  F6:Modify  F8:Drop  Q:Quit│
└─────────────────────────────────────────────────────────────┘
```

**Message List** — Scrollable list of all `ProxyMessage` entries. Direction
indicators (► client→server, ◄ server→client). Held messages show ⏸ icon.
Color-coded by direction. Selecting a message shows its detail.

**Message Detail** — Full JSON-RPC payload of the selected message, syntax
highlighted. For held messages, this panel becomes editable (modify mode).

**Control Bar** — Intercept mode toggle, action buttons for held messages
(forward/modify/drop), replay trigger, session management (save/load/export).

**Filter Bar** — Text filter by tool name, JSON-RPC method, or content pattern.
Filters the message list in real-time without affecting the session store.

**Status Bar** — Key bindings and current state.

---

## Extension Points

### Adding a New Transport

1. Implement `TransportAdapter` for both client-facing and server-facing sides
2. Register in transport factory with a new `Transport` enum value
3. Add CLI flag support in `cli.py`
4. Add integration test with fixture server using the new transport

The message pipeline requires zero changes — it only interacts with the
`TransportAdapter` interface.

### Adding Intercept Conditions (v2)

1. Define a `Breakpoint` dataclass with match criteria (tool name, method, content pattern)
2. Extend `InterceptEngine.should_hold()` to evaluate breakpoints
3. Add TUI controls for creating/managing breakpoints
4. Add CLI flags for pre-configured breakpoints

### Adding Export Formats

1. Create a new exporter in `exporting/` implementing the `SessionExporter` protocol
2. Register in exporter factory
3. Add `--output-format` option value

---

## Security Considerations

mcp-proxy is an offensive security research tool. It intentionally performs MITM
on MCP connections. This creates responsibilities:

- **Only proxy connections you own or have permission to test.** The proxy does not
  phone home, collect telemetry, or transmit data. All data stays local.
- **Session files may contain sensitive data** — tool arguments, server responses,
  authentication tokens. Session files should be treated as sensitive artifacts.
- **The proxy's client-facing adapters listen on localhost only** (for SSE/HTTP).
  No remote connections accepted by default.
- **No credential extraction or storage.** If auth tokens pass through the proxy,
  they're logged in the session like any other message content. The proxy does not
  parse, extract, or persist credentials separately.

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| CLI framework | Click | Explicit, battle-tested, Textual ecosystem (Trogon) |
| TUI framework | Textual 8.x | Terminal UI for researchers, cross-platform, asyncio-native |
| Async runtime | asyncio (stdlib) | Textual owns the event loop; anyio only at SDK boundary |
| MCP transport | mcp SDK v1.x | Transport setup and framing only (anyio streams) |
| JSON-RPC types | mcp SDK types | `JSONRPCMessage`, `SessionMessage` — no reimplementation |
| HTTP server (SSE/HTTP adapters) | Starlette + uvicorn | Lightweight ASGI, SDK examples use Starlette |
| Serialization | Pydantic | Session export, config, consistent with SDK types |
| Testing | pytest + pytest-asyncio | Consistent with mcp-audit |

---

## Documentation Standards

- Google-style docstrings (Args, Returns, Raises, Example)
- New modules get docstrings when created, not retrofitted
- Inline comments for non-obvious logic only
