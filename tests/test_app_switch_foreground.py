from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState, Status, Window
from windows_mcp.tools import app
from windows_mcp.tree.views import BoundingBox


@pytest.fixture
def switch_desktop(monkeypatch, sample_window):
    desktop = Desktop.__new__(Desktop)
    desktop._find_window_by_name = Mock(return_value=(sample_window, ""))
    desktop.bring_window_to_top = Mock()
    monkeypatch.setattr(service.uia, "IsIconic", lambda _: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: sample_window.handle)

    clock = {"elapsed": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["elapsed"] += seconds

    monkeypatch.setattr(service, "perf_counter", lambda: clock["elapsed"])
    monkeypatch.setattr(service, "sleep", sleep)
    return desktop, clock


@pytest.mark.parametrize("minimized", [False, True])
def test_switch_succeeds_only_after_target_is_foreground(
    switch_desktop, sample_window, monkeypatch, minimized
):
    desktop, clock = switch_desktop
    monkeypatch.setattr(service.uia, "IsIconic", lambda _: minimized)

    response, status = desktop.switch_app("Notepad")

    assert status == 0
    assert response == (
        f"Restored {sample_window.name.title()} from minimized and switched to it."
        if minimized
        else f"Switched to {sample_window.name.title()} window."
    )
    desktop.bring_window_to_top.assert_called_once_with(sample_window.handle)
    assert clock["sleeps"] == []


def test_switch_waits_for_delayed_foreground(switch_desktop, sample_window, monkeypatch):
    desktop, clock = switch_desktop
    foreground = Mock(side_effect=[0, 999, sample_window.handle])
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", foreground)

    assert desktop.switch_app("Notepad")[1] == 0
    assert foreground.call_count == 3
    assert 0 < clock["elapsed"] < 1.0


@pytest.mark.parametrize("foreground_handle", [0, 999])
def test_switch_rejects_unchanged_foreground_with_bounded_wait(
    switch_desktop, sample_window, monkeypatch, foreground_handle
):
    desktop, clock = switch_desktop
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: foreground_handle)

    response, status = desktop.switch_app("Notepad")

    assert status == 1
    assert "Failed to bring" in response
    assert sample_window.name in response
    assert "foreground" in response
    assert 1.0 <= clock["elapsed"] <= 1.05
    desktop.bring_window_to_top.assert_called_once_with(sample_window.handle)


def test_switch_does_not_activate_a_missing_window(switch_desktop):
    desktop, clock = switch_desktop
    desktop._find_window_by_name.return_value = (None, "Application not found.")

    assert desktop.switch_app("missing") == ("Application not found.", 1)
    desktop.bring_window_to_top.assert_not_called()
    assert clock["sleeps"] == []


def test_switch_reports_foreground_query_failure(switch_desktop, monkeypatch):
    desktop, _ = switch_desktop
    monkeypatch.setattr(
        service.win32gui, "GetForegroundWindow", Mock(side_effect=RuntimeError("query failed"))
    )

    assert desktop.switch_app("Notepad") == ("Error switching app: query failed", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreground_matches", [False, True])
async def test_app_tool_reports_switch_failure_as_mcp_error(
    switch_desktop, sample_window, monkeypatch, foreground_matches
):
    desktop, _ = switch_desktop
    monkeypatch.setattr(
        service.win32gui,
        "GetForegroundWindow",
        lambda: sample_window.handle if foreground_matches else 999,
    )
    server = FastMCP("switch-foreground-test")
    app.register(server, get_desktop=lambda: desktop, get_analytics=lambda: None)

    async with Client(server) as client:
        result = await client.call_tool(
            "App", {"mode": "switch", "name": "Notepad"}, raise_on_error=False
        )

    assert result.is_error is (not foreground_matches)
    assert ("Switched to" if foreground_matches else "Failed to bring") in result.content[0].text


@pytest.mark.parametrize("refresh_state", [False, True])
def test_find_window_by_name_lists_windows_without_tree_walk(sample_window, refresh_state):
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    desktop.get_state = Mock(side_effect=AssertionError("get_state walks the UI tree"))
    desktop.get_windows = Mock(return_value=([sample_window], {sample_window.handle}))

    assert desktop._find_window_by_name("Notepad", refresh_state=refresh_state) == (
        sample_window,
        "",
    )
    desktop.get_state.assert_not_called()
    desktop.get_windows.assert_called_once_with()


def test_find_window_by_name_reports_empty_window_list():
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    desktop.get_windows = Mock(return_value=([], set()))

    assert desktop._find_window_by_name("Notepad") == (None, "No windows found on the desktop.")


def _window(
    name: str, handle: int, *, left: int = 100, top: int = 100, status: Status = Status.NORMAL
) -> Window:
    return Window(
        name=name,
        is_browser=False,
        depth=0,
        status=status,
        bounding_box=BoundingBox(
            left=left, top=top, right=left + 800, bottom=top + 600, width=800, height=600
        ),
        handle=handle,
        process_id=handle,
    )


def _list_windows(*windows: Window) -> Mock:
    return Mock(return_value=(list(windows), {window.handle for window in windows}))


NOTEPAD = _window("Untitled - Notepad", 100)
OUTLOOK = _window("Inbox - Outlook", 200)


@pytest.fixture
def snapshot_desktop(monkeypatch):
    """A Desktop whose last Snapshot listed Notepad (active) and Outlook."""
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = DesktopState(
        active_desktop={"id": "1", "name": "Desktop 1"},
        all_desktops=[{"id": "1", "name": "Desktop 1"}],
        active_window=NOTEPAD,
        windows=[OUTLOOK],
    )
    desktop.get_state = Mock(side_effect=AssertionError("get_state walks the UI tree"))
    foreground = [NOTEPAD.handle]
    desktop.bring_window_to_top = Mock(side_effect=foreground.append)
    monkeypatch.setattr(service.uia, "IsIconic", lambda _: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: foreground[-1])
    return desktop


def test_switch_finds_a_window_opened_after_the_last_snapshot(snapshot_desktop):
    desktop = snapshot_desktop
    desktop.get_windows = _list_windows(NOTEPAD, OUTLOOK, _window("Calculator", 300))

    assert desktop.switch_app("Calculator") == ("Switched to Calculator window.", 0)
    desktop.get_windows.assert_called_once_with()
    desktop.bring_window_to_top.assert_called_once_with(300)


def test_switch_uses_the_current_handle_of_a_reopened_window(snapshot_desktop):
    desktop = snapshot_desktop
    desktop.get_windows = _list_windows(NOTEPAD, _window("Inbox - Outlook", 201))

    assert desktop.switch_app("Outlook") == ("Switched to Inbox - Outlook window.", 0)
    desktop.bring_window_to_top.assert_called_once_with(201)


def test_switch_reports_a_window_closed_since_the_last_snapshot(snapshot_desktop):
    desktop = snapshot_desktop
    desktop.get_windows = _list_windows(NOTEPAD)

    assert desktop.switch_app("Outlook") == ("Application Outlook not found.", 1)
    desktop.bring_window_to_top.assert_not_called()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Outlook", "Switched to Inbox - Outlook window."),
        ("Teams", "Restored Chat | Microsoft Teams from minimized and switched to it."),
    ],
)
def test_switch_finds_windows_a_display_snapshot_left_out(
    snapshot_desktop, monkeypatch, name, expected
):
    desktop = snapshot_desktop
    edge = _window("Bing - Microsoft Edge", 400)
    outlook_on_display_1 = _window("Inbox - Outlook", 500, left=2100)
    teams = _window("Chat | Microsoft Teams", 600, left=-32000, top=-32000, status=Status.MINIMIZED)
    display_0 = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)
    # Snapshot(display=[0]) keeps only the windows on that display; a minimized one sits at -32000.
    desktop.desktop_state.windows = desktop._filter_windows_to_region(
        [edge, outlook_on_display_1, teams], display_0
    )
    assert desktop.desktop_state.windows == [edge]
    desktop.get_windows = _list_windows(NOTEPAD, edge, outlook_on_display_1, teams)
    monkeypatch.setattr(service.uia, "IsIconic", lambda handle: handle == teams.handle)

    assert desktop.switch_app(name) == (expected, 0)
