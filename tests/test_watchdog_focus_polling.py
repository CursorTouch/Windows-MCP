"""Regression tests for issue #332 - focus is polled, not delivered by a COM callback.

The WatchDog used to register a UIA focus-changed event handler and run
comtypes.client.PumpEvents, so native UIA code called back into Python on the
WatchDog thread. When that path failed with E_UNEXPECTED (-2147418113) it
could end in an access violation that Python cannot catch, killing the server.
Focus is now read with IUIAutomation.GetFocusedElement() on the WatchDog's own
thread; any failure is an ordinary, catchable COMError on an outgoing call.

These tests drive the real _event_loop/_run against a fake UIA client.
"""

import types

import comtypes
import pytest

from windows_mcp.watchdog import service as watchdog_service
from windows_mcp.watchdog.service import FAIL_THRESHOLD, WatchDog

E_UNEXPECTED = -2147418113  # 0x8000FFFF


def e_unexpected():
    return comtypes.COMError(E_UNEXPECTED, "Catastrophic failure", (None, None, None, 0, None))


class FakeElement:
    def __init__(self, *runtime_id):
        self.runtime_id = list(runtime_id)

    def GetRuntimeId(self):
        return self.runtime_id


class FakeUIA:
    """Fake IUIAutomation. ``focus`` yields what each GetFocusedElement() call
    returns (an element, None, or an exception to raise)."""

    def __init__(self, focus, on_poll=None):
        self.focus = iter(focus)
        self.on_poll = on_poll
        self.polls = 0
        self.calls = []

    def GetFocusedElement(self):
        self.polls += 1
        if self.on_poll:
            self.on_poll(self)
        result = next(self.focus, None)
        if isinstance(result, BaseException):
            raise result
        return result

    def __getattr__(self, name):
        # Record any other UIA method the WatchDog reaches for.
        def method(*args):
            self.calls.append(name)

        return method


@pytest.fixture
def watchdog(monkeypatch):
    monkeypatch.setattr(
        watchdog_service._AutomationClient,
        "instance",
        classmethod(lambda cls: types.SimpleNamespace(UIAutomationCore=object())),
    )
    monkeypatch.setattr(watchdog_service.comtypes, "CoInitialize", lambda: None)
    monkeypatch.setattr(watchdog_service.comtypes, "CoUninitialize", lambda: None)
    monkeypatch.setattr(watchdog_service, "FOCUS_POLL_INTERVAL_SECONDS", 0)
    return WatchDog()


@pytest.fixture
def pump_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        watchdog_service.comtypes.client, "PumpEvents", lambda timeout: calls.append(timeout)
    )
    return calls


def stop_after(polls):
    def on_poll(uia):
        if uia.polls >= polls:
            uia.watchdog.is_running.clear()

    return on_poll


def run_event_loop(watchdog, uia):
    uia.watchdog = watchdog
    watchdog.uia = uia
    watchdog.is_running.set()
    watchdog._event_loop()


class TestNoComCallbackOnFocusPath:
    def test_focus_never_registers_a_handler_or_pumps(self, watchdog, pump_calls):
        seen = []
        watchdog.set_focus_callback(seen.append)
        uia = FakeUIA([FakeElement(42, 1)] * 5, on_poll=stop_after(5))

        run_event_loop(watchdog, uia)

        assert uia.polls == 5
        assert "AddFocusChangedEventHandler" not in uia.calls
        assert uia.calls == [], "focus path must make no other UIA calls"
        assert pump_calls == [], "PumpEvents must not run without event handlers"

    def test_events_are_pumped_only_while_a_structure_handler_exists(
        self, watchdog, pump_calls, monkeypatch
    ):
        monkeypatch.setattr(watchdog_service, "StructureChangedEventHandler", lambda parent: object())
        watchdog.set_structure_callback(lambda *a: None, element=object())
        uia = FakeUIA([], on_poll=stop_after(1))
        watchdog.set_focus_callback(lambda element: None)

        run_event_loop(watchdog, uia)

        assert "AddStructureChangedEventHandler" in uia.calls
        assert pump_calls == [0.1]


class TestFocusChangesAndDedup:
    def test_callback_fires_once_per_focused_element(self, watchdog, pump_calls):
        a, a_again, b = FakeElement(42, 1), FakeElement(42, 1), FakeElement(42, 2)
        seen = []
        watchdog.set_focus_callback(seen.append)
        uia = FakeUIA([a, a_again, a, b, b, a], on_poll=stop_after(6))

        run_event_loop(watchdog, uia)

        assert [e.runtime_id for e in seen] == [[42, 1], [42, 2], [42, 1]]
        assert seen[0] is a

    def test_no_focused_element_is_skipped(self, watchdog):
        seen = []
        watchdog.set_focus_callback(seen.append)
        watchdog.uia = FakeUIA([None, FakeElement(7)])

        watchdog._poll_focus()
        watchdog._poll_focus()

        assert [e.runtime_id for e in seen] == [[7]]

    def test_no_callback_means_no_polling(self, watchdog):
        watchdog.uia = FakeUIA([FakeElement(1)])
        watchdog._poll_focus()
        assert watchdog.uia.polls == 0


class TestEUnexpectedIsCaughtAndCounted:
    def test_get_focused_element_e_unexpected_counts_to_threshold(self, watchdog):
        watchdog.set_focus_callback(lambda element: None)
        watchdog.uia = FakeUIA([e_unexpected()] * FAIL_THRESHOLD)

        for i in range(1, FAIL_THRESHOLD):
            watchdog._poll_focus()
            assert watchdog._focus_fail_count == i
            assert not watchdog._needs_rebuild.is_set(), f"rebuilt too early at failure {i}"

        watchdog._poll_focus()
        assert watchdog._focus_fail_count == FAIL_THRESHOLD
        assert watchdog._needs_rebuild.is_set()

    def test_callback_e_unexpected_counts_too(self, watchdog):
        def callback(element):
            raise e_unexpected()

        watchdog.set_focus_callback(callback)
        # A new element on every poll, so the callback runs every time.
        watchdog.uia = FakeUIA([FakeElement(n) for n in range(FAIL_THRESHOLD)])

        for _ in range(FAIL_THRESHOLD):
            watchdog._poll_focus()

        assert watchdog._focus_fail_count == FAIL_THRESHOLD
        assert watchdog._needs_rebuild.is_set()

    def test_success_resets_the_count(self, watchdog):
        watchdog.set_focus_callback(lambda element: None)
        watchdog.uia = FakeUIA([e_unexpected()] * (FAIL_THRESHOLD - 1) + [FakeElement(1)])

        for _ in range(FAIL_THRESHOLD):
            watchdog._poll_focus()

        assert watchdog._focus_fail_count == 0
        assert not watchdog._needs_rebuild.is_set()

    def test_non_com_errors_do_not_count(self, watchdog):
        def callback(element):
            raise ValueError("not a COM failure")

        watchdog.set_focus_callback(callback)
        watchdog.uia = FakeUIA([FakeElement(n) for n in range(FAIL_THRESHOLD + 1)])

        for _ in range(FAIL_THRESHOLD + 1):
            watchdog._poll_focus()

        assert watchdog._focus_fail_count == 0
        assert not watchdog._needs_rebuild.is_set()

    def test_persistent_e_unexpected_rebuilds_with_exponential_backoff(
        self, watchdog, pump_calls, monkeypatch
    ):
        clients = []

        def create_uia():
            if len(clients) == 3:
                watchdog.is_running.clear()
            uia = FakeUIA([e_unexpected()] * 100)
            clients.append(uia)
            return uia

        waits = []
        monkeypatch.setattr(watchdog, "_create_uia", create_uia)
        monkeypatch.setattr(
            watchdog.is_running, "wait", lambda timeout: waits.append(timeout) or False
        )
        watchdog.set_focus_callback(lambda element: None)
        watchdog.is_running.set()

        watchdog._run()

        assert [c.polls for c in clients[:3]] == [FAIL_THRESHOLD] * 3, (
            "each client must be rebuilt after exactly FAIL_THRESHOLD errors"
        )
        assert waits == [1.0, 2.0, 4.0]
        assert pump_calls == []
