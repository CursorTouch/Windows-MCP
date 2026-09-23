"""Win32 SendInput based keyboard and human-like input primitives.

This module intentionally does not import windows_mcp.uia. It contains a
small, local copy of the INPUT/KEYBDINPUT ABI so the tool can be used without
changing the existing desktop service.
"""

from __future__ import annotations

import ctypes
import json
import math
import random
import threading
import time
from collections.abc import Sequence
from ctypes import wintypes

# Win32 constants -----------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_BACK = 0x08
VK_TAB = 0x09
VK_CLEAR = 0x0C
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SEPARATOR = 0x6C
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_BROWSER_BACK = 0xA6
VK_BROWSER_FORWARD = 0xA7
VK_BROWSER_REFRESH = 0xA8
VK_BROWSER_STOP = 0xA9
VK_BROWSER_SEARCH = 0xAA
VK_BROWSER_FAVORITES = 0xAB
VK_BROWSER_HOME = 0xAC
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_OEM_1 = 0xBA
VK_OEM_PLUS = 0xBB
VK_OEM_COMMA = 0xBC
VK_OEM_MINUS = 0xBD
VK_OEM_PERIOD = 0xBE
VK_OEM_2 = 0xBF
VK_OEM_3 = 0xC0
VK_OEM_4 = 0xDB
VK_OEM_5 = 0xDC
VK_OEM_6 = 0xDD
VK_OEM_7 = 0xDE
VK_OEM_8 = 0xDF
VK_OEM_102 = 0xE2

# Canonical keys exposed by the tool. Aliases are normalized below.
KEY_NAME_TABLE: dict[str, int] = {
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): 0x30 + number for number in range(10)},
    **{f"f{number}": 0x70 + number - 1 for number in range(1, 25)},
    "backspace": VK_BACK,
    "tab": VK_TAB,
    "clear": VK_CLEAR,
    "enter": VK_RETURN,
    "esc": VK_ESCAPE,
    "space": VK_SPACE,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "end": VK_END,
    "home": VK_HOME,
    "left": VK_LEFT,
    "up": VK_UP,
    "right": VK_RIGHT,
    "down": VK_DOWN,
    "insert": VK_INSERT,
    "delete": VK_DELETE,
    "ctrl": VK_CONTROL,
    "lctrl": VK_LCONTROL,
    "rctrl": VK_RCONTROL,
    "alt": VK_MENU,
    "lalt": VK_LMENU,
    "ralt": VK_RMENU,
    "shift": VK_SHIFT,
    "lshift": VK_LSHIFT,
    "rshift": VK_RSHIFT,
    "win": VK_LWIN,
    "lwin": VK_LWIN,
    "rwin": VK_RWIN,
    "apps": VK_APPS,
    "capslock": VK_CAPITAL,
    "numlock": VK_NUMLOCK,
    "scrolllock": VK_SCROLL,
    "printscreen": VK_SNAPSHOT,
    "pause": VK_PAUSE,
    "multiply": VK_MULTIPLY,
    "add": VK_ADD,
    "separator": VK_SEPARATOR,
    "subtract": VK_SUBTRACT,
    "decimal": VK_DECIMAL,
    "divide": VK_DIVIDE,
    "numpad0": 0x60,
    "numpad1": 0x61,
    "numpad2": 0x62,
    "numpad3": 0x63,
    "numpad4": 0x64,
    "numpad5": 0x65,
    "numpad6": 0x66,
    "numpad7": 0x67,
    "numpad8": 0x68,
    "numpad9": 0x69,
    "semicolon": VK_OEM_1,
    "plus": VK_OEM_PLUS,
    "comma": VK_OEM_COMMA,
    "minus": VK_OEM_MINUS,
    "period": VK_OEM_PERIOD,
    "slash": VK_OEM_2,
    "grave": VK_OEM_3,
    "leftbracket": VK_OEM_4,
    "backslash": VK_OEM_5,
    "rightbracket": VK_OEM_6,
    "quote": VK_OEM_7,
    "oem8": VK_OEM_8,
    "oem102": VK_OEM_102,
    "browserback": VK_BROWSER_BACK,
    "browserforward": VK_BROWSER_FORWARD,
    "browserrefresh": VK_BROWSER_REFRESH,
    "browserstop": VK_BROWSER_STOP,
    "browsersearch": VK_BROWSER_SEARCH,
    "browserfavorites": VK_BROWSER_FAVORITES,
    "browserhome": VK_BROWSER_HOME,
    "volumemute": VK_VOLUME_MUTE,
    "volumedown": VK_VOLUME_DOWN,
    "volumeup": VK_VOLUME_UP,
    "medianext": VK_MEDIA_NEXT_TRACK,
    "mediaprev": VK_MEDIA_PREV_TRACK,
    "mediastop": VK_MEDIA_STOP,
    "mediaplaypause": VK_MEDIA_PLAY_PAUSE,
}

_ALIASES = {
    "back": "backspace",
    "return": "enter",
    "escape": "esc",
    "spacebar": "space",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "del": "delete",
    "ins": "insert",
    "control": "ctrl",
    "leftctrl": "lctrl",
    "leftcontrol": "lctrl",
    "rightctrl": "rctrl",
    "rightcontrol": "rctrl",
    "menu": "alt",
    "leftalt": "lalt",
    "rightalt": "ralt",
    "option": "alt",
    "leftshift": "lshift",
    "rightshift": "rshift",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "leftwin": "lwin",
    "rightwin": "rwin",
    "contextmenu": "apps",
    "caps": "capslock",
    "prtsc": "printscreen",
    "numpad0": "numpad0",
    "numpadmultiply": "multiply",
    "numpadadd": "add",
    "numpadsubtract": "subtract",
    "numpaddecimal": "decimal",
    "numpaddivide": "divide",
    "semi": "semicolon",
    "equals": "plus",
    "equal": "plus",
    "dot": "period",
    "apostrophe": "quote",
    "singlequote": "quote",
    "backtick": "grave",
    "bracketleft": "leftbracket",
    "bracketright": "rightbracket",
    "volmute": "volumemute",
    "voldown": "volumedown",
    "volup": "volumeup",
}

_EXTENDED_VKEYS = {
    VK_RCONTROL,
    VK_RMENU,
    VK_INSERT,
    VK_DELETE,
    VK_HOME,
    VK_END,
    VK_PRIOR,
    VK_NEXT,
    VK_LEFT,
    VK_RIGHT,
    VK_UP,
    VK_DOWN,
    VK_NUMLOCK,
    VK_DIVIDE,
    VK_SNAPSHOT,
    VK_APPS,
    VK_LWIN,
    VK_RWIN,
}

_SPECIAL_SCAN_CODES = {
    VK_LSHIFT: 0x02A,
    VK_RSHIFT: 0x036,
    VK_LCONTROL: 0x01D,
    VK_RCONTROL: 0x11D,
    VK_LMENU: 0x038,
    VK_RMENU: 0x138,
    VK_LWIN: 0x15B,
    VK_RWIN: 0x15C,
    VK_NUMPAD0: 0x052,
    VK_NUMPAD1: 0x04F,
    VK_NUMPAD2: 0x050,
    VK_NUMPAD3: 0x051,
    VK_NUMPAD4: 0x04B,
    VK_NUMPAD5: 0x04C,
    VK_NUMPAD6: 0x04D,
    VK_NUMPAD7: 0x047,
    VK_NUMPAD8: 0x048,
    VK_NUMPAD9: 0x049,
    VK_DECIMAL: 0x053,
    VK_NUMLOCK: 0x145,
    VK_DIVIDE: 0x135,
    VK_MULTIPLY: 0x037,
    VK_SUBTRACT: 0x04A,
    VK_ADD: 0x04E,
}

ULONG_PTR = ctypes.c_size_t


class MouseInput(ctypes.Structure):
    """Win32 MOUSEINPUT."""

    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KeyboardInput(ctypes.Structure):
    """Win32 KEYBDINPUT."""

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HardwareInput(ctypes.Structure):
    """Win32 HARDWAREINPUT."""

    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class InputUnion(ctypes.Union):
    """Win32 INPUT union."""

    _fields_ = (("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput))


class Input(ctypes.Structure):
    """Win32 INPUT."""

    _fields_ = (("type", wintypes.DWORD), ("union", InputUnion))


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT
_user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
_user32.VkKeyScanW.restype = ctypes.c_short


def _compact_key_name(value: str) -> str:
    return "".join(character for character in value.casefold().strip() if character not in " _-")


def normalize_key_name(key: str) -> str:
    """Return the canonical name for key or raise ValueError.

    Names are case-insensitive and tolerate spaces, underscores, and dashes.
    Examples include Page_Down, left ctrl, and win.
    """

    if not isinstance(key, str) or not key.strip():
        raise ValueError("key must be a non-empty string")
    raw = key.casefold().strip()
    compact = _compact_key_name(raw)
    if raw in KEY_NAME_TABLE:
        return raw
    if compact in KEY_NAME_TABLE:
        return compact
    canonical = _ALIASES.get(compact)
    if canonical in KEY_NAME_TABLE:
        return canonical
    raise ValueError(f"unsupported key name: {key!r}")


def _vk_for_key(key: str) -> tuple[str, int]:
    canonical = normalize_key_name(key)
    return canonical, KEY_NAME_TABLE[canonical]


def _new_input(input_type: int, union: InputUnion) -> Input:
    return Input(type=input_type, union=union)


def _keyboard_input(vk: int, scan: int, flags: int) -> Input:
    return _new_input(INPUT_KEYBOARD, InputUnion(ki=KeyboardInput(vk, scan, flags, 0, 0)))


def _mouse_input(dx: int, dy: int, flags: int, mouse_data: int = 0) -> Input:
    return _new_input(
        INPUT_MOUSE,
        InputUnion(mi=MouseInput(dx, dy, mouse_data, flags, 0, 0)),
    )


def _send_inputs(*inputs: Input) -> None:
    if not inputs:
        return
    array_type = Input * len(inputs)
    packed = array_type(*inputs)
    sent = _user32.SendInput(len(inputs), packed, ctypes.sizeof(Input))
    if sent != len(inputs):
        error_code = ctypes.get_last_error()
        detail = f"Win32 error {error_code}" if error_code else "input was blocked or rejected"
        raise OSError(f"SendInput inserted {sent}/{len(inputs)} events ({detail})")


def _scan_code(vk: int) -> tuple[int, bool]:
    raw = _SPECIAL_SCAN_CODES.get(vk)
    if raw is None:
        raw = int(_user32.MapVirtualKeyW(vk, 0))
    if not raw:
        raise OSError(f"MapVirtualKeyW failed for virtual key 0x{vk:02X}")
    return raw & 0xFF, bool(raw & 0x100) or vk in _EXTENDED_VKEYS


def _key_event_flags(vk: int, is_key_up: bool = False) -> tuple[int, int]:
    scan, extended = _scan_code(vk)
    flags = 0
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if is_key_up:
        flags |= KEYEVENTF_KEYUP
    return scan, flags


def _send_key_event(vk: int, is_key_up: bool = False) -> None:
    scan, flags = _key_event_flags(vk, is_key_up)
    # With KEYEVENTF_SCANCODE, Windows reconstructs the virtual key from the
    # scan code and active keyboard layout.
    _send_inputs(_keyboard_input(vk, scan, flags))


def _send_unicode_unit(unit: int) -> None:
    _send_inputs(
        _keyboard_input(0, unit, KEYEVENTF_UNICODE),
        _keyboard_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
    )


def _send_unicode_char(character: str) -> None:
    encoded = character.encode("utf-16-le")
    for offset in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[offset : offset + 2], byteorder="little")
        _send_unicode_unit(unit)


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not math.isfinite(parsed) or parsed != int(parsed):
        raise ValueError(f"{name} must be an integer")
    return int(parsed)


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be true or false")


def _split_hotkey(keys: Sequence[str] | str) -> list[str]:
    if isinstance(keys, str):
        text = keys.strip()
        if not text:
            raise ValueError("keys must not be empty")
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("keys must be a list or a '+'-separated string") from exc
            if not isinstance(parsed, list):
                raise ValueError("keys must be a list or a '+'-separated string")
            return [str(item) for item in parsed]
        if "+" in text:
            parts = text.split("+")
        elif "," in text:
            parts = text.split(",")
        else:
            parts = text.split()
        result = [part.strip() for part in parts if part.strip()]
        if not result:
            raise ValueError("keys must contain at least one key")
        return result
    if isinstance(keys, Sequence) and not isinstance(keys, (bytes, bytearray)):
        result = [str(item) for item in keys]
        if not result:
            raise ValueError("keys must contain at least one key")
        return result
    raise ValueError("keys must be a key-list or a '+'-separated string")


def _ease_in_out_cubic(value: float) -> float:
    if value < 0.5:
        return 4.0 * value * value * value
    return 1.0 - pow(-2.0 * value + 2.0, 3) / 2.0


def _bezier_point(
    start: tuple[float, float],
    control1: tuple[float, float],
    control2: tuple[float, float],
    end: tuple[float, float],
    progress: float,
) -> tuple[float, float]:
    inverse = 1.0 - progress
    a = inverse**3
    b = 3.0 * inverse * inverse * progress
    c = 3.0 * inverse * progress * progress
    d = progress**3
    return (
        a * start[0] + b * control1[0] + c * control2[0] + d * end[0],
        a * start[1] + b * control1[1] + c * control2[1] + d * end[1],
    )


class HumanInputService:
    """Stateful keyboard and human-like pointer service.

    The service tracks keys it has pressed. release_all is available as a
    safety valve, and compound operations release keys in finally blocks even
    if an injection fails halfway through.
    """

    def __init__(self, *, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._pressed: dict[int, str] = {}
        self._lock = threading.RLock()

    @property
    def pressed_keys(self) -> tuple[str, ...]:
        """Return the currently tracked key names in insertion order."""

        with self._lock:
            return tuple(self._pressed.values())

    def key_down(self, key: str) -> str:
        """Press and hold a key until key_up or release_all is called."""

        canonical, vk = _vk_for_key(key)
        with self._lock:
            if vk in self._pressed:
                return self._pressed[vk]
            _send_key_event(vk, is_key_up=False)
            self._pressed[vk] = canonical
            return canonical

    def key_up(self, key: str) -> str:
        """Release a key, sending key-up even if it was not tracked."""

        canonical, vk = _vk_for_key(key)
        with self._lock:
            _send_key_event(vk, is_key_up=True)
            self._pressed.pop(vk, None)
            return canonical

    def release_all(self) -> list[str]:
        """Release every key currently tracked as pressed."""

        released: list[str] = []
        first_error: OSError | None = None
        with self._lock:
            for vk, name in reversed(list(self._pressed.items())):
                try:
                    _send_key_event(vk, is_key_up=True)
                except OSError as error:
                    if first_error is None:
                        first_error = error
                else:
                    self._pressed.pop(vk, None)
                    released.append(name)
        if first_error is not None:
            raise first_error
        return released

    def press(self, key: str, times: int | str = 1, interval: float | str = 0.05) -> None:
        """Tap key times, waiting interval seconds between taps."""

        canonical, vk = _vk_for_key(key)
        count = _integer(times, "times")
        wait_seconds = _finite_float(interval, "interval")
        if count < 1 or count > 1000:
            raise ValueError("times must be between 1 and 1000")
        if wait_seconds < 0 or wait_seconds > 10:
            raise ValueError("interval must be between 0 and 10 seconds")

        # Character keys are emitted as Unicode so an active IME cannot
        # reinterpret a plain press as composition text. Modifier chords and
        # named keys still use real key down/up events.
        modifier_vks = {
            VK_CONTROL,
            VK_LCONTROL,
            VK_RCONTROL,
            VK_MENU,
            VK_LMENU,
            VK_RMENU,
            VK_LWIN,
            VK_RWIN,
        }
        with self._lock:
            other_modifier_down = bool(modifier_vks.intersection(self._pressed))
            printable_single = len(canonical) == 1 and canonical in KEY_NAME_TABLE
            if printable_single and not other_modifier_down:
                shift_vks = [
                    vk_value
                    for vk_value in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT)
                    if vk_value in self._pressed
                ]
                shifted_digits = {
                    "1": "!",
                    "2": "@",
                    "3": "#",
                    "4": "$",
                    "5": "%",
                    "6": "^",
                    "7": "&",
                    "8": "*",
                    "9": "(",
                    "0": ")",
                }
                text = canonical
                if shift_vks:
                    text = (
                        canonical.upper()
                        if canonical.isalpha()
                        else shifted_digits.get(canonical, canonical)
                    )
                for index in range(count):
                    # Some controls ignore KEYEVENTF_UNICODE while a modifier
                    # is physically down. Keep the logical state held but
                    # briefly release/restore shift around the character event.
                    for vk_value in reversed(shift_vks):
                        _send_key_event(vk_value, is_key_up=True)
                    try:
                        _send_unicode_char(text)
                    finally:
                        for vk_value in shift_vks:
                            _send_key_event(vk_value, is_key_up=False)
                    self._rng_pause(0.012, 0.035)
                    if index < count - 1:
                        time.sleep(wait_seconds)
                return

        with self._lock:
            was_pressed = vk in self._pressed
            try:
                for index in range(count):
                    if not was_pressed:
                        self.key_down(canonical)
                    self._rng_pause(0.012, 0.035)
                    if not was_pressed:
                        self.key_up(canonical)
                    if index < count - 1:
                        time.sleep(wait_seconds)
            finally:
                if not was_pressed and vk in self._pressed:
                    self.key_up(canonical)

    def hotkey(self, keys: Sequence[str] | str) -> None:
        """Press a chord in order and release it in reverse order."""

        names = _split_hotkey(keys)
        canonical_names: list[str] = []
        cleanup_error: OSError | None = None
        with self._lock:
            preexisting = set(self._pressed)
            try:
                for name in names:
                    canonical, vk = _vk_for_key(name)
                    canonical_names.append(canonical)
                    if vk not in self._pressed:
                        _send_key_event(vk, is_key_up=False)
                        self._pressed[vk] = canonical
                    self._rng_pause(0.008, 0.024)
            finally:
                for canonical in reversed(canonical_names):
                    _, vk = _vk_for_key(canonical)
                    if vk not in preexisting:
                        try:
                            _send_key_event(vk, is_key_up=True)
                        except OSError as error:
                            if cleanup_error is None:
                                cleanup_error = error
                            continue
                        self._pressed.pop(vk, None)
        if cleanup_error is not None:
            raise cleanup_error

    def hold(self, key: str, seconds: float | str) -> None:
        """Press key, keep it down for seconds, then release it."""

        canonical, vk = _vk_for_key(key)
        delay = _finite_float(seconds, "seconds")
        if delay < 0 or delay > 300:
            raise ValueError("seconds must be between 0 and 300")
        preexisting = vk in self._pressed
        created = False
        try:
            if not preexisting:
                self.key_down(canonical)
                created = True
            time.sleep(delay)
        finally:
            if created and vk in self._pressed:
                self.key_up(canonical)

    def type_human(
        self,
        text: str,
        wpm: float | str = 60,
        jitter: float | str = 0.3,
        typo_rate: float | str = 0.0,
    ) -> int:
        """Type text with variable inter-key timing and optional corrections."""

        if not isinstance(text, str):
            raise ValueError("text must be a string")
        words_per_minute = _finite_float(wpm, "wpm")
        jitter_ratio = _finite_float(jitter, "jitter")
        mistake_rate = _finite_float(typo_rate, "typo_rate")
        if words_per_minute < 10 or words_per_minute > 400:
            raise ValueError("wpm must be between 10 and 400")
        if jitter_ratio < 0 or jitter_ratio > 1:
            raise ValueError("jitter must be between 0 and 1")
        if mistake_rate < 0 or mistake_rate > 1:
            raise ValueError("typo_rate must be between 0 and 1")
        if not text:
            return 0

        base_delay = 60.0 / (words_per_minute * 5.0)
        cleanup_error: OSError | None = None
        with self._lock:
            pressed_before = set(self._pressed)
            try:
                for character in text:
                    if (
                        mistake_rate > 0
                        and character.isascii()
                        and character.isalnum()
                        and self._rng.random() < mistake_rate
                    ):
                        typo = self._neighbor_character(character)
                        self._send_text_char(typo)
                        self._pause(base_delay, jitter_ratio)
                        self._tap_named_key("backspace")
                        self._pause(base_delay * 0.7, jitter_ratio)
                    self._send_text_char(character)
                    time.sleep(self._delay_for_character(base_delay, jitter_ratio, character))
            finally:
                for vk in reversed(list(self._pressed)):
                    if vk not in pressed_before:
                        try:
                            _send_key_event(vk, is_key_up=True)
                        except OSError as error:
                            if cleanup_error is None:
                                cleanup_error = error
                            continue
                        self._pressed.pop(vk, None)
        if cleanup_error is not None:
            raise cleanup_error
        return len(text)

    def move_human(
        self,
        x: int | str,
        y: int | str,
        duration: float | int | str | None = None,
        curve: str = "bezier",
        overshoot: bool | str = True,
    ) -> tuple[int, int]:
        """Move to x, y using a curved, eased, variable-speed trajectory."""

        target_x = _integer(x, "x")
        target_y = _integer(y, "y")
        curve_name = str(curve).strip().casefold() if curve is not None else "bezier"
        if curve_name not in {"bezier", "linear"}:
            raise ValueError("curve must be 'bezier' or 'linear'")
        use_overshoot = _boolean(overshoot, "overshoot")
        start_x, start_y = self.cursor_position()
        distance = math.hypot(target_x - start_x, target_y - start_y)
        if duration is None:
            effective_duration = max(0.12, min(1.1, distance / 850.0))
            effective_duration *= self._rng.uniform(0.85, 1.2)
        else:
            effective_duration = _finite_float(duration, "duration")
        if effective_duration <= 0 or effective_duration > 30:
            raise ValueError("duration must be greater than 0 and at most 30 seconds")
        if distance < 1:
            self._move_cursor(target_x, target_y)
            return target_x, target_y

        steps = max(12, min(320, int(effective_duration * 95)))
        if curve_name == "linear":
            path = self._linear_path(start_x, start_y, target_x, target_y, steps, use_overshoot)
        else:
            path = self._bezier_path(start_x, start_y, target_x, target_y, steps, use_overshoot)
        started = time.monotonic()
        base_wait = effective_duration / max(len(path), 1)
        for index, (point_x, point_y) in enumerate(path):
            self._move_cursor(point_x, point_y)
            if index < len(path) - 1:
                scheduled = effective_duration * (index + 1) / len(path)
                remaining = scheduled - (time.monotonic() - started)
                jittered = remaining + self._rng.uniform(-base_wait * 0.12, base_wait * 0.12)
                if jittered > 0:
                    time.sleep(jittered)
        self._move_cursor(target_x, target_y)
        return target_x, target_y

    def click_human(
        self,
        x: int | str | None = None,
        y: int | str | None = None,
        button: str = "left",
    ) -> tuple[int, int]:
        """Move with move_human, pause, and click with a human hold time."""

        button_name = str(button).strip().casefold()
        if button_name not in {"left", "right", "middle"}:
            raise ValueError("button must be 'left', 'right', or 'middle'")
        if (x is None) != (y is None):
            raise ValueError("x and y must both be provided or both omitted")
        if x is not None and y is not None:
            target_x, target_y = self.move_human(x, y)
        else:
            target_x, target_y = self.cursor_position()
        self._rng_pause(0.06, 0.20)
        down_flag, up_flag = self._mouse_button_flags(button_name)
        try:
            _send_inputs(_mouse_input(0, 0, down_flag))
            self._rng_pause(0.045, 0.135)
        finally:
            _send_inputs(_mouse_input(0, 0, up_flag))
        self._rng_pause(0.035, 0.12)
        return target_x, target_y

    def drag_human(
        self,
        from_x: int | str,
        from_y: int | str,
        to_x: int | str,
        to_y: int | str,
        duration: float | int | str | None = None,
        button: str = "left",
    ) -> tuple[int, int]:
        """Drag from one point to another using the human movement path."""

        start_x = _integer(from_x, "from_x")
        start_y = _integer(from_y, "from_y")
        end_x = _integer(to_x, "to_x")
        end_y = _integer(to_y, "to_y")
        button_name = str(button).strip().casefold()
        if button_name not in {"left", "right", "middle"}:
            raise ValueError("button must be 'left', 'right', or 'middle'")
        self.move_human(start_x, start_y, duration=0.12)
        self._rng_pause(0.04, 0.12)
        down_flag, up_flag = self._mouse_button_flags(button_name)
        try:
            _send_inputs(_mouse_input(0, 0, down_flag))
            self._rng_pause(0.05, 0.15)
            self.move_human(end_x, end_y, duration=duration)
        finally:
            _send_inputs(_mouse_input(0, 0, up_flag))
        self._rng_pause(0.04, 0.12)
        return end_x, end_y

    def cursor_position(self) -> tuple[int, int]:
        """Return the current cursor position in virtual-desktop coordinates."""

        point = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            raise OSError("GetCursorPos failed")
        return int(point.x), int(point.y)

    def _move_cursor(self, x: float, y: float) -> None:
        left = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = max(1, _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = max(1, _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        normalized_x = int(round((x - left) * 65535 / max(width - 1, 1)))
        normalized_y = int(round((y - top) * 65535 / max(height - 1, 1)))
        normalized_x = max(0, min(65535, normalized_x))
        normalized_y = max(0, min(65535, normalized_y))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send_inputs(_mouse_input(normalized_x, normalized_y, flags))

    def _linear_path(
        self,
        start_x: int,
        start_y: int,
        target_x: int,
        target_y: int,
        steps: int,
        overshoot: bool,
    ) -> list[tuple[float, float]]:
        end_x, end_y = float(target_x), float(target_y)
        if overshoot:
            distance = math.hypot(target_x - start_x, target_y - start_y)
            over = min(28.0, max(3.0, distance * 0.025)) * self._rng.uniform(0.75, 1.15)
            if distance:
                end_x += (target_x - start_x) / distance * over
                end_y += (target_y - start_y) / distance * over
        main_steps = max(6, int(steps * 0.82)) if overshoot else steps
        path: list[tuple[float, float]] = []
        for index in range(main_steps):
            progress = _ease_in_out_cubic(index / max(main_steps - 1, 1))
            path.append(
                (
                    start_x + (end_x - start_x) * progress,
                    start_y + (end_y - start_y) * progress,
                )
            )
        if overshoot:
            correction_steps = max(4, steps - main_steps)
            for index in range(1, correction_steps + 1):
                progress = _ease_in_out_cubic(index / correction_steps)
                path.append(
                    (
                        end_x + (target_x - end_x) * progress,
                        end_y + (target_y - end_y) * progress,
                    )
                )
        return path

    def _bezier_path(
        self,
        start_x: int,
        start_y: int,
        target_x: int,
        target_y: int,
        steps: int,
        overshoot: bool,
    ) -> list[tuple[float, float]]:
        dx = target_x - start_x
        dy = target_y - start_y
        distance = math.hypot(dx, dy)
        if distance == 0:
            return [(float(target_x), float(target_y))]
        perpendicular_x = -dy / distance
        perpendicular_y = dx / distance
        bend1 = self._rng.uniform(-0.16, 0.16) * distance
        bend2 = self._rng.uniform(-0.11, 0.11) * distance
        control1 = (
            start_x + dx * 0.28 + perpendicular_x * bend1,
            start_y + dy * 0.28 + perpendicular_y * bend1,
        )
        control2 = (
            start_x + dx * 0.72 + perpendicular_x * bend2,
            start_y + dy * 0.72 + perpendicular_y * bend2,
        )
        end_x, end_y = float(target_x), float(target_y)
        if overshoot:
            over = min(30.0, max(3.0, distance * 0.028)) * self._rng.uniform(0.75, 1.15)
            end_x += dx / distance * over
            end_y += dy / distance * over
        main_steps = max(8, int(steps * 0.82)) if overshoot else steps
        path: list[tuple[float, float]] = []
        for index in range(main_steps):
            progress = _ease_in_out_cubic(index / max(main_steps - 1, 1))
            x, y = _bezier_point(
                (start_x, start_y),
                control1,
                control2,
                (end_x, end_y),
                progress,
            )
            if 0 < index < main_steps - 1:
                x += self._rng.gauss(0, 0.22)
                y += self._rng.gauss(0, 0.22)
            path.append((x, y))
        if overshoot:
            correction_steps = max(4, steps - main_steps)
            for index in range(1, correction_steps + 1):
                progress = _ease_in_out_cubic(index / correction_steps)
                path.append(
                    (
                        end_x + (target_x - end_x) * progress,
                        end_y + (target_y - end_y) * progress,
                    )
                )
        return path

    def _send_text_char(self, character: str) -> None:
        if character == "\r":
            return
        if character == "\n":
            self._tap_named_key("enter")
            return
        if character == "\t":
            self._tap_named_key("tab")
            return
        if character == "\b":
            self._tap_named_key("backspace")
            return
        # Unicode injection keeps the requested text exact even when the
        # user has an IME (for example Chinese Pinyin) active.
        _send_unicode_char(character)

    def _tap_named_key(self, key: str) -> None:
        _, vk = _vk_for_key(key)
        with self._lock:
            self._tap_vk(vk)

    def _tap_vk(self, vk: int) -> None:
        scan, down_flags = _key_event_flags(vk)
        _, up_flags = _key_event_flags(vk, is_key_up=True)
        _send_inputs(
            _keyboard_input(vk, scan, down_flags),
            _keyboard_input(vk, scan, up_flags),
        )

    def _delay_for_character(self, base_delay: float, jitter: float, character: str) -> float:
        if character in ".!?":
            return base_delay * self._rng.uniform(2.2, 4.0)
        if character in ",;:":
            return base_delay * self._rng.uniform(1.5, 2.6)
        if character == " ":
            return base_delay * self._rng.uniform(1.0, 1.7)
        if character in "\n\r":
            return self._rng.uniform(0.18, 0.45)
        return self._random_delay(base_delay, jitter)

    def _random_delay(self, base_delay: float, jitter: float) -> float:
        low = max(0.012, base_delay * (1.0 - jitter * 1.35))
        high = max(low, base_delay * (1.0 + jitter * 1.8))
        delay = self._rng.uniform(low, high)
        if self._rng.random() < 0.015:
            delay += base_delay * self._rng.uniform(1.5, 4.0)
        return delay

    def _pause(
        self, base_delay: float, jitter: float, *, return_value: bool = False
    ) -> float | None:
        delay = self._random_delay(base_delay, jitter)
        time.sleep(delay)
        return delay if return_value else None

    def _rng_pause(self, low: float, high: float) -> None:
        time.sleep(self._rng.uniform(low, high))

    @staticmethod
    def _mouse_button_flags(button: str) -> tuple[int, int]:
        return {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }[button]

    def _neighbor_character(self, character: str) -> str:
        keyboard_neighbors = {
            "a": "sqzw",
            "b": "vghn",
            "c": "xdfv",
            "d": "serfcx",
            "e": "wsdr",
            "f": "drtgvc",
            "g": "ftyhbv",
            "h": "gyujnb",
            "i": "ujko",
            "j": "huikmn",
            "k": "jiolm",
            "l": "kop",
            "m": "njk",
            "n": "bhjm",
            "o": "iklp",
            "p": "ol",
            "q": "wa",
            "r": "edft",
            "s": "awedxz",
            "t": "rfgy",
            "u": "yhji",
            "v": "cfgb",
            "w": "qase",
            "x": "zsdc",
            "y": "tghu",
            "z": "asx",
        }
        lowered = character.casefold()
        if lowered in keyboard_neighbors:
            candidate = self._rng.choice(keyboard_neighbors[lowered])
            return candidate.upper() if character.isupper() else candidate
        if character.isdigit():
            offset = self._rng.choice((-1, 1))
            return str((int(character) + offset) % 10)
        return self._rng.choice("abcdefghijklmnopqrstuvwxyz")


__all__ = ["HumanInputService", "KEY_NAME_TABLE", "normalize_key_name"]
