"""Regression tests for issue #332 — backoff must apply to degraded rebuilds.

The watchdog rebuilds its UIA client when the focus pipeline goes stale, which
`_event_loop` signals by returning with `_needs_rebuild` set. That return was
treated as a clean exit and reset the backoff to 1 second, so an environment
that degrades immediately on every rebuild was retried once a second forever —
churning COM clients and emitting a warning per second, which is the log flood
in the report. Backoff now only resets after a run that actually lasted.
"""

import time
import types

import pytest

from windows_mcp.watchdog import service as watchdog_service
from windows_mcp.watchdog.service import MAX_BACKOFF_SECONDS, WatchDog


@pytest.fixture
def watchdog(monkeypatch):
    """A WatchDog with every COM touchpoint stubbed out."""
    # __init__ grabs the UIA singleton, which would build COM.
    monkeypatch.setattr(
        watchdog_service._AutomationClient,
        "instance",
        classmethod(lambda cls: types.SimpleNamespace(UIAutomationCore=object())),
    )
    monkeypatch.setattr(watchdog_service.comtypes, "CoInitialize", lambda: None)
    monkeypatch.setattr(watchdog_service.comtypes, "CoUninitialize", lambda: None)

    watchdog = WatchDog()
    monkeypatch.setattr(watchdog, "_create_uia", lambda: object())
    monkeypatch.setattr(watchdog, "_teardown_handlers", lambda: None)
    return watchdog


def run_capturing_waits(watchdog, monkeypatch, event_loop):
    """Drive _run synchronously, recording what it would have slept."""
    waits = []
    monkeypatch.setattr(watchdog, "_event_loop", event_loop)
    monkeypatch.setattr(
        watchdog.is_running, "wait", lambda timeout: waits.append(timeout) or False
    )
    watchdog.is_running.set()
    watchdog._run()
    return waits


class TestDegradedRebuildBacksOff:
    def test_immediate_degradation_escalates(self, watchdog, monkeypatch):
        """The reported failure mode: rebuild requested as soon as each run starts."""
        runs = {"n": 0}

        def event_loop():
            runs["n"] += 1
            watchdog._needs_rebuild.set()  # what FAIL_THRESHOLD COMErrors do
            if runs["n"] >= 5:
                watchdog.is_running.clear()

        waits = run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert waits == [1.0, 2.0, 4.0, 8.0], "a degraded rebuild must not reset backoff"

    def test_backoff_is_capped(self, watchdog, monkeypatch):
        runs = {"n": 0}

        def event_loop():
            runs["n"] += 1
            watchdog._needs_rebuild.set()
            if runs["n"] >= 12:
                watchdog.is_running.clear()

        waits = run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert max(waits) == MAX_BACKOFF_SECONDS
        assert waits[-1] == MAX_BACKOFF_SECONDS

    def test_exception_path_still_backs_off(self, watchdog, monkeypatch):
        runs = {"n": 0}

        def event_loop():
            runs["n"] += 1
            if runs["n"] >= 4:
                watchdog.is_running.clear()
            raise OSError("pump exploded")

        waits = run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert waits == [1.0, 2.0, 4.0]


class TestHealthyRunResetsBackoff:
    def test_a_run_that_lasted_resets_the_backoff(self, watchdog, monkeypatch):
        """An intermittent glitch after a long healthy run should retry promptly."""
        monkeypatch.setattr(watchdog_service, "HEALTHY_RUN_SECONDS", 0.05)
        runs = {"n": 0}

        def event_loop():
            runs["n"] += 1
            if runs["n"] == 2:
                time.sleep(0.06)  # this run survived long enough to count
            watchdog._needs_rebuild.set()
            if runs["n"] >= 3:
                watchdog.is_running.clear()

        waits = run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert waits == [1.0, 1.0], "a long run should return the retry delay to 1s"

    def test_stopping_does_not_wait(self, watchdog, monkeypatch):
        def event_loop():
            watchdog.is_running.clear()

        waits = run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert waits == []


class TestClientIsRebuiltEachCycle:
    def test_every_cycle_gets_a_fresh_client_and_clean_counters(self, watchdog, monkeypatch):
        """#334's contract: never reuse a client that just reported failure."""
        clients = []
        monkeypatch.setattr(watchdog, "_create_uia", lambda: clients.append(object()) or clients[-1])
        runs = {"n": 0}
        seen_counts = []

        def event_loop():
            runs["n"] += 1
            seen_counts.append(watchdog._focus_fail_count)
            watchdog._focus_fail_count = 99  # simulate failures during the run
            watchdog._needs_rebuild.set()
            if runs["n"] >= 3:
                watchdog.is_running.clear()

        run_capturing_waits(watchdog, monkeypatch, event_loop)

        assert len(clients) == 3, "each cycle must build its own client"
        assert len(set(map(id, clients))) == 3
        assert seen_counts == [0, 0, 0], "failure counters must reset per cycle"
