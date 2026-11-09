"""Snapshot and Screenshot must keep working when the virtual desktop query fails.

The internal virtual desktop manager is a proxy to an object that explorer.exe
serves, and each thread caches the manager it created. Once Explorer restarts,
every call on that proxy raises COMError. Desktop.get_state only caught
RuntimeError, so the error failed the whole capture, and the cached proxy made
every later capture fail the same way. A manager whose public interface could
not be created never set _internal_manager, so the fallback meant for builds
without virtual desktops raised AttributeError instead.

No COM object is created here: every manager is built around mocks.
"""

from unittest.mock import MagicMock, patch

import pytest
from _ctypes import COMError
from PIL import Image

from windows_mcp.desktop.service import Desktop
from windows_mcp.tree.views import BoundingBox, TreeState
from windows_mcp.vdm import core as vdm
from windows_mcp.vdm.core import VirtualDesktopManager

DEFAULT_DESKTOP = {"id": "00000000-0000-0000-0000-000000000000", "name": "Default Desktop"}
DESKTOP_IDS = [
    "{11111111-1111-1111-1111-111111111111}",
    "{22222222-2222-2222-2222-222222222222}",
]
_MISSING = object()

CAPTURES = pytest.mark.parametrize(
    ("use_ui_tree", "use_vision"),
    [(True, False), (False, True)],
    ids=["snapshot", "screenshot"],
)


@pytest.fixture(autouse=True)
def isolated_manager_cache(monkeypatch):
    """Keep real managers out of the tests and restore this thread's cached one."""
    # A real manager creates COM objects. Each test builds the managers it needs.
    unexpected = MagicMock(side_effect=AssertionError("unexpected VirtualDesktopManager()"))
    monkeypatch.setattr(vdm, "VirtualDesktopManager", unexpected)
    monkeypatch.setattr(
        VirtualDesktopManager, "_get_name_from_registry", lambda self, guid_str: None
    )
    saved = getattr(vdm._thread_local, "manager", _MISSING)
    yield
    if saved is _MISSING:
        vdm._thread_local.__dict__.pop("manager", None)
    else:
        vdm._thread_local.manager = saved


def disconnected() -> COMError:
    """What a call on a proxy raises once the process serving it has gone."""
    return COMError(
        -2147417848,  # RPC_E_DISCONNECTED
        "The object invoked has disconnected from its clients.",
        (None, None, None, 0, None),
    )


def make_manager(internal_manager) -> VirtualDesktopManager:
    manager = VirtualDesktopManager.__new__(VirtualDesktopManager)
    manager._manager = None
    manager._internal_manager = internal_manager
    return manager


def stale_manager() -> VirtualDesktopManager:
    internal = MagicMock()
    internal.GetCurrentDesktop.side_effect = disconnected()
    internal.GetDesktops.side_effect = disconnected()
    return make_manager(internal)


def working_manager() -> VirtualDesktopManager:
    desktops = []
    for desktop_id in DESKTOP_IDS:
        desktop = MagicMock()
        desktop.QueryInterface.return_value = desktop
        desktop.GetID.return_value = desktop_id
        desktops.append(desktop)
    internal = MagicMock()
    internal.GetCurrentDesktop.return_value = desktops[1]
    internal.GetDesktops.return_value.GetCount.return_value = len(desktops)
    internal.GetDesktops.return_value.GetAt.side_effect = lambda index, iid: desktops[index]
    return make_manager(internal)


def manager_without_public_interface() -> VirtualDesktopManager:
    """Run the real __init__ with CreateObject failing for the public manager."""
    with (
        patch.object(vdm.ctypes, "windll"),  # keeps CoInitialize from running
        patch.object(
            vdm.comtypes.client, "CreateObject", side_effect=OSError("Class not registered")
        ),
    ):
        return VirtualDesktopManager()


def make_desktop() -> Desktop:
    """A Desktop whose window, tree, cursor and screenshot calls are all mocked."""
    desktop = Desktop.__new__(Desktop)
    desktop.tree = MagicMock()
    desktop.tree.screen_box = BoundingBox(
        left=0, top=0, right=800, bottom=600, width=800, height=600
    )
    desktop.tree.get_state.return_value = TreeState(status=True)
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_controls_handles = MagicMock(return_value=[])
    desktop.get_windows = MagicMock(return_value=([], set()))
    desktop.get_active_window = MagicMock(return_value=None)
    desktop.get_cursor_location = MagicMock(return_value=(10, 20))
    desktop.get_screenshot = MagicMock(return_value=Image.new("RGB", (80, 60)))
    desktop.get_annotated_screenshot = MagicMock(return_value=Image.new("RGB", (80, 60)))
    return desktop


@CAPTURES
@pytest.mark.parametrize(
    "build_manager",
    [stale_manager, manager_without_public_interface],
    ids=["disconnected", "not-created"],
)
def test_capture_reports_the_default_desktop_when_the_query_fails(
    build_manager, use_ui_tree, use_vision
):
    vdm._thread_local.manager = build_manager()

    state = make_desktop().get_state(
        use_ui_tree=use_ui_tree, use_vision=use_vision, use_annotation=False
    )

    assert state.active_desktop == DEFAULT_DESKTOP
    assert state.all_desktops == [DEFAULT_DESKTOP]
    assert (state.screenshot is not None) == use_vision


def test_manager_without_public_interface_uses_the_fallback_and_stays_cached():
    manager = manager_without_public_interface()
    vdm._thread_local.manager = manager

    assert vdm.get_current_desktop() == DEFAULT_DESKTOP
    assert vdm.get_all_desktops() == [DEFAULT_DESKTOP]
    assert vdm._thread_local.manager is manager


@pytest.mark.parametrize(
    "query", [vdm.get_current_desktop, vdm.get_all_desktops], ids=lambda query: query.__name__
)
def test_failed_query_forgets_the_manager_so_the_next_call_reconnects(query):
    vdm._thread_local.manager = stale_manager()

    with pytest.raises(COMError):
        query()
    assert not hasattr(vdm._thread_local, "manager")

    fresh = working_manager()
    with patch.object(vdm, "VirtualDesktopManager", return_value=fresh):
        query()
    assert vdm._thread_local.manager is fresh


@CAPTURES
def test_next_capture_reads_the_desktops_again(use_ui_tree, use_vision):
    vdm._thread_local.manager = stale_manager()
    desktop = make_desktop()

    first = desktop.get_state(use_ui_tree=use_ui_tree, use_vision=use_vision, use_annotation=False)
    with patch.object(vdm, "VirtualDesktopManager", return_value=working_manager()) as reconnect:
        second = desktop.get_state(
            use_ui_tree=use_ui_tree, use_vision=use_vision, use_annotation=False
        )

    assert first.active_desktop == DEFAULT_DESKTOP
    reconnect.assert_called_once_with()
    assert second.active_desktop == {"id": DESKTOP_IDS[1], "name": "Desktop 2"}
    assert second.all_desktops == [
        {"id": DESKTOP_IDS[0], "name": "Desktop 1"},
        {"id": DESKTOP_IDS[1], "name": "Desktop 2"},
    ]
