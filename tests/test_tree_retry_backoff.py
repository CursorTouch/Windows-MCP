"""A window that still exists but fails its capture is retried, and each wait is slept once.

get_window_wise_nodes() retries such a window up to THREAD_MAX_RETRIES times. The wait before a
retry starts at 0.5 seconds and doubles each time, as its "Retrying window ... in 0.5s..." debug
line says. The window here stands for an app that is busy but alive: while it is busy, UI
Automation calls on it fail with RPC_E_SERVERCALL_RETRYLATER, which the server maps to
UIARetryableError. Every walk goes through the real get_nodes(), and the tree module's sleep()
records every wait, so a wait that is slept twice is counted twice.
"""

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

BUSY_APP = 0x2A0B7C
SCREEN = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)
# The wait before each retry: 0.5, 1 and 2 seconds with THREAD_MAX_RETRIES = 3.
BACKOFF = [0.5 * 2**retry for retry in range(THREAD_MAX_RETRIES)]


def _tree():
    tree = object.__new__(tree_service.Tree)
    tree.desktop = SimpleNamespace(
        get_screen_box=lambda: SCREEN,
        is_window_browser=lambda node: False,
    )
    tree.element_budget = TreeElementBudget(500)
    return tree


def _button(window_name):
    return TreeElementNode(
        name="OK",
        control_type="Button",
        window_name=window_name,
        bounding_box=BoundingBox(left=10, top=10, right=90, bottom=40, width=80, height=30),
        center=Center(x=50, y=25),
    )


@contextmanager
def _busy_app(tree, busy_walks):
    """Patch UI Automation for BUSY_APP, a window that still exists (IsWindow is True).

    Its first `busy_walks` walks fail: until then, ControlFromHandle raises
    RPC_E_SERVERCALL_RETRYLATER. After that, its walk finds one button. Yields the number of
    walks and the waits slept by the tree module, in order.
    """
    record = SimpleNamespace(walks=0, sleeps=[])
    real_get_nodes = tree_service.Tree.get_nodes

    def control_from_handle(handle):
        if record.walks <= busy_walks:
            raise COMError(
                UIAError.RPC_E_SERVERCALL_RETRYLATER,
                "The message filter indicated that the application is busy.",
                (None, None, None, 0, None),
            )
        return SimpleNamespace(ClassName="Window", Name="Busy app", BoundingRectangle=None)

    def get_nodes(handle, is_browser=False, wait_time=0, use_dom=False):
        record.walks += 1
        return real_get_nodes(tree, handle, is_browser, wait_time=wait_time, use_dom=use_dom)

    def tree_traversal(
        node, window_box, window_name, is_browser, interactive_nodes, *args, **kwargs
    ):
        interactive_nodes.append(_button(window_name))

    cache_requests = SimpleNamespace(create_tree_traversal_cache=lambda: SimpleNamespace())
    with (
        mock.patch.object(tree_service, "ControlFromHandle", control_from_handle),
        mock.patch.object(win32gui, "IsWindow", lambda handle: True),
        mock.patch.object(tree_service, "CacheRequestFactory", cache_requests),
        mock.patch.object(tree, "get_nodes", get_nodes, create=True),
        mock.patch.object(tree, "tree_traversal", tree_traversal),
        mock.patch.object(tree_service, "sleep", record.sleeps.append),
    ):
        yield record


@pytest.mark.parametrize("busy_walks", [1, 2], ids=["busy-for-one-walk", "busy-for-two-walks"])
def test_window_that_recovers_waits_once_before_each_retry(busy_walks):
    tree = _tree()
    with _busy_app(tree, busy_walks) as record:
        interactive, _, _, failed_handles, _ = tree.get_window_wise_nodes(
            windows_handles=[BUSY_APP], active_window_flag=False
        )

    assert failed_handles == []
    assert [node.name for node in interactive] == ["OK"]
    assert record.walks == busy_walks + 1
    assert record.sleeps == BACKOFF[:busy_walks]


def test_window_that_stays_busy_waits_once_before_each_retry():
    tree = _tree()
    with _busy_app(tree, busy_walks=THREAD_MAX_RETRIES + 1) as record:
        interactive, _, _, failed_handles, _ = tree.get_window_wise_nodes(
            windows_handles=[BUSY_APP], active_window_flag=False
        )

    assert failed_handles == [BUSY_APP]
    assert interactive == []
    assert record.walks == THREAD_MAX_RETRIES + 1
    # 0.5 + 1 + 2 = 3.5 seconds in all.
    assert record.sleeps == BACKOFF
