"""Cross-call Pointer ownership against the merged desktop takeover gate."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import pointer
from windows_mcp.desktop.control import ControlBlocked, ControlCoordinator
from windows_mcp.desktop.control_context import current_token
from windows_mcp.desktop.pointer import PointerController
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


class FakeTimer:
    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def fire(self) -> None:
        self.callback()


@pytest.fixture
def setup_pointer(monkeypatch: pytest.MonkeyPatch):
    owner = ControlCoordinator()
    owner._state = "ready"
    events: list[str] = []
    timers: list[FakeTimer] = []

    monkeypatch.setattr(pointer.uia, "PressMouse", lambda *a, **k: events.append("down"))
    monkeypatch.setattr(pointer.uia, "ReleaseMouse", lambda *a, **k: events.append("up"))
    monkeypatch.setattr(pointer.uia, "GetCursorPos", lambda: (0, 0))
    monkeypatch.setattr(pointer.uia, "SetCursorPos", lambda *a: events.append("move"))

    def make_timer(interval: float, callback: Callable[[], None]) -> FakeTimer:
        timer = FakeTimer(interval, callback)
        timers.append(timer)
        return timer

    return owner, events, timers, make_timer


def _gesture_call(owner: ControlCoordinator, action: Callable[[], object]) -> object:
    lease = owner.begin_call("Pointer")
    marker = current_token.set(lease)
    try:
        return action()
    finally:
        current_token.reset(marker)
        owner.end_call(lease)


@pytest.mark.asyncio
async def test_pointer_gesture_crosses_real_mcp_gate_without_reopening_other_tools(setup_pointer):
    owner, events, _, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    mcp = FastMCP("pointer-integration")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))

    @mcp.tool(name="Pointer")
    def pointer_tool(action: str) -> str:
        if action == "down":
            gesture.down([1, 2])
        elif action == "move":
            gesture.move([3, 4])
        else:
            gesture.up()
        return action

    @mcp.tool(name="App")
    def app_tool() -> str:
        return "unexpected"

    async with Client(mcp) as client:
        await client.call_tool("Pointer", {"action": "down"})
        blocked = await client.call_tool("App", {}, raise_on_error=False)
        assert blocked.is_error and "AI_INPUT_HELD" in str(blocked.content)
        await client.call_tool("Pointer", {"action": "move"})
        await client.call_tool("Pointer", {"action": "up"})
        assert (await client.call_tool("App", {})).is_error is False
    assert events == ["down", "move", "up"]


@pytest.mark.parametrize("timeout", [30, 120])
def test_pointer_hold_survives_lease_but_blocks_other_tools(setup_pointer, timeout):
    owner, events, timers, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2], timeout=timeout))

    assert timers[0].interval == timeout
    owner._lease_until = time.monotonic() + 0.01
    assert owner.status()["state"] == "ai"
    owner._lease_until = time.monotonic() - 1  # Beyond the normal 15-second lease.
    assert owner.status()["state"] == "ai"
    with pytest.raises(ControlBlocked, match="AI_INPUT_HELD"):
        owner.begin_call("App")

    _gesture_call(owner, lambda: gesture.move([3, 4]))
    _gesture_call(owner, gesture.up)
    owner._lease_until = time.monotonic() - 1
    assert owner.status()["state"] == "ready"
    assert events == ["down", "move", "up"]


def test_pointer_timeout_releases_hold_before_ai_lease_ends(setup_pointer):
    owner, events, timers, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    owner._lease_until = time.monotonic() - 1
    assert owner.status()["state"] == "ai"

    timers[0].fire()
    assert owner.input_ledger.pending() == ()
    assert gesture.held_button is None
    assert owner.status()["state"] == "ready"
    assert events == ["down", "up"]


def test_unowned_legacy_hold_fails_closed_after_lease(setup_pointer):
    owner, events, _, _ = setup_pointer
    lease = owner.begin_call("App")
    owner.input_ledger.press(
        "mouse:left", lambda: events.append("down"), lambda: events.append("up"), lambda: False
    )
    owner.end_call(lease)
    owner._lease_until = time.monotonic() - 1

    assert owner.status()["state"] == "unavailable"
    assert owner._suppress is False
    owner.status()  # The emergency cleanup must retry the pending ledger hold.
    assert owner.input_ledger.pending() == ()
    assert events == ["down", "up"]


def test_old_receipt_and_timer_cannot_release_new_same_button(setup_pointer):
    owner, events, timers, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    old_receipt = gesture._receipt
    old_timer = timers[0]
    owner.input_ledger.release_all()  # Physical takeover owns this cleanup.
    assert gesture.held_button is None

    _gesture_call(owner, lambda: gesture.down([3, 4]))
    assert not owner.input_ledger.release("mouse:left", owner="Pointer", receipt=old_receipt)
    old_timer.fire()
    assert gesture.held_button == "left"
    assert events == ["down", "up", "down"]
    _gesture_call(owner, gesture.cancel)
    assert events == ["down", "up", "down", "up"]


def test_takeover_during_timed_move_stops_before_next_injection(setup_pointer):
    owner, events, _, make_timer = setup_pointer

    def takeover(_seconds: float) -> None:
        owner._fast_takeover = True
        owner.input_ledger.release_all()

    gesture = PointerController(control=owner, timer_factory=make_timer, sleeper=takeover)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    with pytest.raises(ControlBlocked, match="CONTROL_PREEMPTED"):
        _gesture_call(owner, lambda: gesture.move([30, 40], duration=0.1))
    assert events == ["down", "up"]
    assert owner.input_ledger.pending() == ()


def test_move_and_takeover_barrier_finish_without_post_takeover_move(setup_pointer):
    owner, events, _, make_timer = setup_pointer
    sleeping = threading.Event()
    resume = threading.Event()
    errors: list[BaseException] = []

    def pause(_seconds: float) -> None:
        sleeping.set()
        assert resume.wait(2)

    gesture = PointerController(control=owner, timer_factory=make_timer, sleeper=pause)
    _gesture_call(owner, lambda: gesture.down([1, 2]))

    def worker() -> None:
        try:
            _gesture_call(owner, lambda: gesture.move([30, 40], duration=0.1))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert sleeping.wait(2)
        owner._fast_takeover = True
        owner.input_ledger.release_all()
    finally:
        resume.set()
        thread.join(2)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ControlBlocked)
    assert events == ["down", "up"]


def test_timeout_can_release_during_timed_move_sleep(setup_pointer):
    owner, events, timers, make_timer = setup_pointer
    sleeping = threading.Event()
    resume = threading.Event()
    errors: list[BaseException] = []

    def pause(_seconds: float) -> None:
        sleeping.set()
        assert resume.wait(2)

    gesture = PointerController(control=owner, timer_factory=make_timer, sleeper=pause)
    _gesture_call(owner, lambda: gesture.down([1, 2]))

    def moving() -> None:
        try:
            _gesture_call(owner, lambda: gesture.move([30, 40], duration=0.1))
        except BaseException as exc:
            errors.append(exc)

    move_thread = threading.Thread(target=moving)
    timer_thread = threading.Thread(target=timers[0].fire)
    move_thread.start()
    try:
        assert sleeping.wait(2)
        timer_thread.start()
        timer_thread.join(1)
        assert not timer_thread.is_alive(), "timeout release waited for the move sleeper"
    finally:
        resume.set()
        move_thread.join(2)
        if timer_thread.ident is not None:
            timer_thread.join(2)
    assert not move_thread.is_alive()
    assert not timer_thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert events == ["down", "up"]


def test_timer_and_next_call_finish_without_deadlock(setup_pointer, monkeypatch):
    owner, events, timers, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    releasing = threading.Event()
    resume = threading.Event()

    def slow_up(*args, **kwargs) -> None:
        releasing.set()
        assert resume.wait(2)
        events.append("up")

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", slow_up)
    timer_thread = threading.Thread(target=timers[0].fire)
    next_thread = threading.Thread(
        target=lambda: _gesture_call(owner, lambda: gesture.down([3, 4]))
    )
    timer_thread.start()
    try:
        assert releasing.wait(2)
        next_thread.start()
    finally:
        resume.set()
        timer_thread.join(2)
        next_thread.join(2)
    assert not timer_thread.is_alive() and not next_thread.is_alive()
    assert gesture.held_button == "left"
    assert events == ["down", "up", "down"]
    _gesture_call(owner, gesture.cancel)


def test_shutdown_retries_failed_pointer_release(setup_pointer, monkeypatch):
    owner, events, _, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    attempts = 0

    def flaky_up(*args, **kwargs) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("first release failed")
        events.append("up")

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", flaky_up)
    with pytest.raises(OSError, match="first release failed"):
        gesture.close()
    assert owner.input_ledger.pending() == ("mouse:left",)
    owner.stop()
    assert owner.input_ledger.pending() == ()
    assert attempts == 2
    assert owner._thread is None and owner._watchdog is None


def test_failed_retry_schedule_releases_physical_interception(setup_pointer, monkeypatch):
    owner, _, timers, make_timer = setup_pointer
    created = 0

    def first_timer_only(interval: float, callback: Callable[[], None]) -> FakeTimer:
        nonlocal created
        created += 1
        if created > 1:
            raise OSError("timer unavailable")
        return make_timer(interval, callback)

    gesture = PointerController(control=owner, timer_factory=first_timer_only)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    monkeypatch.setattr(
        pointer.uia,
        "ReleaseMouse",
        lambda *a, **k: (_ for _ in ()).throw(OSError("release failed")),
    )

    timers[0].fire()
    assert owner._emergency is True
    assert owner._suppress is False
    assert owner.input_ledger.pending_entries() == (("mouse:left", "Pointer"),)


def test_shutdown_and_timer_finish_with_one_release(setup_pointer, monkeypatch):
    owner, events, timers, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    releasing = threading.Event()
    resume = threading.Event()

    def slow_up(*args, **kwargs) -> None:
        releasing.set()
        assert resume.wait(2)
        events.append("up")

    monkeypatch.setattr(pointer.uia, "ReleaseMouse", slow_up)
    timer_thread = threading.Thread(target=timers[0].fire)
    shutdown_thread = threading.Thread(target=lambda: (gesture.close(), owner.stop()))
    timer_thread.start()
    try:
        assert releasing.wait(2)
        shutdown_thread.start()
    finally:
        resume.set()
        timer_thread.join(2)
        shutdown_thread.join(2)
    assert not timer_thread.is_alive() and not shutdown_thread.is_alive()
    assert owner.input_ledger.pending() == ()
    assert events == ["down", "up"]


def test_failed_shutdown_keeps_ledger_receipt_for_audit(setup_pointer, monkeypatch):
    owner, _, _, make_timer = setup_pointer
    gesture = PointerController(control=owner, timer_factory=make_timer)
    _gesture_call(owner, lambda: gesture.down([1, 2]))
    monkeypatch.setattr(
        pointer.uia, "ReleaseMouse", lambda *a, **k: (_ for _ in ()).throw(OSError("up failed"))
    )
    with pytest.raises(OSError, match="up failed"):
        gesture.close()
    with pytest.raises(OSError, match="up failed"):
        owner.stop()
    assert owner.input_ledger.pending_entries() == (("mouse:left", "Pointer"),)
    assert owner._stop.is_set()
    assert owner._thread is None and owner._watchdog is None
