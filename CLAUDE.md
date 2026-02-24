# mcp-proxy

Interactive MCP traffic interceptor for security research. Sits between an MCP client and server, intercepting JSON-RPC messages for inspection, modification, and replay. Part of the CounterAgent research program under richardspicer.io.

## Project Layout

```
src/mcp_proxy/
├── cli.py                    # Click CLI (proxy, replay, export, inspect)
├── app.py                    # Textual App — owns the event loop, launches pipeline
├── pipeline.py               # Core message forwarding loop (asyncio.TaskGroup)
├── models.py                 # ProxyMessage, ProxySession, Direction, Transport
├── intercept.py              # InterceptEngine, HeldMessage, InterceptAction
├── session_store.py          # In-memory capture, save/load JSON
├── replay.py                 # Replay engine — re-send modified messages
├── correlation.py            # JSON-RPC request-response ID correlation
├── adapters/
│   ├── base.py               # TransportAdapter protocol
│   ├── stdio.py              # stdio client-facing + server-facing adapters
│   ├── sse.py                # SSE adapters (Phase 2)
│   └── http.py               # Streamable HTTP adapters (Phase 2)
├── tui/
│   ├── messages.py           # Textual message types (MessageReceived, MessageHeld, etc.)
│   ├── message_list.py       # Scrollable message list widget
│   ├── detail_panel.py       # JSON-RPC payload viewer/editor
│   ├── controls.py           # Intercept controls, action buttons
│   └── filter_bar.py         # Real-time message filtering
└── exporting/
    └── json_export.py        # Session export to JSON
fixtures/                     # FastMCP fixture servers for integration tests
tests/
├── test_pipeline.py
├── test_intercept.py
├── test_session_store.py
├── test_replay.py
├── test_adapters/
│   └── test_stdio.py
└── test_tui/
```

## Architecture Summary

### SDK for Transport, Raw for Messages

Uses MCP Python SDK (`mcp` v1.x) transport functions (`stdio_client`, `sse_client`, `streamable_http_client`) for connection setup and framing. Operates on raw `JSONRPCMessage` objects — does NOT use `ClientSession`/`ServerSession` typed dispatch. The proxy forwards everything, including custom methods, protocol extensions, and malformed payloads.

### asyncio Primary, anyio at the SDK Boundary

Textual owns the asyncio event loop. **All application code uses asyncio primitives** (`asyncio.Queue`, `asyncio.Event`, `asyncio.TaskGroup`). anyio appears ONLY inside transport adapters where the SDK returns `MemoryObjectStream` pairs. Adapters translate anyio streams → asyncio queues. Nothing above the adapter layer touches anyio.

```
Textual App (asyncio event loop)
  └── Pipeline Worker (Textual Worker, background async task)
       ├── Client Adapter (anyio streams from SDK → asyncio.Queue)
       └── Server Adapter (anyio streams from SDK → asyncio.Queue)
```

### Message Flow

```
Client → [Client Adapter] → asyncio.Queue → [Pipeline] → asyncio.Queue → [Server Adapter] → Server
                                                │
                                          InterceptEngine
                                          (asyncio.Event)
                                                │
                                          Textual Messages
                                          (post_message)
                                                │
                                              TUI
```

Pipeline runs two concurrent `_forward_loop` tasks (client→server, server→client). Each message gets wrapped in a `ProxyMessage` envelope, stored, checked against the intercept engine, and forwarded.

### TUI-Pipeline Communication

- **Pipeline → TUI:** `app.post_message(MessageReceived(...))`, `app.post_message(MessageHeld(...))`
- **TUI → Pipeline:** User actions written to `asyncio.Queue`, consumed by intercept engine to release held messages

## Code Standards

- **CLI framework:** Click (not Typer, not argparse). Explicit decorators, no magic.
- **TUI framework:** Textual 8.x. CSS-like styling via `.tcss` files.
- **Async:** asyncio stdlib. No anyio outside of adapters. No trio.
- **Docstrings:** Google-style on all public functions and classes (Args, Returns, Raises, Example)
- **Type hints:** Required on all function signatures
- **Line length:** 100 chars (ruff)
- **Imports:** Sorted by ruff (isort rules)
- **Models:** Pydantic for serialized types (ProxySession export). dataclass for internal types.

## Testing

- Framework: pytest + pytest-asyncio (asyncio_mode = "auto")
- Fixture servers: FastMCP-based servers in `fixtures/`
- Integration tests: proxy traffic through fixture servers, verify message integrity
- Unit tests: pipeline logic, intercept engine, correlation, session store
- TUI tests: use Textual's `App.run_test()` / Pilot API for widget interaction testing
- **All tests must pass before committing**

Run tests:
```
uv run pytest -q
```

Smoke test:
```
mcp-proxy --help
```

## Git Workflow

**Never commit directly to main.** Branch protection enforced.

```
git checkout main && git pull
git checkout -b feature/description    # or fix/, docs/, refactor/
# ... work ...
uv run pytest -q                       # all tests pass
mcp-proxy --help                       # CLI smoke test
git add .
git commit -F .commitmsg               # see shell quoting note below
git push -u origin feature/description
# Create PR on GitHub, merge after CI passes
```

### Shell Quoting (CRITICAL)

The CMD shell corrupts `git commit -m "message with spaces"`. Always use:
```
echo "feat: description here" > .commitmsg
git commit -F .commitmsg
rm .commitmsg
```

This applies to any shell command where arguments contain spaces, commas, or parentheses.

### End of Session

Commit to branch, `git stash -m "description"`, or `git restore .` — never leave uncommitted changes.

## Pre-commit Hooks

Hooks run automatically on `git commit`:
- trailing-whitespace, end-of-file-fixer, check-yaml, check-toml
- check-added-large-files, check-merge-conflict
- **no-commit-to-branch** (blocks direct commits to main)
- **ruff-check** (lint + auto-fix) + **ruff-format**
- **gitleaks** (secrets detection)
- **mypy** (type checking)

If pre-commit fails, fix issues and re-stage before committing.

## Dependencies

Managed via `uv` with `pyproject.toml`. Sync with:
```
uv sync --group dev
```

**Without `--group dev`, dev dependencies get stripped.**

Core dependencies:
- `mcp` (SDK v1.x) — transport functions, JSON-RPC types
- `click` — CLI
- `textual` — TUI
- `pydantic` — serialization
- `starlette` + `uvicorn` — SSE/HTTP client-facing adapters (Phase 2)

## Key Patterns to Follow

- **ProxyMessage** wraps every JSON-RPC message with: id, sequence, timestamp, direction, transport, raw payload, correlation
- **HeldMessage** pairs a ProxyMessage with an `asyncio.Event` for release signaling
- **TransportAdapter** protocol: `read()`, `write()`, `close()` — pipeline never sees transport details
- Adapters translate anyio `MemoryObjectStream` → `asyncio.Queue`. This is the ONLY place anyio appears.
- Session files are JSON — the unit of evidence for bounty submissions
- Request-response correlation by JSON-RPC `id` field

- **`docs/Architecture.md`:** Update at end of session if new modules, endpoints, or data models were introduced

## CLI Usage

```powershell
mcp-proxy proxy --transport stdio --target-command "python server.py"
mcp-proxy proxy --transport stdio --target-command "python server.py" --intercept
mcp-proxy replay --session-file capture.json --target-command "python server.py"
mcp-proxy export --session-file capture.json --output results.json
mcp-proxy inspect --session-file capture.json
```

After changes, smoke test: `mcp-proxy --help`

## Cross-Platform Compatibility

Must run on Windows, macOS, and Linux out of the box.

- **No platform-specific shell commands** in source code or test fixtures
- **Paths:** Use `pathlib.Path` or `os.path`. Never hardcode separators.
- **Subprocess calls:** Use list form over shell strings
- **Test suite:** Must pass on all platforms. Mark platform-specific tests with `@pytest.mark.skipif`
- **CI matrix:** Test on `ubuntu-latest` and `windows-latest` at minimum

## Claude Code Guardrails

### Verification Scope
- Run only the tests for new/changed code, not the full suite
- Smoke test the CLI after changes
- Full suite verification is the developer's responsibility before merging

### Timeout Policy
- If any test run exceeds 60 seconds, stop and identify the stuck test
- Do not set longer timeouts and wait — diagnose instead

### Process Hygiene
- Before running tests, kill any orphaned python/node processes from previous runs
- After killing a stuck process, clean up zombies before retrying
- Textual TUI tests may leave orphaned processes — always clean up

### Failure Mode
- If verification hits a problem you can't resolve in 2 attempts, commit the work to the branch and report what failed
- Do not spin on the same failure

### Boundaries
- Do not create PRs. Push the branch and stop. The developer creates PRs manually.
- Do not attempt to install CLI tools (gh, hub, etc.)
- Do not create implementation plan files, design docs, or any docs/plans/ directory in the repo. NEVER commit plan files. If subagent-driven development requires a plan file, write it to the system temp directory (e.g., `$TEMP/mcp-proxy-plans/`), not the repo. Plans are transient session artifacts, not project documentation.

## Legal & Ethical

- Only test systems you own, control, or have explicit permission to test
- Responsible disclosure for all vulnerabilities — never publish exploits before vendor notification
- Frame all tooling as defensive security testing tools (analogous to Burp Suite, mitmproxy)
