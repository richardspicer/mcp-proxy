"""Smoke test for mcp-proxy CLI."""

from click.testing import CliRunner

from mcp_proxy.cli import main


def test_cli_help():
    """CLI --help exits cleanly."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Interactive MCP traffic interceptor" in result.output


def test_proxy_help():
    """proxy --help exits cleanly."""
    runner = CliRunner()
    result = runner.invoke(main, ["proxy", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.output


def test_version():
    """--version exits cleanly."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
