"""Thread-dispatch behaviour of ``with_analytics``.

``with_analytics`` wraps every tool in an async function and, for a synchronous
body, executes it with ``asyncio.to_thread``. That is the right default for
non-blocking behaviour, but it is wrong for thread-affine work: the shared
``Desktop`` holds UIAutomation/comtypes state constructed on the event-loop
thread during the MCP lifespan, so a body that touches it from a worker thread
silently observes an empty desktop.

These tests pin the ``run_sync_in_thread`` opt-out that keeps such a body on the
event-loop thread. Windows UIAutomation is not required: affinity is simulated
with a plain object that refuses cross-thread access, so this runs anywhere.
"""

import asyncio
import inspect
import threading
from unittest.mock import AsyncMock

import pytest

from windows_mcp.infrastructure import with_analytics


class ApartmentBound:
    """Stand-in for a COM/UIAutomation object: usable only from its creating thread."""

    def __init__(self) -> None:
        self._owner_thread = threading.get_ident()

    def call(self) -> str:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("cross-apartment call from a non-owning thread")
        return "ok"


class TestThreadDispatch:
    async def test_default_sync_body_runs_off_event_loop_thread(self):
        @with_analytics(None, "sync_tool")
        def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() != caller

    async def test_opt_out_sync_body_runs_inline(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() == caller

    async def test_async_body_is_awaited_on_the_calling_thread(self):
        @with_analytics(None, "async_tool")
        async def my_tool():
            await asyncio.sleep(0)
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() == caller

    async def test_opt_out_does_not_affect_async_bodies(self):
        """The flag only governs synchronous bodies."""

        @with_analytics(None, "async_tool", run_sync_in_thread=False)
        async def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() == caller


class TestBehaviourPreserved:
    async def test_return_value_preserved_for_sync_body(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return {"key": "value", "count": 42}

        assert await my_tool() == {"key": "value", "count": 42}

    async def test_exception_propagates_for_sync_body(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            raise ValueError("something broke")

        with pytest.raises(ValueError, match="something broke"):
            await my_tool()

    async def test_analytics_tracks_sync_body_success(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return "result"

        assert await my_tool() == "result"
        mock_analytics.track_tool.assert_called_once()
        call_args = mock_analytics.track_tool.call_args
        assert call_args[0][0] == "sync_tool"
        assert call_args[0][1]["success"] is True
        assert "duration_ms" in call_args[0][1]

    async def test_analytics_tracks_sync_body_error(self):
        mock_analytics = AsyncMock()

        @with_analytics(mock_analytics, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await my_tool()
        mock_analytics.track_error.assert_called_once()
        call_args = mock_analytics.track_error.call_args
        assert isinstance(call_args[0][0], RuntimeError)
        assert call_args[0][1]["tool_name"] == "sync_tool"
        assert "duration_ms" in call_args[0][1]

    def test_default_keeps_worker_thread_dispatch(self):
        """The new parameter must stay optional so existing call sites are unaffected."""
        parameter = inspect.signature(with_analytics).parameters["run_sync_in_thread"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is True


class TestThreadAffinityRationale:
    async def test_thread_affine_object_needs_the_opt_out(self):
        """Why the opt-out exists: default dispatch breaks apartment affinity."""
        # Built on the event-loop thread, mirroring Desktop() in the MCP lifespan.
        bound = ApartmentBound()

        @with_analytics(None, "affine_tool")
        def default_tool():
            return bound.call()

        @with_analytics(None, "affine_tool", run_sync_in_thread=False)
        def opted_in_tool():
            return bound.call()

        with pytest.raises(RuntimeError, match="cross-apartment"):
            await default_tool()

        assert await opted_in_tool() == "ok"
