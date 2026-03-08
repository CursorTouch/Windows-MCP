from unittest.mock import MagicMock, patch

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import Size


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        d.execute_command = MagicMock()
        d.get_screen_size = MagicMock(return_value=Size(width=1920, height=1080))
        return d


class TestScreenInfo:
    def test_single_monitor(self, desktop):
        desktop.execute_command.return_value = (
            "\\\\.\\DISPLAY1|1920|1080|0|0|True\n",
            0,
        )
        result = desktop.get_screen_info()
        assert "Monitors (1)" in result
        assert "1920x1080" in result
        assert "(primary)" in result

    def test_dual_monitors(self, desktop):
        desktop.execute_command.return_value = (
            "\\\\.\\DISPLAY1|1920|1080|0|0|True\n\\\\.\\DISPLAY2|2560|1440|1920|0|False\n",
            0,
        )
        result = desktop.get_screen_info()
        assert "Monitors (2)" in result
        assert "1920x1080" in result
        assert "2560x1440" in result
        assert "(primary)" in result

    def test_command_failure_fallback(self, desktop):
        desktop.execute_command.return_value = ("Error", 1)
        result = desktop.get_screen_info()
        assert "Monitors (1)" in result
        assert "1920x1080" in result

    def test_empty_output_fallback(self, desktop):
        desktop.execute_command.return_value = ("", 0)
        result = desktop.get_screen_info()
        assert "Monitors (1)" in result

    def test_exception_fallback(self, desktop):
        desktop.execute_command.side_effect = RuntimeError("PowerShell not found")
        result = desktop.get_screen_info()
        assert "Monitors (1)" in result
        assert "1920x1080" in result
