# mcp-proxy

Interactive MCP traffic interceptor for security research. Sits between an MCP client and server, intercepting JSON-RPC messages for inspection, modification, and replay.

Part of the [CounterAgent](https://github.com/richardspicer/counteragent) research program.

## Status

**Pre-Alpha** — Under active development. Not yet functional.

## What It Does

mcp-proxy is a man-in-the-middle proxy for the [Model Context Protocol](https://modelcontextprotocol.io). It lets security researchers:

- **Intercept** live MCP traffic between clients (Claude, Cursor, etc.) and servers
- **Inspect** JSON-RPC messages with syntax highlighting and request-response correlation
- **Modify** tool call arguments before forwarding to the server
- **Replay** captured requests with modified payloads
- **Export** session captures as JSON evidence for bounty submissions

Think [Burp Suite](https://portswigger.net/burp) or [mitmproxy](https://mitmproxy.org/), but for MCP.

## Installation

```bash
pip install mcp-proxy
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uvx mcp-proxy --help
```

## Usage

```bash
# Proxy a stdio MCP server
mcp-proxy proxy --transport stdio --target-command "python my_server.py"

# Start in intercept mode (hold all messages for review)
mcp-proxy proxy --transport stdio --target-command "python my_server.py" --intercept

# Replay a saved session
mcp-proxy replay --session-file capture.json --target-command "python my_server.py"

# Export a session
mcp-proxy export --session-file capture.json --output results.json
```

## Transports

| Transport | Status |
|-----------|--------|
| stdio | Phase 1 (active) |
| SSE | Phase 2 (planned) |
| Streamable HTTP | Phase 2 (planned) |

## Documentation

- [Architecture](docs/Architecture.md) — Design, data models, component boundaries
- [Roadmap](docs/Roadmap.md) — Phased delivery plan

## Related Projects

- [mcp-audit](https://github.com/richardspicer/mcp-audit) — Automated MCP server security scanner (OWASP MCP Top 10)
- [CounterAgent](https://github.com/richardspicer/counteragent) — Program overview and roadmap

## AI-Assisted Development

This project uses a human-led, AI-augmented development workflow. See [AI-STATEMENT.md](AI-STATEMENT.md) for details.

## License

Apache 2.0 — See [LICENSE](LICENSE).

## Responsible Use

mcp-proxy is a security testing tool intended for authorized testing only. Only use it on systems you own, control, or have explicit permission to test. See [SECURITY.md](SECURITY.md) for trust boundaries and responsible use guidelines.
