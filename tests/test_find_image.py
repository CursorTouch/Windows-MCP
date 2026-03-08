from unittest.mock import MagicMock, patch
import sys

import pytest

from windows_mcp.desktop.service import Desktop


@pytest.fixture
def desktop():
    with patch.object(Desktop, "__init__", lambda self: None):
        d = Desktop()
        return d


class TestFindImage:
    def test_missing_deps(self, desktop):
        """Should return install instructions when opencv is not installed."""
        with patch.dict(sys.modules, {"cv2": None, "numpy": None}):
            original_import = (
                __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
            )

            def mock_import(name, *args, **kwargs):
                if name in ("cv2", "numpy"):
                    raise ImportError(f"No module named '{name}'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = desktop.find_image("template.png")
                assert "opencv" in result.lower() or "Error" in result

    @patch("pathlib.Path.resolve")
    def test_file_not_found(self, mock_resolve, desktop):
        """Should error when template file doesn't exist."""
        mock_path = MagicMock()
        mock_path.is_file.return_value = False
        mock_resolve.return_value = mock_path

        mock_cv2 = MagicMock()
        mock_np = MagicMock()
        with patch.dict(sys.modules, {"cv2": mock_cv2, "numpy": mock_np}):
            result = desktop.find_image("/nonexistent/template.png")
            assert "Error" in result
            assert "not found" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    @patch("pathlib.Path.resolve")
    def test_match_found(self, mock_resolve, mock_grab, desktop):
        """Should return coordinates when match exceeds threshold."""
        mock_path = MagicMock()
        mock_path.is_file.return_value = True
        mock_path.suffix = ".png"
        mock_path.__str__ = lambda self: "/fake/template.png"
        mock_resolve.return_value = mock_path

        mock_cv2 = MagicMock()
        mock_np = MagicMock()

        # Template is 20x10
        mock_template = MagicMock()
        mock_template.shape = (10, 20, 3)
        mock_cv2.imread.return_value = mock_template

        # Screen is 1920x1080
        mock_screen_bgr = MagicMock()
        mock_screen_bgr.shape = (1080, 1920, 3)
        mock_cv2.cvtColor.return_value = mock_screen_bgr

        # Match at (100, 200) with confidence 0.95
        mock_cv2.matchTemplate.return_value = MagicMock()
        mock_cv2.minMaxLoc.return_value = (0, 0.95, (0, 0), (100, 200))
        mock_cv2.TM_CCOEFF_NORMED = 5

        mock_screen_img = MagicMock()
        mock_grab.grab.return_value = mock_screen_img
        mock_np.array.return_value = MagicMock()
        mock_cv2.COLOR_RGB2BGR = 4

        with patch.dict(sys.modules, {"cv2": mock_cv2, "numpy": mock_np}):
            result = desktop.find_image("template.png", threshold=0.8)
            assert "Match found" in result
            assert "0.95" in result
            # Center should be x=100+10, y=200+5
            assert "110" in result
            assert "205" in result

    @patch("windows_mcp.desktop.service.ImageGrab")
    @patch("pathlib.Path.resolve")
    def test_no_match(self, mock_resolve, mock_grab, desktop):
        """Should report no match when confidence is below threshold."""
        mock_path = MagicMock()
        mock_path.is_file.return_value = True
        mock_path.suffix = ".png"
        mock_path.__str__ = lambda self: "/fake/template.png"
        mock_resolve.return_value = mock_path

        mock_cv2 = MagicMock()
        mock_np = MagicMock()

        mock_template = MagicMock()
        mock_template.shape = (10, 20, 3)
        mock_cv2.imread.return_value = mock_template

        mock_screen_bgr = MagicMock()
        mock_screen_bgr.shape = (1080, 1920, 3)
        mock_cv2.cvtColor.return_value = mock_screen_bgr

        mock_cv2.matchTemplate.return_value = MagicMock()
        mock_cv2.minMaxLoc.return_value = (0, 0.3, (0, 0), (50, 50))
        mock_cv2.TM_CCOEFF_NORMED = 5

        mock_screen_img = MagicMock()
        mock_grab.grab.return_value = mock_screen_img
        mock_np.array.return_value = MagicMock()
        mock_cv2.COLOR_RGB2BGR = 4

        with patch.dict(sys.modules, {"cv2": mock_cv2, "numpy": mock_np}):
            result = desktop.find_image("template.png", threshold=0.8)
            assert "No match" in result
            assert "0.3" in result

    def test_invalid_region(self, desktop):
        """Should error when region has wrong number of elements."""
        mock_cv2 = MagicMock()
        mock_np = MagicMock()
        mock_cv2.imread.return_value = MagicMock()

        mock_path = MagicMock()
        mock_path.is_file.return_value = True
        mock_path.suffix = ".png"
        mock_path.__str__ = lambda self: "/fake/template.png"

        with patch.dict(sys.modules, {"cv2": mock_cv2, "numpy": mock_np}):
            with patch("pathlib.Path.resolve", return_value=mock_path):
                result = desktop.find_image("template.png", region=[10, 20])
                assert "Error" in result
                assert "region" in result

    def test_invalid_extension(self, desktop):
        """Should reject non-image file extensions."""
        mock_cv2 = MagicMock()
        mock_np = MagicMock()

        mock_path = MagicMock()
        mock_path.is_file.return_value = True
        mock_path.suffix = ".exe"
        mock_path.__str__ = lambda self: "/fake/malware.exe"

        with patch.dict(sys.modules, {"cv2": mock_cv2, "numpy": mock_np}):
            with patch("pathlib.Path.resolve", return_value=mock_path):
                result = desktop.find_image("malware.exe")
                assert "Error" in result
                assert "image file" in result
