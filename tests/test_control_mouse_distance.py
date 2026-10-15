"""Deliberate physical motion takes control; small movements do not pause AI."""

import ctypes

import pytest

from windows_mcp.desktop import control, control_hooks


def owner_ready():
    owner = control.ControlCoordinator()
    owner._state = "ready"
    token = owner.begin_call("Type")
    owner._motion_since = 0.0  # Synthetic arrivals use a small, deterministic epoch.
    return owner, token


def test_round_trip_counts_path_length_and_only_threshold_invalidates_token():
    owner, token = owner_ready()
    generation = owner._generation
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, -199, 0, 10.1))
    owner.checkpoint(token)
    assert owner._generation == generation and owner._suppress
    owner._handle(("raw", 1, -1, 0, 10.2))
    assert owner.status()["state"] == "user"
    assert not owner._suppress
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)


@pytest.mark.parametrize("gap,takes_over", [(0.499, True), (0.5, False), (0.501, False)])
def test_arrival_gap_boundary_even_when_queue_processing_is_delayed(gap, takes_over):
    owner, token = owner_ready()
    # These arrivals are deliberately much older than the processing clock.
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, 200, 0, 10.0 + gap))
    assert (owner.status()["state"] == "user") == takes_over
    if not takes_over:
        owner.checkpoint(token)


def test_zero_motion_does_not_extend_segment():
    owner, token = owner_ready()
    owner._handle(("raw", 1, 300, 0, 10.0))
    owner._handle(("raw", 1, 0, 0, 10.4))
    owner._handle(("raw", 1, 200, 0, 10.5))
    owner.checkpoint(token)
    assert owner._raw_distance == 200


def test_diagonal_distance_and_repeated_small_steps():
    owner, token = owner_ready()
    for i in range(99):
        owner._handle(("raw", 1, 3, 4, 10.0 + i * 0.001))
    owner.checkpoint(token)
    assert owner._raw_distance == 495
    owner._handle(("raw", 1, 3, 4, 10.1))
    assert owner.status()["state"] == "user"


def test_point_and_raw_are_not_added_together():
    owner, token = owner_ready()
    owner._handle(("point", 0, 0, 0x200))
    owner._handle(("point", 10000, 10000, 0x200))
    owner._handle(("raw", 1, 499, 0))
    owner.checkpoint(token)
    assert owner._raw_distance == 499


def test_new_control_generation_clears_distance():
    owner, _ = owner_ready()
    owner._handle(("raw", 1, 400, 0))
    owner._set_locked("ready")
    token = owner.begin_call("Move")
    owner._handle(("raw", 1, 100, 0))
    owner.checkpoint(token)
    assert owner._raw_distance == 100


def test_event_queued_before_new_control_period_does_not_count():
    owner, token = owner_ready()
    owner._motion_since = 11.0
    owner._handle(("raw", 1, 500, 0, 10.9))
    owner.checkpoint(token)
    assert owner._raw_distance == 0


def test_raw_callback_does_not_pause_and_preserves_arrival_timestamp(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: (1, 2, -3))
    monkeypatch.setattr(control_hooks.time, "monotonic", lambda: 42.0)
    owner._raw_input(0)
    assert owner._events.get_nowait() == ("raw", 1, 2, -3, 42.0)
    owner.checkpoint(token)
    assert owner._suppress and not owner._fast_pending


def test_injected_motion_is_not_queued(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    data = control._MouseHookData()
    data.flags = 1
    data.pt.x = 50000
    assert owner._physical_mouse(0, 0x200, ctypes.addressof(data)) == 7
    assert owner._events.empty()
    owner.checkpoint(token)


def test_unusable_raw_event_does_not_count(monkeypatch):
    owner, token = owner_ready()
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda _: None)
    owner._raw_input(0)
    assert owner._events.empty()
    owner.checkpoint(token)


@pytest.mark.parametrize("last_step,blocked", [(199, False), (200, True)])
async def test_mcp_affine_tool_continues_below_threshold_and_stops_at_threshold(last_step, blocked):
    """Real MCP gate and UIA dispatch; physical input effects are simulated."""
    from fastmcp import Client, FastMCP
    from windows_mcp.infrastructure.analytics import with_analytics
    from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate

    owner = control.ControlCoordinator()
    owner._state = "ready"
    owner._suppress = True
    mcp = FastMCP("cumulative-mouse-takeover")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
    effects = []

    @mcp.tool(name="Click")
    @with_analytics(None, "simulated-input", run_sync_in_thread=False)
    def operation():
        stamp = control.time.monotonic()
        owner._handle(("raw", 1, 300, 0, stamp))
        owner.checkpoint_current()
        effects.append("first")
        owner.record_step_current()
        owner._handle(("raw", 1, -last_step, 0, stamp + 0.01))
        owner.checkpoint_current()
        effects.append("second")
        return "completed"

    try:
        async with Client(mcp) as client:
            result = await client.call_tool("Click", raise_on_error=False)
        assert result.is_error == blocked
        assert effects == (["first"] if blocked else ["first", "second"])
        if blocked:
            assert "CONTROL_PREEMPTED" in result.content[0].text
        assert owner._active_calls == 0
    finally:
        owner.stop()
