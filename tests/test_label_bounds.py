import asyncio
from unittest.mock import MagicMock

import pytest

from tests.test_multi_tools import FakeMCP, make_desktop_with_tree_state


def _input_tools(desktop):
    from windows_mcp.tools.input import register

    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


def test_negative_label_raises_instead_of_indexing_from_the_end():
    desktop = make_desktop_with_tree_state()

    # Without the guard this returned (80, 80), the last scrollable node,
    # and Click/Type then reported success against the wrong element.
    with pytest.raises(IndexError, match="Label -1 out of range"):
        desktop.get_coordinates_from_label(-1)


def test_negative_label_in_bulk_resolution_raises():
    desktop = make_desktop_with_tree_state()

    with pytest.raises(IndexError, match="Label -2 out of range"):
        desktop.get_coordinates_from_labels([0, -2])


def test_valid_labels_still_resolve():
    desktop = make_desktop_with_tree_state()

    assert desktop.get_coordinates_from_label(0) == (10, 10)
    assert desktop.get_coordinates_from_label(2) == (80, 80)
    assert desktop.get_coordinates_from_labels([0, 1, 2]) == [(10, 10), (40, 40), (80, 80)]


def test_labels_above_range_still_raise():
    desktop = make_desktop_with_tree_state()

    with pytest.raises(IndexError, match="Label 3 out of range"):
        desktop.get_coordinates_from_label(3)


def test_click_tool_reports_the_label_as_out_of_range():
    desktop = make_desktop_with_tree_state()
    # Keep the real tree state but stub the pointer: a regression here must not
    # move the mouse of whoever runs the suite.
    desktop.click = MagicMock()

    tools = _input_tools(desktop)
    with pytest.raises(ValueError, match="Failed to find element with label -1"):
        asyncio.run(tools["Click"](label=-1))

    desktop.click.assert_not_called()
