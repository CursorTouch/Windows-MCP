"""Tests for DPI coordinate_system conversion helpers in __main__.py."""

from unittest.mock import patch


class TestToPhysical:
    """Test _to_physical helper for [x, y] coordinate conversion."""

    def test_physical_passthrough(self):
        """Physical coordinates should not be modified."""
        from windows_mcp.__main__ import _to_physical

        loc = [100, 200]
        result = _to_physical(loc, "physical")
        assert result == [100, 200]

    def test_physical_returns_same_list(self):
        """Physical mode should return the same list object."""
        from windows_mcp.__main__ import _to_physical

        loc = [50, 75]
        result = _to_physical(loc, "physical")
        assert result is loc

    @patch("windows_mcp.__main__.desktop")
    def test_logical_scales_by_dpi(self, mock_desktop):
        """Logical coordinates should be multiplied by DPI scale factor."""
        from windows_mcp.__main__ import _to_physical

        mock_desktop.get_dpi_scaling.return_value = 1.5
        result = _to_physical([100, 200], "logical")
        assert result == [150, 300]

    @patch("windows_mcp.__main__.desktop")
    def test_logical_150_percent(self, mock_desktop):
        """Test 150% DPI scaling (common on laptops)."""
        from windows_mcp.__main__ import _to_physical

        mock_desktop.get_dpi_scaling.return_value = 1.5
        result = _to_physical([960, 540], "logical")
        assert result == [1440, 810]

    @patch("windows_mcp.__main__.desktop")
    def test_logical_200_percent(self, mock_desktop):
        """Test 200% DPI scaling (4K displays)."""
        from windows_mcp.__main__ import _to_physical

        mock_desktop.get_dpi_scaling.return_value = 2.0
        result = _to_physical([500, 300], "logical")
        assert result == [1000, 600]

    @patch("windows_mcp.__main__.desktop")
    def test_logical_100_percent_no_change(self, mock_desktop):
        """100% DPI (scale=1.0) should not change values."""
        from windows_mcp.__main__ import _to_physical

        mock_desktop.get_dpi_scaling.return_value = 1.0
        result = _to_physical([100, 200], "logical")
        assert result == [100, 200]

    @patch("windows_mcp.__main__.desktop")
    def test_logical_rounds_to_int(self, mock_desktop):
        """Scaled values should be rounded to nearest int."""
        from windows_mcp.__main__ import _to_physical

        mock_desktop.get_dpi_scaling.return_value = 1.25
        result = _to_physical([100, 100], "logical")
        assert result == [125, 125]
        assert all(isinstance(v, int) for v in result)

    @patch("windows_mcp.__main__.desktop")
    def test_logical_rounds_up_at_midpoint(self, mock_desktop):
        """round() should round 0.5 up for correct pixel targeting."""
        from windows_mcp.__main__ import _to_physical

        # 99 * 1.25 = 123.75 -> should round to 124, not truncate to 123
        mock_desktop.get_dpi_scaling.return_value = 1.25
        result = _to_physical([99, 99], "logical")
        assert result == [124, 124]

    def test_logical_raises_when_desktop_none(self):
        """Should raise RuntimeError when desktop is not initialized."""
        from windows_mcp.__main__ import _to_physical

        with patch("windows_mcp.__main__.desktop", None):
            try:
                _to_physical([100, 200], "logical")
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "not initialized" in str(e)


class TestRegionToPhysical:
    """Test _region_to_physical helper for [x, y, w, h] conversion."""

    def test_physical_passthrough(self):
        from windows_mcp.__main__ import _region_to_physical

        region = [100, 200, 300, 400]
        result = _region_to_physical(region, "physical")
        assert result == [100, 200, 300, 400]

    def test_physical_returns_same_list(self):
        from windows_mcp.__main__ import _region_to_physical

        region = [10, 20, 30, 40]
        result = _region_to_physical(region, "physical")
        assert result is region

    @patch("windows_mcp.__main__.desktop")
    def test_logical_scales_all_values(self, mock_desktop):
        """All 4 values (x, y, w, h) should be scaled."""
        from windows_mcp.__main__ import _region_to_physical

        mock_desktop.get_dpi_scaling.return_value = 2.0
        result = _region_to_physical([100, 200, 300, 400], "logical")
        assert result == [200, 400, 600, 800]


class TestPathToPhysical:
    """Test _path_to_physical helper for [[x,y], ...] conversion."""

    def test_physical_passthrough(self):
        from windows_mcp.__main__ import _path_to_physical

        path = [[0, 0], [100, 100], [200, 200]]
        result = _path_to_physical(path, "physical")
        assert result == [[0, 0], [100, 100], [200, 200]]

    def test_physical_returns_same_list(self):
        from windows_mcp.__main__ import _path_to_physical

        path = [[10, 20], [30, 40]]
        result = _path_to_physical(path, "physical")
        assert result is path

    @patch("windows_mcp.__main__.desktop")
    def test_logical_scales_all_waypoints(self, mock_desktop):
        from windows_mcp.__main__ import _path_to_physical

        mock_desktop.get_dpi_scaling.return_value = 1.5
        result = _path_to_physical([[100, 200], [300, 400]], "logical")
        assert result == [[150, 300], [450, 600]]

    @patch("windows_mcp.__main__.desktop")
    def test_logical_empty_path(self, mock_desktop):
        from windows_mcp.__main__ import _path_to_physical

        mock_desktop.get_dpi_scaling.return_value = 2.0
        result = _path_to_physical([], "logical")
        assert result == []
