from unittest.mock import patch

import pytest

from windows_mcp.desktop.service import Desktop, _HIGHLIGHT_COLORS


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestHighlightRegion:
    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.ctypes")
    def test_success(self, mock_ctypes, mock_sleep, desktop):
        result = desktop.highlight_region([100, 200], [300, 400], duration=1.0, color="red")
        assert "Highlighted" in result
        assert "100" in result
        assert "200" in result
        assert "300x400" in result
        assert "red" in result
        mock_sleep.assert_called_once_with(1.0)

    def test_invalid_loc(self, desktop):
        result = desktop.highlight_region([100], [300, 400])
        assert "Error" in result
        assert "loc" in result

    def test_invalid_size(self, desktop):
        result = desktop.highlight_region([100, 200], [300])
        assert "Error" in result
        assert "size" in result

    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.ctypes")
    def test_all_colors(self, mock_ctypes, mock_sleep, desktop):
        for color in ("red", "green", "blue", "yellow"):
            result = desktop.highlight_region([0, 0], [100, 100], color=color)
            assert "Error" not in result

    def test_highlight_colors_map(self):
        assert "red" in _HIGHLIGHT_COLORS
        assert "green" in _HIGHLIGHT_COLORS
        assert "blue" in _HIGHLIGHT_COLORS
        assert "yellow" in _HIGHLIGHT_COLORS
