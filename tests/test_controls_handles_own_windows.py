from unittest.mock import patch

from windows_mcp.desktop.service import Desktop

SERVICE = "windows_mcp.desktop.service"


def _handles(owner_pids, own_pid=4242):
    """Run get_controls_handles over fake top-level windows {hwnd: owner pid}."""

    def enum_windows(callback, extra):
        for hwnd in owner_pids:
            callback(hwnd, extra)

    def owner(hwnd):
        pid = owner_pids[hwnd]
        if pid is None:
            raise OSError("invalid window handle")
        return (1, pid)

    with (
        patch.object(Desktop, "__init__", lambda self: None),
        patch(f"{SERVICE}.os.getpid", return_value=own_pid),
        patch(f"{SERVICE}.win32gui.EnumWindows", side_effect=enum_windows),
        patch(f"{SERVICE}.win32gui.IsWindow", return_value=True),
        patch(f"{SERVICE}.win32gui.IsWindowVisible", return_value=True),
        patch(f"{SERVICE}.is_window_on_current_desktop", return_value=True),
        patch(f"{SERVICE}.win32gui.FindWindow", return_value=0),
        patch(f"{SERVICE}.win32process.GetWindowThreadProcessId", side_effect=owner),
    ):
        return Desktop().get_controls_handles()


def test_skips_windows_owned_by_the_server_process():
    # 11 and 12 stand for the control overlay and a click flash, created by this process.
    assert _handles({10: 100, 11: 4242, 12: 4242, 13: 200}) == {10, 13}


def test_keeps_a_window_whose_owner_cannot_be_read():
    assert _handles({10: 100, 14: None}) == {10, 14}
