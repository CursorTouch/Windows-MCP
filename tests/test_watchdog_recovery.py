"""Tests for the WatchDog self-healing reconnect logic (issue #332).

These exercise the pure failure-tracking/rebuild-signalling behavior of the
event handlers and the WatchDog's rebuild bookkeeping without going through a
live UIA event pump (that needs a real desktop and isn't practical to unit
test).
"""

import comtypes
import pytest

from windows_mcp.watchdog.event_handlers import FocusChangedEventHandler
from windows_mcp.watchdog.service import FAIL_THRESHOLD, WatchDog


def _com_error():
    return comtypes.COMError(-2147220991, "boom", (None, None, None, 0, None))


class TestFocusFailureTracking:
    def test_success_keeps_fail_count_at_zero(self):
        watchdog = WatchDog()
        watchdog.set_focus_callback(lambda sender: None)
        handler = FocusChangedEventHandler(watchdog)

        handler.HandleFocusChangedEvent(sender=object())

        assert watchdog._focus_fail_count == 0
        assert not watchdog._needs_rebuild.is_set()

    def test_success_resets_fail_count_after_prior_failures(self):
        watchdog = WatchDog()
        handler = FocusChangedEventHandler(watchdog)

        def failing_callback(sender):
            raise _com_error()

        watchdog.set_focus_callback(failing_callback)
        for _ in range(FAIL_THRESHOLD - 1):
            handler.HandleFocusChangedEvent(sender=object())
        assert watchdog._focus_fail_count == FAIL_THRESHOLD - 1

        watchdog.set_focus_callback(lambda sender: None)
        handler.HandleFocusChangedEvent(sender=object())

        assert watchdog._focus_fail_count == 0
        assert not watchdog._needs_rebuild.is_set()

    def test_repeated_com_errors_request_rebuild_at_threshold(self):
        watchdog = WatchDog()
        handler = FocusChangedEventHandler(watchdog)

        def failing_callback(sender):
            raise _com_error()

        watchdog.set_focus_callback(failing_callback)

        for i in range(1, FAIL_THRESHOLD):
            handler.HandleFocusChangedEvent(sender=object())
            assert watchdog._focus_fail_count == i
            assert not watchdog._needs_rebuild.is_set(), f"rebuilt too early at failure {i}"

        handler.HandleFocusChangedEvent(sender=object())
        assert watchdog._focus_fail_count == FAIL_THRESHOLD
        assert watchdog._needs_rebuild.is_set()

    def test_non_com_errors_do_not_request_rebuild(self):
        watchdog = WatchDog()
        handler = FocusChangedEventHandler(watchdog)

        def failing_callback(sender):
            raise ValueError("not a COM failure")

        watchdog.set_focus_callback(failing_callback)
        for _ in range(FAIL_THRESHOLD + 1):
            handler.HandleFocusChangedEvent(sender=object())

        assert watchdog._focus_fail_count == 0
        assert not watchdog._needs_rebuild.is_set()


class TestWatchDogRebuildState:
    def test_request_rebuild_sets_event(self):
        watchdog = WatchDog()
        assert not watchdog._needs_rebuild.is_set()
        watchdog.request_rebuild()
        assert watchdog._needs_rebuild.is_set()

    def test_fresh_watchdog_has_no_live_uia_client(self):
        # The watchdog must not inherit the process-wide singleton's
        # IUIAutomation pointer - it builds its own per-run in _run().
        watchdog = WatchDog()
        assert watchdog.uia is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
