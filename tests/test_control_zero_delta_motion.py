"""A mouse move that does not move the cursor is not user activity.

Some wireless receivers keep streaming WM_MOUSEMOVE at an unchanged position
for as long as they are powered. Counting those repeats as physical activity
keeps the coordinator in "user", so every tool call is rejected with
USER_CONTROL until the receiver is unplugged (issue #498).
"""

import ctypes

import pytest

from windows_mcp.desktop import control, control_hooks


class Clock:
    """Stand-in for the ``time`` module so the idle deadline is deterministic."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    owner._state = "ready"
    return owner


def mouse_event(owner, x: int, y: int, message: int = 0x200, injected: bool = False) -> int:
    data = control._MouseHookData()
    data.flags = 1 if injected else 0
    data.pt.x, data.pt.y = x, y
    return owner._physical_mouse(0, message, ctypes.addressof(data))


def prime(owner, x: int = 400, y: int = 300) -> None:
    """Give the coordinator a cursor baseline without registering activity."""
    mouse_event(owner, x, y, injected=True)
    assert owner._last_physical_event == 0.0
    assert owner._events.empty()


@pytest.fixture(autouse=True)
def passthrough_hook(monkeypatch):
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 0)


def test_stationary_repeats_are_not_activity():
    owner = ready_controller()
    prime(owner)
    for _ in range(50):
        mouse_event(owner, 400, 300)
    assert owner._last_physical_event == 0.0
    assert owner._events.empty()


def test_stationary_repeats_leave_the_coordinator_ready():
    owner = ready_controller()
    prime(owner)
    for _ in range(50):
        mouse_event(owner, 400, 300)
    assert owner.status()["state"] == "ready"
    owner.begin_call("Screenshot")  # Must not raise USER_CONTROL.


def test_real_displacement_still_counts_as_activity():
    owner = ready_controller()
    prime(owner)
    mouse_event(owner, 401, 300)
    assert owner._last_physical_event > 0
    assert owner._events.get_nowait() == ("point", 401, 300, 0x200)
    assert owner.status()["state"] == "user"


def test_button_without_movement_still_counts_as_activity():
    owner = ready_controller()
    prime(owner)
    mouse_event(owner, 400, 300, 0x201)  # WM_LBUTTONDOWN at the same point.
    assert owner._events.get_nowait() == ("point", 400, 300, 0x201)
    assert owner._mouse_down == {1}
    assert owner.status()["state"] == "user"


def test_injected_moves_rebase_the_baseline_without_counting():
    owner = ready_controller()
    prime(owner, 100, 100)
    # The AI drives the cursor elsewhere; the device then repeats there.
    assert mouse_event(owner, 900, 700, injected=True) == 0
    for _ in range(10):
        mouse_event(owner, 900, 700)
    assert owner._last_physical_event == 0.0
    assert owner._events.empty()
    assert owner.status()["state"] == "ready"


def test_zero_delta_raw_reports_do_not_count(monkeypatch):
    owner = ready_controller()
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: (1, 0, 0))
    owner._raw_input(0)
    assert owner._last_physical_event == 0.0
    assert owner._events.empty()


def test_real_raw_delta_still_counts(monkeypatch):
    owner = ready_controller()
    clock = Clock(42.0)
    monkeypatch.setattr(control_hooks, "time", clock)
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: (1, 0, -3))
    owner._raw_input(0)
    assert owner._last_physical_event == 42.0
    assert owner._events.get_nowait() == ("raw", 1, 0, -3, 42.0)


def test_stationary_stream_cannot_hold_an_expired_user_state(monkeypatch):
    """The reported symptom: chatter kept the idle deadline from ever passing."""
    owner = ready_controller()
    owner.idle_timeout_seconds = 10.0
    clock = Clock(1000.0)
    monkeypatch.setattr(control, "time", clock)
    monkeypatch.setattr(control_hooks, "time", clock)

    mouse_event(owner, 400, 300)  # One real event hands control to the user.
    assert owner.status()["state"] == "user"

    while clock.now - owner._last_user < owner.idle_timeout_seconds + 1:
        clock.now += 0.015  # A 15 ms receiver repeat, always at the same point.
        mouse_event(owner, 400, 300)

    assert owner.status()["state"] == "ready"
    owner.begin_call("Click")  # Control returns instead of raising USER_CONTROL.
