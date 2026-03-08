from unittest.mock import patch

import pytest

from windows_mcp.desktop.service import Desktop, _VK_MAP


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestKeyHold:
    @patch("windows_mcp.desktop.service.uia")
    def test_press_single_key(self, mock_uia, desktop):
        result = desktop.key_hold("down", ["shift"])
        assert "Pressed" in result
        assert "shift" in result
        mock_uia.PressKey.assert_called_once()

    @patch("windows_mcp.desktop.service.uia")
    def test_release_single_key(self, mock_uia, desktop):
        result = desktop.key_hold("up", ["ctrl"])
        assert "Released" in result
        assert "ctrl" in result
        mock_uia.ReleaseKey.assert_called_once()

    @patch("windows_mcp.desktop.service.uia")
    def test_press_multiple_keys(self, mock_uia, desktop):
        result = desktop.key_hold("down", ["shift", "ctrl", "alt"])
        assert "Pressed" in result
        assert mock_uia.PressKey.call_count == 3

    @patch("windows_mcp.desktop.service.uia")
    def test_single_character_key(self, mock_uia, desktop):
        result = desktop.key_hold("down", ["a"])
        assert "Pressed" in result
        assert "a" in result
        call_args = mock_uia.PressKey.call_args
        assert call_args[0][0] == ord("A")

    def test_unknown_key_returns_error(self, desktop):
        result = desktop.key_hold("down", ["nonexistent_key_xyz"])
        assert "Error" in result
        assert "Unknown key" in result
        assert "nonexistent_key_xyz" in result

    def test_unknown_key_lists_available(self, desktop):
        result = desktop.key_hold("down", ["invalidkey"])
        assert "Available keys" in result
        assert "shift" in result

    @patch("windows_mcp.desktop.service.uia")
    def test_key_aliases(self, mock_uia, desktop):
        """ctrl and control should both work."""
        result1 = desktop.key_hold("down", ["ctrl"])
        result2 = desktop.key_hold("down", ["control"])
        assert "Error" not in result1
        assert "Error" not in result2

    @patch("windows_mcp.desktop.service.uia")
    def test_case_insensitive(self, mock_uia, desktop):
        result = desktop.key_hold("down", ["SHIFT"])
        assert "Pressed" in result
        assert "Error" not in result

    def test_vk_map_has_essential_keys(self):
        essential = ["shift", "ctrl", "alt", "enter", "tab", "escape", "space", "f1", "f12"]
        for key in essential:
            assert key in _VK_MAP, f"Missing essential key: {key}"
