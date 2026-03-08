from unittest.mock import patch

import pytest

from windows_mcp.desktop.service import Desktop


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestMousePath:
    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.uia")
    def test_two_waypoints(self, mock_uia, mock_sleep, desktop):
        result = desktop.mouse_path([[0, 0], [100, 100]], duration=0.1)
        assert "2 waypoints" in result
        assert mock_uia.MoveTo.called

    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.uia")
    def test_multiple_waypoints(self, mock_uia, mock_sleep, desktop):
        path = [[0, 0], [50, 50], [100, 0], [150, 50]]
        result = desktop.mouse_path(path, duration=0.2)
        assert "4 waypoints" in result

    def test_single_waypoint_error(self, desktop):
        result = desktop.mouse_path([[100, 200]])
        assert "Error" in result
        assert "at least 2" in result

    def test_empty_path_error(self, desktop):
        result = desktop.mouse_path([])
        assert "Error" in result

    def test_invalid_waypoint_shape(self, desktop):
        result = desktop.mouse_path([[0, 0], [100]])
        assert "Error" in result
        assert "waypoint" in result

    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.uia")
    def test_endpoints_visited(self, mock_uia, mock_sleep, desktop):
        desktop.mouse_path([[10, 20], [30, 40]], duration=0.01)
        calls = [call[0] for call in mock_uia.MoveTo.call_args_list]
        # First point
        assert calls[0] == (10, 20)
        # Last point
        assert calls[-1] == (30, 40)
