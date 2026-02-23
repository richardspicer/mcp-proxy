# mcp-proxy — Roadmap

## Problem Statement

When bounty hunting against a real MCP server, you need to see what the client
sends, what the server returns, and modify payloads on the fly. No existing tool
provides this for MCP.

**Burp Suite** sees HTTP but doesn't understand MCP JSON-RPC semantics or stdio
transport. **mcpwall** is a defensive stdio-only proxy (block/redact) with no
intercept/modify/replay. **MCP Inspector** is a visualization tool, not a testing
tool. **mitmproxy-mcp** exposes HTTP traffic analysis via MCP, but doesn't proxy
MCP traffic itself.

mcp-audit (Phase 1) handles automated scanning — predefined checks producing
OWASP-mapped findings. But automated scanners can't explore complex multi-step
JSON-RPC flows, test logic bugs, or craft targeted payloads interactively.
mcp-proxy fills that gap: the manual exploration tool that complements automated
scanning.

---

## Phased Delivery

### Phase 1: stdio Proxy with TUI + Replay

**Goal:** A fully interactive proxy for stdio transport — proxy, intercept,
modify, replay, and export, all through the Textual TUI. This is the complete
research workflow for the simplest transport.

**Deliverables:**
- Transport adapters: stdio client-facing and server-facing
- Message pipeline with bidirectional forwarding and request-response correlation
- ProxyMessage envelope with timestamps, direction, sequence, JSON-RPC correlation
- Session store: in-memory capture with save/load to JSON
- Passthrough mode: log everything, forward immediately
- Intercept mode: hold messages for user action (forward/modify/drop)
- Replay engine: re-send captured requests with modified arguments
- Textual TUI: live message stream, detail panel, intercept controls, filter,
  replay, session management (save/load/export)
- CLI (Click): `mcp-proxy proxy`, `replay`, `export`, `inspect`
- Integration tests against FastMCP fixture servers

**Done when:**
- Proxy sits between a real MCP client and a FastMCP fixture server over stdio
- All JSON-RPC messages pass through without corruption
- TUI displays live message traffic with direction indicators and syntax highlighting
- Intercept mode allows modifying a `tools/call` argument in the TUI before
  forwarding
- Replay sends a modified request and displays the before/after responses
- Session export produces valid JSON with full message history and correlation
- Tests pass on Windows and Linux

### Phase 2: SSE + Streamable HTTP

**Goal:** Extend proxy support to network transports. Same pipeline, same TUI,
new adapters.

**Deliverables:**
- Transport adapters: SSE client-facing (local server) and server-facing (SDK client)
- Transport adapters: Streamable HTTP client-facing and server-facing
- CLI flags: `--transport sse --target-url`, `--transport streamable-http --target-url`
- `--listen-port` for client-facing HTTP listener
- Integration tests for SSE and Streamable HTTP against fixture servers

**Done when:**
- Proxy works with SSE-based MCP servers (e.g., LibreChat MCP endpoints)
- Proxy works with Streamable HTTP servers
- All TUI and replay features work identically across transports
- No transport-specific logic leaks into the pipeline or TUI

### Phase 3: Polish + Release

**Goal:** Ready for public use by other security researchers.

**Deliverables:**
- README with install, usage, and examples
- CONTRIBUTING.md
- SECURITY.md with trust boundaries
- Pre-release security review (bandit, pip-audit)
- SARIF/structured evidence export for bounty submissions (optional)
- Conditional breakpoints: pause only on specific tools/methods (v2 enhancement)

**Done when:**
- `pip install mcp-proxy` (or `uvx mcp-proxy`) works out of the box
- A researcher who has never seen the tool can proxy a stdio MCP server, intercept
  a message, and export the session within 5 minutes of reading the README

---

## What Success Looks Like

- mcp-proxy intercepts live MCP traffic and allows modification of in-flight
  JSON-RPC messages across all three transports
- Replay mode produces evidence chains ("client sent X, I modified to Y, server
  returned Z") structured for bounty submissions
- At least one real vulnerability discovered or explored using mcp-proxy that
  mcp-audit alone couldn't find
- Blog post: "Manual MCP Security Testing: Finding Logic Bugs That Scanners Miss"

---

## Out of Scope (for now)

- **Multi-pair proxying** — proxying multiple concurrent client-server pairs in one
  instance. Single pair per instance for v1. Multi-pair is a future enhancement if
  real-world usage demands it.
- **TLS interception** — generating CA certs for HTTPS MITM. Most MCP servers in
  testing are localhost. If needed, use mitmproxy upstream for TLS termination.
- **Automated testing integration** — mcp-proxy is an interactive tool. Scriptable
  proxy automation (e.g., "run this modification on every 5th tool call") is
  interesting but out of scope for initial delivery.
- **SDK v2 migration** — build on v1.x now. Migrate transport adapters when v2
  stabilizes. The adapter interface isolates this change.
- **Shared library with mcp-audit** — no premature code extraction. If transport
  utilities overlap, extract after both tools are individually stable.
