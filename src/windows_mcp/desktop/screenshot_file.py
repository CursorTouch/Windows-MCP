"""Write a captured screenshot to disk for the Snapshot/Screenshot save_path option."""

import os
from datetime import datetime
from pathlib import Path


def save_screenshot(screenshot_bytes: bytes | None, save_path: str) -> str:
    """Write the returned PNG to *save_path* and return the absolute file path.

    *save_path* must be absolute (``~`` and environment variables are expanded).
    An existing directory, or a path ending in a separator, gets a timestamped
    ``screenshot-*.png`` name. Existing files are never overwritten.
    """
    if not screenshot_bytes:
        raise ValueError("No screenshot was captured, so nothing could be saved.")
    raw = save_path.strip()
    if not raw:
        raise ValueError("save_path must not be empty.")
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        raise ValueError(f"save_path must be an absolute path, got {save_path!r}.")
    if path.is_dir() or raw.endswith(("/", "\\")):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = path / f"screenshot-{stamp}.png"
    elif path.suffix.lower() != ".png":
        path = path.with_name(path.name + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    # "xb" refuses to replace a file that is already there.
    with open(path, "xb") as file:
        file.write(screenshot_bytes)
    return str(path.resolve())
