"""One table for desktop ownership, separate from authorization or data safety.

When adding a tool or a mode, classify it here. Registration coverage tests catch
omissions; unclassified extensions conservatively retain the desktop gate.
"""

# Categories describe desktop interference, not whether an operation is destructive.
DIRECT = frozenset({"Click", "Type", "Scroll", "Move", "Shortcut", "MultiSelect", "MultiEdit"})
POSSIBLE = frozenset({"PowerShell", "Notification"})
OTHER = frozenset(
    {
        "FileSystem",
        "Registry",
        "Snapshot",
        "Screenshot",
        "Scrape",
        "Wait",
        "WaitFor",
        "DisplayInventory",
    }
)
MODES = {
    "App": {
        "launch": "possible",
        "launch_executable": "possible",
        "resize": "direct",
        "switch": "direct",
    },
    "Process": {"list": "other", "kill": "possible"},
    "Clipboard": {"get": "other", "set": "possible"},
}
CLASSIFIED_TOOLS = DIRECT | POSSIBLE | OTHER | MODES.keys() | {"ControlStatus"}


def requires_control(policy: str, name: str, arguments: dict | None = None) -> bool:
    """Decide before taking the ownership lock; never inspect arbitrary scripts."""
    if name == "ControlStatus":
        return False
    if policy == "strict":
        return True
    if policy not in ("relaxed", "balanced"):
        raise ValueError(f"Unknown control policy: {policy}")
    category = "direct"  # Unknown tools/modes must not silently bypass ownership.
    if name in MODES:
        mode = (arguments or {}).get("mode", "launch" if name == "App" else None)
        if isinstance(mode, str):
            category = MODES[name].get(mode, "direct")
    elif name in POSSIBLE:
        category = "possible"
    elif name in OTHER:
        category = "other"
    return category == "direct" or (policy == "balanced" and category == "possible")
