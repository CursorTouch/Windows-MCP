"""Regression test for the annotated-screenshot drawing race.

Previously ``get_annotated_screenshot`` drew every annotation through a
``ThreadPoolExecutor`` that ran ``draw_annotation`` concurrently. All threads
wrote into the *same* PIL image via the same ``ImageDraw`` handle with no lock,
so identical input could produce different pixels (or crash the process)
depending on thread interleaving. Drawing is GIL-bound, so the parallelism
added risk without speed.

This test asserts that identical input always produces identical output —
which is only possible when the drawing is deterministic (i.e. sequential).
"""

import random
from types import SimpleNamespace

from PIL import Image

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop import service as desktop_service
from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode


def _make_nodes(seed: int = 42, count: int = 50) -> list[TreeElementNode]:
    rng = random.Random(seed)
    nodes = []
    for i in range(count):
        left = rng.randint(0, 1500)
        top = rng.randint(0, 900)
        width = rng.randint(50, 300)
        height = rng.randint(30, 200)
        nodes.append(
            TreeElementNode(
                bounding_box=BoundingBox(
                    left=left,
                    top=top,
                    right=left + width,
                    bottom=top + height,
                    width=width,
                    height=height,
                ),
                center=Center(x=left + width // 2, y=top + height // 2),
                name=f"Window {i}",
                control_type="PaneControl",
                window_name=f"Window {i}",
            )
        )
    return nodes


def _fake_capture(capture_rect=None):
    """Deterministic stand-in for the real screen capture backend."""
    image = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
    return image, "test"


def test_annotated_screenshot_is_deterministic(monkeypatch):
    """Identical input must yield identical pixels on every run.

    A race condition makes output timing-dependent, so any divergence across
    trials with identical input is a failure. Sequential drawing is the only
    way this can hold.
    """
    monkeypatch.setattr(desktop_service.screenshot_capture, "capture", _fake_capture)

    desktop = object.__new__(Desktop)  # skip heavy COM init; screenshot path only
    nodes = _make_nodes()
    capture_rect = SimpleNamespace(left=0, top=0)

    outputs = set()
    for _ in range(100):
        # Seed the global RNG so every trial draws the same colors. If the
        # draw were running in threads, the order in which workers consume
        # random values would depend on scheduling and outputs would differ.
        random.seed(42)
        outputs.add(
            desktop.get_annotated_screenshot(nodes=nodes, capture_rect=capture_rect).tobytes()
        )

    assert len(outputs) == 1, (
        "Annotated screenshot output is non-deterministic (race condition present)."
    )
