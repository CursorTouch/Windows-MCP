"""Regression tests for issue #301 — UIA child enumeration returning empty.

On Windows ARM64 under x86-emulated Python the UIA tree walker returns no
children for any element while `FindAll` on the same element works. Two guards
cover that: `Control.GetChildren` falls back to `FindAll`, and
`Desktop.is_overlay_window` no longer treats "childless" alone as an overlay
(which had turned degraded enumeration into an empty desktop).
"""

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.uia.controls import Control, _ViewWalkerState


@pytest.fixture(autouse=True)
def reset_walker_state():
    _ViewWalkerState.reset()
    yield
    _ViewWalkerState.reset()


class FakeControl:
    """Stands in for a `Control` without touching COM.

    walk_children: what the tree walker path yields.
    find_children: what the FindAll path yields.
    """

    def __init__(self, name="Window", walk_children=(), find_children=()):
        self.Name = name
        self._walk = list(walk_children)
        self._find = list(find_children)
        self.walk_calls = 0
        self.find_calls = 0

    def GetFirstChildControl(self):
        self.walk_calls += 1
        return self._walk[0] if self._walk else None

    def GetNextSiblingControl(self):
        return None

    def _FindChildren(self):
        self.find_calls += 1
        return list(self._find)

    GetChildren = Control.GetChildren


class TestGetChildrenFallback:
    def test_healthy_walker_is_used_and_costs_no_findall(self):
        child = FakeControl(name="Child")
        ctrl = FakeControl(walk_children=[child], find_children=[child])
        assert ctrl.GetChildren() == [child]
        assert ctrl.find_calls == 0
        assert not _ViewWalkerState.is_broken()

    def test_broken_walker_falls_back_to_findall(self):
        child = FakeControl(name="Child")
        ctrl = FakeControl(walk_children=[], find_children=[child])
        assert ctrl.GetChildren() == [child]
        assert _ViewWalkerState.is_broken()

    def test_once_latched_the_walker_is_skipped_entirely(self):
        child = FakeControl(name="Child")
        first = FakeControl(walk_children=[], find_children=[child])
        first.GetChildren()
        assert _ViewWalkerState.is_broken()

        later = FakeControl(walk_children=[], find_children=[child])
        assert later.GetChildren() == [child]
        assert later.walk_calls == 0, "walker should not be consulted once latched broken"

    def test_genuine_leaf_probes_at_most_max_probes_times(self):
        """A truly childless element must not pay for a FindAll forever."""
        leaves = [FakeControl(name="Leaf") for _ in range(_ViewWalkerState.MAX_PROBES + 3)]
        for leaf in leaves:
            assert leaf.GetChildren() == []
        probed = sum(leaf.find_calls for leaf in leaves)
        assert probed == _ViewWalkerState.MAX_PROBES
        assert not _ViewWalkerState.is_broken()


class TestIsOverlayWindow:
    @pytest.fixture
    def desktop(self):
        return Desktop.__new__(Desktop)

    def test_named_overlay_is_filtered(self, desktop):
        overlay = FakeControl(name="NVIDIA GeForce Overlay")
        assert desktop.is_overlay_window(overlay) is True

    def test_unnamed_childless_window_is_filtered(self, desktop):
        assert desktop.is_overlay_window(FakeControl(name="   ")) is True

    def test_named_childless_window_is_kept(self, desktop):
        """The issue #301 case: degraded enumeration must not empty the desktop."""
        window = FakeControl(name="Realtek Audio Console")
        assert desktop.is_overlay_window(window) is False

    def test_ordinary_window_is_kept(self, desktop):
        window = FakeControl(name="Notepad", walk_children=[FakeControl(name="Edit")])
        assert desktop.is_overlay_window(window) is False
