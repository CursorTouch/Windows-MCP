from unittest.mock import MagicMock, patch

import pytest

from windows_mcp.desktop.service import Desktop


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        d.execute_command = MagicMock()
        return d


class TestScreenReader:
    @patch("windows_mcp.desktop.service.os")
    @patch("windows_mcp.desktop.service.tempfile")
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_success_windows_ocr(self, mock_grab, mock_tempfile, mock_os, desktop):
        mock_img = MagicMock()
        mock_grab.grab.return_value = mock_img
        mock_tmp = MagicMock()
        mock_tmp.__enter__ = MagicMock(return_value=mock_tmp)
        mock_tmp.__exit__ = MagicMock(return_value=False)
        mock_tmp.name = "C:\\temp\\ocr.png"
        mock_tempfile.NamedTemporaryFile.return_value = mock_tmp
        desktop.execute_command.return_value = ("Hello World\n", 0)

        result = desktop.read_screen_text()
        assert "OCR text" in result
        assert "Hello World" in result

    @patch("windows_mcp.desktop.service.os")
    @patch("windows_mcp.desktop.service.tempfile")
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_no_text_detected(self, mock_grab, mock_tempfile, mock_os, desktop):
        mock_img = MagicMock()
        mock_grab.grab.return_value = mock_img
        mock_tmp = MagicMock()
        mock_tmp.__enter__ = MagicMock(return_value=mock_tmp)
        mock_tmp.__exit__ = MagicMock(return_value=False)
        mock_tmp.name = "C:\\temp\\ocr.png"
        mock_tempfile.NamedTemporaryFile.return_value = mock_tmp
        desktop.execute_command.return_value = ("\n", 0)

        result = desktop.read_screen_text()
        assert "No text detected" in result

    def test_invalid_region(self, desktop):
        result = desktop.read_screen_text(region=[100, 200])
        assert "Error" in result
        assert "region" in result

    @patch("windows_mcp.desktop.service.os")
    @patch("windows_mcp.desktop.service.tempfile")
    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_region_capture(self, mock_grab, mock_tempfile, mock_os, desktop):
        mock_img = MagicMock()
        mock_grab.grab.return_value = mock_img
        mock_tmp = MagicMock()
        mock_tmp.__enter__ = MagicMock(return_value=mock_tmp)
        mock_tmp.__exit__ = MagicMock(return_value=False)
        mock_tmp.name = "C:\\temp\\ocr.png"
        mock_tempfile.NamedTemporaryFile.return_value = mock_tmp
        desktop.execute_command.return_value = ("Some text", 0)

        result = desktop.read_screen_text(region=[10, 20, 300, 200])
        assert "Error" not in result
        mock_grab.grab.assert_called_once_with(bbox=(10, 20, 310, 220))

    @patch("windows_mcp.desktop.service.ImageGrab")
    def test_capture_exception(self, mock_grab, desktop):
        mock_grab.grab.side_effect = OSError("No display")
        result = desktop.read_screen_text()
        assert "Error" in result
