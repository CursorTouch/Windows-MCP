from types import SimpleNamespace

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop


def _desktop() -> Desktop:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    return desktop


def test_desktop_drag_uses_explicit_start_duration_and_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int, int, int, float | None]] = []
    desktop = _desktop()

    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Untitled - Notepad", ProcessId=123),
    )
    monkeypatch.setattr(service, "Process", lambda pid: SimpleNamespace(name=lambda: "notepad.exe"))
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda x1, y1, x2, y2, moveSpeed=1, duration=None: calls.append(
            (x1, y1, x2, y2, moveSpeed, duration)
        ),
    )

    result = desktop.drag(
        [100, 200],
        from_loc=[10, 20],
        duration="0.25",
        expected_window_title="notepad",
        expected_process="NOTEPAD.EXE",
    )

    assert calls == [(10, 20, 100, 200, 1, 0.25)]
    assert result["start"] == [10, 20]
    assert result["end"] == [100, 200]
    assert result["duration"] == 0.25
    assert result["foreground"] == {
        "title": "Untitled - Notepad",
        "process": "notepad.exe",
        "process_id": 123,
    }


def test_desktop_drag_legacy_start_uses_current_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, int, int]] = []
    desktop = _desktop()

    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(service.uia, "GetCursorPos", lambda: (7, 8))
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda x1, y1, x2, y2, **kwargs: calls.append((x1, y1, x2, y2)),
    )

    result = desktop.drag((30, 40))

    assert calls == [(7, 8, 30, 40)]
    assert result["start"] == [7, 8]
    assert result["duration"] is None


def test_desktop_drag_fails_before_press_on_title_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Calculator", ProcessId=123),
    )
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not drag")),
    )

    with pytest.raises(ValueError, match="expected_window_title"):
        desktop.drag([100, 200], from_loc=[10, 20], expected_window_title="Notepad")


def test_desktop_drag_fails_before_press_on_process_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()

    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        desktop,
        "get_foreground_window",
        lambda: SimpleNamespace(Name="Untitled - Notepad", ProcessId=123),
    )
    monkeypatch.setattr(service, "Process", lambda pid: SimpleNamespace(name=lambda: "notepad.exe"))
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not drag")),
    )

    with pytest.raises(ValueError, match="expected_process"):
        desktop.drag([100, 200], from_loc=[10, 20], expected_process="note.exe")


def test_desktop_drag_rejects_non_finite_duration() -> None:
    desktop = _desktop()

    with pytest.raises(ValueError, match="finite"):
        desktop.drag([1, 2], from_loc=[3, 4], duration="nan")
