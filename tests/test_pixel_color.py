from unittest.mock import MagicMock, patch

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.utils import approximate_color_name


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestApproximateColorName:
    def test_exact_red(self):
        assert approximate_color_name(255, 0, 0) == "red"

    def test_exact_green(self):
        assert approximate_color_name(0, 128, 0) == "green"

    def test_exact_blue(self):
        assert approximate_color_name(0, 0, 255) == "blue"

    def test_exact_white(self):
        assert approximate_color_name(255, 255, 255) == "white"

    def test_exact_black(self):
        assert approximate_color_name(0, 0, 0) == "black"

    def test_near_red(self):
        assert approximate_color_name(250, 5, 5) == "red"

    def test_near_yellow(self):
        assert approximate_color_name(250, 250, 10) == "yellow"

    def test_returns_string(self):
        result = approximate_color_name(100, 100, 100)
        assert isinstance(result, str)
        assert len(result) > 0


class TestPixelColor:
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_success(self, mock_grab, desktop):
        mock_img = MagicMock()
        mock_img.getpixel.return_value = (255, 0, 0)
        mock_grab.grab.return_value = mock_img
        result = desktop.get_pixel_color([100, 200])
        assert "R=255" in result
        assert "G=0" in result
        assert "B=0" in result
        assert "#FF0000" in result
        assert "red" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_white_pixel(self, mock_grab, desktop):
        mock_img = MagicMock()
        mock_img.getpixel.return_value = (255, 255, 255)
        mock_grab.grab.return_value = mock_img
        result = desktop.get_pixel_color([0, 0])
        assert "#FFFFFF" in result
        assert "white" in result

    def test_invalid_loc_length(self, desktop):
        result = desktop.get_pixel_color([100])
        assert "Error" in result

    def test_invalid_loc_too_many(self, desktop):
        result = desktop.get_pixel_color([1, 2, 3])
        assert "Error" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_grab_exception(self, mock_grab, desktop):
        mock_grab.grab.side_effect = OSError("Screen capture failed")
        result = desktop.get_pixel_color([100, 200])
        assert "Error" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_hex_format(self, mock_grab, desktop):
        mock_img = MagicMock()
        mock_img.getpixel.return_value = (10, 20, 30)
        mock_grab.grab.return_value = mock_img
        result = desktop.get_pixel_color([50, 50])
        assert "#0A141E" in result
