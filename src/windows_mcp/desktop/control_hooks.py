"""Fast physical-input hook callbacks; never wait for UI or MCP work."""

import ctypes
import time
from typing import Any

from windows_mcp.desktop.control_win32 import (
    _CHORD,
    _KeyHookData,
    _MouseHookData,
    _key,
    _mouse_button,
    _read_raw_mouse,
    _user32,
)


def physical_mouse(owner: Any, code: int, wparam: int, lparam: int) -> int:
    """Handle a physical mouse hook callback without waiting on other threads."""
    if code < 0:
        return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)
    valid = False
    button = None
    try:
        data = ctypes.cast(lparam, ctypes.POINTER(_MouseHookData)).contents
        if data.flags & 1:  # LL mouse injection flags.
            return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)
        valid = True
        owner._last_physical_event = time.monotonic()
        if wparam == 0x200 and owner._suppress:
            owner._fast_pending = True  # Pause AI before queued movement is processed.
            owner.input_ledger.block_new()
        button = _mouse_button(wparam, data.mouseData)
        if button:
            if button[1]:
                owner._mouse_down.add(button[0])
            else:
                owner._mouse_down.discard(button[0])
        owner._queue(("point", data.pt.x, data.pt.y, int(wparam)))
        if owner._suppress and not owner._emergency:
            if time.monotonic() <= owner._deadline:
                return 1
            owner._fail_open()
    except Exception:
        owner._fail_open()
    if valid and button:
        if button[1]:
            owner._delivered_mouse.add(button[0])
        else:
            owner._delivered_mouse.discard(button[0])
    return _user32.CallNextHookEx(owner._mouse_hook, code, wparam, lparam)


def physical_key(owner: Any, code: int, wparam: int, lparam: int) -> int:
    """Track each physical key independently and honor the takeover chord."""
    if code < 0:
        return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)
    vk = None
    down = False
    try:
        data = ctypes.cast(lparam, ctypes.POINTER(_KeyHookData)).contents
        if data.flags & 0x10:  # LLKHF_INJECTED: AI SendInput stays usable.
            return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)
        owner._last_physical_event = time.monotonic()
        vk = int(data.vkCode)
        down = wparam in (0x100, 0x104)
        was_down = vk in owner._pressed
        if down:
            owner._pressed.add(vk)
        else:
            owner._pressed.discard(vk)
        if vk in owner._quarantine:
            if not down:
                owner._quarantine.discard(vk)
                owner._queue(("key",))  # Idle starts after the last chord key is released.
            return 1
        if (
            down
            and vk == 0x08
            and not was_down
            and not owner._fast_takeover
            and owner._suppress
            and _CHORD.issubset({_key(held) for held in owner._pressed})
        ):
            owner._quarantine.update(held for held in owner._pressed if _key(held) in _CHORD)
            owner._fast_takeover = True
            owner._suppress = False  # Release first, then tell the coordinator.
            owner.input_ledger.block_new()
            owner._queue(("hotkey",))
            return 1
        owner._queue(("key",))
        if owner._suppress and not owner._emergency:
            if time.monotonic() <= owner._deadline:
                return 1
            owner._fail_open()
    except Exception:
        owner._fail_open()
    if vk is not None and down:
        owner._delivered_keys.add(vk)
    elif vk is not None:
        owner._delivered_keys.discard(vk)
    return _user32.CallNextHookEx(owner._key_hook, code, wparam, lparam)


def raw_input(owner: Any, lparam: int) -> None:
    """Queue relative mouse data from a device, independent of injected input."""
    raw = _read_raw_mouse(lparam)
    if raw is None:
        return  # Null devices can be touchpads; do not infer source.
    owner._last_physical_event = time.monotonic()
    if owner._suppress:
        owner._fast_pending = True
        owner.input_ledger.block_new()
    owner._queue(("raw", *raw))
