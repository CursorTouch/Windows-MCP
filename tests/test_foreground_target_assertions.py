from types import SimpleNamespace

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop


def _desktop() -> Desktop:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    return desktop


def test_assert_foreground_target_returns_none_without_guards() -> None:
    desktop = _desktop()

    assert desktop.assert_foreground_target() is None


def test_assert_foreground_target_accepts_matching_title_and_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Untitled - Notepad", ProcessId=123),
    )
    monkeypatch.setattr(service, "Process", lambda pid: SimpleNamespace(name=lambda: "notepad.exe"))

    result = desktop.assert_foreground_target(
        expected_window_title="notepad",
        expected_process="NOTEPAD.EXE",
    )

    assert result == {
        "title": "Untitled - Notepad",
        "process": "notepad.exe",
        "process_id": 123,
    }


def test_assert_foreground_target_rejects_title_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Calculator", ProcessId=123),
    )

    with pytest.raises(ValueError, match="expected_window_title"):
        desktop.assert_foreground_target(expected_window_title="Notepad")


def test_assert_foreground_target_rejects_process_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Untitled - Notepad", ProcessId=123),
    )
    monkeypatch.setattr(service, "Process", lambda pid: SimpleNamespace(name=lambda: "notepad.exe"))

    with pytest.raises(ValueError, match="expected_process"):
        desktop.assert_foreground_target(expected_process="note.exe")


def test_assert_foreground_target_rejects_missing_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(desktop, "get_foreground_window", lambda: None)

    with pytest.raises(ValueError, match="No foreground window"):
        desktop.assert_foreground_target(expected_process="notepad.exe")
