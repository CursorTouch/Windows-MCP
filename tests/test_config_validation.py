from __future__ import annotations

import re
from pathlib import Path

import pytest

from windows_mcp.infrastructure.config import ServerConfig, load_config, write_config


@pytest.mark.parametrize(
    ("toml_text", "message"),
    [
        (
            "[server]\nhost = 80\n",
            "server.host must be a TOML string, not int",
        ),
        (
            '[server]\nhost = ["a"]\n',
            "server.host must be a TOML string, not list",
        ),
        (
            '[server]\nport = "8000"\n',
            "server.port must be a TOML integer, not str",
        ),
        (
            "[server]\nport = true\n",
            "server.port must be a TOML integer, not bool",
        ),
        (
            "[server]\nport = 70000\n",
            "server.port must be between 0 and 65535",
        ),
        (
            "[server]\nauth_key = 123\n",
            "server.auth_key must be a TOML string, not int",
        ),
        (
            "[server]\nssl_certfile = 123\n",
            "server.ssl_certfile must be a TOML string, not int",
        ),
        (
            "[server]\nssl_keyfile = 123\n",
            "server.ssl_keyfile must be a TOML string, not int",
        ),
        (
            "[server]\ntransport = 123\n",
            "server.transport must be a TOML string, not int",
        ),
        (
            "[security]\noauth_client_id = 7\n",
            "security.oauth_client_id must be a TOML string, not int",
        ),
        (
            "[security]\noauth_client_secret = 7\n",
            "security.oauth_client_secret must be a TOML string, not int",
        ),
    ],
)
def test_load_config_rejects_wrong_typed_fields(
    tmp_path: Path, toml_text: str, message: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(toml_text, encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(message)):
        load_config(path)


def test_valid_server_config_loads_and_round_trips(tmp_path: Path) -> None:
    text = '[server]\nhost = "127.0.0.1"\nport = 8080\nauth_key = "tok"\n'
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")

    cfg = load_config(path)

    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8080
    assert cfg.server.auth_key == "tok"

    out = tmp_path / "out.toml"
    write_config(cfg, out)
    assert out.read_text(encoding="utf-8") == text


def test_empty_server_section_loads_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\n", encoding="utf-8")

    cfg = load_config(path)

    assert cfg.server == ServerConfig()
