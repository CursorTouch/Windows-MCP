"""Thread-dispatch behaviour of ``with_analytics``.

Synchronous tool bodies normally use ``asyncio.to_thread`` so they cannot block
the MCP event loop.  UIAutomation/comtypes work additionally needs stable COM
apartment affinity, so ``run_sync_in_thread=False`` uses one dedicated UIA
worker instead of running inline on the event loop.

These tests do not require a live desktop.  They pin the dispatch semantics,
including the regression that a long WaitFor-style synchronous body must not
stall unrelated async work.
"""

import asyncio
import inspect
import threading
import time
from unittest.mock import AsyncMock

import pytest

from windows_mcp.infrastructure import uia_thread_id, with_analytics


class TestThreadDispatch:
    async def test_default_sync_body_runs_off_event_loop_thread(self):
        @with_analytics(None, "sync_tool")
        def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() != caller

    async def test_affine_sync_body_runs_on_dedicated_worker(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        owner = await my_tool()
        assert owner == uia_thread_id()
        assert owner != caller

    async def test_affine_sync_body_reuses_same_worker(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return threading.get_ident()

        assert await my_tool() == await my_tool() == uia_thread_id()

    async def test_async_body_is_awaited_on_the_calling_thread(self):
        @with_analytics(None, "async_tool")
        async def my_tool():
            await asyncio.sleep(0)
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() == caller

    async def test_affinity_flag_does_not_move_async_body(self):
        """Async tools must explicitly hop only their UIA operation."""

        @with_analytics(None, "async_tool", run_sync_in_thread=False)
        async def my_tool():
            return threading.get_ident()

        caller = threading.get_ident()
        assert await my_tool() == caller


class TestBehaviourPreserved:
    async def test_return_value_preserved_for_affine_sync_body(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            return {"key": "value", "count": 42}

        assert await my_tool() == {"key": "value", "count": 42}

    async def test_exception_propagates_for_affine_sync_body(self):
        @with_analytics(None, "sync_tool", run_sync_in_thread=False)
        def my_tool():
            raise ValueError("something broke")

        with pytest.raises(ValueError, match="something broke"):
            await my_tool()

    async def test_analytics_tracks_affine_sync_body_success(self):
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

    async def test_analytics_tracks_affine_sync_body_error(self):
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

    def test_default_keeps_generic_worker_dispatch(self):
        parameter = inspect.signature(with_analytics).parameters["run_sync_in_thread"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is True


class TestEventLoopResponsiveness:
    async def test_long_affine_sync_body_does_not_stall_event_loop(self):
        """A WaitFor-style blocking body must not freeze unrelated async work."""

        @with_analytics(None, "wait_for", run_sync_in_thread=False)
        def blocking_tool():
            time.sleep(0.15)
            return "done"

        tool_task = asyncio.create_task(blocking_tool())

        async def ticker():
            await asyncio.sleep(0.02)
            return "tick"

        assert await ticker() == "tick"
        assert not tool_task.done()
        assert await tool_task == "done"


class TestRequestContext:
    async def test_request_token_and_steps_cross_worker_without_leaking(self):
        from windows_mcp.desktop.control_context import current_token, current_steps, StepCounter

        @with_analytics(None, "context", run_sync_in_thread=False)
        def operation():
            token = current_token.get()
            counter = current_steps.get()
            assert counter is not None
            counter.value += 1
            # Changes to bindings in the worker must not leak back or to its next job.
            current_token.set(-1)
            return token

        async def request(value):
            counter = StepCounter()
            token_marker = current_token.set(value)
            steps_marker = current_steps.set(counter)
            try:
                observed = await operation()
                assert current_token.get() == value
                assert counter.value == 1
                return observed
            finally:
                current_steps.reset(steps_marker)
                current_token.reset(token_marker)

        assert await asyncio.gather(request(11), request(22)) == [11, 22]
        from windows_mcp.infrastructure.thread_affinity import run_uia_affine

        assert await run_uia_affine(lambda: (current_token.get(), current_steps.get())) == (
            None,
            None,
        )

    async def test_exception_does_not_leave_worker_context(self):
        from windows_mcp.desktop.control_context import current_token
        from windows_mcp.infrastructure.thread_affinity import run_uia_affine

        def failing():
            assert current_token.get() == 33
            current_token.set(44)
            raise ValueError("expected failure")

        marker = current_token.set(33)
        try:
            with pytest.raises(ValueError, match="expected failure"):
                await run_uia_affine(failing)
            assert current_token.get() == 33
        finally:
            current_token.reset(marker)
        assert await run_uia_affine(current_token.get) is None

    async def test_unguarded_call_remains_usable(self):
        from windows_mcp.desktop.control import ControlCoordinator
        from windows_mcp.desktop.control_context import current_token
        from windows_mcp.infrastructure.thread_affinity import run_uia_affine

        owner = ControlCoordinator()

        def operation():
            assert current_token.get() is None
            owner.checkpoint_current()
            return "unguarded"

        assert await run_uia_affine(operation) == "unguarded"

    async def test_mcp_takeover_stops_next_affine_step_and_preserves_count(self):
        """Real MCP gate and UIA worker; only the input effects are simulated."""
        from fastmcp import Client, FastMCP
        from windows_mcp.desktop.control import ControlBlocked, ControlCoordinator
        from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate

        owner = ControlCoordinator()
        owner._state = "ready"
        owner._suppress = True  # Simulate an acknowledged indicator, without native hooks.
        mcp = FastMCP("affine-takeover")
        mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
        effects = []
        blocked_status = []

        @mcp.tool(name="Click")
        @with_analytics(None, "simulated-input", run_sync_in_thread=False)
        def operation():
            owner.checkpoint_current()
            effects.append("first")
            owner.record_step_current()
            with owner._lock:
                owner._fast_takeover = True
            try:
                owner.checkpoint_current()
            except ControlBlocked as exc:
                blocked_status.append(exc.status)
                raise
            effects.append("second")
            return "incorrectly completed"

        try:
            async with Client(mcp) as client:
                result = await client.call_tool("Click", raise_on_error=False)
            assert result.is_error
            assert "CONTROL_PREEMPTED" in result.content[0].text
            assert blocked_status[0]["executed_steps"] == 1
            assert effects == ["first"]
            assert owner._active_calls == 0
        finally:
            owner.stop()
