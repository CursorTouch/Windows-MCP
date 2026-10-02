"""A slow indicator acknowledgement must not stall status or leak a lease."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from windows_mcp.desktop.control import ControlBlocked
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


class FakeController:
    def __init__(self):
        self.state = "ready"
        self.generation = 1
        self.calls = []
        self.listeners = []

    def status(self):
        return {"state": self.state, "generation": self.generation, "wait_seconds": 0.0}

    def subscribe(self, callback):
        self.listeners.append(callback)

    def emit(self, state):
        self.state = state
        self.generation += 1
        for callback in self.listeners:
            callback(self.status())

    def begin_call(self, name):
        if self.state not in ("ready", "ai"):
            raise ControlBlocked("USER_CONTROL", self.status())
        self.calls.append(("begin", name))
        self.state = "ai"
        return 7

    def end_call(self, token):
        self.calls.append(("end", token))

    def checkpoint(self, token):
        if self.state != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", self.status())


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_during_ack", [False, True])
async def test_slow_visible_ack_keeps_status_responsive_and_balances_cancel(
    cancel_during_ack,
):
    started = threading.Event()
    release = threading.Event()

    class SlowController(FakeController):
        def begin_call(self, name):
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test indicator acknowledgement timed out")
            return super().begin_call(name)

    controller = SlowController()
    gate = ControlToolGate(controller, ControlNotifier(controller))
    action = SimpleNamespace(message=SimpleNamespace(name="Action"), fastmcp_context=None)
    status = SimpleNamespace(message=SimpleNamespace(name="ControlStatus"), fastmcp_context=None)
    effects = []

    async def run_action(_context):
        effects.append("ran")
        return "ran"

    active = asyncio.create_task(gate.on_call_tool(action, run_action))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.5)
        observed = await asyncio.wait_for(
            gate.on_call_tool(status, lambda _: asyncio.sleep(0, result=controller.status())),
            0.5,
        )
        assert observed["state"] == "ready"
        if cancel_during_ack:
            active.cancel()
            await asyncio.sleep(0)
            assert not active.done()
        release.set()
        if cancel_during_ack:
            with pytest.raises(asyncio.CancelledError):
                await active
            assert effects == []
        else:
            assert await asyncio.wait_for(active, 1) == "ran"
            assert effects == ["ran"]
        assert controller.calls == [("begin", "Action"), ("end", 7)]
        assert not gate._call_lock.locked()
    finally:
        release.set()
        if not active.done():
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
