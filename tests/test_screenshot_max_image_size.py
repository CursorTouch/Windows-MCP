"""WINDOWS_MCP_MAX_IMAGE_SIZE sets the screenshot cap that #638 reported as fixed at 1920x1080.

The default stays 1920x1080. ``WIDTHxHEIGHT`` changes it, ``none`` removes it, and an
invalid value is logged and ignored. Nothing here touches the real desktop: the Desktop
is a stand-in and only the arguments passed to get_state are checked.
"""

import logging
from unittest.mock import MagicMock

import pytest

from windows_mcp.desktop.service import Size
from windows_mcp.tools import _snapshot_helpers


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_MAX_IMAGE_SIZE", raising=False)
    monkeypatch.delenv("WINDOWS_MCP_SCREENSHOT_SCALE", raising=False)
    monkeypatch.setattr(_snapshot_helpers, "_logged_capped_scales", set(), raising=False)


def _size_of(value: Size | None) -> tuple[int, int] | None:
    return None if value is None else (value.width, value.height)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, (1920, 1080), id="unset"),
        pytest.param("", (1920, 1080), id="empty"),
        pytest.param("3840x2160", (3840, 2160), id="4k"),
        pytest.param(" 2560X1440 ", (2560, 1440), id="spaces-and-uppercase"),
        pytest.param("none", None, id="none"),
        pytest.param("OFF", None, id="off"),
        pytest.param("0", None, id="zero"),
    ],
)
def test_max_image_size_is_read_from_the_environment(monkeypatch, value, expected) -> None:
    if value is not None:
        monkeypatch.setenv("WINDOWS_MCP_MAX_IMAGE_SIZE", value)

    assert _size_of(_snapshot_helpers._max_image_size()) == expected


@pytest.mark.parametrize("value", ["big", "1920", "1920x", "x1080", "0x1080", "-1x5", "1x2x3"])
def test_invalid_max_image_size_falls_back_to_the_default_and_warns(
    monkeypatch, caplog, value
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_MAX_IMAGE_SIZE", value)

    assert _size_of(_snapshot_helpers._max_image_size()) == (1920, 1080)
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(messages) == 1
    assert "WINDOWS_MCP_MAX_IMAGE_SIZE" in messages[0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, (1920, 1080)), ("3840x2160", (3840, 2160)), ("none", None)],
)
def test_capture_passes_the_configured_cap_to_get_state(monkeypatch, value, expected) -> None:
    if value is not None:
        monkeypatch.setenv("WINDOWS_MCP_MAX_IMAGE_SIZE", value)
    desktop = MagicMock()

    _snapshot_helpers.capture_desktop_state(
        desktop,
        use_vision=False,
        use_dom=False,
        use_annotation=False,
        use_ui_tree=False,
        width_reference_line=None,
        height_reference_line=None,
        display=None,
        region=None,
        tool_name="Screenshot",
    )

    cap = desktop.get_state.call_args.kwargs["max_image_size"]
    assert _size_of(cap) == expected


def _lowered_state() -> MagicMock:
    state = MagicMock()
    state.screenshot_scale = 0.5
    state.screenshot_original_size = Size(width=3840, height=2160)
    return state


def test_warning_names_the_configured_cap(monkeypatch, caplog) -> None:
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", "0.75")

    _snapshot_helpers._log_capped_scale(0.75, _lowered_state(), Size(width=2560, height=1440))

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert messages == [
        "WINDOWS_MCP_SCREENSHOT_SCALE 0.75 lowered to 0.5 for a 3840x2160 capture: "
        "screenshots are capped at 2560x1440"
    ]


def test_no_warning_without_a_cap(monkeypatch, caplog) -> None:
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", "0.75")

    _snapshot_helpers._log_capped_scale(0.75, _lowered_state(), None)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
