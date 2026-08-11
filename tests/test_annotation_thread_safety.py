"""
Regression test for the ImageDraw race condition in
Desktop.get_annotated_screenshot().

Background
-----------
The old code drew every element's bounding box + label onto ONE shared
ImageDraw.Draw object from multiple ThreadPoolExecutor worker threads.
PIL/Pillow does not guarantee ImageDraw is safe to share across threads,
and CPython can pre-empt a thread between any two of the several draw
calls that make up a single annotation (rectangle -> textlength ->
rectangle -> text). When that happens, two threads' writes interleave and
a label's background/box is partially or never drawn.

What this test checks
----------------------
Given IDENTICAL input (same nodes, same deterministic "random" color),
rendering the same screenshot twice must produce byte-identical images.
Any difference between runs proves non-deterministic behavior, i.e. a
race. This does NOT prove the function is race-free (races are
probabilistic), but it will reliably catch a regression back to the
threaded implementation, because that implementation is nondeterministic
under load by construction (see race_repro_demo.py / force_repro.py for
a minimal, dependency-free reproduction of the exact mechanism, which
showed 25/25 divergent outputs when re-threaded).

Notes
-----
- This file imports the real `Desktop` class, so it only runs where
  pywin32 / windows_mcp's Windows-only dependencies are installed
  (i.e. on Windows, or wherever your CI already runs the rest of the
  windows_mcp test suite). It will fail to import on Linux/macOS.
- Adjust the import path below to match your repo layout if it differs.
"""
import hashlib
import io
import sys

import pytest
from PIL import Image

# Adjust this import if Desktop lives elsewhere in your repo.
from windows_mcp.desktop.service import Desktop
from windows_mcp.tree.views import BoundingBox, TreeElementNode

NUM_NODES = 150
TRIALS = 15


def _make_nodes(n: int) -> list[TreeElementNode]:
    """Deliberately overlapping/adjacent boxes -- this is what makes
    interleaved writes from different threads land near/inside each
    other's regions instead of in disjoint, harmless areas."""
    nodes = []
    cols = 12
    box_w, box_h = 140, 40
    for i in range(n):
        col, row = i % cols, i // cols
        left = 20 + col * (box_w - 20)
        top = 20 + row * (box_h - 15)
        box = BoundingBox(
            left=left, top=top, right=left + box_w, bottom=top + box_h,
            width=box_w, height=box_h,
        )
        nodes.append(
            TreeElementNode(
                name=f"node-{i}", control_type="Button", bounding_box=box,
                center=box.get_center(), window_name="TestWindow", metadata={},
            )
        )
    return nodes


def _image_hash(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


@pytest.fixture
def desktop(monkeypatch):
    d = Desktop()
    # Bypass real screen capture -- we only care about the drawing step.
    monkeypatch.setattr(d, "get_screenshot", lambda capture_rect=None:
                         Image.new("RGB", (2000, 1400), color=(255, 255, 255)))
    # Make color assignment order-independent: fixed value regardless of
    # call order, so the ONLY source of run-to-run difference left is
    # thread interleaving itself, not scheduling-dependent color choice.
    import windows_mcp.desktop.service as core_module
    monkeypatch.setattr(core_module.random, "randint", lambda a, b: 0x336699)
    return d


@pytest.fixture(autouse=True)
def _tight_gil_switching():
    """Pre-empt threads far more often than CPython's 5ms default so a
    latent race gets exercised instead of getting lucky on scheduling."""
    original = sys.getswitchinterval()
    sys.setswitchinterval(0.0000001)
    yield
    sys.setswitchinterval(original)


def test_annotated_screenshot_is_deterministic(desktop):
    """Same nodes in -> byte-identical PNG out, every single time.

    This FAILS (multiple distinct hashes) if get_annotated_screenshot
    goes back to drawing from a ThreadPoolExecutor onto a shared
    ImageDraw object, and PASSES reliably with the sequential for-loop.
    """
    nodes = _make_nodes(NUM_NODES)

    hashes = set()
    for _ in range(TRIALS):
        img = desktop.get_annotated_screenshot(nodes=nodes, cursor_pos=None,
                                                grid_lines=None, capture_rect=None)
        hashes.add(_image_hash(img))

    assert len(hashes) == 1, (
        f"get_annotated_screenshot produced {len(hashes)} different outputs "
        f"across {TRIALS} identical-input trials -- this indicates a race "
        f"condition (e.g. concurrent writes to a shared ImageDraw object)."
    )
