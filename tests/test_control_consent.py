"""Consent countdown before a new AI control period."""

import subprocess

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import control, control_consent, control_overlay, flash_overlay
from windows_mcp.desktop.control import ControlBlocked
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


def _ready_owner() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    owner._state = "ready"
    return owner


def _physical_input(owner: control.ControlCoordinator) -> None:
    owner._last_physical_event = control.time.monotonic()
    owner._handle(("point", 1, 1, 0x200))


def test_allowed_prompt_ignores_the_answering_input():
    owner = _ready_owner()
    assert owner.open_consent()
    _physical_input(owner)  # Moving to and clicking Allow.
    assert owner.status()["state"] == "ready"
    owner.close_consent("allow")
    owner._handle(("key",))  # Queued before the answer, consumed after it.
    assert owner.status()["state"] == "ready"
    owner.end_call(owner.begin_call("Click"))
    assert owner.status()["state"] == "ai"


def test_input_within_the_grace_after_allow_is_not_a_takeover():
    owner = _ready_owner()
    assert owner.open_consent()
    owner.close_consent("allow")
    _physical_input(owner)  # The hand leaving the mouse.
    assert owner.status()["state"] == "ready"
    owner.begin_call("Click")
    owner._visual_armed = False  # Before the indicator confirms it is visible.
    _physical_input(owner)
    owner._handle(("raw", 1, 900, 0, control.time.monotonic()))
    assert owner.status()["state"] == "ai"


def test_input_after_the_grace_is_still_user_activity(monkeypatch):
    monkeypatch.setattr(control, "_CONSENT_GRACE_SECONDS", 0.0)
    owner = _ready_owner()
    assert owner.open_consent()
    owner.close_consent("allow")
    _physical_input(owner)
    assert owner.status()["state"] == "user"


@pytest.mark.parametrize("answer", ["timeout", "deny", "error"])
def test_unanswered_or_refused_prompt_with_input_keeps_user_in_control(answer):
    owner = _ready_owner()
    assert owner.open_consent()
    _physical_input(owner)
    owner.close_consent(answer)
    assert owner.status()["state"] == "user"
    with pytest.raises(ControlBlocked, match="USER_CONTROL"):
        owner.begin_call("Click")


def test_unattended_timeout_grants_until_the_user_takes_over():
    owner = _ready_owner()
    assert owner.open_consent()
    owner.close_consent("timeout")
    assert owner.status()["state"] == "ready"
    assert not owner.open_consent()  # Granted consent is not asked again.
    _physical_input(owner)
    assert owner.status()["state"] == "user"
    owner._last_physical_event = owner._last_user = control.time.monotonic() - 60
    assert owner.status()["state"] == "ready"
    assert owner.open_consent()


def test_prompt_can_be_disabled_and_never_interrupts_a_lease():
    owner = _ready_owner()
    owner.consent_countdown_seconds = 0
    assert not owner.open_consent()
    owner.consent_countdown_seconds = 10
    owner._state = "ai"
    assert not owner.open_consent()


def test_active_user_is_asked_and_allow_hands_over():
    owner = _ready_owner()
    _physical_input(owner)
    assert owner.status()["state"] == "user"
    assert owner.open_consent()
    _physical_input(owner)  # Still working, then clicking Allow.
    owner.close_consent("allow")
    assert owner.status()["state"] == "ready"
    owner.end_call(owner.begin_call("Click"))
    assert owner.status()["state"] == "ai"


@pytest.mark.parametrize("answer", ["timeout", "deny"])
def test_declining_while_active_is_not_asked_again_until_idle(answer):
    owner = _ready_owner()
    _physical_input(owner)
    assert owner.open_consent()
    _physical_input(owner)
    owner.close_consent(answer)
    assert owner.status()["state"] == "user"
    assert not owner.would_ask() and not owner.open_consent()
    owner._last_physical_event = owner._last_user = control.time.monotonic() - 60
    assert owner.status()["state"] == "ready"
    assert owner.open_consent()


def test_taking_the_desktop_back_is_not_asked_again_until_idle():
    owner = _ready_owner()
    owner.end_call(owner.begin_call("Click"))
    owner._handle(("hotkey",))
    assert owner.status()["state"] == "user"
    assert not owner.would_ask() and not owner.open_consent()


@pytest.mark.asyncio
async def test_gate_asks_an_active_user_instead_of_refusing():
    owner = _ready_owner()
    _physical_input(owner)
    asked, effects = [], []
    mcp = FastMCP("consent")
    mcp.add_middleware(
        ControlToolGate(
            owner, ControlNotifier(owner), ask_consent=lambda s: asked.append(s) or "allow"
        )
    )

    @mcp.tool(name="Click")
    def click():
        effects.append("clicked")

    async with Client(mcp) as client:
        await client.call_tool("Click")
    assert asked == [10] and effects == ["clicked"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "code"),
    [("allow", None), ("deny", "CONTROL_DENIED"), ("error", "CONTROL_UNAVAILABLE")],
)
async def test_gate_asks_once_per_control_period(answer, code):
    owner = _ready_owner()
    asked, effects = [], []
    mcp = FastMCP("consent")
    mcp.add_middleware(
        ControlToolGate(
            owner, ControlNotifier(owner), ask_consent=lambda s: asked.append(s) or answer
        )
    )

    @mcp.tool(name="Click")
    def click():
        effects.append("clicked")

    async with Client(mcp) as client:
        result = await client.call_tool("Click", raise_on_error=False)
        if code:
            assert result.is_error and code in result.content[0].text
            assert effects == []
        else:
            await client.call_tool("Click")
            assert effects == ["clicked", "clicked"]
    assert asked == [10]


@pytest.mark.parametrize(("code", "answer"), [(10, "allow"), (12, "timeout"), (1, "error")])
def test_ask_maps_exit_status_to_answer(monkeypatch, code, answer):
    class FakeProcess:
        def poll(self):
            return code

        def wait(self, timeout=None):
            return code

    monkeypatch.setattr(control_consent, "_process", None)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    assert control_consent.ask(10) == answer


def test_consent_shows_amber_edges_without_notice_or_cursor(monkeypatch):
    built, shown = [], []

    class FakeLayer:
        hwnd = 1
        breathes = True

        def __init__(self, name):
            self.name = name

        def set_opacity(self, opacity):
            pass

        def show(self):
            shown.append(self.name)

        def hide(self):
            pass

        def move(self, x, y):
            pass

        def close(self):
            pass

    def build(rects, amber, beat=None, notice=True):
        built.append((amber, notice))
        return [FakeLayer("edge")], FakeLayer("ring")

    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    monkeypatch.setattr(control_overlay, "_build_layers", build)
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
    control_overlay.start()
    try:
        control_overlay.set_consent(True)
        assert built == [(True, False)] and shown == ["edge"]
        control_overlay.set_consent(False)
        control_overlay.set_active(True)
        assert built[-1] == (False, True) and "ring" in shown
    finally:
        control_overlay.stop()


def test_ai_control_never_expires_until_released(monkeypatch):
    owner = _ready_owner()
    assert owner.open_consent()
    owner.close_consent("allow")
    owner.end_call(owner.begin_call("Click"))
    real = control.time.monotonic
    monkeypatch.setattr(control.time, "monotonic", lambda: real() + 3600)  # Long thinking.
    owner._handle(("tick",))
    assert owner.status()["state"] == "ai"
    assert owner.release()["state"] == "ready"
    assert owner.release()["state"] == "ready"  # Idempotent.
    assert not owner.open_consent()  # Release keeps consent until the person takes over.
