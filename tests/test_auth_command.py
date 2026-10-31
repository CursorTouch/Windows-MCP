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
