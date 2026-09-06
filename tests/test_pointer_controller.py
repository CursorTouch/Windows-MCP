from collections.abc import Callable

import pytest

from windows_mcp.desktop import pointer
from windows_mcp.desktop.pointer import PointerController
from windows_mcp.desktop.service import Desktop


class FakeTimer:
    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.function()


class TimerFactory:
    def __init__(self) -> None:
        self.timers: list[FakeTimer] = []

    def __call__(self, interval: float, function: Callable[[], None]) -> FakeTimer:
        timer = FakeTimer(interval, function)
        self.timers.append(timer)
        return timer


class FailingTimerFactory:
    def __call__(self, interval: float, function: Callable[[], None]) -> FakeTimer:
        raise RuntimeError("timer setup failed")


def _mouse_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def record(name: str) -> Callable:
        return lambda *args, **kwargs: calls.append((name, *args, kwargs))

    monkeypatch.setattr(pointer.uia, "PressMouse", record("press-left"))
    monkeypatch.setattr(pointer.uia, "RightPressMouse", record("press-right"))
    monkeypatch.setattr(pointer.uia, "MiddlePressMouse", record("press-middle"))
    monkeypatch.setattr(pointer.uia, "ReleaseMouse", record("release-left"))
    monkeypatch.setattr(pointer.uia, "RightReleaseMouse", record("release-right"))
    monkeypatch.setattr(pointer.uia, "MiddleReleaseMouse", record("release-middle"))
    monkeypatch.setattr(pointer.uia, "MoveTo", record("move"))
    monkeypatch.setattr(pointer.uia, "MoveToDuration", record("move-duration"))
    return calls


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_pointer_down_and_up_use_matching_button(
    button: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)

    down_result = controller.down([10, 20], button=button, timeout="12.5")
    up_result = controller.up(button)

    assert down_result == {
        "action": "down",
        "button": button,
        "loc": [10, 20],
        "timeout": 12.5,
    }
    assert up_result == {"action": "up", "button": button}
    assert [call[0] for call in calls] == [f"press-{button}", f"release-{button}"]
    assert timers.timers[0].interval == 12.5
    assert timers.timers[0].daemon is True
    assert timers.timers[0].started is True
    assert timers.timers[0].cancelled is True
    assert controller.held_button is None


def test_pointer_rejects_second_down_without_extra_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2])

    with pytest.raises(RuntimeError, match="already held"):
        controller.down([3, 4], button="right")

    assert [call[0] for call in calls] == ["press-left"]
    assert controller.held_button == "left"


def test_pointer_move_supports_immediate_and_duration_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2])

    immediate = controller.move([3, 4])
    bounded = controller.move([5, 6], duration="0.25")

    assert immediate["duration"] is None
    assert bounded["duration"] == 0.25
    assert [call[0] for call in calls] == ["press-left", "move", "move-duration"]
    assert controller.held_button == "left"


def test_pointer_move_requires_held_button_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())

    with pytest.raises(RuntimeError, match="no mouse button is held"):
        controller.move([3, 4])

    assert calls == []


def test_pointer_up_rejects_button_mismatch_without_releasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2], button="right")

    with pytest.raises(RuntimeError, match="right mouse button is held"):
        controller.up("left")

    assert [call[0] for call in calls] == ["press-right"]
    assert controller.held_button == "right"


def test_pointer_cancel_is_repeatable_and_releases_every_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2], button="middle")

    first = controller.cancel()
    second = controller.cancel()

    assert first == {"action": "cancel", "button": "middle"}
    assert second == {"action": "cancel", "button": None}
    assert [call[0] for call in calls] == [
        "press-middle",
        "release-left",
        "release-right",
        "release-middle",
        "release-left",
        "release-right",
        "release-middle",
    ]
    assert controller.held_button is None


def test_pointer_timeout_releases_all_buttons_and_ignores_stale_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    controller.down([1, 2])
    first_timer = timers.timers[0]
    controller.up()
    controller.down([3, 4], button="right")

    first_timer.fire()
    assert controller.held_button == "right"
    assert [call[0] for call in calls] == ["press-left", "release-left", "press-right"]

    timers.timers[1].fire()
    assert controller.held_button is None
    assert [call[0] for call in calls][-3:] == [
        "release-left",
        "release-right",
        "release-middle",
    ]


def test_pointer_move_failure_releases_tracked_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2])
    monkeypatch.setattr(
        pointer.uia, "MoveTo", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("move failed"))
    )

    with pytest.raises(OSError, match="move failed"):
        controller.move([3, 4])

    assert [call[0] for call in calls] == ["press-left", "release-left"]
    assert controller.held_button is None


def test_pointer_timer_setup_failure_releases_pressed_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=FailingTimerFactory())

    with pytest.raises(RuntimeError, match="timer setup failed"):
        controller.down([1, 2])

    assert [call[0] for call in calls] == ["press-left", "release-left"]
    assert controller.held_button is None


@pytest.mark.parametrize(
    ("method", "value", "message"),
    [
        ("down", [1], "loc"),
        ("down", [1, True], "integers"),
        ("down", [1.0, 2], "integers"),
        ("timeout", True, "finite"),
        ("timeout", 0, "greater than 0"),
        ("timeout", 121, "at most 120"),
        ("duration", False, "finite"),
        ("duration", -1, "between 0 and 10"),
        ("duration", 11, "between 0 and 10"),
    ],
)
def test_pointer_rejects_invalid_values_before_input(
    method: str,
    value: object,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())

    with pytest.raises(ValueError, match=message):
        if method == "down":
            controller.down(value)
        elif method == "timeout":
            controller.down([1, 2], timeout=value)
        else:
            controller.down([1, 2])
            calls.clear()
            controller.move([3, 4], duration=value)

    assert calls == []


class FakePointerController:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def down(self, *args: object) -> dict[str, object]:
        self.calls.append(("down", *args))
        return {"action": "down"}

    def move(self, *args: object) -> dict[str, object]:
        self.calls.append(("move", *args))
        return {"action": "move"}

    def up(self, *args: object) -> dict[str, object]:
        self.calls.append(("up", *args))
        return {"action": "up"}

    def cancel(self) -> dict[str, object]:
        self.calls.append(("cancel",))
        return {"action": "cancel"}

    def close(self) -> None:
        self.calls.append(("close",))


def test_desktop_delegates_pointer_lifecycle() -> None:
    desktop = Desktop.__new__(Desktop)
    controller = FakePointerController()
    desktop._pointer = controller

    desktop.pointer_down([1, 2], "right", 10)
    desktop.pointer_move([3, 4], 0.2)
    desktop.pointer_up("right")
    desktop.pointer_cancel()
    desktop.close()

    assert controller.calls == [
        ("down", [1, 2], "right", 10),
        ("move", [3, 4], 0.2),
        ("up", "right"),
        ("cancel",),
        ("close",),
    ]
