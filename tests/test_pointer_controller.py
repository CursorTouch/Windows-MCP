from collections.abc import Callable

import pytest

from windows_mcp.desktop import pointer
from windows_mcp.desktop.control import ControlCoordinator
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


@pytest.fixture(autouse=True)
def isolated_input_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held gesture in one test must never leak into another test's ledger."""
    owner = ControlCoordinator()
    monkeypatch.setattr(pointer, "get_controller", lambda: owner)


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
    monkeypatch.setattr(pointer.uia, "GetCursorPos", lambda: (1, 2))
    monkeypatch.setattr(pointer.uia, "SetCursorPos", record("set-pos"))
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
    controller = PointerController(timer_factory=TimerFactory(), sleeper=lambda _: None)
    controller.down([1, 2])

    immediate = controller.move([3, 4])
    bounded = controller.move([5, 6], duration="0.25")

    assert immediate["duration"] is None
    assert bounded["duration"] == 0.25
    assert [call[0] for call in calls] == ["press-left"] + ["set-pos"] * 14
    assert calls[-1][1:3] == (5, 6)
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


def test_pointer_cancel_is_repeatable_and_releases_only_owned_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2], button="middle")

    first = controller.cancel()
    second = controller.cancel()

    assert first == {"action": "cancel", "button": "middle"}
    assert second == {"action": "cancel", "button": None}
    assert [call[0] for call in calls] == ["press-middle", "release-middle"]
    assert controller.held_button is None


def test_pointer_timeout_releases_only_owned_button_and_ignores_stale_timer(
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
    assert [call[0] for call in calls][-1:] == ["release-right"]


def test_pointer_close_releases_only_owned_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2], button="right")

    controller.close()
    controller.close()

    assert [call[0] for call in calls] == ["press-right", "release-right"]
    assert controller.held_button is None


def test_pointer_move_failure_releases_tracked_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=TimerFactory())
    controller.down([1, 2])
    monkeypatch.setattr(
        pointer.uia,
        "SetCursorPos",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("move failed")),
    )

    with pytest.raises(OSError, match="move failed"):
        controller.move([3, 4])

    assert [call[0] for call in calls] == ["press-left", "release-left"]
    assert controller.held_button is None


def test_pointer_timer_setup_failure_does_not_press_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    controller = PointerController(timer_factory=FailingTimerFactory())

    with pytest.raises(RuntimeError, match="timer setup failed"):
        controller.down([1, 2])

    assert calls == []
    assert controller.held_button is None


def test_pointer_cancel_retries_after_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    controller.down([1, 2])
    release = pointer.uia.ReleaseMouse
    attempts = 0

    def flaky_release(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("release failed")
        release(*args, **kwargs)

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", flaky_release)

    with pytest.raises(OSError, match="release failed"):
        controller.cancel()

    assert controller.held_button == "left"
    assert timers.timers[0].cancelled is False
    assert [call[0] for call in calls] == ["press-left"]

    controller.cancel()
    assert controller.held_button is None
    assert timers.timers[0].cancelled is True
    assert [call[0] for call in calls] == ["press-left", "release-left"]


def test_pointer_timeout_retries_after_cursor_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    controller.down([1, 2], button="right")
    release = pointer.uia.RightReleaseMouse

    def release_after_cursor_lookup(*args: object, **kwargs: object) -> None:
        pointer.uia.GetCursorPos()
        release(*args, **kwargs)

    monkeypatch.setattr(pointer.uia, "RightReleaseMouse", release_after_cursor_lookup)
    monkeypatch.setattr(
        pointer.uia,
        "GetCursorPos",
        lambda: (_ for _ in ()).throw(OSError("GetCursorPos failed")),
    )

    timers.timers[0].fire()
    assert controller.held_button == "right"
    assert len(timers.timers) == 2
    assert timers.timers[1].interval == pointer.RELEASE_RETRY_DELAY
    assert [call[0] for call in calls] == ["press-right"]

    monkeypatch.setattr(pointer.uia, "GetCursorPos", lambda: (1, 2))
    timers.timers[1].fire()
    assert controller.held_button is None
    assert [call[0] for call in calls] == ["press-right", "release-right"]


def test_pointer_timeout_exhaustion_keeps_ownership_for_manual_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    controller.down([1, 2])
    release = pointer.uia.ReleaseMouse
    monkeypatch.setattr(
        pointer.uia,
        "ReleaseMouse",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("release failed")),
    )

    for attempt in range(pointer.MAX_RELEASE_ATTEMPTS):
        timers.timers[attempt].fire()

    assert len(timers.timers) == pointer.MAX_RELEASE_ATTEMPTS
    assert controller.held_button == "left"
    assert [call[0] for call in calls] == ["press-left"]

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", release)
    controller.cancel()
    assert controller.held_button is None
    assert [call[0] for call in calls] == ["press-left", "release-left"]


def test_pointer_press_and_compensation_failure_retains_retriable_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    release = pointer.uia.ReleaseMouse
    monkeypatch.setattr(
        pointer.uia,
        "PressMouse",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("press failed")),
    )
    monkeypatch.setattr(
        pointer.uia,
        "ReleaseMouse",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("release failed")),
    )

    with pytest.raises(OSError, match="press failed"):
        controller.down([1, 2])

    # The ledger reports the original press error and retains the failed
    # compensation for the timer/watchdog to retry.
    assert controller.held_button == "left"
    assert timers.timers[0].started is True
    assert timers.timers[0].cancelled is False

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", release)
    timers.timers[0].fire()
    assert controller.held_button is None
    assert [call[0] for call in calls] == ["release-left"]


def test_pointer_press_failure_with_successful_compensation_clears_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mouse_calls(monkeypatch)
    timers = TimerFactory()
    controller = PointerController(timer_factory=timers)
    monkeypatch.setattr(
        pointer.uia,
        "PressMouse",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("press failed")),
    )

    with pytest.raises(OSError, match="press failed"):
        controller.down([1, 2])

    assert [call[0] for call in calls] == ["release-left"]
    assert controller.held_button is None
    assert timers.timers[0].cancelled is True


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


def test_desktop_delegates_pointer_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = Desktop.__new__(Desktop)
    controller = FakePointerController()
    desktop._pointer = controller
    validated: list[tuple[int, int]] = []
    monkeypatch.setattr(
        Desktop,
        "_validate_screen_point",
        staticmethod(lambda x, y: validated.append((x, y))),
    )

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
    assert validated == [(1, 2), (3, 4)]


@pytest.mark.parametrize("action", ["down", "move"])
@pytest.mark.parametrize("point", [[-1, 2], [100, 2], [1, 100]])
def test_desktop_rejects_out_of_bounds_pointer_before_controller(
    action: str,
    point: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = Desktop.__new__(Desktop)
    controller = FakePointerController()
    desktop._pointer = controller
    monkeypatch.setattr(pointer.uia, "GetVirtualScreenRect", lambda: (0, 0, 100, 100))

    with pytest.raises(ValueError, match="outside the desktop bounds"):
        if action == "down":
            desktop.pointer_down(point)
        else:
            desktop.pointer_move(point)

    assert controller.calls == []
