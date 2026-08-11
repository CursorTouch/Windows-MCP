import pytest

from windows_mcp.uia import core


def test_dragdrop_releases_mouse_after_move_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def press_mouse(x: int, y: int, wait_time: float) -> None:
        calls.append(f"press:{x},{y},{wait_time}")

    def move_to(x: int, y: int, move_speed: float, wait_time: float) -> None:
        calls.append(f"move:{x},{y},{move_speed},{wait_time}")
        raise RuntimeError("move failed")

    def release_mouse(wait_time: float) -> None:
        calls.append(f"release:{wait_time}")

    monkeypatch.setattr(core, "PressMouse", press_mouse)
    monkeypatch.setattr(core, "MoveTo", move_to)
    monkeypatch.setattr(core, "ReleaseMouse", release_mouse)

    with pytest.raises(RuntimeError, match="move failed"):
        core.DragDrop(10, 20, 30, 40, moveSpeed=2, waitTime=0.3)

    assert calls == ["press:10,20,0.05", "move:30,40,2,0.05", "release:0.3"]


def test_dragdrop_releases_mouse_after_press_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def press_mouse(x: int, y: int, wait_time: float) -> None:
        calls.append(f"press:{x},{y},{wait_time}")
        raise RuntimeError("press wait interrupted")

    monkeypatch.setattr(core, "PressMouse", press_mouse)
    monkeypatch.setattr(
        core,
        "MoveTo",
        lambda *args: pytest.fail("movement must not start after press failure"),
    )
    monkeypatch.setattr(
        core,
        "ReleaseMouse",
        lambda wait_time: calls.append(f"release:{wait_time}"),
    )

    with pytest.raises(RuntimeError, match="press wait interrupted"):
        core.DragDrop(10, 20, 30, 40, waitTime=0.3)

    assert calls == ["press:10,20,0.05", "release:0.3"]


def test_dragdrop_uses_duration_path_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(core, "PressMouse", lambda *args: calls.append(f"press:{args}"))
    monkeypatch.setattr(core, "MoveTo", lambda *args: calls.append(f"move:{args}"))
    monkeypatch.setattr(core, "MoveToDuration", lambda *args: calls.append(f"duration:{args}"))
    monkeypatch.setattr(core, "ReleaseMouse", lambda *args: calls.append(f"release:{args}"))

    core.DragDrop(1, 2, 3, 4, moveSpeed=5, waitTime=0.6, duration=0.25)

    assert calls == [
        "press:(1, 2, 0.05)",
        "duration:(3, 4, 0.25, 0.05)",
        "release:(0.6,)",
    ]


def test_move_to_duration_emits_intermediate_and_final_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions: list[tuple[int, int]] = []

    monkeypatch.setattr(core, "GetCursorPos", lambda: (0, 0))
    monkeypatch.setattr(core, "SetCursorPos", lambda x, y: positions.append((x, y)))
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    core.MoveToDuration(30, 0, duration=0.04, waitTime=0)

    assert len(positions) > 1
    assert positions[-1] == (30, 0)


def test_move_to_duration_honors_short_positive_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions: list[tuple[int, int]] = []
    sleeps: list[float] = []

    monkeypatch.setattr(core, "GetCursorPos", lambda: (0, 0))
    monkeypatch.setattr(core, "SetCursorPos", lambda x, y: positions.append((x, y)))
    monkeypatch.setattr(core.time, "sleep", sleeps.append)

    core.MoveToDuration(10, 0, duration=0.005, waitTime=0.03)

    assert positions == [(5, 0), (10, 0)]
    assert sum(sleeps[:-1]) == pytest.approx(0.005)
    assert sleeps[-1] == 0.03


def test_move_to_duration_zero_duration_sets_final_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions: list[tuple[int, int]] = []

    monkeypatch.setattr(core, "GetCursorPos", lambda: (10, 10))
    monkeypatch.setattr(core, "SetCursorPos", lambda x, y: positions.append((x, y)))
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    core.MoveToDuration(10, 10, duration=0, waitTime=0)

    assert positions == [(10, 10)]
