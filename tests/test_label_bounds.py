import pytest

from tests.test_multi_tools import make_desktop_with_tree_state


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
