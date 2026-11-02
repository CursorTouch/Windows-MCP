import codecs
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import windows_mcp.__main__ as cli
from windows_mcp.__main__ import main
from windows_mcp.infrastructure.config import load_config


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "config.toml")
    # Never run mkcert or openssl (or touch the certificate store) from a test.
    monkeypatch.setattr(cli, "_gen_tls", lambda host, cert_path, key_path: None)
    return tmp_path


def test_auth_with_tls_saves_a_config_that_loads(config_dir: Path) -> None:
    result = CliRunner().invoke(main, ["auth", "--transport", "streamable-http", "--with-tls"])

    assert result.exit_code == 0, result.output
    cfg = load_config(config_dir / "config.toml")
    assert cfg.server.ssl_certfile == str(config_dir / "cert.pem")
    assert cfg.server.ssl_keyfile == str(config_dir / "key.pem")


def test_auth_force_keeps_a_config_with_cert_files_loadable(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    config_file.write_text(
        '[server]\nauth_key = "old"\nssl_certfile = "cert.pem"\nssl_keyfile = "key.pem"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["auth", "--force", "--transport", "streamable-http"])

    assert result.exit_code == 0, result.output
    cfg = load_config(config_file)
    assert cfg.server.auth_key != "old"
    assert cfg.server.ssl_certfile == str(config_dir / "cert.pem")
    assert cfg.server.ssl_keyfile == str(config_dir / "key.pem")


def test_auth_reads_a_config_saved_with_a_utf8_bom(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    config_file.write_bytes(codecs.BOM_UTF8 + b'[control]\npolicy = "strict"\n')

    result = CliRunner().invoke(main, ["auth", "--transport", "streamable-http"])

    assert result.exit_code == 0, result.output
    cfg = load_config(config_file)
    assert cfg.server.auth_key
    assert cfg.control.policy == "strict"


@pytest.mark.parametrize(
    ("args", "url"),
    [
        # A wildcard bind address is not one to connect to; the URL uses loopback instead.
        ([], "http://127.0.0.1:8000/sse"),
        (["--host", "0.0.0.0", "--transport", "streamable-http"], "http://127.0.0.1:8000/mcp/"),
        (["--host", "::", "--transport", "streamable-http"], "http://[::1]:8000/mcp/"),
        # IPv6 literals need brackets in a URL.
        (["--host", "::1"], "http://[::1]:8000/sse"),
        (["--host", "2001:db8::5", "--port", "9000"], "http://[2001:db8::5]:9000/sse"),
        # An address given in brackets is printed as it is.
        (["--host", "[::1]"], "http://[::1]:8000/sse"),
        # Hostnames and specific IPv4 addresses are printed as given.
        (["--host", "127.0.0.1"], "http://127.0.0.1:8000/sse"),
        (["--host", "localhost", "--transport", "streamable-http"], "http://localhost:8000/mcp/"),
        (["--host", "192.168.1.20"], "http://192.168.1.20:8000/sse"),
        # With TLS, the URL names a host that the certificate covers.
        (["--transport", "streamable-http", "--with-tls"], "https://127.0.0.1:8000/mcp/"),
        (["--host", "::", "--with-tls"], "https://[::1]:8000/sse"),
    ],
)
def test_auth_prints_a_client_url_for_the_bind_host(
    config_dir: Path, args: list[str], url: str
) -> None:
    result = CliRunner().invoke(main, ["auth", *args])

    assert result.exit_code == 0, result.output
    assert f'"url": "{url}"' in result.output


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_auth_still_saves_the_wildcard_host_to_bind(config_dir: Path, host: str) -> None:
    result = CliRunner().invoke(main, ["auth", "--host", host])

    assert result.exit_code == 0, result.output
    assert load_config(config_dir / "config.toml").server.host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_gen_tls_covers_loopback_for_a_wildcard_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str
) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    # "where mkcert" succeeds, so this takes the mkcert path, but nothing is run.
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._gen_tls(host, tmp_path / "cert.pem", tmp_path / "key.pem")

    assert calls[-1][0] == "mkcert"
    assert calls[-1][-3:] == ["localhost", "127.0.0.1", "::1"]
