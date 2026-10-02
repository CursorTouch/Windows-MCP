"""Hook replacement and fail-open behavior at the Win32 monitor boundary."""

from collections import deque
from types import SimpleNamespace

import pytest

from windows_mcp.desktop import control, control_win32
from windows_mcp.desktop.control_ledger import InputUnavailable


def _fake_user32(handles, events):
    hooks = deque(handles)

    def install(kind, callback, instance, thread_id):
        handle = hooks.popleft()
        events.append(("install", kind, handle))
        return handle

    def unhook(handle):
        events.append(("unhook", handle))
        return 1

    def register_raw(devices, count, size):
        events.append(("raw", count))
        return 1

    return SimpleNamespace(
        SetWindowsHookExW=install,
        UnhookWindowsHookEx=unhook,
        RegisterClassExW=lambda wc: 1,
        CreateWindowExW=lambda *args: 777,
        RegisterRawInputDevices=register_raw,
        PeekMessageW=lambda *args: 0,
        TranslateMessage=lambda *args: 1,
        DispatchMessageW=lambda *args: 1,
        DefWindowProcW=lambda *args: 0,
        DestroyWindow=lambda hwnd: events.append(("destroy", hwnd)),
        UnregisterClassW=lambda *args: events.append(("unregister",)),
    )


def _run_monitor(monkeypatch, handles, *, active=False):
    events = []
    user32 = _fake_user32(handles, events)
    monkeypatch.setattr(control, "_user32", user32)
    monkeypatch.setattr(control_win32, "_user32", user32)
    clock = [100.0]
    monkeypatch.setattr(control_win32.time, "monotonic", lambda: clock[0])
    owner = control.ControlCoordinator()
    releases = []
    if active:
        with owner._lock:
            owner._set_locked("ready")
        owner.begin_call("Smoke")
        owner.input_ledger.press(
            "key:shift",
            lambda: releases.append("down"),
            lambda: releases.append("up"),
            lambda: False,
        )
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        clock[0] += 5.01
        if len(sleeps) == 2:
            owner._stop.set()

    monkeypatch.setattr(control_win32.time, "sleep", advance)
    control_win32.run_input_monitor(owner)
    return owner, events, sleeps, releases


def test_hook_rotation_replaces_only_after_both_installations(monkeypatch):
    events = []
    monkeypatch.setattr(control, "_user32", _fake_user32([11, 12, 21, 22], events))
    owner = control.ControlCoordinator()
    owner._mouse_callback = owner._key_callback = object()
    owner._install_hooks(None)
    owner._install_hooks(None)
    assert events == [
        ("install", 14, 11),
        ("install", 13, 12),
        ("install", 14, 21),
        ("install", 13, 22),
        ("unhook", 11),
        ("unhook", 12),
    ]
    assert (owner._mouse_hook, owner._key_hook) == (21, 22)


def test_input_monitor_rotates_after_interval_and_cleans_hooks(monkeypatch):
    owner, events, sleeps, _ = _run_monitor(monkeypatch, [11, 12, 21, 22])
    assert owner._startup_error is None
    assert len(sleeps) == 2
    assert events.index(("install", 14, 21)) < events.index(("unhook", 11))
    assert events.index(("install", 13, 22)) < events.index(("unhook", 12))
    assert events[-5:] == [
        ("unhook", 21),
        ("unhook", 22),
        ("raw", 1),  # RIDEV_REMOVE
        ("destroy", 777),
        ("unregister",),
    ]


def test_hook_install_partial_failure_preserves_old_and_fails_open(monkeypatch):
    owner, events, _, releases = _run_monitor(monkeypatch, [11, 12, 21, 0], active=True)
    assert owner._startup_error is not None
    assert events.index(("unhook", 21)) < events.index(("unhook", 11))
    assert events.index(("unhook", 21)) < events.index(("unhook", 12))
    assert not owner._suppress
    assert owner.status()["state"] == "unavailable"
    assert releases == ["down", "up"]


def test_input_monitor_rotation_failure_releases_physical_input(monkeypatch):
    owner, events, _, releases = _run_monitor(monkeypatch, [11, 12, 0, 0], active=True)
    assert owner._emergency and not owner._suppress
    assert ("unhook", 11) in events and ("unhook", 12) in events
    owner.status()  # The watchdog/status path clears outstanding AI holds.
    assert releases == ["down", "up"]
    with pytest.raises(InputUnavailable):
        owner.input_ledger.press("key:shift", lambda: None, lambda: None, lambda: False)
