"""Diagnostics for proxy/VPN Fake-IP DNS answers blocked by SSRF protection."""

import socket

import pytest

from windows_mcp.infrastructure import validate_url


def _addrinfo(ip: str, family: int, port: int = 443) -> list[tuple]:
    if family == socket.AF_INET6:
        sockaddr = (ip, port, 0, 0)
    else:
        sockaddr = (ip, port)
    return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]


@pytest.mark.parametrize(
    ("address", "family"),
    [
        ("198.18.1.238", socket.AF_INET),
        ("2001:2::1", socket.AF_INET6),
    ],
)
def test_hostname_fake_ip_dns_answer_gets_actionable_diagnostic(
    monkeypatch, address: str, family: int
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo(address, family),
    )

    with pytest.raises(ValueError) as exc_info:
        validate_url("https://public.example/page")

    message = str(exc_info.value)
    assert address in message
    assert "Fake-IP DNS" in message
    assert "remains blocked by SSRF protection" in message
    assert "real public address" in message
    assert "exclude this hostname from Fake-IP mode" in message


def test_literal_benchmark_ip_stays_generic_block(monkeypatch) -> None:
    address = "198.18.1.238"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo(address, socket.AF_INET, 80),
    )

    with pytest.raises(ValueError) as exc_info:
        validate_url(f"http://{address}/page")

    message = str(exc_info.value)
    assert "addresses are blocked" in message
    assert "Fake-IP DNS" not in message


def test_other_private_dns_answer_stays_generic_block(monkeypatch) -> None:
    address = "10.0.0.5"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _addrinfo(address, socket.AF_INET),
    )

    with pytest.raises(ValueError) as exc_info:
        validate_url("https://internal.example/page")

    message = str(exc_info.value)
    assert "addresses are blocked" in message
    assert "Fake-IP DNS" not in message
