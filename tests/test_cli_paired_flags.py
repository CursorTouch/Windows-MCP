from unittest.mock import MagicMock

from click.testing import CliRunner

import windows_mcp.__main__ as cli
from windows_mcp.__main__ import main


def _prepare_serve(monkeypatch):
    monkeypatch.setattr(
        cli.asyncio, "WindowsSelectorEventLoopPolicy", lambda: object(), raising=False
    )
    monkeypatch.setattr(cli.asyncio, "set_event_loop_policy", lambda _policy: None)
    monkeypatch.setattr(cli, "discover_config_path", lambda _path: None)
    monkeypatch.setattr(cli, "_run_server", lambda **_kwargs: None)


def test_ssl_certfile_without_keyfile_errors(monkeypatch, tmp_path):
    _prepare_serve(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--ssl-certfile", str(tmp_path / "cert.pem")])

    assert result.exit_code != 0
    assert result.output == "Error: --ssl-certfile and --ssl-keyfile must be provided together.\n"


def test_ssl_keyfile_without_certfile_errors(monkeypatch, tmp_path):
    _prepare_serve(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--ssl-keyfile", str(tmp_path / "key.pem")])

    assert result.exit_code != 0
    assert result.output == "Error: --ssl-certfile and --ssl-keyfile must be provided together.\n"


def test_oauth_client_id_without_secret_errors(monkeypatch):
    _prepare_serve(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--oauth-client-id", "abc"])

    assert result.exit_code != 0
    assert (
        result.output == "Error: OAuth requires both --oauth-client-id and --oauth-client-secret.\n"
    )


def test_oauth_client_secret_without_id_errors(monkeypatch):
    _prepare_serve(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--oauth-client-secret", "secret"])

    assert result.exit_code != 0
    assert (
        result.output == "Error: OAuth requires both --oauth-client-id and --oauth-client-secret.\n"
    )


def test_both_ssl_and_both_oauth_accepted(monkeypatch, tmp_path):
    _prepare_serve(monkeypatch)
    monkeypatch.setattr(cli, "_build_mcp", lambda: MagicMock())

    run_server_kwargs = {}

    def fake_run_server(**kwargs):
        run_server_kwargs.update(kwargs)

    oauth_route_kwargs = {}

    def fake_build_oauth_routes(**kwargs):
        oauth_route_kwargs.update(kwargs)
        return {}

    monkeypatch.setattr(cli, "_run_server", fake_run_server)
    monkeypatch.setattr(cli, "build_oauth_routes", fake_build_oauth_routes)

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("", encoding="utf-8")
    key.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "serve",
            "--transport",
            "sse",
            "--host",
            "0.0.0.0",
            "--port",
            "9123",
            "--auth-key",
            "token",
            "--ssl-certfile",
            str(cert),
            "--ssl-keyfile",
            str(key),
            "--oauth-client-id",
            "id",
            "--oauth-client-secret",
            "secret",
        ],
    )

    assert result.exit_code == 0, result.output
    assert run_server_kwargs["ssl_certfile"] == str(cert)
    assert run_server_kwargs["ssl_keyfile"] == str(key)
    assert run_server_kwargs["oauth_validator"] is not None
    assert oauth_route_kwargs["configured_client_id"] == "id"
    assert oauth_route_kwargs["configured_client_secret"] == "secret"
