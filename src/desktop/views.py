from dataclasses import dataclass
from typing import Literal, Optional

from src.tree.views import TreeState


@dataclass
class App:
    name: str
    title: str  # Window title for distinguishing between instances
    depth: int
    status: Literal["Maximized", "Minimized", "Normal"]
    size: "Size"
    handle: int

    def to_string(self) -> str:
        return f"Name: {self.name} Title: {self.title} Depth: {self.depth} Status: {self.status} Size: {self.size.to_string()}"


@dataclass
class Size:
    width: int
    height: int

    def to_string(self) -> str:
        return f"({self.width},{self.height})"


@dataclass
class DesktopState:
    apps: list[App]
    active_app: Optional[App]
    screenshot: bytes | None
    tree_state: TreeState

    def active_app_to_string(self):
        if self.active_app is None:
            return "No active app"
        return self.active_app.to_string()

    def apps_to_string(self):
        if len(self.apps) == 0:
            return "No apps opened"
        return "\n".join([app.to_string() for app in self.apps])
