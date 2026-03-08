from unittest.mock import MagicMock, patch

import pytest

from windows_mcp.desktop.service import Desktop


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestWaitForChange:
    def test_invalid_region(self, desktop):
        result = desktop.wait_for_change([100, 200])
        assert "Error" in result
        assert "region" in result

    @patch("windows_mcp.desktop.service.time")
    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_change_detected(self, mock_grab, mock_sleep, mock_time, desktop):
        """Should detect change when pixels differ beyond threshold."""
        baseline_img = MagicMock()
        baseline_img.getdata.return_value = [(0, 0, 0)] * 100

        changed_img = MagicMock()
        # Change 50% of pixels
        changed_img.getdata.return_value = [(255, 255, 255)] * 50 + [(0, 0, 0)] * 50

        mock_grab.grab.side_effect = [baseline_img, changed_img]
        mock_time.side_effect = [0.0, 0.0, 0.6]

        result = desktop.wait_for_change([0, 0, 10, 10], timeout=5.0, threshold=0.05)
        assert "Change detected" in result
        assert "50.0%" in result

    @patch("windows_mcp.desktop.service.time")
    @patch("windows_mcp.desktop.service.sleep")
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_timeout(self, mock_grab, mock_sleep, mock_time, desktop):
        """Should timeout when no significant change occurs."""
        same_img = MagicMock()
        same_img.getdata.return_value = [(100, 100, 100)] * 100

        mock_grab.grab.return_value = same_img
        # baseline capture at t=0, then poll at t=0.5, t=1.0, ... until timeout
        mock_time.side_effect = [0.0, 0.0, 0.5, 0.5, 1.0, 1.0, 1.5, 1.5, 2.1]

        result = desktop.wait_for_change(
            [0, 0, 10, 10], timeout=2.0, threshold=0.05, poll_interval=0.5
        )
        assert "Timeout" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_capture_failure(self, mock_grab, desktop):
        mock_grab.grab.side_effect = OSError("No display")
        result = desktop.wait_for_change([0, 0, 100, 100])
        assert "Error" in result
        assert "baseline" in result
