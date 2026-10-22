"""Shortcut key names and MultiSelect input validation (#483).

Natural names like "comma" must work, SendKeys syntax characters must be escaped,
and bad input must fail before any key is pressed.
"""

from unittest.mock import MagicMock

import pytest

import windows_mcp.desktop.service as service
from windows_mcp.desktop.service import Desktop, _shortcut_to_sendkeys


@pytest.mark.parametrize(
    ("shortcut", "expected"),
    [
        ("ctrl+c", "{ctrl}c"),
        ("ctrl+comma", "{ctrl},"),
        ("ctrl+,", "{ctrl},"),
        ("ctrl+shift+minus", "{ctrl}{shift}-"),
        ("ctrl+plus", "{ctrl}="),
        ("ctrl+backslash", "{ctrl}\\"),
        ("ctrl+Bracketleft", "{ctrl}["),
        ("alt+(", "{alt}{(}"),
        ("ctrl+shift+)", "{ctrl}{shift}{)}"),
        ("win+backspace", "{win}{Back}"),
        (" ctrl + period ", "{ctrl}."),
    ],
)
def test_shortcut_to_sendkeys(shortcut, expected):
    assert _shortcut_to_sendkeys(shortcut) == expected


@pytest.mark.parametrize("bad", ["", "ctrl++", "+a", "ctrl+ +s"])
def test_empty_key_part_is_rejected(bad):
    with pytest.raises(ValueError):
        _shortcut_to_sendkeys(bad)


def _desktop(monkeypatch):
    owner = MagicMock()
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    return Desktop.__new__(Desktop), owner


def test_shortcut_rejects_before_any_input(monkeypatch):
    desktop, _ = _desktop(monkeypatch)
    send = MagicMock()
    monkeypatch.setattr(service.uia, "SendKeys", send)
    with pytest.raises(ValueError):
        desktop.shortcut("ctrl++")
    send.assert_not_called()
    desktop.shortcut("ctrl+comma")
    send.assert_called_once_with("{ctrl},", interval=0.01)


@pytest.mark.parametrize("locs", [[[100]], [[1, 2, 3]], [{"x": 1}], [[10, 10], [5]]])
def test_multi_select_validates_before_pressing_ctrl(monkeypatch, locs):
    desktop, owner = _desktop(monkeypatch)
    click = MagicMock()
    monkeypatch.setattr(service.uia, "Click", click)
    with pytest.raises(ValueError):
        desktop.multi_select(press_ctrl=True, locs=locs)
    owner.input_ledger.press.assert_not_called()
    click.assert_not_called()


def test_multi_select_valid_points(monkeypatch):
    desktop, owner = _desktop(monkeypatch)
    click = MagicMock()
    monkeypatch.setattr(service.uia, "Click", click)
    monkeypatch.setattr(service, "sleep", lambda _s: None)
    desktop.multi_select(press_ctrl=True, locs=[[1, 2], (3, 4)])
    assert [c.args[:2] for c in click.call_args_list] == [(1, 2), (3, 4)]
    owner.input_ledger.press.assert_called_once()
    owner.input_ledger.release.assert_called_once_with("key:ctrl")
