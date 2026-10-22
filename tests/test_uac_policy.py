import pytest

from windows_mcp.desktop import service as desktop_service
from windows_mcp.service import policy, secure_desktop
from windows_mcp.service import pipe


def test_extract_target_path_from_utf16_buffer():
    path = r"C:\Windows\system32\notepad.exe"
    data = (
        b"\x01\x02\x03\x04"
        + path.encode("utf-16le")
        + b"\x00\x00"
        + f'"{path}" '.encode("utf-16le")
    )

    assert secure_desktop._extract_target_path(data, exists=lambda _: True) == path


def test_extract_target_path_requires_existing_file():
    path = r"C:\Windows\system32\notepad.exe"
    data = path.encode("utf-16le")

    assert secure_desktop._extract_target_path(data, exists=lambda _: False) is None


def test_extract_target_path_ignores_non_path_strings():
    data = "Verified publisher: Microsoft Windows".encode("utf-16le")

    assert secure_desktop._extract_target_path(data, exists=lambda _: True) is None


def test_route_click_through_host_raises_for_policy_denial(monkeypatch):
    class FakeClient:
        def is_available(self):
            return True

        def uia_click_at(self, x, y):
            raise RuntimeError(
                "Host service error (uia_click_at): policy denied: policy=block"
            )

    monkeypatch.setattr(pipe, "get_client", lambda: FakeClient())

    with pytest.raises(PermissionError, match="policy denied") as exc_info:
        desktop_service._route_click_through_host(10, 20)

    assert "human must click the UAC dialog" in str(exc_info.value)


def test_route_click_through_host_falls_back_for_other_errors(monkeypatch):
    class FakeClient:
        def is_available(self):
            return True

        def uia_click_at(self, x, y):
            raise RuntimeError("Pipe I/O error")

    monkeypatch.setattr(pipe, "get_client", lambda: FakeClient())

    assert desktop_service._route_click_through_host(10, 20) is False


def test_allow_with_match_matches_publisher_substring():
    secure_policy = policy.SecureDesktopPolicy(
        policy="allow_with_match",
        publishers_allowlist=["Microsoft"],
    )

    assert secure_policy.allows_auto_click("Microsoft Windows") == (
        True,
        "publisher 'Microsoft Windows' matched allowlist entry 'Microsoft'",
    )
    assert secure_policy.allows_auto_click(None)[0] is False
