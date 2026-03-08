from unittest.mock import patch

import pytest

from windows_mcp.desktop.service import Desktop


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestCursorPosition:
    @patch("windows_mcp.desktop.service.uia")
    def test_returns_coordinates(self, mock_uia, desktop):
        mock_uia.GetCursorPos.return_value = (150, 300)
        result = desktop.get_cursor_position()
        assert "150" in result
        assert "300" in result
        assert "Cursor position" in result

    @patch("windows_mcp.desktop.service.uia")
    def test_origin_coordinates(self, mock_uia, desktop):
        mock_uia.GetCursorPos.return_value = (0, 0)
        result = desktop.get_cursor_position()
        assert "(0, 0)" in result

    @patch("windows_mcp.desktop.service.uia")
    def test_large_coordinates(self, mock_uia, desktop):
        mock_uia.GetCursorPos.return_value = (3840, 2160)
        result = desktop.get_cursor_position()
        assert "3840" in result
        assert "2160" in result

    @patch("windows_mcp.desktop.service.uia")
    def test_negative_coordinates(self, mock_uia, desktop):
        """Multi-monitor setups can have negative coordinates."""
        mock_uia.GetCursorPos.return_value = (-500, 200)
        result = desktop.get_cursor_position()
        assert "-500" in result
        assert "200" in result
