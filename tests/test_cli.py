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


class TestProxyValidation:
    """proxy command validates transport/target combinations."""

    def test_stdio_requires_target_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["proxy", "--transport", "stdio"])
        assert result.exit_code != 0
        assert "target-command" in result.output.lower() or "required" in result.output.lower()

    def test_sse_requires_target_url(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["proxy", "--transport", "sse"])
        assert result.exit_code != 0
        assert "target-url" in result.output.lower() or "required" in result.output.lower()

    def test_streamable_http_requires_target_url(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["proxy", "--transport", "streamable-http"])
        assert result.exit_code != 0
        assert "target-url" in result.output.lower() or "required" in result.output.lower()
