"""SendKeys must not leave synthetic keys held when a chord fails (#483)."""

import pytest

from windows_mcp.uia import core
from windows_mcp.uia.enums import KeyboardEventFlag, Keys


@pytest.fixture
def keyboard(monkeypatch):
    """Capture keyboard events without injecting input into the desktop."""
    events = []
    monkeypatch.setattr(core, "keybd_event", lambda *event: events.append(event))
    monkeypatch.setattr(core, "_VKtoSC", lambda key: key)
    monkeypatch.setattr(core, "SendUnicodeChar", lambda *args: None)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
    return events


def key_up(key):
    return (key, key, KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey, 0)


@pytest.mark.parametrize("text", ["{Ctrl}{comma}", "{Ctrl}a{unknown}", "{unknown 0}"])
def test_unknown_key_is_rejected_before_any_input(keyboard, text):
    with pytest.raises(ValueError, match="Unknown key name"):
        core.SendKeys(text, waitTime=0)
    assert keyboard == []


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize(
    ("modifier", "vk"),
    [
        ("Ctrl", Keys.VK_CONTROL),
        ("Shift", Keys.VK_SHIFT),
        ("Alt", Keys.VK_MENU),
        ("Win", Keys.VK_LWIN),
    ],
)
def test_chord_releases_keys_when_sleep_is_interrupted(
    monkeypatch, keyboard, error_type, modifier, vk
):
    def interrupt(seconds):
        raise error_type("interrupted")

    monkeypatch.setattr(core.time, "sleep", interrupt)
    with pytest.raises(error_type, match="interrupted"):
        core.SendKeys("{" + modifier + "}a", waitTime=0)

    assert len(keyboard) == 2
    assert keyboard[-1] == key_up(vk)


def test_failed_key_up_is_retried_before_releasing_modifier(monkeypatch, keyboard):
    failed = False

    def fail_first_release(vk, scan, flags, extra):
        nonlocal failed
        keyboard.append((vk, scan, flags, extra))
        if vk == ord("A") and flags & KeyboardEventFlag.KeyUp and not failed:
            failed = True
            raise RuntimeError("key up failed")

    monkeypatch.setattr(core, "keybd_event", fail_first_release)
    with pytest.raises(RuntimeError, match="key up failed"):
        core.SendKeys("{Ctrl}a", waitTime=0)

    assert keyboard[-3:] == [key_up(ord("A")), key_up(ord("A")), key_up(Keys.VK_CONTROL)]


def test_multiple_keys_are_released_in_reverse_order(monkeypatch, keyboard):
    def fail_on_letter(vk, scan, flags, extra):
        keyboard.append((vk, scan, flags, extra))
        if vk == ord("A") and not flags & KeyboardEventFlag.KeyUp:
            raise RuntimeError("input failed after key down")

    monkeypatch.setattr(core, "keybd_event", fail_on_letter)
    with pytest.raises(RuntimeError, match="input failed after key down"):
        core.SendKeys("{Ctrl}{Shift}a", waitTime=0)

    assert keyboard[-3:] == [key_up(ord("A")), key_up(Keys.VK_SHIFT), key_up(Keys.VK_CONTROL)]


def test_scan_code_failure_still_releases_previous_key(monkeypatch, keyboard):
    def scan_code(key):
        if key == ord("A"):
            raise RuntimeError("scan failed")
        return key

    monkeypatch.setattr(core, "_VKtoSC", scan_code)
    with pytest.raises(RuntimeError, match="scan failed"):
        core.SendKeys("{Ctrl}a", waitTime=0)

    assert keyboard[-1] == key_up(Keys.VK_CONTROL)


def test_unicode_failure_releases_held_modifier(monkeypatch, keyboard):
    def fail_unicode(*args):
        raise RuntimeError("unicode failed")

    monkeypatch.setattr(core, "SendUnicodeChar", fail_unicode)
    with pytest.raises(RuntimeError, match="unicode failed"):
        core.SendKeys("{Ctrl}{日}", waitTime=0)

    assert keyboard[-1] == key_up(Keys.VK_CONTROL)


def test_cleanup_continues_after_a_release_failure(monkeypatch, keyboard):
    def fail_input(vk, scan, flags, extra):
        keyboard.append((vk, scan, flags, extra))
        if vk == ord("A"):
            if flags & KeyboardEventFlag.KeyUp:
                raise RuntimeError("release failed")
            raise ValueError("original failure")

    monkeypatch.setattr(core, "keybd_event", fail_input)
    with pytest.raises(ValueError, match="original failure"):
        core.SendKeys("{Ctrl}{Shift}a", waitTime=0)

    assert keyboard[-3:] == [key_up(ord("A")), key_up(Keys.VK_SHIFT), key_up(Keys.VK_CONTROL)]


def test_released_keys_are_not_released_again(monkeypatch, keyboard):
    def fail_unicode(*args):
        raise RuntimeError("later failure")

    monkeypatch.setattr(core, "SendUnicodeChar", fail_unicode)
    with pytest.raises(RuntimeError, match="later failure"):
        core.SendKeys("{Ctrl}ab", waitTime=0)

    assert keyboard.count(key_up(Keys.VK_CONTROL)) == 1
    assert keyboard.count(key_up(ord("A"))) == 1
    assert len(keyboard) == 4


def test_successful_chord_keeps_existing_event_order(keyboard):
    core.SendKeys("{Ctrl}a", waitTime=0)

    down = KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey
    assert keyboard == [
        (Keys.VK_CONTROL, Keys.VK_CONTROL, down, 0),
        (ord("A"), ord("A"), down, 0),
        key_up(ord("A")),
        key_up(Keys.VK_CONTROL),
    ]


@pytest.mark.parametrize("text", ["{{}", "{}}", "{a 3}", "{日}", "{Ctrl}{OEM_COMMA}"])
def test_valid_braced_keys_remain_supported(keyboard, text):
    core.SendKeys(text, waitTime=0)
