"""Hook replacement and fail-open behavior at the Win32 monitor boundary."""

from collections import deque
from types import SimpleNamespace

import pytest

from windows_mcp.desktop import control, control_win32
from windows_mcp.desktop.control_ledger import InputUnavailable


def _fake_user32(handles, events, on_idle=None):
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

    def wait_for_input(count, handle, timeout, wake_mask, flags):
        if on_idle is not None:
            on_idle(timeout, wake_mask, flags)
        return 0

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
        MsgWaitForMultipleObjectsEx=wait_for_input,
    )


def _run_monitor(monkeypatch, handles, *, active=False):
    events = []
    waits = []
    clock = [100.0]
    owner = control.ControlCoordinator()

    def advance(timeout, wake_mask, flags):
        waits.append((timeout, wake_mask, flags))
        clock[0] += 5.01
        if len(waits) == 2:
            owner._stop.set()

    user32 = _fake_user32(handles, events, on_idle=advance)
    monkeypatch.setattr(control, "_user32", user32)
    monkeypatch.setattr(control_win32, "_user32", user32)
    monkeypatch.setattr(control_win32.time, "monotonic", lambda: clock[0])
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
    # The idle path must block on Win32 input, never on a fixed sleep.
    polling = []
    monkeypatch.setattr(control_win32.time, "sleep", lambda seconds: polling.append(seconds))
    control_win32.run_input_monitor(owner)
    assert polling == []
    return owner, events, waits, releases


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
    owner, events, waits, _ = _run_monitor(monkeypatch, [11, 12, 21, 22])
    assert owner._startup_error is None
    # The pump parks on the full input queue so queued input is never missed.
    assert waits == [(10, 0x04FF, 0x0006), (10, 0x04FF, 0x0006)]
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
