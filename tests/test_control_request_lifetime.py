"""Request completion revokes workers without sending physical input."""

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from windows_mcp.desktop.control import ControlBlocked, ControlCoordinator
from windows_mcp.desktop.control_context import current_request_closed, current_token
from windows_mcp.infrastructure.thread_affinity import run_uia_affine
from windows_mcp.tools.control_notifications import ControlToolGate


def make_gate():
    owner = ControlCoordinator()
    owner._state = "ready"
    owner._suppress = True  # Acknowledged indicator; no hooks or real input.
    gate = ControlToolGate(owner, SimpleNamespace(remember=lambda _: None))
    context = SimpleNamespace(message=SimpleNamespace(name="Click", arguments={}))
    return owner, gate, context


@pytest.mark.parametrize("dispatch", [run_uia_affine, asyncio.to_thread])
@pytest.mark.parametrize("ending", ["cancel", "timeout"])
async def test_running_worker_stops_at_next_checkpoint(dispatch, ending):
    owner, gate, context = make_gate()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    effects, failures = [], []

    def operation():
        try:
            owner.checkpoint_current()
            effects.append("first")
            owner.record_step_current()
            entered.set()
            assert release.wait(5), "test worker was not released"
            owner.checkpoint_current()
            effects.append("second")
        except ControlBlocked as exc:
            failures.append(exc)
        finally:
            finished.set()

    async def call_next(_):
        await dispatch(operation)

    task = asyncio.create_task(gate.on_call_tool(context, call_next))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 0.01)
        assert owner._active_calls == 0
        assert not gate._call_lock.locked()

        # New calls share the AI generation but must not revive the old worker.
        generation = owner._generation

        async def next_call(_):
            owner.checkpoint_current()
            assert not current_request_closed.get().is_set()
            return "new request works"

        assert await gate.on_call_tool(context, next_call) == "new request works"
        assert owner._generation == generation
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        assert effects == ["first"]
        assert len(failures) == 1
        assert failures[0].code == "CONTROL_PREEMPTED"
        assert failures[0].status["executed_steps"] == 1
        assert current_token.get() is None
        assert current_request_closed.get() is None
        assert await dispatch(current_request_closed.get) is None
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 3)
        owner.stop()


@pytest.mark.parametrize("ending", ["success", "error"])
async def test_all_exits_revoke_before_releasing_lease(ending):
    owner, gate, context = make_gate()
    saved = []
    original_end = owner.end_call

    def end_call(token):
        assert saved[0].get(current_request_closed).is_set()
        assert gate._call_lock.locked()
        original_end(token)

    owner.end_call = end_call

    async def call_next(_):
        owner.checkpoint_current()
        saved.append(contextvars.copy_context())
        if ending == "error":
            raise ValueError("expected failure")
        return "done"

    try:
        if ending == "error":
            with pytest.raises(ValueError, match="expected failure"):
                await gate.on_call_tool(context, call_next)
        else:
            assert await gate.on_call_tool(context, call_next) == "done"
        with pytest.raises(ControlBlocked):
            saved[0].run(owner.checkpoint_current)
        assert not gate._call_lock.locked()
        assert owner._active_calls == 0
    finally:
        owner.stop()


@pytest.mark.parametrize("ending", ["cancel", "timeout", "disconnect"])
async def test_mcp_client_termination_revokes_running_worker(ending):
    """Use the real MCP in-memory transport; only desktop effects are simulated."""
    from fastmcp import Client, FastMCP
    from windows_mcp.infrastructure.analytics import with_analytics

    owner, gate, _ = make_gate()
    mcp = FastMCP("request-lifetime")
    mcp.add_middleware(gate)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    closed = []
    effects = []

    @mcp.tool(name="Click")
    @with_analytics(None, "simulated-input", run_sync_in_thread=False)
    def operation():
        try:
            owner.checkpoint_current()
            closed.append(current_request_closed.get())
            entered.set()
            assert release.wait(5), "test worker was not released"
            owner.checkpoint_current()
            effects.append("unexpected input")
        except ControlBlocked:
            return "preempted"
        finally:
            finished.set()

    task = None
    try:
        async with Client(mcp) as client:
            task = asyncio.create_task(
                client.call_tool("Click", timeout=0.3 if ending == "timeout" else 10)
            )
            assert await asyncio.to_thread(entered.wait, 3)
            if ending == "cancel":
                task.cancel()
            elif ending == "disconnect":
                await asyncio.wait_for(client.close(), 3)
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, BaseException)
            assert await asyncio.to_thread(closed[0].wait, 3)
            assert owner._active_calls == 0
            assert not gate._call_lock.locked()
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            assert effects == []
    finally:
        release.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 3)
        owner.stop()
