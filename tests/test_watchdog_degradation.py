"""Regression tests for issue #374 — a failing watchdog must not kill startup.

`windows_mcp/watchdog/event_handlers.py` builds the UIA client at module scope
and evaluates `UIA.IUIAutomation*EventHandler` when the handler classes are
created, so merely importing the watchdog constructs COM. When the comtypes
generated-module cache is mid-regeneration, that import raises and used to
abort the whole server: every tool went down because an optional component
could not start.

The watchdog only refreshes cached UI state, and is already optional via
WINDOWS_MCP_WATCHDOG, so any failure setting it up should degrade to running
without it.
"""

import logging
import sys
import types

import pytest

import windows_mcp.__main__ as cli

WATCHDOG_MODULE = "windows_mcp.watchdog.service"


class FakeWatchDog:
    """Stand-in for the real WatchDog, which would construct COM on import."""

    def __init__(self, fail_on_start=False):
        self.fail_on_start = fail_on_start
        self.focus_callback = None
        self.started = False
        self.stopped = False

    def set_focus_callback(self, callback):
        self.focus_callback = callback

    def start(self):
        if self.fail_on_start:
            raise OSError("STA thread refused to start")
        self.started = True

    def stop(self):
        self.stopped = True


class FakeDesktop:
    def __init__(self):
        self.tree = types.SimpleNamespace(on_focus_change=lambda sender: None)


@pytest.fixture
def desktop():
    return FakeDesktop()


@pytest.fixture(autouse=True)
def watchdog_enabled(monkeypatch):
    """Opt in for these tests; the real env var must not leak in either way."""
    monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", "on")


def install_fake_module(monkeypatch, watchdog_factory):
    """Make `from windows_mcp.watchdog.service import WatchDog` yield a fake.

    Importing the real module builds COM, which is the very thing under test.
    """
    module = types.ModuleType(WATCHDOG_MODULE)
    module.WatchDog = watchdog_factory
    monkeypatch.setitem(sys.modules, WATCHDOG_MODULE, module)


class TestFailuresDegrade:
    def test_import_failure_does_not_abort_startup(self, desktop, monkeypatch, caplog):
        # A None entry in sys.modules makes the import statement raise
        # ImportError, standing in for a half-written comtypes.gen module.
        monkeypatch.setitem(sys.modules, WATCHDOG_MODULE, None)

        with caplog.at_level(logging.WARNING, logger="windows_mcp.__main__"):
            assert cli._start_watchdog(desktop) is None

        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_construction_failure_does_not_abort_startup(self, desktop, monkeypatch, caplog):
        def explode():
            raise AttributeError(
                "module 'comtypes.gen.UIAutomationClient' has no attribute 'IUIAutomation'"
            )

        install_fake_module(monkeypatch, explode)

        with caplog.at_level(logging.WARNING, logger="windows_mcp.__main__"):
            assert cli._start_watchdog(desktop) is None

        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_failed_start_is_stopped_and_dropped(self, desktop, monkeypatch, caplog):
        """A watchdog that constructs but fails to start must not be left running."""
        built = []

        def factory():
            watchdog = FakeWatchDog(fail_on_start=True)
            built.append(watchdog)
            return watchdog

        install_fake_module(monkeypatch, factory)

        with caplog.at_level(logging.WARNING, logger="windows_mcp.__main__"):
            assert cli._start_watchdog(desktop) is None

        assert built[0].stopped, "a half-started watchdog must be stopped"


class TestHealthyPath:
    def test_watchdog_is_wired_and_started(self, desktop, monkeypatch):
        install_fake_module(monkeypatch, FakeWatchDog)

        watchdog = cli._start_watchdog(desktop)

        assert isinstance(watchdog, FakeWatchDog)
        assert watchdog.started
        assert watchdog.focus_callback == desktop.tree.on_focus_change

    def test_no_warning_when_healthy(self, desktop, monkeypatch, caplog):
        install_fake_module(monkeypatch, FakeWatchDog)

        with caplog.at_level(logging.WARNING, logger="windows_mcp.__main__"):
            cli._start_watchdog(desktop)

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestDisabled:
    @pytest.mark.parametrize("value", ["off", "0", "false", "no", "disabled", "OFF", " off "])
    def test_disabled_never_imports_the_watchdog(self, desktop, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", value)
        # Would raise ImportError if the disabled path tried to import at all.
        monkeypatch.setitem(sys.modules, WATCHDOG_MODULE, None)

        assert cli._start_watchdog(desktop) is None

    def test_off_by_default_never_imports_the_watchdog(self, desktop, monkeypatch):
        """The watchdog is opt-in, so an unset env var must not even import it."""
        monkeypatch.delenv("WINDOWS_MCP_WATCHDOG", raising=False)
        monkeypatch.setitem(sys.modules, WATCHDOG_MODULE, None)

        assert cli._start_watchdog(desktop) is None

    def test_explicit_opt_in_starts_it(self, desktop, monkeypatch):
        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", "on")
        install_fake_module(monkeypatch, FakeWatchDog)

        assert cli._start_watchdog(desktop) is not None
