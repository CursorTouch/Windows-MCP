import asyncio
import shutil
import os
from uuid import uuid4
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from windows_mcp.infrastructure import PostHogAnalytics, with_analytics


class TestWithAnalytics:
    async def test_success_path(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "test_tool")
        async def my_tool():
            return "result"

        result = await my_tool()
        assert result == "result"
        mock_analytics.track_tool.assert_called_once()
        call_args = mock_analytics.track_tool.call_args
        assert call_args[0][0] == "test_tool"
        assert call_args[0][1]["success"] is True
        assert "duration_ms" in call_args[0][1]

    async def test_error_path(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "test_tool")
        async def failing_tool():
            raise ValueError("something broke")

        with pytest.raises(ValueError, match="something broke"):
            await failing_tool()
        mock_analytics.track_error.assert_called_once()
        call_args = mock_analytics.track_error.call_args
        assert isinstance(call_args[0][0], ValueError)
        assert call_args[0][1]["tool_name"] == "test_tool"

    async def test_no_analytics_instance(self):
        @with_analytics(None, "test_tool")
        async def my_tool():
            return 42

        result = await my_tool()
        assert result == 42

    async def test_duration_measurement(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "test_tool")
        async def slow_tool():
            await asyncio.sleep(0.1)
            return "done"

        await slow_tool()
        call_args = mock_analytics.track_tool.call_args
        duration_ms = call_args[0][1]["duration_ms"]
        assert duration_ms >= 50  # Should be ~100ms but allow margin

    async def test_error_still_tracks_duration(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "test_tool")
        async def failing_tool():
            await asyncio.sleep(0.05)
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await failing_tool()
        call_args = mock_analytics.track_error.call_args
        assert "duration_ms" in call_args[0][1]

    async def test_return_value_preserved(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "test_tool")
        async def tool_with_complex_result():
            return {"key": "value", "count": 42}

        result = await tool_with_complex_result()
        assert result == {"key": "value", "count": 42}


class TestPostHogAnalytics:
    def test_user_id_falls_back_when_persisted_file_cannot_be_read(self, monkeypatch):
        user_data_dir = (
            Path(__file__).resolve().parent
            / f".tmp-analytics-user-data-{os.getpid()}-{uuid4().hex}"
        )
        user_data_dir.mkdir()
        try:
            user_id_file = user_data_dir / ".windows-mcp-user-id"
            user_id_file.write_text("persisted-user", encoding="utf-8")

            analytics = PostHogAnalytics.__new__(PostHogAnalytics)
            analytics._user_id = None
            analytics.USER_DATA_DIR = user_data_dir

            original_read_text = Path.read_text

            def read_text_with_permission_error(path, *args, **kwargs):
                if path == user_id_file:
                    raise PermissionError("permission denied")
                return original_read_text(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", read_text_with_permission_error)

            assert analytics.user_id
            assert analytics.user_id != "persisted-user"
            assert original_read_text(user_id_file, encoding="utf-8") == analytics.user_id
        finally:
            shutil.rmtree(user_data_dir, ignore_errors=True)
