from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


_ACTION_MAP: dict[str, str] = {
    "edit": "fill",
    "check box": "toggle",
    "checkbox": "toggle",
    "combo box": "select",
    "combobox": "select",
    "slider": "slide",
    "radio button": "select",
    "document": "scroll",
}


def _action_for(control_type: str) -> str:
    return _ACTION_MAP.get(control_type.lower(), "click")


def _node_meta_str(metadata: dict[str, Any]) -> str:
    parts = []
    if metadata.get("has_focused"):
        parts.append("focused")
    if metadata.get("is_password"):
        parts.append("password")
    value = metadata.get("value")
    if value and value != "(empty)":
        parts.append(f'value:"{value}"')
    toggle = metadata.get("toggle_state")
    if toggle:
        parts.append(f"toggle:{toggle}")
    state = metadata.get("expand_collapse_state")
    if state:
        parts.append(f"state:{state}")
    shortcut = metadata.get("shortcut")
    if shortcut:
        parts.append(f"shortcut:{shortcut}")
    if not parts:
        return ""
    return "  " + "  ".join(f"[{p}]" for p in parts)


def _scroll_meta_str(metadata: dict[str, Any]) -> str:
    parts = []
    if metadata.get("has_focused"):
        parts.append("focused")
    if metadata.get("vertical_scrollable"):
        pct = metadata.get("vertical_scroll_percent", 0)
        parts.append(f"v:{pct}%")
    if metadata.get("horizontal_scrollable"):
        pct = metadata.get("horizontal_scroll_percent", 0)
        parts.append(f"h:{pct}%")
    if not parts:
        return ""
    return "  " + "  ".join(f"[{p}]" for p in parts)


def _render_tree(nodes: list, meta_fn) -> str:
    windows: dict[str, list] = {}
    for node in nodes:
        windows.setdefault(node.window_name, []).append(node)

    lines = []
    for window_name, window_nodes in windows.items():
        lines.append(f'window "{window_name}"')
        for i, node in enumerate(window_nodes):
            connector = "└──" if i == len(window_nodes) - 1 else "├──"
            coords = node.center.to_string()
            ctrl = node.control_type.lower()
            name = node.name
            action = _action_for(ctrl)
            meta = meta_fn(node.metadata)
            lines.append(f'{connector} {coords} {ctrl} "{name}"  [action: {action}]{meta}')
        lines.append("")
    return "\n".join(lines).rstrip()


def _truncation_note(element_limit: int) -> str:
    return (
        f"... [truncated: reached the {element_limit}-element capture limit — "
        "some elements were not visited. Narrow the view (filter by window, scroll to "
        "the area of interest, use_dom=True for a browser page) or raise "
        "WINDOWS_MCP_MAX_TREE_ELEMENTS for a complete tree.]"
    )


# ---------------------------------------------------------------------------
# Semantic tree — full parent-child hierarchy (Option B)
# ---------------------------------------------------------------------------

@dataclass
class SemanticNode:
    """A node in the semantic UI tree.

    element_type is one of:
      'desktop'     — synthetic root
      'window'      — top-level window
      'structural'  — named container (toolbar, group, pane, …)
      'interactive' — actionable element with (x,y) coords
      'scrollable'  — scrollable region with (x,y) coords
    """
    control_type: str
    element_type: str
    name: str = ""
    window_name: str = ""
    center: Optional["Center"] = None
    bounding_box: Optional["BoundingBox"] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["SemanticNode"] = field(default_factory=list)

    def add_child(self, child: "SemanticNode") -> None:
        self.children.append(child)


def _format_semantic_node(node: SemanticNode) -> str:
    ctrl = node.control_type.lower()
    name = node.name
    if node.element_type == "window":
        return f'window "{name}"'
    if node.element_type == "structural":
        return f'{ctrl} "{name}"'
    if node.element_type in ("interactive", "scrollable"):
        coords = node.center.to_string() if node.center else "(?)"
        action = _action_for(ctrl)
        meta = _node_meta_str(node.metadata) if node.element_type == "interactive" else _scroll_meta_str(node.metadata)
        return f'{coords} {ctrl} "{name}"  [action: {action}]{meta}'
    return f'{ctrl} "{name}"'


def _render_semantic_node(node: SemanticNode, lines: list[str], prefix: str, is_last: bool) -> None:
    if node.element_type == "desktop":
        lines.append("desktop")
    else:
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{_format_semantic_node(node)}")

    if not node.children:
        return

    extension = "    " if is_last else "│   "
    new_prefix = prefix + extension
    for i, child in enumerate(node.children):
        _render_semantic_node(child, lines, new_prefix, i == len(node.children) - 1)


def _prune_structural(node: SemanticNode) -> bool:
    """Remove structural nodes that ended up with no children. Returns True = keep."""
    node.children = [c for c in node.children if _prune_structural(c)]
    if node.element_type == "structural" and not node.children:
        return False
    return True


def _reverse_children_order(node: SemanticNode) -> None:
    """Fix child ordering: tree_traversal visits reversed(children) so we reverse back."""
    node.children.reverse()
    for child in node.children:
        _reverse_children_order(child)


@dataclass
class TreeState:
    status: bool = True
    root_node: "TreeElementNode | None" = None
    dom_node: "ScrollElementNode | None" = None
    interactive_nodes: list["TreeElementNode"] = field(default_factory=list)
    scrollable_nodes: list["ScrollElementNode"] = field(default_factory=list)
    dom_informative_nodes: list["TextElementNode"] = field(default_factory=list)
    capture_sec: float = 0.0
    semantic_tree_root: "SemanticNode | None" = None
    truncated: bool = False
    element_limit: int = 0

    def semantic_tree_to_string(self) -> str:
        if not self.semantic_tree_root:
            return "No elements"
        lines: list[str] = []
        _render_semantic_node(self.semantic_tree_root, lines, "", is_last=True)
        text = "\n".join(lines)
        if self.truncated:
            text += "\n\n" + _truncation_note(self.element_limit)
        return text

    def interactive_elements_to_string(self) -> str:
        if not self.interactive_nodes:
            return "No interactive elements"
        text = _render_tree(self.interactive_nodes, _node_meta_str)
        if self.truncated:
            text += "\n\n" + _truncation_note(self.element_limit)
        return text

    def scrollable_elements_to_string(self) -> str:
        if not self.scrollable_nodes:
            return "No scrollable elements"
        text = _render_tree(self.scrollable_nodes, _scroll_meta_str)
        if self.truncated:
            text += "\n\n" + _truncation_note(self.element_limit)
        return text


@dataclass
class BoundingBox:
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int

    @classmethod
    def from_bounding_rectangle(cls, bounding_rectangle: Any) -> "BoundingBox":
        return cls(
            left=bounding_rectangle.left,
            top=bounding_rectangle.top,
            right=bounding_rectangle.right,
            bottom=bounding_rectangle.bottom,
            width=bounding_rectangle.width(),
            height=bounding_rectangle.height(),
        )

    def get_center(self) -> "Center":
        return Center(x=self.left + self.width // 2, y=self.top + self.height // 2)

    def xywh_to_string(self):
        return f"({self.left},{self.top},{self.width},{self.height})"

    def xyxy_to_string(self):
        x1, y1, x2, y2 = self.convert_xywh_to_xyxy()
        return f"({x1},{y1},{x2},{y2})"

    def convert_xywh_to_xyxy(self) -> tuple[int, int, int, int]:
        x1, y1 = self.left, self.top
        x2, y2 = self.left + self.width, self.top + self.height
        return x1, y1, x2, y2


@dataclass
class Center:
    x: int
    y: int

    def to_string(self) -> str:
        return f"({self.x},{self.y})"


@dataclass
class TreeElementNode:
    bounding_box: BoundingBox
    center: Center
    name: str = ""
    control_type: str = ""
    window_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def update_from_node(self, node: "TreeElementNode"):
        self.name = node.name
        self.control_type = node.control_type
        self.window_name = node.window_name
        self.bounding_box = node.bounding_box
        self.center = node.center
        self.metadata = node.metadata


@dataclass
class ScrollElementNode:
    name: str
    control_type: str
    window_name: str
    bounding_box: BoundingBox
    center: Center
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TextElementNode:
    text: str


ElementNode = TreeElementNode | ScrollElementNode | TextElementNode
