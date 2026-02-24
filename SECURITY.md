# Security Policy

## Supported Versions

mcp-proxy is pre-release software under active development. Security
updates are applied to the latest version on `main` only.

| Version     | Supported          |
| ----------- | ------------------ |
| main (HEAD) | :white_check_mark: |
| < 1.0       | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in mcp-proxy, please report it
responsibly via [GitHub Private Vulnerability Reporting](https://github.com/richardspicer/mcp-proxy/security/advisories/new).

**Do not open a public issue for security vulnerabilities.**

You can expect an initial response within 7 days. We will work with you
to understand the issue and coordinate a fix before any public disclosure.

## Scope

mcp-proxy is an **offensive security research tool** designed to intercept
and modify MCP traffic. The following are expected behaviors, not
vulnerabilities:

- Man-in-the-middle interception of MCP connections
- Message modification and replay
- Session capture containing sensitive data

Vulnerabilities in scope include:

- Arbitrary code execution outside the intended proxy pipeline
- Path traversal in session file save/load
- Unintended network exposure (listening on non-localhost interfaces)
- Dependency vulnerabilities with exploitable attack paths
