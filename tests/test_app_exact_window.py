import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.tools import app as app_module


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.tool_options: dict[str, dict[str, object]] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        self.tool_options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def find_exact_windows(self, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(("find", kwargs))
        return [{"handle": 100, "title": "Target App"}]

    def activate_exact_window(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("activate", kwargs))
        return {"handle": kwargs["handle"], "title": "Target App"}

    def set_exact_window_bounds(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("bounds", kwargs))
        return {"handle": kwargs["handle"], "outer": kwargs["outer"]}


def _register(get_desktop: Callable[[], object]) -> tuple[Callable, FakeMCP]:
    mcp = FakeMCP()
    app_module.register(mcp, get_desktop=get_desktop, get_analytics=lambda: None)
    return mcp.tools["App"], mcp


def test_app_annotations_cover_mutating_exact_modes() -> None:
    _, mcp = _register(FakeDesktop)

    assert set(mcp.tools) == {"App"}
    annotations = mcp.tool_options["App"]["annotations"]
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is True
    assert annotations.idempotentHint is False
    assert annotations.openWorldHint is False


def test_find_window_returns_native_structure_and_parses_identity() -> None:
    desktop = FakeDesktop()
    tool, _ = _register(lambda: desktop)

    result = asyncio.run(
        tool(
            mode="find_window",
            title="Target",
            title_match="exact",
            process="app.exe",
            process_id="200",
            handle="100",
        )
    )

    assert result == {
        "windows": [{"handle": 100, "title": "Target App"}],
        "count": 1,
    }
    assert desktop.calls == [
        (
            "find",
            {
                "title": "Target",
                "title_match": "exact",
                "process": "app.exe",
                "process_id": 200,
                "handle": 100,
            },
        )
    ]


def test_activate_window_requires_handle_before_desktop_access() -> None:
    tool, _ = _register(lambda: pytest.fail("desktop must not be accessed"))

    with pytest.raises(ValueError, match="handle is required"):
        asyncio.run(tool(mode="activate_window"))


def test_activate_window_delegates_assertions() -> None:
    desktop = FakeDesktop()
    tool, _ = _register(lambda: desktop)

    result = asyncio.run(
        tool(
            mode="activate_window",
            handle=100,
            title="Target",
            process_id=200,
        )
    )

    assert result == {"activated": {"handle": 100, "title": "Target App"}}
    assert desktop.calls[0] == (
        "activate",
        {
            "handle": 100,
            "process_id": 200,
            "process": None,
            "title": "Target",
            "title_match": "contains",
        },
    )


@pytest.mark.parametrize(
    ("argument", "value", "expected"),
    [
        ("outer", "[10, 20, 300, 200]", [10, 20, 300, 200]),
        ("client", [30, 40, 320, 180], [30, 40, 320, 180]),
    ],
)
def test_set_window_bounds_accepts_json_or_native_list(
    argument: str,
    value: object,
    expected: list[int],
) -> None:
    desktop = FakeDesktop()
    tool, _ = _register(lambda: desktop)

    result = asyncio.run(tool(mode="set_window_bounds", handle=100, **{argument: value}))

    assert result["window"]["handle"] == 100
    call = desktop.calls[0]
    assert call[0] == "bounds"
    assert call[1][argument] == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "unknown"},
        {"mode": "find_window", "title_match": "prefix"},
        {"mode": "find_window", "title": ""},
        {"mode": "find_window", "process": ""},
        {"mode": "find_window", "handle": True},
        {"mode": "find_window", "process_id": 0},
        {"mode": "find_window", "outer": [0, 0, 100, 100]},
        {"mode": "activate_window", "handle": 100, "client": [0, 0, 100, 100]},
        {"mode": "set_window_bounds", "handle": 100},
        {
            "mode": "set_window_bounds",
            "handle": 100,
            "outer": [0, 0, 100, 100],
            "client": [0, 0, 100, 100],
        },
        {"mode": "set_window_bounds", "handle": 100, "outer": 123},
        {"mode": "set_window_bounds", "handle": 100, "outer": "not-json"},
        {"mode": "set_window_bounds", "handle": 100, "outer": [0, 0, 0, 100]},
        {"mode": "set_window_bounds", "handle": 100, "outer": [0, 0, True, 100]},
    ],
)
def test_exact_modes_reject_invalid_options_before_desktop_access(
    kwargs: dict[str, object],
) -> None:
    tool, _ = _register(lambda: pytest.fail("desktop must not be accessed"))

    with pytest.raises(ValueError):
        asyncio.run(tool(**kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "launch", "handle": 100},
        {"mode": "resize", "title": "Target"},
        {"mode": "switch", "process_id": 200},
        {"mode": "launch", "title_match": "exact"},
    ],
)
def test_exact_arguments_are_rejected_for_legacy_modes(
    kwargs: dict[str, object],
) -> None:
    tool, _ = _register(lambda: pytest.fail("desktop must not be accessed"))

    with pytest.raises(ValueError, match="require an exact window mode"):
        asyncio.run(tool(**kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "find_window", "name": "Target"},
        {"mode": "activate_window", "handle": 100, "window_loc": [0, 0]},
        {
            "mode": "set_window_bounds",
            "handle": 100,
            "outer": [0, 0, 100, 100],
            "window_size": [100, 100],
        },
        {"mode": "find_window", "executable": r"C:\\App.exe"},
        {"mode": "activate_window", "handle": 100, "args": ["--flag"]},
        {"mode": "find_window", "cwd": r"C:\\work"},
    ],
)
def test_legacy_and_launch_arguments_are_rejected_for_exact_modes(
    kwargs: dict[str, object],
) -> None:
    tool, _ = _register(lambda: pytest.fail("desktop must not be accessed"))

    with pytest.raises(ValueError):
        asyncio.run(tool(**kwargs))
