# mcp-proxy

Interactive MCP traffic interceptor for security research. Sits between an MCP client and server, intercepting JSON-RPC messages for inspection, modification, and replay.

Part of the [CounterAgent](https://github.com/richardspicer/counteragent) research program.

## Status

**Alpha** — Core functionality working for stdio transport. TUI, intercept, replay, and session capture are operational.

## What It Does

mcp-proxy is a man-in-the-middle proxy for the [Model Context Protocol](https://modelcontextprotocol.io). It lets security researchers:

- **Intercept** live MCP traffic between clients and servers
- **Inspect** JSON-RPC messages with metadata, request-response correlation, and payload detail
- **Modify** messages before forwarding — edit JSON payloads in-place
- **Replay** captured requests against a live server and compare responses
- **Filter** the message stream by method, direction, or content
- **Export** session captures as JSON evidence for bounty submissions

Think [Burp Suite](https://portswigger.net/burp) or [mitmproxy](https://mitmproxy.org/), but for MCP.

## Installation

Requires Python 3.11+.

```bash
# Clone and install in development mode
git clone https://github.com/richardspicer/mcp-proxy.git
cd mcp-proxy
uv sync --group dev
```

## Quick Start

```bash
# Proxy a stdio MCP server with the TUI
mcp-proxy proxy --transport stdio --target-command "uv run my_server.py"

# Start in intercept mode (hold all messages for review)
mcp-proxy proxy --transport stdio --target-command "uv run my_server.py" --intercept

# Auto-save the session on exit
mcp-proxy proxy --transport stdio --target-command "uv run my_server.py" --session-file capture.json
```

## TUI Keybindings

| Key | Action |
|-----|--------|
| `i` | Toggle intercept mode (passthrough ↔ intercept) |
| `f` / `F5` | Forward held message |
| `d` / `F8` | Drop held message |
| `m` / `F6` | Modify held message (opens editor) |
| `Ctrl+S` | Confirm edit |
| `Escape` | Cancel edit |
| `r` / `F9` | Replay selected message against server |
| `s` | Save session to file |
| `/` | Focus filter bar |
| `q` | Quit |

### Filtering

Press `/` to focus the filter bar. Type to filter the message list in real time:

- `tools` — show messages with "tools" in the method name
- `>` — show only client→server messages
- `<` — show only server→client messages
- `>tools` — client→server messages matching "tools"

## CLI Commands

```bash
# Replay a saved session against a live server
mcp-proxy replay --session-file capture.json --target-command "uv run my_server.py"

# Replay with custom timeout and JSON output
mcp-proxy replay --session-file capture.json --target-command "uv run my_server.py" \
  --timeout 5.0 --output replay-results.json

# Inspect a saved session (summary view)
mcp-proxy inspect --session-file capture.json

# Inspect with full JSON payloads
mcp-proxy inspect --session-file capture.json --verbose

# Export a session to a new file
mcp-proxy export --session-file capture.json --output copy.json
```

## Transports

| Transport | Status |
|-----------|--------|
| stdio | ✅ Working |
| SSE | Phase 2 (planned) |
| Streamable HTTP | Phase 2 (planned) |

## Research Workflow

The intended workflow for vulnerability research:

1. **Capture** — proxy a target MCP server, exercise its tools normally
2. **Intercept** — enable intercept mode, hold a tool call
3. **Modify** — edit the payload (inject paths, flags, oversized inputs)
4. **Forward** — send the modified message, observe the response
5. **Replay** — re-send the same payload, compare responses across server versions
6. **Export** — save the session as evidence for disclosure

## Documentation

- [Architecture](docs/Architecture.md) — Design, data models, component boundaries

## Related Projects

- [mcp-audit](https://github.com/richardspicer/mcp-audit) — Automated MCP server security scanner (OWASP MCP Top 10)
- [CounterAgent](https://github.com/richardspicer/counteragent) — Program overview and roadmap

## AI-Assisted Development

This project uses a human-led, AI-augmented development workflow. See [AI-STATEMENT.md](AI-STATEMENT.md) for details.

## License

Apache 2.0 — See [LICENSE](LICENSE).

## Responsible Use

mcp-proxy is a security testing tool intended for authorized testing only. Only use it on systems you own, control, or have explicit permission to test. See [SECURITY.md](SECURITY.md) for reporting guidelines and trust boundaries.
