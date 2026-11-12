"""A window that closes before or during its tree walk is left out of the capture, not retried.

The window list is taken before the tree walk, and the walk goes through the windows one at a
time, so a dialog, menu or notification can close in between. UI Automation then fails every
call on it: IUIAutomation::ElementFromHandle raises UIA_E_ELEMENTNOTAVAILABLE for a handle that
no longer names a window. Retrying cannot reach it, and a window that is gone is not a failed
capture. A window that still exists keeps the retry policy.
"""

import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest
import win32gui
from _ctypes import COMError

from windows_mcp.tree import service as tree_service
from windows_mcp.tree.budget import TreeElementBudget
from windows_mcp.tree.config import THREAD_MAX_RETRIES
from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode
from windows_mcp.uia import UIAError

DIALOG, NOTEPAD = 0x1F0A3C, 0x10012
SCREEN = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)


def _tree():
    tree = object.__new__(tree_service.Tree)
    tree.desktop = SimpleNamespace(
        get_screen_box=lambda: SCREEN,
        is_window_browser=lambda node: False,
    )
    tree.element_budget = TreeElementBudget(500)
    return tree


def _button():
    return TreeElementNode(
        name="OK",
        control_type="Button",
        window_name="Notepad",
        bounding_box=BoundingBox(left=10, top=10, right=90, bottom=40, width=80, height=30),
        center=Center(x=50, y=25),
    )


@contextmanager
def _desktop(tree, lookups_before_close, dialog_still_exists=False):
    """Patch UI Automation and IsWindow for the windows DIALOG and NOTEPAD.

    DIALOG answers its first `lookups_before_close` ControlFromHandle calls, then closes: later
    calls raise UIA_E_ELEMENTNOTAVAILABLE and IsWindow reports it gone, unless
    `dialog_still_exists`. DIALOG goes through the real get_nodes; NOTEPAD returns one button.
    Yields what was called: ControlFromHandle and IsWindow handles, the handles get_nodes was
    called for, and the backoff sleeps.
    """
    record = SimpleNamespace(lookups=[], is_window=[], walked=[], sleeps=[])
    real_get_nodes = tree_service.Tree.get_nodes

    def dialog_closed():
        return record.lookups.count(DIALOG) >= lookups_before_close

    def control_from_handle(handle):
        closed = handle == DIALOG and dialog_closed()
        record.lookups.append(handle)
        if closed:
            raise COMError(
                UIAError.UIA_E_ELEMENTNOTAVAILABLE,
                "An event was unable to invoke any of the subscribers",
                (None, None, None, 0, None),
            )
        return SimpleNamespace(ClassName="Window", Name="Window")

    def is_window(handle):
        record.is_window.append(handle)
        return handle != DIALOG or dialog_still_exists or not dialog_closed()

    def get_nodes(handle, is_browser=False, wait_time=0, use_dom=False):
        record.walked.append(handle)
        if handle == NOTEPAD:
            return [_button()], [], [], None
        return real_get_nodes(tree, handle, is_browser, wait_time=wait_time, use_dom=use_dom)

    with (
        mock.patch.object(tree_service, "ControlFromHandle", control_from_handle),
        mock.patch.object(win32gui, "IsWindow", is_window),
        mock.patch.object(tree, "get_nodes", get_nodes, create=True),
        mock.patch.object(tree_service, "sleep", record.sleeps.append),
    ):
        yield record


@pytest.mark.parametrize(
    "lookups_before_close, walked",
    [(0, [NOTEPAD]), (1, [DIALOG, NOTEPAD])],
    ids=["closed-before-its-walk", "closed-during-its-walk"],
)
def test_closed_window_is_skipped_without_retries(lookups_before_close, walked):
    tree = _tree()
    with _desktop(tree, lookups_before_close) as record:
        interactive, _, _, failed_handles, _ = tree.get_window_wise_nodes(
            windows_handles=[DIALOG, NOTEPAD], active_window_flag=False
        )

    assert failed_handles == []
    assert [node.name for node in interactive] == ["OK"]
    assert record.walked == walked
    # The failed lookup is the last one: no retries, and no Name lookup for a log line.
    assert record.lookups.count(DIALOG) == lookups_before_close + 1
    assert record.sleeps == []
    # Only a window whose capture failed is checked.
    assert record.is_window == [DIALOG]


def test_capture_with_a_closed_window_succeeds(caplog):
    tree = _tree()
    with (
        _desktop(tree, lookups_before_close=1) as record,
        caplog.at_level(logging.WARNING, logger=tree_service.__name__),
    ):
        tree_state = tree.get_state(None, [DIALOG, NOTEPAD])

    assert tree_state.status is True
    assert [node.name for node in tree_state.interactive_nodes] == ["OK"]
    # No retry warnings, and no "window(s) failed to capture" warning.
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert record.sleeps == []


def test_window_that_still_exists_is_retried():
    tree = _tree()
    with _desktop(tree, lookups_before_close=1, dialog_still_exists=True) as record:
        _, _, _, failed_handles, _ = tree.get_window_wise_nodes(
            windows_handles=[DIALOG, NOTEPAD], active_window_flag=False
        )

    assert failed_handles == [DIALOG]
    assert record.walked == [DIALOG] * (THREAD_MAX_RETRIES + 1) + [NOTEPAD]
    assert record.sleeps  # backed off between the attempts
