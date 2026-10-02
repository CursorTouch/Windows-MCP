"""Ownership gate and notification regressions at the MCP boundary."""

from __future__ import annotations

import asyncio
import inspect
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop.control import ControlBlocked, ControlCoordinator, current_token
from windows_mcp.desktop.control_ledger import InputLedger
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


def _legacy_notification_client(transport, **kwargs):
    """Use the sessionful era required for unsolicited logs on FastMCP 4."""
    if "mode" in inspect.signature(Client).parameters:
        kwargs["mode"] = "legacy"
    return Client(transport, **kwargs)


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
async def test_gate_blocks_dynamic_tools_but_status_remains_available():
    controller = FakeController()
    mcp = FastMCP("test")
    notifier = ControlNotifier(controller)
    mcp.add_middleware(ControlToolGate(controller, notifier))
    effects = []

    @mcp.tool(name="DynamicTool")
    def dynamic_tool():
        effects.append(current_token.get())
        return "executed"

    @mcp.tool(name="ControlStatus")
    def control_status():
        return controller.status()

    async with Client(mcp) as client:
        controller.state = "user"
        blocked = await client.call_tool("DynamicTool", raise_on_error=False)
        assert blocked.is_error
        assert "USER_CONTROL" in str(blocked.content)
        assert effects == []
        status = await client.call_tool("ControlStatus")
        assert "user" in str(status.content)
        controller.state = "ready"
        result = await client.call_tool("DynamicTool")
        assert "executed" in str(result.content)
        assert effects == [7]
        assert current_token.get() is None
        assert controller.calls == [("begin", "DynamicTool"), ("end", 7)]


@pytest.mark.asyncio
async def test_ledger_preemption_is_structured_and_releases_tool_lock():
    controller = FakeController()
    step_owner = ControlCoordinator()
    ledger = InputLedger()
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, ControlNotifier(controller)))
    effects = []

    @mcp.tool(name="Interrupted")
    def interrupted():
        for _ in range(3):
            step_owner.record_step_current()
        ledger.block_new()
        controller.state = "user"
        ledger.press("key:ctrl", lambda: effects.append("down"), lambda: None, lambda: False)

    @mcp.tool(name="After")
    def after():
        effects.append("after")

    async with Client(mcp) as client:
        result = await client.call_tool("Interrupted", raise_on_error=False)
        assert result.is_error
        assert '"code": "CONTROL_PREEMPTED"' in str(result.content)
        assert '"executed_steps": 3' in str(result.content)
        assert ledger.pending() == ()
        controller.state = "ready"
        ledger.enable()
        await asyncio.wait_for(client.call_tool("After"), 1)
    assert effects == ["after"]


@pytest.mark.asyncio
async def test_queued_call_rechecks_after_takeover():
    controller = FakeController()
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, ControlNotifier(controller)))
    started = asyncio.Event()
    release = asyncio.Event()
    effects = []

    @mcp.tool(name="Hold")
    async def hold():
        started.set()
        await release.wait()
        effects.append("hold")

    @mcp.tool(name="Next")
    def next_tool():
        effects.append("next")

    async with Client(mcp) as client:
        first = asyncio.create_task(client.call_tool("Hold"))
        await started.wait()
        second = asyncio.create_task(client.call_tool("Next", raise_on_error=False))
        await asyncio.sleep(0)
        controller.state = "user"
        release.set()
        await first
        result = await second
        assert result.is_error
        assert effects == ["hold"]


@pytest.mark.asyncio
async def test_cancelled_active_and_queued_calls_release_gate_and_lease():
    controller = FakeController()
    gate = ControlToolGate(controller, ControlNotifier(controller))
    context = SimpleNamespace(message=SimpleNamespace(name="Action"), fastmcp_context=None)
    started = asyncio.Event()
    never = asyncio.Event()

    async def hold(_context):
        started.set()
        await never.wait()

    active = asyncio.create_task(gate.on_call_tool(context, hold))
    await asyncio.wait_for(started.wait(), 1)
    queued = asyncio.create_task(gate.on_call_tool(context, hold))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert not gate._call_lock.locked()
    assert controller.calls == [("begin", "Action"), ("end", 7)]

    controller.state = "ready"
    assert await gate.on_call_tool(context, lambda _: asyncio.sleep(0, result="ok")) == "ok"
    assert controller.calls[-2:] == [("begin", "Action"), ("end", 7)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["App", "PowerShell"])
async def test_already_running_external_command_is_not_claimed_cancelled(tool_name):
    controller = FakeController()
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, ControlNotifier(controller)))
    started, release = asyncio.Event(), asyncio.Event()
    effects = []

    @mcp.tool(name=tool_name)
    async def command():
        started.set()
        await release.wait()
        effects.append("external effect")

    async with Client(mcp) as client:
        running = asyncio.create_task(client.call_tool(tool_name))
        await asyncio.wait_for(started.wait(), 1)
        controller.state = "user"
        blocked = await client.call_tool(tool_name, raise_on_error=False)
        assert blocked.is_error
        release.set()
        await asyncio.wait_for(running, 1)
    assert effects == ["external effect"]


@pytest.mark.asyncio
async def test_lease_invalidated_during_begin_does_not_run_tool():
    class Invalidated(FakeController):
        def begin_call(self, name):
            token = super().begin_call(name)
            self.state = "user"  # Equivalent to indicator failure/takeover in callback.
            return token

    controller = Invalidated()
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, ControlNotifier(controller)))
    effects = []

    @mcp.tool(name="Action")
    def action():
        effects.append("ran")

    async with Client(mcp) as client:
        result = await client.call_tool("Action", raise_on_error=False)
    assert result.is_error
    assert "CONTROL_PREEMPTED" in str(result.content)
    assert effects == []
    assert controller.calls == [("begin", "Action"), ("end", 7)]


class FakeSession:
    def __init__(self, *, fail=False):
        self.messages = []
        self.fail = fail

    async def send_log_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("closed")
        self.messages.append(kwargs)


@pytest.mark.asyncio
async def test_notifier_sends_takeover_and_recovery_and_prunes_failed_session():
    controller = FakeController()
    notifier = ControlNotifier(controller)
    notifier.start()
    good, bad = FakeSession(), FakeSession(fail=True)
    loop = asyncio.get_running_loop()
    now = time.monotonic()
    from windows_mcp.tools.control_notifications import _Session

    notifier._sessions[id(good)] = _Session(good, loop, now)
    notifier._sessions[id(bad)] = _Session(bad, loop, now)
    controller.emit("ai")
    controller.emit("user")
    await asyncio.sleep(0.05)
    assert [m["data"]["event"] for m in good.messages] == ["USER_CONTROL"]
    assert id(bad) not in notifier._sessions
    controller.emit("ready")
    await asyncio.sleep(0.05)
    assert [m["data"]["event"] for m in good.messages] == [
        "USER_CONTROL",
        "AI_CONTROL_AVAILABLE",
    ]
    await notifier.close()
    controller.emit("user")
    await asyncio.sleep(0)
    assert len(good.messages) == 2


@pytest.mark.asyncio
async def test_notifier_orders_cross_thread_events_by_generation():
    controller = FakeController()
    notifier = ControlNotifier(controller)
    notifier.start()
    session = FakeSession()
    from windows_mcp.tools.control_notifications import _Session

    notifier._sessions[id(session)] = _Session(
        session, asyncio.get_running_loop(), time.monotonic()
    )
    notifier._on_state({"state": "user", "generation": 3, "wait_seconds": 10.0})
    notifier._on_state({"state": "ai", "generation": 2, "wait_seconds": 0.0})
    await asyncio.sleep(0.06)
    assert [m["data"]["event"] for m in session.messages] == ["USER_CONTROL"]
    await notifier.close()


@pytest.mark.asyncio
async def test_notifier_close_cancels_and_awaits_blocked_send():
    controller = FakeController()
    notifier = ControlNotifier(controller)
    notifier.start()
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    delivered = []

    class BlockingSession:
        async def send_log_message(self, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            delivered.append(kwargs)

    session = BlockingSession()
    from windows_mcp.tools.control_notifications import _Session

    notifier._sessions[id(session)] = _Session(
        session, asyncio.get_running_loop(), time.monotonic()
    )
    controller.emit("ai")
    controller.emit("user")
    await asyncio.wait_for(started.wait(), 1)
    assert len(notifier._send_tasks) == 1

    await asyncio.wait_for(notifier.close(), 1)
    assert cancelled.is_set()
    assert notifier._send_tasks == set()
    assert delivered == []
    release.set()
    await asyncio.sleep(0)
    assert delivered == []


@pytest.mark.asyncio
async def test_stateless_mode_registers_no_push_session():
    controller = FakeController()
    notifier = ControlNotifier(controller)
    notifier.push_enabled = False
    notifier.start()
    try:
        context = SimpleNamespace(fastmcp_context=SimpleNamespace(session=FakeSession()))
        notifier.remember(context)
        assert notifier._sessions == {}
    finally:
        await notifier.close()


@pytest.mark.asyncio
async def test_expired_sessions_receive_no_push_and_status_filter_is_reserved():
    from windows_mcp.__main__ import _apply_tool_filter

    controller = FakeController()
    notifier = ControlNotifier(controller, ttl=60)
    notifier.start()
    stale = FakeSession()
    from windows_mcp.tools.control_notifications import _Session

    notifier._sessions[id(stale)] = _Session(
        stale, asyncio.get_running_loop(), time.monotonic() - 61
    )
    controller.emit("ai")
    controller.emit("user")
    await asyncio.sleep(0.05)
    assert stale.messages == []
    assert id(stale) not in notifier._sessions
    await notifier.close()

    mcp = FastMCP("test")

    @mcp.tool(name="ControlStatus")
    def status():
        return "status"

    @mcp.tool(name="Other")
    def other():
        return "other"

    _apply_tool_filter(mcp, explicit_tools=["Other"], exclude_tools=None)
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {"ControlStatus", "Other"}
    _apply_tool_filter(mcp, explicit_tools=None, exclude_tools=["ControlStatus", "Other"])
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {"ControlStatus"}


@pytest.mark.asyncio
async def test_overlay_callback_never_waits_on_hook_thread(monkeypatch):
    from windows_mcp import __main__ as wm
    from windows_mcp.desktop import control as control_module, control_overlay
    from windows_mcp.desktop import service as desktop_service
    from windows_mcp.watchdog import service as watchdog_service
    from windows_mcp import tools as tool_registry

    class Controller(FakeController):
        _thread = None
        fail_count = 0
        sync_visible = True

        def start(self):
            self.state = "user"

        def stop(self):
            self.state = "unavailable"

        def _fail_open(self):
            self.fail_count += 1

        def set_health_probe(self, probe):
            self.health_probe = probe

        def visible_ack_required(self):
            return self.sync_visible

    class Desktop:
        tree = type("Tree", (), {"on_focus_change": lambda *args: None})()

        def get_screen_size(self):
            return (100, 100)

    class WatchDog:
        def set_focus_callback(self, callback):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    controller = Controller()
    calls = []

    def slow_show(active, *, generation=None):
        time.sleep(0.15)
        calls.append(active)
        return True

    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.setattr(wm, "_mcp", None)
    monkeypatch.setattr(control_module, "get_controller", lambda: controller)
    monkeypatch.setattr(tool_registry, "register_all", lambda *a, **k: None)
    monkeypatch.setattr(desktop_service, "Desktop", Desktop)
    monkeypatch.setattr(watchdog_service, "WatchDog", WatchDog)
    monkeypatch.setattr(control_overlay, "start", lambda: None)
    monkeypatch.setattr(control_overlay, "stop", lambda: None)
    monkeypatch.setattr(control_overlay, "set_active", slow_show)
    monkeypatch.setattr(
        control_overlay,
        "set_pending",
        lambda active, *, generation=None: calls.append(active),
    )

    mcp = wm._build_mcp()
    callback = controller.listeners[0]
    async with mcp._lifespan(mcp):
        assert controller.health_probe is control_overlay.is_healthy
        # begin_call runs on the server loop and must wait for the indicator.
        start = time.monotonic()
        callback({"state": "ai"})
        assert time.monotonic() - start >= 0.14
        assert calls == [True]

        # A ControlStatus-triggered visual update must not block the server loop.
        controller.sync_visible = False
        start = time.monotonic()
        callback({"state": "ai"})
        assert time.monotonic() - start < 0.1
        await asyncio.sleep(0.2)
        assert calls == [True, True]

        # A normal tool can advance user -> ready on the event loop. Its
        # visual acknowledgement must not delay other MCP calls.
        controller.sync_visible = True
        for state in ("user", "ready"):
            start = time.monotonic()
            callback({"state": state})
            assert time.monotonic() - start < 0.1
        await asyncio.sleep(0.2)
        assert calls == [True, True, False, False]

        elapsed = []

        def from_hook_thread():
            start = time.monotonic()
            callback({"state": "user"})
            elapsed.append(time.monotonic() - start)

        thread = threading.Thread(target=from_hook_thread)
        controller._thread = thread
        thread.start()
        thread.join(timeout=1)
        assert elapsed and elapsed[0] < 0.1
        await asyncio.sleep(0.2)
        assert calls == [True, True, False, False, False]

        monkeypatch.setattr(
            control_overlay,
            "set_active",
            lambda active, *, generation=None: (_ for _ in ()).throw(RuntimeError("lost")),
        )
        controller.sync_visible = True
        callback({"state": "ai"})
        assert controller.fail_count == 1


@pytest.mark.asyncio
async def test_live_mcp_client_receives_unsolicited_state_log():
    controller = FakeController()
    notifier = ControlNotifier(controller)
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, notifier))
    received = []
    delivered = asyncio.Event()

    @mcp.tool(name="ControlStatus")
    def status():
        return controller.status()

    async def on_log(message):
        received.append(message)
        delivered.set()

    notifier.start()
    try:
        async with _legacy_notification_client(mcp, log_handler=on_log) as client:
            await client.call_tool("ControlStatus")  # Registers the live session.
            controller.emit("ai")
            controller.emit("user")
            await asyncio.wait_for(delivered.wait(), timeout=2)
            assert any("USER_CONTROL" in str(m.data) for m in received)
    finally:
        await notifier.close()


@pytest.mark.asyncio
async def test_stateful_http_client_receives_unsolicited_state_log():
    """Exercise the actual HTTP session transport, not only in-memory Client."""
    import uvicorn

    controller = FakeController()
    notifier = ControlNotifier(controller)
    mcp = FastMCP("test")
    mcp.add_middleware(ControlToolGate(controller, notifier))
    received = []
    delivered = asyncio.Event()

    @mcp.tool(name="ControlStatus")
    def status():
        return controller.status()

    async def on_log(message):
        received.append(message)
        delivered.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    app = mcp.http_app(stateless_http=False)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        notifier.start()
        async with _legacy_notification_client(
            f"http://127.0.0.1:{port}/mcp", log_handler=on_log
        ) as client:
            await client.call_tool("ControlStatus")
            controller.emit("ai")
            controller.emit("user")
            await asyncio.wait_for(delivered.wait(), 2)
            assert any("USER_CONTROL" in str(message.data) for message in received)
    finally:
        await notifier.close()
        server.should_exit = True
        await asyncio.wait_for(server_task, 3)
