from unittest.mock import patch

from windows_mcp.desktop.service import Desktop

SERVICE = "windows_mcp.desktop.service"


def _handles(owner_pids, own_pid=4242, shell_windows=None):
    """Run get_controls_handles over fake top-level windows {hwnd: owner pid}, in EnumWindows order.

    shell_windows maps the window classes that are looked up with FindWindow to their handles.
    """
    shell_windows = shell_windows or {}

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
        patch(
            f"{SERVICE}.win32gui.FindWindow",
            side_effect=lambda class_name, _title: shell_windows.get(class_name, 0),
        ),
        patch(f"{SERVICE}.win32process.GetWindowThreadProcessId", side_effect=owner),
    ):
        return Desktop().get_controls_handles()


def test_skips_windows_owned_by_the_server_process():
    # 11 and 12 stand for the control overlay and a click flash, created by this process.
    assert _handles({10: 100, 11: 4242, 12: 4242, 13: 200}) == [10, 13]


def test_keeps_a_window_whose_owner_cannot_be_read():
    assert _handles({10: 100, 14: None}) == [10, 14]


def test_keeps_the_enumwindows_z_order():
    # EnumWindows reports the windows front to back; a set would iterate these as 10, 20, 30.
    assert _handles({30: 100, 10: 200, 20: 300}) == [30, 10, 20]


def test_shell_windows_keep_their_place_and_are_not_repeated():
    # EnumWindows usually reports the taskbar (40) and the desktop (50) too. A shell window that
    # it did not report (60) goes after the others.
    shell_windows = {"Shell_TrayWnd": 40, "Progman": 50, "Shell_SecondaryTrayWnd": 60}
    handles = _handles({40: 100, 30: 200, 10: 300, 50: 100}, shell_windows=shell_windows)
    assert handles == [40, 30, 10, 50, 60]
