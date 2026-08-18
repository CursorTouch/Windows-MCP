import ctypes
from types import SimpleNamespace

import pytest

from windows_mcp.desktop import window as window_module
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.window import ExactWindowController


class FakeDesktop:
    def __init__(self) -> None:
        self.activations: list[int] = []

    def bring_window_to_top(self, handle: int) -> None:
        self.activations.append(handle)


def _patch_window_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    foreground: int = 100,
) -> list[tuple[int, int, int, int, int, bool]]:
    moves: list[tuple[int, int, int, int, int, bool]] = []
    outer_rect = {"value": (10, 20, 210, 170)}
    client_rect = {"value": (0, 0, 180, 120)}
    client_origin = {"value": (20, 50)}
    monkeypatch.setattr(
        window_module.win32gui,
        "EnumWindows",
        lambda callback, context: callback(100, context),
    )
    monkeypatch.setattr(window_module.win32gui, "IsWindow", lambda handle: handle == 100)
    monkeypatch.setattr(window_module.win32gui, "GetWindowText", lambda handle: "Target App")
    monkeypatch.setattr(window_module.uia, "IsIconic", lambda handle: False)
    monkeypatch.setattr(window_module.uia, "IsZoomed", lambda handle: False)
    monkeypatch.setattr(window_module.uia, "IsWindowVisible", lambda handle: True)
    monkeypatch.setattr(
        window_module.win32process,
        "GetWindowThreadProcessId",
        lambda handle: (300, 200),
    )
    monkeypatch.setattr(
        window_module.win32gui,
        "GetWindowRect",
        lambda handle: outer_rect["value"],
    )
    monkeypatch.setattr(
        window_module.win32gui,
        "GetClientRect",
        lambda handle: client_rect["value"],
    )
    monkeypatch.setattr(
        window_module.win32gui,
        "ClientToScreen",
        lambda handle, point: client_origin["value"],
    )
    monkeypatch.setattr(
        window_module.win32gui,
        "GetForegroundWindow",
        lambda: foreground,
    )

    def move_window(
        handle: int,
        x: int,
        y: int,
        width: int,
        height: int,
        repaint: bool,
    ) -> None:
        moves.append((handle, x, y, width, height, repaint))
        outer_rect["value"] = (x, y, x + width, y + height)
        client_origin["value"] = (x + 10, y + 30)
        client_rect["value"] = (0, 0, width - 20, height - 30)

    monkeypatch.setattr(window_module.win32gui, "MoveWindow", move_window)
    monkeypatch.setattr(
        window_module,
        "Process",
        lambda pid: type(
            "P",
            (),
            {
                "name": lambda self: "app.exe",
                "exe": lambda self: r"C:\Apps\app.exe",
            },
        )(),
    )
    return moves


def test_find_exact_windows_filters_by_process_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    _patch_window_api(monkeypatch)

    result = controller.find_exact_windows(title="target", process="APP.EXE")

    assert result == [
        {
            "handle": 100,
            "process_id": 200,
            "process": "app.exe",
            "process_path": r"C:\Apps\app.exe",
            "title": "Target App",
            "status": "Normal",
            "outer": {
                "left": 10,
                "top": 20,
                "width": 200,
                "height": 150,
                "right": 210,
                "bottom": 170,
            },
            "client": {
                "left": 20,
                "top": 50,
                "width": 180,
                "height": 120,
                "right": 200,
                "bottom": 170,
            },
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"title": ""}, "title must not be empty"),
        ({"process": ""}, "process must not be empty"),
        ({"handle": True}, "handle must be a positive integer"),
        ({"process_id": 0}, "process_id must be a positive integer"),
        ({"title_match": "prefix"}, 'title_match must be "exact" or "contains"'),
    ],
)
def test_find_rejects_invalid_identity_before_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    message: str,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    monkeypatch.setattr(
        window_module.win32gui,
        "EnumWindows",
        lambda *args: pytest.fail("windows must not be enumerated"),
    )

    with pytest.raises(ValueError, match=message):
        controller.find_exact_windows(**kwargs)


def test_find_by_handle_does_not_depend_on_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ExactWindowController(FakeDesktop())
    _patch_window_api(monkeypatch)
    monkeypatch.setattr(
        window_module.win32gui,
        "EnumWindows",
        lambda *args: pytest.fail("windows must not be enumerated"),
    )

    result = controller.find_exact_windows(handle=100)

    assert result[0]["handle"] == 100


def test_activate_verifies_foreground_and_rereads_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    _patch_window_api(monkeypatch, foreground=999)
    foreground_reads = iter([999, 100])
    monkeypatch.setattr(
        window_module.win32gui,
        "GetForegroundWindow",
        foreground_reads.__next__,
    )
    reads: list[int] = []
    original_is_window = window_module.win32gui.IsWindow
    monkeypatch.setattr(
        window_module.win32gui,
        "IsWindow",
        lambda handle: reads.append(handle) or original_is_window(handle),
    )

    result = controller.activate_exact_window(handle=100, process_id=200)

    assert result["handle"] == 100
    assert desktop.activations == [100]
    assert reads.count(100) >= 2


def test_activate_already_foreground_skips_redundant_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    _patch_window_api(monkeypatch, foreground=100)

    result = controller.activate_exact_window(handle=100, process_id=200)

    assert result["handle"] == 100
    assert desktop.activations == []


def test_activate_fails_when_readback_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    _patch_window_api(monkeypatch, foreground=999)

    with pytest.raises(ValueError, match="Failed to activate"):
        controller.activate_exact_window(handle=100, process_id=200)


def test_set_client_bounds_converts_and_verifies_actual_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    moves = _patch_window_api(monkeypatch)
    monkeypatch.setattr(
        controller,
        "_outer_bounds_for_client",
        lambda handle, client: (20, 40, 320, 230),
    )

    result = controller.set_exact_window_bounds(
        handle=100,
        process_id=200,
        client=[30, 70, 300, 200],
    )

    assert moves == [(100, 20, 40, 320, 230, True)]
    assert result["client"] == {
        "left": 30,
        "top": 70,
        "width": 300,
        "height": 200,
        "right": 330,
        "bottom": 270,
    }


def test_set_client_bounds_corrects_native_frame_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    moves = _patch_window_api(monkeypatch)
    monkeypatch.setattr(
        controller,
        "_outer_bounds_for_client",
        lambda handle, client: (23, 48, 316, 220),
    )

    result = controller.set_exact_window_bounds(
        handle=100,
        client=[30, 70, 300, 200],
    )

    assert moves == [
        (100, 23, 48, 316, 220, True),
        (100, 20, 40, 320, 230, True),
    ]
    assert result["client"]["left"] == 30
    assert result["client"]["top"] == 70
    assert result["client"]["width"] == 300
    assert result["client"]["height"] == 200


def test_set_bounds_reports_actual_geometry_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDesktop()
    controller = ExactWindowController(desktop)
    _patch_window_api(monkeypatch)
    monkeypatch.setattr(window_module.win32gui, "MoveWindow", lambda *args: None)
    monkeypatch.setattr(window_module, "perf_counter", iter([0.0, 3.0]).__next__)

    with pytest.raises(
        TimeoutError,
        match=r"expected \[30, 40, 300, 200\].*actual.*left.*10",
    ):
        controller.set_exact_window_bounds(
            handle=100,
            process_id=200,
            outer=[30, 40, 300, 200],
        )


def test_client_adjustment_accounts_for_native_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ExactWindowController(FakeDesktop())
    menu_flags: list[bool] = []
    monkeypatch.setattr(window_module.win32gui, "GetWindowLong", lambda *args: 0)
    monkeypatch.setattr(window_module.win32gui, "GetMenu", lambda handle: 1)

    def adjust_window_rect(
        rect_pointer: object,
        style: int,
        has_menu: bool,
        ex_style: int,
        dpi: int,
    ) -> int:
        menu_flags.append(bool(has_menu))
        rect = ctypes.cast(
            rect_pointer,
            ctypes.POINTER(window_module.wintypes.RECT),
        ).contents
        rect.left = -10
        rect.top = -40
        rect.right = 310
        rect.bottom = 230
        return 1

    user32 = SimpleNamespace(
        GetDpiForWindow=lambda handle: 144,
        AdjustWindowRectExForDpi=adjust_window_rect,
    )
    monkeypatch.setattr(window_module.ctypes.windll, "user32", user32)

    result = controller._outer_bounds_for_client(100, [30, 70, 300, 190])

    assert result == (20, 30, 320, 270)
    assert menu_flags == [True]


def test_client_adjustment_falls_back_to_current_frame_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ExactWindowController(FakeDesktop())
    monkeypatch.setattr(
        window_module.win32gui,
        "GetWindowLong",
        lambda *args: (_ for _ in ()).throw(OSError("unsupported")),
    )
    monkeypatch.setattr(
        controller,
        "_outer_bounds",
        lambda handle: {"left": 10, "top": 20, "width": 200, "height": 150},
    )
    monkeypatch.setattr(
        controller,
        "_client_bounds",
        lambda handle: {"left": 20, "top": 50, "width": 180, "height": 120},
    )

    result = controller._outer_bounds_for_client(100, [30, 70, 300, 190])

    assert result == (20, 40, 320, 220)


@pytest.mark.parametrize(
    ("outer", "client"),
    [(None, None), ([0, 0, 100, 100], [0, 0, 100, 100])],
)
def test_set_bounds_requires_exactly_one_target(
    outer: list[int] | None,
    client: list[int] | None,
) -> None:
    controller = ExactWindowController(FakeDesktop())

    with pytest.raises(ValueError, match="exactly one"):
        controller.set_exact_window_bounds(handle=100, outer=outer, client=client)


def test_desktop_delegates_exact_window_operations() -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    controller = SimpleNamespace(
        find_exact_windows=lambda **kwargs: calls.append(("find", (), kwargs)) or ["found"],
        activate_exact_window=lambda *args: calls.append(("activate", args, {})) or "active",
        set_exact_window_bounds=lambda *args: calls.append(("bounds", args, {})) or "moved",
    )
    desktop = Desktop.__new__(Desktop)
    desktop._exact_window = controller

    assert desktop.find_exact_windows(title="Target") == ["found"]
    assert desktop.activate_exact_window(100, 200, "app.exe", "Target", "exact") == "active"
    assert (
        desktop.set_exact_window_bounds(
            100,
            [10, 20, 300, 200],
            None,
            200,
            "app.exe",
            "Target",
            "exact",
        )
        == "moved"
    )
    assert calls == [
        (
            "find",
            (),
            {
                "title": "Target",
                "title_match": "contains",
                "process": None,
                "process_id": None,
                "handle": None,
            },
        ),
        ("activate", (100, 200, "app.exe", "Target", "exact"), {}),
        (
            "bounds",
            (100, [10, 20, 300, 200], None, 200, "app.exe", "Target", "exact"),
            {},
        ),
    ]
