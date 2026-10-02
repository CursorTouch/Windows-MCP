import pytest
from unittest.mock import Mock

from windows_mcp.desktop import service
from windows_mcp.desktop.control_ledger import InputLedger
from windows_mcp.desktop.service import Desktop


def _desktop() -> Desktop:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    return desktop


def _stub_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give drag tests an isolated AI-input ledger without real mouse injection."""
    owner = Mock()
    owner.input_ledger = InputLedger()
    owner.physical_mouse_down.return_value = False
    monkeypatch.setattr(service, "get_controller", lambda: owner)


def test_desktop_drag_uses_explicit_start_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[int, ...]]] = []
    desktop = _desktop()

    _stub_control(monkeypatch)
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        service.uia, "PressMouse", lambda x, y, **kw: calls.append(("down", (x, y)))
    )
    monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: calls.append(("move", (x, y))))
    monkeypatch.setattr(service.uia, "ReleaseMouse", lambda **kw: calls.append(("up", ())))

    result = desktop.drag(
        [100, 200],
        from_loc=[10, 20],
        duration="0.25",
    )

    assert calls[0] == ("down", (10, 20))
    assert calls[-2:] == [("move", (100, 200)), ("up", ())]
    assert len([call for call in calls if call[0] == "move"]) == 25
    assert result["start"] == [10, 20]
    assert result["end"] == [100, 200]
    assert result["duration"] == 0.25


def test_desktop_drag_legacy_start_uses_current_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[int, ...]]] = []
    desktop = _desktop()

    _stub_control(monkeypatch)
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(service.uia, "GetCursorPos", lambda: (7, 8))
    monkeypatch.setattr(
        service.uia, "PressMouse", lambda x, y, **kw: calls.append(("down", (x, y)))
    )
    monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: calls.append(("move", (x, y))))
    monkeypatch.setattr(service.uia, "ReleaseMouse", lambda **kw: calls.append(("up", ())))

    result = desktop.drag((30, 40))

    assert calls[0] == ("down", (7, 8))
    assert calls[-2:] == [("move", (30, 40)), ("up", ())]
    assert result["start"] == [7, 8]
    assert result["duration"] is None


def test_desktop_drag_rejects_non_finite_duration() -> None:
    desktop = _desktop()

    with pytest.raises(ValueError, match="finite"):
        desktop.drag([1, 2], from_loc=[3, 4], duration="nan")


@pytest.mark.parametrize("duration", [True, False])
def test_desktop_drag_rejects_boolean_duration_before_input(
    duration: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()
    monkeypatch.setattr(
        service,
        "sleep",
        lambda seconds: pytest.fail("duration must be rejected before waiting"),
    )
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda *args, **kwargs: pytest.fail("duration must be rejected before input"),
    )

    with pytest.raises(ValueError, match="finite"):
        desktop.drag([1, 2], from_loc=[3, 4], duration=duration)


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("loc", [1]),
        ("loc", [1, True]),
        ("loc", [1, 2.0]),
        ("loc", [1, "2"]),
        ("from_loc", (3,)),
        ("from_loc", (3, False)),
        ("from_loc", (3.0, 4)),
        ("from_loc", ("3", 4)),
    ],
)
def test_desktop_drag_rejects_invalid_points_before_waiting_or_input(
    argument: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()
    monkeypatch.setattr(
        service,
        "sleep",
        lambda seconds: pytest.fail("point must be rejected before waiting"),
    )
    monkeypatch.setattr(
        service.uia,
        "DragDrop",
        lambda *args, **kwargs: pytest.fail("point must be rejected before input"),
    )
    kwargs: dict[str, object] = {
        "loc": [1, 2],
        "from_loc": [3, 4],
        argument: value,
    }

    with pytest.raises(ValueError, match=argument):
        desktop.drag(**kwargs)
