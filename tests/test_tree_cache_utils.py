from _ctypes import COMError
from unittest.mock import MagicMock

from windows_mcp.tree.cache_utils import CachedControlHelper


UIA_E_ELEMENTNOTAVAILABLE = -2147220991


def _cache_request():
    request = MagicMock()
    clone = MagicMock()
    request.Clone.return_value = clone
    clone.check_request = object()
    return request


def _stale_element_error():
    return COMError(
        UIA_E_ELEMENTNOTAVAILABLE,
        "An event was unable to invoke any of the subscribers",
        (None, None, None, 0, None),
    )


def test_unavailable_element_skips_regular_children_fallback():
    node = MagicMock()
    node.Element.BuildUpdatedCache.side_effect = _stale_element_error()

    children = CachedControlHelper.get_cached_children(node, _cache_request())

    assert children == []
    node.GetChildren.assert_not_called()


def test_unavailable_element_during_fallback_is_pruned():
    node = MagicMock()
    node.Element.BuildUpdatedCache.side_effect = RuntimeError("cache provider failed")
    node.GetChildren.side_effect = _stale_element_error()

    children = CachedControlHelper.get_cached_children(node, _cache_request())

    assert children == []


def test_unexpected_cache_failure_still_uses_regular_children():
    node = MagicMock()
    expected = [MagicMock(), MagicMock()]
    node.Element.BuildUpdatedCache.side_effect = RuntimeError("cache provider failed")
    node.GetChildren.return_value = expected

    assert CachedControlHelper.get_cached_children(node, _cache_request()) == expected
